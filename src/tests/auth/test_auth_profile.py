"""Change username / change password from the account panel.

Covers the account-panel self-service endpoints end to end:
rename is id-backed (same user id, sessions survive), duplicates and bad
names are rejected, password change requires the current password when one
is set and the new password works for the next login.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api import web_chat_api as w

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    from api.limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    c = w.app.test_client()
    c.settings = sm
    return c


def _register(client, username, password="password1", remote="127.0.0.1"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
        environ_base={"REMOTE_ADDR": remote},
    )


def test_change_username_keeps_user_id_and_session(client):
    from api.auth_db import get_auth_db

    assert _register(client, "oldname").status_code == 200
    db = get_auth_db()
    before = db.get_user_by_username("oldname")
    assert before is not None

    res = client.patch("/api/auth/me", json={"username": "newname"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["user"]["username"] == "newname"

    # Id-backed: same row, old handle gone, session still valid.
    after = db.get_user_by_username("newname")
    assert after is not None
    assert after["id"] == before["id"]
    assert db.get_user_by_username("oldname") is None

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["username"] == "newname"

    # Old username no longer logs in; new one does (fresh client).
    assert client.post("/api/auth/login", json={"username": "oldname", "password": "password1"}).status_code == 401
    fresh = client.application.test_client() if hasattr(client, "application") else None
    assert fresh is not None
    login = fresh.post("/api/auth/login", json={"username": "newname", "password": "password1"})
    assert login.status_code == 200


def test_change_username_rejects_taken_and_invalid(client):
    from api.auth_db import get_auth_db

    assert _register(client, "alice").status_code == 200
    db = get_auth_db()
    db.create_user("bob@local", "Bob", "local", password="password1", username="bob")

    taken = client.patch("/api/auth/me", json={"username": "BOB"})
    assert taken.status_code == 409

    for bad in ("ab", "has space", "no!good", ""):
        res = client.patch("/api/auth/me", json={"username": bad})
        assert res.status_code == 400, bad

    # Failed renames leave the original handle untouched.
    assert db.get_user_by_username("alice") is not None


def test_change_username_requires_auth(client):
    res = client.patch("/api/auth/me", json={"username": "nobody"})
    assert res.status_code == 401


def test_change_password_needs_current_and_updates_login(client):
    assert _register(client, "pwuser", password="oldpass99").status_code == 200

    wrong = client.post(
        "/api/auth/password",
        json={"current_password": "nottheright", "new_password": "newpass99"},
    )
    assert wrong.status_code == 401

    short = client.post(
        "/api/auth/password",
        json={"current_password": "oldpass99", "new_password": "short"},
    )
    assert short.status_code == 400

    ok = client.post(
        "/api/auth/password",
        json={"current_password": "oldpass99", "new_password": "newpass99"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["success"] is True

    # Session survives the change; next login needs the new password.
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/login", json={"username": "pwuser", "password": "oldpass99"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "pwuser", "password": "newpass99"}).status_code == 200


def test_change_password_requires_auth(client):
    res = client.post(
        "/api/auth/password",
        json={"current_password": "x", "new_password": "newpass99"},
    )
    assert res.status_code == 401


def test_user_payload_carries_role_owner_user_guest(client):
    first = _register(client, "roleowner")
    assert first.status_code == 200
    assert first.get_json()["user"]["role"] == "owner"

    client.settings.set_setting("auth", {"allow_registration": True})
    second = _register(client, "rolemember", remote="192.0.2.11")
    assert second.status_code == 200
    assert second.get_json()["user"]["role"] == "user"

    guest = client.post("/api/auth/guest", json={})
    assert guest.status_code == 200
    assert guest.get_json()["user"]["role"] == "guest"


def test_rename_keeps_role(client):
    assert _register(client, "rolekeeper").status_code == 200
    res = client.patch("/api/auth/me", json={"username": "rolekeeper2"})
    assert res.status_code == 200
    assert res.get_json()["user"]["role"] == "owner"


def test_account_panel_offers_username_and_password_change():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[3] / "src" / "web" / "app_shell.html").read_text(encoding="utf-8")
    assert "logoutConfirmChangeUsername" in html
    assert "logoutConfirmChangePassword" in html
    assert 'id="logoutConfirmRole"' in html

    js = (Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "shell" / "account_panel.js").read_text(
        encoding="utf-8"
    )
    assert "/api/auth/me" in js
    assert "/api/auth/password" in js
    assert "logoutConfirmRole" in js
