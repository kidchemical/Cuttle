"""Non-destructive worktree cleanup safeguards.

Only reads and reconciliation live here. Removing a worktree directory is an
explicit, per-checkout action owned by ``api.worktrees.service`` (owner-gated at
the route, dirty-guarded). There is no automatic/deferred deletion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from api.worktrees import registry, service
from api.worktrees.models import Checkout


def list_orphans(
    *,
    primary_path: str,
    settings=None,
    db_path_override: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Managed-root git worktrees with no registry row (leftovers)."""
    return service.discover_worktrees(
        primary_path=primary_path, settings=settings, db_path_override=db_path_override
    )


def list_missing(
    project_id: Optional[int] = None,
    *,
    db_path_override: Optional[Path] = None,
) -> List[Checkout]:
    """Registered checkouts whose directory is gone."""
    return [
        c for c in registry.list_checkouts(project_id, db_path_override=db_path_override)
        if c.state == "missing"
    ]


def reconcile(
    *,
    primary_path: Optional[str] = None,
    db_path_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """Mark vanished checkouts ``missing`` and prune stale git admin entries."""
    return service.reconcile_worktrees(
        primary_path=primary_path, db_path_override=db_path_override
    )
