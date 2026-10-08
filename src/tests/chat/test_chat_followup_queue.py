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
globalThis.CuttleFollowupQueue = require(process.env.MOD_FQ);
globalThis.CuttleChatActivity = require(process.env.MOD_ACT_JS);
const Mutations = require(require('path').join(require('path').dirname(process.env.MOD_FQ),'chat_mutations.js'));
const sessionMutations = Mutations.create();
const followupWriteField='followupWrite',followupClaimField='followupClaim';
let followupQueue = CuttleFollowupQueue.createQueueState();
let followupDrainTimer = null, editingFollowupId = null, editingFollowupWasPaused = false;
let currentSessionId = 's1', generating = false, authSid = 's1';
let queueServer=[], revision=0, fail=false, editDuringWrite=false, writes=0;
const prefs = {};
function updateSessionPrefs(sid,p) { prefs[sid]={...prefs[sid],...p}; }
function getSessionPrefs(sid) { return prefs[sid] || null; }
function sessionIdsEqual(a,b) {return String(a)===String(b);}
const timers=new Map(); let nextTimer=0;
const realSetTimeout=setTimeout;
globalThis.setTimeout=(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
const fireTimers=async()=>{const due=[...timers.values()];timers.clear();for(const t of due)await t.fn();};
const sent=[];
globalThis.fetch=async(url,opts)=>{
 if(fail)throw Error('offline');
 const body=JSON.parse(opts.body || '{}');
 if(editDuringWrite && opts.method==='PUT')editingFollowupId='e9';
 if(body.revision!==revision)return {ok:false,status:409,json:async()=>({success:false,conflict:true,followups:queueServer,revision})};
 if(url.endsWith('/take')) {
   const taken=queueServer.filter(x=>!x.paused);queueServer=queueServer.filter(x=>x.paused);revision++;
   return {ok:true,status:200,json:async()=>({success:true,followups:taken,remaining:queueServer,revision})};
 }
 writes++;queueServer=body.followups;revision++;
 return {ok:true,status:200,json:async()=>({success:true,followups:queueServer,revision})};
};
function renderFollowupQueue() {}
function patchLiveSessionFollowupQueue() {}
function isSessionGenerating() {return generating;}
function healStaleGeneratingState() {}
function scheduleChatActivityBroadcast() {}
function followupAuthSid() {return authSid;}
function getStickySlashCommandFromMessage() {return null;}
function formatMessageWithAttachments(text) {return text;}
async function processMessage(message) {sent.push(['send',message,[]]);}
function addMessageToUI() {}
function autoScrollChatToBottom() {}
function saveChatSession() {}
function recordPromptHistory() {}
const document={getElementById:()=>({style:{display:'flex'}})};
for(const name of ['combineFollowupBatch','followupQueueFingerprint','rememberUnsyncedQueue',
 'enqueueFollowup','toggleFollowupPaused','removeFollowup','clearFollowupQueue','scheduleFollowupDrain',
 'drainNextFollowup','applyServerFollowups','persistFollowupAppend','persistFollowupPut'])eval(fnBody(name));
const flush=()=>new Promise(r=>realSetTimeout(r,0));
const reset=(items=[])=>{
 followupQueue=CuttleFollowupQueue.createQueueState();
 followupQueue.items=JSON.parse(JSON.stringify(items));followupQueue.base=JSON.parse(JSON.stringify(items));
 queueServer=JSON.parse(JSON.stringify(items));revision=0;timers.clear();sent.length=0;
 editingFollowupId=null;fail=false;authSid='s1';writes=0;
};
const out={};
reset();enqueueFollowup('first');enqueueFollowup('second');await flush();
out.enqueue={n:followupQueue.items.length,puts:writes};
editingFollowupId='x';scheduleFollowupDrain();const blockedTimers=timers.size;
editingFollowupId=null;scheduleFollowupDrain();scheduleFollowupDrain();
out.schedule={blockedTimers,timers:timers.size};
reset([{id:'a',content:'A'},{id:'r',content:'R',paused:true}]);await drainNextFollowup();
out.drainTake={sent:sent.slice(),n:followupQueue.items.length,paused:followupQueue.items.map(x=>x.paused)};
reset([{id:'p1',content:'one'},{id:'p2',content:'two',paused:true}]);fail=true;await drainNextFollowup();
out.drainFail={sent:sent.slice(),n:followupQueue.items.length};
reset([{id:'q1',content:'qq'}]);authSid=null;await drainNextFollowup();
out.drainLocal={sent:sent.slice(),extraFetch:writes,n:followupQueue.items.length};
reset([{id:'e9',content:'editme'}]);followupQueue.dirty=true;editDuringWrite=true;await drainNextFollowup();editDuringWrite=false;
out.editRace={sent:sent.length,takeInFlight:followupQueue.takeInFlight,n:followupQueue.items.length};
reset([{id:'w1',content:'w'}]);toggleFollowupPaused('w1');const paused=followupQueue.items[0].paused;
toggleFollowupPaused('w1');await flush();out.pause={paused,timersAfterResume:timers.size};
reset([{id:'d1',content:'d'},{id:'d2',content:'e'}]);removeFollowup('d1');clearFollowupQueue();await flush();
out.removeClear={n:followupQueue.items.length,puts:writes};
reset();followupQueue.takeInFlight=true;applyServerFollowups([{content:'x'}]);const guardedTake=followupQueue.items.length;
followupQueue.takeInFlight=false;followupQueue.dirty=true;fail=true;applyServerFollowups([{content:'x'}]);const guardedDirty=followupQueue.items.length;await flush();
followupQueue.dirty=false;editingFollowupId='e';applyServerFollowups([{content:'x'}]);const guardedEdit=followupQueue.items.length;
editingFollowupId=null;applyServerFollowups('nope');const guardedInvalid=followupQueue.items.length;
followupQueue.items=[{id:'same',content:'same'}];applyServerFollowups([{id:'same',content:'same'}]);const guardedSame=followupQueue.items.length;
applyServerFollowups([{content:'  spaced  ',paused:true}]);
out.applyGuards={guardedTake,guardedDirty,guardedEdit,guardedInvalid,guardedSame,applied:followupQueue.items.map(x=>[x.content,x.paused])};
process.stdout.write(JSON.stringify(out));
})().catch(e=>{console.error(e);process.exitCode=2;});

"""


def _run_adapter():
    import os
    proc = subprocess.run(
        ["node", "-e", ADAPTER_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={"PATH": os.environ["PATH"], "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "MOD_ACT_JS": str(MOD_ACT_JS),
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
    assert guards["guardedEdit"] == 0
    assert guards["guardedInvalid"] == 0
    assert guards["guardedSame"] == 1
    assert guards["applied"] == [["  spaced  ", True]]
