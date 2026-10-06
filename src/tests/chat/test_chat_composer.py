"""Composer / send-planning frontend domain.

Behavioral characterization of src/web/js/chat/chat_composer.js under node
(Phase 3 Slice 6). Covers keyboard-submit interpretation, send
eligibility (bare sticky tokens, control commands, nested one-shots,
model chips, attachments), control-lane text classification, sticky
resolution after send, and the send-dispatch plan (guard, empty,
duplicate, follow-up, normal, control bypass). Textarea DOM,
focus/caret, event wiring, draft persistence, fetch/SSE, streaming,
history persistence, and message rendering stay in chat_page.js;
slash/project/attachment/activity logic stays in its owning domain
and is injected here as narrow callbacks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_composer.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// keyboard-submit interpretation
out.enter = A.enterSubmits({ key: 'Enter', shiftKey: false, touchMode: false });
out.shiftEnter = A.enterSubmits({ key: 'Enter', shiftKey: true, touchMode: false });
out.otherKey = A.enterSubmits({ key: 'a', shiftKey: false, touchMode: false });
out.touchEnter = A.enterSubmits({ key: 'Enter', shiftKey: false, touchMode: true });
out.touchCtrl = A.enterSubmits(
  { key: 'Enter', shiftKey: false, ctrlKey: true, touchMode: true });
out.touchMeta = A.enterSubmits(
  { key: 'Enter', shiftKey: false, metaKey: true, touchMode: true });
// sticky/slash callbacks mirroring the slash domain surface
const stickyOf = (rest) => {
  const m = String(rest || '').match(/^\\/(cursor|codex|claude) (.*)$|^\\/(cursor|codex|claude)$/i);
  if (!m) return null;
  const agent = (m[1] || m[3] || '').toLowerCase();
  return { prefix: '/' + agent + ' ', label: agent };
};
const isControl = (t) => /^\\/restart(\\s|$)/i.test(String(t || '').trim());
const deps = { stickyOf, isControl };
// send eligibility
out.bareCursor = A.isSendableComposerMessage('/cursor', [], deps);
out.bareCodex = A.isSendableComposerMessage('/codex', [], deps);
out.prompt = A.isSendableComposerMessage('/cursor fix it', [], deps);
out.plain = A.isSendableComposerMessage('hello', [], deps);
out.empty = A.isSendableComposerMessage('   ', [], deps);
out.attachOnly = A.isSendableComposerMessage('/cursor', [{ filename: 'a.png' }], deps);
out.control = A.isSendableComposerMessage('/restart status', [], deps);
out.controlSticky = A.isSendableComposerMessage('/cursor /restart status', [], deps);
out.usage = A.isSendableComposerMessage('/usage', [], deps);
out.usageBare = A.isSendableComposerMessage('usage', [], deps);
out.modelOnly = A.isSendableComposerMessage('/model auto', [], deps);
out.modelPrompt = A.isSendableComposerMessage('/model auto draw a cat', [], deps);
out.modelRefresh = A.isSendableComposerMessage('/model refresh', [], deps);
out.costNested = A.isSendableComposerMessage('/cost', [], deps);
out.noDeps = A.isSendableComposerMessage('hello', [], null);
// control-lane text
out.laneRestart = A.isImmediateControlLaneText('/restart status');
out.laneSticky = A.isImmediateControlLaneText('/cursor /coordinate status');
out.laneCoord = A.isImmediateControlLaneText('/coordinate followup x');
out.laneCoordNo = A.isImmediateControlLaneText('/coordinate frobnicate');
out.laneOr = A.isImmediateControlLaneText('/coordinator worker');
out.lanePlain = A.isImmediateControlLaneText('hello');
out.laneSlashPrompt = A.isImmediateControlLaneText('/cursor fix it');
out.laneEmpty = A.isImmediateControlLaneText('');
// sticky resolution after send
out.stickKeep = A.stickyCommandAfterSend({ message: '/cursor' }, { stickyOf });
out.stickPrompt = A.stickyCommandAfterSend({ message: '/cursor fix it' }, { stickyOf });
out.stickNone = A.stickyCommandAfterSend({ message: 'hello' }, { stickyOf });
out.stickNoDep = A.stickyCommandAfterSend({ message: '/cursor' }, null);
// send-dispatch plan
const P = (o) => A.composerSendPlan(Object.assign(
  { guardSet: false, generating: false, generatingBeforeHeal: false, sendable: true,
    control: false, message: 'hi', normalizedMessage: 'hi', inFlightMessage: 'old',
    queuedMessages: [] }, o));
out.planNormal = P({});
out.planGuard = P({ guardSet: true });
out.planGuardGen = P({ guardSet: true, generating: true, generatingBeforeHeal: true });
out.planEmpty = P({ sendable: false });
out.planFollow = P({ generating: true, generatingBeforeHeal: true });
out.planDupFlight = P({ generating: true, generatingBeforeHeal: true, inFlightMessage: 'hi' });
out.planDupQueue = P({ generating: true, generatingBeforeHeal: true,
  queuedMessages: ['other', 'hi'] });
out.planNoDup = P({ generating: true, generatingBeforeHeal: true, inFlightMessage: '' });
out.planControl = P({ generating: true, generatingBeforeHeal: true, control: true });
out.planAttachOnly = P({ message: '', normalizedMessage: '' });
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
def test_enter_submit_interpretation():
    res = _run()
    assert res["enter"] is True
    assert res["shiftEnter"] is False  # Shift+Enter never submits
    assert res["otherKey"] is False
    assert res["touchEnter"] is False  # touch needs Ctrl/Meta+Enter
    assert res["touchCtrl"] is True
    assert res["touchMeta"] is True


@node_only
def test_send_eligibility():
    res = _run()
    assert res["bareCursor"] is False  # bare sticky token is not a prompt
    assert res["bareCodex"] is False
    assert res["prompt"] is True
    assert res["plain"] is True
    assert res["empty"] is False
    assert res["attachOnly"] is True  # attachment-only sends go through
    assert res["control"] is True
    assert res["controlSticky"] is True  # control wins after strip
    assert res["usage"] is True  # nested one-shots are real turns
    assert res["usageBare"] is True
    assert res["modelOnly"] is False  # setting chip alone is not a prompt
    assert res["modelPrompt"] is True
    assert res["modelRefresh"] is True
    assert res["costNested"] is True
    assert res["noDeps"] is True  # plain text needs no domain callbacks


@node_only
def test_control_lane_text_and_sticky_resolution():
    res = _run()
    assert res["laneRestart"] is True
    assert res["laneSticky"] is True  # sticky chip dropped before matching
    assert res["laneCoord"] is True
    assert res["laneCoordNo"] is False
    assert res["laneOr"] is True
    assert res["lanePlain"] is False
    assert res["laneSlashPrompt"] is False
    assert res["laneEmpty"] is False
    assert res["stickKeep"] == {"prefix": "/cursor ", "label": "cursor"}
    assert res["stickPrompt"] == {"prefix": "/cursor ", "label": "cursor"}
    assert res["stickNone"] is None  # page clears all chips
    assert res["stickNoDep"] is None


@node_only
def test_send_dispatch_plan():
    res = _run()
    assert res["planNormal"] == {"action": "normal", "outbound": "hi",
                                "controlLane": False}
    assert res["planGuard"] == {"action": "ignore-guard"}
    # guard never blocks once a turn claimed the generating slot
    assert res["planGuardGen"] == {"action": "followup", "outbound": "hi"}
    assert res["planEmpty"] == {"action": "ignore-empty"}
    assert res["planFollow"] == {"action": "followup", "outbound": "hi"}
    assert res["planDupFlight"] == {"action": "ignore-duplicate", "outbound": "hi"}
    assert res["planDupQueue"] == {"action": "ignore-duplicate", "outbound": "hi"}
    assert res["planNoDup"]["action"] == "followup"
    # control lane bypasses the queue while generating
    assert res["planControl"] == {"action": "normal", "outbound": "hi",
                                 "controlLane": True}
    # attachment-only normal send falls back to the placeholder outbound
    assert res["planAttachOnly"] == {"action": "normal",
                                    "outbound": "(see attached files)",
                                    "controlLane": False}


@node_only
def test_chat_composer_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

# Executes the REAL sendMessage from chat_page.js (extracted by exact
# markers, never copied) with stubbed page dependencies. The real
# takePendingAttachments / clearPendingAttachments /
# normalizeMessageContentForMatch bodies are extracted too; everything
# else (DOM, prefs, fetch, steer/queue execution, send pipeline) is a
# tracking stub. This pins effect ORDER — a take/dismiss hoisted above
# the duplicate check fails here even though the text inside the
# duplicate branch is unchanged.
SEND_HARNESS = """
const fs = require('fs');
const SRC = fs.readFileSync(process.env.CHAT_PAGE_JS, 'utf-8');
const span = (s, e) => SRC.slice(SRC.indexOf(s), SRC.indexOf(e, SRC.indexOf(s)));
const CuttleChatComposer = require(process.env.MOD_JS);

async function scenario(cfg) {
  const calls = { take: 0, dismiss: 0, steer: 0, enqueue: 0,
                  process: 0, effort: 0, sticky: 0 };
  let pendingAttachments = cfg.staged.map((f) => ({ filename: f }));
  let openCards = cfg.cards.map((id) => ({ id }));
  let inFlightUserMessage = cfg.inFlight;
  // sendMessage reads the owned queue object; seed it like production.
  const CuttleFollowupQueue = require(process.env.MOD_FQ);
  let followupQueue = CuttleFollowupQueue.createQueueState();
  cfg.queued.forEach((content) => CuttleFollowupQueue.enqueue(followupQueue,
    { content }, { now: () => 1, rand: () => 0.5 }));
  let sendDispatchGuard = false;
  let currentSessionId = 'CH-duptest';
  const input = { id: 'chatInput', value: cfg.composerText };
  const document = { getElementById: (id) => (id === 'chatInput' ? input : null) };
  const window = {};
  const LOG = () => {}, LOG_ERR = () => {};
  const isSessionGenerating = () => cfg.generating;
  const composeMessageWithSlashChip = (t) => t.value;
  const isSendableComposerMessage = (m, a) =>
    CuttleChatComposer.isSendableComposerMessage(m, a, null);
  const healStaleGeneratingState = () => {};
  const maybeRefreshCursorModelsCatalogFromOutbound = () => {};
  const isImmediateControlLaneMessage = (t) =>
    CuttleChatComposer.isImmediateControlLaneText(t);
  const clearComposerDraft = () => {};
  const applyStickySlashAfterComposerSend = () => { calls.sticky++; };
  const autoResizeTextarea = () => {};
  const trySteerRunningTurn = async () => { calls.steer++; return false; };
  const enqueueFollowup = () => { calls.enqueue++; };
  const formatMessageWithAttachments = (m) => m;
  const dismissOpenInteractiveCards = () => { calls.dismiss++; openCards.length = 0; };
  const ensureCodexEffortForSend = async () => { calls.effort++; };
  const processMessage = async () => { calls.process++; };
  const beginLocalGeneration = () => { calls.process++; };
  const addMessageToUI = () => { calls.process++; };
  const saveChatSession = () => { calls.process++; };
  const _clearPersistedPending = () => {};
  const renderAttachmentChips = () => {};
  eval(span('    function clearPendingAttachments() {',
            '    function isImageAttachment(att) {'));
  eval(span('    function normalizeMessageContentForMatch(s) {',
            '    function claimUnmarkedMessageEl('));
  const _realTake = takePendingAttachments;
  takePendingAttachments = (...a) => { calls.take++; return _realTake(...a); };
  eval(span('    async function sendMessage(opts) {',
            '    function userAvatarInnerHtmlForChat() {'));
  await sendMessage();
  return { calls, inputValue: input.value,
           stagedLeft: pendingAttachments.map((a) => a.filename),
           cardsLeft: openCards.map((c) => c.id) };
}

(async () => {
  const base = { composerText: 'hi', staged: ['a.png'], cards: [7],
                 generating: true, inFlight: 'hi', queued: [] };
  const out = {};
  out.dupFlight = await scenario(base);
  out.dupQueue = await scenario(Object.assign({}, base,
    { inFlight: 'other', queued: ['other', 'hi'] }));
  out.followup = await scenario(Object.assign({}, base,
    { composerText: 'hello new', inFlight: 'other', queued: [] }));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run_send_scenarios():
    import os
    proc = subprocess.run(
        ["node", "-e", SEND_HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "CHAT_PAGE_JS": str(CHAT_PAGE_JS),
             "MOD_FQ": str(REPO_ROOT / "src" / "web" / "js" / "chat/chat_followup_queue.js")},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_send_message_duplicate_preserves_staged_attachments_and_cards():
    """Slice 6 regression, executed against the real sendMessage.

    The original bug hoisted takePendingAttachments +
    dismissOpenInteractiveCards ABOVE the duplicate check, so an
    ignored duplicate swallowed staged attachments and dismissed
    interactive cards. These scenarios fail if any take/dismiss runs
    before (or inside) the duplicate early-return, for both
    in-flight and queued duplicates.

    Duplicate sends DO clear composer text (input reset + sticky
    re-resolve); they must NOT clear staged attachments or cards.
    """
    res = _run_send_scenarios()
    for name in ("dupFlight", "dupQueue"):
        dup = res[name]
        assert dup["calls"]["take"] == 0, name
        assert dup["calls"]["dismiss"] == 0, name
        assert dup["calls"]["steer"] == 0, name
        assert dup["calls"]["enqueue"] == 0, name
        assert dup["calls"]["process"] == 0, name
        assert dup["calls"]["effort"] == 0, name
        assert dup["stagedLeft"] == ["a.png"], name
        assert dup["cardsLeft"] == [7], name
        # Composer text IS cleared on duplicates; attachments are not.
        assert dup["inputValue"] == "", name
        assert dup["calls"]["sticky"] == 1, name


@node_only
def test_send_message_followup_takes_attachments_and_cards():
    """Non-duplicate control: a real follow-up takes staged state."""
    res = _run_send_scenarios()
    follow = res["followup"]
    assert follow["calls"]["take"] == 1
    assert follow["calls"]["dismiss"] == 1
    assert follow["calls"]["steer"] == 1
    assert follow["calls"]["enqueue"] == 1
    assert follow["calls"]["process"] == 0
    assert follow["stagedLeft"] == []
    assert follow["cardsLeft"] == []
    assert follow["inputValue"] == ""


@node_only
def test_draft_chips_keep_commands_and_model_companions_without_aliasing():
    import os
    proc = subprocess.run(["node", "-e", """
const A = require(process.env.MOD_JS);
const chips = [
  {prefix: '/cursor ', category: 'command'},
  {prefix: '/model custom', category: 'cursor-model', modelId: 'custom'},
  {prefix: '/usage live', category: 'command', label: 'Live usage'},
];
const saved = A.draftChips(chips);
chips[2].label = 'changed';
process.stdout.write(JSON.stringify({saved, empty: A.draftChips(null)}));
"""], capture_output=True, text=True,
        env={**os.environ, "MOD_JS": str(MOD_JS)}, timeout=10)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert [row['prefix'] for row in result['saved']] == ['/cursor ', '/model custom', '/usage live']
    assert result['saved'][1]['modelId'] == 'custom'
    assert result['saved'][2]['label'] == 'Live usage'
    assert result['empty'] == []
