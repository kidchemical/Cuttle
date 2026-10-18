"""Owner-only issuance of short-lived voice credentials (never cached),
Realtime voice settings, and usage accounting for the live session."""
import base64
import json
import re

from flask import Blueprint, current_app, jsonify, request

from api import voice_usage
from api.http_authz import owner_required, current_user
from api.http_authz import require_chat_session_access
from api.voice_realtime import (
    catalog,
    create_session,
    load_settings,
    save_settings,
)

voice_realtime_bp = Blueprint("voice_realtime", __name__, url_prefix="/realtime")

_PREFIX_RE = re.compile(r"(?:/[a-zA-Z0-9_-]+\s+){1,3}")


@voice_realtime_bp.post("/session")
@owner_required
def session():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object."), 400
    sid = None
    if data.get("session_id"):
        _, sid, error = require_chat_session_access(data["session_id"])
        if error:
            return error
    context = data.get("chat_request") if isinstance(data.get("chat_request"), dict) else {}
    context = {key: context[key] for key in ("agent_pins", "sticky_agent", "project_id") if key in context}
    if data.get("transport") == "native":
        return _native_session(data, sid, context)
    try:
        out = create_session(current_user()["id"], sid, context, str(data.get("prefix") or "")[:128]) if sid else create_session(current_user()["id"])
        response = jsonify(success=True, **out)
    except PermissionError:
        response = jsonify(success=False, disabled=True, error="Realtime voice is disabled")
    except RuntimeError as exc:
        response = jsonify(success=False, error=str(exc))
        response.status_code = 503
    response.headers["Cache-Control"] = "no-store"
    return response


def _native_session(data, sid, context):
    """Android/native transport: Cuttle owns the OpenAI Realtime WebSocket.

    No ephemeral credential is minted; the client never talks to OpenAI. The
    page only needs an opaque Cuttle voice id to open the audio socket.
    """
    from api.auth_session import get_request_session_token
    from api.voice_realtime import service

    if not sid:
        return jsonify(success=False, error="Native Realtime voice needs an open chat."), 400
    prefix = str(data.get("prefix") or "")
    if (prefix and not _PREFIX_RE.fullmatch(prefix)) or len(prefix) > 128:
        return jsonify(success=False, error="Invalid voice agent prefix."), 400
    try:
        out = service.start_native(user_id=current_user()["id"], chat_id=sid,
            token=get_request_session_token(), prefix=prefix, chat_request=context)
    except PermissionError:
        response = jsonify(success=False, disabled=True, error="Realtime voice is disabled")
        response.status_code = 403
    except (ValueError, LookupError) as exc:
        return jsonify(success=False, error=str(exc)), 400
    else:
        response = jsonify(success=True, connection_id=out["id"], model=out["model"], native=True)
    response.headers["Cache-Control"] = "no-store"
    return response


@voice_realtime_bp.get("/sessions/<voice_id>")
@owner_required
def get_native_session(voice_id):
    from api.voice_realtime import service
    try:
        response = jsonify(success=True, session=service.snapshot(voice_id, current_user()["id"]))
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404
    response.headers["Cache-Control"] = "no-store"
    return response


@voice_realtime_bp.post("/sessions/<voice_id>/close")
@owner_required
def close_native_session(voice_id):
    from api.voice_realtime import service
    try:
        return jsonify(success=True, session=service.close(voice_id, current_user()["id"]))
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except Exception:
        return jsonify(success=False, error="Could not confirm voice closure; reconnect to check status."), 503


@voice_realtime_bp.get("/settings")
@owner_required
def get_settings():
    response = jsonify(success=True, voice_realtime=load_settings(), **catalog())
    response.headers["Cache-Control"] = "no-store"
    return response


@voice_realtime_bp.post("/settings")
@owner_required
def update_settings():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object"), 400
    partial = data.get("voice_realtime") if isinstance(data.get("voice_realtime"), dict) else data
    try:
        saved = save_settings(partial)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except RuntimeError as exc:
        return jsonify(success=False, error=str(exc)), 500
    return jsonify(success=True, voice_realtime=saved)


@voice_realtime_bp.post("/preview")
@owner_required
def preview():
    """Speak a short sample with the selected Realtime model and voice."""
    from api.experimental import is_enabled
    from api.voice_realtime import FLAG_ID
    from api.voice_realtime import preview as realtime_preview
    if not is_enabled(FLAG_ID):
        return jsonify(success=False, disabled=True, error="OpenAI Realtime is disabled in Settings → Experimental."), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object."), 400
    current = load_settings()
    try:
        audio, mime, meta = realtime_preview.synthesize(
            str(data.get("model") or current["model"]), str(data.get("voice") or current["voice"]))
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except RuntimeError as exc:
        return jsonify(success=False, error=str(exc)), 503
    except Exception as exc:
        from api.model_providers.cache import public_error
        return jsonify(success=False, error=public_error(exc, "Voice preview")), 503
    return jsonify(success=True, content_type=mime, cached=meta["cached"], cost=meta["cost"],
                   audio_base64=base64.b64encode(audio).decode("ascii"))


@voice_realtime_bp.post("/usage")
@owner_required
def record_usage():
    """Price browser-forwarded OpenAI Realtime usage and append a ledger row.

    Body: ``{model?, responses?: [usage…], transcriptions?: [usage…],
    session_id?}``. ``responses`` are ``response.done`` usage objects;
    ``transcriptions`` are input-transcription completion usage objects.
    """
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object"), 400
    responses = data.get("responses") if isinstance(data.get("responses"), list) else []
    transcriptions = data.get("transcriptions") if isinstance(data.get("transcriptions"), list) else []
    model = str(data.get("model") or load_settings()["model"]).strip()
    session_id = data.get("session_id")
    if data.get("connection_id"):
        from api.voice_live import store
        from api.model_providers import usage as ledger
        row = store.get(data["connection_id"], current_user()["id"])
        if not row or row["data"].get("engine") != "realtime":
            return jsonify(success=False, error="Voice session not found."), 404
        _, session_id, error = require_chat_session_access(row["chat_id"])
        if error:
            return error
        if len(responses) + len(transcriptions) > 1000:
            return jsonify(success=False, error="Too many voice usage events."), 400
        prefix = "realtime:" + row["id"] + ":"
        for kind, events in (("response", responses), ("transcription", transcriptions)):
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get("_usage_id"), str) or len(event["_usage_id"]) > 160:
                    return jsonify(success=False, error="Usage event ID required."), 400
                written = voice_usage.record_realtime_usage(row["data"]["model"],
                    [event] if kind == "response" else [], [event] if kind == "transcription" else [],
                    session_id=session_id, transcription_model=row["data"]["transcription_model"],
                    usage_key=prefix + kind + ":" + event["_usage_id"])
                if not written.get("recorded"):
                    return jsonify(success=False, error="Voice usage recording failed; retry the same event IDs."), 503
        records = [record for record in ledger.calls_for_sessions([session_id])
                   if (record.get("usage_key") or "").startswith(prefix)]
        priced = {"cost": sum(record.get("cost") or 0 for record in records), "cost_estimated": True,
                  "prompt_tokens": sum(record.get("prompt_tokens") or 0 for record in records),
                  "completion_tokens": sum(record.get("completion_tokens") or 0 for record in records),
                  "cumulative": True}
        return jsonify(success=True, usage=priced)
    priced = voice_usage.record_realtime_usage(
        model, responses, transcriptions, session_id=session_id,
    )
    return jsonify(success=True, usage=priced)


@voice_realtime_bp.post("/delegate")
@owner_required
def delegate():
    from api.auth_session import get_request_session_token
    from api.voice_live import store, service
    from api.voice_live.delegation import run_request
    from api.experimental import is_enabled
    if not is_enabled("voice_realtime"):
        return jsonify(success=False, error="Realtime is disabled."), 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected JSON object."), 400
    row = store.get(str(data.get("connection_id") or ""), current_user()["id"])
    if not row or row["data"].get("engine") != "realtime":
        return jsonify(success=False, error="Voice session not found."), 404
    _, sid, error = require_chat_session_access(row["chat_id"])
    if error:
        return error
    message = data.get("message")
    call_id = data.get("call_id")
    if not isinstance(message, str) or not 1 <= len(message) <= 16000 or not isinstance(call_id, str) or not 1 <= len(call_id) <= 160:
        return jsonify(success=False, error="Invalid delegated request."), 400
    if not store.claim_delegation(row["id"], call_id, {}):
        return jsonify(success=False, error="This request was already handled; check the chat."), 409
    run = service.Run(row["id"], row["user_id"], sid, get_request_session_token(), {},
                      prefix=row["data"].get("prefix", ""), chat_request=row["data"].get("context", {}))
    try:
        result = run_request(run, call_id, message)
    except Exception:
        result = {"error": "Cuttle handoff failed. Check the chat before retrying."}
    store.finish_delegation(row["id"], call_id, result)
    return jsonify(success=True, **result)


def register_socket():
    from flask_sock import Sock
    sock = Sock()

    @sock.route("/sessions/<voice_id>/socket", bp=voice_realtime_bp)
    def native_voice(socket, voice_id):
        from api.http_authz import require_owner
        from api.voice_realtime import service
        user, error = require_owner()
        if error:
            socket.close()
            return
        try:
            service.native_connection(voice_id, user["id"], socket)
        except Exception as exc:
            current_app.logger.exception("Realtime native transport failed for session %s", voice_id)
            status = getattr(exc, "status_code", None)
            if status in {401, 403}:
                message = "OpenAI rejected the Realtime connection; check API key and project access."
            elif status == 404:
                message = "OpenAI could not find the Realtime model for this project (HTTP 404)."
            elif status == 429:
                message = "OpenAI rate or usage limit blocked the Realtime connection (HTTP 429)."
            elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
                message = "Cuttle could not connect to OpenAI for Realtime; check the host network."
            elif "OpenAI API key" in str(exc):
                message = "Configure an OpenAI API key in Cuttle Settings → Providers."
            else:
                message = f"Realtime setup failed ({type(exc).__name__}); details were logged on the Cuttle host."
            try:
                from api.voice_live import store
                store.update(voice_id, state="disconnected", error=message, finalized=False)
            except Exception:
                current_app.logger.exception("Could not persist Realtime transport error for session %s", voice_id)
            try:
                socket.send(json.dumps({"type": "cuttle.error", "message": message}))
                socket.close()
            except Exception:
                pass


register_socket()
