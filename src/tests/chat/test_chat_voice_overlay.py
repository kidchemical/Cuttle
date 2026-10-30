"""Voice mode: owned by chat_voice*.js, not the chat_page.js composition root.

Contracts pinned here:
- a tap starts listening and only a second tap sends; recognizer pauses
  restart listening instead of auto-sending (the old silence timer cut users off);
- each recognized phrase is its own removable bubble; × drops it from the send;
- cumulative STT re-emissions grow one bubble instead of duplicating text;
- a sent voice turn still speaks its reply after the overlay closed mid-run;
- the overlay offers "Watch agent work" while the agent runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat"
SEGMENTS_JS = CHAT_JS / "chat_voice_segments.js"
STARS_JS = CHAT_JS / "chat_voice_stars.js"
NARRATOR_JS = CHAT_JS / "chat_voice_narrator.js"
RECORDER_JS = CHAT_JS / "chat_voice_recorder.js"
ACTIVITY_JS = CHAT_JS / "chat_voice_activity.js"
VOICE_JS = CHAT_JS / "chat_voice.js"
PAGE_JS = CHAT_JS / "chat_page.js"
CHAT_HTML = WEB / "chat_page.html"
VOICE_CSS = WEB / "css" / "chat_voice.css"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _fn_body(src: str, name: str) -> str:
    start = src.find(name)
    assert start >= 0, f"missing {name!r}"
    paren = src.find("(", start + len(name))
    assert paren >= 0
    depth = 0
    i = paren
    while i < len(src):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise AssertionError(f"unbalanced parens for {name!r}")
    brace = src.find("{", i)
    assert brace >= 0
    depth = 0
    j = brace
    while j < len(src):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[brace + 1 : j]
        j += 1
    raise AssertionError(f"unbalanced braces for {name!r}")


def _node(script: str) -> None:
    proc = subprocess.run(
        ["node", "-e", script, str(SEGMENTS_JS), str(STARS_JS), str(NARRATOR_JS), str(VOICE_JS), str(RECORDER_JS), str(ACTIVITY_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@node_only
def test_segments_commit_remove_and_join():
    _node(r"""
const assert = require('assert');
const S = require(process.argv[1]);
const r = (t, f) => ({ isFinal: f, 0: { transcript: t } });
const st = S.createState();
S.beginSession(st);
S.applyResults(st, [r('hello there', true), r('and', false)]);
assert.deepStrictEqual(st.segments.map((s) => s.text), ['hello there']);
assert.strictEqual(st.live, 'and');
S.applyResults(st, [r('hello there', true), r('and more', true)]);
assert.deepStrictEqual(st.segments.map((s) => s.text), ['hello there', 'and more']);
assert.strictEqual(st.live, '');
S.endSession(st);
S.beginSession(st);
S.applyResults(st, [r('unfinished words', false)]);
S.endSession(st);
assert.strictEqual(S.utterance(st), 'hello there and more unfinished words');
assert.ok(S.remove(st, st.segments[1].id));
assert.strictEqual(S.utterance(st), 'hello there unfinished words');
S.clear(st);
assert.ok(S.isEmpty(st));
""")


@node_only
def test_segments_cumulative_finals_grow_one_bubble():
    _node(r"""
const assert = require('assert');
const S = require(process.argv[1]);
const r = (t) => ({ isFinal: true, 0: { transcript: t } });
const st = S.createState();
S.beginSession(st);
S.applyResults(st, [r('hello'), r('hello how'), r('hello how are you')]);
assert.deepStrictEqual(st.segments.map((s) => s.text), ['hello how are you']);
S.endSession(st);
S.beginSession(st);
S.applyResults(st, [r('hello how')]);
assert.deepStrictEqual(st.segments.map((s) => s.text), ['hello how are you']);
""")


@node_only
def test_segments_split_sentences_and_merge_reemitted_overlap():
    """Phrase bubbles follow the spoken content: one per sentence, re-emitted
    words are joined once instead of duplicated into separate bubbles."""
    _node(r"""
const assert = require('assert');
const S = require(process.argv[1]);
const r = (t, f) => ({ isFinal: f, 0: { transcript: t } });
assert.deepStrictEqual(S.splitSentences('Book a flight. Then a hotel.'),
  ['Book a flight.', 'Then a hotel.']);
assert.deepStrictEqual(S.splitSentences('3.14 is pi'), ['3.14 is pi']);
const st = S.createState();
S.beginSession(st);
// One recognizer final carrying two sentences becomes two removable bubbles.
S.applyResults(st, [r('Book a flight. Then a hotel.', true)]);
assert.deepStrictEqual(st.segments.map((s) => s.text), ['Book a flight.', 'Then a hotel.']);
// A re-emitted phrase that repeats the tail joins once ("how" is not duplicated).
S.commit(st, 'hello how');
S.commit(st, 'how are you');
assert.deepStrictEqual(st.segments.map((s) => s.text),
  ['Book a flight.', 'Then a hotel.', 'hello how are you']);
S.endSession(st);
""")


CONTROLLER_HARNESS = r"""
const assert = require('assert');
class CL {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  toggle(c, on) { if (on) this.s.add(c); else this.s.delete(c); }
  contains(c) { return this.s.has(c); }
}
class El {
  constructor(id) { this.id = id; this.children = []; this.classList = new CL(); this.attrs = {};
    this.dataset = {}; this.hidden = false; this._t = ''; this.listeners = {}; this.style = {};
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; }
  set className(v) { this.classList = new CL(); String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classList.add(c)); }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(''); }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  remove() { const p = this.parentNode; if (p) { const i = p.children.indexOf(this); if (i >= 0) p.children.splice(i, 1); } this.parentNode = null; }
  scrollTo(opts) { if (opts && typeof opts === 'object' && 'top' in opts) this.scrollTop = opts.top; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener() {}
  dispatch(t, ev) { (this.listeners[t] || []).forEach((f) => f(ev)); }
  closest(sel) { let n = this; while (n) { if (n.classList.contains(sel.slice(1))) return n; n = n.parentNode; } return null; }
}
const els = {};
globalThis.document = {
  getElementById: (id) => (id === 'voiceModeStars' ? null : (els[id] = els[id] || new El(id))),
  createElement: () => new El(),
  body: new El('body'),
  hidden: false,
  listeners: {},
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
  removeEventListener() {},
  dispatch(t) { (this.listeners[t] || []).forEach((f) => f({})); },
};
globalThis.addEventListener = () => {};
const recs = [];
globalThis.SpeechRecognition = class { constructor() { recs.push(this); } start() { this.started = true; } stop() { this.stopped = true; } };
require(process.argv[1]);
require(process.argv[2]);
const N = require(process.argv[3]);
const R = require(process.argv[5]);
require(process.argv[6]);
const V = require(process.argv[4]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const res = (t, f) => ({ isFinal: f, 0: { transcript: t } });
const ev = (target) => ({ target, preventDefault() {}, stopPropagation() {} });
const submitted = [];
let played = 0;
let stops = 0;
let flagsOn = [];
const clips = [];
const narrateCalls = [];
let narrateReply = () => ({ success: true, text: null });
globalThis.URL.createObjectURL = () => 'blob:narration';
const assistant = { dataset: { messageId: '42' } };
const host = {
  closeMenus() {}, isGenerating: () => false, compose: (s) => s, isSendable: (m) => !!m,
  isControlLane: () => false, steer: async () => false, enqueue() {},
  submit: async (m) => { submitted.push(m); },
  lastAssistantMessage: () => assistant,
  speechFor: async () => ({ url: 'blob:x', spoken: 'reply' }),
  play: async () => { played += 1; return 'ended'; },
  stopSpeech() { stops += 1; }, toast() {}, logError() {},
  playClip: async (url) => { clips.push(url); return 'ended'; },
  experimentalFlags: async () => new Set(flagsOn),
  fetch: async (url, opts) => { const body = JSON.parse(opts.body); narrateCalls.push(body);
    return { ok: true, json: async () => (narrateReply(body)) }; },
};
const voice = V.create(host);
const mic = document.getElementById('voiceModeMicBtn');
const box = document.getElementById('voiceModeSegments');
"""


@node_only
def test_tap_listens_through_pauses_and_sends_only_on_second_tap():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 1);
  assert.ok(recs[0].started);
  recs[0].onresult({ results: [res('hello there', true)] });
  assert.strictEqual(box.children.length, 1);
  assert.strictEqual(box.hidden, false);
  // Browser ends the session on a pause: keep listening, never send.
  recs[0].onend();
  await sleep(250);
  assert.strictEqual(recs.length, 2, 'recognition restarts after a pause');
  assert.deepStrictEqual(submitted, []);
  recs[1].onresult({ results: [res('scratch that', true), res('send this', false)] });
  assert.strictEqual(box.children.length, 3);
  assert.ok(box.children[2].classList.contains('is-live'));
  // × on the middle phrase removes it from what gets sent.
  const removeBtn = box.children[1].children[1];
  assert.ok(removeBtn.classList.contains('voice-mode-segment-remove'));
  box.dispatch('click', ev(removeBtn));
  assert.strictEqual(box.children.length, 2);
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(submitted, ['hello there send this']);
  assert.ok(recs[1].stopped);
  assert.strictEqual(box.hidden, true);
  assert.strictEqual(narrateCalls.length, 0, 'narrator flag off → no narration requests');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_unavailable_speech_service_stops_instead_of_looping():
    # Electron/Chromium expose SpeechRecognition without Google's backend:
    # every session fails with `network`. That must stop, not restart forever.
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const toasts = [];
  host.toast = (msg) => toasts.push(msg);
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 1);
  recs[0].onerror({ error: 'network' });
  recs[0].onend && recs[0].onend();
  await sleep(250);
  assert.strictEqual(recs.length, 1, 'no restart after a fatal recognizer error');
  assert.ok(toasts.some((t) => /server transcription/.test(t)), toasts.join('|'));
  // A fresh tap tries again.
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 2);
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_instantly_ending_recognizer_gives_up_but_silence_keeps_listening():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  // Sessions that die immediately with no words: capped.
  for (let i = 0; i < 10; i += 1) {
    const rec = recs[recs.length - 1];
    rec.onend();
    await sleep(150);
  }
  assert.strictEqual(recs.length, 6, 'stops after 6 instant empty sessions');
  // Fresh tap; a silent user's sessions last seconds and never hit the cap.
  mic.dispatch('click', ev(mic));
  await sleep(5);
  const realNow = Date.now;
  let t = realNow();
  Date.now = () => t;
  try {
    for (let i = 0; i < 10; i += 1) {
      const rec = recs[recs.length - 1];
      t += 8000;
      rec.onerror({ error: 'no-speech' });
      rec.onend();
      await sleep(150);
    }
  } finally { Date.now = realNow; }
  assert.strictEqual(recs.length, 6 + 1 + 10, 'silence keeps listening');
  assert.deepStrictEqual(submitted, []);
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_pending_reply_still_speaks_after_leaving_overlay_mid_run():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  recs[0].onresult({ results: [res('do the thing', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(submitted, ['do the thing']);
  voice.exit();
  assert.strictEqual(voice.isActive(), false);
  voice.onGenerationEnded({ isError: false });
  await sleep(300);
  assert.strictEqual(played, 1, 'reply speaks even though the overlay closed');
  voice.onGenerationEnded({ isError: false });
  await sleep(300);
  assert.strictEqual(played, 1, 'no pending reply → nothing more to speak');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_agent_activity_bubble_matches_chat_style_and_updates_in_place():
    """While the agent runs, the voice transcript shows the same chat-style
    processing bubble as the regular chat view (orbit + wave + sweep status),
    carrying tool activity instead of a bare center caption."""
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const A = require(process.argv[6]);
  assert.ok(A && A.orbitHtml().indexOf('typing-orbit') >= 0, 'shared orbit markup');
  assert.ok(A.orbitHtml().indexOf('typing-wave') >= 0, 'shared wave markup');
  const transcript = document.getElementById('voiceModeTranscript');
  const center = document.getElementById('voiceModeStatus');
  const count = () => transcript.children.filter((c) => c.classList.contains('is-activity')).length;

  voice.enter();
  voice.onGenerationStarted();
  assert.strictEqual(count(), 1, 'one activity bubble while the agent runs');
  const bubble = transcript.children[0];
  assert.ok(bubble.classList.contains('voice-mode-line'));
  const indicator = bubble.children[0];
  const status = bubble.children[1];
  assert.ok(indicator.classList.contains('typing-indicator'));
  assert.ok(indicator.innerHTML.indexOf('typing-orbit') >= 0);
  assert.ok(status.classList.contains('typing-status'));
  assert.strictEqual(status.textContent, 'Thinking…');

  // Tool activity lands in the bubble, never as a bare center caption.
  voice.onAgentStatus('Reading src/api/app.py');
  assert.strictEqual(status.textContent, 'Reading src/api/app.py');
  assert.strictEqual(center.textContent, 'Thinking…');
  voice.onAgentStatus('Running tests');
  assert.strictEqual(count(), 1, 'status updates never duplicate the bubble');
  assert.strictEqual(status.textContent, 'Running tests');

  // The reply supersedes the bubble.
  voice.onGenerationEnded({ isError: false });
  await sleep(250);
  assert.strictEqual(count(), 0, 'activity bubble clears when the turn ends');

  // Leaving voice view clears it too.
  voice.enter();
  voice.onGenerationStarted();
  assert.strictEqual(count(), 1);
  voice.exit();
  assert.strictEqual(count(), 0, 'leaving voice view clears the activity bubble');
})().catch((e) => { console.error(e); process.exit(1); });
""")


def test_voice_activity_owner_and_styles_are_wired():
    activity = ACTIVITY_JS.read_text(encoding="utf-8")
    for cls in ("typing-orbit", "typing-orbit-ring", "typing-orbit-sat", "typing-wave"):
        assert cls in activity
    voice = VOICE_JS.read_text(encoding="utf-8")
    assert "'typing-status'" in voice, "activity status line built by the overlay owner"
    css = VOICE_CSS.read_text(encoding="utf-8")
    assert ".voice-mode-line.is-activity" in css
    assert ".voice-mode-line.is-activity .typing-status" in css
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert "chat_voice_activity.js" in html
    assert html.find("chat_voice_activity.js") < html.find("/js/chat/chat_voice.js")


@node_only
def test_narrator_pacing():
    _node(r"""
const assert = require('assert');
const N = require(process.argv[3]);
const p = N.createPlan();
assert.strictEqual(N.noteStatus(p, 'Editing a.py'), false, 'no open turn');
const t = N.beginTurn(p, '/cursor x', 0);
assert.strictEqual(N.dueIn(p, 0), N.FIRST_PROGRESS_MS - N.MIN_GAP_MS + N.HEARTBEAT_MS,
  'quiet turn still gets a heartbeat');
assert.ok(!N.noteStatus(p, 'Connecting...'));
assert.ok(!N.noteStatus(p, 'Thinking…'));
assert.ok(N.noteStatus(p, 'Editing a.py'));
assert.ok(!N.noteStatus(p, 'Editing a.py'), 'repeat status is not progress');
assert.strictEqual(N.dueIn(p, 0), N.FIRST_PROGRESS_MS);
assert.strictEqual(N.takeProgress(p, 100), null);
const pay = N.takeProgress(p, N.FIRST_PROGRESS_MS);
assert.deepStrictEqual(pay.events, ['Editing a.py']);
assert.strictEqual(pay.kind, 'progress');
assert.strictEqual(N.dueIn(p, 1e9), null, 'in flight');
N.finishRequest(p);
N.noteSaid(p, 'Editing a.', 8000);
N.noteStatus(p, 'Running tests');
assert.strictEqual(N.dueIn(p, 8000), N.MIN_GAP_MS);
N.setSpeaking(p, true);
assert.strictEqual(N.dueIn(p, 1e9), null, 'never stack over a playing clip');
N.setSpeaking(p, false);
N.endTurn(p);
assert.ok(!N.isCurrent(p, t));
assert.strictEqual(N.dueIn(p, 1e9), null);
""")


@node_only
def test_vad_cuts_phrases_at_pauses_and_ignores_blips():
    _node(r"""
const assert = require('assert');
const R = require(process.argv[5]);
const v = R.createVad();
const f = R.VAD_DEFAULTS.frameMs;
const run = (rms, ms) => { const out = []; for (let t = 0; t < ms; t += f) { const e = R.vadStep(v, rms); if (e) out.push(e); } return out; };
assert.deepStrictEqual(run(0.001, 500), []);
assert.deepStrictEqual(run(0.2, 100), [], 'a 100 ms click is not speech');
assert.deepStrictEqual(run(0.001, 300), []);
assert.deepStrictEqual(run(0.2, 600), ['speech_start']);
assert.deepStrictEqual(run(0.001, 500), [], 'short pause keeps the phrase open');
assert.deepStrictEqual(run(0.2, 300), []);
assert.deepStrictEqual(run(0.001, R.VAD_DEFAULTS.endSilenceMs), ['phrase_end']);
assert.deepStrictEqual(run(0.001, R.VAD_DEFAULTS.maxIdleMs), ['idle_rotate']);
assert.ok(Math.abs(R.rmsOf(new Float32Array([0.5, -0.5])) - 0.5) < 1e-9);
""")


@node_only
def test_native_app_mic_records_over_http_without_browser_apis():
    _node(r"""
const assert = require('assert');
globalThis.isSecureContext = false;
const calls = [];
let queued = [];
const wav = Buffer.alloc(44 + 32000);  // 1 s of 16 kHz mono 16-bit
globalThis.parent = { cuttleMobile: {
  micStart: () => { calls.push('start'); return 'ok'; },
  micLevels: () => { const out = queued.join(','); queued = []; return out; },
  micCut: (keep) => { calls.push('cut:' + keep); return keep ? wav.toString('base64') : ''; },
  micStop: () => { calls.push('stop'); },
} };
const R = require(process.argv[5]);
assert.deepStrictEqual(R.missingSupport(), [], 'app mic needs no secure page');
assert.strictEqual(R.sourceKind(), 'native');
const uploads = [], texts = [], speech = [];
const rec = R.create({
  fetch: async (url, opts) => {
    const audio = opts.body.get('audio');
    uploads.push({ url, type: audio.type, name: audio.name, size: audio.size, ms: opts.body.get('duration_ms') });
    return { ok: true, json: async () => ({ success: true, text: 'hello there' }) };
  },
  onSpeech: (on) => speech.push(on), onPhraseText: (t) => texts.push(t),
  onPending() {}, onError: (m) => { throw new Error(m); },
});
(async () => {
  await rec.start();
  queued = Array(6).fill(0.2).concat(Array(20).fill(0.001));
  await new Promise((r) => setTimeout(r, 120));
  await rec.stop({ flush: true });
  assert.deepStrictEqual(speech.slice(0, 2), [true, false]);
  assert.deepStrictEqual(texts, ['hello there']);
  assert.strictEqual(uploads.length, 1, 'silence after the phrase is not uploaded');
  assert.deepStrictEqual(uploads[0], { url: '/api/voice-stt/transcribe', type: 'audio/wav',
    name: 'phrase.wav', size: wav.length, ms: '1000' });
  assert.deepStrictEqual(calls, ['start', 'cut:true', 'cut:false', 'stop']);

  globalThis.parent.cuttleMobile.micStart = () => 'denied';
  await assert.rejects(rec.start(), (e) => e.name === 'NotAllowedError');
  globalThis.parent.cuttleMobile.micStart = () => 'error:microphone busy';
  await assert.rejects(rec.start(), (e) => e.name === 'NativeMicError' && /microphone busy/.test(e.message));
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_screen_lock_keeps_phrases_and_retap_appends():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  recs[0].onresult({ results: [res('keep me', true), res('and th', false)] });
  document.hidden = true;
  document.dispatch('visibilitychange');
  await sleep(5);
  assert.ok(recs[0].stopped, 'mic stops when the screen locks');
  assert.strictEqual(box.children.length, 2, 'phrases kept, interim committed');
  assert.match(document.getElementById('voiceModeStatus').textContent, /phrases are kept/);
  document.hidden = false;
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 2);
  assert.strictEqual(box.children.length, 2, 're-tapping the mic does not wipe phrases');
  recs[1].onresult({ results: [res('and more', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(submitted, ['keep me and th and more']);
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_server_engine_records_without_speech_recognizer():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  let h = null, starts = 0, flushes = 0, tail = '';
  R.missingSupport = () => [];
  R.create = (hostArg) => { h = hostArg; return {
    start: async () => { starts += 1; },
    stop: async (o) => { if (o && o.flush) { flushes += 1; if (tail) h.onPhraseText(tail); } },
    resetContext() {}, isRunning: () => false, pending: () => 0,
  }; };
  flagsOn = ['voice_server_stt'];
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(starts, 1);
  assert.strictEqual(recs.length, 0, 'no browser speech recognizer → no Android chime');
  h.onSpeech(true);
  assert.strictEqual(box.children[box.children.length - 1].textContent, 'Listening…');
  h.onPhraseText('first part');
  h.onSpeech(false);
  h.onPending(1);
  assert.strictEqual(box.children[box.children.length - 1].textContent, 'Transcribing…');
  h.onPending(0);
  tail = 'second part';
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(flushes, 1, 'send flushes the phrase in progress');
  assert.deepStrictEqual(submitted, ['first part second part']);
  assert.match(document.getElementById('voiceModeHint').textContent, /Server transcription$/);
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_server_engine_without_openai_key_falls_back_to_browser_speech():
    # Server transcription is default on, but a fresh install has no key: use
    # browser speech (Chrome/Edge), and name the key where that is unavailable.
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  let starts = 0;
  const toasts = [];
  host.toast = (msg) => toasts.push(msg);
  R.missingSupport = () => [];
  R.create = () => ({ start: async () => { starts += 1; }, stop: async () => {},
    resetContext() {}, isRunning: () => false, pending: () => 0 });
  const narrate = host.fetch;
  host.fetch = async (url, opts) => url === '/api/voice-stt/status'
    ? { ok: true, json: async () => ({ success: true, enabled: true, configured: false }) }
    : narrate(url, opts);
  flagsOn = ['voice_server_stt'];
  voice.enter();
  await sleep(5);
  assert.match(document.getElementById('voiceModeHint').textContent, /needs an OpenAI API key/);
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(starts, 0, 'no server recording without a key');
  assert.strictEqual(recs.length, 1, 'browser speech runs instead');
  // Electron: Chromium without a speech backend.
  recs[0].onerror({ error: 'network' });
  await sleep(250);
  assert.strictEqual(recs.length, 1);
  assert.ok(toasts.some((t) => /OpenAI API key/.test(t)), toasts.join('|'));
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_realtime_flag_selects_live_engine_and_hands_off_to_chat():
    setup = r"""
let realtimeHost, realtimeStarts = 0, realtimeStops = 0, liveOpen = false;
globalThis.CuttleChatVoiceRealtime = {
  supported: () => true,
  create: (h) => { realtimeHost = h; return {
    start: async () => { realtimeStarts++; liveOpen = true; h.onStatus('Listening…'); },
    stop: () => { realtimeStops++; liveOpen = false; }, isOpen: () => liveOpen,
    agentResult() {},
  }; },
};
"""
    harness = CONTROLLER_HARNESS.replace("const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  flagsOn = ['voice_realtime', 'voice_server_stt', 'voice_narrator'];
  let last = assistant;
  host.lastAssistantMessage = () => last;
  host.submit = async (m) => { submitted.push(m); last = {dataset: {rawContent: 'Verified agent result'}}; };
  host.compose = (m) => '/codex ' + m;
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  assert.strictEqual(realtimeStarts, 1);
  assert.strictEqual(recs.length, 0, 'Realtime takes precedence over other voice engines');
  assert.match(document.getElementById('voiceModeHint').textContent, /OpenAI Realtime/);
  const result = await realtimeHost.delegate('inspect files');
  assert.deepStrictEqual(submitted, ['/codex inspect files']);
  assert.deepStrictEqual(result, {reply: 'Verified agent result'});
  voice.onGenerationEnded({}); await sleep(150);
  assert.strictEqual(played, 0, 'Realtime tool result owns voice playback, no duplicate TTS');
  assert.strictEqual(narrateCalls.length, 0, 'Realtime has no competing narrator');
  mic.dispatch('click', ev(mic)); await sleep(5);
  assert.ok(realtimeStops > 0 && !liveOpen, 'tap disconnects without sending another turn');
  mic.dispatch('click', ev(mic)); await sleep(5);
  document.hidden = true; document.dispatch('visibilitychange'); await sleep(5);
  assert.ok(!liveOpen, 'screen lock closes live microphone');
  voice.exit();
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_mobile_screen_dim_keeps_full_duplex_call():
    """A phone screen lock must not pause/disconnect GPT-Live or Realtime.

    The foreground microphone service keeps the call alive with the screen off.
    Pausing the presenter here left the connection open while the orb read
    "paused", so the next tap disconnected the call.
    """
    setup = r"""
let realtimeHost, realtimeStarts = 0, realtimeStops = 0, liveOpen = false;
globalThis.parent = { cuttleMobile: { isNative: true, platform: 'android' } };
globalThis.CuttleChatVoiceRealtime = {
  supported: () => true,
  create: (h) => { realtimeHost = h; return {
    start: async () => { realtimeStarts++; liveOpen = true; h.onStatus('Listening…'); },
    stop: () => { realtimeStops++; liveOpen = false; }, isOpen: () => liveOpen,
    agentResult() {},
  }; },
};
"""
    harness = CONTROLLER_HARNESS.replace(
        "const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  flagsOn = ['voice_realtime', 'voice_server_stt', 'voice_narrator'];
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  assert.strictEqual(realtimeStarts, 1);
  document.hidden = true; document.dispatch('visibilitychange'); await sleep(5);
  assert.ok(liveOpen && realtimeStops === 0, 'screen lock keeps the phone call alive');
  assert.doesNotMatch(document.getElementById('voiceModeStatus').textContent, /paused/i);
  document.hidden = false; document.dispatch('visibilitychange'); await sleep(5);
  assert.ok(liveOpen, 'call is still open after waking');
  voice.exit();
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_voice_transcript_follows_only_when_pinned_to_bottom():
    """Auto-scroll is sticky, never forced.

    At the bottom → new bubbles and streamed text keep up; scrolled up → the
    reader's place is kept (a long spoken reply must not yank them down).
    """
    setup = r"""
let rtHost = null;
globalThis.CuttleChatVoiceRealtime = {
  supported: () => true,
  create: (h) => { rtHost = h; return {
    start: async () => { h.onStatus('Listening…'); }, stop() {}, isOpen: () => true,
  }; },
};
"""
    harness = CONTROLLER_HARNESS.replace(
        "const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  flagsOn = ['voice_realtime', 'voice_server_stt', 'voice_narrator'];
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  const t = document.getElementById('voiceModeTranscript');
  t.clientHeight = 200;
  // Pinned to the newest line → a new bubble scrolls into view.
  t.scrollHeight = 1000; t.scrollTop = 800;
  rtHost.onTranscript('assistant', 'first reply', false);
  assert.strictEqual(t.scrollTop, 1000, 'pinned: new bubble follows to the bottom');
  // Reader scrolled up → a new bubble must not yank them down.
  t.scrollHeight = 1200; t.scrollTop = 100;
  rtHost.onTranscript('assistant', 'second reply', false);
  assert.strictEqual(t.scrollTop, 100, 'scrolled up: new bubble keeps the reading position');
  // Streamed growth of the live line follows the same rule.
  t.scrollHeight = 1300; t.scrollTop = 1100;         // at bottom
  rtHost.onTranscript('assistant', 'stream', true);  // opens a live line
  assert.strictEqual(t.scrollTop, 1300, 'pinned: live line opens at the bottom');
  t.scrollHeight = 1400; t.scrollTop = 1200;         // at bottom
  rtHost.onTranscript('assistant', 'ing…', true);    // extends the live line
  assert.strictEqual(t.scrollTop, 1400, 'pinned: streamed text follows');
  t.scrollHeight = 1500; t.scrollTop = 0;            // scrolled up
  rtHost.onTranscript('assistant', 'more', true);
  assert.strictEqual(t.scrollTop, 0, 'scrolled up: streamed text stays put');
  voice.exit();
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_voice_transcript_groups_streaming_deltas_by_utterance():
    """Streaming transcript deltas grow one bubble per spoken item.

    A late input transcript landing between two output-transcript deltas used to
    split the assistant's phrase into two bubbles; grouping by the provider item
    id keeps each utterance whole, and a new response starts a fresh bubble.
    """
    setup = r"""
let rtHost = null;
globalThis.CuttleChatVoiceRealtime = {
  supported: () => true,
  create: (h) => { rtHost = h; return {
    start: async () => { h.onStatus('Listening…'); }, stop() {}, isOpen: () => true,
  }; },
};
"""
    harness = CONTROLLER_HARNESS.replace(
        "const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  flagsOn = ['voice_realtime'];
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  const t = document.getElementById('voiceModeTranscript');
  const bodies = () => t.children.map((c) => c.lastElementChild && c.lastElementChild.textContent);
  rtHost.onTranscript('assistant', 'Hel', true, 'resp-1');
  rtHost.onTranscript('user', 'hi', true, 'in-1');
  rtHost.onTranscript('assistant', 'lo', true, 'resp-1');
  rtHost.onTranscript('user', ' there', true, 'in-1');
  assert.strictEqual(t.children.length, 2, 'one bubble per utterance, not per delta');
  assert.deepStrictEqual(bodies(), ['Hello', 'hi there']);
  rtHost.onTranscript('assistant', 'Again', true, 'resp-2');
  assert.strictEqual(t.children.length, 3, 'a new response opens a new bubble');
  assert.deepStrictEqual(bodies(), ['Hello', 'hi there', 'Again']);
  voice.exit();
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_voice_steers_the_running_agent():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  let generating = true;
  const steered = [], queued = [];
  host.isGenerating = () => generating;
  host.steer = async (m) => { steered.push(m); return true; };
  host.enqueue = (m) => { queued.push(m); };
  voice.enter();
  await sleep(5);
  assert.strictEqual(mic.disabled, false, 'mic stays live while the agent works');
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 1, 'tapping while the agent works starts listening');
  assert.match(document.getElementById('voiceModeStatus').textContent, /running agent/);
  recs[0].onresult({ results: [res('also check the tests', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(steered, ['also check the tests']);
  assert.deepStrictEqual(queued, []);
  assert.deepStrictEqual(submitted, [], 'a steer is not a new turn');
  assert.strictEqual(document.getElementById('voiceModeStatus').textContent, 'Added to the running reply…');

  host.steer = async () => false;
  mic.dispatch('click', ev(mic));
  await sleep(5);
  recs[1].onresult({ results: [res('then deploy', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(queued, ['then deploy'], 'non-steerable agent → follow-up queue');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_reply_waits_while_user_is_talking():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  let generating = true;
  host.isGenerating = () => generating;
  host.steer = async () => true;
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  generating = false;
  voice.onGenerationEnded({});
  await sleep(150);
  assert.strictEqual(played, 0, 'reply never talks over the user');
  assert.strictEqual(recs[0].stopped, undefined, 'still listening');
  assert.match(document.getElementById('voiceModeStatus').textContent, /Reply ready/);
  recs[0].onresult({ results: [res('one more thing', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(10);
  assert.deepStrictEqual(submitted, ['one more thing']);
  assert.strictEqual(played, 1, 'held reply plays after the send');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_server_engine_fallback_names_the_missing_feature():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const toasts = [];
  host.toast = (m) => toasts.push(m);
  R.missingSupport = () => ['MediaRecorder'];
  document.getElementById('voiceModeHint').textContent = 'Tap to talk';
  flagsOn = ['voice_server_stt'];
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.strictEqual(recs.length, 1, 'falls back to the browser recognizer');
  assert.strictEqual(document.getElementById('voiceModeHint').textContent,
    'Tap to talk · Browser speech — server transcription needs MediaRecorder');
  assert.deepStrictEqual(toasts, ['Server transcription unavailable here (needs MediaRecorder)']);
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_narrator_acks_then_narrates_and_stops_at_reply():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const realSetTimeout = setTimeout;
  globalThis.setTimeout = (f, ms) => realSetTimeout(f, Math.min(ms || 0, 5));
  let clock = 1000000;
  Date.now = () => clock;
  flagsOn = ['voice_narrator'];
  const audio = Buffer.from('mp3').toString('base64');
  narrateReply = (b) => ({ success: true, audio_base64: audio,
    text: b.kind === 'ack' ? 'On it.' : 'Editing the voice file.' });
  voice.enter();
  await sleep(0);
  mic.dispatch('click', ev(mic));
  await sleep(5);
  recs[0].onresult({ results: [res('fix the mic', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(30);
  assert.deepStrictEqual(narrateCalls[0], { kind: 'ack', message: 'fix the mic', mode: 'new' });
  assert.strictEqual(clips.length, 1, 'acknowledgment spoken right away');
  const lines = document.getElementById('voiceModeTranscript').children;
  assert.ok(lines[lines.length - 1].classList.contains('is-narration'));
  voice.onAgentStatus('Connecting...');
  voice.onAgentStatus('Editing chat_voice.js');
  await sleep(30);
  assert.strictEqual(narrateCalls.length, 1, 'progress waits for the pacing gap');
  clock += N.MIN_GAP_MS;
  await sleep(30);
  assert.strictEqual(narrateCalls[1].kind, 'progress');
  assert.deepStrictEqual(narrateCalls[1].events, ['Editing chat_voice.js']);
  assert.deepStrictEqual(narrateCalls[1].said, ['On it.']);
  assert.strictEqual(clips.length, 2);
  // Agent goes quiet: a "still working" heartbeat, not silence.
  clock += N.HEARTBEAT_MS;
  await sleep(30);
  assert.strictEqual(narrateCalls[2].kind, 'progress');
  assert.deepStrictEqual(narrateCalls[2].events, []);
  assert.ok(narrateCalls[2].elapsed_sec >= 35);
  voice.onAgentStatus('Running tests');
  clock += N.MIN_GAP_MS;
  voice.onGenerationEnded({ isError: false });
  await sleep(60);
  assert.strictEqual(narrateCalls.length, 3, 'reply ended the turn: no late narration');
  assert.strictEqual(played, 1, 'final reply still spoken');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_voice_mode_persists_across_space_switch_reload():
    """The shell reloads the chat frame on a space switch; voice view must return."""
    setup = r"""
globalThis.location = { search: '?chat=42' };
const _voiceStore = {};
globalThis.sessionStorage = {
  getItem: (k) => (k in _voiceStore ? _voiceStore[k] : null),
  setItem: (k, v) => { _voiceStore[k] = String(v); },
  removeItem: (k) => { delete _voiceStore[k]; },
};
"""
    harness = CONTROLLER_HARNESS.replace("const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  const v = V.create(host);
  assert.ok(!v.isActive(), 'a fresh chat opens in chat view');
  v.enter();
  assert.ok(v.isActive());
  assert.strictEqual(_voiceStore['cuttle.voiceMode.42'], '1', 'voice mode is remembered per chat');
  v.exit();
  assert.strictEqual(_voiceStore['cuttle.voiceMode.42'], undefined, 'exiting voice mode clears the memory');
  // The shell reloads this chat on a space switch: voice view is restored.
  _voiceStore['cuttle.voiceMode.42'] = '1';
  const v2 = V.create(host);
  assert.ok(v2.isActive(), 'voice mode restored after the space-switch reload');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_leaving_voice_view_keeps_a_shell_owned_call_alive():
    """Looking at the chat must not hang up the call.

    The chat page is a presenter over the shell-owned voice runtime; exiting
    voice view used to call the runtime's stop(), dropping the call the moment
    the user switched to the chat to read the reply.
    """
    setup = r"""
let runtimeStops = 0, attached = 0;
globalThis.parent = { cuttleVoiceRuntime: {
  isOpen: () => true, engine: () => 'live',
  attach: () => { attached++; return () => {}; },
  start: async () => {}, stop: () => { runtimeStops++; },
} };
globalThis.CuttleChatVoiceLive = { supported: () => true,
  create: () => ({ start: async () => {}, stop: async () => {}, isOpen: () => true, mute() {} }) };
"""
    harness = CONTROLLER_HARNESS.replace(
        "const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  host.sessionId = () => 42;
  host.fetch = async () => ({ ok: true, json: async () => ({ success: true, engine: 'auto', flags: [] }) });
  flagsOn = ['voice_live'];
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  assert.ok(attached > 0, 'presenter attaches to the shell-owned runtime');
  voice.exit();
  await sleep(5);
  assert.strictEqual(runtimeStops, 0, 'exiting voice view must not stop the shell call');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_leaving_voice_view_keeps_a_standalone_call_alive():
    """A chat opened on its own (no shell iframe) owns a document-lifetime call.

    Without the shell runtime the presenter used a bare Live controller that
    ``exit()`` stopped, so opening a chat in a browser tab and switching back to
    chat view dropped the call. It must use a page-owned runtime with the same
    attach/start contract as the shell.
    """
    setup = r"""
let creates = 0, runtimeStarts = 0, runtimeStops = 0, attached = 0, open = false;
globalThis.CuttleShellVoice = { create: () => { creates++; return {
  isOpen: () => open, engine: () => 'live',
  attach: () => { attached++; return () => {}; },
  start: async () => { runtimeStarts++; open = true; },
  stop: () => { runtimeStops++; open = false; },
}; } };
globalThis.CuttleChatVoiceLive = { supported: () => true,
  create: () => ({ start: async () => { open = true; }, stop: async () => { open = false; },
    isOpen: () => open, mute() {} }) };
"""
    harness = CONTROLLER_HARNESS.replace(
        "const V = require(process.argv[4]);", setup + "const V = require(process.argv[4]);")
    _node(harness + r"""
(async () => {
  host.sessionId = () => 42;
  host.fetch = async () => ({ ok: true, json: async () => ({ success: true, engine: 'auto', flags: [] }) });
  flagsOn = ['voice_live'];
  voice.enter(); mic.dispatch('click', ev(mic)); await sleep(5);
  assert.strictEqual(creates, 1, 'one document-lifetime runtime is created');
  assert.ok(runtimeStarts > 0, 'standalone chat starts through the page-owned runtime');
  assert.ok(attached > 0, 'presenter attaches to the page-owned runtime');
  voice.exit();
  await sleep(5);
  assert.strictEqual(runtimeStops, 0, 'exiting voice view must not stop the page-owned call');
})().catch((e) => { console.error(e); process.exit(1); });
""")


def test_chat_page_does_not_own_voice_state():
    page = PAGE_JS.read_text(encoding="utf-8")
    for owned in ("SpeechRecognition", "voiceModePhase", "voiceModeActive", "_voiceHoldOwner"):
        assert owned not in page, f"{owned} belongs in chat_voice.js, not chat_page.js"
    assert "CuttleChatVoice.create(" in page


def test_voice_submits_through_shared_turn_interface():
    """4E: voice must not own a second turn lifecycle.

    ``submitVoiceTurn`` is a thin submitter over ``processMessage`` — the one
    shared turn-submission interface (over chat_generation's busy-lock) also
    used by terminal inputs, cuttle buttons, action cards and the supervised
    lane. It used to write ``inFlightUserMessage`` and begin generation itself;
    both belong to processMessage's PREPARE/SUBMIT path, so voice no longer
    duplicates them.
    """
    page = PAGE_JS.read_text(encoding="utf-8")
    body = _fn_body(page, "function submitVoiceTurn")
    assert "processMessage(message" in body
    assert "beginLocalGeneration" not in body, "busy-lock begin belongs to processMessage"
    assert "inFlightUserMessage" not in body, "in-flight tracking belongs to processMessage"
    # The shared interface still owns the busy-lock begin + in-flight write.
    proc = _fn_body(page, "function processMessage")
    assert "beginLocalGeneration()" in proc
    assert "inFlightUserMessage = message" in proc


@node_only
def test_reply_shows_combined_usage_and_session_total():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const summed = [];
  host.usageFor = (el) => ({ prompt_tokens: 1000, completion_tokens: 100,
    cost: 0.01, cost_estimated: false });
  host.sumUsage = (parts) => { summed.push(parts); return { prompt_tokens: 1600,
    completion_tokens: 150, cost: 0.012, cost_estimated: true }; };
  host.usageHtml = (u) => '<div class="message-usage">u:' + u.prompt_tokens + '</div>';
  host.sessionUsage = () => ({ prompt_tokens: 1600, completion_tokens: 150,
    cost: 0.012, cost_estimated: true });
  host.speechFor = async () => ({ url: 'blob:x', spoken: 'reply text',
    voiceUsage: { prompt_tokens: 600, completion_tokens: 50, cost: 0.002, cost_estimated: true } });
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(5);
  recs[0].onresult({ results: [res('hello there', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(5);
  assert.deepStrictEqual(submitted, ['hello there']);
  voice.onGenerationEnded({});
  await sleep(400);
  assert.strictEqual(played, 1);
  // Agent + voice layers summed for the reply footer.
  assert.strictEqual(summed.length, 1);
  assert.strictEqual(summed[0].length, 2);
  assert.strictEqual(summed[0][1].prompt_tokens, 600);
  const lines = document.getElementById('voiceModeTranscript').children;
  const last = lines[lines.length - 1];
  const usage = last.children.find((c) => c.classList.contains('voice-mode-line-usage'));
  assert.ok(usage, 'assistant line carries a usage footer');
  assert.match(usage.innerHTML, /message-usage/);
  // Session-total strip appears with the same footer markup.
  const strip = document.getElementById('voiceModeSessionUsage');
  assert.strictEqual(strip.hidden, false);
  assert.strictEqual(strip.children[0].textContent, 'Session total');
  assert.match(strip.children[1].innerHTML, /message-usage/);
})().catch((e) => { console.error(e); process.exit(1); });
""")


def test_voice_modules_load_before_page():
    html = CHAT_HTML.read_text(encoding="utf-8")
    order = [
        html.find('src="/js/chat/chat_voice_segments.js'),
        html.find('src="/js/chat/chat_voice_stars.js'),
        html.find('src="/js/chat/chat_voice_narrator.js'),
        html.find('src="/js/chat/chat_voice_recorder.js'),
        html.find('src="/js/chat/chat_voice.js'),
        html.find('src="/js/chat/chat_page.js'),
    ]
    assert all(i >= 0 for i in order) and order == sorted(order)
    assert 'href="css/chat_voice.css' in html


def test_voice_overlay_markup_and_styles():
    html = CHAT_HTML.read_text(encoding="utf-8")
    overlay_at = html.find('id="voiceModeOverlay"')
    assert overlay_at >= 0
    assert html.find('id="voiceModeSegments"') > overlay_at
    assert html.find('id="voiceModeWatchBtn"') > overlay_at
    # The orbital orb itself is the talk control; the standalone record button is gone.
    assert html.count('id="voiceModeMicBtn"') == 1
    assert 'class="voice-mode-orbit-wrap voice-mode-mic-btn" id="voiceModeMicBtn"' in html
    assert 'voice-mic-icon' not in html, "no separate record button / mic icon"
    css = VOICE_CSS.read_text(encoding="utf-8")
    for sel in (".voice-mode-segment-remove", ".voice-mode-watch-btn", ".voice-mode-mic-btn"):
        assert sel in css
    assert "touch-action: none" in css, "orb must opt out of touch scrolling for reliable holds"
    # The typing status sets inline visibility:visible; only !important on
    # descendants keeps it from showing through the transparent overlay.
    assert "body.voice-mode .chat-area *" in css
    assert "visibility: hidden !important" in css
    stage = css[css.index(".voice-mode-stage {"):]
    assert "max-height: 100%" in stage.split("}")[0], "stage must fit the viewport"


def _first_rule_block(css: str, selector: str) -> str:
    at = css.index(selector + " {")
    return css[at:css.index("}", at)]


def _top_value(block: str) -> str:
    m = re.search(r"(?<![\w-])top\s*:\s*([^;]+);", block)
    assert m, "corner rule must declare top"
    return " ".join(m.group(1).split())


def test_voice_mode_corner_buttons_share_one_row():
    """Hamburger, back-to-chat, new-chat and history buttons share one y.

    The hamburger is an in-flow inline-level button inside the fixed
    `.chat-chrome-menu` box, so without a top alignment the inline strut
    drops it a few px below the directly-fixed corner buttons (measured
    18.75px vs the shared 14px row in voice mode).
    """
    page_css = (WEB / "css" / "chat_page.css").read_text(encoding="utf-8")
    voice_css = VOICE_CSS.read_text(encoding="utf-8")
    safe_css = (WEB / "css" / "safe_area.css").read_text(encoding="utf-8")

    inner = _first_rule_block(page_css, ".chat-chrome-menu .corner-chat-button")
    menu = _first_rule_block(page_css, ".chat-chrome-menu")
    flat = (inner + menu).replace(" ", "")
    assert ("vertical-align:top" in flat
            or "display:block" in flat
            or "display:flex" in flat
            or "display:grid" in flat), \
        "hamburger must top-align inside .chat-chrome-menu (or the menu must be flex/grid)"

    base_top = _top_value(_first_rule_block(page_css, ".corner-chat-button"))
    exit_top = _top_value(_first_rule_block(voice_css, ".voice-mode-exit-btn"))
    assert exit_top == base_top, f"voice exit top ({exit_top}) != corner row ({base_top})"

    menu_top = _top_value(_first_rule_block(safe_css, "html.is-cuttle-mobile .chat-chrome-menu"))
    safe_exit_top = _top_value(_first_rule_block(safe_css, "html.is-cuttle-mobile .voice-mode-exit-btn"))
    assert safe_exit_top == menu_top, "mobile safe-area must move the exit button with the menu"


@node_only
def test_recorder_level_starts_at_zero():
    _node(r"""
const assert = require('assert');
const R = require(process.argv[5]);
const rec = R.create({ fetch: async () => ({}), onSpeech() {}, onPhraseText() {}, onPending() {}, onError() {} });
assert.strictEqual(typeof rec.level, 'function');
assert.strictEqual(rec.level(), 0, 'idle recorder reports no level');
""")


@node_only
def test_live_voice_view_offers_persistent_call_button_until_exit():
    """GPT-Live voice view registers a Call offer; Call runs the orb's connect path."""
    _node(r"""
globalThis.parent = globalThis;
const runtime = { offers: [], started: [], withdrawn: 0, open: false,
  offer(o) { this.offers.push(o); return () => { this.withdrawn += 1; }; },
  attach: () => () => {},
  start: async (ctx) => { runtime.started.push(ctx); runtime.open = true; },
  stop: async () => { runtime.open = false; },
  isOpen: () => runtime.open, engine: () => (runtime.open ? 'live' : null) };
globalThis.CuttleShellVoice = { create: () => runtime };
globalThis.CuttleChatVoiceLive = { supported: () => true, create: () => ({}) };
""" + CONTROLLER_HARNESS + r"""
host.sessionId = () => '42';
flagsOn = ['voice_live'];
(async () => {
  voice.enter();
  await sleep(20);
  assert.strictEqual(runtime.offers.length, 1, 'voice view offers a call');
  const offer = runtime.offers[0];
  assert.ok(offer.alive(), 'offer is live while voice view is open');
  await offer.connect();
  assert.strictEqual(runtime.started.length, 1);
  assert.strictEqual(runtime.started[0].session_id, '42');
  assert.strictEqual(runtime.started[0].engine, 'live');
  await offer.connect();
  assert.strictEqual(runtime.started.length, 1, 'Call never redials an open call');
  voice.exit();
  assert.strictEqual(runtime.withdrawn, 1, 'leaving voice view withdraws the offer');
  assert.ok(!offer.alive());
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_push_to_talk_voice_view_offers_no_call_button():
    _node(r"""
globalThis.parent = globalThis;
let offered = 0;
globalThis.CuttleShellVoice = { create: () => ({ offer() { offered += 1; return () => {}; } }) };
""" + CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  await sleep(20);
  assert.strictEqual(offered, 0, 'browser speech keeps the orb as push-to-talk');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_realtime_voice_view_on_new_chat_still_offers_call():
    """New chat (no id yet) → voice view: Call is offered and creates the chat on connect.

    Regression: the offer was gated on an existing session id, so voice view on a
    fresh chat (or before a restore finished) showed no Call button at all.
    """
    _node(r"""
globalThis.parent = globalThis;
const runtime = { offers: [], started: [], open: false,
  offer(o) { this.offers.push(o); return () => {}; },
  attach: () => () => {},
  start: async (ctx) => { runtime.started.push(ctx); runtime.open = true; },
  stop: async () => { runtime.open = false; },
  isOpen: () => runtime.open, engine: () => (runtime.open ? 'realtime' : null) };
globalThis.CuttleShellVoice = { create: () => runtime };
globalThis.CuttleChatVoiceRealtime = { supported: () => true, create: () => ({}) };
""" + CONTROLLER_HARNESS + r"""
let sid = null;
const created = [];
host.sessionId = () => sid;
host.adoptSession = (id) => { sid = id; };
host.fetch = async (url, opts) => {
  if (url === '/api/auth/sessions') { created.push(JSON.parse(opts.body)); return { ok: true, json: async () => ({ success: true, session_id: 77 }) }; }
  return { ok: true, json: async () => ({ success: true }) };
};
flagsOn = ['voice_realtime'];
(async () => {
  voice.enter();
  await sleep(20);
  assert.strictEqual(runtime.offers.length, 1, 'new chat voice view offers Call');
  await runtime.offers[0].connect();
  assert.strictEqual(created.length, 1, 'connecting creates the chat');
  assert.strictEqual(runtime.started.length, 1);
  assert.strictEqual(runtime.started[0].session_id, '77');
  assert.strictEqual(runtime.started[0].engine, 'realtime');
  voice.exit();
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_realtime_voice_view_without_session_capability_offers_no_call():
    _node(r"""
globalThis.parent = globalThis;
let offered = 0;
globalThis.CuttleShellVoice = { create: () => ({ offer() { offered += 1; return () => {}; }, attach: () => () => {} }) };
globalThis.CuttleChatVoiceRealtime = { supported: () => true, create: () => ({}) };
""" + CONTROLLER_HARNESS + r"""
host.sessionId = () => null;
flagsOn = ['voice_realtime'];
(async () => {
  voice.enter();
  await sleep(20);
  assert.strictEqual(offered, 0, 'no chat and no way to create one: no Call');
})().catch((e) => { console.error(e); process.exit(1); });
""")
