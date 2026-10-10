"""Checkout binding — resolve a chat/turn's checkout path and authorize it.

The decision layer the composition roots call. It owns:

- turning a ``checkout_uid`` into a usable execution path (refusing a checkout
  whose registry state is not ``ready``),
- checking that a checkout belongs to a project,
- listing a project's checkout paths for Git/filesystem authorization.

The registry is the authority for *identity*; git is the authority for *reality*
(``api.worktrees.service.discover_worktrees``/``reconcile_worktrees`` close the
gap). This module never runs git and never touches the Flask entry module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set

from api.worktrees import registry
from api.worktrees.models import Checkout

# States where a checkout's directory is usable as an execution root.
_USABLE_STATES = ("ready",)


def get_usable_checkout(
    checkout_uid: str,
    *,
    db_path_override: Optional[Path] = None,
) -> Optional[Checkout]:
    """The checkout when its state allows execution, else ``None``."""
    if not str(checkout_uid or "").strip():
        return None
    checkout = registry.get_checkout(checkout_uid, db_path_override=db_path_override)
    if checkout is None or checkout.state not in _USABLE_STATES:
        return None
    return checkout


def resolve_bound_checkout_path(
    checkout_uid: str,
    project_id: Optional[int] = None,
    *,
    db_path_override: Optional[Path] = None,
) -> Optional[str]:
    """Execution path for a bound checkout, or ``None`` when unusable/foreign.

    ``None`` means "fall back to the primary" at the caller; a checkout bound to
    a *different* project is treated as unusable, never silently honored.
    """
    checkout = get_usable_checkout(checkout_uid, db_path_override=db_path_override)
    if checkout is None:
        return None
    if project_id is not None and int(checkout.project_id) != int(project_id):
        return None
    return checkout.path


def checkout_belongs_to_project(
    checkout_uid: str,
    project_id: int,
    *,
    db_path_override: Optional[Path] = None,
) -> bool:
    return registry.checkout_belongs_to(
        checkout_uid, int(project_id), db_path_override=db_path_override
    )


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path or "")))


def checkout_paths_for_project(
    project_id: int,
    *,
    db_path_override: Optional[Path] = None,
) -> Set[str]:
    """Normalized paths of every registered checkout of a project."""
    return {
        _norm(c.path)
        for c in registry.list_checkouts(int(project_id), db_path_override=db_path_override)
        if c.path
    }


def is_checkout_path_for_project(
    path: str,
    project_id: int,
    *,
    db_path_override: Optional[Path] = None,
) -> bool:
    """True when ``path`` is a registered checkout of ``project_id``."""
    target = _norm(path)
    return bool(target) and target in checkout_paths_for_project(
        project_id, db_path_override=db_path_override
    )


def parse_session_id(sid_raw) -> Optional[int]:
    """Parse ``db_session_87`` / ``CH-000087`` / ``87`` to an int id, else None."""
    if sid_raw is None or str(sid_raw).strip() == "":
        return None
    s = str(sid_raw).strip()
    if s.startswith("db_session_"):
        s = s[len("db_session_"):]
    if s.upper().startswith("CH-"):
        bare = s.split("-", 1)[-1]
        try:
            return int(bare.lstrip("0") or "0")
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def _stored_session_project(sid_raw):
    """Stored project fields for a chat session handle, or ``{}``."""
    sid = parse_session_id(sid_raw)
    if sid is None:
        return {}
    try:
        from api.auth_db import get_auth_db
        return get_auth_db().get_session_project(sid) or {}
    except Exception:
        return {}


def resolve_request_checkout_path(
    data,
    *,
    db_path_override: Optional[Path] = None,
) -> str:
    """Execution path for the checkout a request/chat is bound to, or ''.

    Reads ``checkout_uid``/``project_id`` from the request, falling back to the
    stored auth session row. Returns '' when nothing is bound or the checkout is
    unusable/foreign — callers then use the project's primary path.
    """
    req = data if isinstance(data, dict) else {}
    uid = str(req.get("checkout_uid") or req.get("checkoutUid") or "").strip()
    pid = req.get("project_id", req.get("projectId"))
    if (not uid or pid in (None, "")) and (req.get("session_id") or req.get("chat_session_id")):
        stored = _stored_session_project(req.get("session_id") or req.get("chat_session_id"))
        if not uid:
            uid = str(stored.get("checkout_uid") or "").strip()
        if pid in (None, ""):
            pid = stored.get("project_id")
    if not uid:
        return ""
    try:
        pid_int = int(pid) if pid not in (None, "") else None
    except (TypeError, ValueError):
        pid_int = None
    return resolve_bound_checkout_path(
        uid, pid_int, db_path_override=db_path_override
    ) or ""
