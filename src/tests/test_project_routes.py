"""Projects HTTP contract: routes, auth matrix, shapes, guards.

Characterization for the Phase 2 Projects extraction slice. These tests pin
the behavior owned by the Projects HTTP boundary regardless of which module
registers the routes (monolith before, ``api.project_routes`` after):

- all 10 routes registered with identical paths + methods,
- reads need auth (401 anon), writes need owner (403 non-owner),
- response shapes incl. 400/404/500/503 cases.

Auth sessions reuse the pattern from test_http_authz._auth_client.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

LAN = {"REMOTE_ADDR": "192.0.2.77"}

# Modules that may hold the `project_manager` reference across the slice.
_PM_HOLDERS = ("api.web_chat_api", "api.project_routes")


def _set_pm(monkeypatch, value):
    patched = []
    for name in _PM_HOLDERS:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "project_manager"):
            monkeypatch.setattr(mod, "project_manager", value)
            patched.append(name)
    assert patched, "no project_manager holder found"
    return patched


class _FakePM:
    def __init__(self, projects=None, current=None):
        self._projects = list(projects or [])
        self._current = current
        self.calls = []

    def get_projects(self):
        self.calls.append("get_projects")
        return list(self._projects)

    def get_project(self, project_id):
        self.calls.append(("get_project", project_id))
        for p in self._projects:
            try:
                if int(p.get("id")) == int(project_id):
                    return p
            except (TypeError, ValueError):
                continue
        return None

    def get_current_project(self):
        self.calls.append("get_current_project")
        return self._current if self._current is not None else (self._projects[0] if self._projects else None)

    def get_project_history(self, project_id=None, limit=50):
        self.calls.append(("get_project_history", project_id, limit))
        return [{"id": 1}]

    def get_project_stats(self):
        self.calls.append("get_project_stats")
        return {"total": len(self._projects)}

    def add_local_project(self, name, path, description="", tags=None):
        self.calls.append(("add_local_project", name, path))
        return True

    def add_github_project(self, name, repo_url, local_path=None, description="", tags=None):
        self.calls.append(("add_github_project", name, repo_url))
        return True

    def add_gitlab_project(self, name, repo_url, local_path=None, description="", tags=None):
        self.calls.append(("add_gitlab_project", name, repo_url))
        return True

    def update_project(self, project_id, **data):
        self.calls.append(("update_project", project_id))
        return True

    def delete_project(self, project_id):
        self.calls.append(("delete_project", project_id))
        return True

    def switch_to_project(self, project_id):
        self.calls.append(("switch_to_project", project_id))
        return True

    def sync_remote_project(self, project_id):
        self.calls.append(("sync_remote_project", project_id))
        return True


def _auth_ctx(tmp_path, monkeypatch):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "proj-routes.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    # Multi-user mode: only owner@local may mutate; guests get 403.
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    other = db.create_user("other@local", "Other", "local", password="x")
    db.create_chat_session(owner, "o")
    db.create_chat_session(other, "g")
    client = wca.app.test_client()
    return {
        "client": client,
        "owner": db.create_auth_session(owner),
        "guest": db.create_auth_session(other),
    }


def _as(client, token):
    client.set_cookie("session_token", token)
    return client


EXPECTED_ROUTES = {
    ("/api/projects", ("GET",)),
    ("/api/projects", ("POST",)),
    ("/api/projects/<int:project_id>", ("GET",)),
    ("/api/projects/<int:project_id>", ("PUT",)),
    ("/api/projects/<int:project_id>", ("DELETE",)),
    ("/api/projects/current", ("GET",)),
    ("/api/projects/<int:project_id>/switch", ("POST",)),
    ("/api/projects/<int:project_id>/sync", ("POST",)),
    ("/api/projects/history", ("GET",)),
    ("/api/projects/stats", ("GET",)),
}


def test_project_routes_registered_once_with_methods():
    from api import web_chat_api as wca

    found = {}
    for rule in wca.app.url_map.iter_rules():
        key = (str(rule.rule), tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        if str(rule.rule).startswith("/api/projects"):
            found.setdefault(key, 0)
            found[key] += 1
    for path, methods in EXPECTED_ROUTES:
        matches = [k for k in found if k[0] == path and set(methods) <= set(k[1])]
        assert matches, f"missing route {methods} {path}"
        for m in matches:
            assert found[m] == 1, f"duplicate registration {m}"


def test_reads_need_auth_and_match_shapes(tmp_path, monkeypatch):
    ctx = _auth_ctx(tmp_path, monkeypatch)
    _set_pm(monkeypatch, _FakePM(
        projects=[{"id": 7, "name": "Seven", "path": "/tmp/seven", "type": "local"}],
    ))
    anon = ctx["client"]
    assert anon.get("/api/projects", environ_base=LAN).status_code == 401
    c = _as(ctx["client"], ctx["guest"])
    res = c.get("/api/projects", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json() == {"success": True, "data": [
        {"id": 7, "name": "Seven", "path": "/tmp/seven", "type": "local"}]}
    res = c.get("/api/projects/7", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["data"]["id"] == 7
    res = c.get("/api/projects/999", environ_base=LAN)
    assert res.status_code == 404
    assert res.get_json() == {"success": False, "error": "Project not found"}
    res = c.get("/api/projects/current", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["data"]["id"] == 7
    res = c.get("/api/projects/history?project_id=7&limit=5", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json() == {"success": True, "data": [{"id": 1}]}
    res = c.get("/api/projects/stats", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json() == {"success": True, "data": {"total": 1}}


def test_writes_need_owner_and_validate(tmp_path, monkeypatch):
    import os

    ctx = _auth_ctx(tmp_path, monkeypatch)
    seven = {"id": 7, "name": "Seven", "path": str(tmp_path), "type": "local"}
    fake = _FakePM(projects=[seven], current=seven)
    _set_pm(monkeypatch, fake)
    g = _as(ctx["client"], ctx["guest"])
    for method, path, payload in [
        ("POST", "/api/projects", {"name": "x", "type": "local", "path": str(tmp_path)}),
        ("PUT", "/api/projects/7", {"name": "y"}),
        ("DELETE", "/api/projects/7", None),
        ("POST", "/api/projects/7/switch", {}),
        ("POST", "/api/projects/7/sync", {}),
    ]:
        res = g.open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 403, (method, path, res.status_code)
    o = _as(ctx["client"], ctx["owner"])
    res = o.post("/api/projects", json={"name": "x"}, environ_base=LAN)
    assert res.status_code == 400  # name without type
    res = o.post("/api/projects", json={"name": "x", "type": "local"}, environ_base=LAN)
    assert res.status_code == 400  # local without path
    res = o.post("/api/projects", json={"name": "x", "type": "local", "path": "/no/such/dir"},
                 environ_base=LAN)
    assert res.status_code == 400  # path must exist
    res = o.post("/api/projects",
                 json={"name": "x", "type": "local", "path": str(tmp_path)}, environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["success"] is True
    res = o.post("/api/projects", json={"name": "x", "type": "weird"}, environ_base=LAN)
    assert res.status_code == 400
    res = o.put("/api/projects/7", json={}, environ_base=LAN)
    assert res.status_code == 400  # no data
    res = o.put("/api/projects/7", json={"name": "y"}, environ_base=LAN)
    assert res.status_code == 200
    res = o.delete("/api/projects/7", environ_base=LAN)
    assert res.status_code == 200
    res = o.post("/api/projects/7/switch", json={}, environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["data"]["id"] == 7
    res = o.post("/api/projects/7/sync", json={}, environ_base=LAN)
    assert res.status_code == 200
    assert ("switch_to_project", 7) in fake.calls
    assert ("sync_remote_project", 7) in fake.calls
    # failure from the manager surfaces as 500 with the frozen shape
    fake_fail = _FakePM()
    fake_fail.delete_project = lambda project_id: False
    _set_pm(monkeypatch, fake_fail)
    res = o.delete("/api/projects/7", environ_base=LAN)
    assert res.status_code == 500
    assert res.get_json() == {"success": False, "error": "Failed to delete project"}


def test_manager_missing_answers_503(tmp_path, monkeypatch):
    ctx = _auth_ctx(tmp_path, monkeypatch)
    _set_pm(monkeypatch, None)
    o = _as(ctx["client"], ctx["owner"])
    for method, path in [
        ("GET", "/api/projects"),
        ("GET", "/api/projects/7"),
        ("GET", "/api/projects/current"),
        ("POST", "/api/projects"),
        ("PUT", "/api/projects/7"),
        ("DELETE", "/api/projects/7"),
        ("POST", "/api/projects/7/switch"),
        ("POST", "/api/projects/7/sync"),
        ("GET", "/api/projects/history"),
        ("GET", "/api/projects/stats"),
    ]:
        res = o.open(path, method=method, json={}, environ_base=LAN)
        assert res.status_code == 503, (method, path, res.status_code)
        assert res.get_json()["error"] == "Project manager not available"
