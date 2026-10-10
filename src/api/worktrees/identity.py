"""Stable worktree/repository directory identity.

The physical layout never depends on a branch name or a bare repository name:

    <home>/worktrees/<repo-id>/<worktree-id>/

- ``repo-id`` is ``<slug>-<hash>``: the human name plus a short digest of a
  stable basis (the registry project id, else the canonical primary path). Two
  checkouts that share a folder name therefore get different ``repo-id``\\ s.
- ``worktree-id`` is a generated ``wt-<hex>`` token, unique per worktree.
- The branch name lives only in registry metadata (``Checkout.branch``); it can
  change without moving the directory.

Both segments are validated before use so a hostile name can never escape the
worktrees root.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

from core.runtime_paths import worktrees_dir

_SLUG_INVALID = re.compile(r"[^A-Za-z0-9]+")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_segment(value: str) -> str:
    """Return a safe single path segment or raise ``ValueError``."""
    text = str(value or "").strip()
    if (
        not text
        or text in (".", "..")
        or not _SAFE_SEGMENT.match(text)
        or os.sep in text
        or "/" in text
        or (os.altsep and os.altsep in text)
    ):
        raise ValueError(f"unsafe worktree path segment: {value!r}")
    return text


def slugify(name: str, *, fallback: str = "repo", limit: int = 40) -> str:
    """Lowercase, dash-joined slug of a repository name."""
    slug = _SLUG_INVALID.sub("-", str(name or "")).strip("-").lower()
    return slug[:limit].strip("-") or fallback


def repo_dir_id(
    name: str,
    *,
    project_id: int | None = None,
    primary_path: str | None = None,
) -> str:
    """Stable repository directory id, independent of branch and name collision.

    Basis order: registry ``project_id`` (stable within the install), else the
    canonicalized ``primary_path``. Same basis → same id; different repo sharing
    a name → different id.
    """
    slug = slugify(name)
    if project_id is not None:
        basis = f"project:{int(project_id)}"
    else:
        basis = f"path:{os.path.abspath(os.path.expanduser(str(primary_path or slug)))}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:6]
    return validate_segment(f"{slug}-{digest}")


def new_worktree_id() -> str:
    """A fresh, unique, branch-independent worktree id."""
    return validate_segment(f"wt-{secrets.token_hex(4)}")


def worktree_dir(
    name: str,
    *,
    worktree_id: str,
    project_id: int | None = None,
    primary_path: str | None = None,
    root: str | None = None,
) -> Path:
    """Absolute worktree directory for a project's worktree id.

    ``<root>/<repo-id>/<worktree-id>`` — never derived from a branch. ``root``
    defaults to the built-in home root; a configured root is passed explicitly.
    """
    repo = repo_dir_id(name, project_id=project_id, primary_path=primary_path)
    base = Path(root) if root else worktrees_dir()
    return base / validate_segment(repo) / validate_segment(worktree_id)
