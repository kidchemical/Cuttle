"""Flask blueprint: /api/workers — coordinator surface for the device mesh."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.device_workers.auth import authorize_enroll_request, authorize_worker_request
from api.device_workers.config import (
    device_workers_enabled,
    heartbeat_stale_seconds,
    lease_seconds,
)
from api.device_workers.executor import validate_job_submission
from api.device_workers.store import get_store
from api.http_authz import require_ui_operator

workers_bp = Blueprint("device_workers", __name__, url_prefix="/api/workers")


def _auth_or_401():
    ok, err = authorize_worker_request(request)
    if not ok:
        return jsonify({"success": False, "error": err or "unauthorized"}), 401
    return None


def _ui_operator_or_401():
    _user, err = require_ui_operator()
    return err


def _ui_or_worker_or_401():
    """Job status: Host UI operator, or a valid worker token."""
    denied = _ui_operator_or_401()
    if denied is None:
        return None
    return _auth_or_401()


@workers_bp.route("/enroll", methods=["POST"])
def enroll_worker():
    """
    Auto-issue a per-device worker token.

    Electron Client calls this after a successful host connect — same trust as
    opening the UI over LAN. No manual .env token.
    """
    if not device_workers_enabled():
        return jsonify({"success": False, "error": "device workers disabled"}), 503
    ok, err = authorize_enroll_request(request)
    if not ok:
        return jsonify({"success": False, "error": err or "enroll denied"}), 403

    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    hostname = str(data.get("hostname") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    rotate = bool(data.get("rotate"))
    try:
        enrolled = get_store().enroll_device(
            worker_id=wid,
            hostname=hostname,
            remote_addr=(request.remote_addr or ""),
            rotate=rotate,
        )
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({
        "success": True,
        "worker_id": enrolled["worker_id"],
        "token": enrolled["token"],
        "hostname": enrolled.get("hostname") or hostname,
        "rotated": enrolled.get("rotated"),
    })


@workers_bp.route("", methods=["GET"])
@workers_bp.route("/", methods=["GET"])
def list_workers():
    """List registered device workers (UI / agents). Owner session or loopback."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers import platform as plat

    result = plat.list_workers()
    result["stale_seconds"] = heartbeat_stale_seconds()
    return jsonify(result)


@workers_bp.route("/self-update", methods=["POST"])
def self_update_worker():
    """Enqueue cuttle_self_update for a Client worker (owner / loopback)."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers import platform as plat

    data = request.get_json(silent=True) or {}
    target = str(data.get("target") or data.get("target_worker_id") or data.get("worker_id") or "").strip()
    if not target:
        return jsonify({"success": False, "error": "target required"}), 400
    result = plat.submit_self_update(
        target_worker_id=target,
        repo=str(data.get("repo") or "").strip(),
        restart_electron=not bool(data.get("no_electron")),
        restart_daemon=not bool(data.get("no_daemon")),
        submitted_by=str(data.get("submitted_by") or "jobs-ui").strip() or "jobs-ui",
    )
    code = 200 if result.get("success") else 400
    return jsonify(result), code


@workers_bp.route("/<worker_id>", methods=["DELETE"])
def remove_worker_route(worker_id: str):
    """Drop a worker from the registry (owner / loopback). Does not auto-expire."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers import platform as plat

    data = request.get_json(silent=True) or {}
    revoke = data.get("revoke_enroll")
    if revoke is None:
        revoke = True
    result = plat.remove_worker(
        worker_id,
        revoke_enroll=bool(revoke),
    )
    if not result.get("success"):
        err = str(result.get("error") or "")
        code = 404 if "not found" in err.lower() else 400
        return jsonify(result), code
    return jsonify(result)


@workers_bp.route("/register", methods=["POST"])
def register_worker():
    denied = _auth_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    try:
        worker = get_store().upsert_worker(
            worker_id=wid,
            hostname=str(data.get("hostname") or ""),
            os_name=str(data.get("os") or ""),
            capabilities=data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {},
            storage=data.get("storage") if isinstance(data.get("storage"), dict) else {},
            load=data.get("load") if isinstance(data.get("load"), dict) else {},
            interactive_priority=str(data.get("interactive_priority") or "low"),
            ac_power=data.get("ac_power") if isinstance(data.get("ac_power"), bool) else None,
            meta=data.get("meta") if isinstance(data.get("meta"), dict) else {},
        )
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "worker": worker})


@workers_bp.route("/heartbeat", methods=["POST"])
def worker_heartbeat():
    # Same payload as register for W1 simplicity
    return register_worker()


@workers_bp.route("/jobs", methods=["GET"])
def list_jobs():
    denied = _ui_operator_or_401()
    if denied:
        return denied
    status = (request.args.get("status") or "").strip() or None
    try:
        limit = max(1, min(int(request.args.get("limit") or 50), 200))
    except ValueError:
        limit = 50
    get_store().reclaim_expired()
    jobs = get_store().list_jobs(status=status, limit=limit)
    return jsonify({"success": True, "jobs": jobs})


@workers_bp.route("/jobs", methods=["POST"])
def submit_job():
    """Submit a mesh job. Owner session or loopback — not a worker token."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers.executor import _normalize_job_type

    data = request.get_json(silent=True) or {}
    jtype = _normalize_job_type(str(data.get("type") or "").strip())
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    ok, err = validate_job_submission(jtype, params)
    if not ok:
        return jsonify({"success": False, "error": err}), 400
    if not device_workers_enabled():
        return jsonify({"success": False, "error": "device workers disabled"}), 503
    try:
        priority = int(data.get("priority") or 50)
    except (TypeError, ValueError):
        priority = 50
    ttl_seconds = data.get("ttl_seconds")
    expires_at = data.get("expires_at")
    max_attempts = data.get("max_attempts")
    try:
        ttl_seconds = float(ttl_seconds) if ttl_seconds is not None else None
    except (TypeError, ValueError):
        ttl_seconds = None
    try:
        expires_at = float(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        expires_at = None
    try:
        max_attempts = int(max_attempts) if max_attempts is not None else None
    except (TypeError, ValueError):
        max_attempts = None
    job = get_store().submit_job(
        job_type=jtype,
        params=params,
        requirements=data.get("requirements") if isinstance(data.get("requirements"), dict) else {},
        target_worker_id=str(data.get("target_worker_id") or "").strip() or None,
        submitted_by=str(data.get("submitted_by") or data.get("sender") or "").strip() or None,
        priority=priority,
        ttl_seconds=ttl_seconds,
        expires_at=expires_at,
        max_attempts=max_attempts,
    )
    return jsonify({"success": True, "job": job})


@workers_bp.route("/jobs/claim", methods=["POST"])
def claim_jobs():
    denied = _auth_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    try:
        limit = max(1, min(int(data.get("limit") or 1), 5))
    except (TypeError, ValueError):
        limit = 1
    caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    jobs = get_store().claim_jobs(
        worker_id=wid,
        capabilities=caps,
        limit=limit,
        lease_seconds=lease_seconds(),
    )
    return jsonify({"success": True, "jobs": jobs})


@workers_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    denied = _ui_or_worker_or_401()
    if denied:
        return denied
    job = get_store().get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "not found"}), 404
    return jsonify({"success": True, "job": job})


@workers_bp.route("/jobs/<job_id>/heartbeat", methods=["POST"])
def job_heartbeat(job_id: str):
    denied = _auth_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    progress = data.get("progress") if isinstance(data.get("progress"), dict) else None
    ok = get_store().heartbeat_job(
        job_id, wid, lease_seconds=lease_seconds(), progress=progress
    )
    if not ok:
        return jsonify({"success": False, "error": "job not claimed by worker"}), 409
    return jsonify({"success": True})


@workers_bp.route("/jobs/<job_id>/complete", methods=["POST"])
def complete_job(job_id: str):
    denied = _auth_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    ok = get_store().complete_job(job_id, wid, result=result)
    if not ok:
        return jsonify({"success": False, "error": "job not claimed by worker"}), 409
    return jsonify({"success": True})


@workers_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    """Cancel a queued/claimed/running mesh job (owner / loopback)."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "cancelled").strip() or "cancelled"
    job = get_store().cancel_job(job_id, reason=reason)
    if not job:
        return jsonify({"success": False, "error": "not found or not cancellable"}), 404
    return jsonify({"success": True, "job": job})


@workers_bp.route("/plan", methods=["POST"])
def plan_mesh():
    """Classify a message for mesh-worthiness (no enqueue)."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers.intent import plan_from_message
    from api.device_workers.platform import list_workers

    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or data.get("text") or "").strip()
    listing = list_workers(online_only=False)
    plan = plan_from_message(
        text,
        workers=listing.get("workers") or [],
        self_worker_id=str(listing.get("self_worker_id") or ""),
    )
    return jsonify(plan)


@workers_bp.route("/<worker_id>/probe", methods=["POST"])
def probe_worker_route(worker_id: str):
    """Targeted ping + measure RTT; stores last_rtt_ms on the worker."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers.platform import probe_worker

    data = request.get_json(silent=True) or {}
    try:
        timeout = float(data.get("timeout_seconds") or data.get("timeout") or 20)
    except (TypeError, ValueError):
        timeout = 20.0
    result = probe_worker(worker_id, timeout_seconds=timeout)
    code = 200 if result.get("success") else 504 if "timeout" in str(result.get("error") or "").lower() else 400
    return jsonify(result), code


@workers_bp.route("/ssh-approval/pending", methods=["GET"])
def ssh_approval_pending():
    """UI poll: pending human approvals for execute_shell_ssh."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers import ssh_approval as sa

    sa.expire_stale()
    return jsonify({"success": True, "pending": sa.list_pending()})


@workers_bp.route("/ssh-approval/request", methods=["POST"])
def ssh_approval_request():
    """Worker asks the UI to approve first SSH in this worker process."""
    denied = _auth_or_401()
    if denied:
        return denied
    from api.device_workers import ssh_approval as sa

    data = request.get_json(silent=True) or {}
    row = sa.create_request(
        worker_id=str(data.get("worker_id") or "").strip(),
        target=str(data.get("target") or "").strip(),
        command_preview=str(data.get("command_preview") or data.get("command") or "").strip(),
        job_id=str(data.get("job_id") or "").strip(),
        job_kind=str(data.get("job_kind") or data.get("kind") or "execute_shell_ssh").strip(),
    )
    return jsonify({"success": True, "request": row})


@workers_bp.route("/ssh-approval/<request_id>", methods=["GET"])
def ssh_approval_get(request_id: str):
    denied = _ui_or_worker_or_401()
    if denied:
        return denied
    from api.device_workers import ssh_approval as sa

    sa.expire_stale()
    row = sa.get_request(request_id)
    if not row:
        return jsonify({"success": False, "error": "not found"}), 404
    return jsonify({"success": True, "request": row})


@workers_bp.route("/ssh-approval/<request_id>/decide", methods=["POST"])
def ssh_approval_decide(request_id: str):
    """UI decision: once | session | deny."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers import ssh_approval as sa

    data = request.get_json(silent=True) or {}
    decision = str(data.get("decision") or data.get("choice") or "").strip()
    row = sa.decide(request_id, decision)
    if not row:
        return jsonify({"success": False, "error": "not found or bad decision"}), 400
    return jsonify({"success": True, "request": row})


@workers_bp.route("/jobs/blender-shard", methods=["POST"])
def blender_shard():
    """Enqueue blender_render shards across online blender workers."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers.platform import submit_blender_shards

    data = request.get_json(silent=True) or {}
    try:
        fs = int(data.get("frame_start") or 1)
        fe = int(data.get("frame_end") or fs)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "frame_start/frame_end must be ints"}), 400
    result = submit_blender_shards(
        blend_file=str(data.get("blend_file") or "").strip(),
        output_dir=str(data.get("output_dir") or "").strip(),
        frame_start=fs,
        frame_end=fe,
        engine=str(data.get("engine") or "").strip(),
        blender_bin=str(data.get("blender_bin") or "").strip(),
        submitted_by=str(data.get("submitted_by") or "api").strip() or "api",
        prefer_remote=bool(data.get("prefer_remote", True)),
        max_shards=int(data.get("max_shards") or 8),
        dry_run=bool(data.get("dry_run")),
        distribution=str(data.get("distribution") or "work_steal").strip() or "work_steal",
        chunk_size=int(data.get("chunk_size") or 0),
        batch_id=str(data.get("batch_id") or "").strip(),
    )
    code = 200 if result.get("success") else 400
    return jsonify(result), code


@workers_bp.route("/jobs/batch/<batch_id>", methods=["GET"])
def blender_batch_status(batch_id: str):
    """Summarize a blender work-steal / shard batch by batch_id."""
    denied = _ui_operator_or_401()
    if denied:
        return denied
    from api.device_workers.platform import batch_status

    result = batch_status(batch_id)
    code = 200 if result.get("success") else 400
    return jsonify(result), code


@workers_bp.route("/jobs/<job_id>/fail", methods=["POST"])
def fail_job(job_id: str):
    denied = _auth_or_401()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    wid = str(data.get("worker_id") or "").strip()
    if not wid:
        return jsonify({"success": False, "error": "worker_id required"}), 400
    params_update = (
        data.get("params_update")
        if isinstance(data.get("params_update"), dict)
        else None
    )
    partial_result = (
        data.get("partial_result")
        if isinstance(data.get("partial_result"), dict)
        else None
    )
    ok = get_store().fail_job(
        job_id,
        wid,
        error=str(data.get("error") or "failed"),
        retry=bool(data.get("retry")),
        params_update=params_update,
        partial_result=partial_result,
    )
    if not ok:
        return jsonify({"success": False, "error": "job not claimed by worker"}), 409
    return jsonify({"success": True})
