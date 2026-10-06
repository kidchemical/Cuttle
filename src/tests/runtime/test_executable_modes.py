"""Fresh POSIX clones must be able to run documented launchers with `./…`.

Git, not the working tree, decides a clone's file modes. This checkout may run
with ``core.filemode=false`` (it began on Windows, where every file looks
executable), so ``ls -l`` and ``chmod +x`` prove nothing. Read the index.
Shell scripts meant to be ``source``d have no shebang and stay 100644.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _index_modes() -> dict[str, str]:
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", "*.sh", "*gradlew"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    modes = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = meta.split()[0]
    return modes


def _has_shebang(path: str) -> bool:
    with open(REPO_ROOT / path, "rb") as fh:
        return fh.read(2) == b"#!"


def test_runnable_scripts_are_tracked_executable():
    modes = _index_modes()
    assert modes, "expected tracked shell scripts"
    missing = sorted(
        path for path, mode in modes.items()
        if mode != "100755" and _has_shebang(path)
    )
    assert not missing, (
        "Record the execute bit in git (a plain chmod is not enough here): "
        "git update-index --chmod=+x " + " ".join(missing)
    )


def test_sourced_helpers_stay_non_executable():
    modes = _index_modes()
    assert modes.get(".cuttle/scripts/electron-sandbox.sh") == "100644"
