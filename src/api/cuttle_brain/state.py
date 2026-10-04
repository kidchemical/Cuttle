"""Lifecycle for Brain per-chat state (briefing receipts + handoff cursors).

Both stores are JSON maps keyed by chat id. Without cleanup they only grow:
``forget_chat`` runs on chat delete, ``prune`` sweeps orphans left by chats
deleted earlier and by test runs that wrote into the live data dir.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

# Project paths that only ever come from pytest tmp dirs.
_TEST_PATH_MARKERS = ("pytest-of-", "/temp/pytest-", "\\temp\\pytest-")


def forget_chat(chat_session_id: Any) -> None:
    """Drop every Brain record for one chat (all key spellings)."""
    from api.cuttle_brain.context_delta import clear_injected_snapshot
    from api.cuttle_brain.handoff import clear_last_agent

    for fn in (clear_injected_snapshot, clear_last_agent):
        try:
            fn(chat_session_id)
        except Exception:
            pass


def _numeric(sid: str) -> Optional[int]:
    s = sid[len("db_session_"):] if sid.startswith("db_session_") else sid
    return int(s) if s.isdigit() else None


def _live_session_ids() -> Set[int]:
    from api.auth_db import get_auth_db

    conn = get_auth_db()._get_connection()
    return {int(r[0]) for r in conn.execute("SELECT id FROM chat_sessions")}


def _orphan(sid: str, project: str, live: Set[int]) -> bool:
    num = _numeric(sid)
    if num is not None:
        return num not in live
    return any(marker in project for marker in _TEST_PATH_MARKERS)


def prune(*, dry_run: bool = False, live_ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
    """Remove records for deleted chats and pytest leftovers.

    Numeric chat ids are kept only while the chat still exists; non-numeric
    keys (Discord, legacy handles) are kept unless their project path is a
    pytest tmp dir. Returns removed/kept counts per store.
    """
    from api.cuttle_brain import context_delta as cd
    from api.cuttle_brain import handoff as ho

    live = set(live_ids) if live_ids is not None else _live_session_ids()
    out: Dict[str, int] = {}

    with cd._lock:
        snaps = cd._load_all()
        drop = []
        for key in snaps:
            sid, _, rest = str(key).partition("|")
            _agent, _, project = rest.partition("|")
            if _orphan(sid, project, live):
                drop.append(key)
        if drop and not dry_run:
            for key in drop:
                del snaps[key]
            cd._write_all(snaps)
        out["snapshots_removed"] = len(drop)
        out["snapshots_kept"] = len(snaps) - (len(drop) if dry_run else 0)

    with ho._lock:
        agents = ho._load_all()
        drop = [k for k in agents if _orphan(str(k), "", live)]
        if drop and not dry_run:
            for key in drop:
                del agents[key]
            ho._write_all(agents)
        out["handoff_removed"] = len(drop)
        out["handoff_kept"] = len(agents) - (len(drop) if dry_run else 0)
    return out
