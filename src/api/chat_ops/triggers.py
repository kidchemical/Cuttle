"""Event-driven guard checks for supervision schedules.

A finished tool call or a worktree bind publishes a session event
(``api.session_events``). This listener notes the chat, then on its own thread
re-checks the guard rules of every schedule watching that chat or one of its
sub-agent ancestors, so a child committing to a forbidden branch is caught too.
Only a new violation wakes the supervisor (``worker.guard_check``); quiet chats
cost nothing. The interval schedule stays the backstop and owns stall detection.

Checkpoints (``api.chat_ops.checkpoints``) ride the same events: a finished
tool call (its command and failed flag) can fire ``tests`` / ``repeat_failures``
/ ``watched_paths``, a Tasks ``tasks`` event (items just checked off) fires
``milestone`` when the list is one the schedule follows (``run_checkpoints``).

A finished agent turn (``turn_end``, published by ``api.chat_delivery.end``) is
the other trigger: when the supervised chat stopped on a question card
(``awaiting_input``), with open Tasks items (``idle_with_work``) or on an error,
its schedule's next check is pulled to now, so the normal check-in runs within
seconds instead of up to one interval later (``wake_on_turn_end``). A busy or
background-waiting chat never wakes it, and each schedule wakes early at most
once per ``EARLY_WAKE_DEBOUNCE_SEC``.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from api.chat_ops import rules, service, store, worker

log = logging.getLogger(__name__)

# Coalesce a burst of tool calls into one check per chat.
DEBOUNCE_SEC = 2.0
MAX_ANCESTORS = 8
KINDS = ('tool', 'worktree_bound')
TURN_END = 'turn_end'
# Target states a finished turn wakes the supervisor for (never busy or
# waiting_background: those wake by themselves or are still working).
EARLY_WAKE_STATES = ('awaiting_input', 'idle_with_work', 'error')
EARLY_WAKE_DEBOUNCE_SEC = 120.0

_lock = threading.Lock()
_pending = {}
_commands = {}
_turn_ends = set()
_early_wakes = {}  # schedule id -> time of its last early wake (this process)
_tools = []  # (session id, command, failed) of finished tool calls, for checkpoints
_task_events = []  # (session id, detail) of Tasks completions
MAX_TOOLS = 500
MAX_COMMANDS = 50
_wake = threading.Event()
_stop = threading.Event()
_thread = None


def on_session_event(kind, session_id, detail):
    """Runs on the publisher's thread (a live agent stream): note it and return."""
    if kind == 'tasks':
        with _lock:
            _task_events.append((int(session_id), dict(detail or {})))
            del _task_events[:-MAX_TOOLS]
        _wake.set()
        return
    if kind == 'tool':
        with _lock:
            _tools.append((int(session_id), str((detail or {}).get('command') or ''), bool((detail or {}).get('failed'))))
            del _tools[:-MAX_TOOLS]
    if kind == TURN_END:
        with _lock:
            _turn_ends.add(int(session_id))
        _wake.set()
        return
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


def _drain_checkpoint_events():
    with _lock:
        tools, tasks = list(_tools), list(_task_events)
        _tools.clear()
        _task_events.clear()
    return tools, tasks


def run_checkpoints(tools, task_events, *, db=None, request=None):
    """Fire the checkpoints of schedules watching the chats these events came from.

    ``tools``: [(session id, command, failed)]; ``task_events``: [(session id, detail)].
    A sub-agent's event counts for the schedule on its orchestrator, as for guards."""
    from api.chat_ops import checkpoints as cp
    if not tools and not task_events:
        return 0
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    lineage = {}
    for sid in {t[0] for t in tools} | {e[0] for e in task_events}:
        lineage[sid] = _lineage(db, sid)
    watched = {w for chain in lineage.values() for w in chain}
    fired = 0
    for op in store.supervising(watched):
        cfg = op['payload']
        points = cfg.get('checkpoints') or {}
        if not points:
            continue
        target = int(op['target'])
        mine = lambda sid: target in lineage.get(sid, ())  # noqa: E731
        result = op.get('result') or {}
        try:
            hits = []
            counts = result.get('failure_counts') or {}
            touched = False
            for sid, command, failed in tools:
                if not mine(sid):
                    continue
                touched = True
                new_hits, counts = cp.tool_hits(points, command, failed, counts)
                hits += [(cid, lines, f'tool in CH-{sid:06d}') for cid, lines in new_hits]
            if touched and 'repeat_failures' in points:
                store.patch_result(op['id'], failure_counts=counts)
            if touched and 'watched_paths' in points:
                _, row_ = service.context(target, op['actor'], db=db)[1:]
                path = service.checkout_state(row_, db)[0]
                if 'paths_base' not in result:
                    # First look: today's state is the baseline, never a violation.
                    head, files = cp.changed_paths(path, '')
                    rx = re.compile(points['watched_paths']['regex'])
                    store.patch_result(op['id'], paths_base=head, paths_reported=[f for f in files if rx.search(f)])
                else:
                    lines, head, reported = cp.watched_hits(points['watched_paths'], path, result.get('paths_base') or '',
                                                            result.get('paths_reported') or [])
                    if lines:
                        store.patch_result(op['id'], paths_reported=reported)
                        hits.append(('watched_paths', lines, 'files changed'))
            if 'milestone' in points:
                followed = None
                for sid, detail in task_events:
                    if not (mine(sid) or sid == target):
                        continue
                    if followed is None:
                        followed = {t['id'] for t in service.supervised_tasks(
                            db, int(db.get_chat_session_by_id(target)['user_id']), target,
                            *(service.task_pick(cfg) or (None, None)))}
                    if detail.get('gizmo_id') in followed:
                        lines = cp.milestone_lines(detail)
                        if lines:
                            hits.append(('milestone', lines, f"tasks {detail.get('gizmo_id')}"))
            # One firing per checkpoint kind per round; their lines merged.
            merged = {}
            for cid, lines, trigger in hits:
                merged.setdefault(cid, ([], trigger))[0].extend(lines)
            for cid, (lines, trigger) in merged.items():
                fired += bool(worker.fire_checkpoint(op, cid, list(dict.fromkeys(lines)), db=db, request=request,
                                                     trigger=trigger))
        except Exception:
            log.exception('checkpoint check failed for schedule %s', op['id'])
    return fired


def _drain_turn_ends():
    with _lock:
        ended = set(_turn_ends)
        _turn_ends.clear()
    return ended


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


def wake_on_turn_end(session_ids, *, db=None, request=None, now=None):
    """Pull the check of every active schedule on a chat whose turn just ended.

    For each live (not paused) supervision schedule whose target is one of
    ``session_ids``, read the target's state with the check-in's own logic
    (``service.status``). On ``awaiting_input`` / ``idle_with_work`` / ``error``
    the schedule's due time moves to now, so the outbox pump runs an ordinary
    check-in within seconds (and that check-in still decides whether the
    supervisor's chat gets a turn). Returns the ids of schedules pulled."""
    targets = {int(s) for s in session_ids or ()}
    if not targets:
        return []
    now = time.time() if now is None else now
    pulled = []
    for op in store.supervising(targets):
        if op['state'] != 'pending' or int(op['target']) not in targets:
            continue  # a check already running needs no early one
        with _lock:
            last = _early_wakes.get(op['id'])
        if last is not None and now - last < EARLY_WAKE_DEBOUNCE_SEC:
            continue
        try:
            state = service.status(op['target'], op['actor'], db=db, request=request,
                                   task_pick=service.task_pick(op.get('payload')))['state']
        except Exception:
            log.exception('turn-end state read failed for schedule %s', op['id'])
            continue
        if state not in EARLY_WAKE_STATES:
            continue
        if not store.pull_due(op['id'], now):
            continue  # already due, or paused/cancelled since the read
        with _lock:
            _early_wakes[op['id']] = now
        store.record_check(op['id'], 'early_wake', {'state': state, 'trigger': TURN_END})
        pulled.append(op['id'])
    return pulled


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
        try:
            run_checkpoints(*_drain_checkpoint_events())
        except Exception:
            log.exception('checkpoint pump failed')
        ended = _drain_turn_ends()
        try:
            wake_on_turn_end(ended)
        except Exception:
            log.exception('turn-end wake failed')
        try:
            # A sub-agent child's turn driven by `chat_cli send` ends its card round.
            from api.subagents.service import on_child_turns_ended
            on_child_turns_ended(ended)
        except Exception:
            log.exception('sub-agent round close failed')


def start():
    """Subscribe and start the check thread (Flask-owned, alongside the outbox pump).

    Guard checks and turn-end wakes share it."""
    global _thread
    from api.session_events import subscribe
    subscribe(on_session_event)
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='cuttle-chat-ops-guards', daemon=True)
        _thread.start()
