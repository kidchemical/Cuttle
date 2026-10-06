"""Spaces canonical drop target: preview and commit share one computation.

Behavioral characterization of src/web/js/spaces/spaces_drop.js, executed
under node. Fixture layout (px):

  tab A [0..100] | sleeve g1 [100..340] { B [150..230], C [240..320] } | tab D [350..450]
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DROP_JS = REPO_ROOT / "src" / "web" / "js" / "spaces" / "spaces_drop.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const drop = require(process.env.DROP_JS);
const spaces = [
  { id: 'A' },
  { id: 'B', groupId: 'g1' },
  { id: 'C', groupId: 'g1' },
  { id: 'D' },
];
const groups = [{ id: 'g1', collapsed: false }];
const visible = ['A', 'B', 'C', 'D'];
const rects = { A: { left: 0, width: 100 }, B: { left: 150, width: 80 }, C: { left: 240, width: 80 }, D: { left: 350, width: 100 } };
const sleeveRects = { g1: { left: 100, width: 240 } };
const lanes = [{ t: 'tab', id: 'A' }, { t: 'sleeve', gid: 'g1' }, { t: 'tab', id: 'B' }, { t: 'tab', id: 'C' }, { t: 'tab', id: 'D' }];
const t = (draggedId, x, over) => drop.computeDropTarget(Object.assign(
  { spaces, groups, visibleIds: visible, draggedId, tabRects: rects, sleeveRects, lanes }, over || {}, { x }));
const cases = {
  reorderWithinGroup: t('C', 160),       // before B's midpoint -> front slot of g1
  betweenMembers: t('D', 235),           // between B and C -> joins g1
  moveOutLeft: t('B', 50),               // left of sleeve -> parks outside, ungroups
  outsiderStaysOut: t('D', 50),          // outsider left of sleeve -> stays out
  pastEndOutside: t('A', 347),           // right+7 -> outside
  endSlopJoins: t('A', 346),             // right+6 -> inside end
  frontSlot: t('D', 120),                // inside sleeve, before B -> front, joins
  onPill: t('D', 110),                   // on the pill -> joins at end
  memberStays: t('B', 200),              // member dropped inside own sleeve stays
  noCoords: t('B', NaN),                 // conservative: order end, outsider? (member stays in)
  collapsed: t('D', 200, {               // collapsed g1: members hidden, never gains
    groups: [{ id: 'g1', collapsed: true }], visibleIds: ['A', 'D'],
    sleeveRects: {}, lanes: [{ t: 'tab', id: 'A' }, { t: 'tab', id: 'D' }],
  }),
};
process.stdout.write(JSON.stringify(cases));
"""


def _run():
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": __import__("os").environ["PATH"], "DROP_JS": str(DROP_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_reorder_within_group_keeps_membership():
    res = _run()
    assert res["reorderWithinGroup"]["order"] == ["A", "C", "B", "D"]
    assert res["reorderWithinGroup"]["groupId"] == "g1"


@node_only
def test_between_members_joins_group():
    res = _run()
    assert res["betweenMembers"]["order"] == ["A", "B", "D", "C"]
    assert res["betweenMembers"]["groupId"] == "g1"


@node_only
def test_move_out_of_group_left_of_sleeve():
    res = _run()
    assert res["moveOutLeft"]["order"] == ["A", "B", "C", "D"]
    assert res["moveOutLeft"]["groupId"] is None


@node_only
def test_outsider_left_of_sleeve_stays_out():
    res = _run()
    assert res["outsiderStaysOut"]["groupId"] is None


@node_only
def test_sleeve_end_slop_boundary():
    res = _run()
    assert res["pastEndOutside"]["groupId"] is None
    assert res["endSlopJoins"]["groupId"] == "g1"


@node_only
def test_front_slot_and_pill_join():
    res = _run()
    assert res["frontSlot"]["order"] == ["A", "D", "B", "C"]
    assert res["frontSlot"]["groupId"] == "g1"
    assert res["onPill"]["groupId"] == "g1"


@node_only
def test_member_dropped_inside_stays():
    res = _run()
    assert res["memberStays"]["groupId"] == "g1"


@node_only
def test_no_coords_conservative():
    res = _run()
    assert res["noCoords"]["order"] == ["A", "C", "D", "B"]
    # Faithful port: with NaN the neighbor rule sees (outside, none) and
    # ungroups. Note the old code comment claims "members stay in" — the
    # code does not do that at end position; no UI caller passes NaN
    # (pointerup always carries clientX), so this path is unreachable and
    # preserved exactly rather than "fixed".
    assert res["noCoords"]["groupId"] is None


@node_only
def test_collapsed_group_never_gains():
    res = _run()
    assert res["collapsed"]["groupId"] is None


@node_only
def test_place_directive_mirrors_old_preview_ops():
    res = _run()
    # x=235 (between B and C, inside span): member lane -> beforeTab, joins
    assert res["betweenMembers"]["place"] == {"beforeTab": "C"}
    # x=50 (left of sleeve): sleeve lane -> insert before the sleeve, stays out
    assert res["moveOutLeft"]["place"] == {"beforeSleeve": "g1"}
    # parked end slot: x=346 aims past C but within +6 slop -> park inside end
    assert res["endSlopJoins"]["place"] == {"park": "g1"}


@node_only
def test_apply_drop_target_commits_order_and_group():
    harness = """
const drop = require(process.env.DROP_JS);
const state = { active: 'A', spaces: [{ id: 'A' }, { id: 'B', groupId: 'g1' }, { id: 'C', groupId: 'g1' }, { id: 'D' }], groups: [{ id: 'g1', collapsed: false }] };
const out = drop.applyDropTarget(state, { order: ['A', 'C', 'B', 'D'], groupId: 'g1', noop: false }, 'C');
process.stdout.write(JSON.stringify({ out, spaces: state.spaces }));
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": __import__("os").environ["PATH"], "DROP_JS": str(DROP_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["out"] == {"orderChanged": True, "groupChanged": False, "pruned": False}
    assert [s["id"] for s in res["spaces"]] == ["A", "C", "B", "D"]
