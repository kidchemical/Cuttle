/* Shell connection popup: the Electron titlebar Host/Client badge opens this
 * in-shell modal (current connection info + switch host) instead of
 * navigating the window to connect.html.
 *
 * Pure helpers (``describeConnection``, ``badgeTooltip``, ``hostPortLabel``)
 * take the desktop config from the Electron preload and the Host's
 * ``/api/network-info`` payload. ``create(deps)`` binds the modal markup in
 * app_shell.html; shell_electron.js owns the badge and calls ``open()``.
 * Switching to a different target is Electron-owned (main process reloads the
 * window on the new origin); re-picking the current target is a no-op.
 */
(function (root) {
    'use strict';

    const DEFAULT_HTTP_PORT = 8000;

    function bracketHost(host) {
        const h = String(host || '');
        return h.includes(':') && !h.startsWith('[') ? `[${h}]` : h;
    }

    function hostPortLabel(host, port) {
        const h = bracketHost(host);
        if (!h) return '';
        const p = Number(port);
        return Number.isInteger(p) && p > 0 ? `${h}:${p}` : h;
    }

    function isClient(cfg) {
        return !!(cfg && cfg.clientMode);
    }

    /** The address other devices type into "Connect to Cuttle" (port only when non-default). */
    function connectAddress(ip, httpPort) {
        if (!ip) return '';
        const p = Number(httpPort) || DEFAULT_HTTP_PORT;
        return p === DEFAULT_HTTP_PORT ? ip : `${ip}:${p}`;
    }

    function deviceLanIp(cfg, net) {
        const fromCfg = cfg && Array.isArray(cfg.lanIps) ? cfg.lanIps.find(Boolean) : '';
        if (fromCfg) return String(fromCfg);
        if (!isClient(cfg) && net && net.lan_ipv4) return String(net.lan_ipv4);
        return '';
    }

    function remoteEndpoint(cfg) {
        const ep = (cfg && cfg.endpoint) || {};
        const scheme = String(ep.scheme || (cfg && cfg.scheme) || 'http');
        const port = ep.port || (scheme === 'https' ? cfg && cfg.httpsPort : cfg && cfg.httpPort);
        return { scheme, host: String((cfg && cfg.host) || ''), port: Number(port) || 0 };
    }

    /**
     * View model for the popup. ``net`` is the Host's /api/network-info
     * payload (Host role only; may be null while loading or on failure).
     */
    function describeConnection(cfg, net) {
        const client = isClient(cfg);
        const lanIp = deviceLanIp(cfg, net);
        const hostname = String((cfg && cfg.hostname) || '').trim();
        const rows = [];
        let warning = '';
        if (client) {
            const ep = remoteEndpoint(cfg);
            rows.push({ label: 'Connected to', value: hostPortLabel(ep.host, ep.port) || 'remote Cuttle', copy: false });
            rows.push({ label: 'Protocol', value: ep.scheme.toUpperCase(), copy: false });
            if (hostname) rows.push({ label: 'This device', value: hostname, copy: false });
            rows.push({ label: 'This device LAN IP', value: lanIp || 'Unknown', copy: !!lanIp });
            return {
                role: 'client',
                title: 'Client',
                subtitle: 'This app is a window onto a Cuttle host on your network.',
                rows,
                warning,
            };
        }
        const httpPort = (cfg && cfg.httpPort) || DEFAULT_HTTP_PORT;
        const lanEnabled = net ? net.lan_access_enabled !== false : true;
        rows.push({ label: 'LAN IP', value: lanIp || 'Unknown', copy: !!lanIp });
        if (lanIp && lanEnabled) {
            rows.push({ label: 'Connect other devices to', value: connectAddress(lanIp, httpPort), copy: true });
        }
        if (net && net.portal_url_phone && lanEnabled) {
            rows.push({ label: 'Phone / browser', value: String(net.portal_url_phone), copy: true });
        }
        if (hostname) rows.push({ label: 'This PC', value: hostname, copy: false });
        if (net && net.lan_access_enabled === false) {
            warning = 'LAN access is off, so other devices cannot connect yet. Turn it on in Settings, then restart Flask.';
        } else if (!lanIp) {
            warning = 'No LAN address found. Check this PC is on your local network.';
        }
        return {
            role: 'host',
            title: 'Host',
            subtitle: 'This PC runs the Cuttle daemon.',
            rows,
            warning,
        };
    }

    function badgeTooltip(cfg, net) {
        const lanIp = deviceLanIp(cfg, net);
        if (isClient(cfg)) {
            const ep = remoteEndpoint(cfg);
            const target = hostPortLabel(ep.host, ep.port) || 'remote Cuttle';
            return `Client — connected to ${target}`
                + (lanIp ? ` · this device ${lanIp}` : '')
                + ' · click for connection info';
        }
        return 'Host — this PC runs Cuttle'
            + (lanIp ? ` · LAN IP ${lanIp}` : '')
            + ' · click for connection info';
    }

    function isCurrentSaved(cfg, entry) {
        if (!isClient(cfg) || !entry) return false;
        const ep = remoteEndpoint(cfg);
        return entry.host === ep.host && Number(entry.port) === ep.port && entry.scheme === ep.scheme;
    }

    function copyText(doc, text) {
        const nav = doc.defaultView && doc.defaultView.navigator;
        if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
            return nav.clipboard.writeText(text).then(() => true, () => legacyCopy(doc, text));
        }
        return Promise.resolve(legacyCopy(doc, text));
    }

    function legacyCopy(doc, text) {
        try {
            const ta = doc.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            doc.body.appendChild(ta);
            ta.select();
            const ok = doc.execCommand('copy');
            ta.remove();
            return !!ok;
        } catch (_) {
            return false;
        }
    }

    /**
     * deps: { api: window.electron.desktop, doc, fetchNetworkInfo(): Promise<obj|null>,
     *         getConfig(): cfg, onConfig(cfg) }
     */
    function create(deps) {
        const api = deps.api;
        const doc = deps.doc || document;
        const $ = (id) => doc.getElementById(id);
        const modal = $('shellConnectionModal');
        if (!modal || !api) return null;
        const controller = root.CuttleModal ? root.CuttleModal.create(modal) : null;
        const els = {
            role: $('shellConnectionRole'),
            subtitle: $('shellConnectionSubtitle'),
            info: $('shellConnectionInfo'),
            warning: $('shellConnectionWarning'),
            input: $('shellConnectionHostInput'),
            connect: $('shellConnectionConnect'),
            local: $('shellConnectionLocal'),
            saved: $('shellConnectionSaved'),
            savedList: $('shellConnectionSavedList'),
            status: $('shellConnectionStatus'),
        };
        let cfg = deps.getConfig ? deps.getConfig() : null;
        let net = null;
        let busy = false;

        function setStatus(msg, isErr) {
            if (!els.status) return;
            els.status.textContent = msg || '';
            els.status.classList.toggle('is-error', !!isErr);
        }

        function setBusy(on) {
            busy = !!on;
            if (els.connect) els.connect.disabled = busy;
            if (els.local) els.local.disabled = busy || !isClient(cfg);
            if (els.savedList) {
                els.savedList.querySelectorAll('button').forEach((b) => { b.disabled = busy || b.dataset.current === '1'; });
            }
        }

        function renderInfo() {
            const view = describeConnection(cfg, net);
            if (els.role) {
                els.role.textContent = view.title;
                els.role.dataset.role = view.role;
            }
            if (els.subtitle) els.subtitle.textContent = view.subtitle;
            if (els.info) {
                els.info.replaceChildren();
                for (const row of view.rows) {
                    const dt = doc.createElement('dt');
                    dt.textContent = row.label;
                    const dd = doc.createElement('dd');
                    const val = doc.createElement('span');
                    val.className = 'shell-connection-value';
                    val.textContent = row.value;
                    dd.appendChild(val);
                    if (row.copy) {
                        const btn = doc.createElement('button');
                        btn.type = 'button';
                        btn.className = 'shell-connection-copy';
                        btn.textContent = 'Copy';
                        btn.setAttribute('aria-label', `Copy ${row.label}`);
                        btn.addEventListener('click', () => {
                            copyText(doc, row.value).then((ok) => {
                                btn.textContent = ok ? 'Copied' : 'Copy failed';
                                setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
                            });
                        });
                        dd.appendChild(btn);
                    }
                    els.info.append(dt, dd);
                }
            }
            if (els.warning) {
                els.warning.hidden = !view.warning;
                els.warning.textContent = view.warning;
            }
            if (els.local) {
                els.local.textContent = isClient(cfg) ? 'This PC hosts Cuttle' : 'This PC hosts Cuttle (current)';
            }
            renderSaved();
            setBusy(busy);
        }

        function renderSaved() {
            if (!els.saved || !els.savedList) return;
            const entries = (cfg && Array.isArray(cfg.recentHosts) ? cfg.recentHosts : [])
                .filter((e) => e && e.host && ['http', 'https'].includes(e.scheme));
            els.savedList.replaceChildren();
            els.saved.hidden = !entries.length;
            for (const entry of entries) {
                const current = isCurrentSaved(cfg, entry);
                const btn = doc.createElement('button');
                btn.type = 'button';
                btn.className = 'shell-connection-saved-entry';
                btn.dataset.current = current ? '1' : '0';
                btn.textContent = hostPortLabel(entry.host, entry.port);
                const small = doc.createElement('small');
                small.textContent = `${String(entry.scheme).toUpperCase()}${current ? ' · current' : ''}`;
                btn.appendChild(small);
                btn.addEventListener('click', () => runSwitch(
                    () => api.connectSavedHost(entry.index),
                    'Reconnecting to saved server…'
                ));
                els.savedList.appendChild(btn);
            }
        }

        async function runSwitch(fn, pendingMsg) {
            if (busy) return;
            setBusy(true);
            setStatus(pendingMsg);
            try {
                const result = await fn();
                if (!result || !result.ok) {
                    setStatus((result && result.error) || 'Could not switch Cuttle host.', true);
                    setBusy(false);
                    return;
                }
                if (result.unchanged) {
                    setStatus('Already connected here.');
                    setBusy(false);
                    return;
                }
                // Main process is loading the new origin; this document goes away.
                setStatus('Switching…');
            } catch (err) {
                setStatus((err && err.message) || String(err), true);
                setBusy(false);
            }
        }

        function connectTyped() {
            const raw = els.input ? els.input.value.trim() : '';
            if (!raw) {
                setStatus('Enter the host IP you use in Chrome.', true);
                if (els.input) els.input.focus();
                return;
            }
            runSwitch(() => api.connectHost(raw), 'Connecting…');
        }

        async function refresh() {
            try {
                const fresh = api.getConfig ? await api.getConfig() : null;
                if (fresh) {
                    cfg = fresh;
                    if (deps.onConfig) deps.onConfig(cfg);
                }
            } catch (_) {}
            renderInfo();
            if (!isClient(cfg) && deps.fetchNetworkInfo) {
                try {
                    net = await deps.fetchNetworkInfo();
                } catch (_) {
                    net = null;
                }
                renderInfo();
                if (deps.onConfig) deps.onConfig(cfg, net);
            }
        }

        function isOpen() {
            return controller ? controller.isOpen() : modal.classList.contains('active');
        }

        function open() {
            if (deps.getConfig) cfg = deps.getConfig() || cfg;
            setStatus('');
            if (els.input) els.input.value = '';
            renderInfo();
            if (controller) controller.open();
            else modal.classList.add('active');
            refresh();
        }

        function close() {
            if (controller) controller.close();
            else modal.classList.remove('active');
        }

        modal.querySelectorAll('[data-connection-close]').forEach((b) => b.addEventListener('click', close));
        modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
        doc.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen()) close(); });
        if (els.connect) els.connect.addEventListener('click', connectTyped);
        if (els.input) els.input.addEventListener('keydown', (e) => { if (e.key === 'Enter') connectTyped(); });
        if (els.local) {
            els.local.addEventListener('click', () => runSwitch(() => api.useLocal(), 'Starting Cuttle on this PC…'));
        }

        return { open, close, isOpen, refresh };
    }

    const api = { create, describeConnection, badgeTooltip, hostPortLabel, connectAddress };
    root.CuttleShellConnection = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
