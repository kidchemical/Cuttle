"""Worktree-root resolution: the configured root, its previous roots, defaults.

Pure decisions over an explicit ``settings`` mapping (the ``worktrees`` block in
``settings.json``) — no global reads, no filesystem writes. The built-in default
is ``core.runtime_paths.worktrees_dir()`` (``<home>/worktrees``).

Cross-machine safety mirrors the platform-aware path rule: a setting that is not
an absolute path on *this* host (a ``D:\\worktrees`` synced from Windows, or a
drive root) resolves to ``None`` so the feature fails closed instead of guessing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from core.runtime_paths import worktrees_dir

from api.worktrees.models import WorktreeRoot


def default_worktrees_dir() -> str:
    """Built-in root (``<home>/worktrees``) as a resolved string."""
    return str(worktrees_dir())


def _expand(raw: str, home: Optional[str]) -> str:
    if raw == "~":
        return home or os.path.expanduser("~")
    if raw.startswith("~/") or raw.startswith("~\\"):
        return os.path.join(home or os.path.expanduser("~"), raw[2:])
    return os.path.expanduser(raw)


def _is_filesystem_root(directory: str) -> bool:
    parent = os.path.dirname(directory)
    return parent == directory


def validate_root(raw: str, *, home: Optional[str] = None) -> Optional[str]:
    """Resolve one configured root, or ``None`` when unusable on this host.

    ``None`` covers: not absolute here, a filesystem/drive root, or empty after
    trimming. Callers treat ``None`` as "feature unavailable", never as a
    fallback path.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    expanded = _expand(text, home)
    if not os.path.isabs(expanded):
        return None
    resolved = os.path.abspath(expanded)
    if _is_filesystem_root(resolved):
        return None
    return resolved


def resolve_worktrees_root(
    settings: Optional[Mapping[str, object]],
    *,
    home: Optional[str] = None,
) -> Optional[WorktreeRoot]:
    """The active worktree root: configured, else the built-in default.

    ``None`` when a non-empty configured root is unusable on this host.
    """
    cfg = settings if isinstance(settings, Mapping) else {}
    raw = str(cfg.get("root") or "").strip()
    if not raw:
        return WorktreeRoot(default_worktrees_dir(), True)
    resolved = validate_root(raw, home=home)
    if resolved is None:
        return None
    return WorktreeRoot(resolved, False)


def managed_worktree_roots(
    settings: Optional[Mapping[str, object]],
    *,
    home: Optional[str] = None,
) -> tuple[str, ...]:
    """Every root that may hold T3-style managed worktrees on this host.

    Default first, then the configured root, then each valid previous root.
    Order-stable and de-duplicated (case-insensitive on Windows).
    """
    cfg = settings if isinstance(settings, Mapping) else {}
    candidates = [default_worktrees_dir()]
    configured = validate_root(str(cfg.get("root") or ""), home=home)
    if configured is not None:
        candidates.append(configured)
    previous = cfg.get("previous_roots")
    if isinstance(previous, (list, tuple)):
        for entry in previous:
            resolved = validate_root(str(entry or ""), home=home)
            if resolved is not None:
                candidates.append(resolved)

    seen: set[str] = set()
    out: list[str] = []
    for root in candidates:
        key = os.path.normcase(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return tuple(out)


def rewrite_protected_prefixes(
    settings: Optional[Mapping[str, object]],
    *,
    home: Optional[str] = None,
) -> tuple[str, ...]:
    """Roots the Windows path rewriter must not treat as a Cuttle checkout."""
    return managed_worktree_roots(settings, home=home)


def is_under_managed_root(path: str, roots: tuple[str, ...]) -> bool:
    """True when ``path`` is a managed worktree root or inside one."""
    target = str(path or "").strip()
    if not target:
        return False
    normalized = os.path.normcase(os.path.abspath(target)).rstrip(os.sep)
    for root in roots:
        candidate = os.path.normcase(os.path.abspath(root)).rstrip(os.sep)
        if not candidate:
            continue
        if normalized == candidate or normalized.startswith(candidate + os.sep):
            return True
    return False
