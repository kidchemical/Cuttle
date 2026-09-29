"""Durable, local-only outcome records for CuttleRouter."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.agent_router.types import ExecutionTarget, RoutingDecision


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "router_outcomes.db"


def database_path() -> Path:
    override = os.getenv("CUTTLE_ROUTER_DB", "").strip()
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS router_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at REAL NOT NULL,
            decision_id TEXT NOT NULL,
            attempt_index INTEGER NOT NULL,
            session_id TEXT,
            project_path TEXT,
            task_type TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            strategy TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            target_model TEXT NOT NULL,
            source TEXT NOT NULL,
            success INTEGER NOT NULL,
            failure_kind TEXT NOT NULL,
            reason TEXT,
            latency_ms REAL,
            query_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cached_tokens INTEGER,
            cost REAL,
            user_feedback TEXT,
            UNIQUE(decision_id, attempt_index)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_router_outcomes_target_time "
        "ON router_outcomes(target_agent, target_model, recorded_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_router_outcomes_session "
        "ON router_outcomes(session_id, recorded_at)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(router_outcomes)")}
    if "reasoning_effort" not in columns:
        conn.execute("ALTER TABLE router_outcomes ADD COLUMN reasoning_effort TEXT")
    if "cached_tokens" not in columns:
        conn.execute("ALTER TABLE router_outcomes ADD COLUMN cached_tokens INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_router_outcomes_query "
        "ON router_outcomes(query_id)"
    )
    return conn


def _number(value: Any, cast):
    try:
        return cast(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_CACHED_KEYS = (
    "cached_tokens",
    "cached_input_tokens",
    "cache_read_tokens",
    "cache_read_input_tokens",
    "cachedInputTokens",
    "cacheReadTokens",
)


def _cached_tokens(usage: Dict[str, Any]) -> Optional[int]:
    for key in _CACHED_KEYS:
        value = _number(usage.get(key), int)
        if value is not None:
            return value
    return None


def _usage(result: Dict[str, Any]) -> Dict[str, Any]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)
    return {
        "prompt_tokens": _number(prompt, int),
        "completion_tokens": _number(completion, int),
        "total_tokens": _number(total, int),
        "cached_tokens": _cached_tokens(usage),
        "cost": _number(result.get("cost", usage.get("cost")), float),
    }


def record_attempt(
    *,
    decision: RoutingDecision,
    attempt_index: int,
    target: ExecutionTarget,
    source: str,
    failure_kind: str,
    reason: str,
    latency_ms: float,
    result: Optional[Dict[str, Any]] = None,
    session_id: Any = None,
    project_path: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Store one execution attempt. Telemetry failure never fails the user turn."""
    payload = dict(result or {})
    usage = _usage(payload)
    try:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO router_outcomes (
                    recorded_at, decision_id, attempt_index, session_id, project_path,
                    task_type, difficulty, strategy, target_agent, target_model,
                    source, success, failure_kind, reason, latency_ms, query_id,
                    prompt_tokens, completion_tokens, total_tokens, cached_tokens, cost, user_feedback
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    time.time(),
                    decision.decision_id,
                    int(attempt_index),
                    None if session_id is None else str(session_id),
                    str(project_path or ""),
                    decision.task_type,
                    decision.difficulty,
                    decision.strategy or "direct",
                    target.agent,
                    target.model,
                    source,
                    1 if failure_kind == "none" else 0,
                    failure_kind,
                    str(reason or "")[:500],
                    max(0.0, float(latency_ms)),
                    str(payload.get("query_id") or "") or None,
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                    usage["cached_tokens"],
                    usage["cost"],
                ),
            )
        return True
    except Exception as exc:
        print(f"[AGENT-ROUTER] event=outcome_store_failed error={str(exc)[:240]!r}", flush=True)
        return False


def record_turn(
    *,
    decision_id: str,
    target_agent: str,
    target_model: str,
    source: str,
    failure_kind: str,
    reason: str = "",
    latency_ms: Optional[float] = None,
    result: Optional[Dict[str, Any]] = None,
    session_id: Any = None,
    project_path: Optional[str] = None,
    query_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    recorded_at: Optional[float] = None,
    task_type: str = "other",
    difficulty: str = "medium",
    replace: bool = True,
    db_path: Optional[Path] = None,
) -> bool:
    """Store one non-router turn (pinned agent or history backfill).

    ``replace=False`` keeps an existing row for ``decision_id`` untouched, which
    makes history backfills idempotent.
    """
    payload = dict(result or {})
    usage = _usage(payload)
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    try:
        with _connect(db_path) as conn:
            conn.execute(
                f"""
                {verb} INTO router_outcomes (
                    recorded_at, decision_id, attempt_index, session_id, project_path,
                    task_type, difficulty, strategy, target_agent, target_model,
                    source, success, failure_kind, reason, latency_ms, query_id,
                    prompt_tokens, completion_tokens, total_tokens, cached_tokens, cost, user_feedback,
                    reasoning_effort
                ) VALUES (?, ?, 0, ?, ?, ?, ?, 'direct', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    float(recorded_at) if recorded_at is not None else time.time(),
                    str(decision_id),
                    None if session_id is None else str(session_id),
                    str(project_path or ""),
                    task_type,
                    difficulty,
                    str(target_agent or "unknown"),
                    str(target_model or ""),
                    source,
                    1 if failure_kind == "none" else 0,
                    failure_kind,
                    str(reason or "")[:500],
                    None if latency_ms is None else max(0.0, float(latency_ms)),
                    str(query_id or payload.get("query_id") or "") or None,
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                    usage["cached_tokens"],
                    usage["cost"],
                    (str(reasoning_effort).strip().lower() or None) if reasoning_effort else None,
                ),
            )
        return True
    except Exception as exc:
        print(f"[AGENT-ROUTER] event=turn_store_failed error={str(exc)[:240]!r}", flush=True)
        return False


def known_query_ids(*, db_path: Optional[Path] = None) -> set:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT query_id FROM router_outcomes WHERE query_id IS NOT NULL"
        ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def all_outcomes(
    *,
    since: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Every attempt (optionally since a unix time), newest first."""
    sql = "SELECT * FROM router_outcomes"
    params: List[Any] = []
    if since is not None:
        sql += " WHERE recorded_at >= ?"
        params.append(float(since))
    sql += " ORDER BY recorded_at DESC, attempt_index DESC"
    with _connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def set_feedback_for_query(
    feedback: Optional[str],
    query_id: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Attach good/bad (or clear with None) to the final attempt that produced ``query_id``."""
    value = str(feedback or "").strip().lower() or None
    if value not in (None, "good", "bad"):
        raise ValueError("feedback must be good, bad, or empty")
    qid = str(query_id or "").strip()
    if not qid:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT decision_id, attempt_index FROM router_outcomes
            WHERE query_id = ?
            ORDER BY recorded_at DESC, attempt_index DESC
            LIMIT 1
            """,
            (qid,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE router_outcomes SET user_feedback = ? WHERE decision_id = ? AND attempt_index = ?",
            (value, row["decision_id"], row["attempt_index"]),
        )
        return str(row["decision_id"])


def list_outcomes(
    *,
    decision_id: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read recent outcomes for tests and future metrics commands."""
    sql = "SELECT * FROM router_outcomes"
    params: List[Any] = []
    if decision_id:
        sql += " WHERE decision_id = ?"
        params.append(decision_id)
    sql += " ORDER BY recorded_at DESC, attempt_index DESC LIMIT ?"
    params.append(max(1, min(int(limit), 5000)))
    with _connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def outcomes_since(
    *,
    seconds: float,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """All attempts recorded within the last ``seconds`` (drift windows)."""
    cutoff = time.time() - max(1.0, float(seconds))
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM router_outcomes
            WHERE recorded_at >= ?
            ORDER BY recorded_at ASC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]


def recent_session_decision_ids(
    session_id: Any,
    *,
    limit: int = 5,
    db_path: Optional[Path] = None,
) -> List[str]:
    """Most recent distinct decision ids for a session (newest first)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT decision_id, MAX(recorded_at) AS last_at
            FROM router_outcomes
            WHERE session_id = ?
            GROUP BY decision_id
            ORDER BY last_at DESC
            LIMIT ?
            """,
            (str(session_id), max(1, int(limit))),
        ).fetchall()
        return [str(r["decision_id"]) for r in rows]


def set_feedback(
    feedback: str,
    *,
    session_id: Any = None,
    decision_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Attach good/bad feedback to the final attempt of a decision."""
    value = str(feedback or "").strip().lower()
    if value not in {"good", "bad"}:
        raise ValueError("feedback must be good or bad")
    with _connect(db_path) as conn:
        chosen = str(decision_id or "").strip()
        if not chosen:
            if session_id is None:
                return None
            row = conn.execute(
                """
                SELECT decision_id
                FROM router_outcomes
                WHERE session_id = ?
                ORDER BY recorded_at DESC, attempt_index DESC
                LIMIT 1
                """,
                (str(session_id),),
            ).fetchone()
            chosen = str(row["decision_id"]) if row else ""
        if not chosen:
            return None
        cursor = conn.execute(
            """
            UPDATE router_outcomes
            SET user_feedback = ?
            WHERE decision_id = ?
              AND attempt_index = (
                SELECT MAX(attempt_index)
                FROM router_outcomes
                WHERE decision_id = ?
              )
            """,
            (value, chosen, chosen),
        )
        return chosen if cursor.rowcount else None


def metrics_summary(
    *,
    days: int = 7,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Aggregate recent attempts by agent/model for chat and future charts."""
    cutoff = time.time() - max(1, int(days)) * 86400
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                target_agent,
                target_model,
                COUNT(*) AS attempts,
                SUM(success) AS successes,
                SUM(CASE WHEN failure_kind = 'task' THEN 1 ELSE 0 END) AS task_failures,
                SUM(CASE WHEN failure_kind = 'transport' THEN 1 ELSE 0 END) AS transport_failures,
                SUM(CASE WHEN failure_kind = 'cancelled' THEN 1 ELSE 0 END) AS cancellations,
                SUM(CASE WHEN user_feedback = 'good' THEN 1 ELSE 0 END) AS good_feedback,
                SUM(CASE WHEN user_feedback = 'bad' THEN 1 ELSE 0 END) AS bad_feedback,
                ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
                SUM(total_tokens) AS total_tokens,
                SUM(cost) AS total_cost
            FROM router_outcomes
            WHERE recorded_at >= ?
            GROUP BY target_agent, target_model
            ORDER BY attempts DESC, target_agent, target_model
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]
