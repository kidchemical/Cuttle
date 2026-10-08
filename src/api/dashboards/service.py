"""Assemble dashboard payloads for the hub API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

import api.dashboards.benchmarklist as benchmarklist
import api.dashboards.catalog as catalog
import api.dashboards.deepswe as deepswe
import api.dashboards.integrations as integrations

JsonFetcher = Callable[[str], Any]


def hub() -> Dict[str, Any]:
    return {"success": True, "dashboards": catalog.list_dashboards()}


def model_benchmarks(
    *,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    fetcher: Optional[JsonFetcher] = None,
    include_discovery: bool = True,
    source_id: str = "deepswe",
) -> Dict[str, Any]:
    if source_id not in {item["id"] for item in integrations.SOURCES}:
        source_id = "deepswe"
    board = {}
    if source_id in ("deepswe", "aggregate"):
        try:
            board = deepswe.fetch_and_cache(cache_dir=cache_dir, force=force, fetcher=fetcher)
        except Exception as exc:
            if source_id == "deepswe":
                raise
            board = {"rows": [], "fetch_error": str(exc), "stale": False}
    discovery = None
    if include_discovery:
        discovery = benchmarklist.fetch_recent(fetcher=fetcher)

    datasets = {"deepswe": {
        "rows": [{**row, "source_id": "deepswe", "source_name": "DeepSWE"} for row in board.get("rows", [])],
        "fetched_at": board.get("fetched_at"),
        "fetch_error": board.get("fetch_error"),
        "stale": board.get("stale", False),
    }}
    if source_id in ("swebench", "aider", "aggregate"):
        for external_id in ("swebench", "aider"):
            datasets[external_id] = integrations.source_data(
                external_id, cache_dir=cache_dir, force=force, fetcher=fetcher,
            )
    if source_id == "deepswe":
        rows = datasets["deepswe"]["rows"]
        source = {
            "id": "deepswe", "name": "DeepSWE v1.1", "url": deepswe.DEEPSWE_LIVE_URL,
            "page": "https://deepswe.datacurve.ai/", "score_name": "pass@1",
            "generated_at": board.get("generated_at"),
            "n_tasks_in_set": board.get("n_tasks_in_set"),
        }
        fetched_at = board.get("fetched_at")
        fetch_error = board.get("fetch_error")
        stale = board.get("stale")
    elif source_id == "aggregate":
        rows = integrations.aggregate_rows(list(datasets.values()))
        source = {"id": "aggregate", "name": "Aggregate", "page": "", "score_name": "Mean score",
                  "generated_at": None,
                  "n_tasks_in_set": None}
        fetched_at = max((d.get("fetched_at") or "" for d in datasets.values()), default="") or None
        failures = [f"{name}: {d['fetch_error']}" for name, d in datasets.items() if d.get("fetch_error")]
        fetch_error = "; ".join(failures) or None
        stale = any(d.get("stale") for d in datasets.values())
    else:
        selected = datasets[source_id]
        source_meta = next(item for item in integrations.SOURCES if item["id"] == source_id)
        rows = selected.get("rows") or []
        source = {**source_meta, "url": integrations.SWE_BENCH_URL if source_id == "swebench" else integrations.AIDER_URL,
                  "generated_at": None, "n_tasks_in_set": None}
        fetched_at = selected.get("fetched_at")
        fetch_error = selected.get("fetch_error")
        stale = selected.get("stale")

    providers = sorted({r.get("provider") or "unknown" for r in rows}, key=lambda s: s.lower())
    efforts = deepswe.sort_efforts(list({r.get("reasoning_effort") or "unspecified" for r in rows}))
    harnesses = sorted({r.get("harness") or "unknown" for r in rows})
    benchmark_names = sorted({
        item.get("source")
        for row in rows
        for item in (row.get("benchmark_scores") or [])
        if item.get("source")
    } or {r.get("source_name") or "DeepSWE" for r in rows})
    plottable = [
        r for r in rows
        if r.get("score") is not None
        and r.get("mean_cost_usd") is not None
        and r.get("mean_duration_seconds") is not None
    ]
    return {
        "success": True,
        "id": "model-benchmarks",
        "title": "Model Benchmarks",
        "dataset": "public",
        "source": source,
        "selected_source": source_id,
        "available_sources": integrations.SOURCES,
        "fetched_at": fetched_at,
        "stale": bool(stale),
        "fetch_error": fetch_error,
        "new_count": sum(1 for r in rows if r.get("is_new")),
        "filters": {
            "providers": providers,
            "reasoning_efforts": efforts,
            "harnesses": harnesses,
        },
        "stats": {
            "configs": len(rows),
            "plottable": len(plottable),
            "new": sum(1 for r in rows if r.get("is_new")),
            "benchmarks": len(benchmark_names),
        },
        "rows": rows,
        "aggregate_note": (
            "Equal-weight mean of each model's best published score in each available benchmark. "
            "Benchmarks use different tasks and harnesses; compare this as a rough summary, not a controlled ranking."
            if source_id == "aggregate" else None
        ),
        "discovery": discovery,
        "axes": {
            "x": ["mean_cost_usd", "mean_output_tokens", "mean_agent_steps", "mean_duration_seconds", "score"],
            "y": ["score", "mean_cost_usd", "mean_duration_seconds", "mean_output_tokens", "mean_agent_steps"],
            "z": ["mean_duration_seconds", "mean_cost_usd", "mean_output_tokens", "mean_agent_steps", "score"],
        },
    }


PERFORMANCE_SOURCES = [
    {"id": "all", "name": "All turns"},
    {"id": "pinned", "name": "Pinned agents"},
    {"id": "router", "name": "Agent router"},
]
PERFORMANCE_WINDOWS = [7, 30, 90, 0]
PERFORMANCE_AXES = [
    "score",
    "success_rate",
    "mean_duration_seconds",
    "mean_total_duration_seconds",
    "mean_cost_usd",
    "mean_output_tokens",
    "mean_total_tokens",
    "turns",
]
_TURN_LIMIT = 200
_TURNS_PER_CONFIG = 30


def _median(values: list) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _mean(values: list) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _turn_accepted(row: Dict[str, Any]) -> bool:
    """User thumbs outrank a Jev label, which outranks run success."""
    fb = row.get("user_feedback")
    if fb in ("good", "bad"):
        return fb == "good"
    jev = row.get("jev") if isinstance(row.get("jev"), dict) else {}
    if jev.get("accepted") in (True, False):
        return bool(jev["accepted"])
    return bool(row.get("success"))


def _config_identity(row: Dict[str, Any]) -> Dict[str, str]:
    from api.agent_router.pinned_outcomes import provider_for, split_model_effort

    agent = str(row.get("target_agent") or "unknown").lower()
    model, effort = split_model_effort(row.get("target_model"))
    effort = str(row.get("reasoning_effort") or effort or "unspecified").lower()
    return {
        "agent": agent,
        "model": model,
        "reasoning_effort": effort,
        "provider": provider_for(agent, model),
    }


def _attach_jev(rows: list, label: bool, db_path: Optional[Path] = None) -> tuple:
    """Attach labels; returns (rows, jev_status).

    ``label`` labels this window synchronously; otherwise cached labels are
    attached and any unlabeled turns are handed to the background labeler.
    """
    try:
        from api.jev import labels as jl
    except Exception:
        return rows, {"available": False}
    if label:
        stats: Dict[str, Any] = {}
        try:
            rows = jl.label_rows(rows, limit=600, stats=stats)
        except Exception as exc:
            stats["last_error"] = str(exc)[:160]
        return rows, {**jl.labeling_status(), **stats, "running": False}
    try:
        rows = jl.attach_cached(rows)
        pending = sum(1 for r in rows if "jev" not in r)
        status = jl.labeling_status()
        if pending and not status.get("running") and db_path is None:
            status = jl.label_pending_async()
        return rows, {**status, "window_pending": pending}
    except Exception:
        return rows, {"available": False}


def cuttle_performance(
    *,
    label: bool = False,
    source_id: str = "all",
    days: int = 30,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Local outcomes (router + pinned agents), one point per agent/model/effort."""
    import time as _time

    source_id = source_id if source_id in {s["id"] for s in PERFORMANCE_SOURCES} else "all"
    days = int(days) if int(days or 0) in PERFORMANCE_WINDOWS else 30
    base = {
        "success": True,
        "id": "cuttle-performance",
        "title": "My Cuttle Performance",
        "dataset": "local",
        "status": "live",
        "selected_source": source_id,
        "available_sources": PERFORMANCE_SOURCES,
        "selected_days": days,
        "available_days": PERFORMANCE_WINDOWS,
        "source": {"id": source_id, "name": "My Cuttle Performance", "score_name": "Accept score"},
        "axis_fields": PERFORMANCE_AXES,
    }
    try:
        from api.agent_router.outcomes import all_outcomes

        since = (_time.time() - days * 86400) if days else None
        rows = all_outcomes(since=since, db_path=db_path)
    except Exception as exc:
        return {**base, "message": f"Outcomes store unavailable: {str(exc)[:160]}",
                "rows": [], "turns": [], "stats": {}, "filters": {}}

    if source_id == "pinned":
        rows = [r for r in rows if r.get("source") == "pinned"]
    elif source_id == "router":
        rows = [r for r in rows if r.get("source") != "pinned"]
    rows, jev_status = _attach_jev(rows, label, db_path=db_path)

    groups: Dict[str, Dict[str, Any]] = {}
    turns = []
    turns_per_config: Dict[str, int] = {}
    for index, r in enumerate(rows):
        ident = _config_identity(r)
        key = f"{ident['agent']}|{ident['model']}|{ident['reasoning_effort']}"
        jev = r.get("jev") if isinstance(r.get("jev"), dict) else {}
        latency = r.get("latency_ms")
        # Newest turns overall, plus the newest few of every config so a
        # selected point always has rows to show.
        if index < _TURN_LIMIT or turns_per_config.get(key, 0) < _TURNS_PER_CONFIG:
            turns_per_config[key] = turns_per_config.get(key, 0) + 1
            turns.append({
                "config_id": key,
                "recorded_at": r.get("recorded_at"),
                "agent": ident["agent"],
                "model": ident["model"],
                "reasoning_effort": ident["reasoning_effort"],
                "source": r.get("source"),
                "success": bool(r.get("success")),
                "failure_kind": r.get("failure_kind"),
                "reason": r.get("reason") or "",
                "user_feedback": r.get("user_feedback"),
                "miss_kind": jev.get("miss_kind"),
                "label_source": jev.get("source"),
                "jev_accepted": jev.get("accepted") if jev.get("source") == "jev" else None,
                "accept_noul": jev.get("accept_noul"),
                "duration_seconds": (float(latency) / 1000.0) if latency is not None else None,
                "cost_usd": r.get("cost"),
                "output_tokens": r.get("completion_tokens"),
                "total_tokens": r.get("total_tokens"),
                "session_id": r.get("session_id"),
            })
        if r.get("failure_kind") == "cancelled":
            continue
        g = groups.setdefault(key, {**ident, "rows": []})
        g["rows"].append(r)

    configs = []
    for key, g in groups.items():
        items = g.pop("rows")
        n = len(items)
        successes = sum(1 for r in items if r.get("success"))
        accepted = sum(1 for r in items if _turn_accepted(r))
        sources = sorted({"pinned" if r.get("source") == "pinned" else "router" for r in items})
        durations = [
            float(r["latency_ms"]) / 1000.0 for r in items if r.get("latency_ms") is not None
        ]
        effort = g["reasoning_effort"]
        configs.append({
            "id": key,
            "label": g["model"] if effort == "unspecified" else f"{g['model']} · {effort}",
            "model": g["model"],
            "provider": g["provider"],
            "harness": g["agent"],
            "reasoning_effort": effort,
            "sources": sources,
            "turns": n,
            "successes": successes,
            "success_rate": 100.0 * successes / n,
            "score": 100.0 * accepted / n,
            "good_feedback": sum(1 for r in items if r.get("user_feedback") == "good"),
            "bad_feedback": sum(1 for r in items if r.get("user_feedback") == "bad"),
            "transport_failures": sum(1 for r in items if r.get("failure_kind") == "transport"),
            "task_failures": sum(1 for r in items if r.get("failure_kind") == "task"),
            "mean_duration_seconds": _median(durations),
            "mean_total_duration_seconds": _mean(durations),
            "p90_duration_seconds": (
                sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.9))]
                if durations else None
            ),
            "mean_cost_usd": _mean([r.get("cost") for r in items]),
            "total_cost_usd": sum(float(r["cost"]) for r in items if r.get("cost") is not None) or None,
            "mean_output_tokens": _mean([r.get("completion_tokens") for r in items]),
            "mean_total_tokens": _mean([r.get("total_tokens") for r in items]),
            "last_used_at": max((r.get("recorded_at") or 0) for r in items) or None,
        })
    configs.sort(key=lambda c: (-c["turns"], c["id"]))

    attempts = len(rows)
    counted = [r for r in rows if r.get("failure_kind") != "cancelled"]
    successes = sum(1 for r in counted if r.get("success"))
    labeled = [r for r in rows if isinstance(r.get("jev"), dict) and r["jev"].get("source") == "jev"]
    miss_kinds: Dict[str, int] = {}
    for r in labeled:
        k = str((r.get("jev") or {}).get("miss_kind") or "ok")
        miss_kinds[k] = miss_kinds.get(k, 0) + 1
    stamps = [r.get("recorded_at") for r in rows if r.get("recorded_at")]
    return {
        **base,
        "message": (
            "Router and pinned-agent turns recorded locally. Each point is one agent/model/effort. "
            "Accept score uses your thumbs first, then Jev labels, then whether the run finished; "
            "duration is the median per turn."
        ),
        "stats": {
            "configs": len(configs),
            "attempts": attempts,
            "turns": len(counted),
            "successes": successes,
            "success_rate": (successes / len(counted)) if counted else 0.0,
            "pinned_turns": sum(1 for r in rows if r.get("source") == "pinned"),
            "router_turns": sum(1 for r in rows if r.get("source") != "pinned"),
            "cancelled": attempts - len(counted),
            "transport": sum(1 for r in rows if r.get("failure_kind") == "transport"),
            "task_fail": sum(1 for r in rows if r.get("failure_kind") == "task"),
            "good_feedback": sum(1 for r in rows if r.get("user_feedback") == "good"),
            "bad_feedback": sum(1 for r in rows if r.get("user_feedback") == "bad"),
            "labeled": len(labeled),
            "accepted_labels": sum(1 for r in labeled if r["jev"].get("accepted") is True),
            "rejected_labels": sum(1 for r in labeled if r["jev"].get("accepted") is False),
            "with_cost": sum(1 for r in rows if r.get("cost") is not None),
            "plottable": sum(1 for c in configs if c["mean_duration_seconds"] is not None),
            "miss_kinds": miss_kinds,
            "first_recorded_at": min(stamps) if stamps else None,
            "last_recorded_at": max(stamps) if stamps else None,
        },
        "filters": {
            "providers": sorted({c["provider"] for c in configs}, key=str.lower),
            "reasoning_efforts": deepswe.sort_efforts(list({c["reasoning_effort"] for c in configs})),
            "harnesses": sorted({c["harness"] for c in configs}),
        },
        "rows": configs,
        "turns": turns,
        "jev": {k: jev_status.get(k) for k in (
            "available", "running", "pending", "window_pending", "labeled", "cost_usd", "errors", "last_error",
        ) if k in jev_status},
    }
