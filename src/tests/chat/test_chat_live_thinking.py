"""Live THINKING box in Studio (chat_live_thinking.js + studio/threads.css), in Chromium.

Studio: the live reasoning opens by default as a compact box, a few lines tall,
with a faded top edge once it overflows; the reader's own choice still holds for
the turn; a new turn starts open again; the box goes with the live bubble.
Classic: the collapsed "Thinking" disclosure, unchanged (see
src/tests/e2e/test_chat_live_thinking.py for the Classic contract).
"""
from playwright.sync_api import expect

from tests.e2e.test_shared_diff_modal import browser, static_server  # noqa: F401

BUBBLE = ('<div class="message assistant" id="typing-indicator"><div class="message-content">'
          '<div class="typing-indicator"><div class="typing-wave"><i></i><i></i><i></i></div></div>'
          '<div class="typing-status">working</div></div></div>')
LONG = "\n\n".join("Step %d: checking the tokenizer edge case number %d." % (i, i) for i in range(1, 15))


def _open(browser, static_server, *, studio, reduced_motion="no-preference"):  # noqa: F811
    page = browser.new_page(viewport={"width": 900, "height": 700}, reduced_motion=reduced_motion)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(static_server + "/agent_feed.html")
    page.set_content(
        '<link rel="stylesheet" href="' + static_server + '/css/studio/studio_tokens.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/studio/threads.css">'
        '<div id="chatMessages">' + BUBBLE + '</div>'
    )
    if studio:
        page.evaluate("() => document.documentElement.classList.add('cuttle-studio')")
    page.add_script_tag(url=static_server + "/js/chat/chat_messages.js")
    page.add_script_tag(url=static_server + "/js/chat/chat_live_thinking.js")
    page.evaluate("""(studio) => {
        const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
        window.studio = studio;
        window.lt = CuttleChatLiveThinking.create({ doc: document, currentSession: () => '7',
            sessionIdsEqual: (a, b) => String(a) === String(b),
            isStudio: () => window.studio,
            formatThinking: (t) => CuttleChatMessages.formatThinkingInner(t, { escapeHtmlInline: esc }),
            afterPaint: () => {} });
    }""", studio)
    return page, errors


def _sync(page, query_id, thinking):
    page.evaluate("([q, t]) => lt.sync({ query_id: q, thinking: t }, '7')", [query_id, thinking])


def test_studio_thinking_opens_by_default_and_fades_the_older_lines(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=True)
    try:
        block = page.locator("#typing-indicator details.thinking-block--live")
        _sync(page, "q1", "First I check the tokenizer.")
        expect(block).to_have_attribute("open", "")
        expect(block.locator("summary")).to_have_text("Thinking")
        expect(block.locator(".thinking-live-body")).to_be_visible()
        # Short text: no faded edge.
        assert "is-clipped" not in (block.locator(".thinking-live-body").get_attribute("class") or "")
        # Long reasoning: the box stays a few lines tall, scrolled to the newest line, faded at the top.
        _sync(page, "q1", LONG)
        body = block.locator(".thinking-live-body")
        expect(body).to_have_class("thinking-live-body is-clipped")
        box_h = body.evaluate("(n) => n.getBoundingClientRect().height")
        assert box_h < 120, box_h
        assert body.evaluate("(n) => n.scrollTop + n.clientHeight >= n.scrollHeight - 2")
        assert block.evaluate("(n) => getComputedStyle(n).backgroundColor") != "rgba(0, 0, 0, 0)"
        assert body.evaluate("(n) => getComputedStyle(n).maskImage") != "none"
        assert errors == []
    finally:
        page.close()


def test_studio_reader_choice_holds_for_the_turn_and_resets_for_the_next(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=True)
    try:
        block = page.locator("#typing-indicator details.thinking-block--live")
        _sync(page, "q1", "plan")
        expect(block).to_have_attribute("open", "")
        block.locator("summary").click()
        expect(block).not_to_have_attribute("open", "")
        _sync(page, "q1", "plan\n\nthen edit")
        expect(block).not_to_have_attribute("open", "")
        # A new turn starts open again.
        _sync(page, "q2", "new plan")
        expect(block).to_have_attribute("open", "")
        assert errors == []
    finally:
        page.close()


def test_studio_box_goes_with_the_live_bubble_when_the_turn_ends(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=True)
    try:
        block = page.locator("#typing-indicator details.thinking-block--live")
        _sync(page, "q1", "plan")
        expect(block).to_have_count(1)
        # The saved reply replaces the live bubble.
        page.evaluate("() => { document.getElementById('chatMessages').innerHTML = '<div class=\"message assistant\"><div class=\"message-content\">Done.</div></div>'; }")
        expect(page.locator("details.thinking-block--live")).to_have_count(0)
        assert errors == []
    finally:
        page.close()


def test_studio_summary_marker_has_no_transition_under_reduced_motion(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=True, reduced_motion="reduce")
    try:
        _sync(page, "q1", "plan")
        duration = page.locator("#typing-indicator details.thinking-block--live > summary").evaluate(
            "(n) => getComputedStyle(n, '::before').transitionDuration")
        assert duration == "0s", duration
        assert errors == []
    finally:
        page.close()


def test_classic_keeps_the_collapsed_disclosure_and_no_studio_styling(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=False)
    try:
        block = page.locator("#typing-indicator details.thinking-block--live")
        _sync(page, "q1", LONG)
        expect(block).to_have_count(1)
        expect(block).not_to_have_attribute("open", "")
        expect(block.locator(".thinking-live-body")).to_be_hidden()
        assert "is-clipped" not in (block.locator(".thinking-live-body").get_attribute("class") or "")
        assert errors == []
    finally:
        page.close()
