"""Sub-agent launchers (chat_subagent_launchers.js) + history nesting helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CHAT_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
ACTIVITY_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_activity.js"
FLEET_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_subagent_fleet.js"
LAUNCHERS_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_subagent_launchers.js"
MEDIA_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_media.js"
VIEW_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_history_view.js"
PANEL_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_history_panel.js"
CHAT_CSS = Path(__file__).resolve().parents[2] / "web" / "css" / "chat_page.css"
# Page-owned id/handle adapters (still sliced from chat_page.js).
_PAGE_HELPERS = (
    "toAuthDbSessionId",
    "sessionIdsEqual",
    "formatChatDisplayId",
    "escapeHtmlInline",
    "parseChatHandleToken",
)
# History nesting algorithms now live in chat_history_view.js (Slice 3A commit 3).
_VIEW_HELPERS = (
    "historyEntryParentId",
    "historyLookupNestedEntry",
    "nestHistoryEntries",
    "historySessionAncestorIds",
    "isHistorySubagentGroupExpanded",
    "visibleHistoryRoots",
    "historySubagentNoun",
    "historySubagentToggleTitle",
)


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"    function {name}(")
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


_DRIVER = r"""
%s
const assert = require('assert');
// Launchers come from their owner; the page supplies handle parsing + escaping.
const launchers = require(process.env.LAUNCHERS_JS).create({
  win: { document: {} }, escapeHtmlInline, parseChatHandleToken,
  avatarHtml: () => '', chipCategoryClass: () => '' });
const normalizeSubagentList = launchers.normalizeList;
const renderSubagentLaunchersHtml = launchers.renderHtml;
const parentRefsFromOpts = launchers.parentRefs;

const list = normalizeSubagentList([
  { handle: 'CH-000540', label: 'Chef A', agent: 'cursor', generating: true },
  { session_id: 541, title: 'Chef B', agent: 'codex' },
]);
assert.strictEqual(list.length, 2);
assert.strictEqual(list[0].handle, 'CH-000540');
assert.strictEqual(list[1].handle, 'CH-000541');

const html = renderSubagentLaunchersHtml(list);
assert.ok(html.includes('class="subagent-launchers"'), html);
assert.ok(html.includes('data-chat-handle="CH-000540"'), html);
assert.ok(html.includes('Chef A'), html);
assert.ok(html.includes('codex'), html);

const parentHtml = renderSubagentLaunchersHtml(parentRefsFromOpts({
  parent_handle: 'CH-000010',
  parent_session_id: 10,
  parent_label: 'Main chat',
}), { kind: 'parent' });
assert.ok(parentHtml.includes('is-parent'), parentHtml);
assert.ok(parentHtml.includes('Main chat'), parentHtml);
assert.ok(parentHtml.includes('data-chat-handle="CH-000010"'), parentHtml);

const nested = nestHistoryEntries([
  { sessionId: 10, serverSession: { id: 10 } },
  { sessionId: 11, serverSession: { id: 11, parent_session_id: 10 } },
  { sessionId: 12, serverSession: { id: 12, parent_session_id: 10 } },
  { sessionId: 99, serverSession: { id: 99, parent_session_id: 404 } },
]);
assert.strictEqual(nested.roots.length, 2);
assert.strictEqual(nested.childrenByParent.get('10').length, 2);
assert.ok(nested.roots.some((e) => String(e.sessionId) === '99'));
assert.ok(nested.byId.has('10'));

const deep = nestHistoryEntries([
  { sessionId: 10, serverSession: { id: 10 } },
  { sessionId: 11, serverSession: { id: 11, parent_session_id: 10 } },
  { sessionId: 13, serverSession: { id: 13, parent_session_id: 11 } },
]);
assert.strictEqual(deep.roots.length, 1);
assert.strictEqual(deep.childrenByParent.get('11').length, 1);
assert.deepStrictEqual(historySessionAncestorIds(13, deep), ['11', '10']);
assert.strictEqual(isHistorySubagentGroupExpanded('10', deep, { currentSessionId: 13 }), true);
assert.strictEqual(isHistorySubagentGroupExpanded('11', deep, { currentSessionId: 13 }), true);
assert.strictEqual(isHistorySubagentGroupExpanded('10', deep, { currentSessionId: 10 }), false);
assert.strictEqual(isHistorySubagentGroupExpanded('10', deep, {}), false);
assert.strictEqual(isHistorySubagentGroupExpanded('10', deep, { prefs: { '10': 'expanded' } }), true);
assert.strictEqual(isHistorySubagentGroupExpanded('10', deep, { searchActive: true }), true);

const many = nestHistoryEntries(
  Array.from({ length: 8 }, (_, i) => ({
    sessionId: i + 1,
    serverSession: { id: i + 1 },
  })).concat([{ sessionId: 20, serverSession: { id: 20, parent_session_id: 8 } }])
);
assert.strictEqual(many.roots.length, 8);
const pinned = visibleHistoryRoots(many, 5, { currentSessionId: 20 });
assert.ok(pinned.some((e) => String(e.sessionId) === '8'), JSON.stringify(pinned.map((e) => e.sessionId)));
assert.strictEqual(historySubagentToggleTitle(3, false, false), 'Show 3 sub-agents');
assert.strictEqual(historySubagentToggleTitle(1, true, false), 'Hide 1 sub-agent');

console.log('ok');
"""


@node_only
def test_subagent_ui_helpers():
    page_src = CHAT_JS.read_text(encoding="utf-8")
    view_src = VIEW_JS.read_text(encoding="utf-8")
    blob = "\n".join(_extract_function(page_src, name) for name in _PAGE_HELPERS)
    blob += "\n" + "\n".join(_extract_function(view_src, name) for name in _VIEW_HELPERS)
    # Phase 3 Slice 4: the id helpers are thin adapters over chat_activity.js;
    # requiring the module resolves the CuttleChatActivity global they call.
    driver = "require(%s);\n" % json.dumps(str(ACTIVITY_JS)) + (_DRIVER % blob)
    proc = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LAUNCHERS_JS": str(LAUNCHERS_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# Page-owned deps the launcher controller receives; the controller is the owner.
_ORB_HELPERS = (
    "escapeHtmlInline",
    "formatChatDisplayId",
    "parseChatHandleToken",
    "slashCommandChipCategoryClass",
    "profileAvatarInnerHtml",
)

def test_subagent_live_orbs_and_badges():
    """Exercise mounted cards with the browser's real template/DOM APIs."""
    playwright = pytest.importorskip("playwright.sync_api")
    from tests.browser_guard import launch_chromium

    src = CHAT_JS.read_text(encoding="utf-8")
    blob = "\n".join(_extract_function(src, name) for name in _ORB_HELPERS)
    first = {"handle": "CH-000540", "label": "Chef A", "agent": "cursor",
             "generating": True, "fleet": True, "outcome": "running",
             "summary": "Reading files"}
    second = {"session_id": 541, "title": "Chef B", "agent": "codex",
              "fleet": True, "outcome": "queued"}
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page()
        try:
            page.set_content('<div id="typing-indicator"><div class="message-content-wrapper">'
                             '<div class="message-footer"></div></div></div>')
            page.add_script_tag(content=ACTIVITY_JS.read_text(encoding="utf-8"))
            page.add_script_tag(content=FLEET_JS.read_text(encoding="utf-8"))
            page.add_script_tag(content=MEDIA_JS.read_text(encoding="utf-8"))
            page.add_script_tag(content=LAUNCHERS_JS.read_text(encoding="utf-8"))
            page.add_script_tag(content=blob + "\nwindow.launchers = CuttleChatSubagentLaunchers.create({"
                                "win: window, escapeHtmlInline, parseChatHandleToken,"
                                " avatarHtml: profileAvatarInnerHtml,"
                                " chipCategoryClass: slashCommandChipCategoryClass});")
            page.evaluate("rows => launchers.syncLive(rows)", [first])
            group = page.locator(".subagent-launchers")
            assert group.count() == 1
            assert page.locator(".subagent-launchers + .message-footer").count() == 1
            assert group.locator(".typing-orbit--fleet").count() == 1
            assert group.locator(".subagent-fleet-summary--live").inner_text() == "Reading files"
            assert group.locator('[data-chat-handle="CH-000540"]').count() == 1
            assert "Chef A" in group.inner_text()
            page.evaluate("window.initialGroup = document.querySelector('.subagent-launchers');"
                          "window.initialCard = initialGroup.firstElementChild;")
            page.evaluate("rows => launchers.syncLive(rows)", [first, second])
            assert group.count() == 1
            assert group.locator('[data-chat-handle="CH-000541"]').count() == 1
            assert group.evaluate("el => el === window.initialGroup")
            assert group.locator('[data-chat-handle="CH-000540"]').evaluate(
                "el => el === window.initialCard")
            page.evaluate("launchers.clearLive()")
            assert group.count() == 0
        finally:
            browser.close()


_FLEET_CHIP_HELPERS = (
    "escapeHtmlInline",
    "slashCommandChipCategoryClass",
)

_FLEET_CHIP_DRIVER = r"""
%s
const assert = require('assert');
const fleetAgentChipHtml = require(process.env.LAUNCHERS_JS).create({
  win: { document: {} }, escapeHtmlInline, parseChatHandleToken: () => null,
  avatarHtml: () => '', chipCategoryClass: slashCommandChipCategoryClass }).agentChipHtml;
globalThis.CuttleChatSlash = { SLASH_COMMANDS: [
  { prefix: '/cursor', label: 'Cursor Agent' },
  { prefix: '/muse', label: 'Muse Code' },
] };
const html = fleetAgentChipHtml({ agent: 'cursor', model: 'gpt-5', effort: 'high' });
assert.ok(html.includes('slash-command-chip'), html);
assert.ok(html.includes('slash-command-chip--header'), html);
assert.ok(html.includes('slash-command-chip--palette-cursor'), html);
assert.ok(html.includes('<span class="slash-chip-label">Cursor Agent</span>'), html);
assert.ok(html.includes('Cursor Agent | gpt-5 | high'), html);
assert.ok(!html.includes('>gpt-5<'), html);
const bare = fleetAgentChipHtml({ agent: 'codex', model: '', effort: '' });
assert.ok(bare.includes('data-tip="codex"'), bare);
assert.strictEqual(fleetAgentChipHtml({ agent: '' }), '');
console.log('ok');
"""


@node_only
def test_fleet_agent_chip_uses_shared_slash_chip():
    src = CHAT_JS.read_text(encoding="utf-8")
    blob = "\n".join(_extract_function(src, name) for name in _FLEET_CHIP_HELPERS)
    proc = subprocess.run(
        ["node", "-e", (_FLEET_CHIP_DRIVER % blob)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LAUNCHERS_JS": str(LAUNCHERS_JS)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_subagent_fleet_cards_clip_to_card():
    """Long names/statuses must truncate inside the card, never spill past it."""
    import re

    css = CHAT_CSS.read_text(encoding="utf-8")
    card = re.search(r"\.subagent-fleet-card \{([^}]*)\}", css)
    assert card and "overflow:hidden" in card.group(1).replace(" ", "")
    head = re.search(r"\.subagent-fleet-head \{([^}]*)\}", css)
    assert head and "overflow:hidden" in head.group(1).replace(" ", "")
    name = re.search(r"\.subagent-fleet-name \{([^}]*)\}", css)
    assert name and "text-overflow:ellipsis" in name.group(1).replace(" ", "")


def test_subagent_markup_present():
    js = CHAT_JS.read_text(encoding="utf-8")
    controls_js = (CHAT_JS.parent / "chat_agent_controls.js").read_text(encoding="utf-8")
    view_js = VIEW_JS.read_text(encoding="utf-8")
    panel_js = PANEL_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    fleet_js = FLEET_JS.read_text(encoding="utf-8")
    launchers_js = LAUNCHERS_JS.read_text(encoding="utf-8")
    for src in (js, launchers_js):
        assert "typing-subagent-orbs" not in src
        assert "typing-subagent-orb" not in src
        assert "typingOrbitMiniHtml" not in src
        assert "typing-subagent-label" not in src
    assert "typing-orbit--fleet" in fleet_js
    # Launchers + live badges are owned by the controller; the page delegates.
    assert "subagentLaunchers.syncLive(" in js
    assert "subagentLaunchers.paintLive()" in js
    assert "subagentLaunchers.clearLive()" in js
    assert "subagentLaunchers.mountForMessage(" in js
    assert "function mountLiveSubagentBadges(" in launchers_js
    assert "function parentRefsFromOpts(" in launchers_js
    assert "function mountSubagentLaunchers(" in launchers_js
    assert "is-parent-speaker" in js
    assert "applySessionIdentity" in js
    assert "toggleHistorySubagentGroup" in js
    # Nested-tree markup/expansion moved to the view + panel owners (3A commit 3);
    # the page keeps the window forwarder and the menu "Show sub-agents" item.
    assert "history-subagent-group" in view_js
    assert "history-subagent-group" in panel_js
    assert "history-subagent-toggle" in view_js
    assert "Show sub-agents" in js
    assert ".subagent-launchers" in css
    assert ".history-subagent-group.is-collapsed" in css
    assert ".history-subagent-toggle" in css
    assert ".typing-orbit--fleet" in css
    assert "typing-sat-orbit-fleet" in css
    assert ".subagent-fleet-summary--live" in css
    assert ".typing-subagent-orb" not in css
    assert ".typing-orbit--subagent" not in css
    assert "typing-subagent-label" not in css
    assert ".typing-orbit-core--glyph" in css
    assert "parentSpeaker ? null" in js
    assert "stickyChipsFromAssistantSlash" in js
    # Cursor badge enrichment moved to the CuttleChatAgentControls owner (2A).
    assert "storedLooksSpecific" in controls_js
