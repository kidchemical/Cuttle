"""Composer / send-planning frontend domain.

Behavioral characterization of src/web/js/chat_composer.js under node
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

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_composer.js"

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
