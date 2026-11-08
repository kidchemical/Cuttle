"""Event log written by the owners' own code paths, and the dashboard report over it."""

from __future__ import annotations

import pytest

from api.experimental import flags
from api.orchestration_telemetry import record, report, store
from api.subagents import store as sub_store
from api.subagents.types import ChildSpec


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from api.auth_db import AuthDatabase
    from api.orchestrator import service as orch, store as orch_store
    from api.subagents.profiles import save_profile

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(orch_store, "_db_path", lambda: tmp_path / "orchestrator.db")
    flags.set_enabled("orchestrator", True)
    db = AuthDatabase(tmp_path / "auth.db")
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    for pid, agent, mdl in (("grunt", "codex", "gpt-6-luna"), ("grunt-haiku", "claude", "haiku"),
                            ("escalate", "codex", "gpt-6.1-sol"), ("expert", "claude", "claude-opus-5-5")):
        save_profile(db, owner, profile_id=pid, name=pid, agent=agent, model=mdl, effort="")
    orch.save_playbook(owner, {"id": "pb", "name": "PB", "tiers": [
        {"id": "w", "role": "worker", "members": ["grunt", "grunt-haiku"]},
        {"id": "e", "role": "escalation", "members": ["escalate"]},
        {"id": "x", "role": "expert", "members": ["expert"]}]}, db=db)
    parent = db.create_chat_session(owner, "orchestrator")
    db.set_session_project(parent, owner, project_id=1, project_name="Game", project_path="/games/x")
    orch.bind(parent, "pb", "ship it", db=db)
    yield db, owner, parent
    flags.set_enabled("orchestrator", False)


def _spawn(db, owner, parent, profile, agent, model, *, turn="t1"):
    batch = sub_store.insert_batch(db, parent_session_id=parent, user_id=owner, collect="all",
                                   lifetime="one_shot", turn_id=turn)
    sid = db.create_chat_session(owner, profile)
    child = sub_store.insert_child(db, batch_id=batch.id, session_id=sid, sort_index=0,
                                   spec=ChildSpec(title=profile, message="go", agent=agent, model=model,
                                                  profile_id=profile), prompt="go")
    return child


def _run(db, child, status, error=""):
    sub_store.update_child(db, child.id, status="running", started=True)
    sub_store.update_child(db, child.id, status=status, error=error or None, finished=True)


def test_spawns_and_rounds_carry_tier_playbook_and_escalation_source(world):
    db, owner, parent = world
    luna = _spawn(db, owner, parent, "grunt", "codex", "gpt-6-luna")
    _run(db, luna, "failed", error="tests still red")
    sol = _spawn(db, owner, parent, "escalate", "codex", "gpt-6.1-sol")
    _run(db, sol, "done")
    spawns = store.events(kinds=["child_spawn"])
    assert [(e["role"], e["tier"], e["playbook_id"], e["project_path"]) for e in spawns] == [
        ("worker", "w", "pb", "/games/x"), ("escalation", "e", "pb", "/games/x")]
    assert spawns[1]["detail"]["link"] == "failed_worker" and spawns[1]["detail"]["from_model"] == "gpt-6-luna"
    ends = store.events(kinds=["child_round_end"])
    assert [(e["status"], e["role"]) for e in ends] == [("failed", "worker"), ("done", "escalation")]
    assert ends[0]["detail"]["error"] == "tests still red" and ends[1]["duration_s"] is not None
    assert len(store.events(kinds=["child_round_start"])) == 2


def test_unbound_parent_spawns_are_untiered_and_recorders_never_raise(world, monkeypatch):
    db, owner, _parent = world
    other = db.create_chat_session(owner, "plain chat")
    _spawn(db, owner, other, "grunt", "codex", "gpt-6-luna")
    assert store.events(kinds=["child_spawn"])[-1]["role"] == ""
    monkeypatch.setattr(store, "append", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    _spawn(db, owner, other, "grunt", "codex", "gpt-6-luna")  # a broken log never breaks a spawn


def test_report_answers_tier_model_and_escalation_questions(world, monkeypatch):
    db, owner, parent = world
    import json
    # The orchestrator's own turn: its assistant row names its harness and model.
    db.add_message(parent, "assistant", "plan", metadata={"turn_id": "t1", "slash_command": {"chips": [
        {"label": "Codex", "meta": "/codex · model gpt-6.1-terra · effort high", "category": "codex"}]}})
    for profile, agent, model, status in (("grunt", "codex", "gpt-6-luna", "failed"),
                                          ("grunt", "codex", "gpt-6-luna", "done"),
                                          ("grunt-haiku", "claude", "haiku", "done")):
        _run(db, _spawn(db, owner, parent, profile, agent, model), status)
    _run(db, _spawn(db, owner, parent, "escalate", "codex", "gpt-6.1-sol"), "done")
    out = report.report(user_id=owner, range_id="all", db=db)
    s = out["stats"]
    assert s["spawns"] == 4 and s["escalations"] == 1 and s["orchestrated_chats"] == 1 and s["failed_runs"] == 1
    rows = {(r["role"], r["label"]): r for r in out["by_tier"]}
    luna = rows[("worker", "codex · gpt-6-luna")]
    assert luna["runs"] == 2 and luna["fail_pct"] == 50.0
    assert rows[("escalation", "codex · gpt-6.1-sol")]["spawns"] == 1
    assert [o["label"] for o in out["orchestrators"]] == ["codex · gpt-6.1-terra"]
    assert out["orchestrators"][0]["escalations_per_chat"] == 1.0
    assert out["escalations"] == [{"project": "/games/x", "target": "codex · gpt-6.1-sol", "count": 1}]
    assert out["failures"][0]["label"] == "codex · gpt-6-luna"
    assert report.report(user_id=owner, range_id="all", project="/elsewhere", db=db)["stats"]["spawns"] == 0
    json.dumps(out)  # the route jsonifies it


def test_supervision_checks_actions_and_lifecycle_are_logged(world):
    from api.chat_ops import service as ops, store as ops_store

    db, owner, parent = world
    sup = db.create_chat_session(owner, "supervisor")
    op = ops.supervise(parent, sup, instructions="watch", db=db)["operation"]
    ops_store.record_check(op["id"], "woke", {"state": "idle_with_work"})
    ops_store.set_paused(op["id"], sup, True)
    ops_store.set_paused(op["id"], sup, False)
    record.supervisor_action("sup_send", sup, parent, chars=12)
    record.supervisor_action("sup_send", parent, sup, chars=1)  # not a supervisor of sup: ignored
    ops_store.cancel(op["id"], sup)
    sched = [e["status"] for e in store.events(kinds=["sup_schedule"])]
    assert sched == ["start", "pause", "resume", "cancel"]
    chk = store.events(kinds=["sup_check"])
    assert [(c["status"], c["detail"]["state"], c["parent_session_id"]) for c in chk] == [
        ("woke", "idle_with_work", parent)]
    assert len(store.events(kinds=["sup_send"])) == 1


def test_backfill_imports_children_and_checks_once(world):
    db, owner, parent = world
    child = _spawn(db, owner, parent, "grunt", "codex", "gpt-6-luna")
    _run(db, child, "done")
    import sqlite3
    sqlite3.connect(store.db_path()).execute("DELETE FROM events").connection.commit()
    assert report.backfill(db, dry_run=True)["spawns"] == 1 and not store.events()
    counts = report.backfill(db)
    assert counts["spawns"] == 1 and counts["rounds"] == 1
    assert [e["role"] for e in store.events(kinds=["child_spawn"])] == ["worker"]
    assert report.backfill(db) == {"skipped": 1}
    assert report.report(user_id=owner, range_id="all", db=db)["backfilled"] is True
