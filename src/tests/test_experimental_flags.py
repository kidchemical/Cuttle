"""Experimental feature flag surface: resolver precedence, routes, CLI.

Contract being pinned:
* an unregistered flag id resolves False (a typo can never light a feature),
* precedence is kill switch → stored value → spec default,
* the settings key stays free of redundant defaults,
* reads are authenticated, writes are owner-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import api.experimental.flags as flags
from api.experimental import FlagSpec, enabled_flags, is_enabled, set_enabled


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Isolate the stored-override read + the global kill switch per test.

    ``set_enabled`` is deliberately NOT stubbed — tests that need to assert on
    the persistence contract install their own fake settings manager.
    """
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    monkeypatch.setattr(flags, "_stored_flags", lambda: {})
    yield
    monkeypatch.undo()


@pytest.fixture()
def temp_flag():
    spec = FlagSpec("temp_flag", "T", "temp", default=False, category="ui")
    flags.register_flag(spec)
    yield spec
    flags.FLAG_SPECS.pop("temp_flag", None)


def test_unknown_flag_resolves_false():
    assert is_enabled("definitely-not-registered") is False


def test_spec_default_applies_when_unset(temp_flag):
    assert is_enabled("temp_flag") is False
    temp_flag.default = True
    assert is_enabled("temp_flag") is True


def test_kill_switch_beats_everything(monkeypatch, temp_flag):
    assert is_enabled("temp_flag") is False
    monkeypatch.setenv("CUTTLE_EXPERIMENTAL", "0")
    assert flags.kill_switch_active() is True
    assert is_enabled("temp_flag") is False


@pytest.mark.parametrize("raw,expected", [
    ("0", True), ("false", True), ("off", True), ("no", True),
    ("1", False), ("true", False), ("", False),
])
def test_kill_switch_values(monkeypatch, raw, expected):
    monkeypatch.setenv("CUTTLE_EXPERIMENTAL", raw)
    assert flags.kill_switch_active() is expected


def test_register_flag_rejects_duplicates(temp_flag):
    with pytest.raises(ValueError):
        flags.register_flag(FlagSpec("temp_flag", "again", "dup"))


def test_enabled_flags_covers_registry(temp_flag):
    payload = enabled_flags()
    assert payload["temp_flag"] is False
    assert set(payload) == set(flags.FLAG_SPECS)


def test_flags_payload_shape(temp_flag):
    payload = flags.flags_payload()
    row = next(f for f in payload["flags"] if f["id"] == "temp_flag")
    assert row["enabled"] is False
    assert row["label"] == "T"
    assert "kill_switch" in payload and payload["kill_switch"] is False


def test_achievements_flag_is_opt_in():
    """The shipped flag must default OFF (experimental = opt-in)."""
    assert flags.get_flag("achievements") is not None
    assert flags.get_flag("achievements").default is False


def test_set_enabled_drops_redundant_default(monkeypatch):
    """Writing the spec default must not leave a value in settings.json."""
    written = {}

    class _SM:
        def get_setting(self, key, default=None):
            return {}

        def set_setting(self, key, value):
            written[key] = value

    import managers.settings_manager as sm_mod

    monkeypatch.setattr(sm_mod, "get_settings_manager", lambda: _SM())
    monkeypatch.setattr(flags, "_stored_flags", lambda: {})
    spec = flags.get_flag("achievements")
    flags.set_enabled("achievements", spec.default)
    assert written.get("experimental_flags") == {}
    flags.set_enabled("achievements", not spec.default)
    assert written["experimental_flags"] == {"achievements": not spec.default}


def test_set_enabled_rejects_unknown_id():
    with pytest.raises(ValueError):
        flags.set_enabled("nope_not_real", True)


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------
def test_experimental_routes_registered():
    from api import web_chat_api as wca

    rules = {str(r.rule) for r in wca.app.url_map.iter_rules()}
    assert "/api/experimental/flags" in rules
    assert "/api/experimental/flags/<flag_id>" in rules
    assert "/api/experimental/flags/reset" in rules


def test_achievement_routes_registered():
    from api import web_chat_api as wca

    rules = {str(r.rule) for r in wca.app.url_map.iter_rules()}
    assert "/api/achievements" in rules
    assert "/api/achievements/pending" in rules
    assert "/api/achievements/<achievement_id>/ack" in rules
    assert "/api/achievements/scan" in rules


def test_flag_read_requires_auth():
    from api import web_chat_api as wca
    from tests.test_http_authz import LAN

    anon = wca.app.test_client()
    assert anon.get("/api/experimental/flags", environ_base=LAN).status_code == 401


def test_flag_write_requires_owner(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from tests.test_http_authz import LAN, _auth_client

    anon = wca.app.test_client()
    res = anon.post("/api/experimental/flags/achievements", json={"enabled": True},
                    environ_base=LAN)
    assert res.status_code == 401

    guest = wca.app.test_client()
    assert guest.post("/api/auth/guest", json={}).status_code == 200
    res = guest.post("/api/experimental/flags/achievements", json={"enabled": True},
                     environ_base=LAN)
    assert res.status_code == 403

    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    ctx["client"].set_cookie("session_token", ctx["token"])
    res = ctx["client"].post("/api/experimental/flags/achievements", json={"enabled": True},
                             environ_base=LAN)
    assert res.status_code == 200, res.get_data(as_text=True)
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["flag"]["id"] == "achievements"
    # leave the install as we found it
    ctx["client"].post("/api/experimental/flags/achievements", json={"enabled": False},
                       environ_base=LAN)


def test_flag_write_validation(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from tests.test_http_authz import LAN, _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    ctx["client"].set_cookie("session_token", ctx["token"])

    bad_flag = ctx["client"].post("/api/experimental/flags/not_a_flag",
                                  json={"enabled": True}, environ_base=LAN)
    assert bad_flag.status_code == 400

    bad_body = ctx["client"].post("/api/experimental/flags/achievements", json={},
                                  environ_base=LAN)
    assert bad_body.status_code == 400

    bad_type = ctx["client"].post("/api/experimental/flags/achievements",
                                  json={"enabled": "maybe"}, environ_base=LAN)
    assert bad_type.status_code == 400


def test_cli_list_emits_json(capsys):
    from api.experimental.__main__ import main

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["success"] is True
    assert any(f["id"] == "achievements" for f in payload["flags"])