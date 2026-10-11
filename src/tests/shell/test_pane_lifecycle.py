"""Characterization fences for the shell's pane lifetime contracts."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHELL = REPO / "src" / "web" / "js" / "shell"


def test_navigation_preserves_page_url_and_history_contracts():
    source = (SHELL / "app_shell.js").read_text(encoding="utf-8")
    owner = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    assert "function navigate(colIdx, page, opts)" in owner
    assert "pushShellBackEntry(state.page)" in owner
    assert "syncShellChatUrl(cid, { mode: histMode, page })" in owner
    assert "function syncShellChatUrl(chatId, opts)" in owner
    assert "const shellBackStack = []" in owner
    assert "return paneController.navigate(colIdx, page, opts)" in source
    assert "paneController.applyShellHistoryState(page)" in source
    assert "navigate(0, page || '/chat_page.html?new=1', { history: 'none' })" in owner


def test_navigation_behavior_is_characterized_before_owner_move():
    """Pin navigation decisions and their effects against the current shell code."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        return
    source = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    script = r'''
const assert = require('node:assert/strict');
const calls = [];
const state = { page: '/chat_page.html?chat=7' };
global.window = {};
'''+source+r'''
const api = window.CuttleShellPanes.create({
 win: { location: { origin: 'https://cuttle.test', href: 'https://cuttle.test/' , pathname: '/' , search: '', hash: '' }, history: { replaceState: (...a) => calls.push(['url', ...a]) } }, doc: { addEventListener() {}, documentElement: { style: { setProperty() {} }, classList: { toggle() {} } }, querySelectorAll() { return []; } },
 fetch() {}, setTimeout, clearTimeout, URL, URLSearchParams, AbortController,
 getSplitColumns: () => [], getLiveFrame: () => null, findColumnIndexForSource: () => 0,
 columnState: new Map(), chatSurfaceKind: () => null, broadcastToFrames() {}, getColumnEl: () => null,
 getSplitColumnCount: () => 1, pauseAllShellPaneStreams() {}, pauseShellFrameStreams() {},
 stampComposerDraftScope() {}, withCacheBust: (x) => x, attachFrameLoadListener() {},
 localStorage: { getItem: () => null }, isSplitDiscardedEl: () => false, isCuttleMobileShell: () => false,
 withRememberedChat: (_i, page) => page, rememberChatHandleFromPage() {}, getState: () => state,
 pushShellBackEntry: (page) => calls.push(['back', page]), setState: (_i, page) => { state.page = page; calls.push(['state', page]); },
 storagePageKey: 'shell_page', sessionStorage: { setItem: (...a) => calls.push(['session', ...a]), getItem: () => null },
 updateColumnUI: (...a) => calls.push(['ui', ...a]), persistSplitLayout: () => calls.push(['persist']),
 desktopCfg: null,
});
api.replaceFrame = (...a) => { calls.push(['replace', ...a]); return {}; };
api.attachFrameLoadListener = () => calls.push(['listener']);
api.navigate(0, '/settings_page.html');
assert.equal(state.page, '/settings_page.html');
assert.ok(calls.some((x) => x[0] === 'state' && x[1] === '/settings_page.html'));
assert.ok(calls.some((x) => x[0] === 'url' && x[1].chatId === null));
assert.ok(calls.some((x) => x[0] === 'persist'));
assert.equal(api.popShellBack(), true);
assert.equal(state.page, '/chat_page.html?chat=7', 'back restores the prior pane page');
api.navigate(0, '/settings_page.html');
const count = calls.length;
api.navigate(0, '/settings_page.html');
assert.equal(calls.length, count, 'same-page navigation is a no-op');
'''
    subprocess.run(["node", "-"], input=script, text=True, encoding="utf-8", check=True)


def test_frame_lifetime_contracts_are_characterized():
    source = (SHELL / "app_shell.js").read_text(encoding="utf-8")
    owner = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    assert "function replaceFrameInPlace(colIdx, page)" in owner
    assert "if (getSplitColumnCount() > 1)" in owner
    assert "type: 'cuttle-pause-streams'" in source
    assert "type: 'cuttle-pane-teardown'" in owner
    assert "frame.srcdoc = '<!doctype html><title></title>'" in owner
    assert "paneController.replaceFrame(colIdx, page)" in source
    assert "e.data?.type === 'cuttle-page-ready'" in source


def test_frame_creation_and_teardown_are_owned_by_pane_controller():
    source = (SHELL / "app_shell.js").read_text(encoding="utf-8")
    owner = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    assert "function createContentFrame(colIdx)" in owner
    assert "function parkShellIframes(container)" in owner
    assert "function discardSplitSubtree(container)" in owner
    assert "return paneController.parkShellIframes(root)" in source
    assert "return paneController.discardSplitSubtree(root)" in source
    assert "paneController.createContentFrame(" in source


def test_cross_pane_message_contracts_are_characterized():
    source = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    source += (SHELL / "app_shell.js").read_text(encoding="utf-8")
    for message_type in (
        "cuttle-live-status",
        "cuttle-pending-changes-data",
        "cuttle-open-panes",
        "cuttle-pane-focus",
        "cuttle-open-panes-request",
    ):
        assert message_type in source
    assert "window.getOpenPaneSessions = getOpenPaneSessions" in source


def test_pane_hubs_keep_session_scope_and_fan_out():
    import shutil
    import subprocess

    if not shutil.which("node"):
        return
    source = (SHELL / "shell_panes.js").read_text(encoding="utf-8")
    script = "global.window = {};\n" + source + r'''
const assert = require('node:assert/strict');
const frames = new Map([[0, { messages: [], contentWindow: { messages: [], postMessage(m) { this.messages.push(m); } } }]]);
const columns = [{ dataset: { column: '0' } }];
const win = { location: { origin: 'https://cuttle.test' } };
const doc = { hidden: false, addEventListener() {}, querySelectorAll() { return []; } };
const api = window.CuttleShellPanes.create({
  win, doc, fetch: async () => ({ ok: true, json: async () => ({ success: true, statuses: { '42': { running: true } } }) }),
  setTimeout: (fn) => { fn(); return 1; }, clearTimeout() {}, URL, URLSearchParams, AbortController,
  getSplitColumns: () => columns, getLiveFrame: (i) => frames.get(i),
  findColumnIndexForSource: () => 0, columnState: new Map([[0, { page: '/chat_page.html' }]]),
  chatSurfaceKind: () => 'chat', broadcastToFrames: (m) => { frames.get(0).contentWindow.postMessage(m); },
  isChatPageFrame: () => true,
});
api.registerPaneChatSession(frames.get(0).contentWindow, '42');
assert.deepEqual(api.getOpenPaneSessions(), [{ sessionId: '42', pane: 1, column: 0, kind: 'chat', focused: true }]);
api.setFocusedColumn(0);
setTimeout(() => {
  assert.ok(frames.get(0).contentWindow.messages.some((m) => m.type === 'cuttle-live-status' && m.sessionId === '42'));
}, 0);
'''
    subprocess.run(["node", "-"], input=script, text=True, encoding="utf-8", check=True)
