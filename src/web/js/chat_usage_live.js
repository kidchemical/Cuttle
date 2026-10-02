/* Live usage DOM lifecycle. One scheduler/fetch per account+range in the shell;
 * the API also coalesces requests from separate windows/devices. */
(function (root) {
    'use strict';
    const AGENTS = new Set(['cursor', 'codex', 'muse', 'hermes', 'opencode']);
    const PERIOD = 60000;
    const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    function reportBody(text, format) {
        const title = text.match(/^\*\*((?:Cursor|Codex|Muse Code|Hermes|OpenCode) — usage)\*\*(?:\r?\n|$)/);
        if (!title) return format(text);
        const lines = text.slice(title[0].length).trim().split('\n');
        const metadata = [];
        while (lines.length && /^- /.test(lines[0])) {
            const line = lines.shift().slice(2);
            const colon = line.indexOf(':');
            const label = colon >= 0 ? line.slice(0, colon) : 'Activity';
            const value = colon >= 0 ? line.slice(colon + 1).trim() : line;
            const inline = escape(value).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            metadata.push('<div class="usage-report-detail"><dt>' + escape(label) + '</dt><dd>' + inline + '</dd></div>');
        }
        return '<header class="usage-report-heading"><strong>' + escape(title[1]) + '</strong>'
            + (metadata.length ? '<dl class="usage-report-details">' + metadata.join('') + '</dl>' : '')
            + '</header><div class="usage-report-body">' + format(lines.join('\n').trim()) + '</div>';
    }
    function render(text, format) {
        if (typeof text !== 'string') return null;
        const resets = text.match(/<cuttle_codex_resets>([\s\S]*?)<\/cuttle_codex_resets>/i);
        // Live wrappers are decoded first; their markdown comes back through render.
        if (resets && !text.trim().startsWith('<cuttle_usage_live>')) {
            try {
                const data = JSON.parse(resets[1]);
                if (!Array.isArray(data.credits)) return null;
                const rows = data.credits.slice().sort((a, b) =>
                    (a.expiresAt ?? Infinity) - (b.expiresAt ?? Infinity)).map(credit => {
                    if (!credit || typeof credit.id !== 'string') return '';
                    const expires = credit.expiresAt == null ? 'No expiration'
                        : 'Expires ' + new Date(credit.expiresAt * 1000).toLocaleString();
                    const available = credit.status === 'available' && credit.resetType === 'codexRateLimits'
                        && (credit.expiresAt == null || credit.expiresAt * 1000 > Date.now());
                    const soon = available && credit.expiresAt != null && credit.expiresAt * 1000 - Date.now() < 7 * 86400000;
                    return '<div class="codex-reset-row' + (soon ? ' codex-reset-row--soon' : '') + '">'
                        + '<span class="codex-reset-icon" aria-hidden="true">↻</span><div class="codex-reset-info">'
                        + '<div class="codex-reset-title"><strong>' + escape(credit.title || 'Usage limit reset')
                        + '</strong>' + (soon ? '<span class="codex-reset-badge">Expires soon</span>' : '') + '</div>'
                        + '<small class="codex-reset-expiry">' + escape(expires) + '</small>'
                        + (credit.description ? '<p class="codex-reset-description">' + escape(credit.description) + '</p>' : '')
                        + '</div>'
                        + (available ? '<button class="codex-reset-button" type="button" data-codex-reset="' + escape(credit.id)
                            + '" data-account-id="' + escape(data.account_id || '') + '">Use reset</button>'
                            : '<span class="codex-reset-unavailable">' + escape(credit.status === 'available' ? 'Unavailable' : credit.status || 'Unknown status') + '</span>')
                        + '</div>';
                }).join('');
                const missing = Number(data.available_count) > data.credits.length
                    ? '<p>Additional resets are available; Codex returned only part of the list.</p>' : '';
                const count = Math.max(0, Math.floor(Number(data.available_count) || 0));
                const html = '<section class="codex-reset-credits"><div class="codex-reset-heading">'
                    + '<strong>Saved resets</strong><span class="codex-reset-count">' + count + ' available</span></div>'
                    + (rows || '<p>No saved resets available.</p>')
                    + missing + '<p role="status" class="codex-reset-status"></p></section>';
                const parts = text.split(resets[0]);
                const summary = parts[0].replace(/\nRate-limit resets available: \*\*\d+\*\*/g, '').trim();
                return '<div class="cuttle-usage-report codex-usage-report">' + reportBody(summary, format)
                    + html + '<footer class="codex-usage-footer">'
                    + format((parts[1] || '').trim()) + '</footer></div>';
            } catch (_) { return null; }
        }
        const match = text.match(/^\s*<cuttle_usage_live>([\s\S]*?)<\/cuttle_usage_live>\s*$/i);
        if (!match) {
            if (/^\*\*(?:Cursor|Codex|Muse Code|Hermes|OpenCode) — usage\*\*(?:\r?\n|$)/.test(text)) {
                return '<section class="cuttle-usage-report">' + reportBody(text, format) + '</section>';
            }
            return null;
        }
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
        return data.error || ((data.notice ? data.notice + ' ' : '') + 'Live · refreshes every minute while visible'
            + (data.updated_at ? ' · updated ' + new Date(data.updated_at * 1000).toLocaleTimeString() : ''));
    }
    function createBroker(host) {
        const sources = new Set(), cache = new Map(), pending = new Map(), attempted = new Map(), revisions = new Map();
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
                const revision = revisions.get(key) || 0;
                const request = host.fetch('/api/usage-live?agent=' + agent + '&days=' + days, {credentials: 'same-origin'})
                    .then(response => { if (!response.ok) throw new Error('Usage refresh failed'); return response.json(); })
                    .then(data => {
                        if ((revisions.get(key) || 0) !== revision) return;
                        cache.set(key, data); apply(data);
                    })
                    .catch(() => {
                        if ((revisions.get(key) || 0) === revision) targets.forEach(target => target.failed());
                    })
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
            update(key, data) {
                revisions.set(key, (revisions.get(key) || 0) + 1);
                cache.set(key, data); attempted.set(key, Date.now()); tick();
            },
        };
    }
    function start(format) {
        let host = root;
        try { if (root.top.location.origin === root.location.origin) host = root.top; } catch (_) { /* standalone */ }
        const broker = host.__cuttleUsageLiveBroker || (host.__cuttleUsageLiveBroker = createBroker(host));
        const applied = new WeakMap();
        root.document.addEventListener('click', async event => {
            const button = event.target.closest('[data-codex-reset]');
            if (!button || button.disabled) return;
            const report = button.closest('.codex-usage-report');
            const statusNode = report.querySelector('.codex-reset-status');
            const title = button.closest('.codex-reset-row').querySelector('strong').textContent;
            if (!root.confirm('Use “' + title + '” now? This spends one saved reset and cannot be undone.')) return;
            // Preserve across re-renders and panes, including ambiguous network failures.
            const storageKey = 'cuttle.codex.reset.' + button.dataset.accountId + '.' + button.dataset.codexReset;
            let key;
            try {
                key = host.sessionStorage.getItem(storageKey) || host.crypto.randomUUID();
                host.sessionStorage.setItem(storageKey, key);
            } catch (_) {
                key = button.dataset.redemptionKey || root.crypto.randomUUID();
            }
            button.dataset.redemptionKey = key;
            report.dataset.redeeming = 'true';
            report.querySelectorAll('[data-codex-reset]').forEach(b => { b.disabled = true; });
            statusNode.textContent = 'Redeeming reset…';
            try {
                const response = await host.fetch('/api/usage/codex/reset', {
                    method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({credit_id: button.dataset.codexReset,
                        account_id: button.dataset.accountId || null, idempotency_key: key, confirmed: true})
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Redemption failed');
                // Update the static report as well as every visible live Codex report.
                report.innerHTML = format(data.snapshot.markdown) + '<p role="status">' + escape(data.message) + '</p>';
                delete report.dataset.redeeming;
                broker.update('codex:30', {...data.snapshot, notice:data.message});
            } catch (error) {
                delete report.dataset.redeeming;
                statusNode.textContent = error.message || 'Could not verify redemption. Retry the same button.';
                report.querySelectorAll('[data-codex-reset]').forEach(b => { b.disabled = false; });
            }
        });
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
                    if (node.querySelector('[data-redeeming="true"]')) return;
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
