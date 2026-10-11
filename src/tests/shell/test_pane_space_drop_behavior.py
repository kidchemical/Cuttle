"""Behavioral characterization of the cross-space pane-drop controller.

5C moved the grip→space-tab coupling out of app_shell.js into
shell_spaces.js (`CuttleShellSpaces.create(...).paneDrop`). This pins the
decision + effect under node: a quick release over another space's tab appends
the dragged leaf to that space's stored layout, and a drag may only start when
another pane or another space exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
OWNER_JS = REPO_ROOT / "src" / "web" / "js" / "shell" / "shell_spaces.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const assert = require('node:assert/strict');
global.window = {};
const window = global.window;

window.CuttleSpaces = {
  DEFAULT_COLOR: '#e8eaed',
  sanitizeColor: (c) => c || null,
  loadSpacesState: () => window.__state,
  saveSpacesState: (storage, state) => { window.__saved = JSON.parse(JSON.stringify(state)); },
  createDefaultState: () => ({ active: 'A', spaces: [{ id: 'A', name: 'A' }], groups: [] }),
  getSpaceById: (state, id) => (state.spaces || []).find((s) => s && s.id === id) || null,
  getGroupById: () => null,
  ensureGroupsArray: () => [],
  pruneEmptyGroups: () => false,
};
window.CuttleSessionPrefs = { create: () => ({ map: () => ({}), update() {}, invalidate() {} }), isKey: () => false };
window.CuttleActivityBroker = { create: () => ({ start() {}, dispose() {}, sessions: async () => null, refresh() {} }) };

__SOURCE__

function makeTabs() {
  return [
    { dataset: { spaceId: 'A' }, getBoundingClientRect: () => ({ left: 0, right: 50, top: 0, bottom: 20 }), classList: { add() {}, remove() {} } },
    { dataset: { spaceId: 'B' }, getBoundingClientRect: () => ({ left: 60, right: 110, top: 0, bottom: 20 }), classList: { add() {}, remove() {} } },
  ];
}
const doc = { querySelectorAll: (sel) => (sel === '.shell-space-tab' ? makeTabs() : []), getElementById: () => null, body: { classList: { add() {}, remove() {} } } };
const win = { setTimeout: (fn) => { fn(); return 1; }, clearTimeout() {}, location: { origin: 'https://cuttle.test' }, showToast() {}, CSS: { escape: (s) => s } };

function makeHost() {
  return {
    win, doc, storage: { getItem: () => null, setItem() {} },
    escapeHtml: (s) => String(s),
    getOpenPaneSessions: () => [], flattenLayoutLeaves: () => [], chatSurfaceKind: () => 'chat',
    broadcastToFrames() {}, broadcastUsageLiveWake() {}, updateUnreadBadge() {},
    getAuthUserId: () => 1, isRestoring: () => false, snapshotLayout: () => null,
    applySpaceRoot() {}, defaultSpaceRoot: () => null,
    persistLastChatHandles() {}, clearLastChatColumn() {},
    getSplitContainer: () => null, getSplitColumns: () => [], closeSplitColumn() {},
    getColumnEl: () => null, getState: () => ({}), setState() {}, updateColumnUI() {},
    replaceFrame: () => null, attachFrameLoadListener() {}, pageWithPaneSession: (i, p) => p,
    canonicalizeShellPage: (p) => p, persistSplitLayout() {}, lastChatByColumn: new Map(),
    newLeafId: () => 'leaf-x', newGroupId: () => 'grp-x', normalizeSplitOrientation: (o) => o,
    setFocusedColumnDirect() {}, broadcastPaneFocus() {},
  };
}

// Two spaces + a single pane -> a cross-space drag may start.
window.__state = { active: 'A', spaces: [{ id: 'A', name: 'A', root: null }, { id: 'B', name: 'B', root: null }], groups: [] };
const api = window.CuttleShellSpaces.create(makeHost());
const pd = api.paneDrop;
assert.equal(pd.hoverMs, 1000);
assert.equal(pd.canStartDrag(), true);
pd.beginDrag({ type: 'leaf', id: 'L1', page: '/chat_page.html' }, null, () => {});
assert.equal(pd.isActive(), true);
// Release over tab B: consumed by the Spaces owner.
assert.equal(pd.finishDrop({ clientX: 80, clientY: 10 }, null), true);
assert.equal(pd.isActive(), false);
const b = window.__state.spaces.find((s) => s.id === 'B');
assert.ok(b.root, 'target space gained a layout root');
assert.ok(b.root.children.some((n) => n.id === 'L1'), 'dragged leaf appended to the target space');
assert.ok(window.__saved, 'spaces state persisted');
// No payload -> not consumed (shell does the within-space reorder).
assert.equal(pd.finishDrop({ clientX: 80, clientY: 10 }, null), false);

// One space + a single pane -> nothing to drag to.
window.__state = { active: 'A', spaces: [{ id: 'A', name: 'A', root: null }], groups: [] };
const api2 = window.CuttleShellSpaces.create(makeHost());
assert.equal(api2.paneDrop.canStartDrag(), false);

process.stdout.write(JSON.stringify({ ok: true }));
"""


def _run():
    script = HARNESS.replace("__SOURCE__", OWNER_JS.read_text(encoding="utf-8"))
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_quick_drop_appends_leaf_to_target_space():
    assert _run()["ok"] is True
