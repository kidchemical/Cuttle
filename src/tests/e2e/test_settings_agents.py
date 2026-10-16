"""Production Agents settings with fake APIs: no host or vendor calls."""
import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from .control_helpers import choose
from .test_shared_diff_modal import browser, static_server, apply_request_guard, IsolatedAPI  # noqa: F401

AGENTS = [
    {"id": "alpha", "label": "Alpha Agent", "slash": "/alpha", "prefix": "/alpha ",
     "sticky": True, "available": True, "ready": True, "icon": "✦",
     "supports_model_pin": True, "supports_effort": True,
     "models": ["alpha-model"], "efforts": ["low", "high"]},
    {"id": "opencode", "label": "OpenCode", "slash": "/opencode", "prefix": "/opencode ",
     "sticky": True, "available": True, "ready": True,
     "supports_model_pin": True, "supports_effort": True,
     "models": ["openrouter/model"], "efforts": ["low", "high"]},
    {"id": "missing", "label": "A very long drop-in agent label that must wrap on a phone",
     "slash": "/missing", "prefix": "/missing ", "sticky": True,
     "available": False, "ready": False, "install_hint": "Install this tool yourself."},
    {"id": "helper", "label": "Non-sticky helper", "slash": "/helper",
     "sticky": False, "available": True, "ready": True},
]


class AgentWorld(IsolatedAPI):
    def __init__(self):
        super().__init__()
        self.prefixes = ["/alpha "]
        self.saves = []
        self.fail_save = False
        self.fail_load = False
        self.subagents = {}
        self.cli_paths = {'missing': {'path': '', 'env_var': 'MISSING_CLI_PATH', 'env_path': '', 'error': ''}}
        self.path_saves = []
        self.fail_path_save = False
        self.prompts = {'global': '', 'agents': {}}
        self.processor = {'enabled': False, 'prompt': 'Give me a TLDR', 'mode': 'append', 'chain': []}
        self.executor_targets = [
            {'kind': 'api', 'key': 'api:local', 'target': 'local', 'label': 'Local API', 'configured': True, 'suggested_models': ['api-model']},
            {'kind': 'cli', 'key': 'cli:alpha', 'target': 'alpha', 'label': 'Alpha CLI', 'configured': True, 'suggested_models': ['cli-model'], 'auth_modes': ['subscription']},
        ]
        self.fail_preferences = False
        self.processor_flag = False
        self.kill_switch = False
        self.availability_error = False
        self.availability_pending = None
        self.background_preferences = {'chain': [], 'tasks': {}}
        self.background_saves = []

    def handle(self, route):
        path = urlparse(route.request.url).path
        available = self.processor_flag and not self.kill_switch
        if path.startswith('/api/settings/response-postprocessing/') and path.endswith('/models'):
            model = 'cli-model' if '/cli/' in path else 'api-model'
            data = {'success': True, 'models': [{'id': model, 'availability': 'available', 'compatibility': 'supported',
                    'capabilities': {'reasoning_efforts': ['low', 'high']}}], 'default_efforts': ['low', 'high']}
        elif path.startswith('/api/experimental/flags'):
            if route.request.method == 'POST':
                self.processor_flag = json.loads(route.request.post_data)['enabled']
            flag = {'id': 'response_postprocessing', 'label': 'Response post-processing',
                    'enabled': self.processor_flag and not self.kill_switch,
                    'configured_enabled': self.processor_flag}
            data = {'success': True, 'flags': [flag], 'flag': flag, 'kill_switch': self.kill_switch}
        elif path == '/api/settings/background-helpers':
            if route.request.method == 'POST':
                self.background_preferences = json.loads(route.request.post_data)
                self.background_saves.append(self.background_preferences)
            tasks = {'titles': 'Chat titles'}
            data = {'success': True, 'tasks': tasks, 'preferences': self.background_preferences,
                    'targets': [], 'legacy': {}}
        elif path in ('/api/settings/chat-prompts', '/api/settings/response-postprocessing'):
            if path.endswith('response-postprocessing') and route.request.method == 'GET':
                if self.availability_pending is not None:
                    self.availability_pending.append(route)
                    return
                if self.availability_error:
                    route.fulfill(status=503, content_type='application/json', body='{}')
                    return
            if route.request.method == 'POST':
                if self.fail_preferences:
                    route.fulfill(status=403, content_type='application/json', body=json.dumps({'success': False, 'error': 'Owner required'}))
                    return
                body = json.loads(route.request.post_data)
                if path.endswith('chat-prompts'):
                    if 'global' in body: self.prompts['global'] = body['global']
                    self.prompts['agents'].update(body.get('agents', {}))
                else:
                    self.processor = body
            data = ({'success': True, 'prompts': self.prompts} if path.endswith('chat-prompts')
                    else {'success': True, 'preferences': self.processor, 'available': available,
                          'targets': self.executor_targets if available else []})
        elif path == "/api/agents":
            data = {"success": True, "agents": AGENTS, "roots": []}
        elif path == "/api/settings/agent-adapters":
            if route.request.method == "POST":
                body = json.loads(route.request.post_data)
                self.subagents.update(body.get("subagents", {}))
                if 'cli_paths' in body:
                    self.path_saves.append(body['cli_paths'])
                    if self.fail_path_save:
                        route.fulfill(status=400, content_type='application/json',
                                      body=json.dumps({'success': False, 'error': 'Executable does not exist'}))
                        return
                    for agent, path in body['cli_paths'].items():
                        self.cli_paths[agent]['path'] = path
            data = {"success": True, "defaults": {}, "steer": {}, "subagents": self.subagents,
                    "cli_paths": self.cli_paths, "system_prompts": self.prompts}
        elif path == "/api/settings/starred-slash":
            if route.request.method == "POST":
                body = json.loads(route.request.post_data)
                self.saves.append(body)
                if self.fail_save:
                    route.fulfill(status=403, content_type="application/json",
                                  body=json.dumps({"success": False, "error": "Owner required"}))
                    return
                self.prefixes = body["prefixes"]
            elif self.fail_load:
                route.fulfill(status=503, content_type="application/json", body='{}')
                return
            data = {"success": True, "prefixes": self.prefixes}
        else:
            return super().handle(route)
        route.fulfill(content_type="application/json", body=json.dumps(data))


def open_settings(browser, static_server, world, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 900})
    apply_request_guard(page.context, static_server)
    page.route(static_server + "/api/**", world.handle)
    page.goto(static_server + "/settings_page.html?tab=agents", wait_until="domcontentloaded")
    return page


@pytest.mark.parametrize('width', [1280, 390])
def test_prompt_editors_and_processor_roundtrip(browser, static_server, width):
    world = AgentWorld()
    world.processor_flag = True
    world.prompts = {'global': 'Shared instructions', 'agents': {'alpha': 'Alpha instructions'}}
    page = open_settings(browser, static_server, world, width=width)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        prompt = card.get_by_role('textbox', name='Alpha Agent system prompt', exact=True)
        expect(prompt).to_have_value('Alpha instructions')
        prompt.fill('Explain with examples')
        card.get_by_role('button', name='Save prompt', exact=True).click()
        expect(card.get_by_role('status').last).to_contain_text('Saved.')
        assert world.prompts['agents']['alpha'] == 'Explain with examples'
        assert world.prompts['global'] == 'Shared instructions'

        processor = page.locator('#responsePostprocessingEditor')
        processor.get_by_role('switch', name='Process completed replies').click()
        processor.get_by_role('textbox', name='Post-processing instructions').fill('Three bullet TLDR')
        choose(page, processor.locator('select'), 'replace')
        processor.get_by_role('button', name='Save post-processing').click()
        expect(processor.get_by_role('status').last).to_contain_text('saved')
        assert world.processor == {'enabled': True, 'prompt': 'Three bullet TLDR', 'mode': 'replace', 'chain': []}

        page.get_by_role('tab', name='General').click()
        shared = page.locator('#sharedSystemPromptEditor')
        expect(shared.get_by_role('textbox')).to_have_value('Shared instructions')
        shared.get_by_role('textbox').fill('Use short sentences')
        shared.get_by_role('button', name='Save prompt').click()
        expect(shared.get_by_role('status')).to_contain_text('Saved.')
        assert world.prompts['global'] == 'Use short sentences'
        assert world.prompts['agents']['alpha'] == 'Explain with examples'
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
        assert errors == []

        page.reload(wait_until='domcontentloaded')
        page.get_by_role('tab', name='General').click()
        expect(page.locator('#sharedSystemPromptEditor textarea')).to_have_value('Use short sentences')
        page.get_by_role('tab', name='Agents', exact=False).click()
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        expect(card.get_by_role('textbox', name='Alpha Agent system prompt', exact=True)).to_have_value('Explain with examples')
    finally:
        page.close()


def test_prompt_save_error_keeps_draft(browser, static_server):
    world = AgentWorld()
    world.fail_preferences = True
    page = open_settings(browser, static_server, world)
    try:
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        prompt = card.get_by_role('textbox', name='Alpha Agent system prompt', exact=True)
        prompt.fill('Keep this draft')
        card.get_by_role('button', name='Save prompt', exact=True).click()
        expect(card.get_by_role('status').last).to_contain_text('Owner required')
        expect(prompt).to_have_value('Keep this draft')
        expect(prompt).to_be_enabled()
        assert world.prompts['agents'] == {}
    finally:
        page.close()


@pytest.mark.parametrize('state', ['off', 'kill_switch', 'lookup_error'])
def test_disabled_processor_hides_panel_and_task_but_keeps_prompts(browser, static_server, state):
    world = AgentWorld()
    world.processor_flag = state != 'off'
    world.kill_switch = state == 'kill_switch'
    world.availability_error = state == 'lookup_error'
    page = open_settings(browser, static_server, world)
    try:
        expect(page.locator('#responsePostprocessingSettings')).to_be_hidden()
        expect(page.locator('#responsePostprocessingEditor textarea')).to_have_count(0)
        if state != 'lookup_error':
            expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        expect(card.get_by_role('textbox', name='Alpha Agent system prompt', exact=True)).to_be_visible()
        page.get_by_role('tab', name='General').click()
        expect(page.locator('#sharedSystemPromptEditor textarea')).to_be_visible()
    finally:
        page.close()


def test_processor_starts_hidden_while_availability_is_pending(browser, static_server):
    world = AgentWorld()
    world.processor_flag = True
    world.availability_pending = []
    page = open_settings(browser, static_server, world)
    try:
        expect(page.locator('#agentCliList .agent-card')).to_have_count(len(AGENTS))
        assert world.availability_pending
        expect(page.locator('#responsePostprocessingSettings')).to_be_hidden()
        expect(page.locator('#responsePostprocessingEditor textarea')).to_have_count(0)
        world.availability_pending.pop().fulfill(content_type='application/json', body=json.dumps(
            {'success': True, 'available': True, 'preferences': world.processor}))
        expect(page.locator('#responsePostprocessingSettings')).to_be_visible()
    finally:
        page.close()


def test_processor_flag_toggle_updates_ui_without_losing_saved_settings(browser, static_server):
    world = AgentWorld()
    world.processor = {'enabled': True, 'prompt': 'Saved summary instructions', 'mode': 'replace', 'chain': []}
    saved_chain = [{'kind': 'api', 'target': 'local', 'model': 'saved-model', 'effort': ''}]
    world.background_preferences['tasks']['postprocessing'] = saved_chain
    page = open_settings(browser, static_server, world)
    try:
        expect(page.locator('#backgroundTask option[value="titles"]')).to_have_count(1)
        expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
        page.get_by_role('button', name='Save background helpers').click()
        assert world.background_preferences['tasks']['postprocessing'] == saved_chain

        def toggle():
            page.get_by_role('tab', name='Experimental').click()
            page.locator('#expFlagToggle-response_postprocessing').click()
            page.get_by_role('tab', name='Agents', exact=False).click()

        toggle()
        expect(page.locator('#responsePostprocessingSettings')).to_be_visible()
        expect(page.locator('#responsePostprocessingEditor textarea')).to_have_value('Saved summary instructions')
        expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
        # Existing, enabled editor keeps working after a flag catalog refresh.
        page.locator('#responsePostprocessingEditor textarea').fill('Updated instructions')
        page.get_by_role('button', name='Save post-processing').click()
        expect(page.locator('#responsePostprocessingEditor [role="status"]').last).to_contain_text('saved')

        toggle()
        expect(page.locator('#responsePostprocessingSettings')).to_be_hidden()
        expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
        expect(page.locator('#backgroundTask')).to_have_value('')
        assert world.processor['prompt'] == 'Updated instructions'
        assert world.background_preferences['tasks']['postprocessing'] == saved_chain

        toggle()
        expect(page.locator('#responsePostprocessingEditor textarea')).to_have_value('Updated instructions')
        expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
    finally:
        page.close()


@pytest.mark.parametrize('kind', ['api', 'cli'])
def test_processor_has_independent_executor_toggle_and_info_tooltips(browser, static_server, kind):
    world = AgentWorld()
    world.processor_flag = True
    page = open_settings(browser, static_server, world, width=390)
    try:
        processor = page.locator('#responsePostprocessingEditor')
        toggle = processor.get_by_role('switch', name='Process completed replies')
        expect(toggle).to_have_class('toggle-switch')
        bounds = toggle.bounding_box()
        assert bounds['width'] == 50 and bounds['height'] == 24
        assert processor.get_by_role('textbox', name='Post-processing instructions').bounding_box()['height'] >= 110
        toggle.focus()
        toggle.press('Space')
        expect(toggle).to_have_attribute('aria-checked', 'true')
        expect(toggle).to_have_class('toggle-switch active')
        processor.get_by_role('button', name='About post-processing', exact=True).click()
        expect(page.locator('#cuttle-shared-tooltip')).to_contain_text('extra text pass')
        assert processor.locator('p.setting-description').count() == 0
        processor.get_by_role('button', name='Add executor', exact=True).click()
        target = processor.locator('select[aria-label="Post-processing executor 1"]')
        choose(page, target, 'cli:alpha' if kind == 'cli' else 'api:local')
        model = processor.locator('select[aria-label="Post-processing model 1"]')
        choose(page, model, 'cli-model' if kind == 'cli' else 'api-model')
        effort = processor.locator('select[aria-label="Post-processing effort 1"]')
        expect(effort.locator('option[value="high"]')).to_have_count(1)
        choose(page, effort, 'high')
        processor.get_by_role('button', name='Save post-processing', exact=True).click()
        expect(processor.get_by_role('status').last).to_contain_text('saved')
        selected = world.processor['chain'][0]
        assert selected['kind'] == kind and selected['effort'] == 'high'
        assert selected['model'] == ('cli-model' if kind == 'cli' else 'api-model')
        assert world.processor['enabled'] is True
        assert world.background_saves == []
        assert world.background_preferences == {'chain': [], 'tasks': {}}
        expect(page.locator('#backgroundTask option[value="postprocessing"]')).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#responsePostprocessingEditor select[aria-label="Post-processing model 1"]')).to_have_value(selected['model'])
        expect(page.locator('#responsePostprocessingEditor [role="switch"]')).to_have_attribute('aria-checked', 'true')
    finally:
        page.close()


def test_default_agent_button_saves_and_failed_save_keeps_default_unset(browser, static_server):
    world = AgentWorld()
    world.prefixes = []
    page = open_settings(browser, static_server, world)
    try:
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        button = card.get_by_role("button", name="Set as default")
        button.click()
        assert world.prefixes == ["/alpha "]
        expect(card.locator('.agent-default-chip')).to_have_text("Default")
        expect(card.get_by_role("button", name="Default agent")).to_be_disabled()
        assert page.evaluate("JSON.parse(localStorage.getItem('cuttleStarredSlashCommands'))") == ["/alpha "]
    finally:
        page.close()

    world = AgentWorld()
    world.prefixes = []
    world.fail_save = True
    page = open_settings(browser, static_server, world)
    try:
        card = page.locator('.agent-card[data-agent-id="alpha"]')
        card.locator('.agent-card-head').click()
        card.get_by_role("button", name="Set as default").click()
        expect(card.get_by_role("button", name="Set as default")).to_be_enabled()
        expect(card.locator('.agent-default-chip')).to_have_count(0)
        assert world.prefixes == []
    finally:
        page.close()


def test_rows_keyboard_reload_and_phone_layout(browser, static_server):
    world = AgentWorld()
    page = open_settings(browser, static_server, world, width=390)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        head = page.locator('.agent-card[data-agent-id="missing"] .agent-card-head')
        detail = page.locator("#agent-detail-missing")
        expect(head).to_have_attribute("aria-expanded", "false")
        expect(detail).to_be_hidden()
        head.focus()
        page.keyboard.press("Enter")
        expect(detail).to_be_visible()
        expect(head).to_have_attribute("aria-controls", "agent-detail-missing")
        expect(detail).to_contain_text("Install this tool yourself.")
        page.evaluate("loadAgentCatalog()")
        expect(head).to_have_attribute("aria-expanded", "true")
        expect(detail).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("#agentAdvanced").get_attribute("open") is None
        page.locator("#panel-agents").screenshot(path="temp/settings-agents-mobile.png")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.locator("#panel-agents").screenshot(path="temp/settings-agents-desktop.png")
        assert not errors
    finally:
        page.close()


def test_subagent_model_and_effort_only_show_for_non_native_choice(browser, static_server):
    world = AgentWorld()
    page = open_settings(browser, static_server, world)
    try:
        card = page.locator('.agent-card[data-agent-id="opencode"]')
        card.locator('.agent-card-head').click()
        model_row = card.locator('.agent-config-row').filter(has_text='Subagent model')
        effort_row = card.locator('.agent-config-row').filter(has_text='Subagent effort')
        expect(card.locator('.agent-config-row').filter(has_text='Default model').first).to_be_visible()
        expect(card.locator('.agent-config-row').filter(has_text='Default effort').first).to_be_visible()
        expect(model_row).to_be_hidden()
        expect(effort_row).to_be_hidden()

        with page.expect_response(lambda response: response.url.endswith('/api/settings/agent-adapters')
                                  and response.request.method == 'POST') as saved:
            choose(page, card.locator('.agent-config-row').filter(has_text='Default subagent').locator('select'), 'alpha')
        expect(model_row).to_be_visible()
        expect(effort_row).to_be_visible()
        assert json.loads(saved.value.request.post_data)['subagents'] == {
            'opencode': {'agent':'alpha','model':'','effort':''}
        }
    finally:
        page.close()


@pytest.mark.parametrize('width', [1280, 390])
def test_agent_dropdown_panel_is_anchored_to_clicked_control(browser, static_server, width):
    world = AgentWorld()
    world.subagents = {'opencode': {'agent': 'opencode', 'model': 'openrouter/model', 'effort': 'high'}}
    page = open_settings(browser, static_server, world, width=width)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        card = page.locator('.agent-card[data-agent-id="opencode"]')
        card.locator('.agent-card-head').click()
        for label in ['Default model', 'Default effort', 'Default subagent', 'Subagent model', 'Subagent effort']:
            select = card.locator('.agent-config-row').filter(has_text=label).locator('select')
            trigger = select.locator('xpath=..').locator('.cuttle-combo-trigger')
            trigger.evaluate("element => element.scrollIntoView({block: 'center'})")
            before = card.bounding_box()
            trigger.click()
            panel = page.locator('.cuttle-combo-popup')
            expect(panel).to_have_count(1)
            expect(panel).to_be_visible()
            geometry = page.evaluate("""(select) => {
            const panel = document.querySelector('.cuttle-combo-popup');
            const trigger = select.__cuttleSearchable.trigger;
            const pr = panel.getBoundingClientRect(), tr = trigger.getBoundingClientRect();
            const ps = getComputedStyle(panel), ts = getComputedStyle(trigger);
            return {parentIsBody: panel.parentElement === document.body, top: pr.top, bottom: pr.bottom,
                triggerTop: tr.top, triggerBottom: tr.bottom,
                left: pr.left, triggerLeft: tr.left,
                panelCorner: ps.borderTopLeftRadius, triggerCorner: ts.borderBottomLeftRadius,
                panelBorder: ps.borderTopColor, triggerBorder: ts.borderBottomColor,
                ownList: panel.querySelector('.cuttle-combo-list').id === trigger.getAttribute('aria-controls')};
            }""", select.element_handle())
            assert geometry['parentIsBody']
            assert geometry['ownList']
            assert abs(geometry['left'] - geometry['triggerLeft']) < 2
            # The panel and trigger share an edge, with no detached gap.
            assert abs(geometry['top'] - geometry['triggerBottom'] + 1) < 1, geometry
            assert geometry['panelCorner'] == geometry['triggerCorner'] == '0px'
            assert geometry['panelBorder'] == geometry['triggerBorder']
            assert card.bounding_box() == before
            if label in ['Default model', 'Subagent model']:
                expect(panel).to_contain_text('openrouter/model')
            page.keyboard.press('Escape')
            expect(panel).to_have_count(0)
        assert not errors
    finally:
        page.close()


def test_removed_default_is_not_assigned_to_another_agent(browser, static_server):
    world = AgentWorld()
    world.prefixes = ["/removed "]
    page = open_settings(browser, static_server, world)
    try:
        expect(page.locator("#defaultAgentSelect")).to_have_count(0)
        expect(page.locator('.agent-default-chip')).to_have_count(0)
        assert world.saves == []
    finally:
        page.close()


def test_dropin_declarations_render_as_text_on_phone(browser, static_server, monkeypatch):
    monkeypatch.setitem(AGENTS[2], 'source', 'project')
    monkeypatch.setitem(AGENTS[2], 'permissions', {'filesystem':'workspace','network':'outbound','subprocess':True})
    monkeypatch.setitem(AGENTS[2], 'provenance', {'verified':True,'source_url':'https://example.com/source',
        'revision':'<img src=x onerror=alert(1)>'})
    page = open_settings(browser, static_server, AgentWorld(), width=390)
    try:
        page.locator('.agent-card[data-agent-id="missing"] .agent-card-head').click()
        detail = page.locator('#agent-detail-missing')
        expect(detail).to_contain_text('Declared access: files workspace; network outbound; subprocess yes')
        expect(detail).to_contain_text('Trusted Python code')
        expect(detail).to_contain_text('Declared SHA-256 files verified.')
        expect(detail).to_contain_text('<img src=x onerror=alert(1)>')
        assert detail.locator('img[src=x]').count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        page.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_missing_agent_wrapper_path_save_error_and_reset(browser, static_server, width):
    world = AgentWorld()
    page = open_settings(browser, static_server, world, width=width)
    try:
        card = page.locator('.agent-card[data-agent-id="missing"]')
        card.locator('.agent-card-head').click()
        path = card.get_by_role('textbox', name='CLI executable or wrapper', exact=False)
        expect(path).to_be_visible()
        path.fill('/home/user/wrappers/agent sandbox')
        card.get_by_role('button', name='Save path', exact=True).click()
        expect(card.locator('.agent-cli-path').get_by_role('status')).to_contain_text('Path saved for new launches.')
        assert world.path_saves[-1] == {'missing': '/home/user/wrappers/agent sandbox'}
        world.fail_path_save = True
        path.fill('/missing/wrapper')
        card.get_by_role('button', name='Save path', exact=True).click()
        expect(card.locator('.agent-cli-path').get_by_role('status')).to_contain_text('Executable does not exist')
        expect(path).to_have_value('/missing/wrapper')
        expect(path).to_be_enabled()
        world.fail_path_save = False
        card.get_by_role('button', name='Use default', exact=True).click()
        expect(card.locator('.agent-cli-path').get_by_role('status')).to_contain_text('Default discovery restored.')
        expect(path).to_have_value('')
        assert world.path_saves[-1] == {'missing': ''}
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        page.close()
