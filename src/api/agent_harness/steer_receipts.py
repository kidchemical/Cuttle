"""Turn-local steer receipt correlation; vendor events never create chat rows.

The transport registers a request before sending it. A row can be bound later;
the latest snapshot is retained until then, including echo and reply races.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import re
import uuid


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clip(text):
    return re.split(r'\n[ \t]*\n', str(text or '').strip(), maxsplit=1)[0].strip()[:280]


class Receipts:
    def __init__(self, changed=None):
        self._lock = threading.RLock()
        self._entries = []
        self._items = set()
        self._finished = False
        self._changed = changed or (lambda entry: None)

    def begin(self, text, turn_id):
        with self._lock:
            entry = {'steer_id': uuid.uuid4().hex, 'text': text, 'turn_id': turn_id,
                     'message_id': None, 'accepted': False, 'ended': self._finished,
                     'delivered_at': None, 'reply': None, 'state': None}
            self._entries.append(entry)
            return entry['steer_id']

    def _notify(self, steer_id):
        try:
            self._changed(self.snapshot(steer_id))
        except Exception:
            logging.getLogger(__name__).exception("Steer receipt update failed")

    def _find(self, steer_id):
        return next((e for e in self._entries if e['steer_id'] == steer_id), None)

    def snapshot(self, steer_id):
        with self._lock:
            e = self._find(steer_id)
            if e is None:
                return None
            return {**e, 'reply': dict(e['reply']) if e['reply'] else None}

    def refresh(self, steer_id):
        with self._lock:
            if self._find(steer_id) is not None:
                self._notify(steer_id)

    def bind(self, steer_id, message_id):
        with self._lock:
            e = self._find(steer_id)
            if e is None:
                return
            e['message_id'] = message_id
            self._notify(steer_id)

    def accepted(self, steer_id, accepted):
        with self._lock:
            e = self._find(steer_id)
            if e is None:
                return
            e['accepted'] = accepted
            if not accepted and not e['delivered_at']:
                self._entries.remove(e)
            elif e['ended'] and not e['delivered_at']:
                e['state'] = 'undelivered'
                self._notify(steer_id)

    def echo(self, text, item_id=None):
        with self._lock:
            if item_id and item_id in self._items:
                return
            if item_id:
                self._items.add(item_id)
            for e in self._entries:
                if not e['delivered_at'] and not e['ended'] and e['text'].strip() == text.strip():
                    e['delivered_at'] = _now()
                    e['state'] = 'delivered'
                    self._notify(e['steer_id'])
                    return

    def reply(self, text):
        clipped = _clip(text)
        if not clipped:
            return
        with self._lock:
            waiting = [e for e in self._entries if e['delivered_at'] and e['reply'] is None and not e['ended']]
            covers = [e['steer_id'] for e in waiting]
            at = _now()
            for e in waiting:
                e['reply'] = {'text': clipped, 'at': at, 'covers': covers}
                self._notify(e['steer_id'])

    def finish(self):
        with self._lock:
            self._finished = True
            for e in self._entries:
                e['ended'] = True
                if e['accepted'] and not e['delivered_at']:
                    e['state'] = 'undelivered'
                self._notify(e['steer_id'])

# Pending associations survive unregister until the post-delivery row is bound.
# Access to these maps never nests with the per-turn tracker lock.
_pending_lock = threading.Lock()
_pending = {}
_live = {}


def public(entry):
    reply = entry.get('reply')
    return {'steer_id': entry['steer_id'],
            'reply': {'text': reply['text'], 'at': reply['at']} if reply else None,
            'covers': list(reply.get('covers', [])) if reply else [],
            'delivered': bool(entry.get('delivered_at')),
            'state': entry.get('state'), 'delivered_at': entry.get('delivered_at')}


def track(session, tracker, steer_id):
    with _pending_lock:
        _pending[steer_id] = {'tracker': tracker, 'session': str(session), 'db': None, 'dirty': False}


def changed(entry):
    with _pending_lock:
        binding = _pending.get(entry['steer_id'])
        if binding is None:
            return
        session, db = binding['session'], binding['db']
        if db is not None and entry.get('message_id') is not None:
            binding['dirty'] = True
        if entry.get('message_id') is not None:
            bucket = _live.setdefault(session, {})
            bucket[entry['steer_id']] = {'message_id': entry['message_id'],
                                         'turn_id': entry['turn_id'], **public(entry)}
            while len(bucket) > 100:
                bucket.pop(next(iter(bucket)))
    if db is not None and entry.get('message_id') is not None:
        import json
        conn = db._get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT metadata FROM chat_messages WHERE id=? AND role=?',
                               (entry['message_id'], 'user')).fetchone()
            if row:
                metadata = json.loads(row['metadata']) if row['metadata'] else {}
                metadata['steer'] = public(entry)
                conn.execute('UPDATE chat_messages SET metadata=? WHERE id=?',
                             (json.dumps(metadata), entry['message_id']))
            conn.commit()
        finally:
            conn.close()
        try:
            from api.chat_live_status import notify_changed
            notify_changed()
        except Exception:
            pass
        with _pending_lock:
            current = _pending.get(entry['steer_id'])
            if current is not None:
                current['dirty'] = False
                if entry.get('ended') and entry.get('accepted'):
                    _pending.pop(entry['steer_id'], None)


def bind(steer_id, db, message_id):
    with _pending_lock:
        binding = _pending.get(steer_id)
        if binding is None:
            return
        binding['db'] = db
        tracker = binding['tracker']
    tracker.bind(steer_id, message_id)


def discard(steer_id):
    with _pending_lock:
        _pending.pop(steer_id, None)


def live(session):
    from api.agent_harness.steer import _key
    key = str(_key(session))
    # Retry a failed metadata write when the client polls. The tracker lock
    # serializes this retry with later stream updates for the same turn.
    with _pending_lock:
        retries = [(sid, binding['tracker']) for sid, binding in _pending.items()
                   if binding['session'] == key and binding['dirty']]
    for sid, tracker in retries:
        tracker.refresh(sid)
    with _pending_lock:
        return [dict(value) for value in _live.get(key, {}).values()]
