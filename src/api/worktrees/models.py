"""Value types for Cuttle-managed git worktrees.

Pure data. Identity is the stable ``checkout_uid`` — never the branch, whose
name may change without moving the directory. A ``primary`` checkout is the
project's registered path; only worktrees get a persisted row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

CheckoutKind = Literal["primary", "worktree"]
CheckoutState = Literal["creating", "ready", "removing", "missing", "broken"]


@dataclass(frozen=True)
class WorktreeRoot:
    """Where new worktrees are created (``is_default`` = built-in home root)."""

    root: str
    is_default: bool


@dataclass(frozen=True)
class Checkout:
    """One execution checkout of a project (its primary path or a worktree)."""

    checkout_uid: str
    project_id: int
    kind: CheckoutKind
    path: str
    git_worktree_name: Optional[str]
    base_branch: str
    branch: Optional[str]
    state: CheckoutState
    created_at: str
    updated_at: str
    last_used: str
