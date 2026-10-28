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
        checkout_uid=None,
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
        checkout_uid=None,
    )


def _stamp(stored, data, owners):
    from api.chat_session_project import stamp_auth_session_project

    db = MagicMock()
    db.get_session_project.return_value = stored
    stamp_auth_session_project(
        {"id": 1}, 1576, data, db=db,
        checkout_belongs=lambda uid, pid: owners.get(uid) == pid,
    )
    return db


def test_stamp_refuses_a_checkout_from_another_project():
    """CH-001576: a Cuttle chat must never persist a Demo Game worktree binding."""
    db = _stamp(
        {},
        {"project_id": 4, "project_name": "Cuttle", "project_path": "/c", "checkout_uid": "wt71"},
        {"wt71": 7},
    )
    db.set_session_project.assert_called_once_with(
        1576, 1, project_id=4, project_name="Cuttle", project_path="/c", checkout_uid=None,
    )


def test_stamp_drops_stored_checkout_when_project_switches():
    """Switching project without naming a checkout must not carry the old worktree over."""
    db = _stamp(
        {"project_id": 7, "project_name": "Demo Game", "project_path": "/d", "checkout_uid": "wt71"},
        {"project_id": 4, "project_name": "Cuttle", "project_path": "/c"},
        {"wt71": 7},
    )
    db.set_session_project.assert_called_once_with(
        1576, 1, project_id=4, project_name="Cuttle", project_path="/c", checkout_uid=None,
    )


def test_stamp_keeps_a_checkout_of_the_same_project():
    db = _stamp(
        {},
        {"project_id": 7, "project_name": "Demo Game", "project_path": "/d", "checkout_uid": "wt71"},
        {"wt71": 7},
    )
    db.set_session_project.assert_called_once_with(
        1576, 1, project_id=7, project_name="Demo Game", project_path="/d", checkout_uid="wt71",
    )
