"""Offline UI workflows for event detail and database policy controls."""
import json
from playwright.sync_api import expect
from .test_shared_diff_modal import browser,static_server  # noqa: F401


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
