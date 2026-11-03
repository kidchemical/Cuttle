"""Tasks App client: pure CuttleTasksModel (node) + shell/App registration."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
MODEL = WEB / "js" / "tasks" / "tasks_model.js"

HARNESS = r"""
const M = require(process.env.MODEL);
const assert = require('assert');
const tree = () => [
  { id: 'a', text: 'A', done: false, children: [
    { id: 'a1', text: 'A1', done: false, children: [] },
    { id: 'a2', text: 'A2', done: false, children: [] } ] },
  { id: 'b', text: 'B', done: false, children: [] },
];

// Tree edits.
let t = tree();
assert.strictEqual(M.indent(t, 'b'), true);
assert.deepStrictEqual(t[0].children.map((n) => n.id), ['a1', 'a2', 'b']);
assert.strictEqual(M.outdent(t, 'b'), true);
assert.deepStrictEqual(t.map((n) => n.id), ['a', 'b']);
assert.strictEqual(M.indent(t, 'a'), false);           // first item cannot nest
assert.strictEqual(M.move(t, 'a2', -1), true);
assert.deepStrictEqual(t[0].children.map((n) => n.id), ['a2', 'a1']);
assert.strictEqual(M.move(t, 'a2', -1), false);
const id = M.addItem(t, { afterId: 'a1', rand: () => 'x1' });
assert.strictEqual(id, 'u-x1');
assert.deepStrictEqual(t[0].children.map((n) => n.id), ['a2', 'a1', 'u-x1']);
assert.strictEqual(M.addItem(t, { parentId: 'b', rand: () => 'x2' }), 'u-x2');
assert.strictEqual(M.removeItem(t, 'u-x2'), 'b');      // focus falls back to the parent
assert.strictEqual(M.count(t).total, 5);

// Checking a parent checks its sub-tasks; unchecking a child reopens ancestors.
t = tree();
M.setDone(t, 'a', true);
assert.deepStrictEqual([t[0].done, t[0].children[0].done, t[0].children[1].done], [true, true, true]);
M.setDone(t, 'a2', false);
assert.deepStrictEqual([t[0].done, t[0].children[0].done, t[0].children[1].done], [false, true, false]);
assert.deepStrictEqual(M.count(t), { done: 1, total: 4, pct: 25 });

// Blank rows are dropped on save; comparison ignores them.
const draft = tree();
M.addItem(draft, { rand: () => 'blank' });
assert.strictEqual(M.prune(draft).length, 2);
assert.strictEqual(M.sameItems(draft, tree()), true);
M.setText(draft, 'b', 'B!');
assert.strictEqual(M.sameItems(draft, tree()), false);

// Lists: filtering, reach labels, counts.
const lists = [
  { id: 'p', title: 'Plan', status: 'active', scope: 'session', home_chat: { id: 1, name: 'Orch', handle: 'CH-000001' },
    linked_chats: [{ id: 2, name: 'Super', handle: 'CH-000002' }], payload: { items: [{ id: 'q', text: 'Shaders' }] } },
  { id: 'old', title: 'Old', status: 'archived', scope: 'project', project_path: '/x/game', linked_chats: [], payload: { items: [] } },
];
assert.deepStrictEqual(M.filterLists(lists, { status: 'active' }).map((l) => l.id), ['p']);
assert.deepStrictEqual(M.filterLists(lists, { status: 'all', query: 'shader' }).map((l) => l.id), ['p']);
assert.deepStrictEqual(M.filterLists(lists, { status: 'all', query: 'super' }).map((l) => l.id), ['p']);
assert.deepStrictEqual(M.statusCounts(lists), { active: 1, archived: 1, all: 2 });
assert.strictEqual(M.reachLabel(lists[0]), 'Orch +1 linked');
assert.strictEqual(M.reachLabel(lists[1]), 'Project · game');
assert.deepStrictEqual(M.reachChats(lists[0]).map((c) => c.role), ['home', 'linked']);

// History: writes only by default, readable summaries.
const ev = M.historyEntries([
  { sequence: 3, operation: 'get', actor: { source: 'cli', agent_id: 'codex' } },
  { sequence: 2, operation: 'patch', actor: { source: 'cli', agent_id: 'codex', model: 'gpt' },
    before_state: { title: 'T', status: 'active', payload: { items: [{ id: 'a', done: false }] }, linked_sessions: [] },
    after_state: { title: 'T', status: 'archived', payload: { items: [{ id: 'a', done: true }] }, linked_sessions: [5] } },
  { sequence: 1, operation: 'create', actor: { source: 'ui' }, after_state: { payload: { items: [{ id: 'a' }] } } },
]);
assert.deepStrictEqual(ev.map((e) => e.sequence), [2, 1]);
assert.strictEqual(ev[0].actor, 'codex · gpt');
assert.strictEqual(ev[0].detail, 'Archived · Linked 1 chat · Checked 1');
assert.strictEqual(ev[1].actor, 'You');
assert.strictEqual(ev[1].detail, '1 task');
assert.strictEqual(M.historyEntries([{ operation: 'get', actor: {} }], { includeReads: true }).length, 1);

// SQLite UTC timestamps.
const now = Date.parse('2026-11-03T12:00:00Z');
assert.strictEqual(M.relativeTime('2026-11-03 11:58:00', now), '2m ago');
assert.strictEqual(M.relativeTime('', now), '');
assert.strictEqual(M.esc('<a href="x">'), '&lt;a href=&quot;x&quot;&gt;');
console.log(JSON.stringify({ ok: true }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_tasks_model_tree_lists_and_history():
    proc = subprocess.run(["node", "-e", HARNESS], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}


def test_tasks_app_is_registered_stashed_in_apps():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    shell = (WEB / "js" / "shell" / "app_shell.js").read_text(encoding="utf-8")
    assert 'id="nav-tasks"' in html and 'data-page="/tasks_page.html"' in html
    assert "'nav-tasks'" in rail.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "'nav-tasks'" in rail.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
    # Established behavior: no experimental gate hides the App.
    assert "'nav-tasks'" not in rail.split("const EXPERIMENTAL_RAIL_APPS =")[1].split("};")[0]
    assert "'/tasks_page.html': 'Tasks'" in shell
    page = (WEB / "tasks_page.html").read_text(encoding="utf-8")
    assert page.index("page_controls.css") < page.index("orchestrator_page.css") < page.index("tasks_page.css")
    assert page.index("tasks_model.js") < page.index("tasks_page.js")
    assert 'data-page="tasks"' in page
    skin = (WEB / "css" / "orchestrator_page.css").read_text(encoding="utf-8")
    assert 'body[data-page="tasks"]' in skin.split("{")[0]


MIGRATE = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.RAIL, 'utf8');
const body = src.split('function migrateUILayout(saved) {')[1].split('\n        function ')[0];
const pre = src.split('const CANONICAL_RAIL_ITEM_ORDER')[1].split('let enabledFlags')[0];
const fn = new Function('return (function(){ const CANONICAL_RAIL_ITEM_ORDER' + pre +
  '; return function migrateUILayout(saved) {' + body + '; })()')();
const v13 = fn({ layout_version: 13, rail_items: ['nav-chat', 'nav-orchestrator', 'nav-apps'], rail_hidden: ['nav-agents'] });
const v12 = fn({ layout_version: 12, rail_items: ['nav-chat', 'nav-orchestrator', 'nav-apps'], rail_hidden: [] });
console.log(JSON.stringify({ v13: v13.layout, v12: v12.layout }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_v14_migration_stashes_tasks_and_keeps_v13_pins():
    proc = subprocess.run(["node", "-e", MIGRATE],
                          env={**os.environ, "RAIL": str(WEB / "js" / "shell" / "shell_rail.js")},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["v13"]["layout_version"] == 14
    assert "nav-tasks" in out["v13"]["rail_hidden"]
    assert out["v13"]["rail_items"] == ["nav-chat", "nav-orchestrator", "nav-apps"]  # v13 pin survives
    assert "nav-orchestrator" in out["v12"]["rail_hidden"]  # v13 step still applies to v12
