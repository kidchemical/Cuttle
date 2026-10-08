"""Authenticated transport over the voice transcription owner.

Answers ``200 {success: false, disabled: true}`` while the ``voice_server_stt``
experimental flag is off.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api import voice_stt
from api.http_authz import authenticated_required

voice_stt_bp = Blueprint("voice_stt", __name__, url_prefix="/api/voice-stt")

_DISABLED = {"success": False, "disabled": True, "error": "Server transcription is disabled"}


@voice_stt_bp.route("/transcribe", methods=["POST"])
@authenticated_required
def transcribe():
    if not voice_stt.is_enabled():
        return jsonify(_DISABLED)
    upload = request.files.get("audio")
    if upload is None:
        return jsonify(success=False, error="audio file is required"), 400
    try:
        duration = int(request.form.get("duration_ms") or 0) or None
    except ValueError:
        duration = None
    try:
        text = voice_stt.transcribe(
            upload.read(voice_stt.MAX_AUDIO_BYTES + 1),
            mime=upload.mimetype or "audio/webm",
            prompt=request.form.get("prompt") or "",
            language=request.form.get("language") or "",
            duration_ms=duration,
        )
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400
    except RuntimeError as error:
        return jsonify(success=False, error=str(error)), 503
    return jsonify(success=True, text=text)
