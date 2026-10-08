"""Host-approved pairing: no token without owner approval, bound identity."""

from __future__ import annotations

import pytest
from flask import Flask

LAN = {"REMOTE_ADDR": "192.168.1.50"}
LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}
PUBLIC = {"REMOTE_ADDR": "8.8.8.8"}
SECRET = "ab" * 32
SECRET2 = "cd" * 32


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(tmp_path / "device_workers.db"))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    import api.device_workers.store as store_mod
    from api.device_workers import enroll_approval as enroll_mod

    store_mod._store = None
    enroll_mod.clear_all()
    store = store_mod.get_store()
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: store)
    monkeypatch.setattr("api.device_workers.store.get_store", lambda: store)
    monkeypatch.setattr("api.device_workers.auth.lan_access_enabled", lambda: True)
    # Owner gate: tests act as the host owner for approve/deny/list.
    monkeypatch.setattr(
        "api.device_workers.routes._ui_operator_or_401", lambda: None
    )

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    yield app.test_client()
    store_mod._store = None
    enroll_mod.clear_all()


def _pair(client, wid, secret=SECRET, remote=LAN):
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": wid, "pairing_secret": secret},
        environ_base=remote,
    )
    assert r.status_code == 202, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "pending"
    assert "token" not in body
    assert len(str(body.get("code") or "")) == 6
    return body


def _approve(client, rid):
    r = client.post(f"/api/workers/enroll-requests/{rid}/approve", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _poll(client, rid, secret=SECRET):
    return client.post(
        f"/api/workers/enroll/{rid}/poll", json={"pairing_secret": secret}
    )


def _pair_token(client, wid, secret=SECRET):
    body = _pair(client, wid, secret)
    _approve(client, body["request_id"])
    r = _poll(client, body["request_id"], secret)
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "approved", data
    assert data["worker_id"] == wid
    return data["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_bare_lan_enroll_returns_pending_without_token(client):
    body = _pair(client, "worker-a")
    assert body["request_id"]


def test_bare_lan_enroll_requires_pairing_secret(client):
    r = client.post(
        "/api/workers/enroll", json={"worker_id": "worker-a"}, environ_base=LAN
    )
    assert r.status_code == 400
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a", "pairing_secret": "short"},
        environ_base=LAN,
    )
    assert r.status_code == 400


def test_non_lan_enroll_rejected(client):
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a", "pairing_secret": SECRET},
        environ_base=PUBLIC,
    )
    assert r.status_code == 403


def test_poll_wrong_secret_rejected(client):
    body = _pair(client, "worker-a")
    r = _poll(client, body["request_id"], "ff" * 32)
    assert r.status_code == 200
    assert r.get_json()["status"] == "denied"
    # The real secret still sees pending (wrong guesses do not consume it).
    assert _poll(client, body["request_id"]).get_json()["status"] == "pending"


def test_approve_mints_token_delivered_once(client):
    token = _pair_token(client, "worker-a")
    assert token
    # Second pickup finds nothing.
    body = _pair(client, "worker-b")
    _approve(client, body["request_id"])
    first = _poll(client, body["request_id"])
    assert first.get_json()["status"] == "approved"
    again = _poll(client, body["request_id"])
    assert again.status_code == 404


def test_deny_blocks_poll(client):
    body = _pair(client, "worker-a")
    r = client.post(f"/api/workers/enroll-requests/{body['request_id']}/deny", json={})
    assert r.status_code == 200
    assert _poll(client, body["request_id"]).get_json()["status"] == "denied"


def test_approve_rotates_existing_token(client):
    first = _pair_token(client, "worker-a")
    body = _pair(client, "worker-a", SECRET2)
    _approve(client, body["request_id"])
    second = _poll(client, body["request_id"], SECRET2).get_json()
    assert second["status"] == "approved"
    assert second["token"] != first
    # Old token is revoked.
    claim = client.post(
        "/api/workers/jobs/claim",
        json={},
        headers=_bearer(first),
        environ_base=LAN,
    )
    assert claim.status_code == 401


def test_poll_expired(client):
    from api.device_workers import enroll_approval as enroll_mod

    body = _pair(client, "worker-a")
    rid = body["request_id"]
    enroll_mod._pending[rid]["created_at"] -= 700
    assert _poll(client, rid).get_json()["status"] == "expired"


def test_loopback_no_longer_authorizes_runtime(client):
    # No bearer, no token: loopback files pairing like anyone else.
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a", "pairing_secret": SECRET},
        environ_base=LOOPBACK,
    )
    assert r.status_code == 202
    # Loopback cannot drive worker runtime routes.
    reg = client.post(
        "/api/workers/register",
        json={"worker_id": "worker-a"},
        environ_base=LOOPBACK,
    )
    assert reg.status_code == 401
    claim = client.post(
        "/api/workers/jobs/claim", json={"worker_id": "worker-a"}, environ_base=LOOPBACK
    )
    assert claim.status_code == 401


def test_loopback_cannot_rotate(client):
    token = _pair_token(client, "worker-a")
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a", "rotate": True, "pairing_secret": SECRET},
        environ_base=LOOPBACK,
    )
    assert r.status_code == 202
    assert "token" not in r.get_json()
    # Existing token still works.
    claim = client.post(
        "/api/workers/jobs/claim", json={}, headers=_bearer(token), environ_base=LAN
    )
    assert claim.status_code == 200


def test_own_bearer_reenroll_never_echoes_token(client):
    token = _pair_token(client, "worker-a")
    again = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a"},
        headers=_bearer(token),
        environ_base=LAN,
    )
    assert again.status_code == 200
    assert "token" not in again.get_json()
    rotated = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-a", "rotate": True},
        headers=_bearer(token),
        environ_base=LAN,
    )
    assert rotated.status_code == 200
    fresh = rotated.get_json()["token"]
    assert fresh and fresh != token


def test_token_cannot_enroll_or_claim_as_another_worker(client):
    a = _pair_token(client, "worker-a")
    _pair_token(client, "worker-b")
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-b"},
        headers=_bearer(a),
        environ_base=LAN,
    )
    assert r.status_code == 403
    claim = client.post(
        "/api/workers/jobs/claim",
        json={"worker_id": "worker-b"},
        headers=_bearer(a),
        environ_base=LAN,
    )
    assert claim.status_code == 403


def test_bound_token_supplies_worker_id_when_omitted(client):
    a = _pair_token(client, "worker-a")
    claim = client.post(
        "/api/workers/jobs/claim", json={}, headers=_bearer(a), environ_base=LAN
    )
    assert claim.status_code == 200
    assert claim.get_json()["jobs"] == []


def test_stale_saved_token_files_pairing_request(client):
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "worker-c", "pairing_secret": SECRET},
        headers=_bearer("token-from-a-reset-host"),
        environ_base=LAN,
    )
    assert r.status_code == 202
    assert r.get_json()["status"] == "pending"


def test_local_worker_token_binds_to_host_id(client):
    from api.device_workers.store import get_store

    token = get_store().ensure_local_worker_token("host-w")
    assert get_store().lookup_enrolled_token(token) == "host-w"
    claim = client.post(
        "/api/workers/jobs/claim",
        json={"worker_id": "someone-else"},
        headers=_bearer(token),
        environ_base=LAN,
    )
    assert claim.status_code == 403
    own = client.post(
        "/api/workers/jobs/claim", json={}, headers=_bearer(token), environ_base=LAN
    )
    assert own.status_code == 200


def test_owner_routes_require_owner_session(client, monkeypatch):
    import api.device_workers.routes as routes

    monkeypatch.setattr(
        routes,
        "_ui_operator_or_401",
        lambda: ({"success": False, "error": "denied"}, 401),
    )
    assert client.get("/api/workers/enroll-requests").status_code == 401
    assert (
        client.post("/api/workers/enroll-requests/abc/approve", json={}).status_code
        == 401
    )
    assert (
        client.post("/api/workers/enroll-requests/abc/deny", json={}).status_code
        == 401
    )


def test_worker_can_poll_only_its_own_approval(client, monkeypatch):
    from api.device_workers import ssh_approval as sa
    import api.device_workers.routes as routes

    monkeypatch.setattr(sa, "_pending", {})
    a = _pair_token(client, "worker-a")
    b = _pair_token(client, "worker-b")
    # Drop the owner session: worker-bearer scoping must apply below.
    monkeypatch.setattr(
        routes,
        "_ui_operator_or_401",
        lambda: ({"success": False, "error": "denied"}, 401),
    )
    row = client.post(
        "/api/workers/ssh-approval/request",
        json={"worker_id": "worker-a"},
        headers=_bearer(a),
        environ_base=LAN,
    ).get_json()["request"]
    path = f"/api/workers/ssh-approval/{row['id']}"
    assert client.get(path, headers=_bearer(a), environ_base=LAN).status_code == 200
    assert client.get(path, headers=_bearer(b), environ_base=LAN).status_code == 403
    assert client.get(path, environ_base=LAN).status_code == 401


@pytest.mark.parametrize("source", ["environment", "settings", "secret-file"])
def test_retired_shared_token_cannot_authenticate_or_enroll(client, monkeypatch, tmp_path, source):
    from api.device_workers import config
    from core.runtime_paths import secrets_dir
    import json

    shared = "retired-shared"
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    if source == "environment":
        monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_TOKEN", shared)
    elif source == "settings":
        monkeypatch.setattr(config, "_settings_block", lambda: {"token": shared})
    else:
        path = secrets_dir() / "worker_shared_token.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"token": shared}))
    assert config.worker_token() == ""
    headers = _bearer(shared)
    assert client.post("/api/workers/jobs/claim", json={}, headers=headers, environ_base=LAN).status_code == 401
    pending = client.post("/api/workers/enroll", json={"worker_id": "worker-a", "pairing_secret": SECRET}, headers=headers, environ_base=LAN)
    assert pending.status_code == 202
    assert "token" not in pending.get_json()


def test_pairing_retry_replaces_same_device_request(client):
    a = _pair(client, "worker-a", SECRET)
    b = _pair(client, "worker-a", SECRET2)
    assert b["request_id"] != a["request_id"]
    assert _poll(client, a["request_id"]).status_code == 404
    assert _poll(client, b["request_id"], SECRET2).get_json()["status"] == "pending"


def test_pairing_table_bounded(client):
    for i in range(32):
        r = client.post(
            "/api/workers/enroll",
            json={"worker_id": f"w-{i}", "pairing_secret": SECRET},
            environ_base=LAN,
        )
        assert r.status_code == 202, i
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "w-full", "pairing_secret": SECRET},
        environ_base=LAN,
    )
    assert r.status_code == 429


def test_terminal_rows_swept_after_ttl(client):
    from api.device_workers import enroll_approval as enroll_mod

    body = _pair(client, "worker-a")
    client.post(f"/api/workers/enroll-requests/{body['request_id']}/deny", json={})
    assert _poll(client, body["request_id"]).get_json()["status"] == "denied"
    enroll_mod._pending[body["request_id"]]["updated_at"] -= 700
    assert client.get("/api/workers/enroll-requests").get_json()["pending"] == []
    assert _poll(client, body["request_id"]).status_code == 404
