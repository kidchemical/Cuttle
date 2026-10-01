"""Live generation status store (P4-1 owned service).

Single owner of the cross-device live-status registry that backs history
spinners, the live-status HTTP route, and refresh polls. Moved verbatim
out of the Flask entry module so delivery, auth, run-registry, and
supervised orchestration depend on this purpose-specific service instead
of reverse importing that entry module.

State ownership and lifetimes:

- ``_STORE`` + ``_LOCK``: process lifetime, in-memory only. Same
  semantics as before the move (one dict per Flask process; a Flask
  restart wipes it and chats default idle until live-status confirms —
  restart-sensitive by design, never moved into a transient
  per-request object).
- Keys/values: per-session rows (all id aliases share one entry);
  callers pass plain session ids, never locks or stores.
- Cancel predicate: injected per call (``is_cancelled``), never
  imported — this module must not depend on the Flask entry module or
  the delivery module. The composition root (entry-module wrappers)
  injects the turn-cancelled predicate.

No Flask routes, no persistence writes, no delivery orchestration here.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from api.session_keys import chat_session_keys

# Process-lifetime registry. Keys: normalized session ids
# ("db_session_12" and "12"); Values: status dict. Latest in-flight
# status for cross-device viewers (phone + PC).
_STORE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

# Drop live-status rows that have not been refreshed — otherwise a crashed
# worker leaves history spinners spinning forever (busy has its own TTL).
TTL_SECONDS = 45 * 60
# Initial SSE status is "Connecting..."; if nothing else arrives, expire much sooner
# so Electron/history don't sit on that label until a full Cuttle restart.
CONNECTING_TTL_SECONDS = 90


def _ttl_seconds(entry: Dict[str, Any]) -> float:
    status = (entry or {}).get("status") or ""
    if status.strip() == "Connecting...":
        return float(CONNECTING_TTL_SECONDS)
    return float(TTL_SECONDS)


def live_status_keys(session_id: Any) -> List[str]:
    """All store keys a session id maps to (aliases share one entry)."""
    return chat_session_keys(session_id)


def set_live_status(
    session_id: Any,
    message: Optional[str] = None,
    *,
    active: bool = True,
    report_url: Optional[str] = None,
    query_id: Optional[str] = None,
    is_cancelled: Optional[Callable[[Any], bool]] = None,
    turn: Optional[int] = None,
) -> None:
    """Publish (or refresh) live generation status for all devices watching this session."""
    keys = live_status_keys(session_id)
    if not keys:
        return
    # A dying worker must not republish active=True after Stop — refresh
    # polls read this and would resurrect the spinner (CH-000522).
    if active and is_cancelled is not None:
        try:
            if is_cancelled(session_id):
                return
        except Exception:
            pass
    now = time.time()
    with _LOCK:
        prev = _STORE.get(keys[0]) or {}
        # A producer that passed its freshness check just before Stop/re-send
        # still cannot overwrite a status already published by the newer turn.
        previous_turn = prev.get("turn")
        if turn is not None and previous_turn is not None and previous_turn > turn:
            return
        if turn is not None and previous_turn != turn:
            prev = {}
        entry = {
            "active": bool(active),
            "status": (message if message is not None else prev.get("status")) or "Connecting...",
            "updated_at": now,
            "report_url": report_url if report_url is not None else prev.get("report_url"),
            "query_id": query_id if query_id is not None else prev.get("query_id"),
            "turn": turn if turn is not None else previous_turn,
        }
        for k in keys:
            _STORE[k] = entry


def clear_live_status(session_id: Any, *, turn: Optional[int] = None) -> None:
    keys = live_status_keys(session_id)
    if not keys:
        return
    with _LOCK:
        for k in keys:
            if turn is not None and (_STORE.get(k) or {}).get("turn") not in (None, turn):
                continue
            _STORE.pop(k, None)


def get_live_status(session_id: Any) -> Dict[str, Any]:
    keys = live_status_keys(session_id)
    now = time.time()
    with _LOCK:
        for k in keys:
            entry = _STORE.get(k)
            if not entry:
                continue
            updated = float(entry.get("updated_at") or 0)
            if entry.get("active") and updated and (now - updated) > _ttl_seconds(entry):
                for kk in keys:
                    _STORE.pop(kk, None)
                break
            return dict(entry)
    return {"active": False, "status": None, "updated_at": None, "report_url": None, "query_id": None}


def active_live_session_ids() -> List[str]:
    """Bare session ids with an active live-status entry (for history spinners)."""
    out: List[str] = []
    seen = set()
    now = time.time()
    with _LOCK:
        for k, entry in list(_STORE.items()):
            if not entry or not entry.get("active"):
                continue
            updated = float(entry.get("updated_at") or 0)
            if updated and (now - updated) > _ttl_seconds(entry):
                bare = k[len("db_session_") :] if k.startswith("db_session_") else k
                for kk in live_status_keys(bare):
                    _STORE.pop(kk, None)
                continue
            bare = k[len("db_session_") :] if k.startswith("db_session_") else k
            if not bare or bare in seen:
                continue
            seen.add(bare)
            out.append(bare)
    return out
