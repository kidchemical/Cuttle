"""Git service boundary: HTTP parity + service behavior.

Phase 2 Slice 3A. Part 1 (HTTP parity) runs against the monolith BEFORE the
extraction and pins the exact responses the service-backed handlers must
preserve: status/branches/commits/files/commit-diff/pull/push/branch shapes,
auth matrix, non-repo behavior, and the shadowed legacy commit route.

Part 2 (service unit tests) exercises ``api.git_service`` directly against
real git repos in tmp_path (skipped until the module exists).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAN = {"REMOTE_ADDR": "192.0.2.77"}


def _git(*args, cwd, **kw):
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]
    return subprocess.run(base + list(args), cwd=cwd, capture_output=True,
                          text=True, timeout=60, **kw)


def _init_repo(path: Path, files=("a.txt",), dirty=False):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    for name in files:
        (path / name).write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "seed", cwd=path)
    if dirty:
        (path / files[0]).write_text("one\ntwo\n", encoding="utf-8")
        (path / "new.txt").write_text("new\n", encoding="utf-8")
    return path


class _FakePM:
    def __init__(self, projects, current=None):
        self._projects = list(projects)
        self._current = current if current is not None else (projects[0] if projects else None)

    def get_projects(self):
        return list(self._projects)

    def get_current_project(self):
        return self._current


def _ctx(tmp_path, monkeypatch, repo: Path):
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "git-svc.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    registry = [{"id": 1, "name": "R", "type": "local", "path": str(repo)}]
    fake = _FakePM(registry)
    # Stub every holder: transport moved to the git blueprint in Slice 3B
    # while other monolith handlers still read the monolith's reference.
    monkeypatch.setattr(wca, "project_manager", fake)
    try:
        import api.git_routes as git_routes_mod
    except ImportError:
        git_routes_mod = None
    if git_routes_mod is not None and hasattr(git_routes_mod, "project_manager"):
        monkeypatch.setattr(git_routes_mod, "project_manager", fake)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    guest = db.create_user("guest@local", "Guest", "local", password="x")
    db.create_chat_session(owner, "o")
    db.create_chat_session(guest, "g")
    client = wca.app.test_client()
    client.set_cookie("session_token", db.create_auth_session(owner))
    guest_client = wca.app.test_client()
    guest_client.set_cookie("session_token", db.create_auth_session(guest))
    anon = wca.app.test_client()
    return {"client": client, "guest": guest_client, "anon": anon}


def test_git_routes_auth_matrix(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    ctx = _ctx(tmp_path, monkeypatch, repo)
    # Reads: anon 401.
    for path in ("/api/git/status", "/api/git/branches", "/api/git/commits",
                 "/api/git/files", "/api/git/repos", "/api/git/graph"):
        res = ctx["anon"].get(path, environ_base=LAN)
        assert res.status_code == 401, path
    # Mutations: guest 403.
    for method, path, payload in [
        ("POST", "/api/git/pull", {}),
        ("POST", "/api/git/push", {}),
        ("POST", "/api/git/branch", {"action": "create", "branch": "x"}),
        ("POST", "/api/git/commit", {"message": "x", "path": str(repo)}),
        ("POST", "/api/git/ignore", {"path": "f"}),
    ]:
        res = ctx["guest"].open(path, method=method, json=payload, environ_base=LAN)
        assert res.status_code == 403, (method, path, res.status_code)


def test_git_status_shape_and_counts(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo", dirty=True)
    ctx = _ctx(tmp_path, monkeypatch, repo)
    res = ctx["client"].get("/api/git/status", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["repository"] == "repo"
    assert data["currentBranch"] in ("main", "master")
    assert data["workingDirectory"]["clean"] is False
    assert data["workingDirectory"]["modified"] == 1
    assert data["workingDirectory"]["untracked"] == 1
    # Quirk preserved verbatim by the service: stdout.strip() eats the first
    # line's leading status space, so a worktree-modified first file counts
    # as staged=1 via the `line[0] != ' '` branch.
    assert data["stagingArea"]["staged"] == 1


def test_git_status_non_repo_is_400(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    ctx = _ctx(tmp_path, monkeypatch, plain)
    res = ctx["client"].get("/api/git/status", environ_base=LAN)
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "Not in a Git repository"}


def test_git_branches_commits_files_shapes(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo", dirty=True)
    _git("branch", "feature", cwd=repo)
    ctx = _ctx(tmp_path, monkeypatch, repo)
    res = ctx["client"].get("/api/git/branches", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    names = {b["name"] for b in body["data"]}
    assert body["current"] in names
    assert "feature" in names
    assert {b["name"] for b in body["data"] if b["current"]} == {body["current"]}
    res = ctx["client"].get("/api/git/commits?per_page=5", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["message"] == "seed"
    assert set(body["data"][0]) == {"hash", "author", "email", "date", "message"}
    # Quirk (same stdout.strip() root cause as status): a worktree-modified
    # first file misses the status map (line[3:] shifts) and reads 'clean'.
    res = ctx["client"].get("/api/git/files?status=modified", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"] == []
    res = ctx["client"].get("/api/git/files", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    got = {f["name"]: f for f in res.get_json()["data"]}
    assert got["a.txt"]["status"] == "clean"  # quirk, see above
    # Staged content has no leading space to strip: the filter works there.
    _git("add", "a.txt", cwd=repo)
    res = ctx["client"].get("/api/git/files?status=modified", environ_base=LAN)
    assert [f["name"] for f in res.get_json()["data"]] == ["a.txt"]


def test_git_commit_diff_shape(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "second", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    ctx = _ctx(tmp_path, monkeypatch, repo)
    res = ctx["client"].get(f"/api/git/commit/{head}/diff", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["commit"]["hash"] == head
    assert data["commit"]["message"] == "second"
    assert any(f["filename"] == "a.txt" for f in data["files"])
    assert "+two" in data["diff"]
    res = ctx["client"].get(f"/api/git/commit/{head}/diff?file=a.txt", environ_base=LAN)
    assert res.status_code == 200
    assert "+two" in res.get_json()["data"]["diff"]
    res = ctx["client"].get("/api/git/commit/deadbeef/diff", environ_base=LAN)
    assert res.status_code == 500


def test_git_branch_lifecycle_and_validation(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    ctx = _ctx(tmp_path, monkeypatch, repo)
    res = ctx["client"].post("/api/git/branch", json={}, environ_base=LAN)
    assert res.status_code == 400
    res = ctx["client"].post(
        "/api/git/branch", json={"action": "frobnicate", "branch": "x"}, environ_base=LAN)
    assert res.status_code == 400
    for action, branch, frag in [
        ("create", "life", "Created and switched to branch"),
        ("switch", "main", "Switched to branch"),
        ("delete", "life", "Deleted branch"),
    ]:
        res = ctx["client"].post(
            "/api/git/branch", json={"action": action, "branch": branch}, environ_base=LAN)
        assert res.status_code == 200, (action, res.get_json())
        assert frag in res.get_json()["message"]
    assert _git("branch", "--show-current", cwd=repo).stdout.strip() == "main"


def test_git_pull_push_local_remote(tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    _git("init", "--bare", str(bare), cwd=tmp_path)
    repo = _init_repo(tmp_path / "repo")
    _git("remote", "add", "origin", str(bare), cwd=repo)
    _git("push", "-u", "origin", "main", cwd=repo)
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "ahead", cwd=repo)
    ctx = _ctx(tmp_path, monkeypatch, repo)
    res = ctx["client"].post("/api/git/push", json={}, environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["success"] is True
    assert body["branch"] == "main"
    assert body["remote"] == "origin"
    assert body["repo_root"] == str(repo)
    # Move remote ahead via a second clone, then pull fast-forwards.
    clone2 = tmp_path / "clone2"
    _git("clone", str(bare), str(clone2), cwd=tmp_path)
    (clone2 / "b.txt").write_text("b\n", encoding="utf-8")
    _git("add", ".", cwd=clone2)
    _git("commit", "-m", "remote work", cwd=clone2)
    _git("push", "origin", "main", cwd=clone2)
    _git("reset", "--hard", "HEAD~1", cwd=repo)
    res = ctx["client"].post(
        "/api/git/pull", json={"remote": "origin", "branch": "main"}, environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["success"] is True
    assert (repo / "b.txt").exists()


def _svc():
    return pytest.importorskip("api.git_service")


def test_service_status_and_quirks(tmp_path):
    gs = _svc()
    repo = _init_repo(tmp_path / "repo", dirty=True)
    out = gs.repo_status(str(repo))
    assert out["repository"] == "repo"
    assert out["workingDirectory"]["modified"] == 1
    assert out["workingDirectory"]["untracked"] == 1
    assert out["stagingArea"]["staged"] == 1  # strip() quirk, see HTTP test
    with pytest.raises(gs.NotARepositoryError):
        gs.repo_status(str(tmp_path))
    assert gs.resolve_repo_cwd({"type": "local", "path": str(repo)}) == str(repo)
    assert gs.resolve_repo_cwd({"type": "weird", "path": str(repo)}, fallback="/fb") == "/fb"
    assert gs.resolve_repo_cwd(None) == os.getcwd()


def test_service_branches_commits_files(tmp_path):
    import os as _os  # noqa: F401

    gs = _svc()
    repo = _init_repo(tmp_path / "repo", dirty=True)
    _git("branch", "feature", cwd=repo)
    out = gs.list_branches(str(repo))
    assert {b["name"] for b in out["branches"]} >= {"main", "feature"}
    assert out["current"] == "main"
    out = gs.list_commits(str(repo), 1, 20)
    assert out["pagination"]["total"] == 1
    assert out["commits"][0]["message"] == "seed"
    out = gs.list_files(str(repo), 1, 50, "modified")
    assert out["files"] == []  # strip() quirk: worktree-modified first file
    _git("add", "a.txt", cwd=repo)
    out = gs.list_files(str(repo), 1, 50, "modified")
    assert [f["name"] for f in out["files"]] == ["a.txt"]
    with pytest.raises(gs.GitError):
        gs.list_commits(str(tmp_path), 1, 20)


def test_service_commit_diff(tmp_path):
    gs = _svc()
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-m", "second", cwd=repo)
    assert "second" in _git("log", "--oneline", cwd=repo).stdout
    head = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    d = gs.commit_diff(str(repo), head, 1, 100, None)
    assert d["commit"]["message"] == "second"
    assert any(f["filename"] == "a.txt" for f in d["files"])
    assert "+two" in d["diff"]
    d = gs.commit_diff(str(repo), head, 1, 100, "a.txt")
    assert "+two" in d["diff"]
    with pytest.raises(gs.GitError):
        gs.commit_diff(str(repo), "deadbeef", 1, 100, None)


def test_service_pull_push_branch(tmp_path):
    gs = _svc()
    bare = tmp_path / "remote.git"
    _git("init", "--bare", str(bare), cwd=tmp_path)
    repo = _init_repo(tmp_path / "repo")
    _git("remote", "add", "origin", str(bare), cwd=repo)
    _git("push", "-u", "origin", "main", cwd=repo)
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "ahead", cwd=repo)
    res = gs.push_repo(str(repo), "origin", "main")
    assert res.returncode == 0
    assert gs.current_branch_name(str(repo)) == "main"
    assert gs.current_branch_name(str(tmp_path)) == ""
    clone2 = tmp_path / "clone2"
    _git("clone", str(bare), str(clone2), cwd=tmp_path)
    (clone2 / "b.txt").write_text("b\n", encoding="utf-8")
    _git("add", ".", cwd=clone2)
    _git("commit", "-m", "remote work", cwd=clone2)
    _git("push", "origin", "main", cwd=clone2)
    _git("reset", "--hard", "HEAD~1", cwd=repo)
    out = gs.pull_repo(str(repo), "origin", "main")
    assert isinstance(out, str)
    assert (repo / "b.txt").exists()
    with pytest.raises(gs.GitError):
        gs.pull_repo(str(repo), "origin", "no-such-branch")
    assert gs.branch_operation(str(repo), "create", "life")["message"].startswith("Created")
    assert gs.branch_operation(str(repo), "switch", "main")["message"].startswith("Switched")
    assert gs.branch_operation(str(repo), "delete", "life")["message"].startswith("Deleted")
    with pytest.raises(ValueError):
        gs.branch_operation(str(repo), "frobnicate", "x")
    with pytest.raises(gs.GitError):
        gs.branch_operation(str(repo), "switch", "no-such-branch")


def test_commit_route_registered_once():
    """Slice 3B removed the shadowed legacy ``git_commit`` handler (closure
    proven: no prod references, both frontend callers hit the path served
    by ``git_commit_pending``, legacy modal is shape-agnostic). Exactly one
    POST /api/git/commit must remain."""
    from api import web_chat_api as wca

    endpoints = [r.endpoint for r in wca.app.url_map.iter_rules()
                 if str(r.rule) == "/api/git/commit" and "POST" in r.methods]
    assert endpoints == ["git.git_commit_pending"]


def test_push_failure_exposes_hook_report_and_prefers_hook_summary(tmp_path, monkeypatch):
    import json
    import subprocess
    from api import git_service
    from core.git_push_diagnostics import REPORT_PREFIX
    repo = _init_repo(tmp_path / 'hook-report')
    ctx = _ctx(tmp_path, monkeypatch, repo)
    report = {'version': 1, 'findings': [{'hook': 'secret-patterns', 'rule': 'private key', 'file': 'test.py', 'commit': 'abc', 'line': 3}], 'warnings': []}
    monkeypatch.setattr(git_service, 'push_repo', lambda *a: subprocess.CompletedProcess([], 1, REPORT_PREFIX + json.dumps(report), 'error: failed to push some refs'))
    response = ctx['client'].post('/api/git/push', json={}, environ_base=LAN)
    assert response.status_code == 500
    data = response.get_json()
    assert 'secret-patterns' in data['error']
    assert data['push_report']['findings'][0]['file'] == 'test.py'
    assert data['push_report']['findings'][0]['commit'] == 'abc'
