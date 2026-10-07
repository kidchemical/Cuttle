"""Durable achievement state (SQLite, local-only).

One table of progress/unlock state plus one table of unlock events. Schema is
created idempotently at open time, matching the ``api.agent_router.outcomes``
convention (imperative DDL, ``PRAGMA table_info`` column adds, WAL).

The catalog (vocabulary) lives in :mod:`api.achievements.catalog`; only state
lives here. Deleting ``achievements.db`` resets progress without touching code.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_db_path() -> Path:
    from core.runtime_paths import data_db_dir

    return data_db_dir() / "achievements.db"


DEFAULT_DB_PATH = _default_db_path()


def database_path() -> Path:
    override = os.getenv("CUTTLE_ACHIEVEMENTS_DB", "").strip()
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS achievement_state (
            achievement_id TEXT PRIMARY KEY,
            progress      REAL NOT NULL DEFAULT 0,
            target        REAL,
            unlocked_at   REAL,
            seen_at       REAL,
            updated_at    REAL NOT NULL DEFAULT 0,
            detail        TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS achievement_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            achievement_id TEXT NOT NULL,
            unlocked_at    REAL NOT NULL,
            acknowledged   INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_achievement_events_ack "
        "ON achievement_events(acknowledged, unlocked_at)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(achievement_state)")}
    if "seen_at" not in columns:  # pragma: no cover - upgrade path
        conn.execute("ALTER TABLE achievement_state ADD COLUMN seen_at REAL")
    return conn


def load_state(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """``{achievement_id: {progress, unlocked_at, seen_at, detail}}``."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT achievement_id, progress, target, unlocked_at, seen_at, detail "
            "FROM achievement_state"
        ).fetchall()
    finally:
        conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        detail = None
        if row["detail"]:
            try:
                detail = json.loads(row["detail"])
            except (TypeError, ValueError):
                detail = None
        out[row["achievement_id"]] = {
            "progress": float(row["progress"] or 0.0),
            "target": row["target"],
            "unlocked_at": row["unlocked_at"],
            "seen_at": row["seen_at"],
            "detail": detail,
        }
    return out


def record_progress(
    achievement_id: str,
    progress: float,
    target: Optional[float] = None,
    *,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Upsert progress for one achievement. Never lowers an existing value."""
    value = max(0.0, float(progress or 0.0))
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT progress, target, unlocked_at, seen_at FROM achievement_state "
            "WHERE achievement_id = ?",
            (achievement_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO achievement_state "
                "(achievement_id, progress, target, updated_at) VALUES (?, ?, ?, ?)",
                (achievement_id, value, target, time.time()),
            )
            conn.commit()
            return {"achievement_id": achievement_id, "progress": value,
                    "unlocked_at": None, "seen_at": None, "changed": True}
        prior = float(row["progress"] or 0.0)
        new_value = max(prior, value)  # progress is monotonic
        changed = abs(new_value - prior) > 1e-9
        if changed or row["target"] is None:
            conn.execute(
                "UPDATE achievement_state SET progress = ?, target = ?, updated_at = ? "
                "WHERE achievement_id = ?",
                (new_value, target, time.time(), achievement_id),
            )
            conn.commit()
        return {
            "achievement_id": achievement_id,
            "progress": new_value,
            "unlocked_at": row["unlocked_at"],
            "seen_at": row["seen_at"],
            "changed": changed,
        }
    finally:
        conn.close()


def mark_unlocked(
    achievement_id: str,
    detail: Optional[dict] = None,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """Set ``unlocked_at`` + enqueue an event. Returns True only the first time."""
    now = time.time()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT unlocked_at FROM achievement_state WHERE achievement_id = ?",
            (achievement_id,),
        ).fetchone()
        if row is not None and row["unlocked_at"] is not None:
            return False  # already unlocked — idempotent re-scan
        payload = json.dumps(detail) if detail else None
        if row is None:
            conn.execute(
                "INSERT INTO achievement_state "
                "(achievement_id, progress, target, unlocked_at, updated_at, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (achievement_id, 0.0, None, now, now, payload),
            )
        else:
            conn.execute(
                "UPDATE achievement_state SET unlocked_at = ?, updated_at = ?, detail = ? "
                "WHERE achievement_id = ?",
                (now, now, payload, achievement_id),
            )
        conn.execute(
            "INSERT INTO achievement_events (achievement_id, unlocked_at, acknowledged) "
            "VALUES (?, ?, 0)",
            (achievement_id, now),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_seen(achievement_id: str, *, db_path: Optional[Path] = None) -> bool:
    """Acknowledge the celebration. Returns True when something changed."""
    now = time.time()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE achievement_state SET seen_at = ? "
            "WHERE achievement_id = ? AND unlocked_at IS NOT NULL AND seen_at IS NULL",
            (now, achievement_id),
        )
        conn.execute(
            "UPDATE achievement_events SET acknowledged = 1 "
            "WHERE achievement_id = ? AND acknowledged = 0",
            (achievement_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def pending_unlocks(
    catalog_payload: Optional[List[Dict[str, Any]]] = None,
    *,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Unlocked-but-unseen achievements, oldest first, enriched from the catalog."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT s.achievement_id, s.unlocked_at, s.progress, s.detail "
            "FROM achievement_state s "
            "LEFT JOIN achievement_events e "
            "  ON e.achievement_id = s.achievement_id AND e.acknowledged = 0 "
            "WHERE s.unlocked_at IS NOT NULL AND e.id IS NOT NULL "
            "ORDER BY s.unlocked_at ASC"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    by_id = {a["id"]: a for a in (catalog_payload or [])}
    out: List[Dict[str, Any]] = []
    for row in rows:
        entry = dict(by_id.get(row["achievement_id"], {"id": row["achievement_id"]}))
        detail = None
        if row["detail"]:
            try:
                detail = json.loads(row["detail"])
            except (TypeError, ValueError):
                detail = None
        entry.update(
            {
                "unlocked_at": row["unlocked_at"],
                "progress": float(row["progress"] or 0.0),
                "detail": detail,
            }
        )
        out.append(entry)
    return out


def reset(db_path: Optional[Path] = None) -> int:
    """Wipe progress and events. Returns the number of achievements cleared."""
    conn = _connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM achievement_state").fetchone()[0]
        conn.execute("DELETE FROM achievement_events")
        conn.execute("DELETE FROM achievement_state")
        conn.commit()
        return int(count)
    finally:
        conn.close()


def grant(
    achievement_id: str,
    detail: Optional[dict] = None,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """Manual unlock (testing / "give me the achievement I earned offline")."""
    return mark_unlocked(achievement_id, detail or {"granted": True}, db_path=db_path)