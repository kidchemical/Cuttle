"""Contract tests for the settings blueprint (api.settings_routes).

The blueprint owns validation, persistence, defaults and authorization for
all settings families. These tests pin the frozen route contract: same
paths, same auth matrix, same payload shapes as the monolith era.
"""

from __future__ import annotations

from tests.auth.test_http_authz import _auth_client, LAN

READS = [
    "/api/settings/lan-access",
    "/api/settings/channels",
    "/api/settings/starred-slash",
    "/api/settings/starred-project",
    "/api/settings/ui-layout",
    "/api/settings/video-background",
    "/api/settings/git-auto-commit",
    "/api/settings/github-app",
    "/api/settings/agent-adapters",
    "/api/app-settings",
]

# (method, path, payload): writes must stay owner-only.
WRITES = [
    ("POST", "/api/settings/lan-access", {"lan_access_enabled": False}),
    ("POST", "/api/settings/channels", {"channel": "webchat"}),
    ("POST", "/api/settings/starred-slash", {"prefixes": []}),
    ("POST", "/api/settings/starred-project", {"project": None}),
    ("POST", "/api/settings/ui-layout", {"rail_items": []}),
    ("POST", "/api/settings/video-background", {"enabled": False}),
    ("POST", "/api/settings/git-auto-commit", {"git_auto_commit": False}),
    ("POST", "/api/settings/agent-adapters", {"steer": {"claude": True}}),
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
    for path in ("/api/settings/lan-access", "/api/settings/channels"):
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


def test_agent_adapters_round_trip(tmp_path, monkeypatch):
    """Steer switches and the drop-in opt-in persist; env overrides are reported."""
    isolated = _isolated_settings(monkeypatch, tmp_path)
    monkeypatch.delenv("CUTTLE_AGENT_STEER", raising=False)
    monkeypatch.delenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", raising=False)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    body = ctx["client"].get("/api/settings/agent-adapters", environ_base=LAN).get_json()
    assert body["steer"] == {"codex": True, "muse": True, "claude": True}
    assert body["allow_project_adapters"] is False
    assert body["steer_env_disabled"] is False
    assert {"claude", "codex"} <= set(body["defaults"])

    res = ctx["client"].post(
        "/api/settings/agent-adapters",
        json={"steer": {"claude": False}, "allow_project_adapters": True},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["steer"]["claude"] is False and body["steer"]["codex"] is True
    assert body["allow_project_adapters"] is True
    assert isolated.get_setting("agent_steer") == {"claude": False}
    assert isolated.get_setting("agent_harness") == {"allow_project_adapters": True}

    from api.agent_harness.steer import steer_enabled

    assert steer_enabled("claude") is False
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")
    monkeypatch.setenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", "1")
    body = ctx["client"].get("/api/settings/agent-adapters", environ_base=LAN).get_json()
    assert body["steer_env_disabled"] is True
    assert body["allow_project_adapters_env"] is True

    for bad in ({"steer": {"cursor": True}}, {"steer": {"claude": "yes"}},
                {"allow_project_adapters": 1}, {}):
        res = ctx["client"].post("/api/settings/agent-adapters", json=bad, environ_base=LAN)
        assert res.status_code == 400, bad


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


def test_app_settings_snapshot_is_owner_only(tmp_path, monkeypatch):
    """GET /api/app-settings: 401 anon, 403 guest/non-owner, 200 owner.

    The aggregate returns the full settings.json snapshot (channel posture,
    discovery/LAN, device_workers incl. SSH targets, router/provider config,
    starred project paths), so it must never answer an unauthenticated
    caller — especially over LAN HTTP.
    """
    from api import settings_routes as sr
    from api import web_chat_api as wca

    assert next(
        f["read"] for f in sr.SETTING_FAMILIES if f["name"] == "app-settings"
    ) == "owner"

    _isolated_settings(monkeypatch, tmp_path)
    anon = wca.app.test_client()
    assert anon.get("/api/app-settings", environ_base=LAN).status_code == 401

    guest = wca.app.test_client()
    assert guest.post("/api/auth/guest", json={}).status_code == 200
    assert guest.get("/api/app-settings", environ_base=LAN).status_code == 403

    # Single-user mode (helper clears OWNER_USER_EMAIL): the first account is
    # the owner, the second an authenticated non-owner.
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert ctx["client"].get("/api/app-settings", environ_base=LAN).status_code == 403

    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].get("/api/app-settings", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    assert "device_workers" in res.get_json()["settings"]


def test_retired_graph_settings_routes_are_absent():
    from api import web_chat_api as wca
    assert '/api/settings/sandbox' not in {r.rule for r in wca.app.url_map.iter_rules()}


def test_retired_bot_settings_route_is_gone():
    """The old RuntimeConfig GET/POST /api/settings family was retired."""
    from api import web_chat_api as wca
    from api.settings_routes import SETTING_FAMILIES

    rules = {str(r.rule) for r in wca.app.url_map.iter_rules()}
    assert "/api/settings" not in rules
    assert all(f["name"] != "bot" for f in SETTING_FAMILIES)
