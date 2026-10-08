"""Offline history refresh/search acceptance at phone and tablet widths."""
from __future__ import annotations

import time
from urllib.parse import urlparse, parse_qs

import pytest

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class HistoryWorld(CardWorld):
    def __init__(self):
        super().__init__([])
        now = time.time() * 1000
        self.sessions = [dict(id=1000 + i, session_name=f'Alpha chat {i}',
                              message_count=2, last_message_time=now - i * 1000,
                              project_id=1, project_name='Cuttle', project_path='/repo/Cuttle')
                         for i in range(30)]
        self.searches = []

    def handle(self, route):
        parsed = urlparse(route.request.url)
        if parsed.path == '/api/auth/sessions/search':
            self.searches.append(route)
            return  # The test releases the late content-search wave explicitly.
        if parsed.path == '/api/auth/sessions':
            archived = parse_qs(parsed.query).get('archived') == ['only']
            return route.fulfill(json={'success': True, 'sessions': [] if archived else self.sessions})
        return super().handle(route)


def reveal(page, frame, count=3):
    for _ in range(count):
        frame.locator('#chatHistory .history-show-all-btn').first.click()


def position(frame):
    return frame.locator('#chatHistory').evaluate('''el => {
        const top = el.getBoundingClientRect().top;
        const row = Array.from(el.querySelectorAll('.chat-history-item')).find(row => {
            const rect = row.getBoundingClientRect();
            return rect.height && rect.bottom > top;
        });
        return {sid: row.dataset.sessionId, offset: row.getBoundingClientRect().top - top};
    }''')


def assert_position(frame, before):
    after = position(frame)
    assert after['sid'] == before['sid']
    assert abs(after['offset'] - before['offset']) <= 1


@pytest.mark.parametrize('width', [390, 1024])
def test_refresh_keeps_scroll_and_unchanged_rows(browser, static_server, width):
    world = HistoryWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        page.set_viewport_size({'width': width, 'height': 768})
        frame.locator('html').evaluate('() => window.openChatHistoryPanel()')
        frame.locator('#chatHistory .chat-history-item').first.wait_for()
        reveal(page, frame)
        frame.locator('#chatHistory').evaluate('''el => {
            el.scrollTop = 100;
            window.firstHistoryRow = el.querySelector('.chat-history-item');
        }''')
        before = position(frame)
        for _ in range(3):
            frame.locator('html').evaluate('() => window.refreshChatHistoryList()')
            assert_position(frame, before)
            assert frame.locator('#chatHistory .chat-history-item').first.evaluate(
                'el => el === window.firstHistoryRow')
        # A new chat above the reader should preserve the visible row, not pixels.
        world.sessions.insert(0, dict(world.sessions[0], id=2000,
                                     session_name='New Alpha chat', last_message_time=time.time() * 1000 + 1000))
        frame.locator('html').evaluate('() => window.refreshChatHistoryList()')
        assert_position(frame, before)
        assert frame.locator('#chatHistory .chat-history-item[data-session-id="2000"]').count() == 1
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize('width', [390, 1024])
def test_search_reveal_late_results_scrolling_and_clear(browser, static_server, width):
    world = HistoryWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        page.set_viewport_size({'width': width, 'height': 768})
        frame.locator('html').evaluate('() => window.openChatHistoryPanel()')
        search = frame.locator('#searchInput')
        search.fill('Alpha')
        frame.locator('#historySearchClear').wait_for(state='visible')
        frame.locator('[data-search-tier="__search_titles__"]').wait_for()
        reveal(page, frame)
        assert frame.locator('#chatHistory .chat-history-item').count() == 20
        # Approximate the reduced viewport with an open mobile keyboard.
        page.set_viewport_size({'width': width, 'height': 420})
        search.focus()
        frame.locator('#chatHistory').evaluate('el => el.scrollTop = 300')
        before = position(frame)
        page.wait_for_function('true')  # let already scheduled route callbacks run
        assert world.searches
        hits = [dict(row, match='title', snippet='Alpha in saved messages') for row in world.sessions]
        world.searches.pop().fulfill(json={'success': True, 'sessions': hits})
        frame.locator('#chatHistory .history-search-snippet').first.wait_for()
        assert_position(frame, before)
        for top in (380, 450, 250):
            frame.locator('#chatHistory').evaluate('(el, top) => el.scrollTop = top', top)
            assert frame.locator('#chatHistory').evaluate('el => el.scrollTop') == top
            before = position(frame)
            frame.locator('html').evaluate('() => window.refreshChatHistoryList()')
            assert_position(frame, before)
        assert search.input_value() == 'Alpha'
        # Clear cancels an in-flight search as well as the applied query latch.
        search.fill('Alpha new')
        frame.locator('#historySearchClear').click()
        assert search.input_value() == ''
        assert frame.locator('#historySearchClear').is_hidden()
        assert frame.locator('#chatHistory [data-search-tier]').count() == 0
        assert search.evaluate('el => document.activeElement === el')
        frame.locator('html').evaluate('() => window.refreshChatHistoryList()')
        assert frame.locator('#chatHistory [data-search-tier]').count() == 0
        assert not errors
    finally:
        page.close()
