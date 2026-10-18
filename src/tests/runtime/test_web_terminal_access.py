"""Tests for localhost-only web terminal access checks and PTY resume registry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from api import web_terminal
from api.web_terminal import (
    _attach_or_create_session,
    _destroy_session,
    _is_local_request,
    _registry_get,
    register_terminal_routes,
)


@pytest.fixture(autouse=True)
def _clear_pty_registry():
    with web_terminal._PTY_LOCK:
        web_terminal._PTY_REGISTRY.clear()
    yield
    with web_terminal._PTY_LOCK:
        for sess in list(web_terminal._PTY_REGISTRY.values()):
            try:
                sess.close()
            except Exception:
                pass
        web_terminal._PTY_REGISTRY.clear()


@pytest.fixture
def terminal_app():
    app = Flask(__name__)
    register_terminal_routes(app)
    return app


def test_is_local_request_loopback():
    app = Flask(__name__)
    with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '127.0.0.1'}):
        assert _is_local_request() is True
    with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '::1'}):
        assert _is_local_request() is True
    with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '::ffff:127.0.0.1'}):
        assert _is_local_request() is True


def test_is_local_request_same_pc_lan_ip():
    app = Flask(__name__)
    with patch('api.lan_access.get_lan_ipv4', return_value='192.0.2.42'):
        with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '192.0.2.42'}):
            assert _is_local_request() is True


def test_is_local_request_remote_lan_client():
    app = Flask(__name__)
    with patch('api.lan_access.get_lan_ipv4', return_value='192.0.2.42'):
        with app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '192.0.2.99'}):
            assert _is_local_request() is False


def test_terminal_status_local(terminal_app):
    with terminal_app.test_client() as client:
        resp = client.get('/api/terminal/status', environ_overrides={'REMOTE_ADDR': '127.0.0.1'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['available'] is True


def test_terminal_status_remote(terminal_app):
    with terminal_app.test_client() as client:
        resp = client.get('/api/terminal/status', environ_overrides={'REMOTE_ADDR': '192.0.2.99'})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data['success'] is False
        assert 'localhost_url' in data


def test_attach_or_create_resumes_same_shell():
    import time

    fake = MagicMock()
    fake.is_alive.return_value = True
    fake.shell_id = 'powershell'
    fake.session_id = 'CH-TEST01'
    fake.last_active = time.time()
    fake.take_scrollback_b64.return_value = 'YWJj'  # base64 'abc'

    with web_terminal._PTY_LOCK:
        web_terminal._PTY_REGISTRY['CH-TEST01'] = fake

    on_out = MagicMock()
    on_exit = MagicMock()
    sess, resumed, err = _attach_or_create_session(
        'CH-TEST01', 'powershell', 80, 24, on_out, on_exit,
    )
    assert err is None
    assert resumed is True
    assert sess is fake
    fake.resize.assert_called_once_with(80, 24)
    fake.set_callbacks.assert_called_once_with(on_out, on_exit)
    fake.touch.assert_called()


def test_attach_or_create_replaces_on_shell_change():
    import time

    old = MagicMock()
    old.is_alive.return_value = True
    old.shell_id = 'powershell'
    old.session_id = 'CH-TEST02'
    old.last_active = time.time()

    with web_terminal._PTY_LOCK:
        web_terminal._PTY_REGISTRY['CH-TEST02'] = old

    created = MagicMock()
    created.session_id = 'CH-TEST02'
    created.shell_id = 'cmd'
    created.start.return_value = None

    with patch.object(web_terminal, '_PtySession', return_value=created) as ctor:
        on_out = MagicMock()
        on_exit = MagicMock()
        sess, resumed, err = _attach_or_create_session(
            'CH-TEST02', 'cmd', 100, 30, on_out, on_exit,
        )
        assert err is None
        assert resumed is False
        assert sess is created
        ctor.assert_called_once()
        old.detach.assert_called()
        old.close.assert_called()
        assert _registry_get('CH-TEST02') is created


def test_destroy_session_removes_registry_entry():
    sess = MagicMock()
    with web_terminal._PTY_LOCK:
        web_terminal._PTY_REGISTRY['CH-GONE'] = sess
    _destroy_session('CH-GONE')
    assert _registry_get('CH-GONE') is None
    sess.detach.assert_called_once()
    sess.close.assert_called_once()


def test_project_cwd_only_for_registered_projects(tmp_path, monkeypatch):
    """IDE terminals start in a registered project folder; anything else is refused."""
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    class _PM:
        def get_projects(self):
            return [{"id": 1, "name": "P", "path": str(proj)}]

        def get_current_project(self):
            return None

    monkeypatch.setattr("managers.project_manager.project_manager", _PM())
    assert web_terminal.resolve_project_cwd(str(proj)) == str(proj.resolve())
    assert web_terminal.resolve_project_cwd(str(other)) is None
    assert web_terminal.resolve_project_cwd("") is None
    argv, cwd, _env = web_terminal._resolve_shell_preset('bash', str(proj))
    assert cwd == str(proj)
    assert web_terminal._resolve_shell_preset('bash')[1] == str(web_terminal._REPO_ROOT)


def test_attach_or_create_passes_cwd_to_new_session():
    created = MagicMock()
    created.start.return_value = None
    with patch.object(web_terminal, '_PtySession', return_value=created) as ctor:
        sess, resumed, err = _attach_or_create_session(
            'ide-1', 'bash', 80, 24, MagicMock(), MagicMock(), '/projects/demo',
        )
    assert err is None and resumed is False and sess is created
    assert ctor.call_args.args == ('ide-1', 'bash', 80, 24, '/projects/demo')
