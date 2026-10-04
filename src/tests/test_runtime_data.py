"""Storage migration preserves state and refuses ambiguous destinations."""
from pathlib import Path

import pytest

from core.runtime_data import migrate
from core.runtime_paths import runtime_state_path


def test_legacy_remains_authoritative_until_migration(tmp_path):
    base = tmp_path / "src/data"
    old = base / "workspace/codex_cli_session_map.json"
    old.parent.mkdir(parents=True)
    old.write_bytes(b'{"resume":"native-thread"}')
    assert runtime_state_path("sessions", old.name, project_root=tmp_path,
                              legacy="workspace/" + old.name) == old
    moved = migrate(tmp_path)
    new = base / "sessions" / old.name
    assert (old, new) in moved
    assert new.read_bytes() == b'{"resume":"native-thread"}'
    assert not old.exists()
    assert runtime_state_path("sessions", old.name, project_root=tmp_path,
                              legacy="workspace/" + old.name) == new
    assert migrate(tmp_path) == []


def test_preflight_conflict_moves_nothing(tmp_path):
    base = tmp_path / "src/data"
    for relative in ("workspace/codex_cli_session_map.json", "sessions/codex_cli_session_map.json",
                     "home_automation_schedule.json"):
        p = base / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(relative)
    with pytest.raises(FileExistsError):
        migrate(tmp_path)
    assert (base / "home_automation_schedule.json").exists()
    assert (base / "workspace/codex_cli_session_map.json").exists()


def test_directory_migration_preserves_sidecars_and_unknown_files(tmp_path):
    base = tmp_path / "src/data"
    for name in ("edit_journal.sqlite3", "edit_journal.sqlite3-wal", "edit_journal.sqlite3-shm"):
        p = base / "workspace/edit_attribution" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode())
    unknown = base / "workspace/custom.json"
    unknown.write_text("keep")
    pack = base / "harness_agents/custom"
    pack.mkdir(parents=True)
    (pack / "adapter.py").write_text("# custom adapter")
    migrate(tmp_path)
    for name in ("edit_journal.sqlite3", "edit_journal.sqlite3-wal", "edit_journal.sqlite3-shm"):
        assert (base / "edit_attribution" / name).read_bytes() == name.encode()
    assert unknown.read_text() == "keep"
    assert (tmp_path / ".cuttle_global/personal/agents/custom/adapter.py").is_file()


def test_home_automation_legacy_generation_uses_one_lock_directory(tmp_path):
    from core.runtime_paths import home_automation_path
    base = tmp_path / "src/data"
    base.mkdir(parents=True)
    (base / "home_automation_schedule.json").write_text("{}")
    assert home_automation_path("govee_api_batch.lock", tmp_path) == base / "govee_api_batch.lock"
    assert home_automation_path("auto_state.json", tmp_path) == base / "home_automation_auto_state.json"
    migrate(tmp_path)
    assert home_automation_path("govee_api_batch.lock", tmp_path) == base / "home_automation/govee_api_batch.lock"


def test_startup_skips_migration_when_another_host_is_active(tmp_path, monkeypatch):
    from core import runtime_data
    old = tmp_path / "src/data/home_automation_schedule.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    monkeypatch.setattr(runtime_data, "active_processes", lambda root: [123])
    with pytest.raises(RuntimeError, match="123"):
        runtime_data.migrate_when_stopped(tmp_path)
    assert old.is_file()


def test_failed_move_rolls_back_prior_moves(tmp_path, monkeypatch):
    base = tmp_path / "src/data/workspace"
    base.mkdir(parents=True)
    first = base / "antigravity_cli_session_map.json"
    second = base / "codex_cli_session_map.json"
    first.write_text("first")
    second.write_text("second")
    original = Path.rename

    def fail_second(source, target):
        if source == second:
            raise PermissionError("blocked")
        return original(source, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(PermissionError):
        migrate(tmp_path)
    assert first.read_text() == "first"
    assert second.read_text() == "second"
    assert not (tmp_path / "src/data/sessions/antigravity_cli_session_map.json").exists()


@pytest.mark.parametrize("hidden_attribute", ["cmdline", "cwd"])
def test_migration_refuses_unreadable_process_attributes(tmp_path, monkeypatch, hidden_attribute):
    import psutil
    from core.runtime_data import migrate_when_stopped

    old = tmp_path / "src/data/home_automation_schedule.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")

    class Process:
        pid = 123456789
        # Reproduce process_iter(attrs=...) swallowing this failure into None.
        info = {"cmdline": None if hidden_attribute == "cmdline" else
                ["python", "src/scripts/cuttle_daemon.py"], "cwd": None}

        def cmdline(self):
            if hidden_attribute == "cmdline":
                raise psutil.AccessDenied(self.pid)
            return self.info["cmdline"]

        def cwd(self):
            raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: iter([Process()]))
    with pytest.raises(RuntimeError, match="migration refused"):
        migrate_when_stopped(tmp_path)
    assert old.read_text() == "{}"
    assert not (tmp_path / "src/data/home_automation/schedule.json").exists()


def test_absolute_host_path_blocks_without_requiring_cwd(tmp_path, monkeypatch):
    import psutil
    from core.runtime_data import active_processes

    class Process:
        pid = 123456789

        def cmdline(self):
            return ["python", str(tmp_path / "src/scripts/cuttle_daemon.py")]

        def cwd(self):
            raise AssertionError("Absolute host path already proves the host is active")

    monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: iter([Process()]))
    assert active_processes(tmp_path) == [123456789]
