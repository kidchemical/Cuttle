"""Settings sidebar, click-in subpages, breadcrumbs, and the phone index.

Production HTML against fake APIs. Owners: settings_page_tabs.js (sidebar,
index/panel view) and settings_subpages.js (link rows + breadcrumbs).
"""
from playwright.sync_api import expect

from .test_settings_agents import AgentWorld, open_settings
from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401


def _crumbs(page):
    return [t.strip() for t in page.locator('.settings-crumbs > .settings-crumb').all_inner_texts()]


def test_link_rows_open_a_subpage_with_breadcrumbs_and_deep_link(browser, static_server):
    page = open_settings(browser, static_server, AgentWorld())
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    try:
        panel = page.locator('#panel-agents')
        expect(page.locator('.agent-card[data-agent-id="alpha"]')).to_be_visible()
        rows = panel.locator('.settings-link-row')
        expect(rows).to_have_count(4)
        assert rows.locator('.settings-link-title').all_inner_texts() == [
            'Git', 'Timeouts', 'Add your own agent', 'Routing & identity']
        expect(page.locator('[data-subpage="git"]')).to_be_hidden()
        assert _crumbs(page) == ['Settings', 'Agents']

        rows.filter(has_text='Timeouts').click()
        expect(page.locator('#timeoutsSettings')).to_be_visible()
        # The tab's root view (agent list, other rows) steps aside.
        expect(page.locator('#agentCliList')).to_be_hidden()
        expect(panel.locator('.settings-link-list')).to_be_hidden()
        assert _crumbs(page) == ['Settings', 'Agents', 'Timeouts']
        assert 'page=timeouts' in page.url
        # Lazy loaders already ran for the tab, so the subpage is live.
        expect(page.locator('#agentRunTimeoutSeconds')).to_be_visible()

        page.locator('.settings-crumbs button', has_text='Agents').click()
        expect(page.locator('#timeoutsSettings')).to_be_hidden()
        expect(page.locator('#agentCliList')).to_be_visible()
        expect(rows.filter(has_text='Timeouts')).to_be_focused()
        assert 'page=' not in page.url

        # Switching tab closes any open subpage.
        rows.filter(has_text='Git').click()
        expect(page.locator('[data-subpage="git"]')).to_be_visible()
        page.evaluate("CuttleSettingsTabs.setTab('appearance')")
        assert _crumbs(page) == ['Settings', 'Appearance']
        page.evaluate("CuttleSettingsTabs.setTab('agents')")
        expect(page.locator('[data-subpage="git"]')).to_be_hidden()

        page.goto(static_server + '/settings_page.html?tab=agents&page=routing', wait_until='networkidle')
        expect(page.locator('#agentName')).to_be_visible()
        assert _crumbs(page) == ['Settings', 'Agents', 'Routing & identity']
        assert not errors, errors
    finally:
        page.close()


def test_sidebar_groups_and_keyboard(browser, static_server):
    page = open_settings(browser, static_server, AgentWorld())
    try:
        nav = page.get_by_role('tablist', name='Settings categories')
        expect(nav).to_have_attribute('aria-orientation', 'vertical')
        tabs = nav.get_by_role('tab')
        assert [t.strip() for t in tabs.all_inner_texts()] == [
            'General', 'Appearance', 'Voice', 'Agents', 'Providers', 'Experimental',
            'Devices', 'Data', 'Account']
        expect(nav.get_by_role('tab', name='Agents')).to_have_attribute('aria-selected', 'true')
        nav.get_by_role('tab', name='Agents').focus()
        page.keyboard.press('ArrowDown')
        expect(nav.get_by_role('tab', name='Providers')).to_be_focused()
        expect(page.locator('#panel-providers')).to_be_visible()
        # Sidebar and content sit side by side on desktop.
        side = page.locator('.settings-sidebar').bounding_box()
        main = page.locator('.settings-main').bounding_box()
        assert side['x'] + side['width'] <= main['x']
    finally:
        page.close()


def test_phone_starts_on_the_index_and_taps_into_a_page(browser, static_server):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(static_server + '/api/**', AgentWorld().handle)
    try:
        page.goto(static_server + '/settings_page.html', wait_until='networkidle')
        expect(page.locator('.settings-sidebar')).to_be_visible()
        expect(page.locator('.settings-main')).to_be_hidden()
        page.get_by_role('tab', name='Agents').tap()
        expect(page.locator('.settings-sidebar')).to_be_hidden()
        expect(page.locator('#panel-agents')).to_be_visible()
        assert 'tab=agents' in page.url
        page.locator('.settings-link-row', has_text='Git').tap()
        expect(page.locator('[data-subpage="git"]')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('.settings-crumbs button', has_text='Settings').tap()
        expect(page.locator('.settings-sidebar')).to_be_visible()
        expect(page.locator('.settings-main')).to_be_hidden()
        assert 'tab=' not in page.url and 'page=' not in page.url
        # A deep link opens its page directly, even on a phone.
        page.goto(static_server + '/settings_page.html?tab=data', wait_until='networkidle')
        expect(page.locator('#panel-data')).to_be_visible()
        expect(page.locator('.settings-sidebar')).to_be_hidden()
    finally:
        context.close()
