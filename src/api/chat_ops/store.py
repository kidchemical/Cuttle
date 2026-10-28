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
    return get(oid, actor)


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


def recent(limit=200):
    with connection() as conn:
        return [public(r) for r in conn.execute(
            "SELECT * FROM operations WHERE kind IN ('send','supervise') ORDER BY updated DESC LIMIT ?", (limit,))]


def cancel(oid, actor):
    with connection() as conn:
        conn.execute("UPDATE operations SET state='cancelled', updated=? WHERE id=? AND actor=? "
                     "AND (state IN ('pending','paused','verifying') OR (state='dispatching' AND kind='supervise'))",
                     (time.time(), oid, actor))
    return get(oid, actor)


def set_paused(oid, actor, paused):
    """Pause or resume a schedule; a resumed schedule checks right away."""
    now = time.time()
    with connection() as conn:
        if paused:
            conn.execute("UPDATE operations SET state='paused', updated=? WHERE id=? AND actor=? "
                         "AND kind='supervise' AND state IN ('pending','dispatching')", (now, oid, actor))
        else:
            conn.execute("UPDATE operations SET state='pending', due=?, updated=? WHERE id=? AND actor=? "
                         "AND kind='supervise' AND state='paused'", (now, now, oid, actor))
    return get(oid, actor)


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


def record_check(schedule_id, outcome, detail=None):
    now = time.time()
    with connection() as conn:
        conn.execute('INSERT INTO checks VALUES (?,?,?,?)', (schedule_id, now, outcome, json.dumps(detail or {})))
        conn.execute('DELETE FROM checks WHERE schedule_id=? AND at < (SELECT MIN(at) FROM '
                     '(SELECT at FROM checks WHERE schedule_id=? ORDER BY at DESC LIMIT ?))',
                     (schedule_id, schedule_id, HISTORY_KEEP))


def checks(schedule_id, limit=HISTORY_KEEP):
    with connection() as conn:
        return [{'at': r['at'], 'outcome': r['outcome'], 'detail': json.loads(r['detail'])}
                for r in conn.execute('SELECT * FROM checks WHERE schedule_id=? ORDER BY at DESC LIMIT ?',
                                      (schedule_id, limit))]
