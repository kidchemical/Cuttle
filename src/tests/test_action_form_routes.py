"""Action-form HTTP transport contract: routes, auth, shapes, recovery.

Characterization for the Phase 2 Slice 2 extraction. These tests pin the
transport boundary regardless of which module registers the routes
(monolith before, ``api.action_form_routes`` after):

- 4 routes registered POST with no duplicates,
- anonymous / guest-owning-chat / owner / wrong-chat matrix,
- fabricated/tampered/expired token behavior,
- restart-recovered (history) Confirm/Cancel,
- dismiss + follow-up + watch-state ownership and shapes.

Domain-level HMAC/service behavior stays covered by test_action_forms.py
and test_action_form_process_restart.py (retained, not duplicated here).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAN = {"REMOTE_ADDR": "192.168.1.77"}


def _ctx(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca
    from api.action_forms import clear_forms_for_tests

    db_path = tmp_path / "af-routes.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "transport-characterization")
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    clear_forms_for_tests()
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    guest = db.create_user("guest@local", "Guest", "local", password="x")
    return {
        "client": wca.app.test_client(),
        "db": db,
        "owner_sid": db.create_chat_session(owner, "o"),
        "guest_sid": db.create_chat_session(guest, "g"),
        "owner": db.create_auth_session(owner),
        "guest": db.create_auth_session(guest),
    }


def _as(client, token):
    client.set_cookie("session_token", token)
    return client


def _choice_spec(session_id):
    # flask.restart/status resolves as a global action; tests mock
    # execute_inline_action so nothing really runs.
    return {
        "mode": "choice",
        "title": "Pick",
        "silent": True,
        "session_id": f"db_session_{session_id}",
        "options": [{"id": "a", "label": "A", "action": "flask.restart",
                     "params": {"mode": "status"}}],
    }


def test_action_form_routes_registered_post_no_duplicates():
    from api import web_chat_api as wca

    found = {}
    for rule in wca.app.url_map.iter_rules():
        name = str(rule.rule)
        if name.startswith("/api/action-form/"):
            found.setdefault((name, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))), 0)
            found[(name, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))] += 1
    for path in (
        "/api/action-form/run",
        "/api/action-form/dismiss",
        "/api/action-form/watch-state",
        "/api/action-form/followup-message",
    ):
        matches = [k for k in found if k[0] == path and k[1] == ("POST",)]
        assert matches, f"missing POST {path}"
        for m in matches:
            assert found[m] == 1, f"duplicate registration {m}"


def test_run_anonymous_and_validation_shapes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    anon = ctx["client"]
    res = anon.post("/api/action-form/run", json={"token": "x"}, environ_base=LAN)
    assert res.status_code == 401
    assert res.get_json()["type"] == "action_form"
    res = anon.post("/api/action-form/run", json={}, environ_base=LAN)
    assert res.status_code == 400
    assert res.get_json()["toast"] == "Missing form token."
    # Unknown token with a session the caller owns: expired, silent.
    c = _as(ctx["client"], ctx["guest"])
    res = c.post(
        "/api/action-form/run",
        json={"token": "no-such-form", "session_id": ctx["guest_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["success"] is False
    assert body["silent"] is True
    assert "expired" in body["toast"]


def test_run_success_lock_and_cancel(tmp_path, monkeypatch):
    import json as _json

    from api.action_forms import rewrite_action_forms
    import api.action_forms as af

    ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(
        af, "execute_inline_action", lambda token, session_id=None: {"success": True, "response": "ok"}
    )
    # One-shot lock persists through history: write the card there first.
    text = "<cuttle_action_form>" + _json.dumps(_choice_spec(ctx["owner_sid"])) + "</cuttle_action_form>"
    out, n = rewrite_action_forms(
        text, session_id=f"db_session_{ctx['owner_sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    ctx["db"].add_message(ctx["owner_sid"], "assistant", out)
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>", out, re.S
    )
    assert m
    fid = _json.loads(m.group(1)).get("id")
    assert fid
    c = _as(ctx["client"], ctx["owner"])
    res = c.post(
        "/api/action-form/run",
        json={"token": fid, "selection": {"option": "a"}, "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["success"] is True
    assert body["form_id"] == fid
    assert "a" in (body.get("selected") or [])
    assert "injected_user_message" not in body  # non-resume stays silent
    # One-shot lock persisted: second run reports already_locked.
    res2 = c.post(
        "/api/action-form/run",
        json={"token": fid, "selection": {"option": "a"}, "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res2.get_json().get("already_locked") is True
    # Cancel on a fresh form succeeds without resume.
    from api.action_forms import normalize_action_form_spec, register_action_form

    spec2 = normalize_action_form_spec(_choice_spec(ctx["owner_sid"]), project_path=str(tmp_path))
    fid2 = register_action_form(
        session_id=f"db_session_{ctx['owner_sid']}", project_path=str(tmp_path), spec=spec2
    )
    res3 = c.post(
        "/api/action-form/run",
        json={"token": fid2, "selection": {"cancel": True}, "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res3.get_json()["success"] is True
    assert res3.get_json().get("resume") in (False, None)


def test_run_wrong_chat_is_denied(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    c = _as(ctx["client"], ctx["guest"])
    res = c.post(
        "/api/action-form/run",
        json={"token": "whatever", "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 404, res.get_json()
    assert res.get_json()["type"] == "action_form"


def test_run_tampered_and_garbage_tokens_expire(tmp_path, monkeypatch):
    from api.action_forms import encode_form_fallback, normalize_action_form_spec

    ctx = _ctx(tmp_path, monkeypatch)
    spec = normalize_action_form_spec(_choice_spec(ctx["owner_sid"]), project_path=str(tmp_path))
    good = encode_form_fallback(dict(spec, session_id=f"db_session_{ctx['owner_sid']}"))
    assert good.startswith("inline.")
    bad = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    c = _as(ctx["client"], ctx["owner"])
    for token in (bad, "inline.garbage", "not-a-token-at-all"):
        res = c.post(
            "/api/action-form/run",
            json={"token": token, "session_id": ctx["owner_sid"]},
            environ_base=LAN,
        )
        assert res.status_code == 200, (token, res.get_json())
        body = res.get_json()
        assert body["success"] is False
        assert body["silent"] is True


def test_run_restart_recovery_confirm_and_cancel(tmp_path, monkeypatch):
    from api.action_forms import clear_forms_for_tests, rewrite_action_forms
    import api.action_forms as af

    ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(
        af, "execute_inline_action", lambda token, session_id=None: {"success": True, "response": "ok"}
    )
    text = "<cuttle_action_form>" + json.dumps(_choice_spec(ctx["owner_sid"])) + "</cuttle_action_form>"
    out, n = rewrite_action_forms(
        text, session_id=f"db_session_{ctx['owner_sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    ctx["db"].add_message(ctx["owner_sid"], "assistant", out)
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>", out, re.S
    )
    assert m
    form_id = json.loads(m.group(1)).get("id")
    assert form_id
    clear_forms_for_tests()  # simulate Flask restart: memory flushed
    c = _as(ctx["client"], ctx["owner"])
    res = c.post(
        "/api/action-form/run",
        json={"token": "stale-pending-id", "form_id": form_id,
              "selection": {"option": "a"}, "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["success"] is True
    # Cancel path needs an unconsumed card with a DISTINCT id: flask.restart
    # specs share one controller id per generation, so use a plain choice.
    plain2 = dict(_choice_spec(ctx["owner_sid"]))
    plain2["options"] = [{"id": "a", "label": "A"}]
    text2 = "<cuttle_action_form>" + json.dumps(plain2) + "</cuttle_action_form>"
    out2, n2 = rewrite_action_forms(
        text2, session_id=f"db_session_{ctx['owner_sid']}", project_path=str(tmp_path)
    )
    assert n2 == 1
    ctx["db"].add_message(ctx["owner_sid"], "assistant", out2)
    m2 = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>", out2, re.S
    )
    form_id2 = json.loads(m2.group(1)).get("id")
    clear_forms_for_tests()
    res2 = c.post(
        "/api/action-form/run",
        json={"token": "stale-pending-id", "form_id": form_id2,
              "selection": {"cancel": True}, "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res2.get_json()["success"] is True


def test_dismiss_ownership_and_shapes(tmp_path, monkeypatch):
    import json as _json

    from api.action_forms import rewrite_action_forms

    ctx = _ctx(tmp_path, monkeypatch)
    # Dismiss locks cards in persisted history: write the form there first.
    # Plain choice (no action) so the pending id is a uuid, not a shared
    # flask-restart controller (those are skipped pre-auth by design).
    plain = dict(_choice_spec(ctx["guest_sid"]))
    plain["options"] = [{"id": "a", "label": "A"}]
    text = "<cuttle_action_form>" + _json.dumps(plain) + "</cuttle_action_form>"
    out, n = rewrite_action_forms(
        text, session_id=f"db_session_{ctx['guest_sid']}", project_path=str(tmp_path)
    )
    assert n == 1
    ctx["db"].add_message(ctx["guest_sid"], "assistant", out)
    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*?\})\s*</cuttle_action_form_pending>", out, re.S
    )
    assert m
    fid = _json.loads(m.group(1)).get("id")
    assert fid
    anon = ctx["client"]
    res = anon.post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["guest_sid"], "form_ids": [fid]},
        environ_base=LAN,
    )
    assert res.status_code == 401
    res = anon.post("/api/action-form/dismiss", json={}, environ_base=LAN)
    assert res.status_code == 400  # missing session_id, checked before auth
    c = _as(ctx["client"], ctx["guest"])
    res = c.post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["owner_sid"], "form_ids": [fid]},
        environ_base=LAN,
    )
    assert res.status_code == 404  # another user's chat
    res = c.post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["guest_sid"], "form_ids": [fid]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["locked"] == [fid]
    # Restart-controller ids never lock: skipped, not an error — decided
    # pre-auth so blind client retries cannot poison shared controllers.
    res = anon.post(
        "/api/action-form/dismiss",
        json={"session_id": ctx["guest_sid"], "form_ids": ["flask-restart-g3"]},
        environ_base=LAN,
    )
    assert res.status_code == 200
    assert res.get_json() == {"success": True, "locked": [], "toast": "Ignored", "skipped": True}


def test_followup_ownership_and_shapes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    anon = ctx["client"]
    res = anon.post("/api/action-form/followup-message", json={}, environ_base=LAN)
    assert res.status_code == 400  # missing spec, checked before auth
    res = anon.post(
        "/api/action-form/followup-message",
        json={"spec": _choice_spec(ctx["guest_sid"]), "session_id": ctx["guest_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 401
    c = _as(ctx["client"], ctx["guest"])
    res = c.post(
        "/api/action-form/followup-message",
        json={"spec": _choice_spec(ctx["owner_sid"]), "session_id": ctx["owner_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 404
    res = c.post(
        "/api/action-form/followup-message",
        json={"spec": _choice_spec(ctx["guest_sid"]), "session_id": ctx["guest_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["success"] is True
    assert body["session_id"] == ctx["guest_sid"]
    assert "cuttle_action_form" in body["response"]


def test_watch_state_auth_and_shapes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    anon = ctx["client"]
    res = anon.post(
        "/api/action-form/watch-state",
        json={"form_id": "f", "session_id": ctx["guest_sid"]},
        environ_base=LAN,
    )
    assert res.status_code == 401
    c = _as(ctx["client"], ctx["guest"])
    res = c.post("/api/action-form/watch-state", json={}, environ_base=LAN)
    assert res.status_code == 400
    res = c.post(
        "/api/action-form/watch-state",
        json={"form_id": "f", "session_id": ctx["guest_sid"], "snapshot": {"label": "L"}},
        environ_base=LAN,
    )
    assert res.status_code == 200
    assert set(res.get_json()) == {"success"}


@pytest.mark.parametrize('mode', ['status', 'graceful', 'when-idle', 'force'])
def test_restart_card_bypasses_broken_project_discovery(tmp_path, monkeypatch, mode):
    from api import action_forms as af, project_actions as pa, flask_restart as restart
    ctx = _ctx(tmp_path, monkeypatch)
    calls = []
    def broken(*args, **kwargs):
        raise ImportError('simulated partially loaded configuration modules')
    monkeypatch.setattr(af, 'find_project_action_resolved', broken)
    monkeypatch.setattr(pa, 'list_project_actions', broken)
    monkeypatch.setattr(pa, '_execute_shell', broken)
    monkeypatch.setattr(restart, 'request_restart', lambda **kw: calls.append(kw) or {'success': True, 'state': 'waiting_for_idle'})
    spec = af.normalize_action_form_spec({
        'mode': 'choice', 'silent': True,
        'options': [{'id': 'restart', 'label': 'Restart', 'action': 'flask.restart', 'params': {'mode': mode}}],
    }, project_path=str(tmp_path))
    fid = af.register_action_form(session_id=f"db_session_{ctx['owner_sid']}", project_path=str(tmp_path), spec=spec)
    response = _as(ctx['client'], ctx['owner']).post('/api/action-form/run', json={
        'token': fid, 'selection': {'option': 'restart'}, 'session_id': ctx['owner_sid'],
    })
    assert response.status_code == 200
    assert response.get_json()['success'] is True, response.get_json()
    assert calls == [{'mode': mode, 'session_id': f"db_session_{ctx['owner_sid']}",
                      'user_source': 'flask.restart_action', 'force_confirm': mode == 'force', 'chat_notify': False}]


def test_native_restart_card_still_requires_owner(tmp_path, monkeypatch):
    from api import action_forms as af, flask_restart as restart
    ctx = _ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(restart, 'request_restart', lambda **kw: pytest.fail('non-owner reached restart'))
    spec = af.normalize_action_form_spec(_choice_spec(ctx['guest_sid']), project_path=str(tmp_path))
    fid = af.register_action_form(session_id=f"db_session_{ctx['guest_sid']}", project_path=str(tmp_path), spec=spec)
    response = _as(ctx['client'], ctx['guest']).post('/api/action-form/run', json={
        'token': fid, 'selection': {'option': 'a'}, 'session_id': ctx['guest_sid'],
    })
    assert response.status_code == 200
    assert response.get_json()['success'] is False
    assert 'Owner privileges required' in response.get_json()['toast']
