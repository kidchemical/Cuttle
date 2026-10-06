"""Indexed Brain bookkeeping with atomic cross-process updates.

Each former JSON map gets a sibling SQLite store. The first transaction imports
its legacy JSON once, preserving the original file. Normal turns read/write one
key; enumeration is reserved for maintenance/pruning.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class KeyStore:
    def __init__(self, legacy_path: Path):
        self.legacy_path = Path(legacy_path)
        self.path = self.legacy_path.with_suffix('.sqlite3')

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            if conn.execute("SELECT 1 FROM metadata WHERE key='legacy_imported'").fetchone() is None:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    # Another process may have finished migration while we waited.
                    if conn.execute("SELECT 1 FROM metadata WHERE key='legacy_imported'").fetchone() is None:
                        data = {}
                        if self.legacy_path.is_file():
                            data = json.loads(self.legacy_path.read_text(encoding='utf-8'))
                            if not isinstance(data, dict):
                                raise ValueError('Legacy Brain state must be a JSON object')
                        conn.executemany('INSERT OR IGNORE INTO entries VALUES (?, ?)',
                                         [(str(k), json.dumps(v)) for k, v in data.items()])
                        conn.execute("INSERT INTO metadata VALUES ('legacy_imported', '1')")
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
            yield conn
        finally:
            conn.close()

    def get(self, key):
        with self.connection() as conn:
            row = conn.execute('SELECT value FROM entries WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, key, change):
        """Read-modify-write one entry under SQLite's process-wide writer lock."""
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT value FROM entries WHERE key=?', (key,)).fetchone()
                value = change(json.loads(row[0]) if row else None)
                conn.execute('INSERT INTO entries VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             (key, json.dumps(value)))
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    def put(self, key, value):
        self.update(key, lambda _old: value)

    def all(self):
        with self.connection() as conn:
            return {k: json.loads(v) for k, v in conn.execute('SELECT key, value FROM entries')}

    def delete(self, keys):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.executemany('DELETE FROM entries WHERE key=?', [(k,) for k in keys])
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    def delete_prefixes(self, prefixes):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                for prefix in prefixes:
                    conn.execute('DELETE FROM entries WHERE key >= ? AND key < ?', (prefix, prefix + '\U0010ffff'))
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    def replace_all(self, data):
        """Compatibility for offline maintenance/tests; never a normal turn path."""
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('DELETE FROM entries')
                conn.executemany('INSERT INTO entries VALUES (?, ?)', [(str(k), json.dumps(v)) for k, v in data.items()])
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
