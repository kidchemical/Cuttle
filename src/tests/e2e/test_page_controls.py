"""Real app controls with fake APIs; no host, agents, or paid providers."""
import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from .test_settings_model_providers import ProviderWorld
from .test_settings_agents import open_settings
from .control_helpers import choose
from .test_shared_diff_modal import browser, static_server, apply_request_guard  # noqa: F401


def test_floating_menu_handles_dialogs_keyboard_paging_and_removed_triggers(browser, static_server):
    page = open_settings(browser, static_server, ProviderWorld())
    try:
        page.evaluate('''() => {
            const dialog = document.createElement('dialog');
            dialog.style.cssText = 'position:fixed;inset:auto 10px 10px auto;width:260px;max-height:100px;overflow:hidden';
            dialog.innerHTML = '<label>Choices<select id="menuFixture" aria-label="Choices"></select></label>';
            document.body.appendChild(dialog);
            const select = dialog.querySelector('select');
            for (let i = 0; i < 160; i++) {
                const opt = new Option('Choice ' + i, String(i)); opt.disabled = i === 1; select.add(opt);
            }
            select.onchange = () => { window.lastChoice = select.value; };
            dialog.showModal();
        }''')
        select = page.locator('#menuFixture')
        trigger = select.locator('..').locator('.cuttle-combo-trigger')
        trigger.click()
        menu = page.locator('.cuttle-combo-popup')
        assert menu.bounding_box()['y'] + menu.bounding_box()['height'] <= trigger.bounding_box()['y']
        assert menu.evaluate("el => el.matches(':popover-open')")
        menu.locator('[data-value="1"]').click(force=True)
        expect(select).to_have_value('0')
        expect(menu).to_be_visible()
        menu.locator('[data-value="2"]').click()
        expect(select).to_have_value('2')
        trigger.click()
        trigger.focus()
        page.keyboard.press('End')
        page.keyboard.press('Enter')
        expect(select).to_have_value('159')
        assert page.evaluate('lastChoice') == '159'
        expect(trigger).to_be_focused()
        trigger.click()
        page.keyboard.press('Escape')
        expect(page.locator('dialog[open]')).to_have_count(1)
        expect(page.locator('.cuttle-combo-popup')).to_have_count(0)
        trigger.click()
        select.locator('..').evaluate('el => el.remove()')
        expect(page.locator('.cuttle-combo-popup')).to_have_count(0)
    finally:
        page.close()


def test_touch_menus_keep_search_optional_and_fit_the_phone(browser, static_server):
    context = browser.new_context(has_touch=True, is_mobile=True, viewport={'width': 390, 'height': 844})
    apply_request_guard(context, static_server)
    page = context.new_page()
    page.route(static_server + '/api/**', ProviderWorld().handle)
    try:
        page.goto(static_server + '/settings_page.html?tab=agents', wait_until='domcontentloaded')
        model = page.locator('select[aria-label="Background model 1"]')
        trigger = model.locator('..').locator('.cuttle-combo-trigger')
        expect(trigger).to_be_enabled()
        assert trigger.bounding_box()['height'] == 40
        trigger.tap()
        menu = page.locator('.cuttle-combo-popup')
        expect(menu).to_be_visible()
        assert page.evaluate('document.activeElement.className') != 'cuttle-combo-search'
        menu.locator('[data-value="alpha-new"]').tap()
        expect(model).to_have_value('alpha-new')
        expect(menu).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()


def test_legacy_combobox_presentation_stays_opt_in(browser):
    from pathlib import Path
    web = Path(__file__).resolve().parents[2] / 'web'
    page = browser.new_page()
    try:
        page.set_content('<select id="legacy"><option value="a">Alpha</option><option value="b">Beta</option></select>')
        page.add_style_tag(path=str(web / 'css/searchable_select.css'))
        page.add_script_tag(path=str(web / 'js/shared/searchable_select.js'))
        page.evaluate("CuttleSearchableSelect.enhance(document.getElementById('legacy'), {label:'Legacy model'})")
        page.locator('.cuttle-combo-trigger').click()
        panel = page.locator('.cuttle-combo-panel')
        expect(panel).to_be_visible()
        assert panel.evaluate("el => getComputedStyle(el).position") == 'static'
        assert panel.evaluate("el => el.parentElement.classList.contains('cuttle-combo')")
        assert page.locator('.cuttle-combo-popup').count() == 0
        expect(panel.locator('input')).to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize('width', [320, 1200])
def test_apps_launcher_and_context_menu_fit_the_shell_panel(browser, static_server, width):
    context = browser.new_context(viewport={'width': width, 'height': 900})
    apply_request_guard(context, static_server)
    context.add_init_script("localStorage.setItem('theme', 'light')")
    page = context.new_page()
    apps = [{'id': 'nav-' + name, 'label': name.replace('_', ' ').title(), 'page': '/' + name + '_page.html', 'pinned': index < 3}
            for index, name in enumerate(['settings', 'projects', 'jobs', 'mesh', 'dashboards', 'achievements', 'gizmos', 'test_lab'])]
    host = '<script>addEventListener("message", e => { if (e.data.type === "cuttle-apps-request") e.source.postMessage({type:"cuttle-apps", apps:' + json.dumps(apps) + '}, location.origin); });</script>'
    host += '<body style="margin:0"><iframe id="apps" src="/apps_page.html" style="border:0;width:100%;height:100vh"></iframe></body>'
    page.route(static_server + '/__controls_host', lambda route: route.fulfill(content_type='text/html', body=host))
    try:
        page.goto(static_server + '/__controls_host', wait_until='networkidle')
        frame = page.frame_locator('#apps')
        expect(frame.locator('.apps-tile')).to_have_count(len(apps))
        frame.locator('.apps-tile').last.click(button='right')
        menu = frame.locator('#appsMenu')
        expect(menu).to_be_visible()
        assert menu.evaluate('el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }')
        page.screenshot(path=f'temp/ui-apps-frame-{width}.png')
        page.keyboard.press('Escape')
        expect(menu).to_be_hidden()
        frame.locator('#appsSearch').fill('test')
        expect(frame.locator('.apps-tile')).to_have_count(1)
        assert frame.locator('body').evaluate('() => document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()


@pytest.mark.parametrize('theme', ['dark', 'midnight', 'light', 'sakura', 'mint', 'lavender', 'paper', 'graphite', 'nord', 'rgb', 'synthwave', 'glitch', 'matrix'])
@pytest.mark.parametrize('width', [320, 390, 1200])
def test_settings_dropdowns_float_without_resizing_and_share_the_skin(browser, static_server, theme, width):
    page = open_settings(browser, static_server, ProviderWorld(), width=width)
    try:
        expect(page.locator('.agent-card[data-agent-id="opencode"]')).to_be_visible()
        page.add_style_tag(content='*, *::before, *::after { transition: none !important; animation: none !important; }')
        page.evaluate('(theme) => CuttleUiBoot.applyTheme(theme)', theme)
        add = page.locator('#backgroundAdd')
        save = page.get_by_role('button', name='Save background helpers')
        expect(add).to_be_enabled()
        assert abs(add.bounding_box()['height'] - save.bounding_box()['height']) < 1
        expect(page.get_by_role('button', name='About Background helpers', exact=True)).to_have_count(1)
        assert page.locator('#backgroundHelpersSettings > .setting-description').count() == 0
        model = page.locator('select[aria-label="Background model 1"]')
        trigger = model.locator('..').locator('.cuttle-combo-trigger')
        trigger.scroll_into_view_if_needed()
        before = page.evaluate('({height: document.documentElement.scrollHeight, y: document.getElementById("backgroundAdd").getBoundingClientRect().top})')
        trigger.click()
        menu = page.locator('.cuttle-combo-popup')
        expect(menu).to_be_visible()
        assert menu.evaluate('(el) => getComputedStyle(el).position') == 'fixed'
        assert menu.evaluate('(el) => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }')
        after = page.evaluate('({height: document.documentElement.scrollHeight, y: document.getElementById("backgroundAdd").getBoundingClientRect().top})')
        assert before == after
        search = menu.locator('input')
        search.fill('alpha-new')
        page.keyboard.press('ArrowDown')
        page.keyboard.press('Enter')
        expect(model).to_have_value('alpha-new')
        expect(menu).to_be_hidden()
        # The short non-search menu has the same selected/hover styling.
        page.evaluate("CuttleSettingsTabs.setTab('appearance')")
        theme_select = page.locator('#themeSelect')
        theme_trigger = theme_select.locator('..').locator('.cuttle-combo-trigger')
        assert abs(theme_trigger.bounding_box()['height'] - trigger.evaluate('(el) => parseFloat(getComputedStyle(el).height)')) < 1
        theme_trigger.click()
        menu = page.locator('.cuttle-combo-popup')
        expect(menu.locator('input')).to_be_hidden()
        expect(menu.locator('[aria-selected="true"]')).to_have_count(1)
        assert menu.evaluate('(el) => getComputedStyle(el).color') == theme_trigger.evaluate('(el) => getComputedStyle(el).color')
        assert menu.evaluate('''el => {
            const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
            function luminance(color) {
                ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
                const rgb = Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3).map(v => {
                    v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
                });
                return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
            }
            const style = getComputedStyle(el), a = luminance(style.color), b = luminance(style.backgroundColor);
            return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 4.5;
        }''')
        page.screenshot(path=f'temp/ui-settings-{theme}-{width}.png')
        page.keyboard.press('Escape')
        expect(menu).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        page.close()


def test_subagent_controls_save_and_restore_after_a_rejected_save(browser, static_server):
    world = ProviderWorld()
    page = open_settings(browser, static_server, world)
    try:
        card = page.locator('.agent-card[data-agent-id="opencode"]')
        card.locator('.agent-card-head').click()
        subagent = card.locator('.agent-config-row').filter(has_text='Default subagent').locator('select')
        choose(page, subagent, 'opencode')
        expect(subagent).to_be_enabled()
        model = card.locator('.agent-config-row').filter(has_text='Subagent model').locator('select')
        effort = card.locator('.agent-config-row').filter(has_text='Subagent effort').locator('select')
        choose(page, model, 'openrouter/model')
        expect(model).to_be_enabled()
        choose(page, effort, 'high')
        expect(effort).to_be_enabled()
        assert world.subagents['opencode'] == {'agent': 'opencode', 'model': 'openrouter/model', 'effort': 'high'}
        page.route('**/api/settings/agent-adapters', lambda route: route.fulfill(
            status=400, content_type='application/json', body=json.dumps({'success': False, 'error': 'Rejected fixture'})))
        choose(page, effort, 'low')
        expect(effort).to_have_value('high')
        expect(effort).to_be_enabled()
    finally:
        page.close()


@pytest.mark.parametrize('page_name', ['apps', 'projects', 'jobs', 'mesh', 'dashboards', 'achievements', 'gizmos', 'test_lab', 'git_graph', 'router', 'agent_feed'])
@pytest.mark.parametrize('width', [320, 1200])
def test_app_pages_fit_narrow_panels_and_use_the_shared_controls(browser, static_server, page_name, width):
    from .test_projects_app import FakeAPI
    from .test_router_editor import RouterAPI
    context = browser.new_context(viewport={'width': width, 'height': 900})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    world = ProviderWorld()
    def handle(route):
        path = urlparse(route.request.url).path
        payload = {'success': True, 'data': [], 'jobs': [], 'devices': [], 'workers': [], 'entries': [],
                   'dashboards': [], 'gizmos': [], 'types': [], 'projects': [], 'achievements': []}
        if page_name == 'projects': return projects.handle(route)
        if page_name == 'git_graph': return world.handle(route)
        if page_name == 'router' and path.startswith('/api/'): return router.handle(route)
        if path == '/api/dashboards':
            payload['dashboards'] = [{'id': 'example', 'title': 'Example dashboard', 'description': 'Example', 'available': True}]
        if path == '/api/achievements':
            payload.update(total=1, unlocked=1, items=[{'id': 'first', 'category': 'activity', 'title': 'A milestone with a long title', 'description': 'Completed a project task.', 'unlocked': True, 'percent': 100, 'icon': '★'}])
        if path == '/api/gizmos':
            payload.update(enabled=True, revision=0, types=[{'id': 'usage_meter', 'label': 'Usage meter', 'options': {'agents': [{'id': 'codex', 'label': 'Codex'}], 'metrics': ['auto', 'percent']}}])
        if path == '/api/test-lab/projects': payload['projects'] = [{'id': 7, 'name': 'A project with a longer descriptive name', 'available': True}]
        if path == '/api/test-lab/projects/7':
            payload.update(suite_count=1, test_file_count=42, suites=[{'id': 'pytest-root', 'framework': 'pytest', 'label': 'Python regression suite', 'location': 'src/tests', 'test_files': 42}],
                           runs=[{'id': 'run-1', 'suite_id': 'pytest-root', 'suite_label': 'Python regression suite', 'status': 'passed', 'started_at': 1791660000, 'duration_s': 3.2}])
        if path == '/api/jobs': payload['jobs'] = [{'id': 'job-1', 'kind': 'run', 'label': 'A completed task with a longer name', 'status': 'succeeded', 'started_at': 1791660000, 'duration_s': 3.2, 'metadata': {'model': 'example-model', 'cost': .01}, 'actions': []}]
        if path == '/api/workers': payload.update(enabled=True, workers=[{'worker_id': 'host-1', 'hostname': 'A workstation with a long name', 'online': True, 'is_self': True, 'capabilities': []}])
        if path.startswith('/api/'): return route.fulfill(content_type='application/json', body=json.dumps(payload))
        return world.handle(route)
    projects = FakeAPI()
    router = RouterAPI()
    page.route(static_server + '/api/**', handle)
    name = {'router': 'router_editor.html', 'agent_feed': 'agent_feed.html'}.get(page_name, page_name + '_page.html')
    try:
        page.goto(static_server + '/' + name, wait_until='networkidle')
        assert page.locator('link[href*="page_controls.css"]').count() == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page_name
        page.add_style_tag(content='*, *::before, *::after { transition: none !important; animation: none !important; }')
        page.evaluate("CuttleUiBoot.applyTheme('light')")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page_name
        page.screenshot(path=f'temp/ui-{page_name}-{width}.png')
        assert not errors, errors
    finally:
        context.close()
