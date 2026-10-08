"""Agent/Model controls frontend domain.

Behavioral characterization of src/web/js/chat/chat_agent_model.js under node
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

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_agent_model.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// Session preferences use only the canonical agent_pins object
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
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_session_pin_reads():
    res = _run()
    assert res["pinNested"] == "xhigh"
    # Flat response keys are obsolete; blanks stay blank
    assert res["pinNestedBlank"] == ""
    assert res["pinFlat"] == ""
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
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
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
        ("function loadClaudeModelsForPalette(anyChat, opts)",
         "CuttleChatAgentModel.claudeModelsFetchForPalette"),
        ("function loadClaudeModelsForPalette(anyChat, opts)",
         "CuttleChatAgentModel.applyClaudeModelsResponse"),
        ("function persistClaudeModelSelection(modelId)",
         "CuttleChatAgentModel.isClaudeModelRefreshPick"),
        ("function persistClaudeModelSelection(modelId)",
         "CuttleChatAgentModel.markClaudeCurrentModel"),
        ("function buildClaudeModelPaletteItems(filterLower)",
         "CuttleChatAgentModel.claudeModelFilterForPalette"),
        ("function buildClaudeModelPaletteItems(filterLower)",
         "CuttleChatAgentModel.buildClaudeModelRow"),
        ("function seedClaudeSupplementFromSessionData(data, sessionId)",
         "CuttleChatAgentModel.claudeSeedPatchFromSessionData"),
        ("function loadClaudeEffortForPalette()",
         "CuttleChatAgentModel.claudeEffortFetchForPalette"),
        ("function loadClaudeEffortForPalette()",
         "CuttleChatAgentModel.applyClaudeEffortResponse"),
        ("function buildClaudeEffortPaletteItems(filterLower)",
         "CuttleChatAgentModel.claudeEffortFilterForPalette"),
        ("function buildClaudeEffortPaletteItems(filterLower)",
         "CuttleChatAgentModel.claudeEffortLevelsForModel"),
        ("function buildClaudeEffortPaletteItems(filterLower)",
         "CuttleChatAgentModel.buildClaudeEffortRow"),
        ("function prettyClaudeModelLabel(model)",
         "CuttleChatAgentModel.prettyClaudeModelLabel"),
        ("function claudeModelLabel(model)",
         "CuttleChatAgentModel.claudeModelLabel"),
    ):
        assert adapter in src, f"page adapter {adapter} must stay (same signature)"
        assert owned in src, f"page must delegate to {owned}"
    assert "CuttleChatAgentModel.resolveAgentEffortForBadge" in src


CLAUDE_HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// palette filter normalization strips agent + section prefixes
out.modelFilter = A.claudeModelFilterForPalette('claude model opus');
out.modelFilterBare = A.claudeModelFilterForPalette('claude');
out.modelFilterModels = A.claudeModelFilterForPalette('claude models opus 4');
out.modelFilterNoPrefix = A.claudeModelFilterForPalette('model opus');
out.effortFilter = A.claudeEffortFilterForPalette('claude effort high');
out.effortFilterBare = A.claudeEffortFilterForPalette('claude effort');
out.effortFilterWord = A.claudeEffortFilterForPalette('effort');
// model row mapping: favorite + current + per-model efforts
out.rowFav = A.buildClaudeModelRow(
  { id: 'opus-4', label: 'Opus 4', favorite: true,
    efforts: ['low', 'high'], description: 'Top model' }, 'opus-4');
out.rowPlain = A.buildClaudeModelRow({ id: 'sonnet' }, 'opus-4');
out.rowBlank = A.buildClaudeModelRow({ id: '  ' }, '');
out.rowNull = A.buildClaudeModelRow(null, '');
// refresh-pick detection covers the `refresh` alias
out.refreshPlain = A.isClaudeModelRefreshPick('refresh');
out.refreshMagic = A.isClaudeModelRefreshPick('__refresh__');
out.refreshCase = A.isClaudeModelRefreshPick('REFRESH');
out.refreshNo = A.isClaudeModelRefreshPick('opus-4');
out.refreshEmpty = A.isClaudeModelRefreshPick('');
// current-flagging is a pure map over the cached catalog
out.marked = A.markClaudeCurrentModel([{ id: 'a' }, { id: 'b' }], 'b');
// effort levels: selected model wins, unknown model means none,
// no model falls back to catalog-wide common levels
const MODELS = [{ id: 'opus-4', efforts: ['low', 'high'] }, { id: 'bare' }];
out.levelsModel = A.claudeEffortLevelsForModel(
  { selectedModel: 'OPUS-4', models: MODELS, commonEfforts: ['med'] });
out.levelsBare = A.claudeEffortLevelsForModel(
  { selectedModel: 'bare', models: MODELS, commonEfforts: ['med'] });
out.levelsUnknown = A.claudeEffortLevelsForModel(
  { selectedModel: 'nope', models: MODELS, commonEfforts: ['med'] });
out.levelsCommon = A.claudeEffortLevelsForModel(
  { selectedModel: '', models: MODELS, commonEfforts: ['med'] });
out.levelsNull = A.claudeEffortLevelsForModel(null);
// effort row mapping marks the current pick
out.effortRow = A.buildClaudeEffortRow('high',
  { preferredLower: 'high', selectedModel: 'opus-4' });
out.effortRowDefault = A.buildClaudeEffortRow('low',
  { preferredLower: '', selectedModel: '' });
// seed patch: nested pins win, dirty picks are left alone,
// a bare pin presence clears a non-dirty effort
out.seedFull = A.claudeSeedPatchFromSessionData(
  { agent_pins: { claude: { model: 'opus-4', effort: 'high' } } },
  { modelDirty: false, effortDirty: false, sessionKey: '7' });
out.seedDirty = A.claudeSeedPatchFromSessionData(
  { agent_pins: { claude: { model: 'opus-4', effort: 'high' } } },
  { modelDirty: true, effortDirty: true, sessionKey: '7' });
out.seedFlat = A.claudeSeedPatchFromSessionData(
  { claude_model: 'sonnet', claude_effort: 'low' },
  { modelDirty: false, effortDirty: false, sessionKey: '7' });
out.seedClear = A.claudeSeedPatchFromSessionData(
  { agent_pins: { claude: {} } },
  { modelDirty: false, effortDirty: false, sessionKey: '7' });
out.seedNoKey = A.claudeSeedPatchFromSessionData(
  { agent_pins: { claude: { model: 'opus-4' } } },
  { modelDirty: false, effortDirty: false, sessionKey: '' });
// models fetch gate mirrors the codex gate shape
out.gateModelsNoChip = A.claudeModelsFetchForPalette(
  { anyChat: false, hasClaudeChip: false, sessionKey: '7' });
out.gateModelsAny = A.claudeModelsFetchForPalette(
  { anyChat: true, hasClaudeChip: false, loading: false,
    modelsLength: 0, modelsKey: '', sessionKey: '7', forceRefresh: false });
out.gateModelsCached = A.claudeModelsFetchForPalette(
  { anyChat: true, hasClaudeChip: false, loading: false,
    modelsLength: 3, modelsKey: '7', sessionKey: '7', forceRefresh: false });
out.gateModelsLoading = A.claudeModelsFetchForPalette(
  { anyChat: true, hasClaudeChip: true, loading: true,
    modelsLength: 0, modelsKey: '', sessionKey: '7', forceRefresh: false });
out.gateModelsRefresh = A.claudeModelsFetchForPalette(
  { anyChat: true, hasClaudeChip: true, loading: false,
    modelsLength: 3, modelsKey: '7', sessionKey: '7', forceRefresh: true });
out.gateModelsNoSession = A.claudeModelsFetchForPalette(
  { anyChat: true, hasClaudeChip: true, loading: false,
    modelsLength: 0, modelsKey: '', sessionKey: '', forceRefresh: false });
// models response normalization; dirty keeps the current pick (null)
out.applyOk = A.applyClaudeModelsResponse(
  { success: true, models: [{ id: 'a' }], preferredModel: 'a',
    source: 'cli', count: 9, commonEfforts: ['low'], error: '' },
  { modelDirty: false });
out.applyDirty = A.applyClaudeModelsResponse(
  { success: true, models: [{ id: 'a' }], preferredModel: 'a' },
  { modelDirty: true });
out.applyFail = A.applyClaudeModelsResponse(
  { success: false, error: 'boom' }, { modelDirty: false });
// effort fetch gate + response normalization
out.gateEffortNoChip = A.claudeEffortFetchForPalette(
  { hasClaudeChip: false, sessionKey: '7' });
out.gateEffort = A.claudeEffortFetchForPalette(
  { hasClaudeChip: true, loading: false, effortKey: '', sessionKey: '7' });
out.gateEffortKey = A.claudeEffortFetchForPalette(
  { hasClaudeChip: true, loading: false, effortKey: '7', sessionKey: '7' });
out.gateEffortNoSession = A.claudeEffortFetchForPalette(
  { hasClaudeChip: true, loading: false, effortKey: '', sessionKey: '' });
out.applyEffort = A.applyClaudeEffortResponse({ preferredEffort: 'high' });
out.applyEffortBlank = A.applyClaudeEffortResponse({});
// labels read the injected catalog, unknown ids title-case the leaf
const CATALOG = [{ id: 'opus-4', label: 'Opus 4' }];
out.prettyKnown = A.prettyClaudeModelLabel('opus-4', CATALOG);
out.prettyUnknown = A.prettyClaudeModelLabel('my-model/x', CATALOG);
out.prettyEmpty = A.prettyClaudeModelLabel('', CATALOG);
out.shortKnown = A.claudeModelLabel('opus-4', CATALOG);
out.shortUnknown = A.claudeModelLabel('zz', CATALOG);
out.shortEmpty = A.claudeModelLabel('', CATALOG);
process.stdout.write(JSON.stringify(out));
"""


def _run_claude():
    import os
    proc = subprocess.run(
        ["node", "-e", CLAUDE_HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_claude_palette_filters():
    res = _run_claude()
    assert res["modelFilter"] == "opus"
    assert res["modelFilterBare"] == ""
    assert res["modelFilterModels"] == "opus 4"
    assert res["modelFilterNoPrefix"] == "opus"
    assert res["effortFilter"] == "high"
    assert res["effortFilterBare"] == ""
    assert res["effortFilterWord"] == ""


@node_only
def test_claude_model_row_mapping():
    res = _run_claude()
    fav = res["rowFav"]
    assert fav["category"] == "claude-model"
    assert fav["prefix"] == "/claude model opus-4"
    assert fav["label"] == "★ Opus 4 (current)"
    assert fav["hint"] == "Top model · Supported efforts: low, high"
    assert fav["meta"] == "opus-4"
    assert fav["modelId"] == "opus-4"
    assert fav["claudeModel"] is True
    plain = res["rowPlain"]
    assert plain["label"] == "sonnet"
    assert plain["hint"] == "Set Claude Code model to sonnet · No effort levels for this model"
    assert res["rowBlank"] is None
    assert res["rowNull"] is None


@node_only
def test_claude_refresh_pick_and_current_marking():
    res = _run_claude()
    assert res["refreshPlain"] is True
    assert res["refreshMagic"] is True
    assert res["refreshCase"] is True
    assert res["refreshNo"] is False
    assert res["refreshEmpty"] is False
    assert res["marked"] == [{"id": "a", "current": False}, {"id": "b", "current": True}]


@node_only
def test_claude_effort_levels_and_rows():
    res = _run_claude()
    assert res["levelsModel"] == ["low", "high"]
    assert res["levelsBare"] == []
    assert res["levelsUnknown"] == []
    assert res["levelsCommon"] == ["med"]
    assert res["levelsNull"] == []
    row = res["effortRow"]
    assert row["prefix"] == "/claude effort high"
    assert row["label"] == "Effort high (current)"
    assert row["hint"] == "Set Claude Code effort (--effort) to high for opus-4"
    assert row["claudeEffort"] is True
    assert res["effortRowDefault"]["label"] == "Effort low"
    assert res["effortRowDefault"]["hint"].endswith("(CLI default model)")


@node_only
def test_claude_seed_patch():
    res = _run_claude()
    assert res["seedFull"] == {
        "model": "opus-4", "modelsKey": "7", "effort": "high", "effortKey": "7"}
    assert res["seedDirty"] == {}
    assert res["seedFlat"] == {}
    assert res["seedClear"] == {"effort": "", "effortKey": "7"}
    assert res["seedNoKey"] == {"model": "opus-4"}


@node_only
def test_claude_fetch_gates_and_response_normalization():
    res = _run_claude()
    assert res["gateModelsNoChip"] == {"fetch": False}
    assert res["gateModelsAny"] == {
        "fetch": True, "url": "/api/claude/models?session=7"}
    assert res["gateModelsCached"] == {"fetch": False}
    assert res["gateModelsLoading"] == {"fetch": False}
    assert res["gateModelsRefresh"] == {
        "fetch": True, "url": "/api/claude/models?session=7&refresh=1"}
    assert res["gateModelsNoSession"] == {"fetch": True, "url": "/api/claude/models"}
    applied = res["applyOk"]
    assert applied["models"] == [{"id": "a"}]
    assert applied["model"] == "a"
    assert applied["source"] == "cli"
    assert applied["count"] == 9
    assert applied["commonEfforts"] == ["low"]
    assert res["applyDirty"]["model"] is None
    assert res["applyDirty"]["models"] == [{"id": "a"}]
    failed = res["applyFail"]
    assert failed["models"] == []
    assert failed["model"] == ""
    assert failed["count"] == 0
    assert failed["error"] == "boom"
    assert res["gateEffortNoChip"] == {"fetch": False}
    assert res["gateEffort"] == {
        "fetch": True, "url": "/api/claude/effort?session=7"}
    assert res["gateEffortKey"] == {"fetch": False}
    assert res["gateEffortNoSession"] == {"fetch": True, "url": "/api/claude/effort"}
    assert res["applyEffort"] == "high"
    assert res["applyEffortBlank"] == ""


@node_only
def test_claude_labels():
    res = _run_claude()
    assert res["prettyKnown"] == "Opus 4"
    assert res["prettyUnknown"] == "X"
    assert res["prettyEmpty"] == "Claude Code"
    assert res["shortKnown"] == "Opus 4"
    assert res["shortUnknown"] == "zz"
    assert res["shortEmpty"] == "default"


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
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
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
