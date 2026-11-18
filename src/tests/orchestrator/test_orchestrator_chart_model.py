"""Studio playbook org chart: pure CuttleOrchestrator.chartTiers / ruleChips (node)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
MODEL = WEB / "js" / "orchestrator" / "orchestrator_model.js"

HARNESS = r"""
const M = require(process.env.MODEL);
const assert = require('assert');

const profiles = [
  { id: 'codex-low', name: 'Codex', agent: 'codex', model: 'gpt-5.6', effort: 'low' },
  { id: 'cursor-auto', name: 'Cursor', agent: 'cursor', model: 'auto', effort: '' },
  { id: 'any', name: 'Any', agent: 'none', model: '', effort: '' },
  { id: 'opus', name: 'Expert (Opus)', agent: 'claude', model: 'claude-opus-5-5', effort: 'high' },
];
const pb = {
  schema: 2, id: 'release', name: 'Release crew',
  tiers: [
    { id: 'workers', label: 'Workers', role: 'worker', use_for: 'bugs',
      members: [{ profile: 'codex-low', on: false }, { profile: 'cursor-auto', on: true },
                { name: 'Muse temp', agent: 'muse', model: '', effort: '', on: true }] },
    { id: 'escalation', label: 'Escalation', role: 'escalation',
      members: [{ name: 'Claude sonnet', agent: 'claude', model: 'sonnet', effort: 'medium', on: true }] },
    { id: 'gone', label: 'Gone', role: 'expert', members: [{ profile: 'deleted-one', on: true }] },
    { id: 'expert', label: 'Expert', role: 'expert', members: [{ profile: 'opus', on: true }, { profile: 'any', on: true }] },
  ],
  limits: { solo_attempts: 2, solo_minutes: 40, child_retries: 1, setup_retries: 2 },
  supervisor: { every_sec: 900 },
};

// Tiers keep the stored (escalation) order; members resolve from profiles / pins.
const tiers = M.chartTiers(pb, profiles);
assert.deepStrictEqual(tiers.map((t) => t.id), ['workers', 'escalation', 'gone', 'expert']);
assert.deepStrictEqual(tiers.map((t) => t.index), [0, 1, 2, 3]);
assert.strictEqual(tiers[0].label, 'Workers');
assert.strictEqual(tiers[0].roleLabel, 'Worker');
assert.strictEqual(tiers[0].hint, 'bugs');
const [codexOff, cursor, temp] = tiers[0].members;
assert.deepStrictEqual([codexOff.name, codexOff.agent, codexOff.detail], ['Codex', 'codex', 'gpt-5.6 · low']);
assert.strictEqual(codexOff.on, false);
assert.strictEqual(codexOff.isDefault, false);
// The first switched-on member is the default, even when an earlier one is off.
assert.strictEqual(cursor.isDefault, true);
assert.strictEqual(cursor.detail, 'auto');
assert.strictEqual(temp.temporary, true);
assert.strictEqual(temp.name, 'Muse temp');
assert.strictEqual(temp.detail, '');
assert.strictEqual(tiers[1].members[0].detail, 'sonnet · medium');
// Missing profile: shown as missing, never dropped.
assert.strictEqual(tiers[2].members[0].missing, true);
assert.strictEqual(tiers[2].members[0].name, 'deleted-one');
// "Any harness" profile has no agent id and says so.
const any = tiers[3].members[1];
assert.deepStrictEqual([any.agent, any.anyHarness, any.missing], ['', true, false]);
assert.strictEqual(tiers[3].members[0].isDefault, true);
assert.strictEqual(tiers[3].members[1].isDefault, false);

// A tier whose every member is off is flagged as skipped.
const offOnly = M.chartTiers({ tiers: [{ id: 'w', label: 'W', role: 'worker',
  members: [{ profile: 'cursor-auto', on: false }] }] }, profiles);
assert.strictEqual(offOnly[0].skipped, true);
assert.strictEqual(offOnly[0].members[0].isDefault, false);

// Schema-1 tiers (profiles: [id]) read as members.
const v1 = M.chartTiers({ tiers: [{ id: 'w', label: 'W', role: 'worker', profiles: ['cursor-auto'] }] }, profiles);
assert.strictEqual(v1[0].members[0].name, 'Cursor');
assert.strictEqual(v1[0].members[0].isDefault, true);

// Empty / missing input is safe.
assert.deepStrictEqual(M.chartTiers(null, null), []);
assert.deepStrictEqual(M.chartTiers({ tiers: [] }, profiles), []);

// Rule chips: one per stored field, none invented.
const chips = M.ruleChips(pb);
assert.deepStrictEqual(chips.map((c) => c.id), ['grind', 'stall', 'escalate', 'supervisor']);
assert.strictEqual(chips[0].text, 'Grind limit · 2 tries');
assert.strictEqual(chips[1].text, 'No progress · 40 min');
assert.strictEqual(chips[2].text, 'Escalate after 1 same-tier retry');
assert.strictEqual(chips[3].text, 'Supervisor · every 15 min');
assert.strictEqual(M.ruleChips({ limits: { solo_attempts: 1, child_retries: 0 }, supervisor: { every_sec: 3600 } })
  .map((c) => c.text).join('|'), 'Grind limit · 1 try|Escalate on first failure|Supervisor · every 60 min');
// Absent or non-integer fields: no chip.
assert.deepStrictEqual(M.ruleChips({}), []);
assert.deepStrictEqual(M.ruleChips({ limits: { solo_attempts: '' , solo_minutes: 'soon' }, supervisor: { every_sec: 1.5 } }), []);
assert.deepStrictEqual(M.ruleChips(null), []);
assert.deepStrictEqual(M.ruleChips({ limits: { solo_attempts: true } }), []);

console.log(JSON.stringify({ ok: true }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_orchestrator_chart_model_tiers_and_rule_chips():
    proc = subprocess.run(["node", "-e", HARNESS], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}
