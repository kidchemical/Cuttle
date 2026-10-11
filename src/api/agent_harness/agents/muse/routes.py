"""Muse Code palette HTTP — models, per-chat model pin, per-chat effort pin.

Same contract as ``/api/codex/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the Muse agent slice (not the
monolith); registered by ``web_chat_api`` as ``muse_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import require_session_actor as _require_session_actor

muse_bp = Blueprint("muse_agent", __name__, url_prefix="/api/muse")


@muse_bp.route("/models", methods=["GET"])
def api_muse_models():
    """Muse Code models for the chat slash palette, plus this chat's pick."""
    try:
        from scripts.utilities.muse_cli_tool import (
            list_muse_catalog_models,
            muse_model_label,
            resolve_muse_default_model,
        )
        from scripts.utilities.muse_cli_session_store import load_muse_model

        from api.agent_harness.agent_defaults import get_starred_model as _starred_m

        session = (request.args.get("session") or "").strip()
        refresh = (request.args.get("refresh") or "").strip().lower() in (
            "1", "true", "yes", "refresh",
        )
        catalog = list_muse_catalog_models(refresh=refresh)
        _sess = (load_muse_model(session) if session else None)
        _star = _starred_m("muse")
        _cli = resolve_muse_default_model()
        preferred = _sess or _star or _cli
        preferred_source = "session" if _sess else ("starred" if _star else "cli_default")
        pref_l = (preferred or "").lower()
        models = [
            {**m, "current": str(m.get("id") or "").lower() == pref_l}
            for m in (catalog.get("models") or [])
            if isinstance(m, dict)
        ]
        if preferred and not any(m.get("current") for m in models):
            models.append({
                "id": preferred,
                "label": muse_model_label(preferred),
                "description": "Custom model id pinned for this chat",
                "current": True,
            })
        return jsonify({
            "success": True,
            "models": models,
            "preferredModel": preferred,
            "preferredSource": preferred_source,
            "starredModel": _star,
            "sessionModel": _sess,
            "defaultModel": resolve_muse_default_model(),
            "source": catalog.get("source"),
            "count": catalog.get("count"),
            "error": catalog.get("error"),
            "fetchedAt": catalog.get("fetched_at"),
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "models": [],
            "preferredModel": "muse-spark-1.3",
        }), 500


@muse_bp.route("/model", methods=["POST"])
def api_set_muse_model():
    """Body: { session, model } — pin the Muse model for one chat ('' resets)."""
    try:
        from scripts.utilities.muse_cli_tool import muse_model_label, resolve_muse_default_model
        from scripts.utilities.muse_cli_session_store import save_muse_model

        data = request.get_json(silent=True) or {}
        session = str(data.get("session") or data.get("session_id") or "").strip()
        if not session:
            return jsonify({"success": False, "error": "session is required"}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        model = str(data.get("model") or "").strip()
        if model.lower() in ("default", "reset", "clear"):
            model = ""
        from api.agent_harness.agent_defaults import get_starred_model as _starred_m2

        saved = save_muse_model(session, model) or _starred_m2("muse") or resolve_muse_default_model()
        return jsonify({
            "success": True,
            "preferredModel": saved,
            "label": muse_model_label(saved),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@muse_bp.route("/effort", methods=["GET"])
def api_muse_effort():
    """Muse reasoning-effort levels for the chat slash palette, plus this chat's pin."""
    try:
        from scripts.utilities.muse_cli_tool import MUSE_REASONING_EFFORTS
        from scripts.utilities.muse_cli_session_store import load_muse_effort

        from api.agent_harness.agent_defaults import get_starred_effort as _starred_me

        session = (request.args.get("session") or "").strip()
        _sess_e = (load_muse_effort(session) if session else None) or ""
        _star_e = _starred_me("muse") or ""
        preferred = _sess_e or _star_e
        levels = [
            {"id": str(e), "current": str(e) == preferred}
            for e in MUSE_REASONING_EFFORTS
        ]
        return jsonify({
            "success": True,
            "levels": levels,
            "preferredEffort": preferred,
            "preferredSource": "session" if _sess_e else ("starred" if _star_e else "none"),
            "starredEffort": _star_e or None,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "levels": []}), 500


@muse_bp.route("/effort", methods=["POST"])
def api_set_muse_effort():
    """Body: { session, effort } — pin the Muse reasoning effort for one chat ('' resets)."""
    try:
        from scripts.utilities.muse_cli_tool import MUSE_REASONING_EFFORTS
        from scripts.utilities.muse_cli_session_store import save_muse_effort

        data = request.get_json(silent=True) or {}
        session = str(data.get("session") or data.get("session_id") or "").strip()
        if not session:
            return jsonify({"success": False, "error": "session is required"}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        effort = str(data.get("effort") or "").strip().lower()
        known = [str(e) for e in MUSE_REASONING_EFFORTS]
        if effort.lower() in ("default", "reset", "clear", "none"):
            # "none" was a list entry before the CLI declared it invalid;
            # treat it as "no pin" rather than rejecting it.
            effort = ""
            effort = ""
        if effort and effort not in known:
            return jsonify({
                "success": False,
                "error": f"Unknown effort `{effort}`. Pick one of: {', '.join(known)}.",
            }), 400
        saved = save_muse_effort(session, effort) or ""
        return jsonify({"success": True, "preferredEffort": saved})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
