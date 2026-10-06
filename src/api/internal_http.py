"""Loopback HTTP helpers for Cuttle Flask routes (LLM fallback without a second TLS hop)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def _internal_api_base() -> str:
    override = (os.environ.get("CUTTLE_INTERNAL_API_BASE") or "").strip()
    if override:
        return override.rstrip("/")
    from api.server_ports import resolve_with_env_file

    return f"https://127.0.0.1:{resolve_with_env_file().https}".rstrip("/")


def _internal_requests_extra() -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    base = _internal_api_base()
    env = (os.environ.get("CUTTLE_INTERNAL_TLS_VERIFY") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        extra["verify"] = True
    elif env in ("0", "false", "no", "off"):
        extra["verify"] = False
    elif base.startswith("https://"):
        host = (urlparse(base).hostname or "").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            extra["verify"] = False
    if extra.get("verify") is False:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    return extra


def _internal_response_ok(resp: Any) -> bool:
    code = getattr(resp, "status_code", None)
    if code is not None:
        return int(code) < 400
    return bool(getattr(resp, "ok", False))


def _internal_response_json(resp: Any) -> Optional[dict]:
    if hasattr(resp, "get_json"):
        out = resp.get_json(silent=True)
        return out if isinstance(out, dict) else None
    try:
        out = resp.json()
        return out if isinstance(out, dict) else None
    except Exception:
        return None
