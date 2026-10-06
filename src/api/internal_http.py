"""Loopback HTTP helpers for Cuttle Flask routes (LLM fallback without a second TLS hop)."""

from __future__ import annotations

import os


def _internal_api_base() -> str:
    override = (os.environ.get("CUTTLE_INTERNAL_API_BASE") or "").strip()
    if override:
        return override.rstrip("/")
    from api.server_ports import resolve_with_env_file

    return f"https://127.0.0.1:{resolve_with_env_file().https}".rstrip("/")
