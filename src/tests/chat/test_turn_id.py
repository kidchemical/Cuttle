"""The turn id anchors sub-agent cards (api.chat_turn_id), never "the next reply".

- Every harness CLI gets ``CUTTLE_TURN_ID`` (the turn's query id).
- Every assistant row is stamped with ``metadata.turn_id`` by the DB itself,
  whichever path writes it (insert, once-insert, in-place update).
- ``spawn`` / ``message`` store the calling turn; cards bind to that turn's
  row — when it already exists, or when any path saves it later — and a
  reply of another turn never takes them. ``--anchor CH-…-N`` is the manual
  fallback.
"""
from __future__ import annotations

import pytest

from api import chat_turn_row
from api.subagents import fleet, service, store
from tests.subagents.test_subagents import _runner_factory, _seed


@pytest.fixture(autouse=True)
def _fleet_on(monkeypatch):
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: True)
    monkeypatch.delenv("CUTTLE_TURN_ID", raising=False)
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)


def _cli_process(monkeypatch, parent, turn):
    """What an agent CLI process sees while working in ``parent``'s turn."""
    chat_turn_row.reset_for_tests()  # the turn's registry lives in Flask, not here
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(parent))
    monkeypatch.setenv("CUTTLE_TURN_ID", turn)


def _assistant(db, parent):
    return [m for m in db.get_messages(parent, include_in_progress=True) if m["role"] == "assistant"]


# ── export ───────────────────────────────────────────────────────────────────


def test_operation_context_exports_the_turn_id():
    from core.agent_cli_env import agent_cli_env, agent_operation_context

    with agent_operation_context(session_id="7", agent_id="claude", run_id="run1"):
        assert agent_cli_env({})["CUTTLE_TURN_ID"] == "run1"  # a run is its own turn by default
    with agent_operation_context(session_id="7", agent_id="claude", run_id="run2", turn_id="turnA"):
        env = agent_cli_env({})
        assert env["CUTTLE_TURN_ID"] == "turnA" and env["CUTTLE_AGENT_RUN_ID"] == "run2"
    assert "CUTTLE_TURN_ID" not in agent_cli_env({})


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_every_harness_cli_gets_the_turn_id(monkeypatch, tmp_path, agent):
    """Real kernel + real adapter (execute faked): the CLI env names the turn's row id."""
    from api import web_chat_api as wca
    from api.auth_db import AuthDatabase
    from tests.chat.test_turn_row import Gate

    db = AuthDatabase(tmp_path / "turn_id_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    for target in ("api.auth_api.get_auth_db", "api.auth_db.get_auth_db"):
        monkeypatch.setattr(target, lambda: db)
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr("api.chat_titler.schedule_session_autoname", lambda *a, **k: None)
    sid = db.create_chat_session(owner)
    gate = Gate("done").install(monkeypatch, agent, db)
    seen = {}
    inner = gate.release

    from core.agent_cli_env import agent_cli_env
    from api.agent_harness import catalog
    _manifest, adapter = catalog.get_agent(agent)
    real_execute = adapter.execute

    async def execute(prompt, **kwargs):
        seen["env"] = agent_cli_env({})
        return await real_execute(prompt, **kwargs)

    monkeypatch.setattr(adapter, "execute", execute)
    inner.set()
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    client.post("/api/chat", json={"message": f"/{agent} hi", "session_id": sid, "stream": False})
    [row] = _assistant(db, sid)
    assert seen["env"]["CUTTLE_TURN_ID"] == row["metadata"]["turn_id"] == row["metadata"]["query_id"]
    assert seen["env"]["CUTTLE_CHAT_SESSION_ID"] == str(sid)


# ── stamping on every write path ─────────────────────────────────────────────


def test_every_assistant_write_is_stamped_with_its_turn(tmp_path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    a = db.add_message(parent, "assistant", "kernel reply", metadata={"query_id": "q1"})
    b = db.add_message_once(parent, "recovered", delivery_key="k1", metadata={"query_id": "q2"})
    _cli_process(monkeypatch, parent, "turnC")
    c = db.add_message(parent, "assistant", "written by a CLI inside turn C", metadata=None)
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", "999")  # working for another chat
    d = db.add_message(parent, "assistant", "not this chat's turn", metadata={"kind": "x"})
    u = db.add_message(parent, "user", "users are not turns", metadata=None)
    rows = {m["id"]: m for m in db.get_messages(parent)}
    assert rows[a]["metadata"]["turn_id"] == "q1"
    assert rows[b]["metadata"]["turn_id"] == "q2"
    assert rows[c]["metadata"]["turn_id"] == "turnC"
    assert "turn_id" not in rows[d]["metadata"]
    assert rows[u]["metadata"] is None
    # An in-place rewrite (card lock, supervised update, …) never loses the anchor.
    db.update_message_content(a, "kernel reply (locked)", metadata={"action_form_result": {"locked": True}})
    assert db.get_messages(parent)[1]["metadata"]["turn_id"] == "q1"


def test_turn_row_and_its_reply_carry_the_turn_id(tmp_path):
    db, _p, _o, parent = _seed(tmp_path)
    row = chat_turn_row.open_row(db, parent, turn=1)
    [live] = db.in_progress_messages(parent)
    assert live["metadata"]["turn_id"] == row["query_id"]
    # A router retry reports another query id: the row stays anchored to its turn.
    chat_turn_row.finalize(db, parent, "reply", {"query_id": "retry999"})
    [saved] = _assistant(db, parent)
    assert saved["metadata"]["turn_id"] == row["query_id"]
    assert saved["metadata"]["query_id"] == "retry999"


# ── spawn / message bind by turn ─────────────────────────────────────────────


def test_spawn_from_a_turns_cli_binds_to_that_turns_row(tmp_path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    row = chat_turn_row.open_row(db, parent, turn=1)
    _cli_process(monkeypatch, parent, row["query_id"])
    service.spawn(parent_session_id=parent, children=[{"title": "Scout", "message": "go"}],
                  wait=True, timeout=6, runner=_runner_factory(), db=db)
    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.turn_id == row["query_id"] and batch.attach_message_id == row["message_id"]
    [entry] = service.public_parent_subagents(parent, db=db)
    assert entry["turn_id"] == row["query_id"] and entry["bound_message_id"] == row["message_id"]


def test_cards_wait_for_their_turns_row_and_another_turn_never_takes_them(tmp_path, monkeypatch):
    """No row for the turn yet: unbound (live strip). A reply of ANOTHER turn
    leaves them alone; the row of their own turn binds them whatever path saves it."""
    db, _p, _o, parent = _seed(tmp_path)
    _cli_process(monkeypatch, parent, "turnA")
    service.spawn(parent_session_id=parent, children=[{"title": "Scout", "message": "go"}],
                  wait=False, runner=_runner_factory(), db=db)
    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.turn_id == "turnA" and batch.attach_message_id is None
    monkeypatch.delenv("CUTTLE_TURN_ID")
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID")

    # Another turn's reply, through the real saver: it must not grab turn A's cards.
    from api.chat_turn_persist import make_assistant_saver

    saver = make_assistant_saver(
        chat_session_id=parent, db=db, resolve_project=lambda d: "",
        assistant_meta_fn=lambda r: {"query_id": r.get("query_id")},
        project_merge_fn=lambda meta, *a: meta, schedule_autoname=lambda *a: None, project_root=tmp_path)
    saver({"success": True, "response": "turn B's answer", "query_id": "turnB"})
    other = _assistant(db, parent)[-1]
    assert other["metadata"]["turn_id"] == "turnB" and not other["metadata"].get("subagents")
    assert store.list_batches_for_parent(db, parent)[0].attach_message_id is None

    # Turn A's row lands through some other path (supervised delivery, a hook, …).
    mid = db.add_message(parent, "assistant", "turn A's answer", metadata={"turn_id": "turnA"})
    assert store.list_batches_for_parent(db, parent)[0].attach_message_id == mid
    row = next(m for m in _assistant(db, parent) if m["id"] == mid)
    assert [c["label"] for c in row["metadata"]["subagents"]] == ["Scout"]


def test_follow_up_round_binds_to_the_turn_that_sent_it(tmp_path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    first = chat_turn_row.open_row(db, parent, turn=1)
    service.spawn(parent_session_id=parent, children=[{"title": "Scout", "message": "go"}],
                  wait=True, timeout=6, runner=_runner_factory(), db=db)
    chat_turn_row.finalize(db, parent, "round one done", {"query_id": first["query_id"]})
    [child] = store.list_batches_for_parent(db, parent)[0].children

    second = chat_turn_row.open_row(db, parent, turn=2)
    _cli_process(monkeypatch, parent, second["query_id"])
    service.message_child(child.session_id, "and now the tests", wait=True, timeout=6,
                          runner=_runner_factory(), db=db)
    again = store.get_child(db, child.id)
    assert again.card_turn_id == second["query_id"]
    assert again.card_message_id == second["message_id"] and not again.card_pending
    first_row = next(m for m in _assistant(db, parent) if m["id"] == first["message_id"])
    assert [c.get("frozen") for c in first_row["metadata"]["subagents"]] == [True]  # round one kept


def test_anchor_is_the_manual_fallback(tmp_path):
    from api.subagents.cli import _anchor_message_id

    db, _p, _o, parent = _seed(tmp_path)  # user row = bubble 1
    mid = db.add_message(parent, "assistant", "earlier answer", metadata=None)  # bubble 2
    assert _anchor_message_id(db, f"CH-{parent:06d}-2", parent) == mid
    with pytest.raises(ValueError, match="not an assistant"):
        _anchor_message_id(db, f"CH-{parent:06d}-1", parent)
    with pytest.raises(ValueError, match="not a bubble of the parent"):
        _anchor_message_id(db, "CH-000999-2", parent)
    service.spawn(parent_session_id=parent, children=[{"title": "Scout", "message": "go"}],
                  wait=True, timeout=6, runner=_runner_factory(), db=db, anchor_message_id=mid)
    [batch] = store.list_batches_for_parent(db, parent)
    assert batch.attach_message_id == mid and batch.turn_id == ""
    row = next(m for m in _assistant(db, parent) if m["id"] == mid)
    assert [c["label"] for c in row["metadata"]["subagents"]] == ["Scout"]


def test_child_turn_rows_carry_the_childs_own_turn(tmp_path, monkeypatch):
    """A sub-agent's reply row is anchored to the child's turn, not the parent's."""
    db, _p, _o, parent = _seed(tmp_path)
    _cli_process(monkeypatch, parent, "parentTurn")
    service.spawn(parent_session_id=parent, children=[{"title": "Scout", "message": "go"}],
                  wait=True, timeout=6, runner=_runner_factory(), db=db)
    [child] = store.list_batches_for_parent(db, parent)[0].children
    [reply] = _assistant(db, child.session_id)
    assert reply["metadata"]["turn_id"] and reply["metadata"]["turn_id"] != "parentTurn"
