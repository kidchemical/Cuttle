/* Curated local-AI landscape dashboard.
 *
 * This owner consumes the versioned api.dashboards.local_ai contract.  It owns
 * its client-only filters, sort and Plotly view; dashboards_page.js only
 * dispatches the three local-AI ids here.
 */
(function () {
    'use strict';

    const IDS = new Set(['local-models', 'local-ai-hardware', 'local-ai-builds']);
    const LABELS = {
        overall_rank: 'Overall rank', overall_score: 'Overall score', name: 'Entry', publisher: 'Publisher', vendor: 'Vendor',
        family: 'Family', license: 'License', params_b: 'Parameters (B)', active_params_b: 'Active parameters (B)',
        context_k: 'Context (K)', recommended_vram_gb: 'Recommended VRAM (GB)', weight_size_q4_gb: 'Q4 weights (GB)',
        agent_score: 'Agent evidence (%)', coding_score: 'Coding evidence (%)', release_year: 'Release year', category: 'Category',
        price_usd: 'Reference cost (USD)', memory_gb: 'Memory (GB)', single_device_memory_gb: 'Largest device memory (GB)',
        bandwidth_gbps: 'Memory bandwidth (GB/s)', tdp_w: 'TDP (W)', typical_draw_w: 'Typical draw (W)', measured_tps: 'Measured tokens/s',
        introduced_year: 'Introduced', cost_score: 'Cost score', efficiency_score: 'Efficiency score', capacity_score: 'Capacity score',
        speed_score: 'Speed score', compatibility_score: 'Compatibility score', approach: 'Approach', software: 'Software stack',
        link_type: 'Device linking', availability: 'Availability',
    };
    const NUMBER_FIELDS = new Set(Object.keys(LABELS).filter((key) => !['name', 'publisher', 'vendor', 'family', 'license', 'category', 'approach', 'software', 'link_type', 'availability'].includes(key)));
    let lastRenderer = null;

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    }

    function attr(value) { return esc(value); }
    function label(field) { return LABELS[field] || field.replace(/_/g, ' '); }

    function money(value) {
        return value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(Number(value));
    }

    function value(field, raw) {
        if (raw == null || raw === '') return '—';
        const n = Number(raw);
        if (field === 'overall_rank') return '#' + n;
        if (field === 'price_usd') return money(n);
        if (field === 'memory_gb' || field === 'single_device_memory_gb' || field === 'recommended_vram_gb' || field === 'weight_size_q4_gb') return n + ' GB';
        if (field === 'bandwidth_gbps') return n.toLocaleString() + ' GB/s';
        if (field === 'tdp_w' || field === 'typical_draw_w') return n + ' W';
        if (field === 'measured_tps') return n + ' tok/s';
        if (field === 'overall_score' || field.endsWith('_score') || field === 'agent_score' || field === 'coding_score') return n.toFixed(1);
        return NUMBER_FIELDS.has(field) ? n.toLocaleString() : String(raw);
    }

    function sourceLinks(row) {
        const sources = Array.isArray(row.sources) ? row.sources : [];
        const listing = row.listing ? [row.listing] : [];
        return sources.concat(listing).map((source) => source && source.url
            ? '<a href="' + attr(source.url) + '" target="_blank" rel="noopener">' + esc(source.label || 'source') + '</a>' : '').filter(Boolean).join(' · ');
    }

    function defaultAxes(id) {
        if (id === 'local-models') return { x: 'recommended_vram_gb', y: 'overall_score', z: 'context_k' };
        if (id === 'local-ai-hardware') return { x: 'price_usd', y: 'overall_score', z: 'memory_gb' };
        return { x: 'price_usd', y: 'overall_score', z: 'memory_gb' };
    }

    function create(host) {
        const state = {
            root: null, payload: null, filters: {}, sort: 'overall_rank', direction: 1,
            axes: null, chartReady: false,
        };

        function rows() {
            const raw = (state.payload && state.payload.rows) || [];
            const filtered = raw.filter((row) => Object.entries(state.filters).every(([field, choices]) => !choices.size || choices.has(String(row[field]))));
            return filtered.slice().sort((a, b) => {
                const av = a[state.sort]; const bv = b[state.sort];
                if (av == null && bv == null) return a.name.localeCompare(b.name);
                if (av == null) return 1; if (bv == null) return -1;
                if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * state.direction;
                return String(av).localeCompare(String(bv)) * state.direction;
            });
        }

        function filtersHtml() {
            const definitions = state.payload.filters || {};
            return '<div class="local-ai-filters">' + Object.entries(definitions).map(([field, options]) => {
                const selected = state.filters[field] || new Set();
                return '<label><span>' + esc(label(field)) + '</span><select data-local-filter="' + attr(field) + '" multiple size="1" aria-label="Filter ' + attr(label(field)) + '">'
                    + options.map((option) => '<option value="' + attr(option) + '"' + (selected.has(String(option)) ? ' selected' : '') + '>' + esc(value(field, option)) + '</option>').join('')
                    + '</select></label>';
            }).join('') + '<button type="button" class="dash-chip" data-local-clear>Clear filters</button></div>';
        }

        function axesHtml() {
            const axes = state.payload.axes || [];
            return '<div class="local-ai-axes" aria-label="3D chart dimensions">' + ['x', 'y', 'z'].map((axis) =>
                '<label><span>' + axis.toUpperCase() + '</span><select data-local-axis="' + axis + '" aria-label="' + axis.toUpperCase() + ' 3D dimension">'
                + axes.map((field) => '<option value="' + attr(field) + '"' + (state.axes[axis] === field ? ' selected' : '') + '>' + esc(label(field)) + '</option>').join('')
                + '</select></label>').join('') + '</div>';
        }

        function tableHtml(viewRows) {
            const columns = state.payload.columns || [];
            const arrow = (field) => state.sort === field ? (state.direction === 1 ? ' ↑' : ' ↓') : '';
            const head = columns.map((field) => '<th class="' + (NUMBER_FIELDS.has(field) ? 'is-num' : '') + '"><button type="button" data-local-sort="' + attr(field) + '">'
                + esc(label(field)) + arrow(field) + '</button></th>').join('');
            const body = viewRows.map((row) => '<tr data-local-row="' + attr(row.id) + '">' + columns.map((field) => {
                if (field === 'name') return '<td><strong>' + esc(row.name) + '</strong><small>' + sourceLinks(row) + '</small></td>';
                return '<td class="' + (NUMBER_FIELDS.has(field) ? 'is-num' : '') + '">' + esc(value(field, row[field])) + '</td>';
            }).join('') + '</tr>').join('');
            return '<div class="jobs-table-scroll local-ai-table"><table class="hist-table"><thead><tr>' + head + '</tr></thead><tbody>'
                + (body || '<tr><td colspan="' + columns.length + '">No entries match the current filters.</td></tr>') + '</tbody></table></div>';
        }

        function buildsHtml(viewRows) {
            if (state.payload.id !== 'local-ai-builds') return '';
            return '<section class="local-ai-build-details"><h2>Advertised build components</h2><p>Part links point to the verified system listing when the seller does not publish a discrete part SKU. Confirm exact brands and substitutions before checkout.</p>'
                + viewRows.map((row) => '<details><summary><strong>' + esc(row.name) + '</strong><span>' + esc(row.availability) + '</span></summary>'
                    + '<p>' + esc(row.model_fit) + '</p><p><b>Multi-device:</b> ' + esc(row.unification) + '</p><ul>'
                    + (row.parts || []).map((part) => '<li><b>' + esc(part.role) + ':</b> <a href="' + attr(part.url) + '" target="_blank" rel="noopener">' + esc(part.name) + '</a>'
                        + (part.note ? ' <small>— ' + esc(part.note) + '</small>' : '') + '</li>').join('') + '</ul></details>').join('') + '</section>';
        }

        function renderData() {
            if (!state.root || !state.payload) return;
            const viewRows = rows();
            const stats = state.payload.stats || {};
            const top = viewRows.slice().sort((a, b) => a.overall_rank - b.overall_rank)[0];
            state.root.innerHTML = '<div class="dash-page-bar">' + host.backButton + '<div class="compact-page-title"><h1>' + esc(state.payload.title) + '</h1><p>'
                + esc(state.payload.description) + '</p></div></div>'
                + '<div class="local-ai-meta"><span>Research snapshot: <strong>' + esc(new Date(state.payload.researched_at).toLocaleDateString()) + '</strong></span>'
                + '<span>Catalog v' + esc(state.payload.catalog_version) + ' · schema ' + esc(state.payload.catalog_schema_version) + '</span>'
                + '<span>TPS evidence: <strong>' + esc(stats.with_measured_tps || 0) + '</strong> matched runs</span></div>'
                + '<div class="dash-stats local-ai-stats">'
                + '<div class="dash-stat-card"><div class="dash-stat-value">' + esc(stats.entries || 0) + '</div><div class="dash-stat-label">Entries</div></div>'
                + '<div class="dash-stat-card"><div class="dash-stat-value">' + (top ? '#' + esc(top.overall_rank) : '—') + '</div><div class="dash-stat-label">Top filtered rank</div></div>'
                + '<div class="dash-stat-card"><div class="dash-stat-value">' + (top ? esc(top.name) : '—') + '</div><div class="dash-stat-label">Top filtered entry</div></div></div>'
                + '<div class="local-ai-rank-note"><b>Overall decision score:</b> ' + esc(state.payload.ranking.note) + ' Weights: '
                + Object.entries(state.payload.ranking.weights || {}).map(([key, weight]) => esc(label(key).replace(' score', '')) + ' ' + Math.round(Number(weight) * 100) + '%').join(' · ') + '.</div>'
                + filtersHtml() + axesHtml() + '<section class="dash-widget local-ai-widget"><div class="dash-widget-head"><h2>3D trade-off map</h2><span>Rotate, zoom and select an entry</span></div><div id="localAiChart" class="dash-chart local-ai-chart"></div></section>'
                + tableHtml(viewRows) + buildsHtml(viewRows);
            state.root.querySelector('#dashBack').addEventListener('click', () => host.navigateTo(''));
            state.root.querySelectorAll('[data-local-filter]').forEach((select) => select.addEventListener('change', () => {
                state.filters[select.dataset.localFilter] = new Set(Array.from(select.selectedOptions).map((option) => option.value));
                renderData();
            }));
            state.root.querySelector('[data-local-clear]').addEventListener('click', () => { state.filters = {}; renderData(); });
            state.root.querySelectorAll('[data-local-axis]').forEach((select) => select.addEventListener('change', () => {
                state.axes[select.dataset.localAxis] = select.value; drawChart();
            }));
            state.root.querySelectorAll('[data-local-sort]').forEach((button) => button.addEventListener('click', () => {
                const field = button.dataset.localSort;
                if (state.sort === field) state.direction *= -1;
                else { state.sort = field; state.direction = field === 'overall_rank' ? 1 : -1; }
                renderData();
            }));
            drawChart();
        }

        function drawChart() {
            const chart = state.root && state.root.querySelector('#localAiChart');
            if (!chart) return;
            if (!window.Plotly) {
                chart.innerHTML = '<p class="dash-loading">Loading 3D graph…</p>';
                if (window.CuttleOptionalCdn && typeof window.CuttleOptionalCdn.loadPlotlyExtras === 'function') window.CuttleOptionalCdn.loadPlotlyExtras();
                return;
            }
            const points = rows().filter((row) => ['x', 'y', 'z'].every((axis) => Number.isFinite(Number(row[state.axes[axis]]))));
            if (!points.length) { chart.innerHTML = '<p class="dash-empty">No plottable entries for these axes and filters.</p>'; return; }
            const colors = ['#636efa', '#ef553b', '#00cc96', '#ab63fa', '#ffa15a', '#19d3f3', '#ff6692'];
            const groups = new Map();
            points.forEach((row) => {
                const group = row.publisher || row.vendor || row.category || 'Other';
                if (!groups.has(group)) groups.set(group, []);
                groups.get(group).push(row);
            });
            const traces = Array.from(groups.entries()).map(([group, items], index) => ({
                type: 'scatter3d', mode: 'markers', name: group,
                x: items.map((row) => row[state.axes.x]), y: items.map((row) => row[state.axes.y]), z: items.map((row) => row[state.axes.z]),
                text: items.map((row) => row.name),
                hovertemplate: items.map((row) => '<b>' + esc(row.name) + '</b><br>Rank #' + row.overall_rank + ' · score ' + row.overall_score
                    + '<br>' + esc(label(state.axes.x)) + ': ' + esc(value(state.axes.x, row[state.axes.x]))
                    + '<br>' + esc(label(state.axes.y)) + ': ' + esc(value(state.axes.y, row[state.axes.y]))
                    + '<br>' + esc(label(state.axes.z)) + ': ' + esc(value(state.axes.z, row[state.axes.z])) + '<extra>' + esc(group) + '</extra>'),
                marker: { size: items.map((row) => Math.max(7, Math.min(17, 7 + row.overall_score / 14))), color: colors[index % colors.length], opacity: 0.9, line: { color: '#e2e8f0', width: 1 } },
            }));
            const color = getComputedStyle(document.body).getPropertyValue('--text-primary').trim() || '#e2e8f0';
            const muted = getComputedStyle(document.body).getPropertyValue('--text-secondary').trim() || '#94a3b8';
            const grid = getComputedStyle(document.body).getPropertyValue('--border-color').trim() || '#334155';
            const axis = (field) => ({ title: { text: label(field) }, color: muted, gridcolor: grid, zerolinecolor: grid, backgroundcolor: 'rgba(0,0,0,0)', showbackground: false });
            window.Plotly.react(chart, traces, {
                autosize: true, margin: { l: 0, r: 0, t: 0, b: 0 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color },
                scene: { xaxis: axis(state.axes.x), yaxis: axis(state.axes.y), zaxis: axis(state.axes.z), aspectmode: 'cube', dragmode: 'turntable' },
                legend: { font: { color: muted }, bgcolor: 'rgba(0,0,0,0.25)' },
            }, { responsive: true, displaylogo: false, displayModeBar: false });
        }

        return {
            render(root, payload) {
                state.root = root; state.payload = payload; state.axes = defaultAxes(payload.id); state.filters = {}; state.sort = 'overall_rank'; state.direction = 1;
                lastRenderer = this;
                renderData();
            },
            redraw: drawChart,
        };
    }

    window.CuttleLocalAiDashboard = { owns: (id) => IDS.has(id), create };
    window.addEventListener('cuttle-plotly-ready', () => { if (lastRenderer) lastRenderer.redraw(); });
})();
