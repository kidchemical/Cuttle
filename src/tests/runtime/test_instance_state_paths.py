"""Runtime files live in the per-user instance state dir, never the checkout root."""
import importlib
import json

from core import runtime_paths
from core.runtime_paths import (
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
