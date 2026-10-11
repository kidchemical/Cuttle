"""Behavioral coverage for src/web/js/chat/chat_followup_queue.js under node.

Executes the real module (composing the real chat_activity.js item
decisions, never reimplemented) over enqueue/pause/remove/clear,
take-resolution branches, and reconcile guards. The adapter suite
below drives the real owner controller (``createController``) with fake
timers, transport, and host effects, pinning enqueue/steer/pause/resume/
drain/edit behavior and its guards.
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
const followupWriteField='followupWrite',followupClaimField='followupClaim';
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
        capture_output=True, text=True, encoding="utf-8", timeout=30,
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
    assert res["takeFailed"] == []
    assert res["takeFailedState"] == ["a", "b"]
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
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


MOD_ACT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_activity.js"

ADAPTER_HARNESS = """
(async () => {
const path = require('path');
const Q = require(process.env.MOD_FQ);
const A = require(process.env.MOD_ACT_JS);
const Mutations = require(path.join(path.dirname(process.env.MOD_FQ), 'chat_mutations.js'));
const sessionMutations = Mutations.create();
const CuttleChatSession = require(process.env.MOD_SESSION);
const session = CuttleChatSession.create(); session.adopt('s1');
const prefs = {};
function getPrefs(sid) { return prefs[sid] || null; }
function setPrefs(sid, p) { prefs[sid] = {...prefs[sid], ...p}; }
let generating = false, authSid = 's1';
let queueServer = [], revision = 0, fail = false, editDuringWrite = false, writes = 0;
const timers = new Map(); let nextTimer = 0;
const realSetTimeout = setTimeout;
const sent = [];
let steerResult = null; const steerCalls = []; const toasts = [];
function render() {}
function patchLiveSession() {}
function broadcast() {}
let ctl = null;
// The controller owns the queue lifecycle; the harness supplies every effect
// as an explicit host capability, mirroring the page wiring.
const host = {
  activity: A,
  clock: { now: () => Date.now(), rand: () => Math.random() },
  sessionIdsEqual: (a, b) => String(a) === String(b),
  canonicalizeSessionId: (id) => String(id),
  toAuthDbSessionId: (id) => String(id),
  currentSession: () => session.current(),
  isAuthMode: () => true,
  isSessionGenerating: () => generating,
  healStaleGenerating: () => {},
  followupAuthSid: () => authSid,
  getPrefs, setPrefs,
  writeField: () => 'followupWrite',
  claimField: () => 'followupClaim',
  restoreQueue: () => null,
  serializeMutation: (key, fn) => sessionMutations.enqueue(key, fn),
  putFollowups: async (sid, body) => {
    if (fail) throw Error('offline');
    if (editDuringWrite && ctl) ctl.beginEdit('e9');
    if (body.revision !== revision) {
      return { ok: false, status: 409, data: { success: false, conflict: true, followups: queueServer, revision } };
    }
    writes++; queueServer = body.followups; revision++;
    return { ok: true, status: 200, data: { success: true, followups: queueServer, revision } };
  },
  takeFollowups: async (sid, body) => {
    if (fail) throw Error('offline');
    if (body.revision !== revision) {
      return { ok: false, status: 409, data: { success: false, conflict: true, followups: queueServer, revision } };
    }
    const taken = queueServer.filter(x => !x.paused);
    queueServer = queueServer.filter(x => x.paused); revision++;
    return { ok: true, status: 200, data: { success: true, followups: taken, remaining: queueServer, revision } };
  },
  steer: async (sid, text) => { steerCalls.push({ session_id: sid, message: text }); return steerResult; },
  render, patchLiveSession, broadcastActivity: broadcast,
  showChatArea: () => {}, addUserMessage: () => {}, autoScroll: () => {},
  saveUserMessage: () => {}, recordPromptHistory: () => {},
  processMessage: async (message) => { sent.push(['send', message, []]); },
  toast: (m, k) => toasts.push([m, k]),
  steerLabel: (agent) => ({ codex: 'Codex', muse: 'Muse Code' })[agent],
  setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
  clearTimeout: (id) => timers.delete(id),
  formatWithAttachments: (t) => t,
  stickyPrefixFor: () => '',
};
const fireTimers = async () => { const due = [...timers.values()]; timers.clear(); for (const t of due) await t.fn(); };
const flush = () => new Promise(r => realSetTimeout(r, 0));
const reset = async (items = []) => {
  await flush();
  ctl = Q.createController(host);
  const st = ctl.getState();
  st.items = JSON.parse(JSON.stringify(items)); st.base = JSON.parse(JSON.stringify(items));
  queueServer = JSON.parse(JSON.stringify(items)); revision = 0; timers.clear(); sent.length = 0;
  fail = false; authSid = 's1'; writes = 0; generating = false;
  toasts.length = 0; steerCalls.length = 0; steerResult = null;
  session.adopt('s1');
};
const out = {};
await reset(); ctl.enqueue('first'); ctl.enqueue('second'); await flush();
out.enqueue = { n: ctl.getState().items.length, puts: writes };
await reset([{ id: 'x', content: 'x' }]); ctl.beginEdit('x'); ctl.requestDrain(); const blockedTimers = timers.size;
ctl.finishEdit({ resumeDrain: false }); ctl.requestDrain(); ctl.requestDrain();
out.schedule = { blockedTimers, timers: timers.size };
await reset([{ id: 'a', content: 'A' }, { id: 'r', content: 'R', paused: true }]); await ctl.drain();
out.drainTake = { sent: sent.slice(), n: ctl.getState().items.length, paused: ctl.getState().items.map(x => x.paused) };
await reset([{ id: 'p1', content: 'one' }, { id: 'p2', content: 'two', paused: true }]); fail = true; await ctl.drain();
out.drainFail = { sent: sent.slice(), n: ctl.getState().items.length };
await reset([{ id: 'q1', content: 'qq' }]); authSid = null; await ctl.drain();
out.drainLocal = { sent: sent.slice(), extraFetch: writes, n: ctl.getState().items.length };
await reset([{ id: 'e9', content: 'editme' }]); ctl.getState().dirty = true; editDuringWrite = true; await ctl.drain(); editDuringWrite = false;
out.editRace = { sent: sent.length, takeInFlight: ctl.getState().takeInFlight, n: ctl.getState().items.length };
await reset([{ id: 'w1', content: 'w' }]); ctl.togglePaused('w1'); const paused = ctl.getState().items[0].paused;
ctl.togglePaused('w1'); await flush(); out.pause = { paused, timersAfterResume: timers.size };
await reset([{ id: 'd1', content: 'd' }, { id: 'd2', content: 'e' }]); ctl.remove('d1'); ctl.clear(); await flush();
out.removeClear = { n: ctl.getState().items.length, puts: writes };
await reset(); ctl.getState().takeInFlight = true; ctl.applyServerList([{ content: 'x' }]); const guardedTake = ctl.getState().items.length;
ctl.getState().takeInFlight = false; ctl.getState().dirty = true; fail = true; ctl.applyServerList([{ content: 'x' }]); const guardedDirty = ctl.getState().items.length; await flush();
await reset([{ id: 'e', content: 'e' }]); ctl.beginEdit('e'); ctl.applyServerList([{ content: 'x' }]); const guardedEdit = ctl.getState().items.length;
ctl.finishEdit({ resumeDrain: false }); await flush(); ctl.applyServerList('nope'); const guardedInvalid = ctl.getState().items.length;
ctl.getState().items = [{ id: 'same', content: 'same' }]; ctl.applyServerList([{ id: 'same', content: 'same' }]); const guardedSame = ctl.getState().items.length;
ctl.applyServerList([{ content: '  spaced  ', paused: true }]);
out.applyGuards = { guardedTake, guardedDirty, guardedEdit, guardedInvalid, guardedSame, applied: ctl.getState().items.map(x => [x.content, x.paused]) };
// Edit transitions: soft-pause on begin, restore prior paused on finish/cancel,
// drain-timer clear on begin, resume drain when unpaused items remain.
await reset([{ id: 'e1', content: 'one' }, { id: 'e2', content: 'two', paused: true }]);
ctl.requestDrain(); const drainBeforeEdit = timers.size;
ctl.beginEdit('e1');
out.editBegin = { editing: ctl.getEditingId(), paused: ctl.getState().items[0].paused, drainBeforeEdit, timers: timers.size };
ctl.saveEdit('e1', '  edited text  '); await flush();
out.editSave = { editing: ctl.getEditingId(), raw: ctl.getState().items[0].rawMessage, content: ctl.getState().items[0].content };
await reset([{ id: 'e1', content: 'one' }]); ctl.beginEdit('e1'); ctl.saveEdit('e1', '   ');
out.editDropEmpty = { n: ctl.getState().items.length, editing: ctl.getEditingId() };
await reset([{ id: 'e1', content: 'one', paused: false }]); ctl.beginEdit('e1'); const softPause = ctl.getState().items[0].paused;
ctl.cancelEdit();
out.editCancel = { softPause, editing: ctl.getEditingId(), paused: ctl.getState().items[0].paused, timers: timers.size };
await reset([{ id: 'p', content: 'p', paused: true }]); ctl.beginEdit('p'); ctl.cancelEdit();
out.editPausedStays = ctl.getState().items[0].paused;
out.editableText = [Q.followupEditableText({ rawMessage: '/cursor hi', content: 'x' }), Q.followupEditableText({ content: 'body' })];
// Steering: steered turn paints + toasts; attachments/empty/no-steer fall back to queue.
await reset(); generating = true; session.adopt('s1');
steerResult = { steered: true, agent: 'codex' };
out.steer = await ctl.steer('hello', []);
out.steerCalls = steerCalls.slice();
out.steerToast = toasts.slice();
steerResult = { steered: false };
out.steerNoSteer = await ctl.steer('again', []);
out.steerAttachment = await ctl.steer('with att', [{ filename: 'a.png' }]);
out.steerEmpty = await ctl.steer('', []);
out.steerCallsAfter = steerCalls.length;
process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exitCode = 2; });
"""


def _run_adapter():
    import os
    proc = subprocess.run(
        ["node", "-e", ADAPTER_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={"PATH": os.environ["PATH"],
             "MOD_ACT_JS": str(MOD_ACT_JS),
             "MOD_SESSION": str(REPO_ROOT / "src" / "web" / "js" / "chat/chat_session.js"),
             "MOD_FQ": str(MOD_FQ)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_queue_adapter_enqueue_and_schedule():
    res = _run_adapter()
    assert res["enqueue"] == {"n": 2, "puts": 1}
    assert res["schedule"] == {"blockedTimers": 0, "timers": 1}


@node_only
def test_queue_adapter_drain_take_paths():
    res = _run_adapter()
    take = res["drainTake"]
    assert take["sent"] == [["send", "A", []]]
    assert take["n"] == 1 and take["paused"] == [True]
    fail = res["drainFail"]
    assert fail["sent"] == []
    assert fail["n"] == 2
    local = res["drainLocal"]
    assert local["sent"] == [["send", "qq", []]]
    assert local["extraFetch"] == 0 and local["n"] == 0
    assert res["editRace"] == {"sent": 0, "takeInFlight": False, "n": 1}


@node_only
def test_queue_adapter_pause_remove_clear_apply():
    res = _run_adapter()
    assert res["pause"] == {"paused": True, "timersAfterResume": 1}
    assert res["removeClear"] == {"n": 0, "puts": 1}
    guards = res["applyGuards"]
    assert guards["guardedTake"] == 0
    assert guards["guardedDirty"] == 0
    assert guards["guardedEdit"] == 1
    assert guards["guardedInvalid"] == 1
    assert guards["guardedSame"] == 1
    assert guards["applied"] == [["  spaced  ", True]]


@node_only
def test_queue_adapter_edit_and_steer():
    res = _run_adapter()
    begin = res["editBegin"]
    assert begin["editing"] == "e1"
    assert begin["paused"] is True  # soft-pause while editing
    assert begin["drainBeforeEdit"] == 1
    assert begin["timers"] == 0  # begin clears the pending drain timer
    assert res["editSave"] == {
        "editing": None, "raw": "edited text", "content": "edited text",
    }
    assert res["editDropEmpty"] == {"n": 0, "editing": None}
    cancel = res["editCancel"]
    assert cancel["softPause"] is True
    assert cancel["editing"] is None
    assert cancel["paused"] is False  # prior paused restored
    assert cancel["timers"] == 1  # unpaused item resumes the drain
    assert res["editPausedStays"] is True  # a paused item stays paused across begin/cancel
    assert res["editableText"] == ["/cursor hi", "body"]
    assert res["steer"] is True
    assert res["steerCalls"] == [{"session_id": "s1", "message": "hello"}]
    assert res["steerToast"] == [["Sent into the running Codex turn.", "success"]]
    assert res["steerNoSteer"] is False
    assert res["steerAttachment"] is False
    assert res["steerEmpty"] is False
    assert res["steerCallsAfter"] == 2  # attachments/empty never hit the endpoint
