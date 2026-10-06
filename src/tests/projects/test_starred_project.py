"""Exclusive starred default project (new Cuttle chats)."""

from __future__ import annotations

from pathlib import Path

from api import starred_project as sp


def test_normalize_and_starred_roundtrip(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)

    saved = sp.set_starred_project(
        {"id": 2, "path": r"E:\Projects\DemoGame", "name": "Demo Game"}
    )
    assert saved == {
        "id": 2,
        "path": "E:/Projects/DemoGame",
        "name": "Demo Game",
    }
    assert sp.get_starred_project() == saved

    # Exclusive: second star replaces the first.
    replaced = sp.set_starred_project(
        {"id": 1, "path": "C:/Projects/Cuttle", "name": "Cuttle"}
    )
    assert replaced["path"] == "C:/Projects/Cuttle"
    assert sp.get_starred_project()["id"] == 1

    assert sp.set_starred_project(None) is None
    assert sp.get_starred_project() is None


def test_set_from_path_string(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)

    saved = sp.set_starred_project("C:/Projects/Cuttle/")
    assert saved["path"] == "C:/Projects/Cuttle"
    assert saved["name"] == "Cuttle"
    assert saved["id"] is None


def test_list_keeps_only_first(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)

    saved = sp.set_starred_project(
        [
            {"path": "C:/Projects/Cuttle", "name": "Cuttle"},
            {"path": "E:/Projects/DemoGame", "name": "Demo Game"},
        ]
    )
    assert saved["path"] == "C:/Projects/Cuttle"
