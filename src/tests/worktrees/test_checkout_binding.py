"""W2 checkout binding: resolver, authorization, subagent/supervised inheritance."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.worktrees import binding, registry


def _db(tmp_path) -> Path:
    return tmp_path / "projects.db"


def _make(tmp_path, db, *, uid, project_id=1, state="ready"):
    path = tmp_path / "home" / "worktrees" / "demo" / uid
    path.mkdir(parents=True, exist_ok=True)
    return registry.create_checkout(
        project_id, str(path), checkout_uid=uid, branch="cuttle/x",
        state=state, db_path_override=db,
    )


def test_resolve_bound_checkout_path(tmp_path):
    db = _db(tmp_path)
    checkout = _make(tmp_path, db, uid="wt", project_id=1)
    assert binding.resolve_bound_checkout_path("wt", 1, db_path_override=db) == checkout.path
    assert binding.resolve_bound_checkout_path("nope", 1, db_path_override=db) is None
    # Foreign project is treated as unusable, never honored.
    assert binding.resolve_bound_checkout_path("wt", 2, db_path_override=db) is None
    _make(tmp_path, db, uid="wt2", project_id=1, state="missing")
    assert binding.resolve_bound_checkout_path("wt2", 1, db_path_override=db) is None


def test_checkout_paths_and_membership(tmp_path):
    db = _db(tmp_path)
    checkout = _make(tmp_path, db, uid="m1", project_id=5)
    paths = binding.checkout_paths_for_project(5, db_path_override=db)
    assert binding._norm(checkout.path) in paths
    assert binding.is_checkout_path_for_project(checkout.path, 5, db_path_override=db)
    assert not binding.is_checkout_path_for_project(checkout.path, 6, db_path_override=db)


def test_resolver_honors_bound_checkout(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from api import worktrees

    checkout = registry.create_checkout(
        3, str(tmp_path / "wt"), checkout_uid="rc1", branch="cuttle/rc"
    )
    monkeypatch.setattr(worktrees, "is_worktrees_enabled", lambda: True)
    assert wca._resolve_request_project_path(
        {"checkout_uid": "rc1", "project_id": 3}
    ) == checkout.path

    # Flag off → the bound checkout is ignored.
    monkeypatch.setattr(worktrees, "is_worktrees_enabled", lambda: False)
    assert wca._resolve_request_project_path({"checkout_uid": "rc1"}) != checkout.path


def test_git_allowlist_accepts_registered_checkout(tmp_path):
    from scripts.utilities.git_pending_changes import resolve_allowed_project_cwd

    primary = tmp_path / "primary"
    primary.mkdir()
    (tmp_path / "wt").mkdir()
    checkout = registry.create_checkout(
        1, str(tmp_path / "wt"), checkout_uid="ga1", branch="cuttle/ga"
    )
    cwd, project, error = resolve_allowed_project_cwd(
        str(checkout.path), None, [{"id": 1, "path": str(primary)}], None
    )
    assert error is None
    assert project and project["id"] == 1
    assert Path(cwd) == Path(checkout.path).resolve()
    # An arbitrary unregistered path is still rejected.
    assert resolve_allowed_project_cwd(
        str(tmp_path / "elsewhere"), None, [{"id": 1, "path": str(primary)}], None
    )[2] is not None


def test_subagent_execution_path_follows_parent_checkout(tmp_path):
    from api.subagents import service

    checkout = registry.create_checkout(
        2, str(tmp_path / "wt"), checkout_uid="sp1", branch="cuttle/sp"
    )
    parent = {"project_path": "/identity/path", "checkout_uid": "sp1", "project_id": 2}
    assert service._execution_path_for_parent(parent) == checkout.path
    # No checkout → the parent's project identity path.
    assert service._execution_path_for_parent({"project_path": "/identity/path"}) == "/identity/path"


def test_auth_session_checkout_uid_roundtrip(tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "auth.db")
    sid = db.create_chat_session(1, "wt-chat")
    assert db.set_session_project(
        sid, 1, project_id=1, project_name="Demo", project_path="/p", checkout_uid="abc"
    ) is True
    assert db.get_session_project(sid)["checkout_uid"] == "abc"
    db.set_session_project(sid, 1, project_id=1, project_name="Demo", project_path="/p")
    assert db.get_session_project(sid)["checkout_uid"] is None


def test_subagent_session_inherits_checkout(tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "auth.db")
    parent = db.create_chat_session(1, "parent")
    child = db.create_subagent_chat_session(
        1, parent_session_id=parent, session_name="child",
        project_id=1, project_path="/p", checkout_uid="abc",
    )
    assert db.get_session_project(child)["checkout_uid"] == "abc"


def test_supervised_task_round_trips_checkout_uid():
    from api.agent_router.supervised.types import SupervisedTask

    task = SupervisedTask(
        task_id="t1",
        parent_session_id="p1",
        coordinator_session_id="c1",
        worker_session_id="w1",
        profile_id="diet-frontier",
        phase="created",
        user_objective="do a thing",
        project_path="/wt",
        checkout_uid="abc",
    )
    payload = task.to_dict()
    assert payload["checkout_uid"] == "abc"
    assert SupervisedTask.from_dict(payload).checkout_uid == "abc"
