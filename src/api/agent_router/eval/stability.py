"""Aggregate repeated routing decisions for stability reporting."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _target_label(run: Dict[str, Any]) -> str:
    agent = str(run.get("target_agent") or (run.get("decision") or {}).get("target_agent") or "")
    model = str(run.get("target_model") or (run.get("decision") or {}).get("target_model") or "")
    return f"{agent}/{model or 'default'}"


def _field_stability(values: List[str]) -> Dict[str, Any]:
    counts = Counter(v for v in values if v is not None)
    unique = len(counts)
    return {
        "values": dict(counts),
        "unique": unique,
        "stable": unique <= 1,
        "mode": counts.most_common(1)[0][0] if counts else None,
    }


def _confidence_stats(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"min": None, "mean": None, "max": None, "n": 0}
    return {
        "min": round(min(values), 4),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
        "n": len(values),
    }


def _oscillates_auto_grok(target_counts: Dict[str, int]) -> bool:
    has_auto = any(
        k.endswith("/auto") or k.endswith("/default") for k in target_counts
    )
    has_grok = any("grok" in k.lower() for k in target_counts)
    return bool(has_auto and has_grok and len(target_counts) >= 2)


def _worst_result(results: List[str]) -> str:
    """Pick the most severe result label among repeats for the summary row."""
    order = [
        "invalid",
        "api_error",
        "fallback",
        "expectation_failure",
        "preference_warning",
        "manual_review",
        "pass",
    ]
    present = set(results)
    for r in order:
        if r in present:
            return r
    return results[0] if results else "pass"


def aggregate_repeated_runs(
    case_meta: Dict[str, Any],
    runs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a per-case aggregate preserving every raw run.

    ``case_meta`` should include id/category/prompt/expectation fields from the
    first run (or suite case). ``runs`` is the full list of raw decision results.
    """
    task_types = [str(r.get("task_type") or "") for r in runs]
    difficulties = [str(r.get("difficulty") or "") for r in runs]
    targets = [_target_label(r) for r in runs]
    confidences: List[float] = []
    for r in runs:
        c = r.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))

    task_s = _field_stability(task_types)
    diff_s = _field_stability(difficulties)
    tgt_s = _field_stability(targets)
    osc = _oscillates_auto_grok(tgt_s["values"])

    result_counts = Counter(str(r.get("result") or "pass") for r in runs)
    # Representative row = first run's scored fields, overridden by mode target/type
    primary = dict(runs[0]) if runs else {}
    if task_s["mode"]:
        primary["task_type"] = task_s["mode"]
    if diff_s["mode"]:
        primary["difficulty"] = diff_s["mode"]
    if tgt_s["mode"] and "/" in str(tgt_s["mode"]):
        a, m = str(tgt_s["mode"]).split("/", 1)
        primary["target_agent"] = a
        primary["target_model"] = "" if m == "default" else m
        primary["actual_summary"] = tgt_s["mode"]

    primary["result"] = _worst_result(list(result_counts.keys()))
    # expectation_pass false if any run failed expectations / invalid / fallback
    primary["expectation_pass"] = all(
        bool(r.get("expectation_pass", True))
        and str(r.get("result"))
        not in ("expectation_failure", "invalid", "fallback", "api_error")
        for r in runs
    )
    primary["preference_warning"] = any(r.get("preference_warning") for r in runs) or (
        primary["result"] == "preference_warning"
    )
    primary["validation_pass"] = all(bool(r.get("validation_pass", True)) for r in runs)
    primary["manual_review"] = any(r.get("manual_review") for r in runs) or bool(
        case_meta.get("manual_review_requested")
    )
    if primary.get("manual_review") and primary["result"] == "pass":
        primary["result"] = "manual_review"

    conf = _confidence_stats(confidences)
    latencies = [float(r["latency_ms"]) for r in runs if isinstance(r.get("latency_ms"), (int, float))]
    out = {
        **case_meta,
        **{k: primary.get(k) for k in (
            "task_type",
            "difficulty",
            "target_agent",
            "target_model",
            "actual_summary",
            "expected_summary",
            "result",
            "expectation_pass",
            "preference_warning",
            "validation_pass",
            "manual_review",
            "reason",
            "decision",
            "decision_id",
            "source",
            "used_fallback",
            "api_error",
            "invalid_rejected",
            "confidence",
            "failure_reasons",
            "notes",
        ) if k in primary or k in case_meta},
        "repeats": len(runs),
        "runs": runs,
        "stability": {
            "task_type": task_s,
            "difficulty": diff_s,
            "target": {
                **tgt_s,
                "oscillates_auto_grok": osc,
            },
            "fully_stable": bool(task_s["stable"] and diff_s["stable"] and tgt_s["stable"]),
        },
        "confidence_stats": conf,
        "confidence": conf.get("mean"),
        "result_counts": dict(result_counts),
        "latency_ms_total": round(sum(latencies), 1) if latencies else 0,
        "latency_ms_mean": round(sum(latencies) / len(latencies), 1) if latencies else 0,
    }
    # Keep a single decision blob from the mode run when possible
    mode_run = next((r for r in runs if _target_label(r) == tgt_s["mode"]), runs[0] if runs else None)
    if mode_run and mode_run.get("decision"):
        out["decision"] = mode_run["decision"]
        out["decision_id"] = mode_run.get("decision_id")
        out["reason"] = mode_run.get("reason")
        out["confidence"] = mode_run.get("confidence")
    return out


def summarize_stability(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Suite-level stability rollup."""
    oscillating = [
        c["id"]
        for c in cases
        if (c.get("stability") or {}).get("target", {}).get("oscillates_auto_grok")
    ]
    unstable_target = [
        c["id"]
        for c in cases
        if c.get("repeats", 1) > 1
        and not (c.get("stability") or {}).get("target", {}).get("stable", True)
    ]
    return {
        "cases_with_target_oscillation": oscillating,
        "cases_with_unstable_target": unstable_target,
        "oscillation_count": len(oscillating),
        "unstable_target_count": len(unstable_target),
    }
