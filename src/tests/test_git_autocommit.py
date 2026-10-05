"""Opt-in auto-commit of agent edits (api.git_autocommit)."""

from __future__ import annotations

import subprocess

import pytest


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def settings(monkeypatch, tmp_path):
    from managers import settings_manager as sm

    isolated = sm.SettingsManager(settings_file=str(tmp_path / "settings.json"))
    monkeypatch.setattr(sm, "_settings_manager", isolated)
    return isolated


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "agent.py").write_text("a = 1\n")
    (root / "mine.py").write_text("m = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    monkeypatch.setattr(
        "api.commit_message_suggester.suggest_commit_message",
        lambda ctx, **k: {"message": "Update agent value", "source": "heuristic"},
    )
    return root


def _journal(root, rel, qid):
    from api.edit_attribution.journal import append_events

    append_events([{"repo_root": str(root), "rel_path": rel, "agent_id": "codex",
                    "model": "gpt", "query_id": qid, "chat_session_id": "5"}])


def test_default_off(settings):
    from api.git_autocommit import is_enabled, schedule_after_turn

    assert is_enabled() is False
    assert schedule_after_turn(cwd="/tmp", query_id="q") is False


def test_commits_only_files_the_turn_changed(settings, repo):
    from api.git_autocommit import commit_turn

    (repo / "agent.py").write_text("a = 2\n")
    (repo / "mine.py").write_text("m = 2  # user's own uncommitted edit\n")
    _journal(repo, "agent.py", "q1")
    out = commit_turn(cwd=str(repo), query_id="q1", chat_session_id="5", prompt="bump a")
    assert out["committed"] and out["paths"] == ["agent.py"]
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["agent.py"]
    assert "Update agent value" in _git(repo, "log", "-1", "--format=%B")
    # The user's unrelated edit stays pending; nothing is pushed.
    assert "mine.py" in _git(repo, "status", "--porcelain")


def test_turn_without_edits_commits_nothing(settings, repo):
    from api.git_autocommit import commit_turn

    (repo / "mine.py").write_text("m = 3\n")
    out = commit_turn(cwd=str(repo), query_id="q-none")
    assert out["committed"] is False
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


def test_already_committed_is_quiet(settings, repo, monkeypatch):
    from api.git_autocommit import commit_turn

    toasts = []
    monkeypatch.setattr("api.chat_vfx.toast", lambda *a, **k: toasts.append(a))
    (repo / "agent.py").write_text("a = 4\n")
    _journal(repo, "agent.py", "q2")
    _git(repo, "commit", "-qam", "user beat us to it")
    out = commit_turn(cwd=str(repo), query_id="q2", chat_session_id="5")
    assert out["committed"] is False and toasts == []


def test_kernel_schedules_only_when_enabled(settings, monkeypatch):
    import api.git_autocommit as ga

    calls = []
    monkeypatch.setattr(ga, "commit_turn", lambda **k: calls.append(k))
    assert ga.schedule_after_turn(cwd="/x", query_id="q") is False
    ga.set_enabled(True)
    assert ga.schedule_after_turn(cwd="/x", query_id="q", chat_session_id="5", prompt="p") is True
    import time
    for _ in range(50):
        if calls:
            break
        time.sleep(0.01)
    assert calls and calls[0]["query_id"] == "q"


def test_settings_route_round_trip(tmp_path, monkeypatch, settings):
    from tests.test_http_authz import _auth_client, LAN

    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    c = ctx["client"]
    assert c.get("/api/settings/git-auto-commit", environ_base=LAN).get_json()["git_auto_commit"] is False
    res = c.post("/api/settings/git-auto-commit", json={"git_auto_commit": True}, environ_base=LAN)
    assert res.status_code == 200 and res.get_json()["git_auto_commit"] is True
    assert settings.get_setting("git_auto_commit") is True
    bad = c.post("/api/settings/git-auto-commit", json={"git_auto_commit": "yes"}, environ_base=LAN)
    assert bad.status_code == 400
