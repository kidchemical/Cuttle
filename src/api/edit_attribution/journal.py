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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_root TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    ts REAL NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT,
    query_id TEXT,
    chat_session_id TEXT,
    digest_before TEXT,
    digest_after TEXT,
    commit_sha TEXT
);
CREATE INDEX IF NOT EXISTS idx_edit_open
    ON edit_events (repo_root, rel_path);
CREATE INDEX IF NOT EXISTS idx_edit_query
    ON edit_events (query_id);
"""


def _cuttle_root() -> Path:
    # .../Cuttle/src/api/edit_attribution/journal.py → Cuttle
    return Path(__file__).resolve().parents[3]


def _db_path() -> Path:
    d = runtime_state_path("edit_attribution", project_root=_cuttle_root(),
                           legacy="workspace/edit_attribution")
    return d / "edit_journal.sqlite3"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def normalize_repo_root(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return (path or "").strip()


def normalize_rel_path(rel_path: str) -> str:
    rel = (rel_path or "").replace("\\", "/").strip()
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
                    query_id, chat_session_id, digest_before, digest_after, commit_sha
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                           query_id, chat_session_id, digest_before, digest_after
                    FROM edit_events
                    WHERE repo_root = ?
                      AND commit_sha IS NULL
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
            if event_ids:
                ids = [int(i) for i in event_ids if i is not None]
                n = 0
                for i in range(0, len(ids), 200):
                    chunk = ids[i : i + 200]
                    placeholders = ",".join("?" * len(chunk))
                    cur = conn.execute(
                        f"""
                        UPDATE edit_events
                        SET commit_sha = ?
                        WHERE commit_sha IS NULL AND id IN ({placeholders})
                        """,
                        (sha, *chunk),
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
                      AND commit_sha IS NULL
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
