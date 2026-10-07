"""OS-aware paths: the install tree (code) and the per-user Cuttle home (state).

Stdlib only — imported by the daemon before optional packages are guaranteed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional


def is_windows() -> bool:
    return os.name == "nt" or sys.platform == "win32"


def venv_bin_dir(project_root: Path) -> Path:
    root = Path(project_root)
    if is_windows():
        return root / ".venv" / "Scripts"
    wsl = root / ".venv_wsl" / "bin"
    posix = root / ".venv" / "bin"
    if posix.is_dir():
        return posix
    if wsl.is_dir():
        return wsl
    return posix


def venv_python(project_root: Path) -> Path:
    """Prefer the project venv interpreter; fall back to the current process."""
    root = Path(project_root)
    if is_windows():
        candidates = [root / ".venv" / "Scripts" / "python.exe"]
    else:
        candidates = [
            root / ".venv" / "bin" / "python3",
            root / ".venv" / "bin" / "python",
            root / ".venv_wsl" / "bin" / "python3",
            root / ".venv_wsl" / "bin" / "python",
        ]
    for path in candidates:
        if path.is_file():
            return path
    return Path(sys.executable)


def cuttle_home() -> Path:
    """Per-user Cuttle home: every mutable byte Cuttle owns lives here.

    The install tree (checkout, ``Program Files``, read-only AppImage mount)
    is code only and is never written. ``CUTTLE_HOME`` overrides (tests,
    shadow instances, a second checkout that must not share history).
    Windows: ``%LOCALAPPDATA%/Cuttle``. POSIX: ``$XDG_DATA_HOME/cuttle`` or
    ``~/.local/share/cuttle``. Pure (creates nothing).

    Layout: ``.env``, ``config/`` (settings), ``db/``, ``sessions/``,
    ``brain/``, ``cache/``, ``agent_events/``, ``supervised_tasks/``,
    ``edit_attribution/``, ``output/``, ``logs/``, ``secrets/``, ``personal/``.
    """
    override = (os.environ.get("CUTTLE_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Cuttle"
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "cuttle"


def env_file() -> Path:
    """Provider keys and env config, loaded by the daemon before children spawn."""
    return cuttle_home() / ".env"


def data_db_dir() -> Path:
    """SQLite application stores (``<home>/db``)."""
    d = cuttle_home() / "db"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_state_path(owner: str, name: str = "") -> Path:
    """``<home>/<owner>[/<name>]``; ensures the owning directory exists."""
    path = cuttle_home() / owner / name if name else cuttle_home() / owner
    (path.parent if name else path).mkdir(parents=True, exist_ok=True)
    return path


def runtime_cache_path(name: str) -> Path:
    """Replaceable caches: deleting one only causes a refresh."""
    return runtime_state_path("cache", name)


def config_dir() -> Path:
    """Settings files (server, machine, UI state). Pure."""
    return cuttle_home() / "config"


def settings_path() -> Path:
    """Server preferences (``settings.json``); owned by ``SettingsManager``."""
    return config_dir() / "settings.json"


def output_dir() -> Path:
    """Generated output served at ``/output/`` (uploads, shared media, job status). Pure."""
    return cuttle_home() / "output"


def logs_dir() -> Path:
    """Daemon/Flask logs and restart events. Pure."""
    return cuttle_home() / "logs"


def query_logs_dir() -> Path:
    """Per-query sidecars served at ``/logs/``. Pure."""
    return logs_dir() / "queries"


def secrets_dir() -> Path:
    """File-shaped secrets (TLS cert/key, PEM keys, token files). Pure.

    Environment-variable secrets stay in :func:`env_file`.
    """
    return cuttle_home() / "secrets"


def action_hmac_secret_path() -> Path:
    return secrets_dir() / "action_hmac_secret"


def personal_dir() -> Path:
    """Install-local overlay of the shared ``.cuttle_global/`` layer. Pure.

    Mirrors its layout (docs, rules, actions, commands, scripts, skills,
    agents) plus ``path-aliases.json``. A project's own ``.cuttle/personal/``
    stays in that project: the project, not the install, owns it.
    """
    return cuttle_home() / "personal"


def electron_packaged_exe(project_root: Path) -> Optional[Path]:
    root = Path(project_root)
    names: List[Path] = []
    if is_windows():
        names.append(root / "electron" / "dist" / "win-unpacked" / "Cuttle.exe")
    elif sys.platform == "darwin":
        names.append(
            root / "electron" / "dist" / "mac" / "Cuttle.app" / "Contents" / "MacOS" / "Cuttle"
        )
    else:
        unpacked = root / "electron" / "dist" / "linux-unpacked"
        names.extend(
            [
                unpacked / "cuttle",
                unpacked / "Cuttle",
                unpacked / "cuttle-desktop",
            ]
        )
    for path in names:
        if path.is_file():
            return path
    return None


def electron_dev_bin(project_root: Path) -> Optional[Path]:
    root = Path(project_root)
    if is_windows():
        candidates = [
            root / "electron" / "node_modules" / ".bin" / "electron.cmd",
            root / "electron" / "node_modules" / "electron" / "dist" / "electron.exe",
        ]
    else:
        candidates = [
            root / "electron" / "node_modules" / ".bin" / "electron",
            root / "electron" / "node_modules" / "electron" / "dist" / "electron",
        ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def electron_launch_argv(project_root: Path) -> Optional[List[str]]:
    """Argv to open the desktop UI: packaged app, else local electron binary."""
    root = Path(project_root)
    packaged = electron_packaged_exe(root)
    if packaged is not None:
        return [str(packaged)]
    dev = electron_dev_bin(root)
    if dev is not None:
        return [str(dev), str(root / "electron")]
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_personal_path_aliases(project_root: Optional[Path] = None) -> dict:
    """Install-local path aliases (gitignored). Stdlib JSON only.

    ``<home>/personal/path-aliases.json``. Missing file → empty dict.
    """
    import json

    _ = project_root
    path = personal_dir() / "path-aliases.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def personal_string_list(key: str, project_root: Optional[Path] = None) -> List[str]:
    extra = load_personal_path_aliases(project_root).get(key) or []
    if not isinstance(extra, list):
        return []
    return [str(p).strip() for p in extra if str(p).strip()]


def personal_sibling_project_paths(project_root: Optional[Path] = None) -> List[str]:
    """Install-local sibling checkouts (Discord actions, etc.). Empty on a fresh clone."""
    return personal_string_list("sibling_project_paths", project_root)


def _windows_cuttle_prefixes(project_root: Path) -> List[str]:
    """Explicit Windows prefixes that map onto this checkout (personal + env)."""
    out: List[str] = []
    env = (os.environ.get("CUTTLE_WINDOWS_PREFIXES") or "").strip()
    if env:
        out.extend(p.strip() for p in env.split(",") if p.strip())
    aliases = load_personal_path_aliases(project_root)
    extra = aliases.get("windows_cuttle_prefixes") or []
    if isinstance(extra, list):
        out.extend(str(p).strip() for p in extra if str(p).strip())
    return out


def _join_under(root: Path, rest: str) -> str:
    rest = (rest or "").replace("\\", "/").lstrip("/")
    if not rest:
        return str(root.resolve())
    return str((root / rest).resolve())


def rewrite_windows_cuttle_path(path: str, project_root: Optional[Path] = None) -> str:
    """Map a Windows Cuttle checkout path onto this machine's project root.

    On POSIX, copied settings / session cwd still look like ``C:/Projects/Cuttle``.
    Matching is generic: any path *segment* equal to this repo's folder name
    (usually ``Cuttle``) maps onto ``project_root``. Extra prefixes that do not
    contain that folder name live in gitignored
    ``<home>/personal/path-aliases.json`` (or ``CUTTLE_WINDOWS_PREFIXES``).
    """
    raw = (path or "").strip()
    if not raw or is_windows():
        return raw
    root = Path(project_root) if project_root is not None else _repo_root()
    text = raw.replace("\\", "/")
    normalized = text.lower()
    # Only Windows-shaped paths are foreign here. A local POSIX path is left
    # alone: /home/runner/work/Cuttle/Cuttle would otherwise match the first
    # "Cuttle" segment and be rewritten to …/Cuttle/Cuttle/Cuttle.
    if not (re.match(r"^[A-Za-z]:", text) or "\\" in raw or text.startswith("//")):
        return raw

    prefixes = []
    for prefix in _windows_cuttle_prefixes(root):
        p = prefix.replace("\\", "/").rstrip("/").lower()
        if p:
            prefixes.append((p, prefix.replace("\\", "/").rstrip("/")))
    prefixes.sort(key=lambda x: len(x[0]), reverse=True)
    for p, _orig in prefixes:
        if normalized == p or normalized.startswith(p + "/"):
            rest = text[len(p) :].lstrip("/")
            return _join_under(root, rest)

    name = root.name
    if name:
        parts = [p for p in text.split("/") if p]
        needle = name.lower()
        idx = next((i for i, part in enumerate(parts) if part.lower() == needle), -1)
        if idx >= 0:
            rest = "/".join(parts[idx + 1 :])
            return _join_under(root, rest)
    return raw


def path_mappings(project_root: Optional[Path] = None) -> List[tuple]:
    """Install-local foreign-path prefixes → candidate local roots.

    ``path-aliases.json`` → ``path_mappings``, e.g.
    ``{"E:/Projects": ["~/Projects", "/mnt/data/Projects"]}``. Longest prefix
    first; prefixes are compared case-insensitively with ``/`` separators.
    Empty on a fresh clone — core knows no machine's folder layout.
    """
    raw = load_personal_path_aliases(project_root).get("path_mappings") or {}
    if not isinstance(raw, dict):
        return []
    out = []
    for prefix, roots in raw.items():
        key = str(prefix).replace("\\", "/").rstrip("/").lower()
        if isinstance(roots, str):
            roots = [roots]
        if not key or not isinstance(roots, list):
            continue
        out.append((key, [Path(str(r)).expanduser() for r in roots if str(r).strip()]))
    out.sort(key=lambda item: len(item[0]), reverse=True)
    return out


def rewrite_windows_lab_path(path: str, project_root: Optional[Path] = None) -> str:
    """Rewrite a Windows path onto this machine: Cuttle checkout, then ``path_mappings``."""
    raw = (path or "").strip()
    if not raw or is_windows():
        return raw
    repo = Path(project_root) if project_root is not None else _repo_root()
    mapped = rewrite_windows_cuttle_path(raw, repo)
    if mapped != raw and Path(mapped).exists():
        return mapped
    text = raw.replace("\\", "/")
    lower = text.lower()
    for prefix, roots in path_mappings(repo):
        if lower != prefix and not lower.startswith(prefix + "/"):
            continue
        rest = text[len(prefix):].lstrip("/")
        for root in roots:
            candidate = (root / rest) if rest else root
            if candidate.exists():
                return str(candidate.resolve())
    return mapped


def desktop_state_dir() -> Path:
    """Per-user Cuttle desktop logs (updater, host Electron restart)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "cuttle-desktop"
    xdg = (os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "cuttle-desktop"
    return Path.home() / ".local" / "state" / "cuttle-desktop"


def user_state_dir() -> Path:
    """Writable per-user runtime state (notify queues, restart IPC).

    Never the checkout root: an installed Cuttle may live in a read-only
    program folder (the Windows ``Program Files`` case), and the checkout
    is shared while these files are per-user and per-machine.
    POSIX: ``$XDG_STATE_HOME/cuttle`` or ``~/.local/state/cuttle``.
    Windows: ``%LOCALAPPDATA%/Cuttle`` (else ``%APPDATA%``).
    Pure (creates nothing); writers ensure the parent.
    """
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Cuttle"
    xdg = (os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "cuttle"
    return Path.home() / ".local" / "state" / "cuttle"


def instance_state_dir(project_root: Optional[Path] = None) -> Path:
    """Where this instance keeps mutable runtime files.

    Shadow dev children (``CUTTLE_SHADOW_DATA`` set by ``api.dev_instance``)
    stay inside their private snapshot data dir — isolated and pruned with
    the snapshot, so a shadow never touches the live queues. The snapshot
    directory itself is the key; no install-id bookkeeping needed.
    The live install uses the shared per-user state dir unkeyed (only one
    live daemon can bind the ports, so there is nothing to collide with).
    Pure (creates nothing); writers ensure the parent.
    """
    shadow_data = (os.environ.get("CUTTLE_SHADOW_DATA") or "").strip()
    if shadow_data:
        return Path(shadow_data) / "state"
    _ = project_root
    return user_state_dir()


def notify_queue_path(project_root: Optional[Path] = None) -> Path:
    """Tray-notify queue (Flask appends, daemon drains)."""
    return instance_state_dir(project_root) / "cuttle_notify_queue.jsonl"


def ui_toast_path(project_root: Optional[Path] = None) -> Path:
    """In-app UI toasts (Flask appends, ``GET /api/ui-toasts`` drains)."""
    return instance_state_dir(project_root) / "cuttle_ui_toasts.jsonl"


def flask_restart_status_path(project_root: Optional[Path] = None) -> Path:
    """Durable Flask-restart status (daemon owns stop/start/health)."""
    return instance_state_dir(project_root) / "cuttle_flask_restart_status.json"


def flask_restart_request_path(project_root: Optional[Path] = None) -> Path:
    """Flask restart request (Flask writes, daemon consumes)."""
    return instance_state_dir(project_root) / "cuttle_flask_restart_request.json"


