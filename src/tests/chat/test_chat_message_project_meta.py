"""Per-message project metadata for chat bubbles."""

from __future__ import annotations

from api import web_chat_api as wca


class _PM:
    def get_projects(self):
        return [
            {"id": 4, "name": "Cuttle", "path": r"C:\Projects\Cuttle\src"},
            {"id": 6, "name": "Demo Game", "path": r"E:\Projects\DemoGame\source"},
        ]

    def get_project(self, pid):
        return next((p for p in self.get_projects() if p["id"] == int(pid)), None)


def test_assistant_metadata_uses_cursor_cwd(monkeypatch):
    monkeypatch.setattr(wca, "project_manager", _PM())
    meta = wca._assistant_message_metadata(
        {
            "type": "cursor_command",
            "response": "ok",
            "cursor_run": {
                "cwd": r"E:\Projects\DemoGame\source",
                "requested_model": "auto",
            },
        },
        request_data={},
    )
    assert meta["project_name"] == "Demo Game"
    assert int(meta["project_id"]) == 6
    assert meta["slash_command"]["chips"][0]["label"] == "Cursor - Auto"


def test_metadata_does_not_invent_default_project_without_context(monkeypatch):
    monkeypatch.setattr(wca, "project_manager", _PM())
    meta = wca._assistant_message_metadata({"type": "plain", "response": "hi"}, request_data={})
    assert not meta or not meta.get("project_name")


def test_user_persist_merges_request_project(monkeypatch, tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "ep chat")
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr(wca, "project_manager", _PM())
    monkeypatch.setattr(
        wca,
        "_current_request_data",
        lambda: {
            "session_id": sid,
            "project_id": 6,
            "project_name": "Demo Game",
            "project_path": r"E:\Projects\DemoGame\source",
        },
    )
    wca._persist_auth_user_message(sid, "/cursor wall cling")
    msg = db.get_messages(sid)[-1]
    meta = msg.get("metadata") or {}
    assert meta.get("project_name") == "Demo Game"
    assert int(meta.get("project_id")) == 6
