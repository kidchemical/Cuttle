"""Archive / unarchive chat sessions (history-cleanup without delete).

Delete stays a soft-delete (``is_active = 0``); archiving only hides the chat
from the history list and search until restored from the Archived section.
"""
import pytest


def _session_with_message(db, user_id, name="chat"):
    sid = db.create_chat_session(user_id, name)
    db.add_message(sid, "user", "hello")
    return sid


def test_archive_hides_from_list_and_search(owner_session):
    db = owner_session.db
    uid = owner_session.user_id
    sid = _session_with_message(db, uid, "keep me")

    assert db.set_session_archived(sid, uid, True) is True
    assert [s["id"] for s in db.get_user_chat_sessions(uid)] == []
    archived = db.get_archived_chat_sessions(uid)
    assert [s["id"] for s in archived] == [sid]
    # Archived chats stay out of history search too.
    assert db.search_user_chats(uid, "keep me") == []


def test_unarchive_restores_to_list(owner_session):
    db = owner_session.db
    uid = owner_session.user_id
    sid = _session_with_message(db, uid)

    db.set_session_archived(sid, uid, True)
    assert db.set_session_archived(sid, uid, False) is True
    assert [s["id"] for s in db.get_user_chat_sessions(uid)] == [sid]
    assert db.get_archived_chat_sessions(uid) == []


def test_archived_session_still_openable(owner_session):
    db = owner_session.db
    uid = owner_session.user_id
    sid = _session_with_message(db, uid)

    db.set_session_archived(sid, uid, True)
    assert db.get_chat_session(sid, uid)["id"] == sid


def test_archive_rejects_other_users_session(owner_session):
    db = owner_session.db
    other = db.create_user("other@local", "Other", "local", password="x")
    sid = _session_with_message(db, other)

    assert db.set_session_archived(sid, owner_session.user_id, True) is False
    assert [s["id"] for s in db.get_user_chat_sessions(other)] == [sid]


def test_delete_behavior_unchanged(owner_session):
    """Delete still soft-deletes; archived+deleted chats surface nowhere."""
    db = owner_session.db
    uid = owner_session.user_id
    sid = _session_with_message(db, uid)

    db.set_session_archived(sid, uid, True)
    assert db.delete_chat_session(sid, uid) is True
    assert db.get_user_chat_sessions(uid) == []
    assert db.get_archived_chat_sessions(uid) == []
    assert db.set_session_archived(sid, uid, False) is False


def _client(owner_session):
    from api import web_chat_api as wca

    return owner_session.sign_in(wca.app.test_client())


def test_patch_archived_round_trip(owner_session):
    client = _client(owner_session)
    sid = _session_with_message(owner_session.db, owner_session.user_id, "http chat")

    resp = client.patch(f"/api/auth/sessions/{sid}", json={"archived": True})
    assert resp.status_code == 200
    assert resp.get_json()["archived"] is True

    default = client.get("/api/auth/sessions").get_json()["sessions"]
    assert [s["id"] for s in default] == []

    only = client.get("/api/auth/sessions?archived=only").get_json()["sessions"]
    assert [s["id"] for s in only] == [sid]

    resp = client.patch(f"/api/auth/sessions/{sid}", json={"archived": False})
    assert resp.status_code == 200
    default = client.get("/api/auth/sessions").get_json()["sessions"]
    assert [s["id"] for s in default] == [sid]


def test_patch_archived_missing_session_404(owner_session):
    client = _client(owner_session)
    resp = client.patch("/api/auth/sessions/987654", json={"archived": True})
    assert resp.status_code == 404
