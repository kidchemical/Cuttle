"""Authorization helpers and high-risk mutating routes."""

from __future__ import annotations

import json

import pytest
from flask import Flask

from api.http_authz import is_guest_user, is_owner_user
from api.project_actions import (
    _b64url_encode,
    decode_inline_action_payload,
    encode_inline_action_payload,
)


@pytest.fixture()
def worker_db(tmp_path, monkeypatch):
    db = tmp_path / "device_workers.db"
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(db))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    monkeypatch.delenv("CUTTLE_DEVICE_WORKERS_TOKEN", raising=False)
    import api.device_workers.store as store_mod

    store_mod._store = None
    yield store_mod.get_store()
    store_mod._store = None


def test_guest_is_never_owner(monkeypatch):
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    guest = {"id": 1, "email": "g@x", "username": "g_1", "auth_provider": "guest"}
    local = {"id": 2, "email": "me@x", "username": "me", "auth_provider": "local"}
    assert is_guest_user(guest) is True
    assert is_owner_user(guest) is False
    assert is_owner_user(local) is True


def test_owner_email_match(monkeypatch):
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@cuttle")
    user = {"id": 3, "email": "owner@cuttle", "username": "x", "auth_provider": "local"}
    other = {"id": 4, "email": "guest@cuttle", "username": "y", "auth_provider": "local"}
    assert is_owner_user(user) is True
    assert is_owner_user(other) is False


def test_unsigned_inline_token_rejected():
    blob = json.dumps(
        {"action": "discord.post", "project_path": "/", "params": {"content": "x"}},
        separators=(",", ":"),
    ).encode("utf-8")
    token = "inline." + _b64url_encode(blob)
    assert decode_inline_action_payload(token) is None


def test_signed_inline_token_roundtrip(monkeypatch):
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "test-secret-for-hmac")
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    token = encode_inline_action_payload(
        action_name="discord.post",
        project_path="/tmp/p",
        params={"channel": "prompt-lab"},
        session_id="db_session_9",
    )
    decoded = decode_inline_action_payload(token)
    assert decoded and decoded["action"] == "discord.post"
    assert decoded["session_id"] == "db_session_9"
    pa._hmac_secret_cache = None


def test_anonymous_sandbox_post_rejected():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.post(
        "/api/settings/sandbox",
        json={"enabled": False},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
    )
    assert res.status_code == 401


def test_guest_cannot_change_sandbox():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    guest = client.post("/api/auth/guest", json={})
    assert guest.status_code == 200
    res = client.post("/api/settings/sandbox", json={"enabled": False})
    assert res.status_code == 403


def test_anonymous_action_form_run_rejected():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    token = encode_inline_action_payload(
        action_name="__action_form__",
        project_path="",
        params={"spec": {"mode": "choice", "options": []}},
    )
    res = client.post(
        "/api/action-form/run",
        json={"token": token, "selection": {"id": "a"}, "session_id": 1},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
    )
    assert res.status_code == 401


def test_stop_webapi_is_gone():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.post("/api/stop-webapi")
    assert res.status_code == 410
    assert res.get_json()["error"] == "legacy_process_control_removed"


def test_lan_enroll_denied_when_lan_disabled(worker_db, monkeypatch):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr("api.device_workers.auth.lan_access_enabled", lambda: False)
    monkeypatch.setattr("api.device_workers.auth.worker_token", lambda: "")

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()
    r = client.post(
        "/api/workers/enroll",
        json={"worker_id": "yoga", "hostname": "YOGA"},
        environ_base={"REMOTE_ADDR": "192.168.1.40"},
    )
    assert r.status_code == 403


def test_lan_job_submit_without_owner_is_401(worker_db, monkeypatch):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr("api.device_workers.auth.worker_token", lambda: "")

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()
    r = client.post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}},
        environ_base={"REMOTE_ADDR": "192.168.1.99"},
    )
    assert r.status_code == 401


def test_worker_token_cannot_submit_jobs(worker_db, monkeypatch):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr("api.device_workers.auth.worker_token", lambda: "shared")
    monkeypatch.setattr("api.device_workers.store.get_store", lambda: worker_db)

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()
    r = client.post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}},
        headers={"Authorization": "Bearer shared"},
        environ_base={"REMOTE_ADDR": "192.168.1.99"},
    )
    assert r.status_code == 401


def test_loopback_without_session_cannot_submit_jobs(worker_db, monkeypatch):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr("api.device_workers.auth.worker_token", lambda: "")

    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()
    r = client.post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code == 401


def test_hmac_secret_unavailable_raises(monkeypatch, tmp_path):
    import api.project_actions as pa

    monkeypatch.delenv("CUTTLE_ACTION_HMAC_SECRET", raising=False)
    pa._hmac_secret_cache = None

    class _FailPath:
        def is_file(self):
            return False

        @property
        def parent(self):
            class _P:
                def mkdir(self, **_kw):
                    raise OSError("read-only")

            return _P()

        def __str__(self):
            return str(tmp_path / "denied-secret")

    monkeypatch.setattr(pa, "_HMAC_SECRET_PATH", _FailPath())
    with pytest.raises(RuntimeError, match="HMAC secret"):
        encode_inline_action_payload(
            action_name="discord.post",
            project_path="/",
            params={},
        )
    pa._hmac_secret_cache = None


def test_single_user_mode_all_non_guests_are_owners(monkeypatch):
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    a = {"id": 1, "email": "a@x", "username": "a", "auth_provider": "local"}
    b = {"id": 2, "email": "b@x", "username": "b", "auth_provider": "google"}
    assert is_owner_user(a) is True
    assert is_owner_user(b) is True


def _auth_client(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca
    from api.action_forms import clear_forms_for_tests, rewrite_action_forms

    db_path = tmp_path / "authz.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "test-hmac-recovery")
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    clear_forms_for_tests()
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    sid = db.create_chat_session(owner, "forms")
    other_sid = db.create_chat_session(other, "other")
    token = db.create_auth_session(owner)
    other_token = db.create_auth_session(other)
    client = wca.app.test_client()
    return {
        "client": client,
        "sid": sid,
        "other_sid": other_sid,
        "token": token,
        "other_token": other_token,
        "rewrite": rewrite_action_forms,
        "clear": clear_forms_for_tests,
        "tmp": tmp_path,
    }


def test_action_form_recovers_via_spec_after_memory_flush(tmp_path, monkeypatch):
    """Flask restart drops pending form ids; spec is recovered from chat history."""
    import json
    import re

    from api.auth_db import get_auth_db

    ctx = _auth_client(tmp_path, monkeypatch)
    text = (
        '<cuttle_action_form>{"mode":"choice","title":"Pick","silent":true,'
        '"options":[{"id":"a","label":"A"}]}</cuttle_action_form>'
    )
    out, n = ctx["rewrite"](
        text, session_id=f"db_session_{ctx['sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    get_auth_db().add_message(ctx["sid"], "assistant", out)
    ctx["clear"]()
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>",
        out,
        re.S,
    )
    assert m
    spec = json.loads(m.group(1))
    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].post(
        "/api/action-form/run",
        json={
            "token": "stale-pending-id",
            "spec": spec,
            "selection": {"option": "a"},
            "session_id": ctx["sid"],
            "form_id": spec.get("id"),
        },
    )
    data = res.get_json()
    assert res.status_code == 200, data
    assert data["success"] is True
    assert "a" in (data.get("selected") or [])


def test_action_form_ignores_tampered_spec_after_history_recovery(tmp_path, monkeypatch):
    """Authenticated owner cannot swap actions/params/project via data-spec."""
    import copy
    import json
    import re

    from api.auth_db import get_auth_db

    ctx = _auth_client(tmp_path, monkeypatch)
    text = (
        '<cuttle_action_form>{"mode":"choice","title":"Pick","silent":true,'
        '"options":[{"id":"a","label":"A"}]}</cuttle_action_form>'
    )
    out, n = ctx["rewrite"](
        text, session_id=f"db_session_{ctx['sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    get_auth_db().add_message(ctx["sid"], "assistant", out)
    ctx["clear"]()
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>",
        out,
        re.S,
    )
    spec = json.loads(m.group(1))
    tampered = copy.deepcopy(spec)
    tampered["options"] = [
        {
            "id": "a",
            "label": "A",
            "action": "flask.restart",
            "params": {"mode": "force"},
        }
    ]
    tampered["project_path"] = "/tmp/evil"
    tampered["session_id"] = f"db_session_{ctx['other_sid']}"
    tampered["id"] = spec.get("id")
    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].post(
        "/api/action-form/run",
        json={
            "token": spec.get("id"),
            "spec": tampered,
            "selection": {"option": "a"},
            "session_id": ctx["sid"],
            "form_id": spec.get("id"),
            "project_path": "/tmp/evil",
        },
    )
    data = res.get_json()
    assert res.status_code == 200, data
    assert data["success"] is True
    assert "a" in (data.get("selected") or [])
    assert "flask.restart" not in (data.get("actions") or [])


def test_action_form_fabricated_spec_without_history_fails(tmp_path, monkeypatch):
    ctx = _auth_client(tmp_path, monkeypatch)
    spec = {
        "id": "forged-form-id",
        "mode": "choice",
        "title": "Pick",
        "session_id": f"db_session_{ctx['sid']}",
        "project_path": str(tmp_path),
        "options": [
            {
                "id": "a",
                "label": "A",
                "action": "flask.restart",
                "params": {"mode": "force"},
            }
        ],
    }
    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].post(
        "/api/action-form/run",
        json={
            "token": "forged-form-id",
            "spec": spec,
            "selection": {"option": "a"},
            "session_id": ctx["sid"],
            "form_id": "forged-form-id",
        },
    )
    data = res.get_json()
    assert data and data.get("success") is False
    assert "flask.restart" not in (data.get("actions") or [])


def test_flask_restart_card_recovers_canonical_spec_after_memory_flush(
    tmp_path, monkeypatch
):
    """Restart-equivalent: persist card, clear in-memory registry, ignore param tamper."""
    import copy
    import json
    import re
    from unittest.mock import MagicMock, patch

    from api.auth_db import get_auth_db

    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("CUTTLE_FLASK_GENERATION", "9")
    actions = tmp_path / ".cuttle" / "actions"
    actions.mkdir(parents=True)
    (actions / "flask-restart.yaml").write_text(
        "name: flask.restart\ntype: shell\nrun: echo hi\nworkdir: .\n",
        encoding="utf-8",
    )
    text = (
        '<cuttle_action_form>{"title":"Restart Flask (daemon-owned)","mode":"choice",'
        '"options":[{"id":"graceful","label":"Graceful","action":"flask.restart",'
        '"params":{"mode":"graceful"}}]}</cuttle_action_form>'
    )
    out, n = ctx["rewrite"](
        text, session_id=f"db_session_{ctx['sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    get_auth_db().add_message(ctx["sid"], "assistant", out)
    ctx["clear"]()
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*)\s*</cuttle_action_form_pending>",
        out,
        re.S,
    )
    spec = json.loads(m.group(1))
    tampered = copy.deepcopy(spec)
    tampered["options"][0]["params"] = {"mode": "force"}
    ctx["client"].set_cookie("session_token", ctx["token"])
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "ok"
    proc.stderr = ""
    with patch("api.project_actions.subprocess.run", return_value=proc) as run:
        res = ctx["client"].post(
            "/api/action-form/run",
            json={
                "token": spec.get("id"),
                "spec": tampered,
                "selection": {"option": "graceful"},
                "session_id": ctx["sid"],
                "form_id": spec.get("id"),
            },
        )
    data = res.get_json()
    assert res.status_code == 200, data
    assert data["success"] is True
    env = run.call_args.kwargs["env"]
    assert env["CUTTLE_PARAM_MODE"] == "graceful"


def test_action_form_unsigned_token_without_spec_fails_after_flush(tmp_path, monkeypatch):
    ctx = _auth_client(tmp_path, monkeypatch)
    blob = json.dumps(
        {
            "action": "__action_form__",
            "project_path": str(tmp_path),
            "params": {
                "spec": {
                    "mode": "choice",
                    "options": [{"id": "a", "label": "A"}],
                    "session_id": f"db_session_{ctx['sid']}",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    unsigned = "inline." + _b64url_encode(blob)
    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].post(
        "/api/action-form/run",
        json={"token": unsigned, "selection": {"option": "a"}, "session_id": ctx["sid"]},
    )
    data = res.get_json()
    assert data and data.get("success") is False


def test_action_form_spec_rejected_for_other_user(tmp_path, monkeypatch):
    ctx = _auth_client(tmp_path, monkeypatch)
    spec = {
        "mode": "choice",
        "title": "Pick",
        "session_id": f"db_session_{ctx['sid']}",
        "options": [{"id": "a", "label": "A"}],
    }
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    res = ctx["client"].post(
        "/api/action-form/run",
        json={
            "spec": spec,
            "selection": {"option": "a"},
            "session_id": ctx["sid"],
        },
    )
    assert res.status_code == 404


def test_owner_session_can_submit_mesh_job(tmp_path, monkeypatch, worker_db):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr(
        "api.device_workers.executor.validate_job_submission",
        lambda *_a, **_k: (True, None),
    )
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    r = ctx["client"].post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["success"] is True
    assert r.get_json()["job"]["type"] == "ping"


def test_guest_cannot_submit_mesh_job(tmp_path, monkeypatch, worker_db):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    guest = client.post("/api/auth/guest", json={})
    assert guest.status_code == 200
    r = client.post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code == 403


def test_action_form_dismiss_requires_chat_ownership(tmp_path, monkeypatch):
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    res = ctx["client"].post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["sid"], "form_ids": ["form-x"], "toast": "Ignored"},
    )
    assert res.status_code == 404
    ctx["client"].set_cookie("session_token", ctx["token"])
    ok = ctx["client"].post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["sid"], "form_ids": ["form-x"], "toast": "Ignored"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["success"] is True

