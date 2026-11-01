"""First-launch published release notes in the app shell."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.browser_guard import launch_chromium


REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
SHELL_HTML = WEB / "app_shell.html"


def _shell_modal_markup() -> str:
    """The real What's new markup, so the test follows the shipped modal."""
    html = SHELL_HTML.read_text(encoding="utf-8")
    match = re.search(
        r'<div class="auth-modal-overlay" id="cuttleWhatsNewModal".*?</section>\s*</div>',
        html,
        re.S,
    )
    assert match, "What's new modal markup missing from app_shell.html"
    return match.group(0)


FIXTURE = """<!doctype html><html><head>
<link rel="stylesheet" href="/css/app_shell.css">
<link rel="stylesheet" href="/css/auth_modal.css">
</head><body>
<button id="before">before</button>
{modal}
<script src="/js/shared/modal.js"></script>
<script src="/js/chat/chat_markdown.js"></script>
<script src="/js/shell/whats_new.js"></script>
</body></html>"""

LONG_NOTES = "\r\n".join(
    [
        "First line with <script>window.__pwned = 1</script> and **bold**.",
        "",
        "## Downloads",
        "",
        "| File | For |",
        "|---|---|",
        "| `Cuttle-0.3.0-x64.exe` | Windows installer |",
        "",
        "## Highlights",
        "",
        *[f"- **Item {i}** — a useful improvement with `code`." for i in range(60)],
        "",
        "Docs: [README](https://github.com/kidchemical/cuttle#readme) and "
        "[bad](javascript:alert(1)) and https://example.com/a_b_c.",
    ]
)


def _serve(page, payload):
    def route(route):
        path = urlparse(route.request.url).path
        if path == "/fixture.html":
            body = FIXTURE.replace("{modal}", _shell_modal_markup())
            return route.fulfill(body=body, content_type="text/html")
        if path == "/api/settings/releases":
            return route.fulfill(json=payload)
        file = WEB / path.lstrip("/")
        if file.is_file():
            kind = "text/css" if file.suffix == ".css" else "application/javascript"
            return route.fulfill(body=file.read_bytes(), content_type=kind)
        return route.fulfill(status=404, body="missing")

    page.route("**/*", route)
    page.goto("http://cuttle.test/fixture.html")


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
        _serve(page, payload)
        modal = page.locator("#cuttleWhatsNewModal")
        modal.wait_for(state="visible")
        assert page.locator("[data-whats-new-version]").text_content() == "v0.3.0"
        notes = page.locator("[data-whats-new-notes]")
        assert notes.locator("h1").inner_text() == "New stuff"
        assert notes.locator("li").inner_text() == "A useful improvement"
        assert page.locator("[data-whats-new-link]").get_attribute("href").endswith("/tag/v0.3.0")

        page.locator("[data-whats-new-dismiss]").last.click()
        assert modal.is_hidden()
        assert page.evaluate("localStorage.getItem('cuttleLastSeenReleaseVersion')") == "0.3.0"
        browser.close()


def test_whats_new_long_notes_scroll_in_one_region_and_stay_escaped():
    playwright = pytest.importorskip("playwright.sync_api")
    payload = {
        "current_version": "0.3.0",
        "release": {"version": "0.3.0", "notes": LONG_NOTES, "url": ""},
    }
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        _serve(page, payload)
        page.locator("#cuttleWhatsNewModal").wait_for(state="visible")
        notes = page.locator("[data-whats-new-notes]")

        scroll = page.evaluate(
            """() => {
                const box = (sel) => {
                    const el = document.querySelector(sel);
                    return {
                        overflows: el.scrollHeight > el.clientHeight + 1,
                        overflowY: getComputedStyle(el).overflowY,
                    };
                };
                return {
                    overlay: box('#cuttleWhatsNewModal'),
                    dialog: box('.cuttle-whats-new-modal'),
                    notes: box('[data-whats-new-notes]'),
                    actionsVisible: document.querySelector('.cuttle-whats-new-actions')
                        .getBoundingClientRect().bottom <= innerHeight,
                };
            }"""
        )
        # Only the notes scroll; the dialog and overlay never add a second bar.
        assert scroll["notes"]["overflows"] and scroll["notes"]["overflowY"] == "auto"
        assert not scroll["dialog"]["overflows"]
        assert scroll["dialog"]["overflowY"] == "hidden"
        assert not scroll["overlay"]["overflows"]
        assert scroll["actionsVisible"]
        assert page.evaluate("document.activeElement.hasAttribute('data-whats-new-notes')")

        # Markdown renders; markup in the notes stays text.
        assert notes.locator("h2").count() == 2
        assert notes.locator("table th").first.inner_text() == "File"
        assert notes.locator("li").count() == 60
        assert notes.locator("script").count() == 0
        assert page.evaluate("window.__pwned") is None
        assert "<script>" in notes.inner_text()

        hrefs = notes.locator("a").evaluate_all("els => els.map(a => a.getAttribute('href'))")
        assert hrefs == [
            "https://github.com/kidchemical/cuttle#readme",
            "https://example.com/a_b_c",
        ]
        assert notes.locator("a").first.get_attribute("rel") == "noopener noreferrer"
        assert page.locator("[data-whats-new-link]").get_attribute("href") == (
            "https://github.com/kidchemical/Cuttle/releases"
        )

        page.keyboard.press("Escape")
        assert page.locator("#cuttleWhatsNewModal").is_hidden()
        browser.close()
