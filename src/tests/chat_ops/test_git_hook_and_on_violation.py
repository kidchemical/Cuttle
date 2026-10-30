"""Protected-branch git hook, and what a supervision violation does (on_violation)."""
from __future__ import annotations

import os
import subprocess

import pytest

from api.auth_db import AuthDatabase
from api.chat_ops import git_hook, service, store, triggers, worker


def _git(path, *args, env=None, check=True):
    return subprocess.run(["git", "-C", str(path), *args], check=check, capture_output=True, text=True,
                          env=env)


def _env(agent):
    env = {k: v for k, v in os.environ.items() if k != "CUTTLE_CHAT_SESSION_ID"}
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t"})
    if agent:
        env["CUTTLE_CHAT_SESSION_ID"] = "42"
    return env


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "dev/core")
    _git(path, "commit", "-q", "--allow-empty", "-m", "base", env=_env(False))
    _git(path, "checkout", "-q", "-b", "dev/feature")
    return path


def _commit(repo, branch, *, agent):
    _git(repo, "checkout", "-q", branch, env=_env(False))
    out = _git(repo, "commit", "--allow-empty", "-m", "x", env=_env(agent), check=False)
    _git(repo, "checkout", "-q", "dev/feature", env=_env(False))
    return out


# --- git hook ------------------------------------------------------------------


def test_hook_refuses_agent_updates_of_protected_branches_only(repo):
    result = git_hook.install(repo, ["dev/core"])
    assert result["protected"] == ["dev/core"]
    refused = _commit(repo, "dev/core", agent=True)
    assert refused.returncode != 0 and "may not update protected branch 'dev/core'" in refused.stderr
    assert _commit(repo, "dev/core", agent=False).returncode == 0      # the user's own commit
    assert _commit(repo, "dev/feature", agent=True).returncode == 0    # the agent's own branch
    # Non-commit ref updates are caught too.
    moved = _git(repo, "branch", "-f", "dev/core", "dev/feature", env=_env(True), check=False)
    assert moved.returncode != 0


def test_hook_never_overwrites_a_foreign_hook_and_uninstall_only_removes_ours(repo):
    hook = git_hook.hooks_dir(repo) / git_hook.HOOK_NAME
    hook.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="not Cuttle's"):
        git_hook.install(repo, ["dev/core"])
    assert git_hook.uninstall(repo)["removed"] is False and hook.exists()
    hook.unlink()
    git_hook.install(repo, ["dev/core"])
    assert git_hook.uninstall(repo)["removed"] is True and not hook.exists()
    assert _commit(repo, "dev/core", agent=True).returncode == 0


# --- on_violation --------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    from api.gizmos import tasks
    monkeypatch.setattr(tasks, "database", lambda db=None: db)
    monkeypatch.setattr(triggers, "_pending", {})


@pytest.fixture
def chats(tmp_path, repo):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    supervisor = db.create_chat_session(owner, "supervisor")
    target = db.create_chat_session(owner, "worker")
    db.set_session_project(target, owner, project_name="repo", project_path=str(repo))
    return db, owner, supervisor, target


def _host(uid, sid, method, path, data=None, *, stream=False):
    return {"success": True, "generating": True, "status": "working"}


def _violate(chats, repo, **kw):
    db, _owner, sup, target = chats
    service.supervise(f"CH-{target:06d}", f"CH-{sup:06d}", every=900, instructions="watch",
                      required_branch="dev/feature", db=db, **kw)
    _git(repo, "checkout", "-q", "dev/core", env=_env(False))  # wrong branch → state violation
    return triggers.run_checks({target: "tool"}, db=db, request=_host)


def _checkins(chats, chat):
    return [o for o in store.listing(chat) if o["kind"] == "checkin"]


def test_default_is_wake(chats, repo):
    assert _violate(chats, repo) == 1
    assert len(_checkins(chats, chats[2])) == 1


def test_stop_and_action_run_without_waking_the_supervisor(chats, repo, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "stop", lambda target, actor=None, **k: calls.append(("stop", target)))
    monkeypatch.setattr(worker, "_run_action",
                        lambda name, row, target, violations: calls.append(("action", name)) or {"success": True})
    _violate(chats, repo, on_violation=["stop", "action:notify-phone"])
    target = f"CH-{chats[3]:06d}"
    assert calls == [("stop", target), ("action", "notify-phone")]
    assert _checkins(chats, chats[2]) == []


def test_supervisor_profile_spawns_a_pinned_incident_chat(chats, repo, monkeypatch):
    from api.subagents import profiles
    monkeypatch.setattr(profiles, "resolve", lambda db, uid, pid: {"agent": "claude", "model": "opus",
                                                                  "effort": "high"})
    monkeypatch.setattr("api.agent_harness.agent_models.check_agent_pin", lambda *a: None)
    _violate(chats, repo, on_violation=["supervisor:expert"])
    assert _checkins(chats, chats[2]) == []
    incident = chats[3] + 1  # the next chat created
    found = _checkins(chats, incident)
    assert len(found) == 1
    assert found[0]["payload"]["agent_pins"] == {"claude": {"model": "opus", "effort": "high"}}
    assert found[0]["payload"]["text"].startswith("/claude ")


def test_invalid_entries_are_rejected():
    with pytest.raises(ValueError):
        worker.parse_on_violation(["explode"])
    with pytest.raises(ValueError):
        worker.parse_on_violation(["action:"])
    assert worker.parse_on_violation([]) == ["wake"]
