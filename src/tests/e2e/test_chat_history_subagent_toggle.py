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


def _toggle(frame):
    return frame.locator(
        '#chatHistory .chat-history-item[data-session-id="3000"] .history-subagent-toggle')


def _ensure_panel_open(frame):
    frame.locator('html').evaluate('''() => {
        const p = document.querySelector('.chat-history-panel');
        if (!p.classList.contains('open')) window.openChatHistoryPanel();
    }''')


def test_group_holding_the_open_chat_still_collapses(browser, static_server):
    """Opening a sub-agent reveals its parent group, but a collapse click must
    still win (it used to be silently ignored while the chat was inside)."""
    world = SubagentWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('html').evaluate('() => window.openChatHistoryPanel()')
        _toggle(frame).wait_for()
        if _group_state(frame)['collapsed']:
            _toggle(frame).click()
        frame.locator('#chatHistory .chat-history-item[data-session-id="3001"]').click()
        frame.locator('#chatHistory .chat-history-item.active[data-session-id="3001"]').wait_for(state='attached')
        _ensure_panel_open(frame)
        assert _group_state(frame) == {'collapsed': False, 'force': '1'}
        _toggle(frame).click()
        assert _group_state(frame)['collapsed'] is True
        # A repaint keeps the user's collapse instead of re-forcing it open.
        frame.locator('html').evaluate('() => window.refreshChatHistoryList(null, { force: true })')
        assert _group_state(frame)['collapsed'] is True
        _toggle(frame).click()
        assert _group_state(frame)['collapsed'] is False
        assert not errors
    finally:
        page.close()


def test_repaint_during_a_press_does_not_eat_the_toggle_click(browser, static_server):
    world = SubagentWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('html').evaluate('() => window.openChatHistoryPanel()')
        _toggle(frame).wait_for()
        before = _group_state(frame)['collapsed']
        # Let the panel's slide-in finish so the button sits at its final spot.
        frame.locator('.chat-history-panel').evaluate(
            "p => new Promise((ok) => { const t = () => p.classList.contains('hist-slide') ? setTimeout(t, 20) : ok(); t(); })")
        box = _toggle(frame).bounding_box()
        assert box and box['x'] >= 0, box
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()
        # A live update rebuilds the list while the button is held down.
        frame.locator('html').evaluate('''() => {
            const el = document.getElementById('chatHistory');
            CuttleChatHistoryView.paint(el, el.innerHTML + '<!-- live -->');
        }''')
        page.mouse.up()
        # The click landed on the original button: the toggle wrote its pref.
        pref = frame.locator('html').evaluate(
            "() => JSON.parse(localStorage.getItem('cuttle.historySubagentExpand') || '{}')['3000']")
        assert pref == ('expanded' if before else 'collapsed')
        # The parked repaint lands right after the click.
        frame.locator('#chatHistory').evaluate(
            "el => new Promise((ok) => { const t = () => el.innerHTML.includes('<!-- live -->') ? ok() : setTimeout(t, 20); t(); })")
        frame.locator('html').evaluate('() => window.refreshChatHistoryList(null, { force: true })')
        assert _group_state(frame)['collapsed'] is (not before)
        assert not errors
    finally:
        page.close()
