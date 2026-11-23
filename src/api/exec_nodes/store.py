"""SQLite state for execution nodes (Cuttle home, host side only).

Durable: a chat's node, per-node preferences, the CLI resume id a chat holds
on each node. Transient: one ``turns`` row + its ``turn_events`` while a turn
runs on a node. They are the relay between the node's HTTP posts (Flask) and
the process waiting on the turn, which may be Flask or a sub-agent CLI, so it
has to cross processes; both are deleted when the turn ends.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_paths import data_db_dir

# A relay row nobody closed (its waiter died): dropped after this long.
_STALE_TURN_SECONDS = 6 * 3600


def _db_path() -> Path:
    return data_db_dir() / "exec_nodes.db"


_ready: set = set()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    if str(path) in _ready:
        return conn
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_nodes (
        session_id INTEGER PRIMARY KEY,
        node_id TEXT NOT NULL,
        cwd TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS node_prefs (
        node_id TEXT PRIMARY KEY,
        label TEXT NOT NULL DEFAULT '',
        default_cwd TEXT NOT NULL DEFAULT '',
        paths TEXT NOT NULL DEFAULT '{}'
    )""")
    # Where each agent of a chat last ran, and its CLI session on each node.
    conn.execute("""CREATE TABLE IF NOT EXISTS last_node (
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        PRIMARY KEY (session_id, agent_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS resume (
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        cwd TEXT NOT NULL,
        cli_session_id TEXT NOT NULL,
        PRIMARY KEY (session_id, agent_id, node_id, cwd)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS turns (
        job_id TEXT PRIMARY KEY,
        node_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        cancel INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        node_seen REAL,
        created_at REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS turn_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        item TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS turn_events_job ON turn_events(job_id, id)")
    conn.commit()
    _ready.add(str(path))
    return conn


# -- chat binding ---------------------------------------------------------

def get_chat_node(session_id: int) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT node_id, cwd, updated_at FROM chat_nodes WHERE session_id = ?", (int(session_id),)
        ).fetchone()
    return dict(row) if row else None


def set_chat_node(session_id: int, node_id: str, cwd: str = "") -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO chat_nodes (session_id, node_id, cwd, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET node_id = excluded.node_id, cwd = excluded.cwd, "
            "updated_at = excluded.updated_at",
            (int(session_id), node_id, cwd, time.time()),
        )
        conn.commit()


def clear_chat_node(session_id: int) -> None:
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM chat_nodes WHERE session_id = ?", (int(session_id),))
        conn.commit()


def chats_on_node(node_id: str) -> List[int]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT session_id FROM chat_nodes WHERE node_id = ?", (node_id,)).fetchall()
    return [int(r["session_id"]) for r in rows]


# -- node preferences -----------------------------------------------------

def get_prefs(node_id: str) -> Dict[str, Any]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT label, default_cwd, paths FROM node_prefs WHERE node_id = ?", (node_id,)
        ).fetchone()
    if not row:
        return {"label": "", "default_cwd": "", "paths": {}}
    try:
        paths = json.loads(row["paths"] or "{}")
    except ValueError:
        paths = {}
    return {"label": row["label"], "default_cwd": row["default_cwd"],
            "paths": paths if isinstance(paths, dict) else {}}


def set_prefs(node_id: str, *, label: str, default_cwd: str, paths: Dict[str, str]) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO node_prefs (node_id, label, default_cwd, paths) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(node_id) DO UPDATE SET label = excluded.label, "
            "default_cwd = excluded.default_cwd, paths = excluded.paths",
            (node_id, label, default_cwd, json.dumps(paths, ensure_ascii=False)),
        )
        conn.commit()


# -- where an agent last ran + its CLI session per node -------------------

def swap_last_node(session_id: str, agent_id: str, node_id: str) -> Optional[str]:
    """Record ``node_id`` as where this agent runs now; returns the previous
    node (``None`` when it never ran through here before)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT node_id FROM last_node WHERE session_id = ? AND agent_id = ?", (session_id, agent_id)
        ).fetchone()
        previous = row["node_id"] if row else None
        # A chat that never left the host keeps no row at all.
        if previous != node_id and not (previous is None and node_id == "host"):
            conn.execute(
                "INSERT INTO last_node (session_id, agent_id, node_id) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id, agent_id) DO UPDATE SET node_id = excluded.node_id",
                (session_id, agent_id, node_id),
            )
            conn.commit()
    return previous


def load_resume(session_id: str, agent_id: str, node_id: str, cwd: str) -> Optional[str]:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT cli_session_id FROM resume WHERE session_id = ? AND agent_id = ? AND node_id = ? AND cwd = ?",
            (session_id, agent_id, node_id, cwd),
        ).fetchone()
    return row["cli_session_id"] if row else None


def save_resume(session_id: str, agent_id: str, node_id: str, cwd: str, cli_session_id: Optional[str]) -> None:
    with closing(_connect()) as conn:
        if cli_session_id:
            conn.execute(
                "INSERT INTO resume (session_id, agent_id, node_id, cwd, cli_session_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, agent_id, node_id, cwd) DO UPDATE SET cli_session_id = excluded.cli_session_id",
                (session_id, agent_id, node_id, cwd, cli_session_id),
            )
        else:
            conn.execute(
                "DELETE FROM resume WHERE session_id = ? AND agent_id = ? AND node_id = ? AND cwd = ?",
                (session_id, agent_id, node_id, cwd),
            )
        conn.commit()


# -- turn relay (transient) ----------------------------------------------

def open_turn(job_id: str, node_id: str, session_id: str, payload: Dict[str, Any]) -> None:
    now = time.time()
    with closing(_connect()) as conn:
        stale = [r["job_id"] for r in conn.execute(
            "SELECT job_id FROM turns WHERE created_at < ?", (now - _STALE_TURN_SECONDS,))]
        for old in stale:
            conn.execute("DELETE FROM turn_events WHERE job_id = ?", (old,))
            conn.execute("DELETE FROM turns WHERE job_id = ?", (old,))
        conn.execute(
            "INSERT INTO turns (job_id, node_id, session_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, node_id, session_id, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()


def close_turn(job_id: str) -> None:
    """Drop the relay row: the prompt and reply stay only in chat history."""
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM turn_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM turns WHERE job_id = ?", (job_id,))
        conn.commit()


def turn_payload(job_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    """The payload for the node that owns this turn (``None`` for anyone else)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT payload FROM turns WHERE job_id = ? AND node_id = ?", (job_id, node_id)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE turns SET node_seen = ? WHERE job_id = ?", (time.time(), job_id))
        conn.commit()
    try:
        data = json.loads(row["payload"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def post_events(job_id: str, node_id: str, items: List[Any], result: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Append a node's status items (and its final result). Returns the
    turn's cancel flag, or ``None`` when the turn is gone or not this node's."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT cancel FROM turns WHERE job_id = ? AND node_id = ?", (job_id, node_id)
        ).fetchone()
        if not row:
            return None
        for item in items:
            conn.execute("INSERT INTO turn_events (job_id, item) VALUES (?, ?)",
                         (job_id, json.dumps(item, ensure_ascii=False)))
        if result is not None:
            conn.execute("UPDATE turns SET result = ?, node_seen = ? WHERE job_id = ?",
                         (json.dumps(result, ensure_ascii=False), time.time(), job_id))
        else:
            conn.execute("UPDATE turns SET node_seen = ? WHERE job_id = ?", (time.time(), job_id))
        conn.commit()
    return bool(row["cancel"])


def read_turn(job_id: str, after: int = 0) -> Tuple[List[Tuple[int, Any]], Optional[Dict[str, Any]]]:
    """``([(event id, item), …] after the cursor, turn state)``; state is
    ``{"result", "node_seen", "cancel"}`` or ``None`` when the row is gone."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, item FROM turn_events WHERE job_id = ? AND id > ? ORDER BY id", (job_id, int(after))
        ).fetchall()
        turn = conn.execute(
            "SELECT result, node_seen, cancel FROM turns WHERE job_id = ?", (job_id,)
        ).fetchone()
    events: List[Tuple[int, Any]] = []
    for row in rows:
        try:
            events.append((int(row["id"]), json.loads(row["item"])))
        except ValueError:
            continue
    if not turn:
        return events, None
    result = None
    if turn["result"]:
        try:
            result = json.loads(turn["result"])
        except ValueError:
            result = {"success": False, "error": "unreadable result from node"}
    return events, {"result": result, "node_seen": turn["node_seen"], "cancel": bool(turn["cancel"])}


def request_cancel(job_id: str) -> None:
    with closing(_connect()) as conn:
        conn.execute("UPDATE turns SET cancel = 1 WHERE job_id = ?", (job_id,))
        conn.commit()


def running_turns() -> Dict[str, int]:
    """Node id → turns currently relayed through this host."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT node_id, COUNT(*) AS n FROM turns GROUP BY node_id").fetchall()
    return {r["node_id"]: int(r["n"]) for r in rows}
