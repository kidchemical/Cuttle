/* ================================================================
   Cuttle Dashboards — Orchestration & Supervision (orchestration_dashboard.js).
   Owner: rendering the `orchestration` dashboard payload
   (GET /api/dashboards/orchestration, built by api.orchestration_telemetry.report).
   Tables first; one dependency-free SVG chart (sub-agent spawns per day,
   stacked by tier role). dashboards_page.js only dispatches here.
   host: { escapeHtml, fmtCount, backButton, onBack(), setParams(obj) }.
   ================================================================ */
(function (root) {
    'use strict';

    // Validated categorical slots 1–3 (reference palette; light + dark steps,
    // checked against Cuttle's surfaces); "untiered" is the neutral Other bucket.
    const ROLES = [
        { id: 'worker', label: 'Worker' },
        { id: 'escalation', label: 'Escalation' },
        { id: 'expert', label: 'Expert' },
        { id: 'untiered', label: 'Not in a playbook' },
    ];

    function fmtDuration(s) {
        if (s == null || !Number.isFinite(Number(s))) return '—';
        const v = Math.round(Number(s));
        if (v < 60) return v + 's';
        if (v < 3600) return Math.floor(v / 60) + 'm ' + String(v % 60).padStart(2, '0') + 's';
        return Math.floor(v / 3600) + 'h ' + String(Math.floor((v % 3600) / 60)).padStart(2, '0') + 'm';
    }

    function fmtPct(p) {
        return p == null ? '—' : (Math.round(p * 10) / 10) + '%';
    }

    function create(host) {
        const esc = host.escapeHtml;
        const n = host.fmtCount;

        function pctBar(p, title) {
            if (p == null) return '<span class="orch-dash-muted">—</span>';
            const w = Math.max(0, Math.min(100, Number(p)));
            return '<span class="orch-dash-bar" title="' + esc(title || '') + '"><span class="orch-dash-bar-track">'
                + '<span class="orch-dash-bar-fill" style="width:' + w + '%"></span></span>'
                + '<span class="orch-dash-bar-num">' + esc(fmtPct(p)) + '</span></span>';
        }

        function table(id, head, rows, empty) {
            if (!rows.length) return '<p class="dash-empty">' + esc(empty) + '</p>';
            return '<div class="jobs-table-scroll"><table class="hist-table" id="' + id + '"><thead><tr>'
                + head.map((h) => '<th' + (h.num ? ' class="is-num"' : '') + ' title="' + esc(h.title || '') + '">'
                    + esc(h.label) + '</th>').join('') + '</tr></thead><tbody>'
                + rows.map((r) => '<tr>' + r.map((c, i) => '<td' + (head[i].num ? ' class="is-num"' : '') + '>' + c + '</td>').join('')
                    + '</tr>').join('') + '</tbody></table></div>';
        }

        // Stacked bars: one per day, segments in fixed role order (color follows the role).
        function chartSvg(daily) {
            const W = 760; const H = 220; const padL = 34; const padB = 22; const padT = 8;
            const days = daily.length;
            const totals = daily.map((d) => ROLES.reduce((s, r) => s + (d[r.id] || 0), 0));
            const max = Math.max(1, ...totals);
            const step = max <= 5 ? 1 : Math.ceil(max / 4);
            const top = Math.ceil(max / step) * step;
            const plotW = W - padL - 6; const plotH = H - padT - padB;
            const slot = plotW / Math.max(days, 1);
            const barW = Math.max(3, Math.min(22, slot * 0.62));
            const y = (v) => padT + plotH - (v / top) * plotH;
            let grid = '';
            for (let v = 0; v <= top; v += step) {
                grid += '<line class="orch-dash-grid" x1="' + padL + '" x2="' + (W - 6) + '" y1="' + y(v) + '" y2="' + y(v) + '"/>'
                    + '<text class="orch-dash-axis" x="' + (padL - 6) + '" y="' + (y(v) + 3) + '" text-anchor="end">' + v + '</text>';
            }
            let bars = '';
            const labelEvery = Math.max(1, Math.ceil(days / 8));
            daily.forEach((d, i) => {
                const x = padL + i * slot + (slot - barW) / 2;
                let base = 0;
                const present = ROLES.filter((r) => d[r.id]);
                present.forEach((r, k) => {
                    const v = d[r.id];
                    const y0 = y(base); const y1 = y(base + v);
                    const gap = k > 0 ? 2 : 0; // 2px surface gap between stacked segments
                    const h = Math.max(1, y0 - y1 - gap);
                    const isTop = k === present.length - 1;
                    const rx = isTop ? Math.min(4, barW / 2, h / 2) : 0;
                    bars += isTop && rx
                        ? '<path class="orch-dash-seg role-' + r.id + '" d="M' + x + ',' + (y1 + h) + 'V' + (y1 + rx)
                          + 'Q' + x + ',' + y1 + ' ' + (x + rx) + ',' + y1 + 'H' + (x + barW - rx) + 'Q' + (x + barW) + ',' + y1
                          + ' ' + (x + barW) + ',' + (y1 + rx) + 'V' + (y1 + h) + 'Z"/>'
                        : '<rect class="orch-dash-seg role-' + r.id + '" x="' + x + '" y="' + y1 + '" width="' + barW + '" height="' + h + '"/>';
                    base += v;
                });
                // Hit target: the whole day column, larger than the mark.
                bars += '<rect class="orch-dash-hit" data-day="' + i + '" x="' + (padL + i * slot) + '" y="' + padT
                    + '" width="' + slot + '" height="' + plotH + '"/>';
                if (i % labelEvery === 0) {
                    bars += '<text class="orch-dash-axis" x="' + (x + barW / 2) + '" y="' + (H - 6) + '" text-anchor="middle">'
                        + esc(String(d.day).slice(5)) + '</text>';
                }
            });
            return '<svg class="orch-dash-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Sub-agent spawns per day by tier">'
                + grid + '<line class="orch-dash-baseline" x1="' + padL + '" x2="' + (W - 6) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>'
                + bars + '</svg>';
        }

        function render(el, payload) {
            const p = payload || {};
            const s = p.stats || {};
            const ranges = (p.available_ranges || []).map((r) => '<button type="button" class="dash-chip'
                + (r.id === p.selected_range ? ' is-on' : '') + '" data-orch-range="' + esc(r.id) + '">' + esc(r.label) + '</button>').join('');
            const projects = '<select class="orch-dash-project" id="orchDashProject" aria-label="Project">'
                + '<option value="">All projects</option>' + (p.projects || []).map((pp) => '<option value="' + esc(pp) + '"'
                    + (pp === p.project ? ' selected' : '') + '>' + esc(pp.split('/').filter(Boolean).pop() || pp) + '</option>').join('')
                + '</select>';
            const tiles = [
                [n(s.orchestrated_chats), 'Orchestrated chats'],
                [n(s.spawns), 'Sub-agents spawned'],
                [n(s.escalations), 'Escalations'],
                [fmtPct(s.escalation_pct), 'Escalations per worker spawn'],
                [fmtDuration(s.median_worker_s), 'Median worker round'],
                [n(s.failed_runs), 'Failed rounds'],
                [n(s.checks), 'Supervision checks'],
                [n(s.wakes), 'Supervisor wakes'],
                [n((s.sends || 0) + (s.stops || 0)), 'Supervisor interventions'],
            ].map(([v, l]) => '<div class="dash-stat-card"><div class="dash-stat-value">' + esc(v) + '</div><div class="dash-stat-label">'
                + esc(l) + '</div></div>').join('');
            const daily = p.daily || [];
            const legend = '<div class="orch-dash-legend">' + ROLES.map((r) => '<span><i class="orch-dash-swatch role-' + r.id
                + '"></i>' + esc(r.label) + '</span>').join('') + '</div>';
            const dailyTable = table('orchDashDaily', [{ label: 'Day' }].concat(ROLES.map((r) => ({ label: r.label, num: true }))),
                daily.map((d) => [esc(d.day)].concat(ROLES.map((r) => esc(String(d[r.id] || 0))))), 'No spawns in this range.');

            const tierRows = (p.by_tier || []).map((r) => [
                esc(r.role), esc(r.label), esc(n(r.spawns)), esc(n(r.runs)), pctBar(r.fail_pct, r.failed + ' failed of ' + r.runs),
                esc(fmtDuration(r.median_s)), esc(fmtDuration(r.p90_s)),
                r.role === 'worker' ? pctBar(r.escalation_pct, r.escalated_after + ' escalations after this model') : '<span class="orch-dash-muted">—</span>',
            ]);
            const orchRows = (p.orchestrators || []).map((o) => [
                esc(o.label), esc(n(o.chats)), esc(String(o.spawns_per_chat ?? '—')), esc(String(o.escalations_per_chat ?? '—')),
                esc(n(o.experts)), pctBar(o.child_fail_pct, 'failed sub-agent rounds'), esc(fmtDuration(o.median_child_s)),
                esc(n(o.supervisor_interventions)),
            ]);
            const escRows = (p.escalations || []).map((e) => [
                esc(e.project ? e.project.split('/').filter(Boolean).pop() : '—'), esc(e.target), esc(n(e.count))]);
            const links = p.escalation_links || {};
            const supRows = (p.supervision || []).map((r) => [
                esc(r.label), esc(n(r.schedules)), esc(n(r.checks)), pctBar(r.wake_pct, r.wakes + ' wakes'), esc(n(r.violations)),
                esc(n(r.stalls)), esc(n(r.sends)), esc(n(r.stops))]);
            const wakeStates = Object.entries(p.wake_states || {}).sort((a, b) => b[1] - a[1])
                .map(([k, v]) => esc(k.replace(/_/g, ' ')) + ' ' + esc(n(v))).join(' · ');
            const failRows = (p.failures || []).map((f) => [
                esc(new Date(f.at * 1000).toLocaleString()), esc(f.child), esc(f.parent), esc(f.role), esc(f.label),
                '<span class="orch-dash-error" title="' + esc(f.error) + '">' + esc(f.error || '(no error text)') + '</span>']);

            el.innerHTML = '<div class="dash-page-bar">' + host.backButton
                + '<div class="compact-page-title"><h1>Orchestration &amp; Supervision</h1>'
                + '<p>Which models orchestrate, grind, escalate and supervise — and how it goes.</p></div></div>'
                + '<div class="dash-usage-range orch-dash-filters" id="orchDashRange">' + ranges + projects + '</div>'
                + '<div class="dash-stats dash-stats--usage">' + tiles + '</div>'
                + (p.backfilled ? '<p class="dash-chart-hint">History before the event log was imported once: one round per sub-agent'
                    + ' (later rounds of a reused child are not split out), tiers from each chat’s playbook at import time,'
                    + ' and only the supervision checks still kept (last 50 per schedule).</p>' : '')
                + (s.untiered_spawns ? '<p class="dash-chart-hint">' + esc(n(s.untiered_spawns)) + ' spawns came from chats not bound to'
                    + ' a playbook (or a profile no tier lists); they show as “Not in a playbook”.</p>' : '')
                + '<div class="dash-usage-card dash-usage-card--wide"><div class="dash-usage-card-head"><h3>Sub-agents spawned per day</h3>'
                + '<span>stacked by tier</span></div>' + legend
                + (daily.length ? '<div class="orch-dash-chart" id="orchDashChart">' + chartSvg(daily)
                    + '<div class="orch-dash-tip" id="orchDashTip" hidden></div></div>' : '<p class="dash-empty">No spawns in this range.</p>')
                + '<details class="orch-dash-details"><summary>Data table</summary>' + dailyTable + '</details></div>'
                + '<h2 class="dash-section-title">Orchestrators by model</h2>'
                + '<p class="dash-chart-hint">The model of the orchestrator turn that spawned each sub-agent.</p>'
                + table('orchDashOrch', [{ label: 'Orchestrator' }, { label: 'Chats', num: true }, { label: 'Spawns / chat', num: true },
                    { label: 'Escalations / chat', num: true }, { label: 'Expert calls', num: true }, { label: 'Sub-agent failures' },
                    { label: 'Median sub-agent round', num: true }, { label: 'Supervisor interventions', num: true,
                        title: 'Messages and stops the supervisor sent these chats' }], orchRows, 'No orchestrated chats in this range.')
                + '<h2 class="dash-section-title">Sub-agents by tier and model</h2>'
                + '<p class="dash-chart-hint">“Escalated after” counts escalation spawns whose most likely source (the chat’s latest'
                + ' failed worker round, else its latest worker) ran on this model.</p>'
                + table('orchDashTier', [{ label: 'Tier' }, { label: 'Agent · model' }, { label: 'Spawns', num: true },
                    { label: 'Rounds', num: true }, { label: 'Failed' }, { label: 'Median round', num: true },
                    { label: 'p90 round', num: true }, { label: 'Escalated after' }], tierRows, 'No sub-agents in this range.')
                + '<h2 class="dash-section-title">Escalation targets by project</h2>'
                + (Object.keys(links).length ? '<p class="dash-chart-hint">Source link: ' + esc(Object.entries(links)
                    .map(([k, v]) => v + ' ' + k.replace(/_/g, ' ')).join(' · ')) + '.</p>' : '')
                + table('orchDashEsc', [{ label: 'Project' }, { label: 'Escalated to' }, { label: 'Escalations', num: true }],
                    escRows, 'No escalations in this range.')
                + '<h2 class="dash-section-title">Supervisors by model</h2>'
                + (wakeStates ? '<p class="dash-chart-hint">What woke supervisors: ' + wakeStates + '.</p>' : '')
                + table('orchDashSup', [{ label: 'Supervisor' }, { label: 'Schedules', num: true }, { label: 'Checks', num: true },
                    { label: 'Woke' }, { label: 'Rule breaks', num: true }, { label: 'Stalls', num: true },
                    { label: 'Messages sent', num: true }, { label: 'Stops', num: true }], supRows, 'No supervision in this range.')
                + '<h2 class="dash-section-title">Recent sub-agent failures</h2>'
                + table('orchDashFail', [{ label: 'When' }, { label: 'Child' }, { label: 'Parent' }, { label: 'Tier' },
                    { label: 'Agent · model' }, { label: 'Error' }], failRows, 'No failed sub-agent rounds in this range.');

            el.querySelector('#dashBack').addEventListener('click', () => host.onBack());
            el.querySelector('#orchDashRange').addEventListener('click', (e) => {
                const b = e.target.closest('[data-orch-range]');
                if (b) host.setParams({ range: b.getAttribute('data-orch-range') });
            });
            el.querySelector('#orchDashProject').addEventListener('change', (e) => host.setParams({ project: e.target.value }));
            const chart = el.querySelector('#orchDashChart');
            const tip = el.querySelector('#orchDashTip');
            if (chart && tip) {
                chart.addEventListener('mousemove', (e) => {
                    const hit = e.target.closest('.orch-dash-hit');
                    if (!hit) { tip.hidden = true; return; }
                    const d = daily[Number(hit.getAttribute('data-day'))];
                    const total = ROLES.reduce((sum, r) => sum + (d[r.id] || 0), 0);
                    tip.innerHTML = '<strong>' + esc(d.day) + '</strong> · ' + total + ' spawned'
                        + ROLES.filter((r) => d[r.id]).map((r) => '<div><i class="orch-dash-swatch role-' + r.id + '"></i>'
                            + esc(r.label) + ' <b>' + d[r.id] + '</b></div>').join('');
                    const box = chart.getBoundingClientRect();
                    tip.hidden = false;
                    const x = e.clientX - box.left + 12;
                    tip.style.left = Math.min(x, box.width - tip.offsetWidth - 4) + 'px';
                    tip.style.top = Math.max(0, e.clientY - box.top - tip.offsetHeight - 8) + 'px';
                });
                chart.addEventListener('mouseleave', () => { tip.hidden = true; });
            }
        }

        return { render };
    }

    const api = { create, fmtDuration, ROLES };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleOrchestrationDashboard = api;
})(typeof window !== 'undefined' ? window : globalThis);
