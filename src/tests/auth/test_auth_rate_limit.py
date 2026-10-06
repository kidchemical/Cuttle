"""Auth and pairing endpoints reject bursts from one IP."""

from __future__ import annotations


def _post(client, path: str, ip: str, payload: dict | None = None, method: str = "POST"):
    env = {"REMOTE_ADDR": ip}
    if method == "GET":
        return client.get(path, environ_overrides=env)
    return client.post(path, json=payload or {}, environ_overrides=env)


def test_login_returns_429_after_five_attempts():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    ip = "203.0.113.10"
    payload = {"username": "nope", "password": "wrong-password-xx"}
    codes = [_post(client, "/api/auth/login", ip, payload).status_code for _ in range(6)]
    assert codes[:5] == [401, 401, 401, 401, 401]
    assert codes[5] == 429


def test_register_returns_429_after_five_attempts():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    ip = "203.0.113.11"
    payload = {"username": "ab", "password": "short"}
    codes = [_post(client, "/api/auth/register", ip, payload).status_code for _ in range(6)]
    assert codes[:5] == [400, 400, 400, 400, 400]
    assert codes[5] == 429


def test_pairing_approve_returns_429_after_five_attempts():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    ip = "203.0.113.12"
    payload = {"code": "AAAAAA"}
    codes = [_post(client, "/api/pairing/approve", ip, payload).status_code for _ in range(6)]
    assert codes[:5] == [401, 401, 401, 401, 401]
    assert codes[5] == 429


def test_pairing_pending_returns_429_after_five_attempts():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    ip = "203.0.113.13"
    codes = [_post(client, "/api/pairing/pending", ip, method="GET").status_code for _ in range(6)]
    assert codes[:5] == [401, 401, 401, 401, 401]
    assert codes[5] == 429


def test_oauth_login_returns_429_after_ten_attempts():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    ip = "203.0.113.14"
    codes = [
        client.get("/api/auth/oauth/google", environ_overrides={"REMOTE_ADDR": ip}).status_code
        for _ in range(11)
    ]
    assert 429 not in codes[:10]
    assert codes[10] == 429
