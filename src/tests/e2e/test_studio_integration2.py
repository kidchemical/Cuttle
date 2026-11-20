"""Studio on the REAL chat page, integration 2: steer receipts, tray push, tray embed, Working pill.

Loads ``src/web/chat_page.html`` itself, like integration 1 (test_studio_chat_page_integration.py),
and reuses its fixtures. Only the network is mocked. Each test checks one gap the
integration step closes:

- steer receipts: a saved steer (``metadata.steer``) and a pending live steer
  (``/api/chat-live-status`` ``steers``) render their label and the agent's excerpt in
  Studio, and nothing renders in Classic;
- tray push: the open tray shifts the transcript column left (``.chat-main``), no card
  sits under the tray, and a narrow pane still overlays;
- tray embed: the tray iframe (``?peek=1``) drops the top bar, the sidebar and the
  header buttons, and keeps the transcript and composer;
- Working pill: a turn in flight shows the header "Working" pill.

Screenshots go to ``temp/studio_shots/integrate2/``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.e2e.test_studio_chat_page_integration import (  # noqa: F401
    SESSION, TURN, ChatWorld, _assistant_bubble, _history, _open,
)
from tests.e2e.test_shared_diff_modal import browser, static_server  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

REPO = Path(__file__).resolve().parents[3]
SHOTS = REPO / "temp" / "studio_shots" / "integrate2"
REPLY = "The changelog line is in, right after the parser fix."
LABEL_DELIVERED = "Steered into the live turn · no restart"
LABEL_PENDING = "Sending into the live turn…"
EXCERPT_LABEL = "Agent response excerpt"


def _steer(state, *, steer_id, covers, reply=None, mode="live"):
    return {"v": 1, "steer_id": steer_id, "state": state, "mode": mode,
            "delivered_at": "2026-11-18T10:00:00+00:00" if state == "delivered" else None,
            "reply": reply, "covers": covers}


def _steered_history():
    """The integration-1 history, plus two steers into the live turn: one saved with its reply,
    one still pending (its receipt comes from the live-status payload)."""
    rows = _history()
    rows.append({"id": 3, "role": "user", "content": "Also add a changelog line.",
                 "timestamp": "2026-11-18 09:02:00",
                 "metadata": {"steered": True, "turn_id": TURN,
                              "steer": _steer("delivered", steer_id="s3", covers=[3],
                                              reply={"text": REPLY, "at": "2026-11-18T10:00:05+00:00"})}})
    rows.append({"id": 4, "role": "assistant", "content": "Done. The changelog line is in.",
                 "timestamp": "2026-11-18 09:02:30",
                 "metadata": {"turn_id": TURN, "query_id": TURN, "completed_at": int(time.time())}})
    rows.append({"id": 5, "role": "user", "content": "Use the lowercase flag too.",
                 "timestamp": "2026-11-18 09:03:00",
                 "metadata": {"steered": True, "turn_id": TURN}})
    return rows


def _live_payload(*, steers=(), active=True):
    return {"success": True, "active": active, "query_id": TURN, "status": "Working on it",
            "steers": list(steers), "subagents": [], "background": None, "orchestration": "",
            "thinking": ""}


class SteerWorld(ChatWorld):
    """ChatWorld with a steered history, a live-status payload, and an optional held send."""

    def __init__(self, *, flag, history=True, live=None, hold_send=False):
        super().__init__(flag=flag, history=history)
        self.live = live
        self.hold_send = hold_send

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path

        def reply(body):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        if path == "/api/chat-live-status":
            return reply(self.live or {"success": True, "active": False})
        if path == f"/api/auth/sessions/{SESSION}/messages" and self.history:
            return reply({"success": True, "messages": _steered_history()})
        if path == "/api/auth/sessions/41/messages":
            # The fleet child (CH-000041) opened in the tray: a short transcript of its own.
            return reply({"success": True, "messages": [
                {"id": 1, "role": "user", "content": "Changelog entries for v0.8?", "timestamp": "2026-11-18 09:00:00",
                 "metadata": {}},
                {"id": 2, "role": "assistant", "content": "Parsing the changelog entries now.",
                 "timestamp": "2026-11-18 09:00:20", "metadata": {"turn_id": "turn41", "query_id": "turn41"}},
            ]})
        if path == "/api/chat" and req.method == "POST" and self.hold_send:
            # Leave the turn in flight: the page keeps its typing indicator until the request ends.
            self.chat_posts.append(req.post_data or "")
            return None
        return super().handle(route)


def _row(page, mid):
    return page.locator(f'#chatMessages .message[data-message-id="{mid}"]')


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), animations="disabled")


def _open_thread(page):
    _assistant_bubble(page).locator(".chat-thread-toggle").first.click()
    expect(_row(page, 3)).to_be_visible()


def _open_tray(page):
    page.locator(".studio-fleet-card").first.click()
    expect(page.locator('.subagent-peek[data-layout="push"]')).to_be_visible()
    page.wait_for_function(
        "() => document.getElementById('chatMessages').getBoundingClientRect().right"
        " <= document.querySelector('.subagent-peek[data-layout=\"push\"] .subagent-peek-panel').getBoundingClientRect().left + 1"
    )


def test_steer_receipts_render_on_real_page_in_studio(browser, static_server):  # noqa: F811
    world = SteerWorld(flag=True, live=_live_payload(steers=[
        {"v": 1, "steer_id": "s5", "state": "pending", "mode": "live", "message_id": 5,
         "turn_id": TURN, "covers": [5], "reply": None, "delivered_at": None}]))
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        _open_thread(page)
        # Saved steer: label and the agent's excerpt, both from metadata.steer.
        expect(_row(page, 3).locator(".chat-thread-receipt-text")).to_have_text(LABEL_DELIVERED)
        expect(_row(page, 3).locator(".chat-thread-excerpt-label")).to_have_text(EXCERPT_LABEL)
        expect(_row(page, 3).locator(".chat-thread-excerpt-text")).to_have_text(REPLY)
        # Pending live steer: its receipt comes from the live-status payload for the live turn.
        expect(_row(page, 5).locator(".chat-thread-receipt-text")).to_have_text(LABEL_PENDING)
        assert _row(page, 5).locator(".chat-thread-excerpt").count() == 0
        # The excerpt appears once, on the row that carries the reply.
        assert page.locator("#chatMessages .chat-thread-excerpt-text").count() == 1
        _shot(page, "steer-real-studio.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_steer_receipts_absent_in_classic(browser, static_server):  # noqa: F811
    world = SteerWorld(flag=False, live=_live_payload(steers=[
        {"v": 1, "steer_id": "s5", "state": "pending", "mode": "live", "message_id": 5,
         "turn_id": TURN, "covers": [5], "reply": None, "delivered_at": None}]))
    page, errors = _open(browser, static_server, world, pref="studio", cache="0")
    try:
        _open_thread(page)
        assert page.locator("#chatMessages .chat-thread-receipt").count() == 0
        assert page.locator("#chatMessages .chat-thread-excerpt").count() == 0
        html = page.evaluate("() => document.getElementById('chatMessages').innerHTML")
        for text in (LABEL_DELIVERED, LABEL_PENDING, EXCERPT_LABEL, REPLY):
            assert text not in html, text
        _shot(page, "steer-real-classic.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_tray_pushes_transcript_left_in_studio(browser, static_server):  # noqa: F811
    world = SteerWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        transcript = page.locator("#chatMessages")
        before = transcript.bounding_box()["x"] + transcript.bounding_box()["width"]
        _open_tray(page)
        panel = page.locator('.subagent-peek[data-layout="push"] .subagent-peek-panel').bounding_box()
        after = transcript.bounding_box()["x"] + transcript.bounding_box()["width"]
        # The transcript's right edge moves left by about the tray's width.
        assert before - after >= panel["width"] * 0.9, (before, after, panel)
        # No visible card sits under the tray.
        rights = page.evaluate(
            "() => Array.from(document.querySelectorAll('#chatMessages .message'))"
            ".filter((el) => el.getClientRects().length)"
            ".map((el) => el.getBoundingClientRect().right)")
        assert rights and max(rights) <= panel["x"] + 1, (max(rights), panel)
        _shot(page, "tray-push-studio.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_tray_overlays_on_a_narrow_pane(browser, static_server):  # noqa: F811
    world = SteerWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        page.set_viewport_size({"width": 720, "height": 860})
        before = page.locator("#chatMessages").bounding_box()
        page.locator(".studio-fleet-card").first.click()
        expect(page.locator('.subagent-peek[data-layout="side"]')).to_be_visible()
        assert page.locator("html.cuttle-peek-push").count() == 0
        after = page.locator("#chatMessages").bounding_box()
        assert abs(after["x"] + after["width"] - (before["x"] + before["width"])) < 1
        assert not errors, errors
    finally:
        page.context.close()


def test_tray_frame_is_embedded_without_chrome(browser, static_server):  # noqa: F811
    world = SteerWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        # Control: the parent chat shows its corner buttons and the sidebar toggle.
        expect(page.locator(".new-chat-corner-button")).to_be_visible()
        _open_tray(page)
        frame = page.frame_locator(".subagent-peek-frame")
        expect(frame.locator("html")).to_have_class(re.compile(r"\bcuttle-peek-frame\b"))
        for selector in ("#chatHistoryPanel", ".chat-session-title", ".new-chat-corner-button",
                         ".chat-history-panel-button", ".chat-chrome-menu"):
            expect(frame.locator(selector).first).to_be_hidden()
        # The transcript and the composer stay.
        expect(frame.locator("#chatMessages")).to_be_visible()
        expect(frame.locator("#chatInput")).to_be_visible()
        page.locator(".subagent-peek-frame").screenshot(path=str(SHOTS / "tray-embed-studio.png"))
        assert not errors, errors
    finally:
        page.context.close()


def test_working_pill_shows_while_a_turn_runs(browser, static_server):  # noqa: F811
    # No live turn yet: a send into a running turn would be a steer (POST /api/chat-steer), not a new turn.
    world = SteerWorld(flag=True, history=False, hold_send=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        box = page.locator("#chatInput")
        box.fill("Check the changelog entries")
        page.locator("#sendButton").click()
        pill = page.locator("#chatOrchestrationSlot .chat-orch-studio-pill")
        expect(pill).to_be_visible()
        expect(pill.locator(".chat-orch-working-label")).to_have_text("Working")
        _shot(page, "working-pill-studio.png")
        assert not errors, errors
    finally:
        page.context.close()
