"""Link Google OAuth identity onto an existing local Cuttle account."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.auth_db import AuthDatabase


@pytest.fixture()
def db(tmp_path: Path):
    return AuthDatabase(db_path=tmp_path / "test_auth.db")


def test_link_oauth_keeps_local_login_and_sets_avatar(db: AuthDatabase):
    uid = db.create_user(
        email="ian@local",
        display_name="Ian",
        auth_provider="local",
        password="password123",
        username="ian",
    )
    assert uid

    ok, reason = db.link_oauth_provider(
        uid,
        "google",
        "google-sub-123",
        profile_image="https://lh3.googleusercontent.com/a/test",
    )
    assert ok and reason == "ok"

    user = db.get_user_by_id(uid)
    assert user["auth_provider"] == "local"
    assert user["provider_user_id"] == "google-sub-123"
    assert user["profile_image"] == "https://lh3.googleusercontent.com/a/test"
    assert user["username"] == "ian"
    assert db.verify_password("ian", "password123") == uid


def test_link_oauth_rejects_duplicate_provider(db: AuthDatabase):
    a = db.create_user(
        email="a@local",
        display_name="A",
        auth_provider="local",
        password="password123",
        username="alice",
    )
    b = db.create_user(
        email="b@local",
        display_name="B",
        auth_provider="local",
        password="password123",
        username="bob",
    )
    assert a and b
    ok, _ = db.link_oauth_provider(a, "google", "same-sub", profile_image="https://x/a.png")
    assert ok
    ok2, reason = db.link_oauth_provider(b, "google", "same-sub", profile_image="https://x/b.png")
    assert not ok2
    assert reason == "provider_already_linked"


def test_oauth_state_stores_link_user_id(db: AuthDatabase):
    uid = db.create_user(
        email="ian@local",
        display_name="Ian",
        auth_provider="local",
        password="password123",
        username="ian",
    )
    token = db.create_oauth_state("google", link_user_id=uid)
    row = db.consume_oauth_state(token, "google")
    assert row is not None
    assert int(row["link_user_id"]) == uid
    assert db.consume_oauth_state(token, "google") is None


def test_guest_google_linked_is_public():
    from api.auth_api import _user_public

    public = _user_public(
        {
            "id": 1,
            "username": "g_abcd",
            "email": "g_abcd@guest.local",
            "display_name": "Guest",
            "profile_image": "https://lh3.googleusercontent.com/a/x",
            "auth_provider": "guest",
            "provider_user_id": "google-sub-9",
        }
    )
    assert public["is_guest"] is True
    assert public["google_linked"] is True


def test_oauth_redirect_uses_https_base(monkeypatch):
    monkeypatch.setenv("CUTTLE_API_URL", "https://127.0.0.1:8080")
    from api import auth_api

    assert auth_api._oauth_redirect_uri("google") == (
        "https://127.0.0.1:8080/api/auth/callback/google"
    )


def test_public_oauth_status_resolves_google_rollout(monkeypatch):
    from flask import Flask
    from api.auth_api import auth_bp
    from api.experimental import set_enabled

    monkeypatch.delenv('CUTTLE_EXPERIMENTAL', raising=False)
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-secret')
    app = Flask(__name__)
    app.register_blueprint(auth_bp)
    client = app.test_client()

    def status():
        return client.get('/api/auth/oauth/status').get_json()['providers']['google']

    set_enabled('google_signin', False)
    assert status() == {'configured': True, 'enabled': False}
    set_enabled('google_signin', True)
    assert status() == {'configured': True, 'enabled': True}
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    assert status() == {'configured': True, 'enabled': False}
