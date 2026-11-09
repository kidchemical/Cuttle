"""Account logins slide forward while in use instead of dying on day 30.

Regression: ``expires_at`` was fixed at login + 30 days and never renewed,
so the desktop app — in constant use — was signed out mid-session exactly 30
days after its login (CH-002063: token created 2026-10-07 19:34:48 UTC, 401s
from 2026-11-06 19:34:49 UTC). A refresh while signed out then reset the
active space's panes to new chats.
"""
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from api import auth_db as auth_db_mod
from api.auth_db import AuthDatabase


def _row(db, token):
    conn = db._get_connection()
    try:
        return conn.execute(
            'SELECT created_at, expires_at FROM auth_sessions WHERE session_token = ?',
            (token,),
        ).fetchone()
    finally:
        conn.close()


def _set_times(db, token, created, expires):
    conn = db._get_connection()
    try:
        conn.execute(
            'UPDATE auth_sessions SET created_at = ?, expires_at = ? WHERE session_token = ?',
            (created.isoformat(' '), expires.isoformat(' '), token),
        )
        conn.commit()
    finally:
        conn.close()


def _parse(ts):
    return datetime.fromisoformat(str(ts))


@pytest.fixture
def db(tmp_path):
    d = AuthDatabase(tmp_path / 'auth.db')
    uid = d.create_user('slide@local', 'Slide', 'local', username='slide')
    d._uid = uid
    return d


def test_login_near_expiry_is_renewed_on_use(db):
    token = db.create_auth_session(db._uid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Day 29 of a 30-day login: one day left.
    _set_times(db, token, now - timedelta(days=29), now + timedelta(days=1))
    assert db.verify_auth_session(token)
    expires = _parse(_row(db, token)['expires_at'])
    assert expires > now + timedelta(days=29)


def test_fresh_login_is_not_rewritten(db):
    token = db.create_auth_session(db._uid)
    before = _row(db, token)['expires_at']
    assert db.verify_auth_session(token)
    assert _row(db, token)['expires_at'] == before


def test_short_lived_token_is_never_extended(db):
    token = db.create_auth_session(db._uid, expires_hours=2)
    before = _parse(_row(db, token)['expires_at'])
    assert db.verify_auth_session(token)
    assert _parse(_row(db, token)['expires_at']) == before


def test_expired_login_is_not_resurrected(db):
    token = db.create_auth_session(db._uid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _set_times(db, token, now - timedelta(days=31), now - timedelta(seconds=1))
    assert db.verify_auth_session(token) is None


def test_auth_me_refreshes_cookie_for_cookie_login(db, monkeypatch):
    from api import auth_api

    token = db.create_auth_session(db._uid)
    monkeypatch.setattr(auth_api, 'get_auth_db', lambda: db)
    app = Flask(__name__)
    app.register_blueprint(auth_api.auth_bp, url_prefix='/api/auth')
    client = app.test_client()

    client.set_cookie('session_token', token)
    resp = client.get('/api/auth/me')
    assert resp.status_code == 200
    cookie = resp.headers.get('Set-Cookie') or ''
    assert 'session_token=' + token in cookie
    assert 'Max-Age=%d' % (30 * 24 * 60 * 60) in cookie

    # Header-token clients (phone WebViews) get no cookie written for them.
    client.delete_cookie('session_token')
    resp = client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token})
    assert resp.status_code == 200
    assert 'Set-Cookie' not in resp.headers


def test_lifetime_constant_backs_default():
    assert auth_db_mod.AUTH_SESSION_LIFETIME_HOURS == 720
