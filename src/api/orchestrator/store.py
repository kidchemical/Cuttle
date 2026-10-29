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


def bind(session_id: int, user_id: int, playbook_id: str, goal: str = "") -> None:
    with closing(_connect()) as conn:
        conn.execute(
            """INSERT INTO bindings (session_id, user_id, playbook_id, goal, bound_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET playbook_id = excluded.playbook_id,
                   goal = excluded.goal, bound_at = excluded.bound_at""",
            (int(session_id), int(user_id), playbook_id, goal, time.time()),
        )
        conn.commit()


def unbind(session_id: int) -> bool:
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM bindings WHERE session_id = ?", (int(session_id),))
        conn.commit()
        return cur.rowcount > 0


def binding(session_id: int) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT session_id, user_id, playbook_id, goal, bound_at FROM bindings WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
    return dict(row) if row else None


def list_bindings(user_id: int) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT session_id, user_id, playbook_id, goal, bound_at FROM bindings "
            "WHERE user_id = ? ORDER BY bound_at DESC LIMIT 200",
            (int(user_id),),
        ).fetchall()
    return [dict(r) for r in rows]
