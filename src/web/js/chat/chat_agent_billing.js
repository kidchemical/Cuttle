/* ================================================================
   Cuttle Chat — per-chat billing order (chat_agent_billing.js).
   Owner: the composer agent popup's "Billing" choice — subscription
   first or API first — for agents that can bill either way (Claude
   Code today). The server owns the pin (`/api/claude/billing`); this
   module caches it per chat, holds a pick made before the chat exists
   and hands it to the first send as `agent_pins.claude.billing`.

   `CuttleChatAgentBilling.options(defaultMode)` is pure (select rows).
   `CuttleChatAgentBilling.create(host)` → `{ spec(agentId),
   attachToRequest(body) }`; host: `{ getSessionId, fetch, toast }`.
   One message can still override with `/use-api` / `/use-subscription`.
   ================================================================ */
(function (root) {
    'use strict';

    const AGENTS = { claude: '/api/claude/billing' };
    const LABELS = { subscription: 'Subscription first', api: 'API first' };

    /** Select rows for the popup; `defaultMode` names what Default means. Pure. */
    function options(defaultMode) {
        const def = LABELS[defaultMode] ? defaultMode : 'subscription';
        return [
            { value: 'default', label: 'Default (' + LABELS[def] + ')' },
            { value: 'subscription', label: LABELS.subscription },
            { value: 'api', label: LABELS.api },
        ];
    }

    function create(host) {
        const h = host || {};
        const cache = {};            // `${agent}\x1f${sid}` → { billing, default }
        const defaults = {};         // agent → settings default
        const pending = {};          // agent → pick made before the chat had an id
        const sid = () => {
            const v = h.getSessionId ? h.getSessionId() : null;
            return v != null && v !== '' ? String(v) : '';
        };
        const doFetch = (url, opts) => (h.fetch ? h.fetch(url, opts) : root.fetch(url, opts));

        function load(agent) {
            const s = sid();
            const url = AGENTS[agent] + (s ? '?session=' + encodeURIComponent(s) : '');
            return doFetch(url, { credentials: 'same-origin' })
                .then((r) => r.json())
                .then((j) => {
                    if (!j || !j.success) return;
                    defaults[agent] = j.default;
                    if (s) cache[agent + '\x1f' + s] = j.billing || 'default';
                })
                .catch(() => {});
        }

        function current(agent) {
            const s = sid();
            if (!s) return pending[agent] || 'default';
            return cache[agent + '\x1f' + s] || 'default';
        }

        function set(agent, mode) {
            const s = sid();
            if (!s) {
                if (mode === 'default') delete pending[agent];
                else pending[agent] = mode;
                return;
            }
            cache[agent + '\x1f' + s] = mode;
            doFetch(AGENTS[agent], {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: s, billing: mode }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!j || !j.success) {
                        if (h.toast) h.toast((j && j.error) || 'Could not save billing choice', 'error');
                        return;
                    }
                    cache[agent + '\x1f' + s] = j.billing || 'default';
                    if (j.billing === 'api' && j.api_ready && j.api_ready !== 'ready' && h.toast) {
                        h.toast(j.api_ready === 'no_key'
                            ? 'API first: no Anthropic key in Settings → Providers — turns use the subscription'
                            : 'API first: the monthly API budget is spent — turns use the subscription', 'warning');
                    }
                })
                .catch(() => {});
        }

        /** Chip-popup setting for one agent, or null when it bills one way only. */
        function spec(agentId) {
            const agent = String(agentId || '').toLowerCase();
            if (!AGENTS[agent]) return null;
            return {
                label: 'Billing',
                load: () => load(agent),
                options: () => options(defaults[agent]),
                current: () => current(agent),
                set: (mode) => set(agent, mode),
            };
        }

        /** First send of a new chat carries the pre-session pick. */
        function attachToRequest(body) {
            if (!body || typeof body !== 'object' || sid()) return;
            Object.keys(pending).forEach((agent) => {
                const pins = body.agent_pins && typeof body.agent_pins === 'object' ? body.agent_pins : {};
                pins[agent] = Object.assign({}, pins[agent] || {}, { billing: pending[agent] });
                body.agent_pins = pins;
                delete pending[agent];
            });
        }

        return { spec, attachToRequest };
    }

    root.CuttleChatAgentBilling = { options, create };
})(typeof window !== 'undefined' ? window : globalThis);
