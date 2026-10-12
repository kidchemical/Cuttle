"""Source-checkout Windows setup; stdlib only.

Installs Cuttle dependencies and delegates lifetime to Electron Host or daemon.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def run(argv: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(
        argv, cwd=cwd, check=True, text=True, encoding="utf-8",
        stdout=subprocess.PIPE if capture else None,
    )
    return (result.stdout or "").strip()


def desktop_tools(root: Path) -> tuple[str, str]:
    node = shutil.which("node.exe")
    npm = shutil.which("npm.cmd")
    if not node or not npm:
        raise RuntimeError(
            "Desktop setup needs Node.js and npm. Install Node.js LTS from "
            "https://nodejs.org/ and reopen PowerShell, or use -Browser."
        )
    package = json.loads((root / "electron" / "package.json").read_text(encoding="utf-8"))
    minimum = package["engines"]["node"]
    required = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", minimum)
    version = run([node, "--version"], root, capture=True)
    actual = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not required or not actual or tuple(map(int, actual.groups())) < tuple(map(int, required.groups())):
        raise RuntimeError(f"Desktop requires Node.js {minimum}; found {version}. Install Node.js LTS and reopen PowerShell.")
    return node, npm


def setup(root: Path, *, browser: bool, no_launch: bool, launch_only: bool) -> None:
    if sys.version_info < (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("Cuttle requires 64-bit Python 3.11+. Install it, then recreate the checkout's .venv if needed.")
    # Fail before downloads if desktop prerequisites are absent.
    tools = None if browser else desktop_tools(root)
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        if launch_only:
            raise RuntimeError("Run setup_windows.ps1 first to create the Python environment.")
        print("[1/3] Creating the project Python environment...", flush=True)
        run([sys.executable, "-m", "venv", str(root / ".venv")], root)
    if not launch_only:
        print("[2/3] Installing Python runtime dependencies...", flush=True)
        run([str(python), "-m", "pip", "install", "-r", str(root / "src" / "requirements" / "requirements.txt")], root)
        run([str(python), "-m", "pip", "check"], root)
    if not browser:
        node, npm = tools
        desktop = root / "electron"
        installer = desktop / "node_modules" / "electron" / "install.js"
        binary = desktop / "node_modules" / "electron" / "dist" / "electron.exe"
        # A partial node_modules directory is not a completed install.
        if not launch_only or not installer.is_file():
            print("[3/3] Installing locked desktop dependencies...", flush=True)
            run([npm, "ci"], desktop)
        # npm can defer dependency install scripts. Download before opening UI.
        run([node, str(installer)], desktop)
        if not binary.is_file():
            raise RuntimeError("Electron download did not produce electron.exe. Check proxy/network settings and rerun setup_windows.ps1.")
    print("Setup ready. Provider keys are optional; install and authenticate your chosen agent CLI separately.", flush=True)
    if no_launch:
        return
    print("Closing the window keeps Cuttle running. Use tray Exit to stop the daemon.", flush=True)
    if browser:
        # Daemon owns Flask, tray, and UI launch.
        run([str(python), str(root / "src" / "scripts" / "cuttle_daemon.py"), "--browser"], root)
    else:
        run([str(binary), str(root / "electron"), "--mode=host"], root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Skip Node/Electron and launch the browser daemon")
    parser.add_argument("--no-launch", action="store_true", help="Install only; do not start Cuttle")
    parser.add_argument("--launch-only", action="store_true", help="Reuse installed Python dependencies")
    args = parser.parse_args(argv)
    try:
        if os.name != "nt":
            raise RuntimeError("This launcher is for Windows. On POSIX use start_cuttle.sh.")
        setup(ROOT, browser=args.browser, no_launch=args.no_launch, launch_only=args.launch_only)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
