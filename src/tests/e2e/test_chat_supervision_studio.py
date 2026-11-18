"""Studio supervision panel (lane L5) on the real chat page in Chromium.

Studio: the supervision chip opens one timeline panel (header, rows newest first,
"Show older" through the cursor, refresh on the poll path); the worker's
"Supervised by" badge opens the supervisor's timeline narrowed to this chat.
Classic: the chip still opens the Classic popover and never calls the timeline.

The fake server answers the timeline route with the projection's own cursor
semantics (``next_before`` = "at|id" of the last row returned). It caps a page at
PAGE rows so paging is exercised with a small fixture.

Screenshots for the side-by-side go to temp/studio_shots/L5/ (gitignored):
supervision-studio.png, supervision-studio-dark.png, supervision-classic.png,
supervision-mock.png (scene supervise.html at ~3 s).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.e2e.test_chat_supervision import SupervisionWorld
from tests.e2e.test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
SHOTS = REPO / "temp" / "studio_shots" / "L5"
WEB = REPO / "src" / "web"
MOCK = Path("/mnt/galaxy/Dev/Cuttle Promo/v4/project/film/scenes/supervise.html")
PAGE = 5
STUDIO_INIT = ("localStorage.setItem('cuttleChatDisplay', 'studio');"
               "localStorage.setItem('cuttleChatStudioFlag', '1');")


def _item(i, at, kind, chat, headline, detail=""):
    return {"schema_version": 1, "id": f"check:{i}", "at": at, "kind": kind, "chat": chat,
            "headline": headline, "detail": detail, "refs": {}, "source": "check"}


def _chip_items(now):
    """The supervisor's (chat 42) timeline: every kind, two chats, one old day."""
    return [
        _item(9, now - 60, "stalled", "CH-001548", "Chat stalled", "Same failing test, three runs in a row."),
        _item(8, now - 180, "nudged", "CH-001548", "Nudge delivered",
              "Quarantine <b>test_render_timeout</b>, then continue."),
        _item(7, now - 400, "care", "CH-001549", "Leftover process ended: blender (PID 48213, 3.1 GB)"),
        _item(6, now - 900, "guard", "CH-001548", "Guard blocked a commit to main"),
        _item(5, now - 1500, "milestone", "CH-001549", "Task milestone reached"),
        _item(4, now - 2000, "paused", "CH-001549", "Supervision paused"),
        _item(3, now - 2600, "resumed", "CH-001549", "Supervision resumed"),
        _item(2, now - 3000, "error", "CH-001548", "Supervision check failed", "boom"),
        _item(1, now - 3 * 86400, "stalled", "CH-001548", "Chat stalled", "From three days ago."),
    ]


def _badge_items(now):
    """The worker's supervisor (chat 1581) runs two chats; the badge must show only CH-000042."""
    return [
        _item(12, now - 120, "nudged", "CH-000042", "Nudge delivered", "Keep going on the export."),
        _item(11, now - 240, "nudged", "CH-001548", "Nudge delivered", "Not this worker."),
        _item(10, now - 1000, "stalled", "CH-000042", "Chat stalled", "No progress for 20 min."),
    ]


def _page(items, query):
    """api.chat_ops.timeline's paging: newest first, ``before`` = "at|id", cursor = last row returned."""
    limit = min(int(query.get("limit", ["50"])[0]), PAGE)
    before = query.get("before", [""])[0]
    rows = sorted(items, key=lambda x: (x["at"], x["id"]), reverse=True)
    if before:
        ts, _, ident = before.partition("|")
        key = (float(ts), ident)
        rows = [x for x in rows if (x["at"], x["id"]) < key]
    cursor = f"{rows[limit - 1]['at']}|{rows[limit - 1]['id']}" if len(rows) > limit else None
    return {"schema_version": 1, "items": rows[:limit], "next_before": cursor}


class StudioWorld(SupervisionWorld):
    def __init__(self):
        super().__init__(flag=True)
        now = time.time()
        # A second schedule so the chip panel aggregates two chats.
        self.overview["supervising"].append(dict(self.overview["supervising"][0], id="sup3", target="CH-001549"))
        self.timelines = {"42": _chip_items(now), "1581": _badge_items(now)}
        self.timeline_calls = []
        self.timeline_fail = False

    def handle(self, route):
        path = urlparse(route.request.url).path
        match = re.fullmatch(r"/api/chat-ops/sessions/(\d+)/timeline", path)
        if match and self.flag:
            query = parse_qs(urlparse(route.request.url).query)
            self.timeline_calls.append((match.group(1), query))
            if self.timeline_fail:
                return route.fulfill(status=500, content_type="application/json",
                                     body=json.dumps({"success": False, "error": "timeline unavailable"}))
            body = _page(self.timelines.get(match.group(1), []), query)
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"success": True, **body}))
        return super().handle(route)


def _open(browser, static_server, world, *, studio):  # noqa: F811
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    if studio:
        page.add_init_script(STUDIO_INIT)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(state="visible")
    return page, frame, errors


def _chat_frame(page):
    for candidate in page.frames:
        if "chat_page.html" in candidate.url:
            return candidate
    raise AssertionError("chat frame not found")


def _load_studio_assets(page):
    """The Studio parts this lane ships. The chat page wires them in the integration step."""
    chat = _chat_frame(page)
    chat.add_script_tag(path=str(WEB / "js" / "shared" / "chat_display.js"))
    chat.add_style_tag(path=str(WEB / "css" / "studio" / "studio_tokens.css"))
    chat.add_style_tag(path=str(WEB / "css" / "studio" / "supervision.css"))
    return chat


def _shot(page, name, settle=None):
    # The panel fades in; wait for that to finish so the shot shows the settled look.
    if settle is not None:
        settle.evaluate("e => Promise.all(e.getAnimations().map(a => a.finished))")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name))


def test_studio_chip_panel_shows_timeline_pages_and_refreshes(browser, static_server):  # noqa: F811
    from playwright.sync_api import expect
    world = StudioWorld()
    page, frame, errors = _open(browser, static_server, world, studio=True)
    try:
        _load_studio_assets(page)
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        panel = frame.locator(".sup-studio-panel")
        expect(panel).to_be_visible()
        expect(panel.locator(".sup-studio-title")).to_have_text("Supervising CH-001548 and CH-001549")
        expect(panel.locator(".sup-studio-sub")).to_have_text("2 chats · check-in every 15 min")
        expect(panel.locator(".sup-studio-status")).to_have_text("active")
        # Studio replaces the Classic popover; this panel is the only surface.
        expect(frame.locator(".chat-supervision-popover")).to_have_count(0)

        # First page: five newest rows, newest first, one per kind in the fixture.
        expect(panel.locator(".sup-tl-row")).to_have_count(PAGE)
        kinds = panel.locator(".sup-tl-row").evaluate_all("els => els.map(e => e.dataset.kind)")
        assert kinds == ["stalled", "nudged", "care", "guard", "milestone"]
        assert panel.locator(".sup-tl-row.tone-warn .sup-tl-icon").count() == 1
        assert panel.locator(".sup-tl-row.tone-bad .sup-tl-icon").count() == 1
        # Two chats in view: each row names its chat.
        assert panel.locator(".sup-tl-chat").count() == PAGE
        # Server text is shown as text, never as markup.
        expect(panel.locator(".sup-tl-row").nth(1).locator(".sup-tl-detail")).to_contain_text(
            "Quarantine <b>test_render_timeout</b>, then continue.")
        assert panel.locator(".sup-tl-detail b").count() == 0
        _shot(page, "supervision-studio.png", panel)

        # Dark mode: same markup, the studio dark tokens apply.
        _chat_frame(page).evaluate("document.documentElement.classList.add('dark-mode')")
        _shot(page, "supervision-studio-dark.png", panel)
        _chat_frame(page).evaluate("document.documentElement.classList.remove('dark-mode')")

        # Show older follows the cursor; the day label appears only on the older row.
        more = panel.locator(".sup-tl-more")
        expect(more).to_have_text("Show older")
        more.click()
        expect(panel.locator(".sup-tl-row")).to_have_count(PAGE + 4)
        expect(panel.locator(".sup-tl-more")).to_have_count(0)
        older = panel.locator(".sup-tl-row").last.locator(".sup-tl-time")
        three_days = datetime.fromtimestamp(time.time() - 3 * 86400)
        expect(older).to_contain_text(f"{three_days:%b} {three_days.day}")
        expect(panel.locator(".sup-tl-row").first.locator(".sup-tl-time")).not_to_contain_text("Nov")
        timeline_paths = [call for call in world.timeline_calls]
        assert [sid for sid, _ in timeline_paths] == ["42", "42"]
        assert "before" in timeline_paths[1][1] and "before" not in timeline_paths[0][1]

        # Refresh while open rides the poll path: a new event lands on top, older pages stay.
        world.timelines["42"].insert(0, _item(99, time.time(), "milestone", "CH-001548",
                                              "Milestone: new run started"))
        frame.locator("html").evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        expect(panel.locator(".sup-tl-row")).to_have_count(PAGE + 5)
        expect(panel.locator(".sup-tl-row").first.locator(".sup-tl-headline")).to_contain_text(
            "Milestone: new run started")
        assert not errors
    finally:
        page.close()


def test_classic_chip_keeps_the_popover_and_no_timeline_call(browser, static_server):  # noqa: F811
    from playwright.sync_api import expect
    world = StudioWorld()
    page, frame, errors = _open(browser, static_server, world, studio=False)
    try:
        _load_studio_assets(page)
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        popover = frame.locator(".chat-supervision-popover")
        expect(popover).to_be_visible()
        expect(popover.locator(".chat-supervision-head")).to_contain_text("Supervising CH-001548")
        expect(frame.locator(".sup-studio-panel")).to_have_count(0)
        assert world.timeline_calls == []
        _shot(page, "supervision-classic.png")
        assert not errors
    finally:
        page.close()


def test_worker_badge_opens_supervisor_timeline_for_this_chat_only(browser, static_server):  # noqa: F811
    from playwright.sync_api import expect
    world = StudioWorld()
    page, frame, errors = _open(browser, static_server, world, studio=True)
    try:
        _load_studio_assets(page)
        frame.locator("#chatSupervisionSlot [data-sup-open=badge]").click()
        panel = frame.locator(".sup-studio-panel")
        expect(panel).to_be_visible()
        expect(panel.locator(".sup-studio-title")).to_have_text("Supervised by CH-001581")
        expect(panel.locator(".sup-studio-sub")).to_have_text("1 chat · check-in every 15 min")
        expect(panel.locator(".sup-studio-status")).to_have_text("active")
        # The supervisor's timeline, narrowed to this worker; the other chat's row is gone.
        expect(panel.locator(".sup-tl-row")).to_have_count(2)
        assert panel.locator(".sup-tl-chat").count() == 0
        assert "CH-001548" not in panel.inner_text()
        assert world.timeline_calls[-1][0] == "1581"
        expect(panel.locator(".sup-tl-more")).to_have_count(0)

        # Narrow pane: the time moves above the headline (grid row 1), the icon sits beside the headline.
        page.set_viewport_size({"width": 520, "height": 800})
        time_row = panel.locator(".sup-tl-row").first.locator(".sup-tl-time")
        icon_row = panel.locator(".sup-tl-row").first.locator(".sup-tl-icon")
        assert time_row.evaluate("e => getComputedStyle(e).gridRowStart") == "1"
        assert icon_row.evaluate("e => getComputedStyle(e).gridRowStart") == "2"
        assert icon_row.evaluate("e => getComputedStyle(e).gridColumnStart") == "1"
        assert errors == []
    finally:
        page.close()


def test_empty_and_failed_timeline_copy(browser, static_server):  # noqa: F811
    from playwright.sync_api import expect
    world = StudioWorld()
    world.timelines["42"] = []
    page, frame, errors = _open(browser, static_server, world, studio=True)
    try:
        _load_studio_assets(page)
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        panel = frame.locator(".sup-studio-panel")
        expect(panel.locator(".sup-tl-state")).to_have_text("No supervision events yet.")
        world.timeline_fail = True
        frame.locator("#chatSupervisionSlot [data-sup-open=badge]").click()
        expect(frame.locator(".sup-tl-state.is-error")).to_contain_text("Couldn’t load the timeline.")
        expect(frame.locator(".sup-tl-state.is-error")).to_contain_text("timeline unavailable")
        assert not errors
    finally:
        page.close()


def test_reduced_motion_drops_the_panel_animation(browser, static_server):  # noqa: F811
    from playwright.sync_api import expect
    world = StudioWorld()
    page, frame, _errors = _open(browser, static_server, world, studio=True)
    try:
        page.emulate_media(reduced_motion="reduce")
        _load_studio_assets(page)
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        panel = frame.locator(".sup-studio-panel")
        expect(panel).to_be_visible()
        assert panel.evaluate("e => getComputedStyle(e).animationName") == "none"
    finally:
        page.close()


def test_scene_mock_screenshot_for_side_by_side(browser):  # noqa: F811
    if not MOCK.exists():
        pytest.skip("promo scene not present on this machine")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.goto(MOCK.as_uri(), wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(SHOTS / "supervision-mock.png"))
    finally:
        page.close()
