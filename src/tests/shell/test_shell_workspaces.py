"""Save / load shell workspaces (split panes + open chats)."""
from pathlib import Path

import pytest


@pytest.fixture
def workspace_settings(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    return sm


def test_normalize_workspace_columns_keeps_chat_query(workspace_settings):
    from api import web_chat_api as w

    cols = w._normalize_shell_workspace_columns([
        {"page": "/chat_page.html?chat=177", "flex": "1 1 0%", "chat": "177"},
        {"page": "/settings_page.html", "flex": "1 1 0%", "chat": "42"},
        {"page": "javascript:alert(1)"},
        {"page": "http://evil.example/chat_page.html"},
    ])
    assert cols[0]["page"] == "/chat_page.html?chat=177"
    assert cols[0]["chat"] == "177"
    assert cols[1]["page"] == "/settings_page.html"
    assert cols[1]["chat"] == "42"
    assert all(c["page"].startswith("/") for c in cols)
    assert all("://" not in c["page"] for c in cols)


def test_workspace_summary_uses_chat_handle(workspace_settings):
    from api import web_chat_api as w

    assert "CH-000177" in w._workspace_column_summary({"page": "/chat_page.html?chat=177"})
    assert "Settings" in w._workspace_column_summary({"page": "/settings_page.html"})


def test_workspace_api_save_list_replace_delete(workspace_settings, owner_session):
    from api import web_chat_api as w

    client = w.app.test_client()
    assert client.get("/api/shell/workspaces").status_code == 401
    owner_session.sign_in(client)
    empty = client.get("/api/shell/workspaces")
    assert empty.status_code == 200
    assert empty.get_json()["workspaces"] == []

    payload = {
        "name": "Coding",
        "columns": [
            {"page": "/chat_page.html?chat=10", "flex": "1.2 1 0%"},
            {"page": "/chat_page.html?chat=20", "flex": "0.8 1 0%"},
        ],
    }
    saved = client.post("/api/shell/workspaces", json=payload)
    assert saved.status_code == 200
    body = saved.get_json()
    assert body["success"] is True
    ws = body["workspace"]
    assert ws["name"] == "Coding"
    assert ws["pane_count"] == 2
    assert "CH-000010" in ws["summary"]
    assert "CH-000020" in ws["summary"]
    wid = ws["id"]

    listed = client.get("/api/shell/workspaces").get_json()["workspaces"]
    assert len(listed) == 1
    assert listed[0]["id"] == wid

    # Same name replaces rather than duplicating
    again = client.post("/api/shell/workspaces", json={
        "name": "coding",
        "columns": [{"page": "/chat_page.html?chat=99", "flex": ""}],
    })
    assert again.status_code == 200
    listed = again.get_json()["workspaces"]
    assert len(listed) == 1
    assert listed[0]["id"] == wid
    assert listed[0]["pane_count"] == 1
    assert "CH-000099" in listed[0]["summary"]

    deleted = client.delete(f"/api/shell/workspaces/{wid}")
    assert deleted.status_code == 200
    assert deleted.get_json()["workspaces"] == []

    missing = client.delete(f"/api/shell/workspaces/{wid}")
    assert missing.status_code == 404


def test_workspace_api_round_trips_spaces_and_groups(workspace_settings, owner_session):
    from api import web_chat_api as w

    client = w.app.test_client()
    owner_session.sign_in(client)
    spaces = {
        "active": "sp_b",
        "groups": [{"id": "sg_1", "name": "Work", "color": "#c58af9", "collapsed": False}],
        "spaces": [
            {"id": "sp_a", "name": "Docs", "groupId": "sg_1",
             "root": {"type": "leaf", "page": "/chat_page.html?chat=5", "chat": "5"}},
            {"id": "sp_b", "name": "Code", "groupId": "sg_1",
             "root": {"type": "leaf", "page": "/chat_page.html?chat=6", "chat": "6"}},
            {"id": "sp_c", "name": "Loose", "root": None},
        ],
    }
    saved = client.post("/api/shell/workspaces", json={
        "name": "Laptop",
        "columns": [{"page": "/chat_page.html?chat=6", "flex": ""}],
        "spaces": spaces,
    }).get_json()
    assert saved["workspace"]["spaces"] == spaces
    assert saved["workspace"]["summary"].startswith("3 spaces · ")

    listed = client.get("/api/shell/workspaces").get_json()["workspaces"]
    assert listed[0]["spaces"] == spaces

    # Re-saving without spaces (or with junk) drops the stale snapshot.
    again = client.post("/api/shell/workspaces", json={
        "name": "Laptop",
        "columns": [{"page": "/chat_page.html", "flex": ""}],
        "spaces": {"spaces": []},
    }).get_json()
    assert "spaces" not in again["workspace"]
