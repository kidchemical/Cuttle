"""Per-chat auto-compact HTTP surface (the context gauge popover).

Nested under the agent-harness blueprint, next to ``/api/agent-context``.
Global settings live in ``api.settings_routes`` (``/api/settings/auto-compact``).
While the ``auto_compact`` flag is off every route answers
``{success: true, available: false}`` and writes nothing.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import numeric_chat_session_id, require_session_actor

auto_compact_bp = Blueprint("auto_compact_api", __name__)


@auto_compact_bp.record_once
def _start_trigger(_state):
    try:
        from api.auto_compact import triggers

        triggers.start()
    except Exception as exc:  # noqa: BLE001 — the feature is optional
        print(f"[AUTO COMPACT] trigger not started: {exc}", flush=True)


def _chat(session_id):
    _user, _sid, err = require_session_actor(session_id)
    if err:
        return None, err
    sid = numeric_chat_session_id(session_id)
    if sid is None:
        return None, (jsonify({"success": False, "error": "chat session id required"}), 400)
    return sid, None


@auto_compact_bp.route("/api/auto-compact/chats/<session_id>", methods=["GET"])
def get_chat_auto_compact(session_id):
    sid, err = _chat(session_id)
    if err:
        return err
    from api.auto_compact import service

    if not service.available():
        return jsonify({"success": True, "available": False})
    return jsonify({"success": True, **service.describe(sid, history_limit=5)})


@auto_compact_bp.route("/api/auto-compact/chats/<session_id>", methods=["POST"])
def set_chat_auto_compact(session_id):
    """Body: ``{override: {enabled?, percent?, milestone?}}``; null fields inherit."""
    sid, err = _chat(session_id)
    if err:
        return err
    from api.auto_compact import policy, service

    if not service.available():
        return jsonify({"success": True, "available": False})
    data = request.get_json(silent=True) or {}
    try:
        service.set_chat_override(sid, data.get("override"))
    except policy.PolicyError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, **service.describe(sid, history_limit=5)})
