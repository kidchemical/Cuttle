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
        if '/log/stream' in route.request.url:
            return route.fulfill(status=204)
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
        page.wait_for_function('typeof window.pollLog === "function"')
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


def _expanded_log_page(browser, static_server, events, details):
    page = browser.new_page(viewport={'width': 900, 'height': 800})

    def handle(route):
        url = route.request.url
        if '/log/stream' in url:
            return route.fulfill(status=204)
        if '/events/' in url:
            details.append(url)
            n = int(url.rsplit('/', 1)[1])
            rev = next((e['rev'] for e in events if e['id'] == n), 1)
            data = {'detail': {'text': '\n'.join([f'Output r{rev}'] * 80)}}
        else:
            data = dict(event_store=True, executing=True, events=events,
                        has_more=False, next_cursor=events[-1]['id'])
        route.fulfill(content_type='application/json', body=json.dumps(data))

    page.route('**/api/agent-events/**', handle)
    page.add_init_script('window.setInterval = fn => { window.pollLog = fn; return 1; };')
    page.goto(static_server + '/query_log.html?id=inner-scroll')
    page.add_style_tag(content='#queryLogBody { height: 500px; flex: none; overflow: auto; }')
    return page


def test_poll_keeps_scroll_inside_expanded_entries(browser, static_server):
    events = [dict(id=i, detail_id=i, seq=i, rev=1, kind='thinking', t=i,
                   summary=f'Step {i}') for i in range(1, 6)]
    details = []
    page = _expanded_log_page(browser, static_server, events, details)
    try:
        expect(page.locator('details.query-log-event')).to_have_count(5)
        page.wait_for_function('typeof window.pollLog === "function"')
        page.locator('details.query-log-event summary').first.click()
        pre = page.locator('details.query-log-event').first.locator('.query-log-pre')
        expect(pre).to_contain_text('Output r1')
        page.evaluate('''() => {
            window.savedRow = document.querySelector('details.query-log-event');
            window.savedPre = savedRow.querySelector('.query-log-pre');
            savedPre.scrollTop = 300;
            window.savedInner = savedPre.scrollTop;
        }''')
        assert page.evaluate('savedInner') > 0
        # Appending a row must not detach (and so rewind) the expanded entry.
        events.append(dict(id=6, detail_id=6, seq=6, rev=1, kind='tool', t=6, summary='New'))
        page.evaluate('pollLog()')
        expect(page.locator('details.query-log-event')).to_have_count(6)
        assert page.evaluate('savedPre.isConnected && savedPre.scrollTop === savedInner')
        # A live revision of the open row itself patches it in place.
        events[0] = dict(events[0], rev=2, summary='Step 1 revised')
        page.evaluate('pollLog()')
        expect(pre).to_contain_text('Output r2')
        assert page.evaluate('document.querySelector("details.query-log-event") === savedRow')
        assert page.evaluate('savedRow.open')
        assert page.evaluate('''() => savedRow.querySelector('.query-log-pre').scrollTop === savedInner''')
    finally:
        page.close()


def test_changes_tab_poll_keeps_open_diff_and_scroll(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 800})
    events = [dict(id=1, seq=1, rev=1, kind='edit', t=1, summary='edit')]
    files = [dict(path=f'f{i}.py', status='reported', additions=1, deletions=0) for i in range(30)]
    patch = '\n'.join(f'diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n-a\n+b' for i in range(30))
    changes_calls = []

    def handle(route):
        url = route.request.url
        if '/log/stream' in url:
            return route.fulfill(status=204)
        if url.endswith('/changes'):
            changes_calls.append(url)
            data = dict(status='running', verdict='consistent', files=list(files),
                        counts={'reported': len(files)}, snapshot_event_id=99)
        elif '/events/99' in url:
            data = {'detail': {'text': patch}}
        else:
            data = dict(event_store=True, executing=True, events=events,
                        has_more=False, next_cursor=events[-1]['id'])
        route.fulfill(content_type='application/json', body=json.dumps(data))

    page.route('**/api/agent-events/**', handle)
    page.add_init_script('window.setInterval = fn => { window.pollLog = fn; return 1; };')
    try:
        page.goto(static_server + '/query_log.html?id=changes-scroll')
        page.add_style_tag(content='#queryLogBody { height: 400px; flex: none; overflow: auto; }')
        expect(page.locator('.query-log-event[data-row]')).to_have_count(1)
        page.wait_for_function('typeof window.pollLog === "function"')
        page.locator('[data-tab="changes"]').click()
        expect(page.locator('details.query-changes-file')).to_have_count(30)
        page.locator('details.query-changes-file[data-path="f20.py"] summary').click()
        expect(page.locator('[data-path="f20.py"] .cuttle-diff')).to_have_count(1)
        page.evaluate('''() => {
            const body = document.getElementById('queryLogBody');
            body.scrollTop = 600;
            window.savedTop = body.scrollTop;
            window.savedTable = document.querySelector('[data-path="f20.py"] .cuttle-diff');
        }''')
        # Unchanged model: polling must not repaint at all.
        events.append(dict(id=2, seq=2, rev=1, kind='edit', t=2, summary='edit 2'))
        page.evaluate('pollLog()')
        page.wait_for_function('() => !document.getElementById("queryLogBody")._queryChanges.loading')
        assert page.evaluate('savedTable.isConnected')
        assert page.evaluate('document.getElementById("queryLogBody").scrollTop === savedTop')
        # Changed model: repaint keeps the open diff in place and the reader's spot.
        files.append(dict(path='new.py', status='unreported'))
        events.append(dict(id=3, seq=3, rev=1, kind='edit', t=3, summary='edit 3'))
        page.evaluate('pollLog()')
        expect(page.locator('details.query-changes-file')).to_have_count(31)
        assert page.evaluate('document.querySelector(\'[data-path="f20.py"]\').open')
        expect(page.locator('[data-path="f20.py"] .cuttle-diff')).to_have_count(1)
        assert page.evaluate('document.getElementById("queryLogBody").scrollTop === savedTop')
        assert len(changes_calls) >= 2
    finally:
        page.close()


def test_live_run_uses_push_stream_and_omits_repeated_metadata(browser, static_server):
    page = browser.new_page(viewport={'width': 900, 'height': 800})
    log_urls, stream_urls = [], []
    meta = {'harness': {'agent_id': 'codex', 'label': 'Codex'}, 'sent': {'text': 'x' * 5000, 'chars': 5000}}
    first = dict(meta, event_store=True, executing=True, has_more=False, next_cursor=2,
                 stream_cursor=7, meta_rev='r1', query_id='push',
                 events=[dict(id=1, detail_id=1, seq=1, rev=1, kind='thinking', t=1, summary='think'),
                         dict(id=2, detail_id=2, seq=2, rev=1, kind='tool', t=2, summary='tool')])
    push = [
        {'events': [dict(id=2, detail_id=2, seq=2, rev=2, kind='tool', t=2, summary='tool done'),
                    dict(id=3, detail_id=3, seq=3, rev=1, kind='edit', t=3, summary='edit')],
         'executing': True, 'meta_rev': 'r1', 'cursor': 9},
        {'events': [dict(id=4, detail_id=4, seq=4, rev=1, kind='finish', t=4, summary='done')],
         'meta': dict(meta, success=True, total_tokens=42), 'executing': False, 'meta_rev': 'r2', 'cursor': 11},
    ]
    sse = ''.join(f"id: {m['cursor']}\ndata: {json.dumps(m)}\n\n" for m in push) + 'event: end\ndata: {}\n\n'

    def handle(route):
        url = route.request.url
        if '/log/stream' in url:
            stream_urls.append(url)
            return route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'}, body=sse)
        if '/log' in url:
            log_urls.append(url)
            return route.fulfill(content_type='application/json', body=json.dumps(first))
        route.fulfill(content_type='application/json', body=json.dumps({'detail': {}}))

    page.route('**/api/agent-events/**', handle)
    page.add_init_script('window.setInterval = fn => { window.pollLog = fn; return 1; };')
    try:
        page.goto(static_server + '/query_log.html?id=push')
        expect(page.locator('.query-log-event[data-row]')).to_have_count(4)
        expect(page.locator('#queryLogMeta')).to_contain_text('42 tok')
        expect(page.locator('.query-log-event[data-row="1"]')).to_contain_text('tool done')
        assert page.evaluate('typeof window.pollLog') == 'undefined'
        assert len(log_urls) == 1 and len(stream_urls) == 1
        assert 'after=7' in stream_urls[0] and 'meta_rev=r1' in stream_urls[0]
        expect(page.locator('.query-log-live')).to_have_count(0)
    finally:
        page.close()
