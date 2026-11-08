"""Reply-thread grouping (chat_thread_model.js) — pure rules under node.

Which rows fold under a turn's bubble: steers (by turn id, or the current
turn for rows saved before steers carried one), later follow-ups of the
same turn, finished sub-agent children; and what the bar says.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_thread_model.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const M = require(process.env.MOD_JS);
const u = (o) => Object.assign({ role: 'user' }, o);
const a = (o) => Object.assign({ role: 'assistant' }, o);
const shape = (g) => ({
  memberOf: g.memberOf,
  threads: g.threads.map((t) => ({ key: t.key, root: t.root, replies: t.replies, counts: t.counts,
    live: t.live, working: t.working, latest: t.latest && { kind: t.latest.kind, index: t.latest.index },
    unread: t.unreadKeys.map((k) => k.key), summary: M.summary(t) })),
});
const out = {};
// Saved history: steers named by turn id, a follow-up of the same turn.
out.saved = shape(M.group([
  u({}), a({ turnId: 'T1', messageId: 11 }), u({ steered: true, turnId: 'T1', messageId: 12 }),
  a({ turnId: 'T1', messageId: 13, ts: 5 }), u({ messageId: 14 }), a({ turnId: 'T2', messageId: 15 }),
]));
// Older rows: steers saved without a turn id join the current turn's root.
out.legacy = shape(M.group([
  u({}), a({ turnId: 'T1', messageId: 11 }), u({ steered: true }), u({ steered: true }),
  u({}), a({ turnId: 'T2' }), u({ steered: true }),
]));
// A steer above its turn's reply (no early row) stays a plain row.
out.steerBeforeRoot = shape(M.group([u({}), u({ steered: true }), a({ turnId: 'T1' })]));
// A steer naming another turn never falls back onto this one.
out.otherTurn = shape(M.group([u({}), a({ turnId: 'T1' }), u({ steered: true, turnId: 'T9' })]));
// Live: the typing bubble is the root; the steer you just sent joins it.
out.live = shape(M.group([u({}), a({ live: true }), u({ steered: true })]));
// Detached follow-up typing bubble with the first reply's turn id is a live reply.
out.liveFollowup = shape(M.group([u({}), a({ turnId: 'T1', messageId: 11 }), u({ steered: true, turnId: 'T1' }),
  a({ turnId: 'T1', live: true })]));
// Sub-agents: finished children are replies, running ones only "working".
out.subagents = shape(M.group([u({}), a({ turnId: 'T1', messageId: 11, subagents: [
  { handle: 'CH-000201', label: 'Scout', outcome: 'done', finishedAt: '2026-11-08T10:00:00Z', summary: 'found it' },
  { handle: 'CH-000202', label: 'Judge', outcome: 'running', generating: true },
  { handle: 'CH-000203', label: 'Critic', outcome: 'failed', finishedAt: '2026-11-08T09:00:00Z' },
] })]));
out.onlyRunning = shape(M.group([u({}), a({ turnId: 'T1', subagents: [{ handle: 'CH-000202', outcome: 'running' }] })]));
out.preview = [
  M.previewText('/claude   also check\n the token refresh'),
  M.previewText('<think>hmm</think> Done: ```code``` tests pass'),
  M.previewText('x'.repeat(200), 20),
];
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    res = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, check=True,
                         env={"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    return json.loads(res.stdout)


@node_only
def test_saved_steers_and_followups_fold_under_their_turn(out):
    g = out["saved"]
    assert len(g["threads"]) == 1
    t = g["threads"][0]
    assert t["key"] == "t:T1" and t["root"] == 1
    assert t["replies"] == [{"index": 2, "kind": "steer"}, {"index": 3, "kind": "followup"}]
    assert t["summary"] == {"total": 2, "label": "2 replies", "detail": "1 steer · 1 follow-up"}
    assert t["unread"] == ["m:13"]  # your own steer is never unread
    assert g["memberOf"] == [-1, 0, 0, 0, -1, -1]  # T2 has no replies: no thread


@node_only
def test_older_steers_without_turn_id_join_the_current_turn(out):
    keys = [(t["root"], [r["index"] for r in t["replies"]]) for t in out["legacy"]["threads"]]
    assert keys == [(1, [2, 3]), (5, [6])]


@node_only
def test_steer_never_threads_above_or_into_the_wrong_turn(out):
    assert out["steerBeforeRoot"]["threads"] == []
    assert out["otherTurn"]["threads"] == []


@node_only
def test_live_bubble_is_the_root_and_live_followups_are_replies(out):
    live = out["live"]["threads"][0]
    assert live["root"] == 1 and live["live"] is True and live["replies"] == [{"index": 2, "kind": "steer"}]
    lf = out["liveFollowup"]["threads"][0]
    assert lf["live"] is True
    assert lf["replies"][-1] == {"index": 3, "kind": "followup"}
    assert lf["unread"] == []  # still being written


@node_only
def test_finished_subagents_are_replies_running_ones_only_working(out):
    t = out["subagents"]["threads"][0]
    assert t["counts"] == {"steer": 0, "followup": 0, "subagent": 2}
    assert t["working"] == 1 and t["live"] is False
    assert t["summary"]["detail"] == "2 sub-agents"
    assert t["latest"]["kind"] == "subagent"
    assert t["unread"] == ["s:CH-000201:done", "s:CH-000203:failed"]
    assert out["onlyRunning"]["threads"] == []


@node_only
def test_preview_is_one_quiet_line(out):
    assert out["preview"][0] == "also check the token refresh"
    assert out["preview"][1] == "Done: tests pass"
    assert len(out["preview"][2]) == 20 and out["preview"][2].endswith("…")
