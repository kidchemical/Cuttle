"""Durable voice session and delegation receipts, without credentials."""
import json
import sqlite3
import time
from contextlib import contextmanager

from core.runtime_paths import data_db_dir


@contextmanager
def database():
    path = data_db_dir() / "voice_sessions.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS voice_sessions (
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
          request_id TEXT NOT NULL, provider_id TEXT, state TEXT NOT NULL,
          data TEXT NOT NULL DEFAULT '{}', updated REAL NOT NULL,
          UNIQUE(user_id, request_id));
        CREATE TABLE IF NOT EXISTS voice_events (
          session_id TEXT NOT NULL, event_id TEXT NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(session_id,event_id));
        CREATE TABLE IF NOT EXISTS voice_delegations (
          session_id TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL,
          data TEXT NOT NULL, PRIMARY KEY(session_id,id));
    """)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def create(voice_id, user_id, chat_id, request_id, data):
    with database() as conn:
        conn.execute("INSERT INTO voice_sessions(id,user_id,chat_id,request_id,state,data,updated) VALUES (?,?,?,?,'starting',?,?)",
                     (voice_id, user_id, chat_id, request_id, json.dumps(data), time.time()))


def by_request(user_id, request_id):
    with database() as conn:
        row = conn.execute("SELECT * FROM voice_sessions WHERE user_id=? AND request_id=?", (user_id, request_id)).fetchone()
        return unpack(row)


def unpack(row):
    return {**dict(row), "data": json.loads(row["data"])} if row else None


def get(voice_id, user_id=None):
    with database() as conn:
        row = conn.execute("SELECT * FROM voice_sessions WHERE id=?", (voice_id,)).fetchone()
        return unpack(row) if row and (user_id is None or row["user_id"] == user_id) else None


def update(voice_id, *, state=None, provider_id=None, **data):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM voice_sessions WHERE id=?", (voice_id,)).fetchone()
        if not row:
            return
        merged = json.loads(row["data"])
        merged.update(data)
        conn.execute("UPDATE voice_sessions SET state=?,provider_id=?,data=?,updated=? WHERE id=?",
                     (state or row["state"], provider_id or row["provider_id"], json.dumps(merged), time.time(), voice_id))


def event(voice_id, payload):
    event_id = payload.get("event_id")
    if not event_id:
        return True  # reflected audio has no event id and is never a charge/task receipt
    with database() as conn:
        return bool(conn.execute("INSERT OR IGNORE INTO voice_events VALUES (?,?,?)",
                    (voice_id, event_id, json.dumps(payload))).rowcount)


def transcripts(voice_id):
    with database() as conn:
        rows = conn.execute("SELECT data FROM voice_events WHERE session_id=? ORDER BY rowid", (voice_id,)).fetchall()
    return [event for row in rows if (event := json.loads(row[0])).get("type") in
            {"session.input_transcript.delta", "session.output_transcript.delta"}]


def recent_voice_history(chat_id):
    with database() as conn:
        rows = conn.execute("""SELECT e.data FROM voice_events e JOIN voice_sessions s ON s.id=e.session_id
            WHERE s.chat_id=? AND s.state IN ('closed','disconnected') ORDER BY e.rowid DESC LIMIT 80""", (chat_id,)).fetchall()
    return [event for row in reversed(rows) if (event := json.loads(row[0])).get("type") in
            {"session.input_transcript.delta", "session.output_transcript.delta"}]


def claim_delegation(voice_id, delegation_id, data):
    with database() as conn:
        return bool(conn.execute("INSERT OR IGNORE INTO voice_delegations VALUES (?,?,'pending',?)",
                    (voice_id, delegation_id, json.dumps(data))).rowcount)


def finish_delegation(voice_id, delegation_id, result, state="complete"):
    with database() as conn:
        conn.execute("UPDATE voice_delegations SET state=?,data=? WHERE session_id=? AND id=?",
                     (state, json.dumps(result), voice_id, delegation_id))
