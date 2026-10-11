"""Jobs/Mesh split: two owned surfaces, one stashed Mesh app.

Jobs = the execution ledger (agent turns + mesh jobs). Mesh = device fleet +
mesh job queue admin. Guards against re-mixing the two pages/modules.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"

JOBS_HTML = WEB / "jobs_page.html"
MESH_HTML = WEB / "mesh_page.html"
JOBS_JS = WEB / "js" / "jobs" / "jobs_page.js"
MESH_JS = WEB / "js" / "mesh" / "mesh_page.js"
SHELL_JS = WEB / "js" / "shell" / "shell_rail.js"
SHELL_HTML = WEB / "app_shell.html"
APPS_JS = WEB / "js" / "shell" / "apps_page.js"


def test_mesh_rail_app_registered_and_stashed():
    html = SHELL_HTML.read_text(encoding="utf-8")
    assert 'id="nav-mesh"' in html and 'data-page="/mesh_page.html"' in html
    shell = SHELL_JS.read_text(encoding="utf-8")
    order = shell.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "'nav-mesh'" in order
    assert "'nav-mesh'" in shell.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
    assert "const RAIL_LAYOUT_VERSION = 10;" in shell
    assert "'nav-mesh'" in APPS_JS.read_text(encoding="utf-8")


def test_jobs_page_is_ledger_only():
    html = JOBS_HTML.read_text(encoding="utf-8")
    assert "/js/jobs/jobs_ledger.js" in html and "/js/jobs/jobs_page.js" in html
    assert 'id="runningJobsList"' in html and 'id="recentJobsList"' in html
    # Mesh admin moved out.
    assert 'id="devicesList"' not in html
    assert 'id="pairingList"' not in html
    assert 'id="meshJobsList"' not in html
    js = JOBS_JS.read_text(encoding="utf-8")
    assert "/api/jobs" in js
    assert "/api/workers" not in js
    assert "loadDevices" not in js


def test_mesh_page_is_admin_only():
    html = MESH_HTML.read_text(encoding="utf-8")
    assert "/js/mesh/mesh_page.js" in html
    assert 'id="devicesList"' in html and 'id="pairingList"' in html and 'id="meshJobsList"' in html
    assert "/js/jobs/" not in html
    js = MESH_JS.read_text(encoding="utf-8")
    assert "/api/workers" in js
    assert "/api/jobs" not in js
