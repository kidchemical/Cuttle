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


def _run_watchdog(d, checks, pressure=0.0):
    """Drive watch_flask_health with scripted health results; return restart count.

    The fake clock advances with each sleep, so hold limits elapse."""
    results = iter(checks)
    restarts = []
    clock = [1000.0]

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
    d._host_memory_pressure = lambda: pressure
    d.time = type("T", (), {"sleep": staticmethod(lambda s: clock.__setitem__(0, clock[0] + s)),
                            "time": staticmethod(lambda: clock[0])})
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


def test_watchdog_holds_restart_while_host_memory_is_starved():
    """A swap storm slows every process; restarting Flask then only ends turns."""
    d = _load_daemon()
    assert _run_watchdog(d, [False] * 20 + [True], pressure=40.0) == []


def test_watchdog_memory_pressure_hold_is_bounded():
    d = _load_daemon()
    assert len(_run_watchdog(d, [False] * 80, pressure=40.0)) == 1


def test_host_memory_pressure_reads_psi(tmp_path, monkeypatch):
    import builtins

    d = _load_daemon()
    psi = tmp_path / "memory"
    psi.write_text("some avg10=6.65 avg60=15.39 avg300=6.00 total=1\nfull avg10=6.32 avg60=15.02 avg300=5.84 total=1\n")
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: real_open(psi if p == "/proc/pressure/memory" else p, *a, **k))
    assert d._host_memory_pressure() == 15.39
