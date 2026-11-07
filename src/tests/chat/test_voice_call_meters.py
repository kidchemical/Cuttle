"""Voice call meters (assistant audio levels) and the call-survives-the-reply fix.

- ``CuttleAudioLevel`` (src/web/js/shared/audio_level.js): pure level math and
  the Android bridge payload parser.
- Realtime / GPT-Live controllers expose ``levels()``; on Android they read the
  native ``voiceCallLevels`` bridge (PCM RMS of what the phone plays/records).
- The shell call strip shows a live meter and ``isActive()`` lets the reply
  chirp stay quiet during a call.
- The native call used to hang up on any audio-focus loss; the WebView's
  completion chirp is a transient (duck) loss, so every call ended the moment a
  delegated agent's reply landed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src/web"
LEVEL_JS = WEB / "js/shared/audio_level.js"
REALTIME_JS = WEB / "js/chat/chat_voice_realtime.js"
LIVE_JS = WEB / "js/chat/chat_voice_live.js"
SHELL_VOICE_JS = WEB / "js/shell/shell_voice.js"
TOAST_JS = WEB / "js/shared/toast.js"
NATIVE = REPO / "apps/mobile/android/app/src/main/java/com/cuttle/mobile"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(script: str, *args: Path) -> None:
    subprocess.run(["node", "-e", script, *map(str, args)], check=True, text=True, encoding="utf-8")


@node_only
def test_audio_level_helpers():
    _node(r"""
const assert = require('assert');
const L = require(process.argv[1]);
assert.strictEqual(L.rms(new Float32Array(8)), 0);
assert.ok(Math.abs(L.rms(Float32Array.from([0.5, -0.5, 0.5, -0.5])) - 0.5) < 1e-6);
assert.strictEqual(L.peakHold(0.1, 0.4), 0.4, 'attack is instant');
assert.ok(L.peakHold(0.4, 0) < 0.4 && L.peakHold(0.4, 0) > 0, 'release decays');
assert.strictEqual(L.display(0), 0);
assert.strictEqual(L.display(5), 1);
const quiet = L.bars(0, 5, 0), loud = L.bars(1, 5, 0);
assert.strictEqual(quiet.length, 5);
assert.ok(quiet.every((h) => h === 0.12), 'silent bars rest at the floor');
assert.ok(loud[2] > loud[0], 'centre bar tallest');
assert.deepStrictEqual(L.parseNative('{"out":0.25,"in":1.0E-4}'), { out: 0.25, in: 0.0001 });
assert.strictEqual(L.parseNative(''), null);
assert.strictEqual(L.parseNative('nope'), null);
assert.strictEqual(L.createStreamMeter({}, null), null);
assert.strictEqual(L.createStreamMeter({}, {}), null, 'no AudioContext -> null, never throws');
""", LEVEL_JS)


@node_only
def test_native_realtime_levels_read_the_bridge():
    _node(r"""
const assert = require('assert');
global.isSecureContext = false;
let payload = '{"out":0.3,"in":0.05}';
global.cuttleMobile = { realtimeVoiceStart: () => 'ok', realtimeVoiceStop() {}, realtimeVoiceMute() {},
  voiceCallLevels: () => payload };
global.addEventListener = () => {}; global.removeEventListener = () => {};
delete global.RTCPeerConnection;
Object.defineProperty(global, 'navigator', { value: {}, configurable: true });
require(process.argv[1]);
const R = require(process.argv[2]);
const voice = R.create({
  fetch: async (url) => url.includes('/sessions/')
    ? { ok: true, json: async () => ({ success: true, session: { state: 'active', transcripts: [] } }) }
    : { ok: true, json: async () => ({ success: true, connection_id: 'abc', model: 'm' }) },
  experimentalFlags: async () => new Set(['voice_realtime']),
  onStatus() {}, onError() {}, onTranscript() {}, onUsage() {}, delegate: async () => ({}),
});
(async () => {
  assert.strictEqual(voice.levels(), null, 'no call -> null');
  await voice.start({ session_id: '1' });
  assert.deepStrictEqual(voice.levels(), { out: 0.3, in: 0.05 });
  payload = '';
  assert.strictEqual(voice.levels(), null, 'native not running -> null');
  voice.stop();
  assert.strictEqual(voice.levels(), null);
})().catch((e) => { console.error(e); process.exit(1); });
""", LEVEL_JS, REALTIME_JS)


@node_only
def test_live_controller_exposes_levels():
    _node(r"""
const assert = require('assert');
global.window = undefined;
const Live = require(process.argv[1]);
const c = Live.create({ fetch: async () => ({}), onStatus() {}, onError() {}, onTranscript() {}, onUsage() {} });
assert.strictEqual(typeof c.levels, 'function');
assert.strictEqual(c.levels(), null, 'no call -> null');
""", LIVE_JS)


@node_only
def test_shell_strip_meter_and_is_active():
    _node(r"""
const assert = require('assert');
function el(tag){return {tag,hidden:false,className:'',style:{transform:''},children:[],attrs:{},
  setAttribute(k,v){this.attrs[k]=v;},append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);return c;}};}
const doc={head:el(),body:el(),createElement:(t)=>el(t),addEventListener(){},hidden:false,
  documentElement:{style:{setProperty(){}}}};
const frames=[];
const win={document:doc,isCuttleMobile:true,fetch:async()=>({ok:true,json:async()=>({success:true,flags:[]})}),
  addEventListener(){},dispatchEvent(){},Event:function(){},
  requestAnimationFrame(f){frames.push(f);return frames.length;},cancelAnimationFrame(){}};
let levels={out:0.3,in:0.01};
win.CuttleChatVoiceRealtime={create:()=>({start:async()=>{},stop:async()=>{},isOpen:()=>true,mute(){},levels:()=>levels})};
globalThis.window=win;
win.CuttleAudioLevel=require(process.argv[1]);
require(process.argv[2]);
const api=win.CuttleShellVoice.create({win});
(async()=>{
  assert.strictEqual(api.isActive(),false);
  await api.start({session_id:'9',engine:'realtime'});
  assert.strictEqual(api.isActive(),true,'call live -> chirp must stay quiet');
  assert.deepStrictEqual(api.levels('9'),{out:0.3,in:0.01});
  assert.strictEqual(api.levels('8'),null,'other chat -> no levels');
  const strip=doc.body.children[0];
  const meter=strip.children.find(c=>c.className==='shell-voice-meter');
  assert.ok(meter,'strip carries a meter');
  assert.ok(frames.length>0,'meter loop scheduled while a call is live');
  frames.shift()();
  assert.strictEqual(meter.hidden,false);
  assert.strictEqual(meter.attrs['data-source'],'assistant');
  assert.ok(meter.children.some(b=>/scaleY\(0\.[2-9]|scaleY\(1/.test(b.style.transform)),'bars follow the assistant level');
  levels={out:0,in:0.2};
  frames.shift()();
  assert.strictEqual(meter.attrs['data-source'],'you');
  await api.stop();
  assert.strictEqual(api.isActive(),false);
})().catch(e=>{console.error(e);process.exit(1);});
""", LEVEL_JS, SHELL_VOICE_JS)


def test_completion_chirp_is_silent_during_a_call():
    src = TOAST_JS.read_text(encoding="utf-8")
    body = src.split("async function playCuttleCompletionChirp()", 1)[1].split("\n    }\n", 1)[0]
    assert "voiceCallActive()" in body.split("window.parent")[0], (
        "the chirp must check for a live call before playing or forwarding to the shell"
    )


def test_native_call_survives_transient_audio_focus_loss():
    src = (NATIVE / "voice/LiveVoiceAudio.java").read_text(encoding="utf-8")
    listener = re.search(r"OnAudioFocusChangeListener focus = change -> \{(.*?)\};", src, re.S)
    assert listener, "focus listener not found"
    assert "change < 0" not in listener.group(1), "a transient/duck loss (reply chirp) must not hang up"
    assert "AUDIOFOCUS_LOSS)" in listener.group(1)
    assert "public String levels()" in src
    main = (NATIVE / "MainActivity.java").read_text(encoding="utf-8")
    assert "public String voiceCallLevels()" in main
    assert "voiceCallLevels:function()" in main, "bridge shim exposes the level getter to the page"
