"""Production Agents settings with fake APIs: no host or vendor calls."""
import json
from urllib.parse import urlparse

from playwright.sync_api import expect
from .test_shared_diff_modal import browser, static_server, apply_request_guard, IsolatedAPI  # noqa: F401

AGENTS = [
    {"id": "alpha", "label": "Alpha Agent", "slash": "/alpha", "prefix": "/alpha ",
     "sticky": True, "available": True, "ready": True, "icon": "✦"},
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

    def handle(self, route):
        path = urlparse(route.request.url).path
        if path == "/api/agents":
            data = {"success": True, "agents": AGENTS, "roots": []}
        elif path == "/api/settings/agent-adapters":
            data = {"success": True, "defaults": {}, "steer": {}}
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


def test_default_agent_saves_clears_and_rolls_back(browser, static_server):
    world = AgentWorld()
    page = open_settings(browser, static_server, world)
    try:
        select = page.locator("#defaultAgentSelect")
        expect(select).to_be_enabled()
        expect(select).to_have_value("/alpha ")
        assert select.locator('option[value="/missing "]').evaluate("(opt) => opt.disabled")
        assert select.locator('option[value="/helper "]').count() == 0
        select.select_option("")
        expect(page.locator("#defaultAgentStatus")).to_contain_text("use the router")
        assert world.saves == [{"prefixes": []}]
        assert page.evaluate("localStorage.getItem('cuttleStarredSlashCommands')") == "[]"
        page.reload(wait_until="domcontentloaded")
        expect(select).to_be_enabled()
        expect(select).to_have_value("")
        select.select_option("/alpha ")
        expect(page.locator("#defaultAgentStatus")).to_contain_text("saved")
        assert world.prefixes == ["/alpha "]
        world.fail_save = True
        select.select_option("")
        expect(page.locator("#defaultAgentStatus")).to_contain_text("Owner required")
        expect(select).to_have_value("/alpha ")
        expect(select).to_be_enabled()
        assert page.evaluate("JSON.parse(localStorage.getItem('cuttleStarredSlashCommands'))") == ["/alpha "]
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


def test_removed_default_and_load_failure_preserve_saved_state(browser, static_server):
    world = AgentWorld()
    world.prefixes = ["/removed "]
    page = open_settings(browser, static_server, world)
    try:
        select = page.locator("#defaultAgentSelect")
        expect(select).to_be_enabled()
        expect(select).to_have_value("/removed ")
        expect(select.locator('option[value="/removed "]')).to_contain_text("Unavailable")
        assert world.saves == []
        world.fail_load = True
        page.reload(wait_until="domcontentloaded")
        expect(select).to_contain_text("Could not load")
        expect(select).to_be_disabled()
        assert world.saves == []
    finally:
        page.close()


def test_dropin_declarations_render_as_text_on_phone(browser, static_server, monkeypatch):
    monkeypatch.setitem(AGENTS[1], 'source', 'project')
    monkeypatch.setitem(AGENTS[1], 'permissions', {'filesystem':'workspace','network':'outbound','subprocess':True})
    monkeypatch.setitem(AGENTS[1], 'provenance', {'verified':True,'source_url':'https://example.com/source',
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
