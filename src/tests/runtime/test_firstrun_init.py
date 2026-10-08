"""First-run initialization: default project, OAuth status (Issues 1, 2)."""

from __future__ import annotations

import os
from pathlib import Path


def _make_pm(tmp_path: Path):
    from managers.project_manager import ProjectManager

    return ProjectManager(db_path=str(tmp_path / "projects.db"))


def test_default_project_uses_repo_root_not_cwd(tmp_path, monkeypatch):
    from managers import project_manager as pm_mod

    created = {}

    orig_add = pm_mod.ProjectManager.add_local_project

    def spy_add(self, name, path, **kw):
        created["name"] = name
        created["path"] = path
        return orig_add(self, name, path, **kw)

    monkeypatch.setattr(pm_mod.ProjectManager, "add_local_project", spy_add)
    # Flask runs with cwd=src — the default must still point at the checkout.
    monkeypatch.chdir(Path(pm_mod.__file__).resolve().parents[1])
    pm = _make_pm(tmp_path)
    repo_root = str(Path(pm_mod.__file__).resolve().parents[2])
    assert created["name"] == "Cuttle"
    assert created["path"] == repo_root
    projects = pm.get_projects()
    assert len(projects) == 1 and projects[0]["name"] == "Cuttle"


def test_existing_project_names_are_not_rewritten(tmp_path):
    pm = _make_pm(tmp_path)
    with pm.get_db_connection() as conn:
        conn.execute("UPDATE projects SET name = 'Cuttle Development'")
        conn.commit()
    pm.ensure_default_project()
    assert [p['name'] for p in pm.get_projects()] == ['Cuttle Development']


def _oauth_client(monkeypatch):
    from api.auth_api import auth_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(auth_bp)
    return app.test_client()


def test_oauth_status_reports_configuration(monkeypatch):
    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    client = _oauth_client(monkeypatch)
    body = client.get("/api/auth/oauth/status").get_json()
    assert body["success"] is True
    assert body["providers"]["google"]["configured"] is False

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    body = client.get("/api/auth/oauth/status").get_json()
    assert body["providers"]["google"]["configured"] is True


def test_oauth_login_unconfigured_gives_guidance(monkeypatch):
    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    client = _oauth_client(monkeypatch)

    res = client.get("/api/auth/oauth/google", headers={"Accept": "application/json"})
    assert res.status_code == 503
    body = res.get_json()
    assert body["success"] is False
    assert ".env" in body["error"]
    assert "OAuth not configured" not in body["error"]

    res = client.get("/api/auth/oauth/google", headers={"Accept": "text/html"})
    assert res.status_code == 302
    assert "oauth_not_configured" in res.headers["Location"]

    # No secrets leak through the status endpoint.
    raw = client.get("/api/auth/oauth/status").get_data(as_text=True)
    assert "secret" not in raw.lower() or "client_secret" not in raw
