"""Google auth rollout visibility against real shell assets and an isolated API."""
import json

import pytest

from tests.e2e.test_shared_diff_modal import browser, static_server  # noqa: F401
from tests.e2e.test_app_shell_layout import ShellWorld, _open, _load_shell


class GoogleWorld(ShellWorld):
    enabled = False
    fail = False
    linked = False

    def handle(self, route):
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/api/auth/oauth/status'):
            route.fulfill(status=503 if self.fail else 200, content_type='application/json',
                          body=json.dumps({'providers': {'google': {'configured': True, 'enabled': self.enabled}}}))
        elif path.endswith('/api/auth/me'):
            route.fulfill(content_type='application/json', body=json.dumps({
                'authenticated': True, 'user': {'id': 1, 'username': 'owner', 'role': 'owner',
                                               'auth_provider': 'local', 'google_linked': self.linked}}))
        else:
            super().handle(route)


def refresh(page):
    page.evaluate('() => CuttleAuth.refreshGoogleSignInUi()')


@pytest.mark.parametrize('linked', [False, True])
def test_google_controls_follow_effective_flag(browser, static_server, linked):
    world = GoogleWorld()
    world.linked = linked
    context, page, errors, _ = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        page.wait_for_function('CuttleAuth.isAuthenticated()')
        page.evaluate("openAuthModal('login')")
        refresh(page)
        assert not page.locator('.auth-oauth-block').first.is_visible()
        assert page.locator('#loginUsername').count()  # local login remains available
        page.evaluate('closeAuthModal()')
        page.locator('.rail-account').first.click()
        panel = page.locator('#logoutConfirmModal')
        panel.wait_for(state='visible')
        assert 'Google' not in panel.inner_text()
        world.enabled = True
        page.evaluate("window.postMessage({type:'cuttle-experimental-flags-changed'}, location.origin)")
        target = '#logoutConfirmRefreshGoogle' if linked else '#logoutConfirmLinkGoogle'
        page.locator(target).wait_for(state='visible')
        world.enabled = False
        refresh(page)
        assert 'Google' not in panel.inner_text()
        assert page.locator('#logoutConfirmSubmit').is_visible()
        page.locator('#logoutConfirmCancel').click()
        world.enabled = True
        page.evaluate("openAuthModal('login')")
        page.locator('.auth-oauth-block').first.wait_for(state='visible')
        page.locator('#signupTab').click()
        assert page.locator('#signupForm .auth-oauth-block').is_visible()
        world.fail = True
        refresh(page)
        assert not page.locator('#signupForm .auth-oauth-block').is_visible()
        world.fail = False
        refresh(page)
        assert page.locator('#signupForm .auth-oauth-block').is_visible()
    finally:
        context.close()


def test_google_signin_stays_hidden_until_status_resolves(browser, static_server):
    world = GoogleWorld()
    context, page, _, _ = _open(browser, static_server, world)
    pending = []
    context.route(f'{static_server}/api/auth/oauth/status', lambda route: pending.append(route))
    try:
        _load_shell(page, static_server)
        page.evaluate("openAuthModal('login')")
        assert not page.locator('.auth-oauth-block').first.is_visible()
        # Legacy user-menu entry shares the same gate.
        page.evaluate('''() => {
            const a = document.createElement('a');
            a.id = 'userMenuLinkGoogle'; a.hidden = true; a.textContent = 'Connect Google';
            document.body.appendChild(a);
        }''')
        assert page.locator('#userMenuLinkGoogle').is_hidden()
        context.unroute(f'{static_server}/api/auth/oauth/status')
        world.enabled = True
        for route in pending:
            world.handle(route)
        refresh(page)
        page.locator('.auth-oauth-block').first.wait_for(state='visible')
        page.locator('#userMenuLinkGoogle').wait_for(state='visible')
        world.enabled = False
        refresh(page)
        assert page.locator('#userMenuLinkGoogle').is_hidden()
    finally:
        context.close()
