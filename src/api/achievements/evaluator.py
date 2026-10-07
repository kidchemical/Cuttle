"""Metric evaluation: turn telemetry → numbers the catalog thresholds.

Reads two existing stores, never writes:

* ``router_outcomes.db`` (``api.agent_router.outcomes``) — per-turn tokens,
  latency, target harness/model, success, fallback attempts, ``recorded_at``.
* ``cuttle_auth.db`` (``api.auth_db``) — chats, user messages, and the
  sub-agent child session ids to **exclude**.

Sub-agent exclusion is a deliberate part of the contract: a child chat's turns
roll up into the parent chat's history, so counting them would let a fan-out
earn the single-message achievements. Every turn-based metric therefore filters
``session_id NOT IN (child session ids)``.

One evaluation = one ``all_outcomes``-shaped read + a handful of aggregates.
No per-achievement queries.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

# 10 minutes, in ms — the "long turn" rung used by several achievements.
LONG_TURN_MS = 10 * 60_000

# Local-time windows that the clockwork achievements look at.
NIGHT_HOURS = (0, 1, 2, 3, 4)
DAWN_HOURS = (5, 6)

METRIC_NAMES = (
    "turns_total",
    "turns_completed",
    "user_messages",
    "sessions_with_turns",
    "lifetime_total_tokens",
    "lifetime_input_tokens",
    "lifetime_output_tokens",
    "lifetime_cached_tokens",
    "max_single_turn_tokens",
    "max_single_turn_input_tokens",
    "max_turn_latency_ms",
    "agent_hours",
    "long_turns_10min",
    "cancelled_turns",
    "distinct_harnesses",
    "distinct_models",
    "escalations",
    "subagent_children",
    "night_turns",
    "dawn_turns",
    "weekend_turns",
    "active_hours_distinct",
    "streak_days",
    "active_days",
    "distinct_projects",
    "lifetime_cost",
)


def _empty_metrics() -> Dict[str, float]:
    return {name: 0.0 for name in METRIC_NAMES}


def _outcomes_db_path() -> Path:
    from api.agent_router.outcomes import database_path

    return Path(database_path())


def _auth_db_path() -> Path:
    from api.auth_db import DB_PATH

    return Path(DB_PATH)


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    """Read-only connect. Returns None when the store does not exist yet."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# auth.db side metrics
# ---------------------------------------------------------------------------
def _subagent_session_ids(auth_db_path: Optional[Path]) -> Set[str]:
    conn = _connect_ro(Path(auth_db_path or _auth_db_path()))
    if conn is None:
        return set()
    try:
        rows = conn.execute(
            "SELECT id FROM chat_sessions WHERE parent_session_id IS NOT NULL"
        ).fetchall()
        return {str(r[0]) for r in rows}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def _auth_metrics(auth_db_path: Optional[Path]) -> Dict[str, float]:
    out = _empty_metrics()
    out["subagent_children"] = 0.0
    conn = _connect_ro(Path(auth_db_path or _auth_db_path()))
    if conn is None:
        return out
    try:
        try:
            out["user_messages"] = float(
                conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE role = 'user'"
                ).fetchone()[0]
            )
        except sqlite3.Error:
            pass
        try:
            out["subagent_children"] = float(
                conn.execute("SELECT COUNT(*) FROM subagent_children").fetchone()[0]
            )
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# router_outcomes side metrics
# ---------------------------------------------------------------------------
def _not_child_filter(exclude_sessions: Iterable[str]) -> str:
    """SQL fragment excluding sub-agent child sessions.

    ``session_id`` is a TEXT column holding numeric ids, so cast both sides.
    """
    ids = sorted({str(s) for s in exclude_sessions if str(s) != ""})
    if not ids:
        return ""
    joined = ", ".join("'%s'" % s.replace("'", "''") for s in ids)
    return f" AND CAST(session_id AS TEXT) NOT IN ({joined})"


def _distinct_days(recorded_at: Iterable[float]) -> List[str]:
    import datetime as _dt

    days = set()
    for ts in recorded_at:
        try:
            days.add(
                _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
            )
        except (TypeError, ValueError, OSError):
            continue
    return sorted(days)


def _max_streak(days: List[str]) -> int:
    """Longest run of consecutive YYYY-MM-DD entries."""
    if not days:
        return 0
    import datetime as _dt

    parsed = []
    for d in days:
        try:
            parsed.append(_dt.date.fromisoformat(d))
        except ValueError:
            continue
    if not parsed:
        return 0
    best = run = 1
    for prev, cur in zip(parsed, parsed[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    return best


def _outcomes_metrics(
    outcomes_db_path: Optional[Path],
    exclude_sessions: Iterable[str],
) -> Dict[str, float]:
    out = _empty_metrics()
    conn = _connect_ro(Path(outcomes_db_path or _outcomes_db_path()))
    if conn is None:
        return out
    where_extra = _not_child_filter(exclude_sessions)
    base = f"FROM router_outcomes WHERE 1=1{where_extra}"
    try:
        def scalar(sql: str):
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.Error:
                return None
            return None if row is None else row[0]

        out["turns_total"] = float(scalar(f"SELECT COUNT(DISTINCT decision_id) {base}") or 0)
        out["turns_completed"] = float(
            scalar(f"SELECT COUNT(DISTINCT decision_id) {base} AND success = 1") or 0
        )
        out["sessions_with_turns"] = float(
            scalar(f"SELECT COUNT(DISTINCT session_id) {base}") or 0
        )
        out["lifetime_total_tokens"] = float(
            scalar(f"SELECT SUM(COALESCE(total_tokens,0)) {base}") or 0
        )
        out["lifetime_input_tokens"] = float(
            scalar(f"SELECT SUM(COALESCE(prompt_tokens,0)) {base}") or 0
        )
        out["lifetime_output_tokens"] = float(
            scalar(f"SELECT SUM(COALESCE(completion_tokens,0)) {base}") or 0
        )
        out["lifetime_cached_tokens"] = float(
            scalar(f"SELECT SUM(COALESCE(cached_tokens,0)) {base}") or 0
        )
        out["lifetime_cost"] = float(scalar(f"SELECT SUM(COALESCE(cost,0)) {base}") or 0)

        # --- single-message ladders (a "message" == one routing decision) -----
        def max_group_sum(column: str) -> float:
            sql = (
                "SELECT MAX(total) FROM ("
                f"  SELECT SUM(COALESCE({column},0)) AS total {base} GROUP BY decision_id"
                ")"
            )
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.Error:
                return 0.0
            return float(row[0] or 0.0) if row else 0.0

        out["max_single_turn_tokens"] = max_group_sum("total_tokens")
        out["max_single_turn_input_tokens"] = max_group_sum("prompt_tokens")

        out["max_turn_latency_ms"] = float(
            scalar(f"SELECT MAX(COALESCE(latency_ms,0)) {base}") or 0
        )
        out["agent_hours"] = float(
            scalar(f"SELECT SUM(COALESCE(latency_ms,0)) {base}") or 0
        ) / 3_600_000.0
        out["long_turns_10min"] = float(
            scalar(
                f"SELECT COUNT(*) FROM (SELECT decision_id {base} GROUP BY decision_id "
                f"HAVING MAX(COALESCE(latency_ms,0)) >= {LONG_TURN_MS})"
            )
            or 0
        )
        out["cancelled_turns"] = float(
            scalar(f"SELECT COUNT(*) FROM (SELECT decision_id {base} AND failure_kind = 'cancelled' GROUP BY decision_id)")
            or 0
        )
        out["escalations"] = float(
            scalar(f"SELECT COUNT(*) {base} AND attempt_index > 0") or 0
        )
        out["distinct_harnesses"] = float(
            scalar(f"SELECT COUNT(DISTINCT target_agent) {base}") or 0
        )
        out["distinct_models"] = float(
            scalar(f"SELECT COUNT(DISTINCT target_model) {base}") or 0
        )
        out["distinct_projects"] = float(
            scalar(
                f"SELECT COUNT(DISTINCT project_path) {base} "
                "AND project_path IS NOT NULL AND project_path <> ''"
            )
            or 0
        )

        # --- local-time buckets ---------------------------------------------
        try:
            rows = conn.execute(
                "SELECT recorded_at FROM router_outcomes WHERE 1=1" + where_extra
            ).fetchall()
        except sqlite3.Error:
            rows = []
        stamps = [r[0] for r in rows if r[0] is not None]
        out["night_turns"] = _count_hours(stamps, NIGHT_HOURS)
        out["dawn_turns"] = _count_hours(stamps, DAWN_HOURS)
        out["weekend_turns"] = _count_weekends(stamps)
        out["active_hours_distinct"] = float(len(_hours(stamps)))
        days = _distinct_days(stamps)
        out["active_days"] = float(len(days))
        out["streak_days"] = float(_max_streak(days))
    finally:
        conn.close()
    return out


def _hours(stamps: Iterable[float]) -> List[int]:
    import datetime as _dt

    seen = set()
    for ts in stamps:
        try:
            seen.add(_dt.datetime.fromtimestamp(float(ts)).hour)
        except (TypeError, ValueError, OSError):
            continue
    return sorted(seen)


def _count_hours(stamps: Iterable[float], hours: Iterable[int]) -> float:
    wanted = set(hours)
    return float(sum(1 for h in _hours(stamps) if h in wanted))


def _count_weekends(stamps: Iterable[float]) -> float:
    import datetime as _dt

    count = 0
    for ts in stamps:
        try:
            if _dt.datetime.fromtimestamp(float(ts)).weekday() >= 5:
                count += 1
        except (TypeError, ValueError, OSError):
            continue
    return float(count)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
def snapshot(
    *,
    outcomes_db: Optional[Path] = None,
    auth_db: Optional[Path] = None,
) -> Dict[str, float]:
    """One read-only pass over both stores → every catalog metric.

    Always returns the full metric key set (zeros when a store is missing), so
    the catalog can never KeyError and a fresh install simply has no progress.
    """
    started = time.perf_counter()
    exclude = _subagent_session_ids(auth_db)
    metrics = _outcomes_metrics(outcomes_db, exclude)
    auth_side = _auth_metrics(auth_db)
    metrics.update({k: v for k, v in auth_side.items() if v})
    metrics["_snapshot_ms"] = (time.perf_counter() - started) * 1000.0
    return metrics