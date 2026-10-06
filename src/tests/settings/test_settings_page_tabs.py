"""Settings page category tabs (src/web/settings_page.html).

Two layers:

* **Markup contract** (always runs): every `.settings-tab[data-tab]` owns a
  matching `#panel-<id>`, aria wiring is closed, ids are unique, only the
  pre-activation panel is visible, and the fetch-backed loaders are registered
  per tab instead of all firing on page load. That last one is the point of the
  split — a phone opening Settings must not pull API keys, LAN status, TTS
  prefs, and the local model list in one burst.
* **Behavior** (node): tab switching, `?tab=` deep link, localStorage memory,
  once-only loaders, and keyboard navigation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PAGE = REPO_ROOT / "src" / "web" / "settings_page.html"
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "settings/settings_page_tabs.js"
MOD_CSS = REPO_ROOT / "src" / "web" / "css" / "settings_page.css"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


# ── markup ────────────────────────────────────────────────────────────────


def _tab_tags(html: str) -> list[str]:
    return re.findall(r"<button[^>]*class=\"[^\"]*\bsettings-tab\b[^\"]*\"[^>]*>", html)


def _tab_ids_any_order(html: str) -> list[str]:
    """Attribute order is not fixed by the tab shell, so match per tag."""
    return [
        m.group(1)
        for tag in _tab_tags(html)
        if (m := re.search(r'data-tab="([^"]+)"', tag))
    ]


def test_every_tab_has_a_panel():
    html = _page()
    tabs = _tab_ids_any_order(html)
    assert tabs, "settings tab strip missing"
    for tid in tabs:
        assert f'id="panel-{tid}"' in html, f"tab '{tid}' has no #panel-{tid}"


def test_tabs_and_panels_line_up_in_order():
    html = _page()
    tabs = _tab_ids_any_order(html)
    panels = re.findall(r'id="panel-([a-z-]+)"', html)
    assert tabs == panels, "tab order and panel order must match so arrows move together"


def test_aria_wiring_is_closed():
    html = _page()
    for tid in _tab_ids_any_order(html):
        tab_tag = next(
            tag
            for tag in re.findall(r"<button[^>]*settings-tab[^>]*>", html)
            if f'data-tab="{tid}"' in tag
        )
        assert f'aria-controls="panel-{tid}"' in tab_tag
        assert f'id="tab-{tid}"' in tab_tag
        assert 'role="tab"' in tab_tag
        panel = re.search(rf'<section[^>]*id="panel-{tid}"[^>]*>', html).group(0)
        assert 'role="tabpanel"' in panel
        assert f'aria-labelledby="tab-{tid}"' in panel


def test_only_the_pre_activation_panel_starts_visible():
    """General renders even if the tab script never loads."""
    html = _page()
    visible = [
        tid
        for tid in _tab_ids_any_order(html)
        if "hidden" not in re.search(rf"<section[^>]*id=\"panel-{tid}\"[^>]*>", html).group(0)
    ]
    assert visible == ["general"]
    general = re.search(r'<section[^>]*id="panel-general"[^>]*>', html).group(0)
    assert re.search(r'class="[^"]*\bactive\b[^"]*"', general), (
        "panel-general needs class=active as the no-JS fallback"
    )


def test_page_ids_are_unique():
    ids = re.findall(r'\sid="([^"]+)"', _page())
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate element ids: {dupes}"


def test_sections_are_balanced():
    class Balance(HTMLParser):
        VOID = {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
        }

        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []
            self.errors: list[tuple] = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack:
                self.errors.append(("close-without-open", tag))
                return
            if self.stack[-1] != tag:
                self.errors.append(("mismatch", tag, self.stack[-1]))
                if tag in self.stack:
                    while self.stack and self.stack[-1] != tag:
                        self.stack.pop()
                    self.stack.pop()
                return
            self.stack.pop()

    parser = Balance()
    parser.feed(_page())
    assert parser.errors == []
    assert parser.stack == [], f"unclosed tags: {parser.stack}"


# ── lazy loading ──────────────────────────────────────────────────────────

FETCH_LOADERS = {
    "loadApiKeys": "providers",
    "loadCompletionProviders": "providers",
    "loadLanAccessSettings": "devices",
    "loadDesktopElectronSettings": "devices",
    "loadChatTtsSettings": "voice",
    "loadAgentCatalog": "agents",
    "loadGitHubAppSettings": "account",
}

# Wallpaper sync paints this page, not just the Appearance panel, so it stays
# eager — the tabs split must not leave a stale playlist behind.
EAGER_ON_LOAD = ("syncVideoBackgroundWithServer",)


def _dom_ready_body(html: str) -> str:
    match = re.search(
        r"document\.addEventListener\('DOMContentLoaded'.*?\n        \}\);", html, re.S
    )
    assert match, "DOMContentLoaded bootstrap not found"
    return match.group(0)


def test_fetch_loaders_run_only_when_their_tab_opens():
    body = _dom_ready_body(_page())
    for fn, tab in FETCH_LOADERS.items():
        assert f"tabs.register('{tab}'" in body, "no registration block for a tab"
        registrations = re.findall(
            r"tabs\.register\('([a-z]+)', function \(\) \{(.*?)\}\);", body, re.S
        )
        owners = [tid for tid, src in registrations if fn in src]
        assert owners == [tab], f"{fn} should be lazy-loaded by the '{tab}' tab, got {owners}"


def test_every_lazy_loader_is_registered_and_tab_id_exists():
    html = _page()
    body = _dom_ready_body(html)
    tabs = _tab_ids_any_order(html)
    for fn, tab in FETCH_LOADERS.items():
        assert re.search(
            rf"tabs\.register\('{tab}', function \(\) \{{[^}}]*{fn}\(", body
        ), f"{fn} is not registered on the '{tab}' tab"
    # Registration must be paired with an init(), or nothing ever activates.
    assert "tabs.init();" in body
    assert "CuttleSettingsTabs" in html
    assert "/js/settings/settings_page_tabs.js" in html
    for tab in ("agents", "providers", "voice", "devices"):
        assert tab in tabs


def test_wallpaper_sync_stays_eager():
    """It paints the page background; deferring it shows a stale playlist."""
    body = _dom_ready_body(_page())
    for fn in EAGER_ON_LOAD:
        assert fn in body
        assert not re.search(rf"tabs\.register\('[a-z]+', function \(\) \{{[^}}]*{fn}\(", body)


def test_no_js_fallback_still_loads_everything():
    """If the shell is missing, the page must not silently show empty controls."""
    body = _dom_ready_body(_page())
    fallback = body.split("} else {", 1)[-1]
    for fn in FETCH_LOADERS:
        assert fn in fallback, f"{fn} missing from the no-tab-shell fallback"


def test_tab_shell_and_css_are_linked():
    html = _page()
    assert "css/settings_page.css" in html
    assert "/js/settings/settings_page_tabs.js" in html
    css = MOD_CSS.read_text(encoding="utf-8")
    assert ".settings-tabs" in css
    assert ".settings-panel.active" in css
    # Panel visibility is CSS-driven; `hidden` alone would be ignored by the
    # display:block rule the active panel relies on.
    assert re.search(r"\.settings-panel\s*\{[^}]*display:\s*none", css)


def test_scope_badges_say_which_world_a_setting_lives_in():
    """Two storage worlds on one page; every group says which one it writes."""
    html = _page()
    groups = re.findall(r'<h3 class="settings-title">(.*?)</h3>', html, re.S)
    assert groups
    for group in groups:
        assert 'class="settings-scope"' in group, (
            f"group without a scope badge: {re.sub(r'<[^>]+>', '', group).strip()!r}"
        )
    for scope in ("device", "server", "mixed"):
        assert f'data-scope="{scope}"' in html


# ── behavior (node) ───────────────────────────────────────────────────────

HARNESS = r"""
const fs = require('fs');
const path = process.env.MOD_JS;

// ── minimal DOM ───────────────────────────────────────────────────────────
let lastFocused = null;
function El(tag, attrs) {
  this.tag = tag; this.attrs = attrs || {}; this.classes = new Set();
  this.children = []; this.listeners = {}; this.tabIndex = 0; this.hidden = false;
}
El.prototype.getAttribute = function (k) { return this.attrs[k] === undefined ? null : this.attrs[k]; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
// classList must bind to the *instance*, not the prototype.
Object.defineProperty(El.prototype, 'classList', {
  get: function () {
    const self = this;
    return {
      toggle: function (name, on) { if (on) self.classes.add(name); else self.classes.delete(name); },
      add: function (n) { self.classes.add(n); },
      remove: function (n) { self.classes.delete(n); },
      contains: function (n) { return self.classes.has(n); },
    };
  },
});
El.prototype.addEventListener = function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); };
El.prototype.contains = function (node) {
  if (node === this) return true;
  return this.children.some(function (c) { return c.contains(node); });
};
El.prototype.focus = function () { this.focused = true; lastFocused = this.getAttribute('data-tab'); };
El.prototype.matches = function (sel) {
  const attr = /\[([a-z-]+)\]$/.exec(sel);
  if (attr) return this.getAttribute(attr[1]) !== null;
  const cls = /\.([a-z-]+)/.exec(sel);
  return !!(cls && this.classes.has(cls[1]));
};
El.prototype.closest = function (sel) {
  let node = this;
  while (node) { if (node.matches && node.matches(sel)) return node; node = node.parent; }
  return null;
};
// Events bubble: the strip owns the delegated listener, not the buttons.
El.prototype.click = function () {
  const e = { type: 'click', target: this, preventDefault: function () {} };
  let node = this;
  while (node) {
    (node.listeners['click'] || []).forEach(function (fn) { fn(e); });
    node = node.parent;
  }
};
El.prototype.fireKey = function (key) {
  const e = { key: key, preventDefault: function () {} };
  (this.listeners['keydown'] || []).forEach(function (fn) { fn(e); });
};

const IDS = ['general', 'appearance', 'agents', 'providers', 'voice', 'devices', 'data'];
function buildDom() {
  const strip = new El('div');
  strip.attrs = { class: 'settings-tabs' };
  const buttons = IDS.map(function (id) {
    const b = new El('button', { 'data-tab': id });
    b.parent = strip;
    strip.children.push(b);
    return b;
  });
  const panels = {};
  IDS.forEach(function (id) { panels[id] = new El('section', { id: 'panel-' + id }); });
  return { strip: strip, buttons: buttons, panels: panels };
}

function loadModule(dom, search, stored) {
  global.window = {
    location: { search: search || '', href: 'http://x/settings_page.html' + (search || ''),
                pathname: '/settings_page.html', hash: '' },
    history: { replaceState: function (_s, _t, url) { global.window.__url = url; } },
  };
  global.URL = URL; global.URLSearchParams = URLSearchParams;
  global.document = {
    _dom: dom,
    addEventListener: function () {},
    querySelector: function (sel) {
      if (sel === '.settings-tabs') return dom.strip;
      if (sel === '.settings-tab[data-tab]') return dom.buttons;
      const m = /\[data-tab="([^"]+)"\]$/.exec(sel);
      if (m) return dom.buttons.filter(function (b) { return b.getAttribute('data-tab') === m[1]; })[0] || null;
      return null;
    },
    querySelectorAll: function (sel) {
      if (sel === '.settings-tab[data-tab]') return dom.buttons;
      return [];
    },
    getElementById: function (id) {
      const m = /^panel-(.+)$/.exec(id);
      return m && dom.panels[m[1]] ? dom.panels[m[1]] : null;
    },
  };
  global.localStorage = {
    _v: stored ? { cuttleSettingsTab: stored } : {},
    getItem: function (k) { return this._v[k] === undefined ? null : this._v[k]; },
    setItem: function (k, v) { this._v[k] = String(v); },
  };
  delete global.window.CuttleSettingsTabs;
  delete require.cache[require.resolve(path)];
  eval(fs.readFileSync(path, 'utf8'));
  return global.window.CuttleSettingsTabs;
}

const out = {};
function state(dom, id) {
  return {
    visible: IDS.filter(function (t) { return dom.panels[t].hidden === false; }),
    active: IDS.filter(function (t) {
      return dom.buttons.filter(function (b) { return b.getAttribute('data-tab') === t; })
        .some(function (b) { return b.classes.has('active'); });
    }),
    aria: dom.buttons.map(function (b) { return b.getAttribute('aria-selected'); }),
    tab: id,
  };
}

// 1. No URL param, no memory -> first tab.
let dom = buildDom();
let T = loadModule(dom, '', null);
T.init();
out.first = state(dom, T.currentTab());

// 2. Register loaders; only the active tab's loader runs, and only once.
let ran = [];
dom = buildDom();
T = loadModule(dom, '', null);
['general', 'appearance', 'agents', 'providers', 'voice', 'devices', 'data'].forEach(function (id) {
  T.register(id, function () { ran.push(id); });
});
T.init();
out.ranOnInit = ran.slice();
T.setTab('devices');
T.setTab('general');
T.setTab('devices');
out.ranAfterCycling = ran.slice();

// 3. ?tab= deep link wins over memory.
dom = buildDom();
T = loadModule(dom, '?tab=providers', 'data');
T.init();
out.deepLink = state(dom, T.currentTab());

// 4. Unknown ?tab= falls back to memory, then to the first tab.
dom = buildDom();
T = loadModule(dom, '?tab=nope', 'voice');
T.init();
out.unknownWithMemory = T.currentTab();
dom = buildDom();
T = loadModule(dom, '?tab=nope', null);
T.init();
out.unknownNoMemory = T.currentTab();

// 5. Click a tab button.
dom = buildDom();
T = loadModule(dom, '', null);
T.init();
dom.buttons[2].click();
out.click = state(dom, T.currentTab());

// 6. Keyboard: Arrow, Home, End (End used to index `tabIds.length`, a function).
dom = buildDom();
T = loadModule(dom, '', null);
T.init();
dom.strip.fireKey('ArrowRight');
const afterRight = T.currentTab();
dom.strip.fireKey('ArrowLeft');
const backLeft = T.currentTab();
dom.strip.fireKey('End');
const afterEnd = T.currentTab();
dom.strip.fireKey('Home');
out.keys = { afterRight: afterRight, backLeft: backLeft, afterEnd: afterEnd, afterHome: T.currentTab() };
// Roving tabindex: the keyboard must land on the button it just selected, not
// leave focus stranded on the one it came from.
out.focusedAfterKeys = lastFocused;

// 7. Wrap-around from the first tab.
dom = buildDom();
T = loadModule(dom, '', null);
T.init();
dom.strip.fireKey('ArrowLeft');
out.wrapBack = T.currentTab();

// 8. A loader that throws must not break the switch.
dom = buildDom();
T = loadModule(dom, '', null);
let reached = false;
T.register('voice', function () { throw new Error('boom'); });
T.register('voice', function () { reached = true; });
T.init();
T.setTab('voice');
out.loaderSurvivedThrow = { reached: reached, tab: T.currentTab() };

// 9. refresh() re-runs a tab's loaders.
dom = buildDom();
T = loadModule(dom, '', null);
let refreshRuns = 0;
T.register('data', function () { refreshRuns += 1; });
T.init();
T.setTab('data');
const afterFirst = refreshRuns;
T.refresh('data');
out.refresh = { afterFirst: afterFirst, afterRefresh: refreshRuns };

// 10. init() is idempotent (a second call must not double-bind the strip).
dom = buildDom();
T = loadModule(dom, '', null);
T.init();
T.init();
dom.buttons[3].click();
out.doubleInit = T.currentTab();

process.stdout.write(JSON.stringify(out));
"""


def _run() -> dict:
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_default_tab_is_general():
    res = _run()
    assert res["first"] == {
        "visible": ["general"],
        "active": ["general"],
        "aria": ["true", "false", "false", "false", "false", "false", "false"],
        "tab": "general",
    }


@node_only
def test_only_the_visible_tab_loads_and_only_once():
    res = _run()
    assert res["ranOnInit"] == ["general"]
    assert res["ranAfterCycling"] == ["general", "devices"]


@node_only
def test_url_param_beats_memory_and_bad_params_fall_back():
    res = _run()
    assert res["deepLink"]["tab"] == "providers"
    assert res["deepLink"]["visible"] == ["providers"]
    assert res["deepLink"]["aria"] == [
        "false", "false", "false", "true", "false", "false", "false",
    ]
    assert res["unknownWithMemory"] == "voice"
    assert res["unknownNoMemory"] == "general"


@node_only
def test_click_switches_panels():
    res = _run()
    assert res["click"]["tab"] == "agents"
    assert res["click"]["visible"] == ["agents"]
    assert res["click"]["active"] == ["agents"]


@node_only
def test_keyboard_navigation():
    res = _run()
    assert res["keys"] == {
        "afterRight": "appearance",
        "backLeft": "general",
        "afterEnd": "data",  # End jumps to the last real tab
        "afterHome": "general",
    }
    assert res["wrapBack"] == "data"
    # Roving tabindex: focus lands on the tab the key selected, not the old one.
    assert res["focusedAfterKeys"] == "general"


@node_only
def test_loader_failure_does_not_break_the_tab_switch():
    res = _run()
    assert res["loaderSurvivedThrow"] == {"reached": True, "tab": "voice"}


@node_only
def test_refresh_reruns_loaders_and_init_is_idempotent():
    res = _run()
    assert res["refresh"]["afterFirst"] == 1
    assert res["refresh"]["afterRefresh"] == 2
    assert res["doubleInit"] == "providers"