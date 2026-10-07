"""DeepSWE public leaderboard: fetch, cache, normalize, NEW badges."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from api.dashboards.http_fetch import get_json
from core.runtime_paths import output_dir

DEEPSWE_LIVE_URL = "https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json"
CACHE_TTL = timedelta(hours=6)
NEW_WINDOW = timedelta(hours=48)
MAX_ROWS = 1000

JsonFetcher = Callable[[str], Any]


def default_cache_dir() -> Path:
    return output_dir() / "dashboards"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


_PROVIDER_RULES = (
    (("gpt-", "o1", "o3", "o4", "chatgpt", "openai"), "openai"),
    (("claude", "anthropic"), "anthropic"),
    (("gemini", "gemma", "google"), "google"),
    (("grok", "xai"), "xai"),
    (("deepseek",), "deepseek"),
    (("qwen",), "alibaba"),
    (("glm-", "chatglm", "zhipu"), "zhipu"),
    (("kimi", "moonshot"), "moonshot"),
    (("muse", "spark"), "muse"),
    (("llama", "meta-"), "meta"),
    (("mistral", "mixtral", "codestral"), "mistral"),
    (("command-", "cohere"), "cohere"),
    (("mimo", "xiaomi"), "xiaomi"),
)

EFFORT_RANK = {
    "": 0,
    "none": 0,
    "unspecified": 0,
    "minimal": 1,
    "min": 1,
    "low": 2,
    "medium": 3,
    "mid": 3,
    "high": 4,
    "xhigh": 5,
    "x-high": 5,
    "extra high": 5,
    "max": 6,
}


def infer_provider(model: str, provider: str = "") -> str:
    raw = (provider or "").strip()
    if raw and raw.lower() not in ("unknown", "none", "null"):
        return raw
    text = (model or "").strip().lower()
    if not text:
        return "unknown"
    for prefixes, name in _PROVIDER_RULES:
        if any(text.startswith(p) or p in text for p in prefixes):
            return name
    return "unknown"


def sort_efforts(values: List[str]) -> List[str]:
    def key(v: str) -> tuple:
        s = (v or "").strip().lower()
        rank = EFFORT_RANK.get(s, 50)
        return (rank, s)

    return sorted(values, key=key)


def pretty_model_label(model: str, effort: str) -> str:
    label = (model or "unknown").strip() or "unknown"
    effort = (effort or "").strip()
    if effort:
        return f"{label} [{effort}]"
    return label


def config_id(row: Dict[str, Any]) -> str:
    cfg = str(row.get("config") or "").strip()
    if cfg:
        return cfg
    bits = [
        str(row.get("harness") or ""),
        str(row.get("model") or ""),
        str(row.get("reasoning_effort") or ""),
    ]
    return "_".join(b for b in bits if b) or "unknown"


def normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    pass1 = raw.get("pass_at_1")
    if pass1 is None:
        pass1 = raw.get("pass_rate")
    try:
        pass1_f = float(pass1) if pass1 is not None else None
    except (TypeError, ValueError):
        pass1_f = None
    cost = raw.get("mean_cost_usd")
    dur = raw.get("mean_duration_seconds")
    try:
        cost_f = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_f = None
    try:
        dur_f = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur_f = None
    model = str(raw.get("model") or "").strip() or "unknown"
    provider = infer_provider(model, str(raw.get("provider") or ""))
    effort = str(raw.get("reasoning_effort") or "").strip() or "unspecified"
    harness = str(raw.get("harness") or "").strip() or "unknown"
    cid = config_id(raw)
    return {
        "id": cid,
        "model": model,
        "label": pretty_model_label(model, effort),
        "provider": provider,
        "reasoning_effort": effort,
        "harness": harness,
        "pass_at_1": pass1_f,
        "score": round(pass1_f * 100, 4) if pass1_f is not None else None,
        "mean_cost_usd": cost_f,
        "median_cost_usd": _opt_float(raw.get("median_cost_usd")),
        "mean_duration_seconds": dur_f,
        "median_duration_seconds": _opt_float(raw.get("median_duration_seconds")),
        "mean_output_tokens": _opt_float(raw.get("mean_output_tokens")),
        "mean_input_tokens": _opt_float(raw.get("mean_input_tokens")),
        "mean_agent_steps": _opt_float(raw.get("mean_agent_steps")),
        "ci_lo": _opt_float(raw.get("ci_lo")),
        "ci_hi": _opt_float(raw.get("ci_hi")),
        "n_tasks_attempted": raw.get("n_tasks_attempted"),
        "n_runs": raw.get("n_runs"),
        "source": str(raw.get("source") or "deep-swe"),
    }


def _opt_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def annotate_new(
    rows: List[Dict[str, Any]],
    first_seen: Dict[str, str],
    now: Optional[datetime] = None,
    seeded_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    now = now or _now()
    cutoff = now - NEW_WINDOW
    seeded_dt = _parse_iso(seeded_at)
    out = []
    for row in rows:
        seen_raw = first_seen.get(row["id"])
        seen_dt = _parse_iso(seen_raw)
        appeared_after_seed = bool(
            seen_dt and (seeded_dt is None or seen_dt > seeded_dt)
        )
        is_new = bool(seen_dt and seen_dt >= cutoff and appeared_after_seed)
        item = dict(row)
        item["provider"] = infer_provider(item.get("model") or "", item.get("provider") or "")
        item["first_seen"] = seen_raw
        item["is_new"] = is_new
        out.append(item)
    return out


def merge_first_seen(
    previous: Dict[str, str],
    current_ids: List[str],
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    now = now or _now()
    stamp = _iso(now)
    merged = dict(previous or {})
    for cid in current_ids:
        if cid not in merged:
            merged[cid] = stamp
    return merged


def cache_path(cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or default_cache_dir()) / "deepswe-v1.1.json"


def load_cache(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def cache_is_fresh(payload: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or _now()
    fetched = _parse_iso(payload.get("fetched_at"))
    if not fetched:
        return False
    return (now - fetched) <= CACHE_TTL


def parse_leaderboard(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("DeepSWE payload must be an object")
    rows_in = raw.get("rows") or []
    if not isinstance(rows_in, list):
        raise ValueError("DeepSWE rows must be a list")
    rows = []
    for item in rows_in[:MAX_ROWS]:
        if isinstance(item, dict):
            rows.append(normalize_row(item))
    return {
        "generated_at": raw.get("generated_at"),
        "n_tasks_in_set": raw.get("n_tasks_in_set"),
        "scope": raw.get("scope"),
        "latest_job": raw.get("latest_job"),
        "rows": rows,
        "source_url": DEEPSWE_LIVE_URL,
    }


def fetch_and_cache(
    *,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    fetcher: Optional[JsonFetcher] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or _now()
    path = cache_path(cache_dir)
    cached = load_cache(path)
    if cached and not force and cache_is_fresh(cached, now):
        return _with_new_flags(cached, now)

    fetch = fetcher or get_json
    try:
        raw = fetch(DEEPSWE_LIVE_URL)
        parsed = parse_leaderboard(raw)
    except Exception as exc:
        if cached:
            stale = _with_new_flags(cached, now)
            stale["stale"] = True
            stale["fetch_error"] = str(exc)
            return stale
        raise

    prev_seen = (cached or {}).get("first_seen") or {}
    ids = [r["id"] for r in parsed["rows"]]
    first_seen = merge_first_seen(prev_seen, ids, now)
    seeded_at = (cached or {}).get("seeded_at") or (None if cached else _iso(now))
    if cached and not seeded_at:
        seeded_at = cached.get("fetched_at") or _iso(now)
    payload = {
        **parsed,
        "fetched_at": _iso(now),
        "seeded_at": seeded_at,
        "first_seen": first_seen,
        "stale": False,
        "fetch_error": None,
    }
    save_cache(path, payload)
    return _with_new_flags(payload, now)


def _with_new_flags(payload: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    first_seen = payload.get("first_seen") or {}
    out = dict(payload)
    out["rows"] = annotate_new(
        list(payload.get("rows") or []),
        first_seen,
        now,
        seeded_at=payload.get("seeded_at"),
    )
    out["new_count"] = sum(1 for r in out["rows"] if r.get("is_new"))
    return out
