"""HTTP transport for the Media App (page + read-only index).

One blueprint so ``web_chat_api`` registers it in a single line. Each signed-in
user sees only media from their own chats.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from api.http_authz import authenticated_required, current_user
from api.media_library import service

media_library_bp = Blueprint("media_library", __name__)

_WEB = Path(__file__).resolve().parents[2] / "web"


@media_library_bp.get("/media_page.html")
@authenticated_required
def media_page():
    return send_from_directory(_WEB, "media_page.html")


@media_library_bp.get("/api/media-library")
@authenticated_required
def list_route():
    raw_chat = (request.args.get("chat") or "").strip()
    chat_id = int(raw_chat) if raw_chat.isdigit() else None
    payload = service.list_media(int(current_user()["id"]), chat_id=chat_id)
    response = jsonify(success=True, **payload)
    response.headers["Cache-Control"] = "no-store"
    return response
