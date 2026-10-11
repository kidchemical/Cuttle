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

    def handle(self, route):
        path = urlparse(route.request.url).path
        if path == "/api/agents":
            data = {"success": True, "agents": AGENTS, "roots": []}
        elif path == "/api/settings/agent-adapters":
            if route.request.method == "POST":
                body = json.loads(route.request.post_data)
                self.subagents.update(body.get("subagents", {}))
            data = {"success": True, "defaults": {}, "steer": {}, "subagents": self.subagents}
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
