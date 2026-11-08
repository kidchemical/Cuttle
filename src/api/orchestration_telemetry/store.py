"""SQLite event log in the Cuttle home (``<home>/db/orchestration_telemetry.db``)."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.runtime_paths import data_db_dir

COLUMNS = ("at", "kind", "user_id", "session_id", "parent_session_id", "project_path", "playbook_id",
           "tier", "role", "profile_id", "agent", "model", "effort", "child_id", "batch_id", "turn_id",
           "status", "duration_s", "detail")


def db_path() -> Path:
    return data_db_dir() / "orchestration_telemetry.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        user_id INTEGER,
        session_id INTEGER,
        parent_session_id INTEGER,
        project_path TEXT NOT NULL DEFAULT '',
        playbook_id TEXT NOT NULL DEFAULT '',
        tier TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '',
        profile_id TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        effort TEXT NOT NULL DEFAULT '',
        child_id TEXT NOT NULL DEFAULT '',
        batch_id TEXT NOT NULL DEFAULT '',
        turn_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        duration_s REAL,
        detail TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS events_kind_at ON events(kind, at)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_child ON events(child_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_parent ON events(parent_session_id, at)")
    return conn


def append(kind: str, *, at: Optional[float] = None, detail: Optional[Dict[str, Any]] = None,
           **fields: Any) -> None:
    row = {c: fields.get(c) for c in COLUMNS if c not in ("at", "kind", "detail")}
    for key in ("project_path", "playbook_id", "tier", "role", "profile_id", "agent", "model", "effort",
                "child_id", "batch_id", "turn_id", "status"):
        row[key] = str(row.get(key) or "")
    values = [float(at if at is not None else time.time()), kind] + [row[c] for c in COLUMNS[2:-1]] + [
        json.dumps(detail or {}, default=str)]
    with closing(_connect()) as conn:
        conn.execute(f"INSERT INTO events ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", values)
        conn.commit()


def events(*, kinds: Iterable[str] = (), since: float = 0.0, user_id: Optional[int] = None,
           limit: int = 200000) -> List[Dict[str, Any]]:
    clauses, params = ["at >= ?"], [float(since)]
    kinds = list(kinds)
    if kinds:
        clauses.append("kind IN (" + ",".join("?" * len(kinds)) + ")")
        params += kinds
    if user_id is not None:
        clauses.append("(user_id = ? OR user_id IS NULL)")
        params.append(int(user_id))
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY at, id LIMIT ?",
                            (*params, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except ValueError:
            d["detail"] = {}
        out.append(d)
    return out


def latest(kind: str, *, child_id: str = "", parent_session_id: Optional[int] = None,
           role: str = "") -> Optional[Dict[str, Any]]:
    clauses, params = ["kind = ?"], [kind]
    if child_id:
        clauses.append("child_id = ?")
        params.append(child_id)
    if parent_session_id is not None:
        clauses.append("parent_session_id = ?")
        params.append(int(parent_session_id))
    if role:
        clauses.append("role = ?")
        params.append(role)
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY at DESC, id DESC LIMIT 1",
                           params).fetchone()
    return dict(row) if row else None


def has_backfill() -> bool:
    with closing(_connect()) as conn:
        return bool(conn.execute("SELECT 1 FROM events WHERE detail LIKE '%\"backfilled\": true%' LIMIT 1").fetchone())
