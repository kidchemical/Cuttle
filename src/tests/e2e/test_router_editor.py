"""Real Router UI in Chromium; isolated fake API, no hosting runtime or vendors."""
import copy
import json
from urllib.parse import urlparse

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard


class RouterAPI:
    def __init__(self):
        self.calls=[]
        self.fail_preview=False
        self.empty_flow=False
        self.config={'success':True,
            'config':{'provider':{'mode':'api','api_model':'jev-latest'},
                      'default_target':{'agent':'cursor','model':'auto'},
                      'escalation_target':{'agent':'claude','model':'sonnet'},'fallbacks':{'ordered':[]}},
            'classifier':{'fast_path':True,'fast_path_confidence':.75},
            'budget':{'enabled':False}, 'use_cases':[
                {'id':f'lane-{i}','name':name,'enabled':True,'priority':i*10,'description':'Lane description',
                 'criteria':{'task_types':[kind],'difficulties':[],'keywords':[],'code_changes':None},
                 'routing':{'targets':[{'agent':'claude','model':'sonnet','effort':'high'}, {'agent':'cursor','model':'auto'}], 'never_use':[]}}
                for i,(name,kind) in enumerate([('Chat','basic_ask'),('Operations','ops'),('Writing','writing'),('Explain','explain'),('Research','research'),('Coding','coding'),('Architecture','architecture'),('Debugging','debugging'),('Everything else','other')])]}

    def handle(self, route):
        req=route.request; path=urlparse(req.url).path
        body=json.loads(req.post_data or '{}')
        self.calls.append((req.method,path,body))
        status=200; response={'success':True}
        if path=='/api/agent-router/options':
            response.update(agents=[{'id':'claude','label':'Claude'},{'id':'cursor','label':'Cursor'}],
                            agent_models={'claude':['sonnet','opus'],'cursor':['auto']},
                            agent_efforts={'claude':{'supported':True,'levels':['low','medium','high','max']},'cursor':{'supported':False}},
                            api_models=['jev-latest','gpt-4o-mini'])
        elif path=='/api/router/config':
            if req.method=='PUT':
                self.config['use_cases']=copy.deepcopy(body['use_cases'])
            response=copy.deepcopy(self.config)
        elif path=='/api/router/health': response.update(metrics=[], report=[], demotions={})
        elif path=='/api/router/preview':
            if self.fail_preview: status=503; response={'success':False,'error':'Brain offline'}
            else:
                response.update(executed=False,target={'agent':'claude','model':'sonnet','effort':'high'},
                    decision={'task_type':'writing','difficulty':'medium','confidence':.9,'reason':'Matched Writing'},
                    meta={'use_case':'lane-2','use_case_name':'Writing','provisional':not body['consult_brain']})
        elif path=='/api/router/decisions':
            response['decisions']=[] if self.empty_flow else [
                {'decision_id':'abc123','recorded_at':1791220000,'task_type':'coding','difficulty':'medium',
                 'target_agent':'claude','target_model':'sonnet','reasoning_effort':'high','attempt_index':1,
                 'success':True,'failure_kind':'none','source':'fallback','reason':'Literal <script>window.pwned=true</script>'}]
        else: raise AssertionError(f'Unexpected request: {path}')
        route.fulfill(status=status, content_type='application/json', body=json.dumps(response))


@pytest.fixture
def surface(browser, static_server):
    context=browser.new_context(viewport={'width':1200,'height':850})
    apply_request_guard(context, static_server)
    page=context.new_page(); errors=[]
    page.on('pageerror',lambda e: errors.append(str(e)))
    api=RouterAPI(); page.route('**/api/**',api.handle)
    page.goto(static_server+'/router_editor.html')
    page.locator('.re-lane-row').nth(8).wait_for()
    yield page,api,errors
    context.close()


def test_compact_overview_expand_effort_and_save(surface):
    page,api,errors=surface
    assert page.locator('.re-lane-detail:visible').count()==0
    assert page.locator('#usecase-card').bounding_box()['y']+page.locator('#usecase-card').bounding_box()['height'] < 850
    page.screenshot(path='temp/router-overview.png',full_page=True)
    page.get_by_role('button',name='Writing',exact=True).focus()
    page.keyboard.press('Enter')
    assert page.locator('.re-lane-detail:visible').count()==1
    detail=page.locator('#lane-detail-2')
    assert detail.locator('.effort').first.input_value()=='high'
    detail.locator('.effort').first.select_option('max')
    assert 'max' in page.locator('.re-lane-row').nth(2).inner_text()
    detail.locator('.re-uc-name').fill('Writing & docs')
    assert page.get_by_role('button',name='Writing & docs',exact=True).count()==1
    page.get_by_role('button',name='Coding',exact=True).click()
    assert page.locator('.re-lane-detail:visible').count()==1
    assert detail.is_hidden()
    page.locator('#btn-save').click()
    page.locator('#dirty-chip').wait_for(state='hidden')
    save=next(c for c in api.calls if c[0]=='PUT')
    assert save[2]['use_cases'][2]['routing']['targets'][0]['effort']=='max'
    assert not errors


def test_preview_live_brain_errors_flow_and_mobile(surface):
    page,api,errors=surface
    page.locator('#try-prompt').fill('Write release notes')
    page.get_by_text('Provisional · Ask brain',exact=False).wait_for()
    previews=[c for c in api.calls if c[1]=='/api/router/preview']
    assert previews[-1][2]['consult_brain'] is False
    assert 'claude / sonnet · high' in page.locator('#try-result').inner_text()
    page.locator('#btn-try-brain').click()
    page.wait_for_function("!document.querySelector('#btn-try-brain').disabled")
    assert 'Provisional' not in page.locator('#try-result').inner_text()
    assert [c for c in api.calls if c[1]=='/api/router/preview'][-1][2]['consult_brain'] is True
    assert 'fallback · completed' in page.locator('#decision-flow').inner_text()
    assert '2 attempts' in page.locator('#decision-flow').inner_text()
    assert page.evaluate('window.pwned') is None
    api.fail_preview=True
    page.locator('#try-prompt').fill('More prose')
    page.get_by_text('Preview unavailable: Brain offline').wait_for()
    page.locator('#try-prompt').fill('')
    assert page.locator('#btn-try-brain').is_disabled()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='temp/router-mobile.png',full_page=True)
    assert not errors


def test_flow_refresh_does_not_replace_draft(surface):
    page,api,errors=surface
    page.get_by_role('button',name='Writing',exact=True).click()
    page.locator('#lane-detail-2 .re-uc-name').fill('My unsaved lane')
    api.empty_flow=True
    # Exercise the page's visibility-resume poll without a 10-second wait.
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.get_by_text('No routed outcomes yet.',exact=False).wait_for()
    assert page.locator('#lane-detail-2 .re-uc-name').input_value()=='My unsaved lane'
    assert page.locator('#dirty-chip').is_visible()
    assert not errors


def test_stale_preview_cannot_overwrite_new_prompt(surface):
    page,api,errors=surface
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (url, options) => {
            if (url === '/api/router/preview' && JSON.parse(options.body).prompt === 'old prompt') {
                return new Promise(resolve => { window.releaseOldPreview = () => resolve(new Response(JSON.stringify({
                    success:true, target:{agent:'cursor',model:'auto'},
                    decision:{task_type:'coding',difficulty:'high',reason:'STALE RESULT'}, meta:{}
                }), {status:200, headers:{'Content-Type':'application/json'}})); });
            }
            return original(url, options);
        };
    }""")
    page.locator('#try-prompt').fill('old prompt')
    page.wait_for_function('typeof window.releaseOldPreview === "function"')
    page.locator('#try-prompt').fill('Write newer notes')
    page.get_by_text('Matched Writing',exact=False).wait_for()
    page.evaluate('window.releaseOldPreview()')
    assert 'STALE RESULT' not in page.locator('#try-result').inner_text()
    assert 'claude / sonnet' in page.locator('#try-result').inner_text()
    assert not errors
