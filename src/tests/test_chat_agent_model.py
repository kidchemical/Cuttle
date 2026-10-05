"""Agent/Model controls frontend domain.

Behavioral characterization of src/web/js/chat_agent_model.js under node
(Phase 3 Slice 7). Covers the pure Agent/Model control decisions moved out
of chat_page.js: canonical backend pin reads, starred model/effort default
lookups, starred-default row matching, send-time agent_pins construction,
the Codex pre-send effort fetch gate, and badge effort resolution.

The page keeps: supplement state, all fetch/POST transport, palette/badge
DOM, session-prefs IO, sticky restore + override orchestration, and the
send-path ordering (ensureCodexEffortForSend still precedes addMessageToUI).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_agent_model.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// sessionPin: nested agent_pins win, legacy flat keys fall back
out.pinNested = A.sessionPin({ agent_pins: { codex: { effort: 'xhigh' } } }, 'codex', 'effort');
out.pinNestedBlank = A.sessionPin({ agent_pins: { codex: { effort: '  ' } }, codex_effort: 'low' }, 'codex', 'effort');
out.pinFlat = A.sessionPin({ codex_effort: 'low' }, 'codex', 'effort');
out.pinMissing = A.sessionPin({}, 'codex', 'effort');
out.pinNull = A.sessionPin(null, 'codex', 'effort');
out.pinModel = A.sessionPin({ agent_pins: { muse: { model: 'muse-spark-1.2' } } }, 'muse', 'model');
// starred lookups are case-insensitive over the explicit map
out.starModel = A.starredAgentModel({ codex: 'gpt-5.6' }, 'CODEX');
out.starEffort = A.starredAgentEffort({ codex: 'xhigh' }, 'Codex');
out.starMissing = A.starredAgentModel({}, 'codex');
out.starNullMap = A.starredAgentEffort(null, 'codex');
// starred-default row matching
out.rowMatch = A.isAgentDefaultStarred(
  { kind: 'effort', agent: 'codex' }, { modelId: 'XHIGH' }, 'xhigh');
out.rowMismatch = A.isAgentDefaultStarred(
  { kind: 'effort', agent: 'codex' }, { modelId: 'low' }, 'xhigh');
out.rowNoStar = A.isAgentDefaultStarred(
  { kind: 'effort', agent: 'codex' }, { modelId: 'xhigh' }, '');
out.rowNoSpec = A.isAgentDefaultStarred(null, { modelId: 'xhigh' }, 'xhigh');
out.rowModelKind = A.isAgentDefaultStarred(
  { kind: 'model', agent: 'codex' }, { modelId: 'gpt-5.6' }, 'gpt-5.6');
// pins: active harness always attaches current composer values
const MODELS = (over) => Object.assign({
  muse: { model: '', effort: '', modelDirty: false, effortDirty: false },
  hermes: { model: '', effort: '', modelDirty: false, effortDirty: false },
  opencode: { model: '', effort: '', modelDirty: false, effortDirty: false },
  codex: { model: 'gpt-5.6', effort: 'xhigh', modelDirty: false, effortDirty: false },
}, over);
out.pinsActive = A.buildAgentPinsForRequest({ message: '/codex fix it', models: MODELS() });
// dirty-only for inactive harnesses: pre-session picks are not lost
out.pinsDirty = A.buildAgentPinsForRequest({ message: 'hello', models: MODELS({
  muse: { model: 'muse-spark-1.2', effort: '', modelDirty: true, effortDirty: false },
}) });
// clean + no active harness -> no pins key content
out.pinsEmpty = A.buildAgentPinsForRequest({ message: 'hello', models: MODELS({
  codex: { model: '', effort: '', modelDirty: false, effortDirty: false },
}) });
// effort-only entry still pins (no model required)
out.pinsEffortOnly = A.buildAgentPinsForRequest({ message: '/hermes go', models: MODELS({
  hermes: { model: '', effort: 'ultra', modelDirty: false, effortDirty: false },
}) });
// codex fetch gate
const G = (o) => A.codexEffortFetchForSend(Object.assign(
  { messageText: '/codex fix it', hasCodexChip: false, codexEffortDirty: false,
    sessionKey: 's1', effortKey: '' }, o));
out.gateFetch = G({});
out.gateKeyMatch = G({ effortKey: 's1' });
out.gateDirty = G({ codexEffortDirty: true });
out.gateNoCodex = G({ messageText: 'hello' });
out.gateChip = G({ messageText: 'hello', hasCodexChip: true });
out.gateNoSession = G({ sessionKey: '' });
// badge effort: data chain first, then composer pins in fixed order
out.badgeData = A.resolveAgentEffortForBadge(
  { data: { codex_effort: 'max' }, pinnedEfforts: ['low', '', '', ''] });
out.badgeGeneric = A.resolveAgentEffortForBadge(
  { data: { agent_effort: 'high' }, pinnedEfforts: ['', '', '', ''] });
out.badgePinned = A.resolveAgentEffortForBadge(
  { data: {}, pinnedEfforts: ['', 'ultra', 'medium', 'xhigh'] });
out.badgeNone = A.resolveAgentEffortForBadge({ data: {}, pinnedEfforts: ['', '', '', ''] });
out.badgeNull = A.resolveAgentEffortForBadge({ data: null, pinnedEfforts: ['', '', '', ''] });
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
def test_session_pin_reads():
    res = _run()
    assert res["pinNested"] == "xhigh"
    # blank nested falls back to the legacy flat key
    assert res["pinNestedBlank"] == "low"
    assert res["pinFlat"] == "low"
    assert res["pinMissing"] == ""
    assert res["pinNull"] == ""
    assert res["pinModel"] == "muse-spark-1.2"


@node_only
def test_starred_lookups_and_row_match():
    res = _run()
    assert res["starModel"] == "gpt-5.6"
    assert res["starEffort"] == "xhigh"
    assert res["starMissing"] == ""
    assert res["starNullMap"] == ""
    assert res["rowMatch"] is True
    assert res["rowMismatch"] is False
    assert res["rowNoStar"] is False
    assert res["rowNoSpec"] is False
    assert res["rowModelKind"] is True


@node_only
def test_agent_pins_for_request():
    res = _run()
    assert res["pinsActive"] == {"codex": {"model": "gpt-5.6", "effort": "xhigh"}}
    assert res["pinsDirty"] == {"muse": {"model": "muse-spark-1.2"}}
    assert res["pinsEmpty"] == {}
    assert res["pinsEffortOnly"] == {"hermes": {"effort": "ultra"}}


@node_only
def test_codex_effort_fetch_gate():
    res = _run()
    assert res["gateFetch"] == {
        "fetch": True, "url": "/api/codex/effort?session=s1"}
    assert res["gateKeyMatch"] == {"fetch": False}
    assert res["gateDirty"] == {"fetch": False}
    assert res["gateNoCodex"] == {"fetch": False}
    assert res["gateChip"]["fetch"] is True
    assert res["gateNoSession"] == {"fetch": True, "url": "/api/codex/effort"}


@node_only
def test_badge_effort_resolution():
    res = _run()
    assert res["badgeData"] == "max"
    assert res["badgeGeneric"] == "high"
    assert res["badgePinned"] == "ultra"
    assert res["badgeNone"] == ""
    assert res["badgeNull"] == ""


@node_only
def test_chat_agent_model_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_page_delegates_agent_model_decisions_to_owned_module():
    """Narrow page adapters: same signatures, no duplicated decision logic."""
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    for adapter, owned in (
        ("function sessionPin(data, agent, kind)", "CuttleChatAgentModel.sessionPin"),
        ("function starredAgentModel(agentId)", "CuttleChatAgentModel.starredAgentModel"),
        ("function starredAgentEffort(agentId)", "CuttleChatAgentModel.starredAgentEffort"),
        ("function isAgentDefaultStarred(spec, cmd)",
         "CuttleChatAgentModel.isAgentDefaultStarred"),
        ("function attachAgentIdentityToRequest(requestBody)",
         "CuttleChatAgentModel.buildAgentPinsForRequest"),
        ("async function ensureCodexEffortForSend(message)",
         "CuttleChatAgentModel.codexEffortFetchForSend"),
    ):
        assert adapter in src, f"page adapter {adapter} must stay (same signature)"
        assert owned in src, f"page must delegate to {owned}"
    assert "CuttleChatAgentModel.resolveAgentEffortForBadge" in src


@node_only
def test_draft_pins_keep_explicit_overrides_without_freezing_defaults():
    script = """
const A = require(process.env.MOD_JS);
const state = {
  codexModel: 'custom', codexModelDirty: true,
  codexEffort: 'high', codexEffortDirty: true,
  museModel: 'starred', museModelDirty: false,
  hermesEffort: 'none', hermesEffortDirty: true,
};
const pins = A.draftOverrides(state);
const restored = A.draftSupplementPatch(pins);
process.stdout.write(JSON.stringify({ pins, restored }));
"""
    import os
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          env={**os.environ, "MOD_JS": str(MOD_JS)}, timeout=10)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result['pins'] == {'codex': {'model': 'custom', 'effort': 'high'},
                              'hermes': {'effort': 'none'}}
    assert result['restored'] == {
        'codexModel': 'custom', 'codexModelDirty': True,
        'codexEffort': 'high', 'codexEffortDirty': True,
        'hermesEffort': 'none', 'hermesEffortDirty': True,
    }
