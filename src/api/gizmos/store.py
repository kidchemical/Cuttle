"""Durable gizmo rows (SQLite, install-wide, local-only).

One row per gizmo plus a monotonic ``revision`` counter bumped by every write
(including deletes), so the shell, the App page, desktop pop-outs, and agent
CLIs all converge by polling one integer. Validation lives in
:mod:`api.gizmos.service`; this module stores what it is given.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_db_path() -> Path:
    try:
        from core.runtime_paths import data_db_dir

        return Path(data_db_dir()) / "gizmos.db"
    except Exception:  # pragma: no cover - standalone/test fallback
        return Path(__file__).resolve().parents[2] / "data" / "db" / "gizmos.db"


def database_path() -> Path:
    override = os.getenv("CUTTLE_GIZMOS_DB", "").strip()
    return Path(override).expanduser() if override else _default_db_path()


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gizmos (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL,
            title      TEXT NOT NULL DEFAULT '',
            config     TEXT NOT NULL DEFAULT '{}',
            placement  TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            revision   INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gizmo_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
    )
    conn.execute("INSERT OR IGNORE INTO gizmo_meta (key, value) VALUES ('revision', 0)")
    return conn


def _bump(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE gizmo_meta SET value = value + 1 WHERE key = 'revision'")
    return int(conn.execute("SELECT value FROM gizmo_meta WHERE key = 'revision'").fetchone()[0])


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    out = dict(row)
    for key in ("config", "placement"):
        try:
            value = json.loads(out.get(key) or "{}")
        except (TypeError, ValueError):
            value = {}
        out[key] = value if isinstance(value, dict) else {}
    return out


def current_revision() -> int:
    conn = _connect()
    try:
        conn.commit()
        return int(conn.execute("SELECT value FROM gizmo_meta WHERE key = 'revision'").fetchone()[0])
    finally:
        conn.close()


def list_gizmos() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM gizmos ORDER BY created_at, id").fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def get_gizmo(gizmo_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM gizmos WHERE id = ?", (gizmo_id,)).fetchone()
    finally:
        conn.close()
    return _row(row) if row else None


def insert_gizmo(*, gizmo_id: str, gtype: str, title: str, config: Dict[str, Any],
                 placement: Dict[str, Any], created_by: str) -> Dict[str, Any]:
    now = time.time()
    conn = _connect()
    try:
        with conn:
            rev = _bump(conn)
            conn.execute(
                "INSERT INTO gizmos (id, type, title, config, placement, created_by, "
                "created_at, updated_at, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (gizmo_id, gtype, title, json.dumps(config), json.dumps(placement),
                 created_by, now, now, rev),
            )
    finally:
        conn.close()
    return get_gizmo(gizmo_id) or {}


def update_gizmo(gizmo_id: str, *, title: str, config: Dict[str, Any],
                 placement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        with conn:
            rev = _bump(conn)
            cur = conn.execute(
                "UPDATE gizmos SET title = ?, config = ?, placement = ?, updated_at = ?, "
                "revision = ? WHERE id = ?",
                (title, json.dumps(config), json.dumps(placement), time.time(), rev, gizmo_id),
            )
            if cur.rowcount == 0:
                raise LookupError(gizmo_id)
    except LookupError:
        return None
    finally:
        conn.close()
    return get_gizmo(gizmo_id)


def delete_gizmo(gizmo_id: str) -> bool:
    conn = _connect()
    try:
        with conn:
            cur = conn.execute("DELETE FROM gizmos WHERE id = ?", (gizmo_id,))
            if cur.rowcount:
                _bump(conn)
            return cur.rowcount > 0
    finally:
        conn.close()


def count() -> int:
    conn = _connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM gizmos").fetchone()[0])
    finally:
        conn.close()
