"""A slow `agy` quota probe never holds a process open at exit.

kcslaptop 2026-11-26: `python -m api.subagents spawn --wait --timeout 3` printed
its result after ~47 s. The router budget refresh ran `agy /usage` and
`agy /credits` in a ThreadPoolExecutor, whose workers Python joins at
interpreter exit, so every CLI that refreshed the budget waited out the 45 s
probe timeout on a machine with Antigravity installed.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]

_SCRIPT = r"""
import threading, time
from api.agent_harness.agents.antigravity import usage, adapter
adapter.antigravity_executable = lambda: "agy-fake"
usage._run_command = lambda exe, command, timeout: time.sleep(60) or {}
threading.Thread(target=usage.fetch_antigravity_quota, daemon=True).start()
time.sleep(0.5)
print("done", flush=True)
"""


def test_process_exits_while_a_quota_probe_is_still_running():
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=SRC, capture_output=True,
                          text=True, encoding="utf-8", timeout=60,
                          env={**__import__("os").environ, "PYTHONPATH": str(SRC)})
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "done"
    assert elapsed < 20, f"exit waited on the probe ({elapsed:.1f}s)"
