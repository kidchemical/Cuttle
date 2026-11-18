"""Reply-thread grouping (chat_thread_model.js) — pure rules under node.

Which rows fold under a turn's bubble: steers (by turn id, or the current
turn for rows saved before steers carried one), later follow-ups of the
same turn; and what the bar says.
"""

from __future__ import annotations

import json
import shutil
import os
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
// Idle steers into a detached run: plain rows; the run's later rows start a new thread.
out.idleSteer = shape(M.group([
  u({}), a({ turnId: 'T1', messageId: 11 }),
  u({ steered: true, steerIdle: true, turnId: 'T1', messageId: 12 }), a({ turnId: 'T1', messageId: 13 }),
  u({ steered: true, turnId: 'T1', messageId: 14 }), a({ turnId: 'T1', messageId: 15 }),
  u({ steered: true, steerIdle: true, turnId: 'T1', messageId: 16 }), a({ turnId: 'T1', messageId: 17 }),
]));
// Sub-agents: finished children are replies, running ones only "working".
out.subagents = shape(M.group([u({}), a({ turnId: 'T1', messageId: 11, subagents: [
  { handle: 'CH-000201', label: 'Scout', outcome: 'done', finishedAt: '2026-11-08T10:00:00Z', summary: 'found it' },
  { handle: 'CH-000202', label: 'Judge', outcome: 'running', generating: true },
  { handle: 'CH-000203', label: 'Critic', outcome: 'failed', finishedAt: '2026-11-08T09:00:00Z' },
] })]));
out.mixed = shape(M.group([u({}), a({ turnId: 'T1', messageId: 11, subagents: [{ handle: 'CH-000201', outcome: 'done' }] }), u({ steered: true, turnId: 'T1' })]));
out.onlyRunning = shape(M.group([u({}), a({ turnId: 'T1', subagents: [{ handle: 'CH-000202', outcome: 'running' }] })]));
out.preview = [
  M.previewText('/claude   also check\n the token refresh'),
  M.previewText('<think>hmm</think> Done: ```code``` tests pass'),
  M.previewText('x'.repeat(200), 20),
];
const receipt = (state, mode, reply, covers) => M.steerReceipt({messageId: 12, turnId:'T1', metadata:{steer:{v:1,state,mode,reply,covers}}, steerIdle:false}, [], 'T1');
out.receipts = [receipt('pending','live',null,[]), receipt('delivered','live',{text:'ok'},[12]), receipt('pending','detached',null,[]), receipt('delivered','detached',{text:'ok'},[12]), receipt('undelivered','live',null,[]), M.steerReceipt({metadata:{steer:{state:'delivered'}}}, [], 'T1')];
const row = (o, steer) => Object.assign({ messageId: 12, turnId: 'T1', steerIdle: false, metadata: steer === undefined ? undefined : { steer } }, o);
const st = (o) => Object.assign({ v: 1, steer_id: 'h', state: 'pending', delivered_at: null, mode: 'live', reply: null, covers: [] }, o);
const label = (r) => (r ? r.label : null);
out.labels = {
  // The live extra wins for the current turn, even when the row has no saved receipt yet.
  liveWinsOverSaved: label(M.steerReceipt(row({}, st({ state: 'pending' })), [{ message_id: 12, ...st({ state: 'delivered' }) }], 'T1')),
  liveWithoutSavedReceipt: label(M.steerReceipt(row({}, undefined), [{ message_id: 12, ...st({}) }], 'T1')),
  liveOtherRowIgnored: label(M.steerReceipt(row({}, st({ state: 'delivered' })), [{ message_id: 99, ...st({}) }], 'T1')),
  // A row without an id never matches a live entry.
  noIdNoLiveMatch: label(M.steerReceipt(row({ messageId: null }, undefined), [{ message_id: 12, ...st({}) }], 'T1')),
  // Pending on a live row whose turn is no longer live: not a spinner.
  pendingOtherTurn: label(M.steerReceipt(row({ turnId: 'T2' }, st({})), [], 'T1')),
  pendingNoLiveTurn: label(M.steerReceipt(row({}, st({})), [], '')),
  undeliveredDetached: label(M.steerReceipt(row({}, st({ mode: 'detached', state: 'undelivered' })), [], 'T1')),
  undeliveredLive: label(M.steerReceipt(row({}, st({ state: 'undelivered' })), [], 'T1')),
  // A live entry without v is ignored; the row's own v1 receipt still renders.
  unknownLiveShape: label(M.steerReceipt(row({}, st({})), [{ message_id: 12, state: 'delivered', mode: 'live' }], 'T1')),
  noRowAtAll: M.steerReceipt(null, [], 'T1'),
};
// Excerpt placement.
const excerptOf = (steer) => M.steerReceipt(row({}, st(steer)), [], 'T1');
out.excerpts = {
  groupedFirst: excerptOf({ state: 'delivered', mode: 'live', reply: null, covers: [12, 13] }).excerpt,
  groupedLast: M.steerReceipt(row({ messageId: 13 }, st({ state: 'delivered', mode: 'live', reply: { text: 'answer' }, covers: [12, 13] })), [], 'T1').excerpt,
  // A reply that names this row but is not the last covered row is never shown here.
  notLastCovered: excerptOf({ state: 'delivered', mode: 'live', reply: { text: 'x' }, covers: [12, 13] }).excerpt,
  idleSteer: M.steerReceipt(row({ steerIdle: true }, st({ state: 'delivered', mode: 'detached', reply: { text: 'x' }, covers: [12] })), [], 'T1').excerpt,
  emptyCovers: excerptOf({ state: 'delivered', mode: 'live', reply: { text: 'x' }, covers: [] }).excerpt,
  liveReply: M.steerReceipt(row({}, st({ state: 'pending' })), [{ message_id: 12, ...st({ state: 'delivered', reply: { text: 'live' }, covers: [12] }) }], 'T1').excerpt,
  label: M.EXCERPT_LABEL,
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    res = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, check=True,
                         encoding="utf-8",
                         env={**os.environ, "MOD_JS": str(MOD_JS)})
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
def test_fleet_cards_do_not_create_or_change_reply_threads(out):
    assert out["subagents"]["threads"] == []
    assert out["onlyRunning"]["threads"] == []
    t = out["mixed"]["threads"][0]
    assert t["summary"] == {"total": 1, "label": "1 reply", "detail": "1 steer"}
    assert t["unread"] == []


@node_only
def test_preview_is_one_quiet_line(out):
    assert out["preview"][0] == "also check the token refresh"
    assert out["preview"][1] == "Done: tests pass"
    assert len(out["preview"][2]) == 20 and out["preview"][2].endswith("…")


@node_only
def test_idle_steer_starts_a_new_thread_instead_of_joining_the_old_turn(out):
    g = out["idleSteer"]
    # Rows 0..2 and 6..7 stand alone; 3 (answer to the idle steer) roots a new
    # thread that the working-phase steer (4) and its answer (5) join.
    assert g["memberOf"] == [-1, -1, -1, 0, 0, 0, -1, -1]
    assert len(g["threads"]) == 1
    t = g["threads"][0]
    assert t["root"] == 3 and t["key"] == "t:T1#1"
    assert [r["index"] for r in t["replies"]] == [4, 5]


def test_steer_receipt_copy_and_legacy_shape(out):
    assert [item["label"] if item else None for item in out["receipts"]] == [
        "Sending into the live turn…", "Steered into the live turn · no restart",
        "Accepted into the background run", "Read by the background run",
        "Ended without confirmation", None,
    ]
    assert out["receipts"][1]["excerpt"] == "ok"
    assert out["receipts"][0]["excerpt"] is None


@node_only
def test_receipt_label_for_every_contract_state(out):
    labels = out["labels"]
    assert labels["liveWinsOverSaved"] == "Steered into the live turn · no restart"
    assert labels["liveWithoutSavedReceipt"] == "Sending into the live turn…"
    # A live entry for another message never overrides this row's saved receipt.
    assert labels["liveOtherRowIgnored"] == "Steered into the live turn · no restart"
    # A row without an id cannot be matched by a live entry, and has no saved receipt: nothing to show.
    assert labels["noIdNoLiveMatch"] is None
    assert labels["pendingOtherTurn"] == "Not confirmed"
    assert labels["pendingNoLiveTurn"] == "Not confirmed"
    assert labels["undeliveredDetached"] == "Ended without confirmation"
    assert labels["undeliveredLive"] == "Ended without confirmation"
    # A live entry without `v` is not trusted: the row's own v1 receipt is used.
    assert labels["unknownLiveShape"] == "Sending into the live turn…"
    assert out["labels"]["noRowAtAll"] is None


@node_only
def test_excerpt_is_shown_once_on_the_last_covered_row(out):
    ex = out["excerpts"]
    assert ex["groupedFirst"] is None  # earlier member of a group carries no reply
    assert ex["groupedLast"] == "answer"
    assert ex["notLastCovered"] is None
    assert ex["idleSteer"] is None  # never on a steer_idle row
    assert ex["emptyCovers"] is None  # covers [] means no reply yet
    assert ex["liveReply"] == "live"  # the live extra carries the reply for the current turn
    assert ex["label"] == "Agent response excerpt"
