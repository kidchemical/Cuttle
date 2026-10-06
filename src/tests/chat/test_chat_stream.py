"""Byte-reader owner frontend domain.

Characterization of src/web/js/chat/chat_stream.js (plan D1b): the page
composes one `CuttleChatStream.readEvents(body, options)` call per
stream turn with its hold/read timeouts and one synchronous
`onEvents(batch)` callback; the owner keeps the native reader, one
retained read promise across timeout observations, timers, decoding,
framing, cancellation, and lock cleanup. Classification, session,
paint, turn/Stop, and recovery stay in the page and existing owners.

Covers the module contract under node with native streams and a
controlled clock/scheduler — including the timeout-settlement race —
plus load order. DOM/fetch globals are poisoned to prove no hidden
page/network lookup.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_stream.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

# Shared probe prelude: poison page/network globals, install a manual
# clock, and offer a scripted native stream. The owner under test must
# work with only stream/timer/decoder primitives.
PRELUDE = """
for (const g of ['window', 'document', 'fetch', 'self',
                 'XMLHttpRequest', 'navigator']) {
  try { delete globalThis[g]; } catch (_) {}
}
let nowMs = 1000000;
const pendingTimers = [];
Date.now = () => nowMs;
globalThis.setTimeout = (fn, ms) => {
  const id = pendingTimers.length + 1;
  pendingTimers.push({ id, at: nowMs + Number(ms), fn, settled: false });
  return id;
};
globalThis.clearTimeout = (id) => {
  const t = pendingTimers.find((x) => x.id === id);
  if (t) t.settled = true;
};
function fireDue() {
  for (;;) {
    const next = pendingTimers
      .filter((t) => !t.settled).sort((a, b) => a.at - b.at)[0];
    if (!next || next.at > nowMs) return;
    next.settled = true;
    next.fn();
  }
}
function advance(ms) {
  const end = nowMs + ms;
  for (;;) {
    const next = pendingTimers
      .filter((t) => !t.settled).sort((a, b) => a.at - b.at)[0];
    if (!next || next.at > end) break;
    nowMs = next.at;
    next.settled = true;
    next.fn();
  }
  nowMs = end;
}
function liveTimers() {
  return pendingTimers.filter((t) => !t.settled).length;
}
async function flush(n) {
  for (let i = 0, end = (n || 25); i < end; i++) {
    await Promise.resolve();
  }
}
const api = require(%s);
const assert = require('assert');
const enc = new TextEncoder();
function makeStream() {
  let controller = null;
  let cancels = 0;
  let cancelReason;
  const stream = new ReadableStream({
    start(c) { controller = c; },
    cancel(reason) { cancels += 1; cancelReason = reason; },
  });
  return {
    stream,
    pushText(s) { controller.enqueue(enc.encode(String(s))); },
    pushBytes(a) { controller.enqueue(new Uint8Array(a)); },
    close() { controller.close(); },
    fail(e) { controller.error(e); },
    get cancels() { return cancels; },
    get cancelReason() { return cancelReason; },
  };
}
function countingBody(stream, counts) {
  const origGetReader = stream.getReader.bind(stream);
  return {
    getReader() {
      const reader = origGetReader();
      const origRead = reader.read.bind(reader);
      reader.read = function () {
        counts.reads += 1;
        return origRead().then(
          (r) => { counts.resolutions += 1; return r; },
          (e) => { throw e; });
      };
      return reader;
    },
  };
}
function frame(obj) {
  return 'data: ' + JSON.stringify(obj) + '\\n\\n';
}
""" % repr(str(MOD_JS))


def run_probe(body):
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as handle:
        handle.write(PRELUDE + body)
        probe_path = handle.name
    try:
        proc = subprocess.run(["node", probe_path],
                              capture_output=True, text=True, timeout=60)
    finally:
        Path(probe_path).unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc.stdout.strip()


@node_only
def test_chat_stream_module_parses():
    proc = subprocess.run(["node", "--check", str(MOD_JS)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@node_only
def test_chat_stream_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(
        encoding="utf-8")
    tag = '<script src="/js/chat/chat_stream.js?v='
    assert tag in html, "chat_stream.js must load via versioned script tag"
    assert html.index("chat_pending_result.js") < html.index(
        "chat_stream.js") < html.index("chat_page.js?v="), \
        "load order: classifier before the stream owner before the page"


@node_only
def test_chat_stream_exports_read_events_and_validates():
    out = run_probe("""
(async () => {
  assert.strictEqual(typeof api.readEvents, 'function');
  await assert.rejects(api.readEvents(null, { onEvents() {} }), TypeError);
  await assert.rejects(
    api.readEvents({ getReader() {} }, {}), TypeError);
  const s = makeStream();
  await assert.rejects(
    api.readEvents(s.stream, { holdMs: 10, readTimeoutMs: 5 }),
    TypeError);
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_terminal_batch_and_outcome():
    out = run_probe("""
(async () => {
  const s = makeStream();
  const seen = [];
  let calls = 0;
  const done = api.readEvents(s.stream, {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents(batch) { calls += 1; seen.push(batch); return true; },
  });
  s.pushText(frame({ type: 'session', session_id: 42 })
    + frame({ type: 'status', message: 'hi' })
    + frame({ type: 'response', success: true, response: 'R' }));
  const outcome = await done;
  assert.deepStrictEqual(seen.length, 1);
  assert.deepStrictEqual(seen[0].map((e) => e.type),
    ['session', 'status', 'response']);
  assert.deepStrictEqual(outcome.status, 'terminal');
  assert.deepStrictEqual(outcome.reason, 'final');
  assert.strictEqual(typeof outcome.elapsedMs, 'number');
  assert.strictEqual(s.stream.locked, false);
  assert.strictEqual(liveTimers(), 0);
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_timeout_settlement_race_keeps_one_read():
    # Controlled ordering pin: the timeout rejects its own race, then a
    # frame lands before the catch continuation runs. The retained read
    # must deliver it with no second native read.
    out = run_probe("""
(async () => {
  const s = makeStream();
  const counts = { reads: 0, resolutions: 0 };
  const seen = [];
  const done = api.readEvents(countingBody(s.stream, counts), {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents(batch) {
      seen.push({ batch, reads: counts.reads });
      return false;
    },
  });
  advance(8000); // timeout tick rejects its own race; read stays pending
  s.pushText(frame({ type: 'response', success: true, response: 'LATE' }));
  fireDue();
  await flush();
  assert.deepStrictEqual(seen.length, 1);
  assert.deepStrictEqual(seen[0].batch[0].response, 'LATE');
  // Pinned at frame-processing time: the retained promise delivered the
  // frame, so exactly one native read existed. (Later loop progress,
  // e.g. EOF discovery, legitimately reads again.)
  assert.deepStrictEqual(seen[0].reads, 1);
  s.close();
  const outcome = await done;
  assert.deepStrictEqual(outcome.status, 'eof');
  assert.strictEqual(s.stream.locked, false);
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_split_framing_reassembles():
    out = run_probe("""
(async () => {
  const s = makeStream();
  const seen = [];
  const done = api.readEvents(s.stream, {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents(batch) { seen.push(batch); return batch.some(
      (e) => e.type === 'response'); },
  });
  // Split multi-byte code point, then a split delimiter and JSON body.
  const raw = Buffer.from('data: ' + JSON.stringify(
    { type: 'response', success: true, response: 'A✓B' }) + '\\n\\n');
  const cut = raw.indexOf(Buffer.from('✓')) + 1;
  s.pushBytes(raw.slice(0, cut));
  advance(10);
  assert.deepStrictEqual(seen.length, 0);
  s.pushBytes(raw.slice(cut, raw.length - 1));
  advance(10);
  assert.deepStrictEqual(seen.length, 0);
  s.pushBytes(raw.slice(raw.length - 1));
  const outcome = await done;
  assert.deepStrictEqual(outcome.status, 'terminal');
  assert.deepStrictEqual(seen.length, 1);
  assert.deepStrictEqual(seen[0][0].response, 'A✓B');
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_malformed_frames_skipped():
    out = run_probe("""
(async () => {
  const s = makeStream();
  const seen = [];
  const done = api.readEvents(s.stream, {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents(batch) { seen.push(batch); return true; },
  });
  s.pushText('data: {not json}\\n\\n'
    + 'data: {"type": "mystery"}\\n\\n'
    + 'data:{"type": "response", "response": "NOSPACE"}\\n\\n'
    + frame({ type: 'response', response: 'GOOD' }));
  const outcome = await done;
  assert.deepStrictEqual(outcome.status, 'terminal');
  assert.deepStrictEqual(seen.length, 1);
  assert.deepStrictEqual(seen[0].length, 2); // mystery + GOOD
  assert.deepStrictEqual(seen[0][1].response, 'GOOD');
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_eof_releases_lock():
    out = run_probe("""
(async () => {
  const s = makeStream();
  let calls = 0;
  const done = api.readEvents(s.stream, {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents() { calls += 1; return false; },
  });
  s.pushText(frame({ type: 'status', message: 'x' }));
  await Promise.resolve();
  s.close();
  const outcome = await done;
  assert.deepStrictEqual(outcome, {
    status: 'eof', elapsedMs: outcome.elapsedMs, reason: 'eof' });
  assert.strictEqual(typeof outcome.elapsedMs, 'number');
  assert.strictEqual(calls, 1);
  assert.strictEqual(s.stream.locked, false);
  assert.strictEqual(liveTimers(), 0);
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_abort_rejects_and_cleans_up():
    out = run_probe("""
(async () => {
  // Pre-aborted signal never touches the stream.
  const s0 = makeStream();
  const ctl0 = new AbortController();
  ctl0.abort();
  await assert.rejects(api.readEvents(s0.stream, {
    holdMs: 60000, readTimeoutMs: 8000, signal: ctl0.signal,
    onEvents() { return false; },
  }), (e) => e && e.name === 'AbortError');
  assert.strictEqual(s0.stream.locked, false);
  // Mid-read abort fails the retained read as AbortError.
  const s = makeStream();
  const counts = { reads: 0, resolutions: 0 };
  const ctl = new AbortController();
  const done = api.readEvents(countingBody(s.stream, counts), {
    holdMs: 60000, readTimeoutMs: 8000, signal: ctl.signal,
    onEvents() { return false; },
  });
  advance(1000);
  ctl.abort();
  await assert.rejects(done, (e) => e && e.name === 'AbortError');
  assert.strictEqual(counts.reads, 1);
  assert.strictEqual(s.stream.locked, false);
  assert.strictEqual(liveTimers(), 0);
  // Abort racing an already-errored stream: cancel() rejects, which
  // the owner must observe (never an unhandled rejection), and the
  // turn still settles as AbortError via the flag.
  let unhandled = 0;
  process.on('unhandledRejection', () => { unhandled += 1; });
  const s2 = makeStream();
  const ctl2 = new AbortController();
  const done2 = api.readEvents(s2.stream, {
    holdMs: 60000, readTimeoutMs: 8000, signal: ctl2.signal,
    onEvents() { return false; },
  });
  s2.fail(new Error('boom-then-abort'));
  ctl2.abort();
  await assert.rejects(done2, (e) => e && e.name === 'AbortError');
  await flush();
  assert.strictEqual(s2.stream.locked, false);
  assert.strictEqual(unhandled, 0);
  console.log('ok');
})();
""")
    assert out == "ok"


@node_only
def test_chat_stream_hold_detach_cancels_reader():
    out = run_probe("""
(async () => {
  const s = makeStream();
  const counts = { reads: 0, resolutions: 0 };
  const done = api.readEvents(countingBody(s.stream, counts), {
    holdMs: 100, readTimeoutMs: 50,
    onEvents() { return false; },
  });
  advance(1000);
  const outcome = await done;
  assert.deepStrictEqual(outcome.status, 'detached');
  assert.ok(outcome.reason === 'hold' || outcome.reason === 'read-timeout');
  assert.ok(s.cancels >= 1);
  assert.strictEqual(s.stream.locked, false);
  assert.strictEqual(liveTimers(), 0);
  console.log('ok ' + outcome.reason);
})();
""")
    assert out.startswith("ok")


@node_only
def test_chat_stream_read_error_propagates_and_releases():
    out = run_probe("""
(async () => {
  const s = makeStream();
  const done = api.readEvents(s.stream, {
    holdMs: 60000, readTimeoutMs: 8000,
    onEvents() { return false; },
  });
  s.fail(new Error('boom-disconnect'));
  await assert.rejects(done, (e) => e && e.message === 'boom-disconnect');
  assert.strictEqual(s.stream.locked, false);
  assert.strictEqual(liveTimers(), 0);
  console.log('ok');
})();
""")
    assert out == "ok"
