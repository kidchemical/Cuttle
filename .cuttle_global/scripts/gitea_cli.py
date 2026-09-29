#!/usr/bin/env python3
"""Shim — prefer ``python -m api.gitea``. Kept for older runbooks/scripts."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from api.gitea.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
