"""Flask-owned durable outbox pump. No independent executor or daemon lifetime."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from api.chat_ops import service, store, transport

_lock = threading.Lock()
_thread = None
_stop = threading.Event()
_active = {}


# An in-flight request may still be saving the turn when its connection drops;
# wait this long (and for the chat to go idle) before reading history.
VERIFY_GRACE_SEC = 30
MAX_SEND_ATTEMPTS = 3


def _uncertain(op, reason):
    attempts = int((op.get('result') or {}).get('attempts') or 0) + 1
    store.finish(op['id'], 'verifying', {'attempts': attempts, 'uncertain': reason},
                 due=time.time() + VERIFY_GRACE_SEC)


def _verify(op, db, sid):
    """Resolve an uncertain send from the chat's own history: the user row
    carries ``chat_op_id`` when the turn arrived. Absent means never sent."""
    result = op.get('result') or {}
    received = any(m.get('role') == 'user' and (m.get('metadata') or {}).get('chat_op_id') == op['id']
                   for m in db.get_messages(sid, limit=200))
    if received:
        store.finish(op['id'], 'delivered', {**result, 'recovered': True})
    elif int(result.get('attempts') or 0) >= MAX_SEND_ATTEMPTS:
        store.finish(op['id'], 'unknown', {**result, 'error': 'Not delivered after '
                                           f'{MAX_SEND_ATTEMPTS} attempts; check the chat before resending'})
    else:
        store.finish(op['id'], 'pending', {**result, 'resend': True})


def dispatch(op, *, request=None):
    """One outbox attempt. A send whose outcome is lost is verified against the
    target's history before any resend, so a turn never runs twice."""
    request = request or transport.request
    db, actor, row = service.context(op['target'], op['actor'])
    uid, sid = row['user_id'], row['id']
    live = request(uid, sid, 'GET', '/api/chat-live-status')
    if live.get('generating') and op['kind'] != 'supervise':
        return  # Still pending; no busy-loop or second harness.
    if not store.claim(op['id'], op.get('state') or 'pending'):
        return
    if op.get('state') == 'verifying':
        try:
            _verify(op, db, sid)
        except Exception:
            store.finish(op['id'], 'verifying', op.get('result') or {}, due=time.time() + VERIFY_GRACE_SEC)
        return
    try:
        if op['kind'] == 'supervise':
            _check_in(op, db, actor, sid, request)
            return
        text = service.pin_to_chat_agent(db, row, op['payload']['text'])
        # The host turn outlives this connection (chat-pending-result), so read
        # only until the host accepts or rejects it; a long silent tool run must
        # not time out a delivery that already started.
        body = {'message': text, 'stream': True, 'chat_op_id': op['id']}
        with request(uid, sid, 'POST', '/api/chat', body, stream=True) as response:
            if response.status_code >= 400:
                if response.status_code == 409:
                    store.finish(op['id'], 'pending', {'busy': True}, due=time.time() + 2)
                else:
                    store.finish(op['id'], 'failed', {'http_status': response.status_code})
                return
            for line in response.iter_lines():
                if not line.startswith(b'data: '):
                    continue
                event = json.loads(line[6:])
                kind = event.get('type')
                if kind == 'busy':
                    store.finish(op['id'], 'pending', {'busy': True}, due=time.time() + 2)
                    return
                if kind == 'query_started':
                    store.finish(op['id'], 'delivered', {'query_id': event.get('query_id')})
                    return
                if kind == 'response':
                    store.finish(op['id'], 'done' if event.get('success', True) else 'failed',
                                 {'query_id': event.get('query_id'), 'success': event.get('success', True)})
                    return
            _uncertain(op, 'stream ended before the host accepted the turn')
    except Exception:
        # Do not persist request exceptions, which can contain bearer tokens.
        if op['kind'] == 'supervise':
            store.record_check(op['id'], 'error', {'error': 'Check failed; retrying next interval'})
            store.finish(op['id'], 'pending', {**(op.get('result') or {}),
                                                'error': 'Check-in failed; retrying next interval'},
                         due=time.time() + op['payload']['every'])
        else:
            _uncertain(op, 'connection lost during delivery')


def _signature(snap, violations):
    latest = snap.get('latest_reply') or {}
    return json.dumps([snap['state'], latest.get('id'), sorted(violations),
                       [(t['id'], t['done'], t['total']) for t in snap['tasks']]])


_STATE_TEXT = {'busy': 'working', 'idle': 'idle, nothing open', 'idle_with_work': 'idle with open Tasks items',
               'awaiting_input': 'waiting on a question card', 'error': 'stopped on an error',
               'cancelled': 'stopped (cancelled)'}
CHECKIN_REPLY_CHARS = 2500


def checkin_text(cfg, snap, violations):
    """The check-in turn as markdown: it renders as a readable Cuttle bubble in the
    supervisor chat and reads as a brief to the agent. Full data: `status --json`."""
    handle = snap['handle']
    # A UI-made supervisor chat has no history to pin its agent; the prefix does.
    lines = [(f"/{cfg['agent']} " if cfg.get('agent') else '')
             + f"**Supervision check-in for {handle}**: {_STATE_TEXT.get(snap['state'], snap['state'])}."]
    where = [f"branch `{snap['branch']}`" if snap.get('branch') else '',
             f"checkout `{snap['execution_path']}`" if snap.get('execution_path') else '']
    if any(where):
        joined = ' · '.join(w for w in where if w)
        lines.append(joined[0].upper() + joined[1:])
    if violations:
        lines += ['', '**Rule violations:**'] + [f'- {v}' for v in violations]
    if snap['tasks']:
        lines += ['', '**Tasks:**'] + [f"- {t['title']}: {t['done']}/{t['total']} done" for t in snap['tasks']]
    status = (snap.get('live') or {}).get('status')
    if status:
        lines.append(f'Live status: {status}')
    for w in snap.get('warnings') or []:
        lines.append(f'Note: {w}')
    reply = (snap.get('latest_reply') or {})
    text = reply.get('text') or ''
    if text:
        cut = len(text) > CHECKIN_REPLY_CHARS
        lines += ['', f"**Its latest reply{' (start)' if cut else ''}:**"]
        lines += ['> ' + ln if ln.strip() else '>' for ln in text[:CHECKIN_REPLY_CHARS].rstrip().splitlines()]
        if cut:
            lines.append(f'> … (`python -m api.chat_cli status {handle} --json` has the rest)')
    lines += ['', '**Your instructions:**', cfg['instructions'].strip()]
    tiers = cfg.get('model_tiers') or {}
    if tiers:
        lines += ['', '**Model guidance:**'] + [f"- {k}: {', '.join(v)}" for k, v in tiers.items() if v]
    lines += ['', f"Act with `python -m api.chat_cli send {handle} --text \"…\"` (steers a live turn, otherwise "
              'queues). Message this chat only, not its sub-agents. End your turn when done; '
              'the next check-in comes on schedule.']
    return '\n'.join(lines)


def _check_in(op, db, actor, sid, request):
    """Wake the supervisor only when the target needs attention or changed."""
    cfg = op['payload']
    snap = service.snapshot(sid, actor, db=db, request=request)
    violations = []
    if cfg['required_branch'] and snap['branch'] != cfg['required_branch']:
        violations.append(f"branch is {snap['branch']!r}, required {cfg['required_branch']!r}")
    if snap['branch'] in cfg['forbidden_branches']:
        violations.append(f"target is on forbidden branch {snap['branch']!r}")
    if cfg['required_path'] and os.path.realpath(snap['execution_path'] or '') != cfg['required_path']:
        violations.append(f"checkout is {snap['execution_path']!r}, required {cfg['required_path']!r}")
    previous = op.get('result') or {}
    signature = _signature(snap, violations)
    nxt = time.time() + cfg['every']
    seen = {'state': snap['state'], 'branch': snap['branch'], 'violations': violations,
            'tasks': [{'title': t['title'], 'done': t['done'], 'total': t['total']} for t in snap['tasks']]}
    # The first check of a UI-started schedule always briefs the new supervisor.
    briefing = bool(cfg.get('brief_on_start')) and not previous.get('last_checkin')
    # A working target or an unchanged picture costs no supervisor tokens.
    if not violations and not briefing and (snap['state'] == 'busy' or signature == previous.get('signature')):
        reason = 'busy' if snap['state'] == 'busy' else 'unchanged'
        store.record_check(op['id'], 'skipped', {**seen, 'reason': reason})
        store.finish(op['id'], 'pending', {**previous, 'last_check': time.time(), 'last_seen': seen,
                                            'skipped': reason}, due=nxt)
        return
    active = [o for o in store.listing(actor) if o['kind'] == 'checkin'
              and o['payload'].get('schedule_id') == op['id'] and o['state'] in ('pending', 'dispatching')]
    store.record_check(op['id'], 'violation' if violations else 'woke',
                       {**seen, 'already_waiting': bool(active)})
    if not active:
        store.create('checkin', actor, actor, {'text': checkin_text(cfg, snap, violations), 'schedule_id': op['id']})
    store.finish(op['id'], 'pending', {'signature': signature, 'last_check': time.time(),
                                        'last_checkin': time.time(), 'last_seen': seen}, due=nxt)


def _background_tick():
    # A long agent response must not delay unrelated chats or check-ins.
    for oid, thread in list(_active.items()):
        if not thread.is_alive():
            _active.pop(oid, None)
    for op in store.due():
        if len(_active) >= 8:
            break
        if op['id'] in _active:
            continue
        def run(item=op):
            try:
                dispatch(item)
            except ValueError as exc:
                store.finish(item['id'], 'blocked', {'error': str(exc)})
            except Exception:
                keep = 'verifying' if item.get('state') == 'verifying' else 'pending'
                store.finish(item['id'], keep, {**(item.get('result') or {}), 'error': 'Host unavailable'},
                             due=time.time() + 15)
        thread = threading.Thread(target=run, name='chat-op-' + op['id'][:8], daemon=True)
        _active[op['id']] = thread
        thread.start()


def _loop():
    store.recover_after_restart()
    while not _stop.wait(2):
        try:
            _background_tick()
        except Exception as exc:
            print(f'[CHAT OPS] pump failed: {type(exc).__name__}', flush=True)


def ensure_started():
    if 'pytest' in sys.modules or os.environ.get('CUTTLE_TEST_MODE') == '1' or os.environ.get('CUTTLE_CHAT_OPS') == '0':
        return False
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='cuttle-chat-ops', daemon=True)
        _thread.start()
    return True
