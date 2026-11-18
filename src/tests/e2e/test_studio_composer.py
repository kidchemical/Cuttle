"""Studio composer (L7) on the real chat page: slash prefix bold + /orchestrate bubble chip.

Studio: the typed slash prefix is bold in the palette, and a sent
`/orchestrate @<id>` renders as the orchestrate chip (name from the playbook
list; unknown id shows the plain id). Classic: no prefix markup, raw text.

Transport is offline (IsolatedAPI + the orchestrator routes). Side-by-side
PNGs go to temp/studio_shots/L7/. The promo frames need the mock file on this
machine; that test skips when it is absent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .test_orchestrate_chip import _route_orchestrator  # noqa: F401
from .test_shared_diff_modal import IsolatedAPI, apply_request_guard, browser, static_server  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
SHOTS = REPO / "temp" / "studio_shots" / "L7"
MOCK = Path("/mnt/galaxy/Dev/Cuttle Promo/v4/project/intro/chat_template.html")

# Until chat_page.html links these (integration step), the test adds them.
STUDIO_CSS = [WEB / "css" / "studio" / "studio_tokens.css", WEB / "css" / "studio" / "composer.css"]


def _flags_body():
    return json.dumps({
        "success": True,
        "kill_switch": False,
        "flags": [{
            "id": "chat_studio_display", "label": "Studio chat display", "description": "",
            "default": False, "category": "chat", "risk": "low", "enabled": True,
            "configured_enabled": True, "needs_restart": False, "blocked_by": [],
        }],
    })


MESSAGES = [
    {"id": 1, "role": "user", "content": "/orchestrate @fast ship it", "metadata": {}},
    {"id": 2, "role": "user", "content": "/orchestrate @zeta go", "metadata": {}},
]


def _open(browser, base, *, studio):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    apply_request_guard(context, base)
    if studio:
        # The pref and the cached flag both say Studio before ui_boot reads them.
        context.add_init_script(
            "localStorage.setItem('cuttleChatDisplay', 'studio');"
            "localStorage.setItem('cuttleChatStudioFlag', '1');"
        )
    api = IsolatedAPI()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/api/experimental/flags":
            route.fulfill(status=200, content_type="application/json", body=_flags_body())
        elif path.endswith("/messages"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "success": True, "messages": MESSAGES, "session_name": "Orchestrate",
            }))
        else:
            api.handle(route)

    page.route(f"{base}/api/**", handle)
    _route_orchestrator(page, [])
    page.goto(f"{base}/chat_page.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.loadChatSession === 'function'")
    for path in STUDIO_CSS:
        if path.exists():
            page.add_style_tag(path=str(path))
    page.evaluate("() => window.loadChatSession(42)")
    return context, page, errors


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def _settle_rows(page):
    # Message rows fade in on paint; wait for them to finish before a screenshot.
    page.wait_for_function(
        "() => [...document.querySelectorAll('.message')].every((m) => getComputedStyle(m).opacity === '1')",
        timeout=5000,
    )
    page.wait_for_timeout(300)


def _type_slash(page):
    box = page.locator("#chatInput")
    box.wait_for(state="visible")
    box.click()
    box.type("/orc")
    item = page.locator("#chatSlashCommandMenu .slash-command-item--project-cmd").first
    item.wait_for(state="visible", timeout=5000)
    return item


def test_studio_palette_bolds_the_typed_prefix(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        assert page.evaluate("document.documentElement.classList.contains('cuttle-studio')")
        item = _type_slash(page)
        expect(item.locator(".slash-cmd-meta .slash-prefix-hit")).to_have_text(re.compile(r"^/?orc$"))
        _settle_rows(page)
        _shot(page, "composer-palette-studio")
        # Picking the command leaves the same orchestrate chip in the composer.
        item.click()
        composer_chip = page.locator("#chatSlashChipsRow .slash-command-chip[data-chip-index]").first
        expect(composer_chip.locator(".slash-chip-seg--model")).to_have_text("Default org chart")
        expect(composer_chip).to_have_attribute("data-full-label", "Orchestrate - Default org chart")
        page.wait_for_timeout(300)
        _shot(page, "composer-chip-studio")
        assert not errors, errors
    finally:
        context.close()


def test_classic_palette_is_unchanged(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=False)
    try:
        assert not page.evaluate("document.documentElement.classList.contains('cuttle-studio')")
        item = _type_slash(page)
        assert page.locator("#chatSlashCommandMenu .slash-prefix-hit").count() == 0
        expect(item.locator(".slash-cmd-meta")).to_contain_text("orchestrate")
        _settle_rows(page)
        _shot(page, "composer-palette-classic")
        assert not errors, errors
    finally:
        context.close()


def test_studio_sent_bubble_renders_the_orchestrate_chip(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        chips = page.locator(".user-message-with-slash .orch-bubble-chip .slash-command-chip")
        expect(chips).to_have_count(2)
        # Known id: the name from the playbook list, patched in after it loads.
        expect(chips.nth(0).locator(".slash-chip-seg--model")).to_have_text("Fast")
        expect(chips.nth(0)).to_have_attribute("data-full-label", "Orchestrate - Fast")
        # Unknown id: the plain id.
        expect(chips.nth(1).locator(".slash-chip-seg--model")).to_have_text("zeta")
        # Display only: the body keeps the text after the prefix.
        expect(page.locator(".user-slash-body").first).to_have_text("ship it")
        assert page.get_by_text("/orchestrate @fast ship it").count() == 0
        _settle_rows(page)
        _shot(page, "bubble-chip-studio")
        assert not errors, errors
    finally:
        context.close()


def test_classic_sent_bubble_keeps_the_raw_prefix(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=False)
    try:
        expect(page.get_by_text("/orchestrate @fast ship it").first).to_be_visible()
        assert page.locator(".orch-bubble-chip").count() == 0
        _settle_rows(page)
        _shot(page, "bubble-chip-classic")
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.skipif(not MOCK.exists(), reason="promo mock not present on this machine")
def test_mock_frames_for_side_by_side(browser):
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    try:
        # The mock links Google Fonts; keep it offline and let the system fallback render.
        context.route(re.compile(r"^https?://"), lambda route: route.abort())
        page = context.new_page()
        page.goto(MOCK.as_uri(), wait_until="domcontentloaded")
        # The take parks until the recorder releases it (the mock's own hook).
        page.wait_for_function("() => typeof window.__release === 'function'", timeout=20000)
        page.evaluate("() => window.__release()")
        page.wait_for_function(
            "() => { const b = document.querySelector('#mname b');"
            " return !!b && b.textContent === '/orch' && document.querySelector('#menu').classList.contains('show'); }",
            timeout=20000,
        )
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "composer-palette-mock.png"))
        page.wait_for_function(
            "() => { const u = document.querySelector('.bubble.user');"
            " return !!u && getComputedStyle(u.closest('.row')).opacity === '1'; }",
            timeout=40000,
        )
        page.wait_for_timeout(900)
        page.screenshot(path=str(SHOTS / "bubble-chip-mock.png"))
    finally:
        context.close()
