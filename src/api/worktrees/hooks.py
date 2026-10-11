"""Project setup hooks for new worktrees (opt-in, authorization owned by caller).

Reads ``{project}/.cuttle/worktrees.yaml``::

    setup:
      run: <Windows command>       # executed with cmd.exe on Windows
      run_posix: <POSIX command>   # executed with /bin/sh elsewhere
      timeout: 600                 # seconds, clamped

An absent/empty file means no hook. This module only parses and runs a command
with the worktree as cwd; it never runs anything automatically — the HTTP route
requires an owner and an explicit ``authorize_setup`` acknowledgement.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

try:  # PyYAML is a project dependency; stay importable if it is absent.
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency guard
    yaml = None

HOOK_RELATIVE = Path(".cuttle") / "worktrees.yaml"
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
_MAX_OUTPUT = 4000


def hook_file_path(project_path: str) -> Path:
    return Path(str(project_path or "")) / HOOK_RELATIVE


def load_setup_hook(project_path: str) -> Optional[Dict[str, Any]]:
    """Parsed ``setup`` block, or ``None`` when absent/empty/invalid."""
    path = hook_file_path(project_path)
    if yaml is None or not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    setup = data.get("setup") if isinstance(data, dict) else None
    if not isinstance(setup, dict):
        return None
    run = str(setup.get("run") or "").strip()
    run_posix = str(setup.get("run_posix") or "").strip()
    if not run and not run_posix:
        return None
    try:
        timeout = int(setup.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "run": run,
        "run_posix": run_posix,
        "timeout": max(1, min(timeout, MAX_TIMEOUT)),
    }


def command_for_platform(hook: Dict[str, Any]) -> Optional[str]:
    """The command to run on this OS, or ``None`` when the hook targets another."""
    if os.name == "nt":
        return hook.get("run") or hook.get("run_posix") or None
    return hook.get("run_posix") or None


def run_setup_hook(
    project_path: str,
    worktree_path: str,
    *,
    timeout: Optional[int] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run the project's setup hook in ``worktree_path`` (caller must authorize).

    Best-effort: never raises for a nonzero exit or timeout; a worktree is not
    rolled back because a setup command failed.
    """
    hook = load_setup_hook(project_path)
    if hook is None:
        return {"ran": False, "reason": "no_hook"}
    command = command_for_platform(hook)
    if not command:
        return {"ran": False, "reason": "no_command_for_platform"}
    cwd = str(worktree_path or "")
    if not os.path.isdir(cwd):
        return {"ran": False, "reason": "worktree_missing"}
    limit = max(1, min(int(timeout or hook["timeout"]), MAX_TIMEOUT))
    try:
        proc = subprocess.run(
            command, cwd=cwd, shell=True, capture_output=True, text=True,
            timeout=limit, env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        return {"ran": True, "ok": False, "reason": "timeout", "timeout": limit}
    except OSError as exc:
        return {"ran": False, "reason": f"spawn_failed: {exc}"}
    return {
        "ran": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-_MAX_OUTPUT:],
        "stderr": (proc.stderr or "")[-_MAX_OUTPUT:],
    }
