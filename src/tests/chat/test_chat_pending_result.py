"""Behavioral coverage for src/web/js/chat/chat_pending_result.js under node.

Executes the real lifecycle module (pending-result waiter, history
recovery, sync exactly-once classification, stale-heal decision, SSE event
classification, send-failure recovery, post-restart poll) with scripted
fake fetch/clock/timers. Values marked ORACLE were captured by executing
the pre-extraction page functions (collectPendingResult,
recoverChatResultFromServer, healStaleGeneratingState) with equivalent
scripted fakes before the move; identical assertions must hold after
rewiring. Page wiring itself is pinned structurally below (delegation
call-sites + script tag); timers, transport URLs, DOM, persistence, and
the busy lock stay page-owned by design.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_PR = REPO_ROOT / "src" / "web" / "js" / "chat/chat_pending_result.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
CHAT_PAGE_HTML = REPO_ROOT / "src" / "web" / "chat_page.html"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const PR = require(process.env.MOD_PR);
const out = {};
let now = 1000000;
const clock = { now: () => now, sleep: async (ms) => { now += (ms || 0); } };
const norm = (s) => String(s || '').trim().toLowerCase();
// ---------- waiter deps ----------
const mkWaitDeps = (o) => {
  const log = { consume: [], status: [], links: [], debug: [], history: 0 };
  let pi = 0;
  return { log, deps: {
    isStaleNav: () => !!o.stale,
    isStopped: () => !!o.stopped,
    hasReplyForTurn: () => !!o.replied,
    fetchPending: async (sid) => {
      const r = o.pending[Math.min(pi++, o.pending.length - 1)];
      if (r instanceof Error) throw r;
      return r;
    },
    fetchLive: async (sid) => o.live,
    liveLooksActive: (l) => !!(l && (l.active || l.generating)),
    recoverFromHistory: async (sid) => { log.history += 1; return o.history || null; },
    onLiveStatus: (l) => log.status.push(l.status),
    onReportUrl: (u) => log.links.push(u),
    consume: (sid) => log.consume.push(sid),
    netDebug: (n, i) => log.debug.push(n),
    sleep: clock.sleep, now: clock.now,
  } };
};
// hit + consume + exact shaped return (ORACLE waiterHit)
{
  const { log, deps } = mkWaitDeps({ pending: [{ success: true, pending: true,
    result: { success: true, response: 'hi', session_id: 's1',
      type: 'chat', query_id: 'q', report_url: 'r' } }] });
  out.waiterHit = await PR.waitForPendingResult('s1',
    { waitMs: 9000, pollMs: 4000 }, deps);
  out.waiterHitConsume = log.consume;
}
// stale navGen -> null before any fetch (ORACLE waiterStale)
{
  const { log, deps } = mkWaitDeps({ stale: true, pending: [{ success: true }] });
  let fetched = false;
  const d2 = Object.assign({}, deps,
    { fetchPending: async () => { fetched = true; return {}; } });
  out.waiterStale = await PR.waitForPendingResult('s1', { waitMs: 9000 }, d2);
  out.waiterStaleFetched = fetched;
}
// stopped -> null (ORACLE waiterStopped)
{
  const { log, deps } = mkWaitDeps({ stopped: true, pending: [{ success: true }] });
  out.waiterStopped = await PR.waitForPendingResult('s1', { waitMs: 9000 }, deps);
}
// reply already painted -> peek skip, no consume (ORACLE waiterPeekSkip)
{
  const { log, deps } = mkWaitDeps({ replied: true,
    pending: [{ success: true, pending: true,
      result: { success: true, response: 'late' } }] });
  out.waiterPeekSkip = await PR.waitForPendingResult('s1', { waitMs: 9000 }, deps);
  out.waiterPeekDebug = log.debug;
  out.waiterPeekConsume = log.consume;
}
// transient !success, idle live, deadline passes -> null, history never
// consulted, nothing consumed (ORACLE waiterHistoryFallback*)
{
  now = 1000000;
  const { log, deps } = mkWaitDeps({ pending: [{ success: false }],
    live: { success: true, active: false, generating: false },
    history: { success: true, response: 'should-not-use' } });
  out.waiterTransientMiss = await PR.waitForPendingResult('s1',
    { waitMs: 20000, pollMs: 4000 }, deps);
  out.waiterTransientHistory = log.history;
  out.waiterTransientConsume = log.consume;
}
// idle pending, empty history -> null at deadline (ORACLE waiterMiss)
{
  now = 1000000;
  const { log, deps } = mkWaitDeps({
    pending: [{ success: true, pending: false, generating: false }],
    live: { success: true, active: false }, history: null });
  out.waiterMiss = await PR.waitForPendingResult('s1',
    { waitMs: 9000, pollMs: 4000 }, deps);
}
// generating + history hit on 3rd poll
{
  now = 1000000;
  const { log, deps } = mkWaitDeps({
    pending: [{ success: true, pending: false, generating: true }],
    live: { success: true, active: true, generating: true },
    history: { success: true, response: 'third-poll' } });
  out.waiterThirdPoll = await PR.waitForPendingResult('s1',
    { waitMs: 60000, pollMs: 4000 }, deps);
  out.waiterThirdPollHistory = log.history;
}
// fetch blip then hit
{
  now = 1000000;
  const { log, deps } = mkWaitDeps({ pending: [new Error('blip'),
    { success: true, pending: true,
      result: { success: true, response: 'after-blip' } }] });
  out.waiterBlip = await PR.waitForPendingResult('s1',
    { waitMs: 9000, pollMs: 4000 }, deps);
}
// null session -> null, no fetch
{
  const { log, deps } = mkWaitDeps({ pending: [{ success: true }] });
  let fetched = false;
  const d2 = Object.assign({}, deps,
    { fetchPending: async () => { fetched = true; return {}; } });
  out.waiterNullSid = await PR.waitForPendingResult(null, { waitMs: 9 }, d2);
  out.waiterNullSidFetched = fetched;
}
// ---------- history recovery ----------
const mkRecDeps = (o) => {
  const deps = {
    isAuthMode: () => o.auth !== false,
    toAuthDbSessionId: (s) => s,
    fetchMessages: async (sid, limit) => {
      deps.seenLimit = limit;
      if (o.err) throw o.err;
      return { ok: o.ok !== false, data: o.data };
    },
    pageSize: 20, inFlightText: o.text || null, norm,
    alreadyOnScreen: (role, c) => !!o.painted,
    isCancelledText: (t) => /cancelled/i.test(String(t || '')),
    normalizeUsage: (u) => u || null,
    netDebug: () => {},
    seenLimit: 0,
  };
  return deps;
};
{
  const d = mkRecDeps({ text: 'Hello there', data: { success: true, messages: [
    { id: 1, role: 'user', content: '  hello THERE ' },
    { id: 2, role: 'assistant', content: 'matched reply', type: 'chat',
      query_id: 'qq', metadata: { usage: { t: 5 } } } ] } });
  out.recoverMatch = await PR.recoverChatResult('s1', d);
  out.recoverLimit = d.seenLimit;
}
{
  const d = mkRecDeps({ data: { success: true, messages: [
    { id: 9, role: 'assistant', content: 'trailing', type: 'chat' } ] } });
  out.recoverTrailing = await PR.recoverChatResult('s1', d);
}
{
  const d = mkRecDeps({ painted: true, data: { success: true, messages: [
    { id: 9, role: 'assistant', content: 'trailing', type: 'chat' } ] } });
  out.recoverPainted = await PR.recoverChatResult('s1', d);
}
{
  const d = mkRecDeps({ data: { success: true, messages: [
    { id: 9, role: 'assistant', content: '[CANCELLED] old run', type: 'chat' } ] } });
  out.recoverCancelled = await PR.recoverChatResult('s1', d);
}
{
  const d = mkRecDeps({ auth: false, data: { success: true, messages: [
    { id: 9, role: 'assistant', content: 'x', type: 'chat' } ] } });
  out.recoverAnon = await PR.recoverChatResult('s1', d);
}
{
  const d = mkRecDeps({ err: new Error('down') });
  out.recoverThrow = await PR.recoverChatResult('s1', d);
}
{
  const d = mkRecDeps({ text: 'zzz', data: { success: true, messages: [
    { id: 1, role: 'user', content: 'hello' },
    { id: 2, role: 'assistant', content: 'wrong turn', type: 'chat' } ] } });
  out.recoverNoMatch = await PR.recoverChatResult('s1', d);
}
// retries
{
  let n = 0; const paints = [];
  const r = await PR.recoverChatResultWithRetries('s1', 3, 10, {
    isStopped: () => false,
    recover: async () => (++n === 3 ? { success: true, response: 'late' } : null),
    isViewing: () => true, paintSyncing: () => paints.push(1), sleep: clock.sleep });
  out.retries = [r && r.response, paints.length];
}
{
  let n = 0;
  const r = await PR.recoverChatResultWithRetries('s1', 3, 10, {
    isStopped: () => true, recover: async () => { n += 1; return null; },
    isViewing: () => true, paintSyncing: () => {}, sleep: clock.sleep });
  out.retriesStopped = [r, n];
}
// consume
{
  const fired = [];
  PR.consumeParkedChatResult('s9', (sid) => fired.push(sid));
  PR.consumeParkedChatResult(null, (sid) => fired.push(sid));
  out.consume = fired;
}
// ---------- sync classification ----------
const mkFns = (o) => ({
  hasDomId: (id) => (o.domIds || []).includes(Number(id)),
  findControlClaim: (crid, role) => {
    if ((o.claims || {})[crid + '|' + role]) return 'exact';
    if ((o.claims || {})[crid]) return 'role';
    return null;
  },
  isCancelledText: (t) => /cancelled/i.test(String(t || '')),
  claimableUnmarked: (role, c) => !!(o.unmarked || {})[role + '|' + c],
  loadingThisSession: !!o.loading,
  replyShownFor: (c) => !!(o.shown || {})[c],
  hasUnmarkedAssistant: () => !!o.anyUnmarked,
  alreadyOnScreen: (role, c) => !!(o.screen || {})[role + '|' + c],
});
const cls = (msg, snap, fns) => PR.classifyServerMessage(msg, snap, fns).decision;
const clsFull = (msg, snap, fns) => PR.classifyServerMessage(msg, snap, fns);
out.clsInvalid = [cls(null, {}, mkFns({})), cls({}, {}, mkFns({}))];
out.clsUpdate = cls({ id: 5, role: 'user' }, {}, mkFns({ domIds: [5] }));
out.clsClaimExact = clsFull({ id: 6, role: 'assistant', content: 'c',
  metadata: { control_request_id: 'k1' } }, {},
  mkFns({ claims: { 'k1|assistant': 1 } }));
out.clsClaimRole = clsFull({ id: 6, role: 'assistant', content: 'c',
  metadata: '{\"control_request_id\":\"k2\"}' }, {},
  mkFns({ claims: { k2: 1 } }));
out.clsClaimMiss = cls({ id: 6, role: 'user', content: 'hi',
  metadata: { control_request_id: 'k9' } }, {}, mkFns({}));
out.clsCancelled = cls({ id: 7, role: 'assistant', content: '[CANCELLED] x' },
  {}, mkFns({}));
out.clsStopped = cls({ id: 7, role: 'assistant', content: 'live reply' },
  { stopped: true }, mkFns({}));
out.clsClaimUnmarked = cls({ id: 8, role: 'assistant', content: 'dup?' },
  {}, mkFns({ unmarked: { 'assistant|dup?': 1 } }));
out.clsShown = cls({ id: 8, role: 'assistant', content: 'on screen' },
  {}, mkFns({ loading: true, shown: { 'on screen': 1 } }));
out.clsAdoptLoading = cls({ id: 8, role: 'assistant', content: 'server text' },
  {}, mkFns({ loading: true, anyUnmarked: true }));
out.clsAppendNew = cls({ id: 8, role: 'assistant', content: 'server text' },
  {}, mkFns({ loading: true }));
out.clsDupUser = cls({ id: 3, role: 'user', content: 'same prompt' },
  {}, mkFns({ screen: { 'user|same prompt': 1 } }));
out.clsStampDup = cls({ id: 8, role: 'assistant', content: 'streamed' },
  {}, mkFns({ screen: { 'assistant|streamed': 1 } }));
out.clsAdoptIdle = cls({ id: 8, role: 'assistant', content: 'fresh' },
  {}, mkFns({ anyUnmarked: true }));
out.clsAppendIdle = cls({ id: 8, role: 'assistant', content: 'fresh' },
  {}, mkFns({}));
out.clsAppendUser = cls({ id: 3, role: 'user', content: 'new prompt' },
  {}, mkFns({}));
// ---------- heal decisions (ORACLE heal* map to kind/why/drain) ----------
const heal = (s) => PR.decideStaleHeal(s);
out.healOrphan = heal({ loading: true });
out.healLive = heal({ loading: true, hasEventSource: true,
  hasTrackedTurn: true, replyOnScreen: true });
out.healStopped = heal({ stopped: true, loading: true });
out.healSuppressed = heal({ suppressed: true, loading: true });
out.healRemote = heal({ loading: false, replyOnScreen: true,
  hasRemoteWait: true, queueLength: 1 });
out.healRemoteEmpty = heal({ loading: false, replyOnScreen: true,
  hasRemoteWait: true, queueLength: 0 });
out.healRemoteNoReply = heal({ loading: false, hasRemoteWait: true });
out.healZombie = heal({ loading: true, hasRequest: true,
  hasTrackedTurn: true, replyOnScreen: true });
out.healIdle = heal({ loading: false });
// ---------- SSE event classification ----------
out.evStatus = PR.classifyStreamEvent({ type: 'status', message: 'Thinking' });
out.evStatusEmpty = PR.classifyStreamEvent({ type: 'status' });
out.evSession = PR.classifyStreamEvent({ type: 'session', session_id: 'CH-1' });
out.evBusy = PR.classifyStreamEvent({ type: 'busy', session_id: 's1' });
out.evQuery = PR.classifyStreamEvent({ type: 'query_started',
  report_url: 'http://r', query_id: 'q1' });
out.evResponse = PR.classifyStreamEvent({ type: 'response', response: 'x' });
out.evUnknown = PR.classifyStreamEvent({ type: 'progress', pct: 3 });
out.evNull = PR.classifyStreamEvent(null);
// ---------- detach settle ----------
const mkSettle = (o) => {
  const log = [];
  return { log, deps: {
    wait: async (sid, opts) => o.wait,
    retries: async (sid) => (o.retries2 && o.second ? o.retries2 : o.retries) || null,
    fetchLive: (sid) => Promise.resolve(o.live),
    liveLooksActive: (l) => !!(l && (l.active || l.generating)),
    canPaint: () => o.paint !== false,
    paintStatus: (t) => log.push('paint:' + t),
    uiPaintedReply: () => ('painted' in o)
      ? { present: true, raw: o.painted } : null,
    hasTyping: () => !!o.typing,
    isNavAway: () => !!o.nav,
    isStopped: () => !!o.stopped,
    netDebug: (n) => log.push('dbg:' + n),
    waitOpts: {},
  }, o };
};
{
  const { log, deps } = mkSettle({ wait: { success: true, response: 'w' } });
  out.settleHit = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null,
    retries: { success: true, response: 'hist' } });
  out.settleHistory = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null, retries: null,
    painted: 'raw reply', typing: false });
  out.settlePainted = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null, retries: null,
    painted: '', typing: false });
  out.settlePaintedEmpty = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null, retries: null,
    painted: 'raw reply', typing: true, live: { active: false } });
  out.settleTypingWins = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null, retries: null, nav: true });
  out.settleNav = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps, o } = mkSettle({ wait: null, retries: null,
    live: { active: true, status: 'Running' } });
  deps.wait = async () => o.secondHit;
  o.secondHit = { success: true, response: 'second' };
  out.settleLiveAgain = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
{
  const { log, deps } = mkSettle({ wait: null, retries: null,
    live: { active: false } });
  out.settleIdle = [await PR.recoverAfterStreamDetach('s1', 7, deps), log];
}
// ---------- transport failure ----------
const mkT = (o) => {
  const log = [];
  const deps = {
    canPaint: () => true, paintStatus: (t) => log.push('paint:' + t),
    recoverServer: async (opts) => { log.push('server:' + opts.waitMs); return null; },
    wait: async () => o.wait, retries: async () => o.retries || null,
    fetchLive: async () => o.live || null, waitOpts: {},
  };
  return { log, deps };
};
{
  const { log, deps } = mkT({ wait: { success: true, response: 't' } });
  out.transportHit = [await PR.recoverAfterTransportFailure('s1', 7, new Error('x'), deps), log];
}
{
  const { log, deps } = mkT({ wait: null, live: { active: true } });
  out.transportHandoff = [await PR.recoverAfterTransportFailure('s1', 7, new Error('x'), deps), log];
}
{
  const err = new Error('orig');
  const { log, deps } = mkT({ wait: null, live: { active: false } });
  const r = await PR.recoverAfterTransportFailure('s1', 7, err, deps);
  out.transportThrow = [r.outcome, r.error === err, log];
}
// ---------- control lane ----------
{
  const r = await PR.recoverControlLaneFailure('s1', new Error('c'),
    { retries: async () => ({ success: true, response: 'ctl' }),
      controlRequestId: 'cr-1' });
  out.controlHit = r;
}
{
  const err = new Error('c2');
  const r = await PR.recoverControlLaneFailure('s1', err,
    { retries: async () => null });
  out.controlMiss = [r.outcome, r.error === err];
}
// ---------- server recovery ----------
const mkR = (o) => {
  const log = [];
  let i = 0;
  const deps = {
    fetchRestartStatus: async () => {
      const r = o.statuses[Math.min(i++, o.statuses.length - 1)];
      if (r instanceof Error) throw r;
      return r;
    },
    fetchHealthOk: async () => !!o.healthy,
    syncMessages: async () => log.push('sync'),
    paintStatus: (t) => log.push('paint:' + t),
    clearTyping: () => log.push('clear'),
    toast: (m, k) => log.push('toast:' + k + ':' + m),
    sleep: clock.sleep, now: clock.now,
    wasNotified: (id) => (o.notified || []).includes(id),
    markNotified: (id) => log.push('notified:' + id),
  };
  return { log, deps };
};
{
  now = 2000000;
  const { log, deps } = mkR({ statuses: [{ status: { state: 'healthy',
    restart_id: 'r1' }, completion_message: 'done!' }] });
  out.restartHealthy = [await PR.waitForServerRecovery({}, deps), log];
}
{
  now = 3000000;
  const { log, deps } = mkR({ notified: ['r1'], statuses: [{ status: {
    state: 'failed', restart_id: 'r1' } }] });
  out.restartFailedNotified = [await PR.waitForServerRecovery({}, deps), log];
}
{
  now = 4000000;
  const { log, deps } = mkR({ statuses: [new Error('blip'),
    { status: { state: 'healthy', restart_id: 'r2' } }] });
  out.restartBlip = [(await PR.waitForServerRecovery({}, deps)).status.state, log];
}
{
  now = 5000000;
  const { log, deps } = mkR({ statuses: [{ status: { state: 'restarting' } }] });
  out.restartTimeout = [await PR.waitForServerRecovery(
    { waitMs: 3000, pollMs: 1000 }, deps), log];
}
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ["PATH"], "MOD_PR": str(MOD_PR)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_waiter_hit_consume_and_shape():
    res = _run()
    # ORACLE waiterHit: exact shaped return from pre-change execution.
    assert res["waiterHit"] == {
        "success": True, "response": "hi", "session_id": "s1",
        "type": "chat", "query_id": "q", "report_url": "r",
    }
    assert res["waiterHitConsume"] == ["s1"]


@node_only
def test_waiter_early_exits():
    res = _run()
    assert res["waiterStale"] is None and res["waiterStaleFetched"] is False
    assert res["waiterStopped"] is None
    assert res["waiterPeekSkip"] is None
    assert res["waiterPeekDebug"] == ["pending-skip-ui-has-reply"]
    assert res["waiterPeekConsume"] == []
    assert res["waiterNullSid"] is None and res["waiterNullSidFetched"] is False


@node_only
def test_waiter_transient_and_miss():
    res = _run()
    # ORACLE: transient !success polls live to the deadline, returns null,
    # never consults history, never consumes.
    assert res["waiterTransientMiss"] is None
    assert res["waiterTransientHistory"] == 0
    assert res["waiterTransientConsume"] == []
    assert res["waiterMiss"] is None
    assert res["waiterThirdPoll"] == {"success": True, "response": "third-poll"}
    assert res["waiterThirdPollHistory"] == 1
    assert res["waiterBlip"] == {"success": True, "response": "after-blip"}


@node_only
def test_history_recovery_match():
    res = _run()
    # ORACLE recoverMatch / recoverTrailing / recoverPainted /
    # recoverCancelled from pre-change execution.
    assert res["recoverMatch"] == {
        "success": True, "response": "matched reply", "session_id": "s1",
        "type": "chat", "query_id": "qq", "usage": {"t": 5},
    }
    assert res["recoverLimit"] == 20
    assert res["recoverTrailing"] == {
        "success": True, "response": "trailing", "session_id": "s1",
        "type": "chat",
    }
    assert res["recoverPainted"] is None
    assert res["recoverCancelled"] is None
    assert res["recoverAnon"] is None
    assert res["recoverThrow"] is None
    # Unmatched in-flight text falls through to the trailing-assistant
    # branch (pre-change behavior, preserved exactly).
    assert res["recoverNoMatch"] == {
        "success": True, "response": "wrong turn", "session_id": "s1",
        "type": "chat",
    }
    assert res["retries"] == ["late", 2]
    assert res["retriesStopped"] == [None, 0]
    assert res["consume"] == ["s9"]


@node_only
def test_sync_classification():
    res = _run()
    assert res["clsInvalid"] == ["skip-invalid", "skip-invalid"]
    assert res["clsUpdate"] == "update-in-place"
    assert res["clsClaimExact"] == {"decision": "claim-control", "matched": "exact", "crid": "k1"}
    assert res["clsClaimRole"] == {"decision": "claim-control", "matched": "role", "crid": "k2"}
    assert res["clsClaimMiss"] == "append"
    assert res["clsCancelled"] == "skip-cancelled"
    assert res["clsStopped"] == "skip-stopped"
    assert res["clsClaimUnmarked"] == "claim-unmarked"
    assert res["clsShown"] == "already-shown"
    assert res["clsAdoptLoading"] == "adopt-last"
    assert res["clsAppendNew"] == "append-new"
    assert res["clsDupUser"] == "skip-dup-user"
    assert res["clsStampDup"] == "stamp-duplicate"
    assert res["clsAdoptIdle"] == "adopt-last"
    assert res["clsAppendIdle"] == "append"
    assert res["clsAppendUser"] == "append"


@node_only
def test_heal_decisions():
    res = _run()
    # ORACLE heal*: orphan finishes; live/stopped/no-reply/idle do not;
    # stale remote-wait clears (+drain only with queued items).
    assert res["healOrphan"] == {"kind": "finish", "why": "orphan"}
    assert res["healLive"] == {"kind": "none"}
    assert res["healStopped"] == {"kind": "none"}
    assert res["healSuppressed"] == {"kind": "none"}
    assert res["healRemote"] == {"kind": "clear-remote", "drain": True}
    assert res["healRemoteEmpty"] == {"kind": "clear-remote", "drain": False}
    assert res["healRemoteNoReply"] == {"kind": "none"}
    assert res["healZombie"] == {"kind": "finish", "why": "zombie"}
    assert res["healIdle"] == {"kind": "none"}


@node_only
def test_stream_event_classification():
    res = _run()
    assert res["evStatus"] == {"kind": "status", "message": "Thinking"}
    assert res["evStatusEmpty"] == {"kind": "ignore"}
    assert res["evSession"] == {"kind": "session", "sessionId": "CH-1"}
    assert res["evBusy"]["kind"] == "busy"
    assert res["evBusy"]["result"]["busy"] is True
    assert res["evBusy"]["result"]["session_id"] == "s1"
    assert "Still working" in res["evBusy"]["result"]["response"]
    assert res["evQuery"] == {"kind": "query", "reportUrl": "http://r", "queryId": "q1"}
    assert res["evResponse"] == {"kind": "response"}
    assert res["evUnknown"] == {"kind": "ignore"}
    assert res["evNull"] == {"kind": "ignore"}


@node_only
def test_detach_settle_paths():
    res = _run()
    assert res["settleHit"][0] == {"outcome": "data", "data": {"success": True, "response": "w"}}
    assert res["settleHit"][1][0] == "paint:Working… (waiting for reply)"
    assert res["settleHistory"][0]["data"] == {"success": True, "response": "hist"}
    assert "dbg:pending-miss-history" in res["settleHistory"][1]
    assert res["settlePainted"][0] == {
        "outcome": "data",
        "data": {"success": True, "response": "raw reply", "session_id": "s1"},
    }
    assert res["settlePaintedEmpty"][0] == {
        "outcome": "data",
        "data": {"success": True, "response": " ", "session_id": "s1"},
    }
    assert res["settleTypingWins"][0]["outcome"] == "no-reply"
    assert res["settleNav"][0] == {
        "outcome": "no-reply",
        "data": {"success": True, "response": "", "no_reply": True, "session_id": "s1"},
    }
    assert "dbg:pending-miss-nav" in res["settleNav"][1]
    assert res["settleLiveAgain"][0]["data"] == {"success": True, "response": "second"}
    assert res["settleIdle"][0]["outcome"] == "no-reply"


@node_only
def test_transport_and_control_recovery():
    res = _run()
    assert res["transportHit"][0]["outcome"] == "data"
    assert res["transportHit"][1][0] == "paint:Connection interrupted — waiting for reply…"
    assert res["transportHit"][1][1] == "server:45000"
    assert res["transportHandoff"][0]["outcome"] == "handoff-remote"
    assert res["transportHandoff"][0]["live"] == {"active": True}
    assert res["transportThrow"][:2] == ["throw", True]
    assert res["controlHit"]["outcome"] == "data"
    assert res["controlHit"]["data"]["reconcile_only"] is True
    assert res["controlHit"]["data"]["control_request_id"] == "cr-1"
    assert res["controlMiss"] == ["throw", True]


@node_only
def test_server_recovery_poll():
    res = _run()
    data, log = res["restartHealthy"]
    assert data["completion_message"] == "done!"
    assert "toast:success:done!" in log and "sync" in log and "clear" in log
    assert "notified:r1" in log
    data2, log2 = res["restartFailedNotified"]
    assert data2["status"]["state"] == "failed"
    assert "notified:r1" not in log2
    assert not [x for x in log2 if x.startswith("toast:")]
    assert res["restartBlip"][0] == "healthy"
    assert res["restartTimeout"][0] is None


@node_only
def test_chat_pending_result_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_PR)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


def test_page_delegates_lifecycle_to_owner():
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    for call in [
        "CuttleChatPendingResult.classifyStreamEvent(",
        "CuttleChatPendingResult.waitForPendingResult(",
        "CuttleChatPendingResult.recoverChatResult(",
        "CuttleChatPendingResult.recoverChatResultWithRetries(",
        "CuttleChatPendingResult.consumeParkedChatResult(",
        "CuttleChatPendingResult.classifyServerMessage(",
        "CuttleChatPendingResult.decideStaleHeal(",
        "CuttleChatPendingResult.recoverAfterStreamDetach(",
        "CuttleChatPendingResult.recoverAfterTransportFailure(",
        "CuttleChatPendingResult.recoverControlLaneFailure(",
        "CuttleChatPendingResult.waitForServerRecovery(",
    ]:
        assert call in src, f"missing delegation {call}"
    # Thin adapters intentionally keep the original page function names
    # (caller contracts); what must be gone is the inline decision bodies,
    # now owned by the module (checked as literals).
    for gone in [
        "pending-skip-ui-has-reply",
        "'history-recover'",
        "pending-miss-history",
        "pending-miss-ui-ok",
        "pending-miss-nav",
        "Flask restart complete (",
        "Wait for it to finish, or stop it first.",
    ]:
        assert gone not in src, f"page still owns {gone}"
    html = CHAT_PAGE_HTML.read_text(encoding="utf-8")
    assert "chat_pending_result.js?v=" in html
