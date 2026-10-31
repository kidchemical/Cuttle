"""Flask blueprint: /api/chat-ops — the chat supervision UI's transport.

Logic lives in :mod:`api.chat_ops.service`. Every endpoint is gated on the
``chat_supervision`` experimental flag and answers ``200 {success: false,
disabled: true}`` when off. Agent CLI verbs (``python -m api.chat_cli``) do
not go through here and stay available regardless.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.chat_ops import service
from api.experimental import is_enabled
from api.http_authz import authenticated_required, current_user

chat_ops_bp = Blueprint("chat_ops", __name__, url_prefix="/api/chat-ops")

_DISABLED = {"success": False, "disabled": True, "error": "Chat supervision is disabled"}


def _call(fn, *args, **kwargs):
    if not is_enabled("chat_supervision"):
        return jsonify(_DISABLED)
    try:
        return jsonify({"success": True, **fn(*args, **kwargs)})
    except LookupError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


def _uid() -> int:
    return int(current_user()["id"])


@chat_ops_bp.get("/sessions/<session>")
@authenticated_required
def session_overview(session: str):
    return _call(service.overview, session, _uid())


@chat_ops_bp.get("/sessions/<session>/defaults")
@authenticated_required
def supervise_defaults(session: str):
    """Prefill for the "Supervise this chat…" dialog (live branch, checkout)."""
    return _call(service.supervise_defaults, session, _uid())


@chat_ops_bp.post("/supervise")
@authenticated_required
def start_supervision():
    data = request.get_json(silent=True) or {}
    forbidden = data.get("forbidden_branches") or []
    if isinstance(forbidden, str):
        forbidden = [b.strip() for b in forbidden.split(",") if b.strip()]
    tiers = data.get("model_tiers") or {}

    def run():
        return service.start_supervisor(
            data.get("target"), user_id=_uid(), agent=str(data.get("agent") or ""),
            every=int(data.get("every") or 900), instructions=str(data.get("instructions") or ""),
            required_branch=str(data.get("required_branch") or ""),
            required_path=str(data.get("required_path") or ""),
            forbidden_branches=[str(b) for b in forbidden], model_tiers=tiers,
        )

    return _call(run)


@chat_ops_bp.post("/operations/<op_id>/<verb>")
@authenticated_required
def operation_control(op_id: str, verb: str):
    if verb == "cancel":
        return _call(lambda: {"operation": service.cancel(op_id, _uid())})
    if verb in ("pause", "resume"):
        return _call(lambda: {"operation": service.set_paused(op_id, _uid(), verb == "pause")})
    return jsonify({"success": False, "error": "Unknown action"}), 404
