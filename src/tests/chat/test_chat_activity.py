"""Activity / unread / follow-up queue frontend domain.

Behavioral characterization of src/web/js/chat/chat_activity.js under node
(Phase 3 Slice 4). Covers session-id normalization/equivalence, unread
and attention-kind priority, manual-unread hold transitions, the
open-chat attention reducer, chirp/response-ready plans, follow-up
queue interpretation and batching, live-status activity, and
malformed-input edges. DOM badges, sounds, toast rendering,
fetch/poll loops, persistence IO, and streaming orchestration stay in
chat_page.js and are covered by the neighboring suites
(test_chat_attention_dots, test_chat_followup_heal,
test_stop_refresh_live_status, test_space_activity_dots, …).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_activity.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// session identity
out.bareDb = A.toAuthDbSessionId('db_session_182');
out.bareCh = A.toAuthDbSessionId('CH-000182');
out.bareChRef = A.toAuthDbSessionId('CH-000182-23');
out.bareNum = A.toAuthDbSessionId('182');
out.bareStr = A.toAuthDbSessionId('anon-abc');
out.bareNull = A.toAuthDbSessionId(null);
out.bareEmpty = A.toAuthDbSessionId('');
out.bareBadCh = A.toAuthDbSessionId('CH-xyz');
out.canonNum = A.canonicalizeChatSessionId('CH-000182');
out.canonStr = A.canonicalizeChatSessionId('anon-abc');
out.canonNull = A.canonicalizeChatSessionId(null);
out.eqChNum = A.sessionIdsEqual('CH-000182', '182');
out.eqDbNum = A.sessionIdsEqual('db_session_182', 'CH-000182-5');
out.eqSame = A.sessionIdsEqual('a', 'a');
out.eqNull = A.sessionIdsEqual(null, 'a');
out.eqDiff = A.sessionIdsEqual('182', '183');
out.dispNum = A.formatChatDisplayId('182');
out.dispCh = A.formatChatDisplayId('CH-000182');
out.dispChLower = A.formatChatDisplayId('ch-000182');
out.dispStr = A.formatChatDisplayId('web_session_123');
out.dispNull = A.formatChatDisplayId(null);
// unread decisions
const msgs = [{ role: 'user', content: 'hi', timestamp: 100 },
  { role: 'assistant', content: 'hello', timestamp: 200 }];
out.unreadFlag = A.sessionHasUnread({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: { hasUnread: true }, sessionObj: null });
out.unreadOpen = A.sessionHasUnread({ sessionId: '1', currentSessionId: 'CH-000001',
  backgrounded: false, prefs: { hasUnread: true }, sessionObj: null });
out.unreadOpenBg = A.sessionHasUnread({ sessionId: '1', currentSessionId: '1',
  backgrounded: true, prefs: { hasUnread: true }, sessionObj: null });
out.unreadWater = A.sessionHasUnread({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: { lastReadAt: 150 }, sessionObj: { messages: msgs } });
out.unreadWaterOld = A.sessionHasUnread({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: { lastReadAt: 250 }, sessionObj: { messages: msgs } });
out.unreadNoWater = A.sessionHasUnread({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: {}, sessionObj: { messages: msgs } });
out.unreadNull = A.sessionHasUnread({ sessionId: null, currentSessionId: '1',
  backgrounded: false, prefs: { hasUnread: true }, sessionObj: null });
out.flagOn = A.prefsHasUnreadFlag({ hasUnread: true });
out.flagOff = A.prefsHasUnreadFlag(null);
out.flagErr = [A.prefsUnreadIsError({ hasUnread: true, unreadIsError: true }),
  A.prefsUnreadIsError({ hasUnread: true }), A.prefsUnreadIsError(null)];
out.lastAsst = A.lastAssistantMessageFromSessionObj({ messages: msgs });
out.lastAsstNone = A.lastAssistantMessageFromSessionObj({ messages: [{ role: 'user' }] });
out.errMeta = A.assistantReplyLooksLikeError('x', { slash_command_failed: true });
out.errEmoji = A.assistantReplyLooksLikeError('\\u274c boom', {});
out.errApi = A.assistantReplyLooksLikeError('Could not reach the Cuttle API (x)', {});
out.errThink = A.assistantReplyLooksLikeError('<think>hmm</think>Sorry, I encountered an error!', {});
out.errNo = A.assistantReplyLooksLikeError('hello there', {});
out.errEmpty = A.assistantReplyLooksLikeError('', {});
out.kindErr = A.sessionHistoryAttentionKind({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: { hasUnread: true, unreadIsError: true },
  sessionObj: { messages: msgs }, queue: [{ id: 'a' }] });
out.kindUnread = A.sessionHistoryAttentionKind({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: { hasUnread: true }, sessionObj: null, queue: [{ id: 'a' }] });
out.kindQueued = A.sessionHistoryAttentionKind({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: {}, sessionObj: null, queue: [{ id: 'a' }] });
out.kindPaused = A.sessionHistoryAttentionKind({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: {}, sessionObj: null, queue: [{ id: 'a', paused: true }] });
out.kindNone = A.sessionHistoryAttentionKind({ sessionId: '2', currentSessionId: '1',
  backgrounded: false, prefs: {}, sessionObj: null, queue: [] });
out.qActive = [A.queueHasActive([{ paused: true }]), A.queueHasActive([{ id: 'a' }]),
  A.queueHasActive(null)];
out.qPaused = [A.queueHasPaused([{ id: 'a' }]), A.queueHasPaused([{ paused: true }])];
// manual hold
out.holdBlocks = [A.manualHoldBlocksRead({ holdId: 'CH-000182', sessionId: '182' }),
  A.manualHoldBlocksRead({ holdId: '182', sessionId: '183' }),
  A.manualHoldBlocksRead({ holdId: null, sessionId: '182' })];
out.holdReopen = A.releaseManualHold({ holdId: '182', currentSessionId: '1', nextSessionId: '182' });
out.holdLeave = A.releaseManualHold({ holdId: '1', currentSessionId: '1', nextSessionId: '2' });
out.holdStay = A.releaseManualHold({ holdId: '9', currentSessionId: '1', nextSessionId: '2' });
out.holdNull = A.releaseManualHold({ holdId: null, currentSessionId: '1', nextSessionId: '2' });
// attention reducer
const s0 = A.defaultAttentionState();
out.attActive0 = A.attentionIsActive(s0);
out.attResetUnread = A.attentionAfterReset(s0, { needsAck: true, isError: true });
out.attResetClean = A.attentionAfterReset(
  { unseenBelow: true, needsAck: true, isError: true, viewportActivated: false }, null);
out.attFinAway = A.attentionAfterFinalized(s0, { isError: false, scrolledAway: true });
out.attFinInactive = A.attentionAfterFinalized(
  Object.assign({}, s0, { viewportActivated: false }), { isError: true, scrolledAway: false });
out.attFinErr = A.attentionAfterFinalized(s0, { isError: true, scrolledAway: false });
out.attFinOk = A.attentionAfterFinalized(s0, { isError: false, scrolledAway: false });
out.attFinActive = A.attentionIsActive(out.attFinAway);
out.attClear = A.attentionAfterClearUnseen(
  { unseenBelow: true, needsAck: false, isError: true, viewportActivated: true });
out.attClearAck = A.attentionAfterClearUnseen(
  { unseenBelow: true, needsAck: true, isError: true, viewportActivated: true });
out.attActivate = A.attentionAfterActivate(
  { unseenBelow: false, needsAck: true, isError: true, viewportActivated: false });
out.attActivateClean = A.attentionAfterActivate(s0);
// chirp + response-ready plan
out.chirpOk = A.shouldChirp({ lastChirpAt: 0, nowMs: 9000, cooldownMs: 8000 });
out.chirpCool = A.shouldChirp({ lastChirpAt: 5000, nowMs: 9000, cooldownMs: 8000 });
out.planView = A.responseReadyPlan({ sessionId: '182', currentSessionId: 'CH-000182',
  backgrounded: false, lastChirpAt: 0, nowMs: 9000, cooldownMs: 8000,
  isError: false, fromSync: false });
out.planViewBg = A.responseReadyPlan({ sessionId: '182', currentSessionId: '182',
  backgrounded: true, lastChirpAt: 0, nowMs: 9000, cooldownMs: 8000,
  isError: true, fromSync: false });
out.planOther = A.responseReadyPlan({ sessionId: '183', currentSessionId: '182',
  backgrounded: false, lastChirpAt: 0, nowMs: 9000, cooldownMs: 8000,
  isError: false, fromSync: false });
out.planOtherSyncCool = A.responseReadyPlan({ sessionId: '183', currentSessionId: '182',
  backgrounded: false, lastChirpAt: 5000, nowMs: 9000, cooldownMs: 8000,
  isError: false, fromSync: true });
out.planOtherSyncFresh = A.responseReadyPlan({ sessionId: '183', currentSessionId: '182',
  backgrounded: false, lastChirpAt: 5000, nowMs: 9000, cooldownMs: 8000,
  isError: false, fromSync: false });
out.planNull = A.responseReadyPlan({ sessionId: null, currentSessionId: '182',
  backgrounded: false, lastChirpAt: 0, nowMs: 9000, cooldownMs: 8000 });
// follow-up queue
out.parseArr = A.parseFollowupQueue([{ id: 'a' }]);
out.parseStr = A.parseFollowupQueue('[{\\"id\\":\\"a\\"}]');
out.parseBad = A.parseFollowupQueue('nope');
out.parseNull = A.parseFollowupQueue(null);
out.fp = A.followupQueueFingerprint([{ id: 'a', rawMessage: 'hi' }, { id: 'b', content: 'x', paused: true }]);
out.norm = A.normalizeFollowupItem({ content: 'hi' }, 1000);
out.normFull = A.normalizeFollowupItem(
  { id: 'q', content: 'c', created: 5, attachments: ['a'], rawMessage: 'r', paused: 1 }, 1000);
out.part = A.partitionFollowupForDrain(
  [{ id: 'a' }, { id: 'b', paused: true }, { id: 'c' }], 'c');
out.combNull = A.combineFollowupBatch([], { stickyPrefixFor: () => '', formatWithAttachments: (t) => t });
out.combSingle = A.combineFollowupBatch([{ rawMessage: 'hello', content: 'hello' }],
  { stickyPrefixFor: () => '', formatWithAttachments: (t) => t });
const deps = { stickyPrefixFor: (raw) => String(raw || '').startsWith('/cursor ') ? '/cursor ' : '',
  formatWithAttachments: (t) => String(t || '') };
out.combMulti = A.combineFollowupBatch(
  [{ rawMessage: '/cursor fix a', content: 'fix a' }, { rawMessage: '/cursor fix b', content: 'fix b' }], deps);
out.combAtt = A.combineFollowupBatch(
  [{ rawMessage: '', content: '', attachments: ['f.png'] }], deps);
// live status + snapshot class mapping
const NOW = 1000000000000;
out.liveGen = A.liveStatusLooksActive({ generating: true }, NOW);
out.liveCancel = A.liveStatusLooksActive({ generating: true, cancelled: true }, NOW);
out.liveNull = A.liveStatusLooksActive(null, NOW);
out.liveFresh = A.liveStatusLooksActive(
  { active: true, updated_at: NOW / 1000 - 60 }, NOW);
out.liveStale = A.liveStatusLooksActive(
  { active: true, updated_at: NOW / 1000 - 600 }, NOW);
out.liveNoTs = A.liveStatusLooksActive({ active: true }, NOW);
out.liveIdle = A.liveStatusLooksActive({ active: false }, NOW);
out.clsKinds = [A.activityClassToKind('chat-history-item has-unread-error'),
  A.activityClassToKind('chat-history-item has-unread'),
  A.activityClassToKind('chat-history-item has-queued'),
  A.activityClassToKind('chat-history-item has-paused-queue'),
  A.activityClassToKind('chat-history-item')];
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_session_identity_normalization():
    res = _run()
    assert res["bareDb"] == "182"
    assert res["bareCh"] == "182"
    assert res["bareChRef"] == "182"  # share ref → session digits only
    assert res["bareNum"] == "182"
    assert res["bareStr"] == "anon-abc"
    assert res["bareNull"] is None
    assert res["bareEmpty"] is None
    assert res["bareBadCh"] == "xyz"
    assert res["canonNum"] == "182"
    assert res["canonStr"] == "anon-abc"
    assert res["canonNull"] is None
    assert res["eqChNum"] is True
    assert res["eqDbNum"] is True
    assert res["eqSame"] is True
    assert res["eqNull"] is False  # null never equals
    assert res["eqDiff"] is False
    assert res["dispNum"] == "CH-000182"
    assert res["dispCh"] == "CH-000182"
    assert res["dispChLower"] == "CH-000182"
    assert res["dispStr"] == "CH-00003F"  # 123 → base36, not decimal padding
    assert res["dispNull"] is None


@node_only
def test_unread_and_attention_priority():
    res = _run()
    assert res["unreadFlag"] is True
    assert res["unreadOpen"] is False  # open + visible reads as not-unread
    assert res["unreadOpenBg"] is True  # backgrounded open chat stays unread
    assert res["unreadWater"] is True  # watermark fallback: newer assistant reply
    assert res["unreadWaterOld"] is False
    assert res["unreadNoWater"] is False  # no watermark → never mark historical unread
    assert res["unreadNull"] is False
    assert res["flagOn"] is True
    assert res["flagOff"] is False
    assert res["flagErr"] == [True, False, False]
    assert res["lastAsst"] == {"role": "assistant", "content": "hello", "timestamp": 200}
    assert res["lastAsstNone"] is None
    assert res["errMeta"] is True
    assert res["errEmoji"] is True
    assert res["errApi"] is True
    assert res["errThink"] is True  # think blocks stripped before matching
    assert res["errNo"] is False
    assert res["errEmpty"] is False
    # priority: error > unread > queued > paused > none
    assert res["kindErr"] == "error"
    assert res["kindUnread"] == "unread"
    assert res["kindQueued"] == "queued"
    assert res["kindPaused"] == "paused"
    assert res["kindNone"] == ""
    assert res["qActive"] == [False, True, False]
    assert res["qPaused"] == [False, True]


@node_only
def test_manual_hold_and_attention_reducer():
    res = _run()
    assert res["holdBlocks"] == [True, False, False]
    assert res["holdReopen"] is None  # re-opening held chat releases
    assert res["holdLeave"] is None  # leaving releases
    assert res["holdStay"] == "9"  # unrelated nav keeps the hold
    assert res["holdNull"] is None
    assert res["attActive0"] is False
    assert res["attResetUnread"] == {"unseenBelow": False, "needsAck": True,
                                    "isError": True, "viewportActivated": False}
    assert res["attResetClean"] == {"unseenBelow": False, "needsAck": False,
                                   "isError": False, "viewportActivated": True}
    assert res["attFinAway"] == {"unseenBelow": True, "needsAck": False,
                                "isError": False, "viewportActivated": True}
    fin_inactive = res["attFinInactive"]
    assert fin_inactive["needsAck"] is True and fin_inactive["isError"] is True
    fin_err = res["attFinErr"]  # bottom-pinned error still nags until ack
    assert fin_err == {"unseenBelow": False, "needsAck": True,
                      "isError": True, "viewportActivated": False}
    assert res["attFinOk"] == {"unseenBelow": False, "needsAck": False,
                              "isError": False, "viewportActivated": True}
    assert res["attFinActive"] is True
    assert res["attClear"] == {"unseenBelow": False, "needsAck": False,
                              "isError": False, "viewportActivated": True}
    assert res["attClearAck"] == {"unseenBelow": False, "needsAck": True,
                                 "isError": True, "viewportActivated": True}
    assert res["attActivate"] == {"unseenBelow": False, "needsAck": False,
                                 "isError": False, "viewportActivated": True}
    assert res["attActivateClean"] == {"unseenBelow": False, "needsAck": False,
                                      "isError": False, "viewportActivated": True}


@node_only
def test_chirp_and_response_ready_plan():
    res = _run()
    assert res["chirpOk"] is True
    assert res["chirpCool"] is False  # 8s cooldown blocks double-fire
    plan = res["planView"]
    assert plan == {"chirp": True, "nowMs": 9000, "viewingThis": True,
                   "mark": "read", "toast": False, "isError": False}
    assert res["planViewBg"]["viewingThis"] is False  # backgrounded ≠ viewing
    assert res["planViewBg"]["mark"] == "unread"
    other = res["planOther"]
    assert other["viewingThis"] is False and other["toast"] is True
    assert res["planOtherSyncCool"]["toast"] is False  # sync echo in cooldown: no dup toast
    assert res["planOtherSyncFresh"]["toast"] is True  # sync echo outside cooldown still toasts
    assert res["planNull"] is None


@node_only
def test_followup_queue_interpretation():
    res = _run()
    assert res["parseArr"] == [{"id": "a"}]
    assert res["parseStr"] == [{"id": "a"}]
    assert res["parseBad"] == []
    assert res["parseNull"] == []
    assert res["fp"] == "a:hi:0|b:x:1"
    norm = res["norm"]
    assert norm["content"] == "hi" and norm["created"] == 1000
    assert norm["id"].startswith("fq_") and norm["paused"] is False
    assert res["normFull"] == {"id": "q", "content": "c", "created": 5,
                              "attachments": ["a"], "rawMessage": "r", "paused": True}
    assert res["part"] == {"batch": [{"id": "a"}],
                          "remaining": [{"id": "b", "paused": True}, {"id": "c"}]}
    assert res["combNull"] is None
    assert res["combSingle"] == {"message": "hello", "displayMessage": "hello",
                                "attachments": []}
    multi = res["combMulti"]
    assert multi["message"].startswith("/cursor The user queued 2 follow-ups")
    assert "1. fix a" in multi["message"] and "2. fix b" in multi["message"]
    assert multi["displayMessage"] == multi["message"] and multi["attachments"] == []
    assert res["combAtt"]["message"] == "(see attached files)"


@node_only
def test_live_status_and_snapshot_mapping():
    res = _run()
    assert res["liveGen"] is True  # busy lock wins over stale text
    assert res["liveCancel"] is False  # cancelled never resurrects the spinner
    assert res["liveNull"] is False
    assert res["liveFresh"] is True
    assert res["liveStale"] is False  # 3-minute staleness gate
    assert res["liveNoTs"] is True  # no timestamp → trust the flag
    assert res["liveIdle"] is False
    assert res["clsKinds"] == ["error", "unread", "queued", "paused", ""]


@node_only
def test_chat_activity_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
