"""Flask transport for the unified Jobs surface (/api/jobs).

Composition stays in web_chat_api; this file owns only HTTP. Read model in
api.jobs.ledger (+ live owner tree in api.jobs.work_tree), control + telemetry
in api.jobs.service, agent-owned process control in api.jobs.processes.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import owner_required
from api.jobs import ledger, processes, work_tree
from api.jobs.service import (
    JobNotCancellableError,
    JobNotFoundError,
    JobNotRerunnableError,
    cancel_job,
    job_stats,
    rerun_mesh_job,
)

jobs_bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


def _int_arg(name: str, default: int) -> int:
    try:
        return max(1, int(request.args.get(name) or default))
    except (TypeError, ValueError):
        return default


@jobs_bp.get("")
@owner_required
def list_jobs():
    """Unified recent jobs across local runs and the mesh."""
    from api.experimental import is_enabled

    if not is_enabled("jobs_surface"):
        return jsonify(error="Enable Jobs surface in Experimental settings"), 403
    jobs = ledger.unified_jobs(
        limit=_int_arg("limit", 100),
        kind=request.args.get("kind") or None,
        status=request.args.get("status") or None,
        active_only=request.args.get("active_only") == "1",
    )
    return jsonify(jobs=jobs)


@jobs_bp.get("/in-flight")
@owner_required
def inflight_route():
    """Live in-flight jobs for the shell badge / running-now (same shape as the
    retired /api/executing-jobs so the badge consumer stays compatible)."""
    jobs = ledger.inflight()
    return jsonify(
        executing_jobs=[{k: v for k, v in j.items() if k in ("kind", "id", "short_id", "label", "status", "started_at")} for j in jobs],
        # Not counted by the running badge: detached processes from finished turns.
        leftover_processes=ledger.leftovers(),
    )


@jobs_bp.get("/stats")
@owner_required
def stats_route():
    from api.experimental import is_enabled

    if not is_enabled("jobs_surface"):
        return jsonify(error="Enable Jobs surface in Experimental settings"), 403
    return jsonify(job_stats(hours=_int_arg("hours", 24)))


@jobs_bp.post("/<kind>/<job_id>/cancel")
@owner_required
def cancel_route(kind: str, job_id: str):
    kind = str(kind or "").strip().lower()
    try:
        return jsonify(cancel_job(kind, job_id))
    except JobNotFoundError as e:
        return jsonify(success=False, error=str(e)), 404
    except JobNotCancellableError as e:
        return jsonify(success=False, error=str(e)), 409


@jobs_bp.post("/mesh/<job_id>/rerun")
@owner_required
def rerun_route(job_id: str):
    try:
        return jsonify(rerun_mesh_job(job_id))
    except JobNotFoundError as e:
        return jsonify(success=False, error=str(e)), 404
    except JobNotRerunnableError as e:
        return jsonify(success=False, error=str(e)), 409

@jobs_bp.get("/work-tree")
@owner_required
def work_tree_route():
    """Every chat with live work: turns, waits, watches, schedules, owned processes."""
    return jsonify(work_tree.work_tree())


@jobs_bp.post("/unit-watch/<job_id>/start-now")
@owner_required
def start_unit_now_route(job_id: str):
    """Start a timer-scheduled detached job now (its watch keeps following it)."""
    from api.job_watch import start_scheduled_now

    try:
        result = start_scheduled_now(job_id)
    except LookupError as e:
        return jsonify(success=False, error=str(e)), 404
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 409
    work_tree.invalidate()
    return jsonify(success=True, **result)


@jobs_bp.post("/processes/<int:pid>/end")
@owner_required
def end_process_route(pid: int):
    """End one agent-owned process tree; ``create_time`` must match the listed process."""
    body = request.get_json(silent=True) or {}
    try:
        result = processes.end_process(pid, body.get("create_time"))
    except LookupError as e:
        return jsonify(success=False, error=str(e)), 404
    except PermissionError as e:
        return jsonify(success=False, error=str(e)), 403
    work_tree.invalidate()
    return jsonify(success=True, **result)


@jobs_bp.post("/chats/<chat>/end-processes")
@owner_required
def end_chat_processes_route(chat: str):
    """End every process a chat's agents left running (after its turn is stopped)."""
    try:
        result = processes.end_chat_processes(chat)
    except LookupError as e:
        return jsonify(success=False, error=str(e)), 404
    work_tree.invalidate()
    return jsonify(success=True, **result)
