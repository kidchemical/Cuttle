import pytest

from api import chat_preferences as cp


@pytest.fixture
def settings(monkeypatch, tmp_path):
    from managers import settings_manager as sm
    private = sm.SettingsManager(str(tmp_path / 'settings.json'))
    monkeypatch.setattr(sm, '_settings_manager', private)
    return private


def test_shared_and_independent_agent_prompts_merge_and_clear(settings):
    assert cp.apply_agent_prompt('Request', 'codex') == 'Request'
    assert cp.save_prompts({'global': 'Be concise', 'agents': {'codex': 'Explain code'}})
    cp.save_prompts({'agents': {'claude': 'Use examples'}})
    cp.save_prompts({'global': 'Be direct'})
    prefs = cp.prompts()
    assert prefs == {'global': 'Be direct', 'agents': {'codex': 'Explain code', 'claude': 'Use examples'}}
    prompt = cp.apply_agent_prompt('Request', 'codex')
    assert 'Be direct' in prompt and 'Explain code' in prompt and 'Use examples' not in prompt
    assert prompt.endswith('Request')
    cp.save_prompts({'global': '', 'agents': {'codex': ''}})
    prompt = cp.apply_agent_prompt('Request', 'codex')
    assert '"shared": "", "agent": ""' in prompt  # explicit reset for resumed threads
    assert 'replaces' in prompt
    assert cp.prompts()['agents'] == {'claude': 'Use examples'}


@pytest.mark.parametrize('raw', [None, {}, {'global': 3}, {'global': 'x' * 32001},
    {'agents': []}, {'agents': {'missing-agent': 'x'}}, {'global': 'ok', 'extra': True}])
def test_invalid_prompts_do_not_partially_save(settings, raw):
    with pytest.raises(ValueError): cp.save_prompts(raw)
    assert cp.prompts() == {'global': '', 'agents': {}}


@pytest.mark.parametrize('raw', [None, {}, {'enabled': 1, 'prompt': 'x', 'mode': 'append'},
    {'enabled': True, 'prompt': '', 'mode': 'append'},
    {'enabled': True, 'prompt': 'x', 'mode': 'invalid'}])
def test_processor_validation(settings, raw):
    with pytest.raises(ValueError): cp.save_processor(raw)
    assert not cp.processor_preferences()['enabled']


def test_routes_roundtrip_prompts_and_processor(settings, monkeypatch, tmp_path):
    from tests.auth.test_http_authz import _auth_client, LAN
    ctx = _auth_client(tmp_path, monkeypatch)
    client = ctx['client']
    client.set_cookie('session_token', ctx['token'])
    for path, body in [
        ('chat-prompts', {'global': 'Be brief', 'agents': {'codex': 'Explain changes'}}),
        ('response-postprocessing', {'enabled': True, 'prompt': 'Summarize', 'mode': 'replace'}),
    ]:
        response = client.post('/api/settings/' + path, json=body, environ_base=LAN)
        assert response.status_code == 200, response.get_json()
        reread = client.get('/api/settings/' + path, environ_base=LAN).get_json()
        assert response.get_json() == reread
        bad = client.post('/api/settings/' + path, json={'unknown': 'x'}, environ_base=LAN)
        assert bad.status_code == 400
    assert cp.prompts()['global'] == 'Be brief'
    assert cp.processor_preferences()['mode'] == 'replace'


@pytest.mark.parametrize('enabled,kill_switch', [(False, False), (True, False), (True, True)])
def test_ui_catalog_uses_effective_flag_and_preserves_hidden_preferences(settings, monkeypatch, enabled, kill_switch):
    from api import background_helpers as bg
    from api.settings_routes import _response_processor_payload
    monkeypatch.setattr(bg, 'targets', lambda: [])
    monkeypatch.setattr(bg, 'api_description', lambda: {})
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0' if kill_switch else '1')
    settings.set_setting('experimental_flags', {'response_postprocessing': enabled})
    prefs = {'chain': [], 'tasks': {'postprocessing': [{'kind': 'api', 'target': 'local', 'model': 'saved-model'}]}}
    settings.set_setting('background_helpers', prefs)
    cp.save_processor({'enabled': True, 'prompt': 'Saved instructions', 'mode': 'replace'})
    effective = enabled and not kill_switch
    assert _response_processor_payload()['available'] is effective
    described = bg.describe()
    assert 'postprocessing' not in described['tasks']
    assert 'titles' in described['tasks']
    assert described['preferences'] == prefs
    assert cp.processor_preferences()['prompt'] == 'Saved instructions'


def test_processor_chain_saves_independently_and_rejects_invalid_executor(settings, monkeypatch):
    from api import background_helpers as bg
    monkeypatch.setattr(bg, 'targets', lambda: [{'key': 'api:local'}])
    monkeypatch.setattr(bg, 'resolve_model', lambda *a: 'chosen')
    monkeypatch.setattr(bg.providers, 'validate_effort', lambda *a: None)
    background = {'chain': [{'kind': 'api', 'target': 'other'}]}
    settings.set_setting('background_helpers', background)
    selection = {'kind': 'api', 'target': 'local', 'model': 'chosen', 'effort': 'high'}
    assert cp.save_processor({'enabled': True, 'prompt': 'TLDR', 'mode': 'append', 'chain': [selection]})
    assert cp.processor_preferences()['chain'][0]['effort'] == 'high'
    assert settings.get_setting('background_helpers') == background
    with pytest.raises(ValueError):
        cp.save_processor({'enabled': True, 'prompt': 'TLDR', 'mode': 'append', 'chain': [{'kind': 'api', 'target': 'unknown'}]})
    assert cp.processor_preferences()['chain'][0]['target'] == 'local'


def test_processor_model_discovery_is_owner_only_and_flag_gated(settings, monkeypatch, tmp_path):
    from api import background_helpers as bg
    from tests.auth.test_http_authz import _auth_client, LAN
    calls = []
    monkeypatch.delenv('CUTTLE_EXPERIMENTAL', raising=False)
    monkeypatch.setattr(bg, 'discover', lambda *a: calls.append(a) or {'models': [{'id': 'model'}]})
    ctx = _auth_client(tmp_path, monkeypatch)
    client = ctx['client']
    path = '/api/settings/response-postprocessing/cli/codex/models'
    assert client.get(path, environ_base=LAN).status_code == 401
    client.set_cookie('session_token', ctx['other_token'])
    assert client.get(path, environ_base=LAN).status_code == 403
    client.set_cookie('session_token', ctx['token'])
    assert client.get(path, environ_base=LAN).status_code == 403
    assert calls == []
    settings.set_setting('experimental_flags', {'response_postprocessing': True})
    assert client.get(path, environ_base=LAN).get_json()['models'] == [{'id': 'model'}]
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    assert client.get(path, environ_base=LAN).status_code == 403
    assert len(calls) == 1
