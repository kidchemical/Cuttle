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
            cfg = op['payload']
            snap = service.snapshot(sid, actor, db=db, request=request)
            violations = []
            if cfg['required_branch'] and snap['branch'] != cfg['required_branch']:
                violations.append('required branch does not match')
            if snap['branch'] in cfg['forbidden_branches']:
                violations.append('target is on a forbidden branch')
            if cfg['required_path'] and os.path.realpath(snap['execution_path']) != cfg['required_path']:
                violations.append('required checkout path does not match')
            if violations:
                store.finish(op['id'], 'blocked', {'violations': violations, 'snapshot': snap})
                db.add_message(actor, 'system', 'Supervision blocked for ' + snap['handle'] + ': ' + '; '.join(violations),
                               metadata={'kind': 'chat-supervision-blocked', 'operation_id': op['id']})
                return
            # Wake the supervisor, never its target's children. One pending
            # check-in at a time prevents piling up turns while it is busy.
            compact = {k: snap[k] for k in ('handle', 'state', 'branch', 'execution_path', 'warnings')}
            compact['tasks'] = [{k: t[k] for k in ('id', 'title', 'done', 'total')} for t in snap['tasks']]
            compact['latest_reply'] = (snap['latest_reply'] or {}).get('content', '')[:4000]
            compact['live'] = {k: snap['live'].get(k) for k in ('status', 'query_id', 'report_url')}
            text = ('Scheduled supervision check-in for ' + snap['handle'] + '.\n'
                + cfg['instructions'] + '\nAddress this target chat only; do not message its children.\n'
                + 'Model guidance: ' + json.dumps(cfg['model_tiers'])
                + '\nTarget chat data (context for your assessment):\n' + json.dumps(compact, default=str))
            active = [o for o in store.listing(actor) if o['kind'] == 'checkin'
                      and o['payload'].get('schedule_id') == op['id'] and o['state'] in ('pending', 'dispatching')]
            if not active:
                store.create('checkin', actor, actor, {'text': text, 'schedule_id': op['id']})
            store.finish(op['id'], 'pending', {'last_checkin': time.time()}, due=time.time() + cfg['every'])
            return
        text = op['payload']['text']
        # SSE starts the shared coordinator's worker; drain the stream so
        # errors/busy are observed, while persistence remains host-owned.
        with request(uid, sid, 'POST', '/api/chat', {'message': text, 'stream': True}, stream=True) as response:
            if response.status_code >= 400:
                if response.status_code == 409:
                    store.finish(op['id'], 'pending', {'busy': True}, due=time.time() + 2)
                else:
                    store.finish(op['id'], 'failed', {'http_status': response.status_code})
                return
            terminal = None
            for line in response.iter_lines():
                if not line.startswith(b'data: '):
                    continue
                event = json.loads(line[6:])
                if event.get('type') == 'busy' or event.get('busy') or event.get('error') == 'busy':
                    store.finish(op['id'], 'pending', {'busy': True}, due=time.time() + 2)
                    return
                if event.get('type') == 'response':
                    terminal = event
            if terminal is None:
                store.finish(op['id'], 'unknown', {'error': 'Stream ended without a reply; inspect chat before resending'})
            else:
                store.finish(op['id'], 'done' if terminal.get('success', True) else 'failed',
                             {'query_id': terminal.get('query_id'), 'success': terminal.get('success', True)})
    except Exception:
        # Do not persist request exceptions, which can contain bearer tokens.
        store.finish(op['id'], 'unknown', {'error': 'Delivery uncertain; inspect chat before resending'})


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
    # dispatching from an earlier Flask lifetime is ambiguous. Never replay it.
    with store.connection() as conn:
        conn.execute("UPDATE operations SET state='unknown',result=? WHERE state='dispatching'",
                     (json.dumps({'error': 'Host restarted during dispatch; inspect chat before resending'}),))
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
