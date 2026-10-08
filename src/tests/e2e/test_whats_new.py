"""First-launch published release notes in the app shell."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.browser_guard import launch_chromium


REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"

FIXTURE = """<!doctype html><html><body>
<div class="cuttle-whats-new-modal" id="cuttleWhatsNewModal" hidden>
  <section role="dialog" aria-labelledby="cuttleWhatsNewTitle">
    <button type="button" data-whats-new-dismiss>Close</button>
    <h2 id="cuttleWhatsNewTitle">What’s new in Cuttle <span data-whats-new-version></span></h2>
    <div id="cuttleWhatsNewNotes" data-whats-new-notes></div>
    <a href="https://github.com/kidchemical/Cuttle/releases" data-whats-new-link>Notes</a>
    <button type="button" data-whats-new-dismiss>Got it</button>
  </section>
</div>
<script src="/js/shell/whats_new.js"></script>
</body></html>"""


def test_whats_new_shows_once_for_current_published_version():
    playwright = pytest.importorskip("playwright.sync_api")
    payload = {
        "current_version": "0.3.0",
        "release": {
            "version": "0.3.0",
            "notes": "# New stuff\n- A useful improvement",
            "url": "https://github.com/kidchemical/Cuttle/releases/tag/v0.3.0",
        },
    }
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page()

        def route(route):
            request = route.request
            path = urlparse(request.url).path
            if path == "/fixture.html":
                return route.fulfill(body=FIXTURE, content_type="text/html")
            if path == "/api/settings/releases":
                return route.fulfill(json=payload)
            file = WEB / path.lstrip("/")
            if file.is_file():
                return route.fulfill(body=file.read_bytes(), content_type="application/javascript")
            return route.fulfill(status=404, body="missing")

        page.route("**/*", route)
        page.goto("http://cuttle.test/fixture.html")
        modal = page.locator("#cuttleWhatsNewModal")
        modal.wait_for(state="visible")
        assert page.locator("[data-whats-new-version]").text_content() == "v0.3.0"
        assert page.locator("[data-whats-new-notes]").inner_text().startswith("# New stuff")
        assert page.locator("[data-whats-new-link]").get_attribute("href").endswith("/tag/v0.3.0")

        page.locator("[data-whats-new-dismiss]").last.click()
        assert modal.is_hidden()
        assert page.evaluate("localStorage.getItem('cuttleLastSeenReleaseVersion')") == "0.3.0"
        browser.close()
