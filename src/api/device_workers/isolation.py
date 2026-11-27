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
from typing import List, Tuple

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
