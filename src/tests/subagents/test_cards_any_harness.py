"""Sub-agent cards attach to the turn that launched them, whichever agent ran it.

A real ``/api/chat`` turn runs under each harness adapter (the real kernel and
adapter; only ``execute`` is faked). Inside the turn, the "agent" runs the
sub-agent command the way any agent does: a separate process with the agent's
own environment (``agent_cli_env``). Nothing in the flow knows which harness it
is:

- the agent process sees ``CUTTLE_TURN_ID`` and the user bubble that started
  the turn (``CUTTLE_TURN_MESSAGE`` / ``CUTTLE_TURN_MESSAGE_ID``);
- ``spawn`` records that turn on the batch, and the card is live on that
  turn's in-progress bubble before the turn ends;
- the reply that turn saves carries the card (matched by turn id); the next
  turn's reply does not.

The no-env fallback (manual CLI run) is covered in ``test_fleet_live_binding``
and ``tests/chat/test_turn_id.py`` (``--anchor``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from api import web_chat_api as wca
from api.agent_harness.types import AgentResult
from api.subagents import fleet, service, store

AGENTS = ("claude", "codex", "cursor", "opencode")
SRC = Path(__file__).resolve().parents[2]

_SPAWN = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    from api.auth_db import AuthDatabase
    from api.subagents import service

    db = AuthDatabase(Path(sys.argv[1]))
    out = service.spawn(
        parent_session_id=int(os.environ["CUTTLE_CHAT_SESSION_ID"]),
        children=[{"title": "Scout", "message": "look around"}], wait=True, timeout=20,
        runner=lambda **kw: {"success": True, "response": "Scout found it"}, db=db)
    print(json.dumps({"batch": out["id"], "turn_id": out.get("turn_id"),
                      "env": {k: os.environ.get(k) for k in (
                          "CUTTLE_TURN_ID", "CUTTLE_TURN_MESSAGE", "CUTTLE_TURN_MESSAGE_ID",
                          "CUTTLE_CHAT_SESSION_ID")}}))
""")


@pytest.fixture
def authed(monkeypatch, tmp_path):
    from api.auth_db import AuthDatabase

    path = tmp_path / "any_harness_auth.db"
    db = AuthDatabase(path)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr("api.chat_titler.schedule_session_autoname", lambda *a, **k: None)
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: True)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client, db, owner, path


def _install(monkeypatch, agent, db, db_path, seen):
    """The real adapter, whose 'agent' runs `spawn` as its own process mid-turn."""
    from api.agent_harness import catalog, kernel
    from core.agent_cli_env import agent_cli_env

    manifest, adapter = catalog.get_agent(agent)

    async def execute(prompt, **kwargs):
        env = {**agent_cli_env(), "PYTHONPATH": str(SRC)}
        proc = subprocess.run([sys.executable, "-c", _SPAWN, str(db_path)], capture_output=True,
                              text=True, timeout=60, env=env, cwd=str(SRC))
        seen["proc"] = proc
        if proc.returncode == 0:
            seen["spawn"] = json.loads(proc.stdout.strip().splitlines()[-1])
            sid = int(seen["spawn"]["env"]["CUTTLE_CHAT_SESSION_ID"])
            # Mid-turn: the card is live, bound to this turn's in-progress bubble.
            seen["live"] = service.public_parent_subagents(sid, db=db)
            seen["live_rows"] = db.in_progress_messages(sid)
        return AgentResult(success=True, output=f"{agent}: spawned Scout")

    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "execute", execute)
    monkeypatch.setattr(kernel, "get_agent", lambda aid, project_path=None: (manifest, adapter))


@pytest.mark.parametrize("agent", AGENTS)
def test_card_attaches_to_the_turn_that_launched_it_for_any_agent(authed, monkeypatch, agent):
    client, db, owner, db_path = authed
    sid = db.create_chat_session(owner)
    seen = {}
    _install(monkeypatch, agent, db, db_path, seen)

    res = client.post("/api/chat", json={"message": f"/{agent} send a scout", "session_id": sid,
                                         "stream": False})
    assert res.status_code == 200, res.get_json()
    proc = seen["proc"]
    assert proc.returncode == 0, proc.stderr[-3000:]

    [reply] = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
    turn = reply["metadata"]["turn_id"]
    user = [m for m in db.get_messages(sid) if m["role"] == "user"][0]
    env = seen["spawn"]["env"]
    # The agent's process knew its turn and the bubble that started it.
    assert env["CUTTLE_TURN_ID"] == turn == seen["spawn"]["turn_id"]
    assert env["CUTTLE_TURN_MESSAGE_ID"] == str(user["id"])
    assert env["CUTTLE_TURN_MESSAGE"] == f"CH-{sid:06d}-1"
    # Live during the turn, on that turn's bubble.
    [live_row] = seen["live_rows"]
    [entry] = seen["live"]
    assert entry["turn_id"] == turn and entry["bound_message_id"] == live_row["id"] == reply["id"]
    # The reply that turn saved carries the card, once.
    assert [c["label"] for c in reply["metadata"]["subagents"]] == ["Scout"]
    [batch] = store.list_batches_for_parent(db, sid)
    assert batch.turn_id == turn and batch.attach_message_id == reply["id"]

    # The next turn's reply never takes it.
    _install_plain(monkeypatch, agent)
    client.post("/api/chat", json={"message": f"/{agent} thanks", "session_id": sid, "stream": False})
    nxt = [m for m in db.get_messages(sid) if m["role"] == "assistant"][-1]
    assert nxt["id"] != reply["id"] and nxt["metadata"]["turn_id"] != turn
    assert not nxt["metadata"].get("subagents")


def _install_plain(monkeypatch, agent):
    from api.agent_harness import catalog, kernel

    manifest, adapter = catalog.get_agent(agent)

    async def execute(prompt, **kwargs):
        return AgentResult(success=True, output="you're welcome")

    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "execute", execute)
    monkeypatch.setattr(kernel, "get_agent", lambda aid, project_path=None: (manifest, adapter))


def test_no_turn_env_manual_run_uses_the_anchor_else_the_latest_bubble(authed, tmp_path):
    """The same spawn process with no turn env (a terminal): --anchor, else latest bubble."""
    _client, db, owner, db_path = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "hi", metadata=None)
    first = db.add_message(sid, "assistant", "first", metadata=None)
    latest = db.add_message(sid, "assistant", "second", metadata=None)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CUTTLE_TURN")}
    env.update(PYTHONPATH=str(SRC), CUTTLE_CHAT_SESSION_ID=str(sid))
    proc = subprocess.run([sys.executable, "-c", _SPAWN, str(db_path)], capture_output=True, text=True,
                          timeout=60, env=env, cwd=str(SRC))
    assert proc.returncode == 0, proc.stderr[-3000:]
    [batch] = store.list_batches_for_parent(db, sid)
    assert batch.turn_id == "" and batch.attach_message_id == latest

    from api.subagents.cli import _anchor_message_id

    anchored = service.spawn(parent_session_id=sid, children=[{"title": "Pinned", "message": "x"}],
                             wait=True, timeout=10, runner=lambda **kw: {"success": True, "response": "ok"},
                             db=db, anchor_message_id=_anchor_message_id(db, f"CH-{sid:06d}-2", sid))
    assert store.get_batch(db, anchored["id"]).attach_message_id == first


# ── the CH-001822 shapes ─────────────────────────────────────────────────────


def _saver(db, sid, tmp_path):
    from api.chat_turn_persist import make_assistant_saver

    return make_assistant_saver(
        chat_session_id=sid, db=db, resolve_project=lambda d: "",
        assistant_meta_fn=lambda r: {"query_id": r.get("query_id")},
        project_merge_fn=lambda meta, *a: meta, schedule_autoname=lambda *a: None, project_root=tmp_path)


def test_spawn_after_the_turns_first_reply_binds_to_the_next_reply_of_that_turn(authed, monkeypatch, tmp_path):
    """A Claude-style detached run: the turn's first reply is saved, the agent keeps
    working and spawns from its background phase. The card goes on the first reply
    saved AFTER the spawn (that turn's follow-up bubble), never back onto the earlier one."""
    from api import chat_turn_row
    from api.agent_harness.background_wait import BackgroundWait

    _client, db, owner, _path = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "/claude send a scout", metadata=None)
    row = chat_turn_row.open_row(db, sid, turn=1)
    saver = _saver(db, sid, tmp_path)
    saver({"success": True, "response": "first reply", "query_id": row["query_id"]})
    first = row["message_id"]

    wait = BackgroundWait(sid, "claude")
    wait.start(["background work"], pid=None)
    wait.bind(saver, {"query_id": row["query_id"], "success": True})
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))
    monkeypatch.setenv("CUTTLE_TURN_ID", row["query_id"])
    chat_turn_row.reset_for_tests()  # the agent's CLI process: no Flask registry
    service.spawn(parent_session_id=sid, children=[{"title": "Scout", "message": "go"}], wait=False,
                  runner=lambda **kw: {"success": True, "response": "found"}, db=db)
    [batch] = store.list_batches_for_parent(db, sid)
    assert batch.turn_id == row["query_id"] and batch.attach_message_id is None  # not back onto `first`
    [entry] = service.public_parent_subagents(sid, db=db)
    assert entry["bound_message_id"] is None and entry["turn_id"] == row["query_id"]  # pinned card rows

    wait.working("answering")  # the same turn's next bubble
    wait.followup("the scout is out")
    rows = [m for m in db.get_messages(sid) if m["role"] == "assistant"]
    assert [r["id"] for r in rows][0] == first
    follow = rows[-1]
    assert follow["id"] != first and follow["metadata"]["turn_id"] == row["query_id"]
    assert store.list_batches_for_parent(db, sid)[0].attach_message_id == follow["id"]
    assert [c["label"] for c in follow["metadata"]["subagents"]] == ["Scout"]
    assert not rows[0]["metadata"].get("subagents")
    wait.finish("done", "finished")


def test_chat_cli_send_to_a_child_gets_a_card_round(authed, monkeypatch, tmp_path):
    from api import chat_turn_row
    from api.chat_ops import service as ops

    _client, db, owner, _path = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "/codex review", metadata=None)
    first = chat_turn_row.open_row(db, sid, turn=1)
    service.spawn(parent_session_id=sid, children=[{"title": "Reviewer", "message": "review"}], wait=True,
                  timeout=10, runner=lambda **kw: {"success": True, "response": "round one ok"}, db=db)
    _saver(db, sid, tmp_path)({"success": True, "response": "spawned", "query_id": first["query_id"]})
    [child] = store.list_batches_for_parent(db, sid)[0].children

    db.add_message(sid, "user", "/codex ask the reviewer again", metadata=None)
    second = chat_turn_row.open_row(db, sid, turn=2)
    host = lambda uid, s, method, path, data=None, stream=False: {"generating": False}  # noqa: E731
    out = ops.send(f"CH-{child.session_id:06d}", "check the tests too", f"CH-{sid:06d}", db=db, request=host)
    assert out["delivery"] == "queued"
    again = store.get_child(db, child.id)
    assert again.status == "running" and again.card_turn_id == second["query_id"]
    assert again.card_message_id == second["message_id"]  # the sending turn's live bubble
    [entry] = service.public_parent_subagents(sid, db=db)
    assert entry["outcome"] == "running" and entry["bound_message_id"] == second["message_id"]

    # The child's turn (run by the ordinary chat ingress) ends with a reply.
    db.add_message(child.session_id, "assistant", "tests pass too", metadata=None)
    assert service.on_child_turns_ended([child.session_id], db=db) == [child.id]
    done = store.get_child(db, child.id)
    assert done.status == "done" and done.result == "tests pass too"
    assert service.on_child_turns_ended([child.session_id], db=db) == []  # once
    first_row = next(m for m in db.get_messages(sid) if m["id"] == first["message_id"])
    assert [c.get("frozen") for c in first_row["metadata"]["subagents"]] == [True]  # round one kept
