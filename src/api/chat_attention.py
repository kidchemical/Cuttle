"""Durable chat attention, owned independently of browser observation.

AuthDatabase supplies its connection; updates share the message transaction.
Reply ids, not client clocks, order reads. Idle/running stays owned by delivery
and live status, so restarting Flask cannot leave a persistent running flag.
"""
from __future__ import annotations

import json
import re

_CARDS = re.compile(r'<cuttle_action_form(?:_pending)?\b([^>]*)>(.*?)</cuttle_action_form(?:_pending)?\s*>', re.I | re.S)


def awaiting_input(content: str) -> bool:
    """Only the current assistant turn's unlocked interactive cards need input."""
    for attrs, body in _CARDS.findall(content or ''):
        try:
            spec = json.loads(body.strip())
        except (TypeError, ValueError):
            continue
        if not isinstance(spec, dict) or spec.get('locked') or re.search(r'\blocked=["\']1["\']', attrs):
            continue
        # Watch/restart execution is running work, separately from a question.
        if spec.get('watch') or spec.get('restartId'):
            continue
        submit = spec.get('submit') if isinstance(spec.get('submit'), dict) else {}
        if spec.get('options') or spec.get('fields') or submit.get('action'):
            return True
    return False


def is_error(content: str, metadata: str | dict | None) -> bool:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}
    meta = metadata if isinstance(metadata, dict) else {}
    if any(meta.get(k) for k in ('slash_command_failed', 'failed', 'is_error')):
        return True
    text = re.sub(r'<think>.*?</think>', '', content or '', flags=re.I | re.S).strip()
    return bool(re.search(r'^(?:❌|Request failed\b)|Could not reach the Cuttle API|Sorry, I encountered an error|\b(?:cursor|codex|muse|claude|hermes|opencode|deepseek|antigravity)_error\b', text, re.I))


def ensure_schema(conn) -> None:
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    existed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_attention'").fetchone()
    conn.execute('''CREATE TABLE IF NOT EXISTS chat_attention (
        session_id INTEGER PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
        reply_id INTEGER NOT NULL DEFAULT 0, read_id INTEGER NOT NULL DEFAULT 0,
        reply_error INTEGER NOT NULL DEFAULT 0, awaiting_input INTEGER NOT NULL DEFAULT 0,
        manual_unread INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
        legacy_imported INTEGER NOT NULL DEFAULT 0)''')
    if not existed:
        # Upgrade baseline: don't light up every historical chat as unread.
        for row in conn.execute('SELECT id FROM chat_sessions').fetchall():
            refresh(conn, row['id'])
        conn.execute('UPDATE chat_attention SET read_id = reply_id, revision = 0')


def refresh(conn, session_id: int) -> None:
    """Reconcile attention after insert/content update within the same transaction."""
    conn.execute('INSERT OR IGNORE INTO chat_attention(session_id) VALUES (?)', (session_id,))
    reply = conn.execute("SELECT id, content, metadata FROM chat_messages WHERE chat_session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    user = conn.execute("SELECT MAX(id) FROM chat_messages WHERE chat_session_id=? AND role='user'", (session_id,)).fetchone()[0] or 0
    reply_id = reply['id'] if reply else 0
    error = int(is_error(reply['content'], reply['metadata'])) if reply else 0
    awaiting = int(bool(reply and reply_id > user and awaiting_input(reply['content'])))
    conn.execute('''UPDATE chat_attention SET reply_id=?, reply_error=?, awaiting_input=?,
        revision=revision+1 WHERE session_id=? AND (reply_id!=? OR reply_error!=? OR awaiting_input!=?)''',
        (reply_id, error, awaiting, session_id, reply_id, error, awaiting))


def snapshot(row) -> dict:
    r = dict(row)
    unread = bool(r['manual_unread'] or r['reply_id'] > r['read_id'])
    return {'reply_id': r['reply_id'], 'read_id': r['read_id'], 'hasUnread': unread,
            'unreadIsError': unread and bool(r['reply_error']),
            'awaitingInput': bool(r['awaiting_input']), 'revision': r['revision']}


def get_many(conn, user_id: int, ids: list[int]) -> dict:
    if not ids:
        return {}
    # Only owned active sessions; bound parameters even for batch queries.
    marks = ','.join('?' for _ in ids)
    rows = conn.execute(f'''SELECT a.* FROM chat_attention a JOIN chat_sessions s ON s.id=a.session_id
        WHERE s.user_id=? AND s.is_active=1 AND s.id IN ({marks})''', [user_id, *ids]).fetchall()
    return {r['session_id']: snapshot(r) for r in rows}


def mark(conn, session_id: int, user_id: int, data: dict) -> dict | None:
    conn.execute('BEGIN IMMEDIATE')
    if not conn.execute('SELECT 1 FROM chat_sessions WHERE id=? AND user_id=? AND is_active=1', (session_id, user_id)).fetchone():
        return None
    conn.execute('INSERT OR IGNORE INTO chat_attention(session_id) VALUES (?)', (session_id,))
    row = conn.execute('SELECT * FROM chat_attention WHERE session_id=?', (session_id,)).fetchone()
    if data.get('legacy'):
        if not row['legacy_imported']:
            conn.execute('UPDATE chat_attention SET manual_unread=?, legacy_imported=1, revision=revision+1 WHERE session_id=?', (int(bool(data.get('unread'))), session_id))
    elif data.get('unread'):
        conn.execute('UPDATE chat_attention SET manual_unread=1, legacy_imported=1, revision=revision+1 WHERE session_id=? AND (manual_unread=0 OR legacy_imported=0)', (session_id,))
    else:
        # A stale read must never acknowledge a reply that wasn't displayed.
        through = data.get('through_id')
        if not isinstance(through, int) or isinstance(through, bool) or through < 0:
            raise ValueError('through_id must be a nonnegative message id')
        through = min(through, row['reply_id'])
        conn.execute('''UPDATE chat_attention SET read_id=MAX(read_id,?), manual_unread=0,
            legacy_imported=1, revision=revision+1 WHERE session_id=? AND (read_id<? OR manual_unread=1 OR legacy_imported=0)''', (through, session_id, through))
    result = snapshot(conn.execute('SELECT * FROM chat_attention WHERE session_id=?', (session_id,)).fetchone())
    conn.commit()
    return result
