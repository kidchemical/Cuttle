"""Source-checkout setup for Windows and Linux; stdlib only.

Installs Cuttle dependencies and delegates lifetime to Electron Host or daemon.
Entry points: ``setup_windows.ps1`` and ``setup_linux.sh`` at the repo root.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Platform:
    entry: str
    python: str
    node: str
    npm: str
    electron: str
    browser_flag: str
    reopen: str


WINDOWS = Platform(
    entry="setup_windows.ps1",
    python=".venv/Scripts/python.exe",
    node="node.exe",
    npm="npm.cmd",
    electron="electron/node_modules/electron/dist/electron.exe",
    browser_flag="-Browser",
    reopen="reopen PowerShell",
)
LINUX = Platform(
    entry="setup_linux.sh",
    python=".venv/bin/python",
    node="node",
    npm="npm",
    electron="electron/node_modules/electron/dist/electron",
    browser_flag="--browser",
    reopen="open a new terminal",
)


def current_platform() -> Platform:
    return WINDOWS if os.name == "nt" else LINUX


def run(argv: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(
        argv, cwd=cwd, check=True, text=True, encoding="utf-8",
        stdout=subprocess.PIPE if capture else None,
    )
    return (result.stdout or "").strip()


def desktop_tools(root: Path, plat: Platform) -> tuple[str, str]:
    node = shutil.which(plat.node)
    npm = shutil.which(plat.npm)
    if not node or not npm:
        raise RuntimeError(
            "Desktop setup needs Node.js and npm. Install Node.js LTS from "
            f"https://nodejs.org/ and {plat.reopen}, or use {plat.browser_flag}."
        )
    package = json.loads((root / "electron" / "package.json").read_text(encoding="utf-8"))
    minimum = package["engines"]["node"]
    required = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", minimum)
    version = run([node, "--version"], root, capture=True)
    actual = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not required or not actual or tuple(map(int, actual.groups())) < tuple(map(int, required.groups())):
        raise RuntimeError(f"Desktop requires Node.js {minimum}; found {version}. Install Node.js LTS and {plat.reopen}.")
    return node, npm


def _read_flag(path: str, default: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _nosuid(path: Path) -> bool:
    try:
        return bool(os.statvfs(path).f_flag & os.ST_NOSUID)
    except (OSError, AttributeError):
        return False


def sandbox_problem(binary: Path) -> str:
    """Linux only: why Chromium's sandbox cannot start, or "" when it can.

    Mirrors ``.cuttle/scripts/electron-sandbox.sh``, which the launchers source.
    """
    if os.environ.get("CUTTLE_ELECTRON_NO_SANDBOX") == "1":
        return ""
    helper = binary.parent / "chrome-sandbox"
    try:
        info = helper.stat()
        if info.st_uid == 0 and info.st_mode & 0o4000:
            return ""
    except OSError:
        pass
    restricted = (
        _read_flag("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "0") == "1"
        or _read_flag("/proc/sys/kernel/unprivileged_userns_clone", "1") == "0"
    )
    if not restricted:
        return ""
    if _nosuid(binary.parent):
        return (
            "The Electron sandbox needs a setuid-root helper on this system (Ubuntu 24.04+ restricts "
            f"user namespaces), but {binary.parent} is on a nosuid filesystem where setuid has no effect. "
            "Clone Cuttle onto a regular Linux filesystem, or for development only set "
            f"CUTTLE_ELECTRON_NO_SANDBOX=1. Or use {LINUX.browser_flag} to run Cuttle in the browser."
        )
    return (
        "The Electron sandbox needs a setuid-root helper on this system (Ubuntu 24.04+ restricts "
        "user namespaces). Run once, then rerun setup:\n"
        f"  sudo chown root:root '{helper}'\n"
        f"  sudo chmod 4755 '{helper}'\n"
        f"Or use {LINUX.browser_flag} to run Cuttle in the browser."
    )


def create_venv(root: Path, plat: Platform) -> None:
    venv = root / ".venv"
    try:
        run([sys.executable, "-m", "venv", str(venv)], root)
    except subprocess.CalledProcessError:
        if plat is LINUX:
            # A failed venv leaves a pip-less .venv that later runs would reuse.
            shutil.rmtree(venv, ignore_errors=True)
            major, minor = sys.version_info[:2]
            raise RuntimeError(
                "Could not create the Python environment. On Ubuntu/Debian install the venv "
                f"package (sudo apt install python{major}.{minor}-venv) and rerun {plat.entry}."
            ) from None
        raise


def setup(root: Path, *, browser: bool, no_launch: bool, launch_only: bool, plat: Platform | None = None) -> None:
    plat = plat or current_platform()
    if sys.version_info < (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("Cuttle requires 64-bit Python 3.11+. Install it, then recreate the checkout's .venv if needed.")
    # Fail before downloads if desktop prerequisites are absent.
    tools = None if browser else desktop_tools(root, plat)
    python = root / plat.python
    if not python.is_file():
        if launch_only:
            raise RuntimeError(f"Run {plat.entry} first to create the Python environment.")
        print("[1/3] Creating the project Python environment...", flush=True)
        create_venv(root, plat)
    if not launch_only:
        print("[2/3] Installing Python runtime dependencies...", flush=True)
        run([str(python), "-m", "pip", "install", "-r", str(root / "src" / "requirements" / "requirements.txt")], root)
        run([str(python), "-m", "pip", "check"], root)
    if not browser:
        node, npm = tools
        desktop = root / "electron"
        installer = desktop / "node_modules" / "electron" / "install.js"
        binary = root / plat.electron
        # A partial node_modules directory is not a completed install.
        if not launch_only or not installer.is_file():
            print("[3/3] Installing locked desktop dependencies...", flush=True)
            run([npm, "ci"], desktop)
        # npm can defer dependency install scripts. Download before opening UI.
        run([node, str(installer)], desktop)
        if not binary.is_file():
            raise RuntimeError(f"Electron download did not produce {binary.name}. Check proxy/network settings and rerun {plat.entry}.")
        problem = sandbox_problem(binary) if plat is LINUX else ""
        if problem:
            if not no_launch:
                raise RuntimeError(problem)
            print(f"WARNING: {problem}", flush=True)
    print("Setup ready. Provider keys are optional; install and authenticate your chosen agent CLI separately.", flush=True)
    if no_launch:
        return
    print("Closing the window keeps Cuttle running. Use tray Exit to stop the daemon.", flush=True)
    if browser:
        # Daemon owns Flask, tray, and UI launch.
        run([str(python), str(root / "src" / "scripts" / "cuttle_daemon.py"), "--browser"], root)
    elif plat is LINUX:
        # The launcher owns sandbox and display flags.
        run([str(root / ".cuttle" / "scripts" / "launch-cuttle-host.sh")], root)
    else:
        run([str(root / plat.electron), str(root / "electron"), "--mode=host"], root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Skip Node/Electron and launch the browser daemon")
    parser.add_argument("--no-launch", action="store_true", help="Install only; do not start Cuttle")
    parser.add_argument("--launch-only", action="store_true", help="Reuse installed Python dependencies")
    args = parser.parse_args(argv)
    try:
        if sys.platform == "darwin":
            raise RuntimeError("Guided setup supports Windows and Linux. On macOS follow the manual steps in README.md.")
        setup(ROOT, browser=args.browser, no_launch=args.no_launch, launch_only=args.launch_only)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
