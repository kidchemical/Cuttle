"""Orchestration dashboard renders its payload: tiles, chart + hover, tables, filters."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import parse_qs, urlparse

from .test_shared_diff_modal import browser, static_server  # noqa: F401

NOW = time.time()


def _payload(range_id="30d", project=""):
    return {
        "success": True, "id": "orchestration", "selected_range": range_id, "project": project,
        "available_ranges": [{"id": "7d", "label": "7 days"}, {"id": "30d", "label": "30 days"}],
        "projects": ["/games/x", "/mnt/Dev/Cuttle"], "backfilled": True,
        "stats": {"orchestrated_chats": 3, "spawns": 14, "runs": 13, "failed_runs": 2, "escalations": 2,
                  "escalation_pct": 20.0, "median_worker_s": 422.5, "checks": 40, "wakes": 9, "sends": 3,
                  "stops": 1, "untiered_spawns": 4},
        "daily": [{"day": "2026-11-05", "worker": 3, "escalation": 1},
                  {"day": "2026-11-06", "worker": 5, "expert": 1, "untiered": 2},
                  {"day": "2026-11-07", "worker": 2, "escalation": 1, "untiered": 2}],
        "orchestrators": [{"label": "codex · gpt-6.1-terra", "chats": 2, "spawns": 9, "spawns_per_chat": 4.5,
                           "escalations": 1, "escalations_per_chat": 0.5, "experts": 1, "child_fail_pct": 11.1,
                           "median_child_s": 300, "supervisor_interventions": 2},
                          {"label": "claude · claude-opus-5-5", "chats": 1, "spawns": 5, "spawns_per_chat": 5,
                           "escalations": 1, "escalations_per_chat": 1, "experts": 0, "child_fail_pct": 20.0,
                           "median_child_s": 500, "supervisor_interventions": 2}],
        "by_tier": [{"role": "worker", "label": "codex · gpt-6-luna", "spawns": 6, "runs": 6, "failed": 2,
                     "fail_pct": 33.3, "median_s": 61, "p90_s": 200, "escalated_after": 2, "escalation_pct": 33.3},
                    {"role": "escalation", "label": "codex · gpt-6.1-sol", "spawns": 2, "runs": 2, "failed": 0,
                     "fail_pct": 0.0, "median_s": 800, "p90_s": 900, "escalated_after": 0, "escalation_pct": None}],
        "escalations": [{"project": "/games/x", "target": "codex · gpt-6.1-sol", "count": 2}],
        "escalation_links": {"failed_worker": 2},
        "supervision": [{"label": "claude · claude-opus-5-5", "schedules": 2, "checks": 40, "wakes": 9,
                         "wake_pct": 22.5, "violations": 1, "stalls": 2, "skips": 29, "sends": 3, "stops": 1}],
        "wake_states": {"idle_with_work": 5, "awaiting_input": 4}, "check_outcomes": {"woke": 6},
        "failures": [{"at": NOW - 600, "child": "CH-000101", "parent": "CH-000100", "role": "worker",
                      "label": "codex · gpt-6-luna", "project": "/games/x", "error": "tests still red <b>x</b>"}],
    }


def test_dashboard_renders_tables_chart_hover_and_filters(browser, static_server):
    from playwright.sync_api import expect
    seen = []

    def handle(route):
        url = urlparse(route.request.url)
        if url.path == "/api/dashboards/orchestration":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            seen.append(q)
            return route.fulfill(content_type="application/json",
                                 body=json.dumps(_payload(q.get("range", "30d"), q.get("project", ""))))
        return route.fulfill(content_type="application/json", body=json.dumps({"success": True}))

    page = browser.new_page(viewport={"width": 1280, "height": 1400})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(f"{static_server}/api/**", handle)
    try:
        page.goto(static_server + "/dashboards_page.html?d=orchestration", wait_until="domcontentloaded")
        expect(page.locator("h1")).to_have_text("Orchestration & Supervision")
        expect(page.locator(".dash-stat-card")).to_have_count(9)
        expect(page.locator("#orchDashOrch tbody tr")).to_have_count(2)
        expect(page.locator("#orchDashTier tbody tr").first).to_contain_text("33.3%")
        expect(page.locator("#orchDashFail")).to_contain_text("tests still red <b>x</b>")  # escaped
        assert page.locator(".orch-dash-seg").count() == 8  # one segment per non-zero role-day
        hit = page.locator(".orch-dash-hit").nth(1)
        hit.hover()
        tip = page.locator("#orchDashTip")
        expect(tip).to_be_visible()
        expect(tip).to_contain_text("2026-11-06 · 8 spawned")
        page.screenshot(path=os.environ.get("ORCH_DASH_SHOT", "/tmp/orchestration_dashboard.png"), full_page=True)
        page.locator("[data-orch-range='7d']").click()
        expect(page.locator("[data-orch-range='7d'].is-on")).to_have_count(1)
        # The page upgrades <select> to its searchable dropdown; drive the native control's change.
        page.locator("#orchDashProject").evaluate(
            "(el) => { el.value = '/games/x'; el.dispatchEvent(new Event('change', {bubbles: true})); }")
        expect(page.locator("#orchDashProject")).to_have_value("/games/x")
        assert seen[-1] == {"range": "7d", "project": "/games/x"}
        assert not errors
    finally:
        page.close()
