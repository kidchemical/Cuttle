"""Flask blueprint: /api/dashboards."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import api.dashboards.catalog as catalog
import api.dashboards.context as context
import api.dashboards.service as service
import api.dashboards.usage as usage
from api.http_authz import authenticated_required, current_user, is_owner_user

dashboards_bp = Blueprint("dashboards", __name__, url_prefix="/api/dashboards")


@dashboards_bp.route("", methods=["GET"])
@dashboards_bp.route("/", methods=["GET"])
@authenticated_required
def list_dashboards():
    hub = service.hub()
    if not is_owner_user(current_user()):
        hub["dashboards"] = [d for d in hub["dashboards"] if d.get("public")]
    return jsonify(hub)


@dashboards_bp.route("/<dash_id>", methods=["GET"])
@authenticated_required
def get_dashboard(dash_id: str):
    meta = catalog.get_dashboard(dash_id)
    if not meta:
        return jsonify({"success": False, "error": "unknown dashboard"}), 404
    # Personal dashboards read local turns, costs and projects; only public ones
    # are open to other accounts, and only the owner can force a refetch.
    owner = is_owner_user(current_user())
    if not owner and not meta.get("public"):
        return jsonify({"success": False, "error": "Owner privileges required."}), 403
    force = owner and str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")
    if dash_id == "model-benchmarks":
        source_id = (request.args.get("source") or "deepswe").strip().lower()
        return jsonify(service.model_benchmarks(force=force, source_id=source_id))
    if dash_id == "cuttle-performance":
        label = str(request.args.get("label") or request.args.get("refresh") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        try:
            days = int(request.args.get("days") or 30)
        except ValueError:
            days = 30
        return jsonify(service.cuttle_performance(
            label=label,
            source_id=(request.args.get("source") or "all").strip().lower(),
            days=days,
        ))
    if dash_id == usage.USAGE_ID:
        try:
            tz_offset = int(request.args["tz"]) if request.args.get("tz") else None
        except ValueError:
            tz_offset = None
        return jsonify(usage.cuttle_usage(
            range_id=(request.args.get("range") or "30d").strip().lower(),
            start=request.args.get("start") or None,
            end=request.args.get("end") or None,
            group_by=(request.args.get("group") or "model").strip().lower(),
            interval=(request.args.get("interval") or "auto").strip().lower(),
            source_id=(request.args.get("source") or "all").strip().lower(),
            tz_offset_minutes=tz_offset,
        ))
    if dash_id == context.CONTEXT_ID:
        try:
            tz_offset = int(request.args["tz"]) if request.args.get("tz") else None
        except ValueError:
            tz_offset = None
        return jsonify(context.cuttle_context(
            range_id=(request.args.get("range") or "30d").strip().lower(),
            tz_offset_minutes=tz_offset,
        ))
    if dash_id == "cuttle-jobs":
        try:
            hours = int(request.args.get("hours") or 24)
        except ValueError:
            hours = 24
        return jsonify(service.cuttle_jobs(hours=hours))
    return jsonify({"success": True, **meta})
