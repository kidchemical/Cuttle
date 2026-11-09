/* ================================================================
   Cuttle Chat — per-chat auto-compact controls (chat_auto_compact.js).
   Owner: the "Auto-compact" section of the composer context-gauge
   popover. Reads/writes `/api/auto-compact/chats/<id>` (api.auto_compact);
   the server decides when to compact, this only shows the chat's policy
   and edits its override (enabled / percent / milestone; blank inherits).

   Interface: pure `sectionModel(view)` / `renderSectionHtml(view, esc)`
   plus `create(deps)` → `{ mount(slot, sessionId) }` (resolves true when
   the section is shown). deps: { fetch, escapeHtml, toast? }.
   Hidden while the `auto_compact` flag is off (the API says
   `available: false`) or the read fails. Loaded before chat_context_gauge.js.
   ================================================================ */
(function (root) {
    'use strict';

    function onOff(v) { return v ? 'on' : 'off'; }

    function triValue(override, key) {
        const v = override ? override[key] : undefined;
        if (v === true) return 'on';
        if (v === false) return 'off';
        return 'inherit';
    }

    function tokens(n) {
        const v = Number(n) || 0;
        if (v >= 1000000) return (Math.round(v / 100000) / 10) + 'M';
        if (v >= 1000) return Math.round(v / 1000) + 'k';
        return String(v);
    }

    const MANAGED = {
        supervised: 'supervised chat',
        supervisor: 'supervisor chat',
        'sub-agent': 'sub-agent chat',
        orchestrator: 'orchestrated chat',
    };

    /** Plain view model for the section. Pure. */
    function sectionModel(view) {
        if (!view || view.available !== true || !view.policy) return { visible: false };
        const p = view.policy;
        const base = view.inherited || p;
        const override = view.override || {};
        const last = (view.history || []).find((h) => h && (h.outcome === 'compacted' || h.outcome === 'failed'));
        let lastText = '';
        if (last) {
            const d = last.detail || {};
            const pct = d.before != null && d.after != null ? (d.before + '% → ' + d.after + '%')
                : (d.before != null ? 'at ' + d.before + '%' : '');
            lastText = (last.outcome === 'compacted' ? 'Last auto-compact ' : 'Last attempt failed ')
                + pct + (d.trigger === 'milestone' ? ' (milestone)' : '');
        }
        const scope = view.managed ? (MANAGED[view.managed_reason] || 'managed chat') : 'chat';
        const rule = p.enabled
            ? ('Compacts between turns at ' + p.percent + '%'
                + (p.max_tokens ? ' or ' + tokens(p.max_tokens) + ' tokens' : '')
                + (p.milestone ? '; ' + p.milestone_percent + '% at a Tasks milestone' : '') + '.')
            : 'Off for this ' + scope + '.';
        return {
            visible: true,
            enabled: triValue(override, 'enabled'),
            milestone: triValue(override, 'milestone'),
            percent: override.percent != null ? String(override.percent) : '',
            percentPlaceholder: String(base.percent),
            inheritEnabled: 'Default (' + onOff(base.enabled) + ')',
            inheritMilestone: 'Default (' + onOff(base.milestone) + ')',
            rule,
            scopeText: view.managed ? 'Uses the ' + scope + ' defaults.' : '',
            lastText,
            failing: !!(view.state && view.state.last_decision === 'failing'),
        };
    }

    function options(selected, inheritLabel) {
        return [['inherit', inheritLabel], ['on', 'On'], ['off', 'Off']]
            .map(([v, label]) => '<option value="' + v + '"' + (v === selected ? ' selected' : '') + '>'
                + label + '</option>').join('');
    }

    /** Section markup (empty string when hidden). Pure. */
    function renderSectionHtml(view, escapeHtml) {
        const esc = typeof escapeHtml === 'function' ? escapeHtml : String;
        const m = sectionModel(view);
        if (!m.visible) return '';
        return (
            '<div class="context-gauge-auto-compact" data-auto-compact="1">' +
            '<div class="context-gauge-auto-row">' +
            '<label for="autoCompactEnabled">Auto-compact</label>' +
            '<select id="autoCompactEnabled" data-field="enabled">' + options(m.enabled, esc(m.inheritEnabled)) + '</select>' +
            '</div>' +
            '<div class="context-gauge-auto-row">' +
            '<label for="autoCompactPercent">At</label>' +
            '<span class="context-gauge-auto-pct"><input id="autoCompactPercent" data-field="percent" type="number" ' +
            'min="20" max="95" step="5" inputmode="numeric" value="' + esc(m.percent) + '" placeholder="' +
            esc(m.percentPlaceholder) + '" aria-label="Auto-compact threshold percent (blank uses the default)">%</span>' +
            '</div>' +
            '<div class="context-gauge-auto-row">' +
            '<label for="autoCompactMilestone">Milestones</label>' +
            '<select id="autoCompactMilestone" data-field="milestone">' + options(m.milestone, esc(m.inheritMilestone)) + '</select>' +
            '</div>' +
            '<p class="context-gauge-auto-note">' + esc(m.rule) + (m.scopeText ? ' ' + esc(m.scopeText) : '') + '</p>' +
            (m.failing ? '<p class="context-gauge-auto-note context-gauge-auto-note--warn">Stopped after repeated failures; change a setting to retry.</p>' : '') +
            (m.lastText ? '<p class="context-gauge-auto-note">' + esc(m.lastText) + '</p>' : '') +
            '</div>'
        );
    }

    /** Override body from the section's inputs. Pure over plain values. */
    function overrideFrom(values) {
        const tri = (v) => (v === 'on' ? true : v === 'off' ? false : null);
        const raw = String(values.percent == null ? '' : values.percent).trim();
        const out = { enabled: tri(values.enabled), milestone: tri(values.milestone) };
        out.percent = raw === '' ? null : Math.round(Number(raw));
        return out;
    }

    function create(deps) {
        const d = deps || {};
        const doFetch = d.fetch || ((u, o) => root.fetch(u, o));
        const escapeHtml = d.escapeHtml || String;
        const toast = typeof d.toast === 'function' ? d.toast : function () {};

        async function read(sessionId) {
            const r = await doFetch('/api/auto-compact/chats/' + encodeURIComponent(sessionId), { cache: 'no-store' });
            const data = await r.json();
            return data && data.success ? data : null;
        }

        function paint(slot, view, sessionId) {
            slot.innerHTML = renderSectionHtml(view, escapeHtml);
            slot.hidden = !slot.innerHTML;
            if (slot.hidden) return false;
            const save = async () => {
                const val = (f) => { const el = slot.querySelector('[data-field="' + f + '"]'); return el ? el.value : ''; };
                const body = { override: overrideFrom({ enabled: val('enabled'), milestone: val('milestone'), percent: val('percent') }) };
                if (body.override.percent != null && !(body.override.percent >= 20 && body.override.percent <= 95)) {
                    toast('Auto-compact threshold must be 20–95%.', 'error');
                    return;
                }
                try {
                    const r = await doFetch('/api/auto-compact/chats/' + encodeURIComponent(sessionId), {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                    });
                    const data = await r.json();
                    if (!data || !data.success) throw new Error((data && data.error) || 'Save failed');
                    paint(slot, data, sessionId);
                } catch (e) {
                    toast('Auto-compact: ' + (e && e.message ? e.message : e), 'error');
                }
            };
            slot.querySelectorAll('select[data-field]').forEach((el) => { el.onchange = save; });
            const pct = slot.querySelector('input[data-field="percent"]');
            if (pct) {
                pct.onchange = save;
                pct.onkeydown = (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); pct.blur(); } };
            }
            return true;
        }

        async function mount(slot, sessionId) {
            if (!slot) return false;
            slot.hidden = true;
            slot.innerHTML = '';
            if (!sessionId || !/^(\d+|db_session_\d+|CH-\d+)$/.test(String(sessionId))) return false;
            try {
                const view = await read(sessionId);
                return paint(slot, view, sessionId);
            } catch (_) {
                return false;
            }
        }

        return { mount };
    }

    const api = { sectionModel, renderSectionHtml, overrideFrom, create };
    root.CuttleChatAutoCompact = api;
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
