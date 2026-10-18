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
        assert data["artifactSha256"] == desk.artifact_sha256(desk.update_asar())
    assert data["updateSource"] == "host"


def test_packaged_host_disables_host_built_updates(monkeypatch, tmp_path):
    """A release-installed Host has no electron/ sources: no hash, no artifact,
    no download, no pack attempt — Clients update from the release instead."""
    monkeypatch.setattr(desk, "ELECTRON_DIR", tmp_path / "electron")
    monkeypatch.setattr(desk, "PACK_SCRIPT", tmp_path / "electron" / "pack-desktop-update.js")
    monkeypatch.setattr(desk, "_find_node", lambda: (_ for _ in ()).throw(AssertionError("must not pack")))
    assert desk.has_electron_sources() is False
    data = desk.desktop_manifest()
    assert data["ok"] is True
    assert data["updateSource"] == "release"
    assert data["hash"] is None and data["artifact"] is False and data["downloadPath"] is None
    assert desk.ensure_update_asar("anything") is None

    from flask import Flask

    app = Flask(__name__)
    desk.register_desktop_electron_routes(app)
    res = app.test_client().get("/api/desktop/electron/app.asar")
    assert res.status_code == 404


def test_update_artifact_lives_in_cuttle_home_cache():
    from core.runtime_paths import cuttle_home

    assert desk.update_dir().is_relative_to(cuttle_home())
    assert not desk.update_dir().is_relative_to(REPO)


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


def test_linux_wayland_sessions_launch_under_xwayland(monkeypatch, tmp_path):
    """Native Wayland spins before 'ready' with no active output and lacks
    always-on-top/positioning, so every launcher passes --ozone-platform=x11 on
    Wayland sessions (no in-process relaunch: that broke on Electron 28)."""
    import subprocess
    from core import runtime_paths

    main_js = (desk.ELECTRON_DIR / "main.js").read_text(encoding="utf-8")
    assert "app.relaunch(" not in main_js[: main_js.index("let mainWindow")]

    monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("CUTTLE_ELECTRON_WAYLAND", raising=False)
    assert runtime_paths.electron_display_args() == ["--ozone-platform=x11"]
    monkeypatch.setenv("CUTTLE_ELECTRON_WAYLAND", "1")
    assert runtime_paths.electron_display_args() == []
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("CUTTLE_ELECTRON_WAYLAND")
    assert runtime_paths.electron_display_args() == []

    helper = desk.ELECTRON_DIR.parent / ".cuttle" / "scripts" / "electron-display.sh"
    def shell_args(**env):
        out = subprocess.run(["bash", "-c", f'source "{helper}"; echo "${{DISPLAY_ARGS[*]}}"'],
                             capture_output=True, text=True, encoding="utf-8",
                             env={"PATH": "/usr/bin:/bin", **env})
        return out.stdout.strip()
    if subprocess.run(["bash", "-c", "true"]).returncode == 0:
        assert shell_args(XDG_SESSION_TYPE="wayland") == "--ozone-platform=x11"
        assert shell_args(XDG_SESSION_TYPE="wayland", CUTTLE_ELECTRON_WAYLAND="1") == ""
        assert shell_args(XDG_SESSION_TYPE="x11") == ""
    for name in ("launch-cuttle-host.sh", "launch-cuttle-client.sh"):
        script = (helper.parent / name).read_text(encoding="utf-8")
        assert "electron-display.sh" in script and '"${DISPLAY_ARGS[@]}"' in script


def _run_policy_node(script: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=20, cwd=str(REPO),
    )
    if proc.returncode != 0 and (
        proc.returncode == 127
        or "not recognized" in (proc.stderr or "").lower()
        or "cannot find the path" in (proc.stderr or "").lower()
    ):
        electron = REPO / "electron" / "node_modules" / "electron" / "dist" / "electron.exe"
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        proc = subprocess.run(
            [str(electron), "-e", script],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
            cwd=str(REPO), env=env,
        )
    return proc


def _update_plan(cases: str) -> None:
    policy_js = json.dumps(str(POLICY))
    script = f"""
const p = require({policy_js});
const assert = require('assert');
const src = (extra) => Object.assign({{install_kind:'source', developer_mode:{{enabled:true, available:true}},
    update_source:'official', release:{{state:'latest', release:{{version:'0.3.0', url:'u'}}}},
    checkout:{{path:'/repo', branch:'dev', upstream:'origin/dev', behind:0, ahead:0, commits:[]}}}}, extra || {{}});
const ids = (plan) => plan.channels.map((c) => c.id + ':' + c.state);
{cases}
console.log('ok');
"""
    proc = _run_policy_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    assert "ok" in (proc.stdout or "")


def test_update_plan_release_install_shows_release_only():
    """A release build gets one channel: download the published release."""
    _update_plan("""
let plan = p.updatePlan({role:'host', snapshot:{install_kind:'release', developer_mode:{enabled:false, available:false},
    release:{state:'available', release:{version:'0.4.0', url:'https://github.com/kidchemical/Cuttle/releases/tag/v0.4.0'}}}});
assert.deepStrictEqual(ids(plan), ['release:available']);
assert.strictEqual(plan.selected, 'release');
assert.strictEqual(plan.channels[0].action, 'download');
assert.strictEqual(plan.channels[0].actionLabel, 'Download v0.4.0');
assert.strictEqual(plan.titlebar.show, true);
assert.strictEqual(plan.titlebar.chip, 'RELEASE');
// Even a stored Developer flag cannot add checkout channels to a release install.
plan = p.updatePlan({role:'host', snapshot:{install_kind:'release', developer_mode:{enabled:false}, checkout:{behind:9}}});
assert.deepStrictEqual(ids(plan), ['release:unknown']);
assert.strictEqual(plan.titlebar.show, false);
// No snapshot (non-owner / old server): release-only, nothing actionable.
plan = p.updatePlan({});
assert.deepStrictEqual(ids(plan), ['release:unknown']);
assert.strictEqual(plan.selected, '');
""")


def test_update_plan_source_checkout_without_developer_mode_never_pulls():
    """Developer mode off: the release is information, not an action on a checkout."""
    _update_plan("""
const plan = p.updatePlan({role:'host', snapshot:src({developer_mode:{enabled:false, available:true},
    release:{state:'available', release:{version:'0.4.0'}}, checkout:null})});
assert.deepStrictEqual(ids(plan), ['release:info']);
assert.strictEqual(plan.channels[0].selectable, false);
assert.strictEqual(plan.titlebar.show, false);
assert.strictEqual(plan.developerModeAvailable, true);
""")


def test_update_plan_developer_host_branch_channel():
    """Developer Host: GitHub branch channel with commit preview, or blocked/current."""
    _update_plan("""
let plan = p.updatePlan({role:'host', snapshot:src({checkout:{path:'/repo', branch:'dev', upstream:'origin/dev',
    behind:2, ahead:0, commits:[{rev:'b', subject:'two'}, {rev:'a', subject:'one'}]}})});
assert.deepStrictEqual(ids(plan), ['release:current', 'official:available']);
const branch = plan.channels[1];
assert.strictEqual(plan.selected, 'official');
assert.strictEqual(branch.actionLabel, 'Pull 2 commits');
assert.strictEqual(branch.source, 'official');
assert.strictEqual(branch.path, '/repo');
assert.strictEqual(plan.titlebar.chip, 'GITHUB');
// Host role never offers the Host channel (it would follow itself).
assert.ok(!plan.channels.some((c) => c.id === 'host'));
plan = p.updatePlan({role:'host', snapshot:src({checkout:{upstream:'origin/dev', behind:1, blocked:'Local changes'}})});
assert.deepStrictEqual(ids(plan), ['release:current', 'official:blocked']);
assert.strictEqual(plan.selected, '');
assert.strictEqual(plan.titlebar.show, false);
plan = p.updatePlan({role:'host', snapshot:src()});
assert.deepStrictEqual(ids(plan), ['release:current', 'official:current']);
assert.ok(plan.channels[1].summary.includes('as of the last fetch'));
""")


def test_update_plan_client_must_pick_when_several_channels_have_updates():
    """Two available channels: nothing preselected until the user picks one."""
    _update_plan("""
const mesh = {available:true, localVersion:'0.2.1 (aaa)', hostVersion:'0.2.1 (bbb)', target:'laptop'};
let plan = p.updatePlan({role:'client', snapshot:src({update_source:'host'}), mesh, desktop:{available:true}});
assert.deepStrictEqual(ids(plan), ['release:current', 'host:available', 'official:unknown', 'desktop:available']);
assert.strictEqual(plan.selected, '');
assert.deepStrictEqual(plan.available, ['host', 'desktop']);
assert.strictEqual(plan.titlebar.chip, '2 UPDATES');
// The titlebar's preferred channel preselects; unknown channels stay pickable.
plan = p.updatePlan({role:'client', snapshot:src(), mesh, preferred:'host'});
assert.strictEqual(plan.selected, 'host');
assert.strictEqual(plan.channels.find((c) => c.id === 'official').selectable, true);
// A Client on another branch cannot follow the Host.
plan = p.updatePlan({role:'client', snapshot:src(), mesh:{wrongBranch:true, branch:'main', hostBranch:'dev'}});
assert.strictEqual(plan.channels.find((c) => c.id === 'host').state, 'blocked');
assert.strictEqual(plan.titlebar.show, false);
""")
