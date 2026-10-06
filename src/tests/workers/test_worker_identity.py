"""A device token proves *which* worker is calling, not just "some worker"."""

from __future__ import annotations

import pytest
from flask import Flask

LAN = {"REMOTE_ADDR": "192.168.1.50"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(tmp_path / "device_workers.db"))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    import api.device_workers.store as store_mod

    store_mod._store = None
    store = store_mod.get_store()
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: store)
    monkeypatch.setattr("api.device_workers.store.get_store", lambda: store)
    monkeypatch.setattr("api.device_workers.auth.lan_access_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.auth.worker_token", lambda: "")

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    yield app.test_client()
    store_mod._store = None


def _enroll(client, wid, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/workers/enroll", json={"worker_id": wid}, headers=headers,
                       environ_base=LAN)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_lan_caller_cannot_obtain_an_enrolled_workers_token(client):
    first = _enroll(client, "worker-a")
    assert first.status_code == 200
    again = _enroll(client, "worker-a")
    assert again.status_code == 409
    assert first.get_json()["token"] not in again.get_data(as_text=True)


def test_worker_reenrolls_itself_with_its_own_token(client):
    token = _enroll(client, "worker-a").get_json()["token"]
    again = _enroll(client, "worker-a", token=token)
    assert again.status_code == 200
    assert again.get_json()["token"] == token


def test_token_cannot_enroll_or_claim_as_another_worker(client):
    a = _enroll(client, "worker-a").get_json()["token"]
    _enroll(client, "worker-b")
    assert _enroll(client, "worker-b", token=a).status_code == 403
    claim = client.post("/api/workers/jobs/claim", json={"worker_id": "worker-b"},
                        headers=_bearer(a), environ_base=LAN)
    assert claim.status_code == 403
    for path in ("/api/workers/register", "/api/workers/jobs/j1/complete",
                 "/api/workers/jobs/j1/fail", "/api/workers/jobs/j1/heartbeat"):
        res = client.post(path, json={"worker_id": "worker-b"}, headers=_bearer(a),
                          environ_base=LAN)
        assert res.status_code == 403, path


def test_bound_token_supplies_worker_id_when_omitted(client):
    a = _enroll(client, "worker-a").get_json()["token"]
    claim = client.post("/api/workers/jobs/claim", json={}, headers=_bearer(a),
                        environ_base=LAN)
    assert claim.status_code == 200
    assert claim.get_json()["jobs"] == []


def test_loopback_host_may_rotate_an_enrolled_worker(client):
    _enroll(client, "worker-a")
    loop = client.post("/api/workers/enroll", json={"worker_id": "worker-a", "rotate": True},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert loop.status_code == 200


def test_stale_saved_token_still_enrolls_a_new_worker(client):
    res = _enroll(client, "worker-c", token="token-from-a-reset-host")
    assert res.status_code == 200
    assert res.get_json()["token"] != "token-from-a-reset-host"
