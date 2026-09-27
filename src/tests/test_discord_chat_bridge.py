"""Discord DMs become owner-owned CH sessions."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase
from api import discord_chat_bridge as bridge


def _db(tmp_path: Path) -> AuthDatabase:
    return AuthDatabase(tmp_path / "cuttle_auth.db")


def test_dm_creates_one_session_and_reuses_it(tmp_path: Path, monkeypatch):
    db = _db(tmp_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x", username="alice")
    assert owner

    monkeypatch.setattr(bridge, "get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")

    session = {"session_kind": "discord_dm", "user_id": "99", "channel_id": "dm-1"}
    ctx = {"id": "99", "display_name": "JamminJoey", "username": "jamminjoey", "channel_id": "dm-1"}

    sid1, created1 = bridge.resolve_discord_chat_session(ctx, session)
    assert created1 is True
    assert sid1
    assert session["session_id"] == f"db_session_{sid1}"
    assert session["chat_session_id"] == sid1

    session2 = {"session_kind": "discord_dm", "user_id": "99", "channel_id": "dm-1"}
    sid2, created2 = bridge.resolve_discord_chat_session(ctx, session2)
    assert created2 is False
    assert sid2 == sid1

    listed = db.get_user_chat_sessions(owner)
    # Empty until a message is stored.
    assert listed == []

    bridge.persist_discord_user_turn(sid1, "the pig is clipping", ctx)
    bridge.persist_discord_assistant_turn(
        sid1,
        {"response": "I'll look at the maze pig.", "query_id": "q1"},
    )
    listed = db.get_user_chat_sessions(owner)
    assert len(listed) == 1
    row = listed[0]
    assert row["id"] == sid1
    assert row["origin"] == "discord_dm"
    assert row["discord_user_id"] == "99"
    assert row["session_name"] == "JamminJoey"

    msgs = db.get_messages(sid1)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "clipping" in msgs[0]["content"]
    assert msgs[0]["metadata"]["speaker"] == "JamminJoey"


def test_guild_sessions_are_per_user_and_channel(tmp_path: Path, monkeypatch):
    db = _db(tmp_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x", username="kid")
    monkeypatch.setattr(bridge, "get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")

    ctx = {"id": "77", "display_name": "Joey", "channel_id": "chan-a"}
    a = {"session_kind": "discord_guild", "user_id": "77", "channel_id": "chan-a"}
    b = {"session_kind": "discord_guild", "user_id": "77", "channel_id": "chan-b"}
    sid_a, _ = bridge.resolve_discord_chat_session(ctx, a)
    sid_b, _ = bridge.resolve_discord_chat_session({**ctx, "channel_id": "chan-b"}, b)
    assert sid_a and sid_b and sid_a != sid_b


def test_cancelled_assistant_is_not_persisted(tmp_path: Path, monkeypatch):
    db = _db(tmp_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x", username="kid")
    monkeypatch.setattr(bridge, "get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    session = {"session_kind": "discord_dm"}
    ctx = {"id": "1", "display_name": "Joey"}
    sid, _ = bridge.resolve_discord_chat_session(ctx, session)
    bridge.persist_discord_assistant_turn(
        sid, {"response": "[CANCELLED] Cursor Agent run was cancelled."}
    )
    assert db.get_messages(sid) == []
    assert owner


def test_invite_moves_link_and_dms_follow_the_target_chat(tmp_path: Path, monkeypatch):
    db = _db(tmp_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x", username="kid")
    monkeypatch.setattr(bridge, "get_auth_db", lambda: db)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    monkeypatch.setattr(
        bridge,
        "dm_discord_user",
        lambda *_a, **_k: {"success": True, "channel_id": "dm-1"},
    )

    ctx = {"id": "99", "display_name": "JamminJoey", "username": "jamminjoey", "channel_id": "dm-1"}
    dm_session = {"session_kind": "discord_dm", "user_id": "99", "channel_id": "dm-1"}
    dm_sid, created = bridge.resolve_discord_chat_session(ctx, dm_session)
    assert created is True
    assert dm_sid

    web_sid = db.create_chat_session(owner, "EP debug")
    result = bridge.invite_discord_to_session(web_sid, "JamminJoey", notify=True)
    assert result["success"] is True
    assert result["discord_user_id"] == "99"
    assert int(result["chat_session_id"]) == web_sid
    assert result["notified"] is True

    link = db.find_discord_link("99")
    assert link
    assert int(link["chat_session_id"]) == web_sid

    follow = {"session_kind": "discord_dm", "user_id": "99", "channel_id": "dm-1"}
    follow_sid, follow_created = bridge.resolve_discord_chat_session(ctx, follow)
    assert follow_created is False
    assert follow_sid == web_sid
    assert follow_sid != dm_sid

    # Guild mentions keep their own CH — invite is DM-only.
    guild = {"session_kind": "discord_guild", "user_id": "99", "channel_id": "chan-g"}
    guild_sid, _ = bridge.resolve_discord_chat_session(
        {**ctx, "channel_id": "chan-g"}, guild
    )
    assert guild_sid and guild_sid != web_sid
    # Guild traffic must not steal the DM invite off the target CH.
    assert int(db.find_discord_link("99")["chat_session_id"]) == web_sid

    attached = db.list_discord_links_for_sessions([web_sid, dm_sid])
    assert len(attached.get(web_sid) or []) == 1
    assert (attached.get(dm_sid) or []) == []


def test_parse_invite_slash():
    assert bridge.parse_invite_slash("/invite") == ""
    assert bridge.parse_invite_slash("/invite JamminJoey") == "JamminJoey"
    assert bridge.parse_invite_slash("/cursor /invite 99") == "99"
    assert bridge.parse_invite_slash("/help") is None
    assert bridge.parse_invite_slash("/invitee") is None
    assert bridge.parse_invite_slash("please /invite Joey") is None
