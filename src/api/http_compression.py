"""gzip for text responses (UI assets, HTML, JSON).

Werkzeug serves everything uncompressed. A chat pane loads ~75 scripts plus
stylesheets, the shell opens one pane iframe per split column, and Chromium
never caches responses from a self-signed HTTPS Host (cert-error responses
skip its HTTP cache), so a LAN Client refresh re-downloads the whole bundle per
pane. The full session list is ~0.8 MB of JSON that the activity broker
re-reads on every change pulse. Over Wi-Fi that saturated the Client link and
chats took 20 s+ to appear. Text compresses 5-15x.

Skipped: SSE / streamed bodies, ranges, non-2xx, already-encoded, small or
non-text bodies, clients without ``gzip`` in ``Accept-Encoding``. Static files
(``send_from_directory`` passthrough) are compressed once per ETag and cached.

Install before other ``after_request`` hooks (Flask runs them in reverse), so
this runs last and sees the final body (e.g. the stamped HTML).
This module must never import ``api.web_chat_api``.
"""

from __future__ import annotations

import gzip
import threading
from collections import OrderedDict

from flask import Flask, Response, request

MIN_SIZE = 1024
GZIP_LEVEL = 5
STATIC_CACHE_MAX_BYTES = 32 * 1024 * 1024

COMPRESSIBLE_MIMETYPES = frozenset({
    "text/html",
    "text/css",
    "text/plain",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
})

_cache: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def accepts_gzip(accept_encoding: str | None) -> bool:
    """True when ``Accept-Encoding`` allows gzip (``q=0`` refuses it)."""
    for part in (accept_encoding or "").split(","):
        token, _, params = part.strip().partition(";")
        if token.strip().lower() not in ("gzip", "*"):
            continue
        q = params.strip().lower()
        if q.startswith("q="):
            try:
                return float(q[2:]) > 0
            except ValueError:
                return False
        return True
    return False


def _cached_gzip(key: tuple[str, str], data: bytes) -> bytes:
    global _cache_bytes
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    body = gzip.compress(data, GZIP_LEVEL, mtime=0)
    with _cache_lock:
        if key not in _cache and len(body) <= STATIC_CACHE_MAX_BYTES:
            _cache[key] = body
            _cache_bytes += len(body)
            while _cache_bytes > STATIC_CACHE_MAX_BYTES and _cache:
                _, old = _cache.popitem(last=False)
                _cache_bytes -= len(old)
    return body


def clear_cache() -> None:
    global _cache_bytes
    with _cache_lock:
        _cache.clear()
        _cache_bytes = 0


def compress_response(response: Response) -> Response:
    """gzip ``response`` in place when the request and body qualify."""
    if response.status_code != 200 or request.method == "HEAD":
        return response
    if response.mimetype not in COMPRESSIBLE_MIMETYPES:
        return response
    if "Content-Encoding" in response.headers or "Content-Range" in response.headers:
        return response
    if not accepts_gzip(request.headers.get("Accept-Encoding")):
        return response
    passthrough = response.direct_passthrough
    if response.is_streamed and not passthrough:
        return response  # generators (SSE, progressive output) stay streamed
    length = response.content_length
    if length is not None and length < MIN_SIZE:
        return response

    response.direct_passthrough = False
    data = response.get_data()
    if len(data) < MIN_SIZE:
        return response

    etag, weak = response.get_etag()
    if passthrough and etag:
        body = _cached_gzip((request.path, etag), data)
    else:
        body = gzip.compress(data, GZIP_LEVEL, mtime=0)
    if len(body) >= len(data):
        return response

    response.set_data(body)
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    if etag and not weak:
        # Same representation, different bytes: a weak validator still
        # matches conditional requests (Werkzeug compares weakly).
        response.set_etag(etag, weak=True)
    return response


def install(app: Flask) -> None:
    app.after_request(compress_response)
