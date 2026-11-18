"""Studio steer reply thread (chat_threads.js + chat_thread_model.js), in Chromium.

Owner-level harness, like test_chat_reply_threads.py: transcript rows shaped
like the chat page's, the owner scripts, Studio on/off from the real
chat_display.js. A steered row carries its receipt as ``data-steer`` (the JSON of
``metadata.steer``), the wiring the integration step adds in chat_page.js.

Studio: the steer reply shows its label line and, on the last covered row only,
the agent's excerpt. Classic: no receipt markup at all. Live steer updates
re-paint receipts only; other rows keep their DOM nodes.
"""
import re

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

LABEL_DELIVERED = "Steered into the live turn · no restart"
LABEL_PENDING = "Sending into the live turn…"
EXCERPT_LABEL = "Agent response excerpt"
REPLY = "On it. The changelog line goes in right after the fix."

ROW_JS = r'''
window.mkRow = (o) => {
  const el = document.createElement('div');
  el.className = 'message ' + o.role + (o.steered ? ' is-steered' : '');
  if (o.mid != null) el.dataset.messageId = String(o.mid);
  if (o.turn) el.dataset.turnId = o.turn;
  if (o.idle) el.dataset.steerIdle = '1';
  if (o.steer) el.dataset.steer = JSON.stringify(o.steer);
  el.innerHTML = '<div class="message-avatar"></div><div class="message-content-wrapper">'
    + '<div class="message-header-row"><span class="message-sender">' + (o.sender || (o.role === 'user' ? 'You' : 'Codex'))
    + '</span><span class="message-timestamp" data-ts="' + (o.ts || 1700000000000) + '"></span></div>'
    + '<div class="message-content">' + o.text + '</div><div class="message-footer"></div></div>';
  return el;
};
window.box = () => document.getElementById('chatMessages');
window.liveSteers = [];
window.liveTurn = 'T1';
window.boot = () => {
  window.threads = CuttleChatThreads.create({
    doc: document, win: window, container: () => box(), sessionId: () => '2641', storage: localStorage,
    escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    renderHandleLink: (h) => h, avatarHtml: () => '', isFollowingLatest: () => true,
    revealTop: () => {},
    liveSteers: () => window.liveSteers, liveTurnId: () => window.liveTurn,
  });
  threads.start(true);
};
'''


def _steer(state, mode="live", reply=None, covers=None, steer_id="s1"):
    return {"v": 1, "steer_id": steer_id, "state": state, "mode": mode,
            "delivered_at": "2026-11-18T10:00:00+00:00" if state == "delivered" else None,
            "reply": reply, "covers": covers if covers is not None else []}


def _rows(steer_row):
    """Turn T1: the user's ask, the agent's root, the steer, the agent's follow-up."""
    return [
        {"role": "user", "mid": 10, "turn": "T1", "text": "Fix the parser."},
        {"role": "assistant", "mid": 11, "turn": "T1", "text": "Working on it."},
        dict(steer_row, role="user", steered=True, mid=12, turn="T1", text="Also add a changelog line."),
        {"role": "assistant", "mid": 13, "turn": "T1", "text": "Done."},
    ]


def _open(browser, static_server, *, studio, rows):  # noqa: F811
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(static_server + "/agent_feed.html")
    page.evaluate(
        "(mode) => { localStorage.clear();"
        " localStorage.setItem('cuttleChatDisplay', mode);"
        " localStorage.setItem('cuttleChatStudioFlag', '1'); }",
        "studio" if studio else "classic",
    )
    page.set_content(
        '<link rel="stylesheet" href="' + static_server + '/css/studio/studio_tokens.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/chat_threads.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/studio/threads.css">'
        '<div id="chatMessages" class="chat-messages" style="height:700px"></div>'
    )
    page.add_script_tag(url=static_server + "/js/shared/chat_display.js")
    page.add_script_tag(url=static_server + "/js/chat/chat_thread_model.js")
    page.add_script_tag(url=static_server + "/js/chat/chat_threads.js")
    page.add_script_tag(content=ROW_JS)
    page.evaluate(
        "(rows) => { rows.forEach((r) => box().appendChild(mkRow(r))); boot(); }",
        rows,
    )
    return page, errors


def _steer_row(page):
    return page.locator("#chatMessages .message.thread-reply--steer")


def _open_thread(page):
    page.locator("#chatMessages .chat-thread-toggle").first.click()


def test_studio_steer_reply_shows_receipt_and_excerpt_once(browser, static_server):  # noqa: F811
    rows = _rows({"steer": _steer("delivered", reply={"text": REPLY, "at": "x"}, covers=[12])})
    page, errors = _open(browser, static_server, studio=True, rows=rows)
    try:
        expect(page.locator("html")).to_have_class(re.compile(r"\bcuttle-studio\b"))
        _open_thread(page)
        receipt = _steer_row(page).locator(".chat-thread-receipt")
        expect(receipt).to_have_count(1)
        expect(receipt.locator(".chat-thread-receipt-text")).to_have_text(LABEL_DELIVERED)
        expect(receipt.locator(".chat-thread-receipt-icon")).to_have_text("↳")
        expect(receipt.locator(".chat-thread-excerpt-label")).to_have_text(EXCERPT_LABEL)
        expect(receipt.locator(".chat-thread-excerpt-text")).to_have_text(REPLY)
        # The excerpt appears exactly once in the thread.
        assert page.locator("#chatMessages .chat-thread-excerpt-text").count() == 1
        # The receipt sits under the steer bubble's own content.
        assert _steer_row(page).locator(".message-content-wrapper > .message-content + .chat-thread-receipt").count() == 1
        assert errors == []
    finally:
        page.close()


def test_classic_adds_no_receipt_markup(browser, static_server):  # noqa: F811
    rows = _rows({"steer": _steer("delivered", reply={"text": REPLY, "at": "x"}, covers=[12])})
    page, errors = _open(browser, static_server, studio=False, rows=rows)
    try:
        _open_thread(page)
        assert _steer_row(page).count() == 1  # still a reply in the thread
        assert page.locator("#chatMessages .chat-thread-receipt").count() == 0
        assert page.locator("#chatMessages .chat-thread-excerpt").count() == 0
        assert not page.evaluate(
            "() => box().innerHTML.includes('Steered into') || box().innerHTML.includes('Agent response')")
        assert errors == []
    finally:
        page.close()


def test_live_steer_update_repaints_only_the_receipt(browser, static_server):  # noqa: F811
    rows = _rows({"steer": _steer("pending")})
    page, errors = _open(browser, static_server, studio=True, rows=rows)
    try:
        # The live extra is the current state for this row; the saved receipt is still pending.
        page.evaluate("() => { window.liveSteers = [{ message_id: 12, v: 1, state: 'pending', mode: 'live', reply: null, covers: [] }]; }")
        _open_thread(page)
        receipt = _steer_row(page).locator(".chat-thread-receipt-text")
        expect(receipt).to_have_text(LABEL_PENDING)
        # Mark an unrelated row: a receipt repaint must not rebuild it.
        page.evaluate("() => { box().children[1].__mark = 'kept'; }")
        page.evaluate("""() => {
            window.liveSteers = [{ message_id: 12, v: 1, state: 'delivered', mode: 'live',
                reply: { text: 'On it. The changelog line goes in right after the fix.', at: 'x' }, covers: [12] }];
            threads.refreshReceipts();
        }""")
        expect(receipt).to_have_text(LABEL_DELIVERED)
        expect(page.locator("#chatMessages .chat-thread-excerpt-text")).to_have_text(REPLY)
        assert page.evaluate("() => box().children[1].__mark") == "kept"
        assert errors == []
    finally:
        page.close()


def test_switching_display_mode_mounts_and_removes_receipts(browser, static_server):  # noqa: F811
    rows = _rows({"steer": _steer("delivered", reply={"text": REPLY, "at": "x"}, covers=[12])})
    page, errors = _open(browser, static_server, studio=True, rows=rows)
    try:
        _open_thread(page)
        expect(page.locator("#chatMessages .chat-thread-receipt")).to_have_count(1)
        page.evaluate("() => CuttleChatDisplay.setMode('classic')")
        expect(page.locator("#chatMessages .chat-thread-receipt")).to_have_count(0)
        page.evaluate("() => CuttleChatDisplay.setMode('studio')")
        expect(page.locator("#chatMessages .chat-thread-receipt")).to_have_count(1)
        assert errors == []
    finally:
        page.close()
