"""Loopback adapter over ordinary chat HTTP; never runs a harness itself."""
from __future__ import annotations

import ipaddress
import warnings
from urllib.parse import urlsplit


def request(user_id, session_id, method, path, data=None, *, stream=False):
    import requests
    from api.internal_http import _internal_api_base
    from api.chat_ops.local_auth import issue
    base = _internal_api_base()
    parsed = urlsplit(base)
    try:
        local = ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = parsed.hostname == 'localhost'
    if not local or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise ValueError('Chat operations require a loopback API base')
    payload = {**(data or {}), 'session_id': f'db_session_{session_id}'}
    token = issue(user_id, session_id, method, path)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', requests.packages.urllib3.exceptions.InsecureRequestWarning)
        response = requests.request(method, base + path,
            params=payload if method == 'GET' else None,
            json=payload if method != 'GET' else None,
            headers={'Authorization': 'Bearer ' + token}, verify=False,
            timeout=(5, 60), stream=stream, allow_redirects=False)
    if stream:
        return response
    with response:
        result = response.json()
        if response.status_code >= 400:
            raise RuntimeError(str(result.get('error') or f'HTTP {response.status_code}'))
        return result
