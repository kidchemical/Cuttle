"""Refuse session-destroying process kills (PID 1 / ancestors / stale PIDs).

``list_descendant_pids(1)`` is every process on the machine. Signaling that
tree SIGKILLs the user's GNOME session. Never enumerate or kill that tree.
"""

from __future__ import annotations

import os
from typing import Optional, Set


def coerce_pid(pid) -> Optional[int]:
    """Return a real positive PID, or None. Bools are rejected (``True is 1``)."""
    if isinstance(pid, bool) or pid is None:
        return None
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_i <= 0:
        return None
    return pid_i


def protected_pids() -> Set[int]:
    """PIDs that must never be signaled: init, self, parent, and ancestors."""
    pids: Set[int] = {1, os.getpid(), os.getppid()}
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        for parent in proc.parents():
            try:
                pids.add(int(parent.pid))
            except Exception:
                continue
    except Exception:
        pass
    return {p for p in pids if p > 0}


def is_forbidden_kill_target(pid) -> bool:
    pid_i = coerce_pid(pid)
    if pid_i is None:
        return True
    if pid_i <= 1:
        return True
    return pid_i in protected_pids()


def is_descendant_of(pid: int, ancestor: int) -> bool:
    if pid <= 1 or ancestor <= 0 or pid == ancestor:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        return any(int(p.pid) == int(ancestor) for p in proc.parents())
    except Exception:
        return False


def may_kill_pid(pid, *, require_descendant_of: Optional[int] = None) -> bool:
    """True only if we may signal ``pid`` without risking the login session."""
    pid_i = coerce_pid(pid)
    if pid_i is None or is_forbidden_kill_target(pid_i):
        return False
    ancestor = os.getpid() if require_descendant_of is None else int(require_descendant_of)
    if ancestor <= 0:
        return False
    if pid_i == ancestor:
        return False
    return is_descendant_of(pid_i, ancestor)
