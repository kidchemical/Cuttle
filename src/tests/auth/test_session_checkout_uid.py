"""Session project PATCH/payload round-trips the bound worktree ``checkout_uid``."""

from __future__ import annotations


def test_session_checkout_uid_round_trip(owner_session):
    from api.web_chat_api import app

    client = owner_session.sign_in(app.test_client())
    sid = owner_session.db.create_chat_session(owner_session.user_id, "wt")

    patched = client.patch(
        f"/api/auth/sessions/{sid}",
        json={
            "project_id": 1, "project_name": "Demo", "project_path": "/p",
            "checkout_uid": "abc",
        },
    )
    assert patched.status_code == 200, patched.get_json()
    assert patched.get_json().get("checkout_uid") == "abc"

    payload = client.get(f"/api/auth/sessions/{sid}/messages").get_json()
    assert payload["checkout_uid"] == "abc"

    # Clearing back to Local round-trips None.
    cleared = client.patch(
        f"/api/auth/sessions/{sid}",
        json={"project_id": 1, "project_name": "Demo", "project_path": "/p", "checkout_uid": None},
    )
    assert cleared.status_code == 200
    assert client.get(f"/api/auth/sessions/{sid}/messages").get_json()["checkout_uid"] is None
