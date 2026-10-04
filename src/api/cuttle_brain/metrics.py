"""Per-turn context metrics: what Cuttle sent, and how full the agent's window is.

One row per harness turn (kernel), so context growth can be charted over time:
briefing mode/size by layer, handoff size, and the agent's reported window
fill. Owned by Cuttle Brain; the Context dashboard reads it
(``api.dashboards.context``). Failures never affect a turn.

Store: ``src/data/brain/context_metrics.db`` (``CUTTLE_CONTEXT_METRICS_DB``
overrides; tests isolate it).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.runtime_paths import runtime_state_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    chat_session_id TEXT,
    agent_id TEXT,
    model TEXT,
    project_path TEXT,
    mode TEXT,
    full_reason TEXT,
    prompt_chars INTEGER,
    envelope_chars INTEGER,
    delta_chars INTEGER,
    handoff_chars INTEGER,
    handoff_messages INTEGER,
    layer_chars TEXT,
    context_tokens INTEGER,
    context_limit INTEGER,
    compacted INTEGER DEFAULT 0,
    success INTEGER,
    query_id TEXT UNIQUE,
    source TEXT DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS idx_context_turns_ts ON context_turns(ts);
CREATE INDEX IF NOT EXISTS idx_context_turns_chat ON context_turns(chat_session_id, ts);
"""

_COLUMNS = (
    "ts", "chat_session_id", "agent_id", "model", "project_path", "mode",
    "full_reason", "prompt_chars", "envelope_chars", "delta_chars",
    "handoff_chars", "handoff_messages", "layer_chars", "context_tokens",
    "context_limit", "compacted", "success", "query_id", "source",
)


def database_path() -> Path:
    override = (os.environ.get("CUTTLE_CONTEXT_METRICS_DB") or "").strip()
    if override:
        return Path(override).expanduser()
    root = Path(__file__).resolve().parents[3]
    return runtime_state_path("brain", "context_metrics.db", project_root=root)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _row(**fields: Any) -> Dict[str, Any]:
    layers = fields.get("layer_chars")
    return {
        "ts": float(fields.get("ts") or time.time()),
        "chat_session_id": (str(fields["chat_session_id"]) if fields.get("chat_session_id") is not None else None),
        "agent_id": fields.get("agent_id"),
        "model": fields.get("model") or None,
        "project_path": fields.get("project_path") or None,
        "mode": fields.get("mode") or None,
        "full_reason": fields.get("full_reason") or None,
        "prompt_chars": _int(fields.get("prompt_chars")),
        "envelope_chars": _int(fields.get("envelope_chars")),
        "delta_chars": _int(fields.get("delta_chars")),
        "handoff_chars": _int(fields.get("handoff_chars")),
        "handoff_messages": _int(fields.get("handoff_messages")),
        "layer_chars": json.dumps(layers) if isinstance(layers, dict) and layers else None,
        "context_tokens": _int(fields.get("context_tokens")) or None,
        "context_limit": _int(fields.get("context_limit")) or None,
        "compacted": 1 if fields.get("compacted") else 0,
        "success": None if fields.get("success") is None else (1 if fields.get("success") else 0),
        "query_id": fields.get("query_id") or None,
        "source": fields.get("source") or "live",
    }


def _insert(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    cols = ", ".join(_COLUMNS)
    marks = ", ".join("?" for _ in _COLUMNS)
    cur = conn.executemany(
        f"INSERT OR IGNORE INTO context_turns ({cols}) VALUES ({marks})",
        [tuple(r[c] for c in _COLUMNS) for r in rows],
    )
    conn.commit()
    return cur.rowcount or 0


def record_turn(**fields: Any) -> None:
    """Append one turn. Never raises."""
    try:
        conn = connect()
        try:
            _insert(conn, [_row(**fields)])
        finally:
            conn.close()
    except Exception as exc:
        print(f"[cuttle_brain.metrics] record failed: {exc}", flush=True)


def fetch_turns(*, since_ts: float, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM context_turns WHERE ts >= ? ORDER BY ts ASC", (float(since_ts),)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["layer_chars"] = json.loads(d["layer_chars"]) if d.get("layer_chars") else {}
        except ValueError:
            d["layer_chars"] = {}
        out.append(d)
    return out


_TIMESTAMPED = re.compile(r"_\d{8}_\d{6}\.json$")


def _is_test_turn(harness: Dict[str, Any]) -> bool:
    """Pytest turns that leaked into the live logs before tests were isolated."""
    from api.cuttle_brain.state import _TEST_PATH_MARKERS

    cwd = str(harness.get("cwd") or "")
    return any(marker in cwd for marker in _TEST_PATH_MARKERS)


def backfill_from_query_logs(logs_dir: Optional[Path] = None) -> Dict[str, int]:
    """Import historical turns from query-log sidecars (brain + harness blocks).

    Layer sizes and window fill were not logged before this table existed, so
    backfilled rows carry briefing mode/size only. Idempotent by query id.
    """
    from datetime import datetime

    if logs_dir is None:
        from api.query_events import logs_dir as _logs_dir

        logs_dir = _logs_dir()
    rows: List[Dict[str, Any]] = []
    scanned = 0
    for path in Path(logs_dir).glob("query_data_*.json"):
        if _TIMESTAMPED.search(path.name):
            continue
        scanned += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        brain = data.get("brain") or {}
        harness = data.get("harness") or {}
        if not brain or not harness:
            continue
        if _is_test_turn(harness):
            continue
        try:
            ts = datetime.fromisoformat(str(data.get("timestamp"))).timestamp()
        except (TypeError, ValueError):
            ts = path.stat().st_mtime
        rows.append(_row(
            ts=ts,
            chat_session_id=harness.get("chat_session_id"),
            agent_id=harness.get("agent_id"),
            model=harness.get("model"),
            project_path=harness.get("cwd"),
            mode=brain.get("mode"),
            full_reason=brain.get("full_reason"),
            prompt_chars=brain.get("prompt_chars"),
            envelope_chars=brain.get("envelope_chars"),
            delta_chars=brain.get("delta_chars"),
            handoff_messages=brain.get("handoff_messages"),
            success=data.get("success"),
            query_id=data.get("query_id") or path.stem[len("query_data_"):],
            source="backfill",
        ))
    conn = connect()
    try:
        inserted = _insert(conn, rows)
    finally:
        conn.close()
    return {"scanned": scanned, "turns": len(rows), "inserted": max(inserted, 0)}
