"""Worktree/repository directory identity: stable, branch-free, collision-safe."""

from __future__ import annotations

import pytest

from api.worktrees import identity


def test_repo_dir_id_stable_for_same_project():
    a = identity.repo_dir_id("Cuttle", project_id=4)
    b = identity.repo_dir_id("Cuttle", project_id=4)
    assert a == b
    assert a.startswith("cuttle-")


def test_repo_dir_id_disambiguates_same_name():
    one = identity.repo_dir_id("cuttle", project_id=1)
    two = identity.repo_dir_id("cuttle", project_id=2)
    assert one != two


def test_repo_dir_id_path_basis_is_stable_and_distinct(tmp_path):
    a = identity.repo_dir_id("Demo", primary_path=str(tmp_path / "a"))
    b = identity.repo_dir_id("Demo", primary_path=str(tmp_path / "b"))
    assert a != b
    assert a.startswith("demo-")
    assert a == identity.repo_dir_id("Demo", primary_path=str(tmp_path / "a"))


def test_slugify_sanitizes():
    assert identity.slugify("My Repo!!") == "my-repo"
    assert identity.slugify("") == "repo"


def test_new_worktree_id_unique_and_branch_free():
    ids = {identity.new_worktree_id() for _ in range(50)}
    assert len(ids) == 50
    for worktree_id in ids:
        assert worktree_id.startswith("wt-")
        assert identity.validate_segment(worktree_id) == worktree_id


def test_worktree_dir_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    repo = identity.repo_dir_id("Cuttle", project_id=4)
    path = identity.worktree_dir("Cuttle", worktree_id="wt-abc12345", project_id=4)
    assert path == tmp_path / "home" / "worktrees" / repo / "wt-abc12345"


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", "-lead", "a b"])
def test_validate_segment_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        identity.validate_segment(bad)
