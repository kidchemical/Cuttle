"""Safe checks for `.cuttle/scripts/restart-daemon.sh` (no live restart)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".cuttle" / "scripts" / "restart-daemon.sh"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX daemon helper")
def test_restart_daemon_sh_dry_run():
    assert SCRIPT.is_file()
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "DryRun complete" in out
    assert "Started PID" not in out
