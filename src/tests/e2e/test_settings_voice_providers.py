"""Production Voice settings, fake account catalogs, no vendor/Flask calls."""
import json
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import expect
from .control_helpers import choose
from .test_settings_agents import AgentWorld, open_settings
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class VoiceWorld(AgentWorld):
    def __init__(self):
        super().__init__()
        self.conf={'enabled':True,'summarize':True,'provider':'openai','models':{'openai':'tts-old'},
                   'voices':{'openai':'alloy'},'speed':1,'stability':.5,'summarize_provider':'anthropic',
                   'summarize_models':{'anthropic':'haiku-new'},'summarize_efforts':{'anthropic':{'haiku-new':'high'}}}
        self.requests=[]; self.pending=None; self.delay=False; self.fail_models=False

    def models(self,pid,op):
        mids=['haiku-new','haiku-budget'] if pid=='anthropic' else ['gpt-text'] if op=='text_generation' else ['tts-new','tts-old'] if pid=='openai' else ['speech-new']
        return {'success':True,'source':'provider_api','models':[{'id':mid,'label':mid,'compatibility':'supported',
                'availability':'available','capabilities':{'reasoning_efforts':['low','high'] if mid=='haiku-new' else []}} for mid in mids] +
                [{'id':'wrong-operation','compatibility':'unsupported'}]}

    def handle(self,route):
        parsed=urlparse(route.request.url); path=parsed.path; query=parse_qs(parsed.query)
        if path=='/api/tts/providers':
            data={'success':True,'providers':[{'id':pid,'label':pid.title(),'configured':True,'default_model':'tts-new' if pid=='openai' else 'speech-new',
                    'models':[{'id':'tts-new' if pid=='openai' else 'speech-new'}],'voices':[{'id':'alloy' if pid=='openai' else 'custom-voice'}],
                    'speed_range':[.25,4] if pid=='openai' else [.7,1.2],'supports_stability':pid=='elevenlabs','supports_fetch_voices':pid=='elevenlabs'} for pid in ('openai','elevenlabs')],
                  'summarize_providers':[{'id':pid,'label':pid.title(),'configured':True,'default_model':'haiku-new' if pid=='anthropic' else 'gpt-text',
                    'effective_model':'haiku-new' if pid=='anthropic' else 'gpt-text','suggested_models':['haiku-new'] if pid=='anthropic' else ['gpt-text']} for pid in ('openai','anthropic')]}
        elif path=='/api/settings/chat-tts':
            if route.request.method=='POST':
                self.saves.append(json.loads(route.request.post_data))
                if self.fail_save:
                    route.fulfill(status=500,content_type='application/json',body='{"success":false,"error":"Save failed"}'); return
                self.conf=self.saves[-1]
            data={'success':True,'chat_tts':self.conf}
        elif path.startswith('/api/settings/model-providers/'):
            pid=path.split('/')[-2]; op=query.get('operation',['text_generation'])[0]; self.requests.append(route.request.url)
            if self.delay and pid=='openai' and op=='speech_generation': self.pending=route; return
            if self.fail_models:
                route.fulfill(status=502,content_type='application/json',body='{"success":false,"error":"Catalog unavailable"}'); return
            data=self.models(pid,op)
        elif path=='/api/tts/voices':
            pid=query['provider'][0]
            data={'success':True,'source':'provider_api','voices':[{'id':'alloy','label':'Alloy'},{'id':'coral','label':'Coral'}] if pid=='openai' else [{'id':'custom-voice','label':'My voice'}]}
        else: return super().handle(route)
        route.fulfill(content_type='application/json',body=json.dumps(data))


def show_voice(page):
    expect(page.locator('.agent-card[data-agent-id="opencode"]')).to_be_visible()
    page.evaluate("CuttleSettingsTabs.setTab('voice')")
    page.locator('#voiceDetails').evaluate('(element) => element.open = true')


def test_voice_and_narrator_catalogs_efforts_custom_defaults_and_save(browser,static_server):
    world=VoiceWorld(); page=open_settings(browser,static_server,world,width=390); errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        show_voice(page)
        model=page.locator('#chatTtsModel'); narrator=page.locator('#chatTtsNarratorModel'); effort=page.locator('#chatTtsNarratorEffort')
        expect(model).to_have_value('tts-old'); expect(narrator).to_have_value('haiku-new'); expect(effort).to_have_value('high')
        expect(effort).to_be_enabled(); assert model.locator('option[value="wrong-operation"]').count()==0
        choose(page, narrator, 'haiku-budget'); expect(effort).to_be_disabled()
        choose(page, narrator, 'haiku-new'); expect(effort).to_have_value('high'); choose(page, effort, 'low')
        choose(page, model, 'tts-new'); choose(page, page.locator('#chatTtsVoice'), 'coral')
        choose(page, page.locator('#chatTtsProvider'), 'elevenlabs'); expect(model).to_contain_text('speech-new')
        expect(page.locator('#chatTtsStabilityRow')).to_be_visible()
        choose(page, page.locator('#chatTtsProvider'), 'openai'); expect(model).to_have_value('tts-new')
        expect(page.locator('#chatTtsVoice')).to_have_value('coral'); expect(page.locator('#chatTtsStabilityRow')).to_be_hidden()
        page.evaluate('refreshNarratorModels()'); assert any('refresh=1' in url for url in world.requests)
        expect(effort).to_have_value('low')
        world.fail_models=True; page.evaluate('refreshChatTtsModels()')
        expect(page.locator('#chatTtsModelStatus')).to_contain_text('selection is preserved'); expect(model).to_have_value('tts-new')
        choose(page, model, '__custom_model__'); page.locator('#chatTtsCustomModel').fill('future-tts')
        choose(page, narrator, '__custom_model__'); page.locator('#chatTtsNarratorCustomModel').fill('future-text')
        world.fail_save=True; page.evaluate('saveChatTtsSettings()')
        expect(page.locator('.cuttle-toast').last).to_contain_text('Save failed')
        expect(page.locator('#chatTtsCustomModel')).to_have_value('future-tts')
        world.fail_save=False; world.fail_models=False; page.evaluate('saveChatTtsSettings()')
        expect(model).to_have_value('future-tts'); assert world.conf['summarize_models']['anthropic']=='future-text'
        assert world.conf['summarize_efforts']['anthropic']['haiku-new']=='low'
        choose(page, model, ''); choose(page, narrator, ''); page.evaluate('saveChatTtsSettings()')
        expect(model).to_have_value(''); assert world.conf['models']['openai']=='' and world.conf['summarize_models']['anthropic']==''
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        for selector in ('#chatTtsModel', '#chatTtsNarratorModel'):
            assert 34 <= page.locator(selector).locator('..').locator('.cuttle-combo-trigger').bounding_box()['height'] <= 65
        page.locator('#panel-voice').screenshot(path='temp/voice-providers-phone.png')
        page.set_viewport_size({'width':1280,'height':900}); assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
    finally: page.close()


def test_slow_previous_speech_catalog_cannot_replace_new_provider(browser,static_server):
    world=VoiceWorld(); world.delay=True; page=open_settings(browser,static_server,world)
    try:
        show_voice(page); expect(page.locator('#chatTtsModelStatus')).to_contain_text('Loading')
        choose(page, page.locator('#chatTtsProvider'), 'elevenlabs')
        expect(page.locator('#chatTtsModel')).to_contain_text('speech-new')
        assert world.pending is not None
        world.pending.fulfill(content_type='application/json',body=json.dumps(world.models('openai','speech_generation')))
        assert page.locator('#chatTtsModel option[value="tts-old"]').count()==0
    finally: page.close()
