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


def _env(agent, checkout=None, supervised="subagent"):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CUTTLE_CHAT_SESSION_ID", "CUTTLE_AGENT_PROJECT_PATH", "CUTTLE_SUPERVISED")}
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t"})
    if agent:
        env["CUTTLE_CHAT_SESSION_ID"] = "42"
        if supervised:
            env["CUTTLE_SUPERVISED"] = supervised
    if checkout:
        env["CUTTLE_AGENT_PROJECT_PATH"] = str(checkout)
    return env


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "dev/core")
    _git(path, "commit", "-q", "--allow-empty", "-m", "base", env=_env(False))
    _git(path, "checkout", "-q", "-b", "dev/feature")
    return path


def _commit(repo, branch, *, agent, checkout=None, supervised="subagent"):
    _git(repo, "checkout", "-q", branch, env=_env(False))
    out = _git(repo, "commit", "--allow-empty", "-m", "x", env=_env(agent, checkout, supervised), check=False)
    _git(repo, "checkout", "-q", "dev/feature", env=_env(False))
    return out


# --- git hook ------------------------------------------------------------------


def test_hook_refuses_supervised_agents_outside_the_branch_checkout_only(repo):
    result = git_hook.install(repo, ["dev/core"])
    assert result["protected"] == ["dev/core"]
    other = repo.parent / "other-wt"
    _git(repo, "worktree", "add", "-q", "-b", "dev/other", str(other), env=_env(False))
    # A sub-agent / supervised chat working in another worktree may not touch dev/core.
    refused = _commit(repo, "dev/core", agent=True, checkout=other)
    assert refused.returncode != 0 and "may not update protected branch 'dev/core'" in refused.stderr
    assert "supervised: subagent" in refused.stderr
    assert _commit(repo, "dev/core", agent=True, checkout=other, supervised="supervised").returncode != 0
    # No checkout known: refused too (conservative).
    assert _commit(repo, "dev/core", agent=True).returncode != 0
    # The user's terminal, and a supervised agent whose checkout is on dev/core, pass.
    assert _commit(repo, "dev/core", agent=False).returncode == 0
    assert _commit(repo, "dev/core", agent=True, checkout=repo).returncode == 0
    # The outside agent keeps working on its own branch.
    assert _git(other, "commit", "--allow-empty", "-m", "x", env=_env(True, other), check=False).returncode == 0
    # Non-commit ref updates are caught too.
    moved = _git(repo, "branch", "-f", "dev/core", "dev/other", env=_env(True, other), check=False)
    assert moved.returncode != 0


def test_hook_lets_a_user_driven_top_level_chat_update_protected_branches(repo):
    git_hook.install(repo, ["dev/core"])
    other = repo.parent / "other-wt"
    _git(repo, "worktree", "add", "-q", "-b", "dev/other", str(other), env=_env(False))
    assert _commit(repo, "dev/core", agent=True, checkout=other, supervised="").returncode == 0
    moved = _git(repo, "branch", "-f", "dev/core", "dev/other", env=_env(True, other, supervised=""),
                 check=False)
    assert moved.returncode == 0, moved.stderr


def test_install_upgrades_an_older_cuttle_hook_in_place(repo):
    hook = git_hook.hooks_dir(repo) / git_hook.HOOK_NAME
    hook.write_text("#!/bin/sh\n# cuttle-protect-branch-hook v1\nexit 1\n")
    git_hook.install(repo, ["dev/core"])
    assert git_hook.MARKER in hook.read_text() and "v1" not in hook.read_text()


def test_supervised_reason_marks_subagents_and_supervised_targets_only(chats, monkeypatch):
    db, owner, sup, target = chats
    assert git_hook.supervised_reason(target, db=db) == ""
    assert git_hook.supervised_reason(None, db=db) == ""
    child = db.create_subagent_chat_session(owner, parent_session_id=target, session_name="lane")
    assert git_hook.supervised_reason(child, db=db) == "subagent"
    service.supervise(f"CH-{target:06d}", f"CH-{sup:06d}", every=900, instructions="watch", db=db)
    assert git_hook.supervised_reason(target, db=db) == "supervised"
    # Paused supervision is not supervision: the chat is the user's again until resumed.
    op = store.supervising([target])[0]
    store.set_paused(op["id"], op["actor"], True)
    assert git_hook.supervised_reason(target, db=db) == ""
    assert store.supervisor_links().get(target) == sup  # UI grouping still sees the paused link
    assert git_hook.supervised_reason(child, db=db) == "subagent"
    store.set_paused(op["id"], op["actor"], False)
    assert git_hook.supervised_reason(target, db=db) == "supervised"
    # The supervisor chat itself is the user's, not supervised.
    assert git_hook.supervised_reason(sup, db=db) == ""


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
