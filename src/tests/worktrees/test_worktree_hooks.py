"""Setup hooks (.cuttle/worktrees.yaml) + non-destructive cleanup safeguards."""

from __future__ import annotations

import os
from pathlib import Path

from api.worktrees import cleanup, hooks, registry


def _project_with_hook(tmp_path, body: str) -> Path:
    proj = tmp_path / "proj"
    (proj / ".cuttle").mkdir(parents=True)
    (proj / ".cuttle" / "worktrees.yaml").write_text(body, encoding="utf-8")
    return proj


def test_hook_absent_is_none(tmp_path):
    assert hooks.load_setup_hook(str(tmp_path)) is None
    assert hooks.run_setup_hook(str(tmp_path), str(tmp_path))["reason"] == "no_hook"


def test_hook_parses_and_runs_in_worktree(tmp_path):
    proj = _project_with_hook(tmp_path, "setup:\n  run_posix: touch setup-ran\n  timeout: 5\n")
    hook = hooks.load_setup_hook(str(proj))
    assert hook is not None
    assert hook["run_posix"] == "touch setup-ran"
    assert hook["timeout"] == 5

    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = hooks.run_setup_hook(str(proj), str(worktree))
    assert result["ran"] is True and result["ok"] is True
    assert (worktree / "setup-ran").exists()


def test_hook_without_posix_command_is_not_run_on_posix(tmp_path):
    if os.name == "nt":
        return  # a Windows-only hook IS the platform command there
    proj = _project_with_hook(tmp_path, "setup:\n  run: echo windows-only\n")
    assert hooks.command_for_platform(hooks.load_setup_hook(str(proj))) is None


def test_cleanup_lists_missing_checkouts(tmp_path):
    db = tmp_path / "projects.db"
    registry.create_checkout(1, str(tmp_path / "gone"), checkout_uid="m", db_path_override=db)
    cleanup.reconcile(db_path_override=db)
    missing = cleanup.list_missing(db_path_override=db)
    assert [c.checkout_uid for c in missing] == ["m"]
