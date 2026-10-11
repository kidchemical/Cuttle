"""python -m api.worktrees agent-ops verbs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from api.worktrees import __main__ as cli


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
def wired(monkeypatch, tmp_path):
    repo = _repo(tmp_path / "primary")
    monkeypatch.setattr(
        cli, "_resolve_project",
        lambda pid: ({"id": 1, "name": "Demo", "default_branch": "main"}, str(repo), None),
    )
    monkeypatch.setattr(cli, "_settings_block", lambda: {})
    return repo


def _run(capsys, argv):
    code = cli.main(argv)
    out = json.loads(capsys.readouterr().out)
    return code, out


def test_cli_create_list_and_remove(wired, capsys):
    code, created = _run(capsys, ["create", "--project-id", "1", "--branch", "cuttle/cli"])
    assert code == 0 and created["success"] is True
    wt = created["worktree"]
    assert Path(wt["path"]).is_dir()

    code, listed = _run(capsys, ["list", "--project-id", "1"])
    assert code == 0
    assert any(w["checkout_uid"] == wt["checkout_uid"] for w in listed["worktrees"])

    code, removed = _run(capsys, ["remove", wt["checkout_uid"], "--force"])
    assert code == 0 and removed["success"] is True
    assert not Path(wt["path"]).exists()


def test_cli_create_refuses_branch_collision(wired, capsys):
    _run(capsys, ["create", "--project-id", "1", "--branch", "cuttle/dup"])
    code, again = _run(capsys, ["create", "--project-id", "1", "--branch", "cuttle/dup"])
    assert code == 1 and again["success"] is False


def test_cli_unknown_project(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_project", lambda pid: (None, None, "Unknown project"))
    code, out = _run(capsys, ["create", "--project-id", "99"])
    assert code == 1 and out["error"] == "Unknown project"
