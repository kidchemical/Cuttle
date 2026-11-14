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


def _unit_waits(sid):
    """Pending detached-job watches on the chat, shaped like background waits.

    Read from the records only (no ``systemctl``): a record exists until the
    poller resumes the chat, which is exactly "it wakes by itself"."""
    try:
        from api.job_watch import unit_watch_records
        records = unit_watch_records(sid)
    except Exception:
        return []
    return [{'job_id': r.get('id'), 'label': r.get('label') or '', 'unit': r.get('unit') or '',
             'pid': int(r.get('pid') or 0), 'started_ts': r.get('created_ts'), 'kind': 'unit_watch',
             'chat_session_id': str(sid)} for r in records]


def _detached_spawn_waits(db, sid):
    """Sub-agent spawns this chat started under a service manager (no listener):
    the turn ends while the spawn CLI runs, and its hand-back notice wakes the
    chat when the batch ends. Counted while the CLI process is alive."""
    try:
        from api.job_watch import pid_alive
        from api.subagents import store as subagent_store
        rows = subagent_store.detached_cli_pids(db, sid)
    except Exception:
        return []
    return [{'job_id': r['batch_id'], 'label': 'sub-agent spawn', 'unit': '', 'pid': r['pid'],
             'started_ts': None, 'kind': 'detached_spawn', 'chat_session_id': str(sid)}
            for r in rows if pid_alive(r['pid'])]


def supervised_tasks(db, uid, sid, task_ids=None, known_ids=None):
    """Tasks lists a check reads: every list visible to the chat, or the
    schedule's pick ``task_ids`` (missing/archived ids are skipped) plus any of
    the chat's own lists created after the pick (ids not in ``known_ids``)."""
    from api.gizmos import tasks as gizmo_tasks
    visible = gizmo_tasks.list_tasks(user_id=uid, session_id=sid, db=db)
    if task_ids is None:
        return visible
    known = set(known_ids or ()) | set(task_ids)
    out = [t for t in visible if t['id'] not in known and t.get('scope') != 'project'
           and (t.get('session_id') == sid or sid in (t.get('linked_sessions') or []))]
    for tid in task_ids:
        try:
            task = gizmo_tasks.get(str(tid), user_id=uid, db=db)
        except Exception:
            continue
        if (task.get('status') or 'active') == 'active':
            out.append(task)
    return out


def task_pick(cfg):
    """A schedule payload's Tasks pick as ``(task_ids, task_ids_known)``, or None (every list)."""
    cfg = cfg or {}
    if cfg.get('task_ids') is None:
        return None
    return list(cfg['task_ids']), list(cfg.get('task_ids_known') or ())


def task_candidates(db, uid, sid, project_path=''):
    """Active Tasks lists a supervisor could follow for chat ``sid``: the chat's
    own (session-scoped, linked, or created there) first and preselected, then
    the rest of its project's lists."""
    project = str(project_path or '').strip().replace('\\', '/').rstrip('/')
    rows = [r for r in db.list_chat_widgets(user_id=uid, status='active') if r.get('type') == 'tasks']
    out, owner_paths = [], {}

    def chat_project(owner):
        # Session-scoped lists store no project; it is the owning chat's.
        if owner not in owner_paths:
            chat = db.get_chat_session_by_id(owner) or {}
            owner_paths[owner] = str(chat.get('project_path') or '')
        return owner_paths[owner]

    for r in rows:
        owner = int(r['session_id']) if r.get('session_id') is not None else None
        own = owner == sid or sid in [int(x) for x in (r.get('linked_sessions') or [])]
        path = r.get('project_path') or (chat_project(owner) if owner is not None and not own else '')
        path = str(path).replace('\\', '/').rstrip('/')
        if not own and not (project and path == project):
            continue
        items = (r.get('payload') or {}).get('items') or []
        done, total = _count_items(items)
        out.append({'id': r['id'], 'title': r.get('title') or 'Tasks', 'scope': r.get('scope') or 'session',
                    'session_id': r.get('session_id'), 'done': done, 'total': total, 'selected': own})
    out.sort(key=lambda t: not t['selected'])
    return out


def _count_items(items):
    done = total = 0
    for item in items or []:
        total += 1
        done += bool(item.get('done'))
        a, b = _count_items(item.get('children') or [])
        done += a
        total += b
    return done, total


def undelivered(sid, *, older_than=0.0, now=None):
    """Messages from or to the chat that are still in the outbox: who, how long,
    and what they wait on. Read-only; an agent or check-in reads this to see a
    send that never arrived instead of assuming it did."""
    now = time.time() if now is None else now
    out = []
    for op in store.waiting_sends(sid, older_than=older_than, now=now):
        result = op.get('result') or {}
        out.append({'id': op['id'], 'kind': op['kind'], 'state': op['state'],
                    'from': f"CH-{int(op['actor']):06d}", 'to': f"CH-{int(op['target']):06d}",
                    'waiting_sec': int(now - op['waiting_since']),
                    'waiting_on': result.get('waiting_on') or ('verifying' if op['state'] == 'verifying' else 'queued'),
                    'text': ' '.join(str((op.get('payload') or {}).get('text') or '').split())[:160]})
    return out


def wake_line(state, schedules, *, orchestration=''):
    """What starts the chat's next turn, in one sentence (pure).

    ``schedules``: supervision schedules watching it (``store.watching``);
    ``orchestration``: ``api.orchestrator.control.state``. Two things can wake
    an idle chat, and they are easy to mix up: its own background work (a job or
    sub-agent it started hands back when it ends) and its supervisor's check."""
    if state == 'busy':
        return 'Working now.'
    if orchestration in ('paused', 'stopped'):
        return f'Orchestration {orchestration}: nothing wakes it until the user resumes it or a message arrives.'
    if state == 'waiting_background':
        return 'Resumes by itself when its background work ends.'
    live = [s for s in schedules if s.get('state') != 'paused']
    if live:
        op = min(live, key=lambda s: s.get('due') or 0)
        at = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(float(op.get('due') or time.time())))
        return (f"No background work pending, so it does not wake itself. Its supervisor CH-{int(op['actor']):06d} "
                f'checks next at {at} UTC.')
    if schedules:
        return 'Supervision is paused and no background work is pending: nothing wakes it until a message arrives.'
    return 'No background work pending and no supervisor: nothing wakes it until a message arrives.'


def status(target, actor=None, *, db=None, request=None, task_pick=None):
    """``task_pick``: a schedule's ``(task_ids, task_ids_known)`` (``task_pick()``), or None for every list."""
    from api.chat_attention import is_error
    db, actor, row = context(target, actor, db=db)
    sid, uid = int(row['id']), int(row['user_id'])
    request = request or transport.request
    live = request(uid, sid, 'GET', '/api/chat-live-status')
    tasks = supervised_tasks(db, uid, sid, *(task_pick or (None, None)))
    progress = []
    for task in tasks:
        done, total = _count_items(task['payload']['items'])
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
        if meta.get('completed_at'):
            # ``timestamp`` is the turn's start (api.chat_turn_row); this is when the reply landed.
            latest['completed'] = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(float(meta['completed_at'])))
        latest['metadata'] = {k: meta[k] for k in ('query_id', 'report_url', 'slash_command_failed', 'failed', 'is_error', 'usage', 'completed_at') if k in meta}
    if live.get('generating'):
        state = 'busy'
    elif live.get('cancelled'):
        state = 'cancelled'
    elif attention.get('awaitingInput'):
        state = 'awaiting_input'
    elif latest and is_error(latest.get('content'), latest.get('metadata')):
        state = 'error'
    else:
        from api.agent_harness.background_wait import running_on_disk
        waits = running_on_disk(sid) + _unit_waits(sid) + _detached_spawn_waits(db, sid)
        if waits:
            # The turn ended but its CLI is still waiting on background work and
            # will post a follow-up bubble by itself (agent_harness.background_wait),
            # or a detached-job watch will resume it when the job exits (job_watch).
            state = 'waiting_background'
            live = {**live, 'background_wait': waits}
        elif any(t['done'] < t['total'] for t in progress):
            state = 'idle_with_work'
        else:
            state = 'idle'
    try:
        from api.orchestrator.control import state as orchestration_state
        orchestration = orchestration_state(sid)
    except Exception:
        orchestration = ''
    return {'schema_version': 1, 'ok': True, 'handle': f'CH-{sid:06d}', 'state': state,
            'wake': wake_line(state, store.watching(sid), orchestration=orchestration),
            'live': live, 'attention': attention, 'tasks': progress,
            'followups': db.get_followup_state(sid, uid),
            'operations': [o for o in store.listing(actor) if o['target'] == sid and o['state'] in ('pending', 'dispatching', 'verifying')],
            'undelivered': undelivered(sid),
            'latest_reply': latest}


_WRAPPED = re.compile(r'^\s*<cuttle_original_response>(.*?)</cuttle_original_response>\s*', re.S)
_THINK = re.compile(r'<think(?:ing)?>.*?</think(?:ing)?>\s*', re.S | re.I)
_FORM = re.compile(r'<cuttle_action_form\b[^>]*>(.*?)</cuttle_action_form>', re.S)


def readable_reply(content):
    """A stored reply as a person reads it: the agent's own answer (unwrapped from
    post-processing), no reasoning blocks, and question cards as one line."""
    text = _THINK.sub('', str(content or ''))
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


def checkout_state(row, db, *, binding=None):
    """``(execution_path, branch)`` the chat's turns run in; branch is None when unresolvable."""
    if binding is None:
        from api.worktrees.chat_binding import get_chat_binding
        binding = get_chat_binding(row['id'], db=db)
    path = ((binding or {}).get('checkout') or {}).get('path') or row.get('project_path') or ''
    if not path:
        return '', None
    try:
        return path, subprocess.run(['git', '-C', path, 'symbolic-ref', '--short', 'HEAD'],
                                    capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return path, None


def snapshot(target, actor=None, *, db=None, request=None, usage_agents=(), task_pick=None):
    from api.worktrees.chat_binding import get_chat_binding
    from api.jobs.ledger import unified_jobs
    db, actor, row = context(target, actor, db=db)
    result = status(target, actor, db=db, request=request, task_pick=task_pick)
    warnings = []
    binding = get_chat_binding(row['id'], db=db)
    path, branch = checkout_state(row, db, binding=binding)
    if path and branch is None:
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
    try:
        fill = context_fill(db, row)
    except Exception:
        fill = None
        warnings.append('Context fill unavailable')
    result.update(worktree=binding, execution_path=path, branch=branch,
                  jobs=jobs, watches=watches, usage=usage, warnings=warnings, context=fill)
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
    from api.orchestration_telemetry import record
    record.supervisor_action('sup_send', actor, int(row['id']), chars=len(text))
    request = request or transport.request
    # Read before the card round below marks a sub-agent child as working: that
    # mark makes live status say "generating" with no turn behind it yet.
    live = request(row['user_id'], row['id'], 'GET', '/api/chat-live-status') if mode == 'auto' else {}
    try:
        # A follow-up to a sub-agent child gets its card round on the parent,
        # exactly like `subagents message` (bound to the sending turn).
        from api.subagents.service import note_external_round
        note_external_round(int(row['id']), db=db)
    except Exception as exc:
        print(f"[CHAT OPS] sub-agent card round for CH-{int(row['id']):06d} failed: {exc}", flush=True)
    if mode == 'auto':
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
            # The turn is live but took nothing: the send waits for that turn to end,
            # however long that is. Say so, so an urgent send is not mistaken for delivered.
            return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': store.get(op['id'], actor),
                    'target_busy': True, 'steer_reason': str(steered.get('reason') or 'rejected'),
                    'hint': ('Target turn is live but not steerable; this waits until that turn ends, '
                             'however long. Let it: stopping the target throws away its running work. '
                             'Only when the user asked to pause or stop it, run `chat_cli stop` '
                             '(which also drops queued sends).')}
    op = store.create('send', actor, row['id'], {'text': text})
    return {'schema_version': 1, 'ok': True, 'delivery': 'queued', 'operation': op,
            'hint': 'Host-owned outbox dispatches when idle; no open browser required'}


def _latest_turn_prefix(db, sid):
    from api.starred_slash import sticky_prefix_from_text
    for msg in reversed(db.get_messages(sid, limit=40) or []):
        if isinstance(msg, dict) and msg.get('role') == 'user':
            found = sticky_prefix_from_text(str(msg.get('content') or ''))
            if found:
                return found.strip()
    return ''


def pin_to_chat_agent(db, row, text):
    """Run a sent turn on the target chat's own agent, not the agent router.

    A plain message carries no composer badge, so ``/api/chat`` would route a
    chat whose agent lives only in its badge selection or sub-agent spec.
    A badge the user removed (``stickyCleared``) stays unpinned on purpose.
    The chat's latest explicit agent turn beats a saved badge: a send that
    switched harness (``/claude …``) never updates the composer selection."""
    if text.startswith('/'):
        return text
    selection = db.get_composer_selection(int(row['id']), int(row['user_id'])) or {}
    if selection.get('stickyCleared') and not selection.get('stickyChips'):
        return text
    latest = _latest_turn_prefix(db, int(row['id']))
    if latest:
        return f'{latest} {text}'
    chips = selection.get('stickyChips') or []
    if chips:
        return chips[0]['prefix'] + text
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
              start_now=False, forbidden_paths='', on_violation=None, task_ids=None, checkpoints=None,
              involve_user='', nudge='', db=None):
    """``instructions`` is the optional Focus note; the brief comes from the settings (``brief``)."""
    from api.chat_ops import brief
    db, actor, row = context(target, actor, db=db)
    if actor == int(row['id']):
        raise ValueError('Supervisor and target must be different chats')
    if not 60 <= every <= 86400:
        raise ValueError('Check-in interval must be 60–86400 seconds')
    if model_tiers is not None and not isinstance(model_tiers, dict):
        raise ValueError('model_tiers must be a JSON object')
    instructions = brief.focus(instructions)
    if forbidden_paths:
        try:
            re.compile(forbidden_paths)
        except re.error as exc:
            raise ValueError(f'forbidden_paths is not a valid regex: {exc}') from None
    payload = {'every': every, 'instructions': instructions, 'required_branch': required_branch,
               'required_path': str(Path(required_path).resolve()) if required_path else '',
               'forbidden_branches': list(forbidden_branches), 'model_tiers': model_tiers or {},
               'agent': _agent_slash(agent, row.get('project_path')), 'brief_on_start': bool(start_now),
               'forbidden_paths': forbidden_paths or '', 'involve_user': brief.involve(involve_user),
               'nudge': brief.nudge(nudge)}
    if task_ids is not None:
        # An explicit pick (possibly empty: follow no Tasks list); absent = every list the chat sees.
        if not isinstance(task_ids, (list, tuple)) or len(task_ids) > 50:
            raise ValueError('task_ids must be a list of at most 50 Tasks ids')
        payload['task_ids'] = list(dict.fromkeys(str(t).strip() for t in task_ids if str(t).strip()))
        # Lists offered at pick time; the chat's own lists made later are still followed.
        payload['task_ids_known'] = [t['id'] for t in task_candidates(db, int(row['user_id']), int(row['id']),
                                                                      row.get('project_path') or '')]
    if on_violation:
        from api.chat_ops.worker import parse_on_violation
        payload['on_violation'] = parse_on_violation(on_violation)
    if checkpoints:
        from api.chat_ops.checkpoints import parse_checkpoints
        payload['checkpoints'] = parse_checkpoints(checkpoints)
    if forbidden_branches:
        from api.chat_ops.rules import branch_tips
        # Baseline for "a commit landed on a forbidden branch", from any checkout of the repo.
        payload['forbidden_tips'] = branch_tips(checkout_state(row, db)[0], list(forbidden_branches))
    due = time.time() + (0 if start_now else every)
    return {'schema_version': 1, 'ok': True, 'operation': store.create('supervise', actor, row['id'], payload, due=due),
            'warnings': brief.focus_warnings(instructions)}


def _pin_supervisor_chat(db, sup, user_id, agent, model, effort, project_path):
    """Pin the new supervisor chat's harness/model/effort and seed its composer chip.

    Check-in rows are Cuttle-authored (``speaker_kind: parent``), so they never
    set the composer agent; without this seed the chat opens with no badge."""
    from api.subagents.identity import child_slash_command, pin_child_session
    from api.subagents.types import ChildSpec
    spec = ChildSpec(title='', message='', agent=agent, model=model, effort=effort)
    pin_child_session(sup, spec, project_path=project_path or '')
    chip = (child_slash_command(spec, session_id=sup).get('chips') or [{}])[0]
    db.set_composer_selection(sup, int(user_id), {'stickyChips': [{
        'prefix': f'/{agent}', 'label': chip.get('label') or agent, 'category': agent}]})


def start_supervisor(target, *, user_id, agent, every=900, instructions='', required_branch='',
                     required_path='', forbidden_branches=(), model_tiers=None, model='', effort='',
                     task_ids=None, checkpoints=None, involve_user='', nudge='', db=None):
    """Create a fresh supervisor chat for ``target`` and brief it right away."""
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    tid = session_id(target)
    row = db.get_chat_session(tid, int(user_id))
    if not row or not row.get('is_active'):
        raise ValueError('Target chat not found')
    slash = _agent_slash(agent, row.get('project_path'))
    if not slash:
        raise ValueError('Pick an agent for the supervisor')
    model, effort = str(model or '').strip(), str(effort or '').strip().lower()
    if model or effort:
        from api.agent_harness.agent_models import check_agent_pin
        problem = check_agent_pin(slash, model, effort)
        if problem:
            raise ValueError(problem)
    from api.chat_ops import brief
    brief.focus(instructions), brief.involve(involve_user), brief.nudge(nudge)  # validate before creating a chat
    sup = db.create_chat_session(int(user_id), f'Supervisor · CH-{tid:06d}')
    try:
        # Same project so the supervisor sees its rules, but never the worker's checkout.
        if row.get('project_path'):
            db.set_session_project(sup, int(user_id), project_id=row.get('project_id'),
                                   project_name=row.get('project_name'), project_path=row.get('project_path'))
        _pin_supervisor_chat(db, sup, user_id, slash, model, effort, row.get('project_path'))
        out = supervise(tid, sup, every=every, instructions=instructions, required_branch=required_branch,
                        required_path=required_path, forbidden_branches=forbidden_branches,
                        model_tiers=model_tiers, agent=agent, start_now=True, task_ids=task_ids,
                        checkpoints=checkpoints, involve_user=involve_user, nudge=nudge, db=db)
        store.patch_payload(out['operation']['id'], model=model, effort=effort)
        out['operation'] = store.find(out['operation']['id'])
        return out
    except Exception:
        db.delete_chat_session(sup, int(user_id))
        raise


# Schedule fields an outside source (an orchestrator playbook) may own.
SYNCED_FIELDS = ('every', 'agent', 'instructions', 'required_branch', 'required_path',
                 'forbidden_branches', 'model_tiers', 'checkpoints', 'involve_user', 'nudge')
_FIELD_LABELS = {'every': 'check-in interval', 'agent': 'check-in agent', 'instructions': 'Focus',
                 'involve_user': 'when to involve the user', 'nudge': 'how idle chats are nudged',
                 'required_branch': 'required branch', 'required_path': 'required checkout',
                 'forbidden_branches': 'forbidden branches', 'model_tiers': 'model guidance',
                 'model': 'check-in model', 'effort': 'check-in effort', 'task_ids': 'Tasks lists',
                 'checkpoints': 'wake checkpoints'}


def _schedule_fields(cfg, project_path):
    """``cfg`` in the stored shape ``supervise`` writes (resolved path, slash agent)."""
    out = {k: cfg.get(k) for k in SYNCED_FIELDS if k in cfg}
    if out.get('required_path'):
        out['required_path'] = str(Path(out['required_path']).resolve())
    if 'agent' in out:
        try:
            out['agent'] = _agent_slash(out['agent'], project_path)
        except ValueError:
            out.pop('agent')  # an unknown agent never replaces a working one
    if 'every' in out and not 60 <= int(out['every'] or 0) <= 86400:
        out.pop('every')
    return out


def resync_supervision(target, previous, current, *, note_lines=(), source='the playbook', db=None):
    """Carry an edit of the settings a supervision schedule came from into it.

    ``previous``/``current`` are the source's settings before and after the edit
    (``SYNCED_FIELDS`` keys). A field still holding the previous value follows
    the edit; a field someone changed on the schedule itself is left alone and
    reported. Each live schedule on ``target`` (paused included) then queues one
    check-in telling its supervisor what changed, so it can relay the change into
    a turn already running. Returns one summary per schedule.
    """
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    sid = session_id(target)
    row = db.get_chat_session_by_id(sid)
    if not row:
        return []
    project = row.get('project_path')
    old, new = _schedule_fields(previous, project), _schedule_fields(current, project)
    handle = f'CH-{sid:06d}'
    out = []
    for op in store.involving(sid):
        if op['kind'] != 'supervise' or int(op['target']) != sid:
            continue
        cfg = op['payload']
        updated, kept = {}, []
        for key in SYNCED_FIELDS:
            if key not in new:
                continue
            was = old.get(key, cfg.get(key))
            if _same_setting(new[key], was):
                continue
            if _same_setting(cfg.get(key), was):
                updated[key] = new[key]
            elif not _same_setting(cfg.get(key), new[key]):
                kept.append(key)
        if updated:
            store.patch_payload(op['id'], **updated)
            if 'checkpoints' in updated:
                store.drop_result_keys(op['id'], 'paths_base', 'paths_reported', 'failure_counts')
            from api.orchestration_telemetry import record
            record.supervision_schedule('edit', store.find(op['id']), fields=sorted(updated), source='playbook')
        # Nothing of this schedule changed: the target reads the new brief at its
        # next turn by itself, so only a turn already running is worth a wake.
        notice = 'none'
        if updated or (op['state'] != 'paused' and _target_busy(row)):
            notice = record_schedule_change(op, updated, kept, note_lines, source=source, db=db)
        out.append({'operation': op['id'], 'supervisor': f"CH-{int(op['actor']):06d}",
                    'updated': sorted(updated), 'kept': kept, 'notice': notice})
    return out


def _same_setting(a, b):
    """Equal settings; every blank (None, '', {}, []) is the same "not set"."""
    return a == b or (not a and not b and not isinstance(a, (int, float)) and not isinstance(b, (int, float)))


def _target_busy(row):
    """Whether the target is mid-turn; an unreadable status counts as busy."""
    try:
        live = transport.request(int(row['user_id']), int(row['id']), 'GET', '/api/chat-live-status')
        return bool(live.get('generating'))
    except Exception:
        return True


def record_schedule_change(op, updated, kept, note_lines, *, source, db=None):
    """Record an edit of a schedule's settings without spending a turn on it.

    The user sees one timeline notice in the supervisor chat (a fact, not a
    message). The supervisor reads the change in its next check-in
    (``changes_section``): the pending record is folded into it, and a live
    schedule checks within ``CHANGE_WAKE_SEC`` so the change reaches it soon; a
    paused one keeps the record until it resumes. ``source``: who edited ("you",
    "playbook Default"). Returns ``'pending'`` or ``'held'`` (paused)."""
    now = time.time()
    store.merge_pending_changes(op['id'], updated=sorted(updated), kept=list(kept),
                                notes=[n for n in note_lines if n], source=source, at=now)
    if updated:
        _timeline_notice(op, updated, source, db=db)
    if op['state'] == 'paused':
        return 'held'
    store.pull_due(op['id'], now + CHANGE_WAKE_SEC)
    return 'pending'


# A burst of saves (edit, fix a typo, save again) reaches the supervisor once.
CHANGE_WAKE_SEC = 20


def _timeline_notice(op, updated, source, *, db=None):
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    fields = ', '.join(_FIELD_LABELS.get(k, k) for k in sorted(updated))
    label = f"Supervision of CH-{int(op['target']):06d} changed"
    detail = f'{fields} · by {source}'
    try:
        db.add_message(int(op['actor']), 'system', f'{label} · {detail}', metadata={
            'kind': 'supervision-change', 'schedule_id': op['id'],
            'timeline_notice': {'type': 'supervision', 'label': label, 'detail': detail, 'state': 'done'}})
    except Exception:
        pass  # the record still reaches the supervisor; the notice is only the user's view


def changes_section(cfg, handle):
    """Check-in lines naming the schedule edits since the last check-in ([] when none)."""
    pending = cfg.get('pending_changes') or {}
    updated, kept, notes = pending.get('updated') or [], pending.get('kept') or [], pending.get('notes') or []
    if not (updated or notes):
        return []
    by = ', '.join(pending.get('sources') or []) or 'the user'
    lines = ['', f'**Settings changed since your last check-in** (by {by}):']
    lines += [f'- {n}' for n in notes]
    if updated:
        lines.append('- Your schedule now uses the new ' + ', '.join(_FIELD_LABELS.get(k, k) for k in updated)
                     + ' (below).')
    if kept:
        lines.append('- Kept your own ' + ', '.join(_FIELD_LABELS.get(k, k) for k in kept)
                     + ' (set on this schedule, not by the source).')
    lines.append(f'- If {handle} is mid-turn on work the change affects, steer it now; otherwise it needs '
                 'no message about the change.')
    return lines


EDITABLE_FIELDS = ('every', 'agent', 'model', 'effort', 'instructions', 'required_branch', 'required_path',
                   'forbidden_branches', 'model_tiers', 'task_ids', 'checkpoints', 'involve_user', 'nudge')


def update_supervision(op_id, user_id, changes, *, db=None):
    """Edit a live (or paused) schedule in place; only fields present in ``changes`` move.

    Validates like ``supervise``/``start_supervisor``, re-pins the supervisor chat
    when its agent/model/effort change, re-baselines forbidden-branch tips, and
    queues one "Supervision update" check-in naming what changed. A paused
    schedule stays paused."""
    from api.auth_db import get_auth_db
    db = db or get_auth_db()
    op = owned_operation(op_id, user_id, db=db)
    if op['kind'] != 'supervise' or op['state'] not in ('pending', 'dispatching', 'verifying', 'paused'):
        raise ValueError('Only a live supervision schedule can be edited')
    changes = {k: v for k, v in (changes or {}).items() if k in EDITABLE_FIELDS}
    cfg = op['payload']
    row = db.get_chat_session_by_id(int(op['target'])) or {}
    project = row.get('project_path') or ''
    new = {}
    if 'every' in changes:
        every = int(changes['every'] or 0)
        if not 60 <= every <= 86400:
            raise ValueError('Check-in interval must be 60–86400 seconds')
        new['every'] = every
    from api.chat_ops import brief
    if 'instructions' in changes:
        new['instructions'] = brief.focus(changes['instructions'])
    if 'involve_user' in changes:
        new['involve_user'] = brief.involve(changes['involve_user'])
    if 'nudge' in changes:
        new['nudge'] = brief.nudge(changes['nudge'])
    if 'required_branch' in changes:
        new['required_branch'] = str(changes['required_branch'] or '').strip()
    if 'required_path' in changes:
        path = str(changes['required_path'] or '').strip()
        new['required_path'] = str(Path(path).resolve()) if path else ''
    if 'forbidden_branches' in changes:
        raw = changes['forbidden_branches'] or []
        if isinstance(raw, str):
            raw = raw.split(',')
        new['forbidden_branches'] = [str(b).strip() for b in raw if str(b).strip()]
    if new.get('required_branch', cfg.get('required_branch')) and \
            new.get('required_branch', cfg.get('required_branch')) in new.get('forbidden_branches', cfg.get('forbidden_branches') or []):
        raise ValueError('The required branch is also in the forbidden list')
    if 'model_tiers' in changes:
        if not isinstance(changes['model_tiers'] or {}, dict):
            raise ValueError('model_tiers must be a JSON object')
        new['model_tiers'] = changes['model_tiers'] or {}
    if 'checkpoints' in changes:
        from api.chat_ops.checkpoints import parse_checkpoints
        new['checkpoints'] = parse_checkpoints(changes['checkpoints'])
    agent = cfg.get('agent') or ''
    if 'agent' in changes:
        agent = _agent_slash(changes['agent'], project)
        if not agent:
            raise ValueError('Pick an agent for the supervisor')
        new['agent'] = agent
    pin_keys = ('agent', 'model', 'effort')
    if any(k in changes for k in pin_keys):
        model = str(changes.get('model', cfg.get('model')) or '').strip()
        effort = str(changes.get('effort', cfg.get('effort')) or '').strip().lower()
        if 'agent' in changes and 'model' not in changes and new['agent'] != cfg.get('agent'):
            model = effort = ''  # another harness's model never carries over
        if model or effort:
            from api.agent_harness.agent_models import check_agent_pin
            problem = check_agent_pin(agent, model, effort)
            if problem:
                raise ValueError(problem)
        new['model'], new['effort'] = model, effort
    if 'task_ids' in changes:
        ids = changes['task_ids']
        if ids is not None and (not isinstance(ids, (list, tuple)) or len(ids) > 50):
            raise ValueError('task_ids must be a list of at most 50 Tasks ids')
        new['task_ids'] = None if ids is None else list(dict.fromkeys(str(t).strip() for t in ids if str(t).strip()))
        new['task_ids_known'] = [t['id'] for t in task_candidates(db, int(row.get('user_id') or user_id),
                                                                  int(op['target']), project)]
    policy = {'involve_user': brief.DEFAULT_INVOLVE, 'nudge': brief.DEFAULT_NUDGE}

    def same(k, v):  # blank model/effort/tiers equal a field the schedule never stored
        old = cfg.get(k) or policy.get(k)
        return v == old or (k != 'task_ids' and not v and not old)
    updated = {k: v for k, v in new.items() if k != 'task_ids_known' and not same(k, v)}
    if not updated:
        return {'operation': op, 'updated': []}
    patch = dict(updated)
    if 'checkpoints' in updated:
        # New watch settings start from today's files and counts, never a backlog.
        store.drop_result_keys(op['id'], 'paths_base', 'paths_reported', 'failure_counts')
    if 'task_ids' in updated:
        patch['task_ids_known'] = new['task_ids_known']
    if 'forbidden_branches' in updated and updated['forbidden_branches']:
        from api.chat_ops.rules import branch_tips
        patch['forbidden_tips'] = branch_tips(checkout_state(row, db)[0], updated['forbidden_branches'])
    store.patch_payload(op['id'], **patch)
    from api.orchestration_telemetry import record
    record.supervision_schedule('edit', store.find(op['id']), fields=sorted(updated), source='user')
    if any(k in updated for k in pin_keys):
        _pin_supervisor_chat(db, int(op['actor']), int(user_id), agent, new.get('model', ''),
                             new.get('effort', ''), project)
    if 'every' in updated:
        # A shorter interval takes effect now; a longer one from the next check.
        last = (op.get('result') or {}).get('last_check') or time.time()
        store.pull_due(op['id'], last + updated['every'])
    record_schedule_change(store.find(op['id']), updated, [], [], source='you', db=db)
    warnings = brief.focus_warnings(updated['instructions']) if 'instructions' in updated else []
    return {'operation': store.find(op['id']), 'updated': sorted(updated), 'warnings': warnings}


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
    return {'handle': f'CH-{sid:06d}', 'branch': branch, 'execution_path': path, 'branches': _branch_names(path),
            'worktree': bool(checkout), 'project_path': row.get('project_path') or '',
            'tasks': task_candidates(db, int(user_id), sid, row.get('project_path') or ''),
            'playbook': _playbook_supervisor(sid, db)}


def _branch_names(path):
    """Local branches, then remote-only ones, for the dialog's branch pickers ([] off git)."""
    if not path:
        return []
    try:
        from api.git_service import list_branches
        rows = list_branches(path)['branches']
    except Exception:
        return []
    local = [b['name'] for b in rows if not b.get('remote')]
    remote = [b['name'] for b in rows if b.get('remote') and '->' not in b['name']]
    return list(dict.fromkeys(local + remote))


def _playbook_supervisor(sid, db):
    """The supervisor defaults of the playbook ``sid`` runs (None when it is not
    orchestrating): the "Supervise this chat…" dialog starts from them."""
    try:
        from api.orchestrator import service as orch, store as orch_store
        from api.orchestrator.model import supervisor_config
        binding = orch_store.binding(int(sid)) if orch.enabled() else None
        if not binding:
            return None
        owner = int(db.get_chat_session_by_id(int(sid))['user_id'])
        doc = orch.get_playbook(owner, binding['playbook_id'], db=db)
        return {'id': doc['id'], 'name': doc.get('name') or doc['id'],
                **supervisor_config(doc, orch.profile_map(owner, db=db))}
    except Exception:
        return None


# A pause with no stated end is how supervision gets forgotten: default to a
# timed one, and keep the indefinite pause an explicit choice.
DEFAULT_PAUSE_SEC = 4 * 3600
MAX_PAUSE_SEC = 30 * 24 * 3600
MIN_PAUSE_SEC = 60


def pause_expiry(*, hours=None, until=None, indefinite=False, now=None):
    """Epoch expiry for a pause request, or None for an explicit indefinite pause."""
    now = time.time() if now is None else now
    if indefinite:
        return None
    if until not in (None, ''):
        try:
            when = float(until)
        except (TypeError, ValueError):
            raise ValueError('until must be epoch seconds') from None
    elif hours not in (None, ''):
        try:
            when = now + float(hours) * 3600
        except (TypeError, ValueError):
            raise ValueError('hours must be a number') from None
    else:
        when = now + DEFAULT_PAUSE_SEC
    if not now + MIN_PAUSE_SEC <= when <= now + MAX_PAUSE_SEC:
        raise ValueError('Pause must end between 1 minute and 30 days from now')
    return when


def set_paused(op_id, user_id, paused, *, hours=None, until=None, indefinite=False, db=None):
    op = owned_operation(op_id, user_id, db=db)
    if op['kind'] != 'supervise':
        raise ValueError('Only supervision schedules can be paused')
    expiry = pause_expiry(hours=hours, until=until, indefinite=indefinite) if paused else None
    return store.set_paused(op['id'], op['actor'], paused, until=expiry)


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


def _ago(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 3600:
        return f'{seconds // 60} min'
    if seconds < 86400:
        return f'{seconds // 3600} h {(seconds % 3600) // 60} min'
    return f'{seconds // 86400} d {(seconds % 86400) // 3600} h'


def _clock(epoch):
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(epoch)).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OSError):
        return ''


def supervision_digest(session_id, *, now=None, operations=None):
    """The chat's own supervision state as markdown for its context envelope.

    A paused schedule is invisible from inside a chat: check-ins simply stop, so
    both the supervisor and the supervised chat can work for days without anyone
    noticing (incident 2026-11-03: one schedule paused from the UI stayed paused
    for three days). Every turn of either chat now carries this block."""
    now = time.time() if now is None else now
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return ''
    ops = store.involving(sid) if operations is None else operations
    mine, over = [], []
    for op in ops:
        if op['kind'] != 'supervise':
            continue
        (mine if int(op['actor']) == sid else over).append(op)
    if not mine and not over:
        return ''
    lines = []
    for op in sorted(mine + over, key=lambda o: (o['state'] != 'paused', o['updated'])):
        cfg, result = op['payload'], op['result'] or {}
        own = int(op['actor']) == sid
        who = (f"you supervise CH-{int(op['target']):06d}" if own
               else f"CH-{int(op['actor']):06d} supervises this chat")
        every = f"every {int(cfg.get('every') or 0) // 60} min" if cfg.get('every') else ''
        if op['state'] == 'paused':
            since = result.get('paused_at') or op['updated']
            until = result.get('paused_until')
            head = f"**PAUSED since {_clock(since)} ({_ago(now - float(since or now))} ago)** — {who}"
            tail = (f"auto-resumes {_clock(until)}" if until
                    else 'no auto-resume: nothing checks or wakes anyone until someone resumes it')
            extra = (f"Resume with `python -m api.chat_cli resume-operation {op['id']} "
                     f"--as CH-{int(op['actor']):06d}`." if own else
                     f"Tell CH-{int(op['actor']):06d}, or resume it from the chip in the UI.")
            lines.append(f"- {head} — {every}, {tail}. {extra}")
        else:
            state = 'checking now' if op['state'] == 'dispatching' else f"next check {_clock(op['due'])}"
            lines.append(f"- active — {who}, {every}, {state}")
        if own:
            # The supervisor can retune its own schedule; say how, so it never
            # tells the user the CLI cannot (incident 2026-11-10, CH-001822).
            pin = ' · '.join(p for p in (cfg.get('agent') or '', cfg.get('model') or '',
                                         cfg.get('effort') or '') if p) or 'chat default'
            lines.append(f"  schedule `{op['id']}` · check-ins run on {pin}. Change model/effort/harness/"
                         f"interval in place: `python -m api.chat_cli edit-operation {op['id']} "
                         f"--model M [--effort E] [--agent A] [--every S] --as CH-{sid:06d}`.")
    return ('### Supervision schedules on this chat\n'
            'Cuttle owns these timers, not your turn. A PAUSED schedule means no check-ins and '
            'no wake-ups for anyone — say so to the user instead of assuming supervision is running.\n'
            + '\n'.join(lines))


def mark_supervision(sessions):
    """Set ``supervisor_session_id`` (the chat supervising this row) and
    ``supervises_session_ids`` (chats this row supervises) on session-list rows,
    so the history panel can nest supervised chats under their supervisor."""
    from api.experimental import is_enabled
    links = store.supervisor_links() if is_enabled('chat_supervision') else {}
    links = {t: a for t, a in links.items() if t != a}
    targets = {}
    for target, actor in links.items():
        targets.setdefault(actor, []).append(target)
    for row in sessions:
        try:
            sid = int(row.get('id'))
        except (TypeError, ValueError):
            continue
        row['supervisor_session_id'] = links.get(sid)
        row['supervises_session_ids'] = sorted(targets.get(sid, []))


def _schedule_view(op):
    cfg, result = op['payload'], op['result'] or {}
    seen = result.get('last_seen') or {}
    return {'id': op['id'], 'state': op['state'], 'supervisor': f"CH-{op['actor']:06d}",
            'target': f"CH-{op['target']:06d}", 'every': cfg.get('every'), 'next_check': op['due'],
            'agent': cfg.get('agent') or '', 'required_branch': cfg.get('required_branch') or '',
            'model': cfg.get('model') or '', 'effort': cfg.get('effort') or '',
            'instructions': cfg.get('instructions') or '', 'involve_user': cfg.get('involve_user') or 'blocked',
            'nudge': cfg.get('nudge') or 'agent', 'required_path': cfg.get('required_path') or '',
            'model_tiers': cfg.get('model_tiers') or {}, 'task_ids': cfg.get('task_ids'),
            'checkpoints': cfg.get('checkpoints') or {},
            'forbidden_branches': cfg.get('forbidden_branches') or [], 'last_check': result.get('last_check'),
            'last_seen': seen, 'violations': seen.get('violations') or [],
            # A paused schedule says since when and whether it comes back by itself,
            # so "paused" can never read as "running" on a chip.
            'paused_at': result.get('paused_at') or (op['updated'] if op['state'] == 'paused' else None),
            'paused_until': result.get('paused_until'),
            'history': store.checks(op['id'], limit=20)}


def stop(target, actor=None, *, db=None, request=None):
    """Stop the target's turn and drop sends still queued for it, so the stop
    holds: an outbox send would otherwise restart the chat as soon as it idles."""
    db, actor, row = context(target, actor, db=db)
    from api.orchestration_telemetry import record
    record.supervisor_action('sup_stop', actor, int(row['id']))
    held = store.cancel_sends_to(row['id'])
    out = dict((request or transport.request)(row['user_id'], row['id'], 'POST', '/api/chat-cancel') or {})
    out['cancelled_sends'] = [{'id': o['id'], 'from': f"CH-{o['actor']:06d}", 'text': o['payload'].get('text', '')[:200]}
                              for o in held]
    return out


def chat_agent(db, row):
    """Harness id (``claude``) the chat's turns run on, or ''.

    The agent that last ran a turn here (Brain handoff record) wins; otherwise
    the same pin a sent turn would get (``pin_to_chat_agent``)."""
    from api.cuttle_brain.handoff import get_last_agent
    last = (get_last_agent(str(row['id'])) or '').strip()
    if last:
        return last
    pinned = pin_to_chat_agent(db, row, '_')
    return pinned.split()[0].lstrip('/').lower() if pinned.startswith('/') else ''


def context_fill(db, row, *, agent=None):
    """Compact window-fill view of a chat's agent (None when it has no harness)."""
    from api.agent_context import get_agent_context_status
    aid = (agent or chat_agent(db, row) or '').strip().lower()
    if not aid:
        return None
    path, _branch = checkout_state(row, db)
    status = get_agent_context_status(chat_session_id=str(row['id']), agent_id=aid, cwd=path or None)
    if not status.get('success'):
        return {'agent': aid, 'supported': False, 'error': status.get('error')}
    out = {'agent': aid, 'supported': True, 'percent': status.get('percent'),
           'used_tokens': status.get('used_tokens'), 'limit_tokens': status.get('limit_tokens'),
           'compact_available': bool(status.get('compact_available')), 'hint': status.get('hint')}
    auto = _auto_compact_view(row['id'], db)
    if auto:
        out['auto_compact'] = auto
    return out


def _auto_compact_view(sid, db):
    """The chat's auto-compact policy when the feature is on (else None)."""
    try:
        from api.auto_compact.service import chat_policy
        view = chat_policy(sid, db=db)
    except Exception:
        return None
    if not view.get('available'):
        return None
    p = view['policy']
    return {'enabled': p['enabled'], 'percent': p['percent'], 'max_tokens': p['max_tokens'],
            'milestone': p['milestone'], 'scope': p['scope']}


def agent_context(target, actor=None, *, agent=None, db=None):
    db, actor, row = context(target, actor, db=db)
    return {'schema_version': 1, 'ok': True, 'handle': f"CH-{row['id']:06d}",
            'context': context_fill(db, row, agent=agent)}


def compact(target, actor=None, *, mode='native', agent=None, db=None, request=None):
    """Compact the target chat's agent session between turns.

    ``native`` asks the CLI to summarize its own session; ``fresh`` discards it
    so the next turn starts a new session with the full Cuttle briefing and
    the recent transcript. Runs on the host (it owns the live-CLI check) and
    is refused while the target is busy: a compact must never race a turn."""
    if mode not in ('native', 'fresh'):
        raise ValueError('mode must be native or fresh')
    db, actor, row = context(target, actor, db=db)
    sid, uid = int(row['id']), int(row['user_id'])
    aid = (agent or chat_agent(db, row) or '').strip().lower()
    if not aid:
        raise ValueError('Target chat has not run a harness agent yet; pass --agent')
    request = request or transport.request
    live = request(uid, sid, 'GET', '/api/chat-live-status') or {}
    if live.get('generating'):
        return {'schema_version': 1, 'ok': False, 'handle': f'CH-{sid:06d}',
                'error': 'Target chat is busy; compact between turns'}
    path, _branch = checkout_state(row, db)
    try:
        result = request(uid, sid, 'POST', '/api/agent-context/compact',
                         {'agent': aid, 'session': str(sid), 'mode': mode, 'path': path})
    except RuntimeError as exc:
        # The host's own refusal (no session yet, live CLI, unsupported agent).
        return {'schema_version': 1, 'ok': False, 'handle': f'CH-{sid:06d}',
                'agent': aid, 'mode': mode, 'error': str(exc)}
    after = (result.get('status') or {}) if isinstance(result, dict) else {}
    return {'schema_version': 1, 'ok': bool(result.get('success')), 'handle': f'CH-{sid:06d}',
            'agent': aid, 'mode': mode, 'method': result.get('method'),
            'detail': result.get('detail') or result.get('message'),
            'error': result.get('error'),
            'percent_after': after.get('percent') if after.get('success') else None}
