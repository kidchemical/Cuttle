"""SQLite state for orchestrator playbooks and chat bindings (Cuttle home)."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import data_db_dir


def _db_path() -> Path:
    return data_db_dir() / "orchestrator.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS playbooks (
        user_id INTEGER NOT NULL,
        id TEXT NOT NULL,
        doc TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (user_id, id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS bindings (
        session_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        playbook_id TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        bound_at REAL NOT NULL
    )""")
    # Append-only: every bind / switch / goal edit / unbind, so a chat's playbook
    # at any moment (and telemetry over it) survives the binding being replaced.
    conn.execute("""CREATE TABLE IF NOT EXISTS binding_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        playbook_id TEXT NOT NULL DEFAULT '',
        goal TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        at REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS binding_events_session ON binding_events(session_id, at)")
    # Hold: '' (running) / 'paused' / 'stopped'. Owner: api.orchestrator.control.
    have = {r["name"] for r in conn.execute("PRAGMA table_info(bindings)")}
    for column, ddl in (("hold", "TEXT NOT NULL DEFAULT ''"), ("hold_at", "REAL"),
                        ("hold_ops", "TEXT NOT NULL DEFAULT '[]'")):
        if column not in have:
            try:
                conn.execute(f"ALTER TABLE bindings ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass  # another process added it first
    conn.execute("""CREATE TABLE IF NOT EXISTS prefs (
        user_id INTEGER PRIMARY KEY,
        default_playbook TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()
    return conn


def list_playbooks(user_id: int) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT doc, updated_at FROM playbooks WHERE user_id = ? ORDER BY id", (int(user_id),)
        ).fetchall()
    out = []
    for row in rows:
        try:
            doc = json.loads(row["doc"])
        except ValueError:
            continue
        doc["updated_at"] = row["updated_at"]
        out.append(doc)
    return out


def get_playbook(user_id: int, playbook_id: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT doc, updated_at FROM playbooks WHERE user_id = ? AND id = ?",
            (int(user_id), playbook_id),
        ).fetchone()
    if not row:
        return None
    try:
        doc = json.loads(row["doc"])
    except ValueError:
        return None
    doc["updated_at"] = row["updated_at"]
    return doc


def put_playbook(user_id: int, doc: Dict[str, Any]) -> None:
    body = {k: v for k, v in doc.items() if k != "updated_at"}
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT INTO playbooks (user_id, id, doc, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, id) DO UPDATE SET doc = excluded.doc, updated_at = excluded.updated_at""",
            (int(user_id), body["id"], json.dumps(body, sort_keys=True), time.time()),
        )
        conn.commit()


def delete_playbook(user_id: int, playbook_id: str) -> bool:
    with closing(_connect()) as conn:
        cur = conn.execute(
            "DELETE FROM playbooks WHERE user_id = ? AND id = ?", (int(user_id), playbook_id)
        )
        conn.commit()
        return cur.rowcount > 0


def default_playbook(user_id: int) -> str:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT default_playbook FROM prefs WHERE user_id = ?", (int(user_id),)
        ).fetchone()
    return str(row["default_playbook"]) if row else ""


def set_default_playbook(user_id: int, playbook_id: str) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT INTO prefs (user_id, default_playbook) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET default_playbook = excluded.default_playbook""",
            (int(user_id), playbook_id),
        )
        conn.commit()


def bind(session_id: int, user_id: int, playbook_id: str, goal: str = "", *,
         action: str = "bind", source: str = "") -> None:
    """Upsert the chat's binding and log it (``action``: bind / switch / goal)."""
    now = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO binding_events (session_id, user_id, action, playbook_id, goal, source, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(session_id), int(user_id), action, playbook_id, goal, source, now),
        )
        conn.execute(
            """INSERT INTO bindings (session_id, user_id, playbook_id, goal, bound_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET playbook_id = excluded.playbook_id,
                   goal = excluded.goal, bound_at = excluded.bound_at""",
            (int(session_id), int(user_id), playbook_id, goal, now),
        )
        conn.commit()


def unbind(session_id: int, *, source: str = "") -> bool:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT user_id, playbook_id FROM bindings WHERE session_id = ?",
                           (int(session_id),)).fetchone()
        cur = conn.execute("DELETE FROM bindings WHERE session_id = ?", (int(session_id),))
        if row:
            conn.execute(
                "INSERT INTO binding_events (session_id, user_id, action, playbook_id, goal, source, at) "
                "VALUES (?, ?, 'unbind', ?, '', ?, ?)",
                (int(session_id), int(row["user_id"]), row["playbook_id"], source, time.time()),
            )
        conn.commit()
        return cur.rowcount > 0


def set_hold(session_id: int, hold: str, *, ops: Optional[List[str]] = None, source: str = "") -> bool:
    """Record the chat's hold (``''`` = running, ``paused``, ``stopped``) and log it.

    ``ops`` are the supervision schedules the hold paused, so resume brings back
    exactly those. False when the chat is not orchestrating."""
    now = time.time()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT user_id, playbook_id, goal FROM bindings WHERE session_id = ?",
                           (int(session_id),)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE bindings SET hold = ?, hold_at = ?, hold_ops = ? WHERE session_id = ?",
                     (hold, now if hold else None, json.dumps(list(ops or [])), int(session_id)))
        conn.execute(
            "INSERT INTO binding_events (session_id, user_id, action, playbook_id, goal, source, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(session_id), int(row["user_id"]), {"paused": "pause", "stopped": "halt"}.get(hold, "resume"),
             row["playbook_id"], row["goal"], source, now),
        )
        conn.commit()
    return True


def binding_events(*, user_id: Optional[int] = None, session_id: Optional[int] = None,
                   since: float = 0.0, limit: int = 5000) -> List[Dict[str, Any]]:
    """Binding history, oldest first (telemetry and "which playbook at time T")."""
    clauses, params = ["at >= ?"], [float(since)]
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(int(user_id))
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(int(session_id))
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM binding_events WHERE " + " AND ".join(clauses) + " ORDER BY at, id LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def binding(session_id: int) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT session_id, user_id, playbook_id, goal, bound_at, hold, hold_at, hold_ops "
            "FROM bindings WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
    return dict(row) if row else None


def list_bindings(user_id: int) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT session_id, user_id, playbook_id, goal, bound_at, hold, hold_at FROM bindings "
            "WHERE user_id = ? ORDER BY bound_at DESC LIMIT 200",
            (int(user_id),),
        ).fetchall()
    return [dict(r) for r in rows]
