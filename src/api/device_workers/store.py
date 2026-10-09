"""SQLite store for device workers + mesh jobs (host-first queue)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import data_db_dir

DEFAULT_DB_PATH = data_db_dir() / "device_workers.db"

_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS render_deliveries (
    batch_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL DEFAULT '',
    os TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    storage_json TEXT NOT NULL DEFAULT '{}',
    load_json TEXT NOT NULL DEFAULT '{}',
    interactive_priority TEXT NOT NULL DEFAULT 'low',
    ac_power INTEGER,
    last_seen REAL NOT NULL,
    registered_at REAL NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    target_worker_id TEXT,
    submitted_by TEXT NOT NULL DEFAULT '',
    requirements_json TEXT NOT NULL DEFAULT '{}',
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    claimed_by TEXT,
    claim_expires REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_dw_jobs_status ON jobs(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_dw_jobs_claimed ON jobs(claimed_by, status);
CREATE INDEX IF NOT EXISTS idx_dw_workers_seen ON workers(last_seen);

CREATE TABLE IF NOT EXISTS enrolled_devices (
    worker_id TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    hostname TEXT NOT NULL DEFAULT '',
    enrolled_at REAL NOT NULL,
    last_used REAL NOT NULL,
    remote_addr TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dw_enrolled_token ON enrolled_devices(token);

CREATE TABLE IF NOT EXISTS enrollment_requests (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    hostname TEXT NOT NULL DEFAULT '',
    remote_addr TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL,
    secret_sha256 TEXT NOT NULL,
    token TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied', 'expired')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    decided_at REAL,
    CHECK(status != 'approved' OR token != '')
);

"""


def new_device_token() -> str:
    """Generate a bearer for immediate enrollment or staged pairing."""
    import secrets

    return secrets.token_urlsafe(32)


def database_path() -> Path:
    override = os.getenv("CUTTLE_DEVICE_WORKERS_DB", "").strip()
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
    _migrate_jobs_columns(conn)
    _migrate_workers_columns(conn)
    return conn


def _migrate_jobs_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    altered = False
    if "submitted_by" not in cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN submitted_by TEXT NOT NULL DEFAULT ''"
        )
        altered = True
    if "expires_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN expires_at REAL")
        altered = True
    if "attempts" not in cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
        )
        altered = True
    if "max_attempts" not in cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 2"
        )
        altered = True
    if "progress_json" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN progress_json TEXT")
        altered = True
    if altered:
        conn.commit()


def _migrate_workers_columns(conn: sqlite3.Connection) -> None:
    """Durable version/RTT columns — survive older daemon upserts that wipe meta_json."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(workers)").fetchall()}
    altered = False
    if "cuttle_version" not in cols:
        conn.execute("ALTER TABLE workers ADD COLUMN cuttle_version TEXT NOT NULL DEFAULT ''")
        altered = True
    if "last_rtt_ms" not in cols:
        conn.execute("ALTER TABLE workers ADD COLUMN last_rtt_ms REAL")
        altered = True
    if "last_rtt_at" not in cols:
        conn.execute("ALTER TABLE workers ADD COLUMN last_rtt_at REAL")
        altered = True
    if altered:
        conn.commit()

def _dumps(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, separators=(",", ":"), default=str)


def _loads(raw: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _row_worker(row: sqlite3.Row) -> Dict[str, Any]:
    meta = _loads(row["meta_json"])
    keys = set(row.keys())
    col_ver = ""
    if "cuttle_version" in keys:
        col_ver = str(row["cuttle_version"] or "")
    col_rtt = row["last_rtt_ms"] if "last_rtt_ms" in keys else None
    col_rtt_at = row["last_rtt_at"] if "last_rtt_at" in keys else None
    meta_ver = ""
    meta_rtt = None
    meta_rtt_at = None
    if isinstance(meta, dict):
        meta_ver = str(meta.get("cuttle_version") or "")
        if isinstance(meta.get("last_rtt_ms"), (int, float)):
            meta_rtt = float(meta["last_rtt_ms"])
        if isinstance(meta.get("last_rtt_at"), (int, float)):
            meta_rtt_at = float(meta["last_rtt_at"])
    ver = col_ver or meta_ver
    rtt = col_rtt if col_rtt is not None else meta_rtt
    rtt_at = col_rtt_at if col_rtt_at is not None else meta_rtt_at
    return {
        "worker_id": row["worker_id"],
        "hostname": row["hostname"],
        "os": row["os"],
        "capabilities": _loads(row["capabilities_json"]),
        "storage": _loads(row["storage_json"]),
        "load": _loads(row["load_json"]),
        "interactive_priority": row["interactive_priority"] or "low",
        "ac_power": None if row["ac_power"] is None else bool(row["ac_power"]),
        "last_seen": row["last_seen"],
        "registered_at": row["registered_at"],
        "meta": meta if isinstance(meta, dict) else {},
        "cuttle_version": ver,
        "last_rtt_ms": float(rtt) if isinstance(rtt, (int, float)) else None,
        "last_rtt_at": float(rtt_at) if isinstance(rtt_at, (int, float)) else None,
    }


def _row_job(row: sqlite3.Row) -> Dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "priority": row["priority"],
        "target_worker_id": row["target_worker_id"],
        "submitted_by": (row["submitted_by"] if "submitted_by" in keys else "") or "",
        "requirements": _loads(row["requirements_json"]),
        "params": _loads(row["params_json"]),
        "result": _loads(row["result_json"], default=None),
        "progress": _loads(row["progress_json"], default=None)
        if "progress_json" in keys
        else None,
        "error": row["error"],
        "claimed_by": row["claimed_by"],
        "claim_expires": row["claim_expires"],
        "expires_at": row["expires_at"] if "expires_at" in keys else None,
        "attempts": int(row["attempts"] or 0) if "attempts" in keys else 0,
        "max_attempts": int(row["max_attempts"] or 2) if "max_attempts" in keys else 2,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


class DeviceWorkerStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or database_path())

    def upsert_worker(
        self,
        *,
        worker_id: str,
        hostname: str = "",
        os_name: str = "",
        capabilities: Optional[Dict[str, Any]] = None,
        storage: Optional[Dict[str, Any]] = None,
        load: Optional[Dict[str, Any]] = None,
        interactive_priority: str = "low",
        ac_power: Optional[bool] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        wid = (worker_id or "").strip()
        if not wid:
            raise ValueError("worker_id required")
        now = time.time()
        with _lock:
            conn = _connect(self.db_path)
            try:
                existing = conn.execute(
                    "SELECT * FROM workers WHERE worker_id = ?",
                    (wid,),
                ).fetchone()
                registered = float(existing["registered_at"]) if existing else now
                existing_meta = _loads(existing["meta_json"]) if existing else {}
                if not isinstance(existing_meta, dict):
                    existing_meta = {}
                incoming_meta = meta if isinstance(meta, dict) else {}
                # Merge so heartbeats (empty/partial meta) do not wipe last_rtt_*.
                merged_meta = {**existing_meta, **incoming_meta}
                ver = str(
                    incoming_meta.get("cuttle_version")
                    or merged_meta.get("cuttle_version")
                    or ""
                ).strip()
                if existing:
                    keys = set(existing.keys())
                    if "cuttle_version" in keys and not ver:
                        ver = str(existing["cuttle_version"] or "").strip()
                ac_val = None if ac_power is None else (1 if ac_power else 0)
                # Preserve durable RTT columns across register/heartbeat.
                keys = set(existing.keys()) if existing else set()
                prev_rtt = existing["last_rtt_ms"] if existing and "last_rtt_ms" in keys else None
                prev_rtt_at = existing["last_rtt_at"] if existing and "last_rtt_at" in keys else None
                # Prefer RTT from merged meta when columns empty (first write after probe).
                if prev_rtt is None and isinstance(merged_meta.get("last_rtt_ms"), (int, float)):
                    prev_rtt = float(merged_meta["last_rtt_ms"])
                if prev_rtt_at is None and isinstance(merged_meta.get("last_rtt_at"), (int, float)):
                    prev_rtt_at = float(merged_meta["last_rtt_at"])
                conn.execute(
                    """
                    INSERT INTO workers (
                        worker_id, hostname, os, capabilities_json, storage_json,
                        load_json, interactive_priority, ac_power, last_seen,
                        registered_at, meta_json, cuttle_version, last_rtt_ms, last_rtt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET
                        hostname=excluded.hostname,
                        os=excluded.os,
                        capabilities_json=excluded.capabilities_json,
                        storage_json=excluded.storage_json,
                        load_json=excluded.load_json,
                        interactive_priority=excluded.interactive_priority,
                        ac_power=excluded.ac_power,
                        last_seen=excluded.last_seen,
                        meta_json=excluded.meta_json,
                        cuttle_version=CASE
                            WHEN excluded.cuttle_version != '' THEN excluded.cuttle_version
                            ELSE workers.cuttle_version
                        END
                    """,
                    (
                        wid,
                        hostname or "",
                        os_name or "",
                        _dumps(capabilities or {}),
                        _dumps(storage or {}),
                        _dumps(load or {}),
                        interactive_priority or "low",
                        ac_val,
                        now,
                        registered,
                        _dumps(merged_meta),
                        ver,
                        prev_rtt,
                        prev_rtt_at,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM workers WHERE worker_id = ?", (wid,)
                ).fetchone()
                return _row_worker(row)
            finally:
                conn.close()

    def patch_worker_meta(self, worker_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Merge keys into workers.meta_json and durable RTT/version columns."""
        wid = (worker_id or "").strip()
        if not wid or not isinstance(patch, dict) or not patch:
            return None
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workers WHERE worker_id = ?", (wid,)
                ).fetchone()
                if not row:
                    return None
                meta = _loads(row["meta_json"])
                if not isinstance(meta, dict):
                    meta = {}
                meta.update(patch)
                ver = str(meta.get("cuttle_version") or row["cuttle_version"] or "").strip()
                rtt = meta.get("last_rtt_ms")
                if not isinstance(rtt, (int, float)):
                    rtt = row["last_rtt_ms"]
                rtt_at = meta.get("last_rtt_at")
                if not isinstance(rtt_at, (int, float)):
                    rtt_at = row["last_rtt_at"]
                conn.execute(
                    """
                    UPDATE workers SET
                        meta_json = ?,
                        cuttle_version = ?,
                        last_rtt_ms = ?,
                        last_rtt_at = ?
                    WHERE worker_id = ?
                    """,
                    (_dumps(meta), ver, rtt, rtt_at, wid),
                )
                conn.commit()
                full = conn.execute(
                    "SELECT * FROM workers WHERE worker_id = ?", (wid,)
                ).fetchone()
                return _row_worker(full) if full else None
            finally:
                conn.close()

    def list_workers(self, *, stale_after: Optional[float] = None) -> List[Dict[str, Any]]:
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM workers ORDER BY registered_at ASC, worker_id ASC"
                ).fetchall()
                out = [_row_worker(r) for r in rows]
            finally:
                conn.close()
        if stale_after is not None:
            now = time.time()
            for w in out:
                w["online"] = (now - float(w["last_seen"])) <= stale_after
        return out

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM workers WHERE worker_id = ?", (worker_id,)
                ).fetchone()
                return _row_worker(row) if row else None
            finally:
                conn.close()

    def remove_worker(
        self,
        worker_id: str,
        *,
        revoke_enroll: bool = True,
    ) -> Dict[str, Any]:
        """Drop a worker registry row (and optionally its token and pairing requests).

        Does not cancel in-flight jobs. A live Client that reconnects will
        re-register / re-enroll on the next cycle.
        """
        wid = (worker_id or "").strip()
        if not wid:
            raise ValueError("worker_id required")
        with self.enrollment_transaction() as conn:
            existing = conn.execute(
                "SELECT worker_id FROM workers WHERE worker_id = ?", (wid,)
            ).fetchone()
            enrolled = conn.execute(
                "SELECT worker_id FROM enrolled_devices WHERE worker_id = ?", (wid,)
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM workers WHERE worker_id = ?", (wid,))
            revoked = False
            cancelled = 0
            if revoke_enroll:
                conn.execute("DELETE FROM enrolled_devices WHERE worker_id = ?", (wid,))
                revoked = bool(enrolled)
                cancelled = conn.execute(
                    "DELETE FROM enrollment_requests WHERE worker_id = ?", (wid,)
                ).rowcount
            return {
                "worker_id": wid,
                "removed": bool(existing),
                "enroll_revoked": revoked,
                "found": bool(existing or enrolled or cancelled),
            }

    def submit_job(
        self,
        *,
        job_type: str,
        params: Optional[Dict[str, Any]] = None,
        requirements: Optional[Dict[str, Any]] = None,
        target_worker_id: Optional[str] = None,
        submitted_by: Optional[str] = None,
        priority: int = 50,
        job_id: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
        expires_at: Optional[float] = None,
        max_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        from api.device_workers.job_policy import (
            normalize_job_type,
            resolve_expires_at,
            resolve_max_attempts,
        )

        jtype = normalize_job_type((job_type or "").strip())
        if not jtype:
            raise ValueError("type required")
        params = params if isinstance(params, dict) else {}
        # Allow ttl / expires / max_attempts on params for convenience (stripped from stored copy).
        p = dict(params)
        if ttl_seconds is None and p.get("ttl_seconds") is not None:
            try:
                ttl_seconds = float(p.pop("ttl_seconds"))
            except (TypeError, ValueError):
                p.pop("ttl_seconds", None)
        if expires_at is None and p.get("expires_at") is not None:
            try:
                expires_at = float(p.pop("expires_at"))
            except (TypeError, ValueError):
                p.pop("expires_at", None)
        if max_attempts is None and p.get("max_attempts") is not None:
            try:
                max_attempts = int(p.pop("max_attempts"))
            except (TypeError, ValueError):
                p.pop("max_attempts", None)

        now = time.time()
        jid = job_id or uuid.uuid4().hex
        sender = (submitted_by or "").strip()[:120]
        exp = resolve_expires_at(
            job_type=jtype,
            created_at=now,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )
        attempts_cap = resolve_max_attempts(job_type=jtype, max_attempts=max_attempts)
        with _lock:
            conn = _connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, type, status, priority, target_worker_id, submitted_by,
                        requirements_json, params_json, created_at, updated_at,
                        expires_at, attempts, max_attempts
                    ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        jid,
                        jtype,
                        int(priority),
                        (target_worker_id or "").strip() or None,
                        sender,
                        _dumps(requirements or {}),
                        _dumps(p),
                        now,
                        now,
                        exp,
                        attempts_cap,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
                return _row_job(row)
            finally:
                conn.close()

    def expire_queued(self, *, now: Optional[float] = None) -> int:
        """Cancel queued jobs past expires_at (or created_at + type TTL fallback)."""
        from api.device_workers.job_policy import default_queue_ttl_seconds

        ts = float(now if now is not None else time.time())
        cancelled = 0
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'queued'"
                ).fetchall()
                for row in rows:
                    job = _row_job(row)
                    exp = job.get("expires_at")
                    if exp is None:
                        # Legacy rows: synthesize from type default
                        exp = float(job["created_at"]) + float(
                            default_queue_ttl_seconds(str(job.get("type") or ""))
                        )
                    try:
                        exp_f = float(exp)
                    except (TypeError, ValueError):
                        continue
                    if exp_f > ts:
                        continue
                    conn.execute(
                        """
                        UPDATE jobs SET
                            status = 'cancelled',
                            error = ?,
                            claimed_by = NULL,
                            claim_expires = NULL,
                            completed_at = ?,
                            updated_at = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (
                            "expired — queued past TTL",
                            ts,
                            ts,
                            job["id"],
                        ),
                    )
                    cancelled += 1
                if cancelled:
                    conn.commit()
                return cancelled
            finally:
                conn.close()

    def reclaim_expired(self) -> int:
        """Expire stale queued jobs; reclaim dead leases (requeue or cancel by policy)."""
        from api.device_workers.job_policy import reclaim_disposition

        now = time.time()
        changed = self.expire_queued(now=now)
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status IN ('claimed', 'running')
                      AND claim_expires IS NOT NULL
                      AND claim_expires < ?
                    """,
                    (now,),
                ).fetchall()
                for row in rows:
                    job = _row_job(row)
                    attempts = int(job.get("attempts") or 0)
                    max_attempts = int(job.get("max_attempts") or 1)
                    action, reason = reclaim_disposition(
                        job_type=str(job.get("type") or ""),
                        attempts=attempts,
                        max_attempts=max_attempts,
                    )
                    if action == "requeue":
                        conn.execute(
                            """
                            UPDATE jobs SET
                                status = 'queued',
                                claimed_by = NULL,
                                claim_expires = NULL,
                                error = ?,
                                updated_at = ?
                            WHERE id = ? AND status IN ('claimed', 'running')
                            """,
                            (reason[:2000], now, job["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE jobs SET
                                status = 'cancelled',
                                claimed_by = NULL,
                                claim_expires = NULL,
                                error = ?,
                                completed_at = ?,
                                updated_at = ?
                            WHERE id = ? AND status IN ('claimed', 'running')
                            """,
                            (reason[:2000], now, now, job["id"]),
                        )
                    changed += 1
                if changed:
                    conn.commit()
                return changed
            finally:
                conn.close()

    def claim_jobs(
        self,
        *,
        worker_id: str,
        capabilities: Optional[Dict[str, Any]] = None,
        limit: int = 1,
        lease_seconds: int = 600,
    ) -> List[Dict[str, Any]]:
        wid = (worker_id or "").strip()
        if not wid:
            return []
        caps = capabilities or {}
        self.reclaim_expired()
        claimed: List[Dict[str, Any]] = []
        now = time.time()
        expires = now + max(60, int(lease_seconds))
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 50
                    """
                ).fetchall()
                for row in rows:
                    if len(claimed) >= max(1, min(int(limit), 5)):
                        break
                    job = _row_job(row)
                    target = job.get("target_worker_id")
                    if target and target != wid:
                        continue
                    reqs = job.get("requirements") or {}
                    if not _requirements_met(reqs, caps):
                        continue
                    cur = conn.execute(
                        """
                        UPDATE jobs SET
                            status = 'claimed',
                            claimed_by = ?,
                            claim_expires = ?,
                            attempts = COALESCE(attempts, 0) + 1,
                            started_at = COALESCE(started_at, ?),
                            updated_at = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (wid, expires, now, now, job["id"]),
                    )
                    if cur.rowcount != 1:
                        continue
                    conn.commit()
                    refreshed = conn.execute(
                        "SELECT * FROM jobs WHERE id = ?", (job["id"],)
                    ).fetchone()
                    claimed.append(_row_job(refreshed))
                return claimed
            finally:
                conn.close()

    def heartbeat_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 600,
        progress: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = time.time()
        expires = now + max(60, int(lease_seconds))
        prog_json = None
        if isinstance(progress, dict) and progress:
            # Bound payload size for SQLite.
            safe = {
                "units_done": progress.get("units_done"),
                "units_total": progress.get("units_total"),
                "last_unit_id": str(progress.get("last_unit_id") or "")[:128] or None,
                "message": str(progress.get("message") or "")[:500],
                "updated_at": float(progress.get("updated_at") or now),
            }
            prog_json = _dumps(safe)
        with _lock:
            conn = _connect(self.db_path)
            try:
                if prog_json is not None:
                    cur = conn.execute(
                        """
                        UPDATE jobs SET
                            status = CASE WHEN status = 'claimed' THEN 'running' ELSE status END,
                            claim_expires = ?,
                            progress_json = ?,
                            updated_at = ?
                        WHERE id = ? AND claimed_by = ?
                          AND status IN ('claimed', 'running')
                        """,
                        (expires, prog_json, now, job_id, worker_id),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE jobs SET
                            status = CASE WHEN status = 'claimed' THEN 'running' ELSE status END,
                            claim_expires = ?,
                            updated_at = ?
                        WHERE id = ? AND claimed_by = ?
                          AND status IN ('claimed', 'running')
                        """,
                        (expires, now, job_id, worker_id),
                    )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = time.time()
        row = None
        with _lock:
            conn = _connect(self.db_path)
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET
                        status = 'succeeded',
                        result_json = ?,
                        error = NULL,
                        claim_expires = NULL,
                        completed_at = ?,
                        updated_at = ?
                    WHERE id = ? AND claimed_by = ?
                      AND status IN ('claimed', 'running')
                    """,
                    (_dumps(result or {}), now, now, job_id, worker_id),
                )
                conn.commit()
                if cur.rowcount != 1:
                    return False
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is not None:
            try:
                from api.device_workers.profiles import maybe_record_job_result

                maybe_record_job_result(self, worker_id, _row_job(row))
            except Exception:
                pass
            try:
                from api.device_workers.render_results import reconcile_job
                reconcile_job(self, _row_job(row))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Render attachment deferred; reconciliation can retry')
        return True

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        error: str,
        retry: bool = False,
        params_update: Optional[Dict[str, Any]] = None,
        partial_result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        now = time.time()
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if not row:
                    return False
                job = _row_job(row)
                if str(job.get("claimed_by") or "") != worker_id:
                    return False
                if str(job.get("status") or "") not in ("claimed", "running"):
                    return False

                from api.device_workers.job_policy import fail_disposition

                action, reason = fail_disposition(
                    job_type=str(job.get("type") or ""),
                    attempts=int(job.get("attempts") or 0),
                    max_attempts=int(job.get("max_attempts") or 1),
                    retry=bool(retry),
                )
                err_text = (error or "")[:2000]
                if action == "requeue" and reason:
                    # Keep worker error; append brief disposition for Jobs UI.
                    if reason not in err_text:
                        err_text = f"{err_text} ({reason})"[:2000]

                if action == "requeue":
                    params = dict(job.get("params") or {})
                    if isinstance(params_update, dict) and params_update:
                        params.update(params_update)
                    result_payload = None
                    if isinstance(partial_result, dict) and partial_result:
                        result_payload = _dumps(
                            {"partial": True, **partial_result}
                        )
                    cur = conn.execute(
                        """
                        UPDATE jobs SET
                            status = 'queued',
                            claimed_by = NULL,
                            claim_expires = NULL,
                            params_json = ?,
                            result_json = COALESCE(?, result_json),
                            error = ?,
                            updated_at = ?
                        WHERE id = ? AND claimed_by = ?
                          AND status IN ('claimed', 'running')
                        """,
                        (
                            _dumps(params),
                            result_payload,
                            err_text,
                            now,
                            job_id,
                            worker_id,
                        ),
                    )
                else:
                    result_payload = None
                    if isinstance(partial_result, dict) and partial_result:
                        result_payload = _dumps(
                            {"partial": True, **partial_result}
                        )
                    cur = conn.execute(
                        """
                        UPDATE jobs SET
                            status = 'failed',
                            error = ?,
                            result_json = COALESCE(?, result_json),
                            claim_expires = NULL,
                            completed_at = ?,
                            updated_at = ?
                        WHERE id = ? AND claimed_by = ?
                          AND status IN ('claimed', 'running')
                        """,
                        (
                            err_text,
                            result_payload,
                            now,
                            now,
                            job_id,
                            worker_id,
                        ),
                    )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def cancel_job(self, job_id: str, *, reason: str = "cancelled") -> Optional[Dict[str, Any]]:
        """Cancel queued/claimed/running jobs. Terminal jobs unchanged (returns None)."""
        jid = (job_id or "").strip()
        if not jid:
            return None
        now = time.time()
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
                if not row:
                    return None
                job = _row_job(row)
                st = str(job.get("status") or "")
                if st in ("succeeded", "failed", "cancelled"):
                    return None
                conn.execute(
                    """
                    UPDATE jobs SET
                        status = 'cancelled',
                        error = ?,
                        claimed_by = NULL,
                        claim_expires = NULL,
                        completed_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    ((reason or "cancelled")[:2000], now, now, jid),
                )
                conn.commit()
                refreshed = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (jid,)
                ).fetchone()
                return _row_job(refreshed) if refreshed else None
            finally:
                conn.close()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                return _row_job(row) if row else None
            finally:
                conn.close()

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        lim = max(1, min(int(limit), 200))
        with _lock:
            conn = _connect(self.db_path)
            try:
                if status:
                    wanted = [s.strip() for s in status.split(",") if s.strip()]
                    placeholders = ",".join("?" for _ in wanted)
                    rows = conn.execute(
                        f"""
                        SELECT * FROM jobs
                        WHERE status IN ({placeholders})
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        (*wanted, lim),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                        (lim,),
                    ).fetchall()
                return [_row_job(r) for r in rows]
            finally:
                conn.close()

    def list_batch_jobs(self, batch_id: str) -> List[Dict[str, Any]]:
        """Complete batch inventory, independent of the recent-jobs UI limit."""
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE json_extract(params_json, '$.batch_id') = ? "
                    "ORDER BY created_at, id", (batch_id,),
                ).fetchall()
                return [_row_job(r) for r in rows]
            finally:
                conn.close()

    def enroll_device(
        self,
        *,
        worker_id: str,
        hostname: str = "",
        remote_addr: str = "",
        rotate: bool = False,
    ) -> Dict[str, Any]:
        """Issue (or reuse) a per-device bearer token — no manual .env setup."""
        with self.enrollment_transaction() as conn:
            return self._enroll_device(
                conn, worker_id=worker_id, hostname=hostname,
                remote_addr=remote_addr, rotate=rotate,
            )

    @contextmanager
    def enrollment_transaction(self):
        """Serialize pairing and credential changes, with rollback on failure."""
        with _lock:
            conn = _connect(self.db_path)
            try:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    yield conn
            finally:
                conn.close()

    def _enroll_device(
        self, conn: sqlite3.Connection, *, worker_id: str,
        hostname: str = "", remote_addr: str = "", rotate: bool = False,
        issued_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a credential inside the caller's enrollment transaction.

        Pairing pickup supplies its previously staged token. Replacing an
        active credential also invalidates other approved handoffs for the
        worker, so a later pickup cannot resurrect a superseded credential.
        """
        wid = (worker_id or "").strip()
        if not wid:
            raise ValueError("worker_id required")
        now = time.time()
        row = conn.execute(
            "SELECT token, enrolled_at FROM enrolled_devices WHERE worker_id = ?",
            (wid,),
        ).fetchone()
        if row and not rotate:
            token = row["token"]
            enrolled_at = float(row["enrolled_at"])
            conn.execute(
                "UPDATE enrolled_devices SET hostname = ?, last_used = ?, remote_addr = ? "
                "WHERE worker_id = ?",
                (hostname or "", now, remote_addr or "", wid),
            )
        else:
            token = issued_token if issued_token is not None else new_device_token()
            if not token:
                raise ValueError("worker token required")
            enrolled_at = now
            conn.execute(
                """
                INSERT INTO enrolled_devices (
                    worker_id, token, hostname, enrolled_at, last_used, remote_addr
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    token = excluded.token,
                    hostname = excluded.hostname,
                    enrolled_at = excluded.enrolled_at,
                    last_used = excluded.last_used,
                    remote_addr = excluded.remote_addr
                """,
                (wid, token, hostname or "", enrolled_at, now, remote_addr or ""),
            )
        if rotate:
            conn.execute(
                "DELETE FROM enrollment_requests WHERE worker_id = ? "
                "AND status = 'approved' AND token != ?", (wid, token),
            )
        return {
            "worker_id": wid, "token": token, "hostname": hostname or "",
            "enrolled_at": enrolled_at, "rotated": bool(rotate or not row),
        }

    def ensure_local_worker_token(self, worker_id: str, *, hostname: str = "") -> str:
        """Token for the host's own local worker loop (in-process, no HTTP).

        Reuses the existing enrolled token or mints one. The daemon calls
        this at startup so the local loop authenticates as exactly its own
        worker id instead of relying on loopback trust.
        """
        wid = (worker_id or "").strip()
        if not wid:
            raise ValueError("worker_id required")
        return str(self.enroll_device(worker_id=wid, hostname=hostname)["token"] or "")

    def is_enrolled(self, worker_id: str) -> bool:
        wid = (worker_id or "").strip()
        if not wid:
            return False
        with _lock:
            conn = _connect(self.db_path)
            try:
                return conn.execute(
                    "SELECT 1 FROM enrolled_devices WHERE worker_id = ?", (wid,)
                ).fetchone() is not None
            finally:
                conn.close()

    def lookup_enrolled_token(self, token: str) -> Optional[str]:
        tok = (token or "").strip()
        if not tok:
            return None
        now = time.time()
        with _lock:
            conn = _connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT worker_id FROM enrolled_devices WHERE token = ?",
                    (tok,),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE enrolled_devices SET last_used = ? WHERE token = ?",
                    (now, tok),
                )
                conn.commit()
                return str(row["worker_id"])
            finally:
                conn.close()

    def list_enrolled(self) -> List[Dict[str, Any]]:
        with _lock:
            conn = _connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT worker_id, hostname, enrolled_at, last_used, remote_addr "
                    "FROM enrolled_devices ORDER BY last_used DESC"
                ).fetchall()
                return [
                    {
                        "worker_id": r["worker_id"],
                        "hostname": r["hostname"],
                        "enrolled_at": r["enrolled_at"],
                        "last_used": r["last_used"],
                        "remote_addr": r["remote_addr"],
                    }
                    for r in rows
                ]
            finally:
                conn.close()


def _requirements_met(reqs: Dict[str, Any], caps: Dict[str, Any]) -> bool:
    if not reqs:
        return True
    for key, needed in reqs.items():
        if key == "storage" and isinstance(needed, list):
            storage = caps.get("storage") if isinstance(caps.get("storage"), dict) else {}
            # Also accept top-level storage from worker blob merged into caps
            for name in needed:
                if not storage.get(name) and not caps.get(f"storage_{name}"):
                    # Fall back: if caps has nested storage under worker registration
                    # caller should pass flattened caps; soft-fail only when explicitly false
                    if storage and name not in storage:
                        return False
                    if not storage:
                        continue
            continue
        have = caps.get(key)
        if needed is True:
            if not have:
                return False
        elif needed is False:
            continue
        else:
            if have != needed and str(have).lower() != str(needed).lower():
                return False
    return True


_store: Optional[DeviceWorkerStore] = None


def get_store() -> DeviceWorkerStore:
    global _store
    if _store is None:
        _store = DeviceWorkerStore()
    return _store
