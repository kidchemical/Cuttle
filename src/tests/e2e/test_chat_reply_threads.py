"""Reply threads (chat_threads.js) in a browser, over transcript rows shaped
like the chat page's: steers and follow-ups fold under their turn's bubble,
open threads never close by themselves, the live bubble → saved reply swap
keeps the thread open, unread replies show a dot + preview, and rows are
never reordered (CH-…-N numbering is DOM order)."""
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

ROW_JS = r'''
window.row = (role, text, o = {}) => {
  const el = document.createElement('div');
  el.className = 'message ' + role + (o.steered ? ' is-steered' : '');
  if (o.id) el.id = o.id;
  if (o.mid != null) el.dataset.messageId = String(o.mid);
  if (o.turn) el.dataset.turnId = o.turn;
  if (o.idle) el.dataset.steerIdle = '1';
  el.innerHTML = '<div class="message-avatar"></div><div class="message-content-wrapper">'
    + '<div class="message-header-row"><span class="message-sender">' + (o.sender || (role === 'user' ? 'You' : 'Claude Code'))
    + '</span><span class="message-timestamp" data-ts="' + (o.ts || Date.now()) + '"></span></div>'
    + '<div class="message-content">' + text + '</div><div class="message-footer"></div></div>';
  if (o.cards) el.__cuttleSubagentCards = o.cards;
  return el;
};
window.box = () => document.getElementById('chatMessages');
window.add = (el) => { box().appendChild(el); return el; };
window.sid = '2161';
window.following = true;
window.revealed = [];
window.boot = () => {
  window.threads = CuttleChatThreads.create({
    doc: document, win: window, container: () => box(), sessionId: () => window.sid, storage: localStorage,
    escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    renderHandleLink: (h) => '<a class="chat-handle-link" data-chat-handle="' + h + '">' + h + '</a>',
    avatarHtml: () => '', isFollowingLatest: () => window.following,
    revealTop: (el) => window.revealed.push(el.dataset.messageId),
  });
  threads.start(true);
};
window.order = () => Array.from(box().querySelectorAll('.message')).map((el) => el.querySelector('.message-content').textContent);
'''


def _page(browser, static_server):
    page = browser.new_page(viewport={'width': 1000, 'height': 800})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/agent_feed.html')
    page.evaluate('() => localStorage.clear()')
    page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                     '<link rel="stylesheet" href="' + static_server + '/css/chat_threads.css">'
                     '<div id="chatMessages" class="chat-messages" style="height:700px"></div>')
    page.add_script_tag(url=static_server + '/js/chat/chat_thread_model.js')
    page.add_script_tag(url=static_server + '/js/chat/chat_threads.js')
    page.add_script_tag(content=ROW_JS)
    return page, errors


def _history(page, boot=True):
    page.evaluate('''(boot) => {
        add(row('user', 'Fix the login bug', { mid: 10 }));
        add(row('assistant', 'Fixed it.', { mid: 11, turn: 'T1' }));
        add(row('user', '/claude also check the token refresh', { mid: 12, steered: true }));
        add(row('user', "/claude don't touch the schema", { mid: 13, steered: true, turn: 'T1' }));
        add(row('assistant', 'Background tests pass.', { mid: 14, turn: 'T1' }));
        add(row('user', 'Next thing', { mid: 15 }));
        add(row('assistant', 'Sure.', { mid: 16, turn: 'T2' }));
        if (boot) window.boot();
    }''', boot)


def _reload(page):
    """Leave the chat and come back: state is read again from storage."""
    page.evaluate('() => { threads.flush(); window.sid = "other"; box().innerHTML = ""; }')
    page.evaluate('() => { window.sid = "2161"; }')
    _history(page, boot=False)


def test_history_folds_replies_under_the_turn_and_keeps_row_order(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        _history(page)
        before = page.evaluate('order()')
        bar = page.locator('.message[data-message-id="11"] .chat-thread-bar')
        expect(bar.locator('.chat-thread-label')).to_have_text('3 replies')
        expect(bar.locator('.chat-thread-detail')).to_have_text('2 steers · 1 follow-up')
        for mid in ('12', '13', '14'):
            expect(page.locator(f'.message[data-message-id="{mid}"]')).to_be_hidden()
        expect(page.locator('.message[data-message-id="15"]')).to_be_visible()
        assert page.locator('.chat-thread-bar').count() == 1  # T2 has no replies
        # Open → replies show in place, label flips; rows never move.
        bar.locator('.chat-thread-toggle').click()
        expect(bar.locator('.chat-thread-label')).to_have_text('Hide replies')
        expect(page.locator('.message[data-message-id="12"]')).to_be_visible()
        expect(page.locator('.message[data-message-id="14"]')).to_be_visible()
        assert page.locator('.message[data-message-id="14"]').get_attribute('class').count('thread-reply-last') == 1
        assert page.evaluate('order()') == before
        # Content changes inside an open thread (sync) never close it.
        page.evaluate('''() => { document.querySelector('.message[data-message-id="14"] .message-content').textContent = 'Background tests pass (edited).'; }''')
        page.wait_for_timeout(50)
        expect(page.locator('.message[data-message-id="14"]')).to_be_visible()
        # The choice survives a reload of the transcript.
        _reload(page)
        expect(page.locator('.message[data-message-id="12"]')).to_be_visible()
        page.locator('.message[data-message-id="11"] .chat-thread-toggle').click()
        expect(page.locator('.message[data-message-id="12"]')).to_be_hidden()
        assert not errors
    finally:
        page.close()


def test_live_turn_thread_opens_and_stays_open_when_the_answer_lands(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        page.evaluate('''() => {
            add(row('user', 'Refactor auth', { mid: 20 }));
            boot();
            const live = add(row('assistant', '<div class="typing-status">Editing files…</div>', { id: 'typing-indicator' }));
            live.dataset.turnId = 'T5';
            add(row('user', '/codex keep the API stable. ' + 'Also more detail. '.repeat(300), { steered: true }));  // optimistic, unsaved
        }''')
        steer = page.locator('.message.is-steered')
        expect(steer).to_be_visible()
        bar = page.locator('#typing-indicator .chat-thread-bar')
        expect(bar.locator('.chat-thread-label')).to_have_text('Hide replies')
        expect(bar.locator('.chat-thread-status')).to_contain_text('working')
        # The final reply replaces the live bubble (saved row id 21 above the steer).
        page.evaluate('''() => {
            const live = document.getElementById('typing-indicator');
            const done = row('assistant', 'Done. ' + 'Long answer. '.repeat(400), { mid: 21, turn: 'T5' });
            live.replaceWith(done);
            box().scrollTop = box().scrollHeight;  // the page follows the bottom
        }''')
        expect(steer).to_be_visible()
        root_bar = page.locator('.message[data-message-id="21"] .chat-thread-bar')
        expect(root_bar.locator('.chat-thread-label')).to_have_text('Hide replies')
        expect(root_bar.locator('.chat-thread-status')).to_have_count(0)
        # The tall steer pushed the answer out of view: its start is brought back.
        page.wait_for_function('window.revealed.length === 1')
        assert page.evaluate('window.revealed') == ['21']
        assert not errors
    finally:
        page.close()


def test_reply_arriving_in_a_closed_thread_shows_a_quiet_dot_and_preview(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        _history(page)
        bar = page.locator('.message[data-message-id="11"] .chat-thread-bar')
        expect(bar.locator('.chat-thread-status')).to_have_count(0)  # history on first sight: read
        expect(bar.locator('.chat-thread-preview')).to_have_count(0)
        # A detached follow-up of T1 lands while the thread is closed.
        page.evaluate('''() => { box().insertBefore(row('assistant', 'CI is green on main.', { mid: 17, turn: 'T1' }),
            document.querySelector('.message[data-message-id="15"]')); }''')
        expect(bar.locator('.chat-thread-status.is-unread')).to_have_text('1 new')
        expect(bar.locator('.chat-thread-preview')).to_have_text('Claude Code: CI is green on main.')
        expect(bar.locator('.chat-thread-label')).to_have_text('4 replies')
        # Still unread after the transcript reloads.
        _reload(page)
        page.evaluate('''() => { box().insertBefore(row('assistant', 'CI is green on main.', { mid: 17, turn: 'T1' }),
            document.querySelector('.message[data-message-id="15"]')); }''')
        expect(bar.locator('.chat-thread-status.is-unread')).to_have_text('1 new')
        # Opening reads it.
        bar.locator('.chat-thread-toggle').click()
        bar.locator('.chat-thread-toggle').click()
        expect(bar.locator('.chat-thread-status')).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_reveal_opens_a_closed_thread_and_flag_off_restores_flat_rows(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        _history(page)
        target = page.locator('.message[data-message-id="13"]')
        expect(target).to_be_hidden()
        page.evaluate('''() => document.querySelector('.message[data-message-id="13"]')
            .dispatchEvent(new CustomEvent('cuttle:reveal', { bubbles: true }))''')
        expect(target).to_be_visible()
        flag = '''async (body) => threads.syncFlag(async () => ({ json: async () => body }))'''
        # A server that does not list the flag yet keeps the default (on).
        assert page.evaluate(flag, {'success': True, 'flags': [{'id': 'other', 'enabled': False}]}) is True
        assert page.locator('.chat-thread-bar').count() == 1
        # The kill switch, or the flag listed off, turns threads off.
        assert page.evaluate(flag, {'success': True, 'kill_switch': True, 'flags': []}) is False
        assert page.evaluate(flag, {'success': True, 'flags': [{'id': 'chat_reply_threads', 'enabled': True}]}) is True
        assert page.evaluate(flag, {'success': True, 'flags': [{'id': 'chat_reply_threads', 'enabled': False}]}) is False
        assert page.locator('.chat-thread-bar').count() == 0
        assert page.locator('.thread-reply, .thread-reply-hidden, .thread-root').count() == 0
        assert not errors
    finally:
        page.close()


def test_fleet_cards_do_not_create_reply_threads(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        page.evaluate('''() => {
            add(row('user', 'Review this three ways', { mid: 30 }));
            add(row('assistant', 'Synthesis…', { mid: 31, turn: 'T7', cards: [
              { handle: 'CH-000501', label: 'Scout', displayName: 'Scout', outcome: 'done', detail: 'Found two bugs in auth.' },
              { handle: 'CH-000502', label: 'Judge', outcome: 'running', generating: true, summary: 'Reading…' },
            ] }));
            boot();
        }''')
        expect(page.locator('.chat-thread-bar')).to_have_count(0)
        expect(page.locator('.chat-thread-subagents')).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_reload_mid_turn_keeps_the_steer_threaded_and_visible(browser, static_server):
    """Reloaded while the turn runs: the saved steer comes back as a row and the
    working bubble is appended below it. The bubble moves back above its steer
    (its turn row id is older), the thread is live and open, and it stays open
    when the answer replaces the bubble — the steer never disappears."""
    page, errors = _page(browser, static_server)
    try:
        page.evaluate('''() => {
            add(row('user', 'ok im going to try it now', { mid: 40 }));
            add(row('user', '/claude test steering', { mid: 42, steered: true, turn: 'T9' }));
            boot();
        }''')
        steer = page.locator('.message[data-message-id="42"]')
        expect(steer).to_be_visible()
        order = '''() => Array.from(box().children).map((el) => el.id || el.dataset.messageId)'''
        # The remote working bubble appears last, then live status stamps its turn row.
        page.evaluate('''() => {
            const live = add(row('assistant', '<div class="typing-status">Running tests…</div>', { id: 'typing-indicator-remote' }));
            live.dataset.turnMessageId = '41';
            live.dataset.turnId = 'T9';
        }''')
        assert page.evaluate(order) == ['40', 'typing-indicator-remote', '42']
        expect(steer).to_be_visible()
        bar = page.locator('#typing-indicator-remote .chat-thread-bar')
        expect(bar.locator('.chat-thread-label')).to_have_text('Hide replies')
        # The answer replaces the working bubble: still open, steer still shown.
        page.evaluate('''() => document.getElementById('typing-indicator-remote')
            .replaceWith(row('assistant', 'Steering works.', { mid: 41, turn: 'T9' }))''')
        expect(page.locator('.message[data-message-id="41"] .chat-thread-label')).to_have_text('Hide replies')
        expect(steer).to_be_visible()
        assert page.evaluate(order) == ['40', '41', '42']
        assert not errors
    finally:
        page.close()


def test_thread_forming_around_rows_on_screen_opens(browser, static_server):
    """A steer shown as a plain row (its root not on screen yet) never vanishes
    when the root arrives and the thread forms."""
    page, errors = _page(browser, static_server)
    try:
        page.evaluate('''() => {
            add(row('user', 'Fix it', { mid: 50 }));
            add(row('user', '/codex also the docs', { mid: 52, steered: true, turn: 'T8' }));
            boot();
        }''')
        steer = page.locator('.message[data-message-id="52"]')
        expect(steer).to_be_visible()
        page.evaluate('''() => box().insertBefore(row('assistant', 'Done.', { mid: 51, turn: 'T8' }),
            document.querySelector('.message[data-message-id="52"]'))''')
        expect(page.locator('.message[data-message-id="51"] .chat-thread-label')).to_have_text('Hide replies')
        expect(steer).to_be_visible()
        assert not errors
    finally:
        page.close()


def test_hide_replies_at_the_end_of_an_open_thread_closes_it(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        _history(page)
        page.locator('.message[data-message-id="11"] .chat-thread-toggle').click()
        tail = page.locator('.message[data-message-id="14"] .chat-thread-tail .chat-thread-toggle')
        expect(tail).to_be_visible()
        assert page.locator('.chat-thread-tail').count() == 1  # only under the last reply
        tail.click()
        for mid in ('12', '13', '14'):
            expect(page.locator(f'.message[data-message-id="{mid}"]')).to_be_hidden()
        expect(page.locator('.message[data-message-id="11"] .chat-thread-label')).to_have_text('3 replies')
        assert page.locator('.chat-thread-tail').count() == 0
        assert not errors
    finally:
        page.close()


def test_idle_steer_into_a_detached_run_is_a_new_message_not_a_reply(browser, static_server):
    page, errors = _page(browser, static_server)
    try:
        page.evaluate('''() => {
            add(row('user', 'Why the window?', { mid: 20 }));
            add(row('assistant', 'Fixed; still waiting on the bench.', { mid: 21, turn: 'T5' }));
            add(row('user', '/claude keep going', { mid: 22, steered: true, idle: true, turn: 'T5' }));
            add(row('assistant', 'On it.', { mid: 23, turn: 'T5' }));
            window.boot();
        }''')
        for mid in ('21', '22', '23'):
            expect(page.locator(f'.message[data-message-id="{mid}"]')).to_be_visible()
        assert page.locator('.chat-thread-bar').count() == 0
        assert 'thread-reply' not in (page.locator('.message[data-message-id="22"]').get_attribute('class') or '')
        assert not errors
    finally:
        page.close()

