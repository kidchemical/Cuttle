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

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat"
SEGMENTS_JS = CHAT_JS / "chat_voice_segments.js"
STARS_JS = CHAT_JS / "chat_voice_stars.js"
NARRATOR_JS = CHAT_JS / "chat_voice_narrator.js"
VOICE_JS = CHAT_JS / "chat_voice.js"
PAGE_JS = CHAT_JS / "chat_page.js"
CHAT_HTML = WEB / "chat_page.html"
VOICE_CSS = WEB / "css" / "chat_voice.css"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(script: str) -> None:
    proc = subprocess.run(
        ["node", "-e", script, str(SEGMENTS_JS), str(STARS_JS), str(NARRATOR_JS), str(VOICE_JS)],
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
    this.dataset = {}; this.hidden = false; this._t = ''; this.listeners = {}; this.style = {}; }
  set className(v) { this.classList = new CL(); String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classList.add(c)); }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(''); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
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
  addEventListener() {}, removeEventListener() {},
};
globalThis.addEventListener = () => {};
const recs = [];
globalThis.SpeechRecognition = class { constructor() { recs.push(this); } start() { this.started = true; } stop() { this.stopped = true; } };
require(process.argv[1]);
require(process.argv[2]);
const N = require(process.argv[3]);
const V = require(process.argv[4]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const res = (t, f) => ({ isFinal: f, 0: { transcript: t } });
const ev = (target) => ({ target, preventDefault() {}, stopPropagation() {} });
const submitted = [];
let played = 0;
let stops = 0;
let narratorOn = false;
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
  narratorEnabled: async () => narratorOn,
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
  await sleep(0);
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
  await sleep(0);
  assert.deepStrictEqual(submitted, ['hello there send this']);
  assert.ok(recs[1].stopped);
  assert.strictEqual(box.hidden, true);
  assert.strictEqual(narrateCalls.length, 0, 'narrator flag off → no narration requests');
})().catch((e) => { console.error(e); process.exit(1); });
""")


@node_only
def test_pending_reply_still_speaks_after_leaving_overlay_mid_run():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  voice.enter();
  mic.dispatch('click', ev(mic));
  await sleep(0);
  recs[0].onresult({ results: [res('do the thing', true)] });
  mic.dispatch('click', ev(mic));
  await sleep(0);
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
def test_narrator_pacing():
    _node(r"""
const assert = require('assert');
const N = require(process.argv[3]);
const p = N.createPlan();
assert.strictEqual(N.noteStatus(p, 'Editing a.py'), false, 'no open turn');
const t = N.beginTurn(p, '/cursor x', 0);
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
def test_narrator_acks_then_narrates_and_stops_at_reply():
    _node(CONTROLLER_HARNESS + r"""
(async () => {
  const realSetTimeout = setTimeout;
  globalThis.setTimeout = (f, ms) => realSetTimeout(f, Math.min(ms || 0, 5));
  let clock = 1000000;
  Date.now = () => clock;
  narratorOn = true;
  const audio = Buffer.from('mp3').toString('base64');
  narrateReply = (b) => ({ success: true, audio_base64: audio,
    text: b.kind === 'ack' ? 'On it.' : 'Editing the voice file.' });
  voice.enter();
  await sleep(0);
  mic.dispatch('click', ev(mic));
  await sleep(0);
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
  voice.onAgentStatus('Running tests');
  clock += N.MIN_GAP_MS;
  voice.onGenerationEnded({ isError: false });
  await sleep(60);
  assert.strictEqual(narrateCalls.length, 2, 'reply ended the turn: no late narration');
  assert.strictEqual(played, 1, 'final reply still spoken');
})().catch((e) => { console.error(e); process.exit(1); });
""")


def test_chat_page_does_not_own_voice_state():
    page = PAGE_JS.read_text(encoding="utf-8")
    for owned in ("SpeechRecognition", "voiceModePhase", "voiceModeActive", "_voiceHoldOwner"):
        assert owned not in page, f"{owned} belongs in chat_voice.js, not chat_page.js"
    assert "CuttleChatVoice.create(" in page


def test_voice_modules_load_before_page():
    html = CHAT_HTML.read_text(encoding="utf-8")
    order = [
        html.find('src="/js/chat/chat_voice_segments.js'),
        html.find('src="/js/chat/chat_voice_stars.js'),
        html.find('src="/js/chat/chat_voice_narrator.js'),
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
    css = VOICE_CSS.read_text(encoding="utf-8")
    for sel in (".voice-mode-segment-remove", ".voice-mode-watch-btn", ".voice-mode-mic-btn"):
        assert sel in css
    assert "touch-action: none" in css, "mic must opt out of touch scrolling for reliable holds"
