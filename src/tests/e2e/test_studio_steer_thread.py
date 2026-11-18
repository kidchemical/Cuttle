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
from pathlib import Path

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

LABEL_DELIVERED = "Steered into the live turn · no restart"
LABEL_PENDING = "Sending into the live turn…"
EXCERPT_LABEL = "Agent response excerpt"
LABEL_UNDELIVERED = "Ended without confirmation"
REPLY = "On it. The changelog line goes in right after the fix."
GROUP_REPLY = "Got it: lowercase flag, and the vendored folder is skipped."
IDLE_REPLY = "The build is green."
REPO = Path(__file__).resolve().parents[3]
SHOTS = REPO / "temp" / "studio_shots" / "L6"
MOCK = Path("/mnt/galaxy/Dev/Cuttle Promo/v4/project/film/scenes/steer.html")

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


def _open(browser, static_server, *, studio, rows, live=None):  # noqa: F811
    page = browser.new_page(viewport={"width": 1000, "height": 1350})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(static_server + "/agent_feed.html")
    page.evaluate(
        "(mode) => { localStorage.clear();"
        " localStorage.setItem('cuttleChatDisplay', mode);"
        " localStorage.setItem('cuttleChatStudioFlag', '1');"
        " localStorage.setItem('theme', 'light'); }",
        "studio" if studio else "classic",
    )
    page.set_content(
        '<link rel="stylesheet" href="' + static_server + '/css/studio/studio_tokens.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/chat_threads.css">'
        '<link rel="stylesheet" href="' + static_server + '/css/studio/threads.css">'
        '<div id="chatMessages" class="chat-messages" style="height:1300px"></div>'
    )
    # Light surface (the Studio base token) unless the dark class is set later in a test.
    page.add_style_tag(content="html:not(.dark-mode) body { background: #fbf8f3; }")
    page.add_script_tag(url=static_server + "/js/shared/chat_display.js")
    page.add_script_tag(url=static_server + "/js/chat/chat_thread_model.js")
    page.add_script_tag(url=static_server + "/js/chat/chat_threads.js")
    page.add_script_tag(content=ROW_JS)
    page.evaluate(
        "([rows, live]) => { rows.forEach((r) => box().appendChild(mkRow(r)));"
        " if (live) window.liveSteers = live; boot(); }",
        [rows, live],
    )
    return page, errors


def _live(mid, state, *, reply=None, covers=None, steer_id="a"):
    """One entry of the live-status `steers` extra (current turn T1)."""
    entry = _steer(state, reply=reply, covers=covers, steer_id=steer_id)
    entry.update(message_id=mid, turn_id="T1")
    return entry


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), animations="disabled")


# One live turn (T1) with every receipt case: a steer that goes pending → delivered
# with an excerpt, a grouped pair, an idle steer into a background run, and an undelivered one.
SCENARIO = [
    {"role": "user", "mid": 10, "turn": "T1", "text": "Fix the parser."},
    {"role": "assistant", "mid": 11, "turn": "T1", "text": "Working on it."},
    {"role": "user", "steered": True, "mid": 12, "turn": "T1", "text": "Also add a changelog line.",
     "steer": _steer("pending", steer_id="a", covers=[12])},
    {"role": "user", "steered": True, "mid": 13, "turn": "T1", "text": "Use the lowercase flag too.",
     "steer": _steer("delivered", steer_id="b", covers=[13, 14])},
    {"role": "user", "steered": True, "mid": 14, "turn": "T1", "text": "And skip the vendored folder.",
     "steer": _steer("delivered", steer_id="c", reply={"text": GROUP_REPLY, "at": "x"}, covers=[13, 14])},
    {"role": "assistant", "mid": 16, "turn": "T1", "text": "Done."},
    {"role": "user", "steered": True, "mid": 17, "turn": "T1", "text": "Also rename the flag.",
     "steer": _steer("undelivered", steer_id="e")},
    # Sent while the chat looked idle into a background run: a plain row, and its answer is its own bubble.
    {"role": "user", "steered": True, "idle": True, "mid": 15, "turn": "T0", "text": "Any news on the build?",
     "steer": _steer("delivered", mode="detached", reply={"text": IDLE_REPLY, "at": "x"}, covers=[15], steer_id="d")},
    {"role": "assistant", "mid": 18, "turn": "T0", "text": IDLE_REPLY},
]


def _row(page, mid):
    return page.locator(f'#chatMessages .message[data-message-id="{mid}"]')


def _excerpts(page):
    return page.evaluate("() => Array.from(document.querySelectorAll('#chatMessages .chat-thread-excerpt-text'))"
                         ".map((n) => n.textContent)")


def test_steer_thread_scenario_in_studio(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=True, rows=SCENARIO,
                         live=[_live(12, "pending", steer_id="a")])
    try:
        _open_thread(page)
        # Pending on the live turn: a status, never a spinner.
        expect(_row(page, 12).locator(".chat-thread-receipt-text")).to_have_text(LABEL_PENDING)
        assert _excerpts(page) == [GROUP_REPLY]  # the grouped pair's reply, on its last row only
        # The live extra turns delivered with the reply; only this row changes.
        page.evaluate("""() => {
            window.liveSteers = [{ message_id: 12, turn_id: 'T1', v: 1, steer_id: 'a', state: 'delivered', mode: 'live',
                delivered_at: '2026-11-18T10:00:00+00:00', reply: { text: %s, at: 'x' }, covers: [12] }];
            threads.refreshReceipts();
        }""" % json_str(REPLY))
        expect(_row(page, 12).locator(".chat-thread-receipt-text")).to_have_text(LABEL_DELIVERED)
        expect(_row(page, 12).locator(".chat-thread-excerpt-label")).to_have_text(EXCERPT_LABEL)
        expect(_row(page, 12).locator(".chat-thread-excerpt-text")).to_have_text(REPLY)
        # Grouped pair: the label on both rows, the excerpt only on the last covered row.
        expect(_row(page, 13).locator(".chat-thread-receipt-text")).to_have_text(LABEL_DELIVERED)
        assert _row(page, 13).locator(".chat-thread-excerpt").count() == 0
        expect(_row(page, 14).locator(".chat-thread-excerpt-text")).to_have_text(GROUP_REPLY)
        # Idle steer into a background run: a plain row, never an excerpt; its answer is the bubble below.
        assert _row(page, 15).locator(".chat-thread-receipt").count() == 0
        assert _row(page, 15).locator(".chat-thread-excerpt").count() == 0
        # Undelivered: ended without confirmation, nothing asks to resend.
        expect(_row(page, 17).locator(".chat-thread-receipt-text")).to_have_text(LABEL_UNDELIVERED)
        # Each excerpt appears exactly once.
        assert sorted(_excerpts(page)) == sorted([REPLY, GROUP_REPLY])
        _shot(page, "steer-thread-studio.png")
        page.evaluate("() => document.documentElement.classList.add('dark-mode')")
        _shot(page, "steer-thread-studio-dark.png")
        assert errors == []
    finally:
        page.close()


def test_steer_thread_classic_is_unchanged(browser, static_server):  # noqa: F811
    page, errors = _open(browser, static_server, studio=False, rows=SCENARIO,
                         live=[_live(12, "pending", steer_id="a")])
    try:
        _open_thread(page)
        assert page.locator("#chatMessages .chat-thread-receipt").count() == 0
        assert page.locator("#chatMessages .chat-thread-excerpt").count() == 0
        assert not page.evaluate("() => /Steered into|Agent response|Sending into/.test(document.getElementById('chatMessages').innerHTML)")
        # The steers are still replies under the turn's bar, and the rows keep their text.
        assert page.locator("#chatMessages .message.thread-reply--steer").count() == 4
        expect(_row(page, 12).locator(".message-content")).to_have_text("Also add a changelog line.")
        _shot(page, "steer-thread-classic.png")
        assert errors == []
    finally:
        page.close()


def test_mock_frame_for_the_side_by_side(browser):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    # Offline: only the local scene is loaded (its web fonts are skipped).
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file://") else route.abort())
    page.goto(MOCK.as_uri(), wait_until="load")
    # The scene parks until the recorder releases it; release it the same way, then shoot
    # once both reply bubbles are in (the second lands ~1s into the scene, 0:26 of the film).
    page.wait_for_function("typeof window.__release === 'function'", timeout=10000)
    page.evaluate("() => window.__release()")
    page.wait_for_selector("#r1.show", state="attached", timeout=10000)
    page.wait_for_timeout(1500)
    _shot(page, "steer-thread-mock.png")
    page.close()


def json_str(value):
    import json
    return json.dumps(value)


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
