"""HTTP surface for agent profiles (the Agents App), behind flag ``orchestrator``.

Profiles stay owned by ``api.subagents.profiles``; this is owner-only transport.
Saves run the same harness/model/effort check as ``profiles save`` and spawn.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.experimental import is_enabled
from api.http_authz import current_user, owner_required
from api.subagents import profiles as profiles_mod

agent_profiles_bp = Blueprint("agent_profiles", __name__, url_prefix="/api/agent-profiles")


def _db():
    from api.auth_db import get_auth_db

    return get_auth_db()


def _disabled():
    if is_enabled("orchestrator"):
        return None
    return jsonify(success=False, disabled=True,
                   error="Enable Orchestrator in Settings → Experimental."), 403


def _uid() -> int:
    return int(current_user()["id"])


@agent_profiles_bp.get("")
@owner_required
def list_route():
    disabled = _disabled()
    if disabled:
        return disabled
    return jsonify(success=True, profiles=profiles_mod.list_profiles(_db(), _uid()))


@agent_profiles_bp.put("/<profile_id>")
@owner_required
def save_route(profile_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(success=False, error="Expected a JSON object."), 400
    try:
        row = profiles_mod.save_profile(
            _db(), _uid(),
            profile_id=profile_id,
            name=str(body.get("name") or ""),
            avatar=str(body.get("avatar") or ""),
            agent=str(body.get("agent") or "none"),
            model=str(body.get("model") or ""),
            effort=str(body.get("effort") or ""),
        )
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    return jsonify(success=True, profile=row)


@agent_profiles_bp.post("/check")
@owner_required
def check_route():
    """Dry-run the pin check without saving (live form feedback)."""
    disabled = _disabled()
    if disabled:
        return disabled
    body = request.get_json(silent=True) or {}
    agent = profiles_mod.normalize_harness(body.get("agent") or "none")
    model = str(body.get("model") or "").strip()
    effort = str(body.get("effort") or "").strip()
    if agent == "none":
        error = "a profile with no harness cannot pin a model or effort" if (model or effort) else None
    else:
        from api.agent_harness.agent_models import check_agent_pin

        error = check_agent_pin(agent, model, effort)
    return jsonify(success=True, ok=error is None, error=error)


@agent_profiles_bp.delete("/<profile_id>")
@owner_required
def delete_route(profile_id: str):
    disabled = _disabled()
    if disabled:
        return disabled
    if profiles_mod.slug_id(profile_id) in profiles_mod.BUILTIN_IDS:
        return jsonify(success=False, error="Built-in profiles cannot be deleted."), 400
    if not profiles_mod.delete_profile(_db(), _uid(), profile_id):
        return jsonify(success=False, error="Profile not found."), 404
    return jsonify(success=True, deleted=profiles_mod.slug_id(profile_id))
