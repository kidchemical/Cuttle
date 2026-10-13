"""Authenticated GPT-Live transport and durable voice usage snapshots."""
import re

from flask import Blueprint, jsonify, request
from api.auth_session import get_request_session_token
from api.http_authz import owner_required, authenticated_required, current_user, require_chat_session_access
from api.voice_live import service
from api import voice_usage

voice_live_bp = Blueprint("voice_live", __name__, url_prefix="/live")
voice_usage_bp = Blueprint("voice_usage", __name__)


@voice_live_bp.post("/sessions")
@owner_required
def create_session():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object."), 400
    user, sid, error = require_chat_session_access(data.get("session_id"))
    if error:
        return error
    prefix = str(data.get("prefix") or "")
    if prefix and not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+\s+){1,3}", prefix) or len(prefix) > 128:
        return jsonify(success=False, error="Invalid voice agent prefix."), 400
    supplied = data.get("chat_request") or {}
    if not isinstance(supplied, dict):
        return jsonify(success=False, error="Invalid chat context."), 400
    # Normal chat validates these same selection fields. Never accept transport,
    # auth, session identity, instructions, or attachments from the voice model.
    context = {key: supplied[key] for key in ("agent_pins", "sticky_agent", "project_id") if key in supplied}
    try:
        out = service.start(user_id=user["id"], chat_id=sid, token=get_request_session_token(),
            request_id=data.get("request_id"), sdp=data.get("sdp"), prefix=prefix,
            chat_request=context, native=data.get("transport") == "native")
        response = jsonify(success=True, session=out)
        response.headers["Cache-Control"] = "no-store"
        return response
    except PermissionError as exc:
        return jsonify(success=False, disabled=True, error=str(exc)), 403
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except RuntimeError as exc:
        return jsonify(success=False, error=str(exc)), 503


@voice_live_bp.get("/sessions/<voice_id>")
@owner_required
def get_session(voice_id):
    try:
        response = jsonify(success=True, session=service.snapshot(voice_id, current_user()["id"]))
        response.headers["Cache-Control"] = "no-store"
        return response
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404


@voice_live_bp.post("/sessions/<voice_id>/close")
@owner_required
def close_session(voice_id):
    try:
        return jsonify(success=True, session=service.close(voice_id, current_user()["id"]))
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except Exception:
        return jsonify(success=False, error="Could not confirm voice session closure; reconnect to check status."), 503


@voice_usage_bp.get("/usage/<int:sid>")
@authenticated_required
def session_usage(sid):
    user, sid, error = require_chat_session_access(sid)
    if error:
        return error
    from api.auth_db import get_auth_db
    ids = [sid]
    if request.args.get("include_subagents") == "1":
        ids += get_auth_db().descendant_session_ids(sid, user["id"])
    try:
        entries = voice_usage.session_entries(ids)
        return jsonify(success=True, entries=entries)
    except Exception:
        return jsonify(success=False, error="Voice usage is temporarily unavailable."), 503


def register_socket():
    from flask_sock import Sock
    sock = Sock()

    @sock.route("/sessions/<voice_id>/socket", bp=voice_live_bp)
    def native_voice(socket, voice_id):
        from api.http_authz import require_owner
        user, error = require_owner()
        if error:
            socket.close()
            return
        try:
            service.native_connection(voice_id, user["id"], socket)
        except Exception:
            try:
                socket.send('{"type":"cuttle.error","message":"Native voice connection failed; reconnect to retry."}')
                socket.close()
            except Exception:
                pass


register_socket()
