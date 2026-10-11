/* ================================================================
   Cuttle Chat — composer context gauge (chat_context_gauge.js).
   Owner: the agent-context radial shown next to the composer (the chat
   and welcome buttons), its tooltip/aria plan, the usage->status math
   that seeds the ring from a turn's usage blob, the `/api/agent-context`
   fetch/refresh loop, the details popover and the compact action.

   Interface: pure helpers on the namespace (`formatContextTokenCount`,
   `normalizeAgentHint`, `stickyAgentIdFromSlashCtx`, `gaugeDomPlan`,
   `computeUsageStatus`, `renderPopoverHtml`) plus `create(deps)` → one
   controller per chat frame. The page keeps the call sites (note a turn's
   usage, schedule a refresh after chip/composer changes, init) and every
   page global; the controller receives them as explicit capabilities and
   never reads page scope.

   deps: { win, fetch, escapeHtml, normalizeUsage, getSlashCtx,
           getSessionId, getProjectPath, toast }.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const CONTEXT_GAUGE_AGENTS = new Set(['cursor', 'muse', 'opencode', 'codex', 'antigravity', 'hermes', 'claude', 'deepseek']);
    const GAUGE_BTN_IDS = ['chatContextGauge', 'welcomeContextGauge'];

    function formatContextTokenCount(n) {
        const v = Number(n) || 0;
        if (v >= 1_000_000) {
            const m = v / 1_000_000;
            return (m >= 10 ? Math.round(m) : Math.round(m * 10) / 10) + 'M';
        }
        if (v >= 1000) {
            const k = v / 1000;
            return (k >= 100 ? Math.round(k) : Math.round(k * 10) / 10) + 'k';
        }
        return String(Math.round(v));
    }

    function normalizeAgentHint(raw) {
        const s = String(raw || '').toLowerCase().replace(/^\//, '');
        for (const aid of CONTEXT_GAUGE_AGENTS) {
            if (s === aid || s.startsWith(aid + ' ') || s.startsWith(aid + '_')) return aid;
        }
        return null;
    }

    /**
     * Sticky agent id from a page `slashCtx` snapshot ({ chat, welcome },
     * each `{ chips }`). Pure: callers pass the object, never page scope.
     */
    function stickyAgentIdFromSlashCtx(slashCtx) {
        for (const key of ['chat', 'welcome']) {
            const chips = (slashCtx && slashCtx[key] && slashCtx[key].chips) || [];
            for (const chip of chips) {
                const prefix = String((chip && chip.prefix) || '').toLowerCase();
                const meta = String((chip && chip.meta) || '').toLowerCase();
                const cat = String((chip && chip.category) || '').toLowerCase();
                const label = String((chip && chip.label) || '').toLowerCase();
                for (const aid of CONTEXT_GAUGE_AGENTS) {
                    if (
                        prefix === '/' + aid
                        || prefix.startsWith('/' + aid + ' ')
                        || cat === aid
                        || meta.startsWith('/' + aid)
                        || (aid === 'muse' && label.startsWith('muse code'))
                        || (aid === 'opencode' && label.startsWith('opencode'))
                        || (aid === 'codex' && label.startsWith('codex'))
                        || (aid === 'cursor' && (label.startsWith('cursor') || cat === 'cursor'))
                    ) {
                        return aid;
                    }
                }
            }
        }
        return null;
    }

    /**
     * Decide the ring/aria/tooltip for one gauge button from a status.
     * Pure: returns the values the caller writes to the DOM.
     */
    function gaugeDomPlan(status, fallbackAgent) {
        const pct = Math.max(0, Math.min(100, Number(status && status.percent) || 0));
        const used = Number(status && status.used_tokens) || 0;
        const limit = Number(status && status.limit_tokens) || 0;
        const agent = (status && status.agent_id) || fallbackAgent || 'agent';
        const aggregated = (status && status.token_source) === 'aggregated' && used <= 0;
        const filled = aggregated ? 0 : Math.round(pct * 10) / 10;
        const tipParts = [
            agent.charAt(0).toUpperCase() + agent.slice(1) + ' context',
        ];
        if (aggregated) {
            tipParts.push('awaiting next reply');
        } else {
            tipParts.push(Math.round(pct) + '%');
            if (used || limit) {
                tipParts.push(
                    formatContextTokenCount(used) + ' / ' + formatContextTokenCount(limit)
                );
            }
        }
        if (status && status.hint) tipParts.push(status.hint);
        tipParts.push('Click for details / Compact');
        return {
            agent,
            aggregated,
            dashArray: filled + ' ' + (100 - filled),
            warn: !aggregated && pct >= 60 && pct < 85,
            hot: !aggregated && pct >= 85,
            title: tipParts.join(' · '),
            ariaLabel: aggregated
                ? agent + ' context awaiting next reply'
                : (agent + ' context ' + Math.round(pct) + ' percent'),
        };
    }

    /**
     * Seed a gauge status from a turn's usage blob. Pure (normalizeUsage is
     * injected). Returns a status object, or null when there is no explicit
     * occupancy stamp to paint — the caller then just schedules a refresh.
     */
    function computeUsageStatus(usage, agent, prevStatus, normalizeUsage) {
        if (!agent || !CONTEXT_GAUGE_AGENTS.has(agent)) return null;
        const u = (typeof normalizeUsage === 'function') ? normalizeUsage(usage) : null;
        const raw = usage && typeof usage === 'object' ? usage : {};
        const peak = Number(
            raw.context_tokens != null ? raw.context_tokens
                : (raw.peak_context_tokens != null ? raw.peak_context_tokens : raw.contextTokens)
        ) || 0;
        // Billing input is summed across calls. Only seed the ring from an
        // explicit occupancy stamp; the API supplies live/transcript fallbacks.
        let fill = peak;
        if (agent === 'cursor' && peak > 0) {
            const cr = Number(raw.cache_read_tokens || raw.cacheReadTokens || 0) || 0;
            const cw = Number(raw.cache_write_tokens || raw.cacheWriteTokens || 0) || 0;
            const sumIn = ((u && u.prompt_tokens) || 0) + cr + cw;
            if (peak >= 400000 && cr >= 200000 && peak >= cr &&
                    Math.abs(peak - sumIn) <= Math.max(2000, peak * 0.03)) fill = 0;
        }
        if (!fill) return null;
        const prev = prevStatus || {};
        const limit = Number(prev.limit_tokens) || (
            (agent === 'opencode' || agent === 'codex') ? 200000 : 1000000
        );
        const pct = Math.min(100, Math.round((1000 * fill) / limit) / 10);
        return {
            success: true,
            agent_id: agent,
            model: (u && u.model) || prev.model || '',
            used_tokens: fill,
            limit_tokens: limit,
            percent: pct,
            estimated: true,
            compact_available: true,
            hint: pct >= 85
                ? 'Context is nearly full — compact soon to avoid overflows.'
                : (pct >= 60
                    ? 'Context is filling up — compact when the thread feels heavy.'
                    : 'Context looks healthy.'),
            has_resume: prev.has_resume,
        };
    }

    /**
     * Popover markup for a status. Pure: escapeHtml and busy are injected.
     */
    function renderPopoverHtml(status, opts) {
        const o = opts || {};
        const escapeHtml = typeof o.escapeHtml === 'function' ? o.escapeHtml : String;
        const busy = !!o.busy;
        const agent = (status && status.agent_id) || o.fallbackAgent || 'agent';
        const pct = Math.round(Number(status && status.percent) || 0);
        const used = Number(status && status.used_tokens) || 0;
        const limit = Number(status && status.limit_tokens) || 0;
        const model = (status && status.model) ? String(status.model) : '';
        const hint = (status && status.hint) || '';
        const aggregated = (status && status.token_source) === 'aggregated' && used <= 0;
        const label = agent.charAt(0).toUpperCase() + agent.slice(1);
        const meter = aggregated
            ? `<strong>—</strong> · waiting for next reply`
            : (`<strong>${pct}%</strong>` +
                (used || limit
                    ? ` · ${escapeHtml(formatContextTokenCount(used))} / ${escapeHtml(formatContextTokenCount(limit))}`
                    : '') +
                (model ? ` · ${escapeHtml(model)}` : ''));
        const canCompact = !(status && status.compact_available === false);
        return (
            `<p class="context-gauge-popover-title">${escapeHtml(label)} context</p>` +
            `<p class="context-gauge-popover-meter">${meter}</p>` +
            (hint ? `<p class="context-gauge-popover-hint">${escapeHtml(hint)}</p>` : '') +
            `<div class="context-gauge-popover-actions">` +
            (canCompact
                ? (`<button type="button" class="context-gauge-compact-btn" data-action="compact"` +
                    (busy ? ' disabled' : '') +
                    `>${busy ? 'Compacting…' : 'Compact'}</button>`)
                : '') +
            `<button type="button" class="context-gauge-popover-close" data-action="close">Close</button>` +
            `</div>`
        );
    }

    /**
     * One gauge controller per chat frame.
     * @param {{win: Window, fetch: Function, escapeHtml: Function,
     *          normalizeUsage: Function, getSlashCtx: Function,
     *          getSessionId: Function, getProjectPath: Function,
     *          toast: Function}} deps
     */
    function create(deps) {
        const frameWin = deps.win;
        const frameDoc = frameWin.document;
        const doFetch = deps.fetch;
        const escapeHtml = deps.escapeHtml;
        const normalizeUsage = deps.normalizeUsage;
        const getSlashCtx = deps.getSlashCtx;
        const getSessionId = deps.getSessionId;
        const getProjectPath = deps.getProjectPath;
        const toast = deps.toast || function () {};

        let status = null;
        let fetchTimer = 0;
        let busy = false;
        let popoverEl = null;

        function stickyAgentId() {
            return stickyAgentIdFromSlashCtx(getSlashCtx());
        }

        function applyGaugeDom(btn, payload) {
            if (!btn) return;
            const plan = gaugeDomPlan(payload, stickyAgentId());
            const arc = btn.querySelector('.context-gauge-arc');
            if (arc) arc.setAttribute('stroke-dasharray', plan.dashArray);
            btn.classList.toggle('context-gauge--warn', plan.warn);
            btn.classList.toggle('context-gauge--hot', plan.hot);
            btn.title = plan.title;
            btn.setAttribute('aria-label', plan.ariaLabel);
        }

        function setVisible(visible) {
            GAUGE_BTN_IDS.forEach((id) => {
                const btn = frameDoc.getElementById(id);
                if (!btn) return;
                btn.hidden = !visible;
                if (!visible) {
                    btn.classList.remove('context-gauge--warn', 'context-gauge--hot');
                    const arc = btn.querySelector('.context-gauge-arc');
                    if (arc) arc.setAttribute('stroke-dasharray', '0 100');
                }
            });
            if (!visible) closePopover();
        }

        function paintFromStatus(next) {
            status = next && next.success ? next : null;
            const agent = stickyAgentId();
            if (!agent) {
                setVisible(false);
                return;
            }
            setVisible(true);
            const payload = next && next.success
                ? next
                : {
                    success: true,
                    agent_id: agent,
                    percent: 0,
                    used_tokens: 0,
                    limit_tokens: 1000000,
                    hint: 'Waiting for the next turn to report context size.',
                };
            GAUGE_BTN_IDS.forEach((id) => {
                applyGaugeDom(frameDoc.getElementById(id), payload);
            });
            const pop = popoverEl;
            if (pop && !pop.hidden) renderPopover(pop, payload);
        }

        function noteUsage(usage, agentHint) {
            const agent = normalizeAgentHint(agentHint) || stickyAgentId();
            if (!agent || !CONTEXT_GAUGE_AGENTS.has(agent)) return;
            const next = computeUsageStatus(usage, agent, status, normalizeUsage);
            if (!next) {
                scheduleRefresh(250);
                return;
            }
            paintFromStatus(next);
            scheduleRefresh(250);
        }

        async function fetchStatus(opts) {
            const live = !!(opts && opts.live);
            const agent = stickyAgentId();
            if (!agent) {
                setVisible(false);
                status = null;
                return null;
            }
            setVisible(true);
            const sessionId = getSessionId();
            if (!sessionId) {
                paintFromStatus({
                    success: true,
                    agent_id: agent,
                    percent: 0,
                    used_tokens: 0,
                    limit_tokens: (agent === 'opencode' || agent === 'codex') ? 200000 : 1000000,
                    hint: 'No agent session yet — send a message to start tracking context.',
                });
                return status;
            }
            const qs = new URLSearchParams();
            qs.set('agent', agent);
            qs.set('session', String(sessionId));
            if (live) qs.set('live', '1');
            const path = getProjectPath();
            if (path) qs.set('path', path);
            try {
                const res = await doFetch('/api/agent-context?' + qs.toString(), {
                    credentials: 'same-origin',
                });
                const data = await res.json().catch(() => ({}));
                if (data && data.success) {
                    paintFromStatus(data);
                    return data;
                }
                paintFromStatus({
                    success: true,
                    agent_id: agent,
                    percent: (status && status.percent) || 0,
                    used_tokens: (status && status.used_tokens) || 0,
                    limit_tokens: (status && status.limit_tokens) || 1000000,
                    hint: (data && data.error) || 'Could not load context status.',
                });
            } catch (_) {
                /* keep last paint */
            }
            return status;
        }

        function scheduleRefresh(delayMs) {
            if (fetchTimer) frameWin.clearTimeout(fetchTimer);
            fetchTimer = frameWin.setTimeout(() => {
                fetchTimer = 0;
                fetchStatus();
            }, delayMs == null ? 80 : delayMs);
        }

        function ensurePopover() {
            if (popoverEl && popoverEl.isConnected) {
                return popoverEl;
            }
            const el = frameDoc.createElement('div');
            el.className = 'context-gauge-popover';
            el.hidden = true;
            el.setAttribute('role', 'dialog');
            el.setAttribute('aria-label', 'Agent context usage');
            frameDoc.body.appendChild(el);
            popoverEl = el;
            return el;
        }

        function closePopover() {
            const el = popoverEl;
            if (!el) return;
            el.hidden = true;
            el.innerHTML = '';
            GAUGE_BTN_IDS.forEach((id) => {
                const btn = frameDoc.getElementById(id);
                if (btn) btn.setAttribute('aria-expanded', 'false');
            });
        }

        function renderPopover(el, payload) {
            el.innerHTML = renderPopoverHtml(payload, {
                escapeHtml,
                busy,
                fallbackAgent: stickyAgentId(),
            });
        }

        function positionPopover(anchorBtn, pop) {
            const rect = anchorBtn.getBoundingClientRect();
            const pad = 8;
            pop.style.visibility = 'hidden';
            pop.hidden = false;
            const pw = pop.offsetWidth || 240;
            const ph = pop.offsetHeight || 120;
            let left = rect.right - pw;
            let top = rect.top - ph - 8;
            if (top < pad) top = rect.bottom + 8;
            if (left < pad) left = pad;
            if (left + pw > frameWin.innerWidth - pad) {
                left = Math.max(pad, frameWin.innerWidth - pw - pad);
            }
            pop.style.left = Math.round(left) + 'px';
            pop.style.top = Math.round(top) + 'px';
            pop.style.visibility = '';
        }

        async function openPopover(anchorBtn) {
            const agent = stickyAgentId();
            if (!agent || !anchorBtn) return;
            const pop = ensurePopover();
            let current = status;
            const needsLive = agent === 'codex' && (
                !current
                || current.agent_id !== agent
                || current.token_source === 'aggregated'
                || !(Number(current.used_tokens) > 0)
            );
            if (!current || current.agent_id !== agent || needsLive) {
                current = await fetchStatus({ live: needsLive });
            }
            renderPopover(pop, current || { agent_id: agent, percent: 0 });
            positionPopover(anchorBtn, pop);
            anchorBtn.setAttribute('aria-expanded', 'true');
            const compactBtn = pop.querySelector('[data-action="compact"]');
            if (compactBtn) {
                compactBtn.onclick = () => runCompact(anchorBtn);
            }
            const closeBtn = pop.querySelector('[data-action="close"]');
            if (closeBtn) closeBtn.onclick = () => closePopover();
        }

        async function runCompact(anchorBtn) {
            const agent = stickyAgentId();
            if (!agent || busy) return;
            const sessionId = getSessionId();
            if (!sessionId) {
                try { toast('Send a message first so the agent session exists.', 'info'); } catch (_) {}
                return;
            }
            busy = true;
            GAUGE_BTN_IDS.forEach((id) => {
                const b = frameDoc.getElementById(id);
                if (b) b.classList.add('context-gauge--busy');
            });
            const pop = ensurePopover();
            if (!pop.hidden) {
                renderPopover(pop, status || { agent_id: agent, percent: 0 });
                if (anchorBtn) positionPopover(anchorBtn, pop);
            }
            try {
                const body = {
                    agent,
                    session: sessionId,
                    path: getProjectPath(),
                };
                const res = await doFetch('/api/agent-context/compact', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => ({}));
                if (data && data.success) {
                    try {
                        toast(
                            (agent.charAt(0).toUpperCase() + agent.slice(1)) + ' context compacted',
                            'success'
                        );
                    } catch (_) {}
                    if (data.status && data.status.success) {
                        paintFromStatus(data.status);
                    } else {
                        scheduleRefresh(400);
                    }
                } else {
                    const err = (data && data.error) || 'Compact failed';
                    try { toast(err, 'error'); } catch (_) {}
                }
            } catch (e) {
                try { toast(String(e && e.message || e), 'error'); } catch (_) {}
            } finally {
                busy = false;
                GAUGE_BTN_IDS.forEach((id) => {
                    const b = frameDoc.getElementById(id);
                    if (b) b.classList.remove('context-gauge--busy');
                });
                if (anchorBtn && popoverEl && !popoverEl.hidden) {
                    openPopover(anchorBtn);
                } else {
                    closePopover();
                }
            }
        }

        function init() {
            GAUGE_BTN_IDS.forEach((id) => {
                const btn = frameDoc.getElementById(id);
                if (!btn || btn.dataset.contextGaugeBound) return;
                btn.dataset.contextGaugeBound = '1';
                btn.addEventListener('click', (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    if (btn.getAttribute('aria-expanded') === 'true'
                        && popoverEl
                        && !popoverEl.hidden) {
                        closePopover();
                        return;
                    }
                    openPopover(btn);
                });
            });
            frameDoc.addEventListener('click', (ev) => {
                const pop = popoverEl;
                if (!pop || pop.hidden) return;
                const t = ev.target;
                if (pop.contains(t)) return;
                if (t && t.closest && t.closest('.context-gauge-btn')) return;
                closePopover();
            });
            frameDoc.addEventListener('keydown', (ev) => {
                if (ev.key === 'Escape') closePopover();
            });
            scheduleRefresh(200);
        }

        return {
            init,
            noteUsage,
            scheduleRefresh,
            stickyAgentId,
        };
    }

    const api = {
        CONTEXT_GAUGE_AGENTS,
        GAUGE_BTN_IDS,
        formatContextTokenCount,
        normalizeAgentHint,
        stickyAgentIdFromSlashCtx,
        gaugeDomPlan,
        computeUsageStatus,
        renderPopoverHtml,
        create,
    };

    const ns = (root.CuttleChatContextGauge = root.CuttleChatContextGauge || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
