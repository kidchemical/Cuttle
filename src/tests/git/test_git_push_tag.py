"""git.push `tag` param publishes exactly one release tag (never --tags)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".cuttle_global" / "scripts" / "git-push.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    (work / "a.txt").write_text("a", encoding="utf-8")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "a")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "tag", "stable")  # private tag that must never ride along
    _git(work, "tag", "v1.2.3")
    return work, remote


def _run(work: Path, *, chat_project: bool = False, **params: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CUTTLE_PARAM_") and k != "CUTTLE_CHAT_PROJECT_PATH"}
    env.update({f"CUTTLE_PARAM_{k.upper()}": v for k, v in params.items()})
    env["CUTTLE_CHAT_PROJECT_PATH" if chat_project else "CUTTLE_PARAM_PATH"] = str(work)
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_tag_push_publishes_only_that_tag(repo):
    work, remote = repo
    result = _run(work, mode="push", remote="origin", tag="v1.2.3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(remote, "tag") == "v1.2.3"
    assert _git(remote, "branch") == ""


def test_missing_tag_is_refused(repo):
    work, remote = repo
    result = _run(work, mode="push", remote="origin", tag="v9.9.9")
    assert result.returncode == 1
    assert "No local tag" in result.stdout
    assert _git(remote, "tag") == ""


def test_pathless_card_pushes_the_chat_project(repo):
    work, remote = repo
    result = _run(work, chat_project=True, mode="push", remote="origin", branch="main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(remote, "branch") == "main"


def test_shell_actions_receive_the_chat_project(tmp_path):
    from api import project_actions as actions

    owner, chat = tmp_path / "install", tmp_path / "game"
    folder = owner / ".cuttle" / "actions"
    folder.mkdir(parents=True)
    chat.mkdir()
    (folder / "probe.yaml").write_text(
        "name: test.probe\ntype: shell\nrun_posix: echo $CUTTLE_CHAT_PROJECT_PATH\n"
        "run: echo %CUTTLE_CHAT_PROJECT_PATH%\n", encoding="utf-8")
    action = {**actions.find_project_action(str(owner), "test.probe"),
              "context_project_path": str(chat)}
    assert str(chat) in actions._execute_shell(action, {})["response"]


def test_option_shaped_tag_is_refused(repo):
    work, remote = repo
    result = _run(work, mode="push", remote="origin", tag="--tags")
    assert result.returncode == 1
    assert _git(remote, "tag") == ""
