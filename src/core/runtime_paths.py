"""OS-aware paths for the Cuttle checkout (venv Python, Electron, env files).

Stdlib only — imported by the daemon before optional packages are guaranteed.
"""

from __future__ import annotations

import os
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


def env_file_candidates(project_root: Path, src_root: Optional[Path] = None) -> List[Path]:
    root = Path(project_root)
    src = Path(src_root) if src_root is not None else root / "src"
    return [src / ".env", root / ".env"]


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

    See ``.cuttle/personal/README.md``. Missing file → empty dict.
    """
    import json

    root = Path(project_root) if project_root is not None else _repo_root()
    path = root / ".cuttle" / "personal" / "path-aliases.json"
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
    ``.cuttle/personal/path-aliases.json`` (or ``CUTTLE_WINDOWS_PREFIXES``).
    """
    raw = (path or "").strip()
    if not raw or is_windows():
        return raw
    root = Path(project_root) if project_root is not None else _repo_root()
    text = raw.replace("\\", "/")
    normalized = text.lower()

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


def game_dev_roots() -> List[Path]:
    """Likely POSIX mounts for the Windows ``E:\\Game Dev`` tree."""
    env = (os.environ.get("CUTTLE_GAME_DEV_ROOT") or "").strip()
    user = Path.home().name
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            Path.home() / "Game Dev",
            Path.home() / "Dev",
        ]
    )
    media = Path(f"/media/{user}")
    if media.is_dir():
        candidates.extend(sorted(media.glob("*/Game Dev")))
        candidates.extend(sorted(media.glob("*/Dev")))
    return [p for p in candidates if p.is_dir()]


def _game_dev_windows_markers(project_root: Path) -> List[str]:
    """Windows folder prefixes for a sibling 'Game Dev' tree (generic + personal)."""
    markers = ["e:/game dev", "e:/projects"]
    aliases = load_personal_path_aliases(project_root)
    extra = (aliases.get("game_dev_windows_prefix") or "").strip()
    if extra:
        markers.insert(0, extra.replace("\\", "/").rstrip("/").lower())
    env = (os.environ.get("CUTTLE_GAME_DEV_WINDOWS_PREFIX") or "").strip()
    if env:
        markers.insert(0, env.replace("\\", "/").rstrip("/").lower())
    # unique, keep order
    seen = set()
    out: List[str] = []
    for m in markers:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def rewrite_windows_lab_path(path: str, project_root: Optional[Path] = None) -> str:
    """Rewrite Windows Cuttle + Game Dev paths onto this Linux checkout/mounts."""
    raw = (path or "").strip()
    if not raw or is_windows():
        return raw
    repo = Path(project_root) if project_root is not None else _repo_root()
    mapped = rewrite_windows_cuttle_path(raw, repo)
    if mapped != raw and Path(mapped).exists():
        return mapped
    text = raw.replace("\\", "/")
    lower = text.lower()
    for marker in _game_dev_windows_markers(repo):
        if lower == marker:
            roots = game_dev_roots()
            return str(roots[0].resolve()) if roots else raw
        prefix = marker + "/"
        if lower.startswith(prefix):
            rest = text[len(prefix) :].lstrip("/")
            for groot in game_dev_roots():
                candidate = (groot / rest) if rest else groot
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
