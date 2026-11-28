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
            encoding="utf-8", errors="replace",
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
        swap = psutil.swap_memory()
        out["ram_used_gb"] = round(mem.used / (1024**3), 2)
        out["ram_total_gb"] = round(mem.total / (1024**3), 2)
        out["ram_available_gb"] = round(mem.available / (1024**3), 2)
        out["swap_used_gb"] = round(swap.used / (1024**3), 2)
        out["swap_total_gb"] = round(swap.total / (1024**3), 2)
    except Exception:
        out["cpu_pct"] = None
        out["ram_used_gb"] = None
        out["ram_total_gb"] = None
    return out


def parse_psi(text: str) -> Dict[str, float]:
    """``/proc/pressure/<res>`` → ``{"some": avg10, "full": avg10}``."""
    out: Dict[str, float] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        for field in parts[1:]:
            if field.startswith("avg10="):
                try:
                    out[parts[0]] = float(field[6:])
                except ValueError:
                    pass
    return out


def _psi() -> Optional[Dict[str, Dict[str, float]]]:
    """Linux pressure stall info (memory / io), the earliest sign of thrashing."""
    root = Path("/proc/pressure")
    if not root.is_dir():
        return None
    out: Dict[str, Dict[str, float]] = {}
    for res in ("memory", "io"):
        try:
            out[res] = parse_psi((root / res).read_text(encoding="utf-8"))
        except OSError:
            continue
    return out or None


def parse_nvidia_gpus(text: str) -> List[Dict[str, Any]]:
    """``nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu``."""
    gpus: List[Dict[str, Any]] = []
    for line in (text or "").strip().splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 6:
            continue
        try:
            gpus.append({
                "index": int(cells[0]),
                "uuid": cells[1],
                "name": cells[2],
                "vram_used_mb": int(float(cells[3])),
                "vram_total_mb": int(float(cells[4])),
                "util_pct": float(cells[5]) if cells[5].replace(".", "", 1).isdigit() else None,  # "[N/A]"
            })
        except ValueError:
            continue
    return gpus


def parse_nvidia_apps(text: str) -> List[Dict[str, Any]]:
    """``nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory``."""
    apps: List[Dict[str, Any]] = []
    for line in (text or "").strip().splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 3:
            continue
        try:
            apps.append({"gpu_uuid": cells[0], "pid": int(cells[1]), "used_mb": int(float(cells[2]))})
        except ValueError:
            continue
    return apps


def _nvidia_smi(query: str) -> str:
    completed = subprocess.run(
        ["nvidia-smi", query, "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8", errors="replace",
    )
    return completed.stdout if completed.returncode == 0 else ""


def _gpus() -> tuple:
    """Per-GPU VRAM/utilisation and per-process VRAM; ``([], [])`` without NVIDIA."""
    if not _which("nvidia-smi"):
        return [], []
    try:
        gpus = parse_nvidia_gpus(_nvidia_smi(
            "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu"))
        apps = parse_nvidia_apps(_nvidia_smi("--query-compute-apps=gpu_uuid,pid,used_memory")) if gpus else []
        return gpus, apps
    except Exception:
        return [], []


# Process names of apps a person is using interactively; their memory is not the mesh's to take.
INTERACTIVE_APPS = {"unity": "Unity Editor", "unity.exe": "Unity Editor", "blender": "Blender",
                    "blender.exe": "Blender"}


def classify_service(cmdline: List[str], cwd: str = "") -> str:
    """Name of a long-lived adapter service a process is (``comfyui``), else ``""``.

    ComfyUI is usually started as ``python main.py`` from its own folder, so the
    working directory counts as much as the command line."""
    joined = " ".join(cmdline or []).replace("\\", "/").lower()
    if "main.py" in joined and "comfyui" in (joined + " " + (cwd or "").lower()):
        return "comfyui"
    return ""


HEAVY_RSS_MB = 1024
HEAVY_VRAM_MB = 256


def _in_job_scope(pid: int) -> bool:
    try:
        return "cuttle-job-" in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False


def _processes(apps: List[Dict[str, Any]], gpus: List[Dict[str, Any]]) -> tuple:
    """``(interactive, services, heavy)``: apps a person is using, adapter services, and
    open interactive apps plus every process holding ≥ 1 GB RAM or real VRAM, each tagged with what it is
    (``interactive``, ``service``, ``cuttle-job``, ``headless``, ``external``) so a
    wait reason can name what is in the way, whether Cuttle started it or not."""
    try:
        import psutil
    except ImportError:
        return [], {}, []
    index_by_uuid = {g["uuid"]: g["index"] for g in gpus}
    vram_by_pid: Dict[int, Dict[int, int]] = {}
    for app in apps:
        idx = index_by_uuid.get(app["gpu_uuid"])
        if idx is not None:
            vram_by_pid.setdefault(app["pid"], {})[idx] = app["used_mb"]
    interactive: Dict[str, float] = {}
    services: Dict[str, Dict[str, Any]] = {}
    heavy: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        info = proc.info
        pid = int(info["pid"])
        name = str(info.get("name") or "").lower()
        rss_mb = int((info.get("memory_info").rss if info.get("memory_info") else 0) / (1024**2))
        vram_mb = sum(vram_by_pid.get(pid, {}).values())
        args = [str(a).lower() for a in (info.get("cmdline") or [])]
        # The Unity Editor renames its main thread ("Unity Main Thre"), so the
        # executable in the command line identifies the app, not the process name.
        exe = args[0].replace("\\", "/").rsplit("/", 1)[-1] if args else ""
        label = INTERACTIVE_APPS.get(exe) or INTERACTIVE_APPS.get(name)
        # A headless run (blender -b, Unity -batchmode) is a job, not a person's session;
        # Unity Hub's own `unity serve` helper is not the Editor.
        if label == "Unity Editor" and "-projectpath" not in args:
            label = None
        headless = bool(label) and bool({"-b", "--background", "-batchmode"} & set(args))
        service = ""
        if name.startswith("python") and "main.py" in " ".join(args):
            try:
                cwd = proc.cwd()
            except (psutil.Error, OSError):
                cwd = ""
            service = classify_service(info.get("cmdline") or [], cwd)
        if label and not headless:
            interactive[label] = interactive.get(label, 0) + rss_mb
            kind = "interactive"
        elif service:
            entry = services.setdefault(service, {"pids": [], "rss_mb": 0, "vram_mb": {}})
            entry["pids"].append(pid)
            entry["rss_mb"] += rss_mb
            for idx, mb in vram_by_pid.get(pid, {}).items():
                entry["vram_mb"][str(idx)] = entry["vram_mb"].get(str(idx), 0) + mb
            kind = f"service:{service}"
        elif _in_job_scope(pid):
            kind = "cuttle-job"
        else:
            kind = "headless" if headless else "external"
        if kind == "interactive" or rss_mb >= HEAVY_RSS_MB or vram_mb >= HEAVY_VRAM_MB:
            heavy.append({"name": label or (info.get("name") or "?"), "pid": pid, "rss_mb": rss_mb,
                          "vram_mb": vram_mb, "kind": kind})
    heavy.sort(key=lambda h: -(h["rss_mb"] + h["vram_mb"]))
    return [{"name": k, "rss_mb": v} for k, v in sorted(interactive.items())], services, heavy[:12]


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
            encoding="utf-8", errors="replace",
        )
        if completed.returncode == 0:
            rev = (completed.stdout or "").strip()
    except Exception:
        rev = ""
    _GIT_REV_CACHE = (now, rev)
    return rev


def _repo_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    env_root = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    return Path(env_root) if env_root else root


def is_checkout() -> bool:
    """A git checkout updates by ``cuttle_self_update``; a packaged install
    (the release installer's ``resources/app``) has no ``.git`` and updates
    only by installing the next release."""
    return (_repo_root() / ".git").exists()


def _git_stdout(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_repo_root()), *args],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            encoding="utf-8", errors="replace",
        )
        if completed.returncode == 0:
            return (completed.stdout or "").strip()
    except Exception:
        pass
    return ""


def git_branch() -> str:
    """Current branch name; empty on detached HEAD or outside a work tree."""
    name = _git_stdout("rev-parse", "--abbrev-ref", "HEAD")
    return "" if name == "HEAD" else name


def git_upstream() -> str:
    """Configured upstream ref (e.g. ``origin/dev``); empty when unset."""
    return _git_stdout("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def update_source() -> str:
    """This device's update channel from settings: 'official' or 'host'."""
    try:
        from managers.settings_manager import get_settings_manager

        block = get_settings_manager().get_setting("device_workers") or {}
        src = str((block or {}).get("update_source") or "official").strip().lower()
        return "host" if src == "host" else "official"
    except Exception:
        return "official"


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
    gpus, apps = _gpus()
    load["gpus"] = [{k: v for k, v in g.items() if k != "uuid"} for g in gpus]
    load["gpu_pct"] = max((g["util_pct"] or 0.0 for g in gpus), default=None)
    load["psi"] = _psi()
    load["interactive"], load["services"], load["heavy"] = _processes(apps, gpus)
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
        "cuttle_self_update": is_checkout(),
        "install": "checkout" if is_checkout() else "packaged",
        "client_daemon": bool(os.environ.get("CUTTLE_CLIENT_DAEMON")),
        "features": sorted(RUNNING_FEATURES),
        "blender_python_expr": True,
    }
    try:
        # Execution node ad (api.exec_nodes): opt-in flag + installed agent CLIs.
        from api.exec_nodes import node as exec_node

        caps.update(exec_node.advertise())
        load["agent_turns"] = exec_node.running()
    except Exception:
        caps["agent_exec"] = False
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
        "git_branch": git_branch(),
        "git_upstream": git_upstream(),
        "update_source": update_source(),
        "boot_at": boot_at(),
        "stale_process": bool(boot and disk_rev and git_revs_differ(boot, disk_rev)),
    }
