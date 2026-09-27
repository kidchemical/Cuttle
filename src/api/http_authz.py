"""Reusable HTTP authentication and authorization checks.

Cuttle historically treated “can reach Flask” as enough for many mutating
routes. LAN HTTP on all interfaces makes that unsafe. These helpers are the
shared owner / session / UI-operator gates for those surfaces.

Authorization model
-------------------
``OWNER_USER_EMAIL`` (env) selects the mode:

- **Unset (single-user / home-lab):** every authenticated **non-guest** account
  is an owner. Guests (``auth_provider=guest``) are never owners. This matches
  a machine with one operator plus optional guest previews.
- **Set (multi-user):** only the account whose email or username equals that
  value is an owner. Other signed-in users are authenticated non-owners.

Worker mesh **administration** (submit/cancel/self-update, …) requires an
**owner session** (cookie or ``Authorization: Bearer`` session token). Loopback
is **not** a substitute: Host Electron and the Jobs UI send the same cookies as
the rest of the shell; ``python -m api.device_workers.cli`` talks to the store
in-process and does not use these HTTP admin routes.

Worker **runtime** (register/claim/heartbeat/complete) still uses
``authorize_worker_request`` (loopback or device token) — that is not this
module.

Roles used by tests and routes:

- anonymous
- guest (auth_provider=guest)
- authenticated non-owner
- owner (see OWNER_USER_EMAIL above)
- worker bearer (device token — never treated as UI operator)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from flask import Request, jsonify, request

from api.auth_session import get_request_session_token
from api.cuttle_ui_capabilities import numeric_chat_session_id

JsonError = Tuple[Any, int]


def owner_email() -> str:
    return (os.getenv("OWNER_USER_EMAIL") or "").strip().lower()


def is_guest_user(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    return (user.get("auth_provider") or "").strip().lower() == "guest"


def is_owner_user(user: Optional[Dict[str, Any]]) -> bool:
    """See module docstring: guests never; unset OWNER_USER_EMAIL → single-user."""
    if not user or is_guest_user(user):
        return False
    expected = owner_email()
    if not expected:
        return True
    email = (user.get("email") or "").strip().lower()
    uname = (user.get("username") or "").strip().lower()
    return email == expected or uname == expected


def is_loopback_addr(remote: str) -> bool:
    r = (remote or "").strip()
    if r in ("127.0.0.1", "::1", "localhost"):
        return True
    if r.startswith("::ffff:127."):
        return True
    return False


def request_is_loopback(req: Optional[Request] = None) -> bool:
    req = req or request
    return is_loopback_addr((req.remote_addr or "").strip())


def current_user() -> Optional[Dict[str, Any]]:
    token = get_request_session_token()
    if not token:
        return None
    try:
        from api.auth_db import get_auth_db

        return get_auth_db().verify_auth_session(token)
    except Exception:
        return None


def _err(message: str, status: int = 401, extra: Optional[Dict[str, Any]] = None) -> JsonError:
    body = {"success": False, "error": message}
    if extra:
        body.update(extra)
    return jsonify(body), status


def require_authenticated() -> Tuple[Optional[Dict[str, Any]], Optional[JsonError]]:
    user = current_user()
    if not user:
        return None, _err("Not authenticated.")
    return user, None


def require_owner() -> Tuple[Optional[Dict[str, Any]], Optional[JsonError]]:
    user, err = require_authenticated()
    if err:
        return None, err
    if not is_owner_user(user):
        return None, _err("Owner privileges required.", 403)
    return user, None


def require_ui_operator() -> Tuple[Optional[Dict[str, Any]], Optional[JsonError]]:
    """Mesh administration (Jobs UI, HTTP worker admin).

    Owner session only — not loopback, not a worker device token. Loopback
    exemption would also bless reverse-proxied clients whose peer is 127.0.0.1
    and any local curl without cookies.
    """
    user, err = require_authenticated()
    if err:
        return None, err
    if not is_owner_user(user):
        return None, _err("Owner privileges required.", 403)
    return user, None


def require_chat_session_access(
    session_id: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[JsonError]]:
    """Authenticated user must own the numeric chat session."""
    user, err = require_authenticated()
    if err:
        return None, None, err
    nid = numeric_chat_session_id(session_id)
    if not nid:
        return None, None, _err("missing session", 400)
    try:
        from api.auth_db import get_auth_db

        sess = get_auth_db().get_chat_session(nid, user["id"])
    except Exception:
        sess = None
    if not sess:
        return None, None, _err("Session not found or access denied", 404)
    return user, nid, None
