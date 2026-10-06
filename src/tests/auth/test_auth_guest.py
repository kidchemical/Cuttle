"""Guest sign-in creates a session without a password."""

from __future__ import annotations


def test_guest_login_returns_user_and_cookie():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.post("/api/auth/guest", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["user"]["is_guest"] is True
    assert body["user"]["display_name"] == "Guest"
    assert body["user"]["username"].startswith("g_")
    set_cookie = res.headers.get("Set-Cookie") or ""
    assert "session_token=" in set_cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.get_json()
    assert me_body.get("authenticated") is True
    assert me_body["user"]["is_guest"] is True


def test_auth_js_has_guest_control():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "shared/auth.js").read_text(
        encoding="utf-8"
    )
    assert "/api/auth/guest" in src
    assert "Continue as guest" in src
    assert "Continue with Google" in src
    assert "/api/auth/oauth/google" in src
    assert "_hostWantsOauth" in src
    assert "_setAuthRequiredGate" in src


def test_shell_login_modal_includes_oauth():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[3] / "src" / "web" / "app_shell.html"
    ).read_text(encoding="utf-8")
    assert 'id="authFormsHost"' in html
    assert 'data-include-oauth="1"' in html
    assert "Continue with Google" in html
