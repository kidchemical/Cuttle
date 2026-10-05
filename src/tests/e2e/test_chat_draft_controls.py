"""Full unsent composer overrides, against real assets and isolated fake APIs."""
import json
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .test_chat_draft_agent import DraftWorld, _chat, _pick
from .test_app_shell_layout import (
    _open, _load_shell, _pump_until, _assert_blocked_api_free,
    browser, static_server,  # noqa: F401
)


class ControlsWorld(DraftWorld):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.writes = []

    def handle(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/chat':
            self.sent.append(json.loads(route.request.post_data))
            sid = self.sent[-1].get('session_id') or 456
            events = [
                {'type': 'session', 'session_id': sid},
                {'type': 'response', 'success': True, 'session_id': sid, 'response': 'fake reply'},
            ]
            route.fulfill(status=200, content_type='text/event-stream',
                          body=''.join('data: ' + json.dumps(event) + '\n\n' for event in events))
            return
        elif path.endswith('/models'):
            data = {'success': True, 'preferredModel': 'star-model',
                    'defaultModel': 'star-model', 'commonEfforts': ['low', 'high'],
                    'models': [
                        {'id': 'star-model', 'label': 'Star Model', 'efforts': ['low', 'high']},
                        {'id': 'custom-model', 'label': 'Custom Model', 'efforts': ['low', 'high']},
                    ]}
        elif path.endswith('/effort'):
            if route.request.method == 'POST':
                self.writes.append(json.loads(route.request.post_data))
            data = {'success': True, 'preferredEffort': 'low'}
        elif path.endswith('/model') and route.request.method == 'POST':
            self.writes.append(json.loads(route.request.post_data))
            data = {'success': True}
        else:
            return super().handle(route)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data))


def _select(frame, command, label):
    frame.locator('#welcomeChatInput').fill(command)
    menu = frame.locator('#welcomeSlashCommandMenu')
    expect(menu).to_be_visible()
    menu.locator('.slash-command-item').filter(has_text=label).first.click()
    expect(frame.locator('#welcomeChatInput')).to_have_value('')


@pytest.mark.parametrize('agent', ['codex', 'muse', 'hermes', 'opencode', 'claude'])
def test_model_and_effort_override_survive_space_reload_and_first_send(browser, static_server, agent):
    world = ControlsWorld()
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        original = page.evaluate('spacesState.active')
        frame = _chat(page)
        _pick(frame, agent)
        _select(frame, f'/{agent} model custom-model', 'Custom Model')
        _select(frame, f'/{agent} effort high', 'Effort high')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        frame.locator('#welcomeChatInput').fill('unsent text')
        page.evaluate('addSpace()')
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).not_to_contain_text('high')
        page.evaluate('(id) => switchSpace(id)', original)
        frame = _chat(page)
        expect(frame.locator('#welcomeChatInput')).to_have_value('unsent text')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Custom Model')
        page.reload(wait_until='domcontentloaded')
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Custom Model')
        # Catalog hydration remains usable after restoring an override, and
        # its default response must not replace the custom model.
        frame.locator('#welcomeChatInput').fill(f'/{agent} model')
        expect(frame.locator('#welcomeSlashCommandMenu')).to_contain_text('Star Model')
        expect(frame.locator('#welcomeSlashCommandMenu')).to_contain_text('Custom Model')
        frame.locator('#welcomeChatInput').fill('unsent text')
        frame.locator('#welcomeSendButton').click()
        _pump_until(page, lambda: bool(world.sent), 10000, 'fake first send')
        assert world.sent[0]['agent_pins'][agent] == {'model': 'custom-model', 'effort': 'high'}
        assert world.sent[0]['message'].startswith('/' + agent)
        assert all(row.get('session') for row in world.writes)
        expect(frame.locator('#chatMessages')).to_contain_text('fake reply')
        frame.locator('[aria-label="New chat"]').click()
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Cursor')
        expect(frame.locator('#welcomeSlashChipsRow')).not_to_contain_text('Custom Model')
        expect(frame.locator('#welcomeSlashChipsRow')).not_to_contain_text('high')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize('agent,command,label', [
    ('codex', '/codex /usage', re.compile('usage', re.I)),
    ('cursor', '/model custom-model', 'Custom Model'),
])
def test_unsent_command_or_cursor_model_badge_survives_navigation(browser, static_server, agent, command, label):
    world = ControlsWorld()
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        original = page.evaluate('spacesState.active')
        frame = _chat(page)
        _pick(frame, agent)
        _select(frame, command, label)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text(label)
        page.evaluate('addSpace()')
        expect(_chat(page).locator('#welcomeSlashChipsRow')).to_contain_text('Cursor')
        page.evaluate('(id) => switchSpace(id)', original)
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text(label)
        page.reload(wait_until='domcontentloaded')
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text(label)
        # Removing a restored one-shot is itself saved.
        frame.locator('#welcomeSlashChipsRow .slash-command-chip').filter(has_text=label).last.locator('.slash-chip-remove').click()
        page.reload(wait_until='domcontentloaded')
        expect(_chat(page).locator('#welcomeSlashChipsRow')).not_to_contain_text(label)
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_effort_only_override_with_no_text_survives_new_chat_and_space(browser, static_server):
    world = ControlsWorld()
    context, page, errors, blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        original = page.evaluate('spacesState.active')
        frame = _chat(page)
        _pick(frame, 'codex')
        _select(frame, '/codex effort high', 'Effort high')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        page.evaluate('addSpace()')
        expect(_chat(page).locator('#welcomeSlashChipsRow')).to_contain_text('Cursor')
        page.evaluate('(id) => switchSpace(id)', original)
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        expect(frame.locator('#welcomeChatInput')).to_have_value('')
        # Visiting an existing chat and returning to the welcome composer
        # must recover this same unsent override.
        frame.locator('#welcomeChatInput').evaluate("() => { window.loadChatSession('123'); }")
        expect(frame.locator('#chatMessages')).to_contain_text('prior message')
        frame.locator('[aria-label="New chat"]').click()
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('high')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Star Model')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()
