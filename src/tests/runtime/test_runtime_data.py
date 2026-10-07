"""Install-tree state moves into the Cuttle home once, all or nothing."""
from pathlib import Path

import pytest

from core.runtime_data import migrate, migration_pairs, refuse_unmigrated


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_every_install_location_lands_in_the_home(tmp_path):
    root, home = tmp_path / "checkout", tmp_path / "home"
    _write(root / "src/data/db/cuttle_auth.db", b"chats")
    _write(root / "src/data/sessions/codex_cli_session_map.json", b"{}")
    _write(root / "src/settings.json", b'{"schema_version": 1}')
    _write(root / "src/settings.json.lock")
    _write(root / "src/output/uploads/7/shot.png", b"png")
    _write(root / "src/web/logs/query_data_1.json", b"{}")
    _write(root / "src/.env", b"OPENAI_API_KEY=k")
    _write(root / ".cuttle/personal/secrets/localhost.pem", b"pem")
    _write(root / ".cuttle/personal/path-aliases.json", b"{}")
    _write(root / ".cuttle/personal/learnings/LEARNINGS.md", b"stays")
    _write(root / ".cuttle_global/personal/docs/discord.md", b"delta")

    migrate(root, home)

    assert (home / "db/cuttle_auth.db").read_bytes() == b"chats"
    assert (home / "sessions/codex_cli_session_map.json").is_file()
    assert (home / "config/settings.json").read_bytes() == b'{"schema_version": 1}'
    assert (home / "output/uploads/7/shot.png").read_bytes() == b"png"
    assert (home / "logs/queries/query_data_1.json").is_file()
    assert (home / ".env").read_bytes() == b"OPENAI_API_KEY=k"
    assert (home / "secrets/localhost.pem").read_bytes() == b"pem"
    assert (home / "personal/path-aliases.json").is_file()
    assert (home / "personal/docs/discord.md").read_bytes() == b"delta"
    for gone in ("src/data", "src/settings.json", "src/settings.json.lock", "src/output",
                 "src/web/logs", "src/.env", ".cuttle/personal/secrets", ".cuttle_global/personal"):
        assert not (root / gone).exists(), gone
    # The project overlay is the project's, not the install's.
    assert (root / ".cuttle/personal/learnings/LEARNINGS.md").read_bytes() == b"stays"
    assert migration_pairs(root, home) == []


def test_merges_into_existing_empty_owner_folders_with_sidecars(tmp_path):
    root, home = tmp_path / "checkout", tmp_path / "home"
    (home / "db").mkdir(parents=True)  # created by an import-time data_db_dir()
    for name in ("router_outcomes.db", "router_outcomes.db-wal", "router_outcomes.db-shm"):
        _write(root / "src/data/db" / name, name.encode())
    migrate(root, home)
    for name in ("router_outcomes.db", "router_outcomes.db-wal", "router_outcomes.db-shm"):
        assert (home / "db" / name).read_bytes() == name.encode()


def test_empty_install_dirs_are_not_pending(tmp_path):
    root, home = tmp_path / "checkout", tmp_path / "home"
    (root / "src/data/db").mkdir(parents=True)
    (root / "src/output").mkdir(parents=True)
    assert migration_pairs(root, home) == []


def test_stray_home_files_are_set_aside_never_overwritten(tmp_path):
    """Harness CLIs run fresh code and may write the home before the host restarts."""
    root, home = tmp_path / "checkout", tmp_path / "home"
    _write(root / "src/data/db/gizmos.db", b"real")
    _write(root / "src/data/sessions/cursor_cli_session_map.json", b"old map")
    _write(home / "db/gizmos.db", b"stray")
    _write(home / "db/gizmos.db-wal", b"stray wal")  # must never pair with the real DB
    _write(home / "sessions/cursor_cli_session_map.json", b"new map")
    migrate(root, home)
    assert (home / "db/gizmos.db").read_bytes() == b"real"
    assert not (home / "db/gizmos.db-wal").exists()
    assert (home / "sessions/cursor_cli_session_map.json").read_bytes() == b"old map"
    aside = {p.name.split(".pre-migration-")[0] for p in home.rglob("*.pre-migration-*")}
    assert aside == {"cursor_cli_session_map.json", "gizmos.db", "gizmos.db-wal"}
    assert {p.read_bytes() for p in home.rglob("*.pre-migration-*")} == {
        b"stray", b"stray wal", b"new map"}


def test_failed_move_rolls_back_prior_moves(tmp_path, monkeypatch):
    import shutil

    root, home = tmp_path / "checkout", tmp_path / "home"
    first = _write(root / "src/data/db/a.db", b"first")
    second = _write(root / "src/data/db/b.db", b"second")
    original = shutil.move

    def fail_second(source, target):
        if Path(source) == second:
            raise PermissionError("blocked")
        return original(source, target)

    monkeypatch.setattr(shutil, "move", fail_second)
    _write(home / "db/a.db", b"stray")
    with pytest.raises(PermissionError):
        migrate(root, home)
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert (home / "db/a.db").read_bytes() == b"stray"
    assert not list(home.rglob("*.pre-migration-*"))


def test_flask_refuses_to_open_an_empty_home_beside_unmigrated_state(tmp_path, monkeypatch):
    _write(tmp_path / "src/data/db/cuttle_auth.db")
    monkeypatch.delenv("CUTTLE_HOME")
    with pytest.raises(SystemExit, match="Exit Cuttle from the tray"):
        refuse_unmigrated(tmp_path)
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    refuse_unmigrated(tmp_path)


def test_startup_skips_migration_when_another_host_is_active(tmp_path, monkeypatch):
    from core import runtime_data

    old = _write(tmp_path / "src/data/sessions/harness_last_agent_map.json", b"{}")
    monkeypatch.setattr(runtime_data, "active_processes", lambda root: [123])
    with pytest.raises(RuntimeError, match="123"):
        runtime_data.migrate_when_stopped(tmp_path)
    assert old.is_file()


@pytest.mark.parametrize("hidden_attribute", ["cmdline", "cwd"])
def test_migration_refuses_unreadable_process_attributes(tmp_path, monkeypatch, hidden_attribute):
    import psutil
    from core.runtime_data import migrate_when_stopped

    old = _write(tmp_path / "src/data/sessions/harness_last_agent_map.json", b"{}")

    class Process:
        pid = 123456789
        # Reproduce process_iter(attrs=...) swallowing this failure into None.
        info = {"cmdline": None if hidden_attribute == "cmdline" else
                ["python", "src/scripts/cuttle_shadow_app.py"], "cwd": None}

        def name(self):
            return "python.exe"

        def cmdline(self):
            if hidden_attribute == "cmdline":
                raise psutil.AccessDenied(self.pid)
            return self.info["cmdline"]

        def cwd(self):
            raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: iter([Process()]))
    with pytest.raises(RuntimeError, match="migration refused"):
        migrate_when_stopped(tmp_path)
    assert old.read_bytes() == b"{}"


def test_absolute_host_path_blocks_without_requiring_cwd(tmp_path, monkeypatch):
    import psutil
    from core.runtime_data import active_processes

    class Process:
        pid = 123456789

        def name(self):
            return "python"

        def cmdline(self):
            return ["python", str(tmp_path / "src/scripts/cuttle_daemon.py")]

        def cwd(self):
            raise AssertionError("Absolute host path already proves the host is active")

    monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: iter([Process()]))
    assert active_processes(tmp_path) == [123456789]


def test_default_home_adopts_old_log_folder_explicit_home_never_does(tmp_path, monkeypatch):
    from core import runtime_data

    root = tmp_path / "checkout"
    old_logs = _write(tmp_path / "cuttle_logs/flask.log", b"history").parent
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Tests, shadows and second checkouts set CUTTLE_HOME: ~/cuttle_logs stays put.
    assert migration_pairs(root) == []
    monkeypatch.delenv("CUTTLE_HOME")
    monkeypatch.setattr(runtime_data, "cuttle_home", lambda: tmp_path / "home")
    assert migration_pairs(root) == [(old_logs, tmp_path / "home/logs")]
    # Old logs alone never stop a host from booting.
    refuse_unmigrated(root)
    migrate(root)
    assert (tmp_path / "home/logs/flask.log").read_bytes() == b"history"
    assert not old_logs.exists()


def test_own_launcher_is_not_another_host(tmp_path, monkeypatch):
    """A Windows venv launcher repeats the daemon's command line one level up."""
    import os
    import psutil
    from core.runtime_data import active_processes
    monkeypatch.setattr("core.runtime_data.is_windows", lambda: True)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["python", str(tmp_path / "src/scripts/cuttle_daemon.py")]

        def name(self):
            return "python.exe"

        def parents(self):
            return [Process(424242)]

    monkeypatch.setattr(psutil, "Process", lambda pid=None: Process(os.getpid()))
    monkeypatch.setattr(psutil, "process_iter",
                        lambda *a, **k: iter([Process(os.getpid()), Process(424242), Process(515151)]))
    assert active_processes(tmp_path) == [515151]


def test_live_host_ancestor_is_not_excluded(tmp_path, monkeypatch):
    """An agent CLI launched by Flask must still refuse to move Flask's stores."""
    import os
    import psutil
    from core.runtime_data import active_processes

    monkeypatch.setattr("core.runtime_data.is_windows", lambda: True)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "python.exe"

        def cmdline(self):
            script = "runtime_data.py" if self.pid == os.getpid() else "web_chat_api.py"
            return ["python", str(tmp_path / "src/api" / script)]

        def parents(self):
            return [Process(424242)]

    monkeypatch.setattr(psutil, "Process", lambda pid=None: Process(os.getpid()))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Process(os.getpid()), Process(424242)]))
    assert active_processes(tmp_path) == [424242]


def test_unrelated_protected_system_process_does_not_block_migration(tmp_path, monkeypatch):
    import psutil
    from core.runtime_data import migrate_when_stopped

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    old = _write(tmp_path / "src/data/db/example.db", b"chats")

    class Process:
        pid = 4

        def name(self):
            return "System"

        def cmdline(self):
            raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Process()]))
    assert migrate_when_stopped(tmp_path)
    assert not old.exists()


@pytest.mark.parametrize("script", ["cuttle_daemon.py", "web_chat_api.py"])
def test_host_in_another_checkout_blocks_shared_home_migration(tmp_path, monkeypatch, script):
    import psutil
    from core.runtime_data import migrate_when_stopped

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    root = tmp_path / "checkout"
    old = _write(root / "src/data/db/example.db", b"chats")

    class Process:
        pid = 515151

        def name(self):
            return "python"

        def cmdline(self):
            return ["python", str(tmp_path / "another-checkout/src" / script)]

    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Process()]))
    with pytest.raises(RuntimeError, match="Cuttle is running"):
        migrate_when_stopped(root)
    assert old.read_bytes() == b"chats"


def test_default_home_adopts_old_debug_logs(tmp_path, monkeypatch):
    from core import runtime_data

    root, home = tmp_path / "checkout", tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime_data, "cuttle_home", lambda: home)
    old = _write(tmp_path / "cuttle_nav_debug.log", b"nav history")
    _write(tmp_path / "cuttle_net_debug.log", b"net history")
    assert migration_pairs(root) == []  # Explicit homes never adopt user logs.
    monkeypatch.delenv("CUTTLE_HOME")
    refuse_unmigrated(root)
    migrate(root)
    assert not old.exists()
    assert (home / "logs/cuttle_nav_debug.log").read_bytes() == b"nav history"
    assert (home / "logs/cuttle_net_debug.log").read_bytes() == b"net history"
