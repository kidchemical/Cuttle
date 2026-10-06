"""Owner bootstrap: one durable owner, closed self-registration after it."""

from __future__ import annotations

import pytest

from api.http_authz import is_owner_user


@pytest.fixture
def client(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api import web_chat_api as w

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    from api.limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    client = w.app.test_client()
    client.settings = sm
    return client


def _register(client, username, remote="127.0.0.1"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "password1"},
        environ_base={"REMOTE_ADDR": remote},
    )


def test_first_account_from_lan_is_refused(client):
    res = _register(client, "intruder", remote="192.0.2.10")
    assert res.status_code == 403
    assert res.get_json()["registration_closed"] is True


def test_first_loopback_account_becomes_owner_and_closes_registration(client):
    from api.auth_db import get_auth_db

    assert _register(client, "owner").status_code == 200
    db = get_auth_db()
    assert is_owner_user(db.get_user_by_username("owner")) is True

    second = _register(client, "second")
    assert second.status_code == 403
    assert db.get_user_by_username("second") is None


def test_allow_registration_adds_non_owner_accounts(client):
    from api.auth_db import get_auth_db

    assert _register(client, "owner").status_code == 200
    client.settings.set_setting("auth", {"allow_registration": True})
    assert _register(client, "friend", remote="192.0.2.11").status_code == 200

    db = get_auth_db()
    assert is_owner_user(db.get_user_by_username("owner")) is True
    assert is_owner_user(db.get_user_by_username("friend")) is False


def test_deactivated_first_account_hands_ownership_to_next(monkeypatch):
    from api.auth_db import get_auth_db

    db = get_auth_db()
    first = db.create_user("a@local", "A", "local", password="password1", username="a")
    second = db.create_user("b@local", "B", "local", password="password1", username="b")
    db.create_user("g@guest.local", "G", "guest", username="g")
    assert is_owner_user(db.get_user_by_id(second)) is False

    conn = db._get_connection()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (first,))
    conn.commit()
    conn.close()
    assert is_owner_user(db.get_user_by_id(second)) is True
