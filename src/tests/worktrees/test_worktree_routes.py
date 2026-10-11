"""Worktree HTTP surface: flag gate, owner-gated CRUD, collision, setup auth."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(*args, cwd, **kw):
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]
    return subprocess.run(base + list(args), cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", timeout=60, **kw)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "seed", cwd=path)
    return path


@pytest.fixture
def client(owner_session):
    from api.web_chat_api import app
    return owner_session.sign_in(app.test_client())


def _enable(monkeypatch, primary, *, project_id=1, name="Demo"):
    from api.worktrees import routes as routes_mod
    monkeypatch.setattr(routes_mod, "is_worktrees_enabled", lambda: True)
    monkeypatch.setattr(
        routes_mod, "_project_primary",
        lambda pid: ({"id": project_id, "name": name, "default_branch": "main"},
                     str(primary), None),
    )
    monkeypatch.setattr(routes_mod, "_settings_block", lambda: {})
    return routes_mod


def test_flag_off_returns_404(client, monkeypatch):
    from api.worktrees import routes as routes_mod
    monkeypatch.setattr(routes_mod, "is_worktrees_enabled", lambda: False)
    assert client.get("/api/worktrees").status_code == 404
    assert client.post("/api/worktrees", json={"project_id": 1}).status_code == 404


def test_create_list_collision_and_remove(client, monkeypatch, tmp_path):
    repo = _repo(tmp_path / "primary")
    _enable(monkeypatch, repo)

    created = client.post("/api/worktrees", json={"project_id": 1, "branch": "cuttle/ui1"})
    assert created.status_code == 201, created.get_json()
    wt = created.get_json()["worktree"]
    assert Path(wt["path"]).is_dir()
    assert wt["branch"] == "cuttle/ui1"

    listed = client.get("/api/worktrees?project_id=1").get_json()["worktrees"]
    assert any(w["checkout_uid"] == wt["checkout_uid"] for w in listed)

    # Same branch already checked out → refused.
    dup = client.post("/api/worktrees", json={"project_id": 1, "branch": "cuttle/ui1"})
    assert dup.status_code == 409

    # Dirty removal refused; force removes.
    (Path(wt["path"]) / "wip.txt").write_text("dirty\n", encoding="utf-8")
    assert client.delete(f"/api/worktrees/{wt['checkout_uid']}").status_code == 409
    forced = client.delete(f"/api/worktrees/{wt['checkout_uid']}?force=1")
    assert forced.status_code == 200 and forced.get_json()["removed"] is True
    assert not Path(wt["path"]).exists()


def test_setup_hook_requires_explicit_authorization(client, monkeypatch, tmp_path):
    repo = _repo(tmp_path / "primary")
    (repo / ".cuttle").mkdir()
    (repo / ".cuttle" / "worktrees.yaml").write_text(
        "setup:\n  run_posix: touch setup-ran\n", encoding="utf-8"
    )
    _enable(monkeypatch, repo, project_id=2, name="Hook")

    unauth = client.post("/api/worktrees", json={
        "project_id": 2, "branch": "cuttle/h1", "run_setup": True,
    })
    assert unauth.status_code == 201
    assert unauth.get_json()["setup"] == {"ran": False, "reason": "authorization_required"}
    assert not (Path(unauth.get_json()["worktree"]["path"]) / "setup-ran").exists()

    auth = client.post("/api/worktrees", json={
        "project_id": 2, "branch": "cuttle/h2", "run_setup": True, "authorize_setup": True,
    })
    assert auth.status_code == 201
    assert auth.get_json()["setup"]["ok"] is True
    assert (Path(auth.get_json()["worktree"]["path"]) / "setup-ran").exists()
