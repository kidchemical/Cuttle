"""Composer lock in the chat page (chat_composer_lock.js).

* a sub-agent child chat opens locked: input parts inert, lock badge shown,
  typing and Send do nothing; Unlock in the ☰ menu frees it, the choice
  survives a reload, and Lock composer locks it again
* a supervised chat (``supervised_by`` from chat-ops) locks only while the
  ``chat_supervision`` flag is on; a plain chat never locks
"""
from __future__ import annotations

import json
import os

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_chat_supervision import SupervisionWorld, _toggle_flag
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class ChildWorld(CardWorld):
    """Chat 42 is a sub-agent child of chat 7."""

    def __init__(self):
        super().__init__(["child reply"])
        self.chat_posts = 0

    def handle(self, route):
        req = route.request
        if req.url.split("?")[0].endswith("/messages"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "success": True, "session_name": "Child", "messages": list(self.history),
                "parent_session_id": 7, "origin": "subagent", "display_name": "Scout"}))
            return
        if req.method == "POST" and req.url.split("?")[0].endswith("/api/chat"):
            self.chat_posts += 1
        return super().handle(route)


def _try_type(page, frame, text):
    frame.locator("#chatComposerWrap").click(position={"x": 60, "y": 50}, force=True)
    page.keyboard.type(text)
    return frame.locator("#chatInput").input_value()


def _menu_click(frame, action):
    frame.locator("#chatChromeMenuBtn").click()
    frame.locator(f"#chatChromeMenuDropdown [data-composer-lock={action}]").click()


def test_subagent_chat_opens_locked_and_unlocks_from_menu(browser, static_server):
    from playwright.sync_api import expect
    world = ChildWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatComposerWrap.is-composer-locked").wait_for()
        expect(frame.locator(".composer-lock-overlay .composer-lock-badge")).to_be_visible()
        assert "sub-agent chat" in frame.locator(".composer-lock-badge").get_attribute("aria-label")
        for sel in ("#chatSlashChipsRow", ".textarea-with-slash-menu", "#sendButton", "#attachButton"):
            assert frame.locator(f"#chatComposerWrap {sel}").get_attribute("inert") is not None, sel
        assert frame.locator("#stopButton").get_attribute("inert") is None
        assert frame.locator("#chatContextGauge").get_attribute("inert") is None

        assert _try_type(page, frame, "oops wrong chat") == ""
        frame.locator("html").evaluate("() => window.sendMessage()")
        page.wait_for_timeout(200)
        assert world.chat_posts == 0
        page.screenshot(path=os.environ.get("COMPOSER_LOCK_SHOT", "/tmp/composer_lock.png"))

        frame.locator("#chatChromeMenuBtn").click()
        expect(frame.locator("[data-composer-lock=unlock]")).to_contain_text("Unlock composer")
        page.screenshot(path=os.environ.get("COMPOSER_LOCK_MENU_SHOT", "/tmp/composer_lock_menu.png"))
        frame.locator("[data-composer-lock=unlock]").click()
        expect(frame.locator("#chatChromeMenuDropdown")).to_be_hidden()
        expect(frame.locator("#chatComposerWrap.is-composer-locked")).to_have_count(0)
        expect(frame.locator(".composer-lock-overlay")).to_be_hidden()
        assert frame.locator("#chatComposerWrap .textarea-with-slash-menu").get_attribute("inert") is None
        frame.locator("#chatInput").fill("hello child")
        frame.locator("#sendButton").click()
        expect(frame.locator("#chatMessages .message.user")).to_have_count(2)
        assert world.chat_posts == 1

        # Remembered on this device: a reload keeps it unlocked.
        page.reload(wait_until="domcontentloaded")
        frame = page.frame_locator("iframe")
        frame.locator("#chatMessages .message.user").first.wait_for(state="visible")
        page.wait_for_timeout(300)
        expect(frame.locator("#chatComposerWrap.is-composer-locked")).to_have_count(0)

        _menu_click(frame, "lock")
        frame.locator("#chatComposerWrap.is-composer-locked").wait_for()
        assert not errors
    finally:
        page.close()


def test_supervised_chat_locks_while_supervision_flag_is_on(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)  # overview: chat 42 is supervised by CH-001581
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatComposerWrap.is-composer-locked").wait_for()
        assert "supervised chat" in frame.locator(".composer-lock-badge").get_attribute("aria-label")
        _toggle_flag(frame, world, False)
        expect(frame.locator("#chatComposerWrap.is-composer-locked")).to_have_count(0)
        _toggle_flag(frame, world, True)
        frame.locator("#chatComposerWrap.is-composer-locked").wait_for()
        assert not errors
    finally:
        page.close()


def test_plain_chat_never_locks(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)
    world.overview["supervised_by"] = []
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").wait_for()
        expect(frame.locator("#chatComposerWrap.is-composer-locked")).to_have_count(0)
        frame.locator("#chatChromeMenuBtn").click()
        expect(frame.locator("[data-composer-lock]")).to_have_count(0)
        assert not errors
    finally:
        page.close()
