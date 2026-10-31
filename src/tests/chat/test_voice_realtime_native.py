"""Native Realtime transport: server-owned WebSocket bridge, no provider calls."""
import base64
import json
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from api.experimental import flags
from api.voice_realtime import service
from api.voice_realtime.routes import voice_realtime_bp
from api.voice_live import store


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.incoming = queue.Queue()
        self.closed = False

    def send(self, event):
        self.sent.append(event)

    def close(self):
        self.closed = True
        self.incoming.put(None)

    def __iter__(self):
        while True:
            item = self.incoming.get()
            if item is None:
                return
            yield item


class FakeManager:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *args):
        self.connection.close()
        return False


class FakeSocket:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.connected = True

    def send(self, data):
        self.sent.append(json.loads(data))

    def receive(self, timeout=None):
        if self.script:
            return self.script.pop(0)
        if not self.connected:
            return None
        time.sleep(0.01)
        return None

    def close(self):
        self.connected = False


@contextmanager
def _fake_transport(connection):
    @contextmanager
    def fake_client():
        yield SimpleNamespace(realtime=SimpleNamespace(connect=lambda **kwargs: FakeManager(connection)))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "_realtime_client", fake_client)
        yield


@pytest.fixture
def isolated(tmp_path, monkeypatch, _isolated_application_settings):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    yield
    service._runs.clear()


def test_native_realtime_bridges_audio_transcripts_and_tool(isolated, monkeypatch):
    flags.set_enabled("voice_realtime", True)
    connection = FakeConnection()
    with _fake_transport(connection):
        delegated = []
        monkeypatch.setattr(service, "run_request",
                            lambda run, did, msg: (delegated.append((did, msg)), {"response": "done"})[1])
        recorded = []
        monkeypatch.setattr(service.voice_usage, "record_realtime_usage",
                            lambda model, responses, transcriptions, **kw:
                            (recorded.append((model, responses, transcriptions, kw)), {"cost": 0.02})[1])

        out = service.start_native(user_id=7, chat_id=42, token="tok", prefix="", chat_request={})
        voice_id = out["id"]
        assert out["native"] is True and out["model"]
        audio = base64.b64encode(b"\x00\x00" * 240).decode()
        socket = FakeSocket([json.dumps({"type": "session.input_audio.append", "audio": audio})])
        worker = threading.Thread(target=lambda: service.native_connection(voice_id, 7, socket), daemon=True)
        worker.start()
        connection.incoming.put({"type": "session.updated", "event_id": "e0", "session": {"id": "p1"}})
        _wait(lambda: any(e.get("type") == "input_audio_buffer.append" for e in connection.sent))
        connection.incoming.put({"type": "response.output_audio.delta", "event_id": "e1", "delta": "AQI="})
        connection.incoming.put({"type": "conversation.item.input_audio_transcription.completed",
            "event_id": "e2", "item_id": "i1", "transcript": "hello",
            "usage": {"input_tokens": 5, "output_tokens": 2}})
        connection.incoming.put({"type": "response.done", "event_id": "e3", "response": {"id": "r1",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "function_call", "call_id": "c1", "name": "ask_cuttle_agent",
                        "arguments": json.dumps({"message": "do it"})}]}})
        _wait(lambda: any(e.get("type") == "conversation.item.create" for e in connection.sent))
        socket.close()
        worker.join(timeout=5)

    kinds = [event["type"] for event in socket.sent]
    assert "session.started" in kinds
    assert "session.closed" in kinds
    assert "session.output_audio.delta" in kinds
    assert "session.input_transcript.delta" in kinds
    assert delegated == [("c1", "do it")]
    assert any(entry[1] and entry[1][0]["_usage_id"] for entry in recorded)   # response usage
    assert any(entry[2] and entry[2][0]["_usage_id"] for entry in recorded)   # transcription usage
    assert [e["type"] for e in connection.sent if e.get("type") == "response.create"] == ["response.create"]
    assert any(e.get("type") == "conversation.item.create" and e["item"]["type"] == "function_call_output"
               for e in connection.sent)


def test_native_realtime_barge_in_clears_phone_playback(isolated, monkeypatch):
    """Speech start cancels the reply upstream; buffered phone audio must be dropped too."""
    flags.set_enabled("voice_realtime", True)
    connection = FakeConnection()
    with _fake_transport(connection):
        out = service.start_native(user_id=7, chat_id=42, token="tok")
        socket = FakeSocket([])
        worker = threading.Thread(target=lambda: service.native_connection(out["id"], 7, socket), daemon=True)
        worker.start()
        connection.incoming.put({"type": "session.updated", "event_id": "u1", "session": {"id": "p1"}})
        connection.incoming.put({"type": "response.output_audio.delta", "event_id": "a1", "delta": "AQI="})
        connection.incoming.put({"type": "input_audio_buffer.speech_started", "event_id": "s1"})
        _wait(lambda: any(e["type"] == "session.output_audio.clear" for e in socket.sent))
        socket.close()
        worker.join(timeout=5)
    kinds = [e["type"] for e in socket.sent]
    assert kinds.index("session.output_audio.delta") < kinds.index("session.output_audio.clear")


def test_native_realtime_requires_the_flag(isolated, monkeypatch):
    flags.set_enabled("voice_realtime", False)
    with pytest.raises(PermissionError):
        service.start_native(user_id=7, chat_id=42, token="tok")


def test_native_realtime_reconnects_a_dropped_provider_without_ending_the_call(isolated, monkeypatch):
    """A long agent turn can outlive one provider WS; the phone call must survive."""
    flags.set_enabled("voice_realtime", True)
    connections = []

    def make():
        connection = FakeConnection()
        connections.append(connection)
        return connection

    @contextmanager
    def fake_client():
        yield SimpleNamespace(realtime=SimpleNamespace(connect=lambda **kwargs: FakeManager(make())))

    monkeypatch.setattr(service, "_realtime_client", fake_client)
    out = service.start_native(user_id=7, chat_id=42, token="tok")
    socket = FakeSocket([])
    worker = threading.Thread(target=lambda: service.native_connection(out["id"], 7, socket), daemon=True)
    worker.start()
    _wait(lambda: connections)
    connections[0].incoming.put({"type": "session.updated", "event_id": "u1", "session": {"id": "p1"}})
    _wait(lambda: any(e["type"] == "session.started" for e in socket.sent))
    # The provider socket ends mid-call: the Cuttle socket must stay open.
    connections[0].incoming.put(None)
    _wait(lambda: len(connections) >= 2)
    connections[1].incoming.put({"type": "session.updated", "event_id": "u2", "session": {"id": "p2"}})
    connections[1].incoming.put({"type": "response.output_audio.delta", "event_id": "a2", "delta": "AQ=="})
    _wait(lambda: any(e["type"] == "session.output_audio.delta" for e in socket.sent))
    assert not any(e["type"] == "session.closed" for e in socket.sent), "call must not end on a provider drop"
    socket.close()
    worker.join(timeout=5)


def test_native_realtime_drops_mic_audio_while_a_task_delegates(isolated, monkeypatch):
    """A long agent turn must not flood the busy model (backpressure closes the call)."""
    flags.set_enabled("voice_realtime", True)
    connection = FakeConnection()
    with _fake_transport(connection):
        out = service.start_native(user_id=7, chat_id=42, token="tok")
        run = service._runs[out["id"]]
        run.ready.set()
        run.delegating = 1
        audio = base64.b64encode(b"\x00\x00" * 240).decode()
        socket = FakeSocket([json.dumps({"type": "session.input_audio.append", "audio": audio})])
        worker = threading.Thread(target=lambda: service.native_connection(out["id"], 7, socket), daemon=True)
        worker.start()
        _wait(lambda: not socket.script)  # pump consumed the frame
        assert not any(e.get("type") == "input_audio_buffer.append" for e in connection.sent)
        socket.close()
        worker.join(timeout=5)


def test_native_session_route_gates_and_returns_connection(isolated, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(voice_realtime_bp, url_prefix="/api/voice-stt/realtime")
    client = app.test_client()
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    monkeypatch.setattr("api.voice_realtime.routes.current_user", lambda: {"id": 7})
    monkeypatch.setattr("api.voice_realtime.routes.require_chat_session_access", lambda sid: ({"id": 7}, 42, None))
    monkeypatch.setattr("api.auth_session.get_request_session_token", lambda: "tok")

    flags.set_enabled("voice_realtime", False)
    disabled = client.post("/api/voice-stt/realtime/session", json={"session_id": 42, "transport": "native"})
    assert disabled.status_code == 403 and disabled.json["disabled"]

    flags.set_enabled("voice_realtime", True)
    ok = client.post("/api/voice-stt/realtime/session",
                     json={"session_id": 42, "transport": "native", "prefix": "", "chat_request": {}})
    assert ok.status_code == 200 and ok.json["success"] and ok.json["native"] is True
    voice_id = ok.json["connection_id"]
    assert voice_id and store.get(voice_id, 7)["data"]["engine"] == "realtime"

    snapshot = client.get(f"/api/voice-stt/realtime/sessions/{voice_id}")
    assert snapshot.status_code == 200 and snapshot.json["session"]["native"] is True
    closed = client.post(f"/api/voice-stt/realtime/sessions/{voice_id}/close")
    assert closed.status_code == 200 and closed.json["session"]["state"] == "closed"


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_native_realtime_controller_uses_bridge_and_dedupes_events():
    js = Path(__file__).resolve().parents[2] / "web/js/chat/chat_voice_realtime.js"
    script = r"""
const assert = require('assert');
let bridgeStart = null, stops = 0; const mutes = [];
global.isSecureContext = false;
global.cuttleMobile = {
  realtimeVoiceStart(id) { bridgeStart = id; return 'ok'; },
  realtimeVoiceMute(on) { mutes.push(on); },
  realtimeVoiceStop() { stops++; },
};
const listeners = {};
global.addEventListener = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
global.removeEventListener = (name, fn) => { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); };
const emit = (name, detail) => { for (const fn of listeners[name] || []) fn({ detail: JSON.stringify(detail) }); };
delete global.RTCPeerConnection;
Object.defineProperty(global, 'navigator', { value: {}, configurable: true });
const R = require(process.argv[1]);
const transcripts = [], usages = [], errors = [], statuses = [];
let closed = false;
const voice = R.create({
  fetch: async (url) => {
    if (url.includes('/close')) { closed = true; return { ok: true, json: async () => ({ success: true }) }; }
    if (url.includes('/sessions/')) return { ok: true, json: async () => ({ success: true, session:
      { state: 'active', cost: 0.02, finalized: false, model: 'gpt-realtime-2.1', transcripts: [] } }) };
    return { ok: true, json: async () => ({ success: true, connection_id: 'abc123', model: 'gpt-realtime-2.1' }) };
  },
  experimentalFlags: async () => new Set(['voice_realtime']),
  onStatus: (s) => statuses.push(s),
  onError: (m) => errors.push(m),
  onTranscript: (role, text, partial) => transcripts.push([role, text, partial]),
  onUsage: (u) => usages.push(u),
  delegate: async () => ({}),
});
const tick = () => new Promise((r) => setTimeout(r, 15));
(async () => {
  await voice.start({ session_id: '42' });
  assert.strictEqual(bridgeStart, 'abc123');
  emit('cuttle-realtime-voice', { type: 'session.started', event_id: 's1' });
  emit('cuttle-realtime-voice', { type: 'session.input_transcript.delta', delta: 'hello', event_id: 't1' });
  emit('cuttle-realtime-voice', { type: 'session.output_transcript.delta', delta: 'world', event_id: 't2' });
  emit('cuttle-realtime-voice', { type: 'session.input_transcript.delta', delta: 'hello', event_id: 't1' });
  await tick();
  assert.deepStrictEqual(transcripts.map((t) => [t[0], t[1]]), [['user', 'hello'], ['assistant', 'world']]);
  assert.ok(transcripts.every((t) => t[2] === true), 'native transcripts stream as partials');
  assert.ok(usages.length >= 1 && usages[0].cost === 0.02, 'snapshot cost flows to onUsage');
  voice.mute(true); assert.deepStrictEqual(mutes, [true]);
  voice.stop();
  await tick();
  assert.strictEqual(stops, 1); assert.ok(closed); assert.ok(!voice.isOpen());
  assert.deepStrictEqual(errors, []);
})().catch((e) => { console.error(e); process.exitCode = 1; try { voice.stop(); } catch (_) {} });
"""
    proc = subprocess.run(["node", "-e", script, str(js)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
