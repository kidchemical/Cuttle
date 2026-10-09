"""Control + telemetry service for the Jobs surface.

Control dispatches to the real owners (never mutating them here):
  - cancel run   -> chat_run_registry.cancel_session_runs(session)
  - cancel mesh  -> device_workers store cancel_job
  - rerun mesh   -> device_workers store submit_job with the original spec

Telemetry aggregates the same ledger the list reads, so the page and any
dashboard handler see one consistent view.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, Optional


class JobNotFoundError(Exception):
    pass


class JobNotCancellableError(Exception):
    pass


class JobNotRerunnableError(Exception):
    pass


def _mesh_store():
    from api.device_workers.store import get_store

    return get_store()


def _agent_events_store():
    from api.agent_events.store import EventStore, state_dir

    return EventStore(state_dir())


# ── control ─────────────────────────────────────────────────────────────────


def cancel_job(kind: str, job_id: str) -> Dict[str, Any]:
    """Cancel a job by source kind. Runs map to their chat session's Stop."""
    if kind == "run":
        row = _agent_events_store().run(job_id)
        if not row:
            raise JobNotFoundError(f"run {job_id} not found")
        if row["status"] != "running":
            raise JobNotCancellableError("only running runs can be cancelled")
        sid = row.get("chat_session_id")
        if not sid:
            raise JobNotCancellableError("run has no chat session to cancel")
        from api.chat_run_registry import cancel_session_runs

        return {
            "success": True,
            "kind": "run",
            "id": job_id,
            "result": cancel_session_runs(sid) or {},
        }
    if kind == "mesh":
        job = _mesh_store().cancel_job(job_id, reason="cancelled from Jobs")
        if not job:
            raise JobNotCancellableError(f"mesh job {job_id} not found or not cancellable")
        return {"success": True, "kind": "mesh", "id": job_id, "result": job}
    raise JobNotFoundError(f"unknown job kind {kind}")


def rerun_mesh_job(job_id: str) -> Dict[str, Any]:
    """Re-submit a terminal mesh job with its original spec (new job id)."""
    store = _mesh_store()
    job = store.get_job(job_id)
    if not job:
        raise JobNotFoundError(f"mesh job {job_id} not found")
    if job.get("status") not in ("succeeded", "failed", "cancelled", "expired"):
        raise JobNotRerunnableError("only terminal mesh jobs can be rerun")
    new = store.submit_job(
        job_type=str(job.get("type") or ""),
        params=job.get("params") or {},
        requirements=job.get("requirements") or {},
        target_worker_id=job.get("target_worker_id") or None,
        submitted_by=str(job.get("submitted_by") or "") or None,
        priority=int(job.get("priority") or 50),
    )
    return {"success": True, "id": job_id, "job": new}


# ── telemetry ───────────────────────────────────────────────────────────────


def _cost(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def job_stats(*, hours: int = 24) -> Dict[str, Any]:
    """Aggregations over the unified ledger for the telemetry pane."""
    try:
        hours = max(1, min(int(hours), 24 * 30))
    except (TypeError, ValueError):
        hours = 24
    since = time.time() - hours * 3600

    runs = [r for r in _agent_events_store().recent_runs(limit=500) if (r.get("started_at") or 0) >= since]
    mesh = [m for m in _mesh_store().list_jobs(limit=200) if (m.get("created_at") or m.get("updated_at") or 0) >= since]

    run_status: Dict[str, int] = {}
    for r in runs:
        key = str(r.get("status") or "unknown")
        run_status[key] = run_status.get(key, 0) + 1

    mesh_status: Dict[str, int] = {}
    for m in mesh:
        key = str(m.get("status") or "unknown")
        mesh_status[key] = mesh_status.get(key, 0) + 1

    durations = []
    cost_total = 0.0
    by_agent: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        meta = r.get("metadata") or {}
        harness = meta.get("harness") or {}
        agent = str(harness.get("agent_id") or r.get("agent_id") or "agent")
        started = r.get("started_at")
        finished = meta.get("finished_at") or r.get("finished_at")
        if started and finished:
            durations.append(max(0.0, finished - started))
        bucket = by_agent.setdefault(
            agent, {"agent": agent, "count": 0, "durations": [], "cost": 0.0, "tokens": 0}
        )
        bucket["count"] += 1
        if started and finished:
            bucket["durations"].append(max(0.0, finished - started))
        c = _cost(meta.get("total_cost"))
        if c is not None:
            bucket["cost"] += c
            cost_total += c
        t = meta.get("total_tokens")
        if t:
            try:
                bucket["tokens"] += int(t)
            except (TypeError, ValueError):
                pass

    by_agent_out = [
        {
            "agent": b["agent"],
            "count": b["count"],
            "avg_duration_s": round(statistics.mean(b["durations"]), 1) if b["durations"] else None,
            "total_cost": round(b["cost"], 4),
            "tokens": b["tokens"],
        }
        for b in sorted(by_agent.values(), key=lambda x: -x["count"])
    ]

    by_type: Dict[str, int] = {}
    for m in mesh:
        t = str(m.get("type") or "job")
        by_type[t] = by_type.get(t, 0) + 1

    dur_sorted = sorted(durations)
    def _fraction(pct: float) -> Optional[float]:
        if not dur_sorted:
            return None
        idx = max(0, min(len(dur_sorted) - 1, int(round(pct * (len(dur_sorted) - 1)))))
        return round(dur_sorted[idx], 1)

    run_total = len(runs)
    succeeded = run_status.get("ok", 0)
    return {
        "window_hours": hours,
        "runs": {
            "total": run_total,
            "running": run_status.get("running", 0),
            "succeeded": succeeded,
            "failed": run_status.get("failed", 0),
            "cancelled": run_status.get("cancelled", 0),
            "success_rate": round(succeeded / run_total, 3) if run_total else None,
            "cost": round(cost_total, 4),
        },
        "mesh": {
            "total": len(mesh),
            "active": sum(mesh_status.get(s, 0) for s in ("queued", "claimed", "running")),
            "succeeded": mesh_status.get("succeeded", 0),
            "failed": mesh_status.get("failed", 0),
            "cancelled": mesh_status.get("cancelled", 0),
        },
        "duration": {
            "avg_s": round(statistics.mean(durations), 1) if durations else None,
            "p50_s": _fraction(0.50),
            "p95_s": _fraction(0.95),
            "longest_s": round(max(durations), 1) if durations else None,
        },
        "by_agent": by_agent_out,
        "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
    }