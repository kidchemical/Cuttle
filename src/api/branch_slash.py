"""Native ``/branch`` Cuttle control command.

A global (every-project) chat command for reading and switching the git
branch of the chat's working project. Handled before sticky/starred agent
prefixing, the router, and any agent dispatch, so it never becomes a model
turn.

Subcommands:

- ``/branch``              — show the current branch (status).
- ``/branch list``         — list local/remote branches, current marked.
- ``/branch <name>``       — switch the working branch (owner, clean tree).

Git behavior lives in ``api.git_service`` (single owner); this module only
parses the chat message and shapes the reply.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_STATUS_TOKENS = ("status", "show", "current")
_LIST_TOKENS = ("list", "ls", "branches")


def _strip_sticky(message: str) -> str:
    """Drop a leading sticky agent prefix (``/cursor /branch …``)."""
    raw = (message or "").strip()
    for _ in range(2):
        try:
            from api.starred_slash import sticky_prefix_from_text
        except Exception:
            return raw
        prefix = sticky_prefix_from_text(raw)
        if not prefix:
            return raw
        stripped = raw[len(prefix.strip()):].lstrip()
        if not stripped.startswith("/"):
            return raw
        raw = stripped
    return raw


def parse_branch_slash(message: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Parse ``/branch …`` → ``(mode, opts)`` or ``None``.

    Modes: ``status`` (no args), ``list`` (list/ls/branches), ``switch``
    (any other single argument = target branch name).
    """
    raw = _strip_sticky(message)
    low = raw.lower()
    if not (low == "/branch" or low.startswith("/branch ")):
        return None
    parts = raw.split()
    if len(parts) == 1:
        return "status", {}
    sub = parts[1].strip()
    low_sub = sub.lower()
    if low_sub in _STATUS_TOKENS:
        return "status", {}
    if low_sub in _LIST_TOKENS:
        return "list", {}
    return "switch", {"branch": sub}


def _resolve_cwd(project_path: Optional[str]) -> str:
    """Working directory for the branch operation (project path, else cwd)."""
    path = (project_path or "").strip()
    if path:
        return path
    try:
        from managers.project_manager import project_manager

        proj = project_manager.get_current_project()
        from api.git_service import resolve_repo_cwd

        return resolve_repo_cwd(proj)
    except Exception:
        import os

        return os.getcwd()


def handle_branch_slash(
    message: str,
    *,
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Execute a ``/branch …`` turn. ``None`` when not a branch command."""
    parsed = parse_branch_slash(message)
    if not parsed:
        return None
    mode, opts = parsed
    cwd = _resolve_cwd(project_path)

    from api.git_service import (
        GitError,
        current_branch_name,
        list_branches,
        use_working_branch,
    )

    if mode == "switch":
        # Writes are owner-only (mirrors /restart non-status gates).
        try:
            from flask import has_request_context

            if has_request_context():
                from api.http_authz import require_owner

                _user, err = require_owner()
                if err:
                    return {
                        "success": False,
                        "type": "git_branch",
                        "native_command": "/branch",
                        "response": "Owner privileges required to switch branches.",
                    }
        except Exception:
            pass

    try:
        if mode == "status":
            branch = current_branch_name(cwd)
            if not branch:
                return {
                    "success": False,
                    "type": "git_branch",
                    "native_command": "/branch",
                    "response": "Not a git repository (or no branch is checked out).",
                }
            return {
                "success": True,
                "type": "git_branch",
                "native_command": "/branch",
                "branch": branch,
                "response": f"Current branch: `{branch}`. Use `/branch list` or `/branch <name>`.",
            }

        if mode == "list":
            data = list_branches(cwd)
            branches = data.get("branches") or []
            current = data.get("current") or current_branch_name(cwd)
            if not branches:
                return {
                    "success": False,
                    "type": "git_branch",
                    "native_command": "/branch",
                    "response": "Not a git repository (no branches found).",
                }
            lines = [f"**Branches** (current: `{current or '—'}`)"]
            for b in branches:
                name = b.get("name") or ""
                if not name:
                    continue
                mark = "* " if b.get("current") else "  "
                remote = " (remote)" if b.get("remote") else ""
                lines.append(f"{mark}`{name}`{remote}")
            lines.append("")
            lines.append("Switch with `/branch <name>`.")
            return {
                "success": True,
                "type": "git_branch",
                "native_command": "/branch",
                "branch": current,
                "branches": [b.get("name") for b in branches if b.get("name")],
                "response": "\n".join(lines),
            }

        target = (opts.get("branch") or "").strip()
        result = use_working_branch(cwd, target)
        branch = result.get("branch") or target
        return {
            "success": True,
            "type": "git_branch",
            "native_command": "/branch",
            "branch": branch,
            "response": result.get("message") or f"Switched to branch: `{branch}`",
        }
    except ValueError as exc:
        return {
            "success": False,
            "type": "git_branch",
            "native_command": "/branch",
            "response": f"Invalid branch name: {exc}",
        }
    except GitError as exc:
        return {
            "success": False,
            "type": "git_branch",
            "native_command": "/branch",
            "response": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "success": False,
            "type": "git_branch",
            "native_command": "/branch",
            "response": f"Branch command failed: {exc}",
        }
