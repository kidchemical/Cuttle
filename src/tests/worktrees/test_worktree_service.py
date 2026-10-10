"""Worktree service: create/register/remove/discover/reconcile, fail-closed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api import git_service
from api.worktrees import registry
from api.worktrees import service


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


def _db(tmp_path) -> Path:
    return tmp_path / "projects.db"


def _create(tmp_path, monkeypatch, settings=None, branch="cuttle/abc"):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path / "primary")
    checkout = service.create_worktree(
        project_id=1, project_name="Demo", primary_path=str(repo),
        base_branch="main", branch=branch, settings=settings or {},
        db_path_override=_db(tmp_path),
    )
    return repo, checkout


def test_create_registers_and_lists(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    assert checkout.kind == "worktree"
    assert checkout.branch == "cuttle/abc"
    assert checkout.state == "ready"
    assert Path(checkout.path).is_dir()
    assert str(tmp_path / "home" / "worktrees") in checkout.path
    listed = service.list_worktrees(1, db_path_override=_db(tmp_path))
    assert [c.checkout_uid for c in listed] == [checkout.checkout_uid]


def test_configured_root_is_used(tmp_path, monkeypatch):
    root = tmp_path / "custom-root"
    _, checkout = _create(tmp_path, monkeypatch, settings={"root": str(root)})
    assert checkout.path.startswith(str(root))
    assert (root / "Demo").exists() or Path(checkout.path).parent.name.startswith("demo-")


def test_relative_root_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path / "primary")
    with pytest.raises(service.WorktreeError):
        service.create_worktree(
            project_id=1, project_name="Demo", primary_path=str(repo),
            base_branch="main", branch="cuttle/x", settings={"root": "relative/path"},
            db_path_override=_db(tmp_path),
        )


def test_remove_refuses_dirty_then_forces_and_deregisters(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    (Path(checkout.path) / "wip.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(service.WorktreeError):
        service.remove_worktree(checkout.checkout_uid, primary_path=str(repo),
                                db_path_override=_db(tmp_path))
    out = service.remove_worktree(checkout.checkout_uid, primary_path=str(repo),
                                  force=True, db_path_override=_db(tmp_path))
    assert out["removed"] is True
    assert not Path(checkout.path).exists()
    assert registry.get_checkout(checkout.checkout_uid, db_path_override=_db(tmp_path)) is None


def test_discover_finds_unregistered_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path / "primary")
    managed_root = tmp_path / "home" / "worktrees" / "orphan"
    target = managed_root / "wt-12345678"
    git_service.worktree_add(str(repo), path=str(target), new_branch="cuttle/orphan", base_ref="main")
    found = service.discover_worktrees(
        primary_path=str(repo), settings={}, db_path_override=_db(tmp_path)
    )
    assert any(Path(e["path"]) == target for e in found)


def test_reconcile_marks_missing(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    import shutil

    shutil.rmtree(checkout.path)
    out = service.reconcile_worktrees(primary_path=str(repo), db_path_override=_db(tmp_path))
    assert checkout.checkout_uid in out["marked_missing"]
    assert registry.get_checkout(checkout.checkout_uid, db_path_override=_db(tmp_path)).state == "missing"
