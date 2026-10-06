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


def test_rewrite_windows_lab_path_uses_personal_mappings(tmp_path):
    if is_windows():
        return
    root = tmp_path / "drive" / "Projects"
    (root / "DemoGame").mkdir(parents=True)
    d = tmp_path / ".cuttle" / "personal"
    d.mkdir(parents=True)
    (d / "path-aliases.json").write_text(
        json.dumps({"path_mappings": {"E:/Projects": [str(tmp_path / "missing"), str(root)]}}),
        encoding="utf-8",
    )
    mapped = rewrite_windows_lab_path(r"E:\Projects\DemoGame", tmp_path)
    assert Path(mapped) == (root / "DemoGame").resolve()
    # No mapping on a fresh clone: foreign paths are left alone.
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


def test_personal_sibling_project_paths(tmp_path):
    d = tmp_path / ".cuttle" / "personal"
    d.mkdir(parents=True)
    (d / "path-aliases.json").write_text(
        json.dumps({"sibling_project_paths": [r"E:\Projects\DemoGame"]}),
        encoding="utf-8",
    )
    assert personal_sibling_project_paths(tmp_path) == [r"E:\Projects\DemoGame"]
    assert personal_sibling_project_paths(tmp_path / "missing") == []


def test_electron_launch_argv_is_none_or_existing():
    argv = electron_launch_argv(REPO)
    if argv is None:
        return
    assert Path(argv[0]).is_file()
    if sys.platform != "win32" and len(argv) >= 2:
        assert Path(argv[1]).is_dir() or Path(argv[1]).is_file()


def test_desktop_state_dir_is_named_cuttle_desktop():
    path = desktop_state_dir()
    assert path.name == "cuttle-desktop"
