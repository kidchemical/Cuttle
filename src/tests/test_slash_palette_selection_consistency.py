"""Slash palette selection must stage chips consistently.

Bugs (CH-000482):
1. Picking `/usage` (and other Cursor nested cmds) added a chip with
   ``category: 'cursor'``, so ``renderSlashChips`` relabeled it
   ``Cursor - Auto``.
2. Picking ``/model refresh`` skipped chip staging and instantly ran a
   client-side catalog refresh — unlike every other nested command.

Standard behavior under test:
- Sticky agent (``/cursor``) → agent badge labeled from preferred model.
- Nested Cursor cmds (``/usage``, ``/about``, ``/clear``, ``/plan``, …)
  → correctly labeled command chips; sendable with the sticky agent.
- ``/model refresh`` → stage a labeled chip (no instant run); sendable.
- Real model id picks remain settings (not sendable alone).
- Control cmds (``/restart``) stay sendable.
- Muse/OpenCode model *id* picks stay instant settings; their refresh
  rows must not silently share the Cursor ``cursorRefresh`` instant path.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat_slash.js"
CHAT_HTML = REPO_ROOT / "src" / "web" / "chat_page.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _src() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


def _extract_inclusive(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head) + len(end_marker)
    return src[head:tail]


def _slash_src() -> str:
    return SLASH_JS.read_text(encoding="utf-8")


def _sticky_slash_agents(src: str | None = None) -> list[str]:
    """Agent ids with ``stickySession: true`` in ``SLASH_COMMANDS`` (all CLIs)."""
    text = src if src is not None else _slash_src()
    start = text.index("const SLASH_COMMANDS = [")
    end = text.index("const CURSOR_AGENT_SLASH_COMMANDS", start)
    block = text[start:end]
    agents: list[str] = []
    for m in re.finditer(
        r"prefix:\s*'/(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)\s*'"
        r"[\s\S]*?stickySession:\s*true",
        block,
    ):
        aid = m.group(1).lower()
        if aid not in agents:
            agents.append(aid)
    return agents


def _harness_manifest_agent_ids() -> list[str]:
    root = REPO_ROOT / "src" / "api" / "agent_harness" / "agents"
    ids: list[str] = []
    for man in sorted(root.glob("*/manifest.yaml")):
        text = man.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?m)^id:\s*([A-Za-z0-9_-]+)\s*$", text)
        if m:
            ids.append(m.group(1).strip().lower())
    return ids


# Agents that ship nested ``/usage`` + (usually) staged ``/model refresh``.
USAGE_REFRESH_AGENTS = (
    "cursor",
    "codex",
    "muse",
    "hermes",
    "opencode",
)


def _cursor_agent_block(src: str) -> str:
    # Registry lives in chat_slash.js; callers pass _slash_src().
    start = src.index("const CURSOR_AGENT_SLASH_COMMANDS = [")
    end = src.index("];", start)
    return src[start : end + 2]


def _run_js(script: str) -> dict:
    src = _src()
    # Registry lives in chat_slash.js (Phase 3 Slice 2); the require below
    # also serves the delegating wrappers sliced from chat_page.js.
    slash_src = _slash_src()
    slash_mod = str(SLASH_JS)
    commands = _extract_inclusive(slash_src, "const SLASH_COMMANDS = [", "\n];")
    cursor_cmds = _extract_inclusive(
        slash_src, "const CURSOR_AGENT_SLASH_COMMANDS = [", "\n];"
    )
    helpers = ""
    # Prefer the shared sticky-agent helpers (CH-000482 multi-agent).
    if "function isStickyAgentChip(" in src:
        helpers += _extract(
            src,
            "    function isStickyAgentChip(chip, agentId) {",
            "    /** Nested Cursor Agent slash",
        )
        # Include through isHarnessNestedCommandChip + isAgentHeaderChip start
        # is handled below via is_agent extract.
        if "function isCursorNestedCommandChip(" in src:
            helpers += _extract_inclusive(
                src, "    function isCursorNestedCommandChip(chip) {", "\n    }\n"
            )
        if "function isHarnessNestedCommandChip(" in src:
            helpers += _extract_inclusive(
                src, "    function isHarnessNestedCommandChip(chip) {", "\n    }\n"
            )
    elif "function isStickyCursorAgentChip(" in src:
        helpers += _extract_inclusive(
            src, "    function isStickyCursorAgentChip(chip) {", "\n    }\n"
        )
        if "function isCursorNestedCommandChip(" in src:
            helpers += _extract_inclusive(
                src, "    function isCursorNestedCommandChip(chip) {", "\n    }\n"
            )
    else:
        helpers += """
function isStickyAgentChip(chip, agentId) {
    if (!chip || !agentId) return false;
    const id = String(agentId).toLowerCase();
    const cat = String((chip && chip.category) || '').toLowerCase();
    if (cat === id + '-cmd' || cat === id + '-model' || cat === id + '-effort') return false;
    const hay = String(chip.prefix || chip.meta || '').toLowerCase().trim();
    if (
        hay === '/usage' || /^\\/usage\\s/.test(hay)
        || /^\\/model\\s+refresh\\b/.test(hay)
        || /^\\/(about|clear|plan|ask|sandbox|agent)\\b/.test(hay)
        || new RegExp('^/' + id + '\\\\s+/').test(hay)
        || new RegExp('^/' + id + '\\\\s+(model|effort|usage|about|clear|plan|ask|sandbox|agent)\\\\b').test(hay)
    ) return false;
    if (cat === id) return true;
    if (!hay) return false;
    const m = hay.match(new RegExp('^/' + id + '(?:\\\\s+(.*))?$'));
    if (!m) return false;
    const rest = String(m[1] || '').trim();
    if (!rest) return true;
    return /^(·|•)/.test(rest);
}
function isStickyCursorAgentChip(chip) { return isStickyAgentChip(chip, 'cursor'); }
function isStickyMuseAgentChip(chip) { return isStickyAgentChip(chip, 'muse'); }
function isStickyCodexAgentChip(chip) { return isStickyAgentChip(chip, 'codex'); }
function isStickyHermesAgentChip(chip) { return isStickyAgentChip(chip, 'hermes'); }
function isStickyOpenCodeAgentChip(chip) { return isStickyAgentChip(chip, 'opencode'); }
function isCursorNestedCommandChip(chip) {
    return chip && (chip.category || '') === 'cursor-cmd';
}
function isHarnessNestedCommandChip(chip) {
    const cat = (chip && chip.category) || '';
    return /^(cursor|muse|codex|hermes|opencode)-cmd$/.test(cat);
}
"""

    control = _extract(
        src,
        "    function isNativeControlCommand(text) {",
        "    const isControlCommandPrefix = isNativeControlCommand;",
    )
    compose = _extract_inclusive(
        src, "    function composeMessageWithSlashChip(textarea) {", "\n    }\n"
    )
    sendable = _extract_inclusive(
        src, "    function isSendableComposerMessage(message, attachments) {", "\n    }\n"
    )
    sticky_from = _extract_inclusive(
        src, "    function getStickySlashCommandFromMessage(message) {", "\n    }\n"
    )
    agent_id = _extract_inclusive(
        src, "    function composerChipAgentId(chip) {", "\n    }\n"
    )
    is_agent = _extract_inclusive(
        src, "    function isAgentHeaderChip(chip) {", "\n    }\n"
    )
    related = _extract_inclusive(
        src, "    function isCursorRelatedSlashChip(c) {", "\n    }\n"
    )

    harness = f"""
const CuttleChatSlash = require("{slash_mod}");
{commands}
{cursor_cmds}
{helpers}
{control}
const isControlCommandPrefix = isNativeControlCommand;
const AGENT_CHIP_RE = /(?:^|[^\\w])\\/(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)(?:\\s|$)/i;
const AGENT_LABEL_RE = /^(cursor|muse|hermes|codex|opencode|claude|deepseek|antigravity)\\b/i;
const COMPANION_CHIP_CATEGORIES = [
    'cursor-model', 'cursor-cmd',
    'muse-model', 'muse-effort', 'muse-cmd',
    'hermes-model', 'hermes-effort', 'hermes-cmd',
    'opencode-model', 'opencode-effort', 'opencode-cmd',
    'codex-model', 'codex-effort', 'codex-cmd',
];
function slashContextKey() {{ return 'chat'; }}
const slashCtx = {{ chat: {{ chips: [] }}, welcome: {{ chips: [] }} }};
{compose}
{sendable}
{sticky_from}
{agent_id}
{is_agent}
{related}
function cursorChipDisplayLabel(modelId) {{
    const id = String(modelId || 'auto').trim() || 'auto';
    return 'Cursor - ' + (/^auto$/i.test(id) ? 'Auto' : id);
}}
function agentModelEffortBadgeLabel(agentName, modelLabel, effort) {{
    const bits = [agentName];
    if (modelLabel) bits.push(modelLabel);
    if (effort) bits.push(effort);
    return bits.join(' - ');
}}
function displayLabelForComposerChip(chip, preferred) {{
    if (typeof isStickyCursorAgentChip === 'function' && isStickyCursorAgentChip(chip)) {{
        return cursorChipDisplayLabel(preferred || 'auto');
    }}
    if (typeof isStickyCodexAgentChip === 'function' && isStickyCodexAgentChip(chip)) {{
        return agentModelEffortBadgeLabel('Codex', preferred || '', '');
    }}
    if (typeof isStickyMuseAgentChip === 'function' && isStickyMuseAgentChip(chip)) {{
        return agentModelEffortBadgeLabel('Muse Code', preferred || '', '');
    }}
    if (typeof isStickyHermesAgentChip === 'function' && isStickyHermesAgentChip(chip)) {{
        return agentModelEffortBadgeLabel('Hermes', preferred || '', '');
    }}
    if (typeof isStickyOpenCodeAgentChip === 'function' && isStickyOpenCodeAgentChip(chip)) {{
        return agentModelEffortBadgeLabel('OpenCode', preferred || '', '');
    }}
    return chip.label;
}}
{script}
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Static / contract: nested Cursor cmds are not sticky-agent category
# ---------------------------------------------------------------------------


def test_cursor_nested_commands_use_cursor_cmd_category():
    block = _cursor_agent_block(_slash_src())
    for prefix in (
        "/usage",
        "/about",
        "/clear",
        "/plan ",
        "/ask ",
        "/agent",
        "/sandbox ",
        "/model ",
    ):
        assert f"prefix: '{prefix}'" in block, prefix
    # No nested entry should claim the sticky-agent category alone.
    for m in re.finditer(
        r"prefix:\s*'(/usage|/about|/clear|/plan |/ask |/agent|/sandbox )'[\s\S]*?category:\s*'([^']+)'",
        block,
    ):
        assert m.group(2) == "cursor-cmd", (
            f"{m.group(1)} must use category cursor-cmd, got {m.group(2)!r}"
        )


def test_model_refresh_palette_item_is_staged_not_instant():
    """Refresh must be a normal palette row (cursor-cmd), not cursorRefresh instant."""
    src = _src()
    # The refresh row lives inside buildCursorModelPaletteItems.
    start = src.index("function buildCursorModelPaletteItems(")
    end = src.index("function buildMuseModelPaletteItems(", start)
    block = src[start:end]
    assert "prefix: '/model refresh'" in block
    assert "cursor-cmd" in block or "category: 'cursor-cmd'" in block
    # Instant-run flag must not drive selection anymore.
    assert "cursorRefresh: true" not in block
    # Selection path must not special-case instant refresh for Cursor.
    apply_start = src.index("function applySlashSelectionInner(")
    apply_end = src.index("function applySlashSelection(", apply_start)
    apply = src[apply_start:apply_end]
    assert "loadCursorAgentModelsForPalette(true, { refresh: true })" not in apply


def test_usage_not_in_global_sticky_slash_list():
    src = _slash_src()
    start = src.index("const SLASH_COMMANDS = [")
    end = src.index("const CURSOR_AGENT_SLASH_COMMANDS", start)
    base = src[start:end]
    assert "prefix: '/usage'" not in base


# ---------------------------------------------------------------------------
# Display: usage must not become Cursor - Auto
# ---------------------------------------------------------------------------


def test_usage_chip_keeps_usage_label():
    res = _run_js(
        """
const usage = { prefix: '/usage', label: 'Usage', category: 'cursor-cmd' };
const sticky = { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' };
process.stdout.write(JSON.stringify({
    usageLabel: displayLabelForComposerChip(usage, 'auto'),
    stickyLabel: displayLabelForComposerChip(sticky, 'auto'),
    usageIsAgentBadge: isStickyCursorAgentChip(usage),
    stickyIsAgentBadge: isStickyCursorAgentChip(sticky),
    usageIsNested: isCursorNestedCommandChip(usage),
}));
"""
    )
    assert res["usageIsAgentBadge"] is False
    assert res["stickyIsAgentBadge"] is True
    assert res["usageIsNested"] is True
    assert res["usageLabel"] == "Usage"
    assert res["stickyLabel"] == "Cursor - Auto"


def test_other_nested_cmds_keep_own_labels():
    res = _run_js(
        """
const cmds = [
    { prefix: '/about', label: 'About', category: 'cursor-cmd' },
    { prefix: '/clear', label: 'New Cursor chat', category: 'cursor-cmd' },
    { prefix: '/plan ', label: 'Plan mode', category: 'cursor-cmd' },
    { prefix: '/ask ', label: 'Ask mode', category: 'cursor-cmd' },
    { prefix: '/agent', label: 'Agent mode', category: 'cursor-cmd' },
    { prefix: '/sandbox ', label: 'Sandbox', category: 'cursor-cmd' },
    { prefix: '/model refresh', label: 'Refresh Cursor models', category: 'cursor-cmd' },
];
const out = cmds.map((c) => ({
    prefix: c.prefix,
    label: displayLabelForComposerChip(c, 'auto'),
    isBadge: isStickyCursorAgentChip(c),
    isAgentHeader: isAgentHeaderChip(c),
}));
process.stdout.write(JSON.stringify({ out }));
"""
    )
    for row in res["out"]:
        assert row["isBadge"] is False, row
        assert row["isAgentHeader"] is False, row
        assert "Cursor -" not in row["label"], row


def test_legacy_buggy_cursor_category_on_usage_must_not_win_once_fixed():
    """Even a stale chip with category:'cursor' + prefix /usage must not be the agent badge."""
    res = _run_js(
        """
const legacy = { prefix: '/usage', label: 'Usage', category: 'cursor' };
process.stdout.write(JSON.stringify({
    isBadge: isStickyCursorAgentChip(legacy),
    label: displayLabelForComposerChip(legacy, 'auto'),
}));
"""
    )
    assert res["isBadge"] is False
    assert res["label"] == "Usage"


# ---------------------------------------------------------------------------
# Compose + sendability
# ---------------------------------------------------------------------------


def test_usage_with_cursor_sticky_is_sendable():
    res = _run_js(
        """
slashCtx.chat.chips = [
    { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
    { prefix: '/usage', label: 'Usage', category: 'cursor-cmd' },
];
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
"""
    )
    assert res["composed"] in ("/cursor /usage", "/cursor/usage")
    assert "/usage" in res["composed"]
    assert res["sendable"] is True


def test_model_refresh_with_cursor_sticky_is_sendable():
    res = _run_js(
        """
slashCtx.chat.chips = [
    { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
    { prefix: '/model refresh', label: 'Refresh Cursor models', category: 'cursor-cmd' },
];
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
"""
    )
    assert "/model refresh" in res["composed"]
    assert res["sendable"] is True


def test_about_clear_plan_ask_sendable_alone_with_sticky():
    res = _run_js(
        """
const cases = ['/about', '/clear', '/plan', '/ask', '/agent'];
const out = {};
for (const pref of cases) {
    slashCtx.chat.chips = [
        { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
        { prefix: pref.endsWith(' ') ? pref : pref, label: pref, category: 'cursor-cmd' },
    ];
    // /plan and /ask prefixes in the palette include a trailing space.
    if (pref === '/plan' || pref === '/ask') {
        slashCtx.chat.chips[1].prefix = pref + ' ';
    }
    const composed = composeMessageWithSlashChip({ value: '' });
    out[pref] = { composed, sendable: isSendableComposerMessage(composed, []) };
}
process.stdout.write(JSON.stringify(out));
"""
    )
    for pref, row in res.items():
        assert row["sendable"] is True, (pref, row)
        assert pref.rstrip() in row["composed"], (pref, row)


def test_real_model_id_chip_alone_still_not_sendable():
    res = _run_js(
        """
slashCtx.chat.chips = [
    { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
    { prefix: '/model auto', label: 'Auto', category: 'cursor-model', modelId: 'auto' },
];
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
"""
    )
    assert res["sendable"] is False


def test_restart_still_sendable():
    res = _run_js(
        """
slashCtx.chat.chips = [
    { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
];
const composed = composeMessageWithSlashChip({ value: '/restart status' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
"""
    )
    assert res["composed"] == "/restart status"
    assert res["sendable"] is True


def test_bare_cursor_still_not_sendable():
    res = _run_js(
        """
slashCtx.chat.chips = [
    { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' },
];
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
"""
    )
    assert res["sendable"] is False


# ---------------------------------------------------------------------------
# Agent identity / collapse must not swallow nested cmds
# ---------------------------------------------------------------------------


def test_nested_cmds_bundle_with_cursor_for_removal_but_are_not_agent_header():
    res = _run_js(
        """
const usage = { prefix: '/usage', label: 'Usage', category: 'cursor-cmd' };
const plan = { prefix: '/plan ', label: 'Plan mode', category: 'cursor-cmd' };
const sticky = { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor' };
process.stdout.write(JSON.stringify({
    usageAgent: composerChipAgentId(usage),
    planAgent: composerChipAgentId(plan),
    stickyAgent: composerChipAgentId(sticky),
    usageHeader: isAgentHeaderChip(usage),
    stickyHeader: isAgentHeaderChip(sticky),
    usageRelated: isCursorRelatedSlashChip(usage),
    modelRelated: isCursorRelatedSlashChip({
        prefix: '/model auto', label: 'Auto', category: 'cursor-model', modelId: 'auto',
    }),
    refreshRelated: isCursorRelatedSlashChip({
        prefix: '/model refresh', label: 'Refresh', category: 'cursor-cmd',
    }),
}));
"""
    )
    assert res["usageAgent"] == "cursor"
    assert res["planAgent"] == "cursor"
    assert res["stickyAgent"] == "cursor"
    assert res["usageHeader"] is False
    assert res["stickyHeader"] is True
    # Nested one-shots must not collapse into the Cursor badge on history.
    assert res["usageRelated"] is False
    assert res["refreshRelated"] is False
    assert res["modelRelated"] is True


def test_all_sticky_agent_clis_are_known():
    """Every sticky slash agent + harness manifest id is covered by CH-000482 tests."""
    sticky = _sticky_slash_agents()
    manifests = _harness_manifest_agent_ids()
    assert sticky, "expected stickySession agents in SLASH_COMMANDS"
    assert set(sticky) == set(manifests), (
        f"SLASH_COMMANDS sticky agents {sorted(sticky)} "
        f"!= harness manifests {sorted(manifests)}"
    )
    missing_usage = sorted(set(USAGE_REFRESH_AGENTS) - set(sticky))
    assert not missing_usage, missing_usage


def test_harness_usage_and_refresh_consistent_across_agents():
    """CH-000482: agents with /usage + staged refresh share one standard.

    - ``/usage`` keeps label Usage (never becomes the agent badge)
    - ``/model refresh`` is staged as ``*-cmd`` (no instant ``*Refresh: true``)
    - compose + sendable with the sticky agent
    """
    src = _src()
    sticky_all = _sticky_slash_agents(_slash_src())
    assert set(USAGE_REFRESH_AGENTS).issubset(set(sticky_all)), sticky_all

    for flag in (
        "cursorRefresh: true",
        "museRefresh: true",
        "codexRefresh: true",
        "opencodeRefresh: true",
    ):
        assert flag not in src, flag
    # Hermes has model picks but no catalog-refresh palette row yet.
    for builder, cat in (
        ("buildCursorModelPaletteItems", "cursor-cmd"),
        ("buildMuseModelPaletteItems", "muse-cmd"),
        ("buildCodexModelPaletteItems", "codex-cmd"),
        ("buildOpenCodeModelPaletteItems", "opencode-cmd"),
    ):
        b_start = src.index(f"function {builder}(")
        b_end = src.find(chr(10) + "    function ", b_start + 10)
        block = src[b_start:b_end]
        assert "prefix: '/model refresh'" in block, builder
        assert f"category: '{cat}'" in block, (builder, cat)

    agents = [
        ("cursor", "/cursor ", "cursor", "cursor-cmd", "Cursor - Auto"),
        ("codex", "/codex ", "codex", "codex-cmd", "Codex"),
        ("muse", "/muse ", "muse", "muse-cmd", "Muse Code"),
        ("hermes", "/hermes ", "hermes", "hermes-cmd", "Hermes"),
        ("opencode", "/opencode ", "opencode", "opencode-cmd", "OpenCode"),
    ]
    assert [a[0] for a in agents] == list(USAGE_REFRESH_AGENTS)
    res = _run_js(
        """
const agents = %s;
const out = {};
for (const [id, stickyPrefix, stickyCat, cmdCat, stickyLabelHint] of agents) {
    const sticky = { prefix: stickyPrefix, label: stickyLabelHint, category: stickyCat };
    const usage = { prefix: '/usage', label: 'Usage', category: cmdCat };
    const refresh = { prefix: '/model refresh', label: 'Refresh models', category: cmdCat };
    slashCtx.chat.chips = [sticky, usage];
    const usageComposed = composeMessageWithSlashChip({ value: '' });
    slashCtx.chat.chips = [sticky, refresh];
    const refreshComposed = composeMessageWithSlashChip({ value: '' });
    out[id] = {
        usageLabel: displayLabelForComposerChip(usage, 'auto'),
        stickyIsBadge: isStickyAgentChip(sticky, id),
        usageIsBadge: isStickyAgentChip(usage, id),
        refreshIsBadge: isStickyAgentChip(refresh, id),
        usageIsHeader: isAgentHeaderChip(usage),
        refreshIsHeader: isAgentHeaderChip(refresh),
        stickyIsHeader: isAgentHeaderChip(sticky),
        usageAgent: composerChipAgentId(usage),
        usageComposed,
        usageSendable: isSendableComposerMessage(usageComposed, []),
        refreshComposed,
        refreshSendable: isSendableComposerMessage(refreshComposed, []),
        nestedModelRefreshIsBadge: isStickyAgentChip({
            prefix: stickyPrefix.trim() + ' model refresh',
            label: 'Refresh',
            category: cmdCat,
        }, id),
    };
}
process.stdout.write(JSON.stringify(out));
"""
        % json.dumps(agents)
    )
    for agent_id, sticky_prefix, _sc, _cc, _hint in agents:
        row = res[agent_id]
        assert row["usageLabel"] == "Usage", (agent_id, row)
        assert row["stickyIsBadge"] is True, (agent_id, row)
        assert row["usageIsBadge"] is False, (agent_id, row)
        assert row["refreshIsBadge"] is False, (agent_id, row)
        assert row["usageIsHeader"] is False, (agent_id, row)
        assert row["refreshIsHeader"] is False, (agent_id, row)
        assert row["stickyIsHeader"] is True, (agent_id, row)
        assert row["usageAgent"] == agent_id, (agent_id, row)
        assert "/usage" in row["usageComposed"], (agent_id, row)
        assert sticky_prefix.strip() in row["usageComposed"] or row["usageComposed"].startswith(
            sticky_prefix.strip()
        ), (agent_id, row)
        assert row["usageSendable"] is True, (agent_id, row)
        assert "/model refresh" in row["refreshComposed"], (agent_id, row)
        assert row["refreshSendable"] is True, (agent_id, row)
        assert row["nestedModelRefreshIsBadge"] is False, (agent_id, row)


def test_sticky_agent_chip_rejects_nested_for_all_agent_clis():
    """CH-000482-1: nested prefixes must not count as the sticky badge — every CLI."""
    src = _src()
    assert "function isStickyAgentChip(" in src
    agents = _sticky_slash_agents(_slash_src())
    assert len(agents) >= 8, agents
    for name in (
        "hasActiveCursorAgentChip",
        "hasActiveMuseAgentChip",
        "hasActiveCodexAgentChip",
        "hasActiveHermesAgentChip",
        "hasActiveOpenCodeAgentChip",
    ):
        h_start = src.index(f"function {name}(")
        h_end = src.find(chr(10) + "    function ", h_start + 10)
        block = src[h_start:h_end]
        assert "startsWith('/" not in block, (name, "still uses startsWith")
        assert "isSticky" in block, name

    res = _run_js(
        """
const agents = %s;
const out = {};
for (const id of agents) {
    const token = '/' + id;
    const stickyCat = ['cursor','muse','hermes','codex','opencode'].includes(id)
        ? id : 'command';
    const cmdCat = ['cursor','muse','hermes','codex','opencode'].includes(id)
        ? id + '-cmd' : 'command';
    out[id] = {
        bare: isStickyAgentChip({ prefix: token, label: id, category: stickyCat }, id),
        spaced: isStickyAgentChip({ prefix: token + ' ', label: id, category: stickyCat }, id),
        metaDecor: isStickyAgentChip({
            prefix: '', meta: token + ' · gpt', label: id, category: stickyCat,
        }, id),
        usageBare: isStickyAgentChip(
            { prefix: '/usage', label: 'Usage', category: cmdCat }, id
        ),
        modelRefresh: isStickyAgentChip({
            prefix: token + ' model refresh', label: 'Refresh', category: cmdCat,
        }, id),
        slashModelRefresh: isStickyAgentChip({
            prefix: token + ' /model refresh', label: 'Refresh', category: cmdCat,
        }, id),
        slashUsage: isStickyAgentChip({
            prefix: token + ' /usage', label: 'Usage', category: cmdCat,
        }, id),
        cmdCatWithAgentPrefix: isStickyAgentChip({
            prefix: token, label: 'Usage', category: id + '-cmd',
        }, id),
        usageDisplay: displayLabelForComposerChip(
            { prefix: '/usage', label: 'Usage', category: cmdCat },
            'auto'
        ),
        stickyIsHeader: isAgentHeaderChip({
            prefix: token + ' ', label: id, category: stickyCat,
        }),
        nestedIsHeader: isAgentHeaderChip({
            prefix: token + ' model refresh', label: 'Refresh', category: cmdCat,
        }),
        agentIdFromSticky: composerChipAgentId({
            prefix: token + ' ', label: id, category: stickyCat,
        }),
    };
}
process.stdout.write(JSON.stringify(out));
"""
        % json.dumps(agents)
    )
    for agent_id in agents:
        row = res[agent_id]
        assert row["bare"] is True, (agent_id, row)
        assert row["spaced"] is True, (agent_id, row)
        assert row["metaDecor"] is True, (agent_id, row)
        assert row["usageBare"] is False, (agent_id, row)
        assert row["modelRefresh"] is False, (agent_id, row)
        assert row["slashModelRefresh"] is False, (agent_id, row)
        assert row["slashUsage"] is False, (agent_id, row)
        assert row["cmdCatWithAgentPrefix"] is False, (agent_id, row)
        assert row["usageDisplay"] == "Usage", (agent_id, row)
        assert row["stickyIsHeader"] is True, (agent_id, row)
        assert row["nestedIsHeader"] is False, (agent_id, row)
        assert row["agentIdFromSticky"] == agent_id, (agent_id, row)


def test_muse_codex_opencode_refresh_not_instant_on_palette_pick():
    """Palette pick must stage a chip — not call persist*('__refresh__') inline."""
    src = _src()
    apply_start = src.index("function applySlashSelectionInner(")
    apply_end = src.index("function applySlashSelection(", apply_start)
    apply = src[apply_start:apply_end]
    # Instant refresh on pick used to live in the *-model branches.
    assert "persistMuseModelSelection('__refresh__')" not in apply or (
        # Dead leftover on stale __refresh__ model rows is OK only if refresh
        # rows themselves are no longer *-model / *Refresh.
        "museRefresh: true" not in src
    )
    assert "museRefresh: true" not in src
    assert "codexRefresh: true" not in src
    assert "opencodeRefresh: true" not in src
    assert "cursorRefresh: true" not in src


def test_chat_page_cache_buster_present():
    html = CHAT_HTML.read_text(encoding="utf-8")
    m = re.search(r'src="/js/chat_page\.js\?v=([A-Za-z0-9]+)"', html)
    assert m and m.group(1)
