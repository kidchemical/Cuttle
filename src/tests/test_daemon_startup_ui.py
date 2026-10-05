"""Daemon console coloring + Electron auto-open / no extra python console."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "src" / "scripts" / "cuttle_daemon.py"
ELECTRON_MAIN = REPO / "electron" / "main.js"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("cuttle_daemon_under_test", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_colorize_maps_severity():
    d = _load_daemon()
    err = d.colorize_daemon_line("[DAEMON] Flask restart FAILED (health timeout)")
    warn = d.colorize_daemon_line("[DAEMON] WARNING: port 8080 conflict")
    ok = d.colorize_daemon_line("[DAEMON] Flask is ready on https://127.0.0.1:8080")
    info = d.colorize_daemon_line("[DAEMON] Watching pipelines for hot-reload")
    assert "\033[91m" in err and err.endswith("\033[0m")
    assert "\033[93m" in warn
    assert "\033[92m" in ok
    assert "\033[96m" in info
    assert "[DAEMON]" in err
    # log file path: helper must not wrap already-colored text
    assert d.colorize_daemon_line(err) == err


def test_stamp_daemon_line_prefixes_time():
    d = _load_daemon()
    now = datetime(2026, 8, 19, 12, 4, 9)
    out = d.stamp_daemon_line("[DAEMON] Flask is ready", now=now)
    assert out == "[12:04:09] [DAEMON] Flask is ready"
    assert d.stamp_daemon_line(out, now=now) == out
    assert d.stamp_daemon_line("", now=now) == ""
    colored = d.colorize_daemon_line(out)
    assert "\033[92m" in colored
    assert "[12:04:09]" in colored


def _fake_desktop(monkeypatch, d, *, display: bool):
    monkeypatch.setattr(d.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if display:
        monkeypatch.setenv("DISPLAY", ":0")
    else:
        monkeypatch.delenv("DISPLAY", raising=False)


def test_should_open_ui_respects_env(monkeypatch):
    d = _load_daemon()
    _fake_desktop(monkeypatch, d, display=True)
    monkeypatch.delenv("CUTTLE_NO_UI", raising=False)
    assert d._should_open_ui() is True
    monkeypatch.setenv("CUTTLE_NO_UI", "1")
    assert d._should_open_ui() is False


def test_should_show_tray_respects_env(monkeypatch):
    d = _load_daemon()
    _fake_desktop(monkeypatch, d, display=True)
    monkeypatch.delenv("CUTTLE_NO_TRAY", raising=False)
    assert d._should_show_tray() is True
    monkeypatch.setenv("CUTTLE_NO_TRAY", "1")
    assert d._should_show_tray() is False


def test_headless_linux_skips_tray_and_ui(monkeypatch):
    """No DISPLAY/WAYLAND_DISPLAY: pystray's Xlib import would raise, Electron cannot open."""
    d = _load_daemon()
    _fake_desktop(monkeypatch, d, display=False)
    monkeypatch.delenv("CUTTLE_NO_TRAY", raising=False)
    monkeypatch.delenv("CUTTLE_NO_UI", raising=False)
    assert d._has_graphical_session() is False
    assert d._should_show_tray() is False
    assert d._should_open_ui() is False
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert d._should_show_tray() is True


def test_non_linux_always_has_desktop(monkeypatch):
    d = _load_daemon()
    monkeypatch.setattr(d.sys, "platform", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert d._has_graphical_session() is True


def test_electron_skips_second_daemon_when_hosted_by_daemon():
    src = ELECTRON_MAIN.read_text(encoding="utf-8")
    assert "CUTTLE_HOSTED_BY_DAEMON" in src
    assert "not spawning another Python process" in src
    start_fn = src.split("function startDaemon")[1].split("function autoStartPipeline")[0]
    assert "cwd: projectRoot" in start_fn
    assert "path.join(projectRoot, 'src', 'scripts'" in start_fn
    assert "cwd: path.join(projectRoot, 'src')" not in start_fn
    assert "CUTTLE_NO_UI" in start_fn
    assert "CUTTLE_NO_TRAY" in start_fn


def test_daemon_sets_hosted_env_when_opening_ui():
    src = DAEMON.read_text(encoding="utf-8")
    assert 'CUTTLE_HOSTED_BY_DAEMON' in src
    assert "DETACHED_PROCESS" in src
    assert "open_cuttle_ui()" in src
    assert "wait_for_flask_ready" in src


def test_watchdog_uses_https_liveness_not_diagnostic_health():
    """Watchdog must hit cheap /api/status over HTTPS :8080, not /api/health."""
    src = DAEMON.read_text(encoding="utf-8")
    fn = src.split("def _flask_health_check()")[1].split("def watch_flask_health()")[0]
    assert '_flask_url("/api/status")' in fn
    assert '_flask_url("/api/health")' not in fn
    assert ":8000" not in fn
    assert "https://127.0.0.1:{FLASK_PORT}" in src
    assert "FLASK_PORT = 8080" in src
