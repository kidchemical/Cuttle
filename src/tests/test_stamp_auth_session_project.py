"""Auth session project stamp — client chip wins; never invent Cuttle."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_stamp_skips_when_request_has_no_project(monkeypatch):
    from api import web_chat_api as api

    db = MagicMock()
    db.get_session_project.return_value = {}
    monkeypatch.setattr(api, "get_auth_db", lambda: db)

    api._stamp_auth_session_project({"id": 1}, 111, {"message": "hello"})
    db.set_session_project.assert_not_called()


def test_stamp_writes_client_project(monkeypatch):
    from api import web_chat_api as api

    db = MagicMock()
    db.get_session_project.return_value = {}
    monkeypatch.setattr(api, "get_auth_db", lambda: db)

    api._stamp_auth_session_project(
        {"id": 1},
        111,
        {
            "project_id": 7,
            "project_name": "Demo Game",
            "project_path": r"E:\Projects\DemoGame",
        },
    )
    db.set_session_project.assert_called_once_with(
        111,
        1,
        project_id=7,
        project_name="Demo Game",
        project_path=r"E:\Projects\DemoGame",
    )


def test_stamp_skips_when_same_project_already_stored(monkeypatch):
    from api import web_chat_api as api

    db = MagicMock()
    db.get_session_project.return_value = {
        "project_id": 7,
        "project_name": "Demo Game",
        "project_path": r"E:\Projects\DemoGame",
    }
    monkeypatch.setattr(api, "get_auth_db", lambda: db)

    api._stamp_auth_session_project(
        {"id": 1},
        111,
        {
            "project_id": 7,
            "project_name": "Demo Game",
            "project_path": r"E:\Projects\DemoGame",
        },
    )
    db.set_session_project.assert_not_called()


def test_stamp_updates_when_client_switches_project(monkeypatch):
    """CH-000204: mid-chat /project to Escape Purgatory must replace stamped Cuttle."""
    from api import web_chat_api as api

    db = MagicMock()
    db.get_session_project.return_value = {
        "project_id": 4,
        "project_name": "Cuttle",
        "project_path": r"C:\Projects\Cuttle\src",
    }
    monkeypatch.setattr(api, "get_auth_db", lambda: db)

    api._stamp_auth_session_project(
        {"id": 1},
        204,
        {
            "project_id": 7,
            "project_name": "Demo Game",
            "project_path": r"E:\Projects\DemoGame",
        },
    )
    db.set_session_project.assert_called_once_with(
        204,
        1,
        project_id=7,
        project_name="Demo Game",
        project_path=r"E:\Projects\DemoGame",
    )
