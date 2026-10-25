"""Electron titlebar Host/Client badge → in-shell connection popup.

The badge opens ``shell/shell_connection.js`` (connection info + switch host)
instead of navigating the window to ``connect.html``; re-picking the current
target must not reload the Electron window. Pure helpers run under node; the
wiring and the main-process no-reload paths are source contracts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONN_JS = REPO / "src" / "web" / "js" / "shell" / "shell_connection.js"
ELECTRON_JS = REPO / "src" / "web" / "js" / "shell" / "shell_electron.js"
HTML = REPO / "src" / "web" / "app_shell.html"
MAIN_JS = REPO / "electron" / "main.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = """
const C = require(process.env.CONN_JS);
const host = { clientMode: false, hostname: 'Desk', httpPort: 8000, lanIps: ['192.168.1.20', '10.0.0.5'] };
const hostCustomPort = { ...host, httpPort: 8100 };
const hostNoIps = { clientMode: false, hostname: 'Desk', httpPort: 8000 };
const client = {
  clientMode: true, hostname: 'Laptop', host: '192.168.1.20', httpPort: 8000, httpsPort: 8080,
  endpoint: { kind: 'paired', scheme: 'http', host: '192.168.1.20', port: 8000 },
  lanIps: ['192.168.1.33'],
};
const netOn = { lan_access_enabled: true, lan_ipv4: '192.168.1.99', portal_url_phone: 'https://192.168.1.20:8888' };
const netOff = { lan_access_enabled: false, lan_ipv4: null };
console.log(JSON.stringify({
  hostOn: C.describeConnection(host, netOn),
  hostCustom: C.describeConnection(hostCustomPort, netOn),
  hostOff: C.describeConnection(host, netOff),
  hostFallbackIp: C.describeConnection(hostNoIps, netOn),
  hostLoading: C.describeConnection(host, null),
  client: C.describeConnection(client, null),
  tipHost: C.badgeTooltip(host, null),
  tipHostNoIp: C.badgeTooltip(hostNoIps, null),
  tipClient: C.badgeTooltip(client, null),
  v6: C.hostPortLabel('fe80::1', 8000),
}));
"""


@pytest.fixture(scope="module")
def results():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    out = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, check=True,
        env={**os.environ, "CONN_JS": str(CONN_JS)},
    )
    return json.loads(out.stdout)


def _rows(view):
    return {r["label"]: r["value"] for r in view["rows"]}


@node_only
def test_host_view_shows_lan_ip_and_connect_address(results):
    view = results["hostOn"]
    assert view["role"] == "host"
    rows = _rows(view)
    # This device's own address (Electron) wins over Flask's guess.
    assert rows["LAN IP"] == "192.168.1.20"
    assert rows["Connect other devices to"] == "192.168.1.20"
    assert rows["Phone / browser"] == "https://192.168.1.20:8888"
    assert view["warning"] == ""
    assert _rows(results["hostCustom"])["Connect other devices to"] == "192.168.1.20:8100"


@node_only
def test_host_view_warns_when_lan_access_off(results):
    view = results["hostOff"]
    rows = _rows(view)
    assert rows["LAN IP"] == "192.168.1.20"
    assert "Connect other devices to" not in rows
    assert "LAN access is off" in view["warning"]


@node_only
def test_host_view_falls_back_to_flask_lan_ip(results):
    assert _rows(results["hostFallbackIp"])["LAN IP"] == "192.168.1.99"
    assert _rows(results["hostLoading"])["LAN IP"] == "192.168.1.20"


@node_only
def test_client_view_shows_remote_and_own_ip(results):
    view = results["client"]
    assert view["role"] == "client"
    rows = _rows(view)
    assert rows["Connected to"] == "192.168.1.20:8000"
    assert rows["Protocol"] == "HTTP"
    assert rows["This device LAN IP"] == "192.168.1.33"


@node_only
def test_badge_tooltips_include_device_lan_ip(results):
    assert "LAN IP 192.168.1.20" in results["tipHost"]
    assert "LAN IP" not in results["tipHostNoIp"]
    assert "connected to 192.168.1.20:8000" in results["tipClient"]
    assert "this device 192.168.1.33" in results["tipClient"]
    assert results["v6"] == "[fe80::1]:8000"


def test_badge_opens_popup_instead_of_navigating():
    owner = ELECTRON_JS.read_text(encoding="utf-8")
    assert "connection.create({" in owner
    assert "if (connectionPopup) connectionPopup.open();" in owner
    # connect.html navigation is only the fallback when the module is missing.
    assert "else if (typeof api.desktop?.showConnect === 'function') api.desktop.showConnect();" in owner
    assert "connection.badgeTooltip(cfg, hostNetInfo)" in owner


def test_shell_loads_connection_owner_and_markup():
    html = HTML.read_text(encoding="utf-8")
    assert html.index("/js/shell/shell_connection.js") < html.index("/js/shell/shell_electron.js")
    assert html.index("/js/shared/modal.js") < html.index("/js/shell/shell_connection.js")
    for el_id in (
        "shellConnectionModal", "shellConnectionInfo", "shellConnectionHostInput",
        "shellConnectionConnect", "shellConnectionLocal", "shellConnectionSavedList",
    ):
        assert f'id="{el_id}"' in html


def test_main_skips_reload_when_target_unchanged():
    main = MAIN_JS.read_text(encoding="utf-8")
    assert "function deviceLanIpv4s()" in main
    assert "lanIps: deviceLanIpv4s()," in main
    assert "function isShowingCuttleUi()" in main
    # Re-picking "This PC hosts Cuttle" while hosting: no stopWorkerSidecar, no reload.
    use_local = main[main.index("ipcMain.handle('desktop-use-local'"):]
    early = use_local.index("return { ok: true, unchanged: true };")
    assert early < use_local.index("stopWorkerSidecar();")
    assert early < use_local.index("await loadCuttleUi();")
    # Reconnecting to the current Client host does not reload either.
    connect = main[main.index("async function connectDesktopTarget(parsed)"):]
    assert connect.index("if (unchanged) return { ok: true, unchanged: true") < connect.index("await loadCuttleUi();")
