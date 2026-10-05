"""Chat activity push stream — one SSE connection instead of per-session polls.

Transport only. Emits an ``activity`` event whenever the live-status store
(``api.chat_live_status``) changes, so external listeners (Cuttle Pets'
bridge) can react immediately and re-read busy chats through the existing
``/api/auth/sessions`` + ``/api/chat-live-status-batch`` routes.

Events carry only a change counter, never session ids or status text, so
the stream cannot leak another user's chats; authorization and per-user
filtering stay with the read routes.

- Auth: any authenticated user (same as the read routes).
- This module must never import ``api.web_chat_api``.
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, stream_with_context

from api import chat_live_status
from api.http_authz import require_authenticated

activity_bp = Blueprint("activity_stream", __name__, url_prefix="/api")

KEEPALIVE_SEC = 15.0


@activity_bp.route("/activity/stream", methods=["GET"])
def activity_stream():
    _user, err = require_authenticated()
    if err:
        return err

    def generate():
        version = chat_live_status.change_version()
        yield f"event: activity\ndata: {json.dumps({'version': version})}\n\n"
        last_write = time.monotonic()
        while True:
            wait = max(0.0, KEEPALIVE_SEC - (time.monotonic() - last_write))
            current = chat_live_status.wait_for_change(version, wait)
            if current != version:
                version = current
                yield f"event: activity\ndata: {json.dumps({'version': version})}\n\n"
            else:
                yield ": keepalive\n\n"
            last_write = time.monotonic()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
