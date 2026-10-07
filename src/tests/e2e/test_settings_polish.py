"""Settings disclosure and accessibility against production HTML and fake APIs."""
import json
import pytest
from playwright.sync_api import expect
from .test_settings_agents import AgentWorld, open_settings
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class SettingsWorld(AgentWorld):
    def handle(self, route):
        if route.request.url.endswith('/api/load-api-keys'):
            route.fulfill(content_type='application/json',
                          body=json.dumps({'success': True, 'api_keys': {'openai': 'sk-••••1234'}}))
        else:
            super().handle(route)


@pytest.mark.parametrize('tab', ['general', 'appearance', 'agents', 'experimental', 'providers', 'voice', 'devices', 'data', 'account'])
def test_all_settings_tabs_fit_phone_and_desktop(browser, static_server, tab):
    page = open_settings(browser, static_server, SettingsWorld(), width=390)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        expect(page.locator('#defaultAgentSelect')).to_be_enabled()
        page.evaluate('(tab) => CuttleSettingsTabs.setTab(tab)', tab)
        panel = page.locator('#panel-' + tab)
        expect(panel).to_be_visible()
        # Wait for the first tab loader and the accessibility observer.
        page.wait_for_timeout(100)
        if tab == 'providers':
            expect(page.locator('[data-credential-summary="openai"]')).to_have_text('Saved')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), tab
        if tab in ('appearance', 'providers', 'voice'):
            details = panel.locator('.settings-disclosure')
            assert details.count()
            for i in range(details.count()):
                summary = details.nth(i).locator('summary').first
                summary.focus()
                page.keyboard.press('Enter')
                assert details.nth(i).get_attribute('open') is not None
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), tab
        panel.screenshot(path=f'temp/settings-{tab}-phone.png')
        page.set_viewport_size({'width': 1200, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        panel.screenshot(path=f'temp/settings-{tab}-desktop.png')
        assert not errors, errors
    finally:
        page.close()


def test_setup_copy_and_refresh(browser, static_server):
    page = open_settings(browser, static_server, AgentWorld())
    try:
        expect(page.locator('[data-agent-id="alpha"]')).to_be_visible()
        page.evaluate("""() => {
            window.copied = [];
            Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => copied.push(text)}});
            const agents = [
                {id: 'login', label: 'Login Agent', available: true, ready: true, auth_command: 'tool login'},
                {id: 'setup', label: 'Setup Agent', available: false, install_hint: 'Install tool, then run tool login.'}
            ];
            renderAgentCliList({agents});
        }""")
        page.locator('[data-agent-id="login"] .agent-card-head').click()
        page.get_by_role('button', name='Copy login command').click()
        expect(page.locator('#agentSetupStatus')).to_contain_text('Copied')
        page.locator('[data-agent-id="setup"] .agent-card-head').click()
        page.get_by_role('button', name='Copy setup instructions').click()
        assert page.evaluate('copied') == ['tool login', 'Install tool, then run tool login.']
        assert page.locator('[data-agent-id="setup"] .agent-config').count() == 0
        page.get_by_role('button', name='Check again', exact=True).click()
        expect(page.locator('#agentSetupStatus')).to_contain_text('updated')
        expect(page.locator('[data-agent-id="alpha"]')).to_be_visible()
    finally:
        page.close()


def test_keyboard_switches_and_credential_disclosure(browser, static_server):
    page = open_settings(browser, static_server, SettingsWorld())
    try:
        page.evaluate("CuttleSettingsTabs.setTab('appearance')")
        switch = page.get_by_role('switch', name='Page Transitions')
        expect(switch).to_have_attribute('aria-checked', 'true')
        switch.focus()
        page.keyboard.press('Space')
        expect(switch).to_have_attribute('aria-checked', 'false')
        assert page.evaluate("localStorage.getItem('cuttleUiAnimations')") == '0'
        expect(page.locator('#uiTransitionMs')).to_be_disabled()
        page.keyboard.press('Enter')
        expect(switch).to_have_attribute('aria-checked', 'true')
        page.evaluate("CuttleSettingsTabs.setTab('providers')")
        expect(page.locator('[data-credential-summary="openai"]')).to_have_text('Saved')
        expect(page.locator('[data-credential-summary="anthropic"]')).to_have_text('Not set')
        expect(page.locator('#openaiKey')).to_be_hidden()
        page.locator('#openaiCredential > summary').click()
        expect(page.get_by_role('textbox', name='OpenAI API Key')).to_be_visible()
        # The input displays only a saved-key hint, never a secret value.
        expect(page.locator('#openaiKey')).to_have_value('')
    finally:
        page.close()


@pytest.mark.parametrize('touch', [False, True])
def test_existing_info_tooltip_opens_and_dismisses(browser, static_server, touch):
    from .test_shared_diff_modal import apply_request_guard
    context = browser.new_context(has_touch=touch, viewport={'width': 390 if touch else 1200, 'height': 844})
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(static_server + '/api/**', SettingsWorld().handle)
    try:
        page.goto(static_server + '/settings_page.html?tab=agents', wait_until='domcontentloaded')
        expect(page.locator('#defaultAgentSelect')).to_be_enabled()
        page.evaluate("CuttleSettingsTabs.setTab('appearance')")
        info = page.get_by_role('button', name='About Page Transitions', exact=True)
        tip = page.locator('#cuttle-shared-tooltip')
        row = info.locator('xpath=ancestor::div[contains(@class,"setting-item")][1]')
        assert row.locator('.setting-description').count() == 0
        if touch:
            info.tap()
        else:
            info.hover()
        expect(tip).to_be_visible()
        expect(tip).to_contain_text('Fade gently when switching pages.')
        assert tip.evaluate('(el) => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }')
        page.keyboard.press('Escape')
        expect(tip).to_be_hidden()
        if touch:
            info.tap()
            expect(tip).to_be_visible()
            info.tap()
            expect(tip).to_be_hidden()
        else:
            page.mouse.move(0, 0)
            info.focus()
            expect(tip).to_be_visible()
            assert info.get_attribute('aria-describedby') == 'cuttle-shared-tooltip'
        page.keyboard.press('Escape')
        # Help for dynamically rendered flags uses the same shared component,
        # while operational restart requirements remain visible in the row.
        page.evaluate("""() => {
            CuttleSettingsTabs.setTab('experimental');
            document.getElementById('experimentalFlagList').innerHTML =
                renderExperimentalFlag({id: 'example', label: 'Example feature',
                    description: 'Details with "quotes" & special characters.',
                    needs_restart: true, enabled: false});
        }""")
        dynamic = page.get_by_role('button', name='About Example feature', exact=True)
        # Wait for the native loader before painting the deterministic fixture.
        page.wait_for_timeout(100)
        page.evaluate("""() => {
            document.getElementById('experimentalFlagList').innerHTML =
                renderExperimentalFlag({id: 'example', label: 'Example feature',
                    description: 'Details with "quotes" & special characters.',
                    needs_restart: true, enabled: false});
        }""")
        expect(page.locator('[data-experimental-flag="example"]')).to_contain_text('restart')
        if touch:
            dynamic.tap()
        else:
            dynamic.hover()
        expect(tip).to_contain_text('Details with "quotes" & special characters.')
    finally:
        context.close()
