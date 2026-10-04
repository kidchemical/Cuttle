"""Registry of Cuttle dashboards (hub cards)."""

from __future__ import annotations

from typing import Any, Dict, List

DASHBOARDS: List[Dict[str, Any]] = [
    {
        "id": "model-benchmarks",
        "title": "Model Benchmarks",
        "blurb": "Switch between DeepSWE, SWE-bench Verified, and Aider Polyglot, with an aggregate model view.",
        "status": "live",
        "href": "/dashboards_page.html?d=model-benchmarks",
    },
    {
        "id": "cuttle-performance",
        "title": "My Cuttle Performance",
        "blurb": "Your actual Cuttle task outcomes (accept/reject, cost, time) next to public scores.",
        "status": "live",
        "href": "/dashboards_page.html?d=cuttle-performance",
    },
    {
        "id": "cuttle-usage",
        "title": "Cuttle Usage",
        "blurb": "Cost, tokens, turns and agent time over any date range, broken down by model or harness.",
        "status": "live",
        "href": "/dashboards_page.html?d=cuttle-usage",
    },
    {
        "id": "cuttle-context",
        "title": "Context",
        "blurb": "Briefing size by layer, full re-sends and why, and each chat's context window filling up between compactions.",
        "status": "live",
        "href": "/dashboards_page.html?d=cuttle-context",
    },
]


def list_dashboards() -> List[Dict[str, Any]]:
    return [dict(d) for d in DASHBOARDS]


def get_dashboard(dash_id: str) -> Dict[str, Any] | None:
    want = (dash_id or "").strip().lower()
    for d in DASHBOARDS:
        if d["id"] == want:
            return dict(d)
    return None
