#!/usr/bin/env python3
"""Explicit launcher for optional developer test analytics on port 5000."""
import subprocess
import sys
from pathlib import Path


def main():
    src_root = Path(__file__).resolve().parents[2]
    return subprocess.call([sys.executable, "tests/reporting/history_api.py"], cwd=src_root)


if __name__ == "__main__":
    raise SystemExit(main())
