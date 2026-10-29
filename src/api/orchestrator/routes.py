"""HTTP surface for the Orchestrator and Agents Apps (owner-only)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.http_authz import authenticated_required, current_user, owner_required
from api.orchestrator import service

orchestrator_bp = Blueprint("orchestrator", __name__, url_prefix="/api/orchestrator")
orchestrator_pages_bp = Blueprint("orchestrator_pages", __name__)
_WEB = Path(__file__).resolve().parents[2] / "web"


def _disabled():
    if service.enabled():
        return None
    return jsonify(success=False, disabled=True,
                   error="Enable Orchestrator in Settings → Experimental."), 403


def _uid() -> int:
    return int(current_user()["id"])


def _error(exc: Exception):
    message = str(exc)
    return jsonify(success=False, error=message), 404 if "not found" in message else 400


@orchestrator_bp.get("/playbooks")
@owner_required
def list_route():
    disabled = _disabled()
    if disabled:
        return disabled
    return jsonify(success=True, playbooks=service.list_playbooks(_uid()))


@orchestrator_bp.get("/playbooks/<playbook_id>")
@owner_required
def get_route(playbook_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        return jsonify(success=True, playbook=service.get_playbook(_uid(), playbook_id))
    except service.OrchestratorError as exc:
        return _error(exc)


@orchestrator_bp.put("/playbooks/<playbook_id>")
@owner_required
def save_route(playbook_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(success=False, error="Expected a JSON object."), 400
    body = {**body, "id": playbook_id}
    try:
        return jsonify(success=True, playbook=service.save_playbook(_uid(), body))
    except service.OrchestratorError as exc:
        return _error(exc)


@orchestrator_bp.delete("/playbooks/<playbook_id>")
@owner_required
def delete_route(playbook_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    if not service.delete_playbook(_uid(), playbook_id):
        return jsonify(success=False, error="Playbook not found."), 404
    return jsonify(success=True, deleted=playbook_id)


@orchestrator_bp.get("/playbooks/<playbook_id>/preview")
@owner_required
def preview_route(playbook_id: str):
    """Rendered brief + supervisor command, for a target chat when given."""
    disabled = _disabled()
    if disabled:
        return disabled
    target = (request.args.get("session") or "").strip()
    try:
        out = service.brief(target or None, playbook_id=playbook_id, user_id=_uid())
    except service.OrchestratorError as exc:
        return _error(exc)
    return jsonify(success=True, **out)


@orchestrator_bp.get("/bindings")
@owner_required
def bindings_route():
    disabled = _disabled()
    if disabled:
        return disabled
    return jsonify(success=True, bindings=service.list_bindings(_uid()))


@orchestrator_bp.delete("/bindings/<session>")
@owner_required
def unbind_route(session: str):
    disabled = _disabled()
    if disabled:
        return disabled
    try:
        return jsonify(success=True, unbound=service.unbind(session, user_id=_uid()))
    except service.OrchestratorError as exc:
        return _error(exc)


@orchestrator_pages_bp.get("/orchestrator_page.html")
@authenticated_required
def orchestrator_page():
    disabled = _disabled()
    if disabled:
        return disabled
    return send_from_directory(_WEB, "orchestrator_page.html")


@orchestrator_pages_bp.get("/agent_profiles_page.html")
@authenticated_required
def agent_profiles_page():
    disabled = _disabled()
    if disabled:
        return disabled
    return send_from_directory(_WEB, "agent_profiles_page.html")
