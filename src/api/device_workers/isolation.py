"""Per-job memory scopes: an OOM kills the job, never Flask, the daemon or a sibling.

A job that declares ``requirements.resources.ram_mb`` runs (Linux, systemd user
session) inside its own transient scope with a hard ``MemoryMax`` a little
above its reservation, almost no swap, and the highest OOM score, so the kernel
picks the job first even under whole-machine pressure. The scope's ``Result``
tells an OOM kill from an ordinary failure afterwards.

Other platforms run the job unscoped (Windows Job Objects are a later phase).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SWAP_MAX_MB = 512


def memory_limit_mb(ram_mb: int) -> int:
    """Hard limit for a job that reserved ``ram_mb``: 25 % (at least 1 GB) of headroom."""
    return int(ram_mb) + max(1024, int(ram_mb) // 4)


def _scope_name(job_id: str) -> str:
    return "cuttle-job-" + re.sub(r"[^A-Za-z0-9_.-]", "-", str(job_id or "job"))[:64] + ".scope"


def available() -> bool:
    return sys.platform.startswith("linux") and bool(shutil.which("systemd-run"))


def scoped_argv(argv: List[str], *, job_id: str, ram_mb: int) -> Tuple[List[str], str]:
    """``(argv, scope)`` running ``argv`` in a memory-limited scope; unchanged when unavailable."""
    if not ram_mb or not available():
        return list(argv), ""
    scope = _scope_name(job_id)
    wrapped = [
        "systemd-run", "--user", "--scope", "--quiet", f"--unit={scope}",
        "--slice=cuttle-jobs.slice",
        "-p", f"MemoryMax={memory_limit_mb(ram_mb)}M",
        "-p", f"MemorySwapMax={SWAP_MAX_MB}M",
        "-p", "OOMPolicy=kill",
        "--",
    ]
    if shutil.which("choom"):
        wrapped += ["choom", "-n", "1000", "--"]
    return wrapped + list(argv), scope


def oom_killed(scope: str) -> bool:
    """True when ``scope`` ended by an OOM kill; clears the failed unit either way."""
    if not scope:
        return False
    try:
        shown = subprocess.run(
            ["systemctl", "--user", "show", scope, "-p", "Result", "--value"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        ).stdout.strip()
        subprocess.run(["systemctl", "--user", "reset-failed", scope], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return shown == "oom-kill"


def _systemctl(*args: str) -> str:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def stop_scope(job_id: str) -> bool:
    """Stop what is left of a job's scope (a worker that died left it running).

    True once no process of it runs; False when it would not stop, so the
    caller keeps the job's reservation."""
    if not available():
        return True
    scope = _scope_name(job_id)
    if _systemctl("is-active", scope) not in ("active", "activating", "deactivating"):
        return True
    _systemctl("stop", scope)
    _systemctl("reset-failed", scope)
    return _systemctl("is-active", scope) not in ("active", "activating", "deactivating")


class ScopeMeter:
    """Peak RAM (cgroup ``memory.peak``) and VRAM of a running scope, sampled in the background.

    The scope is gone once its last process exits, so the meter keeps the last
    reading; ``memory.peak`` is monotonic, so that is the job's peak to within
    one interval."""

    def __init__(self, scope: str, *, interval: float = 2.0):
        self.scope = scope
        self.interval = interval
        self.peak_ram_mb = 0
        self.peak_vram_mb = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "ScopeMeter":
        if self.scope:
            self._thread = threading.Thread(target=self._run, name=f"meter-{self.scope}", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from api.device_workers.capabilities import _gpus

        cgroup: Optional[Path] = None
        while not self._stop.wait(self.interval):
            if cgroup is None:
                rel = _systemctl("show", self.scope, "-p", "ControlGroup", "--value")
                cgroup = Path("/sys/fs/cgroup" + rel) if rel else None
                if cgroup is None:
                    continue
            try:
                self.peak_ram_mb = max(self.peak_ram_mb, int((cgroup / "memory.peak").read_text(encoding="utf-8")) // (1024**2))
                pids = {int(p) for p in (cgroup / "cgroup.procs").read_text(encoding="utf-8").split()}
            except (OSError, ValueError):
                continue
            _gpu_list, apps = _gpus()
            used = sum(int(a["used_mb"]) for a in apps if a["pid"] in pids)
            self.peak_vram_mb = max(self.peak_vram_mb, used)

    def as_result(self) -> Dict[str, int]:
        out = {}
        if self.peak_ram_mb:
            out["peak_ram_mb"] = self.peak_ram_mb
        if self.peak_vram_mb:
            out["peak_vram_mb"] = self.peak_vram_mb
        return out
