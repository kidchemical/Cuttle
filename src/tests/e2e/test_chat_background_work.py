"""Chat background-work bubble (chat_background_work.js), isolated in a browser."""
import json
import re

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

LIVE = {'success': True, 'generating': False, 'active': False, 'background': {
    'label': 'Waiting on: Wait up to 40 min for fg12', 'process_count': 10, 'detached_count': 1,
    'waits': [{'job_id': 'claude-bg-1821-x', 'label': 'Waiting on: fg12', 'started_at': None}],
    'watches': [{'job_id': 'build', 'label': 'Unity build', 'percent': 40}],
    'schedules': [], 'started_at': None, 'working': True, 'feed': True}}


def test_bubble_stays_last_hides_while_generating_and_stops(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    cancels = []

    def on_cancel(route):
        cancels.append(json.loads(route.request.post_data or '{}'))
        route.fulfill(content_type='application/json', body=json.dumps({'success': True}))

    page.route('**/api/chat-cancel', on_cancel)
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => {
            window.busy = false; window.open = '1821';
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => window.open,
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => window.busy });
        }''')
        page.evaluate('live => bw.sync(live, "1821")', LIVE)
        bubble = page.locator('#chat-background-work')
        expect(bubble).to_be_visible()
        expect(bubble.locator('.chat-bg-title')).to_contain_text('Waiting in background')
        expect(bubble.locator('.chat-bg-label')).to_have_text('Waiting on: Wait up to 40 min for fg12')
        expect(bubble.locator('.chat-bg-meta')).to_have_text('10 processes (1 detached)')
        expect(bubble.locator('.chat-bg-pct')).to_have_text('40%')
        assert bubble.locator('.chat-bg-details').get_attribute('href') == '/agent_feed.html?chat=CH-001821'
        # Not a transcript record, and it stays last when a follow-up bubble lands.
        assert page.locator('#chatMessages .message').count() == 1
        page.evaluate('''() => { const m = document.createElement('div'); m.className = 'message assistant';
            m.textContent = 'follow-up'; document.getElementById('chatMessages').appendChild(m); }''')
        page.wait_for_function("document.getElementById('chatMessages').lastElementChild.id === 'chat-background-work'")
        # A missed poll keeps it; another chat's status is ignored; a turn hides it.
        page.evaluate('() => bw.sync(null, "1821")')
        expect(bubble).to_be_visible()
        page.evaluate('live => bw.sync({ ...live, background: null }, "999")', LIVE)
        expect(bubble).to_be_visible()
        page.evaluate('live => bw.sync({ ...live, generating: true }, "1821")', LIVE)
        expect(bubble).to_have_count(0)
        page.evaluate('live => bw.sync(live, "1821")', LIVE)
        page.locator('.chat-bg-stop').click()
        expect(page.locator('.chat-bg-stop')).to_have_text('Stopped')
        assert cancels == [{'session_id': '1821', 'cancel_jobs': True}]
        page.evaluate('live => bw.sync({ ...live, background: null }, "1821")', LIVE)
        expect(bubble).to_have_count(0)
        assert not errors
    finally:
        page.close()


SCHEDULED = {'success': True, 'generating': False, 'active': False, 'background': {
    'label': 'Supervise CH-001821 · every 15m', 'process_count': 0, 'detached_count': 0,
    'waits': [], 'watches': [], 'started_at': None, 'working': False, 'feed': False,
    'schedules': [{'id': 'op1', 'label': 'Supervise CH-001821', 'detail': 'every 15m',
                   'status': 'running', 'target': 'CH-001821'}]}}


def test_supervising_card_opens_the_supervised_chat(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message assistant">reply</div></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_background_work.js')
        page.evaluate('''() => {
            window.opened = [];
            window.bw = CuttleChatBackgroundWork.create({ win: window, currentSession: () => '1822',
                sessionIdsEqual: (a, b) => String(a) === String(b), isBusy: () => false,
                openChat: (sid) => window.opened.push(sid) });
        }''')
        page.evaluate('live => bw.sync(live, "1822")', SCHEDULED)
        card = page.locator('#chat-background-work')
        expect(card.locator('.chat-bg-title')).to_contain_text('Supervising')
        expect(card).to_have_class(re.compile(r'\bis-clickable\b'))
        card.locator('.chat-bg-open').click()
        card.locator('.chat-bg-meta').click()
        assert page.evaluate('() => window.opened') == ['1821', '1821']
        assert not errors
    finally:
        page.close()


def test_timeline_notice_running_settles_into_saved_row(browser, static_server):
    """Compact flow: running row spins, settles in place, persists to its own chat."""
    page = browser.new_page(viewport={'width': 900, 'height': 700})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    posts = []

    def on_post(route):
        posts.append((route.request.url, json.loads(route.request.post_data or '{}')))
        route.fulfill(content_type='application/json', body=json.dumps({'success': True, 'message_id': 77}))

    page.route('**/api/auth/sessions/*/messages', on_post)
    try:
        page.goto(static_server + '/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">'
                         '<div id="chatMessages"><div class="message user">hi</div></div>')
        page.add_script_tag(url=static_server + '/js/chat/chat_timeline_notice.js')
        page.evaluate('''() => {
            window.sid = '1947'; window.seen = 0;
            window.tn = CuttleChatTimelineNotice.create({ doc: document, win: window,
                escapeHtml: (s) => String(s).replace(/</g, '&lt;'), fetch: (u, o) => fetch(u, o),
                container: () => document.getElementById('chatMessages'), isVisible: () => true,
                autoScroll: () => {}, persistTarget: () => window.sid, onPersisted: (m) => { window.seen = m; } });
            window.row = tn.add({ type: 'compact', state: 'running', label: 'Compacting Claude context…' },
                { kind: 'context-compact', replace: false });
        }''')
        notice = page.locator('.message.system[data-notice-type="compact"] .timeline-notice')
        expect(notice).to_have_class(re.compile('is-running'))
        expect(notice.locator('.timeline-notice-spinner')).to_have_count(1)
        expect(notice.locator('.timeline-notice-elapsed')).to_have_text(re.compile(r'^\ds$'), timeout=3000)
        page.wait_for_timeout(3300)  # running rows never auto-fade
        expect(notice).to_have_count(1)
        assert posts == []  # running rows are never persisted
        # The user switched chats while compaction ran: the row still saves to 1947.
        page.evaluate('''() => { window.sid = '2000';
            tn.update(row, { type: 'compact', label: 'Claude context compacted', detail: '82% → 14%' },
                { persist: true, kind: 'context-compact' }); }''')
        expect(notice).to_have_class(re.compile('is-done'))
        expect(notice).to_have_text('🗜️ Claude context compacted · 82% → 14%'.replace(' · ', ' '))
        page.wait_for_function('window.seen === 77')
        url, body = posts[0]
        assert url.endswith('/api/auth/sessions/1947/messages')
        assert body['content'] == '🗜️ Claude context compacted · 82% → 14%'
        assert body['kind'] == 'context-compact'
        assert body['metadata']['timeline_notice']['detail'] == '82% → 14%'
        # A detached row is not repainted or persisted into whatever chat is open now.
        assert page.evaluate('''() => { const el = tn.add({ type: 'compact', state: 'running', label: 'x' });
            el.remove(); return tn.update(el, { label: 'y' }, { persist: true }); }''') is None
        assert len(posts) == 1
        assert not errors
    finally:
        page.close()
