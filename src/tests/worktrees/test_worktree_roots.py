"""Worktree root resolution: defaults, overrides, previous roots, fail-closed."""

from __future__ import annotations

import os
from pathlib import Path

from api.worktrees import roots


def test_default_root_when_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    root = roots.resolve_worktrees_root({})
    assert root is not None and root.is_default
    assert Path(root.root) == tmp_path / "home" / "worktrees"


def test_configured_absolute_root_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    target = tmp_path / "custom-wt"
    root = roots.resolve_worktrees_root({"root": str(target)})
    assert root is not None and not root.is_default
    assert Path(root.root) == target


def test_tilde_expands_against_home(tmp_path):
    assert roots.validate_root("~/wt", home=str(tmp_path)) == os.path.abspath(str(tmp_path / "wt"))


def test_relative_root_fails_closed():
    assert roots.resolve_worktrees_root({"root": "relative/wt"}) is None
    assert roots.resolve_worktrees_root({"root": ""}).is_default


def test_filesystem_root_fails_closed():
    assert roots.validate_root(os.sep) is None


def test_foreign_windows_root_fails_closed_on_posix():
    if os.name == "nt":
        return
    assert roots.resolve_worktrees_root({"root": "D:\\worktrees"}) is None


def test_managed_roots_track_previous_and_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    cfg = {
        "root": str(tmp_path / "current"),
        "previous_roots": [str(tmp_path / "old"), "relative", str(tmp_path / "current")],
    }
    got = roots.managed_worktree_roots(cfg)
    assert str(tmp_path / "home" / "worktrees") in got
    assert str(tmp_path / "current") in got
    assert str(tmp_path / "old") in got
    assert "relative" not in got
    assert len(got) == len({os.path.normcase(p) for p in got})


def test_is_under_managed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    managed = roots.managed_worktree_roots({})
    inside = str(tmp_path / "home" / "worktrees" / "myrepo" / "abc123")
    assert roots.is_under_managed_root(inside, managed)
    assert not roots.is_under_managed_root(str(tmp_path / "elsewhere"), managed)


def test_rewrite_protected_prefixes_matches_managed_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    cfg = {"root": str(tmp_path / "custom"), "previous_roots": [str(tmp_path / "old")]}
    assert roots.rewrite_protected_prefixes(cfg) == roots.managed_worktree_roots(cfg)
