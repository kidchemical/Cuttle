"""Unsent agent choices survive real shell space/frame lifetimes.

Production assets, isolated storage, fake same-origin API; no agent execution.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from .test_app_shell_layout import (
    ShellWorld, _open, _load_shell, _pump_until, _assert_blocked_api_free,
    browser, static_server,  # noqa: F401
)


class DraftWorld(ShellWorld):
    def handle(self, route):
        if '/api/auth/sessions/123/messages' in route.request.url:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'success': True, 'messages': [
                              {'id': 1, 'role': 'user', 'content': 'prior message'}]}))
        elif '/models' in route.request.url:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'success': True, 'preferredModel': 'test-model', 'defaultModel': 'test-model', 'models': [
                              {'id': 'test-model', 'label': 'Test Model'}]}))
        elif '/api/settings/starred-slash' in route.request.url:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'success': True, 'prefixes': ['/cursor ']}))
        else:
            super().handle(route)


def _chat(page):
    frame = page.frame_locator('#contentFrame')
    expect(frame.locator('#welcomeChatInput')).to_be_visible()
    return frame


def _pick(frame, agent):
    frame.locator('#welcomeChatInput').fill('/' + agent)
    expect(frame.locator('#welcomeSlashCommandMenu')).to_be_visible()
    frame.locator('#welcomeChatInput').press('Enter')
    expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text(re.compile(re.escape(agent), re.I))


def _assert_draft(frame, agent, text):
    expect(frame.locator('#welcomeChatInput')).to_have_value(text)
    expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text(re.compile(re.escape(agent), re.I))


@pytest.mark.parametrize('draft', ['', 'unsent message'])
def test_unsent_agent_survives_space_switch_and_reload(browser, static_server, draft):
    context, page, errors, blocked = _open(browser, static_server, DraftWorld())
    try:
        _load_shell(page, static_server)
        first = page.evaluate('spacesState.active')
        frame = _chat(page)
        _pick(frame, 'codex')
        frame.locator('#welcomeChatInput').fill(draft)
        # A second space must get its own default, not the first space's draft.
        page.evaluate('addSpace()')
        _pump_until(page, lambda: page.evaluate('spacesState.active') != first,
                    10000, 'second space')
        frame = _chat(page)
        expect(frame.locator('#welcomeChatInput')).to_have_value('')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Cursor')
        _pick(frame, 'muse')
        frame.locator('#welcomeChatInput').fill('second draft')
        page.evaluate('(id) => switchSpace(id)', first)
        _assert_draft(_chat(page), 'codex', draft)
        page.reload(wait_until='domcontentloaded')
        _assert_draft(_chat(page), 'codex', draft)
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_removed_unsent_agent_survives_reload(browser, static_server):
    context, page, errors, blocked = _open(browser, static_server, DraftWorld())
    try:
        _load_shell(page, static_server)
        frame = _chat(page)
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Cursor')
        frame.locator('#welcomeSlashChipsRow .slash-chip-remove').click()
        frame.locator('#welcomeChatInput').fill('route this myself')
        _pump_until(page, lambda: page.locator('.shell-main iframe').count() == 1,
                    10000, 'outgoing frame disposed')
        page.reload(wait_until='commit')
        frame = _chat(page)
        expect(frame.locator('#welcomeChatInput')).to_have_value('route this myself')
        expect(frame.locator('#welcomeSlashChipsRow .slash-command-chip')).to_have_count(0)
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_delayed_starred_settings_do_not_replace_manual_agent(browser, static_server):
    context, page, errors, blocked = _open(browser, static_server, DraftWorld())
    pending = []
    context.route(f'{static_server}/api/settings/starred-slash',
                  lambda route: pending.append(route))
    try:
        _load_shell(page, static_server)
        frame = _chat(page)
        _pick(frame, 'codex')
        _pump_until(page, lambda: bool(pending), 10000, 'starred settings requested')
        for route in pending:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'success': True, 'prefixes': ['/cursor ']}))
        _pump_until(page, lambda: page.evaluate(
            "localStorage.getItem('cuttleStarredSlashCommands')") == '["/cursor "]',
            10000, 'starred settings hydrated')
        expect(frame.locator('#welcomeSlashChipsRow')).to_contain_text('Codex')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_saved_chat_unsent_agent_survives_space_switch(browser, static_server):
    context, page, errors, blocked = _open(browser, static_server, DraftWorld())
    try:
        _load_shell(page, static_server)
        _chat(page)
        first = page.evaluate('spacesState.active')
        page.evaluate("navigate(0, '/chat_page.html?chat=123')")
        frame = page.frame_locator('#contentFrame')
        expect(frame.locator('#chatMessages')).to_contain_text('prior message')
        expect(frame.locator('#chatInput')).to_be_visible()
        frame.locator('#chatInput').fill('/codex')
        expect(frame.locator('#chatSlashCommandMenu')).to_be_visible()
        frame.locator('#chatInput').press('Enter')
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        frame.locator('#chatInput').fill('saved chat draft')
        page.evaluate('addSpace()')
        _chat(page)
        page.evaluate('(id) => switchSpace(id)', first)
        frame = page.frame_locator('#contentFrame')
        expect(frame.locator('#chatInput')).to_have_value('saved chat draft')
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_unsent_agents_and_text_are_separate_per_pane(browser, static_server):
    layout = {'version': 2, 'root': {
        'type': 'group', 'id': 'draft-panes', 'orientation': 'horizontal',
        'children': [
            {'type': 'leaf', 'id': 'draft-left', 'page': '/chat_page.html'},
            {'type': 'leaf', 'id': 'draft-right', 'page': '/chat_page.html'},
        ]}}
    context, page, errors, blocked = _open(browser, static_server, DraftWorld(), seed=layout)
    try:
        _load_shell(page, static_server)
        left = page.frame_locator('[data-leaf-id="draft-left"] iframe')
        right = page.frame_locator('[data-leaf-id="draft-right"] iframe')
        expect(left.locator('#welcomeChatInput')).to_be_visible()
        expect(right.locator('#welcomeChatInput')).to_be_visible()
        _pick(left, 'codex')
        left.locator('#welcomeChatInput').fill('left draft')
        _pick(right, 'cursor')
        right.locator('#welcomeChatInput').fill('right draft')
        page.reload(wait_until='domcontentloaded')
        _assert_draft(left, 'codex', 'left draft')
        _assert_draft(right, 'cursor', 'right draft')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()


def test_legacy_welcome_text_is_preserved_when_scoping_drafts(browser, static_server):
    context, page, errors, blocked = _open(browser, static_server, DraftWorld())
    context.add_init_script("""if (!localStorage.getItem('legacySeeded')) {
        localStorage.setItem('legacySeeded', '1');
        localStorage.setItem('cuttle.composerDraft.new', 'draft from before the fix');
    }""")
    try:
        _load_shell(page, static_server)
        expect(_chat(page).locator('#welcomeChatInput')).to_have_value('draft from before the fix')
        assert page.evaluate("localStorage.getItem('cuttle.composerDraft.new')") is None
        page.evaluate('addSpace()')
        expect(_chat(page).locator('#welcomeChatInput')).to_have_value('')
        _assert_blocked_api_free(blocked)
        assert not errors
    finally:
        context.close()
