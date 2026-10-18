"""Resolve the active project + checkout for a turn (provider-neutral).

Reuses the existing owners: ``managers.project_manager`` (the ``projects``
registry) and ``api.worktrees.registry`` (the ``checkouts`` table). No parallel
project registry. A worktree matches its own path (longest prefix wins) so an
index never leaks across branches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ResolvedContext:
    project: Dict[str, Any]
    execution_path: str
    checkout_uid: Optional[str]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))


def _under(child: str, parent: str) -> bool:
    if child == parent:
        return True
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def _projects() -> List[Dict[str, Any]]:
    try:
        from managers.project_manager import project_manager
    except Exception:
        return []
    try:
        return project_manager.get_projects()
    except Exception:
        return []


def _checkout_rows() -> List[Dict[str, Any]]:
    try:
        from api.worktrees.registry import list_checkouts
    except Exception:
        return []
    try:
        return [{"checkout_uid": c.checkout_uid, "project_id": c.project_id,
                 "path": c.path, "branch": c.branch, "state": c.state}
                for c in list_checkouts()]
    except Exception:
        return []


def find_project(project_id: Optional[int] = None,
                 project_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    projects = _projects()
    if project_id is not None:
        for p in projects:
            if p.get("id") == project_id:
                return p
        return None
    if project_name:
        needle = str(project_name).strip().lower()
        for p in projects:
            if str(p.get("name") or "").strip().lower() == needle:
                return p
    return None


def match_path(target: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Map an execution path to ``(project, checkout_uid, matched_path)``."""
    if not target:
        return None, None, ""
    wanted = _norm(target)
    projects = {p.get("id"): p for p in _projects()}

    best: Tuple[int, Optional[Dict[str, Any]], Optional[str], str] = (0, None, None, "")
    for row in _checkout_rows():
        path = _norm(row["path"])
        project = projects.get(row["project_id"])
        if project is None:
            continue
        if _under(wanted, path) and len(path) > best[0]:
            best = (len(path), project, row["checkout_uid"], row["path"])
    if best[1] is not None:
        return best[1], best[2], best[3]

    best = (0, None, None, "")
    for p in projects.values():
        resolved = p.get("resolved_path") or p.get("path")
        if not resolved:
            continue
        path = _norm(resolved)
        if _under(wanted, path) and len(path) > best[0]:
            best = (len(path), p, None, resolved)
    return best[1], best[2], best[3]


def resolve(cwd: Optional[str] = None, *, project_id: Optional[int] = None,
            project_name: Optional[str] = None,
            checkout_uid: Optional[str] = None) -> Optional[ResolvedContext]:
    """Resolve the active project + checkout for a turn.

    Precedence: explicit project id/name, else the working directory (or
    ``CUTTLE_AGENT_PROJECT_PATH``), matched against worktrees then primaries.
    """
    project: Optional[Dict[str, Any]] = None
    matched_path = ""
    resolved_checkout = checkout_uid

    if project_id is not None or project_name:
        project = find_project(project_id, project_name)

    if project is None:
        target = cwd or os.environ.get("CUTTLE_AGENT_PROJECT_PATH") or os.getcwd()
        m_project, m_checkout, matched_path = match_path(target)
        project = m_project
        if resolved_checkout is None:
            resolved_checkout = m_checkout

    if project is None:
        return None

    execution_path = matched_path or project.get("resolved_path") or project.get("path") or ""
    return ResolvedContext(project=project, execution_path=execution_path,
                           checkout_uid=resolved_checkout)
