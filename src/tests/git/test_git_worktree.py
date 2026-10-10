"""git_service worktree operations against real temp repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api.git_service import (
    GitError,
    branch_checked_out_in_worktree,
    worktree_add,
    worktree_list,
    worktree_prune,
    worktree_remove,
)


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


def test_add_lists_and_detects_branch(tmp_path):
    repo = _repo(tmp_path / "primary")
    target = tmp_path / "wt-new"
    out = worktree_add(repo, path=str(target), new_branch="cuttle/x", base_ref="main")
    assert out["branch"] == "cuttle/x"
    assert target.is_dir()
    paths = {e["path"] for e in worktree_list(str(repo))}
    assert str(target).rstrip("/") in {p.rstrip("/") for p in paths}
    assert branch_checked_out_in_worktree(str(repo), "cuttle/x") == str(target)
    assert branch_checked_out_in_worktree(str(repo), "nope") is None


def test_add_refuses_base_branch_checked_out_in_primary(tmp_path):
    repo = _repo(tmp_path / "primary")
    with pytest.raises(GitError, match="already checked out"):
        worktree_add(repo, path=str(tmp_path / "wt-main"), new_branch="main", base_ref="main")


def test_add_refuses_duplicate_branch(tmp_path):
    repo = _repo(tmp_path / "primary")
    worktree_add(repo, path=str(tmp_path / "wt-a"), new_branch="cuttle/dup", base_ref="main")
    with pytest.raises(GitError, match="already checked out"):
        worktree_add(repo, path=str(tmp_path / "wt-b"), new_branch="cuttle/dup", base_ref="main")


def test_add_refuses_nonempty_path(tmp_path):
    repo = _repo(tmp_path / "primary")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "junk.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(GitError, match="not empty"):
        worktree_add(repo, path=str(occupied), new_branch="cuttle/occ", base_ref="main")


def test_remove_refuses_dirty_then_forces(tmp_path):
    repo = _repo(tmp_path / "primary")
    target = tmp_path / "wt-clean"
    worktree_add(repo, path=str(target), new_branch="cuttle/clean", base_ref="main")
    (target / "dirty.txt").write_text("wip\n", encoding="utf-8")
    with pytest.raises(GitError, match="uncommitted changes"):
        worktree_remove(str(repo), str(target))
    assert target.is_dir()
    out = worktree_remove(str(repo), str(target), force=True)
    assert out["removed"] is True and out["forced"] is True
    assert not target.exists()


def test_remove_missing_path_prunes_without_error(tmp_path):
    repo = _repo(tmp_path / "primary")
    out = worktree_remove(str(repo), str(tmp_path / "gone"))
    assert out["removed"] is False and out["pruned"] is True


def test_prune_ok(tmp_path):
    repo = _repo(tmp_path / "primary")
    assert worktree_prune(str(repo)) == {"pruned": True}
