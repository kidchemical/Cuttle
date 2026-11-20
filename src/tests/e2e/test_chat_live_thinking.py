"""Live bubble thinking disclosure (chat_live_thinking.js), isolated in a browser.

Regression guard: reasoning must be readable *while* the turn works, in the
live bubble next to the activity animation, not only in the saved reply.
"""
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

BUBBLE = ('<div class="message assistant" id="%s"><div class="message-content">'
          '<div class="typing-indicator"><div class="typing-wave"><i></i><i></i><i></i></div></div>'
          '<div class="typing-status">thinking: weighing options</div></div></div>')


def _open(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/agent_feed.html')
    page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                     '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                     '<div id="chatMessages">' + (BUBBLE % 'typing-indicator') + '</div>')
    page.add_script_tag(url=static_server + '/js/chat/chat_messages.js')
    page.add_script_tag(url=static_server + '/js/chat/chat_live_thinking.js')
    page.evaluate("""() => {
        const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
        window.open = '7'; window.paints = 0;
        window.lt = CuttleChatLiveThinking.create({ doc: document, currentSession: () => window.open,
            sessionIdsEqual: (a, b) => String(a) === String(b),
            formatThinking: (t) => CuttleChatMessages.formatThinkingInner(t, { escapeHtmlInline: esc }),
            afterPaint: () => { window.paints++; } });
    }""")
    return page, errors


def test_live_bubble_shows_thinking_collapsed_with_activity_still_running(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        bubble = page.locator('#typing-indicator')
        block = bubble.locator('details.thinking-block--live')
        # No reasoning yet: nothing but the activity indicator.
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "" }, "7")')
        expect(block).to_have_count(0)
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "First I check <b>the</b> files" }, "7")')
        expect(block).to_have_count(1)
        expect(block.locator('summary')).to_have_text('Thinking')
        expect(block).not_to_have_attribute('open', '')
        expect(block.locator('.thinking-live-body')).to_be_hidden()
        # Activity indication keeps going, below the disclosure.
        expect(bubble.locator('.typing-wave')).to_be_visible()
        expect(bubble.locator('.typing-status')).to_be_visible()
        assert bubble.locator('.message-content').evaluate(
            '(n) => n.firstElementChild.matches("details.thinking-block--live")')
        # Opening it shows the text (inert), and the choice survives updates.
        block.locator('summary').click()
        expect(block.locator('.thinking-live-body')).to_contain_text('First I check <b>the</b> files')
        assert block.locator('b').count() == 0
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "First I check <b>the</b> files\\n\\nThen I edit" }, "7")')
        expect(block).to_have_attribute('open', '')
        expect(block.locator('.thinking-live-body')).to_contain_text('Then I edit')
        # A missed/empty poll keeps it; another chat's payload is ignored.
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "" }, "7")')
        page.evaluate('() => lt.sync(null, "7")')
        page.evaluate('() => lt.sync({ query_id: "q9", thinking: "other chat" }, "999")')
        expect(block).to_have_count(1)
        expect(block.locator('.thinking-live-body')).to_contain_text('Then I edit')
        expect(block.locator('.thinking-live-body')).not_to_contain_text('other chat')
        assert errors == []
    finally:
        page.close()


def test_open_choice_survives_a_rebuilt_bubble_and_resets_for_the_next_turn(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        block = page.locator('details.thinking-block--live')
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "plan" }, "7")')
        page.evaluate('() => { window.paints = 0; }')
        block.locator('summary').click()
        expect(block).to_have_attribute('open', '')
        page.wait_for_function('window.paints > 0')  # the toggle event is async
        # The page swaps the local bubble for the remote one mid-turn.
        page.evaluate("(html) => { document.getElementById('chatMessages').innerHTML = html; }",
                      BUBBLE % 'typing-indicator-remote')
        expect(block).to_have_count(0)
        page.evaluate('() => lt.sync({ query_id: "q1", thinking: "plan" }, "7")')
        expect(page.locator('#typing-indicator-remote details.thinking-block--live')).to_have_attribute('open', '')
        # Next turn in the same bubble: the old turn's reasoning goes, the new
        # one starts collapsed.
        page.evaluate('() => lt.sync({ query_id: "q2", thinking: "" }, "7")')
        expect(block).to_have_count(0)
        page.evaluate('() => lt.sync({ query_id: "q2", thinking: "new plan" }, "7")')
        expect(block).to_have_count(1)
        expect(block).not_to_have_attribute('open', '')
        # No live bubble: nothing is painted anywhere.
        page.evaluate("() => { document.getElementById('chatMessages').innerHTML = ''; }")
        page.evaluate('() => lt.sync({ query_id: "q2", thinking: "new plan" }, "7")')
        expect(block).to_have_count(0)
        assert errors == []
    finally:
        page.close()
