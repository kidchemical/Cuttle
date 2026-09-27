"""Auth for device-worker coordinator APIs — auto-enroll, no manual token."""

from __future__ import annotations

import ipaddress
from typing import Optional, Tuple

from flask import Request

from api.device_workers.config import worker_token


def extract_bearer(request: Request) -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Cuttle-Worker-Token") or "").strip()


def is_loopback(remote: str) -> bool:
    r = (remote or "").strip()
    return r in ("127.0.0.1", "::1", "localhost")


def is_private_lan(remote: str) -> bool:
    """True for RFC1918 / loopback — same trust boundary as Electron Client on LAN."""
    r = (remote or "").strip()
    if is_loopback(r):
        return True
    try:
        ip = ipaddress.ip_address(r)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback)


def lan_access_enabled() -> bool:
    try:
        from managers.settings_manager import get_settings_manager

        discovery = get_settings_manager().get_setting("discovery") or {}
        if isinstance(discovery, dict) and "lan_access_enabled" in discovery:
            return bool(discovery.get("lan_access_enabled"))
    except Exception:
        pass
    return True  # home-lab default: Client already reaches host over LAN


def authorize_worker_request(request: Request) -> Tuple[bool, Optional[str]]:
    """
    Returns (ok, error_message).

    Accepts (any one):
    - Loopback (host local worker)
    - Per-device token from auto-enroll (Electron Client enroll)
    - Optional shared env/settings token (legacy / override only)
    """
    provided = extract_bearer(request)
    remote = (request.remote_addr or "").strip()

    if is_loopback(remote):
        return True, None

    if provided:
        expected = worker_token()
        if expected and provided == expected:
            return True, None
        try:
            from api.device_workers.store import get_store

            if get_store().lookup_enrolled_token(provided):
                return True, None
        except Exception:
            pass
        return False, "invalid or missing worker token"

    return False, "worker token required (enroll via Electron Client connect)"


def authorize_enroll_request(request: Request) -> Tuple[bool, Optional[str]]:
    """
    Enroll is allowed when the caller can already talk to this Flask as a Client:
    loopback, private LAN with lan_access_enabled, or an existing valid worker bearer.
    """
    remote = (request.remote_addr or "").strip()
    if is_loopback(remote):
        return True, None

    # Already enrolled / shared token — re-issue or refresh
    ok, _ = authorize_worker_request(request)
    if ok:
        return True, None

    if not is_private_lan(remote):
        return False, "enroll only from LAN or loopback"

    if not lan_access_enabled():
        return False, "LAN access disabled — enable discovery.lan_access_enabled"

    return True, None
