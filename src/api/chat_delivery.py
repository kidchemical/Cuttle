"""
Decouple chat replies from the browser's SSE connection.

A streamed chat reply used to exist only inside the SSE generator: if the tab
was closed, refreshed, or the message was re-sent, the generator died and the
finished reply was thrown away — the agent had done all the work for nothing.

This module keeps two pieces of state per chat session, owned by the worker
thread rather than the connection:

* **busy** — a reply is being generated right now, so a second send for the
  same chat can be rejected instead of spawning a duplicate agent run.
* **pending result** — a finished reply the browser never received. The client
  polls for it after a dropped stream and the reply is handed over then.

Both are in-memory: a Flask restart drops them, which is fine because the runs
they describe die with it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

_lock = threading.Lock()

# session_key -> {'result': dict, 'at': float}
_pending: Dict[str, Dict[str, Any]] = {}
# session_key -> {'since': float, 'query_id': str | None, 'turn': int}
_busy: Dict[str, Dict[str, Any]] = {}
# Highest turn number ever handed out for a session key. A worker that outlived
# its turn (user pressed Stop, then re-sent) compares against this so it cannot
# persist its late answer into the new turn or release the new turn's lock.
_last_turn: Dict[str, int] = {}
_turn_counter = 0
# Session keys whose live turn was cancelled by Stop. Sticky until the next
# try_begin so a cancel that lands before the CLI is spawned still aborts.
_cancel_sticky: Dict[str, int] = {}

# Undelivered replies older than this are dropped so the store cannot grow
# without bound; the reply is still saved in the chat history either way.
_PENDING_TTL = 3600.0
# A run stuck this long is treated as dead, so a wedged worker cannot lock a
# chat out of sending forever.
_BUSY_TTL = 7200.0
# If busy is held but there is no live status and no live subprocess for this
# long, treat it as a zombie lock (agent died without chat_delivery.end).
# Keeps history spinners from sticking after a crashed /cursor run.
_BUSY_ZOMBIE_GRACE = 90.0


def _session_keys(session_id) -> List[str]:
    from api.session_keys import chat_session_keys

    return chat_session_keys(session_id)


def _bare_session_id(key: str) -> str:
    """Canonical numeric id when the key is a Cuttle handle, else the key."""
    from api.session_keys import bare_chat_session_id

    return bare_chat_session_id(key)


def _purge_locked(now: float) -> None:
    for k, v in list(_pending.items()):
        if now - v.get("at", 0) > _PENDING_TTL:
            _pending.pop(k, None)
    for k, v in list(_busy.items()):
        if now - v.get("since", 0) > _BUSY_TTL:
            _busy.pop(k, None)


# ── busy tracking ────────────────────────────────────────────────────────────

def try_begin(session_id, query_id: Optional[str] = None) -> bool:
    """Claim the chat for a new reply. False if one is already generating."""
    global _turn_counter
    keys = _session_keys(session_id)
    if not keys:
        return True  # no id to gate on (brand-new anonymous chat)
    now = time.time()
    with _lock:
        _purge_locked(now)
        for k in keys:
            if k in _busy:
                return False
        # Drop any parked reply from a prior turn. Leaving it caused a detached
        # client to collectPendingResult the old answer mid-run (chirp + typing
        # indicator gone while the new agent was still working).
        for k in keys:
            _pending.pop(k, None)
            _cancel_sticky.pop(k, None)
        _turn_counter += 1
        entry = {"since": now, "query_id": query_id, "turn": _turn_counter}
        for k in keys:
            _busy[k] = entry
            _last_turn[k] = _turn_counter
    return True


def current_turn(session_id) -> Optional[int]:
    """Turn number of the reply currently being generated for this chat."""
    keys = _session_keys(session_id)
    with _lock:
        for k in keys:
            entry = _busy.get(k)
            if entry:
                return entry.get("turn")
    return None


def is_stale_turn(session_id, turn: Optional[int]) -> bool:
    """True when a newer turn has since claimed this chat.

    A stale worker must not persist its answer, park it for the client, or
    release the live turn's busy lock — that is how a 30-minute-late router
    result ended up reading as a reply to a much newer message.
    """
    if turn is None:
        return False
    keys = _session_keys(session_id)
    with _lock:
        for k in keys:
            last = _last_turn.get(k)
            if last is not None and last > int(turn):
                return True
    return False


def cancel_current_turn(session_id) -> Optional[int]:
    """Stop owns this chat: bump the turn, drop busy, drop any parked reply.

    Stop without a follow-up send used to only ``end()`` the busy lock. The
    worker kept the same turn token, so ``is_stale_turn`` stayed false and the
    answer still landed in history after the UI said generation was stopped.
    """
    global _turn_counter
    keys = _session_keys(session_id)
    if not keys:
        return None
    now = time.time()
    with _lock:
        _purge_locked(now)
        old_turn = None
        for k in keys:
            entry = _busy.get(k)
            if entry and entry.get("turn") is not None:
                old_turn = int(entry["turn"])
                break
        _turn_counter += 1
        sentinel = _turn_counter
        for k in keys:
            _last_turn[k] = sentinel
            _busy.pop(k, None)
            _pending.pop(k, None)
            _cancel_sticky[k] = sentinel
    try:
        from api.flask_restart import maybe_fire_when_idle

        maybe_fire_when_idle()
    except Exception:
        pass
    return old_turn


def is_turn_cancelled(session_id) -> bool:
    """True between Stop and the next ``try_begin`` for this chat."""
    keys = _session_keys(session_id)
    with _lock:
        return any(k in _cancel_sticky for k in keys)


def end(session_id, turn: Optional[int] = None) -> None:
    keys = _session_keys(session_id)
    if not keys:
        return
    with _lock:
        for k in keys:
            entry = _busy.get(k)
            if entry is None:
                continue
            if turn is not None and entry.get("turn") not in (None, int(turn)):
                continue  # lock belongs to a newer turn
            _busy.pop(k, None)
    try:
        from api.flask_restart import maybe_fire_when_idle

        maybe_fire_when_idle()
    except Exception:
        pass


def is_busy(session_id) -> bool:
    keys = _session_keys(session_id)
    if not keys:
        return False
    now = time.time()
    # Self-heal before answering — a wedged lock should not block sends or
    # keep the history spinner spinning after the agent is gone.
    reconcile_zombie_busy(now)
    with _lock:
        _purge_locked(now)
        return any(k in _busy for k in keys)


def busy_since(session_id) -> Optional[float]:
    keys = _session_keys(session_id)
    with _lock:
        for k in keys:
            entry = _busy.get(k)
            if entry:
                return entry.get("since")
    return None


def _session_has_live_work(session_id) -> bool:
    """True if live status or a tracked subprocess still backs this busy lock."""
    try:
        from api.chat_live_status import get_live_status

        live = get_live_status(session_id)
        if live and live.get("active"):
            return True
    except Exception:
        pass
    try:
        from api.chat_run_registry import has_live_process

        if has_live_process(session_id):
            return True
    except Exception:
        pass
    return False


def reconcile_zombie_busy(now: Optional[float] = None) -> List[str]:
    """
    Drop busy locks that outlived their worker (no live status, no live proc).

    Returns bare session ids that were cleared. Safe to call from history /
    live-status reads so spinners self-heal without a Flask restart.
    """
    now = time.time() if now is None else now
    cleared: List[str] = []
    with _lock:
        _purge_locked(now)
        candidates = []
        seen = set()
        for k, entry in list(_busy.items()):
            bare = _bare_session_id(k)
            if not bare or bare in seen:
                continue
            seen.add(bare)
            since = float(entry.get("since") or 0)
            if now - since < _BUSY_ZOMBIE_GRACE:
                continue
            candidates.append(bare)
    for bare in candidates:
        if _session_has_live_work(bare):
            continue
        end(bare)
        cleared.append(bare)
        try:
            from api.chat_live_status import clear_live_status

            clear_live_status(bare)
        except Exception:
            pass
    return cleared


def busy_session_ids() -> List[str]:
    """Bare session id strings currently generating (for chat history badges)."""
    now = time.time()
    reconcile_zombie_busy(now)
    out: List[str] = []
    seen = set()
    with _lock:
        _purge_locked(now)
        for k in _busy.keys():
            bare = _bare_session_id(k)
            if not bare or bare in seen:
                continue
            seen.add(bare)
            out.append(bare)
    return out


def busy_entries() -> List[Dict[str, Any]]:
    """Busy chats with their query_id, so callers can dedupe against job records.

    A single agent turn registers both a busy lock here and an entry in
    active_executions; both carry the same query_id.
    """
    now = time.time()
    reconcile_zombie_busy(now)
    out: List[Dict[str, Any]] = []
    seen = set()
    with _lock:
        _purge_locked(now)
        for k, entry in _busy.items():
            bare = _bare_session_id(k)
            if not bare or bare in seen:
                continue
            seen.add(bare)
            out.append(
                {
                    "session_id": bare,
                    "query_id": entry.get("query_id"),
                    "since": entry.get("since"),
                }
            )
    return out


# ── pending results ──────────────────────────────────────────────────────────

def store_result(session_id, result: Dict[str, Any]) -> None:
    """Hold a finished reply until the client confirms it arrived."""
    keys = _session_keys(session_id)
    if not keys or not isinstance(result, dict):
        return
    now = time.time()
    entry = {"result": result, "at": now}
    with _lock:
        _purge_locked(now)
        for k in keys:
            _pending[k] = entry


def take_result(session_id) -> Optional[Dict[str, Any]]:
    """Pop the undelivered reply for this chat, if any."""
    keys = _session_keys(session_id)
    if not keys:
        return None
    with _lock:
        entry = None
        for k in keys:
            if k in _pending:
                entry = _pending.pop(k)
        for k in keys:
            _pending.pop(k, None)
    return entry.get("result") if entry else None


def peek_result(session_id) -> Optional[Dict[str, Any]]:
    """Read the undelivered reply without consuming it (multi-device safe)."""
    keys = _session_keys(session_id)
    if not keys:
        return None
    with _lock:
        for k in keys:
            entry = _pending.get(k)
            if entry:
                return entry.get("result")
    return None


def clear_result(session_id) -> None:
    """Called once the reply has actually reached the browser."""
    keys = _session_keys(session_id)
    if not keys:
        return
    with _lock:
        for k in keys:
            _pending.pop(k, None)
