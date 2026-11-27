"""Idempotent daemon shutdown shared by Ctrl+C and tray Exit (Issue 9)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DAEMON = REPO / "src" / "scripts" / "cuttle_daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("cuttle_daemon_shutdown_test", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_request_shutdown_stops_tray_and_processes():
    d = _load_daemon()
    d.daemon_running = True
    d._shutdown_done = False

    stopped = {}

    class FakeIcon:
        def stop(self):
            stopped["tray"] = True

    d.tray_icon = FakeIcon()
    calls = []
    d.stop_all_processes = lambda: calls.append("stop")  # noqa: E731

    d.request_shutdown("signal")
    assert stopped.get("tray") is True
    assert calls == ["stop"]
    assert d.daemon_running is False

    # Idempotent: second call (e.g. tray Exit after Ctrl+C) is a no-op.
    d.request_shutdown("tray-exit")
    assert calls == ["stop"]


def test_request_shutdown_without_tray():
    d = _load_daemon()
    d.daemon_running = True
    d._shutdown_done = False
    d.tray_icon = None
    calls = []
    d.stop_all_processes = lambda: calls.append("stop")  # noqa: E731
    d.request_shutdown("signal")
    assert calls == ["stop"]
    assert d.daemon_running is False


def test_signal_handler_and_tray_exit_share_shutdown():
    src = DAEMON.read_text(encoding="utf-8")
    assert "def request_shutdown" in src
    # Signal handler must do the full shutdown, not just flip the flag.
    handler = src.split("def handler(signum, frame):", 1)[1].split(
        "signal.signal", 1
    )[0]
    assert "request_shutdown" in handler
    # Tray Exit must use the same routine (previously duplicated flag +
    # stop calls and never ran on Ctrl+C).
    on_exit = src.split("def on_exit(icon, item):", 1)[1].split(
        "image = ", 1
    )[0]
    assert "request_shutdown" in on_exit
    # The pystray non-daemon thread must be stopped during shutdown.
    assert "icon.stop()" in src


def _run_watchdog(d, checks):
    """Drive watch_flask_health with scripted health results; return restart count."""
    results = iter(checks)
    restarts = []

    class FakeProc:
        def poll(self):
            return None

    def health():
        try:
            return next(results)
        except StopIteration:
            d.daemon_running = False
            return True

    d.daemon_running = True
    d._shutdown_done = False
    d._flask_restart_in_progress = False
    d.processes = {"flask": FakeProc()}
    d._flask_health_check = health
    d.perform_flask_restart = lambda **kw: restarts.append(kw)
    d.time = type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(lambda: 1000.0)})
    d.watch_flask_health()
    return restarts


def test_watchdog_ignores_a_single_missed_check():
    """One slow check must not restart Flask: that ends every running agent turn."""
    d = _load_daemon()
    assert _run_watchdog(d, [True, False, True, False, False, True]) == []


def test_watchdog_restarts_after_consecutive_missed_checks():
    d = _load_daemon()
    restarts = _run_watchdog(d, [True, False, False, False, True])
    assert len(restarts) == 1
    assert restarts[0]["mode"] == "watchdog"
