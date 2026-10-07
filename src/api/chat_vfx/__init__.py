"""Transient, session-scoped visual effects. Local agent library; no Flask import.

Publication means queued, not rendered. Every browser reads independently;
refresh can replay unexpired effects. Events expire after 30 seconds.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from api.session_keys import bare_chat_session_id
from core.runtime_paths import data_db_dir

TTL = 30

def session_key(value):
    key = bare_chat_session_id(value)
    if not key.isdigit() or int(key) <= 0:
        raise ValueError('a numeric chat session or CH handle is required')
    return int(key)

@contextmanager
def _connect():
    path = Path(os.getenv('CUTTLE_CHAT_VFX_DB') or data_db_dir() / 'chat_vfx.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, session INTEGER, expires REAL, payload TEXT)')
    try:
        with db:
            yield db
    finally:
        db.close()

def publish(session_id, kind, **options):
    session = session_key(session_id)
    if kind == 'confetti':
        count = options.get('count', 120)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 400:
            raise ValueError('count must be an integer from 1 to 400')
        payload = {'kind': kind, 'count': count}
    elif kind == 'toast':
        message = options.get('message')
        variant = options.get('variant', 'info')
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise ValueError('message must contain 1–500 characters')
        if variant not in ('info', 'success', 'warning', 'error'):
            raise ValueError('invalid toast variant')
        payload = {'kind': kind, 'message': message, 'variant': variant}
    else:
        raise ValueError('unknown effect')
    extra = set(options) - ({'count'} if kind == 'confetti' else {'message', 'variant'})
    if extra:
        raise ValueError('unknown effect options: ' + ', '.join(sorted(extra)))
    event_id = uuid.uuid4().hex
    now = time.time()
    with _connect() as db:
        db.execute('DELETE FROM events WHERE expires <= ?', (now,))
        db.execute('INSERT INTO events(id, session, expires, payload) VALUES (?,?,?,?)',
                   (event_id, session, now + TTL, json.dumps(payload)))
        db.execute('DELETE FROM events WHERE session=? AND seq NOT IN (SELECT seq FROM events WHERE session=? ORDER BY seq DESC LIMIT 1000)', (session, session))
    return event_id

def spawn_confetti(session_id, count=120):
    return publish(session_id, 'confetti', count=count)

def toast(session_id, message, variant='info'):
    return publish(session_id, 'toast', message=message, variant=variant)

def pending(session_id, after=0):
    session = session_key(session_id)
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise ValueError('after must be a nonnegative integer')
    with _connect() as db:
        db.execute('DELETE FROM events WHERE expires <= ?', (time.time(),))
        rows = db.execute('SELECT seq,id,expires,payload FROM events WHERE session=? AND seq>? ORDER BY seq LIMIT 100', (session, after)).fetchall()
    return [{'seq': seq, 'id': eid, 'expires': expires, **json.loads(payload)} for seq, eid, expires, payload in rows]
