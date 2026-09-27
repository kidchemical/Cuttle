"""Shared helpers for reading the Cuttle account session token from a request.

Phone WebViews sometimes accept the login JSON body but fail to persist the
HttpOnly ``session_token`` cookie. Clients may also send the token as
``Authorization: Bearer …`` or ``X-Cuttle-Session-Token`` (see auth.js).
"""

from __future__ import annotations

from typing import Optional

from flask import request


def get_request_session_token() -> Optional[str]:
    """Return the auth session token from cookie or mobile bearer headers."""
    cookie = (request.cookies.get("session_token") or "").strip()
    if cookie:
        return cookie

    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        bearer = auth[7:].strip()
        if bearer:
            return bearer

    header = (request.headers.get("X-Cuttle-Session-Token") or "").strip()
    return header or None


def wants_session_token_in_body() -> bool:
    """True when the client needs the token in the JSON body (cookie unreliable)."""
    ua = request.headers.get("User-Agent") or ""
    if "CuttleMobile" in ua:
        return True
    client = (request.headers.get("X-Cuttle-Client") or "").strip().lower()
    return client in ("mobile", "cuttle-mobile", "android", "ios")
