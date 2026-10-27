"""SQLite state for agent review (``<home>/agent_review/agent_review.sqlite3``).

One open review per (repo, file): the baseline the user has agreed to, the
last content the agent produced (to tell later user edits apart), and the
turns that touched it. Rows survive restarts; a review closes (row deleted)
when baseline equals disk.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.runtime_paths import runtime_state_path

ABSENT = object()  # settled() miss: distinct from an absent (None) file


def default_path() -> Path:
    return runtime_state_path("agent_review", "agent_review.sqlite3")


def pack(text: Optional[str]) -> Optional[bytes]:
    return None if text is None else zlib.compress(text.encode("utf-8", "surrogateescape"))


def unpack(blob: Optional[bytes]) -> Optional[str]:
    return None if blob is None else zlib.decompress(blob).decode("utf-8", "surrogateescape")


class ReviewStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reviews(
                  repo_root TEXT NOT NULL, rel_path TEXT NOT NULL,
                  baseline BLOB, last_agent BLOB,
                  first_query_id TEXT, last_query_id TEXT,
                  chat_session_id TEXT, agent_id TEXT,
                  mixed INTEGER NOT NULL DEFAULT 0,
                  opened_at REAL NOT NULL, updated_at REAL NOT NULL,
                  PRIMARY KEY(repo_root, rel_path));
                CREATE TABLE IF NOT EXISTS review_turns(
                  repo_root TEXT NOT NULL, rel_path TEXT NOT NULL, query_id TEXT NOT NULL,
                  ts REAL NOT NULL, PRIMARY KEY(repo_root, rel_path, query_id));
                CREATE TABLE IF NOT EXISTS turns(
                  query_id TEXT PRIMARY KEY, repo_root TEXT NOT NULL, start_tree TEXT NOT NULL,
                  skipped TEXT NOT NULL DEFAULT '{}', chat_session_id TEXT, agent_id TEXT, model TEXT,
                  status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL);
                CREATE TABLE IF NOT EXISTS turn_files(
                  query_id TEXT NOT NULL, rel_path TEXT NOT NULL, settled BLOB,
                  PRIMARY KEY(query_id, rel_path));
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                """
            )
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Snapshot reads (WAL): never takes the write lock."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ── revision ────────────────────────────────────────────────────
    def revision(self) -> int:
        # Polled every second by the stream: a plain read, no write lock.
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else 0

    @staticmethod
    def bump(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO meta(key,value) VALUES('revision',1) "
                     "ON CONFLICT(key) DO UPDATE SET value=value+1")

    # ── turns ───────────────────────────────────────────────────────
    def add_turn(self, conn, *, query_id: str, repo_root: str, start_tree: str, skipped: Dict[str, str],
                 chat_session_id: Optional[str], agent_id: Optional[str], model: Optional[str]) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO turns(query_id,repo_root,start_tree,skipped,chat_session_id,agent_id,model,status,started_at)"
            " VALUES(?,?,?,?,?,?,?,'running',?)",
            (query_id, repo_root, start_tree, json.dumps(skipped or {}), chat_session_id, agent_id, model, time.time()))
        # Finished turns no open review points at are history, not state.
        conn.execute(
            "DELETE FROM turns WHERE status!='running' AND finished_at<? AND query_id NOT IN"
            " (SELECT query_id FROM review_turns)", (time.time() - 7 * 86400,))

    def turn(self, conn, query_id: str) -> Optional[Dict[str, Any]]:
        row = conn.execute("SELECT * FROM turns WHERE query_id=?", (str(query_id or ""),)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["skipped"] = json.loads(out.get("skipped") or "{}")
        return out

    def finish_turn(self, conn, query_id: str) -> None:
        conn.execute("UPDATE turns SET status='finished', finished_at=? WHERE query_id=?", (time.time(), query_id))
        conn.execute("DELETE FROM turn_files WHERE query_id=?", (query_id,))

    def settled(self, conn, query_id: str, rel_path: str):
        row = conn.execute("SELECT settled FROM turn_files WHERE query_id=? AND rel_path=?",
                           (query_id, rel_path)).fetchone()
        return ABSENT if row is None else unpack(row["settled"])

    def set_settled(self, conn, query_id: str, rel_path: str, text: Optional[str]) -> None:
        conn.execute("INSERT OR REPLACE INTO turn_files(query_id,rel_path,settled) VALUES(?,?,?)",
                     (query_id, rel_path, pack(text)))

    # ── reviews ─────────────────────────────────────────────────────
    @staticmethod
    def _review(row) -> Dict[str, Any]:
        out = dict(row)
        out["baseline"] = unpack(row["baseline"])
        out["last_agent"] = unpack(row["last_agent"])
        out["mixed"] = bool(row["mixed"])
        return out

    def review(self, conn, repo_root: str, rel_path: str) -> Optional[Dict[str, Any]]:
        row = conn.execute("SELECT * FROM reviews WHERE repo_root=? AND rel_path=?", (repo_root, rel_path)).fetchone()
        return self._review(row) if row else None

    def reviews(self, conn, repo_root: str) -> List[Dict[str, Any]]:
        rows = conn.execute("SELECT * FROM reviews WHERE repo_root=? ORDER BY rel_path", (repo_root,)).fetchall()
        return [self._review(r) for r in rows]

    def turns_for(self, conn, repo_root: str, rel_path: str) -> List[str]:
        rows = conn.execute("SELECT query_id FROM review_turns WHERE repo_root=? AND rel_path=? ORDER BY ts",
                            (repo_root, rel_path)).fetchall()
        return [r["query_id"] for r in rows]

    def open_review(self, conn, *, repo_root: str, rel_path: str, baseline: Optional[str], last_agent: Optional[str],
                    query_id: str, chat_session_id: Optional[str], agent_id: Optional[str]) -> None:
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO reviews(repo_root,rel_path,baseline,last_agent,first_query_id,last_query_id,"
            "chat_session_id,agent_id,mixed,opened_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,?,?)",
            (repo_root, rel_path, pack(baseline), pack(last_agent), query_id, query_id,
             chat_session_id, agent_id, now, now))
        self.add_review_turn(conn, repo_root, rel_path, query_id)

    def add_review_turn(self, conn, repo_root: str, rel_path: str, query_id: str) -> None:
        conn.execute("INSERT OR IGNORE INTO review_turns(repo_root,rel_path,query_id,ts) VALUES(?,?,?,?)",
                     (repo_root, rel_path, query_id, time.time()))

    def update_review(self, conn, repo_root: str, rel_path: str, **fields: Any) -> None:
        cols, args = [], []
        for key, value in fields.items():
            if key in ("baseline", "last_agent"):
                value = pack(value)
            elif key == "mixed":
                value = int(bool(value))
            cols.append(f"{key}=?")
            args.append(value)
        cols.append("updated_at=?")
        args.append(time.time())
        conn.execute(f"UPDATE reviews SET {', '.join(cols)} WHERE repo_root=? AND rel_path=?",
                     (*args, repo_root, rel_path))

    def close_review(self, conn, repo_root: str, rel_path: str) -> None:
        conn.execute("DELETE FROM reviews WHERE repo_root=? AND rel_path=?", (repo_root, rel_path))
        conn.execute("DELETE FROM review_turns WHERE repo_root=? AND rel_path=?", (repo_root, rel_path))

