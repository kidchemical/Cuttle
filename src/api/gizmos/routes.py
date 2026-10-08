"""Flask blueprints: ``/api/gizmos`` + the Gizmos App and pop-out pages.

Transport only; semantics live in :mod:`api.gizmos.service`. Every data
endpoint answers ``200 {success: false, disabled: true}`` while the ``gizmos``
flag is off. Owner-only: gizmos rearrange the owner's shell and show plan
usage, which ``/api/usage-live`` already restricts to the owner.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.gizmos import is_enabled, service, store
from api.http_authz import authenticated_required, owner_required
from api.gizmos.attribution import request_actor as _actor
from api.gizmos.tasks_routes import tasks_bp

gizmos_bp = Blueprint("gizmos", __name__, url_prefix="/api/gizmos")
gizmos_bp.register_blueprint(tasks_bp)

gizmos_pages_bp = Blueprint("gizmos_pages", __name__)

_DISABLED = {"success": False, "disabled": True, "error": "Gizmos are disabled"}
_STATUS = {"not_found": 404, "conflict": 409}


def _error(exc: service.GizmoError):
    return jsonify({"success": False, "error": str(exc), "code": exc.code}), _STATUS.get(exc.code, 400)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@gizmos_bp.route("", methods=["GET"])
@gizmos_bp.route("/", methods=["GET"])
@owner_required
def list_gizmos():
    if not is_enabled():
        return jsonify(_DISABLED)
    return _no_store(jsonify({"success": True, **service.list_payload(), "types": service.types()}))


@gizmos_bp.route("/types", methods=["GET"])
@owner_required
def list_types():
    if not is_enabled():
        return jsonify(_DISABLED)
    return jsonify({"success": True, "types": service.types()})


@gizmos_bp.route("", methods=["POST"])
@gizmos_bp.route("/", methods=["POST"])
@owner_required
def create_gizmo():
    if not is_enabled():
        return jsonify(_DISABLED)
    data = _body()
    try:
        gizmo = service.create(
            str(data.get("type") or ""),
            config=data.get("config") if isinstance(data.get("config"), dict) else None,
            placement=data.get("placement") if isinstance(data.get("placement"), dict) else None,
            title=data.get("title"),
            gizmo_id=data.get("id"),
            created_by="ui", actor=_actor(data),
        )
    except service.GizmoError as exc:
        return _error(exc)
    return jsonify({"success": True, "gizmo": gizmo}), 201


@gizmos_bp.route("/<gizmo_id>", methods=["GET"])
@owner_required
def get_gizmo(gizmo_id: str):
    if not is_enabled():
        return jsonify(_DISABLED)
    try:
        gizmo = service.get(gizmo_id)
        store.record_interaction(gizmo_id, "get", _actor())
        return _no_store(jsonify({"success": True, "gizmo": gizmo}))
    except service.GizmoError as exc:
        return _error(exc)


@gizmos_bp.route("/<gizmo_id>", methods=["PATCH"])
@owner_required
def update_gizmo(gizmo_id: str):
    if not is_enabled():
        return jsonify(_DISABLED)
    data = _body()
    for key in ("config", "placement"):
        if key in data and not isinstance(data[key], dict):
            return jsonify({"success": False, "error": f"{key} must be an object"}), 400
    try:
        gizmo = service.update(gizmo_id, title=data.get("title"),
                               config=data.get("config"), placement=data.get("placement"), actor=_actor(data))
    except service.GizmoError as exc:
        return _error(exc)
    return jsonify({"success": True, "gizmo": gizmo})


@gizmos_bp.route("/<gizmo_id>", methods=["DELETE"])
@owner_required
def delete_gizmo(gizmo_id: str):
    if not is_enabled():
        return jsonify(_DISABLED)
    try:
        service.remove(gizmo_id, actor=_actor())
    except service.GizmoError as exc:
        return _error(exc)
    return jsonify({"success": True, "id": gizmo_id})


@gizmos_bp.route("/<gizmo_id>/data", methods=["GET"])
@owner_required
def gizmo_data(gizmo_id: str):
    if not is_enabled():
        return jsonify(_DISABLED)
    refresh = request.args.get("refresh", "").strip().lower() in ("1", "true", "yes")
    try:
        data = service.resolve_data(gizmo_id, refresh=refresh)
        if refresh:
            store.record_interaction(gizmo_id, "refresh", _actor())
    except service.GizmoError as exc:
        return _error(exc)
    return _no_store(jsonify({"success": True, "id": gizmo_id, "data": data}))


@gizmos_bp.route("/<gizmo_id>/history", methods=["GET"])
@owner_required
def gizmo_history(gizmo_id):
    if not is_enabled():
        return jsonify(_DISABLED)
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        return jsonify(success=False, error="limit must be an integer"), 400
    return _no_store(jsonify(success=True, events=store.history(gizmo_id, limit)))


_WEB = Path(__file__).resolve().parents[2] / "web"


@gizmos_pages_bp.route("/gizmos_page.html")
@authenticated_required
def gizmos_page():
    """Feature App; the data endpoints enforce the flag and owner role."""
    return send_from_directory(_WEB, "gizmos_page.html")


@gizmos_pages_bp.route("/gizmo_popout.html")
@authenticated_required
def gizmo_popout_page():
    """Body of an Electron always-on-top pop-out window (one gizmo)."""
    return send_from_directory(_WEB, "gizmo_popout.html")
