"""Stream/status transport owner frontend domain (4B).

Characterization of src/web/js/chat/chat_stream_transport.js: the page
drives the status poll, the connecting watchdog, pool-stall detection and
the typing-indicator status through this narrow decision interface while
keeping timers, DOM paint, fetch and recovery effects. chat_stream.js
stays the only SSE byte decoder.

The module is pure (no document/window/fetch/timers); these probes require
it under node and exercise the branch tables directly, plus load order.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_stream_transport.js"
HTML = REPO_ROOT / "src" / "web" / "chat_page.html"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def run_probe(body: str) -> str:
    script = (
        "const api = require(" + repr(str(MOD_JS)) + ");\n"
        "const assert = require('assert');\n"
        "(async () => {\n" + body + "\n})()"
        ".catch((e) => { console.error(e && e.stack || e); process.exit(2); });"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


@node_only
def test_chat_stream_transport_module_parses():
    proc = subprocess.run(
        ["node", "--check", str(MOD_JS)], capture_output=True, text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr


@node_only
def test_chat_stream_transport_script_tag_versioned_and_ordered():
    html = HTML.read_text(encoding="utf-8")
    tag = '<script src="/js/chat/chat_stream_transport.js?v='
    assert tag in html, "chat_stream_transport.js must load via versioned tag"
    assert html.index("chat_stream.js") < html.index(
        "chat_stream_transport.js") < html.index("chat_page.js?v="), \
        "load order: stream decoder before transport owner before the page"


@node_only
def test_chat_stream_transport_exports_interface():
    out = run_probe("""
  for (const n of ['createStatusPollState', 'planStatusPollStart',
      'planStatusPollLive', 'planTypingStatusUpdate', 'watchdogWantsLive',
      'planWatchdogAfterLive', 'planWatchdogProbe', 'poolStallSwitchMessage',
      'watchdogStallMessage', 'poolStallSameHost', 'poolStallTarget',
      'poolStallHttpTarget']) {
    assert.strictEqual(typeof api[n], 'function', n);
  }
  assert.strictEqual(api.STATUS_POLL_MS, 3500);
  assert.strictEqual(api.STAGNANT_TICKS, 24);
  assert.strictEqual(api.WATCHDOG_MS, 12000);
  assert.strictEqual(api.WATCHDOG_PROBE_MS, 4000);
  assert.strictEqual(api.POOL_STALL_PROBE_MS, 3000);
  console.log('ok');
""")


def test_chat_stream_transport_module_is_pure():
    """No DOM/network/timer global is referenced by the owner."""
    src = MOD_JS.read_text(encoding="utf-8")
    for forbidden in ("document.", "window.", "fetch(", "setTimeout",
                      "setInterval", "XMLHttpRequest"):
        assert forbidden not in src, forbidden


@node_only
def test_status_poll_start_gate():
    out = run_probe("""
  assert.deepStrictEqual(
    api.planStatusPollStart({loadingThisSession: false, sessionId: 'a',
      backgrounded: false}), {stop: true});
  assert.deepStrictEqual(
    api.planStatusPollStart({loadingThisSession: true, sessionId: null,
      backgrounded: false}), {stop: true});
  assert.deepStrictEqual(
    api.planStatusPollStart({loadingThisSession: true, sessionId: 'a',
      backgrounded: true}), {skip: true});
  assert.deepStrictEqual(
    api.planStatusPollStart({loadingThisSession: true, sessionId: 'a',
      backgrounded: false}), {fetch: true});
  console.log('ok');
""")


@node_only
def test_status_poll_live_paint_local_idle_recover_resync():
    out = run_probe("""
  // No live payload -> skip, no state mutation.
  const s0 = api.createStatusPollState();
  assert.deepStrictEqual(api.planStatusPollLive(s0, null, false), {skip: true});
  assert.deepStrictEqual(s0, {lastStatus: '', stagnantTicks: 0});

  // New status paints and resets stagnation; subagents/reportUrl forwarded.
  const s1 = api.createStatusPollState();
  const a = api.planStatusPollLive(s1,
    {status: 'tool 1', subagents: [{id: 1}], report_url: '/q?id=1'}, true);
  assert.strictEqual(a.paintStatus, 'tool 1');
  assert.deepStrictEqual(a.subagents, [{id: 1}]);
  assert.strictEqual(a.reportUrl, '/q?id=1');
  assert.strictEqual(a.recover, false);
  assert.deepStrictEqual(s1, {lastStatus: 'tool 1', stagnantTicks: 0});

  // Repeated status bumps the stagnation counter without repaint decided.
  api.planStatusPollLive(s1, {status: 'tool 1', subagents: null, report_url: null}, true);
  assert.strictEqual(s1.stagnantTicks, 1);

  // Not active server-side -> stop + recover, still paints status/links.
  const s2 = api.createStatusPollState();
  const r = api.planStatusPollLive(s2,
    {status: 'done?', subagents: null, report_url: '/q?id=2'}, false);
  assert.strictEqual(r.stop, true);
  assert.strictEqual(r.recover, true);
  assert.strictEqual(r.paintStatus, 'done?');
  assert.strictEqual(r.reportUrl, '/q?id=2');

  // Stagnant while active -> one resync at the threshold, then reset.
  const s3 = api.createStatusPollState();
  let resyncs = 0;
  for (let i = 0; i < api.STAGNANT_TICKS + 3; i++) {
    const p = api.planStatusPollLive(s3,
      {status: 'same', subagents: null, report_url: null}, true);
    if (p.resync) resyncs += 1;
  }
  assert.strictEqual(resyncs, 1);
  assert.strictEqual(s3.stagnantTicks, 2);
  console.log('ok');
""")


@node_only
def test_status_poll_no_status_does_not_touch_state():
    out = run_probe("""
  const s = api.createStatusPollState();
  const p = api.planStatusPollLive(s,
    {subagents: [{id: 2}]}, true);
  assert.strictEqual(p.paintStatus, null);
  assert.deepStrictEqual(p.subagents, [{id: 2}]);
  assert.deepStrictEqual(s, {lastStatus: '', stagnantTicks: 0});
  console.log('ok');
""")


@node_only
def test_typing_status_update_decisions():
    out = run_probe("""
  // No indicator: clear watchdog, never paint.
  assert.deepStrictEqual(
    api.planTypingStatusUpdate({statusText: 'x', hasIndicator: false,
      currentText: null}),
    {text: 'x', paint: false, clearWatchdog: true, armWatchdog: false});

  // Same text as default "Connecting..." -> keep watchdog armed, no paint.
  assert.deepStrictEqual(
    api.planTypingStatusUpdate({statusText: null, hasIndicator: true,
      currentText: 'Connecting...'}),
    {text: 'Connecting...', paint: false, clearWatchdog: false,
     armWatchdog: false});

  // Same real text -> clear watchdog, no paint.
  assert.deepStrictEqual(
    api.planTypingStatusUpdate({statusText: 'working', hasIndicator: true,
      currentText: 'working'}),
    {text: 'working', paint: false, clearWatchdog: true, armWatchdog: false});

  // Changed text -> clear + paint, no re-arm.
  assert.deepStrictEqual(
    api.planTypingStatusUpdate({statusText: 'next', hasIndicator: true,
      currentText: 'prev'}),
    {text: 'next', paint: true, clearWatchdog: true, armWatchdog: false});

  // Empty/default with an indicator -> paint default and re-arm.
  assert.deepStrictEqual(
    api.planTypingStatusUpdate({statusText: '', hasIndicator: true,
      currentText: 'prev'}),
    {text: 'Connecting...', paint: true, clearWatchdog: true,
     armWatchdog: true});
  console.log('ok');
""")


@node_only
def test_watchdog_label_live_and_probe_decisions():
    out = run_probe("""
  assert.strictEqual(api.watchdogWantsLive('Connecting...'), true);
  assert.strictEqual(api.watchdogWantsLive('Still connecting — checking server…'), true);
  assert.strictEqual(api.watchdogWantsLive('thinking: work'), false);
  assert.strictEqual(api.watchdogWantsLive(''), false);

  assert.deepStrictEqual(api.planWatchdogAfterLive(null), {kind: 'probe'});
  assert.deepStrictEqual(
    api.planWatchdogAfterLive({active: true, status: 'working'}),
    {kind: 'live', status: 'working', reportUrl: null});
  assert.deepStrictEqual(
    api.planWatchdogAfterLive({generating: true, status: 'working',
      report_url: '/q?id=9'}),
    {kind: 'live', status: 'working', reportUrl: '/q?id=9'});
  assert.deepStrictEqual(
    api.planWatchdogAfterLive({active: true, status: ''}), {kind: 'probe'});
  assert.deepStrictEqual(
    api.planWatchdogAfterLive({active: false, generating: false,
      status: 'stale'}), {kind: 'probe'});

  assert.deepStrictEqual(
    api.planWatchdogProbe({ok: true, status: 200, hasIndicator: false,
      stillConnecting: true}), {abort: true});
  assert.deepStrictEqual(
    api.planWatchdogProbe({ok: true, status: 200, hasIndicator: true,
      stillConnecting: false}), {abort: true});
  assert.deepStrictEqual(
    api.planWatchdogProbe({ok: true, status: 200, hasIndicator: true,
      stillConnecting: true}),
    {message: 'Server is up — waiting for chat stream…'});
  assert.deepStrictEqual(
    api.planWatchdogProbe({ok: false, status: 503, hasIndicator: true,
      stillConnecting: true}),
    {message: 'Server health HTTP 503 — try refreshing or restart Flask'});
  console.log('ok');
""")


@node_only
def test_stall_messages_and_pool_target():
    out = run_probe("""
  assert.strictEqual(api.poolStallSwitchMessage(8001),
    'HTTPS pool stalled — switching to HTTP portal (:8001)…');
  assert.ok(api.watchdogStallMessage(true).indexOf('Electron') >= 0);
  assert.ok(api.watchdogStallMessage(false).indexOf('stream may be stalled') >= 0);

  assert.strictEqual(api.poolStallSameHost('127.0.0.1', 'localhost', true), true);
  assert.strictEqual(api.poolStallSameHost('192.168.1.9', 'localhost', true), false);
  assert.deepStrictEqual(api.poolStallTarget('h', 8000),
    {probeUrl: 'http://h:8000/api/status', destBase: 'http://h:8000', httpPort: 8000});

  // Legacy default-mode plain browser keeps :8000.
  const t = await api.poolStallHttpTarget(
    {origin: 'https://127.0.0.1:8080', getDesktopConfig: async () => null});
  assert.deepStrictEqual(t,
    {probeUrl: 'http://127.0.0.1:8000/api/status',
     destBase: 'http://127.0.0.1:8000', httpPort: 8000});
  // Explicit single never downgrades.
  assert.strictEqual(await api.poolStallHttpTarget({
    origin: 'https://127.0.0.1:8080',
    getDesktopConfig: async () => ({endpoint: {kind: 'single'}})}), null);
  console.log('ok');
""")
