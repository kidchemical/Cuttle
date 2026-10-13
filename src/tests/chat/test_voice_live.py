"""Live contracts use official-shaped events, private state and no paid calls."""
import json
from types import SimpleNamespace

import pytest
from flask import Flask
from api.experimental import flags
from api.model_providers import usage
from api.voice_live import service, settings, store
from api.voice_live.routes import voice_live_bp


@pytest.fixture
def run(tmp_path, monkeypatch, _isolated_application_settings):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setenv("CUTTLE_PROVIDER_CALLS_DB", str(tmp_path / "calls.db"))
    store.create("voice-test", 7, 42, "request-test", {"model": "gpt-live-1", "voice": "marin"})
    value = service.Run("voice-test", 7, 42, "private-token", settings.load())
    value.connection = SimpleNamespace(send=lambda event: None)
    return value


def test_usage_checkpoints_replay_and_initialization_credit(run):
    run.meter(15, initializing=True)
    run.receive({"type": "session.usage.updated", "event_id": "usage1", "usage": {"seconds": 12}})
    run.receive({"type": "session.usage.updated", "event_id": "usage2", "usage": {"seconds": 90}})
    run.receive({"type": "session.usage.updated", "event_id": "usage1", "usage": {"seconds": 12}})
    run.receive({"type": "session.closed", "event_id": "close", "usage": {"seconds": 90}, "reason": "close_requested"})
    rows = usage.all_calls()
    assert len(rows) == 1
    assert rows[0]["cost"] == pytest.approx(.075)
    assert rows[0]["session_id"] == "42"
    assert json.loads(rows[0]["usage_json"])["finalized"] is True
    assert store.get(run.id)["data"]["seconds"] == 90
    run.receive({"type": "session.usage.updated", "event_id": "late", "usage": {"seconds": 80}})
    assert store.get(run.id)["data"]["finalized"] is True
    assert store.get(run.id)["data"]["seconds"] == 90


def test_final_usage_retries_failed_ledger_write(run, monkeypatch):
    real = usage.record_duration
    monkeypatch.setattr(usage, "record_duration", lambda **kwargs: False)
    run.meter(90, finalized=True)
    assert run.usage_pending == (90, True, False)
    monkeypatch.setattr(usage, "record_duration", real)
    run.meter(*run.usage_pending)
    assert run.usage_pending is None
    assert usage.all_calls()[0]["cost"] == pytest.approx(.075)


def test_delegation_claims_once_and_preserves_corrections(run, monkeypatch):
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    seen, sent = [], []
    monkeypatch.setattr(service, "run_request", lambda owner, did, text: seen.append((owner.chat_id, did, text)) or {"response": "The change is complete."})
    run.connection = SimpleNamespace(send=sent.append)
    run.receive({"type": "session.input_transcript.delta", "event_id": "a", "delta": "Change it to Tuesday. ", "start_ms": 0, "end_ms": 1000})
    run.receive({"type": "session.input_transcript.delta", "event_id": "b", "delta": "Actually Thursday.", "start_ms": 1000, "end_ms": 1500})
    run.delegate("delegation-1", 1500)
    run.delegate("delegation-1", 1500)
    assert len(seen) == 1
    assert seen[0][0] == 42 and "Actually Thursday" in seen[0][2]
    assert sent[0]["type"] == "session.commentary.append"
    assert sent[0]["delegation_id"] == "delegation-1"
    assert "private-token" not in store.get(run.id)["data"].values()


def test_speakable_result_chunks_respect_limit_and_strip_ui():
    from api.voice_live.delegation import speakable_chunks
    text = "The result is ready. " * 800 + "<cuttle_action_form>secret card</cuttle_action_form>"
    chunks = speakable_chunks(text)
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 400 for chunk in chunks)
    assert all(len(chunk.encode("utf-8")) <= 400 for chunk in speakable_chunks("漢字の結果。 " * 800))
    assert "secret card" not in " ".join(chunks)


def test_close_keeps_receiver_until_final_event(run):
    events = []
    run.connection = SimpleNamespace(send=events.append)
    run.close()
    assert not run.closed.is_set()
    assert events[0]["type"] == "session.close"
    run.receive({"type": "session.closed", "event_id": "closed", "usage": {"seconds": 10}, "reason": "close_requested"})
    assert run.closed.is_set() and store.get(run.id)["state"] == "closed"


def test_sideband_loss_hangs_up_without_a_presenter(run, monkeypatch):
    from contextlib import contextmanager
    calls = []
    run.provider_id = "provider-session"
    @contextmanager
    def disconnected(_):
        yield iter([])
    monkeypatch.setattr(service.provider, "attach", disconnected)
    monkeypatch.setattr(service.provider, "hangup", calls.append)
    service.observe(run)
    assert calls == ["provider-session"]
    assert store.get(run.id)["state"] == "disconnected"
    assert store.get(run.id)["data"]["finalized"] is False


def test_configuration_uses_client_delegation_and_limits_frontend_permissions():
    config = service.config(settings.load(), [])
    assert config["model"] == "gpt-live-1"
    assert config["delegation"] == {"type": "client"}
    assert config["store"] is False
    assert "session.commentary.append" not in config["client"]["data_channel"]["allowed_client_events"]
    assert "format" not in config["audio"]
    assert service.config(settings.load(), [], native=True)["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}


def test_start_gate_and_idempotent_native_setup(tmp_path, monkeypatch, _isolated_application_settings):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setattr(service.provider, "create_webrtc", lambda *_: pytest.fail("Paid call was attempted"))
    with pytest.raises(PermissionError):
        service.start(user_id=7, chat_id=42, token="private", request_id="req", sdp="sdp")
    flags.set_enabled("voice_live", True)
    try:
        first = service.start(user_id=7, chat_id=42, token="private", request_id="req", native=True)
        second = service.start(user_id=7, chat_id=42, token="private", request_id="req", native=True)
        assert first["id"] == second["id"]
        assert "token" not in str(store.get(first["id"]))
        with pytest.raises(ValueError):
            service.start(user_id=7, chat_id=43, token="private", request_id="req", native=True)
    finally:
        service._runs.clear()


def test_http_owner_chat_gate_and_settings_validation(run, monkeypatch):
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    monkeypatch.setattr("api.voice_live.routes.require_chat_session_access", lambda sid: ({"id": 7}, 42, None))
    app = Flask(__name__); app.register_blueprint(voice_live_bp, url_prefix="/live")
    client = app.test_client()
    response = client.post("/live/sessions", json={"session_id": 42, "request_id": "req", "sdp": "sdp"})
    assert response.status_code == 403 and response.json["disabled"]
    with pytest.raises(ValueError):
        settings.normalize({"voice": "not-a-live-voice"})
    with pytest.raises(ValueError):
        settings.normalize({"model": "gpt-live-1-mini"})
