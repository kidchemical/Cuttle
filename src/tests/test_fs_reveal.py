"""Tests for chat file-chip reveal-in-Explorer helper."""
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
