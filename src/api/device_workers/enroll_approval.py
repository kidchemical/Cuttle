"""Host-approved pairing for device-worker enrollment.

A bare caller (no valid device bearer) never receives a token directly.
Instead it files a pending pairing request carrying a client-random
``pairing_secret`` and shows the user a short ``code``. The host owner
approves/denies in Jobs → Devices; the worker polls with its secret and
receives the minted token exactly once.

Process-lifetime state (same pattern as ``ssh_approval``): pending rows live
in this module, keyed by request id. The minted token is attached on approve
and handed out once on poll, then the row is deleted.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.RLock()
_pending: Dict[str, Dict[str, Any]] = {}

TTL_SECONDS = 600.0

# Hard bound on process-lifetime pairing rows.
MAX_PENDING = 32


class PairingFullError(RuntimeError):
    """Too many pending pairing requests; the worker should retry later."""


_HEX_32 = re.compile(r"^[0-9a-fA-F]{32,}$")


def valid_pairing_secret(secret: str) -> bool:
    """Client-random secret, at least 32 hex chars."""
    return bool(secret) and bool(_HEX_32.match(secret.strip()))


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def create_request(
    *,
    worker_id: str,
    hostname: str = "",
    remote_addr: str = "",
    pairing_secret: str,
) -> Dict[str, Any]:
    """File a pending pairing request. Raises ValueError on bad input."""
    wid = (worker_id or "").strip()
    if not wid:
        raise ValueError("worker_id required")
    if not valid_pairing_secret(pairing_secret or ""):
        raise ValueError("pairing_secret must be >=32 hex chars")
    addr = (remote_addr or "").strip()
    _sweep_terminal()
    with _lock:
        # A retry from the same device replaces its older pending request.
        for rid, row in list(_pending.items()):
            if row.get("status") == "pending" and row.get("worker_id") == wid \
                    and row.get("remote_addr") == addr:
                del _pending[rid]
        if sum(1 for row in _pending.values() if row.get("status") == "pending") >= MAX_PENDING:
            raise PairingFullError(
                f"too many pending pairing requests ({MAX_PENDING}); retry later"
            )
    rid = uuid.uuid4().hex
    now = time.time()
    code = f"{secrets.randbelow(1_000_000):06d}"
    row = {
        "id": rid,
        "worker_id": wid,
        "hostname": (hostname or "").strip(),
        "remote_addr": addr,
        "code": code,
        "secret_sha256": _secret_hash(pairing_secret),
        "token": "",
        "status": "pending",  # pending | approved | denied | expired
        "created_at": now,
        "updated_at": now,
        "decided_at": None,
    }
    with _lock:
        _pending[rid] = row
    return _public(row)


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "worker_id": row["worker_id"],
        "hostname": row["hostname"],
        "remote_addr": row["remote_addr"],
        "code": row["code"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "age_seconds": max(0.0, time.time() - float(row.get("created_at") or 0)),
    }


def _check_secret(row: Dict[str, Any], pairing_secret: str) -> bool:
    if not pairing_secret:
        return False
    given = _secret_hash(pairing_secret)
    return hmac.compare_digest(given, str(row.get("secret_sha256") or ""))


def _sweep_terminal(ttl_seconds: float = TTL_SECONDS) -> int:
    """Delete terminal rows (denied/expired, or approved but never picked up).

    Terminal rows stay queryable for one TTL so workers polling every few
    seconds still see the verdict; afterwards they are deleted instead of
    accumulating forever.
    """
    now = time.time()
    doomed = []
    with _lock:
        for rid, row in _pending.items():
            if row.get("status") == "pending":
                continue
            if now - float(row.get("updated_at") or 0) >= ttl_seconds:
                doomed.append(rid)
        for rid in doomed:
            _pending.pop(rid, None)
    return len(doomed)


def expire_stale(ttl_seconds: float = TTL_SECONDS) -> int:
    now = time.time()
    n = 0
    with _lock:
        for row in _pending.values():
            if row.get("status") != "pending":
                continue
            if now - float(row.get("created_at") or 0) >= ttl_seconds:
                row["status"] = "expired"
                row["updated_at"] = now
                n += 1
    return n


def poll(request_id: str, pairing_secret: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Worker poll. Returns (status, payload).

    ``approved`` payload is ``{"worker_id": ..., "token": ...}`` and is
    delivered exactly once — the row is deleted on first delivery.
    Wrong secret → ("denied", None); unknown id → ("not_found", None).
    """
    expire_stale()
    _sweep_terminal()
    rid = (request_id or "").strip()
    with _lock:
        row = _pending.get(rid)
        if not row:
            return "not_found", None
        if not _check_secret(row, pairing_secret or ""):
            return "denied", None
        status = str(row.get("status") or "pending")
        if status == "approved":
            payload = {"worker_id": row["worker_id"], "token": str(row.get("token") or "")}
            del _pending[rid]
            return "approved", payload
        return status, None


def decide(request_id: str, decision: str) -> Optional[Dict[str, Any]]:
    """Owner decision: approve | deny. Returns the public row or None."""
    rid = (request_id or "").strip()
    dec = (decision or "").strip().lower()
    if dec in ("approve", "approved", "allow", "once"):
        dec = "approved"
    elif dec in ("deny", "denied", "reject", "no"):
        dec = "denied"
    else:
        return None
    with _lock:
        row = _pending.get(rid)
        if not row:
            return None
        if row.get("status") != "pending":
            return _public(row)
        row["status"] = dec
        row["updated_at"] = time.time()
        row["decided_at"] = row["updated_at"]
        return _public(row)


def attach_token(request_id: str, token: str) -> bool:
    """Store the minted token on an approved request for one-time pickup."""
    rid = (request_id or "").strip()
    with _lock:
        row = _pending.get(rid)
        if not row or row.get("status") != "approved":
            return False
        row["token"] = token or ""
        row["updated_at"] = time.time()
        return True


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Public row for the owner UI (any status; never includes secrets)."""
    expire_stale()
    _sweep_terminal()
    rid = (request_id or "").strip()
    with _lock:
        row = _pending.get(rid)
        return _public(row) if row else None


def list_pending() -> List[Dict[str, Any]]:
    expire_stale()
    _sweep_terminal()
    with _lock:
        rows = [_public(v) for v in _pending.values() if v.get("status") == "pending"]
    rows.sort(key=lambda r: float(r.get("created_at") or 0))
    return rows


def clear_all() -> None:
    """Test helper."""
    with _lock:
        _pending.clear()
