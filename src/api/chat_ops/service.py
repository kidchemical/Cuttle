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
        latest['text'] = readable_reply(content)[:6000]
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
            'operations': [o for o in store.listing(actor) if o['target'] == sid and o['state'] in ('pending', 'dispatching', 'verifying')],
            'latest_reply': latest}


_WRAPPED = re.compile(r'^\s*<cuttle_original_response>(.*?)</cuttle_original_response>\s*', re.S)
_THINK = re.compile(r'<think(?:ing)?>.*?</think(?:ing)?>\s*', re.S | re.I)
_FORM = re.compile(r'<cuttle_action_form\b[^>]*>(.*?)</cuttle_action_form>', re.S)


def readable_reply(content):
    """A stored reply as a person reads it: the agent's own answer (unwrapped from
    post-processing), no reasoning blocks, and question cards as one line."""
    text = str(content or '')
    wrapped = _WRAPPED.match(text)
    if wrapped:
        try:
            original = json.loads(wrapped.group(1))
            text = original if isinstance(original, str) else text[wrapped.end():]
        except ValueError:
            text = text[wrapped.end():]

    def form(match):
        try:
            spec = json.loads(match.group(1))
        except ValueError:
            return '[card]'
        label = spec.get('title') or ('watch' if spec.get('watch') else spec.get('mode') or 'question')
        return f'[card: {label}]'
    return _FORM.sub(form, _THINK.sub('', text)).strip()


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
            # Recorded first (not dispatchable) so the steered row renders as Cuttle.
            op = store.create('send', actor, row['id'], {'text': text}, state='steering')
            try:
                steered = request(row['user_id'], row['id'], 'POST', '/api/chat-steer',
                                  {'message': text, 'chat_op_id': op['id']})
            except Exception:
                store.finish(op['id'], 'verifying', {'attempts': 1, 'uncertain': 'steer request failed'},
                             due=time.time() + 30)
                return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': store.get(op['id'], actor),
                        'hint': 'Steer outcome unknown; the host checks the chat before resending'}
            if steered.get('steered'):
                store.finish(op['id'], 'delivered', {'steered': True})
                return {'schema_version': 1, 'ok': True, 'delivery': 'steered', 'handle': f"CH-{row['id']:06d}"}
            store.finish(op['id'], 'pending', {})
            return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': store.get(op['id'], actor),
                    'hint': 'Host-owned outbox dispatches when idle; no open browser required'}
    op = store.create('send', actor, row['id'], {'text': text})
    return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': op,
            'hint': 'Host-owned outbox dispatches when idle; no open browser required'}


def pin_to_chat_agent(db, row, text):
    """Run a sent turn on the target chat's own agent, not the agent router.

    A plain message carries no composer badge, so ``/api/chat`` would route a
    chat whose agent lives only in its badge selection or sub-agent spec.
    A badge the user removed (``stickyCleared``) stays unpinned on purpose."""
    if text.startswith('/'):
        return text
    selection = db.get_composer_selection(int(row['id']), int(row['user_id'])) or {}
    chips = selection.get('stickyChips') or []
    if chips:
        return chips[0]['prefix'] + text
    if selection.get('stickyCleared'):
        return text
    from api.subagents.store import get_child_by_session
    child = get_child_by_session(db, int(row['id']))
    agent = str(getattr(child, 'agent', '') or '').strip().lstrip('/')
    return f'/{agent} {text}' if agent else text


def _agent_slash(agent, project_path):
    """Validated harness slash word (``cursor``) for check-in turns, or ''."""
    if not agent:
        return ''
    from api.agent_harness.catalog import get_agent
    found = get_agent(str(agent), project_path or None)
    if not found:
        raise ValueError(f'Unknown agent {agent!r}')
    return found[0].slash_prefix().strip().lstrip('/')


def supervise(target, actor=None, *, every=900, instructions='', required_branch='',
              required_path='', forbidden_branches=(), model_tiers=None, agent='',
              start_now=False, db=None):
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
               'forbidden_branches': list(forbidden_branches), 'model_tiers': model_tiers or {},
               'agent': _agent_slash(agent, row.get('project_path')), 'brief_on_start': bool(start_now)}
    due = time.time() + (0 if start_now else every)
    return {'schema_version': 1, 'ok': True, 'operation': store.create('supervise', actor, row['id'], payload, due=due)}


def start_supervisor(target, *, user_id, agent, every=900, instructions='', required_branch='',
                     required_path='', forbidden_branches=(), model_tiers=None, db=None):
    """Create a fresh supervisor chat for ``target`` and brief it right away."""
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    tid = session_id(target)
    row = db.get_chat_session(tid, int(user_id))
    if not row or not row.get('is_active'):
        raise ValueError('Target chat not found')
    _agent_slash(agent, row.get('project_path'))
    if not str(instructions or '').strip():
        raise ValueError('Give the supervisor explicit instructions')
    sup = db.create_chat_session(int(user_id), f'Supervisor · CH-{tid:06d}')
    try:
        # Same project so the supervisor sees its rules, but never the worker's checkout.
        if row.get('project_path'):
            db.set_session_project(sup, int(user_id), project_id=row.get('project_id'),
                                   project_name=row.get('project_name'), project_path=row.get('project_path'))
        return supervise(tid, sup, every=every, instructions=instructions, required_branch=required_branch,
                         required_path=required_path, forbidden_branches=forbidden_branches,
                         model_tiers=model_tiers, agent=agent, start_now=True, db=db)
    except Exception:
        db.delete_chat_session(sup, int(user_id))
        raise


def supervise_defaults(session, user_id, *, db=None):
    from api.auth_db import get_auth_db
    from api.worktrees.chat_binding import get_chat_binding
    db = db or get_auth_db()
    sid = session_id(session)
    row = db.get_chat_session(sid, int(user_id))
    if not row:
        raise LookupError('Chat not found')
    checkout = (get_chat_binding(sid, db=db) or {}).get('checkout') or {}
    path = checkout.get('path') or row.get('project_path') or ''
    branch = ''
    if path:
        try:
            branch = subprocess.run(['git', '-C', path, 'symbolic-ref', '--short', 'HEAD'], capture_output=True,
                                    text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return {'handle': f'CH-{sid:06d}', 'branch': branch, 'execution_path': path,
            'worktree': bool(checkout), 'project_path': row.get('project_path') or ''}


def set_paused(op_id, user_id, paused, *, db=None):
    op = owned_operation(op_id, user_id, db=db)
    if op['kind'] != 'supervise':
        raise ValueError('Only supervision schedules can be paused')
    return store.set_paused(op['id'], op['actor'], paused)


def cancel(op_id, user_id, *, db=None):
    op = owned_operation(op_id, user_id, db=db)
    return store.cancel(op['id'], op['actor'])


def owned_operation(op_id, user_id, *, db=None):
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    op = store.find(str(op_id or ''))
    if not op or not db.get_chat_session(int(op['actor']), int(user_id)):
        raise LookupError('Operation not found')
    return op


def overview(session, user_id, *, db=None):
    """What the UI shows for one chat: schedules it runs, schedules on it, queued sends."""
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    sid = session_id(session)
    if not db.get_chat_session(sid, int(user_id)):
        raise LookupError('Chat not found')
    supervising, supervised_by, queued = [], [], []
    for op in store.involving(sid):
        if op['kind'] == 'supervise':
            item = _schedule_view(op)
            (supervising if op['actor'] == sid else supervised_by).append(item)
        elif op['kind'] == 'send' and op['target'] == sid:
            queued.append({'id': op['id'], 'from': f"CH-{op['actor']:06d}", 'state': op['state'],
                           'text': op['payload'].get('text', '')[:500], 'queued_at': op['updated']})
    return {'schema_version': 1, 'ok': True, 'handle': f'CH-{sid:06d}', 'supervising': supervising,
            'supervised_by': supervised_by, 'queued': queued}


def _schedule_view(op):
    cfg, result = op['payload'], op['result'] or {}
    seen = result.get('last_seen') or {}
    return {'id': op['id'], 'state': op['state'], 'supervisor': f"CH-{op['actor']:06d}",
            'target': f"CH-{op['target']:06d}", 'every': cfg.get('every'), 'next_check': op['due'],
            'agent': cfg.get('agent') or '', 'required_branch': cfg.get('required_branch') or '',
            'forbidden_branches': cfg.get('forbidden_branches') or [], 'last_check': result.get('last_check'),
            'last_seen': seen, 'violations': seen.get('violations') or [],
            'history': store.checks(op['id'], limit=20)}


def stop(target, actor=None, *, db=None, request=None):
    db, actor, row = context(target, actor, db=db)
    return (request or transport.request)(row['user_id'], row['id'], 'POST', '/api/chat-cancel')
