"""Scoped settings persistence, always using isolated paths."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from managers.settings_manager import SettingsManager
from managers.settings_storage import SettingsStorage, read_json


def store(root):
    return SettingsStorage(root / "config/settings.json",
                           machine=root / "config/machine_settings.json",
                           ui=root / "config/ui_state.json")


def seed(root, value):
    path = root / "config/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_missing_reads_create_nothing(tmp_path):
    manager = SettingsManager(str(tmp_path / "settings.json"))
    assert manager.get_setting("discovery")["lan_access_enabled"] is False
    assert list(tmp_path.iterdir()) == []








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
    storage.machine.write_text('{"extension":7,"agent_router":{"owner":"machine"}}', encoding="utf-8")
    storage.update("device_workers", lambda _: {"enabled": False})
    assert read_json(storage.machine)["extension"] == 7
    assert storage.read()["agent_router"] == {"owner": "server"}


def test_shared_token_cannot_be_written_to_new_machine_config(tmp_path):
    storage = store(tmp_path)
    with pytest.raises(ValueError, match="secrets directory"):
        storage.update("device_workers", lambda _: {"token": "do-not-persist"})
    assert not storage.machine.exists()


def test_production_settings_live_in_the_cuttle_home_from_any_cwd(tmp_path):
    """The install tree is never written: settings resolve through CUTTLE_HOME."""
    source = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    seed(home, {"schema_version": 1, "extension": 42})
    script = """
import os
from pathlib import Path
from managers.settings_manager import SettingsManager
m = SettingsManager()
home = Path(os.environ['CUTTLE_HOME'])
assert m.settings_file == home / 'config/settings.json'
assert m.storage.machine == home / 'config/machine_settings.json'
assert m.storage.ui == home / 'config/ui_state.json'
assert m.get_setting('extension') == 42
assert m.set_setting('device_workers', {'ssh_host': 'keep', 'enabled': False})
"""
    env = {**os.environ, "PYTHONPATH": str(source), "CUTTLE_HOME": str(home)}
    for cwd in (tmp_path, source):
        result = subprocess.run([sys.executable, "-c", script], cwd=cwd, env=env,
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode()
    assert read_json(home / "config/settings.json") == {"schema_version": 1, "extension": 42}
    assert read_json(home / "config/machine_settings.json")["device_workers"]["enabled"] is False



@pytest.mark.parametrize("platform,failures", [("nt", 2), ("nt", 10_000), ("posix", 1)])
def test_sharing_retry_is_bounded_and_windows_only(monkeypatch, platform, failures):
    from types import SimpleNamespace
    from managers import settings_storage
    monkeypatch.setattr(settings_storage, "os", SimpleNamespace(name=platform))
    delays = []
    monkeypatch.setattr(settings_storage.time, "sleep", delays.append)
    attempts = []

    def operation():
        attempts.append(1)
        if len(attempts) <= failures:
            raise PermissionError("sharing violation")
        return "read or replaced"

    if platform == "nt" and failures < 100:
        assert settings_storage._retry_sharing(operation) == "read or replaced"
        assert len(attempts) == failures + 1
        assert delays and delays == sorted(delays)  # backs off, never shrinks
    else:
        with pytest.raises(PermissionError, match="sharing violation"):
            settings_storage._retry_sharing(operation)
        if platform == "nt":
            # Bounded: gives up after ~10 s of waiting, with capped steps.
            assert 9.0 <= sum(delays) <= 10.5 and max(delays) <= 0.25
        else:
            assert len(attempts) == 1 and delays == []


def test_sharing_retry_does_not_hide_other_errors(monkeypatch):
    from types import SimpleNamespace
    from managers import settings_storage
    monkeypatch.setattr(settings_storage, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(settings_storage.time, "sleep", lambda _: pytest.fail("unexpected retry"))

    def operation():
        raise OSError("disk error")

    with pytest.raises(OSError, match="disk error"):
        settings_storage._retry_sharing(operation)
