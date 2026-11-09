"""Browser coverage for the isolated local-AI dashboard owner."""

import json
import re
from urllib.parse import urlparse

from .test_shared_diff_modal import browser, static_server  # noqa: F401


def _payload(dash_id):
    from api.dashboards.local_ai import dashboard

    return dashboard(dash_id)


def test_local_hardware_dashboard_filters_sorts_and_exposes_rank_as_a_3d_axis(browser, static_server):
    from playwright.sync_api import expect

    def handle(route):
        dash_id = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        return route.fulfill(content_type="application/json", body=json.dumps(_payload(dash_id)))

    page = browser.new_page(viewport={"width": 1280, "height": 1200})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(f"{static_server}/api/dashboards/local-ai-hardware", handle)
    try:
        page.goto(static_server + "/dashboards_page.html?d=local-ai-hardware", wait_until="domcontentloaded")
        expect(page.locator("h1")).to_have_text("Local AI hardware")
        expect(page.locator(".local-ai-table tbody tr")).to_have_count(len(_payload("local-ai-hardware")["rows"]))
        # The page upgrades native selects into its shared searchable control;
        # drive the source control so this remains an owner-level behavior test.
        page.locator('[data-local-axis="x"]').evaluate(
            "(el) => { el.value = 'overall_rank'; el.dispatchEvent(new Event('change', {bubbles: true})); }")
        expect(page.locator('[data-local-axis="x"]')).to_have_value("overall_rank")
        page.locator('[data-local-filter="vendor"]').evaluate(
            "(el) => { [...el.options].forEach((o) => { o.selected = o.value === 'Apple'; });"
            "el.dispatchEvent(new Event('change', {bubbles: true})); }")
        expect(page.locator(".local-ai-table tbody tr")).to_have_count(1)
        expect(page.locator(".local-ai-table tbody tr")).to_contain_text("Mac Studio")
        page.locator('[data-local-clear]').click()
        expect(page.locator(".local-ai-table tbody tr")).to_have_count(len(_payload("local-ai-hardware")["rows"]))
        page.locator('[data-local-sort="price_usd"]').click()
        expect(page.locator('[data-local-sort="price_usd"]')).to_contain_text("Reference cost")
        assert not errors
    finally:
        page.close()


def test_local_build_dashboard_shows_live_listing_and_linked_part_breakdowns(browser, static_server):
    from playwright.sync_api import expect

    def handle(route):
        return route.fulfill(content_type="application/json", body=json.dumps(_payload("local-ai-builds")))

    page = browser.new_page(viewport={"width": 1280, "height": 1200})
    page.route(f"{static_server}/api/dashboards/local-ai-builds", handle)
    try:
        page.goto(static_server + "/dashboards_page.html?d=local-ai-builds", wait_until="domcontentloaded")
        expect(page.locator("h1")).to_have_text("Purchasable local-AI builds")
        expect(page.locator(".local-ai-build-details details")).to_have_count(5)
        first = page.locator(".local-ai-build-details details").first
        first.locator("summary").click()
        expect(first.locator("a").first).to_have_attribute("href", re.compile(r"^https://"))
        assert first.locator("li").count() >= 5
    finally:
        page.close()
