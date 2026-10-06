"""Node checks for sub-agent launcher HTML + history nesting helpers."""

from __future__ import annotations

import json
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
CHAT_CSS = Path(__file__).resolve().parents[2] / "web" / "css" / "chat_page.css"
_HELPERS = (
    "toAuthDbSessionId",
    "sessionIdsEqual",
    "formatChatDisplayId",
    "escapeHtmlInline",
    "parseChatHandleToken",
    "normalizeSubagentList",
    "renderSubagentLaunchersHtml",
    "parentRefsFromOpts",
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
    src = CHAT_JS.read_text(encoding="utf-8")
    blob = "\n".join(_extract_function(src, name) for name in _HELPERS)
    # Phase 3 Slice 4: the id helpers are thin adapters over chat_activity.js;
    # requiring the module resolves the CuttleChatActivity global they call.
    driver = "require(%s);\n" % json.dumps(str(ACTIVITY_JS)) + (_DRIVER % blob)
    proc = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


_ORB_HELPERS = (
    "escapeHtmlInline",
    "formatChatDisplayId",
    "parseChatHandleToken",
    "normalizeSubagentList",
    "slashCommandChipCategoryClass",
    "fleetAgentChipHtml",
    "renderSubagentLaunchersHtml",
    "mountSubagentLaunchers",
    "profileAvatarInnerHtml",
    "rememberSubagentOrbList",
    "paintSubagentOrbs",
    "syncSubagentOrbs",
    "clearSubagentOrbs",
    "mountLiveSubagentBadges",
)

_ORB_DRIVER = r"""
let _subagentOrbList = [];
%s
const assert = require('assert');

let inserted = [];
let removed = 0;
function makeLaunchersStub() {
  return { outerHTML: '', remove() { removed += 1; } };
}
const fakeWrap = {
  _launchers: null,
  querySelector(sel) {
    if (sel === '.subagent-launchers') return fakeWrap._launchers;
    if (sel === '.message-footer') {
      return {
        insertAdjacentHTML(pos, html) {
          inserted.push([pos, html]);
          fakeWrap._launchers = makeLaunchersStub();
          fakeWrap._launchers.outerHTML = html;
        },
      };
    }
    return null;
  },
  insertAdjacentHTML(pos, html) {
    inserted.push([pos, html]);
    fakeWrap._launchers = makeLaunchersStub();
    fakeWrap._launchers.outerHTML = html;
  },
};
const typingEl = { querySelector(sel) {
  if (sel === '.message-content-wrapper') return fakeWrap;
  return fakeWrap.querySelector(sel);
} };
global.document = {
  querySelectorAll(sel) {
    if (sel.indexOf('typing-indicator') >= 0) return [typingEl];
    return [];
  },
};

// Orbs live inside the fleet cards now: no standalone orb row, the live
// badges mount cards (with mini orbit) onto the in-progress bubble.
syncSubagentOrbs([
  { handle: 'CH-000540', label: 'Chef A', agent: 'cursor', generating: true,
    fleet: true, outcome: 'running', summary: 'Reading files' },
]);
assert.strictEqual(inserted.length, 1);
assert.strictEqual(inserted[0][0], 'beforebegin');
assert.ok(inserted[0][1].includes('subagent-launchers'), inserted[0][1]);
assert.ok(inserted[0][1].includes('typing-orbit--fleet'), inserted[0][1]);
assert.ok(inserted[0][1].includes('data-chat-handle="CH-000540"'), inserted[0][1]);
assert.ok(inserted[0][1].includes('Chef A'), inserted[0][1]);

// Second attach updates the badges in place instead of duplicating.
syncSubagentOrbs([
  { handle: 'CH-000540', label: 'Chef A', agent: 'cursor', generating: true,
    fleet: true, outcome: 'running', summary: 'Reading files' },
  { session_id: 541, title: 'Chef B', agent: 'codex', fleet: true, outcome: 'queued' },
]);
assert.strictEqual(inserted.length, 1);
assert.ok(fakeWrap._launchers.outerHTML.includes('CH-000541'), fakeWrap._launchers.outerHTML);

// Clearing (new turn) unmounts the live badges.
clearSubagentOrbs();
assert.strictEqual(removed, 1);

console.log('ok');
"""


@node_only
def test_subagent_live_orbs_and_badges():
    src = CHAT_JS.read_text(encoding="utf-8")
    blob = "\n".join(_extract_function(src, name) for name in _ORB_HELPERS)
    # Same as test_subagent_ui_helpers: formatChatDisplayId delegates to the
    # CuttleChatActivity global, so the activity module must be required first.
    # Fleet entries render through the fleet module, which self-registers on
    # globalThis when required.
    driver = (
        "require(%s);\n" % json.dumps(str(ACTIVITY_JS))
        + "require(%s);\n" % json.dumps(str(FLEET_JS))
        + (_ORB_DRIVER % blob)
    )
    proc = subprocess.run(
        ["node", "-e", driver],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


_FLEET_CHIP_HELPERS = (
    "escapeHtmlInline",
    "slashCommandChipCategoryClass",
    "fleetAgentChipHtml",
)

_FLEET_CHIP_DRIVER = r"""
%s
const assert = require('assert');
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
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_subagent_markup_present():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    fleet_js = FLEET_JS.read_text(encoding="utf-8")
    assert "typing-subagent-orbs" not in js
    assert "typing-subagent-orb" not in js
    assert "typingOrbitMiniHtml" not in js
    assert "typing-orbit--fleet" in fleet_js
    assert "syncSubagentOrbs" in js
    assert "paintSubagentOrbs" in js
    assert "clearSubagentOrbs" in js
    assert "mountLiveSubagentBadges" in js
    assert "typing-subagent-label" not in js
    assert "is-parent-speaker" in js
    assert "parentRefsFromOpts" in js
    assert "applySessionIdentity" in js
    assert "mountSubagentLaunchers" in js
    assert "toggleHistorySubagentGroup" in js
    assert "history-subagent-group" in js
    assert "history-subagent-toggle" in js
    assert "Show sub-agents" in js
    assert ".subagent-launchers" in css
    assert ".history-subagent-group.is-collapsed" in css
    assert ".history-subagent-toggle" in css
    assert ".typing-orbit--fleet" in css
    assert ".typing-subagent-orb" not in css
    assert ".typing-orbit--subagent" not in css
    assert "typing-subagent-label" not in css
    assert ".typing-orbit-core--glyph" in css
    assert "parentSpeaker ? null" in js
    assert "stickyChipsFromAssistantSlash" in js
    assert "storedLooksSpecific" in js
