"""Host-approved pairing with durable, one-time credential handoff.

Pairing rows live in the worker SQLite database in the Cuttle home. Approval
stages a fresh credential without replacing the working one. Secret-authenticated
pickup activates it and consumes the handoff in one transaction; abandoned
approvals leave the previous credential usable, including across Flask restarts.
Only a hash of the client-random pairing secret is stored. Owner/UI responses
never include that hash or the token. The worker polls with its secret and
collects the approved token once, within the existing ten-minute pickup TTL.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from api.device_workers.store import get_store, new_device_token

TTL_SECONDS = 600.0
MAX_PENDING = 32


class PairingFullError(RuntimeError):
    """Too many pending pairing requests; the worker should retry later."""


_HEX_32 = re.compile(r"^[0-9a-fA-F]{32,}$")


def valid_pairing_secret(secret: str) -> bool:
    """Client-random secret, at least 32 hex chars."""
    return bool(secret) and bool(_HEX_32.match(secret.strip()))


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def _public(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "worker_id": row["worker_id"],
        "hostname": row["hostname"],
        "remote_addr": row["remote_addr"],
        "code": row["code"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "age_seconds": max(0.0, time.time() - float(row["created_at"])),
    }


def _sweep(conn, now: float, ttl_seconds: float = TTL_SECONDS) -> int:
    """Keep terminal verdicts and uncollected tokens for one TTL."""
    return conn.execute(
        "DELETE FROM enrollment_requests WHERE status != 'pending' AND updated_at <= ?",
        (now - ttl_seconds,),
    ).rowcount


def _expire(conn, now: float, ttl_seconds: float = TTL_SECONDS) -> int:
    return conn.execute(
        "UPDATE enrollment_requests SET status = 'expired', updated_at = ? "
        "WHERE status = 'pending' AND created_at <= ?",
        (now, now - ttl_seconds),
    ).rowcount


def _maintain(conn) -> None:
    now = time.time()
    _expire(conn, now)
    _sweep(conn, now)


def create_request(
    *, worker_id: str, hostname: str = "", remote_addr: str = "", pairing_secret: str,
) -> Dict[str, Any]:
    """Persist a pending pairing request. Raises ValueError on bad input."""
    wid = (worker_id or "").strip()
    if not wid:
        raise ValueError("worker_id required")
    if not valid_pairing_secret(pairing_secret or ""):
        raise ValueError("pairing_secret must be >=32 hex chars")
    addr = (remote_addr or "").strip()
    with get_store().enrollment_transaction() as conn:
        _maintain(conn)
        # Replacement, capacity check, and insertion share one write lock.
        conn.execute(
            "DELETE FROM enrollment_requests WHERE status = 'pending' "
            "AND worker_id = ? AND remote_addr = ?", (wid, addr),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM enrollment_requests WHERE status = 'pending'"
        ).fetchone()[0]
        if count >= MAX_PENDING:
            raise PairingFullError(
                f"too many pending pairing requests ({MAX_PENDING}); retry later"
            )
        rid = uuid.uuid4().hex
        now = time.time()
        conn.execute(
            "INSERT INTO enrollment_requests "
            "(id, worker_id, hostname, remote_addr, code, secret_sha256, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (rid, wid, (hostname or "").strip(), addr,
             f"{secrets.randbelow(1_000_000):06d}", _secret_hash(pairing_secret), now, now),
        )
        return _public(conn.execute(
            "SELECT * FROM enrollment_requests WHERE id = ?", (rid,)
        ).fetchone())


def expire_stale(ttl_seconds: float = TTL_SECONDS) -> int:
    with get_store().enrollment_transaction() as conn:
        return _expire(conn, time.time(), ttl_seconds)


def poll(request_id: str, pairing_secret: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Activate the staged credential and consume its handoff atomically.

    Activation happens only after authenticating the pairing secret. A failed
    transaction preserves both the previous credential and the retryable row.
    This is server-side pickup, not acknowledgment of network delivery.
    """
    rid = (request_id or "").strip()
    store = get_store()
    with store.enrollment_transaction() as conn:
        _maintain(conn)
        row = conn.execute(
            "SELECT * FROM enrollment_requests WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            return "not_found", None
        if not pairing_secret or not hmac.compare_digest(
            _secret_hash(pairing_secret), row["secret_sha256"]
        ):
            return "denied", None
        status = row["status"]
        if status == "approved":
            store._enroll_device(
                conn, worker_id=row["worker_id"], hostname=row["hostname"],
                remote_addr=row["remote_addr"], rotate=True, issued_token=row["token"],
            )
            payload = {"worker_id": row["worker_id"], "token": row["token"]}
            conn.execute("DELETE FROM enrollment_requests WHERE id = ?", (rid,))
            return "approved", payload
        return status, None


def decide(request_id: str, decision: str) -> Optional[Dict[str, Any]]:
    """Persist owner approval and its staged token without changing access.

    Repeated decisions return the existing verdict without minting again.
    The previous credential remains valid until authenticated pickup.
    """
    rid = (request_id or "").strip()
    dec = (decision or "").strip().lower()
    if dec in ("approve", "approved", "allow", "once"):
        dec = "approved"
    elif dec in ("deny", "denied", "reject", "no"):
        dec = "denied"
    else:
        return None
    store = get_store()
    with store.enrollment_transaction() as conn:
        _maintain(conn)
        row = conn.execute(
            "SELECT * FROM enrollment_requests WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            return _public(row)
        token = new_device_token() if dec == "approved" else ""
        now = time.time()
        conn.execute(
            "UPDATE enrollment_requests SET status = ?, token = ?, updated_at = ?, decided_at = ? "
            "WHERE id = ?", (dec, token, now, now, rid),
        )
        return _public(conn.execute(
            "SELECT * FROM enrollment_requests WHERE id = ?", (rid,)
        ).fetchone())


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Public row for the owner UI; never includes secrets."""
    with get_store().enrollment_transaction() as conn:
        _maintain(conn)
        row = conn.execute(
            "SELECT * FROM enrollment_requests WHERE id = ?", ((request_id or "").strip(),)
        ).fetchone()
        return _public(row) if row else None


def list_pending() -> List[Dict[str, Any]]:
    with get_store().enrollment_transaction() as conn:
        _maintain(conn)
        rows = conn.execute(
            "SELECT * FROM enrollment_requests WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [_public(row) for row in rows]


def clear_all() -> None:
    """Test helper; callers must provide an isolated worker database."""
    with get_store().enrollment_transaction() as conn:
        conn.execute("DELETE FROM enrollment_requests")
