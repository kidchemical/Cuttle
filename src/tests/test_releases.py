"""Release discovery is isolated from network and live settings."""
import pytest
import requests
from flask import Flask

from api import releases


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(releases, '_cache', {})
    monkeypatch.setattr(releases, 'cuttle_version', lambda: '1.0.0')
    monkeypatch.setattr(releases, 'cuttle_git_rev', lambda: 'abc123')
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected network request')
    monkeypatch.setattr(releases.requests, 'get', forbidden)


def response(monkeypatch, data=None, status=200):
    calls = []
    class Response:
        status_code = status
        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError('HTTP failure')
        def json(self):
            return data
    monkeypatch.setattr(releases.requests, 'get', lambda *a, **k: calls.append((a, k)) or Response())
    return calls


@pytest.mark.parametrize('older,newer', [
    ('1.9.0', '1.10.0'), ('1.0.0-rc.2', '1.0.0-rc.10'),
    ('1.0.0-beta', '1.0.0'), ('1.0.0-alpha.1', '1.0.0-alpha.beta'),
    ('1.0.0-alpha', '1.0.0-alpha.1'),
])
def test_semver_precedence(older, newer):
    assert releases.version_key(older) < releases.version_key(newer)
    assert releases.version_key('v1.0.0+build.2') == releases.version_key('1.0.0')


@pytest.mark.parametrize('tag', ['banana', '1.2', '01.2.3', '1.2.3-01'])
def test_invalid_tags(tag):
    with pytest.raises(ValueError):
        releases.version_key(tag)


def test_cache_refresh_and_release_content(monkeypatch):
    calls = response(monkeypatch, {'tag_name': 'v1.2.0', 'body': '<script>bad()</script>',
                                   'html_url': 'javascript:bad()', 'name': 'New release'})
    result = releases.check_releases()
    assert result['state'] == 'available'
    assert result['release']['notes'] == '<script>bad()</script>'
    assert result['release']['url'] == releases.RELEASES_URL + '/tag/v1.2.0'
    assert result['git_rev'] == 'abc123'
    assert not result['stale']
    releases.check_releases()
    assert len(calls) == 1
    releases.check_releases(force=True)
    assert len(calls) == 2


def test_offline_preserves_notes_and_check_time(monkeypatch):
    response(monkeypatch, {'tag_name': 'v1.2.0', 'body': 'Changes'})
    first = releases.check_releases()
    def offline(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(releases.requests, 'get', offline)
    result = releases.check_releases(force=True)
    assert result['release'] == first['release']
    assert result['checked_at'] == first['checked_at']
    assert result['stale'] and result['error']
    assert releases.check_releases()['stale']


def test_no_releases_cached(monkeypatch):
    calls = response(monkeypatch, status=404)
    assert releases.check_releases()['state'] == 'no_release'
    releases.check_releases()
    assert len(calls) == 1


def test_malformed_and_unpublished_are_unknown(monkeypatch):
    for data in ({'tag_name': 'oops'}, {'tag_name': 'v2.0.0', 'draft': True}, []):
        response(monkeypatch, data)
        result = releases.check_releases(force=True)
        assert result['state'] == 'unknown'
        assert result['error']


@pytest.mark.parametrize('current,state', [('1.2.0', 'latest'), ('1.3.0', 'ahead'), ('', 'unknown')])
def test_current_version_states(monkeypatch, current, state):
    response(monkeypatch, {'tag_name': 'v1.2.0'})
    monkeypatch.setattr(releases, 'cuttle_version', lambda: current)
    assert releases.check_releases()['state'] == state


def test_authenticated_route(monkeypatch):
    from api.settings_routes import settings_bp
    app = Flask(__name__)
    app.register_blueprint(settings_bp)
    monkeypatch.setattr(releases, 'check_releases', lambda **kwargs: {'state': 'latest', **kwargs})
    client = app.test_client()
    assert client.get('/api/settings/releases', environ_base={'REMOTE_ADDR': '192.168.1.20'}).status_code == 401
    from api import http_authz
    monkeypatch.setattr(http_authz, 'current_user', lambda: {'id': 1, 'auth_provider': 'local'})
    res = client.get('/api/settings/releases?force=1')
    assert res.status_code == 200
    assert res.json['force'] is True
