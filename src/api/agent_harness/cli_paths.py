"""Host-local executable overrides shared by bundled harness discovery.

Saved paths are exact launch targets, including wrappers. Never replace them
with a sibling binary or fall through to an unsandboxed installation.
"""
from __future__ import annotations

import os

from managers.settings_manager import get_settings_manager


CLI_PATH_ENV = {
    "antigravity": "AGY_CLI_PATH",
    "claude": "CLAUDE_CLI_PATH",
    "codex": "CODEX_CLI_PATH",
    "cursor": "CURSOR_CLI_PATH",
    "deepseek": "DSH_CLI_PATH",
    "hermes": "HERMES_CLI_PATH",
    "muse": "MUSE_CLI_PATH",
    "opencode": "OPENCODE_CLI_PATH",
}


def _paths() -> dict:
    paths = get_settings_manager().get_setting("agent_cli_paths")
    return paths if isinstance(paths, dict) else {}


def executable_error(path: str) -> str:
    if not os.path.isabs(path):
        return "Use an absolute path on the Cuttle host, without arguments."
    if not os.path.isfile(path):
        return "Executable does not exist or is not a file on the Cuttle host."
    if os.name != "nt" and not os.access(path, os.X_OK):
        return "File is not executable. Make the wrapper executable first (chmod +x)."
    return ""


def cli_path_override(agent_id: str) -> tuple[bool, str | None]:
    """Return (configured, executable); a missing saved target fails closed.

    Read on discovery rather than mutating process environment: concurrent
    turns keep their own launch targets, and the next launch sees a saved edit.
    Environment overrides and automatic discovery remain with each adapter.
    """
    path = _paths().get(agent_id)
    if not path:
        return False, None
    if not isinstance(path, str) or executable_error(path):
        return True, None
    return True, path


def describe_cli_paths() -> dict:
    paths = _paths()
    return {
        agent: {
            "path": paths.get(agent, ""),
            "env_var": env,
            "env_path": os.environ.get(env, "").strip(),
            "error": executable_error(paths[agent]) if paths.get(agent) else "",
        }
        for agent, env in CLI_PATH_ENV.items()
    }


def validate_cli_paths(raw: object) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("cli_paths must map agent ids to executable paths.")
    clean = {}
    for agent, value in raw.items():
        if agent not in CLI_PATH_ENV:
            raise ValueError(f"CLI path overrides are not supported for {agent}.")
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in "\x00\r\n"):
            raise ValueError(f"cli_paths.{agent} must be a path string without control characters.")
        path = value.strip()
        error = executable_error(path) if path else ""
        if error:
            raise ValueError(f"{agent}: {error}")
        clean[agent] = path
    return clean


def save_cli_paths(patch: dict) -> None:
    def update(current):
        merged = dict(current) if isinstance(current, dict) else {}
        for agent, path in patch.items():
            if path:
                merged[agent] = path
            else:
                merged.pop(agent, None)
        return merged

    if not get_settings_manager().update_setting("agent_cli_paths", update):
        raise OSError("Failed to save agent CLI paths.")
