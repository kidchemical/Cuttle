"""Contract tests for the settings blueprint (api.settings_routes).

The blueprint owns validation, persistence, defaults and authorization for
all settings families. These tests pin the frozen route contract: same
paths, same auth matrix, same payload shapes as the monolith era.
"""

from __future__ import annotations

from tests.test_http_authz import _auth_client, LAN

READS = [
    "/api/settings",
    "/api/settings/lan-access",
    "/api/settings/channels",
    "/api/settings/starred-slash",
    "/api/settings/starred-project",
    "/api/settings/ui-layout",
    "/api/settings/video-background",
    "/api/settings/github-app",
    "/api/app-settings",
]

# (method, path, payload): writes must stay owner-only.
WRITES = [
    ("POST", "/api/settings", {"mode": "open"}),
    ("POST", "/api/settings/lan-access", {"lan_access_enabled": False}),
    ("POST", "/api/settings/channels", {"channel": "webchat"}),
    ("POST", "/api/settings/starred-slash", {"prefixes": []}),
    ("POST", "/api/settings/starred-project", {"project": None}),
    ("POST", "/api/settings/ui-layout", {"rail_items": []}),
    ("POST", "/api/settings/video-background", {"enabled": False}),
    ("POST", "/api/settings/github-app", {"app_id": "1", "installation_id": "2"}),
    ("POST", "/api/settings/github-app/test", {}),
    ("DELETE", "/api/settings/github-app", {}),
]


def test_blueprint_serves_all_settings_paths(tmp_path, monkeypatch):
    """Every moved route answers (never 404) on the registered app."""
    from api import web_chat_api as wca

    rules = {str(r.rule) for r in wca.app.url_map.iter_rules()}
    for path in READS:
        assert path in rules, path


def test_settings_read_auth_matrix(tmp_path, monkeypatch):
    """Owner-only reads 401 anon; shared reads 200 for owner, 403 never."""
    from api import web_chat_api as wca

    anon = wca.app.test_client()
    for path in ("/api/settings", "/api/settings/lan-access", "/api/settings/channels"):
        assert anon.get(path, environ_base=LAN).status_code == 401, path

    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    for path in READS:
        res = ctx["client"].get(path, environ_base=LAN)
        assert res.status_code == 200, (path, res.status_code)


def test_settings_write_auth_matrix(tmp_path, monkeypatch):
    """All writes: 401 anon, 403 guest/non-owner, success owner."""
    from api import web_chat_api as wca

    anon = wca.app.test_client()
    for method, path, payload in WRITES:
        res = anon.open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 401, (method, path)

    guest = wca.app.test_client()
    assert guest.post("/api/auth/guest", json={}).status_code == 200
    for method, path, payload in WRITES:
        res = guest.open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 403, (method, path)

    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    for method, path, payload in WRITES:
        res = ctx["client"].open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 403, (method, path)


def _isolated_settings(monkeypatch, tmp_path):
    """Point the settings module at a throwaway file (never live settings)."""
    from managers import settings_manager as sm
    from api import settings_routes as sr

    isolated = sm.SettingsManager(settings_file=str(tmp_path / "test-settings.json"))
    monkeypatch.setattr(sm, "_settings_manager", isolated)
    monkeypatch.setattr(sr, "get_settings_manager", lambda: isolated)
    return isolated


def test_settings_owner_write_round_trip(tmp_path, monkeypatch):
    """Owner writes persist and read back through the owned module."""
    _isolated_settings(monkeypatch, tmp_path)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    res = ctx["client"].post(
        "/api/settings/ui-layout",
        json={"rail_items": ["chat", "jobs"], "panel_sections": ["old"]},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()["ui_layout"]
    assert body["rail_items"] == ["chat", "jobs"]
    assert "panel_sections" not in body  # legacy keys dropped by validator

    res = ctx["client"].post(
        "/api/settings/lan-access", json={}, environ_base=LAN
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "lan_access_enabled required"

    res = ctx["client"].post(
        "/api/settings/channels", json={"channel": "telegram"}, environ_base=LAN
    )
    assert res.status_code == 400


def test_settings_validators_owned():
    """Validation lives in api.settings_routes, next to the registry."""
    from api import settings_routes as sr

    assert sr.validate_channel_update({"channel": "webchat"}) == (True, "")
    assert sr.validate_channel_update({"channel": "telegram"})[0] is False
    assert sr.validate_lan_access_update({"lan_access_enabled": True}) == (True, "")
    assert sr.validate_lan_access_update({})[0] is False
    assert sr.validate_ui_layout_update({"rail_items": [1], "nope": 1}) == {
        "rail_items": ["1"]
    }
    assert "sandbox" not in {f["name"] for f in sr.SETTING_FAMILIES}
    assert sr.validate_channel_update({"channel": "discord"})[0] is False
    # Every writable family is owner-only. Read-only lookups declare write
    # "n/a" (app-settings aggregate, video-metadata proxy) and are exempt.
    writable = {f["name"] for f in sr.SETTING_FAMILIES if f["write"] != "n/a"}
    assert "app-settings" not in writable
    assert {f["write"] for f in sr.SETTING_FAMILIES if f["write"] != "n/a"} == {"owner"}


def test_retired_graph_settings_routes_are_absent():
    from api import web_chat_api as wca
    assert '/api/settings/sandbox' not in {r.rule for r in wca.app.url_map.iter_rules()}
