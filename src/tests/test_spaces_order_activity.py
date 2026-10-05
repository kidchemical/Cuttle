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
A.setUnreadPrefs({ db_session_7: { hasUnread: true } });
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
    assert res["rank"] == {"input": 6, "running": 5, "error": 4, "unread": 3, "queued": 2, "paused": 1}
    assert res["look"] == {"activity": "unread", "running": False}
    assert res["kinds"] == {"q": "paused", "a": "queued", "e": ""}
    assert res["best"] == "running"  # running outranks unread + queued
    assert res["seen"] == ""  # unread on a visible chat is already seen
    assert res["seenReverse"] == ""  # reverse id form also suppresses
    assert res["variants"] == ["db_session_7", "7"]


LIFECYCLE = """
const A = require(process.env.SPACES_DIR + '/spaces_activity.js');
const out = {};
const frame = {};
const look = (id, t) => A.lookupSessionActivity(id, t);
A.resetActivity();

// Frame raises a spinner for its own turn -> running before any poll.
A.notePushSnapshot(frame, [{ id: '5', activity: '', running: true }], ['5'], 1000);
out.pushRunning = look('5', 1100);
// Poll sent after the spinner rose, past the grace window, says idle -> cleared.
out.finished1 = A.noteServerSnapshot([{ id: '5', running: false, queue: '' }], 6000);
out.serverIdle = look('5', 6100);
// The frame re-pushes the same stale spinner: it must not come back.
A.notePushSnapshot(frame, [{ id: '5', activity: '', running: true }], ['5'], 7000);
out.staleRepush = look('5', 7100);
// A poll that raced the turn start (inside grace) does not clear it.
A.resetActivity();
A.notePushSnapshot(frame, [{ id: '5', activity: '', running: true }], ['5'], 1000);
A.noteServerSnapshot([{ id: '5', running: false }], 1500);
out.graceKeeps = look('5', 2000);

// Server-only running (background space, no frame) and its finish edge.
A.resetActivity();
A.noteServerSnapshot([{ id: 'db_session_9', running: true, queue: '' }], 1000);
out.serverRunning = look('9', 1100);
out.finished = A.noteServerSnapshot([{ id: '9', running: false, queue: '' }], 2000);
out.afterFinish = look('9', 2100);

// Owning frame saw the end after the poll -> trust the frame.
A.resetActivity();
A.noteServerSnapshot([{ id: '4', running: true }], 1000);
A.notePushSnapshot(frame, [], ['4'], 2000);
out.frameSawEnd = look('4', 2100);

// Prefs decide unread; a cleared row beats an older pushed unread.
A.resetActivity();
A.notePushSnapshot(frame, [{ id: '3', activity: 'unread', running: false }], ['3'], 1000);
out.pushUnreadNoRow = look('3', 1100);
A.setUnreadPrefs({ 3: { hasUnread: false, stickyChips: [] } });
out.prefsCleared = look('3', 1200);
A.setUnreadPrefs({ 'CH-000003': { hasUnread: true, unreadIsError: true } });
out.prefsError = look('3', 1300);

// Queue: server kind, live-status rows (queue null) keep it.
A.resetActivity();
A.noteServerSnapshot([{ id: '8', running: false, queue: 'paused' }], 1000);
A.noteServerSnapshot([{ id: '8', running: false, queue: null }], 2000);
out.queueKept = look('8', 2100);
A.setLocalQueues({ web_session_1: [{ paused: false }] });
out.localQueue = look('web_session_1', 2200);

// Replaced/closed frames are pruned.
A.resetActivity();
A.notePushSnapshot(frame, [{ id: '2', activity: '', running: true }], ['2'], 1000);
A.pruneSources(() => false);
out.pruned = look('2', 1100);
out.bare = [A.bareSid('db_session_12'), A.bareSid('CH-000012-4'), A.bareSid('012'), A.bareSid('web_x')];
// An owned local turn survives idle polls; passive stale spinners still expire.
A.resetActivity();
A.notePushSnapshot(frame, [{id:'989', running:true, localRunning:true}], ['989'], 1000);
A.noteServerSnapshot([{id:'989', running:false}], 6000);
out.localTurn = look('989', 9000);
A.notePushSnapshot(frame, [], ['989'], 10000);
out.localFinished = look('989', 11000);
// Input on any pane wins over running; visible input is not read/unread.
A.setUnreadPrefs({989:{awaitingInput:true}, 4:{hasUnread:true,unreadIsError:true}});
A.noteServerSnapshot([{id:'5',running:true}], 12000);
out.multiPaneInput = A.selectSpaceActivity(['989','5','4'], id => look(id, 13000), true, new Set(['989']));
A.setUnreadPrefs({989:{awaitingInput:false}, 4:{hasUnread:true,unreadIsError:true}});
out.afterAnswer = A.selectSpaceActivity(['989','5','4'], id => look(id, 13000), false, new Set());
A.resetActivity();
A.setUnreadPrefs({42:{hasUnread:false}});
A.notePushSnapshot(frame, [{id:'42',activity:'error',visibleAttention:true}], ['42'], 1000);
out.visibleAttention = A.selectSpaceActivity(['42'], id => look(id, 1100), true, new Set(['42']));
A.notePushSnapshot(frame, [], ['42'], 2000);
out.attentionAcked = look('42', 2100);
process.stdout.write(JSON.stringify(out));
"""


@node_only
def test_activity_lifecycle_server_truth_beats_stale_frames():
    import os
    proc = subprocess.run(
        ["node", "-e", LIFECYCLE],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "SPACES_DIR": str(SPACES_DIR)},
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    running = {"activity": "", "running": True}
    assert res["pushRunning"] == running
    assert res["finished1"] == []  # the server never saw it running
    assert res["serverIdle"] is None
    assert res["staleRepush"] is None  # stuck-spinner regression
    assert res["graceKeeps"] == running
    assert res["serverRunning"] == running
    assert res["finished"] == ["9"]
    assert res["afterFinish"] is None
    assert res["frameSawEnd"] is None
    assert res["pushUnreadNoRow"] == {"activity": "unread", "running": False}
    assert res["prefsCleared"] is None
    assert res["prefsError"] == {"activity": "error", "running": False}
    assert res["queueKept"] == {"activity": "paused", "running": False}
    assert res["localQueue"] == {"activity": "queued", "running": False}
    assert res["pruned"] is None
    assert res["visibleAttention"] == "error"
    assert res["attentionAcked"] is None
    assert res["localTurn"] == running
    assert res["localFinished"] is None
    assert res["multiPaneInput"] == "input"
    assert res["afterAnswer"] == "running"
    assert res["bare"] == ["12", "12", "12", "web_x"]


@node_only
def test_order_and_activity_modules_parse():
    import subprocess as sp
    for name in ("spaces_order.js", "spaces_activity.js"):
        proc = sp.run(["node", "--check", str(SPACES_DIR / name)], capture_output=True, text=True)
        assert proc.returncode == 0, name + ": " + proc.stderr
