"""Existing-chat operations; execution remains behind the ordinary chat ingress."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from api.chat_ops import store, transport


def session_id(value):
    from api.cuttle_ui_capabilities import parse_chat_handle
    parsed = parse_chat_handle(str(value or ''))
    if not parsed or parsed.get('message_index') is not None:
        raise ValueError('Pass a session handle CH-… (not a bubble reference)')
    return int(parsed['session_id'])


def context(target, actor=None, *, db=None):
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    actor = session_id(actor or os.environ.get('CUTTLE_CHAT_SESSION_ID'))
    target = session_id(target)
    source = db.get_chat_session_by_id(actor)
    row = db.get_chat_session_by_id(target)
    if not source or not source.get('is_active') or not row or not row.get('is_active'):
        raise ValueError('Acting or target chat not found/active')
    if source['user_id'] != row['user_id']:
        raise ValueError('Acting and target chats must have the same owner')
    return db, actor, row


def _latest(db, sid):
    # A bounded transcript tail is enough for the newest reply; report absence
    # rather than loading an entire long chat.
    rows = db.get_messages(sid, limit=40)
    return next((r for r in reversed(rows) if r.get('role') == 'assistant'), None)


def status(target, actor=None, *, db=None, request=None):
    from api.chat_attention import is_error
    from api.gizmos.tasks import list_tasks
    db, actor, row = context(target, actor, db=db)
    sid, uid = int(row['id']), int(row['user_id'])
    request = request or transport.request
    live = request(uid, sid, 'GET', '/api/chat-live-status')
    tasks = list_tasks(user_id=uid, session_id=sid, db=db)
    def counts(items):
        done = total = 0
        for item in items:
            total += 1
            done += bool(item.get('done'))
            a, b = counts(item.get('children') or [])
            done += a
            total += b
        return done, total
    progress = []
    for task in tasks:
        done, total = counts(task['payload']['items'])
        progress.append({'id': task['id'], 'title': task['title'], 'done': done, 'total': total,
                         'items': task['payload']['items']})
    attention = db.get_attention(sid, uid) or {}
    latest = _latest(db, sid)
    if latest:
        latest = dict(latest)
        content = latest.get('content') or ''
        latest['content'] = content[:6000]
        latest['content_truncated'] = len(content) > 6000
        meta = latest.get('metadata') or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        latest['metadata'] = {k: meta[k] for k in ('query_id', 'report_url', 'slash_command_failed', 'failed', 'is_error', 'usage') if k in meta}
    if live.get('generating'):
        state = 'busy'
    elif live.get('cancelled'):
        state = 'cancelled'
    elif attention.get('awaitingInput'):
        state = 'awaiting_input'
    elif latest and is_error(latest.get('content'), latest.get('metadata')):
        state = 'error'
    elif any(t['done'] < t['total'] for t in progress):
        state = 'idle_with_work'
    else:
        state = 'idle'
    return {'schema_version': 1, 'ok': True, 'handle': f'CH-{sid:06d}', 'state': state,
            'live': live, 'attention': attention, 'tasks': progress,
            'followups': db.get_followup_state(sid, uid),
            'operations': [o for o in store.listing(actor) if o['target'] == sid and o['state'] in ('pending', 'dispatching')],
            'latest_reply': latest}


def snapshot(target, actor=None, *, db=None, request=None, usage_agents=()):
    from api.worktrees.chat_binding import get_chat_binding
    from api.jobs.ledger import unified_jobs
    db, actor, row = context(target, actor, db=db)
    result = status(target, actor, db=db, request=request)
    warnings = []
    binding = get_chat_binding(row['id'], db=db)
    checkout = binding.get('checkout')
    path = (checkout or {}).get('path') or row.get('project_path') or ''
    branch = None
    try:
        if path:
            branch = subprocess.run(['git', '-C', path, 'symbolic-ref', '--short', 'HEAD'],
                capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        warnings.append('Cannot resolve a live branch (missing checkout or detached HEAD)')
    try:
        jobs = [j for j in unified_jobs(limit=200)
                if str((j.get('origin') or {}).get('chat_session_id') or '').removeprefix('db_session_') == str(row['id'])]
    except Exception:
        jobs = []
        warnings.append('Jobs snapshot unavailable')
    # Only watch ids present in this chat's reply are read; never scan all jobs.
    from api.job_watch import read_status_reconciled
    watches = {}
    for match in re.finditer(r'<cuttle_action_form\b[^>]*>(.*?)</cuttle_action_form>',
                             (result['latest_reply'] or {}).get('content') or '', re.S):
        try:
            spec = json.loads(match[1])
            watch = spec.get('watch') or {}
            url = watch.get('url') or watch.get('status_url') or ''
            jid = re.fullmatch(r'/output/([A-Za-z0-9_-]+)-status\.json', url)
            if jid:
                watches[jid[1]] = read_status_reconciled(jid[1])
        except (ValueError, TypeError, AttributeError):
            continue
    from api.gizmos.usage import snapshot as usage_snapshot
    usage = {agent: usage_snapshot(agent) for agent in usage_agents}
    result.update(worktree=binding, execution_path=path, branch=branch,
                  jobs=jobs, watches=watches, usage=usage, warnings=warnings)
    return result


def send(target, text, actor=None, *, mode='auto', db=None, request=None):
    db, actor, row = context(target, actor, db=db)
    text = str(text or '').strip()
    if not text or len(text) > 20000:
        raise ValueError('Message must contain 1–20000 characters')
    if actor == int(row['id']):
        raise ValueError('Send to a different chat to avoid self-steering loops')
    if mode not in ('auto', 'queue'):
        raise ValueError('mode must be auto or queue')
    request = request or transport.request
    if mode == 'auto':
        live = request(row['user_id'], row['id'], 'GET', '/api/chat-live-status')
        if live.get('generating'):
            steered = request(row['user_id'], row['id'], 'POST', '/api/chat-steer', {'message': text})
            if steered.get('steered'):
                return {'schema_version': 1, 'ok': True, 'delivery': 'steered', 'handle': f"CH-{row['id']:06d}"}
    op = store.create('send', actor, row['id'], {'text': text})
    return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': op,
            'hint': 'Host-owned outbox dispatches when idle; no open browser required'}


def supervise(target, actor=None, *, every=900, instructions='', required_branch='',
              required_path='', forbidden_branches=(), model_tiers=None, db=None):
    db, actor, row = context(target, actor, db=db)
    if actor == int(row['id']):
        raise ValueError('Supervisor and target must be different chats')
    if not 60 <= every <= 86400:
        raise ValueError('Check-in interval must be 60–86400 seconds')
    if model_tiers is not None and not isinstance(model_tiers, dict):
        raise ValueError('model_tiers must be a JSON object')
    if not instructions.strip():
        raise ValueError('Give the supervisor explicit instructions')
    payload = {'every': every, 'instructions': instructions, 'required_branch': required_branch,
               'required_path': str(Path(required_path).resolve()) if required_path else '',
               'forbidden_branches': list(forbidden_branches), 'model_tiers': model_tiers or {}}
    return {'schema_version': 1, 'ok': True, 'operation': store.create('supervise', actor, row['id'], payload, due=time.time() + every)}


def stop(target, actor=None, *, db=None, request=None):
    db, actor, row = context(target, actor, db=db)
    return (request or transport.request)(row['user_id'], row['id'], 'POST', '/api/chat-cancel')
