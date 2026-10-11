"""Runtime SQLite history for Test Lab runs."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import data_db_dir


def _db_path() -> Path:
    return data_db_dir() / "test_lab.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS test_runs (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL,
        project_name TEXT NOT NULL,
        suite_id TEXT NOT NULL,
        suite_label TEXT NOT NULL,
        framework TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        duration_s REAL,
        exit_code INTEGER,
        output TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_test_runs_project_started "
                 "ON test_runs(project_id, started_at DESC)")
    conn.commit()
    return conn


def create_run(run: Dict[str, Any]) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT id FROM test_runs WHERE project_id = ? AND status IN ('queued','running') LIMIT 1",
            (int(run["project_id"]),),
        ).fetchone()
        if active:
            raise RuntimeError("A test run is already active for this project.")
        conn.execute(
            "INSERT INTO test_runs (id, project_id, project_name, suite_id, suite_label, "
            "framework, status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
            (run["id"], int(run["project_id"]), run["project_name"], run["suite_id"],
             run["suite_label"], run["framework"], time.time()),
        )
        conn.execute(
            "DELETE FROM test_runs WHERE id NOT IN "
            "(SELECT id FROM test_runs ORDER BY started_at DESC LIMIT 500)"
        )
        conn.commit()
    finally:
        conn.close()


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {"status", "finished_at", "duration_s", "exit_code", "output", "error"}
    changes = [(key, value) for key, value in fields.items() if key in allowed]
    if not changes:
        return
    assignments = ", ".join(f"{key} = ?" for key, _ in changes)
    with closing(_connect()) as conn:
        conn.execute(f"UPDATE test_runs SET {assignments} WHERE id = ?",
                     (*[value for _, value in changes], str(run_id)))
        conn.commit()


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE test_runs SET status = 'interrupted', finished_at = ?, "
            "error = 'Cuttle restarted before the test run completed.' "
            "WHERE id = ? AND status IN ('queued','running') AND started_at < ?",
            (time.time(), str(run_id), time.time() - 25 * 60),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM test_runs WHERE id = ?", (str(run_id),)).fetchone()
        return _row(row) if row else None


def recent_runs(project_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE test_runs SET status = 'interrupted', finished_at = ?, "
            "error = 'Cuttle restarted before the test run completed.' "
            "WHERE project_id = ? AND status IN ('queued','running') AND started_at < ?",
            (time.time(), int(project_id), time.time() - 25 * 60),
        )
        rows = conn.execute(
            "SELECT * FROM test_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
            (int(project_id), max(1, min(int(limit), 100))),
        ).fetchall()
        conn.commit()
        return [_row(row) for row in rows]
