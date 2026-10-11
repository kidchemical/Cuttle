// Chat Page JavaScript - ChatGPT-like Interface
(function() {
    'use strict';

    const LOG = (...args) => console.log('[Cuttle Chat]', ...args);
    const LOG_ERR = (...args) => console.error('[Cuttle Chat]', ...args);
    LOG('Script loaded');

    // Listeners this frame adds to the app-shell window must go with the frame:
    // the shell outlives every chat iframe, so a lingering closure keeps the
    // whole unloaded chat document alive (renderer OOM after many chat switches).
    const parentListenersAbort = new AbortController();
    window.addEventListener('pagehide', event => {
        if (!event.persisted) parentListenersAbort.abort();
    });

    // Keep the chat iframe document pinned, and (for existing sessions only)
    // fit .chat-layout to the visual viewport while the keyboard is up.
    // Splash composer is mid-screen so iOS never needs this; the bottom-docked
    // session composer otherwise leaves a white gap under the UI on focus.
    // Enhanced for Samsung Galaxy (Android) where keyboard covers content and
    // for iPad where focus moves page up — handles both iframe and parent viewport.
    (function pinChatDocumentScroll() {
        const getEffectiveViewport = () => {
            const vv = window.visualViewport;
            let pv = null;
            try {
                if (window.parent && window.parent !== window && window.parent.visualViewport) {
                    pv = window.parent.visualViewport;
                }
            } catch (_) {}
            let height = vv ? vv.height : window.innerHeight;
            let offsetTop = vv ? (vv.offsetTop || 0) : 0;
            if (pv) {
                if (pv.height < height) height = pv.height;
                if (pv.offsetTop > offsetTop) offsetTop = pv.offsetTop;
            }
            const winH = window.innerHeight;
            if (winH && winH < height) height = winH;
            const docH2 = document.documentElement.clientHeight;
            if (docH2 && docH2 < height) height = docH2;
            try {
                if (window.parent && window.parent !== window && window.parent.innerHeight < height) {
                    height = window.parent.innerHeight;
                }
            } catch (_) {}
            return { height, offsetTop, vv, pv };
        };
        const pin = () => {
            if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
            if (document.documentElement.scrollTop) document.documentElement.scrollTop = 0;
            if (document.body.scrollTop) document.body.scrollTop = 0;

            const root = document.querySelector('.chat-layout');
            const chatArea = document.getElementById('chatArea');
            const eff = getEffectiveViewport();
            if (!root) return;

            const chatVisible = chatArea
                && chatArea.style.display !== 'none'
                && window.getComputedStyle(chatArea).display !== 'none';

            if (!chatVisible || (!eff.vv && !eff.pv && !window.innerHeight)) {
                root.style.position = '';
                root.style.top = '';
                root.style.left = '';
                root.style.right = '';
                root.style.width = '';
                root.style.height = '';
                return;
            }

            root.style.position = 'fixed';
            root.style.left = '0';
            root.style.right = '0';
            root.style.width = '100%';
            // Clamp height to the actual iframe viewport (narrow split columns must not use full-window height)
            let clampedHeight = eff.height;
            const docH = document.documentElement.clientHeight;
            if (docH && docH < clampedHeight) clampedHeight = docH;
            // iPad Safari reports offsetTop that nudges page past bottom - clamp to 0 unless truly shifted
            const topVal = (eff.offsetTop && eff.offsetTop > 10 && clampedHeight < window.innerHeight * 0.95) ? eff.offsetTop : 0;
            root.style.top = topVal + 'px';
            root.style.height = clampedHeight + 'px';
        };
        window.addEventListener('scroll', pin, { passive: true });
        function unlockComposerField(el) {
            if (!el || (el.id !== 'chatInput' && el.id !== 'welcomeChatInput')) return false;
            if (!el.hasAttribute('readonly')) return false;
            el.removeAttribute('readonly');
            return true;
        }
        function unlockAllComposerFields() {
            unlockComposerField(document.getElementById('chatInput'));
            unlockComposerField(document.getElementById('welcomeChatInput'));
        }
        // readonly is an iPad autofill shield. Android WebView often never
        // focuses a readonly textarea, so the composer looks dead while
        // history buttons still work. Unlock on press, not only on focusin.
        document.addEventListener('pointerdown', (e) => {
            if (unlockComposerField(e.target)) {
                try { e.target.focus({ preventScroll: true }); } catch (_) {
                    try { e.target.focus(); } catch (_2) {}
                }
            }
        }, true);
        document.addEventListener('touchstart', (e) => {
            unlockComposerField(e.target);
        }, { capture: true, passive: true });
        document.addEventListener('focusin', (e) => {
            pin();
            const t = e.target;
            if (t && (t.id === 'chatInput' || t.id === 'welcomeChatInput')) {
                unlockComposerField(t);
                setTimeout(() => {
                    try { t.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (_) {}
                    pin();
                }, 100);
                setTimeout(pin, 300);
            }
        });
        setTimeout(unlockAllComposerFields, 280);
        document.addEventListener('focusout', () => {
            setTimeout(pin, 100);
            setTimeout(pin, 300);
        });
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', pin);
            window.visualViewport.addEventListener('scroll', pin);
        }
        try {
            if (window.parent && window.parent !== window && window.parent.visualViewport) {
                const parentOpts = { signal: parentListenersAbort.signal };
                window.parent.visualViewport.addEventListener('resize', pin, parentOpts);
                window.parent.visualViewport.addEventListener('scroll', pin, parentOpts);
            }
        } catch (_) {}
        window.addEventListener('resize', pin);
        window.__cuttlePinChatLayout = pin;
    })();

    /**
     * Auth DB routes (/api/auth/sessions/<int>/...) need bare numeric ids.
     * Pipeline runtime often uses "db_session_<id>" — strip that for auth APIs
     * and for stable URL / equality comparisons.
     */
    // Owned by chat_activity.js — thin adapter.
    function toAuthDbSessionId(sessionId) {
        return CuttleChatActivity.toAuthDbSessionId(sessionId);
    }

    // Owned by chat_activity.js — thin adapter.
    function canonicalizeChatSessionId(sessionId) {
        return CuttleChatActivity.canonicalizeChatSessionId(sessionId);
    }

    // Owned by chat_activity.js — thin adapter.
    function sessionIdsEqual(a, b) {
        return CuttleChatActivity.sessionIdsEqual(a, b);
    }

    /**
     * Human-friendly chat id shown in the corner badge (and copied on click).
     * Auth DB ids stay numeric internally; display as CH-XXXXXX.
     * Numeric sessions use zero-padded decimal (CH-000042) so they match the DB id.
     */
    // Owned by chat_activity.js — thin adapter.
    function formatChatDisplayId(sessionId) {
        return CuttleChatActivity.formatChatDisplayId(sessionId);
    }

    /** New anonymous (localStorage) chat ids — CH-XXXXXX. */
    function generateLocalChatId() {
        const mod = Math.pow(36, 6);
        let id;
        do {
            const mixed = (Date.now() + Math.floor(Math.random() * 46656)) % mod;
            id = 'CH-' + mixed.toString(36).toUpperCase().padStart(6, '0');
        } while (readTerminalSessionRegistry()[id]);
        return id;
    }

    /** Shared with terminal_page.js — durable terminal chat handles. */
    const TERMINAL_SESSIONS_KEY = 'cuttle_terminal_sessions_v1';
    const TERMINAL_LAYOUT_KEY = 'cuttle_terminal_layout_v1';

    function readTerminalSessionRegistry() {
        try {
            const map = JSON.parse(localStorage.getItem(TERMINAL_SESSIONS_KEY) || '{}');
            return map && typeof map === 'object' ? map : {};
        } catch (_) {
            return {};
        }
    }

    function writeTerminalSessionRegistry(map) {
        try {
            localStorage.setItem(TERMINAL_SESSIONS_KEY, JSON.stringify(map || {}));
        } catch (_) {}
    }

    function isTerminalSessionId(sessionId) {
        if (sessionId == null || sessionId === '') return false;
        const map = readTerminalSessionRegistry();
        return !!map[String(sessionId)];
    }

    function getTerminalHistoryEntries() {
        const map = readTerminalSessionRegistry();
        return Object.values(map)
            .filter((s) => s && s.id)
            .map((s) => ({
                sessionId: s.id,
                session: {
                    ...s,
                    type: 'terminal',
                    projectId: s.projectId != null ? s.projectId : '__terminal__',
                    projectName: s.projectName || 'Terminal',
                    projectPath: s.projectPath || '',
                    title: s.title || s.shell || 'Terminal',
                },
                updated: Number(s.updated) || Number(s.created) || 0,
                isTerminal: true,
            }));
    }

    function removeTerminalSessionFromStorage(sessionId) {
        const id = String(sessionId);
        const map = readTerminalSessionRegistry();
        if (map[id]) {
            delete map[id];
            writeTerminalSessionRegistry(map);
        }
        // Drop from open-tabs layout if present.
        try {
            const raw = localStorage.getItem(TERMINAL_LAYOUT_KEY);
            if (!raw) return;
            const layout = JSON.parse(raw);
            if (!layout || !Array.isArray(layout.tabs)) return;
            layout.tabs = layout.tabs.filter((t) => t && String(t.id) !== id);
            if (String(layout.active) === id) {
                layout.active = layout.tabs.length ? layout.tabs[layout.tabs.length - 1].id : null;
            }
            localStorage.setItem(TERMINAL_LAYOUT_KEY, JSON.stringify(layout));
        } catch (_) {}
    }

    function openTerminalSession(sessionId) {
        if (sessionId == null || sessionId === '') return;
        const page = '/terminal_page.html?chat=' + encodeURIComponent(String(sessionId));
        try {
            if (typeof closeChatHistoryPanel === 'function') closeChatHistoryPanel();
        } catch (_) {}
        const shell = window.parent;
        if (shell && shell !== window && typeof shell.navigateFromSource === 'function') {
            shell.navigateFromSource(window, page);
            return;
        }
        if (shell && shell !== window && typeof shell.navigate === 'function') {
            const colIdx = typeof shell.findColumnIndexForSource === 'function'
                ? shell.findColumnIndexForSource(window)
                : 0;
            shell.navigate(colIdx, page);
            return;
        }
        window.location.href = page;
    }

    function updateChatIdBadge(sessionId) {
        // Menu items are rebuilt on open; this only keeps the session title in sync.
        const displayId = formatChatDisplayId(sessionId);
        if (!displayId) {
            updateChatSessionTitle(null);
            return;
        }
        updateChatSessionTitle(sessionId);
    }

    // Titles learned from /messages or the session list — used when
    // CuttleAuth.getSessions() is still empty on refresh (history panel closed).
    const _sessionTitleById = Object.create(null);

    function rememberChatSessionTitle(sessionId, title) {
        if (sessionId == null || sessionId === '') return;
        const name = String(title || '').trim();
        if (!name) return;
        const key = String(canonicalizeChatSessionId(sessionId));
        _sessionTitleById[key] = name;
        const authKey = String(toAuthDbSessionId(sessionId) || '');
        if (authKey && authKey !== key) _sessionTitleById[authKey] = name;
    }

    function updateChatSessionArchivedBadge(sessionId) {
        const badge = document.getElementById('chatSessionArchivedBadge');
        if (!badge) return;
        badge.hidden = !(sessionId != null && sessionId !== '' && isChatSessionArchived(sessionId));
    }

    function updateChatSessionTitle(sessionId) {
        const wrap = document.getElementById('chatSessionTitle');
        const text = document.getElementById('chatSessionTitleText');
        if (!wrap || !text) return;
        if (sessionId == null || sessionId === '') {
            wrap.hidden = true;
            text.textContent = '';
            wrap.removeAttribute('title');
            updateChatSessionStarButton(null);
            updateChatSessionArchivedBadge(null);
            updateSessionTitleUnreadDot();
            updateSessionTitleRunningIcon();
            return;
        }
        const raw = getChatSessionDisplayTitle(sessionId);
        let name = 'Chat';
        try {
            const parts = chatTitleParts(raw, sessionId);
            // Prefer the plain name (chips stay in the history panel).
            const tmp = document.createElement('div');
            tmp.innerHTML = parts.titleHtml || '';
            name = String(tmp.textContent || '').trim() || 'Chat';
        } catch (_) {
            name = String(raw || '').trim() || 'Chat';
        }
        text.textContent = name;
        wrap.title = name;
        wrap.hidden = false;
        updateChatSessionStarButton(sessionId);
        updateChatSessionArchivedBadge(sessionId);
        updateSessionTitleUnreadDot();
        updateSessionTitleRunningIcon();
    }

    function updateChatSessionStarButton(sessionId) {
        const btn = document.getElementById('chatSessionStarBtn');
        const hasSession = sessionId != null && sessionId !== '';
        const starred = hasSession && !!isChatSessionStarred(sessionId);

        if (btn) {
            // Title star is indicator-only when applied; click unstars.
            btn.hidden = !starred;
            btn.classList.toggle('is-starred', starred);
            btn.setAttribute('aria-pressed', starred ? 'true' : 'false');
            btn.title = 'Unstar chat';
            btn.setAttribute('aria-label', 'Unstar chat');
        }
        // Star / unread overflow items are rebuilt when either menu opens.
    }

    function toggleCurrentChatStar(event) {
        if (session.current() == null || session.current() === '') return;
        return toggleStarChatSession(session.current(), event);
    }

    async function toggleCurrentChatStarFromMenu(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        closeAllSessionMenus();
        return toggleCurrentChatStar(event);
    }

    /** Manual unread — history green dot until this chat is opened again. */
    function markSessionUnreadManual(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        if (sessionId == null || sessionId === '') return;
        markChatSessionUnread(sessionId, {manual: true});
        if (session.current() != null && sessionIdsEqual(session.current(), sessionId)) {
            _manualUnreadHoldId = String(sessionId);
            // Title green dot until click/type; prefs flag survives leave → reopen.
            chatNeedsAck = true;
            chatViewportActivated = false;
            syncChatAttentionIndicators();
        }
        syncHistoryUnreadIndicators();
        closeAllSessionMenus();
        try {
            const toast = window.showToast;
            if (typeof toast === 'function') toast('Marked as unread', 'info');
        } catch (_) {}
    }

    function markCurrentSessionUnreadFromMenu(event) {
        if (session.current() == null || session.current() === '') return;
        return markSessionUnreadManual(session.current(), event);
    }

    function currentProjectPathKey() {
        // Git/branch scope: the bound worktree checkout, else the project path.
        return effectiveGitPath();
    }

    // Git push menu + action owner: the menu HTML/mode, repo cache, multi-repo
    // submenu, confirm modal and push transport live in git/git_push_report.js
    // (CuttleGitPushReport). The page supplies project identity, the
    // pending-changes repo snapshot and DOM/toast/close effects as explicit
    // host capabilities; the inline-handler wiring below stays page-side.
    const gitPush = CuttleGitPushReport.create({
        getProjectPath: function () { return currentProjectPathKey(); },
        getPendingRepos: function () {
            if (pendingChangesCtl && typeof pendingChangesCtl.getRepos === 'function') {
                return pendingChangesCtl.getRepos() || [];
            }
            return [];
        },
        getCurrentProject: function () { return currentProject; },
        closeMenus: function () { closeAllSessionMenus(); },
        escapeHtmlInline: escapeHtmlInline,
        toast: function () {
            const t = window.showToast;
            if (typeof t === 'function') return t.apply(window, arguments);
        },
        closeToast: function (id) {
            if (typeof window.closeCuttleToast === 'function') window.closeCuttleToast(id);
        },
    });

    // Branch shown on the segmented project chip (star | name | branch).
    // Cache + fetch live in chat_chip_config.js (branchFor / refreshBranch).
    function currentProjectBranch() {
        return CuttleChipConfig.branchFor(currentProjectPathKey());
    }

    /** Refresh the project chip branch (cached ~15s). Re-renders when it changes. */
    function refreshProjectBranch(force) {
        return CuttleChipConfig.refreshBranch(currentProjectPathKey(), renderProjectChips, force);
    }

    function copyTextToClipboard(text) {
        if (!text) return Promise.resolve(false);
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function' && window.isSecureContext) {
            return navigator.clipboard.writeText(text).then(() => true).catch(() => copyTextFallback(text));
        }
        return Promise.resolve(copyTextFallback(text));
    }

    async function copyChatSessionId(sessionId, event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        const id = formatChatDisplayId(sessionId) || '';
        if (!id) return;
        const btn = event && event.currentTarget && event.currentTarget.closest
            ? event.currentTarget.closest('.chat-chrome-menu-item')
            : null;
        const ok = await copyTextToClipboard(id);
        if (ok) {
            if (btn) {
                btn.classList.add('copied');
                const menuLabel = btn.querySelector('.chat-chrome-menu-item-label');
                const prev = menuLabel ? menuLabel.textContent : '';
                if (menuLabel) menuLabel.textContent = 'Copied ' + id;
                btn.title = 'Copied ' + id;
                setTimeout(() => {
                    btn.classList.remove('copied');
                    btn.title = 'Copy chat ID (' + id + ')';
                    if (menuLabel) menuLabel.textContent = prev || ('Copy ' + id);
                }, 1500);
            }
            (window.showToast || function () {})('Copied ' + id, 'success');
            // Leave menu open briefly so the "Copied" state is visible, then close.
            setTimeout(() => closeAllSessionMenus(), 400);
        } else {
            (window.showToast || function () {})('Could not copy chat ID', 'error');
        }
    }

    async function copyChatIdBadge() {
        return copyChatSessionId(session.current(), null);
    }

    // State Management
    // Session identity and its navigation sequence are owned by
    // chat_session.js (CuttleChatSession) — the only writer. Reads go through
    // session.current(); every identity change goes through
    // session.open/adopt/create. See chat_session.js.
    const session = CuttleChatSession.create();
    if (window.CuttleChatVfx) {
        const vfxController = window.CuttleChatVfx.create({
            getSessionId: () => session.current(),
            fetch: window.fetch.bind(window), now: Date.now,
            setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window),
            confetti: window.CuttleChatVfx.confetti,
            toast: (message, variant) => (window.showToast || function () {})(message, variant),
        });
        window.addEventListener('pagehide', event => { if (!event.persisted) vfxController.dispose(); });
    }

    /**
     * In-session attention (title unread + jump glow). Separate from history
     * hasUnread: stays until the user scrolls to the new content and/or clicks
     * the transcript / types in the composer.
     */
    let chatUnseenBelow = false;
    let chatNeedsAck = false;
    /** When title/jump attention is for a failed assistant reply (red, not green). */
    let chatAttentionIsError = false;
    /** Once true for this open session, new bottom-pinned replies won't re-nag the title. */
    let chatViewportActivated = true;
    let _suppressChatAttention = 0;
    // Busy-generation lock { loading, localSessionId, seq }, owned by
    // chat_generation.js. Exactly one instance; all transitions go through
    // CuttleChatGeneration (token-scoped release) — never direct writes.
    // Reads stay inline as generation.loading / generation.localSessionId.
    const generation = CuttleChatGeneration.createGenerationState();
    /** Session ids known to be generating (local stream and/or remote live status). */
    const runningSessionIds = new Set();
    /** Sessions with an active action form (pending restart / watch) — same spinner. */
    const formAwaitingSessionIds = new Set();
    /**
     * After switching chats, ignore sessions.generating for the open chat until
     * the one-shot live-status runs. Prevents a purple title/history flash from
     * a stale busy flag; does not change poll intervals.
     */
    let deferOpenChatGeneratingFromSessions = false;
    let agentName = 'Cuttle';
    let currentAgentIdentity = { displayName: '', avatar: '', profileId: '', parentSessionId: null, origin: '' };
    let currentProject = null;
    // Bound worktree checkout for this chat (experimental). '' = Local/primary.
    let currentCheckoutUid = '';
    let currentCheckoutPath = '';
    /** Project fields from the last /messages load — used when the sessions list is empty or stale. */
    let lastHydratedSessionProject = null;
    let projects = [];
    let activeRequestController = null;
    let activeEventSource = null;

    const chatWidgetsCtrl = (window.CuttleTaskGizmos && typeof window.CuttleTaskGizmos.create === 'function')
        ? window.CuttleTaskGizmos.create({
            getSessionId: function () { return session.current(); },
            getProjectPath: function () {
                return (currentProject && currentProject.path) ? String(currentProject.path) : '';
            },
        })
        : null;

    function refreshChatWidgets() {
        try {
            if (chatWidgetsCtrl) chatWidgetsCtrl.refresh();
        } catch (_) {}
    }

    function noteWidgetsRevision(liveStatus) {
        try {
            if (chatWidgetsCtrl && liveStatus && liveStatus.widgets_revision != null) {
                chatWidgetsCtrl.onRevision(liveStatus.widgets_revision);
            }
        } catch (_) {}
    }

    // Cross-device auth chat sync (poll + focus). LocalStorage chats need no sync.
    let lastSeenServerMessageId = 0;
    /** Lazy transcript window — newest page first; scroll-up loads older pages. */
    const CHAT_HISTORY_PAGE_SIZE = 10;
    let chatHistoryHasMore = false;
    let chatHistoryOlderVisibleCount = 0;
    let chatHistoryOldestId = null;
    let chatHistoryLoadingOlder = false;
    let _chatHistoryLazyScrollWired = false;
    /**
     * When the API returns a full transcript (no has_more — pre-pagination server
     * or legacy prefetch), hold older rows here and peel them off on scroll-up
     * without another round trip.
     */
    let chatHistoryClientBuffer = [];
    let messageSyncTimer = null;
    // Message-sync claim lifetime, owned by CuttleChatGeneration (D2b):
    // one page-held state; claim, current-token test, and token-scoped
    // finish all go through the owner. Navigation fencing still uses the
    // existing navigation sequence captured per sync below.
    // 3C (message sync) KEPT page-side after the post-3B review: session
    // identity is owned by chat_session.js and every sync decision already
    // has an owner (cadence + claim/replacement in chat_generation.js,
    // replay classification in chat_pending_result.js, windowing/page-meta
    // in chat_messages.js). Only the timer handle + DOM reconciliation
    // effects remain, which the page owns; see monolith-refactor-plan.md §3C.
    const messageSync = CuttleChatGeneration.createSyncState();
    // Stop/cancel lifecycle flags { userStopped, abortSuppressed,
    // waitingSuppressed }, owned by chat_stop_state.js. One instance;
    // transitions via CuttleStopState, never direct writes. After Stop,
    // the waiting latch suppresses "waiting for reply" until next send.
    const stopState = CuttleStopState.createStopState();
    /**
     * Bumped on New Chat / history switch so an orphaned SSE or pending waiter
     * from the previous chat cannot paint status or re-adopt into the open UI
     * (phone: start A → New Chat → send B → A's activity showed on B).
     */
    // Turn/staleness token source, owned by chat_turn_guard.js. Exactly
    // one instance; New Chat / history switch bumps it (see detach).
    const turnGeneration = CuttleTurnGuard.createGeneration();
    // Message-sync poll cadence intervals, owned by chat_generation.js
    // (CuttleChatGeneration.SYNC_MS). Split panes each run this timer.
    // Aggressive dual GETs exhaust Chromium's HTTPS/HTTP pool and wedge
    // Electron / starve YouTube + refresh.
    /** Inside app shell iframe — live-status is batched by the parent. */
    const inAppShell = !!(window.parent && window.parent !== window);
    let shellPaneFocused = true;
    let shellHubSeen = false;
    let hubLiveStatusCache = null; // { sessionId, live, at }
    /** Open chats/terminals in THIS window's split panes only (not other devices). */
    let localOpenPanes = [];
    /** Blocks double-firing send (capture delegation + button, or mobile double-tap). */
    let sendDispatchGuard = false;
    /** Text of the user turn currently generating — used to ignore duplicate sends. */
    let inFlightUserMessage = null;

    // Follow-up queue while a reply is generating. On idle, the whole queue is
    // combined into one agent turn (not drained one-by-one). Queue state, the
    // edit state machine and the enqueue/pause/resume/take/drain/steer
    // transitions are owned by chat_followup_queue.js (createController); the
    // page supplies transport, persistence, DOM render and timers via the host.
    // Built lazily so page constants declared later are initialized.
    let _followupController = null;
    function followupController() {
        if (!_followupController) {
            _followupController = CuttleFollowupQueue.createController({
                activity: CuttleChatActivity,
                clock: { now: () => Date.now(), rand: () => Math.random() },
                sessionIdsEqual: sessionIdsEqual,
                canonicalizeSessionId: (id) => canonicalizeChatSessionId(id),
                toAuthDbSessionId: (id) => toAuthDbSessionId(id),
                currentSession: () => session.current(),
                isAuthMode: isAuthMode,
                isSessionGenerating: isSessionGenerating,
                healStaleGenerating: healStaleGeneratingState,
                followupAuthSid: followupAuthSid,
                getPrefs: (sid) => getSessionPrefs(sid),
                setPrefs: (sid, patch) => updateSessionPrefs(sid, patch),
                writeField: () => followupWriteField,
                claimField: () => followupClaimField,
                restoreQueue: (key) => getSessionPrefs(key)?.[followupWriteField],
                serializeMutation: (key, fn) => sessionMutations.enqueue(key, fn),
                putFollowups: (sid, body) => followupPutRequest(sid, body),
                takeFollowups: (sid, body) => followupTakeRequest(sid, body),
                steer: (sid, text) => followupSteerRequest(sid, text),
                render: renderFollowupQueue,
                patchLiveSession: patchLiveSessionFollowupQueue,
                broadcastActivity: () => scheduleChatActivityBroadcast(),
                showChatArea: showFollowupChatArea,
                addUserMessage: (text, opts) => addMessageToUI(text, 'user', opts),
                autoScroll: () => transcriptNav.autoScrollChatToBottom(true),
                saveUserMessage: (text, opts) => saveChatSession(text, 'user', opts),
                recordPromptHistory: recordPromptHistory,
                processMessage: processMessage,
                toast: (msg, kind) => (window.showToast || function () {})(msg, kind),
                steerLabel: (agent) => ({ codex: 'Codex', muse: 'Muse Code' })[agent],
                setTimeout: (fn, ms) => setTimeout(fn, ms),
                clearTimeout: (id) => clearTimeout(id),
                formatWithAttachments: formatMessageWithAttachments,
                stickyPrefixFor: (raw) => {
                    const sticky = getStickySlashCommandFromMessage(raw);
                    return sticky ? sticky.prefix : '';
                },
            });
        }
        return _followupController;
    }
    function adoptFollowupQueue(sessionId, opts = {}) {
        followupController().adopt(sessionId, opts);
    }
    /** Uploaded file refs awaiting the next send: {filename, path, mime, size?} */
    let pendingAttachments = [];
    function _pendingKey(sid) { return `cuttle.pendingAttachments.${sid || 'anon'}`; }
    function _savePendingAttachments() {
        try {
            if (!session.current()) return;
            localStorage.setItem(_pendingKey(session.current()), JSON.stringify(pendingAttachments));
        } catch {}
    }
    function _restorePendingAttachments() {
        try {
            if (!session.current()) return;
            const raw = localStorage.getItem(_pendingKey(session.current()));
            if (raw) pendingAttachments = JSON.parse(raw) || [];
        } catch {}
        renderAttachmentChips();
    }
    function _clearPersistedPending(sid) {
        try { localStorage.removeItem(_pendingKey(sid)); } catch {}
    }

    // Unsent composer text survives hard-refresh / Electron restart (per chat id).
    // The shell supplies space/pane identity; standalone chat keeps the legacy
    // `new` draft key. Capture once, never consult the active space on unload.
    const newComposerDraftId = (() => {
        try {
            const scope = window.frameElement?.dataset.cuttleDraftScope;
            if (scope) return 'new:' + scope;
        } catch (_) {}
        return 'new';
    })();
    const newComposerPrefsId = 'draft:' + newComposerDraftId;
    const followupOwnerId = CuttleFollowupQueue.ownerId(() => sessionStorage, newComposerDraftId);
    const followupWriteField = 'followupWrite:' + followupOwnerId;
    const followupClaimField = 'followupClaim:' + followupOwnerId;

    const COMPOSER_DRAFT_MAX = 50000;
    let composerDraftControlsReady = false;
    // Owned by chat_composer.js — shell-scoped `new` draft id in, key out.
    function _draftKey(sid) {
        return CuttleChatComposer.composerDraftKey(sid, newComposerDraftId);
    }
    function _activeComposerTextarea() {
        const welcome = document.getElementById('welcomeScreen');
        const welcomeOn = welcome && welcome.style.display !== 'none';
        if (welcomeOn) return document.getElementById('welcomeChatInput');
        return document.getElementById('chatInput') || document.getElementById('welcomeChatInput');
    }
    function _readComposerDraftText() {
        const el = _activeComposerTextarea();
        return el ? String(el.value || '') : '';
    }
    function saveComposerDraftControls(sid, composerKey) {
        if (!composerDraftControlsReady) return;
        const el = _activeComposerTextarea();
        const key = composerKey || (el && el.id === 'welcomeChatInput' ? 'welcome' : 'chat');
        updateSessionPrefs(sid === 'new' ? newComposerPrefsId : sid, {
            composerDraft: {
                chips: CuttleChatComposer.draftChips(slashCtx[key].chips),
                pins: CuttleChatAgentModel.draftOverrides(slashPaletteSupplement),
            },
        });
    }

    function restoreComposerDraftControls(sid) {
        const prefs = getSessionPrefs(sid === 'new' ? newComposerPrefsId : sid);
        // Owned by chat_composer.js — prefs snapshot in, chips/pins out.
        const plan = CuttleChatComposer.composerDraftRestorePlan(
            prefs && prefs.composerDraft,
            { draftChips: CuttleChatComposer.draftChips }
        );
        if (!plan) return;
        const chips = plan.chips;
        Object.assign(slashPaletteSupplement, CuttleChatAgentModel.draftSupplementPatch(plan.pins));
        if (plan.cursorModel) {
            slashPaletteSupplement.preferredModel = plan.cursorModel.modelId
                || plan.cursorModel.prefix.replace(/^\/model\s+/i, '').trim();
            beginSupplementFetch('cursorModelsGen');
        }
        slashCtx.welcome.chips = chips.slice();
        slashCtx.chat.chips = chips.slice();
    }

    function saveComposerDraft(sid, composerKey) {
        const keySid = sid != null && sid !== '' ? sid : (session.current() || 'new');
        saveComposerDraftControls(keySid, composerKey);
        try {
            // Owned by chat_composer.js — whitespace/empty + clip decision.
            const plan = CuttleChatComposer.composerDraftSavePlan(
                _readComposerDraftText(), COMPOSER_DRAFT_MAX);
            const key = _draftKey(keySid);
            if (plan.remove) {
                localStorage.removeItem(key);
                return;
            }
            localStorage.setItem(key, plan.text);
        } catch (_) {}
    }
    function clearComposerDraft(sid) {
        const keySid = sid != null && sid !== '' ? sid : (session.current() || 'new');
        try { localStorage.removeItem(_draftKey(keySid)); } catch (_) {}
        updateSessionPrefs(keySid === 'new' ? newComposerPrefsId : keySid, { composerDraft: null });
    }
    function restoreComposerDraft(sid, opts = {}) {
        const force = !!(opts && opts.force);
        const keySid = sid != null && sid !== '' ? sid : (session.current() || 'new');
        restoreComposerDraftControls(keySid);
        composerDraftControlsReady = true;
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        renderSlashChips('chat', document.getElementById('chatInput'));
        let text = '';
        try {
            const key = _draftKey(keySid);
            let stored = localStorage.getItem(key);
            // Claim the legacy shared welcome draft once on its first restore.
            // Owned by chat_composer.js — claim predicate.
            if (CuttleChatComposer.composerDraftLegacyClaim({
                stored, keySid, newDraftId: newComposerDraftId,
            })) {
                stored = localStorage.getItem('cuttle.composerDraft.new');
                if (stored != null) {
                    localStorage.setItem(key, stored);
                    localStorage.removeItem('cuttle.composerDraft.new');
                }
            }
            text = stored || '';
        } catch (_) { text = ''; }
        const welcome = document.getElementById('welcomeScreen');
        const welcomeOn = welcome && welcome.style.display !== 'none';
        const welcomeEl = document.getElementById('welcomeChatInput');
        const chatEl = document.getElementById('chatInput');
        const el = welcomeOn ? welcomeEl : chatEl;
        if (!el) return;
        // Soft restore (boot race): don't clobber text the user already typed
        // this tick when the stored draft is empty. Session switch / New Chat
        // pass force:true so an empty destination clears leftover text.
        // Owned by chat_composer.js — restore decision.
        const decision = CuttleChatComposer.composerDraftRestoreDecision({
            force, currentValue: el.value, text,
        });
        if (!decision.apply) {
            if (decision.unchanged) {
                if (welcomeOn) autoResizeWelcomeTextarea();
                else autoResizeTextarea();
            }
            return;
        }
        el.value = text;
        // Clear the inactive composer so prior-session text cannot leak when
        // the splash and bottom dock swap.
        if (decision.clearOther) {
            const other = welcomeOn ? chatEl : welcomeEl;
            if (other && other !== el) other.value = '';
        }
        try {
            if (welcomeOn) autoResizeWelcomeTextarea();
            else autoResizeTextarea();
            syncSlashMenuFromInput(el);
        } catch (_) {}
    }
    function migrateComposerDraft(oldId, newId) {
        if (oldId == null || newId == null) return;
        if (String(oldId) === String(newId)) return;
        try {
            const raw = localStorage.getItem(_draftKey(oldId));
            if (raw == null) return;
            localStorage.setItem(_draftKey(newId), raw);
            localStorage.removeItem(_draftKey(oldId));
        } catch (_) {}
    }

    /**
     * True while this chat has an in-flight reply — local SSE stream OR server-side
     * run after refresh / dropped connection (remote waiting indicator).
     * Follow-ups must queue on this, not only on isLoading, or a second send races
     * the first and sync appends the late reply under the new user bubble (looks
     * like the transcript was "reordered").
     *
     * Use isLoadingThisSession() — not bare isLoading. A prior chat's hung stream
     * used to keep isLoading true after New Chat / history switch, so idle chats
     * parked every send in the follow-up queue for ~4–5s (one message-sync tick).
     */
    function isSessionGenerating() {
        if (isLoadingThisSession()) return true;
        if (document.getElementById('typing-indicator-remote')) return true;
        if (session.current() == null) return false;
        return [...runningSessionIds].some((rid) => sessionIdsEqual(rid, session.current()));
    }

    /** Welcome splash composer — processMessage disables it for first-turn-only. */
    function setWelcomeComposerEnabled(enabled) {
        const welcomeInput = document.getElementById('welcomeChatInput');
        const welcomeSendButton = document.getElementById('welcomeSendButton');
        if (welcomeInput) {
            welcomeInput.disabled = !enabled;
            if (enabled && welcomeInput.hasAttribute('readonly')) {
                welcomeInput.removeAttribute('readonly');
            }
        }
        if (welcomeSendButton) welcomeSendButton.disabled = !enabled;
    }

    function focusWelcomeComposer() {
        const chatInput = document.getElementById('chatInput');
        const welcomeInput = document.getElementById('welcomeChatInput');
        try { if (chatInput) chatInput.blur(); } catch (_) {}
        setWelcomeComposerEnabled(true);
        if (!welcomeInput) return;
        if (welcomeInput.hasAttribute('readonly')) welcomeInput.removeAttribute('readonly');
        try {
            welcomeInput.focus({ preventScroll: true });
        } catch (_) {
            try { welcomeInput.focus(); } catch (_2) {}
        }
    }

    /**
     * Drop local generating UI when leaving a chat (New Chat / history switch)
     * without cancelling the server run. Aborting the tab's fetch/SSE stops
     * painting here; the agent keeps going and message sync can collect later.
     *
     * Leave stopState.abortSuppressed true until the next beginLocalGeneration so
     * orphaned collectPendingResult loops exit instead of painting into the
     * next chat. CuttleTurnGuard.bump(turnGeneration) invalidates canPaintTurnHere for that turn.
     *
     * History/title: keep the *left* chat in runningSessionIds (server still
     * working). endLocalGeneration would clear it and leave a gap until the
     * next sessions poll — and a stale flag on the chat we open used to flash
     * the purple spinner on the title until live-status arrived.
     */
    function detachLocalGenerationForNavigation() {
        CuttleTurnGuard.bump(turnGeneration);
        CuttleStopState.markStreamDetached(stopState);
        try {
            if (activeEventSource) {
                activeEventSource.close();
                activeEventSource = null;
            }
        } catch (_) {}
        try {
            if (activeRequestController) {
                activeRequestController.abort();
                activeRequestController = null;
            }
        } catch (_) {}
        removeTypingIndicator();
        removeRemoteWaitingIndicator();
        // Owned by chat_generation.js — detach transition (drops the local
        // lock, invalidates pre-detach tokens); the page applies transport
        // teardown below and keeps the spinner/watch effects.
        const keepRunningId = CuttleChatGeneration.detachGeneration(generation).keepRunningId;
        clearStreamStatusPoll();
        // Server run continues — keep history spinner for the chat we left.
        if (keepRunningId) {
            setHistorySessionRunning(keepRunningId, true);
            // processMessage's pending waiter exits on suppress/nav-gen; poll in
            // the background so a real finish still chirps (without the old
            // false "painted" shortcut that fired on switch).
            recoveryController().watchDetachedSessionForCompletion(keepRunningId);
        }
        if (messageSyncTimer) scheduleNextMessageSync();
        inFlightUserMessage = null;
        sendDispatchGuard = false;
        const chatInput = document.getElementById('chatInput');
        const sendButton = document.getElementById('sendButton');
        const stopButton = document.getElementById('stopButton');
        if (chatInput) chatInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (stopButton) {
            stopButton.disabled = true;
            stopButton.style.display = 'none';
        }
        setWelcomeComposerEnabled(true);
    }

    /**
     * Shell is closing this iframe's EventSource before a src swap (Electron
     * freezes if the stream stays open). That is not Stop — the server run
     * continues. Mark the abort so the send catch does not persist
     * "Stopped generating."
     *
     * Teardown: this document is going away, so drop message sync too.
     * Pause: a sibling pane may be the one navigating; this chat can stay
     * on screen, so keep recovery polls and let the reply paint.
     */
    function releaseLocalStreamForShellPause(teardown) {
        CuttleStopState.markStreamDetached(stopState);
        try {
            if (activeRequestController) activeRequestController.abort();
        } catch (_) {}
        activeRequestController = null;
        try {
            if (activeEventSource) activeEventSource.close();
        } catch (_) {}
        activeEventSource = null;
        clearStreamStatusPoll();
        if (teardown) {
            stopMessageSync();
            return;
        }
        if (!messageSyncTimer) startMessageSync();
        else scheduleNextMessageSync();
    }

    /**
     * True when the *user* isn't looking at Cuttle. Android WebView iframes
     * often report document.hidden even while the app is on screen, which
     * used to freeze message-sync and leave follow-ups queued forever.
     */
    function pageIsBackgrounded() {
        try {
            if (window.parent && window.parent !== window) {
                return !!window.parent.document.hidden;
            }
        } catch (_) {}
        return !!document.hidden;
    }

    // Activity bubbles have no persisted message index or assistant reply.
    // Keep them visible without including them in record/navigation decisions.
    const CHAT_RECORD_SELECTOR =
        '.message:not(#typing-indicator):not(#typing-indicator-remote):not([data-stopped="true"])';

    /** Last real chat bubble in the open transcript (ignores typing placeholders). */
    function lastVisibleChatMessageEl() {
        const box = document.getElementById('chatMessages');
        if (!box) return null;
        const nodes = box.querySelectorAll(
            CHAT_RECORD_SELECTOR
        );
        return nodes.length ? nodes[nodes.length - 1] : null;
    }

    function messageElMatchesText(el, text) {
        if (!el || text == null) return false;
        const want = normalizeMessageContentForMatch(text);
        if (!want) return false;
        const got = normalizeMessageContentForMatch(
            el.dataset && el.dataset.rawContent != null ? el.dataset.rawContent : ''
        );
        return !!(got && (got === want || got.endsWith(want) || want.endsWith(got)));
    }

    /**
     * Assistant bubble that belongs to the in-flight turn (after that user
     * prompt). An older unstamped assistant must not count as "already painted"
     * or the chirp fires while the new reply stays invisible until refresh.
     *
     * Requires inFlightUserMessage. After chat switch, detach clears that text;
     * treating "any assistant in the (now different) DOM" as this turn used to
     * fire Reply ready / green dot while the agent was still working.
     */
    function assistantElAfterInFlightUser() {
        const wantUser = normalizeMessageContentForMatch(inFlightUserMessage);
        if (!wantUser) return null;
        const box = document.getElementById('chatMessages');
        if (!box) return null;
        const nodes = Array.from(box.querySelectorAll(
            CHAT_RECORD_SELECTOR
        ));
        let seenUser = false;
        let asst = null;
        for (const el of nodes) {
            if (el.classList.contains('user')) {
                if (messageElMatchesText(el, inFlightUserMessage)) {
                    seenUser = true;
                    asst = null;
                }
            } else if (seenUser && el.classList.contains('assistant')) {
                const raw = normalizeMessageContentForMatch(
                    el.dataset && el.dataset.rawContent != null ? el.dataset.rawContent : ''
                );
                if (raw) asst = el;
            }
        }
        return asst;
    }

    function turnAlreadyShowsAssistantReply(content) {
        const el = assistantElAfterInFlightUser();
        if (!el) return false;
        const raw = normalizeMessageContentForMatch(
            el.dataset && el.dataset.rawContent != null ? el.dataset.rawContent : ''
        );
        if (!raw) return false;
        if (content == null || !String(content).trim()) return true;
        // Exact match only. Suffix/fuzzy match used to treat a prior error /
        // partial bubble as "already painted" and skip addMessageToUI — chirp
        // with no new bubble until Electron refresh (CH-000384 after Flask reboot).
        const want = normalizeMessageContentForMatch(content);
        return !!(want && raw === want);
    }

    /** True when this pane is missing an assistant after the in-flight user. */
    function openTranscriptMissingThisTurnReply() {
        if (inFlightUserMessage && !assistantElAfterInFlightUser()) return true;
        const last = lastVisibleChatMessageEl();
        if (!last) return true;
        return last.classList.contains('user');
    }

    /**
     * True only when THIS turn already has an assistant after the in-flight
     * user prompt. A previous-turn assistant at the end of the transcript
     * must not count — that aborted pending collection and dropped the
     * activity bubble (CH-000199-3).
     */
    function thisTurnHasAssistantReply(messages) {
        if (inFlightUserMessage && turnAlreadyShowsAssistantReply()) return true;
        if (!Array.isArray(messages) || !messages.length) return false;
        const wantUser = normalizeMessageContentForMatch(inFlightUserMessage);
        if (wantUser) {
            let userIdx = -1;
            for (let i = messages.length - 1; i >= 0; i--) {
                const m = messages[i];
                if (!m || m.role !== 'user') continue;
                const got = normalizeMessageContentForMatch(m.content);
                if (got === wantUser || got.endsWith(wantUser) || wantUser.endsWith(got)) {
                    userIdx = i;
                    break;
                }
            }
            if (userIdx < 0) return false;
            for (let i = userIdx + 1; i < messages.length; i++) {
                const m = messages[i];
                if (
                    m
                    && m.role === 'assistant'
                    && String(m.content || '').trim()
                    && !isCancelledAgentText(m.content)
                ) {
                    return true;
                }
            }
            return false;
        }
        return transcriptEndsWithAssistant(messages);
    }

    function stampAssistantElFromServer(el, msg) {
        if (!el || !msg) return el;
        if (msg.id != null && msg.id !== '') el.dataset.messageId = String(msg.id);
        const raw = String(msg.content || '');
        el.dataset.rawContent = raw;
        let meta = msg.metadata;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
        }
        if (!meta || typeof meta !== 'object') meta = {};
        const queryId = msg.query_id || meta.query_id;
        if (queryId) el.dataset.queryId = String(queryId);
        if (msg.slash_command_failed) meta = Object.assign({}, meta, { slash_command_failed: true });
        if (assistantReplyLooksLikeError(raw, meta)) el.dataset.failed = '1';
        else delete el.dataset.failed;
        const contentEl = el.querySelector('.message-content');
        if (contentEl && raw.trim()) {
            const display = stripLegacyAgentChrome(raw);
            const shown = normalizeMessageContentForMatch(contentEl.innerText || '');
            const want = normalizeMessageContentForMatch(display);
            if (shown !== want) {
                contentEl.innerHTML = formatMessage(display);
                try { activateEnhancements(el); } catch (_) {}
            }
            transcriptNav.autoScrollChatToBottom();
        }
        const statusHeader = el.querySelector('.message-header-row');
        if (statusHeader) {
            statusHeader.querySelectorAll('.cuttle-status-chip').forEach(chip => chip.remove());
            statusHeader.insertAdjacentHTML('beforeend', CuttleChatMessages.renderAssistantStatusHtml(
                { routing_badge: msg.routing_badge || meta.routing_badge }, el.dataset.failed === '1', raw, escapeHtmlInline));
        }
        const usage = normalizeUsagePayload(msg.usage, meta) || normalizeUsagePayload(meta.usage, meta);
        if (usage) {
            try {
                el._cuttleUsage = usage;
            } catch (_) {}
            const wrap = el.querySelector('.message-content-wrapper');
            if (wrap) {
                let footer = wrap.querySelector('.message-footer');
                const usageHtml = getMessageUsageHtml(usage);
                if (footer) {
                    const existingUsage = footer.querySelector('.message-usage');
                    if (existingUsage) existingUsage.outerHTML = usageHtml;
                    else if (usageHtml) footer.insertAdjacentHTML('beforeend', usageHtml);
                } else if (usageHtml) {
                    const icons = wrap.querySelector('.message-footer-icons');
                    if (icons) {
                        const shell = document.createElement('div');
                        shell.className = 'message-footer';
                        icons.replaceWith(shell);
                        shell.appendChild(icons);
                        shell.insertAdjacentHTML('beforeend', usageHtml);
                    }
                }
            }
            try {
                const chipMeta = meta.slash_command;
                let agentHint = '';
                if (chipMeta && Array.isArray(chipMeta.chips) && chipMeta.chips[0]) {
                    agentHint = chipMeta.chips[0].prefix || chipMeta.chips[0].meta || '';
                }
                contextGauge.noteUsage(usage, agentHint || contextGauge.stickyAgentId());
            } catch (_) {}
        }
        if (meta.subagents) {
            try { subagentLaunchers.mount(el, meta.subagents); } catch (_) {}
        }
        return el;
    }

    /**
     * Drop leftover "generating" UI when the transcript already ends on an
     * assistant reply. Message sync samples live-status in parallel with
     * messages; a reply can land in history while that snapshot still says
     * active — which used to re-arm remote waiting / runningSessionIds and
     * queue the user's next send forever (drain only ran when the remote
     * indicator was present).
     */
    // Owned by chat_pending_result.js — thin adapter: the page builds the
    // snapshot from live state and applies the decided effects.
    function healStaleGeneratingState() {
        const decision = CuttleChatPendingResult.decideStaleHeal({
            stopped: stopState.userStopped,
            suppressed: stopState.waitingSuppressed,
            loading: generation.loading,
            hasEventSource: !!activeEventSource,
            hasRequest: !!activeRequestController,
            hasTrackedTurn: !!inFlightUserMessage,
            replyOnScreen: turnAlreadyShowsAssistantReply(),
            hasRemoteWait: !!document.getElementById('typing-indicator-remote'),
            sessionRunning: session.current() != null
                && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, session.current())),
            queueLength: followupController().getState().items.length,
        });
        if (decision.kind === 'finish') {
            finishLocalStreamFromServerSync();
            return true;
        }
        if (decision.kind === 'clear-remote') {
            removeRemoteWaitingIndicator();
            if (session.current()) setHistorySessionRunning(session.current(), false);
            const stopButton = document.getElementById('stopButton');
            if (stopButton) {
                stopButton.disabled = true;
                stopButton.style.display = 'none';
            }
            if (decision.drain) scheduleFollowupDrain(0);
            return true;
        }
        return false;
    }

    /** Server transcript ends on an assistant turn → treat live-status as idle. */
    function transcriptEndsWithAssistant(messages) {
        return CuttleChatMessages.transcriptEndsWithAssistant(messages);
    }

    /** Persisted Stop notice — survives refresh; in-memory stop flags do not. */
    function transcriptEndsWithGenerationStop(messages) {
        if (Array.isArray(messages)) {
            for (let i = messages.length - 1; i >= 0; i--) {
                const m = messages[i];
                if (!m) continue;
                const kind = m.kind || (m.metadata && m.metadata.kind) || '';
                if (kind === 'generation-stop') return true;
                if (m.role === 'assistant' || m.role === 'user') break;
            }
        }
        // Only a Stop notice after the last user/assistant bubble counts — an
        // older turn's notice must not hide a newer run started elsewhere.
        const box = document.getElementById('chatMessages');
        if (!box) return false;
        const rows = box.querySelectorAll(CHAT_RECORD_SELECTOR);
        for (let i = rows.length - 1; i >= 0; i--) {
            const el = rows[i];
            if (el.id === 'typing-indicator' || el.id === 'typing-indicator-remote') continue;
            if (el.classList.contains('system')) {
                if (el.dataset.kind === 'generation-stop') return true;
                continue;
            }
            if (el.classList.contains('user') || el.classList.contains('assistant')) break;
        }
        return false;
    }

    function renderAttachmentChips() {
        const rows = [
            document.getElementById('chatAttachChipsRow'),
            document.getElementById('welcomeAttachChipsRow'),
        ].filter(Boolean);
        for (const row of rows) {
            row.innerHTML = '';
            if (!pendingAttachments.length) {
                row.hidden = true;
                continue;
            }
            row.hidden = false;
            pendingAttachments.forEach((att, idx) => {
                const chip = document.createElement('span');
                const isImg = isImageAttachment(att);
                chip.className = 'attach-chip' + (isImg ? ' attach-chip--thumb' : '');
                chip.title = att.filename || 'file';
                if (isImg && att.url) {
                    const thumb = document.createElement('img');
                    thumb.className = 'attach-chip-thumb';
                    thumb.src = att.url;
                    thumb.alt = att.filename || 'image';
                    thumb.loading = 'lazy';
                    chip.appendChild(thumb);
                }
                const name = document.createElement('span');
                name.className = 'attach-chip-name';
                name.textContent = att.filename || 'file';
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'attach-chip-remove';
                remove.setAttribute('aria-label', 'Remove attachment');
                remove.textContent = '×';
                remove.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    pendingAttachments = CuttleChatAttachments.removeAttachmentAt(pendingAttachments, idx);
                    _savePendingAttachments();
                    renderAttachmentChips();
                });
                chip.appendChild(name);
                chip.appendChild(remove);
                row.appendChild(chip);
            });
        }
    }

    function clearPendingAttachments() {
        const sid = session.current();
        pendingAttachments = [];
        if (sid) _clearPersistedPending(sid);
        renderAttachmentChips();
    }

    function takePendingAttachments() {
        // Owned by chat_attachments.js — copy-then-clear decision.
        const plan = CuttleChatAttachments.takePendingAttachments(pendingAttachments);
        clearPendingAttachments();
        return plan.taken;
    }

    // Owned by chat_attachments.js — thin adapter.
    function isImageAttachment(att) {
        return CuttleChatAttachments.isImageAttachment(att);
    }

    // Owned by chat_attachments.js — thin adapter.
    function normalizeAttachmentList(list) {
        return CuttleChatAttachments.normalizeAttachmentList(list);
    }

    /** Strip trailing `[Attached: …]` note used in stored history text. */
    // Owned by chat_attachments.js — thin adapter.
    function stripAttachedNote(content) {
        return CuttleChatAttachments.stripAttachedNote(content);
    }

    /** Parse `[Attached: a.png, b.pdf]` names from stored message text. */
    // Owned by chat_attachments.js — thin adapter.
    function parseAttachedFilenames(content) {
        return CuttleChatAttachments.parseAttachedFilenames(content);
    }

    /**
     * Infer attachment refs from history text when metadata is missing
     * (older messages). Tries current session folder, then anon.
     */
    // Owned by chat_attachments.js — DOM/state gather, domain decides.
    function inferAttachmentsFromContent(content) {
        return CuttleChatAttachments.inferAttachmentsFromContent({
            content,
            sessionId: session.current(),
        });
    }

    function buildMessageAttachmentsHtml(attachments) {
        const list = normalizeAttachmentList(attachments);
        if (!list.length) return '';
        const parts = list.map((att) => {
            const name = escapeHtmlInline(att.filename || 'file');
            const url = String(att.url || '').trim();
            const safeUrl = escapeHtmlInline(url).replace(/"/g, '&quot;');
            if (CuttleChatAttachments.messageAttachmentKind(att) === 'thumb') {
                return (
                    '<button type="button" class="msg-attach-thumb cuttle-media-thumb" data-src="' +
                    safeUrl +
                    '" data-kind="image" data-title="' +
                    name +
                    '" data-download="' +
                    escapeHtmlInline(CuttleChatMedia.mediaDownloadUrl(url)).replace(/"/g, '&quot;') +
                    '" title="' +
                    name +
                    '">' +
                    '<img src="' +
                    safeUrl +
                    '" alt="' +
                    name +
                    '" loading="lazy" onerror="this.closest(\'.msg-attach-thumb\')?.remove()">' +
                    '</button>'
                );
            }
            if (CuttleChatAttachments.messageAttachmentKind(att) === 'link') {
                return (
                    '<a class="msg-attach-file" href="' +
                    safeUrl +
                    '" target="_blank" rel="noopener" title="' +
                    name +
                    '">' +
                    '<span class="msg-attach-file-icon" aria-hidden="true">📄</span>' +
                    '<span class="msg-attach-file-name">' +
                    name +
                    '</span></a>'
                );
            }
            return (
                '<span class="msg-attach-file" title="' +
                name +
                '">' +
                '<span class="msg-attach-file-icon" aria-hidden="true">📄</span>' +
                '<span class="msg-attach-file-name">' +
                name +
                '</span></span>'
            );
        });
        return '<div class="msg-attachments">' + parts.join('') + '</div>';
    }

    // Owned by chat_attachments.js — thin adapter.
    function formatMessageWithAttachments(message, attachments) {
        return CuttleChatAttachments.formatMessageWithAttachments(message, attachments);
    }

    // Owned by chat_attachments.js — DOM/state gather, domain decides.
    async function uploadChatFiles(fileList) {
        const files = Array.from(fileList || []).filter(Boolean);
        if (!files.length) return;
        const form = new FormData();
        form.append('session_id', authSessionIdForRequest() || session.current() || 'anon');
        for (const f of files) form.append('files', f, f.name);
        const resp = await fetch('/api/upload', {
            method: 'POST',
            credentials: 'include',
            body: form,
        });
        let data = null;
        try {
            data = await resp.json();
        } catch (_) {
            data = null;
        }
        const plan = CuttleChatAttachments.uploadResultPlan({
            ok: resp.ok,
            status: resp.status,
            data,
        });
        if (!plan.ok) {
            (window.showToast || function (m) { alert(m); })(plan.error, 'error');
            return;
        }
        pendingAttachments = CuttleChatAttachments.addAttachmentsToPending(
            pendingAttachments, plan.items);
        _savePendingAttachments();
        renderAttachmentChips();
    }

    function wireAttachControls() {
        const pairs = [
            ['attachButton', 'chatFileInput'],
            ['welcomeAttachButton', 'welcomeFileInput'],
        ];
        for (const [btnId, inputId] of pairs) {
            const btn = document.getElementById(btnId);
            const input = document.getElementById(inputId);
            if (!btn || !input) continue;
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                input.click();
            });
            input.addEventListener('change', async () => {
                try {
                    await uploadChatFiles(input.files);
                } finally {
                    input.value = '';
                }
            });
        }
    }

    /** Wand button state per composer: lets a second click undo the rewrite. */
    const enhanceState = CuttleChatComposer.createEnhanceState();
    let enhanceInFlight = false;

    function recentMessagesForEnhance(limit = 4) {
        const box = document.getElementById('chatMessages');
        if (!box) return [];
        const nodes = Array.from(box.querySelectorAll(CHAT_RECORD_SELECTOR + ':not(.system)')).slice(-limit);
        const out = [];
        for (const el of nodes) {
            const content = el.querySelector('.message-content');
            const text = ((content && content.textContent) || '').trim();
            if (!text) continue;
            out.push({
                role: el.classList.contains('user') ? 'user' : 'assistant',
                content: text.slice(0, 2000),
            });
        }
        return out;
    }

    async function enhanceComposerPrompt(key, btn, textarea) {
        const toast = window.showToast || function () {};
        // Owned by chat_composer.js — click decision (busy / revert / empty / request).
        const click = CuttleChatComposer.enhanceClickPlan({
            state: enhanceState[key],
            currentValue: textarea.value,
            inFlight: enhanceInFlight,
        });
        if (click.action === 'busy') return;

        if (click.action === 'revert') {
            textarea.value = click.text;
            enhanceState[key] = null;
            btn.classList.remove('is-enhanced');
            btn.title = 'Enhance prompt (rewrite with AI)';
            if (key === 'chat') autoResizeTextarea(); else autoResizeWelcomeTextarea();
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
            toast('Reverted to your original prompt', 'info');
            return;
        }

        if (click.action === 'empty') {
            toast('Type a prompt first', 'info');
            textarea.focus();
            return;
        }

        const current = textarea.value || '';
        const prompt = click.prompt;
        enhanceInFlight = true;
        btn.classList.add('is-busy');
        btn.disabled = true;
        try {
            const resp = await fetch('/api/prompt/enhance', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(CuttleChatComposer.enhanceRequestPayload({
                    prompt,
                    projectName: (currentProject && currentProject.name) || '',
                    context: recentMessagesForEnhance(),
                })),
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (_) {
                data = null;
            }
            // Owned by chat_composer.js — response decision (error / no-change / apply).
            const plan = CuttleChatComposer.enhanceResponsePlan({
                ok: resp.ok,
                status: resp.status,
                data,
                originalPrompt: prompt,
            });
            if (plan.action === 'error') {
                toast(plan.error, 'error');
                return;
            }
            if (plan.action === 'no-change') {
                toast('Prompt already looks clear', 'info');
                return;
            }
            const enhanced = plan.enhanced;
            enhanceState[key] = { original: current, enhanced };
            textarea.value = enhanced;
            btn.classList.add('is-enhanced');
            btn.title = 'Undo enhancement (restore your original prompt)';
            if (key === 'chat') autoResizeTextarea(); else autoResizeWelcomeTextarea();
            textarea.focus();
            textarea.setSelectionRange(enhanced.length, enhanced.length);
            toast(`Prompt enhanced${plan.source ? ' (' + plan.source + ')' : ''} — click the wand to undo`, 'success');
        } catch (e) {
            toast(`Enhance failed: ${e && e.message ? e.message : e}`, 'error');
        } finally {
            enhanceInFlight = false;
            btn.classList.remove('is-busy');
            btn.disabled = false;
        }
    }

    function wireEnhanceControls() {
        const triples = [
            ['enhanceButton', 'chatInput', 'chat'],
            ['welcomeEnhanceButton', 'welcomeChatInput', 'welcome'],
        ];
        for (const [btnId, inputId, key] of triples) {
            const btn = document.getElementById(btnId);
            const textarea = document.getElementById(inputId);
            if (!btn || !textarea) continue;
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                enhanceComposerPrompt(key, btn, textarea);
            });
            // Editing after a rewrite means the undo target is stale.
            textarea.addEventListener('input', () => {
                // Owned by chat_composer.js — undo staleness decision.
                if (CuttleChatComposer.enhanceInputInvalidated(enhanceState[key], textarea.value)) {
                    enhanceState[key] = null;
                    btn.classList.remove('is-enhanced');
                    btn.title = 'Enhance prompt (rewrite with AI)';
                }
            });
        }
    }

    /** Ids of enabled experimental flags (empty when the kill switch is on or the fetch fails). */
    async function fetchEnabledExperimentalFlags() {
        try {
            const r = await fetch('/api/experimental/flags', { credentials: 'include', cache: 'no-store' });
            const d = await r.json();
            if (r.ok && d && d.success && !d.kill_switch) {
                return new Set((d.flags || []).filter(f => f.enabled).map(f => f.id));
            }
        } catch (_) {}
        return new Set();
    }

    /** Composer controls tagged data-experimental-flag stay hidden unless their flag is on. */
    async function syncExperimentalComposerControls() {
        const controls = document.querySelectorAll('[data-experimental-flag]');
        if (!controls.length) return;
        const enabled = await fetchEnabledExperimentalFlags();
        controls.forEach((el) => {
            el.hidden = !enabled.has(el.getAttribute('data-experimental-flag'));
        });
    }

    const sessionPrefsStore = CuttleSessionPrefs.create(localStorage);
    const sessionMutations = CuttleChatMutations.create();
    // Server attention persistence + history/title DOM projection. The page
    // injects its capabilities; the controller never reads page globals.
    const serverAttention = CuttleChatAttention.create({
        prefs: sessionPrefsStore,
        id: () => Date.now() + ':' + Math.random().toString(36).slice(2),
        request: async (sid, data) => {
            const response = await fetch('/api/auth/sessions/' + sid + '/attention', {
                method: 'PUT', credentials: 'include', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data),
            });
            if (!response.ok) throw new Error('Attention sync HTTP ' + response.status);
            return response.json();
        },
        changed: () => { syncHistoryUnreadIndicators(); scheduleChatActivityBroadcast(); },
        // ---- history/title projection capabilities ----
        doc: document,
        escapeHtmlInline,
        sessionIdsEqual,
        openPaneSnapshot: () => localOpenPanes,
        sessionObjFor: (sid) => {
            if (isAuthMode()) return findAuthServerSession(sid);
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                return sessions[sid] || null;
            } catch (_) {
                return null;
            }
        },
        attentionKindFor: (sid, sessionObj) => sessionHistoryAttentionKind(sid, sessionObj),
        sessionShowsHistorySpinner: (sid) => sessionShowsHistorySpinner(sid),
        syncSubagentAttention: () => historyPanel.syncHistorySubagentAttention(),
        scheduleBroadcast: () => scheduleChatActivityBroadcast(),
        currentSessionId: () => session.current(),
        attentionPrefsFor,
        attentionActive: () => chatAttentionActive(),
        attentionIsError: () => chatAttentionIsError,
        hasActiveFollowup: (sid) => sessionHasActiveFollowup(sid, null),
        hasPausedFollowup: (sid) => sessionHasPausedFollowup(sid, null),
    });

    function attentionPrefsFor(sessionId, sessionObj) {
        const key = toAuthDbSessionId(sessionId);
        const obj = sessionObj || (isAuthMode() ? findAuthServerSession(sessionId) : null);
        if (obj && obj.attention) serverAttention.accept(key, obj.attention);
        return serverAttention.get(key) || getSessionPrefs(sessionId);
    }

    function acceptSessionAttention(sessionId, snapshot) {
        if (!snapshot) return;
        serverAttention.accept(toAuthDbSessionId(sessionId), snapshot);
        syncHistoryUnreadIndicators();
    }

    /** Global default sticky slash agents (e.g. /cursor) applied to new chats. */
    const STARRED_SLASH_STORAGE_KEY = 'cuttleStarredSlashCommands';
    const STARRED_PROJECT_STORAGE_KEY = 'cuttleStarredProject';

    /** Device preferences stay owned/cached by CuttleSessionPrefs. */
    function invalidateSessionPrefsMapCache() {
        sessionPrefsStore.invalidate();
    }

    function readSessionPrefsMap() {
        return sessionPrefsStore.map();
    }

    // Guarded: node test harnesses eval page slices with a minimal window
    // stub (no addEventListener); browsers always take this branch.
    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
        window.addEventListener('storage', (event) => {
            if (event && CuttleSessionPrefs.isKey(event.key)) {
                invalidateSessionPrefsMapCache();
            }
        });
    }

    function getSessionPrefs(sessionId) {
        return sessionPrefsStore.get(sessionId);
    }

    function updateSessionPrefs(sessionId, patch) {
        sessionPrefsStore.update(sessionId, patch);
    }

    function clearSessionPrefs(sessionId) {
        sessionPrefsStore.remove(sessionId);
    }

    function migrateSessionPrefs(oldId, newId) {
        if (oldId == null || newId == null) return;
        if (String(oldId) === String(newId)) return;
        const map = readSessionPrefsMap();
        const prev = map[String(oldId)];
        if (!prev) return;
        const existing =
            map[String(newId)] && typeof map[String(newId)] === 'object'
                ? map[String(newId)]
                : {};
        // Merge so a partial prefs row under the new id cannot drop sticky agent /
        // project fields that only lived under the temporary local id.
        const merged = Object.assign({}, prev, existing);
        const prevSticky = Array.isArray(prev.stickyChips) ? prev.stickyChips : [];
        const existingSticky = Array.isArray(existing.stickyChips) ? existing.stickyChips : [];
        if (!existingSticky.length && prevSticky.length) {
            merged.stickyChips = prevSticky;
        }
        if (
            (existing.projectId === undefined || existing.projectId === null) &&
            prev.projectId !== undefined &&
            prev.projectId !== null
        ) {
            merged.projectId = prev.projectId;
            merged.projectPath = prev.projectPath || '';
            merged.projectName = prev.projectName || '';
        }
        if (existing.hasUnread === undefined && prev.hasUnread) {
            merged.hasUnread = true;
        }
        if (existing.unreadIsError === undefined && prev.unreadIsError) {
            merged.unreadIsError = true;
        }
        if (
            (existing.lastReadAt == null || existing.lastReadAt === undefined) &&
            prev.lastReadAt != null
        ) {
            merged.lastReadAt = prev.lastReadAt;
        }
        updateSessionPrefs(newId, merged);
        clearSessionPrefs(oldId);
    }

    function markChatSessionUnread(sessionId, opts) {
        if (sessionId == null || sessionId === '') return;
        const isError = !!(opts && opts.isError);
        if (isAuthMode() && /^\d+$/.test(String(toAuthDbSessionId(sessionId)))) {
            if (opts && opts.manual) serverAttention.mark(toAuthDbSessionId(sessionId), {unread: true});
            else if (inAppShell) window.parent.postMessage({type: 'cuttle-activity-refresh'}, '*');
            else refreshChatHistoryList();
        } else updateSessionPrefs(sessionId, { hasUnread: true, unreadIsError: isError });
        try { scheduleChatActivityBroadcast(); } catch (_) {}
    }

    // Another pane (or the shell, for chats that finish in a background
    // space) changed unread flags — repaint this pane's history dots.
    window.addEventListener('storage', (e) => {
        if (!e || !CuttleSessionPrefs.isKey(e.key)) return;
        try { syncHistoryUnreadIndicators(); } catch (_) {}
    });

    /** Manual "Mark as unread" while still viewing — don't let focus/visibility clear it. */
    let _manualUnreadHoldId = null;

    function markChatSessionRead(sessionId) {
        if (sessionId == null || sessionId === '') return;
        if (CuttleChatActivity.manualHoldBlocksRead({ holdId: _manualUnreadHoldId, sessionId })) {
            return;
        }
        if (isAuthMode() && /^\d+$/.test(String(toAuthDbSessionId(sessionId)))) {
            // A hidden page or a response for a previous chat hasn't been viewed.
            if (!sessionIdsEqual(session.current(), sessionId) || pageIsBackgrounded()) return;
            const through = lastSeenServerMessageId;
            serverAttention.mark(toAuthDbSessionId(sessionId), {through_id: through});
        }
        updateSessionPrefs(sessionId, {
            hasUnread: false,
            unreadIsError: false,
            lastReadAt: Date.now(),
        });
        try { scheduleChatActivityBroadcast(); } catch (_) {}
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function releaseManualUnreadHoldIfLeaving(nextSessionId) {
        _manualUnreadHoldId = CuttleChatActivity.releaseManualHold({
            holdId: _manualUnreadHoldId,
            currentSessionId: session.current(),
            nextSessionId,
        });
    }

    /** Prefs flag only — ignores "currently viewing" so open-chat handoff can keep the title dot. */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionPrefsHasUnreadFlag(sessionId) {
        if (sessionId == null || sessionId === '') return false;
        return CuttleChatActivity.prefsHasUnreadFlag(attentionPrefsFor(sessionId));
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionPrefsUnreadIsError(sessionId) {
        if (sessionId == null || sessionId === '') return false;
        return CuttleChatActivity.prefsUnreadIsError(attentionPrefsFor(sessionId));
    }

    /**
     * Failed assistant reply for attention dots (red). Uses slash_command_failed
     * metadata when present; otherwise common error bubble text.
     */
    // Owned by chat_activity.js — thin adapter.
    function assistantReplyLooksLikeError(content, meta) {
        return CuttleChatActivity.assistantReplyLooksLikeError(content, meta);
    }

    function beginSuppressChatAttention() {
        _suppressChatAttention += 1;
    }

    function endSuppressChatAttention() {
        _suppressChatAttention = Math.max(0, _suppressChatAttention - 1);
    }

    /** Open-chat attention snapshot (plain data for the activity domain). */
    function snapshotChatAttention() {
        return {
            unseenBelow: chatUnseenBelow,
            needsAck: chatNeedsAck,
            isError: chatAttentionIsError,
            viewportActivated: chatViewportActivated,
        };
    }

    function applyChatAttention(next) {
        chatUnseenBelow = !!next.unseenBelow;
        chatNeedsAck = !!next.needsAck;
        chatAttentionIsError = !!next.isError;
        chatViewportActivated = next.viewportActivated !== false;
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function chatAttentionActive() {
        return CuttleChatActivity.attentionIsActive(snapshotChatAttention());
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function resetChatAttentionState(opts) {
        applyChatAttention(CuttleChatActivity.attentionAfterReset(snapshotChatAttention(), {
            needsAck: !!(opts && opts.needsAck),
            isError: !!(opts && opts.isError),
        }));
        syncChatAttentionIndicators();
    }

    /**
     * Finalized assistant reply only — not mid-stream / generating.
     *
     * Green (or red-on-error) jump glow → content landed below the fold.
     * Title unread dot → unseen below, OR in-view but viewport never activated.
     * Does NOT re-nag after every reply once the viewport is already active.
     */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function noteFinalizedAssistantAttention(opts) {
        if (_suppressChatAttention > 0) return;
        applyChatAttention(CuttleChatActivity.attentionAfterFinalized(snapshotChatAttention(), {
            isError: !!(opts && opts.isError),
            scrolledAway: !transcriptNav.isFollowingLatest() || transcriptNav.isChatScrolledUp(140),
        }));
        syncChatAttentionIndicators();
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function clearChatUnseenBelow() {
        if (!chatUnseenBelow) {
            transcriptNav.updateJumpToBottomVisibility();
            return;
        }
        applyChatAttention(CuttleChatActivity.attentionAfterClearUnseen(snapshotChatAttention()));
        syncChatAttentionIndicators();
    }

    /** Click in transcript or typing in composer acknowledges the open chat. */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function activateChatViewport() {
        const hadAck = chatNeedsAck;
        applyChatAttention(CuttleChatActivity.attentionAfterActivate(snapshotChatAttention()));
        if (!hadAck) {
            updateSessionTitleUnreadDot();
            return;
        }
        syncChatAttentionIndicators();
    }

    // Slice 3A commit 4: title-level unread dot projection now lives in the
    // CuttleChatAttention controller (chat_attention.js); the page delegates.
    function updateSessionTitleUnreadDot() {
        return serverAttention.updateSessionTitleUnreadDot();
    }

    function updateSessionTitleRunningIcon() {
        const icon = document.getElementById('chatSessionRunningIcon');
        const wrap = document.getElementById('chatSessionTitle');
        if (!icon) return;
        const running = !!(
            wrap
            && !wrap.hidden
            && session.current() != null
            && sessionShowsHistorySpinner(session.current())
        );
        icon.hidden = !running;
        if (wrap) wrap.classList.toggle('is-running', running);
    }

    function syncChatAttentionIndicators() {
        transcriptNav.updateJumpToBottomVisibility();
        updateSessionTitleUnreadDot();
        try { scheduleChatActivityBroadcast(); } catch (_) {}
    }

    function setupChatAttentionListeners() {
        const messages = document.getElementById('chatMessages');
        if (messages && !messages.dataset.attentionWired) {
            messages.dataset.attentionWired = '1';
            messages.addEventListener('pointerdown', () => {
                activateChatViewport();
            }, { passive: true });
        }
        const input = document.getElementById('chatInput');
        if (input && !input.dataset.attentionWired) {
            input.dataset.attentionWired = '1';
            input.addEventListener('input', () => {
                activateChatViewport();
            });
            input.addEventListener('keydown', () => {
                activateChatViewport();
            });
        }
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionHasUnread(sessionId, sessionObj) {
        return CuttleChatActivity.sessionHasUnread({
            sessionId,
            currentSessionId: session.current(),
            backgrounded: pageIsBackgrounded(),
            prefs: attentionPrefsFor(sessionId, sessionObj),
            sessionObj,
        });
    }

    // Owned by chat_activity.js — thin adapter.
    function parseFollowupQueue(raw) {
        return CuttleChatActivity.parseFollowupQueue(raw);
    }

    /** Resolve followup_queue for a session (open chat uses the live controller state). */
    function followupQueueForSession(sessionId, sessionObj) {
        if (sessionId == null || sessionId === '') return [];
        if (session.current() != null && sessionIdsEqual(session.current(), sessionId)) {
            return followupController().getState().items.slice();
        }
        let obj = sessionObj;
        if (!obj && isAuthMode()) {
            obj = findAuthServerSession(sessionId);
        }
        if (!obj && !isAuthMode()) {
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                obj = sessions[sessionId] || sessions[String(sessionId)] || null;
            } catch (_) {}
        }
        if (!obj) return [];
        return parseFollowupQueue(
            obj.followup_queue != null ? obj.followup_queue : obj.followups
        );
    }

    /** True when this chat has at least one paused queued prompt. */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionHasPausedFollowup(sessionId, sessionObj) {
        return CuttleChatActivity.queueHasPaused(
            followupQueueForSession(sessionId, sessionObj));
    }

    /** True when this chat has at least one active (unpaused) queued prompt. */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionHasActiveFollowup(sessionId, sessionObj) {
        return CuttleChatActivity.queueHasActive(
            followupQueueForSession(sessionId, sessionObj));
    }

    /** Last assistant message on a session object (for unread-error fallback). */
    // Owned by chat_activity.js — thin adapter.
    function lastAssistantMessageFromSessionObj(sessionObj) {
        return CuttleChatActivity.lastAssistantMessageFromSessionObj(sessionObj);
    }

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionHasUnreadError(sessionId, sessionObj) {
        return CuttleChatActivity.sessionHasUnreadError({
            sessionId,
            currentSessionId: session.current(),
            backgrounded: pageIsBackgrounded(),
            prefs: attentionPrefsFor(sessionId, sessionObj),
            sessionObj,
        });
    }

    /**
     * Attention kind for history dots (priority high → low):
     * error (red) > unread (green) > queued/active (amber) > paused (yellow).
     * Extends the CH-000439-14 legend (paused was amber; active queue takes amber now).
     */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function sessionHistoryAttentionKind(sessionId, sessionObj) {
        return CuttleChatActivity.sessionHistoryAttentionKind({
            sessionId,
            currentSessionId: session.current(),
            backgrounded: pageIsBackgrounded(),
            prefs: attentionPrefsFor(sessionId, sessionObj),
            sessionObj,
            queue: followupQueueForSession(sessionId, sessionObj),
        });
    }

    function projectFieldsFromServerSession(serverSession) {
        return CuttleChatProject.projectFieldsFromServerSession(serverSession);
    }

    function findAuthServerSession(sessionId) {
        if (sessionId == null) return null;
        if (
            lastHydratedSessionProject
            && sessionIdsEqual(lastHydratedSessionProject.id, sessionId)
        ) {
            return lastHydratedSessionProject;
        }
        if (!isAuthMode()) return null;
        try {
            const serverSessions = (window.CuttleAuth && window.CuttleAuth.getSessions
                && window.CuttleAuth.getSessions()) || [];
            return serverSessions.find((x) => sessionIdsEqual(x.id, sessionId)) || null;
        } catch (_) {
            return null;
        }
    }

    function authSessionListHas(sessionId) {
        if (!isAuthMode() || sessionId == null) return false;
        try {
            const serverSessions = (window.CuttleAuth && window.CuttleAuth.getSessions
                && window.CuttleAuth.getSessions()) || [];
            return serverSessions.some((x) => sessionIdsEqual(x.id, sessionId));
        } catch (_) {
            return false;
        }
    }

    function resolveSessionProjectInfo(sessionId, sessionObj) {
        const prefs = getSessionPrefs(sessionId);
        let id = null;
        let name = '';
        let path = '';
        // Only treat as "stored" when project fields exist. Unread-only prefs
        // (or explicit null/empty from a pre-persist race) should not block the
        // default-project fallback used by reconcileChatProject().
        const serverProj = projectFieldsFromServerSession(
            (sessionObj && (sessionObj.project_id != null || sessionObj.project_path || sessionObj.project_name))
                ? sessionObj
                : findAuthServerSession(sessionId)
        );
        // Auth DB is the durable copy. Local prefs lag when an agent/API moves
        // the chat to another project, so they must not beat a populated server row.
        if (serverProj) {
            id = serverProj.projectId != null ? serverProj.projectId : null;
            name = serverProj.projectName || '';
            path = serverProj.projectPath || '';
        } else if (prefs && (prefs.projectId != null || prefs.projectName || prefs.projectPath)) {
            id = prefs.projectId != null ? prefs.projectId : null;
            name = prefs.projectName || '';
            path = prefs.projectPath || '';
        }
        if (
            id == null && !name && !path
            && sessionObj
            && (sessionObj.projectId != null || sessionObj.projectName || sessionObj.projectPath)
        ) {
            id = sessionObj.projectId != null ? sessionObj.projectId : null;
            name = sessionObj.projectName || '';
            path = sessionObj.projectPath || '';
        }
        if ((!name || !path) && id != null) {
            const p = findProjectById(id);
            if (p) {
                name = name || p.name || '';
                path = path || p.path || '';
            }
        }
        if (!name && path) {
            const parts = String(path).split(/[\\/]/);
            name = parts[parts.length - 1] || path;
        }
        if (id == null && !name && !path) {
            // Chat UI already falls back to the Cuttle default; history used to
            // dump these under "No project" because auth sessions never persist
            // project_id server-side — only local session prefs do.
            const def = findDefaultProject();
            if (def) {
                const defName = def.name || 'Cuttle';
                const defPath = def.path || '';
                const defId = def.id != null ? def.id : null;
                const key = defId != null
                    ? `id:${defId}`
                    : (defPath ? `path:${defPath}` : `name:${defName}`);
                return { key, id: defId, name: defName, path: defPath };
            }
            return { key: '__none__', id: null, name: 'No project', path: '' };
        }
        const key = id != null ? `id:${id}` : (path ? `path:${path}` : `name:${name}`);
        return { key, id, name: name || 'Project', path };
    }

    function sessionHasStoredProject(sessionId, sessionObj) {
        if (projectFieldsFromServerSession(sessionObj || findAuthServerSession(sessionId))) {
            return true;
        }
        const prefs = getSessionPrefs(sessionId);
        if (prefs && (prefs.projectId != null || prefs.projectName || prefs.projectPath)) {
            return true;
        }
        if (
            sessionObj
            && (sessionObj.projectId != null || sessionObj.projectName || sessionObj.projectPath)
        ) {
            return true;
        }
        return false;
    }

    function isChatSessionStarred(sessionId) {
        if (sessionId == null) return false;
        if (isTerminalSessionId(sessionId)) {
            const map = readTerminalSessionRegistry();
            return !!(map[String(sessionId)] && map[String(sessionId)].starred);
        }
        if (isAuthMode()) {
            const serverSessions = (window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [];
            const s = serverSessions.find((x) => sessionIdsEqual(x.id, sessionId));
            return !!(s && Number(s.starred));
        }
        try {
            const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
            const s = sessions[sessionId] || sessions[String(sessionId)];
            return !!(s && s.starred);
        } catch (_) {
            return false;
        }
    }

    function getChatSessionDisplayTitle(sessionId) {
        if (sessionId == null) return 'Chat';
        const key = String(canonicalizeChatSessionId(sessionId));
        const authKey = String(toAuthDbSessionId(sessionId) || '');
        const cached = _sessionTitleById[key] || (authKey && _sessionTitleById[authKey]);
        if (cached) return cached;
        if (isAuthMode()) {
            const serverSessions = (window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [];
            const s = serverSessions.find((x) => String(x.id) === String(sessionId)
                || String(x.id) === String(toAuthDbSessionId(sessionId)));
            if (s && s.session_name) {
                rememberChatSessionTitle(sessionId, s.session_name);
                return s.session_name;
            }
        } else {
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                const s = sessions[sessionId];
                if (s && s.title) {
                    rememberChatSessionTitle(sessionId, s.title);
                    return s.title;
                }
            } catch (_) {}
        }
        return 'Chat';
    }

    /** True when the open transcript is this session (not a backgrounded turn). */
    function isViewingSession(sessionId) {
        return sessionId != null
            && session.current() != null
            && sessionIdsEqual(session.current(), sessionId);
    }

    /**
     * Local generation is tied to localGeneratingSessionId. After the user
     * switches chats, isLoading can still be true for the *previous* chat —
     * recovery/paint must not treat that as "this transcript is generating."
     */
    // Owned by chat_generation.js — thin adapter over the owned lock.
    function isLoadingThisSession() {
        return CuttleChatGeneration.isLoadingForSession(
            generation, (sid) => isViewingSession(sid)
        );
    }

    /** Mark unread + toast when a reply arrives while the user isn't looking at that chat. */
    const _chirpedReadyAtBySession = Object.create(null);
    const completionNotificationPendingId = 'pending:' + Date.now() + ':' + Math.random().toString(36).slice(2);
    const CHIRP_SESSION_COOLDOWN_MS = 8000;

    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function notifyAssistantResponseReady(sessionId, opts = {}) {
        const sid = sessionId != null ? String(sessionId) : null;
        if (!sid) return;
        const isError = !!(opts && opts.isError);
        if (window.CuttleCompletionNotifications) {
            window.CuttleCompletionNotifications.broker().finishTurn(sid, isError ? 'failed' : 'done',
                (opts && opts.title) || getChatSessionDisplayTitle(sid)).catch(() => {});
        }
        // One chirp per session completion — blocks processMessage + sync double-fire
        // (and multi-pane sync) from stacking two full chirps back-to-back.
        const now = Date.now();
        const plan = CuttleChatActivity.responseReadyPlan({
            sessionId: sid,
            currentSessionId: session.current(),
            backgrounded: pageIsBackgrounded(),
            lastChirpAt: _chirpedReadyAtBySession[sid] || 0,
            nowMs: now,
            cooldownMs: CHIRP_SESSION_COOLDOWN_MS,
            isError,
            fromSync: !!(opts && opts.fromSync),
        });
        if (plan.chirp) {
            _chirpedReadyAtBySession[sid] = now;
            try {
                if (typeof window.playCuttleCompletionChirp === 'function') {
                    window.playCuttleCompletionChirp();
                } else if (window.parent && window.parent !== window) {
                    window.parent.postMessage({ type: 'cuttle-completion-chirp' }, '*');
                }
            } catch (_) {}
        }
        if (plan.viewingThis) {
            markChatSessionRead(sid);
            syncHistoryUnreadIndicators();
            // Green/red unread / jump glow — only once the reply is finalized.
            noteFinalizedAssistantAttention({ isError });
            schedulePendingChangesRefresh(800);
            // Chirp can fire from processMessage / sync even when the pane never
            // painted the bubble (dropped SSE after Flask restart, false
            // "already painted", etc.). Pull history so the open pane heals
            // without requiring an Electron refresh.
            if (!opts.fromSync && openTranscriptMissingThisTurnReply()) {
                try {
                    window.CuttleNetDebug && window.CuttleNetDebug.event(
                        'chirp-heal-missing-bubble',
                        'session=' + sid
                    );
                } catch (_) {}
                Promise.resolve()
                    .then(() => syncSessionMessagesFromServer())
                    .catch(() => {});
            }
            chatVoice.onGenerationEnded({ isError });
            return;
        }
        markChatSessionUnread(sid, { isError });
        // Avoid duplicate "Reply ready" toasts from sync shortly after local notify.
        if (plan.toast) {
            const title = (opts && opts.title) || getChatSessionDisplayTitle(sid);
            (window.showToast || function () {})(
                (isError ? 'Reply failed — ' : 'Reply ready — ') + title,
                isError ? 'error' : 'success',
                {
                    unread: true,
                    sessionId: sid,
                    kind: 'chat-response',
                }
            );
        }
        syncHistoryUnreadIndicators();
        // Soft refresh titles/grouping without wiping search or filter chips.
        historyPanel.reloadKeepingSearch();
    }

    // Slice 3A commit 4: the history attention icon markup, row/section/title
    // projection and the open-pane badge now live in the CuttleChatAttention
    // controller (chat_attention.js). The page keeps the pane-snapshot helpers
    // (the `cuttle-open-panes` postMessage protocol stays here) and thin
    // delegating wrappers so page call sites and injected deps are unchanged.

    function applyLocalOpenPanes(panes) {
        localOpenPanes = Array.isArray(panes) ? panes.filter((p) => p && p.sessionId) : [];
        serverAttention.syncHistoryOpenPaneIndicators();
    }

    function snapshotLocalOpenPanes() {
        if (inAppShell) {
            try {
                if (window.parent && window.parent !== window && typeof window.parent.getOpenPaneSessions === 'function') {
                    return window.parent.getOpenPaneSessions() || [];
                }
            } catch (_) {}
            return localOpenPanes;
        }
        if (session.current() != null && session.current() !== '') {
            return [{ sessionId: String(session.current()), pane: 1, kind: 'chat', focused: true }];
        }
        return [];
    }

    function refreshLocalOpenPanes() {
        localOpenPanes = snapshotLocalOpenPanes();
        if (inAppShell && (!localOpenPanes || !localOpenPanes.length)) {
            try {
                window.parent.postMessage({ type: 'cuttle-open-panes-request' }, '*');
            } catch (_) {}
        }
    }

    function openPaneEntriesForSession(sessionId) {
        if (sessionId == null || sessionId === '') return [];
        return localOpenPanes.filter((p) => sessionIdsEqual(p.sessionId, sessionId));
    }

    function syncHistoryUnreadIndicators() {
        return serverAttention.syncHistoryUnreadIndicators();
    }


    /** User-sent prompts for ↑/↓ recall (newest at end). Single browse
        session owned by chat_prompt_history.js; the page holds this
        instance plus storage/session/DOM/slash IO. */
    const PROMPT_HISTORY_STORAGE_KEY = 'cuttlePromptHistoryBySession';
    const promptHistoryState = CuttlePromptHistory.createState();

    function readPromptHistoryMap() {
        try {
            const raw = localStorage.getItem(PROMPT_HISTORY_STORAGE_KEY);
            const o = raw ? JSON.parse(raw) : {};
            return o && typeof o === 'object' && !Array.isArray(o) ? o : {};
        } catch (_) {
            return {};
        }
    }

    function writePromptHistoryMap(map) {
        try {
            localStorage.setItem(PROMPT_HISTORY_STORAGE_KEY, JSON.stringify(map));
        } catch (_) {}
    }

    /** When the server returns a different session_id, keep ↑/↓ history under the canonical id. */
    function migratePromptHistorySession(oldId, newId) {
        if (!oldId || !newId || oldId === newId) return;
        const map = readPromptHistoryMap();
        const merged = CuttlePromptHistory.mergeSessionHistories(
            map[oldId], map[newId], promptHistoryState.list);
        if (merged.length) {
            map[newId] = merged;
        } else {
            delete map[newId];
        }
        delete map[oldId];
        writePromptHistoryMap(map);
    }

    /** Keep local anonymous chat history when the server remaps session_id. */
    function migrateLocalChatSession(oldId, newId) {
        if (!oldId || !newId || String(oldId) === String(newId)) return;
        try {
            const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
            const oldS = sessions[oldId];
            if (!oldS) return;
            const newS = sessions[newId] || {
                id: newId,
                title: oldS.title,
                created: oldS.created || Date.now(),
                updated: Date.now(),
                messages: [],
                projectId: oldS.projectId || null,
                projectPath: oldS.projectPath || '',
                projectName: oldS.projectName || '',
            };
            const byKey = new Map();
            (newS.messages || []).concat(oldS.messages || []).forEach((m) => {
                if (!m) return;
                const key = `${m.role}|${m.timestamp || ''}|${String(m.content || '').slice(0, 120)}`;
                if (!byKey.has(key)) byKey.set(key, m);
            });
            newS.messages = Array.from(byKey.values());
            newS.updated = Date.now();
            sessions[newId] = newS;
            delete sessions[oldId];
            localStorage.setItem('chatSessions', JSON.stringify(sessions));
        } catch (e) {
            console.warn('migrateLocalChatSession failed:', e);
        }
    }

    function loadPromptHistoryForSession(sessionId) {
        CuttlePromptHistory.resetBrowse(promptHistoryState);
        if (!sessionId) {
            promptHistoryState.list = [];
            return;
        }
        const map = readPromptHistoryMap();
        let arr = Array.isArray(map[sessionId]) ? map[sessionId].slice() : [];
        if (arr.length === 0) {
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                const s = sessions[sessionId];
                if (s && Array.isArray(s.messages)) {
                    arr = CuttlePromptHistory.deriveFromUserMessages(s.messages);
                }
            } catch (_) {}
            if (arr.length) {
                map[sessionId] = arr;
                writePromptHistoryMap(map);
            }
        }
        promptHistoryState.list = arr;
    }

    function persistPromptHistoryForCurrentSession() {
        if (!session.current()) return;
        const map = readPromptHistoryMap();
        map[session.current()] = promptHistoryState.list.slice();
        writePromptHistoryMap(map);
    }

    function recordPromptHistory(composedMessage) {
        if (!session.current()) return;
        if (CuttlePromptHistory.appendRecord(promptHistoryState, composedMessage)) {
            persistPromptHistoryForCurrentSession();
        }
    }

    function textareaAtHistoryAnchor(textarea) {
        if (!textarea) return false;
        return CuttlePromptHistory.atStartAnchor(textarea.selectionStart, textarea.selectionEnd);
    }

    /** Caret at end of text — ↓ walks toward newer history / draft (symmetric to ↑ at start). */
    function textareaAtHistoryEndAnchor(textarea) {
        if (!textarea) return false;
        return CuttlePromptHistory.atEndAnchor(
            textarea.selectionStart, textarea.selectionEnd, (textarea.value || '').length);
    }

    function applyHistoryTextToTextarea(textarea, text) {
        const key = slashContextKey(textarea);
        if (key) {
            slashCtx[key].chips = [];
            hideSlashMenu(key);
            renderSlashChips(key, textarea);
        }
        const t = String(text ?? '');
        textarea.value = t;
        const len = t.length;
        textarea.setSelectionRange(len, len);
        if (textarea.id === 'chatInput') {
            autoResizeTextarea();
        } else {
            autoResizeWelcomeTextarea();
        }
        syncSlashMenuFromInput(textarea);
    }

    /**
     * @param {HTMLTextAreaElement} textarea
     * @param {number} direction -1 = older (Up), +1 = newer (Down)
     * @returns {boolean} true if the key event was consumed
     */
    function navigatePromptHistory(textarea, direction) {
        const st = promptHistoryState;
        // Draft capture (slash chips) and textarea apply (DOM) stay here;
        // index stepping lives in the owned state machine.
        if (direction < 0 && st.list.length && st.index === -1) {
            CuttlePromptHistory.captureDraft(st, composeMessageWithSlashChip(textarea).trim());
        }
        const r = CuttlePromptHistory.step(st, direction);
        if (r.status === 'none') return false;
        if (r.status === 'text' || r.status === 'draft') {
            applyHistoryTextToTextarea(textarea, r.text);
        }
        return true;
    }

    function handlePromptHistoryKeyDown(event, textarea) {
        if (!textarea) return false;
        if (event.key === 'ArrowUp' && textareaAtHistoryAnchor(textarea)) {
            const consumed = navigatePromptHistory(textarea, -1);
            if (consumed) event.preventDefault();
            return consumed;
        }
        if (event.key === 'ArrowDown' && promptHistoryState.index >= 0 && textareaAtHistoryEndAnchor(textarea)) {
            const consumed = navigatePromptHistory(textarea, 1);
            if (consumed) event.preventDefault();
            return consumed;
        }
        return false;
    }

    function escapeHtmlInline(s) {
        return String(s ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /** Turn agent-emitted local paths into file:/// URLs the chip + reveal API accept. */
    function windowsPathToFileUrl(path) {
        const norm = String(path || '').replace(/\\/g, '/');
        const parts = norm.split('/');
        const encoded = parts.map(function (seg, i) {
            if (i === 0 && /^[a-zA-Z]:$/.test(seg)) return seg;
            return encodeURIComponent(seg);
        }).join('/');
        return 'file:///' + encoded;
    }

    /**
     * Normalize markdown hrefs: trim, unwrap <…>, promote Windows paths
     * (G:/…, /g:/…, G:\…) to file:///, leave http(s)/file/vscode/cursor alone.
     */
    function normalizeMdHref(url) {
        let u = String(url || '').trim();
        if (!u) return '';
        if (u.charAt(0) === '<' && u.charAt(u.length - 1) === '>') {
            u = u.slice(1, -1).trim();
        }
        if (/^(https?:|file:|vscode:|cursor:)/i.test(u)) return u;
        // Agents sometimes emit /g:/Dev/... (slash + drive)
        let m = u.match(/^\/([a-zA-Z]:)([\\/].*)$/);
        if (m) return windowsPathToFileUrl(m[1] + m[2]);
        m = u.match(/^([a-zA-Z]:)([\\/].*)$/);
        if (m) return windowsPathToFileUrl(m[1] + m[2]);
        // UNC \\server\share\...
        if (/^\\\\[^\\\/]+[\\/]/.test(u) || /^\/\/[^\/]+\//.test(u)) {
            const unc = u.replace(/^\/\//, '\\\\').replace(/\//g, '\\');
            const body = unc.replace(/^\\\\/, '').replace(/\\/g, '/');
            const segs = body.split('/').map(encodeURIComponent);
            return 'file://' + segs.join('/');
        }
        return u;
    }

    function isSafeMdHref(url) {
        const u = normalizeMdHref(url);
        return /^(https?:|file:|vscode:|cursor:)/i.test(u);
    }

    /** Basename (and optional :line) for file / vscode deep links. */
    function mdLinkChipLabel(label, url) {
        const lab = String(label || '').trim();
        const u = normalizeMdHref(url);
        if (lab && lab !== String(url || '').trim() && lab !== u) return lab;
        try {
            let path = u;
            if (/^vscode:\/\/file\//i.test(u) || /^cursor:\/\/file\//i.test(u)) {
                path = decodeURIComponent(u.replace(/^(?:vscode|cursor):\/\/file\//i, ''));
            } else if (/^file:/i.test(u)) {
                path = decodeURIComponent(u.replace(/^file:\/\//i, ''));
            } else if (/^https?:\/\//i.test(u)) {
                try {
                    const parsed = new URL(u);
                    return parsed.hostname + (parsed.pathname && parsed.pathname !== '/' ? parsed.pathname : '');
                } catch (_) {
                    return lab || u;
                }
            }
            path = path.replace(/\\/g, '/');
            const base = path.split('/').filter(Boolean).pop() || path;
            return base || lab || u;
        } catch (_) {
            return lab || u;
        }
    }

    function mdLinkChipKind(url) {
        const u = normalizeMdHref(url);
        if (/^file:/i.test(u)) return 'file';
        if (/^vscode:/i.test(u) || /^cursor:/i.test(u)) return 'code';
        if (/^https?:/i.test(u)) return 'web';
        return 'link';
    }

    function renderMdLinkChip(label, url) {
        // Agents sometimes write [CH-000405](file://…) or [CH-000405](…). Prefer
        // the chat-handle badge whenever the visible label is a CH- token —
        // otherwise click reveals a folder / opens a dead href and does nothing
        // useful in chat.
        const labelHandle = parseChatHandleToken(String(label || '').trim());
        if (labelHandle) {
            return renderChatHandleLink(labelHandle.display);
        }
        const hrefHandle = parseChatHandleToken(String(url || '').trim());
        if (hrefHandle) {
            return renderChatHandleLink(hrefHandle.display);
        }
        const href = normalizeMdHref(url);
        if (!isSafeMdHref(href)) {
            return escapeHtmlInline(label || href || url);
        }
        const kind = mdLinkChipKind(href);
        const display = escapeHtmlInline(mdLinkChipLabel(label, href));
        const safeHref = escapeHtmlInline(href);
        // File chips open Explorer via /api/fs/reveal (browsers block file://).
        const title = escapeHtmlInline(
            kind === 'file'
                ? ('Open in default application — ' + href)
                : kind === 'code'
                ? ('Open in editor — ' + href)
                : href
        );
        const icon = kind === 'file'
            ? '<svg class="md-link-chip-svg" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M3.5 1.5h6l3 3V14a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zm6 .5v2.5H12z"/></svg>'
            : kind === 'code'
            ? '<svg class="md-link-chip-svg" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M5.8 3.5 2.3 8l3.5 4.5.9-.7L3.7 8l3-3.8-.9-.7zm4.4 0-.9.7L12.3 8l-3 3.8.9.7L13.7 8l-3.5-4.5z"/></svg>'
            : '<svg class="md-link-chip-svg" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M6.5 3.5a3 3 0 0 1 4.24 0l1.76 1.76a3 3 0 0 1 0 4.24l-.7.7-.85-.85.7-.7a1.8 1.8 0 0 0 0-2.54L10.1 4.35a1.8 1.8 0 0 0-2.55 0L6.7 5.2l-.85-.85 1.65-1.65zM4.2 6.8l.85.85-.7.7a1.8 1.8 0 0 0 0 2.54l1.76 1.76a1.8 1.8 0 0 0 2.55 0l.85-.85.85.85-.85.85a3 3 0 0 1-4.24 0L3.5 11.74a3 3 0 0 1 0-4.24l.7-.7zm1.4 2.1 3.5-3.5.85.85-3.5 3.5-.85-.85z"/></svg>';
        return (
            '<a class="md-link-chip md-link-chip--' + kind + '" href="' + safeHref +
            '" data-tooltip="' + title + '" target="_blank" rel="noopener noreferrer">' +
            '<span class="md-link-chip-icon" aria-hidden="true">' + icon + '</span>' +
            '<span class="md-link-chip-label">' + display + '</span></a>'
        );
    }

    async function openFileInDefaultApp(href) {
        const toast = window.showToast || function () {};
        try {
            const resp = await fetch('/api/fs/open', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: String(href || '') }),
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (_) {}
            if (!resp.ok || !data || !data.success) {
                const err = (data && data.error) || ('HTTP ' + resp.status);
                toast('Could not open file: ' + err, 'error');
                return false;
            }
            return true;
        } catch (e) {
            toast('Could not open file: ' + ((e && e.message) || e), 'error');
            return false;
        }
    }

    function bindMdLinkChipClicks() {
        // Capture-phase so we beat default <a target=_blank> navigation.
        document.addEventListener('click', function (e) {
            const a = e.target && e.target.closest && e.target.closest('a.md-link-chip--file');
            if (!a) return;
            const href = a.getAttribute('href') || '';
            if (!/^file:/i.test(href)) return;
            // Allow modified clicks to fall through (copy link / open elsewhere).
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            openFileInDefaultApp(href);
        }, true);
    }

    /**
     * Parse a bare chat / message handle (CH-000182 or CH-000182-23).
     * Returns { sessionId, messageIndex, display } or null.
     */
    function parseChatHandleToken(raw) {
        const s = String(raw || '').trim();
        const m = s.match(/^CH-(\d+)(?:-(\d+))?$/i);
        if (!m) return null;
        const sessionId = parseInt(m[1], 10);
        if (!Number.isFinite(sessionId) || sessionId < 1) return null;
        let messageIndex = null;
        if (m[2] != null) {
            messageIndex = parseInt(m[2], 10);
            if (!Number.isFinite(messageIndex) || messageIndex < 1) return null;
        }
        const chatDisplay = formatChatDisplayId(sessionId);
        if (!chatDisplay) return null;
        return {
            sessionId: String(sessionId),
            messageIndex,
            display: messageIndex ? (chatDisplay + '-' + messageIndex) : chatDisplay,
        };
    }

    function buildChatHandleHref(parsed) {
        if (!parsed) return '#';
        try {
            const url = new URL(window.location.href);
            url.pathname = url.pathname || '/chat_page.html';
            if (!/chat_page\.html$/i.test(url.pathname) && !/\/chat\/?$/i.test(url.pathname)) {
                url.pathname = '/chat_page.html';
            }
            url.searchParams.set('chat', parsed.sessionId);
            if (parsed.messageIndex) {
                url.searchParams.set('msg', String(parsed.messageIndex));
            } else {
                url.searchParams.delete('msg');
            }
            url.searchParams.delete('session');
            url.hash = '';
            return url.pathname + url.search;
        } catch (_) {
            let href = '/chat_page.html?chat=' + encodeURIComponent(parsed.sessionId);
            if (parsed.messageIndex) href += '&msg=' + encodeURIComponent(String(parsed.messageIndex));
            return href;
        }
    }

    function renderChatHandleLink(raw) {
        const parsed = parseChatHandleToken(raw);
        if (!parsed) return escapeHtmlInline(raw);
        const display = escapeHtmlInline(parsed.display);
        const href = escapeHtmlInline(buildChatHandleHref(parsed));
        const title = parsed.messageIndex
            ? ('Open ' + parsed.display)
            : ('Open chat ' + parsed.display);
        return (
            '<a class="chat-handle-link" href="' + href + '" ' +
            'data-chat-handle="' + display + '" ' +
            'title="' + escapeHtmlInline(title) + '">' + display + '</a>'
        );
    }

    /** Switch this viewport to the chat named by a CH- handle (optional -N bubble). */
    async function navigateToChatHandle(raw) {
        const parsed = parseChatHandleToken(raw);
        if (!parsed) return false;
        if (parsed.messageIndex) {
            transcriptNav.queueMessageJump(parsed.messageIndex);
            try {
                const url = new URL(window.location.href);
                url.searchParams.set('msg', String(parsed.messageIndex));
                history.replaceState(null, '', url.pathname + url.search + url.hash);
            } catch (_) {}
        }
        if (sessionIdsEqual(session.current(), parsed.sessionId)) {
            if (parsed.messageIndex) {
                transcriptNav.consumePendingMessageJump();
            }
            return true;
        }
        try {
            await loadChatSession(parsed.sessionId);
        } catch (err) {
            console.warn('navigateToChatHandle failed:', err);
            if (typeof window.showToast === 'function') {
                window.showToast('Could not open ' + parsed.display, 'error');
            }
            return false;
        }
        return true;
    }

    /**
     * Turn bare CH-000431 / CH-000431-23 tokens into link placeholders.
     * Skips handles inside inline `code`. Mutates linkChips in place.
     */
    function linkifyChatHandlesInText(text, linkChips) {
        if (typeof text !== 'string' || !text) return text;
        const chips = Array.isArray(linkChips) ? linkChips : [];
        const inlineCodeHold = [];
        let out = text.replace(/`([^`\n]+)`/g, function (_, code) {
            const ph = '{{CUTTLE_IC_' + inlineCodeHold.length + '}}';
            inlineCodeHold.push('`' + code + '`');
            return ph;
        });
        out = out.replace(/\b(CH-\d+(?:-\d+)?)\b/gi, function (match) {
            if (!parseChatHandleToken(match)) return match;
            const placeholder = '{{CUTTLE_LINK_' + chips.length + '}}';
            chips.push(renderChatHandleLink(match));
            return placeholder;
        });
        for (let i = 0; i < inlineCodeHold.length; i++) {
            out = out.split('{{CUTTLE_IC_' + i + '}}').join(inlineCodeHold[i]);
        }
        return out;
    }

    function bindChatHandleLinkClicks() {
        document.addEventListener('click', function (e) {
            const launcher = e.target && e.target.closest && e.target.closest('.subagent-launcher, .subagent-fleet-card');
            if (launcher) {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                const handle = launcher.getAttribute('data-chat-handle') || '';
                if (!handle) return;
                e.preventDefault();
                e.stopPropagation();
                navigateToChatHandle(handle);
                return;
            }
            const a = e.target && e.target.closest && e.target.closest('a.chat-handle-link');
            if (!a) return;
            // Modified clicks / middle-click: let the browser follow href (new tab).
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
            const handle = a.getAttribute('data-chat-handle') || '';
            if (!handle) return;
            e.preventDefault();
            e.stopPropagation();
            navigateToChatHandle(handle);
        }, true);
    }

    function initGitCommitViewer() {
        if (!window.CuttleGitCommitViewer || typeof window.CuttleGitCommitViewer.initChat !== 'function') {
            return;
        }
        window.CuttleGitCommitViewer.initChat({
            getProjectPath: function () {
                return effectiveGitPath();
            },
        });
    }

    function bindGitCommitLinkClicks() {
        document.addEventListener('click', function (e) {
            const a = e.target && e.target.closest && e.target.closest('a.git-commit-link');
            if (!a) return;
            const hash = a.getAttribute('data-git-hash') || '';
            if (!hash) return;
            // Always stay in this chat — never let href="#git-commit-…" rewrite the URL.
            e.preventDefault();
            e.stopPropagation();
            if (!window.CuttleGitCommitViewer || typeof window.CuttleGitCommitViewer.openFromChat !== 'function') {
                return;
            }
            window.CuttleGitCommitViewer.openFromChat({
                hash: hash,
                getProjectPath: function () {
                    return (typeof currentProject !== 'undefined' && currentProject && currentProject.path)
                        ? String(currentProject.path)
                        : '';
                },
            });
        }, true);
    }

    // Sub-agent launcher rows + live in-progress badges: chat_subagent_launchers.js.
    const subagentLaunchers = CuttleChatSubagentLaunchers.create({
        win: window,
        escapeHtmlInline,
        parseChatHandleToken,
        avatarHtml: profileAvatarInnerHtml,
        chipCategoryClass: slashCommandChipCategoryClass,
    });

    // Media thumbs, lightbox and image Copy / Save menu: owned by chat_media.js.
    // Host-document listeners ride the frame teardown signal.
    const chatMedia = CuttleChatMedia.create({
        win: window,
        escapeHtmlInline,
        clamp,
        hostTeardownSignal: parentListenersAbort.signal,
    });
    // Inline `onerror` hook on video poster thumbs (see buildMediaThumbHtml).
    window.CuttleMediaThumbFallback = chatMedia.thumbFallback;

    function clamp(n, min, max) {
        const x = Number(n);
        if (!Number.isFinite(x)) return min;
        return Math.min(max, Math.max(min, x));
    }

    function safeJsonParse(s) {
        try {
            const obj = JSON.parse(String(s ?? ''));
            if (obj && typeof obj === 'object') return obj;
        } catch (_) {}
        return null;
    }

    /** Slash commands: / menu + chips (stack multiple). `category` drives palette styling. */

    // Slash decisions live in chat_slash.js; wrappers bind page state.
    function availableSlashCommands() {
        return CuttleChatSlash.availableSlashCommands(slashPaletteSupplement.harnessAgents || []);
    }

    /**
     * Overlay `/api/agents` onto the static slash list: install hints when CLI
     * is missing, and append drop-in harness agents not already hardcoded.
     */
    function mergeHarnessAgentsIntoSlashCommands(base) {
        return CuttleChatSlash.mergeHarnessAgentsIntoSlashCommands(
            base, slashPaletteSupplement.harnessAgents || []);
    }

    function loadHarnessAgentsForPalette() {
        const path = currentProject && currentProject.path ? String(currentProject.path) : '';
        if (
            slashPaletteSupplement.harnessAgentsLoading
            && slashPaletteSupplement.harnessAgentsPath === path
            && slashPaletteSupplement.harnessAgentsLoaded
        ) {
            return;
        }
        slashPaletteSupplement.harnessAgentsLoading = true;
        slashPaletteSupplement.harnessAgentsPath = path;
        const qs = new URLSearchParams();
        if (path) qs.set('path', path);
        const url = qs.toString() ? ('/api/agents?' + qs.toString()) : '/api/agents';
        fetch(url, { credentials: 'include', cache: 'no-store' })
            .then((r) => r.json())
            .then((j) => {
                if (slashPaletteSupplement.harnessAgentsPath !== path) return;
                slashPaletteSupplement.harnessAgents =
                    j && j.success && Array.isArray(j.agents) ? j.agents : [];
            })
            .catch(() => {
                if (slashPaletteSupplement.harnessAgentsPath === path) {
                    slashPaletteSupplement.harnessAgents = [];
                }
            })
            .finally(() => {
                slashPaletteSupplement.harnessAgentsLoading = false;
                slashPaletteSupplement.harnessAgentsLoaded = true;
                const welcomeInput = document.getElementById('welcomeChatInput');
                const chatInput = document.getElementById('chatInput');
                if (welcomeInput && document.activeElement === welcomeInput) {
                    syncSlashMenuFromInput(welcomeInput);
                }
                if (chatInput && document.activeElement === chatInput) {
                    syncSlashMenuFromInput(chatInput);
                }
            });
    }

    /** True when the Cursor Agent sticky badge/chip is applied to this composer. */
    function hasActiveCursorAgentChip(key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyCursorAgentChip(c))) return true;
        }
        return false;
    }

    function hasActiveMuseAgentChip(key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyMuseAgentChip(c))) return true;
        }
        return false;
    }

    function hasActiveHermesAgentChip(key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyHermesAgentChip(c))) return true;
        }
        return false;
    }

    function hasActiveOpenCodeAgentChip(key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyOpenCodeAgentChip(c))) return true;
        }
        return false;
    }

    function hasActiveCodexAgentChip(key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyCodexAgentChip(c))) return true;
        }
        return false;
    }

    function cursorAgentSlashCommandsForPalette() {
        if (!hasActiveCursorAgentChip()) return [];
        return CuttleChatSlash.CURSOR_AGENT_SLASH_COMMANDS.slice();
    }

    /**
     * Nested ``/usage`` for Muse / Codex / Hermes / OpenCode / Claude — same chip
     * pattern as Cursor's Usage row (gated to the active agent badge).
     * Cursor keeps its own entry inside CuttleChatSlash.CURSOR_AGENT_SLASH_COMMANDS.
     */
    // Harness usage/cost tables live in chat_slash.js (single owner).
    function harnessCostSlashCommand(agent) {
        return CuttleChatSlash.harnessCostSlashCommand(agent);
    }

    function hasActiveHarnessAgentChip(agent, key) {
        const keys = key ? [key] : ['chat', 'welcome'];
        for (let i = 0; i < keys.length; i++) {
            const chips = (slashCtx[keys[i]] && slashCtx[keys[i]].chips) || [];
            if (chips.some((c) => isStickyAgentChip(c, agent))) return true;
        }
        return false;
    }

    /** Nested harness one-shots (``/usage`` where supported, ``/cost``) for the active badge. */
    // Entries owned by chat_usage_live.js — page supplies chip state, domain builds the list.
    // (Dedicated hasActive*Chip matchers delegate to hasActiveHarnessAgentChip per agent.)
    function harnessUsageSlashCommandsForPalette() {
        return CuttleUsageLive.harnessUsageCommands({
            isActive: (agent) => hasActiveHarnessAgentChip(agent),
            usageTable: CuttleChatSlash.HARNESS_USAGE_SLASH_BY_AGENT,
            costCommand: harnessCostSlashCommand,
        });
    }

    function readStarredSlashPrefixes() {
        try {
            const raw = localStorage.getItem(STARRED_SLASH_STORAGE_KEY);
            const arr = raw ? JSON.parse(raw) : [];
            if (!Array.isArray(arr)) return [];
            return arr
                .map((p) => String(p || ''))
                .filter((p) => CuttleChatSlash.SLASH_COMMANDS.some((c) => c.stickySession && c.prefix === p));
        } catch (_) {
            return [];
        }
    }

    function writeStarredSlashPrefixes(prefixes, opts) {
        const next = Array.isArray(prefixes) ? prefixes : [];
        try {
            localStorage.setItem(STARRED_SLASH_STORAGE_KEY, JSON.stringify(next));
        } catch (_) {}
        if (!(opts && opts.localOnly)) {
            persistStarredSlashToServer(next);
        }
    }

    function persistStarredSlashToServer(prefixes) {
        fetch('/api/settings/starred-slash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prefixes: Array.isArray(prefixes) ? prefixes : [] }),
        }).catch(() => {});
    }

    /** Server copy is what new chats use; migrate localStorage on first load. */
    function hydrateStarredSlashFromServer() {
        fetch('/api/settings/starred-slash')
            .then((r) => r.json())
            .then((j) => {
                const server = j && j.success && Array.isArray(j.prefixes) ? j.prefixes : null;
                if (!server) return;
                const local = readStarredSlashPrefixes();
                if (server.length) {
                    writeStarredSlashPrefixes(server, { localOnly: true });
                    if (!session.current() && !getSessionPrefs(newComposerPrefsId)) applyStarredSlashChips();
                    else {
                        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
                        renderSlashChips('chat', document.getElementById('chatInput'));
                    }
                } else if (local.length) {
                    persistStarredSlashToServer(local);
                }
            })
            .catch(() => {});
    }

    function isSlashCommandStarred(prefix) {
        return CuttleChatSlash.isSlashCommandStarred(readStarredSlashPrefixes(), prefix);
    }

    /**
     * Toggle star for a sticky agent command. Only one sticky agent can be
     * starred at a time so new chats get a single default (e.g. Cursor Agent).
     */
    function toggleStarredSlashCommand(prefix) {
        const cmd = CuttleChatSlash.SLASH_COMMANDS.find((c) => c.prefix === prefix && c.stickySession);
        if (!cmd) return false;
        const current = readStarredSlashPrefixes();
        const wasStarred = current.includes(prefix);
        const next = wasStarred ? [] : [prefix];
        writeStarredSlashPrefixes(next);
        // On the welcome / new-chat composer, mirror the default immediately.
        if (!session.current()) {
            applyStarredSlashChips();
        } else {
            // Keep session sticky chips; refresh so ★ on the badge updates.
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
        }
        return !wasStarred;
    }

    function getStarredStickyChips() {
        return CuttleChatSlash.starredStickyChips(readStarredSlashPrefixes());
    }

    /** Apply globally starred sticky agent chips (new chat / welcome only). */
    function applyStarredSlashChips() {
        // In-memory only: currentSessionId may still be the chat we are leaving,
        // whose own "badge removed" choice must survive.
        stickyAgentClearedPending = false;
        const chips = getStarredStickyChips();
        slashCtx.welcome.chips = chips.slice();
        slashCtx.chat.chips = chips.slice();
        hideSlashMenu('welcome');
        hideSlashMenu('chat');
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        renderSlashChips('chat', document.getElementById('chatInput'));
    }

    function normalizeStarredProjectPath(path) {
        return CuttleChatProject.normalizeProjectPath(path);
    }

    function readStarredProject() {
        try {
            const raw = localStorage.getItem(STARRED_PROJECT_STORAGE_KEY);
            if (!raw) return null;
            const obj = JSON.parse(raw);
            if (!obj || typeof obj !== 'object') return null;
            const path = String(obj.path || '').trim();
            const name = String(obj.name || '').trim();
            const id = obj.id != null ? obj.id : null;
            if (id == null && !path && !name) return null;
            return { id, path, name: name || 'Project' };
        } catch (_) {
            return null;
        }
    }

    function writeStarredProject(proj, opts) {
        const next = proj
            ? {
                id: proj.id != null ? proj.id : null,
                path: String(proj.path || '').trim(),
                name: String(proj.name || '').trim() || 'Project',
            }
            : null;
        try {
            if (next) localStorage.setItem(STARRED_PROJECT_STORAGE_KEY, JSON.stringify(next));
            else localStorage.removeItem(STARRED_PROJECT_STORAGE_KEY);
        } catch (_) {}
        if (!(opts && opts.localOnly)) {
            persistStarredProjectToServer(next);
        }
    }

    function persistStarredProjectToServer(proj) {
        fetch('/api/settings/starred-project', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project: proj || null }),
        }).catch(() => {});
    }

    function hydrateStarredProjectFromServer() {
        fetch('/api/settings/starred-project')
            .then((r) => r.json())
            .then((j) => {
                if (!(j && j.success)) return;
                const server = j.project || null;
                const local = readStarredProject();
                if (server && (server.path || server.id != null || server.name)) {
                    writeStarredProject(server, { localOnly: true });
                    if (!session.current()) {
                        reconcileChatProject();
                    } else {
                        renderProjectChips();
                    }
                } else if (local) {
                    persistStarredProjectToServer(local);
                }
            })
            .catch(() => {});
    }

    function isProjectStarred(proj) {
        const starred = readStarredProject();
        if (!starred || !proj) return false;
        if (starred.id != null && proj.id != null && String(starred.id) === String(proj.id)) {
            return true;
        }
        const a = normalizeStarredProjectPath(starred.path);
        const b = normalizeStarredProjectPath(proj.path || proj.projectPath);
        return !!(a && b && a === b);
    }

    /**
     * Toggle star for a registered project. Only one project can be starred
     * at a time so new chats get a single default working directory.
     */
    function toggleStarredProject(proj) {
        if (!proj || (proj.id == null && !proj.path && !proj.projectPath && !proj.name && !proj.projectName)) {
            return false;
        }
        const record = {
            id: proj.id != null ? proj.id : (proj.projectId != null ? proj.projectId : null),
            path: String(proj.path || proj.projectPath || '').trim(),
            name: String(proj.name || proj.projectName || '').trim() || 'Project',
        };
        const wasStarred = isProjectStarred(record);
        // Exclusive: star replaces any previous star; unstar clears.
        writeStarredProject(wasStarred ? null : record);
        if (!session.current()) {
            // New / welcome chat: apply the new default immediately.
            if (!wasStarred) {
                const live = findProjectById(record.id)
                    || (projects || []).find(
                        (p) => normalizeStarredProjectPath(p.path)
                            === normalizeStarredProjectPath(record.path)
                    )
                    || record;
                setChatProject(live, { silent: true });
            } else {
                currentProject = null;
                reconcileChatProject();
            }
        } else {
            renderProjectChips();
        }
        return !wasStarred;
    }

    const slashPaletteSupplement = Object.assign(
        CuttleChatAgentControls.createProviderState(),
        {
            projectCommands: [],
            projectCommandsPath: '',
            loading: false,
            loaded: false,
            projectCommandsLoading: false,
            harnessAgents: [],
            harnessAgentsPath: '',
            harnessAgentsLoading: false,
            harnessAgentsLoaded: false,
        }
    );

    /**
     * Model/effort control owner. Owns the provider state above and the
     * six provider model/effort loaders + palette-item builders; the page
     * keeps thin wrappers plus project-command/harness-agent supplement
     * state, persistence setters, seeding/reset and badge helpers.
     */
    const agentControls = CuttleChatAgentControls.create({
        state: slashPaletteSupplement,
        getSessionId: () => session.current(),
        getProjectPath: () => (currentProject && currentProject.path ? String(currentProject.path) : ''),
        hasActiveCursor: () => hasActiveCursorAgentChip(),
        hasActiveMuse: () => hasActiveMuseAgentChip(),
        hasActiveHermes: () => hasActiveHermesAgentChip(),
        hasActiveOpenCode: () => hasActiveOpenCodeAgentChip(),
        hasActiveCodex: () => hasActiveCodexAgentChip(),
        hasActiveHarness: (id) => hasActiveHarnessAgentChip(id),
        hasCursorModelChip: () => slashCtx.chat.chips.concat(slashCtx.welcome.chips).some((c) => c.category === 'cursor-model'),
        beginFetch: (field) => beginSupplementFetch(field),
        isCurrentFetch: (field, gen) => isCurrentSupplementFetch(field, gen),
        captureMutation: (sid, label) => sessionMutations.capture(sid, label, session.navSeq()),
        isMutationCurrent: (tok) => sessionMutations.current(tok, session.current(), session.navSeq()),
        sessionMutationFetch: (url, opts) => sessionMutationFetch(url, opts),
        renderSlashChips: () => {
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
        },
        syncActiveSlashMenu: () => {
            const welcomeInput = document.getElementById('welcomeChatInput');
            const chatInput = document.getElementById('chatInput');
            if (welcomeInput && document.activeElement === welcomeInput) syncSlashMenuFromInput(welcomeInput);
            if (chatInput && document.activeElement === chatInput) syncSlashMenuFromInput(chatInput);
        },
        syncSlashInput: (textarea) => syncSlashMenuFromInput(textarea),
        // DOM rendering stays in the page; the controller only decides which
        // user-bubble badge labels to repaint.
        paintAgentUserBadges: (matchLabelRe, nextLabel) => repaintAgentUserBadges(matchLabelRe, nextLabel),
        persistStickySlashForCurrentSession: () => persistStickySlashForCurrentSession(),
        refreshHistoryAgentChips: () => refreshHistoryAgentChips(),
        fetch: (url, opts) => fetch(url, opts),
        toast: (msg, type) => (window.showToast ? window.showToast(msg, type) : undefined),
    });

    function cursorAgentSessionOptionsKey() {
        const path = currentProject && currentProject.path ? String(currentProject.path) : '';
        const sid = session.current() != null ? String(session.current()) : '';
        return path + '\x1f' + sid;
    }

    /** Model/effort palette rows that carry a star button (global default). */
    const COMPANION_CHIP_CATEGORIES = [
        'cursor-model', 'cursor-cmd',
        'muse-model', 'muse-effort', 'muse-cmd',
        'hermes-model', 'hermes-effort', 'hermes-cmd',
        'opencode-model', 'opencode-effort', 'opencode-cmd',
        'codex-model', 'codex-effort', 'codex-cmd',
        'claude-model', 'claude-effort', 'claude-cmd',
        'deepseek-cmd', 'antigravity-cmd',
    ];

    const MODEL_STAR_SPEC_BY_CATEGORY = {
        'cursor-model': { agent: 'cursor', kind: 'model' },
        'muse-model': { agent: 'muse', kind: 'model' },
        'muse-effort': { agent: 'muse', kind: 'effort' },
        'hermes-model': { agent: 'hermes', kind: 'model' },
        'hermes-effort': { agent: 'hermes', kind: 'effort' },
        'opencode-model': { agent: 'opencode', kind: 'model' },
        'opencode-effort': { agent: 'opencode', kind: 'effort' },
        'codex-model': { agent: 'codex', kind: 'model' },
        'codex-effort': { agent: 'codex', kind: 'effort' },
        'claude-model': { agent: 'claude', kind: 'model' },
        'claude-effort': { agent: 'claude', kind: 'effort' },
    };

    function loadAgentCapabilities() {
        return agentControls.loadAgentCapabilities();
    }

    function agentSupportsModelPin(agentId) {
        return agentControls.agentSupportsModelPin(agentId);
    }

    function agentSupportsEffort(agentId) {
        return agentControls.agentSupportsEffort(agentId);
    }

    function starredAgentModel(agentId) {
        return agentControls.starredAgentModel(agentId);
    }

    function starredAgentEffort(agentId) {
        return agentControls.starredAgentEffort(agentId);
    }

    /** True when this model/effort row is the agent's global starred default. */
    function isAgentDefaultStarred(spec, cmd) {
        return agentControls.isAgentDefaultStarred(spec, cmd);
    }

    function loadAllStarredDefaults() {
        return agentControls.loadAllStarredDefaults();
    }

    function loadCursorStarredDefault() {
        return agentControls.loadCursorStarredDefault();
    }

    /**
     * Star/unstar a model or effort row as the global default for that agent.
     * Clicking the filled star clears it back to the CLI default.
     */
    function toggleStarredAgentDefault(agentId, kind, value, textarea, key) {
        return agentControls.toggleStarredAgentDefault(agentId, kind, value, textarea, key);
    }

    /** Re-resolve the effective preference after a star change. */
    function refreshAgentPreferenceAfterStar(aid) {
        return agentControls.refreshAgentPreferenceAfterStar(aid);
    }

    /** When the user sends ``/model refresh``, reload the active agent's catalog. */
    function maybeRefreshModelsCatalogFromOutbound(message) {
        return agentControls.maybeRefreshModelsCatalogFromOutbound(message);
    }

    function maybeRefreshCursorModelsCatalogFromOutbound(message) {
        maybeRefreshModelsCatalogFromOutbound(message);
    }

    function loadCursorAgentModelsForPalette(force, opts) {
        return agentControls.loadCursorAgentModelsForPalette(force, opts);
    }

    /** anyChat: load even without a /muse chip (user typed "muse" in the palette). */
    function loadMuseModelsForPalette(anyChat, opts) {
        return agentControls.loadMuseModelsForPalette(anyChat, opts);
    }

    /** Persist the chat's Muse model pick (palette click — no message sent). */
    function persistMuseModelSelection(modelId) {
        return agentControls.persistMuseModelSelection(modelId);
    }

    /** anyChat: load even without a /hermes chip (user typed "hermes" in the palette). */
    function loadHermesModelsForPalette(anyChat) {
        return agentControls.loadHermesModelsForPalette(anyChat);
    }

    /** Persist the chat's Hermes model pick (palette click — no message sent). */
    function persistHermesModelSelection(modelId) {
        return agentControls.persistHermesModelSelection(modelId);
    }

    function loadSlashPaletteSupplement() {
        if (slashPaletteSupplement.loading || slashPaletteSupplement.loaded) return;
        slashPaletteSupplement.loading = true;
        slashPaletteSupplement.loaded = true;
        slashPaletteSupplement.loading = false;
        loadProjectCommandsForPalette();
        loadHarnessAgentsForPalette();
        const welcomeInput = document.getElementById('welcomeChatInput');
        const chatInput = document.getElementById('chatInput');
        if (welcomeInput && document.activeElement === welcomeInput) {
            syncSlashMenuFromInput(welcomeInput);
        }
        if (chatInput && document.activeElement === chatInput) {
            syncSlashMenuFromInput(chatInput);
        }
    }

    function loadProjectCommandsForPalette() {
        const path = currentProject && currentProject.path ? String(currentProject.path) : '';
        if (!path) {
            slashPaletteSupplement.projectCommands = [];
            slashPaletteSupplement.projectCommandsPath = '';
            return;
        }
        if (
            slashPaletteSupplement.projectCommandsLoading
            && slashPaletteSupplement.projectCommandsPath === path
        ) {
            return;
        }
        slashPaletteSupplement.projectCommandsLoading = true;
        slashPaletteSupplement.projectCommandsPath = path;
        const qs = new URLSearchParams();
        qs.set('path', path);
        if (currentProject && currentProject.id != null) {
            qs.set('project_id', String(currentProject.id));
        }
        fetch('/api/project-commands?' + qs.toString(), { credentials: 'include', cache: 'no-store' })
            .then((r) => r.json())
            .then((j) => {
                if (slashPaletteSupplement.projectCommandsPath !== path) return;
                slashPaletteSupplement.projectCommands =
                    j && j.success && Array.isArray(j.commands) ? j.commands : [];
            })
            .catch(() => {
                if (slashPaletteSupplement.projectCommandsPath === path) {
                    slashPaletteSupplement.projectCommands = [];
                }
            })
            .finally(() => {
                slashPaletteSupplement.projectCommandsLoading = false;
                const welcomeInput = document.getElementById('welcomeChatInput');
                const chatInput = document.getElementById('chatInput');
                if (welcomeInput && document.activeElement === welcomeInput) {
                    syncSlashMenuFromInput(welcomeInput);
                }
                if (chatInput && document.activeElement === chatInput) {
                    syncSlashMenuFromInput(chatInput);
                }
            });
    }

    // Palette category/bucket/type-filter state and the menu lifecycle live in
    // chat_slash_palette.js (CuttleChatSlashPalette); the page keeps the
    // project-command builders below for chip/history consumers.

    function buildProjectPaletteItems() {
        return CuttleChatSlash.buildProjectPaletteItems(projects);
    }

    function buildProjectCommandPaletteItems() {
        return CuttleChatSlash.buildProjectCommandPaletteItems(
            slashPaletteSupplement.projectCommands || []);
    }

    function buildCursorModelPaletteItems(filterLower) {
        return agentControls.buildCursorModelPaletteItems(filterLower);
    }

    /**
     * `/muse model <id>` entries. Unlike Cursor's `/model`, picking one is a local
     * setting change (POST + chip relabel) rather than a chip that composes into
     * the next message, so it works while `/muse` is sticky.
     */
    function buildMuseModelPaletteItems(filterLower) {
        return agentControls.buildMuseModelPaletteItems(filterLower);
    }

    /**
     * Seed the Muse supplement from a session payload (messages response)
     * before first paint. Keys are marked so the palette loaders treat the
     * values as fresh and only top up the models list.
     */
    function sessionMutationFetch(url, options) {
        const sid = JSON.parse(options.body || '{}').session || session.current();
        return sessionMutations.enqueue(String(sid) + ':' + url, () => fetch(url, options));
    }

    function beginSupplementFetch(field) {
        const S = slashPaletteSupplement;
        S[field] = (S[field] || 0) + 1;
        return S[field];
    }

    function isCurrentSupplementFetch(field, gen) {
        return slashPaletteSupplement[field] === gen;
    }

    /**
     * Palette pick of a Cursor model id: record the preference and invalidate
     * any in-flight models fetch. The palette controller owns the selection
     * logic; this keeps the preferred-model state page-owned.
     */
    function onCursorModelPick(mid) {
        slashPaletteSupplement.preferredModel = mid;
        beginSupplementFetch('cursorModelsGen');
    }

    /**
     * Neutralize cached agent preferences when the session identity changes.
     * prev/next are '' for the unsent composer: '' → id keeps dirty user
     * picks (same unsent chat gaining an id); anything else fully resets so a
     * new chat never inherits the previous chat's model. Always invalidates
     * caches and in-flight fetches via the generation counters.
     */
    function resetAgentSupplementForIdentity(prev, next) {
        return agentControls.resetAgentSupplementForIdentity(prev, next);
    }

    /** True when chip is a palette-added model/effort companion of another agent. */
    function isOrphanedCompanionChip(chip, incomingCmd) {
        const cat = String((chip && chip.category) || '').toLowerCase();
        if (COMPANION_CHIP_CATEGORIES.indexOf(cat) === -1) return false;
        const chipAgent = composerChipAgentId(chip);
        if (!chipAgent) return false;
        const incomingAgent = composerChipAgentId({
            prefix: incomingCmd && incomingCmd.prefix,
            category: incomingCmd && incomingCmd.category,
        });
        return !!incomingAgent && chipAgent !== incomingAgent;
    }

    function sessionPin(data, agent, kind) {
        return CuttleChatAgentModel.sessionPin(data, agent, kind);
    }

    function seedMuseSupplementFromSessionData(data, sessionId) {
        return agentControls.seedMuseSupplementFromSessionData(data, sessionId);
    }

    /** Hydrate this chat's pinned Muse reasoning effort (badge-gated like models). */
    function loadMuseEffortForPalette() {
        return agentControls.loadMuseEffortForPalette();
    }

    /** Persist the chat's Muse reasoning-effort pick (palette click — no message sent). */
    function persistMuseEffortSelection(effort) {
        return agentControls.persistMuseEffortSelection(effort);
    }

    /**
     * `/muse effort <level>` entries. Badge-gated like `/muse model`: they only
     * appear while the Muse chip is applied to this chat.
     */
    function buildMuseEffortPaletteItems(filterLower) {
        return agentControls.buildMuseEffortPaletteItems(filterLower);
    }

    /**
     * `/hermes model <id>` entries. Badge-gated like Muse: picking one is a local
     * setting change (POST + chip relabel), not a composed chip.
     */
    function buildHermesModelPaletteItems(filterLower) {
        return agentControls.buildHermesModelPaletteItems(filterLower);
    }

    /** Seed Hermes supplement from a session payload (messages response). */
    function seedHermesSupplementFromSessionData(data, sessionId) {
        return agentControls.seedHermesSupplementFromSessionData(data, sessionId);
    }

    function loadHermesEffortForPalette() {
        return agentControls.loadHermesEffortForPalette();
    }

    function persistHermesEffortSelection(effort) {
        return agentControls.persistHermesEffortSelection(effort);
    }

    /**
     * `/hermes effort <level>` entries. Badge-gated like `/hermes model`.
     */
    function buildHermesEffortPaletteItems(filterLower) {
        return agentControls.buildHermesEffortPaletteItems(filterLower);
    }

    /** anyChat: load even without an /opencode chip (user typed "opencode" in the palette). */
    function loadOpenCodeModelsForPalette(anyChat, opts) {
        return agentControls.loadOpenCodeModelsForPalette(anyChat, opts);
    }

    function persistOpenCodeModelSelection(modelId) {
        return agentControls.persistOpenCodeModelSelection(modelId);
    }

    function buildOpenCodeModelPaletteItems(filterLower, opts) {
        return agentControls.buildOpenCodeModelPaletteItems(filterLower, opts);
    }

    function seedOpenCodeSupplementFromSessionData(data, sessionId) {
        return agentControls.seedOpenCodeSupplementFromSessionData(data, sessionId);
    }

    function loadOpenCodeEffortForPalette() {
        return agentControls.loadOpenCodeEffortForPalette();
    }

    function persistOpenCodeEffortSelection(effort) {
        return agentControls.persistOpenCodeEffortSelection(effort);
    }

    function buildOpenCodeEffortPaletteItems(filterLower) {
        return agentControls.buildOpenCodeEffortPaletteItems(filterLower);
    }

    /** anyChat: load even without a /codex chip (user typed "codex" in the palette). */
    function loadCodexModelsForPalette(anyChat, opts) {
        return agentControls.loadCodexModelsForPalette(anyChat, opts);
    }

    function persistCodexModelSelection(modelId) {
        return agentControls.persistCodexModelSelection(modelId);
    }

    function buildCodexModelPaletteItems(filterLower, opts) {
        return agentControls.buildCodexModelPaletteItems(filterLower, opts);
    }

    function seedCodexSupplementFromSessionData(data, sessionId) {
        return agentControls.seedCodexSupplementFromSessionData(data, sessionId);
    }

    function loadCodexEffortForPalette() {
        return agentControls.loadCodexEffortForPalette();
    }

    /**
     * Resolve Codex's effective effort before snapshotting a first-turn user
     * badge. The palette fetch is intentionally async; a fast Send must not
     * persist a bare Codex chip while the starred default is still loading.
     *
     * Never clobber a user pick that has not been persisted yet (new chat /
     * no session id). That used to overwrite `xhigh` with the starred `low`
     * right before the badge snapshot, then flush the corrupted value on adopt.
     */
    async function ensureCodexEffortForSend(message) {
        return agentControls.ensureCodexEffortForSend(message);
    }

    /**
     * First-turn model/effort picks happen before the server mints a session
     * id. Include them on `/api/chat` so the server can pin them before the
     * user-badge snapshot and agent run (the post-adopt POST races both).
     */
    /**
     * Send-time model/effort identity for this POST.
     *
     * Always include the active harness's current composer pins (not only
     * *Dirty). Dirty-only attach missed "palette shows max, dirty already
     * cleared" and raced the user-badge snapshot against a later pin POST.
     * Other agents still attach when dirty so a pre-session pick is not lost.
     */
    function attachAgentIdentityToRequest(requestBody) {
        return agentControls.attachAgentIdentityToRequest(requestBody);
    }

    function persistCodexEffortSelection(effort) {
        return agentControls.persistCodexEffortSelection(effort);
    }

    function buildCodexEffortPaletteItems(filterLower) {
        return agentControls.buildCodexEffortPaletteItems(filterLower);
    }

    /** anyChat: load even without a /claude chip (user typed "claude" in the palette). */
    function loadClaudeModelsForPalette(anyChat, opts) {
        return agentControls.loadClaudeModelsForPalette(anyChat, opts);
    }

    function persistClaudeModelSelection(modelId) {
        return agentControls.persistClaudeModelSelection(modelId);
    }

    function buildClaudeModelPaletteItems(filterLower, opts) {
        return agentControls.buildClaudeModelPaletteItems(filterLower, opts);
    }

    function seedClaudeSupplementFromSessionData(data, sessionId) {
        return agentControls.seedClaudeSupplementFromSessionData(data, sessionId);
    }

    function loadClaudeEffortForPalette() {
        return agentControls.loadClaudeEffortForPalette();
    }

    function persistClaudeEffortSelection(effort) {
        return agentControls.persistClaudeEffortSelection(effort);
    }

    function buildClaudeEffortPaletteItems(filterLower) {
        return agentControls.buildClaudeEffortPaletteItems(filterLower);
    }

    /** `/restart <mode>` entries. Native Cuttle control — never an agent prompt. */
    // Slash palette controller: menu DOM, keyboard navigation, filtering, the
    // selection apply logic and the dismiss lifecycle are owned by
    // chat_slash_palette.js (CuttleChatSlashPalette). The page shares the
    // palette state so composer chips keep working, keeps the
    // `applySlashSelection` draft choke point, and passes every page-owned
    // capability in.
    const slashPalette = CuttleChatSlashPalette.create({
        win: window,
        escapeHtmlInline,
        clamp,
        availableSlashCommands,
        cursorAgentSlashCommandsForPalette,
        harnessUsageSlashCommandsForPalette,
        buildProjectPaletteItems,
        buildProjectCommandPaletteItems,
        agentControls,
        readStarredSlashPrefixes,
        readStarredProject,
        normalizeStarredProjectPath,
        isSlashCommandStarred,
        isAgentDefaultStarred,
        isProjectStarred,
        toggleStarredProject,
        toggleStarredAgentDefault,
        toggleStarredSlashCommand,
        getModelStarSpec: (cat) => MODEL_STAR_SPEC_BY_CATEGORY[cat],
        loadAgentCapabilities,
        loadCursorStarredDefault,
        loadAllStarredDefaults,
        loadSlashPaletteSupplement,
        isPaletteLoading: () => slashPaletteSupplement.loading,
        hasActiveCursorAgentChip,
        loadCursorAgentModelsForPalette,
        applySlashSelection,
        saveDraft: () => saveComposerDraft(session.current() || 'new'),
        findProjectById,
        setChatProject,
        autoResizeTextarea,
        autoResizeWelcomeTextarea,
        renderSlashChips,
        markStickyAgentCleared,
        persistStickySlashForCurrentSession,
        isOrphanedCompanionChip,
        onCursorModelPick,
    });
    slashPalette.bind();

    // History row/header markup lives in chat_history_view.js
    // (CuttleChatHistoryView.createRenderers). The page binds its
    // session/attention/project capabilities once and hands the renderers to
    // the history panel controller and the full-list paint.
    const historyView = CuttleChatHistoryView.createRenderers({
        escapeHtml,
        escapeHtmlInline,
        currentSessionId: () => session.current(),
        sessionIdsEqual,
        sessionShowsHistorySpinner,
        sessionHistoryAttentionKind,
        resolveSessionProjectInfo,
        projectChipHtml,
        historyBranchFor,
        chatTitleParts,
        historyAttentionIconHTML: serverAttention.historyAttentionIconHTML,
        openPaneEntriesForSession,
        historyOpenPaneBadgeHTML: serverAttention.historyOpenPaneBadgeHTML,
        historySubagentToggleTitle: CuttleChatHistoryView.historySubagentToggleTitle,
        formatHistoryLastMessageAge,
        resolveHistoryLastMessageMs,
        historyRunningIconHTML,
        formatChatDisplayId,
    });

    // History panel search/filter + row-menu/modal controller: query/debounce/
    // abort state, the `/project` + `/agent` filter chips and slash palette,
    // search-result paint, the active-search hard gate, and the row overflow
    // menu + rename/delete modal lifecycle live in chat_history_panel.js
    // (CuttleChatHistoryPanel). The page supplies row/header renderers, project
    // resolution, session/attention capabilities and action effects.
    const historyPanel = CuttleChatHistoryPanel.create({
        win: window,
        doc: document,
        storage: localStorage,
        fetch: (...args) => window.fetch(...args),
        escapeHtml,
        parseSessionTimestamp,
        formatChatDisplayId,
        resolveSessionProjectInfo,
        getProjects: () => projects,
        stickyTitleChipsForSession,
        parseTitleSlashChips,
        getSlashCommands: () => CuttleChatSlash.SLASH_COMMANDS,
        isAuthMode,
        getAuthSessions: () => ((window.CuttleAuth && window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || []),
        applyGeneratingFlagsFromSessions,
        isChatSessionDeleted,
        getTerminalHistoryEntries,
        createAuthHistoryItemHTML: historyView.createAuthHistoryItemHTML,
        createHistoryItemHTML: historyView.createHistoryItemHTML,
        createTerminalHistoryItemHTML: historyView.createTerminalHistoryItemHTML,
        createHistorySectionHeaderHTML: historyView.createHistorySectionHeaderHTML,
        scheduleSlashChipCompactLabels,
        renderArchivedSection,
        rememberActiveHistorySessions,
        refreshArchivedSessions,
        isArchivedSessionsLoaded: () => archivedSessionsLoaded,
        syncHistoryRunningIndicators,
        syncHistoryUnreadIndicators,
        sessionHasUnread,
        sessionHasUnreadError,
        sessionHasPausedFollowup,
        sessionHasActiveFollowup,
        refreshLocalOpenPanes,
        // Row menu + modal orchestration capabilities.
        buildSessionMenuHTML,
        refreshGitPushMenuMode: gitPush.refreshMenuMode,
        closeChatChromeMenu,
        isTerminalSessionId,
        getChatSessionDisplayTitle,
        readTerminalSessionRegistry,
        writeTerminalSessionRegistry,
        fetchWithTimeout,
        currentSessionId: () => session.current(),
        sessionIdsEqual,
        toAuthDbSessionId,
        deleteChatSession,
        persistRenamedTitle,
        refreshChatHistoryList,
        updateChatSessionTitle,
    });

    const slashCtx = slashPalette.ctx;

    function slashContextKey(textarea) {
        return slashPalette.contextKey(textarea);
    }

    function slashChipsRowId(key) {
        return slashPalette.chipsRowId(key);
    }

    function slashMenuId(key) {
        return slashPalette.menuId(key);
    }

    function hideSlashMenu(key, opts) {
        return slashPalette.hideMenu(key, opts);
    }

    function buildProjectChipHtml() {
        return projectChipHtml(currentProject, { composer: true });
    }

    /** Branch to label a history row's checkout: the bound worktree's branch. */
    function historyBranchFor(session) {
        const uid = String((session && session.checkout_uid) || '').trim();
        if (!uid || !window.CuttleChatWorktrees || !window.CuttleChatWorktrees.branchForUid) {
            return '';
        }
        return window.CuttleChatWorktrees.branchForUid(uid) || '';
    }

    function projectChipHtml(proj, opts) {
        opts = opts || {};
        if (!proj || !(proj.name || proj.path)) return '';
        const safe = escapeHtmlInline(proj.name || 'Project');
        const pathBit = proj.path || proj.name || '';
        const starred = isProjectStarred(proj);
        const showBranch = !!opts.composer || !!opts.header || !!opts.history;
        const worktreeChip = opts.worktree !== undefined
            ? (showBranch && !!opts.worktree)
            : (showBranch && !!currentCheckoutUid);
        const branch = !showBranch
            ? ''
            : (opts.branch !== undefined ? String(opts.branch || '') : currentProjectBranch());
        const branchBit = branch ? ((worktreeChip ? ' · worktree ' : ' · branch ') + branch) : '';
        const tip = escapeHtmlInline(
            (opts.composer
                ? (starred
                    ? 'Starred default project (type /project or /cd to change): '
                    : 'Working project (type /project or /cd to change): ')
                : 'Project used for this message: ') + pathBit + branchBit
        ).replace(/"/g, '&quot;');
        // One chip, one style — the project chip looks identical in the composer,
        // message headers and history rows (no per-surface variant classes).
        const headerClass = '';
        const starredClass = starred ? ' slash-command-chip--starred' : '';
        const starIcon = starred
            ? '<span class="slash-chip-star" aria-hidden="true">★</span>'
            : '';
        const branchSeg = branch
            ? '<span class="slash-chip-seg slash-chip-seg--branch" title="' +
              escapeHtmlInline(branch).replace(/"/g, '&quot;') +
              '">' +
              (worktreeChip ? '\uD83C\uDF3F ' : '') +
              escapeHtmlInline(branch) +
              '</span>'
            : '';
        const projectIcon =
            '<span class="slash-chip-icon" aria-hidden="true">\uD83D\uDCC1</span>';
        const labelHtml =
            '<span class="slash-chip-seg slash-chip-seg--project">' +
            projectIcon +
            '<span class="slash-chip-label">' + safe + '</span>' +
            '</span>' +
            branchSeg;
        const segmentedClass = ' slash-command-chip--segmented';
        return (
            '<span class="slash-command-chip slash-command-chip--project' +
            segmentedClass +
            headerClass +
            starredClass +
            '" role="img" aria-haspopup="true" aria-expanded="false" tabindex="0" aria-label="' +
            escapeHtmlInline(
                (starred ? 'Starred project: ' : 'Project: ') + (proj.name || pathBit) + branchBit
            ).replace(/"/g, '&quot;') +
            '" title="' +
            tip +
            '" data-project-chip="1" data-full-label="' +
            safe.replace(/"/g, '&quot;') +
            '" data-tip="' +
            tip +
            '">' +
            starIcon +
            labelHtml +
            '</span>'
        );
    }

    // Agent chip regexes live in chat_slash.js (single owner).

    /**
     * Sticky agent badge only (``/cursor``, ``/codex``, …). Nested cmds like
     * ``/usage`` or ``/codex model refresh`` must not match — a naive
     * ``startsWith('/codex')`` / ``startsWith(token + ' ')`` used to
     * relabel those chips as the agent badge (CH-000482 / CH-000482-1).
     * Legacy chips with ``category:'cursor'`` + ``prefix:'/usage'`` also
     * must not win.
     */
    function isStickyAgentChip(chip, agentId) {
        return CuttleChatSlash.isStickyAgentChip(chip, agentId);
    }

    function isStickyCursorAgentChip(chip) {
        return CuttleChatSlash.isStickyCursorAgentChip(chip);
    }
    function isStickyMuseAgentChip(chip) {
        return CuttleChatSlash.isStickyMuseAgentChip(chip);
    }
    function isStickyCodexAgentChip(chip) {
        return CuttleChatSlash.isStickyCodexAgentChip(chip);
    }
    function isStickyHermesAgentChip(chip) {
        return CuttleChatSlash.isStickyHermesAgentChip(chip);
    }
    function isStickyOpenCodeAgentChip(chip) {
        return CuttleChatSlash.isStickyOpenCodeAgentChip(chip);
    }

    /** Nested Cursor Agent slash (``/usage``, ``/plan``, ``/model refresh``, …). */
    function isCursorNestedCommandChip(chip) {
        return CuttleChatSlash.isCursorNestedCommandChip(chip);
    }

    /** Nested harness cmd chip (``/usage``, ``/cost``, staged ``/model refresh``, …). */
    function isHarnessNestedCommandChip(chip) {
        return CuttleChatSlash.isHarnessNestedCommandChip(chip);
    }

    function isAgentHeaderChip(chip) {
        return CuttleChatSlash.isAgentHeaderChip(chip);
    }

    /** Agent id owning a composer chip, or '' for agent-less chips. Pure. */
    function composerChipAgentId(chip) {
        return CuttleChatSlash.composerChipAgentId(chip);
    }

    /**
     * Indexes to delete when the × on chips[idx] is clicked. The agent badge
     * removes its whole group; any other chip removes only itself. Pure.
     */
    function composerChipRemovalIndexes(chips, idx) {
        return CuttleChatSlash.composerChipRemovalIndexes(chips, idx);
    }

    function sortChipsAgentThenCommand(chips) {
        return CuttleChatSlash.sortChipsAgentThenCommand(chips);
    }

    // Project mapping lives in chat_project.js (pure over explicit inputs).
    function projectFromPath(path) {
        return CuttleChatProject.projectFromPath(path, projects);
    }

    function projectFromMessageOpts(opts, projectsArg, current) {
        return CuttleChatProject.projectFromMessageOpts(
            opts,
            projectsArg !== undefined ? projectsArg : projects,
            current !== undefined ? current : currentProject
        );
    }

    function messageHeaderBadgesHtml(chipList, isError, project) {
        const projectHtml = projectChipHtml(project, { header: true });
        const ordered = sortChipsAgentThenCommand(chipList || []);
        const failed = !!isError;
        const agent = ordered.filter(isAgentHeaderChip);
        const command = ordered.filter((c) => !isAgentHeaderChip(c));
        return (
            projectHtml +
            slashCommandChipsHeaderHtml(agent, failed) +
            slashCommandChipsHeaderHtml(command, failed)
        );
    }

    /**
     * Badge for a user message that was steered into a live agent turn
     * (POST /api/chat-steer) instead of starting a new turn. Tint matches
     * the steered harness so the odd position — below the still-running
     * assistant bubble — reads as intentional.
     */
    function steerBadgeHtml(agent) {
        const id = String(agent || '').trim().toLowerCase();
        const label = { codex: 'Codex', muse: 'Muse Code', claude: 'Claude Code' }[id] || 'agent';
        const category = (id === 'codex' || id === 'muse') ? id : 'command';
        return slashCommandChipHeaderHtml(
            'steer',
            'Steered into the running ' + label + ' turn',
            category,
            false
        );
    }

    function renderSlashChips(key, textarea) {
        const row = document.getElementById(slashChipsRowId(key));
        if (!row) return;
        const _idPrev = slashPaletteSupplement.sessionIdentity;
        const _idPath = (currentProject && currentProject.path) ? String(currentProject.path) : '';
        const _idNextSid = String(session.current() != null ? session.current() : '');
        const _idNext = _idNextSid + '|' + _idPath; // (duplicate path suffix, inert) + '' + _idPath;
        if (_idPrev !== _idNext) {
            slashPaletteSupplement.sessionIdentity = _idNext;
            resetAgentSupplementForIdentity(String(_idPrev || '').split('|')[0], _idNextSid);
        }
        const chips = slashCtx[key].chips || [];
        const hasCursorSticky = chips.some((c) => isStickyCursorAgentChip(c));
        // Model picks stay in the chip list for compose (`/model <id>`) but are not
        // shown next to Cursor Agent — that badge already includes the model name.
        const visibleChips = sortChipsAgentThenCommand(hasCursorSticky
            ? chips.filter((c) => (c.category || '') !== 'cursor-model')
            : chips);
        const projChipHtml = buildProjectChipHtml();
        if (!visibleChips.length && !projChipHtml) {
            row.hidden = true;
            row.innerHTML = '';
            contextGauge.scheduleRefresh(60);
            return;
        }
        row.hidden = false;
        row.innerHTML = projChipHtml + visibleChips
            .map((chip) => {
                const cat = chip.category || 'command';
                // Only the sticky agent chip gets the agent/model badge label.
                // Nested cmds (/usage, staged /model refresh, …) keep their own
                // labels (CH-000482 — Cursor, Codex, Muse, Hermes, OpenCode).
                const isCursor = isStickyCursorAgentChip(chip);
                const preferred =
                    (slashPaletteSupplement.preferredModel || 'auto').trim() || 'auto';
                const lastRep = (slashPaletteSupplement.lastReportedModel || '').trim();
                const isMuse = isStickyMuseAgentChip(chip);
                const isHermes = isStickyHermesAgentChip(chip);
                const isOpenCode = isStickyOpenCodeAgentChip(chip);
                const isCodex = isStickyCodexAgentChip(chip);
                const isClaude = isStickyAgentChip(chip, 'claude');
                const museModel = (slashPaletteSupplement.museModel || '').trim();
                const museEffortBadge = (slashPaletteSupplement.museEffort || '').trim();
                const hermesModel = (slashPaletteSupplement.hermesModel || '').trim();
                const hermesEffortBadge = (slashPaletteSupplement.hermesEffort || '').trim();
                const opencodeModel = (slashPaletteSupplement.opencodeModel || '').trim();
                const opencodeEffortBadge = (slashPaletteSupplement.opencodeEffort || '').trim();
                const codexModel = (slashPaletteSupplement.codexModel || '').trim();
                const codexEffortBadge = (slashPaletteSupplement.codexEffort || '').trim();
                const claudeModel = (slashPaletteSupplement.claudeModel || '').trim();
                const claudeEffortBadge = (slashPaletteSupplement.claudeEffort || '').trim();
                let displayLabel = chip.label;
                if (isCursor && preferred) {
                    // Same format as bubble / queue badges — not "Cursor Agent · Auto".
                    displayLabel = cursorChipDisplayLabel(preferred);
                } else if (isMuse) {
                    displayLabel = agentModelEffortBadgeLabel(
                        'Muse Code',
                        museModel ? prettyMuseModelLabel(museModel) : '',
                        museEffortBadge
                    ) || chip.label;
                } else if (isHermes) {
                    displayLabel = agentModelEffortBadgeLabel(
                        'Hermes',
                        hermesModel ? prettyHermesModelLabel(hermesModel) : '',
                        hermesEffortBadge
                    ) || chip.label;
                } else if (isOpenCode) {
                    displayLabel = agentModelEffortBadgeLabel(
                        'OpenCode',
                        opencodeModel ? prettyOpenCodeModelLabel(opencodeModel) : '',
                        opencodeEffortBadge
                    ) || chip.label;
                } else if (isCodex) {
                    displayLabel = agentModelEffortBadgeLabel(
                        'Codex',
                        codexModel ? prettyCodexModelLabel(codexModel) : '',
                        codexEffortBadge
                    ) || chip.label;
                } else if (isClaude) {
                    displayLabel = agentModelEffortBadgeLabel(
                        'Claude Code',
                        claudeModel ? prettyClaudeModelLabel(claudeModel) : '',
                        claudeEffortBadge
                    ) || chip.label;
                }
                const safeLabel = escapeHtmlInline(displayLabel);
                const starred = isSlashCommandStarred(chip.prefix);
                let tipBase = starred
                    ? chip.prefix + ' — starred default for new chats'
                    : chip.prefix;
                if (isCursor) {
                    tipBase +=
                        ' · preferred `' +
                        preferred +
                        '`' +
                        (lastRep ? ' · last reported `' + lastRep + '`' : '') +
                        ' · /model to change';
                } else if (isMuse && museModel) {
                    tipBase += ' · model `' + museModel + '` · /muse model … to change';
                    const museEffortTip = (slashPaletteSupplement.museEffort || '').trim();
                    if (museEffortTip) tipBase += ' · effort `' + museEffortTip + '`';
                } else if (isHermes && hermesModel) {
                    tipBase += ' · model `' + hermesModel + '` · /hermes model … to change';
                    if (hermesEffortBadge) tipBase += ' · effort `' + hermesEffortBadge + '`';
                } else if (isOpenCode && opencodeModel) {
                    tipBase += ' · model `' + opencodeModel + '` · /opencode model … to change';
                    if (opencodeEffortBadge) tipBase += ' · effort `' + opencodeEffortBadge + '`';
                } else if (isCodex && codexModel) {
                    tipBase += ' · model `' + codexModel + '` · /codex model … to change';
                    if (codexEffortBadge) tipBase += ' · effort `' + codexEffortBadge + '`';
                } else if (isClaude && claudeModel) {
                    tipBase += ' · model `' + claudeModel + '` · /claude model … to change';
                    if (claudeEffortBadge) tipBase += ' · effort `' + claudeEffortBadge + '`';
                }
                const tip = escapeHtmlInline(tipBase).replace(/"/g, '&quot;');
                const catClass = slashCommandChipCategoryClass(
                    resolveSlashChipPaletteCategory(chip)
                );
                const labelInner = slashChipLabelInnerHtml(displayLabel);
                // Agent badges: multi-tone segments (model truncates; effort stays).
                // Bare agent chips still skip hyphen-chop.
                const noShortenClass = (isCursor || isMuse || isHermes || isOpenCode || isCodex || isClaude || labelInner.segmented)
                    ? ' slash-command-chip--no-shorten'
                    : '';
                const segmentedClass = labelInner.segmented
                    ? ' slash-command-chip--segmented'
                    : '';
                const starredClass = starred ? ' slash-command-chip--starred' : '';
                const starIcon = starred
                    ? '<span class="slash-chip-star" aria-hidden="true">★</span>'
                    : '';
                const aria = starred
                    ? 'Selected item (starred default): ' + displayLabel
                    : 'Selected item: ' + displayLabel;
                const safeAria = escapeHtmlInline(aria).replace(/"/g, '&quot;');
                const realIdx = chips.indexOf(chip);
                const chipMenuAttrs = composerChipAgentId(chip)
                    ? ' aria-haspopup="true" aria-expanded="false" tabindex="0"'
                    : '';
                // Segmented: per-segment titles only — no chip-wide tip dump.
                const chipTitleAttr = labelInner.segmented
                    ? ''
                    : ' title="' + tip + '"';
                const chipTipAttr = labelInner.segmented
                    ? ''
                    : ' data-tip="' + tip + '"';
                return (
                    '<span class="slash-command-chip' +
                    catClass +
                    starredClass +
                    noShortenClass +
                    segmentedClass +
                    '" role="group" aria-label="' +
                    safeAria +
                    '"' +
                    chipMenuAttrs +
                    chipTitleAttr +
                    ' data-chip-index="' +
                    realIdx +
                    '" data-full-label="' +
                    safeLabel.replace(/"/g, '&quot;') +
                    '"' +
                    chipTipAttr +
                    '>' +
                    starIcon +
                    labelInner.html +
                    '<button type="button" class="slash-chip-remove" aria-label="Remove" title="Remove" data-chip-index="' +
                    realIdx +
                    '">×</button>' +
                    '</span>'
                );
            })
            .join('');
        if (hasActiveCursorAgentChip(key)) {
            agentControls.loadCursorAgentModelsForPalette();
        }
        if (hasActiveMuseAgentChip(key)) {
            agentControls.loadMuseModelsForPalette();
            agentControls.loadMuseEffortForPalette();
        }
        if (hasActiveHermesAgentChip(key)) {
            agentControls.loadHermesModelsForPalette();
            agentControls.loadHermesEffortForPalette();
        }
        if (hasActiveOpenCodeAgentChip(key)) {
            agentControls.loadOpenCodeModelsForPalette();
            agentControls.loadOpenCodeEffortForPalette();
        }
        if (hasActiveCodexAgentChip(key)) {
            agentControls.loadCodexModelsForPalette();
            agentControls.loadCodexEffortForPalette();
        }
        if (hasActiveHarnessAgentChip('claude', key)) {
            agentControls.loadClaudeModelsForPalette();
            agentControls.loadClaudeEffortForPalette();
        }
        row.querySelectorAll('.slash-chip-remove').forEach((btn) => {
            btn.addEventListener('click', () => {
                const i = parseInt(btn.getAttribute('data-chip-index'), 10);
                if (!Number.isFinite(i)) return;
                const _victims = composerChipRemovalIndexes(slashCtx[key].chips, i);
                slashCtx[key].chips = slashCtx[key].chips.filter((_, n) => _victims.indexOf(n) === -1);
                const other = key === 'chat' ? 'welcome' : 'chat';
                slashCtx[other].chips = slashCtx[key].chips.slice();
                if (!hasStickyAgentChip()) markStickyAgentCleared(true);
                renderSlashChips(key, textarea);
                renderSlashChips(other, document.getElementById(other === 'chat' ? 'chatInput' : 'welcomeChatInput'));
                persistStickySlashForCurrentSession(true);
                saveComposerDraftControls(session.current() || 'new', key);
                if (textarea) {
                    try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                }
            });
        });
        chipConfigControl.bindRow(row, {
            chips: slashCtx[key].chips,
            composerChipAgentId,
        });
        scheduleSlashChipCompactLabels(row);
        contextGauge.scheduleRefresh(60);
    }

    // ── Chip click menus ────────────────────────────────────────────────────
    // Clicking the project chip opens a working-project picker; clicking an
    // agent chip opens an agent/model/effort picker. Anchored, body-appended
    // popover (mirrors the context-gauge popover pattern).
    // Agent model/effort catalog + setters for the chip config modal. The
    // page owns supplement state and IO; chat_chip_config.js renders them.
    function agentMenuSpec(id) {
        const S = slashPaletteSupplement;
        const models = {
            cursor: { build: () => agentControls.buildCursorModelPaletteItems('model'), load: () => agentControls.loadCursorAgentModelsForPalette(true), set: (v) => { S.preferredModel = v; beginSupplementFetch('cursorModelsGen'); }, current: () => String(S.preferredModel || 'auto').toLowerCase() },
            muse: { build: () => agentControls.buildMuseModelPaletteItems('model'), load: () => agentControls.loadMuseModelsForPalette(true), set: persistMuseModelSelection, current: () => String(S.museModel || '').toLowerCase() },
            hermes: { build: () => agentControls.buildHermesModelPaletteItems('model'), load: () => agentControls.loadHermesModelsForPalette(true), set: persistHermesModelSelection, current: () => String(S.hermesModel || '').toLowerCase() },
            opencode: { build: (opts) => agentControls.buildOpenCodeModelPaletteItems('model', opts), load: () => agentControls.loadOpenCodeModelsForPalette(true), set: persistOpenCodeModelSelection, current: () => String(S.opencodeModel || '').toLowerCase() },
            codex: { build: (opts) => agentControls.buildCodexModelPaletteItems('model', opts), load: () => agentControls.loadCodexModelsForPalette(true), set: persistCodexModelSelection, current: () => String(S.codexModel || '').toLowerCase() },
            claude: { build: (opts) => agentControls.buildClaudeModelPaletteItems('model', opts), load: () => agentControls.loadClaudeModelsForPalette(true), set: persistClaudeModelSelection, current: () => String(S.claudeModel || '').toLowerCase() },
        }[id];
        const efforts = {
            muse: { build: () => agentControls.buildMuseEffortPaletteItems('effort'), load: () => agentControls.loadMuseEffortForPalette(), set: persistMuseEffortSelection, current: () => String(S.museEffort || '').toLowerCase(), repaint: repaintMuseUserBadges },
            hermes: { build: () => agentControls.buildHermesEffortPaletteItems('effort'), load: () => agentControls.loadHermesEffortForPalette(), set: persistHermesEffortSelection, current: () => String(S.hermesEffort || '').toLowerCase(), repaint: repaintHermesUserBadges },
            opencode: { build: () => agentControls.buildOpenCodeEffortPaletteItems('effort'), load: () => agentControls.loadOpenCodeEffortForPalette(), set: persistOpenCodeEffortSelection, current: () => String(S.opencodeEffort || '').toLowerCase(), repaint: repaintOpenCodeUserBadges },
            codex: { build: () => agentControls.buildCodexEffortPaletteItems('effort'), load: () => agentControls.loadCodexEffortForPalette(), set: persistCodexEffortSelection, current: () => String(S.codexEffort || '').toLowerCase(), repaint: repaintCodexUserBadges },
            claude: { build: () => agentControls.buildClaudeEffortPaletteItems('effort'), load: () => agentControls.loadClaudeEffortForPalette(), set: persistClaudeEffortSelection, current: () => String(S.claudeEffort || '').toLowerCase(), repaint: repaintClaudeUserBadges },
        }[id];
        if (!models && !efforts) return null;
        return { models: models || null, efforts: efforts || null };
    }

    // Chip config modal — DOM/IO owned by chat_chip_config.js; the page
    // supplies state, palette catalogs/setters, and chip rendering.
    const chipConfigControl = CuttleChipConfig.create({
        escapeHtml: escapeHtmlInline,
        getProjects: () => projects,
        getCurrentProject: () => currentProject,
        isProjectStarred, findProjectById, setChatProject,
        getAgentEntries: () => CuttleChipConfig.stickyAgentEntries(
            CuttleChatSlash.SLASH_COMMANDS, composerChipAgentId),
        getAgentSpec: (id) => agentMenuSpec(String(id || '').toLowerCase()),
        applyAgent: (entry) => applyStickyAgentCommand(entry),
        rerenderChips: () => { renderSlashChips('welcome', document.getElementById('welcomeChatInput')); renderSlashChips('chat', document.getElementById('chatInput')); },
        refreshBranch: () => refreshProjectBranch(true),
        worktreesEnabled: () => !!(window.CuttleChatWorktrees && window.CuttleChatWorktrees.isEnabled()),
        getWorktrees: (projectId) => (window.CuttleChatWorktrees
            ? window.CuttleChatWorktrees.list(projectId) : Promise.resolve([])),
        createWorktree: (projectId, branch) => (window.CuttleChatWorktrees
            ? window.CuttleChatWorktrees.create(projectId, branch) : Promise.resolve(null)),
        defaultWorktreeBranch: () => (window.CuttleChatWorktrees
            ? window.CuttleChatWorktrees.defaultBranch(currentProject) : ''),
        getChatCheckout: () => currentCheckoutUid,
        setChatCheckout,
        showToast: (msg, kind) => (window.showToast || function () {})(msg, kind),
    });

    // Probe the worktrees feature once (server 404s it when disabled) so the
    // composer modal can show the Local/Worktree selector when available.
    try { if (window.CuttleChatWorktrees) window.CuttleChatWorktrees.probe(); } catch (_) {}

    function applyStickyAgentCommand(entry) {
        const chip = {
            prefix: entry.prefix,
            label: entry.label,
            category: entry.category || 'command',
        };
        ['welcome', 'chat'].forEach((k) => {
            const ctx = slashCtx[k];
            if (!ctx) return;
            ctx.chips = (ctx.chips || []).filter((c) => {
                const m = CuttleChatSlash.SLASH_COMMANDS.find((s) => s.prefix === c.prefix);
                if (m && m.stickySession) return false;
                return !isOrphanedCompanionChip(c, chip);
            });
            ctx.chips.push(chip);
        });
        markStickyAgentCleared(false);
        persistStickySlashForCurrentSession(true);
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        renderSlashChips('chat', document.getElementById('chatInput'));
    }

    // Single choke point for every palette pick (click, Enter, Tab): the pick rewrites
    // the composer programmatically (typed filter text stripped), which fires no input
    // event, so the persisted draft would keep the stale slash fragment. That fragment
    // resurrects on New Chat and pops the palette open with the previously picked model
    // as raw slash text. Persist synchronously: a debounce would lose a fast New Chat click.
    function applySlashSelection(textarea, key, idx) {
        slashPalette.applySelection(textarea, key, idx);
        try {
            saveComposerDraft(session.current() || 'new', key);
        } catch (_) {}
    }
    function syncSlashMenuFromInput(textarea) {
        return slashPalette.syncFromInput(textarea);
    }

    /** Native Cuttle control commands are handled by Cuttle itself, so a sticky
     *  agent chip must never wrap them into an agent prompt. */
    function isNativeControlCommand(text) {
        return CuttleChatSlash.isNativeControlCommand(text);
    }

    /**
     * Supervised / restart control-lane messages must never enter the pending-prompt
     * queue while a worker (or any generating turn) is active.
     */
    // Owned by chat_composer.js — DOM/state gather, domain decides.
    function isImmediateControlLaneMessage(text) {
        if (window.CuttleSupervised && typeof window.CuttleSupervised.isImmediateControlLaneMessage === 'function') {
            return window.CuttleSupervised.isImmediateControlLaneMessage(text);
        }
        return CuttleChatComposer.isImmediateControlLaneText(text);
    }

    const seenControlRequestIds = new Set();
    let inFlightControlRequestId = null;

    const isControlCommandPrefix = isNativeControlCommand;

    function composeMessageWithSlashChip(textarea) {
        const key = slashContextKey(textarea);
        const body = (textarea.value || '').trim();
        if (!key) return body;
        let chips = (slashCtx[key].chips && slashCtx[key].chips.length
            ? slashCtx[key].chips
            : null);
        if (!chips || !chips.length) {
            const other = key === 'chat' ? 'welcome' : 'chat';
            if (slashCtx[other].chips && slashCtx[other].chips.length) {
                chips = slashCtx[other].chips;
            }
        }
        // Owned by chat_composer.js — DOM/state gather, domain composes.
        return CuttleChatComposer.composeMessageWithSlashChip(
            { body, chips: chips || [] },
            { isControl: isNativeControlCommand }
        );
    }

    /**
     * True when composed text (plus optional attachments) is worth sending.
     * Sticky-agent chips alone (``/cursor``, ``/cursor /model auto``, …) are
     * not a prompt — Enter must no-op. Native control commands (``/restart``)
     * and real user text remain sendable.
     */
    // Owned by chat_composer.js — DOM/state gather, domain decides.
    function isSendableComposerMessage(message, attachments) {
        return CuttleChatComposer.isSendableComposerMessage(message, attachments, {
            stickyOf: getStickySlashCommandFromMessage,
            isControl: isNativeControlCommand,
        });
    }

    function clearAllSlashChips() {
        slashCtx.welcome.chips = [];
        slashCtx.chat.chips = [];
        hideSlashMenu('welcome');
        hideSlashMenu('chat');
        const welcomeEl = document.getElementById('welcomeChatInput');
        const chatEl = document.getElementById('chatInput');
        renderSlashChips('welcome', welcomeEl);
        renderSlashChips('chat', chatEl);
    }

    /** After send (or queueing a follow-up): keep sticky agent chips (/cursor, /claude, …);
     *  clear non-sticky chips. Mirrors welcome + chat composers. */
    // Owned by chat_composer.js — DOM/state gather, domain decides.
    function applyStickySlashAfterComposerSend(message) {
        // A native control command is a one-off; it must not evict the sticky
        // agent chip the chat was already using.
        if (isNativeControlCommand(message)) {
            const kept = (slashCtx.chat.chips || []).filter(
                (c) => !isControlCommandPrefix(c.prefix)
            );
            slashCtx.welcome.chips = kept.slice();
            slashCtx.chat.chips = kept.slice();
            hideSlashMenu('welcome');
            hideSlashMenu('chat');
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            return null;
        }
        const stickyCmd = CuttleChatComposer.stickyCommandAfterSend({ message }, {
            stickyOf: getStickySlashCommandFromMessage,
        });
        if (!stickyCmd) {
            clearAllSlashChips();
            return null;
        }
        const chip = {
            prefix: stickyCmd.prefix,
            label: stickyCmd.label,
            category: stickyCmd.category || 'command',
        };
        slashCtx.welcome.chips = [chip];
        slashCtx.chat.chips = [chip];
        hideSlashMenu('welcome');
        hideSlashMenu('chat');
        renderSlashChips('chat', document.getElementById('chatInput'));
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        persistStickySlashForCurrentSession(true);
        return stickyCmd;
    }

    /** Leading sticky slash command (e.g. /antigravity, /claude) for session persistence.
     *  Uses the full command list (not mode-filtered) so a Local-mode composer
     *  cannot accidentally drop a previously chosen cloud agent chip on send.
     *  Matches bare tokens too (``/cursor``) — compose strips the trailing space
     *  from empty-body chip sends, and must not look like a badge removal. */
    function getStickySlashCommandFromMessage(message) {
        return CuttleChatSlash.getStickySlashCommandFromMessage(message);
    }

    /** True when the API response indicates a failed sticky agent run (UI only). */
    function isStickySlashAssistantFailure(stickyCmd, data) {
        return CuttleChatSlash.isStickySlashAssistantFailure(stickyCmd, data);
    }

    function persistStickySlashForCurrentSession(publishShared = false) {
        const prefsId = session.current() || newComposerPrefsId;
        const chips = (slashCtx.chat.chips && slashCtx.chat.chips.length)
            ? slashCtx.chat.chips
            : (slashCtx.welcome.chips || []);
        // Owned by chat_composer.js — DOM/state gather, domain decides.
        const selection = CuttleChatComposer.stickySessionSelection(chips, {
            commands: CuttleChatSlash.SLASH_COMMANDS,
            liveLabel: liveAgentBadgeLabelForChip,
            paletteCategory: resolveSlashChipPaletteCategory,
            clearedPending: stickyAgentClearedPending,
        });
        if (selection.stickyChips.length) stickyAgentClearedPending = false;
        updateSessionPrefs(prefsId, selection);
        if (publishShared) publishSharedComposerSelection(selection);
    }

    // Removing the agent badge is a real choice: this chat must fall through to
    // Cuttle's router, and neither the star nor its own /cursor history may put
    // the badge back. The flag covers the window before the server has assigned
    // a session id (badge removed on the welcome composer, as in CH-000141).
    let stickyAgentClearedPending = false;

    function isStickyAgentCleared() {
        if (stickyAgentClearedPending) return true;
        const prefs = session.current() ? getSessionPrefs(session.current()) : null;
        return !!(prefs && prefs.stickyCleared);
    }

    function markStickyAgentCleared(cleared) {
        stickyAgentClearedPending = !!cleared;
        if (session.current()) {
            updateSessionPrefs(session.current(), { stickyCleared: !!cleared });
        }
    }

    function hasStickyAgentChip() {
        return CuttleChatSlash.hasStickyAgentChip(slashCtx.chat.chips, slashCtx.welcome.chips);
    }

    /** 'none' when this turn must skip the server-side sticky/starred agent. */
    function stickyAgentOverrideForRequest(message) {
        return CuttleChatSlash.stickyAgentOverrideForRequest({
            message,
            hasChip: hasStickyAgentChip(),
            cleared: isStickyAgentCleared(),
            starredPrefixes: readStarredSlashPrefixes(),
        });
    }

    /** Recover sticky agent from recent user turns when prefs were never saved / wiped. */
    function inferStickyChipsFromUserMessages(messages) {
        return CuttleChatSlash.inferStickyChipsFromUserMessages(messages);
    }

    function stickyChipsFromAssistantSlash(sc) {
        return CuttleChatSlash.stickyChipsFromAssistantSlash(sc);
    }

    function restoreWelcomeStickySlash() {
        if (getSessionPrefs(newComposerPrefsId)) {
            restoreSessionStickySlash(newComposerPrefsId, []);
        }
    }

    // Session-scoped snapshots: execution identity and next-send preferences
    // have distinct lifetimes. Canonical ids also cover CH-/db_session aliases.
    const turnBadgeBySession = new Map();
    const sharedComposerState = new Map();

    function rememberTurnBadge(messages, liveStatus) {
        // Before the new user row is saved, a local send's history poll may
        // still contain the previous turn. Its send-time snapshot stays put.
        if (isLoadingThisSession() && !(liveStatus && liveStatus.active && liveStatus.slash_command)) return;
        const meta = CuttleChatAgentModel.turnSlashFromMessages(messages, liveStatus);
        if (meta !== undefined) {
            turnBadgeBySession.set(String(canonicalizeChatSessionId(session.current()) || ''), meta);
        }
    }

    function hydrateAgentSelectionFromSessionData(data, sessionId) {
        const incoming = data && data.composer_selection;
        const unsynced = getSessionPrefs(sessionId)?.composerWrite;
        if (unsynced) {
            publishSharedComposerSelection(unsynced.selection, sessionId, unsynced.id);
            return;
        }
        const state = sharedComposerState.get(String(canonicalizeChatSessionId(sessionId)));
        // Reject stale/pending-write snapshots BEFORE seeding model/effort as
        // well as the chip. Otherwise a delayed GET could undo an accepted pick.
        if (incoming && !CuttleChatAgentModel.shouldAdoptComposerSelection(incoming, state)) return;
        seedMuseSupplementFromSessionData(data, sessionId);
        seedHermesSupplementFromSessionData(data, sessionId);
        seedOpenCodeSupplementFromSessionData(data, sessionId);
        seedCodexSupplementFromSessionData(data, sessionId);
        seedClaudeSupplementFromSessionData(data, sessionId);
        applySharedComposerSelection(data, sessionId);
    }

    function applySharedComposerSelection(data, sessionId) {
        const incoming = data && data.composer_selection;
        const key = String(canonicalizeChatSessionId(sessionId));
        const state = sharedComposerState.get(key);
        if (!CuttleChatAgentModel.shouldAdoptComposerSelection(incoming, state)) return;
        const revision = Number(incoming.revision || 0);
        const pins = JSON.stringify(data.agent_pins || {});
        if (state && state.appliedRevision === revision && state.pins === pins) return;
        sharedComposerState.set(key, { ...state, revision, appliedRevision: revision, pins });
        const prefs = getSessionPrefs(sessionId);
        // Owned by chat_composer.js — server snapshot in, prefs patch out.
        const patch = CuttleChatComposer.sharedSelectionPrefsPatch(
            incoming,
            prefs && prefs.composerDraft,
            { commands: CuttleChatSlash.SLASH_COMMANDS }
        );
        updateSessionPrefs(sessionId, patch);
        restoreSessionStickySlash(sessionId, []);
    }

    function publishSharedComposerSelection(selection, sessionId = session.current(), retryId = null) {
        const sid = toAuthDbSessionId(sessionId);
        if (!isAuthMode() || !sid || !/^\d+$/.test(sid)) return;
        const key = String(canonicalizeChatSessionId(sessionId));
        const state = sharedComposerState.get(key) || { revision: 0 };
        if (retryId && state.pending) return;
        const writeId = retryId || Date.now() + ':' + Math.random().toString(36).slice(2);
        if (!retryId) updateSessionPrefs(sessionId, {composerWrite: {selection, id: writeId}});
        state.unsynced = true;
        state.pending = (state.pending || 0) + 1;
        sharedComposerState.set(key, state);
        // Serialize local writes so a slow earlier choice cannot win later.
        state.write = (state.write || Promise.resolve()).catch(() => {}).then(async () => {
            const response = await fetch('/api/auth/sessions/' + sid + '/composer', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(selection),
            });
            if (!response.ok) throw new Error('Composer sync HTTP ' + response.status);
            const result = await response.json();
            if (result.success && result.composer_selection) {
                state.revision = Math.max(state.revision, Number(result.composer_selection.revision || 0));
                if (getSessionPrefs(sessionId)?.composerWrite?.id === writeId) {
                    updateSessionPrefs(sessionId, {composerWrite: null});
                    state.unsynced = false;
                }
            }
        }).catch((error) => {
            console.warn('[Cuttle Chat] composer selection sync failed:', error);
        }).finally(() => { state.pending--; });
    }

    function restoreSessionStickySlash(sessionId, messages) {
        // Owned by chat_composer.js — prefs/history snapshot in, chips out.
        const restored = CuttleChatComposer.stickyChipsToRestore(
            getSessionPrefs(sessionId),
            messages,
            { infer: inferStickyChipsFromUserMessages }
        );
        stickyAgentClearedPending = restored.cleared;
        if (restored.inferred && sessionId != null && sessionId !== '') {
            updateSessionPrefs(sessionId, { stickyChips: restored.chips });
        }
        slashCtx.welcome.chips = restored.chips.slice();
        slashCtx.chat.chips = restored.chips.slice();
        restoreComposerDraftControls(sessionId);
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        renderSlashChips('chat', document.getElementById('chatInput'));
        // Hub (phone) may have painted the working bubble before this restore —
        // re-apply agent + project chips onto any existing typing indicator.
        refreshTypingIndicatorHeaderBadges();
    }

    function persistProjectForCurrentSession(opts) {
        opts = opts || {};
        if (!session.current()) return;
        const bare = toAuthDbSessionId(session.current());
        const payload = {
            projectId: currentProject ? currentProject.id : null,
            projectPath: currentProject ? currentProject.path : '',
            projectName: currentProject ? currentProject.name : '',
            checkoutUid: currentCheckoutUid || null,
            checkoutPath: currentCheckoutPath || null,
        };
        // Keep hydrate/list caches aligned so a later reconcile cannot resurrect
        // the previous project (CH-000204: EP chip → Cuttle on send).
        rememberSessionProject(session.current(), currentProject);
        // Write under both display and bare keys so reopen / history grouping stay in sync.
        updateSessionPrefs(session.current(), payload);
        if (bare && String(bare) !== String(session.current())) {
            updateSessionPrefs(bare, payload);
        }
        if (opts.localOnly) return;
        if (isAuthMode() && bare) {
            // Durable server copy — survives Electron cache clears and other devices.
            sessionMutations.enqueue(String(bare) + 'project', () => fetch(`/api/auth/sessions/${encodeURIComponent(bare)}`, {
                method: 'PATCH',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: payload.projectId,
                    project_name: payload.projectName,
                    project_path: payload.projectPath,
                    checkout_uid: payload.checkoutUid,
                }),
            })).catch((e) => console.warn('[Cuttle Chat] persist session project failed:', e));
        }
    }

    function handleSlashKeyDown(event, textarea) {
        return slashPalette.handleKeyDown(event, textarea);
    }

    /**
     * Parse one slash / skill prefix from the start of `s`. Used for display + assistant reply chips.
     * Mirrors composeMessageWithSlashChip() output shapes.
     */
    /** Page-owned resolvers for the slash decision layer (chat_slash.js). */
    function slashParseDeps() {
        return {
            modelLabel: (mid) => preferredModelLabel(mid),
        };
    }

    function parseStoredSlashCommandHead(s) {
        return CuttleChatSlash.parseStoredSlashCommandHead(s, slashParseDeps());
    }

    /** Full message: first command(s), then optional freeform body. Supports multiple consecutive commands. */
    function parseStoredSlashCommandMessage(raw) {
        return CuttleChatSlash.parseStoredSlashCommandMessage(raw, slashParseDeps());
    }

    /**
     * Palette color class for a chip. Agent harness badges (Cursor / Muse /
     * Hermes / OpenCode) share one look whether the stored category is the
     * agent id, a model/effort subcommand, or a legacy bare "command".
     */
    function slashCommandChipCategoryClass(category) {
        const cat = category || 'command';
        if (cat === 'skill') return ' slash-command-chip--palette-skill';
        if (cat === 'project-cmd') return ' slash-command-chip--palette-project-cmd';
        if (cat === 'cursor' || cat === 'cursor-model' || cat === 'cursor-cmd') {
            return ' slash-command-chip--palette-cursor';
        }
        if (cat === 'muse' || cat === 'muse-model' || cat === 'muse-effort' || cat === 'muse-cmd') {
            return ' slash-command-chip--palette-muse';
        }
        if (cat === 'hermes' || cat === 'hermes-model' || cat === 'hermes-effort' || cat === 'hermes-cmd') {
            return ' slash-command-chip--palette-hermes';
        }
        if (cat === 'opencode' || cat === 'opencode-model' || cat === 'opencode-effort' || cat === 'opencode-cmd') {
            return ' slash-command-chip--palette-opencode';
        }
        if (cat === 'codex' || cat === 'codex-model' || cat === 'codex-effort' || cat === 'codex-cmd') {
            return ' slash-command-chip--palette-codex';
        }
        if (cat === 'warning') {
            return ' slash-command-chip--palette-warning';
        }
        return ' slash-command-chip--palette-command';
    }

    /**
     * Resolve the palette category used for chip chrome. Prefers the agent
     * harness owning the chip (prefix / label / category) so history sticky
     * rows and legacy "command" Muse badges match in-chat agent styling.
     */
    function resolveSlashChipPaletteCategory(chip) {
        if (!chip || typeof chip !== 'object') {
            return String(chip || 'command');
        }
        const cat = String(chip.category || '').toLowerCase();
        // Keep model/effort subcommand categories — they still map to the
        // same harness palette class.
        if (
            cat === 'cursor-model' || cat === 'cursor-cmd'
            || cat === 'muse-model' || cat === 'muse-effort' || cat === 'muse-cmd'
            || cat === 'hermes-model' || cat === 'hermes-effort' || cat === 'hermes-cmd'
            || cat === 'opencode-model' || cat === 'opencode-effort' || cat === 'opencode-cmd'
            || cat === 'codex-model' || cat === 'codex-effort' || cat === 'codex-cmd'
            || cat === 'skill' || cat === 'project-cmd'
            || cat === 'project' || cat === 'warning'
        ) {
            return cat;
        }
        const agentId = composerChipAgentId(chip);
        if (agentId === 'cursor' || agentId === 'muse'
            || agentId === 'hermes' || agentId === 'opencode' || agentId === 'codex') {
            return agentId;
        }
        return cat || 'command';
    }

    /** Drop " - model" (or any hyphen suffix) for compact chip labels. */
    function slashChipLabelBeforeHyphen(label) {
        const s = String(label || '').trim();
        if (!s) return s;
        const m = s.match(/^(.+?)\s*[-–—]\s+.+/);
        return m ? m[1].trim() : s;
    }

    function slashChipHasLeadingGlyph(chip) {
        if (!chip) return false;
        return !!(
            chip.querySelector('.slash-chip-star') ||
            chip.querySelector('.slash-chip-icon') ||
            chip.querySelector('.slash-chip-error-icon')
        );
    }

    function slashChipLabelOverflows(chip, labelEl) {
        if (!chip || !labelEl || labelEl.hidden) return false;
        if (labelEl.scrollWidth > labelEl.clientWidth + 1) return true;
        if (chip.scrollWidth > chip.clientWidth + 1) return true;
        return false;
    }

    function slashChipHoverTitle(fullLabel, tip) {
        const full = String(fullLabel || '').trim();
        const base = String(tip || '').trim();
        if (!full) return base;
        if (!base || base === full) return full;
        if (base.indexOf(full) !== -1) return base;
        return full + ' — ' + base;
    }

    /**
     * Progressive compact labels: full → before hyphen → star/icon only.
     * Hover title keeps the full label whenever we shortened.
     * Segmented agent badges skip hyphen-chop; CSS ellipsis truncates model only.
     */
    function applySlashChipCompactLabels(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const chips = scope.querySelectorAll
            ? scope.querySelectorAll('.slash-command-chip')
            : [];
        chips.forEach((chip) => {
            // Status chips own their full-message tooltip; CSS truncates only the message.
            if (chip.classList.contains('cuttle-status-chip')) return;
            const tip = chip.dataset.tip || chip.getAttribute('title') || '';
            if (!chip.dataset.tip && tip) {
                chip.dataset.tip = tip;
            }
            const full = chip.dataset.fullLabel || '';

            if (chip.classList.contains('slash-command-chip--segmented')) {
                // Per-segment titles only — never put the long chip tip on the shell.
                chip.removeAttribute('title');
                try { delete chip.dataset.tip; } catch (_) { chip.dataset.tip = ''; }
                chip.querySelectorAll('.slash-chip-seg').forEach((seg) => {
                    const text = (seg.textContent || '').trim();
                    if (text) seg.title = text;
                });
                return;
            }

            const labelEl = chip.querySelector('.slash-chip-label');
            if (!labelEl) return;
            if (!chip.dataset.fullLabel) {
                chip.dataset.fullLabel = (labelEl.textContent || '').trim();
            }
            const fullLabel = chip.dataset.fullLabel || '';
            const noShorten = chip.classList.contains('slash-command-chip--no-shorten');
            chip.classList.remove('slash-command-chip--icon-only');
            labelEl.hidden = false;
            labelEl.style.display = '';
            labelEl.textContent = fullLabel;

            const overflowing = slashChipLabelOverflows(chip, labelEl);
            let truncated = false;
            if (!noShorten && overflowing) {
                const short = slashChipLabelBeforeHyphen(fullLabel);
                if (short && short !== fullLabel) {
                    labelEl.textContent = short;
                    truncated = true;
                }
                if (slashChipLabelOverflows(chip, labelEl) && slashChipHasLeadingGlyph(chip)) {
                    chip.classList.add('slash-command-chip--icon-only');
                    labelEl.hidden = true;
                    truncated = true;
                } else if (slashChipLabelOverflows(chip, labelEl)) {
                    truncated = true;
                }
            }

            if ((truncated || (noShorten && overflowing)) && fullLabel) {
                chip.title = slashChipHoverTitle(fullLabel, tip);
            } else if (tip) {
                chip.title = tip;
            } else if (fullLabel) {
                chip.title = fullLabel;
            }
        });
    }

    let _slashChipCompactRaf = 0;
    function scheduleSlashChipCompactLabels(/* root ignored — always refresh all */) {
        if (_slashChipCompactRaf) cancelAnimationFrame(_slashChipCompactRaf);
        _slashChipCompactRaf = requestAnimationFrame(() => {
            _slashChipCompactRaf = 0;
            applySlashChipCompactLabels(document);
        });
    }

    function slashCommandChipHistoryHtml(label, metaPrefix, category) {
        const chip = {
            label: label,
            meta: metaPrefix,
            category: category,
            prefix: metaPrefix,
        };
        const fullAttr = escapeHtmlInline(label).replace(/"/g, '&quot;');
        const tip = escapeHtmlInline(metaPrefix).replace(/"/g, '&quot;');
        const aria = escapeHtmlInline('Command: ' + metaPrefix).replace(/"/g, '&quot;');
        const catClass = slashCommandChipCategoryClass(resolveSlashChipPaletteCategory(chip));
        const labelInner = slashChipLabelInnerHtml(label);
        const segmentedClass = labelInner.segmented
            ? ' slash-command-chip--segmented'
            : '';
        const noShortenClass = slashChipShouldNotShorten(label)
            ? ' slash-command-chip--no-shorten'
            : '';
        const chipTitleAttr = labelInner.segmented ? '' : ' title="' + tip + '"';
        const chipTipAttr = labelInner.segmented ? '' : ' data-tip="' + tip + '"';
        return (
            '<span class="slash-command-chip slash-command-chip--history' +
            catClass +
            noShortenClass +
            segmentedClass +
            '" role="img" aria-label="' +
            aria +
            '"' +
            chipTitleAttr +
            ' data-full-label="' +
            fullAttr +
            '"' +
            chipTipAttr +
            '>' +
            labelInner.html +
            '</span>'
        );
    }

    const SLASH_CHIP_ERR_ICON =
        '<svg class="slash-chip-error-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>';

    /** Compact chip(s) beside assistant name (reply to slash commands). */
    function slashCommandChipHeaderHtml(label, metaPrefix, category, isError) {
        const chip = {
            label: label,
            meta: metaPrefix,
            category: category,
            prefix: metaPrefix,
        };
        const fullAttr = escapeHtmlInline(label).replace(/"/g, '&quot;');
        const tip = escapeHtmlInline(metaPrefix).replace(/"/g, '&quot;');
        const aria = escapeHtmlInline(
            (isError ? 'Failed command: ' : 'Command: ') + metaPrefix
        ).replace(/"/g, '&quot;');
        const catClass = slashCommandChipCategoryClass(resolveSlashChipPaletteCategory(chip));
        const errClass = isError ? ' slash-command-chip--error' : '';
        const icon = isError ? SLASH_CHIP_ERR_ICON : '';
        const labelInner = slashChipLabelInnerHtml(label);
        // Same as the composer: segmented agent badges truncate only the model.
        const noShortenClass = slashChipShouldNotShorten(label)
            ? ' slash-command-chip--no-shorten'
            : '';
        const segmentedClass = labelInner.segmented
            ? ' slash-command-chip--segmented'
            : '';
        const chipTitleAttr = labelInner.segmented ? '' : ' title="' + tip + '"';
        const chipTipAttr = labelInner.segmented ? '' : ' data-tip="' + tip + '"';
        return (
            '<span class="slash-command-chip slash-command-chip--header' +
            catClass +
            errClass +
            noShortenClass +
            segmentedClass +
            '" role="img" aria-label="' +
            aria +
            '"' +
            chipTitleAttr +
            ' data-full-label="' +
            fullAttr +
            '"' +
            chipTipAttr +
            '>' +
            icon +
            labelInner.html +
            '</span>'
        );
    }

    function slashCommandChipsHeaderHtml(chipList, isError) {
        if (!chipList || !chipList.length) return '';
        const failed = !!isError;
        return chipList.map((c) => slashCommandChipHeaderHtml(c.label, c.meta, c.category, failed)).join('');
    }

    function prettyCursorModelLabel(model) {
        return agentControls.prettyCursorModelLabel(model);
    }

    /** True for the unpinned/Auto model id (or an empty value meaning "not pinned"). */
    function isCursorAutoModelName(model) {
        return agentControls.isCursorAutoModelName(model);
    }

    /** Canonical Cursor badge text everywhere (composer, queue, bubbles): "Cursor - Auto". */
    function cursorChipDisplayLabel(modelId) {
        return agentControls.cursorChipDisplayLabel(modelId);
    }

    /** Look up the human-readable label for a model ID from the loaded models list. */
    function preferredModelLabel(modelId) {
        return agentControls.preferredModelLabel(modelId);
    }

    /** Cursor Agent sticky or a model *setting* pick — not nested one-shots. */
    function isCursorRelatedSlashChip(c) {
        return CuttleChatSlash.isCursorRelatedSlashChip(c);
    }

    /**
     * One Cursor badge only — picking `/model auto` while Cursor Agent is sticky
     * used to leave two chips that both became "Cursor - Auto" on the reply.
     *
     * When collapsing history chips with no replacement /model hint, keep the
     * existing "Cursor - …" label. Re-deriving from preferredModel (still
     * 'auto' until /api/cursor-agent/models returns) was wiping Sonnet badges.
     */
    function collapseCursorSlashChips(chips, replacement) {
        return agentControls.collapseCursorSlashChips(chips, replacement);
    }

    /** True when sessionId refers to the currently open chat (live pins apply). */
    function isCurrentHistorySession(sessionId) {
        if (sessionId == null || sessionId === '' || session.current() == null || session.current() === '') {
            return false;
        }
        if (String(sessionId) === String(session.current())) return true;
        try {
            const a = toAuthDbSessionId(sessionId);
            const b = toAuthDbSessionId(session.current());
            return !!(a && b && String(a) === String(b));
        } catch (_) {
            return false;
        }
    }

    /**
     * Live model/effort label for a sticky agent chip (composer + history).
     * Returns '' when pins are unknown so callers keep the stored label.
     */
    function liveAgentBadgeLabelForChip(chip) {
        const id = composerChipAgentId(chip);
        if (id === 'cursor') {
            return cursorChipDisplayLabel(slashPaletteSupplement.preferredModel || 'auto');
        }
        if (id === 'muse') {
            const model = String(slashPaletteSupplement.museModel || '').trim();
            const effort = String(slashPaletteSupplement.museEffort || '').trim();
            return agentModelEffortBadgeLabel(
                'Muse Code',
                model ? prettyMuseModelLabel(model) : '',
                effort
            );
        }
        if (id === 'hermes') {
            const model = String(slashPaletteSupplement.hermesModel || '').trim();
            const effort = String(slashPaletteSupplement.hermesEffort || '').trim();
            return agentModelEffortBadgeLabel(
                'Hermes',
                model ? prettyHermesModelLabel(model) : '',
                effort
            );
        }
        if (id === 'opencode') {
            const model = String(slashPaletteSupplement.opencodeModel || '').trim();
            const effort = String(slashPaletteSupplement.opencodeEffort || '').trim();
            return agentModelEffortBadgeLabel(
                'OpenCode',
                model ? prettyOpenCodeModelLabel(model) : '',
                effort
            );
        }
        if (id === 'codex') {
            const model = String(slashPaletteSupplement.codexModel || '').trim();
            const effort = String(slashPaletteSupplement.codexEffort || '').trim();
            return agentModelEffortBadgeLabel(
                'Codex',
                model ? prettyCodexModelLabel(model) : '',
                effort
            );
        }
        if (id === 'claude') {
            const model = String(slashPaletteSupplement.claudeModel || '').trim();
            const effort = String(slashPaletteSupplement.claudeEffort || '').trim();
            return agentModelEffortBadgeLabel(
                'Claude Code',
                model ? prettyClaudeModelLabel(model) : '',
                effort
            );
        }
        return '';
    }

    function isBareAgentChipLabel(chip) {
        const id = composerChipAgentId(chip);
        const lab = String((chip && chip.label) || '').trim();
        if (!id || !lab) return false;
        if (id === 'cursor') return /^cursor(\s+agent)?$/i.test(lab);
        if (id === 'muse') return /^muse code$/i.test(lab);
        if (id === 'hermes') return /^hermes(\s+agent)?$/i.test(lab);
        if (id === 'opencode') return /^opencode$/i.test(lab);
        if (id === 'codex') return /^codex$/i.test(lab);
        if (id === 'claude') return /^claude(\s+code)?$/i.test(lab);
        return false;
    }

    /**
     * Unify agent chips for history / queue / titles: Cursor collapse plus
     * Muse/Hermes/OpenCode/Codex category + label enrichment (same chrome as chat).
     * Bare title-parsed chips ("codex") upgrade from live pins (open chat) or
     * that session's sticky prefs (every other row) so the history panel shows
     * each session's current agent/model/effort.
     */
    function normalizeAgentSlashChips(chips, sessionId) {
        let list = collapseCursorSlashChips(Array.isArray(chips) ? chips : []);
        const useLive = isCurrentHistorySession(sessionId);
        const stickyByKey = new Map();
        stickyTitleChipsForSession(sessionId).forEach((c) => {
            const key = titleChipKey(c);
            if (key) stickyByKey.set(key, c);
        });
        return list.map((c) => {
            if (!c) return c;
            const id = composerChipAgentId(c);
            const paletteCat = resolveSlashChipPaletteCategory(c);
            let label = String(c.label || '').trim();
            if (id && isBareAgentChipLabel(c)) {
                if (useLive) {
                    const live = liveAgentBadgeLabelForChip(c);
                    if (live) label = live;
                } else {
                    const sticky = stickyByKey.get(titleChipKey(c));
                    const stickyLab = sticky && String(sticky.label || '').trim();
                    if (stickyLab && !isBareAgentChipLabel(sticky)) label = stickyLab;
                }
            }
            const nextCat = (paletteCat === 'cursor-model' || paletteCat === 'cursor-cmd'
                || paletteCat === 'muse-model' || paletteCat === 'muse-effort' || paletteCat === 'muse-cmd'
                || paletteCat === 'hermes-model' || paletteCat === 'hermes-effort' || paletteCat === 'hermes-cmd'
                || paletteCat === 'opencode-model' || paletteCat === 'opencode-effort' || paletteCat === 'opencode-cmd'
                || paletteCat === 'codex-model' || paletteCat === 'codex-effort' || paletteCat === 'codex-cmd'
                || paletteCat === 'claude-model' || paletteCat === 'claude-effort' || paletteCat === 'claude-cmd')
                ? paletteCat
                : (id || paletteCat || c.category || 'command');
            if (label === String(c.label || '').trim() && nextCat === c.category) return c;
            return Object.assign({}, c, {
                label: label || c.label,
                category: nextCat,
            });
        });
    }

    const TITLE_SLASH_SKIP = { help: 1, project: 1, cd: 1 };

    function titleChipKey(c) {
        return CuttleChatSlash.titleChipKey(c);
    }

    function parseProjectOrGenericSlashHead(rest) {
        return CuttleChatSlash.parseProjectOrGenericSlashHead(
            rest, buildProjectCommandPaletteItems());
    }

    function parseTitleSlashChips(raw) {
        return CuttleChatSlash.parseTitleSlashChips(raw, Object.assign(
            slashParseDeps(), { projectCmdItems: buildProjectCommandPaletteItems() }));
    }

    function sessionPrefsForHistory(sessionId) {
        if (sessionId == null || sessionId === '') return null;
        const bare = toAuthDbSessionId(sessionId);
        const keys = [sessionId, canonicalizeChatSessionId(sessionId), bare];
        if (bare) keys.push('db_session_' + bare);
        const seen = new Set();
        for (const k of keys) {
            if (k == null || k === '') continue;
            const s = String(k);
            if (seen.has(s)) continue;
            seen.add(s);
            const p = getSessionPrefs(s);
            if (p) return p;
        }
        return null;
    }

    function stickyTitleChipsForSession(sessionId) {
        const prefs = sessionPrefsForHistory(sessionId);
        const sticky = prefs && Array.isArray(prefs.stickyChips) ? prefs.stickyChips : [];
        return sticky.map((c) => ({
            label: c.label || String(c.prefix || '').replace(/^\//, '').trim() || 'cmd',
            meta: String(c.prefix || c.meta || '').replace(/\s+$/, '') || c.label,
            category: c.category || 'command',
            prefix: c.prefix,
        })).filter((c) => c.meta);
    }

    /**
     * Split a stored chat name into the chips row (meta line) and the plain
     * name shown underneath it.
     */
    function chatTitleParts(title, sessionId) {
        const parsed = parseTitleSlashChips(title);
        let chips = parsed.chips.slice();
        let body = String(parsed.body || '').trim();
        if (!body && !chips.length) body = String(title || '').trim();

        const have = new Map();
        chips.forEach((c, i) => {
            const key = titleChipKey(c);
            if (key) have.set(key, i);
        });
        stickyTitleChipsForSession(sessionId).forEach((c) => {
            const key = titleChipKey(c);
            if (!key) return;
            if (have.has(key)) {
                // Title `/codex Foo` parses a bare "codex" chip that would
                // shadow the session sticky "Codex - model · effort". Upgrade
                // in place so the history panel matches composer state.
                const idx = have.get(key);
                const existing = chips[idx];
                if (existing && isBareAgentChipLabel(existing)
                    && c.label && !isBareAgentChipLabel(c)) {
                    chips[idx] = Object.assign({}, existing, {
                        label: c.label,
                        category: c.category || existing.category,
                        meta: c.meta || existing.meta,
                        prefix: c.prefix || existing.prefix,
                    });
                }
                return;
            }
            have.set(key, chips.length);
            chips.push(c);
        });

        chips = normalizeAgentSlashChips(chips, sessionId);
        const chipsHtml = chips.length
            ? ('<span class="slash-chips-inline">'
                + chips.map((c) => slashCommandChipHistoryHtml(
                    c.label,
                    c.meta || c.prefix || c.label,
                    c.category
                )).join('')
                + '</span>')
            : '';
        // A name that was nothing but slash tokens already shows as chips.
        const name = body || (chips.length ? 'Chat' : String(title || '').trim()) || 'Chat';
        return { chipsHtml, titleHtml: escapeHtml(name) };
    }

    /**
     * Re-paint agent chips in the history panel (e.g. after Muse model pin
     * loads) so sidebar badges stay on the same unified path as composer/chat.
     */
    function refreshHistoryAgentChips() {
        if (typeof document === 'undefined' || !document.querySelectorAll) return;
        document.querySelectorAll('.chat-history-item[data-session-id]').forEach((item) => {
            if (item.classList.contains('is-terminal')) return;
            const sid = item.getAttribute('data-session-id');
            if (sid == null || sid === '') return;
            const rawTitle = String(
                item.getAttribute('data-chat-title')
                || (item.querySelector('.chat-title') || {}).textContent
                || ''
            ).trim();
            let chipsHtml = '';
            try {
                chipsHtml = (chatTitleParts(rawTitle || 'Chat', sid).chipsHtml) || '';
            } catch (_) {
                return;
            }
            const meta = historyItemMetaEl(item);
            if (!meta) return;
            let inline = meta.querySelector('.slash-chips-inline');
            if (!chipsHtml) {
                if (inline) inline.remove();
                return;
            }
            const wrap = document.createElement('div');
            wrap.innerHTML = chipsHtml;
            const next = wrap.firstElementChild;
            if (!next) return;
            if (inline) inline.replaceWith(next);
            else meta.insertBefore(next, meta.firstChild);
        });
        scheduleSlashChipCompactLabels(document.getElementById('chatHistory'));
    }

    /** Meta line element, created on demand so live indicator updates have a home. */
    function historyItemMetaEl(item) {
        if (!item) return null;
        let meta = item.querySelector('.history-item-meta');
        if (meta) return meta;
        meta = document.createElement('div');
        meta.className = 'history-item-meta';
        const body = item.querySelector('.history-item-body');
        if (body) body.insertBefore(meta, body.firstChild);
        else item.insertBefore(meta, item.firstChild);
        return meta;
    }

    /**
     * Derive the chip(s) to show in the typing indicator (before the response arrives).
     * Uses preferred model from the palette rather than a hardcoded 'Auto'.
     */
    function pendingSlashForTypingIndicator(replySlash, stickyCmd) {
        const isCursor =
            (stickyCmd && String(stickyCmd.prefix || '').toLowerCase().startsWith('/cursor'))
            || (
                replySlash
                && Array.isArray(replySlash.chips)
                && replySlash.chips.some(isCursorRelatedSlashChip)
            );
        // Non-Cursor agents (/muse, /codex, /antigravity, …) already carry their own
        // chip; grafting a synthetic Cursor run added a stray "Cursor - Auto"
        // badge next to e.g. "Muse Code" while generating.
        if (!isCursor) {
            let slash = enrichSlashCommandWithMuseModel(
                replySlash || null,
                slashPaletteSupplement.museModel,
                slashPaletteSupplement.museEffort
            );
            slash = enrichSlashCommandWithHermesModel(
                slash,
                slashPaletteSupplement.hermesModel,
                slashPaletteSupplement.hermesEffort
            );
            slash = enrichSlashCommandWithOpenCodeModel(
                slash,
                slashPaletteSupplement.opencodeModel,
                slashPaletteSupplement.opencodeEffort
            );
            slash = enrichSlashCommandWithCodexModel(
                slash,
                slashPaletteSupplement.codexModel,
                slashPaletteSupplement.codexEffort
            );
            slash = enrichSlashCommandWithClaudeModel(
                slash,
                slashPaletteSupplement.claudeModel,
                slashPaletteSupplement.claudeEffort
            );
            return slash;
        }
        const preferred = (slashPaletteSupplement.preferredModel || 'auto').trim() || 'auto';
        // Synthetic run carries the requested model so enrichSlashCommandWithCursorRun
        // can look up the friendly label from the models list.
        const syntheticRun = { requested_model: preferred };
        return enrichSlashCommandWithCursorRun(replySlash, syntheticRun, stickyCmd);
    }

    /**
     * Active sticky agent composer chip (Cursor / Muse / Codex / …), or null.
     * Used when recreating the typing / remote-waiting bubble mid-run.
     */
    function activeStickyAgentChip() {
        return CuttleChatSlash.activeStickyAgentChip(slashCtx.chat.chips, slashCtx.welcome.chips);
    }

    /** Frozen turn identity, also used when recreating typing UI after navigation. */
    function currentTypingSlashMeta() {
        return turnBadgeBySession.get(String(canonicalizeChatSessionId(session.current()) || '')) || null;
    }

    function syncPreferredModelFromResponse(data) {
        return agentControls.syncPreferredModelFromResponse(data);
    }

    /** Build / refresh assistant header chips using Cursor run meta (model badge). */
    function enrichSlashCommandWithCursorRun(replySlash, cursorRun, stickyCmd) {
        return agentControls.enrichSlashCommandWithCursorRun(replySlash, cursorRun, stickyCmd);
    }

    function prettyMuseModelLabel(model) {
        return agentControls.prettyMuseModelLabel(model);
    }

    /** Full palette label for a Muse model id (e.g. toasts). */
    function museModelLabel(model) {
        return agentControls.museModelLabel(model);
    }

    /**
     * Shared "Agent - model · effort" badge text for composer + bubble chips.
     * Returns '' when neither model nor effort is available (caller keeps stock label).
     */
    function agentModelEffortBadgeLabel(agentName, modelLabel, effort) {
        return agentControls.agentModelEffortBadgeLabel(agentName, modelLabel, effort);
    }

    /**
     * Split an agent badge label into harness / model / effort for the
     * multi-tone chip. Effort may be absent (Cursor bakes it into the model
     * name). Returns null for bare agent names with nothing to segment.
     *
     * Formats: "Agent - model · effort" | "Agent - model" | "Agent · effort"
     */
    function parseAgentBadgeSegments(label) {
        const s = String(label || '').trim();
        if (!s) return null;
        const hyphen = s.match(/^(.+?)\s+[-–—]\s+(.+)$/);
        if (hyphen) {
            const agent = hyphen[1].trim();
            const rest = hyphen[2].trim();
            if (!agent || !rest) return null;
            const mid = rest.match(/^(.+?)\s+·\s+(.+)$/);
            if (mid) {
                const model = mid[1].trim();
                const effort = mid[2].trim();
                if (!model && !effort) return null;
                return { agent: agent, model: model, effort: effort };
            }
            return { agent: agent, model: rest, effort: '' };
        }
        const dot = s.match(/^(.+?)\s+·\s+(.+)$/);
        if (dot) {
            const agent = dot[1].trim();
            const effort = dot[2].trim();
            if (!agent || !effort) return null;
            return { agent: agent, model: '', effort: effort };
        }
        return null;
    }

    /**
     * Inner HTML for a chip label: multi-tone segments when parseable,
     * otherwise a single .slash-chip-label span.
     * Each segment gets its own title so hover reveals only that portion
     * (full model name when truncated; no chip-wide instruction dump).
     */
    function slashChipLabelInnerHtml(label) {
        const segs = parseAgentBadgeSegments(label);
        if (segs && (segs.model || segs.effort)) {
            const titleAttr = (t) =>
                ' title="' + escapeHtmlInline(t).replace(/"/g, '&quot;') + '"';
            let html = '<span class="slash-chip-seg slash-chip-seg--agent"' +
                titleAttr(segs.agent) + '>' +
                escapeHtmlInline(segs.agent) + '</span>';
            if (segs.model) {
                html += '<span class="slash-chip-seg slash-chip-seg--model"' +
                    titleAttr(segs.model) + '>' +
                    escapeHtmlInline(segs.model) + '</span>';
            }
            if (segs.effort) {
                html += '<span class="slash-chip-seg slash-chip-seg--effort"' +
                    titleAttr(segs.effort) + '>' +
                    escapeHtmlInline(segs.effort) + '</span>';
            }
            return { html: html, segmented: true };
        }
        return {
            html: '<span class="slash-chip-label">' + escapeHtmlInline(label) + '</span>',
            segmented: false,
        };
    }

    /** History/header chips with model·effort must not hyphen-chop to a bare name. */
    function slashChipShouldNotShorten(label) {
        const s = String(label || '').trim();
        if (!s) return false;
        if (parseAgentBadgeSegments(s)) return true;
        if (/^(Muse Code|Cursor|Hermes|OpenCode|Codex)\b/i.test(s) && /\s[-–—]\s/.test(s)) {
            return true;
        }
        if (/^(Muse Code|Hermes|OpenCode|Codex)\b/i.test(s) && /\s·\s/.test(s)) {
            return true;
        }
        return false;
    }

    /**
     * Upgrade bare agent chips with pinned model (+ effort). Never downgrade a
     * richer stored badge when the palette pin is still loading.
     */
    function enrichSlashCommandWithAgentPin(replySlash, spec) {
        return agentControls.enrichSlashCommandWithAgentPin(replySlash, spec);
    }

    function repaintAgentUserBadges(matchLabelRe, nextLabel) {
        const next = String(nextLabel || '').trim();
        if (!next || typeof document === 'undefined' || !document.querySelectorAll) return;
        document.querySelectorAll('.message.user .slash-command-chip').forEach((chip) => {
            const labelEl = chip.querySelector('.slash-chip-label');
            const current = (chip.dataset && chip.dataset.fullLabel)
                || (labelEl && labelEl.textContent)
                || '';
            const cur = String(current).trim();
            if (!matchLabelRe.test(cur)) return;
            if (cur === next) return;
            // Stored history badges already carry their send-time model/effort
            // ("Agent - model · effort"). Only bare labels may be upgraded;
            // never rewrite a snapshot with the current pin.
            if (/ - /.test(cur) || /\s·\s/.test(cur)) return;
            const inner = slashChipLabelInnerHtml(next);
            chip.querySelectorAll('.slash-chip-label, .slash-chip-seg').forEach((el) => {
                el.remove();
            });
            chip.classList.toggle('slash-command-chip--segmented', !!inner.segmented);
            if (inner.segmented) {
                chip.classList.add('slash-command-chip--no-shorten');
            }
            const removeBtn = chip.querySelector('.slash-chip-remove');
            const holder = document.createElement('span');
            holder.innerHTML = inner.html;
            while (holder.firstChild) {
                chip.insertBefore(holder.firstChild, removeBtn);
            }
            try { chip.dataset.fullLabel = next; } catch (_) {}
            const tip = (chip.dataset && chip.dataset.tip) || '';
            if (inner.segmented) {
                chip.removeAttribute('title');
                try { delete chip.dataset.tip; } catch (_) { chip.dataset.tip = ''; }
            } else {
                try { chip.title = slashChipHoverTitle(next, tip); } catch (_) {}
            }
        });
    }

    /**
     * Repaint user-bubble Muse badges in place with the loaded pin. User
     * bubbles re-derive their badge at render, so ones painted while the
     * pin fetch was in flight would otherwise keep stale defaults.
     */
    function repaintMuseUserBadges() {
        return agentControls.repaintMuseUserBadges();
    }

    /** Add the actual Muse model (+ pinned effort) to its live reply badge. */
    function enrichSlashCommandWithMuseModel(replySlash, model, effort) {
        return agentControls.enrichSlashCommandWithMuseModel(replySlash, model, effort);
    }

    function prettyHermesModelLabel(model) {
        return agentControls.prettyHermesModelLabel(model);
    }

    function hermesModelLabel(model) {
        return agentControls.hermesModelLabel(model);
    }

    function repaintHermesUserBadges() {
        return agentControls.repaintHermesUserBadges();
    }

    /** Add the actual Hermes model (+ pinned effort) to its live reply badge. */
    function enrichSlashCommandWithHermesModel(replySlash, model, effort) {
        return agentControls.enrichSlashCommandWithHermesModel(replySlash, model, effort);
    }

    function prettyOpenCodeModelLabel(model) {
        return agentControls.prettyOpenCodeModelLabel(model);
    }

    function opencodeModelLabel(model) {
        return agentControls.opencodeModelLabel(model);
    }

    function repaintOpenCodeUserBadges() {
        return agentControls.repaintOpenCodeUserBadges();
    }

    function prettyCodexModelLabel(model) {
        return agentControls.prettyCodexModelLabel(model);
    }

    function codexModelLabel(model) {
        return agentControls.codexModelLabel(model);
    }

    function repaintCodexUserBadges() {
        return agentControls.repaintCodexUserBadges();
    }

    function prettyClaudeModelLabel(model) {
        return agentControls.prettyClaudeModelLabel(model);
    }

    function claudeModelLabel(model) {
        return agentControls.claudeModelLabel(model);
    }

    function repaintClaudeUserBadges() {
        return agentControls.repaintClaudeUserBadges();
    }

    /**
     * Single agent-badge entry point (CH-000419 guideline 5). Reads the
     * canonical backend keys — agent_model / model_source / agent_effort /
     * effort_source (+ legacy per-agent keys) — and delegates to the shared
     * pin enricher, so composer / user / assistant badges cannot drift apart.
     * Appends a warning chip when the run reports drift_warning / effort_ignored.
     */
    function enrichSlashCommandWithAgentBadge(replySlash, data, stickyCmd) {
        return agentControls.enrichSlashCommandWithAgentBadge(replySlash, data, stickyCmd);
    }

    /** Add the actual OpenCode model (+ pinned effort) to its live reply badge. */
    function enrichSlashCommandWithOpenCodeModel(replySlash, model, effort) {
        return agentControls.enrichSlashCommandWithOpenCodeModel(replySlash, model, effort);
    }

    /** Add the actual Codex model (+ pinned effort) to its live reply badge. */
    function enrichSlashCommandWithCodexModel(replySlash, model, effort) {
        return agentControls.enrichSlashCommandWithCodexModel(replySlash, model, effort);
    }

    /** Add the actual Claude model (+ pinned effort) to its live reply badge. */
    function enrichSlashCommandWithClaudeModel(replySlash, model, effort) {
        return agentControls.enrichSlashCommandWithClaudeModel(replySlash, model, effort);
    }

    /** Strip legacy Cursor banner/footer from older saved replies. */
    function stripLegacyAgentChrome(content) {
        let s = String(content ?? '');
        s = s.replace(/^\*\*Cursor Agent\*\*[^\n]*:\s*\n+/i, '');
        s = s.replace(/\n*---\s*\n\*Cursor run ·[\s\S]*$/i, '');
        // Old replies prefixed the agent name the header chip already shows.
        // Only the success marker is dropped — ❌ lines still carry the failure
        // for history saved before the chip tracked error state.
        s = s.replace(/^✅\s*\*\*[^*\n]{1,48}:\*\*\s*\n+/, '');
        return s.trimEnd();
    }

    function normalizeSlashCommandStored(sc) {
        return CuttleChatSlash.normalizeSlashCommandStored(sc);
    }

    function slashCommandMetaFromUserMessage(userMessage) {
        return CuttleChatSlash.slashCommandMetaFromUserMessage(userMessage, slashParseDeps());
    }

    /** User bubble: show slash commands as chips + optional body (not raw /prefix). */
    const CUTTLE_KNOWN_BUTTON_LABELS = {
        'project-action-confirm': 'Confirm action',
        'project-action-cancel': 'Cancel action',
    };

    function parseButtonClickFromContent(content) {
        const raw = String(content ?? '').trim();
        const m = raw.match(/^\[button:([^\]]+)\]/);
        if (!m) return null;
        const id = m[1];
        const label = CUTTLE_KNOWN_BUTTON_LABELS[id] || id.replace(/-/g, ' ');
        return { id, label };
    }

    function parseButtonClickFromMessage(msg) {
        if (!msg) return null;
        const meta = msg.metadata && typeof msg.metadata === 'object' ? msg.metadata.button_click : null;
        if (meta && meta.id) {
            return { id: String(meta.id), label: meta.label || CUTTLE_KNOWN_BUTTON_LABELS[meta.id] || meta.id };
        }
        const sel = String(msg.content ?? '').trim().match(/^Selected:\s*(.+)$/i);
        if (sel && msg.role === 'user') {
            return { id: null, label: sel[1].trim() };
        }
        return msg.role === 'user' ? parseButtonClickFromContent(msg.content) : null;
    }

    function lockCuttleButtonsInContainer(containerEl, selectedId, selectedLabel) {
        if (!containerEl) return;
        const buttons = containerEl.querySelectorAll('button.cuttle-button[data-cuttle-button-id]');
        if (!buttons.length) return;
        let matched = false;
        buttons.forEach((btn) => {
            const id = btn.getAttribute('data-cuttle-button-id') || '';
            const label = (btn.textContent || '').trim();
            const isSelected = selectedId
                ? id === selectedId
                : !!(selectedLabel && label.toLowerCase() === String(selectedLabel).toLowerCase());
            if (isSelected) matched = true;
            btn.disabled = true;
            btn.classList.toggle('is-selected', isSelected);
            btn.classList.toggle('is-unselected', !isSelected);
            btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });
        if (matched || selectedId || selectedLabel) {
            containerEl.classList.add('cuttle-buttons-locked');
        }
    }

    function reconcileCuttleButtonLocks(messages) {
        if (!Array.isArray(messages) || !messages.length) return;
        const nodes = document.querySelectorAll('#chatMessages ' + CHAT_RECORD_SELECTOR);
        for (let i = 0; i < messages.length; i++) {
            const msg = messages[i];
            const click = parseButtonClickFromMessage(msg);
            if (!click) continue;
            for (let j = i - 1; j >= 0; j--) {
                if (messages[j].role !== 'assistant') continue;
                const node = messages[j].id != null
                    ? document.querySelector(`#chatMessages .message[data-message-id="${CSS.escape(String(messages[j].id))}"]`)
                    : nodes[j];
                const contentEl = node && node.querySelector('.message-content');
                if (!contentEl || !contentEl.querySelector('button.cuttle-button[data-cuttle-button-id]')) continue;
                if (click.id && !contentEl.querySelector(`[data-cuttle-button-id="${CSS.escape(click.id)}"]`)) continue;
                lockCuttleButtonsInContainer(contentEl, click.id, click.label);
                break;
            }
        }
    }

    const FORM_REPLY_ICON_PICK = '<svg class="cuttle-form-reply-icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg>';
    const FORM_REPLY_ICON_ANSWERS = '<svg class="cuttle-form-reply-icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4h6v3H9z"/><path d="M9 13l2 2 4-4"/></svg>';

    /**
     * Resume turns sent by Q&A action forms keep their `[form-selection] …` /
     * `[form-answers]\n- Q: A` wire text for the agent; render them as a pick chip.
     */
    function formatFormReplyHtml(content) {
        const raw = String(content ?? '').trim();
        const sel = raw.match(/^\[form-selection\]\s*([\s\S]*)$/i);
        if (sel) {
            // "Label (option-id), Other (other-id)" → drop the trailing option ids.
            const label = sel[1].replace(/\s*\([^()]*\)(?=,\s|$)/g, '').trim() || 'Selected';
            return (
                '<div class="cuttle-form-reply" title="Form selection">'
                + FORM_REPLY_ICON_PICK
                + '<span class="cuttle-form-reply-label">' + escapeHtmlInline(label) + '</span>'
                + '</div>'
            );
        }
        const ans = raw.match(/^\[form-answers\]\s*([\s\S]*)$/i);
        if (ans) {
            const rows = ans[1]
                .split('\n')
                .map((line) => line.replace(/^\s*-\s*/, '').trim())
                .filter(Boolean)
                .map((line) => {
                    const idx = line.indexOf(': ');
                    const q = idx >= 0 ? line.slice(0, idx) : '';
                    const a = idx >= 0 ? line.slice(idx + 2) : line;
                    const empty = /^\((none|no answer)\)$/i.test(a.trim());
                    return (
                        '<li>'
                        + (q ? '<span class="cuttle-form-reply-q">' + escapeHtmlInline(q) + '</span>' : '')
                        + '<span class="cuttle-form-reply-a' + (empty ? ' is-empty' : '') + '">'
                        + escapeHtmlInline(empty ? '—' : a)
                        + '</span></li>'
                    );
                });
            return (
                '<div class="cuttle-form-reply cuttle-form-reply--answers" title="Form answers">'
                + '<div class="cuttle-form-reply-head">' + FORM_REPLY_ICON_ANSWERS
                + '<span class="cuttle-form-reply-label">Answers</span></div>'
                + (rows.length ? '<ul class="cuttle-form-reply-list">' + rows.join('') + '</ul>' : '')
                + '</div>'
            );
        }
        return null;
    }

    function formatUserMessageForDisplay(content, opts = {}) {
        return CuttleChatMessages.formatUserMessageForDisplay(content, opts, {
            normalizeAttachmentList,
            stripAttachedNote,
            inferAttachmentsFromContent,
            buildMessageAttachmentsHtml,
            parseButtonClickFromContent,
            formatFormReplyHtml,
            formatMessage,
            parseStoredSlashCommandMessage,
            collapseCursorSlashChips,
            slashCommandChipHistoryHtml,
            escapeHtmlInline,
        });
    }

    function buildVegaWrapHtml(rawSpec) {
        return CuttleChatMarkdown.buildVegaWrapHtml(rawSpec, { escapeHtmlInline });
    }

    function activateVegaEmbeds(containerEl) {
        return CuttleChatActivate.activateVegaEmbeds(containerEl, {
            vegaEmbed: window.vegaEmbed,
        });
    }

    // Invisible characters that survive a rendered-text copy and show up as
    // garbage in terminals: zero-width/BOM/soft hyphen dropped, exotic spaces
    // flattened to ASCII space.
    function cleanCodeCopyText(raw) {
        return CuttleChatMarkdown.cleanCodeCopyText(raw);
    }

    function attachCodeCopyButtons(containerEl) {
        return CuttleChatActivate.attachCodeCopyButtons(containerEl, {
            createElement: (tag) => document.createElement(tag),
            copyIcon: COPY_ICON,
            copiedIcon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
            copyText: copyTextToClipboard,
            notifyError: (message) => (window.showToast || function () {})(message, 'error'),
            later: (fn, ms) => setTimeout(fn, ms),
        });
    }

    function activateEnhancements(containerEl) {
        // Syntax highlight (best-effort; owned by chat_activate.js).
        try {
            CuttleChatActivate.highlightCodeBlocks(containerEl, {
                hljs: window.hljs,
            });
        } catch (_) {}

        try {
            attachCodeCopyButtons(containerEl);
        } catch (_) {}

        // Vega embeds (may load after first paint via optional_cdn)
        try {
            activateVegaEmbeds(containerEl);
        } catch (_) {}

        // Terminal input -> sends a message back (owned by chat_activate.js).
        try {
            CuttleChatActivate.wireTerminalInputs(containerEl, {
                sendMessage: (msg) => processMessage(msg),
            });
        } catch (_) {}

        // Cuttle buttons -> send back into chat
        try {
            containerEl.querySelectorAll('button.cuttle-button[data-cuttle-button-id]').forEach((btn) => {
                if (btn.__wired_button) return;
                btn.__wired_button = true;
                btn.addEventListener('click', () => {
                    try {
                        if (generation.loading) return;
                        if (btn.disabled || btn.closest('.cuttle-buttons-locked')) return;
                        const id = btn.getAttribute('data-cuttle-button-id') || 'button';
                        const payload = btn.getAttribute('data-cuttle-button-payload') || '';
                        const msg = payload && String(payload).trim() ? `[button:${id}] ${payload}` : `[button:${id}]`;
                        const contentEl = btn.closest('.message-content');
                        if (contentEl) lockCuttleButtonsInContainer(contentEl, id, (btn.textContent || '').trim());
                        processMessage(msg);
                    } catch (_) {}
                });
            });
        } catch (_) {}

        // Supervised Activity disclosure controls (typed endpoints).
        try {
            containerEl.querySelectorAll(
                '.supervised-activity-disclosure, .supervised-activity-card'
            ).forEach((el) => {
                wireSupervisedActivityCard(el);
            });
        } catch (_) {}

        // Forms -> submits structured data back into chat
        try {
            containerEl.querySelectorAll('form.cuttle-form[data-form-id]').forEach((form) => {
                if (form.__wired) return;
                form.__wired = true;
                const formId = form.getAttribute('data-form-id') || 'form';

                const setBusy = (busy) => {
                    form.querySelectorAll('button, input, textarea, select').forEach((el) => {
                        if (el.matches('button[type="submit"]')) return;
                        el.disabled = !!busy;
                    });
                };

                form.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const payload = { id: formId, fields: {} };

                    form.querySelectorAll('[data-field-id]').forEach((el) => {
                        const fid = el.getAttribute('data-field-id');
                        if (!fid) return;

                        if (el.tagName === 'INPUT') {
                            const t = (el.getAttribute('type') || 'text').toLowerCase();
                            if (t === 'checkbox') {
                                payload.fields[fid] = !!el.checked;
                            } else if (t === 'range' || t === 'number') {
                                payload.fields[fid] = el.value === '' ? null : Number(el.value);
                            } else {
                                payload.fields[fid] = el.value ?? '';
                            }
                        } else if (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
                            payload.fields[fid] = el.value ?? '';
                        }
                    });

                    // Radios are grouped by name; capture checked
                    form.querySelectorAll('[data-radio-group]').forEach((wrap) => {
                        const fid = wrap.getAttribute('data-radio-group');
                        if (!fid) return;
                        const chosen = wrap.querySelector('input[type="radio"]:checked');
                        payload.fields[fid] = chosen ? (chosen.value ?? '') : '';
                    });

                    // Toggle UI (checkbox under the hood) already handled via input[type=checkbox]
                    const submitBtn = form.querySelector('button[type="submit"]');
                    if (submitBtn) {
                        submitBtn.disabled = true;
                        submitBtn.textContent = submitBtn.getAttribute('data-busy-label') || 'Sending…';
                    }
                    setBusy(true);
                    try {
                        await processMessage(`[form:${formId}] ${JSON.stringify(payload)}`);
                    } finally {
                        if (submitBtn) {
                            submitBtn.disabled = false;
                            submitBtn.textContent = submitBtn.getAttribute('data-label') || 'Submit';
                        }
                        setBusy(false);
                    }
                });
            });
        } catch (_) {}

        // Action-card effects (mount, submit, watch, restart link) are owned
        // by CuttleChatActionCards (chat_action_cards.js). The page supplies
        // transport/session/paint capabilities through the controller host.
        try {
            actionCardsController().mount(containerEl);
        } catch (_) {}

        // Slider value live updates
        try {
            containerEl.querySelectorAll('input[type="range"][data-field-id]').forEach((slider) => {
                if (slider.__wired_slider) return;
                slider.__wired_slider = true;
                const fid = slider.getAttribute('data-field-id');
                const out = fid ? containerEl.querySelector(`[data-slider-value-for="${fid}"]`) : null;
                const update = () => { if (out) out.textContent = String(slider.value); };
                slider.addEventListener('input', update);
                update();
            });
        } catch (_) {}
    }
    
    // Theme Management
    // ui_boot.js settles the theme classes before first paint; re-applying them
    // here would reintroduce the flash it exists to prevent.
    function initializeTheme() {
        if (!window.CuttleUiBoot) {
            const savedTheme = localStorage.getItem('theme') || 'dark';
            const body = document.body;

            body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');

            if (savedTheme === 'light') {
                body.classList.add('light-mode');
            } else if (savedTheme === 'midnight') {
                body.classList.add('midnight-mode');
            } else {
                body.classList.add('dark-mode');
            }
        }

        updateThemeIcon();
    }
    
    function toggleTheme() {
        const body = document.body;
        if (window.CuttleUiBoot) {
            const cycle = { dark: 'midnight', midnight: 'light', light: 'dark' };
            window.CuttleUiBoot.applyTheme(cycle[localStorage.getItem('theme') || 'dark'] || 'midnight');
            updateThemeIcon();
            return;
        }
        let theme = 'dark';
        if (body.classList.contains('dark-mode')) {
            body.classList.remove('dark-mode');
            body.classList.add('midnight-mode');
            localStorage.setItem('theme', 'midnight');
            theme = 'midnight';
        } else if (body.classList.contains('midnight-mode')) {
            body.classList.remove('midnight-mode');
            body.classList.add('light-mode');
            localStorage.setItem('theme', 'light');
            theme = 'light';
        } else {
            body.classList.remove('light-mode');
            body.classList.add('dark-mode');
            localStorage.setItem('theme', 'dark');
            theme = 'dark';
        }
        if (window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-theme-change', theme }, '*');
        }
        updateThemeIcon();
    }
    
    function updateThemeIcon() {
        const themeIcon = document.querySelector('.theme-icon');
        const body = document.body;
        
        if (!themeIcon) return;
        
        if (body.classList.contains('dark-mode')) {
            themeIcon.textContent = '🌑';
        } else if (body.classList.contains('midnight-mode')) {
            themeIcon.textContent = '☀️';
        } else {
            themeIcon.textContent = '🌙';
        }
    }
    
    // Agent Name Management
    function updateAgentNameDisplay() {
        const welcomeAgentName = document.getElementById('welcomeAgentName');
        if (welcomeAgentName) {
            welcomeAgentName.textContent = agentName;
        }
    }
    
    // Project Management
    async function loadProjects() {
        LOG('loadProjects: fetching /api/projects');

        try {
            const response = await fetch('/api/projects');
            LOG('loadProjects: response status=', response.status, response.statusText);

            let data;
            try {
                data = await response.json();
            } catch (parseErr) {
                LOG_ERR('loadProjects: failed to parse JSON:', parseErr);
                projects = [];
                reconcileChatProject();
                historyPanel.reloadKeepingSearch();
                return;
            }

            LOG('loadProjects: data.success=', data?.success, 'data.data length=', data?.data?.length);

            if (data.success && Array.isArray(data.data)) {
                projects = data.data;
            } else {
                projects = [];
                if (data?.error) LOG('loadProjects: API error:', data.error);
            }
            // Resolve this chat's project (session-stored or Cuttle default) and show its chip.
            reconcileChatProject();
            // History grouping needs the project list for the Cuttle default fallback.
            historyPanel.reloadKeepingSearch();
        } catch (error) {
            LOG_ERR('loadProjects: fetch error:', error);
            projects = [];
            reconcileChatProject();
            historyPanel.reloadKeepingSearch();
        }
    }
    
    function updateProjectSelector() {
        const select = document.getElementById('projectSelect');
        if (!select) return;

        if (projects.length === 0) {
            select.innerHTML = '<option value="">No projects available</option>';
            return;
        }

        select.innerHTML = '<option value="">Select a project...</option>';
        
        projects.forEach(project => {
            const option = document.createElement('option');
            option.value = project.id;
            
            // Create descriptive display name
            let displayName = project.name;
            if (project.type === 'local' && project.path) {
                const pathParts = project.path.split(/[\\\/]/);
                const folderName = pathParts[pathParts.length - 1];
                if (folderName && folderName !== project.name) {
                    displayName = `${project.name} (${folderName})`;
                }
            }
            
            option.textContent = displayName;
            
            if (currentProject && project.id === currentProject.id) {
                option.selected = true;
            }
            
            select.appendChild(option);
        });
    }
    
    function updateProjectDisplay() {
        const projectPath = document.getElementById('projectPath');
        if (!projectPath) return;
        
        if (currentProject && currentProject.path) {
            projectPath.textContent = currentProject.path;
            projectPath.style.display = 'block';
        } else {
            projectPath.style.display = 'none';
        }
    }

    // --- Per-chat project ("cwd") via the /project (/cd) slash command ---
    // Pure lookups live in chat_project.js; wrappers bind the registry.
    function findProjectById(id) {
        return CuttleChatProject.findProjectById(projects, id);
    }

    function findDefaultProject() {
        // Starred project wins (exclusive default for new chats).
        return CuttleChatProject.findDefaultProject(projects, readStarredProject());
    }

    function renderProjectChips() {
        refreshProjectBranch();
        renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
        renderSlashChips('chat', document.getElementById('chatInput'));
    }

    /**
     * Generating / remote-waiting bubbles bake project + agent chips into HTML
     * at create time. Hub live-status (phone shell) often paints the bubble
     * before sticky restore finishes — refresh both chips from current state.
     */
    function refreshTypingIndicatorHeaderBadges() {
        const meta = typeof currentTypingSlashMeta === 'function'
            ? currentTypingSlashMeta()
            : null;
        const chips = meta && meta.chips ? collapseCursorSlashChips(meta.chips) : [];
        const html = messageHeaderBadgesHtml(chips, false, currentProject);
        ['typing-indicator', 'typing-indicator-remote'].forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            const group = el.querySelector('.message-sender-group');
            if (!group) return;
            Array.from(group.children).forEach((child) => {
                if (!child.classList || !child.classList.contains('message-sender')) {
                    child.remove();
                }
            });
            if (html) {
                const tmp = document.createElement('div');
                tmp.innerHTML = html;
                while (tmp.firstChild) group.appendChild(tmp.firstChild);
            }
            scheduleSlashChipCompactLabels(el);
        });
    }

    /**
     * Keep the in-memory auth session + last /messages hydrate in sync with the
     * chip the user just picked. reconcileChatProject() prefers
     * lastHydratedSessionProject over local prefs; leaving it stale (e.g. Cuttle
     * from open) made /project → Escape Purgatory snap back on send/reload.
     */
    function rememberSessionProject(sessionId, proj) {
        if (sessionId == null || sessionId === '') return;
        const authSid = toAuthDbSessionId(sessionId);
        const anchor = authSid != null ? authSid : String(sessionId);
        if (proj && (proj.id != null || proj.path || proj.name)) {
            lastHydratedSessionProject = {
                id: anchor,
                project_id: proj.id != null ? proj.id : null,
                project_name: proj.name || '',
                project_path: proj.path || '',
                checkout_uid: currentCheckoutUid || null,
            };
        } else if (
            lastHydratedSessionProject
            && sessionIdsEqual(lastHydratedSessionProject.id, sessionId)
        ) {
            lastHydratedSessionProject = null;
        }
        try {
            const list = (window.CuttleAuth && window.CuttleAuth.getSessions
                && window.CuttleAuth.getSessions()) || [];
            const row = list.find((s) => sessionIdsEqual(s.id, sessionId)) || null;
            if (row) {
                row.project_id = proj && proj.id != null ? proj.id : null;
                row.project_name = proj ? (proj.name || null) : null;
                row.project_path = proj ? (proj.path || null) : null;
                row.checkout_uid = currentCheckoutUid || null;
            }
        } catch (_) {}
    }

    /** Set the active project for this chat, persist it on the session, and refresh chips. */
    function setChatProject(proj, opts = {}) {
        // Always take path/name from the projects registry when id is known.
        if (proj && proj.id != null) {
            const byId = findProjectById(proj.id);
            if (byId) {
                proj = { id: byId.id, name: byId.name || proj.name, path: byId.path || proj.path || '' };
            }
        }
        const prevProjectId = currentProject && currentProject.id != null ? String(currentProject.id) : '';
        currentProject = proj || null;
        const nextProjectId = currentProject && currentProject.id != null ? String(currentProject.id) : '';
        // A different project starts fresh (Local); the chip can rebind a worktree.
        if (prevProjectId !== nextProjectId) { currentCheckoutUid = ''; currentCheckoutPath = ''; }
        reportProjectToShell(effectiveGitPath());
        if (session.current()) {
            rememberSessionProject(session.current(), proj);
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                if (sessions[session.current()]) {
                    sessions[session.current()].projectId = proj ? proj.id : null;
                    sessions[session.current()].projectPath = proj ? proj.path : '';
                    sessions[session.current()].projectName = proj ? proj.name : '';
                    localStorage.setItem('chatSessions', JSON.stringify(sessions));
                }
            } catch (_) {}
            persistProjectForCurrentSession();
        }
        renderProjectChips();
        // Project-scoped Tasks follow the chip — refresh so a prior project's
        // strip does not linger after a chip change / session reconcile.
        refreshChatWidgets();
        // App shell owns periodic pending-changes; register already triggers a sample.
        if (!inAppShell) schedulePendingChangesRefresh(120);
        loadProjectCommandsForPalette();
        loadHarnessAgentsForPalette();
        if (!opts.silent && proj) {
            const pathBit = proj.path ? ` → \`${proj.path}\`` : '';
            // Stick in the transcript (default system toast fades after 3s and is not saved).
            addSystemMessage(`📁 Project: ${proj.name}${pathBit}`, { persist: true });
        }
    }

    /** Bind this chat to a worktree checkout (null/empty = Local/primary). */
    function setChatCheckout(checkout) {
        const uid = checkout && checkout.checkout_uid ? String(checkout.checkout_uid) : '';
        currentCheckoutUid = uid;
        currentCheckoutPath = checkout && checkout.path ? String(checkout.path) : '';
        if (session.current()) {
            persistProjectForCurrentSession();
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                if (sessions[session.current()]) {
                    sessions[session.current()].checkoutUid = uid || null;
                    sessions[session.current()].checkoutPath = currentCheckoutPath || null;
                    localStorage.setItem('chatSessions', JSON.stringify(sessions));
                }
            } catch (_) {}
        }
        renderProjectChips();
        reportProjectToShell(effectiveGitPath());
        if (uid) {
            const branch = checkout && checkout.branch ? checkout.branch : uid;
            addSystemMessage(`\u{1F33F} Worktree: ${branch}`, { persist: true });
        } else {
            addSystemMessage('\u{1F4C1} Local checkout', { persist: true });
        }
    }

    // --- Pending changes strip (shared module; multi-repo aware) ---
    let pendingChangesCtl = null;

    function schedulePendingChangesRefresh(delayMs, refreshOpts) {
        if (!pendingChangesCtl) return;
        pendingChangesCtl.scheduleRefresh(delayMs, refreshOpts);
    }

    /** Git-facing path for this chat: the bound checkout, else the project. */
    function effectiveGitPath() {
        return CuttleChatProject.effectiveGitPath(currentCheckoutPath, currentProject);
    }

    function reportProjectToShell(projectPath) {
        if (!inAppShell) return;
        try {
            window.parent.postMessage({
                type: 'cuttle-pending-project',
                path: projectPath ? String(projectPath) : null,
            }, '*');
        } catch (_) {}
    }

    function initPendingChangesPanel() {
        if (!window.CuttlePendingChangesPanel || typeof window.CuttlePendingChangesPanel.create !== 'function') {
            console.warn('[chat] pending_changes_panel.js not loaded');
            return;
        }
        if (pendingChangesCtl) {
            try { pendingChangesCtl.destroy(); } catch (_) {}
            pendingChangesCtl = null;
        }
        pendingChangesCtl = window.CuttlePendingChangesPanel.create({
            getProject: function () {
                // Pending changes / git target the bound checkout when present.
                return CuttleChatProject.pendingChangesProject(
                    currentProject, effectiveGitPath(), currentProjectBranch(), currentCheckoutUid);
            },
            getSessionId: function () { return session.current(); },
            getPrompts: function () {
                try {
                    if (typeof getComposerPromptHistory === 'function') {
                        return getComposerPromptHistory() || [];
                    }
                } catch (_) {}
                return [];
            },
            onOpenDiff: function (relPath, meta) {
                openPendingDiffModal(relPath, {
                    repoRoot: meta && meta.repoRoot ? meta.repoRoot : null,
                });
            },
            onAfterCommit: function () {
                schedulePendingChangesRefresh(80);
            },
            onAfterIgnore: function () {
                schedulePendingChangesRefresh(80);
            },
            useShellHub: !!inAppShell,
            isPageBackgrounded: function () { return document.hidden; },
        });
        reportProjectToShell(effectiveGitPath());
        if (!inAppShell) schedulePendingChangesRefresh(120);
    }

    // The shared popup owns rendering, navigation, focus and file opening.
    function openPendingDiffModal(relPath, opts) {
        if (!currentProject || !currentProject.path || !relPath) return;
        const options = opts || {};
        const repos = pendingChangesCtl ? pendingChangesCtl.getRepos() : [];
        const repoRoot = options.repoRoot || (repos.length === 1 ? repos[0].repo_root : null);
        return window.CuttleDiffModal.open({
            projectPath: currentProject.path,
            repoRoot: repoRoot,
            file: relPath,
            files: pendingChangesCtl && repoRoot ? pendingChangesCtl.getFilesForRepo(repoRoot) : [relPath],
            full: options.full,
            maxLines: options.maxLines,
        });
    }

    /** Resolve the chat's project from the active session, falling back to the Cuttle default. */
    function reconcileChatProject() {
        // Gather session inputs (chat-page owns all session IO); the priority
        // chain itself lives in chat_project.js (pure over explicit inputs).
        let serverProj = null;
        let mergedPrefs = null;
        let stored = null;
        if (session.current()) {
            serverProj = projectFieldsFromServerSession(findAuthServerSession(session.current()));
            const prefs = getSessionPrefs(session.current());
            const barePrefs = getSessionPrefs(toAuthDbSessionId(session.current()));
            mergedPrefs = Object.assign({}, barePrefs || {}, prefs || {});
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                stored = sessions[session.current()] || null;
            } catch (_) {
                stored = null;
            }
        }
        const decided = CuttleChatProject.resolveChatProject({
            projects,
            currentSessionId: session.current(),
            currentProject,
            serverFields: serverProj,
            prefs: mergedPrefs,
            stored,
            starred: readStarredProject(),
        });
        const proj = decided.proj;
        const hadStored = decided.hadStored;
        currentProject = proj || null;
        reportProjectToShell(effectiveGitPath());
        // Restore the chat's bound worktree checkout (server row, then prefs).
        // An unsaved new chat has no session row yet: keep the in-memory pick
        // instead of clearing it, or the chip snaps back to the primary branch.
        if (session.current()) {
            const storedCheckout = (serverProj && serverProj.checkoutUid)
                || (mergedPrefs && mergedPrefs.checkoutUid)
                || (stored && stored.checkoutUid) || '';
            currentCheckoutUid = String(storedCheckout || '').trim();
            currentCheckoutPath = String((mergedPrefs && mergedPrefs.checkoutPath)
                || (stored && stored.checkoutPath) || '').trim();
        }
        // Stamp the default onto prefs so history grouping matches the cwd chip
        // (auth sessions do not store project_id on the server).
        if (session.current() && currentProject && !hadStored
            && !sessionHasStoredProject(session.current(), null)
            && authSessionListHas(session.current())
            && !projectFieldsFromServerSession(findAuthServerSession(session.current()))) {
            persistProjectForCurrentSession();
        } else if (session.current() && currentProject && hadStored) {
            const prefs = getSessionPrefs(session.current());
            const prefsStale = !prefs
                || (currentProject.id != null && String(prefs.projectId) !== String(currentProject.id))
                || (currentProject.path && String(prefs.projectPath || '') !== String(currentProject.path));
            if (prefsStale) persistProjectForCurrentSession({ localOnly: true });
        }
        renderProjectChips();
        refreshTypingIndicatorHeaderBadges();
        schedulePendingChangesRefresh(200);
        loadProjectCommandsForPalette();
        loadHarnessAgentsForPalette();
    }

    /** Project to send with /api/chat — always prefers a real filesystem path. */
    function resolveOutboundProject() {
        const keepWelcomePick =
            !session.current()
            && currentProject
            && (currentProject.path || currentProject.id != null || currentProject.name);
        // Don't re-read empty session storage on first send — that overwrites
        // a welcome-page pick with the Cuttle default.
        if (!keepWelcomePick) {
            reconcileChatProject();
        }
        // Always re-bind path from the live projects list when we have an id/name.
        // Stale localStorage often keeps name "Escape Purgatory" with path Cuttle.
        const rebound = CuttleChatProject.rebindProjectToRegistry(currentProject, projects);
        currentProject = rebound.proj;
        if (rebound.changed && session.current()) persistProjectForCurrentSession();
        return currentProject;
    }

    function applyOutboundProjectToRequest(requestBody) {
        CuttleChatProject.applyOutboundProjectToRequest(
            requestBody, resolveOutboundProject(), currentCheckoutUid);
    }
    
    async function switchChatProject() {
        const select = document.getElementById('projectSelect');
        const projectId = parseInt(select.value);
        
        if (!projectId) {
            currentProject = null;
            updateProjectDisplay();
            return;
        }
        
        try {
            const response = await fetch(`/api/projects/${projectId}/switch`, {
                method: 'POST'
            });
            const data = await response.json();
            
            if (data.success) {
                currentProject = data.data;
                updateProjectDisplay();
                console.log(`Switched to project: ${currentProject.name}`);
                
                // Show a brief notification
                addSystemMessage(`📁 Switched to project: ${currentProject.name}`);
            } else {
                throw new Error(data.error || 'Failed to switch project');
            }
        } catch (error) {
            console.error('Error switching project:', error);
            addSystemMessage(`❌ Failed to switch project: ${error.message}`);
        }
    }
    
    function addSystemMessage(message, opts = {}) {
        const messagesContainer = document.getElementById('chatMessages');
        if (!messagesContainer) return;
        
        // Only add if chat area is visible
        if (document.getElementById('chatArea').style.display === 'none') {
            return;
        }

        let messageDiv = null;
        if (opts.kind) {
            const existingList = messagesContainer.querySelectorAll(
                `.message.system[data-kind="${CSS.escape(String(opts.kind))}"]`
            );
            const existing = existingList.length ? existingList[existingList.length - 1] : null;
            // Update the latest matching notice when it still belongs to this turn
            // (after the last user bubble). Otherwise insert a new one.
            if (existing && opts.replace !== false) {
                const users = messagesContainer.querySelectorAll('.message.user');
                const lastUser = users.length ? users[users.length - 1] : null;
                const stale = lastUser
                    && (existing.compareDocumentPosition(lastUser) & Node.DOCUMENT_POSITION_FOLLOWING);
                if (!stale) {
                    const content = existing.querySelector('.system-message');
                    if (content) content.textContent = message;
                    existing.dataset.rawContent = String(message ?? '');
                    if (!opts.skipScroll) transcriptNav.autoScrollChatToBottom();
                    // Already durable — do not POST a duplicate row on text tweak.
                    if (opts.persist && !existing.dataset.messageId) {
                        durablePersistSystemMessage(existing, message, opts);
                    }
                    return existing;
                }
            }
        }
        
        messageDiv = document.createElement('div');
        messageDiv.className = 'message system';
        if (opts.kind) messageDiv.dataset.kind = String(opts.kind);
        messageDiv.dataset.rawContent = String(message ?? '');
        if (opts.message_id != null && opts.message_id !== '') {
            messageDiv.dataset.messageId = String(opts.message_id);
        }
        messageDiv.innerHTML = `
            <div class="message-content system-message">${escapeHtml(message)}</div>
        `;
        
        if (opts.prepend) {
            const anchor = opts.insertBefore
                || messagesContainer.querySelector('.message')
                || messagesContainer.firstChild;
            if (anchor) messagesContainer.insertBefore(messageDiv, anchor);
            else messagesContainer.appendChild(messageDiv);
        } else {
            messagesContainer.appendChild(messageDiv);
        }
        if (!opts.skipScroll) transcriptNav.autoScrollChatToBottom();

        // Sticky notices (e.g. Stopped generating) stay until the next send.
        // ``persist`` also means durable: write to the auth chat log so refresh /
        // other devices keep the same timeline.
        if (opts.persist) {
            if (!messageDiv.dataset.messageId) {
                durablePersistSystemMessage(messageDiv, message, opts);
            }
            return messageDiv;
        }
        
        // Auto-remove after 3 seconds
        setTimeout(() => {
            messageDiv.style.transition = 'opacity 0.3s';
            messageDiv.style.opacity = '0';
            setTimeout(() => messageDiv.remove(), 300);
        }, 3000);
        return messageDiv;
    }

    /** Write a system notice into chat_messages so it survives refresh / devices. */
    function durablePersistSystemMessage(el, message, opts) {
        opts = opts || {};
        if (!el || !isAuthMode() || session.current() == null) return;
        if (el.dataset.persistInFlight === '1' || el.dataset.messageId) return;
        const bare = toAuthDbSessionId(session.current());
        if (!bare || !/^\d+$/.test(String(bare))) return;
        el.dataset.persistInFlight = '1';
        const body = {
            role: 'system',
            content: String(message ?? ''),
        };
        if (opts.kind) body.kind = String(opts.kind);
        fetch(`/api/auth/sessions/${encodeURIComponent(bare)}/messages`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then((r) => r.json().catch(() => ({})))
            .then((data) => {
                if (data && data.success && data.message_id != null) {
                    el.dataset.messageId = String(data.message_id);
                    const mid = Number(data.message_id);
                    if (Number.isFinite(mid) && mid > lastSeenServerMessageId) {
                        lastSeenServerMessageId = mid;
                    }
                }
            })
            .catch((e) => console.warn('[Cuttle Chat] persist system notice failed:', e))
            .finally(() => {
                delete el.dataset.persistInFlight;
            });
    }

    function isCancelledAgentText(text) {
        return /^\s*\[CANCELLED\]/i.test(String(text || ''));
    }

    function ensureGenerationStopNotice(text) {
        addSystemMessage(text, { persist: true, kind: 'generation-stop' });
    }

    /**
     * Swallow a [CANCELLED] assistant payload.
     *
     * Announce only when this pane was actually waiting on the run that got
     * cancelled. History and background sync replay old cancelled rows on every
     * load/poll, so they must stay silent or the notice reappears out of nowhere.
     */
    function consumeCancelledAgentReply(text, opts = {}) {
        if (!isCancelledAgentText(text)) return false;
        const live = stopState.userStopped || generation.loading;
        if (opts.announce === true || (opts.announce !== false && live)) {
            ensureGenerationStopNotice('⏹ Generation cancelled.');
        }
        return true;
    }
    
    // Chat history panel management
    function getChatHistoryPanel() {
        return document.getElementById('chatHistoryPanel');
    }

    function toggleChatHistoryPanelCollapse() {
        const panel = getChatHistoryPanel();
        if (!panel) return;
        const isCollapsed = panel.classList.toggle('collapsed');
        localStorage.setItem('chatHistoryPanelCollapsed', isCollapsed ? 'true' : 'false');
    }

    // While the history panel is open, re-pull generating flags so zombie
    // "running" spinners clear without a hard refresh.
    let historyGeneratingPollTimer = null;
    const HISTORY_GENERATING_POLL_MS = 12000;

    function isHistoryItemMenuOpen() {
        const menu = document.getElementById('historyItemOverflowMenu');
        if (menu && !menu.hidden) return true;
        return !!document.querySelector('.chat-history-item.menu-open');
    }

    function isHistoryPanelUiBusy() {
        if (historyPanel.isDeleteModalOpen()) return true;
        if (historyPanel.isRenameModalOpen()) return true;
        if (isHistoryItemMenuOpen()) return true;
        // Full list rebuilds destroy the ⋮ button mid-click and close the menu.
        return !!document.querySelector(
            '.chat-menu-btn:hover, .chat-menu-btn:active, .chat-actions:hover, #historyItemOverflowMenu:hover'
        );
    }

    function stopHistoryGeneratingPoll() {
        if (historyGeneratingPollTimer) {
            clearInterval(historyGeneratingPollTimer);
            historyGeneratingPollTimer = null;
        }
    }

    function startHistoryGeneratingPoll() {
        stopHistoryGeneratingPoll();
        if (!isAuthMode()) return;
        historyGeneratingPollTimer = setInterval(function () {
            const panel = getChatHistoryPanel();
            if (!panel || !panel.classList.contains('open')) {
                stopHistoryGeneratingPoll();
                return;
            }
            if (isHistoryPanelUiBusy()) return;
            refreshChatHistoryList();
        }, HISTORY_GENERATING_POLL_MS);
    }

    // Chat history panel (off-canvas) — opened by the corner button
    // The slide transition lives only under .hist-slide (applied here and in
    // close): the panel has no base transition, so viewport/breakpoint flips
    // from shell resizes can never replay as a visible sweep.
    let historyPanelSlideTimer = 0;
    function animateHistoryPanelSlide(panel) {
        if (!panel) return;
        if (historyPanelSlideTimer) clearTimeout(historyPanelSlideTimer);
        panel.classList.add('hist-slide');
        // Let the class paint before the open-state flips, then drop it so
        // later style flips stay instant.
        void panel.offsetWidth;
        historyPanelSlideTimer = setTimeout(function () {
            panel.classList.remove('hist-slide');
            historyPanelSlideTimer = 0;
        }, 350);
    }
    function openChatHistoryPanel() {
        const panel = getChatHistoryPanel();
        const overlay = document.getElementById('chatHistoryPanelOverlay');
        if (!panel) return;
        // Dismiss any hover/focus tip from the history button (phones otherwise
        // leave "Chat history" floating above Search into the unsafe top inset).
        if (window.CuttleTooltips && typeof window.CuttleTooltips.hide === 'function') {
            window.CuttleTooltips.hide();
        }
        animateHistoryPanelSlide(panel);
        panel.classList.add('open');
        if (overlay) overlay.classList.add('active');
        // Pull latest sessions on open so chats started on another device show up
        // without a full page refresh (no continuous polling).
        refreshChatHistoryList();
        startHistoryGeneratingPoll();
        refreshLocalOpenPanes();
    }

    function closeChatHistoryPanel() {
        historyPanel.closeMenu();
        historyPanel.closeDeleteModal();
        historyPanel.closeRenameModal();
        if (window.CuttleTooltips && typeof window.CuttleTooltips.hide === 'function') {
            window.CuttleTooltips.hide();
        }
        // Don't keep "Show N more" reveals across open/close — each open starts fresh.
        historyPanel.clearGroupReveals();
        const panel = getChatHistoryPanel();
        const overlay = document.getElementById('chatHistoryPanelOverlay');
        animateHistoryPanelSlide(panel);
        if (panel) panel.classList.remove('open', 'mobile-open');
        if (overlay) overlay.classList.remove('active');
        stopHistoryGeneratingPoll();
    }

    function toggleChatHistoryPanel() {
        const panel = getChatHistoryPanel();
        if (!panel) return;
        if (panel.classList.contains('open')) {
            closeChatHistoryPanel();
        } else {
            openChatHistoryPanel();
        }
    }

    // Page Escape shortcuts; the shared diff popup handles its own keyboard input.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        const menu = document.getElementById('historyItemOverflowMenu');
        if (historyPanel.isDeleteModalOpen()) {
            historyPanel.closeDeleteModal();
            e.preventDefault();
            return;
        }
        if (historyPanel.isRenameModalOpen()) {
            historyPanel.closeRenameModal();
            e.preventDefault();
            return;
        }
        if (menu && !menu.hidden) {
            historyPanel.closeMenu();
            e.preventDefault();
            return;
        }
        const panel = getChatHistoryPanel();
        if (panel && panel.classList.contains('open')) {
            closeChatHistoryPanel();
        }
    });
    
    function isAuthMode() {
        return !!(window.CuttleAuth && window.CuttleAuth.isAuthenticated && window.CuttleAuth.isAuthenticated());
    }

    /** Wait until /api/auth/me has settled so ?chat= deep-links enter auth mode. */
    function waitForAuthReady(timeoutMs = 8000) {
        if (isAuthMode()) return Promise.resolve(true);
        if (!window.CuttleAuth) {
            // auth.js may still be parsing — poll briefly
            return new Promise((resolve) => {
                const t0 = Date.now();
                const tick = () => {
                    if (isAuthMode()) return resolve(true);
                    if (Date.now() - t0 > timeoutMs) return resolve(false);
                    setTimeout(tick, 50);
                };
                tick();
            });
        }
        return new Promise((resolve) => {
            let done = false;
            const finish = (ok) => {
                if (done) return;
                done = true;
                window.removeEventListener('cuttle-auth-changed', onAuth);
                clearTimeout(timer);
                resolve(ok);
            };
            const onAuth = () => finish(isAuthMode());
            window.addEventListener('cuttle-auth-changed', onAuth);
            const timer = setTimeout(() => finish(isAuthMode()), timeoutMs);
            // Auth may already be mid-flight and about to emit.
            setTimeout(() => {
                if (isAuthMode()) finish(true);
            }, 0);
        });
    }

    function authSessionIdForRequest() {
        // chat_page's currentSessionId is authoritative while on this page
        // (null = new chat / welcome — never fall back to auth's "latest").
        return session.current();
    }

    async function ensureAuthChatSession() {
        // Sessions are created on first message by /api/chat — never pre-create empties.
        return session.current();
    }

    /** "+" on a project heading: fresh chat already scoped to that project. */
    function newChatForProject(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const btn = event && event.currentTarget;
        if (!btn) return;
        const rawId = btn.getAttribute('data-project-id');
        const id = rawId ? rawId : null;
        const name = btn.getAttribute('data-project-name') || '';
        const path = btn.getAttribute('data-project-path') || '';
        const proj = CuttleChatProject.findProject(projects, { id, path, name });
        Promise.resolve(createNewChat()).then(() => {
            // Runs after createNewChat's reconcile so the group's project wins
            // over the Cuttle default fallback.
            setChatProject(proj, { silent: true });
            updateProjectDisplay();
        });
    }

    function refreshChatHistoryList(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const btn = (event && event.currentTarget)
            || document.getElementById('chatHistoryRefreshBtn');
        if (btn) btn.classList.add('is-refreshing');

        const done = function () {
            // Button may have been replaced by re-render; clear on the new one too.
            const next = document.getElementById('chatHistoryRefreshBtn');
            if (next) next.classList.remove('is-refreshing');
            if (btn && btn !== next) btn.classList.remove('is-refreshing');
        };

        if (!document.getElementById('chatHistory')) {
            done();
            return Promise.resolve();
        }
        if (isAuthMode() && typeof loadChatSessions === 'function') {
            return Promise.resolve(loadChatSessions()).then(function () {
                // Prefer re-applying search/filters — a full list paint races with
                // typing (open refresh, generating poll, title refresh timers).
                historyPanel.reloadKeepingSearch();
                if (session.current()) updateChatSessionTitle(session.current());
                done();
            }).catch(function () {
                historyPanel.reloadKeepingSearch();
                if (session.current()) updateChatSessionTitle(session.current());
                done();
            });
        }
        historyPanel.reloadKeepingSearch();
        if (session.current()) updateChatSessionTitle(session.current());
        done();
        return Promise.resolve();
    }

    // Chat Session Management
    /** Keep ?chat=<id> in the address bar (iframe + app shell parent). */
    function syncChatUrl(sessionId) {
        const id = (sessionId != null && sessionId !== '')
            ? String(canonicalizeChatSessionId(sessionId))
            : null;
        try {
            const url = new URL(window.location.href);
            if (id) {
                url.searchParams.set('chat', id);
                url.searchParams.delete('session');
            } else {
                url.searchParams.delete('chat');
                url.searchParams.delete('session');
                url.searchParams.delete('msg');
            }
            const next = url.pathname + url.search + url.hash;
            if (next !== window.location.pathname + window.location.search + window.location.hash) {
                history.replaceState(null, '', next);
            }
        } catch (_) {}
        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'cuttle-chat-url', chatId: id }, '*');
            }
        } catch (_) {}
    }

    // createNewChat is owned by chat_session.js (CuttleChatSession.createSessionBoot).
    // The page builds the capability host lazily and keeps this thin call site.
    async function createNewChat() {
        return sessionBoot().createNewChat();
    }
    
    async function sendWelcomeMessage() {
        LOG('sendWelcomeMessage called');
        // Same as sendMessage: allow fall-through once a turn is generating so
        // we don't drop the first follow-up while the welcome send is in flight.
        if (sendDispatchGuard && !isSessionGenerating()) {
            LOG('sendWelcomeMessage: ignored (dispatch guard)');
            return;
        }
        const input = document.getElementById('welcomeChatInput');
        if (!input) {
            LOG_ERR('sendWelcomeMessage: welcomeChatInput not found');
            return;
        }
        const message = composeMessageWithSlashChip(input).trim();
        const attachments = pendingAttachments.slice();

        if (!isSendableComposerMessage(message, attachments)) {
            LOG('sendWelcomeMessage: early return (empty / agent-only)');
            return;
        }
        maybeRefreshCursorModelsCatalogFromOutbound(message);
        // Welcome composer is first-turn only; if a reply is already going,
        // hand off to the main send path so follow-ups queue normally.
        healStaleGeneratingState();
        if (isSessionGenerating()) {
            const chatInput = document.getElementById('chatInput');
            if (chatInput) chatInput.value = input.value;
            input.value = '';
            document.getElementById('welcomeScreen').style.display = 'none';
            document.getElementById('chatArea').style.display = 'flex';
            if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
            sendMessage();
            return;
        }
        LOG('sendWelcomeMessage: sending message');
        sendDispatchGuard = true;
        await ensureCodexEffortForSend(message);

        input.value = '';
        clearComposerDraft('new');
        applyStickySlashAfterComposerSend(message);
        const displayMessage = formatMessageWithAttachments(message, attachments);
        clearPendingAttachments();
        
        // Hide welcome screen and show chat area
        document.getElementById('welcomeScreen').style.display = 'none';
        document.getElementById('chatArea').style.display = 'flex';
        if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        
        // Focus on main chat input
        const chatInput = document.getElementById('chatInput');
        try { chatInput.focus({ preventScroll: true }); } catch (_) { chatInput.focus(); }
        
        // Send the message through the main chat flow
        const userTs = Date.now();
        addMessageToUI(displayMessage, 'user', { timestamp: userTs, attachments });
        transcriptNav.autoScrollChatToBottom(true);
        saveChatSession(displayMessage, 'user', { attachments });
        recordPromptHistory(message || displayMessage);

        const outbound = message || '(see attached files)';
        beginLocalGeneration();
        inFlightUserMessage = outbound;
        
        // Process the message
        Promise.resolve(processMessage(outbound, { attachments })).finally(() => {
            sendDispatchGuard = false;
        });
    }
    
    function handleWelcomeInputKeyDown(event) {
        const input = document.getElementById('welcomeChatInput');
        if (input && handleSlashKeyDown(event, input)) {
            event.stopPropagation();
            return;
        }
        if (input && handlePromptHistoryKeyDown(event, input)) {
            event.stopPropagation();
            return;
        }
        if (composerEnterSends(event)) {
            LOG('handleWelcomeInputKeyDown: Enter pressed, preventing default and sending');
            event.preventDefault();
            sendWelcomeMessage();
        }
    }
    
    function autoResizeWelcomeTextarea() {
        const textarea = document.getElementById('welcomeChatInput');
        if (textarea) {
            textarea.style.height = 'auto';
            const next = Math.min(textarea.scrollHeight, 168);
            textarea.style.height = next + 'px';
            textarea.style.overflowY = textarea.scrollHeight > 168 ? 'auto' : 'hidden';
        }
    }
    
    // ── Deep-link restore (?chat=) ─────────────────────────────
    // chat_page.html raises data-chat-restoring and kicks off the transcript
    // fetch before this file even parses; everything below consumes that.

    function clearChatRestoreVeil() {
        try { document.documentElement.removeAttribute('data-chat-restoring'); } catch (_) {}
    }

    /** The in-flight head-script transcript fetch, once, if it's for this chat. */
    function takeChatTranscriptPrefetch(sessionId) {
        const pre = window.__cuttleChatPrefetch;
        if (!pre || pre.used || !pre.promise) return null;
        if (!sessionIdsEqual(pre.id, sessionId)) return null;
        pre.used = true;
        // A transcript fetched a while ago can already be missing turns; only a
        // boot-fresh one is worth reusing.
        if (Date.now() - (pre.startedAt || 0) > 15000) return null;
        return pre.promise;
    }

    /** Drop to the new-chat splash (also the give-up path for a slow restore). */
    function showWelcomeSplash() {
        clearChatRestoreVeil();
        detachLocalGenerationForNavigation();
        const welcome = document.getElementById('welcomeScreen');
        const area = document.getElementById('chatArea');
        if (welcome) welcome.style.display = 'flex';
        if (area) area.style.display = 'none';
        const chatMessages = document.getElementById('chatMessages');
        if (chatMessages) chatMessages.innerHTML = '';
        resetChatHistoryWindow();
        transcriptNav.resetNav();
        releaseManualUnreadHoldIfLeaving(null);
        session.create(null);
        adoptFollowupQueue(session.current());
        applySessionIdentity(null);
        updateChatIdBadge(null);
        if (window.CuttleAuth && window.CuttleAuth.setCurrentChatSession) {
            window.CuttleAuth.setCurrentChatSession(null);
        }
        syncChatUrl(null);
        applyStarredSlashChips();
        restoreWelcomeStickySlash();
        restoreComposerDraft('new', { force: true });
        focusWelcomeComposer();
    }

    /** First promise to resolve truthy, else null once all settle or time runs out. */
    function firstTruthy(promises, timeoutMs) {
        const live = promises.filter(Boolean);
        if (!live.length) return Promise.resolve(null);
        return new Promise((resolve) => {
            let settled = false;
            let pending = live.length;
            const done = (value) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                resolve(value);
            };
            const timer = setTimeout(() => done(null), timeoutMs);
            live.forEach((p) => {
                Promise.resolve(p).then(
                    (v) => { if (v) done(v); else if (--pending === 0) done(null); },
                    () => { if (--pending === 0) done(null); }
                );
            });
        });
    }

    // bootRestoreSession is owned by chat_session.js (CuttleChatSession.createSessionBoot).
    // The page builds the capability host lazily and keeps this thin call site.
    async function bootRestoreSession(sessionParam) {
        return sessionBoot().bootRestoreSession(sessionParam);
    }

    // loadChatSession is owned by chat_session.js (CuttleChatSession.createLoader).
    // The page builds the capability host lazily and keeps this thin call site.
    async function loadChatSession(sessionId, opts = {}) {
        return sessionLoader().loadChatSession(sessionId, opts);
    }

    // The server auto-names sessions (fallback + background LLM title) shortly
    // after each assistant reply; re-pull the list so the sidebar picks it up.
    // OpenAI titles are usually ready in ~1s; later ticks catch slower local models.
    let _titleRefreshTimers = [];
    function scheduleHistoryTitleRefresh() {
        if (!document.getElementById('chatHistory')) return;
        _titleRefreshTimers.forEach(clearTimeout);
        _titleRefreshTimers = [];
        [800, 3000, 8000, 18000].forEach(function(delay) {
            _titleRefreshTimers.push(setTimeout(refreshChatHistoryList, delay));
        });
    }

    function reportSessionToShell(sessionId, opts) {
        if (!inAppShell) return;
        try {
            window.parent.postMessage({
                type: 'cuttle-chat-session',
                sessionId: sessionId == null ? null : String(sessionId),
                preferFocus: !!(opts && opts.preferFocus),
            }, '*');
        } catch (_) {}
    }

    function notifyShellPaneActivity() {
        if (!inAppShell) return;
        shellPaneFocused = true;
        try {
            window.parent.postMessage({ type: 'cuttle-pane-activity' }, '*');
        } catch (_) {}
    }

    function getCachedHubLiveStatus(sessionId) {
        if (!hubLiveStatusCache || sessionId == null) return null;
        if (!sessionIdsEqual(hubLiveStatusCache.sessionId, sessionId)) return null;
        if ((Date.now() - hubLiveStatusCache.at) > 8000) return null;
        return hubLiveStatusCache.live;
    }

    async function applyHubLiveStatusPayload(live, sessionId) {
        if (!live || sessionId == null) return;
        if (session.current() == null || !sessionIdsEqual(session.current(), sessionId)) return;
        hubLiveStatusCache = { sessionId: String(sessionId), live, at: Date.now() };
        shellHubSeen = true;
        // Batch live-status is the Electron strip's revision pulse — forward it.
        noteWidgetsRevision(live);

        try { subagentLaunchers.syncLive(live.subagents); } catch (_) {}
        if (isLoadingThisSession()) {
            const statusLabel = live.status || undefined;
            ensureLocalTypingIndicator(statusLabel);
            try { subagentLaunchers.paintLive(); } catch (_) {}
        }

        if (live.status && (isLoadingThisSession() || document.getElementById('typing-indicator-remote'))) {
            updateTypingStatus(live.status);
            if (document.getElementById('typing-indicator-remote')) {
                updateRemoteWaitingStatus(live.status, live.report_url);
            }
        }
        if (live.report_url) updateTypingIndicatorQueryLink(live.report_url);

        const stillGoing = liveStatusLooksActive(live);
        if (isLoadingThisSession() && turnAlreadyShowsAssistantReply() && !activeEventSource) {
            healStaleGeneratingState();
        }
        if (isLoadingThisSession() && !stillGoing) {
            clearStreamStatusPoll();
            try {
                window.CuttleNetDebug && window.CuttleNetDebug.event(
                    'hub-status-idle',
                    'recovering session=' + sessionId
                );
            } catch (_) {}
            await syncSessionMessagesFromServer();
            if (isLoadingThisSession() && !stopState.userStopped) {
                const recovered = await recoveryController().recoverChatResultFromServer(session.current());
                if (recovered && String(recovered.response || '').trim()) {
                    if (consumeCancelledAgentReply(recovered.response)) {
                        finishLocalStreamFromServerSync();
                    } else if (!turnAlreadyShowsAssistantReply(recovered.response)) {
                        addMessageToUI(recovered.response, 'assistant', {
                            report_url: recovered.report_url,
                            query_id: recovered.query_id,
                            timestamp: Date.now(),
                            routing_badge: recovered.routing_badge || undefined,
                            usage: normalizeUsagePayload(recovered.usage) || undefined,
                        });
                        notifyAssistantResponseReady(session.current(), { fromSync: true });
                        finishLocalStreamFromServerSync();
                    } else {
                        finishLocalStreamFromServerSync();
                    }
                }
                // No this-turn assistant yet — do NOT abort the in-flight
                // collectPendingResult. A stale idle snapshot used to do that
                // and the UI painted "No response received." while Cursor still ran.
            }
        } else if (!isLoadingThisSession()) {
            // Keep remote waiting / history generating flags in sync without a GET.
            updateRemoteWaitingFromMessages([], live);
            if (!stillGoing) {
                try {
                    await syncSessionMessagesFromServer();
                } catch (_) {}
            }
        }
    }

    // Session lifecycle writers (adopt/save) are owned by chat_session.js
    // (CuttleChatSession.createLifecycle). The host is built lazily on first
    // use so page constants declared later in this IIFE are initialized; the
    // page keeps only these thin call sites.
    let _sessionLifecycle = null;
    function sessionLifecycle() {
        if (!_sessionLifecycle) {
            _sessionLifecycle = CuttleChatSession.createLifecycle(session, {
                canonicalizeChatSessionId: canonicalizeChatSessionId,
                sessionIdsEqual: sessionIdsEqual,
                toAuthDbSessionId: toAuthDbSessionId,
                isAuthMode: isAuthMode,
                migratePromptHistorySession: migratePromptHistorySession,
                migrateSessionPrefs: migrateSessionPrefs,
                migrateComposerDraft: migrateComposerDraft,
                migrateLocalChatSession: migrateLocalChatSession,
                turnBadgeBySession: turnBadgeBySession,
                getCompletionNotifications: () => window.CuttleCompletionNotifications,
                generation: generation,
                getCompletionNotificationPendingId: () => completionNotificationPendingId,
                releaseManualUnreadHoldIfLeaving: releaseManualUnreadHoldIfLeaving,
                adoptFollowupQueue: adoptFollowupQueue,
                updateChatIdBadge: updateChatIdBadge,
                storage: localStorage,
                getAuth: () => window.CuttleAuth,
                syncChatUrl: syncChatUrl,
                reportSessionToShell: reportSessionToShell,
                clearSessionPrefs: clearSessionPrefs,
                newComposerPrefsId: newComposerPrefsId,
                persistProjectForCurrentSession: persistProjectForCurrentSession,
                persistStickySlashForCurrentSession: persistStickySlashForCurrentSession,
                flushDirtyAgentSelectionsOnAdopt: () => agentControls.flushDirtyAgentSelectionsOnAdopt(),
                rebindGeneration: (g, sid) => CuttleChatGeneration.rebindGeneration(g, sid),
                setHistorySessionRunning: setHistorySessionRunning,
                refreshChatHistoryList: refreshChatHistoryList,
                scheduleHistoryTitleRefresh: scheduleHistoryTitleRefresh,
                startMessageSync: startMessageSync,
                generateLocalChatId: generateLocalChatId,
                getCurrentProject: () => currentProject,
                normalizeAttachmentList: normalizeAttachmentList,
                reloadHistoryListKeepingSearch: () => historyPanel.reloadKeepingSearch(),
            });
        }
        return _sessionLifecycle;
    }

    // loadChatSession host (CuttleChatSession.createLoader). The page supplies
    // every capability the loader needs plus the mutable page state it advances.
    // Built lazily on first load so page constants declared later are initialized.
    let _sessionLoader = null;
    function sessionLoader() {
        if (!_sessionLoader) {
            _sessionLoader = CuttleChatSession.createLoader(session, {
                sessionIdsEqual: sessionIdsEqual,
                saveComposerDraft: saveComposerDraft,
                clearAllSlashChips: clearAllSlashChips,
                canonicalizeChatSessionId: canonicalizeChatSessionId,
                closeChatHistoryPanel: closeChatHistoryPanel,
                detachLocalGenerationForNavigation: detachLocalGenerationForNavigation,
                isAuthMode: isAuthMode,
                releaseManualUnreadHoldIfLeaving: releaseManualUnreadHoldIfLeaving,
                cancelDetachedSessionCompletionWatch: (sid) => recoveryController().cancelDetachedSessionCompletionWatch(sid),
                adoptFollowupQueue: adoptFollowupQueue,
                setHistorySessionRunning: setHistorySessionRunning,
                updateChatIdBadge: updateChatIdBadge,
                loadPromptHistoryForSession: loadPromptHistoryForSession,
                syncChatUrl: syncChatUrl,
                refreshChatWidgets: refreshChatWidgets,
                restoreSessionStickySlash: restoreSessionStickySlash,
                reportSessionToShell: reportSessionToShell,
                sessionPrefsHasUnreadFlag: sessionPrefsHasUnreadFlag,
                resetChatAttentionState: resetChatAttentionState,
                sessionPrefsUnreadIsError: sessionPrefsUnreadIsError,
                resetChatHistoryWindow: resetChatHistoryWindow,
                removeRemoteWaitingIndicator: removeRemoteWaitingIndicator,
                stopMessageSync: stopMessageSync,
                toAuthDbSessionId: toAuthDbSessionId,
                takeChatTranscriptPrefetch: takeChatTranscriptPrefetch,
                fetchWithTimeout: fetchWithTimeout,
                sessionMessagesUrl: sessionMessagesUrl,
                windowChatHistoryMessages: windowChatHistoryMessages,
                annotateMessageProjects: annotateMessageProjects,
                reconcileChatProject: reconcileChatProject,
                preferredModelFromMessages: preferredModelFromMessages,
                hydrateAgentSelectionFromSessionData: hydrateAgentSelectionFromSessionData,
                applySessionIdentity: applySessionIdentity,
                rememberChatSessionTitle: rememberChatSessionTitle,
                updateChatSessionTitle: updateChatSessionTitle,
                beginSuppressChatAttention: beginSuppressChatAttention,
                appendServerMessageToUI: appendServerMessageToUI,
                endSuppressChatAttention: endSuppressChatAttention,
                applyServerFollowups: applyServerFollowups,
                acceptSessionAttention: acceptSessionAttention,
                clearChatRestoreVeil: clearChatRestoreVeil,
                reconcileCuttleButtonLocks: reconcileCuttleButtonLocks,
                fetchChatLiveStatus: fetchChatLiveStatus,
                updateRemoteWaitingFromMessages: updateRemoteWaitingFromMessages,
                healStaleGeneratingState: healStaleGeneratingState,
                isSessionGenerating: isSessionGenerating,
                scheduleFollowupDrain: scheduleFollowupDrain,
                refreshTypingIndicatorHeaderBadges: refreshTypingIndicatorHeaderBadges,
                applyGeneratingFlagsFromSessions: applyGeneratingFlagsFromSessions,
                _restorePendingAttachments: _restorePendingAttachments,
                restoreComposerDraft: restoreComposerDraft,
                markChatSessionRead: markChatSessionRead,
                applyHistorySubagentExpandState: () => historyPanel.applyHistorySubagentExpandState(),
                syncHistoryUnreadIndicators: syncHistoryUnreadIndicators,
                syncChatAttentionIndicators: syncChatAttentionIndicators,
                setupChatHistoryLazyLoad: setupChatHistoryLazyLoad,
                startMessageSync: startMessageSync,
                consumeCancelledAgentReply: consumeCancelledAgentReply,
                addMessageToUI: addMessageToUI,
                normalizeUsagePayload: normalizeUsagePayload,
                _mutateSessionIdSet: _mutateSessionIdSet,
                CHAT_HISTORY_PAGE_SIZE: CHAT_HISTORY_PAGE_SIZE,
                stopState: stopState,
                transcriptNav: transcriptNav,
                agentControls: agentControls,
                formAwaitingSessionIds: formAwaitingSessionIds,
                slashPaletteSupplement: slashPaletteSupplement,
                generation: generation,
                storage: localStorage,
                pinChatLayout: function () {
                    if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
                },
                getAuth: function () { return window.CuttleAuth; },
                getNetDebug: function () { return window.CuttleNetDebug; },
                getFollowupQueue: function () { return followupController().getState(); },
                getEditingFollowupId: function () { return followupController().getEditingId(); },
                setHubLiveStatusCache: function (v) { hubLiveStatusCache = v; },
                setLastSeenServerMessageId: function (v) { lastSeenServerMessageId = v; },
                setDeferOpenChatGeneratingFromSessions: function (v) { deferOpenChatGeneratingFromSessions = v; },
                getLastHydratedSessionProject: function () { return lastHydratedSessionProject; },
                setLastHydratedSessionProject: function (v) { lastHydratedSessionProject = v; },
            });
        }
        return _sessionLoader;
    }

    // createNewChat / bootRestoreSession host (CuttleChatSession.createSessionBoot).
    // The page supplies every capability these paths need plus the mutable page
    // state they advance. Built lazily on first use so page constants declared
    // later in this IIFE are initialized.
    let _sessionBoot = null;
    function sessionBoot() {
        if (!_sessionBoot) {
            _sessionBoot = CuttleChatSession.createSessionBoot(session, {
                saveComposerDraft: saveComposerDraft,
                applyStarredSlashChips: applyStarredSlashChips,
                clearChatRestoreVeil: clearChatRestoreVeil,
                storage: localStorage,
                detachLocalGenerationForNavigation: detachLocalGenerationForNavigation,
                resetChatHistoryWindow: resetChatHistoryWindow,
                transcriptNav: transcriptNav,
                releaseManualUnreadHoldIfLeaving: releaseManualUnreadHoldIfLeaving,
                adoptFollowupQueue: adoptFollowupQueue,
                applyHistorySubagentExpandState: () => historyPanel.applyHistorySubagentExpandState(),
                applySessionIdentity: applySessionIdentity,
                setCurrentProject: function (v) { currentProject = v; },
                setHubLiveStatusCache: function (v) { hubLiveStatusCache = v; },
                reconcileChatProject: reconcileChatProject,
                refreshChatWidgets: refreshChatWidgets,
                closeChatHistoryPanel: closeChatHistoryPanel,
                updateChatIdBadge: updateChatIdBadge,
                loadPromptHistoryForSession: loadPromptHistoryForSession,
                getAuth: function () { return window.CuttleAuth; },
                stopMessageSync: stopMessageSync,
                clearFollowupQueue: clearFollowupQueue,
                stopState: stopState,
                syncChatUrl: syncChatUrl,
                reportSessionToShell: reportSessionToShell,
                restoreWelcomeStickySlash: restoreWelcomeStickySlash,
                restoreComposerDraft: restoreComposerDraft,
                pinChatLayout: function () {
                    if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
                },
                resetChatAttentionState: resetChatAttentionState,
                focusWelcomeComposer: focusWelcomeComposer,
                takeChatTranscriptPrefetch: takeChatTranscriptPrefetch,
                waitForAuthReady: waitForAuthReady,
                firstTruthy: firstTruthy,
                loadChatSession: loadChatSession,
                showWelcomeSplash: showWelcomeSplash,
                showToast: function (message, type) {
                    if (typeof window.showToast === 'function') window.showToast(message, type);
                },
            });
        }
        return _sessionBoot;
    }

    function adoptChatSessionId(sessionId) {
        return sessionLifecycle().adoptChatSessionId(sessionId);
    }

    // Owned by chat_generation.js — thin adapter: cadence decision lives
    // in the owner (intervals in CuttleChatGeneration.SYNC_MS); the page
    // supplies the snapshot and owns the timer mechanics.
    function messageSyncDelayMs() {
        return CuttleChatGeneration.decideSyncDelayMs({
            backgrounded: pageIsBackgrounded(),
            shellUnfocused: inAppShell && !shellPaneFocused,
            loading: generation.loading,
            generating: isSessionGenerating(),
        });
    }

    function stopMessageSync(opts = {}) {
        if (messageSyncTimer) {
            clearTimeout(messageSyncTimer);
            messageSyncTimer = null;
        }
        removeRemoteWaitingIndicator();
        if (opts.resetCursor) lastSeenServerMessageId = 0;
    }

    /** Build `/api/auth/sessions/:id/messages` with optional limit / before_id / after_id. */
    function sessionMessagesUrl(authSid, opts = {}) {
        const params = new URLSearchParams();
        if (opts.limit != null) params.set('limit', String(opts.limit));
        if (opts.beforeId != null) params.set('before_id', String(opts.beforeId));
        if (opts.afterId != null) params.set('after_id', String(opts.afterId));
        const q = params.toString();
        return `/api/auth/sessions/${encodeURIComponent(authSid)}/messages${q ? `?${q}` : ''}`;
    }

    function resetChatHistoryWindow() {
        chatHistoryHasMore = false;
        chatHistoryOlderVisibleCount = 0;
        chatHistoryOldestId = null;
        chatHistoryLoadingOlder = false;
        chatHistoryClientBuffer = [];
        const ind = document.getElementById('chatHistoryLoadOlder');
        if (ind) ind.remove();
    }

    /** Count non-system rows (same filter as message-nav / CH-refs). */
    function countVisibleChatMessages(messages) {
        return CuttleChatMessages.countVisibleChatMessages(messages);
    }

    /**
     * If the payload is an unpaged full dump, keep only the newest page on
     * screen and stash the rest for scroll-up. Returns the messages to paint.
     */
    function windowChatHistoryMessages(data) {
        if (!data || !Array.isArray(data.messages)) return [];
        const plan = CuttleChatMessages.windowHistoryMessages({
            messages: data.messages,
            hasMore: data.has_more,
            olderVisibleCount: data.older_visible_count,
            pageSize: CHAT_HISTORY_PAGE_SIZE,
        });
        chatHistoryClientBuffer = plan.buffer;
        chatHistoryHasMore = plan.hasMore;
        if (plan.olderVisibleCount !== undefined) {
            chatHistoryOlderVisibleCount = plan.olderVisibleCount;
        }
        if (plan.oldestId !== undefined) chatHistoryOldestId = plan.oldestId;
        syncChatHistoryLoadOlderIndicator();
        return plan.paint;
    }

    function applyChatHistoryPageMeta(data, messages) {
        if (!data || typeof data !== 'object') return;
        const meta = CuttleChatMessages.historyPageMeta({
            hasMore: data.has_more,
            olderVisibleCount: data.older_visible_count,
            messages,
        });
        if (meta.hasMore !== undefined) chatHistoryHasMore = meta.hasMore;
        if (meta.olderVisibleCount !== undefined) {
            chatHistoryOlderVisibleCount = meta.olderVisibleCount;
        }
        if (meta.oldestId !== undefined) chatHistoryOldestId = meta.oldestId;
        syncChatHistoryLoadOlderIndicator();
    }

    function syncChatHistoryLoadOlderIndicator() {
        const box = document.getElementById('chatMessages');
        if (!box) return;
        let ind = document.getElementById('chatHistoryLoadOlder');
        if (!chatHistoryHasMore && !chatHistoryLoadingOlder) {
            if (ind) ind.remove();
            return;
        }
        if (!ind) {
            ind = document.createElement('div');
            ind.id = 'chatHistoryLoadOlder';
            ind.className = 'chat-history-load-older';
            ind.setAttribute('aria-live', 'polite');
            box.insertBefore(ind, box.firstChild);
        } else if (box.firstChild !== ind) {
            box.insertBefore(ind, box.firstChild);
        }
        ind.hidden = !chatHistoryLoadingOlder;
        ind.textContent = chatHistoryLoadingOlder ? 'Loading earlier messages…' : '';
    }

    /**
     * Fetch the next older page and prepend it, preserving scroll position so the
     * bubble the user was looking at stays put.
     */
    async function loadOlderChatMessages() {
        if (!isAuthMode() || session.current() == null) return false;
        if (!chatHistoryHasMore || chatHistoryLoadingOlder) return false;

        const box = document.getElementById('chatMessages');
        if (!box) return false;

        chatHistoryLoadingOlder = true;
        syncChatHistoryLoadOlderIndicator();
        const prevHeight = box.scrollHeight;
        const prevTop = box.scrollTop;

        try {
            let msgs = [];
            let pageMeta = null;

            if (chatHistoryClientBuffer.length) {
                const take = Math.min(CHAT_HISTORY_PAGE_SIZE, chatHistoryClientBuffer.length);
                msgs = chatHistoryClientBuffer.splice(-take, take);
                pageMeta = {
                    has_more: chatHistoryClientBuffer.length > 0,
                    older_visible_count: countVisibleChatMessages(chatHistoryClientBuffer),
                };
            } else {
                if (chatHistoryOldestId == null) {
                    chatHistoryHasMore = false;
                    return false;
                }
                const authSid = toAuthDbSessionId(session.current());
                if (!authSid) return false;
                const sessionAtStart = String(session.current());
                const resp = await fetchWithTimeout(
                    sessionMessagesUrl(authSid, {
                        limit: CHAT_HISTORY_PAGE_SIZE,
                        beforeId: chatHistoryOldestId,
                    }),
                    { credentials: 'include', cache: 'no-store' },
                    12000
                );
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();
                if (String(session.current()) !== sessionAtStart) return false;
                if (!data.success || !Array.isArray(data.messages)) {
                    chatHistoryHasMore = false;
                    return false;
                }
                // Old server ignoring before_id may echo the full transcript —
                // only keep rows older than what we already have.
                msgs = data.messages.filter((m) => {
                    if (!m || m.id == null) return false;
                    const mid = Number(m.id);
                    return Number.isFinite(mid) && mid < chatHistoryOldestId;
                });
                if (typeof data.has_more === 'boolean') {
                    pageMeta = {
                        has_more: data.has_more,
                        older_visible_count: data.older_visible_count,
                    };
                } else {
                    pageMeta = {
                        has_more: false,
                        older_visible_count: 0,
                    };
                }
            }

            if (!msgs.length) {
                if (pageMeta) applyChatHistoryPageMeta(pageMeta, msgs);
                else {
                    chatHistoryHasMore = false;
                    syncChatHistoryLoadOlderIndicator();
                }
                return false;
            }

            annotateMessageProjects(msgs);
            const anchor = box.querySelector('.message');
            beginSuppressChatAttention();
            try {
                for (const msg of msgs) {
                    prependServerMessageToUI(msg, {
                        announce: false,
                        insertBefore: anchor,
                        skipNav: true,
                        skipScroll: true,
                    });
                }
            } finally {
                endSuppressChatAttention();
            }

            applyChatHistoryPageMeta(pageMeta || {}, msgs);
            transcriptNav.updateMessageNav();

            const newHeight = box.scrollHeight;
            box.scrollTop = prevTop + (newHeight - prevHeight);
            transcriptNav.holdScrollPin(200);
            return true;
        } catch (e) {
            console.warn('[Cuttle Chat] load older messages failed:', e);
            return false;
        } finally {
            chatHistoryLoadingOlder = false;
            syncChatHistoryLoadOlderIndicator();
        }
    }

    function setupChatHistoryLazyLoad() {
        const box = document.getElementById('chatMessages');
        if (!box || _chatHistoryLazyScrollWired) return;
        _chatHistoryLazyScrollWired = true;
        box.addEventListener('scroll', () => {
            if (!isAuthMode() || session.current() == null) return;
            if (!chatHistoryHasMore || chatHistoryLoadingOlder) return;
            if (transcriptNav.isScrollPinHeld()) return;
            if (box.scrollTop > 72) return;
            loadOlderChatMessages();
        }, { passive: true });
    }

    function scheduleNextMessageSync() {
        if (!isAuthMode() || session.current() == null) return;
        if (messageSyncTimer) {
            clearTimeout(messageSyncTimer);
            messageSyncTimer = null;
        }
        messageSyncTimer = setTimeout(async function () {
            messageSyncTimer = null;
            try {
                await syncSessionMessagesFromServer();
            } finally {
                if (isAuthMode() && session.current() != null) {
                    scheduleNextMessageSync();
                }
            }
        }, messageSyncDelayMs());
    }

    function startMessageSync() {
        if (!isAuthMode() || session.current() == null) return;
        if (messageSyncTimer) return;
        scheduleNextMessageSync();
    }

    function messageRecordDeps() {
        return {
            parseSessionTimestamp,
            normalizeAttachmentList,
            normalizeUsagePayload,
            projectFromMessageOpts,
            projects,
            currentProject,
        };
    }

    function authMessageOptsFromServer(msg) {
        return CuttleChatMessages.authMessageOptsFromServer(msg, messageRecordDeps());
    }

    /** Pull the latest requested Cursor model from assistant message metadata. */
    function preferredModelFromMessages(messages) {
        return CuttleChatMessages.preferredModelFromMessages(messages);
    }

    function projectFromMessageRecord(msg) {
        return CuttleChatMessages.projectFromMessageRecord(msg, messageRecordDeps());
    }

    function annotateMessageProjects(messages) {
        return CuttleChatMessages.annotateMessageProjects(messages, messageRecordDeps());
    }

    function appendServerMessageToUI(msg, opts = {}) {
        if (!msg || msg.id == null) return;
        const cancelOpts = Object.assign({ announce: false }, opts);
        if (msg.role === 'assistant' && consumeCancelledAgentReply(msg.content, cancelOpts)) {
            const mid = Number(msg.id);
            if (Number.isFinite(mid) && mid > lastSeenServerMessageId) lastSeenServerMessageId = mid;
            return;
        }
        const mid = Number(msg.id);
        if (mid > lastSeenServerMessageId) lastSeenServerMessageId = mid;
        addMessageToUI(msg.content, msg.role, Object.assign({}, authMessageOptsFromServer(msg), {
            skipNav: !!opts.skipNav,
            skipScroll: !!opts.skipScroll,
            prepend: !!opts.prepend,
            insertBefore: opts.insertBefore || null,
        }));
        // Sync can learn about an older id after a newer optimistic bubble is
        // already on screen — move the new node into id order.
        if (!opts.prepend) reorderChatMessageNodeById(mid);
    }

    function prependServerMessageToUI(msg, opts = {}) {
        return appendServerMessageToUI(msg, Object.assign({}, opts, { prepend: true }));
    }

    function updateServerMessageInPlace(el, msg, { notifyTerminal = false } = {}) {
        if (!el || !msg) return false;
        const next = String(msg.content || '');
        const changed = normalizeMessageContentForMatch(el.dataset.rawContent) !== normalizeMessageContentForMatch(next);
        const opts = authMessageOptsFromServer(msg);
        if (changed) {
            const content = el.querySelector('.message-content');
            if (content) {
                content.innerHTML = formatMessage(stripLegacyAgentChrome(next));
                try { activateEnhancements(el); } catch (_) {}
            }
            el.dataset.rawContent = next;
        }
        if (opts.supervised_task_id) {
            el.dataset.supervisedTaskId = String(opts.supervised_task_id);
            el.classList.add('supervised-coordinator-message');
        }
        const disclosure = el.querySelector('.supervised-activity-disclosure, .supervised-activity-card');
        if (disclosure) wireSupervisedActivityCard(disclosure);
        try { subagentLaunchers.mountForMessage(el, msg.role, opts); } catch (_) {}
        if (changed && notifyTerminal && opts.supervised_terminal) {
            showSupervisedJumpNotification({
                task_id: opts.supervised_task_id,
                phase: opts.supervised_phase,
                terminal: true,
                supervised_delivery_event_id: opts.supervised_delivery_event_id,
                coordinator_response_message_id: opts.coordinator_response_message_id,
            });
        }
        return changed;
    }

    /** Place a just-appended message node among siblings by data-message-id. */
    function reorderChatMessageNodeById(messageId) {
        const mid = Number(messageId);
        if (!Number.isFinite(mid)) return;
        const box = document.getElementById('chatMessages');
        if (!box) return;
        const node = box.querySelector(`.message[data-message-id="${mid}"]`);
        if (!node) return;
        const messages = Array.from(box.querySelectorAll('.message')).filter(
            (el) => el.id !== 'typing-indicator' && el.id !== 'typing-indicator-remote' && el.dataset.stopped !== 'true'
        );
        let insertBefore = null;
        for (const el of messages) {
            if (el === node) continue;
            const id = el.dataset.messageId;
            if (id != null && Number(id) > mid) {
                insertBefore = el;
                break;
            }
        }
        // Server-known ids must stay above optimistic (unstamped) bubbles.
        // Otherwise a late sync of an older assistant reply appends under the
        // user's newer send — looks like the transcript was "reordered".
        if (!insertBefore) {
            const firstUnstamped = messages.find(
                (el) => el !== node && el.dataset.messageId == null
            );
            if (firstUnstamped) insertBefore = firstUnstamped;
        }
        if (!insertBefore) {
            const typing =
                document.getElementById('typing-indicator')
                || document.getElementById('typing-indicator-remote');
            if (typing && node.nextSibling !== typing) {
                box.insertBefore(node, typing);
            }
            return;
        }
        if (node.nextSibling !== insertBefore) {
            box.insertBefore(node, insertBefore);
        }
    }

    function normalizeMessageContentForMatch(s) {
        return String(s || '').replace(/\r\n/g, '\n').trim();
    }

    function claimUnmarkedMessageEl(role, content) {
        const want = normalizeMessageContentForMatch(content);
        const nodes = Array.from(
            document.querySelectorAll(`#chatMessages .message.${role}:not([data-message-id])`)
        ).filter((el) => el.id !== 'typing-indicator' && el.id !== 'typing-indicator-remote' && el.dataset.stopped !== 'true');
        for (const el of nodes) {
            const raw = normalizeMessageContentForMatch(
                el.dataset.rawContent != null ? el.dataset.rawContent : ''
            );
            if (raw === want) return el;
        }
        // Content must match — do not stamp the sole unmarked bubble on a
        // mismatch (that put server ids on the wrong optimistic turn).
        return null;
    }

    /**
     * Adopt the newest unmarked assistant bubble when exact text match fails.
     * Stream vs DB often differs slightly (Cursor cwd banner, trailing newlines)
     * — without this, sync appends a second bubble (~slash chip) a few seconds later.
     */
    function claimLastUnmarkedAssistantEl() {
        const nodes = Array.from(
            document.querySelectorAll('#chatMessages .message.assistant:not([data-message-id])')
        ).filter((el) => el.id !== 'typing-indicator' && el.id !== 'typing-indicator-remote' && el.dataset.stopped !== 'true');
        if (nodes.length !== 1) return null;
        const el = nodes[0];
        // Only the current turn's optimistic assistant (last real bubble).
        // Adopting an older unstamped reply wrote the new text onto dataset
        // but left the old HTML, then skipped adding a bubble — chirp, no
        // visible answer until refresh.
        if (lastVisibleChatMessageEl() !== el) return null;
        return el;
    }

    function removeRemoteWaitingIndicator() {
        document.getElementById('typing-indicator-remote')?.remove();
    }

    function updateRemoteWaitingStatus(statusText, reportUrl) {
        const indicator = document.getElementById('typing-indicator-remote');
        if (!indicator) return;
        const el = indicator.querySelector('.typing-status');
        if (el && statusText) {
            el.textContent = statusText;
        }
        const link = indicator.querySelector('.message-query-log-link');
        if (link && reportUrl) {
            applyQueryLogTarget(link, reportUrl);
        }
    }

    function addRemoteWaitingIndicator(initialStatus) {
        if (document.getElementById('typing-indicator') || document.getElementById('typing-indicator-remote')) {
            if (initialStatus) updateRemoteWaitingStatus(initialStatus);
            // Status-only updates used to leave a stale project chip from the
            // chat we switched away from.
            refreshTypingIndicatorHeaderBadges();
            return;
        }
        const messagesContainer = document.getElementById('chatMessages');
        if (!messagesContainer) return;
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message assistant';
        typingDiv.id = 'typing-indicator-remote';
        const statusLabel = initialStatus || 'Waiting for reply…';
        const pendingChip = (() => {
            const meta = currentTypingSlashMeta();
            const chips = meta && meta.chips ? collapseCursorSlashChips(meta.chips) : [];
            return messageHeaderBadgesHtml(chips, false, currentProject);
        })();
        typingDiv.innerHTML = `
            <div class="message-avatar">${assistantAvatarInnerHtml()}</div>
            <div class="message-content-wrapper">
                <div class="message-header-row">
                    <span class="message-sender-group">
                        <span class="message-sender">${assistantDisplayName()}</span>
                        ${pendingChip}
                    </span>
                </div>
                <div class="message-content">
                    <div class="typing-indicator" aria-hidden="true">
                        <div class="typing-orbit">
                            <span class="typing-orbit-ring"></span>
                            <span class="typing-orbit-ring typing-orbit-ring--inner"></span>
                            <span class="typing-orbit-core"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--1"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--2"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--3"></span>
                            <span class="typing-orbit-beam"></span>
                        </div>
                        <div class="typing-wave">
                            <i></i><i></i><i></i><i></i><i></i><i></i><i></i>
                        </div>
                    </div>
                    <div class="typing-status" aria-live="polite" role="status">${escapeHtml(statusLabel)}</div>
                </div>
                ${getLiveQueryLogFooterHtml()}
            </div>
        `;
        messagesContainer.appendChild(typingDiv);
        scheduleSlashChipCompactLabels(typingDiv);
        transcriptNav.autoScrollChatToBottom();
        try { subagentLaunchers.paintLive(); } catch (_) {}
    }

    function ensureLocalTypingIndicator(statusText) {
        if (!document.getElementById('typing-indicator')) {
            addTypingIndicator(currentTypingSlashMeta());
        }
        if (statusText) updateTypingStatus(statusText);
        removeRemoteWaitingIndicator();
    }

    // Owned by chat_activity.js — thin adapter.
    function liveStatusLooksActive(liveStatus) {
        return CuttleChatActivity.liveStatusLooksActive(liveStatus);
    }

    function remoteLiveStatusLooksActive(liveStatus) {
        const last = lastVisibleChatMessageEl();
        const isReply = last && last.classList.contains('assistant');
        const stamp = isReply && last.querySelector('.message-timestamp[data-ts]');
        const reply = isReply ? {
            queryId: last.dataset.queryId,
            timestamp: stamp && stamp.dataset.ts,
        } : null;
        return liveStatusLooksActive(liveStatus)
            && !CuttleChatActivity.liveStatusPredatesReply(liveStatus, reply);
    }

    // Supervised activity cards: the rendering/coordination decisions live in
    // supervised_control.js (CuttleSupervised.createController); the page keeps
    // thin delegating call sites and supplies DOM/transport/session effects.
    let _supervisedController = null;
    function supervisedController() {
        if (!_supervisedController) {
            _supervisedController = CuttleSupervised.createController({
                win: window,
                formatMessage: formatMessage,
                stripLegacyAgentChrome: stripLegacyAgentChrome,
                activateEnhancements: activateEnhancements,
                currentSession: () => session.current(),
                sessionIdsEqual: sessionIdsEqual,
                toAuthDbSessionId: toAuthDbSessionId,
                fetch: (url, opts) => fetch(url, opts),
                toast: (msg, kind) => (window.showToast || function () {})(msg, kind),
                copyTextToClipboard: typeof copyTextToClipboard === 'function'
                    ? copyTextToClipboard
                    : null,
                addUserMessage: (text, opts) => addMessageToUI(text, 'user', opts),
                autoScroll: () => transcriptNav.autoScrollChatToBottom(true),
                processMessage: processMessage,
            });
        }
        return _supervisedController;
    }

    function showSupervisedJumpNotification(task) {
        return supervisedController().showJumpNotification(task);
    }

    function upsertSupervisedActivityCard(task) {
        return supervisedController().upsertActivityCard(task);
    }

    function wireSupervisedActivityCard(card) {
        return supervisedController().wireActivityCard(card);
    }

    function dispatchSupervisedTaskControl(taskId, action, params) {
        return supervisedController().dispatchTaskControl(taskId, action, params);
    }

    async function dispatchSupervisedControlMessage(message, opts) {
        return supervisedController().dispatchControlMessage(message, opts);
    }

    function observeSupervisedCanonicalBubble(bubble, task) {
        return supervisedController().observeCanonicalBubble(bubble, task);
    }

    function updateSupervisedTaskIndicator(liveStatus, sessionId) {
        return supervisedController().updateTaskIndicator(liveStatus, sessionId);
    }

    function updateRemoteWaitingFromMessages(messages, liveStatus) {
        rememberTurnBadge(messages, liveStatus);
        refreshTypingIndicatorHeaderBadges();
        noteWidgetsRevision(liveStatus);
        updateSupervisedTaskIndicator(liveStatus, session.current());
        // Local stream owns this chat's turn — keep/restore the typing bubble
        // (it is wiped when switching chats or after a hard refresh race).
        if (
            generation.loading
            && generation.localSessionId != null
            && session.current() != null
            && sessionIdsEqual(generation.localSessionId, session.current())
        ) {
            const statusLabel = (liveStatus && liveStatus.status) || undefined;
            ensureLocalTypingIndicator(statusLabel);
            setHistorySessionRunning(session.current(), true);
            try { subagentLaunchers.syncLive(liveStatus && liveStatus.subagents); } catch (_) {}
            return;
        }
        // Generating in another chat in this tab — don't steal that stream's UI.
        if (generation.loading) {
            removeRemoteWaitingIndicator();
            const liveActiveOther = remoteLiveStatusLooksActive(liveStatus);
            if (session.current() && liveActiveOther) {
                setHistorySessionRunning(session.current(), true);
                const statusLabel = (liveStatus && liveStatus.status) || 'Waiting for reply…';
                addRemoteWaitingIndicator(statusLabel);
                updateRemoteWaitingStatus(statusLabel, liveStatus && liveStatus.report_url);
                try { subagentLaunchers.syncLive(liveStatus && liveStatus.subagents); } catch (_) {}
            }
            return;
        }
        // User pressed Stop — don't resurrect a "generating" indicator for the
        // cancelled turn (last message is still the user bubble).
        // After refresh, in-memory stopState.userStopped is gone; honor the
        // persisted Stop notice and the live-status cancelled flag.
        if (
            stopState.waitingSuppressed
            || stopState.userStopped
            || (liveStatus && liveStatus.cancelled)
            || transcriptEndsWithGenerationStop(messages)
        ) {
            removeRemoteWaitingIndicator();
            if (session.current()) setHistorySessionRunning(session.current(), false);
            return;
        }
        // live-status is fetched in parallel with messages; a reply can already be
        // in history while that snapshot still says active. Prefer the transcript.
        const liveActiveRaw = remoteLiveStatusLooksActive(liveStatus);
        const liveActive = liveActiveRaw
            && !thisTurnHasAssistantReply(messages)
            && !turnAlreadyShowsAssistantReply();
        const statusLabel = (liveStatus && liveStatus.status) || 'Waiting for reply…';
        const wasGeneratingUi = !!document.getElementById('typing-indicator-remote')
            || (session.current() != null
                && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, session.current())));
        // Only show remote waiting when the server says a run is still active —
        // not merely because the last stored message is from the user (e.g. after Stop).
        if (session.current()) {
            setHistorySessionRunning(session.current(), liveActive);
        }
        if (liveActive) {
            addRemoteWaitingIndicator(statusLabel);
            updateRemoteWaitingStatus(statusLabel, liveStatus && liveStatus.report_url);
            try { subagentLaunchers.syncLive(liveStatus && liveStatus.subagents); } catch (_) {}
            // Offer Stop for remote/other-tab runs too.
            const stopButton = document.getElementById('stopButton');
            if (stopButton) {
                stopButton.style.display = 'flex';
                stopButton.disabled = false;
            }
        } else {
            removeRemoteWaitingIndicator();
            // Remote completion owns its composer cleanup too. Preserve Stop
            // for running watch cards; an idle chat must not offer agent Stop.
            syncComposerStopWithWatch();
            // Idle again (remote run finished, or stale live-status after reply) —
            // drain follow-ups queued while isSessionGenerating() was true.
            // Drain on any generating UI clear, not only when the remote indicator
            // was present — history "generating" flags alone used to queue forever.
            if (wasGeneratingUi && !generation.loading && !stopState.waitingSuppressed) {
                scheduleFollowupDrain(80);
            }
        }
    }

    // Recovery (detached-turn completion, parked-result retrieval, history
    // recovery, post-restart recovery) is owned by chat_pending_result.js as
    // one controller per chat root. The page supplies every effect — transport
    // URLs, paint, clock, turn/stop guards, the history-running chirp — as an
    // explicit `host`; the sequencing and the guard/registry state live in the
    // owner. Built lazily so page constants declared later are initialized.
    let _recoveryController = null;
    function recoveryController() {
        if (!_recoveryController) {
            _recoveryController = CuttleChatPendingResult.createRecoveryController({
                sessionIdsEqual: sessionIdsEqual,
                canonicalizeSessionId: (id) => canonicalizeChatSessionId(id),
                toAuthDbSessionId: (id) => toAuthDbSessionId(id),
                currentSession: () => session.current(),
                isStaleNav: (navGen) => CuttleTurnGuard.isStale(navGen, turnGeneration),
                isStopped: () => stopState.userStopped || stopState.abortSuppressed,
                hasReplyForTurn: () => !!(inFlightUserMessage && turnAlreadyShowsAssistantReply()),
                isViewing: (sid) => isViewingSession(sid),
                isAuthMode: () => isAuthMode(),
                inFlightUserText: () => inFlightUserMessage,
                norm: (text) => normalizeMessageContentForMatch(text),
                alreadyOnScreen: (role, content) => uiAlreadyHasMessage(role, content),
                isCancelledText: (text) => isCancelledAgentText(text),
                normalizeUsage: (usage) => normalizeUsagePayload(usage),
                liveLooksActive: (live) => liveStatusLooksActive(live),
                fetchPending: async (sid) => {
                    const resp = await fetchWithTimeout(
                        `/api/chat-pending-result?session_id=${encodeURIComponent(sid)}`,
                        { credentials: 'include', cache: 'no-store' },
                        8000
                    );
                    return resp.json();
                },
                fetchLive: (sid) => fetchChatLiveStatus(sid),
                // Prefer hub cache for live-status when in the app shell (no extra GET).
                fetchLiveForRecovery: (sid) => (inAppShell && shellHubSeen)
                    ? (getCachedHubLiveStatus(sid) || fetchChatLiveStatus(sid))
                    : fetchChatLiveStatus(sid),
                consumeFetch: (sid) => {
                    try {
                        fetchWithTimeout(
                            `/api/chat-pending-result?session_id=${encodeURIComponent(sid)}&consume=1`,
                            { credentials: 'include', cache: 'no-store' },
                            5000
                        ).catch(() => {});
                    } catch (_) {}
                },
                fetchMessages: async (authSid, limit) => {
                    const resp = await fetchWithTimeout(
                        sessionMessagesUrl(authSid, { limit }),
                        { credentials: 'include', cache: 'no-store' },
                        8000
                    );
                    if (!resp.ok) return { ok: false };
                    return { ok: true, data: await resp.json() };
                },
                pageSize: CHAT_HISTORY_PAGE_SIZE,
                fetchRestartStatus: async () => {
                    const resp = await fetchWithTimeout(
                        '/api/flask/restart/status?consume=1',
                        { credentials: 'include', cache: 'no-store' },
                        6000
                    );
                    return resp.json();
                },
                fetchHealthOk: async () => {
                    const h = await fetchWithTimeout('/api/status', {
                        credentials: 'include',
                        cache: 'no-store',
                    }, 4000);
                    return !!h.ok;
                },
                paintStatus: (text) => updateTypingStatus(text),
                paintQueryLink: (url) => updateTypingIndicatorQueryLink(url),
                paintSyncing: () => updateTypingStatus('Working… (syncing reply)'),
                clearTyping: () => removeTypingIndicator(),
                setHistoryRunning: (sid, on) => setHistorySessionRunning(sid, on),
                notifyReady: (sid, opts) => notifyAssistantResponseReady(sid, opts),
                syncMessages: () => syncSessionMessagesFromServer(),
                toast: (msg, kind) => (window.showToast || function () {})(msg, kind),
                tryDetachedWatchViaBroker: () => {
                    if (inAppShell && window.parent.cuttleActivityBroker) {
                        window.parent.cuttleActivityBroker.refresh();
                        return true;
                    }
                    return false;
                },
                classifyDetachedPoll: (step) => CuttleChatGeneration.classifyDetachedPoll(step),
                netDebug: (name, info) => {
                    try {
                        window.CuttleNetDebug && window.CuttleNetDebug.event(name, info);
                    } catch (_) {}
                },
                sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
                now: () => Date.now(),
            });
        }
        return _recoveryController;
    }

    /**
     * True only for an explicit dismiss/cancel option. Q&A choices omit
     * `action` on purpose and must NOT be treated as cancel (that used to
     * collapse every preference click to "Cancelled.").
     */
    // Owned by chat_action_forms.js — thin adapter.
    let _actionCardsController = null;
    /**
     * Single CuttleChatActionCards instance for this chat root. The page
     * keeps every send lane, the history panel, the composer, and all
     * message transport; the controller receives them as explicit host
     * capabilities and never reads page scope directly. Identity matching
     * reuses CuttleChatActivity (sessionIdsEqual) and the pure watch
     * predicates in CuttleChatActionForms, same as before.
     */
    function actionCardsController() {
        if (!_actionCardsController) {
            // Explicit chat root: the controller validates it and throws
            // on anything else — never a whole-document fallback.
            _actionCardsController = CuttleChatActionCards.mountCards(
                document.getElementById('chatMessages'),
                {
                    request: (url, reqOpts) => {
                        const o = reqOpts || {};
                        const init = Object.assign({}, o.options || {});
                        if (o.signal) init.signal = o.signal;
                        // No invented timeouts: plain fetch unless the
                        // controller passes an explicit timeout through.
                        if (o.timeout != null) {
                            return fetchWithTimeout(url, init, o.timeout);
                        }
                        return fetch(url, init);
                    },
                    // Q&A resume lane: exactly sendMessage({ text }).
                    sendAnswerText: (msg) => {
                        try {
                            const text = String((msg && msg.text != null) ? msg.text : msg);
                            const send = (typeof sendMessage === 'function') ? sendMessage : window.sendMessage;
                            if (typeof send === 'function') send({ text });
                        } catch (_) {}
                    },
                    // Watch-resume lane: exactly processMessage(text).
                    resumeWithStatus: (text) => processMessage(String(text)),
                    // Native restart lane: exactly
                    // processMessage(text, { controlLane: true }).
                    sendControlCommand: (text) => processMessage(String(text), { controlLane: true }),
                    context: () => ({
                        sessionId: session.current(),
                        projectPath: (currentProject && currentProject.path)
                            ? String(currentProject.path)
                            : undefined,
                        authSessionId: authSessionIdForRequest(),
                        isAuthMode: isAuthMode(),
                    }),
                    // Deferred storage: sessionStorage is only touched
                    // inside these calls (each call site stays guarded),
                    // never read at controller construction.
                    storage: {
                        getItem: (k) => sessionStorage.getItem(k),
                        setItem: (k, v) => sessionStorage.setItem(k, v),
                        removeItem: (k) => sessionStorage.removeItem(k),
                    },
                    notify: (text, kind) => {
                        try { (window.showToast || function () {})(text, kind); } catch (_) {}
                    },
                    notifyWorkCompletion: (event) => {
                        if (window.CuttleCompletionNotifications) {
                            window.CuttleCompletionNotifications.broker().complete(event).catch(() => {});
                        }
                    },
                    showPushFailure: data => window.CuttleGitPushReport ? window.CuttleGitPushReport.fromAction(data, {projectPath: currentProject && currentProject.path}) : '',
                    syncMessages: () => syncSessionMessagesFromServer(),
                    publishRestartEvent: (payload) => {
                        try {
                            window.parent.postMessage({
                                type: 'cuttle-flask-restart-linked',
                                ...payload,
                            }, '*');
                        } catch (_) {}
                    },
                    setHistoryAwaiting: (sessionId, on) => {
                        // pending=true sets this session; pending=false
                        // recomputes from all cards (another may be active).
                        if (on) setHistorySessionFormAwaiting(sessionId, true);
                        else syncHistoryFormAwaitingFromDom();
                    },
                    syncHistoryAwaiting: () => syncHistoryFormAwaitingFromDom(),
                    syncComposerStop: () => syncComposerStopWithWatch(),
                    paintAssistantMessage: (content) => addMessageToUI(content, 'assistant'),
                    escapeHtml: (value) => escapeHtmlInline(value),
                }
            );
        }
        return _actionCardsController;
    }

    function isExplicitActionFormCancelOption(opt) {
        return CuttleChatActionForms.isExplicitActionFormCancelOption(opt);
    }

    /** False for Q&A cards: answers go back to the agent, nothing runs or posts. */
    // Owned by chat_action_forms.js — thin adapter.
    function actionFormHasSideEffect(spec) {
        return CuttleChatActionForms.actionFormHasSideEffect(spec);
    }

    /**
     * Action cards own their own progress line: a daemon restart requested from
     * a card reports here (spinner → green check) instead of dropping ack and
     * completion bubbles into the transcript.
     */
    // Owned by chat_action_forms.js — thin adapter.
    function isWatchFormAction(action) {
        return CuttleChatActionForms.isWatchFormAction(action);
    }


    // Owned by chat_action_forms.js — thin adapter.
    function specLooksLikeFlaskRestart(spec, formId) {
        return CuttleChatActionForms.specLooksLikeFlaskRestart(spec, formId);
    }

    /**
     * User sent a follow-up instead of clicking open cards — lock + collapse
     * one-shot action forms (and disable open confirms) so they stop competing.
     */
    /**
     * User sent a follow-up instead of clicking open cards — lock + collapse
     * one-shot action forms (and disable open confirms) so they stop competing.
     * Card effects live in CuttleChatActionCards; the page keeps the
     * confirm-button half and the send-trigger call sites.
     */
    function dismissOpenInteractiveCards(reason) {
        const dismissed = actionCardsController().dismissOpenActionForms(reason);
        const summary = dismissed.summary;
        const root = document.getElementById('chatMessages');
        if (!root) return;
        root.querySelectorAll('.cuttle-confirm:not(.cuttle-buttons-locked)').forEach((card) => {
            lockCuttleButtonsInContainer(card, null, summary);
            card.classList.add('cuttle-buttons-locked', 'is-ignored');
            if (!card.querySelector('.cuttle-confirm-hint')) {
                const hint = document.createElement('div');
                hint.className = 'cuttle-confirm-hint';
                hint.textContent = summary;
                card.appendChild(hint);
            }
            card.querySelectorAll('button').forEach((btn) => { btn.disabled = true; });
        });
        actionCardsController().sendDismissNotice(dismissed.formIds, summary);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function inferActionFormWatch(spec) {
        return CuttleChatActionForms.inferActionFormWatch(spec);
    }


    // Owned by chat_action_forms.js — thin adapter.
    function safeActionFormWatchUrl(url) {
        return CuttleChatActionForms.safeActionFormWatchUrl(url);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function watchRunKey(data) {
        return CuttleChatActionForms.watchRunKey(data);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function cardWatchBind(watch) {
        return CuttleChatActionForms.cardWatchBind(watch);
    }


    // Owned by chat_action_forms.js — thin adapter.
    function watchSnapshotFromSpec(watch) {
        return CuttleChatActionForms.watchSnapshotFromSpec(watch);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function watchIsTerminalState(watch, data, doneStates, failStates) {
        return CuttleChatActionForms.watchIsTerminalState(watch, data, doneStates, failStates);
    }


    // Owned by chat_action_forms.js — thin adapter.
    function formatWatchElapsedSeconds(sec) {
        return CuttleChatActionForms.formatWatchElapsedSeconds(sec);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function watchElapsedText(data) {
        return CuttleChatActionForms.watchElapsedText(data);
    }

    // Owned by chat_action_forms.js — thin adapter.
    function normalizeWatchBars(data) {
        return CuttleChatActionForms.normalizeWatchBars(data);
    }

    // Bar-stack HTML owned by chat_action_forms.js — thin adapter for the
    // live watch-update path (initial card markup comes from the owner).
    function renderWatchBarsHtml(bars) {
        return CuttleChatActionForms.renderWatchBarsHtml(bars, escapeHtmlInline);
    }


    // Owned by chat_action_forms.js — thin adapter.
    function restartProgressLabel(status, liveWork) {
        return CuttleChatActionForms.restartProgressLabel(status, liveWork);
    }

    /** Follow one restart to completion, surviving the Flask replacement itself. */
    /**
     * Flask restart cards share form id `flask-restart-g{generation}` across chats
     * until the daemon actually replaces Flask. One click should settle every
     * sibling card; after restart the generation bumps and new cards unlink.
     */
    // Owned by chat_action_forms.js — thin adapter.
    function flaskRestartFormEpoch(formId) {
        return CuttleChatActionForms.flaskRestartFormEpoch(formId);
    }


    // Owned by chat_action_forms.js — DOM gather, domain decides.


    /**
     * The request itself can die with Flask (idle graceful restarts hand off in
     * well under a second), so the card adopts whatever restart is in flight.
     */

    function fetchWithTimeout(url, opts = {}, timeoutMs = 5000) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => {
            try { ctrl.abort(); } catch (_) {}
        }, timeoutMs);
        const parent = opts.signal;
        const onParentAbort = () => {
            try { ctrl.abort(); } catch (_) {}
        };
        if (parent) {
            if (parent.aborted) {
                clearTimeout(timer);
                ctrl.abort();
            } else {
                parent.addEventListener('abort', onParentAbort, { once: true });
            }
        }
        // A long-lived parent signal must not collect one listener per request.
        return fetch(url, { ...opts, signal: ctrl.signal }).finally(() => {
            clearTimeout(timer);
            if (parent) parent.removeEventListener('abort', onParentAbort);
        });
    }

    /** Coalesce parallel live-status GETs for the same session (split panes / polls). */
    const _liveStatusInflight = new Map();

    async function fetchChatLiveStatus(sessionId) {
        if (sessionId == null) return null;
        const cached = getCachedHubLiveStatus(sessionId);
        if (cached) return cached;
        // In the app shell, prefer the parent batch hub — skip per-pane GETs once
        // the hub has spoken (avoids starving sibling iframes). Focused panes may
        // fall through to a direct GET on cache miss so recovery still works.
        if (inAppShell && shellHubSeen) {
            const hub = getCachedHubLiveStatus(sessionId);
            if (hub) return hub;
            if (!shellPaneFocused) return null;
        }
        const key = String(sessionId);
        const existing = _liveStatusInflight.get(key);
        if (existing) return existing;
        const pending = (async () => {
            try {
                const response = await fetchWithTimeout(
                    `/api/chat-live-status?session_id=${encodeURIComponent(sessionId)}`,
                    { credentials: 'include', cache: 'no-store' },
                    4000
                );
                const data = await response.json();
                if (data && data.success) return data;
            } catch (_) {}
            return null;
        })().finally(() => {
            if (_liveStatusInflight.get(key) === pending) _liveStatusInflight.delete(key);
        });
        _liveStatusInflight.set(key, pending);
        return pending;
    }

    function finishLocalStreamFromServerSync() {
        if (!generation.loading) return;
        CuttleStopState.markStreamDetached(stopState);
        try {
            if (activeRequestController) activeRequestController.abort();
        } catch (_) {}
        activeRequestController = null;
        try {
            if (activeEventSource) {
                activeEventSource.close();
                activeEventSource = null;
            }
        } catch (_) {}
        removeTypingIndicator();
        removeRemoteWaitingIndicator();
        // Current turn owns this finish: a newer turn's lock must survive.
        endLocalGeneration(CuttleChatGeneration.currentToken(generation));
        const chatInput = document.getElementById('chatInput');
        const sendButton = document.getElementById('sendButton');
        const stopButton = document.getElementById('stopButton');
        if (chatInput) chatInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (stopButton) {
            stopButton.disabled = true;
            stopButton.style.display = 'none';
        }
        setWelcomeComposerEnabled(true);
        if (followupController().getState().items.length && !stopState.waitingSuppressed) {
            scheduleFollowupDrain(80);
        }
    }

    function uiAlreadyHasMessage(role, content) {
        const want = normalizeMessageContentForMatch(content);
        const nodes = document.querySelectorAll(`#chatMessages ${CHAT_RECORD_SELECTOR}.${role}`);
        for (const el of nodes) {
            if (el.id === 'typing-indicator' || el.id === 'typing-indicator-remote') continue;
            const raw = normalizeMessageContentForMatch(
                el.dataset.rawContent != null ? el.dataset.rawContent : ''
            );
            if (raw === want) return true;
        }
        return false;
    }

    async function syncSessionMessagesFromServer() {
        if (!isAuthMode() || session.current() == null) return;
        // Background / minimized: skip churn; visibilitychange triggers a sync on return.
        if (pageIsBackgrounded()) return;
        const chatArea = document.getElementById('chatArea');
        if (!chatArea || chatArea.style.display === 'none') return;
        // Local isLoading: still do a messages-only recovery sync so a finished
        // reply can paint without requiring an Electron refresh. Do not skip
        // when only runningSessionIds / remote-wait is stale — that trapped
        // follow-ups on the phone while the PC could send.
        const recoveringLocal = !!isLoadingThisSession();
        // Claim lifetime is owner-held (CuttleChatGeneration): claim only
        // when this sync will actually dispatch, so skipped ticks never
        // advance the request token. A fresh claim coalesces; a stale one
        // is replaced here (a hung fetch used to block recovery sync
        // until refresh).
        const syncClaim = CuttleChatGeneration.claimSync(messageSync, Date.now());
        if (!syncClaim) return;

        const syncSeq = syncClaim.token;
        const sessionAtStart = String(session.current());
        // Capture the existing navigation sequence for A->B->A fencing
        // (no second generation).
        const navAtStart = session.navSeq();
        // True only while this request is still the current one: it owns
        // the claim, no navigation happened since, and the session still
        // matches. Required before ANY body-derived effect, including the
        // malformed active-status branch below.
        const syncStillCurrent = () =>
            CuttleChatGeneration.isSyncCurrent(messageSync, syncSeq)
            && session.navSeq() === navAtStart
            && String(session.current()) === sessionAtStart;
        try {
            const authSid = toAuthDbSessionId(session.current());
            let msgResp;
            let liveStatus = null;
            const useHubStatus = inAppShell && shellHubSeen;
            if (recoveringLocal || useHubStatus) {
                msgResp = await fetchWithTimeout(
                    sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }),
                    { credentials: 'include', cache: 'no-store' },
                    8000
                );
                if (useHubStatus) liveStatus = getCachedHubLiveStatus(session.current());
            } else {
                const pair = await Promise.all([
                    fetchWithTimeout(sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }), {
                        credentials: 'include',
                        cache: 'no-store',
                    }, 8000),
                    fetchChatLiveStatus(session.current()),
                ]);
                msgResp = pair[0];
                liveStatus = pair[1];
            }
            if (!msgResp.ok) {
                throw new Error(`HTTP ${msgResp.status}`);
            }
            const data = await msgResp.json();
            if (!data.success || !Array.isArray(data.messages)) {
                if (syncStillCurrent() && liveStatus && liveStatus.active && !generation.loading) {
                    updateRemoteWaitingFromMessages([], liveStatus);
                }
                return;
            }
            if (!syncStillCurrent()) return;
            applyServerFollowups(data.followups, data.followup_revision);
            acceptSessionAttention(session.current(), data.attention);
            // Keep badges truthful on incremental syncs too (same seeding as open).
            hydrateAgentSelectionFromSessionData(data, session.current());
            applySessionIdentity(data);
            if (data.session_name) {
                rememberChatSessionTitle(session.current(), data.session_name);
                updateChatSessionTitle(session.current());
            }

            let appendedAssistant = false;
            let addedAny = false;
            /** True only when we created a new assistant bubble (not when we
             *  stamped an id onto the optimistic stream reply). Claiming must
             *  not re-fire the completion chirp/toast — that caused a second
             *  "chip" ~sync-interval after the first. */
            let appendedNewAssistant = false;

            // Exactly-once control_request_id lookup (DOM read; the page
            // stamps the id in the claim-control effect below).
            const controlClaimEl = (crid, role) => {
                const byControl = document.querySelector(
                    `#chatMessages .message[data-control-request-id="${CSS.escape(String(crid))}"][class*="${role}"]`
                );
                if (byControl) return { el: byControl, exact: true };
                // Also match role more reliably
                const allCr = document.querySelectorAll(
                    `#chatMessages .message[data-control-request-id="${CSS.escape(String(crid))}"]`
                );
                for (const node of allCr) {
                    if (node.classList.contains(role)) return { el: node, exact: false };
                }
                return null;
            };
            for (const msg of data.messages) {
                if (msg == null || msg.id == null) continue;
                const mid = Number(msg.id);
                if (mid > lastSeenServerMessageId) lastSeenServerMessageId = mid;

                // Owned by chat_pending_result.js — exactly-once replay
                // classification; the page applies the existing effect per
                // decision and accumulates the flags exactly as before.
                // Peek queries re-run in the effect on the same tick, so the
                // claim/settle outcome is identical to the decided one.
                const syncDecision = CuttleChatPendingResult.classifyServerMessage(
                    msg,
                    { stopped: stopState.userStopped },
                    {
                        hasDomId: (id) => !!document.querySelector(
                            `#chatMessages .message[data-message-id="${Number(id)}"]`
                        ),
                        findControlClaim: (crid, role) => {
                            const hit = controlClaimEl(crid, role);
                            return hit ? (hit.exact ? 'exact' : 'role') : null;
                        },
                        isCancelledText: (text) => isCancelledAgentText(text),
                        claimableUnmarked: (role, content) => !!claimUnmarkedMessageEl(role, content),
                        loadingThisSession: isLoadingThisSession(),
                        replyShownFor: (content) => turnAlreadyShowsAssistantReply(content),
                        hasUnmarkedAssistant: () => !!claimLastUnmarkedAssistantEl(),
                        alreadyOnScreen: (role, content) => uiAlreadyHasMessage(role, content),
                    }
                );
                switch (syncDecision.decision) {
                    case 'update-in-place': {
                        const existing = document.querySelector(
                            `#chatMessages .message[data-message-id="${mid}"]`
                        );
                        if (existing) {
                            updateServerMessageInPlace(existing, msg, { notifyTerminal: true });
                        }
                        continue;
                    }
                    case 'claim-control': {
                        const hit = controlClaimEl(syncDecision.crid, msg.role);
                        if (hit && !hit.el.dataset.messageId) {
                            hit.el.dataset.messageId = String(mid);
                        }
                        continue;
                    }
                    case 'skip-cancelled':
                        consumeCancelledAgentReply(msg.content, { announce: false });
                        continue;
                    case 'skip-stopped':
                    case 'skip-dup-user':
                    case 'skip-invalid':
                        continue;
                    case 'claim-unmarked': {
                        const claimed = claimUnmarkedMessageEl(msg.role, msg.content);
                        if (claimed) {
                            if (msg.role === 'assistant') {
                                stampAssistantElFromServer(claimed, msg);
                                appendedAssistant = true;
                            } else {
                                claimed.dataset.messageId = String(mid);
                                claimed.dataset.rawContent = String(msg.content || '');
                            }
                        }
                        continue;
                    }
                    case 'already-shown':
                        appendedAssistant = true;
                        continue;
                    case 'adopt-last': {
                        const adopted = claimLastUnmarkedAssistantEl();
                        if (adopted) {
                            stampAssistantElFromServer(adopted, msg);
                            appendedAssistant = true;
                        }
                        continue;
                    }
                    case 'stamp-duplicate': {
                        const stamped = claimUnmarkedMessageEl('assistant', msg.content)
                            || claimLastUnmarkedAssistantEl();
                        if (stamped) stampAssistantElFromServer(stamped, msg);
                        appendedAssistant = true;
                        continue;
                    }
                    case 'append-new':
                        appendServerMessageToUI(msg);
                        addedAny = true;
                        appendedAssistant = true;
                        appendedNewAssistant = true;
                        continue;
                    case 'append':
                    default:
                        appendServerMessageToUI(msg);
                        addedAny = true;
                        if (msg.role === 'assistant') {
                            appendedAssistant = true;
                            appendedNewAssistant = true;
                        }
                        continue;
                }
            }

            if (appendedAssistant && generation.loading && (appendedNewAssistant || turnAlreadyShowsAssistantReply())) {
                finishLocalStreamFromServerSync();
                scheduleHistoryTitleRefresh();
            } else if (generation.loading) {
                healStaleGeneratingState();
            }
            // Only chirp/toast for replies that arrived via sync (other device /
            // dropped stream). Local processMessage already notified.
            if (appendedNewAssistant) {
                const lastAsst = lastVisibleChatMessageEl();
                const errFromUi = !!(
                    lastAsst
                    && lastAsst.classList.contains('assistant')
                    && (
                        lastAsst.dataset.failed === '1'
                        || lastAsst.querySelector('.slash-command-chip--error')
                    )
                );
                notifyAssistantResponseReady(session.current(), {
                    fromSync: true,
                    isError: errFromUi,
                });
                refreshChatWidgets();
            }

            if (!recoveringLocal) {
                updateRemoteWaitingFromMessages(data.messages, liveStatus);
                reconcileCuttleButtonLocks(data.messages);
            }

            if (addedAny) transcriptNav.autoScrollChatToBottom();
        } catch (e) {
            console.warn('[Cuttle Chat] message sync failed:', e);
        } finally {
            // Token-scoped cleanup via the owner: a stale finisher whose
            // claim was replaced (timeout) must not clear the newer
            // owner's in-flight state. finishSync is a no-op on token
            // mismatch, so this stays safe to call unconditionally.
            CuttleChatGeneration.finishSync(messageSync, syncSeq);
        }
    }

    // Owned by chat_session.js (CuttleChatSession.createLifecycle) — thin adapter.
    function saveChatSession(message, role, opts = {}) {
        return sessionLifecycle().saveChatSession(message, role, opts);
    }

    // Owned by chat_history_view.js (CuttleChatHistoryView.historyActionIcons) —
    // thin alias so the shared chrome menu builder keeps a page-local name.
    function _historyActionIcons() {
        return CuttleChatHistoryView.historyActionIcons();
    }

    /** True until /api/terminal/status says otherwise — shared by both overflow menus. */
    let _terminalMenuAvailable = true;

    /**
     * Shared overflow items for chrome + history session menus.
     * Order: Copy → Star → Mark unread → Rename → Sub-agents → Git push → Terminal → Voice → Archive → Delete.
     */
    function buildSessionMenuHTML(sessionId, opts) {
        opts = opts || {};
        const icons = _historyActionIcons();
        const hasSession = sessionId != null && sessionId !== '';
        const starred = hasSession && !!isChatSessionStarred(sessionId);
        const displayId = hasSession ? (formatChatDisplayId(sessionId) || '') : '';
        const includeRename = hasSession && opts.includeRename !== false;
        const includeDelete = hasSession && opts.includeDelete !== false;
        const sid = hasSession ? String(sessionId).replace(/\\/g, '\\\\').replace(/'/g, "\\'") : '';
        const copyLabel = displayId ? ('Copy ' + displayId) : 'Copy chat ID';
        const starLabel = starred ? 'Unstar chat' : 'Star chat';
        const parts = [];

        if (hasSession && displayId) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' data-menu-action="copy-id" title="Copy chat ID" aria-label="'
                + escapeHtmlInline(copyLabel) + '"'
                + ' onclick="window.copyChatSessionId(\'' + sid + '\', event)">'
                + icons.copy
                + '<span class="chat-chrome-menu-item-label">' + escapeHtmlInline(copyLabel) + '</span>'
                + '<span class="chat-id-badge-label" hidden>' + escapeHtmlInline(displayId) + '</span>'
                + '</button>'
            );
        }
        if (hasSession) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="Export transcript (Markdown)" aria-label="Export transcript (Markdown)"'
                + ' onclick="window.chatPageExportSession(\'' + sid + '\', event)">'
                + icons.download
                + '<span class="chat-chrome-menu-item-label">Export</span>'
                + '</button>'
            );
        }
        if (hasSession) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item'
                + (starred ? ' is-starred' : '') + '" role="menuitem"'
                + ' title="' + starLabel + '" aria-label="' + starLabel + '"'
                + ' onclick="window.chatPageToggleStarSession(\'' + sid + '\', event)">'
                + icons.starOutline + icons.starFilled
                + '<span class="chat-chrome-menu-item-label">' + starLabel + '</span>'
                + '</button>'
            );
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="Mark as unread" aria-label="Mark as unread"'
                + ' onclick="window.chatPageMarkUnreadSession(\'' + sid + '\', event)">'
                + icons.unread
                + '<span class="chat-chrome-menu-item-label">Mark as unread</span>'
                + '</button>'
            );
        }
        if (includeRename) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="Rename" aria-label="Rename"'
                + ' onclick="window.chatPageRenameSession(\'' + sid + '\', event)">'
                + icons.pencil
                + '<span class="chat-chrome-menu-item-label">Rename</span>'
                + '</button>'
            );
        }
        const subGroup = historyPanel.historySubagentGroupOwnedBy(sessionId);
        if (subGroup && subGroup.getAttribute('data-force-expanded') !== '1') {
            const collapsed = subGroup.classList.contains('is-collapsed');
            const subLabel = collapsed ? 'Show sub-agents' : 'Hide sub-agents';
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="' + subLabel + '" aria-label="' + subLabel + '"'
                + ' onclick="window.toggleHistorySubagentGroup(\'' + sid + '\', event)">'
                + icons.subagents
                + '<span class="chat-chrome-menu-item-label">' + subLabel + '</span>'
                + '</button>'
            );
        }
        parts.push(gitPush.menuHtml(icons));
        if (_terminalMenuAvailable) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="Open terminal" aria-label="Open terminal"'
                + ' onclick="window.chatPageOpenTerminalFromMenu(event)">'
                + icons.terminal
                + '<span class="chat-chrome-menu-item-label">Terminal</span>'
                + '</button>'
            );
        }
        parts.push(
            '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
            + ' title="Voice mode" aria-label="Voice mode"'
            + ' onclick="window.chatPageEnterVoiceMode(event)">'
            + icons.voice
            + '<span class="chat-chrome-menu-item-label">Voice</span>'
            + '</button>'
        );
        // Archive lives on server sessions only (local-fallback and terminal
        // entries have no archived state to toggle).
        if (hasSession && isAuthMode() && !isTerminalSessionId(sessionId)) {
            const archived = !!((opts && opts.archived) || isChatSessionArchived(sessionId));
            const archiveLabel = archived ? 'Unarchive' : 'Archive';
            parts.push(
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="' + archiveLabel + '" aria-label="' + archiveLabel + '"'
                + ' onclick="window.chatPageToggleArchiveSession(\'' + sid + '\', event)">'
                + (archived ? icons.unarchive : icons.archive)
                + '<span class="chat-chrome-menu-item-label">' + archiveLabel + '</span>'
                + '</button>'
            );
        }
        if (includeDelete) {
            parts.push(
                '<button type="button" class="chat-chrome-menu-item is-danger" role="menuitem"'
                + ' title="Delete" aria-label="Delete"'
                + ' onclick="window.chatPageRequestDeleteSession(\'' + sid + '\', event)">'
                + icons.trash
                + '<span class="chat-chrome-menu-item-label">Delete</span>'
                + '</button>'
            );
        }
        return parts.join('');
    }

    function closeAllSessionMenus() {
        historyPanel.closeMenu();
        closeChatChromeMenu();
    }

    function exportHHMM(ts) {
        if (!ts) return '';
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) return '';
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return hh + ':' + mm;
    }

    function exportAgentDisplayName(agent) {
        const name = String(agent || '');
        return ({ codex: 'Codex', cursor: 'Cursor', claude: 'Claude Code', muse: 'Muse Code', deepseek: 'DeepSeek', opencode: 'OpenCode', hermes: 'Hermes' })[name] || name;
    }

    /** Author label for one exported row: subagent speaker → routing badge → slash chip → UI fallback. */
    function exportMessageAuthor(role, opts) {
        const speaker = opts && opts.speaker && String(opts.speaker).trim();
        if (speaker) return speaker;
        if (role === 'user') return 'You';
        const badge = opts && opts.routing_badge;
        if (badge && badge.agent) return exportAgentDisplayName(badge.agent);
        const sc = normalizeSlashCommandStored(opts && opts.slash_command);
        if (sc && sc.chips && sc.chips.length) {
            const meta = String((sc.chips[0] && sc.chips[0].meta) || '');
            const m = meta.match(/^\/([a-z][\w-]*)/);
            if (m) return exportAgentDisplayName(m[1]);
            const label = String((sc.chips[0] && sc.chips[0].label) || '').split(/\s*[—-]\s*/)[0].trim();
            if (label) return label;
        }
        return assistantDisplayName({});
    }

function buildTranscriptMarkdown(sessionId, messages, isAuth) {
        const title = String(getChatSessionDisplayTitle(sessionId).trim() || 'Chat transcript');
        const lines = [];
        const displayId = formatChatDisplayId(sessionId) || String(sessionId);
        const at = new Date();
        lines.push('# ' + title);
        lines.push('Chat: ' + displayId + ' · Exported ' + at.toISOString().slice(0, 16).replace('T', ' ') + ' UTC');
        lines.push('---');
        lines.push('');
        messages.filter((m) => m && m.role !== 'system').forEach((m) => {
            const role = String(m.role || 'user');
            const opts = authMessageOptsFromServer(m);
            const contentRaw = String(m.content ?? '');
            const body = role === 'assistant' ? stripLegacyAgentChrome(contentRaw) : contentRaw;
            if (!body) return;
            const ts = opts.timestamp > 0 ? Number(opts.timestamp) : 0;
            const author = exportMessageAuthor(role, opts);
            const stamp = exportHHMM(ts);
            lines.push('**' + (stamp ? stamp + ' — ' : '') + author + '**');
            lines.push('');
            body.trim().split(/\r\n|\r|\n/).forEach((line) => lines.push(line));
            lines.push('');
        });
        return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
    }

function downloadTranscriptMarkdown(text, sessionId) {
        const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const displayId = formatChatDisplayId(sessionId) || 'chat';
        const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        const filename = 'cuttle-chat-' + String(displayId).replace(/[^A-Za-z0-9_-]/g, '').toLowerCase() + '-' + stamp + '.md';
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.rel = 'noopener';
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        try { a.remove(); } catch (_) {}
        setTimeout(function () { try { URL.revokeObjectURL(url); } catch (_) {} }, 4000);
        return filename;
    }

    async function exportChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        if (sessionId == null || sessionId === '') return;
        closeAllSessionMenus();
        if (isAuthMode()) {
            const authSid = toAuthDbSessionId(sessionId);
            if (authSid == null || authSid === '') return;
            const tz = -new Date().getTimezoneOffset();
            const displayId = formatChatDisplayId(authSid) || String(authSid);
            const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
            const safeId = String(displayId).replace(/[^A-Za-z0-9_-]/g, '').toLowerCase();
            const filename = 'cuttle-chat-' + safeId + '-' + stamp + '.md';
            const a = document.createElement('a');
            a.href = '/api/auth/sessions/' + encodeURIComponent(authSid) + '/export?tz=' + tz;
            a.download = filename;
            a.rel = 'noopener';
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            try { a.remove(); } catch (_) {}
            (window.showToast || function () {})('Exported ' + filename, 'success');
            return;
        }
        let messages = null;
        try {
            const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
            const s = sessions[sessionId] || sessions[String(sessionId)];
            if (s && Array.isArray(s.messages)) messages = s.messages;
        } catch (_) {}
        if (!messages || !messages.some((m) => m && m.role !== 'system')) {
            (window.showToast || function () {})('Nothing to export yet', 'error');
            return;
        }
        const md = buildTranscriptMarkdown(sessionId, messages, false);
        try {
            const filename = downloadTranscriptMarkdown(md, sessionId);
            (window.showToast || function () {})('Exported ' + filename, 'success');
        } catch (e) {
            (window.showToast || function () {})('Export failed: ' + String((e && e.message) || e), 'error');
        }
    }

    async function toggleStarChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        if (sessionId == null || sessionId === '') return;
        closeAllSessionMenus();

        const next = !isChatSessionStarred(sessionId);

        if (isTerminalSessionId(sessionId)) {
            const map = readTerminalSessionRegistry();
            const id = String(sessionId);
            if (!map[id]) return;
            map[id].starred = next;
            writeTerminalSessionRegistry(map);
            historyPanel.reloadKeepingSearch();
            if (sessionIdsEqual(sessionId, session.current())) updateChatSessionStarButton(session.current());
            return;
        }

        if (isAuthMode()) {
            const authSid = toAuthDbSessionId(sessionId);
            if (authSid == null) return;
            try {
                const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ starred: next }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.success) {
                    console.warn('[Cuttle Chat] Star toggle failed:', data.error || response.status);
                    return;
                }
                const list = (window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [];
                const row = list.find((x) => sessionIdsEqual(x.id, sessionId) || sessionIdsEqual(x.id, authSid));
                if (row) row.starred = next ? 1 : 0;
                if (typeof window.CuttleAuth.loadChatSessions === 'function') {
                    await window.CuttleAuth.loadChatSessions();
                }
            } catch (err) {
                console.warn('[Cuttle Chat] Star toggle error:', err);
                return;
            }
            historyPanel.reloadKeepingSearch();
            if (sessionIdsEqual(sessionId, session.current())) updateChatSessionStarButton(session.current());
            return;
        }

        try {
            const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
            const key = sessions[sessionId] ? sessionId : String(sessionId);
            if (!sessions[key]) return;
            sessions[key].starred = next;
            localStorage.setItem('chatSessions', JSON.stringify(sessions));
        } catch (err) {
            console.warn('[Cuttle Chat] Local star toggle failed:', err);
            return;
        }
        historyPanel.reloadKeepingSearch();
        if (sessionIdsEqual(sessionId, session.current())) updateChatSessionStarButton(session.current());
    }

    // Sessions this tab deleted. A /api/auth/sessions fetch that was already in
    // flight when the DELETE landed still contains the row, so without this the
    // chat blinks back into the sidebar.
    const deletedChatSessionIds = new Set();

    function markChatSessionDeleted(sessionId) {
        if (sessionId == null || sessionId === '') return;
        deletedChatSessionIds.add(String(canonicalizeChatSessionId(sessionId)));
        const authSid = toAuthDbSessionId(sessionId);
        if (authSid != null) deletedChatSessionIds.add(String(authSid));
    }

    function isChatSessionDeleted(sessionId) {
        if (sessionId == null || sessionId === '') return false;
        if (deletedChatSessionIds.has(String(canonicalizeChatSessionId(sessionId)))) return true;
        const authSid = toAuthDbSessionId(sessionId);
        return authSid != null && deletedChatSessionIds.has(String(authSid));
    }

    // Sessions this tab knows are archived (populated from ?archived=only).
    // Drives the Archive/Unarchive menu label; the server list is the source
    // of truth after every archive toggle. Mark decisions live in
    // CuttleChatHistoryArchive over this tab-lifetime Set.
    const archivedChatSessionIds = new Set();

    function markChatSessionArchived(sessionId) {
        const mod = historyArchiveModule();
        if (!mod) return;
        mod.markArchivedIds(archivedChatSessionIds, sessionId, archiveIdExtras(sessionId));
    }

    function unmarkChatSessionArchived(sessionId) {
        const mod = historyArchiveModule();
        if (!mod) return;
        mod.unmarkArchivedIds(archivedChatSessionIds, sessionId, archiveIdExtras(sessionId));
    }

    function isChatSessionArchived(sessionId) {
        const mod = historyArchiveModule();
        if (!mod) return false;
        return mod.isArchivedId(archivedChatSessionIds, sessionId, archiveIdExtras(sessionId));
    }

    async function toggleArchiveChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        if (sessionId == null || sessionId === '') return;
        closeAllSessionMenus();
        if (!isAuthMode() || isTerminalSessionId(sessionId)) return;
        const authSid = toAuthDbSessionId(sessionId);
        if (authSid == null) return;
        const next = !isChatSessionArchived(sessionId);
        try {
            const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ archived: next }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                console.warn('[Cuttle Chat] Archive toggle failed:', data.error || response.status);
                return;
            }
            if (next) markChatSessionArchived(sessionId);
            else unmarkChatSessionArchived(sessionId);
        } catch (error) {
            console.error('Error toggling chat archive:', error);
            return;
        }
        // Archiving the open chat keeps it open — it only leaves the list.
        await refreshChatHistoryList();
        await refreshArchivedSessions();
    }

    async function deleteChatSession(sessionId) {
        if (isTerminalSessionId(sessionId)) {
            removeTerminalSessionFromStorage(sessionId);
            // If a terminal iframe is open in the shell, ask it to close the live tab.
            try {
                if (window.parent && window.parent !== window) {
                    window.parent.postMessage({
                        type: 'cuttle-terminal-delete',
                        sessionId: String(sessionId),
                    }, '*');
                }
            } catch (_) {}
            historyPanel.load();
            return;
        }

        // If this chat is actively generating, abort the local stream immediately.
        if (sessionIdsEqual(session.current(), sessionId) && generation.loading) {
            try {
                if (activeRequestController) activeRequestController.abort();
            } catch (_) {}
            try {
                if (activeEventSource) {
                    activeEventSource.close();
                    activeEventSource = null;
                }
            } catch (_) {}
            removeTypingIndicator();
            removeRemoteWaitingIndicator();
            // Force: deleting the chat owns its turn outright.
            endLocalGeneration();
            setHistorySessionRunning(sessionId, false);
        }

        if (isAuthMode()) {
            try {
                const authSid = toAuthDbSessionId(sessionId);
                const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}`, {
                    method: 'DELETE',
                    credentials: 'include',
                });
                const data = await response.json();
                if (!data.success) {
                    console.error('Failed to delete session:', data.error || response.status);
                    return;
                }
                if (data.cancelled_run) {
                    console.log('[Cuttle Chat] Cancelled in-flight run for deleted session', sessionId, data);
                }
                markChatSessionDeleted(sessionId);
            } catch (error) {
                console.error('Error deleting chat session:', error);
                return;
            }

            if (sessionIdsEqual(session.current(), sessionId)) {
                await createNewChat();
            }
            clearSessionPrefs(sessionId);
            await refreshChatHistoryList();
            // Deleting from the Archived section must update its count too.
            await refreshArchivedSessions();
            return;
        }

        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        delete sessions[sessionId];
        localStorage.setItem('chatSessions', JSON.stringify(sessions));

        const phMap = readPromptHistoryMap();
        delete phMap[sessionId];
        writePromptHistoryMap(phMap);
        clearSessionPrefs(sessionId);

        if (session.current() === sessionId) {
            createNewChat();
        }

        historyPanel.reloadKeepingSearch();
    }
    

    // Rename persistence effect (panel owns the modal; the page owns the API and
    // the terminal shell postMessage contract). `renameKind` comes from the panel.
    async function persistRenamedTitle(sessionId, newTitle, renameKind) {
        const title = String(newTitle || '').trim();
        if (!title) return false;

        if (isTerminalSessionId(sessionId) || renameKind === 'terminal') {
            const map = readTerminalSessionRegistry();
            const session = map[String(sessionId)];
            if (!session) return false;
            map[String(sessionId)] = {
                ...session,
                title,
                titleCustom: true,
                updated: Date.now(),
            };
            writeTerminalSessionRegistry(map);
            try {
                if (window.parent && window.parent !== window) {
                    window.parent.postMessage({
                        type: 'cuttle-terminal-rename',
                        sessionId: String(sessionId),
                        title,
                    }, '*');
                }
            } catch (_) {}
            return true;
        }

        if (isAuthMode() && toAuthDbSessionId(sessionId) != null) {
            const authSid = toAuthDbSessionId(sessionId);
            const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ session_name: title }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error((data && data.error) || ('HTTP ' + response.status));
            }
            rememberChatSessionTitle(sessionId, title);
            const list = (window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [];
            const row = list.find((x) => sessionIdsEqual(x.id, sessionId) || sessionIdsEqual(x.id, authSid));
            if (row) {
                row.session_name = title;
                row.name_auto = 0;
            }
            if (typeof window.CuttleAuth.loadChatSessions === 'function') {
                try { await window.CuttleAuth.loadChatSessions(); } catch (_) {}
            }
            return true;
        }

        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        const key = sessions[sessionId] ? sessionId : String(sessionId);
        if (!sessions[key]) return false;
        sessions[key].title = title;
        localStorage.setItem('chatSessions', JSON.stringify(sessions));
        rememberChatSessionTitle(sessionId, title);
        return true;
    }

    // Owned by chat_attention.js (CuttleChatAttention); the controller owns the
    // status-slot icon markup. Kept as a page adapter for existing call sites.
    function historyRunningIconHTML() {
        return serverAttention.historyRunningIconHTML();
    }

    function sessionShowsHistorySpinner(sessionId) {
        if (sessionId == null || sessionId === '') return false;
        return [...runningSessionIds].some((rid) => sessionIdsEqual(rid, sessionId))
            || [...formAwaitingSessionIds].some((rid) => sessionIdsEqual(rid, sessionId));
    }

    function _mutateSessionIdSet(set, sessionId, on) {
        if (sessionId == null || sessionId === '') return;
        const id = String(canonicalizeChatSessionId(sessionId));
        const bare = toAuthDbSessionId(sessionId);
        set.delete(id);
        set.delete(String(sessionId));
        if (bare) {
            set.delete(String(bare));
            set.delete(`db_session_${bare}`);
        }
        if (on) set.add(id);
    }

    function setHistorySessionRunning(sessionId, running) {
        _mutateSessionIdSet(runningSessionIds, sessionId, !!running);
        syncHistoryRunningIndicators();
    }

    function setHistorySessionFormAwaiting(sessionId, awaiting) {
        _mutateSessionIdSet(formAwaitingSessionIds, sessionId, !!awaiting);
        syncHistoryRunningIndicators();
    }

    function formAwaitingSessionIdFromCard(card) {
        return actionCardsController().awaitingSessionId(card);
    }

    /** Keep history spinners in sync with action forms that are actually executing.
     *
     * A form merely waiting for the user (unlocked question, a Flask-restart
     * card still "Checking restart status…") is not work: the blue input dot
     * owns it. Only a submitted/adopted restart or a running watch spins. */
    function syncHistoryFormAwaitingFromDom() {
        const next = new Set();
        actionCardsController().processingSessionIds().forEach((sid) => {
            if (sid == null || sid === '') return;
            next.add(String(canonicalizeChatSessionId(sid)));
        });
        // Drop sessions no longer pending (unless still generating).
        [...formAwaitingSessionIds].forEach((id) => {
            if (![...next].some((nid) => sessionIdsEqual(nid, id))) {
                formAwaitingSessionIds.delete(id);
            }
        });
        next.forEach((id) => formAwaitingSessionIds.add(id));
        if (session.current() != null && !serverAttention.get(toAuthDbSessionId(session.current()))) {
            const awaitingInput = actionCardsController().awaitingInputSessionIds()
                .some(sid => sessionIdsEqual(sid, session.current()));
            if (!!(attentionPrefsFor(session.current()) || {}).awaitingInput !== awaitingInput) {
                updateSessionPrefs(session.current(), { awaitingInput });
            }
        }
        syncHistoryUnreadIndicators();
        syncHistoryRunningIndicators();
    }

    // Owned by chat_generation.js — thin adapter: the transition runs in
    // the owner (idempotent re-begin keeps the token); the page applies
    // stop/flag/voice/sync effects. Returns the begin token so the turn's
    // own end calls can prove they still own the lock.
    function beginLocalGeneration() {
        const begun = CuttleChatGeneration.beginGeneration(generation, {
            currentSessionId: session.current(),
            requestSessionId: authSessionIdForRequest(),
        });
        CuttleStopState.clearAbortSuppression(stopState);
        if (begun.markRunning) {
            setHistorySessionRunning(begun.markRunning, true);
        }
        chatVoice.onGenerationStarted();
        // Do NOT poll live-status here. The chat SSE (or collectPendingResult after
        // we detach) already owns status updates. Extra GETs compete for Chromium's
        // ~6 HTTP/1.1 sockets per host and are the main Electron lockup cause.
        // Keep message sync alive on a recovery cadence so finished replies can
        // paint if the SSE/pending path stalls (no Electron refresh required).
        startMessageSync();
        if (messageSyncTimer) scheduleNextMessageSync();
        return begun.token;
    }

    // Owned by chat_generation.js — thin adapter. token scopes the release:
    // pass the turn's begin token; omit it only when the caller owns the
    // turn outright (Stop / chat-delete force paths).
    function endLocalGeneration(token) {
        const ended = CuttleChatGeneration.endGeneration(generation, token);
        if (!ended.released) return false;
        clearStreamStatusPoll();
        if (ended.sessionId) {
            setHistorySessionRunning(ended.sessionId, false);
        }
        if (messageSyncTimer) scheduleNextMessageSync();
        return true;
    }

    function syncHistoryRunningIndicators() {
        // The running spinner shares the far-left status slot with the unread /
        // input / queue dots; syncHistoryUnreadIndicators owns that slot and the
        // is-running class. Only the title icon is running-specific.
        syncHistoryUnreadIndicators();
        updateSessionTitleRunningIcon();
    }

    /** Space tabs (app shell) mirror these dots. Push only the chats this
     *  frame owns — the open chat, its local turn, and pending action forms.
     *  History rows for other chats can hold stale `is-running` classes
     *  (generating flags refresh only while the panel is open); the shell
     *  polls the server for those, and re-pushing them revived dead spinners.
     *  Idle owned chats are listed in `owned` so the shell clears them. */
    let _chatActivityBroadcastTimer = null;
    // Snapshot decisions owned by chat_activity.js — page gathers, domain decides.
    function collectChatActivitySnapshot() {
        return CuttleChatActivity.collectFrameSnapshot({
            currentSessionId: session.current(),
            localSessionId: generation.localSessionId,
            awaitingIds: [...formAwaitingSessionIds],
            canonicalize: canonicalizeChatSessionId,
            idsEqual: sessionIdsEqual,
            spinnerFor: sessionShowsHistorySpinner,
            attentionKindFor: (sid) => sessionHistoryAttentionKind(sid, null),
            prefsFor: attentionPrefsFor,
            attentionActive: chatAttentionActive(),
            attentionIsError: chatAttentionIsError,
            generationLoading: generation.loading,
        });
    }
    function scheduleChatActivityBroadcast() {
        if (!inAppShell) return;
        if (_chatActivityBroadcastTimer) return;
        _chatActivityBroadcastTimer = setTimeout(() => {
            _chatActivityBroadcastTimer = null;
            try {
                const snap = collectChatActivitySnapshot();
                window.parent.postMessage({
                    type: 'cuttle-chat-activity',
                    sessions: snap.sessions,
                    owned: snap.owned,
                }, '*');
            } catch (_) {}
        }, 150);
    }

    // Chat History Management
    function parseSessionTimestamp(value) {
        if (value == null || value === '') return 0;
        if (typeof value === 'number' && Number.isFinite(value)) {
            // Epoch seconds vs milliseconds
            return value > 0 && value < 1e12 ? value * 1000 : value;
        }
        let s = String(value).trim();
        if (/^\d+(\.\d+)?$/.test(s)) {
            const n = Number(s);
            if (Number.isFinite(n) && n > 0) {
                return n < 1e12 ? n * 1000 : n;
            }
        }
        // SQLite CURRENT_TIMESTAMP is "YYYY-MM-DD HH:MM:SS" in UTC. Without a
        // trailing Z, Date.parse treats it as local time and shifts the clock.
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
            s = s.replace(' ', 'T');
            if (!s.endsWith('Z')) s += 'Z';
        }
        const ms = Date.parse(s);
        return Number.isNaN(ms) ? 0 : ms;
    }

    /** Compact recency for history rows: <1m, 4h, 7d, 1y. */
    function formatHistoryLastMessageAge(ts, nowMs) {
        const now = nowMs == null ? Date.now() : nowMs;
        if (!ts || ts <= 0) return '';
        const diff = Math.max(0, now - ts);
        if (diff < 60000) return '<1m';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
        if (diff < 31536000000) return Math.floor(diff / 86400000) + 'd';
        return Math.floor(diff / 31536000000) + 'y';
    }

    function resolveHistoryLastMessageMs(source) {
        if (!source) return 0;
        const fromLast = parseSessionTimestamp(source.last_message_time);
        if (fromLast) return fromLast;
        if (Array.isArray(source.messages) && source.messages.length) {
            const last = source.messages[source.messages.length - 1];
            const fromMsg = parseSessionTimestamp(last && (last.timestamp || last.created));
            if (fromMsg) return fromMsg;
        }
        return parseSessionTimestamp(source.updated)
            || parseSessionTimestamp(source.last_activity)
            || parseSessionTimestamp(source.created)
            || 0;
    }

    function refreshHistoryItemTimes() {
        document.querySelectorAll('.chat-history-item .chat-time[data-ts]').forEach((el) => {
            const ts = parseInt(el.dataset.ts, 10);
            if (!ts) return;
            const label = formatHistoryLastMessageAge(ts);
            if (label && el.textContent !== label) el.textContent = label;
        });
    }

    function resolveMessageTimestamp(value) {
        if (value == null || value === '') return Date.now();
        const ms = parseSessionTimestamp(value);
        return ms > 0 ? ms : Date.now();
    }

    function applyGeneratingFlagsFromSessions(serverSessions) {
        const currentWasRunning = session.current() != null
            && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, session.current()));
        // Only executing work in the open chat may supplement server execution.
        // Entries left behind by another chat must yield to an idle snapshot.
        const localPending = new Set();
        actionCardsController().processingSessionIds().forEach(sid => {
            if (sessionIdsEqual(sid, session.current())) localPending.add(String(canonicalizeChatSessionId(sid)));
        });
        const still = new Set();
        (serverSessions || []).forEach((s) => {
            if (!s || s.id == null) return;
            if (!s.awaiting_action && !localPending.has(String(canonicalizeChatSessionId(s.id)))) {
                _mutateSessionIdSet(formAwaitingSessionIds, s.id, false);
            }
            if (s.attention) serverAttention.accept(toAuthDbSessionId(s.id), s.attention);
            // After a chat switch, the open chat stays idle until one-shot
            // live-status confirms. sessions.generating can lag and used to
            // flash the purple title/history spinner for a few seconds.
            // Once settled, sessions may still arm/clear other chats; for the
            // open chat prefer live-status but allow a sessions fallback when
            // live-status was null (unfocused hub miss) and we didn't just switch.
            if (
                session.current() != null
                && sessionIdsEqual(s.id, session.current())
            ) {
                if (s.awaiting_action) {
                    still.add(String(canonicalizeChatSessionId(s.id)));
                    setHistorySessionRunning(s.id, true);
                    _mutateSessionIdSet(formAwaitingSessionIds, s.id, true);
                    return;
                }
                if (deferOpenChatGeneratingFromSessions) {
                    if ([...runningSessionIds].some((rid) => sessionIdsEqual(rid, s.id))) {
                        still.add(String(canonicalizeChatSessionId(s.id)));
                    }
                    return;
                }
                // Don't re-arm from a stale sessions.generating flag when the
                // reply is already on screen (same race as live-status sync).
                if (
                    s.generating
                    && !generation.loading
                    && turnAlreadyShowsAssistantReply()
                ) {
                    return;
                }
                if (s.generating) {
                    still.add(String(canonicalizeChatSessionId(s.id)));
                    setHistorySessionRunning(s.id, true);
                } else if (
                    [...runningSessionIds].some((rid) => sessionIdsEqual(rid, s.id))
                ) {
                    // Keep live-status/local arm; message sync clears when idle.
                    still.add(String(canonicalizeChatSessionId(s.id)));
                }
                return;
            }
            if (s.generating || s.awaiting_action) {
                still.add(String(canonicalizeChatSessionId(s.id)));
                setHistorySessionRunning(s.id, true);
                if (s.awaiting_action) {
                    _mutateSessionIdSet(formAwaitingSessionIds, s.id, true);
                }
            }
        });
        // Drop spinners for sessions the server no longer reports as generating,
        // but keep the one this tab is actively streaming and form-awaiting sessions.
        [...runningSessionIds].forEach((id) => {
            if (generation.localSessionId != null && sessionIdsEqual(id, generation.localSessionId)) {
                return;
            }
            if ([...formAwaitingSessionIds].some((fid) => sessionIdsEqual(fid, id))) {
                return;
            }
            // While deferring after switch, don't let sessions poll clear/re-arm
            // the open chat — live-status owns that window.
            if (
                deferOpenChatGeneratingFromSessions
                && session.current() != null
                && sessionIdsEqual(id, session.current())
            ) {
                return;
            }
            if (!still.has(String(canonicalizeChatSessionId(id)))) {
                setHistorySessionRunning(id, false);
            }
        });
        const currentStillRunning = session.current() != null
            && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, session.current()));
        if (
            currentWasRunning
            && !currentStillRunning
            && !generation.loading
            && !stopState.waitingSuppressed
            && followupController().getState().items.length
        ) {
            scheduleFollowupDrain(80);
        }
    }

    // Archived chats: hidden from the main list, restorable from a collapsed
    // section at the bottom of the history panel (server is source of truth).
    let archivedChatSessions = [];
    let archivedSessionsLoaded = false;
    // Active (main-list) ids for archive reconciliation — the Archived fetch
    // may hit a backend that predates ?archived= (new JS ships from disk
    // before Flask restarts), which answers with the full list. Populated on
    // every full-list paint; see CuttleChatHistoryArchive.
    let activeHistorySessionIds = new Set();

    function historyArchiveModule() {
        return window.CuttleChatHistoryArchive || null;
    }

    // Every id form the page uses for one session: raw, frame-canonical,
    // and auth-db numeric — so the active set matches archived marks.
    function archiveIdExtras(id) {
        const out = [];
        if (id != null && id !== '') out.push(String(id));
        try {
            const canon = canonicalizeChatSessionId(id);
            if (canon != null && canon !== '') out.push(String(canon));
        } catch (_) {}
        const authSid = toAuthDbSessionId(id);
        if (authSid != null && authSid !== '') out.push(String(authSid));
        return out;
    }

    function rememberActiveHistorySessions(serverSessions) {
        const mod = historyArchiveModule();
        if (!mod) {
            activeHistorySessionIds = new Set();
            return;
        }
        activeHistorySessionIds = mod.activeSessionIdSet(serverSessions || [], archiveIdExtras);
        // A chat back in the main list is not archived (unarchived on another
        // device, or mis-marked by an unfiltered response) — drop stale marks.
        mod.staleArchivedMarks(Array.from(archivedChatSessionIds), activeHistorySessionIds)
            .forEach((marked) => unmarkChatSessionArchived(marked));
    }

    // Safe storage handle for the owner (node harnesses eval page slices
    // with no localStorage global; browsers always have it).
    function pageArchiveStorage() {
        try {
            return (typeof localStorage !== 'undefined') ? localStorage : null;
        } catch (_) {
            return null;
        }
    }

    function isArchiveSectionCollapsed() {
        const mod = historyArchiveModule();
        if (!mod) return true;
        return mod.isArchiveSectionCollapsed(pageArchiveStorage());
    }

    function toggleArchiveSection(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const mod = historyArchiveModule();
        if (mod) mod.storeArchiveSectionCollapsed(pageArchiveStorage(), !isArchiveSectionCollapsed());
        renderArchivedSection();
    }

    function toggleArchiveSectionKey(event) {
        if (event && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            toggleArchiveSection(event);
        }
    }

    async function refreshArchivedSessions() {
        if (!isAuthMode()) {
            archivedChatSessions = [];
            archivedSessionsLoaded = true;
            renderArchivedSection();
            if (session.current() != null && session.current() !== '')
                updateChatSessionTitle(session.current());
            return;
        }
        try {
            const response = await fetch('/api/auth/sessions?archived=only', { credentials: 'include' });
            const data = await response.json().catch(() => ({}));
            if (data && data.success) {
                const mod = historyArchiveModule();
                const listed = Array.isArray(data.sessions) ? data.sessions : [];
                // Drop chats the main list already shows: a backend without
                // ?archived= support answers with the full list, which must
                // not duplicate the panel or mark everything archived.
                archivedChatSessions = mod
                    ? mod.filterArchivedSessions(listed, activeHistorySessionIds, archiveIdExtras)
                    : [];
                archivedSessionsLoaded = true;
                archivedChatSessionIds.clear();
                archivedChatSessions.forEach((s) => markChatSessionArchived(s && s.id));
                if (!mod) console.warn('[Cuttle Chat] Archive module missing; skipping Archived section.');
            }
        } catch (error) {
            console.error('Error loading archived sessions:', error);
        }
        renderArchivedSection();
        // Archived marks arrive after the title renders on page load (and
        // change on archive toggle) — re-sync the title-bar badge.
        if (session.current() != null && session.current() !== '')
            updateChatSessionTitle(session.current());
    }

    function renderArchivedSection() {
        const historyContainer = document.getElementById('chatHistory');
        if (!historyContainer) return;
        const prev = document.getElementById('historyArchivedSection');
        const mod = historyArchiveModule();
        // Without the owner there is nothing to render:
        // refreshArchivedSessions already fail-closes to an empty list
        // then, so this return is unreachable in practice and changes no
        // live behavior.
        if (!mod || !archivedSessionsLoaded || !archivedChatSessions.length) {
            if (prev) CuttleChatHistoryView.preserveScroll(historyContainer, () => prev.remove());
            return;
        }
        // Defensive re-filter: the main list may have refreshed after the
        // Archived fetch resolved (archive toggle, delete, second device).
        const visible = mod.filterArchivedSessions(archivedChatSessions, activeHistorySessionIds, archiveIdExtras);
        if (!visible.length) {
            if (prev) CuttleChatHistoryView.preserveScroll(historyContainer, () => prev.remove());
            return;
        }
        const collapsed = isArchiveSectionCollapsed();
        const count = visible.length;
        let itemsHtml = '';
        visible.forEach((s) => {
            itemsHtml += historyView.createAuthHistoryItemHTML(s, {});
        });
        // Header template + section class live in CuttleChatHistoryArchive;
        // the page keeps container/row-DOM wiring (it owns the row renderer
        // and the window-facing toggle names passed in below).
        const section = document.createElement('div');
        section.className = mod.archivedSectionClassName(collapsed);
        section.id = 'historyArchivedSection';
        section.innerHTML = mod.archivedSectionHeaderHTML({
                collapsed,
                count,
                toggleHandler: 'window.chatPageToggleArchiveSection(event)',
                keyHandler: 'window.chatPageToggleArchiveSectionKey(event)',
            })
            + '<div class="history-section-body">' + itemsHtml + '</div>';
        const source = section.outerHTML;
        if (prev && prev.__cuttleArchiveHtml === source) return;
        section.__cuttleArchiveHtml = source;
        CuttleChatHistoryView.preserveScroll(historyContainer, () => {
            if (prev) prev.replaceWith(section);
            else historyContainer.appendChild(section);
        });
    }

    // Message Handling
    // Follow-up edit state + transitions are owned by the queue controller;
    // these are the thin call sites the edit UI and keyboard handlers use.
    function followupEditableText(item) {
        return CuttleFollowupQueue.followupEditableText(item);
    }

    function beginEditFollowup(id) { followupController().beginEdit(id); }

    function finishEditFollowup(opts = {}) { followupController().finishEdit(opts); }

    function cancelEditFollowup() { followupController().cancelEdit(); }

    function saveEditFollowup(id, nextText) { followupController().saveEdit(id, nextText); }

    function renderFollowupQueue() {
        const root = document.getElementById('followupQueue');
        if (!root) return;
        const ctl = followupController();
        ctl.pruneEditing();
        const queueItems = ctl.getState().items;
        const editingId = ctl.getEditingId();
        patchLiveSessionFollowupQueue(session.current(), queueItems);
        if (!queueItems.length) {
            root.hidden = true;
            root.innerHTML = '';
            syncHistoryUnreadIndicators();
            return;
        }
        root.hidden = false;
        const itemsHtml = queueItems.map((item, idx) => {
            const isEditing = item.id === editingId;
            if (isEditing) {
                const editVal = escapeHtml(followupEditableText(item));
                return `
                <div class="followup-queue-item is-editing" data-followup-id="${escapeHtml(item.id)}">
                    <span class="followup-queue-badge">#${idx + 1}</span>
                    <div class="followup-queue-edit">
                        <textarea class="followup-queue-edit-input" rows="3" aria-label="Edit queued message">${editVal}</textarea>
                        <div class="followup-queue-edit-actions">
                            <button type="button" class="followup-queue-edit-save" data-save-followup="${escapeHtml(item.id)}">Save</button>
                            <button type="button" class="followup-queue-edit-cancel" data-cancel-followup="${escapeHtml(item.id)}">Cancel</button>
                            <span class="followup-queue-edit-hint">Send paused while editing · Ctrl+Enter to save · Esc to cancel</span>
                        </div>
                    </div>
                </div>
            `;
            }
            const raw = String(item.content || '').trim();
            const parsed = parseStoredSlashCommandMessage(raw);
            let preview;
            if (parsed && parsed.chips.length) {
                const chips = normalizeAgentSlashChips(parsed.chips, session.current());
                const chipsHtml = chips
                    .map((c) => slashCommandChipHistoryHtml(c.label, c.meta, c.category))
                    .join('');
                const bodyText = escapeHtml(String(parsed.body || '').trim());
                preview = '<span class="slash-chips-inline">' + chipsHtml + '</span>'
                    + (bodyText ? '<span class="followup-queue-body">' + bodyText + '</span>' : '');
            } else {
                preview = escapeHtml(raw);
            }
            return `
                <div class="followup-queue-item${item.paused ? ' is-paused' : ''}" data-followup-id="${escapeHtml(item.id)}">
                    <span class="followup-queue-badge">#${idx + 1}</span>
                    <div class="followup-queue-text">${preview}</div>
                    <div class="followup-queue-actions">
                        <button type="button" class="followup-queue-pause-btn" title="${item.paused ? 'Resume queued message' : 'Pause queued message'}" aria-label="${item.paused ? 'Resume queued message' : 'Pause queued message'}" data-toggle-pause-followup="${escapeHtml(item.id)}" aria-pressed="${item.paused ? 'true' : 'false'}">
                            ${item.paused
                                ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`
                                : `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`}
                        </button>
                        <button type="button" class="followup-queue-edit-btn" title="Edit queued message" aria-label="Edit queued message" data-edit-followup="${escapeHtml(item.id)}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>
                            </svg>
                        </button>
                        <button type="button" class="followup-queue-remove" title="Remove from queue" aria-label="Remove queued message" data-remove-followup="${escapeHtml(item.id)}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                            </svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
        const otherPaused = queueItems.filter(
            (x) => x.paused && x.id !== editingId
        ).length;
        const titleBits = [`Queued · ${queueItems.length}`];
        if (editingId) titleBits.push('editing · send paused');
        if (otherPaused) titleBits.push(`${otherPaused} paused`);
        root.innerHTML = `
            <div class="followup-queue-header">
                <span class="followup-queue-title">${titleBits.join(' · ')}</span>
                <button type="button" class="followup-queue-clear" id="followupQueueClear">Clear all</button>
            </div>
            <div class="followup-queue-list">${itemsHtml}</div>
        `;
        root.querySelector('#followupQueueClear')?.addEventListener('click', clearFollowupQueue);
        root.querySelectorAll('[data-remove-followup]').forEach((btn) => {
            btn.addEventListener('click', () => removeFollowup(btn.getAttribute('data-remove-followup')));
        });
        root.querySelectorAll('[data-toggle-pause-followup]').forEach((btn) => {
            btn.addEventListener('click', () => toggleFollowupPaused(btn.getAttribute('data-toggle-pause-followup')));
        });
        root.querySelectorAll('[data-edit-followup]').forEach((btn) => {
            btn.addEventListener('click', () => beginEditFollowup(btn.getAttribute('data-edit-followup')));
        });
        root.querySelectorAll('[data-cancel-followup]').forEach((btn) => {
            btn.addEventListener('click', () => cancelEditFollowup());
        });
        root.querySelectorAll('[data-save-followup]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.getAttribute('data-save-followup');
                const row = btn.closest('.followup-queue-item');
                const ta = row && row.querySelector('.followup-queue-edit-input');
                saveEditFollowup(id, ta ? ta.value : '');
            });
        });
        const editTa = root.querySelector('.followup-queue-edit-input');
        if (editTa) {
            const sizeFollowupEditTa = () => {
                try {
                    editTa.style.height = 'auto';
                    const cs = window.getComputedStyle(editTa);
                    const maxPx = parseFloat(cs.maxHeight);
                    const next = editTa.scrollHeight;
                    if (Number.isFinite(maxPx) && maxPx > 0) {
                        editTa.style.height = Math.min(next, maxPx) + 'px';
                    } else {
                        editTa.style.height = next + 'px';
                    }
                } catch (_) {}
            };
            sizeFollowupEditTa();
            try {
                editTa.focus({ preventScroll: true });
            } catch (_) {
                editTa.focus();
            }
            const len = editTa.value.length;
            try {
                editTa.setSelectionRange(len, len);
            } catch (_) {}
            editTa.addEventListener('input', sizeFollowupEditTa);
            editTa.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    e.stopPropagation();
                    cancelEditFollowup();
                    return;
                }
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    e.stopPropagation();
                    const row = editTa.closest('.followup-queue-item');
                    const id = row && row.getAttribute('data-followup-id');
                    saveEditFollowup(id, editTa.value);
                }
            });
        }
        scheduleSlashChipCompactLabels(root);
        // Chips may reflow after compacting; measure once now and again shortly after.
        syncFollowupQueueActionsLayout(root);
        requestAnimationFrame(() => syncFollowupQueueActionsLayout(root));
        setTimeout(() => syncFollowupQueueActionsLayout(root), 80);
        syncHistoryUnreadIndicators();
    }

    /** Horizontal actions for short previews; stack when the text wraps tall. */
    function syncFollowupQueueActionsLayout(root) {
        const host = root || document.getElementById('followupQueue');
        if (!host) return;
        host.querySelectorAll('.followup-queue-item:not(.is-editing)').forEach((row) => {
            const text = row.querySelector('.followup-queue-text');
            const actions = row.querySelector('.followup-queue-actions');
            if (!text || !actions) return;
            let lineH = 18;
            try {
                const lh = parseFloat(window.getComputedStyle(text).lineHeight);
                if (Number.isFinite(lh) && lh > 0) lineH = lh;
            } catch (_) {}
            const tall = text.scrollHeight > (lineH * 1.65 + 2);
            row.classList.toggle('is-tall', tall);
            actions.classList.toggle('is-vertical', tall);
        });
    }

    function followupAuthSid() {
        if (!isAuthMode() || session.current() == null) return null;
        return toAuthDbSessionId(session.current());
    }

    function patchLiveSessionFollowupQueue(sessionId, followups) {
        if (sessionId == null) return;
        const s = findAuthServerSession(sessionId);
        if (s) {
            try {
                s.followup_queue = JSON.stringify(Array.isArray(followups) ? followups : []);
            } catch (_) {
                s.followup_queue = '[]';
            }
        }
    }

    function applyServerFollowups(list, revision) {
        followupController().applyServerList(list, revision);
    }

    // ---- follow-up transport (owned by the page) ----
    async function followupPutRequest(sid, body) {
        const response = await fetch('/api/auth/sessions/' + sid + '/followups', {
            method: 'PUT', credentials: 'include', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, status: response.status, data };
    }

    async function followupTakeRequest(sid, body) {
        const response = await fetch('/api/auth/sessions/' + sid + '/followups/take', {
            method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, status: response.status, data };
    }

    async function followupSteerRequest(sid, text) {
        try {
            const resp = await fetch('/api/chat-steer', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sid, message: text }),
            });
            return await resp.json().catch(() => null);
        } catch (_) {
            return null;
        }
    }

    function showFollowupChatArea() {
        const chatArea = document.getElementById('chatArea');
        if (chatArea && chatArea.style.display === 'none') {
            document.getElementById('welcomeScreen').style.display = 'none';
            chatArea.style.display = 'flex';
            if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        }
    }

    // ---- thin call sites (caller contracts; decisions live in the controller) ----
    function enqueueFollowup(message, opts = {}) {
        return followupController().enqueue(message, opts);
    }

    async function trySteerRunningTurn(message, attachments) {
        return followupController().steer(message, attachments);
    }

    function toggleFollowupPaused(id) { followupController().togglePaused(id); }

    function removeFollowup(id) { followupController().remove(id); }

    function clearFollowupQueue() { followupController().clear(); }

    function scheduleFollowupDrain(delayMs = 120) { followupController().requestDrain(delayMs); }

    async function drainNextFollowup() { return followupController().drain(); }

    async function requestServerCancelCurrentRun(opts = {}) {
        const sid = session.current();
        if (sid == null || sid === '') return null;
        const body = { session_id: sid };
        if (opts.cancelJobs) body.cancel_jobs = true;
        if (Array.isArray(opts.jobIds) && opts.jobIds.length) body.job_ids = opts.jobIds;
        try {
            const resp = await fetch('/api/chat-cancel', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            return await resp.json().catch(() => null);
        } catch (e) {
            console.warn('[Cuttle Chat] chat-cancel failed:', e);
            return null;
        }
    }

    /** Refocus composer after a reply only when this iframe already has focus and the user is in its composer — avoids stealing focus from another split pane. */
    function refocusChatInputIfAppropriate(chatInput) {
        if (!chatInput) return;
        if (!document.hasFocus()) return;
        const welcomeInput = document.getElementById('welcomeChatInput');
        const active = document.activeElement;
        const composerRoot = chatInput.closest('.chat-input-container');
        const welcomeRoot = welcomeInput?.closest('.welcome-input-container');
        const inComposer =
            active === chatInput
            || active === welcomeInput
            || (composerRoot && composerRoot.contains(active))
            || (welcomeRoot && welcomeRoot.contains(active));
        if (!inComposer) return;
        try {
            chatInput.focus({ preventScroll: true });
        } catch (_) {
            chatInput.focus();
        }
    }

    function isProjectShellTurn(message) {
        const s = String(message || '').trim();
        if (!s) return false;
        const rest = s.replace(
            /^\/(?:cursor(?:-cli)?|claude|hermes|codex|muse|opencode|antigravity|deepseek)\s+/i,
            ''
        );
        if (!rest.startsWith('/')) return false;
        const name = rest.slice(1).split(/\s+/)[0].toLowerCase();
        const reserved = {
            help: 1, project: 1, cd: 1,
            cursor: 1, claude: 1, hermes: 1,
            codex: 1, muse: 1, opencode: 1, antigravity: 1, deepseek: 1,
            model: 1, plan: 1, ask: 1, agent: 1, clear: 1, sandbox: 1, about: 1,
        };
        if (name === 'cmd' || name === 'cmds' || name === 'command') return true;
        return !!name && !reserved[name];
    }

    function runningWatchJobIds() {
        return actionCardsController().runningWatchJobIds();
    }

    function chatHasRunningWatchJob() {
        return runningWatchJobIds().length > 0;
    }

    function syncComposerStopWithWatch() {
        const stopButton = document.getElementById('stopButton');
        if (!stopButton) return;
        if (stopState.userStopped || generation.loading) return;
        if (chatHasRunningWatchJob()) {
            stopButton.style.display = 'flex';
            stopButton.disabled = false;
        } else {
            stopButton.disabled = true;
            stopButton.style.display = 'none';
        }
    }

    async function stopGenerating(opts = {}) {
        const announce = opts.announce !== false;
        const inflight = inFlightUserMessage;
        if (typeof window !== 'undefined' && window.CuttleCompletionNotifications) {
            window.CuttleCompletionNotifications.broker().cancel(session.current() || completionNotificationPendingId).catch(() => {});
        }
        const watchIds = runningWatchJobIds();
        const cancelJobs = (!inflight && watchIds.length > 0) || isProjectShellTurn(inflight);
        CuttleStopState.requestStop(stopState);
        inFlightUserMessage = null;
        followupController().cancelDrain();
        // Keep the stopped activity and durable notice immediately; cancellation
        // can take seconds, and its acknowledgment must not replace the notice.
        retainStoppedAgentBubble();
        if (announce) ensureGenerationStopNotice('⏹ Stopped generating.');
        try {
            if (activeRequestController) activeRequestController.abort();
        } catch (_) {}
        try {
            if (activeEventSource) {
                activeEventSource.close();
                activeEventSource = null;
            }
        } catch (_) {}
        removeTypingIndicator();
        removeRemoteWaitingIndicator();
        // Force: Stop owns the turn outright (the re-send claims a new token).
        endLocalGeneration();
        if (session.current()) setHistorySessionRunning(session.current(), false);
        activeRequestController = null;
        const chatInput = document.getElementById('chatInput');
        const sendButton = document.getElementById('sendButton');
        const stopButton = document.getElementById('stopButton');
        if (chatInput) {
            chatInput.disabled = false;
            refocusChatInputIfAppropriate(chatInput);
        }
        if (sendButton) sendButton.disabled = false;
        if (stopButton) {
            stopButton.disabled = true;
            stopButton.style.display = 'none';
        }
        setWelcomeComposerEnabled(true);
        requestServerCancelCurrentRun({
            cancelJobs,
            jobIds: cancelJobs ? watchIds : [],
        }).then((info) => {
            syncComposerStopWithWatch();
        });
        // Do not auto-drain the follow-up queue after an explicit Stop — that
        // looked like generation continuing. Queued items stay until Send/Clear.
    }

    async function processMessage(message, opts = {}) {
        const attachments = Array.isArray(opts.attachments) ? opts.attachments : [];
        // PREPARE stage — the pre-submit send decision record (control-lane
        // classification, sticky/slash meta, pending typing badge) is owned
        // by chat_generation.js; the page passes resolvers + applies effects.
        const sendPlan = CuttleChatGeneration.prepareSend({
            message,
            controlLaneOpt: !!opts.controlLane,
            isImmediateControl: isImmediateControlLaneMessage,
            stickyOf: getStickySlashCommandFromMessage,
            slashMetaOf: slashCommandMetaFromUserMessage,
            pendingSlashOf: pendingSlashForTypingIndicator,
            sessionKey: String(canonicalizeChatSessionId(session.current()) || ''),
        });
        const controlLane = sendPlan.controlLane;
        const stickyCmd = sendPlan.stickyCmd;
        const replySlash = sendPlan.replySlash;
        let responseData = null;
        // This turn's generation-lock token: end calls below release only
        // if no newer turn (or detach) has since claimed generation.
        let turnGenToken = null;
        // Control-lane replies must not clobber an in-flight agent turn's stop/abort state.
        const prevInFlight = inFlightUserMessage;
        const prevController = activeRequestController;
        if (!controlLane) {
            if (window.CuttleCompletionNotifications) window.CuttleCompletionNotifications.broker().begin(session.current() || completionNotificationPendingId);
            CuttleStopState.beginSend(stopState);
            inFlightUserMessage = message;
            turnGenToken = beginLocalGeneration();
        }

        const chatInput = document.getElementById('chatInput');
        const sendButton = document.getElementById('sendButton');
        const stopButton = document.getElementById('stopButton');

        if (chatInput) chatInput.disabled = false;
        if (sendButton) sendButton.disabled = false;
        if (!controlLane && stopButton) {
            stopButton.style.display = 'flex';
            stopButton.disabled = false;
        }
        // Welcome composer is only for the first turn
        setWelcomeComposerEnabled(false);

        // New turn owns the typing indicator — drop the previous turn's
        // sub-agent orbs first so they never bleed into a turn that uses none.
        // Live-status / stream syncs repopulate when this turn spawns sub-agents.
        try { subagentLaunchers.clearLive(); } catch (_) {}
        // Add typing indicator — pass slash meta so chip appears immediately
        turnBadgeBySession.set(sendPlan.badgeKey, sendPlan.pendingSlash);
        addTypingIndicator(sendPlan.pendingSlash);

        let skipFollowupDrain = false;
        let controlController = null;
        // Declared outside try so catch/finally can still gate paints after a switch.
        let boundSessionId = canonicalizeChatSessionId(session.current());
        // Snapshot nav generation at send time. New Chat / history switch bumps
        // turnGeneration; zombie status/adopt must not touch the new UI.
        const turnNavGen = CuttleTurnGuard.capture(turnGeneration);
        const turnSessionId = () => (
            boundSessionId != null
                ? boundSessionId
                : null
        );
        // Adapter: mutable per-turn bound id + live session state bound here;
        // the freshness/binding truth table lives in CuttleTurnGuard.
        const canPaintTurnHere = () => CuttleTurnGuard.canPaintHere(turnNavGen, turnSessionId(), {
            gen: turnGeneration.gen,
            currentSessionId: session.current(),
            isViewing: (id) => isViewingSession(id),
        });
        try {
            await ensureAuthChatSession();
            // Send to API with project context (stream for status updates; JSON fallback if SSE fails)
            // SUBMIT stage — the outbound request body, the origin session
            // binding, and the transport policy (SSE-vs-JSON, control-lane
            // JSON-only) are owned by chat_generation.js (planSubmit). The page
            // gathers the values, applies the page-owned identity/project
            // mutators, and owns the fetch/SSE transport.
            const submitPlan = CuttleChatGeneration.planSubmit({
                message: message,
                sessionId: authSessionIdForRequest(),
                currentSessionId: session.current(),
                controlLane: controlLane,
                stickyAgent: stickyAgentOverrideForRequest(message),
                attachments: attachments.length
                    ? CuttleChatAttachments.requestAttachmentPayload(attachments)
                    : null,
                controlRequestIdOpt: opts.controlRequestId,
                // Stable per-send id so a stream=false retry cannot mint a
                // second chat when this first POST still has a null session_id
                // (CH-000559).
                newChatRequestId: () => (
                    'ct_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10)
                ),
                newControlRequestId: () => (
                    (window.CuttleSupervised
                        && window.CuttleSupervised.newControlRequestId
                        && window.CuttleSupervised.newControlRequestId())
                    || (`cr_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`)
                ),
                canonicalize: canonicalizeChatSessionId,
            });
            const requestBody = submitPlan.body;
            // Pin this turn to the chat that sent it. Switching chats updates
            // currentSessionId; recovery/paint must keep using the origin id
            // or the reply lands in the newly opened transcript (phone bug).
            boundSessionId = submitPlan.boundSessionId;
            // New-chat model/effort picks (dirty, no session yet) must travel
            // with this POST — adopt-time pin POSTs race the agent + badge.
            attachAgentIdentityToRequest(requestBody);
            if (controlLane) {
                // Control-lane idempotency key is chosen by the owner.
                inFlightControlRequestId = submitPlan.controlRequestId;
                // Stamp the latest unmarked user bubble so sync can claim by control id.
                const userEl = claimUnmarkedMessageEl('user', message);
                if (userEl) {
                    userEl.dataset.controlRequestId = submitPlan.controlRequestId;
                }
            }

            // Include current project if available (path + id + name so /cursor
            // can resolve cwd even when path was missing from local prefs).
            applyOutboundProjectToRequest(requestBody);

            if (controlLane) {
                controlController = new AbortController();
            } else {
                activeRequestController = new AbortController();
            }
            const requestSignal = controlLane
                ? controlController.signal
                : activeRequestController.signal;

            async function parseChatJsonBody(resp) {
                const text = await resp.text();
                if (!text.trim()) {
                    return {
                        success: false,
                        response: `Empty reply from server (HTTP ${resp.status}). Is the Cuttle API still running?`
                    };
                }
                try {
                    return JSON.parse(text);
                } catch (parseErr) {
                    const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 320);
                    return {
                        success: false,
                        response:
                            `Server returned HTTP ${resp.status} with a non-JSON body (often a crash page or proxy error), not a normal pipeline reply. Preview: ${snippet}`
                    };
                }
            }

            async function fetchChatPayload(useStream) {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify({ ...requestBody, stream: useStream }),
                    signal: requestSignal
                });
                const contentType = response.headers.get('content-type') || '';

                if (contentType.includes('text/event-stream') && response.ok) {
                    let finalResult = null;
                    // Hold the SSE socket briefly so short replies stream live.
                    // Detach early — long holds pin Chromium's ~6-conn pool and
                    // starve sibling split-pane chats (one updates, others freeze).
                    const STREAM_HOLD_MAX_MS = inAppShell ? 10000 : 20000;
                    // reader.read() can hang forever on a half-open HTTPS body —
                    // never reach the detach check. Cap each read so we can exit.
                    const STREAM_READ_TIMEOUT_MS = 8000;
                    let sawProgress = false;
                    try { window.CuttleNetDebug && window.CuttleNetDebug.event('stream-hold', 'POST /api/chat'); } catch (_) {}

                    // Bytes/framing live in chat_stream.js; event
                    // classification, the per-event effect plan (paint gating +
                    // final-event selection) and session adoption live in
                    // chat_pending_result.js / chat_turn_guard.js; the page
                    // keeps request, DOM paint, and turn effects. onEvents
                    // applies the whole batch, then reports stop — last final
                    // frame wins, as before.
                    const streamOutcome = await CuttleChatStream.readEvents(response.body, {
                        holdMs: STREAM_HOLD_MAX_MS,
                        readTimeoutMs: STREAM_READ_TIMEOUT_MS,
                        signal: requestSignal,
                        onEvents: (events) => {
                            for (const ev of events) {
                                try {
                                    // Owned by chat_pending_result.js — event
                                    // classification + per-event effect plan;
                                    // the page applies DOM paint, session-adopt,
                                    // and finalResult effects.
                                    const streamEv = CuttleChatPendingResult.classifyStreamEvent(ev);
                                    if (streamEv.kind === 'session') {
                                        const sid = canonicalizeChatSessionId(streamEv.sessionId);
                                        requestBody.session_id = sid;
                                        if (boundSessionId == null) boundSessionId = sid;
                                        // Never yank the open chat to a different id mid-turn.
                                        // The freshness/binding truth table lives in
                                        // CuttleTurnGuard.shouldAdoptStreamSession; a stale
                                        // turn after New Chat (welcome is
                                        // currentSessionId == null) must not re-adopt chat A.
                                        if (CuttleTurnGuard.shouldAdoptStreamSession(
                                            turnNavGen, turnGeneration, boundSessionId, sid, {
                                                currentSessionId: session.current(),
                                                sessionIdsEqual: sessionIdsEqual,
                                                isViewing: (id) => isViewingSession(id),
                                            }
                                        )) {
                                            adoptChatSessionId(sid);
                                        }
                                        continue;
                                    }
                                    const streamPlan = CuttleChatPendingResult.planStreamEvent(
                                        streamEv, ev, {
                                            canPaint: canPaintTurnHere(),
                                            slashCommand: !!ev.slash_command,
                                        }
                                    );
                                    if (streamPlan.progress) sawProgress = true;
                                    if (streamPlan.paintStatus != null) {
                                        updateTypingStatus(streamPlan.paintStatus);
                                    }
                                    if (streamPlan.paintBadge) {
                                        rememberTurnBadge([], { active: true, slash_command: ev.slash_command });
                                        refreshTypingIndicatorHeaderBadges();
                                    }
                                    if (streamPlan.paintLink) {
                                        updateTypingIndicatorQueryLink(streamPlan.reportUrl);
                                        (window.showToast || function() {})('Running…', 'info', {
                                            linkUrl: streamPlan.queryId
                                                ? ('/query_log.html?id=' + encodeURIComponent(streamPlan.queryId))
                                                : streamPlan.reportUrl,
                                            linkText: 'View query log'
                                        });
                                    }
                                    if (streamPlan.final != null) finalResult = streamPlan.final;
                                } catch (e) { /* skip */ }
                            }
                            return finalResult != null;
                        },
                    });
                    if (streamOutcome.status === 'detached') {
                        try {
                            window.CuttleNetDebug && window.CuttleNetDebug.event(
                                'stream-detach',
                                (streamOutcome.reason === 'read-timeout'
                                    ? 'read-timeout after '
                                    : (sawProgress ? 'hold after ' : 'silent hold after '))
                                + Math.round(streamOutcome.elapsedMs / 1000) + 's'
                            );
                        } catch (_) {}
                    }
                    if (finalResult) {
                        try { window.CuttleNetDebug && window.CuttleNetDebug.event('stream-done', 'got response event'); } catch (_) {}
                        // Pop parked copy now that the browser actually got the SSE event.
                        recoveryController().consumeParkedChatResult(
                            finalResult.session_id
                            || turnSessionId()
                            || requestBody.session_id
                        );
                        // Final-result shaping (response_type -> type,
                        // cursor_run/usage/busy normalization) is owned by
                        // chat_pending_result.js; the page forwards the shaped
                        // payload so processMessage can enqueue a busy turn.
                        return CuttleChatPendingResult.shapeStreamFinalResult(finalResult);
                    }
                    // Detached or stream ended without a final event — wait for
                    // the parked reply (short polls; does not hold a socket open).
                    // Owned by chat_pending_result.js — recovery sequence; the
                    // page owns transport, paint, and turn/stop guards.
                    const pendingSid = turnSessionId() || requestBody.session_id;
                    const pendingPollMs = (inAppShell && !shellPaneFocused) ? 8000 : 4000;
                    const pendingDeps = {
                        // Unfocused split panes poll slower so the focused
                        // pane keeps connection-pool headroom.
                        wait: (sid) => recoveryController().collectPendingResult(sid, {
                            waitMs: 90 * 60 * 1000,
                            pollMs: pendingPollMs,
                            navGen: turnNavGen,
                        }),
                        retries: (sid) => recoveryController().recoverChatResultWithRetries(sid, 5, 1600),
                        fetchLive: (sid) => fetchChatLiveStatus(sid).catch(() => null),
                        liveLooksActive: (live) => liveStatusLooksActive(live),
                        canPaint: () => canPaintTurnHere(),
                        paintStatus: (text) => updateTypingStatus(text),
                        uiPaintedReply: () => {
                            const el = assistantElAfterInFlightUser();
                            return el
                                ? { present: true, raw: (el.dataset && el.dataset.rawContent) || '' }
                                : null;
                        },
                        hasTyping: () => !!document.getElementById('typing-indicator'),
                        isNavAway: () => stopState.abortSuppressed
                            || CuttleTurnGuard.isStale(turnNavGen, turnGeneration),
                        isStopped: () => stopState.userStopped,
                        netDebug: (name, info) => {
                            try {
                                window.CuttleNetDebug && window.CuttleNetDebug.event(name, info);
                            } catch (_) {}
                        },
                        waitOpts: {},
                    };
                    const settled = await CuttleChatPendingResult.recoverAfterStreamDetach(
                        pendingSid, turnNavGen, pendingDeps
                    );
                    return settled.data;
                }
                return parseChatJsonBody(response);
            }

            let data;
            try {
                // Control-lane: force non-stream so we never fall through SSE detach
                // into a second POST with the same control_request_id.
                data = await fetchChatPayload(submitPlan.firstTransportStream);
            } catch (streamErr) {
                // SUBMIT fallback policy is owned by chat_generation.js
                // (classifySubmitFailure); the page owns transport + recovery.
                const submitFailure = CuttleChatGeneration.classifySubmitFailure({
                    controlLane: controlLane,
                    errorName: streamErr && streamErr.name,
                });
                if (submitFailure === 'abort') throw streamErr;
                if (submitFailure === 'control-recover') {
                    // Do not transport-retry control commands — server may have
                    // already applied; recover from history / live status instead.
                    console.warn('[Cuttle Chat] Control-lane request failed; not retrying POST:', streamErr);
                    const sid = turnSessionId() || requestBody.session_id;
                    // Owned by chat_pending_result.js — recovery decision; the
                    // page owns transport/guards.
                    const laneOutcome = await CuttleChatPendingResult.recoverControlLaneFailure(
                        sid, streamErr, {
                            retries: (rsid) => recoveryController().recoverChatResultWithRetries(rsid, 3, 800),
                            controlRequestId: requestBody.control_request_id,
                        }
                    );
                    if (laneOutcome.outcome === 'data') {
                        data = laneOutcome.data;
                    } else {
                        throw laneOutcome.error;
                    }
                } else {
                console.warn('[Cuttle Chat] Streaming chat failed; retrying with stream=false:', streamErr);
                try {
                    data = await fetchChatPayload(false);
                } catch (retryErr) {
                    if (retryErr && retryErr.name === 'AbortError') throw retryErr;
                    // Phone sleep / Wi‑Fi blip: stream + retry both died, but the
                    // agent often keeps running server-side. Recover instead of
                    // inventing a "connection failed" assistant bubble.
                    // Owned by chat_pending_result.js — recovery sequence; the
                    // page owns transport, paint, and turn/stop guards.
                    const sid = turnSessionId() || requestBody.session_id;
                    console.warn('[Cuttle Chat] Chat request dropped; waiting for pending result:', retryErr);
                    const transportOutcome = await CuttleChatPendingResult.recoverAfterTransportFailure(
                        sid, turnNavGen, retryErr, {
                            canPaint: () => canPaintTurnHere(),
                            paintStatus: (text) => updateTypingStatus(text),
                            // Flask restart may be in progress — recover durable
                            // status + history without another user message.
                            recoverServer: (opts) => recoveryController().recoverAfterFlaskRestart(opts),
                            wait: (wsid) => recoveryController().collectPendingResult(wsid, {
                                waitMs: 90 * 60 * 1000,
                                navGen: turnNavGen,
                            }),
                            retries: (rsid) => recoveryController().recoverChatResultWithRetries(rsid, 5, 1600),
                            fetchLive: (fsid) => fetchChatLiveStatus(fsid),
                            waitOpts: {},
                        }
                    );
                    if (transportOutcome.outcome === 'data') {
                        data = transportOutcome.data;
                    } else if (transportOutcome.outcome === 'handoff-remote') {
                        // Hand off to remote waiting + poll; don't fake an error.
                        removeTypingIndicator();
                        // Clear local generating before remote-waiting UI (it no-ops while isLoading).
                        if (!controlLane) endLocalGeneration(turnGenToken);
                        startMessageSync();
                        updateRemoteWaitingFromMessages([], transportOutcome.live);
                        return;
                    } else {
                        throw transportOutcome.error;
                    }
                }
                }
            }

            responseData = data;

            // /branch may have moved the working branch — refresh the project chip.
            if (data && String(data.native_command || '') === '/branch') {
                try { refreshProjectBranch(true); } catch (_) {}
            }

            // Typed completion metadata may request a Flask restart. Generate
            // the native inline form here; agents never need proprietary tags.
            if (
                data
                && String(data.restart_required || '').toLowerCase() === 'flask'
                && typeof data.response === 'string'
                && !/__native_restart__/.test(data.response)
                && window.CuttleSupervised
                && window.CuttleSupervised.buildRestartFormHtml
            ) {
                data.response = data.response.trimEnd()
                    + '\n\n'
                    + window.CuttleSupervised.buildRestartFormHtml();
            }

            // Action-form silent results (toast + lock only — no assistant bubble).
            if (data && (data.type === 'action_form' || data.silent) && !(data.response || '').trim()) {
                removeTypingIndicator();
                if (data.toast) {
                    (window.showToast || function () {})(
                        data.toast,
                        data.success ? 'success' : 'error'
                    );
                }
                if (!controlLane) endLocalGeneration(turnGenToken);
                return;
            }

            // Keep composer / badges in sync when /model (or a run) reports a preferred model.
            syncPreferredModelFromResponse(data);

            // Remove typing indicator
            removeTypingIndicator();

            const assistantFailed = stickyCmd && isStickySlashAssistantFailure(stickyCmd, data);
            const routedBadge = data && data.routing_badge;
            let assistantSlash = routedBadge
                ? CuttleChatMessages.routingAgentSlash(routedBadge)
                : enrichSlashCommandWithCursorRun(replySlash, data && data.cursor_run, stickyCmd);
            if (routedBadge && routedBadge.agent === 'cursor' && data.cursor_run) {
                assistantSlash = enrichSlashCommandWithCursorRun(assistantSlash, data.cursor_run, null);
            }
            // Single badge path: canonical agent_model / agent_effort keys
            // (legacy per-agent keys as fallback) + drift warning chip.
            assistantSlash = enrichSlashCommandWithAgentBadge(assistantSlash, data, routedBadge ? null : stickyCmd);

            if (data && data.session_id != null && boundSessionId == null) {
                boundSessionId = canonicalizeChatSessionId(data.session_id);
            }

            // Finalize disposition is owned by chat_pending_result.js — the
            // page gathers the snapshot (turn identity / cancellation / busy)
            // and applies the matching effect below. Branch order lives in
            // the owner; do not re-derive it here.
            const finalizeDisp = CuttleChatPendingResult.classifyTurnFinalize({
                canPaint: canPaintTurnHere(),
                stopped: stopState.userStopped,
                cancelledText: isCancelledAgentText(data && (data.response || data.output)),
                busy: !!(data && data.busy),
                controlLane: controlLane,
                noReply: !!(data && data.no_reply),
                success: !!(data && data.success),
                hasResponse: !!(data && String(data.response || data.output || '').trim()),
                responseTruthy: !!(data && data.response),
                uiSystem: !!(data && data.ui === 'system'),
                hasNotice: !!(data && data.notice),
                preferredModelSet: !!(
                    data
                    && data.preferred_model
                    && /Model set to/i.test(String(data.response || data.output || ''))
                ),
            });
            if (finalizeDisp.kind === 'not-here') {
                // Switched chats while this turn was in flight. Reply is already
                // on the server under the origin session — never append it to the
                // open transcript (refresh used to "fix" the phantom bubble).
                const readySid = turnSessionId() || (data && data.session_id) || null;
                const readyBody = String(
                    (data && (data.response || data.output)) || ''
                ).trim();
                if (
                    readySid
                    && data
                    && !stopState.userStopped
                    && !data.busy
                    && !data.no_reply
                    // Require a real body — success+whitespace used to chirp from
                    // the post-nav "already painted" heuristic while still working.
                    && readyBody
                ) {
                    notifyAssistantResponseReady(readySid, { isError: !!assistantFailed });
                    // If we are somehow still on that session's pane (id drift /
                    // focus race), chirp alone left an empty transcript until refresh.
                    if (
                        session.current() != null
                        && sessionIdsEqual(session.current(), readySid)
                        && openTranscriptMissingThisTurnReply()
                        && readyBody
                    ) {
                        addMessageToUI(readyBody, 'assistant', {
                            report_url: data.report_url,
                            timestamp: Date.now(),
                        });
                    }
                }
            } else if (finalizeDisp.kind === 'cancelled') {
                consumeCancelledAgentReply(data && (data.response || data.output));
                // Stop already cleaned UI; don't append a partial/error reply.
            } else if (finalizeDisp.kind === 'system-notice') {
                const notice = String(data.notice || '').trim()
                    || ('⚡ Model: ' + preferredModelLabel(data.preferred_model));
                if (notice) addSystemMessage(notice, { persist: true, kind: 'model-change' });
                if (data.session_id) adoptChatSessionId(data.session_id);
            } else if (finalizeDisp.kind === 'busy-queue') {
                // Previous reply is still generating — queue this turn as a
                // follow-up instead of faking an assistant bubble or racing again.
                skipFollowupDrain = true;
                const sentBubble = claimUnmarkedMessageEl('user', message);
                if (sentBubble) sentBubble.remove();
                const queuedText = formatMessageWithAttachments(message, attachments);
                const queuedAttachments = Array.isArray(attachments) ? attachments.slice() : [];
                if (data.session_id) adoptChatSessionId(data.session_id);
                trySteerRunningTurn(message, queuedAttachments).then((steered) => {
                    if (steered) return;
                    (window.showToast || function () {})(
                        'Still working on your previous message — follow-up queued.',
                        'warning'
                    );
                    enqueueFollowup(queuedText, {
                        attachments: queuedAttachments,
                        rawMessage: message,
                    });
                });
                startMessageSync();
                // Re-attach waiting UI for the still-running first prompt.
                const sid = session.current() != null ? session.current() : data.session_id;
                Promise.resolve(fetchChatLiveStatus(sid)).then((live) => {
                    updateRemoteWaitingFromMessages([], live || {
                        active: true,
                        generating: true,
                        status: 'Still working on your previous message…',
                    });
                }).catch(() => {
                    updateRemoteWaitingFromMessages([], {
                        active: true,
                        generating: true,
                        status: 'Still working on your previous message…',
                    });
                });
            } else if (finalizeDisp.kind === 'wait-sync') {
                // Detached stream with no parked payload yet — don't fake
                // "No response received." Message sync / pending poll will
                // attach the real reply when the worker finishes.
                if (data.session_id) adoptChatSessionId(data.session_id);
                startMessageSync();
                const sid = session.current() != null ? session.current() : data.session_id;
                Promise.resolve(fetchChatLiveStatus(sid)).then((live) => {
                    if (liveStatusLooksActive(live) || (live && live.generating)) {
                        updateRemoteWaitingFromMessages([], live);
                    }
                }).catch(() => {});
            } else if (finalizeDisp.kind === 'assistant') {
                // Canonical supervised bubble: update in place; never append a
                // second worker/result participant for this task.
                const CS = window.CuttleSupervised;
                if (
                    CS
                    && typeof CS.shouldAppendControlAssistantBubble === 'function'
                    && !CS.shouldAppendControlAssistantBubble(data, seenControlRequestIds)
                    && (data.update_existing_bubble || data.canonical_bubble
                        || data.type === 'supervised_started'
                        || data.type === 'supervised_complete'
                        || data.type === 'supervised_cancelled')
                ) {
                    const box = document.getElementById('chatMessages');
                    let bubble = null;
                    if (data.coordinator_response_message_id != null) {
                        bubble = box && box.querySelector(
                            `.message[data-message-id="${CSS.escape(String(data.coordinator_response_message_id))}"]`
                        );
                    }
                    if (!bubble && data.task_id) {
                        bubble = box && box.querySelector(
                            `.message.assistant[data-supervised-task-id="${CSS.escape(String(data.task_id))}"]`
                        );
                    }
                    if (bubble) {
                        stampAssistantElFromServer(bubble, {
                            id: data.coordinator_response_message_id,
                            content: data.response,
                        });
                        bubble.dataset.supervisedTaskId = String(data.task_id || '');
                        bubble.classList.add('supervised-coordinator-message');
                        const disclosure = bubble.querySelector(
                            '.supervised-activity-disclosure, .supervised-activity-card'
                        );
                        if (disclosure) wireSupervisedActivityCard(disclosure);
                        if (data.type === 'supervised_complete' || data.supervised_terminal) {
                            showSupervisedJumpNotification({
                                task_id: data.task_id,
                                phase: data.phase,
                                terminal: true,
                                supervised_delivery_event_id: data.supervised_delivery_event_id,
                                coordinator_response_message_id: data.coordinator_response_message_id,
                            });
                        }
                    }
                    if (data.session_id) adoptChatSessionId(data.session_id);
                    removeTypingIndicator();
                    startMessageSync();
                    syncSessionMessagesFromServer();
                    // Fall through to finally without appending.
                } else
                // Control-lane: one action → one visible exchange. Idempotent /
                // reconcile_only replies must not append a second pair.
                if (controlLane) {
                    const cid = String(
                        (data && data.control_request_id)
                        || requestBody.control_request_id
                        || inFlightControlRequestId
                        || ''
                    );
                    if (
                        !cid
                        || data.idempotent
                        || data.reconcile_only
                        || data.history_reconcile_only
                        || seenControlRequestIds.has(cid)
                    ) {
                        seenControlRequestIds.add(cid);
                        if (data.session_id) adoptChatSessionId(data.session_id);
                        startMessageSync();
                        // Fall through only if nothing is on screen for this control id.
                        const already = document.querySelector(
                            `#chatMessages .message[data-control-request-id="${CSS.escape(cid)}"]`
                        );
                        if (already) {
                            removeTypingIndicator();
                            if (!controlLane) endLocalGeneration(turnGenToken);
                            // skip assistant append
                        } else if (!turnAlreadyShowsAssistantReply(data.response)) {
                            const assistantTs = Date.now();
                            const el = addMessageToUI(data.response, 'assistant', {
                                report_url: data.report_url,
                                timestamp: assistantTs,
                                slash_command: assistantSlash || undefined,
                                slash_command_failed: assistantFailed,
                                routing_badge: data.routing_badge || undefined,
                                usage: normalizeUsagePayload(data.usage) || undefined,
                            });
                            if (el && el.dataset) el.dataset.controlRequestId = cid;
                            seenControlRequestIds.add(cid);
                        }
                        // Continue to finally (do not duplicate saveChatSession path)
                        removeTypingIndicator();
                        if (data.session_id) adoptChatSessionId(data.session_id);
                        startMessageSync();
                        refreshChatWidgets();
                        syncSessionMessagesFromServer();
                    } else {
                        seenControlRequestIds.add(cid);
                        if (!turnAlreadyShowsAssistantReply(data.response)) {
                            const assistantTs = Date.now();
                            const elOpts = {
                                report_url: data.report_url,
                                timestamp: assistantTs,
                                slash_command: assistantSlash || undefined,
                                slash_command_failed: assistantFailed,
                                routing_badge: data.routing_badge || undefined,
                                usage: normalizeUsagePayload(data.usage) || undefined,
                            };
                            addMessageToUI(data.response, 'assistant', elOpts);
                            const lastAsst = document.querySelector('#chatMessages .message.assistant:last-child');
                            if (lastAsst) lastAsst.dataset.controlRequestId = cid;
                        }
                        if (data.session_id) adoptChatSessionId(data.session_id);
                        removeRemoteWaitingIndicator();
                        notifyAssistantResponseReady(session.current() || generation.localSessionId, {
                            isError: !!assistantFailed,
                        });
                        startMessageSync();
                        refreshChatWidgets();
                        syncSessionMessagesFromServer();
                    }
                } else
                // Add assistant response with query log link if available
                // (skip if this turn already shows that reply — not a prior bubble)
                if (!turnAlreadyShowsAssistantReply(data.response)) {
                    const assistantTs = Date.now();
                    addMessageToUI(data.response, 'assistant', {
                        report_url: data.report_url,
                        timestamp: assistantTs,
                        slash_command: assistantSlash || undefined,
                        slash_command_failed: assistantFailed,
                        routing_badge: data.routing_badge || undefined,
                        usage: normalizeUsagePayload(data.usage) || undefined,
                    });
                } else {
                    const existing = assistantElAfterInFlightUser();
                    if (existing) {
                        stampAssistantElFromServer(existing, {
                            content: data.response,
                            report_url: data.report_url,
                            routing_badge: data.routing_badge || undefined,
                            usage: normalizeUsagePayload(data.usage) || undefined,
                        });
                    }
                    transcriptNav.autoScrollChatToBottom();
                }
                if (!controlLane) {
                saveChatSession(data.response, 'assistant', {
                    query_id: data.query_id,
                    report_url: data.report_url,
                    slash_command: assistantSlash || undefined,
                    slash_command_failed: assistantFailed,
                    routing_badge: data.routing_badge || undefined,
                    usage: normalizeUsagePayload(data.usage) || undefined,
                });

                // Update session ID if provided
                if (data.session_id) {
                    adoptChatSessionId(data.session_id);
                }
                // Server rewrites <cuttle_widget> → pin chips before paint, so
                // formatMessage never upserts. Refresh the strip now (Electron
                // otherwise only shows Tasks after a hard reload).
                refreshChatWidgets();
                // Reply is on screen — drop any remote-waiting UI a parallel
                // sync may have armed from a stale live-status snapshot.
                removeRemoteWaitingIndicator();
                notifyAssistantResponseReady(session.current() || generation.localSessionId, {
                    isError: !!assistantFailed,
                });
                startMessageSync();
                // Pull server ids so optimistic bubbles get stamped
                syncSessionMessagesFromServer();
                }
            } else {
                // Error handling - show backend response when available
                const errBody = data.response || data.output
                    || 'Sorry, I encountered an error. Please try again.';
                if (/^\s*No response received\.?\s*$/i.test(errBody)) {
                    if (data.session_id) adoptChatSessionId(data.session_id);
                    startMessageSync();
                } else if (consumeCancelledAgentReply(errBody)) {
                    if (data.session_id) adoptChatSessionId(data.session_id);
                } else {
                    if (!turnAlreadyShowsAssistantReply(errBody)) {
                        addMessageToUI(errBody, 'assistant', {
                            report_url: data.report_url,
                            timestamp: Date.now(),
                            slash_command: assistantSlash || undefined,
                            slash_command_failed: assistantFailed,
                        });
                    }
                    saveChatSession(errBody, 'assistant', {
                        report_url: data.report_url,
                        slash_command: assistantSlash || undefined,
                        slash_command_failed: assistantFailed,
                    });
                    if (data.session_id) {
                        adoptChatSessionId(data.session_id);
                    }
                    notifyAssistantResponseReady(session.current() || generation.localSessionId, {
                        isError: true,
                    });
                    startMessageSync();
                }
            }
        } catch (error) {
            // Classified by chat_stop_state.js: navigation aborts keep
            // suppression (orphaned waiters exit); explicit Stop clears it
            // so a later recovery path is not stuck suppressed.
            const abortClass = CuttleStopState.classifySendAbort(stopState, error?.name);
            if (abortClass === 'detached' || abortClass === 'stopped') {
                if (abortClass === 'stopped') {
                    CuttleStopState.clearAbortSuppression(stopState);
                }
                removeTypingIndicator();
            } else if (error?.name === 'AbortError') {
                // Dropped local stream (shell pause, navigation, refresh).
                // Not a user Stop — stopGenerating() is the only writer of
                // "Stopped generating." The server run keeps going.
                removeTypingIndicator();
            } else {
                console.error('Error sending message:', error);
                removeTypingIndicator();
                responseData = null;
                const em = error && error.message ? String(error.message) : '';
                const netFail =
                    /failed to fetch|networkerror|network error|load failed|network request failed|err_connection|connection reset/i.test(
                        em
                    );
                const offlineMsg = netFail
                    ? `Could not reach the Cuttle API (connection failed) from ${window.location.origin}. Often a dropped link during a long agent run, Wi‑Fi blip, phone sleep, or Flask restart — confirm the tray daemon is still running, then retry. On PC prefer https://127.0.0.1:8080; on LAN phone use this same host (port 8000 HTTP or the HTTPS portal).`
                    : `Request failed${em ? ': ' + em : ''}. If the UI loaded but chat fails, check ${window.location.origin} matches the API and see the browser console for details.`;
                if (canPaintTurnHere()) {
                    addMessageToUI(offlineMsg, 'assistant', {
                        timestamp: Date.now(),
                        slash_command: replySlash || undefined,
                        slash_command_failed: !!stickyCmd,
                    });
                    saveChatSession(offlineMsg, 'assistant', {
                        slash_command: replySlash || undefined,
                        slash_command_failed: !!stickyCmd,
                    });
                    notifyAssistantResponseReady(
                        session.current() || generation.localSessionId || turnSessionId(),
                        { isError: true }
                    );
                } else {
                    const readySid = turnSessionId();
                    if (readySid) notifyAssistantResponseReady(readySid, { isError: true });
                }
            }
        } finally {
            // Re-enable inputs
            const wasStopped = stopState.userStopped;
            if (controlLane) {
                // Restore in-flight agent turn bookkeeping; do not end generation.
                inFlightUserMessage = prevInFlight;
                activeRequestController = prevController;
                removeTypingIndicator();
            } else {
                inFlightUserMessage = null;
                endLocalGeneration(turnGenToken);
                activeRequestController = null;
            }
            if (stickyCmd && !controlLane) {
                // Sticky agent chips are session-scoped until the user removes them.
                // Never clear on busy / network / agent failures — only the chip ✕ does.
                if (!wasStopped) {
                    persistStickySlashForCurrentSession();
                }
            }
            if (chatInput) {
                chatInput.disabled = false;
                refocusChatInputIfAppropriate(chatInput);
            }
            if (sendButton) sendButton.disabled = false;
            if (!controlLane && stopButton) {
                if (wasStopped) {
                    stopButton.disabled = true;
                    stopButton.style.display = 'none';
                } else {
                    syncComposerStopWithWatch();
                }
            }
            setWelcomeComposerEnabled(true);
            if (!controlLane && !wasStopped && !skipFollowupDrain) {
                scheduleFollowupDrain(80);
            }
        }
    }
    
    // Owned by chat_composer.js — DOM/state gather, domain decides.
    async function sendMessage(opts) {
        LOG('sendMessage called');
        // opts.text: send this instead of the composer (form resume). The
        // user's draft and staged attachments stay untouched.
        const overrideText = (opts && typeof opts.text === 'string') ? opts.text : null;
        // Guard only blocks true double-fire (same-tick button + Enter, mobile
        // double-tap) before a turn has claimed the generating slot. Once
        // isSessionGenerating() is true, fall through so follow-ups can queue —
        // the guard used to stay set for the whole processMessage await and
        // silently dropped every follow-up send.
        const generatingBeforeHeal = isSessionGenerating();
        if (sendDispatchGuard && !generatingBeforeHeal) {
            LOG('sendMessage: ignored (dispatch guard)');
            return;
        }
        const input = document.getElementById('chatInput');
        if (!input) {
            LOG_ERR('sendMessage: chatInput not found');
            return;
        }
        const composerSource = overrideText != null ? { id: input.id, value: overrideText } : input;
        const message = composeMessageWithSlashChip(composerSource).trim();
        const attachments = overrideText != null ? [] : pendingAttachments.slice();
        const clearComposer = () => {
            if (overrideText != null) return;
            input.value = '';
            clearComposerDraft(session.current());
        };

        if (!isSendableComposerMessage(message, attachments)) {
            LOG('sendMessage: early return (empty / agent-only)');
            return;
        }
        maybeRefreshCursorModelsCatalogFromOutbound(message);

        // While generating (local stream OR remote/server run after refresh),
        // queue follow-ups instead of starting a second request — EXCEPT for
        // supervised/restart control-lane commands, which must execute immediately.
        healStaleGeneratingState();
        const plan = CuttleChatComposer.composerSendPlan({
            guardSet: !!sendDispatchGuard,
            generatingBeforeHeal,
            generating: isSessionGenerating(),
            sendable: true,
            control: isImmediateControlLaneMessage(message),
            message,
            normalizedMessage: normalizeMessageContentForMatch(message),
            inFlightMessage: normalizeMessageContentForMatch(inFlightUserMessage),
            queuedMessages: followupController().getState().items.map((x) => normalizeMessageContentForMatch(x.content)),
        });
        if (plan.action === 'ignore-guard') {
            // Unreachable here (guard already returned above); kept so the
            // plan's full action space stays handled if this flow changes.
            LOG('sendMessage: ignored (dispatch guard)');
            return;
        }
        if (plan.action === 'ignore-duplicate') {
            LOG('sendMessage: ignored duplicate of in-flight/queued message');
            clearComposer();
            applyStickySlashAfterComposerSend(message);
            autoResizeTextarea();
            return;
        }
        if (plan.action === 'followup') {
            LOG('sendMessage: follow-up while generating — steer or queue');
            // Attachments on a queued follow-up: take them with this item
            const queuedText = formatMessageWithAttachments(message, attachments);
            const queuedAttachments = overrideText != null ? [] : takePendingAttachments();
            dismissOpenInteractiveCards('Ignored');
            clearComposer();
            // Keep sticky agent chip (/cursor, /claude, …) — same as a normal send.
            applyStickySlashAfterComposerSend(message);
            autoResizeTextarea();
            if (await trySteerRunningTurn(message, queuedAttachments)) return;
            enqueueFollowup(queuedText, {
                attachments: queuedAttachments,
                rawMessage: message,
            });
            return;
        }
        if (isSessionGenerating() && isImmediateControlLaneMessage(message)) {
            LOG('sendMessage: control-lane bypass while generating');
        }

        LOG('sendMessage: sending message');
        sendDispatchGuard = true;
        try {
            await ensureCodexEffortForSend(message);

            // Show chat area if it's hidden
            if (document.getElementById('chatArea').style.display === 'none') {
                document.getElementById('welcomeScreen').style.display = 'none';
                document.getElementById('chatArea').style.display = 'flex';
                if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
            }

            const displayMessage = formatMessageWithAttachments(message, attachments);
            if (overrideText == null) clearPendingAttachments();

            // Follow-up instead of clicking open cards → lock/collapse them.
            dismissOpenInteractiveCards('Ignored');

            // Add user message to UI
            const userTs = Date.now();
            addMessageToUI(displayMessage, 'user', { timestamp: userTs, attachments });
            transcriptNav.autoScrollChatToBottom(true);

            // Save to session
            saveChatSession(displayMessage, 'user', { attachments });
            if (overrideText == null) recordPromptHistory(message || displayMessage);

            clearComposer();
            applyStickySlashAfterComposerSend(message);
            autoResizeTextarea();

            // Claim generating before the long await so a follow-up send hits
            // isSessionGenerating() (and queues) instead of the dispatch guard.
            // Control-lane commands skip this — they must not monopolize the chat.
            const outbound = plan.outbound;
            const controlLane = plan.controlLane;
            if (!controlLane) {
                beginLocalGeneration();
                inFlightUserMessage = outbound;
            }

            // Process the message (beginLocalGeneration is idempotent)
            await processMessage(outbound, { attachments, controlLane });
        } finally {
            sendDispatchGuard = false;
        }
    }
    
    function userAvatarInnerHtmlForChat() {
        const user = window.CuttleAuth && window.CuttleAuth.getUser && window.CuttleAuth.getUser();
        if (user && window.CuttleAuth.userAvatarInnerHtml) {
            return window.CuttleAuth.userAvatarInnerHtml(user);
        }
        return '?';
    }

    function applySessionIdentity(data) {
        currentAgentIdentity = {
            displayName: String((data && data.display_name) || '').trim(),
            avatar: String((data && data.avatar) || '').trim(),
            profileId: String((data && data.agent_profile_id) || '').trim(),
            parentSessionId: (data && data.parent_session_id != null) ? data.parent_session_id : null,
            origin: String((data && data.origin) || '').trim(),
        };
    }

    function profileAvatarInnerHtml(avatar) {
        const a = String(avatar || '').trim();
        if (!a || a.toLowerCase() === 'cuttle') {
            return '<img src="/img/cuttle-avatar.png?v=3" alt="" class="assistant-avatar-img">';
        }
        const looksUrl = /^https?:\/\//i.test(a) || (a.charAt(0) === '/' && a.charAt(1) !== '/');
        if (looksUrl) {
            if (!CuttleChatMedia.isSafeMediaSrc(a)) {
                return '<img src="/img/cuttle-avatar.png?v=3" alt="" class="assistant-avatar-img">';
            }
            return '<img src="' + escapeHtmlInline(a) + '" alt="" class="assistant-avatar-img">';
        }
        return '<span class="profile-avatar-emoji" aria-hidden="true">' + escapeHtmlInline(a) + '</span>';
    }

    function assistantAvatarInnerHtml(avatar) {
        return profileAvatarInnerHtml(avatar || currentAgentIdentity.avatar);
    }

    function assistantDisplayName(opts) {
        const fromMsg = opts && opts.speaker && String(opts.speaker).trim();
        if (fromMsg) return fromMsg;
        return currentAgentIdentity.displayName || agentName;
    }

    function isParentSpeakerOpts(opts) {
        if (!opts) return false;
        if (String(opts.speaker_kind || '') === 'parent') return true;
        if (String(opts.origin || '') !== 'subagent') return false;
        return !!(opts.parent_handle || (opts.parent_session_id != null && opts.parent_session_id !== ''));
    }

    function refreshUserMessageAvatars() {
        const inner = userAvatarInnerHtmlForChat();
        document.querySelectorAll('.message.user .message-avatar').forEach((el) => {
            if (el.closest('.message.user.is-parent-speaker')) return;
            el.innerHTML = inner;
        });
    }

    function addMessageToUI(content, role, opts = {}) {
        const messagesContainer = document.getElementById('chatMessages');
        if (!messagesContainer) return null;

        // Durable chat-log notices (project change, stop, model) — same chrome as
        // live addSystemMessage, restored from auth DB on refresh / sync.
        // These rows were already persisted (they have a message_id), so render
        // them sticky (no 3s auto-remove) and skip re-inserting ones already shown.
        if (role === 'system') {
            const mid = opts.message_id != null ? String(opts.message_id) : null;
            const messagesContainer = document.getElementById('chatMessages');
            if (mid && messagesContainer) {
                const already = messagesContainer.querySelector(
                    `.message.system[data-message-id="${CSS.escape(mid)}"]`
                );
                if (already) return already;
            }
            return addSystemMessage(String(content ?? ''), {
                persist: true,
                kind: opts.kind,
                message_id: mid,
                replace: false,
                prepend: !!opts.prepend,
                insertBefore: opts.insertBefore || null,
                skipScroll: !!opts.skipScroll,
            });
        }

        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;
        messageDiv.dataset.rawContent = String(content ?? '');
        if (opts.query_id) messageDiv.dataset.queryId = String(opts.query_id);
        if (opts.message_id != null && opts.message_id !== '') {
            messageDiv.dataset.messageId = String(opts.message_id);
        }
        if (opts.control_request_id != null && opts.control_request_id !== '') {
            messageDiv.dataset.controlRequestId = String(opts.control_request_id);
        }
        if (opts.supervised_task_id) {
            messageDiv.dataset.supervisedTaskId = String(opts.supervised_task_id);
            messageDiv.classList.add('supervised-coordinator-message');
        }
        const parentSpeaker = role === 'user' && isParentSpeakerOpts(opts);
        if (parentSpeaker) {
            messageDiv.classList.add('is-parent-speaker');
        }
        if (role === 'user' && opts.steered) {
            messageDiv.classList.add('is-steered');
        }
        if (
            role === 'assistant'
            && (
                opts.slash_command_failed
                || assistantReplyLooksLikeError(content, {
                    slash_command_failed: opts.slash_command_failed,
                })
            )
        ) {
            messageDiv.dataset.failed = '1';
        }
        
        const avatar = parentSpeaker
            ? profileAvatarInnerHtml(opts.avatar || 'cuttle')
            : (role === 'user'
                ? userAvatarInnerHtmlForChat()
                : assistantAvatarInnerHtml(opts.avatar));
        const speakerName = (opts.speaker && String(opts.speaker).trim()) || '';
        const sender = parentSpeaker
            ? (speakerName || 'Cuttle')
            : (role === 'user'
                ? (speakerName || 'You')
                : assistantDisplayName(opts));
        const reportUrl = opts.report_url || null;
        const messageFooter = getMessageFooterHtml(role, reportUrl, true, opts.usage, opts.user_feedback);
        // Stash normalized usage for the voice overlay (per-reply + session totals).
        try {
            messageDiv._cuttleUsage = (role === 'assistant' && opts.usage)
                ? normalizeUsagePayload(opts.usage)
                : null;
        } catch (_) {
            messageDiv._cuttleUsage = null;
        }
        const ts = resolveMessageTimestamp(opts.timestamp);
        const timeAgo = formatTimeAgo(ts);
        const timeLabel = timeAgo === 'now' ? 'Just now' : 'Sent ' + timeAgo + ' ago';
        const sc = normalizeSlashCommandStored(opts.slash_command || (role === 'assistant' ? CuttleChatMessages.routingAgentSlash(opts.routing_badge) : null));
        let project = projectFromMessageOpts(opts, projects, currentProject);
        if (!project && opts.message_id == null && currentProject) {
            project = currentProject;
        }
        let headerChip = '';
        let suppressInlineSlashChips = false;
        if (role === 'assistant') {
            // Re-derive badge from cursor_run when present so history doesn't keep
            // stale CLI "200K" labels that disagree with the palette "1M" name.
            const enriched =
                opts.cursor_run
                    ? enrichSlashCommandWithCursorRun(sc, opts.cursor_run, null)
                    : sc;
            // normalizeAgentSlashChips (sessionId null) only fixes harness
            // palette category — it does not rewrite stored labels with live pins.
            const chips = (enriched && enriched.chips)
                ? normalizeAgentSlashChips(enriched.chips, null)
                : [];
            headerChip = messageHeaderBadgesHtml(chips, !!opts.slash_command_failed, project)
                + CuttleChatMessages.renderAssistantStatusHtml(opts, messageDiv.dataset.failed === '1', content, escapeHtmlInline);
        } else if (role === 'user') {
            // Stored user chips are a send-time snapshot (model + effort).
            // Never re-enrich them with live pins — that rewrote every older
            // bubble on refresh. Live pins only fill in legacy turns that
            // were persisted before the snapshot existed.
            const hasStoredChips = !!(sc && sc.chips && sc.chips.length);
            const baseSc = hasStoredChips
                ? sc
                : (parentSpeaker ? null : slashCommandMetaFromUserMessage(content));
            const userSc = hasStoredChips || parentSpeaker ? baseSc : enrichSlashCommandWithClaudeModel(enrichSlashCommandWithCodexModel(
                enrichSlashCommandWithOpenCodeModel(
                    enrichSlashCommandWithHermesModel(
                        enrichSlashCommandWithMuseModel(
                            baseSc,
                            slashPaletteSupplement.museModel,
                            slashPaletteSupplement.museEffort
                        ),
                        slashPaletteSupplement.hermesModel,
                        slashPaletteSupplement.hermesEffort
                    ),
                    slashPaletteSupplement.opencodeModel,
                    slashPaletteSupplement.opencodeEffort
                ),
                slashPaletteSupplement.codexModel,
                slashPaletteSupplement.codexEffort
            ), slashPaletteSupplement.claudeModel, slashPaletteSupplement.claudeEffort);
            const chips = (userSc && userSc.chips)
                ? normalizeAgentSlashChips(userSc.chips, null)
                : [];
            headerChip = messageHeaderBadgesHtml(chips, false, project);
            if (opts.steered) headerChip += steerBadgeHtml(opts.steered_agent);
            if (chips.length) suppressInlineSlashChips = true;
        } else {
            headerChip = messageHeaderBadgesHtml([], false, project);
        }
        const displayContent = role === 'assistant'
            ? stripLegacyAgentChrome(content)
            : content;
        
        messageDiv.innerHTML = `
            <div class="message-avatar">${avatar}</div>
            <div class="message-content-wrapper">
                <div class="message-header-row">
                    <span class="message-sender-group">
                        <span class="message-sender">${sender}</span>
                        ${headerChip}
                        ${role === 'assistant' ? chatTts.playButtonHtml() : ''}
                    </span>
                    <span class="message-timestamp" data-ts="${ts}">${timeLabel}</span>
                </div>
                <div class="message-content">${role === 'user' ? formatUserMessageForDisplay(displayContent, { ...opts, suppressInlineSlashChips }) : formatMessage(displayContent)}</div>
                ${messageFooter}
            </div>
        `;
        
        if (opts.prepend) {
            const anchor = opts.insertBefore
                || messagesContainer.querySelector('.message')
                || messagesContainer.firstChild;
            if (anchor) messagesContainer.insertBefore(messageDiv, anchor);
            else messagesContainer.appendChild(messageDiv);
        } else {
            messagesContainer.appendChild(messageDiv);
        }
        try {
            activateEnhancements(messageDiv);
        } catch (_) {}
        try { subagentLaunchers.mountForMessage(messageDiv, role, opts); } catch (_) {}
        scheduleSlashChipCompactLabels(messageDiv);
        if (!opts.skipNav) transcriptNav.updateMessageNav();
        if (!opts.skipScroll) {
            transcriptNav.autoScrollChatToBottom();
            if (role === 'assistant' && transcriptNav.isFollowingLatest()) {
                // Late layout (code blocks, images) can grow the bubble after paint.
                requestAnimationFrame(() => transcriptNav.autoScrollChatToBottom(true));
            }
        }
        return messageDiv;
    }
    
    const QUERY_LOG_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="16" y1="13" x2="8" y2="13"/>
        <line x1="16" y1="17" x2="8" y2="17"/>
        <polyline points="10 9 9 9 8 9"/>
    </svg>`;
    const COPY_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
    </svg>`;
    const SHARE_REF_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>`;
    const THUMB_UP_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>
    </svg>`;
    const THUMB_DOWN_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>
    </svg>`;

    // Bubble speech: prefs, speaker buttons and the shared audio channel (chat_tts.js).
    const chatTts = CuttleChatTts.create({
        win: window,
        fetch: (url, opts) => fetch(url, opts),
        toast: (message, variant) => (window.showToast || function () {})(message, variant),
        speakTextFor: getMessageSpeakText,
        normalizeUsage: normalizeUsagePayload,
    });

    // ── Voice mode: owned by chat_voice.js; the page supplies chat send + TTS ──
    function lastAssistantMessageEl() {
        const box = document.getElementById('chatMessages');
        if (!box) return null;
        const list = box.querySelectorAll(CHAT_RECORD_SELECTOR + '.assistant');
        return list.length ? list[list.length - 1] : null;
    }

    function composeVoiceOutbound(spokenText) {
        const body = String(spokenText || '').trim();
        if (!body) return '';
        const input = document.getElementById('chatInput')
            || document.getElementById('welcomeChatInput');
        if (!input || typeof composeMessageWithSlashChip !== 'function') return body;
        const prev = input.value;
        try {
            input.value = body;
            return composeMessageWithSlashChip(input) || body;
        } finally {
            input.value = prev;
        }
    }

    async function submitVoiceTurn(message, spokenText) {
        const chatArea = document.getElementById('chatArea');
        if (chatArea && chatArea.style.display === 'none') {
            const welcome = document.getElementById('welcomeScreen');
            if (welcome) welcome.style.display = 'none';
            chatArea.style.display = 'flex';
            if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        }
        dismissOpenInteractiveCards('Ignored');
        addMessageToUI(message, 'user', { timestamp: Date.now() });
        saveChatSession(message, 'user');
        recordPromptHistory(spokenText || message);
        applyStickySlashAfterComposerSend(message);
        // Turn lifecycle (control-lane classification, busy-lock begin, in-flight
        // tracking, prepare/submit/stream/finalize) is owned by the shared
        // turn-submission interface: processMessage over chat_generation. Voice
        // is a thin submitter, exactly like the terminal/button/action-card and
        // supervised lanes — never a second execution lifecycle.
        await processMessage(message);
    }

    const chatVoice = CuttleChatVoice.create({
        closeMenus: closeAllSessionMenus,
        isGenerating: isSessionGenerating,
        compose: composeVoiceOutbound,
        isSendable: (message) => isSendableComposerMessage(message, []),
        isControlLane: isImmediateControlLaneMessage,
        steer: (message) => trySteerRunningTurn(message, []),
        enqueue: (message, spoken) => enqueueFollowup(message, { rawMessage: spoken }),
        submit: submitVoiceTurn,
        lastAssistantMessage: lastAssistantMessageEl,
        speechFor: chatTts.speechForMessage,
        play: chatTts.playSpeech,
        stopSpeech: () => chatTts.stop(),
        playClip: (url) => chatTts.playAudio(url, null, false),
        playbackLevel: chatTts.playbackLevel,
        usageFor: (el) => (el && el._cuttleUsage) || null,
        sumUsage: (parts) => CuttleChatUsage.sum(parts),
        usageHtml: (usage) => getMessageUsageHtml(usage),
        sessionUsage: () => {
            const box = document.getElementById('chatMessages');
            if (!box) return null;
            const parts = [];
            box.querySelectorAll(CHAT_RECORD_SELECTOR + '.assistant').forEach((el) => {
                if (el._cuttleUsage) parts.push(el._cuttleUsage);
                if (el._cuttleVoiceUsage) parts.push(el._cuttleVoiceUsage);
            });
            return CuttleChatUsage.sum(parts);
        },
        experimentalFlags: fetchEnabledExperimentalFlags,
        fetch: (url, opts) => fetch(url, opts),
        toast: (message, variant, opts) => (window.showToast || function () {})(message, variant, opts),
        logError: LOG_ERR,
    });

    function formatTokenCount(n) {
        return CuttleChatUsage.formatTokenCount(n);
    }

    function normalizeUsagePayload(raw, metadata) {
        return CuttleChatUsage.normalize(raw, metadata);
    }

    function getMessageUsageHtml(usage) {
        return CuttleChatUsage.render(usage, escapeHtml);
    }

    function getQueryLogLinkHtml(reportUrl) {
        const href = reportUrl || '/query_log.html';
        const qid = queryIdFromReportUrl(reportUrl);
        const title = reportUrl ? 'View query log' : 'View query log index';
        const qAttr = qid ? ` data-query-id="${escapeHtmlInline(qid)}"` : '';
        return `<a class="message-query-log-link message-icon-btn" href="${href}"${qAttr} rel="noopener" title="${title}">${QUERY_LOG_ICON}</a>`;
    }

    /**
     * Footer for a *running* turn. The button paints immediately, before the
     * harness mints a query id, so it ships `data-query-pending` and stays
     * inert — clicking it must never navigate the chat panel away.
     * `applyQueryLogTarget` promotes it to a real inspector link.
     */
    function getLiveQueryLogFooterHtml() {
        return (
            `<div class="message-footer">` +
            `<div class="message-footer-icons">` +
            `<a class="message-query-log-link message-icon-btn is-pending" href="/query_log.html" ` +
            `data-query-pending="1" aria-disabled="true" ` +
            `title="Query log appears once the agent starts working">${QUERY_LOG_ICON}</a>` +
            `</div></div>`
        );
    }

    /** Point a typing indicator's query-log button at a real log. */
    function applyQueryLogTarget(link, reportUrl) {
        if (!link || !reportUrl) return;
        link.href = reportUrl;
        link.title = 'View query log (in progress)';
        link.removeAttribute('data-query-pending');
        link.removeAttribute('aria-disabled');
        link.classList.remove('is-pending');
        const qid = queryIdFromReportUrl(reportUrl);
        if (qid) link.setAttribute('data-query-id', qid);
        else link.removeAttribute('data-query-id');
    }

    function getCopyButtonHtml() {
        return `<button type="button" class="message-copy-btn message-icon-btn" title="Copy to clipboard" onclick="window.chatPageCopyMessage(this)">${COPY_ICON}</button>`;
    }

    function getShareRefButtonHtml() {
        return (
            `<button type="button" class="message-share-ref-btn message-icon-btn" ` +
            `title="Copy message ref (CH-000000-N)" ` +
            `aria-label="Copy message reference" ` +
            `onclick="window.chatPageCopyMessageRef(this)">${SHARE_REF_ICON}</button>`
        );
    }

    function queryIdFromReportUrl(reportUrl) {
        const s = String(reportUrl || '');
        let m = /[?&]id=([A-Za-z0-9_-]+)/.exec(s);
        if (m) return m[1];
        m = /query_report_([A-Za-z0-9_-]+)\.html/.exec(s);
        return m ? m[1] : '';
    }

    function getFeedbackButtonsHtml(reportUrl, feedback) {
        const qid = queryIdFromReportUrl(reportUrl);
        if (!qid || !isAuthMode()) return '';
        const btn = (value, label, icon) => {
            const on = feedback === value;
            return `<button type="button" class="message-feedback-btn message-icon-btn${on ? ' is-on' : ''}" ` +
                `data-feedback="${value}" data-query-id="${escapeHtmlInline(qid)}" aria-pressed="${on ? 'true' : 'false'}" ` +
                `title="${label} (feeds My Cuttle Performance)" aria-label="${label}" ` +
                `onclick="window.chatPageTurnFeedback(this)">${icon}</button>`;
        };
        return btn('good', 'Good answer', THUMB_UP_ICON) + btn('bad', 'Bad answer', THUMB_DOWN_ICON);
    }

    function getAssistantMessageFooterHtml(reportUrl, includeCopy, usage, feedback) {
        const queryLog = getQueryLogLinkHtml(reportUrl);
        const shareBtn = includeCopy ? getShareRefButtonHtml() : '';
        const copyBtn = includeCopy ? getCopyButtonHtml() : '';
        const feedbackBtns = includeCopy ? getFeedbackButtonsHtml(reportUrl, feedback) : '';
        const usageHtml = getMessageUsageHtml(usage);
        return (
            `<div class="message-footer">` +
            `<div class="message-footer-icons">${queryLog}${shareBtn}${copyBtn}${feedbackBtns}</div>` +
            usageHtml +
            `</div>`
        );
    }

    function getMessageFooterHtml(role, reportUrl, includeCopy, usage, feedback) {
        if (role === 'assistant') {
            return getAssistantMessageFooterHtml(reportUrl, includeCopy, usage, feedback);
        }
        if (role === 'user' && includeCopy) {
            // Same .message-footer shell as assistant so narrow display:contents
            // layouts can order the whole footer below the bubble.
            return (
                `<div class="message-footer message-footer--user">` +
                `<div class="message-footer-icons message-footer-icons--user">` +
                `${getShareRefButtonHtml()}${getCopyButtonHtml()}</div></div>`
            );
        }
        return '';
    }

    // Agent context radial (Cursor / Muse / OpenCode / Codex …): owned by
    // chat_context_gauge.js. The page supplies page scope as capabilities; the
    // controller owns the status/timer/popover state.
    const contextGauge = CuttleChatContextGauge.create({
        win: window,
        fetch: (url, opts) => fetch(url, opts),
        escapeHtml,
        normalizeUsage: normalizeUsagePayload,
        getSlashCtx: () => slashCtx,
        getSessionId: () => session.current(),
        getProjectPath: () => (currentProject && currentProject.path) ? String(currentProject.path) : '',
        toast: (message, variant) => (window.showToast || function () {})(message, variant),
    });

    // Transcript navigation: follow-latest scroll, nav rail + pins, jumps,
    // ?msg= deep links and message refs (chat_transcript_nav.js).
    const transcriptNav = CuttleChatTranscriptNav.create({
        win: window,
        storage: localStorage,
        escapeHtmlInline,
        recordSelector: CHAT_RECORD_SELECTOR,
        currentSessionId: () => session.current(),
        formatChatDisplayId,
        olderVisibleCount: () => chatHistoryOlderVisibleCount,
        hasOlderMessages: () => chatHistoryHasMore,
        loadOlderMessages: loadOlderChatMessages,
        unseenBelow: () => chatUnseenBelow,
        attentionIsError: () => chatAttentionIsError,
        clearUnseenBelow: clearChatUnseenBelow,
        activateViewport: activateChatViewport,
        copyText: copyTextToClipboard,
    });

    function formatTimeAgo(ts) {
        if (!ts || ts <= 0) return 'now';
        const diff = Date.now() - ts;
        // Small future skew (clock / TZ rounding) still reads as "now".
        if (diff < 1000) return 'now';
        if (diff < 60000) return Math.floor(diff / 1000) + 's';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
        if (diff < 604800000) return Math.floor(diff / 86400000) + 'd';
        if (diff < 31536000000) return Math.floor(diff / 604800000) + 'w';
        return Math.floor(diff / 31536000000) + 'y';
    }

    function getMessageCopyText(btn) {
        const messageEl = btn.closest('.message');
        const raw = messageEl?.dataset?.rawContent;
        let text = '';
        if (raw != null && String(raw).trim()) {
            text = String(raw);
        } else {
            const wrapper = btn.closest('.message-content-wrapper');
            const contentEl = wrapper?.querySelector('.message-content');
            text = contentEl?.innerText?.trim() || '';
        }
        // Slash chips (/cursor, /muse, …) are UI chrome — copy the body only.
        const parsedSlash = parseStoredSlashCommandMessage(text);
        if (parsedSlash) {
            text = parsedSlash.body || '';
        }
        const CS = window.CuttleSupervised;
        if (CS && typeof CS.stripCuttleCopyMetadata === 'function') {
            return CS.stripCuttleCopyMetadata(text);
        }
        // Fallback if supervised helper not loaded yet.
        return String(text)
            .replace(/<cuttle_supervised_activity\b[^>]*>[\s\S]*?<\/cuttle_supervised_activity>/gi, '')
            .replace(/<cuttle_trace\b[^>]*>[\s\S]*?<\/cuttle_trace>/gi, '')
            .replace(/<cuttle_action_form(?:_pending)?\b[^>]*>[\s\S]*?<\/cuttle_action_form(?:_pending)?>/gi, '')
            .replace(/<cuttle_widget\b[^>]*>[\s\S]*?<\/cuttle_widget\s*>/gi, '')
            .replace(/<cuttle_confirm(?:_pending)?\b[^>]*>([\s\S]*?)<\/cuttle_confirm(?:_pending)?>/gi, '$1')
            .replace(/<cuttle_button\b[^>]*\/>/gi, '')
            .replace(/<cuttle_button\b[^>]*>[\s\S]*?<\/cuttle_button>/gi, '')
            .replace(/<cuttle_form\b[^>]*>[\s\S]*?<\/cuttle_form>/gi, '')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    /** Speakable body only — never include Progress (<think>). */
    function getMessageSpeakText(btn) {
        let text = getMessageCopyText(btn);
        text = String(text || '')
            .replace(/<think\b[^>]*>[\s\S]*?<\/think\s*>/gi, '')
            .replace(/<redacted_thinking\b[^>]*>[\s\S]*?<\/redacted_thinking\s*>/gi, '')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
        return text;
    }

    function copyTextFallback(text) {
        // Android Chrome over plain HTTP rejects off-screen / opacity:0 nodes.
        // Prefer an in-viewport selection target, then a near-visible textarea.
        const cleanup = [];
        const tryExec = () => {
            try {
                return document.execCommand('copy');
            } catch (_) {
                return false;
            }
        };
        try {
            const pre = document.createElement('pre');
            pre.textContent = text;
            pre.setAttribute('aria-hidden', 'true');
            pre.style.cssText =
                'position:fixed;top:0;left:0;width:1px;height:1px;margin:0;padding:0;' +
                'border:0;outline:none;opacity:0.01;overflow:hidden;z-index:2147483647;' +
                'white-space:pre-wrap;word-break:break-all;pointer-events:none;';
            document.body.appendChild(pre);
            cleanup.push(pre);
            const range = document.createRange();
            range.selectNodeContents(pre);
            const sel = window.getSelection();
            if (sel) {
                sel.removeAllRanges();
                sel.addRange(range);
            }
            if (tryExec()) return true;

            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.setAttribute('aria-hidden', 'true');
            // Keep inside viewport — Android often ignores left:-9999px copies.
            ta.style.cssText =
                'position:fixed;top:0;left:0;width:2px;height:2px;padding:0;margin:0;' +
                'border:0;opacity:0.01;z-index:2147483647;font-size:16px;';
            document.body.appendChild(ta);
            cleanup.push(ta);
            ta.focus({ preventScroll: true });
            ta.select();
            ta.setSelectionRange(0, ta.value.length);
            return !!tryExec();
        } catch (_) {
            return false;
        } finally {
            try {
                const sel = window.getSelection();
                if (sel) sel.removeAllRanges();
            } catch (_) {}
            cleanup.forEach((el) => {
                try { el.remove(); } catch (_) {}
            });
        }
    }

    function copyMessageToClipboard(btn) {
        const text = getMessageCopyText(btn);
        if (!text) return Promise.resolve(false);
        // navigator.clipboard requires a secure context (HTTPS/localhost).
        // LAN phone access via http://192.168.x.x:8000 is insecure → must fallback.
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function' && window.isSecureContext) {
            return navigator.clipboard.writeText(text).then(() => true).catch(() => copyTextFallback(text));
        }
        return Promise.resolve(copyTextFallback(text));
    }
    
    function addTypingIndicator(slashMeta) {
        const messagesContainer = document.getElementById('chatMessages');
        if (!messagesContainer) return;
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message assistant';
        typingDiv.id = 'typing-indicator';
        const pendingChip = (() => {
            const chips = slashMeta && slashMeta.chips
                ? collapseCursorSlashChips(slashMeta.chips)
                : [];
            return messageHeaderBadgesHtml(chips, false, currentProject);
        })();
        
        typingDiv.innerHTML = `
            <div class="message-avatar">${assistantAvatarInnerHtml()}</div>
            <div class="message-content-wrapper">
                <div class="message-header-row">
                    <span class="message-sender-group">
                        <span class="message-sender">${assistantDisplayName()}</span>
                        ${pendingChip}
                    </span>
                </div>
                <div class="message-content">
                    <div class="typing-indicator" aria-hidden="true">
                        <div class="typing-orbit">
                            <span class="typing-orbit-ring"></span>
                            <span class="typing-orbit-ring typing-orbit-ring--inner"></span>
                            <span class="typing-orbit-core"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--1"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--2"></span>
                            <span class="typing-orbit-sat typing-orbit-sat--3"></span>
                            <span class="typing-orbit-beam"></span>
                        </div>
                        <div class="typing-wave">
                            <i></i><i></i><i></i><i></i><i></i><i></i><i></i>
                        </div>
                    </div>
                    <div class="typing-status" id="typing-status" aria-live="polite" role="status">Connecting...</div>
                </div>
                ${getLiveQueryLogFooterHtml()}
            </div>
        `;
        
        messagesContainer.appendChild(typingDiv);
        scheduleSlashChipCompactLabels(typingDiv);
        transcriptNav.updateMessageNav();
        transcriptNav.autoScrollChatToBottom();
        try { subagentLaunchers.paintLive(); } catch (_) {}
        armConnectingWatchdog();
        // Keep tool/status text moving even when the SSE body stalls (Electron HTTPS).
        armStreamStatusPoll();
    }

    /** If status never leaves the default "Connecting...", nudge the label and probe /api/status. */
    let connectingWatchdogTimer = null;
    let streamStatusPollTimer = null;
    function clearConnectingWatchdog() {
        if (connectingWatchdogTimer) {
            clearTimeout(connectingWatchdogTimer);
            connectingWatchdogTimer = null;
        }
    }
    function clearStreamStatusPoll() {
        if (streamStatusPollTimer) {
            clearInterval(streamStatusPollTimer);
            streamStatusPollTimer = null;
        }
    }
    function armStreamStatusPoll() {
        clearStreamStatusPoll();
        // App shell batches live-status for all panes — don't open a per-pane
        // poll that competes for the shared HTTPS connection pool.
        if (inAppShell) {
            reportSessionToShell(session.current());
            return;
        }
        // SSE can stall on Electron/HTTPS while the agent keeps updating live-status.
        // Poll with short timeouts so typing text still moves and connections free up.
        // Cadence, the tick branch decision and the {lastStatus, stagnantTicks}
        // bookkeeping are owned by chat_stream_transport.js; the page keeps the
        // timer, the live-status fetch, DOM paint and the recovery effects.
        const pollState = CuttleChatStreamTransport.createStatusPollState();
        streamStatusPollTimer = setInterval(async () => {
            const gate = CuttleChatStreamTransport.planStatusPollStart({
                loadingThisSession: isLoadingThisSession(),
                sessionId: session.current(),
                backgrounded: pageIsBackgrounded(),
            });
            if (gate.stop) {
                clearStreamStatusPoll();
                return;
            }
            if (gate.skip) return;
            const live = await fetchChatLiveStatus(session.current());
            const plan = CuttleChatStreamTransport.planStatusPollLive(
                pollState, live, liveStatusLooksActive(live));
            if (plan.skip) return;
            if (plan.paintStatus != null) updateTypingStatus(plan.paintStatus);
            try { subagentLaunchers.syncLive(plan.subagents); } catch (_) {}
            if (plan.reportUrl) updateTypingIndicatorQueryLink(plan.reportUrl);

            if (plan.recover) {
                // Run finished server-side — pull the reply even if SSE/pending stalled.
                clearStreamStatusPoll();
                try {
                    window.CuttleNetDebug && window.CuttleNetDebug.event(
                        'status-poll-idle',
                        'recovering session=' + session.current()
                    );
                } catch (_) {}
                await syncSessionMessagesFromServer();
                if (isLoadingThisSession()) {
                    const recovered = await recoveryController().recoverChatResultFromServer(session.current());
                    if (recovered && String(recovered.response || '').trim()) {
                        if (consumeCancelledAgentReply(recovered.response)) {
                            finishLocalStreamFromServerSync();
                        } else if (!turnAlreadyShowsAssistantReply(recovered.response)) {
                            addMessageToUI(recovered.response, 'assistant', {
                                report_url: recovered.report_url,
                                query_id: recovered.query_id,
                                timestamp: Date.now(),
                                routing_badge: recovered.routing_badge || undefined,
                                usage: normalizeUsagePayload(recovered.usage) || undefined,
                            });
                            notifyAssistantResponseReady(session.current(), { fromSync: true });
                            finishLocalStreamFromServerSync();
                        } else {
                            finishLocalStreamFromServerSync();
                        }
                    }
                    // No this-turn assistant yet — keep the local stream/pending
                    // collector alive instead of aborting into "No response received."
                }
                return;
            }
            // Same tool status for a long time while still "active" — nudge pending
            // recovery in case the worker died without clearing live-status.
            if (plan.resync) {
                try {
                    await syncSessionMessagesFromServer();
                } catch (_) {}
            }
        }, CuttleChatStreamTransport.STATUS_POLL_MS);
    }
    function armConnectingWatchdog() {
        clearConnectingWatchdog();
        connectingWatchdogTimer = setTimeout(async () => {
            connectingWatchdogTimer = null;
            const el = document.getElementById('typing-status')
                || document.querySelector('#typing-indicator .typing-status');
            if (!el) return;
            // Label gate, live-status preemption, probe interpretation and
            // pool-stall target resolution are owned by
            // chat_stream_transport.js; the page keeps the timer, DOM reads,
            // fetch, navigation and message paint.
            if (!CuttleChatStreamTransport.watchdogWantsLive(el.textContent)) return;

            // Prefer live generation status over a health probe (SSE may be stalled).
            if (session.current() != null) {
                const live = await fetchChatLiveStatus(session.current());
                const livePlan = CuttleChatStreamTransport.planWatchdogAfterLive(live);
                if (livePlan.kind === 'live') {
                    updateTypingStatus(livePlan.status);
                    if (livePlan.reportUrl) updateTypingIndicatorQueryLink(livePlan.reportUrl);
                    return;
                }
            }

            el.textContent = 'Still connecting — checking server…';
            try {
                const r = await fetchWithTimeout('/api/status', {
                    credentials: 'include',
                    cache: 'no-store',
                }, CuttleChatStreamTransport.WATCHDOG_PROBE_MS);
                const cur = document.getElementById('typing-status')
                    || document.querySelector('#typing-indicator .typing-status');
                const probe = CuttleChatStreamTransport.planWatchdogProbe({
                    ok: r.ok,
                    status: r.status,
                    hasIndicator: !!document.getElementById('typing-indicator'),
                    stillConnecting: !!cur && (cur.textContent || '').indexOf('Still connecting') >= 0,
                });
                if (probe.abort) return;
                cur.textContent = probe.message;
            } catch (_) {
                if (!document.getElementById('typing-indicator')) return;
                const cur = document.getElementById('typing-status')
                    || document.querySelector('#typing-indicator .typing-status');
                if (cur && (cur.textContent || '').indexOf('Still connecting') >= 0) {
                    // HTTPS pool stall (Chromium's ~6-conn limit): switch to
                    // the plain-HTTP companion only when its endpoint is
                    // known — poolStallHttpTarget never guesses an unrelated
                    // default for custom/explicit origins.
                    const inElectron = !!(window.electron && window.electron.isElectron);
                    const stallTarget = await CuttleChatStreamTransport.poolStallHttpTarget({
                        origin: window.location.origin || '',
                        getDesktopConfig: inElectron && window.electron.desktop && window.electron.desktop.getConfig
                            ? () => window.electron.desktop.getConfig()
                            : null,
                    });
                    if (stallTarget) {
                        cur.textContent = CuttleChatStreamTransport.poolStallSwitchMessage(stallTarget.httpPort);
                        try {
                            const probe = await fetchWithTimeout(stallTarget.probeUrl, {
                                cache: 'no-store',
                                mode: 'cors',
                            }, CuttleChatStreamTransport.POOL_STALL_PROBE_MS);
                            if (probe && probe.ok) {
                                const dest = stallTarget.destBase + (window.location.pathname || '/app_shell.html')
                                    + (window.location.search || '') + (window.location.hash || '');
                                try {
                                    if (window.top && window.top !== window) {
                                        window.top.location.href = dest.replace(/\/chat_page\.html.*/, '/app_shell.html');
                                    } else {
                                        window.location.href = dest;
                                    }
                                    return;
                                } catch (_) { /* fall through */ }
                            }
                        } catch (_) { /* fall through */ }
                    }
                    cur.textContent = CuttleChatStreamTransport.watchdogStallMessage(inElectron);
                }
            }
        }, CuttleChatStreamTransport.WATCHDOG_MS);
    }

    function updateTypingStatus(statusText) {
        const indicator = document.getElementById('typing-indicator');
        const el = indicator ? indicator.querySelector('.typing-status') : document.getElementById('typing-status');
        const plan = CuttleChatStreamTransport.planTypingStatusUpdate({
            statusText: statusText,
            hasIndicator: !!indicator,
            currentText: el ? el.textContent : null,
        });
        chatVoice.onAgentStatus(plan.text);
        if (plan.paint && el) {
            el.textContent = plan.text;
            el.style.display = 'block';
            el.style.visibility = 'visible';
            transcriptNav.autoScrollChatToBottom();
        }
        if (plan.clearWatchdog) clearConnectingWatchdog();
        if (plan.armWatchdog) armConnectingWatchdog();
    }
    
    function updateTypingIndicatorQueryLink(reportUrl) {
        const indicator = document.getElementById('typing-indicator');
        const link = indicator?.querySelector('.message-query-log-link');
        if (link && reportUrl) {
            applyQueryLogTarget(link, reportUrl);
        }
    }
    
    /** Finalize the live placeholder before Stop tears down its transports. */
    function retainStoppedAgentBubble() {
        clearConnectingWatchdog();
        clearStreamStatusPoll();
        document.querySelectorAll('#typing-indicator, #typing-indicator-remote').forEach((bubble) => {
            // Release live ids so later cleanup cannot remove the stopped activity.
            bubble.removeAttribute('id');
            bubble.dataset.stopped = 'true';
            bubble.removeAttribute('data-message-index');
            bubble.removeAttribute('data-pin-key');
            bubble.classList.remove('is-pinned-message');
            bubble.dataset.rawContent = 'Agent stopped.';
            bubble.querySelector('.typing-indicator')?.remove();
            const status = bubble.querySelector('.typing-status');
            if (status) {
                status.removeAttribute('id');
                status.textContent = 'Agent stopped.';
            }
            bubble.querySelectorAll('.slash-command-chip--header').forEach((chip) => {
                chip.classList.add('slash-command-chip--error');
                chip.setAttribute('aria-label', 'Stopped command: ' + (chip.dataset.fullLabel || chip.textContent));
            });
            const link = bubble.querySelector('.message-query-log-link');
            if (link) {
                // Stop can beat query-id creation: retain a usable index link.
                link.removeAttribute('data-query-pending');
                link.removeAttribute('aria-disabled');
                link.classList.remove('is-pending');
                link.title = link.dataset.queryId ? 'View query log' : 'View query log index';
            }
        });
    }

    function removeTypingIndicator() {
        clearConnectingWatchdog();
        clearStreamStatusPoll();
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            indicator.remove();
            transcriptNav.autoScrollChatToBottom();
        }
    }
    
    /** ``<cuttle_pricing>`` JSON → table; owned by CuttleChatMessages. */
    function renderCuttlePricingHtml(rawBody) {
        return CuttleChatMessages.renderCuttlePricingHtml(rawBody, { escapeHtmlInline });
    }


    /** Page-owned seams for structured-block render planning (chat_messages.js). */
    function structuredRenderDeps() {
        return {
            escapeHtmlInline,
            clamp,
            renderMdLinkChip,
            renderCuttlePricingHtml,
            mediaKindFromUrl: CuttleChatMedia.mediaKindFromUrl,
            buildMediaThumbHtml: chatMedia.buildMediaThumbHtml,
        };
    }

    function ingestAndStripCuttleWidgets(text) {
        return String(text || '').replace(
            /<cuttle_widget\b([^>]*)>([\s\S]*?)<\/cuttle_widget\s*>/gi,
            ''
        );
    }

    function formatMessage(text) {
        const live = CuttleUsageLive.render(text, formatMessage);
        if (live !== null) return live;
        // Historical Tasks tags are presentation only; Gizmos API owns writes.
        if (typeof text === 'string' && /<cuttle_widget\b/i.test(text)) {
            text = ingestAndStripCuttleWidgets(text);
        }
        // Extract <think> / <redacted_thinking> blocks (before escapeHtml)
        const headStructured = CuttleChatMessages.extractHeadStructuredBlocks(
            text, structuredRenderDeps());
        text = headStructured.text;
        const thinkBlocks = headStructured.blocks.think;
        const toolBlocks = headStructured.blocks.tool;

        // Supervised live activity indicator + Activity disclosure (collapsed).
        const supervisedActivityBlocks = [];
        text = text.replace(
            /<cuttle_supervised_activity>([\s\S]*?)<\/cuttle_supervised_activity>/gi,
            function (_, inner) {
                const CS = window.CuttleSupervised;
                let state = null;
                try {
                    state = JSON.parse(String(inner || '').trim());
                } catch (_) {
                    state = null;
                }
                const placeholder = '{{CUTTLE_SUP_ACT_' + supervisedActivityBlocks.length + '}}';
                if (CS && state && CS.buildActivityDisclosureHtml) {
                    const live = (!state.terminal && CS.buildLiveIndicatorHtml)
                        ? CS.buildLiveIndicatorHtml(state)
                        : '';
                    supervisedActivityBlocks.push(live + CS.buildActivityDisclosureHtml(state));
                } else {
                    supervisedActivityBlocks.push(
                        state && !state.terminal
                            ? '<div class="typing-status" aria-live="polite" role="status">'
                                + escapeHtml(state.status_text || state.phase_label || 'Working…')
                                + '</div>'
                            : ''
                    );
                }
                return placeholder;
            }
        );

        const tailStructured = CuttleChatMessages.extractTailStructuredBlocks(
            text, structuredRenderDeps());
        text = tailStructured.text;
        const structuredBlocks = Object.assign(
            { think: thinkBlocks, tool: toolBlocks }, tailStructured.blocks);

        // Extract <cuttle_button .../> blocks
        const buttonBlocks = [];
        const parseAttr = (attrsStr, attrName) => {
            const m = String(attrsStr ?? '').match(new RegExp(`\\b${attrName}\\s*=\\s*["']([^"']+)["']`, 'i'));
            return m ? m[1] : '';
        };

        // Dynamic action forms (choice / multi / form → allowlisted actions, no LLM).
        const actionFormBlocks = [];
        const extractBalancedJsonObject = (text, startIndex) => {
            const s = String(text || '');
            let i = startIndex;
            while (i < s.length && /\s/.test(s[i])) i += 1;
            if (s[i] !== '{') return null;
            let depth = 0;
            let inStr = false;
            let esc = false;
            for (let j = i; j < s.length; j++) {
                const c = s[j];
                if (inStr) {
                    if (esc) { esc = false; continue; }
                    if (c === '\\') { esc = true; continue; }
                    if (c === '"') inStr = false;
                    continue;
                }
                if (c === '"') { inStr = true; continue; }
                if (c === '{') depth += 1;
                else if (c === '}') {
                    depth -= 1;
                    if (depth === 0) {
                        try { return JSON.parse(s.slice(i, j + 1)); } catch (_) { return null; }
                    }
                }
            }
            return null;
        };
        // Card HTML/render planning is owned by chat_action_forms.js —
        // thin adapter. Session targeting arrives stamped in spec.session_id;
        // nested previews stay pre-rendered here (formatMessage sequencing).
        const renderActionFormCard = (spec, formId, fallback, lockedAttr, selectedAttr) => {
            const contentPreviewHtml = (spec && spec.content)
                ? `<div class="cuttle-action-form-preview">${formatMessage(String(spec.content))}</div>`
                : '';
            return CuttleChatActionForms.renderActionFormCardHtml({
                spec, formId, fallback, lockedAttr, selectedAttr,
                contentPreviewHtml, esc: escapeHtmlInline,
            });
        };

        const formOpenRe = /<cuttle_action_form\b[^>]*>\s*(\{)/gi;
        let formScan = '';
        let formLast = 0;
        let formOpenMatch;
        while ((formOpenMatch = formOpenRe.exec(text)) !== null) {
            const braceIndex = formOpenMatch.index + formOpenMatch[0].length - 1;
            const spec = extractBalancedJsonObject(text, braceIndex);
            const closeRe = /<\/cuttle_action_form\s*>/i;
            const closeSlice = text.slice(braceIndex);
            const closeM = closeRe.exec(closeSlice);
            if (!spec || !closeM) continue;
            const hasChoice = Array.isArray(spec.options) && spec.options.length > 0;
            const hasFields = Array.isArray(spec.fields) && spec.fields.length > 0;
            const mode = String(spec.mode || 'choice').toLowerCase();
            if (mode === 'form' ? !hasFields && !spec.submit && !spec.action : !hasChoice) {
                continue;
            }
            const closeEnd = braceIndex + closeM.index + closeM[0].length;
            formScan += text.slice(formLast, formOpenMatch.index);
            const placeholder = '{{CUTTLE_AF_' + actionFormBlocks.length + '}}';
            if (!spec.project_path && currentProject && currentProject.path) {
                spec.project_path = String(currentProject.path);
            }
            if (!spec.session_id && session.current() != null && session.current() !== '') {
                spec.session_id = String(session.current());
            }
            // Do not bake a huge inline. token into data-fallback — data-spec is
            // enough; tokenForCard rebuilds from data-spec when the pending id is gone.
            actionFormBlocks.push(renderActionFormCard(spec, '', '', '', ''));
            formScan += placeholder;
            formLast = closeEnd;
            formOpenRe.lastIndex = closeEnd;
        }
        if (actionFormBlocks.length) {
            formScan += text.slice(formLast);
            text = formScan;
        }

        text = text.replace(
            /<cuttle_action_form_pending\b([^>]*)>([\s\S]*?)<\/cuttle_action_form_pending>/gi,
            function (_, attrs, inner) {
                const formId = parseAttr(attrs, 'id') || '';
                // Ignore legacy fallback="inline.<base64 of entire spec>" — the
                // JSON body → data-spec rebuild is the restart-safe path.
                const fallback = '';
                const lockedAttr = parseAttr(attrs, 'locked') || '';
                const selectedAttr = parseAttr(attrs, 'selected') || '';
                const spec = safeJsonParse(String(inner ?? '').trim()) || {};
                const placeholder = '{{CUTTLE_AF_' + actionFormBlocks.length + '}}';
                actionFormBlocks.push(
                    renderActionFormCard(spec, formId, fallback, lockedAttr, selectedAttr)
                );
                return placeholder;
            }
        );

        // Legacy path: raw <cuttle_action_form> without pending rewrite (handled above).
        text = text.replace(
            /<cuttle_action_form\b[^>]*>[\s\S]*?<\/cuttle_action_form>/gi,
            function (full) {
                return actionFormBlocks.length ? '' : full;
            }
        );

        // Server-rewritten confirm cards (pending action id already registered).
        // Also accept unre-written <cuttle_confirm> as a preview; Confirm still
        // requires a server HMAC fallback or in-memory pending id.
        const buildConfirmCardHtml = (opts) => {
            const actionId = (opts.actionId || '').trim();
            const fallback = (opts.fallback || '').trim();
            const title = opts.title || 'Confirm';
            const confirmLabel = opts.confirmLabel || 'Confirm';
            const cancelLabel = opts.cancelLabel || 'Cancel';
            const bodyHtml = opts.bodyHtml || '';
            // Prefer inline payload (restart-safe). Keep pending id as first
            // token for logs; server prefers inline when both are present.
            let payload = '';
            if (actionId && fallback) payload = actionId + ' ' + fallback;
            else if (fallback) payload = fallback;
            else if (actionId) payload = actionId;
            return (
                `<div class="cuttle-confirm" data-action-id="${escapeHtmlInline(actionId)}">` +
                `<div class="cuttle-confirm-title">${escapeHtmlInline(title)}</div>` +
                `<div class="cuttle-confirm-body">${bodyHtml}</div>` +
                `<div class="cuttle-confirm-actions">` +
                `<button type="button" class="cuttle-button cuttle-button--primary" data-cuttle-button-id="project-action-confirm" data-cuttle-button-payload="${escapeHtmlInline(payload)}">${escapeHtmlInline(confirmLabel)}</button>` +
                `<button type="button" class="cuttle-button" data-cuttle-button-id="project-action-cancel" data-cuttle-button-payload="${escapeHtmlInline(payload)}">${escapeHtmlInline(cancelLabel)}</button>` +
                `</div></div>`
            );
        };
        text = text.replace(
            /<cuttle_confirm_pending\b([^>]*)>([\s\S]*?)<\/cuttle_confirm_pending>/gi,
            function (_, attrs, inner) {
                const actionId = parseAttr(attrs, 'id') || '';
                const fallback = parseAttr(attrs, 'fallback') || '';
                const title = parseAttr(attrs, 'title') || 'Confirm';
                const confirmLabel = parseAttr(attrs, 'confirm_label') || 'Confirm';
                const cancelLabel = parseAttr(attrs, 'cancel_label') || 'Cancel';
                const bodyHtml = formatMessage(String(inner ?? '').trim());
                const placeholder = '{{CUTTLE_BTN_' + buttonBlocks.length + '}}';
                buttonBlocks.push(
                    buildConfirmCardHtml({
                        actionId,
                        fallback,
                        title,
                        confirmLabel,
                        cancelLabel,
                        bodyHtml,
                    })
                );
                return placeholder;
            }
        );
        // Agent-emitted confirms (rewrite missed / old Flask) — show the card,
        // but do not mint unsigned inline tokens. Confirm needs a server rewrite.
        text = text.replace(
            /<cuttle_confirm(?!_pending)\b([^>]*)>([\s\S]*?)<\/cuttle_confirm>/gi,
            function (_, attrs, inner) {
                const title =
                    parseAttr(attrs, 'confirm_label')
                    || parseAttr(attrs, 'title')
                    || 'Confirm';
                const confirmLabel = parseAttr(attrs, 'confirm_label') || 'Confirm';
                const cancelLabel = parseAttr(attrs, 'cancel_label') || 'Cancel';
                const content = String(inner ?? '').trim();
                const bodyHtml = formatMessage(content);
                const placeholder = '{{CUTTLE_BTN_' + buttonBlocks.length + '}}';
                buttonBlocks.push(
                    buildConfirmCardHtml({
                        actionId: '',
                        fallback: '',
                        title,
                        confirmLabel,
                        cancelLabel,
                        bodyHtml,
                    })
                );
                return placeholder;
            }
        );
        text = text.replace(/<cuttle_button([^>]*?)\/>/g, function(_, attrs) {
            const id = parseAttr(attrs, 'id') || ('btn_' + buttonBlocks.length);
            const label = parseAttr(attrs, 'label') || 'Button';
            const payload = parseAttr(attrs, 'payload') || parseAttr(attrs, 'action');
            const placeholder = '{{CUTTLE_BTN_' + buttonBlocks.length + '}}';
            buttonBlocks.push(
                `<div class="ui-card">
                    <button type="button" class="cuttle-button" data-cuttle-button-id="${escapeHtmlInline(id)}" data-cuttle-button-payload="${escapeHtmlInline(payload)}">
                        ${escapeHtmlInline(label)}
                    </button>
                 </div>`
            );
            return placeholder;
        });
        text = text.replace(/<cuttle_button([^>]*?)>([\s\S]*?)<\/cuttle_button>/g, function(_, attrs, inner) {
            const id = parseAttr(attrs, 'id') || ('btn_' + buttonBlocks.length);
            const label = parseAttr(attrs, 'label') || String(inner ?? '').trim() || 'Button';
            const payload = parseAttr(attrs, 'payload') || parseAttr(attrs, 'action');
            const placeholder = '{{CUTTLE_BTN_' + buttonBlocks.length + '}}';
            buttonBlocks.push(
                `<div class="ui-card">
                    <button type="button" class="cuttle-button" data-cuttle-button-id="${escapeHtmlInline(id)}" data-cuttle-button-payload="${escapeHtmlInline(payload)}">
                        ${escapeHtmlInline(label)}
                    </button>
                 </div>`
            );
            return placeholder;
        });

        // Extract <cuttle_form>...</cuttle_form> JSON schema
        const formBlocks = [];
        text = text.replace(/<cuttle_form>([\s\S]*?)<\/cuttle_form>/g, function(_, content) {
            const raw = String(content ?? '').trim();
            const formSpec = safeJsonParse(raw);
            if (!formSpec) return '';

            const id = escapeHtmlInline(formSpec.id || ('form_' + formBlocks.length));
            const title = escapeHtmlInline(formSpec.title || 'Please choose an option');
            const description = escapeHtmlInline(formSpec.description || '');
            const submitLabel = escapeHtmlInline(formSpec.submitLabel || 'Submit');
            const busyLabel = escapeHtmlInline(formSpec.busyLabel || 'Sending…');
            const fields = Array.isArray(formSpec.fields) ? formSpec.fields : [];

            const renderField = (f, idx) => {
                const fid = escapeHtmlInline(f.id || ('field_' + idx));
                const label = escapeHtmlInline(f.label || fid);
                const type = String(f.type || 'text').toLowerCase();
                const help = escapeHtmlInline(f.help || '');
                const required = !!f.required;
                const placeholder = escapeHtmlInline(f.placeholder || '');
                const value = f.value ?? f.default ?? '';

                if (type === 'textarea') {
                    return `<div class="form-field">
                        <label class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</label>
                        <textarea class="form-textarea" data-field-id="${fid}" ${required ? 'required' : ''} placeholder="${placeholder}">${escapeHtmlInline(value)}</textarea>
                        ${help ? `<div class="form-help">${help}</div>` : ''}
                    </div>`;
                }

                if (type === 'select') {
                    const opts = Array.isArray(f.options) ? f.options : [];
                    const optionsHtml = opts.map((o) => {
                        const ov = escapeHtmlInline(o.value ?? o.id ?? o);
                        const ol = escapeHtmlInline(o.label ?? o.name ?? o.value ?? o);
                        const selected = String(value) === String(o.value ?? o.id ?? o) ? 'selected' : '';
                        return `<option value="${ov}" ${selected}>${ol}</option>`;
                    }).join('');
                    return `<div class="form-field">
                        <label class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</label>
                        <select class="form-select" data-field-id="${fid}" ${required ? 'required' : ''}>
                            ${optionsHtml}
                        </select>
                        ${help ? `<div class="form-help">${help}</div>` : ''}
                    </div>`;
                }

                if (type === 'radio') {
                    const opts = Array.isArray(f.options) ? f.options : [];
                    const groupName = `radio_${id}_${fid}`;
                    const radios = opts.map((o, i) => {
                        const ov = escapeHtmlInline(o.value ?? o.id ?? o);
                        const ol = escapeHtmlInline(o.label ?? o.name ?? o.value ?? o);
                        const checked = String(value) === String(o.value ?? o.id ?? o) ? 'checked' : '';
                        return `<label class="radio-item">
                            <input type="radio" name="${groupName}" value="${ov}" ${checked}>
                            <span>${ol}</span>
                        </label>`;
                    }).join('');
                    return `<div class="form-field">
                        <div class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</div>
                        <div class="radio-group" data-radio-group="${fid}">
                            ${radios}
                        </div>
                        ${help ? `<div class="form-help">${help}</div>` : ''}
                    </div>`;
                }

                if (type === 'checkbox') {
                    const checked = !!(value === true || value === 'true' || value === 1);
                    return `<div class="form-field">
                        <label class="check-item">
                            <input type="checkbox" data-field-id="${fid}" ${checked ? 'checked' : ''}>
                            <span>${label}${required ? ' <span class="req">*</span>' : ''}</span>
                        </label>
                        ${help ? `<div class="form-help">${help}</div>` : ''}
                    </div>`;
                }

                if (type === 'toggle') {
                    const checked = !!(value === true || value === 'true' || value === 1);
                    return `<div class="form-field form-field-inline">
                        <div>
                            <div class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</div>
                            ${help ? `<div class="form-help">${help}</div>` : ''}
                        </div>
                        <label class="toggle">
                            <input type="checkbox" data-field-id="${fid}" ${checked ? 'checked' : ''}>
                            <span class="toggle-track"><span class="toggle-thumb"></span></span>
                        </label>
                    </div>`;
                }

                if (type === 'slider') {
                    const min = Number.isFinite(Number(f.min)) ? Number(f.min) : 0;
                    const max = Number.isFinite(Number(f.max)) ? Number(f.max) : 100;
                    const step = Number.isFinite(Number(f.step)) ? Number(f.step) : 1;
                    const v = clamp(value === '' ? min : value, min, max);
                    return `<div class="form-field">
                        <label class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</label>
                        <div class="slider-row">
                            <input type="range" min="${min}" max="${max}" step="${step}" value="${v}" data-field-id="${fid}">
                            <span class="slider-value" data-slider-value-for="${fid}">${escapeHtmlInline(v)}</span>
                        </div>
                        ${help ? `<div class="form-help">${help}</div>` : ''}
                    </div>`;
                }

                // Default: text/number
                const inputType = (type === 'number') ? 'number' : 'text';
                const v = escapeHtmlInline(value);
                return `<div class="form-field">
                    <label class="form-label">${label}${required ? '<span class="req">*</span>' : ''}</label>
                    <input class="form-input" type="${inputType}" data-field-id="${fid}" ${required ? 'required' : ''} placeholder="${placeholder}" value="${v}">
                    ${help ? `<div class="form-help">${help}</div>` : ''}
                </div>`;
            };

            const body = fields.map(renderField).join('');
            const placeholder = '{{CUTTLE_FORM_' + formBlocks.length + '}}';
            formBlocks.push(
                `<div class="ui-card">
                    <div class="ui-title">${title}</div>
                    ${description ? `<div class="form-desc">${description}</div>` : ''}
                    <form class="cuttle-form" data-form-id="${id}">
                        ${body}
                        <div class="form-actions">
                            <button type="submit" class="form-submit" data-label="${submitLabel}" data-busy-label="${busyLabel}">${submitLabel}</button>
                        </div>
                    </form>
                </div>`
            );
            return placeholder;
        });

        const codeLinkStructured = CuttleChatMessages.extractCodeLinkBlocks(text, {
            escapeHtmlInline,
            renderMdLinkChip,
            isSafeMdHref,
            buildMediaThumbHtml: chatMedia.buildMediaThumbHtml,
        });
        text = codeLinkStructured.text;
        // Live array: handle/git linking below pushes chips the restore
        // protocol later reads through structuredBlocks.
        const linkChips = codeLinkStructured.blocks.link;
        Object.assign(structuredBlocks, codeLinkStructured.blocks);

        text = linkifyChatHandlesInText(text, linkChips);
        // Bare / backticked git SHAs → in-pane commit modal (same as Git page).
        if (window.CuttleGitCommitViewer && typeof window.CuttleGitCommitViewer.linkifyGitHashesInText === 'function') {
            text = window.CuttleGitCommitViewer.linkifyGitHashesInText(text, linkChips);
        }

        // Escape HTML
        text = escapeHtml(text);
        
        // Split into lines for processing
        // Block markdown (headers, tables, lists, quotes, paragraphs;
        // owned by chat_markdown.js). Input is already escaped.
        const blockHtml = CuttleChatMarkdown.renderMarkdownBlocks(text);
        let result = blockHtml;
        // Code/link chips restore after forms/buttons exactly as before: a
        // literal {{CUTTLE_FORM_0}}-style token inside a fenced block must not
        // be substituted, and vice versa. structuredBlocks stays the live
        // owner; only this pass order is pinned here.
        result = CuttleChatMessages.restoreStructuredBlocks(
            result, Object.assign({}, structuredBlocks, { code: [], link: [] }));
        // Restore supervised Activity disclosure
        for (let i = 0; i < supervisedActivityBlocks.length; i++) {
            result = result.split('{{CUTTLE_SUP_ACT_' + i + '}}').join(supervisedActivityBlocks[i]);
        }
        for (let i = 0; i < formBlocks.length; i++) {
            result = result.split('{{CUTTLE_FORM_' + i + '}}').join(formBlocks[i]);
        }
        for (let i = 0; i < actionFormBlocks.length; i++) {
            result = result.split('{{CUTTLE_AF_' + i + '}}').join(actionFormBlocks[i]);
        }
        for (let i = 0; i < buttonBlocks.length; i++) {
            result = result.split('{{CUTTLE_BTN_' + i + '}}').join(buttonBlocks[i]);
        }
        for (let i = 0; i < structuredBlocks.code.length; i++) {
            result = result.split('{{CUTTLE_CODE_' + i + '}}').join(structuredBlocks.code[i]);
        }
        for (let i = 0; i < structuredBlocks.link.length; i++) {
            result = result.split('{{CUTTLE_LINK_' + i + '}}').join(structuredBlocks.link[i]);
        }
        return result;
    }
    

    // Input Handling
    // Touch keyboards have no Shift+Enter, so Enter must stay a newline there; Send button sends.
    const composerTouchMq = window.matchMedia ? window.matchMedia('(hover: none) and (pointer: coarse)') : null;
    function isTouchComposer() {
        return !!(composerTouchMq && composerTouchMq.matches) || /CuttleMobile/.test(navigator.userAgent || '');
    }
    // Owned by chat_composer.js — DOM/state gather, domain decides.
    function composerEnterSends(event) {
        return CuttleChatComposer.enterSubmits({
            key: event && event.key,
            shiftKey: !!(event && event.shiftKey),
            ctrlKey: !!(event && event.ctrlKey),
            metaKey: !!(event && event.metaKey),
            touchMode: isTouchComposer(),
        });
    }
    function syncComposerEnterKeyHint() {
        const hint = isTouchComposer() ? 'enter' : 'send';
        ['welcomeChatInput', 'chatInput'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.setAttribute('enterkeyhint', hint);
        });
    }

    function handleInputKeyDown(event) {
        const input = document.getElementById('chatInput');
        if (input && handleSlashKeyDown(event, input)) {
            event.stopPropagation();
            return;
        }
        if (input && handlePromptHistoryKeyDown(event, input)) {
            event.stopPropagation();
            return;
        }
        if (composerEnterSends(event)) {
            LOG('handleInputKeyDown: Enter pressed, preventing default and sending');
            event.preventDefault();
            sendMessage();
        }
    }
    
    function wireComposerEmoticons(textarea) {
        if (!textarea || !window.CuttleEmoticons || textarea.dataset.emoticonWired === '1') return;
        textarea.dataset.emoticonWired = '1';
        textarea.addEventListener('compositionstart', function () {
            textarea.dataset.emoticonComposing = '1';
        });
        textarea.addEventListener('compositionend', function () {
            textarea.dataset.emoticonComposing = '0';
            // Composition commits as typed input — convert only that path.
            window.CuttleEmoticons.applyToTextarea(textarea);
        });
    }

    function applyComposerEmoticons(textarea, inputEvent) {
        if (!textarea || !window.CuttleEmoticons) return;
        // Only convert intentional typing — never paste, drop, undo, or history recall.
        if (inputEvent) {
            const t = inputEvent.inputType || '';
            if (t && t !== 'insertText' && t !== 'insertCompositionText') return;
        }
        window.CuttleEmoticons.applyToTextarea(textarea);
    }

    function autoResizeTextarea() {
        const textarea = document.getElementById('chatInput');
        if (textarea) {
            textarea.style.height = 'auto';
            const next = Math.min(textarea.scrollHeight, 168);
            textarea.style.height = next + 'px';
            textarea.style.overflowY = textarea.scrollHeight > 168 ? 'auto' : 'hidden';
        }
    }
    
    // Utility Functions
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // Initialize on page load
    document.addEventListener('DOMContentLoaded', function() {
        LOG('DOMContentLoaded: Chat page initializing');
        CuttleUsageLive.start(formatMessage);

        // Initialize theme
        initializeTheme();

        // Chat TTS play-button prefs (Settings → Chat voice)
        chatTts.loadPrefs();

        // file:// chips → host OS default application
        bindMdLinkChipClicks();
        bindChatHandleLinkClicks();
        bindGitCommitLinkClicks();
        // Markdown / attachment image+video lightbox
        chatMedia.bind();
        
        // Load agent name from settings
        agentName = localStorage.getItem('agentName') || 'Cuttle';
        updateAgentNameDisplay();
        
        // Load projects
        loadProjects();

        window.addEventListener('cuttle-auth-changed', function(ev) {
            refreshUserMessageAvatars();
            // Auth settle used to auto-open the latest session and yank users
            // off the welcome splash a few seconds after load. Only refresh the
            // history list here — unless the URL already asked for a specific chat
            // (deep-link / second tab), which must wait for auth before loading.
            // Must not wipe an active history search (see reloadHistoryListKeepingSearch).
            //
            // Reload projects too: the first viewport often fetches
            // /api/projects before auth settles (empty list → no pin badge),
            // while later viewports load after auth and show it. Refreshing
            // here re-reconciles the chip so no refresh is required, and
            // sign-out/in correctly restores the pin state.
            if (typeof loadProjects === 'function') {
                loadProjects();
            } else {
                historyPanel.reloadKeepingSearch();
            }
            const urlParams = new URLSearchParams(window.location.search);
            const wanted = urlParams.get('chat') || urlParams.get('session');
            if (!wanted || !isAuthMode()) return;
            // The restore already gave up and handed the user a new chat — don't
            // yank them into the old one when auth finally settles.
            if (sessionBoot().isRestoreAbandoned()) return;
            // Only settle the deep link before a chat is open. Auth refreshes
            // fire repeatedly; once the user is reading a chat, re-reading the
            // URL could yank them into a different one mid-click.
            if (session.current() != null) {
                if (String(session.current()) === String(wanted) && !messageSyncTimer) {
                    startMessageSync();
                }
                return;
            }
            loadChatSession(wanted);
        });

        // Stop button: cancel server run + abort local stream
        const stopButton = document.getElementById('stopButton');
        if (stopButton) {
            stopButton.addEventListener('click', function() {
                stopGenerating();
            });
        }

        loadSlashPaletteSupplement();
        hydrateStarredSlashFromServer();
        hydrateStarredProjectFromServer();
        window.addEventListener('cuttle-vega-ready', function () {
            try {
                const box = document.getElementById('chatMessages');
                if (box) activateVegaEmbeds(box);
            } catch (_) {}
        });
        // If CDN already finished before this listener attached.
        if (window.vegaEmbed) {
            try {
                const box = document.getElementById('chatMessages');
                if (box) activateVegaEmbeds(box);
            } catch (_) {}
        }

        // Load chat history from localStorage
        historyPanel.load();
        
        // Landing on Chat always shows the welcome splash unless a session is
        // requested via ?chat= (or legacy ?session=). History / New Chat manage the rest.
        const urlParams = new URLSearchParams(window.location.search);
        const sessionParam = urlParams.get('chat') || urlParams.get('session');
        const msgParam = urlParams.get('msg');
        if (msgParam) transcriptNav.queueMessageJump(msgParam);
        // Also accept compact refs in hash: #CH-000102-9 or #msg-9
        try {
            const hash = String(window.location.hash || '').replace(/^#/, '');
            const fromHash = transcriptNav.parseMessageRef(hash);
            if (fromHash) {
                transcriptNav.queueMessageJump(fromHash.messageIndex);
                if (!sessionParam) {
                    // If only a hash ref was given, still open that chat.
                    waitForAuthReady().then(function () {
                        loadChatSession(fromHash.chatId);
                    });
                }
            } else {
                const mOnly = hash.match(/^msg-?(\d+)$/i);
                if (mOnly) transcriptNav.queueMessageJump(mOnly[1]);
            }
        } catch (_) {}

        if (sessionParam) {
            bootRestoreSession(sessionParam);
        } else if (!(window.location.hash || '').match(/^#CH-/i)) {
            showWelcomeSplash();
        }

        if (urlParams.get('history') === '1') {
            try {
                const clean = new URL(window.location.href);
                clean.searchParams.delete('history');
                history.replaceState(null, '', clean.pathname + clean.search + clean.hash);
            } catch (_) {}
            openChatHistoryPanel();
        }

        // Attach event listeners programmatically (reliable fallback if inline handlers fail)
        const welcomeInput = document.getElementById('welcomeChatInput');
        const chatInput = document.getElementById('chatInput');
        const welcomeSendBtn = document.getElementById('welcomeSendButton');
        const sendBtn = document.getElementById('sendButton');

        LOG('DOMContentLoaded: attaching listeners. welcomeInput=', !!welcomeInput, 'chatInput=', !!chatInput, 'welcomeSendBtn=', !!welcomeSendBtn, 'sendBtn=', !!sendBtn);
        [welcomeInput, chatInput].forEach((el) => {
            if (el && el.hasAttribute('readonly')) el.removeAttribute('readonly');
        });
        syncComposerEnterKeyHint();
        if (composerTouchMq && composerTouchMq.addEventListener) {
            composerTouchMq.addEventListener('change', syncComposerEnterKeyHint);
        }

        wireAttachControls();
        wireEnhanceControls();
        syncExperimentalComposerControls();
        window.addEventListener('focus', syncExperimentalComposerControls);

        // Direct listeners
        if (welcomeInput) {
            welcomeInput.addEventListener('keydown', handleWelcomeInputKeyDown);
            welcomeInput.addEventListener('input', function (e) {
                applyComposerEmoticons(welcomeInput, e);
                CuttlePromptHistory.resetBrowse(promptHistoryState);
                syncSlashMenuFromInput(welcomeInput);
                // Frame removal need not fire unload; save before a fast space switch.
                saveComposerDraft(session.current() || 'new');
            });
            wireComposerEmoticons(welcomeInput);
            LOG('DOMContentLoaded: welcome input keydown listener attached');
        } else {
            LOG_ERR('DOMContentLoaded: welcomeChatInput not found');
        }
        if (chatInput) {
            chatInput.addEventListener('keydown', handleInputKeyDown);
            chatInput.addEventListener('input', function (e) {
                applyComposerEmoticons(chatInput, e);
                CuttlePromptHistory.resetBrowse(promptHistoryState);
                syncSlashMenuFromInput(chatInput);
                // Frame removal need not fire unload; save before a fast space switch.
                saveComposerDraft(session.current() || 'new');
            });
            wireComposerEmoticons(chatInput);
            chatInput.style.height = 'auto';
            LOG('DOMContentLoaded: chat input keydown listener attached');
        } else {
            LOG_ERR('DOMContentLoaded: chatInput not found');
        }

        // Flush drafts on refresh / Electron restart / pane unload.
        const flushComposerDraft = () => {
            try { saveComposerDraft(session.current() || 'new'); } catch (_) {}
        };
        window.addEventListener('pagehide', flushComposerDraft);
        window.addEventListener('beforeunload', flushComposerDraft);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') flushComposerDraft();
        });
        if (welcomeSendBtn) {
            welcomeSendBtn.addEventListener('click', sendWelcomeMessage);
            welcomeSendBtn.setAttribute('type', 'button');
            LOG('DOMContentLoaded: welcome send button click listener attached');
        } else {
            LOG_ERR('DOMContentLoaded: welcomeSendButton not found');
        }
        if (sendBtn) {
            sendBtn.addEventListener('click', sendMessage);
            sendBtn.setAttribute('type', 'button');
            LOG('DOMContentLoaded: send button click listener attached');
        } else {
            LOG_ERR('DOMContentLoaded: sendButton not found');
        }

        // Event delegation fallback: catch keydown/click on document (handles iframe timing, dynamic DOM)
        // Fallback Enter-to-send when direct textarea listeners miss (e.g. timing). Skip if textarea already handled (slash menu or send).
        document.addEventListener('keydown', function delegatedKeydown(e) {
            const t = e.target;
            if (!t || (t.id !== 'welcomeChatInput' && t.id !== 'chatInput')) return;
            if (!composerEnterSends(e)) return;
            if (e.defaultPrevented) return;
            e.preventDefault();
            LOG('Delegated keydown: Enter on', t.id);
            if (t.id === 'welcomeChatInput') sendWelcomeMessage();
            else sendMessage();
        });
        document.addEventListener('click', function delegatedClick(e) {
            const btn = e.target.closest?.('#welcomeSendButton, #sendButton');
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            LOG('Delegated click: send button');
            if (btn.id === 'welcomeSendButton') sendWelcomeMessage();
            else sendMessage();
        }, true);
        LOG('DOMContentLoaded: event delegation fallback attached');

        document.addEventListener(
            'mousedown',
            function slashMenuOutside(e) {
                const t = e.target;
                if (t.closest?.('.slash-command-menu')) return;
                if (t.closest?.('.slash-chips-row')) return;
                if (t.closest?.('.textarea-with-slash-menu')) return;
                ['welcome', 'chat'].forEach((key) => {
                    const el = document.getElementById(slashMenuId(key));
                    const wasOpen = !!(el && !el.hidden);
                    hideSlashMenu(key, wasOpen ? { dismiss: true } : undefined);
                });
            },
            true
        );

        // Live-update message timestamps (coarse — "Just now" → minutes; no 1Hz paint)
        setInterval(function updateMessageTimestamps() {
            document.querySelectorAll('.message-timestamp[data-ts]').forEach(function(el) {
                const ts = parseInt(el.dataset.ts, 10);
                if (!ts) return;
                const ago = formatTimeAgo(ts);
                el.textContent = ago === 'now' ? 'Just now' : 'Sent ' + ago + ' ago';
            });
            refreshHistoryItemTimes();
        }, 60000);

        document.addEventListener('mousedown', function historyMenuOutside(e) {
            const t = e.target;
            if (t.closest?.('#historyItemOverflowMenu')) return;
            if (t.closest?.('.chat-menu-btn')) return;
            historyPanel.closeMenu();
        }, true);
        window.addEventListener('resize', function () { historyPanel.closeMenu(); });
        window.addEventListener('resize', () => scheduleSlashChipCompactLabels(document));
        if (typeof ResizeObserver === 'function') {
            try {
                const chipRo = new ResizeObserver(() => scheduleSlashChipCompactLabels(document));
                const chatMain = document.getElementById('chatMessages') || document.getElementById('messagesContainer');
                if (chatMain) chipRo.observe(chatMain);
                const hist = document.getElementById('chatHistory');
                if (hist) chipRo.observe(hist);
                ['welcomeSlashChipsRow', 'chatSlashChipsRow'].forEach((id) => {
                    const el = document.getElementById(id);
                    if (el) chipRo.observe(el);
                });
            } catch (_) {}
        }
        const historyScroll = document.getElementById('chatHistory');
        if (historyScroll) {
            historyScroll.addEventListener('scroll', function () { historyPanel.closeMenu(); }, { passive: true });
        }
        historyPanel.setupModals();
        gitPush.setupConfirmModal();

        // Cross-device: refresh open session when tab becomes visible / focused
        const syncIfVisible = function () {
            if (pageIsBackgrounded()) {
                syncHistoryUnreadIndicators();
                // Stretch the next tick so background iframes stop hammering Flask.
                if (messageSyncTimer) scheduleNextMessageSync();
                return;
            }
            // After sleep / Flask restart, stale running flags can trap follow-ups.
            healStaleGeneratingState();
            if (session.current() != null) {
                markChatSessionRead(session.current());
                syncHistoryUnreadIndicators();
            }
            syncSessionMessagesFromServer();
            if (messageSyncTimer) scheduleNextMessageSync();
        };
        document.addEventListener('visibilitychange', syncIfVisible);
        window.addEventListener('focus', syncIfVisible);
        try {
            if (window.parent && window.parent !== window) {
                window.parent.document.addEventListener('visibilitychange', syncIfVisible, {
                    signal: parentListenersAbort.signal,
                });
            }
        } catch (_) {}

        // App shell: one batch live-status poll + focus affinity across split panes.
        if (inAppShell) {
            window.addEventListener('message', function (e) {
                if (!e.data || typeof e.data !== 'object') return;
                if (e.data.type === 'cuttle-pane-teardown' || e.data.type === 'cuttle-pause-streams') {
                    releaseLocalStreamForShellPause(e.data.type === 'cuttle-pane-teardown');
                    return;
                }
                if (e.data.type === 'cuttle-pane-focus') {
                    const next = !!e.data.focused;
                    const changed = next !== shellPaneFocused;
                    shellPaneFocused = next;
                    if (messageSyncTimer) scheduleNextMessageSync();
                    if (changed && shellPaneFocused && session.current() != null) {
                        syncSessionMessagesFromServer();
                    }
                    // Hidden panes stop usage-live polling while away; rescan
                    // on focus so budgets refresh without an app reload.
                    if (shellPaneFocused) {
                        try {
                            if (window.CuttleUsageLive) window.CuttleUsageLive.wake();
                        } catch (_) {}
                    }
                    return;
                }
                if (e.data.type === 'cuttle-live-status') {
                    const sid = e.data.sessionId;
                    if (sid == null || session.current() == null) return;
                    if (!sessionIdsEqual(sid, session.current())) return;
                    applyHubLiveStatusPayload(e.data.status || null, sid);
                    return;
                }
                if (e.data.type === 'cuttle-flask-restart-linked') {
                    try { actionCardsController().handleExternalRestart(e.data); } catch (_) {}
                    return;
                }
                if (e.data.type === 'cuttle-sessions-snapshot') {
                    if (window.CuttleAuth && window.CuttleAuth.adoptSessions) window.CuttleAuth.adoptSessions(e.data.sessions);
                    applyGeneratingFlagsFromSessions(e.data.sessions || []);
                    syncHistoryUnreadIndicators();
                    return;
                }
                if (e.data.type === 'cuttle-open-panes') {
                    applyLocalOpenPanes(e.data.panes);
                    return;
                }
                if (e.data.type === 'cuttle-pending-changes-data') {
                    if (pendingChangesCtl) pendingChangesCtl.applyHubPayload(e.data.path, e.data.data);
                    return;
                }
                if (e.data.type === 'cuttle-pending-changes-changed') {
                    if (pendingChangesCtl) pendingChangesCtl.onInvalidated(e.data.path, e.data.data);
                }
            });
            document.addEventListener('pointerdown', function () {
                notifyShellPaneActivity();
            }, true);
            if (session.current() != null) reportSessionToShell(session.current());
            // Ask shell to re-broadcast focus (in case we loaded after click).
            try {
                window.parent.postMessage({ type: 'cuttle-pane-activity' }, '*');
                window.parent.postMessage({ type: 'cuttle-open-panes-request' }, '*');
            } catch (_) {}
            try { scheduleChatActivityBroadcast(); } catch (_) {}
        }

        initPendingChangesPanel();
        initGitCommitViewer();

        const jumpBottomBtn = document.getElementById('chatJumpBottom');
        if (jumpBottomBtn) {
            jumpBottomBtn.addEventListener('click', (e) => {
                e.preventDefault();
                transcriptNav.jumpToBottom(true);
            });
        }
        transcriptNav.setupMessageNavScrollSync();
        setupChatAttentionListeners();
        transcriptNav.updateJumpToBottomVisibility();
        updateSessionTitleUnreadDot();
        try { contextGauge.init(); } catch (e) { LOG_ERR('context gauge init', e); }

        LOG('DOMContentLoaded: init complete. In iframe=', window !== window.parent);
    });
    
    // Export functions to window
    window.toggleChatHistoryPanelCollapse = toggleChatHistoryPanelCollapse;
    window.toggleChatHistoryPanel = toggleChatHistoryPanel;
    window.openChatHistoryPanel = openChatHistoryPanel;
    window.closeChatHistoryPanel = closeChatHistoryPanel;
    window.toggleHistoryGroupExpand = (event) => historyPanel.revealMore(event); // back-compat alias
    window.revealMoreHistoryGroup = (event) => historyPanel.revealMore(event);
    window.collapseHistoryGroupReveal = (event) => historyPanel.collapseReveal(event);
    window.toggleHistoryProjectCollapse = (event) => historyPanel.toggleProjectCollapse(event);
    window.toggleHistorySubagentGroup = (sessionId, event) => historyPanel.toggleSubagentGroup(sessionId, event);
    window.toggleHistoryProjectCollapseKey = function (event) {
        if (!event) return;
        const key = event.key || event.code;
        if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar' && key !== 'Space') return;
        event.preventDefault();
        historyPanel.toggleProjectCollapse(event);
    };
    window.newChatForProject = newChatForProject;
    window.toggleTheme = toggleTheme;
    window.createNewChat = createNewChat;
    window.copyChatIdBadge = copyChatIdBadge;
    window.copyChatSessionId = copyChatSessionId;
    window.chatPageOpenTerminalFromMenu = openTerminalFromMenu;
    window.sendWelcomeMessage = sendWelcomeMessage;
    window.handleWelcomeInputKeyDown = handleWelcomeInputKeyDown;
    window.autoResizeWelcomeTextarea = autoResizeWelcomeTextarea;
    window.switchChatProject = switchChatProject;
    window.chatPageLoadSession = loadChatSession;
    window.loadChatSession = loadChatSession;
    window.chatPageNavigateToChatHandle = navigateToChatHandle;
    window.chatPageOpenTerminalSession = openTerminalSession;
    window.chatPageToggleArchiveSession = toggleArchiveChatSession;
    window.chatPageToggleArchiveSection = toggleArchiveSection;
    window.chatPageToggleArchiveSectionKey = toggleArchiveSectionKey;
    window.chatPageRequestDeleteSession = (sessionId, event) => historyPanel.requestDelete(sessionId, event);
    window.chatPageCancelDeleteSession = (sessionId, event) => historyPanel.cancelDelete(sessionId, event);
    window.chatPageConfirmDeleteSession = (sessionId, event) => historyPanel.confirmDelete(sessionId, event);
    window.chatPageDeleteSession = deleteChatSession;
    window.chatPageRenameSession = (sessionId, event) => historyPanel.renameSession(sessionId, event);
    window.chatPageToggleStarSession = toggleStarChatSession;
    window.chatPageToggleCurrentStar = toggleCurrentChatStar;
    window.chatPageToggleCurrentStarFromMenu = toggleCurrentChatStarFromMenu;
    window.chatPageMarkUnreadSession = markSessionUnreadManual;
    window.chatPageMarkCurrentUnreadFromMenu = markCurrentSessionUnreadFromMenu;

    window.chatPageExportSession = exportChatSession;
    window.chatPageGitPush = (event, repoRoot) => gitPush.push(event, repoRoot);

    document.addEventListener('click', function (e) {
        const t = e.target;
        if (!t || !t.closest) return;
        const repoBtn = t.closest('[data-git-push-repo]');
        if (repoBtn) {
            e.preventDefault();
            e.stopPropagation();
            let root = '';
            try {
                root = decodeURIComponent(repoBtn.getAttribute('data-git-push-repo') || '');
            } catch (_) {
                root = repoBtn.getAttribute('data-git-push-repo') || '';
            }
            gitPush.push(e, root || null);
            return;
        }
        const trigger = t.closest('[data-git-push-trigger]');
        if (trigger) {
            e.preventDefault();
            e.stopPropagation();
            gitPush.openSubmenu(trigger);
        }
    }, true);

    window.chatPageToggleHistoryMenu = (sessionId, event) => historyPanel.toggleMenu(sessionId, event);
    window.searchChats = (opts) => historyPanel.search(opts);
    window.onHistorySearchInput = (ev) => historyPanel.onInput(ev);
    window.clearHistorySearch = () => historyPanel.clear();
    window.onHistorySearchKeydown = (ev) => historyPanel.onKeydown(ev);
    window.refreshChatHistoryList = refreshChatHistoryList;
    window.sendMessage = sendMessage;
    window.handleInputKeyDown = handleInputKeyDown;
    window.autoResizeTextarea = autoResizeTextarea;
    window.chatPageJumpToMessage = transcriptNav.jumpToMessage;
    window.chatPageJumpToBottom = transcriptNav.jumpToBottom;
    window.chatPageCopyMessageRef = async function(btn) {
        const { ok, ref } = await transcriptNav.copyMessageRefToClipboard(btn);
        if (ok && ref) {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
            btn.title = 'Copied ' + ref;
            (window.showToast || function () {})('Copied ' + ref, 'success');
            setTimeout(() => {
                btn.innerHTML = SHARE_REF_ICON;
                btn.title = 'Copy message ref (' + ref + ')';
            }, 1500);
        } else {
            btn.title = 'Could not copy message ref';
            (window.showToast || function () {})('Could not copy message ref', 'error');
            setTimeout(() => {
                btn.innerHTML = SHARE_REF_ICON;
                btn.title = 'Copy message ref (CH-000000-N)';
            }, 2000);
        }
    };
    window.chatPageTurnFeedback = async function(btn) {
        const qid = btn && btn.getAttribute('data-query-id');
        const value = btn && btn.getAttribute('data-feedback');
        if (!qid || !value || session.current() == null) return;
        const group = btn.closest('.message-footer-icons');
        const buttons = group ? Array.from(group.querySelectorAll('.message-feedback-btn')) : [btn];
        const active = buttons.find((b) => b.classList.contains('is-on'));
        const prev = active ? active.getAttribute('data-feedback') : null;
        const next = prev === value ? null : value;
        const paint = (fb) => buttons.forEach((b) => {
            const on = fb != null && b.getAttribute('data-feedback') === fb;
            b.classList.toggle('is-on', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        paint(next);
        try {
            const resp = await fetch('/api/turn-feedback', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: session.current(), query_id: qid, feedback: next }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error((data && data.error) || ('HTTP ' + resp.status));
        } catch (err) {
            paint(prev);
            (window.showToast || function () {})('Could not save feedback: ' + (err.message || err), 'error');
        }
    };
    window.chatPageSpeakMessage = function(btn) {
        return chatTts.speakFromButton(btn);
    };
    window.chatPageEnterVoiceMode = chatVoice.enter;
    window.chatPageExitVoiceMode = chatVoice.exit;
    window.chatPageCopyMessage = async function(btn) {
        const ok = await copyMessageToClipboard(btn);
        if (ok) {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
            btn.title = 'Copied!';
            setTimeout(() => { btn.innerHTML = COPY_ICON; btn.title = 'Copy to clipboard'; }, 1500);
        } else {
            // Last resort on locked-down mobile browsers: select the bubble text
            // so the user can use the native "Copy" action.
            try {
                const wrapper = btn.closest('.message-content-wrapper');
                const contentEl = wrapper?.querySelector('.message-content');
                if (contentEl) {
                    const range = document.createRange();
                    range.selectNodeContents(contentEl);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                }
            } catch (_) {}
            btn.title = 'Copy failed — text selected';
            (window.showToast || function () {})('Could not copy automatically — text is selected, use Copy', 'error');
            setTimeout(() => { btn.title = 'Copy to clipboard'; }, 2500);
        }
    };

    function resolveTerminalOpenPage() {
        try {
            const layout = JSON.parse(localStorage.getItem(TERMINAL_LAYOUT_KEY) || 'null');
            const active = layout && layout.active ? String(layout.active) : '';
            if (active) {
                return '/terminal_page.html?chat=' + encodeURIComponent(active);
            }
            if (layout && Array.isArray(layout.tabs) && layout.tabs.length && layout.tabs[0].id) {
                return '/terminal_page.html?chat=' + encodeURIComponent(String(layout.tabs[0].id));
            }
            const map = readTerminalSessionRegistry();
            const latest = Object.values(map)
                .filter((e) => e && e.id)
                .sort((a, b) => (b.updated || 0) - (a.updated || 0))[0];
            if (latest && latest.id) {
                return '/terminal_page.html?chat=' + encodeURIComponent(String(latest.id));
            }
        } catch (_) {}
        return '/terminal_page.html';
    }

    function openTerminalFromMenu(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        closeAllSessionMenus();
        const page = resolveTerminalOpenPage();
        const shell = window.parent;
        if (shell && shell !== window && typeof shell.navigateFromSource === 'function') {
            shell.navigateFromSource(window, page);
            return;
        }
        if (shell && shell !== window && typeof shell.navigate === 'function') {
            const colIdx = typeof shell.findColumnIndexForSource === 'function'
                ? shell.findColumnIndexForSource(window)
                : 0;
            shell.navigate(colIdx, page);
            return;
        }
        window.location.href = page;
    }

    const terminalOpenBtn = document.getElementById('terminalOpenBtn');
    if (terminalOpenBtn) {
        // Legacy static control — kept for compatibility if present.
        terminalOpenBtn.addEventListener('click', function(e) {
            openTerminalFromMenu(e);
        });
    }
    fetch('/api/terminal/status', { credentials: 'same-origin' })
        .then((resp) => {
            if (!resp.ok) {
                _terminalMenuAvailable = false;
                if (terminalOpenBtn) {
                    terminalOpenBtn.hidden = true;
                    terminalOpenBtn.setAttribute('aria-hidden', 'true');
                }
            }
        })
        .catch(() => {
            const host = (window.location.hostname || '').toLowerCase();
            const loopback = host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
            if (!loopback) {
                _terminalMenuAvailable = false;
                if (terminalOpenBtn) {
                    terminalOpenBtn.hidden = true;
                    terminalOpenBtn.setAttribute('aria-hidden', 'true');
                }
            }
        });

    function isChatChromeMenuOpen() {
        const dropdown = document.getElementById('chatChromeMenuDropdown');
        return !!(dropdown && !dropdown.hidden);
    }

    function closeChatChromeMenu() {
        const btn = document.getElementById('chatChromeMenuBtn');
        const dropdown = document.getElementById('chatChromeMenuDropdown');
        if (dropdown) dropdown.hidden = true;
        if (btn) btn.setAttribute('aria-expanded', 'false');
    }

    function fillChatChromeMenu() {
        const dropdown = document.getElementById('chatChromeMenuDropdown');
        if (!dropdown) return;
        const sid = session.current();
        const hasSession = sid != null && sid !== '';
        dropdown.innerHTML = buildSessionMenuHTML(hasSession ? sid : null, {
            includeRename: hasSession,
            includeDelete: hasSession,
        });
        gitPush.refreshMenuMode(dropdown);
    }

    function toggleChatChromeMenu(forceOpen) {
        const btn = document.getElementById('chatChromeMenuBtn');
        const dropdown = document.getElementById('chatChromeMenuDropdown');
        if (!btn || !dropdown) return;
        const open = forceOpen == null ? dropdown.hidden : !!forceOpen;
        if (open) {
            historyPanel.closeMenu();
            fillChatChromeMenu();
        }
        dropdown.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    const chatChromeMenuBtn = document.getElementById('chatChromeMenuBtn');
    if (chatChromeMenuBtn) {
        chatChromeMenuBtn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            toggleChatChromeMenu();
        });
    }

    document.addEventListener('click', function (e) {
        if (!isChatChromeMenuOpen()) return;
        const menu = document.getElementById('chatChromeMenu');
        if (menu && menu.contains(e.target)) return;
        closeChatChromeMenu();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isChatChromeMenuOpen()) {
            closeChatChromeMenu();
        }
    });

    // Expose for headless e2e verification (history nav + badge)
    try {
        window._e2eChat = {
            addMessageToUI,
            updateMessageNav: transcriptNav.updateMessageNav,
            autoScrollMessageNavToBottom: transcriptNav.autoScrollMessageNavToBottom,
            autoScrollChatToBottom: transcriptNav.autoScrollChatToBottom,
            isMessageNavScrolledUp: transcriptNav.isMessageNavScrolledUp,
            isMessageNavInteracting: transcriptNav.isMessageNavInteracting,
            syncMessageNavPinnedFromScroll: transcriptNav.syncMessageNavPinnedFromScroll,
            getNav: () => document.getElementById('messageNav'),
            getChat: () => document.getElementById('chatMessages'),
            getSlashCtx: () => slashCtx,
            getSupplement: () => slashPaletteSupplement
        };
    } catch (_) {}
    
})();
