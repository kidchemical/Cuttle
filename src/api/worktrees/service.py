"""Worktree lifecycle service — composes git_service + identity + registry.

Headless (no GUI). Callers (CLI, routes, chat binding) own the rollout flag and
the user authorization; this service only enforces filesystem safety:

- the target lives inside a managed root (never an arbitrary path),
- git refuses a branch already checked out elsewhere (primary untouched),
- a dirty worktree is never removed without ``force``,
- a created worktree is registered; a registry failure rolls the git worktree
  back so nothing is left unregistered.

No automatic/destructive cleanup is performed here — ``reconcile_worktrees``
only marks state and prunes stale admin entries.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.git_refs import validate_branch_name

from api import git_service
from api.git_service import GitError
from api.worktrees import registry
from api.worktrees.identity import new_worktree_id, worktree_dir
from api.worktrees.models import Checkout
from api.worktrees.roots import (
    is_under_managed_root,
    managed_worktree_roots,
    resolve_worktrees_root,
)


class WorktreeError(RuntimeError):
    """Worktree lifecycle refused or failed."""


def create_worktree(
    *,
    project_id: int,
    project_name: str,
    primary_path: str,
    base_branch: str,
    branch: str,
    settings: Optional[Mapping] = None,
    db_path_override: Optional[Path] = None,
) -> Checkout:
    """Create a registered worktree on a fresh ``branch`` off ``base_branch``."""
    root = resolve_worktrees_root(settings)
    if root is None:
        raise WorktreeError("The configured worktree root is not usable on this host.")
    branch = validate_branch_name(branch)
    worktree_id = new_worktree_id()
    target = worktree_dir(
        project_name, worktree_id=worktree_id, project_id=project_id, root=root.root
    )
    if not is_under_managed_root(str(target), managed_worktree_roots(settings)):
        raise WorktreeError("Resolved worktree path is outside the managed root.")

    git_service.repo_work_tree(primary_path)  # NotARepositoryError if not a repo
    try:
        git_service.worktree_add(
            primary_path, path=str(target), new_branch=branch,
            base_ref=base_branch or "HEAD",
        )
    except GitError as exc:
        raise WorktreeError(str(exc)) from exc

    try:
        return registry.create_checkout(
            int(project_id), str(target), base_branch=base_branch or "",
            branch=branch, git_worktree_name=worktree_id, state="ready",
            db_path_override=db_path_override,
        )
    except Exception:
        try:
            git_service.worktree_remove(primary_path, str(target), force=True)
        except Exception:
            pass
        raise


def list_worktrees(
    project_id: Optional[int] = None,
    *,
    db_path_override: Optional[Path] = None,
) -> List[Checkout]:
    return registry.list_checkouts(
        project_id, kind="worktree", db_path_override=db_path_override
    )


def remove_worktree(
    checkout_uid: str,
    *,
    primary_path: str,
    force: bool = False,
    db_path_override: Optional[Path] = None,
    prune: bool = True,
) -> Dict[str, Any]:
    """Remove a registered worktree. Refuses dirty state unless ``force``."""
    checkout = registry.get_checkout(checkout_uid, db_path_override=db_path_override)
    if checkout is None:
        raise WorktreeError("Unknown checkout.")
    try:
        result = git_service.worktree_remove(primary_path, checkout.path, force=force)
    except GitError as exc:
        raise WorktreeError(str(exc)) from exc
    registry.remove_checkout(checkout_uid, db_path_override=db_path_override)
    if prune:
        try:
            git_service.worktree_prune(primary_path)
        except GitError:
            pass
    return {"checkout_uid": checkout_uid, **result}


def discover_worktrees(
    *,
    primary_path: str,
    settings: Optional[Mapping] = None,
    db_path_override: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Managed-root git worktrees that are not in the registry (orphans)."""
    roots = managed_worktree_roots(settings)
    known = {
        os.path.normcase(os.path.abspath(c.path))
        for c in registry.list_checkouts(db_path_override=db_path_override)
    }
    found: List[Dict[str, Any]] = []
    for entry in git_service.worktree_list(primary_path):
        path = entry.get("path") or ""
        if not path or not is_under_managed_root(path, roots):
            continue
        if os.path.normcase(os.path.abspath(path)) in known:
            continue
        found.append(entry)
    return found


def reconcile_worktrees(
    *,
    primary_path: Optional[str] = None,
    db_path_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mark vanished checkouts ``missing`` and prune stale git admin entries."""
    out: Dict[str, Any] = registry.reconcile_checkouts(db_path_override=db_path_override)
    out["pruned"] = False
    if primary_path:
        try:
            git_service.worktree_prune(primary_path)
            out["pruned"] = True
        except GitError:
            pass
    return out
