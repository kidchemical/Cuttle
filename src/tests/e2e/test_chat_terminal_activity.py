"""Real Chromium rendering of the shell hub/completed-reply race.

Uses the repository's isolated static/API browser fixture. No real login DB,
provider, Flask lifetime, or external service is involved.
"""
import json
import time
from urllib.parse import urlparse

import pytest

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)


@pytest.mark.parametrize('reply', ['The work is complete.', '[FAIL] Cursor Agent: upstream policy refusal'])
@pytest.mark.parametrize('viewport', [{'width': 1280, 'height': 800}, {'width': 390, 'height': 844}])
def test_completed_reply_ignores_replayed_shell_activity(browser, static_server, tmp_path, reply, viewport):
    now = time.time()
    messages = [
        {'id': 1, 'role': 'user', 'content': 'Check the regression', 'timestamp': (now - 3) * 1000},
        {'id': 2, 'role': 'assistant', 'content': reply, 'timestamp': now * 1000,
         'metadata': {'query_id': 'completed'}},
    ]
    api = IsolatedAPI()
    errors = []
    page = browser.new_page(viewport=viewport)
    apply_request_guard(page.context, static_server)
    page.on('pageerror', lambda err: errors.append(str(err)))
    def handle(route):
        path = urlparse(route.request.url).path
        if path.endswith('/messages'):
            data = {'success': True, 'session_name': 'Regression QA', 'messages': messages}
        elif path == '/api/chat-live-status':
            data = {'active': False, 'generating': False, 'status': None}
        elif path == '/api/auth/sessions':
            data = {'success': True, 'sessions': [{'id': 42, 'session_name': 'Regression QA'}]}
        else:
            return api.handle(route)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data))
    page.route(f'{static_server}/api/**', handle)
    # A minimal same-origin host creates the normal app-shell iframe boundary;
    # all chat scripts and the message listener are the production page.
    page.route(f'{static_server}/qa-host.html', lambda route: route.fulfill(
        content_type='text/html', body='<iframe style="border:0;width:100%;height:96vh" src="/chat_page.html?chat=42"></iframe>'))
    try:
        page.goto(static_server + '/qa-host.html', wait_until='domcontentloaded')
        frame = page.frame_locator('iframe')
        frame.locator('#chatMessages .message.assistant').wait_for(state='visible')
        assert frame.locator('#chatMessages .message.assistant').count() == 1
        chat = page.frames[1]
        # Track even brief flashes, not just the final DOM after polling clears it.
        chat.evaluate('''() => {
            window.__qaGhosts = [];
            new MutationObserver(records => {
                for (const r of records) for (const n of r.addedNodes) {
                    if (n.nodeType === 1 && (n.id === 'typing-indicator-remote'
                        || n.querySelector('#typing-indicator-remote'))) window.__qaGhosts.push(n.textContent);
                }
            }).observe(document.getElementById('chatMessages'), {childList: true, subtree: true});
        }''')
        for label in ['Starting Cursor Agent…', 'thinking: old progress', 'tool 1: grep'] * 3:
            chat.evaluate('''live => window.dispatchEvent(new MessageEvent('message', {data: {
                type: 'cuttle-live-status', sessionId: '42', status: live
            }}))''', {'active': True, 'generating': True, 'status': label,
                      'updated_at': now - 1, 'query_id': 'completed'})
            page.wait_for_timeout(100)
            assert frame.locator('#typing-indicator-remote').count() == 0
        assert chat.evaluate('window.__qaGhosts') == []
        assert frame.locator('#chatMessages .message.assistant').count() == 1
        # A new turn must still display activity despite an older visible reply.
        chat.evaluate('''live => window.dispatchEvent(new MessageEvent('message', {data: {
            type: 'cuttle-live-status', sessionId: '42', status: live
        }}))''', {'active': True, 'generating': True, 'status': 'New work',
                  'updated_at': now + 1, 'query_id': 'new-turn'})
        frame.locator('#typing-indicator-remote').wait_for(state='visible')
        chat.evaluate('''() => window.dispatchEvent(new MessageEvent('message', {data: {
            type: 'cuttle-live-status', sessionId: '42',
            status: {active: false, generating: false, status: null}
        }}))''')
        frame.locator('#typing-indicator-remote').wait_for(state='detached')
        assert not frame.locator('#stopButton').is_visible(), 'Idle chat still offers Stop after remote completion'
        page.screenshot(path=str(tmp_path / 'completed-chat.png'))
        page.reload(wait_until='domcontentloaded')
        frame.locator('#chatMessages .message.assistant').wait_for(state='visible')
        assert frame.locator('#chatMessages .message.assistant').count() == 1
        assert frame.locator('#typing-indicator-remote').count() == 0
        assert errors == []
    finally:
        page.close()
