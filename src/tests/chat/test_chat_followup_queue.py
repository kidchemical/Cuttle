"""Behavioral coverage for src/web/js/chat/chat_followup_queue.js under node.

Executes the real module (composing the real chat_activity.js item
decisions, never reimplemented) over enqueue/pause/remove/clear,
take-resolution branches, and reconcile guards. The adapter suite
below executes the real page queue/drain bodies with fake timers,
fetch, and DOM both before and after the rewire; identical assertions
must hold on both runs.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_FQ = REPO_ROOT / "src" / "web" / "js" / "chat/chat_followup_queue.js"
MOD_ACT = REPO_ROOT / "src" / "web" / "js" / "chat/chat_activity.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const Q = require(process.env.MOD_FQ);
const A = require(process.env.MOD_ACT);
const out = {};
const clock = (() => { let t = 1000, r = 0.123456789; return {
  now: () => (t += 7), rand: () => (r += 0.000000001) }; })();
const ids = (s) => s.items.map((x) => x.id);
// enqueue incl attachments/rawMessage/paused shaping + id uniqueness
const st = Q.createQueueState();
const a = Q.enqueue(st, { content: 'first', attachments: [{ f: 1 }],
  rawMessage: '/cursor first', paused: false }, clock);
const b = Q.enqueue(st, { content: 'second' }, clock);
const c = Q.enqueue(st, { content: 'third', paused: true }, clock);
out.enqueue = [st.items.length, a.id !== b.id && b.id !== c.id,
  a.rawMessage, a.attachments, b.rawMessage, b.attachments, c.paused,
  a.id.startsWith('fq_'), typeof a.created];
// pause/remove/clear
out.pause = [Q.setPaused(st, b.id, true) !== null, st.items[1].paused,
  Q.setPaused(st, 'nope', true)];
out.remove = [Q.removeItem(st, a.id), ids(st), Q.removeItem(st, a.id)];
Q.clearAll(st);
out.cleared = [st.items.length, st.dirty, st.takeInFlight];
// dirty/take flags
Q.markDirty(st); const d1 = st.dirty;
Q.markClean(st); const d2 = st.dirty;
Q.beginTake(st); const t1 = st.takeInFlight;
Q.endTake(st); const t2 = st.takeInFlight;
out.flags = [d1, d2, t1, t2];
// resolveTake branches
const mk = (items) => { const s = Q.createQueueState();
  s.items = items.map((x, i) => Object.assign({ id: 'q' + i }, x)); return s; };
let s1 = mk([{ content: 'a' }, { content: 'b', paused: true }]);
out.takeServer = Q.resolveTake(s1, null,
  { ok: true, followups: [{ content: 'A' }], remaining: [{ content: 'R' }] }, A);
out.takeServerState = [s1.items.length, s1.items[0].content];
let s2 = mk([{ content: 'a' }, { content: 'b', paused: true }]);
out.takeServerNoRemaining = Q.resolveTake(s2, null, { ok: true, followups: [{ content: 'A' }] }, A);
out.takeServerNoRemainingState = s2.items.map((x) => x.content);
let s3 = mk([{ content: 'a' }, { content: 'b', paused: true }]);
out.takeFailed = Q.resolveTake(s3, null, { ok: false }, A);
out.takeFailedState = s3.items.map((x) => x.content);
let s4 = mk([]);
out.takeFailedEmpty = [Q.resolveTake(s4, null, { ok: false }, A), s4.items.length];
let s5 = mk([{ content: 'a' }, { content: 'e', paused: true }]);
out.takeLocal = Q.resolveTake(s5, null, null, A);
out.takeLocalState = s5.items.map((x) => x.content);
let s6 = mk([]);
out.takeLocalEmpty = [Q.resolveTake(s6, null, null, A), s6.items.length];
// editing id stays queued on local partition
let s7 = mk([{ content: 'x' }]);
out.takeEditing = Q.resolveTake(s7, 'q0', null, A);
out.takeEditingState = s7.items.map((x) => x.id);
// reconcile guards
const rc = (items, list, opts, pre) => { const s = Q.createQueueState();
  s.items = items; Object.assign(s, pre || {}); return Q.reconcileServerList(s, list, opts, A); };
out.reconcile = [
  rc([], [{ content: 'n' }], {}),
  rc([{ content: 'a' }], [{ content: 'a' }], {}),
  rc([], [{ content: 'n' }], {}, { takeInFlight: true }),
  rc([], [{ content: 'n' }], {}, { dirty: true }),
  rc([], [{ content: 'n' }], { editingId: 'e1' }),
  rc([], 'nope', {}),
  rc([], [{ content: '  spaced  ', paused: true }], {}),
];
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_FQ": str(MOD_FQ),
             "MOD_ACT": str(MOD_ACT)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_enqueue_pause_remove_clear():
    res = _run()
    assert res["enqueue"] == [3, True, "/cursor first", [{"f": 1}],
                              "second", [], True, True, "number"]
    assert res["pause"] == [True, True, None]
    assert res["remove"][0] is True and res["remove"][2] is False
    assert len(res["remove"][1]) == 2
    assert res["cleared"] == [0, False, False]
    assert res["flags"] == [True, False, True, False]


@node_only
def test_resolve_take_branches():
    res = _run()
    assert res["takeServer"] == [{"content": "A"}]
    assert res["takeServerState"] == [1, "R"]
    assert res["takeServerNoRemaining"] == [{"content": "A"}]
    assert res["takeServerNoRemainingState"] == ["b"]
    assert res["takeFailed"] == [{"content": "a", "id": "q0"}]
    assert res["takeFailedState"] == ["b"]
    assert res["takeFailedEmpty"] == [[], 0]
    assert res["takeLocal"] == [{"content": "a", "id": "q0"}]
    assert res["takeLocalState"] == ["e"]
    assert res["takeLocalEmpty"] == [[], 0]
    assert res["takeEditing"] == []
    assert res["takeEditingState"] == ["q0"]


@node_only
def test_reconcile_guards():
    rec = _run()["reconcile"]
    assert rec[0] == {"applied": True}
    assert rec[1] == {"applied": False, "reason": "same"}
    assert rec[2] == {"applied": False, "reason": "busy"}
    assert rec[3] == {"applied": False, "reason": "busy"}
    assert rec[4] == {"applied": False, "reason": "editing"}
    assert rec[5] == {"applied": False, "reason": "invalid"}
    assert rec[6] == {"applied": True}


@node_only
def test_chat_followup_queue_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_FQ)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
MOD_ACT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_activity.js"

ADAPTER_HARNESS = """
(async () => {
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const fnBody = (name) => {
  // async first: 'function X(' is a substring of 'async function X('
  let start = SRC.indexOf('async function ' + name + '(');
  if (start < 0) start = SRC.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('missing fn ' + name);
  let pd = 0, brace = -1;
  for (let i = start; i < SRC.length; i++) {
    if (SRC[i] === '(') pd += 1;
    else if (SRC[i] === ')') { pd -= 1; if (pd === 0) { brace = SRC.indexOf('{', i); break; } }
  }
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    if (SRC[i] === '{') depth += 1;
    else if (SRC[i] === '}') { depth -= 1; if (depth === 0) return SRC.slice(start, i + 1); }
  }
  throw new Error('unbalanced ' + name);
};
// ---- live page-scope state: post-rewire the page holds ONE queue object
// (real module here, so the adapter suite is true page+module integration)
globalThis.CuttleFollowupQueue = require(process.env.MOD_FQ);
const followupQueue = CuttleFollowupQueue.createQueueState();
let followupDrainTimer = null;
let editingFollowupId = null;
let editingFollowupWasPaused = false;
let currentSessionId = 's1';
let generating = false;
let authSid = 's1';
// ---- fake timers: manual fire ----
let timerSeq = 0;
const timers = new Map();
const realSetTimeout = setTimeout;
globalThis.setTimeout = (fn, ms) => { const id = ++timerSeq; timers.set(id, { fn, ms }); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); };
const fireTimers = async () => {
  const due = [...timers.entries()];
  timers.clear();
  for (const [, t] of due) await t.fn();
};
// ---- scripted fetch ----
let fetchScript = [];
let fetchCalls = [];
globalThis.fetch = async (url, opts) => {
  fetchCalls.push([url, opts && opts.method, opts && opts.body]);
  const next = fetchScript.shift();
  if (next instanceof Error) throw next;
  if (typeof next === 'function') return next(url, opts);
  return { json: async () => next };
};
const okAppend = (item) => ({ success: true, followups: [item] });
// ---- DOM/effect stubs (leaves outside queue ownership) ----
const fx = [];
globalThis.CuttleChatActivity = require(process.env.MOD_ACT_JS);
globalThis.document = { getElementById: (id) => {
  if (id === 'chatArea') return { style: { display: 'flex' } };
  return { style: {} }; } };
function renderFollowupQueue() { fx.push(['render', followupQueue.items.length]); }
function patchLiveSessionFollowupQueue() {}
function isSessionGenerating() { return generating; }
function healStaleGeneratingState() { fx.push(['heal']); }
function scheduleChatActivityBroadcast() {}
function followupAuthSid() { return authSid; }
function getStickySlashCommandFromMessage() { return null; }
function formatMessageWithAttachments(text) { return String(text || ''); }
async function processMessage(msg, opts) { fx.push(['send', msg, (opts && opts.attachments) || []]); }
function addMessageToUI(text, role, o) { fx.push(['bubble', text, role]); }
function autoScrollChatToBottom() {}
function saveChatSession(text, role, o) { fx.push(['save', text, role]); }
function recordPromptHistory(text) { fx.push(['history', text]); }
// ---- the REAL page bodies under test ----
eval(fnBody('combineFollowupBatch'));
eval(fnBody('followupQueueFingerprint'));
eval(fnBody('enqueueFollowup'));
eval(fnBody('toggleFollowupPaused'));
eval(fnBody('removeFollowup'));
eval(fnBody('clearFollowupQueue'));
eval(fnBody('scheduleFollowupDrain'));
eval(fnBody('drainNextFollowup'));
eval(fnBody('applyServerFollowups'));
eval(fnBody('persistFollowupAppend'));
eval(fnBody('persistFollowupPut'));
const out = {};
const flush = () => new Promise((r) => realSetTimeout(r, 0));
// 1. enqueue two (one with attachments): items, render x2, POST x2, no drain timer
// server echoes the canonical list; each POST reconciles + schedules a drain
fetchScript = [{ success: true, followups: [{ id: 'f1', content: 'first' }] },
  { success: true, followups: [{ id: 'f1', content: 'first' }, { id: 'f2', content: 'second' }] }];
enqueueFollowup('first', { attachments: [{ f: 'a.png' }], rawMessage: '/cursor first' });
enqueueFollowup('second', {});
await flush();
out.enqueue = { n: followupQueue.items.length, timers: timers.size,
  posts: fetchCalls.filter((c) => c[1] === 'POST').length };
// 2. schedule gate: editing blocks, else one timer; re-schedule replaces
editingFollowupId = 'x';
const timersBeforeEdit = timers.size;
scheduleFollowupDrain(120);
const blockedTimers = timers.size - timersBeforeEdit;
editingFollowupId = null;
scheduleFollowupDrain(120); scheduleFollowupDrain(120);
out.schedule = { blockedTimers, timers: timers.size };
// 3. drain with server take success (remaining kept as-is)
followupQueue.items = []; timers.clear();
fetchScript = [{ success: true,
  followups: [{ content: 'A' }], remaining: [{ content: 'R', paused: true }] }];
scheduleFollowupDrain(0);
await fireTimers(); await flush();
out.drainTake = { sent: fx.filter((e) => e[0] === 'send'), n: followupQueue.items.length,
  paused: followupQueue.items.map((x) => x.paused) };
// 4. drain with take failure -> local partition fallback
followupQueue.items = [{ id: 'p1', content: 'one', paused: false }, { id: 'p2', content: 'two', paused: true }];
fetchScript = [new Error('down'), new Error('down')];
scheduleFollowupDrain(0);
await fireTimers(); await flush();
out.drainFail = { sent: fx.filter((e) => e[0] === 'send').slice(-1), n: followupQueue.items.length };
// 5. drain with no session id -> local partition, no fetch
authSid = null;
followupQueue.items = [{ id: 'q1', content: 'qq', paused: false }];
const callsBefore = fetchCalls.length;
scheduleFollowupDrain(0);
await fireTimers(); await flush();
out.drainLocal = { sent: fx.filter((e) => e[0] === 'send').slice(-1),
  extraFetch: fetchCalls.length - callsBefore, n: followupQueue.items.length };
authSid = 's1';
// 6. edit starts mid-PUT -> drain exits, no batch, take flag cleared
followupQueue.items = [{ id: 'e9', content: 'editme', paused: false }];
fetchScript = [async () => { editingFollowupId = 'e9'; return { json: async () => ({ success: true, followups: [] }) }; }];
scheduleFollowupDrain(0);
await fireTimers(); await flush();
out.editRace = { sent: fx.filter((e) => e[0] === 'send').length, takeInFlight: followupQueue.takeInFlight,
  n: followupQueue.items.length };
editingFollowupId = null;
// 7. pause excludes from batch; resume reschedules when idle
followupQueue.items = [{ id: 'w1', content: 'w', paused: false }];
toggleFollowupPaused('w1');
const pausedBatch = followupQueue.items[0].paused;
fetchScript = [{ success: true, followups: [] }];
toggleFollowupPaused('w1');
out.pause = { paused: pausedBatch, timersAfterResume: timers.size };
await fireTimers(); await flush();
// 8. remove + clear
followupQueue.items = [{ id: 'd1', content: 'd', paused: false }, { id: 'd2', content: 'e', paused: false }];
fetchScript = [{ success: true }, { success: true }];
const putsBefore = fetchCalls.filter((c) => c[1] === 'PUT').length;
removeFollowup('d1');
clearFollowupQueue();
await flush();
out.removeClear = { n: followupQueue.items.length,
  puts: fetchCalls.filter((c) => c[1] === 'PUT').length - putsBefore };
// 9. applyServerFollowups guards then apply
followupQueue.takeInFlight = true;
applyServerFollowups([{ content: 'x' }]);
const guardedTake = followupQueue.items.length;
followupQueue.takeInFlight = false; followupQueue.dirty = true;
applyServerFollowups([{ content: 'x' }]);
const guardedDirty = followupQueue.items.length;
followupQueue.dirty = false; editingFollowupId = 'e';
applyServerFollowups([{ content: 'x' }]);
const guardedEdit = followupQueue.items.length;
editingFollowupId = null;
applyServerFollowups('nope');
const guardedInvalid = followupQueue.items.length;
followupQueue.items = [{ id: 's1', content: 'same', paused: false }];
applyServerFollowups([{ id: 's1', content: 'same', paused: false }]);
const guardedSame = followupQueue.items.length;
applyServerFollowups([{ content: '  spaced  ', paused: true }]);
out.applyGuards = { guardedTake, guardedDirty, guardedEdit, guardedInvalid, guardedSame,
  applied: followupQueue.items.map((x) => [x.content, x.paused]) };
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run_adapter():
    import os
    proc = subprocess.run(
        ["node", "-e", ADAPTER_HARNESS],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ["PATH"], "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "MOD_ACT_JS": str(MOD_ACT_JS),
             "MOD_FQ": str(MOD_FQ)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_queue_adapter_enqueue_and_schedule():
    res = _run_adapter()
    assert res["enqueue"] == {"n": 2, "timers": 1, "posts": 2}
    assert res["schedule"] == {"blockedTimers": 0, "timers": 1}


@node_only
def test_queue_adapter_drain_take_paths():
    res = _run_adapter()
    take = res["drainTake"]
    assert take["sent"] == [["send", "A", []]]
    assert take["n"] == 1 and take["paused"] == [True]
    fail = res["drainFail"]
    assert fail["sent"] == [["send", "one", []]]
    assert fail["n"] == 1
    local = res["drainLocal"]
    assert local["sent"] == [["send", "qq", []]]
    assert local["extraFetch"] == 0 and local["n"] == 0
    assert res["editRace"] == {"sent": 3, "takeInFlight": False, "n": 1}


@node_only
def test_queue_adapter_pause_remove_clear_apply():
    res = _run_adapter()
    assert res["pause"] == {"paused": True, "timersAfterResume": 1}
    assert res["removeClear"] == {"n": 0, "puts": 2}
    guards = res["applyGuards"]
    assert guards["guardedTake"] == 0
    assert guards["guardedDirty"] == 0
    assert guards["guardedEdit"] == 0
    assert guards["guardedInvalid"] == 0
    assert guards["guardedSame"] == 1
    assert guards["applied"] == [["  spaced  ", True]]
