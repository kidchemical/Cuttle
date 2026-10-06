"""Behavioral coverage for src/web/js/chat/chat_stop_state.js under node.

Executes the real module over the flag transition table, stop/send/
detach race sequences, and the send-abort classification matrix. The
adapter suite below (`ADAPTER_HARNESS`) executes the real page
stop/detach/begin paths with fake transports both before and after
the rewire; the pre/post outputs must match exactly.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_SS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_stop_state.js"
MOD_GEN = REPO_ROOT / "src" / "web" / "js" / "chat/chat_generation.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const S = require(process.env.MOD_SS);
const out = {};
const snap = (s) => [s.userStopped, s.abortSuppressed, s.waitingSuppressed];
// transition table from a fresh state
const s = S.createStopState();
out.fresh = snap(s);
S.requestStop(s); out.stop = snap(s);
S.requestStop(s); out.stopTwice = snap(s);
S.beginSend(s); out.sendAfterStop = snap(s);
S.markStreamDetached(s); out.detachAfterSend = snap(s);
S.clearAbortSuppression(s); out.beginGen = snap(s);
S.requestStop(s); S.clearWaitingSuppression(s); out.newChat = snap(s);
// detach without a stop: only the abort latch sets
const d = S.createStopState();
S.markStreamDetached(d); out.detachFresh = snap(d);
S.beginSend(d); out.sendAfterDetach = snap(d);
// full race: send -> detach -> send -> stop -> send -> stop
const r = S.createStopState();
const seq = [];
S.beginSend(r); seq.push(snap(r));
S.markStreamDetached(r); seq.push(snap(r));
S.beginSend(r); seq.push(snap(r));
S.requestStop(r); seq.push(snap(r));
S.beginSend(r); seq.push(snap(r));
S.requestStop(r); seq.push(snap(r));
out.race = seq;
// abort classification matrix
const cls = (flags, name) => {
  const t = S.createStopState();
  Object.assign(t, flags);
  return S.classifySendAbort(t, name);
};
out.classify = {
  detached: cls({ abortSuppressed: true }, 'AbortError'),
  detachedNoAbortName: cls({ abortSuppressed: true }, 'TypeError'),
  stopped: cls({ userStopped: true }, 'AbortError'),
  stoppedClearsSuppressedAbort: cls({ userStopped: true, abortSuppressed: true }, 'AbortError'),
  dropped: cls({}, 'AbortError'),
  other: cls({}, 'TypeError'),
  otherStopped: cls({ userStopped: true }, 'TypeError'),
  undefName: cls({}, undefined),
};
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_SS": str(MOD_SS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_stop_transitions_and_idempotence():
    res = _run()
    assert res["fresh"] == [False, False, False]
    assert res["stop"] == [True, True, True]
    assert res["stopTwice"] == [True, True, True]
    # a new send clears stop latches but keeps orphan suppression
    assert res["sendAfterStop"] == [False, True, False]
    assert res["detachAfterSend"] == [False, True, False]
    assert res["beginGen"] == [False, False, False]
    assert res["newChat"] == [True, True, False]
    assert res["detachFresh"] == [False, True, False]
    assert res["sendAfterDetach"] == [False, True, False]


@node_only
def test_stop_send_detach_race_sequence():
    assert _run()["race"] == [
        [False, False, False],
        [False, True, False],
        [False, True, False],
        [True, True, True],
        [False, True, False],
        [True, True, True],
    ]


@node_only
def test_send_abort_classification():
    c = _run()["classify"]
    assert c["detached"] == "detached"
    # suppression alone without an abort is not a detach recovery
    assert c["detachedNoAbortName"] == "other"
    assert c["stopped"] == "stopped"
    # navigation abort wins over a stale stop latch (matches page order)
    assert c["stoppedClearsSuppressedAbort"] == "detached"
    assert c["dropped"] == "dropped"
    assert c["other"] == "other"
    assert c["otherStopped"] == "stopped"
    assert c["undefName"] == "other"


@node_only
def test_chat_stop_state_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_SS)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

ADAPTER_HARNESS = """
(async () => {
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const fnBody = (name) => {
  let start = SRC.indexOf('function ' + name + '(');
  if (start < 0) start = SRC.indexOf('async function ' + name + '(');
  if (start < 0) throw new Error('missing fn ' + name);
  // body brace = first { after the parameter list closes (skips opts = {})
  let pd = 0, brace = -1;
  for (let i = start; i < SRC.length; i++) {
    if (SRC[i] === '(') pd += 1;
    else if (SRC[i] === ')') { pd -= 1; if (pd === 0) { brace = SRC.indexOf('{', i); break; } }
  }
  if (brace < 0) throw new Error('no body ' + name);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    if (SRC[i] === '{') depth += 1;
    else if (SRC[i] === '}') { depth -= 1; if (depth === 0) return SRC.slice(start, i + 1); }
  }
  throw new Error('unbalanced ' + name);
};
// ---- live page-scope state the real bodies read/write ----
// Post-rewire the page holds ONE stop-state object (real module here,
// so the adapter suite executes the true page+module integration).
globalThis.CuttleStopState = require(process.env.MOD_SS);
const stopState = CuttleStopState.createStopState();
globalThis.CuttleChatGeneration = require(process.env.MOD_GEN);
const generation = CuttleChatGeneration.createGenerationState();
let currentSessionId = 's1';
let inFlightUserMessage = null;
let activeRequestController = null;
let activeEventSource = null;
let followupDrainTimer = null;
let messageSyncTimer = null;
// queue contents live in the owned module state (Slice 9C)
globalThis.CuttleFollowupQueue = require(process.env.MOD_FQ);
const followupQueue = CuttleFollowupQueue.createQueueState();
let sendDispatchGuard = true;
let voiceModeActive = false;
let voiceModePhase = '';
// ---- effect log + stubs for leaves outside stop-state ownership ----
const fx = [];
const mkCtrl = (throwing) => ({ aborted: 0,
  abort() { if (throwing) throw new Error('abort boom'); this.aborted++; } });
const mkSSE = (throwing) => ({ closed: 0,
  close() { if (throwing) throw new Error('close boom'); this.closed++; } });
const mkEl = () => ({ disabled: false, style: {}, value: '' });
globalThis.document = { getElementById: () => mkEl() };
function retainStoppedAgentBubble() { fx.push(['bubble', 'stopped']); }
function ensureGenerationStopNotice(t) { fx.push(['notice', t]); }
function removeTypingIndicator() { fx.push(['typing', 'off']); }
function removeRemoteWaitingIndicator() { fx.push(['remote', 'off']); }
function setHistorySessionRunning(id, r) { fx.push(['running', id, r]); }
function setWelcomeComposerEnabled(b) { fx.push(['welcome', b]); }
function refocusChatInputIfAppropriate() {}
function clearStreamStatusPoll() { fx.push(['poll', 'clear']); }
function watchDetachedSessionForCompletion(id) { fx.push(['watch', id]); }
function startMessageSync() { fx.push(['sync', 'start']); }
function scheduleNextMessageSync() { fx.push(['sync', 'next']); }
function stopMessageSync() { fx.push(['sync', 'stop']); }
function runningWatchJobIds() { return []; }
function isProjectShellTurn() { return false; }
async function requestServerCancelCurrentRun(o) { fx.push(['cancel', o.cancelJobs, o.jobIds]); return {}; }
function syncComposerStopWithWatch() {}
function scheduleFollowupDrain(ms) { fx.push(['drain', ms]); }
function authSessionIdForRequest() { return currentSessionId; }
function setVoiceModePhase() {}
// ---- the REAL page bodies under test (9A-rewired: needs the guard) ----
globalThis.CuttleTurnGuard = require(process.env.MOD_TG);
let turnGeneration = CuttleTurnGuard.createGeneration();
eval(fnBody('endLocalGeneration'));
eval(fnBody('detachLocalGenerationForNavigation'));
eval(fnBody('releaseLocalStreamForShellPause'));
eval(fnBody('beginLocalGeneration'));
eval(fnBody('finishLocalStreamFromServerSync'));
eval(fnBody('stopGenerating'));
const flags = () => [stopState.userStopped, stopState.abortSuppressed, stopState.waitingSuppressed];
const flush = () => new Promise((r) => setTimeout(r, 0));
const out = {};
// 1. idle stop: flags latch, notice, server cancel without jobs
await stopGenerating(); await flush();
out.idle = { flags: flags(), fx: fx.splice(0) };
// 2. stop with live transports + in-flight turn
activeRequestController = mkCtrl(false); activeEventSource = mkSSE(false);
inFlightUserMessage = 'hello';
await stopGenerating(); await flush();
out.live = { flags: flags(), ctrlAborted: 1, sseClosed: 1,
  ctrlNull: activeRequestController === null, sseNull: activeEventSource === null,
  inflightNull: inFlightUserMessage === null, fx: fx.splice(0) };
// 3. repeated stop: transports already gone, latches re-asserted
await stopGenerating(); await flush();
out.repeat = { flags: flags(), fx: fx.splice(0) };
// 4. throwing transports are swallowed, stop still completes
activeRequestController = mkCtrl(true); activeEventSource = mkSSE(true);
await stopGenerating(); await flush();
out.throwing = { flags: flags(),
  ctrlNull: activeRequestController === null, sseNull: activeEventSource === null };
// 5. detach with live transports (navigation keeps server run)
stopState.userStopped = false; stopState.abortSuppressed = false; stopState.waitingSuppressed = false;
generation.loading = true; generation.localSessionId = 's1';
activeRequestController = mkCtrl(false); activeEventSource = mkSSE(false);
detachLocalGenerationForNavigation();
out.detach = { flags: flags(), loading: generation.loading, genSession: generation.localSessionId,
  ctrlNull: activeRequestController === null, sseNull: activeEventSource === null,
  inflightNull: inFlightUserMessage === null, fx: fx.splice(0) };
// 6. shell pause vs teardown
releaseLocalStreamForShellPause(false);
out.pause = { flags: flags(), fx: fx.splice(0) };
releaseLocalStreamForShellPause(true);
out.teardown = { flags: flags(), fx: fx.splice(0) };
// 7. begin lifts suppression and marks running
beginLocalGeneration();
out.begin = { flags: flags(), loading: generation.loading, genSession: generation.localSessionId, fx: fx.splice(0) };
// 8. server-sync finish: noop when idle; teardown + drain when live
generation.loading = false;
finishLocalStreamFromServerSync();
out.finishIdle = { flags: flags(), fx: fx.splice(0) };
generation.loading = true;
activeRequestController = mkCtrl(false); activeEventSource = mkSSE(false);
followupQueue.items = [{ id: 'q1' }];
finishLocalStreamFromServerSync();
out.finishLive = { flags: flags(), loading: generation.loading, fx: fx.splice(0) };
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run_adapter():
    import os
    proc = subprocess.run(
        ["node", "-e", ADAPTER_HARNESS],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ["PATH"], "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "MOD_TG": str(REPO_ROOT / "src" / "web" / "js" / "chat/chat_turn_guard.js"),
             "MOD_SS": str(MOD_SS),
             "MOD_GEN": str(MOD_GEN),
             "MOD_FQ": str(REPO_ROOT / "src" / "web" / "js" / "chat/chat_followup_queue.js")},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_stop_adapter_idle_and_live_effects():
    res = _run_adapter()
    idle = res["idle"]
    assert idle["flags"] == [True, True, True]
    assert ["notice", "⏹ Stopped generating."] in idle["fx"]
    assert idle["fx"].index(["bubble", "stopped"]) < idle["fx"].index(["notice", "⏹ Stopped generating."])
    assert ["cancel", False, []] in idle["fx"]
    live = res["live"]
    assert live["flags"] == [True, True, True]
    assert live["ctrlNull"] and live["sseNull"] and live["inflightNull"]
    assert live["fx"].count(["typing", "off"]) == 1
    assert res["repeat"]["flags"] == [True, True, True]
    assert res["throwing"]["flags"] == [True, True, True]
    # controller nulls outside the try; a throwing close skips its own null.
    assert res["throwing"]["ctrlNull"] is True
    assert res["throwing"]["sseNull"] is False


@node_only
def test_stop_adapter_detach_pause_begin_finish():
    res = _run_adapter()
    detach = res["detach"]
    assert detach["flags"] == [False, True, False]
    assert detach["loading"] is False and detach["genSession"] is None
    assert detach["ctrlNull"] and detach["sseNull"] and detach["inflightNull"]
    assert res["pause"]["flags"] == [False, True, False]
    assert ["sync", "start"] in res["pause"]["fx"]
    assert ["sync", "stop"] in res["teardown"]["fx"]
    assert ["sync", "start"] not in res["teardown"]["fx"]
    begin = res["begin"]
    assert begin["flags"] == [False, False, False]
    assert begin["loading"] is True and begin["genSession"] == "s1"
    assert ["running", "s1", True] in begin["fx"]
    assert res["finishIdle"] == {"flags": [False, False, False], "fx": []}
    finish = res["finishLive"]
    assert finish["flags"] == [False, True, False]
    assert ["drain", 80] in finish["fx"]


@node_only
def test_chat_stop_state_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat/chat_stop_state.js?v='
    assert tag in html, "chat_stop_state.js must load via versioned script tag"
    assert html.index("chat_turn_guard.js") < html.index("chat_stop_state.js") < html.index(
        "chat_page.js?v="
    ), "load order: owned modules before the page orchestrator"


@node_only
def test_stopped_bubble_retains_query_target_and_failed_badge():
    import os
    harness = ADAPTER_HARNESS[:ADAPTER_HARNESS.index('// ---- live page-scope state')]
    harness += r"""
const make = (pending) => {
  const removed = [];
  const classes = new Set(pending ? ['is-pending'] : []);
  const link = { href: pending ? '/query_log.html' : '/query_log.html?id=turn123',
    dataset: pending ? {} : {queryId: 'turn123'},
    removeAttribute: (k) => removed.push(k), classList: {remove: (k) => classes.delete(k)} };
  const chip = {dataset: {fullLabel: 'Codex'}, classList: {add: (k) => classes.add(k)}, setAttribute() {} };
  const status = {textContent: 'Thinking', removeAttribute: (k) => removed.push(k)};
  const bubble = {dataset: {}, classList: {remove: (k) => classes.delete(k)}, removeAttribute: (k) => removed.push(k),
    querySelector: (q) => q === '.typing-status' ? status : q === '.message-query-log-link' ? link : {remove() {removed.push('animation');}},
    querySelectorAll: () => [chip]};
  return {bubble, link, classes, removed, status};
};
const ready = make(false), pending = make(true);
globalThis.document = {querySelectorAll: () => [ready.bubble, pending.bubble]};
function clearConnectingWatchdog() {}
function clearStreamStatusPoll() {}
eval(fnBody('retainStoppedAgentBubble'));
retainStoppedAgentBubble();
process.stdout.write(JSON.stringify([ready, pending].map(x => ({
  stopped: x.bubble.dataset.stopped, href: x.link.href, title: x.link.title,
  status: x.status.textContent, classes: [...x.classes], removed: x.removed
}))));
})().catch(e => {console.error(e); process.exit(2);});
"""
    proc = subprocess.run(['node', '-e', harness], capture_output=True, text=True,
                          env={**os.environ, 'CHAT_PAGE_JS': str(CHAT_PAGE_JS)}, timeout=30)
    assert proc.returncode == 0, proc.stderr
    ready, pending = json.loads(proc.stdout)
    assert ready['href'] == '/query_log.html?id=turn123'
    assert pending['href'] == '/query_log.html'
    for result in (ready, pending):
        assert result['stopped'] == 'true'
        assert result['status'] == 'Agent stopped.'
        assert 'slash-command-chip--error' in result['classes']
        assert 'is-pending' not in result['classes']
        assert 'animation' in result['removed']
        assert 'id' in result['removed']
        assert 'data-message-index' in result['removed']
        assert 'data-pin-key' in result['removed']
        assert 'aria-disabled' in result['removed']
