"""Studio work panel (L4), pure side under node: binding, rows, owners, escaping, cards, Classic rows."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
WORK_PANEL = WEB / "js" / "chat" / "chat_work_panel.js"
TASKS = WEB / "js" / "gizmos" / "tasks_gizmo.js"
FLEET = WEB / "js" / "chat" / "chat_subagent_fleet.js"
IDENTITY = WEB / "js" / "shared" / "agent_identity.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")

DRIVER = r"""
const assert = require('assert');
const P = require(process.env.WORK_PANEL);
const T = require(process.env.TASKS);
const F = require(process.env.FLEET);

P.setCatalog([
  { id: 'codex', label: 'Codex', color: '#10a37f' },
  { id: 'claude', label: 'Claude Code', color: '#d97757' },
]);

const turn = 'turn-40';
const list = (id, over) => Object.assign({
  id, type: 'tasks', status: 'active', title: 'Release ' + id,
  origin_turn_id: turn, payload: { items: [] },
}, over || {});

const items = [
  { id: 't1', text: 'Plan', done: true },
  { id: 't2', text: 'Write <b>parser</b>', done: false, in_progress: true,
    owner: { kind: 'chat', id: 'CH-000041', label: 'Scout', agent: 'codex' } },
  { id: 't3', text: 'Ship', done: false,
    owner: { kind: 'user', label: 'You' } },
  { id: 't4', text: 'Open', done: false },
];

// --- binding -----------------------------------------------------------------
const cards = [{ handle: 'CH-000041', label: 'Scout', agent: 'codex', outcome: 'running', summary: 'Parsing' }];
const bound = list('a', { payload: { items } });
const other = list('b', { origin_turn_id: 'turn-9', payload: { items: [] } });
const linked = list('c', { origin_turn_id: 'turn-9', payload: { items: [
  { id: 'x', text: 'Child', owner: { kind: 'chat', id: 'ch-000041', label: 'Scout', agent: 'codex' } }] } });
const archived = list('d', { status: 'archived' });
const m = P.model([bound, other, linked, archived], cards, turn);
assert.deepStrictEqual(m.tasks.map((t) => t.id), ['a', 'c'], 'turn-origin and batch-owned lists only');
assert.strictEqual(P.listBound(other, turn, new Set()), false, 'other turn, no link');
assert.strictEqual(P.listBound(list('e', { origin_turn_id: '' }), turn, new Set()), false, 'no origin');
assert.strictEqual(P.model([other], cards, '').tasks.length, 0, 'no turn id: origin never matches');
assert.strictEqual(P.model([bound], cards, '').tasks.length, 1, 'no turn id: the batch link still binds');
assert.strictEqual(P.model([bound], [], '').hasTasks, false);

// --- rows, state, owners -------------------------------------------------------
const rows = m.tasks[0].rows;
assert.deepStrictEqual(rows.map((r) => r.state), ['done', 'in_progress', 'open', 'open']);
assert.strictEqual(rows[1].owner.name, 'Codex', 'agent name, not the child title');
assert.strictEqual(rows[1].owner.title, 'Scout', 'child title rides as the tooltip');
assert.strictEqual(rows[1].owner.color, '#10a37f', 'harness colour from the catalog');
assert.strictEqual(rows[2].owner.name, 'You');
assert.strictEqual(rows[2].owner.color, '#6b7280', 'a person is neutral');
assert.strictEqual(m.tasks[0].counts.done, 1);
assert.strictEqual(m.tasks[0].counts.total, 4);

// --- html ---------------------------------------------------------------------
const both = P.html(m);
assert.ok(both.includes('class="work-panel"') && both.includes('data-work-panel="1"'), both);
assert.ok(both.includes('work-panel-cols is-two'), 'two columns when tasks and cards');
assert.ok(both.includes('data-list-id="a"'));
assert.ok(both.includes('class="work-list-bar"') && both.includes('aria-valuenow="25"'),
  'list-level done% bar from 1/4: ' + both);
assert.ok(both.includes('style="width:25%"'), 'bar width matches pct');
const glow = both.match(/<li class="chat-widget-task is-in-progress"[^>]*>/g) || [];
assert.strictEqual(glow.length, 1, 'one in-progress row, one glow class: ' + both);
assert.ok(/<li class="chat-widget-task is-done"[^>]*><input type="checkbox" checked/.test(both), 'done row ticked');
assert.ok(/<input type="checkbox"\s+disabled/.test(both), 'panel checkboxes are read-only');
assert.ok(both.includes('agent-dot') && both.includes('background:#10a37f'), 'owner dot in harness colour');
assert.ok(both.includes('agent-identity-name">Codex<'), 'owner shows the agent name');
assert.ok(both.includes('<span class="work-owner" data-tooltip="Scout">'), 'child title is the tooltip');
assert.ok(!both.includes('agent-identity-name">Scout<'), 'child title is not the owner name');
assert.ok(!both.includes('<b>parser</b>'), 'item text is escaped');
assert.ok(both.includes('Write &lt;b&gt;parser&lt;/b&gt;'));
assert.ok(both.includes('subagent-fleet-card'), 'fleet cards in the right column');

assert.ok(P.html(P.model([bound], [], turn)).includes('work-panel-cols is-tasks'), 'tasks only');
assert.ok(P.html(P.model([], cards, turn)).includes('work-panel-cols is-cards'), 'cards only');
assert.strictEqual(P.html(P.model([], [], turn)), '', 'empty model renders nothing');

// Escaping in a list title and an owner label.
const evil = list('z', { title: '<img src=x onerror=1>', payload: { items: [
  { id: 'q', text: 'x', owner: { kind: 'user', label: '<i>me</i>' } }] } });
const ev = P.html(P.model([evil], [], turn));
assert.ok(!ev.includes('<img'), ev);
assert.ok(!ev.includes('<i>me</i>'), ev);

// --- Classic rows are the legacy markup, byte for byte --------------------------
const legacy = (item, shared, nested) => {
  const done = !!item.done;
  const disabled = shared ? '' : ' disabled';
  const id = item.id || '';
  return '<li class="chat-widget-task' + (done ? ' is-done' : '') + '" data-item-id="' + id + '">' +
    '<input type="checkbox" ' + (done ? 'checked ' : '') + disabled +
    ' aria-label="' + (shared ? 'Toggle task' : 'Agent-managed task') + '"/>' +
    '<div style="flex:1;min-width:0">' +
    '<div class="chat-widget-task-text">' + (item.text || '') + '</div>' +
    (nested || '') +
    '</div></li>';
};
const classicRow = { id: 'k1', text: 'Plain', done: false, in_progress: true,
  owner: { kind: 'chat', id: 'CH-1', label: 'Scout', agent: 'codex' } };
assert.strictEqual(T.taskRowHtml(classicRow, { shared: false, studio: false, hideable: false }),
  legacy(classicRow, false));
assert.strictEqual(T.taskRowHtml({ id: 'k2', text: 'Done', done: true }, { shared: true, hideable: false }),
  legacy({ id: 'k2', text: 'Done', done: true }, true));

// --- short labels: the work panel shows `label`, the dock keeps `text` ----------------
const labelled = { id: 'l1', text: 'Lua API: breakable <world> object', label: 'Lua API', done: false };
const shortRow = T.taskRowHtml(labelled, { studio: true, short: true });
assert.ok(shortRow.includes('>Lua API</div>'), shortRow);
assert.ok(shortRow.includes('data-tooltip="Lua API: breakable &lt;world&gt; object"'), shortRow);
const dockRow = T.taskRowHtml(labelled, { studio: true, hideable: false });
assert.ok(dockRow.includes('>Lua API: breakable &lt;world&gt; object</div>') && !dockRow.includes('data-tooltip='), dockRow);
assert.ok(T.taskRowHtml({ id: 'l2', text: 'No label' }, { studio: true, short: true }).includes('data-tooltip="No label">No label<'));
assert.ok(P.html(P.model([list('g', { payload: { items: [labelled] } })], [], turn)).includes('>Lua API</div>'));

// --- agent header ------------------------------------------------------------------
const hdr = P.agentHeaderHtml('codex', { label: 'Codex - gpt-5 · high', working: true });
assert.ok(hdr.includes('data-agent="codex"') && hdr.includes('>Codex<'), hdr);
assert.ok(hdr.includes('studio-working-tag'), 'working tag while the turn runs');
assert.ok(!P.agentHeaderHtml('codex', { label: 'Codex' }).includes('studio-working-tag'));
const unknown = P.agentHeaderHtml('zed', { label: 'Zed - model · low' });
assert.ok(unknown.includes('>Zed<'), 'chip label fallback when the agent is not in the catalog: ' + unknown);

// --- fleet studio cards -------------------------------------------------------------
const studio = F.renderStudioCardsHtml([
  { handle: 'CH-000041', label: 'Scout', agent: 'codex', outcome: 'running', summary: 'Parsing\n the   file' },
  { handle: 'CH-000042', label: 'Fin', agent: 'claude', outcome: 'done', summary: 'Ok',
    startedAt: '2026-11-18 10:00:00', finishedAt: '2026-11-18 10:02:05' },
], { esc: (s) => String(s), identityHtml: (s) => '<span class="agent-identity-name">' + s.agent + '</span>' });
assert.ok(!/progress/i.test(studio), 'no progress bar or percentages');
assert.ok(studio.includes('is-running') && studio.includes('is-done'));
assert.ok(studio.includes('Parsing the file'), 'live status is one line');
assert.ok(studio.includes('Done · 2m 05s'), 'outcome with duration when finished');
assert.ok(studio.includes('data-card-title="Scout"'), 'child title rides the card');
assert.ok(studio.includes('subagent-fleet-handle">CH-000041<'), 'handle on the identity line');
assert.ok(!studio.includes('subagent-fleet-name'), 'no separate title line');
assert.ok(!studio.includes('is-selected'), 'nothing selected by default');
const picked = F.renderStudioCardsHtml([
  { handle: 'CH-000041', label: 'Scout', agent: 'codex', outcome: 'running', summary: 'x' },
], { esc: (s) => String(s), selectedHandle: 'CH-000041', parentTitle: 'Release v0.8',
     identityHtml: () => '<span class="agent-identity-name">Codex</span>' });
assert.ok(picked.includes('studio-fleet-card is-running is-selected'), 'selected card gets the accent state');
assert.ok(picked.includes('data-parent-title="Release v0.8"'), 'parent Tasks list title for the tray');
assert.ok(picked.includes('aria-pressed="true"'));
assert.ok(studio.includes('data-chat-handle="CH-000041"'));

console.log('ok');
"""


def _run(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    env = {
        "WORK_PANEL": str(WORK_PANEL),
        "TASKS": str(TASKS),
        "FLEET": str(FLEET),
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run([node, "-e", script], env=env, capture_output=True, text=True, timeout=60, encoding="utf-8")


@node_only
def test_work_panel_model_binding_rows_and_html(tmp_path):
    result = _run(tmp_path, DRIVER)
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "ok"


def test_identity_module_is_loaded_by_the_panel_under_test():
    # The pure panel reads the identity helper; if its path moves, the driver breaks loudly.
    assert IDENTITY.exists()
    assert "module.exports" in IDENTITY.read_text(encoding="utf-8")


PEEK = WEB / "js" / "chat" / "chat_subagent_peek.js"
TRAY_CSS = WEB / "css" / "studio" / "tray.css"


@node_only
def test_tray_layout_is_push_in_studio_when_wide_and_overlay_below_900():
    driver = r"""
const P = require(process.env.PEEK);
const out = [
  P.layoutForMode('side', { studio: true, width: 1400 }),
  P.layoutForMode('center', { studio: true, width: 1400 }),
  P.layoutForMode('side', { studio: true, width: 899 }),
  P.layoutForMode('side', { studio: true, width: 900 }),
  P.layoutForMode('center', { studio: false, width: 1400 }),
  P.layoutForMode(null, { studio: false, width: 300 }),
];
console.log(JSON.stringify(out));
"""
    env = {"PEEK": str(PEEK), "PATH": "/usr/bin:/bin"}
    result = subprocess.run([shutil.which("node"), "-e", driver], env=env, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '["push","push","side","push","center","side"]'


def test_tray_css_is_studio_scoped_and_push_only():
    css = TRAY_CSS.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for prelude in re.findall(r"([^{};]+)\{", body):
        text = prelude.strip()
        if not text or text.startswith("@"):
            continue
        if text.startswith(".subagent-peek-id"):
            # Classic-neutral wrappers: display: contents keeps the flat header row.
            assert "display: contents" in css
            continue
        for part in text.split(","):
            assert part.strip().startswith(":root.cuttle-studio"), part
    assert '[data-layout="push"]' in css
    assert "cuttle-peek-push" in css
