"""Auto-compact state in the Cuttle home (``<home>/auto_compact/auto_compact.sqlite3``).

Per chat: the user's override, the decision state (turns seen, last compact,
failure streak, Tasks items already done) and a short history. A lease row
makes "one compact per chat at a time" hold across processes: a sub-agent
turn runs in its parent's CLI process, not in Flask.

Persistent lifetime; survives restarts. A lease past its ``until`` is free.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

HISTORY_KEEP = 50


def _connect() -> sqlite3.Connection:
    from core.runtime_paths import runtime_state_path

    conn = sqlite3.connect(runtime_state_path("auto_compact", "auto_compact.sqlite3"), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS overrides (session_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS state (session_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, "
                 "at REAL NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}')")
    conn.execute("CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, at)")
    conn.execute("CREATE TABLE IF NOT EXISTS leases (session_id INTEGER PRIMARY KEY, holder TEXT NOT NULL, until REAL NOT NULL)")
    return conn


def _load(raw: Optional[str]) -> Dict[str, Any]:
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def get_override(session_id: int) -> Dict[str, Any]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT data FROM overrides WHERE session_id=?", (int(session_id),)).fetchone()
    return _load(row["data"]) if row else {}


def set_override(session_id: int, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace the chat's override; an empty one deletes it (inherit everything)."""
    with closing(_connect()) as conn, conn:
        if data:
            conn.execute("INSERT INTO overrides(session_id, data, updated) VALUES(?,?,?) "
                         "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                         (int(session_id), json.dumps(data), time.time()))
        else:
            conn.execute("DELETE FROM overrides WHERE session_id=?", (int(session_id),))
    return dict(data or {})


def get_state(session_id: int) -> Dict[str, Any]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT data FROM state WHERE session_id=?", (int(session_id),)).fetchone()
    return _load(row["data"]) if row else {}


def patch_state(session_id: int, **fields: Any) -> Dict[str, Any]:
    """Merge ``fields`` into the chat's state atomically; returns the new state."""
    with closing(_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT data FROM state WHERE session_id=?", (int(session_id),)).fetchone()
            data = _load(row["data"]) if row else {}
            data.update(fields)
            conn.execute("INSERT INTO state(session_id, data, updated) VALUES(?,?,?) "
                         "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                         (int(session_id), json.dumps(data), time.time()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return data


def record(session_id: int, outcome: str, detail: Optional[Dict[str, Any]] = None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO events(session_id, at, outcome, detail) VALUES(?,?,?,?)",
                     (int(session_id), time.time(), str(outcome), json.dumps(detail or {}, default=str)))
        conn.execute("DELETE FROM events WHERE session_id=? AND id NOT IN "
                     "(SELECT id FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                     (int(session_id), int(session_id), HISTORY_KEEP))


def history(session_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT at, outcome, detail FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?",
                            (int(session_id), max(1, int(limit)))).fetchall()
    return [{"at": r["at"], "outcome": r["outcome"], "detail": _load(r["detail"])} for r in rows]


def claim_lease(session_id: int, holder: str, ttl: float) -> bool:
    """Take the chat's compact lease unless another holder has a live one."""
    now = time.time()
    with closing(_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT holder, until FROM leases WHERE session_id=?", (int(session_id),)).fetchone()
            if row and row["holder"] != holder and float(row["until"]) > now:
                conn.execute("ROLLBACK")
                return False
            conn.execute("INSERT INTO leases(session_id, holder, until) VALUES(?,?,?) "
                         "ON CONFLICT(session_id) DO UPDATE SET holder=excluded.holder, until=excluded.until",
                         (int(session_id), holder, now + float(ttl)))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def lease_holder(session_id: int) -> Optional[Dict[str, Any]]:
    """The live lease on the chat, or None."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT holder, until FROM leases WHERE session_id=?", (int(session_id),)).fetchone()
    if row and float(row["until"]) > time.time():
        return {"holder": row["holder"], "until": float(row["until"])}
    return None


def release_lease(session_id: int, holder: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM leases WHERE session_id=? AND holder=?", (int(session_id), holder))
