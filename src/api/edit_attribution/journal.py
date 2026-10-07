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

from core.runtime_paths import runtime_state_path

_lock = threading.Lock()



def _cuttle_root() -> Path:
    # .../Cuttle/src/api/edit_attribution/journal.py → Cuttle
    return Path(__file__).resolve().parents[3]


def _db_path() -> Path:
    from api.agent_events.store import state_dir
    return state_dir() / "agent_events.sqlite3"


def _connect() -> sqlite3.Connection:
    from api.agent_events.store import SCHEMA
    import os
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if not os.environ.get("CUTTLE_AGENT_EVENTS_DIR"):
        base = _cuttle_root() / "src" / "data"
        for legacy in (base / "workspace/edit_attribution/edit_journal.sqlite3", base / "edit_attribution/edit_journal.sqlite3"):
            if legacy.is_file():
                import_legacy(legacy, path)
                break
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
                        SET commit_sha = ?
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
                    SET commit_sha = ?
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


def import_legacy(path: Path, target: Optional[Path] = None) -> Dict[str, int]:
    """Idempotent read-only import; retain legacy DB as a rollback artifact."""
    target=target or _db_path()
    if not path.is_file() or path.resolve()==target.resolve():
        return {'imported':0,'stale':0}
    target.parent.mkdir(parents=True,exist_ok=True)
    probe=sqlite3.connect(target)
    from api.agent_events.store import SCHEMA
    probe.executescript(SCHEMA)
    cursor_key='legacy_import:'+str(path.resolve())
    saved=probe.execute('SELECT value FROM state_meta WHERE key=?',(cursor_key,)).fetchone()
    last_id=int(saved[0]) if saved else probe.execute('SELECT COALESCE(MAX(legacy_id),0) FROM edit_events').fetchone()[0]
    probe.close()
    old=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    old.row_factory=sqlite3.Row
    try:
        rows=old.execute('SELECT * FROM edit_events WHERE id>? ORDER BY id',(last_id,)).fetchall()
    finally:
        old.close()
    from api.agent_events.store import SCHEMA
    conn=sqlite3.connect(target)
    conn.executescript(SCHEMA)
    imported=stale=0
    try:
        with conn:
            for row in rows:
                item=dict(row)
                state='open' if item['commit_sha'] is None and item['ts']>=time.time()-7*86400 else ('settled' if item['commit_sha'] else 'stale')
                cursor=conn.execute('''INSERT OR IGNORE INTO edit_events(repo_root,rel_path,ts,agent_id,model,
                    query_id,chat_session_id,digest_before,digest_after,commit_sha,legacy_id,settlement)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',tuple(item.get(k) for k in
                    ('repo_root','rel_path','ts','agent_id','model','query_id','chat_session_id','digest_before','digest_after','commit_sha','id'))+(state,))
                imported+=cursor.rowcount
                stale+=cursor.rowcount if state=='stale' else 0
            if rows:
                conn.execute('INSERT OR REPLACE INTO state_meta(key,value) VALUES(?,?)',(cursor_key,str(rows[-1]['id'])))
    finally:
        conn.close()
    return {'imported':imported,'stale':stale}


def reconcile_commits(path: Optional[Path] = None) -> int:
    """Match only commits after the observed edit, never earlier identical blobs.

    Settlement recognizes terminal commits; it cannot retroactively insert trailers.
    A bounded history scan leaves unproven rows open for the next maintenance pass.
    """
    import hashlib
    import subprocess
    conn=sqlite3.connect(path or _db_path())
    conn.row_factory=sqlite3.Row
    count=0
    try:
        rows=conn.execute("SELECT * FROM edit_events WHERE commit_sha IS NULL AND settlement='open' AND ambiguous=0 ORDER BY ts DESC LIMIT 1000").fetchall()
        grouped={}
        for row in rows:
            if row['digest_after'] and Path(row['repo_root']).is_dir():
                grouped.setdefault((row['repo_root'],row['rel_path']),[]).append(row)
        for (root,rel),edits in grouped.items():
            history=subprocess.run(['git','log','-100','--format=%H %ct','--',rel],
                                   cwd=root,capture_output=True,text=True,timeout=15)
            if history.returncode:continue
            earliest=min(edit['ts'] for edit in edits)
            for line in reversed(history.stdout.splitlines()):
                sha,stamp=line.split(' ',1)
                if float(stamp)+1<earliest:continue
                blob=subprocess.run(['git','show',sha+':'+rel],cwd=root,capture_output=True,timeout=15)
                if blob.returncode:continue
                digest=hashlib.sha256(blob.stdout).hexdigest()
                matched=[edit for edit in edits if digest==edit['digest_after'] and float(stamp)+1>=edit['ts']]
                for edit in matched:
                    conn.execute("UPDATE edit_events SET commit_sha=?,settlement='settled' WHERE id=?",(sha,edit['id']))
                    count+=1;edits.remove(edit)
                if not edits:break
        conn.commit()
    finally:
        conn.close()
    return count
