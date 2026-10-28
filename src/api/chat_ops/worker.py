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


def dispatch(op, *, request=None):
    """One outbox attempt. An ambiguous send is terminal, never auto-replayed."""
    request = request or transport.request
    db, actor, row = service.context(op['target'], op['actor'])
    uid, sid = row['user_id'], row['id']
    live = request(uid, sid, 'GET', '/api/chat-live-status')
    if live.get('generating') and op['kind'] != 'supervise':
        return  # Still pending; no busy-loop or second harness.
    if not store.claim(op['id']):
        return
    try:
        if op['kind'] == 'supervise':
            _check_in(op, db, actor, sid, request)
            return
        text = op['payload']['text']
        # The host turn outlives this connection (chat-pending-result), so read
        # only until the host accepts or rejects it; a long silent tool run must
        # not time out a delivery that already started.
        with request(uid, sid, 'POST', '/api/chat', {'message': text, 'stream': True}, stream=True) as response:
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
            store.finish(op['id'], 'unknown', {'error': 'Stream ended before the host accepted the turn; inspect chat before resending'})
    except Exception:
        # Do not persist request exceptions, which can contain bearer tokens.
        if op['kind'] == 'supervise':
            store.finish(op['id'], 'pending', {'error': 'Check-in failed; retrying next interval'},
                         due=time.time() + op['payload']['every'])
        else:
            store.finish(op['id'], 'unknown', {'error': 'Delivery uncertain; inspect chat before resending'})


def _signature(snap, violations):
    latest = snap.get('latest_reply') or {}
    return json.dumps([snap['state'], latest.get('id'), sorted(violations),
                       [(t['id'], t['done'], t['total']) for t in snap['tasks']]])


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
    # A working target or an unchanged picture costs no supervisor tokens.
    if not violations and (snap['state'] == 'busy' or signature == previous.get('signature')):
        store.finish(op['id'], 'pending', {**previous, 'last_check': time.time(),
                                            'skipped': snap['state']}, due=nxt)
        return
    active = [o for o in store.listing(actor) if o['kind'] == 'checkin'
              and o['payload'].get('schedule_id') == op['id'] and o['state'] in ('pending', 'dispatching')]
    if not active:
        compact = {k: snap[k] for k in ('handle', 'state', 'branch', 'execution_path', 'warnings')}
        compact['violations'] = violations
        compact['tasks'] = [{k: t[k] for k in ('id', 'title', 'done', 'total')} for t in snap['tasks']]
        compact['latest_reply'] = (snap['latest_reply'] or {}).get('content', '')[:4000]
        compact['live'] = {k: snap['live'].get(k) for k in ('status', 'query_id', 'report_url')}
        text = ('Scheduled supervision check-in for ' + snap['handle'] + f" (state: {snap['state']}).\n"
                + cfg['instructions']
                + '\nAct with `python -m api.chat_cli send ' + snap['handle'] + ' --text …` (steers a live turn,'
                ' otherwise queues). Address this target chat only; do not message its children.\n'
                + 'Model guidance: ' + json.dumps(cfg['model_tiers'])
                + '\nTarget chat data (context for your assessment):\n' + json.dumps(compact, default=str))
        store.create('checkin', actor, actor, {'text': text, 'schedule_id': op['id']})
    store.finish(op['id'], 'pending', {'signature': signature, 'last_check': time.time(),
                                        'last_checkin': time.time()}, due=nxt)


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
                store.finish(item['id'], 'pending', {'error': 'Host unavailable'}, due=time.time() + 15)
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
