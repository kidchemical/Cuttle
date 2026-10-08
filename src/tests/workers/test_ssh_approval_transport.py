"""Remote approval transport carries identity through request and polling."""
from io import BytesIO
import json
import urllib.error

import pytest
from flask import Flask

from api.device_workers import ssh_approval as sa


@pytest.mark.parametrize('decision', ['once', 'session', 'deny'])
def test_enrolled_remote_worker_can_request_and_poll(monkeypatch, tmp_path, decision):
    from api.device_workers import routes, store, auth

    monkeypatch.setenv('CUTTLE_DEVICE_WORKERS_DB', str(tmp_path / 'workers.db'))
    monkeypatch.setattr(store, '_store', None)
    monkeypatch.setattr(auth, 'lan_access_enabled', lambda: True)
    app = Flask(__name__)
    app.register_blueprint(routes.workers_bp)
    client = app.test_client()
    remote = {'REMOTE_ADDR': '192.168.1.44'}
    token = store.get_store().enroll_device(worker_id='fixture-worker')['token']
    monkeypatch.setattr('api.device_workers.config.worker_token', lambda: token)
    monkeypatch.setenv('CUTTLE_DEVICE_WORKERS_COORDINATOR_URL', 'http://fixture-coordinator')
    monkeypatch.delenv('CUTTLE_SSH_APPROVAL_LOCAL', raising=False)
    monkeypatch.setattr(sa, '_pending', {})
    seen = []

    def urlopen(req, **kwargs):
        from urllib.parse import urlsplit
        seen.append((req.method, req.get_header('Authorization')))
        path = urlsplit(req.full_url).path
        response = client.open(path, method=req.method, data=req.data,
                               headers=dict(req.header_items()), environ_base=remote)
        if response.status_code != 200:
            raise urllib.error.HTTPError(req.full_url, response.status_code, 'denied', {}, None)
        body = response.get_json()
        if req.method == 'POST':
            sa.decide(body['request']['id'], decision)
        return BytesIO(json.dumps(body).encode())

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    assert sa.request_via_coordinator(worker_id='fixture-worker', command_preview='fixture') == decision
    assert seen == [('POST', f'Bearer {token}'), ('GET', f'Bearer {token}')]


def test_transport_auth_failure_denies_without_polling(monkeypatch):
    monkeypatch.setenv('CUTTLE_DEVICE_WORKERS_COORDINATOR_URL', 'http://fixture-coordinator')
    monkeypatch.delenv('CUTTLE_SSH_APPROVAL_LOCAL', raising=False)
    monkeypatch.setattr('api.device_workers.config.worker_token', lambda: 'invalid-fixture')
    seen = []

    def urlopen(req, **kwargs):
        seen.append(req.method)
        raise urllib.error.HTTPError(req.full_url, 401, 'unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    assert sa.request_via_coordinator(worker_id='fixture') == 'deny'
    assert seen == ['POST']
