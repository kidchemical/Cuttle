"""Runtime files live in the per-user instance state dir, never the checkout root."""
import importlib
import json
from pathlib import Path

import pytest

from core import runtime_paths
from core.runtime_paths import (
    cuttle_home,
    flask_restart_request_path,
    flask_restart_status_path,
    instance_state_dir,
    notify_queue_path,
    ui_toast_path,
    user_state_dir,
)


def test_user_state_dir_defaults_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    assert user_state_dir() == tmp_path / ".local" / "state" / "cuttle"


def test_user_state_dir_honors_xdg_and_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert user_state_dir() == tmp_path / "xdg" / "cuttle"
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert user_state_dir() == tmp_path / "local" / "Cuttle"


def test_instance_state_dir_live_unkeyed_shadow_scoped(tmp_path, monkeypatch):
    monkeypatch.delenv("CUTTLE_SHADOW_DATA", raising=False)
    assert instance_state_dir() == user_state_dir()
    shadow = tmp_path / "temp" / "shadows" / "abc123" / "data"
    monkeypatch.setenv("CUTTLE_SHADOW_DATA", str(shadow))
    assert instance_state_dir() == shadow / "state"


def test_leaf_paths_stay_under_instance_dir_and_create_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("CUTTLE_SHADOW_DATA", raising=False)
    base = instance_state_dir()
    assert notify_queue_path().parent == base
    assert ui_toast_path().parent == base
    assert flask_restart_status_path().parent == base
    assert flask_restart_request_path().parent == base
    assert not base.exists()


def test_notify_tray_writes_state_dir_never_repo_root(tmp_path, monkeypatch):
    from api import ui_notify

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CUTTLE_SHADOW_DATA", raising=False)
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: False)
    importlib.reload(ui_notify)
    try:
        ui_notify.notify_tray("hello", variant="success")
        assert ui_notify._NOTIFY_PATH.parent == tmp_path / "xdg" / "cuttle"
        entry = json.loads(ui_notify._NOTIFY_PATH.read_text(encoding="utf-8"))
        assert entry["message"] == "hello"
        assert ui_notify.pull_ui_toasts()[0]["variant"] == "success"
    finally:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        importlib.reload(ui_notify)


def test_cuttle_home_is_per_user_and_overridable(tmp_path, monkeypatch):
    """All mutable state resolves outside the install tree (read-only installs)."""
    monkeypatch.delenv("CUTTLE_HOME", raising=False)
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    assert cuttle_home() == tmp_path / ".local" / "share" / "cuttle"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert cuttle_home() == tmp_path / "xdg" / "cuttle"
    monkeypatch.setattr(runtime_paths, "is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert cuttle_home() == tmp_path / "local" / "Cuttle"
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "explicit"))
    assert cuttle_home() == tmp_path / "explicit"
    repo = Path(runtime_paths.__file__).resolve().parents[2]
    assert not runtime_paths.settings_path().is_relative_to(repo)


def test_relative_cuttle_home_is_rejected(monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", "src/data")
    with pytest.raises(ValueError, match="absolute path"):
        cuttle_home()


@pytest.mark.parametrize("channel,filename", [
    ("nav", "cuttle_nav_debug.log"),
    ("net", "cuttle_net_debug.log"),
])
def test_debug_log_writes_under_cuttle_home(tmp_path, monkeypatch, owner_session, channel, filename):
    from api.web_chat_api import app

    home = tmp_path / "home"
    monkeypatch.setenv("CUTTLE_HOME", str(home))
    client = owner_session.sign_in(app.test_client())
    response = client.post("/api/debug-log", json={"channel": channel, "msg": "debug event"})
    assert response.get_json() == {"ok": True}
    assert (home / "logs" / filename).read_text(encoding="utf-8") == "debug event\n"
