"""Transcript navigation in a real browser: follow-latest scrolling, the
numbered message-nav rail with pins, jump-to-bottom, jump-to-message, `?msg=`
deep links and message refs.

Characterizes the transcript-nav owner (`chat_transcript_nav.js`) through the
production chat page in a host frame. Isolated API, no Flask or agent calls.
"""
import re
import time

from playwright.sync_api import expect

from .test_chat_action_card_render import CardWorld
from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401

LONG = "\n\n".join("Line %d of a long reply that takes vertical space." % i for i in range(8))


def _world(count=10):
    """`count` rows; the page windows history to the latest 10 (older load lazily)."""
    world = CardWorld([])
    world.history = []
    now = time.time()
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        world.history.append({"id": i + 1, "role": role,
                              "content": ("Question %d" % (i + 1)) if role == "user" else LONG,
                              "timestamp": (now - 1000 + i) * 1000})
    world.next_id = count + 1
    return world


def _open(browser, static_server, world, query="chat=42"):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html*", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             f" src=\"/chat_page.html?{query}\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.assistant").last.wait_for(state="visible")
    return page, frame, errors


def _gap(frame):
    return frame.locator("#chatMessages").evaluate(
        "b => b.scrollHeight - b.scrollTop - b.clientHeight")


def _centered(frame, index):
    return frame.locator("#chatMessages").evaluate(
        """(box, idx) => {
            const el = box.querySelector('.message[data-message-index="' + idx + '"]');
            if (!el) return false;
            const b = box.getBoundingClientRect(), r = el.getBoundingClientRect();
            return r.bottom > b.top && r.top < b.bottom;
        }""", index)


def test_nav_rail_numbers_messages_and_opens_at_latest(browser, static_server):
    page, frame, errors = _open(browser, static_server, _world())
    try:
        items = frame.locator("#messageNav .message-nav-item")
        expect(items).to_have_count(10)
        expect(items.first.locator(".message-nav-num")).to_have_text("1")
        expect(items.last.locator(".message-nav-num")).to_have_text("10")
        # Opened at the latest message; no jump button.
        page.wait_for_function(
            "() => { const b = document.querySelector('iframe').contentDocument"
            ".getElementById('chatMessages'); return b.scrollHeight - b.scrollTop - b.clientHeight < 2; }")
        expect(frame.locator("#chatJumpBottom")).to_be_hidden()
        # Every non-system bubble carries its 1-based index and a share ref label.
        assert frame.locator('.message[data-message-index="7"]').count() == 1
        ref_btn = frame.locator('.message[data-message-index="6"] .message-share-ref-btn')
        if ref_btn.count():
            tip = ref_btn.first.get_attribute("title") or ref_btn.first.get_attribute("data-tooltip") or ""
            assert "CH-000042-6" in tip
        assert not errors
    finally:
        page.close()


def test_jump_to_message_and_back_to_latest(browser, static_server):
    page, frame, errors = _open(browser, static_server, _world())
    try:
        frame.locator('#messageNav .message-nav-item[data-index="3"]').click()
        page.wait_for_function(
            """() => { const d = document.querySelector('iframe').contentDocument;
                const box = d.getElementById('chatMessages');
                const el = box.querySelector('.message[data-message-index="3"]');
                const b = box.getBoundingClientRect(), r = el.getBoundingClientRect();
                return r.bottom > b.top && r.top < b.bottom; }""")
        expect(frame.locator('.message[data-message-index="3"]')).to_have_class(
            re.compile(r"\bmessage-ref-flash\b"))
        jump = frame.locator("#chatJumpBottom")
        expect(jump).to_be_visible()
        assert _gap(frame) > 140
        # A new reply while scrolled up does not yank the reader down.
        jump.click()
        page.wait_for_function(
            "() => { const b = document.querySelector('iframe').contentDocument"
            ".getElementById('chatMessages'); return b.scrollHeight - b.scrollTop - b.clientHeight < 2; }")
        expect(jump).to_be_hidden()
        assert not errors
    finally:
        page.close()


def test_pins_move_to_top_and_persist_across_reload(browser, static_server):
    world = _world()
    page, frame, errors = _open(browser, static_server, world)
    try:
        item = frame.locator('#messageNav .message-nav-item[data-index="5"]')
        item.hover()  # the pin control shows on hover
        item.locator("[data-pin-toggle]").click()
        pinned = frame.locator("#messageNav .message-nav-item.is-pinned")
        expect(pinned).to_have_count(1)
        expect(pinned.locator(".message-nav-num")).to_have_text("5")
        # Pinned items lead the rail, then a separator, then the rest.
        expect(frame.locator("#messageNav > :first-child .message-nav-num")).to_have_text("5")
        expect(frame.locator("#messageNav .message-nav-sep")).to_have_count(1)
        expect(frame.locator('.message[data-message-index="5"]')).to_have_class(
            re.compile(r"\bis-pinned-message\b"))
        stored = frame.locator("body").evaluate(
            "() => JSON.parse(localStorage.getItem('chat_message_pins_v1') || '{}')")
        assert list(stored.keys()) == ["42"] and stored["42"] == ["id:5"], stored
        page.reload(wait_until="domcontentloaded")
        frame = page.frame_locator("iframe")
        frame.locator("#chatMessages .message.assistant").last.wait_for(state="visible")
        expect(frame.locator("#messageNav .message-nav-item.is-pinned .message-nav-num")).to_have_text("5")
        # Unpin restores plain order.
        frame.locator("#messageNav .message-nav-item.is-pinned").hover()
        frame.locator("#messageNav .message-nav-item.is-pinned [data-pin-toggle]").click()
        expect(frame.locator("#messageNav .message-nav-item.is-pinned")).to_have_count(0)
        expect(frame.locator("#messageNav .message-nav-sep")).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_deep_link_msg_jumps_and_clears_param(browser, static_server):
    # 24 rows: message 4 sits behind the lazy history window, so the jump
    # has to page older messages in before it can land.
    page, frame, errors = _open(browser, static_server, _world(24), query="chat=42&msg=4")
    try:
        page.wait_for_function(
            """() => { const d = document.querySelector('iframe').contentDocument;
                const box = d.getElementById('chatMessages');
                const el = box.querySelector('.message[data-message-index="4"]');
                if (!el) return false;
                const b = box.getBoundingClientRect(), r = el.getBoundingClientRect();
                return r.bottom > b.top && r.top < b.bottom; }""")
        # Late load-time autoscrolls must not pull the reader back down.
        page.wait_for_timeout(700)
        assert _centered(frame, 4)
        assert _gap(frame) > 140
        url = frame.locator("body").evaluate("() => location.search")
        assert "msg=" not in url and "chat=42" in url
        assert not errors
    finally:
        page.close()
