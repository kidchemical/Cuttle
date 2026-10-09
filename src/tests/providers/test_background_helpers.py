"""Background selection and restricted CLI contracts; never invoke live vendors."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
import threading

import pytest

from api import background_helpers as bg
from api.agent_harness.helper import HelperCancelled, HelperResult, HelperRequest, helper_env
from api.model_providers import capabilities, catalog
from api.model_providers.types import TextRequest, TextResult


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('CUTTLE_HOME', str(tmp_path))
    stored = {}
    monkeypatch.setattr(bg, '_settings', lambda: NS(get_setting=lambda k: stored.get(k),
        set_setting=lambda k,v: (stored.__setitem__(k,v) or True)))
    monkeypatch.setattr(bg.providers, 'generate_text', lambda *a: pytest.fail('Unexpected external API execution'))
    monkeypatch.setattr(bg.catalog, 'get_agent', lambda *a: pytest.fail('Unexpected CLI execution'))
    return stored


def test_global_and_task_chain_inheritance(isolated):
    default = [{'kind':'api', 'target':'openai'}]
    local = [{'kind':'api', 'target':'local'}]
    isolated[bg.SETTINGS_KEY] = {'chain':default, 'tasks':{'progress':local}}
    assert bg.execution_chain('titles') == default
    assert bg.execution_chain('progress') == local
    assert bg.has_selection('narration')


def test_unconfigured_has_no_hidden_chain(isolated):
    """No third source: unset config yields no executor, not the provider order."""
    isolated[bg.SETTINGS_KEY] = {}
    assert bg.execution_chain('titles') == []
    assert not bg.has_selection('titles')


def test_api_fallback_and_task_temperature(monkeypatch, isolated):
    requests = []
    def fake(pid, request):
        requests.append((pid, request))
        return None if pid == 'openai' else TextResult('hello')
    monkeypatch.setattr(bg.providers, 'generate_text', fake)
    chain = [{'kind':'api','target':'openai','model':'a','temperature':.7}, {'kind':'api','target':'local','model':'b'}]
    assert bg.generate('titles', TextRequest('title', temperature=.3), chain=chain).text == 'hello'
    assert requests[0][1].temperature == .7 and requests[1][1].temperature == .3
    assert requests[1][1].timeout <= requests[0][1].timeout


def test_cli_result_and_explicit_exclusive_chain(monkeypatch):
    calls = []
    adapter = NS(generate_helper=lambda r: (calls.append(r) or HelperResult('A title', 'cli-model', {'input_tokens': 20})), helper_capabilities=lambda m: {'reasoning_efforts':['low']})
    monkeypatch.setattr(bg.catalog, 'get_agent', lambda _: (NS(background_text={'tools':False}), adapter))
    result = bg.generate('titles', TextRequest('title', max_tokens=40), chain=[{'kind':'cli','target':'claude','model':'haiku','auth':'subscription','effort':'low'}])
    assert result.text == 'A title'
    assert result.usage['billing_source'] == 'subscription'
    assert calls[0].model == 'haiku' and calls[0].effort == 'low'
    assert calls[0].max_tokens == 40


def test_cancellation_is_terminal(monkeypatch):
    event = threading.Event(); event.set()
    with pytest.raises(HelperCancelled):
        bg.generate('progress', TextRequest('hello'), chain=[{'kind':'api','target':'openai'}], cancel_event=event)


def test_bad_json_falls_back(monkeypatch):
    monkeypatch.setattr(bg.providers, 'cached_capabilities', lambda *a: {'json_object':True})
    monkeypatch.setattr(bg.providers, 'generate_text', lambda pid,r: TextResult('[]' if pid == 'openai' else '{"ok":true}'))
    chain = [{'kind':'api','target':'openai'}, {'kind':'api','target':'local'}]
    assert bg.generate('router', TextRequest('json', json_object=True), chain=chain).text == '{"ok":true}'


def test_temperature_ranges_and_thinking_conditions():
    anthropic = catalog.get_provider('anthropic')
    caps = capabilities.resolve(anthropic, 'claude-opus-4-6-20260101', live={'capabilities': {'thinking_types':{'disabled':True,'adaptive':True}, 'reasoning_efforts':['low','xhigh']}})
    assert capabilities.temperature_control(caps, 'low')['max'] == 1
    assert not capabilities.temperature_control(caps, 'xhigh')['supported']
    caps = capabilities.resolve(catalog.get_provider('openai'), 'gpt-5.1')
    assert capabilities.temperature_control(caps, 'none')['supported']
    assert not capabilities.temperature_control(caps, 'high')['supported']
    assert not capabilities.resolve(anthropic, 'claude-haiku-5-5')['temperature_control']['supported']


def test_validation_rejects_cli_temperature_invalid_effort_and_duplicate(monkeypatch):
    monkeypatch.setattr(bg, 'targets', lambda: [{'key':'cli:fake','auth_modes':['subscription']}])
    monkeypatch.setattr(bg.catalog, 'get_agent', lambda _: (None, NS(helper_capabilities=lambda m: {'reasoning_efforts':['low']})))
    target = {'kind':'cli','target':'fake','auth':'subscription'}
    with pytest.raises(ValueError, match='temperature'): bg.validate_target({**target, 'temperature': .5})
    with pytest.raises(ValueError, match='effort'): bg.validate_target({**target, 'effort':'ultra'})
    with pytest.raises(ValueError, match='Duplicate'): bg.validate({'chain':[target,target]})
    assert bg.validate({'chain':[target]})['chain'][0]['auth'] == 'subscription'


def test_helper_environment_drops_host_auth_without_mutating(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','dead-key'); monkeypatch.setenv('CODEX_API_KEY','dead-key')
    monkeypatch.setenv('CLAUDECODE','1'); monkeypatch.setenv('CLAUDE_CODE_USE_BEDROCK','1')
    env = helper_env()
    assert not {'ANTHROPIC_API_KEY','CODEX_API_KEY','CLAUDECODE','CLAUDE_CODE_USE_BEDROCK'} & env.keys()
    import os
    assert os.environ['ANTHROPIC_API_KEY'] == 'dead-key'


def test_claude_helper_has_no_tools_resume_or_bare(monkeypatch):
    from api.agent_harness.agents.claude import helper
    captured = {}
    @contextmanager
    def fake(args, request, env):
        if 'auth' in args:
            yield NS(next=lambda: {'loggedIn':True,'authMethod':'claude.ai'}), '/isolated'
            return
        captured.update(args=args, env=env)
        yield NS(proc=NS(stdin=NS(write=lambda b: None, close=lambda: None)), next=lambda: {'type':'result','subtype':'success','result':'Title'}), '/isolated'
    monkeypatch.setattr(helper,'helper_process',fake)
    monkeypatch.setattr(helper,'claude_executable',lambda:'/fake/claude')
    assert helper.generate(HelperRequest('title')).text == 'Title'
    args = captured['args']
    assert args[args.index('--tools')+1] == ''
    assert '--no-session-persistence' in args and '--strict-mcp-config' in args
    assert '--bare' not in args and '--resume' not in args
    assert '--system-prompt-file' in args and 'title' not in args
    assert captured['env']['CLAUDE_CODE_SAFE_MODE'] == '1'


def test_codex_refuses_environment_access_before_inference(monkeypatch):
    from api.agent_harness.agents.codex import helper
    sent = []
    replies = iter([{'id':1,'result':{}}, {'id':2,'result':{'account':{'type':'chatgpt'}}}, {'id':3,'result':{'thread':{'id':'x','environments':[{'id':'real'}]}}}])
    @contextmanager
    def fake(*args):
        yield NS(send=sent.append, next=lambda:next(replies)), '/isolated'
    monkeypatch.setattr(helper,'helper_process',fake)
    monkeypatch.setattr(helper,'codex_executable',lambda:'/fake/codex')
    monkeypatch.setattr(helper,'config',lambda auth:{})
    with pytest.raises(RuntimeError,match='environment-free'):
        helper.generate(HelperRequest('title'))
    assert not any(r.get('method') == 'turn/start' for r in sent)
    thread = next(r['params'] for r in sent if r.get('method') == 'thread/start')
    assert thread['environments'] == [] and thread['ephemeral'] is True


def test_codex_subscription_refuses_api_login(monkeypatch):
    from api.agent_harness.agents.codex import helper
    replies = iter([{'id':1,'result':{}},{'id':2,'result':{'account':{'type':'apiKey'}}}])
    @contextmanager
    def fake(*args): yield NS(send=lambda _:None,next=lambda:next(replies)), '/isolated'
    monkeypatch.setattr(helper,'helper_process',fake)
    monkeypatch.setattr(helper,'codex_executable',lambda:'/fake/codex')
    monkeypatch.setattr(helper,'config',lambda auth:{})
    with pytest.raises(RuntimeError,match='ChatGPT login'): helper.generate(HelperRequest('title'))


def test_claude_subscription_refuses_native_api_auth(monkeypatch):
    from api.agent_harness.agents.claude import helper
    sent = []
    @contextmanager
    def fake(args, request, env):
        sent.append(args)
        yield NS(next=lambda: {'loggedIn':True,'authMethod':'api_key'}), '/isolated'
    monkeypatch.setattr(helper,'helper_process',fake)
    monkeypatch.setattr(helper,'claude_executable',lambda:'/fake/claude')
    with pytest.raises(RuntimeError,match='subscription login'): helper.generate(HelperRequest('title'))
    assert len(sent) == 1 and '-p' not in sent[0]


def test_router_override_precedes_jev_and_local_but_off_stays_off(monkeypatch, isolated):
    from api.agent_router.engine import _provider_for
    from api.agent_router.types import RouterConfig
    isolated[bg.SETTINGS_KEY] = {'chain':[{'kind':'cli','target':'fake'}]}
    config = RouterConfig()
    config.provider.api_provider = 'jev'
    assert _provider_for(config).name == 'background'
    config.provider.mode = 'local'
    assert _provider_for(config).name == 'background'
    config.provider.mode = 'off'
    assert _provider_for(config) is None


def test_anthropic_json_text_is_validated_without_json_mode(monkeypatch):
    captured = []
    monkeypatch.setattr(bg.providers,'cached_capabilities',lambda *a: {'json_object':False})
    monkeypatch.setattr(bg.providers,'generate_text',lambda pid,r: (captured.append(r) or TextResult('{"ok":true}')))
    result = bg.generate('router', TextRequest('choose',json_object=True), chain=[{'kind':'api','target':'anthropic','model':'fake'}])
    assert result.text == '{"ok":true}'
    assert captured[0].json_object is False and 'JSON object' in captured[0].system


def test_task_callers_use_configured_cli_without_api_hops(monkeypatch, isolated):
    from api import chat_titler, commit_message_suggester, prompt_enhancer, chat_tts
    from api.voice_narrator import _complete
    isolated[bg.SETTINGS_KEY] = {'chain':[{'kind':'cli','target':'fake','auth':'subscription'}]}
    calls = []
    def execute(request):
        calls.append(request)
        return HelperResult('Improve background helper routing', 'fake-model', {'input_tokens':20})
    adapter = NS(generate_helper=execute, helper_capabilities=lambda model: {})
    monkeypatch.setattr(bg.catalog, 'get_agent', lambda _: (NS(background_text={'tools':False}), adapter))
    assert chat_titler._generate_title([{'role':'user','content':'Fix background helper routing'}])
    assert commit_message_suggester.suggest_commit_message({'files':[{'path':'helper.py','status':'modified'}]})['source'] == 'background'
    assert prompt_enhancer.enhance_prompt('Fix routing')['source'] == 'background'
    spoken, usage = chat_tts.summarize_for_speech_with_usage('Long reply text',target_chars=200)
    assert spoken and usage['execution'] == 'cli' and 'estimated_cost_usd' not in usage
    assert _complete('progress','Report progress')
    assert len(calls) == 5


def test_cancel_during_api_request_discards_result_and_stops_fallback(monkeypatch):
    event = threading.Event()
    def fake(pid, request):
        assert pid == 'openai'; event.set(); return TextResult('cancelled result')
    monkeypatch.setattr(bg.providers,'generate_text',fake)
    with pytest.raises(HelperCancelled):
        bg.generate('titles',TextRequest('title'),chain=[{'kind':'api','target':'openai'},{'kind':'api','target':'local'}],cancel_event=event)


def test_helper_lifecycle_stages_system_prompt_and_unregisters(monkeypatch):
    import io
    from pathlib import Path
    from api.agent_harness import helper
    from api.active_executions import get_executing_jobs
    captured, killed = {}, []
    proc = NS(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"type":"result","result":"title"}\n'),
              poll=lambda:None, wait=lambda **kwargs:None, kill=lambda:None)
    def spawn(args, **kwargs): captured.update(args=args, **kwargs); return proc
    monkeypatch.setattr(helper.subprocess,'Popen',spawn)
    monkeypatch.setattr('api.chat_run_registry.kill_process_tree',lambda p:killed.append(p))
    request = HelperRequest('title',system='Multi-line\n' + 'context '*2000)
    with helper.helper_process(['/fake/claude','--system-prompt-file','{helper_system_prompt}'],request) as (runner, cwd):
        prompt = Path(captured['args'][2])
        assert prompt.read_text().startswith(request.system)
        assert request.system not in captured['args']
        assert get_executing_jobs()
        assert runner.next()['result'] == 'title'
    assert not Path(cwd).exists() and not get_executing_jobs() and killed == [proc]


def test_settings_routes_require_owner_and_round_trip(monkeypatch, tmp_path, isolated):
    from api import auth_db as auth_db_module
    from api import web_chat_api as wca
    db = auth_db_module.AuthDatabase(tmp_path / 'background-auth.db')
    monkeypatch.setattr(auth_db_module,'_db_instance',db)
    monkeypatch.setattr(auth_db_module,'get_auth_db',lambda:db)
    monkeypatch.setattr(wca,'get_auth_db',lambda:db)
    monkeypatch.setenv('OWNER_USER_EMAIL','owner@local')
    owner = db.create_user('owner@local','Owner','local',password='x')
    monkeypatch.setattr(bg,'targets',lambda:[{'key':'api:openai','auth_modes':[]}])
    monkeypatch.setattr(bg,'discover',lambda *args:{'models':[],'source':'fake'})
    anon = wca.app.test_client()
    assert anon.get('/api/settings/background-helpers').status_code == 401
    assert anon.post('/api/settings/background-helpers',json={'chain':[]}).status_code == 401
    guest = wca.app.test_client()
    assert guest.post('/api/auth/guest',json={}).status_code == 200
    assert guest.post('/api/settings/background-helpers',json={'chain':[]}).status_code == 403
    assert guest.get('/api/settings/background-helpers/cli/claude/models').status_code == 403
    client = wca.app.test_client(); client.set_cookie('session_token',db.create_auth_session(owner))
    body = {'chain':[{'kind':'api','target':'openai','model':'gpt-4o-mini','temperature':.5}], 'tasks':{}}
    saved = client.post('/api/settings/background-helpers',json=body)
    assert saved.status_code == 200, saved.get_json()
    assert client.get('/api/settings/background-helpers').get_json()['preferences']['chain'][0]['temperature'] == .5
    assert client.get('/api/settings/background-helpers/api/openai/models').status_code == 200
    assert client.post('/api/settings/background-helpers',json={'chain':body['chain'],'tasks':{'bad-task':body['chain']}}).status_code == 400
