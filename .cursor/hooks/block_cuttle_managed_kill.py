#!/usr/bin/env python3
"""Cursor beforeShellExecution hook: block kills of Cuttle-managed processes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Project root = parents[2] from .cursor/hooks/this_file.py
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from api.cuttle_managed_process_guard import hook_decision_from_stdin  # noqa: E402


def main() -> int:
    raw = sys.stdin.read()
    try:
        decision = hook_decision_from_stdin(raw)
    except Exception as e:
        # failClosed in hooks.json — still emit deny JSON for safety-critical path
        decision = {
            "permission": "deny",
            "user_message": f"Cuttle process guard error: {e}",
            "agent_message": (
                "Direct termination of Cuttle-managed processes is blocked.\n"
                "Use /restart graceful or /restart when-idle."
            ),
        }
    sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
