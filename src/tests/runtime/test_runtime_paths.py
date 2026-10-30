"""POSIX/Windows venv + Electron path helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from core.runtime_paths import (
    desktop_state_dir,
    electron_launch_argv,
    is_windows,
    personal_sibling_project_paths,
    rewrite_windows_cuttle_path,
    rewrite_windows_lab_path,
    venv_bin_dir,
    venv_python,
    worktree_path,
    worktrees_dir,
)

REPO = Path(__file__).resolve().parents[3]


def test_venv_python_points_at_existing_interpreter():
    py = venv_python(REPO)
    assert py.is_file()
    if is_windows():
        assert py.name.lower() in ("python.exe", "pythonw.exe")
    else:
        assert py.name in ("python3", "python") or py.name.startswith("python")


def test_venv_bin_dir_matches_platform():
    bindir = venv_bin_dir(REPO)
    if is_windows():
        assert bindir.name == "Scripts"
    else:
        assert bindir.name == "bin"


def test_rewrite_windows_cuttle_path_on_posix():
    if is_windows():
        return
    mapped = rewrite_windows_cuttle_path(r"C:\Projects\Cuttle\src", REPO)
    assert mapped == str((REPO / "src").resolve())
    assert rewrite_windows_cuttle_path("C:/Projects/Cuttle", REPO) == str(REPO.resolve())


def test_rewrite_windows_lab_path_uses_personal_mappings(tmp_path, monkeypatch):
    if is_windows():
        return
    root = tmp_path / "drive" / "Projects"
    (root / "DemoGame").mkdir(parents=True)
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / "personal"
    d.mkdir(parents=True)
    (d / "path-aliases.json").write_text(
        json.dumps({"path_mappings": {"E:/Projects": [str(tmp_path / "missing"), str(root)]}}),
        encoding="utf-8",
    )
    mapped = rewrite_windows_lab_path(r"E:\Projects\DemoGame", tmp_path)
    assert Path(mapped) == (root / "DemoGame").resolve()
    # No mapping in a fresh home: foreign paths are left alone.
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "fresh"))
    raw = r"E:\Projects\DemoGame"
    assert rewrite_windows_lab_path(raw, tmp_path / "fresh") == raw


def test_rewrite_windows_cuttle_path_ignores_cuttleworkspaces():
    if is_windows():
        return
    raw = r"E:\CuttleWorkspaces\demo-game\source"
    assert rewrite_windows_cuttle_path(raw, REPO) == raw


def test_rewrite_windows_cuttle_path_env_prefix(monkeypatch):
    if is_windows():
        return
    monkeypatch.setenv("CUTTLE_WINDOWS_PREFIXES", "Z:/OldBot")
    mapped = rewrite_windows_cuttle_path(r"Z:\OldBot\src", REPO)
    assert mapped == str((REPO / "src").resolve())


def test_personal_sibling_project_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    d = tmp_path / "personal"
    d.mkdir(parents=True)
    (d / "path-aliases.json").write_text(
        json.dumps({"sibling_project_paths": [r"E:\Projects\DemoGame"]}),
        encoding="utf-8",
    )
    assert personal_sibling_project_paths() == [r"E:\Projects\DemoGame"]
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "missing"))
    assert personal_sibling_project_paths() == []


def test_electron_launch_argv_is_none_or_existing():
    argv = electron_launch_argv(REPO)
    if argv is None:
        return
    assert Path(argv[0]).is_file()
    paths = [a for a in argv[1:] if not a.startswith("--")]
    if sys.platform != "win32" and paths:
        assert Path(paths[-1]).is_dir() or Path(paths[-1]).is_file()


def test_electron_launch_argv_opens_the_host_profile(tmp_path, monkeypatch):
    """The daemon's window is the Host UI; without --mode=host Electron restores
    a saved Client target from the shared default profile."""
    import core.runtime_paths as rp

    exe = tmp_path / "Cuttle.exe"
    exe.write_text("")
    monkeypatch.setattr(rp, "electron_packaged_exe", lambda _root: exe)
    assert rp.electron_launch_argv(tmp_path)[-1] == "--mode=host"

    dev = tmp_path / "electron.exe"
    dev.write_text("")
    monkeypatch.setattr(rp, "electron_packaged_exe", lambda _root: None)
    monkeypatch.setattr(rp, "electron_dev_bin", lambda _root: dev)
    argv = rp.electron_launch_argv(tmp_path)
    assert argv[-2:] == [str(tmp_path / "electron"), "--mode=host"]


def test_windows_launcher_runs_the_daemon_in_one_console():
    """`start` spawned a second console for the Host shortcut; Daemon-only must not open the app."""
    text = (REPO / ".cuttle" / "scripts" / "start-cuttle.cmd").read_text(encoding="utf-8")
    host = text.split(":START_HOST", 1)[1].split(":START_DAEMON", 1)[0]
    daemon = text.split(":START_DAEMON", 1)[1].split(":MAKE_SHORTCUT", 1)[0]
    assert '"%VENV_PY%" "%DAEMON%"' in host and "start " not in host.lower()
    assert '"%VENV_PY%" "%DAEMON%" --no-ui' in daemon and "start " not in daemon.lower()


def test_desktop_state_dir_is_named_cuttle_desktop():
    path = desktop_state_dir()
    assert path.name == "cuttle-desktop"


def test_worktrees_dir_defaults_under_cuttle_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    assert worktrees_dir() == tmp_path / "home" / "worktrees"
    # Branch-independent: identity is the worktree id, not a branch name.
    assert worktree_path("myrepo", "abc123") == (
        tmp_path / "home" / "worktrees" / "myrepo" / "abc123"
    )


def test_rewrite_leaves_windows_cuttle_home_alone():
    if is_windows():
        return
    home_state = r"C:\Users\dev\AppData\Local\Cuttle\config\settings.json"
    assert rewrite_windows_cuttle_path(home_state, REPO) == home_state
    worktree = r"C:\Users\dev\AppData\Local\Cuttle\worktrees\myrepo\abc123"
    assert rewrite_windows_cuttle_path(worktree, REPO) == worktree
    # A real checkout on the same drive still maps onto this machine's root.
    assert rewrite_windows_cuttle_path(r"C:\Projects\Cuttle\src", REPO) == str((REPO / "src").resolve())


def test_rewrite_protected_prefixes_skip_configured_root(tmp_path, monkeypatch):
    if is_windows():
        return
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    worktree = r"D:\worktrees\Cuttle\abc123"
    # Without protection the "Cuttle" segment would rewrite it onto REPO.
    assert rewrite_windows_cuttle_path(worktree, REPO, protected_prefixes=(r"D:\worktrees",)) == worktree
