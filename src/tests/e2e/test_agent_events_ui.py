"""Offline UI workflows for event detail and database policy controls."""
import json
import pytest
from playwright.sync_api import expect
from .test_shared_diff_modal import browser,static_server  # noqa: F401


@pytest.mark.parametrize('reduced_motion', ['no-preference', 'reduce'])
def test_live_feed_motion_handles_bursts_and_preserves_detail(browser,static_server,reduced_motion):
    page=browser.new_page(viewport={'width':1100,'height':900},reduced_motion=reduced_motion)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    row={'id':1,'query_id':'test','seq':1,'kind':'tool','ts':1,'rev':1,'summary':'First step','agent_id':'codex'}
    detail_requests=[]
    def handle(route):
        if '/events/1' in route.request.url:
            detail_requests.append(route.request.url)
            body={'detail':{'output':'Loaded detail'}}
        else:
            body={'events':[row],'next_cursor':1,'stream_cursor':1}
        route.fulfill(content_type='application/json',body=json.dumps(body))
    page.route('**/api/agent-events**',handle)
    page.add_init_script('''
        window.EventSource=class {
            constructor() { window.feedSource=this; }
            close() {}
        };
        window.feedAnimations=[];
        const animate=Element.prototype.animate;
        Element.prototype.animate=function(frames,options) {
            const animation=animate.call(this,frames,options);
            feedAnimations.push({id:this.dataset.id,frames,options,animation});
            return animation;
        };
    ''')
    try:
        page.goto(static_server+'/agent_feed.html')
        expect(page.locator('.feed-step')).to_have_count(1)
        page.wait_for_function('!!window.feedSource')
        assert page.evaluate('feedAnimations.length')==0  # History loads quietly.
        page.locator('.feed-step summary').click()
        expect(page.locator('.feed-step pre')).to_have_text('Loaded detail')
        page.evaluate('''() => {
            window.originalStep=document.querySelector('.feed-step');
            originalStep.querySelector('summary').focus();
            window.originalTop=originalStep.getBoundingClientRect().top;
        }''')
        incoming=[dict(row,id=i,seq=i,ts=i,summary=f'Step {i}') for i in range(2,8)]
        page.evaluate('''events => {
            for(const row of events) feedSource.onmessage({data:JSON.stringify({events:[row]})});
        }''',incoming)
        expect(page.locator('.feed-step')).to_have_count(7)
        assert page.evaluate("document.querySelector('[data-id=\"1\"]')===originalStep")
        assert page.evaluate("originalStep.open && document.activeElement===originalStep.querySelector('summary')")
        assert len(detail_requests)==1
        if reduced_motion=='reduce':
            assert page.evaluate('feedAnimations.length')==0
        else:
            assert page.evaluate("feedAnimations.some(a=>a.id==='1' && a.frames[0].transform!=='translateY(0)')")
            assert page.evaluate("feedAnimations.some(a=>a.id==='7' && a.frames[0].opacity===0)")
            assert page.evaluate('feedAnimations.every(a=>a.options.duration===250)')
        # Interrupt motion with a second burst; all effects must settle promptly.
        incoming=[dict(row,id=i,seq=i,ts=i) for i in range(8,38)]
        page.evaluate('events => feedSource.onmessage({data:JSON.stringify({events})})',incoming)
        expect(page.locator('.feed-step')).to_have_count(37)
        page.wait_for_function('document.getAnimations().length===0')
        assert page.locator('.feed-step').first.get_attribute('data-id')=='37'
        assert page.evaluate("originalStep.getBoundingClientRect().top>originalTop")
        assert page.evaluate("getComputedStyle(originalStep).transform==='none'")
        assert not errors
    finally:page.close()


def test_feed_and_full_detail(browser,static_server):
    page=browser.new_page(viewport={'width':1100,'height':800})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    row={'id':1,'query_id':'test','seq':1,'kind':'edit','ts':1,'rev':1,'summary':'Edit example.py','agent_id':'codex','model':'cheap','chat_session_id':'1066'}
    def handle(route):
        url=route.request.url
        if '/stream' in url:
            route.fulfill(content_type='text/event-stream',body=': offline test\n\n');return
        body={'events':[row],'next_cursor':1,'stream_cursor':1} if '/api/agent-events?' in url else {'detail':{'patch':'-before\n+after\n','source':'native'}}
        route.fulfill(content_type='application/json',body=json.dumps(body))
    page.route('**/api/agent-events**',handle)
    try:
        page.goto(static_server+'/agent_feed.html')
        expect(page.locator('.feed-step')).to_have_count(1)
        page.locator('.feed-step summary').click()
        expect(page.locator('.feed-step pre')).to_contain_text('+after')
        assert page.locator('.feed-step a').get_attribute('href').endswith('&seq=1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='temp/agent-feed-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':800})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='temp/agent-feed-phone.png',full_page=True)
        page.locator('.feed-advanced summary').click()
        expect(page.locator('[name=agent]')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('[name=agent]').fill('codex')
        page.get_by_role('button',name='Apply filters').click()
        expect(page.locator('#feedFilterCount')).to_have_text('· 1')
        expect(page.locator('[name=agent]')).to_be_hidden()
        assert not errors
    finally:page.close()


def test_database_controls(browser,static_server):
    page=browser.new_page(viewport={'width':900,'height':800})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    sent=[]
    policy={'retention_days':90,'thinking_full_days':14,'quota_mb':5000,'thinking_mode':'full_then_summary','starred_retention_days':-1}
    def handle(route):
        if route.request.method=='POST':
            sent.append(route.request.post_data_json)
            body={'success':True,'policy':sent[-1]}
        else:body={'stores':[{'id':'agent_events','label':'Agent events','bytes':123456,'events':42,'configurable':True,'reset':True,'policy':policy}]}
        route.fulfill(content_type='application/json',body=json.dumps(body))
    page.route('**/api/settings/storage**',handle)
    # Isolate the new component with production settings CSS and JS. Other
    # settings components are verified by their existing page suites.
    try:
        page.goto(static_server+'/agent_feed.html')
        page.set_content('<link rel="stylesheet" href="'+static_server+'/css/settings_page.css"><div id="storageStores"></div>')
        page.add_script_tag(url=static_server+'/js/settings/settings_storage.js')
        expect(page.get_by_role('heading',name='Agent events')).to_be_visible()
        page.locator('[name="quota_mb"]').fill('6000')
        page.get_by_role('button',name='Save policy').click()
        expect(page.get_by_role('status')).to_contain_text('Policy saved')
        assert sent[-1]['quota_mb']==6000
        expect(page.get_by_role('button',name='Reset agent events')).to_be_disabled()
        assert not errors
    finally:page.close()


def test_feed_experiment_controls_apps_and_saved_pin(browser,static_server):
    page=browser.new_page()
    enabled=False
    def handle(route):
        if '/api/experimental/flags' in route.request.url:
            body={'flags':[{'id':'agent_feed','enabled':enabled}],'kill_switch':False}
        elif '/api/settings/ui-layout' in route.request.url:
            body={'ui_layout':{'layout_version':8,'rail_items':['nav-chat','nav-apps','nav-agent-feed'],'rail_hidden':[]}}
        else:body={}
        route.fulfill(content_type='application/json',body=json.dumps(body))
    page.route('**/api/**',handle)
    try:
        page.goto(static_server+'/app_shell.html')
        page.wait_for_function('typeof getAppsList === "function"')
        assert not page.evaluate('getAppsList().some(app=>app.id==="nav-agent-feed")')
        expect(page.locator('[data-id="nav-agent-feed"]').first).to_be_hidden()
        enabled=True
        page.evaluate('refreshAgentFeedAvailability()')
        page.wait_for_function('getAppsList().some(app=>app.id==="nav-agent-feed")')
        expect(page.locator('.rail-items [data-id="nav-agent-feed"]').first).to_be_visible()
        enabled=False
        page.evaluate('refreshAgentFeedAvailability()')
        page.wait_for_function('!getAppsList().some(app=>app.id==="nav-agent-feed")')
        expect(page.locator('[data-id="nav-agent-feed"]').first).to_be_hidden()
        assert page.evaluate('lastUILayout.rail_items.includes("nav-agent-feed")')
    finally:page.close()
