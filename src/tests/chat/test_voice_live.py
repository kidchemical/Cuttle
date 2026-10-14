"""Live contracts use official-shaped events, private state and no paid calls."""
import base64
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
    native = service.config(settings.load(), [], native=True)
    assert native["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert "client" not in native  # WebSocket setup rejects client.data_channel.


def test_speaking_speed_is_validated_and_sent_only_when_non_default():
    """Reply rate is an API output setting, exposed as a preference."""
    base = settings.normalize({})
    assert base["speed"] == 1.0
    assert service.config(base, [])["audio"]["output"] == {"voice": base["voice"]}
    slow = service.config(settings.normalize({"speed": 0.8}), [])
    assert slow["audio"]["output"]["speed"] == 0.8
    assert settings.normalize({"speed": 0.75})["speed"] == 0.75
    with pytest.raises(ValueError):
        settings.normalize({"speed": 2.0})
    with pytest.raises(ValueError):
        settings.normalize({"speed": "fast"})
    with pytest.raises(ValueError):
        settings.normalize({"speed": True})


def test_spoken_progress_interval_is_validated_and_throttles(monkeypatch):
    from types import SimpleNamespace as NS
    from api.voice_live import delegation
    assert settings.normalize({})["progress_seconds"] == 0
    assert settings.normalize({"progress_seconds": 45})["progress_seconds"] == 45
    with pytest.raises(ValueError):
        settings.normalize({"progress_seconds": -1})
    with pytest.raises(ValueError):
        settings.normalize({"progress_seconds": 999})
    clock = {"t": 1000.0}
    monkeypatch.setattr(delegation, "time", NS(monotonic=lambda: clock["t"]))
    run = NS(prefs={"progress_seconds": 30}, progress_at=0.0)
    assert delegation.progress_due(run) is True          # first line always speaks
    assert delegation.progress_due(run) is False         # within the gap
    clock["t"] += 31
    assert delegation.progress_due(run) is True
    unthrottled = NS(prefs={"progress_seconds": 0}, progress_at=0.0)
    assert delegation.progress_due(unthrottled) and delegation.progress_due(unthrottled)


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


def test_voice_preview_synthesizes_without_opening_a_session(monkeypatch, _isolated_application_settings):
    """The settings preview is a TTS sample, never a billable Live session."""
    import api.tts_providers as tts
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    calls = []
    monkeypatch.setattr(tts, "synthesize",
                        lambda pid, text, **kw: (calls.append((pid, text, kw)), (b"MP3:preview", "audio/mpeg", {}))[1])
    app = Flask(__name__)
    app.register_blueprint(voice_live_bp, url_prefix="/live")
    client = app.test_client()
    flags.set_enabled("voice_live", True)
    try:
        ok = client.post("/live/preview", json={"voice": "marin", "speed": 0.9})
        assert ok.status_code == 200 and ok.json["success"]
        assert ok.json["content_type"] == "audio/mpeg"
        assert base64.b64decode(ok.json["audio_base64"]) == b"MP3:preview"
        assert calls[0][0] == "openai" and calls[0][2]["voice"] == "marin"
        bad = client.post("/live/preview", json={"voice": "not-a-live-voice"})
        assert bad.status_code == 400 and not bad.json["success"]
    finally:
        flags.set_enabled("voice_live", False)


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


def test_catalog_falls_back_to_the_enabled_engine(_isolated_application_settings):
    """Switching the engine flag must not strand a saved engine on plain TTS."""
    manager = _isolated_application_settings
    flags.set_enabled("voice_live", False)
    flags.set_enabled("voice_realtime", True)
    manager.set_setting("voice_chat", {**settings.load(), "engine": "live"})
    catalog = settings.catalog()
    assert catalog["engine"] == "realtime"
    assert catalog["realtime_enabled"] is True and catalog["live_enabled"] is False
    # An explicit TTS preference is never overridden by an enabled engine.
    manager.set_setting("voice_chat", {**settings.load(), "engine": "tts"})
    assert settings.catalog()["engine"] == "tts"
    # Re-enabling the saved engine's flag restores the saved choice.
    flags.set_enabled("voice_realtime", False)
    flags.set_enabled("voice_live", True)
    manager.set_setting("voice_chat", {**settings.load(), "engine": "live"})
    assert settings.catalog()["engine"] == "live"
    flags.set_enabled("voice_live", False)


def test_native_setup_error_reaches_client_and_closes_socket(run, monkeypatch):
    from api.voice_live import routes
    handlers = []
    class FakeSock:
        def route(self, *args, **kwargs):
            def register(handler):
                handlers.append(handler)
                return handler
            return register
    monkeypatch.setattr('flask_sock.Sock', FakeSock)
    monkeypatch.setattr('api.http_authz.require_owner', lambda: ({'id': run.user_id}, None))
    def unavailable(*args):
        raise TimeoutError('provider unavailable')
    monkeypatch.setattr(service, 'native_connection', unavailable)
    routes.register_socket()
    sent, closed = [], []
    socket = SimpleNamespace(send=sent.append, close=lambda: closed.append(True))
    with Flask(__name__).test_request_context():
        handlers[0](socket, run.id)
    assert closed == [True]
    error = json.loads(sent[0])
    assert error['type'] == 'cuttle.error'
    assert 'host network' in error['message']
    assert store.get(run.id)['state'] == 'disconnected'


def _voice_owner(**overrides):
    owner = SimpleNamespace(chat_id=42, user_id=7, prefix="", chat_request={},
                            token="private-token", id="voice-test")
    for key, value in overrides.items():
        setattr(owner, key, value)
    return owner


def test_run_request_spawns_a_subagent_chat_when_busy_and_not_steerable(monkeypatch):
    """GPT-Live latches to one agent; a second request runs in its own chat."""
    from api import chat_delivery
    from api.voice_live import delegation
    monkeypatch.setattr(delegation, "get_auth_db", lambda: SimpleNamespace(
        get_chat_session=lambda sid, uid: {"id": sid}, get_messages=lambda *a, **k: []))
    monkeypatch.setattr(chat_delivery, "is_busy", lambda sid: True)
    monkeypatch.setattr(chat_delivery, "current_turn", lambda sid: "turn-1")
    monkeypatch.setattr(delegation, "request_json", lambda token, path, data: (200, {"steered": False}))
    spawned = {}
    monkeypatch.setattr(delegation, "spawn_parallel_request",
                        lambda run, did, message: (spawned.__setitem__("message", message), {"response": "Done."})[1])
    monkeypatch.setattr(delegation, "queue_request", lambda *a: pytest.fail("queue must be bypassed"))
    assert delegation.run_request(_voice_owner(), "d1", "do the thing") == {"response": "Done."}
    assert spawned["message"] == "do the thing"


def test_run_request_falls_back_to_queue_when_no_subagent_can_start(monkeypatch):
    from api import chat_delivery
    from api.voice_live import delegation
    monkeypatch.setattr(delegation, "get_auth_db", lambda: SimpleNamespace(
        get_chat_session=lambda sid, uid: {"id": sid}, get_messages=lambda *a, **k: []))
    monkeypatch.setattr(chat_delivery, "is_busy", lambda sid: True)
    monkeypatch.setattr(chat_delivery, "current_turn", lambda sid: "turn-1")
    monkeypatch.setattr(delegation, "request_json", lambda token, path, data: (200, {"steered": False}))
    monkeypatch.setattr(delegation, "spawn_parallel_request", lambda *a: None)
    monkeypatch.setattr(delegation, "queue_request", lambda run, did, message: {"response": "queued"})
    assert delegation.run_request(_voice_owner(), "d1", "task") == {"response": "queued"}


def test_spawn_parallel_request_runs_child_in_its_own_chat_and_speaks_reply(monkeypatch):
    from api.subagents import service as subagents
    from api.voice_live import delegation
    captured = {}
    payload = {"children": [{"session_id": 99, "handle": "CH-000099", "status": "done"}], "timed_out": False}
    monkeypatch.setattr(subagents, "spawn", lambda **kwargs: captured.update(kwargs) or payload)
    monkeypatch.setattr(delegation, "_child_reply", lambda sid: "The file is updated.")
    appended = []
    owner = _voice_owner(prefix="/cursor ")
    owner.append = lambda kind, did, text: appended.append(text)
    assert delegation.spawn_parallel_request(owner, "d1", "/cursor fix the tests") == {
        "response": "The file is updated."}
    assert captured["parent_session_id"] == 42
    assert captured["collect"] == "first" and captured["lifetime"] == "one_shot"
    assert captured["children"][0]["agent"] == "cursor"
    assert "its own chat" in appended[0]


def test_spawn_parallel_request_reports_a_still_running_child(monkeypatch):
    from api.subagents import service as subagents
    from api.voice_live import delegation
    payload = {"children": [{"session_id": 99, "handle": "CH-000099", "status": "running"}], "timed_out": True}
    monkeypatch.setattr(subagents, "spawn", lambda **kwargs: payload)
    owner = _voice_owner()
    owner.append = lambda *a: None
    out = delegation.spawn_parallel_request(owner, "d1", "a long task")
    assert "still running" in out["response"] and "CH-000099" in out["response"]
