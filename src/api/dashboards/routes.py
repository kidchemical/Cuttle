"""Flask blueprint: /api/dashboards."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.dashboards import catalog, service, usage
from api.http_authz import owner_required

dashboards_bp = Blueprint("dashboards", __name__, url_prefix="/api/dashboards")


@dashboards_bp.route("", methods=["GET"])
@dashboards_bp.route("/", methods=["GET"])
@owner_required
def list_dashboards():
    return jsonify(service.hub())


@dashboards_bp.route("/<dash_id>", methods=["GET"])
@owner_required
def get_dashboard(dash_id: str):
    meta = catalog.get_dashboard(dash_id)
    if not meta:
        return jsonify({"success": False, "error": "unknown dashboard"}), 404
    force = str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")
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
    return jsonify({"success": True, **meta})
