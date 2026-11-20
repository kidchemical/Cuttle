"""Settings → Appearance → Display (Classic | Studio) on the real settings page.

Only the experimental-flags endpoint is answered; other /api calls get a bare
success so the page can finish booting. Chromium-only (skips without one).
"""
import json

import pytest

from tests.e2e.control_helpers import choose
from tests.e2e.test_shared_diff_modal import browser, static_server  # noqa: F401


def _flags_body(enabled, kill_switch=False):
    return json.dumps({
        "success": True,
        "kill_switch": kill_switch,
        "flags": [{
            "id": "chat_studio_display", "label": "Studio chat display", "description": "",
            "default": False, "category": "chat", "risk": "low", "enabled": enabled,
            "configured_enabled": enabled, "needs_restart": False, "blocked_by": [],
        }],
    })


def _open(browser, base, *, enabled, kill_switch=False, pref=None, cache=None):
    context = browser.new_context()

    def handle(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/experimental/flags"):
            route.fulfill(status=200, content_type="application/json",
                          body=_flags_body(enabled, kill_switch))
        else:
            route.fulfill(status=200, content_type="application/json", body='{"success": true}')

    context.route("**/api/**", handle)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(base + "/settings_page.html")
    page.wait_for_function("window.CuttleChatDisplay !== undefined")
    # Set the per-browser state after the origin exists, then reload so ui_boot
    # reads it before first paint, as it does for a real returning user.
    page.evaluate(
        """([pref, cache]) => {
            if (pref) localStorage.setItem('cuttleChatDisplay', pref);
            if (cache) localStorage.setItem('cuttleChatStudioFlag', cache);
        }""",
        [pref, cache],
    )
    page.reload()
    page.wait_for_function("window.CuttleChatDisplay !== undefined")
    return context, page, errors


def _studio_class(page):
    return page.evaluate("document.documentElement.classList.contains('cuttle-studio')")


def test_row_stays_hidden_when_flag_is_off(browser, static_server):  # noqa: F811
    context, page, _errors = _open(browser, static_server, enabled=False, pref="studio", cache="1")
    try:
        # Flag off on the server wins over the cached '1' once the fetch resolves.
        page.wait_for_function("localStorage.getItem('cuttleChatStudioFlag') === '0'")
        assert page.is_hidden("#chatDisplayRow")
        assert _studio_class(page) is False
        # The stale pref is kept, so re-enabling the flag restores Studio.
        assert page.evaluate("localStorage.getItem('cuttleChatDisplay')") == "studio"
    finally:
        context.close()


def test_kill_switch_hides_row_and_forces_classic(browser, static_server):  # noqa: F811
    context, page, _errors = _open(browser, static_server, enabled=True, kill_switch=True,
                                   pref="studio", cache="1")
    try:
        page.wait_for_function("localStorage.getItem('cuttleChatStudioFlag') === '0'")
        assert page.is_hidden("#chatDisplayRow")
        assert _studio_class(page) is False
    finally:
        context.close()


def test_choice_applies_here_and_persists(browser, static_server):  # noqa: F811
    context, page, _errors = _open(browser, static_server, enabled=True)
    try:
        page.wait_for_function("!document.getElementById('chatDisplayRow').hidden")
        page.click("#tab-appearance")
        assert page.input_value("#chatDisplaySelect") == "classic"
        choose(page, page.locator("#chatDisplaySelect"), "studio")
        assert _studio_class(page) is True
        assert page.evaluate("localStorage.getItem('cuttleChatDisplay')") == "studio"
        choose(page, page.locator("#chatDisplaySelect"), "classic")
        assert _studio_class(page) is False
        assert page.evaluate("localStorage.getItem('cuttleChatDisplay')") == "classic"
    finally:
        context.close()


def test_studio_pref_is_restored_on_reload_when_flag_on(browser, static_server):  # noqa: F811
    context, page, _errors = _open(browser, static_server, enabled=True, pref="studio", cache="1")
    try:
        page.wait_for_function("!document.getElementById('chatDisplayRow').hidden")
        assert page.input_value("#chatDisplaySelect") == "studio"
        assert _studio_class(page) is True
    finally:
        context.close()
