"""Real-Chromium rendering of production action cards (plan C1).

Drives the actual chat page with an isolated API (no Flask, provider,
subprocess, model, or login): the fake ``/api/chat`` fulfills immediately
with production-shaped SSE whose response text carries card markup, and the
test asserts the painted card DOM. Covers choice structure, escaping,
locked state, and watch bars through the real formatMessage ->
placeholder -> owner-renderer path.
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


def _sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events)


def _fulfill_card(route, sid, text):
    route.fulfill(status=200, content_type="text/event-stream",
                  body=_sse({"type": "session", "session_id": sid},
                            {"type": "response", "success": True,
                             "response": text, "session_id": sid},
                            {"type": "done"}))


CARD_CHOICE = (
    "Here:\n<cuttle_action_form>"
    '{"mode":"choice","title":"Pick <b>one</b> & \\"two\\"",'
    '"options":[{"id":"a","label":"A&P"},{"id":"cancel","label":"No"}]}'
    "</cuttle_action_form>"
)

CARD_LOCKED = (
    "<cuttle_action_form>"
    '{"mode":"choice","title":"Done","locked":true,'
    '"options":[{"id":"a","label":"A"}]}'
    "</cuttle_action_form>"
)

CARD_WATCH = (
    "<cuttle_action_form>"
    '{"mode":"choice","title":"Bake",'
    '"watch":{"id":"job","url":"/output/x.json",'
    '"snapshot":{"state":"running","percent":42,"label":"Half",'
    '"bars":[{"id":"all","percent":42},{"label":"w1","percent":10}]}},'
    '"options":[{"id":"a","label":"A","action":"__watch_resume__"}]}'
    "</cuttle_action_form>"
)


class CardWorld:
    """Fake server: scripted card replies, persisted-row history model."""

    def __init__(self, replies):
        now = time.time()
        self.history = [
            {"id": 1, "role": "user", "content": "seed",
             "timestamp": (now - 30) * 1000},
        ]
        self.next_id = 2
        self.replies = list(replies)

    def _append(self, role, content):
        row = {"id": self.next_id, "role": role, "content": content,
               "timestamp": time.time() * 1000}
        self.next_id += 1
        self.history.append(row)

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if req.method == "POST" and path == "/api/chat":
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                body = {}
            self._append("user", body.get("message", ""))
            text = self.replies.pop(0) if self.replies else "no card"
            self._append("assistant", text)
            _fulfill_card(route, 42, text)
            return
        if path.endswith("/messages"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "session_name": "Cards",
                                           "messages": list(self.history)}))
            return
        if path == "/api/chat-live-status":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"active": False,
                                           "generating": False,
                                           "status": None}))
            return
        if path == "/api/auth/sessions":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(
                              {"success": True,
                               "sessions": [{"id": 42,
                                             "session_name": "Cards"}]}))
            return
        IsolatedAPI().handle(route)


def _open_card_chat(browser, static_server, world):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
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


def _send_and_wait_card(frame, text):
    frame.locator("#chatInput").fill(text)
    frame.locator("#sendButton").click()
    card = frame.locator(".cuttle-action-form").last
    card.wait_for(state="visible")
    return card


def test_choice_card_renders_escaped(browser, static_server):
    world = CardWorld([CARD_CHOICE])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show choice")
        assert card.locator(".cuttle-action-form-title").inner_text() == \
            'Pick <b>one</b> & "two"'
        assert card.locator("b").count() == 0  # title text is escaped
        assert card.locator('[data-action-form-option="a"]').inner_text() == "A&P"
        cancel = card.locator('[data-action-form-option="cancel"]')
        assert cancel.get_attribute("data-action-form-cancel") == "1"
        assert "cuttle-button--primary" not in (cancel.get_attribute("class") or "")
    finally:
        page.close()


def test_locked_card_disabled_with_status(browser, static_server):
    world = CardWorld([CARD_LOCKED])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show locked")
        assert card.get_attribute("data-locked") == "1"
        assert "cuttle-action-form--locked" in (card.get_attribute("class") or "")
        assert card.locator('[data-action-form-option="a"]').is_disabled()
        status = card.locator(".cuttle-action-form-status")
        assert status.count() == 1
        assert status.get_attribute("hidden") is None
    finally:
        page.close()


def test_watch_card_multi_bar(browser, static_server):
    world = CardWorld([CARD_WATCH])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show watch")
        items = card.locator(".progress-bar-item")
        assert items.count() == 2
        assert card.locator(".progress-bar-item--primary").count() == 1
        assert card.locator(".progress-bar-item--worker").count() == 1
        assert "Half" in card.locator(".cuttle-action-form-progress-label").inner_text()
    finally:
        page.close()
