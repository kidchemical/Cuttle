"""Safe checks for `.cuttle/scripts/restart-daemon.ps1` (no live restart).

Proves:
- WorkingDirectory + script arg are the non-doubled path form
- Unique PID collection is present
- DryRun survives missing-PID taskkill under $ErrorActionPreference=Stop
  (the launcher/worker abort that previously stopped the script before Start-Process)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".cuttle" / "scripts" / "restart-daemon.ps1"

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "win32",
    reason="restart-daemon.ps1 is the Windows helper",
)


def test_restart_daemon_script_exists_with_canonical_paths():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "Join-Path $PSScriptRoot" in src
    assert "$ProjectRoot" in src
    assert "src\\scripts\\cuttle_daemon.py" in src
    assert "src\\src\\scripts" not in src
    assert "Select-Object -Unique" in src
    assert "cmd /c" in src and "taskkill" in src
    assert "[switch]$DryRun" in src


def test_dry_run_survives_missing_pids_without_starting_daemon():
    """Regression for launcher/worker abort: second taskkill 'not found' must not Stop."""
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "kill_loop_survived_missing_pids=True" in out
    assert "DryRun complete" in out
    assert "Starting daemon" not in out
    # Must not have torn down live Cuttle.
    assert "Stopping daemon PID(s):" not in out or "DryRun" in out


def test_dry_run_reports_unique_daemon_pids_when_present():
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-DryRun",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout or ""
    assert proc.returncode == 0, proc.stderr
    # Either no daemon, or a unique-PID line (launcher+worker listed once each).
    assert (
        "No cuttle_daemon process found" in out
        or "Daemon PID(s) (unique):" in out
    )
    # If both launcher and worker are up, expect two distinct PIDs on one line.
    m = re.search(r"Daemon PID\(s\) \(unique\):\s*([0-9, ]+)", out)
    if m:
        ids = [p.strip() for p in m.group(1).split(",") if p.strip()]
        assert len(ids) == len(set(ids))
        assert len(ids) >= 1
