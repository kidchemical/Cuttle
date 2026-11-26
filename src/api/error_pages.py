"""Cuttle-styled error pages for browser page loads (404, 403, 500, …).

Only document / iframe navigations that ask for HTML get the page
(``src/web/error_page.html`` with the error injected as JSON). API calls,
fetches, images and every non-HTML caller keep Flask's normal error response,
so nothing that parses errors changes. Routing redirects (3xx) are untouched.

Electron and the Android app load the same file locally when Cuttle cannot be
reached at all (see the file's header). This module must never import
``api.web_chat_api``.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from flask import Flask, Response, request
from werkzeug.exceptions import HTTPException, InternalServerError

PAGE = Path(__file__).resolve().parents[1] / "web" / "error_page.html"
MARKER = "null/*CUTTLE_ERROR_DATA*/"

_cache: dict = {"mtime": None, "text": ""}


def _template() -> str:
    try:
        mtime = PAGE.stat().st_mtime
    except OSError:
        return ""
    if _cache["mtime"] != mtime:
        _cache["text"] = PAGE.read_text(encoding="utf-8")
        _cache["mtime"] = mtime
    return _cache["text"]


def wants_html_page() -> bool:
    """True for a browser loading a page (tab, pane iframe), not a fetch/API/asset."""
    if request.method not in ("GET", "HEAD") or request.path.startswith("/api/"):
        return False
    dest = (request.headers.get("Sec-Fetch-Dest") or "").lower()
    if dest:
        return dest in ("document", "iframe", "frame")
    accept = request.accept_mimetypes
    return accept.accept_html and accept.best_match(["application/json", "text/html"]) == "text/html"


def render(status: int, name: str, message: str = "", request_id: Optional[str] = None) -> Optional[Response]:
    """The error page as a response, or None when the template is missing."""
    html = _template()
    if MARKER not in html:
        return None
    data = {
        "kind": "http",
        "status": int(status),
        "name": name or "",
        "message": message or "",
        "path": request.full_path.rstrip("?") if request.query_string else request.path,
        "method": request.method,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": request_id or "",
        "platform": "server",
    }
    # JSON inside <script>: never let a value close the tag.
    payload = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    resp = Response(html.replace(MARKER, payload, 1), status=int(status), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _handle(exc: HTTPException):
    code = getattr(exc, "code", None)
    if not code or code < 400 or not wants_html_page():
        return exc
    request_id = uuid.uuid4().hex[:10]
    if code >= 500:
        original = getattr(exc, "original_exception", None)
        print(f"[HTTP {code}] {request_id} {request.method} {request.path}: "
              f"{original!r}" if original else f"[HTTP {code}] {request_id} {request.method} {request.path}",
              flush=True)
    # Werkzeug descriptions are generic; the page has its own copy per status.
    resp = render(code, exc.name, exc.description if code >= 500 else "", request_id)
    if resp is None:
        return exc
    # Keep headers the exception set (e.g. Allow on 405, Retry-After on 429/503).
    for key, value in exc.get_headers():
        if key.lower() not in ("content-type", "content-length"):
            resp.headers[key] = value
    return resp


def install(app: Flask) -> None:
    """Route HTTP errors (and unhandled exceptions → 500) through the page."""
    app.register_error_handler(HTTPException, _handle)
    app.register_error_handler(InternalServerError, _handle)
