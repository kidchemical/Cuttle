"""Flask blueprint: /api/achievements.

Transport only; logic lives in :mod:`api.achievements.unlocks` /
:mod:`api.achievements.store`. Every endpoint is gated on the
``achievements`` experimental flag and answers ``200 {success: false,
disabled: true}`` when off, so a client can silently no-op without treating
the feature as an error.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.achievements import catalog, store, unlocks
from api.experimental import is_enabled
from api.http_authz import authenticated_required, owner_required

achievements_bp = Blueprint("achievements", __name__, url_prefix="/api/achievements")

_DISABLED = {"success": False, "disabled": True, "error": "Achievements are disabled"}


def _on() -> bool:
    return is_enabled("achievements")


@achievements_bp.route("", methods=["GET"])
@achievements_bp.route("/", methods=["GET"])
@authenticated_required
def get_achievements():
    if not _on():
        return jsonify(_DISABLED)
    return jsonify({"success": True, **unlocks.status()})


@achievements_bp.route("/pending", methods=["GET"])
@authenticated_required
def get_pending_achievements():
    """Unlocked-but-unseen unlocks. The client polls this for celebrations."""
    if not _on():
        return jsonify(_DISABLED)
    pending = store.pending_unlocks(catalog.catalog_payload())
    return jsonify({"success": True, "pending": pending, "count": len(pending)})


@achievements_bp.route("/<achievement_id>/ack", methods=["POST"])
@authenticated_required
def ack_achievement(achievement_id: str):
    if not _on():
        return jsonify(_DISABLED)
    if catalog.get(achievement_id) is None:
        return jsonify({"success": False, "error": "unknown achievement"}), 400
    changed = store.mark_seen(achievement_id)
    return jsonify({"success": True, "id": achievement_id, "changed": changed})


@achievements_bp.route("/scan", methods=["POST"])
@owner_required
def scan_achievements():
    """Full re-evaluation (used by the CLI and the "Rescan" button)."""
    if not _on():
        return jsonify(_DISABLED)
    result = unlocks.evaluate(force=True)
    return jsonify({"success": True, **result})


@achievements_bp.route("/<achievement_id>/grant", methods=["POST"])
@owner_required
def grant_achievement(achievement_id: str):
    if not _on():
        return jsonify(_DISABLED)
    ach = catalog.get(achievement_id)
    if ach is None:
        return jsonify({"success": False, "error": "unknown achievement"}), 400
    body = request.get_json(silent=True) or {}
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {"granted": True}
    changed = store.grant(ach.id, detail)
    return jsonify({"success": True, "id": ach.id, "unlocked": changed})


@achievements_bp.route("/reset", methods=["POST"])
@owner_required
def reset_achievements():
    if not _on():
        return jsonify(_DISABLED)
    cleared = store.reset()
    return jsonify({"success": True, "cleared": cleared})