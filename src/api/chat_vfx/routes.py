"""Authenticated transport over the VFX owner."""
from flask import Blueprint, jsonify, request
from api.http_authz import require_chat_session_access, require_owner
from api import chat_vfx

chat_vfx_bp = Blueprint('chat_vfx', __name__, url_prefix='/api/chat-vfx')

@chat_vfx_bp.route('/<session_id>', methods=['GET', 'POST'])
def effects(session_id):
    if request.method == 'POST':
        _, error = require_owner()
        if error:
            return error
    _, session, error = require_chat_session_access(session_id)
    if error:
        return error
    try:
        if request.method == 'GET':
            events = chat_vfx.pending(session, int(request.args.get('after', '0')))
            return jsonify(success=True, events=events)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('expected JSON object')
        body = dict(body)
        event_id = chat_vfx.publish(session, body.pop('kind', None), **body)
        return jsonify(success=True, event_id=event_id, status='queued')
    except (ValueError, TypeError) as error:
        return jsonify(success=False, error=str(error)), 400
