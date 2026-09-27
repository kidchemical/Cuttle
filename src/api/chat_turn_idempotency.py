"""Deduplicate first-message /api/chat retries that would mint a second chat.

The welcome splash POSTs with ``session_id`` absent. If the SSE stream errors
and the client retries ``stream=false`` with the same body, a second
``create_chat_session`` used to run (CH-000558 then CH-000559). A stable
``chat_request_id`` on that body maps both POSTs onto the first mint.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional, Tuple

_lock = threading.Lock()
_turns: dict[str, dict[str, Any]] = {}
_TTL_SEC = 15 * 60


def reset_for_tests() -> None:
    with _lock:
        _turns.clear()


def _prune_locked() -> None:
    now = time.time()
    dead = [k for k, row in _turns.items() if (now - float(row.get("at") or 0)) > _TTL_SEC]
    for k in dead:
        _turns.pop(k, None)


def remember_chat_request(request_id: str, session_id: Any, user_id: Any = None) -> None:
    rid = str(request_id or "").strip()
    if not rid or session_id is None:
        return
    with _lock:
        _prune_locked()
        _turns[rid] = {
            "session_id": session_id,
            "user_id": user_id,
            "at": time.time(),
        }


def lookup_chat_request(request_id: str, user_id: Any = None) -> Optional[Any]:
    rid = str(request_id or "").strip()
    if not rid:
        return None
    with _lock:
        _prune_locked()
        row = _turns.get(rid)
        if not row:
            return None
        if user_id is not None and row.get("user_id") is not None:
            if str(row["user_id"]) != str(user_id):
                return None
        return row.get("session_id")


def mint_or_reuse(
    db: Any,
    *,
    user_id: Any,
    chat_session_id: Any,
    chat_request_id: str = "",
) -> Tuple[Any, bool]:
    """Return ``(session_id, created_new)`` for an absent-id first message.

    Callers that already have a session id should not use this helper.
    """
    rid = str(chat_request_id or "").strip()
    if rid:
        existing = lookup_chat_request(rid, user_id=user_id)
        if existing is not None:
            return existing, False
    new_id = db.create_chat_session(user_id)
    if rid:
        remember_chat_request(rid, new_id, user_id=user_id)
    return new_id, True
