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
        # Public leaderboards only: any signed-in account may read it.
        "public": True,
    },
    {
        "id": "local-models",
        "title": "Open-weight Coding Models",
        "blurb": "A ranked, filterable local-model shortlist with weight links, memory estimates, coding evidence and 3D trade-offs.",
        "status": "live",
        "href": "/dashboards_page.html?d=local-models",
        "public": True,
    },
    {
        "id": "local-ai-hardware",
        "title": "Local AI Hardware",
        "blurb": "Compare GPUs, accelerators and unified-memory systems by cost, power, memory, linking and local inference evidence.",
        "status": "live",
        "href": "/dashboards_page.html?d=local-ai-hardware",
        "public": True,
    },
    {
        "id": "local-ai-builds",
        "title": "Local AI Builds",
        "blurb": "Live-listing snapshots for complete local-AI systems, with ranked trade-offs and linked advertised component configurations.",
        "status": "live",
        "href": "/dashboards_page.html?d=local-ai-builds",
        "public": True,
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
    {
        "id": "cuttle-jobs",
        "title": "Jobs & Executions",
        "blurb": "Every execution Cuttle handles — agent turns and mesh jobs — with runs, success rate, cost, durations and per-agent breakdown.",
        "status": "live",
        "href": "/dashboards_page.html?d=cuttle-jobs",
    },
    {
        "id": "orchestration",
        "title": "Orchestration & Supervision",
        "blurb": "Which models orchestrate, grind, escalate and supervise: tiers, escalations per project, round times, wakes and failures.",
        "status": "live",
        "href": "/dashboards_page.html?d=orchestration",
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
