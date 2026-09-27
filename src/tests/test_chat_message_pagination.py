"""Auth chat message pagination (limit / before_id / has_more)."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase


def _seed_messages(db: AuthDatabase, sid: int, n: int) -> list[int]:
    ids = []
    for i in range(n):
        mid = db.add_message(sid, "user" if i % 2 == 0 else "assistant", f"msg-{i}")
        ids.append(mid)
    return ids


def test_get_messages_limit_returns_newest(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "paged")
    ids = _seed_messages(db, sid, 25)

    page = db.get_messages(sid, limit=10)
    assert len(page) == 10
    assert [m["content"] for m in page] == [f"msg-{i}" for i in range(15, 25)]
    assert page[0]["id"] == ids[15]
    assert page[-1]["id"] == ids[24]


def test_get_messages_before_id_older_page(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "paged")
    ids = _seed_messages(db, sid, 25)

    newest = db.get_messages(sid, limit=10)
    older = db.get_messages(sid, limit=10, before_id=newest[0]["id"])
    assert len(older) == 10
    assert [m["content"] for m in older] == [f"msg-{i}" for i in range(5, 15)]
    assert older[-1]["id"] < newest[0]["id"]
    assert older[0]["id"] == ids[5]


def test_message_page_meta_and_api(tmp_path: Path, monkeypatch):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "paged")
    _seed_messages(db, sid, 25)
    # One system notice should not count toward older_visible_count.
    db.add_message(sid, "system", "notice", metadata={"kind": "test"})

    newest = db.get_messages(sid, limit=10)
    meta = db.message_page_meta(sid, newest[0]["id"])
    assert meta["has_more"] is True
    # 25 user/assistant + 1 system = 26 total; newest page has 10 (incl system
    # if it is newest). System was appended last so it is in the newest page.
    assert newest[-1]["role"] == "system"
    assert meta["older_visible_count"] == 16  # msg-0..msg-15

    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    res = client.get(f"/api/auth/sessions/{sid}/messages?limit=10")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["has_more"] is True
    assert len(body["messages"]) == 10
    assert body["older_visible_count"] == 16

    before = body["messages"][0]["id"]
    older = client.get(
        f"/api/auth/sessions/{sid}/messages?limit=10&before_id={before}"
    ).get_json()
    assert older["success"] is True
    assert len(older["messages"]) == 10
    assert older["messages"][-1]["id"] < before
    assert older["has_more"] is True
