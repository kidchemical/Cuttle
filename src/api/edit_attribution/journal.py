"""Observed edit attribution — journal of worktree deltas from harness runs.

Only paths whose content digest changed during a harness run are recorded.
Commit trailers and UI summaries are built from this journal alone — never from
session heuristics or “recent chat” guessing.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_lock = threading.Lock()


def _db_path() -> Path:
    from api.agent_events.store import state_dir
    return state_dir() / "agent_events.sqlite3"


def _connect() -> sqlite3.Connection:
    from api.agent_events.store import SCHEMA
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def normalize_repo_root(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return (path or "").strip()


def normalize_rel_path(rel_path: str) -> str:
    rel = (rel_path or "")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/")


def append_events(events: Sequence[Dict[str, Any]]) -> int:
    """Insert observed edit events. Returns number of rows written."""
    rows: List[Tuple[Any, ...]] = []
    now = time.time()
    for ev in events:
        repo = normalize_repo_root(str(ev.get("repo_root") or ""))
        rel = normalize_rel_path(str(ev.get("rel_path") or ""))
        agent = str(ev.get("agent_id") or "").strip()
        if not repo or not rel or not agent:
            continue
        rows.append(
            (
                repo,
                rel,
                float(ev.get("ts") or now),
                agent,
                (str(ev.get("model") or "").strip() or None),
                (str(ev.get("query_id") or "").strip() or None),
                (str(ev.get("chat_session_id") or "").strip() or None),
                (str(ev.get("digest_before") or "").strip() or None),
                (str(ev.get("digest_after") or "").strip() or None),
                int(bool(ev.get("ambiguous"))),
            )
        )
    if not rows:
        return 0
    with _lock:
        conn = _connect()
        try:
            conn.executemany(
                """
                INSERT INTO edit_events (
                    repo_root, rel_path, ts, agent_id, model,
                    query_id, chat_session_id, digest_before, digest_after, ambiguous, commit_sha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                rows,
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()


def open_events_for_paths(
    repo_root: str,
    rel_paths: Iterable[str],
) -> List[Dict[str, Any]]:
    """Return unsettled events for the given paths (any agent), oldest first."""
    root = normalize_repo_root(repo_root)
    paths = sorted({normalize_rel_path(p) for p in rel_paths if normalize_rel_path(p)})
    if not root or not paths:
        return []
    with _lock:
        conn = _connect()
        try:
            out: List[Dict[str, Any]] = []
            # Chunk IN clauses for safety on large commits.
            for i in range(0, len(paths), 200):
                chunk = paths[i : i + 200]
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"""
                    SELECT id, repo_root, rel_path, ts, agent_id, model,
                           query_id, chat_session_id, digest_before, digest_after, settlement, ambiguous
                    FROM edit_events
                    WHERE repo_root = ?
                      AND commit_sha IS NULL AND settlement = 'open'
                      AND rel_path IN ({placeholders})
                    ORDER BY ts ASC, id ASC
                    """,
                    (root, *chunk),
                )
                for row in cur.fetchall():
                    out.append(dict(row))
            return out
        finally:
            conn.close()


def open_paths_for_query(repo_root: str, query_id: str) -> List[str]:
    """Unsettled paths one harness run (``query_id``) changed in this repo."""
    root = normalize_repo_root(repo_root)
    qid = (query_id or "").strip()
    if not root or not qid:
        return []
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                SELECT DISTINCT rel_path FROM edit_events
                WHERE repo_root = ? AND query_id = ? AND commit_sha IS NULL
                ORDER BY rel_path
                """,
                (root, qid),
            )
            return [str(r["rel_path"]) for r in cur.fetchall()]
        finally:
            conn.close()


def build_commit_attribution(
    repo_root: str,
    rel_paths: Sequence[str],
) -> Dict[str, Any]:
    """Map committed paths → proven agent/model pairs; list unattributed paths.

    Never invents contributors. A path with zero open journal events is
    unattributed (human / outside Cuttle / pre-journal dirt).
    """
    root = normalize_repo_root(repo_root)
    paths = [normalize_rel_path(p) for p in rel_paths if normalize_rel_path(p)]
    # Preserve commit order but unique.
    seen: set = set()
    ordered: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    events = open_events_for_paths(root, ordered)
    import hashlib
    import subprocess
    proven = []
    for rel in ordered:
        staged = subprocess.run(["git", "diff", "--cached", "--name-only", "-z", "--", rel],
                                cwd=root, capture_output=True, timeout=15)
        if staged.returncode:
            continue
        if staged.stdout:
            blob = subprocess.run(["git", "show", ":" + rel], cwd=root, capture_output=True, timeout=15)
            expected = hashlib.sha256(blob.stdout).hexdigest() if blob.returncode == 0 else None
        else:
            path = Path(root) / rel
            expected = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        candidates = [e for e in events if e["rel_path"] == rel]
        # Walk the observed content chain backwards. Unknown/mismatched content
        # stops attribution, rather than crediting old edits merely by path.
        for ev in reversed(candidates):
            if ev.get("digest_after") != expected or (expected is None and not ev.get("digest_before")):
                break
            if ev.get("ambiguous") or ev.get("settlement", "open") != "open":
                break
            proven.append(ev)
            expected = ev.get("digest_before")
            if expected is None:
                break
    events = sorted(proven, key=lambda ev: (ev["ts"], ev["id"]))
    by_path: Dict[str, List[Dict[str, Any]]] = {p: [] for p in ordered}
    for ev in events:
        rel = normalize_rel_path(str(ev.get("rel_path") or ""))
        if rel in by_path:
            by_path[rel].append(ev)

    attributed: Dict[str, List[Dict[str, str]]] = {}
    unattributed: List[str] = []
    query_ids: List[str] = []
    seen_q: set = set()

    for rel in ordered:
        evs = by_path.get(rel) or []
        if not evs:
            unattributed.append(rel)
            continue
        pairs: List[Dict[str, str]] = []
        seen_pair: set = set()
        for ev in evs:
            agent = str(ev.get("agent_id") or "").strip()
            model = str(ev.get("model") or "").strip() or "unknown"
            key = (agent, model)
            if agent and key not in seen_pair:
                seen_pair.add(key)
                pairs.append({"agent_id": agent, "model": model})
            qid = str(ev.get("query_id") or "").strip()
            if qid and qid not in seen_q:
                seen_q.add(qid)
                query_ids.append(qid)
        if pairs:
            attributed[rel] = pairs
        else:
            unattributed.append(rel)

    return {
        "repo_root": root,
        "attributed": attributed,
        "unattributed": unattributed,
        "query_ids": query_ids,
        "event_ids": [int(ev["id"]) for ev in events if ev.get("id") is not None],
        "attributed_count": len(attributed),
        "unattributed_count": len(unattributed),
    }


def format_attribution_trailers(attribution: Dict[str, Any]) -> str:
    """Commit-body trailer block from proven attribution only."""
    attributed = attribution.get("attributed") or {}
    unattributed = attribution.get("unattributed") or []
    query_ids = attribution.get("query_ids") or []
    if not attributed and not unattributed:
        return ""

    lines: List[str] = []
    if attributed:
        parts: List[str] = []
        for rel in sorted(attributed.keys()):
            pairs = attributed[rel] or []
            labels = []
            for p in pairs:
                agent = p.get("agent_id") or "?"
                model = p.get("model") or "unknown"
                labels.append(f"{agent}/{model}")
            parts.append(f"{rel}={','.join(labels)}")
        # Keep line length reasonable; split if huge.
        chunk: List[str] = []
        for part in parts:
            trial = ", ".join(chunk + [part])
            if chunk and len(trial) > 180:
                lines.append("Cuttle-Attributed: " + ", ".join(chunk))
                chunk = [part]
            else:
                chunk.append(part)
        if chunk:
            lines.append("Cuttle-Attributed: " + ", ".join(chunk))
    if unattributed:
        chunk = []
        for rel in unattributed:
            trial = ", ".join(chunk + [rel])
            if chunk and len(trial) > 180:
                lines.append("Cuttle-Unattributed: " + ", ".join(chunk))
                chunk = [rel]
            else:
                chunk.append(rel)
        if chunk:
            lines.append("Cuttle-Unattributed: " + ", ".join(chunk))
    if query_ids:
        lines.append("Cuttle-Query: " + ",".join(query_ids[:40]))
    return "\n".join(lines)


def merge_message_with_trailers(message: str, attribution: Dict[str, Any]) -> str:
    """Append attribution trailers to a commit message (subject preserved)."""
    body = (message or "").rstrip()
    trailers = format_attribution_trailers(attribution)
    if not trailers:
        return body + ("\n" if body and not body.endswith("\n") else "")
    if not body:
        return trailers + "\n"
    return body + "\n\n" + trailers + "\n"


def settle_events(
    repo_root: str,
    rel_paths: Sequence[str],
    commit_sha: str,
    *,
    event_ids: Optional[Sequence[int]] = None,
) -> int:
    """Mark open events for paths (or explicit ids) as settled by commit_sha."""
    root = normalize_repo_root(repo_root)
    sha = (commit_sha or "").strip()
    if not root or not sha:
        return 0
    with _lock:
        conn = _connect()
        try:
            if event_ids is not None:
                if not event_ids:
                    return 0
                ids = [int(i) for i in event_ids if i is not None]
                n = 0
                for i in range(0, len(ids), 200):
                    chunk = ids[i : i + 200]
                    placeholders = ",".join("?" * len(chunk))
                    cur = conn.execute(
                        f"""
                        UPDATE edit_events
                        SET commit_sha = ?, settlement = 'settled'
                        WHERE repo_root = ? AND commit_sha IS NULL AND id IN ({placeholders})
                        """,
                        (sha, root, *chunk),
                    )
                    n += cur.rowcount
                conn.commit()
                return n
            paths = sorted(
                {normalize_rel_path(p) for p in rel_paths if normalize_rel_path(p)}
            )
            if not paths:
                return 0
            n = 0
            for i in range(0, len(paths), 200):
                chunk = paths[i : i + 200]
                placeholders = ",".join("?" * len(chunk))
                cur = conn.execute(
                    f"""
                    UPDATE edit_events
                    SET commit_sha = ?, settlement = 'settled'
                    WHERE repo_root = ?
                      AND commit_sha IS NULL AND settlement = 'open'
                      AND rel_path IN ({placeholders})
                    """,
                    (sha, root, *chunk),
                )
                n += cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()


def attribution_summary_lines(attribution: Dict[str, Any]) -> List[str]:
    """Short human lines for toasts / status."""
    attributed = attribution.get("attributed") or {}
    unattributed = attribution.get("unattributed") or []
    lines: List[str] = []
    agents: Dict[str, set] = {}
    for _rel, pairs in attributed.items():
        for p in pairs or []:
            agent = p.get("agent_id") or "?"
            model = p.get("model") or "unknown"
            agents.setdefault(agent, set()).add(model)
    if agents:
        bits = [
            f"{a}/{'+'.join(sorted(ms))}" for a, ms in sorted(agents.items())
        ]
        lines.append(
            f"{len(attributed)} file(s) attributed: " + ", ".join(bits)
        )
    if unattributed:
        lines.append(f"{len(unattributed)} file(s) unattributed")
    return lines



REVERT_GRACE_SECONDS = 86400


def _file_digest(root: str, rel: str) -> Optional[str]:
    import hashlib
    path = Path(root) / rel
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return "unreadable"


def _settle(conn: sqlite3.Connection, row_id: int, sha: str) -> None:
    conn.execute("UPDATE edit_events SET commit_sha=?,settlement='settled' WHERE id=? AND settlement='open'", (sha, row_id))


def reconcile_commits(path: Optional[Path] = None, *, now: Optional[float] = None) -> Dict[str, int]:
    """Settle open edits by content and close the ones that can never settle.

    Ambiguous rows (overlapping runs) settle by content for bookkeeping but are
    never credited, closed, or treated as chain breaks.

    - **settled**: a commit after the edit holds exactly its ``digest_after``
      (any commit: Git UI, terminal or agent), or deleted the path for a
      deletion edit; never an earlier identical blob.
    - **chained**: an earlier edit whose output a settled later edit started
      from (``digest_after == later.digest_before``) settles to the same commit.
    - **superseded**: the next observed edit on the path started from other
      content, or the edit's exact content is in neither HEAD nor the worktree
      after a later commit touched the path.
    - **reverted**: the path is back at the content before the edit (or before
      the start of a connected chain ending at the current content).

    Worktree comparisons wait ``REVERT_GRACE_SECONDS`` so a branch switch or
    in-progress turn is not closed early; closing only ever withholds credit.
    """
    import hashlib
    import subprocess
    now = time.time() if now is None else now
    conn = sqlite3.connect(path or _db_path())
    conn.row_factory = sqlite3.Row
    stats = {"settled": 0, "chained": 0, "superseded": 0, "reverted": 0}
    try:
        rows = conn.execute(
            "SELECT * FROM edit_events WHERE commit_sha IS NULL AND settlement='open' "
            "ORDER BY ts DESC").fetchall()
        grouped: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
        for row in rows:
            if Path(row["repo_root"]).is_dir():
                grouped.setdefault((row["repo_root"], row["rel_path"]), []).append(row)
        committed_after: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
        for (root, rel), edits in grouped.items():
            # Every commit since the oldest open edit on this path (not a fixed
            # count): closing below is only safe for fully scanned history.
            since = int(min(e["ts"] for e in edits)) - 2
            history = subprocess.run(["git", "log", f"--since={since}", "--format=%H %ct", "--", rel],
                                     cwd=root, capture_output=True, text=True, timeout=30)
            if history.returncode:
                continue
            commits = [(sha, float(stamp)) for sha, stamp in
                       (line.split(" ", 1) for line in history.stdout.splitlines() if " " in line)]
            committed_after[(root, rel)] = commits
            pending = list(edits)
            for sha, stamp in reversed(commits):  # oldest first
                candidates = [e for e in pending if stamp + 1 >= e["ts"]]
                if not candidates:
                    continue
                blob = subprocess.run(["git", "show", sha + ":" + rel], cwd=root, capture_output=True, timeout=15)
                digest = hashlib.sha256(blob.stdout).hexdigest() if blob.returncode == 0 else None
                for edit in candidates:
                    content_match = digest is not None and digest == edit["digest_after"]
                    deletion = digest is None and edit["digest_after"] is None and edit["digest_before"] is not None
                    if content_match or deletion:
                        _settle(conn, edit["id"], sha)
                        stats["settled"] += 1
                        pending.remove(edit)
                if not pending:
                    break

        # Chains: an earlier edit feeding a settled later one shares its commit.
        changed = True
        while changed:
            changed = False
            for later in conn.execute(
                    "SELECT * FROM edit_events WHERE settlement='settled' AND commit_sha IS NOT NULL "
                    "AND digest_before IS NOT NULL AND ts>? ORDER BY ts", (now - 90 * 86400,)).fetchall():
                earlier = conn.execute(
                    "SELECT id FROM edit_events WHERE repo_root=? AND rel_path=? AND settlement='open' "
                    "AND commit_sha IS NULL AND digest_after=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                    (later["repo_root"], later["rel_path"], later["digest_before"], later["ts"])).fetchone()
                if earlier:
                    _settle(conn, earlier["id"], later["commit_sha"])
                    stats["chained"] += 1
                    changed = True

        # Close what can never settle: only paths whose history was scanned.
        # Ambiguous rows (overlapping runs, often duplicate observations of one
        # change) are never credited; they neither close nor break chains.
        open_rows = conn.execute(
            "SELECT * FROM edit_events WHERE commit_sha IS NULL AND settlement='open' AND ambiguous=0 "
            "ORDER BY ts, id").fetchall()
        by_path: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
        for row in open_rows:
            if (row["repo_root"], row["rel_path"]) in committed_after:
                by_path.setdefault((row["repo_root"], row["rel_path"]), []).append(row)
        for (root, rel), edits in by_path.items():
            observed = conn.execute(
                "SELECT id, ts, digest_before FROM edit_events WHERE repo_root=? AND rel_path=? AND ambiguous=0 "
                "ORDER BY ts, id",
                (root, rel)).fetchall()
            order = [r["id"] for r in observed]
            closed: set = set()
            for edit in edits:
                position = order.index(edit["id"])
                if position + 1 < len(observed) and observed[position + 1]["digest_before"] != edit["digest_after"]:
                    conn.execute("UPDATE edit_events SET settlement='superseded' WHERE id=?", (edit["id"],))
                    stats["superseded"] += 1
                    closed.add(edit["id"])
            remaining = [e for e in edits if e["id"] not in closed and now - e["ts"] >= REVERT_GRACE_SECONDS]
            if not remaining or not Path(root).is_dir():
                continue
            current = _file_digest(root, rel)
            if current == "unreadable":
                continue
            # Connected suffix of open edits ending at the latest observed edit.
            latest = remaining[-1]
            if latest["id"] != order[-1]:
                continue
            chain = [latest]
            for edit in reversed(remaining[:-1]):
                if edit["digest_after"] == chain[0]["digest_before"]:
                    chain.insert(0, edit)
                else:
                    break
            # Back at the content some chain edit started from: that edit and
            # every later one are net no-ops, whoever undid them.
            starts = [i for i, edit in enumerate(chain) if edit["digest_before"] == current]
            if starts:
                for edit in chain[starts[0]:]:
                    conn.execute("UPDATE edit_events SET settlement='reverted' WHERE id=?", (edit["id"],))
                    stats["reverted"] += 1
                continue
            if latest["digest_after"] != current:
                head = subprocess.run(["git", "show", "HEAD:" + rel], cwd=root, capture_output=True, timeout=15)
                head_digest = hashlib.sha256(head.stdout).hexdigest() if head.returncode == 0 else None
                touched_after = any(stamp + 1 >= latest["ts"] for _sha, stamp in committed_after.get((root, rel), []))
                if touched_after and head_digest != latest["digest_after"]:
                    conn.execute("UPDATE edit_events SET settlement='superseded' WHERE id=?", (latest["id"],))
                    stats["superseded"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats
