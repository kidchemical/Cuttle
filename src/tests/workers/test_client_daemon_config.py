"""Client daemon finds the Electron app's config and status across userData dirs.

Regression (kcslaptop, 2026-11-01): the updater-started daemon read only
``%LOCALAPPDATA%\\cuttle-desktop\\desktop-config.json``, which held worker
credentials but no host or TLS pin — Electron keeps those under its userData
(``%APPDATA%\\cuttle-desktop``). The daemon exited "No host", left a stale
``running`` status, and the worker stayed offline. Electron, in turn, looked
for the daemon's status under its own userData and never saw it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent.parent
REPO = SRC_DIR.parent
MAIN_JS = REPO / "electron" / "main.js"

_ENV_KEYS = (
    "CUTTLE_DEVICE_WORKERS_ENABLED", "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL",
    "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP", "CUTTLE_DEVICE_WORKER_TOKEN",
    "CUTTLE_DEVICE_WORKER_ID", "CUTTLE_CLIENT_DAEMON", "CUTTLE_REPO_ROOT",
    "CUTTLE_DEVICE_WORKER_LOG", "CUTTLE_COORDINATOR_TLS_SPKI_SHA256", "CUTTLE_FLASK_HOST",
)


def _load_daemon():
    path = SRC_DIR / "scripts" / "cuttle_client_daemon.py"
    spec = importlib.util.spec_from_file_location("cuttle_client_daemon_config", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    """Windows-shaped layout: daemon state in LOCALAPPDATA, Electron in APPDATA."""
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    home = tmp_path / "home"
    for d in (local / "cuttle-desktop", roaming / "cuttle-desktop", home):
        d.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in _ENV_KEYS:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return {"own": local / "cuttle-desktop", "electron": roaming / "cuttle-desktop"}


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


PIN = {"192.168.4.26:8080": {"spki": "SPKI-HOST", "basis": "user"}}
ELECTRON_CFG = {
    "mode": "client",
    "host": "192.168.4.26",
    "httpPort": 8000,
    "httpsPort": 8080,
    "workerMode": True,
    "workerId": "kcslaptop",
    "workerToken": "electron-token",
    "workerEnrolledAt": 1_000,
    "tlsPins": PIN,
}
DAEMON_CFG = {
    "workerToken": "daemon-token",
    "workerId": "kcslaptop",
    "workerEnrolledAt": 2_000,
    "clientDaemon": True,
}


def test_config_takes_host_and_pins_from_electron_userdata(dirs):
    mod = _load_daemon()
    _write(dirs["electron"] / "desktop-config.json", ELECTRON_CFG)
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    cfg = mod.load_desktop_config()
    assert cfg["host"] == "192.168.4.26"
    assert cfg["tlsPins"] == PIN
    assert cfg["workerMode"] is True
    # The daemon's own enrollment is newer, so its credential wins.
    assert cfg["workerToken"] == "daemon-token"
    assert cfg["clientDaemon"] is True


def test_newer_electron_enrollment_wins_credentials(dirs):
    mod = _load_daemon()
    _write(dirs["electron"] / "desktop-config.json", {**ELECTRON_CFG, "workerEnrolledAt": 5_000})
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    cfg = mod.load_desktop_config()
    assert cfg["workerToken"] == "electron-token"
    assert cfg["workerEnrolledAt"] == 5_000


def test_electron_workermode_false_is_honoured(dirs):
    mod = _load_daemon()
    _write(dirs["electron"] / "desktop-config.json", {**ELECTRON_CFG, "workerMode": False})
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    assert mod.main() == 0
    status = json.loads((dirs["own"] / "client-daemon-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "stopped"


def test_mode_specific_userdata_is_found(dirs, tmp_path):
    mod = _load_daemon()
    client_dir = tmp_path / "home" / ".config" / "cuttle-desktop-client"
    client_dir.mkdir(parents=True)
    _write(client_dir / "desktop-config.json", ELECTRON_CFG)
    assert mod.load_desktop_config()["host"] == "192.168.4.26"


def test_save_never_copies_electron_settings(dirs):
    mod = _load_daemon()
    _write(dirs["electron"] / "desktop-config.json", ELECTRON_CFG)
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    mod.save_desktop_config({"workerToken": "fresh"})
    own = json.loads((dirs["own"] / "desktop-config.json").read_text(encoding="utf-8"))
    assert own["workerToken"] == "fresh"
    assert "host" not in own and "tlsPins" not in own


def test_missing_host_marks_status_failed(dirs):
    mod = _load_daemon()
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    # A previous daemon died while "running"; this run must not leave that.
    _write(dirs["own"] / "client-daemon-status.json",
           {"state": "running", "owns_worker": True, "pid": 1, "updated_at": time.time()})
    assert mod.main() == 2
    status = json.loads((dirs["own"] / "client-daemon-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["owns_worker"] is False
    assert "No host" in status["error"]
    assert str(dirs["electron"] / "desktop-config.json") in status["error"]


def test_main_uses_electron_host_and_pin(dirs, monkeypatch):
    mod = _load_daemon()
    _write(dirs["electron"] / "desktop-config.json", ELECTRON_CFG)
    _write(dirs["own"] / "desktop-config.json", DAEMON_CFG)
    calls: dict = {}

    def fake_enroll(cfg_arg, bases):
        calls["bases"] = list(bases)
        return {"success": True, "worker_id": "kcslaptop"}

    monkeypatch.setattr(mod, "enroll", fake_enroll)
    import api.device_workers.worker_loop as worker_loop
    from api.tls_cert import PIN_ENV

    monkeypatch.delenv(PIN_ENV, raising=False)

    def fake_loop(*, should_continue, base_url):
        calls["base"] = base_url
        calls["pin"] = os.environ.get(PIN_ENV)
        calls["token"] = os.environ.get("CUTTLE_DEVICE_WORKER_TOKEN")

    monkeypatch.setattr(worker_loop, "run_remote_worker_loop", fake_loop)
    assert mod.main() == 0
    assert calls["bases"] == ["https://192.168.4.26:8080", "http://192.168.4.26:8000"]
    assert calls["base"] == "https://192.168.4.26:8080"
    assert calls["pin"] == "SPKI-HOST"
    assert calls["token"] == "daemon-token"
    monkeypatch.delenv(PIN_ENV, raising=False)


def test_console_safe_survives_cp1252_redirect(monkeypatch):
    mod = _load_daemon()
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", out)
    with pytest.raises(UnicodeEncodeError):
        print("worker → host")
    mod._console_safe()
    print("worker → host")
    out.flush()
    assert b"worker \\u2192 host" in out.buffer.getvalue()


def _extract_js_function(name: str) -> str:
    lines = MAIN_JS.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.match(rf"^(async\s+)?function\s+{name}\s*\(", line):
            depth, buf = 0, []
            for j in range(i, len(lines)):
                buf.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0 and j > i:
                    return "".join(buf)
    raise AssertionError(f"{name} not found in main.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("fresh,expect_skip", [(True, True), (False, False)])
def test_electron_sees_daemon_status_in_daemon_state_dir(tmp_path, fresh, expect_skip):
    local = tmp_path / "Local" / "cuttle-desktop"
    userdata = tmp_path / "Roaming" / "cuttle-desktop"
    local.mkdir(parents=True)
    userdata.mkdir(parents=True)
    age = 5 if fresh else 600
    _write(local / "client-daemon-status.json",
           {"state": "running", "owns_worker": True, "pid": 42, "updated_at": time.time() - age})
    script = (
        "const path = require('path'); const os = require('os'); const fs = require('fs');\n"
        "const CLIENT_MODE = true;\n"
        f"const app = {{ getPath: () => {json.dumps(str(userdata))}, isPackaged: false }};\n"
        "function loadDesktopConfig() { return { workerMode: true }; }\n"
        "let enrolled = false;\n"
        "async function enrollWorkerWithHost() { enrolled = true; throw new Error('stop here'); }\n"
        "function saveDesktopConfig() {}\n"
        + _extract_js_function("startWorkerSidecar")
        + "\n(async () => { await startWorkerSidecar(); console.log(JSON.stringify({ enrolled })); })()"
        ".catch((e) => { console.error(e); process.exit(1); });\n"
    )
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming")}
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, cwd=str(REPO), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    # Fresh daemon heartbeat -> Electron must not start a second worker.
    assert result["enrolled"] is (not expect_skip)
