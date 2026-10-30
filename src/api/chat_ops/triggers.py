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
_commands = {}
MAX_COMMANDS = 50
_wake = threading.Event()
_stop = threading.Event()
_thread = None


def on_session_event(kind, session_id, detail):
    """Runs on the publisher's thread (a live agent stream): note it and return."""
    if kind not in KINDS:
        return
    command = str((detail or {}).get('command') or '')
    with _lock:
        _pending.setdefault(int(session_id), kind)
        if command and rules.is_git_write(command):
            _commands.setdefault(int(session_id), []).append(command)
            del _commands[int(session_id)][:-MAX_COMMANDS]
    _wake.set()


def _drain():
    with _lock:
        items = dict(_pending)
        _pending.clear()
    return items


def _drain_all():
    """Events and their git commands, taken together so none splits across rounds."""
    with _lock:
        events, commands = dict(_pending), {sid: list(c) for sid, c in _commands.items()}
        _pending.clear()
        _commands.clear()
    return events, commands


def _lineage(db, sid):
    """The chat and its sub-agent ancestors (a child's commit concerns its orchestrator)."""
    chain, current = [], int(sid)
    while current is not None and current not in chain and len(chain) <= MAX_ANCESTORS:
        chain.append(current)
        row = db.get_chat_session_by_id(current) or {}
        parent = row.get('parent_session_id')
        current = int(parent) if parent not in (None, '') else None
    return chain


def run_checks(events, *, db=None, request=None, commands=None):
    """Guard-check schedules watching the chats in ``events`` ({session_id: kind}).

    ``commands`` ({session_id: [git write commands]}) are attributed to every
    watched ancestor, so a sub-agent's ``git merge`` counts against its orchestrator."""
    if not events:
        return 0
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    trigger_for, commands_for = {}, {}
    for sid, kind in events.items():
        for watched in _lineage(db, sid):
            trigger_for.setdefault(watched, f'{kind} in CH-{sid:06d}')
            for command in (commands or {}).get(sid, []):
                commands_for.setdefault(watched, []).append((sid, command))
    woke = 0
    for op in store.supervising(trigger_for):
        if not rules.has_guards(op['payload']):
            continue
        try:
            woke += bool(worker.guard_check(op, db, request, trigger=trigger_for.get(op['target'], ''),
                                            commands=commands_for.get(op['target'], [])))
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
            events, commands = _drain_all()
            run_checks(events, commands=commands)
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
