/* Pane-lifetime cross-frame hubs. All page/layout access is supplied by host. */
(function (root) {
    'use strict';

    function create(host) {
        const {
            win: window, doc: document, fetch, setTimeout, clearTimeout,
            URL, URLSearchParams, AbortController, getSplitColumns, getLiveFrame,
            findColumnIndexForSource, columnState, chatSurfaceKind, broadcastToFrames,
            getColumnEl, getSplitColumnCount, pauseAllShellPaneStreams,
            pauseShellFrameStreams, stampComposerDraftScope,
            withCacheBust, attachFrameLoadListener,
            isChatPageFrame,
            withRememberedChat, rememberChatHandleFromPage, getState,
            setState, storagePageKey, updateColumnUI, persistSplitLayout,
            sessionStorage, desktopCfg,
        } = host;

        const SPINNER_DELAY_MS = 90;
        const TRANSITION_FAILSAFE_MS = 2500;
        const DEFAULT_TRANSITION_MS = 220;
        const ANIM_KEY = 'cuttleUiAnimations';
        const ANIM_MS_KEY = 'cuttleUiTransitionMs';
        const pendingFrameReveals = new Map();
        const frameRevealTokens = new Map();
        const frameLoadListeners = new Map();
        const frameTitleObservers = new Map();
        const shellBackStack = [];
        let applyingShellHistory = false;

        function uiAnimationsEnabled() {
            try { if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false; } catch (_) {}
            try { return host.localStorage.getItem(ANIM_KEY) !== '0'; } catch (_) { return true; }
        }
        function uiTransitionMs() {
            try {
                const n = parseInt(host.localStorage.getItem(ANIM_MS_KEY), 10);
                return Number.isNaN(n) ? DEFAULT_TRANSITION_MS : Math.max(0, Math.min(1000, n));
            } catch (_) { return DEFAULT_TRANSITION_MS; }
        }
        function applyUiAnimationPrefs() {
            document.documentElement.style.setProperty('--cuttle-transition-ms', uiTransitionMs() + 'ms');
            document.documentElement.classList.toggle('cuttle-anim', uiAnimationsEnabled());
        }
        function ensurePageLoader(mainEl) {
            let loader = mainEl.querySelector('.shell-page-loader');
            if (!loader) {
                loader = document.createElement('div');
                loader.className = 'shell-page-loader';
                loader.setAttribute('aria-hidden', 'true');
                loader.innerHTML = '<div class="shell-page-loader-veil"></div><div class="shell-page-spinner" aria-hidden="true"></div>';
                mainEl.appendChild(loader);
            }
            return loader;
        }
        function setPageLoaderVisible(mainEl, visible, opts) {
            const loader = ensurePageLoader(mainEl);
            const spinning = !!(opts && opts.spinning);
            loader.classList.toggle('visible', !!visible);
            loader.classList.toggle('spinning', !!visible && spinning);
            if (visible) { loader.setAttribute('aria-busy', 'true'); loader.removeAttribute('aria-hidden'); }
            else { loader.removeAttribute('aria-busy'); loader.setAttribute('aria-hidden', 'true'); }
        }
        function cancelOwnedReveal(frame) {
            const entry = pendingFrameReveals.get(frame);
            if (!entry) return;
            if (entry.timer) clearTimeout(entry.timer);
            if (entry.spinnerTimer) clearTimeout(entry.spinnerTimer);
            pendingFrameReveals.delete(frame);
        }
        function revealFrame(frame) {
            const entry = pendingFrameReveals.get(frame);
            if (!entry) return;
            const col = frame.closest?.('.split-column');
            const liveIdx = col && !host.isSplitDiscardedEl(col) ? parseInt(col.dataset.column, 10) : NaN;
            const idx = Number.isFinite(liveIdx) ? liveIdx : entry.colIdx;
            if (entry.token !== frameRevealTokens.get(idx)) { cancelOwnedReveal(frame); return; }
            entry.colIdx = idx;
            cancelOwnedReveal(frame);
            const { mainEl, oldFrames } = entry;
            const ms = uiAnimationsEnabled() ? uiTransitionMs() : 0;
            frame.classList.remove('shell-frame-pending');
            frame.style.transition = ms > 0 ? `opacity ${ms}ms ease` : 'none';
            window.requestAnimationFrame(() => { frame.style.opacity = '1'; frame.style.pointerEvents = ''; });
            setPageLoaderVisible(mainEl, false);
            (oldFrames || []).forEach((old) => {
                if (!old || old === frame || !old.parentNode) return;
                old.classList.add('shell-frame-outgoing'); old.style.pointerEvents = 'none';
                const removeOld = () => { try { old.remove(); } catch (_) {} };
                if (ms > 0) {
                    old.style.transition = `opacity ${ms}ms ease`;
                    window.requestAnimationFrame(() => { old.style.opacity = '0'; });
                    setTimeout(removeOld, ms + 40);
                } else removeOld();
            });
        }
        function armOwnedReveal(colIdx, frame, mainEl, oldFrames) {
            applyUiAnimationPrefs();
            const token = (frameRevealTokens.get(colIdx) || 0) + 1;
            frameRevealTokens.set(colIdx, token);
            (oldFrames || []).forEach(cancelOwnedReveal);
            frame.classList.add('shell-frame-pending');
            frame.style.opacity = '0'; frame.style.pointerEvents = 'none';
            frame.style.transition = 'none'; frame.style.background = 'transparent';
            setPageLoaderVisible(mainEl, true, { spinning: false });
            const spinnerTimer = setTimeout(() => setPageLoaderVisible(mainEl, true, { spinning: true }), SPINNER_DELAY_MS);
            const failsafe = host.isCuttleMobileShell() ? 7000 : TRANSITION_FAILSAFE_MS;
            const timer = setTimeout(() => revealFrame(frame), failsafe);
            pendingFrameReveals.set(frame, { colIdx, mainEl, oldFrames: oldFrames || [], token, timer, spinnerTimer });
        }
        function finishFrameTransitionFor(sourceWindow) {
            if (!sourceWindow) return;
            document.querySelectorAll('.shell-main iframe').forEach((frame) => {
                try { if (frame.contentWindow === sourceWindow) revealFrame(frame); } catch (_) {}
            });
        }
        function clearRevealColumn(idx) { frameRevealTokens.delete(idx); }
        function attachOwnedFrameLoadListener(frame, listener) {
            if (!frame || frame.dataset.shellLoadBound === '1') return;
            const controller = new AbortController();
            frame.dataset.shellLoadBound = '1';
            frameLoadListeners.set(frame, controller);
            frame.addEventListener('load', function () {
                listener.apply(this, arguments);
                frameTitleObservers.get(frame)?.disconnect();
                frameTitleObservers.delete(frame);
                try {
                    const title = frame.contentDocument?.getElementById('chatSessionTitleText');
                    if (!title || !window.MutationObserver) return;
                    const observer = new window.MutationObserver(() => {
                        const col = frame.closest('.split-column');
                        const idx = parseInt(col?.dataset.column, 10);
                        if (col && getLiveFrame(idx) === frame) host.refreshParkedPaneTitle?.(col);
                    });
                    observer.observe(title, { childList: true, characterData: true, subtree: true });
                    frameTitleObservers.set(frame, observer);
                } catch (_) { /* Cross-origin pages have no chat-title observer. */ }
            }, { signal: controller.signal });
        }
        function cleanupFrame(frame) {
            const controller = frameLoadListeners.get(frame);
            if (controller) controller.abort();
            frameLoadListeners.delete(frame);
            frameTitleObservers.get(frame)?.disconnect();
            frameTitleObservers.delete(frame);
            cancelOwnedReveal(frame);
        }
        function remapRevealColumns(map) {
            const next = new Map();
            frameRevealTokens.forEach((value, key) => { if (map.has(key)) next.set(map.get(key), value); });
            frameRevealTokens.clear(); next.forEach((value, key) => frameRevealTokens.set(key, value));
            pendingFrameReveals.forEach((entry) => {
                if (entry && map.has(entry.colIdx)) entry.colIdx = map.get(entry.colIdx);
            });
        }
        function swapRevealColumns(a, b) {
            const av = frameRevealTokens.get(a), bv = frameRevealTokens.get(b);
            const ah = frameRevealTokens.has(a), bh = frameRevealTokens.has(b);
            frameRevealTokens.delete(a); frameRevealTokens.delete(b);
            if (bh) frameRevealTokens.set(a, bv); if (ah) frameRevealTokens.set(b, av);
        }
        function navigate(colIdx, page, opts) {
            page = withRememberedChat(colIdx, page);
            try {
                const u = new URL(page, window.location.origin);
                if (u.pathname === '/jobs_page.html') {
                    if (!u.searchParams.has('_v')) u.searchParams.set('_v', String(Date.now()));
                    try {
                        const wid = String((desktopCfg && desktopCfg.workerId) || sessionStorage.getItem('cuttle_viewer_worker_id') || '').trim().toLowerCase();
                        const clientMode = !!(desktopCfg && desktopCfg.clientMode) || sessionStorage.getItem('cuttle_client_mode') === '1';
                        if (wid && !u.searchParams.has('me')) u.searchParams.set('me', wid);
                        if (clientMode && !u.searchParams.has('client')) u.searchParams.set('client', '1');
                    } catch (_) {}
                    page = u.pathname + u.search + u.hash;
                }
            } catch (_) {}
            rememberChatHandleFromPage(colIdx, page);
            const state = getState(colIdx);
            if (state.page === page && !(opts && opts.force)) return;
            console.log('[App Shell] Navigating to:', page);
            if (colIdx === 0 && !applyingShellHistory && !(opts && opts.history === 'none')) pushShellBackEntry(state.page);
            setState(colIdx, page);
            clearPaneColumn(colIdx);
            if (colIdx === 0) {
                sessionStorage.setItem(storagePageKey, page);
                try {
                    const pageUrl = new URL(page, window.location.origin);
                    const isChat = pageUrl.pathname === '/chat_page.html' || pageUrl.pathname === '/terminal_page.html';
                    const cid = isChat ? (pageUrl.searchParams.get('chat') || pageUrl.searchParams.get('session')) : null;
                    const histMode = (opts && opts.history) || (applyingShellHistory ? 'none' : 'replace');
                    syncShellChatUrl(cid, { mode: histMode, page });
                } catch (_) {}
            }
            updateColumnUI(colIdx, page);
            persistSplitLayout();
            const frame = opts && opts.force ? replaceFrameInPlace(colIdx, page) : replaceFrame(colIdx, page);
            if (frame) attachFrameLoadListener(colIdx, frame);
        }

        function shellPageFromChatId(chatId) {
            return chatId ? '/chat_page.html?chat=' + encodeURIComponent(String(chatId)) : '/chat_page.html?new=1';
        }
        function shellPagesEqual(a, b) {
            try {
                const ua = new URL(a || '/chat_page.html', window.location.origin), ub = new URL(b || '/chat_page.html', window.location.origin);
                const norm = (u) => {
                    u.searchParams.delete('_v'); u.searchParams.delete('_cb');
                    return u.pathname + '|c=' + (u.searchParams.get('chat') || u.searchParams.get('session') || '') + '|n=' + (u.searchParams.has('new') ? '1' : '0');
                };
                return norm(ua) === norm(ub);
            } catch (_) { return String(a || '') === String(b || ''); }
        }
        function isShellNewChatPage(page) {
            try {
                const u = new URL(page || '/chat_page.html', window.location.origin);
                return u.pathname === '/chat_page.html' && !u.searchParams.get('chat') && !u.searchParams.get('session');
            } catch (_) { return false; }
        }
        function pushShellBackEntry(page) {
            if (applyingShellHistory || !page) return;
            if (isShellNewChatPage(page) && shellBackStack.length === 0) return;
            const top = shellBackStack.length ? shellBackStack[shellBackStack.length - 1] : null;
            if (top && shellPagesEqual(top, page)) return;
            shellBackStack.push(page);
            while (shellBackStack.length > 40) shellBackStack.shift();
        }
        function syncShellChatUrl(chatId, opts) {
            const mode = (opts && opts.mode) || 'replace';
            let page = opts && opts.page;
            if (!page) {
                try {
                    const state = getState(0);
                    page = (state && state.page) || shellPageFromChatId(chatId);
                    if (chatId) {
                        const u = new URL(page, window.location.origin);
                        if (u.pathname === '/chat_page.html' || u.pathname === '/terminal_page.html') {
                            u.searchParams.set('chat', String(chatId)); u.searchParams.delete('session'); u.searchParams.delete('new');
                            page = u.pathname + u.search + u.hash;
                        }
                    }
                } catch (_) { page = shellPageFromChatId(chatId); }
            }
            if (applyingShellHistory || mode === 'none') return;
            try {
                const url = new URL(window.location.href);
                if (chatId) { url.searchParams.set('chat', String(chatId)); url.searchParams.delete('session'); }
                else { url.searchParams.delete('chat'); url.searchParams.delete('session'); }
                const next = url.pathname + url.search + url.hash;
                const state = { cuttleShell: 1, page: page || shellPageFromChatId(chatId), chatId: chatId ? String(chatId) : null };
                const cur = window.location.pathname + window.location.search + window.location.hash;
                if (next !== cur || mode === 'replace' || mode === 'push') window.history.replaceState(state, '', next);
            } catch (_) {}
        }
        function currentShellPage() {
            try { const state = getState(0); if (state && state.page) return state.page; } catch (_) {}
            try {
                const url = new URL(window.location.href), cid = url.searchParams.get('chat') || url.searchParams.get('session');
                if (cid) return shellPageFromChatId(cid);
            } catch (_) {}
            try { const live = sessionForColumn(0); if (live) return shellPageFromChatId(live); } catch (_) {}
            return '/chat_page.html?new=1';
        }
        function applyShellHistoryState(page) {
            applyingShellHistory = true;
            try { navigate(0, page || '/chat_page.html?new=1', { history: 'none' }); }
            finally { applyingShellHistory = false; }
        }
        function popShellBack() {
            if (shellBackStack.length) { applyShellHistoryState(shellBackStack.pop()); return true; }
            const page = currentShellPage();
            if (!isShellNewChatPage(page)) { host.forgetChatHandle(); applyShellHistoryState('/chat_page.html?new=1'); }
            return false;
        }

        // Frame creation and soft teardown belong to the pane lifetime. The
        // composition root supplies its existing transition and layout hooks.
        function createContentFrame(colIdx) {
            const frame = document.createElement('iframe');
            frame.frameBorder = '0';
            frame.allowFullscreen = true;
            frame.allow = 'clipboard-read; clipboard-write';
            frame.setAttribute('aria-label', 'Cuttle Content');
            if (colIdx === 0) {
                frame.id = 'contentFrame';
                frame.name = 'contentFrame';
            }
            return frame;
        }

        function replaceFrameInPlace(colIdx, page) {
            const col = getColumnEl(colIdx);
            if (!col) return null;
            const mainEl = col.querySelector('.shell-main');
            if (!mainEl) return null;
            pauseAllShellPaneStreams();
            let frame = getLiveFrame(colIdx);
            if (!frame) {
                frame = createContentFrame(colIdx);
                mainEl.appendChild(frame);
                attachFrameLoadListener(colIdx, frame);
            }
            Array.from(mainEl.querySelectorAll('iframe')).forEach((f) => {
                if (f !== frame) { try { f.remove(); } catch (_) {} }
            });
            armOwnedReveal(colIdx, frame, mainEl, []);
            stampComposerDraftScope(frame, colIdx);
            const target = withCacheBust(page);
            setTimeout(() => { try { frame.src = target; } catch (_) {} }, 0);
            return frame;
        }

        function replaceFrame(colIdx, page) {
            if (getSplitColumnCount() > 1) return replaceFrameInPlace(colIdx, page);
            const col = getColumnEl(colIdx);
            if (!col) return null;
            const mainEl = col.querySelector('.shell-main');
            if (!mainEl) return null;
            const oldFrames = Array.from(mainEl.querySelectorAll('iframe'))
                .filter((f) => !f.classList.contains('shell-frame-outgoing'));
            oldFrames.forEach(pauseShellFrameStreams);
            oldFrames.forEach(cleanupFrame);
            const newFrame = createContentFrame(colIdx);
            if (colIdx === 0) oldFrames.forEach((f) => {
                f.removeAttribute('id'); f.removeAttribute('name');
            });
            armOwnedReveal(colIdx, newFrame, mainEl, oldFrames);
            mainEl.appendChild(newFrame);
            stampComposerDraftScope(newFrame, colIdx);
            newFrame.src = withCacheBust(page);
            return newFrame;
        }

        function parkShellIframes(container) {
            if (!container) return;
            Array.from(container.querySelectorAll('iframe')).forEach((frame) => {
                cleanupFrame(frame);
                try { cancelOwnedReveal(frame); } catch (_) {}
                try { pauseShellFrameStreams(frame); } catch (_) {}
                try {
                    if (frame.contentDocument !== null) {
                        frame.contentWindow?.postMessage({ type: 'cuttle-pane-teardown' }, '*');
                    }
                } catch (_) {}
                try { frame.style.display = 'none'; frame.style.pointerEvents = 'none'; } catch (_) {}
                setTimeout(() => { try { frame.remove(); } catch (_) {} }, 0);
            });
        }

        function discardSplitSubtree(container) {
            if (!container || container.dataset?.splitDiscarded === '1') return;
            try {
                container.dataset.splitDiscarded = '1';
                container.style.display = 'none';
                container.style.pointerEvents = 'none';
                container.setAttribute('aria-hidden', 'true');
                if (container.classList?.contains('split-column')) {
                    container.dataset.column = `discarded-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
                }
                container.querySelectorAll('.split-column, .split-group').forEach((el) => {
                    el.dataset.splitDiscarded = '1';
                    el.style.pointerEvents = 'none';
                    if (el.classList.contains('split-column')) {
                        el.dataset.column = `discarded-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
                    }
                });
            } catch (_) {}
            try {
                container.querySelectorAll('iframe').forEach((frame) => {
                    cleanupFrame(frame);
                    try { cancelOwnedReveal(frame); } catch (_) {}
                    try {
                        frame.style.display = 'none';
                        frame.style.pointerEvents = 'none';
                        frame.setAttribute('aria-hidden', 'true');
                    } catch (_) {}
                    try {
                        frame.removeAttribute('src');
                        frame.srcdoc = '<!doctype html><title></title>';
                    } catch (_) {}
                });
            } catch (_) {}
        }

        // Chromium shares ~6 HTTP/1.1 connections per origin across all chat iframes.
        let focusedColumnIdx = 0;
        Object.defineProperty(window, 'focusedColumnIdx', {
            configurable: true,
            get: () => focusedColumnIdx,
        });
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

        // Collapsed chrome shares the Spaces activity owner and its poll/push updates.
        function refreshCollapsedPane(col, title) {
            let bar = col.querySelector(':scope > .pane-collapse-bar');
            if (!bar) {
                bar = document.createElement('button');
                bar.type = 'button';
                bar.className = 'pane-collapse-bar';
                bar.innerHTML = '<span class="shell-space-activity" hidden></span>'
                    + '<span class="pane-collapse-title"></span><span class="pane-collapse-arrow" aria-hidden="true">↔</span>';
                const close = document.createElement('button');
                close.type = 'button';
                close.className = 'pane-collapse-close';
                close.textContent = '×';
                close.addEventListener('click', e => {
                    e.preventDefault();
                    e.stopPropagation();
                    host.closePane(col);
                });
                col.append(bar, close);
            }
            bar.querySelector('.pane-collapse-title').textContent = title;
            bar.setAttribute('aria-label', 'Expand ' + title);
            bar.title = 'Expand ' + title;
            const close = col.querySelector(':scope > .pane-collapse-close');
            close.title = 'Close ' + title;
            close.setAttribute('aria-label', 'Close ' + title);
            const vertical = col.parentElement?.classList.contains('split-vertical');
            bar.querySelector('.pane-collapse-arrow').textContent = vertical ? '↕' : '↔';
            const sid = sessionForColumn(parseInt(col.dataset.column, 10));
            const activity = host.spacesActivity;
            const kind = activity && sid
                ? activity.selectSpaceActivity([sid], activity.lookupSessionActivity, false, new Set()) : '';
            const dot = bar.querySelector('.shell-space-activity');
            dot.className = 'shell-space-activity' + (kind ? ' is-' + kind : '');
            dot.hidden = !kind;
            const label = activity?.LABEL[kind] || '';
            dot.setAttribute('aria-label', label);
            bar.setAttribute('aria-label', 'Expand ' + title + (label ? ' · ' + label : ''));
        }
        function syncCollapsedPanes() {
            getSplitColumns().forEach(col => {
                if (col.classList.contains('pane-collapsed')) {
                    if (host.refreshParkedPaneTitle) host.refreshParkedPaneTitle(col);
                    else refreshCollapsedPane(col, col.dataset.parkedTitle || 'Pane');
                }
            });
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
        return { refreshCollapsedPane, syncCollapsedPanes, setFocusedColumn, focusedColumnIndex: () => focusedColumnIdx, broadcastUsageLiveWake,
            createContentFrame, replaceFrameInPlace, replaceFrame, parkShellIframes, discardSplitSubtree,
            applyUiAnimationPrefs, armFrameReveal: armOwnedReveal, cancelPendingReveal: cancelOwnedReveal,
            finishFrameTransitionFor, clearRevealColumn, remapRevealColumns, swapRevealColumns,
            attachFrameLoadListener: attachOwnedFrameLoadListener,
            navigate,
            shellPageFromChatId, shellPagesEqual, isShellNewChatPage, currentShellPage,
            pushShellBackEntry, syncShellChatUrl, applyShellHistoryState, popShellBack,
            isApplyingHistory: () => applyingShellHistory,
            registerPaneChatSession, getOpenPaneSessions, registerPaneProject, refreshPendingChangesPath,
            schedulePendingChangesHub, pollPendingChangesHub, pollLiveStatusHub, clearPaneColumn,
            scheduleBroadcastOpenPanes, remapColumns, swapColumns, hasSessions, broadcastFocus: broadcastPaneFocus,
            setFocusedColumnDirect, sessionForColumn, sessionForColumnExists, swapSessionColumns };
    }

    root.CuttleShellPanes = { create };
})(window);
