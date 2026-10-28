"""Durable operation outbox and opt-in supervision profiles in Cuttle home."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


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
        yield conn
        conn.commit()
    finally:
        conn.close()


def public(row):
    return {**dict(row), 'payload': json.loads(row['payload']), 'result': json.loads(row['result'])}


def create(kind, actor, target, payload, *, due=None):
    now = time.time()
    oid = uuid.uuid4().hex
    with connection() as conn:
        conn.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)',
            (oid, kind, actor, target, json.dumps(payload), 'pending', now if due is None else due, now, '{}'))
    return get(oid, actor)


def get(oid, actor):
    with connection() as conn:
        row = conn.execute('SELECT * FROM operations WHERE id=? AND actor=?', (oid, actor)).fetchone()
        if not row:
            raise ValueError('Operation not found for this acting chat')
        return public(row)


def listing(actor):
    with connection() as conn:
        return [public(r) for r in conn.execute('SELECT * FROM operations WHERE actor=? ORDER BY updated DESC LIMIT 100', (actor,))]


def cancel(oid, actor):
    with connection() as conn:
        conn.execute("UPDATE operations SET state='cancelled', updated=? WHERE id=? AND actor=? AND state='pending'", (time.time(), oid, actor))
    return get(oid, actor)


def due():
    with connection() as conn:
        return [public(r) for r in conn.execute("SELECT * FROM operations WHERE state='pending' AND due<=? ORDER BY due LIMIT 20", (time.time(),))]


def claim(oid):
    with connection() as conn:
        changed = conn.execute("UPDATE operations SET state='dispatching', updated=? WHERE id=? AND state='pending'", (time.time(), oid)).rowcount
        return bool(changed)


def finish(oid, state, result, *, due=None):
    with connection() as conn:
        conn.execute('UPDATE operations SET state=?,result=?,updated=?,due=COALESCE(?,due) WHERE id=?',
                     (state, json.dumps(result), time.time(), due, oid))
