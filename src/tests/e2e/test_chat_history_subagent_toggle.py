"""History panel: a parent row's sub-agent toggle collapses and expands its children."""
from __future__ import annotations

import time

from .test_chat_action_card_render import _open_card_chat
from .test_chat_history_scroll import HistoryWorld
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class SubagentWorld(HistoryWorld):
    def __init__(self):
        super().__init__()
        now = time.time() * 1000
        base = dict(message_count=2, project_id=1, project_name='Cuttle', project_path='/repo/Cuttle')
        parent = dict(base, id=3000, session_name='Parent chat', last_message_time=now + 5000)
        kids = [dict(base, id=3001 + i, session_name=f'Kid {i}', origin='subagent',
                     parent_session_id=3000, last_message_time=now + 4000 - i)
                for i in range(2)]
        self.sessions = [parent, *kids, *self.sessions]


def _group_state(frame):
    return frame.locator('#chatHistory').evaluate('''el => {
        const g = el.querySelector('.history-subagent-group[data-parent-session-id="3000"]');
        return g ? {collapsed: g.classList.contains('is-collapsed'),
                    force: g.getAttribute('data-force-expanded')} : null;
    }''')


def test_subagent_toggle_collapses_and_expands(browser, static_server):
    world = SubagentWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('html').evaluate('() => window.openChatHistoryPanel()')
        toggle = frame.locator(
            '#chatHistory .chat-history-item[data-session-id="3000"] .history-subagent-toggle')
        toggle.wait_for()
        initial = _group_state(frame)
        assert initial is not None
        toggle.click()
        after_first = _group_state(frame)
        assert after_first['collapsed'] != initial['collapsed'], (initial, after_first)
        toggle = frame.locator(
            '#chatHistory .chat-history-item[data-session-id="3000"] .history-subagent-toggle')
        toggle.click()
        assert _group_state(frame)['collapsed'] == initial['collapsed']
        assert not errors
    finally:
        page.close()
