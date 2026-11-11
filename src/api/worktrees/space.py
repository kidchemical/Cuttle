"""Disk-space guard and settings view for worktree folders.

A worktree is a full checkout: on a large repository it takes tens of GB, so
Cuttle refuses to create one that would leave the target drive short instead of
filling it. Pure decisions over an explicit ``settings`` mapping (the
``worktrees`` block); the only filesystem reads are free space and the tracked
size of the base ref.

``min_free_gb`` is the space kept free on the drive after the new worktree
(default 10; ``0`` turns the check off).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from typing import Any, Dict, Optional

from api.worktrees.roots import resolve_worktrees_root, validate_root

DEFAULT_MIN_FREE_GB = 10.0
MIN_FREE_GB_RANGE = (0.0, 10000.0)
GB = 1024 ** 3


class SpaceError(ValueError):
    """A worktree settings value is not usable."""


def min_free_gb(settings: Optional[Mapping[str, object]]) -> float:
    """The configured reserve in GB (default when unset or invalid)."""
    cfg = settings if isinstance(settings, Mapping) else {}
    raw = cfg.get("min_free_gb")
    if raw is None or isinstance(raw, bool):
        return DEFAULT_MIN_FREE_GB
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_FREE_GB
    if value != value or not MIN_FREE_GB_RANGE[0] <= value <= MIN_FREE_GB_RANGE[1]:
        return DEFAULT_MIN_FREE_GB
    return value


def free_bytes(path: str) -> Optional[int]:
    """Free space on the drive holding ``path`` (its nearest existing parent)."""
    probe = os.path.abspath(str(path or ""))
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


def _existing(path: str) -> str:
    probe = os.path.abspath(str(path or ""))
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return probe


def same_drive(a: str, b: str) -> bool:
    """True when both paths sit on one filesystem (a move there needs no new space)."""
    try:
        return os.stat(_existing(a)).st_dev == os.stat(_existing(b)).st_dev
    except OSError:
        return False


def _gb(value: float) -> str:
    return f"{value / GB:.1f} GB"


def shortfall(
    *,
    target: str,
    needed_bytes: Optional[int],
    settings: Optional[Mapping[str, object]],
    free: Optional[int] = None,
) -> Optional[str]:
    """Why a worktree of ``needed_bytes`` must not go at ``target``; None when fine.

    Unknown free space or an unknown size never blocks: the guard only refuses
    what it measured.
    """
    reserve = min_free_gb(settings)
    if reserve <= 0:
        return None
    available = free_bytes(target) if free is None else free
    if available is None:
        return None
    needed = max(0, int(needed_bytes or 0))
    if available - needed >= reserve * GB:
        return None
    size = f"about {_gb(needed)}" if needed else "an unknown amount"
    return (
        f"Not enough free space for a new worktree in {target}: it needs {size}, "
        f"the drive has {_gb(available)} free, and Cuttle keeps {reserve:g} GB free. "
        "Choose another worktree folder (Settings → Agents → Worktrees, or "
        "`python -m api.worktrees root --set DIR`), or lower the reserve there."
    )


def validate_patch(patch: Any) -> Dict[str, Any]:
    """Normalize a settings patch: ``root`` / ``min_free_gb`` / ``project_roots``.

    ``project_roots`` maps a project id to a folder; an empty folder clears that
    project's entry. Raises :class:`SpaceError` naming the bad field.
    """
    if not isinstance(patch, Mapping):
        raise SpaceError("Expected an object.")
    out: Dict[str, Any] = {}
    if "root" in patch:
        raw = str(patch.get("root") or "").strip()
        if raw and validate_root(raw) is None:
            raise SpaceError("The worktree folder must be an absolute path on this machine (not a drive root).")
        out["root"] = validate_root(raw) or ""
    if "min_free_gb" in patch:
        raw = patch.get("min_free_gb")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise SpaceError("Free space to keep must be a number of GB.") from None
        if isinstance(raw, bool) or value != value or not MIN_FREE_GB_RANGE[0] <= value <= MIN_FREE_GB_RANGE[1]:
            raise SpaceError("Free space to keep must be between 0 and 10000 GB.")
        out["min_free_gb"] = value
    if "project_roots" in patch:
        table = patch.get("project_roots")
        if not isinstance(table, Mapping):
            raise SpaceError("project_roots must map a project id to a folder.")
        clean: Dict[str, str] = {}
        for key, raw in table.items():
            pid = str(key).strip()
            if not pid.isdigit():
                raise SpaceError(f"Unknown project id: {key!r}")
            text = str(raw or "").strip()
            if text and validate_root(text) is None:
                raise SpaceError(
                    f"The folder for project {pid} must be an absolute path on this machine (not a drive root).")
            clean[pid] = validate_root(text) or ""
        out["project_roots"] = clean
    return out


def apply_patch(block: Any, patch: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``worktrees`` settings block after ``patch`` (from :func:`validate_patch`).

    A replaced folder is remembered in ``previous_roots`` so worktrees already
    under it stay managed.
    """
    out = dict(block) if isinstance(block, Mapping) else {}
    previous = [str(p) for p in (out.get("previous_roots") or []) if str(p or "").strip()]

    def _retire(old: str, new: str) -> None:
        if old and old != new and old not in previous:
            previous.append(old)

    if "root" in patch:
        _retire(str(out.get("root") or "").strip(), patch["root"])
        out["root"] = patch["root"]
    if "min_free_gb" in patch:
        out["min_free_gb"] = patch["min_free_gb"]
    if "project_roots" in patch:
        table = {str(k): str(v) for k, v in dict(out.get("project_roots") or {}).items() if str(v or "").strip()}
        for pid, new in patch["project_roots"].items():
            _retire(table.get(pid, ""), new)
            if new:
                table[pid] = new
            else:
                table.pop(pid, None)
        out["project_roots"] = table
    out["previous_roots"] = previous
    return out


def describe(settings: Optional[Mapping[str, object]], projects: Optional[list] = None) -> Dict[str, Any]:
    """What the Settings page shows: folders, the reserve, free space per folder."""
    cfg = settings if isinstance(settings, Mapping) else {}
    from api.worktrees.roots import default_worktrees_dir

    def _folder(project_id: object = None) -> Dict[str, Any]:
        active = resolve_worktrees_root(cfg, project_id=project_id)
        path = active.root if active else ""
        free = free_bytes(path) if path else None
        return {"path": path, "usable": active is not None,
                "free_gb": round(free / GB, 1) if free is not None else None}

    table = cfg.get("project_roots") if isinstance(cfg.get("project_roots"), Mapping) else {}
    rows = []
    for project in projects or []:
        pid = str(project.get("id"))
        rows.append({"id": pid, "name": str(project.get("name") or pid),
                     "root": str(table.get(pid) or ""), "effective": _folder(pid)})
    return {
        "root": str(cfg.get("root") or ""),
        "default_root": default_worktrees_dir(),
        "effective": _folder(),
        "min_free_gb": min_free_gb(cfg),
        "default_min_free_gb": DEFAULT_MIN_FREE_GB,
        "limits": {"min_free_gb": list(MIN_FREE_GB_RANGE)},
        "projects": rows,
    }
