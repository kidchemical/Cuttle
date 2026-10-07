"""Actual chat page with conflicting devices/drafts; all transport is offline."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server, apply_request_guard  # noqa: F401
from .test_chat_sync_lifetime import SyncWorld, SID_A, SID_B, _open, _ef, _pump_until

CODEX = {'chips': [{'label': 'Codex - GPT-6.1-Sol · medium', 'meta': '/codex · model gpt-6.1-sol · effort medium', 'category': 'codex'}]}
CLAUDE = {'chips': [{'label': 'Claude - Opus 5.5 · high', 'meta': '/claude · model opus-5.5 · effort high', 'category': 'claude'}]}


def selection(slash, revision=1):
    chip = slash['chips'][0]
    return {'stickyChips': [{**chip, 'prefix': chip['meta'].split(' ')[0] + ' '}], 'stickyCleared': False, 'revision': revision}


class BadgeWorld(SyncWorld):
    def __init__(self):
        super().__init__()
        self.shared = selection(CODEX)
        self.writes = []
        self.pins = {'codex': {'model': 'gpt-6.1-sol', 'effort': 'medium'}, 'claude': {'model': 'opus-5.5', 'effort': 'high'}}
        self.scenario[str(SID_A)]['messages'] = [
            {'id': 1, 'role': 'user', 'content': '/codex investigate', 'metadata': {'slash_command': CODEX}},
        ]
        self.scenario[str(SID_A)]['live'] = {'active': True, 'generating': True, 'status': 'Working', 'query_id': 'q-codex', 'slash_command': CODEX}

    def handle(self, route):
        path = urlparse(route.request.url).path
        parts = path.strip('/').split('/')
        if len(parts) == 3 and parts[1] in self.pins and parts[2] in ('models', 'model', 'effort'):
            pin = self.pins[parts[1]]
            route.fulfill(content_type='application/json', body=json.dumps({
                'success': True, 'models': [], 'preferredModel': pin['model'],
                'preferredEffort': pin['effort'],
            }))
            return
        if path.endswith('/composer'):
            self.writes.append(route.request.post_data_json)
            self.shared = {**route.request.post_data_json, 'revision': self.shared['revision'] + 1}
            route.fulfill(content_type='application/json', body=json.dumps({'success': True, 'composer_selection': self.shared}))
            return
        if path.endswith('/messages'):
            sid = path.split('/')[-2]
            sc = self.scenario[sid]
            route.fulfill(content_type='application/json', body=json.dumps({
                'success': True, 'messages': sc['messages'], 'session_name': sc['session_name'],
                'agent_pins': self.pins, 'composer_selection': self.shared if sid == str(SID_A) else None,
            }))
            return
        super().handle(route)


class ContextBrowser:
    def __init__(self, context):
        self.context = context

    def new_page(self, **kwargs):
        page = self.context.new_page()
        return page


def open_stale_desktop(browser, static_server, world):
    # Install storage in this context only: desktop retains Claude, including
    # a saved composer draft. Phone has independent localStorage.
    context = browser.new_context(viewport={'width': 1280, 'height': 800})
    context.add_init_script("localStorage.setItem('cuttleChatSessionPrefs', " + json.dumps(json.dumps({str(SID_A): {
        **selection(CLAUDE), 'composerDraft': {'chips': selection(CLAUDE)['stickyChips'], 'pins': {}},
    }})) + ");")
    page, frame, errors = _open(ContextBrowser(context), static_server, world, SID_A)
    return context, page, frame, errors


def test_conflicting_composer_draft_hub_refresh_reopen_and_completion(browser, static_server):
    world = BadgeWorld()
    context, page, frame, errors = open_stale_desktop(browser, static_server, world)
    try:
        working = frame.locator('#typing-indicator-remote .message-sender-group')
        expect(working).to_contain_text('Codex')
        expect(working).not_to_contain_text('Claude')
        # Actual composer picks come from the shared session, even with a stale draft.
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        assert not world.writes, 'hydration must not publish stale local choices'
        # Model/effort pins already have server ownership; polling must repaint
        # the composer without changing the frozen executing model/effort.
        world.pins['codex']['effort'] = 'xhigh'
        _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('xhigh')
        expect(working).to_contain_text('medium')
        expect(working).not_to_contain_text('xhigh')
        # A next-send change on another device does not relabel the running turn.
        world.shared = selection(CLAUDE, revision=2)
        _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Claude')
        expect(working).to_contain_text('Codex')
        _ef(page).evaluate(f'window.loadChatSession({SID_B})')
        expect(frame.locator('#typing-indicator-remote')).to_have_count(0)
        _ef(page).evaluate(f'window.loadChatSession({SID_A})')
        expect(working).to_contain_text('Codex')
        expect(working).not_to_contain_text('Claude')
        # Full page reload must keep the execution badge, not the composer badge.
        _ef(page).goto(static_server + f'/chat_page.html?chat={SID_A}', wait_until='commit')
        expect(working).to_contain_text('Codex')
        world.scenario[str(SID_A)]['messages'].append({'id': 2, 'role': 'assistant', 'content': 'Done', 'metadata': {'slash_command': CODEX}})
        world.scenario[str(SID_A)]['live'] = {'active': False}
        _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(frame.locator('#typing-indicator-remote')).to_have_count(0)
        expect(frame.locator('#chatMessages .message.assistant .message-sender-group')).to_contain_text('Codex')
        assert not errors
        assert not world.chat_posts
    finally:
        context.close()


def test_device_chip_removal_syncs_without_relabelling_work(browser, static_server):
    world = BadgeWorld()
    desktop_context, desktop, dframe, errors = open_stale_desktop(browser, static_server, world)
    phone_context = browser.new_context(viewport={'width': 390, 'height': 844})
    phone, pframe, phone_errors = _open(ContextBrowser(phone_context), static_server, world, SID_A)
    try:
        expect(pframe.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        pframe.locator('#chatSlashChipsRow .slash-chip-remove').first.click()
        _pump_until(phone, lambda: bool(world.writes), 5000, 'shared composer PUT')
        assert world.shared['stickyCleared'] is True
        _ef(desktop).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(dframe.locator('#chatSlashChipsRow .slash-chip-remove[data-chip-index]')).to_have_count(0)
        expect(pframe.locator('#chatSlashChipsRow .slash-chip-remove[data-chip-index]')).to_have_count(0)
        expect(dframe.locator('#typing-indicator-remote .message-sender-group')).to_contain_text('Codex')
        expect(pframe.locator('#typing-indicator-remote .message-sender-group')).to_contain_text('Codex')
        assert not errors + phone_errors
        assert not world.chat_posts
    finally:
        phone_context.close()
        desktop_context.close()


def test_router_fallback_badge_comes_from_live_execution(browser, static_server):
    world = BadgeWorld()
    # Composer and sent user row say Codex; selected live harness is Claude.
    world.scenario[str(SID_A)]['live']['slash_command'] = CLAUDE
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        expect(frame.locator('#typing-indicator-remote .message-sender-group')).to_contain_text('Claude')
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        assert not errors
    finally:
        page.close()


class LocalBadgeWorld(BadgeWorld):
    def __init__(self):
        super().__init__()
        self.shared = selection(CLAUDE)
        self.scenario[str(SID_A)]['messages'] = [
            {'id': 1, 'role': 'user', 'content': '/claude previous work', 'metadata': {'slash_command': CLAUDE}},
            {'id': 2, 'role': 'assistant', 'content': 'Previous answer', 'metadata': {'slash_command': CLAUDE}},
        ]
        self.scenario[str(SID_A)]['live'] = {'active': False}

    def handle(self, route):
        if route.request.method == 'POST' and urlparse(route.request.url).path == '/api/chat':
            self.chat_posts.append(route.request.post_data_json)
            self.scenario[str(SID_A)]['messages'].append({
                'id': 3, 'role': 'user', 'content': '/codex local work', 'metadata': {'slash_command': CODEX},
            })
            self.scenario[str(SID_A)]['live'] = {
                'active': True, 'generating': True, 'query_id': 'local-codex',
                'report_url': '/query_log.html?id=local-codex', 'slash_command': CODEX,
            }
            event = {'type': 'query_started', 'query_id': 'local-codex',
                     'report_url': '/query_log.html?id=local-codex', 'slash_command': CODEX}
            route.fulfill(content_type='text/event-stream', body='data: ' + json.dumps(event) + '\n\n')
            return
        super().handle(route)


def test_local_stream_badge_is_frozen_when_next_send_selection_changes(browser, static_server):
    world = LocalBadgeWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Claude')
        world.shared = selection(CODEX, revision=2)
        _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Codex')
        _ef(page).evaluate("() => { void window.sendMessage({text: 'local work'}); }")
        working = frame.locator('#typing-indicator .message-sender-group')
        expect(working).to_contain_text('Codex')
        _pump_until(page, lambda: bool(world.chat_posts), 5000, 'local SSE send')
        assert world.chat_posts[0]['message'].startswith('/codex ')
        assert world.chat_posts[0]['agent_pins']['codex']['model'] == 'gpt-6.1-sol'
        world.shared = selection(CLAUDE, revision=10)
        _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")
        expect(frame.locator('#chatSlashChipsRow')).to_contain_text('Claude')
        expect(working).to_contain_text('Codex')
        expect(working).not_to_contain_text('Claude')
        assert not errors
    finally:
        page.close()
