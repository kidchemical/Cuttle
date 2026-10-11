"""Production background helper settings with fake API/CLI catalogs."""
import json
from urllib.parse import urlparse
from playwright.sync_api import expect
from .control_helpers import choose
from .test_settings_agents import AgentWorld, open_settings
from .test_shared_diff_modal import browser, static_server  # noqa: F401

ENDPOINT = '/api/settings/background-helpers'


class ProviderWorld(AgentWorld):
    def __init__(self):
        super().__init__()
        self.preferences = {'chain':[{'kind':'api','target':'alpha','model':'saved-model','effort':'','temperature':None,'auth':''}], 'tasks':{}}
        self.model_requests = []
        self.pending = None
        self.fail_models = False
        self.delay = False

    def payload(self):
        return {'success':True, 'preferences':self.preferences, 'tasks':{'titles':'Chat titles','narration':'Reply narration'}, 'targets':[
            {'id':'alpha','target':'alpha','key':'api:alpha','label':'Alpha','kind':'api','configured':True,'default_model':'alpha-default','suggested_models':['alpha-default']},
            {'id':'beta','target':'beta','key':'cli:beta','label':'Beta CLI','kind':'cli','configured':True,'default_model':'','suggested_models':['beta-new'],'auth_modes':['subscription','cli_default'],'auth_command':'beta login'}]}

    def result(self, mid):
        control = {'supported':mid == 'alpha','min':0,'max':1 if mid == 'alpha' else 2,'reason':'CLI temperature unavailable'}
        return {'success':True,'source':'provider_api' if mid == 'alpha' else 'cli','models':[
            {'id':mid+'-new','compatibility':'supported','availability':'available','capabilities':{'reasoning_efforts':['low','high'],'temperature_control':control}},
            {'id':mid+'-image','compatibility':'unsupported','availability':'available'}], 'default_efforts':['low']}

    def handle(self, route):
        path = urlparse(route.request.url).path
        if path == ENDPOINT:
            if route.request.method == 'POST':
                data = json.loads(route.request.post_data); self.saves.append(data)
                if self.fail_save:
                    route.fulfill(status=500,content_type='application/json',body=json.dumps({'success':False,'error':'Save failed'})); return
                self.preferences = data
            data = self.payload()
        elif path.startswith(ENDPOINT + '/'):
            mid = path.split('/')[-2]; self.model_requests.append(route.request.url)
            if self.delay and mid == 'alpha': self.pending = route; return
            if self.fail_models:
                route.fulfill(status=502,content_type='application/json',body=json.dumps({'success':False,'error':'List unavailable'})); return
            data = self.result(mid)
        else: return super().handle(route)
        route.fulfill(content_type='application/json',body=json.dumps(data))


def show_helpers(page):
    expect(page.locator('.agent-card[data-agent-id="opencode"]')).to_be_visible()
    page.evaluate("CuttleSettingsTabs.setTab('agents')")
    expect(page.locator('select[aria-label="Background model 1"]')).to_have_value('saved-model')


def test_picker_custom_refresh_temperature_failure_and_phone(browser, static_server):
    world = ProviderWorld(); page = open_settings(browser, static_server, world, width=390)
    try:
        show_helpers(page)
        select = page.locator('select[aria-label="Background model 1"]')
        expect(page.locator('#backgroundChain')).to_contain_text('Account model list')
        assert select.locator('option[value="alpha-image"]').count() == 0
        choose(page, select, 'alpha-new')
        temperature = page.get_by_label('Background temperature 1', exact=True)
        expect(temperature).to_be_enabled(); expect(temperature).to_have_attribute('max','1'); temperature.fill('.65')
        page.get_by_role('button',name='Refresh models',exact=True).click()
        expect(page.get_by_role('button',name='Refresh models',exact=True)).to_be_enabled()
        expect(select).to_have_value('alpha-new'); expect(temperature).to_have_value('.65')
        assert any('?refresh=1' in url for url in world.model_requests)
        world.fail_models = True; page.get_by_role('button',name='Refresh models',exact=True).click()
        expect(page.locator('#backgroundChain')).to_contain_text('selection is preserved')
        expect(select).to_have_value('alpha-new')
        choose(page, select, '__custom__')
        custom = page.get_by_label('Custom background model 1', exact=True); expect(custom).to_be_visible(); custom.fill('future/custom')
        world.fail_save = True; page.evaluate('saveCompletionProviders()')
        expect(page.locator('.cuttle-toast').last).to_contain_text('Save failed'); expect(custom).to_have_value('future/custom')
        world.fail_save = False; world.fail_models = False; page.evaluate('saveCompletionProviders()')
        expect(select).to_have_value('future/custom'); assert world.preferences['chain'][0]['model'] == 'future/custom'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#panel-agents').screenshot(path='temp/background-helpers-phone.png')
        page.set_viewport_size({'width':1280,'height':900}); assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally: page.close()


def test_cli_auth_effort_fallback_and_task_override(browser, static_server):
    world = ProviderWorld(); page = open_settings(browser, static_server, world)
    try:
        show_helpers(page)
        choose(page, page.locator('select[aria-label="Background executor 1"]'), 'cli:beta')
        choose(page, page.locator('select[aria-label="Background model 1"]'), 'beta-new')
        expect(page.get_by_label('Background temperature 1',exact=True)).to_be_disabled()
        auth = page.locator('#backgroundChain label').filter(has_text='Authentication').locator('select'); expect(auth).to_have_value('subscription')
        choose(page, page.locator('#backgroundChain label').filter(has_text='Reasoning effort').locator('select'), 'low')
        page.locator('#backgroundAdd').click()
        expect(page.locator('select[aria-label="Background executor 2"]').locator('..')).to_be_visible()
        choose(page, page.locator('#backgroundTask'), 'titles')
        expect(page.locator('#backgroundOverride')).not_to_be_checked()
        page.locator('#backgroundOverride').check()
        choose(page, page.locator('select[aria-label="Background executor 1"]'), 'cli:beta')
        expect(page.locator('#backgroundChain')).to_contain_text('CLI model catalog')
        page.evaluate('saveCompletionProviders()')
        expect(page.locator('.cuttle-toast').last).to_contain_text('settings saved')
        assert len(world.preferences['chain']) == 2
        assert world.preferences['chain'][0]['kind'] == 'cli'
        assert world.preferences['tasks']['titles'][0]['auth'] == 'subscription'
        page.locator('#backgroundOverride').uncheck(); page.evaluate('saveCompletionProviders()')
        expect(page.locator('#backgroundOverride')).not_to_be_checked()
        assert 'titles' not in world.preferences['tasks']
    finally: page.close()


def test_stale_catalog_cannot_overwrite_new_target(browser, static_server):
    world = ProviderWorld(); world.delay = True; page = open_settings(browser, static_server, world)
    try:
        show_helpers(page)
        choose(page, page.locator('select[aria-label="Background executor 1"]'), 'cli:beta')
        expect(page.locator('select[aria-label="Background model 1"]')).to_contain_text('beta-new')
        expect(page.locator('#backgroundChain')).to_contain_text('CLI model catalog')
        assert world.pending is not None
        world.pending.fulfill(content_type='application/json',body=json.dumps(world.result('alpha')))
        expect(page.locator('select[aria-label="Background model 1"]')).not_to_contain_text('alpha-new')
    finally: page.close()
