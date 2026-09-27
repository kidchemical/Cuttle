"""BenchmarkList discovery (agent-oriented public JSON, no key)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from api.dashboards.http_fetch import get_json

RECENT_URL = "https://benchmarklist.com/api/v1/operations/recent-benchmark-updates.json"
JsonFetcher = Callable[[str], Any]


def fetch_recent(fetcher: Optional[JsonFetcher] = None) -> Dict[str, Any]:
    fetch = fetcher or get_json
    try:
        raw = fetch(RECENT_URL)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "updates": [], "latest": []}

    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = {}
    updates = data.get("updates") if isinstance(data.get("updates"), list) else []
    latest = data.get("latest_available") if isinstance(data.get("latest_available"), list) else []
    return {
        "ok": True,
        "error": None,
        "window_days": data.get("window_days"),
        "updates": _slim_updates(updates),
        "latest": _slim_latest(latest),
        "source_url": RECENT_URL,
    }


def _slim_updates(rows: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for item in rows[:40]:
        if not isinstance(item, dict):
            continue
        out.append({
            "benchmark_id": item.get("benchmark_id"),
            "name": item.get("name") or item.get("benchmark_id"),
            "at": item.get("sampled_at") or item.get("latest_result_at") or item.get("at"),
            "url": ((item.get("urls") or {}) if isinstance(item.get("urls"), dict) else {}).get("page"),
        })
    return out


def _slim_latest(rows: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for item in rows[:12]:
        if not isinstance(item, dict):
            continue
        urls = item.get("urls") if isinstance(item.get("urls"), dict) else {}
        out.append({
            "benchmark_id": item.get("benchmark_id"),
            "name": item.get("name") or item.get("benchmark_id"),
            "latest_result_at": item.get("latest_result_at"),
            "url": urls.get("page"),
        })
    return out
