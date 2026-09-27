"""Blade-bar layout persistence for the Apps page (hidden apps + layout version)."""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api import web_chat_api as w

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(w, "get_settings_manager", lambda: sm, raising=False)
    return w.app.test_client()


def test_ui_layout_persists_rail_hidden_and_version(client):
    payload = {
        "rail_items": ["nav-chat", "nav-git", "nav-apps"],
        "rail_footer": ["nav-account", "nav-settings", "panelToggle"],
        "rail_hidden": ["nav-tasks", "nav-automation"],
        "layout_version": 3,
    }
    res = client.post("/api/settings/ui-layout", json=payload)
    assert res.status_code == 200

    layout = client.get("/api/settings/ui-layout").get_json()["ui_layout"]
    assert layout["rail_hidden"] == ["nav-tasks", "nav-automation"]
    assert layout["rail_items"] == ["nav-chat", "nav-git", "nav-apps"]
    assert layout["layout_version"] == 3


def test_ui_layout_pinning_an_app_clears_it_from_hidden(client):
    client.post("/api/settings/ui-layout", json={
        "rail_items": ["nav-chat", "nav-apps"],
        "rail_hidden": ["nav-tasks", "nav-automation"],
        "layout_version": 3,
    })
    client.post("/api/settings/ui-layout", json={
        "rail_items": ["nav-chat", "nav-apps", "nav-tasks"],
        "rail_hidden": ["nav-automation"],
        "layout_version": 3,
    })
    layout = client.get("/api/settings/ui-layout").get_json()["ui_layout"]
    assert layout["rail_hidden"] == ["nav-automation"]
    assert "nav-tasks" in layout["rail_items"]


def test_apps_page_is_served(client):
    res = client.get("/apps_page.html")
    assert res.status_code == 200
    assert b'id="appsGrid"' in res.data
