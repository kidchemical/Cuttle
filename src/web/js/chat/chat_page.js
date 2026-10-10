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
        if (currentSessionId == null || currentSessionId === '') return;
        return toggleStarChatSession(currentSessionId, event);
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
        if (currentSessionId != null && sessionIdsEqual(currentSessionId, sessionId)) {
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
        if (currentSessionId == null || currentSessionId === '') return;
        return markSessionUnreadManual(currentSessionId, event);
    }

    let _gitPushInFlight = false;
    let _gitReposCache = { path: '', repos: [], at: 0 };

    function currentProjectPathKey() {
        // Git/branch scope: the bound worktree checkout, else the project path.
        return effectiveGitPath();
    }

    /** Sync known repos for the current project (cache or pending-changes panel). */
    function knownProjectGitRepos() {
        const path = currentProjectPathKey();
        if (!path) return null;
        const now = Date.now();
        if (
            _gitReposCache.path === path
            && (now - _gitReposCache.at) < 15000
            && Array.isArray(_gitReposCache.repos)
        ) {
            return _gitReposCache.repos;
        }
        if (pendingChangesCtl && typeof pendingChangesCtl.getRepos === 'function') {
            const fromPanel = pendingChangesCtl.getRepos() || [];
            if (fromPanel.length) {
                _gitReposCache = { path, repos: fromPanel, at: now };
                return fromPanel;
            }
        }
        return null;
    }

    async function fetchProjectGitRepos(force) {
        const path = currentProjectPathKey();
        if (!path) return [];
        if (!force) {
            const known = knownProjectGitRepos();
            if (known) return known;
        }
        try {
            const qs = new URLSearchParams();
            qs.set('path', path);
            const resp = await fetch('/api/git/repos?' + qs.toString(), {
                credentials: 'include',
                cache: 'no-store',
            });
            const data = await resp.json().catch(() => null);
            const repos = (data && data.success && Array.isArray(data.repos))
                ? data.repos
                : [];
            _gitReposCache = { path, repos, at: Date.now() };
            return repos;
        } catch (_) {
            return _gitReposCache.path === path ? (_gitReposCache.repos || []) : [];
        }
    }

    // Branch shown on the segmented project chip (star | name | branch).
    // Cache + fetch live in chat_chip_config.js (branchFor / refreshBranch).
    function currentProjectBranch() {
        return CuttleChipConfig.branchFor(currentProjectPathKey());
    }

    /** Refresh the project chip branch (cached ~15s). Re-renders when it changes. */
    function refreshProjectBranch(force) {
        return CuttleChipConfig.refreshBranch(currentProjectPathKey(), renderProjectChips, force);
    }

    async function pushCurrentChatProject(event, repoRoot) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        closeAllSessionMenus();
        const toast = window.showToast || function () {};
        if (_gitPushInFlight) {
            toast('Git push already in progress…', 'info');
            return;
        }
        const path = currentProject && currentProject.path
            ? String(currentProject.path)
            : '';
        const projectId = currentProject && currentProject.id != null
            ? currentProject.id
            : null;
        if (!path && projectId == null) {
            toast('No project selected', 'error');
            return;
        }

        const repos = await fetchProjectGitRepos(true);
        const wantRoot = repoRoot ? String(repoRoot).replace(/\\/g, '/').toLowerCase() : '';
        let info = null;
        if (wantRoot && Array.isArray(repos)) {
            info = repos.find(function (r) {
                const root = String((r && r.repo_root) || '').replace(/\\/g, '/').toLowerCase();
                return root === wantRoot;
            }) || null;
        }
        if (!info && Array.isArray(repos) && repos.length === 1) info = repos[0];
        const projectName = (currentProject && currentProject.name)
            ? String(currentProject.name)
            : 'this project';
        const repoName = (info && (info.repo || info.label))
            ? String(info.repo || info.label)
            : 'the remote';
        const remoteName = (info && info.remote) ? String(info.remote) : 'origin';
        const branchName = (info && info.branch) ? String(info.branch) : 'HEAD';
        const confirmMsg = 'Are you sure you want to push '
            + projectName
            + ' to '
            + repoName
            + ' '
            + remoteName
            + ' '
            + branchName
            + '?';
        const accepted = await confirmGitPushModal(confirmMsg);
        if (!accepted) return;

        _gitPushInFlight = true;
        const name = projectName;
        const repoLabel = repoRoot
            ? String(repoRoot).replace(/\\/g, '/').split('/').filter(Boolean).pop()
            : '';
        const closePushProgress = function () {
            try {
                if (typeof window.closeCuttleToast === 'function') window.closeCuttleToast('git-push');
            } catch (_) {}
        };
        toast('Pushing ' + (repoLabel || name) + '…', 'info', {
            toastId: 'git-push',
            sticky: true,
            progress: true,
            skipHistory: true
        });
        try {
            const body = {};
            if (path) body.path = path;
            if (projectId != null) body.project_id = projectId;
            if (repoRoot) body.repo_root = String(repoRoot);
            const resp = await fetch('/api/git/push', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) {
                if (data.push_report && window.CuttleGitPushReport) window.CuttleGitPushReport.show(data.push_report, {projectPath: path || data.repo_root, repoRoot: data.repo_root});
                let err = String(
                    (data && (data.error || data.message))
                    || ('Git push failed (' + resp.status + ')')
                ).trim();
                err = err.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)[0] || err;
                if (err.length > 220) err = err.slice(0, 217) + '…';
                closePushProgress();
                toast(err, 'error');
                return;
            }
            const branch = data.branch ? String(data.branch) : '';
            const remote = data.remote ? String(data.remote) : '';
            const output = String(data.output || '');
            let okMsg = data.message || 'Push successful';
            if (/everything up-to-date/i.test(output)) {
                okMsg = branch ? (branch + ' already up to date') : 'Already up to date';
            } else if (branch && remote) {
                okMsg = 'Pushed ' + branch + ' → ' + remote;
            } else if (branch) {
                okMsg = 'Pushed ' + branch;
            }
            // Single-repo projects don't need the repo prefix; the outcome
            // reuses the progress toast id so it swaps in place (one toast,
            // one history entry per push).
            if (repoLabel && Array.isArray(repos) && repos.length > 1) okMsg = repoLabel + ': ' + okMsg;
            closePushProgress();
            toast(okMsg, 'success', { toastId: 'git-push' });
        } catch (err) {
            closePushProgress();
            toast((err && err.message) || 'Git push failed', 'error');
        } finally {
            _gitPushInFlight = false;
        }
    }

    /** Caret / submenu only when the project has more than one git repo. */
    function gitPushIsMultiRepo(repos) {
        return Array.isArray(repos) && repos.length > 1;
    }

    function applyGitPushMenuMode(wrap, multi) {
        if (!wrap) return;
        const trigger = wrap.querySelector('[data-git-push-trigger]');
        if (!trigger) return;
        let caret = trigger.querySelector('.chat-chrome-menu-caret');
        if (multi) {
            wrap.setAttribute('data-git-push-multi', '1');
            trigger.setAttribute('aria-haspopup', 'true');
            if (!caret) {
                caret = document.createElement('span');
                caret.className = 'chat-chrome-menu-caret';
                caret.setAttribute('aria-hidden', 'true');
                caret.textContent = '▸';
                trigger.appendChild(caret);
            }
        } else {
            wrap.removeAttribute('data-git-push-multi');
            trigger.removeAttribute('aria-haspopup');
            trigger.setAttribute('aria-expanded', 'false');
            if (caret) caret.remove();
            const submenu = wrap.querySelector('[data-git-push-submenu]');
            if (submenu) {
                submenu.innerHTML = '';
                submenu.hidden = true;
            }
        }
    }

    function gitPushMenuHtml(icons) {
        const known = knownProjectGitRepos();
        const multi = gitPushIsMultiRepo(known);
        return (
            '<div class="chat-chrome-menu-item-wrap is-git-push" data-git-push-wrap="1"'
            + (multi ? ' data-git-push-multi="1"' : '')
            + '>'
            + '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
            + ' title="Git push" aria-label="Git push"'
            + (multi ? ' aria-haspopup="true" aria-expanded="false"' : '')
            + ' data-git-push-trigger="1"'
            + (_gitPushInFlight ? ' aria-disabled="true"' : '')
            + '>'
            + icons.gitPush
            + '<span class="chat-chrome-menu-item-label">Git push</span>'
            + (multi
                ? '<span class="chat-chrome-menu-caret" aria-hidden="true">▸</span>'
                : '')
            + '</button>'
            + '<div class="chat-chrome-menu-submenu" data-git-push-submenu="1" hidden role="menu"></div>'
            + '</div>'
        );
    }

    /** After menu HTML lands, refresh repo count and show/hide the caret. */
    function refreshGitPushMenuMode(rootEl) {
        const wraps = rootEl
            ? rootEl.querySelectorAll('[data-git-push-wrap]')
            : document.querySelectorAll('[data-git-push-wrap]');
        if (!wraps.length) return;
        const known = knownProjectGitRepos();
        if (known) {
            const multi = gitPushIsMultiRepo(known);
            wraps.forEach((wrap) => applyGitPushMenuMode(wrap, multi));
            return;
        }
        fetchProjectGitRepos(false).then((repos) => {
            const multi = gitPushIsMultiRepo(repos);
            wraps.forEach((wrap) => {
                if (!wrap.isConnected) return;
                applyGitPushMenuMode(wrap, multi);
            });
        }).catch(() => {});
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
        return copyChatSessionId(currentSessionId, null);
    }

    // State Management
    let currentSessionId = null;
    if (window.CuttleChatVfx) {
        const vfxController = window.CuttleChatVfx.create({
            getSessionId: () => currentSessionId,
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
            getSessionId: function () { return currentSessionId; },
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
    // existing _loadSessionSeq captured per sync below.
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
    // combined into one agent turn (not drained one-by-one).
    // Follow-up queue contents + persistence/take flags, owned by
    // chat_followup_queue.js (composes chat_activity.js item decisions).
    // followupDrainTimer stays here: raw timer handle for the scheduler.
    let followupQueue = CuttleFollowupQueue.createQueueState();
    const followupQueues = CuttleFollowupQueue.createRegistry((key) => getSessionPrefs(key)?.[followupWriteField]);
    function adoptFollowupQueue(sessionId, opts = {}) {
        followupQueue = followupQueues.adopt(String(canonicalizeChatSessionId(sessionId) || 'new'), followupQueue, !!opts.transfer);
        editingFollowupId = null;
        if (opts.transfer && followupQueue.items.length && isAuthMode()) {
            persistFollowupPut(toAuthDbSessionId(sessionId), followupQueue);
        }
    }

    let followupDrainTimer = null;
    /** Uploaded file refs awaiting the next send: {filename, path, mime, size?} */
    let pendingAttachments = [];
    function _pendingKey(sid) { return `cuttle.pendingAttachments.${sid || 'anon'}`; }
    function _savePendingAttachments() {
        try {
            if (!currentSessionId) return;
            localStorage.setItem(_pendingKey(currentSessionId), JSON.stringify(pendingAttachments));
        } catch {}
    }
    function _restorePendingAttachments() {
        try {
            if (!currentSessionId) return;
            const raw = localStorage.getItem(_pendingKey(currentSessionId));
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
        const keySid = sid != null && sid !== '' ? sid : (currentSessionId || 'new');
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
        const keySid = sid != null && sid !== '' ? sid : (currentSessionId || 'new');
        try { localStorage.removeItem(_draftKey(keySid)); } catch (_) {}
        updateSessionPrefs(keySid === 'new' ? newComposerPrefsId : keySid, { composerDraft: null });
    }
    function restoreComposerDraft(sid, opts = {}) {
        const force = !!(opts && opts.force);
        const keySid = sid != null && sid !== '' ? sid : (currentSessionId || 'new');
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
        if (currentSessionId == null) return false;
        return [...runningSessionIds].some((rid) => sessionIdsEqual(rid, currentSessionId));
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
            watchDetachedSessionForCompletion(keepRunningId);
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
            sessionRunning: currentSessionId != null
                && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, currentSessionId)),
            queueLength: followupQueue.items.length,
        });
        if (decision.kind === 'finish') {
            finishLocalStreamFromServerSync();
            return true;
        }
        if (decision.kind === 'clear-remote') {
            removeRemoteWaitingIndicator();
            if (currentSessionId) setHistorySessionRunning(currentSessionId, false);
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
        const sid = currentSessionId;
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
            sessionId: currentSessionId,
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
        form.append('session_id', authSessionIdForRequest() || currentSessionId || 'anon');
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
            if (!sessionIdsEqual(currentSessionId, sessionId) || pageIsBackgrounded()) return;
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
            currentSessionId,
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

    function updateSessionTitleUnreadDot() {
        const unreadIcon = document.getElementById('chatSessionUnreadIcon');
        const queuedIcon = document.getElementById('chatSessionQueuedIcon');
        const pausedIcon = document.getElementById('chatSessionPausedIcon');
        const wrap = document.getElementById('chatSessionTitle');
        if (!unreadIcon && !queuedIcon && !pausedIcon) return;
        const titleVisible = !!(wrap && !wrap.hidden);
        const showInput = !!(titleVisible && currentSessionId != null && (attentionPrefsFor(currentSessionId) || {}).awaitingInput);
        const inputIcon = document.getElementById('chatSessionInputIcon');
        if (inputIcon) inputIcon.hidden = !showInput;
        const showUnread = !!(titleVisible && !showInput && chatAttentionActive());
        const showUnreadError = !!(showUnread && chatAttentionIsError);
        // Queue dots only when unread is not already showing (green/red wins).
        // Amber = active (unpaused) queue; yellow = paused (see CH-000439-14 legend).
        const showQueued = !!(
            titleVisible
            && !showInput
            && !showUnread
            && currentSessionId != null
            && sessionHasActiveFollowup(currentSessionId, null)
        );
        const showPaused = !!(
            titleVisible
            && !showInput
            && !showUnread
            && !showQueued
            && currentSessionId != null
            && sessionHasPausedFollowup(currentSessionId, null)
        );
        if (unreadIcon) {
            unreadIcon.hidden = !showUnread;
            unreadIcon.classList.toggle('is-error', showUnreadError);
            unreadIcon.title = showUnreadError ? 'Unread error' : 'Unread';
            unreadIcon.setAttribute('aria-label', showUnreadError ? 'Unread error' : 'Unread');
        }
        if (queuedIcon) queuedIcon.hidden = !showQueued;
        if (pausedIcon) pausedIcon.hidden = !showPaused;
        if (wrap) {
            wrap.classList.toggle('has-unread', showUnread);
            wrap.classList.toggle('has-unread-error', showUnreadError);
            wrap.classList.toggle('has-queued', showQueued);
            wrap.classList.toggle('has-paused-queue', showPaused);
        }
    }

    function updateSessionTitleRunningIcon() {
        const icon = document.getElementById('chatSessionRunningIcon');
        const wrap = document.getElementById('chatSessionTitle');
        if (!icon) return;
        const running = !!(
            wrap
            && !wrap.hidden
            && currentSessionId != null
            && sessionShowsHistorySpinner(currentSessionId)
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
            currentSessionId,
            backgrounded: pageIsBackgrounded(),
            prefs: attentionPrefsFor(sessionId, sessionObj),
            sessionObj,
        });
    }

    // Owned by chat_activity.js — thin adapter.
    function parseFollowupQueue(raw) {
        return CuttleChatActivity.parseFollowupQueue(raw);
    }

    /** Resolve followup_queue for a session (open chat uses live followupQueue.items). */
    function followupQueueForSession(sessionId, sessionObj) {
        if (sessionId == null || sessionId === '') return [];
        if (currentSessionId != null && sessionIdsEqual(currentSessionId, sessionId)) {
            return followupQueue.items.slice();
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
            currentSessionId,
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
            currentSessionId,
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

    /** Initial chats shown per project; each "Show N more" adds another page. */
    const HISTORY_GROUP_PREVIEW_LIMIT = 5;
    const HISTORY_GROUP_PAGE_SIZE = 5;
    /** projectKey → how many chats currently revealed (at least PREVIEW). */
    const historyGroupVisibleCount = new Map();
    const HISTORY_PROJECT_COLLAPSE_KEY = 'cuttle.historyProjectCollapse';

    function readHistoryProjectCollapsePrefs() {
        try {
            const raw = localStorage.getItem(HISTORY_PROJECT_COLLAPSE_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function writeHistoryProjectCollapsePrefs(map) {
        try {
            localStorage.setItem(HISTORY_PROJECT_COLLAPSE_KEY, JSON.stringify(map || {}));
        } catch (_) {}
    }

    const HISTORY_SUBAGENT_EXPAND_KEY = 'cuttle.historySubagentExpand';

    function readHistorySubagentExpandPrefs() {
        try {
            const raw = localStorage.getItem(HISTORY_SUBAGENT_EXPAND_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function writeHistorySubagentExpandPrefs(map) {
        try {
            localStorage.setItem(HISTORY_SUBAGENT_EXPAND_KEY, JSON.stringify(map || {}));
        } catch (_) {}
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

    function historyGroupEntrySessionObj(entry) {
        if (!entry) return null;
        return entry.session || entry.serverSession || null;
    }

    function historyGroupHasUnread(group) {
        return !!(group && group.entries && group.entries.some((entry) => (
            sessionHasUnread(entry.sessionId, historyGroupEntrySessionObj(entry))
        )));
    }

    function historyGroupHasUnreadError(group) {
        return !!(group && group.entries && group.entries.some((entry) => (
            sessionHasUnreadError(entry.sessionId, historyGroupEntrySessionObj(entry))
        )));
    }

    function historyGroupHasPausedFollowup(group) {
        return !!(group && group.entries && group.entries.some((entry) => (
            sessionHasPausedFollowup(entry.sessionId, historyGroupEntrySessionObj(entry))
        )));
    }

    function historyGroupHasActiveFollowup(group) {
        return !!(group && group.entries && group.entries.some((entry) => (
            sessionHasActiveFollowup(entry.sessionId, historyGroupEntrySessionObj(entry))
        )));
    }

    function historyGroupContainsCurrent(group) {
        if (currentSessionId == null || !group || !group.entries) return false;
        return group.entries.some((entry) => sessionIdsEqual(entry.sessionId, currentSessionId));
    }

    /** Default expand latest project (+ open/unread/queue); honor localStorage overrides. */
    function isHistoryProjectSectionExpanded(group, groupIdx) {
        if (!group || !group.project) return true;
        const key = String(group.project.key);
        const prefs = readHistoryProjectCollapsePrefs();
        const pref = prefs[key];
        if (pref === 'expanded') return true;
        if (pref === 'collapsed') return false;
        if (historyGroupContainsCurrent(group)) return true;
        if (historyGroupHasUnread(group)) return true;
        if (historyGroupHasActiveFollowup(group)) return true;
        if (historyGroupHasPausedFollowup(group)) return true;
        return groupIdx === 0;
    }

    function getHistoryGroupVisibleLimit(groupKey, total) {
        const t = Math.max(0, Number(total) || 0);
        const stored = historyGroupVisibleCount.get(String(groupKey));
        if (stored == null || !isFinite(stored)) {
            return Math.min(HISTORY_GROUP_PREVIEW_LIMIT, t);
        }
        return Math.min(Math.max(HISTORY_GROUP_PREVIEW_LIMIT, Math.floor(stored)), t);
    }

    function historyGroupToggleHTML(groupKey, total, visible) {
        const safeKey = escapeHtml(String(groupKey));
        if (total <= HISTORY_GROUP_PREVIEW_LIMIT) return '';
        if (visible >= total) {
            return `<button type="button" class="history-show-all-btn" data-group-key="${safeKey}" data-total="${total}" onclick="window.collapseHistoryGroupReveal(event)">Show less</button>`;
        }
        const step = Math.min(HISTORY_GROUP_PAGE_SIZE, total - visible);
        const label = step === 1 ? 'Show 1 more' : `Show ${step} more`;
        return `<button type="button" class="history-show-all-btn" data-group-key="${safeKey}" data-total="${total}" onclick="window.revealMoreHistoryGroup(event)">${escapeHtml(label)}</button>`;
    }

    function renderHistoryGroupBody(group, renderEntry) {
        const key = group.project.key;
        const nested = nestHistoryEntries(group.entries);
        const total = nested.roots.length;
        const visibleLimit = getHistoryGroupVisibleLimit(key, total);
        const visible = visibleHistoryRoots(nested, visibleLimit, { currentSessionId: currentSessionId });
        const searchActive = historySearchQueryActive();
        const prefs = readHistorySubagentExpandPrefs();

        function renderTree(entry) {
            const pid = String(entry && entry.sessionId != null ? entry.sessionId : '');
            const kids = nested.childrenByParent.get(pid) || [];
            const expanded = kids.length
                ? isHistorySubagentGroupExpanded(pid, nested, {
                    searchActive: searchActive,
                    currentSessionId: currentSessionId,
                    prefs: prefs,
                })
                : false;
            const force = !!(kids.length && currentSessionId != null
                && historySessionAncestorIds(currentSessionId, nested).some((id) => sessionIdsEqual(id, pid)));
            const itemHtml = renderEntry(entry, {
                subagentChildCount: kids.length,
                subagentExpanded: expanded,
                subagentForceExpanded: force,
            });
            if (!kids.length) return itemHtml;
            let html = '<div class="history-subagent-group'
                + (expanded ? '' : ' is-collapsed')
                + '" data-parent-session-id="' + escapeHtml(pid) + '"'
                + (force ? ' data-force-expanded="1"' : '')
                + '>';
            html += itemHtml;
            html += '<div class="history-subagent-children" id="history-subagents-'
                + escapeHtml(pid) + '-kids" role="group">';
            kids.forEach((child) => {
                html += renderTree(child);
            });
            html += '</div></div>';
            return html;
        }

        let html = '';
        visible.forEach((entry) => {
            html += renderTree(entry);
        });
        html += historyGroupToggleHTML(key, total, visibleLimit);
        return html;
    }

    // Last applied history search (not only the input DOM). Android/Capacitor
    // WebViews can race keyboard/IME with refreshes; the live query keeps the
    // hard gate from falling through to a full project-list paint.
    let historySearchLiveQuery = '';
    // Last displayed search wave, reused only for explicit group reveal/collapse.
    let repaintHistorySearch = null;

    function repaintHistoryGroups() {
        if (historySearchQueryActive() && repaintHistorySearch) repaintHistorySearch();
        else reloadHistoryListKeepingSearch();
    }

    function historySearchQueryActive() {
        if (historyFiltersActive()) return true;
        if (String(historySearchLiveQuery || '').trim()) return true;
        const searchInput = document.getElementById('searchInput');
        if (searchInput && String(searchInput.value || '').trim()) return true;
        return false;
    }

    function softRefreshHistorySearchIndicators() {
        // Session cache may have been refreshed; do not repaint (would abort
        // in-flight content search and flash the unfiltered list).
        if (isAuthMode()) {
            try {
                const listed = ((window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || []);
                applyGeneratingFlagsFromSessions(listed);
            } catch (_) {}
        }
        syncHistoryRunningIndicators();
        syncHistoryUnreadIndicators();
    }

    function reloadHistoryListKeepingSearch() {
        // Panel open/poll/title refresh / auth-changed must not wipe an active
        // search. Re-running searchChats() aborts content fetch and flashes empty.
        if (historySearchQueryActive()) {
            softRefreshHistorySearchIndicators();
            return;
        }
        loadChatHistory();
    }

    function entrySearchMatchRank(entry) {
        // Lower sorts first: title matches above content-only matches.
        const s = (entry && (entry.serverSession || entry.session)) || {};
        const m = String((entry && entry.searchMatch) || s.match || '').toLowerCase();
        if (m === 'title') return 0;
        if (m === 'content') return 1;
        return 2;
    }

    function revealMoreHistoryGroup(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const btn = event && event.currentTarget;
        const key = btn && btn.getAttribute('data-group-key');
        if (!key) return;
        const total = Math.max(0, Number(btn.getAttribute('data-total')) || 0);
        const cur = getHistoryGroupVisibleLimit(key, total || Number.MAX_SAFE_INTEGER);
        const next = Math.min(
            total || (cur + HISTORY_GROUP_PAGE_SIZE),
            cur + HISTORY_GROUP_PAGE_SIZE
        );
        historyGroupVisibleCount.set(String(key), next);
        repaintHistoryGroups();
    }

    function collapseHistoryGroupReveal(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const btn = event && event.currentTarget;
        const key = btn && btn.getAttribute('data-group-key');
        if (!key) return;
        historyGroupVisibleCount.set(String(key), HISTORY_GROUP_PREVIEW_LIMIT);
        repaintHistoryGroups();
    }

    function toggleHistoryProjectCollapse(event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const header = event && event.currentTarget;
        if (!header) return;
        // Action buttons stopPropagation themselves; belt-and-suspenders.
        if (event && event.target && event.target.closest
            && event.target.closest('.history-section-actions')) {
            return;
        }
        const section = header.closest && header.closest('.history-section');
        const key = (section && section.getAttribute('data-project-key'))
            || header.getAttribute('data-project-key');
        if (!key) return;
        const prefs = readHistoryProjectCollapsePrefs();
        const currentlyExpanded = !(section && section.classList.contains('is-collapsed'));
        prefs[String(key)] = currentlyExpanded ? 'collapsed' : 'expanded';
        writeHistoryProjectCollapsePrefs(prefs);
        repaintHistoryGroups();
    }

    function historySubagentGroupOwnedBy(sessionId) {
        if (sessionId == null || sessionId === '') return null;
        const key = String(sessionId);
        let group = document.querySelector(`.history-subagent-group[data-parent-session-id="${CSS.escape(key)}"]`);
        if (group) return group;
        const bare = toAuthDbSessionId(sessionId);
        if (bare && String(bare) !== key) {
            group = document.querySelector(`.history-subagent-group[data-parent-session-id="${CSS.escape(String(bare))}"]`);
        }
        return group;
    }

    function historySubagentGroupContainsCurrent(group) {
        if (!group || currentSessionId == null) return false;
        const kids = group.querySelectorAll('.history-subagent-children .chat-history-item');
        for (let i = 0; i < kids.length; i++) {
            if (sessionIdsEqual(kids[i].dataset.sessionId, currentSessionId)) return true;
        }
        return false;
    }

    function syncHistorySubagentAttention() {
        document.querySelectorAll('.history-subagent-group').forEach((group) => {
            const items = group.querySelectorAll('.history-subagent-children .chat-history-item');
            let input = false;
            let error = false;
            let unread = false;
            let queued = false;
            let paused = false;
            let running = false;
            items.forEach((item) => {
                if (item.classList.contains('has-input')) input = true;
                if (item.classList.contains('has-unread-error')) error = true;
                if (item.classList.contains('has-unread')) unread = true;
                if (item.classList.contains('has-queued')) queued = true;
                if (item.classList.contains('has-paused-queue')) paused = true;
                if (item.classList.contains('is-running')) running = true;
            });
            const badge = group.querySelector(':scope > .chat-history-item .history-subagent-count-badge');
            if (!badge) return;
            badge.classList.toggle('has-input', input);
            badge.classList.toggle('has-unread-error', error);
            badge.classList.toggle('has-unread', !error && unread);
            badge.classList.toggle('has-queued', !error && !unread && queued);
            badge.classList.toggle('has-paused-queue', !error && !unread && !queued && paused);
            badge.classList.toggle('is-running', running);
        });
    }

    function applyHistorySubagentExpandState() {
        const searchActive = historySearchQueryActive();
        const prefs = readHistorySubagentExpandPrefs();
        document.querySelectorAll('.history-subagent-group').forEach((group) => {
            const pid = group.getAttribute('data-parent-session-id') || '';
            const force = historySubagentGroupContainsCurrent(group);
            const expanded = force || searchActive || prefs[pid] === 'expanded';
            group.classList.toggle('is-collapsed', !expanded);
            if (force) group.setAttribute('data-force-expanded', '1');
            else group.removeAttribute('data-force-expanded');
            const countEl = group.querySelector(':scope > .chat-history-item [data-child-count]');
            const count = countEl ? (Number(countEl.getAttribute('data-child-count')) || 0) : 0;
            const title = historySubagentToggleTitle(count, expanded, force);
            group.querySelectorAll(':scope > .chat-history-item .history-subagent-toggle, :scope > .chat-history-item .history-subagent-count-badge').forEach((el) => {
                el.title = title;
                el.setAttribute('aria-label', title);
                if (el.classList.contains('history-subagent-toggle')) {
                    el.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                }
            });
        });
        syncHistorySubagentAttention();
    }

    function setHistorySubagentGroupExpanded(parentId, expanded) {
        if (parentId == null || parentId === '') return;
        const prefs = readHistorySubagentExpandPrefs();
        prefs[String(parentId)] = expanded ? 'expanded' : 'collapsed';
        writeHistorySubagentExpandPrefs(prefs);
        applyHistorySubagentExpandState();
    }

    function toggleHistorySubagentGroup(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        closeHistoryItemMenu();
        const group = historySubagentGroupOwnedBy(sessionId);
        if (!group || group.getAttribute('data-force-expanded') === '1') return;
        const expand = group.classList.contains('is-collapsed');
        setHistorySubagentGroupExpanded(group.getAttribute('data-parent-session-id') || sessionId, expand);
    }

    function groupSessionsByProject(entries) {
        const groups = new Map();
        (entries || []).forEach((entry) => {
            const origin = String(
                (entry.serverSession && entry.serverSession.origin)
                || (entry.session && entry.session.origin)
                || ''
            ).trim().toLowerCase();
            const proj = (origin === 'discord_dm' || origin === 'discord_guild')
                ? { key: '__discord__', id: null, name: 'Discord', path: '' }
                // Prefer the entry's own row (server rows carry project
                // fields): passing only entry.session (null for server
                // entries) forced a full-list scan per chat — O(n²) paints.
                : resolveSessionProjectInfo(entry.sessionId, historyGroupEntrySessionObj(entry));
            if (!groups.has(proj.key)) {
                groups.set(proj.key, { project: proj, entries: [], latest: 0 });
            }
            const g = groups.get(proj.key);
            g.entries.push(entry);
            const u = Number(entry.updated) || 0;
            if (u > g.latest) g.latest = u;
        });
        const list = Array.from(groups.values());
        // Most recently active project first; "No project" stays last.
        list.sort((a, b) => {
            if (a.project.key === '__none__') return 1;
            if (b.project.key === '__none__') return -1;
            const d = (b.latest || 0) - (a.latest || 0);
            if (d !== 0) return d;
            return String(a.project.name).localeCompare(String(b.project.name), undefined, {
                sensitivity: 'base',
            });
        });
        list.forEach((g) => {
            g.entries.sort((a, b) => {
                // Starred favorites float to the top of each project category.
                const aStarred = entryIsStarred(a);
                const bStarred = entryIsStarred(b);
                if (aStarred && !bStarred) return -1;
                if (bStarred && !aStarred) return 1;
                // Search: title matches before content matches, even if content is newer.
                const aMatch = entrySearchMatchRank(a);
                const bMatch = entrySearchMatchRank(b);
                if (aMatch !== bMatch) return aMatch - bMatch;
                // Opening a chat must not move it: order by last message only.
                const d = (b.updated || 0) - (a.updated || 0);
                if (d !== 0) return d;
                // Stable tie-break so equal timestamps don't reshuffle on refresh.
                return String(b.sessionId).localeCompare(String(a.sessionId), undefined, {
                    numeric: true,
                });
            });
        });
        return list;
    }

    function entryIsStarred(entry) {
        if (!entry) return false;
        if (entry.serverSession) return !!Number(entry.serverSession.starred);
        if (entry.session) return !!entry.session.starred;
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
            && currentSessionId != null
            && sessionIdsEqual(currentSessionId, sessionId);
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
    /** Background pending polls after leaving a chat mid-turn (sessionId → cancel fn). */
    const _detachedCompletionWatchers = Object.create(null);

    function cancelDetachedSessionCompletionWatch(sessionId) {
        if (sessionId == null || sessionId === '') return;
        const sid = String(canonicalizeChatSessionId(sessionId));
        const bare = toAuthDbSessionId(sessionId);
        const keys = [sid, String(sessionId)];
        if (bare) {
            keys.push(String(bare), `db_session_${bare}`);
        }
        keys.forEach((k) => {
            const cancel = _detachedCompletionWatchers[k];
            if (typeof cancel === 'function') {
                try { cancel(); } catch (_) {}
            }
            delete _detachedCompletionWatchers[k];
        });
    }

    /**
     * After detachLocalGenerationForNavigation, the in-flight processMessage
     * waiter stops (stopState.abortSuppressed / nav-gen). Keep a quiet poll so a
     * finished reply still marks unread + chirps — only when pending has a
     * real body and the server is no longer generating.
     */
    function watchDetachedSessionForCompletion(sessionId) {
        if (sessionId == null || sessionId === '') return;
        const sid = String(canonicalizeChatSessionId(sessionId));
        if (inAppShell && window.parent.cuttleActivityBroker) {
            window.parent.cuttleActivityBroker.refresh();
            return;
        }
        cancelDetachedSessionCompletionWatch(sid);
        let cancelled = false;
        _detachedCompletionWatchers[sid] = () => { cancelled = true; };
        const startedAt = Date.now();
        const maxMs = 90 * 60 * 1000;
        const pollMs = 4000;

        (async () => {
            try {
                while (!cancelled && (Date.now() - startedAt) < maxMs) {
                    // No "returned home" check before the first await: navigation
                    // detaches while currentSessionId still names the chat being
                    // left. Re-opening the chat cancels this watch outright.
                    let data = null;
                    try {
                        const resp = await fetchWithTimeout(
                            `/api/chat-pending-result?session_id=${encodeURIComponent(sid)}`,
                            { credentials: 'include', cache: 'no-store' },
                            8000
                        );
                        data = await resp.json();
                    } catch (_) {
                        data = null;
                    }
                    if (cancelled) break;
                    if (currentSessionId != null && sessionIdsEqual(currentSessionId, sid)) break;

                    // Owned by chat_generation.js — one poll-step decision;
                    // the page owns transport, sleeps, chirp, and flag paint.
                    let body = '';
                    if (data && data.success && data.pending && data.result) {
                        body = String(data.result.response || data.result.output || '').trim();
                    }
                    const step = {
                        cancelled: false,
                        returnedHome: false,
                        body: body,
                        generating: !!(data && data.generating),
                        liveActive: false,
                    };
                    if (!step.body && !step.generating) {
                        const live = await fetchChatLiveStatus(sid).catch(() => null);
                        if (cancelled) break;
                        step.liveActive = liveStatusLooksActive(live);
                    }
                    const action = CuttleChatGeneration.classifyDetachedPoll(step);
                    if (action === 'notify-done') {
                        try { consumeParkedChatResult(sid); } catch (_) {}
                        setHistorySessionRunning(sid, false);
                        notifyAssistantResponseReady(sid, {
                            isError: data.result.success === false,
                        });
                        break;
                    }
                    if (action === 'keep-waiting') {
                        setHistorySessionRunning(sid, true);
                        await new Promise((r) => setTimeout(r, pollMs));
                        continue;
                    }
                    if (action === 'stop') break;
                    // Idle, no parked body — spin down; opening the chat loads history.
                    setHistorySessionRunning(sid, false);
                    break;
                }
            } finally {
                if (_detachedCompletionWatchers[sid]) {
                    delete _detachedCompletionWatchers[sid];
                }
            }
        })();
    }

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
            currentSessionId,
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
        reloadHistoryListKeepingSearch();
    }

    function historyUnreadIconHTML(isError) {
        const err = !!isError;
        return `<span class="history-unread-icon${err ? ' is-error' : ''}" title="${err ? 'Unread error' : 'Unread'}" aria-label="${err ? 'Unread error' : 'Unread'}"></span>`;
    }

    function historyQueuedIconHTML() {
        return `<span class="history-queued-icon" title="Queued prompt" aria-label="Queued prompt"></span>`;
    }

    function historyPausedIconHTML() {
        return `<span class="history-paused-icon" title="Paused queue" aria-label="Paused queued prompt"></span>`;
    }

    function historyAttentionIconHTML(kind) {
        if (kind === 'input') return '<span class="history-input-icon" title="Awaiting your input" aria-label="Awaiting your input"></span>';
        if (kind === 'error') return historyUnreadIconHTML(true);
        if (kind === 'unread') return historyUnreadIconHTML(false);
        if (kind === 'queued') return historyQueuedIconHTML();
        if (kind === 'paused') return historyPausedIconHTML();
        return '';
    }

    function applyLocalOpenPanes(panes) {
        localOpenPanes = Array.isArray(panes) ? panes.filter((p) => p && p.sessionId) : [];
        syncHistoryOpenPaneIndicators();
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
        if (currentSessionId != null && currentSessionId !== '') {
            return [{ sessionId: String(currentSessionId), pane: 1, kind: 'chat', focused: true }];
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

    /** Unique split-pane numbers currently showing a chat/terminal in this window. */
    function historyOpenPaneCount() {
        return [...new Set(
            (localOpenPanes || []).map((p) => Number(p.pane) || 0).filter((n) => n > 0)
        )].length;
    }

    function historyOpenPaneBadgeHTML(sessionId) {
        // A "1" badge is noise when there is only one pane.
        if (historyOpenPaneCount() < 2) return '';
        const hits = openPaneEntriesForSession(sessionId);
        if (!hits.length) return '';
        const panes = [...new Set(hits.map((p) => Number(p.pane) || 0).filter((n) => n > 0))].sort((a, b) => a - b);
        if (!panes.length) return '';
        const text = panes.join('+');
        const title = panes.length === 1
            ? ('Open in pane ' + panes[0] + ' (this window)')
            : ('Open in panes ' + panes.join(', ') + ' (this window)');
        return `<span class="history-open-pane-badge" title="${escapeHtmlInline(title)}" aria-label="${escapeHtmlInline(title)}">${escapeHtmlInline(text)}</span>`;
    }

    function syncHistoryOpenPaneIndicators() {
        document.querySelectorAll('.chat-history-item').forEach((item) => {
            const sid = item.dataset.sessionId;
            const hits = openPaneEntriesForSession(sid);
            item.classList.toggle('is-open-pane', hits.length > 0);
            const actions = item.querySelector('.chat-actions');
            let badge = item.querySelector('.history-open-pane-badge');
            const html = historyOpenPaneBadgeHTML(sid);
            if (!html) {
                if (badge) badge.remove();
                return;
            }
            if (!badge) {
                const wrap = document.createElement('div');
                wrap.innerHTML = html;
                badge = wrap.firstElementChild;
                if (actions) actions.insertBefore(badge, actions.firstChild);
                else item.appendChild(badge);
            } else {
                const wrap = document.createElement('div');
                wrap.innerHTML = html;
                const next = wrap.firstElementChild;
                if (next) {
                    badge.title = next.title;
                    badge.setAttribute('aria-label', next.getAttribute('aria-label') || '');
                    badge.textContent = next.textContent;
                }
            }
        });
    }

    function syncHistoryUnreadIndicators() {
        document.querySelectorAll('.chat-history-item').forEach((item) => {
            const sid = item.dataset.sessionId;
            if (sid == null) return;
            let sessionObj = null;
            if (isAuthMode()) {
                sessionObj = findAuthServerSession(sid);
            } else {
                try {
                    const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                    sessionObj = sessions[sid] || null;
                } catch (_) {}
            }
            const kind = sessionHistoryAttentionKind(sid, sessionObj);
            item.classList.toggle('has-unread', kind === 'unread' || kind === 'error');
            item.classList.toggle('has-unread-error', kind === 'error');
            item.classList.toggle('has-queued', kind === 'queued');
            item.classList.toggle('has-paused-queue', kind === 'paused');
            let inputIcon = item.querySelector('.history-input-icon');
            if (kind !== 'input' && inputIcon) { inputIcon.remove(); inputIcon = null; }
            item.classList.toggle('has-input', kind === 'input');
            let unreadIcon = item.querySelector('.history-unread-icon');
            let queuedIcon = item.querySelector('.history-queued-icon');
            let pausedIcon = item.querySelector('.history-paused-icon');
            const clearAll = () => {
                if (unreadIcon) { unreadIcon.remove(); unreadIcon = null; }
                if (queuedIcon) { queuedIcon.remove(); queuedIcon = null; }
                if (pausedIcon) { pausedIcon.remove(); pausedIcon = null; }
            };
            if (kind === 'input') {
                clearAll();
                if (!inputIcon) item.insertAdjacentHTML('afterbegin', historyAttentionIconHTML('input'));
            } else if (kind === 'error' || kind === 'unread') {
                if (queuedIcon) queuedIcon.remove();
                if (pausedIcon) pausedIcon.remove();
                if (!unreadIcon) {
                    unreadIcon = document.createElement('span');
                    unreadIcon.className = 'history-unread-icon';
                    item.insertBefore(unreadIcon, item.firstChild);
                }
                unreadIcon.classList.toggle('is-error', kind === 'error');
                unreadIcon.title = kind === 'error' ? 'Unread error' : 'Unread';
                unreadIcon.setAttribute('aria-label', kind === 'error' ? 'Unread error' : 'Unread');
            } else if (kind === 'queued') {
                if (unreadIcon) unreadIcon.remove();
                if (pausedIcon) pausedIcon.remove();
                if (!queuedIcon) {
                    queuedIcon = document.createElement('span');
                    queuedIcon.className = 'history-queued-icon';
                    queuedIcon.title = 'Queued prompt';
                    queuedIcon.setAttribute('aria-label', 'Queued prompt');
                    item.insertBefore(queuedIcon, item.firstChild);
                }
            } else if (kind === 'paused') {
                if (unreadIcon) unreadIcon.remove();
                if (queuedIcon) queuedIcon.remove();
                if (!pausedIcon) {
                    pausedIcon = document.createElement('span');
                    pausedIcon.className = 'history-paused-icon';
                    pausedIcon.title = 'Paused queue';
                    pausedIcon.setAttribute('aria-label', 'Paused queued prompt');
                    item.insertBefore(pausedIcon, item.firstChild);
                }
            } else {
                clearAll();
            }
        });
        syncHistoryProjectAttentionIndicators();
        syncHistorySubagentAttention();
        updateSessionTitleUnreadDot();
        try { scheduleChatActivityBroadcast(); } catch (_) {}
    }

    function syncHistoryProjectAttentionIndicators() {
        document.querySelectorAll('.history-section[data-project-key]').forEach((section) => {
            const items = section.querySelectorAll('.chat-history-item');
            let hasInput = false;
            let hasError = false;
            let hasUnread = false;
            let hasQueued = false;
            let hasPaused = false;
            items.forEach((item) => {
                if (item.classList.contains('has-input')) hasInput = true;
                if (item.classList.contains('has-unread-error')) hasError = true;
                if (item.classList.contains('has-unread')) hasUnread = true;
                if (item.classList.contains('has-queued')) hasQueued = true;
                if (item.classList.contains('has-paused-queue')) hasPaused = true;
            });
            if (!items.length) {
                hasError = section.getAttribute('data-has-error') === '1';
                hasUnread = section.getAttribute('data-has-unread') === '1';
                hasQueued = section.getAttribute('data-has-queued') === '1';
                hasPaused = section.getAttribute('data-has-paused') === '1';
            }
            const showInput = hasInput;
            const showError = !showInput && hasError;
            const showUnread = !showInput && !showError && hasUnread;
            const showQueued = !showInput && !showError && !showUnread && hasQueued;
            const showPaused = !showInput && !showError && !showUnread && !showQueued && hasPaused;
            section.classList.toggle('has-input', showInput);
            section.classList.toggle('has-unread-error', showError);
            section.classList.toggle('has-unread', showError || showUnread);
            section.classList.toggle('has-queued', showQueued);
            section.classList.toggle('has-paused-queue', showPaused);
            const count = section.querySelector('.history-section-count');
            if (count) {
                count.classList.toggle('has-input', showInput);
                count.classList.toggle('has-unread-error', showError);
                count.classList.toggle('has-unread', showError || showUnread);
                count.classList.toggle('has-queued', showQueued);
                count.classList.toggle('has-paused-queue', showPaused);
            }
            let unreadDot = section.querySelector('.history-section-unread-dot');
            let queuedDot = section.querySelector('.history-section-queued-dot');
            let pausedDot = section.querySelector('.history-section-paused-dot');
            const titleEl = section.querySelector('.history-section-title');
            const removeDots = () => {
                if (unreadDot) { unreadDot.remove(); unreadDot = null; }
                if (queuedDot) { queuedDot.remove(); queuedDot = null; }
                if (pausedDot) { pausedDot.remove(); pausedDot = null; }
            };
            if (showInput || showError || showUnread) {
                if (queuedDot) queuedDot.remove();
                if (pausedDot) pausedDot.remove();
                if (!unreadDot && titleEl) {
                    unreadDot = document.createElement('span');
                    unreadDot.className = 'history-section-unread-dot';
                    titleEl.insertBefore(unreadDot, titleEl.firstChild);
                }
                if (unreadDot) {
                    unreadDot.classList.toggle('is-input', showInput);
                    unreadDot.classList.toggle('is-error', showError);
                    unreadDot.title = showInput ? 'Awaiting your input' : showError ? 'Unread error' : 'Unread';
                    unreadDot.setAttribute('aria-label', showInput ? 'Awaiting your input' : showError ? 'Unread error' : 'Unread');
                }
            } else if (showQueued) {
                if (unreadDot) unreadDot.remove();
                if (pausedDot) pausedDot.remove();
                if (!queuedDot && titleEl) {
                    queuedDot = document.createElement('span');
                    queuedDot.className = 'history-section-queued-dot';
                    queuedDot.title = 'Queued prompt';
                    queuedDot.setAttribute('aria-label', 'Queued prompt');
                    titleEl.insertBefore(queuedDot, titleEl.firstChild);
                }
            } else if (showPaused) {
                if (unreadDot) unreadDot.remove();
                if (queuedDot) queuedDot.remove();
                if (!pausedDot && titleEl) {
                    pausedDot = document.createElement('span');
                    pausedDot.className = 'history-section-paused-dot';
                    pausedDot.title = 'Paused queue';
                    pausedDot.setAttribute('aria-label', 'Paused queued prompt');
                    titleEl.insertBefore(pausedDot, titleEl.firstChild);
                }
            } else {
                removeDots();
            }
        });
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
        if (!currentSessionId) return;
        const map = readPromptHistoryMap();
        map[currentSessionId] = promptHistoryState.list.slice();
        writePromptHistoryMap(map);
    }

    function recordPromptHistory(composedMessage) {
        if (!currentSessionId) return;
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
        if (sessionIdsEqual(currentSessionId, parsed.sessionId)) {
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

    function historyEntryParentId(entry) {
        if (!entry) return '';
        const s = entry.serverSession || entry.session || {};
        const pid = s.parent_session_id;
        if (pid == null || pid === '') return '';
        return String(pid);
    }

    function historyLookupNestedEntry(nested, sessionId) {
        if (!nested || !nested.byId || sessionId == null || sessionId === '') return null;
        const key = String(sessionId);
        if (nested.byId.has(key)) return nested.byId.get(key);
        const bare = typeof toAuthDbSessionId === 'function' ? toAuthDbSessionId(sessionId) : null;
        if (bare != null && nested.byId.has(String(bare))) return nested.byId.get(String(bare));
        for (const [id, entry] of nested.byId.entries()) {
            if (typeof sessionIdsEqual === 'function' && sessionIdsEqual(id, sessionId)) return entry;
            if (String(id) === key) return entry;
        }
        return null;
    }

    function nestHistoryEntries(entries) {
        const list = Array.isArray(entries) ? entries.slice() : [];
        const byId = new Map();
        list.forEach((e) => {
            const id = String(e && e.sessionId != null ? e.sessionId : '');
            if (id) byId.set(id, e);
        });
        const childrenByParent = new Map();
        const roots = [];
        list.forEach((e) => {
            const pid = historyEntryParentId(e);
            if (pid && byId.has(pid)) {
                if (!childrenByParent.has(pid)) childrenByParent.set(pid, []);
                childrenByParent.get(pid).push(e);
            } else {
                roots.push(e);
            }
        });
        return { roots: roots, childrenByParent: childrenByParent, byId: byId };
    }

    function historySessionAncestorIds(sessionId, nested) {
        const ids = [];
        let cur = sessionId;
        const seen = new Set();
        while (cur != null && cur !== '') {
            const key = String(cur);
            if (seen.has(key)) break;
            seen.add(key);
            const entry = historyLookupNestedEntry(nested, cur);
            const pid = historyEntryParentId(entry);
            if (!pid) break;
            if (!historyLookupNestedEntry(nested, pid)) break;
            ids.push(pid);
            cur = pid;
        }
        return ids;
    }

    function isHistorySubagentGroupExpanded(parentId, nested, opts) {
        opts = opts || {};
        const pid = String(parentId || '');
        if (!pid) return false;
        if (opts.searchActive) return true;
        const current = opts.currentSessionId;
        if (current != null && current !== '') {
            const ancestors = historySessionAncestorIds(current, nested);
            for (let i = 0; i < ancestors.length; i++) {
                if (ancestors[i] === pid) return true;
                if (typeof sessionIdsEqual === 'function' && sessionIdsEqual(ancestors[i], pid)) return true;
            }
        }
        const prefs = opts.prefs || {};
        return prefs[pid] === 'expanded';
    }

    function visibleHistoryRoots(nested, visibleLimit, opts) {
        const roots = (nested && Array.isArray(nested.roots)) ? nested.roots : [];
        const limit = Math.max(0, Number(visibleLimit) || 0);
        const visible = roots.slice(0, limit);
        const current = opts && opts.currentSessionId;
        if (current == null || current === '') return visible;
        const ancestors = historySessionAncestorIds(current, nested);
        const rootId = ancestors.length ? ancestors[ancestors.length - 1] : String(current);
        const already = visible.some((e) => {
            const id = e && e.sessionId;
            if (id == null) return false;
            if (String(id) === String(rootId)) return true;
            return typeof sessionIdsEqual === 'function' && sessionIdsEqual(id, rootId);
        });
        if (already) return visible;
        const missing = roots.find((e) => {
            const id = e && e.sessionId;
            if (id == null) return false;
            if (String(id) === String(rootId)) return true;
            return typeof sessionIdsEqual === 'function' && sessionIdsEqual(id, rootId);
        });
        return missing ? visible.concat([missing]) : visible;
    }

    function historySubagentNoun(count) {
        const n = Math.max(0, Number(count) || 0);
        return n === 1 ? 'sub-agent' : 'sub-agents';
    }

    function historySubagentToggleTitle(count, expanded, force) {
        const n = Math.max(0, Number(count) || 0);
        const noun = historySubagentNoun(n);
        const amount = n ? (n + ' ' + noun) : noun;
        if (force) return amount + ' (open because this chat is a sub-agent)';
        return (expanded ? 'Hide ' : 'Show ') + amount;
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
                    if (!currentSessionId && !getSessionPrefs(newComposerPrefsId)) applyStarredSlashChips();
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
        if (!currentSessionId) {
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
                    if (!currentSessionId) {
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
        if (!currentSessionId) {
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
        getSessionId: () => currentSessionId,
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
        captureMutation: (sid, label) => sessionMutations.capture(sid, label, _loadSessionSeq),
        isMutationCurrent: (tok) => sessionMutations.current(tok, currentSessionId, _loadSessionSeq),
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
        const sid = currentSessionId != null ? String(currentSessionId) : '';
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
        const sid = JSON.parse(options.body || '{}').session || currentSessionId;
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
    // Slash palette controller: menu DOM, keyboard navigation, filtering and
    // the dismiss lifecycle are owned by chat_slash_palette.js
    // (CuttleChatSlashPalette). The page shares the palette state so composer
    // chips and the (still page-owned) applySlashSelectionInner keep working,
    // and passes every page-owned capability in.
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
        saveDraft: () => saveComposerDraft(currentSessionId || 'new'),
    });
    slashPalette.bind();

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
        const _idNextSid = String(currentSessionId != null ? currentSessionId : '');
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
                saveComposerDraftControls(currentSessionId || 'new', key);
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

    function applySlashSelectionInner(textarea, key, idx) {
        const ctx = slashCtx[key];
        const cmd = ctx.filtered[idx];
        if (!cmd || !ctx.replaceRange) return;
        const { from, to } = ctx.replaceRange;
        const v = textarea.value;

        // Project entries set the chat's working project (cwd) instead of
        // inserting a sticky command chip into the message text.
        if (cmd.category === 'project') {
            textarea.value = v.slice(0, from) + v.slice(to);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            const proj =
                findProjectById(cmd.projectId) || {
                    id: cmd.projectId,
                    name: cmd.projectName,
                    path: cmd.projectPath,
                };
            setChatProject(proj);
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Muse model picks are a chat setting, not part of the next message: apply
        // and relabel the Muse chip. These entries only appear while the Muse
        // badge is applied, so no chip auto-add here.
        if (cmd.category === 'muse-model') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const mid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/muse\s+model\s+/i, '').trim();
            if (!mid || mid === '__refresh__' || cmd.museRefresh || cmd.disabled) {
                if (mid === '__refresh__' || cmd.museRefresh) {
                    persistMuseModelSelection('__refresh__');
                }
                ctx.replaceRange = null;
                ctx.filtered = [];
                ctx.paletteDismissed = false;
                hideSlashMenu(key);
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
                return;
            }
            persistMuseModelSelection(mid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            (window.showToast || function () {})(
                'Muse Code model: ' + museModelLabel(mid),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Muse effort picks are a chat setting like model picks: apply, toast,
        // send no message. Badge-gated, so no chip auto-add here either.
        if (cmd.category === 'muse-effort') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const eid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/muse\s+effort\s+/i, '').trim();
            persistMuseEffortSelection(eid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            repaintMuseUserBadges();
            (window.showToast || function () {})(
                'Muse Code reasoning effort: ' + (eid || 'default'),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Hermes model picks — chat setting, no message sent (badge-gated).
        if (cmd.category === 'hermes-model') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const mid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/hermes\s+model\s+/i, '').trim();
            persistHermesModelSelection(mid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            (window.showToast || function () {})(
                'Hermes model: ' + hermesModelLabel(mid),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Hermes effort picks — chat setting like model picks.
        if (cmd.category === 'hermes-effort') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const eid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/hermes\s+effort\s+/i, '').trim();
            persistHermesEffortSelection(eid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            repaintHermesUserBadges();
            (window.showToast || function () {})(
                'Hermes reasoning effort: ' + (eid || 'default'),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // OpenCode model picks — chat setting, no message sent (badge-gated).
        if (cmd.category === 'opencode-model') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const mid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/opencode\s+model\s+/i, '').trim();
            if (!mid || mid === '__refresh__' || cmd.opencodeRefresh || cmd.disabled) {
                if (mid === '__refresh__' || cmd.opencodeRefresh) {
                    persistOpenCodeModelSelection('__refresh__');
                }
                ctx.replaceRange = null;
                ctx.filtered = [];
                ctx.paletteDismissed = false;
                hideSlashMenu(key);
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
                return;
            }
            persistOpenCodeModelSelection(mid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            (window.showToast || function () {})(
                'OpenCode model: ' + opencodeModelLabel(mid),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // OpenCode effort picks — chat setting like model picks.
        if (cmd.category === 'opencode-effort') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const eid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/opencode\s+effort\s+/i, '').trim();
            persistOpenCodeEffortSelection(eid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            repaintOpenCodeUserBadges();
            (window.showToast || function () {})(
                'OpenCode reasoning effort: ' + (eid || 'default'),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Codex model picks — chat setting, no message sent (badge-gated).
        if (cmd.category === 'codex-model') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const mid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/codex\s+model\s+/i, '').trim();
            if (!mid || mid === '__refresh__' || cmd.codexRefresh || cmd.disabled) {
                if (mid === '__refresh__' || cmd.codexRefresh) {
                    persistCodexModelSelection('__refresh__');
                }
                ctx.replaceRange = null;
                ctx.filtered = [];
                ctx.paletteDismissed = false;
                hideSlashMenu(key);
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
                return;
            }
            persistCodexModelSelection(mid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            (window.showToast || function () {})(
                'Codex model: ' + codexModelLabel(mid),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Codex effort picks — chat setting like model picks.
        if (cmd.category === 'codex-effort') {
            if (cmd.disabled) return;
            textarea.value = v.slice(0, from) + v.slice(to);
            const eid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/codex\s+effort\s+/i, '').trim();
            persistCodexEffortSelection(eid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            repaintCodexUserBadges();
            (window.showToast || function () {})(
                'Codex reasoning effort: ' + (eid || 'default'),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Claude model picks — chat setting, no message sent (badge-gated).
        if (cmd.category === 'claude-model') {
            textarea.value = v.slice(0, from) + v.slice(to);
            const mid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/claude\s+model\s+/i, '').trim();
            if (!mid || mid === '__refresh__' || cmd.claudeRefresh || cmd.disabled) {
                if (mid === '__refresh__' || cmd.claudeRefresh) {
                    persistClaudeModelSelection('__refresh__');
                }
                ctx.replaceRange = null;
                ctx.filtered = [];
                ctx.paletteDismissed = false;
                hideSlashMenu(key);
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
                return;
            }
            persistClaudeModelSelection(mid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            (window.showToast || function () {})(
                'Claude Code model: ' + claudeModelLabel(mid),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        // Claude effort picks — chat setting like model picks.
        if (cmd.category === 'claude-effort') {
            if (cmd.disabled) return;
            textarea.value = v.slice(0, from) + v.slice(to);
            const eid =
                String(cmd.modelId || '').trim()
                || String(cmd.prefix || '').replace(/^\/claude\s+effort\s+/i, '').trim();
            persistClaudeEffortSelection(eid);
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips('welcome', document.getElementById('welcomeChatInput'));
            renderSlashChips('chat', document.getElementById('chatInput'));
            repaintClaudeUserBadges();
            (window.showToast || function () {})(
                'Claude Code effort: ' + (eid || 'default'),
                'success'
            );
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }

        textarea.value = v.slice(0, from) + v.slice(to);
        // Model picks compose as `/cursor /model <id>` but should not become a second
        // visible badge — the Cursor Agent chip already shows the preferred model.
        if (cmd.category === 'cursor-model') {
            const mid =
                String(cmd.modelId || '').trim() ||
                String(cmd.prefix || '')
                    .replace(/^\/model\s+/i, '')
                    .trim();
            // Catalog refresh is a cursor-cmd chip now (staged + send). Ignore
            // any stale __refresh__ model rows.
            if (!mid || mid === '__refresh__' || cmd.disabled) {
                ctx.replaceRange = null;
                ctx.filtered = [];
                ctx.paletteDismissed = false;
                hideSlashMenu(key);
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
                return;
            }
            textarea.value = v.slice(0, from) + v.slice(to);
            if (mid) {
                slashPaletteSupplement.preferredModel = mid;
                beginSupplementFetch('cursorModelsGen');
            }
            // Drop any prior model chips so we don't stack /model entries.
            ctx.chips = ctx.chips.filter((c) => (c.category || '') !== 'cursor-model');
            ctx.chips.push({
                prefix: cmd.prefix,
                label: preferredModelLabel(mid) || String(cmd.label || mid).replace(/\s*\(current\)\s*$/i, '').trim(),
                category: 'cursor-model',
                modelId: mid || undefined,
            });
            const other = key === 'chat' ? 'welcome' : 'chat';
            slashCtx[other].chips = ctx.chips.slice();
            ctx.replaceRange = null;
            ctx.filtered = [];
            ctx.paletteDismissed = false;
            hideSlashMenu(key);
            renderSlashChips(other, document.getElementById(other === 'chat' ? 'chatInput' : 'welcomeChatInput'));
            renderSlashChips(key, textarea);
            try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
            if (key === 'chat') autoResizeTextarea();
            else autoResizeWelcomeTextarea();
            return;
        }
        // Exclusive sticky agent: only one agent badge at a time (Cursor vs Muse etc.)
        const _stickyMatch = CuttleChatSlash.SLASH_COMMANDS.find((s) => s.prefix === cmd.prefix && s.stickySession);
        if (_stickyMatch) {
            ctx.chips = ctx.chips.filter((c) => {
                const m = CuttleChatSlash.SLASH_COMMANDS.find((s) => s.prefix === c.prefix);
                if (m && m.stickySession) return false;
                return !isOrphanedCompanionChip(c, cmd);
            });
        }
        ctx.chips.push({
            prefix: cmd.prefix,
            label: cmd.label,
            category: cmd.category || 'command',
        });
        ctx.replaceRange = null;
        ctx.filtered = [];
        ctx.paletteDismissed = false;
        hideSlashMenu(key);
        // Sticky agent chips apply for the rest of the session — mirror to both composers.
        const match = CuttleChatSlash.SLASH_COMMANDS.find((s) => s.prefix === cmd.prefix);
        if (match && match.stickySession) {
            const other = key === 'chat' ? 'welcome' : 'chat';
            slashCtx[other].chips = ctx.chips.slice();
            renderSlashChips(other, document.getElementById(other === 'chat' ? 'chatInput' : 'welcomeChatInput'));
            markStickyAgentCleared(false);
            persistStickySlashForCurrentSession(true);
        }
        renderSlashChips(key, textarea);
        try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
        if (key === 'chat') autoResizeTextarea();
        else autoResizeWelcomeTextarea();
    }

    // Single choke point for every palette pick (click, Enter, Tab): the pick rewrites
    // the composer programmatically (typed filter text stripped), which fires no input
    // event, so the persisted draft would keep the stale slash fragment. That fragment
    // resurrects on New Chat and pops the palette open with the previously picked model
    // as raw slash text. Persist synchronously: a debounce would lose a fast New Chat click.
    function applySlashSelection(textarea, key, idx) {
        applySlashSelectionInner(textarea, key, idx);
        try {
            saveComposerDraft(currentSessionId || 'new', key);
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
        const prefsId = currentSessionId || newComposerPrefsId;
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
        const prefs = currentSessionId ? getSessionPrefs(currentSessionId) : null;
        return !!(prefs && prefs.stickyCleared);
    }

    function markStickyAgentCleared(cleared) {
        stickyAgentClearedPending = !!cleared;
        if (currentSessionId) {
            updateSessionPrefs(currentSessionId, { stickyCleared: !!cleared });
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
            turnBadgeBySession.set(String(canonicalizeChatSessionId(currentSessionId) || ''), meta);
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

    function publishSharedComposerSelection(selection, sessionId = currentSessionId, retryId = null) {
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
        if (!currentSessionId) return;
        const bare = toAuthDbSessionId(currentSessionId);
        const payload = {
            projectId: currentProject ? currentProject.id : null,
            projectPath: currentProject ? currentProject.path : '',
            projectName: currentProject ? currentProject.name : '',
            checkoutUid: currentCheckoutUid || null,
            checkoutPath: currentCheckoutPath || null,
        };
        // Keep hydrate/list caches aligned so a later reconcile cannot resurrect
        // the previous project (CH-000204: EP chip → Cuttle on send).
        rememberSessionProject(currentSessionId, currentProject);
        // Write under both display and bare keys so reopen / history grouping stay in sync.
        updateSessionPrefs(currentSessionId, payload);
        if (bare && String(bare) !== String(currentSessionId)) {
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
        if (sessionId == null || sessionId === '' || currentSessionId == null || currentSessionId === '') {
            return false;
        }
        if (String(sessionId) === String(currentSessionId)) return true;
        try {
            const a = toAuthDbSessionId(sessionId);
            const b = toAuthDbSessionId(currentSessionId);
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

    /** Meta line above the name: chips, badges, state dots. Empty → hidden by CSS. */
    function historyItemMetaHTML(parts) {
        const html = (parts || [])
            .filter(Boolean)
            .map((s) => String(s).trim())
            .join('');
        return '<div class="history-item-meta">' + html + '</div>';
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
        return turnBadgeBySession.get(String(canonicalizeChatSessionId(currentSessionId) || '')) || null;
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
                reloadHistoryListKeepingSearch();
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
            reloadHistoryListKeepingSearch();
        } catch (error) {
            LOG_ERR('loadProjects: fetch error:', error);
            projects = [];
            reconcileChatProject();
            reloadHistoryListKeepingSearch();
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
        if (currentSessionId) {
            rememberSessionProject(currentSessionId, proj);
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                if (sessions[currentSessionId]) {
                    sessions[currentSessionId].projectId = proj ? proj.id : null;
                    sessions[currentSessionId].projectPath = proj ? proj.path : '';
                    sessions[currentSessionId].projectName = proj ? proj.name : '';
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
        if (currentSessionId) {
            persistProjectForCurrentSession();
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                if (sessions[currentSessionId]) {
                    sessions[currentSessionId].checkoutUid = uid || null;
                    sessions[currentSessionId].checkoutPath = currentCheckoutPath || null;
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
        return currentCheckoutPath
            || (currentProject && currentProject.path ? String(currentProject.path) : '');
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
                if (!currentProject) return null;
                return Object.assign({}, currentProject, {
                    path: effectiveGitPath(),
                    worktree: currentCheckoutUid ? (currentProjectBranch() || true) : '',
                });
            },
            getSessionId: function () { return currentSessionId; },
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
        if (currentSessionId) {
            serverProj = projectFieldsFromServerSession(findAuthServerSession(currentSessionId));
            const prefs = getSessionPrefs(currentSessionId);
            const barePrefs = getSessionPrefs(toAuthDbSessionId(currentSessionId));
            mergedPrefs = Object.assign({}, barePrefs || {}, prefs || {});
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                stored = sessions[currentSessionId] || null;
            } catch (_) {
                stored = null;
            }
        }
        const decided = CuttleChatProject.resolveChatProject({
            projects,
            currentSessionId,
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
        if (currentSessionId) {
            const storedCheckout = (serverProj && serverProj.checkoutUid)
                || (mergedPrefs && mergedPrefs.checkoutUid)
                || (stored && stored.checkoutUid) || '';
            currentCheckoutUid = String(storedCheckout || '').trim();
            currentCheckoutPath = String((mergedPrefs && mergedPrefs.checkoutPath)
                || (stored && stored.checkoutPath) || '').trim();
        }
        // Stamp the default onto prefs so history grouping matches the cwd chip
        // (auth sessions do not store project_id on the server).
        if (currentSessionId && currentProject && !hadStored
            && !sessionHasStoredProject(currentSessionId, null)
            && authSessionListHas(currentSessionId)
            && !projectFieldsFromServerSession(findAuthServerSession(currentSessionId))) {
            persistProjectForCurrentSession();
        } else if (currentSessionId && currentProject && hadStored) {
            const prefs = getSessionPrefs(currentSessionId);
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
            !currentSessionId
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
        if (rebound.changed && currentSessionId) persistProjectForCurrentSession();
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
        if (!el || !isAuthMode() || currentSessionId == null) return;
        if (el.dataset.persistInFlight === '1' || el.dataset.messageId) return;
        const bare = toAuthDbSessionId(currentSessionId);
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
        if (isHistoryDeleteModalOpen()) return true;
        if (isHistoryRenameModalOpen()) return true;
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
        closeHistoryItemMenu();
        closeHistoryDeleteModal();
        closeHistoryRenameModal();
        if (window.CuttleTooltips && typeof window.CuttleTooltips.hide === 'function') {
            window.CuttleTooltips.hide();
        }
        // Don't keep "Show N more" reveals across open/close — each open starts fresh.
        historyGroupVisibleCount.clear();
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
        if (isHistoryDeleteModalOpen()) {
            closeHistoryDeleteModal();
            e.preventDefault();
            return;
        }
        if (isHistoryRenameModalOpen()) {
            closeHistoryRenameModal();
            e.preventDefault();
            return;
        }
        if (menu && !menu.hidden) {
            closeHistoryItemMenu();
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
        return currentSessionId;
    }

    async function ensureAuthChatSession() {
        // Sessions are created on first message by /api/chat — never pre-create empties.
        return currentSessionId;
    }

    function createHistorySectionHeaderHTML(title, opts) {
        const showRefresh = !(opts && opts.showRefresh === false);
        const project = opts && opts.project;
        const collapsible = !!(opts && opts.collapsible && project && project.key);
        const expanded = !(opts && opts.collapsed);
        const count = Number(opts && opts.count) || 0;
        const hasError = !!(opts && opts.hasError);
        const hasUnread = !!(opts && opts.hasUnread) || hasError;
        const hasQueued = !!(opts && opts.hasQueued);
        const hasPaused = !!(opts && opts.hasPaused);
        const refreshBtn = showRefresh
            ? `<button type="button" class="history-refresh-btn" id="chatHistoryRefreshBtn" onclick="window.refreshChatHistoryList(event)" title="Refresh chats" aria-label="Refresh chat list">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <polyline points="23 4 23 10 17 10"/>
                        <polyline points="1 20 1 14 7 14"/>
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                    </svg>
                </button>`
            : '';
        const newChatBtn = (project && project.key !== '__none__' && project.key !== '__discord__')
            ? `<button type="button" class="history-new-chat-btn"
                    data-project-id="${escapeHtml(project.id != null ? String(project.id) : '')}"
                    data-project-name="${escapeHtml(project.name || '')}"
                    data-project-path="${escapeHtml(project.path || '')}"
                    onclick="window.newChatForProject(event)"
                    title="New chat in ${escapeHtml(project.name || 'this project')}"
                    aria-label="New chat in ${escapeHtml(project.name || 'this project')}">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">
                        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                </button>`
            : '';
        const chevron = collapsible
            ? `<span class="history-section-chevron" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
               </span>`
            : '';
        const countClass = hasError
            ? ' has-unread has-unread-error'
            : (hasUnread
                ? ' has-unread'
                : (hasQueued ? ' has-queued' : (!hasUnread && hasPaused ? ' has-paused-queue' : '')));
        const countHtml = collapsible && count > 0
            ? `<span class="history-section-count${countClass}" title="${count} chat${count === 1 ? '' : 's'}">${count}</span>`
            : '';
        const attentionDot = hasError
            ? `<span class="history-section-unread-dot is-error" title="Unread error" aria-label="Unread error"></span>`
            : (hasUnread
                ? `<span class="history-section-unread-dot" title="Unread" aria-label="Unread"></span>`
                : (hasQueued
                    ? `<span class="history-section-queued-dot" title="Queued prompt" aria-label="Queued prompt"></span>`
                    : (hasPaused
                        ? `<span class="history-section-paused-dot" title="Paused queue" aria-label="Paused queued prompt"></span>`
                        : '')));
        const headerAttrs = collapsible
            ? ` role="button" tabindex="0" data-project-key="${escapeHtml(String(project.key))}" aria-expanded="${expanded ? 'true' : 'false'}" onclick="window.toggleHistoryProjectCollapse(event)" onkeydown="window.toggleHistoryProjectCollapseKey(event)"`
            : '';
        return `
            <div class="history-section-header${collapsible ? ' is-collapsible' : ''}"${headerAttrs}>
                ${chevron}
                <div class="history-section-title">${attentionDot}${escapeHtml(title)}${countHtml}</div>
                <div class="history-section-actions" onclick="event.stopPropagation()">${newChatBtn}${refreshBtn}</div>
            </div>
        `;
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
                reloadHistoryListKeepingSearch();
                if (currentSessionId) updateChatSessionTitle(currentSessionId);
                done();
            }).catch(function () {
                reloadHistoryListKeepingSearch();
                if (currentSessionId) updateChatSessionTitle(currentSessionId);
                done();
            });
        }
        reloadHistoryListKeepingSearch();
        if (currentSessionId) updateChatSessionTitle(currentSessionId);
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

    async function createNewChat() {
        if (currentSessionId != null) saveComposerDraft(currentSessionId);
        applyStarredSlashChips();
        chatRestoreAbandoned = true;
        clearChatRestoreVeil();
        localStorage.setItem('autoResumeSession', 'false');

        // Detach prior chat's local stream UI first — otherwise welcome stays
        // disabled (processMessage) and/or isLoading parks the first send.
        detachLocalGenerationForNavigation();

        const chatMessages = document.getElementById('chatMessages');
        if (chatMessages) {
            chatMessages.innerHTML = '';
        }
        // Drop prior session's lazy-history offset — otherwise new-chat numbers
        // keep starting at older_visible_count+1 from the chat we left.
        resetChatHistoryWindow();
        transcriptNav.resetNav();

        document.getElementById('welcomeScreen').style.display = 'flex';
        document.getElementById('chatArea').style.display = 'none';

        document.querySelectorAll('.chat-history-item').forEach(item => {
            item.classList.remove('active');
        });

        // Drop the previous chat id before reconcile. Otherwise the welcome
        // screen inherits that session's project, then the first send (still
        // unsaved) snaps to the Cuttle default.
        releaseManualUnreadHoldIfLeaving(null);
        currentSessionId = null;
        adoptFollowupQueue(currentSessionId);
        applyHistorySubagentExpandState();
        applySessionIdentity(null);
        currentProject = null;
        hubLiveStatusCache = null;
        reconcileChatProject();
        // Prior chat's Tasks strip must not stick on welcome / new chat.
        refreshChatWidgets();
        closeChatHistoryPanel();

        // Don't persist an empty session — wait until the user sends a message.
        updateChatIdBadge(null);
        loadPromptHistoryForSession(null);
        localStorage.removeItem('lastChatSessionId');
        if (window.CuttleAuth && window.CuttleAuth.setCurrentChatSession) {
            window.CuttleAuth.setCurrentChatSession(null);
        }
        stopMessageSync({ resetCursor: true });
        clearFollowupQueue();
        CuttleStopState.clearWaitingSuppression(stopState);
        syncChatUrl(null);
        reportSessionToShell(null);
        restoreWelcomeStickySlash();
        restoreComposerDraft('new', { force: true });
        if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        resetChatAttentionState();
        focusWelcomeComposer();
        console.log('New chat ready (not saved until first message)');
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
    const CHAT_RESTORE_TIMEOUT_MS = 10000;
    let chatRestoreAbandoned = false;

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
        currentSessionId = null;
        adoptFollowupQueue(currentSessionId);
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

    /**
     * Open the chat named by ?chat= as soon as *either* signal lands: the
     * prefetched transcript (a 200 means the cookie is good, so we don't wait on
     * auth.js) or auth settling. Neither within 10s → show the new-chat splash.
     */
    async function bootRestoreSession(sessionParam) {
        // Never leave a spinner up, whatever happens below.
        setTimeout(clearChatRestoreVeil, CHAT_RESTORE_TIMEOUT_MS + 2000);
        const prefetch = takeChatTranscriptPrefetch(sessionParam);
        let prefetchData = null;
        const prefetchReady = prefetch
            ? prefetch.then((data) => {
                if (data && data.success) {
                    prefetchData = data;
                    return 'prefetch';
                }
                return null;
            }).catch(() => null)
            : null;
        const authReady = waitForAuthReady(CHAT_RESTORE_TIMEOUT_MS)
            .then((ok) => (ok ? 'auth' : null));

        const winner = await firstTruthy([prefetchReady, authReady], CHAT_RESTORE_TIMEOUT_MS);
        if (chatRestoreAbandoned) return;

        if (winner) {
            try {
                await loadChatSession(sessionParam, {
                    assumeAuth: winner === 'prefetch',
                    prefetched: prefetchData,
                    prefetchPromise: prefetch,
                });
            } finally {
                clearChatRestoreVeil();
            }
            return;
        }

        // Anonymous chats live in localStorage and need no auth at all.
        try {
            const local = JSON.parse(localStorage.getItem('chatSessions') || '{}');
            if (local && local[sessionParam]) {
                await loadChatSession(sessionParam);
                return;
            }
        } catch (_) {}

        chatRestoreAbandoned = true;
        showWelcomeSplash();
        if (typeof window.showToast === 'function') {
            window.showToast('Chat took too long to load — starting a new chat.', 'warning');
        }
    }

    let _loadSessionSeq = 0;

    async function loadChatSession(sessionId, opts = {}) {
        // Persist unsent text from the chat we're leaving (survives refresh).
        if (currentSessionId != null && !sessionIdsEqual(currentSessionId, sessionId)) {
            saveComposerDraft(currentSessionId);
        } else if (currentSessionId == null) {
            saveComposerDraft('new');
        }
        clearAllSlashChips();
        CuttleStopState.clearWaitingSuppression(stopState);
        sessionId = canonicalizeChatSessionId(sessionId);
        const loadSeq = ++_loadSessionSeq;
        // Close immediately on tap — don't wait for message fetch (felt stuck open on mobile).
        closeChatHistoryPanel();

        // Leaving a different chat's local stream — detach UI so this session
        // isn't treated as generating (false follow-up queue).
        if (
            generation.loading
            && (
                generation.localSessionId == null
                || !sessionIdsEqual(generation.localSessionId, sessionId)
            )
        ) {
            detachLocalGenerationForNavigation();
        }

        // assumeAuth: the boot prefetch already got a 200 from the auth-only
        // messages endpoint, so the cookie is valid even if auth.js hasn't emitted yet.
        if (isAuthMode() || opts.assumeAuth) {
            // Invalidate the per-session model cache before updating currentSessionId so
            // any render or send that follows picks up the correct model for the new session.
            if (currentSessionId !== sessionId) {
                agentControls.invalidateProviderCacheForSessionSwitch();
            }
            releaseManualUnreadHoldIfLeaving(sessionId);
            const prevSessionId = currentSessionId;
            const switchingAway = prevSessionId != null
                && !sessionIdsEqual(prevSessionId, sessionId);
            // Opening this chat — stop any background completion poll for it.
            cancelDetachedSessionCompletionWatch(sessionId);
            currentSessionId = sessionId;
            adoptFollowupQueue(currentSessionId);
            // Opened chat defaults to idle until the one-shot live-status (below)
            // confirms generating. Stale runningSessionIds / hub cache from a
            // prior visit used to flash the purple title + history spinner for
            // a few seconds while waiting on the shell heartbeat poll.
            // Owned by chat_generation.js — an opened chat defaults to idle
            // until the one-shot live-status confirms generating.
            const openFlags = CuttleChatGeneration.decideSessionOpen({ switchingAway });
            if (openFlags.clearHubCache) hubLiveStatusCache = null;
            if (openFlags.deferGenerating) deferOpenChatGeneratingFromSessions = true;
            if (openFlags.markIdle) setHistorySessionRunning(sessionId, false);
            if (switchingAway) {
                // Form-awaiting is DOM-derived; cleared messages must not keep
                // the old chat's pending-form spinner on this title.
                _mutateSessionIdSet(formAwaitingSessionIds, sessionId, false);
            }
            updateChatIdBadge(sessionId);
            if (window.CuttleAuth && window.CuttleAuth.setCurrentChatSession) {
                window.CuttleAuth.setCurrentChatSession(sessionId);
            }
            loadPromptHistoryForSession(String(sessionId));
            localStorage.setItem('lastChatSessionId', String(sessionId));
            localStorage.setItem('autoResumeSession', 'true');
            syncChatUrl(sessionId);
            refreshChatWidgets();
            // Prefs-only sticky restore BEFORE shell live-status can paint the
            // working bubble (phone hub race). Transcript inference runs again
            // after /messages below.
            restoreSessionStickySlash(sessionId, []);
            reportSessionToShell(sessionId, { preferFocus: true });
            const loadT0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
            // Capture before markChatSessionRead so the title unread can survive open.
            const openNeedsAck = sessionPrefsHasUnreadFlag(sessionId);
            resetChatAttentionState({
                needsAck: openNeedsAck,
                isError: openNeedsAck && sessionPrefsUnreadIsError(sessionId),
            });
            // Don't block transcript load on refreshing the full session list —
            // under a busy connection pool that await made reopen feel like 10–60s.
            const sessionsRefresh = (window.CuttleAuth && typeof window.CuttleAuth.loadChatSessions === 'function')
                ? Promise.resolve(window.CuttleAuth.loadChatSessions()).catch(() => {})
                : Promise.resolve();

            document.getElementById('welcomeScreen').style.display = 'none';
            document.getElementById('chatArea').style.display = 'flex';
            if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();

            const chatMessages = document.getElementById('chatMessages');
            chatMessages.innerHTML = '';
            lastSeenServerMessageId = 0;
            resetChatHistoryWindow();
            removeRemoteWaitingIndicator();
            stopMessageSync();
            // Drop a prior chat's hydrate so findAuthServerSession cannot briefly
            // resolve the wrong project while this session's /messages is in flight.
            if (
                lastHydratedSessionProject
                && !sessionIdsEqual(lastHydratedSessionProject.id, sessionId)
            ) {
                lastHydratedSessionProject = null;
            }

            const authSid = toAuthDbSessionId(sessionId);
            let loadedMessages = [];
            try {
                // Boot deep-links already have this transcript in flight (started in
                // chat_page.html) — reuse it instead of paying for a second round trip.
                let data = opts.prefetched || null;
                if (!data && opts.prefetchPromise) {
                    try { data = await opts.prefetchPromise; } catch (_) { data = null; }
                }
                if (!data) {
                    const prefetch = takeChatTranscriptPrefetch(sessionId);
                    if (prefetch) data = await prefetch;
                }
                if (!data) {
                    const response = await fetchWithTimeout(
                        sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }),
                        { credentials: 'include', cache: 'no-store' },
                        12000
                    );
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    data = await response.json();
                }
                // A newer loadChatSession started while we were fetching — drop this result.
                if (loadSeq !== _loadSessionSeq) return;
                if (data.success && Array.isArray(data.messages)) {
                    loadedMessages = windowChatHistoryMessages(data);
                    annotateMessageProjects(loadedMessages);
                    if (data.project_id != null || data.project_path || data.project_name) {
                        lastHydratedSessionProject = {
                            id: authSid,
                            project_id: data.project_id,
                            project_name: data.project_name,
                            project_path: data.project_path,
                        };
                    }
                    // Apply this session's project BEFORE the generating bubble is
                    // painted — otherwise the chip shows the previous chat's project
                    // (common on phone when switching projects mid-run).
                    reconcileChatProject();
                    // Early refreshChatWidgets() above often still had the prior
                    // chip path; re-fetch now that project matches this session.
                    refreshChatWidgets();
                    // Hydrate preferred model from transcript before painting so
                    // collapse/composer don't flash "Cursor - Auto" while the
                    // models API is still in flight.
                    const fromHistory = preferredModelFromMessages(loadedMessages);
                    if (fromHistory) {
                        slashPaletteSupplement.preferredModel = fromHistory;
                    }
                    // Same for Muse: the messages payload carries the session
                    // pins, so badges paint correctly on the first pass with
                    // no /api/muse/* round trip (and no heal flicker).
                    hydrateAgentSelectionFromSessionData(data, sessionId);
                    applySessionIdentity(data);
                    // Title from the same payload — don't wait for the session
                    // list (history panel) or the delayed title-refresh timers.
                    if (data.session_name) {
                        rememberChatSessionTitle(sessionId, data.session_name);
                        updateChatSessionTitle(sessionId);
                    }
                    beginSuppressChatAttention();
                    try {
                        // skipScroll/skipNav: bulk paint must not schedule bottom
                        // autoScroll rAFs that race a pending CH-…-N message jump.
                        loadedMessages.forEach(msg => {
                            appendServerMessageToUI(msg, {
                                announce: false,
                                skipScroll: true,
                                skipNav: true,
                            });
                        });
                    } finally {
                        endSuppressChatAttention();
                    }
                    transcriptNav.updateMessageNav();
                    // Server follow-ups travel with /messages — apply on open so a
                    // stuck queue (e.g. CH-000441) drains without waiting for sync.
                    applyServerFollowups(data.followups, data.followup_revision);
                    acceptSessionAttention(sessionId, data.attention);
                    // Transcript is on screen — drop the restore veil before the
                    // live-status round trip, which nobody needs to wait behind.
                    clearChatRestoreVeil();
                    reconcileCuttleButtonLocks(loadedMessages);
                    // Sticky agent BEFORE remote-waiting paint — otherwise
                    // currentTypingSlashMeta() sees empty/other-chat chips and
                    // the working bubble reopens without the agent badge.
                    restoreSessionStickySlash(sessionId, loadedMessages);
                    const liveStatus = await fetchChatLiveStatus(sessionId);
                    if (loadSeq !== _loadSessionSeq) return;
                    deferOpenChatGeneratingFromSessions = false;
                    updateRemoteWaitingFromMessages(loadedMessages, liveStatus);
                    healStaleGeneratingState();
                    if (
                        followupQueue.items.some((x) => !x.paused)
                        && !isSessionGenerating()
                        && !editingFollowupId
                    ) {
                        scheduleFollowupDrain(120);
                    }
                    refreshTypingIndicatorHeaderBadges();
                }
            } catch (e) {
                if (loadSeq !== _loadSessionSeq) return;
                deferOpenChatGeneratingFromSessions = false;
                console.error('Failed to load auth session messages:', e);
            }

            clearChatRestoreVeil();
            if (loadSeq !== _loadSessionSeq) return;

            try { await sessionsRefresh; } catch (_) {}
            if (loadSeq !== _loadSessionSeq) return;
            // One-shot generating flags for *other* chats (history rows). The open
            // chat stays idle until fetchChatLiveStatus above confirms — do not
            // wait for HISTORY_GENERATING_POLL_MS / shell heartbeat.
            try {
                const list = (window.CuttleAuth && window.CuttleAuth.getSessions
                    && window.CuttleAuth.getSessions()) || [];
                if (list.length) applyGeneratingFlagsFromSessions(list);
            } catch (_) {}
            // Session list may have landed after the early chrome paint — refresh
            // the open-chat title now that getSessions() is populated.
            updateChatSessionTitle(sessionId);
            reconcileChatProject();

            // Idempotent re-apply (also covers the messages-load failure path
            // where the early restore above never ran). Composer chips must
            // match this session even when there is no generating bubble.
            restoreSessionStickySlash(sessionId, loadedMessages);
            _restorePendingAttachments();
            restoreComposerDraft(sessionId, { force: true });

            // Proactively fetch the per-session model preference so the composer chip
            // and typing indicator show the correct model before the palette is opened.
            agentControls.loadCursorAgentModelsForPalette(true);
            agentControls.loadMuseModelsForPalette(true);

            markChatSessionRead(sessionId);
            document.querySelectorAll('.chat-history-item').forEach(item => {
                item.classList.toggle('active', sessionIdsEqual(item.dataset.sessionId, sessionId));
            });
            applyHistorySubagentExpandState();
            syncHistoryUnreadIndicators();
            syncChatAttentionIndicators();
            transcriptNav.settleAfterLoad();
            setupChatHistoryLazyLoad();
            // Re-apply after messages/sessions settle (server project beats stale empty local path).
            reconcileChatProject();
            startMessageSync();
            try {
                const ms = Math.round(
                    ((typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now()) - loadT0
                );
                window.CuttleNetDebug && window.CuttleNetDebug.event(
                    'chat-load',
                    'session=' + sessionId + ' msgs=' + loadedMessages.length + ' ' + ms + 'ms'
                );
            } catch (_) {}
            return;
        }

        // Load messages for this session from localStorage
        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        const session = sessions[sessionId];
        
        if (!session) {
            console.error('Session not found:', sessionId);
            closeChatHistoryPanel();
            clearChatRestoreVeil();
            return;
        }
        
        releaseManualUnreadHoldIfLeaving(sessionId);
        const prevSessionId = currentSessionId;
        const switchingAway = prevSessionId != null
            && !sessionIdsEqual(prevSessionId, sessionId);
        currentSessionId = sessionId;
        adoptFollowupQueue(currentSessionId);
        if (switchingAway) {
            hubLiveStatusCache = null;
            setHistorySessionRunning(sessionId, false);
            _mutateSessionIdSet(formAwaitingSessionIds, sessionId, false);
        }
        updateChatIdBadge(sessionId);
        loadPromptHistoryForSession(sessionId);
        _restorePendingAttachments();
        restoreComposerDraft(sessionId, { force: true });
        localStorage.setItem('lastChatSessionId', sessionId);
        syncChatUrl(sessionId);
        reportSessionToShell(sessionId, { preferFocus: true });
        refreshChatWidgets();
        const openNeedsAck = sessionPrefsHasUnreadFlag(sessionId);
        resetChatAttentionState({
            needsAck: openNeedsAck,
            isError: openNeedsAck && sessionPrefsUnreadIsError(sessionId),
        });
        
        // Re-enable auto-resume since user explicitly loaded this session
        localStorage.setItem('autoResumeSession', 'true');
        
        // Show chat area
        clearChatRestoreVeil();
        document.getElementById('welcomeScreen').style.display = 'none';
        document.getElementById('chatArea').style.display = 'flex';
        if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        
        // Load messages
        const chatMessages = document.getElementById('chatMessages');
        chatMessages.innerHTML = '';
        
        if (session.messages && session.messages.length > 0) {
            beginSuppressChatAttention();
            try {
                session.messages.forEach(msg => {
                    if (msg.role === 'assistant' && consumeCancelledAgentReply(msg.content, { announce: false })) return;
                    const reportUrl = msg.report_url || (
                        msg.query_id ? `/query_log.html?id=${msg.query_id}` : null
                    );
                    addMessageToUI(msg.content, msg.role, {
                        report_url: reportUrl,
                        query_id: msg.query_id,
                        timestamp: msg.timestamp,
                        slash_command: msg.slash_command,
                        slash_command_failed: msg.slash_command_failed,
                        attachments: msg.attachments,
                        routing_badge: msg.routing_badge || undefined,
                        usage: normalizeUsagePayload(msg.usage) || undefined,
                        skipScroll: true,
                        skipNav: true,
                    });
                });
            } finally {
                endSuppressChatAttention();
            }
            transcriptNav.updateMessageNav();
            reconcileCuttleButtonLocks(session.messages);
        }
        
        // Update active state in sidebar
        document.querySelectorAll('.chat-history-item').forEach(item => {
            item.classList.remove('active');
            if (item.dataset.sessionId === sessionId) {
                item.classList.add('active');
            }
        });
        applyHistorySubagentExpandState();

        // Restore the project (cwd) this conversation was using
        reconcileChatProject();
        restoreSessionStickySlash(sessionId, session.messages);
        markChatSessionRead(sessionId);
        syncHistoryUnreadIndicators();
        syncChatAttentionIndicators();
        
        // Scroll to bottom (or deep-linked message) - default stays pinned to bottom
        transcriptNav.settleAfterLoad();
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
        if (currentSessionId == null || !sessionIdsEqual(currentSessionId, sessionId)) return;
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
                const recovered = await recoverChatResultFromServer(currentSessionId);
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
                        notifyAssistantResponseReady(currentSessionId, { fromSync: true });
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

    function adoptChatSessionId(sessionId) {
        if (sessionId == null || sessionId === '') return;
        sessionId = canonicalizeChatSessionId(sessionId);
        const prev = currentSessionId;
        if (prev && !sessionIdsEqual(sessionId, prev)) {
            const prevBare = toAuthDbSessionId(prev);
            const nextBare = toAuthDbSessionId(sessionId);
            // Already bound to a Cuttle chat. Do not jump to a different numeric
            // id — that painted this turn under another session's chrome (phone
            // notification / CH- vs numeric remap / agent CLI session_id).
            if (
                prevBare && nextBare
                && /^\d+$/.test(prevBare) && /^\d+$/.test(nextBare)
                && prevBare !== nextBare
            ) {
                return;
            }
            migratePromptHistorySession(prev, sessionId);
            migrateSessionPrefs(prev, sessionId);
            migrateComposerDraft(prev, sessionId);
            if (!isAuthMode()) migrateLocalChatSession(prev, sessionId);
        }
        const previousBadgeKey = String(canonicalizeChatSessionId(prev) || '');
        if (previousBadgeKey !== String(sessionId) && turnBadgeBySession.has(previousBadgeKey)) {
            turnBadgeBySession.set(String(sessionId), turnBadgeBySession.get(previousBadgeKey));
            turnBadgeBySession.delete(previousBadgeKey);
        }
        const wasNew = currentSessionId == null || !sessionIdsEqual(currentSessionId, sessionId);
        if (window.CuttleCompletionNotifications && generation.loading) {
            window.CuttleCompletionNotifications.broker().adopt(prev || completionNotificationPendingId, sessionId);
        }
        releaseManualUnreadHoldIfLeaving(sessionId);
        currentSessionId = sessionId;
        adoptFollowupQueue(currentSessionId, {transfer: prev == null || !/^\d+$/.test(String(toAuthDbSessionId(prev)))});
        updateChatIdBadge(sessionId);
        localStorage.setItem('lastChatSessionId', String(sessionId));
        if (window.CuttleAuth && window.CuttleAuth.setCurrentChatSession) {
            window.CuttleAuth.setCurrentChatSession(sessionId);
        }
        syncChatUrl(sessionId);
        reportSessionToShell(sessionId);
        if (prev == null) clearSessionPrefs(newComposerPrefsId);
        // Persist project / sticky agent chosen before the server assigned an id.
        persistProjectForCurrentSession();
        persistStickySlashForCurrentSession(wasNew);
        // Persist a Muse model/effort picked while the chat had no id yet. Only
        // dirty picks persist here — persisting the in-memory default would
        // clobber the stored pin (e.g. Contributor reset to plain Spark 1.3).
        agentControls.flushDirtyAgentSelectionsOnAdopt();
        // Owned by chat_generation.js — rebind the local lock to the newly
        // bound session while loading; idle means no lock to move.
        const rebound = CuttleChatGeneration.rebindGeneration(generation, sessionId);
        if (rebound) {
            if (rebound.prev) setHistorySessionRunning(rebound.prev, false);
            setHistorySessionRunning(rebound.sessionId, true);
        }
        if (wasNew) {
            refreshChatHistoryList();
            scheduleHistoryTitleRefresh();
            startMessageSync();
        }
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
        if (!isAuthMode() || currentSessionId == null) return false;
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
                const authSid = toAuthDbSessionId(currentSessionId);
                if (!authSid) return false;
                const sessionAtStart = String(currentSessionId);
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
                if (String(currentSessionId) !== sessionAtStart) return false;
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
            if (!isAuthMode() || currentSessionId == null) return;
            if (!chatHistoryHasMore || chatHistoryLoadingOlder) return;
            if (transcriptNav.isScrollPinHeld()) return;
            if (box.scrollTop > 72) return;
            loadOlderChatMessages();
        }, { passive: true });
    }

    function scheduleNextMessageSync() {
        if (!isAuthMode() || currentSessionId == null) return;
        if (messageSyncTimer) {
            clearTimeout(messageSyncTimer);
            messageSyncTimer = null;
        }
        messageSyncTimer = setTimeout(async function () {
            messageSyncTimer = null;
            try {
                await syncSessionMessagesFromServer();
            } finally {
                if (isAuthMode() && currentSessionId != null) {
                    scheduleNextMessageSync();
                }
            }
        }, messageSyncDelayMs());
    }

    function startMessageSync() {
        if (!isAuthMode() || currentSessionId == null) return;
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

    const SUPERVISED_PHASE_LABELS = (window.CuttleSupervised && window.CuttleSupervised.PHASE_LABELS) || {
        coordinating: 'Planning task…',
        delegated: 'Working…',
        worker_running: 'Working…',
        follow_up: 'Working…',
        reviewing: 'Reviewing results…',
        awaiting_user: 'Awaiting your input…',
        approved: 'Done',
        cancelled: 'Cancelled',
        failed: 'Failed',
        escalated: 'Escalated',
        budget_exhausted: 'Budget exhausted',
        created: 'Thinking…',
    };

    const _supervisedJumpAck = new Set(
        (function () {
            try {
                return JSON.parse(sessionStorage.getItem('cuttle_supervised_jump_ack') || '[]');
            } catch (_) {
                return [];
            }
        })()
    );
    let _supervisedBubbleObserver = null;

    function acknowledgeSupervisedJump(eventId) {
        const id = String(eventId || '');
        if (!id) return;
        _supervisedJumpAck.add(id);
        try {
            sessionStorage.setItem(
                'cuttle_supervised_jump_ack',
                JSON.stringify(Array.from(_supervisedJumpAck).slice(-40))
            );
        } catch (_) {}
    }

    function scrollToSupervisedBubble(bubble, opts) {
        const highlight = !opts || opts.highlight !== false;
        if (!bubble) return;
        try {
            bubble.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } catch (_) {
            try { bubble.scrollIntoView(); } catch (__) {}
        }
        if (highlight) {
            bubble.classList.add('supervised-bubble-highlight');
            setTimeout(() => bubble.classList.remove('supervised-bubble-highlight'), 1600);
        }
    }

    function showSupervisedJumpNotification(task) {
        const CS = window.CuttleSupervised;
        if (!CS || !task || !task.terminal) return;
        const ev = (task.delivery && task.delivery.terminal_event_id)
            || task.supervised_delivery_event_id
            || (String(task.task_id) + ':' + String(task.phase || ''));
        if (!CS.shouldShowJumpNotification(task, _supervisedJumpAck)) return;
        let bar = document.getElementById('supervisedJumpNotify');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'supervisedJumpNotify';
            bar.className = 'supervised-jump-notify';
            bar.setAttribute('role', 'status');
            bar.setAttribute('aria-live', 'polite');
            document.body.appendChild(bar);
        }
        const label = (CS.jumpLabel && CS.jumpLabel(task.phase))
            || 'Background task completed';
        bar.innerHTML = '';
        const text = document.createElement('span');
        text.textContent = label + ' · ';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'supervised-jump-notify-btn';
        btn.textContent = 'Jump to result';
        btn.setAttribute('aria-label', label + '. Jump to result');
        btn.addEventListener('click', () => {
            acknowledgeSupervisedJump(ev);
            const box = document.getElementById('chatMessages');
            const bubble = CS.findCanonicalBubbleEl
                ? CS.findCanonicalBubbleEl(box, task)
                : null;
            scrollToSupervisedBubble(bubble);
            bar.hidden = true;
        });
        bar.appendChild(text);
        bar.appendChild(btn);
        bar.hidden = false;
        bar.dataset.eventId = ev;
    }

    function upsertSupervisedActivityCard(task) {
        const CS = window.CuttleSupervised;
        if (!CS || !task || !task.task_id) return;
        if (
            task.parent_session_id != null
            && currentSessionId != null
            && String(task.parent_session_id) !== String(currentSessionId)
            && String(task.parent_session_id) !== String(toAuthDbSessionId(currentSessionId))
        ) {
            const bare = String(currentSessionId).replace(/^db_session_/, '');
            if (String(task.parent_session_id) !== bare) return;
        }
        const state = CS.activityStateFromTask(task);
        if (!state) return;
        const box = document.getElementById('chatMessages');
        if (!box) return;

        box.querySelectorAll('.message.system.supervised-activity-row').forEach((row) => {
            try { row.remove(); } catch (_) {}
        });

        let bubble = CS.findCanonicalBubbleEl
            ? CS.findCanonicalBubbleEl(box, task)
            : box.querySelector(
                `.message.assistant[data-supervised-task-id="${CSS.escape(String(task.task_id))}"]`
            );
        if (!bubble && task.coordinator_response_message_id) {
            bubble = box.querySelector(
                `.message[data-message-id="${CSS.escape(String(task.coordinator_response_message_id))}"]`
            );
        }
        if (!bubble) return;

        bubble.dataset.supervisedTaskId = String(task.task_id);
        bubble.classList.add('supervised-coordinator-message');
        if (task.coordinator_response_message_id != null) {
            bubble.dataset.messageId = String(task.coordinator_response_message_id);
        }

        const contentEl = bubble.querySelector('.message-content');
        if (contentEl) {
            const nextRaw = String(
                task.bubble_content
                || (task.terminal && task.final_response)
                || bubble.dataset.rawContent
                || ''
            );
            let html;
            if (nextRaw && /cuttle_supervised_activity|Working…|Planning|Reviewing|Thinking/i.test(nextRaw)) {
                bubble.dataset.rawContent = nextRaw;
                html = formatMessage(stripLegacyAgentChrome(nextRaw));
            } else if (!task.terminal && CS.buildWorkingBubbleHtml) {
                html = CS.buildWorkingBubbleHtml(state);
                bubble.dataset.rawContent = state.phase_label || 'Working…';
            } else {
                const disclosure = CS.buildActivityDisclosureHtml
                    ? CS.buildActivityDisclosureHtml(state)
                    : '';
                const body = String(task.final_response || nextRaw || state.phase_label || '');
                bubble.dataset.rawContent = nextRaw || body;
                html = formatMessage(stripLegacyAgentChrome(body)) + disclosure;
            }
            contentEl.innerHTML = html;
            try { activateEnhancements(bubble); } catch (_) {}
        }

        const disclosure = bubble.querySelector('.supervised-activity-disclosure, .supervised-activity-card');
        if (disclosure) wireSupervisedActivityCard(disclosure);

        if (task.terminal) {
            showSupervisedJumpNotification(task);
        }
        observeSupervisedCanonicalBubble(bubble, task);
    }

    function wireSupervisedActivityCard(card) {
        if (!card || card.__wiredSupervised) return;
        card.__wiredSupervised = true;
        card.addEventListener('click', async (ev) => {
            const btn = ev.target && ev.target.closest
                ? ev.target.closest('[data-supervised-action]')
                : null;
            if (!btn) return;
            ev.preventDefault();
            const action = btn.getAttribute('data-supervised-action');
            const taskId = btn.getAttribute('data-task-id')
                || card.getAttribute('data-supervised-task-id')
                || (card.closest('[data-supervised-task-id]')
                    && card.closest('[data-supervised-task-id]').getAttribute('data-supervised-task-id'));
            if (action === 'followup') {
                const form = card.querySelector('.supervised-activity-followup-form');
                if (form) form.hidden = false;
                const ta = card.querySelector('.supervised-activity-followup-input');
                if (ta) ta.focus();
                return;
            }
            if (action === 'followup_cancel') {
                const form = card.querySelector('.supervised-activity-followup-form');
                if (form) form.hidden = true;
                return;
            }
            if (action === 'followup_submit') {
                const ta = card.querySelector('.supervised-activity-followup-input');
                const text = (ta && ta.value || '').trim();
                if (!text) return;
                btn.disabled = true;
                try {
                    await dispatchSupervisedTaskControl(taskId, 'followup', { instruction: text });
                    if (ta) ta.value = '';
                    const form = card.querySelector('.supervised-activity-followup-form');
                    if (form) form.hidden = true;
                } finally {
                    btn.disabled = false;
                }
                return;
            }
            if (action === 'status') {
                btn.disabled = true;
                try {
                    await dispatchSupervisedTaskControl(taskId, 'status');
                } finally {
                    btn.disabled = false;
                }
                return;
            }
            if (action === 'cancel') {
                btn.disabled = true;
                try {
                    await dispatchSupervisedTaskControl(taskId, 'cancel');
                } finally {
                    btn.disabled = false;
                }
                return;
            }
            if (action === 'view_report') {
                const reportUrl = btn.getAttribute('data-report-url')
                    || (window.__lastSupervisedTask
                        && window.__lastSupervisedTask.raw_report_url)
                    || '';
                if (reportUrl) {
                    window.open(reportUrl + (reportUrl.includes('?') ? '&' : '?') + 'format=text', '_blank', 'noopener');
                } else {
                    (window.showToast || function () {})('No worker report artifact yet.', 'info');
                }
                return;
            }
            if (action === 'copy_details') {
                const pre = card.querySelector('.supervised-activity-details-md');
                const detailsText = (pre && (pre.textContent || pre.innerText) || '').trim();
                if (!detailsText) {
                    (window.showToast || function () {})('No activity details to copy.', 'info');
                    return;
                }
                const copyFn = (typeof copyTextToClipboard === 'function')
                    ? copyTextToClipboard
                    : async (t) => {
                        if (navigator.clipboard && window.isSecureContext) {
                            await navigator.clipboard.writeText(t);
                            return true;
                        }
                        return false;
                    };
                try {
                    const ok = await copyFn(detailsText);
                    (window.showToast || function () {})(
                        ok ? 'Copied activity details' : 'Could not copy details',
                        ok ? 'success' : 'error'
                    );
                } catch (_) {
                    (window.showToast || function () {})('Could not copy details', 'error');
                }
                return;
            }
            if (action === 'view_activity') {
                await dispatchSupervisedTaskControl(taskId, 'status');
            }
        });
    }

    async function dispatchSupervisedTaskControl(taskId, action, params = {}) {
        const key = `${taskId}:${action}:${params.instruction || ''}`;
        if (!dispatchSupervisedTaskControl._inflight) dispatchSupervisedTaskControl._inflight = new Set();
        if (dispatchSupervisedTaskControl._inflight.has(key)) return null;
        dispatchSupervisedTaskControl._inflight.add(key);
        try {
            const response = await fetch(`/api/supervised/tasks/${encodeURIComponent(taskId)}/control`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, ...params, session_id: currentSessionId }),
            });
            const body = await response.json();
            if (!response.ok || body.success === false) throw new Error(body.error || `HTTP ${response.status}`);
            if (body.supervised_task) upsertSupervisedActivityCard(body.supervised_task);
            (window.showToast || function () {})(body.acknowledgement || 'Updated.', 'success');
            return body;
        } catch (err) {
            (window.showToast || function () {})(String(err && err.message || err), 'error');
            return null;
        } finally {
            dispatchSupervisedTaskControl._inflight.delete(key);
        }
    }

    async function dispatchSupervisedControlMessage(message, opts = {}) {
        const onceKey = opts.onceKey || '';
        if (onceKey && dispatchSupervisedControlMessage._inflight
            && dispatchSupervisedControlMessage._inflight.has(onceKey)) {
            return;
        }
        if (!dispatchSupervisedControlMessage._inflight) {
            dispatchSupervisedControlMessage._inflight = new Set();
        }
        if (onceKey) dispatchSupervisedControlMessage._inflight.add(onceKey);
        try {
            const outbound = String(message || '').trim();
            if (!isImmediateControlLaneMessage(outbound)) return;
            const userTs = Date.now();
            addMessageToUI(outbound, 'user', { timestamp: userTs });
            transcriptNav.autoScrollChatToBottom(true);
            await processMessage(outbound, { controlLane: true });
        } finally {
            if (onceKey) dispatchSupervisedControlMessage._inflight.delete(onceKey);
        }
    }

    function observeSupervisedCanonicalBubble(bubble, task) {
        if (!bubble || typeof IntersectionObserver === 'undefined') return;
        if (_supervisedBubbleObserver) {
            try { _supervisedBubbleObserver.disconnect(); } catch (_) {}
        }
        const el = document.getElementById('supervisedTaskIndicator');
        if (!el) return;
        _supervisedBubbleObserver = new IntersectionObserver((entries) => {
            const entry = entries[0];
            const active = task && !task.terminal;
            if (!active) {
                el.hidden = true;
                return;
            }
            el.hidden = !!(entry && entry.isIntersecting);
        }, { root: document.getElementById('chatMessages'), threshold: 0.12 });
        _supervisedBubbleObserver.observe(bubble);
    }

    /** Tiny off-screen chip — never a worker card or pending-prompt modal. */
    function updateSupervisedTaskIndicator(liveStatus, sessionId) {
        const el = document.getElementById('supervisedTaskIndicator');
        if (!el) return;
        if (
            sessionId != null
            && currentSessionId != null
            && !sessionIdsEqual(sessionId, currentSessionId)
        ) {
            return;
        }
        const task = liveStatus && liveStatus.supervised_task;
        window.__lastSupervisedTask = task || null;
        if (task && task.task_id) {
            upsertSupervisedActivityCard(task);
        }
        if (!task || !task.task_id || task.terminal) {
            el.hidden = true;
            el.classList.remove('is-terminal');
            return;
        }
        const phaseEl = document.getElementById('supervisedTaskPhase');
        const metaEl = document.getElementById('supervisedTaskMeta');
        const detailEl = document.getElementById('supervisedTaskDetail');
        if (phaseEl) phaseEl.textContent = 'Background task working';
        if (metaEl) metaEl.textContent = '';
        if (detailEl) detailEl.textContent = 'Jump to task';
        el.classList.remove('is-terminal');
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', 'Background task working. Jump to task.');
        el.onclick = () => {
            const box = document.getElementById('chatMessages');
            const CS = window.CuttleSupervised;
            const bubble = CS && CS.findCanonicalBubbleEl
                ? CS.findCanonicalBubbleEl(box, task)
                : null;
            scrollToSupervisedBubble(bubble);
        };
        el.onkeydown = (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                el.click();
            }
        };
        const box = document.getElementById('chatMessages');
        const CS = window.CuttleSupervised;
        const bubble = CS && CS.findCanonicalBubbleEl
            ? CS.findCanonicalBubbleEl(box, task)
            : null;
        if (!bubble) {
            el.hidden = false;
        } else {
            observeSupervisedCanonicalBubble(bubble, task);
        }
    }

    function updateRemoteWaitingFromMessages(messages, liveStatus) {
        rememberTurnBadge(messages, liveStatus);
        refreshTypingIndicatorHeaderBadges();
        noteWidgetsRevision(liveStatus);
        updateSupervisedTaskIndicator(liveStatus, currentSessionId);
        // Local stream owns this chat's turn — keep/restore the typing bubble
        // (it is wiped when switching chats or after a hard refresh race).
        if (
            generation.loading
            && generation.localSessionId != null
            && currentSessionId != null
            && sessionIdsEqual(generation.localSessionId, currentSessionId)
        ) {
            const statusLabel = (liveStatus && liveStatus.status) || undefined;
            ensureLocalTypingIndicator(statusLabel);
            setHistorySessionRunning(currentSessionId, true);
            try { subagentLaunchers.syncLive(liveStatus && liveStatus.subagents); } catch (_) {}
            return;
        }
        // Generating in another chat in this tab — don't steal that stream's UI.
        if (generation.loading) {
            removeRemoteWaitingIndicator();
            const liveActiveOther = remoteLiveStatusLooksActive(liveStatus);
            if (currentSessionId && liveActiveOther) {
                setHistorySessionRunning(currentSessionId, true);
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
            if (currentSessionId) setHistorySessionRunning(currentSessionId, false);
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
            || (currentSessionId != null
                && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, currentSessionId)));
        // Only show remote waiting when the server says a run is still active —
        // not merely because the last stored message is from the user (e.g. after Stop).
        if (currentSessionId) {
            setHistorySessionRunning(currentSessionId, liveActive);
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

    // A finished reply whose SSE stream died (refresh, network blip, Flask
    // hiccup) is parked server-side. Collect it instead of losing the run.
    // Owned by chat_pending_result.js — thin adapter: the page owns the
    // pending/live transport, paint, clock, and turn/stop guards; the wait
    // decisions live in the owner. After stream detach: one short poll at a
    // time — 2s dual GETs were starving chat reloads / YouTube while a long
    // agent ran.
    async function collectPendingResult(sessionId, opts = {}) {
        return CuttleChatPendingResult.waitForPendingResult(sessionId, opts, {
            isStaleNav: () => CuttleTurnGuard.isStale(opts.navGen, turnGeneration),
            isStopped: () => stopState.userStopped || stopState.abortSuppressed,
            hasReplyForTurn: () => !!(inFlightUserMessage && turnAlreadyShowsAssistantReply()),
            fetchPending: async (sid) => {
                const resp = await fetchWithTimeout(
                    `/api/chat-pending-result?session_id=${encodeURIComponent(sid)}`,
                    { credentials: 'include', cache: 'no-store' },
                    8000
                );
                return resp.json();
            },
            // Prefer hub cache for live-status when in the app shell (no extra GET).
            fetchLive: (sid) => (inAppShell && shellHubSeen)
                ? (getCachedHubLiveStatus(sid) || fetchChatLiveStatus(sid))
                : fetchChatLiveStatus(sid),
            liveLooksActive: (live) => liveStatusLooksActive(live),
            recoverFromHistory: (sid) => recoverChatResultFromServer(sid),
            onLiveStatus: (live) => {
                if (isViewingSession(sessionId)) updateTypingStatus(live.status);
            },
            onReportUrl: (url) => {
                if (isViewingSession(sessionId)) updateTypingIndicatorQueryLink(url);
            },
            consume: (sid) => consumeParkedChatResult(sid),
            netDebug: (name, info) => {
                try {
                    window.CuttleNetDebug && window.CuttleNetDebug.event(name, info);
                } catch (_) {}
            },
            sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
            now: () => Date.now(),
        });
    }

    /** Ack/consume a parked reply after the browser actually received it via SSE. */
    // Owned by chat_pending_result.js — thin adapter: the page owns the URL.
    function consumeParkedChatResult(sessionId) {
        CuttleChatPendingResult.consumeParkedChatResult(sessionId, (sid) => {
            try {
                fetchWithTimeout(
                    `/api/chat-pending-result?session_id=${encodeURIComponent(sid)}&consume=1`,
                    { credentials: 'include', cache: 'no-store' },
                    5000
                ).catch(() => {});
            } catch (_) {}
        });
    }

    /**
     * After a Flask restart, recover durable restart status + chat history without
     * requiring the user to send another message ("yo…did you finish?").
     */
    let _restartRecoverInFlight = false;
    let _lastRestartNotifiedId = null;
    // Owned by chat_pending_result.js — thin adapter: the page holds the
    // in-flight guard + notified-id set and owns status/health transport,
    // paint, and history sync effects; the poll decisions live in the owner.
    async function recoverAfterFlaskRestart(opts = {}) {
        if (_restartRecoverInFlight) return null;
        _restartRecoverInFlight = true;
        try {
            return await CuttleChatPendingResult.waitForServerRecovery(opts, {
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
                syncMessages: () => syncSessionMessagesFromServer(),
                paintStatus: (text) => updateTypingStatus(text),
                clearTyping: () => removeTypingIndicator(),
                toast: (msg, kind) => (window.showToast || function () {})(msg, kind),
                sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
                now: () => Date.now(),
                wasNotified: (id) => id === _lastRestartNotifiedId,
                markNotified: (id) => { _lastRestartNotifiedId = id; },
            });
        } finally {
            _restartRecoverInFlight = false;
        }
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
                        sessionId: currentSessionId,
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

    /**
     * When pending-result is empty after a detached stream, pull the assistant
     * reply that follows this turn's user message from auth history (DB).
     */
    // Owned by chat_pending_result.js — thin adapter: the page owns the
    // messages transport; match decisions live in the owner.
    async function recoverChatResultFromServer(sessionId) {
        return CuttleChatPendingResult.recoverChatResult(sessionId, {
            isAuthMode: () => isAuthMode(),
            toAuthDbSessionId: (sid) => toAuthDbSessionId(sid),
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
            inFlightText: inFlightUserMessage,
            norm: (text) => normalizeMessageContentForMatch(text),
            alreadyOnScreen: (role, content) => uiAlreadyHasMessage(role, content),
            isCancelledText: (text) => isCancelledAgentText(text),
            normalizeUsage: (usage) => normalizeUsagePayload(usage),
            netDebug: (name, info) => {
                try {
                    window.CuttleNetDebug && window.CuttleNetDebug.event(name, info);
                } catch (_) {}
            },
        });
    }

    /** Extra history polls before giving up with "No response received." */
    // Owned by chat_pending_result.js — thin adapter.
    async function recoverChatResultWithRetries(sessionId, attempts, gapMs) {
        return CuttleChatPendingResult.recoverChatResultWithRetries(
            sessionId, attempts, gapMs, {
                isStopped: () => stopState.userStopped || stopState.abortSuppressed,
                recover: (sid) => recoverChatResultFromServer(sid),
                isViewing: () => isViewingSession(sessionId),
                paintSyncing: () => updateTypingStatus('Working… (syncing reply)'),
                sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
            }
        );
    }

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
        if (followupQueue.items.length && !stopState.waitingSuppressed) {
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
        if (!isAuthMode() || currentSessionId == null) return;
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
        const sessionAtStart = String(currentSessionId);
        // Capture the existing navigation sequence for A->B->A fencing
        // (no second generation).
        const navAtStart = _loadSessionSeq;
        // True only while this request is still the current one: it owns
        // the claim, no navigation happened since, and the session still
        // matches. Required before ANY body-derived effect, including the
        // malformed active-status branch below.
        const syncStillCurrent = () =>
            CuttleChatGeneration.isSyncCurrent(messageSync, syncSeq)
            && _loadSessionSeq === navAtStart
            && String(currentSessionId) === sessionAtStart;
        try {
            const authSid = toAuthDbSessionId(currentSessionId);
            let msgResp;
            let liveStatus = null;
            const useHubStatus = inAppShell && shellHubSeen;
            if (recoveringLocal || useHubStatus) {
                msgResp = await fetchWithTimeout(
                    sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }),
                    { credentials: 'include', cache: 'no-store' },
                    8000
                );
                if (useHubStatus) liveStatus = getCachedHubLiveStatus(currentSessionId);
            } else {
                const pair = await Promise.all([
                    fetchWithTimeout(sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }), {
                        credentials: 'include',
                        cache: 'no-store',
                    }, 8000),
                    fetchChatLiveStatus(currentSessionId),
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
            acceptSessionAttention(currentSessionId, data.attention);
            // Keep badges truthful on incremental syncs too (same seeding as open).
            hydrateAgentSelectionFromSessionData(data, currentSessionId);
            applySessionIdentity(data);
            if (data.session_name) {
                rememberChatSessionTitle(currentSessionId, data.session_name);
                updateChatSessionTitle(currentSessionId);
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
                notifyAssistantResponseReady(currentSessionId, {
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

    function saveChatSession(message, role, opts = {}) {
        // Authenticated chats are persisted server-side in /api/chat
        if (isAuthMode()) {
            if (currentSessionId != null) {
                localStorage.setItem('lastChatSessionId', String(currentSessionId));
            }
            localStorage.setItem('autoResumeSession', 'true');
            if (role === 'assistant') scheduleHistoryTitleRefresh();
            return;
        }

        if (!currentSessionId) {
            // Create new session (CH-XXXXXX for anonymous local chats)
            clearSessionPrefs(newComposerPrefsId);
            currentSessionId = generateLocalChatId();
            adoptFollowupQueue(currentSessionId);
            localStorage.setItem('lastChatSessionId', currentSessionId);
            updateChatIdBadge(currentSessionId);
        }
        
        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        
        if (!sessions[currentSessionId]) {
            sessions[currentSessionId] = {
                id: currentSessionId,
                title: message.substring(0, 50) + (message.length > 50 ? '...' : ''),
                created: Date.now(),
                updated: Date.now(),
                messages: [],
                projectId: currentProject ? currentProject.id : null,
                projectPath: currentProject ? currentProject.path : '',
                projectName: currentProject ? currentProject.name : '',
            };
        }
        
        const msg = { content: message, role: role, timestamp: Date.now() };
        if (opts.query_id) msg.query_id = opts.query_id;
        if (opts.report_url) msg.report_url = opts.report_url;
        if (opts.slash_command) msg.slash_command = opts.slash_command;
        if (opts.slash_command_failed) msg.slash_command_failed = true;
        if (opts.usage) msg.usage = opts.usage;
        if (opts.routing_badge) msg.routing_badge = opts.routing_badge;
        const atts = normalizeAttachmentList(opts.attachments);
        if (atts.length) msg.attachments = atts;
        sessions[currentSessionId].messages.push(msg);
        
        sessions[currentSessionId].updated = Date.now();
        
        localStorage.setItem('chatSessions', JSON.stringify(sessions));
        
        // Re-enable auto-resume since user is actively chatting
        localStorage.setItem('autoResumeSession', 'true');
        
        // Reload chat history to update sidebar (keep active search/filters)
        reloadHistoryListKeepingSearch();
    }

    function _historyActionIcons() {
        return {
            // Match in-chat chrome hamburger (3 lines), not a vertical kebab.
            hamburger: '<span class="chat-chrome-hamburger" aria-hidden="true"><span></span><span></span><span></span></span>',
            copy: '<svg class="chat-id-badge-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="18" cy="5" r="3" stroke="currentColor" stroke-width="2"/><circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="19" r="3" stroke="currentColor" stroke-width="2"/><line x1="8.6" y1="10.5" x2="15.4" y2="6.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg><svg class="chat-id-badge-check" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20 6 9 17l-5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            pencil: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
            trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>',
            download: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
            archive: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
            unarchive: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><polyline points="12 12 12 16"/><polyline points="9.5 13.5 12 11 14.5 13.5"/></svg>',
            starOutline: '<svg class="chat-star-menu-icon chat-star-menu-icon--outline" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.8 6.7 19.6l1-5.8-4.2-4.1 5.9-.9L12 3.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
            starFilled: '<svg class="chat-star-menu-icon chat-star-menu-icon--filled" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.8 6.7 19.6l1-5.8-4.2-4.1 5.9-.9L12 3.5z"/></svg>',
            unread: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="5" fill="currentColor" opacity="0.9"/><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.6" opacity="0.45"/></svg>',
            gitPush: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M5 12l7-7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 19h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            terminal: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><polyline points="4 17 10 11 4 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="19" x2="20" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            voice: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="19" x2="12" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="8" y1="23" x2="16" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            subagents: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="6" cy="6" r="2.2" stroke="currentColor" stroke-width="2"/><circle cx="6" cy="18" r="2.2" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="12" r="2.2" stroke="currentColor" stroke-width="2"/><path d="M8.3 7.2c2.1 2 2.1 7.6 0 9.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M8.4 12H15.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
        };
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
        const subGroup = historySubagentGroupOwnedBy(sessionId);
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
        parts.push(gitPushMenuHtml(icons));
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
        closeHistoryItemMenu();
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

    function historyStarredIconHTML() {
        return '<span class="history-starred-icon" title="Starred" aria-label="Starred">★</span>';
    }

    function createHistoryDeleteActionsHTML(sessionId, opts) {
        const icons = _historyActionIcons();
        const includeRename = !!(opts && opts.includeRename);
        const lastMs = Number(opts && opts.lastMessageMs) || 0;
        const sid = String(sessionId).replace(/'/g, "\\'");
        const timeHtml = lastMs > 0
            ? `<time class="chat-time" datetime="${new Date(lastMs).toISOString()}" data-ts="${lastMs}" title="${escapeHtmlInline(new Date(lastMs).toLocaleString())}">${escapeHtmlInline(formatHistoryLastMessageAge(lastMs))}</time>`
            : '';
        const openHtml = historyOpenPaneBadgeHTML(sessionId);
        return `
            <div class="chat-actions" data-include-rename="${includeRename ? '1' : '0'}">
                ${openHtml}
                ${timeHtml}
                <button type="button" class="chat-action-button chat-menu-btn"
                    onclick="window.chatPageToggleHistoryMenu('${sid}', event)"
                    title="Chat actions"
                    aria-label="Chat actions"
                    aria-haspopup="menu"
                    aria-expanded="false">
                    ${icons.hamburger}
                </button>
            </div>
        `;
    }

    function historyItemEl(sessionId, event) {
        const fromEvent = event && event.currentTarget && typeof event.currentTarget.closest === 'function'
            ? event.currentTarget.closest('.chat-history-item')
            : null;
        if (fromEvent) return fromEvent;
        if (sessionId == null || sessionId === '') return null;
        return document.querySelector(`.chat-history-item[data-session-id="${CSS.escape(String(sessionId))}"]`);
    }

    function historyItemOverflowMenuEl() {
        let el = document.getElementById('historyItemOverflowMenu');
        if (el) {
            el.className = 'history-item-menu chat-chrome-menu-dropdown';
            return el;
        }
        el = document.createElement('div');
        el.id = 'historyItemOverflowMenu';
        // Reuse in-chat chrome dropdown skin; history-item-menu only pins fixed coords.
        el.className = 'history-item-menu chat-chrome-menu-dropdown';
        el.setAttribute('role', 'menu');
        el.hidden = true;
        document.body.appendChild(el);
        return el;
    }

    function closeHistoryItemMenu() {
        document.querySelectorAll('.chat-history-item.menu-open').forEach((item) => {
            item.classList.remove('menu-open');
            const btn = item.querySelector('.chat-menu-btn');
            if (btn) btn.setAttribute('aria-expanded', 'false');
        });
        const menu = document.getElementById('historyItemOverflowMenu');
        if (menu) {
            menu.hidden = true;
            menu.innerHTML = '';
        }
    }

    function positionHistoryItemMenu(btn, menu) {
        if (!btn || !menu) return;
        const rect = btn.getBoundingClientRect();
        const menuW = menu.offsetWidth || 160;
        const menuH = menu.offsetHeight || 120;
        let top = rect.bottom + 4;
        let left = rect.right - menuW;
        if (left < 8) left = 8;
        if (left + menuW > window.innerWidth - 8) {
            left = Math.max(8, window.innerWidth - menuW - 8);
        }
        if (top + menuH > window.innerHeight - 8) {
            top = Math.max(8, rect.top - menuH - 4);
        }
        menu.style.top = top + 'px';
        menu.style.left = left + 'px';
    }

    function toggleHistoryItemMenu(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const item = historyItemEl(sessionId, event);
        if (!item) return;
        const alreadyOpen = item.classList.contains('menu-open');
        closeHistoryItemMenu();
        if (alreadyOpen) return;

        closeHistoryDeleteModal();
        closeHistoryRenameModal();
        closeChatChromeMenu();
        item.classList.add('menu-open');
        const btn = item.querySelector('.chat-menu-btn');
        if (btn) btn.setAttribute('aria-expanded', 'true');

        const actions = item.querySelector('.chat-actions');
        const includeRename = !!(actions && actions.getAttribute('data-include-rename') === '1');
        const menu = historyItemOverflowMenuEl();
        menu.innerHTML = buildSessionMenuHTML(sessionId, {
            includeRename,
            includeDelete: true,
        });
        menu.hidden = false;
        positionHistoryItemMenu(btn, menu);
        refreshGitPushMenuMode(menu);
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
            reloadHistoryListKeepingSearch();
            if (sessionIdsEqual(sessionId, currentSessionId)) updateChatSessionStarButton(currentSessionId);
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
            reloadHistoryListKeepingSearch();
            if (sessionIdsEqual(sessionId, currentSessionId)) updateChatSessionStarButton(currentSessionId);
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
        reloadHistoryListKeepingSearch();
        if (sessionIdsEqual(sessionId, currentSessionId)) updateChatSessionStarButton(currentSessionId);
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

    let pendingDeleteSessionId = null;

    function historyDeleteModalEl() {
        return document.getElementById('historyDeleteModal');
    }

    let _gitPushConfirmResolve = null;

    function gitPushConfirmModalEl() {
        return document.getElementById('gitPushConfirmModal');
    }

    function closeGitPushConfirmModal(accepted) {
        const modal = gitPushConfirmModalEl();
        if (modal) modal.hidden = true;
        const fn = _gitPushConfirmResolve;
        _gitPushConfirmResolve = null;
        if (typeof fn === 'function') fn(!!accepted);
    }

    function confirmGitPushModal(message) {
        return new Promise(function (resolve) {
            const modal = gitPushConfirmModalEl();
            if (!modal) {
                resolve(false);
                return;
            }
            if (typeof _gitPushConfirmResolve === 'function') {
                _gitPushConfirmResolve(false);
            }
            _gitPushConfirmResolve = resolve;
            const subtitle = document.getElementById('gitPushConfirmSubtitle');
            if (subtitle) subtitle.textContent = String(message || 'Are you sure you want to push?');
            modal.hidden = false;
            const cancel = document.getElementById('gitPushConfirmCancel');
            if (cancel) cancel.focus();
        });
    }

    function setupGitPushConfirmModal() {
        const modal = gitPushConfirmModalEl();
        if (!modal || modal.dataset.bound === '1') return;
        modal.dataset.bound = '1';
        const cancelBtn = document.getElementById('gitPushConfirmCancel');
        const acceptBtn = document.getElementById('gitPushConfirmAccept');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                closeGitPushConfirmModal(false);
            });
        }
        if (acceptBtn) {
            acceptBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                closeGitPushConfirmModal(true);
            });
        }
        modal.addEventListener('click', function (e) {
            e.stopPropagation();
            if (e.target === modal) closeGitPushConfirmModal(false);
        });
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (!modal || modal.hidden) return;
            e.preventDefault();
            closeGitPushConfirmModal(false);
        });
    }

    function isHistoryDeleteModalOpen() {
        const modal = historyDeleteModalEl();
        return !!(modal && !modal.hidden);
    }

    function closeHistoryDeleteModal() {
        pendingDeleteSessionId = null;
        const modal = historyDeleteModalEl();
        if (modal) modal.hidden = true;
        const confirmBtn = document.getElementById('historyDeleteConfirm');
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Delete';
        }
    }

    function historyDeleteLabel(item, sessionId) {
        const titleEl = item && item.querySelector('.chat-title');
        const title = titleEl ? String(titleEl.textContent || '').trim() : '';
        if (title) return title;
        if (sessionId != null && sessionId !== '') {
            const fallback = String(getChatSessionDisplayTitle(sessionId) || '').trim();
            if (fallback) return fallback;
        }
        return 'this chat';
    }

    function openHistoryDeleteModal(sessionId, item) {
        const modal = historyDeleteModalEl();
        if (!modal) return;
        pendingDeleteSessionId = sessionId;
        const isTerminal = !!(item && item.getAttribute('data-kind') === 'terminal')
            || isTerminalSessionId(sessionId);
        const heading = document.getElementById('historyDeleteTitle');
        const subtitle = document.getElementById('historyDeleteSubtitle');
        if (heading) heading.textContent = isTerminal ? 'Delete terminal?' : 'Delete chat?';
        if (subtitle) {
            subtitle.textContent = '"' + historyDeleteLabel(item, sessionId) + '" will be removed. This cannot be undone.';
        }
        modal.hidden = false;
        const cancel = document.getElementById('historyDeleteCancel');
        if (cancel) cancel.focus();
    }

    function setupHistoryDeleteModal() {
        const modal = historyDeleteModalEl();
        if (!modal || modal.dataset.bound === '1') return;
        modal.dataset.bound = '1';
        const cancelBtn = document.getElementById('historyDeleteCancel');
        const confirmBtn = document.getElementById('historyDeleteConfirm');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeHistoryDeleteModal();
            });
        }
        if (confirmBtn) {
            confirmBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                confirmDeleteChatSession(pendingDeleteSessionId, e);
            });
        }
        modal.addEventListener('click', (e) => {
            e.stopPropagation();
            if (e.target === modal) closeHistoryDeleteModal();
        });
    }

    function requestDeleteChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        closeAllSessionMenus();
        const item = historyItemEl(sessionId, event);
        openHistoryDeleteModal(sessionId, item);
    }

    function cancelDeleteChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        closeHistoryDeleteModal();
    }

    async function confirmDeleteChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const sid = (sessionId != null && sessionId !== '') ? sessionId : pendingDeleteSessionId;
        if (sid == null || sid === '') return;
        const confirmBtn = document.getElementById('historyDeleteConfirm');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Deleting…';
        }
        try {
            await deleteChatSession(sid);
        } finally {
            closeHistoryDeleteModal();
        }
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
            loadChatHistory();
            return;
        }

        // If this chat is actively generating, abort the local stream immediately.
        if (sessionIdsEqual(currentSessionId, sessionId) && generation.loading) {
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

            if (sessionIdsEqual(currentSessionId, sessionId)) {
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

        if (currentSessionId === sessionId) {
            createNewChat();
        }

        reloadHistoryListKeepingSearch();
    }
    
    let pendingRenameSessionId = null;
    let pendingRenameKind = 'chat'; // chat | terminal | local
    let renameEditMode = false;
    let renameSuggestGen = 0;
    let renameSuggestInFlight = false;
    let renameAvoidTitles = [];

    function historyRenameModalEl() {
        return document.getElementById('historyRenameModal');
    }

    function isHistoryRenameModalOpen() {
        const modal = historyRenameModalEl();
        return !!(modal && !modal.hidden);
    }

    function setHistoryRenameStatus(msg, isError) {
        const status = document.getElementById('historyRenameStatus');
        if (!status) return;
        if (!msg) {
            status.hidden = true;
            status.textContent = '';
            status.classList.remove('is-error');
            return;
        }
        status.hidden = false;
        status.textContent = msg;
        status.classList.toggle('is-error', !!isError);
    }

    function descriptiveRenameTitle(fullTitle) {
        const parsed = parseTitleSlashChips(String(fullTitle || ''));
        const body = String(parsed.body || '').trim();
        if (body) return body;
        // Title was only slash chips — leave the editable field empty.
        if (parsed.chips && parsed.chips.length) return '';
        return String(fullTitle || '').trim();
    }

    function rememberRenameAvoid(title) {
        const t = descriptiveRenameTitle(title);
        if (!t) return;
        const key = t.toLowerCase();
        if (renameAvoidTitles.some((x) => String(x).toLowerCase() === key)) return;
        renameAvoidTitles.push(t);
        if (renameAvoidTitles.length > 12) renameAvoidTitles = renameAvoidTitles.slice(-12);
    }

    function syncHistoryRenamePreviewFromInput() {
        const input = document.getElementById('historyRenameInput');
        const preview = document.getElementById('historyRenamePreview');
        if (!preview) return;
        preview.textContent = input ? String(input.value || '') : '';
    }

    function setHistoryRenameSuggesting(loading) {
        const loadingEl = document.getElementById('historyRenameLoading');
        const preview = document.getElementById('historyRenamePreview');
        const previewWrap = document.getElementById('historyRenamePreviewWrap');
        const input = document.getElementById('historyRenameInput');
        const editBtn = document.getElementById('historyRenameEdit');
        if (loading) {
            renameEditMode = false;
            if (previewWrap) previewWrap.hidden = false;
            if (loadingEl) loadingEl.hidden = false;
            if (preview) preview.hidden = true;
            if (input) input.hidden = true;
            if (editBtn) {
                editBtn.hidden = true;
                editBtn.classList.remove('is-editing');
                const pencil = editBtn.querySelector('.history-rename-edit-pencil');
                const check = editBtn.querySelector('.history-rename-edit-check');
                if (pencil) pencil.hidden = false;
                if (check) check.hidden = true;
            }
            return;
        }
        if (loadingEl) loadingEl.hidden = true;
        applyHistoryRenameEditMode(renameEditMode);
    }

    function applyHistoryRenameEditMode(editing) {
        renameEditMode = !!editing;
        const previewWrap = document.getElementById('historyRenamePreviewWrap');
        const preview = document.getElementById('historyRenamePreview');
        const loadingEl = document.getElementById('historyRenameLoading');
        const input = document.getElementById('historyRenameInput');
        const editBtn = document.getElementById('historyRenameEdit');
        const hasText = !!(input && String(input.value || '').trim());
        if (loadingEl) loadingEl.hidden = true;
        if (renameEditMode) {
            if (previewWrap) previewWrap.hidden = true;
            if (preview) preview.hidden = true;
            if (input) {
                input.hidden = false;
                try { input.focus({ preventScroll: true }); } catch (_) { try { input.focus(); } catch (__) {} }
                try { input.select(); } catch (_) {}
            }
        } else {
            syncHistoryRenamePreviewFromInput();
            if (previewWrap) previewWrap.hidden = false;
            if (preview) preview.hidden = false;
            if (input) input.hidden = true;
        }
        if (editBtn) {
            editBtn.hidden = !hasText && !renameEditMode;
            editBtn.classList.toggle('is-editing', renameEditMode);
            editBtn.title = renameEditMode ? 'Done editing' : 'Edit name';
            editBtn.setAttribute('aria-label', renameEditMode ? 'Done editing' : 'Edit chat name');
            const pencil = editBtn.querySelector('.history-rename-edit-pencil');
            const check = editBtn.querySelector('.history-rename-edit-check');
            if (pencil) pencil.hidden = renameEditMode;
            if (check) check.hidden = !renameEditMode;
        }
    }

    function closeHistoryRenameModal() {
        pendingRenameSessionId = null;
        pendingRenameKind = 'chat';
        renameEditMode = false;
        renameSuggestGen += 1;
        renameSuggestInFlight = false;
        renameAvoidTitles = [];
        const modal = historyRenameModalEl();
        if (modal) modal.hidden = true;
        const saveBtn = document.getElementById('historyRenameSave');
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
        }
        const resuggestBtn = document.getElementById('historyRenameResuggest');
        if (resuggestBtn) resuggestBtn.disabled = false;
        setHistoryRenameStatus('', false);
        const input = document.getElementById('historyRenameInput');
        if (input) input.value = '';
        const preview = document.getElementById('historyRenamePreview');
        if (preview) preview.textContent = '';
    }

    function resolveRenameCurrentTitle(sessionId, kind) {
        if (kind === 'terminal') {
            const map = readTerminalSessionRegistry();
            const session = map[String(sessionId)];
            return (session && (session.title || session.shell)) || 'Terminal';
        }
        if (kind === 'local') {
            try {
                const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
                const session = sessions[sessionId] || sessions[String(sessionId)];
                if (session && session.title) return String(session.title);
            } catch (_) {}
        }
        return getChatSessionDisplayTitle(sessionId) || 'Chat';
    }

    async function suggestHistoryRenameTitle(opts) {
        const force = !!(opts && opts.force);
        const input = document.getElementById('historyRenameInput');
        const saveBtn = document.getElementById('historyRenameSave');
        const resuggestBtn = document.getElementById('historyRenameResuggest');
        const sessionId = pendingRenameSessionId;
        if (sessionId == null || sessionId === '') return null;

        if (!force && input && String(input.value || '').trim() && pendingRenameKind !== 'chat') {
            applyHistoryRenameEditMode(false);
            return String(input.value).trim();
        }

        const canSuggest = pendingRenameKind === 'chat' && isAuthMode() && toAuthDbSessionId(sessionId) != null;
        if (!canSuggest) {
            const current = descriptiveRenameTitle(resolveRenameCurrentTitle(sessionId, pendingRenameKind));
            if (input) input.value = current;
            setHistoryRenameSuggesting(false);
            applyHistoryRenameEditMode(false);
            setHistoryRenameStatus('Pencil to edit', false);
            return current;
        }

        const gen = ++renameSuggestGen;
        renameSuggestInFlight = true;
        if (saveBtn) saveBtn.disabled = true;
        if (resuggestBtn) resuggestBtn.disabled = true;
        setHistoryRenameSuggesting(true);
        setHistoryRenameStatus('', false);
        try {
            const authSid = toAuthDbSessionId(sessionId);
            const body = {};
            if (force && renameAvoidTitles.length) {
                body.avoid = renameAvoidTitles.slice();
            } else if (input && String(input.value || '').trim()) {
                // First open still may want to avoid the currently shown name on re-open.
                rememberRenameAvoid(input.value);
            }
            const resp = await fetchWithTimeout(
                `/api/auth/sessions/${encodeURIComponent(authSid)}/suggest-title`,
                {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                },
                90000
            );
            if (gen !== renameSuggestGen) return null;
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) {
                throw new Error((data && data.error) || ('HTTP ' + resp.status));
            }
            const title = descriptiveRenameTitle(
                String(data.title || '').trim()
                || resolveRenameCurrentTitle(sessionId, pendingRenameKind)
            );
            if (input) input.value = title;
            rememberRenameAvoid(title);
            setHistoryRenameSuggesting(false);
            applyHistoryRenameEditMode(false);
            const src = data.source === 'llm'
                ? 'Suggested'
                : (data.source === 'fallback' ? 'From first message' : 'Current title');
            setHistoryRenameStatus(src + ' — pencil to edit', false);
            return title;
        } catch (err) {
            if (gen !== renameSuggestGen) return null;
            const fallback = descriptiveRenameTitle(resolveRenameCurrentTitle(sessionId, pendingRenameKind));
            if (input) input.value = fallback;
            setHistoryRenameSuggesting(false);
            applyHistoryRenameEditMode(true);
            setHistoryRenameStatus(
                'Could not auto-suggest — edit manually (' + ((err && err.message) || 'error') + ')',
                true
            );
            return fallback;
        } finally {
            if (gen === renameSuggestGen) {
                renameSuggestInFlight = false;
                if (saveBtn) saveBtn.disabled = false;
                if (resuggestBtn) resuggestBtn.disabled = false;
            }
        }
    }

    function openHistoryRenameModal(sessionId, item) {
        const modal = historyRenameModalEl();
        if (!modal) return;
        closeHistoryDeleteModal();
        pendingRenameSessionId = sessionId;
        renameAvoidTitles = [];
        const isTerminal = !!(item && item.getAttribute('data-kind') === 'terminal')
            || isTerminalSessionId(sessionId);
        if (isTerminal) {
            pendingRenameKind = 'terminal';
        } else if (isAuthMode() && toAuthDbSessionId(sessionId) != null) {
            pendingRenameKind = 'chat';
        } else {
            pendingRenameKind = 'local';
        }

        const heading = document.getElementById('historyRenameTitle');
        if (heading) {
            heading.textContent = pendingRenameKind === 'terminal' ? 'Rename terminal' : 'Rename chat';
        }
        const resuggestBtn = document.getElementById('historyRenameResuggest');
        if (resuggestBtn) {
            resuggestBtn.hidden = pendingRenameKind !== 'chat';
        }

        const currentFull = resolveRenameCurrentTitle(sessionId, pendingRenameKind);
        const current = descriptiveRenameTitle(currentFull);
        rememberRenameAvoid(current);
        const input = document.getElementById('historyRenameInput');
        if (input) input.value = current;

        modal.hidden = false;
        if (pendingRenameKind === 'chat') {
            setHistoryRenameSuggesting(true);
            suggestHistoryRenameTitle({ force: true });
        } else {
            setHistoryRenameSuggesting(false);
            applyHistoryRenameEditMode(false);
            setHistoryRenameStatus('Pencil to edit', false);
        }
    }

    function setupHistoryRenameModal() {
        const modal = historyRenameModalEl();
        if (!modal || modal.dataset.bound === '1') return;
        modal.dataset.bound = '1';
        const cancelBtn = document.getElementById('historyRenameCancel');
        const saveBtn = document.getElementById('historyRenameSave');
        const resuggestBtn = document.getElementById('historyRenameResuggest');
        const editBtn = document.getElementById('historyRenameEdit');
        const input = document.getElementById('historyRenameInput');

        if (cancelBtn) {
            cancelBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeHistoryRenameModal();
            });
        }
        if (saveBtn) {
            saveBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                confirmRenameChatSession(pendingRenameSessionId, e);
            });
        }
        if (resuggestBtn) {
            resuggestBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                suggestHistoryRenameTitle({ force: true });
            });
        }
        if (editBtn) {
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (renameSuggestInFlight) return;
                applyHistoryRenameEditMode(!renameEditMode);
            });
        }
        if (input) {
            input.addEventListener('input', () => {
                if (renameEditMode) return;
                syncHistoryRenamePreviewFromInput();
            });
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    e.stopPropagation();
                    if (renameEditMode) {
                        applyHistoryRenameEditMode(false);
                    } else {
                        confirmRenameChatSession(pendingRenameSessionId, e);
                    }
                }
            });
        }
        modal.addEventListener('click', (e) => {
            e.stopPropagation();
            if (e.target === modal) closeHistoryRenameModal();
        });
    }

    async function persistRenamedTitle(sessionId, newTitle) {
        const title = String(newTitle || '').trim();
        if (!title) return false;

        if (isTerminalSessionId(sessionId) || pendingRenameKind === 'terminal') {
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

    async function confirmRenameChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }
        const sid = (sessionId != null && sessionId !== '') ? sessionId : pendingRenameSessionId;
        if (sid == null || sid === '') return;
        const input = document.getElementById('historyRenameInput');
        const newTitle = descriptiveRenameTitle(input ? String(input.value || '').trim() : '');
        if (!newTitle) {
            setHistoryRenameStatus('Enter a name (agent badges stay separate)', true);
            applyHistoryRenameEditMode(true);
            return;
        }
        const saveBtn = document.getElementById('historyRenameSave');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving…';
        }
        try {
            const ok = await persistRenamedTitle(sid, newTitle);
            if (!ok) {
                setHistoryRenameStatus('Could not rename this chat', true);
                return;
            }
            closeHistoryRenameModal();
            // refreshChatHistoryList already re-applies search/filters when active.
            await refreshChatHistoryList();
            if (currentSessionId != null && sessionIdsEqual(currentSessionId, sid)) {
                updateChatSessionTitle(sid);
            }
        } catch (err) {
            setHistoryRenameStatus((err && err.message) || 'Rename failed', true);
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        }
    }

    function renameChatSession(sessionId, event) {
        if (event) {
            event.stopPropagation();
        }
        closeAllSessionMenus();
        const item = historyItemEl(sessionId, event);
        openHistoryRenameModal(sessionId, item);
    }

    function historyRunningIconHTML() {
        return `<span class="history-running-icon" title="Generating…" aria-label="Generating"></span>`;
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

    /** Keep history spinners in sync with pending action forms (restart / watch). */
    function syncHistoryFormAwaitingFromDom() {
        const next = new Set();
        document.querySelectorAll('.cuttle-action-form.is-pending').forEach((card) => {
            const sid = formAwaitingSessionIdFromCard(card);
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
        if (currentSessionId != null && !serverAttention.get(toAuthDbSessionId(currentSessionId))) {
            const awaitingInput = actionCardsController().awaitingInputSessionIds()
                .some(sid => sessionIdsEqual(sid, currentSessionId));
            if (!!(attentionPrefsFor(currentSessionId) || {}).awaitingInput !== awaitingInput) {
                updateSessionPrefs(currentSessionId, { awaitingInput });
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
            currentSessionId: currentSessionId,
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
        document.querySelectorAll('.chat-history-item').forEach(item => {
            const sid = item.dataset.sessionId;
            const running = sessionShowsHistorySpinner(sid);
            item.classList.toggle('is-running', running);
            let icon = item.querySelector('.history-running-icon');
            if (running && !icon) {
                icon = document.createElement('span');
                icon.className = 'history-running-icon';
                icon.title = 'Active…';
                icon.setAttribute('aria-label', 'Active');
                const meta = historyItemMetaEl(item);
                if (meta) meta.appendChild(icon);
                else item.appendChild(icon);
            } else if (!running && icon) {
                icon.remove();
            }
        });
        syncHistorySubagentAttention();
        updateSessionTitleRunningIcon();
        scheduleChatActivityBroadcast();
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
            currentSessionId,
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
        const currentWasRunning = currentSessionId != null
            && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, currentSessionId));
        // Only mounted work in the open chat may supplement server execution.
        // Entries left behind by another chat must yield to an idle snapshot.
        const localPending = new Set();
        document.querySelectorAll('.cuttle-action-form.is-pending').forEach(card => {
            const sid = formAwaitingSessionIdFromCard(card);
            if (sessionIdsEqual(sid, currentSessionId)) localPending.add(String(canonicalizeChatSessionId(sid)));
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
                currentSessionId != null
                && sessionIdsEqual(s.id, currentSessionId)
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
                && currentSessionId != null
                && sessionIdsEqual(id, currentSessionId)
            ) {
                return;
            }
            if (!still.has(String(canonicalizeChatSessionId(id)))) {
                setHistorySessionRunning(id, false);
            }
        });
        const currentStillRunning = currentSessionId != null
            && [...runningSessionIds].some((rid) => sessionIdsEqual(rid, currentSessionId));
        if (
            currentWasRunning
            && !currentStillRunning
            && !generation.loading
            && !stopState.waitingSuppressed
            && followupQueue.items.length
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
            if (typeof currentSessionId !== 'undefined' && currentSessionId != null && currentSessionId !== '')
                updateChatSessionTitle(currentSessionId);
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
        if (typeof currentSessionId !== 'undefined' && currentSessionId != null && currentSessionId !== '')
            updateChatSessionTitle(currentSessionId);
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
            itemsHtml += createAuthHistoryItemHTML(s, {});
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

    function paintHistoryEntries(entries, renderEntry, opts) {
        const historyContainer = document.getElementById('chatHistory');
        if (!historyContainer) return;
        const options = opts || {};
        const showRefresh = options.showRefresh !== false;
        if (!entries.length) {
            const icon = options.emptyIcon || '💬';
            const title = options.emptyTitle || 'No chats yet';
            const text = options.emptyText || 'Start a new conversation';
            CuttleChatHistoryView.paint(historyContainer, `
                ${createHistorySectionHeaderHTML('Your chats')}
                <div class="empty-state">
                    <div class="empty-state-icon">${icon}</div>
                    <div class="empty-state-title">${escapeHtml(title)}</div>
                    <div class="empty-state-text">${escapeHtml(text)}</div>
                </div>
            `, renderArchivedSection);
            return;
        }
        const groups = groupSessionsByProject(entries);
        let html = '';
        groups.forEach((group, groupIdx) => {
            const expanded = isHistoryProjectSectionExpanded(group, groupIdx);
            const unread = historyGroupHasUnread(group);
            const unreadError = historyGroupHasUnreadError(group);
            const queuedAny = historyGroupHasActiveFollowup(group);
            const pausedAny = historyGroupHasPausedFollowup(group);
            const queued = !unread && queuedAny;
            const paused = !unread && !queued && pausedAny;
            const sectionClass = [
                expanded ? '' : ' is-collapsed',
                unreadError ? ' has-unread-error' : '',
                unread ? ' has-unread' : '',
                queued ? ' has-queued' : '',
                paused ? ' has-paused-queue' : '',
            ].join('');
            const safeKey = escapeHtml(String(group.project.key));
            html += `<div class="history-section${sectionClass}" data-project-key="${safeKey}" data-has-error="${unreadError ? '1' : '0'}" data-has-unread="${unread ? '1' : '0'}" data-has-queued="${queuedAny ? '1' : '0'}" data-has-paused="${pausedAny ? '1' : '0'}">`;
            html += createHistorySectionHeaderHTML(group.project.name, {
                showRefresh: showRefresh && groupIdx === 0,
                project: group.project,
                collapsible: true,
                collapsed: !expanded,
                count: group.entries.length,
                hasError: unreadError,
                hasUnread: unread,
                hasQueued: queued,
                hasPaused: paused,
            });
            html += `<div class="history-section-body">${renderHistoryGroupBody(group, renderEntry)}</div>`;
            html += '</div>';
        });
        CuttleChatHistoryView.paint(historyContainer, html, () => {
            syncHistoryRunningIndicators();
            syncHistoryUnreadIndicators();
            applyHistorySubagentExpandState();
            scheduleSlashChipCompactLabels(historyContainer);
            renderArchivedSection();
        });
    }

    function loadChatHistory() {
        // Hard gate: accidental full-list paints (auth-changed, loadProjects, poll)
        // must not wipe an active search or filter chips.
        if (historySearchQueryActive()) {
            softRefreshHistorySearchIndicators();
            return;
        }
        closeHistoryItemMenu();
        refreshLocalOpenPanes();
        const historyContainer = document.getElementById('chatHistory');
        if (!historyContainer) return;

        const terminalEntries = getTerminalHistoryEntries();

        if (isAuthMode()) {
            const serverSessions = ((window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [])
                .filter((s) => !isChatSessionDeleted(s && s.id));
            if (!serverSessions.length && !terminalEntries.length) {
                paintHistoryEntries([], null, {});
                return;
            }
            applyGeneratingFlagsFromSessions(serverSessions);
            // Record the main-list ids before the Archived fetch resolves so
            // an unfiltered (?archived=-unaware) response can't duplicate it.
            rememberActiveHistorySessions(serverSessions);
            // First paint kicks off the Archived section fetch (cached after).
            if (!archivedSessionsLoaded) refreshArchivedSessions();
            const entries = serverSessions.map((s) => ({
                sessionId: s.id,
                session: null,
                // Sort by last message only — last_activity can bump on non-message
                // touches and reshuffle the sidebar without a new turn.
                updated: parseSessionTimestamp(s.last_message_time)
                    || parseSessionTimestamp(s.last_activity),
                serverSession: s,
            })).concat(terminalEntries);
            paintHistoryEntries(entries, (entry, itemOpts) => {
                if (entry.isTerminal) return createTerminalHistoryItemHTML(entry.session);
                return createAuthHistoryItemHTML(entry.serverSession, itemOpts);
            });
            return;
        }

        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        const sessionArray = Object.values(sessions);
        const entries = sessionArray.map((session) => ({
            sessionId: session.id,
            session,
            updated: Number(session.updated) || 0,
        })).concat(terminalEntries);

        paintHistoryEntries(entries, (entry) => {
            if (entry.isTerminal) return createTerminalHistoryItemHTML(entry.session);
            return createHistoryItemHTML(entry.session);
        });
    }

    function createAuthHistoryItemHTML(s, extraOpts) {
        const sid = s.id;
        const title = s.session_name || (`Chat ${sid}`);
        const active = sessionIdsEqual(currentSessionId, sid) ? ' active' : '';
        const running = sessionShowsHistorySpinner(sid);
        const runningClass = running ? ' is-running' : '';
        const attention = sessionHistoryAttentionKind(sid, s);
        const unreadClass = attention === 'input' ? ' has-input' : (attention === 'unread' || attention === 'error') ? ' has-unread' : '';
        const errorClass = attention === 'error' ? ' has-unread-error' : '';
        const queuedClass = attention === 'queued' ? ' has-queued' : '';
        const pausedClass = attention === 'paused' ? ' has-paused-queue' : '';
        const starred = !!Number(s.starred);
        const starredClass = starred ? ' is-starred' : '';
        const runningIcon = running ? historyRunningIconHTML() : '';
        const starredIcon = starred ? historyStarredIconHTML() : '';
        const count = Number(s.message_count) || 0;
        const origin = String(s.origin || '').trim().toLowerCase();
        const parentId = s.parent_session_id != null ? String(s.parent_session_id) : '';
        const itemOpts = extraOpts || {};
        const childCount = Math.max(0, Number(itemOpts.subagentChildCount) || 0);
        const subExpanded = !!itemOpts.subagentExpanded;
        const subForce = !!itemOpts.subagentForceExpanded;
        const subagentClass = (origin === 'subagent' || parentId) ? ' is-subagent' : '';
        const hasSubagentsClass = childCount > 0 ? ' has-subagents' : '';
        const discordName = String(s.discord_username || '').trim();
        const participants = Array.isArray(s.discord_participants) ? s.discord_participants : [];
        const participantNames = participants
            .map((p) => String((p && (p.discord_username || p.discord_user_id)) || '').trim())
            .filter(Boolean);
        const isDiscordOrigin = origin === 'discord_dm' || origin === 'discord_guild';
        const discordTip = participantNames.length
            ? ('Discord · ' + participantNames.join(', '))
            : (discordName ? ('Discord · ' + discordName) : 'Discord');
        const discordBadge = (isDiscordOrigin || participantNames.length)
            ? `<span class="history-kind-badge" title="${escapeHtml(discordTip)}">Discord</span>`
            : '';
        const subagentBadge = (origin === 'subagent' || parentId)
            ? `<span class="history-kind-badge history-subagent-badge" title="Sub-agent of ${escapeHtml(parentId ? ('CH-' + String(parentId).padStart(6, '0')) : 'parent chat')}">Subagent</span>`
            : '';
        const projInfo = resolveSessionProjectInfo(sid, s);
        const projectChip = projInfo
            ? projectChipHtml(projInfo, {
                history: true,
                worktree: !!String(s.checkout_uid || '').trim(),
                branch: historyBranchFor(s),
            })
            : '';
        const badge = count > 0
            ? `<span class="history-msg-badge" title="${count} message${count === 1 ? '' : 's'}" aria-label="${count} messages">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <span class="history-msg-count">${count}</span>
               </span>`
            : '';
        const subToggleTitle = historySubagentToggleTitle(childCount, subExpanded, subForce);
        const subCountBadge = childCount > 0
            ? `<button type="button" class="history-subagent-count-badge" data-child-count="${childCount}" title="${escapeHtml(subToggleTitle)}" aria-label="${escapeHtml(subToggleTitle)}" onclick="window.toggleHistorySubagentGroup('${sid}', event)">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="12" r="2.4"/><path d="M8.4 7.2c2.2 2 2.2 7.6 0 9.6"/><path d="M8.5 12H15.5"/></svg>
                <span class="history-msg-count">${childCount}</span>
               </button>`
            : '';
        const subToggle = childCount > 0
            ? `<button type="button" class="history-subagent-toggle" data-child-count="${childCount}" aria-expanded="${subExpanded ? 'true' : 'false'}" aria-controls="history-subagents-${escapeHtml(String(sid))}-kids" title="${escapeHtml(subToggleTitle)}" aria-label="${escapeHtml(subToggleTitle)}" onclick="window.toggleHistorySubagentGroup('${sid}', event)">
                <span class="history-subagent-chevron" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </span>
               </button>`
            : '';
        const parts = chatTitleParts(title, sid);
        const openClass = openPaneEntriesForSession(sid).length ? ' is-open-pane' : '';
        const snippet = String(s.search_snippet || s.snippet || '').trim();
        const isContentMatch = String(s.match || '').toLowerCase() === 'content';
        const snippetText = snippet || (isContentMatch ? 'Matched in message body' : '');
        const snippetHtml = snippetText
            ? `<div class="history-search-snippet${isContentMatch ? ' is-content-match' : ''}">${escapeHtml(snippetText)}</div>`
            : '';
        const contentBadge = isContentMatch
            ? `<span class="history-kind-badge history-match-badge" title="Matched in message body">In messages</span>`
            : '';
        return `<div class="chat-history-item${active}${runningClass}${unreadClass}${errorClass}${queuedClass}${pausedClass}${starredClass}${openClass}${subagentClass}${hasSubagentsClass}" data-session-id="${sid}" data-parent-session-id="${escapeHtml(parentId)}" data-search-match="${isContentMatch ? 'content' : 'title'}" data-chat-title="${escapeHtml(title).replace(/"/g, '&quot;')}" onclick="window.chatPageLoadSession('${sid}')">
            ${historyAttentionIconHTML(attention)}
            ${subToggle}
            <div class="history-item-body">
                ${historyItemMetaHTML([parts.chipsHtml, projectChip, discordBadge, subagentBadge, contentBadge, subCountBadge, badge, starredIcon, runningIcon])}
                <div class="chat-title">${parts.titleHtml}</div>
                ${snippetHtml}
            </div>
            ${createHistoryDeleteActionsHTML(sid, { includeRename: true, starred, lastMessageMs: resolveHistoryLastMessageMs(s) })}
        </div>`;
    }
    
    function createHistoryItemHTML(session) {
        const isActive = currentSessionId === session.id;
        const running = sessionShowsHistorySpinner(session.id);
        const attention = sessionHistoryAttentionKind(session.id, session);
        const starred = !!session.starred;
        const parts = chatTitleParts(session.title, session.id);
        const openClass = openPaneEntriesForSession(session.id).length ? ' is-open-pane' : '';
        const isContentMatch = String(session.match || '').toLowerCase() === 'content';
        const contentBadge = isContentMatch
            ? '<span class="history-kind-badge history-match-badge" title="Matched in message body">In messages</span>'
            : '';
        const snippet = String(session.search_snippet || session.snippet || '').trim();
        const snippetText = snippet || (isContentMatch ? 'Matched in message body' : '');
        const snippetHtml = snippetText
            ? `<div class="history-search-snippet${isContentMatch ? ' is-content-match' : ''}">${escapeHtml(snippetText)}</div>`
            : '';
        const projInfo = resolveSessionProjectInfo(session.id, session);
        const projectChip = projInfo
            ? projectChipHtml(projInfo, {
                history: true,
                worktree: !!String(session.checkout_uid || '').trim(),
                branch: historyBranchFor(session),
            })
            : '';
        const meta = historyItemMetaHTML([
            '<span class="chat-icon">💬</span>',
            parts.chipsHtml,
            projectChip,
            contentBadge,
            starred ? historyStarredIconHTML() : '',
            running ? historyRunningIconHTML() : '',
        ]);
        const unreadClass = attention === 'input' ? ' has-input' : (attention === 'unread' || attention === 'error') ? ' has-unread' : '';
        const errorClass = attention === 'error' ? ' has-unread-error' : '';
        const queuedClass = attention === 'queued' ? ' has-queued' : '';
        const pausedClass = attention === 'paused' ? ' has-paused-queue' : '';
        return `
            <div class="chat-history-item ${isActive ? 'active' : ''}${running ? ' is-running' : ''}${unreadClass}${errorClass}${queuedClass}${pausedClass}${starred ? ' is-starred' : ''}${openClass}" 
                 data-session-id="${session.id}"
                 data-search-match="${isContentMatch ? 'content' : 'title'}"
                 data-chat-title="${escapeHtml(String(session.title || '')).replace(/"/g, '&quot;')}"
                 onclick="window.chatPageLoadSession('${session.id}')">
                ${historyAttentionIconHTML(attention)}
                <div class="history-item-body">
                    ${meta}
                    <div class="chat-title">${parts.titleHtml}</div>
                    ${snippetHtml}
                </div>
                ${createHistoryDeleteActionsHTML(session.id, { includeRename: true, starred, lastMessageMs: resolveHistoryLastMessageMs(session) })}
            </div>
        `;
    }

    function createTerminalHistoryItemHTML(session) {
        const sid = String(session.id).replace(/'/g, "\\'");
        const displayId = formatChatDisplayId(session.id) || '';
        const title = session.title || session.shell || 'Terminal';
        const starred = !!session.starred;
        const openClass = openPaneEntriesForSession(session.id).length ? ' is-open-pane' : '';
        const meta = historyItemMetaHTML([
            '<span class="chat-icon" aria-hidden="true">⌨️</span>',
            '<span class="history-kind-badge" title="Terminal session">Terminal</span>',
            displayId
                ? `<span class="history-chat-handle" title="${escapeHtml(displayId)}">${escapeHtml(displayId)}</span>`
                : '',
            starred ? historyStarredIconHTML() : '',
        ]);
        return `
            <div class="chat-history-item is-terminal${starred ? ' is-starred' : ''}${openClass}"
                 data-session-id="${escapeHtml(String(session.id))}"
                 data-kind="terminal"
                 onclick="window.chatPageOpenTerminalSession('${sid}')">
                <div class="history-item-body">
                    ${meta}
                    <div class="chat-title">${escapeHtml(title)}</div>
                </div>
                ${createHistoryDeleteActionsHTML(session.id, { includeRename: true, starred, lastMessageMs: resolveHistoryLastMessageMs(session) })}
            </div>
        `;
    }
    
    let historySearchSeq = 0;
    let historySearchTimer = null;
    let historySearchAbort = null;

    /** Active history filters from the Search slash palette (AND across kinds, OR within). */
    const historySearchFilters = {
        projects: [], // { key, name, path }
        agents: [],   // { key, id, label, prefix }
    };
    let historySlashPaletteItems = [];
    let historySlashPaletteIndex = 0;

    function historyFiltersActive() {
        return historySearchFilters.projects.length > 0
            || historySearchFilters.agents.length > 0;
    }

    function normalizeHistoryFilterKey(kind, value) {
        return String(kind || '') + ':' + String(value || '').trim().toLowerCase();
    }

    function sessionProjectFields(session) {
        const s = session || {};
        let name = String(s.project_name || s.projectName || '').trim();
        let path = String(s.project_path || s.projectPath || '').trim();
        if (!name && !path && s.id != null) {
            try {
                const info = resolveSessionProjectInfo(s.id, s);
                if (info && info.key !== '__none__') {
                    name = String(info.name || '').trim();
                    path = String(info.path || '').trim();
                    // Avoid inventing the default Cuttle project badge for chats
                    // that truly have no project when listing filters/matching.
                    if (info.name === 'No project') {
                        name = '';
                        path = '';
                    }
                }
            } catch (_) {}
        }
        return { name, path };
    }

    function sessionMatchesProjectFilter(session, filter) {
        if (!filter) return true;
        const fields = sessionProjectFields(session);
        const name = fields.name.toLowerCase();
        const path = fields.path.toLowerCase().replace(/\\/g, '/');
        const wantName = String(filter.name || '').trim().toLowerCase();
        const wantPath = String(filter.path || '').trim().toLowerCase().replace(/\\/g, '/');
        if (wantPath && path && (path === wantPath || path.includes(wantPath) || wantPath.includes(path))) {
            return true;
        }
        if (wantName && name && (name === wantName || name.includes(wantName))) return true;
        if (wantName && path && path.includes(wantName)) return true;
        return false;
    }

    function sessionAgentHaystack(session) {
        const s = session || {};
        const bits = [
            s.session_name,
            s.title,
            s.id,
        ];
        stickyTitleChipsForSession(s.id).forEach((c) => {
            bits.push(c.prefix, c.label, c.meta);
        });
        try {
            const parsed = parseTitleSlashChips(s.session_name || s.title || '');
            (parsed.chips || []).forEach((c) => bits.push(c.prefix, c.label, c.meta));
        } catch (_) {}
        return bits.join(' ').toLowerCase();
    }

    function sessionMatchesAgentFilter(session, filter) {
        if (!filter) return true;
        const hay = sessionAgentHaystack(session);
        const prefix = String(filter.prefix || '').trim().toLowerCase();
        const id = String(filter.id || '').trim().toLowerCase();
        if (prefix && hay.includes(prefix.trim())) return true;
        if (id && (hay.includes('/' + id) || hay.includes(id))) return true;
        return false;
    }

    function sessionMatchesHistoryFilters(session) {
        if (historySearchFilters.projects.length) {
            const ok = historySearchFilters.projects.some((p) => sessionMatchesProjectFilter(session, p));
            if (!ok) return false;
        }
        if (historySearchFilters.agents.length) {
            const ok = historySearchFilters.agents.some((a) => sessionMatchesAgentFilter(session, a));
            if (!ok) return false;
        }
        return true;
    }

    function historySessionHaystack(session) {
        const s = session || {};
        const participants = Array.isArray(s.discord_participants) ? s.discord_participants : [];
        const participantBits = participants.map((p) => (
            (p && (p.discord_username || p.discord_user_id)) || ''
        ));
        const proj = sessionProjectFields(s);
        return [
            s.session_name,
            s.title,
            s.id,
            formatChatDisplayId(s.id),
            s.discord_username,
            s.project_name,
            proj.name,
            proj.path,
            s.origin,
            ...participantBits,
        ].join(' ').toLowerCase();
    }

    function localSessionMatchesSearch(session, searchTerm) {
        if (!sessionMatchesHistoryFilters(session)) return false;
        if (!searchTerm) return true;
        if (historySessionHaystack(session).includes(searchTerm)) return true;
        const messages = session && session.messages;
        if (!Array.isArray(messages)) return false;
        return messages.some((msg) => String((msg && msg.content) || '').toLowerCase().includes(searchTerm));
    }

    function collectHistoryFilterProjects() {
        const byKey = new Map();
        const add = (name, path) => {
            const n = String(name || '').trim();
            const p = String(path || '').trim();
            if (!n && !p) return;
            const label = n || p.split(/[\\/]/).filter(Boolean).pop() || p;
            const key = normalizeHistoryFilterKey('project', p || n);
            if (byKey.has(key)) return;
            byKey.set(key, { key, name: label, path: p });
        };
        (projects || []).forEach((p) => add(p.name || p.project_name, p.path || p.project_path));
        try {
            const listed = ((window.CuttleAuth && window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || []);
            listed.forEach((s) => add(s.project_name, s.project_path));
        } catch (_) {}
        return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
    }

    function collectHistoryFilterAgents() {
        return CuttleChatSlash.SLASH_COMMANDS
            .filter((c) => c && c.stickySession)
            .map((c) => {
                const prefix = String(c.prefix || '').trim();
                const id = prefix.replace(/^\/+/, '').split(/\s/)[0].toLowerCase();
                return {
                    key: normalizeHistoryFilterKey('agent', id),
                    id,
                    label: c.label || id,
                    prefix,
                };
            })
            .filter((a, i, arr) => a.id && arr.findIndex((x) => x.id === a.id) === i);
    }

    function renderHistoryFilterChips() {
        const el = document.getElementById('historySearchFilterChips');
        if (!el) return;
        const chips = [];
        historySearchFilters.projects.forEach((p) => {
            chips.push({
                kind: 'project',
                key: p.key,
                label: '📁 ' + (p.name || 'Project'),
                title: p.path || p.name,
            });
        });
        historySearchFilters.agents.forEach((a) => {
            chips.push({
                kind: 'agent',
                key: a.key,
                label: (a.prefix || ('/' + a.id)).trim(),
                title: a.label || a.id,
            });
        });
        if (!chips.length) {
            el.hidden = true;
            el.innerHTML = '';
            return;
        }
        el.hidden = false;
        el.innerHTML = chips.map((c) => (
            '<span class="history-filter-chip" data-kind="' + escapeHtml(c.kind) + '" data-key="' + escapeHtml(c.key) + '" title="' + escapeHtml(c.title || c.label) + '">' +
            '<span class="history-filter-chip-label">' + escapeHtml(c.label) + '</span>' +
            '<button type="button" class="history-filter-chip-remove" data-history-filter-remove="' + escapeHtml(c.key) + '" aria-label="Remove filter">×</button>' +
            '</span>'
        )).join('');
        el.querySelectorAll('[data-history-filter-remove]').forEach((btn) => {
            btn.addEventListener('click', (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                removeHistorySearchFilter(btn.getAttribute('data-history-filter-remove'));
            });
        });
    }

    function removeHistorySearchFilter(key) {
        const k = String(key || '');
        historySearchFilters.projects = historySearchFilters.projects.filter((p) => p.key !== k);
        historySearchFilters.agents = historySearchFilters.agents.filter((a) => a.key !== k);
        renderHistoryFilterChips();
        searchChats();
    }

    function addHistoryProjectFilter(proj) {
        if (!proj) return;
        const key = proj.key || normalizeHistoryFilterKey('project', proj.path || proj.name);
        if (historySearchFilters.projects.some((p) => p.key === key)) return;
        historySearchFilters.projects.push({
            key,
            name: proj.name || '',
            path: proj.path || '',
        });
        renderHistoryFilterChips();
    }

    function addHistoryAgentFilter(agent) {
        if (!agent) return;
        const key = agent.key || normalizeHistoryFilterKey('agent', agent.id);
        if (historySearchFilters.agents.some((a) => a.key === key)) return;
        historySearchFilters.agents.push({
            key,
            id: agent.id,
            label: agent.label || agent.id,
            prefix: agent.prefix || ('/' + agent.id + ' '),
        });
        renderHistoryFilterChips();
    }

    function parseHistorySlashDraft(raw) {
        const text = String(raw || '');
        const m = text.match(/(?:^|\s)(\/[^\n]*)$/);
        if (!m) return null;
        const token = m[1];
        const start = text.length - token.length;
        return { token, start, before: text.slice(0, start) };
    }

    /** True when project name/path matches a slash query like "esc" → Escape Purgatory. */
    function historyProjectMatchesQuery(proj, query) {
        const q = String(query || '').trim().toLowerCase();
        if (!q) return true;
        const name = String((proj && proj.name) || '').toLowerCase();
        const path = String((proj && proj.path) || '').toLowerCase().replace(/\\/g, '/');
        const hay = (name + ' ' + path).trim();
        if (!hay) return false;
        if (hay.includes(q)) return true;
        // Word / path-segment prefixes: "esc" → "escape", "ep" → no unless present
        const parts = hay.split(/[^a-z0-9]+/).filter(Boolean);
        if (parts.some((w) => w.startsWith(q))) return true;
        // Compact acronym-ish: "ep" from "Escape Purgatory"
        if (q.length >= 2) {
            const initials = parts.map((w) => w[0]).join('');
            if (initials.startsWith(q) || initials.includes(q)) return true;
        }
        return false;
    }

    function historyAgentMatchesQuery(agent, query) {
        const q = String(query || '').trim().toLowerCase();
        if (!q) return true;
        const id = String((agent && agent.id) || '').toLowerCase();
        const label = String((agent && agent.label) || '').toLowerCase();
        const prefix = String((agent && agent.prefix) || '').toLowerCase();
        if (id.startsWith(q) || id.includes(q)) return true;
        if (label.includes(q)) return true;
        if (prefix.includes(q)) return true;
        return false;
    }

    function pushHistoryProjectPaletteItems(items, needle) {
        collectHistoryFilterProjects().forEach((p) => {
            if (!historyProjectMatchesQuery(p, needle)) return;
            items.push({
                kind: 'project',
                id: p.key,
                prefix: '/project',
                label: p.name + (p.path ? ' — ' + p.path : ''),
                apply: () => addHistoryProjectFilter(p),
            });
        });
    }

    function pushHistoryAgentPaletteItems(items, needle) {
        collectHistoryFilterAgents().forEach((a) => {
            if (!historyAgentMatchesQuery(a, needle)) return;
            items.push({
                kind: 'agent',
                id: a.key,
                prefix: (a.prefix || ('/' + a.id)).trim(),
                label: a.label,
                apply: () => addHistoryAgentFilter(a),
            });
        });
    }

    function buildHistorySlashPaletteItems(token) {
        const t = String(token || '').trim().toLowerCase();
        const items = [];
        const q = t.replace(/^\/+/, '');

        const projectCmd = /^(?:project|p|cd)(?:\s+(.*))?$/i.exec(q);
        const agentCmd = /^agent(?:\s+(.*))?$/i.exec(q);
        const isRoot = t === '/' || q === '';

        if (isRoot || /^(?:p|pr|pro|proj|project|cd)$/i.test(q)) {
            items.push({
                kind: 'hint',
                id: 'hint-project',
                prefix: '/project',
                label: 'Filter by project…',
                apply: null,
            });
        }
        if (isRoot || /^(?:a|ag|age|agen|agent)$/i.test(q)) {
            items.push({
                kind: 'hint',
                id: 'hint-agent',
                prefix: '/agent',
                label: 'Filter by agent…',
                apply: null,
            });
        }

        if (isRoot) {
            pushHistoryProjectPaletteItems(items, '');
            pushHistoryAgentPaletteItems(items, '');
        } else if (projectCmd) {
            // /project, /p esc, /cd Escape…
            pushHistoryProjectPaletteItems(items, String(projectCmd[1] || '').trim());
        } else if (agentCmd) {
            pushHistoryAgentPaletteItems(items, String(agentCmd[1] || '').trim());
        } else {
            // Bare query: /esc → Escape Purgatory, /cur → Cursor, etc.
            pushHistoryProjectPaletteItems(items, q);
            pushHistoryAgentPaletteItems(items, q);
        }

        // Dedupe by id
        const seen = new Set();
        return items.filter((it) => {
            const k = it.kind + ':' + it.id;
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        }).slice(0, 40);
    }

    function hideHistorySlashPalette() {
        const el = document.getElementById('historySearchSlashPalette');
        if (el) {
            el.hidden = true;
            el.innerHTML = '';
        }
        historySlashPaletteItems = [];
        historySlashPaletteIndex = 0;
    }

    function paintHistorySlashPalette(items) {
        const el = document.getElementById('historySearchSlashPalette');
        if (!el) return;
        historySlashPaletteItems = items || [];
        if (!historySlashPaletteItems.length) {
            el.hidden = false;
            el.innerHTML = '<div class="history-slash-palette-empty">No matching filters. Try /project or /agent</div>';
            return;
        }
        if (historySlashPaletteIndex >= historySlashPaletteItems.length) {
            historySlashPaletteIndex = 0;
        }
        let html = '';
        let lastKind = '';
        historySlashPaletteItems.forEach((it, idx) => {
            if (it.kind !== lastKind && (it.kind === 'project' || it.kind === 'agent')) {
                html += '<div class="history-slash-palette-section">' + escapeHtml(it.kind === 'project' ? 'Projects' : 'Agents') + '</div>';
                lastKind = it.kind;
            } else if (it.kind === 'hint' && lastKind !== 'hint') {
                html += '<div class="history-slash-palette-section">Filters</div>';
                lastKind = 'hint';
            }
            html += (
                '<button type="button" class="history-slash-palette-item' + (idx === historySlashPaletteIndex ? ' is-active' : '') + '" data-history-slash-idx="' + idx + '" role="option" aria-selected="' + (idx === historySlashPaletteIndex ? 'true' : 'false') + '">' +
                '<span class="hslp-prefix">' + escapeHtml(it.prefix || '') + '</span>' +
                '<span class="hslp-label">' + escapeHtml(it.label || '') + '</span>' +
                '</button>'
            );
        });
        el.hidden = false;
        el.innerHTML = html;
        el.querySelectorAll('[data-history-slash-idx]').forEach((btn) => {
            btn.addEventListener('mousedown', (ev) => {
                ev.preventDefault();
                const idx = Number(btn.getAttribute('data-history-slash-idx'));
                applyHistorySlashPaletteItem(idx);
            });
        });
    }

    function applyHistorySlashPaletteItem(idx) {
        const item = historySlashPaletteItems[idx];
        const input = document.getElementById('searchInput');
        if (!item || !input) return;
        const draft = parseHistorySlashDraft(input.value);
        if (item.kind === 'hint') {
            input.value = (draft ? draft.before : '') + item.prefix + ' ';
            hideHistorySlashPalette();
            onHistorySearchInput({ target: input });
            input.focus();
            return;
        }
        if (typeof item.apply === 'function') item.apply();
        input.value = draft ? draft.before.replace(/\s+$/, '') : '';
        hideHistorySlashPalette();
        searchChats();
        input.focus();
    }

    function onHistorySearchInput() {
        const input = document.getElementById('searchInput');
        const raw = (input && input.value) || '';
        const draft = parseHistorySlashDraft(raw);
        if (draft && draft.token.startsWith('/')) {
            const items = buildHistorySlashPaletteItems(draft.token);
            paintHistorySlashPalette(items);
            // While composing a slash filter, still apply text-before + active chips.
            searchChats({ slashDraft: draft });
            return;
        }
        hideHistorySlashPalette();
        searchChats();
    }

    function clearHistorySearch() {
        const input = document.getElementById('searchInput');
        if (!input) return;
        input.value = '';
        hideHistorySlashPalette();
        searchChats();
        input.focus({ preventScroll: true });
    }

    function onHistorySearchKeydown(ev) {
        const el = document.getElementById('historySearchSlashPalette');
        const open = el && !el.hidden && historySlashPaletteItems.length;
        if (!open) {
            if (ev.key === 'Escape' && historyFiltersActive()) {
                // keep chips; just blur handled elsewhere
            }
            return;
        }
        if (ev.key === 'ArrowDown') {
            ev.preventDefault();
            historySlashPaletteIndex = (historySlashPaletteIndex + 1) % historySlashPaletteItems.length;
            paintHistorySlashPalette(historySlashPaletteItems);
            return;
        }
        if (ev.key === 'ArrowUp') {
            ev.preventDefault();
            historySlashPaletteIndex = (historySlashPaletteIndex - 1 + historySlashPaletteItems.length) % historySlashPaletteItems.length;
            paintHistorySlashPalette(historySlashPaletteItems);
            return;
        }
        if (ev.key === 'Enter') {
            ev.preventDefault();
            applyHistorySlashPaletteItem(historySlashPaletteIndex);
            return;
        }
        if (ev.key === 'Escape') {
            ev.preventDefault();
            hideHistorySlashPalette();
            const input = document.getElementById('searchInput');
            const draft = parseHistorySlashDraft(input && input.value);
            if (input && draft) {
                input.value = draft.before.replace(/\s+$/, '');
                searchChats();
            }
        }
    }

    function filterTerminalHistory(searchTerm) {
        return getTerminalHistoryEntries().filter((entry) => {
            const s = entry.session || {};
            // Terminals rarely have project/agent; hide when project/agent filters are on.
            if (historyFiltersActive()) return false;
            const hay = [
                s.title,
                s.shell,
                s.id,
                formatChatDisplayId(s.id),
                'terminal',
            ].join(' ').toLowerCase();
            return !searchTerm || hay.includes(searchTerm);
        });
    }

    function paintSearchResults(sessions, terminalEntries, renderSession, emptyOpts) {
        const historyContainer = document.getElementById('chatHistory');
        if (!historyContainer) return;
        repaintHistorySearch = () => paintSearchResults(sessions, terminalEntries, renderSession, emptyOpts);
        const options = Object.assign({ showRefresh: false }, emptyOpts || {});

        const toEntry = (session) => {
            const isServer = session.session_name != null || session.message_count != null;
            const match = String(session.match || '').toLowerCase();
            return {
                sessionId: session.id,
                session: isServer ? null : session,
                updated: parseSessionTimestamp(session.last_message_time)
                    || parseSessionTimestamp(session.last_activity)
                    || Number(session.updated)
                    || 0,
                serverSession: isServer ? session : null,
                searchMatch: match === 'content' ? 'content' : (match === 'title' ? 'title' : ''),
            };
        };

        const titleSessions = [];
        const contentSessions = [];
        (sessions || []).forEach((session) => {
            if (String(session.match || '').toLowerCase() === 'content') contentSessions.push(session);
            else titleSessions.push(session);
        });

        const titleEntries = titleSessions.map(toEntry);
        const contentEntries = contentSessions.map(toEntry);
        const termEntries = terminalEntries || [];
        const all = titleEntries.concat(contentEntries).concat(termEntries);

        if (!all.length) {
            const icon = options.emptyIcon || '🔍';
            const title = options.emptyTitle || 'No results found';
            const text = options.emptyText || 'Try a different search term';
            CuttleChatHistoryView.paint(historyContainer, `
                ${createHistorySectionHeaderHTML('Search')}
                <div class="empty-state">
                    <div class="empty-state-icon">${icon}</div>
                    <div class="empty-state-title">${escapeHtml(title)}</div>
                    <div class="empty-state-text">${escapeHtml(text)}</div>
                </div>
            `);
            return;
        }

        const renderEntry = (entry, itemOpts) => {
            if (entry.isTerminal) return createTerminalHistoryItemHTML(entry.session);
            if (entry.serverSession) return renderSession(entry.serverSession, itemOpts);
            return renderSession(entry.session, itemOpts);
        };

        // Search uses match-kind sections (not project/recency groups) so a late
        // content-search wave cannot bury title hits under unrelated recent chats.
        let html = '';
        const paintTier = (key, label, entries) => {
            if (!entries.length) return;
            const group = {
                project: { key, id: null, name: label, path: '' },
                entries: entries.slice().sort((a, b) => {
                    const aStarred = entryIsStarred(a);
                    const bStarred = entryIsStarred(b);
                    if (aStarred && !bStarred) return -1;
                    if (bStarred && !aStarred) return 1;
                    const d = (b.updated || 0) - (a.updated || 0);
                    if (d !== 0) return d;
                    return String(b.sessionId).localeCompare(String(a.sessionId), undefined, {
                        numeric: true,
                    });
                }),
                latest: 0,
            };
            html += `<div class="history-section history-search-tier" data-search-tier="${escapeHtml(key)}" data-project-key="${escapeHtml(key)}">`;
            html += createHistorySectionHeaderHTML(label, {
                showRefresh: false,
                project: group.project,
                collapsible: false,
                collapsed: false,
                count: group.entries.length,
            });
            html += `<div class="history-section-body">${renderHistoryGroupBody(group, renderEntry)}</div>`;
            html += '</div>';
        };

        paintTier('__search_titles__', 'Matching titles', titleEntries.concat(termEntries));
        paintTier('__search_content__', 'In messages', contentEntries);

        CuttleChatHistoryView.paint(historyContainer, html, () => {
            syncHistoryRunningIndicators();
            syncHistoryUnreadIndicators();
            applyHistorySubagentExpandState();
            scheduleSlashChipCompactLabels(historyContainer);
            renderArchivedSection();
        });
    }

    function searchChats(opts) {
        closeHistoryItemMenu();
        opts = opts || {};
        const rawFull = (document.getElementById('searchInput') && document.getElementById('searchInput').value) || '';
        const clear = document.getElementById('historySearchClear');
        if (clear) clear.hidden = !rawFull;
        repaintHistorySearch = null;
        const draft = opts.slashDraft || parseHistorySlashDraft(rawFull);
        const composingSlash = !!(draft && String(draft.token || '').startsWith('/'));
        const rawForText = draft ? draft.before : rawFull;
        const searchTerm = String(rawForText || '').trim().toLowerCase();
        const filtersOn = historyFiltersActive();

        if (historySearchTimer) {
            clearTimeout(historySearchTimer);
            historySearchTimer = null;
        }
        if (historySearchAbort) {
            try { historySearchAbort.abort(); } catch (_) {}
            historySearchAbort = null;
        }
        if (!searchTerm && !filtersOn) {
            historySearchSeq += 1;
            historySearchLiveQuery = '';
            // Keep the slash filter palette open while the user is composing /project|/agent.
            if (!composingSlash) hideHistorySlashPalette();
            loadChatHistory();
            return;
        }

        historySearchLiveQuery = searchTerm || '__filters__';
        const seq = ++historySearchSeq;
        const terminalEntries = filterTerminalHistory(searchTerm);

        if (isAuthMode()) {
            const listed = ((window.CuttleAuth.getSessions && window.CuttleAuth.getSessions()) || [])
                .filter((s) => !isChatSessionDeleted(s && s.id))
                .filter((s) => sessionMatchesHistoryFilters(s));
            const titleHits = listed
                .filter((s) => !searchTerm || historySessionHaystack(s).includes(searchTerm))
                .map((s) => Object.assign({}, s, { match: 'title' }));
            paintSearchResults(
                titleHits,
                terminalEntries,
                createAuthHistoryItemHTML,
                titleHits.length || terminalEntries.length
                    ? null
                    : {
                        emptyIcon: '🔍',
                        emptyTitle: searchTerm ? 'Searching messages…' : 'No matching chats',
                        emptyText: searchTerm ? 'Looking through chat contents' : 'Try another /project or /agent filter',
                    },
            );

            if (!searchTerm) return;

            historySearchTimer = setTimeout(() => {
                if (seq !== historySearchSeq) return;
                const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                historySearchAbort = controller;
                const url = `/api/auth/sessions/search?q=${encodeURIComponent(String(rawForText || '').trim())}`;
                fetch(url, {
                    credentials: 'include',
                    cache: 'no-store',
                    signal: controller ? controller.signal : undefined,
                }).then((res) => (res.ok ? res.json() : null)).then((data) => {
                    if (seq !== historySearchSeq) return;
                    if (!data || !data.success || !Array.isArray(data.sessions)) {
                        if (!titleHits.length && !terminalEntries.length) {
                            paintSearchResults([], [], (s) => createAuthHistoryItemHTML(s), {
                                emptyIcon: '🔍',
                                emptyTitle: 'No results found',
                                emptyText: 'Try a different search term',
                            });
                        }
                        return;
                    }
                    const byId = new Map();
                    titleHits.forEach((s) => byId.set(String(s.id), s));
                    data.sessions.forEach((s) => {
                        if (isChatSessionDeleted(s && s.id)) return;
                        if (!sessionMatchesHistoryFilters(s)) return;
                        const key = String(s.id);
                        const existing = byId.get(key);
                        const matchKind = String(s.match || '').toLowerCase() === 'title' ? 'title' : 'content';
                        if (!existing) {
                            byId.set(key, Object.assign({}, s, { match: matchKind }));
                            return;
                        }
                        // Keep title rank; attach snippet from content hit if present.
                        if (matchKind === 'title') existing.match = 'title';
                        if (s.snippet && !existing.snippet) existing.snippet = s.snippet;
                        if (s.search_snippet && !existing.search_snippet) {
                            existing.search_snippet = s.search_snippet;
                        }
                    });
                    // Title matches first, then content; recency within each tier.
                    const merged = Array.from(byId.values()).sort((a, b) => {
                        const am = String(a.match || '') === 'title' ? 0 : 1;
                        const bm = String(b.match || '') === 'title' ? 0 : 1;
                        if (am !== bm) return am - bm;
                        const at = parseSessionTimestamp(a.last_message_time)
                            || parseSessionTimestamp(a.last_activity)
                            || 0;
                        const bt = parseSessionTimestamp(b.last_message_time)
                            || parseSessionTimestamp(b.last_activity)
                            || 0;
                        return bt - at;
                    });
                    paintSearchResults(
                        merged,
                        terminalEntries,
                        createAuthHistoryItemHTML,
                        {
                            emptyIcon: '🔍',
                            emptyTitle: 'No results found',
                            emptyText: 'Try a different search term',
                        },
                    );
                }).catch((err) => {
                    if (err && err.name === 'AbortError') return;
                    if (seq !== historySearchSeq) return;
                    if (!titleHits.length && !terminalEntries.length) {
                        paintSearchResults([], [], (s) => createAuthHistoryItemHTML(s), {
                            emptyIcon: '🔍',
                            emptyTitle: 'No results found',
                            emptyText: 'Try a different search term',
                        });
                    }
                });
            }, 280);
            return;
        }

        const sessions = JSON.parse(localStorage.getItem('chatSessions') || '{}');
        const filtered = Object.values(sessions)
            .filter((session) => localSessionMatchesSearch(session, searchTerm))
            .map((session) => {
                const titleHit = !searchTerm || historySessionHaystack(session).includes(searchTerm);
                return Object.assign({}, session, { match: titleHit ? 'title' : 'content' });
            })
            .sort((a, b) => {
                const am = a.match === 'title' ? 0 : 1;
                const bm = b.match === 'title' ? 0 : 1;
                if (am !== bm) return am - bm;
                return (Number(b.updated) || 0) - (Number(a.updated) || 0);
            });
        paintSearchResults(
            filtered,
            terminalEntries,
            (s) => createHistoryItemHTML(s),
            {
                emptyIcon: '🔍',
                emptyTitle: 'No results found',
                emptyText: 'Try a different search term or /project /agent filter',
            },
        );
    }
    
    // Message Handling
    /** Id of the follow-up currently being edited in the queue panel (or null). */
    let editingFollowupId = null;
    /** Prior paused flag for the item under edit (restored on save/cancel). */
    let editingFollowupWasPaused = false;

    function followupEditableText(item) {
        if (!item) return '';
        if (item.rawMessage != null) return String(item.rawMessage);
        return String(item.content || '');
    }

    function beginEditFollowup(id) {
        if (!id || !followupQueue.items.some((x) => x.id === id)) return;
        const item = followupQueue.items.find((x) => x.id === id);
        if (!item) return;
        editingFollowupId = id;
        editingFollowupWasPaused = !!item.paused;
        // Soft-pause so /followups/take cannot yank the message mid-edit.
        if (!item.paused) {
            CuttleFollowupQueue.setPaused(followupQueue, id, true);
            persistFollowupPut();
        }
        clearTimeout(followupDrainTimer);
        followupDrainTimer = null;
        renderFollowupQueue();
    }

    function finishEditFollowup(opts = {}) {
        const resumeDrain = opts.resumeDrain !== false;
        const id = editingFollowupId;
        editingFollowupId = null;
        if (id) {
            const item = followupQueue.items.find((x) => x.id === id);
            if (item) CuttleFollowupQueue.setPaused(followupQueue, id, editingFollowupWasPaused);
        }
        editingFollowupWasPaused = false;
        renderFollowupQueue();
        persistFollowupPut();
        if (
            resumeDrain
            && followupQueue.items.some((x) => !x.paused)
            && !isSessionGenerating()
        ) {
            scheduleFollowupDrain(180);
        }
    }

    function cancelEditFollowup() {
        finishEditFollowup({ resumeDrain: true });
    }

    function saveEditFollowup(id, nextText) {
        if (!id) return;
        const item = followupQueue.items.find((x) => x.id === id);
        if (!item) return;
        const text = String(nextText ?? '').trim();
        if (!text && !(item.attachments && item.attachments.length)) {
            // Empty with no attachments — drop it rather than keeping a blank send.
            editingFollowupId = null;
            editingFollowupWasPaused = false;
            removeFollowup(id);
            return;
        }
        item.rawMessage = text;
        item.content = formatMessageWithAttachments(text, item.attachments || []);
        finishEditFollowup({ resumeDrain: true });
    }

    function renderFollowupQueue() {
        const root = document.getElementById('followupQueue');
        if (!root) return;
        patchLiveSessionFollowupQueue(currentSessionId, followupQueue.items);
        if (!followupQueue.items.length) {
            editingFollowupId = null;
            root.hidden = true;
            root.innerHTML = '';
            syncHistoryUnreadIndicators();
            return;
        }
        if (editingFollowupId && !followupQueue.items.some((x) => x.id === editingFollowupId)) {
            editingFollowupId = null;
        }
        root.hidden = false;
        const items = followupQueue.items.map((item, idx) => {
            const isEditing = item.id === editingFollowupId;
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
                const chips = normalizeAgentSlashChips(parsed.chips, currentSessionId);
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
        const otherPaused = followupQueue.items.filter(
            (x) => x.paused && x.id !== editingFollowupId
        ).length;
        const titleBits = [`Queued · ${followupQueue.items.length}`];
        if (editingFollowupId) titleBits.push('editing · send paused');
        if (otherPaused) titleBits.push(`${otherPaused} paused`);
        root.innerHTML = `
            <div class="followup-queue-header">
                <span class="followup-queue-title">${titleBits.join(' · ')}</span>
                <button type="button" class="followup-queue-clear" id="followupQueueClear">Clear all</button>
            </div>
            <div class="followup-queue-list">${items}</div>
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
        if (!isAuthMode() || currentSessionId == null) return null;
        return toAuthDbSessionId(currentSessionId);
    }

    // Owned by chat_activity.js — thin adapter.
    function followupQueueFingerprint(items) {
        return CuttleChatActivity.followupQueueFingerprint(items);
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
        // Don't clobber an in-progress take, unsynced edits, a queue edit,
        // or an identical list (owned reconcile decision).
        if (followupQueue.dirty && !followupQueue.takeInFlight) {
            persistFollowupPut();
            return;
        }
        const r = CuttleFollowupQueue.reconcileServerList(followupQueue, list,
            { editingId: editingFollowupId, revision }, CuttleChatActivity);
        if (!r.applied && r.reason !== 'same') return;
        patchLiveSessionFollowupQueue(currentSessionId, followupQueue.items);
        renderFollowupQueue();
        if ((followupQueue.items.some((x) => !x.paused) || getSessionPrefs(currentSessionId)?.[followupClaimField]) && !isSessionGenerating() && !editingFollowupId) {
            scheduleFollowupDrain(250);
        }
        try { scheduleChatActivityBroadcast(); } catch (_) {}
    }

    function rememberUnsyncedQueue(sid, state) {
        updateSessionPrefs(sid, {[followupWriteField]: state.dirty ? {
            items: state.items, base: state.base, revision: state.revision,
        } : null});
    }

    async function persistFollowupAppend(item) {
        // Rebased revisioned PUT also handles appends. Keeping one mutation path
        // means a failed append retains its intent and cannot be dropped by a GET.
        return persistFollowupPut();
    }

    async function persistFollowupPut(sid = followupAuthSid(), state = followupQueue) {
        if (!sid) return true;
        CuttleFollowupQueue.markDirty(state);
        rememberUnsyncedQueue(sid, state);
        if (state.write) return state.write;
        const write = sessionMutations.enqueue(sid + ':followups', async () => {
            for (let attempt = 0; attempt < 4; attempt++) {
                const version = state.editVersion;
                let desired = JSON.parse(JSON.stringify(state.items));
                const base = JSON.parse(JSON.stringify(state.base));
                const response = await fetch('/api/auth/sessions/' + sid + '/followups', {
                    method: 'PUT', credentials: 'include', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({followups: desired, revision: state.revision}),
                });
                const data = await response.json();
                if (response.status === 409 && data.conflict) {
                    state.revision = data.revision;
                    state.items = CuttleFollowupQueue.mergeEdits(base, state.items, data.followups);
                    state.base = data.followups;
                    rememberUnsyncedQueue(sid, state);
                    continue;
                }
                if (!response.ok || !data.success) throw new Error(data.error || 'Queue sync failed');
                state.revision = data.revision;
                state.base = data.followups || desired;
                CuttleFollowupQueue.markClean(state, version);
                rememberUnsyncedQueue(sid, state);
                patchLiveSessionFollowupQueue(sid, state.base);
                if (!state.dirty) {
                    state.items = state.base;
                    if (followupQueue === state && sessionIdsEqual(currentSessionId, sid)) renderFollowupQueue();
                    return true;
                }
            }
            return false;
        }).catch(() => false).finally(() => { state.write = null; });
        state.write = write;
        return write;
    }

    function enqueueFollowup(message, opts = {}) {
        const item = CuttleFollowupQueue.enqueue(followupQueue, {
            content: message,
            attachments: opts.attachments,
            rawMessage: opts.rawMessage,
            paused: opts.paused,
        }, { now: () => Date.now(), rand: () => Math.random() });
        renderFollowupQueue();
        persistFollowupAppend(item);
        return item;
    }

    const STEER_AGENT_LABELS = { codex: 'Codex', muse: 'Muse Code' };
    let steeringText = '';

    /**
     * Codex / Muse Code accept input while a turn runs — hand the follow-up to
     * the live turn instead of queueing it. The server persists the user row;
     * resolves false (caller queues) when nothing steerable is running.
     */
    async function trySteerRunningTurn(message, attachments) {
        const sid = currentSessionId;
        const text = String(message || '').trim();
        if (sid == null || sid === '' || !text) return false;
        if (Array.isArray(attachments) && attachments.length) return false;
        if (steeringText === text) return true;
        steeringText = text;
        let data = null;
        try {
            const resp = await fetch('/api/chat-steer', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sid, message: text }),
            });
            data = await resp.json().catch(() => null);
        } catch (_) {
            data = null;
        } finally {
            steeringText = '';
        }
        if (!data || !data.steered) return false;
        if (sessionIdsEqual(currentSessionId, sid)) {
            addMessageToUI(text, 'user', {
                timestamp: Date.now(),
                steered: true,
                steered_agent: data.agent,
            });
            transcriptNav.autoScrollChatToBottom(true);
            saveChatSession(text, 'user');
        }
        recordPromptHistory(text);
        const label = STEER_AGENT_LABELS[data.agent] || data.agent || 'agent';
        (window.showToast || function () {})(`Sent into the running ${label} turn.`, 'success');
        return true;
    }

    function toggleFollowupPaused(id) {
        if (!id) return;
        const current = followupQueue.items.find((x) => x.id === id);
        if (!current) return;
        const item = CuttleFollowupQueue.setPaused(followupQueue, id, !current.paused);
        renderFollowupQueue();
        persistFollowupPut();
        if (!item.paused && !isSessionGenerating()) scheduleFollowupDrain(120);
    }

    function removeFollowup(id) {
        if (!id) return;
        if (editingFollowupId === id) {
            editingFollowupId = null;
            editingFollowupWasPaused = false;
        }
        CuttleFollowupQueue.removeItem(followupQueue, id);
        renderFollowupQueue();
        persistFollowupPut();
    }

    function clearFollowupQueue() {
        editingFollowupId = null;
        editingFollowupWasPaused = false;
        CuttleFollowupQueue.clearAll(followupQueue);
        renderFollowupQueue();
        persistFollowupPut();
    }

    function scheduleFollowupDrain(delayMs = 120) {
        if (editingFollowupId) return;
        clearTimeout(followupDrainTimer);
        followupDrainTimer = setTimeout(() => {
            drainNextFollowup();
        }, delayMs);
    }

    /**
     * Merge every pending follow-up into one outbound turn so the agent sees
     * the full queue at once (instead of N serial replies).
     * Sticky slash prefix from the first item is kept on the combined message
     * so /cursor · /claude routing still works.
     */
    // Owned by chat_activity.js — DOM/state gather, domain decides.
    function combineFollowupBatch(items) {
        return CuttleChatActivity.combineFollowupBatch(items, {
            stickyPrefixFor: (raw) => {
                const sticky = getStickySlashCommandFromMessage(raw);
                return sticky ? sticky.prefix : '';
            },
            formatWithAttachments: formatMessageWithAttachments,
        });
    }

    async function drainNextFollowup() {
        // Wait until this chat is fully idle (no local stream, no remote run).
        if (editingFollowupId) return;
        healStaleGeneratingState();
        if (isSessionGenerating()) return;
        // Re-check after heal — edit may have started while we were waiting.
        if (editingFollowupId) return;
        const origin = currentSessionId;
        const state = followupQueue;
        if (state.takeInFlight) return;
        let batch = [];
        const sid = followupAuthSid();
        if (sid) {
            if (state.dirty && !await persistFollowupPut()) return;
            if (editingFollowupId || !sessionIdsEqual(currentSessionId, origin) || followupQueue !== state) return;
            const claimId = getSessionPrefs(sid)?.[followupClaimField] || ('queue-' + Date.now() + '-' + Math.random().toString(36).slice(2));
            updateSessionPrefs(sid, {[followupClaimField]: claimId});
            const takeVersion = state.editVersion;
            CuttleFollowupQueue.beginTake(state);
            try {
                const response = await fetch('/api/auth/sessions/' + sid + '/followups/take', {
                    method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({revision: state.revision, claim_id: claimId}),
                });
                const data = await response.json();
                if (response.status === 409) {
                    updateSessionPrefs(sid, {[followupClaimField]: null});
                    state.revision = data.revision;
                    state.items = state.editVersion !== takeVersion
                        ? CuttleFollowupQueue.mergeEdits(state.base, state.items, data.followups || [])
                        : data.followups || [];
                    state.base = data.followups || [];
                    rememberUnsyncedQueue(sid, state);
                    return;
                }
                if (!response.ok || !data.success) return;
                const editedDuringTake = state.editVersion !== takeVersion;
                const editedItems = state.items;
                state.revision = data.revision;
                state.items = data.remaining || [];
                state.base = state.items.slice();
                batch = data.followups || [];
                if (editedDuringTake || editingFollowupId || !sessionIdsEqual(currentSessionId, origin) || followupQueue !== state) {
                    // Restore to the captured chat when navigation or editing
                    // races the claim; retain the write if the network fails.
                    state.items = editedDuringTake ? editedItems : [...batch, ...state.items];
                    state.editVersion++;
                    state.dirty = true;
                    rememberUnsyncedQueue(sid, state);
                    updateSessionPrefs(sid, {[followupClaimField]: null});
                    await persistFollowupPut(sid, state);
                    return;
                }
                updateSessionPrefs(sid, {[followupClaimField]: null});
            } catch (_) { return; }
            finally { CuttleFollowupQueue.endTake(state); }
        } else {
            batch = CuttleFollowupQueue.resolveTake(state, editingFollowupId, null, CuttleChatActivity);
        }
        // Keep editing state if the edited item remained in the queue.
        if (editingFollowupId && !followupQueue.items.some((x) => x.id === editingFollowupId)) {
            editingFollowupId = null;
            editingFollowupWasPaused = false;
        }
        renderFollowupQueue();
        const combined = combineFollowupBatch(batch);
        if (!combined) return;
        const atts = combined.attachments || [];
        const displayMessage = combined.displayMessage;
        const message = combined.message;
        const chatArea = document.getElementById('chatArea');
        if (chatArea && chatArea.style.display === 'none') {
            document.getElementById('welcomeScreen').style.display = 'none';
            chatArea.style.display = 'flex';
            if (typeof window.__cuttlePinChatLayout === 'function') window.__cuttlePinChatLayout();
        }
        addMessageToUI(displayMessage, 'user', { timestamp: Date.now(), attachments: atts });
        transcriptNav.autoScrollChatToBottom(true);
        saveChatSession(displayMessage, 'user', { attachments: atts });
        recordPromptHistory(message);
        await processMessage(message, { attachments: atts });
    }

    async function requestServerCancelCurrentRun(opts = {}) {
        const sid = currentSessionId;
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
            window.CuttleCompletionNotifications.broker().cancel(currentSessionId || completionNotificationPendingId).catch(() => {});
        }
        const watchIds = runningWatchJobIds();
        const cancelJobs = (!inflight && watchIds.length > 0) || isProjectShellTurn(inflight);
        CuttleStopState.requestStop(stopState);
        inFlightUserMessage = null;
        clearTimeout(followupDrainTimer);
        followupDrainTimer = null;
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
        if (currentSessionId) setHistorySessionRunning(currentSessionId, false);
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
        const controlLane = !!opts.controlLane || isImmediateControlLaneMessage(message);
        const stickyCmd = getStickySlashCommandFromMessage(message);
        let responseData = null;
        // This turn's generation-lock token: end calls below release only
        // if no newer turn (or detach) has since claimed generation.
        let turnGenToken = null;
        // Control-lane replies must not clobber an in-flight agent turn's stop/abort state.
        const prevInFlight = inFlightUserMessage;
        const prevController = activeRequestController;
        if (!controlLane) {
            if (window.CuttleCompletionNotifications) window.CuttleCompletionNotifications.broker().begin(currentSessionId || completionNotificationPendingId);
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

        const replySlash = slashCommandMetaFromUserMessage(message);

        // New turn owns the typing indicator — drop the previous turn's
        // sub-agent orbs first so they never bleed into a turn that uses none.
        // Live-status / stream syncs repopulate when this turn spawns sub-agents.
        try { subagentLaunchers.clearLive(); } catch (_) {}
        // Add typing indicator — pass slash meta so chip appears immediately
        const pendingSlash = replySlash
            ? pendingSlashForTypingIndicator(replySlash, stickyCmd)
            : null;
        turnBadgeBySession.set(String(canonicalizeChatSessionId(currentSessionId) || ''), pendingSlash);
        addTypingIndicator(pendingSlash);

        let skipFollowupDrain = false;
        let controlController = null;
        // Declared outside try so catch/finally can still gate paints after a switch.
        let boundSessionId = canonicalizeChatSessionId(currentSessionId);
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
            currentSessionId,
            isViewing: (id) => isViewingSession(id),
        });
        try {
            await ensureAuthChatSession();
            // Send to API with project context (stream for status updates; JSON fallback if SSE fails)
            const requestBody = {
                message: message,
                session_id: authSessionIdForRequest(),
                stream: true,
            };
            // Stable per-send id so a stream=false retry cannot mint a second
            // chat when this first POST still has a null session_id (CH-000559).
            requestBody.chat_request_id =
                'ct_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
            // Pin this turn to the chat that sent it. Switching chats updates
            // currentSessionId; recovery/paint must keep using the origin id
            // or the reply lands in the newly opened transcript (phone bug).
            boundSessionId = canonicalizeChatSessionId(
                requestBody.session_id != null && requestBody.session_id !== ''
                    ? requestBody.session_id
                    : currentSessionId
            );
            const agentOverride = stickyAgentOverrideForRequest(message);
            if (agentOverride) requestBody.sticky_agent = agentOverride;
            // New-chat model/effort picks (dirty, no session yet) must travel
            // with this POST — adopt-time pin POSTs race the agent + badge.
            attachAgentIdentityToRequest(requestBody);
            if (controlLane) {
                // Exactly-once control-lane idempotency key (server dedupes).
                // Prefer a stable id for this turn; never regenerate on transport retry.
                if (!opts.controlRequestId) {
                    requestBody.control_request_id =
                        (window.CuttleSupervised
                            && window.CuttleSupervised.newControlRequestId
                            && window.CuttleSupervised.newControlRequestId())
                        || (`cr_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`);
                } else {
                    requestBody.control_request_id = opts.controlRequestId;
                }
                inFlightControlRequestId = requestBody.control_request_id;
                // Stamp the latest unmarked user bubble so sync can claim by control id.
                const userEl = claimUnmarkedMessageEl('user', message);
                if (userEl) {
                    userEl.dataset.controlRequestId = requestBody.control_request_id;
                }
                // Control lane: JSON only — avoid SSE detach/retry double POST.
                requestBody.stream = false;
            }
            if (attachments.length) {
                requestBody.attachments = CuttleChatAttachments.requestAttachmentPayload(attachments);
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

                    // Bytes/framing live in chat_stream.js; the page keeps
                    // request, classification, session, paint, and turn
                    // effects. onEvents applies the whole batch, then
                    // reports stop — last final frame wins, as before.
                    const streamOutcome = await CuttleChatStream.readEvents(response.body, {
                        holdMs: STREAM_HOLD_MAX_MS,
                        readTimeoutMs: STREAM_READ_TIMEOUT_MS,
                        signal: requestSignal,
                        onEvents: (events) => {
                            for (const ev of events) {
                                try {
                                    // Owned by chat_pending_result.js — event
                                    // classification; the page applies paint,
                                    // session-adopt, and finalResult effects.
                                    const streamEv = CuttleChatPendingResult.classifyStreamEvent(ev);
                                    if (streamEv.kind === 'status') {
                                        sawProgress = true;
                                        if (canPaintTurnHere()) updateTypingStatus(streamEv.message);
                                    } else if (streamEv.kind === 'session') {
                                        const sid = canonicalizeChatSessionId(streamEv.sessionId);
                                        requestBody.session_id = sid;
                                        if (boundSessionId == null) boundSessionId = sid;
                                        // Never yank the open chat to a different id mid-turn.
                                        // turnNavGen gates zombies after New Chat (welcome is
                                        // currentSessionId == null — must not re-adopt chat A).
                                        if (
                                            !CuttleTurnGuard.isStale(turnNavGen, turnGeneration)
                                            && (
                                                currentSessionId == null
                                                || sessionIdsEqual(currentSessionId, sid)
                                                || (boundSessionId != null && isViewingSession(boundSessionId))
                                            )
                                        ) {
                                            adoptChatSessionId(sid);
                                        }
                                    } else if (streamEv.kind === 'busy') {
                                        finalResult = streamEv.result;
                                    } else if (streamEv.kind === 'query') {
                                        sawProgress = true;
                                        if (canPaintTurnHere() && ev.slash_command) {
                                            rememberTurnBadge([], { active: true, slash_command: ev.slash_command });
                                            refreshTypingIndicatorHeaderBadges();
                                        }
                                        if (streamEv.reportUrl && canPaintTurnHere()) {
                                            updateTypingIndicatorQueryLink(streamEv.reportUrl);
                                            const qid = streamEv.queryId || '';
                                            (window.showToast || function() {})('Running…', 'info', {
                                                linkUrl: qid
                                                    ? ('/query_log.html?id=' + encodeURIComponent(qid))
                                                    : streamEv.reportUrl,
                                                linkText: 'View query log'
                                            });
                                        }
                                    } else if (streamEv.kind === 'response') {
                                        finalResult = ev;
                                    }
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
                        consumeParkedChatResult(
                            finalResult.session_id
                            || turnSessionId()
                            || requestBody.session_id
                        );
                        return {
                            ...finalResult,
                            success: finalResult.success,
                            response: finalResult.response,
                            session_id: finalResult.session_id,
                            type: finalResult.response_type,
                            query_id: finalResult.query_id,
                            report_url: finalResult.report_url,
                            cursor_run: finalResult.cursor_run || null,
                            usage: finalResult.usage || null,
                            // Must forward busy so processMessage can enqueue
                            // the turn instead of rendering a "Still working" bubble.
                            busy: !!finalResult.busy,
                        };
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
                        wait: (sid) => collectPendingResult(sid, {
                            waitMs: 90 * 60 * 1000,
                            pollMs: pendingPollMs,
                            navGen: turnNavGen,
                        }),
                        retries: (sid) => recoverChatResultWithRetries(sid, 5, 1600),
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
                data = await fetchChatPayload(controlLane ? false : true);
            } catch (streamErr) {
                if (streamErr && streamErr.name === 'AbortError') throw streamErr;
                if (controlLane) {
                    // Do not transport-retry control commands — server may have
                    // already applied; recover from history / live status instead.
                    console.warn('[Cuttle Chat] Control-lane request failed; not retrying POST:', streamErr);
                    const sid = turnSessionId() || requestBody.session_id;
                    // Owned by chat_pending_result.js — recovery decision; the
                    // page owns transport/guards.
                    const laneOutcome = await CuttleChatPendingResult.recoverControlLaneFailure(
                        sid, streamErr, {
                            retries: (rsid) => recoverChatResultWithRetries(rsid, 3, 800),
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
                            recoverServer: (opts) => recoverAfterFlaskRestart(opts),
                            wait: (wsid) => collectPendingResult(wsid, {
                                waitMs: 90 * 60 * 1000,
                                navGen: turnNavGen,
                            }),
                            retries: (rsid) => recoverChatResultWithRetries(rsid, 5, 1600),
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

            if (!canPaintTurnHere()) {
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
                        currentSessionId != null
                        && sessionIdsEqual(currentSessionId, readySid)
                        && openTranscriptMissingThisTurnReply()
                        && readyBody
                    ) {
                        addMessageToUI(readyBody, 'assistant', {
                            report_url: data.report_url,
                            timestamp: Date.now(),
                        });
                    }
                }
            } else if (stopState.userStopped) {
                consumeCancelledAgentReply(data && (data.response || data.output));
                // Stop already cleaned UI; don't append a partial/error reply.
            } else if (consumeCancelledAgentReply(data && (data.response || data.output))) {
                // Late cancel confirmation without a local Stop click (other pane / dropped stream).
            } else if (
                !data.busy
                && (
                    data.ui === 'system'
                    || data.notice
                    || (
                        data.preferred_model
                        && /Model set to/i.test(String(data.response || data.output || ''))
                    )
                )
            ) {
                const notice = String(data.notice || '').trim()
                    || ('⚡ Model: ' + preferredModelLabel(data.preferred_model));
                if (notice) addSystemMessage(notice, { persist: true, kind: 'model-change' });
                if (data.session_id) adoptChatSessionId(data.session_id);
            } else if (data.busy && !controlLane) {
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
                const sid = currentSessionId != null ? currentSessionId : data.session_id;
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
            } else if (data.no_reply || (data.success && !(String(data.response || data.output || '').trim()))) {
                // Detached stream with no parked payload yet — don't fake
                // "No response received." Message sync / pending poll will
                // attach the real reply when the worker finishes.
                if (data.session_id) adoptChatSessionId(data.session_id);
                startMessageSync();
                const sid = currentSessionId != null ? currentSessionId : data.session_id;
                Promise.resolve(fetchChatLiveStatus(sid)).then((live) => {
                    if (liveStatusLooksActive(live) || (live && live.generating)) {
                        updateRemoteWaitingFromMessages([], live);
                    }
                }).catch(() => {});
            } else if (data.success && data.response) {
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
                        notifyAssistantResponseReady(currentSessionId || generation.localSessionId, {
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
                notifyAssistantResponseReady(currentSessionId || generation.localSessionId, {
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
                    notifyAssistantResponseReady(currentSessionId || generation.localSessionId, {
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
                        currentSessionId || generation.localSessionId || turnSessionId(),
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
            clearComposerDraft(currentSessionId);
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
            queuedMessages: followupQueue.items.map((x) => normalizeMessageContentForMatch(x.content)),
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
        const controlLane = isImmediateControlLaneMessage(message);
        if (!controlLane) {
            beginLocalGeneration();
            inFlightUserMessage = message;
        }
        await processMessage(message, { controlLane });
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
        getSessionId: () => currentSessionId,
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
        currentSessionId: () => currentSessionId,
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
            reportSessionToShell(currentSessionId);
            return;
        }
        // SSE can stall on Electron/HTTPS while the agent keeps updating live-status.
        // Poll with short timeouts so typing text still moves and connections free up.
        let lastStatus = '';
        let stagnantTicks = 0;
        streamStatusPollTimer = setInterval(async () => {
            if (!isLoadingThisSession() || currentSessionId == null) {
                clearStreamStatusPoll();
                return;
            }
            if (pageIsBackgrounded()) return;
            const live = await fetchChatLiveStatus(currentSessionId);
            if (!live) return;
            if (live.status) {
                if (live.status === lastStatus) stagnantTicks += 1;
                else {
                    stagnantTicks = 0;
                    lastStatus = live.status;
                }
                updateTypingStatus(live.status);
            }
            try { subagentLaunchers.syncLive(live.subagents); } catch (_) {}
            if (live.report_url) updateTypingIndicatorQueryLink(live.report_url);

            const stillGoing = liveStatusLooksActive(live);
            if (!stillGoing) {
                // Run finished server-side — pull the reply even if SSE/pending stalled.
                clearStreamStatusPoll();
                try {
                    window.CuttleNetDebug && window.CuttleNetDebug.event(
                        'status-poll-idle',
                        'recovering session=' + currentSessionId
                    );
                } catch (_) {}
                await syncSessionMessagesFromServer();
                if (isLoadingThisSession()) {
                    const recovered = await recoverChatResultFromServer(currentSessionId);
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
                            notifyAssistantResponseReady(currentSessionId, { fromSync: true });
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
            if (stagnantTicks >= 24) { // ~24 * 3.5s ≈ 84s
                stagnantTicks = 0;
                try {
                    await syncSessionMessagesFromServer();
                } catch (_) {}
            }
        }, 3500);
    }
    // Companion-HTTP target for an HTTPS pool stall (armConnectingWatchdog).
    // Pure except for the injected getDesktopConfig: resolves to
    // { probeUrl, destBase, httpPort } or null (fail closed — the caller
    // shows a generic message instead of guessing an unrelated endpoint).
    // Local paired mode requires matching host and HTTPS port. Explicit
    // single targets never auto-downgrade. Only true legacy
    // default-mode (exact https://127.0.0.1|localhost:8080 with no config
    // available, e.g. plain browser) keeps the documented :8000 companion.
    function poolStallSameHost(cfgHost, normHost, loopback127) {
        const cfgNorm = String(cfgHost || '').trim().replace(/^\[|\]$/g, '').toLowerCase();
        if (!cfgNorm) return false;
        if (cfgNorm === normHost) return true;
        return !!(loopback127 && (cfgNorm === '127.0.0.1' || cfgNorm === 'localhost'));
    }
    function poolStallTarget(host, httpPort) {
        return {
            probeUrl: `http://${host}:${httpPort}/api/status`,
            destBase: `http://${host}:${httpPort}`,
            httpPort,
        };
    }
    async function poolStallHttpTarget({ origin, getDesktopConfig }) {
        const m = /^https:\/\/(\[[^\]]+\]|[^/:]+):(\d+)$/i.exec(String(origin || ''));
        if (!m) return null;
        const host = m[1];
        const httpsPort = Number(m[2]);
        if (!Number.isSafeInteger(httpsPort) || httpsPort < 1 || httpsPort > 65535) return null;
        const normHost = host.replace(/^\[|\]$/g, '').toLowerCase();
        const loopback127 = normHost === '127.0.0.1' || normHost === 'localhost';
        let cfg = null;
        try {
            cfg = typeof getDesktopConfig === 'function' ? await getDesktopConfig() : null;
        } catch (_) {
            cfg = null;
        }
        // Explicit connections never change protocol, including saved configs
        // that contain companion metadata from an older desktop build.
        const ep = cfg ? cfg.endpoint : null;
        if (ep && ep.kind === 'single') return null;
        // A local paired target knows both owner-resolved listeners.
        if (cfg && Number(cfg.httpsPort) === httpsPort
            && poolStallSameHost(cfg.host, normHost, loopback127)) {
            const httpPort = Number(cfg.httpPort);
            if (Number.isSafeInteger(httpPort) && httpPort >= 1 && httpPort <= 65535) {
                return poolStallTarget(host, httpPort);
            }
            return null;
        }
        // Plain-browser legacy compatibility: no desktop config is available.
        if (!cfg && loopback127 && httpsPort === 8080) {
            return poolStallTarget('127.0.0.1', 8000);
        }
        return null;
    }
    function armConnectingWatchdog() {
        clearConnectingWatchdog();
        connectingWatchdogTimer = setTimeout(async () => {
            connectingWatchdogTimer = null;
            const el = document.getElementById('typing-status')
                || document.querySelector('#typing-indicator .typing-status');
            if (!el) return;
            const text = (el.textContent || '').trim();
            if (text !== 'Connecting...' && text.indexOf('Still connecting') < 0) return;

            // Prefer live generation status over a health probe (SSE may be stalled).
            if (currentSessionId != null) {
                const live = await fetchChatLiveStatus(currentSessionId);
                if (live && (live.active || live.generating) && live.status) {
                    updateTypingStatus(live.status);
                    if (live.report_url) updateTypingIndicatorQueryLink(live.report_url);
                    return;
                }
            }

            el.textContent = 'Still connecting — checking server…';
            try {
                const r = await fetchWithTimeout('/api/status', {
                    credentials: 'include',
                    cache: 'no-store',
                }, 4000);
                if (!document.getElementById('typing-indicator')) return;
                const cur = document.getElementById('typing-status')
                    || document.querySelector('#typing-indicator .typing-status');
                if (!cur || (cur.textContent || '').indexOf('Still connecting') < 0) return;
                if (r.ok) {
                    cur.textContent = 'Server is up — waiting for chat stream…';
                } else {
                    cur.textContent = `Server health HTTP ${r.status} — try refreshing or restart Flask`;
                }
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
                    const stallTarget = await poolStallHttpTarget({
                        origin: window.location.origin || '',
                        getDesktopConfig: inElectron && window.electron.desktop && window.electron.desktop.getConfig
                            ? () => window.electron.desktop.getConfig()
                            : null,
                    });
                    if (stallTarget) {
                        cur.textContent = `HTTPS pool stalled — switching to HTTP portal (:${stallTarget.httpPort})…`;
                        try {
                            const probe = await fetchWithTimeout(stallTarget.probeUrl, {
                                cache: 'no-store',
                                mode: 'cors',
                            }, 3000);
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
                    cur.textContent = inElectron
                        ? 'Cannot reach Cuttle API — Electron connection pool stalled. Quit Cuttle.exe fully and reopen (or use the HTTP portal for your connected host).'
                        : 'Cannot reach Cuttle API quickly — stream may be stalled. Try Stop, or open the HTTP portal for this host.';
                }
            }
        }, 12000);
    }

    function updateTypingStatus(statusText) {
        const next = statusText || 'Connecting...';
        chatVoice.onAgentStatus(next);
        const indicator = document.getElementById('typing-indicator');
        const el = indicator ? indicator.querySelector('.typing-status') : document.getElementById('typing-status');
        if (el) {
            if (el.textContent === next) {
                if (next !== 'Connecting...' && next.indexOf('Still connecting') < 0) {
                    clearConnectingWatchdog();
                }
                return;
            }
            clearConnectingWatchdog();
            el.textContent = next;
            el.style.display = 'block';
            el.style.visibility = 'visible';
            transcriptNav.autoScrollChatToBottom();
        } else {
            clearConnectingWatchdog();
        }
        if (!statusText || statusText === 'Connecting...') {
            armConnectingWatchdog();
        }
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
            if (!spec.session_id && currentSessionId != null && currentSessionId !== '') {
                spec.session_id = String(currentSessionId);
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
                reloadHistoryListKeepingSearch();
            }
            const urlParams = new URLSearchParams(window.location.search);
            const wanted = urlParams.get('chat') || urlParams.get('session');
            if (!wanted || !isAuthMode()) return;
            // The restore already gave up and handed the user a new chat — don't
            // yank them into the old one when auth finally settles.
            if (chatRestoreAbandoned) return;
            // Only settle the deep link before a chat is open. Auth refreshes
            // fire repeatedly; once the user is reading a chat, re-reading the
            // URL could yank them into a different one mid-click.
            if (currentSessionId != null) {
                if (String(currentSessionId) === String(wanted) && !messageSyncTimer) {
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
        loadChatHistory();
        
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
                saveComposerDraft(currentSessionId || 'new');
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
                saveComposerDraft(currentSessionId || 'new');
            });
            wireComposerEmoticons(chatInput);
            chatInput.style.height = 'auto';
            LOG('DOMContentLoaded: chat input keydown listener attached');
        } else {
            LOG_ERR('DOMContentLoaded: chatInput not found');
        }

        // Flush drafts on refresh / Electron restart / pane unload.
        const flushComposerDraft = () => {
            try { saveComposerDraft(currentSessionId || 'new'); } catch (_) {}
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
            closeHistoryItemMenu();
        }, true);
        window.addEventListener('resize', closeHistoryItemMenu);
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
            historyScroll.addEventListener('scroll', closeHistoryItemMenu, { passive: true });
        }
        setupHistoryDeleteModal();
        setupGitPushConfirmModal();
        setupHistoryRenameModal();

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
            if (currentSessionId != null) {
                markChatSessionRead(currentSessionId);
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
                    if (changed && shellPaneFocused && currentSessionId != null) {
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
                    if (sid == null || currentSessionId == null) return;
                    if (!sessionIdsEqual(sid, currentSessionId)) return;
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
            if (currentSessionId != null) reportSessionToShell(currentSessionId);
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
    window.toggleHistoryGroupExpand = revealMoreHistoryGroup; // back-compat alias
    window.revealMoreHistoryGroup = revealMoreHistoryGroup;
    window.collapseHistoryGroupReveal = collapseHistoryGroupReveal;
    window.toggleHistoryProjectCollapse = toggleHistoryProjectCollapse;
    window.toggleHistorySubagentGroup = toggleHistorySubagentGroup;
    window.toggleHistoryProjectCollapseKey = function (event) {
        if (!event) return;
        const key = event.key || event.code;
        if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar' && key !== 'Space') return;
        event.preventDefault();
        toggleHistoryProjectCollapse(event);
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
    window.chatPageRequestDeleteSession = requestDeleteChatSession;
    window.chatPageCancelDeleteSession = cancelDeleteChatSession;
    window.chatPageConfirmDeleteSession = confirmDeleteChatSession;
    window.chatPageDeleteSession = deleteChatSession;
    window.chatPageRenameSession = renameChatSession;
    window.chatPageToggleStarSession = toggleStarChatSession;
    window.chatPageToggleCurrentStar = toggleCurrentChatStar;
    window.chatPageToggleCurrentStarFromMenu = toggleCurrentChatStarFromMenu;
    window.chatPageMarkUnreadSession = markSessionUnreadManual;
    window.chatPageMarkCurrentUnreadFromMenu = markCurrentSessionUnreadFromMenu;

    window.chatPageExportSession = exportChatSession;
    window.chatPageGitPush = pushCurrentChatProject;

    async function openGitPushSubmenu(triggerBtn) {
        const wrap = triggerBtn && triggerBtn.closest
            ? triggerBtn.closest('[data-git-push-wrap]')
            : null;
        if (!wrap) return;
        const submenu = wrap.querySelector('[data-git-push-submenu]');
        if (!submenu) {
            pushCurrentChatProject();
            return;
        }

        const pushFromRepos = (repos) => {
            applyGitPushMenuMode(wrap, gitPushIsMultiRepo(repos));
            if (!repos.length) {
                pushCurrentChatProject();
                return true;
            }
            if (repos.length === 1) {
                pushCurrentChatProject(null, repos[0].repo_root);
                return true;
            }
            return false;
        };

        // Single-repo (or none): push immediately — no caret / submenu flash.
        const known = knownProjectGitRepos();
        if (known && !gitPushIsMultiRepo(known)) {
            pushFromRepos(known);
            return;
        }

        const opening = submenu.hidden;
        document.querySelectorAll('[data-git-push-submenu]').forEach((el) => {
            if (el !== submenu) el.hidden = true;
        });
        document.querySelectorAll('[data-git-push-trigger]').forEach((el) => {
            el.setAttribute('aria-expanded', 'false');
        });
        if (!opening) {
            submenu.hidden = true;
            triggerBtn.setAttribute('aria-expanded', 'false');
            return;
        }

        if (known && gitPushIsMultiRepo(known)) {
            applyGitPushMenuMode(wrap, true);
            triggerBtn.setAttribute('aria-expanded', 'true');
            submenu.innerHTML = known.map((r) => {
                const root = String(r.repo_root || '');
                const label = escapeHtmlInline(String(r.label || root));
                const branch = r.branch ? (' · ' + escapeHtmlInline(String(r.branch))) : '';
                const enc = encodeURIComponent(root);
                return (
                    '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                    + ' data-git-push-repo="' + enc + '"'
                    + ' title="' + label + '">'
                    + '<span class="chat-chrome-menu-item-label">' + label + branch + '</span>'
                    + '</button>'
                );
            }).join('');
            submenu.hidden = false;
            return;
        }

        submenu.innerHTML = '<div class="chat-chrome-menu-item is-muted">Loading repos…</div>';
        submenu.hidden = false;
        triggerBtn.setAttribute('aria-expanded', 'true');
        const repos = await fetchProjectGitRepos(true);
        if (!submenu.isConnected) return;
        if (pushFromRepos(repos)) {
            submenu.innerHTML = '';
            submenu.hidden = true;
            triggerBtn.setAttribute('aria-expanded', 'false');
            return;
        }
        applyGitPushMenuMode(wrap, true);
        submenu.innerHTML = repos.map((r) => {
            const root = String(r.repo_root || '');
            const label = escapeHtmlInline(String(r.label || root));
            const branch = r.branch ? (' · ' + escapeHtmlInline(String(r.branch))) : '';
            const enc = encodeURIComponent(root);
            return (
                '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' data-git-push-repo="' + enc + '"'
                + ' title="' + label + '">'
                + '<span class="chat-chrome-menu-item-label">' + label + branch + '</span>'
                + '</button>'
            );
        }).join('');
    }

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
            pushCurrentChatProject(e, root || null);
            return;
        }
        const trigger = t.closest('[data-git-push-trigger]');
        if (trigger) {
            e.preventDefault();
            e.stopPropagation();
            openGitPushSubmenu(trigger);
        }
    }, true);

    window.chatPageToggleHistoryMenu = toggleHistoryItemMenu;
    window.searchChats = searchChats;
    window.onHistorySearchInput = onHistorySearchInput;
    window.clearHistorySearch = clearHistorySearch;
    window.onHistorySearchKeydown = onHistorySearchKeydown;
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
        if (!qid || !value || currentSessionId == null) return;
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
                body: JSON.stringify({ session_id: currentSessionId, query_id: qid, feedback: next }),
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
        const sid = currentSessionId;
        const hasSession = sid != null && sid !== '';
        dropdown.innerHTML = buildSessionMenuHTML(hasSession ? sid : null, {
            includeRename: hasSession,
            includeDelete: hasSession,
        });
        refreshGitPushMenuMode(dropdown);
    }

    function toggleChatChromeMenu(forceOpen) {
        const btn = document.getElementById('chatChromeMenuBtn');
        const dropdown = document.getElementById('chatChromeMenuDropdown');
        if (!btn || !dropdown) return;
        const open = forceOpen == null ? dropdown.hidden : !!forceOpen;
        if (open) {
            closeHistoryItemMenu();
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
