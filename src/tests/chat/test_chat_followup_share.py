"""Shared composer follow-up queue (phone + PC)."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase


def test_followup_queue_roundtrip_and_atomic_take(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "shared queue")

    assert db.get_followup_queue(sid, owner) == []
    first = db.append_followup(
        sid, owner, {"id": "fq_a", "content": "from phone", "rawMessage": "from phone", "created": 1}
    )
    assert [x["id"] for x in first] == ["fq_a"]
    second = db.append_followup(
        sid, owner, {"id": "fq_b", "content": "from pc", "rawMessage": "from pc", "created": 2}
    )
    assert [x["id"] for x in second] == ["fq_a", "fq_b"]

    taken = db.take_followup_queue(sid, owner)
    assert taken is not None
    assert [x["rawMessage"] for x in taken["taken"]] == ["from phone", "from pc"]
    assert taken["remaining"] == []
    assert db.get_followup_queue(sid, owner) == []
    empty = db.take_followup_queue(sid, owner)
    assert empty is not None
    assert empty["taken"] == []
    assert empty["remaining"] == []


def test_followup_take_leaves_paused_items(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "paused queue")

    db.set_followup_queue(
        sid,
        owner,
        [
            {"id": "fq_ready", "content": "go", "rawMessage": "go", "created": 1, "paused": False},
            {"id": "fq_hold", "content": "hold", "rawMessage": "hold", "created": 2, "paused": True},
        ],
    )
    result = db.take_followup_queue(sid, owner)
    assert result is not None
    assert [x["id"] for x in result["taken"]] == ["fq_ready"]
    assert [x["id"] for x in result["remaining"]] == ["fq_hold"]
    assert result["remaining"][0]["paused"] is True
    assert [x["id"] for x in db.get_followup_queue(sid, owner)] == ["fq_hold"]


def test_messages_payload_includes_followups(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "payload")
    db.append_followup(sid, owner, {"id": "fq_1", "content": "queued", "rawMessage": "queued"})
    token = db.create_auth_session(owner)

    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    res = client.get(f"/api/auth/sessions/{sid}/messages")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["followups"][0]["id"] == "fq_1"

    take = client.post(f"/api/auth/sessions/{sid}/followups/take")
    assert take.status_code == 200
    assert take.get_json()["followups"][0]["id"] == "fq_1"
    again = client.get(f"/api/auth/sessions/{sid}/followups")
    assert again.get_json()["followups"] == []
