"""Adapters for public coding benchmark leaderboards."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from api.dashboards import deepswe
from api.dashboards.http_fetch import get_json, get_text

SWE_BENCH_URL = "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json"
SWE_BENCH_PAGE = "https://www.swebench.com/"
AIDER_URL = "https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml"
AIDER_PAGE = "https://aider.chat/docs/leaderboards/"
CACHE_TTL = timedelta(hours=6)

SOURCES = [
    {"id": "deepswe", "name": "DeepSWE", "page": "https://deepswe.datacurve.ai/", "score_name": "pass@1"},
    {"id": "swebench", "name": "SWE-bench Verified", "page": SWE_BENCH_PAGE, "score_name": "Resolved"},
    {"id": "aider", "name": "Aider Polyglot", "page": AIDER_PAGE, "score_name": "Pass@1"},
    {"id": "aggregate", "name": "Aggregate", "page": "", "score_name": "Equal-weight mean score"},
]

JsonFetcher = Callable[[str], Any]


def _cache_path(cache_dir: Optional[Path], key: str) -> Path:
    base = cache_dir or (Path(__file__).resolve().parents[2] / "output" / "dashboards")
    return base / f"{key}.json"


def _cached_rows(
    key: str,
    url: str,
    parser: Callable[[Any], list[dict]],
    *,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    fetcher: Optional[JsonFetcher] = None,
) -> dict:
    path = _cache_path(cache_dir, key)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = None
    now = datetime.now(timezone.utc)
    if cached and not force:
        try:
            fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
            if now - fetched <= CACHE_TTL:
                return cached
        except (KeyError, TypeError, ValueError):
            pass
    try:
        raw = (fetcher or (get_text if key == "aider-polyglot" else get_json))(url)
        result = {
            "rows": parser(raw),
            "fetched_at": now.isoformat(),
            "stale": False,
            "fetch_error": None,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
        return result
    except Exception as exc:
        if cached and isinstance(cached.get("rows"), list):
            return {**cached, "stale": True, "fetch_error": str(exc)}
        return {"rows": [], "fetched_at": None, "stale": False, "fetch_error": str(exc)}


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def parse_swebench(raw: Any) -> list[dict]:
    boards = raw.get("leaderboards", []) if isinstance(raw, dict) else []
    board = next((b for b in boards if str(b.get("name", "")).lower() == "verified"), None)
    if not board:
        raise ValueError("SWE-bench Verified board was not found")
    out = []
    for i, item in enumerate(board.get("results") or []):
        if not isinstance(item, dict) or item.get("warning"):
            continue
        model = str(item.get("model_display") or item.get("name") or "unknown").strip()
        score = _number(item.get("resolved"))
        if score is None:
            continue
        provider = deepswe.infer_provider(model)
        cost_total = _number(item.get("cost"))
        tasks = _number(item.get("num_tasks")) or 500
        out.append({
            "id": f"swebench:{item.get('folder') or i}",
            "model": model,
            "label": model,
            "provider": provider,
            "reasoning_effort": "unspecified",
            "harness": str(item.get("agent") or "unknown"),
            "score": score,
            "mean_cost_usd": cost_total / tasks if cost_total is not None else None,
            "mean_duration_seconds": None,
            "mean_output_tokens": None,
            "mean_agent_steps": None,
            "source_id": "swebench",
            "source_name": "SWE-bench Verified",
            "source_date": item.get("date"),
            "tasks": int(tasks),
            "is_new": False,
        })
    return out


def parse_aider(raw: Any) -> list[dict]:
    entries = yaml.safe_load(raw) if isinstance(raw, str) else raw
    if not isinstance(entries, list):
        raise ValueError("Aider leaderboard must be a YAML list")
    out = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or "unknown").strip()
        score = _number(item.get("pass_rate_1"))
        if score is None:
            continue
        tests = _number(item.get("test_cases")) or 225
        total_cost = _number(item.get("total_cost"))
        out.append({
            "id": f"aider:{item.get('dirname') or i}",
            "model": model,
            "label": model,
            "provider": deepswe.infer_provider(model),
            "reasoning_effort": str(item.get("reasoning_effort") or "unspecified"),
            "harness": "Aider",
            "score": score,
            "mean_cost_usd": total_cost / tests if total_cost is not None else None,
            "mean_duration_seconds": _number(item.get("seconds_per_case")),
            "mean_output_tokens": None,
            "mean_agent_steps": None,
            "source_id": "aider",
            "source_name": "Aider Polyglot",
            "source_date": str(item.get("date") or ""),
            "tasks": int(tests),
            "is_new": False,
        })
    return out


def source_data(
    source_id: str,
    *,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    fetcher: Optional[JsonFetcher] = None,
) -> dict:
    if source_id == "swebench":
        return _cached_rows("swebench-verified", SWE_BENCH_URL, parse_swebench,
                            cache_dir=cache_dir, force=force, fetcher=fetcher)
    if source_id == "aider":
        return _cached_rows("aider-polyglot", AIDER_URL, parse_aider,
                            cache_dir=cache_dir, force=force, fetcher=fetcher)
    raise ValueError(f"Unsupported external benchmark: {source_id}")


def aggregate_rows(datasets: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for dataset in datasets:
        per_model: dict[str, dict] = {}
        for row in dataset.get("rows") or []:
            key = "".join(ch for ch in str(row.get("model") or "").lower() if ch.isalnum())
            if not key or row.get("score") is None:
                continue
            if key not in per_model or row["score"] > per_model[key]["score"]:
                per_model[key] = row
        for key, row in per_model.items():
            grouped.setdefault(key, []).append(row)
    result = []
    for key, entries in grouped.items():
        scores = [float(row["score"]) for row in entries]
        mean_score = sum(scores) / len(scores)
        spread = (
            (sum((score - mean_score) ** 2 for score in scores) / len(scores)) ** 0.5
            if len(scores) > 1 else None
        )
        costs = [row["mean_cost_usd"] for row in entries if row.get("mean_cost_usd") is not None]
        durations = [row["mean_duration_seconds"] for row in entries if row.get("mean_duration_seconds") is not None]
        model = entries[0].get("model") or key
        result.append({
            "id": f"aggregate:{key}",
            "model": model,
            "label": model,
            "provider": entries[0].get("provider") or "unknown",
            "reasoning_effort": "unspecified",
            "harness": f"{len(entries)} benchmarks",
            "score": mean_score,
            "score_spread": spread,
            "mean_cost_usd": sum(costs) / len(costs) if costs else None,
            "mean_duration_seconds": sum(durations) / len(durations) if durations else None,
            "mean_output_tokens": None,
            "mean_agent_steps": None,
            "benchmark_count": len(entries),
            "benchmark_scores": [{"source": r.get("source_name"), "score": r.get("score")} for r in entries],
            "source_id": "aggregate",
            "source_name": "Aggregate",
            "is_new": False,
        })
    return sorted(result, key=lambda row: row["score"], reverse=True)
