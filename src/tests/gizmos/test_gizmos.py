"""Gizmos: usage normalization, store/service semantics, HTTP gating, agent CLI.

Layers are tested separately so a failure names the layer:

* usage    — vendor payloads normalize into one shape; unblock math; stale cache
* service  — placement validation, auto titles, ordering, revision bumps
* routes   — flag gate, owner-only auth, CRUD + data over HTTP
* cli      — ``python -m api.gizmos`` verbs write the same store the shell polls
"""

from __future__ import annotations

import json
import time

import pytest

from api.experimental import flags
from api.gizmos import __main__ as gizmos_cli
from api.gizmos import catalog, service, store, usage

NOW = time.time()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_GIZMOS_DB", str(tmp_path / "gizmos.db"))
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    usage.clear()
    yield
    usage.clear()


@pytest.fixture()
def enabled():
    flags.set_enabled("gizmos", True)
    yield


def _seed_codex(**rate):
    rl = {"primary_window": {"used_percent": 30, "reset_at": int(NOW) + 3600},
          "secondary_window": {"used_percent": 60, "reset_at": int(NOW) + 86400}}
    rl.update(rate)
    return usage.set_snapshot("codex", usage.normalize_codex(
        {"success": True, "plan_type": "plus", "rate_limit": rl}, NOW))


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------
def test_flag_is_registered_default_off():
    spec = flags.get_flag("gizmos")
    assert spec is not None and spec.default is False
    assert flags.is_enabled("gizmos") is False


def test_codex_normalizes_windows_and_remaining():
    snap = _seed_codex()
    assert [w["id"] for w in snap["windows"]] == ["five_hour", "weekly"]
    assert snap["windows"][0]["remaining_percent"] == 70.0
    assert snap["blocked"] is False
    assert snap["next_reset_at"] == int(NOW) + 3600
    assert snap["unblock_at"] is None


def test_codex_blocked_unblocks_when_every_exhausted_window_resets():
    snap = _seed_codex(
        primary_window={"used_percent": 100, "reset_at": int(NOW) + 600},
        secondary_window={"used_percent": 100, "reset_at": int(NOW) + 7200},
        limit_reached=True,
    )
    assert snap["blocked"] is True
    assert snap["unblock_at"] == int(NOW) + 7200


def test_codex_credits_overflow_is_not_blocked():
    body = usage.normalize_codex({"success": True, "rate_limit": {
        "primary_window": {"used_percent": 100, "reset_after_seconds": 60}, "limit_reached": True},
        "credits": {"unlimited": True}}, NOW)
    assert body["blocked"] is False
    assert body["extras"]["credits"] == "Unlimited"
    assert body["windows"][0]["reset_at"] == int(NOW + 60)


def test_claude_windows_and_extra_usage_overflow():
    data = {"success": True, "plan_type": "max", "windows": [
        {"label": "5-hour", "used_percent": 100, "reset_at": int(NOW) + 900},
        {"label": "Weekly Opus", "used_percent": 20, "reset_at": int(NOW) + 99999}]}
    blocked = usage.normalize_claude(data, NOW)
    assert blocked["blocked"] is True
    assert [w["id"] for w in blocked["windows"]] == ["5_hour", "weekly_opus"]
    data["extra_usage"] = {"is_enabled": True}
    assert usage.normalize_claude(data, NOW)["blocked"] is False


def test_cursor_is_never_fully_blocked():
    body = usage.normalize_cursor({"success": True,
                                   "period": {"billingCycleEnd": str(int(NOW + 86400) * 1000)},
                                   "plan_usage": {"totalPercentUsed": 40, "apiPercentUsed": 100}}, NOW)
    assert body["blocked"] is False
    assert {w["id"] for w in body["windows"]} == {"total", "api"}
    assert body["windows"][0]["reset_at"] == int(NOW + 86400)
    assert "bonus usage spent" in body["extras"]["note"]


def test_cursor_note_tracks_bonus_usage():
    pu = {"totalPercentUsed": 100, "autoPercentUsed": 100, "apiPercentUsed": 100,
          "bonusSpend": 44144, "remainingBonus": True}
    body = usage.normalize_cursor({"success": True, "plan_usage": pu}, NOW)
    assert body["extras"] == {"credits": "$441.44 bonus used",
                              "note": "Included usage spent; running on Cursor bonus usage"}
    pu["remainingBonus"] = False
    assert "may limit" in usage.normalize_cursor({"success": True, "plan_usage": pu}, NOW)["extras"]["note"]
    pu.update(totalPercentUsed=40, autoPercentUsed=40, apiPercentUsed=40, bonusSpend=0)
    assert usage.normalize_cursor({"success": True, "plan_usage": pu}, NOW)["extras"] == {}


def test_vendor_failure_is_an_error_not_an_exception():
    assert usage.normalize_codex({"success": False, "error": "login"}, NOW) == {"error": "login"}
    assert usage.normalize_claude(None, NOW)["error"]


def test_failed_refresh_keeps_last_good_windows(monkeypatch):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return usage.normalize_codex({"success": True, "rate_limit": {
                "primary_window": {"used_percent": 10, "reset_at": int(NOW) + 60}}}, NOW)
        raise RuntimeError("vendor down")

    monkeypatch.setitem(usage._PROVIDERS, "codex", usage.UsageProvider("codex", "Codex", fetch))
    first = usage.snapshot("codex")
    monkeypatch.setattr(usage, "TTL_S", 0)
    monkeypatch.setattr(usage, "FORCE_MIN_INTERVAL_S", 0)
    second = usage.snapshot("codex")
    assert calls["n"] == 2
    assert second["windows"] == first["windows"]
    assert second["stale"] is True and second["error"]


def test_force_refresh_is_rate_limited(monkeypatch):
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"windows": []}

    monkeypatch.setitem(usage._PROVIDERS, "codex", usage.UsageProvider("codex", "Codex", fetch))
    usage.snapshot("codex")
    usage.snapshot("codex", force=True)
    assert calls["n"] == 1


def test_register_provider_extends_usage_meter_agents(monkeypatch):
    monkeypatch.setattr(usage, "_PROVIDERS", dict(usage._PROVIDERS))
    usage.register_provider("muse", "Muse Code", lambda: {"windows": []})
    assert catalog.get("usage_meter").normalize_config({"agent": "muse"}, None)["agent"] == "muse"
    with pytest.raises(ValueError):
        usage.register_provider("Bad Id", "x", lambda: {})


# ---------------------------------------------------------------------------
# service + store
# ---------------------------------------------------------------------------
def test_create_defaults_and_auto_title():
    g = service.create("usage_meter", config={"agent": "claude"})
    assert g["title"] == "Claude Code usage"
    assert g["config"] == {"agent": "claude", "window": "tightest", "show": "remaining",
                           "notify_on_unblock": False}
    assert g["placement"] == {"dock": "titlebar", "order": 0.0}
    second = service.create("usage_meter", config={"agent": "codex"})
    assert second["placement"]["order"] == 1.0


def test_notify_on_unblock_arms_disarms_and_survives_other_updates():
    g = service.create("usage_meter", config={"agent": "codex"})
    assert g["config"]["notify_on_unblock"] is False
    armed = service.update(g["id"], config={"notify_on_unblock": True})
    assert armed["config"]["notify_on_unblock"] is True
    # Other config edits keep the armed flag; auto-title still follows.
    renamed = service.update(g["id"], config={"agent": "cursor"})
    assert renamed["config"] == {"agent": "cursor", "window": "tightest", "show": "remaining",
                                 "notify_on_unblock": True}
    assert renamed["title"] == "Cursor usage"
    off = service.update(g["id"], config={"notify_on_unblock": "off"})
    assert off["config"]["notify_on_unblock"] is False


def test_auto_title_follows_agent_but_custom_title_sticks():
    g = service.create("usage_meter", config={"agent": "codex"})
    assert service.update(g["id"], config={"agent": "cursor"})["title"] == "Cursor usage"
    service.update(g["id"], title="My budget")
    assert service.update(g["id"], config={"agent": "codex"})["title"] == "My budget"


def test_placement_validation_and_clamping():
    g = service.create("usage_meter", config={"agent": "codex"})
    moved = service.move(g["id"], "float", x=1.7, y=-2)
    assert moved["placement"]["dock"] == "float"
    assert (moved["placement"]["x"], moved["placement"]["y"]) == (1.0, 0.0)
    with pytest.raises(service.GizmoError):
        service.move(g["id"], "sidebar")
    with pytest.raises(service.GizmoError):
        service.update(g["id"], placement={"order": "nan"})


def test_redock_appends_to_end_of_target_dock():
    a = service.create("usage_meter", config={"agent": "codex"}, placement={"dock": "rail"})
    b = service.create("usage_meter", config={"agent": "claude"})
    assert service.move(b["id"], "rail")["placement"]["order"] == a["placement"]["order"] + 1


def test_invalid_inputs_are_rejected():
    with pytest.raises(service.GizmoError):
        service.create("sparkline")
    with pytest.raises(service.GizmoError):
        service.create("usage_meter", config={"agent": "nope"})
    with pytest.raises(service.GizmoError):
        service.create("usage_meter", config={"agent": "codex", "show": "both"})
    with pytest.raises(service.GizmoError):
        service.create("usage_meter", gizmo_id="Bad ID!")
    service.create("usage_meter", gizmo_id="mine")
    with pytest.raises(service.GizmoError) as exc:
        service.create("usage_meter", gizmo_id="mine")
    assert exc.value.code == "conflict"


def test_every_write_bumps_revision_including_delete():
    r0 = store.current_revision()
    g = service.create("usage_meter")
    r1 = store.current_revision()
    service.move(g["id"], "rail")
    r2 = store.current_revision()
    service.remove(g["id"])
    r3 = store.current_revision()
    assert r0 < r1 < r2 < r3
    with pytest.raises(service.GizmoError) as exc:
        service.remove(g["id"])
    assert exc.value.code == "not_found"
    assert store.current_revision() == r3


def test_gizmo_limit(monkeypatch):
    monkeypatch.setattr(service, "MAX_GIZMOS", 2)
    service.create("usage_meter")
    service.create("usage_meter")
    with pytest.raises(service.GizmoError):
        service.create("usage_meter")


def test_resolve_data_uses_the_configured_agent():
    _seed_codex()
    g = service.create("usage_meter", config={"agent": "codex"})
    data = service.resolve_data(g["id"])
    assert data["agent"] == "codex" and len(data["windows"]) == 2


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def _client(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx


def test_routes_report_disabled_when_flag_off(tmp_path, monkeypatch):
    ctx = _client(tmp_path, monkeypatch)
    body = ctx["client"].get("/api/gizmos").get_json()
    assert body == {"success": False, "disabled": True, "error": "Gizmos are disabled"}
    assert ctx["client"].post("/api/gizmos", json={"type": "usage_meter"}).get_json()["disabled"]
    assert store.count() == 0


def test_routes_require_owner(tmp_path, monkeypatch, enabled):
    ctx = _client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert ctx["client"].get("/api/gizmos").status_code == 403
    ctx["client"].delete_cookie("session_token")
    assert ctx["client"].get("/api/gizmos").status_code == 401


def test_routes_crud_and_data(tmp_path, monkeypatch, enabled):
    _seed_codex()
    client = _client(tmp_path, monkeypatch)["client"]
    created = client.post("/api/gizmos", json={
        "type": "usage_meter", "config": {"agent": "codex"}, "placement": {"dock": "rail"}})
    assert created.status_code == 201
    gid = created.get_json()["gizmo"]["id"]
    listed = client.get("/api/gizmos").get_json()
    assert listed["success"] and [g["id"] for g in listed["gizmos"]] == [gid]
    assert any(t["id"] == "usage_meter" for t in listed["types"])
    moved = client.patch(f"/api/gizmos/{gid}", json={"placement": {"dock": "float", "x": 0.5, "y": 0.2}})
    assert moved.get_json()["gizmo"]["placement"]["dock"] == "float"
    data = client.get(f"/api/gizmos/{gid}/data").get_json()
    assert data["success"] and data["data"]["agent"] == "codex"
    bad = client.patch(f"/api/gizmos/{gid}", json={"placement": {"dock": "nowhere"}})
    assert bad.status_code == 400
    assert client.patch(f"/api/gizmos/{gid}", json={"config": "codex"}).status_code == 400
    assert client.delete(f"/api/gizmos/{gid}").get_json()["success"]
    assert client.get(f"/api/gizmos/{gid}").status_code == 404


def test_pages_require_sign_in(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    assert ctx["client"].get("/gizmos_page.html").status_code == 401
    ctx["client"].set_cookie("session_token", ctx["token"])
    page = ctx["client"].get("/gizmos_page.html")
    assert page.status_code == 200 and b'id="gizmosCreateForm"' in page.data
    assert b'gizmo_popout.js' in ctx["client"].get("/gizmo_popout.html").data


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def _cli(capsys, *argv):
    code = gizmos_cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_refuses_when_flag_off(capsys):
    code, out = _cli(capsys, "list")
    assert code == 1 and out["disabled"] is True


def test_cli_create_move_update_remove(capsys, enabled):
    code, out = _cli(capsys, "create", "usage_meter", "--agent", "claude", "--dock", "rail", "--id", "claude-meter")
    assert code == 0 and out["gizmo"]["created_by"] == "agent"
    code, out = _cli(capsys, "move", "claude-meter", "--dock", "popout")
    assert out["gizmo"]["placement"]["dock"] == "popout"
    code, out = _cli(capsys, "update", "claude-meter", "--show", "used", "--title", "Claude left")
    assert out["gizmo"]["config"]["show"] == "used" and out["gizmo"]["title"] == "Claude left"
    code, out = _cli(capsys, "update", "claude-meter", "--notify-on-unblock")
    assert code == 0 and out["gizmo"]["config"]["notify_on_unblock"] is True
    code, out = _cli(capsys, "update", "claude-meter", "--no-notify-on-unblock")
    assert code == 0 and out["gizmo"]["config"]["notify_on_unblock"] is False
    code, out = _cli(capsys, "list")
    assert out["revision"] >= 3 and len(out["gizmos"]) == 1
    code, out = _cli(capsys, "remove", "claude-meter")
    assert code == 0
    code, out = _cli(capsys, "get", "claude-meter")
    assert code == 2 and out["code"] == "not_found"


def test_cli_usage_and_errors(capsys, enabled):
    _seed_codex()
    code, out = _cli(capsys, "usage")
    assert code == 0 and {p["agent"] for p in out["providers"]} >= {"codex", "claude", "cursor"}
    code, out = _cli(capsys, "usage", "codex")
    assert out["usage"]["windows"][0]["id"] == "five_hour"
    code, out = _cli(capsys, "usage", "nope")
    assert code == 2
    code, out = _cli(capsys, "create", "usage_meter", "--config", "[1]")
    assert code == 2 and "JSON object" in out["error"]
    code, out = _cli(capsys, "move", "x")
    assert code == 2
