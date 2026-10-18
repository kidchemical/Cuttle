/* IDE App — integrated terminal panel. Owns xterm instances and their PTY
 * WebSockets (server owner api.web_terminal, localhost-only), started in the
 * workspace folder. Session ids persist per workspace in sessionStorage so a
 * reload reattaches the same shells. */
(function (root) {
    'use strict';

    function create(host) {
        const tabsEl = host.tabsEl;
        const bodyEl = host.bodyEl;
        const terms = []; // {id, label, shell, term, fit, ws, el, connected}
        let active = null;
        let workspace = null;
        let counter = 0;
        let libsReady = null;
        let defaultShell = null;

        const storageKey = () => 'cuttleIdeTerminals:' + (workspace || '');
        function persist() {
            try { sessionStorage.setItem(storageKey(), JSON.stringify({ ids: terms.map((t) => ({ id: t.id, shell: t.shell })), active: active && active.id })); } catch (_) {}
        }
        function restoreIds() {
            try { return JSON.parse(sessionStorage.getItem(storageKey()) || 'null'); } catch (_) { return null; }
        }

        function loadLibs() {
            if (libsReady) return libsReady;
            libsReady = new Promise((resolve) => {
                if (root.Terminal && root.FitAddon) return resolve(true);
                if (!root.CuttleOptionalCdn) return resolve(false);
                root.CuttleOptionalCdn.loadTerminalExtras(() => resolve(!!(root.Terminal && root.FitAddon)));
            });
            return libsReady;
        }
        async function pickShell() {
            if (defaultShell) return defaultShell;
            try {
                const response = await fetch('/api/terminal/shells', { credentials: 'include', cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Terminal unavailable');
                const shells = (data.shells || []).filter((s) => s.available);
                const pick = shells.find((s) => s.id === 'bash') || shells.find((s) => s.id === 'powershell') || shells[0];
                defaultShell = pick ? pick.id : 'bash';
            } catch (err) {
                throw new Error((err && err.message) || 'Terminal unavailable');
            }
            return defaultShell;
        }
        function wsUrl() {
            return (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/api/terminal/ws';
        }
        function theme() {
            const light = document.body.classList.contains('light-mode');
            return light
                ? { background: '#ffffff', foreground: '#1f2328', cursor: '#1f2328', selectionBackground: '#b6d4fe' }
                : { background: '#11151c', foreground: '#e6edf3', cursor: '#e6edf3', selectionBackground: '#264f78' };
        }
        function writeB64(t, b64) {
            try {
                const bin = atob(b64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                t.term.write(bytes);
            } catch (_) {}
        }
        function send(t, msg) {
            if (t.ws && t.ws.readyState === 1) t.ws.send(JSON.stringify(msg));
        }
        function fit(t) {
            if (!t || !t.fit || t.el.hidden) return;
            try {
                t.fit.fit();
                send(t, { op: 'resize', cols: t.term.cols, rows: t.term.rows });
            } catch (_) {}
        }
        function connect(t) {
            const ws = new WebSocket(wsUrl());
            t.ws = ws;
            ws.onopen = () => {
                fit(t);
                ws.send(JSON.stringify({ op: 'init', session_id: t.id, shell: t.shell, cwd: workspace, cols: t.term.cols || 80, rows: t.term.rows || 24 }));
            };
            ws.onmessage = (ev) => {
                let msg;
                try { msg = JSON.parse(ev.data); } catch (_) { return; }
                if (msg.op === 'ready') {
                    t.connected = true;
                    if (msg.scrollback) writeB64(t, msg.scrollback);
                    fit(t);
                } else if (msg.op === 'output' && msg.data) {
                    writeB64(t, msg.data);
                } else if (msg.op === 'exit') {
                    t.connected = false;
                    t.term.writeln('\r\n\x1b[33m[process exited with code ' + (msg.code == null ? '?' : msg.code) + ']\x1b[0m');
                } else if (msg.op === 'error') {
                    t.connected = false;
                    t.term.writeln('\r\n\x1b[31m' + (msg.message || 'Terminal error') + '\x1b[0m');
                    if (msg.hint) t.term.writeln('\x1b[33m' + msg.hint + '\x1b[0m');
                }
            };
            ws.onclose = () => { t.connected = false; };
        }
        function renderTabs() {
            tabsEl.innerHTML = '';
            terms.forEach((t) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'ide-term-tab' + (t === active ? ' is-active' : '');
                b.textContent = t.label;
                b.title = t.label + ' — ' + (workspace || '');
                b.addEventListener('click', () => activate(t));
                const x = document.createElement('span');
                x.className = 'ide-term-tab-close';
                x.textContent = '×';
                x.title = 'Kill terminal';
                x.addEventListener('click', (e) => { e.stopPropagation(); kill(t); });
                b.appendChild(x);
                tabsEl.appendChild(b);
            });
        }
        function activate(t) {
            active = t;
            terms.forEach((x) => { x.el.hidden = x !== t; });
            renderTabs();
            persist();
            requestAnimationFrame(() => { fit(t); try { t.term.focus(); } catch (_) {} });
        }
        async function spawn(opts) {
            if (!workspace) throw new Error('Open a project first');
            const ok = await loadLibs();
            if (!ok) throw new Error('Terminal needs xterm.js from the CDN (unavailable offline)');
            const shell = (opts && opts.shell) || await pickShell();
            counter += 1;
            const id = (opts && opts.id) || ('ide' + Date.now().toString(36) + counter);
            const el = document.createElement('div');
            el.className = 'ide-term-host';
            bodyEl.appendChild(el);
            const term = new root.Terminal({
                cursorBlink: true, fontSize: 13, allowProposedApi: true,
                fontFamily: "'JetBrains Mono', 'Cascadia Code', ui-monospace, Menlo, Consolas, monospace",
                theme: theme(),
            });
            const fitAddon = new root.FitAddon.FitAddon();
            term.loadAddon(fitAddon);
            if (root.WebLinksAddon) term.loadAddon(new root.WebLinksAddon.WebLinksAddon());
            term.open(el);
            const t = { id: id, shell: shell, label: shell + ' ' + (terms.length + 1), term: term, fit: fitAddon, el: el, ws: null, connected: false };
            term.onData((data) => send(t, { op: 'input', data: data }));
            terms.push(t);
            connect(t);
            activate(t);
            return t;
        }
        function kill(t) {
            send(t, { op: 'close', session_id: t.id });
            try { t.ws && t.ws.close(); } catch (_) {}
            try { t.term.dispose(); } catch (_) {}
            t.el.remove();
            const idx = terms.indexOf(t);
            terms.splice(idx, 1);
            if (active === t) active = terms[Math.max(0, idx - 1)] || null;
            if (active) activate(active);
            else { renderTabs(); persist(); }
            if (!terms.length && host.onEmpty) host.onEmpty();
        }
        /** Detach (keep PTYs alive server-side) when switching workspaces. */
        function detachAll() {
            terms.splice(0).forEach((t) => {
                try { t.ws && t.ws.close(); } catch (_) {}
                try { t.term.dispose(); } catch (_) {}
                t.el.remove();
            });
            active = null;
            renderTabs();
        }
        return {
            setWorkspace(path) {
                if (path === workspace) return;
                detachAll();
                workspace = path;
            },
            /** Show the panel: reattach saved shells, else start one. */
            async ensure() {
                if (terms.length) { activate(active || terms[0]); return; }
                const saved = restoreIds();
                if (saved && saved.ids && saved.ids.length) {
                    for (const s of saved.ids) await spawn({ id: s.id, shell: s.shell });
                    const want = terms.find((t) => t.id === saved.active);
                    if (want) activate(want);
                    return;
                }
                await spawn();
            },
            spawn: spawn,
            killActive() { if (active) kill(active); },
            clear() { if (active) active.term.clear(); },
            fit() { fit(active); },
            focus() { if (active) try { active.term.focus(); } catch (_) {} },
            /** Type text into the active shell (e.g. "Run file"). */
            async run(text) {
                if (!active) await spawn();
                const t = active;
                const go = () => send(t, { op: 'input', data: text + '\r' });
                if (t.connected) go(); else setTimeout(go, 700);
            },
            applyTheme() { terms.forEach((t) => { t.term.options.theme = theme(); }); },
            count: () => terms.length,
        };
    }

    root.CuttleIdeTerminal = { create: create };
})(globalThis);
