"""In-process POST to Cuttle Flask routes (LLM fallback without a second TLS hop)."""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

LLM_INTERNAL_HTTP_TIMEOUT = 900
_internal_app_post_lock = threading.Lock()


def _internal_api_base() -> str:
    return os.environ.get("CUTTLE_INTERNAL_API_BASE", "https://127.0.0.1:8080").rstrip("/")


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


def _internal_use_http_dispatch() -> bool:
    return os.environ.get("CUTTLE_INTERNAL_USE_HTTP", "").strip().lower() in ("1", "true", "yes")


def _internal_app_post(path: str, json_body: dict, timeout: int):
    """POST a Cuttle API route from inside the same process.

    Default uses Flask test_client() so we do not open a second TLS connection to
    Werkzeug while the browser is already on an SSE /api/chat stream.
    Set CUTTLE_INTERNAL_USE_HTTP=1 to force loopback HTTP(S).
    """
    if _internal_use_http_dispatch():
        import requests

        return requests.post(
            f"{_internal_api_base()}{path}",
            json=json_body,
            timeout=timeout,
            **_internal_requests_extra(),
        )
    try:
        from api import web_chat_api as wca

        with _internal_app_post_lock:
            return wca.app.test_client().post(
                path,
                json=json_body,
                content_type="application/json",
            )
    except Exception as e:
        print(f"[INTERNAL] in-process POST {path} failed ({e}); falling back to HTTP")
        import requests

        return requests.post(
            f"{_internal_api_base()}{path}",
            json=json_body,
            timeout=timeout,
            **_internal_requests_extra(),
        )


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
