"""Agent review: per-hunk Keep / Undo of agent edits, shared by every surface.

Agents write to disk immediately; review is a layer on top. Each open review
holds a *baseline* (what the user has agreed to) and the hunks are always
baseline ↔ disk, so the IDE, Pending Changes, the diff modal and chat read one
truth. Keep moves the baseline; Undo rewrites the file on disk.

Inputs come from the harness turn lifecycle:

* ``turn_started`` — the agent_events start snapshot (pinned tree) is the
  pre-turn content of every file; user edits since the previous turn are
  folded into open baselines first.
* ``note_agent_edit`` / ``note_agent_paths`` — live, after each native edit
  or edit-class step snapshot.
* ``turn_finished`` — the start→end turn diff catches unreported writes.

User edits made while no agent turn is live are folded into the baseline
(``hunks.fold``), so they never show as agent hunks. Hooks never raise.
Gated by the ``agent_review`` experimental flag.
"""

from __future__ import annotations

import logging
import os
import stat as stat_mod
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from api.agent_review import hunks as H
from api.agent_review.store import ABSENT, ReviewStore

log = logging.getLogger(__name__)

FLAG = "agent_review"
MAX_BYTES = 2_000_000
SCRATCH = "temp/agent-events"
STALE_TURN_SEC = 12 * 3600


class ReviewError(ValueError):
    status = 400


class NotFound(ReviewError):
    status = 404


class Conflict(ReviewError):
    status = 409


def enabled() -> bool:
    try:
        from api.experimental import is_enabled
        return bool(is_enabled(FLAG))
    except Exception:
        return False


def _store() -> ReviewStore:
    return ReviewStore()


# ── commit policy (Settings → Agents → Git) ─────────────────────────
COMMIT_POLICY_KEY = "agent_review_commit_policy"
COMMIT_POLICIES = ("warn", "keep")  # ask first | keep reviewed files silently


def commit_policy() -> str:
    """What committing files with unreviewed agent changes does (default ``warn``)."""
    try:
        from managers.settings_manager import get_settings_manager
        value = get_settings_manager().get_setting(COMMIT_POLICY_KEY, "warn")
    except Exception:
        value = "warn"
    return value if value in COMMIT_POLICIES else "warn"


def set_commit_policy(value: str) -> bool:
    if value not in COMMIT_POLICIES:
        raise ReviewError(f"commit_policy must be one of {', '.join(COMMIT_POLICIES)}")
    from managers.settings_manager import get_settings_manager
    return bool(get_settings_manager().set_setting(COMMIT_POLICY_KEY, value))


def _key(path: Any) -> str:
    return str(Path(path).resolve())


def etag_for(data: bytes) -> str:
    from api.ide.service import etag_for as ide_etag
    return ide_etag(data)  # same token the IDE holds for its buffers


# ── file + git access ───────────────────────────────────────────────
def _git(root: str, *args: str, input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, input=input_bytes, capture_output=True, timeout=30)


def _decode(data: bytes) -> Optional[str]:
    if len(data) > MAX_BYTES or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "surrogateescape")


def _encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _read(repo_root: str, rel: str) -> Tuple[str, Optional[str], Optional[bytes]]:
    """('ok', text, bytes) | ('absent', None, None) | ('skip', None, None)."""
    path = Path(repo_root) / rel
    if path.is_symlink():
        return "skip", None, None
    if not path.exists():
        return "absent", None, None
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return "skip", None, None
    data = path.read_bytes()
    text = _decode(data)
    return ("skip", None, None) if text is None else ("ok", text, data)


def _at_tree(repo_root: str, tree: str, rel: str) -> Tuple[str, Optional[str]]:
    # Snapshot blobs use Git's clean representation. Compare against the
    # working-tree representation (including autocrlf / eol attributes), just
    # like the bytes _read sees and Undo must restore.
    res = _git(repo_root, "cat-file", "--filters", f"{tree}:{rel}")
    if res.returncode:
        # A failed checkout filter must not make Undo treat an existing file
        # as newly created (and delete it). Only a missing blob is absent.
        exists = _git(repo_root, "cat-file", "-e", f"{tree}:{rel}")
        return ("skip", None) if exists.returncode == 0 else ("absent", None)
    text = _decode(res.stdout)
    return ("skip", None) if text is None else ("ok", text)


def _ignored(repo_root: str, rels: Sequence[str]) -> set:
    if not rels:
        return set()
    res = _git(repo_root, "check-ignore", "-z", "--stdin", input_bytes="\0".join(rels).encode("utf-8", "surrogateescape"))
    return {p for p in res.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def _write(repo_root: str, rel: str, text: Optional[str]) -> bytes:
    """Atomic write (or delete when ``text`` is None); returns the bytes written."""
    path = Path(repo_root) / rel
    if path.is_symlink():
        raise ReviewError(f"{rel} is a symlink; refusing to write through it")
    if text is None:
        path.unlink(missing_ok=True)
        return b""
    data = _encode(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat_mod.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".cuttle-tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return data


def _norm_rel(rel: str) -> Optional[str]:
    p = PurePosixPath(str(rel).replace("\\", "/"))
    if p.is_absolute() or not p.parts or ".." in p.parts or "\0" in str(p) or p.parts[0] == ".git":
        return None
    out = p.as_posix()
    return None if out == SCRATCH or out.startswith(SCRATCH + "/") else out


def _repo_top(directory: Path) -> str:
    res = _git(str(directory), "rev-parse", "--show-toplevel")
    if res.returncode:
        raise ReviewError(f"{directory} is not inside a git repository")
    return _key(res.stdout.decode("utf-8", "surrogateescape").strip())


@contextmanager
def _guard(what: str):
    """Hooks run inside harness turns: log and swallow, never break the turn."""
    try:
        yield
    except Exception as exc:
        log.warning("[agent_review] %s failed: %s", what, exc)


# ── liveness + reconcile ────────────────────────────────────────────
def _is_live(store: ReviewStore, conn, query_id: Optional[str]) -> bool:
    turn = store.turn(conn, query_id) if query_id else None
    if not turn or turn["status"] != "running":
        return False
    status = None
    try:
        from api.agent_events.store import EventStore, state_dir
        run = EventStore(state_dir()).run(query_id)
        status = run["status"] if run else None
    except Exception:
        status = None
    if status not in (None, "running") or (status is None and time.time() - turn["started_at"] > STALE_TURN_SEC):
        store.finish_turn(conn, query_id)  # crashed/interrupted host left it open
        return False
    return True


def _close_if_settled(store: ReviewStore, conn, row: Dict[str, Any], disk: Optional[str]) -> bool:
    if row["baseline"] != disk:
        return False
    store.close_review(conn, row["repo_root"], row["rel_path"])
    # A later edit in the same live turn starts from what the user agreed to.
    if _is_live(store, conn, row["last_query_id"]):
        store.set_settled(conn, row["last_query_id"], row["rel_path"], disk)
    return True


def _reconcile(store: ReviewStore, conn, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fold user edits since the agent last wrote; close settled reviews."""
    state, disk, data = _read(row["repo_root"], row["rel_path"])
    if state == "skip":
        return None
    row = dict(row, current=disk, current_bytes=data)
    # Commits outside the guarded UI (or a late turn-finish hook) can leave
    # review rows behind. Accept only exact committed working-tree content;
    # staged-only changes and later agent edits must remain reviewable.
    if not _is_live(store, conn, row["last_query_id"]):
        head = _git(row["repo_root"], "rev-parse", "--verify", "HEAD")
        if head.returncode == 0:
            committed_state, committed = _at_tree(
                row["repo_root"], head.stdout.decode("ascii").strip(), row["rel_path"])
            if committed_state != "skip" and committed == disk:
                store.close_review(conn, row["repo_root"], row["rel_path"])
                store.bump(conn)
                return None
            if committed_state != "skip":
                # A partial commit may accept some hunks while others remain.
                # Match both their original location and exact content; hunk
                # ids alone are insufficient when identical edits repeat.
                def signature(h):
                    return (h["old_start"], tuple(h["removed"]), tuple(h["added"]))
                accepted = {signature(h) for h in H.compute(row["baseline"], committed)}
                ids = [h["id"] for h in H.compute(row["baseline"], disk) if signature(h) in accepted]
                if ids:
                    baseline = H.keep(row["baseline"], disk, ids)
                    store.update_review(conn, row["repo_root"], row["rel_path"], baseline=baseline)
                    row.update(baseline=baseline)
                    store.bump(conn)
    if not _is_live(store, conn, row["last_query_id"]) and disk != row["last_agent"]:
        baseline, conflicts = H.fold(row["baseline"], row["last_agent"], disk)
        mixed = row["mixed"] or conflicts > 0
        store.update_review(conn, row["repo_root"], row["rel_path"], baseline=baseline, last_agent=disk, mixed=mixed)
        row.update(baseline=baseline, last_agent=disk, mixed=mixed)
        store.bump(conn)
    if _close_if_settled(store, conn, row, disk):
        store.bump(conn)
        return None
    return row


# ── harness hooks (never raise) ─────────────────────────────────────
def turn_started(snapshot: Optional[Dict[str, Any]], *, query_id: str, chat_session_id: Any = None,
                 agent_id: Optional[str] = None, model: Optional[str] = None) -> None:
    if not snapshot or not query_id or not enabled():
        return
    with _guard("turn_started"):
        root = _key(snapshot["repo_root"])
        store = _store()
        with store.tx() as conn:
            for row in store.reviews(conn, root):
                _reconcile(store, conn, row)
            store.add_turn(conn, query_id=str(query_id), repo_root=root, start_tree=snapshot["tree"],
                           skipped=dict(snapshot.get("skipped") or {}),
                           chat_session_id=None if chat_session_id in (None, "") else str(chat_session_id),
                           agent_id=agent_id, model=model)


def _observe(store: ReviewStore, conn, turn: Dict[str, Any], rels: Iterable[str],
             skipped: Optional[Dict[str, Any]] = None) -> int:
    root = turn["repo_root"]
    skip = set(turn["skipped"]) | set(skipped or {})
    wanted = sorted({r for r in (_norm_rel(x) for x in rels) if r and r not in skip})
    wanted = [r for r in wanted if r not in _ignored(root, wanted)]
    changed = 0
    for rel in wanted:
        state, disk, _ = _read(root, rel)
        if state == "skip":
            continue
        row = store.review(conn, root, rel)
        if row:
            store.update_review(conn, root, rel, last_agent=disk, last_query_id=turn["query_id"])
            store.add_review_turn(conn, root, rel, turn["query_id"])
            row.update(last_agent=disk, last_query_id=turn["query_id"])
            _close_if_settled(store, conn, row, disk)
            changed += 1
            continue
        base = store.settled(conn, turn["query_id"], rel)
        if base is ABSENT:
            base_state, base = _at_tree(root, turn["start_tree"], rel)
            if base_state == "skip":
                continue
        if base == disk:
            continue
        store.open_review(conn, repo_root=root, rel_path=rel, baseline=base, last_agent=disk,
                          query_id=turn["query_id"], chat_session_id=turn["chat_session_id"],
                          agent_id=turn["agent_id"])
        changed += 1
    if changed:
        store.bump(conn)
    return changed


def note_agent_paths(query_id: Optional[str], rel_paths: Iterable[str]) -> None:
    """Live: repo-relative paths an edit-class step changed."""
    if not query_id or not enabled():
        return
    with _guard("note_agent_paths"):
        store = _store()
        with store.tx() as conn:
            turn = store.turn(conn, query_id)
            if turn and turn["status"] == "running":
                _observe(store, conn, turn, list(rel_paths))


def note_agent_edit(query_id: Optional[str], path: str) -> None:
    """Live: a vendor-reported native edit (absolute or repo-relative path)."""
    if not query_id or not path or not enabled():
        return
    with _guard("note_agent_edit"):
        store = _store()
        with store.tx() as conn:
            turn = store.turn(conn, query_id)
            if not turn or turn["status"] != "running":
                return
            p = Path(path)
            if p.is_absolute():
                try:
                    rel = p.resolve().relative_to(turn["repo_root"]).as_posix()
                except ValueError:
                    return  # outside this repo
            else:
                rel = p.as_posix()
            _observe(store, conn, turn, [rel])


def turn_finished(query_id: Optional[str], line_event: Optional[Dict[str, Any]]) -> None:
    """End of turn: catch writes the agent never reported, then mark it done."""
    if not query_id or not enabled():
        return
    with _guard("turn_finished"):
        store = _store()
        with store.tx() as conn:
            turn = store.turn(conn, query_id)
            if not turn:
                return
            if line_event:
                _observe(store, conn, turn, [f.get("path") for f in line_event.get("files") or [] if f.get("path")],
                         skipped=line_event.get("skipped"))
            store.finish_turn(conn, query_id)
            store.bump(conn)


# ── read / act (routes + CLI) ───────────────────────────────────────
def _scope(root: Path) -> Tuple[str, str]:
    """(repo root, root's prefix inside the repo — '' at the top)."""
    repo = _repo_top(root)
    prefix = Path(_key(root)).relative_to(repo).as_posix()
    return repo, "" if prefix == "." else prefix


def _to_rel(prefix: str, path: str) -> str:
    norm = _norm_rel(path)
    if not norm:
        raise ReviewError(f"Invalid path: {path!r}")
    return f"{prefix}/{norm}" if prefix else norm


def _public(row: Dict[str, Any], prefix: str, turns: List[str], *, detail: bool = False) -> Dict[str, Any]:
    base, cur = row["baseline"], row["current"]
    hunk_list = H.compute(base, cur)
    out = {
        "path": row["rel_path"][len(prefix) + 1:] if prefix else row["rel_path"],
        "rel_path": row["rel_path"], "repo_root": row["repo_root"],
        "change": "added" if base is None else ("deleted" if cur is None else "modified"),
        "hunks": len(hunk_list),
        "additions": sum(h["new_count"] for h in hunk_list),
        "deletions": sum(h["old_count"] for h in hunk_list),
        "mixed": row["mixed"], "chat_session_id": row["chat_session_id"], "agent_id": row["agent_id"],
        "query_ids": turns, "opened_at": row["opened_at"], "updated_at": row["updated_at"],
    }
    if detail:
        out.update(baseline=base, current=cur, hunk_list=hunk_list,
                   etag=etag_for(row["current_bytes"]) if row["current_bytes"] is not None else None)
    return out


def _under(rel: str, prefix: str) -> bool:
    return not prefix or rel.startswith(prefix + "/")


def revision() -> int:
    return _store().revision()


def list_reviews(root: Path) -> Dict[str, Any]:
    repo, prefix = _scope(root)
    store = _store()
    files = []
    with store.tx() as conn:
        for row in store.reviews(conn, repo):
            if not _under(row["rel_path"], prefix):
                continue
            row = _reconcile(store, conn, row)
            if row is None:
                continue
            item = _public(row, prefix, store.turns_for(conn, repo, row["rel_path"]))
            item["live"] = _is_live(store, conn, row["last_query_id"])
            files.append(item)
        rev = conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
    return {"repo_root": repo, "files": files, "revision": int(rev["value"]) if rev else 0,
            "totals": {k: sum(f[k] for f in files) for k in ("hunks", "additions", "deletions")}}


def file_review(root: Path, path: str) -> Dict[str, Any]:
    repo, prefix = _scope(root)
    rel = _to_rel(prefix, path)
    store = _store()
    with store.tx() as conn:
        row = store.review(conn, repo, rel)
        row = _reconcile(store, conn, row) if row else None
        if row is None:
            return {"review": None}
        item = _public(row, prefix, store.turns_for(conn, repo, rel), detail=True)
        item["live"] = _is_live(store, conn, row["last_query_id"])
        return {"review": item}


def _check_ids(row: Dict[str, Any], ids: Optional[Sequence[str]]) -> None:
    if ids is None:
        return
    known = {h["id"] for h in H.compute(row["baseline"], row["current"])}
    missing = [i for i in ids if i not in known]
    if missing:
        raise Conflict("These changes moved since you loaded them; refresh and try again")


def _act(root: Path, paths: Optional[Sequence[str]], ids: Optional[Sequence[str]], *, undo: bool,
         etag: Optional[str] = None) -> Dict[str, Any]:
    repo, prefix = _scope(root)
    if ids is not None and (not paths or len(paths) != 1):
        raise ReviewError("Hunk ids need exactly one path")
    store = _store()
    done: List[Dict[str, Any]] = []
    with store.tx() as conn:
        if paths is None:
            rels = [r["rel_path"] for r in store.reviews(conn, repo) if _under(r["rel_path"], prefix)]
        else:
            rels = [_to_rel(prefix, p) for p in paths]
        for rel in rels:
            row = store.review(conn, repo, rel)
            row = _reconcile(store, conn, row) if row else None
            if row is None:
                if paths is not None:
                    raise NotFound(f"No agent changes to review in {rel}")
                continue
            _check_ids(row, ids)
            if undo:
                if etag is not None and row["current_bytes"] is not None and etag != etag_for(row["current_bytes"]):
                    raise Conflict(f"{rel} changed since it was loaded; reload before undoing")
                new = H.undo(row["baseline"], row["current"], ids)
                _write(repo, rel, new)
                # The user chose this content: it is not a later user edit to fold.
                store.update_review(conn, repo, rel, last_agent=new)
                row.update(current=new, last_agent=new)
            else:
                base = H.keep(row["baseline"], row["current"], ids)
                store.update_review(conn, repo, rel, baseline=base)
                row.update(baseline=base)
            closed = _close_if_settled(store, conn, row, row["current"])
            done.append({"path": rel[len(prefix) + 1:] if prefix else rel, "closed": closed})
        store.bump(conn)
    return {"files": done, "revision": store.revision()}


def keep(root: Path, paths: Optional[Sequence[str]] = None, hunk_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Accept hunks (baseline moves; disk untouched). ``paths=None`` = all under root."""
    return _act(root, paths, hunk_ids, undo=False)


def undo(root: Path, paths: Optional[Sequence[str]] = None, hunk_ids: Optional[Sequence[str]] = None,
         etag: Optional[str] = None) -> Dict[str, Any]:
    """Revert hunks on disk (agent-created files are deleted when fully undone)."""
    return _act(root, paths, hunk_ids, undo=True, etag=etag)
