"""Explicit Tasks assignment: child ``task`` refs, spawn/finish lifecycle, stale-child guard."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.gizmos import tasks, tasks_model as model
from api.subagents import extras, service, store
from api.subagents.service import SubagentError
from api.subagents.spec import parse_child, parse_task_ref


def _seed(tmp_path: Path, monkeypatch):
    import api.auth_db as auth

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    monkeypatch.setattr(auth, "_db_instance", db)
    monkeypatch.setenv("CUTTLE_GIZMOS_DB", str(tmp_path / "gizmos.db"))
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    parent = db.create_chat_session(owner, "Planner")
    db.set_session_project(parent, owner, project_path="/projects/a")
    tasks.create(gizmo_id="plan", session_id=parent, user_id=owner, db=db,
                 items=[{"id": "a", "text": "API"}, {"id": "b", "text": "UI"}])
    return db, owner, parent


def _gated_runner(gates: dict, failing: frozenset = frozenset()):
    def runner(**kwargs):
        spec = kwargs["spec"]
        gate = gates.get(spec.title)
        if gate is not None:
            gate.wait(5)
        if spec.title in failing:
            return {"success": False, "error": "boom", "response": ""}
        return {"success": True, "response": f"done {spec.title}"}

    return runner


def _child(db, batch_id: str, title: str):
    batch = store.get_batch(db, batch_id)
    return next(c for c in batch.children if c.label == title)


def _wait_child(db, batch_id: str, title: str, statuses, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        child = _child(db, batch_id, title)
        if child.status in statuses:
            return child
        time.sleep(0.02)
    raise AssertionError(f"{title} never reached {statuses}")


def _item(db, owner, item_id, gizmo="plan"):
    row = tasks.get(gizmo, user_id=owner, db=db)
    return next(i for i in row["payload"]["items"] if i["id"] == item_id)


def _spawn(db, parent, children, runner, **kw):
    kw.setdefault("collect", "all")
    kw.setdefault("wait", False)
    kw.setdefault("timeout", 5)
    return service.spawn(parent_session_id=parent, children=children, runner=runner, db=db, **kw)


# --- spec / parsing ------------------------------------------------------------

def test_task_ref_parses_and_is_validated():
    assert parse_task_ref("plan:t1") == ("plan", "t1")
    assert parse_task_ref(" tasks-ab12.cd:1.2 ") == ("tasks-ab12.cd", "1.2")
    for bad in ("plan", ":t1", "plan:", "pl an:t1", "plan:t 1", "a:b:c", "x" * 129 + ":a"):
        with pytest.raises(ValueError):
            parse_task_ref(bad)


def test_child_json_task_key_is_parsed_and_validated():
    spec = parse_child({"title": "Chef A", "message": "eggs", "task": "plan:a"})
    assert spec.task == "plan:a"
    assert parse_child({"title": "Chef A", "message": "eggs"}).task == ""
    with pytest.raises(ValueError, match='task must be'):
        parse_child({"title": "Chef A", "message": "eggs", "task": "plan"})


# --- preflight -----------------------------------------------------------------

def test_preflight_rejects_unknown_or_done_items_before_any_chat_exists(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    tasks.patch("plan", {"set_done": ["b"]}, user_id=owner, session_id=parent, db=db)

    def batches():
        conn = db._get_connection()
        try:
            return conn.execute("SELECT COUNT(*) FROM subagent_batches").fetchone()[0]
        finally:
            conn.close()

    before = batches()
    for task, message in (("nope:a", "no Tasks list"), ("plan:zz", "no item"), ("plan:b", "already done")):
        with pytest.raises(SubagentError, match=message):
            _spawn(db, parent, [{"title": "Chef A", "message": "x", "task": task}], _gated_runner({}))
    assert batches() == before


# --- spawn / finish lifecycle --------------------------------------------------

def test_spawn_assigns_owner_and_in_progress_then_done_checks_the_item_off(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    gate = threading.Event()
    spawned = _spawn(db, parent, [{"title": "Chef A", "message": "x", "task": "plan:a"}],
                     _gated_runner({"Chef A": gate}))
    child = _child(db, spawned["id"], "Chef A")
    assert (child.task_gizmo_id, child.task_item_id) == ("plan", "a")
    node = _item(db, owner, "a")
    assert node["owner"] == {"kind": "chat", "id": child.handle(), "label": "Chef A", "agent": "cursor"}
    assert node["in_progress"] is True and not node["done"]
    gate.set()
    _wait_child(db, spawned["id"], "Chef A", {"done"})
    node = _item(db, owner, "a")
    assert node["done"] is True
    assert "in_progress" not in node
    assert node["owner"]["id"] == child.handle()  # the owner stays on the record


def test_failed_child_releases_the_item_but_keeps_owner_and_leaves_it_open(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    spawned = _spawn(db, parent, [{"title": "Chef A", "message": "x", "task": "plan:a"}],
                     _gated_runner({}, failing=frozenset({"Chef A"})))
    _wait_child(db, spawned["id"], "Chef A", {"failed"})
    node = _item(db, owner, "a")
    assert node["done"] is False
    assert "in_progress" not in node
    assert node["owner"]["id"] == _child(db, spawned["id"], "Chef A").handle()


def test_stale_child_cannot_finish_an_item_reassigned_to_a_newer_child(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    gate_old, gate_new = threading.Event(), threading.Event()
    gates = {"Old": gate_old, "New": gate_new}
    first = _spawn(db, parent, [{"title": "Old", "message": "x", "task": "plan:a"}], _gated_runner(gates))
    old_child = _child(db, first["id"], "Old")
    second = _spawn(db, parent, [{"title": "New", "message": "y", "task": "plan:a"}], _gated_runner(gates))
    new_child = _child(db, second["id"], "New")
    node = _item(db, owner, "a")
    assert node["owner"]["id"] == new_child.handle()  # reassigned at spawn
    gate_old.set()
    _wait_child(db, first["id"], "Old", {"done"})
    node = _item(db, owner, "a")
    assert node["owner"]["id"] == new_child.handle()
    assert node["done"] is False and node["in_progress"] is True
    assert old_child.handle() != new_child.handle()
    gate_new.set()
    _wait_child(db, second["id"], "New", {"done"})
    node = _item(db, owner, "a")
    assert node["done"] is True and node["owner"]["id"] == new_child.handle()


def test_done_write_that_loses_to_a_stop_leaves_the_item_open(tmp_path, monkeypatch):
    """The item settles before the guarded status write. When a Stop lands in between,
    the child stays cancelled and its item must not stay checked off."""
    db, owner, parent = _seed(tmp_path, monkeypatch)
    gate = threading.Event()
    spawned = _spawn(db, parent, [{"title": "Chef A", "message": "x", "task": "plan:a"}],
                     _gated_runner({"Chef A": gate}))
    child = _child(db, spawned["id"], "Chef A")
    real_settle = extras.settle_child_task

    def settle_then_stop(db_, child_id, status):
        real_settle(db_, child_id, status)
        if status == "done":  # the Stop commits after the check, before the done write
            conn = db_._get_connection()
            conn.execute("UPDATE subagent_children SET status = 'cancelled' WHERE id = ?", (child_id,))
            conn.commit()
            conn.close()

    monkeypatch.setattr(extras, "settle_child_task", settle_then_stop)
    store.update_child(db, child.id, status="done", finished=True)
    gate.set()
    assert _child(db, spawned["id"], "Chef A").status == "cancelled"
    node = _item(db, owner, "a")
    assert node["done"] is False
    assert node["owner"]["id"] == child.handle()


def test_settle_is_a_no_op_for_children_without_an_assignment(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    spawned = _spawn(db, parent, [{"title": "Plain", "message": "x"}], _gated_runner({}))
    _wait_child(db, spawned["id"], "Plain", {"done"})
    row = tasks.get("plan", user_id=owner, db=db)
    assert all("owner" not in i and "in_progress" not in i for i in row["payload"]["items"])


# --- batch list mirror is incremental -----------------------------------------

def test_parent_tasks_mirror_is_incremental_and_keeps_user_items(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    spawned = _spawn(db, parent, [{"title": "Chef A", "message": "x"}, {"title": "Chef B", "message": "y"}],
                     _gated_runner({}), tasks=True, wait=True)
    batch = store.get_batch(db, spawned["id"])
    widget = batch.widget_id
    listed = tasks.get(widget, user_id=owner, db=db)["payload"]["items"]
    assert len(listed) == 2 and all(i["done"] and i["owner"]["kind"] == "chat" for i in listed)
    tasks.patch(widget, {"add": [{"id": "mine", "text": "Ship it", "owner": {"kind": "user", "label": "You"}}]},
                user_id=owner, session_id=parent, db=db)
    tasks.patch(widget, {"set_text": {listed[0]["id"]: "stale text"}}, user_id=owner, session_id=parent, db=db)
    extras.ensure_parent_tasks(db, store.get_batch(db, spawned["id"]))
    items = {i["id"]: i for i in tasks.get(widget, user_id=owner, db=db)["payload"]["items"]}
    assert "mine" in items and items["mine"]["owner"] == {"kind": "user", "label": "You"}
    assert len(items) == 3
    assert items[listed[0]["id"]]["text"] == listed[0]["text"]  # the mirror repairs its own item text


def test_ensure_parent_tasks_sets_owner_and_in_progress_for_a_running_child(tmp_path, monkeypatch):
    db, owner, parent = _seed(tmp_path, monkeypatch)
    gate = threading.Event()
    spawned = _spawn(db, parent, [{"title": "Chef A", "message": "x"}], _gated_runner({"Chef A": gate}),
                     tasks=True)
    child = _child(db, spawned["id"], "Chef A")
    item = _item(db, owner, child.id[:12], gizmo=store.get_batch(db, spawned["id"]).widget_id)
    assert item["in_progress"] is True and item["owner"]["id"] == child.handle()
    gate.set()
    _wait_child(db, spawned["id"], "Chef A", {"done"})
    extras.ensure_parent_tasks(db, store.get_batch(db, spawned["id"]))
    item = _item(db, owner, child.id[:12], gizmo=store.get_batch(db, spawned["id"]).widget_id)
    assert item["done"] is True and "in_progress" not in item
