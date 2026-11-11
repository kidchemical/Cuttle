"""Durable operation outbox and opt-in supervision profiles in Cuttle home."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

# Per-schedule check history kept for the UI; older rows are pruned on write.
HISTORY_KEEP = 50


@contextmanager
def connection():
    from core.runtime_paths import runtime_state_path
    conn = sqlite3.connect(runtime_state_path('chat_ops', 'operations.sqlite3'), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor INTEGER NOT NULL,
            target INTEGER NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
            due REAL NOT NULL, updated REAL NOT NULL, result TEXT NOT NULL DEFAULT '{}')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS checks (
            schedule_id TEXT NOT NULL, at REAL NOT NULL, outcome TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}')''')
        conn.execute('CREATE INDEX IF NOT EXISTS checks_by_schedule ON checks(schedule_id, at)')
        yield conn
        conn.commit()
    finally:
        conn.close()


def public(row):
    return {**dict(row), 'payload': json.loads(row['payload']), 'result': json.loads(row['result'])}


def create(kind, actor, target, payload, *, due=None, state='pending'):
    now = time.time()
    oid = uuid.uuid4().hex
    with connection() as conn:
        conn.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)',
            (oid, kind, actor, target, json.dumps(payload), state, now if due is None else due, now, '{}'))
    op = get(oid, actor)
    if kind == 'supervise':
        from api.orchestration_telemetry import record
        record.supervision_schedule('start', op)
    return op


def get(oid, actor):
    with connection() as conn:
        row = conn.execute('SELECT * FROM operations WHERE id=? AND actor=?', (oid, actor)).fetchone()
        if not row:
            raise ValueError('Operation not found for this acting chat')
        return public(row)


def find(oid):
    """Owner-agnostic lookup; callers authorize against the acting chat."""
    with connection() as conn:
        row = conn.execute('SELECT * FROM operations WHERE id=?', (oid,)).fetchone()
        return public(row) if row else None


def listing(actor):
    with connection() as conn:
        return [public(r) for r in conn.execute('SELECT * FROM operations WHERE actor=? ORDER BY updated DESC LIMIT 100', (actor,))]


def involving(session_id, *, states=('pending', 'dispatching', 'verifying', 'paused')):
    """Live operations where the chat is the actor or the target."""
    marks = ','.join('?' * len(states))
    with connection() as conn:
        return [public(r) for r in conn.execute(
            f'SELECT * FROM operations WHERE (actor=? OR target=?) AND state IN ({marks}) '
            'ORDER BY updated DESC LIMIT 100', (session_id, session_id, *states))]


def notice_for_job(target, job_id, *, watch_token=None):
    """Whether a resume notice for detached job ``job_id`` was queued on ``target``.

    Lets a unit-watch poller that died mid-fire be recovered without a second resume."""
    return job_notice(target, job_id, watch_token=watch_token) is not None


def job_notice(target, job_id, *, watch_token=None):
    """Latest durable delivery receipt for a job; no dispatch or other effects."""
    if not job_id:
        return None
    with connection() as conn:
        rows = conn.execute("SELECT * FROM operations WHERE kind='notice' AND target=? ORDER BY updated DESC",
                            (int(target),)).fetchall()
    for row in rows:
        try:
            payload = json.loads(row['payload'])
            if payload.get('job_id') == job_id and (watch_token is None or payload.get('watch_token') == watch_token):
                return public(row)
        except ValueError:
            continue
    return None


def pull_due(oid, at):
    """Bring a pending schedule's next check forward to ``at`` (never later).

    True when it moved; a paused, cancelled or already-checking schedule is left alone."""
    with connection() as conn:
        return bool(conn.execute("UPDATE operations SET due=? WHERE id=? AND kind='supervise' "
                                 "AND state='pending' AND due>?", (float(at), oid, float(at))).rowcount)


def recent(limit=200):
    with connection() as conn:
        return [public(r) for r in conn.execute(
            "SELECT * FROM operations WHERE kind IN ('send','supervise') "
            # Live rows first: a send parked for hours must not fall off the end
            # behind newer finished ones.
            "ORDER BY state IN ('pending','dispatching','verifying','paused','steering') DESC, "
            "updated DESC LIMIT ?", (limit,))]


def cancel(oid, actor):
    with connection() as conn:
        conn.execute("UPDATE operations SET state='cancelled', updated=? WHERE id=? AND actor=? "
                     "AND (state IN ('pending','paused','verifying') OR (state='dispatching' AND kind='supervise'))",
                     (time.time(), oid, actor))
    op = get(oid, actor)
    if op and op.get('kind') == 'supervise' and op.get('state') == 'cancelled':
        from api.orchestration_telemetry import record
        record.supervision_schedule('cancel', op)
    return op


def cancel_sends_to(target, *, kinds=('send',)):
    """Cancel every queued send to a chat; returns the cancelled operations.

    A stopped chat must stay stopped: a send still in the outbox would start a
    new turn the moment the chat goes idle. ``kinds`` adds ``'notice'`` (job
    wake-ups) for a stopped orchestration."""
    now = time.time()
    kinds = [k for k in kinds if k in ('send', 'notice')] or ['send']
    with connection() as conn:
        rows = [public(r) for r in conn.execute(
            f"SELECT * FROM operations WHERE kind IN ({','.join('?' * len(kinds))}) AND target=? "
            "AND state IN ('pending','verifying')", (*kinds, int(target)))]
        if rows:
            marks = ','.join('?' * len(rows))
            conn.execute(f"UPDATE operations SET state='cancelled', updated=? WHERE id IN ({marks})",
                         (now, *[r['id'] for r in rows]))
    return rows


def set_paused(oid, actor, paused, *, until=None):
    """Pause or resume a schedule; a resumed schedule checks right away.

    ``until`` (epoch seconds) makes the pause timed: :func:`resume_expired`
    brings it back by itself. A pause with no expiry is indefinite and only a
    person (or an agent) can end it — which is how a supervision schedule once
    sat forgotten for three days, so ``paused_at``/``paused_until`` are recorded
    for the chips, the agent's own context and the stale-pause alert."""
    now = time.time()
    with connection() as conn:
        if paused:
            # An already-paused schedule may be re-paused to change its expiry
            # (extend a pause, or make a timed one indefinite and back).
            changed = conn.execute(
                "UPDATE operations SET state='paused', updated=? WHERE id=? AND actor=? "
                "AND kind='supervise' AND state IN ('pending','dispatching','paused')",
                (now, oid, actor)).rowcount
        else:
            changed = conn.execute(
                "UPDATE operations SET state='pending', due=?, updated=? WHERE id=? AND actor=? "
                "AND kind='supervise' AND state='paused'", (now, now, oid, actor)).rowcount
    if changed:
        if paused:
            _patch(oid, 'result', {'paused_at': get(oid, actor)['result'].get('paused_at') or now,
                                   'paused_until': float(until) if until else None,
                                   'paused_alerted': False})
        else:
            _patch(oid, 'result', {'paused_at': None, 'paused_until': None, 'paused_alerted': False})
        from api.orchestration_telemetry import record
        record.supervision_schedule('pause' if paused else 'resume', get(oid, actor),
                                    until=float(until) if paused and until else None)
    return get(oid, actor)


def resume_expired(now=None):
    """Resume every timed pause whose expiry has passed; the resumed rows.

    Called by the outbox pump, so a timed pause auto-resumes and checks right
    away even if nobody has the chat open."""
    now = time.time() if now is None else now
    resumed = []
    with connection() as conn:
        rows = [public(r) for r in conn.execute(
            "SELECT * FROM operations WHERE kind='supervise' AND state='paused'")]
    for row in rows:
        until = (row.get('result') or {}).get('paused_until')
        try:
            due_at = float(until)
        except (TypeError, ValueError):
            continue
        if due_at > now:
            continue
        # Due now in real time, whatever clock the caller compared against.
        real = time.time()
        with connection() as conn:
            changed = conn.execute("UPDATE operations SET state='pending', due=?, updated=? "
                                   "WHERE id=? AND state='paused'", (real, real, row['id'])).rowcount
        if changed:
            _patch(row['id'], 'result', {'paused_at': None, 'paused_until': None, 'paused_alerted': False})
            record_check(row['id'], 'resumed', {'auto': True, 'paused_at': (row.get('result') or {}).get('paused_at')})
            resumed.append(find(row['id']))
    return resumed


def merge_pending_changes(oid, *, updated, kept, notes, source, at):
    """Add a settings edit to the schedule's ``pending_changes`` (payload), the
    record its next check-in folds in. Edits merge; ``rev`` grows on each."""
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT payload FROM operations WHERE id=?', (oid,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        old = payload.get('pending_changes') or {}
        merged_updated = sorted(set(old.get('updated') or []) | set(updated))
        merged = {
            'rev': int(old.get('rev') or 0) + 1,
            'updated': merged_updated,
            'kept': [k for k in dict.fromkeys([*(old.get('kept') or []), *kept]) if k not in merged_updated],
            'notes': list(dict.fromkeys([*(old.get('notes') or []), *notes])),
            'sources': list(dict.fromkeys([*(old.get('sources') or []), source])),
            'since': old.get('since') or at,
        }
        payload['pending_changes'] = merged
        conn.execute('UPDATE operations SET payload=? WHERE id=?', (json.dumps(payload), oid))
        return merged


def clear_pending_changes(oid, rev):
    """Drop ``pending_changes`` once a check-in carried them; an edit that landed
    after the check read them (newer ``rev``) stays for the next one."""
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT payload FROM operations WHERE id=?', (oid,)).fetchone()
        if not row:
            return False
        payload = json.loads(row[0])
        if int((payload.get('pending_changes') or {}).get('rev') or 0) != int(rev or 0):
            return False
        payload.pop('pending_changes', None)
        conn.execute('UPDATE operations SET payload=? WHERE id=?', (json.dumps(payload), oid))
        return True


def paused_schedules():
    """Every paused supervision schedule (for the stale-pause alert and context)."""
    with connection() as conn:
        return [public(r) for r in conn.execute(
            "SELECT * FROM operations WHERE kind='supervise' AND state='paused'")]


def due():
    with connection() as conn:
        return [public(r) for r in conn.execute("SELECT * FROM operations WHERE state IN ('pending','verifying') AND due<=? ORDER BY due LIMIT 20", (time.time(),))]


def claim(oid, state='pending'):
    with connection() as conn:
        changed = conn.execute("UPDATE operations SET state='dispatching', updated=? WHERE id=? AND state=?",
                               (time.time(), oid, state)).rowcount
        return bool(changed)


def recover_after_restart():
    """A send dispatching in an earlier Flask lifetime may or may not have
    reached its chat; the worker checks the chat's history for the send's id
    before resending (``verifying``). A schedule has no side effect, so it resumes."""
    now = time.time()
    with connection() as conn:
        conn.execute("UPDATE operations SET state='pending', due=?, updated=? "
                     "WHERE state='dispatching' AND kind='supervise'", (now, now))
        conn.execute("UPDATE operations SET state='verifying', due=?, updated=? WHERE state='dispatching'",
                     (now, now))


def finish(oid, state, result, *, due=None):
    # A pause or cancel that landed mid-check wins over the check's own state.
    with connection() as conn:
        conn.execute("UPDATE operations SET state=?,result=?,updated=?,due=COALESCE(?,due) "
                     "WHERE id=? AND state NOT IN ('cancelled','paused')",
                     (state, json.dumps(result), time.time(), due, oid))


def defer(oid, *, due, reason='', blocked_by=None):
    """An undelivered send whose target is busy: retry at ``due`` and remember since
    when it waits (``result.waiting_since``) and on what.

    Moving ``due`` matters: :func:`due` hands out the oldest rows first, so sends
    parked on a long turn would otherwise fill every slot and starve check-ins."""
    now = time.time()
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT result FROM operations WHERE id=? AND state IN ('pending','verifying')",
                           (oid,)).fetchone()
        if not row:
            return False
        result = json.loads(row[0])
        result.setdefault('waiting_since', now)
        result['waiting_on'] = reason or 'busy'
        if blocked_by:
            result['blocked_by'] = blocked_by
        conn.execute('UPDATE operations SET result=?, due=? WHERE id=?', (json.dumps(result), float(due), oid))
        return True


HELD_REASON = 'orchestration_paused'


def release_held(target):
    """Make deliveries that waited on a paused orchestration due now; how many."""
    now = time.time()
    released = 0
    with connection() as conn:
        for row in conn.execute("SELECT id, result FROM operations WHERE target=? AND state='pending' "
                                "AND kind IN ('send','notice')", (int(target),)).fetchall():
            try:
                waiting = json.loads(row['result'] or '{}').get('waiting_on')
            except ValueError:
                waiting = None
            if waiting == HELD_REASON:
                conn.execute('UPDATE operations SET due=?, updated=? WHERE id=?', (now, now, row['id']))
                released += 1
    return released


def waiting_sends(session_id, *, older_than=0.0, now=None):
    """Sends and notices from or to the chat still undelivered after
    ``older_than`` seconds, oldest first. Age counts from the first busy skip
    (``waiting_since``), else from when the row last changed."""
    now = time.time() if now is None else now
    found = []
    for op in involving(session_id, states=('pending', 'verifying')):
        if op['kind'] not in ('send', 'notice'):
            continue
        since = float((op.get('result') or {}).get('waiting_since') or op['updated'])
        if now - since >= older_than:
            found.append({**op, 'waiting_since': since})
    return sorted(found, key=lambda o: o['waiting_since'])


def record_check(schedule_id, outcome, detail=None):
    now = time.time()
    with connection() as conn:
        conn.execute('INSERT INTO checks VALUES (?,?,?,?)', (schedule_id, now, outcome, json.dumps(detail or {})))
        conn.execute('DELETE FROM checks WHERE schedule_id=? AND at < (SELECT MIN(at) FROM '
                     '(SELECT at FROM checks WHERE schedule_id=? ORDER BY at DESC LIMIT ?))',
                     (schedule_id, schedule_id, HISTORY_KEEP))
    # The checks table keeps the last HISTORY_KEEP per schedule; telemetry keeps them all.
    from api.orchestration_telemetry import record
    record.supervision_check(schedule_id, outcome, detail)


def checks(schedule_id, limit=HISTORY_KEEP):
    with connection() as conn:
        return [{'at': r['at'], 'outcome': r['outcome'], 'detail': json.loads(r['detail'])}
                for r in conn.execute('SELECT * FROM checks WHERE schedule_id=? ORDER BY at DESC LIMIT ?',
                                      (schedule_id, limit))]


def _patch(oid, column, fields):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(f'SELECT {column} FROM operations WHERE id=?', (oid,)).fetchone()
        if row:
            conn.execute(f'UPDATE operations SET {column}=? WHERE id=?',
                         (json.dumps({**json.loads(row[0]), **fields}), oid))


def patch_payload(oid, **fields):
    """Merge fields into a schedule's stored config (e.g. a guard baseline)."""
    _patch(oid, 'payload', fields)


def drop_result_keys(oid, *keys):
    """Forget per-schedule tracking state (e.g. a checkpoint baseline)."""
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT result FROM operations WHERE id=?', (oid,)).fetchone()
        if row:
            result = json.loads(row[0])
            for key in keys:
                result.pop(key, None)
            conn.execute('UPDATE operations SET result=? WHERE id=?', (json.dumps(result), oid))


def patch_result(oid, **fields):
    """Merge fields into an operation's result without touching state or due."""
    _patch(oid, 'result', fields)


def supervisor_links():
    """``{target: actor}`` for supervision schedules still in force (paused
    included, for UI grouping); when a chat has several supervisors the newest
    one wins. Use :func:`actively_supervised` to ask whether a chat is
    supervised right now."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT actor, target FROM operations WHERE kind='supervise' "
            "AND state IN ('pending','dispatching','paused') ORDER BY updated ASC").fetchall()
    return {int(r['target']): int(r['actor']) for r in rows}


def actively_supervised(target):
    """Whether ``target`` has a live (pending/dispatching, not paused) supervision schedule."""
    with connection() as conn:
        return conn.execute(
            "SELECT 1 FROM operations WHERE kind='supervise' AND state IN ('pending','dispatching') "
            "AND target=? LIMIT 1", (int(target),)).fetchone() is not None


def supervising(targets):
    """Live supervision schedules on any of ``targets``."""
    targets = [int(t) for t in targets]
    if not targets:
        return []
    marks = ','.join('?' * len(targets))
    with connection() as conn:
        return [public(r) for r in conn.execute(
            f"SELECT * FROM operations WHERE kind='supervise' AND state IN ('pending','dispatching') "
            f'AND target IN ({marks})', targets)]
