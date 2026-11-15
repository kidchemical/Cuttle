"""Fleet cards from the moment ``spawn`` creates the batch (no wait for the next reply).

- Spawned during a parent turn: bound at once to that turn's row
  (api.chat_turn_row); live status says so (``bound_message_id``); the saved
  reply — the same row — carries the cards exactly once.
- Spawned while no parent turn runs (idle chat, manual CLI run, dead turn): live
  status carries them unbound (the page's live strip); when the batch ends
  with no reply to carry it, the cards stay on the parent's last bubble.
- The page places each live card once (pure ``placeLive``; browser hand-over
  in ``e2e/test_chat_background_work.py``).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api import chat_turn_row
from api.subagents import fleet, service, store
from tests.subagents.test_subagents import _runner_factory, _seed

LAUNCHERS_JS = Path(__file__).resolve().parents[2] / "web" / "js" / "chat" / "chat_subagent_launchers.js"


@pytest.fixture(autouse=True)
def _fleet_on(monkeypatch):
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: True)


def _spawn(db, parent, *, wait, titles=("Scout",)):
    return service.spawn(parent_session_id=parent, children=[{"title": t, "message": t} for t in titles],
                         wait=wait, timeout=6, runner=_runner_factory({t: f"{t} found it" for t in titles}),
                         db=db)


def _saver(db, parent, tmp_path):
    from api.chat_turn_persist import make_assistant_saver

    return make_assistant_saver(
        chat_session_id=parent, db=db, resolve_project=lambda d: "",
        assistant_meta_fn=lambda r: {"query_id": r.get("query_id")},
        project_merge_fn=lambda meta, *a: meta, schedule_autoname=lambda *a: None,
        project_root=tmp_path)


def test_spawn_during_a_turn_binds_to_its_row_and_the_reply_carries_cards_once(tmp_path):
    db, _p, _o, parent = _seed(tmp_path)
    row = chat_turn_row.open_row(db, parent, turn=1)
    _spawn(db, parent, wait=True, titles=("Scout", "Critic"))

    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.attach_message_id == row["message_id"]  # at spawn, not at the reply
    live = service.public_parent_subagents(parent, db=db)
    assert {e["bound_message_id"] for e in live} == {row["message_id"]}
    assert {e["label"] for e in live} == {"Scout", "Critic"}

    _saver(db, parent, tmp_path)({"success": True, "response": "Both done.", "query_id": row["query_id"]})
    [reply] = [m for m in db.get_messages(parent) if m["role"] == "assistant"]
    assert reply["id"] == row["message_id"]
    cards = reply["metadata"]["subagents"]
    assert sorted(c["label"] for c in cards) == ["Critic", "Scout"]  # once each
    assert len({c["id"] for c in cards}) == 2
    # History hydration keeps them current; nothing is unbound to re-attach later.
    assert [b for b in store.list_batches_for_parent(db, parent) if not b.attach_message_id] == []


def test_turn_that_dies_keeps_its_cards_on_the_settled_row(tmp_path):
    db, _p, _o, parent = _seed(tmp_path)
    row = chat_turn_row.open_row(db, parent, turn=1)
    _spawn(db, parent, wait=True)
    chat_turn_row.close(db, parent, status="cancelled", turn=1)
    [card_row] = [m for m in db.get_messages(parent) if m["role"] == "assistant"]
    assert card_row["id"] == row["message_id"]
    assert card_row["content"].startswith("**Agent stopped before completion.**")
    assert [c["label"] for c in card_row["metadata"]["subagents"]] == ["Scout"]


def test_no_turn_env_falls_back_to_the_latest_parent_bubble(tmp_path):
    """A manual CLI run (no CUTTLE_TURN_ID, no --anchor): the latest parent bubble, at once."""
    db, _p, _o, parent = _seed(tmp_path)
    db.add_message(parent, "assistant", "Older reply.", metadata={"query_id": "e0"})
    last = db.add_message(parent, "assistant", "Earlier reply.", metadata={"query_id": "e1"})
    _spawn(db, parent, wait=False)
    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.turn_id == "" and batch.attach_message_id == last  # bound at spawn
    [entry] = service.public_parent_subagents(parent, db=db)
    assert entry["bound_message_id"] == last and entry["turn_id"] is None
    reply = next(m for m in db.get_messages(parent) if m["id"] == last)
    assert [c["label"] for c in reply["metadata"]["subagents"]] == ["Scout"]
    assert reply["metadata"]["query_id"] == "e1"  # the bubble's own metadata kept
    # A later reply of some turn never takes it.
    later = db.add_message(parent, "assistant", "next turn", metadata={"turn_id": "tNext"})
    assert not (next(m for m in db.get_messages(parent) if m["id"] == later)["metadata"].get("subagents"))
    assert store.list_batches_for_parent(db, parent)[0].attach_message_id == last


def test_no_bubble_at_all_keeps_the_cards_on_the_strip_until_one_exists(tmp_path):
    db, _p, _o, parent = _seed(tmp_path)  # only a user row
    _spawn(db, parent, wait=False)
    [entry] = service.public_parent_subagents(parent, db=db)
    assert entry["bound_message_id"] is None  # the page's live strip, at once
    [batch] = store.list_batches_for_parent(db, parent)
    for child in batch.children:
        store.update_child(db, child.id, status="done", result="Scout found it")
    service._finalize_if_complete(db, batch)
    assert [e["label"] for e in service.public_parent_subagents(parent, db=db)] == ["Scout"]  # still shown
    last = db.add_message(parent, "assistant", "first reply", metadata=None)
    service.leave_cards_on_last_bubble(db, store.list_batches_for_parent(db, parent)[0])
    assert store.list_batches_for_parent(db, parent)[0].attach_message_id == last
    assert service.public_parent_subagents(parent, db=db) == []


_PLACE_DRIVER = r"""
require(process.env.JS);
const L = globalThis.CuttleChatSubagentLaunchers.create({
  win: { document: {} }, escapeHtmlInline: (s) => s, parseChatHandleToken: () => null,
  avatarHtml: () => '', chipCategoryClass: () => '' });
const list = [
  { handle: 'CH-000011', boundMessageId: '', turnId: '' },          // manual run, no turn
  { handle: 'CH-000012', boundMessageId: '40', turnId: 'turnA' },   // spawned in the running turn A
  { handle: 'CH-000013', boundMessageId: '33', turnId: 'turnB' },   // its saved bubble is on screen
  { handle: 'CH-000014', boundMessageId: '', turnId: 'turnC' },     // a turn whose bubble is not live here
];
const saved = new Set(['33']);
process.stdout.write(JSON.stringify({
  turnRunning: L.placeLive(list, saved, new Set(['turnA'])),
  idle: L.placeLive(list, saved, new Set()),
  afterReply: L.placeLive(list, new Set(['33', '40']), new Set()),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_page_places_each_live_card_once_keyed_to_its_turn():
    import os

    out = subprocess.run(["node", "-e", _PLACE_DRIVER], capture_output=True, text=True, timeout=30,
                         env={"PATH": os.environ["PATH"], "JS": str(LAUNCHERS_JS)})
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    handles = lambda xs: [x["handle"] for x in xs]  # noqa: E731
    # Turn A runs: its card rides turn A's live bubble; a card of another turn,
    # or of a manual run with no turn, never rides it (strip).
    assert handles(r["turnRunning"]["live"]) == ["CH-000012"]
    assert handles(r["turnRunning"]["strip"]) == ["CH-000011", "CH-000014"]
    # No live bubble: everything not owned by an on-screen bubble is on the strip at once.
    assert handles(r["idle"]["strip"]) == ["CH-000011", "CH-000012", "CH-000014"]
    # Turn A's reply (row 40) is saved and on screen: it carries its card; the strip hands over.
    assert handles(r["afterReply"]["strip"]) == ["CH-000011", "CH-000014"]


@pytest.mark.parametrize("live_row", [False, True])
def test_detached_launch_without_identity_never_binds_to_older_reply(tmp_path, monkeypatch, live_row):
    db, _p, _o, parent = _seed(tmp_path)
    old = db.add_message(parent, "assistant", "Yesterday's answer", metadata={"turn_id": "old"})
    db.add_message(parent, "user", "New work")
    if live_row:
        chat_turn_row.open_row(db, parent, turn=1)
    # A separate systemd process has neither the in-process turn context nor env.
    monkeypatch.setattr("api.chat_turn_id.ambient", lambda sid: "")
    _spawn(db, parent, wait=True)
    [batch] = store.list_batches_for_parent(db, parent)
    assert not batch.turn_id and batch.attach_message_id is None
    assert not json.loads(db.get_message_by_id(old)["metadata"]).get("subagents")
    [entry] = service.public_parent_subagents(parent, db=db)
    assert entry["bound_message_id"] is None  # visible on the strip, including after completion


def test_repair_detached_cards_moves_only_that_batch_and_is_idempotent(tmp_path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    old = db.add_message(parent, "assistant", "Old reply", metadata={"turn_id": "old", "keep": True})
    monkeypatch.setattr("api.chat_turn_id.ambient", lambda sid: "")
    _spawn(db, parent, wait=True)
    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.attach_message_id == old
    target = db.add_message(parent, "assistant", "Originating turn", metadata={"turn_id": "origin"})
    later = db.add_message(parent, "assistant", "Unrelated later turn", metadata={"turn_id": "later"})
    for _ in range(2):
        assert service.repair_batch_cards(batch.id, "origin", db=db)["message_id"] == target
    repaired = store.get_batch(db, batch.id)
    assert repaired.turn_id == "origin" and repaired.attach_message_id == target
    assert json.loads(db.get_message_by_id(old)["metadata"])["keep"] is True
    assert not json.loads(db.get_message_by_id(old)["metadata"]).get("subagents")
    assert len(json.loads(db.get_message_by_id(target)["metadata"])["subagents"]) == 1
    assert not json.loads(db.get_message_by_id(later)["metadata"]).get("subagents")
    with pytest.raises(service.SubagentError, match="different turn"):
        service.repair_batch_cards(batch.id, "later", db=db)
    with pytest.raises(service.SubagentError, match="no assistant row"):
        service.repair_batch_cards(batch.id, "unknown", db=db)
