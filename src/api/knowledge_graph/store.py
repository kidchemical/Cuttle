"""Provider-neutral storage identity: one directory per project + checkout.

The facade owns *where* an index lives (stable, branch-independent, per-worktree);
the provider owns *what* is inside it. ``store_root`` comes from the provider
manifest, so each engine keeps its own namespace and never collides.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import cuttle_home

from api.knowledge_graph.manifest import ProviderInfo, manifest_summary
from api.knowledge_graph.protocol import GraphStore

PRIMARY_CHECKOUT = "primary"


def store_root_path(info: ProviderInfo) -> Path:
    return cuttle_home() / info.store_root


def checkout_key(checkout_uid: Optional[str]) -> str:
    from api.worktrees.identity import validate_segment

    raw = str(checkout_uid or "").strip()
    return validate_segment(raw) if raw else PRIMARY_CHECKOUT


def project_key(project: Dict[str, Any]) -> str:
    from api.worktrees.identity import repo_dir_id

    return repo_dir_id(
        str(project.get("name") or "project"),
        project_id=project.get("id"),
        primary_path=project.get("resolved_path") or project.get("path"),
    )


def build_store(info: ProviderInfo, *, project: Dict[str, Any], execution_path: str,
                checkout_uid: Optional[str]) -> GraphStore:
    return GraphStore(
        provider_id=info.id,
        store_root=store_root_path(info),
        project_key=project_key(project),
        checkout_key=checkout_key(checkout_uid),
        project=project,
        execution_path=execution_path,
        checkout_uid=checkout_uid,
    )


def git_head(path: str) -> str:
    return _git(path, "rev-parse", "HEAD")


def git_branch(path: str) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD")


def _git(path: str, *args: str) -> str:
    if not path:
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", os.path.abspath(os.path.expanduser(str(path))), *args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def list_stored(info: ProviderInfo) -> List[Dict[str, Any]]:
    """Every store directory present on disk for a provider (read-only)."""
    root = store_root_path(info)
    rows: List[Dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "vault"):
        for checkout_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            meta_path = checkout_dir / "meta.json"
            if not meta_path.is_file():
                continue
            store = GraphStore(info.id, root, project_dir.name, checkout_dir.name,
                               {}, "", None)
            meta = store.read_meta()
            rows.append({
                "provider": info.id,
                "project_key": project_dir.name,
                "checkout_key": checkout_dir.name,
                "store_dir": str(checkout_dir),
                **{k: meta.get(k) for k in ("project_id", "project_name", "path",
                                            "branch", "head", "indexed_at",
                                            "nodes", "edges", "code_files")},
            })
    return rows


__all__ = [
    "PRIMARY_CHECKOUT", "ProviderInfo", "GraphStore", "manifest_summary",
    "store_root_path", "checkout_key", "project_key", "build_store",
    "git_head", "git_branch", "list_stored",
]
