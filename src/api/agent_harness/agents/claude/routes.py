"""Claude Code palette HTTP — models, per-chat model pin, per-chat effort pin.

Same contract as ``/api/codex/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the Claude agent slice (not the
monolith); registered by ``web_chat_api`` as ``claude_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.agent_harness.agent_defaults import get_starred_effort, get_starred_model
from api.agent_harness.agents.claude.model_catalog import (
    claude_efforts_for_model,
    claude_model_label,
    list_claude_palette_models,
)
from api.http_authz import authenticated_required, require_session_actor
from scripts.utilities.claude_cli_session_store import (
    load_claude_effort,
    load_claude_model,
    save_claude_effort,
    save_claude_model,
)

claude_bp = Blueprint("claude_agent", __name__, url_prefix="/api/claude")

_RESET_WORDS = ("default", "reset", "clear", "none")


def _session_arg() -> str:
    return (request.args.get("session") or "").strip()


def _body_session(data: dict) -> str:
    return str(data.get("session") or data.get("session_id") or "").strip()


@claude_bp.route("/models", methods=["GET"])
@authenticated_required
def api_claude_models():
    """Claude Code models for the chat slash palette, plus this chat's pick."""
    try:
        session = _session_arg()
        refresh = (request.args.get("refresh") or "").strip().lower() in (
            "1", "true", "yes", "refresh",
        )
        q = (request.args.get("q") or "").strip() or None
        try:
            limit_raw = request.args.get("limit")
            limit = int(limit_raw) if limit_raw not in (None, "") else None
        except (TypeError, ValueError):
            limit = None

        catalog = list_claude_palette_models(refresh=refresh, q=q, limit=limit)
        sess_model = load_claude_model(session) if session else None
        star_model = get_starred_model("claude")
        preferred = sess_model or star_model or ""
        preferred_source = "session" if sess_model else ("starred" if star_model else "cli_default")
        pref_l = preferred.lower()
        models = [
            {**m, "current": str(m.get("id") or "").lower() == pref_l}
            for m in (catalog.get("models") or [])
            if isinstance(m, dict)
        ]
        if preferred and not any(m.get("current") for m in models):
            models.insert(0, {
                "id": preferred,
                "label": claude_model_label(preferred),
                "description": "Custom model id pinned for this chat",
                "current": True,
            })
        return jsonify({
            "success": True,
            "models": models,
            "preferredModel": preferred,
            "preferredSource": preferred_source,
            "starredModel": star_model,
            "sessionModel": sess_model,
            "defaultModel": "",
            "source": catalog.get("source"),
            "count": catalog.get("count"),
            "returned": catalog.get("returned"),
            "commonEfforts": list(catalog.get("default_efforts") or []),
            "fetchedAt": catalog.get("fetched_at"),
            "error": catalog.get("error"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "models": [], "preferredModel": ""}), 500


@claude_bp.route("/model", methods=["POST"])
def api_set_claude_model():
    """Body: { session, model } — pin the Claude model for one chat ('' resets)."""
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
        saved = save_claude_model(session, model) or get_starred_model("claude") or ""
        return jsonify({
            "success": True,
            "preferredModel": saved,
            "label": claude_model_label(saved) if saved else "",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@claude_bp.route("/effort", methods=["GET"])
@authenticated_required
def api_claude_effort():
    """``--effort`` levels for the current Claude model, plus this chat's pin."""
    try:
        session = _session_arg()
        model = (
            (load_claude_model(session) if session else None)
            or get_starred_model("claude")
            or (request.args.get("model") or "").strip()
        )
        sess_effort = (load_claude_effort(session) if session else None) or ""
        star_effort = get_starred_effort("claude") or ""
        preferred = sess_effort or star_effort
        levels = [
            {"id": e, "current": e == preferred}
            for e in claude_efforts_for_model(model or None)
        ]
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


@claude_bp.route("/effort", methods=["POST"])
def api_set_claude_effort():
    """Body: { session, effort } — pin Claude ``--effort`` for one chat ('' resets)."""
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
            or load_claude_model(session)
            or get_starred_model("claude")
        )
        known = claude_efforts_for_model(model or None)
        if effort and effort not in known:
            detail = (
                f"Supported: {', '.join(known)}."
                if known
                else "This model takes no effort level."
            )
            return jsonify({
                "success": False,
                "error": (
                    f"Effort `{effort}` is not supported for "
                    f"`{model or 'the CLI default model'}`. {detail}"
                ),
                "model": model or "",
                "levels": known,
            }), 400
        saved = save_claude_effort(session, effort) or ""
        return jsonify({
            "success": True,
            "preferredEffort": saved,
            "model": model or "",
            "levels": known,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
