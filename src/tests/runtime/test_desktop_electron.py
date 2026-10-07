"""Desktop Electron update manifest + when the titlebar Update button may show."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from api import desktop_electron as desk

REPO = Path(__file__).resolve().parents[3]
POLICY = REPO / "src" / "web" / "js" / "shell/desktop_update_policy.js"


def test_desktop_source_hash_is_stable_for_same_files():
    a = desk.desktop_source_hash()
    b = desk.desktop_source_hash()
    assert a == b
    assert len(a) == 20


def test_desktop_manifest_shape():
    data = desk.desktop_manifest()
    assert data["ok"] is True
    assert data["service"] == "cuttle-desktop"
    assert data["hash"] == desk.desktop_source_hash()
    assert "artifact" in data
    if data["artifact"]:
        assert data["downloadPath"] == "/api/desktop/electron/app.asar"
        assert data["artifactSize"] > 0
        assert data["artifactSha256"] == desk.artifact_sha256(desk.UPDATE_ASAR)


def test_artifact_sha256_is_full_file_digest(tmp_path):
    import hashlib

    blob = tmp_path / "app.asar"
    blob.write_bytes(b"cuttle" * 1000)
    assert desk.artifact_sha256(blob) == hashlib.sha256(blob.read_bytes()).hexdigest()
    blob.write_bytes(b"changed")
    assert desk.artifact_sha256(blob) == hashlib.sha256(b"changed").hexdigest()


def test_desktop_client_verifies_checksum_and_is_windows_only_for_swap():
    main_js = (desk.ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    body = main_js[main_js.index("async function applyDesktopUpdate"):]
    body = body[:body.index("\n}\n")]
    assert "process.platform !== 'win32'" in body
    assert body.index("fileSha256(tmp)") < body.index("move /Y")


def test_desktop_electron_routes_registered():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.get("/api/desktop/electron")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["hash"] == desk.desktop_source_hash()


def test_titlebar_update_is_for_remote_clients_only():
    """Host Start Cuttle is local mode — stale asar vs source is not an update."""
    assert POLICY.is_file()
    policy_js = json.dumps(str(POLICY))
    script = f"""
const p = require({policy_js});
const remote = {{ ok: true, hash: 'bbbbbbbbbbbbbbbbbbbb', artifact: true }};
const local = 'aaaaaaaaaaaaaaaaaaaa';
if (p.desktopUpdateAvailable(remote, local, {{ packaged: true, clientMode: false, host: '127.0.0.1' }})) process.exit(2);
if (p.desktopUpdateAvailable(remote, local, {{ packaged: true, clientMode: true, host: '127.0.0.1' }})) process.exit(3);
if (!p.desktopUpdateAvailable(remote, local, {{ packaged: true, clientMode: true, host: '192.0.2.20' }})) process.exit(4);
if (p.desktopUpdateAvailable(remote, remote.hash, {{ packaged: true, clientMode: true, host: '192.0.2.20' }})) process.exit(5);
if (p.desktopUpdateAvailable(remote, local, {{ packaged: false, clientMode: true, host: '192.0.2.20' }})) process.exit(6);
if (p.desktopRole({{ clientMode: false, host: '127.0.0.1' }}) !== 'host') process.exit(7);
if (p.desktopRole({{ clientMode: true, host: '127.0.0.1' }}) !== 'host') process.exit(8);
if (p.desktopRole({{ clientMode: true, host: '192.0.2.20' }}) !== 'client') process.exit(9);
console.log('ok');
"""
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=20,
        cwd=str(REPO),
    )
    need_electron = proc.returncode != 0 and (
        proc.returncode == 127
        or "not recognized" in (proc.stderr or "").lower()
        or "cannot find the path" in (proc.stderr or "").lower()
    )
    if need_electron:
        electron = REPO / "electron" / "node_modules" / "electron" / "dist" / "electron.exe"
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        proc = subprocess.run(
            [str(electron), "-e", script],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=20,
            cwd=str(REPO),
            env=env,
        )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    assert "ok" in (proc.stdout or "")


def test_linux_wayland_sessions_relaunch_under_x11_by_default():
    """Electron 38+ picks native Wayland, which hangs before 'ready' with no active
    output and lacks always-on-top/positioning; relaunch under XWayland unless opted in."""
    main_js = (desk.ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    head = main_js[: main_js.index("let mainWindow")]
    assert "process.platform === 'linux'" in head
    assert "CUTTLE_ELECTRON_WAYLAND" in head
    assert "'--ozone-platform=x11'" in head and "app.relaunch(" in head
