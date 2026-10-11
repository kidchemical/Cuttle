"""Behavioral coverage for the session-identity lifecycle owner.

Step 1 of slice 3B made ``chat_session.js`` the single writer of session
identity (``open``/``adopt``/``create``). Step 2 moves the two identity
writers that need page capabilities — ``adoptChatSessionId`` (bind a
server-assigned id) and ``saveChatSession`` (persist a local anonymous
message, minting a CH-… id on first save) — into the owner as
``CuttleChatSession.createLifecycle(session, host)``. The page keeps thin
wrappers and injects capabilities via ``host``.

These tests execute the real owner under node with a recording fake host so
the call sequence and the pure state-machine transitions are pinned. The
pre-move page functions were read to capture the ORACLE call order; the
adoption numeric-remap guard and the auth-vs-anonymous split are the
behavior that must not change.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD = REPO_ROOT / "src" / "web" / "js" / "chat/chat_session.js"
MOD_ACT = REPO_ROOT / "src" / "web" / "js" / "chat/chat_activity.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = r"""
const S = require(process.env.MOD);
const A = require(process.env.MOD_ACT);
const out = {};

function makeStorage(seed) {
  const map = Object.assign({}, seed || {});
  const writes = [];
  return {
    map: map,
    writes: writes,
    getItem: (k) => (k in map ? map[k] : null),
    setItem: (k, v) => { map[k] = String(v); writes.push([k, String(v)]); },
  };
}

function makeSessionHost(overrides) {
  const calls = [];
  const rec = (name) => (...args) => { calls.push([name].concat(args)); };
  const storage = makeStorage((overrides && overrides.storageSeed) || {});
  const host = {
    calls: calls,
    storage: storage,
    canonicalizeChatSessionId: (x) => A.canonicalizeChatSessionId(x),
    sessionIdsEqual: (a, b) => A.sessionIdsEqual(a, b),
    toAuthDbSessionId: (x) => A.toAuthDbSessionId(x),
    migratePromptHistorySession: rec('migratePromptHistorySession'),
    migrateSessionPrefs: rec('migrateSessionPrefs'),
    migrateComposerDraft: rec('migrateComposerDraft'),
    migrateLocalChatSession: rec('migrateLocalChatSession'),
    isAuthMode: () => !!(overrides && overrides.auth),
    turnBadgeBySession: overrides && overrides.turnBadgeBySession ? overrides.turnBadgeBySession : new Map(),
    getCompletionNotifications: () => (overrides && overrides.completionNotifications) || null,
    generation: (overrides && overrides.generation) || { loading: false },
    getCompletionNotificationPendingId: () => 'pending-x',
    releaseManualUnreadHoldIfLeaving: rec('releaseManualUnreadHoldIfLeaving'),
    adoptFollowupQueue: rec('adoptFollowupQueue'),
    updateChatIdBadge: rec('updateChatIdBadge'),
    getAuth: () => (overrides && overrides.authObj) || null,
    syncChatUrl: rec('syncChatUrl'),
    reportSessionToShell: rec('reportSessionToShell'),
    clearSessionPrefs: rec('clearSessionPrefs'),
    newComposerPrefsId: 'draft:new',
    persistProjectForCurrentSession: rec('persistProjectForCurrentSession'),
    persistStickySlashForCurrentSession: rec('persistStickySlashForCurrentSession'),
    flushDirtyAgentSelectionsOnAdopt: rec('flushDirtyAgentSelectionsOnAdopt'),
    rebindGeneration: (overrides && overrides.rebindGeneration)
      || ((g, sid) => null),
    setHistorySessionRunning: rec('setHistorySessionRunning'),
    refreshChatHistoryList: rec('refreshChatHistoryList'),
    scheduleHistoryTitleRefresh: rec('scheduleHistoryTitleRefresh'),
    startMessageSync: rec('startMessageSync'),
    generateLocalChatId: () => 'CH-TEST01',
    getCurrentProject: () => (overrides && overrides.currentProject) || null,
    normalizeAttachmentList: (l) => (Array.isArray(l) ? l.filter(Boolean) : []),
    reloadHistoryListKeepingSearch: rec('reloadHistoryListKeepingSearch'),
  };
  return host;
}

function callNames(calls) { return calls.map((c) => c[0]); }

// ---------- pure state machine unchanged ----------
{
  const st = S.create();
  out.pure = [st.current(), st.navSeq()];
  const seq = st.open('a');
  out.pureOpen = [seq, st.current(), st.navSeq()];
  st.adopt('b');
  out.pureAdopt = [st.current(), st.navSeq()];
  st.create(null);
  out.pureCreate = [st.current(), st.navSeq()];
}

// ---------- adopt: empty / null no-op ----------
{
  const st = S.create();
  const host = makeSessionHost();
  const lc = S.createLifecycle(st, host);
  lc.adoptChatSessionId(null);
  lc.adoptChatSessionId('');
  out.adoptEmpty = [st.current(), host.calls.length];
}

// ---------- adopt: numeric remap guard ----------
{
  const st = S.create();
  st.open('5');
  const host = makeSessionHost();
  const lc = S.createLifecycle(st, host);
  lc.adoptChatSessionId('7');
  out.adoptNumericGuard = [st.current(), host.calls.length];
}

// ---------- adopt: same numeric, handle to bare ----------
{
  const st = S.create();
  st.open('CH-000005');
  const host = makeSessionHost();
  const lc = S.createLifecycle(st, host);
  lc.adoptChatSessionId('5');
  out.adoptSameNumeric = [st.current(), callNames(host.calls).includes('migratePromptHistorySession')];
}

// ---------- adopt: fresh binding (prev null) ----------
{
  const st = S.create();
  const badge = new Map();
  const host = makeSessionHost({ turnBadgeBySession: badge, authObj: { setCurrentChatSession: () => {} } });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.adoptChatSessionId('CH-000042');
  out.adoptFresh = {
    current: st.current(),
    names: callNames(host.calls),
    lastChat: host.storage.map['lastChatSessionId'] || null,
  };
}

// ---------- adopt: badge key moves, migrations run for non-numeric prev ----------
{
  const st = S.create();
  st.open('native_999');
  const badge = new Map([['native_999', { slash: '/cursor' }]]);
  const host = makeSessionHost({ turnBadgeBySession: badge });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.adoptChatSessionId('CH-000042');
  out.adoptBadge = {
    current: st.current(),
    oldGone: !badge.has('native_999'),
    movedValue: badge.get('42'),
    names: callNames(host.calls),
  };
}

// ---------- adopt: numeric->numeric with equal bare proceeds ----------
{
  const st = S.create();
  st.open('db_session_12');
  const host = makeSessionHost();
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.adoptChatSessionId('12');
  out.adoptBareEqual = [st.current(), callNames(host.calls).includes('migrateSessionPrefs')];
}

// ---------- adopt: rebind generation marks running ----------
{
  const st = S.create();
  const gen = { loading: false };
  const host = makeSessionHost({ generation: gen, rebindGeneration: () => ({ prev: 'p', sessionId: 's' }) });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.adoptChatSessionId('s');
  out.adoptRebind = host.calls.filter((c) => c[0] === 'setHistorySessionRunning');
}

// ---------- save: auth mode ----------
{
  const st = S.create();
  st.open('196');
  const host = makeSessionHost({ auth: true });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.saveChatSession('hi', 'assistant');
  out.saveAuth = {
    names: callNames(host.calls),
    lastChat: host.storage.map['lastChatSessionId'] || null,
    autoResume: host.storage.map['autoResumeSession'] || null,
    chatSessions: host.storage.map['chatSessions'] || null,
  };
}

// ---------- save: auth mode, no current id ----------
{
  const st = S.create();
  const host = makeSessionHost({ auth: true });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.saveChatSession('hi', 'user');
  out.saveAuthNoId = {
    names: callNames(host.calls),
    lastChat: host.storage.map['lastChatSessionId'] || null,
    autoResume: host.storage.map['autoResumeSession'] || null,
  };
}

// ---------- save: anonymous first message mints id + persists ----------
{
  const st = S.create();
  const proj = { id: 'p1', path: '/tmp/p', name: 'Proj' };
  const host = makeSessionHost({ currentProject: proj });
  const lc = S.createLifecycle(st, host);
  host.calls.length = 0;
  lc.saveChatSession('X'.repeat(60), 'user', {
    query_id: 'q1', report_url: '/r', slash_command: '/cursor',
    slash_command_failed: true, usage: { a: 1 }, routing_badge: { b: 2 },
    attachments: ['f.png', null],
  });
  const saved = JSON.parse(host.storage.map['chatSessions']);
  out.saveAnon = {
    current: st.current(),
    names: callNames(host.calls),
    lastChat: host.storage.map['lastChatSessionId'],
    entry: saved['CH-TEST01'],
  };
}

// ---------- save: anonymous existing session does not re-mint ----------
{
  const st = S.create();
  const host = makeSessionHost();
  const lc = S.createLifecycle(st, host);
  lc.saveChatSession('first', 'user');
  const created = st.current();
  host.calls.length = 0;
  lc.saveChatSession('second', 'assistant');
  const saved = JSON.parse(host.storage.map['chatSessions']);
  out.saveAnonSecond = {
    currentSame: st.current() === created,
    names: callNames(host.calls),
    messages: saved[created].messages.map((m) => m.content),
  };
}

process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={"PATH": os.environ["PATH"], "MOD": str(MOD), "MOD_ACT": str(MOD_ACT)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_pure_state_machine_unchanged():
    res = _run()
    assert res["pure"] == [None, 0]
    assert res["pureOpen"] == [1, "a", 1]
    # adopt does not advance the navigation sequence
    assert res["pureAdopt"] == ["b", 1]
    assert res["pureCreate"] == [None, 1]


@node_only
def test_adopt_empty_is_noop():
    res = _run()
    assert res["adoptEmpty"] == [None, 0]


@node_only
def test_adopt_refuses_numeric_remap():
    res = _run()
    # Already bound to numeric 5; adopting numeric 7 must not move the turn.
    assert res["adoptNumericGuard"] == ["5", 0]
    # CH-000005 and 5 are the same session, so no migration runs; the id is
    # canonicalized to its bare numeric form.
    assert res["adoptSameNumeric"] == ["5", False]
    assert res["adoptBareEqual"] == ["12", False]


@node_only
def test_adopt_fresh_binds_and_persists():
    res = _run()
    assert res["adoptFresh"]["current"] == "42"
    names = res["adoptFresh"]["names"]
    # migrations skipped when prev is null; adopt path runs
    assert "migrateSessionPrefs" not in names
    for expected in (
        "releaseManualUnreadHoldIfLeaving",
        "adoptFollowupQueue",
        "updateChatIdBadge",
        "syncChatUrl",
        "reportSessionToShell",
        "clearSessionPrefs",
        "persistProjectForCurrentSession",
        "persistStickySlashForCurrentSession",
        "flushDirtyAgentSelectionsOnAdopt",
        "refreshChatHistoryList",
        "scheduleHistoryTitleRefresh",
        "startMessageSync",
    ):
        assert expected in names, expected
    assert res["adoptFresh"]["lastChat"] == "42"


@node_only
def test_adopt_moves_badge_and_migrates():
    res = _run()
    assert res["adoptBadge"]["current"] == "42"
    assert res["adoptBadge"]["oldGone"] is True
    assert res["adoptBadge"]["movedValue"] == {"slash": "/cursor"}
    names = res["adoptBadge"]["names"]
    assert "migratePromptHistorySession" in names
    assert "migrateSessionPrefs" in names
    assert "migrateComposerDraft" in names
    # prev is not null → no composer-prefs reset
    assert "clearSessionPrefs" not in names


@node_only
def test_adopt_rebind_marks_previous_and_current_running():
    res = _run()
    assert res["adoptRebind"] == [
        ["setHistorySessionRunning", "p", False],
        ["setHistorySessionRunning", "s", True],
    ]


@node_only
def test_save_auth_does_not_touch_local_store():
    res = _run()
    assert res["saveAuth"]["names"] == ["scheduleHistoryTitleRefresh"]
    assert res["saveAuth"]["lastChat"] == "196"
    assert res["saveAuth"]["autoResume"] == "true"
    assert res["saveAuth"]["chatSessions"] is None
    # user role: no title refresh
    assert res["saveAuthNoId"]["names"] == []
    assert res["saveAuthNoId"]["autoResume"] == "true"


@node_only
def test_save_anonymous_mints_and_persists():
    res = _run()
    assert res["saveAnon"]["current"] == "CH-TEST01"
    assert res["saveAnon"]["lastChat"] == "CH-TEST01"
    names = res["saveAnon"]["names"]
    for expected in ("clearSessionPrefs", "adoptFollowupQueue", "updateChatIdBadge",
                     "reloadHistoryListKeepingSearch"):
        assert expected in names, expected
    entry = res["saveAnon"]["entry"]
    assert entry["id"] == "CH-TEST01"
    assert entry["title"] == "X" * 50 + "..."
    assert entry["projectId"] == "p1"
    assert entry["projectPath"] == "/tmp/p"
    assert entry["projectName"] == "Proj"
    msg = entry["messages"][0]
    assert msg["content"] == "X" * 60
    assert msg["role"] == "user"
    assert msg["query_id"] == "q1"
    assert msg["report_url"] == "/r"
    assert msg["slash_command"] == "/cursor"
    assert msg["slash_command_failed"] is True
    assert msg["usage"] == {"a": 1}
    assert msg["routing_badge"] == {"b": 2}
    assert msg["attachments"] == ["f.png"]


@node_only
def test_save_anonymous_second_message_reuses_session():
    res = _run()
    assert res["saveAnonSecond"]["currentSame"] is True
    assert res["saveAnonSecond"]["messages"] == ["first", "second"]


def test_chat_page_delegates_to_owner():
    text = CHAT_PAGE_JS.read_text(encoding="utf-8")
    assert "CuttleChatSession.createLifecycle(session," in text
    assert "function adoptChatSessionId(sessionId) {" in text
    assert "function saveChatSession(message, role, opts = {}) {" in text
    # The page must no longer hold the moved bodies.
    assert "function adoptChatSessionId(sessionId) {\n        if (sessionId == null || sessionId === '') return;" not in text
