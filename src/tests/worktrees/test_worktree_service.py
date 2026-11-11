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


def _branch_exists(repo, branch):
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def test_remove_deletes_worktree_branch(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    assert _branch_exists(repo, checkout.branch) is True
    out = service.remove_worktree(
        checkout.checkout_uid, primary_path=str(repo), db_path_override=_db(tmp_path)
    )
    assert out["removed"] is True and out["branch_deleted"] is True
    assert _branch_exists(repo, checkout.branch) is False


def test_project_root_places_new_worktrees(tmp_path, monkeypatch):
    big = tmp_path / "big-drive"
    _, checkout = _create(tmp_path, monkeypatch, settings={"project_roots": {"1": str(big)}})
    assert checkout.path.startswith(str(big))


def test_move_follows_the_project_root_and_keeps_work(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    old = Path(checkout.path)
    (old / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    big = tmp_path / "big-drive"
    result = service.move_worktree(
        checkout.checkout_uid, project_name="Demo", primary_path=str(repo),
        settings={"project_roots": {"1": str(big)}}, db_path_override=_db(tmp_path),
    )
    new = Path(result["path"])
    assert result["moved"] and str(new).startswith(str(big)) and new.name == old.name
    assert not old.exists() and (new / "wip.txt").read_text(encoding="utf-8") == "unsaved\n"
    row = registry.get_checkout(checkout.checkout_uid, db_path_override=_db(tmp_path))
    assert row.path == str(new) and row.branch == "cuttle/abc"
    assert _git("status", "--porcelain", cwd=new).stdout.strip() == "?? wip.txt"
    # Already there: a second move is a no-op.
    again = service.move_worktree(
        checkout.checkout_uid, project_name="Demo", primary_path=str(repo),
        settings={"project_roots": {"1": str(big)}}, db_path_override=_db(tmp_path),
    )
    assert again["moved"] is False


def test_move_to_explicit_path_refuses_existing_target(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    taken = tmp_path / "taken"
    taken.mkdir()
    with pytest.raises(service.WorktreeError):
        service.move_worktree(
            checkout.checkout_uid, project_name="Demo", primary_path=str(repo),
            to_path=str(taken), db_path_override=_db(tmp_path),
        )
    assert Path(checkout.path).is_dir()
    with pytest.raises(service.WorktreeError):
        service.move_worktree(
            checkout.checkout_uid, project_name="Demo", primary_path=str(repo),
            to_path="relative/dir", db_path_override=_db(tmp_path),
        )


def test_move_to_another_drive_copies_then_repairs(tmp_path, monkeypatch):
    repo, checkout = _create(tmp_path, monkeypatch)
    old = Path(checkout.path)
    (old / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    monkeypatch.setattr(git_service, "_same_device", lambda source, parent: False)
    target = tmp_path / "other-drive" / "wt"
    result = service.move_worktree(
        checkout.checkout_uid, project_name="Demo", primary_path=str(repo),
        to_path=str(target), db_path_override=_db(tmp_path),
    )
    assert result["copied"] and not result["source_left_behind"] and not old.exists()
    assert _git("status", "--porcelain", cwd=target).stdout.strip() == "?? wip.txt"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=target).stdout.strip() == "cuttle/abc"
    listed = _git("worktree", "list", "--porcelain", cwd=repo).stdout
    assert str(target) in listed and str(old) not in listed
