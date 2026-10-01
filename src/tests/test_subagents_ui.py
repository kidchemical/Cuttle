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

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
ACTIVITY_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_activity.js"
CHAT_CSS = Path(__file__).resolve().parents[1] / "web" / "css" / "chat_page.css"
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


def test_subagent_markup_present():
    js = CHAT_JS.read_text(encoding="utf-8")
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert "typing-subagent-orbs" in js
    assert "syncSubagentOrbs" in js
    assert "paintSubagentOrbs" in js
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
    assert ".typing-orbit--subagent" in css
    assert ".typing-orbit-core--glyph" in css
    assert "parentSpeaker ? null" in js
    assert "stickyChipsFromAssistantSlash" in js
    assert "storedLooksSpecific" in js
