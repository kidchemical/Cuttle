/* Settings → Agents → Billing (api.agent_harness.agents.claude.api_fallback).
   `CuttleSettingsAgentBilling.mount(container)` reads /api/settings/agent-billing
   and builds one row per agent that can bill to a subscription or to API
   credit (Claude Code today): which goes first, automatic API fallback when
   the subscription runs out, and the monthly API budget. A chat can override
   the order from the composer agent popup; one message with /use-api or
   /use-subscription. */
(function (root) {
    'use strict';

    const AGENT_LABELS = { claude: 'Claude Code' };
    const ORDERS = [
        ['subscription', 'Use subscription budget first'],
        ['api', 'Use API budget first'],
    ];

    function el(tag, text, cls) {
        const n = document.createElement(tag);
        if (text) n.textContent = text;
        if (cls) n.className = cls;
        return n;
    }

    function info(label, text) {
        const b = el('button', 'i', 'cuttle-info');
        b.type = 'button';
        b.setAttribute('data-tooltip', text);
        b.setAttribute('aria-label', 'About ' + label);
        return b;
    }

    /** Patch for one agent from plain form values. Pure. */
    function patchFrom(values) {
        const budget = String(values.budget == null ? '' : values.budget).trim();
        return {
            prefer: values.prefer === 'api' ? 'api' : 'subscription',
            fallback_enabled: !!values.fallback,
            monthly_budget_usd: budget === '' ? null : Number(budget),
        };
    }

    /** One status line for an agent's API side. Pure. */
    function statusLine(view) {
        const v = view || {};
        if (v.killed) return 'API billing is switched off on this server (CUTTLE_CLAUDE_API_FALLBACK=0).';
        if (!v.has_key) return 'No Anthropic API key yet — add one in Settings → Providers. Until then every turn uses the subscription.';
        const spent = '$' + Number(v.month_usd || 0).toFixed(2);
        return v.monthly_budget_usd
            ? spent + ' of $' + Number(v.monthly_budget_usd).toFixed(0) + ' API budget spent this month.'
            : spent + ' API credit spent this month (no budget cap).';
    }

    function agentRow(agent, view, onSaved) {
        const label = AGENT_LABELS[agent] || agent;
        const wrap = el('form', '', 'agent-billing-settings');
        const row = el('div', '', 'setting-item');
        const lab = el('div', '', 'setting-label');
        lab.append(el('strong', label), info(label + ' billing',
            'Which budget a ' + label + ' turn spends first. The other one takes over when the first cannot run the turn (no key, budget spent, or subscription limit reached with automatic fallback on). Override per chat from the agent chip in the composer, or for one message with /use-api or /use-subscription.'));
        const order = el('select', '', 'setting-input');
        order.setAttribute('aria-label', label + ' billing order');
        ORDERS.forEach(([v, text]) => {
            const o = el('option', text);
            o.value = v;
            o.selected = view.prefer === v;
            order.append(o);
        });
        const control = el('div', '', 'setting-control');
        control.append(order);
        row.append(lab, control);

        const fbRow = el('div', '', 'setting-item');
        const fbLab = el('div', '', 'setting-label');
        fbLab.append(el('strong', 'Automatic API fallback'), info('Automatic API fallback',
            'With subscription first: when the subscription hits a usage limit, rerun the turn on API credit and keep using it until the limit resets.'));
        const fbControl = el('div', '', 'setting-control');
        const fb = el('input');
        fb.type = 'checkbox';
        fb.checked = !!view.fallback_enabled;
        fb.setAttribute('aria-label', label + ' automatic API fallback');
        fbControl.append(fb);
        fbRow.append(fbLab, fbControl);

        const budgetRow = el('div', '', 'setting-item');
        const budgetLab = el('div', '', 'setting-label');
        budgetLab.append(el('strong', 'Monthly API budget'), info('Monthly API budget',
            'API-billed turns stop once this much is spent in a calendar month, and turns fall back to the subscription. Empty = no cap.'));
        const budgetControl = el('div', '', 'setting-control');
        const budget = el('input', '', 'setting-input');
        budget.type = 'number';
        budget.min = '0';
        budget.step = '1';
        budget.style.width = '7em';
        budget.value = view.monthly_budget_usd == null ? '' : String(view.monthly_budget_usd);
        budget.setAttribute('aria-label', label + ' monthly API budget in dollars');
        budgetControl.append(el('span', '$ ', 'setting-description'), budget);
        budgetRow.append(budgetLab, budgetControl);

        const note = el('div', statusLine(view), 'setting-description');
        const actions = el('div', '', 'provider-picker');
        const save = el('button', 'Save billing', 'control-button btn-start');
        save.type = 'submit';
        actions.append(save);
        const status = el('div', '', 'setting-description');
        status.setAttribute('role', 'status');
        wrap.append(row, fbRow, budgetRow, note, actions, status);
        wrap.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            save.disabled = true;
            status.textContent = 'Saving…';
            try {
                const body = {};
                body[agent] = patchFrom({ prefer: order.value, fallback: fb.checked, budget: budget.value });
                const r = await fetch('/api/settings/agent-billing', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const d = await r.json();
                if (!d.success) throw new Error(d.error || 'Save failed');
                status.textContent = 'Saved.';
                note.textContent = statusLine((d.agents || {})[agent]);
                if (onSaved) onSaved(d);
            } catch (e) {
                status.textContent = 'Could not save: ' + (e && e.message ? e.message : e);
            } finally {
                save.disabled = false;
            }
        });
        return wrap;
    }

    async function mount(target) {
        if (!target || target.dataset.mounted) return;
        const section = document.getElementById('agentBillingSettings');
        try {
            const r = await fetch('/api/settings/agent-billing', { cache: 'no-store' });
            const d = await r.json();
            if (!d || d.success !== true || !d.agents) return;
            target.dataset.mounted = 'true';
            target.replaceChildren(...Object.keys(d.agents).map((a) => agentRow(a, d.agents[a] || {})));
            if (section) section.hidden = false;
        } catch (_) { /* section stays hidden */ }
    }

    const api = { mount, patchFrom, statusLine };
    root.CuttleSettingsAgentBilling = api;
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
