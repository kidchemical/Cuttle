"""Flask transport for the unified Jobs surface (/api/jobs).

Composition stays in web_chat_api; this file owns only HTTP. Read model in
api.jobs.ledger, control + telemetry in api.jobs.service.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import owner_required
from api.jobs import ledger
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
    return jsonify(executing_jobs=[{k: v for k, v in j.items() if k in ("kind", "id", "short_id", "label", "status", "started_at")} for j in jobs])


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