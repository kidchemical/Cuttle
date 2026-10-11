"""Session totals on boot use saved metadata in the first transcript response."""
import json
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect
from .test_shared_diff_modal import browser, static_server, apply_request_guard, IsolatedAPI  # noqa: F401

TRACK_USAGE = """
    window.usageStates = [];
    new MutationObserver(() => {
        const totals = document.getElementById('chatSessionUsageTotals');
        if (totals && !totals.parentElement.hidden) window.usageStates.push(totals.textContent);
    }).observe(document, {childList: true, subtree: true, characterData: true, attributes: true});
"""


class UsageWorld(IsolatedAPI):
    def __init__(self, omit_first=False, empty=False):
        super().__init__()
        self.message_queries = []
        self.omit_first = omit_first
        self.empty = empty

    def handle(self, route):
        parsed = urlparse(route.request.url)
        if parsed.path == '/api/auth/sessions':
            data = {'success': True, 'sessions': [{'id': 42, 'session_name': 'Usage test'}]}
        elif parsed.path == '/api/auth/sessions/42/messages':
            query = parse_qs(parsed.query)
            self.message_queries.append(query)
            data = {'success': True, 'session_name': 'Usage test', 'messages': [
                {'id': 1, 'role': 'user', 'content': 'Previously saved question', 'timestamp': 1700000000000},
            ]}
            if query.get('usage_summary') == ['1'] and not (self.omit_first and len(self.message_queries) == 1):
                data['usage_summary'] = [] if self.empty else [{'usage': {
                    'prompt_tokens': 1200, 'cache_read_tokens': 300,
                    'completion_tokens': 45, 'cache_inclusive': False,
                    'cost': 0.02, 'cost_estimated': True,
                }}]
        else:
            return super().handle(route)
        route.fulfill(content_type='application/json', body=json.dumps(data))


@pytest.mark.parametrize('include', [False, True])
def test_refresh_fetches_session_totals_with_first_transcript(browser, static_server, include):
    world = UsageWorld()
    page = browser.new_page()
    apply_request_guard(page.context, static_server)
    page.add_init_script(f"""
        localStorage.setItem('cuttleShowSessionUsageEstimate', 'true');
        localStorage.setItem('cuttleSessionUsageIncludeSubagents', '{str(include).lower()}');
    """ + TRACK_USAGE)
    page.route(static_server + '/api/**', world.handle)
    try:
        page.goto(static_server + '/chat_page.html?chat=42', wait_until='domcontentloaded')
        totals = page.locator('#chatSessionUsageTotals')
        expect(totals.locator('.message-usage-in')).to_have_text('↑ 1.2k')
        expect(totals.locator('.message-usage-cache')).to_have_text('↑ 300')
        expect(totals.locator('.message-usage-out')).to_have_text('↓ 45')
        expect(totals.locator('.message-usage-cost')).to_have_text('~$0.02')
        assert world.message_queries[0]['usage_summary'] == ['1']
        assert world.message_queries[0]['include_subagents'] == ['1' if include else '0']
        # These totals were not in the painted message window, and did not
        # require waiting for periodic history synchronization.
        assert len(world.message_queries) == 1
        assert 'No token usage recorded for this session' not in page.evaluate('window.usageStates')
    finally:
        page.close()


@pytest.mark.parametrize('empty', [False, True])
def test_missing_prefetch_summary_is_fetched_immediately(browser, static_server, empty):
    world = UsageWorld(omit_first=True, empty=empty)
    page = browser.new_page()
    apply_request_guard(page.context, static_server)
    page.add_init_script("localStorage.setItem('cuttleShowSessionUsageEstimate', 'true');" + TRACK_USAGE)
    page.route(static_server + '/api/**', world.handle)
    try:
        page.goto(static_server + '/chat_page.html?chat=42', wait_until='domcontentloaded')
        totals = page.locator('#chatSessionUsageTotals')
        if empty:
            expect(totals).to_have_text('No token usage recorded for this session', timeout=3000)
        else:
            expect(totals.locator('.message-usage-in')).to_have_text('↑ 1.2k', timeout=3000)
            assert 'Loading session usage…' in page.evaluate('window.usageStates')
            assert 'No token usage recorded for this session' not in page.evaluate('window.usageStates')
        assert len(world.message_queries) == 2
        assert world.message_queries[1]['usage_summary'] == ['1']
    finally:
        page.close()
