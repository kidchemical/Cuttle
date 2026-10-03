"""Real-Chromium Stop -> resend -> refresh journey on the production chat page.

Reuses the isolated static/API browser fixtures (no Flask, provider,
subprocess, model, or login). The fake ``/api/chat`` handler NEVER blocks:
it records arrival, stashes the Route handle, appends the user row, and
returns immediately, leaving the fetch pending. The test thread pumps the
driver (locator waits / ``wait_for_timeout`` polls, no OS blocking waits),
clicks Stop, sends the resend, then fulfills the stashed second route
from the test thread with production-shaped SSE. The stale first route is
fulfilled last; unexpected fulfillment errors fail the test.

Exact fence and gaps: Playwright ``route.fulfill`` delivers the whole SSE
body at once, so byte-chunk timing, hold/detach timeouts, pending-result
recovery, and mutation-guard internals are NOT proven here. Successful
late fulfillment does not prove the aborted fetch consumed that response.
The fake server supplies idealized history; the separate backend HTTP
matrix verifies persistence policy against a temporary database.
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import pytest

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)

FIRST = "first turn hold me"
SECOND = "second turn resend"
SECOND_REPLY = "second reply painted once"
STALE_REPLY = "stale first reply must never paint"


def _sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events)


def _session_event(sid):
    return {"type": "session", "session_id": sid}


def _response_event(sid, text):
    return {"type": "response", "success": True, "response": text,
            "session_id": sid}


def _done_event():
    return {"type": "done"}


def _fulfill_sse(route, sid, text):
    # Production framing is session -> response -> done; the page stops at
    # the response event, the trailing done is transport termination.
    route.fulfill(status=200, content_type="text/event-stream",
                  body=_sse(_session_event(sid),
                            _response_event(sid, text),
                            _done_event()))


def _label(message):
    if message == FIRST:
        return "first"
    if message == SECOND:
        return "second"
    return "lone"


class ChatWorld:
    """Fake server row/request state; history models persisted rows.

    Live-status tracks the SPECIFIC current turn: set on each chat POST,
    cleared on cancel or when that same turn is fulfilled. A stale fulfill
    never clears a newer turn.
    """

    def __init__(self):
        now = time.time()
        self.history = [
            {"id": 1, "role": "user", "content": "seed question",
             "timestamp": (now - 30) * 1000},
        ]
        self.next_id = 2
        self.chat_posts = []
        self.cancel_posts = []
        self.live_turn = None
        self.deferred_first = None
        self.deferred_second = None
        self.stale_outcome = None

    def _append(self, role, content):
        row = {"id": self.next_id, "role": role, "content": content,
               "timestamp": time.time() * 1000}
        self.next_id += 1
        self.history.append(row)

    def snapshot(self):
        return [dict(m) for m in self.history]

    def posts(self):
        return list(self.chat_posts)

    def cancels(self):
        return list(self.cancel_posts)

    def is_live(self):
        return self.live_turn is not None

    def note_fulfilled(self, label):
        """Only the named turn's own fulfill clears live-status."""
        if self.live_turn == label:
            self.live_turn = None

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if req.method == "POST" and path == "/api/chat":
            self._handle_chat(route)
            return
        if req.method == "POST" and path == "/api/chat-cancel":
            try:
                cancel_body = json.loads(req.post_data or "{}")
            except ValueError:
                cancel_body = {}
            self.cancel_posts.append(cancel_body)
            self.live_turn = None
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "cancelled": True}))
            return
        if path.endswith("/messages"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "session_name": "Stop QA",
                                           "messages": self.snapshot()}))
            return
        if path == "/api/chat-live-status":
            live = self.is_live()
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"active": live, "generating": live,
                                 "status": "Working…" if live else None}))
            return
        if path == "/api/auth/sessions":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(
                              {"success": True,
                               "sessions": [{"id": 42,
                                             "session_name": "Stop QA"}]}))
            return
        IsolatedAPI().handle(route)

    def _handle_chat(self, route):
        try:
            body = json.loads(route.request.post_data or "{}")
        except ValueError:
            body = {}
        message = body.get("message", "")
        # Record arrival + stash the Route, then RETURN immediately so the
        # fetch stays pending without blocking driver event handling.
        self.chat_posts.append(message)
        self.live_turn = _label(message)
        if message == FIRST:
            self.deferred_first = route
        elif message == SECOND:
            self.deferred_second = route
        self._append("user", message)
        if message in (FIRST, SECOND):
            return
        self._append("assistant", "lone reply")
        _fulfill_sse(route, 42, "lone reply")
        self.note_fulfilled("lone")


def _open_chat(browser, static_server, world, viewport=None):
    page = (browser.new_page(viewport=viewport) if viewport
            else browser.new_page())
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(
        state="visible")
    return page, frame, errors


def _pump_until(page, predicate, timeout_ms, label):
    """Driver-pumping wait: short Playwright sleeps, no OS blocking."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting: {label}")
        page.wait_for_timeout(200)


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 800},
                                      {"width": 390, "height": 844}])
def test_stop_resend_refresh_no_stale_duplicate(browser, static_server,
                                                tmp_path, viewport):
    world = ChatWorld()
    page, frame, errors = _open_chat(browser, static_server, world,
                                     viewport)
    try:
        # First turn: send; the fake holds the fetch pending (no fulfill).
        frame.locator("#chatInput").fill(FIRST)
        frame.locator("#sendButton").click()
        _pump_until(page, lambda: FIRST in world.posts(), 15000,
                    "first POST arrival")
        frame.locator("#stopButton").wait_for(state="visible")
        frame.locator("#stopButton").click()
        # Stop keeps one activity bubble and the same durable notice even
        # after the server acknowledges cancellation.
        frame.get_by_text("⏹ Stopped generating.").wait_for(state="visible")
        assert frame.get_by_text("⏹ Stopped generating.").count() == 1
        assert frame.get_by_text("⏹ Generation cancelled.").count() == 0
        stopped = frame.locator('#chatMessages [data-stopped="true"]')
        assert stopped.count() == 1
        assert stopped.locator('.slash-command-chip--header').count() > 0
        assert stopped.locator('.slash-command-chip--header:not(.slash-command-chip--error)').count() == 0
        assert stopped.locator('.message-query-log-link').get_attribute('aria-disabled') is None
        assert stopped.get_attribute('data-message-index') is None
        assert frame.locator("#stopButton").is_visible() is False
        cancels = world.cancels()
        assert len(cancels) == 1
        assert str(cancels[0].get("session_id")) == "42"

        # Resend: busy ownership returns until the second turn completes.
        frame.locator("#chatInput").fill(SECOND)
        frame.locator("#sendButton").click()
        _pump_until(page, lambda: SECOND in world.posts(), 15000,
                    "second POST arrival")
        frame.locator("#stopButton").wait_for(state="visible")
        assert world.deferred_second is not None
        world._append("assistant", SECOND_REPLY)
        _fulfill_sse(world.deferred_second, 42, SECOND_REPLY)
        world.note_fulfilled("second")
        frame.get_by_text(SECOND_REPLY).wait_for(state="visible")
        assert frame.locator("#stopButton").is_visible() is False
        assert world.posts() == [FIRST, SECOND]
        assert len(world.cancels()) == 1

        # Late stale fulfill: the observed contract is that the fulfill
        # call completes (both viewports, twice). ANY fulfill error fails
        # this test outright — no error text is accepted as cancellation.
        # Limitation: the browser may consume the response after fetch
        # abortion, so this proves no stale paint and exact history rows,
        # not an internal turn-guard drop.
        _fulfill_sse(world.deferred_first, 42, STALE_REPLY)
        world.stale_outcome = "fulfilled"
        assert world.stale_outcome == "fulfilled"
        page.wait_for_timeout(1500)
        assert frame.get_by_text(STALE_REPLY).count() == 0
        assert frame.get_by_text(SECOND_REPLY).count() == 1
        assert frame.locator(
            "#chatMessages .message.assistant:not([data-stopped='true'])").count() == 1
        assert stopped.count() == 1

        # Refresh/history: exactly the persisted rows, no stale duplicate.
        page.reload(wait_until="domcontentloaded")
        frame.locator("#chatMessages .message.user").first.wait_for(
            state="visible")
        page.wait_for_timeout(1000)
        assert frame.get_by_text(STALE_REPLY).count() == 0
        assert frame.get_by_text(SECOND_REPLY).count() == 1
        assert stopped.count() == 0
        users = frame.locator("#chatMessages .message.user").count()
        assert users == 3
        page.screenshot(path=str(tmp_path / "stop-resend.png"))
        assert errors == []
    finally:
        page.close()


def test_unstopped_reply_paints(browser, static_server):
    """Positive control: the same fake CAN paint, so absence of stale
    paint above is staleness handling, not a broken fake."""
    world = ChatWorld()
    page, frame, errors = _open_chat(browser, static_server, world)
    try:
        frame.locator("#chatInput").fill("lone turn paints")
        frame.locator("#sendButton").click()
        frame.get_by_text("lone reply").wait_for(state="visible",
                                                 timeout=20000)
        assert errors == []
    finally:
        page.close()
