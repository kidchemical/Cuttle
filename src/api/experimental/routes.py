"""Flask blueprint: /api/experimental/flags.

Transport only. Resolution lives in :mod:`api.experimental.flags`.

Auth contract (same as the rest of the settings surface): reads are
``@authenticated_required``, writes are ``@owner_required`` — enforced
solely by the decorator.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import api.experimental.flags as flags
from api.http_authz import authenticated_required, owner_required

experimental_bp = Blueprint("experimental", __name__, url_prefix="/api/experimental")


def validate_flag_update(flag_id: str, data) -> tuple[bool, str, dict]:
    """``(ok, error, patch)`` for one flag write. Owns the input contract."""
    if flags.get_flag(flag_id) is None:
        return False, f"Unknown experimental flag: {flag_id}", {}
    if not isinstance(data, dict):
        return False, "Expected JSON object", {}
    if "enabled" not in data:
        return False, "enabled is required", {}
    raw = data.get("enabled")
    if isinstance(raw, bool):
        return True, "", {"enabled": raw}
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false", "1", "0"):
        return True, "", {"enabled": raw.strip().lower() in ("true", "1")}
    return False, "enabled must be a boolean", {}


@experimental_bp.route("/flags", methods=["GET"])
@authenticated_required
def get_experimental_flags():
    return jsonify({"success": True, **flags.flags_payload()})


@experimental_bp.route("/flags/<flag_id>", methods=["POST"])
@owner_required
def set_experimental_flag(flag_id: str):
    ok, err, patch = validate_flag_update(
        flag_id, request.get_json(silent=True) or {}
    )
    if not ok:
        return jsonify({"success": False, "error": err}), 400
    try:
        spec = flags.set_enabled(flag_id, patch["enabled"])
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify(
        {
            "success": True,
            "flag": spec,
            "kill_switch": flags.kill_switch_active(),
            "flags": flags.enabled_flags(),
        }
    )


@experimental_bp.route("/flags/reset", methods=["POST"])
@owner_required
def reset_experimental_flags():
    try:
        specs = flags.reset_all()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "flags": specs})
