"""Spaces ordering + activity aggregation.

Behavioral characterization of spaces_order.js (visible-order commit with
hidden slots kept) and spaces_activity.js (dot priority) under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPACES_DIR = REPO_ROOT / "src" / "web" / "js" / "spaces"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const O = require(process.env.SPACES_DIR + '/spaces_order.js');
const A = require(process.env.SPACES_DIR + '/spaces_activity.js');
// full [A,B(hidden),C,D], visible [A,C,D], drag D to front => [D,A,B(hidden),C]
const re1 = O.computeReorderedIds(['A', 'B', 'C', 'D'], ['D', 'A', 'C'], 'D');
const re2 = O.computeReorderedIds(['A', 'B'], ['A', 'B'], 'A');   // no change -> null
const re3 = O.computeReorderedIds(['A', 'B'], ['A'], 'ghost');    // dragged missing -> null
const st = { spaces: [{ id: 'A' }, { id: 'B' }, { id: 'C' }] };
const applied = O.applyVisibleOrder(st, ['C', 'A', 'B'], 'C');
// activity
A.resetActivity();
A.noteSessionActivity('db_session_7', 'unread', false);
const look = A.lookupSessionActivity('7');
const kinds = { q: A.followupKind([{ paused: true }]), a: A.followupKind([{}]), e: A.followupKind([]) };
const entries = { s1: { activity: 'unread', running: false }, s2: { activity: 'queued', running: false }, s3: { activity: '', running: true } };
const best = A.selectSpaceActivity(['s1', 's2', 's3'], (id) => entries[id], false, new Set());
const seen = A.selectSpaceActivity(['s1'], (id) => entries[id], true, new Set(['s1', 'db_session_s1']));
const entries9 = { 9: { activity: 'error', running: false } };
const seenReverse = A.selectSpaceActivity(['9'], (id) => entries9[id], true, new Set(['db_session_9']));
const variants = A.sidVariants('db_session_7');
process.stdout.write(JSON.stringify({ re1, re2, re3, applied, order: st.spaces.map((s) => s.id),
  look, kinds, best, seen, seenReverse, variants, rank: A.RANK }));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "SPACES_DIR": str(SPACES_DIR)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_reorder_keeps_hidden_slots():
    res = _run()
    assert res["re1"] == ["D", "A", "B", "C"]
    assert res["re2"] is None
    assert res["re3"] is None
    assert res["applied"] is True
    assert res["order"] == ["C", "A", "B"]


@node_only
def test_activity_priority_and_seen_suppression():
    res = _run()
    assert res["rank"] == {"running": 5, "error": 4, "unread": 3, "queued": 2, "paused": 1}
    assert res["look"] == {"activity": "unread", "running": False, "at": res["look"]["at"]}
    assert res["kinds"] == {"q": "paused", "a": "queued", "e": ""}
    assert res["best"] == "running"  # running outranks unread + queued
    assert res["seen"] == ""  # unread on a visible chat is already seen
    assert res["seenReverse"] == ""  # reverse id form also suppresses
    assert res["variants"] == ["db_session_7", "7"]


@node_only
def test_order_and_activity_modules_parse():
    import subprocess as sp
    for name in ("spaces_order.js", "spaces_activity.js"):
        proc = sp.run(["node", "--check", str(SPACES_DIR / name)], capture_output=True, text=True)
        assert proc.returncode == 0, name + ": " + proc.stderr
