"""Apps grid lists every rail icon, including footer utilities.

The left rail footer (Settings, Account, Notifications, Workspace) lives
outside the pinnable rail order, but the Apps page must still show those
icons. Footer entries without a page carry an `action` id; the Apps page
asks the shell to activate the matching rail button (`cuttle-rail-action`)
instead of navigating.
"""
import shutil
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

REPO = Path(__file__).resolve().parents[3]
SHELL_SRC = REPO / "src/web/js/shell/app_shell.js"
APPS_SRC = REPO / "src/web/js/shell/apps_page.js"


def _extract(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _shell_prelude():
    source = SHELL_SRC.read_text(encoding="utf-8")
    order = _extract(source, "const CANONICAL_RAIL_ITEM_ORDER =", "];") + "];"
    locked_start = source.index("const RAIL_LOCKED_IDS =")
    locked = source[locked_start: source.index("\n", locked_start)]
    footer_ids = _extract(source, "const RAIL_FOOTER_APP_IDS =", "];") + "];"
    get_apps = _extract(source, "function getAppsList()", "function broadcastAppsList")
    # getAppsList reads the shell's agent-feed flag; default it off for the harness.
    return "\n".join(["let agentFeedEnabled = false;", order, locked, footer_ids, get_apps])


SHELL_STUB = """
function makeBtn(id, cfg) {
    const dataset = { id, tooltip: cfg.tooltip };
    if (cfg.page) dataset.page = cfg.page;
    return {
        dataset,
        querySelector: () => ({ outerHTML: '<svg></svg>' }),
        closest: (sel) => (sel === '.rail-items' && cfg.container === 'rail-items')
            ? {} : null,
    };
}
const BUTTONS = {
    'nav-chat': makeBtn('nav-chat', { container: 'rail-items', tooltip: 'Chat', page: '/chat_page.html' }),
    'nav-git': makeBtn('nav-git', { container: 'rail-stash', tooltip: 'Git', page: '/git_graph_page.html' }),
    'nav-apps': makeBtn('nav-apps', { container: 'rail-items', tooltip: 'Apps', page: '/apps_page.html' }),
    'nav-account': makeBtn('nav-account', { container: 'rail-footer', tooltip: 'Account' }),
    'nav-notifications': makeBtn('nav-notifications', { container: 'rail-footer', tooltip: 'Notifications' }),
    'nav-workspace': makeBtn('nav-workspace', { container: 'rail-footer', tooltip: 'Workspace' }),
    'nav-settings': makeBtn('nav-settings', { container: 'rail-footer', tooltip: 'Settings', page: '/settings_page.html' }),
};
const fakeCol = {
    querySelector: (sel) => {
        const m = /data-id="([^"]+)"/.exec(sel);
        if (!m) return null;
        const btn = BUTTONS[m[1]];
        if (!btn) return null;
        if (sel.includes('.rail-footer')) {
            return m[1].startsWith('nav-') && ['nav-account', 'nav-notifications', 'nav-workspace', 'nav-settings'].includes(m[1]) ? btn : null;
        }
        return ['nav-chat', 'nav-git', 'nav-apps'].includes(m[1]) ? btn : null;
    },
};
function getColumnEl() { return fakeCol; }
"""


@node_only
def test_apps_list_includes_footer_utilities():
    import json
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += _shell_prelude() + "\n" + SHELL_STUB
    script += """
const apps = getAppsList();
const byId = Object.fromEntries(apps.map(a => [a.id, a]));
for (const id of ['nav-settings', 'nav-account', 'nav-notifications', 'nav-workspace']) {
    assert.ok(byId[id], id + ' missing from Apps list');
    assert.equal(byId[id].pinned, true);
    assert.equal(byId[id].pinnable, false);
}
assert.equal(byId['nav-settings'].page, '/settings_page.html');
assert.ok(!('action' in byId['nav-settings']));
for (const id of ['nav-account', 'nav-notifications', 'nav-workspace']) {
    assert.equal(byId[id].action, id);
    assert.ok(!('page' in byId[id]), id + ' must not carry a page');
}
assert.ok(!byId['nav-apps'], 'Apps launcher stays out of its own grid');
assert.ok(!byId['panelToggle'], 'collapse toggle is view chrome, not an app');
assert.equal(byId['nav-git'].page, '/git_graph_page.html');
assert.equal(byId['nav-git'].pinned, false);
const labels = apps.map(a => a.label);
assert.deepEqual(labels, [...labels].sort((a, b) => a.localeCompare(b)));
"""
    subprocess.run(["node", "-e", script], check=True)


APPS_STUB = """
const posted = [];
const winHandlers = {};
const docHandlers = {};
function makeEl(tag) {
    return {
        tag, children: [], dataset: {},
        style: { _p: {}, setProperty(k, v) { this._p[k] = v; } },
        classList: { add() {}, remove() {} },
        handlers: {},
        textContent: '', innerHTML: '', hidden: true, title: '', value: '',
        offsetWidth: 120, offsetHeight: 60,
        addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
        append(...c) { this.children.push(...c); },
        appendChild(c) { this.children.push(c); return c; },
        setAttribute() {}, focus() {},
        getBoundingClientRect() { return { left: 10, top: 10, width: 20, bottom: 30 }; },
        replaceChildren(...c) { this.children = c; },
        contains() { return false; },
    };
}
const els = {};
for (const id of ['appsGrid', 'appsEmpty', 'appsSearch', 'appsSummary', 'appsMenu', 'appsStatus']) {
    els[id] = makeEl('div');
}
const parentWindow = { postMessage: (msg) => posted.push(msg) };
const window = {
    parent: parentWindow,
    location: { href: '' },
    addEventListener(t, f) { winHandlers[t] = f; },
    get innerWidth() { return 800; },
    get innerHeight() { return 600; },
};
const document = {
    getElementById: (id) => els[id],
    createElement: (t) => makeEl(t),
    addEventListener(t, f) { docHandlers[t] = f; },
};
"""


@node_only
def test_apps_list_account_tile_forwards_profile_photo():
    """The Account Apps tile carries the rail avatar (photo or initials)."""
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += _shell_prelude() + "\n"
    script += """
function makeBtn(id, cfg) {
    const dataset = { id, tooltip: cfg.tooltip };
    if (cfg.page) dataset.page = cfg.page;
    return {
        dataset,
        querySelector: (sel) => {
            if (sel === '.rail-account-avatar' && cfg.avatarHtml != null) {
                return { innerHTML: cfg.avatarHtml };
            }
            return { outerHTML: '<svg></svg>' };
        },
        closest: (sel) => (sel === '.rail-items' && cfg.container === 'rail-items')
            ? {} : null,
    };
}
function colWith(avatarHtml) {
    const account = makeBtn('nav-account', { container: 'rail-footer', tooltip: 'Account', avatarHtml });
    return {
        querySelector: (sel) => {
            const m = /data-id="([^"]+)"/.exec(sel);
            if (!m || m[1] !== 'nav-account') return null;
            if (!sel.includes('.rail-footer')) return null;
            return account;
        },
    };
}
let _col = colWith('<img src="https://example.com/pic.jpg" alt="User">');
function getColumnEl() { return _col; }
let apps = getAppsList();
assert.ok(apps[0].icon.includes('<img'), 'photo forwarded, got: ' + apps[0].icon);
assert.ok(apps[0].icon.includes('https://example.com/pic.jpg'));
_col = colWith('AB');
apps = getAppsList();
assert.equal(apps[0].icon, 'AB', 'initials forwarded when no photo');
_col = colWith('<svg></svg>');
apps = getAppsList();
assert.ok(apps[0].icon.includes('<svg'), 'signed-out placeholder still forwarded');
"""
    subprocess.run(["node", "-e", script], check=True)


@node_only
def test_apps_page_opens_action_tiles_via_shell():
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += APPS_STUB + "\n"
    script += APPS_SRC.read_text(encoding="utf-8") + "\n"
    script += """
assert.deepEqual(posted[0], { type: 'cuttle-apps-request' });
const shellApps = [
    { id: 'nav-git', label: 'Git', page: '/git_graph_page.html', icon: '<svg></svg>', pinned: false },
    { id: 'nav-settings', label: 'Settings', page: '/settings_page.html', icon: '<svg></svg>', pinned: true, pinnable: false },
    { id: 'nav-notifications', label: 'Notifications', action: 'nav-notifications', icon: '<svg></svg>', pinned: true, pinnable: false },
    { id: 'bogus', label: 'Bogus' },
];
winHandlers['message']({ source: parentWindow, data: { type: 'cuttle-apps', apps: shellApps } });
const tiles = els['appsGrid'].children;
assert.equal(tiles.length, 3, 'action entries accepted, page-less non-action dropped');
const tileById = Object.fromEntries(tiles.map(t => [t.dataset.id, t]));
const click = (id) => tileById[id].handlers['click'][0]({ preventDefault() {} });
posted.length = 0;
click('nav-settings');
assert.deepEqual(posted[0], { type: 'cuttle-navigate', page: '/settings_page.html' });
posted.length = 0;
click('nav-notifications');
assert.deepEqual(posted[0], { type: 'cuttle-rail-action', id: 'nav-notifications' });
// Context menu: normal app offers Open + pin toggle; locked footer app offers Open only.
const fireMenu = (id) => {
    els['appsMenu'].children = [];
    els['appsMenu'].hidden = true;
    tileById[id].handlers['contextmenu'][0]({ preventDefault() {}, clientX: 40, clientY: 40 });
    return els['appsMenu'].children.filter(c => c.tag === 'button').map(c => c.textContent);
};
assert.deepEqual(fireMenu('nav-git'), ['Open', 'Add to blade bar']);
assert.deepEqual(fireMenu('nav-notifications'), ['Open']);
"""
    subprocess.run(["node", "-e", script], check=True)


APPS_AVATAR_STUB = """
const posted = [];
const winHandlers = {};
const docHandlers = {};
function makeEl(tag) {
    const added = [];
    return {
        tag, children: [], dataset: {},
        style: { _p: {}, setProperty(k, v) { this._p[k] = v; } },
        classList: {
            _added: added,
            add(c) { this._added.push(c); },
            remove() {},
            contains(c) { return this._added.includes(c); },
        },
        handlers: {},
        textContent: '', innerHTML: '', hidden: true, title: '', value: '',
        offsetWidth: 120, offsetHeight: 60,
        addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
        append(...c) { this.children.push(...c); },
        appendChild(c) { this.children.push(c); return c; },
        setAttribute() {}, focus() {},
        getBoundingClientRect() { return { left: 10, top: 10, width: 20, bottom: 30 }; },
        replaceChildren(...c) { this.children = c; },
        contains() { return false; },
    };
}
const els = {};
for (const id of ['appsGrid', 'appsEmpty', 'appsSearch', 'appsSummary', 'appsMenu', 'appsStatus']) {
    els[id] = makeEl('div');
}
const parentWindow = { postMessage: (msg) => posted.push(msg) };
const window = {
    parent: parentWindow,
    location: { href: '' },
    addEventListener(t, f) { winHandlers[t] = f; },
    get innerWidth() { return 800; },
    get innerHeight() { return 600; },
};
const document = {
    getElementById: (id) => els[id],
    createElement: (t) => makeEl(t),
    addEventListener(t, f) { docHandlers[t] = f; },
};
"""


@node_only
def test_apps_page_account_tile_uses_profile_photo():
    """The Account tile flags photo/initials icons so CSS fills the tile."""
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += APPS_AVATAR_STUB + "\n"
    script += APPS_SRC.read_text(encoding="utf-8") + "\n"
    script += """
const iconOf = (id) => els['appsGrid'].children
    .find(t => t.dataset.id === id).children[0];
winHandlers['message']({ source: parentWindow, data: { type: 'cuttle-apps', apps: [
    { id: 'nav-git', label: 'Git', page: '/git_graph_page.html', icon: '<svg></svg>', pinned: false },
    { id: 'nav-account', label: 'Account', action: 'nav-account',
      icon: '<img src="https://example.com/pic.jpg" alt="User">', pinned: true, pinnable: false },
] } });
assert.ok(iconOf('nav-account').classList.contains('has-photo'), 'photo tile flagged');
assert.ok(!iconOf('nav-git').classList.contains('has-photo'), 'svg tile untouched');
assert.ok(!iconOf('nav-git').classList.contains('has-initials'));
winHandlers['message']({ source: parentWindow, data: { type: 'cuttle-apps', apps: [
    { id: 'nav-account', label: 'Account', action: 'nav-account', icon: 'AB', pinned: true, pinnable: false },
] } });
assert.ok(iconOf('nav-account').classList.contains('has-initials'), 'initials tile flagged');
assert.ok(!iconOf('nav-account').classList.contains('has-photo'));
"""
    subprocess.run(["node", "-e", script], check=True)
