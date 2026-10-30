"""Event-driven guard checks for supervision schedules.

A finished tool call or a worktree bind publishes a session event
(``api.session_events``). This listener notes the chat, then on its own thread
re-checks the guard rules of every schedule watching that chat or one of its
sub-agent ancestors, so a child committing to a forbidden branch is caught too.
Only a new violation wakes the supervisor (``worker.guard_check``); quiet chats
cost nothing. The interval schedule stays the backstop and owns stall detection.
"""
from __future__ import annotations

import logging
import threading

from api.chat_ops import rules, store, worker

log = logging.getLogger(__name__)

# Coalesce a burst of tool calls into one check per chat.
DEBOUNCE_SEC = 2.0
MAX_ANCESTORS = 8
KINDS = ('tool', 'worktree_bound')

_lock = threading.Lock()
_pending = {}
_wake = threading.Event()
_stop = threading.Event()
_thread = None


def on_session_event(kind, session_id, detail):
    """Runs on the publisher's thread (a live agent stream): note it and return."""
    if kind not in KINDS:
        return
    with _lock:
        _pending.setdefault(int(session_id), kind)
    _wake.set()


def _drain():
    with _lock:
        items = dict(_pending)
        _pending.clear()
    return items


def _lineage(db, sid):
    """The chat and its sub-agent ancestors (a child's commit concerns its orchestrator)."""
    chain, current = [], int(sid)
    while current is not None and current not in chain and len(chain) <= MAX_ANCESTORS:
        chain.append(current)
        row = db.get_chat_session_by_id(current) or {}
        parent = row.get('parent_session_id')
        current = int(parent) if parent not in (None, '') else None
    return chain


def run_checks(events, *, db=None, request=None):
    """Guard-check schedules watching the chats in ``events`` ({session_id: kind})."""
    if not events:
        return 0
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    trigger_for = {}
    for sid, kind in events.items():
        for watched in _lineage(db, sid):
            trigger_for.setdefault(watched, f'{kind} in CH-{sid:06d}')
    woke = 0
    for op in store.supervising(trigger_for):
        if not rules.has_guards(op['payload']):
            continue
        try:
            woke += bool(worker.guard_check(op, db, request, trigger=trigger_for.get(op['target'], '')))
        except Exception:
            log.exception('guard check failed for schedule %s', op['id'])
    return woke


def _loop():
    while not _stop.is_set():
        _wake.wait()
        if _stop.wait(DEBOUNCE_SEC):
            return
        _wake.clear()
        try:
            run_checks(_drain())
        except Exception:
            log.exception('guard trigger pump failed')


def start():
    """Subscribe and start the check thread (Flask-owned, alongside the outbox pump)."""
    global _thread
    from api.session_events import subscribe
    subscribe(on_session_event)
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='cuttle-chat-ops-guards', daemon=True)
        _thread.start()
