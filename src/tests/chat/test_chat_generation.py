"""Behavioral coverage for src/web/js/chat/chat_generation.js under node.

Executes the real generation-lifecycle module (busy-lock transitions
with token-scoped release, sync cadence, detached-poll classification,
session-open flags, sync claim lifetime with token-scoped finish)
over scripted snapshots. Values marked ORACLE were
captured by executing the pre-extraction page functions
(begin/endLocalGeneration, detach, messageSyncDelayMs,
isLoadingThisSession, scheduler mechanics) with equivalent fakes before
the move; identical assertions must hold after rewiring. The stale-token
no-release rule is new specified behavior (previously a stale turn
callback COULD release a newer turn's lock — see the Stop→send test);
it is labeled NEW, not oracle. Page wiring is pinned structurally below
(delegation call-sites + script tag); timers, transport, DOM, and flag
paint stay page-owned by design.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_GEN = REPO_ROOT / "src" / "web" / "js" / "chat/chat_generation.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
CHAT_PAGE_HTML = REPO_ROOT / "src" / "web" / "chat_page.html"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const G = require(process.env.MOD_GEN);
const out = {};
// ---------- begin / end / detach ----------
{
  const st = G.createGenerationState();
  const r = G.beginGeneration(st, { currentSessionId: 's1', requestSessionId: 'req-1' });
  out.begin = [st.loading, st.localSessionId, r];
  const r2 = G.beginGeneration(st, { currentSessionId: 's1', requestSessionId: 'req-1' });
  out.beginTwice = [st.loading, st.localSessionId, r2.token === r.token, r2];
}
{
  const st = G.createGenerationState();
  const r = G.beginGeneration(st, { currentSessionId: null, requestSessionId: 'req-1' });
  out.beginNoCurrent = [st.loading, st.localSessionId, r.sessionId, r.markRunning];
}
{
  const st = G.createGenerationState();
  const r = G.beginGeneration(st, { currentSessionId: null, requestSessionId: null });
  out.beginNoIds = [st.loading, st.localSessionId, r.sessionId, r.markRunning];
}
{
  const st = G.createGenerationState();
  const b = G.beginGeneration(st, { currentSessionId: 's1' });
  const e = G.endGeneration(st, b.token);
  out.end = [st.loading, st.localSessionId, e];
}
{
  const st = G.createGenerationState();
  const b = G.beginGeneration(st, { currentSessionId: null });
  const e = G.endGeneration(st, b.token);
  out.endNoId = [st.loading, e];
}
{
  // NEW (stale-safety): old token must not release the newer turn.
  const st = G.createGenerationState();
  const a = G.beginGeneration(st, { currentSessionId: 'sA' });
  G.endGeneration(st, null); // Stop owns the turn: force release
  const b = G.beginGeneration(st, { currentSessionId: 'sB' });
  const stale = G.endGeneration(st, a.token); // old turn's late finally
  out.stopResend = [stale, st.loading, st.localSessionId, b.token !== a.token];
}
{
  // NEW: detach invalidates pre-detach tokens.
  const st = G.createGenerationState();
  const a = G.beginGeneration(st, { currentSessionId: 'sA' });
  const d = G.detachGeneration(st);
  const stale = G.endGeneration(st, a.token);
  out.detach = [d, st.loading, st.localSessionId, stale];
}
{
  const st = G.createGenerationState();
  out.rebindIdle = G.rebindGeneration(st, 's9');
  const b = G.beginGeneration(st, { currentSessionId: 's1' });
  out.rebind = [G.rebindGeneration(st, 's2'), st.localSessionId,
    G.endGeneration(st, b.token)];
}
{
  const st = G.createGenerationState();
  out.token0 = G.currentToken(st);
  const b = G.beginGeneration(st, { currentSessionId: 's1' });
  out.token1 = [G.currentToken(st), b.token];
}
// ---------- session predicate (ORACLE gates) ----------
{
  const st = G.createGenerationState();
  const isViewing = (sid) => sid === 's1';
  const g1 = G.isLoadingForSession(st, isViewing);
  G.beginGeneration(st, { currentSessionId: null });
  const g2 = G.isLoadingForSession(st, isViewing);
  const st2 = G.createGenerationState();
  G.beginGeneration(st2, { currentSessionId: 's1' });
  const g3 = G.isLoadingForSession(st2, isViewing);
  const g4 = G.isLoadingForSession(st2, (sid) => sid === 's2');
  out.gates = [g1, g2, g3, g4];
}
// ---------- cadence (ORACLE delays) ----------
{
  const d = (s) => G.decideSyncDelayMs(s);
  out.delays = {
    hidden: d({ backgrounded: true }),
    unfocusedLoading: d({ backgrounded: false, shellUnfocused: true, loading: true }),
    unfocusedIdle: d({ backgrounded: false, shellUnfocused: true, loading: false, generating: false }),
    recover: d({ backgrounded: false, shellUnfocused: false, loading: true }),
    active: d({ backgrounded: false, shellUnfocused: false, loading: false, generating: true }),
    idle: d({ backgrounded: false, shellUnfocused: false, loading: false, generating: false }),
  };
  out.constants = G.SYNC_MS;
}
// ---------- detached poll classification ----------
{
  const c = (s) => G.classifyDetachedPoll(s);
  out.detached = [
    c({ cancelled: true }),
    c({ returnedHome: true, body: 'x', generating: true, liveActive: true }),
    c({ body: '  reply  ' }),
    c({ generating: true }),
    c({ liveActive: true }),
    c({}),
  ];
}
// ---------- session open flags ----------
{
  out.openSame = G.decideSessionOpen({ switchingAway: false });
  out.openSwitch = G.decideSessionOpen({ switchingAway: true });
}
// ---------- sync claim lifetime (D2b; clock passed explicitly) ----------
{
  const st = G.createSyncState();
  const c1 = G.claimSync(st, 1000);
  out.syncClaim = [c1, st.inFlight, st.startedAt,
    G.isSyncCurrent(st, c1.token)];
}
{
  const st = G.createSyncState();
  const c1 = G.claimSync(st, 1000);
  const c2 = G.claimSync(st, 1500); // fresh overlap coalesces
  out.syncFresh = [c2, st.seq, st.inFlight, st.startedAt,
    G.isSyncCurrent(st, c1.token)];
}
{
  const st = G.createSyncState();
  G.claimSync(st, 1000);
  const edge = G.claimSync(st, 21000); // exactly inflightMax: coalesce
  const past = G.claimSync(st, 21001); // replacement takes ownership
  out.syncBoundary = [edge, past && past.token, st.inFlight, st.startedAt];
}
{
  const st = G.createSyncState();
  const c1 = G.claimSync(st, 1000);
  const c2 = G.claimSync(st, 21001);
  const stale = G.finishSync(st, c1.token); // must not clear c2's claim
  out.syncStale = [stale, st.inFlight, st.startedAt,
    G.isSyncCurrent(st, c2.token), G.isSyncCurrent(st, c1.token)];
  const ok = G.finishSync(st, c2.token);
  const again = G.finishSync(st, c2.token); // repeated finish safe
  out.syncFinish = [ok, again, st.inFlight, st.startedAt];
}
{
  // Zero startedAt with an outstanding claim preserves the old guard.
  const st = G.createSyncState();
  G.claimSync(st, 0);
  out.syncZeroStart = G.claimSync(st, 50000);
}
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ["PATH"], "MOD_GEN": str(MOD_GEN)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_begin_claims_and_rebegin_keeps_token():
    res = _run()
    # ORACLE begin: loading + local id claimed.
    assert res["begin"][:2] == [True, "s1"]
    assert res["begin"][2] == {"token": 1, "sessionId": "s1", "markRunning": "s1"}
    # ORACLE beginTwice: re-begin is idempotent, same token.
    assert res["beginTwice"][:3] == [True, "s1", True]
    assert res["beginNoCurrent"] == [True, "req-1", "req-1", "req-1"]
    assert res["beginNoIds"] == [True, None, None, None]
    assert res["token0"] == 0
    assert res["token1"] == [1, 1]


@node_only
def test_end_releases_on_token_match():
    res = _run()
    # ORACLE end: loading + id + flag released.
    assert res["end"] == [False, None, {"released": True, "sessionId": "s1"}]
    assert res["endNoId"] == [False, {"released": True, "sessionId": None}]


@node_only
def test_stale_token_never_releases_newer_turn():
    res = _run()
    # NEW: Stop (force) → re-send (new token) → old finally is stale.
    stale, loading, local_id, bumped = res["stopResend"]
    assert stale == {"released": False, "sessionId": None}
    assert loading is True and local_id == "sB" and bumped is True
    # NEW: detach invalidates pre-detach tokens, keeps spinner id.
    d, loading2, local2, stale2 = res["detach"]
    assert d == {"keepRunningId": "sA"}
    assert loading2 is False and local2 is None
    assert stale2 == {"released": False, "sessionId": None}


@node_only
def test_sync_claim_release_and_fresh_overlap():
    res = _run()
    # D2b move: ordinary claim takes ownership with explicit now.
    assert res["syncClaim"][0] == {"token": 1}
    assert res["syncClaim"][1:] == [True, 1000, True]
    # D2b move: fresh overlap coalesces, first token stays current.
    assert res["syncFresh"][0] is None
    assert res["syncFresh"][1:] == [1, True, 1000, True]


@node_only
def test_sync_timeout_replacement_boundary():
    res = _run()
    # D2b move: exactly inflightMax still coalesces (strict > preserved).
    assert res["syncBoundary"][0] is None
    # D2b move: past the boundary the replacement owns the claim.
    assert res["syncBoundary"][1] == 2
    assert res["syncBoundary"][2:] == [True, 21001]
    # D2b move: zero startedAt with a live flag still coalesces.
    assert res["syncZeroStart"] is None


@node_only
def test_sync_stale_finish_and_repeat_safe():
    res = _run()
    # D2b move: stale finish cannot clear the newer claim.
    stale, flying, started, cur2, cur1 = res["syncStale"]
    assert stale == {"released": False}
    assert flying is True and started == 21001
    assert cur2 is True and cur1 is False
    # D2b move: owning finish releases; repeating it is safe.
    ok, again, flying2, started2 = res["syncFinish"]
    assert ok == {"released": True} and again == {"released": True}
    assert flying2 is False and started2 == 0


@node_only
def test_rebind_moves_lock_only_when_loading():
    res = _run()
    assert res["rebindIdle"] is None
    moved, now, end = res["rebind"]
    assert moved == {"prev": "s1", "sessionId": "s2"}
    assert now == "s2"
    assert end == {"released": True, "sessionId": "s2"}


@node_only
def test_session_predicate_gates():
    res = _run()
    # ORACLE gates: idle false; loading without id true; viewing-gated.
    assert res["gates"] == [False, True, True, False]


@node_only
def test_sync_cadence_branches():
    res = _run()
    # ORACLE delays, branch order preserved.
    assert res["delays"] == {
        "hidden": 60000, "unfocusedLoading": 16000, "unfocusedIdle": 45000,
        "recover": 4000, "active": 5000, "idle": 12000,
    }
    assert res["constants"]["inflightMax"] == 20000


@node_only
def test_detached_poll_classification():
    res = _run()
    assert res["detached"] == [
        "stop", "stop", "notify-done", "keep-waiting", "keep-waiting", "spin-down",
    ]


@node_only
def test_session_open_flags():
    res = _run()
    assert res["openSame"] == {
        "clearHubCache": False, "deferGenerating": False, "markIdle": False,
    }
    assert res["openSwitch"] == {
        "clearHubCache": True, "deferGenerating": True, "markIdle": True,
    }


@node_only
def test_chat_generation_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_GEN)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


def test_page_delegates_generation_to_owner():
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    for call in [
        "CuttleChatGeneration.createGenerationState()",
        "CuttleChatGeneration.beginGeneration(",
        "CuttleChatGeneration.endGeneration(",
        "CuttleChatGeneration.detachGeneration(",
        "CuttleChatGeneration.rebindGeneration(",
        "CuttleChatGeneration.isLoadingForSession(",
        "CuttleChatGeneration.decideSyncDelayMs(",
        "CuttleChatGeneration.classifyDetachedPoll(",
        "CuttleChatGeneration.decideSessionOpen(",
    ]:
        assert call in src, f"missing delegation {call}"
    # Thin adapters keep behavior; direct busy-lock writes must be gone —
    # all transitions go through the single owned state object.
    for gone in [
        "let isLoading = false;",
        "let localGeneratingSessionId = null;",
        "localGeneratingSessionId = currentSessionId || authSessionIdForRequest()",
        "const MESSAGE_SYNC_ACTIVE_MS = 5000",
        "if (isLoading) return MESSAGE_SYNC_RECOVER_MS;",
    ]:
        assert gone not in src, f"page still owns {gone}"
    html = CHAT_PAGE_HTML.read_text(encoding="utf-8")
    assert "chat_generation.js?v=" in html
