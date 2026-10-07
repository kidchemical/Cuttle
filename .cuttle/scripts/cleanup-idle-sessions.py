#!/usr/bin/env python3
"""Soft-delete Cuttle web chat sessions idle longer than N hours.

Uses the same soft-delete as DELETE /api/auth/sessions/<id> (is_active = 0).
Default keeps starred chats. Optionally removes upload dirs under
<Cuttle home>/output/uploads/<session_id>.

Examples:
  .venv\\Scripts\\python.exe .cuttle\\scripts\\cleanup-idle-sessions.py --hours 24 --dry-run
  .venv\\Scripts\\python.exe .cuttle\\scripts\\cleanup-idle-sessions.py --hours 24
  .venv\\Scripts\\python.exe .cuttle\\scripts\\cleanup-idle-sessions.py --hours 24 --include-starred
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
import os

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.runtime_paths import cuttle_home, output_dir  # noqa: E402

DB_PATH = cuttle_home() / "db" / "cuttle_auth.db"
UPLOADS_ROOT = output_dir() / "uploads"


def _normalize_ts(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def list_idle(
    conn: sqlite3.Connection,
    *,
    hours: float,
    include_starred: bool,
) -> tuple[list[dict], str]:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cs.id, cs.user_id, cs.session_name, cs.starred, cs.created_at,
               cs.last_activity,
               COUNT(cm.id) AS message_count,
               MAX(cm.timestamp) AS last_message_time,
               COALESCE(MAX(cm.timestamp), cs.last_activity, cs.created_at) AS last_used
        FROM chat_sessions cs
        LEFT JOIN chat_messages cm ON cs.id = cm.chat_session_id
        WHERE cs.is_active = 1
        GROUP BY cs.id
        """
    )
    idle: list[dict] = []
    for row in cur.fetchall():
        r = dict(row)
        if not include_starred and int(r.get("starred") or 0):
            continue
        if _normalize_ts(r.get("last_used")) < cutoff:
            idle.append(r)
    idle.sort(key=lambda x: _normalize_ts(x.get("last_used")))
    return idle, cutoff


def soft_delete(conn: sqlite3.Connection, session_ids: list[int]) -> int:
    if not session_ids:
        return 0
    cur = conn.cursor()
    cur.executemany(
        "UPDATE chat_sessions SET is_active = 0 WHERE id = ?",
        [(sid,) for sid in session_ids],
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else len(session_ids)


def cleanup_uploads(session_ids: list[int]) -> int:
    removed = 0
    for sid in session_ids:
        for name in (str(sid),):
            path = UPLOADS_ROOT / name
            if path.exists() and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Idle threshold in hours (default: 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matches only; do not delete",
    )
    parser.add_argument(
        "--include-starred",
        action="store_true",
        help="Also delete starred sessions that are idle",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Path to cuttle_auth.db (default: {DB_PATH})",
    )
    parser.add_argument(
        "--skip-uploads",
        action="store_true",
        help="Do not remove <Cuttle home>/output/uploads/<id> dirs",
    )
    args = parser.parse_args(argv)

    hours_env = (os.environ.get("CUTTLE_PARAM_HOURS") or "").strip()
    if hours_env:
        try:
            args.hours = float(hours_env)
        except ValueError:
            pass
    dry = (os.environ.get("CUTTLE_PARAM_DRY_RUN") or "").strip().lower()
    if dry in ("1", "true", "yes", "on"):
        args.dry_run = True
    star = (os.environ.get("CUTTLE_PARAM_INCLUDE_STARRED") or "").strip().lower()
    if star in ("1", "true", "yes", "on"):
        args.include_starred = True

    if not args.db.exists():
        print(f"ERROR: auth DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        idle, cutoff = list_idle(
            conn,
            hours=args.hours,
            include_starred=args.include_starred,
        )
        print(f"db={args.db}")
        print(f"cutoff_utc={cutoff} (idle > {args.hours:g}h)")
        print(f"include_starred={args.include_starred}")
        print(f"matched={len(idle)}")
        for r in idle:
            name = (r.get("session_name") or "")[:72]
            print(
                f"  id={r['id']} starred={int(r.get('starred') or 0)} "
                f"msgs={r['message_count']} last={r['last_used']} name={name!r}"
            )

        if args.dry_run:
            print("dry_run=1 (no changes)")
            return 0

        ids = [int(r["id"]) for r in idle]
        deleted = soft_delete(conn, ids)
        uploads = 0 if args.skip_uploads else cleanup_uploads(ids)
        print(f"deleted={deleted} upload_dirs_removed={uploads}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
