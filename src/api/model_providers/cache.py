"""Atomic catalog snapshots in the Cuttle home; never credentials or prompts."""
from __future__ import annotations

import json
import os
import tempfile
import math
from pathlib import Path
from typing import Any

from core.runtime_paths import runtime_cache_path


def read(name: str) -> dict[str, Any]:
    path = runtime_cache_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            return {}
        for key in ("fetched_at", "checked_at"):
            value = data.get(key, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                return {}
        if "models" in data and (not isinstance(data["models"], list) or any(
                not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in data["models"])):
            return {}
        if "catalog" in data and not isinstance(data["catalog"], dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def write(name: str, data: dict[str, Any]) -> None:
    path = runtime_cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({**data, "version": 1}, stream)
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


def public_error(exc: Exception, subject: str) -> str:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    suffix = f"; HTTP {status}" if isinstance(status, int) else ""
    return f"{subject} unavailable ({type(exc).__name__}{suffix})."
