/* ================================================================
   Cuttle App Shell — navigation, rail, split columns
   ================================================================ */

const shell = document.getElementById('appShell');
const splitContainer = document.getElementById('splitContainer');

// ── Pin document scroll (iOS/iPad input-focus scroll-into-view) ──
// Focusing a textarea inside the chat iframe can still nudge the
// *parent* document. Keep the shell pinned without resizing layout
// to the keyboard (that fight caused the bottom white strip).
(function pinShellDocumentScroll() {
    const pin = () => {
        if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
        if (document.documentElement.scrollTop) document.documentElement.scrollTop = 0;
        if (document.body.scrollTop) document.body.scrollTop = 0;
    };
    window.addEventListener('scroll', pin, { passive: true });
    document.addEventListener('focusin', pin);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', pin);
        window.visualViewport.addEventListener('scroll', pin);
    }
})();

// ── Restore rail & active page from session ──────────────────
const STORAGE_PAGE = 'shell_page';
const shellVoice = CuttleShellVoice.create({win:window, navigate:sid=>navigate(0,'/chat_page.html?chat='+encodeURIComponent(sid))});
const STORAGE_RAIL = 'shell_rail_collapsed';
const STORAGE_SPLITS = 'shell_split_count';
/** Durable multi-pane layout (survives browser/Electron close). */
const STORAGE_LAYOUT = 'shell_split_layout';

const _shellIsMobile = () => window.matchMedia('(max-width: 768px)').matches;
// Mobile defaults to collapsed rail for more chat space; desktop keeps prior preference.
let railCollapsed = _shellIsMobile()
    ? sessionStorage.getItem(STORAGE_RAIL) !== 'false'
    : sessionStorage.getItem(STORAGE_RAIL) === 'true';
/** True while recreate-from-localStorage is in progress (skip mid-loop persists). */
let _restoringLayout = false;
/** Shift/Alt/Ctrl held — rail +/× preview icons. */
let _railModShift = false;
let _railModAlt = false;
let _railModCtrl = false;
const _railHeightObservers = new WeakMap();

function normalizeSplitOrientation(raw) {
    return raw === 'vertical' ? 'vertical' : 'horizontal';
}

function isSplitGroupEl(el) {
    return !!(el && (el.id === 'splitContainer' || el.classList?.contains('split-group')));
}

function getGroupOrientation(groupEl) {
    if (!groupEl) return 'horizontal';
    if (groupEl.classList.contains('split-vertical')) return 'vertical';
    if (groupEl.classList.contains('split-horizontal')) return 'horizontal';
    return normalizeSplitOrientation(groupEl.dataset.orientation);
}

function applyGroupOrientation(groupEl, orientation) {
    if (!groupEl) return;
    const dir = normalizeSplitOrientation(orientation);
    groupEl.dataset.orientation = dir;
    groupEl.classList.toggle('split-vertical', dir === 'vertical');
    groupEl.classList.toggle('split-horizontal', dir === 'horizontal');
    if (groupEl.id === 'splitContainer') {
        groupEl.classList.add('split-group');
    }
}

function getLeafParentGroup(leafEl) {
    let p = leafEl && leafEl.parentElement;
    while (p && p !== document.body) {
        if (isSplitGroupEl(p)) return p;
        p = p.parentElement;
    }
    return splitContainer;
}

/** True if a pane/group is pending deferred teardown (must leave layout counts). */
function isSplitDiscardedEl(el) {
    return !!(el && (el.dataset?.splitDiscarded === '1' || el.dataset?.paneClosing === '1'));
}

/** Direct pane/group children of a split group (skips resize handles + discarded).
 *  Order follows DOM order — flex `order` is only a mirror assigned by
 *  rebuildGroupResizeHandles. Sorting by `order` here used to pull a freshly
 *  appended pane (no order yet → 0) between older siblings (0, 2, 4…), so
 *  primary `+` produced A–C–B instead of A–B–C.
 */
function getGroupChildNodes(groupEl) {
    if (!groupEl) return [];
    return Array.from(groupEl.children).filter((el) => (
        !isSplitDiscardedEl(el)
        && (el.classList.contains('split-column') || el.classList.contains('split-group'))
    ));
}

function newLeafId() {
    return 'leaf_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

function newGroupId() {
    return 'grp_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

function createSplitGroupEl(orientation) {
    const el = document.createElement('div');
    el.className = 'split-group';
    el.dataset.groupId = newGroupId();
    applyGroupOrientation(el, orientation);
    return el;
}

/**
 * Serialize nested layout from the live DOM (v2).
 * Leaves carry page/flex; groups carry orientation + children.
 */
function snapshotLayoutTree(nodeEl) {
    if (!nodeEl) return null;
    if (nodeEl.classList.contains('split-column')) {
        const idx = parseInt(nodeEl.dataset.column, 10);
        const state = columnState.get(idx);
        const handles = lastChatByColumn.get(idx) || {};
        const page = pageWithPaneSession(idx, (state && state.page) || '/chat_page.html');
        const entry = {
            type: 'leaf',
            id: nodeEl.dataset.leafId || newLeafId(),
            page,
            flex: nodeEl.style.flex || '',
            railCollapsed: !!state?.railCollapsed,
        };
        if (state && state.paneCollapsed) entry.collapsed = true;
        if (entry.collapsed && nodeEl.dataset.restoreEdge) entry.restoreEdge = nodeEl.dataset.restoreEdge;
        if (handles.chat) entry.chat = String(handles.chat);
        if (handles.terminal) entry.terminal = String(handles.terminal);
        if (!entry.chat || !entry.terminal) {
            try {
                const url = new URL(page, window.location.origin);
                const kind = chatSurfaceKind(url.pathname);
                const cid = url.searchParams.get('chat') || url.searchParams.get('session');
                if (kind && cid && !entry[kind]) entry[kind] = String(cid);
            } catch (_) {}
        }
        return entry;
    }
    if (isSplitGroupEl(nodeEl)) {
        const children = getGroupChildNodes(nodeEl).map(snapshotLayoutTree).filter(Boolean);
        if (children.length === 1 && nodeEl.id === 'splitContainer') {
            return children[0];
        }
        if (children.length === 0) {
            return {
                type: 'leaf',
                id: newLeafId(),
                page: '/chat_page.html',
                flex: '',
            };
        }
        if (children.length === 1) return children[0];
        return {
            type: 'group',
            id: nodeEl.dataset.groupId || newGroupId(),
            orientation: getGroupOrientation(nodeEl),
            flex: nodeEl.style.flex || '',
            children,
        };
    }
    return null;
}

/** Flatten tree leaves in DFS reading order (agent / workspace flat API). */
function flattenLayoutLeaves(node, out = []) {
    if (!node) return out;
    if (node.type === 'leaf') {
        out.push({
            page: node.page || '/chat_page.html',
            flex: node.flex || '',
            chat: node.chat,
            terminal: node.terminal,
            railCollapsed: node.railCollapsed,
        });
        return out;
    }
    if (node.type === 'group' && Array.isArray(node.children)) {
        node.children.forEach((ch) => flattenLayoutLeaves(ch, out));
    }
    return out;
}

function primaryOrientationFromTree(node) {
    if (!node) return 'horizontal';
    if (node.type === 'group') return normalizeSplitOrientation(node.orientation);
    return 'horizontal';
}

/** Migrate flat v1 { orientation, columns[] } → nested tree root. */
function flatLayoutToTree(columns, orientation) {
    const cols = Array.isArray(columns) ? columns : [];
    if (cols.length < 1) {
        return { type: 'leaf', id: newLeafId(), page: '/chat_page.html', flex: '' };
    }
    if (cols.length === 1) {
        const c = cols[0] || {};
        return {
            type: 'leaf',
            id: newLeafId(),
            page: c.page || '/chat_page.html',
            flex: c.flex || '',
            chat: c.chat,
            terminal: c.terminal,
            railCollapsed: c.railCollapsed,
        };
    }
    return {
        type: 'group',
        id: newGroupId(),
        orientation: normalizeSplitOrientation(orientation),
        flex: '',
        children: cols.map((c) => ({
            type: 'leaf',
            id: newLeafId(),
            page: (c && c.page) || '/chat_page.html',
            flex: (c && c.flex) || '',
            chat: c && c.chat,
            terminal: c && c.terminal,
            railCollapsed: c && c.railCollapsed,
        })),
    };
}

function readSplitLayout() {
    try {
        const raw = localStorage.getItem(STORAGE_LAYOUT);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object') return null;
        if (data.version >= 2 && data.root && typeof data.root === 'object') {
            return { version: 2, root: data.root };
        }
        if (Array.isArray(data.columns) && data.columns.length >= 1) {
            return {
                version: 2,
                root: flatLayoutToTree(data.columns, data.orientation),
                _migratedFromV1: true,
            };
        }
        return null;
    } catch (_) {
        return null;
    }
}

/** Reading-order leaves (DFS document order — nested groups included). */
function getSplitColumns() {
    if (!splitContainer) return [];
    return Array.from(splitContainer.querySelectorAll('.split-column'))
        .filter((c) => !isSplitDiscardedEl(c));
}

/** Embed a pane's last chat/terminal handle on the page URL when missing. */
function canonicalizeShellPage(page) {
    const fallback = '/chat_page.html';
    try {
        const u = new URL(page || fallback, window.location.origin);
        if (['/task_management.html', '/media_player.html', '/git_ui.html', '/wizard_page.html'].includes(u.pathname)) return fallback;
        u.searchParams.delete('_cb');
        let path = u.pathname || fallback;
        // Guard against blank / non-page navigations that surface as Werkzeug "Not Found".
        if (!path || path === '/' || path === '/index.html') path = fallback;
        if (!/\.html$/i.test(path)) path = fallback;
        return path + u.search + u.hash;
    } catch (_) {
        return fallback;
    }
}

function pageWithPaneSession(colIdx, page) {
    const fallback = canonicalizeShellPage(page || '/chat_page.html');
    try {
        const url = new URL(fallback, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return url.pathname + url.search + url.hash;
        if (!url.searchParams.get('chat') && !url.searchParams.get('session')) {
            const remembered = (lastChatByColumn.get(colIdx) || {})[kind];
            if (remembered) url.searchParams.set('chat', remembered);
        }
        url.searchParams.delete('_cb');
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return fallback;
    }
}

/** Strip chat/session from a page URL — used for brand-new panes so they never
 *  inherit another column's remembered session. */
function bareShellPage(page) {
    const fallback = canonicalizeShellPage(page || '/chat_page.html');
    try {
        const url = new URL(fallback, window.location.origin);
        url.searchParams.delete('chat');
        url.searchParams.delete('session');
        url.searchParams.delete('_cb');
        return url.pathname + (url.search || '') + (url.hash || '');
    } catch (_) {
        return '/chat_page.html';
    }
}

/**
 * Page URL for a layout-tree leaf. Trust the snapshot (already session-aware).
 * Do not re-apply lastChatByColumn — that contaminated blank panes when remumber
 * briefly duplicated data-column indices.
 */
function pageFromLayoutLeaf(node) {
    if (!node) return '/chat_page.html';
    let page = canonicalizeShellPage(node.page || '/chat_page.html');
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (kind && !url.searchParams.get('chat') && !url.searchParams.get('session')) {
            const handle = node[kind] || node.chat || node.terminal;
            if (handle) url.searchParams.set('chat', String(handle));
        }
        url.searchParams.delete('_cb');
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return page;
    }
}

/** Equal flex among direct children of a split group. */
function equalizeGroupChildren(groupEl) {
    if (!groupEl) return;
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    applySplitRatioFlex(
        cols,
        Array(cols.length).fill(1),
        getGroupOrientation(groupEl),
        groupEl
    );
}

function snapshotSplitColumns() {
    const tree = snapshotLayoutTree(splitContainer);
    return flattenLayoutLeaves(tree);
}

function persistSplitLayout() {
    if (_restoringLayout) return;
    try {
        const root = snapshotLayoutTree(splitContainer);
        const columns = flattenLayoutLeaves(root);
        const orientation = primaryOrientationFromTree(root);
        localStorage.setItem(STORAGE_LAYOUT, JSON.stringify({ version: 2, root, orientation, columns }));
        localStorage.setItem(STORAGE_SPLITS, String(columns.length));
        // Flat columns for agents ("1st pane" / "pane 2") — DFS reading order.
        try {
            fetch('/api/shell/panes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version: 1, orientation, columns }),
                keepalive: true,
            }).catch(() => {});
        } catch (_) {}
        scheduleBroadcastOpenPanes();
        try { shellSpaces.syncActivityTabs(); } catch (_) {}
        try { shellSpaces.schedulePoll(false); } catch (_) {}
    } catch (_) {}
}

// ── Last chat / terminal handle per column ─────────────────────
// The rail nav sends a bare "/chat_page.html", so leaving Chat for Settings and
// coming back landed on the new-chat splash. Remember what each column had open
// (durably, so a reload keeps it) and re-attach ?chat= on the way back.
const STORAGE_LAST_CHAT = 'shell_last_chat';

function readLastChatHandles() {
    const map = new Map();
    try {
        const raw = localStorage.getItem(STORAGE_LAST_CHAT);
        const data = raw ? JSON.parse(raw) : null;
        if (!data || typeof data !== 'object' || Array.isArray(data)) return map;
        Object.keys(data).forEach((key) => {
            const idx = parseInt(key, 10);
            const entry = data[key];
            if (!Number.isFinite(idx) || !entry || typeof entry !== 'object') return;
            const clean = {};
            if (entry.chat) clean.chat = String(entry.chat);
            if (entry.terminal) clean.terminal = String(entry.terminal);
            if (Object.keys(clean).length) map.set(idx, clean);
        });
    } catch (_) {}
    return map;
}

const lastChatByColumn = readLastChatHandles();

function persistLastChatHandles() {
    try {
        const out = {};
        lastChatByColumn.forEach((entry, idx) => { out[idx] = entry; });
        localStorage.setItem(STORAGE_LAST_CHAT, JSON.stringify(out));
    } catch (_) {}
}

/** 'chat' | 'terminal' for the two session-bearing surfaces, else null. */
function chatSurfaceKind(pathname) {
    if (pathname === '/chat_page.html') return 'chat';
    if (pathname === '/terminal_page.html') return 'terminal';
    return null;
}

function rememberChatHandle(colIdx, kind, chatId) {
    if (!kind || !Number.isFinite(colIdx)) return;
    const entry = lastChatByColumn.get(colIdx) || {};
    const next = chatId == null || chatId === '' ? '' : String(chatId);
    if (next) entry[kind] = next;
    else delete entry[kind];
    if (Object.keys(entry).length) lastChatByColumn.set(colIdx, entry);
    else lastChatByColumn.delete(colIdx);
    persistLastChatHandles();
}

/** Record the handle carried by a page URL (no-op for pages without one). */
function rememberChatHandleFromPage(colIdx, page) {
    if (!page) return;
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return;
        const cid = url.searchParams.get('chat') || url.searchParams.get('session');
        if (cid) rememberChatHandle(colIdx, kind, cid);
    } catch (_) {}
}

/**
 * Re-attach a column's last session when nav asks for a bare chat/terminal page.
 * Deep-links, sign-in, and an explicit new chat (handle already forgotten) pass through.
 */
function withRememberedChat(colIdx, page) {
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return page;
        if (url.searchParams.get('chat') || url.searchParams.get('session')) return page;
        if (url.searchParams.has('signin') || url.searchParams.has('new')) return page;
        const remembered = (lastChatByColumn.get(colIdx) || {})[kind];
        if (!remembered) return page;
        url.searchParams.set('chat', remembered);
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return page;
    }
}

function findColumnIndexForSource(source) {
    if (!source) return 0;
    const cols = document.querySelectorAll('.split-column');
    for (const col of cols) {
        const idx = parseInt(col.dataset.column, 10);
        const frame = getLiveFrame(idx);
        try {
            if (frame && frame.contentWindow === source) return idx;
        } catch (_) {}
    }
    return 0;
}

// Pane hubs and focus state have one pane-lifetime owner.
let paneController = null;
function setFocusedColumn(idx) { paneController?.setFocusedColumn(idx); }
function broadcastPaneFocus() { paneController?.broadcastFocus(); }
function broadcastUsageLiveWake() { paneController?.broadcastUsageLiveWake(); }
function registerPaneChatSession(source, sessionId) { paneController?.registerPaneChatSession(source, sessionId); }
function getOpenPaneSessions() { return paneController ? paneController.getOpenPaneSessions() : []; }
function registerPaneProject(source, projectPath) { paneController?.registerPaneProject(source, projectPath); }
function refreshPendingChangesPath(path) { return paneController?.refreshPendingChangesPath(path); }
function schedulePendingChangesHub(immediate) { paneController?.schedulePendingChangesHub(immediate); }
function pollLiveStatusHub() { return paneController?.pollLiveStatusHub(); }

document.addEventListener('pointerdown', (e) => {
    const col = e.target && e.target.closest ? e.target.closest('.split-column') : null;
    if (!col) return;
    const idx = parseInt(col.dataset.column, 10);
    if (Number.isFinite(idx)) setFocusedColumn(idx);
}, true);

// Initial page: restore from session; default to chat_page for immediate usability
const getDefaultPage = () => {
    const skip = localStorage.getItem('cuttle_skip_welcome');
    if (skip) return '/chat_page.html';
    // Prefer chat over landing so users land directly in the full chat UI
    return '/chat_page.html';
};
// Deep-link ?chat=<id> wins. Otherwise restore durable split layout (incl. per-pane
// ?chat=). Same-tab sessionStorage alone must not revive a chat on bare / — that
// used to reopen ?chat=N after navigating to "/".
const bootParams = new URLSearchParams(window.location.search);
const bootChatId = bootParams.get('chat') || bootParams.get('session');
const savedBootLayout = readSplitLayout();

function isTerminalChatHandle(chatId) {
    if (chatId == null || chatId === '') return false;
    try {
        const map = JSON.parse(localStorage.getItem('cuttle_terminal_sessions_v1') || '{}');
        return !!(map && map[String(chatId)]);
    } catch (_) {
        return false;
    }
}

let currentPage;
if (bootChatId) {
    currentPage = isTerminalChatHandle(bootChatId)
        ? '/terminal_page.html?chat=' + encodeURIComponent(bootChatId)
        : '/chat_page.html?chat=' + encodeURIComponent(bootChatId);
} else if (savedBootLayout?.root) {
    const leaves = flattenLayoutLeaves(savedBootLayout.root);
    if (leaves[0]?.page) currentPage = leaves[0].page;
    else {
        const stored = sessionStorage.getItem(STORAGE_PAGE) || getDefaultPage();
        currentPage = stored;
    }
} else {
    const stored = sessionStorage.getItem(STORAGE_PAGE) || getDefaultPage();
    try {
        const storedUrl = new URL(stored, window.location.origin);
        if (storedUrl.pathname === '/chat_page.html') {
            currentPage = '/chat_page.html';
            sessionStorage.setItem(STORAGE_PAGE, '/chat_page.html');
        } else {
            currentPage = stored;
        }
    } catch (_) {
        currentPage = getDefaultPage();
    }
}

currentPage = canonicalizeShellPage(currentPage);
sessionStorage.setItem(STORAGE_PAGE, currentPage);
rememberChatHandleFromPage(0, currentPage);

/** Compatibility calls into the pane-owned URL and history state machine. */
function shellPageFromChatId(chatId) { return paneController.shellPageFromChatId(chatId); }
function shellPagesEqual(a, b) { return paneController.shellPagesEqual(a, b); }
function isShellNewChatPage(page) { return paneController.isShellNewChatPage(page); }
function currentShellPage() { return paneController.currentShellPage(); }
function pushShellBackEntry(page) { return paneController.pushShellBackEntry(page); }
function syncShellChatUrl(chatId, opts) { return paneController.syncShellChatUrl(chatId, opts); }
function applyShellHistoryState(page) { return paneController.applyShellHistoryState(page); }

/**
 * Android system back → previous chat/page, then new chat, then stay put.
 * Always returns true so the native shell never minimizes or opens setup.
 */
window.__cuttleHandleBack = function () {
    try {
        if (typeof isNotificationsPopoverOpen === 'function' && isNotificationsPopoverOpen()) {
            closeNotificationsPopover();
            return true;
        }
    } catch (_) {}
    try {
        if (typeof isWorkspacePopoverOpen === 'function' && isWorkspacePopoverOpen()) {
            closeWorkspacePopover();
            return true;
        }
    } catch (_) {}

    if (paneController.popShellBack()) return true;

    const page = currentShellPage();
    if (!isShellNewChatPage(page)) {
        rememberChatHandle(0, 'chat', null);
        applyShellHistoryState('/chat_page.html?new=1');
        return true;
    }

    // Already on new-chat root — consume back, stay in app.
    return true;
};

// If boot restored a chat/terminal page from durable layout (no explicit ?chat=
// in address bar), mirror it into the URL so refresh / share still deep-link.
if (!bootChatId && currentPage) {
    try {
        const bootPageUrl = new URL(currentPage, window.location.origin);
        if (
            bootPageUrl.pathname === '/chat_page.html'
            || bootPageUrl.pathname === '/terminal_page.html'
        ) {
            const cid = bootPageUrl.searchParams.get('chat') || bootPageUrl.searchParams.get('session');
            if (cid) syncShellChatUrl(cid);
        }
    } catch (_) {}
}

// ── Video background (from Settings/child pages) ───────────────
window.addEventListener('message', function(e) {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'cuttle-video-reinit') {
        if (window.CuttleVideoBackground) window.CuttleVideoBackground.init();
    } else if (e.data.type === 'cuttle-video-play-now' && e.data.url) {
        if (window.CuttleVideoBackground && window.CuttleVideoBackground.playNow) {
            window.CuttleVideoBackground.playNow(String(e.data.url).trim());
        }
    } else if (e.data.type === 'cuttle-video-update-opacity' && typeof e.data.value === 'number') {
        if (window.CuttleVideoBackground && window.CuttleVideoBackground.updateOverlayOpacity) {
            window.CuttleVideoBackground.updateOverlayOpacity(e.data.value);
        }
    } else if (e.data.type === 'cuttle-bg-effect-blend' && typeof e.data.value === 'number') {
        document.querySelectorAll('.split-column .shell-main iframe').forEach(function (fr) {
            try {
                fr.contentWindow?.postMessage({ type: 'cuttle-bg-effect-blend', value: e.data.value }, '*');
            } catch (_) {}
        });
    } else if (e.data.type === 'cuttle-flask-restart-linked') {
        // One restart card answered → settle siblings in every open chat pane.
        document.querySelectorAll('.split-column .shell-main iframe').forEach(function (fr) {
            try {
                if (fr.contentWindow === e.source) return;
                fr.contentWindow?.postMessage(e.data, '*');
            } catch (_) {}
        });
    } else if (e.data.type === 'cuttle-video-request-state') {
        try {
            const active = document.body.classList.contains('has-video-background');
            e.source?.postMessage({ type: 'cuttle-video-state', active: active }, '*');
            var bh = localStorage.getItem('cuttleBackgroundEffectBlend');
            var bv = bh != null ? parseInt(bh, 10) : 100;
            if (isNaN(bv)) bv = 100;
            e.source?.postMessage({ type: 'cuttle-bg-effect-blend', value: Math.max(0, Math.min(100, bv)) }, '*');
            e.source?.postMessage({ type: 'cuttle-shell-visibility', hidden: !!document.hidden }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-shell-visibility-request') {
        try {
            e.source?.postMessage({ type: 'cuttle-shell-visibility', hidden: !!document.hidden }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-auth-changed') {
        setShellAuthUser(e.data.user || null);
    } else if (e.data.type === 'cuttle-chat-url') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        // Unmatched sources default to column 0. An outgoing iframe still
        // streaming during replaceFrame must not retarget the visible pane.
        if (!live || live.contentWindow !== e.source) return;
        // Keep each column's page (and durable layout) in sync with the open chat.
        try {
            const state = getState(colIdx);
            const cur = state.page || '/chat_page.html';
            const pageUrl = new URL(cur, window.location.origin);
            if (pageUrl.pathname === '/chat_page.html') {
                const next = e.data.chatId
                    ? '/chat_page.html?chat=' + encodeURIComponent(String(e.data.chatId))
                    : '/chat_page.html?new=1';
                // Null means the pane is on a fresh unsaved chat — forget the old
                // handle so returning to Chat doesn't resurrect it.
                rememberChatHandle(colIdx, 'chat', e.data.chatId || null);
                if (colIdx === 0 && !paneController.isApplyingHistory() && !shellPagesEqual(cur, next)) {
                    pushShellBackEntry(cur);
                }
                setState(colIdx, next);
                if (colIdx === 0) {
                    sessionStorage.setItem(STORAGE_PAGE, next);
                    syncShellChatUrl(e.data.chatId || null, { mode: 'replace', page: next });
                }
                persistSplitLayout();
            } else if (pageUrl.pathname === '/terminal_page.html') {
                const next = e.data.chatId
                    ? '/terminal_page.html?chat=' + encodeURIComponent(String(e.data.chatId))
                    : '/terminal_page.html';
                rememberChatHandle(colIdx, 'terminal', e.data.chatId || null);
                if (colIdx === 0 && !paneController.isApplyingHistory() && !shellPagesEqual(cur, next)) {
                    pushShellBackEntry(cur);
                }
                setState(colIdx, next);
                if (colIdx === 0) {
                    sessionStorage.setItem(STORAGE_PAGE, next);
                    syncShellChatUrl(e.data.chatId || null, { mode: 'replace', page: next });
                }
                persistSplitLayout();
            } else if (colIdx === 0) {
                syncShellChatUrl(e.data.chatId || null, { mode: 'replace' });
            }
        } catch (_) {
            if (colIdx === 0) syncShellChatUrl(e.data.chatId || null);
        }
    } else if (e.data.type === 'cuttle-jobs-tab') {
        // Per-pane Jobs tab (Devices) — must not
        // share one localStorage key across split viewports or refresh collapses them.
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        const tab = String(e.data.tab || '').trim().toLowerCase();
        const valid = { devices: 1 };
        if (!valid[tab]) return;
        try {
            const state = getState(colIdx);
            const cur = state.page || '/jobs_page.html';
            const pageUrl = new URL(cur, window.location.origin);
            if (pageUrl.pathname !== '/jobs_page.html') return;
            if (pageUrl.searchParams.get('tab') === tab) return;
            pageUrl.searchParams.set('tab', tab);
            const next = pageUrl.pathname + pageUrl.search + pageUrl.hash;
            setState(colIdx, next);
            if (colIdx === 0) sessionStorage.setItem(STORAGE_PAGE, next);
            persistSplitLayout();
        } catch (_) {}
    } else if (e.data.type === 'cuttle-chat-session') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        // Chat iframe reports its open session so the shell can batch live-status.
        registerPaneChatSession(e.source, e.data.sessionId);
        if (e.data.preferFocus) setFocusedColumn(colIdx);
    } else if (e.data.type === 'cuttle-pending-project') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        registerPaneProject(e.source, e.data.path);
    } else if (e.data.type === 'cuttle-pending-changes-refresh') {
        const path = e.data.path || '';
        if (path) refreshPendingChangesPath(path);
        else paneController?.schedulePendingChangesHub(true);
    } else if (e.data.type === 'cuttle-pending-changes-changed') {
        // Commit/ignore/local refresh in one pane → one fetch (or fan-out payload)
        // for every sibling on the same project path.
        if (e.data.data) {
            broadcastToFrames({
                type: 'cuttle-pending-changes-data',
                path: e.data.path || '',
                data: e.data.data,
            });
        } else if (e.data.path) {
            refreshPendingChangesPath(e.data.path);
        }
    } else if (e.data.type === 'cuttle-open-panes-request') {
        try {
            e.source?.postMessage({ type: 'cuttle-open-panes', panes: getOpenPaneSessions() }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-pane-activity') {
        setFocusedColumn(findColumnIndexForSource(e.source));
    } else if (e.data.type === 'cuttle-activity-refresh') {
        window.cuttleActivityBroker.refresh();
    } else if (e.data.type === 'cuttle-chat-activity') {
        // Snapshot of the chats this frame owns (immediate). Replaces the
        // frame's previous snapshot; the server poll stays authoritative.
        try {
            if (e.source) {
                shellSpaces.notePush(
                    e.source,
                    Array.isArray(e.data.sessions) ? e.data.sessions : [],
                    Array.isArray(e.data.owned) ? e.data.owned : null
                );
            } else {
                shellSpaces.refreshLocalState();
                shellSpaces.syncActivityTabs();
            }
        } catch (_) {}
    } else if (
        e.data.type === 'cuttle-terminal-delete'
        || e.data.type === 'cuttle-terminal-rename'
    ) {
        // Fan out history-panel mutations to any live terminal iframes.
        document.querySelectorAll('iframe').forEach((fr) => {
            try {
                const src = fr.getAttribute('src') || '';
                if (src.indexOf('terminal_page') === -1) return;
                fr.contentWindow?.postMessage(e.data, '*');
            } catch (_) {}
        });
    }
});

// ── Navigation map ─────────────────────────────────────────────
const PAGE_TITLES = {
    '/chat_page.html': 'Chat',
    '/router_editor.html': 'Router',
    '/git_graph_page.html': 'Git',
    '/jobs_page.html': 'Jobs',
    '/dashboards_page.html': 'Dashboards',
    '/apps_page.html': 'Apps',
    '/achievements_page.html': 'Achievements',
    '/gizmos_page.html': 'Gizmos',
    '/test_lab_page.html': 'Test Lab',
    '/secrets_page.html': 'Secrets',
    '/ide_page.html': 'IDE',
    '/projects_page.html': 'Projects',
    '/settings_page.html': 'Settings',
    '/query_log.html': 'Query log',
    '/terminal_page.html': 'Terminal',
};

// ── Column state (per-column) ──────────────────────────────────
const columnState = new Map();

function getColumnEl(idx) {
    const want = String(idx);
    // Soft-discarded stubs keep stale data-column values; never return those.
    return getSplitColumns().find((c) => c.dataset.column === want) || null;
}

function getColumnElements(col) {
    const colEl = typeof col === 'number' ? getColumnEl(col) : col;
    if (!colEl) return null;
    const colIdx = parseInt(colEl.dataset.column, 10);
    return {
        column: colEl,
        frame: getLiveFrame(colIdx),
        toggle: colEl.querySelector('.rail-panel-toggle'),
    };
}

function getState(colIdx) {
    let s = columnState.get(colIdx);
    if (!s) {
        s = { page: currentPage, railCollapsed: colIdx === 0 ? railCollapsed : false };
        columnState.set(colIdx, s);
    }
    return s;
}

function setState(colIdx, page) {
    const s = getState(colIdx);
    if (page !== undefined) s.page = page;
}

function applyRailCollapsed(colIdx, collapsed) {
    rail.applyCollapsed(colIdx, collapsed);
}

// ── Get the current live frame for a column ───────────────────
// Always looks up from DOM so it works after iframe replacement.
// During a transition, prefer the incoming frame over the outgoing one.
function getLiveFrame(colIdx) {
    const main = getColumnEl(colIdx)?.querySelector('.shell-main');
    if (!main) return null;
    if (colIdx === 0) {
        const byId = document.getElementById('contentFrame');
        if (byId) return byId;
    }
    const frames = Array.from(main.querySelectorAll('iframe'))
        .filter((f) => !f.classList.contains('shell-frame-outgoing'));
    return frames.find((f) => f.classList.contains('shell-frame-pending'))
        || frames[frames.length - 1]
        || null;
}

function isChatPageFrame(frame) {
    if (!frame) return false;
    let href = '';
    try {
        href = frame.contentWindow?.location?.href || '';
    } catch (_) {}
    if (!href) href = frame.getAttribute('src') || '';
    return href.indexOf('chat_page.html') !== -1;
}

function dispatchFocusedChatFind(action) {
    if (!action) return;
    const focused = paneController ? paneController.focusedColumnIndex() : 0;
    document.querySelectorAll('.split-column').forEach((col) => {
        const idx = parseInt(col.dataset.column, 10);
        const frame = getLiveFrame(idx);
        if (!frame || !frame.contentWindow) return;
        const isTarget = idx === focused && isChatPageFrame(frame);
        try {
            frame.contentWindow.postMessage({
                type: 'cuttle-chat-find',
                action: isTarget ? action : 'close',
            }, '*');
        } catch (_) {}
    });
}


function broadcastToFrames(message, skipWindow) {
    document.querySelectorAll('.shell-main iframe').forEach((frame) => {
        try {
            if (skipWindow && frame.contentWindow === skipWindow) return;
            frame.contentWindow?.postMessage(message, '*');
        } catch (_) {}
    });
}

// ── Rail customization / apps list (owner: shell_rail.js) ──────
// The rail layout lifetime (canonical order, pinned/hidden persistence,
// hold-to-edit mode, apps broadcast, Agent Feed availability) lives in
// CuttleShellRail; app_shell wires it and keeps the pane-mount re-apply calls.
const rail = CuttleShellRail.create({
    win: window, doc: document, getColumnEl, broadcastToFrames,
    getState, persistSplitLayout,
    onRailCollapsed: (idx, collapsed) => {
        if (idx !== 0) return;
        railCollapsed = collapsed;
        sessionStorage.setItem(STORAGE_RAIL, String(collapsed));
    },
});
// Capability for shell_mobile.js's APK-update rail button: it is created by the
// mobile owner but must not fire while the shell is editing the rail or
// suppressing a post-drag click. The rail owner holds that state; the owner asks.
window.cuttleRailInteractionLocked = () => rail.interactionLocked();
// Apps page asks the shell for the current rail apps by bare global name.
function getAppsList() { return rail.getAppsList(); }

paneController = CuttleShellPanes.create({
    win: window, doc: document, fetch, setTimeout, clearTimeout, URL, URLSearchParams,
    AbortController, getSplitColumns, getLiveFrame, findColumnIndexForSource,
    columnState, chatSurfaceKind, broadcastToFrames, isChatPageFrame,
    getColumnEl, getSplitColumnCount: () => getSplitColumns().length,
    pauseAllShellPaneStreams, pauseShellFrameStreams,
    stampComposerDraftScope: (frame, idx) => stampComposerDraftScope(frame, idx),
    withCacheBust: (page) => withCacheBust(page),
    attachFrameLoadListener: (idx, frame) => attachFrameLoadListener(idx, frame),
    localStorage, isSplitDiscardedEl, isCuttleMobileShell,
    withRememberedChat, rememberChatHandleFromPage, getState,
    setState, storagePageKey: STORAGE_PAGE,
    updateColumnUI, persistSplitLayout, sessionStorage,
    splitPane: splitLeafColumn, revealPane: col => setPaneCollapsed(col, false),
    desktopCfg: CuttleShellElectron.getDesktopConfig(),
    spacesActivity: CuttleSpaces,
    refreshParkedPaneTitle,
    closePane: closeSplitColumn,
    forgetChatHandle: () => rememberChatHandle(0, 'chat', null),
});

function applyUiAnimationPrefs() { paneController.applyUiAnimationPrefs(); }
function armFrameReveal(...args) { return paneController.armFrameReveal(...args); }
function cancelPendingReveal(frame) { return paneController.cancelPendingReveal(frame); }
function finishFrameTransitionFor(source) { return paneController.finishFrameTransitionFor(source); }

applyUiAnimationPrefs();


// ── Attach load-sync listener to a frame element ─────────────
function attachFrameLoadListener(colIdx, frameEl) {
    if (!frameEl || frameEl.dataset.shellLoadBound === '1') return;
    paneController.attachFrameLoadListener(frameEl, function () {
        try {
            const href = frameEl.contentWindow?.location?.href;
            // Ignore about:blank — Chromium may fire load for it before the real page loads.
            // Resetting state here would desync the UI. Only sync when we have a real URL.
            if (!href || href === 'about:blank') return;
            CuttleShellElectron.injectElectronZoomHandler(frameEl);
            try {
                if (typeof window.bindRailModKeysForFrame === 'function') {
                    window.bindRailModKeysForFrame(frameEl);
                }
            } catch (_) {}
            try {
                if (window.electron?.isElectron) {
                    frameEl.contentWindow?.postMessage({ type: 'cuttle-electron', enabled: true }, '*');
                }
            } catch (_) {}
            try {
                frameEl.contentWindow?.postMessage({ type: 'cuttle-video-state', active: document.body.classList.contains('has-video-background') }, '*');
            } catch (_) {}
            try {
                var bh = localStorage.getItem('cuttleBackgroundEffectBlend');
                var bv = bh != null ? parseInt(bh, 10) : 100;
                if (isNaN(bv)) bv = 100;
                bv = Math.max(0, Math.min(100, bv));
                frameEl.contentWindow?.postMessage({ type: 'cuttle-bg-effect-blend', value: bv }, '*');
            } catch (_) {}
            const url = new URL(href);
            let path = url.pathname;
            if (path === '/' || path === '/index.html') {
                // Root → redirect to welcome or chat based on preference
                const target = getDefaultPage();
                const newFrame = replaceFrame(colIdx, target);
                attachFrameLoadListener(colIdx, newFrame);
                setState(colIdx, target);
                updateColumnUI(colIdx, target);
                return;
            }
            // Keep ?chat= / ?session= — PAGE_TITLES keys must not strip deep-links
            // (that used to wipe terminal/chat handles from durable split layout).
            // Drop `_cb=` so remount/split never persist cache-bust into layout URLs.
            const matchKey = Object.keys(PAGE_TITLES).find(
                (k) => path === k || path.startsWith(k.endsWith('.html') ? k : k + '/')
            );
            const activePage = canonicalizeShellPage(path + (url.search || ''));
            rememberChatHandleFromPage(colIdx, activePage);
            refreshParkedPaneTitle(colIdx);
            const state = getState(colIdx);
            retryFailedShellFrame(colIdx, frameEl);
            if (state.page !== activePage) {
                setState(colIdx, activePage);
                updateColumnUI(colIdx, matchKey || '');
                if (colIdx === 0) {
                    try {
                        sessionStorage.setItem(STORAGE_PAGE, activePage);
                        const isChatSurface = path === '/chat_page.html' || path === '/terminal_page.html';
                        const cid = isChatSurface
                            ? (url.searchParams.get('chat') || url.searchParams.get('session'))
                            : null;
                        syncShellChatUrl(cid);
                    } catch (_) {}
                    persistSplitLayout();
                }
            }
        } catch (_err) {
            retryFailedShellFrame(colIdx, frameEl);
            setState(colIdx, null);
            updateColumnUI(colIdx, '');
        }
    });
}

// Establish the space identity before mounting the first chat frame.
// Spaces tab strip UI lives in the CuttleShellSpaces owner (shell_spaces.js);
// app_shell supplies the pane/layout effects it composes.
const shellSpaces = CuttleShellSpaces.create({
    win: window,
    doc: document,
    storage: localStorage,
    escapeHtml,
    getOpenPaneSessions,
    flattenLayoutLeaves,
    chatSurfaceKind,
    broadcastToFrames,
    broadcastUsageLiveWake,
    updateUnreadBadge: updateChatUnreadBadge,
    syncCollapsedPanes: () => paneController.syncCollapsedPanes(),
    getAuthUserId: () => (shellAuthUser ? shellAuthUser.id : null),
    isRestoring: () => _restoringLayout,
    snapshotLayout: () => snapshotLayoutTree(splitContainer),
    applySpaceRoot: (root) => applyWorkspaceColumns(null, null, root, { forceNavigate: true }),
    defaultSpaceRoot: () => getBuiltinDefaultWorkspace().root,
    persistLastChatHandles,
    clearLastChatColumn: (idx) => lastChatByColumn.delete(idx),
    // Pane/layout capabilities for the cross-space pane-drag effects.
    getSplitContainer: () => splitContainer,
    getSplitColumns,
    closeSplitColumn,
    getColumnEl,
    getState,
    setState,
    updateColumnUI,
    replaceFrame,
    attachFrameLoadListener,
    pageWithPaneSession,
    canonicalizeShellPage,
    persistSplitLayout,
    lastChatByColumn,
    newLeafId,
    newGroupId,
    normalizeSplitOrientation,
    setFocusedColumnDirect: (idx) => paneController?.setFocusedColumnDirect(idx),
    broadcastPaneFocus,
});
const spacesState = shellSpaces.state;

function stampComposerDraftScope(frame, colIdx) {
    if (!frame) return;
    const col = getColumnEl(colIdx);
    ensureLeafId(col);
    frame.dataset.cuttleDraftScope = spacesState.active + ':' + (col?.dataset.leafId || 'main');
}

/** Append `_cb=` so Chromium cannot reuse a stale HTML document for the iframe. */
function withCacheBust(page) {
    if (!page || String(page).startsWith('data:')) return page;
    // Unique `_cb=` URLs stampede Werkzeug HTTPS on phones (net::ERR_TOO_MANY_RETRIES).
    if (isCuttleMobileShell()) return page;
    try {
        const u = new URL(page, window.location.origin);
        u.searchParams.set('_cb', String(Date.now()));
        return u.pathname + u.search + u.hash;
    } catch (_) {
        const s = String(page);
        return s + (s.includes('?') ? '&' : '?') + '_cb=' + Date.now();
    }
}

const frameLoadRetries = new WeakMap();

function frameDocumentLooksFailed(frameEl) {
    try {
        const href = frameEl.contentWindow?.location?.href || '';
        if (/^chrome-error:/i.test(href) || /chromewebdata/i.test(href)) return true;
        const text = String(frameEl.contentDocument?.body?.innerText || '').slice(0, 800);
        return /ERR_TOO_MANY_RETRIES/i.test(text);
    } catch (_) {
        return false;
    }
}

function retryFailedShellFrame(colIdx, frameEl) {
    if (!isCuttleMobileShell() || !frameEl || !frameDocumentLooksFailed(frameEl)) return;
    const n = (frameLoadRetries.get(frameEl) || 0) + 1;
    frameLoadRetries.set(frameEl, n);
    if (n > 3) return;
    const page = (getState(colIdx) && getState(colIdx).page) || currentPage || '/chat_page.html';
    const delay = 400 * n;
    setTimeout(() => {
        try {
            if (!frameEl.parentNode) return;
            frameEl.src = 'about:blank';
            setTimeout(() => {
                try { frameEl.src = page; } catch (_) {}
            }, 50);
        } catch (_) {}
    }, delay);
}

// ── Replace the iframe element for a column ───────────────────
// Returns the new iframe element. Keeps the previous frame until the new page
// signals cuttle-page-ready (or the failsafe), then fades.
function pauseShellFrameStreams(frame) {
    if (!frame) return;
    try {
        frame.contentWindow?.postMessage({ type: 'cuttle-pause-streams' }, '*');
    } catch (_) {}
}

function pauseAllShellPaneStreams() {
    document.querySelectorAll('.shell-main iframe').forEach((frame) => {
        pauseShellFrameStreams(frame);
    });
}

/**
 * Multi-pane Electron freezes if we create a new iframe and destroy the old
 * chat document (EventSource / connection pool). Reuse the existing iframe and
 * only change src — verified via CDP: in-place src swap loads Git; replaceFrame
 * wedges before paint.
 */
function replaceFrameInPlace(colIdx, page) {
    return paneController.replaceFrameInPlace(colIdx, page);
}

function replaceFrame(colIdx, page) {
    return paneController.replaceFrame(colIdx, page);
}

// ── Apply initial state (column 0) ──────────────────────────────
const col0 = getColumnEl(0);
/** Sanitized column chrome HTML (no iframes). Captured before col0 loads so
 *  later splits never cloneNode/serialize a live iframe tree (Chromium freeze). */
let _leafTemplateHtml = '';
if (col0) {
    try {
        const bare = col0.cloneNode(true);
        bare.querySelectorAll('iframe').forEach((f) => f.remove());
        bare.querySelectorAll('.shell-page-loader, .shell-loader').forEach((el) => el.remove());
        _leafTemplateHtml = bare.outerHTML;
    } catch (_) {
        _leafTemplateHtml = '';
    }
    applyRailCollapsed(0, railCollapsed);
    setState(0, currentPage);
    // Attach load listener to the existing HTML iframe (don't replace on init)
    const initFrame = getLiveFrame(0);
    if (initFrame) {
        console.log('[App Shell] Initial iframe loading:', currentPage);
        const mainEl = col0.querySelector('.shell-main');
        if (mainEl) armFrameReveal(0, initFrame, mainEl, []);
        stampComposerDraftScope(initFrame, 0);
        initFrame.src = withCacheBust(currentPage);
        attachFrameLoadListener(0, initFrame);
    }
}

function parkShellIframes(root) {
    return paneController.parkShellIframes(root);
}

function discardSplitSubtree(root) {
    return paneController.discardSplitSubtree(root);
}

// ── Close a leaf pane (never the last remaining leaf) ───────────
function closeSplitColumn(colIdxOrEl) {
    const leaves = getSplitColumns();
    if (leaves.length <= 1) return;
    const col = (typeof colIdxOrEl === 'number' || typeof colIdxOrEl === 'string')
        ? getColumnEl(parseInt(colIdxOrEl, 10))
        : colIdxOrEl;
    if (!col || !col.classList?.contains('split-column')) return;
    if (isSplitDiscardedEl(col) || col.dataset.paneClosing === '1') return;
    col.dataset.paneClosing = '1';

    const colIdx = parseInt(col.dataset.column, 10);
    const parent = getLeafParentGroup(col);
    // Remove only one adjacent handle (both would orphan the neighbor pair).
    const prevSib = col.previousElementSibling;
    const nextSib = col.nextElementSibling;
    if (prevSib?.classList?.contains('split-resize-handle')) prevSib.remove();
    else if (nextSib?.classList?.contains('split-resize-handle')) nextSib.remove();

    paneController.clearRevealColumn(colIdx);
    columnState.delete(colIdx);
    paneController?.clearPaneColumn(colIdx);
    lastChatByColumn.delete(colIdx);
    if (paneController?.focusedColumnIndex() === colIdx) paneController.setFocusedColumnDirect(0);

    // Safe teardown — do not probe live iframe contentWindows (Chrome freeze).
    discardSplitSubtree(col);

    collapseSplitGroupIfNeeded(parent);
    const host = (parent && parent.isConnected) ? parent : splitContainer;
    const kids = getGroupChildNodes(host);
    if (kids.length >= 2) {
        applySplitRatioFlex(kids, Array(kids.length).fill(1), getGroupOrientation(host), host);
        rebuildGroupResizeHandles(host);
    } else if (kids.length === 1) {
        kids[0].style.flex = '';
        clearPaneBoxStyles(kids[0]);
    }
    sanitizeSplitDom();
    remumberSplitColumns();
    persistSplitLayout();
}

/** Remove orphan handles / empty nested groups that leave blank gaps. */
function sanitizeSplitDom() {
    if (!splitContainer) return;
    // Orphan handles with no neighboring pane/group on both sides
    splitContainer.querySelectorAll('.split-resize-handle').forEach((h) => {
        const parent = h.parentElement;
        if (!parent) { h.remove(); return; }
        const kids = getGroupChildNodes(parent);
        if (kids.length < 2) h.remove();
    });
    // Empty nested groups — soft-discard only. Sync .remove() of a group that
    // still wraps iframe stubs freezes Chromium/Electron.
    Array.from(splitContainer.querySelectorAll('.split-group')).forEach((g) => {
        if (g.id === 'splitContainer') return;
        if (g.dataset.splitDiscarded === '1') return;
        if (getGroupChildNodes(g).length === 0) {
            discardSplitSubtree(g);
        }
    });
    // Single child at root → full-bleed; keep a nested group intact (do not
    // unwrap / flatten complex layouts into a top-level row).
    const rootKids = getGroupChildNodes(splitContainer);
    if (rootKids.length <= 1) {
        splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
        if (rootKids[0]) {
            rootKids[0].style.flex = '';
            clearPaneBoxStyles(rootKids[0]);
        }
        if (rootKids[0]?.classList.contains('split-column')) {
            applyGroupOrientation(splitContainer, 'horizontal');
        }
    }
    // Deferred physical cleanup of soft-discarded stubs (never sync-remove on close).
    schedulePurgeDiscardedSplitNodes();
}

/** If a group has 0/1 children, unwrap it into its parent (except empty root).
 * Never reparent a live iframe column — recreate the leaf instead.
 */
function collapseSplitGroupIfNeeded(groupEl) {
    if (!groupEl || !isSplitGroupEl(groupEl) || !groupEl.isConnected) return;
    const kids = getGroupChildNodes(groupEl);
    if (kids.length > 1) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    if (groupEl.id === 'splitContainer') {
        groupEl.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
        if (kids.length === 1) {
            // Sole remaining root child (leaf or nested group) just fills the shell.
            // Never unwrap a nested group here — that flattened complex layouts into
            // a single horizontal row when the top of a vertical split was closed.
            kids[0].style.flex = '';
            clearPaneBoxStyles(kids[0]);
            if (kids[0].classList.contains('split-column')) {
                applyGroupOrientation(groupEl, 'horizontal');
            }
        }
        return;
    }
    // Nested group with ≤1 child → promote into parent (safe recreate)
    const grand = groupEl.parentElement;
    if (!grand) return;
    const flex = groupEl.style.flex || '1 1 0%';
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) ch.remove();
    });
    if (kids.length === 1) {
        const only = kids[0];
        if (only.classList.contains('split-column')) {
            const idx = parseInt(only.dataset.column, 10);
            const st = columnState.get(idx);
            const page = pageWithPaneSession(idx, (st && st.page) || '/chat_page.html');
            const built = createSplitLeafColumn(page, only.dataset.leafId);
            if (built) {
                built.clone.style.flex = flex;
                grand.insertBefore(built.clone, groupEl);
                discardSplitSubtree(groupEl);
                const provisionalIdx = 9990;
                built.clone.dataset.column = String(provisionalIdx);
                setState(provisionalIdx, page);
                mountNewLeafFrame(provisionalIdx, built.frame, built.mainEl, page);
                applyRailCollapsed(provisionalIdx, !!st?.railCollapsed);
                setupColumnListeners(provisionalIdx, built.clone);
                rail.setupLayoutDragDrop(built.clone);
                rail.applyLayoutToColumn(built.clone);
            } else {
                discardSplitSubtree(groupEl);
            }
        } else if (only.classList.contains('split-group')) {
            // Unwrap one nesting level only — do not flatten all descendant leaves.
            promoteGroupChildrenToParent(only, grand, groupEl);
        }
    } else {
        discardSplitSubtree(groupEl);
    }
    if (isSplitGroupEl(grand) && grand.isConnected) collapseSplitGroupIfNeeded(grand);
}

/**
 * Unwrap `groupEl` into `parentEl` (optionally replacing `replaceEl`), preserving
 * nested subgroup structure. Soft-discards live iframes and remounts from snapshot.
 */
function promoteGroupChildrenToParent(groupEl, parentEl, replaceEl) {
    if (!groupEl || !parentEl) return;
    const tree = snapshotLayoutTree(groupEl);
    const flex = groupEl.style.flex || '';
    const groupOri = getGroupOrientation(groupEl);
    const anchor = replaceEl || groupEl;
    const marker = document.createComment('split-promote');
    try {
        parentEl.insertBefore(marker, anchor);
    } catch (_) {}

    discardSplitSubtree(groupEl);
    if (replaceEl && replaceEl !== groupEl && replaceEl.isConnected) {
        discardSplitSubtree(replaceEl);
    }

    if (!tree) {
        try { marker.remove(); } catch (_) {}
        return;
    }

    const treeOri = tree.type === 'group' ? (tree.orientation || groupOri) : groupOri;
    const parentOri = getGroupOrientation(parentEl);
    const rootEmpty = parentEl.id === 'splitContainer'
        && getGroupChildNodes(parentEl).length === 0;

    if (rootEmpty) {
        // Sole remaining tree under root: keep group nesting intact. Hoisting
        // children into #splitContainer flattened complex layouts into one row
        // when the top of a vertical split was closed.
        if (tree.type === 'group' && Array.isArray(tree.children) && tree.children.length) {
            const nested = tree.children.length > 1
                || tree.children.some((ch) => ch && ch.type === 'group');
            if (nested) {
                applyGroupOrientation(parentEl, treeOri || 'horizontal');
                mountSnapshotAsNestedGroup(tree, parentEl, treeOri);
            } else {
                applyGroupOrientation(parentEl, 'horizontal');
                mountLayoutTree(tree.children[0], parentEl, { allowReuseColumn0: true });
            }
        } else {
            applyGroupOrientation(parentEl, 'horizontal');
            mountLayoutTree(tree, parentEl, { allowReuseColumn0: true });
        }
        try { marker.remove(); } catch (_) {}
        rebuildGroupResizeHandles(parentEl);
        return;
    }

    if (tree.type === 'group' && Array.isArray(tree.children) && tree.children.length >= 1) {
        if (parentOri === treeOri) {
            tree.children.forEach((ch) => {
                mountLayoutTree(ch, parentEl, { allowReuseColumn0: true, before: marker });
            });
        } else {
            const g = createSplitGroupEl(treeOri);
            if (flex) g.style.flex = flex;
            if (tree.id) g.dataset.groupId = tree.id;
            parentEl.insertBefore(g, marker);
            tree.children.forEach((ch) => {
                mountLayoutTree(ch, g, { allowReuseColumn0: true });
            });
            rebuildGroupResizeHandles(g);
        }
    } else {
        mountLayoutTree(tree, parentEl, { allowReuseColumn0: true, before: marker });
    }
    try { marker.remove(); } catch (_) {}
    rebuildGroupResizeHandles(parentEl);
}

function clearPaneBoxStyles(el) {
    if (!el) return;
    el.style.width = '';
    el.style.height = '';
    el.style.minWidth = '';
    el.style.maxWidth = '';
    el.style.minHeight = '';
    el.style.maxHeight = '';
}

// Logo SVG snippets must be initialized before restoreSplitLayout() / createSplitLeafColumn
// (those run mid-script and used to hit TDZ on these consts → blank panes after refresh).
const RAIL_PANE_GRIP_HTML =
    '<svg viewBox="0 0 16 10" width="16" height="10" aria-hidden="true">'
    + '<circle cx="4" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="8" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="12" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="4" cy="8" r="1.2" fill="currentColor"/>'
    + '<circle cx="8" cy="8" r="1.2" fill="currentColor"/>'
    + '<circle cx="12" cy="8" r="1.2" fill="currentColor"/>'
    + '</svg>';

const RAIL_SVG_PLUS =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    + '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

const RAIL_SVG_CLOSE =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    + '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

const RAIL_SVG_SPLIT_V =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="4" y="3" width="16" height="7" rx="1.5"/><rect x="4" y="14" width="16" height="7" rx="1.5"/></svg>';

const RAIL_SVG_SPLIT_H =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="3" y="4" width="7" height="16" rx="1.5"/><rect x="14" y="4" width="7" height="16" rx="1.5"/></svg>';

const RAIL_LOGO_MOD_LAYERS =
    '<span class="rail-logo-mod rail-logo-split-v" aria-hidden="true">' + RAIL_SVG_SPLIT_V + '</span>'
    + '<span class="rail-logo-mod rail-logo-split-h" aria-hidden="true">' + RAIL_SVG_SPLIT_H + '</span>';

function primaryRailLogoInnerHtml() {
    return '<img src="/img/cuttle-mascot.png" alt="Cuttle" class="rail-logo-img"'
        + ' onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
        + '<span class="rail-logo-fallback" style="display:none">C</span>'
        + '<span class="rail-logo-plus rail-logo-face">' + RAIL_SVG_PLUS + '</span>'
        + RAIL_LOGO_MOD_LAYERS;
}

function closeRailLogoInnerHtml() {
    return '<span class="rail-logo-close-face rail-logo-face">' + RAIL_SVG_CLOSE + '</span>'
        + RAIL_LOGO_MOD_LAYERS;
}

/**
 * Build a fresh leaf column from the boot-time chrome template (no live iframes).
 * Does not insert into the DOM.
 */
function createSplitLeafColumn(page, leafId) {
    if (!_leafTemplateHtml) {
        // Last-resort: build a minimal shell if boot capture failed.
        const holder = document.createElement('div');
        holder.innerHTML = '<div class="split-column" data-column="0">'
            + '<nav class="icon-rail"><div class="rail-logo rail-logo-close rail-logo-clickable" data-action="close"></div>'
            + '<div class="rail-items"></div><div class="rail-footer"></div></nav>'
            + '<main class="shell-main"></main></div>';
        _leafTemplateHtml = holder.innerHTML;
    }
    const holder = document.createElement('div');
    holder.innerHTML = _leafTemplateHtml;
    const clone = holder.firstElementChild;
    if (!clone) return null;

    clone.dataset.leafId = leafId || newLeafId();
    // Unique provisional index so remumber never maps two fresh clones that both
    // still say data-column="0" onto the same lastChatByColumn entry.
    clone.dataset.column = String(900000 + Math.floor(Math.random() * 100000));
    clone.classList.remove('rail-collapsed');
    clone.style.flex = '';
    clearPaneBoxStyles(clone);
    clone.removeAttribute('data-pane-closing');
    clone.removeAttribute('data-layout-mounted');

    const logoEl = clone.querySelector('.rail-logo-primary, .rail-logo-add, .rail-logo');
    if (logoEl) {
        logoEl.className = 'rail-logo rail-logo-close rail-logo-clickable';
        logoEl.dataset.action = 'close';
        logoEl.removeAttribute('title');
        logoEl.removeAttribute('id');
        logoEl.dataset.tooltip = defaultRailLogoTooltip('close');
        logoEl.innerHTML = closeRailLogoInnerHtml();
    }

    clone.querySelectorAll('[id]').forEach((el) => { el.id = ''; });
    clone.querySelector('.icon-rail')?.removeAttribute('data-rail-customize');
    clone.querySelector('.icon-rail')?.removeAttribute('data-pane-reorder');
    ensureRailPaneGrip(clone);
    clone.querySelectorAll('.shell-page-loader, .shell-loader').forEach((el) => el.remove());
    clone.querySelectorAll('iframe').forEach((el) => el.remove());

    const mainEl = clone.querySelector('.shell-main');
    let frame = null;
    if (mainEl) {
        // Ensure main is empty before attaching a fresh frame.
        while (mainEl.firstChild) mainEl.removeChild(mainEl.firstChild);
        frame = paneController.createContentFrame(
            parseInt(clone.dataset.column, 10)
        );
        mainEl.appendChild(frame);
    }
    ensurePaneDropOverlay(clone);
    return { clone, frame, mainEl, page: page || '/chat_page.html' };
}

function mountNewLeafFrame(newIdx, frame, mainEl, page) {
    if (!frame || !mainEl) return;
    const safePage = canonicalizeShellPage(page || '/chat_page.html');
    setState(newIdx, safePage);
    updateColumnUI(newIdx, safePage);
    frame.classList.remove('shell-frame-pending', 'frame-entering');
    armFrameReveal(newIdx, frame, mainEl, []);
    stampComposerDraftScope(frame, newIdx);
    frame.src = withCacheBust(safePage);
    attachFrameLoadListener(newIdx, frame);
    // Fresh panes clone the boot-time rail template, which bakes in the
    // pre-flag `display:none` on nav-agent-feed. Re-apply the live flag so a
    // new split matches column 0 instead of losing the icon.
    rail.applyAgentFeedAvailability();
}

/**
 * Mount a snapshot tree as a nested group under parent (never absorb into root).
 */
function mountSnapshotAsNestedGroup(snapshot, parentEl, orientationFallback) {
    if (!snapshot || !parentEl) return null;
    const ori = snapshot.type === 'group'
        ? (snapshot.orientation || orientationFallback || 'horizontal')
        : (orientationFallback || 'horizontal');
    const wrap = createSplitGroupEl(ori);
    wrap.style.flex = snapshot.flex || '1 1 0%';
    if (snapshot.type === 'group' && snapshot.id) wrap.dataset.groupId = snapshot.id;
    parentEl.appendChild(wrap);
    if (snapshot.type === 'group' && Array.isArray(snapshot.children)) {
        snapshot.children.forEach((ch) => {
            mountLayoutTree(ch, wrap, { allowReuseColumn0: true });
        });
    } else {
        mountLayoutTree(snapshot, wrap, { allowReuseColumn0: true });
    }
    rebuildGroupResizeHandles(wrap);
    return wrap;
}

/**
 * Discard every active root child (soft) — used when wrapping the whole layout.
 */
function discardAllRootSplitChildren() {
    if (!splitContainer) return;
    Array.from(splitContainer.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) {
            try { ch.remove(); } catch (_) {}
            return;
        }
        if (ch.classList?.contains('split-column') || ch.classList?.contains('split-group')) {
            discardSplitSubtree(ch);
        }
    });
}

/**
 * Primary-blade `+`: append a fresh chat at the far right of the shell.
 * Horizontal roots append in place (no remount) and equalize widths.
 * Vertical multi-pane roots wrap the stack on the left, then size by leaf count
 * so the new cell is 1/N of the shell — not half.
 */
function appendRootHorizontalPane(initialPage) {
    if (!splitContainer) return;
    const page = bareShellPage(initialPage || '/chat_page.html');
    const kids = getGroupChildNodes(splitContainer);
    const ori = getGroupOrientation(splitContainer);

    const finishNewLeaf = (built) => {
        if (!built) return;
        splitContainer.appendChild(built.clone);
        remumberSplitColumns();
        const newIdx = parseInt(built.clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, built.frame, built.mainEl, page);
        setupColumnListeners(newIdx, built.clone);
        rail.setupLayoutDragDrop(built.clone);
        rail.applyLayoutToColumn(built.clone);
    };

    // Vertical multi-pane root → [existing stack | new] without destroying nesting.
    if (ori === 'vertical' && kids.length >= 2) {
        const leafCountBefore = Math.max(1, getSplitColumns().length);
        const snapshot = snapshotLayoutTree(splitContainer);
        discardAllRootSplitChildren();
        applyGroupOrientation(splitContainer, 'horizontal');
        if (snapshot) mountSnapshotAsNestedGroup(snapshot, splitContainer, 'vertical');
        finishNewLeaf(createSplitLeafColumn(page));
        const rootKids = getGroupChildNodes(splitContainer);
        if (!_restoringLayout && rootKids.length >= 2) {
            applySplitRatioFlex(
                rootKids,
                [leafCountBefore, 1],
                'horizontal',
                splitContainer
            );
        } else {
            rebuildGroupResizeHandles(splitContainer);
        }
        persistSplitLayout();
        return;
    }

    // Single pane / already horizontal: append at end — do not remount siblings.
    if (kids.length <= 1) applyGroupOrientation(splitContainer, 'horizontal');
    finishNewLeaf(createSplitLeafColumn(page));
    if (!_restoringLayout) equalizeGroupChildren(splitContainer);
    else rebuildGroupResizeHandles(splitContainer);
    persistSplitLayout();
}

/**
 * Alt+Shift+click `+`: absolute vertical split — new cell at the very bottom,
 * existing layout pushed up as the top pane/group.
 */
function appendRootVerticalPane(initialPage) {
    if (!splitContainer) return;
    const page = bareShellPage(initialPage || '/chat_page.html');
    const kids = getGroupChildNodes(splitContainer);
    const ori = getGroupOrientation(splitContainer);

    const finishNewLeaf = (built) => {
        if (!built) return null;
        splitContainer.appendChild(built.clone);
        remumberSplitColumns();
        const newIdx = parseInt(built.clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, built.frame, built.mainEl, page);
        setupColumnListeners(newIdx, built.clone);
        rail.setupLayoutDragDrop(built.clone);
        rail.applyLayoutToColumn(built.clone);
        return newIdx;
    };

    if (kids.length <= 1) {
        applyGroupOrientation(splitContainer, 'vertical');
        finishNewLeaf(createSplitLeafColumn(page));
        if (!_restoringLayout) equalizeGroupChildren(splitContainer);
        else rebuildGroupResizeHandles(splitContainer);
        persistSplitLayout();
        return;
    }

    if (ori === 'vertical') {
        finishNewLeaf(createSplitLeafColumn(page));
        if (!_restoringLayout) equalizeGroupChildren(splitContainer);
        else rebuildGroupResizeHandles(splitContainer);
        persistSplitLayout();
        return;
    }

    // Horizontal (or mixed) multi-pane root → wrap everything on top, new cell below.
    const leafCountBefore = Math.max(1, getSplitColumns().length);
    const snapshot = snapshotLayoutTree(splitContainer);
    discardAllRootSplitChildren();
    applyGroupOrientation(splitContainer, 'vertical');
    if (snapshot) mountSnapshotAsNestedGroup(snapshot, splitContainer, 'horizontal');
    finishNewLeaf(createSplitLeafColumn(page));
    const rootKids = getGroupChildNodes(splitContainer);
    if (!_restoringLayout && rootKids.length >= 2) {
        applySplitRatioFlex(
            rootKids,
            [leafCountBefore, 1],
            'vertical',
            splitContainer
        );
    } else {
        rebuildGroupResizeHandles(splitContainer);
    }
    persistSplitLayout();
}

/**
 * Split one viewport/cell only.
 * - Same-orientation parent → append sibling after this leaf
 * - Else create a nested group and replace this leaf with [kept copy + new]
 *   (never reparent a live iframe column — that freezes Chrome/Electron)
 */
function splitLeafColumn(targetCol, orientation, initialPage) {
    if (!targetCol || !splitContainer) return;
    const want = normalizeSplitOrientation(orientation);
    const defaultPage = bareShellPage(initialPage || '/chat_page.html');

    let parent = getLeafParentGroup(targetCol);
    if (parent === splitContainer && getGroupChildNodes(parent).length === 1) {
        applyGroupOrientation(parent, want);
    }

    const sameAxis = getGroupOrientation(parent) === want
        || (parent === splitContainer && getGroupChildNodes(parent).length === 1);

    if (sameAxis) {
        applyGroupOrientation(parent, want);
        const built = createSplitLeafColumn(defaultPage);
        if (!built) return;
        const { clone, frame, mainEl, page } = built;
        if (targetCol.nextSibling) parent.insertBefore(clone, targetCol.nextSibling);
        else parent.appendChild(clone);

        remumberSplitColumns();
        const newIdx = parseInt(clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, frame, mainEl, page);
        setupColumnListeners(newIdx, clone);
        rail.setupLayoutDragDrop(clone);
        rail.applyLayoutToColumn(clone);
        if (!_restoringLayout) equalizeGroupChildren(parent);
        else rebuildGroupResizeHandles(parent);
        persistSplitLayout();
        return;
    }

    // Different orientation → replace target with a nested group of two NEW leaves.
    // Copy the target's page into the first leaf; never move the live iframe node.
    const targetIdx = parseInt(targetCol.dataset.column, 10);
    const targetState = columnState.get(targetIdx);
    const keepPage = pageWithPaneSession(
        targetIdx,
        (targetState && targetState.page) || '/chat_page.html'
    );
    const oldFlex = targetCol.style.flex || '';
    const keepLeafId = targetCol.dataset.leafId || newLeafId();
    const keepHandles = lastChatByColumn.get(targetIdx)
        ? { ...lastChatByColumn.get(targetIdx) }
        : null;

    const builtA = createSplitLeafColumn(keepPage, keepLeafId);
    const builtB = createSplitLeafColumn(defaultPage);
    if (!builtA || !builtB) return;

    const group = createSplitGroupEl(want);
    if (oldFlex) group.style.flex = oldFlex;
    else group.style.flex = '1 1 0%';

    parent.insertBefore(group, targetCol);
    discardSplitSubtree(targetCol);

    columnState.delete(targetIdx);
    paneController?.clearPaneColumn(targetIdx);
    paneController.clearRevealColumn(targetIdx);
    lastChatByColumn.delete(targetIdx);

    builtA.clone.style.flex = '1 1 0%';
    builtB.clone.style.flex = '1 1 0%';
    group.appendChild(builtA.clone);
    group.appendChild(builtB.clone);

    remumberSplitColumns();
    const idxA = parseInt(builtA.clone.dataset.column, 10);
    const idxB = parseInt(builtB.clone.dataset.column, 10);
    if (keepHandles) {
        lastChatByColumn.set(idxA, keepHandles);
        persistLastChatHandles();
    }
    rememberChatHandleFromPage(idxA, keepPage);
    lastChatByColumn.delete(idxB);
    persistLastChatHandles();

    mountNewLeafFrame(idxA, builtA.frame, builtA.mainEl, keepPage);
    mountNewLeafFrame(idxB, builtB.frame, builtB.mainEl, builtB.page);
    setupColumnListeners(idxA, builtA.clone);
    setupColumnListeners(idxB, builtB.clone);
    rail.setupLayoutDragDrop(builtA.clone);
    rail.setupLayoutDragDrop(builtB.clone);
    rail.applyLayoutToColumn(builtA.clone);
    rail.applyLayoutToColumn(builtB.clone);

    if (!_restoringLayout) {
        applySplitRatioFlex(getGroupChildNodes(group), [1, 1], want, group);
    }
    rebuildGroupResizeHandles(group);
    rebuildGroupResizeHandles(parent);
    persistSplitLayout();
}

/** Back-compat name used by restore / workspace load (adds root-level siblings). */
function addSplitColumn(initialPage, opts) {
    const want = normalizeSplitOrientation(
        opts && Object.prototype.hasOwnProperty.call(opts, 'orientation')
            ? opts.orientation
            : 'horizontal'
    );
    if (!_restoringLayout && want === 'horizontal') {
        appendRootHorizontalPane(initialPage);
        return;
    }
    const leaves = getSplitColumns();
    const target = leaves[leaves.length - 1] || getColumnEl(0);
    if (!target) return;
    // Restore path: prefer appending into root when building a flat row/stack.
    if (_restoringLayout && splitContainer) {
        applyGroupOrientation(splitContainer, want);
        const built = createSplitLeafColumn(canonicalizeShellPage(initialPage || '/chat_page.html'));
        if (!built) return;
        const { clone, frame, mainEl, page } = built;
        const kids = getGroupChildNodes(splitContainer);
        if (kids.length >= 1) {
            const handle = document.createElement('div');
            handle.className = 'split-resize-handle';
            splitContainer.appendChild(handle);
        }
        splitContainer.appendChild(clone);
        remumberSplitColumns();
        const newIdx = parseInt(clone.dataset.column, 10);
        if (initialPage) rememberChatHandleFromPage(newIdx, initialPage);
        mountNewLeafFrame(newIdx, frame, mainEl, page);
        setupColumnListeners(newIdx, clone);
        rail.setupLayoutDragDrop(clone);
        rail.applyLayoutToColumn(clone);
        return;
    }
    splitLeafColumn(target, want, initialPage);
}

// ── Split sizing (per-group; ratios scale with window) ───────────
const SPLIT_MIN_PX = 72;           // horizontal (width) floor
const SPLIT_MIN_PX_VERTICAL = 36;  // vertical (height) floor — keep short stacks scrollable
const SPLIT_COLLAPSE_PX = 200;     // horizontal drag-release below this parks the pane

function isPaneCollapsedEl(el) {
    return !!(el && el.classList && el.classList.contains('pane-collapsed'));
}

/** Restore-strip label: live chat name, else page title. */
function parkedPaneTitle(col, idx) {
    try {
        const frame = col.querySelector('.shell-main iframe');
        const name = frame?.contentDocument
            ?.getElementById('chatSessionTitleText')?.textContent?.trim();
        if (name) return name;
    } catch (_) { /* cross-origin or unloading: fall through to the page */ }
    const state = Number.isFinite(idx) ? columnState.get(idx) : null;
    const base = String((state && state.page) || '').split('?')[0];
    if (base && PAGE_TITLES[base]) return PAGE_TITLES[base];
    return 'Pane';
}

function refreshParkedPaneTitle(colOrIdx) {
    const col = (typeof colOrIdx === 'number' || typeof colOrIdx === 'string')
        ? getColumnEl(parseInt(colOrIdx, 10))
        : colOrIdx;
    if (!col || !isPaneCollapsedEl(col)) return;
    col.dataset.parkedTitle = parkedPaneTitle(col, parseInt(col.dataset.column, 10));
    paneController.refreshCollapsedPane(col, col.dataset.parkedTitle);
}

// Every parked pane is a clickable restore strip; its iframe stays mounted.
if (typeof document !== 'undefined') {
    document.addEventListener('click', (e) => {
        if (e.button !== 0) return;
        const bar = e.target?.closest?.(
            '.split-column.pane-collapsed'
        );
        if (!bar || !bar.classList?.contains('split-column')) return;
        e.preventDefault();
        setPaneCollapsed(bar, false);
    });
}

/**
 * Park / restore a viewport in a slim strip without touching its iframe.
 * Options: { quiet: true } applies class/flex/state only (layout restore —
 * the caller owns redistribute + persist).
 */
function setPaneCollapsed(colOrIdx, collapsed, opts) {
    const col = (typeof colOrIdx === 'number' || typeof colOrIdx === 'string')
        ? getColumnEl(parseInt(colOrIdx, 10))
        : colOrIdx;
    if (!col || !col.classList?.contains('split-column')) return false;
    collapsed = !!collapsed;
    const host = getLeafParentGroup(col);
    const idx = parseInt(col.dataset.column, 10);
    col.classList.toggle('pane-collapsed', collapsed);
    if (Number.isFinite(idx)) getState(idx).paneCollapsed = collapsed;
    // The drag preview (.pane-will-collapse dims content, .will-collapse
    // outlines the divider) belongs to an in-flight gesture only — the
    // collapse path bypasses stopDrag, so clear it here or the parked /
    // restored pane keeps the dim + red dash.
    col.classList.remove('pane-will-collapse');
    host?.querySelectorAll(
        ':scope > .pane-will-collapse, :scope > .will-collapse'
    ).forEach((el) => {
        el.classList.remove('pane-will-collapse', 'will-collapse');
    });
    if (collapsed) {
        if (opts?.restoreEdge) col.dataset.restoreEdge = opts.restoreEdge;
        refreshParkedPaneTitle(col);
        col.style.flex = '0 0 auto';
        clearPaneBoxStyles(col);
    } else {
        col.removeAttribute('data-parked-title');
        col.removeAttribute('title');
        col.style.flex = '';
        clearPaneBoxStyles(col);
    }
    if (opts && opts.quiet) return true;
    const kids = host ? getGroupChildNodes(host) : [];
    if (kids.length >= 2) {
        applySplitRatioFlex(kids, kids.map(() => 1), getGroupOrientation(host), host);
    } else if (kids.length === 1) {
        kids[0].style.flex = '';
        clearPaneBoxStyles(kids[0]);
    }
    if (host) rebuildGroupResizeHandles(host);
    persistSplitLayout();
    return true;
}


/** Must match `.split-resize-handle` thickness in app_shell.css */
const SPLIT_HANDLE_PX = 5;

/** Parse legacy drag-resize flex `0 0 Npx` → N, else null. */
function parseSplitFlexPx(flex) {
    const m = String(flex || '').trim().match(/^0\s+0\s+(\d+(?:\.\d+)?)px$/i);
    return m ? parseFloat(m[1]) : null;
}

/** Main-axis size of a pane/group child. */
function measureSplitMain(el, orientation) {
    if (!el) return 0;
    const vertical = normalizeSplitOrientation(orientation) === 'vertical'
        || (!orientation && getGroupOrientation(getLeafParentGroup(el)) === 'vertical');
    return vertical ? (el.offsetHeight || 0) : (el.offsetWidth || 0);
}

/** Pixel budget for siblings inside a group. */
function getSplitGroupBudget(groupEl, childCount) {
    return CuttleShellPanes.splitGroupBudget(groupEl, childCount, getGroupOrientation(groupEl), SPLIT_HANDLE_PX);
}

/**
 * Preferred per-pane floor, lowered when n×min would exceed the window so
 * panes (and their rails/toolbars) can never spill past the viewport.
 */
function splitMinPxFor(colCount, budget, orientation) {
    const n = Math.max(1, colCount | 0);
    const fit = Math.floor((budget || 0) / n);
    const floor = normalizeSplitOrientation(orientation) === 'vertical'
        ? SPLIT_MIN_PX_VERTICAL
        : SPLIT_MIN_PX;
    return Math.max(1, Math.min(floor, fit));
}

/**
 * Force widths to fill exactly `budget` with each pane ≥ min. Prevents the
 * divider from growing a pane so far that siblings are pushed off-screen.
 */
function clampSplitWidths(widths, budget, orientation) {
    const n = widths.length;
    if (n === 0) return [];
    const avail = Math.max(n, Math.round(budget || 0));
    const minPx = splitMinPxFor(n, avail, orientation);
    let next = widths.map((w) => Math.max(minPx, Math.round(Number(w) || 0)));
    let sum = next.reduce((a, b) => a + b, 0);
    if (sum > avail) {
        let overflow = sum - avail;
        // Shrink widest panes first (never below min).
        while (overflow > 0) {
            let progressed = false;
            const order = next.map((w, i) => i).sort((a, b) => next[b] - next[a]);
            for (const i of order) {
                if (overflow <= 0) break;
                const can = next[i] - minPx;
                if (can <= 0) continue;
                const take = Math.min(can, overflow);
                next[i] -= take;
                overflow -= take;
                progressed = true;
            }
            if (!progressed) break;
        }
        sum = next.reduce((a, b) => a + b, 0);
    }
    if (sum < avail) {
        // Give leftover to the last pane so columns always fill the window.
        next[n - 1] += avail - sum;
    }
    return next;
}

/**
 * Apply proportional flex-grow so siblings keep relative sizes when the
 * window is resized. `widths` are relative weights (e.g. [1,1] or measured px);
 * they are normalized to the group budget before clamping.
 */
function applySplitRatioFlex(cols, widths, orientation, groupEl) {
    if (!cols || cols.length === 0) return;
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = orientation || getGroupOrientation(group);
    const budget = getSplitGroupBudget(group, cols.length);
    // Collapsed panes stay pinned to their blade width; only fluid panes
    // share the remaining budget.
    const pinned = cols.map((c) => (isPaneCollapsedEl(c) ? measureSplitMain(c, ori) : 0));
    const pinnedTotal = pinned.reduce((a, b) => a + b, 0);
    const fluidIdx = cols.map((_, i) => i).filter((i) => !pinned[i]);
    cols.forEach((c, i) => {
        if (pinned[i]) {
            c.style.flex = '0 0 auto';
            clearPaneBoxStyles(c);
        }
    });
    if (fluidIdx.length === 0) return;
    const fluidBudget = Math.max(fluidIdx.length, budget - pinnedTotal);
    const weights = fluidIdx.map((i) => Math.max(0, Number(widths?.[i]) || 0));
    const weightSum = weights.reduce((a, b) => a + b, 0);
    const normalized = weightSum > 0
        ? weights.map((w) => (w / weightSum) * fluidBudget)
        : fluidIdx.map(() => fluidBudget / fluidIdx.length);
    const safe = clampSplitWidths(normalized, fluidBudget, ori);
    const total = safe.reduce((a, b) => a + b, 0) || 1;
    fluidIdx.forEach((colIdx, k) => {
        const ratio = safe[k] / total;
        cols[colIdx].style.flex = `${ratio.toFixed(6)} 1 0%`;
        clearPaneBoxStyles(cols[colIdx]);
    });
}

/**
 * After splitting inside a group: new sibling ≈ average of prior siblings.
 */
function fitNewSiblingsInGroup(groupEl, priorSizes) {
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    const ori = getGroupOrientation(groupEl);
    const budget = getSplitGroupBudget(groupEl, cols.length);
    let prior = Array.isArray(priorSizes) ? priorSizes.map((w) => Math.max(0, Number(w) || 0)) : [];
    if (prior.length !== cols.length - 1) {
        prior = cols.slice(0, -1).map((c) => measureSplitMain(c, ori));
    }
    const sumPrior = prior.reduce((a, b) => a + b, 0);
    if (sumPrior <= 0) {
        applySplitRatioFlex(cols, Array(cols.length).fill(1), ori, groupEl);
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    const avg = sumPrior / prior.length;
    const raw = prior.concat([avg]);
    const scaled = raw.map((w) => (w / (sumPrior + avg)) * budget);
    applySplitRatioFlex(cols, scaled, ori, groupEl);
    rebuildGroupResizeHandles(groupEl);
}

function applySplitPixelFlex(cols, widths, orientation, groupEl) {
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = orientation || getGroupOrientation(group);
    const budget = getSplitGroupBudget(group, cols.length);
    const clamped = CuttleShellPanes.fitSplitSizes(widths,
        cols.map(c => isPaneCollapsedEl(c) ? measureSplitMain(c, ori) : 0), budget, ori, clampSplitWidths);
    cols.forEach((c, i) => {
        // A parked pane never takes pixel flex — it keeps its strip size.
        if (isPaneCollapsedEl(c)) {
            c.style.flex = '0 0 auto';
            clamped[i] = measureSplitMain(c, ori);
            return;
        }
        c.style.flex = `0 0 ${clamped[i]}px`;
    });
    return clamped;
}

/** If any sibling still uses fixed-px flex, convert all to ratios. */
function migrateFixedPxSplitFlex(cols, groupEl) {
    if (!cols || cols.length < 2) return false;
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = getGroupOrientation(group);
    let anyFixed = false;
    const widths = cols.map((c) => {
        const px = parseSplitFlexPx(c.style.flex);
        if (px != null) {
            anyFixed = true;
            return px;
        }
        return measureSplitMain(c, ori);
    });
    if (!anyFixed) return false;
    applySplitRatioFlex(cols, widths, ori, group);
    return true;
}

/** After a drag, lock in ratio flex from current main-axis sizes (one group). */
function finalizeGroupToRatios(groupEl) {
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) return;
    const ori = getGroupOrientation(groupEl);
    const widths = cols.map((c) => {
        const px = parseSplitFlexPx(c.style.flex);
        return px != null ? px : measureSplitMain(c, ori);
    });
    applySplitRatioFlex(cols, widths, ori, groupEl);
}

function finalizeSplitColumnsToRatios() {
    if (!splitContainer) return;
    const groups = [splitContainer, ...splitContainer.querySelectorAll('.split-group')];
    groups.forEach((g) => finalizeGroupToRatios(g));
}

function ensureLeafId(colEl) {
    if (colEl && !colEl.dataset.leafId) colEl.dataset.leafId = newLeafId();
}

/**
 * Recursively mount a saved layout tree under `parentEl`.
 * First leaf reuses existing column 0 when present.
 *
 * `absorbIntoRoot: true` — mount a top-level group directly into #splitContainer
 * even when column 0 is already a child. Without this, restore wraps the whole
 * layout in an extra .split-group beside col0 and applyFlexWalk / sizing leave
 * blank flex regions with no blade.
 */
function mountLayoutTree(node, parentEl, opts) {
    const options = opts || {};
    if (!node || !parentEl) return;
    const before = (options.before && options.before.parentNode === parentEl)
        ? options.before
        : null;
    const insertNode = (el) => {
        if (before) parentEl.insertBefore(el, before);
        else parentEl.appendChild(el);
    };

    if (node.type === 'leaf' || (!node.type && node.page)) {
        let col = null;
        const col0 = getColumnEl(0);
        const canReuse = options.allowReuseColumn0 !== false && col0
            && !col0.dataset.layoutMounted
            && getSplitColumns().length <= 1
            && !isSplitDiscardedEl(col0);
        if (canReuse) {
            col = col0;
            col.dataset.layoutMounted = '1';
            ensureLeafId(col);
            if (node.id) col.dataset.leafId = node.id;
            if (parentEl !== col.parentElement) {
                const kids = getGroupChildNodes(parentEl);
                if (kids.length >= 1) {
                    const handle = document.createElement('div');
                    handle.className = 'split-resize-handle';
                    insertNode(handle);
                }
                insertNode(col);
            }
            remumberSplitColumns();
            const idx = parseInt(col.dataset.column, 10);
            rememberHandlesFromWorkspaceEntry(idx, node);
            stampComposerDraftScope(getLiveFrame(idx), idx);
            navigate(idx, pageFromLayoutLeaf(node), { force: !!options.forceNavigate });
        } else {
            const built = createSplitLeafColumn(pageFromLayoutLeaf(node), node.id);
            if (!built) return;
            const kids = getGroupChildNodes(parentEl);
            if (kids.length >= 1) {
                const handle = document.createElement('div');
                handle.className = 'split-resize-handle';
                insertNode(handle);
            }
            insertNode(built.clone);
            col = built.clone;
            col.dataset.layoutMounted = '1';
            remumberSplitColumns();
            const newIdx = parseInt(col.dataset.column, 10);
            rememberHandlesFromWorkspaceEntry(newIdx, node);
            mountNewLeafFrame(newIdx, built.frame, built.mainEl, pageFromLayoutLeaf(node));
            setupColumnListeners(newIdx, col);
            rail.setupLayoutDragDrop(col);
            rail.applyLayoutToColumn(col);
        }
        if (col && node.flex) col.style.flex = node.flex;
        if (col) rail.restoreCollapsed(col, node);
        if (col && node.collapsed) setPaneCollapsed(col, true, { quiet: true, restoreEdge: node.restoreEdge });
        return;
    }

    if (node.type === 'group' && Array.isArray(node.children)) {
        let groupEl = parentEl;
        const absorb = !!(options.absorbIntoRoot && parentEl.id === 'splitContainer');
        const isEmptyRoot = parentEl.id === 'splitContainer'
            && getGroupChildNodes(parentEl).length === 0
            && !parentEl.querySelector(':scope > .split-group:not([data-split-discarded="1"])');
        const isRoot = absorb || isEmptyRoot;
        if (!isRoot) {
            groupEl = createSplitGroupEl(node.orientation);
            if (node.flex) groupEl.style.flex = node.flex;
            if (node.id) groupEl.dataset.groupId = node.id;
            const kids = getGroupChildNodes(parentEl);
            if (kids.length >= 1) {
                const handle = document.createElement('div');
                handle.className = 'split-resize-handle';
                insertNode(handle);
            }
            insertNode(groupEl);
        } else {
            applyGroupOrientation(parentEl, node.orientation);
            if (node.id) parentEl.dataset.groupId = node.id;
        }
        // Nested mounts must not absorb into root again.
        const childOpts = {
            allowReuseColumn0: options.allowReuseColumn0,
            absorbIntoRoot: false,
            forceNavigate: options.forceNavigate,
        };
        node.children.forEach((ch) => {
            mountLayoutTree(ch, groupEl, childOpts);
        });
        rebuildGroupResizeHandles(groupEl);
        return;
    }
}

/** Physically remove soft-discarded split nodes after iframes are neutralized.
 *  NEVER call this synchronously from close/teardown — Chromium/Electron freezes
 *  when a just-live iframe tree is removed in the same turn as the click.
 */
function purgeDiscardedSplitNodes(scope) {
    const root = scope || splitContainer;
    if (!root) return;
    const discarded = Array.from(root.querySelectorAll('[data-split-discarded="1"]'));
    discarded.forEach((el) => {
        try {
            el.querySelectorAll('iframe').forEach((fr) => {
                try {
                    fr.removeAttribute('src');
                    fr.srcdoc = '';
                    fr.remove();
                } catch (_) {}
            });
        } catch (_) {}
    });
    // Deepest first so parents do not re-walk detached subtrees.
    discarded
        .slice()
        .sort((a, b) => b.querySelectorAll('*').length - a.querySelectorAll('*').length)
        .forEach((el) => {
            try { el.remove(); } catch (_) {}
        });
    if (root.dataset?.splitDiscarded === '1' && root.id !== 'splitContainer') {
        try { root.remove(); } catch (_) {}
    }
}

let _purgeDiscardedTimer = null;
function schedulePurgeDiscardedSplitNodes(delayMs) {
    const ms = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 1200;
    if (_purgeDiscardedTimer) {
        try { clearTimeout(_purgeDiscardedTimer); } catch (_) {}
    }
    _purgeDiscardedTimer = setTimeout(() => {
        _purgeDiscardedTimer = null;
        try { purgeDiscardedSplitNodes(splitContainer); } catch (err) {
            console.warn('[App Shell] purgeDiscardedSplitNodes failed:', err);
        }
    }, ms);
}

/** Recreate panes from localStorage after column 0 boots. */
function restoreSplitLayout() {
    const layout = savedBootLayout || readSplitLayout();
    if (!layout || !layout.root) return;

    _restoringLayout = true;
    try {
        // Drop anything beyond column 0, then mount tree (reusing col 0).
        removeExtraSplitColumnsNow();
        // Clear root handles / nested leftovers
        if (splitContainer) {
            splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
            splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
                discardSplitSubtree(g);
            });
            applyGroupOrientation(splitContainer, 'horizontal');
        }
        // Do not sync-remove discarded stubs here (Electron freeze). They are
        // display:none and skipped by getGroupChildNodes; purge is deferred.
        schedulePurgeDiscardedSplitNodes(2000);
        // Absorb into #splitContainer so we don't wrap beside the boot column 0.
        try {
            mountLayoutTree(layout.root, splitContainer, {
                allowReuseColumn0: true,
                absorbIntoRoot: true,
            });
        } catch (err) {
            console.error('[App Shell] restoreSplitLayout mount failed:', err);
        }
        getSplitColumns().forEach((c) => { delete c.dataset.layoutMounted; });
        remumberSplitColumns();
        // Apply flex from tree snapshot where present
        const applyFlexWalk = (node, el) => {
            if (!node || !el) return;
            if (node.type === 'leaf' || (!node.type && node.page)) {
                if (node.flex) el.style.flex = node.flex;
                if (node.collapsed && el.classList?.contains('split-column')) {
                    setPaneCollapsed(el, true, { quiet: true, restoreEdge: node.restoreEdge });
                }
                return;
            }
            if (node.type === 'group') {
                const kids = getGroupChildNodes(el);
                (node.children || []).forEach((ch, i) => applyFlexWalk(ch, kids[i]));
                migrateFixedPxSplitFlex(kids, el);
            }
        };
        applyFlexWalk(layout.root, splitContainer);
        // Parked panes change which dividers are restore affordances.
        rebuildSplitResizeHandles();
        // Drop empty nested groups that would show as blank blade-less regions.
        sanitizeSplitDom();
    } finally {
        _restoringLayout = false;
    }
    persistSplitLayout();
}

function rememberHandlesFromWorkspaceEntry(colIdx, entry) {
    if (!entry || !Number.isFinite(colIdx)) return;
    rememberChatHandleFromPage(colIdx, entry.page);
    if (entry.chat) rememberChatHandle(colIdx, 'chat', entry.chat);
    if (entry.terminal) rememberChatHandle(colIdx, 'terminal', entry.terminal);
}

/** Drop extra blades immediately so a saved workspace can replace the layout. */
function removeExtraSplitColumnsNow() {
    // Re-read live columns each pass: collapsing a stack recreates its survivor
    // as a new element, so a precomputed list would skip it and leak the pane.
    let removed = 0;
    for (let guard = 0; guard < 256; guard++) {
        const extras = getSplitColumns().slice(1);
        if (!extras.length) break;
        const col = extras[extras.length - 1];
        removed += 1;
        const idx = parseInt(col.dataset.column, 10);
        paneController.clearRevealColumn(idx);
        columnState.delete(idx);
        paneController?.clearPaneColumn(idx);
        lastChatByColumn.delete(idx);
        if (paneController?.focusedColumnIndex() === idx) paneController.setFocusedColumnDirect(0);
        const parent = getLeafParentGroup(col);
        const prevSib = col.previousElementSibling;
        const nextSib = col.nextElementSibling;
        if (prevSib?.classList?.contains('split-resize-handle')) prevSib.remove();
        else if (nextSib?.classList?.contains('split-resize-handle')) nextSib.remove();
        // Safe teardown — do not probe live iframe contentWindows (Chrome freeze).
        discardSplitSubtree(col);
        collapseSplitGroupIfNeeded(parent);
    }
    if (removed) {
        persistLastChatHandles();
        remumberSplitColumns();
        getSplitColumns().forEach((c) => {
            c.style.flex = '';
            clearPaneBoxStyles(c);
        });
        if (splitContainer) applyGroupOrientation(splitContainer, 'horizontal');
    }
}

function applyWorkspaceColumns(columns, orientation, root, opts = {}) {
    const tree = root && typeof root === 'object'
        ? root
        : flatLayoutToTree(columns, orientation);
    if (!tree) return false;

    _restoringLayout = true;
    try {
        removeExtraSplitColumnsNow();
        if (splitContainer) {
            splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
            splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
                discardSplitSubtree(g);
            });
            applyGroupOrientation(splitContainer, 'horizontal');
        }
        schedulePurgeDiscardedSplitNodes(2000);
        mountLayoutTree(tree, splitContainer, {
            allowReuseColumn0: true,
            absorbIntoRoot: true,
            forceNavigate: !!opts.forceNavigate,
        });
        getSplitColumns().forEach((c) => { delete c.dataset.layoutMounted; });
        remumberSplitColumns();
        sanitizeSplitDom();
    } finally {
        _restoringLayout = false;
    }
    persistSplitLayout();
    return true;
}

// ── Setup listeners for a column ───────────────────────────────
function setupColumnListeners(colIdx, colEl) {
    const els = getColumnElements(colEl);
    if (!els) return;

    const { column } = els;
    ensureRailPaneGrip(column);
    rail.ensureRailEditScrim(column);
    observeRailHeight(column);
    bindRailLogoModifierKeys();

    // Rail logo:
    //   + → append chat at far right
    //   Shift → horizontal split this cell · Alt → vertical split this cell
    //   Alt+Shift → absolute vertical (new cell at very bottom)
    //   Ctrl+ + → close this pane when others exist
    //   × → close · same Shift/Alt split modifiers
    const logoBtn = column.querySelector('.rail-logo-clickable');
    if (logoBtn) {
        logoBtn.addEventListener('click', (e) => {
            const col = column;
            const action = column.querySelector('.rail-logo-clickable')?.dataset.action
                || logoBtn.dataset.action;
            // Alt+Shift = absolute vertical append at bottom of the shell.
            if (e.shiftKey && e.altKey) {
                e.preventDefault();
                appendRootVerticalPane('/chat_page.html');
                return;
            }
            if (e.shiftKey) {
                e.preventDefault();
                splitLeafColumn(col, 'horizontal');
                return;
            }
            if (e.altKey) {
                e.preventDefault();
                splitLeafColumn(col, 'vertical');
                return;
            }
            // Primary + with Ctrl/Cmd: close this pane when others exist
            // so the top cell of a vertical stack can be dismissed without ×.
            if (
                action === 'add'
                && (e.ctrlKey || e.metaKey)
                && getSplitColumns().length >= 2
            ) {
                e.preventDefault();
                closeSplitColumn(col);
                return;
            }
            if (action === 'close') {
                closeSplitColumn(col);
            } else {
                // Primary +: always add a fresh chat at the far right of the root.
                appendRootHorizontalPane('/chat_page.html');
            }
        });
    }

    // Rail nav (a blade icon also unparks its pane, like the divider).
    column.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (rail.interactionLocked()) return;
            if (e.target.closest('.rail-item-remove')) return;
            const idx = parseInt(column.dataset.column, 10);
            if (isPaneCollapsedEl(column)) setPaneCollapsed(column, false);
            navigate(idx, btn.dataset.page);
        });
    });

    // Notifications rail button (opens popover, does not navigate)
    column.querySelector('[data-id="nav-notifications"]')?.addEventListener('click', () => {
        if (rail.interactionLocked()) return;
        toggleNotificationsPopover();
    });

    column.querySelector('[data-id="nav-workspace"]')?.addEventListener('click', () => {
        if (rail.interactionLocked()) return;
        toggleWorkspacePopover();
    });

    // Account rail button (sign in / sign out, does not navigate)
    column.querySelector('[data-id="nav-account"]')?.addEventListener('click', () => {
        if (rail.interactionLocked()) return;
        const idx = parseInt(column.dataset.column, 10);
        handleAccountClick(idx);
    });

    rail.bindCollapseControls(column);
    rail.setupRailCustomize(column);
    setupPaneReorder(column);
}

// Run setup for column 0 (existing)
setupColumnListeners(0, getColumnEl(0));
ensureLeafId(getColumnEl(0));
observeRailHeight(getColumnEl(0));
bindRailLogoModifierKeys();
if (splitContainer) applyGroupOrientation(splitContainer, 'horizontal');
// Recreate extra split panes from last session (localStorage)
restoreSplitLayout();
sanitizeSplitDom();
updateSplitContainerChrome();
syncSplitColumnLogos();
syncSplitColumnLogos();

// ── Rail customization / apps list (owner: shell_rail.js) ───────
// The rail layout lifetime moved to shell_rail.js; app_shell only runs its
// init (persisted layout load + agent-feed/apps message wiring).
rail.setup();

function basePagePath(p) {
    if (!p) return '';
    const s = String(p);
    const q = s.indexOf('?');
    return q >= 0 ? s.slice(0, q) : s;
}

function updateColumnUI(colIdx, page) {
    const col = getColumnEl(colIdx);
    if (!col) return;
    const cur = basePagePath(page);
    col.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        btn.classList.toggle('active', basePagePath(btn.dataset.page) === cur);
    });
}

// ── Navigate: replace iframe element for reliability ──────────
// Using frame.src = url silently fails in some renderer states
// Replace the frame to avoid carrying stale renderer state across pages.
// Replacing the DOM element is unconditionally reliable.
/** Navigate the split column that contains `source` (child iframe window). */
function navigateFromSource(source, page) {
    navigate(findColumnIndexForSource(source), page);
}

function navigate(colIdx, page, opts) { return paneController.navigate(colIdx, page, opts); }

// ── Account button (rail footer) ────────────────────────────────
const ACCOUNT_PLACEHOLDER_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

let shellAuthUser = null;

function accountDisplayName(user) {
    if (!user) return '';
    return user.username ? '@' + user.username : (user.display_name || user.email || 'Signed in');
}

function accountInitials(user) {
    if (window.CuttleAuth && window.CuttleAuth.userAvatarInitials) {
        return window.CuttleAuth.userAvatarInitials(user);
    }
    const label = user.display_name || user.username || user.email || '?';
    const initials = label.trim().split(/[\s._-]+/).filter(Boolean).map(p => p[0]).join('');
    return (initials || '?').toUpperCase().slice(0, 2);
}

function accountAvatarInnerHtml(user) {
    if (window.CuttleAuth && window.CuttleAuth.userAvatarInnerHtml) {
        return window.CuttleAuth.userAvatarInnerHtml(user);
    }
    if (user.profile_image) {
        return `<img src="${escapeHtml(user.profile_image)}" alt="">`;
    }
    return accountInitials(user);
}

function renderAccountButtons() {
    document.querySelectorAll('.rail-account').forEach(btn => {
        const avatar = btn.querySelector('.rail-account-avatar');
        btn.classList.toggle('signed-in', !!shellAuthUser);
        btn.classList.toggle('signed-out', !shellAuthUser);
        if (shellAuthUser) {
            const name = accountDisplayName(shellAuthUser);
            btn.dataset.tooltip = 'Account — ' + name;
            btn.setAttribute('aria-label', 'Account — signed in as ' + name + '. Click to log out');
            if (avatar) {
                avatar.innerHTML = accountAvatarInnerHtml(shellAuthUser);
            }
        } else {
            btn.dataset.tooltip = 'Account — signed out';
            btn.setAttribute('aria-label', 'Account — signed out. Click to sign in');
            if (avatar) avatar.innerHTML = ACCOUNT_PLACEHOLDER_SVG;
        }
    });
}

function setShellAuthUser(user) {
    const previousId = shellAuthUser && shellAuthUser.id;
    shellAuthUser = user || null;
    if (previousId !== (shellAuthUser && shellAuthUser.id) && window.cuttleActivityBroker) {
        CuttleSpaces.resetActivity();
        window.cuttleActivityBroker.refresh();
    }
    renderAccountButtons();
    // The Account Apps-grid tile carries the profile photo — rebroadcast so
    // an open Apps page swaps the placeholder for the picture on sign in/out.
    rail.broadcastAppsList();
}

async function refreshShellAuth() {
    try {
        const res = await fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' });
        const data = await res.json().catch(() => null);
        if (data && typeof data.authenticated === 'boolean') {
            setShellAuthUser(data.authenticated ? data.user : null);
        }
        // Network/parse failure: keep prior shellAuthUser (don't flash signed-out).
    } catch (_) {
        /* keep prior */
    }
}

/** Open the app-wide sign-in modal (lives on the shell, not the chat page). */
function openSignInForColumn(_colIdx) {
    if (typeof openAuthModal === 'function') {
        openAuthModal('login');
        return;
    }
    if (window.CuttleAuth && typeof window.CuttleAuth.openAuthModal === 'function') {
        window.CuttleAuth.openAuthModal('login');
        return;
    }
    // Fallback: ask the chat iframe to open its modal (standalone / older builds)
    navigate(0, '/chat_page.html?signin=1');
}

// Account panel DOM, sign-out and profile forms: js/shell/account_panel.js.
const accountPanel = CuttleAccountPanel.create({
    getUser: () => shellAuthUser,
    setUser: setShellAuthUser,
    displayName: accountDisplayName,
    avatarHtml: accountAvatarInnerHtml,
    syncServerRows: () => cuttleSyncMobileServerRows(),
});
function closeLogoutConfirmModal() { accountPanel.close(); }
function openLogoutConfirmModal() { accountPanel.open(); }
function setupLogoutConfirmModal() { accountPanel.setup(); }

async function handleAccountClick(_colIdx) {
    if (!shellAuthUser) {
        openSignInForColumn(_colIdx);
        return;
    }
    openLogoutConfirmModal();
}

document.addEventListener('click', (e) => {
    const t = e.target?.closest?.('#logoutConfirmSwitchServer, #authServerChange');
    if (!t) return;
    e.preventDefault();
    closeLogoutConfirmModal();
    cuttleOpenMobileServerSettings();
});

setupLogoutConfirmModal();
renderAccountButtons();
refreshShellAuth();
(function handleAuthQueryFlags() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.has('auth_linked') || params.has('auth_success')) {
            refreshShellAuth().then(() => {
                params.delete('auth_linked');
                params.delete('auth_success');
                params.delete('auth_error');
                const q = params.toString();
                const next = window.location.pathname + (q ? '?' + q : '') + window.location.hash;
                window.history.replaceState({}, document.title, next);
            });
        } else if (params.has('auth_error')) {
            const err = params.get('auth_error') || 'unknown';
            console.warn('OAuth error:', err);
            params.delete('auth_error');
            const q = params.toString();
            window.history.replaceState({}, document.title, window.location.pathname + (q ? '?' + q : ''));
        }
    } catch (_) {}
})();
window.addEventListener('focus', refreshShellAuth);
window.addEventListener('cuttle-auth-changed', function (ev) {
    setShellAuthUser((ev.detail && ev.detail.user) || null);
});

// ── Notifications popover (past toasts) ─────────────────────────
function getNotificationsPopover() {
    return document.getElementById('notificationsPopover');
}

function isNotificationsPopoverOpen() {
    const pop = getNotificationsPopover();
    return pop && pop.classList.contains('notifications-popover-open');
}

function openNotificationsPopover() {
    closeWorkspacePopover();
    const pop = getNotificationsPopover();
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (pop) pop.classList.add('notifications-popover-open');
    if (back) back.classList.add('notifications-popover-backdrop-visible');
    renderNotificationsList();
}

function closeNotificationsPopover() {
    const pop = getNotificationsPopover();
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (pop) pop.classList.remove('notifications-popover-open');
    if (typeof setWorkspaceBackdropVisible === 'function') setWorkspaceBackdropVisible(false);
    else if (back) back.classList.remove('notifications-popover-backdrop-visible');
}

function toggleNotificationsPopover() {
    if (isNotificationsPopoverOpen()) closeNotificationsPopover();
    else {
        closeWorkspacePopover();
        openNotificationsPopover();
    }
}

function notificationUnreadIconHTML() {
    return '<span class="notifications-unread-icon" title="Unread response" aria-label="Unread response"></span>';
}

function openChatFromNotification(sessionId) {
    if (sessionId == null || sessionId === '') return;
    let sid = String(sessionId).trim();
    if (/^CH-\d+$/i.test(sid)) sid = String(parseInt(sid.slice(3), 10));
    else if (sid.startsWith('db_session_')) sid = sid.slice('db_session_'.length);
    const page = '/chat_page.html?chat=' + encodeURIComponent(sid);
    // Already on this chat (cold start deep-linked ?chat=) — do not replaceFrame again.
    try {
        const st = typeof getState === 'function' ? getState(0) : null;
        if (st && st.page === page) {
            closeNotificationsPopover();
            return;
        }
        const cur = new URL(window.location.href);
        if (cur.searchParams.get('chat') === sid || cur.searchParams.get('session') === sid) {
            const live = typeof getLiveFrame === 'function' ? getLiveFrame(0) : null;
            const href = live?.contentWindow?.location?.href || '';
            if (href && href.includes('chat=' + encodeURIComponent(sid))) {
                closeNotificationsPopover();
                return;
            }
        }
    } catch (_) {}
    if (typeof navigate === 'function') navigate(0, page);
    else if (typeof window.navigate === 'function') window.navigate(0, page);
    closeNotificationsPopover();
}
window.openChatFromNotification = openChatFromNotification;

function renderNotificationsList() {
    const listEl = document.getElementById('notificationsPopoverList');
    const emptyEl = document.getElementById('notificationsPopoverEmpty');
    if (!listEl || !emptyEl) return;
    const history = typeof window.getCuttleNotificationHistory === 'function' ? window.getCuttleNotificationHistory() : [];
    if (history.length === 0) {
        listEl.innerHTML = '';
        listEl.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
    }
    emptyEl.style.display = 'none';
    listEl.style.display = 'block';
    const VARIANTS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    listEl.innerHTML = history.map((n, i) => {
        const icon = VARIANTS[n.variant] || 'ℹ';
        const timeStr = n.time ? new Date(n.time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
        const linkHtml = n.linkUrl ? ` <a href="${escapeHtml(n.linkUrl)}" target="_blank" rel="noopener" class="notifications-popover-item-link">${escapeHtml(n.linkText || 'View query log')}</a>` : '';
        const isUnreadResponse = !!(n.unread && !n.read);
        const unreadIcon = isUnreadResponse ? notificationUnreadIconHTML() : '';
        const unreadClass = isUnreadResponse ? ' is-unread' : '';
        const clickable = n.sessionId ? ' is-clickable' : '';
        const sidAttr = n.sessionId ? ` data-session-id="${escapeHtml(String(n.sessionId))}"` : '';
        const idAttr = n.id ? ` data-notif-id="${escapeHtml(String(n.id))}"` : '';
        return `<div class="notifications-popover-item notifications-popover-item-${n.variant}${unreadClass}${clickable}" data-index="${i}"${idAttr}${sidAttr}><span class="notifications-popover-item-icon">${escapeHtml(icon)}</span><span class="notifications-popover-item-message">${escapeHtml(n.message)}${linkHtml}</span>${unreadIcon}<span class="notifications-popover-item-time">${escapeHtml(timeStr)}</span></div>`;
    }).join('');

    listEl.querySelectorAll('.notifications-popover-item').forEach((el) => {
        el.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            const notifId = el.dataset.notifId;
            if (notifId && typeof window.markCuttleNotificationsRead === 'function') {
                window.markCuttleNotificationsRead(notifId);
            }
            const sid = el.dataset.sessionId;
            if (sid) openChatFromNotification(sid);
            else {
                renderNotificationsList();
                updateNotificationsBadge();
            }
        });
    });
}

function updateNotificationsBadge() {
    const unreadCount = typeof window.getCuttleUnreadNotificationCount === 'function'
        ? window.getCuttleUnreadNotificationCount()
        : 0;
    document.querySelectorAll('.rail-badge-notifications').forEach(badge => {
        badge.textContent = String(unreadCount);
        badge.style.display = unreadCount > 0 ? '' : 'none';
    });
}

/** Chats with replies awaiting attention (unread/error dots), minus visible panes. */
function chatUnreadCount() {
    try {
        return CuttleSpaces.countUnreadSessions(shellSpaces.visibleActiveChatIds());
    } catch (_) {
        return 0;
    }
}

/** Green count badge on the Chat (message) rail button, mirrored to the Apps page. */
function updateChatUnreadBadge() {
    const count = chatUnreadCount();
    document.querySelectorAll('.rail-badge-chat').forEach(badge => {
        badge.textContent = count > 0 ? String(count) : '0';
        badge.style.display = count > 0 ? '' : 'none';
    });
    broadcastToFrames({ type: 'cuttle-chat-unread', count });
}

(function setupNotificationsPopover() {
    const back = document.getElementById('notificationsPopoverBackdrop');
    const clearBtn = document.getElementById('notificationsClearBtn');
    if (back) back.addEventListener('click', () => {
        closeNotificationsPopover();
        closeWorkspacePopover();
    });
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            if (typeof window.clearCuttleNotificationHistory === 'function') window.clearCuttleNotificationHistory();
            renderNotificationsList();
            updateNotificationsBadge();
        });
    }
    window.addEventListener('cuttle-notification-added', updateNotificationsBadge);
    updateNotificationsBadge();
})();

// ── Workspace save / load (cross-device split + open chats) ─────
function getWorkspacePopover() {
    return document.getElementById('workspacePopover');
}

function isWorkspacePopoverOpen() {
    const pop = getWorkspacePopover();
    return pop && pop.classList.contains('notifications-popover-open');
}

function setWorkspaceBackdropVisible(on) {
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (!back) return;
    if (on || isNotificationsPopoverOpen() || isWorkspacePopoverOpen()) {
        back.classList.add('notifications-popover-backdrop-visible');
    } else {
        back.classList.remove('notifications-popover-backdrop-visible');
    }
}

function openWorkspacePopover() {
    const pop = getWorkspacePopover();
    if (pop) pop.classList.add('notifications-popover-open');
    setWorkspaceBackdropVisible(true);
    const input = document.getElementById('workspaceNameInput');
    if (input && !input.value.trim()) input.value = 'Workspace';
    refreshWorkspacePopover();
}

function closeWorkspacePopover() {
    const pop = getWorkspacePopover();
    if (pop) pop.classList.remove('notifications-popover-open');
    setWorkspaceBackdropVisible(false);
}

function toggleWorkspacePopover() {
    if (isWorkspacePopoverOpen()) closeWorkspacePopover();
    else {
        closeNotificationsPopover();
        openWorkspacePopover();
    }
}

function workspaceColumnLabel(entry) {
    const page = (entry && entry.page) || '';
    try {
        const url = new URL(page, window.location.origin);
        const title = PAGE_TITLES[url.pathname] || (url.pathname.replace(/^\//, '').replace(/\.html$/, '') || 'Pane');
        const cid = url.searchParams.get('chat') || url.searchParams.get('session')
            || (entry && (entry.chat || entry.terminal));
        if (cid) {
            const raw = String(cid);
            const display = /^\d+$/.test(raw) ? ('CH-' + raw.padStart(6, '0')) : raw;
            return title + ' ' + display;
        }
        return title;
    } catch (_) {
        return page || 'Pane';
    }
}

function formatWorkspaceSummary(columns) {
    const cols = Array.isArray(columns) ? columns : [];
    const n = Math.max(1, cols.length);
    const paneWord = n === 1 ? 'pane' : 'panes';
    const labels = cols.map(workspaceColumnLabel).filter(Boolean);
    return n + ' ' + paneWord + (labels.length ? (' · ' + labels.join(' · ')) : '');
}

function formatWorkspaceTime(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return '';
    try {
        return new Date(n * 1000).toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        });
    } catch (_) {
        return '';
    }
}

async function fetchWorkspaces() {
    const res = await fetch('/api/shell/workspaces', { cache: 'no-store', credentials: 'same-origin' });
    if (res.status === 404) return [];
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.error || 'Could not load workspaces');
    return Array.isArray(data.workspaces) ? data.workspaces : [];
}

const DEFAULT_WORKSPACE_ID = '__default__';

function getBuiltinDefaultWorkspace() {
    return {
        id: DEFAULT_WORKSPACE_ID,
        name: 'Default',
        summary: '1 pane · single viewport',
        builtin: true,
        columns: [{ page: '/chat_page.html' }],
        orientation: 'horizontal',
        root: { type: 'leaf', page: '/chat_page.html', flex: '' },
    };
}

/** Collapse the shell to a single full-bleed viewport (built-in Default workspace). */
function applyDefaultWorkspace() {
    removeExtraSplitColumnsNow();
    if (splitContainer) {
        splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => {
            try { h.remove(); } catch (_) {}
        });
        splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
            discardSplitSubtree(g);
        });
        applyGroupOrientation(splitContainer, 'horizontal');
    }
    sanitizeSplitDom();
    const cols = getSplitColumns();
    cols.forEach((c) => {
        c.style.flex = '';
        clearPaneBoxStyles(c);
    });
    remumberSplitColumns();
    persistSplitLayout();
    closeWorkspacePopover();
    if (typeof window.showToast === 'function') {
        window.showToast('Loaded Default · 1 pane', 'success');
    }
    return true;
}

function renderWorkspaceList(workspaces) {
    const listEl = document.getElementById('workspaceList');
    const emptyEl = document.getElementById('workspaceEmpty');
    if (!listEl || !emptyEl) return;
    const saved = Array.isArray(workspaces) ? workspaces : [];
    const items = [getBuiltinDefaultWorkspace()].concat(
        saved.filter((ws) => String(ws?.id || '') !== DEFAULT_WORKSPACE_ID
            && String(ws?.name || '').trim().toLowerCase() !== 'default')
    );
    emptyEl.style.display = 'none';
    listEl.innerHTML = items.map((ws) => {
        const id = escapeHtml(String(ws.id || ''));
        const name = escapeHtml(String(ws.name || 'Workspace'));
        const summary = escapeHtml(ws.summary || formatWorkspaceSummary(ws.columns));
        const when = ws.builtin ? '' : escapeHtml(formatWorkspaceTime(ws.updated_at));
        const badge = ws.builtin ? '<span class="workspace-item-badge">built-in</span>' : '';
        const deleteBtn = ws.builtin
            ? ''
            : '<button type="button" class="workspace-btn workspace-btn-danger" data-workspace-delete>Delete</button>';
        return '<div class="workspace-item' + (ws.builtin ? ' workspace-item-builtin' : '')
            + '" data-workspace-id="' + id + '"'
            + (ws.builtin ? ' data-workspace-builtin="1"' : '') + '>'
            + '<div class="workspace-item-main">'
            + '<div class="workspace-item-name">' + name + badge + (when ? ' · ' + when : '') + '</div>'
            + '<div class="workspace-item-summary">' + summary + '</div>'
            + '</div>'
            + '<div class="workspace-item-actions">'
            + '<button type="button" class="workspace-btn workspace-btn-primary" data-workspace-load>Load</button>'
            + deleteBtn
            + '</div></div>';
    }).join('');
    listEl.querySelectorAll('[data-workspace-load]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.workspace-item');
            const id = row?.dataset.workspaceId;
            if (id === DEFAULT_WORKSPACE_ID || row?.dataset.workspaceBuiltin === '1') {
                applyDefaultWorkspace();
                return;
            }
            const ws = saved.find((x) => String(x.id) === String(id));
            if (ws) loadWorkspace(ws);
        });
    });
    listEl.querySelectorAll('[data-workspace-delete]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.workspace-item');
            if (row?.dataset.workspaceBuiltin === '1') return;
            const id = row?.dataset.workspaceId;
            if (id) deleteWorkspace(id);
        });
    });
}

async function refreshWorkspacePopover() {
    const summaryEl = document.getElementById('workspaceCurrentSummary');
    if (summaryEl) summaryEl.textContent = formatWorkspaceSummary(snapshotSplitColumns());
    try {
        renderWorkspaceList(await fetchWorkspaces());
    } catch (err) {
        // Still show the built-in Default entry when the API is unreachable.
        renderWorkspaceList([]);
        const emptyEl = document.getElementById('workspaceEmpty');
        if (emptyEl && err) {
            emptyEl.style.display = 'block';
            emptyEl.textContent = (err && err.message) || 'Could not load workspaces';
        }
    }
}

async function saveCurrentWorkspace() {
    const input = document.getElementById('workspaceNameInput');
    const btn = document.getElementById('workspaceSaveBtn');
    const name = ((input && input.value) || 'Workspace').trim() || 'Workspace';
    if (name.toLowerCase() === 'default') {
        if (typeof window.showToast === 'function') {
            window.showToast('Default is built-in and cannot be overwritten', 'info');
        }
        return;
    }
    const columns = snapshotSplitColumns();
    if (btn) btn.disabled = true;
    try {
        const res = await fetch('/api/shell/workspaces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({
                name,
                columns,
                orientation: primaryOrientationFromTree(snapshotLayoutTree(splitContainer)),
                root: snapshotLayoutTree(splitContainer),
                spaces: shellSpaces.exportSnapshot(),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error(data.error || 'Save failed');
        if (typeof window.showToast === 'function') {
            window.showToast('Saved workspace · ' + formatWorkspaceSummary(columns), 'success');
        }
        renderWorkspaceList(data.workspaces || []);
    } catch (err) {
        if (typeof window.showToast === 'function') {
            window.showToast((err && err.message) || 'Save failed', 'error');
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function loadWorkspace(ws) {
    if (ws && (ws.builtin || String(ws.id) === DEFAULT_WORKSPACE_ID)) {
        applyDefaultWorkspace();
        return;
    }
    const columns = ws && ws.columns;
    // Spaces (tabs + groups) carry each space's layout; older saves are panes only.
    const applied = (ws && ws.spaces && shellSpaces.importSnapshot(ws.spaces))
        || applyWorkspaceColumns(columns, ws && ws.orientation, ws && ws.root);
    if (!applied) {
        if (typeof window.showToast === 'function') window.showToast('Could not load workspace', 'error');
        return;
    }
    closeWorkspacePopover();
    if (typeof window.showToast === 'function') {
        window.showToast('Loaded ' + (ws.name || 'workspace') + ' · ' + (ws.summary || formatWorkspaceSummary(columns)), 'success');
    }
}

async function deleteWorkspace(id) {
    if (String(id) === DEFAULT_WORKSPACE_ID) return;
    try {
        const res = await fetch('/api/shell/workspaces/' + encodeURIComponent(id), {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error(data.error || 'Delete failed');
        renderWorkspaceList(data.workspaces || []);
        if (typeof window.showToast === 'function') window.showToast('Workspace deleted', 'info');
    } catch (err) {
        if (typeof window.showToast === 'function') {
            window.showToast((err && err.message) || 'Delete failed', 'error');
        }
    }
}

(function setupWorkspacePopover() {
    document.getElementById('workspaceSaveBtn')?.addEventListener('click', () => saveCurrentWorkspace());
    document.getElementById('workspaceNameInput')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveCurrentWorkspace();
        }
    });
})();

// ── Spaces tab strip (owner: shell_spaces.js) ─────────────────
// Tab rendering, group menus, selection wiring, activity dots, the tab
// drag/drop and the cross-space pane-drag effects all live in
// CuttleShellSpaces; app_shell only calls its setup() and wires shared events.
shellSpaces.setup();

// ── Pane reorder (grip under logo) ─────────────────────────────
// RAIL_SVG_* / primaryRailLogoInnerHtml / closeRailLogoInnerHtml are defined
// earlier (before createSplitLeafColumn) so restoreSplitLayout cannot hit TDZ.

function ensureRailLogoModLayers(logo) {
    if (!logo || logo.querySelector('.rail-logo-split-v')) return;
    logo.insertAdjacentHTML('beforeend', RAIL_LOGO_MOD_LAYERS);
}

function defaultRailLogoTooltip(action) {
    return action === 'add'
        ? 'Add chat on the right · Shift horizontal · Alt vertical · Alt+Shift absolute vertical · Ctrl closes this pane'
        : 'Close · Shift horizontal split · Alt vertical split';
}

function syncRailLogoModifierUi() {
    const absVertical = !!_railModShift && !!_railModAlt;
    document.body.classList.toggle('rail-mod-alt-shift', absVertical);
    document.body.classList.toggle('rail-mod-shift', !!_railModShift && !absVertical);
    document.body.classList.toggle('rail-mod-alt', !!_railModAlt && !absVertical);
    document.querySelectorAll('.rail-logo-clickable').forEach((logo) => {
        const action = logo.dataset.action || 'add';
        if (absVertical) {
            logo.dataset.tooltip = 'Absolute vertical split — new cell at bottom';
        } else if (_railModShift) {
            logo.dataset.tooltip = 'Horizontal split this pane';
        } else if (_railModAlt) {
            logo.dataset.tooltip = 'Vertical split this pane';
        } else {
            logo.dataset.tooltip = defaultRailLogoTooltip(action);
        }
    });
}

function bindRailLogoModifierKeys() {
    if (document.documentElement.dataset.railModKeysBound === '1') return;
    document.documentElement.dataset.railModKeysBound = '1';

    const refresh = (e) => {
        if (!e) return;
        const nextShift = !!e.shiftKey;
        const nextAlt = !!e.altKey;
        const nextCtrl = !!(e.ctrlKey || e.metaKey);
        if (nextShift === _railModShift && nextAlt === _railModAlt && nextCtrl === _railModCtrl) {
            return;
        }
        _railModShift = nextShift;
        _railModAlt = nextAlt;
        _railModCtrl = nextCtrl;
        syncRailLogoModifierUi();
    };

    const clearMods = () => {
        if (!_railModShift && !_railModAlt && !_railModCtrl) return;
        _railModShift = false;
        _railModAlt = false;
        _railModCtrl = false;
        syncRailLogoModifierUi();
    };

    const bindDoc = (doc) => {
        if (!doc || !doc.documentElement) return;
        if (doc.documentElement.dataset.railModKeysDocBound === '1') return;
        doc.documentElement.dataset.railModKeysDocBound = '1';
        doc.addEventListener('keydown', refresh, true);
        doc.addEventListener('keyup', refresh, true);
        doc.addEventListener('blur', clearMods, true);
    };

    bindDoc(document);
    window.addEventListener('blur', clearMods);

    // Pointer over logo: sync modifiers from the mouse event (covers press-then-hover).
    document.addEventListener('pointerover', (e) => {
        const logo = e.target?.closest?.('.rail-logo-clickable');
        if (!logo) return;
        logo.classList.add('is-rail-logo-hot');
        refresh(e);
    }, true);
    document.addEventListener('pointerout', (e) => {
        const logo = e.target?.closest?.('.rail-logo-clickable');
        if (!logo) return;
        const toLogo = e.relatedTarget?.closest?.('.rail-logo-clickable');
        if (toLogo === logo) return;
        logo.classList.remove('is-rail-logo-hot');
    }, true);
    document.addEventListener('pointermove', (e) => {
        if (!e.target?.closest?.('.rail-logo-clickable')) return;
        refresh(e);
    }, true);

    // Fixed tips for icons inside the scrolling rail (avoids horizontal scrollbar).
    document.addEventListener('pointerover', (e) => {
        const item = e.target?.closest?.('.rail-items .rail-item');
        if (!item) return;
        const r = item.getBoundingClientRect();
        item.style.setProperty('--rail-tip-left', Math.round(r.right + 12) + 'px');
        item.style.setProperty('--rail-tip-top', Math.round(r.top + r.height / 2) + 'px');
    }, true);

    /** Call after iframe loads so Shift/Alt while hovering still update (focus is often in the frame). */
    window.bindRailModKeysForFrame = (frame) => {
        if (!frame) return;
        try {
            bindDoc(frame.contentDocument);
        } catch (_) {}
        try {
            const win = frame.contentWindow;
            if (win && win.__railModKeysWinBound !== 1) {
                win.__railModKeysWinBound = 1;
                win.addEventListener('keydown', refresh, true);
                win.addEventListener('keyup', refresh, true);
                win.addEventListener('blur', clearMods);
            }
        } catch (_) {}
    };

    // Bind any frames already present.
    document.querySelectorAll('.shell-main iframe').forEach((fr) => {
        try { window.bindRailModKeysForFrame(fr); } catch (_) {}
    });
}

function syncRailHeightChrome(colEl) {
    if (!colEl || !colEl.classList?.contains('split-column')) return;
    const h = colEl.clientHeight || 0;
    colEl.classList.toggle('rail-compact', h > 0 && h < 320);
    colEl.classList.toggle('rail-tight', h > 0 && h < 200);
}

function observeRailHeight(colEl) {
    if (!colEl || typeof ResizeObserver === 'undefined') {
        syncRailHeightChrome(colEl);
        return;
    }
    if (_railHeightObservers.has(colEl)) {
        syncRailHeightChrome(colEl);
        return;
    }
    const ro = new ResizeObserver(() => syncRailHeightChrome(colEl));
    ro.observe(colEl);
    _railHeightObservers.set(colEl, ro);
    syncRailHeightChrome(colEl);
}

function ensurePaneDropOverlay(colEl) {
    if (!colEl) return null;
    let el = colEl.querySelector(':scope > .pane-drop-overlay');
    if (!el) {
        el = document.createElement('div');
        el.className = 'pane-drop-overlay';
        el.setAttribute('aria-hidden', 'true');
        colEl.appendChild(el);
    }
    return el;
}

function ensureRailPaneGrip(colEl) {
    ensurePaneDropOverlay(colEl);
    const rail = colEl?.querySelector?.('.icon-rail');
    if (!rail) return null;
    let grip = rail.querySelector('.rail-pane-grip');
    if (!grip) {
        const logo = rail.querySelector('.rail-logo');
        grip = document.createElement('button');
        grip.type = 'button';
        grip.className = 'rail-pane-grip';
        grip.dataset.tooltip = 'Drag onto a pane to swap · onto a space tab to move';
        grip.setAttribute('aria-label', 'Drag onto a pane to swap · onto a space tab to move');
        grip.innerHTML = RAIL_PANE_GRIP_HTML;
        if (logo) logo.insertAdjacentElement('afterend', grip);
        else rail.insertBefore(grip, rail.firstChild);
    }
    return grip;
}

function updateSplitContainerChrome() {
    if (!splitContainer) return;
    const n = getSplitColumns().length;
    splitContainer.classList.toggle('has-splits', n >= 2);
    splitContainer.querySelectorAll('.split-group').forEach((g) => {
        g.classList.toggle('has-splits', getGroupChildNodes(g).length >= 2);
    });
}

function syncSplitColumnLogos() {
    const cols = getSplitColumns();
    cols.forEach((col, i) => {
        ensureLeafId(col);
        ensureRailPaneGrip(col);
        observeRailHeight(col);
        let logo = col.querySelector('.rail-logo');
        if (!logo) return;
        if (i === 0) {
            if (logo.dataset.action !== 'add') {
                logo.className = 'rail-logo rail-logo-primary rail-logo-clickable';
                logo.dataset.action = 'add';
                logo.removeAttribute('title');
                logo.id = 'railLogo';
                logo.innerHTML = primaryRailLogoInnerHtml();
            } else {
                ensureRailLogoModLayers(logo);
                if (!logo.querySelector('.rail-logo-plus')) {
                    logo.innerHTML = primaryRailLogoInnerHtml();
                }
            }
            logo.dataset.tooltip = defaultRailLogoTooltip('add');
        } else if (logo.dataset.action !== 'close') {
            logo.className = 'rail-logo rail-logo-close rail-logo-clickable';
            logo.dataset.action = 'close';
            logo.removeAttribute('title');
            logo.removeAttribute('id');
            logo.innerHTML = closeRailLogoInnerHtml();
            logo.dataset.tooltip = defaultRailLogoTooltip('close');
        } else {
            ensureRailLogoModLayers(logo);
            if (!logo.querySelector('.rail-logo-close-face')) {
                logo.innerHTML = closeRailLogoInnerHtml();
            }
            logo.dataset.tooltip = defaultRailLogoTooltip('close');
        }
    });
    syncRailLogoModifierUi();
}

/** Rebuild resize handles for one group (direct children only). */
function rebuildGroupResizeHandles(groupEl) {
    if (!groupEl || !isSplitGroupEl(groupEl)) return;
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) ch.remove();
    });
    const kids = getGroupChildNodes(groupEl);
    kids.forEach((kid, i) => {
        kid.style.order = String(i * 2);
        if (kid.classList.contains('split-column')) ensurePaneDropOverlay(kid);
    });
    for (let i = 0; i < kids.length - 1; i++) {
        const handle = document.createElement('div');
        handle.className = 'split-resize-handle';
        handle.style.order = String(i * 2 + 1);
        handle.dataset.groupId = groupEl.dataset.groupId || groupEl.id || '';
        handle.dataset.between = `${i}-${i + 1}`;
        // Restore on the edge used to collapse; old layouts default to
        // the leading edge, with a trailing fallback for the first pane.
        const leftParked = isPaneCollapsedEl(kids[i]);
        const rightParked = isPaneCollapsedEl(kids[i + 1]);
        const isLeadingEdge = rightParked && kids[i + 1].dataset.restoreEdge !== 'trailing';
        const isTrailingFallback = leftParked && (kids[i].dataset.restoreEdge === 'trailing' || i === 0) && !isLeadingEdge;
        if (isLeadingEdge || isTrailingFallback) {
            handle.classList.add('pane-expand');
            handle.dataset.expand = '1';
            handle.title = 'Expand pane';
            handle.setAttribute('role', 'button');
            handle.setAttribute('aria-label', 'Expand collapsed pane');
            const verticalHandles = getGroupOrientation(groupEl) === 'vertical';
            if (isLeadingEdge) {
                handle.dataset.expandDir = verticalHandles ? 'up' : 'left';
            } else if (isTrailingFallback) {
                handle.dataset.expandDir = verticalHandles ? 'down' : 'right';
            }
        }
        groupEl.appendChild(handle);
        setupGroupSplitResize(handle, groupEl, i, i + 1);
    }
}

function rebuildSplitResizeHandles() {
    if (!splitContainer) return;
    rebuildGroupResizeHandles(splitContainer);
    splitContainer.querySelectorAll('.split-group').forEach((g) => rebuildGroupResizeHandles(g));
}

/** Remumber after reorder/close so col 0 stays the primary (+ logo) blade (1st = DFS reading order). */
function remumberSplitColumns() {
    const cols = getSplitColumns();
    const oldToNew = new Map();
    const nextState = new Map();
    const nextChatHandles = new Map();
    const claimedOldIdx = new Set();

    cols.forEach((col, newIdx) => {
        ensureLeafId(col);
        const oldIdx = parseInt(col.dataset.column, 10);
        const oldValid = Number.isFinite(oldIdx);
        // Each prior index may map to only one new column. Fresh clones used to
        // share data-column="0", which copied pane 0's chat onto every new leaf.
        const claim = oldValid && !claimedOldIdx.has(oldIdx);
        if (claim) {
            claimedOldIdx.add(oldIdx);
            oldToNew.set(oldIdx, newIdx);
            if (columnState.has(oldIdx)) nextState.set(newIdx, columnState.get(oldIdx));
            if (lastChatByColumn.has(oldIdx)) {
                nextChatHandles.set(newIdx, { ...lastChatByColumn.get(oldIdx) });
            }
        }
        col.dataset.column = String(newIdx);
        col.id = `splitColumn${newIdx}`;
    });

    columnState.clear();
    nextState.forEach((v, k) => columnState.set(k, v));
    paneController.remapColumns(oldToNew);
    paneController.remapRevealColumns(oldToNew);
    lastChatByColumn.clear();
    nextChatHandles.forEach((v, k) => lastChatByColumn.set(k, v));
    persistLastChatHandles();

    document.querySelectorAll('.shell-main iframe#contentFrame').forEach((fr) => {
        fr.removeAttribute('id');
    });
    if (cols[0]) {
        const main0 = cols[0].querySelector('.shell-main');
        const frames = main0
            ? Array.from(main0.querySelectorAll('iframe'))
                .filter((f) => !f.classList.contains('shell-frame-outgoing'))
            : [];
        const live0 = frames.find((f) => f.classList.contains('shell-frame-pending'))
            || frames[frames.length - 1]
            || null;
        if (live0) live0.id = 'contentFrame';
    }

    syncSplitColumnLogos();
    rebuildSplitResizeHandles();
    updateSplitContainerChrome();
    broadcastPaneFocus();
}

function clearPaneDropIndicators() {
    document.querySelectorAll('.split-column.pane-drop-before, .split-column.pane-drop-after, .split-column.pane-drop-target')
        .forEach((c) => c.classList.remove('pane-drop-before', 'pane-drop-after', 'pane-drop-target'));
}

function stripGroupResizeHandles(groupEl) {
    if (!groupEl) return;
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) {
            try { ch.remove(); } catch (_) {}
        }
    });
}

/** Swap two Map entries by key (missing keys stay missing). */
function swapMapKeys(map, keyA, keyB) {
    if (!map || keyA === keyB) return;
    const hasA = map.has(keyA);
    const hasB = map.has(keyB);
    const valA = hasA ? map.get(keyA) : undefined;
    const valB = hasB ? map.get(keyB) : undefined;
    if (hasB) map.set(keyA, valB);
    else map.delete(keyA);
    if (hasA) map.set(keyB, valA);
    else map.delete(keyB);
}

/**
 * Swap any two panes via the rail grip.
 *
 * Must NOT reparent `.split-column` nodes that still hold live chat iframes —
 * Chromium/Electron freezes on that DOM move (same class of bug as close/split
 * used to hit). Instead swap slot state + reload each frame in place so vertical
 * neighbors and cross-group cells can trade contents safely.
 */
function reorderSplitColumn(fromCol, targetCol, _before) {
    if (!fromCol || !targetCol || fromCol === targetCol) return false;
    if (!fromCol.classList?.contains('split-column') || !targetCol.classList?.contains('split-column')) {
        return false;
    }
    if (isSplitDiscardedEl(fromCol) || isSplitDiscardedEl(targetCol)) return false;

    const idxA = parseInt(fromCol.dataset.column, 10);
    const idxB = parseInt(targetCol.dataset.column, 10);
    if (!Number.isFinite(idxA) || !Number.isFinite(idxB) || idxA === idxB) return false;

    // Snapshot pages (with chat/terminal handles) before map keys move.
    const stateA = { ...getState(idxA) };
    const stateB = { ...getState(idxB) };
    const pageA = pageWithPaneSession(idxA, canonicalizeShellPage(stateA.page || '/chat_page.html'));
    const pageB = pageWithPaneSession(idxB, canonicalizeShellPage(stateB.page || '/chat_page.html'));

    const leafA = fromCol.dataset.leafId || newLeafId();
    const leafB = targetCol.dataset.leafId || newLeafId();
    fromCol.dataset.leafId = leafB;
    targetCol.dataset.leafId = leafA;

    swapMapKeys(columnState, idxA, idxB);
    swapMapKeys(lastChatByColumn, idxA, idxB);
    paneController.swapSessionColumns(idxA, idxB);
    paneController.swapRevealColumns(idxA, idxB);

    // Slots keep their indices; contents trade. Re-apply explicit pages after the map swap.
    setState(idxA, pageB);
    setState(idxB, pageA);
    getState(idxA).railCollapsed = !!stateB.railCollapsed;
    getState(idxB).railCollapsed = !!stateA.railCollapsed;
    applyRailCollapsed(idxA, !!stateB.railCollapsed);
    applyRailCollapsed(idxB, !!stateA.railCollapsed);

    updateColumnUI(idxA, pageB);
    updateColumnUI(idxB, pageA);

    const frameA = replaceFrame(idxA, pageB);
    if (frameA) attachFrameLoadListener(idxA, frameA);
    const frameB = replaceFrame(idxB, pageA);
    if (frameB) attachFrameLoadListener(idxB, frameB);

    paneController.swapColumns(idxA, idxB);

    persistLastChatHandles();
    persistSplitLayout();
    broadcastPaneFocus();
    return true;
}

function setupPaneReorder(colEl) {
    if (!colEl) return;
    const rail = colEl.querySelector('.icon-rail');
    if (!rail || rail.dataset.paneReorder === '1') return;
    rail.dataset.paneReorder = '1';
    const grip = ensureRailPaneGrip(colEl);
    if (!grip) return;

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let activated = false;
    let dropTarget = null;
    let dropBefore = true;
    // Cross-space carry (hover→switch, drop→move/swap) lives in the Spaces
    // owner (shell_spaces.js); the within-space pane reorder below stays here.
    const paneDrop = shellSpaces.paneDrop;

    const unbindWindow = () => {
        window.removeEventListener('pointermove', onMove, true);
        window.removeEventListener('pointerup', onUp, true);
        window.removeEventListener('pointercancel', onCancel, true);
        window.removeEventListener('blur', onBlurCancel, true);
    };

    const finish = (upEvent, cancelled) => {
        if (!dragging) return;
        const wasActive = activated;
        const target = dropTarget;
        const before = dropBefore;
        dragging = false;
        activated = false;
        unbindWindow();
        grip.classList.remove('is-dragging');
        colEl.classList.remove('is-pane-dragging');
        splitContainer?.classList.remove('is-reordering-panes');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        clearPaneDropIndicators();
        dropTarget = null;
        if (cancelled || !wasActive || !paneDrop.isActive()) {
            paneDrop.endDrag();
            return;
        }
        // A space-tab drop (or a live-pane swap while carrying) is consumed by
        // the Spaces owner; otherwise this is a within-space reorder.
        if (paneDrop.finishDrop(upEvent, target)) return;
        if (!target || target === colEl) return;
        setTimeout(() => reorderSplitColumn(colEl, target, before), 0);
    };

    const onMove = (e) => {
        if (!dragging) return;
        // A grip press whose release was missed must never start a button-less
        // drag on the next hover — cancel it instead of committing anything.
        if (e.buttons === 0) { finish(null, true); return; }
        paneDrop.trackHover(e);
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        if (!activated) {
            if ((dx * dx) + (dy * dy) < 36) return;
            activated = true;
            grip.classList.add('is-dragging');
            colEl.classList.add('is-pane-dragging');
            document.body.style.cursor = 'grabbing';
            document.body.style.userSelect = 'none';
        }

        // Hit-test every live leaf (horizontal, vertical, nested). Prefer the
        // smallest containing rect so a nested cell wins over a larger sibling.
        const cols = getSplitColumns().filter((c) => c !== colEl);
        clearPaneDropIndicators();
        dropTarget = null;

        let hit = null;
        let hitArea = Infinity;
        for (const other of cols) {
            const rect = other.getBoundingClientRect();
            if (
                e.clientX >= rect.left && e.clientX < rect.right
                && e.clientY >= rect.top && e.clientY < rect.bottom
            ) {
                const area = Math.max(1, rect.width) * Math.max(1, rect.height);
                if (area < hitArea) {
                    hit = other;
                    hitArea = area;
                }
            }
        }
        if (!hit || hit === colEl) return;

        const rect = hit.getBoundingClientRect();
        const parentOri = getGroupOrientation(getLeafParentGroup(hit));
        const vertical = parentOri === 'vertical';
        const useVertical = vertical || rect.height >= rect.width;
        dropBefore = useVertical
            ? (e.clientY < (rect.top + rect.height / 2))
            : (e.clientX < (rect.left + rect.width / 2));
        hit.classList.add(
            'pane-drop-target',
            dropBefore ? 'pane-drop-before' : 'pane-drop-after'
        );
        dropTarget = hit;
    };

    const onUp = (e) => finish(e, false);
    const onCancel = () => finish(null, true);
    const onBlurCancel = () => finish(null, true);

    grip.addEventListener('pointerdown', (e) => {
        if (e.button != null && e.button !== 0) return;
        if (!paneDrop.canStartDrag()) return;
        if (dragging) finish();
        e.preventDefault();
        e.stopPropagation();
        dragging = true;
        activated = false;
        startX = e.clientX;
        startY = e.clientY;
        dropTarget = null;
        dropBefore = true;
        paneDrop.beginDrag(snapshotLayoutTree(colEl), colEl, () => {
            dropTarget = null;
            clearPaneDropIndicators();
        });
        splitContainer?.classList.add('is-reordering-panes');
        window.addEventListener('pointermove', onMove, true);
        window.addEventListener('pointerup', onUp, true);
        window.addEventListener('pointercancel', onCancel, true);
        window.addEventListener('blur', onBlurCancel, true);
    });
}

// ── Per-group split resize ────────────────────────────────────
function setupGroupSplitResize(handleEl, groupEl, leftPos, rightPos) {
    if (!handleEl || handleEl.dataset.resizeBound === '1') return;
    handleEl.dataset.resizeBound = '1';
    let dragging = false;

    function stopDrag(keepHandle) {
        if (!dragging) return;
        dragging = false;
        handleEl.classList.remove('dragging');
        handleEl.classList.remove('will-collapse');
        splitContainer?.classList.remove('split-resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        groupEl?.querySelectorAll(':scope > .pane-will-collapse').forEach((c) => {
            c.classList.remove('pane-will-collapse');
        });
        finalizeGroupToRatios(groupEl);
        // Parking / restoring changes which dividers are expand affordances —
        // refresh them here or a drag-restored pane keeps the thick divider.
        if (!keepHandle) rebuildGroupResizeHandles(groupEl);
        persistSplitLayout();
    }

    /**
     * Collapse in place to a slim restore strip, keeping the live iframe.
     * Single-pane shells never collapse.
     */
    function maybeCollapseNarrowPane(cols, shrinkingPos, widths, vertical) {
        if (shrinkingPos < 0 || shrinkingPos >= cols.length) return false;
        if (!Array.isArray(widths) || widths[shrinkingPos] >= SPLIT_COLLAPSE_PX) return false;
        const target = cols[shrinkingPos];
        if (!target?.classList?.contains('split-column')) return false;
        if (isPaneCollapsedEl(target)) return false;
        if (getGroupChildNodes(groupEl).length < 2) return false;
        handleEl.classList.remove('dragging');
        handleEl.classList.remove('will-collapse');
        splitContainer?.classList.remove('split-resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        dragging = false;
        setPaneCollapsed(target, true, { restoreEdge: shrinkingPos <= leftPos ? 'trailing' : 'leading' });
        return true;
    }

    handleEl.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        const cols = getGroupChildNodes(groupEl);
        if (cols.length < 2) return;
        if (leftPos < 0 || rightPos < 0 || rightPos !== leftPos + 1) return;
        if (rightPos >= cols.length) return;

        e.preventDefault();
        handleEl.setPointerCapture(e.pointerId);
        dragging = true;

        const ori = getGroupOrientation(groupEl);
        const vertical = ori === 'vertical';
        const budget = getSplitGroupBudget(groupEl, cols.length);
        const minPx = splitMinPxFor(cols.length, budget, ori);
        // Measure BEFORE unparking: clearing the pin lets the pane spring
        // to ratio size, which would detach the gesture from the press point.
        const startWidths = cols.map(c => measureSplitMain(c, ori));
        const startCoord = vertical ? e.clientY : e.clientX;
        handleEl.classList.add('dragging');
        splitContainer?.classList.add('split-resizing');
        document.body.style.cursor = vertical ? 'row-resize' : 'col-resize';
        document.body.style.userSelect = 'none';

        // A divider touching parked panes never unparks them upfront:
        // drags keep them pinned (only pushing one past the threshold
        // reopens it mid-drag), and only a clean click restores one pane.
        // Unparking on press is what used to cascade — collapsing B
        // unparked A, resizing C unparked B.
        const unparkedMidDrag = [];

        let lastWidths = startWidths.slice();
        let lastShrinking = -1;
        let maxDelta = 0;

        // Only the marked restore divider reopens mid-drag; a plain
        // neighbor-side divider keeps parked panes pinned no matter what.
        const canExpand = handleEl.dataset.expand === '1';
        const onMove = (ev) => {
            if (!dragging) return;
            const coord = vertical ? ev.clientY : ev.clientX;
            const delta = coord - startCoord;
            if (Math.abs(delta) > maxDelta) maxDelta = Math.abs(delta);
            // Pushing a parked pane past the threshold reopens it mid-drag
            // (this is the only drag that unparks); anything less leaves it
            // pinned so neighbors resize around it.
            if (canExpand && handleEl.dataset.expandDir === (vertical ? 'down' : 'right') && delta > 0 && isPaneCollapsedEl(cols[leftPos])
                && startWidths[leftPos] + delta >= SPLIT_COLLAPSE_PX
                && !unparkedMidDrag.includes(cols[leftPos])) {
                setPaneCollapsed(cols[leftPos], false, { quiet: true });
                unparkedMidDrag.push(cols[leftPos]);
            } else if (canExpand && handleEl.dataset.expandDir === (vertical ? 'up' : 'left') && delta < 0 && isPaneCollapsedEl(cols[rightPos])
                && startWidths[rightPos] - delta >= SPLIT_COLLAPSE_PX
                && !unparkedMidDrag.includes(cols[rightPos])) {
                setPaneCollapsed(cols[rightPos], false, { quiet: true });
                unparkedMidDrag.push(cols[rightPos]);
            }
            const widths = startWidths.slice();
            let remaining = delta;
            // Pinned panes are transparent to space flow: freed space lands
            // on the nearest fluid pane on the growing side, so a shrink
            // against a parked neighbor still moves the divider.
            const giveTo = (from, dir) => {
                for (let i = from; i >= 0 && i < cols.length; i += dir) {
                    if (!isPaneCollapsedEl(cols[i])) return i;
                }
                return -1;
            };
            if (remaining > 0) {
                for (let i = rightPos; i < widths.length && remaining > 0; i++) {
                    if (isPaneCollapsedEl(cols[i])) continue;
                    const can = widths[i] - minPx;
                    if (can <= 0) continue;
                    const take = Math.min(can, remaining);
                    widths[i] -= take;
                    remaining -= take;
                }
                const giveLeft = giveTo(leftPos, -1);
                if (giveLeft >= 0) widths[giveLeft] += delta - remaining;
            } else if (remaining < 0) {
                let need = -remaining;
                for (let i = leftPos; i >= 0 && need > 0; i--) {
                    if (isPaneCollapsedEl(cols[i])) continue;
                    const can = widths[i] - minPx;
                    if (can <= 0) continue;
                    const take = Math.min(can, need);
                    widths[i] -= take;
                    need -= take;
                }
                const giveRight = giveTo(rightPos, 1);
                if (giveRight >= 0) widths[giveRight] += (-remaining) - need;
            }
            applySplitPixelFlex(cols, widths, ori, groupEl);
            // Threshold preview: flag any pane sitting under SPLIT_COLLAPSE_PX —
            // the shrinking pane (release parks it) and a restored pane still
            // in the zone (release settles it at the threshold) — so the gray
            // shows while the outcome is still live. Axis-agnostic: vertical
            // stacks report heights through the same widths.
            lastWidths = widths;
            lastShrinking = delta > 0 ? rightPos : (delta < 0 ? leftPos : -1);
            const previewIdx = new Set();
            // Never preview an already-parked pane: its pin shields it from
            // parking again, so dimming its blade is pure noise.
            if (lastShrinking >= 0
                && !isPaneCollapsedEl(cols[lastShrinking])
                && widths[lastShrinking] < SPLIT_COLLAPSE_PX
                && cols[lastShrinking]?.classList?.contains('split-column')) {
                previewIdx.add(lastShrinking);
            }
            unparkedMidDrag.forEach((c) => {
                const i = cols.indexOf(c);
                if (i >= 0 && widths[i] < SPLIT_COLLAPSE_PX) previewIdx.add(i);
            });
            cols.forEach((c, i) => {
                c.classList.toggle('pane-will-collapse', previewIdx.has(i));
            });
            handleEl.classList.toggle('will-collapse', previewIdx.size > 0);
        };
        const endDragListeners = () => {
            try { handleEl.releasePointerCapture(e.pointerId); } catch (_) {}
            handleEl.removeEventListener('pointermove', onMove);
            handleEl.removeEventListener('pointerup', onUp);
            handleEl.removeEventListener('pointercancel', onCancel);
        };
        // Panes reopened mid-drag that land under the threshold settle AT
        // it (never instantly re-park); still-pinned panes keep their pin.
        const floorRestoredPanes = () => {
            let floored = false;
            unparkedMidDrag.forEach((c) => {
                const i = cols.indexOf(c);
                if (i >= 0 && lastWidths[i] < SPLIT_COLLAPSE_PX) {
                    lastWidths[i] = SPLIT_COLLAPSE_PX;
                    floored = true;
                }
            });
            if (floored) applySplitPixelFlex(cols, lastWidths, ori, groupEl);
            return floored;
        };
        const onUp = () => {
            endDragListeners();
            if (!canExpand && maxDelta < 2) { stopDrag(true); return; } // Keep the target for dblclick.
            if (canExpand && maxDelta < 2) {
                // Restore only the pane identified by this divider's arrow.
                const target = (handleEl.dataset.expandDir === (vertical ? 'down' : 'right') ? [cols[leftPos]] : [cols[rightPos]]).find(
                    (c) => c && c.classList?.contains('split-column')
                        && isPaneCollapsedEl(c));
                if (target) {
                    setPaneCollapsed(target, false, { quiet: true });
                    const kids = getGroupChildNodes(groupEl);
                    applySplitRatioFlex(kids, kids.map(() => 1), getGroupOrientation(groupEl), groupEl);
                    rebuildGroupResizeHandles(groupEl);
                    stopDrag();
                    return;
                }
            }
            if (floorRestoredPanes()) {
                stopDrag();
                return;
            }
            if (maybeCollapseNarrowPane(cols, lastShrinking, lastWidths, vertical)) return;
            stopDrag();
        };
        // An interrupted gesture keeps current sizes, floored the same way —
        // it never parks, never restores.
        const onCancel = () => {
            endDragListeners();
            floorRestoredPanes();
            stopDrag();
        };
        handleEl.addEventListener('pointermove', onMove);
        handleEl.addEventListener('pointerup', onUp);
        handleEl.addEventListener('pointercancel', onCancel);
    });
    handleEl.title ||= 'Drag to resize · Double-click to equalize adjacent panes';
    handleEl.addEventListener('dblclick', (e) => {
        if (e.button !== 0) return;
        const cols = getGroupChildNodes(groupEl);
        if (!cols[leftPos] || !cols[rightPos] || isPaneCollapsedEl(cols[leftPos]) || isPaneCollapsedEl(cols[rightPos])) return;
        e.preventDefault();
        const ori = getGroupOrientation(groupEl);
        // The equalize decision lives in the pane owner; the page measures + applies.
        const sizes = CuttleShellPanes.equalizeAdjacentSizes(
            cols.map(c => measureSplitMain(c, ori)), leftPos, rightPos);
        applySplitRatioFlex(cols, sizes, ori, groupEl);
        persistSplitLayout();
    });
    // Intentionally no document/window listeners — rebuilds used to leak them and freeze Chrome.
}

// Legacy sessions may still have fixed-px flex in memory — convert on resize so
// maximize/windowed transitions don't crop panes before the next drag.
// Ratio flex already scales with the container (min-width: 0); no re-finalize.
window.addEventListener('resize', () => {
    if (!splitContainer) return;
    let changed = false;
    const groups = [splitContainer, ...splitContainer.querySelectorAll('.split-group')];
    groups.forEach((g) => {
        if (migrateFixedPxSplitFlex(getGroupChildNodes(g), g)) changed = true;
    });
    if (changed) persistSplitLayout();
});

// ── Theme sync (rail & panel) ─────────────────────────────────
function applyShellTheme(theme) {
    if (window.CuttleUiBoot) {
        if (window.CuttleUiBoot.currentTheme() !== window.CuttleUiBoot.normalizeTheme(theme)) {
            window.CuttleUiBoot.applyTheme(theme);
        }
        applyUiAnimationPrefs();
        return;
    }
    const want = theme === 'light' ? 'light-mode'
        : theme === 'midnight' ? 'midnight-mode'
        : 'dark-mode';
    [document.documentElement, document.body].forEach((el) => {
        if (!el) return;
        const others = ['dark-mode', 'midnight-mode', 'light-mode'].filter((c) => c !== want);
        if (el.classList.contains(want) && !others.some((c) => el.classList.contains(c))) return;
        el.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
        el.classList.add(want);
    });
    applyUiAnimationPrefs();
}

function initializeShellTheme() {
    const saved = window.CuttleUiBoot.currentTheme();
    applyShellTheme(saved);
}

initializeShellTheme();

// ── postMessage: navigate focused/first column; electron zoom; theme ─
window.addEventListener('message', (e) => {
    if (e.data?.type === 'cuttle-page-ready') {
        finishFrameTransitionFor(e.source);
        // Freshly revealed frames (space switches included) rescan live
        // usage immediately instead of waiting out a dead refresh timer.
        broadcastUsageLiveWake();
        return;
    }
    if (e.data?.type === 'cuttle-navigate' && e.data?.page) {
        navigateFromSource(e.source, e.data.page);
    }
    CuttleShellElectron.handleZoomMessage(e.data);
    if (e.data?.type === 'cuttle-theme-change' && e.data?.theme) {
        applyShellTheme(e.data.theme);
        // Keep the other blades in sync; the sender already applied it.
        broadcastToFrames({ type: 'cuttle-theme-change', theme: e.data.theme }, e.source);
    }
    if (e.data?.type === 'cuttle-ui-anim-change') {
        applyUiAnimationPrefs();
        broadcastToFrames({ type: 'cuttle-ui-anim-change' });
    }
});


// ── Shell heartbeat (jobs badge + toasts + workers + live-status) ─
// One cadence instead of separate 2s/3s/8s/15s timers. Slows down when the
// window is hidden so laptop Clients stop waking Wi‑Fi for invisible UI.
const SHELL_HEARTBEAT_VISIBLE_MS = 6000;
const SHELL_HEARTBEAT_HIDDEN_MS = 45000;
let shellHeartbeatTimer = null;
let shellHeartbeatInFlight = false;

async function pollJobsBadge() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const rJobs = await fetch('/api/jobs/in-flight', { cache: 'no-store', signal: controller.signal });
        if (rJobs.ok) {
            const dJobs = await rJobs.json();
            const executing = dJobs.executing_jobs || [];
            document.querySelectorAll('.rail-item[data-page="/jobs_page.html"] .rail-badge').forEach(badge => {
                const count = executing ? executing.length : 0;
                badge.textContent = String(count);
                badge.style.display = count > 0 ? '' : 'none';
            });
        }
    } catch (_) {}
    clearTimeout(timeout);
}

/** Drain system toasts into Electron toast + chirp. */
async function pollUiToasts() {
    try {
        const r = await fetch('/api/ui-toasts', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        const toasts = data.toasts || [];
        for (const t of toasts) {
            const msg = (t && t.message) ? String(t.message) : '';
            if (!msg) continue;
            const variant = (t && t.variant) ? String(t.variant) : 'info';
            if (typeof window.showToast === 'function') {
                window.showToast(msg, variant);
            }
            if ((variant === 'success' || variant === 'error')
                && typeof window.playCuttleCompletionChirp === 'function') {
                window.playCuttleCompletionChirp();
            }
        }
    } catch (_) {}
}

function shellHeartbeatDelayMs() {
    return document.hidden ? SHELL_HEARTBEAT_HIDDEN_MS : SHELL_HEARTBEAT_VISIBLE_MS;
}

function applyShellBackgroundPaused(hidden) {
    const on = !!hidden;
    document.documentElement.classList.toggle('cuttle-bg-paused', on);
    if (document.body) document.body.classList.toggle('cuttle-bg-paused', on);
    try {
        broadcastToFrames({ type: 'cuttle-shell-visibility', hidden: on });
    } catch (_) {}
}

async function runShellHeartbeat() {
    if (shellHeartbeatInFlight) return;
    shellHeartbeatInFlight = true;
    try {
        const tasks = [pollUiToasts(), pollJobsBadge()];
        if (typeof window.__cuttleShellPollWorkers === 'function') {
            tasks.push(Promise.resolve(window.__cuttleShellPollWorkers()));
        }
        if (paneController?.hasSessions()) {
            tasks.push(paneController?.pollLiveStatusHub());
        }
        tasks.push(shellSpaces.refreshFromServer());
        await Promise.all(tasks);
    } catch (_) {
    } finally {
        shellHeartbeatInFlight = false;
    }
}

function scheduleShellHeartbeat(immediate) {
    if (shellHeartbeatTimer) {
        clearTimeout(shellHeartbeatTimer);
        shellHeartbeatTimer = null;
    }
    const delay = immediate ? 0 : shellHeartbeatDelayMs();
    shellHeartbeatTimer = setTimeout(async () => {
        shellHeartbeatTimer = null;
        try {
            await runShellHeartbeat();
        } finally {
            scheduleShellHeartbeat(false);
        }
    }, delay);
}

applyShellBackgroundPaused(document.hidden);
document.addEventListener('visibilitychange', () => {
    applyShellBackgroundPaused(document.hidden);
    // Immediate beat on focus so badges/toasts catch up after a long hide.
    scheduleShellHeartbeat(true);
    paneController?.schedulePendingChangesHub(true);
});
scheduleShellHeartbeat(true);

function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Initial UI
updateColumnUI(0, currentPage);

// ── Nav debug ─────────────────────────────────────────────────
// Enable via localStorage: localStorage.setItem('cuttle_nav_debug', '1')
(function () {
    if (!localStorage.getItem('cuttle_nav_debug')) return;
    function ts() { return new Date().toISOString().slice(11, 23); }
    function log(msg) {
        fetch('/api/debug-log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ msg: '[' + ts() + '] ' + msg })
        }).catch(() => {});
    }
    window._dbg = log;

    // Patch navigate to log every call
    const _navigate = navigate;
    window.navigate = function (colIdx, page) {
        const st = getState(colIdx);
        if (st.page === page) {
            log('NAV-SKIP col=' + colIdx + ' page="' + page + '" (state already="' + st.page + '")');
            return;
        }
        log('NAV col=' + colIdx + ' from="' + st.page + '" to="' + page + '"');
        _navigate(colIdx, page);
        const f = getLiveFrame(colIdx);
        log('NAV-AFTER col=' + colIdx + ' newFrame=' + (f ? f.src : 'null'));
    };

    // Reassign rail listeners to use patched navigate (they close over original)
    document.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        const listeners = btn.cloneNode(true);
        btn.parentNode.replaceChild(listeners, btn);
    });
    const col = getColumnEl(0);
    if (col) {
        col.querySelectorAll('.rail-item[data-page]').forEach(btn => {
            btn.addEventListener('click', () => window.navigate(0, btn.dataset.page));
        });
    }

    // Poll iframe URL every 500ms, log changes
    let lastHref = '', lastState = '';
    setInterval(() => {
        const f = getLiveFrame(0);
        let href = '(null)';
        try { href = f?.contentWindow?.location?.href || '(blank)'; } catch (e) { href = '(err)'; }
        const s = getState(0)?.page || '(none)';
        if (href !== lastHref || s !== lastState) {
            log('POLL state="' + s + '" iframe="' + href + '"');
            lastHref = href; lastState = s;
        }
    }, 500);

    log('--- debug started --- initPage="' + currentPage + '"');
})();
