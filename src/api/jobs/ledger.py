"""Unified job ledger: every execution Cuttle handles as one read model.

Sources are not duplicated — the ledger is a thin normalization over the
owners' stores:
  - local agent runs:  api.agent_events.store.EventStore (durable runs table)
  - mesh jobs:         api.device_workers.store.DeviceWorkerStore (jobs table)
  - in-memory liveset: api.active_executions.get_executing_jobs

The Jobs surface (routes/page) reads only this; control goes through
api.jobs.service so owners keep their own mutations.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from api.jobs.catalog import MESH_ACTIVE_STATUSES, job_actions, normalize_status


def agent_events_store():
    from api.agent_events.store import EventStore, state_dir

    return EventStore(state_dir())


def mesh_store():
    from api.device_workers.store import get_store

    return get_store()


# ── source → unified job mappers ────────────────────────────────────────────


def _run_to_job(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata") or {}
    harness = meta.get("harness") or {}
    agent = harness.get("agent_id") or row.get("agent_id") or ""
    model = harness.get("model") or row.get("model") or ""
    started = row.get("started_at")
    finished = meta.get("finished_at") or row.get("finished_at")
    status = normalize_status(row.get("status"))
    now = time.time()
    duration = None
    if started and finished:
        duration = max(0.0, finished - started)
    elif started:
        duration = max(0.0, now - started)
    return {
        "kind": "run",
        "id": row.get("query_id") or "",
        "short_id": str(row.get("query_id") or "")[:8],
        "label": agent or "agent",
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "duration_s": duration,
        "origin": {
            "chat_session_id": row.get("chat_session_id") or "",
            "project_root": row.get("project_root") or "",
        },
        "target": model or "",
        "meta": {
            "agent_id": agent,
            "model": model,
            "tokens": meta.get("total_tokens"),
            "cost": meta.get("total_cost"),
            "run_time_s": meta.get("total_execution_time"),
            "error": meta.get("error_message"),
        },
        "links": {
            "chat": (
                f"/chat_page.html?session={row.get('chat_session_id')}"
                if row.get("chat_session_id")
                else None
            ),
            "query_log": f"/query_log.html?id={row.get('query_id')}",
        },
        "actions": job_actions("run", row.get("status") or ""),
    }


def _mesh_to_job(row: Dict[str, Any]) -> Dict[str, Any]:
    started = row.get("started_at") or row.get("created_at")
    finished = row.get("completed_at")
    status = normalize_status(row.get("status"))
    now = time.time()
    duration = None
    if started and finished:
        duration = max(0.0, finished - started)
    elif started and status in MESH_ACTIVE_STATUSES:
        duration = max(0.0, now - started)
    return {
        "kind": "mesh",
        "id": row.get("id") or "",
        "short_id": str(row.get("id") or "")[:8],
        "label": str(row.get("type") or "job"),
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "duration_s": duration,
        "origin": {
            "chat_session_id": "",
            "project_root": "",
            "submitted_by": row.get("submitted_by") or "",
            "worker": row.get("claimed_by") or row.get("target_worker_id") or "",
        },
        "target": row.get("claimed_by") or row.get("target_worker_id") or "",
        "meta": {
            "type": row.get("type"),
            "priority": row.get("priority"),
            "attempts": row.get("attempts"),
            "error": row.get("error") or "",
            "detail": _mesh_detail(row),
        },
        "links": {
            "mesh": f"/api/workers/jobs/{row.get('id')}" if row.get("id") else None,
        },
        "actions": job_actions("mesh", row.get("status") or ""),
    }


def _mesh_detail(row: Dict[str, Any]) -> str:
    params = row.get("params") or {}
    jtype = str(row.get("type") or "")
    if jtype == "blender_render":
        fs, fe = params.get("frame_start"), params.get("frame_end")
        parts = [f"frames {fs}–{fe}"] if fs is not None and fe is not None else []
        result = row.get("result") or {}
        if result.get("frames_written") is not None:
            parts.append(f"{result['frames_written']} files")
        if result.get("elapsed_seconds") is not None:
            parts.append(f"{result['elapsed_seconds']}s")
        return " · ".join(parts)
    if jtype == "file_copy":
        return str(params.get("dest") or params.get("source") or "")
    if jtype == "shell":
        return str(params.get("recipe") or "")
    return ""


_CHAT_OP_STATUS = {
    "pending": "queued", "dispatching": "running", "verifying": "queued", "delivered": "succeeded", "done": "succeeded",
    "failed": "failed", "cancelled": "cancelled", "unknown": "failed", "blocked": "failed",
}


def _chat_op_to_job(op: Dict[str, Any]) -> Dict[str, Any]:
    target = f"CH-{int(op['target']):06d}"
    actor = int(op["actor"])
    if op["kind"] == "supervise":
        # A live schedule is ongoing work, not a queued one-shot.
        status = {"pending": "running", "dispatching": "running", "paused": "paused"}.get(
            op["state"], _CHAT_OP_STATUS.get(op["state"], op["state"]))
        every = int((op.get("payload") or {}).get("every") or 0)
        label, detail = f"Supervise {target}", f"every {every // 60}m" if every else ""
        seen = (op.get("result") or {}).get("last_seen") or {}
        if seen.get("state"):
            detail += f" · last seen {seen['state']}"
    else:
        status = _CHAT_OP_STATUS.get(op["state"], op["state"])
        label = f"Send → {target}"
        detail = str((op.get("payload") or {}).get("text") or "")[:120]
    actions = ["cancel"] if op["state"] in ("pending", "paused", "verifying") or (
        op["kind"] == "supervise" and op["state"] == "dispatching") else []
    return {
        "kind": "chat_op",
        "id": op["id"],
        "short_id": op["id"][:8],
        "label": label,
        "status": status,
        "started_at": op.get("updated"),
        "finished_at": None,
        "duration_s": None,
        "origin": {"chat_session_id": str(actor), "project_root": ""},
        "target": target,
        "meta": {"op_kind": op["kind"], "detail": detail,
                 "error": str((op.get("result") or {}).get("error") or "")},
        "links": {"chat": f"/chat_page.html?session={actor}"},
        "actions": actions,
    }


def chat_op_jobs(limit: int = 200) -> List[Dict[str, Any]]:
    """Supervision schedules and queued chat sends (``chat_supervision`` flag)."""
    from api.experimental import is_enabled

    if not is_enabled("chat_supervision"):
        return []
    from api.chat_ops import store

    return [_chat_op_to_job(op) for op in store.recent(limit)]


# ── queries ─────────────────────────────────────────────────────────────────


def _passes(job: Dict[str, Any], status: Optional[str], active_only: bool) -> bool:
    if not job.get("id"):
        return False
    if active_only and not job["status"] in ("queued", "claimed", "starting", "running"):
        return False
    if status and normalize_status(status) != job["status"]:
        return False
    return True


def unified_jobs(
    *,
    limit: int = 100,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    """Recent jobs from every source, newest first."""
    jobs: List[Dict[str, Any]] = []
    if kind in (None, "run"):
        for row in agent_events_store().recent_runs(limit=max(limit, 200)):
            job = _run_to_job(row)
            if _passes(job, status, active_only):
                jobs.append(job)
    if kind in (None, "mesh"):
        for row in mesh_store().list_jobs(limit=max(limit, 200)):
            job = _mesh_to_job(row)
            if _passes(job, status, active_only):
                jobs.append(job)
    if kind in (None, "chat_op"):
        for job in chat_op_jobs(limit=max(limit, 200)):
            if _passes(job, status, active_only):
                jobs.append(job)
    jobs.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return jobs[: max(int(limit) if limit else 100, 1)]


def inflight() -> List[Dict[str, Any]]:
    """Unified current in-flight jobs: live local runs + active mesh jobs.

    The badge and the running-now section both read this. Live runs come from
    active_executions (authoritative for the process); durable 'running' rows
    are overlaid only for extra identity when the registry row is known.
    """
    from api.active_executions import get_executing_jobs

    jobs: List[Dict[str, Any]] = []
    live_runs = get_executing_jobs() or []
    by_qid: Dict[str, Dict[str, Any]] = {}
    for r in agent_events_store().recent_runs(status="running", limit=200):
        if r.get("query_id"):
            by_qid[r["query_id"]] = _run_to_job(r)
    for entry in live_runs:
        qid = entry.get("query_id") or ""
        if qid in by_qid:
            job = by_qid[qid]
            if not job.get("label") or job["label"] == "agent":
                job["label"] = entry.get("pipeline_name") or job["label"]
            jobs.append(job)
        else:
            jobs.append(
                {
                    "kind": "run",
                    "id": qid,
                    "short_id": qid[:8],
                    "label": entry.get("pipeline_name") or "agent",
                    "status": "running",
                    "started_at": entry.get("started_at"),
                    "finished_at": None,
                    "duration_s": None,
                    "origin": {"chat_session_id": "", "project_root": ""},
                    "target": "",
                    "meta": {},
                    "links": {"query_log": f"/query_log.html?id={qid}"},
                    "actions": job_actions("run", "running"),
                }
            )
    for row in mesh_store().list_jobs(limit=200):
        if (row.get("status") or "") in MESH_ACTIVE_STATUSES:
            jobs.append(_mesh_to_job(row))
    jobs.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return jobs


def leftovers() -> List[Dict[str, Any]]:
    """Processes finished agent turns left running (not in flight, not busy).

    Separate from inflight() so the shell running badge never counts them.
    """
    from api.chat_run_registry import list_leftovers

    out: List[Dict[str, Any]] = []
    for rec in list_leftovers():
        sid = rec.get("session_id") or ""
        handle = f"CH-{int(sid):06d}" if str(sid).isdigit() else str(sid)
        out.append(
            {
                "kind": "leftover",
                "id": str(rec["pid"]),
                "short_id": str(rec["pid"]),
                "label": rec.get("name") or str(rec["pid"]),
                "status": "leftover",
                "started_at": rec.get("create_time"),
                "detached_at": rec.get("detached_at"),
                "origin": {"chat_session_id": sid},
                "meta": {
                    "detail": (f"from {handle} · " if handle else "") + f"pid {rec['pid']}",
                    "cmdline": rec.get("cmdline") or "",
                },
                "links": {"chat": f"/chat_page.html?session={sid}" if sid else None},
                "actions": job_actions("leftover", "leftover"),
            }
        )
    return out


def get_job(kind: str, job_id: str) -> Optional[Dict[str, Any]]:
    """One unified job by source kind (for deep-line control lookups)."""
    if kind == "run":
        row = agent_events_store().run(job_id)
        return _run_to_job(row) if row else None
    if kind == "mesh":
        row = mesh_store().get_job(job_id)
        return _mesh_to_job(row) if row else None
    return None