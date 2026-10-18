"""Registry conflicts guard all writers, including old and concurrent settings."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from api.experimental import flags
from api.experimental.routes import experimental_bp


def test_voice_engines_conflict_but_standard_narration_is_compatible(_isolated_application_settings):
    flags.set_enabled("voice_server_stt", True)
    flags.set_enabled("voice_narrator", True)
    assert flags.is_enabled("voice_narrator")
    with pytest.raises(flags.FlagConflictError) as caught:
        flags.set_enabled("voice_live", True)
    assert set(caught.value.conflicts) == {"voice_server_stt", "voice_narrator"}
    flags.set_enabled("voice_server_stt", False)
    flags.set_enabled("voice_narrator", False)
    flags.set_enabled("voice_live", True)
    with pytest.raises(flags.FlagConflictError):
        flags.set_enabled("voice_realtime", True)
    with pytest.raises(flags.FlagConflictError):
        flags.set_enabled("voice_narrator", True)
    assert flags.is_enabled("voice_live")


def test_existing_conflicts_fail_closed_and_remain_repairable(_isolated_application_settings):
    from managers.settings_manager import get_settings_manager
    manager = get_settings_manager()
    manager.set_setting("experimental_flags", {"voice_live": True, "voice_realtime": True, "future_flag": True})
    assert not flags.is_enabled("voice_live")
    assert not flags.is_enabled("voice_realtime")
    rows = {row["id"]: row for row in flags.flags_payload()["flags"]}
    assert rows["voice_live"]["configured_enabled"]
    assert rows["voice_live"]["blocked_by"][0]["id"] == "voice_realtime"
    flags.set_enabled("voice_realtime", False)
    assert flags.is_enabled("voice_live")
    assert manager.get_setting("experimental_flags")["future_flag"] is True


def test_simultaneous_enable_is_atomic(_isolated_application_settings):
    def enable(key):
        try:
            flags.set_enabled(key, True)
            return True
        except flags.FlagConflictError:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(enable, ["voice_live", "voice_realtime"]))
    assert sum(outcomes) == 1
    assert sum(flags.enabled_flags()[key] for key in ("voice_live", "voice_realtime")) == 1


def test_registry_metadata_is_generic_and_symmetric(_isolated_application_settings):
    a = flags.FlagSpec("test_a", "A", "A", exclusive_group="example")
    b = flags.FlagSpec("test_b", "B", "B", exclusive_group="example")
    c = flags.FlagSpec("test_c", "C", "C", conflicts=("test_a",))
    for spec in (a, b, c):
        flags.register_flag(spec)
    try:
        assert flags.incompatible_flags("test_a") == ["test_b", "test_c"]
        flags.set_enabled("test_a", True)
        for key in ("test_b", "test_c"):
            with pytest.raises(flags.FlagConflictError):
                flags.set_enabled(key, True)
    finally:
        for spec in (a, b, c):
            flags.FLAG_SPECS.pop(spec.id)


def test_http_conflict_returns_canonical_state(_isolated_application_settings, monkeypatch):
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 1}, None))
    app = Flask(__name__)
    app.register_blueprint(experimental_bp)
    client = app.test_client()
    assert client.post("/api/experimental/flags/voice_live", json={"enabled": True}).status_code == 200
    response = client.post("/api/experimental/flags/voice_realtime", json={"enabled": True})
    assert response.status_code == 409
    assert response.json["conflicts"] == ["voice_live"]
    assert flags.is_enabled("voice_live") and not flags.is_enabled("voice_realtime")
