"""Worktree HTTP surface — transport only (owner: api.worktrees).

Flag-gated (``worktrees`` experimental flag) and owner-gated for every write.
Decisions live in ``api.worktrees.{binding,service,roots,hooks,cleanup}``; this
module only parses requests, authorizes, and shapes JSON.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from api.http_authz import authenticated_required, owner_required
from api.worktrees import is_worktrees_enabled, registry, service
from api.worktrees import cleanup as cleanup_mod
from api.worktrees import hooks as hooks_mod

worktrees_bp = Blueprint("worktrees", __name__, url_prefix="/api/worktrees")


def _flag_off():
    if is_worktrees_enabled():
        return None
    return jsonify({"success": False, "error": "Worktrees are disabled."}), 404


def _settings_block() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager
        value = get_settings_manager().get_setting("worktrees", {})
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _project_primary(project_id) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Resolve ``(project_row, primary_path, error)`` for a project id."""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return None, None, "project_id is required"
    try:
        from managers.project_manager import project_manager
        from managers.project_locations import require_project_path
    except Exception as exc:  # pragma: no cover - import guard
        return None, None, f"project registry unavailable: {exc}"
    proj = project_manager.get_project(pid) if project_manager is not None else None
    if not proj:
        return None, None, "Unknown project"
    try:
        return proj, require_project_path(proj), None
    except Exception as exc:
        return proj, None, str(exc)


def _checkout_json(checkout) -> Dict[str, Any]:
    return {
        "checkout_uid": checkout.checkout_uid,
        "project_id": checkout.project_id,
        "kind": checkout.kind,
        "path": checkout.path,
        "branch": checkout.branch,
        "base_branch": checkout.base_branch,
        "state": checkout.state,
        "git_worktree_name": checkout.git_worktree_name,
        "created_at": checkout.created_at,
        "last_used": checkout.last_used,
    }


@worktrees_bp.route("", methods=["GET"])
@authenticated_required
def list_worktrees():
    off = _flag_off()
    if off:
        return off
    pid_raw = request.args.get("project_id")
    pid = int(pid_raw) if pid_raw and pid_raw.isdigit() else None
    items = [_checkout_json(c) for c in service.list_worktrees(pid)]
    return jsonify({"success": True, "worktrees": items})


@worktrees_bp.route("", methods=["POST"])
@owner_required
def create_worktree():
    off = _flag_off()
    if off:
        return off
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    proj, primary, err = _project_primary(project_id)
    if err:
        return jsonify({"success": False, "error": err}), 400
    settings = _settings_block()
    base_branch = str(body.get("base_branch") or proj.get("default_branch") or "").strip()
    branch = str(body.get("branch") or "").strip()
    if not branch:
        import secrets
        prefix = str(settings.get("branch_prefix") or "cuttle").strip("/") or "cuttle"
        branch = f"{prefix}/{secrets.token_hex(3)}"
    try:
        checkout = service.create_worktree(
            project_id=int(project_id),
            project_name=str(proj.get("name") or ""),
            primary_path=primary,
            base_branch=base_branch,
            branch=branch,
            settings=settings,
        )
    except service.WorktreeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409

    payload: Dict[str, Any] = {"success": True, "worktree": _checkout_json(checkout)}
    if body.get("run_setup"):
        # Running project code is a deliberate, explicitly-authorized action.
        if str(body.get("authorize_setup") or "").lower() in ("1", "true", "yes") or body.get("authorize_setup") is True:
            payload["setup"] = hooks_mod.run_setup_hook(primary, checkout.path)
        else:
            payload["setup"] = {"ran": False, "reason": "authorization_required"}
    return jsonify(payload), 201


@worktrees_bp.route("/<checkout_uid>", methods=["DELETE"])
@owner_required
def remove_worktree(checkout_uid: str):
    off = _flag_off()
    if off:
        return off
    checkout = registry.get_checkout(checkout_uid)
    if checkout is None:
        return jsonify({"success": False, "error": "Unknown checkout"}), 404
    _proj, primary, err = _project_primary(checkout.project_id)
    if err:
        return jsonify({"success": False, "error": err}), 400
    body = request.get_json(silent=True) or {}
    force_raw = body.get("force", request.args.get("force"))
    force = str(force_raw).lower() in ("1", "true", "yes")
    try:
        result = service.remove_worktree(checkout_uid, primary_path=primary, force=force)
    except service.WorktreeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, **result})


@worktrees_bp.route("/reconcile", methods=["POST"])
@owner_required
def reconcile_worktrees():
    off = _flag_off()
    if off:
        return off
    body = request.get_json(silent=True) or {}
    _proj, primary, _err = _project_primary(body.get("project_id"))
    return jsonify({"success": True, **cleanup_mod.reconcile(primary_path=primary)})


@worktrees_bp.route("/orphans", methods=["GET"])
@authenticated_required
def list_orphans():
    off = _flag_off()
    if off:
        return off
    _proj, primary, err = _project_primary(request.args.get("project_id"))
    if err:
        return jsonify({"success": False, "error": err}), 400
    orphans = cleanup_mod.list_orphans(primary_path=primary, settings=_settings_block())
    return jsonify({"success": True, "orphans": orphans})
