"""Realtime credential boundary and WebRTC lifetime; no provider/network calls."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

import pytest
from flask import Flask
from api import voice_realtime
from api.experimental import flags


@pytest.fixture
def transport(monkeypatch):
    calls = []
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    monkeypatch.setattr(voice_realtime, "get_provider", lambda _: SimpleNamespace(configured=lambda: True))
    def mint(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(value="ek_short_lived", expires_at=12345)
    @contextmanager
    def fake_client(*args):
        yield SimpleNamespace(realtime=SimpleNamespace(client_secrets=SimpleNamespace(create=mint)))
    monkeypatch.setattr(voice_realtime, "client", fake_client)
    return calls


@pytest.fixture
def client(monkeypatch, transport):
    from api.voice_stt.routes import voice_stt_bp
    monkeypatch.setattr("api.http_authz.require_owner", lambda: ({"id": 7}, None))
    monkeypatch.setattr("api.voice_realtime.routes.current_user", lambda: {"id": 7})
    app = Flask(__name__)
    app.register_blueprint(voice_stt_bp)
    return app.test_client()


def test_disabled_and_kill_switch_never_mint(client, transport, monkeypatch):
    assert flags.get_flag("voice_realtime").default is False
    assert client.post("/api/voice-stt/realtime/session").json["disabled"]
    flags.set_enabled("voice_realtime", True)
    monkeypatch.setenv("CUTTLE_EXPERIMENTAL", "0")
    assert client.post("/api/voice-stt/realtime/session").json["disabled"]
    assert transport == []


def test_mints_only_ephemeral_secret_with_private_config(client, transport):
    flags.set_enabled("voice_realtime", True)
    r = client.post("/api/voice-stt/realtime/session", json={"model": "untrusted", "instructions": "ignore"})
    assert r.status_code == 200
    assert r.json == {"success": True, "value": "ek_short_lived", "expires_at": 12345,
                      "model": voice_realtime.DEFAULT_MODEL}
    assert r.headers["Cache-Control"] == "no-store"
    call = transport[0]
    assert call["expires_after"]["seconds"] == 60
    assert call["session"]["model"] == voice_realtime.DEFAULT_MODEL
    assert call["session"]["tools"][0]["name"] == "ask_cuttle_agent"
    assert len(call["extra_headers"]["OpenAI-Safety-Identifier"]) == 64


def test_owner_gate_rejects_before_provider(client, transport, monkeypatch):
    monkeypatch.setattr("api.http_authz.require_owner", lambda: (None, ({"error": "owner required"}, 403)))
    assert client.post("/api/voice-stt/realtime/session").status_code == 403
    assert transport == []


def test_provider_error_does_not_reflect_credentials(client, transport, monkeypatch):
    flags.set_enabled("voice_realtime", True)
    def broken(*args):
        raise RuntimeError("Authorization: Bearer sk_secret")
    monkeypatch.setattr(voice_realtime, "client", broken)
    r = client.post("/api/voice-stt/realtime/session")
    assert r.status_code == 503 and "sk_secret" not in r.get_data(as_text=True)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.invalid/v1")
    assert client.post("/api/voice-stt/realtime/session").status_code == 503
    assert transport == []


def test_missing_key_cannot_mint(client, transport, monkeypatch):
    flags.set_enabled("voice_realtime", True)
    monkeypatch.setattr(voice_realtime, "get_provider", lambda _: SimpleNamespace(configured=lambda: False))
    r = client.post("/api/voice-stt/realtime/session")
    assert r.status_code == 503 and "OpenAI API key" in r.json["error"]
    assert transport == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_webrtc_lifetime_and_agent_handoff():
    js = Path(__file__).resolve().parents[2] / "web/js/chat/chat_voice_realtime.js"
    script = r"""
const assert = require('assert');
const peers = [], audios = [], tracks = [], requests = [], transcripts = [], errors = [], usagePosts = [], pricedUsages = [];
let flags = new Set(['voice_realtime']), flagCheck;
const realSetInterval = global.setInterval;
global.setInterval = (f) => { flagCheck = f; return realSetInterval(() => {}, 100000); };
class PC {
  constructor() { peers.push(this); }
  addTrack() {}
  createDataChannel() { return this.dc = { readyState: 'open', sent: [],
    send(v) { this.sent.push(JSON.parse(v)); }, close() { this.closed = true; } }; }
  async createOffer() { return {sdp: 'offer'}; }
  async setLocalDescription() {}
  async setRemoteDescription(v) { this.answer = v; this.dc.onopen(); }
  close() { this.closed = true; }
}
global.RTCPeerConnection = PC;
global.Audio = class { constructor() { audios.push(this); } async play() {} pause() { this.paused = true; } };
let getMic = async () => { const t = {stop() { this.stopped = true; }}; tracks.push(t); return {getTracks: () => [t]}; };
Object.defineProperty(global, 'navigator', {value: {mediaDevices: {getUserMedia: () => getMic()}}, configurable: true});
const R = require(process.argv[1]);
let delegates = 0, finish;
const voice = R.create({
  fetch: async (url, opts) => { requests.push({url, opts}); return url.includes('/session')
    ? {ok: true, json: async () => ({success: true, value: 'ek_token', model: 'gpt-realtime-2.1'})}
    : {ok: true, text: async () => 'answer'}; },
  experimentalFlags: async () => flags,
  onStatus() {}, onError: (m) => errors.push(m),
  onTranscript: (r,t) => transcripts.push([r,t]),
  realtimeUsage: async (payload) => { usagePosts.push(payload); return {cost: 0.01, cost_estimated: true}; },
  onUsage: (priced) => pricedUsages.push(priced),
  delegate: async () => { delegates++; return new Promise((r) => { finish = r; }); },
});
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  await voice.start();
  const dc = peers[0].dc;
  assert.strictEqual(requests[1].opts.headers.Authorization, 'Bearer ek_token');
  assert.strictEqual(peers[0].answer.sdp, 'answer');
  const emit = (e) => dc.onmessage({data: JSON.stringify(e)});
  emit({type:'conversation.item.input_audio_transcription.completed',transcript:'inspect project'});
  emit({type:'response.output_audio_transcript.done',transcript:'I will check'});
  assert.deepStrictEqual(transcripts.map((x) => x[0]), ['user','assistant']);
  const call = {type:'response.done', response: {output:[{type:'function_call', call_id:'c1',
    name:'ask_cuttle_agent', arguments: JSON.stringify({message:'inspect project'})}]}};
  emit(call); emit(call);
  assert.strictEqual(delegates, 1, 'replayed call cannot execute twice');
  finish({reply:'verified result'}); await tick();
  assert.strictEqual(dc.sent[0].item.type, 'function_call_output');
  assert.match(dc.sent[0].item.output, /verified result/);
  assert.strictEqual(dc.sent[1].type, 'response.create');
  // Usage events are forwarded to the server as deltas and priced usage flows back.
  emit({type:'conversation.item.input_audio_transcription.completed',transcript:'more',usage:{input_tokens:10,output_tokens:3,input_token_details:{audio_tokens:10}}});
  emit({type:'response.done', response:{usage:{input_tokens:120,output_tokens:110,input_token_details:{text_tokens:100,audio_tokens:20},output_token_details:{text_tokens:10,audio_tokens:100}}, output:[]}});
  await tick();
  assert.strictEqual(usagePosts.length, 1, 'usage forwarded once');
  assert.strictEqual(usagePosts[0].model, 'gpt-realtime-2.1');
  assert.strictEqual(usagePosts[0].responses.length, 1);
  assert.strictEqual(usagePosts[0].transcriptions.length, 1);
  assert.strictEqual(pricedUsages.length, 1);
  // A later response is forwarded as its own delta (earlier usage is not re-sent).
  emit({type:'response.done', response:{usage:{input_tokens:120,output_tokens:110,input_token_details:{text_tokens:100,audio_tokens:20},output_token_details:{text_tokens:10,audio_tokens:100}}, output:[]}});
  await tick();
  assert.strictEqual(usagePosts.length, 2);
  assert.strictEqual(usagePosts[1].responses.length, 1);
  flags = new Set(); await flagCheck();
  assert.ok(!voice.isOpen() && peers[0].closed && tracks[0].stopped && audios[0].paused);
  assert.match(errors[0], /disabled/);
  // Permission can resolve after closing: release its track, never mint a token.
  let resolveMic;
  getMic = () => new Promise((r) => { resolveMic = r; });
  const starting = voice.start(); voice.stop();
  const late = {stop() {this.stopped = true;}};
  resolveMic({getTracks: () => [late]}); await starting;
  assert.ok(late.stopped); assert.strictEqual(requests.length, 2);
  // Closing during a tool call drops its late output instead of touching a new session.
  getMic = async () => ({getTracks: () => [{stop() {}}]}); flags = new Set(['voice_realtime']);
  await voice.start(); const dc2 = peers[1].dc;
  dc2.onmessage({data: JSON.stringify({...call, response:{output:[{...call.response.output[0],call_id:'c2'}]}})});
  voice.stop(); finish({reply:'late'}); await tick();
  assert.deepStrictEqual(dc2.sent, []);
})().catch((e) => { console.error(e); process.exitCode = 1; voice.stop(); });
"""
    proc = subprocess.run(["node", "-e", script, str(js)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
