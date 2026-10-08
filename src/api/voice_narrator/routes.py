"""Authenticated transport over the voice narrator owner.

Answers ``200 {success: false, disabled: true}`` while the ``voice_narrator``
experimental flag is off.
"""

from __future__ import annotations

import base64

from flask import Blueprint, jsonify, request

from api import voice_narrator
from api.http_authz import authenticated_required

voice_narrator_bp = Blueprint("voice_narrator", __name__, url_prefix="/api/voice-narrator")

_DISABLED = {"success": False, "disabled": True, "error": "Voice narrator is disabled"}


@voice_narrator_bp.route("/narrate", methods=["POST"])
@authenticated_required
def narrate():
    if not voice_narrator.is_enabled():
        return jsonify(_DISABLED)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(success=False, error="expected JSON object"), 400
    try:
        out = voice_narrator.narrate(body)
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400
    except RuntimeError as error:
        return jsonify(success=False, error=str(error)), 503
    if not out["text"]:
        return jsonify(success=True, text=None)
    return jsonify(
        success=True,
        text=out["text"],
        audio_base64=base64.b64encode(out["audio"]).decode("ascii"),
        content_type="audio/mpeg",
    )
