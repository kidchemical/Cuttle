/* Cuttle Web Terminal — multi-tab xterm.js sessions over WebSocket PTY */

(function () {
    'use strict';

    const WS_PATH = '/api/terminal/ws';
    /** Durable registry of terminal chat handles (survives refresh / close). */
    const STORAGE_SESSIONS = 'cuttle_terminal_sessions_v1';
    /** Which tabs are currently open + active (localStorage — not sessionStorage). */
    const STORAGE_LAYOUT = 'cuttle_terminal_layout_v1';
    const LOCALHOST_TERMINAL_URL = (typeof location !== 'undefined' && location.origin)
        ? (location.origin + '/terminal_page.html')
        : 'http://127.0.0.1:8000/terminal_page.html';

    /** @type {Array<{id:string,shell:string,label:string}>} */
    let shells = [];
    /** @type {Map<string, TabSession>} */
    const sessions = new Map();
    let activeTabId = null;
    let terminalAccessAllowed = null;

    const tabBar = document.getElementById('tabBar');
    const terminalTabStrip = document.getElementById('terminalTabStrip');
    const terminalLayout = document.getElementById('terminalLayout');
    const terminalMain = document.getElementById('terminalMain');
    const terminalEmpty = document.getElementById('terminalEmpty');
    const terminalEmptyActions = document.getElementById('terminalEmptyActions');
    const terminalStatus = document.getElementById('terminalStatus');
    const tabTemplate = document.getElementById('tabTemplate');
    const paneTemplate = document.getElementById('paneTemplate');
    const newTabBtn = document.getElementById('newTabBtn');
    const newChatBtn = document.getElementById('newChatBtn');
    const chatHistoryBtn = document.getElementById('chatHistoryPanelButton');
    const chatViewBtn = document.getElementById('chatViewBtn');

    /** Same CH-XXXXXX format as chat_page anonymous sessions. */
    function generateTerminalChatId() {
        const mod = Math.pow(36, 6);
        const mixed = (Date.now() + Math.floor(Math.random() * 46656)) % mod;
        return 'CH-' + mixed.toString(36).toUpperCase().padStart(6, '0');
    }

    function formatTerminalDisplayId(sessionId) {
        if (sessionId == null || sessionId === '') return null;
        const raw = String(sessionId).trim();
        if (!raw) return null;
        if (/^CH-[A-Z0-9]+$/i.test(raw)) return raw.toUpperCase();
        return raw;
    }

    function readSessionRegistry() {
        try {
            const map = JSON.parse(localStorage.getItem(STORAGE_SESSIONS) || '{}');
            return map && typeof map === 'object' ? map : {};
        } catch (_) {
            return {};
        }
    }

    function writeSessionRegistry(map) {
        try {
            localStorage.setItem(STORAGE_SESSIONS, JSON.stringify(map || {}));
        } catch (_) {}
    }

    function upsertSessionRegistry(id, shellId, opts) {
        if (!id) return;
        const map = readSessionRegistry();
        const prev = map[id] || {};
        const meta = shellMeta(shellId);
        const customTitle = opts && opts.title != null ? String(opts.title).trim() : '';
        const keepTitle = prev.titleCustom && prev.title;
        map[id] = {
            id: String(id),
            type: 'terminal',
            shell: shellId || prev.shell || 'powershell',
            title: customTitle || keepTitle || (meta.label || shellId || 'Terminal'),
            titleCustom: !!(customTitle || (prev.titleCustom && !opts?.resetTitle)),
            created: prev.created || Date.now(),
            updated: Date.now(),
            projectId: '__terminal__',
            projectName: 'Terminal',
            projectPath: '',
        };
        writeSessionRegistry(map);
    }

    function removeSessionRegistry(id) {
        if (!id) return;
        const map = readSessionRegistry();
        if (!map[id]) return;
        delete map[id];
        writeSessionRegistry(map);
    }

    function updateChatIdBadge(sessionId) {
        const badge = document.getElementById('chatIdBadge');
        const label = document.getElementById('chatIdBadgeLabel');
        if (!badge || !label) return;
        const displayId = formatTerminalDisplayId(sessionId);
        if (!displayId) {
            badge.hidden = true;
            label.textContent = '';
            badge.dataset.chatId = '';
            badge.title = 'Copy chat ID';
            if (terminalLayout) terminalLayout.classList.remove('has-chat-id');
            return;
        }
        label.textContent = displayId;
        badge.dataset.chatId = displayId;
        badge.title = 'Copy ' + displayId;
        badge.setAttribute('aria-label', 'Copy chat ID ' + displayId);
        badge.hidden = false;
        badge.classList.remove('copied');
        if (terminalLayout) terminalLayout.classList.add('has-chat-id');
    }

    function copyTextFallback(text) {
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(ta);
            return Promise.resolve(ok);
        } catch (_) {
            return Promise.resolve(false);
        }
    }

    function copyTextToClipboard(text) {
        if (!text) return Promise.resolve(false);
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function' && window.isSecureContext) {
            return navigator.clipboard.writeText(text).then(() => true).catch(() => copyTextFallback(text));
        }
        return copyTextFallback(text);
    }

    async function copyChatIdBadge() {
        const badge = document.getElementById('chatIdBadge');
        const id = (badge && badge.dataset.chatId) || formatTerminalDisplayId(activeTabId);
        if (!id) return;
        const ok = await copyTextToClipboard(id);
        if (ok && badge) {
            badge.classList.add('copied');
            badge.title = 'Copied!';
            setTimeout(() => {
                badge.classList.remove('copied');
                badge.title = 'Copy ' + id;
            }, 1500);
        }
    }

    function syncTerminalUrl(sessionId) {
        try {
            const url = new URL(window.location.href);
            if (sessionId) {
                url.searchParams.set('chat', String(sessionId));
                url.searchParams.delete('session');
            } else {
                url.searchParams.delete('chat');
                url.searchParams.delete('session');
            }
            const next = url.pathname + url.search + url.hash;
            if (next !== window.location.pathname + window.location.search + window.location.hash) {
                history.replaceState(null, '', next);
            }
            if (window.parent && window.parent !== window) {
                try {
                    window.parent.postMessage({ type: 'cuttle-chat-url', chatId: sessionId || null }, '*');
                } catch (_) {}
            }
        } catch (_) {}
    }

    function isLoopbackHost() {
        const host = (location.hostname || '').toLowerCase();
        return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
    }

    function showTerminalBlocked(payload) {
        const message = payload?.error || 'Terminal is only available on this PC.';
        const hint = payload?.hint || 'Open Cuttle on the machine running the daemon — not from a phone or another computer.';
        const localhostUrl = payload?.localhost_url || LOCALHOST_TERMINAL_URL;
        terminalAccessAllowed = false;
        sessions.forEach((session) => session.destroy());
        sessions.clear();
        activeTabId = null;
        tabBar.innerHTML = '';
        terminalMain.querySelectorAll('.terminal-pane').forEach((pane) => pane.remove());
        if (newTabBtn) newTabBtn.disabled = true;
        if (terminalTabStrip) terminalTabStrip.hidden = true;
        terminalEmpty.style.display = 'flex';
        terminalEmpty.innerHTML =
            '<div class="terminal-blocked">'
            + '<p class="terminal-blocked-title">Terminal unavailable here</p>'
            + '<p class="terminal-blocked-body">' + escapeHtml(message) + '</p>'
            + '<p class="terminal-blocked-hint">' + escapeHtml(hint) + '</p>'
            + '<a class="primary-btn terminal-blocked-link" href="' + escapeHtml(localhostUrl) + '">Open on this PC</a>'
            + '</div>';
        setStatus('Localhost only — use this PC', 'error');
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    async function checkTerminalAccess() {
        try {
            const resp = await fetch('/api/terminal/status', { credentials: 'same-origin' });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.success) {
                terminalAccessAllowed = true;
                return data;
            }
            showTerminalBlocked(data);
            return data;
        } catch (_) {
            if (!isLoopbackHost()) {
                showTerminalBlocked({
                    error: 'Could not verify terminal access from this device.',
                    hint: 'The web terminal only runs on the Cuttle PC. Open this page on that machine.',
                    localhost_url: LOCALHOST_TERMINAL_URL,
                });
                return { success: false };
            }
            terminalAccessAllowed = null;
            return { success: true };
        }
    }

    function wsUrl() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return proto + '//' + location.host + WS_PATH;
    }

    function setStatus(text, kind) {
        if (!terminalStatus) return;
        terminalStatus.textContent = text || '';
        terminalStatus.className = 'terminal-status' + (kind ? ' ' + kind : '');
        // Keep the footer quiet unless something needs attention.
        terminalStatus.hidden = kind !== 'error';
    }

    function shellMeta(id) {
        return shells.find((s) => s.id === id) || { id, label: id, icon: '⌨️', available: true };
    }

    function defaultShell() {
        const preferred = ['powershell', 'cuttle', 'cmd'];
        for (const id of preferred) {
            const s = shells.find((x) => x.id === id && x.available);
            if (s) return s.id;
        }
        const first = shells.find((s) => s.available);
        return first ? first.id : 'powershell';
    }

    function encodeInputPayload(data) {
        const bytes = new TextEncoder().encode(data);
        let bin = '';
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        return { op: 'input', encoding: 'base64', data: btoa(bin) };
    }

    /** Keep navigation/editing keys inside xterm (iframe + browser scroll steal them otherwise). */
    const TERMINAL_CAPTURE_KEYS = new Set([
        'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
        'Home', 'End', 'PageUp', 'PageDown', 'Tab', 'Delete', 'Insert',
    ]);

    function installTerminalKeyboard(term, host) {
        term.attachCustomKeyEventHandler((event) => {
            if (event.type !== 'keydown') return true;
            if (TERMINAL_CAPTURE_KEYS.has(event.key) || event.key.startsWith('Arrow')) {
                event.preventDefault();
            }
            return true;
        });

        host.addEventListener('mousedown', (e) => {
            if (e.button === 0) term.focus();
        });
    }

    function isXtermFocused(term) {
        const active = document.activeElement;
        return active === term.textarea
            || (term.element && term.element.contains(active));
    }

    class TabSession {
        constructor(id, shellId) {
            this.id = id;
            this.shellId = shellId;
            this.ws = null;
            this.term = null;
            this.fitAddon = null;
            this.connected = false;
            this.tabEl = null;
            this.paneEl = null;
            this._resizeObserver = null;
            this._fitRaf = null;
            this._fitSettleTimer = null;
            this._onWindowResize = null;
            this._scheduleFit = null;
        }

        label() {
            const reg = readSessionRegistry()[this.id];
            if (reg && reg.titleCustom && reg.title) return reg.title;
            if (reg && reg.title) return reg.title;
            const meta = shellMeta(this.shellId);
            return meta.label || this.shellId;
        }

        icon() {
            return shellMeta(this.shellId).icon || '⌨️';
        }

        buildDom() {
            const tabFrag = tabTemplate.content.cloneNode(true);
            this.tabEl = tabFrag.querySelector('.tab-item');
            this.tabEl.dataset.tabId = this.id;
            this.tabEl.querySelector('.tab-icon').textContent = this.icon();
            this.tabEl.querySelector('.tab-label').textContent = this.label();
            this.tabEl.addEventListener('click', (e) => {
                if (e.target.closest('.tab-close')) return;
                activateTab(this.id);
            });
            this.tabEl.addEventListener('dblclick', (e) => {
                if (e.target.closest('.tab-close')) return;
                e.preventDefault();
                this.reconnect();
            });
            this.tabEl.querySelector('.tab-close').addEventListener('click', (e) => {
                e.stopPropagation();
                closeTab(this.id);
            });
            tabBar.appendChild(tabFrag);

            const paneFrag = paneTemplate.content.cloneNode(true);
            this.paneEl = paneFrag.querySelector('.terminal-pane');
            this.paneEl.dataset.tabId = this.id;
            const host = this.paneEl.querySelector('.xterm-host');
            terminalMain.appendChild(paneFrag);

            const videoBg = document.documentElement.classList.contains('has-video-background')
                || document.body.classList.contains('has-video-background');
            this.term = new Terminal({
                cursorBlink: true,
                fontSize: 14,
                fontFamily: 'Cascadia Code, Consolas, Menlo, monospace',
                windowsMode: true,
                theme: {
                    /* Transparent cells so the frosted .xterm-host (and wallpaper) show through. */
                    background: videoBg ? 'transparent' : '#0d1117',
                    foreground: '#e6edf3',
                    cursor: '#e6edf3',
                    selectionBackground: '#264f78',
                },
                allowProposedApi: true,
            });
            this._applyVideoTheme = (active) => {
                if (!this.term) return;
                this.term.options.theme = {
                    ...this.term.options.theme,
                    background: active ? 'transparent' : '#0d1117',
                };
            };
            this.fitAddon = new FitAddon.FitAddon();
            this.term.loadAddon(this.fitAddon);
            this.term.loadAddon(new WebLinksAddon.WebLinksAddon());
            this.term.open(host);
            installTerminalKeyboard(this.term, host);
            this.term.onData((data) => {
                if (this.ws && this.connected && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify(encodeInputPayload(data)));
                }
            });
            this._scheduleFit = () => {
                if (this._fitRaf) cancelAnimationFrame(this._fitRaf);
                if (this._fitSettleTimer) clearTimeout(this._fitSettleTimer);
                this._fitRaf = requestAnimationFrame(() => {
                    this._fitRaf = null;
                    this.fitAndResize();
                    // Electron (and split-drag) can finish layout a frame late;
                    // a short settle pass clears leftover squashed glyphs.
                    this._fitSettleTimer = setTimeout(() => {
                        this._fitSettleTimer = null;
                        this.fitAndResize();
                    }, 50);
                });
            };
            this._resizeObserver = new ResizeObserver(() => this._scheduleFit());
            this._resizeObserver.observe(host);
            this._onWindowResize = () => this._scheduleFit();
            window.addEventListener('resize', this._onWindowResize);
        }

        fitAndResize() {
            if (!this.term || !this.fitAddon) return;
            const host = this.paneEl && this.paneEl.querySelector('.xterm-host');
            if (host && (host.clientWidth < 2 || host.clientHeight < 2)) return;
            const prevCols = this.term.cols;
            const prevRows = this.term.rows;
            let dims = null;
            try {
                dims = this.fitAddon.proposeDimensions();
            } catch (_) {
                return;
            }
            // ResizeObserver often fires without a real col/row change (status,
            // split chrome, video bg). Fitting anyway can reset scroll to top.
            if (
                dims
                && dims.cols === prevCols
                && dims.rows === prevRows
            ) {
                return;
            }
            // fit() → resize → Viewport._sync can clobber scrollTop (xterm #5096).
            const stickBottom = this._isScrolledToBottom();
            const savedY = this.term.buffer.active.viewportY;
            try {
                this.fitAddon.fit();
                // Force a full cell redraw — cached atlas glyphs can stay
                // horizontally compressed after shrink→expand in Electron.
                this.term.refresh(0, Math.max(0, this.term.rows - 1));
            } catch (_) { /* pane hidden */ }
            this._restoreScrollAfterFit(stickBottom, savedY);
            if (
                this.ws && this.connected
                && (this.term.cols !== prevCols || this.term.rows !== prevRows)
            ) {
                this.ws.send(JSON.stringify({
                    op: 'resize',
                    cols: this.term.cols,
                    rows: this.term.rows,
                }));
            }
        }

        /** True when the viewport is already pinned to the live bottom. */
        _isScrolledToBottom() {
            if (!this.term) return true;
            try {
                const buf = this.term.buffer.active;
                return buf.viewportY >= buf.baseY - 1;
            } catch (_) {
                return true;
            }
        }

        _restoreScrollAfterFit(stickBottom, savedViewportY) {
            if (!this.term) return;
            try {
                if (stickBottom) {
                    this.term.scrollToBottom();
                    return;
                }
                const buf = this.term.buffer.active;
                if (buf.viewportY !== savedViewportY) {
                    this.term.scrollToLine(Math.min(savedViewportY, buf.baseY));
                }
            } catch (_) {}
        }

        _writeBase64ToTerm(b64, opts) {
            if (!this.term || !b64) return;
            const forceBottom = !!(opts && opts.forceBottom);
            const stickBottom = forceBottom || this._isScrolledToBottom();
            try {
                const bin = atob(b64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                this.term.write(bytes, () => {
                    if (stickBottom && this.term) {
                        try { this.term.scrollToBottom(); } catch (_) {}
                    }
                });
            } catch (_) {}
        }

        connect() {
            this.disconnect(false);
            this.term.clear();
            this.term.writeln('\x1b[90mConnecting…\x1b[0m');
            setStatus('Connecting to ' + this.label() + '…');

            const ws = new WebSocket(wsUrl());
            this.ws = ws;

            ws.onopen = () => {
                this.fitAndResize();
                ws.send(JSON.stringify({
                    op: 'init',
                    session_id: this.id,
                    shell: this.shellId,
                    cols: this.term.cols || 80,
                    rows: this.term.rows || 24,
                }));
            };

            ws.onmessage = (ev) => {
                let msg;
                try {
                    msg = JSON.parse(ev.data);
                } catch (_) {
                    return;
                }
                if (msg.op === 'ready') {
                    this.connected = true;
                    this.term.clear();
                    if (msg.scrollback) {
                        this._writeBase64ToTerm(msg.scrollback, { forceBottom: true });
                    }
                    this.term.focus();
                    const resumed = !!msg.resumed;
                    setStatus(
                        this.label() + (resumed ? ' · resumed' : ' · connected'),
                        'connected'
                    );
                    this.fitAndResize();
                    try { this.term.scrollToBottom(); } catch (_) {}
                } else if (msg.op === 'output' && msg.data) {
                    this._writeBase64ToTerm(msg.data);
                } else if (msg.op === 'exit') {
                    this.connected = false;
                    this.term.writeln('\r\n\x1b[33m[process exited with code ' + (msg.code ?? '?') + ']\x1b[0m');
                    setStatus(this.label() + ' · exited (' + (msg.code ?? '?') + ')');
                    try { this.term.scrollToBottom(); } catch (_) {}
                } else if (msg.op === 'error') {
                    this.connected = false;
                    this.term.writeln('\r\n\x1b[31m[error] ' + (msg.message || 'unknown') + '\x1b[0m');
                    if (msg.hint) {
                        this.term.writeln('\x1b[33m' + msg.hint + '\x1b[0m');
                    }
                    if (msg.localhost_url) {
                        this.term.writeln('\x1b[36m' + msg.localhost_url + '\x1b[0m');
                    }
                    setStatus(msg.message || 'Error', 'error');
                    try { this.term.scrollToBottom(); } catch (_) {}
                }
            };

            ws.onclose = () => {
                this.connected = false;
                if (activeTabId === this.id) {
                    setStatus(this.label() + ' · disconnected');
                }
            };

            ws.onerror = () => {
                const hint = isLoopbackHost()
                    ? 'WebSocket failed — confirm Cuttle is running, then hard-refresh this page'
                    : 'Terminal only works on the Cuttle PC — open this page there';
                this.term.writeln('\r\n\x1b[31m[WebSocket connection failed]\x1b[0m');
                this.term.writeln('\x1b[33m' + hint + '\x1b[0m');
                setStatus('WebSocket connection failed', 'error');
            };
        }

        /** Ask the server to kill this PTY (tab close). Refresh only detaches. */
        requestServerClose() {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            try {
                this.ws.send(JSON.stringify({ op: 'close', session_id: this.id }));
            } catch (_) {}
        }

        disconnect(clearTerm) {
            this.connected = false;
            if (this.ws) {
                try { this.ws.close(); } catch (_) {}
                this.ws = null;
            }
            if (clearTerm && this.term) {
                this.term.clear();
            }
        }

        reconnect() {
            this.tabEl.querySelector('.tab-icon').textContent = this.icon();
            this.tabEl.querySelector('.tab-label').textContent = this.label();
            this.connect();
        }

        destroy(opts) {
            const killPty = !(opts && opts.keepPty);
            if (killPty) this.requestServerClose();
            this.disconnect(true);
            if (this._fitRaf) cancelAnimationFrame(this._fitRaf);
            if (this._fitSettleTimer) clearTimeout(this._fitSettleTimer);
            if (this._resizeObserver) this._resizeObserver.disconnect();
            if (this._onWindowResize) {
                window.removeEventListener('resize', this._onWindowResize);
                this._onWindowResize = null;
            }
            if (this.term) this.term.dispose();
            this.tabEl?.remove();
            this.paneEl?.remove();
        }

        focus() {
            this.term?.focus();
            if (this._scheduleFit) this._scheduleFit();
            else requestAnimationFrame(() => this.fitAndResize());
        }
    }

    function renderEmptyShellButtons() {
        if (!terminalEmptyActions || terminalAccessAllowed === false) return;
        const available = shells.filter((s) => s.available);
        const list = available.length ? available : [
            { id: 'powershell', label: 'PowerShell', icon: '⚡', available: true },
        ];
        terminalEmptyActions.innerHTML = '';
        list.forEach((s, idx) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'primary-btn' + (idx === 0 ? '' : ' secondary');
            btn.dataset.shell = s.id;
            btn.textContent = (s.icon ? s.icon + ' ' : '') + (s.label || s.id);
            btn.addEventListener('click', () => createTab(s.id));
            terminalEmptyActions.appendChild(btn);
        });
    }

    function updateEmptyState() {
        const empty = sessions.size === 0;
        terminalEmpty.style.display = empty ? 'flex' : 'none';
        if (terminalTabStrip) terminalTabStrip.hidden = empty;
        if (empty && terminalAccessAllowed !== false) {
            renderEmptyShellButtons();
        }
    }

    function activateTab(id) {
        activeTabId = id;
        sessions.forEach((s, sid) => {
            const active = sid === id;
            s.tabEl?.classList.toggle('active', active);
            s.paneEl?.classList.toggle('active', active);
        });
        const session = sessions.get(id);
        if (session) {
            upsertSessionRegistry(session.id, session.shellId);
            session.focus();
            if (!session.connected && !session.ws) {
                session.connect();
            }
        }
        updateChatIdBadge(id);
        syncTerminalUrl(id);
        saveTabs();
    }

    /**
     * @param {string} [shellId]
     * @param {string} [existingId] — reopen a known CH- handle (history / restore)
     */
    function createTab(shellId, existingId) {
        if (terminalAccessAllowed === false) {
            return null;
        }
        let id = existingId ? formatTerminalDisplayId(existingId) : null;
        if (id && sessions.has(id)) {
            activateTab(id);
            return sessions.get(id);
        }
        if (!id) {
            id = generateTerminalChatId();
            // Extremely unlikely collision — mint again.
            const chatMap = (() => {
                try { return JSON.parse(localStorage.getItem('chatSessions') || '{}'); }
                catch (_) { return {}; }
            })();
            while (sessions.has(id) || readSessionRegistry()[id] || chatMap[id]) {
                id = generateTerminalChatId();
            }
        }
        const reg = readSessionRegistry()[id];
        const resolvedShell = shellId || (reg && reg.shell) || defaultShell();
        const session = new TabSession(id, resolvedShell);
        sessions.set(id, session);
        session.buildDom();
        upsertSessionRegistry(id, resolvedShell);
        updateEmptyState();
        activateTab(id);
        return session;
    }

    function closeTab(id, opts) {
        const session = sessions.get(id);
        if (!session) return;
        const purge = !!(opts && opts.purge);
        // Closing a tab (or purging from history) kills the server PTY.
        session.destroy();
        sessions.delete(id);
        if (purge) {
            removeSessionRegistry(id);
        }
        if (activeTabId === id) {
            const remaining = [...sessions.keys()];
            activeTabId = remaining.length ? remaining[remaining.length - 1] : null;
            if (activeTabId) {
                activateTab(activeTabId);
            } else {
                updateChatIdBadge(null);
                syncTerminalUrl(null);
            }
        }
        updateEmptyState();
        saveTabs();
        if (sessions.size === 0) {
            setStatus('Localhost only · sessions run on this machine');
        }
    }

    function saveTabs() {
        const data = {
            active: activeTabId,
            tabs: [...sessions.values()].map((s) => ({ id: s.id, shell: s.shellId })),
        };
        try {
            localStorage.setItem(STORAGE_LAYOUT, JSON.stringify(data));
        } catch (_) {}
    }

    function readLayout() {
        try {
            const raw = localStorage.getItem(STORAGE_LAYOUT);
            if (!raw) return null;
            const data = JSON.parse(raw);
            if (!data || !Array.isArray(data.tabs)) return null;
            return data;
        } catch (_) {
            return null;
        }
    }

    function restoreTabs() {
        const data = readLayout();
        if (!data || !data.tabs.length) return false;
        data.tabs.forEach((t) => {
            const shell = t.shell || defaultShell();
            const id = t.id ? formatTerminalDisplayId(t.id) : null;
            if (id) {
                // Avoid activateTab side-effects until all tabs exist.
                if (sessions.has(id)) return;
                const session = new TabSession(id, shell);
                sessions.set(id, session);
                session.buildDom();
                upsertSessionRegistry(id, shell);
            } else {
                createTab(shell);
            }
        });
        updateEmptyState();
        const wanted = data.active ? formatTerminalDisplayId(data.active) : null;
        if (wanted && sessions.has(wanted)) {
            activateTab(wanted);
        } else {
            const first = [...sessions.keys()][0];
            if (first) activateTab(first);
        }
        return sessions.size > 0;
    }

    /** Open or focus a terminal chat handle (from history deep-link). */
    function openSessionById(sessionId) {
        const id = formatTerminalDisplayId(sessionId);
        if (!id) return null;
        if (sessions.has(id)) {
            activateTab(id);
            return sessions.get(id);
        }
        const reg = readSessionRegistry()[id];
        return createTab(reg && reg.shell, id);
    }

    function deleteSessionById(sessionId) {
        const id = formatTerminalDisplayId(sessionId);
        if (!id) return;
        if (sessions.has(id)) {
            closeTab(id, { purge: true });
        } else {
            removeSessionRegistry(id);
        }
    }

    function renameSessionById(sessionId, title) {
        const id = formatTerminalDisplayId(sessionId);
        if (!id || !title) return false;
        const map = readSessionRegistry();
        const prev = map[id];
        if (!prev) return false;
        map[id] = { ...prev, title: String(title).trim(), titleCustom: true, updated: Date.now() };
        writeSessionRegistry(map);
        const live = sessions.get(id);
        if (live && live.tabEl) {
            live.tabEl.querySelector('.tab-label').textContent = map[id].title;
        }
        return true;
    }

    async function loadShells() {
        try {
            const resp = await fetch('/api/terminal/shells', { credentials: 'same-origin' });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.success && Array.isArray(data.shells)) {
                shells = data.shells;
                return;
            }
            if (!resp.ok) {
                showTerminalBlocked(data);
            }
        } catch (_) {
            if (!isLoopbackHost()) {
                showTerminalBlocked({
                    error: 'Could not load terminal shells from this device.',
                    hint: 'Open this page on the Cuttle PC.',
                    localhost_url: LOCALHOST_TERMINAL_URL,
                });
            }
        }
        if (terminalAccessAllowed !== false) {
            shells = [
                { id: 'powershell', label: 'PowerShell', icon: '⚡', available: true },
                { id: 'cmd', label: 'Command Prompt', icon: '⌨️', available: true },
                { id: 'cuttle', label: 'Cuttle (venv)', icon: '🦑', available: true },
            ];
        }
    }

    function navigateInShell(page) {
        const shell = window.parent;
        if (shell && shell !== window && typeof shell.navigateFromSource === 'function') {
            shell.navigateFromSource(window, page);
            return true;
        }
        if (shell && shell !== window && typeof shell.navigate === 'function') {
            const colIdx = typeof shell.findColumnIndexForSource === 'function'
                ? shell.findColumnIndexForSource(window)
                : 0;
            shell.navigate(colIdx, page);
            return true;
        }
        return false;
    }

    function resolveChatOpenPage() {
        try {
            const last = localStorage.getItem('lastChatSessionId');
            if (last && String(last).trim()) {
                return '/chat_page.html?chat=' + encodeURIComponent(String(last).trim());
            }
        } catch (_) {}
        return '/chat_page.html';
    }

    if (newTabBtn) {
        newTabBtn.addEventListener('click', () => createTab(defaultShell()));
    }
    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            const page = '/chat_page.html';
            if (!navigateInShell(page)) {
                window.location.href = page;
            }
        });
    }
    if (chatHistoryBtn) {
        chatHistoryBtn.addEventListener('click', () => {
            const page = '/chat_page.html?history=1';
            if (!navigateInShell(page)) {
                window.location.href = page;
            }
        });
    }
    if (chatViewBtn) {
        chatViewBtn.addEventListener('click', (e) => {
            const page = resolveChatOpenPage();
            chatViewBtn.setAttribute('href', page);
            if (navigateInShell(page)) {
                e.preventDefault();
            }
        });
    }

    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.shiftKey && e.key === 'T') {
            e.preventDefault();
            createTab(defaultShell());
            return;
        }
        if (e.ctrlKey && e.shiftKey && e.key === 'W') {
            e.preventDefault();
            if (activeTabId) closeTab(activeTabId);
            return;
        }
        // When xterm owns focus, block browser/iframe from consuming navigation keys.
        if (!activeTabId) return;
        const session = sessions.get(activeTabId);
        if (!session?.term || !isXtermFocused(session.term)) return;
        if (TERMINAL_CAPTURE_KEYS.has(e.key) || e.key.startsWith('Arrow')) {
            e.preventDefault();
        }
    }, true);

    window.addEventListener('message', (e) => {
        if (!e.data) return;
        if (e.data.type === 'cuttle-video-state') {
            const active = !!e.data.active;
            sessions.forEach((session) => {
                if (typeof session._applyVideoTheme === 'function') {
                    session._applyVideoTheme(active);
                }
            });
            return;
        }
        if (e.data.type === 'cuttle-terminal-delete' && e.data.sessionId) {
            deleteSessionById(e.data.sessionId);
            return;
        }
        if (e.data.type === 'cuttle-terminal-rename' && e.data.sessionId && e.data.title) {
            renameSessionById(e.data.sessionId, e.data.title);
        }
    });

    function showXtermMissing() {
        if (terminalEmpty) {
            terminalEmpty.hidden = false;
            const p = terminalEmpty.querySelector('p');
            if (p) {
                p.textContent = 'Terminal UI needs xterm.js from the CDN (unavailable offline). Chat still works locally.';
            }
        }
        if (terminalEmptyActions) terminalEmptyActions.hidden = true;
        if (terminalTabStrip) terminalTabStrip.hidden = true;
    }

    function startTerminalApp() {
        if (typeof Terminal === 'undefined') {
            showXtermMissing();
            return;
        }
        checkTerminalAccess()
            .then(() => loadShells())
            .then(() => {
                if (terminalAccessAllowed === false) {
                    return;
                }
                // Defer one macrotask so the app-shell iframe load handler can finish
                // before xterm/WebSocket start (prevents Electron renderer lockups).
                setTimeout(() => {
                    const urlParams = new URLSearchParams(window.location.search);
                    const wanted = formatTerminalDisplayId(
                        urlParams.get('chat') || urlParams.get('session')
                    );
                    restoreTabs();
                    if (wanted) {
                        openSessionById(wanted);
                    } else if (sessions.size === 0) {
                        // Layout empty (e.g. first open after upgrade) — prefer the
                        // most recently updated registry handle over a brand-new CH-.
                        const reg = readSessionRegistry();
                        const latest = Object.values(reg)
                            .filter((e) => e && e.id)
                            .sort((a, b) => (b.updated || 0) - (a.updated || 0))[0];
                        if (latest) {
                            openSessionById(latest.id);
                        } else {
                            createTab(defaultShell());
                        }
                    }
                    updateEmptyState();
                }, 0);
            });
    }

    function bootWhenXtermReady() {
        if (typeof Terminal !== 'undefined') {
            startTerminalApp();
            return;
        }
        const loader = window.CuttleOptionalCdn && window.CuttleOptionalCdn.loadTerminalExtras;
        if (typeof loader === 'function') {
            loader(startTerminalApp);
            return;
        }
        showXtermMissing();
    }

    bootWhenXtermReady();

    window.copyChatIdBadge = copyChatIdBadge;
    window.cuttleTerminalOpenSession = openSessionById;
    window.cuttleTerminalDeleteSession = deleteSessionById;
    window.cuttleTerminalRenameSession = renameSessionById;
    window.cuttleTerminalReadSessions = readSessionRegistry;
})();