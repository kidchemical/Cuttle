"""Short-lived, session/route-scoped capabilities for trusted local CLI callers.

The local filesystem is the trust boundary, as with other agent-ops CLIs.
These are not login sessions and cannot authorize arbitrary HTTP routes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PREFIX = 'cuttle-chat-op.'
ROUTES = {('GET', '/api/chat-live-status'), ('POST', '/api/chat'),
          ('POST', '/api/chat-steer'), ('POST', '/api/chat-cancel')}


def _key():
    from core.runtime_paths import secrets_dir
    path = secrets_dir() / 'chat-ops.key'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids replacing another process's key. A reader can
    # race the initial write, so retry a short/empty read, never sign with it.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as out:
            out.write(secrets.token_bytes(32))
    for _ in range(20):
        key = path.read_bytes()
        if len(key) == 32:
            return key
        time.sleep(.01)
    raise RuntimeError('Local chat capability key is unavailable')


def issue(user_id, session_id, method, path):
    if (method, path) not in ROUTES:
        raise ValueError('Unsupported local chat operation')
    body = json.dumps({'v': 1, 'uid': int(user_id), 'sid': int(session_id),
                       'method': method, 'path': path, 'exp': int(time.time()) + 60},
                      separators=(',', ':'), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip('=')
    signature = hmac.new(_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return PREFIX + encoded + '.' + signature


def verify(token, db):
    from flask import has_request_context, request
    from api.cuttle_ui_capabilities import numeric_chat_session_id
    if not has_request_context() or request.remote_addr not in ('127.0.0.1', '::1'):
        return None
    if (request.method, request.path) not in ROUTES:
        return None
    try:
        encoded, signature = token[len(PREFIX):].split('.')
        expected = hmac.new(_key(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        body = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        now = time.time()
        data = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
        if (body['v'] != 1 or not now < body['exp'] <= now + 61
                or body['method'] != request.method or body['path'] != request.path
                or body['sid'] != numeric_chat_session_id(data.get('session_id'))):
            return None
        if not db.get_chat_session(body['sid'], body['uid']):
            return None
        user = db.get_user_by_id(body['uid'])
        return user if user and user.get('is_active') else None
    except (ValueError, TypeError, KeyError, OSError):
        return None
