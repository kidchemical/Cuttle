"""Scoped settings migration and persistence, always using isolated paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

from managers.settings_manager import SettingsManager
from managers.settings_storage import SettingsStorage, read_json, migrate_settings
from core.runtime_data import migrate_when_stopped, migrate


def store(root):
    return SettingsStorage(root / "src/settings.json",
                           machine=root / "src/data/config/machine_settings.json",
                           ui=root / "src/data/config/ui_state.json")


def seed(root, value):
    path = root / "src/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_missing_reads_create_nothing(tmp_path):
    manager = SettingsManager(str(tmp_path / "settings.json"))
    assert manager.get_setting("discovery")["lan_access_enabled"] is False
    assert list(tmp_path.iterdir()) == []


def test_split_preserves_values_and_unknown_keys(tmp_path):
    seed(tmp_path, {"version": "1.0.0", "default_pipeline": "Old",
                    "sandbox": {"enabled": True}, "user_preferences": {},
                    "channels": {"webchat": {"allowFrom": ["owner"]}, "discord": {}},
                    "device_workers": {"ssh_host": "custom", "token": "test-secret"},
                    "discovery": {"lan_access_enabled": True},
                    "ui_layout": {"rail_items": ["nav-chat"]},
                    "shell_workspaces": {"version": 1, "items": [{"id": "mine"}]},
                    "agent_router": {"custom": 2}, "unknown_extension": 7})
    before = store(tmp_path).read()
    migrate_settings(tmp_path)
    storage = store(tmp_path)
    server, machine, ui = map(read_json, (storage.server, storage.machine, storage.ui))
    assert server == {"schema_version": 1, "channels": {"webchat": {"allowFrom": ["owner"]}},
                      "agent_router": {"custom": 2}, "unknown_extension": 7}
    assert machine["device_workers"] == {"ssh_host": "custom"}
    assert machine["discovery"] == before["discovery"]
    assert ui["shell_workspaces"] == before["shell_workspaces"]
    secret = tmp_path / ".cuttle/personal/secrets/worker_shared_token.json"
    assert read_json(secret) == {"token": "test-secret"}
    if os.name != "nt":
        assert secret.stat().st_mode & 0o777 == 0o600
    assert migrate_settings(tmp_path) == []
    assert "device_workers" not in server and "ui_layout" not in server
    storage.update("ui_layout", lambda _: {"rail_items": ["nav-apps"]})
    storage.update("device_workers", lambda v: {**v, "ssh_host": "new"})
    assert read_json(storage.server) == server
    assert read_json(storage.machine)["device_workers"]["ssh_host"] == "new"
    assert storage.read()["ui_layout"] == {"rail_items": ["nav-apps"]}


def test_live_legacy_writes_do_not_split(tmp_path):
    seed(tmp_path, {"version": "1.0.0", "device_workers": {"ssh_host": "legacy"}, "unknown": 9})
    storage = store(tmp_path)
    storage.update("device_workers", lambda v: {**v, "enabled": False})
    assert not storage.machine.exists() and not storage.ui.exists()
    assert "schema_version" not in read_json(storage.server)
    assert read_json(storage.server)["unknown"] == 9


def test_empty_legacy_file_stays_monolithic_until_migration(tmp_path):
    seed(tmp_path, {})
    storage = store(tmp_path)
    storage.update("device_workers", lambda _: {"enabled": False})
    assert read_json(storage.server) == {"device_workers": {"enabled": False}}
    assert not storage.machine.exists()
    migrate_settings(tmp_path)
    assert read_json(storage.machine)["device_workers"]["enabled"] is False


def test_cold_start_guard_includes_settings_only_migration(tmp_path, monkeypatch):
    from core import runtime_data
    path = seed(tmp_path, {"version": "1.0.0"})
    before = path.read_bytes()
    monkeypatch.setattr(runtime_data, "active_processes", lambda _: [123])
    with pytest.raises(RuntimeError, match="123"):
        migrate_when_stopped(tmp_path)
    assert path.read_bytes() == before
    monkeypatch.setattr(runtime_data, "active_processes", lambda _: [])
    assert migrate_when_stopped(tmp_path)
    assert migrate_when_stopped(tmp_path) == []


def test_conflict_preserves_all_legacy_paths(tmp_path):
    server = seed(tmp_path, {"version": "1.0.0", "device_workers": {"enabled": False}})
    before = server.read_bytes()
    machine = store(tmp_path).machine
    machine.parent.mkdir(parents=True)
    machine.write_text('{"custom":1}', encoding="utf-8")
    old = tmp_path / "src/data/workspace/harness_last_agent_map.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        migrate(tmp_path)
    assert server.read_bytes() == before and old.exists()
    assert machine.read_text(encoding="utf-8") == '{"custom":1}'


def test_interrupted_split_rolls_back_new_files(tmp_path, monkeypatch):
    from managers import settings_storage
    server = seed(tmp_path, {"version": "1.0.0", "device_workers": {"token": "keep"}})
    before = server.read_bytes()
    real_write = settings_storage.atomic_write

    def fail_server(path, value):
        if path == server:
            raise OSError("blocked")
        real_write(path, value)

    monkeypatch.setattr(settings_storage, "atomic_write", fail_server)
    with pytest.raises(OSError):
        migrate_settings(tmp_path)
    assert server.read_bytes() == before
    assert not store(tmp_path).machine.exists() and not store(tmp_path).ui.exists()
    assert not (tmp_path / ".cuttle/personal/secrets/worker_shared_token.json").exists()


def test_corrupt_file_cannot_be_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(str(path))
    path.write_text("{broken", encoding="utf-8")
    assert manager.set_setting("starred_project", None) is False
    assert path.read_text(encoding="utf-8") == "{broken"


def test_stale_managers_preserve_other_updates_and_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"extension":42}', encoding="utf-8")
    first, second = SettingsManager(str(path)), SettingsManager(str(path))
    assert first.set_setting("starred_slash_commands", ["/cursor "])
    assert second.set_setting("starred_project", {"id": 2})
    assert first.get_setting("starred_project") == {"id": 2}
    assert read_json(path)["extension"] == 42
    value = first.get_setting("starred_project")
    value["id"] = 3
    assert first.get_setting("starred_project") == {"id": 2}


def test_process_updates_are_serialized(tmp_path):
    path = tmp_path / "settings.json"
    source = Path(__file__).resolve().parents[2]
    script = """
import sys
from managers.settings_manager import SettingsManager
m = SettingsManager(sys.argv[1])
for _ in range(30):
    assert m.update_setting('counter', lambda n: (n or 0) + 1)
"""
    env = {**os.environ, "PYTHONPATH": str(source)}
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    assert read_json(path)["counter"] == 120


def test_action_secret_moves_without_rotation(tmp_path):
    from core.runtime_paths import action_hmac_secret_path, secrets_dir
    old = tmp_path / "src/data/db/action_hmac_secret"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"test-signing-key")
    assert action_hmac_secret_path(tmp_path) == old
    migrate(tmp_path)
    new = secrets_dir(tmp_path) / "action_hmac_secret"
    assert action_hmac_secret_path(tmp_path) == new
    assert new.read_bytes() == b"test-signing-key" and not old.exists()


@pytest.mark.parametrize("version", [2, True, "1"])
def test_unsupported_schema_cannot_be_overwritten(tmp_path, version):
    path = tmp_path / "settings.json"
    manager = SettingsManager(str(path))
    path.write_text(json.dumps({"schema_version": version}), encoding="utf-8")
    before = path.read_bytes()
    assert not manager.set_setting("new", 1)
    assert path.read_bytes() == before


def test_scoped_files_preserve_unknown_keys_without_overriding_server(tmp_path):
    seed(tmp_path, {"schema_version": 1, "agent_router": {"owner": "server"}})
    storage = store(tmp_path)
    storage.machine.parent.mkdir(parents=True)
    storage.machine.write_text('{"extension":7,"agent_router":{"owner":"machine"}}', encoding="utf-8")
    storage.update("device_workers", lambda _: {"enabled": False})
    assert read_json(storage.machine)["extension"] == 7
    assert storage.read()["agent_router"] == {"owner": "server"}


def test_shared_token_cannot_be_written_to_new_machine_config(tmp_path):
    storage = store(tmp_path)
    with pytest.raises(ValueError, match="secrets directory"):
        storage.update("device_workers", lambda _: {"token": "do-not-persist"})
    assert not storage.machine.exists()


def test_production_paths_follow_checkout_and_survive_migration(tmp_path):
    """Copied production depth proves routing independent of cwd and live state."""
    source = Path(__file__).resolve().parents[2]
    for package, names in {
        "managers": ("__init__.py", "settings_manager.py", "settings_storage.py"),
        "core": ("__init__.py", "runtime_paths.py"),
    }.items():
        folder = tmp_path / "src" / package
        folder.mkdir(parents=True)
        for name in names:
            shutil.copyfile(source / package / name, folder / name)
    seed(tmp_path, {"version": "1.0.0", "device_workers": {"ssh_host": "keep"},
                    "ui_layout": {"rail_items": ["nav-chat"]}, "extension": 42})
    script = """
from pathlib import Path
from managers.settings_manager import SettingsManager
from managers.settings_storage import migrate_settings
m = SettingsManager()
assert m.get_setting('device_workers')['ssh_host'] == 'keep'
root = m.settings_file.parent.parent
migrate_settings(root)
assert m.set_setting('device_workers', {'ssh_host': 'keep', 'enabled': False})
assert m.get_setting('ui_layout')['rail_items'] == ['nav-chat']
assert m.get_setting('extension') == 42
assert m.storage.machine == root / 'src/data/config/machine_settings.json'
assert m.storage.ui == root / 'src/data/config/ui_state.json'
"""
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
    for cwd in (tmp_path, tmp_path / "src"):
        result = subprocess.run([sys.executable, "-c", script], cwd=cwd, env=env,
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode()
    assert read_json(tmp_path / "src/settings.json") == {"schema_version": 1, "extension": 42}
    assert read_json(tmp_path / "src/data/config/machine_settings.json")["device_workers"]["enabled"] is False
