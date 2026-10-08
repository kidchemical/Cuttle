"""Auth for device-worker coordinator APIs — host-approved pairing, no manual token."""

from __future__ import annotations

import ipaddress
import secrets
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
        return False
    return False


def resolve_worker_identity(request: Request) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (ok, bound_worker_id, error_message).

    Accepts (any one):
    - Optional shared env/settings token (legacy override) — unbound.
      Kept for existing installs that set CUTTLE_DEVICE_WORKERS_TOKEN;
      prefer per-device tokens from host-approved pairing.
    - Per-device token from host-approved pairing — bound to the worker it
      was issued to; that caller may only act as that worker.

    There is no loopback exemption: the host's own local worker loop
    authenticates with its own per-device token (see
    ``DeviceWorkerStore.ensure_local_worker_token``).
    """
    provided = extract_bearer(request)

    if provided:
        expected = worker_token()
        if expected and secrets.compare_digest(provided, expected):
            return True, None, None
        try:
            from api.device_workers.store import get_store

            bound = get_store().lookup_enrolled_token(provided)
            if bound:
                return True, bound, None
        except Exception:
            pass
        return False, None, "invalid or missing worker token"

    return False, None, "worker token required (enroll via Electron Client connect)"


def authorize_worker_request(request: Request) -> Tuple[bool, Optional[str]]:
    """Returns (ok, error_message); see :func:`resolve_worker_identity`."""
    ok, _bound, err = resolve_worker_identity(request)
    return ok, err


def authorize_enroll_request(request: Request) -> Tuple[bool, Optional[str], bool]:
    """
    Returns (ok, error_message, may_reissue).

    Only a currently valid bearer enrolls directly here. Callers without one
    must go through host-approved pairing (see ``pairing_eligible``);
    loopback alone authorizes nothing.

    ``may_reissue`` is True only for the legacy shared override token. A
    device bearer may re-enroll only as its own worker (checked by the route);
    Callers without a valid bearer file a pairing request instead — no credential
    is minted or returned until the host owner approves.
    """
    if extract_bearer(request):
        ok, bound, _err = resolve_worker_identity(request)
        if ok:
            return True, None, bound is None
    return False, "worker credential required (pair via host approval)", False


def pairing_eligible(request: Request) -> Tuple[bool, Optional[str]]:
    """May this peer file a pairing request (no credential minted yet)?

    Loopback, or private LAN with lan_access_enabled. A stale saved
    credential (host DB reset) pairs like a bare LAN caller.
    """
    remote = (request.remote_addr or "").strip()
    if is_loopback(remote):
        return True, None
    if not is_private_lan(remote):
        return False, "enroll only from LAN or loopback"
    if not lan_access_enabled():
        return False, "LAN access disabled — enable discovery.lan_access_enabled"
    return True, None
