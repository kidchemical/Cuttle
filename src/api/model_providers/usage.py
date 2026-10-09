"""Usage ledger for direct model-provider API calls.

Harness turns live in ``api.agent_router.outcomes``; every Cuttle-owned call
that spends API budget *outside* a harness turn lands here instead:
background-helper text, chat titles, the router brain, vision prepass, TTS
narration, STT, and Jev judgments (which bill OpenRouter when no TypeSafe key
is set). ``api.dashboards.usage`` merges these rows so the Cuttle Usage
dashboard tracks the full bill.

Store: ``<home>/db/provider_calls.db`` (``CUTTLE_PROVIDER_CALLS_DB``
overrides; tests redirect it with ``CUTTLE_HOME``). Recording never raises:
a ledger write must not break the call it measures.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import data_db_dir


def database_path() -> Path:
    override = os.getenv("CUTTLE_PROVIDER_CALLS_DB", "").strip()
    return Path(override).expanduser() if override else data_db_dir() / "provider_calls.db"


@contextmanager
def _session(db_path: Optional[Path] = None):
    conn = _connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at REAL NOT NULL,
            provider TEXT NOT NULL,
            operation TEXT NOT NULL,
            model TEXT NOT NULL,
            task TEXT NOT NULL DEFAULT '',
            session_id TEXT,
            success INTEGER NOT NULL,
            failure_kind TEXT NOT NULL DEFAULT 'none',
            latency_ms REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provider_calls_time "
        "ON provider_calls(provider, model, recorded_at)"
    )
    return conn


def _number(value: Any):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def record_call(
    *,
    provider: str,
    operation: str,
    model: str = "",
    task: str = "",
    session_id: Any = None,
    success: bool = True,
    failure_kind: str = "none",
    latency_ms: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
    cost: Optional[float] = None,
    recorded_at: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Append one provider-call row. Never raises; returns False when skipped."""
    if not str(provider or "").strip() or not str(operation or "").strip():
        return False
    use = dict(usage or {})
    prompt = _number(use.get("prompt_tokens", use.get("input_tokens")))
    completion = _number(use.get("completion_tokens", use.get("output_tokens")))
    total = _number(use.get("total_tokens"))
    if total is None and (prompt or completion):
        total = (prompt or 0) + (completion or 0)
    try:
        cost_f = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_f = None
    try:
        with _session(db_path) as conn:
            conn.execute(
                """
                INSERT INTO provider_calls (
                    recorded_at, provider, operation, model, task, session_id,
                    success, failure_kind, latency_ms,
                    prompt_tokens, completion_tokens, total_tokens, cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    float(recorded_at) if recorded_at is not None else time.time(),
                    str(provider).strip().lower(),
                    str(operation).strip(),
                    str(model or "").strip(),
                    str(task or "").strip(),
                    None if session_id is None else str(session_id),
                    1 if success else 0,
                    str(failure_kind or "none"),
                    None if latency_ms is None else max(0.0, float(latency_ms)),
                    prompt,
                    completion,
                    total,
                    cost_f if cost_f is not None and cost_f >= 0 else None,
                ),
            )
    except Exception:
        return False
    return True


def all_calls(
    *,
    since: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Every recorded call (optionally since a unix time), newest first."""
    sql = "SELECT * FROM provider_calls"
    params: List[Any] = []
    if since is not None:
        sql += " WHERE recorded_at >= ?"
        params.append(float(since))
    sql += " ORDER BY recorded_at DESC, id DESC"
    with _session(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
