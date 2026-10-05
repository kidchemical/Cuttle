"""Tasks HTTP contract: routes, auth, shapes, close-via-commit.

Characterization for the Phase 2 Slice 4 extraction. Pins the transport
boundary regardless of which module registers the routes (monolith before,
``api.task_routes`` after):

- 8 routes registered with identical paths + methods, no duplicates,
- owner-only mutations (401 anon / 403 guest); GET one task is
  unintentionally public (pinned, not fixed here),
- CRUD/comment/close shapes incl. 400/404/201/500 cases,
- close-via-commit orchestration (task fetch → git add/commit in the
  process cwd → status Closed → System comment).

Uses an isolated TaskManager SQLite DB; close-via-commit runs inside a
real tmp git repo via monkeypatched cwd.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAN = {"REMOTE_ADDR": "192.0.2.77"}


def _git(*args, cwd):
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]
    return subprocess.run(base + list(args), cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def _ctx(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca
    from managers.task_manager import TaskManager

    db_path = tmp_path / "tasks-auth.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    isolated = TaskManager(str(tmp_path / "tasks.db"))
    monkeypatch.setattr("managers.task_manager.task_manager", isolated)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    guest = db.create_user("guest@local", "Guest", "local", password="x")
    db.create_chat_session(owner, "o")
    db.create_chat_session(guest, "g")
    owner_c = wca.app.test_client()
    owner_c.set_cookie("session_token", db.create_auth_session(owner))
    guest_c = wca.app.test_client()
    guest_c.set_cookie("session_token", db.create_auth_session(guest))
    return {"owner": owner_c, "guest": guest_c, "anon": wca.app.test_client()}


EXPECTED_ROUTES = {
    ("/api/tasks", ("GET",)),
    ("/api/tasks", ("POST",)),
    ("/api/tasks/<int:task_id>", ("GET",)),
    ("/api/tasks/<int:task_id>", ("PUT",)),
    ("/api/tasks/<int:task_id>", ("DELETE",)),
    ("/api/tasks/<int:task_id>/comments", ("POST",)),
    ("/api/tasks/<int:task_id>/comments/<int:comment_id>", ("DELETE",)),
    ("/api/tasks/<int:task_id>/close-via-commit", ("POST",)),
}


def test_task_routes_registered_once_with_methods():
    from api import web_chat_api as wca

    found = {}
    for rule in wca.app.url_map.iter_rules():
        if str(rule.rule).startswith("/api/tasks"):
            key = (str(rule.rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
            found[key] = found.get(key, 0) + 1
    for path, methods in EXPECTED_ROUTES:
        matches = [k for k in found if k[0] == path and set(methods) <= set(k[1])]
        assert matches, f"missing route {methods} {path}"
        for m in matches:
            assert found[m] == 1, f"duplicate registration {m}"


def test_task_auth_matrix(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    # Mutations: anon 401, guest 403.
    for method, path, payload in [
        ("GET", "/api/tasks", None),
        ("POST", "/api/tasks", {"title": "t"}),
        ("PUT", "/api/tasks/1", {"title": "t"}),
        ("DELETE", "/api/tasks/1", None),
        ("POST", "/api/tasks/1/comments", {"content": "c"}),
        ("DELETE", "/api/tasks/1/comments/1", None),
        ("POST", "/api/tasks/1/close-via-commit", {"message": "m"}),
    ]:
        res = ctx["anon"].open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 401, (method, path, res.status_code)
        res = ctx["guest"].open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 403, (method, path, res.status_code)
    # GET one task carries no decorator (public — pinned, not fixed here).
    res = ctx["anon"].get("/api/tasks/9999", environ_base=LAN)
    assert res.status_code == 404
    assert res.get_json() == {"success": False, "error": "Task not found"}


def test_task_crud_shapes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    o = ctx["owner"]
    res = o.post("/api/tasks", json={}, environ_base=LAN)
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "Title is required"}
    res = o.post("/api/tasks", json={"title": "T", "priority": "High"}, environ_base=LAN)
    assert res.status_code == 201, res.get_json()
    task = res.get_json()
    assert task["title"] == "T" and task["priority"] == "High"
    assert task["status"] == "None" and task["description"] == "" and task["owner"] == ""
    tid = task["id"] if "id" in task else task["taskId"]
    res = o.get(f"/api/tasks/{tid}", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["title"] == "T"
    res = o.put(f"/api/tasks/{tid}", json={}, environ_base=LAN)
    assert res.status_code == 400
    res = o.put(f"/api/tasks/{tid}", json={"title": "T2"}, environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["title"] == "T2"
    res = o.put("/api/tasks/9999", json={"title": "x"}, environ_base=LAN)
    assert res.status_code == 404
    res = o.delete(f"/api/tasks/{tid}", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json() == {"success": True}
    res = o.delete(f"/api/tasks/{tid}", environ_base=LAN)
    assert res.status_code == 404


def test_task_comment_shapes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    o = ctx["owner"]
    tid_body = o.post("/api/tasks", json={"title": "T"}, environ_base=LAN).get_json()
    tid = tid_body.get("id", tid_body.get("taskId"))
    res = o.post(f"/api/tasks/{tid}/comments", json={}, environ_base=LAN)
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "Comment content is required"}
    # add_comment does not verify the task exists (orphan comments allowed
    # at the manager layer) — pinned, not fixed here.
    res = o.post(f"/api/tasks/9999/comments", json={"content": "c"}, environ_base=LAN)
    assert res.status_code == 201
    res = o.post(f"/api/tasks/{tid}/comments", json={"content": "hello"}, environ_base=LAN)
    assert res.status_code == 201, res.get_json()
    cid = res.get_json().get("id", res.get_json().get("commentId"))
    res = o.delete(f"/api/tasks/{tid}/comments/9999", environ_base=LAN)
    assert res.status_code == 404
    assert res.get_json() == {"success": False, "error": "Comment or task not found"}
    res = o.delete(f"/api/tasks/{tid}/comments/{cid}", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_close_via_commit_shapes_and_flow(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    # close-via-commit runs bare `git commit` (no -c flags, no env identity),
    # so the repo needs local identity — pinned as-is.
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "w.txt").write_text("work\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    ctx = _ctx(tmp_path, monkeypatch)
    o = ctx["owner"]
    res = o.post("/api/tasks/9999/close-via-commit", json={"message": "m"}, environ_base=LAN)
    assert res.status_code == 404
    tid_body = o.post("/api/tasks", json={"title": "T"}, environ_base=LAN).get_json()
    tid = tid_body.get("id", tid_body.get("taskId"))
    res = o.post(f"/api/tasks/{tid}/close-via-commit", json={}, environ_base=LAN)
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "Commit message is required"}
    res = o.post(f"/api/tasks/{tid}/close-via-commit",
                 json={"message": "done", "files": "w.txt"}, environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["message"] == "Task closed successfully via commit"
    body = _git("log", "--format=%B", "-1", cwd=repo).stdout
    task = o.get(f"/api/tasks/{tid}", environ_base=LAN).get_json()
    assert "done" in body and f"Closes {task['taskId']}" in body
    assert task["status"] == "Closed"
