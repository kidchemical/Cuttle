"""Auto-compact HTTP: per-chat override (``/api/auto-compact/chats/<id>``) and
global settings (``/api/settings/auto-compact``), flag on and off."""

from __future__ import annotations

import pytest

from tests.auth.test_http_authz import LAN, _auth_client


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    from managers import settings_manager as sm
    isolated = sm.SettingsManager(settings_file=str(tmp_path / "settings.json"))
    monkeypatch.setattr(sm, "_settings_manager", isolated)
    from api.auto_compact import service
    flag = {"on": True}
    monkeypatch.setattr(service, "available", lambda: flag["on"])
    c = _auth_client(tmp_path, monkeypatch)
    c["client"].set_cookie("session_token", c["token"])
    c["flag"] = flag
    return c


def test_chat_override_round_trip(ctx):
    client, sid = ctx["client"], ctx["sid"]
    res = client.get(f"/api/auto-compact/chats/{sid}", environ_base=LAN)
    body = res.get_json()
    assert res.status_code == 200 and body["available"] is True
    assert body["policy"]["percent"] == 70 and body["override"] == {}
    res = client.post(f"/api/auto-compact/chats/db_session_{sid}",
                      json={"override": {"percent": 50, "enabled": None}}, environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["policy"]["percent"] == 50 and res.get_json()["inherited"]["percent"] == 70
    bad = client.post(f"/api/auto-compact/chats/{sid}", json={"override": {"percent": 3}}, environ_base=LAN)
    assert bad.status_code == 400 and "percent" in bad.get_json()["error"]


def test_other_users_chat_is_refused(ctx):
    res = ctx["client"].get(f"/api/auto-compact/chats/{ctx['other_sid']}", environ_base=LAN)
    assert res.status_code in (403, 404)


def test_flag_off_answers_unavailable_and_writes_nothing(ctx):
    ctx["flag"]["on"] = False
    client, sid = ctx["client"], ctx["sid"]
    assert client.get(f"/api/auto-compact/chats/{sid}", environ_base=LAN).get_json() == {
        "success": True, "available": False}
    client.post(f"/api/auto-compact/chats/{sid}", json={"override": {"enabled": False}}, environ_base=LAN)
    from api.auto_compact import store
    assert store.get_override(sid) == {}
    assert client.get("/api/settings/auto-compact", environ_base=LAN).get_json()["available"] is False


def test_global_settings_round_trip(ctx, monkeypatch):
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    client = ctx["client"]
    res = client.post("/api/settings/auto-compact", json={"managed": {"percent": 50}, "quiet_seconds": 5},
                      environ_base=LAN)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["settings"]["managed"]["percent"] == 50 and body["settings"]["managed"]["max_tokens"] == 250_000
    assert body["settings"]["quiet_seconds"] == 5 and body["defaults"]["managed"]["percent"] == 60
    bad = client.post("/api/settings/auto-compact", json={"mode": "zip"}, environ_base=LAN)
    assert bad.status_code == 400
