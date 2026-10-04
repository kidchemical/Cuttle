"""Channel admission must precede every /api/chat execution lane."""
from types import SimpleNamespace

import pytest

from api.pairing_manager import PairingManager


@pytest.fixture
def channel(tmp_path, monkeypatch, owner_session):
    from api import web_chat_api as wca

    manager = PairingManager(tmp_path / 'pairing.json')
    config = {'dmPolicy': 'pairing', 'allowFrom': []}
    monkeypatch.setattr(wca, 'PAIRING_AVAILABLE', True)
    monkeypatch.setattr(wca, 'get_pairing_manager', lambda: manager)
    monkeypatch.setattr(wca, 'get_settings_manager', lambda: SimpleNamespace(get_channel_config=lambda _: config))
    # The first post-admission seam is a tripwire: no execution, vision, or
    # persistence may be reached by a blocked user, including early control lanes.
    def unexpected(*args, **kwargs):
        pytest.fail('blocked request reached restart/control dispatch')
    monkeypatch.setattr('api.flask_restart.parse_restart_slash', unexpected)
    client = owner_session.sign_in(wca.app.test_client())
    return client, manager, config, owner_session


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('message', [
    '/cursor execute', '/codex execute', '/router execute', 'plain router prompt',
    '/cmd shell-test', '/coordinate execute', '/restart status',
])
def test_pairing_blocks_all_lanes_before_dispatch(channel, message, stream):
    client, manager, _, owner = channel
    response = client.post('/api/chat', json={'message': message, 'stream': stream})
    assert response.status_code == 403
    body = response.get_json()
    assert body['error'] == 'pairing_required'
    assert body['pairing_code']
    assert manager.list_pending()[0]['identity'] == f'web_user_{owner.user_id}'
    assert owner.db.get_user_chat_sessions(owner.user_id) == []


def test_empty_open_allowlist_is_not_wildcard(channel):
    client, manager, config, _ = channel
    config['dmPolicy'] = 'open'
    response = client.post('/api/chat', json={'message': '/cursor execute'})
    assert response.status_code == 403
    assert response.get_json()['error'] == 'allowlist_denied'
    assert manager.list_pending() == []


def test_pairing_errors_fail_closed(channel, monkeypatch):
    client, manager, _, _ = channel
    def broken(*args, **kwargs):
        raise OSError('private store unavailable')
    monkeypatch.setattr(manager, 'check_access', broken)
    response = client.post('/api/chat', json={'message': '/codex execute'})
    assert response.status_code == 503
    assert response.get_json()['error'] == 'pairing_unavailable'


def test_missing_pairing_module_fails_closed(channel, monkeypatch):
    from api import web_chat_api as wca
    client, _, _, _ = channel
    monkeypatch.setattr(wca, 'PAIRING_AVAILABLE', False)
    assert client.post('/api/chat', json={'message': 'execute'}).status_code == 503


def test_approval_reaches_normal_dispatch(channel, monkeypatch):
    from api import web_chat_api as wca
    client, manager, _, owner = channel
    first = client.post('/api/chat', json={'message': '/pipelines'})
    ok, identity = manager.approve(first.get_json()['pairing_code'])
    assert ok and identity == f'webchat:web_user_{owner.user_id}'
    # The retired-pipeline informational lane gives a terminal, provider-free
    # response after admission. Avoid host session/project settings entirely.
    monkeypatch.setattr('api.flask_restart.parse_restart_slash', lambda _: None)
    monkeypatch.setattr(wca, '_resolve_auth_chat_session', lambda _: ({'id': owner.user_id}, None, False))
    monkeypatch.setattr('api.starred_slash.apply_default_sticky_prefix', lambda message, *a, **kw: message)
    response = client.post('/api/chat', json={'message': '/pipelines'})
    assert response.status_code == 200
    assert response.get_json()['type'] == 'pipelines_removed'
