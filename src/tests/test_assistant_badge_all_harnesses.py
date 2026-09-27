"""Assistant reply badges must carry model (+ effort) for every harness.

Regression: CH-000497-8 — Codex replies persisted a bare ``Codex`` chip
(category ``command``) while Muse/Hermes/OpenCode got ``Agent - model · effort``.
The enricher is now harness-wide; this matrix locks that for every bundled
agent, not just the one that bit us.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from api import web_chat_api as w

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_ROOT = REPO_ROOT / "src" / "api" / "agent_harness" / "agents"
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

# Agents whose reply chips share palette chrome + model enrichment.
PALETTE_AGENTS = frozenset(w._HARNESS_PALETTE_AGENTS) - {"cursor"}

# Synthetic models / efforts used in the matrix (ids need not exist in catalogs).
SAMPLE_MODEL = {
    "muse": "muse-spark-1.2",
    "hermes": "claude-sonnet-4",
    "opencode": "opencode/big-pickle",
    "codex": "gpt-5.6-luna",
    "claude": "claude-opus-4",
    "deepseek": "deepseek-chat",
    "antigravity": "antigravity-default",
}
SAMPLE_EFFORT = "medium"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _harness_agent_ids() -> List[str]:
    ids: List[str] = []
    for man in sorted(AGENTS_ROOT.glob("*/manifest.yaml")):
        text = man.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?m)^id:\s*([A-Za-z0-9_-]+)\s*$", text)
        if m:
            ids.append(m.group(1).strip().lower())
    return ids


def _manifest_supports_effort(agent_id: str) -> bool:
    path = AGENTS_ROOT / agent_id / "manifest.yaml"
    if not path.is_file():
        return agent_id in ("muse", "hermes", "opencode", "codex")
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?m)^supports_effort:\s*false\s*$", text):
        return False
    if re.search(r"(?m)^efforts:\s*$", text) or re.search(
        r"(?m)^efforts:\s*\[", text
    ):
        return True
    # Codex/Hermes/OpenCode/Muse store effort even when efforts: is omitted.
    return agent_id in ("muse", "hermes", "opencode", "codex")


HARNESS_AGENTS = _harness_agent_ids()


@pytest.fixture(params=sorted(PALETTE_AGENTS & set(HARNESS_AGENTS)))
def harness_agent(request):
    return request.param


def test_harness_inventory_includes_codex_and_muse():
    assert "codex" in HARNESS_AGENTS
    assert "muse" in HARNESS_AGENTS
    assert "codex" in PALETTE_AGENTS
    # Every bundled agent (incl. cursor) should be a palette agent.
    assert not (set(HARNESS_AGENTS) - set(w._HARNESS_PALETTE_AGENTS)), (
        f"Harness agents missing from _HARNESS_PALETTE_AGENTS: "
        f"{sorted(set(HARNESS_AGENTS) - set(w._HARNESS_PALETTE_AGENTS))}"
    )


def test_slash_agent_chip_category_is_harness_not_command(harness_agent):
    chip = w._slash_agent_chip(f"{harness_agent}_command")
    assert chip is not None
    assert chip["category"] == harness_agent, chip
    assert chip["category"] != "command"


def test_assistant_metadata_includes_model_for_every_harness(harness_agent):
    model = SAMPLE_MODEL.get(harness_agent) or f"{harness_agent}-model-x"
    res: Dict = {
        "type": f"{harness_agent}_command",
        "agent_model": model,
        "agent_id": harness_agent,
        "query_id": "badge-matrix",
        "session_id": "sess-badge",
    }
    wants_effort = _manifest_supports_effort(harness_agent)
    if wants_effort:
        res["agent_effort"] = SAMPLE_EFFORT

    meta = w._assistant_message_metadata(res)
    assert meta and meta.get("slash_command"), meta
    chip = meta["slash_command"]["chips"][0]
    assert chip["category"] == harness_agent, chip
    assert model in chip["meta"], chip
    assert " - " in chip["label"], (
        f"{harness_agent} badge stayed bare: {chip['label']!r}"
    )
    # Must not be the stock agent-only label.
    stock = (w._SLASH_AGENT_CHIPS.get(harness_agent) or (None,))[0]
    assert chip["label"] != stock, chip
    if wants_effort:
        assert SAMPLE_EFFORT in chip["label"], chip
        assert f"effort {SAMPLE_EFFORT}" in chip["meta"], chip


def test_codex_ch000497_8_shape():
    """Exact failure shape from CH-000497-8: model+effort present on result."""
    meta = w._assistant_message_metadata(
        {
            "type": "codex_command",
            "agent_model": "gpt-5.6-luna",
            "agent_effort": "medium",
            "query_id": "b35af74d",
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "model": "gpt-5.6-luna",
            },
        }
    )
    chip = meta["slash_command"]["chips"][0]
    assert chip["category"] == "codex"
    assert "Luna" in chip["label"] or "gpt-5.6-luna" in chip["label"]
    assert "medium" in chip["label"]
    assert chip["label"] != "Codex"


def test_sse_passthrough_includes_canonical_badge_keys():
    text = (REPO_ROOT / "src" / "api" / "web_chat_api.py").read_text(encoding="utf-8")
    # Locate the streaming response allowlist near agent_model passthrough.
    assert "'agent_effort'" in text
    assert "'agent_id'" in text
    assert "'codex_effort'" in text


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


@node_only
def test_history_panel_prefers_sticky_model_effort_over_bare_title():
    """Title `/codex Foo` must not shadow sticky `Codex - model · effort`."""
    src = CHAT_JS.read_text(encoding="utf-8")
    escape = (
        "function escapeHtml(s) {\n"
        "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
        ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n}\n"
        "function escapeHtmlInline(s) { return escapeHtml(s); }\n"
    )
    # Minimal stubs the extracted helpers close over.
    stubs = r"""
const currentSessionId = null;
const slashPaletteSupplement = {};
const TITLE_SLASH_SKIP = { help: 1, pipelines: 1, project: 1, cd: 1 };
function toAuthDbSessionId(s) { return s; }
function canonicalizeChatSessionId(s) { return s; }
function getSessionPrefs(sid) {
  if (String(sid) !== '497') return null;
  return {
    stickyChips: [{
      prefix: '/codex ',
      label: 'Codex - GPT-5.6 Luna · medium',
      category: 'codex',
    }],
  };
}
function buildProjectCommandPaletteItems() { return []; }
function cursorChipDisplayLabel(m) { return 'Cursor - ' + (m || 'Auto'); }
function isCurrentHistorySession() { return false; }
function liveAgentBadgeLabelForChip() { return ''; }
function collapseCursorSlashChips(chips) { return Array.isArray(chips) ? chips.slice() : []; }
function resolveSlashChipPaletteCategory(chip) {
  const id = composerChipAgentId(chip);
  return id || (chip && chip.category) || 'command';
}
function slashCommandChipHistoryHtml(label, meta, category) {
  return '<span data-label="' + escapeHtmlInline(label) + '" data-cat="' +
    escapeHtmlInline(category || '') + '"></span>';
}
function agentModelEffortBadgeLabel(agentName, modelLabel, effort) {
  const name = String(agentName || '').trim();
  const model = String(modelLabel || '').trim();
  const eff = String(effort || '').trim();
  if (!model && !eff) return '';
  if (model && eff) return name + ' - ' + model + ' · ' + eff;
  if (model) return name + ' - ' + model;
  return name + ' · ' + eff;
}
function prettyCodexModelLabel(m) { return m; }
function prettyMuseModelLabel(m) { return m; }
function prettyHermesModelLabel(m) { return m; }
function prettyOpenCodeModelLabel(m) { return m; }
"""
    composer = _extract(
        src,
        "    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */",
        "    /**\n     * Indexes to delete when the × on chips[idx] is clicked.",
    )
    bare = _extract(
        src,
        "    function isBareAgentChipLabel(chip) {",
        "    /**\n     * Unify agent chips for history / queue / titles:",
    )
    normalize = _extract(
        src,
        "    function normalizeAgentSlashChips(chips, sessionId) {",
        "    const TITLE_SLASH_SKIP = { help: 1, pipelines: 1, project: 1, cd: 1 };",
    )
    title_key = _extract(
        src,
        "    function titleChipKey(c) {",
        "    function parseProjectOrGenericSlashHead(rest) {",
    )
    parse_generic = _extract(
        src,
        "    function parseProjectOrGenericSlashHead(rest) {",
        "    function parseTitleSlashChips(raw) {",
    )
    parse_title = _extract(
        src,
        "    function parseTitleSlashChips(raw) {",
        "    function sessionPrefsForHistory(sessionId) {",
    )
    prefs = _extract(
        src,
        "    function sessionPrefsForHistory(sessionId) {",
        "    function stickyTitleChipsForSession(sessionId) {",
    )
    sticky = _extract(
        src,
        "    function stickyTitleChipsForSession(sessionId) {",
        "    /**\n     * Split a stored chat name into the chips row (meta line) and the plain",
    )
    title_parts = _extract(
        src,
        "    function chatTitleParts(title, sessionId) {",
        "    /**\n     * Re-paint agent chips in the history panel (e.g. after Muse model pin",
    )
    # parseTitleSlashChips calls parseStoredSlashCommandHead — stub it.
    stub_stored = (
        "function parseStoredSlashCommandHead() { return null; }\n"
    )
    harness = f"""
{escape}
{stubs}
{stub_stored}
{composer}
{bare}
{normalize}
{title_key}
{parse_generic}
{parse_title}
{prefs}
{sticky}
{title_parts}
const parts = chatTitleParts('/codex Error Handling in Cuttle', '497');
process.stdout.write(JSON.stringify({{
  html: parts.chipsHtml,
  title: parts.titleHtml,
  hasLuna: parts.chipsHtml.includes('Luna'),
  hasMedium: parts.chipsHtml.includes('medium'),
  hasCodexCat: parts.chipsHtml.includes('data-cat=&quot;codex&quot;')
    || parts.chipsHtml.includes('data-cat="codex"'),
  bareOnly: /data-label="codex"/i.test(parts.chipsHtml)
    && !/Luna/i.test(parts.chipsHtml),
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    res = json.loads(proc.stdout)
    assert res["hasLuna"] is True, res
    assert res["hasMedium"] is True, res
    assert res["bareOnly"] is False, res


@node_only
def test_composer_chip_agent_id_reads_meta_without_prefix():
    """History chips with only meta `/codex` must resolve to agent id codex."""
    src = CHAT_JS.read_text(encoding="utf-8")
    composer = _extract(
        src,
        "    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */",
        "    /**\n     * Indexes to delete when the × on chips[idx] is clicked.",
    )
    harness = f"""
{composer}
const cases = {{
  metaOnly: composerChipAgentId({{ label: 'Codex', meta: '/codex', category: 'command' }}),
  metaEffort: composerChipAgentId({{
    label: 'Codex - GPT-5.6 Luna · medium',
    meta: '/codex · model gpt-5.6-luna · effort medium',
    category: 'command',
  }}),
  labelOnly: composerChipAgentId({{ label: 'codex', meta: '', category: 'command' }}),
  prefix: composerChipAgentId({{ prefix: '/muse ', label: 'Muse Code', category: 'muse' }}),
}};
process.stdout.write(JSON.stringify(cases));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["metaOnly"] == "codex", res
    assert res["metaEffort"] == "codex", res
    assert res["labelOnly"] == "codex", res
    assert res["prefix"] == "muse", res
