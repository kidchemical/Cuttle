"""Gizmos client: pure model (node), shell wiring, and real-browser docking.

* node harness — formatting, window choice, tones, dock fallbacks, ordering,
  and markup escaping in ``src/web/js/gizmos/gizmos_model.js``
* static wiring — load order after app_shell.js, stashed App entry, rail
  migration, Electron pop-out bridge stays id-only
* browser (skipped without Playwright) — the shell controller renders into
  the title bar / blade bar / float layer, drag re-docks with a PATCH, the
  popover moves and removes, and a disabled flag clears everything
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.browser_guard import launch_chromium

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
MODEL = WEB / "js" / "gizmos" / "gizmos_model.js"

HARNESS = r"""
const M = require(process.env.MODEL);
const now = 1_800_000_000_000;
const data = {label: 'Codex', plan: 'plus', blocked: false, updated_at: 1_800_000_000,
  windows: [
    {id: 'five_hour', label: '5-hour', used_percent: 82, remaining_percent: 18, reset_at: 1_800_000_000 + 3 * 3600 + 720},
    {id: 'weekly', label: 'Weekly', used_percent: 40, remaining_percent: 60, reset_at: 1_800_000_000 + 4 * 86400},
  ]};
const g = {id: 'm1', type: 'usage_meter', title: 'Codex <usage>', config: {agent: 'codex', window: 'tightest', show: 'remaining'},
  placement: {dock: 'titlebar', order: 0}};
const out = {};
out.durations = [M.formatDuration(0), M.formatDuration(30), M.formatDuration(14 * 60), M.formatDuration(3 * 3600 + 720),
  M.formatDuration(2 * 86400 + 4 * 3600)];
out.tightest = M.pickWindow(data, {window: 'tightest'}).id;
out.pinned = M.pickWindow(data, {window: 'weekly'}).id;
out.missingFallsBack = M.pickWindow(data, {window: 'nope'}).id;
const model = M.meterModel(g, data, now);
out.model = {pct: model.pct, value: model.value, tone: model.tone, resetText: model.resetText, rows: model.rows.length};
out.used = M.meterModel({...g, config: {...g.config, show: 'used'}}, data, now).value;
out.loading = M.meterModel(g, null, now).state;
out.error = M.meterModel(g, {windows: [], error: 'login'}, now).state;
const blocked = {...data, blocked: true, unblock_at: 1_800_000_000 + 7200,
  windows: [{...data.windows[0], used_percent: 100, remaining_percent: 0}, data.windows[1]]};
out.blocked = M.meterModel(g, blocked, now);
out.blocked = {tone: out.blocked.tone, resetText: out.blocked.resetText};
out.tones = [M.toneFor(50, false), M.toneFor(20, false), M.toneFor(5, false), M.toneFor(null, false), M.toneFor(50, true)];
out.docks = [M.effectiveDock({dock: 'titlebar'}, {titlebar: false}), M.effectiveDock({dock: 'popout'}, {popout: false}),
  M.effectiveDock({dock: 'popout'}, {popout: true}), M.effectiveDock({dock: 'bogus'}, {titlebar: true})];
out.order = [M.orderBetween(null, null), M.orderBetween(null, 2), M.orderBetween(3, null), M.orderBetween(1, 2)];
out.sorted = M.sortForDock([
  {id: 'b', placement: {dock: 'rail', order: 2}}, {id: 'a', placement: {dock: 'rail', order: 1}},
  {id: 'c', placement: {dock: 'titlebar', order: 0}}], 'rail', {titlebar: true}).map(x => x.id);
out.sortedFallback = M.sortForDock([{id: 'c', placement: {dock: 'titlebar', order: 0}}], 'rail', {titlebar: false}).map(x => x.id);
out.keys = [M.dataKey(g), M.dataKey({...g, id: 'm2'}), M.dataKey({id: 'z', type: 'other'})];
const html = ['titlebar', 'rail', 'float', 'popout', 'card'].map(v => M.renderGizmoHtml(g, data, v, now));
out.escaped = html.every(h => !h.includes('<usage>'));
out.variants = html.map(h => /gizmo--(\w+)/.exec(h)[1]);
out.cardRows = (html[4].match(/gizmo-row /g) || []).length;
out.detail = M.renderDetailHtml(g, data, now).includes('Plan: plus');
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def harness():
    if not shutil.which("node"):
        pytest.skip("node not available")
    proc = subprocess.run(["node", "-e", HARNESS], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(proc.stdout)


def test_durations(harness):
    assert harness["durations"] == ["now", "<1m", "14m", "3h 12m", "2d 4h"]


def test_window_choice(harness):
    assert harness["tightest"] == "five_hour"
    assert harness["pinned"] == "weekly"
    assert harness["missingFallsBack"] == "five_hour"


def test_meter_model(harness):
    assert harness["model"] == {"pct": 18, "value": "18%", "tone": "low",
                                "resetText": "5-hour resets in 3h 12m", "rows": 2}
    assert harness["used"] == "82%"
    assert harness["loading"] == "loading"
    assert harness["error"] == "error"
    assert harness["blocked"] == {"tone": "blocked", "resetText": "Unblocks in 2h"}
    assert harness["tones"] == ["ok", "low", "critical", "unknown", "blocked"]


def test_dock_fallbacks_and_ordering(harness):
    assert harness["docks"] == ["rail", "float", "popout", "titlebar"]
    assert harness["order"] == [0, 1, 4, 1.5]
    assert harness["sorted"] == ["a", "b"]
    assert harness["sortedFallback"] == ["c"]
    assert harness["keys"] == ["usage:codex", "usage:codex", "gizmo:z"]


def test_markup(harness):
    assert harness["escaped"] is True
    assert harness["variants"] == ["titlebar", "rail", "float", "popout", "card"]
    assert harness["cardRows"] == 2
    assert harness["detail"] is True


# ---------------------------------------------------------------------------
# static wiring
# ---------------------------------------------------------------------------
def test_shell_loads_gizmos_after_app_shell():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    shell = html.index("/js/shell/app_shell.js")
    model = html.index("/js/gizmos/gizmos_model.js")
    controller = html.index("/js/gizmos/gizmos_shell.js")
    assert shell < model < controller
    assert "/css/gizmos.css" in html
    assert 'id="nav-gizmos"' in html and 'data-page="/gizmos_page.html"' in html


def test_gizmos_app_starts_stashed_and_migrates_once():
    source = (WEB / "js" / "shell" / "app_shell.js").read_text(encoding="utf-8")
    assert "'nav-gizmos'" in source.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "const DEFAULT_RAIL_HIDDEN = ['nav-achievements', 'nav-gizmos', 'nav-projects'];" in source
    start = source.index("function migrateUILayout(saved)")
    end = source.index("// ── Cuttle web apps", start)
    script = """const assert = require('assert');
const DEFAULT_LAYOUT={}; const DEFAULT_RAIL_HIDDEN=['nav-achievements','nav-gizmos','nav-projects'];
const RAIL_LAYOUT_VERSION=7;
const CANONICAL_RAIL_ITEM_ORDER=['nav-chat','nav-achievements','nav-gizmos','nav-projects','nav-apps'];
""" + source[start:end] + """
let r = migrateUILayout({layout_version:6, rail_items:['nav-chat','nav-achievements','nav-apps'], rail_hidden:['nav-projects']});
assert.deepEqual(r.layout.rail_hidden.sort(), ['nav-gizmos','nav-projects']);
assert(r.layout.rail_items.includes('nav-achievements'));
r = migrateUILayout({layout_version:7, rail_items:['nav-chat','nav-gizmos'], rail_hidden:[]});
assert.equal(r.changed, false); assert(r.layout.rail_items.includes('nav-gizmos'));
"""
    if not shutil.which("node"):
        pytest.skip("node not available")
    subprocess.run(["node", "-e", script], check=True)


def test_electron_popout_bridge_is_id_only():
    main = (REPO / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (REPO / "electron" / "preload.js").read_text(encoding="utf-8")
    assert "ipcMain.handle('gizmo-popouts-sync'" in main
    assert "GIZMO_ID_RE.test(id)" in main
    assert "event.sender !== mainWindow.webContents" in main
    assert "new URL('/gizmo_popout.html', base)" in main
    assert "gizmo-popouts-sync" in preload and "gizmo-popout-closed" in preload


def test_settings_toggle_notifies_shell():
    assert "cuttle-experimental-flags-changed" in (WEB / "settings_page.html").read_text(encoding="utf-8")
    assert "cuttle-experimental-flags-changed" in (WEB / "js" / "gizmos" / "gizmos_shell.js").read_text(encoding="utf-8")


def test_popover_closes_when_focus_leaves_shell_document():
    # Clicks into a chat pane land in the content iframe and never reach the
    # shell document's pointerdown closer — window blur closes the panel.
    src = (WEB / "js" / "gizmos" / "gizmos_shell.js").read_text(encoding="utf-8")
    assert "root.addEventListener('blur', () => { if (state.popoverId) closePopover(); });" in src


# ---------------------------------------------------------------------------
# browser: shell controller
# ---------------------------------------------------------------------------
SHELL_FIXTURE = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<link rel="stylesheet" href="/css/app_shell.css"><link rel="stylesheet" href="/css/gizmos.css"></head>
<body class="is-electron">
<div class="shell-titlebar" id="shellTitlebar"><div class="shell-titlebar-drag" id="shellTitlebarDrag">
<span class="shell-titlebar-label">Cuttle</span></div></div>
<div class="app-shell"><div class="split-container split-horizontal">
<div class="split-column" data-column="0" style="height:600px">
<nav class="icon-rail"><div class="rail-items" style="flex:1"></div><div class="rail-footer"></div></nav>
<main class="shell-main"><iframe srcdoc="<p>pane</p>"></iframe></main></div></div></div>
<script>window.navigate = (col, page) => { window.__navigated = page; };</script>
<script src="/js/gizmos/gizmos_model.js"></script><script src="/js/gizmos/gizmos_shell.js"></script>
</body></html>"""


def test_shell_controller_docks_drags_and_removes(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    now = int(time.time())
    state = {
        "enabled": True, "revision": 1, "patches": [], "deletes": [],
        "gizmos": [
            {"id": "codex-m", "type": "usage_meter", "title": "Codex usage",
             "config": {"agent": "codex", "window": "tightest", "show": "remaining"},
             "placement": {"dock": "titlebar", "order": 0}, "created_by": "ui"},
            {"id": "claude-m", "type": "usage_meter", "title": "Claude Code usage",
             "config": {"agent": "claude", "window": "tightest", "show": "remaining"},
             "placement": {"dock": "float", "order": 0, "x": 0.5, "y": 0.5}, "created_by": "agent"},
        ],
    }
    usage = {"label": "Codex", "plan": "plus", "blocked": False, "updated_at": now, "windows": [
        {"id": "five_hour", "label": "5-hour", "used_percent": 70, "remaining_percent": 30, "reset_at": now + 3600}]}
    types = [{"id": "usage_meter", "label": "Usage meter", "options": {
        "agents": [{"agent": "codex", "label": "Codex"}, {"agent": "claude", "label": "Claude Code"}]}}]

    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page(viewport={"width": 1200, "height": 700})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())

        def route(route):
            from urllib.parse import urlparse
            req = route.request
            path = urlparse(req.url).path
            if path == "/fixture.html":
                return route.fulfill(body=SHELL_FIXTURE, content_type="text/html")
            if path == "/api/gizmos":
                if not state["enabled"]:
                    return route.fulfill(json={"success": False, "disabled": True})
                return route.fulfill(json={"success": True, "revision": state["revision"],
                                           "gizmos": state["gizmos"], "types": types})
            if path.startswith("/api/gizmos/") and path.endswith("/data"):
                return route.fulfill(json={"success": True, "data": usage})
            if path.startswith("/api/gizmos/"):
                gid = path.rsplit("/", 1)[1]
                gizmo = next((g for g in state["gizmos"] if g["id"] == gid), None)
                if req.method == "PATCH":
                    body = json.loads(req.post_data or "{}")
                    state["patches"].append((gid, body))
                    gizmo["placement"] = {**gizmo["placement"], **body.get("placement", {})}
                    gizmo["config"] = {**gizmo["config"], **body.get("config", {})}
                    state["revision"] += 1
                    return route.fulfill(json={"success": True, "gizmo": gizmo})
                if req.method == "DELETE":
                    state["deletes"].append(gid)
                    state["gizmos"] = [g for g in state["gizmos"] if g["id"] != gid]
                    state["revision"] += 1
                    return route.fulfill(json={"success": True})
                return route.fulfill(json={"success": True, "gizmo": gizmo})
            file = WEB / path.lstrip("/")
            if file.is_file():
                ctype = {".js": "application/javascript", ".css": "text/css"}.get(file.suffix, "text/plain")
                return route.fulfill(body=file.read_bytes(), content_type=ctype)
            return route.fulfill(status=404, body="missing")

        page.route("**/*", route)
        page.goto("http://cuttle.test/fixture.html")

        titlebar = page.locator("#shellGizmoDockTitlebar .gizmo--titlebar")
        titlebar.wait_for()
        page.wait_for_function("document.querySelector('#shellGizmoDockTitlebar .gizmo-value').textContent === '30%'")
        assert page.locator("#shellGizmoFloatLayer .gizmo--float").count() == 1
        assert page.locator("#shellGizmoDockRail .shell-gizmo").count() == 0

        # Click-off works both in the shell and across the iframe boundary;
        # clicking a control inside the popover must leave it open.
        page.locator("#shellGizmoFloatLayer .shell-gizmo").click()
        popover = page.locator("#shellGizmoPopover")
        popover.wait_for(state="visible")
        popover.locator('select[data-gizmo-field="show"]').focus()
        assert popover.is_visible()
        page.frame_locator('iframe').locator('p').click(position={"x": 10, "y": 5})
        popover.wait_for(state="hidden")
        page.locator("#shellGizmoFloatLayer .shell-gizmo").click()
        popover.wait_for(state="visible")
        page.locator('.shell-titlebar-label').click()
        popover.wait_for(state="hidden")

        # Drag the title-bar meter onto the blade bar.
        box = titlebar.bounding_box()
        rail = page.locator(".icon-rail").bounding_box()
        page.mouse.move(box["x"] + 10, box["y"] + 10)
        page.mouse.down()
        page.mouse.move(box["x"] - 40, box["y"] + 120, steps=6)
        page.mouse.move(rail["x"] + 20, rail["y"] + 200, steps=6)
        page.mouse.up()
        page.locator("#shellGizmoDockRail .gizmo--rail").wait_for()
        assert state["patches"][-1][0] == "codex-m"
        assert state["patches"][-1][1]["placement"]["dock"] == "rail"
        assert page.locator("#shellGizmoDockTitlebar .shell-gizmo").count() == 0

        # Click opens the popover; move the floating meter to the title bar from it.
        page.locator("#shellGizmoFloatLayer .shell-gizmo").click()
        page.locator("#shellGizmoPopover:not([hidden])").wait_for()
        assert "5-hour" in page.locator("#shellGizmoPopover").inner_text()
        page.locator('#shellGizmoPopover [data-dock="titlebar"]').click()
        page.locator("#shellGizmoDockTitlebar .gizmo--titlebar").wait_for()
        assert state["patches"][-1] == ("claude-m", {"placement": {"dock": "titlebar"}})

        # Right-click → change agent → remove.
        page.locator("#shellGizmoDockRail .shell-gizmo").click(button="right")
        page.locator('#shellGizmoPopover select[data-gizmo-field="show"]').select_option("used")
        page.wait_for_function("document.querySelector('#shellGizmoDockRail .gizmo-ring-value').textContent === '70'")
        page.locator("#shellGizmoDockRail .shell-gizmo").click(button="right")
        page.locator('#shellGizmoPopover [data-gizmo-action="remove"]').click()
        page.wait_for_function("!document.querySelector('#shellGizmoDockRail .shell-gizmo')")
        assert state["deletes"] == ["codex-m"]

        page.screenshot(path=str(tmp_path / "gizmos-shell.png"))

        # Turning the flag off clears every dock on the next list refresh.
        state["enabled"] = False
        page.evaluate("window.postMessage({type: 'cuttle-experimental-flags-changed'}, '*')")
        page.wait_for_function("!document.querySelector('.shell-gizmo')")
        assert not errors
        browser.close()


def test_gizmos_app_creates_without_an_agent(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    state = {"enabled": False, "gizmos": [], "revision": 0, "created": []}
    types = [{"id": "usage_meter", "label": "Usage meter", "options": {
        "agents": [{"agent": "codex", "label": "Codex"}, {"agent": "claude", "label": "Claude Code"}]}}]
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page(viewport={"width": 1000, "height": 760})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(route):
            from urllib.parse import urlparse
            req = route.request
            path = urlparse(req.url).path
            if path == "/api/gizmos" and req.method == "POST":
                body = json.loads(req.post_data or "{}")
                state["created"].append(body)
                gizmo = {"id": "usage-meter-1", "type": body["type"], "title": "Claude Code usage",
                         "config": {"agent": body["config"]["agent"], "window": "tightest", "show": "remaining"},
                         "placement": {"dock": body["placement"]["dock"], "order": 0}, "created_by": "ui"}
                state["gizmos"].append(gizmo)
                state["revision"] += 1
                return route.fulfill(status=201, json={"success": True, "gizmo": gizmo})
            if path == "/api/gizmos":
                if not state["enabled"]:
                    return route.fulfill(json={"success": False, "disabled": True})
                return route.fulfill(json={"success": True, "revision": state["revision"],
                                           "gizmos": state["gizmos"], "types": types})
            if path.endswith("/data"):
                return route.fulfill(json={"success": True, "data": {"label": "Claude Code", "windows": []}})
            if path.startswith("/api/"):
                return route.fulfill(json={})
            file = WEB / path.lstrip("/")
            if file.is_file():
                ctype = {".js": "application/javascript", ".css": "text/css", ".html": "text/html"}.get(file.suffix, "text/plain")
                return route.fulfill(body=file.read_bytes(), content_type=ctype)
            return route.fulfill(status=404, body="missing")

        page.route("**/*", route)
        page.goto("http://cuttle.test/gizmos_page.html")
        page.get_by_text("Gizmos is disabled.", exact=False).wait_for()
        assert page.locator("#gizmosCreate").is_hidden()
        state["enabled"] = True
        page.locator("#refreshGizmos").click()
        page.locator("#gizmosCreate").wait_for(state="visible")
        page.locator("#gizmoCreateAgent").select_option("claude")
        page.locator("#gizmoCreateDock").select_option("float")
        page.locator("#gizmosCreateForm button[type=submit]").click()
        page.locator('[data-gizmo-item="usage-meter-1"]').wait_for()
        assert state["created"] == [{"type": "usage_meter", "placement": {"dock": "float"},
                                     "config": {"agent": "claude"}}]
        page.screenshot(path=str(tmp_path / "gizmos-app.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        browser.close()
