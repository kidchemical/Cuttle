"""Git page HEAD jump: ``git_service.checkout_ref`` + detached graph history."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api.git_service import DirtyWorkTreeError, checkout_ref
from scripts.utilities.git_graph import collect_git_graph


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                       text=True, encoding="utf-8", check=False)
    assert r.returncode == 0, r.stderr or r.stdout
    return r.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    for i in range(3):
        (repo / "a.txt").write_text(f"v{i}\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", f"c{i}")
    return repo


def _hashes(repo: Path) -> list:
    return _git(repo, "log", "--format=%H").splitlines()  # newest first


def test_detach_to_commit_and_back_to_branch(repo: Path):
    main = _git(repo, "branch", "--show-current")
    newest, middle, _oldest = _hashes(repo)

    out = checkout_ref(str(repo), middle[:7])
    assert out["detached"] is True and out["branch"] is None
    assert out["head"] == middle
    assert (repo / "a.txt").read_text(encoding="utf-8") == "v1\n"

    out = checkout_ref(str(repo), main)
    assert out == {**out, "detached": False, "branch": main, "head": newest}


def test_detached_graph_keeps_newer_commits_visible(repo: Path):
    newest, middle, oldest = _hashes(repo)
    checkout_ref(str(repo), oldest)

    data = collect_git_graph(str(repo), limit=10)
    assert data["detached"] is True
    assert data["head"] == oldest
    shown = [c["hash"] for c in data["commits"]]  # same-second commits: order varies
    # The branch containing HEAD is walked too, so "forward" stays reachable.
    assert sorted(shown) == sorted([newest, middle, oldest])


def test_attached_graph_reports_head(repo: Path):
    newest = _hashes(repo)[0]
    data = collect_git_graph(str(repo), limit=10)
    assert data["detached"] is False
    assert data["head"] == newest


def test_refuses_with_uncommitted_tracked_changes(repo: Path):
    middle = _hashes(repo)[1]
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DirtyWorkTreeError):
        checkout_ref(str(repo), middle)
    assert (repo / "a.txt").read_text(encoding="utf-8") == "dirty\n"


def test_untracked_files_do_not_block(repo: Path):
    middle = _hashes(repo)[1]
    (repo / "scratch.txt").write_text("x\n", encoding="utf-8")
    assert checkout_ref(str(repo), middle)["head"] == middle


@pytest.mark.parametrize("ref", ["", "--orphan", "not-a-branch", "deadbeefdeadbeef"])
def test_rejects_unknown_or_option_refs(repo: Path, ref: str):
    with pytest.raises(ValueError):
        checkout_ref(str(repo), ref)
