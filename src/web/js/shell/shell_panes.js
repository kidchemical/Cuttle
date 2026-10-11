/* Pane-lifetime cross-frame hubs. All page/layout access is supplied by host. */
(function (root) {
    'use strict';

    function create(host) {
        const {
            win: window, doc: document, fetch, setTimeout, clearTimeout,
            URL, URLSearchParams, AbortController, getSplitColumns, getLiveFrame,
            findColumnIndexForSource, columnState, chatSurfaceKind, broadcastToFrames,
        } = host;

        // Chromium shares ~6 HTTP/1.1 connections per origin across all chat iframes.
        let focusedColumnIdx = 0;
        const paneSessionByColumn = new Map();
        let liveStatusHubArmed = false;
        let liveStatusHubInFlight = false;
        const paneProjectByColumn = new Map();
        let pendingChangesHubTimer = null;
        const pendingChangesPathInFlight = new Map();
        const PENDING_CHANGES_VISIBLE_MS = 12000;
        const PENDING_CHANGES_HIDDEN_MS = 45000;

        function normalizeShellProjectPath(p) {
            return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
        }

        function setFocusedColumn(idx) {
            if (!Number.isFinite(idx) || idx < 0 || focusedColumnIdx === idx) return;
            focusedColumnIdx = idx;
            broadcastPaneFocus();
        }

        function broadcastUsageLiveWake() {
            document.querySelectorAll('.split-column .shell-main iframe').forEach((frame) => {
                if (!host.isChatPageFrame(frame) || !frame.contentWindow) return;
                try {
                    const live = frame.contentWindow.CuttleUsageLive;
                    if (live && typeof live.wake === 'function') { live.wake(); return; }
                } catch (_) {}
                try { frame.contentWindow.postMessage({ type: 'cuttle-usage-live-wake' }, '*'); } catch (_) {}
            });
        }

        function broadcastPaneFocus() {
            document.querySelectorAll('.split-column').forEach((col) => {
                const idx = parseInt(col.dataset.column, 10);
                const frame = getLiveFrame(idx);
                if (!frame || !frame.contentWindow) return;
                try { frame.contentWindow.postMessage({ type: 'cuttle-pane-focus', focused: idx === focusedColumnIdx, column: idx }, '*'); } catch (_) {}
            });
        }

        function registerPaneChatSession(source, sessionId) {
            const colIdx = findColumnIndexForSource(source);
            if (sessionId == null || sessionId === '') paneSessionByColumn.delete(colIdx);
            else paneSessionByColumn.set(colIdx, String(sessionId));
            ensureLiveStatusHub();
            scheduleBroadcastOpenPanes();
        }

        function getOpenPaneSessions() {
            const cols = getSplitColumns();
            const out = [];
            cols.forEach((col, visualIdx) => {
                const idx = parseInt(col.dataset.column, 10);
                const state = columnState.get(idx);
                const page = (state && state.page) || '';
                let kind = null, sid = null;
                try {
                    const url = new URL(page || '/chat_page.html', window.location.origin);
                    kind = chatSurfaceKind(url.pathname);
                    sid = url.searchParams.get('chat') || url.searchParams.get('session');
                } catch (_) {}
                const live = paneSessionByColumn.get(idx);
                if (kind === 'chat' && live) sid = live;
                if (!kind || !sid) return;
                out.push({ sessionId: String(sid), pane: visualIdx + 1, column: idx, kind, focused: idx === focusedColumnIdx });
            });
            return out;
        }

        function broadcastOpenPanes() { broadcastToFrames({ type: 'cuttle-open-panes', panes: getOpenPaneSessions() }); }
        let openPanesBroadcastTimer = null;
        function scheduleBroadcastOpenPanes() {
            if (openPanesBroadcastTimer) return;
            openPanesBroadcastTimer = setTimeout(() => {
                openPanesBroadcastTimer = null;
                try { broadcastOpenPanes(); } catch (_) {}
            }, 40);
        }

        function ensureLiveStatusHub() {
            if (liveStatusHubArmed) return;
            liveStatusHubArmed = true;
            pollLiveStatusHub();
        }

        function registerPaneProject(source, projectPath) {
            const colIdx = findColumnIndexForSource(source);
            if (!Number.isFinite(colIdx) || colIdx < 0) return;
            const path = projectPath != null && String(projectPath).trim() ? String(projectPath).trim() : '';
            if (!path) paneProjectByColumn.delete(colIdx);
            else {
                paneProjectByColumn.set(colIdx, path);
                ensurePendingChangesHub();
                refreshPendingChangesPath(path);
            }
        }

        function uniquePaneProjectPaths() {
            return [...new Set([...paneProjectByColumn.values()].filter((p) => p && String(p).trim()))];
        }

        async function refreshPendingChangesPath(projectPath) {
            const path = String(projectPath || '').trim();
            if (!path) return;
            const key = normalizeShellProjectPath(path);
            if (pendingChangesPathInFlight.has(key)) return pendingChangesPathInFlight.get(key);
            const work = (async () => {
                try {
                    const qs = new URLSearchParams(); qs.set('path', path);
                    const controller = new AbortController();
                    const timeout = setTimeout(() => controller.abort(), 20000);
                    let data = null;
                    try {
                        const r = await fetch('/api/git/pending-changes?' + qs.toString(), { credentials: 'include', cache: 'no-store', signal: controller.signal });
                        data = await r.json().catch(() => null);
                    } finally { clearTimeout(timeout); }
                    if (data) broadcastToFrames({ type: 'cuttle-pending-changes-data', path, data });
                } catch (_) {} finally { pendingChangesPathInFlight.delete(key); }
            })();
            pendingChangesPathInFlight.set(key, work);
            return work;
        }

        async function pollPendingChangesHub() {
            const paths = uniquePaneProjectPaths();
            if (paths.length) await Promise.all(paths.map((p) => refreshPendingChangesPath(p)));
        }
        function pendingChangesHubDelayMs() { return document.hidden ? PENDING_CHANGES_HIDDEN_MS : PENDING_CHANGES_VISIBLE_MS; }
        function schedulePendingChangesHub(immediate) {
            if (pendingChangesHubTimer) { clearTimeout(pendingChangesHubTimer); pendingChangesHubTimer = null; }
            if (!uniquePaneProjectPaths().length) return;
            pendingChangesHubTimer = setTimeout(async () => {
                pendingChangesHubTimer = null;
                try { await pollPendingChangesHub(); } finally { schedulePendingChangesHub(false); }
            }, immediate ? 0 : pendingChangesHubDelayMs());
        }
        function ensurePendingChangesHub() { if (!pendingChangesHubTimer) schedulePendingChangesHub(true); }

        async function pollLiveStatusHub() {
            if (liveStatusHubInFlight) return;
            const ids = [...new Set([...paneSessionByColumn.values()].filter(Boolean))];
            if (!ids.length) return;
            liveStatusHubInFlight = true;
            try {
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 5000);
                let data = null;
                try {
                    const r = await fetch('/api/chat-live-status-batch?session_ids=' + encodeURIComponent(ids.join(',')), { credentials: 'include', cache: 'no-store', signal: controller.signal });
                    if (r.ok) data = await r.json();
                } finally { clearTimeout(timeout); }
                if (!data || !data.success || !data.statuses) return;
                paneSessionByColumn.forEach((sid, colIdx) => {
                    const frame = getLiveFrame(colIdx);
                    if (!frame || !frame.contentWindow) return;
                    try { frame.contentWindow.postMessage({ type: 'cuttle-live-status', sessionId: sid, status: data.statuses[String(sid)] || null, focused: colIdx === focusedColumnIdx }, '*'); } catch (_) {}
                });
            } catch (_) {} finally { liveStatusHubInFlight = false; }
        }

        function clearPaneColumn(idx) { paneSessionByColumn.delete(idx); paneProjectByColumn.delete(idx); }
        function sessionForColumn(idx) { return paneSessionByColumn.get(idx); }
        function sessionForColumnExists(idx) { return paneSessionByColumn.has(idx); }
        function swapSessionColumns(a, b) {
            const av = paneSessionByColumn.get(a), bv = paneSessionByColumn.get(b);
            const ah = paneSessionByColumn.has(a), bh = paneSessionByColumn.has(b);
            paneSessionByColumn.delete(a); paneSessionByColumn.delete(b);
            if (bh) paneSessionByColumn.set(a, bv); if (ah) paneSessionByColumn.set(b, av);
        }
        function remapColumns(map) {
            const nextSessions = new Map(), nextProjects = new Map();
            paneSessionByColumn.forEach((v, k) => { if (map.has(k)) nextSessions.set(map.get(k), v); });
            paneProjectByColumn.forEach((v, k) => { if (map.has(k)) nextProjects.set(map.get(k), v); });
            paneSessionByColumn.clear(); nextSessions.forEach((v, k) => paneSessionByColumn.set(k, v));
            paneProjectByColumn.clear(); nextProjects.forEach((v, k) => paneProjectByColumn.set(k, v));
            if (map.has(focusedColumnIdx)) focusedColumnIdx = map.get(focusedColumnIdx);
            else focusedColumnIdx = 0;
        }
        function swapColumns(a, b) {
            for (const map of [paneSessionByColumn, paneProjectByColumn]) {
                const av = map.get(a), bv = map.get(b), ah = map.has(a), bh = map.has(b);
                map.delete(a); map.delete(b);
                if (bh) map.set(a, bv); if (ah) map.set(b, av);
            }
            if (focusedColumnIdx === a) focusedColumnIdx = b;
            else if (focusedColumnIdx === b) focusedColumnIdx = a;
        }
        function hasSessions() { return paneSessionByColumn.size > 0; }
        function setFocusedColumnDirect(idx) { focusedColumnIdx = idx; }
        document.addEventListener('pointerdown', (e) => {
            const col = e.target && e.target.closest ? e.target.closest('.split-column') : null;
            if (!col) return;
            const idx = parseInt(col.dataset.column, 10);
            if (Number.isFinite(idx)) setFocusedColumn(idx);
        }, true);
        window.getOpenPaneSessions = getOpenPaneSessions;
        return { setFocusedColumn, focusedColumnIndex: () => focusedColumnIdx, broadcastUsageLiveWake,
            registerPaneChatSession, getOpenPaneSessions, registerPaneProject, refreshPendingChangesPath,
            schedulePendingChangesHub, pollPendingChangesHub, pollLiveStatusHub, clearPaneColumn,
            scheduleBroadcastOpenPanes, remapColumns, swapColumns, hasSessions, broadcastFocus: broadcastPaneFocus,
            setFocusedColumnDirect, sessionForColumn, sessionForColumnExists, swapSessionColumns };
    }

    root.CuttleShellPanes = { create };
})(window);
