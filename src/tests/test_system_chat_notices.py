"""Durable system notices in the auth chat log (refresh / cross-device)."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase


def test_post_system_message_persists_in_session_log(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "notices")
    token = db.create_auth_session(owner)

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    res = client.post(
        f"/api/auth/sessions/{sid}/messages",
        json={
            "role": "system",
            "content": "⏹ Generation cancelled.",
            "kind": "generation-stop",
        },
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["success"] is True
    assert body["message_id"]
    assert body["role"] == "system"

    listed = client.get(f"/api/auth/sessions/{sid}/messages")
    assert listed.status_code == 200
    msgs = listed.get_json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "⏹ Generation cancelled."
    meta = msgs[0].get("metadata") or {}
    if isinstance(meta, str):
        import json

        meta = json.loads(meta)
    assert meta.get("kind") == "generation-stop"
    assert msgs[0].get("kind") == "generation-stop"


def test_post_rejects_non_system_role(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "guard")
    token = db.create_auth_session(owner)

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    res = client.post(
        f"/api/auth/sessions/{sid}/messages",
        json={"role": "user", "content": "spoof"},
    )
    assert res.status_code == 400
    assert db.get_messages(sid) == []


def test_post_project_change_notice(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "proj")
    token = db.create_auth_session(owner)

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    note = "📁 Project: Demo Game → `E:\\Projects\\DemoGame`"
    res = client.post(
        f"/api/auth/sessions/{sid}/messages",
        json={"role": "system", "content": note, "kind": "project-change"},
    )
    assert res.status_code == 200
    msgs = client.get(f"/api/auth/sessions/{sid}/messages").get_json()["messages"]
    assert msgs[0]["content"] == note
