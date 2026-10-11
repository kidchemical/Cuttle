"""Worktrees — Cuttle's per-chat checkout identity and root resolution.

Phase W1 foundation (inert). Owns:

- ``models`` — ``Checkout`` / ``WorktreeRoot`` value types.
- ``roots`` — configured/default root resolution + managed-root tracking.
- ``registry`` — the ``checkouts`` table in the per-user ``projects.db``.

Git worktree lifecycle (add/list/remove) belongs to ``api.git_service`` in a
later phase; this package never runs git and never creates directories.
"""

from __future__ import annotations

from api.worktrees.models import Checkout, CheckoutKind, CheckoutState, WorktreeRoot
from api.worktrees.identity import (
    new_worktree_id,
    repo_dir_id,
    slugify,
    validate_segment,
    worktree_dir,
)
from api.worktrees.roots import (
    default_worktrees_dir,
    is_under_managed_root,
    managed_worktree_roots,
    resolve_worktrees_root,
    rewrite_protected_prefixes,
    validate_root,
)
from api.worktrees import binding, registry

FLAG_ID = "worktrees"


def is_worktrees_enabled() -> bool:
    """Rollout gate. Default OFF; callers must check before creating checkouts."""
    from api.experimental import is_enabled

    return bool(is_enabled(FLAG_ID))


__all__ = [
    "Checkout",
    "CheckoutKind",
    "CheckoutState",
    "WorktreeRoot",
    "default_worktrees_dir",
    "is_under_managed_root",
    "is_worktrees_enabled",
    "managed_worktree_roots",
    "new_worktree_id",
    "registry",
    "repo_dir_id",
    "resolve_worktrees_root",
    "rewrite_protected_prefixes",
    "slugify",
    "validate_root",
    "validate_segment",
    "worktree_dir",
]
