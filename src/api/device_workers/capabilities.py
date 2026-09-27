"""Collect local capability / load ads for device worker heartbeats."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _ac_power() -> Optional[bool]:
    if platform.system() == "Linux":
        psy = Path("/sys/class/power_supply")
        if not psy.is_dir():
            return True
        try:
            for entry in psy.iterdir():
                typ_path = entry / "type"
                if not typ_path.is_file():
                    continue
                kind = typ_path.read_text(encoding="utf-8", errors="replace").strip()
                if kind != "Mains":
                    continue
                online_path = entry / "online"
                if online_path.is_file():
                    return online_path.read_text(encoding="utf-8", errors="replace").strip() == "1"
        except OSError:
            return None
        return True
    if platform.system() != "Windows":
        return None
    try:
        # battery status; Online = AC
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue "
                "| Measure-Object).Count; "
                "$s = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue "
                "| Select-Object -First 1 -ExpandProperty BatteryStatus; "
                "if (-not $s) { 'AC' } elseif ($s -eq 2) { 'AC' } else { 'BAT' }",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (completed.stdout or "").strip().splitlines()
        if not text:
            return True
        # No battery → desktop on AC
        if text[0].strip() == "0":
            return True
        return (text[-1].strip().upper() == "AC")
    except Exception:
        return None


def _cpu_ram() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        import psutil

        out["cpu_pct"] = float(psutil.cpu_percent(interval=0.15))
        mem = psutil.virtual_memory()
        out["ram_used_gb"] = round(mem.used / (1024**3), 2)
        out["ram_total_gb"] = round(mem.total / (1024**3), 2)
    except Exception:
        out["cpu_pct"] = None
        out["ram_used_gb"] = None
        out["ram_total_gb"] = None
    return out


def _gpu_pct() -> Optional[float]:
    if not _which("nvidia-smi"):
        return None
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return None
        line = (completed.stdout or "").strip().splitlines()
        if not line:
            return None
        return float(line[0].strip())
    except Exception:
        return None


def _blender_gpu() -> Optional[str]:
    if _which("nvidia-smi"):
        return "optix"
    return None


def find_blender_executable(bin_hint: str = "") -> Optional[str]:
    """Locate blender.exe — PATH, hint, or Program Files\\Blender Foundation\\*."""
    hint = (bin_hint or "").strip()
    if hint and Path(hint).is_file():
        return hint
    which = shutil.which("blender")
    if which:
        return which
    # Env override (useful when not on PATH)
    env = (os.environ.get("CUTTLE_BLENDER_BIN") or os.environ.get("BLENDER_BIN") or "").strip()
    if env and Path(env).is_file():
        return env
    roots = [
        Path(r"C:\Program Files\Blender Foundation"),
        Path(r"C:\Program Files (x86)\Blender Foundation"),
    ]
    found: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                exe = child / "blender.exe"
                if exe.is_file():
                    found.append(exe)
        except OSError:
            continue
    if not found:
        return None
    # Prefer highest version directory name (Blender 4.4 > 4.2 > 3.6)
    found.sort(key=lambda p: p.parent.name, reverse=True)
    return str(found[0])


def cuttle_version() -> str:
    """Desktop/app semver (electron/package.json), else CUTTLE_PACKAGE_VERSION env.

    Prefer on-disk package.json for source checkouts so a stale
    CUTTLE_PACKAGE_VERSION inherited across self-update does not stick after bump.
    """
    pkg_ver = ""
    try:
        # src/api/device_workers → repo root
        root = Path(__file__).resolve().parents[3]
        pkg = root / "electron" / "package.json"
        if pkg.is_file():
            data = json.loads(pkg.read_text(encoding="utf-8"))
            pkg_ver = str(data.get("version") or "").strip()
    except Exception:
        pkg_ver = ""
    if pkg_ver:
        return pkg_ver
    return (os.environ.get("CUTTLE_PACKAGE_VERSION") or "").strip()


_GIT_REV_CACHE: tuple[float, str] = (0.0, "")
_BOOT_GIT_REV: str = ""
_BOOT_AT: float = 0.0

# Features present in *this* running process (not merely on disk).
RUNNING_FEATURES = frozenset(
    {
        "blender_python_expr",
        "long_run_timeouts",
        "partial_retry",
    }
)


def cuttle_git_rev(*, force: bool = False) -> str:
    """Current checkout HEAD short SHA (cached ~15s). Empty when not a git work tree."""
    global _GIT_REV_CACHE
    now = time.time()
    cached_at, cached = _GIT_REV_CACHE
    if (not force) and cached and (now - cached_at) < 15.0:
        return cached

    root = Path(__file__).resolve().parents[3]
    env_root = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    if env_root:
        root = Path(env_root)
    rev = ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0:
            rev = (completed.stdout or "").strip()
    except Exception:
        rev = ""
    _GIT_REV_CACHE = (now, rev)
    return rev


def git_revs_differ(a: str, b: str) -> bool:
    """True when both revs are present and do not refer to the same commit."""
    left = (a or "").strip().lower()
    right = (b or "").strip().lower()
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 7:
        return left != right
    return not longer.startswith(shorter)


def _ensure_boot_marker() -> None:
    global _BOOT_GIT_REV, _BOOT_AT
    if not _BOOT_GIT_REV:
        _BOOT_GIT_REV = cuttle_git_rev(force=True)
        _BOOT_AT = time.time()


def boot_git_rev() -> str:
    """Git rev captured when this worker process first loaded device_workers code."""
    _ensure_boot_marker()
    return _BOOT_GIT_REV


def boot_at() -> float:
    _ensure_boot_marker()
    return float(_BOOT_AT or 0.0)


def process_stale() -> bool:
    """True when on-disk checkout moved ahead of the code this process booted with.

    This is the Muse CH-000478 failure mode: package/version badge matches, but the
    live Python worker still runs pre-pull modules (e.g. ignores ``python_expr``).
    """
    _ensure_boot_marker()
    disk = cuttle_git_rev(force=True)
    return git_revs_differ(_BOOT_GIT_REV, disk)


def refresh_boot_marker() -> str:
    """Reset boot fingerprint after a successful in-process code reload."""
    global _BOOT_GIT_REV, _BOOT_AT
    _BOOT_GIT_REV = cuttle_git_rev(force=True)
    _BOOT_AT = time.time()
    return _BOOT_GIT_REV


def reload_device_workers_modules() -> Dict[str, Any]:
    """Hot-reload worker execution modules after a git pull without full process exit.

    Parent daemons do not always restart the claim loop on return, so reloading
    keeps Clients/Hosts current when disk rev advances under a long-lived process.
    """
    import importlib
    import sys

    names = [
        "api.device_workers.long_run",
        "api.device_workers.job_policy",
        "api.device_workers.profiles",
        "api.device_workers.executor",
        "api.device_workers.intent",
        "api.device_workers.platform",
    ]
    reloaded: List[str] = []
    errors: List[str] = []
    for name in names:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
            reloaded.append(name)
        except Exception as e:
            errors.append(f"{name}: {e}")
    new_boot = refresh_boot_marker()
    return {
        "ok": not errors,
        "reloaded": reloaded,
        "errors": errors,
        "boot_git_rev": new_boot,
    }


def collect_capabilities(
    *,
    interactive_priority: str = "low",
    storage_extra: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    load = _cpu_ram()
    load["gpu_pct"] = _gpu_pct()
    caps = {
        "shell": True,
        "filesystem": True,
        "python": True,
        "ffmpeg": _which("ffmpeg"),
        "blender": bool(find_blender_executable()),
        "blender_gpu": _blender_gpu(),
        "blender_bin": find_blender_executable() or None,
        "unity": bool(
            os.environ.get("UNITY_PATH")
            or Path(r"C:\Program Files\Unity\Hub\Editor").is_dir()
        ),
        "ollama": _which("ollama"),
        "agent_cursor": _which("agent") or _which("cursor-agent"),
        "cuttle_self_update": True,
        "client_daemon": bool(os.environ.get("CUTTLE_CLIENT_DAEMON")),
        "features": sorted(RUNNING_FEATURES),
        "blender_python_expr": True,
    }
    storage = dict(storage_extra or {})
    disk_rev = cuttle_git_rev()
    boot = boot_git_rev()
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "interactive_priority": interactive_priority,
        "ac_power": _ac_power(),
        "capabilities": caps,
        "storage": storage,
        "load": load,
        "cuttle_version": cuttle_version(),
        "cuttle_git_rev": disk_rev,
        "boot_git_rev": boot,
        "boot_at": boot_at(),
        "stale_process": bool(boot and disk_rev and git_revs_differ(boot, disk_rev)),
    }
