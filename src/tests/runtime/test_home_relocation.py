"""Storage moves use disposable homes and the production copy/verification path."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3

import pytest
from core import home_relocation as move


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    home, drive, install = (tmp_path / name for name in ("profile", "drive", "install"))
    for directory in (home / "db", drive, install):
        directory.mkdir(parents=True)
    with closing(sqlite3.connect(home / "db/cuttle_auth.db")) as conn:
        conn.execute("CREATE TABLE chats(body)")
        conn.execute("INSERT INTO chats VALUES ('kept')")
        conn.commit()
    (home / ".env").write_text("test fixture only", encoding="utf-8")
    (home / "db/events.sqlite-wal").write_bytes(b"sidecar")
    if os.name != "nt":
        (home / "history").symlink_to("db", target_is_directory=True)
    monkeypatch.setenv("CUTTLE_HOME", str(home))
    monkeypatch.delenv("CUTTLE_SHADOW_DATA", raising=False)
    monkeypatch.setattr(move, "active_processes", lambda root: [])
    return home, drive / "new home", install


def schedule(target, install):
    return move.configure({"destination": str(target)}, install)


def test_cold_move_and_move_again(dirs):
    home, target, install = dirs
    assert schedule(target, install)["pending"] == str(target)
    assert not target.exists()
    assert home == home.resolve()
    move.apply_at_startup(install)
    assert home.resolve() == target
    with closing(sqlite3.connect(home / "db/cuttle_auth.db")) as conn:
        assert conn.execute("SELECT body FROM chats").fetchone() == ("kept",)
    assert (target / "db/events.sqlite-wal").read_bytes() == b"sidecar"
    if os.name != "nt":
        assert (target / "history").readlink() == Path("db")
        assert target.stat().st_mode & 0o077 == 0
    assert move.location()["pending"] is None
    again = target.with_name("second")
    schedule(again, install)
    move.apply_at_startup(install)
    assert home.resolve() == again
    assert target.resolve() == again  # older absolute paths still work
    assert (home / ".env").read_text(encoding="utf-8") == "test fixture only"
    move.check_reader()
    # Missing drive must not reopen owner registration beside the real store.
    again.rename(target.with_name("offline"))
    with pytest.raises(RuntimeError, match="drive is unavailable"):
        move.apply_at_startup(install)


def test_validation_cancellation_and_active_host(dirs, monkeypatch):
    home, target, install = dirs
    for bad in (home, home / "nested", install / "nested", target.parent, "relative", target / "missing-parent"):
        with pytest.raises(ValueError):
            schedule(bad, install)
    schedule(target, install)
    monkeypatch.setattr(move, "active_processes", lambda root: [1234])
    with pytest.raises(RuntimeError, match="still running"):
        move.apply_at_startup(install)
    assert not target.exists()
    assert move.configure({"cancel": True}, install)["pending"] is None
    move.apply_at_startup(install)
    assert home.resolve() == home


def test_copy_failure_leaves_original_and_exposes_error(dirs, monkeypatch):
    home, target, install = dirs
    schedule(target, install)
    def fail(source, dest):
        (dest / "partial").touch()
        raise OSError("disk full")
    monkeypatch.setattr(move, "_copy_verified", fail)
    move.apply_at_startup(install)
    assert move.location()["error"] == "disk full"
    assert not target.exists()
    assert (home / ".env").is_file()


def test_destination_appeared_after_scheduling(dirs):
    home, target, install = dirs
    schedule(target, install)
    target.mkdir()
    (target / "unrelated").touch()
    move.apply_at_startup(install)
    assert "already exists" in move.location()["error"]
    assert (target / "unrelated").is_file()
    assert home.resolve() == home


def test_crash_between_renames_restores_original(dirs, monkeypatch):
    home, target, install = dirs
    schedule(target, install)
    rename = Path.rename
    def interrupt(path, dest):
        if dest == home and ".next-" in path.name:
            raise KeyboardInterrupt
        return rename(path, dest)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", interrupt)
        with pytest.raises(KeyboardInterrupt):
            move.apply_at_startup(install)
    assert not home.exists()
    with pytest.raises(RuntimeError, match="migration is in progress"):
        move.check_reader()
    move.apply_at_startup(install)
    assert home.resolve() == home
    assert (home / ".env").is_file()
    assert "interrupted" in move.location()["error"]


def test_crash_after_switch_retries_cleanup(dirs, monkeypatch):
    home, target, install = dirs
    schedule(target, install)
    with monkeypatch.context() as patch:
        def interrupt(*args):
            raise KeyboardInterrupt
        patch.setattr(move, "_finish", interrupt)
        with pytest.raises(KeyboardInterrupt):
            move.apply_at_startup(install)
    assert home.resolve() == target
    move.apply_at_startup(install)
    assert move.location()["error"] is None
    assert not list(home.parent.glob(".*.old-*"))

def test_open_database_blocks_copy(dirs):
    home, target, install = dirs
    schedule(target, install)
    with (home / "db/cuttle_auth.db").open("rb"):
        move.apply_at_startup(install)
    assert "still has" in move.location()["error"]
    assert home.resolve() == home and not target.exists()


def test_failed_cleanup_keeps_new_home_available(dirs, monkeypatch):
    home, target, install = dirs
    schedule(target, install)
    with monkeypatch.context() as patch:
        def sharing_violation(*args):
            raise PermissionError("in use")
        patch.setattr(move.shutil, "rmtree", sharing_violation)
        move.apply_at_startup(install)
    move.check_reader()
    assert home.resolve() == target
    assert "cleanup" in move.location()["error"]
    move.apply_at_startup(install)
    assert move.location()["error"] is None
