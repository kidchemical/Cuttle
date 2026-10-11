"""Checkout registry — persistent worktree identity in the per-user projects.db.

Owns the ``checkouts`` table in ``<home>/db/projects.db`` (the same database
``managers.project_manager`` owns for the ``projects`` registry). Only worktrees
get a row: the project's registered path is the *implicit* primary checkout and
is derived, never persisted here.

Identity is ``checkout_uid`` (branch-independent). ``branch`` is advisory and may
change without moving the directory. Nothing here creates git worktrees — that is
``api.git_service``'s job; this module only records/reads their identity.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from core.runtime_paths import data_db_dir

from api.worktrees.models import Checkout

VALID_STATES = ("creating", "ready", "removing", "missing", "broken")
VALID_KINDS = ("primary", "worktree")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkout_uid TEXT UNIQUE NOT NULL,
    project_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('primary', 'worktree')),
    path TEXT UNIQUE NOT NULL,
    git_worktree_name TEXT,
    base_branch TEXT NOT NULL DEFAULT '',
    branch TEXT,
    state TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkouts_project ON checkouts (project_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def db_path() -> Path:
    """Default registry database (the per-user projects.db)."""
    return data_db_dir() / "projects.db"


@contextmanager
def _connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path or db_path()), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> Checkout:
    return Checkout(
        checkout_uid=row["checkout_uid"],
        project_id=int(row["project_id"]),
        kind=row["kind"],
        path=row["path"],
        git_worktree_name=row["git_worktree_name"],
        base_branch=row["base_branch"] or "",
        branch=row["branch"],
        state=row["state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used=row["last_used"],
    )


def _log_history(conn: sqlite3.Connection, project_id: int, action: str, details: dict) -> None:
    """Best-effort audit row; the projects registry table may not exist yet."""
    try:
        conn.execute(
            "INSERT INTO project_history (project_id, action, details) VALUES (?, ?, ?)",
            (int(project_id), action, json.dumps(details)),
        )
    except sqlite3.OperationalError:
        pass


def create_checkout(
    project_id: int,
    path: str,
    *,
    kind: str = "worktree",
    base_branch: str = "",
    branch: Optional[str] = None,
    git_worktree_name: Optional[str] = None,
    checkout_uid: Optional[str] = None,
    state: str = "ready",
    db_path_override: Optional[Path] = None,
) -> Checkout:
    """Insert a checkout row. Raises sqlite3.IntegrityError on duplicate uid/path."""
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid checkout kind: {kind!r}")
    if state not in VALID_STATES:
        raise ValueError(f"invalid checkout state: {state!r}")
    uid = checkout_uid or uuid.uuid4().hex
    resolved = _abs(path)
    now = _now()
    with _connect(db_path_override) as conn:
        conn.execute(
            """
            INSERT INTO checkouts
                (checkout_uid, project_id, kind, path, git_worktree_name,
                 base_branch, branch, state, created_at, updated_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, int(project_id), kind, resolved, git_worktree_name,
             base_branch or "", branch, state, now, now, now),
        )
        _log_history(conn, int(project_id), "checkout_created",
                     {"checkout_uid": uid, "kind": kind, "path": resolved, "branch": branch})
        row = conn.execute("SELECT * FROM checkouts WHERE checkout_uid = ?", (uid,)).fetchone()
    return _row(row)


def get_checkout(checkout_uid: str, *, db_path_override: Optional[Path] = None) -> Optional[Checkout]:
    with _connect(db_path_override) as conn:
        row = conn.execute(
            "SELECT * FROM checkouts WHERE checkout_uid = ?", (str(checkout_uid),)
        ).fetchone()
    return _row(row) if row else None


def checkout_for_path(path: str, *, db_path_override: Optional[Path] = None) -> Optional[Checkout]:
    """The worktree checkout bound to a path, or ``None`` (primary/implicit)."""
    with _connect(db_path_override) as conn:
        row = conn.execute(
            "SELECT * FROM checkouts WHERE path = ?", (_abs(path),)
        ).fetchone()
    return _row(row) if row else None


def list_checkouts(
    project_id: Optional[int] = None,
    *,
    kind: Optional[str] = None,
    db_path_override: Optional[Path] = None,
) -> list[Checkout]:
    clauses, params = [], []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(int(project_id))
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path_override) as conn:
        rows = conn.execute(
            f"SELECT * FROM checkouts{where} ORDER BY created_at, id", tuple(params)
        ).fetchall()
    return [_row(r) for r in rows]


def checkout_belongs_to(
    checkout_uid: str,
    project_id: int,
    *,
    db_path_override: Optional[Path] = None,
) -> bool:
    with _connect(db_path_override) as conn:
        row = conn.execute(
            "SELECT 1 FROM checkouts WHERE checkout_uid = ? AND project_id = ?",
            (str(checkout_uid), int(project_id)),
        ).fetchone()
    return row is not None


def mark_state(
    checkout_uid: str,
    state: str,
    *,
    db_path_override: Optional[Path] = None,
) -> None:
    if state not in VALID_STATES:
        raise ValueError(f"invalid checkout state: {state!r}")
    with _connect(db_path_override) as conn:
        conn.execute(
            "UPDATE checkouts SET state = ?, updated_at = ? WHERE checkout_uid = ?",
            (state, _now(), str(checkout_uid)),
        )


def touch_checkout(checkout_uid: str, *, db_path_override: Optional[Path] = None) -> None:
    """Record use (drives future non-destructive cleanup ordering)."""
    now = _now()
    with _connect(db_path_override) as conn:
        conn.execute(
            "UPDATE checkouts SET last_used = ?, updated_at = ? WHERE checkout_uid = ?",
            (now, now, str(checkout_uid)),
        )


def remove_checkout(
    checkout_uid: str,
    *,
    db_path_override: Optional[Path] = None,
) -> bool:
    """Delete a checkout row (registry only; never touches the git worktree)."""
    with _connect(db_path_override) as conn:
        row = conn.execute(
            "SELECT project_id, path FROM checkouts WHERE checkout_uid = ?",
            (str(checkout_uid),),
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM checkouts WHERE checkout_uid = ?", (str(checkout_uid),))
        _log_history(conn, int(row["project_id"]), "checkout_removed",
                     {"checkout_uid": str(checkout_uid), "path": row["path"]})
    return True


def reconcile_checkouts(*, db_path_override: Optional[Path] = None) -> dict:
    """Mark rows whose directory vanished as ``missing`` (read-only otherwise).

    Never removes git state and never deletes a row. ``creating`` rows are left
    for the creating caller to finish or discard.
    """
    marked = []
    with _connect(db_path_override) as conn:
        rows = conn.execute("SELECT * FROM checkouts").fetchall()
        for row in rows:
            if row["state"] == "creating":
                continue
            if not os.path.isdir(row["path"]):
                if row["state"] != "missing":
                    conn.execute(
                        "UPDATE checkouts SET state = 'missing', updated_at = ? WHERE checkout_uid = ?",
                        (_now(), row["checkout_uid"]),
                    )
                    marked.append(row["checkout_uid"])
            elif row["state"] == "missing":
                conn.execute(
                    "UPDATE checkouts SET state = 'ready', updated_at = ? WHERE checkout_uid = ?",
                    (_now(), row["checkout_uid"]),
                )
    return {"checked": len(rows), "marked_missing": marked}
