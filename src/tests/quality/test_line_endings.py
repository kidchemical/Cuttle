"""Windows batch scripts must check out with CRLF; shell scripts with LF.

cmd.exe treats .bat and .cmd the same, and LF-only files can make it miss
`goto` / `call :label` targets. Bash fails on CRLF (`$'\\r': command not found`).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _tracked_eol(*patterns: str) -> dict[str, str]:
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    files = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert files, f"expected tracked files for {patterns}"
    out = subprocess.run(
        ["git", "check-attr", "eol", "--", *files],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return {line.split(": ")[0]: line.rsplit(": ", 1)[1] for line in out.splitlines()}


def test_windows_batch_scripts_check_out_crlf():
    wrong = {p: eol for p, eol in _tracked_eol("*.bat", "*.cmd").items() if eol != "crlf"}
    assert not wrong, f"add a CRLF rule to .gitattributes for: {sorted(wrong)}"


def test_shell_scripts_check_out_lf():
    wrong = {p: eol for p, eol in _tracked_eol("*.sh", "*gradlew").items() if eol != "lf"}
    assert not wrong, f"shell scripts must stay LF: {sorted(wrong)}"
