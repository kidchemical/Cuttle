"""Cuttle Usage dashboard: cost / tokens / turns over time, grouped by model or harness."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

USAGE_ID = "cuttle-usage"
RANGES = [
    {"id": "1d", "days": 1, "label": "1d"},
    {"id": "3d", "days": 3, "label": "3d"},
    {"id": "7d", "days": 7, "label": "7d"},
    {"id": "14d", "days": 14, "label": "14d"},
    {"id": "30d", "days": 30, "label": "30d"},
    {"id": "90d", "days": 90, "label": "90d"},
    {"id": "1y", "days": 365, "label": "1y"},
    {"id": "all", "days": 0, "label": "All time"},
]
GROUP_BY = [
    {"id": "model", "name": "Model"},
    {"id": "harness", "name": "Harness"},
    {"id": "none", "name": "None (totals)"},
]
INTERVALS = [
    {"id": "auto", "name": "Auto"},
    {"id": "day", "name": "Day"},
    {"id": "week", "name": "Week"},
    {"id": "month", "name": "Month"},
]
SOURCES = [
    {"id": "all", "name": "All turns"},
    {"id": "pinned", "name": "Pinned agents"},
    {"id": "router", "name": "Agent router"},
]
METRICS = [
    {"id": "cost_usd", "label": "Total cost", "unit": "usd"},
    {"id": "total_tokens", "label": "Total tokens", "unit": "tokens"},
    {"id": "turns", "label": "Turns", "unit": "count"},
    {"id": "input_tokens", "label": "Input tokens", "unit": "tokens"},
    {"id": "output_tokens", "label": "Output tokens", "unit": "tokens"},
    {"id": "agent_hours", "label": "Agent time", "unit": "hours"},
]
MAX_GROUPS = 50
_AMBIGUOUS_MODELS = {"", "default", "auto", "codex"}


def _tz(offset_minutes: Optional[int]) -> tzinfo:
    """``offset_minutes`` uses JS ``getTimezoneOffset`` sign (UTC minus local)."""
    if offset_minutes is None:
        return datetime.now().astimezone().tzinfo or timezone.utc
    return timezone(timedelta(minutes=-int(offset_minutes)))


def _parse_day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _bucket_start(day: date, interval: str) -> date:
    if interval == "week":
        return day - timedelta(days=day.weekday())
    if interval == "month":
        return day.replace(day=1)
    return day


def _next_bucket(day: date, interval: str) -> date:
    if interval == "week":
        return day + timedelta(days=7)
    if interval == "month":
        return date(day.year + (day.month // 12), day.month % 12 + 1, 1)
    return day + timedelta(days=1)


def _auto_interval(span_days: int) -> str:
    if span_days <= 45:
        return "day"
    if span_days <= 210:
        return "week"
    return "month"


def _group_identity(row: Dict[str, Any], group_by: str) -> Tuple[str, str]:
    agent = str(row.get("target_agent") or "unknown").lower()
    if group_by == "harness":
        return agent, agent
    if group_by == "none":
        return "all", "All turns"
    from api.agent_router.pinned_outcomes import split_model_effort

    raw = str(row.get("target_model") or "").strip().lower()
    if raw in _AMBIGUOUS_MODELS:
        name = raw or "default"
        return f"{agent}:{name}", f"{agent} · {name}"
    model, _effort = split_model_effort(raw)
    return model, model


def _row_metrics(row: Dict[str, Any]) -> Dict[str, float]:
    latency = row.get("latency_ms")
    return {
        "cost_usd": float(row.get("cost") or 0.0),
        "total_tokens": float(row.get("total_tokens") or 0),
        "turns": 1.0,
        "input_tokens": float(row.get("prompt_tokens") or 0),
        "output_tokens": float(row.get("completion_tokens") or 0),
        "agent_hours": (float(latency) / 3_600_000.0) if latency is not None else 0.0,
    }


def _empty_totals() -> Dict[str, float]:
    return {m["id"]: 0.0 for m in METRICS}


def cuttle_usage(
    *,
    range_id: str = "30d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    group_by: str = "model",
    interval: str = "auto",
    source_id: str = "all",
    tz_offset_minutes: Optional[int] = None,
    db_path: Optional[Path] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Stacked-column series per time bucket; ``start``/``end`` (YYYY-MM-DD) override ``range_id``."""
    group_by = group_by if group_by in {g["id"] for g in GROUP_BY} else "model"
    interval = interval if interval in {i["id"] for i in INTERVALS} else "auto"
    source_id = source_id if source_id in {s["id"] for s in SOURCES} else "all"
    ranges = {r["id"]: r for r in RANGES}
    tz = _tz(tz_offset_minutes)
    today = datetime.fromtimestamp(now if now is not None else time.time(), tz).date()

    start_day, end_day = _parse_day(start), _parse_day(end)
    if start_day or end_day:
        range_id = "custom"
        start_day = start_day or end_day
        end_day = end_day or today
        if start_day > end_day:
            start_day, end_day = end_day, start_day
    else:
        range_id = range_id if range_id in ranges else "30d"
        days = ranges[range_id]["days"]
        start_day = today - timedelta(days=days - 1) if days else None
        end_day = today

    def _ts(day: date) -> float:
        return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()

    base = {
        "success": True,
        "id": USAGE_ID,
        "title": "Cuttle Usage",
        "dataset": "local",
        "status": "live",
        "available_ranges": RANGES,
        "available_group_by": GROUP_BY,
        "available_intervals": INTERVALS,
        "available_sources": SOURCES,
        "metrics": METRICS,
        "selected_range": range_id,
        "selected_group_by": group_by,
        "selected_interval": interval,
        "selected_source": source_id,
    }
    try:
        from api.agent_router.outcomes import all_outcomes

        rows = all_outcomes(since=_ts(start_day) if start_day else None, db_path=db_path)
    except Exception as exc:
        return {**base, "message": f"Outcomes store unavailable: {str(exc)[:160]}",
                "buckets": [], "groups": [], "totals": _empty_totals(), "stats": {}}

    until = _ts(end_day + timedelta(days=1))
    rows = [r for r in rows if r.get("recorded_at") is not None and float(r["recorded_at"]) < until]
    if source_id == "pinned":
        rows = [r for r in rows if r.get("source") == "pinned"]
    elif source_id == "router":
        rows = [r for r in rows if r.get("source") != "pinned"]

    if start_day is None:
        stamps = [float(r["recorded_at"]) for r in rows]
        start_day = datetime.fromtimestamp(min(stamps), tz).date() if stamps else today
    span_days = (end_day - start_day).days + 1
    bucket_interval = _auto_interval(span_days) if interval == "auto" else interval

    buckets: List[date] = []
    cursor = _bucket_start(start_day, bucket_interval)
    while cursor <= end_day:
        buckets.append(cursor)
        cursor = _next_bucket(cursor, bucket_interval)
    index = {b: i for i, b in enumerate(buckets)}

    groups: Dict[str, Dict[str, Any]] = {}
    totals = _empty_totals()
    for r in rows:
        day = datetime.fromtimestamp(float(r["recorded_at"]), tz).date()
        slot = index.get(_bucket_start(day, bucket_interval))
        if slot is None:
            continue
        key, label = _group_identity(r, group_by)
        g = groups.setdefault(key, {
            "key": key,
            "label": label,
            "totals": _empty_totals(),
            "series": {m["id"]: [0.0] * len(buckets) for m in METRICS},
        })
        for metric, value in _row_metrics(r).items():
            g["series"][metric][slot] += value
            g["totals"][metric] += value
            totals[metric] += value

    ordered = sorted(groups.values(), key=lambda g: (-g["totals"]["turns"], g["label"]))
    if len(ordered) > MAX_GROUPS:
        # Fold only groups that are small on every metric, so a few expensive turns never hide in Other.
        def _peak_share(g: Dict[str, Any]) -> float:
            return max((g["totals"][m] / totals[m]) for m in totals if totals[m] > 0) if any(totals.values()) else 0.0

        by_share = sorted(ordered, key=lambda g: (-_peak_share(g), g["label"]))
        kept_keys = {g["key"] for g in by_share[:MAX_GROUPS - 1]}
        keep = [g for g in ordered if g["key"] in kept_keys]
        rest = sorted((g for g in ordered if g["key"] not in kept_keys),
                      key=lambda g: (-g["totals"]["cost_usd"], -g["totals"]["turns"], g["label"]))
        other = {
            "key": "__other__",
            "label": f"Other ({len(rest)})",
            "totals": _empty_totals(),
            "series": {m["id"]: [0.0] * len(buckets) for m in METRICS},
            "members": [g["label"] for g in rest],
        }
        for g in rest:
            for metric in other["totals"]:
                other["totals"][metric] += g["totals"][metric]
                other["series"][metric] = [a + b for a, b in zip(other["series"][metric], g["series"][metric])]
        ordered = keep + [other]

    return {
        **base,
        "bucket_interval": bucket_interval,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "buckets": [b.isoformat() for b in buckets],
        "groups": ordered,
        "totals": totals,
        "stats": {
            "turns": len(rows),
            "with_cost": sum(1 for r in rows if r.get("cost") is not None),
            "with_tokens": sum(1 for r in rows if r.get("total_tokens") is not None),
            "cancelled": sum(1 for r in rows if r.get("failure_kind") == "cancelled"),
            "groups": len(groups),
        },
        "message": (
            "Every recorded attempt (pinned agents, router, fallbacks, cancelled turns). "
            "Cost only counts turns whose harness reports it; Cursor does not."
        ),
    }
