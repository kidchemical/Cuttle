"""Spaces group transitions: assign/create/remove/rename/color/dissolve/collapse.

Behavioral characterization of src/web/js/spaces/spaces_groups.js under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUPS_JS = REPO_ROOT / "src" / "web" / "js" / "spaces" / "spaces_groups.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const G = require(process.env.GROUPS_JS);
const base = () => ({ active: 'A', spaces: [{ id: 'A' }, { id: 'B' }, { id: 'C' }], groups: [] });
// assign + toggle-off + adjacency
const s1 = base();
const set1 = G.setGroup(s1, 'C', 'gx');                       // unknown group -> false
const g = G.createGroupForSpace(s1, 'B', 'g1', '#8ab4f8');
const set2 = G.setGroup(s1, 'C', 'g1');                       // joins, moves next to group
const orderAfterJoin = s1.spaces.map((s) => s.id);
const toggleOff = G.setGroup(s1, 'C', 'g1');                  // clicking current removes
const stillHas = s1.spaces.find((s) => s.id === 'C').groupId || null;
// first/only member stays in place
const s2 = base();
G.createGroupForSpace(s2, 'A', 'g9', '#e8eaed');
const orderFirstStays = s2.spaces.map((s) => s.id);
// colors + rename + dissolve + delete plan
const colorOk = G.setGroupColor(s1, 'g1', '#f28b82');
const colorAfterOk = s1.groups.find((x) => x.id === 'g1').color;
const colorBad = G.setGroupColor(s1, 'g1', 'banana');
const colorAfterBad = s1.groups.find((x) => x.id === 'g1').color;
const renamed = G.renameGroup(s1, 'g1', '  Team X  ');
const s3 = base();
G.createGroupForSpace(s3, 'A', 'gd', '#e8eaed');
G.setGroup(s3, 'B', 'gd');
const members = G.planGroupDelete(s3, 'gd');
const pruned = G.pruneEmptyGroups(s3);
// collapse refuses to hide the active tab
const s4 = { active: 'A', spaces: [{ id: 'A', groupId: 'gc' }, { id: 'B' }], groups: [{ id: 'gc', collapsed: false }] };
const collapseRefused = G.toggleGroupCollapsed(s4, 'gc');
const collapseOk = G.toggleGroupCollapsed({ active: 'B', spaces: s4.spaces, groups: s4.groups }, 'gc');
process.stdout.write(JSON.stringify({ set1, set2, orderAfterJoin, toggleOff, stillHas,
  orderFirstStays, colorOk, colorAfterOk, colorBad, colorAfterBad, renamed, members, pruned,
  color: s1.groups.find((x) => x.id === 'g1').color,
  gname: s1.groups.find((x) => x.id === 'g1').name,
  collapseRefused, collapseOk, groupsLeft: s3.groups }));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "GROUPS_JS": str(GROUPS_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_assign_moves_next_to_group_and_toggles_off():
    res = _run()
    assert res["set1"] is False
    assert res["set2"] is True
    assert res["orderAfterJoin"] == ["A", "B", "C"]
    assert res["toggleOff"] is True
    assert res["stillHas"] is None


@node_only
def test_first_member_stays_in_place():
    assert _run()["orderFirstStays"] == ["A", "B", "C"]


@node_only
def test_color_rename_dissolve_delete():
    res = _run()
    assert res["colorOk"] is True and res["colorAfterOk"] == "#f28b82"
    assert res["colorBad"] is True  # invalid falls back to default, still applies
    assert res["colorAfterBad"] == "#e8eaed"
    assert res["gname"] == "Team X"
    assert res["members"] == ["A", "B"]
    assert res["pruned"] is False  # dissolve already removed it
    assert res["groupsLeft"] == []


@node_only
def test_collapse_never_hides_active():
    res = _run()
    assert res["collapseRefused"] == {"ok": False, "reason": "hides-active"}
    assert res["collapseOk"] == {"ok": True, "collapsed": True}


@node_only
def test_groups_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(GROUPS_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
