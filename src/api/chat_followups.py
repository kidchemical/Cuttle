"""Transactional shared follow-up queue. AuthDatabase injects its connection.

Append and take reserve the SQLite writer before reading. Whole-list edits
carry an expected revision; a conflict never overwrites another device's work.
"""
from __future__ import annotations
import json


class Conflict(ValueError):
    def __init__(self, state):
        super().__init__('Follow-up queue changed; retry against the current revision')
        self.state = state


def state(conn, sid, uid, parse):
    row = conn.execute('SELECT followup_queue, followup_revision FROM chat_sessions WHERE id=? AND user_id=? AND is_active=1', (sid, uid)).fetchone()
    if row is None:
        return None
    return {'followups': parse(row['followup_queue']), 'revision': row['followup_revision'] or 0}


def mutate(conn, sid, uid, operation, value, expected, parse, normalize, claim_id=None):
    conn.execute('BEGIN IMMEDIATE')
    before = state(conn, sid, uid, parse)
    if before is None:
        return None
    if claim_id is not None and (not isinstance(claim_id, str) or not claim_id or len(claim_id) > 160):
        raise ValueError('Invalid queue claim id')
    if operation == 'take' and claim_id:
        receipt = conn.execute('SELECT result FROM chat_followup_claims WHERE session_id=? AND claim_id=?', (sid, claim_id)).fetchone()
        if receipt:
            # The claimed batch is stable, but other clients may have changed
            # the remaining queue since a lost response. Return a fresh base.
            return {**before, 'taken': json.loads(receipt['result'])['taken']}
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool) or expected != before['revision']):
        raise Conflict(before)
    items = before['followups']
    taken = []
    if operation == 'replace':
        if not isinstance(value, list):
            raise ValueError('followups must be a list')
        items = [norm for item in value or [] if (norm := normalize(item))][:40]
    elif operation == 'append':
        norm = normalize(value)
        if norm and not any(x.get('id') == norm['id'] for x in items):
            if len(items) >= 40:
                raise ValueError('Follow-up queue is full')
            items = [*items, norm]
    elif operation == 'take':
        taken = [x for x in items if not x.get('paused')]
        items = [x for x in items if x.get('paused')]
    revision = before['revision']
    if items != before['followups']:
        revision += 1
        conn.execute('UPDATE chat_sessions SET followup_queue=?, followup_revision=?, last_activity=CURRENT_TIMESTAMP WHERE id=?', (json.dumps(items), revision, sid))
    result = {'followups': items, 'revision': revision, 'taken': taken}
    if operation == 'take' and claim_id and taken:
        conn.execute('INSERT INTO chat_followup_claims(session_id, claim_id, result) VALUES (?,?,?)', (sid, claim_id, json.dumps(result)))
    conn.commit()
    return result
