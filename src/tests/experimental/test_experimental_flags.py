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
def _clean_registry(monkeypatch, _isolated_application_settings):
    """Use real private settings and isolate the global kill switch per test.

    ``set_enabled`` is deliberately NOT stubbed — tests that need to assert on
    the persistence contract install their own fake settings manager.
    """
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    yield


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

        def update_setting(self, key, transform):
            written[key] = transform(written.get(key))
            return True

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
    from tests.auth.test_http_authz import LAN

    anon = wca.app.test_client()
    assert anon.get("/api/experimental/flags", environ_base=LAN).status_code == 401


def test_flag_write_requires_owner(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from tests.auth.test_http_authz import LAN, _auth_client

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
    # The real route persisted to the private fixture, never the running install.
    from managers.settings_manager import get_settings_manager
    assert get_settings_manager().get_setting('experimental_flags')['achievements'] is True


def test_flag_write_validation(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from tests.auth.test_http_authz import LAN, _auth_client

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


def test_flag_updates_survive_restart_and_other_settings(_isolated_application_settings, monkeypatch):
    from managers.settings_manager import SettingsManager
    import managers.settings_manager as managers
    manager = _isolated_application_settings
    manager.set_setting('experimental_flags', {'newer_unknown_flag': True})
    flags.set_enabled('achievements', True)
    flags.set_enabled('progress_grid', True)
    manager.set_setting('starred_slash_commands', ['codex'])
    # New manager simulates Flask restarting, with no in-memory state carried over.
    restarted = SettingsManager(str(manager.settings_file))
    monkeypatch.setattr(managers, '_settings_manager', restarted)
    assert flags.is_enabled('achievements')
    assert flags.is_enabled('progress_grid')
    flags.set_enabled('achievements', False)
    stored = restarted.get_setting('experimental_flags')
    assert stored == {'progress_grid': True, 'newer_unknown_flag': True}
    assert not flags.is_enabled('newer_unknown_flag')


def test_concurrent_toggles_merge_under_storage_lock(_isolated_application_settings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    manager = _isolated_application_settings
    original = manager.update_setting
    import threading
    barrier = threading.Barrier(2)
    def synchronized_update(key, transform):
        barrier.wait(timeout=5)
        return original(key, transform)
    monkeypatch.setattr(manager, 'update_setting', synchronized_update)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: flags.set_enabled(name, True), ['achievements', 'progress_grid']))
    assert all(r['enabled'] for r in results)
    assert manager.get_setting('experimental_flags') == {'achievements': True, 'progress_grid': True}


def test_live_settings_write_is_rejected():
    from managers.settings_manager import SettingsManager
    with pytest.raises(RuntimeError, match='live application settings'):
        SettingsManager().set_setting('experimental_flags', {})
