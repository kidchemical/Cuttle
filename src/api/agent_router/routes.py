"""Owner-only Router editor previews and recorded activity. No task execution."""
from flask import Blueprint, jsonify, request

from api.http_authz import owner_required
from api.agent_router.engine import build_context, preview_decision
from api.agent_router.outcomes import recent_decisions

router_editor_bp = Blueprint("router_editor", __name__, url_prefix="/api/router")


@router_editor_bp.post("/preview")
@owner_required
def preview():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected a JSON object"), 400
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        return jsonify(success=False, error="Enter a prompt between 1 and 12,000 characters"), 400
    if "consult_brain" in data and not isinstance(data["consult_brain"], bool):
        return jsonify(success=False, error="consult_brain must be a boolean"), 400
    decision, meta = preview_decision(
        build_context(prompt.strip()), consult_brain=data.get("consult_brain", False)
    )
    return jsonify(success=True, decision=decision.to_dict(), target=decision.target.to_dict(),
                   meta=meta, executed=False)


@router_editor_bp.get("/decisions")
@owner_required
def decisions():
    return jsonify(success=True, decisions=recent_decisions())
