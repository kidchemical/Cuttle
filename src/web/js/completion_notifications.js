/* Opt-in completion delivery. One broker per shell, shared by its chat panes.
 * Starts and delivered ids persist in this browser; no transcript text is stored.
 * Completion never requests permission. No push/background-service dependency. */
(function (root) {
    'use strict';
    const PREFIX = 'cuttle.completion.';
    const MIN_MS = 60000;
    function sessionKey(value) {
        return String(value == null ? '' : value).replace(/^db_session_/, '').replace(/^CH-0*(\d+)$/i, '$1');
    }
    function plan(event, prefs, now) {
        if (!event || !sessionKey(event.sessionId) || !event.id) return null;
        const outcome = event.outcome;
        if (!['done', 'failed', 'cancelled'].includes(outcome)) return null;
        if (event.kind !== 'mesh' && now - Number(event.startedAt || now) < MIN_MS) return null;
        if (!prefs.enabled || prefs.master === false || prefs.permission !== 'granted') return null;
        const labels = {done: event.kind === 'mesh' ? 'Render batch finished' : 'Reply ready', failed: 'Work failed', cancelled: 'Work cancelled'};
        const title = 'Cuttle — ' + labels[outcome];
        const body = prefs.details && event.label ? String(event.label).slice(0, 180) : 'Open the chat to view the result.';
        return {title, body, tag: 'cuttle-completion-' + event.id, sessionId: sessionKey(event.sessionId)};
    }
    function createBroker(host) {
        let pending = null;
        let deliveryChain = Promise.resolve();
        const memory = new Map();
        function get(key) { try { return host.storage.getItem(PREFIX + key); } catch (_) { return memory.get(key) || null; } }
        function set(key, value) { memory.set(key, value); try { host.storage.setItem(PREFIX + key, value); } catch (_) {} }
        function remove(key) { memory.delete(key); try { host.storage.removeItem(PREFIX + key); } catch (_) {} }
        function prefs() { return {enabled: get('enabled') === 'true', details: get('details') === 'true', master: host.masterEnabled(), permission: host.permission()}; }
        function begin(sessionId) {
            const sid = sessionKey(sessionId);
            const rec = {id: host.id(), startedAt: host.now()};
            if (sid) set('turn.' + sid, JSON.stringify(rec)); else pending = rec;
        }
        function adopt(previous, next) {
            const old = sessionKey(previous), sid = sessionKey(next);
            const rec = old ? get('turn.' + old) : pending && JSON.stringify(pending);
            if (!sid || !rec || old === sid) return;
            set('turn.' + sid, rec);
            if (old) remove('turn.' + old);
            pending = null;
        }
        async function complete(event) {
            const run = async () => {
                if (!plan(event, prefs(), host.now())) return false;
                if (!await host.flagEnabled()) return false;
                const p = plan(event, prefs(), host.now());
                if (!p) return false;
                let delivered = [];
                try { delivered = JSON.parse(get('delivered') || '[]'); } catch (_) {}
                if (!Array.isArray(delivered)) delivered = [];
                if (delivered.includes(event.id)) return false;
                try { await host.show(p); } catch (_) { return false; }
                set('delivered', JSON.stringify([...delivered, event.id].slice(-256)));
                return true;
            };
            if (host.lock) return host.lock(run);
            const next = deliveryChain.then(run);
            deliveryChain = next.catch(() => false);
            return next;
        }
        function finishTurn(sessionId, outcome, label) {
            const sid = sessionKey(sessionId);
            let rec;
            try { rec = JSON.parse(get('turn.' + sid) || 'null'); } catch (_) {}
            if (!rec) return Promise.resolve(false);
            if (host.now() - rec.startedAt > 24 * 60 * 60 * 1000) { remove('turn.' + sid); return Promise.resolve(false); }
            // Consume before asynchronous delivery, so a second sync cannot notify this turn again.
            remove('turn.' + sid);
            return complete({...rec, sessionId: sid, kind: 'turn', outcome, label});
        }
        function cancel(sessionId) { if (!sessionKey(sessionId)) pending = null; return finishTurn(sessionId, 'cancelled'); }
        function configure(enabled, details) { set('enabled', String(!!enabled)); if (details != null) set('details', String(!!details)); }
        return {begin, adopt, complete, finishTurn, cancel, configure, prefs};
    }
    async function flagEnabled(win) {
        try {
            const r = await win.fetch('/api/experimental/flags', {credentials: 'include', cache: 'no-store'});
            const d = await r.json();
            return !!(r.ok && d.success && !d.kill_switch && (d.flags || []).some(f => f.id === 'completion_notifications' && f.enabled));
        } catch (_) { return false; }
    }
    function browserBroker(win) {
        try { if (win.top !== win && win.top.CuttleCompletionNotifications) return win.top.CuttleCompletionNotifications.broker(); } catch (_) {}
        if (win.__cuttleCompletionBroker) return win.__cuttleCompletionBroker;
        win.__cuttleCompletionBroker = createBroker({
            storage: {getItem: k => win.localStorage.getItem(k), setItem: (k,v) => win.localStorage.setItem(k,v), removeItem: k => win.localStorage.removeItem(k)},
            now: () => Date.now(), id: () => win.crypto && win.crypto.randomUUID ? win.crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2),
            permission: () => win.Notification ? win.Notification.permission : 'unsupported',
            masterEnabled: () => { try { return win.localStorage.getItem('notificationsEnabled') !== 'false'; } catch (_) { return false; } },
            flagEnabled: () => flagEnabled(win),
            lock: fn => win.navigator.locks ? win.navigator.locks.request('cuttle-completion-delivery', fn) : fn(),
            show: p => {
                const n = new win.Notification(p.title, {body:p.body, tag:p.tag});
                n.onclick = () => {
                    n.close(); win.focus();
                    if (typeof win.openChatFromNotification === 'function') win.openChatFromNotification(p.sessionId);
                    else win.location.assign('/chat_page.html?chat=' + encodeURIComponent(p.sessionId));
                };
            },
        });
        return win.__cuttleCompletionBroker;
    }
    function settings(win) {
        const button = win.document.getElementById('completionNotificationsButton');
        const details = win.document.getElementById('completionNotificationsDetails');
        const status = win.document.getElementById('completionNotificationsStatus');
        if (!button || !details || !status) return;
        const broker = browserBroker(win);
        let available = false;
        async function refresh() {
            available = await flagEnabled(win);
            const p = broker.prefs();
            details.checked = p.details;
            const supported = !!win.Notification && win.isSecureContext;
            button.disabled = !available || !supported;
            button.textContent = p.enabled ? 'Turn off completion notifications' : 'Enable completion notifications';
            status.textContent = !available ? 'Enable Completion notifications in Experimental first.'
                : !supported ? 'OS notifications require a supported browser and a secure connection.'
                : p.permission === 'denied' ? 'Permission blocked. Allow notifications in browser site settings to enable them.'
                : p.enabled && p.master === false ? 'Completion notifications are paused by the Notifications switch above.'
                : p.enabled && p.permission === 'granted' ? 'Enabled on this device. Replies taking at least 1 minute and mesh batches notify while this page is open.'
                : 'Off on this device. Enabling asks for browser permission. Closed pages and suspended mobile apps cannot receive these notifications.';
        }
        button.addEventListener('click', async () => {
            if (!available) return;
            if (broker.prefs().enabled) broker.configure(false);
            else {
                // Must run directly in the click handler, before any network await.
                try { broker.configure((await win.Notification.requestPermission()) === 'granted'); }
                catch (_) { broker.configure(false); }
            }
            await refresh();
        });
        details.addEventListener('change', () => broker.configure(broker.prefs().enabled, details.checked));
        win.addEventListener('focus', refresh);
        win.addEventListener('storage', refresh);
        win.document.addEventListener('visibilitychange', () => { if (!win.document.hidden) refresh(); });
        refresh();
        return {refresh};
    }
    const api = {plan, createBroker, sessionKey, broker: () => browserBroker(root), settings: () => settings(root)};
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.CuttleCompletionNotifications = api;
})(typeof window !== 'undefined' ? window : globalThis);
