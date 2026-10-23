"""Child-process logs (flask.log) rotate between process starts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DAEMON = REPO / "src" / "scripts" / "cuttle_daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("cuttle_daemon_log_rotation_test", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_small_log_is_left_alone(tmp_path):
    d = _load_daemon()
    log = tmp_path / "flask.log"
    log.write_text("short")
    d._rotate_log(log, max_bytes=100, keep=3)
    assert log.read_text() == "short"
    assert not (tmp_path / "flask.log.1").exists()


def test_large_log_shifts_and_oldest_is_dropped(tmp_path):
    d = _load_daemon()
    log = tmp_path / "flask.log"
    for gen in ("a", "b", "c"):
        log.write_text(gen * 200)
        d._rotate_log(log, max_bytes=100, keep=2)
    assert not log.exists()
    assert (tmp_path / "flask.log.1").read_text() == "c" * 200
    assert (tmp_path / "flask.log.2").read_text() == "b" * 200
    assert not (tmp_path / "flask.log.3").exists()


def test_missing_log_is_a_no_op(tmp_path):
    d = _load_daemon()
    d._rotate_log(tmp_path / "flask.log", max_bytes=1, keep=3)
    assert list(tmp_path.iterdir()) == []
