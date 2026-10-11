"""Composer checkout identity on cold load and with stale device storage."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server, apply_request_guard, PROJECT  # noqa: F401
from .test_chat_sync_lifetime import SyncWorld, SID_A, _open, _ef, _pump_until
from .test_chat_badge_identity import ContextBrowser

CHECKOUT = {'checkout_uid': 'refactor', 'project_id': PROJECT['id'],
            'path': '/repo/worktrees/refactor/Cuttle', 'branch': 'refactor/work', 'state': 'ready'}


class CheckoutWorld(SyncWorld):
    def __init__(self, uid):
        super().__init__()
        self.uid = uid
        self.patches = []
        self.git_paths = []
        self.hold_probe = False
        self.probes = []

    def handle(self, route):
        path = urlparse(route.request.url).path
        project = {'project_id': PROJECT['id'], 'project_name': PROJECT['name'],
                   'project_path': PROJECT['path'], 'checkout_uid': self.uid}
        data = None
        if path == '/api/worktrees':
            if self.hold_probe:
                self.probes.append(route)
                return
            data = {'success': True, 'worktrees': [CHECKOUT]}
        elif path == '/api/git/repos':
            cwd = parse_qs(urlparse(route.request.url).query).get('path', [''])[0]
            self.git_paths.append(cwd)
            data = {'success': True, 'repos': [{'branch': 'refactor/work' if cwd == CHECKOUT['path'] else 'dev'}]}
        elif path == '/api/auth/sessions':
            data = {'success': True, 'sessions': [{'id': SID_A, 'session_name': 'Checkout', **project}]}
        elif path.endswith('/messages'):
            data = {'success': True, 'messages': self.scenario[str(SID_A)]['messages'], **project}
        elif path == f'/api/auth/sessions/{SID_A}' and route.request.method == 'PATCH':
            self.patches.append(route.request.post_data_json)
            self.uid = self.patches[-1].get('checkout_uid')
            data = {'success': True}
        if data is not None:
            route.fulfill(content_type='application/json', body=json.dumps(data))
            return
        super().handle(route)


@pytest.mark.parametrize('uid,stale', [('refactor', False), ('refactor', True), (None, True)])
def test_checkout_chip_and_next_request_agree(browser, static_server, uid, stale):
    world = CheckoutWorld(uid)
    context = browser.new_context()
    if stale:
        prefs = {str(SID_A): {'projectId': PROJECT['id'], 'projectPath': PROJECT['path'],
                             'checkoutUid': 'old-worktree', 'checkoutPath': '/repo/old-worktree'}}
        context.add_init_script('localStorage.setItem("cuttleChatSessionPrefs", ' + json.dumps(json.dumps(prefs)) + ');')
    page, frame, errors = _open(ContextBrowser(context), static_server, world, SID_A)
    try:
        chip = frame.locator('#chatSlashChipsRow .slash-chip-seg--branch')
        expected = 'refactor/work' if uid else 'dev'
        expect(chip).to_contain_text(expected)
        cwd = CHECKOUT['path'] if uid else PROJECT['path']
        _pump_until(page, lambda: cwd in world.git_paths, 5000, 'Git checkout path')
        # The server binding is display input, never a reason to write stale prefs back.
        assert not world.patches
        _ef(page).goto(_ef(page).url, wait_until='domcontentloaded')
        expect(chip).to_contain_text(expected)
        frame.locator('#chatInput').fill('check selected checkout')
        frame.locator('#sendButton').click()
        _pump_until(page, lambda: bool(world.chat_posts), 5000, 'next turn request')
        request = json.loads(world.chat_posts[-1])
        assert request.get('checkout_uid') == uid
        assert request['project_path'] == PROJECT['path']
        assert not errors
    finally:
        context.close()


def test_late_registry_response_repaints_current_checkout(browser, static_server):
    world = CheckoutWorld('refactor')
    world.hold_probe = True
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        chip = frame.locator('#chatSlashChipsRow [data-project-chip]')
        # A known worktree with an unresolved path must not borrow primary's branch.
        expect(chip).not_to_contain_text('dev')
        _pump_until(page, lambda: bool(world.probes), 5000, 'held registry probe')
        world.probes[0].fulfill(content_type='application/json', body=json.dumps(
            {'success': True, 'worktrees': [CHECKOUT]}))
        expect(chip).to_contain_text('refactor/work')
        expect(chip).to_have_attribute('data-tooltip', re.compile(CHECKOUT['path']))
        _pump_until(page, lambda: CHECKOUT['path'] in world.git_paths, 5000, 'resolved Git path')
        assert not world.patches
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize('uid,target', [(None, 'refactor'), ('refactor', None)])
def test_user_checkout_pick_survives_send_and_reload(browser, static_server, uid, target):
    world = CheckoutWorld(uid)
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        chip = frame.locator('#chatSlashChipsRow [data-project-chip]')
        expect(chip).to_contain_text('refactor/work' if uid else 'dev')
        chip.click()
        frame.locator('#chipConfigMode').select_option('worktree' if target else 'local', force=True)
        if target:
            frame.locator('#chipConfigWorktree option[value="refactor"]').wait_for(state='attached')
            frame.locator('#chipConfigWorktree').select_option('refactor', force=True)
        frame.locator('#chipConfigDone').click()
        _pump_until(page, lambda: bool(world.patches), 5000, 'persist selected checkout')
        assert world.patches[-1]['checkout_uid'] == target
        expected = 'refactor/work' if target else 'dev'
        expect(chip).to_contain_text(expected)
        expect(chip).to_have_attribute('data-tooltip', re.compile(
            CHECKOUT['path'] if target else PROJECT['path']))
        _ef(page).goto(_ef(page).url, wait_until='domcontentloaded')
        expect(chip).to_contain_text(expected)
        frame.locator('#chatInput').fill('use new checkout')
        frame.locator('#sendButton').click()
        _pump_until(page, lambda: bool(world.chat_posts), 5000, 'next turn after pick')
        assert json.loads(world.chat_posts[-1]).get('checkout_uid') == target
        assert not errors
    finally:
        page.close()
