"""Shell update center: one modal, pick a channel, confirm, then update.

Real browser against the static shell; every API is a same-origin fake and
external traffic is blocked. Pins the safety contract: opening or checking
never submits an update — only the confirm click does.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from .test_app_shell_layout import ShellWorld, _assert_blocked_api_free, _load_shell, _open
from .test_shared_diff_modal import (  # noqa: F401  (existing fixtures)
    browser,
    static_server,
)

DEV_SNAPSHOT = {
    "success": True, "install_kind": "source",
    "developer_mode": {"enabled": True, "available": True, "explicit": False},
    "current_version": "0.2.30", "git_rev": "1111111aaaa", "update_source": "official",
    "release": {"state": "latest", "release": {"version": "0.2.30", "url": "https://github.com/kidchemical/Cuttle/releases/tag/v0.2.30"}},
    "checkout": {"path": "/home/dev/Cuttle", "branch": "dev", "head": "1111111aaaa", "upstream": "origin/dev",
                 "ahead": 0, "behind": 2, "dirty": False, "fetched": True, "error": "", "blocked": "",
                 "commits": [{"rev": "bbbbbbb", "subject": "Fix the thing"}, {"rev": "ccccccc", "subject": "Add a thing"}]},
}

RELEASE_SNAPSHOT = {
    "success": True, "install_kind": "release",
    "developer_mode": {"enabled": False, "available": False, "explicit": False},
    "current_version": "0.2.30", "git_rev": "", "update_source": "official", "checkout": None,
    "release": {"state": "available", "release": {"version": "0.3.0", "url": "https://github.com/kidchemical/Cuttle/releases/tag/v0.3.0"}},
}


class UpdateWorld(ShellWorld):
    def __init__(self, snapshot):
        super().__init__()
        self.snapshot = snapshot
        self.checks = 0
        self.self_updates = []

    def handle(self, route):
        path = urlparse(route.request.url).path
        if path == "/api/settings/updates/check":
            self.checks += 1
            data = self.snapshot
        elif path == "/api/settings/updates":
            data = self.snapshot
        elif path == "/api/workers/self-update":
            self.self_updates.append(json.loads(route.request.post_data or "{}"))
            data = {"success": True, "job": {"id": "job-1", "status": "queued"}}
        elif path == "/api/workers/jobs/job-1":
            data = {"job": {"id": "job-1", "status": "succeeded",
                            "result": {"advanced": True, "head": "ccccccc", "branch": "dev"}}}
        elif path == "/api/workers":
            data = {"success": True, "enabled": True, "self_worker_id": "devbox", "workers": []}
        else:
            return super().handle(route)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


def _open_center(page):
    # Settings (a framed page) asks the shell the same way.
    page.evaluate("window.postMessage({type: 'cuttle-open-updates'}, location.origin)")
    page.wait_for_selector("#cuttleUpdateModal:not([hidden]) .update-channel")


def test_developer_update_requires_pick_review_and_confirm(browser, static_server):
    world = UpdateWorld(DEV_SNAPSHOT)
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        _open_center(page)
        modal = page.locator("#cuttleUpdateModal")
        page.wait_for_function("document.querySelector('[data-update-continue]') && !document.querySelector('[data-update-continue]').disabled")
        assert world.checks == 1
        cards = modal.locator(".update-channel")
        assert cards.count() == 2
        assert "Up to date" in cards.nth(0).inner_text()
        assert "2 new commits on origin/dev" in cards.nth(1).inner_text()
        assert modal.locator("input[value=official]").is_checked()
        assert "Developer mode" in modal.locator("[data-update-badge]").text_content()

        modal.locator("[data-update-continue]").click()
        assert modal.locator("#cuttleUpdateTitle").inner_text() == "Confirm update"
        review = modal.locator("[data-update-body]").inner_text()
        assert "/home/dev/Cuttle" in review and "Fix the thing" in review
        assert "developer checkout" in review
        assert world.self_updates == []  # nothing ran before the confirm click

        # Back keeps the user in control; still nothing submitted.
        modal.locator("[data-update-back]").click()
        modal.locator("[data-update-continue]").click()
        modal.locator("[data-update-confirm]").click()
        page.wait_for_selector("[data-update-restart]")
        assert modal.locator("#cuttleUpdateTitle").inner_text() == "Update ready"
        assert len(world.self_updates) == 1
        posted = world.self_updates[0]
        assert posted["update_source"] == "official" and posted["target"] == "devbox"
        assert posted["defer_restart"] is True and posted["no_daemon"] is True

        modal.locator("[data-update-later]").click()
        assert modal.is_hidden()
        assert not errors
        _assert_blocked_api_free(blocked)
    finally:
        context.close()


def test_release_install_offers_download_only(browser, static_server):
    world = UpdateWorld(RELEASE_SNAPSHOT)
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        page.evaluate("window.open = (url) => { window.__opened = url; return null; }")
        _open_center(page)
        modal = page.locator("#cuttleUpdateModal")
        page.wait_for_function("document.querySelector('[data-update-continue]') && !document.querySelector('[data-update-continue]').disabled")
        assert modal.locator(".update-channel").count() == 1
        assert modal.locator("[data-update-badge]").text_content() == "Release install"
        primary = modal.locator("[data-update-continue]")
        assert primary.inner_text() == "Download v0.3.0"
        primary.click()
        assert page.evaluate("window.__opened").endswith("/tag/v0.3.0")
        assert world.self_updates == []
        assert modal.is_hidden()
        assert not errors
        _assert_blocked_api_free(blocked)
    finally:
        context.close()


def test_update_center_uses_shared_account_modal_surface(browser, static_server):
    world = UpdateWorld(DEV_SNAPSHOT)
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        surface = page.evaluate("""() => {
            const account = document.getElementById('logoutConfirmModal');
            const update = document.getElementById('cuttleUpdateModal');
            window.CuttleModal.create(account).open();
            window.cuttleShellUpdates.open({check: false});
            const result = {
                sharedClass: account.classList.contains('auth-modal-overlay')
                    && update.classList.contains('auth-modal-overlay'),
                accountOverlay: getComputedStyle(account).backgroundColor,
                updateOverlay: getComputedStyle(update).backgroundColor,
                accountBackground: getComputedStyle(account.querySelector('.auth-modal')).backgroundImage,
                updateBackground: getComputedStyle(update.querySelector('.auth-modal')).backgroundImage,
                open: window.cuttleShellUpdates.isOpen(),
            };
            window.cuttleShellUpdates.close();
            result.closed = update.hidden && !update.classList.contains('active');
            return result;
        }""")
        assert surface["sharedClass"] and surface["open"] and surface["closed"]
        assert surface["accountOverlay"] == surface["updateOverlay"]
        assert surface["accountBackground"] == surface["updateBackground"]
        assert not errors
        _assert_blocked_api_free(blocked)
    finally:
        context.close()


# WCAG contrast of every visible text node in a popup, composited over the
# real backdrop (popup backgrounds are translucent color-mix layers).
CONTRAST_JS = """(sel) => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = (s) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = s; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (t, u) => [0, 1, 2].map((i) => t[i] * t[3] + u[i] * (1 - t[3])).concat(1);
  const lum = (c) => c.slice(0, 3).reduce((acc, v, i) => { v /= 255;
    v = v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); return acc + [0.2126, 0.7152, 0.0722][i] * v; }, 0);
  const bgOf = (el) => { const layers = [];
    for (let n = el; n; n = n.parentElement) { const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let base = layers.length && layers[layers.length - 1][3] >= 1 ? layers.pop() : [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base); return base; };
  const out = [];
  for (const el of document.querySelector(sel).querySelectorAll('*')) {
    if (!Array.from(el.childNodes).some((t) => t.nodeType === 3 && t.textContent.trim())) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || !el.getClientRects().length) continue;
    const bg = bgOf(el), fg = over(rgba(cs.color), bg);
    const a = lum(fg), b = lum(bg);
    out.push({text: el.textContent.trim().slice(0, 30), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)});
  }
  return out;
}"""

ALL_THEMES = ("dark", "midnight", "light", "sakura", "mint", "lavender", "paper",
              "graphite", "onyx", "nord", "rgb", "synthwave", "glitch", "matrix")


def test_update_center_text_is_readable_in_every_theme(browser, static_server):
    """Light themes once got fixed dark-theme pastels (~1.1:1 on white)."""
    failures = []
    for theme in ALL_THEMES:
        world = UpdateWorld(DEV_SNAPSHOT)
        context, page, errors, blocked = _open(browser, static_server, world)
        try:
            context.add_init_script(f"localStorage.setItem('theme', '{theme}');")
            _load_shell(page, static_server)
            _open_center(page)
            page.wait_for_function("document.querySelector('[data-update-continue]') && !document.querySelector('[data-update-continue]').disabled")
            rows = page.evaluate(CONTRAST_JS, "#cuttleUpdateModal")
            page.locator("[data-update-continue]").click()
            page.wait_for_selector(".update-confirm-commits")
            rows += page.evaluate(CONTRAST_JS, "#cuttleUpdateModal")
            assert rows
            failures += [(theme, r["text"], round(r["ratio"], 2)) for r in rows if r["ratio"] < 4.5]
            assert not errors
        finally:
            context.close()
    assert not failures, failures
