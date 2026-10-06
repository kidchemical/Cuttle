"""Human-in-the-loop gate for unsafe shell job types (first use per worker process).

Covers execute_shell_unsafe and execute_shell_ssh. Pending requests live on the
coordinator (Flask) so the Cuttle UI can prompt. Per-process session grants live
in executor worker memory and clear when that worker process exits / restarts.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_lock = threading.RLock()
_pending: Dict[str, Dict[str, Any]] = {}
_HISTORY_LIMIT = 40
_history: List[Dict[str, Any]] = []

# Executor-process session grant (not shared across processes).
# One grant covers all unsafe shell kinds for this worker process.
_session_granted = False
_session_granted_at: float = 0.0

UNSAFE_SHELL_KINDS = frozenset({"execute_shell_unsafe", "execute_shell_ssh", "execute_shell"})


def unsafe_shell_approval_required() -> bool:
    try:
        from api.device_workers.config import _settings_block
        block = _settings_block()
        if "unsafe_shell_approval_required" in block:
            return bool(block["unsafe_shell_approval_required"])
        if "ssh_approval_required" in block:
            return bool(block["ssh_approval_required"])
    except Exception:
        pass
    return True


# Back-compat alias
ssh_approval_required = unsafe_shell_approval_required


def session_granted() -> bool:
    return bool(_session_granted)


def grant_session() -> None:
    global _session_granted, _session_granted_at
    _session_granted = True
    _session_granted_at = time.time()


def clear_session_grant() -> None:
    """Test helper / explicit revoke."""
    global _session_granted, _session_granted_at
    _session_granted = False
    _session_granted_at = 0.0


def create_request(
    *,
    worker_id: str,
    target: str = "",
    command_preview: str = "",
    job_id: str = "",
    job_kind: str = "execute_shell_ssh",
) -> Dict[str, Any]:
    rid = uuid.uuid4().hex
    now = time.time()
    kind = (job_kind or "execute_shell_ssh").strip()
    if kind == "execute_shell":
        kind = "execute_shell_unsafe"
    wid = (worker_id or "worker").strip() or "worker"
    tgt = (target or "").strip()
    if kind == "execute_shell_unsafe":
        msg = (
            f'Cuttle ({wid}) is requesting to attempt "execute_shell_unsafe" '
            f"(local free-form shell on this worker). Would you like to approve?"
        )
    else:
        msg = (
            f'Cuttle ({wid}) is requesting to attempt "execute_shell_ssh"'
            + (f" → {tgt}" if tgt else "")
            + ". Would you like to approve?"
        )
    row = {
        "id": rid,
        "worker_id": (worker_id or "").strip() or "unknown",
        "job_kind": kind,
        "target": tgt,
        "command_preview": (command_preview or "")[:240],
        "job_id": (job_id or "").strip(),
        "status": "pending",  # pending | once | session | deny | timeout
        "created_at": now,
        "updated_at": now,
        "decided_at": None,
        "message": msg,
    }
    with _lock:
        _pending[rid] = row
    return dict(row)


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    rid = (request_id or "").strip()
    with _lock:
        row = _pending.get(rid)
        return dict(row) if row else None


def list_pending() -> List[Dict[str, Any]]:
    with _lock:
        rows = [dict(v) for v in _pending.values() if v.get("status") == "pending"]
    rows.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    return rows


def decide(request_id: str, decision: str) -> Optional[Dict[str, Any]]:
    rid = (request_id or "").strip()
    dec = (decision or "").strip().lower()
    if dec in ("approve_once", "once", "one"):
        dec = "once"
    elif dec in ("approve_session", "session", "always_session"):
        dec = "session"
    elif dec in ("deny", "reject", "no"):
        dec = "deny"
    else:
        return None
    with _lock:
        row = _pending.get(rid)
        if not row:
            return None
        if row.get("status") != "pending":
            return dict(row)
        row["status"] = dec
        row["updated_at"] = time.time()
        row["decided_at"] = row["updated_at"]
        _history.append(dict(row))
        while len(_history) > _HISTORY_LIMIT:
            _history.pop(0)
        return dict(row)


def expire_stale(timeout_seconds: float = 300.0) -> int:
    now = time.time()
    n = 0
    with _lock:
        for row in _pending.values():
            if row.get("status") != "pending":
                continue
            if now - float(row.get("created_at") or 0) >= timeout_seconds:
                row["status"] = "timeout"
                row["updated_at"] = now
                row["decided_at"] = now
                _history.append(dict(row))
                n += 1
        while len(_history) > _HISTORY_LIMIT:
            _history.pop(0)
    return n


def wait_for_decision(
    request_id: str,
    *,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 0.5,
) -> str:
    """Block until decided. Returns once|session|deny|timeout."""
    deadline = time.time() + max(5.0, float(timeout_seconds))
    while time.time() < deadline:
        expire_stale(timeout_seconds=timeout_seconds)
        row = get_request(request_id)
        if not row:
            return "deny"
        st = str(row.get("status") or "")
        if st in ("once", "session", "deny", "timeout"):
            return st
        time.sleep(max(0.2, float(poll_seconds)))
    decide(request_id, "deny")
    # mark timeout explicitly if still pending
    with _lock:
        row = _pending.get(request_id)
        if row and row.get("status") == "deny" and not row.get("decided_at"):
            row["status"] = "timeout"
    return "timeout"


def request_via_coordinator(
    *,
    worker_id: str,
    target: str = "",
    command_preview: str = "",
    job_id: str = "",
    job_kind: str = "execute_shell_ssh",
    timeout_seconds: float = 300.0,
) -> str:
    """Create approval on coordinator (local import or HTTP) and wait.

    Returns once|session|deny|timeout.
    """
    # Same process as Flask (rare) — use in-memory store directly.
    if os.environ.get("CUTTLE_SSH_APPROVAL_LOCAL", "").strip() in ("1", "true", "yes"):
        row = create_request(
            worker_id=worker_id,
            target=target,
            command_preview=command_preview,
            job_id=job_id,
            job_kind=job_kind,
        )
        return wait_for_decision(row["id"], timeout_seconds=timeout_seconds)

    if os.environ.get("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL") or os.environ.get("CUTTLE_FLASK_URL"):
        base = (
            os.environ.get("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL")
            or os.environ.get("CUTTLE_FLASK_URL")
            or ""
        ).rstrip("/")
    else:
        from api.server_ports import resolve_with_env_file

        base = f"https://127.0.0.1:{resolve_with_env_file().https}"

    # Prefer in-process when coordinator URL points at us and store is importable
    # (daemon worker → Flask is cross-process, so HTTP).
    try:
        import json
        import ssl
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "worker_id": worker_id,
                "target": target,
                "command_preview": command_preview,
                "job_id": job_id,
                "job_kind": job_kind,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            base + "/api/workers/ssh-approval/request",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        ctx = None
        if base.lower().startswith("https://"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        rid = str((data.get("request") or {}).get("id") or data.get("id") or "").strip()
        if not rid:
            return "deny"
        deadline = time.time() + max(5.0, float(timeout_seconds))
        while time.time() < deadline:
            greq = urllib.request.Request(
                base + f"/api/workers/ssh-approval/{rid}",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(greq, timeout=10, context=ctx) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
            row = body.get("request") or body
            st = str(row.get("status") or "")
            if st in ("once", "session", "deny", "timeout"):
                return st
            time.sleep(0.5)
        return "timeout"
    except Exception:
        # Fallback: if HTTP fails (Flask down), deny rather than silent unsafe shell.
        return "deny"
