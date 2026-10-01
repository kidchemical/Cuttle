/* Live usage DOM lifecycle. One scheduler/fetch per account+range in the shell;
 * the API also coalesces requests from separate windows/devices. */
(function (root) {
    'use strict';
    const AGENTS = new Set(['cursor', 'codex', 'muse', 'hermes', 'opencode']);
    const PERIOD = 60000;
    const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    function render(text, format) {
        if (typeof text !== 'string') return null;
        const match = text.match(/^\s*<cuttle_usage_live>([\s\S]*?)<\/cuttle_usage_live>\s*$/i);
        if (!match) return null;
        try {
            const data = JSON.parse(match[1]);
            if (!AGENTS.has(data.agent) || typeof data.markdown !== 'string' || data.markdown.includes('<cuttle_usage_live>')) return null;
            const days = Math.max(1, Math.min(Number(data.days) || 30, 365));
            return '<section class="cuttle-usage-live" data-usage-agent="' + data.agent
                + '" data-usage-days="' + days + '"><div class="usage-live-report">'
                + format(data.markdown) + '</div><small class="usage-live-status">'
                + escape(status(data)) + '</small></section>';
        } catch (_) { return null; }
    }
    function status(data) {
        return data.error || ('Live · refreshes every minute while visible'
            + (data.updated_at ? ' · updated ' + new Date(data.updated_at * 1000).toLocaleTimeString() : ''));
    }
    function createBroker(host) {
        const sources = new Set(), cache = new Map(), pending = new Map(), attempted = new Map();
        let timer = null;
        async function tick() {
            if (timer !== null) host.clearTimeout(timer);
            timer = null;
            const groups = new Map();
            sources.forEach(source => source().forEach(target => {
                if (!groups.has(target.key)) groups.set(target.key, []);
                groups.get(target.key).push(target);
            }));
            groups.forEach((targets, key) => {
                const apply = data => targets.forEach(target => target.apply(data));
                if (cache.has(key)) apply(cache.get(key));
                if (pending.has(key) || Date.now() - (attempted.get(key) || 0) < PERIOD) return;
                attempted.set(key, Date.now());
                const [agent, days] = key.split(':');
                const request = host.fetch('/api/usage-live?agent=' + agent + '&days=' + days, {credentials: 'same-origin'})
                    .then(response => { if (!response.ok) throw new Error('Usage refresh failed'); return response.json(); })
                    .then(data => { cache.set(key, data); apply(data); })
                    .catch(() => targets.forEach(target => target.failed()))
                    .finally(() => pending.delete(key));
                pending.set(key, request);
            });
            if (groups.size) {
                const delay = Math.min(...Array.from(groups.keys(), key =>
                    pending.has(key) ? PERIOD : Math.max(100, PERIOD - (Date.now() - (attempted.get(key) || 0)))));
                timer = host.setTimeout(tick, delay);
            }
        }
        return {
            add(source) { sources.add(source); tick(); },
            remove(source) { sources.delete(source); tick(); },
            tick,
        };
    }
    function start(format) {
        let host = root;
        try { if (root.top.location.origin === root.location.origin) host = root.top; } catch (_) { /* standalone */ }
        const broker = host.__cuttleUsageLiveBroker || (host.__cuttleUsageLiveBroker = createBroker(host));
        const applied = new WeakMap();
        const source = () => {
            if (root.document.hidden) return [];
            if (root.frameElement && !root.frameElement.getClientRects().length) return [];
            return Array.from(root.document.querySelectorAll('.cuttle-usage-live')).filter(node => {
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < root.innerHeight
                    && rect.right > 0 && rect.left < root.innerWidth;
            }).map(node => ({
                key: node.dataset.usageAgent + ':' + node.dataset.usageDays,
                apply(data) {
                    if (!node.isConnected || applied.get(node) === JSON.stringify(data)) return;
                    applied.set(node, JSON.stringify(data));
                    node.querySelector('.usage-live-report').innerHTML = format(data.markdown);
                    node.querySelector('.usage-live-status').textContent = status(data);
                },
                failed() { if (node.isConnected) node.querySelector('.usage-live-status').textContent = 'Refresh failed; retrying in one minute.'; },
            }));
        };
        let scheduled = false;
        const scan = () => {
            if (scheduled) return;
            scheduled = true;
            root.requestAnimationFrame(() => { scheduled = false; broker.tick(); });
        };
        const observer = new MutationObserver(scan);
        observer.observe(root.document.body, {childList: true, subtree: true});
        root.document.addEventListener('scroll', scan, true);
        root.document.addEventListener('visibilitychange', scan);
        root.addEventListener('resize', scan);
        root.addEventListener('pagehide', () => { observer.disconnect(); broker.remove(source); });
        root.addEventListener('pageshow', event => {
            if (event.persisted) {
                observer.observe(root.document.body, {childList: true, subtree: true});
                broker.add(source);
            }
        });
        broker.add(source);
    }
    root.CuttleUsageLive = {render, start, createBroker};
    if (typeof module !== 'undefined' && module.exports) module.exports = root.CuttleUsageLive;
})(globalThis);
