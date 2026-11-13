"""Composer voice clips: pure decisions + the markup contract.

Contracts pinned here:
- the recording phase machine only takes the events that apply;
- durations, upload names and waveform bar heights are pure and bounded;
- a paused native-mic recording joins its WAV pieces into one valid file;
- audio attachments are classified as voice clips (player in the transcript,
  `duration_ms` carried on staged rows only);
- both composers carry a flag-gated mic button after the attach button, and
  the owner script loads after the recorder it captures audio through;
- live text: each clip owns one block of the composer text that survives the
  user's edits, grows phrase by phrase and leaves with its clip;
- the longest clip follows the setting, capped for the native mic's WAV.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat"
CHAT_HTML = WEB / "chat_page.html"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(script: str, *files: str) -> None:
    proc = subprocess.run(
        ["node", "-e", script, *[str(CHAT_JS / f) for f in files]],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


@node_only
def test_phase_machine_and_formatting():
    _node(r"""
const assert = require('assert');
const C = require(process.argv[1]);
assert.strictEqual(C.nextPhase('idle', 'start'), 'starting');
assert.strictEqual(C.nextPhase('starting', 'started'), 'recording');
assert.strictEqual(C.nextPhase('starting', 'error'), 'idle');
assert.strictEqual(C.nextPhase('recording', 'pause'), 'paused');
assert.strictEqual(C.nextPhase('paused', 'resume'), 'recording');
assert.strictEqual(C.nextPhase('paused', 'stop'), 'saving');
assert.strictEqual(C.nextPhase('saving', 'saved'), 'idle');
assert.strictEqual(C.nextPhase('saving', 'error'), 'failed');
assert.strictEqual(C.nextPhase('failed', 'retry'), 'saving');
assert.strictEqual(C.nextPhase('failed', 'discard'), 'idle');
// Events that do not apply are refused, not coerced.
assert.strictEqual(C.nextPhase('idle', 'stop'), null);
assert.strictEqual(C.nextPhase('saving', 'discard'), null);
assert.strictEqual(C.nextPhase('recording', 'resume'), null);
assert.strictEqual(C.nextPhase('nope', 'start'), null);

assert.strictEqual(C.formatDuration(0), '0:00');
assert.strictEqual(C.formatDuration(12400), '0:12');
assert.strictEqual(C.formatDuration(75000), '1:15');
assert.strictEqual(C.formatDuration(-5), '0:00');

const at = new Date(2026, 10, 12, 9, 5, 7);
assert.strictEqual(C.clipFilename('audio/webm;codecs=opus', at), 'voice-20261112-090507.webm');
assert.strictEqual(C.clipFilename('audio/wav', at), 'voice-20261112-090507.wav');
assert.strictEqual(C.clipFilename('audio/mp4', at), 'voice-20261112-090507.m4a');
assert.strictEqual(C.clipFilename('audio/ogg;codecs=opus', at), 'voice-20261112-090507.ogg');
assert.strictEqual(C.clipFilename('', at), 'voice-20261112-090507.webm');

for (const rms of [-1, 0, 0.01, 0.1, 0.5, 5]) {
  const h = C.levelToBar(rms);
  assert.ok(h >= 0.08 && h <= 1, h);
}
assert.ok(C.levelToBar(0.02) < C.levelToBar(0.2));
const levels = [];
for (let i = 0; i < 10; i++) C.pushLevel(levels, i, 4);
assert.deepStrictEqual(levels, [6, 7, 8, 9]);

assert.strictEqual(C.chipLabel({ duration_ms: 12400 }), 'Voice clip · 0:12');
assert.strictEqual(C.chipLabel({}), 'Voice clip');
""", "chat_voice_clip.js")


@node_only
def test_paused_native_recording_merges_into_one_wav():
    _node(r"""
const assert = require('assert');
const R = require(process.argv[1]);
function wav(samples) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  v.setUint32(0, 0x52494646); v.setUint32(4, 36 + samples.length * 2, true);
  v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => v.setInt16(44 + i * 2, s, true));
  return buf;
}
const out = new DataView(R.mergeWavBuffers([wav([1, 2]), wav([3]), wav([4, 5, 6])]));
assert.strictEqual(out.byteLength, 44 + 12);
assert.strictEqual(out.getUint32(0), 0x52494646);
assert.strictEqual(out.getUint32(4, true), 36 + 12);
assert.strictEqual(out.getUint32(40, true), 12);
const got = [];
for (let i = 0; i < 6; i++) got.push(out.getInt16(44 + i * 2, true));
assert.deepStrictEqual(got, [1, 2, 3, 4, 5, 6]);
assert.strictEqual(R.mergeWavBuffers([]).byteLength, 0);
""", "chat_voice_recorder.js")


@node_only
def test_audio_attachments_are_voice_clips():
    _node(r"""
const assert = require('assert');
const A = require(process.argv[1]);
assert.ok(A.isAudioAttachment({ filename: 'voice-1.webm', mime: 'video/webm' }));
assert.ok(A.isAudioAttachment({ filename: 'x', mime: 'audio/wav' }));
assert.ok(!A.isAudioAttachment({ filename: 'shot.png', mime: 'image/png' }));
assert.ok(!A.isAudioAttachment(null));
assert.strictEqual(A.messageAttachmentKind({ filename: 'v.webm', url: '/output/uploads/1/v.webm' }), 'audio');
assert.strictEqual(A.messageAttachmentKind({ filename: 'v.webm' }), 'span');
assert.strictEqual(A.messageAttachmentKind({ filename: 'a.png', url: '/u/a.png' }), 'thumb');
// duration rides staged rows; it is never sent to the server.
const staged = A.addAttachmentsToPending([], [{ filename: 'v.webm', path: '/p', duration_ms: 3200 }]);
assert.strictEqual(staged[0].duration_ms, 3200);
assert.ok(!('duration_ms' in A.normalizeAttachmentList([{ filename: 'a.png' }])[0]));
assert.ok(!('duration_ms' in A.requestAttachmentPayload(staged)[0]));
""", "chat_attachments.js")


def test_composers_carry_a_flagged_mic_button_after_attach():
    html = CHAT_HTML.read_text(encoding="utf-8")
    for attach_id, mic_id, send_id in (
        ("welcomeAttachButton", "welcomeMicButton", "welcomeSendButton"),
        ("attachButton", "micButton", "sendButton"),
    ):
        attach = html.index(f'id="{attach_id}"')
        mic = html.index(f'id="{mic_id}"')
        send = html.index(f'id="{send_id}"')
        assert attach < mic < send
        tag = re.search(r"<button[^>]*id=\"%s\"[^>]*>" % mic_id, html).group(0)
        assert 'data-experimental-flag="composer_voice_clip"' in tag and " hidden" in tag
    assert (html.index('src="/js/chat/chat_voice_recorder.js') < html.index('src="/js/chat/chat_voice_clip.js')
            < html.index('src="/js/chat/chat_page.js'))


@node_only
def test_live_text_blocks_follow_edits_and_leave_with_their_clip():
    _node(r"""
const assert = require('assert');
const T = require(process.argv[1]);
// A clip's first words start a block after what was already typed.
let s = T.write('/cursor fix', {}, 'a', 'the header', true);
assert.strictEqual(s.value, '/cursor fix the header');
assert.strictEqual(T.textOf(s.value, s.blocks, 'a'), 'the header');
// Later phrases join the same block.
s = T.write(s.value, s.blocks, 'a', 'on mobile', true);
assert.strictEqual(s.value, '/cursor fix the header on mobile');
// Interim words replace each other until a final result commits them.
s = T.write(s.value, s.blocks, 'a', 'and', false);
s = T.write(s.value, s.blocks, 'a', 'and the foot', false);
assert.strictEqual(s.value, '/cursor fix the header on mobile and the foot');
s = T.write(s.value, s.blocks, 'a', 'and the footer', true);
s = T.write(s.value, s.blocks, 'a', '', false);
assert.strictEqual(s.value, '/cursor fix the header on mobile and the footer');

// The user fixes a typo inside the block: it stays one block.
let edited = s.value.replace('header', 'Header bar');
let blocks = T.applyEdit(s.blocks, s.value, edited);
assert.strictEqual(T.textOf(edited, blocks, 'a'), 'the Header bar on mobile and the footer');
// Typing before the block moves it; typing right after it stays outside.
let moved = 'please ' + edited;
blocks = T.applyEdit(blocks, edited, moved);
let after = moved + ' thanks';
blocks = T.applyEdit(blocks, moved, after);
assert.strictEqual(T.textOf(after, blocks, 'a'), 'the Header bar on mobile and the footer');

// A second clip gets its own block at the end.
s = T.write(after, blocks, 'b', 'second clip', true);
assert.strictEqual(s.value, after + ' second clip');
// A phrase arriving late for the first clip lands in the first block.
s = T.write(s.value, s.blocks, 'a', 'too', true);
assert.strictEqual(T.textOf(s.value, s.blocks, 'a'), 'the Header bar on mobile and the footer too');
assert.strictEqual(T.textOf(s.value, s.blocks, 'b'), 'second clip');

// Removing a clip removes exactly its block and leaves no gap.
let gone = T.remove(s.value, s.blocks, 'a');
assert.strictEqual(gone.removed, true);
assert.strictEqual(gone.value, 'please /cursor fix thanks second clip');
assert.strictEqual(T.textOf(gone.value, gone.blocks, 'b'), 'second clip');
gone = T.remove(gone.value, gone.blocks, 'b');
assert.strictEqual(gone.value, 'please /cursor fix thanks');
assert.deepStrictEqual(gone.blocks, {});

// Only a clip in an empty box: removing it empties the box.
s = T.write('', {}, 'x', 'hello there', true);
assert.strictEqual(T.remove(s.value, s.blocks, 'x').value, '');
// The box was cleared by a send: blocks collapse and removal is a no-op.
blocks = T.applyEdit(s.blocks, s.value, '');
assert.strictEqual(T.remove('', blocks, 'x').removed, false);
// After a reload no block is tracked: the clip's saved words are found.
gone = T.remove('fix hello there now', {}, 'x', 'hello there');
assert.strictEqual(gone.value, 'fix now');
assert.strictEqual(T.remove('fix something else', {}, 'x', 'hello there').removed, false);
// A clip that produced no words has nothing to remove.
assert.strictEqual(T.write('abc', {}, 'q', '  ', true).value, 'abc');
assert.strictEqual(T.remove('abc', {}, 'q').value, 'abc');

// Engines: the server engine always runs; the browser one needs a recognizer.
assert.strictEqual(T.usableEngine({}, 'server'), 'server');
assert.strictEqual(T.usableEngine({}, 'browser'), 'none');
assert.strictEqual(T.usableEngine({ webkitSpeechRecognition: function () {} }, 'browser'), 'browser');
assert.strictEqual(T.usableEngine({}, 'none'), 'none');
""", "chat_voice_clip_text.js")


@node_only
def test_clip_limit_follows_the_setting_and_live_text_fields_ride_the_attachment():
    _node(r"""
const assert = require('assert');
const C = require(process.argv[1]);
const A = require(process.argv[2]);
assert.strictEqual(C.clipLimitMs(undefined, false), C.MAX_CLIP_MS);
assert.strictEqual(C.clipLimitMs(180000, false), 180000);
assert.strictEqual(C.clipLimitMs(30 * 60000, false), 30 * 60000);
// The app's native mic records WAV: capped under the upload limit.
assert.strictEqual(C.clipLimitMs(30 * 60000, true), C.NATIVE_MAX_CLIP_MS);
assert.strictEqual(C.clipLimitMs('nope', false), C.MAX_CLIP_MS);

const staged = A.addAttachmentsToPending([], [{ filename: 'voice-1.webm', path: '/u/voice-1.webm', url: '/o/voice-1.webm',
    duration_ms: 2000, clip_id: 'vc1', transcript: 'fix the header', transcribed: true },
    { filename: 'voice-2.webm', path: '/u/voice-2.webm', transcript: 'partial' }]);
assert.strictEqual(staged[0].clip_id, 'vc1');
assert.strictEqual(staged[0].transcript, 'fix the header');
// The server only hears "already in the message" for a fully transcribed clip.
const payload = A.requestAttachmentPayload(staged);
assert.deepStrictEqual(payload[0], { filename: 'voice-1.webm', path: '/u/voice-1.webm', mime: '', url: '/o/voice-1.webm', transcribed: true });
assert.strictEqual('transcribed' in payload[1], false);
assert.strictEqual('transcript' in payload[0], false);
""", "chat_voice_clip.js", "chat_attachments.js")


def test_live_text_script_loads_before_the_clip_owner():
    html = CHAT_HTML.read_text(encoding="utf-8")
    assert html.index('src="/js/chat/chat_voice_recorder.js') < html.index('src="/js/chat/chat_voice_clip_text.js') \
        < html.index('src="/js/chat/chat_voice_clip.js') < html.index('src="/js/chat/chat_page.js')
