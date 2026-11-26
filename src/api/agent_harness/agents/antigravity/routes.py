"""Antigravity palette HTTP — models, per-chat model / effort pins.

Same contract as ``/api/claude/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the Antigravity agent slice;
registered by ``web_chat_api`` as ``antigravity_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.agent_harness.agent_defaults import get_starred_effort, get_starred_model
from api.agent_harness.agents.antigravity.adapter import pin_antigravity_model
from api.agent_harness.agents.antigravity.model_catalog import (
    antigravity_efforts_for_model,
    antigravity_model_label,
    list_antigravity_catalog_models,
)
from api.agent_harness.agents.antigravity.session_store import (
    load_antigravity_effort,
    load_antigravity_model,
    save_antigravity_effort,
    save_antigravity_model,
)
from api.http_authz import authenticated_required, require_session_actor

antigravity_bp = Blueprint("antigravity_agent", __name__, url_prefix="/api/antigravity")

_RESET_WORDS = ("default", "reset", "clear", "none")


def _session_arg() -> str:
    return (request.args.get("session") or "").strip()


def _body_session(data: dict) -> str:
    return str(data.get("session") or data.get("session_id") or "").strip()


@antigravity_bp.route("/models", methods=["GET"])
@authenticated_required
def api_antigravity_models():
    """Antigravity model families for the chat slash palette, plus this chat's pick."""
    try:
        session = _session_arg()
        refresh = (request.args.get("refresh") or "").strip().lower() in ("1", "true", "yes", "refresh")
        catalog = list_antigravity_catalog_models(refresh=refresh)
        sess_model = load_antigravity_model(session) if session else None
        star_model = get_starred_model("antigravity")
        preferred = sess_model or star_model or ""
        pref_l = preferred.lower()
        models = [
            {**m, "current": str(m.get("id") or "").lower() == pref_l}
            for m in (catalog.get("models") or []) if isinstance(m, dict)
        ]
        if preferred and not any(m.get("current") for m in models):
            models.insert(0, {
                "id": preferred,
                "label": antigravity_model_label(preferred),
                "description": "Custom model id pinned for this chat",
                "efforts": antigravity_efforts_for_model(preferred),
                "current": True,
            })
        return jsonify({
            "success": True,
            "models": models,
            "preferredModel": preferred,
            "preferredSource": "session" if sess_model else ("starred" if star_model else "cli_default"),
            "starredModel": star_model,
            "sessionModel": sess_model,
            "defaultModel": "",
            "source": catalog.get("source"),
            "count": catalog.get("count"),
            "commonEfforts": antigravity_efforts_for_model(None),
            "fetchedAt": catalog.get("fetched_at"),
            "error": catalog.get("error"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "models": [], "preferredModel": ""}), 500


@antigravity_bp.route("/model", methods=["POST"])
def api_set_antigravity_model():
    """Body: { session, model } — pin the Antigravity model for one chat ('' resets)."""
    try:
        data = request.get_json(silent=True) or {}
        session = _body_session(data)
        if not session:
            return jsonify({"success": False, "error": "session is required"}), 400
        _user, _sid, err = require_session_actor(session)
        if err:
            return err
        model = str(data.get("model") or "").strip()
        if model.lower() in _RESET_WORDS:
            model = ""
        saved, dropped = pin_antigravity_model(session, model)
        saved = saved or get_starred_model("antigravity") or ""
        return jsonify({
            "success": True,
            "preferredModel": saved,
            "label": antigravity_model_label(saved) if saved else "",
            "droppedEffort": dropped,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@antigravity_bp.route("/effort", methods=["GET"])
@authenticated_required
def api_antigravity_effort():
    """``--effort`` levels for the current Antigravity model, plus this chat's pin."""
    try:
        session = _session_arg()
        model = (
            (load_antigravity_model(session) if session else None)
            or get_starred_model("antigravity")
            or (request.args.get("model") or "").strip()
        )
        sess_effort = (load_antigravity_effort(session) if session else None) or ""
        star_effort = get_starred_effort("antigravity") or ""
        preferred = sess_effort or star_effort
        levels = [{"id": e, "current": e == preferred} for e in antigravity_efforts_for_model(model or None)]
        return jsonify({
            "success": True,
            "levels": levels,
            "model": model or "",
            "preferredEffort": preferred,
            "preferredSource": "session" if sess_effort else ("starred" if star_effort else "none"),
            "starredEffort": star_effort or None,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "levels": []}), 500


@antigravity_bp.route("/effort", methods=["POST"])
def api_set_antigravity_effort():
    """Body: { session, effort } — pin ``--effort`` for one chat ('' resets)."""
    try:
        data = request.get_json(silent=True) or {}
        session = _body_session(data)
        if not session:
            return jsonify({"success": False, "error": "session is required"}), 400
        _user, _sid, err = require_session_actor(session)
        if err:
            return err
        effort = str(data.get("effort") or "").strip().lower()
        if effort in _RESET_WORDS:
            effort = ""
        model = (
            str(data.get("model") or "").strip()
            or load_antigravity_model(session)
            or get_starred_model("antigravity")
        )
        known = antigravity_efforts_for_model(model or None)
        if effort and effort not in known:
            detail = f"Supported: {', '.join(known)}." if known else "This model takes no effort level."
            return jsonify({
                "success": False,
                "error": f"Effort `{effort}` is not supported for `{model or 'the CLI default model'}`. {detail}",
                "model": model or "",
                "levels": known,
            }), 400
        saved = save_antigravity_effort(session, effort) or ""
        return jsonify({"success": True, "preferredEffort": saved, "model": model or "", "levels": known})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def apply_request_pins(session_id, entry: dict) -> None:
    """First-turn ``agent_pins.antigravity`` from the chat POST (model / effort)."""
    model = str(entry.get("model") or "").strip()
    effort = str(entry.get("effort") or "").strip().lower()
    if model:
        save_antigravity_model(session_id, model)
    if effort and effort not in _RESET_WORDS:
        save_antigravity_effort(session_id, effort)
