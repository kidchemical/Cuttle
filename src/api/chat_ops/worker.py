"""Flask-owned durable outbox pump. No independent executor or daemon lifetime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from api.chat_ops import rules, service, store, transport

_lock = threading.Lock()
_thread = None
_stop = threading.Event()
_active = {}


# An in-flight request may still be saving the turn when its connection drops;
# wait this long (and for the chat to go idle) before reading history.
VERIFY_GRACE_SEC = 30
MAX_SEND_ATTEMPTS = 3
# A busy target whose Tasks and git HEAD have not moved for this long wakes the
# supervisor anyway: one long turn can hide a retry loop for hours.
STALL_SEC = 45 * 60


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
        if op['payload'].get('agent_pins'):
            body['agent_pins'] = op['payload']['agent_pins']
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


def checkin_text(cfg, snap, violations, stalled_min=None):
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
    if stalled_min:
        lines += ['', f'**No progress for {stalled_min} min:** still mid-turn, but no commit and no Tasks '
                  'change. Check its run logs for a retry loop; if it keeps repeating a failing attempt, '
                  'steer it to escalate now.']
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


def _git_head(path):
    if not path:
        return ''
    try:
        out = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def _progress(snap):
    return json.dumps([_git_head(snap.get('execution_path')),
                       [(t['id'], t['done'], t['total']) for t in snap['tasks']]])


def _lineage_queries(db, sid):
    """Query ids of the supervised chat and its sub-agents (saved turns + live runs)."""
    from api.chat_run_registry import live_query_ids
    found, todo, seen = set(), [int(sid)], set()
    while todo and len(seen) < 200:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        found |= set(db.assistant_query_ids(current))
        found |= set(live_query_ids(current))
        conn = db._get_connection()
        try:
            todo += [r[0] for r in conn.execute('SELECT id FROM chat_sessions WHERE parent_session_id=?',
                                                (current,)).fetchall()]
        finally:
            conn.close()
    return found


def _violations(op, branch, path, *, db=None, commands=()):
    """Guard-rule violations and which of them are one-shot (moves and commands).

    A forbidden branch that moved is a violation only when the move is tied to
    the supervised chats (``rules.classify_move``) or a git write they ran names
    it; other people's commits become notes in the check history. The new tip
    becomes the baseline either way. Wrong branch / wrong checkout are states,
    re-raised on every interval check."""
    cfg = op['payload']
    found = rules.state_violations(cfg, branch, path)
    moved = []
    forbidden = cfg.get('forbidden_branches') or []
    if forbidden and path:
        worktrees = rules.worktree_branches(path) if commands else {}
        for sid, command in commands:
            if rules.forbidden_command(command, forbidden, worktrees):
                moved.append(f'CH-{sid:06d} ran a git write against a forbidden branch: `{command[:200]}`')
        tips = rules.branch_tips(path, forbidden)
        baseline = cfg.get('forbidden_tips')
        if baseline is None:  # schedule made before baselines existed
            baseline = tips
        changed = {b: (old, tips[b]) for b, old in baseline.items() if tips.get(b) and tips[b] != old}
        if changed:
            own = _lineage_queries(db, op['target']) if db is not None else set()
            required = cfg.get('required_branch') or ''
            protected = rules.branch_tips(path, [required]).get(required, '') if required else ''
            notes = []
            for b, (old, new) in sorted(changed.items()):
                bad, info = rules.classify_move(path, b, old, new, own_queries=own, protected_tip=protected,
                                                protected_name=required,
                                                path_pattern=cfg.get('forbidden_paths') or '')
                moved += bad
                notes += info
            if notes:
                store.record_check(op['id'], 'note', {'notes': notes})
        if changed or baseline != {**baseline, **tips} or 'forbidden_tips' not in cfg:
            store.patch_payload(op['id'], forbidden_tips={**baseline, **tips})
    return found + moved, moved


def _active_checkins(actor, schedule_id):
    return [o for o in store.listing(actor) if o['kind'] == 'checkin'
            and o['payload'].get('schedule_id') == schedule_id and o['state'] in ('pending', 'dispatching')]


VIOLATION_ENTRIES = ('wake', 'stop', 'action:', 'supervisor:')


def parse_on_violation(entries):
    """Validated ``on_violation`` list; ``wake`` (today's behavior) when empty."""
    out = [str(e).strip() for e in (entries or []) if str(e).strip()]
    for e in out:
        if not (e in ('wake', 'stop') or (e.startswith(('action:', 'supervisor:')) and e.split(':', 1)[1])):
            raise ValueError(f'on_violation entry {e!r}: use wake, stop, action:<name> or supervisor:<profile>')
    return out or ['wake']


def _run_action(name, row, target, violations):
    """An allowlisted .cuttle/actions recipe, through the same signed path as a confirm click."""
    from api import project_actions
    token = project_actions.encode_inline_action_payload(
        action_name=name, project_path=row.get('project_path') or '',
        params={'target': target, 'violations': '\n'.join(violations)}, session_id=str(row['id']))
    return project_actions.execute_inline_action(token, session_id=str(row['id']))


def _incident_supervisor(profile_id, db, row, cfg, snap, violations):
    """A fresh chat on the profile's agent/model/effort, briefed once with the incident.

    It is not bound as a second supervisor; it acts through the normal chat verbs."""
    from api.agent_harness.agent_models import check_agent_pin
    from api.subagents import profiles
    uid = int(row['user_id'])
    profile = profiles.resolve(db, uid, profile_id)
    if not profile or not profile.get('agent') or profile['agent'] == 'none':
        raise ValueError(f'profile {profile_id!r} not found or has no agent')
    agent, model, effort = profile['agent'], profile.get('model') or '', profile.get('effort') or ''
    error = check_agent_pin(agent, model, effort)
    if error:
        raise ValueError(error)
    chat = db.create_chat_session(uid, f"Guard incident · CH-{int(row['id']):06d}")
    if row.get('project_path'):
        db.set_session_project(chat, uid, project_id=row.get('project_id'),
                               project_name=row.get('project_name'), project_path=row.get('project_path'))
    text = checkin_text({**cfg, 'agent': agent}, snap, violations)
    payload = {'text': text}
    if model or effort:
        payload['agent_pins'] = {agent: {'model': model, 'effort': effort}}
    store.create('checkin', chat, chat, payload)
    return chat


def _respond(op, db, actor, row, snap, violations):
    """Run the schedule's non-wake ``on_violation`` entries for new violations.

    Returns whether the supervisor should be woken for them."""
    cfg = op['payload']
    entries = parse_on_violation(cfg.get('on_violation'))
    target = f"CH-{int(row['id']):06d}"
    for entry in entries:
        if entry == 'wake':
            continue
        try:
            if entry == 'stop':
                service.stop(target, actor, db=db)
                done = {}
            elif entry.startswith('action:'):
                result = _run_action(entry.split(':', 1)[1], row, target, violations)
                done = {'success': bool((result or {}).get('success'))}
            else:
                done = {'chat': _incident_supervisor(entry.split(':', 1)[1], db, row, cfg, snap, violations)}
            store.record_check(op['id'], 'acted', {'entry': entry, **done})
        except Exception as exc:
            store.record_check(op['id'], 'error', {'entry': entry, 'error': str(exc)[:300]})
    return 'wake' in entries


def guard_check(op, db=None, request=None, *, trigger='', commands=()):
    """Event-triggered rule check (``triggers``): wake the supervisor only on a
    violation it has not been told about. No LLM turn when the rules hold."""
    cfg = op['payload']
    if not rules.has_guards(cfg):
        return False
    db, actor, row = service.context(op['target'], op['actor'], db=db)
    path, branch = service.checkout_state(row, db)
    violations, moved = _violations(op, branch, path, db=db, commands=commands)
    reported = (op.get('result') or {}).get('guard_reported') or []
    if not violations:
        if reported:
            store.patch_result(op['id'], guard_reported=[])
        return False
    if sorted(violations) == sorted(reported):
        return False  # already raised; the interval check repeats it
    snap = service.snapshot(row['id'], actor, db=db, request=request or transport.request)
    store.record_check(op['id'], 'violation', {'state': snap['state'], 'branch': snap['branch'],
                                               'violations': violations, 'trigger': trigger})
    if _respond(op, db, actor, row, snap, [v for v in violations if v not in reported]):
        store.create('checkin', actor, actor, {'text': checkin_text(cfg, snap, violations), 'schedule_id': op['id']})
    store.patch_result(op['id'], guard_reported=violations)
    return True


def _check_in(op, db, actor, sid, request):
    """Wake the supervisor only when the target needs attention or changed."""
    cfg = op['payload']
    snap = service.snapshot(sid, actor, db=db, request=request)
    found, moved = _violations(op, snap['branch'], snap['execution_path'], db=db)
    previous = op.get('result') or {}
    new = [v for v in found if v not in (previous.get('guard_reported') or [])]
    wake = True
    if new:
        db_, _a, row = service.context(op['target'], op['actor'], db=db)
        wake = _respond(op, db_, actor, row, snap, new)
    elif found:
        wake = 'wake' in parse_on_violation(cfg.get('on_violation'))
    # Violations only wake the supervisor when the schedule says so.
    violations = found if wake else []
    if not wake:
        moved = []
    signature = _signature(snap, violations)
    nxt = time.time() + cfg['every']
    seen = {'state': snap['state'], 'branch': snap['branch'], 'violations': violations,
            'tasks': [{'title': t['title'], 'done': t['done'], 'total': t['total']} for t in snap['tasks']]}
    # The first check of a UI-started schedule always briefs the new supervisor.
    briefing = bool(cfg.get('brief_on_start')) and not previous.get('last_checkin')
    now = time.time()
    progress = _progress(snap)
    progress_at = previous.get('progress_at') or now
    if progress != previous.get('progress'):
        progress_at = now
    stalled = snap['state'] == 'busy' and now - progress_at >= cfg.get('stall_after', STALL_SEC)
    tracking = {'progress': progress, 'progress_at': now if stalled else progress_at}
    if stalled:
        seen['stalled_min'] = int((now - progress_at) // 60)
    # A working target or an unchanged picture costs no supervisor tokens.
    if not violations and not briefing and not stalled and (
            snap['state'] == 'busy' or signature == previous.get('signature')):
        reason = 'busy' if snap['state'] == 'busy' else 'unchanged'
        store.record_check(op['id'], 'skipped', {**seen, 'reason': reason})
        store.finish(op['id'], 'pending', {**previous, **tracking, 'last_check': time.time(),
                                            'last_seen': seen, 'skipped': reason, 'guard_reported': found}, due=nxt)
        return
    active = _active_checkins(actor, op['id'])
    store.record_check(op['id'], 'violation' if violations else 'stalled' if stalled else 'woke',
                       {**seen, 'already_waiting': bool(active)})
    # A moved forbidden branch is reported once, so it never waits behind a queued check-in.
    if not active or moved:
        text = checkin_text(cfg, snap, violations, stalled_min=seen.get('stalled_min'))
        store.create('checkin', actor, actor, {'text': text, 'schedule_id': op['id']})
    store.finish(op['id'], 'pending', {**tracking, 'signature': signature, 'last_check': time.time(),
                                        'last_checkin': time.time(), 'last_seen': seen,
                                        'guard_reported': found}, due=nxt)


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
    from api.chat_ops import triggers
    triggers.start()
    return True
