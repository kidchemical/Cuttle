"""Desktop file-opening and file-manager reveal helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from api.fs_reveal import parse_local_path, reveal_in_file_manager, reveal_path_or_error


def test_parse_file_url_windows_drive():
    if sys.platform != "win32":
        pytest.skip("Windows path shape")
    p = parse_local_path("file:///C:/Projects/Cuttle/src/api/fs_reveal.py")
    assert p.replace("/", "\\").lower().startswith("c:\\projects\\cuttle")
    assert p.endswith("fs_reveal.py")


def test_parse_slash_prefixed_drive_path():
    """Muse/agents sometimes emit /g:/Dev/... inside markdown links."""
    if sys.platform != "win32":
        pytest.skip("Windows path shape")
    p = parse_local_path("/g:/Dev/Blender/Demo Intro Video/foo.blend")
    assert p.replace("/", "\\").lower().startswith("g:\\dev\\blender")
    assert p.endswith("foo.blend")


def test_parse_absolute_path():
    here = str(Path(__file__).resolve())
    assert parse_local_path(here) == os.path.normpath(here)


def test_parse_rejects_http():
    with pytest.raises(ValueError, match="file://"):
        parse_local_path("https://example.com/x")


def test_parse_rejects_relative():
    with pytest.raises(ValueError, match="absolute"):
        parse_local_path("src/api/fs_reveal.py")


def test_reveal_missing_path():
    result, err, status = reveal_path_or_error("file:///Z:/no/such/cuttle_reveal_probe_xyz")
    assert result is None
    assert status == 404
    assert err and "does not exist" in err


def test_reveal_calls_explorer(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hi", encoding="utf-8")
    with mock.patch("api.fs_reveal.subprocess.Popen") as popen:
        out = reveal_in_file_manager(str(f))
    assert out["path"]
    if sys.platform == "win32" or sys.platform == "darwin":
        assert out["selected"] is True
    else:
        assert out["selected"] is False
    popen.assert_called_once()
    args = popen.call_args[0][0]
    if sys.platform == "win32":
        assert args[0] == "explorer"
        assert args[1].startswith("/select,")
        assert "hello.txt" in args[1]
    elif sys.platform == "darwin":
        assert args[:2] == ["open", "-R"]
    else:
        assert args[0] == "xdg-open"
        assert out["selected"] is False


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Xwayland environment recovery')
def test_desktop_environment_recovers_live_matching_display(tmp_path, monkeypatch):
    """Use the running display's auth file, not another leftover file nearby."""
    from api import fs_reveal

    real_path = Path
    proc = tmp_path / 'proc'
    proc.mkdir()
    live = tmp_path / 'live-auth'
    live.touch()
    other = tmp_path / 'other-auth'
    other.touch()
    for pid, display, authority in [('101', ':1', other), ('102', ':0', live)]:
        entry = proc / pid
        entry.mkdir()
        entry.joinpath('cmdline').write_bytes(
            f'/usr/bin/Xwayland\0{display}\0-auth\0{authority}\0'.encode()
        )
    # Simulate the daemon retaining an authority file deleted by a desktop restart.
    monkeypatch.setenv('DISPLAY', ':0.0')
    monkeypatch.setenv('XAUTHORITY', str(tmp_path / 'deleted-auth'))
    monkeypatch.setenv('DESKTOP_SESSION', 'ubuntu')
    with mock.patch.object(fs_reveal, 'Path', side_effect=lambda p: proc if str(p) == '/proc' else real_path(p)):
        env = fs_reveal.desktop_launch_environment()
    assert env['XAUTHORITY'] == str(live)
    assert env['DESKTOP_SESSION'] == 'ubuntu'
    assert os.environ['XAUTHORITY'].endswith('deleted-auth')  # only fix the child's environment


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Xwayland environment recovery')
def test_desktop_environment_keeps_valid_auth_and_remote_display(tmp_path, monkeypatch):
    from api.fs_reveal import desktop_launch_environment

    valid = tmp_path / 'valid-auth'
    valid.touch()
    monkeypatch.setenv('DISPLAY', ':0')
    monkeypatch.setenv('XAUTHORITY', str(valid))
    assert desktop_launch_environment()['XAUTHORITY'] == str(valid)
    monkeypatch.setenv('DISPLAY', 'remote-host:10.0')
    monkeypatch.setenv('XAUTHORITY', str(tmp_path / 'missing-auth'))
    assert desktop_launch_environment()['XAUTHORITY'] == str(tmp_path / 'missing-auth')


@pytest.mark.parametrize('platform, command', [('linux', 'xdg-open'), ('darwin', 'open')])
def test_markdown_opener_uses_association_and_observes_result(tmp_path, monkeypatch, platform, command):
    from api import fs_reveal

    file = tmp_path / 'notes with spaces.md'
    file.write_text('# Notes', encoding="utf-8")
    monkeypatch.setattr(fs_reveal.sys, 'platform', platform)
    with mock.patch.object(fs_reveal, 'desktop_launch_environment', return_value={'DISPLAY': ':0', 'XAUTHORITY': '/live-auth'}), \
         mock.patch.object(fs_reveal.subprocess, 'Popen') as popen:
        proc = popen.return_value
        proc.communicate.return_value = ('', '')
        proc.returncode = 0
        result = fs_reveal.open_in_default_app(str(file))
        assert result['path'] == str(file.resolve())
        assert popen.call_args.args[0] == [command, str(file.resolve())]
        assert popen.call_args.kwargs['env']['XAUTHORITY'] == '/live-auth'
        assert 'shell' not in popen.call_args.kwargs
        proc.communicate.assert_called_once_with(timeout=2)


def test_failed_opener_surfaces_error(tmp_path, monkeypatch):
    from api import fs_reveal

    file = tmp_path / 'notes.md'
    file.write_text('# Notes', encoding="utf-8")
    monkeypatch.setattr(fs_reveal.sys, 'platform', 'linux')
    with mock.patch.object(fs_reveal.subprocess, 'Popen') as popen:
        popen.return_value.communicate.return_value = ('', 'Authorization required; cannot open display')
        popen.return_value.returncode = 4
        with pytest.raises(OSError, match='Authorization required'):
            fs_reveal.open_in_default_app(str(file))


def test_long_running_default_app_is_not_stopped(tmp_path, monkeypatch):
    from api import fs_reveal

    file = tmp_path / 'notes.md'
    file.write_text('# Notes', encoding="utf-8")
    monkeypatch.setattr(fs_reveal.sys, 'platform', 'linux')
    with mock.patch.object(fs_reveal.subprocess, 'Popen') as popen, \
         mock.patch.object(fs_reveal.threading, 'Thread') as thread:
        proc = popen.return_value
        proc.communicate.side_effect = fs_reveal.subprocess.TimeoutExpired('xdg-open', 2)
        result = fs_reveal.open_in_default_app(str(file))
        assert result['path'] == str(file.resolve())
        proc.kill.assert_not_called()
        proc.terminate.assert_not_called()
        thread.return_value.start.assert_called_once()


def test_default_opener_rejects_missing_file_before_launch(tmp_path):
    from api.fs_reveal import open_in_default_app

    with mock.patch('api.fs_reveal.subprocess.Popen') as popen:
        with pytest.raises(FileNotFoundError):
            open_in_default_app(str(tmp_path / 'missing.md'))
        popen.assert_not_called()


@pytest.mark.skipif(sys.platform == 'win32', reason='fakes a POSIX opener, which also switches off '
                    'drive-letter URL parsing; Windows: test_directory_opens_in_windows_explorer '
                    'and test_windows_file_url_with_encoded_spaces_parses_to_drive_path')
@pytest.mark.parametrize('platform, command', [('linux', 'xdg-open'), ('darwin', 'open')])
def test_directory_url_opens_in_file_manager(tmp_path, monkeypatch, platform, command):
    """A chat chip for a folder (file:// URL, %20 for spaces) opens the folder itself."""
    from api import fs_reveal

    folder = tmp_path / 'Game Dev' / 'fsr-ab-default'
    folder.mkdir(parents=True)
    monkeypatch.setattr(fs_reveal.sys, 'platform', platform)
    with mock.patch.object(fs_reveal.subprocess, 'Popen') as popen:
        popen.return_value.communicate.return_value = ('', '')
        popen.return_value.returncode = 0
        result = fs_reveal.open_in_default_app(folder.as_uri() + '/')
    assert '%20' in folder.as_uri()
    assert result['kind'] == 'directory'
    assert result['path'] == str(folder.resolve())
    assert popen.call_args.args[0] == [command, str(folder.resolve())]


def test_directory_opens_in_windows_explorer(tmp_path, monkeypatch):
    from api import fs_reveal

    folder = tmp_path / 'Game Dev' / 'fsr-ab-sharpen'
    folder.mkdir(parents=True)
    monkeypatch.setattr(fs_reveal.sys, 'platform', 'win32')
    startfile = mock.Mock()
    monkeypatch.setattr(fs_reveal.os, 'startfile', startfile, raising=False)
    with mock.patch.object(fs_reveal, 'parse_local_path', return_value=str(folder)), \
         mock.patch.object(fs_reveal.subprocess, 'Popen') as popen:
        result = fs_reveal.open_in_default_app('file:///C:/Game%20Dev/fsr-ab-sharpen')
    startfile.assert_called_once_with(str(folder.resolve()))
    popen.assert_not_called()
    assert result['kind'] == 'directory'


def test_windows_file_url_with_encoded_spaces_parses_to_drive_path(monkeypatch):
    """file:///C:/Game%20Dev/x → C:\\Game Dev\\x on Windows (checked with Windows path rules)."""
    import ntpath
    from api import fs_reveal

    monkeypatch.setattr(fs_reveal.sys, 'platform', 'win32')
    monkeypatch.setattr(fs_reveal.os, 'path', ntpath)
    monkeypatch.setattr(fs_reveal.os, 'sep', '\\')
    assert fs_reveal.parse_local_path('file:///C:/Game%20Dev/runs/fsr-ab-default/') == 'C:\\Game Dev\\runs\\fsr-ab-default'
