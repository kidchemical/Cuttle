"""Offline regression for timeline polling while reading expanded details."""
import json

from playwright.sync_api import expect
from .test_shared_diff_modal import browser, static_server  # noqa: F401


def test_poll_preserves_reader_position_and_loaded_details(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 800})
    events = [dict(id=i, detail_id=i, seq=i, rev=1, kind='tool', t=i,
                   summary=f'Step {i}') for i in range(1, 41)]
    detail_requests = []

    def handle(route):
        if '/events/' in route.request.url:
            detail_requests.append(route.request.url)
            data = {'detail': {'text': '\n'.join(['Expanded output'] * 60)}}
        else:
            data = dict(event_store=True, executing=True, events=events,
                        has_more=False, next_cursor=events[-1]['id'])
        route.fulfill(content_type='application/json', body=json.dumps(data))

    page.route('**/api/agent-events/**', handle)
    page.add_init_script('''
        window.setInterval = fn => { window.pollLog = fn; return 1; };
    ''')
    try:
        page.goto(static_server + '/query_log.html?id=scroll-test')
        # Constrain the same body used by the in-chat overlay.
        page.add_style_tag(content='#queryLogBody { height: 500px; flex: none; overflow: auto; }')
        expect(page.locator('details.query-log-event')).to_have_count(40)
        page.locator('details.query-log-event summary').first.click()
        expect(page.locator('.query-log-event-body').first).to_contain_text('Expanded output')
        page.evaluate('''() => {
            window.savedDetail = document.querySelector('details.query-log-event');
            const body = document.getElementById('queryLogBody');
            body.scrollTop = 700;
            window.savedTop = body.scrollTop;
        }''')
        events.append(dict(id=41, detail_id=41, seq=41, rev=1, kind='tool', t=41, summary='New step'))
        page.evaluate('pollLog()')
        expect(page.locator('details.query-log-event')).to_have_count(41)
        assert page.evaluate('document.querySelector("details.query-log-event") === savedDetail')
        assert page.evaluate('savedDetail.open')
        assert page.evaluate('document.getElementById("queryLogBody").scrollTop === savedTop')
        assert len(detail_requests) == 1
        # Readers following the live tail continue following appended entries.
        page.evaluate('document.getElementById("queryLogBody").scrollTop = 1e9')
        events.append(dict(id=42, detail_id=42, seq=42, rev=1, kind='tool', t=42, summary='Tail step'))
        page.evaluate('pollLog()')
        expect(page.locator('details.query-log-event')).to_have_count(42)
        assert page.evaluate('''() => {
            const b = document.getElementById('queryLogBody');
            return b.scrollHeight - b.scrollTop - b.clientHeight < 2;
        }''')
    finally:
        page.close()
