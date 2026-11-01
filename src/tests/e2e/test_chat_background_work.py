"""Chat background-work bubble (chat_background_work.js), isolated in a browser."""
import json

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
