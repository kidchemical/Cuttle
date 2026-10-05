"""Router preview contracts: classification shares selection, never runs a task."""
from pathlib import Path

import pytest
from flask import Flask

from api.agent_router import engine, outcomes
from api.agent_router.types import ExecutionTarget, RoutingDecision
from api.agent_router.routes import router_editor_bp
from api.agent_router.config import reset_router_config
from api.agent_router.use_cases import save_use_cases
from tests.test_http_authz import _auth_client, LAN


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager
    sm = SettingsManager(str(tmp_path / 'settings.json'))
    monkeypatch.setattr('managers.settings_manager.get_settings_manager', lambda: sm)
    reset_router_config()
    monkeypatch.setattr('api.agent_router.drift.active_demotions', lambda **kw: {})
    monkeypatch.setattr('api.agent_router.quota.is_exhausted', lambda target: False)
    monkeypatch.setattr('api.agent_router.budget.budget_settings', lambda: {'enabled': False})
    def no_brain(*args, **kw):
        raise AssertionError('Typing must not consult a provider')
    monkeypatch.setattr(engine, '_provider_for', no_brain)
    return sm


def test_local_preview_matches_real_lane_and_effort(settings):
    save_use_cases([{'id':'write','name':'Writing','enabled':True,'priority':1,
                     'criteria':{'task_types':['writing']},
                     'routing':{'targets':[{'agent':'claude','model':'sonnet','effort':'high'}]}}])
    decision, meta = engine.preview_decision(engine.build_context('Write release notes'))
    assert decision.target == ExecutionTarget('claude', 'sonnet', 'high')
    assert meta['use_case'] == 'write'
    assert not meta['provisional']
    uncertain, meta = engine.preview_decision(engine.build_context('make it pop more'))
    assert meta['provisional']


def test_preview_uses_production_availability_layers(settings, monkeypatch):
    save_use_cases([{'id':'chat','name':'Chat','priority':1,'criteria':{},
                     'routing':{'targets':[{'agent':'codex','model':'gpt-5'}, {'agent':'cursor','model':'auto'}]}}])
    monkeypatch.setattr('api.agent_router.quota.is_exhausted', lambda t: t.agent == 'codex')
    decision, meta = engine.preview_decision(engine.build_context('hello'))
    assert decision.target.agent == 'cursor'
    assert meta['availability_skipped']


def test_explicit_brain_uses_decision_only_path(settings, monkeypatch):
    chosen = RoutingDecision('preview', 'writing', 'low', ExecutionTarget('claude','sonnet','high'),
                             .9, 'writing', ExecutionTarget('cursor','auto'))
    calls=[]
    monkeypatch.setattr(engine, 'decide_with_outcome', lambda ctx, cfg: (calls.append(ctx) or chosen, {'provider':'fake'}))
    app=Flask(__name__); app.register_blueprint(router_editor_bp)
    # Invoke the view behind auth in an isolated request; auth is separately covered.
    with app.test_request_context('/api/router/preview', method='POST', json={'prompt':'write something','consult_brain':True}):
        data=app.view_functions['router_editor.preview'].__wrapped__().get_json()
    assert data['executed'] is False
    assert data['target']['effort'] == 'high'
    assert len(calls) == 1


@pytest.mark.parametrize('body', [[], {}, {'prompt':42}, {'prompt':' '}, {'prompt':'x'*12001}, {'prompt':'hi','consult_brain':'true'}])
def test_bad_preview_input(body):
    app=Flask(__name__); app.register_blueprint(router_editor_bp)
    with app.test_request_context('/api/router/preview', method='POST', json=body):
        response, status = app.view_functions['router_editor.preview'].__wrapped__()
    assert status == 400


def test_editor_endpoints_owner_only(tmp_path, monkeypatch):
    from api.web_chat_api import app
    paths=[('/api/router/preview','POST',{'prompt':'hello'}), ('/api/router/decisions','GET',None)]
    ctx=_auth_client(tmp_path, monkeypatch)
    for path, method, body in paths:
        assert app.test_client().open(path, method=method, json=body, environ_base=LAN).status_code == 401
        ctx['client'].set_cookie('session_token', ctx['other_token'])
        monkeypatch.setenv('OWNER_USER_EMAIL','owner@local')
        assert ctx['client'].open(path, method=method, json=body, environ_base=LAN).status_code == 403
    ctx['client'].set_cookie('session_token', ctx['token'])
    monkeypatch.setattr('api.agent_router.routes.recent_decisions', lambda: [])
    assert ctx['client'].get('/api/router/decisions', environ_base=LAN).get_json()['decisions'] == []


def test_recent_decisions_dedupes_and_excludes_manual(tmp_path):
    path=tmp_path / 'outcomes.db'
    target=ExecutionTarget('cursor','auto')
    for did, index, source in [('routed',0,'router'),('routed',1,'fallback'),('manual',0,'manual_override')]:
        decision=RoutingDecision(did,'coding','medium',target,.9,'pick',target)
        assert outcomes.record_attempt(decision=decision, attempt_index=index, target=target, source=source,
                                       failure_kind='none', reason='success', latency_ms=10, db_path=path)
    before=path.read_bytes()
    rows=outcomes.recent_decisions(db_path=path)
    assert [(r['decision_id'], r['attempt_index']) for r in rows] == [('routed',1)]
    assert path.read_bytes() == before
    missing=tmp_path/'missing.db'
    assert outcomes.recent_decisions(db_path=missing) == []
    assert not missing.exists()
