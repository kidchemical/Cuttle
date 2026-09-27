(function () {
    'use strict';

    const root = document.getElementById('dashRoot');
    const AXIS_LABELS = {
        mean_cost_usd: 'Cost / task (USD)',
        mean_duration_seconds: 'Duration (sec)',
        mean_output_tokens: 'Output tokens',
        mean_agent_steps: 'Agent steps',
        score: 'DeepSWE pass@1 (%)',
        benchmark_count: 'Benchmarks represented',
        score_spread: 'Score spread (percentage points)',
        success_rate: 'Finished without error (%)',
        mean_total_tokens: 'Total tokens / turn',
        turns: 'Turns (log)',
    };
    const PERF_ID = 'cuttle-performance';
    const PERF_DEFAULT_AXES = { x: 'mean_duration_seconds', y: 'score', z: 'turns' };
    const USAGE_ID = 'cuttle-usage';
    const USAGE_COLORS = ['#636efa', '#ef553b', '#00cc96', '#ab63fa', '#ffa15a', '#19d3f3', '#ff6692', '#b6e880', '#ff97ff', '#fecb52'];
    const USAGE_FORMATS = {
        usd: { tick: '$,.2f', hover: '$,.2f' },
        tokens: { tick: '.3~s', hover: ',.0f' },
        count: { tick: ',d', hover: ',d' },
        hours: { tick: ',.1f', hover: ',.2f', suffix: ' h' },
    };
    let usageHidden = new Set();
    const PREF_KEY = 'cuttle_dash_widget_v1';
    const HAMBURGER_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';

    let catalog = [];
    let payload = null;
    let xField = 'mean_cost_usd';
    let yField = 'score';
    let zField = 'mean_duration_seconds';
    let axesUserSet = false;
    let invertAxes = { x: false, y: false, z: false };
    let axisLimits = { x: null, y: null, z: null };
    let sortKey = 'score';
    let sortDir = -1;
    let perfSortChosen = false;
    let filterState = { providers: new Set(), efforts: new Set(), harnesses: new Set() };
    let plotCamera = null;
    let controlMode = 'default';
    let projectionMode = 'perspective';
    let camSpeed = 1;
    let sweet = {
        enabled: false,
        kind: 'bang',
        shape: 'cube',
        scoreMin: 70,
        costMax: 4,
        durationMax: 1100,
    };
    let axisEdge = { x: '', y: '', z: '' };
    let axisFadeTimer = { x: 0, y: 0, z: 0 };
    let flyKeys = new Set();
    let flyRaf = 0;
    let flyVel = { x: 0, y: 0, z: 0 };
    let flyLastT = 0;
    let speedHudTimer = 0;
    let lookDrag = null;
    let pinchLock = false;
    let stableCam = null;
    let lastPlotGd = null;
    let camPushRaf = 0;
    let lastPointer = { x: 0, y: 0, ok: false };
    let activeRowIndex = null;
    let selectedRowIndex = null;
    let selectedRowIdFromUrl = '';
    let pinnedRowIndex = null;
    let widgetResizeObserver = null;
    let resizeRaf = 0;
    let axisLabelRaf = 0;
    let axisLabelTracking = false;
    let urlSyncTimer = 0;

    (function loadPrefs() {
        try {
            const raw = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
            if (raw.controlMode === 'free' || raw.controlMode === 'default') controlMode = raw.controlMode;
            if (raw.projectionMode === 'orthographic' || raw.projectionMode === 'perspective') projectionMode = raw.projectionMode;
            if (raw.sweet && typeof raw.sweet === 'object') {
                sweet = Object.assign(sweet, raw.sweet);
                sweet._user = true;
            }
            if (typeof raw.camSpeed === 'number') camSpeed = raw.camSpeed;
        } catch (_) {}
    })();

    (function loadUrlConfig() {
        const params = new URLSearchParams(window.location.search);
        const validAxes = new Set(Object.keys(AXIS_LABELS));
        ['x', 'y', 'z'].forEach((axis) => {
            const field = params.get(axis);
            if (field && validAxes.has(field)) {
                if (axis === 'x') xField = field;
                else if (axis === 'y') yField = field;
                else zField = field;
                axesUserSet = true;
            }
        });
        const inverted = new Set((params.get('invert') || '').split(',').filter(Boolean));
        invertAxes = { x: inverted.has('x'), y: inverted.has('y'), z: inverted.has('z') };
        ['x', 'y', 'z'].forEach((axis) => {
            const raw = params.get('l' + axis);
            if (!raw) return;
            const pair = raw.split(',').map(Number);
            if (pair.length === 2 && pair.every(Number.isFinite) && pair[0] <= pair[1]) axisLimits[axis] = { min: pair[0], max: pair[1] };
        });
        ['providers', 'efforts', 'harnesses'].forEach((param) => {
            const values = params.getAll(param.slice(0, -1)).filter(Boolean);
            if (values.length) filterState[param] = new Set(values);
        });
        if (params.has('zone')) sweet.enabled = params.get('zone') === '1';
        if (['bang', 'score', 'cheap', 'fast', 'custom'].includes(params.get('opt'))) sweet.kind = params.get('opt');
        if (['cube', 'wedge'].includes(params.get('shape'))) sweet.shape = params.get('shape');
        [['scoreMin', 'smin'], ['costMax', 'cmax'], ['durationMax', 'tmax']].forEach(([key, param]) => {
            const value = Number(params.get(param));
            if (params.has(param) && Number.isFinite(value)) sweet[key] = value;
        });
        if (['opt', 'shape', 'smin', 'cmax', 'tmax'].some((key) => params.has(key))) sweet._user = true;
        if (params.get('control') === 'default' || params.get('control') === 'free') controlMode = params.get('control');
        if (params.get('projection') === 'perspective' || params.get('projection') === 'orthographic') projectionMode = params.get('projection');
        const speed = Number(params.get('speed'));
        if (params.has('speed') && Number.isFinite(speed)) camSpeed = Math.min(12, Math.max(0.15, speed));
        const view = (params.get('view') || '').split(',').map(Number);
        if (view.length === 6 && view.every(Number.isFinite)) {
            plotCamera = {
                eye: { x: view[0], y: view[1], z: view[2] },
                center: { x: view[3], y: view[4], z: view[5] },
                up: { x: 0, y: 0, z: 1 },
                projection: { type: projectionMode },
            };
        }
        selectedRowIdFromUrl = params.get('sel') || '';
    })();

    function savePrefs() {
        try {
            localStorage.setItem(PREF_KEY, JSON.stringify({
                controlMode, projectionMode, sweet, camSpeed,
            }));
        } catch (_) {}
        scheduleUrlConfigSync();
    }

    function scheduleUrlConfigSync() {
        clearTimeout(urlSyncTimer);
        urlSyncTimer = setTimeout(syncUrlConfig, 180);
    }

    function isPerf() {
        return !!(payload && payload.id === PERF_ID);
    }

    function applyAxisLabels() {
        const perf = isPerf();
        const src = (payload && payload.source) || {};
        AXIS_LABELS.score = (src.score_name || (perf ? 'Accept score' : 'DeepSWE pass@1')) + ' (%)';
        AXIS_LABELS.mean_cost_usd = perf ? 'Cost / turn (USD)' : 'Cost / task (USD)';
        AXIS_LABELS.mean_duration_seconds = perf ? 'Median turn time (sec)' : 'Duration (sec)';
        AXIS_LABELS.mean_output_tokens = perf ? 'Output tokens / turn' : 'Output tokens';
    }

    function syncUrlConfig() {
        const url = new URL(window.location.href);
        url.searchParams.set('d', (payload && payload.id) || 'model-benchmarks');
        if (payload && payload.selected_source) url.searchParams.set('source', payload.selected_source);
        if (isPerf()) url.searchParams.set('days', String(payload.selected_days || 0));
        else url.searchParams.delete('days');
        url.searchParams.set('x', xField);
        url.searchParams.set('y', yField);
        url.searchParams.set('z', zField);
        ['x', 'y', 'z'].forEach((axis) => {
            const limit = axisLimits[axis];
            const key = 'l' + axis;
            if (limit) url.searchParams.set(key, Number(limit.min.toFixed(5)) + ',' + Number(limit.max.toFixed(5)));
            else url.searchParams.delete(key);
        });
        const inverted = ['x', 'y', 'z'].filter((axis) => invertAxes[axis]);
        if (inverted.length) url.searchParams.set('invert', inverted.join(','));
        else url.searchParams.delete('invert');
        [['providers', filterState.providers], ['efforts', filterState.efforts], ['harnesses', filterState.harnesses]].forEach(([key, values]) => {
            const param = key.slice(0, -1);
            url.searchParams.delete(param);
            Array.from(values).sort().forEach((value) => url.searchParams.append(param, value));
        });
        url.searchParams.set('zone', sweet.enabled ? '1' : '0');
        url.searchParams.set('opt', sweet.kind);
        url.searchParams.set('shape', sweet.shape);
        url.searchParams.set('smin', String(sweet.scoreMin));
        url.searchParams.set('cmax', String(sweet.costMax));
        url.searchParams.set('tmax', String(sweet.durationMax));
        url.searchParams.set('control', controlMode);
        url.searchParams.set('projection', projectionMode);
        url.searchParams.set('speed', String(Number(camSpeed.toFixed(2))));
        const selectedRow = selectedRowIndex == null ? null : (payload.rows || [])[selectedRowIndex];
        if (selectedRow && selectedRow.id) url.searchParams.set('sel', selectedRow.id);
        else url.searchParams.delete('sel');
        const camera = plotCamera || (lastPlotGd ? readLiveCamera(lastPlotGd) : null);
        if (camera && camera.eye && camera.center) {
            const values = [camera.eye.x, camera.eye.y, camera.eye.z, camera.center.x, camera.center.y, camera.center.z];
            url.searchParams.set('view', values.map((n) => Number(n).toFixed(4)).join(','));
        }
        window.history.replaceState(window.history.state, '', url.toString());
    }

    function dashIdFromUrl() {
        try {
            return new URLSearchParams(window.location.search).get('d') || '';
        } catch (_) {
            return '';
        }
    }

    function navigateTo(id) {
        const url = id
            ? '/dashboards_page.html?d=' + encodeURIComponent(id)
            : '/dashboards_page.html';
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-navigate', page: url }, '*');
            return;
        }
        window.location.href = url;
    }

    async function fetchJson(url) {
        const r = await fetch(url, { cache: 'no-store' });
        const data = await r.json();
        if (!r.ok || data.success === false) {
            throw new Error((data && data.error) || ('HTTP ' + r.status));
        }
        return data;
    }

    function fmtMoney(n) {
        if (n == null || Number.isNaN(n)) return '—';
        return '$' + Number(n).toFixed(n >= 10 ? 2 : 3);
    }
    function fmtScore(n) {
        if (n == null || Number.isNaN(n)) return '—';
        return Number(n).toFixed(1) + '%';
    }
    function fmtDur(sec) {
        if (sec == null || Number.isNaN(sec)) return '—';
        const s = Math.round(sec);
        if (s < 90) return s + 's';
        const m = Math.floor(s / 60);
        const r = s % 60;
        return m + 'm ' + r + 's';
    }
    function fmtWhen(iso) {
        if (!iso) return 'unknown';
        const dt = new Date(iso);
        if (Number.isNaN(dt.getTime())) return iso;
        const diff = Date.now() - dt.getTime();
        const mins = Math.round(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return mins + ' min ago';
        const hrs = Math.round(mins / 60);
        if (hrs < 36) return hrs + ' hr ago';
        return dt.toLocaleString();
    }

    function themeColors() {
        const css = getComputedStyle(document.body);
        return {
            text: (css.getPropertyValue('--text-primary') || '#e2e8f0').trim() || '#e2e8f0',
            muted: (css.getPropertyValue('--text-secondary') || '#94a3b8').trim() || '#94a3b8',
            grid: (css.getPropertyValue('--border-color') || '#334155').trim() || '#334155',
        };
    }

    function hamburgerBtn(id, aria) {
        return `<button type="button" class="dash-hamburger" id="${id}" aria-label="${escapeAttr(aria)}" aria-haspopup="true" aria-expanded="false">${HAMBURGER_SVG}</button>`;
    }

    function closeMenus() {
        document.querySelectorAll('.dash-menu').forEach((el) => { el.hidden = true; });
        document.querySelectorAll('.dash-hamburger[aria-expanded="true"]').forEach((el) => el.setAttribute('aria-expanded', 'false'));
    }

    function bindMenuToggle(btnId, menuId) {
        const btn = document.getElementById(btnId);
        const menu = document.getElementById(menuId);
        if (!btn || !menu) return;
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = menu.hidden;
            closeMenus();
            menu.hidden = !open;
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    }

    function renderHub() {
        const cards = catalog.map((d) => {
            const live = d.status === 'live';
            const tag = live ? 'live' : 'soon';
            const open = live ? `data-open="${escapeAttr(d.id)}"` : '';
            return `<button type="button" class="dash-card ${live ? '' : 'soon'}" ${open}>
                <span class="dash-card-status ${tag}">${tag}</span>
                <h2>${escapeHtml(d.title)}</h2>
                <p>${escapeHtml(d.blurb)}</p>
            </button>`;
        }).join('');
        root.innerHTML = `
            <div class="dash-page-bar">
                <div class="compact-page-title">
                    <h1>Dashboards</h1>
                    <p>Tracked landscapes you can reopen anytime. Model Benchmarks is public; My Cuttle Performance and Cuttle Usage are your local turns.</p>
                </div>
            </div>
            <div class="dash-grid">${cards}</div>`;
        root.querySelectorAll('[data-open]').forEach((btn) => {
            btn.addEventListener('click', () => navigateTo(btn.getAttribute('data-open')));
        });
    }

    function selectedRows() {
        const rows = (payload && payload.rows) || [];
        return rows.filter((r) => {
            if (filterState.providers.size && !filterState.providers.has(r.provider)) return false;
            if (filterState.efforts.size && !filterState.efforts.has(r.reasoning_effort)) return false;
            if (filterState.harnesses.size && !filterState.harnesses.has(r.harness)) return false;
            return true;
        });
    }

    function chipRow(title, values, setName) {
        let list = values || [];
        if (setName === 'efforts') {
            const rank = { unspecified: 0, none: 0, minimal: 1, low: 2, medium: 3, high: 4, xhigh: 5, max: 6 };
            list = list.slice().sort((a, b) => {
                const ra = rank[String(a).toLowerCase()];
                const rb = rank[String(b).toLowerCase()];
                const aa = ra == null ? 50 : ra;
                const bb = rb == null ? 50 : rb;
                if (aa !== bb) return aa - bb;
                return String(a).localeCompare(String(b));
            });
        }
        const chips = list.map((v) => {
            const on = !filterState[setName].size || filterState[setName].has(v);
            return `<button type="button" class="dash-chip ${on ? 'is-on' : ''}" data-set="${escapeAttr(setName)}" data-value="${escapeAttr(v)}">${escapeHtml(v)}</button>`;
        }).join('');
        return `<div class="dash-chip-row"><h3>${escapeHtml(title)}</h3>${chips}</div>`;
    }

    function renderModelBenchmarks() {
        const src = payload.source || {};
        applyAxisLabels();
        const stats = payload.stats || {};
        const filters = payload.filters || {};
        const disc = payload.discovery || {};
        const err = payload.fetch_error
            ? `<div class="dash-error">Live fetch failed (${escapeHtml(payload.fetch_error)}). ${payload.stale ? 'Showing stale cached rows.' : (payload.rows && payload.rows.length ? 'Showing available rows.' : 'No cached rows are available.')}</div>`
            : '';

        if (payload.selected_source === 'aggregate' && !axesUserSet) {
            xField = 'benchmark_count';
            yField = 'score';
            zField = 'score_spread';
        } else if (!axesUserSet && (xField === 'benchmark_count' || zField === 'score_spread')) {
            xField = 'mean_cost_usd';
            yField = 'score';
            zField = 'mean_duration_seconds';
        }
        if (axesUserSet) {
            const available = (field) => (payload.rows || []).some((row) => row[field] != null && Number.isFinite(Number(row[field])));
            if (!available(xField)) xField = 'mean_cost_usd';
            if (!available(yField)) yField = 'score';
            if (!available(zField)) zField = 'mean_duration_seconds';
        }
        restoreSelectionFromUrl();

        const latest = (disc.latest || []).slice(0, 8).map((item) => {
            const name = escapeHtml(item.name || item.benchmark_id || 'benchmark');
            const href = item.url ? `<a href="${escapeAttr(item.url)}" target="_blank" rel="noopener">${name}</a>` : name;
            const when = item.latest_result_at ? ` · ${escapeHtml(fmtWhen(item.latest_result_at))}` : '';
            return `<li>${href}${when}</li>`;
        }).join('');

        root.innerHTML = `
            <div class="dash-page-bar">
                <button type="button" class="dash-back" id="dashBack">← All dashboards</button>
                <div class="compact-page-title">
                    <h1>Model Benchmarks</h1>
                    <p>${payload.selected_source === 'aggregate'
                        ? 'Equal-weight mean score across available public benchmarks; benchmark count and score spread show coverage and disagreement.'
                        : escapeHtml((src.name || 'Public benchmark') + ' results — select a source to switch leaderboards.')}</p>
                </div>
                <div class="dash-menu-wrap">
                    ${hamburgerBtn('dashPageMenuBtn', 'Page menu')}
                    <div class="dash-menu" id="dashPageMenu" hidden>
                        <button type="button" class="dash-menu-item" data-page-action="refresh">Refresh data</button>
                    </div>
                </div>
            </div>
            <div class="dash-source-picker">
                <label for="dashSource">Benchmark source</label>
                <select id="dashSource">${(payload.available_sources || []).map((item) =>
                    `<option value="${escapeAttr(item.id)}"${item.id === payload.selected_source ? ' selected' : ''}>${escapeHtml(item.name)}</option>`
                ).join('')}</select>
                ${payload.aggregate_note ? `<p>${escapeHtml(payload.aggregate_note)}</p>` : ''}
            </div>
            <div class="dash-meta">
                <span>Last refreshed: <strong>${escapeHtml(fmtWhen(payload.fetched_at))}</strong></span>
                ${src.generated_at ? `<span>Source generated: ${escapeHtml(fmtWhen(src.generated_at))}</span>` : ''}
                <span>New in last 48h: <strong>${stats.new || 0}</strong></span>
                ${src.page ? `<a href="${escapeAttr(src.page)}" target="_blank" rel="noopener">${escapeHtml(src.name)}</a>` : ''}
            </div>
            ${err}
            <div class="dash-stats">
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.configs || 0}</div><div class="dash-stat-label">Configs</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${src.n_tasks_in_set || '—'}</div><div class="dash-stat-label">Tasks</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${payload.selected_source === 'aggregate' ? stats.configs || 0 : stats.plottable || 0}</div><div class="dash-stat-label">${payload.selected_source === 'aggregate' ? 'Aggregated models' : 'With cost+time'}</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.new || 0}</div><div class="dash-stat-label">New</div></div>
            </div>
            ${axesToolbarHtml()}
            <div class="dash-chip-filters" id="dashChips">
                ${chipRow('Provider', filters.providers || [], 'providers')}
                ${chipRow('Reasoning', filters.reasoning_efforts || [], 'efforts')}
                ${chipRow('Harness', filters.harnesses || [], 'harnesses')}
            </div>
            ${chartWidgetHtml()}
            <div class="jobs-table-scroll jobs-table-scroll--capped" data-jobs-table="1">
                <table class="hist-table" id="dashTable"></table>
            </div>
            <div class="dash-discovery">
                <h2>BenchmarkList discovery</h2>
                ${disc.ok === false ? `<p class="dash-empty">${escapeHtml(disc.error || 'Unavailable')}</p>` : `<ul>${latest || '<li>No recent public updates in the 7-day window.</li>'}</ul>`}
            </div>`;

        document.getElementById('dashBack').addEventListener('click', () => navigateTo(''));
        document.getElementById('dashSource').addEventListener('change', (e) => {
            switchDataset({ source: e.target.value });
        });
        bindMenuToggle('dashPageMenuBtn', 'dashPageMenu');
        document.getElementById('dashPageMenu').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-page-action]');
            if (!btn) return;
            closeMenus();
            if (btn.getAttribute('data-page-action') === 'refresh') loadDashboard(true);
        });
        bindChartWidget();
    }

    function restoreSelectionFromUrl() {
        if (selectedRowIdFromUrl) {
            selectedRowIndex = (payload.rows || []).findIndex((row) => row.id === selectedRowIdFromUrl);
            if (selectedRowIndex < 0 || !selectedRows().some((row) => (payload.rows || []).indexOf(row) === selectedRowIndex)) selectedRowIndex = null;
            selectedRowIdFromUrl = '';
        }
        activeRowIndex = selectedRowIndex;
        seedSweetFromData(selectedRows().length ? selectedRows() : (payload.rows || []), false);
    }

    function axesToolbarHtml() {
        return `<div class="dash-toolbar" id="dashAxes">
                ${axisControlRow('x', xField, false)}
                ${axisControlRow('y', yField, true)}
                ${axisControlRow('z', zField, false)}
            </div>`;
    }

    function switchDataset(params) {
        filterState = { providers: new Set(), efforts: new Set(), harnesses: new Set() };
        selectedRowIndex = null;
        activeRowIndex = null;
        pinnedRowIndex = null;
        axisLimits = { x: null, y: null, z: null };
        if (lastPlotGd && window.Plotly) {
            try { window.Plotly.purge(lastPlotGd); } catch (_) {}
            lastPlotGd = null;
        }
        const url = new URL(window.location.href);
        url.searchParams.set('d', (payload && payload.id) || 'model-benchmarks');
        Object.keys(params).forEach((key) => {
            if (params[key] == null || params[key] === '') url.searchParams.delete(key);
            else url.searchParams.set(key, params[key]);
        });
        url.searchParams.delete('sel');
        window.history.replaceState({}, '', url);
        loadDashboard(false);
    }

    function bindChartWidget() {
        bindMenuToggle('dashWidgetMenuBtn', 'dashWidgetMenu');
        bindAxisControls();
        document.getElementById('dashResetView').addEventListener('click', resetCameraView);
        document.getElementById('dashChips').addEventListener('click', onChipClick);
        bindWidgetMenu();
        observeWidgetSize();
        syncHint();
        renderTable();
        drawChart();
        syncUrlConfig();
    }

    function chartWidgetHtml() {
        return `<p class="dash-chart-hint" id="dashHint"></p>
            <div class="dash-widget" id="dashWidget">
                <div class="dash-chart" id="dashChart"></div>
                <div class="dash-axis-float" data-axis="x"></div>
                <div class="dash-axis-float" data-axis="y"></div>
                <div class="dash-axis-float" data-axis="z"></div>
                <div class="dash-hover-tip cuttle-tooltip is-wrap" id="dashHoverTip" hidden></div>
                <div class="dash-speed-hud" id="dashSpeedHud">Speed 1.0×</div>
                <div class="dash-widget-tools">
                    <button type="button" class="dash-reset-view" id="dashResetView" title="Reset camera and orbit" aria-label="Reset camera and orbit">↻</button>
                    ${hamburgerBtn('dashWidgetMenuBtn', 'Widget menu')}
                </div>
                <div class="dash-menu" id="dashWidgetMenu" hidden>
                    <button type="button" class="dash-menu-item" data-widget-action="fullscreen">Full screen</button>
                    <h4>Controls</h4>
                    <button type="button" class="dash-menu-item" data-widget-action="controls" data-value="default">Default (turntable)</button>
                    <button type="button" class="dash-menu-item" data-widget-action="controls" data-value="free">Free cam (Unity-style)</button>
                    <h4>Perspective</h4>
                    <button type="button" class="dash-menu-item" data-widget-action="projection" data-value="perspective">Perspective</button>
                    <button type="button" class="dash-menu-item" data-widget-action="projection" data-value="orthographic">Orthographic</button>
                    <h4>Sweet spot</h4>
                    <button type="button" class="dash-menu-item" data-widget-action="sweet-toggle">Show optimal zone</button>
                    <label class="dash-menu-row">Optimization
                        <select id="dashSweetKind">
                            <option value="bang">Bang for buck</option>
                            <option value="score">Highest score</option>
                            <option value="cheap">Cheapest</option>
                            <option value="fast">Fastest</option>
                            <option value="custom">Custom</option>
                        </select>
                    </label>
                    <label class="dash-menu-row">Shape
                        <select id="dashSweetShape">
                            <option value="cube">Cube</option>
                            <option value="wedge">Wedge</option>
                        </select>
                    </label>
                    <div class="dash-tune">
                        <span id="dashTuneScore">Score ≥</span>
                        <input type="range" id="dashSweetScore" min="0" max="100" step="0.5">
                    </div>
                    <div class="dash-tune">
                        <span id="dashTuneCost">Cost ≤</span>
                        <input type="range" id="dashSweetCost" min="0.1" max="20" step="0.1">
                    </div>
                    <div class="dash-tune">
                        <span id="dashTuneTime">Time ≤</span>
                        <input type="range" id="dashSweetTime" min="30" max="4000" step="10">
                    </div>
                    <button type="button" class="dash-menu-item" data-widget-action="sweet-reset">Reset zone to preset</button>
                </div>
            </div>`;
    }

    function onChipClick(e) {
        const chip = e.target.closest('.dash-chip');
        if (!chip) return;
        const setName = chip.getAttribute('data-set');
        const value = chip.getAttribute('data-value');
        const row = chip.parentElement;
        chip.classList.toggle('is-on');
        const on = Array.from(row.querySelectorAll('.dash-chip.is-on')).map((c) => c.getAttribute('data-value'));
        const all = Array.from(row.querySelectorAll('.dash-chip')).map((c) => c.getAttribute('data-value'));
        if (!on.length || on.length === all.length) filterState[setName] = new Set();
        else filterState[setName] = new Set(on);
        refreshAxisControls();
        renderTable();
        drawChart();
        scheduleUrlConfigSync();
    }

    function syncHint() {
        const el = document.getElementById('dashHint');
        if (!el) return;
        el.textContent = controlMode === 'free'
            ? 'Free cam: right-drag / one-finger look · WASD move · Q/E up-down · Shift sprint · wheel = speed · pinch dolly · click a point to pin'
            : 'Default: one finger yaw/pitch · pinch zoom · hover near a point; click to pin';
    }

    function bindWidgetMenu() {
        const menu = document.getElementById('dashWidgetMenu');
        if (!menu) return;
        const kind = document.getElementById('dashSweetKind');
        const shape = document.getElementById('dashSweetShape');
        const sScore = document.getElementById('dashSweetScore');
        const sCost = document.getElementById('dashSweetCost');
        const sTime = document.getElementById('dashSweetTime');
        kind.value = sweet.kind;
        shape.value = sweet.shape;
        sScore.value = String(sweet.scoreMin);
        sCost.value = String(sweet.costMax);
        sTime.value = String(sweet.durationMax);
        updateTuneLabels();
        syncWidgetMenuState();

        menu.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-widget-action]');
            if (!btn) return;
            const action = btn.getAttribute('data-widget-action');
            if (action === 'fullscreen') {
                toggleFullscreen();
                closeMenus();
                return;
            }
            if (action === 'controls') {
                controlMode = btn.getAttribute('data-value');
                flyVel = { x: 0, y: 0, z: 0 };
                flyLastT = 0;
                lookDrag = null;
                if (controlMode === 'default') {
                    plotCamera = sanitizeCamera({
                        eye: { x: 1.55, y: 1.45, z: 1.15 },
                        center: { x: 0, y: 0, z: 0 },
                        up: { x: 0, y: 0, z: 1 },
                    });
                }
                savePrefs();
                syncHint();
                syncWidgetMenuState();
                drawChart();
                return;
            }
            if (action === 'projection') {
                projectionMode = btn.getAttribute('data-value');
                savePrefs();
                syncWidgetMenuState();
                drawChart();
                return;
            }
            if (action === 'sweet-toggle') {
                sweet.enabled = !sweet.enabled;
                savePrefs();
                syncWidgetMenuState();
                renderTable();
                drawChart();
                return;
            }
            if (action === 'sweet-reset') {
                seedSweetFromData(selectedRows(), true);
                kind.value = sweet.kind;
                sScore.value = String(sweet.scoreMin);
                sCost.value = String(sweet.costMax);
                sTime.value = String(sweet.durationMax);
                updateTuneLabels();
                savePrefs();
                renderTable();
                drawChart();
            }
        });
        kind.addEventListener('change', () => {
            sweet.kind = kind.value;
            seedSweetFromData(selectedRows(), true);
            sScore.value = String(sweet.scoreMin);
            sCost.value = String(sweet.costMax);
            sTime.value = String(sweet.durationMax);
            updateTuneLabels();
            savePrefs();
            renderTable();
            drawChart();
        });
        shape.addEventListener('change', () => {
            sweet.shape = shape.value;
            savePrefs();
            renderTable();
            drawChart();
        });
        function onTune() {
            sweet.kind = 'custom';
            kind.value = 'custom';
            sweet._user = true;
            sweet.scoreMin = Number(sScore.value);
            sweet.costMax = Number(sCost.value);
            sweet.durationMax = Number(sTime.value);
            updateTuneLabels();
            savePrefs();
            renderTable();
            drawChart();
        }
        sScore.addEventListener('input', onTune);
        sCost.addEventListener('input', onTune);
        sTime.addEventListener('input', onTune);
    }

    function syncWidgetMenuState() {
        const menu = document.getElementById('dashWidgetMenu');
        if (!menu) return;
        menu.querySelectorAll('[data-widget-action="controls"]').forEach((b) => {
            b.classList.toggle('is-on', b.getAttribute('data-value') === controlMode);
        });
        menu.querySelectorAll('[data-widget-action="projection"]').forEach((b) => {
            b.classList.toggle('is-on', b.getAttribute('data-value') === projectionMode);
        });
        const tog = menu.querySelector('[data-widget-action="sweet-toggle"]');
        if (tog) tog.textContent = sweet.enabled ? 'Hide optimal zone' : 'Show optimal zone';
    }

    function updateTuneLabels() {
        const a = document.getElementById('dashTuneScore');
        const b = document.getElementById('dashTuneCost');
        const c = document.getElementById('dashTuneTime');
        if (a) a.textContent = 'Score ≥ ' + fmtScore(sweet.scoreMin);
        if (b) b.textContent = 'Cost ≤ ' + fmtMoney(sweet.costMax);
        if (c) c.textContent = 'Time ≤ ' + fmtDur(sweet.durationMax);
    }

    function toggleFullscreen() {
        const wrap = document.getElementById('dashWidget');
        if (!wrap) return;
        const doc = document;
        const active = doc.fullscreenElement || doc.webkitFullscreenElement;
        if (active) {
            const fn = doc.exitFullscreen || doc.webkitExitFullscreen;
            if (fn) fn.call(doc);
            wrap.classList.remove('is-fs');
        } else {
            const fn = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
            if (fn) fn.call(wrap);
            else wrap.classList.add('is-fs');
        }
        setTimeout(() => {
            const gd = lastPlotGd;
            if (gd && window.Plotly) {
                try { window.Plotly.Plots.resize(gd); } catch (_) {}
                updateAxisLabels(gd);
            }
        }, 200);
    }

    function percentile(arr, p) {
        const s = arr.filter((n) => n != null && !Number.isNaN(n)).slice().sort((a, b) => a - b);
        if (!s.length) return 0;
        const i = (s.length - 1) * p;
        const lo = Math.floor(i);
        const hi = Math.ceil(i);
        if (lo === hi) return s[lo];
        return s[lo] * (hi - i) + s[hi] * (i - lo);
    }

    function seedSweetFromData(rows, force) {
        if (!force && sweet._user) return;
        const scores = rows.map((r) => r.score);
        const costs = rows.map((r) => r.mean_cost_usd);
        const times = rows.map((r) => r.mean_duration_seconds);
        const kind = sweet.kind;
        if (kind === 'score') {
            sweet.scoreMin = percentile(scores, 0.7);
            sweet.costMax = percentile(costs, 0.98);
            sweet.durationMax = percentile(times, 0.98);
        } else if (kind === 'cheap') {
            sweet.scoreMin = percentile(scores, 0.15);
            sweet.costMax = percentile(costs, 0.35);
            sweet.durationMax = percentile(times, 0.98);
        } else if (kind === 'fast') {
            sweet.scoreMin = percentile(scores, 0.15);
            sweet.costMax = percentile(costs, 0.98);
            sweet.durationMax = percentile(times, 0.35);
        } else {
            sweet.scoreMin = percentile(scores, 0.55);
            sweet.costMax = percentile(costs, 0.45);
            sweet.durationMax = percentile(times, 0.45);
        }
        sweet._user = true;
        sweet._seeded = true;
    }

    function fieldConstraint(field, rows) {
        const vals = rows.map((r) => r[field]).filter((n) => n != null && !Number.isNaN(n));
        if (!vals.length) return { min: 0, max: 1, lo: 0, hi: 1, better: field === 'score' ? 'high' : 'low' };
        const min = Math.min.apply(null, vals);
        const max = Math.max.apply(null, vals);
        let lo = min;
        let hi = max;
        if (field === 'score') lo = Math.max(min, sweet.scoreMin);
        else if (field === 'mean_cost_usd') hi = Math.min(max, sweet.costMax);
        else if (field === 'mean_duration_seconds') hi = Math.min(max, sweet.durationMax);
        if (lo > hi) {
            const t = lo;
            lo = hi;
            hi = t;
        }
        return { min, max, lo, hi, better: field === 'score' ? 'high' : 'low' };
    }

    function sweetSpotTrace(rows) {
        if (!sweet.enabled || !rows.length) return [];
        const cx = fieldConstraint(xField, rows);
        const cy = fieldConstraint(yField, rows);
        const cz = fieldConstraint(zField, rows);
        const x0 = cx.lo, x1 = cx.hi, y0 = cy.lo, y1 = cy.hi, z0 = cz.lo, z1 = cz.hi;
        let vertices;
        let edges;
        if (sweet.shape === 'wedge') {
            const ix = cx.better === 'high' ? x1 : x0;
            const iy = cy.better === 'high' ? y1 : y0;
            const iz = cz.better === 'high' ? z1 : z0;
            const tx = cx.better === 'high' ? x0 : x1;
            const ty = cy.better === 'high' ? y0 : y1;
            const tz = cz.better === 'high' ? z0 : z1;
            vertices = [[ix, iy, iz], [tx, iy, iz], [ix, ty, iz], [ix, iy, tz]];
            edges = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]];
        } else {
            vertices = [
                [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
            ];
            edges = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
        }
        const x = [], y = [], z = [];
        edges.forEach(([a, b]) => {
            [vertices[a], vertices[b], null].forEach((p) => {
                x.push(p && p[0]); y.push(p && p[1]); z.push(p && p[2]);
            });
        });
        // A line-only zone avoids a WebGL mesh winning hover/click picking over
        // model points that sit inside the zone.
        return [{
            type: 'scatter3d',
            mode: 'lines',
            name: 'Sweet spot',
            showlegend: true,
            hoverinfo: 'skip',
            x, y, z,
            line: { color: 'rgba(74, 222, 128, 0.75)', width: 4 },
        }];
    }

    function effortConnectionTraces(rows) {
        const effortRank = { minimal: -1, low: 0, medium: 1, high: 2, xhigh: 3, max: 4 };
        const groups = new Map();
        rows.forEach((row) => {
            const effort = String(row.reasoning_effort || '').toLowerCase();
            if (!(effort in effortRank)) return;
            const model = String(row.model || row.label || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            if (!model) return;
            const key = model + '|' + String(row.provider || '') + '|' + String(row.harness || '');
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push({ row, effort });
        });
        const traces = [];
        groups.forEach((items) => {
            const efforts = new Set(items.map((item) => item.effort));
            if (efforts.size < 2) return;
            items.sort((a, b) => effortRank[a.effort] - effortRank[b.effort]);
            traces.push({
                type: 'scatter3d', mode: 'lines',
                name: (items[0].row.model || items[0].row.label) + ' effort progression',
                showlegend: false, hoverinfo: 'skip',
                meta: { effortKey: effortModelKey(items[0].row) },
                x: items.map((item) => item.row[xField]),
                y: items.map((item) => item.row[yField]),
                z: items.map((item) => item.row[zField]),
                line: effortModelKey(items[0].row) === selectedEffortKey()
                    ? { color: '#fbbf24', width: 6 }
                    : { color: selectedRowIndex == null ? 'rgba(226, 232, 240, 0.55)' : 'rgba(148, 163, 184, 0.20)', width: selectedRowIndex == null ? 3 : 2 },
            });
        });
        return traces;
    }

    function effortModelKey(row) {
        const model = String(row && (row.model || row.label) || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        return model + '|' + String(row && row.provider || '') + '|' + String(row && row.harness || '');
    }

    function selectedEffortKey() {
        const row = selectedRowIndex == null ? null : (payload.rows || [])[selectedRowIndex];
        return row ? effortModelKey(row) : '';
    }

    function zoneDistanceMap(rows) {
        const axes = [xField, yField, zField];
        const constraints = axes.map((field) => fieldConstraint(field, rows));
        const spans = constraints.map((c) => Math.max(c.max - c.min, 1e-9));
        const normPoint = (values) => values.map((v, i) => (v - constraints[i].min) / spans[i]);
        const bounds = constraints.map((c, i) => [
            (c.lo - c.min) / spans[i],
            (c.hi - c.min) / spans[i],
        ]);
        const distanceToTriangle = (p, a, b, c) => {
            const ab = vsub(b, a), ac = vsub(c, a), ap = vsub(p, a);
            const d1 = vdot(ab, ap), d2 = vdot(ac, ap);
            if (d1 <= 0 && d2 <= 0) return vecLen(ap);
            const bp = vsub(p, b), d3 = vdot(ab, bp), d4 = vdot(ac, bp);
            if (d3 >= 0 && d4 <= d3) return vecLen(bp);
            const vc = d1 * d4 - d3 * d2;
            if (vc <= 0 && d1 >= 0 && d3 <= 0) return vecLen(vsub(p, vadd(a, vscale(ab, d1 / (d1 - d3)))));
            const cp = vsub(p, c), d5 = vdot(ab, cp), d6 = vdot(ac, cp);
            if (d6 >= 0 && d5 <= d6) return vecLen(cp);
            const vb = d5 * d2 - d1 * d6;
            if (vb <= 0 && d2 >= 0 && d6 <= 0) return vecLen(vsub(p, vadd(a, vscale(ac, d2 / (d2 - d6)))));
            const va = d3 * d6 - d5 * d4;
            if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
                const edge = vsub(c, b);
                return vecLen(vsub(p, vadd(b, vscale(edge, (d4 - d3) / ((d4 - d3) + (d5 - d6))))));
            }
            const denom = 1 / (va + vb + vc);
            const q = vadd(a, vadd(vscale(ab, vb * denom), vscale(ac, vc * denom)));
            return vecLen(vsub(p, q));
        };
        const distToZone = (p) => {
            if (sweet.shape !== 'wedge') {
                const delta = p.map((v, i) => Math.max(bounds[i][0] - v, 0, v - bounds[i][1]));
                return Math.hypot.apply(null, delta);
            }
            const peak = bounds.map((b, i) => constraints[i].better === 'high' ? b[1] : b[0]);
            const base = bounds.map((b, i) => constraints[i].better === 'high' ? b[0] : b[1]);
            const verts = [peak, [base[0], peak[1], peak[2]], [peak[0], base[1], peak[2]], [peak[0], peak[1], base[2]]];
            const a = { x: verts[0][0], y: verts[0][1], z: verts[0][2] };
            const b = { x: verts[1][0], y: verts[1][1], z: verts[1][2] };
            const c = { x: verts[2][0], y: verts[2][1], z: verts[2][2] };
            const d = { x: verts[3][0], y: verts[3][1], z: verts[3][2] };
            const q = { x: p[0], y: p[1], z: p[2] };
            const u = vsub(b, a), v = vsub(c, a), w = vsub(d, a), rhs = vsub(q, a);
            const det = vdot(u, vcross(v, w));
            if (Math.abs(det) > 1e-9) {
                const wb = vdot(rhs, vcross(v, w)) / det;
                const wc = vdot(u, vcross(rhs, w)) / det;
                const wd = vdot(u, vcross(v, rhs)) / det;
                if (wb >= 0 && wc >= 0 && wd >= 0 && wb + wc + wd <= 1) return 0;
            }
            if (Math.abs(det) <= 1e-9) {
                const delta = p.map((value, i) => Math.max(bounds[i][0] - value, 0, value - bounds[i][1]));
                return Math.hypot.apply(null, delta);
            }
            return Math.min(
                distanceToTriangle(q, a, b, c), distanceToTriangle(q, a, b, d),
                distanceToTriangle(q, a, c, d), distanceToTriangle(q, b, c, d)
            );
        };
        const result = new Map();
        rows.forEach((r) => {
            const index = (payload.rows || []).indexOf(r);
            const vals = axes.map((field) => r[field]);
            result.set(index, vals.some((v) => v == null || !Number.isFinite(Number(v))) ? null : distToZone(normPoint(vals.map(Number))));
        });
        return result;
    }

    function fmtZoneDistance(value) {
        return value == null ? '—' : (value === 0 ? 'Inside zone' : value.toFixed(3));
    }

    function renderTable() {
        const table = document.getElementById('dashTable');
        if (!table) return;
        const filtered = selectedRows();
        if (selectedRowIndex != null && !filtered.some((r) => (payload.rows || []).indexOf(r) === selectedRowIndex)) {
            selectedRowIndex = null;
            selectedRowIdFromUrl = '';
            pinnedRowIndex = null;
            scheduleUrlConfigSync();
        }
        if (activeRowIndex != null && !filtered.some((r) => (payload.rows || []).indexOf(r) === activeRowIndex)) {
            activeRowIndex = null;
            pinnedRowIndex = null;
            hideHoverTip();
        }
        const distances = zoneDistanceMap(filtered);
        const rows = filtered.slice().sort((a, b) => {
            const av = sortKey === 'zone_distance' ? distances.get((payload.rows || []).indexOf(a)) : a[sortKey];
            const bv = sortKey === 'zone_distance' ? distances.get((payload.rows || []).indexOf(b)) : b[sortKey];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (av < bv) return -1 * sortDir;
            if (av > bv) return 1 * sortDir;
            return 0;
        });
        if (isPerf()) {
            renderPerfConfigTable(table, rows, distances);
            return;
        }
        const head = `
            <thead><tr>
                <th data-sort="label">Model</th>
                <th data-sort="provider">Provider</th>
                <th data-sort="score">${escapeHtml((payload.source && payload.source.score_name) || 'Score')} (%)</th>
                <th data-sort="mean_cost_usd">Cost</th>
                <th data-sort="mean_duration_seconds">Time</th>
                <th data-sort="zone_distance" title="Euclidean distance to the current optimal zone, normalized to the plotted axes. Zero means inside the zone.">Shortest zone distance</th>
                <th data-sort="mean_agent_steps">Steps</th>
                <th data-sort="harness">Harness</th>
            </tr></thead>`;
        const body = rows.map((r) => {
            const badge = r.is_new ? '<span class="dash-new">NEW</span>' : '';
            const detail = (r.benchmark_scores || []).map((item) => `${item.source}: ${fmtScore(item.score)}`).join(' · ');
            const rowIndex = (payload.rows || []).indexOf(r);
            return `<tr data-row-index="${rowIndex}" class="${rowIndex === selectedRowIndex ? 'is-selected' : ''}${rowIndex === activeRowIndex ? ' is-active' : ''}" aria-selected="${rowIndex === selectedRowIndex ? 'true' : 'false'}">
                <td class="jobs-cell-wrap"${detail ? ` title="${escapeAttr(detail)}"` : ''}>${escapeHtml(r.label)}${badge}</td>
                <td>${escapeHtml(r.provider)}</td>
                <td>${fmtScore(r.score)}</td>
                <td>${fmtMoney(r.mean_cost_usd)}</td>
                <td>${fmtDur(r.mean_duration_seconds)}</td>
                <td>${fmtZoneDistance(distances.get(rowIndex))}</td>
                <td>${r.mean_agent_steps == null ? '—' : Number(r.mean_agent_steps).toFixed(1)}</td>
                <td>${escapeHtml(r.harness)}</td>
            </tr>`;
        }).join('');
        table.innerHTML = head + '<tbody>' + (body || '<tr><td colspan="8">No rows match filters.</td></tr>') + '</tbody>';
        bindTableInteractions(table, rows);
    }

    function renderPerfConfigTable(table, rows, distances) {
        const head = `
            <thead><tr>
                <th data-sort="label">Model</th>
                <th data-sort="harness">Agent</th>
                <th data-sort="provider">Provider</th>
                <th data-sort="turns">Turns</th>
                <th data-sort="score" title="Your thumbs first, then Jev labels, then whether the run finished without error">Accept score</th>
                <th data-sort="success_rate">Finished</th>
                <th data-sort="mean_duration_seconds" title="Median time from send to reply">Median time</th>
                <th data-sort="mean_output_tokens">Out tokens</th>
                <th data-sort="mean_cost_usd">Cost / turn</th>
                <th data-sort="zone_distance" title="Euclidean distance to the current optimal zone, normalized to the plotted axes. Zero means inside the zone.">Zone distance</th>
            </tr></thead>`;
        const body = rows.map((r) => {
            const rowIndex = (payload.rows || []).indexOf(r);
            const thumbs = (r.good_feedback || r.bad_feedback)
                ? ` <span class="dash-thumbs" title="Thumbs up / down">👍${r.good_feedback} 👎${r.bad_feedback}</span>`
                : '';
            return `<tr data-row-index="${rowIndex}" class="${rowIndex === selectedRowIndex ? 'is-selected' : ''}${rowIndex === activeRowIndex ? ' is-active' : ''}" aria-selected="${rowIndex === selectedRowIndex ? 'true' : 'false'}">
                <td class="jobs-cell-wrap">${escapeHtml(r.label)}</td>
                <td>${escapeHtml(r.harness)}</td>
                <td>${escapeHtml(r.provider)}</td>
                <td>${escapeHtml(String(r.turns))}</td>
                <td>${fmtScore(r.score)}${thumbs}</td>
                <td>${fmtScore(r.success_rate)}</td>
                <td>${fmtDur(r.mean_duration_seconds)}</td>
                <td>${fmtCount(r.mean_output_tokens)}</td>
                <td>${fmtMoney(r.mean_cost_usd)}</td>
                <td>${fmtZoneDistance(distances.get(rowIndex))}</td>
            </tr>`;
        }).join('');
        table.innerHTML = head + '<tbody>' + (body || '<tr><td colspan="10">No configs match filters.</td></tr>') + '</tbody>';
        bindTableInteractions(table, rows);
    }

    function fmtCount(n) {
        if (n == null || !Number.isFinite(Number(n))) return '—';
        const v = Number(n);
        if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
        if (v >= 1e4) return Math.round(v / 1e3) + 'k';
        return String(Math.round(v));
    }

    function bindTableInteractions(table, rows) {
        if (activeRowIndex != null && !rows.some((r) => (payload.rows || []).indexOf(r) === activeRowIndex)) activeRowIndex = null;
        table.querySelectorAll('tbody tr[data-row-index]').forEach((tr) => {
            tr.addEventListener('click', () => {
                commitSelection(Number(tr.dataset.rowIndex), false);
            });
        });
        table.querySelectorAll('th[data-sort]').forEach((th) => {
            th.addEventListener('click', () => {
                const key = th.getAttribute('data-sort');
                if (sortKey === key) sortDir *= -1;
                else { sortKey = key; sortDir = key === 'label' || key === 'provider' || key === 'harness' ? 1 : -1; }
                renderTable();
            });
        });
        if (selectedRowIndex != null) {
            const selectedRow = table.querySelector(`tbody tr[data-row-index="${selectedRowIndex}"]`);
            if (selectedRow) selectedRow.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    function selectActiveRow(index, pin) {
        activeRowIndex = Number.isInteger(index) ? index : null;
        if (pin) {
            pinnedRowIndex = activeRowIndex;
            selectedRowIndex = activeRowIndex;
        }
        const table = document.getElementById('dashTable');
        if (table) {
            table.querySelectorAll('tbody tr[data-row-index]').forEach((tr) => {
                const rowIndex = Number(tr.dataset.rowIndex);
                const active = rowIndex === activeRowIndex;
                const selected = rowIndex === selectedRowIndex;
                tr.classList.toggle('is-active', active);
                tr.classList.toggle('is-selected', selected);
                tr.setAttribute('aria-selected', selected ? 'true' : 'false');
                if (selected) tr.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            });
        }
        if (activeRowIndex == null) hideHoverTip();
    }

    function commitSelection(index, pin) {
        const next = Number.isInteger(index) ? index : null;
        const changed = selectedRowIndex !== next;
        selectedRowIndex = next;
        selectedRowIdFromUrl = '';
        pinnedRowIndex = pin ? next : null;
        activeRowIndex = next;
        selectActiveRow(next, false);
        if (changed) {
            drawChart();
            if (isPerf()) renderPerfTurns();
        }
        scheduleUrlConfigSync();
    }

    function showPointTip(gd, row, rowIndex, clientX, clientY, pinned) {
        const tip = document.getElementById('dashHoverTip');
        if (!tip || !row) return;
        const detail = (row.benchmark_scores || []).map((item) => `${item.source}: ${fmtScore(item.score)}`).join(' · ');
        const bits = isPerf() ? [
            '<strong>' + escapeHtml(row.label || '') + '</strong>',
            'Agent ' + escapeHtml(row.harness || '') + ' · ' + escapeHtml(row.provider || ''),
            escapeHtml(String(row.turns)) + ' turns · ' + escapeHtml((row.sources || []).join(' + ')),
            'Accept ' + escapeHtml(fmtScore(row.score)) + ' · finished ' + escapeHtml(fmtScore(row.success_rate)),
            (row.good_feedback || row.bad_feedback) ? `👍 ${row.good_feedback} · 👎 ${row.bad_feedback}` : '',
            'Median ' + escapeHtml(fmtDur(row.mean_duration_seconds)) + ' · p90 ' + escapeHtml(fmtDur(row.p90_duration_seconds)),
            row.mean_output_tokens != null ? 'Out tokens ' + escapeHtml(fmtCount(row.mean_output_tokens)) + ' / turn' : '',
            row.mean_cost_usd != null ? 'Cost ' + escapeHtml(fmtMoney(row.mean_cost_usd)) + ' / turn' : '',
            pinned ? 'Pinned · click empty space or press Esc to clear' : 'Click point to pin and list its turns',
        ].filter(Boolean) : [
            '<strong>' + escapeHtml(row.label || '') + '</strong>',
            'Provider ' + escapeHtml(row.provider || ''),
            row.harness ? 'Harness ' + escapeHtml(row.harness) : '',
            row.reasoning_effort ? 'Reasoning ' + escapeHtml(row.reasoning_effort) : '',
            row.is_new ? 'NEW' : '',
            'Cost ' + escapeHtml(fmtMoney(row.mean_cost_usd)),
            'Score ' + escapeHtml(fmtScore(row.score)),
            'Time ' + escapeHtml(fmtDur(row.mean_duration_seconds)),
            detail ? escapeHtml(detail) : '',
            pinned ? 'Pinned · click empty space or press Esc to clear' : 'Click point to pin tooltip',
        ].filter(Boolean);
        tip.innerHTML = bits.join('<br>');
        tip.hidden = false;
        tip.classList.add('is-visible');
        placeHoverTip(gd, null, { clientX, clientY });
        if (pinned) commitSelection(rowIndex, true);
        else if (activeRowIndex !== rowIndex) selectActiveRow(rowIndex, false);
    }

    function axisOptions(selected, includeScore) {
        const keys = [];
        if (includeScore) keys.push('score');
        if (payload && Array.isArray(payload.axis_fields)) keys.push.apply(keys, payload.axis_fields);
        else keys.push('mean_cost_usd', 'mean_duration_seconds', 'mean_output_tokens', 'mean_agent_steps');
        if (!includeScore) keys.push('score');
        if (payload && payload.selected_source === 'aggregate') keys.push('benchmark_count', 'score_spread');
        const seen = new Set();
        return keys.filter((k) => {
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        }).filter((k) => (payload.rows || []).some((row) => row[k] != null && Number.isFinite(Number(row[k]))) || k === selected).map((k) => {
            const sel = k === selected ? ' selected' : '';
            const available = (payload.rows || []).some((row) => row[k] != null && Number.isFinite(Number(row[k])));
            const disabled = available ? '' : ' disabled';
            const label = (AXIS_LABELS[k] || k) + (available ? '' : ' (no data)');
            return `<option value="${k}"${sel}${disabled}>${escapeHtml(label)}</option>`;
        }).join('');
    }

    function axisExtent(field) {
        const rows = selectedRows();
        const values = rows.map((row) => row[field] == null ? NaN : Number(row[field])).filter(Number.isFinite);
        if (!values.length) return { min: 0, max: 1 };
        let min = Math.min.apply(null, values);
        let max = Math.max.apply(null, values);
        if (min === max) {
            const pad = Math.max(Math.abs(min) * 0.05, 0.5);
            min -= pad;
            max += pad;
        }
        return { min, max };
    }

    function axisValueLabel(field, value) {
        if (field === 'score' || field === 'success_rate') return fmtScore(value);
        if (field === 'turns') return String(Math.round(value));
        if (field === 'mean_output_tokens' || field === 'mean_total_tokens') return fmtCount(value);
        if (field === 'mean_cost_usd') return fmtMoney(value);
        if (field === 'mean_duration_seconds') return fmtDur(value);
        return Number(value).toFixed(2);
    }

    function axisControlRow(axis, field, includeScore) {
        const extent = axisExtent(field);
        const saved = axisLimits[axis];
        let low = saved ? Math.max(extent.min, Math.min(extent.max, saved.min)) : extent.min;
        let high = saved ? Math.max(extent.min, Math.min(extent.max, saved.max)) : extent.max;
        if (low > high) { low = extent.min; high = extent.max; }
        const step = Math.max((extent.max - extent.min) / 500, 0.000001);
        const id = axis.toUpperCase();
        return `<div class="dash-axis-row" data-axis-row="${axis}">
            <label class="dash-axis-field"><span>${id}</span><select id="dash${id}" aria-label="${id} dimension">${axisOptions(field, includeScore)}</select></label>
            <label class="dash-axis-invert"><input type="checkbox" id="dashInvert${id}" data-axis-invert="${axis}"${invertAxes[axis] ? ' checked' : ''}> Invert</label>
            <label class="dash-axis-slider"><span>Min</span><input type="range" min="${extent.min}" max="${extent.max}" step="${step}" value="${low}" data-axis-limit="${axis}" data-limit-kind="min" aria-label="${id} lower limit" title="${axisValueLabel(field, low)}"></label>
            <label class="dash-axis-slider"><span>Max</span><input type="range" min="${extent.min}" max="${extent.max}" step="${step}" value="${high}" data-axis-limit="${axis}" data-limit-kind="max" aria-label="${id} upper limit" title="${axisValueLabel(field, high)}"></label>
            <output class="dash-axis-limit-value" data-axis-limit-value="${axis}">${escapeHtml(axisValueLabel(field, low))} – ${escapeHtml(axisValueLabel(field, high))}</output>
        </div>`;
    }

    function refreshAxisControls() {
        const toolbar = document.getElementById('dashAxes');
        if (!toolbar) return;
        toolbar.innerHTML = axisControlRow('x', xField, false)
            + axisControlRow('y', yField, true)
            + axisControlRow('z', zField, false);
    }

    function bindAxisControls() {
        const toolbar = document.getElementById('dashAxes');
        if (!toolbar || toolbar.__dashBound) return;
        toolbar.__dashBound = true;
        toolbar.addEventListener('change', (event) => {
            const select = event.target.closest('select[id^="dash"]');
            if (select) {
                const axis = select.id.slice(-1).toLowerCase();
                if (axis === 'x') xField = select.value;
                else if (axis === 'y') yField = select.value;
                else zField = select.value;
                axesUserSet = true;
                axisLimits[axis] = null;
                refreshAxisControls();
                renderTable();
                drawChart();
                scheduleUrlConfigSync();
                return;
            }
            const invert = event.target.closest('[data-axis-invert]');
            if (invert) {
                invertAxes[invert.dataset.axisInvert] = invert.checked;
                drawChart();
                scheduleUrlConfigSync();
            }
        });
        toolbar.addEventListener('input', (event) => {
            const slider = event.target.closest('[data-axis-limit]');
            if (!slider) return;
            const axis = slider.dataset.axisLimit;
            const kind = slider.dataset.limitKind;
            const field = axis === 'x' ? xField : axis === 'y' ? yField : zField;
            const extent = axisExtent(field);
            const limits = axisLimits[axis] || { min: extent.min, max: extent.max };
            const value = Number(slider.value);
            if (kind === 'min') limits.min = Math.min(value, limits.max);
            else limits.max = Math.max(value, limits.min);
            axisLimits[axis] = limits;
            const row = slider.closest('[data-axis-row]');
            const minInput = row.querySelector('[data-limit-kind="min"]');
            const maxInput = row.querySelector('[data-limit-kind="max"]');
            minInput.value = String(limits.min);
            maxInput.value = String(limits.max);
            minInput.title = axisValueLabel(field, limits.min);
            maxInput.title = axisValueLabel(field, limits.max);
            row.querySelector('[data-axis-limit-value]').textContent = axisValueLabel(field, limits.min) + ' – ' + axisValueLabel(field, limits.max);
            drawChart();
            scheduleUrlConfigSync();
        });
    }

    function resetCameraView() {
        controlMode = 'default';
        projectionMode = 'perspective';
        flyKeys.clear();
        flyVel = { x: 0, y: 0, z: 0 };
        flyLastT = 0;
        lookDrag = null;
        plotCamera = defaultCamera();
        stableCam = plotCamera;
        savePrefs();
        syncHint();
        syncWidgetMenuState();
        drawChart();
    }

    function vecLen(v) {
        return Math.hypot(v.x || 0, v.y || 0, v.z || 0);
    }
    function vsub(a, b) { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; }
    function vadd(a, b) { return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }; }
    function vscale(a, s) { return { x: a.x * s, y: a.y * s, z: a.z * s }; }
    function vdot(a, b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
    function vcross(a, b) {
        return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
    }
    function vnorm(a) {
        const n = vecLen(a) || 1;
        return vscale(a, 1 / n);
    }

    function asVec3(v, fallback) {
        const fb = fallback || { x: 0, y: 0, z: 0 };
        if (v == null) return { x: fb.x, y: fb.y, z: fb.z };
        const x = Array.isArray(v) ? v[0] : v.x;
        const y = Array.isArray(v) ? v[1] : v.y;
        const z = Array.isArray(v) ? v[2] : v.z;
        return {
            x: Number.isFinite(x) ? x : fb.x,
            y: Number.isFinite(y) ? y : fb.y,
            z: Number.isFinite(z) ? z : fb.z,
        };
    }

    function freeSanitize(cam) {
        const src = cam && typeof cam === 'object' ? cam : {};
        const prev = plotCamera || {};
        return {
            eye: asVec3(src.eye, prev.eye || { x: 1.55, y: 1.45, z: 1.15 }),
            center: asVec3(src.center, prev.center || { x: 0, y: 0, z: 0 }),
            up: { x: 0, y: 0, z: 1 },
            projection: { type: projectionMode || 'perspective' },
        };
    }

    function sanitizeCamera(cam) {
        if (controlMode === 'free') return freeSanitize(cam);
        const src = cam && typeof cam === 'object' ? cam : {};
        const prev = plotCamera || {};
        const center = asVec3(src.center, prev.center || { x: 0, y: 0, z: 0 });
        const eye = asVec3(src.eye, prev.eye || { x: 1.55, y: 1.45, z: 1.15 });
        const rel = vsub(eye, center);
        const horiz = Math.hypot(rel.x, rel.y);
        const r = vecLen(rel) || 1;
        const minHoriz = Math.max(0.12 * r, 0.08);
        if (horiz < minHoriz) {
            const scale = minHoriz / Math.max(horiz, 1e-6);
            rel.x *= scale;
            rel.y *= scale;
            const nr = vecLen(rel) || r;
            const keep = r / nr;
            rel.x *= keep;
            rel.y *= keep;
            rel.z *= keep;
        }
        const mag = vecLen(rel);
        // Free cam look-distance may grow; never pull the rig back to the origin.
        const minMag = controlMode === 'free' ? 0.08 : 0.35;
        const maxMag = controlMode === 'free' ? 200 : 8;
        if (mag > 0 && (mag < minMag || mag > maxMag)) {
            const s = Math.min(maxMag, Math.max(minMag, mag)) / mag;
            rel.x *= s;
            rel.y *= s;
            rel.z *= s;
        }
        const proj = src.projection && src.projection.type ? src.projection : { type: projectionMode };
        return {
            eye: vadd(center, rel),
            center: center,
            up: { x: 0, y: 0, z: 1 },
            projection: { type: projectionMode || proj.type || 'perspective' },
        };
    }

    function defaultCamera() {
        return sanitizeCamera({
            eye: { x: 1.55, y: 1.45, z: 1.15 },
            center: { x: 0, y: 0, z: 0 },
            up: { x: 0, y: 0, z: 1 },
        });
    }

    function touchPairDist(t0, t1) {
        return Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
    }

    function readLiveCamera(gd) {
        if (controlMode === 'free' && plotCamera) return freeSanitize(plotCamera);
        try {
            const cam = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene.camera;
            if (cam) return sanitizeCamera(cam);
        } catch (_) {}
        return sanitizeCamera(plotCamera || defaultCamera());
    }

    function glScene(gd) {
        try {
            return gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
        } catch (_) {
            return null;
        }
    }

    function enablePlotlyOrbit(gd) {
        const scene = glScene(gd);
        const cam = scene && scene.camera;
        if (!cam) return;
        try {
            if (cam.view && typeof cam.view.setMode === 'function') cam.view.setMode('turntable');
            cam.mode = 'turntable';
            cam.keyBindingMode = 'rotate';
            if (cam.mouseListener) cam.mouseListener.enabled = true;
        } catch (_) {}
    }

    function lockFreeCamController(gd) {
        const scene = glScene(gd);
        const cam = scene && scene.camera;
        if (!cam) return;
        try {
            if (cam.view && typeof cam.view.setMode === 'function') cam.view.setMode('matrix');
            cam.mode = 'matrix';
            cam.keyBindingMode = false;
            if (cam.mouseListener) cam.mouseListener.enabled = false;
        } catch (_) {}
    }

    function writeGlCamera(gd, cam) {
        const scene = glScene(gd);
        if (!scene || !scene.camera) return false;
        lockFreeCamController(gd);
        const eye = [cam.eye.x, cam.eye.y, cam.eye.z];
        const center = [cam.center.x, cam.center.y, cam.center.z];
        const up = [0, 0, 1];
        try {
            const c = scene.camera;
            let ok = false;
            if (c.view && typeof c.view.lookAt === 'function') {
                const t = typeof c.view.lastT === 'function' ? c.view.lastT() : 0;
                c.view.lookAt(t, eye, center, up);
                ok = true;
            }
            if (typeof c.lookAt === 'function') {
                c.lookAt(eye, center, up);
                ok = true;
            }
            if (!ok) return false;
            if (scene.glplot && typeof scene.glplot.redraw === 'function') scene.glplot.redraw();
            if (gd.layout && gd.layout.scene) gd.layout.scene.camera = cam;
            if (gd._fullLayout && gd._fullLayout.scene) gd._fullLayout.scene.camera = cam;
            return true;
        } catch (_) {
            return false;
        }
    }

    function applyCamera(gd, cam) {
        plotCamera = sanitizeCamera(cam);
        if (!pinchLock) stableCam = plotCamera;
        if (!gd || !window.Plotly) return Promise.resolve();
        if (controlMode === 'free') {
            if (camPushRaf) return Promise.resolve();
            camPushRaf = requestAnimationFrame(function () {
                camPushRaf = 0;
                if (!writeGlCamera(gd, plotCamera)) {
                    window.Plotly.relayout(gd, {
                        'scene.camera.eye': plotCamera.eye,
                        'scene.camera.center': plotCamera.center,
                        'scene.camera.up': plotCamera.up,
                    });
                }
                updateAxisLabels(gd);
                scheduleUrlConfigSync();
            });
            return Promise.resolve();
        }
        return window.Plotly.relayout(gd, { 'scene.camera': plotCamera }).then(() => {
            updateAxisLabels(gd);
            scheduleUrlConfigSync();
        });
    }

    function camLooksFlipped(a, b) {
        if (!a || !b) return false;
        const ea = vnorm(vsub(a.eye, a.center));
        const eb = vnorm(vsub(b.eye, b.center));
        return vdot(ea, eb) < 0.2;
    }

    function scheduleAxisLabelUpdate(gd) {
        if (!gd || axisLabelRaf) return;
        axisLabelRaf = requestAnimationFrame(() => {
            axisLabelRaf = 0;
            updateAxisLabels(gd);
            if (axisLabelTracking) scheduleAxisLabelUpdate(gd);
        });
    }

    function bindChartInput(gd) {
        if (!gd || gd.__dashInputBound) return;
        gd.__dashInputBound = true;
        const ptrs = new Map();
        let pinch = null;
        let pinchRaf = 0;
        let pinchCam = null;

        function pts() {
            return Array.from(ptrs.values());
        }

        function pairDist(a, b) {
            return Math.hypot(a.x - b.x, a.y - b.y);
        }

        function beginPinch() {
            pinchLock = true;
            lookDrag = null;
            const cam = sanitizeCamera(stableCam || plotCamera || readLiveCamera(gd));
            plotCamera = cam;
            const p = pts();
            pinch = {
                dist: p.length >= 2 ? Math.max(pairDist(p[0], p[1]), 24) : 24,
                cam: cam,
            };
            applyCamera(gd, cam);
        }

        function onPinchMove() {
            const p = pts();
            if (!pinch || p.length < 2) return;
            const dist = Math.max(pairDist(p[0], p[1]), 1);
            const scale = pinch.dist / dist;
            const cam = pinch.cam;
            if (controlMode === 'free') {
                const look = vsub(cam.center, cam.eye);
                const delta = vscale(look, 1 - scale);
                pinchCam = { eye: vadd(cam.eye, delta), center: vadd(cam.center, delta) };
            } else {
                pinchCam = {
                    eye: {
                        x: cam.center.x + (cam.eye.x - cam.center.x) * scale,
                        y: cam.center.y + (cam.eye.y - cam.center.y) * scale,
                        z: cam.center.z + (cam.eye.z - cam.center.z) * scale,
                    },
                    center: cam.center,
                };
            }
            if (pinchRaf) return;
            pinchRaf = requestAnimationFrame(function () {
                pinchRaf = 0;
                if (pinchCam) applyCamera(gd, pinchCam).then(() => updateAxisLabels(gd));
            });
        }

        function endPointers() {
            if (ptrs.size > 0) return;
            pinch = null;
            pinchCam = null;
            lookDrag = null;
            pinchLock = false;
            stableCam = sanitizeCamera(plotCamera || readLiveCamera(gd));
        }

        gd.addEventListener('pointerdown', function (e) {
            axisLabelTracking = true;
            scheduleAxisLabelUpdate(gd);
            if (e.pointerType === 'touch') {
                ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
                if (ptrs.size >= 2) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    beginPinch();
                    return;
                }
                if (!pinchLock) {
                    stableCam = sanitizeCamera(plotCamera || readLiveCamera(gd));
                }
                if (controlMode === 'free' && ptrs.size === 1 && !pinchLock) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    flyVel = { x: 0, y: 0, z: 0 };
                    lookDrag = { x: e.clientX, y: e.clientY, cam: freeSanitize(plotCamera || defaultCamera()), id: e.pointerId };
                }
                return;
            }
            if (controlMode !== 'free') return;
            if (e.button !== 0 && e.button !== 2) return;
            if (e.target && e.target.closest && e.target.closest('.legend, .dash-widget-menu, button, a')) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            flyVel = { x: 0, y: 0, z: 0 };
            lookDrag = { x: e.clientX, y: e.clientY, cam: freeSanitize(plotCamera || defaultCamera()), id: e.pointerId };
            try { gd.setPointerCapture(e.pointerId); } catch (_) {}
        }, { capture: true, passive: false });

        gd.addEventListener('pointermove', function (e) {
            if (axisLabelTracking) scheduleAxisLabelUpdate(gd);
            if (e.pointerType === 'touch' && ptrs.has(e.pointerId)) {
                ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
            }
            if (pinchLock || ptrs.size >= 2) {
                e.preventDefault();
                e.stopImmediatePropagation();
                onPinchMove();
                return;
            }
            if (controlMode === 'free' && lookDrag && e.pointerId === lookDrag.id) {
                e.preventDefault();
                e.stopImmediatePropagation();
                applyLook(gd, lookDrag, e.clientX - lookDrag.x, e.clientY - lookDrag.y);
                lookDrag.x = e.clientX;
                lookDrag.y = e.clientY;
            }
        }, { capture: true, passive: false });

        function onPtrUp(e) {
            if (e.pointerType === 'touch') ptrs.delete(e.pointerId);
            axisLabelTracking = e.pointerType === 'touch' && ptrs.size > 0;
            if (lookDrag && lookDrag.id === e.pointerId) {
                e.preventDefault();
                e.stopImmediatePropagation();
                lookDrag = null;
            }
            if (pinchLock) {
                e.preventDefault();
                e.stopImmediatePropagation();
            }
            endPointers();
            scheduleAxisLabelUpdate(gd);
        }
        gd.addEventListener('pointerup', onPtrUp, { capture: true });
        gd.addEventListener('pointercancel', onPtrUp, { capture: true });

        gd.addEventListener('touchstart', function (e) {
            if (e.touches.length >= 2 || pinchLock) {
                e.preventDefault();
                e.stopImmediatePropagation();
                if (e.touches.length >= 2 && !pinch) beginPinch();
            }
        }, { capture: true, passive: false });
        gd.addEventListener('touchmove', function (e) {
            if (e.touches.length >= 2 || pinchLock) {
                e.preventDefault();
                e.stopImmediatePropagation();
            }
        }, { capture: true, passive: false });
        gd.addEventListener('touchend', function (e) {
            if (pinchLock || e.touches.length > 0) {
                e.preventDefault();
                e.stopImmediatePropagation();
            }
        }, { capture: true, passive: false });

        gd.addEventListener('contextmenu', function (e) {
            if (controlMode === 'free' || pinchLock) e.preventDefault();
        });
        gd.addEventListener('wheel', function (e) {
            if (controlMode !== 'free') return;
            e.preventDefault();
            e.stopPropagation();
            camSpeed = Math.min(12, Math.max(0.15, camSpeed * (e.deltaY > 0 ? 0.9 : 1.12)));
            savePrefs();
            showSpeedHud();
        }, { passive: false, capture: true });
    }

    function rotateAround(v, axis, ang) {
        const c = Math.cos(ang);
        const s = Math.sin(ang);
        const k = vnorm(axis);
        const kxv = vcross(k, v);
        return vadd(vadd(vscale(v, c), vscale(kxv, s)), vscale(k, vdot(k, v) * (1 - c)));
    }

    function applyLook(gd, drag, dx, dy) {
        const cam = drag.cam;
        const look = vsub(cam.center, cam.eye);
        const dist = vecLen(look) || 1;
        let forward = vnorm(look);
        const yaw = -dx * 0.005;
        const pitch = -dy * 0.005;
        forward = vnorm(rotateAround(forward, { x: 0, y: 0, z: 1 }, yaw));
        let right = vcross(forward, { x: 0, y: 0, z: 1 });
        if (vecLen(right) < 1e-4) right = vcross(forward, { x: 0, y: 1, z: 0 });
        right = vnorm(right);
        const pitched = rotateAround(forward, right, pitch);
        const zClamp = 0.96;
        if (Math.abs(pitched.z) < zClamp) forward = vnorm(pitched);
        else forward = vnorm({ x: forward.x, y: forward.y, z: Math.sign(forward.z || 1) * zClamp });
        const nextCam = freeSanitize({
            eye: { x: cam.eye.x, y: cam.eye.y, z: cam.eye.z },
            center: vadd(cam.eye, vscale(forward, dist)),
        });
        drag.cam = nextCam;
        applyCamera(gd, nextCam).then(() => updateAxisLabels(gd));
    }

    function showSpeedHud() {
        const hud = document.getElementById('dashSpeedHud');
        if (!hud) return;
        hud.textContent = 'Speed ' + camSpeed.toFixed(1) + '×';
        hud.classList.add('is-on');
        clearTimeout(speedHudTimer);
        speedHudTimer = setTimeout(() => hud.classList.remove('is-on'), 2200);
    }

    function flyHasMoveInput() {
        return flyKeys.has('w') || flyKeys.has('a') || flyKeys.has('s') || flyKeys.has('d')
            || flyKeys.has('q') || flyKeys.has('e');
    }

    function flyTick(now) {
        flyRaf = 0;
        if (controlMode !== 'free') {
            flyVel = { x: 0, y: 0, z: 0 };
            flyLastT = 0;
            return;
        }
        now = typeof now === 'number' ? now : performance.now();
        let dt = flyLastT ? (now - flyLastT) / 1000 : 1 / 60;
        flyLastT = now;
        if (dt > 0.05) dt = 0.05;
        if (dt < 0.001) dt = 0.001;

        const gd = lastPlotGd;
        const sprint = flyKeys.has('shift') ? 2.6 : 1;
        const cruise = 2.65 * camSpeed * sprint;
        let desired = { x: 0, y: 0, z: 0 };

        if (gd && window.Plotly && flyHasMoveInput()) {
            const cam = freeSanitize((lookDrag && lookDrag.cam) || plotCamera || defaultCamera());
            const forward = vnorm(vsub(cam.center, cam.eye));
            let right = vcross(forward, { x: 0, y: 0, z: 1 });
            if (vecLen(right) < 1e-5) right = { x: 1, y: 0, z: 0 };
            else right = vnorm(right);
            const up = { x: 0, y: 0, z: 1 };
            let d = { x: 0, y: 0, z: 0 };
            if (flyKeys.has('w')) d = vadd(d, forward);
            if (flyKeys.has('s')) d = vadd(d, vscale(forward, -1));
            if (flyKeys.has('d')) d = vadd(d, right);
            if (flyKeys.has('a')) d = vadd(d, vscale(right, -1));
            if (flyKeys.has('e')) d = vadd(d, up);
            if (flyKeys.has('q')) d = vadd(d, vscale(up, -1));
            if (vecLen(d) > 1e-6) desired = vscale(vnorm(d), cruise);
        }

        const tau = vecLen(desired) > 0.02 ? 0.10 : 0.17;
        const blend = 1 - Math.exp(-dt / tau);
        flyVel = vadd(flyVel, vscale(vsub(desired, flyVel), blend));

        const moving = vecLen(flyVel) > 0.012;
        if (!moving && vecLen(desired) < 0.02) {
            flyVel = { x: 0, y: 0, z: 0 };
            flyLastT = 0;
            return;
        }

        if (gd && window.Plotly && moving) {
            const cam = freeSanitize((lookDrag && lookDrag.cam) || plotCamera || defaultCamera());
            const step = vscale(flyVel, dt);
            const next = { eye: vadd(cam.eye, step), center: vadd(cam.center, step) };
            if (lookDrag) lookDrag.cam = freeSanitize(next);
            applyCamera(gd, next).then(() => updateAxisLabels(gd));
        }
        flyRaf = requestAnimationFrame(flyTick);
    }

    function keyInField(e) {
        const t = e.target;
        return t && t.closest && t.closest('input, select, textarea, [contenteditable="true"]');
    }

    window.addEventListener('keydown', (e) => {
        if (controlMode !== 'free' || keyInField(e)) return;
        const k = e.key.toLowerCase();
        if ('wasdqe'.indexOf(k) >= 0 || e.key === 'Shift') {
            e.preventDefault();
            flyKeys.add(k === 'shift' ? 'shift' : k);
            if (!flyRaf) flyRaf = requestAnimationFrame(flyTick);
        }
    });
    window.addEventListener('keyup', (e) => {
        const k = e.key.toLowerCase();
        flyKeys.delete(k === 'shift' ? 'shift' : k);
        if (e.key === 'Shift') flyKeys.delete('shift');
        if (controlMode === 'free' && !flyRaf && vecLen(flyVel) > 0.012) {
            flyRaf = requestAnimationFrame(flyTick);
        }
    });

    function mat4MulVec4(m, x, y, z, w) {
        return {
            x: m[0] * x + m[4] * y + m[8] * z + m[12] * w,
            y: m[1] * x + m[5] * y + m[9] * z + m[13] * w,
            z: m[2] * x + m[6] * y + m[10] * z + m[14] * w,
            w: m[3] * x + m[7] * y + m[11] * z + m[15] * w,
        };
    }

    function projectGlPoint(gd, x, y, z) {
        const scene = glScene(gd);
        const glplot = scene && scene.glplot;
        const cam = scene && scene.camera;
        if (!glplot || !cam) return null;
        try {
            if (typeof cam.tick === 'function') cam.tick();
        } catch (_) {}
        const params = glplot.cameraParams;
        const model = params && params.model;
        const proj = params && params.projection;
        const view = (params && params.view) || cam.matrix;
        if (!model || !proj || !view) return null;
        let v = mat4MulVec4(model, x, y, z, 1);
        v = mat4MulVec4(view, v.x, v.y, v.z, v.w);
        v = mat4MulVec4(proj, v.x, v.y, v.z, v.w);
        if (!Number.isFinite(v.w) || v.w <= 1e-6) return null;
        const ndcX = v.x / v.w;
        const ndcY = v.y / v.w;
        const ndcZ = v.z / v.w;
        if (ndcX < -1.4 || ndcX > 1.4 || ndcY < -1.4 || ndcY > 1.4) return null;
        const canvas = glplot.canvas || gd.querySelector('canvas');
        const wrap = document.getElementById('dashWidget');
        if (!canvas || !wrap) return null;
        const cw = canvas.clientWidth || gd.clientWidth || 1;
        const ch = canvas.clientHeight || gd.clientHeight || 1;
        const cr = canvas.getBoundingClientRect();
        const wr = wrap.getBoundingClientRect();
        return {
            x: (ndcX * 0.5 + 0.5) * cw + (cr.left - wr.left),
            y: (-ndcY * 0.5 + 0.5) * ch + (cr.top - wr.top),
            z: ndcZ,
        };
    }

    function sceneBounds(gd) {
        try {
            const b = glScene(gd).glplot.bounds;
            if (b && b[0] && b[1] && Number.isFinite(b[0][0]) && Number.isFinite(b[1][0])) {
                return { lo: b[0], hi: b[1] };
            }
        } catch (_) {}
        const scene = gd._fullLayout && gd._fullLayout.scene;
        if (!scene) return null;
        function rng(ax) {
            const r = ax && ax.range;
            if (r && r.length === 2 && Number.isFinite(r[0]) && Number.isFinite(r[1])) return r;
            return [-1, 1];
        }
        const xr = rng(scene.xaxis);
        const yr = rng(scene.yaxis);
        const zr = rng(scene.zaxis);
        return { lo: [xr[0], yr[0], zr[0]], hi: [xr[1], yr[1], zr[1]] };
    }

    function updateAxisLabels(gd) {
        const wrap = document.getElementById('dashWidget');
        if (!wrap || !gd) return;
        const box = sceneBounds(gd);
        if (!box) {
            wrap.querySelectorAll('.dash-axis-float').forEach((el) => el.classList.add('is-hidden'));
            return;
        }
        const lo = box.lo;
        const hi = box.hi;
        const origin = {
            x: 0.5 * (lo[0] + hi[0]),
            y: 0.5 * (lo[1] + hi[1]),
            z: 0.5 * (lo[2] + hi[2]),
        };
        const oscr = projectGlPoint(gd, origin.x, origin.y, origin.z);
        const w = wrap.clientWidth || 1;
        const h = wrap.clientHeight || 1;
        const fields = { x: xField, y: yField, z: zField };
        const corners = {
            x: [[lo[1], hi[1]], [lo[2], hi[2]]],
            y: [[lo[0], hi[0]], [lo[2], hi[2]]],
            z: [[lo[0], hi[0]], [lo[1], hi[1]]],
        };
        ['x', 'y', 'z'].forEach((axis) => {
            const el = wrap.querySelector('.dash-axis-float[data-axis="' + axis + '"]');
            if (!el) return;
            let best = null;
            const pairA = corners[axis][0];
            const pairB = corners[axis][1];
            pairA.forEach((a) => {
                pairB.forEach((b) => {
                    let mid;
                    if (axis === 'x') mid = { x: origin.x, y: a, z: b };
                    else if (axis === 'y') mid = { x: a, y: origin.y, z: b };
                    else mid = { x: a, y: b, z: origin.z };
                    const scr = projectGlPoint(gd, mid.x, mid.y, mid.z);
                    if (!scr) return;
                    const edge = axis + ':' + a + ',' + b;
                    const score = scr.y + Math.abs(scr.x - w / 2) * 0.04 - scr.z * 40;
                    if (!best || score > best.score) best = { edge, scr, score, mid };
                });
            });
            if (!best) {
                el.classList.add('is-hidden');
                return;
            }
            if (oscr) {
                let dx = best.scr.x - oscr.x;
                let dy = best.scr.y - oscr.y;
                const len = Math.hypot(dx, dy) || 1;
                best.scr.x += (dx / len) * 16;
                best.scr.y += (dy / len) * 16;
            }
            const label = fields[axis] === 'score' && payload && payload.source && payload.source.score_name
                ? payload.source.score_name + ' (%)'
                : (AXIS_LABELS[fields[axis]] || fields[axis]);
            const shownLabel = label + (invertAxes[axis] ? ' ↔' : '');
            const onScreen = best.scr.x > 4 && best.scr.x < w - 4 && best.scr.y > 4 && best.scr.y < h - 4;
            el.textContent = shownLabel;
            const place = () => {
                el.style.left = best.scr.x + 'px';
                el.style.top = best.scr.y + 'px';
                el.classList.toggle('is-hidden', !onScreen);
            };
            if (axisEdge[axis] && axisEdge[axis] !== best.edge) {
                el.classList.add('is-hidden');
                clearTimeout(axisFadeTimer[axis]);
                axisFadeTimer[axis] = setTimeout(place, 180);
            } else if (axisEdge[axis] === best.edge) {
                el.style.left = best.scr.x + 'px';
                el.style.top = best.scr.y + 'px';
                el.classList.toggle('is-hidden', !onScreen);
            } else {
                place();
            }
            axisEdge[axis] = best.edge;
        });
    }

    function hideHoverTip() {
        const tip = document.getElementById('dashHoverTip');
        if (!tip) return;
        tip.hidden = true;
        tip.classList.remove('is-visible');
    }

    function placeHoverTip(gd, pt, nativeEvt) {
        const tip = document.getElementById('dashHoverTip');
        if (!tip) return;
        let cx = null;
        let cy = null;
        const ev = nativeEvt || {};
        const stub = Number.isFinite(ev.clientX) && Number.isFinite(ev.clientY)
            && !(ev.clientX === 0 && ev.clientY === 0);
        if (stub) {
            cx = ev.clientX;
            cy = ev.clientY;
        } else if (lastPointer.ok) {
            cx = lastPointer.x;
            cy = lastPointer.y;
        } else if (pt) {
            const scr = projectGlPoint(gd, pt.x, pt.y, pt.z);
            const wrap = document.getElementById('dashWidget');
            if (scr && wrap) {
                const wr = wrap.getBoundingClientRect();
                cx = wr.left + scr.x;
                cy = wr.top + scr.y;
            }
        }
        if (cx == null) return;
        tip.style.left = (cx + 14) + 'px';
        tip.style.top = (cy + 14) + 'px';
        const tw = tip.offsetWidth || 180;
        const th = tip.offsetHeight || 48;
        const maxX = window.innerWidth - tw - 8;
        const maxY = window.innerHeight - th - 8;
        if (cx + 14 > maxX) tip.style.left = Math.max(8, cx - tw - 12) + 'px';
        if (cy + 14 > maxY) tip.style.top = Math.max(8, cy - th - 12) + 'px';
    }

    function bindHoverTip(gd) {
        if (!gd || gd.__dashHoverBound) return;
        gd.__dashHoverBound = true;
        gd.addEventListener('pointermove', (e) => {
            lastPointer = { x: e.clientX, y: e.clientY, ok: true };
        }, { passive: true });
        let lastPointClickAt = 0;
        const rowForPoint = (pt) => {
            const index = Number(pt && pt.customdata && pt.customdata[0]);
            const row = Number.isInteger(index) ? (payload.rows || [])[index] : null;
            return row ? { row, index } : null;
        };
        const clearSelection = () => {
            pinnedRowIndex = null;
            selectedRowIndex = null;
            selectedRowIdFromUrl = '';
            selectActiveRow(null, false);
            drawChart();
            scheduleUrlConfigSync();
            hideHoverTip();
        };
        gd.on('plotly_hover', (ev) => {
            if (pinnedRowIndex != null) return;
            const hit = rowForPoint(ev && ev.points && ev.points[0]);
            if (!hit) return;
            const native = ev && ev.event;
            const x = native && Number.isFinite(native.clientX) ? native.clientX : lastPointer.x;
            const y = native && Number.isFinite(native.clientY) ? native.clientY : lastPointer.y;
            showPointTip(gd, hit.row, hit.index, x, y, false);
        });
        gd.on('plotly_unhover', () => {
            if (pinnedRowIndex == null) {
                hideHoverTip();
                selectActiveRow(selectedRowIndex, false);
            }
        });
        gd.on('plotly_click', (ev) => {
            const hit = rowForPoint(ev && ev.points && ev.points[0]);
            if (!hit) return;
            lastPointClickAt = Date.now();
            if (pinnedRowIndex === hit.index) {
                pinnedRowIndex = null;
                showPointTip(gd, hit.row, hit.index, lastPointer.x, lastPointer.y, false);
            } else {
                pinnedRowIndex = hit.index;
                showPointTip(gd, hit.row, hit.index, lastPointer.x, lastPointer.y, true);
            }
        });
        gd.addEventListener('click', (e) => {
            // Plotly emits plotly_click before the native click. A native click
            // without it is an empty-space click and clears a pinned tooltip.
            setTimeout(() => {
                if (Date.now() - lastPointClickAt < 80) return;
                if (pinnedRowIndex != null) clearSelection();
            }, 0);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && (pinnedRowIndex != null || selectedRowIndex != null)) {
                clearSelection();
            }
        });
    }

    function sceneAxis(colors, axis) {
        const limits = axisLimits[axis];
        const config = {
            title: { text: '' },
            tickfont: { color: colors.muted, size: 10 },
            gridcolor: colors.grid,
            zerolinecolor: colors.grid,
            backgroundcolor: 'rgba(0,0,0,0)',
            showbackground: false,
            autorange: limits ? false : (invertAxes[axis] ? 'reversed' : true),
        };
        const field = axis === 'x' ? xField : axis === 'y' ? yField : zField;
        const logAxis = field === 'turns';
        if (logAxis) config.type = 'log';
        if (limits) {
            const lo = logAxis ? Math.log10(Math.max(limits.min, 0.5)) : limits.min;
            const hi = logAxis ? Math.log10(Math.max(limits.max, 0.5)) : limits.max;
            config.range = invertAxes[axis] ? [hi, lo] : [lo, hi];
        }
        return config;
    }

    function drawChart() {
        const el = document.getElementById('dashChart');
        if (!el) return;
        const rows = selectedRows().filter((r) => r[xField] != null && r[yField] != null && r[zField] != null);
        if (!rows.length) {
            el.innerHTML = '<p class="dash-empty">No plottable rows for these filters / axes.</p>';
            return;
        }
        if (!window.Plotly) {
            el.innerHTML = '<p class="dash-loading">Loading 3D graph…</p>';
            if (window.CuttleOptionalCdn && typeof window.CuttleOptionalCdn.loadPlotlyExtras === 'function') {
                window.CuttleOptionalCdn.loadPlotlyExtras();
            }
            return;
        }
        const colors = themeColors();
        const byProvider = new Map();
        rows.forEach((r) => {
            const key = r.provider || 'unknown';
            if (!byProvider.has(key)) byProvider.set(key, []);
            byProvider.get(key).push(r);
        });
        const colorsByProvider = ['#636efa', '#ef553b', '#00cc96', '#ab63fa', '#ffa15a', '#19d3f3', '#ff6692', '#b6e880', '#ff97ff', '#fecb52'];
        const selectedKey = selectedEffortKey();
        const pointTraces = Array.from(byProvider.entries()).map(([provider, items], providerIndex) => ({
            type: 'scatter3d',
            mode: 'markers',
            name: provider,
            x: items.map((r) => r[xField]),
            y: items.map((r) => r[yField]),
            z: items.map((r) => r[zField]),
            text: items.map((r) => r.label),
            customdata: items.map((r) => ([
                (payload.rows || []).indexOf(r),
                r.harness || '',
                r.reasoning_effort || '',
                r.is_new ? 'NEW' : '',
                fmtMoney(r.mean_cost_usd),
                fmtScore(r.score),
                fmtDur(r.mean_duration_seconds),
            ])),
            hoverinfo: 'none',
            marker: {
                size: items.map((r) => {
                    const idx = (payload.rows || []).indexOf(r);
                    if (idx === selectedRowIndex) return 17;
                    if (selectedKey && effortModelKey(r) === selectedKey) return 12;
                    if (isPerf()) return Math.min(16, 6 + 2 * Math.log2(Math.max(1, r.turns || 1)) / 1.5);
                    return r.is_new ? 10 : 8;
                }),
                color: items.map((r) => {
                    const idx = (payload.rows || []).indexOf(r);
                    if (idx === selectedRowIndex) return '#fbbf24';
                    if (selectedKey && effortModelKey(r) === selectedKey) return '#38bdf8';
                    return colorsByProvider[providerIndex % colorsByProvider.length];
                }),
                opacity: 0.88,
                line: { width: 1, color: colors.text },
            },
        }));
        // Put the translucent mesh behind the point traces so Plotly's 3D
        // hover and click picking reaches points inside the optimal zone.
        const traces = sweetSpotTrace(rows).concat(effortConnectionTraces(rows), pointTraces);
        const layout = {
            autosize: true,
            margin: { l: 0, r: 0, t: 0, b: 0 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: colors.text, size: 11 },
            showlegend: true,
            hovermode: 'closest',
            hoverdistance: 28,
            legend: {
                font: { color: colors.muted, size: 11 },
                bgcolor: 'rgba(0,0,0,0.25)',
                borderwidth: 0,
                x: 0.02, y: 0.98, xanchor: 'left', yanchor: 'top',
            },
            scene: {
                bgcolor: 'rgba(0,0,0,0)',
                xaxis: sceneAxis(colors, 'x'),
                yaxis: sceneAxis(colors, 'y'),
                zaxis: sceneAxis(colors, 'z'),
                camera: sanitizeCamera(plotCamera || defaultCamera()),
                aspectmode: 'cube',
                dragmode: controlMode === 'free' ? false : 'turntable',
            },
            uirevision: 'mb3d-' + controlMode + '-' + projectionMode,
        };
        const config = {
            responsive: true,
            displaylogo: false,
            displayModeBar: false,
            scrollZoom: controlMode === 'default',
            doubleClick: false,
        };
        if (!el.data) el.innerHTML = '';
        window.Plotly.react(el, traces, layout, config).then((gd) => {
            lastPlotGd = gd;
            if (controlMode === 'free') {
                lockFreeCamController(gd);
                writeGlCamera(gd, sanitizeCamera(plotCamera || defaultCamera()));
            } else {
                enablePlotlyOrbit(gd);
            }
            bindChartInput(gd);
            bindHoverTip(gd);
            updateAxisLabels(gd);
            if (gd && !gd.__dashCameraBound) {
                gd.__dashCameraBound = true;
                function onCamEvent(ev, dragging) {
                    const cam = ev && (ev['scene.camera'] || (ev.scene && ev.scene.camera));
                    if (cam) {
                        if (controlMode === 'free') {
                            updateAxisLabels(gd);
                            scheduleUrlConfigSync();
                            return;
                        }
                        const clean = sanitizeCamera(cam);
                        if (pinchLock && camLooksFlipped(stableCam || plotCamera, clean)) {
                            window.Plotly.relayout(gd, { 'scene.camera': sanitizeCamera(stableCam || plotCamera) });
                            return;
                        }
                        if (!pinchLock) {
                            plotCamera = clean;
                            stableCam = clean;
                        }
                        const up = cam.up || {};
                        const rolled = Math.abs(up.x || 0) > 0.02 || Math.abs(up.y || 0) > 0.02 || Math.abs((up.z || 1) - 1) > 0.02;
                        if (rolled && controlMode === 'default' && !pinchLock && !dragging) {
                            window.Plotly.relayout(gd, { 'scene.camera': clean });
                        }
                    }
                    updateAxisLabels(gd);
                    if (!dragging) scheduleUrlConfigSync();
                }
                gd.on('plotly_relayout', (ev) => onCamEvent(ev, false));
                gd.on('plotly_relayouting', (ev) => onCamEvent(ev, true));
            }
        }).catch((err) => {
            el.innerHTML = '<p class="dash-error">' + escapeHtml((err && err.message) || String(err)) + '</p>';
        });
    }

    function fmtAgoUnix(sec) {
        if (!sec) return '—';
        return fmtWhen(new Date(Number(sec) * 1000).toISOString());
    }

    function renderCuttlePerformance() {
        const stats = payload.stats || {};
        const filters = payload.filters || {};
        const rows = payload.rows || [];
        applyAxisLabels();
        const perfFields = new Set(payload.axis_fields || []);
        const usable = (field) => perfFields.has(field) && rows.some((row) => row[field] != null && Number.isFinite(Number(row[field])));
        if (!axesUserSet || !usable(xField)) xField = PERF_DEFAULT_AXES.x;
        if (!axesUserSet || !usable(yField)) yField = PERF_DEFAULT_AXES.y;
        if (!axesUserSet || !usable(zField)) zField = PERF_DEFAULT_AXES.z;
        if (!perfSortChosen) {
            sortKey = 'turns';
            sortDir = -1;
            perfSortChosen = true;
        }
        restoreSelectionFromUrl();

        const miss = stats.miss_kinds || {};
        const missBits = Object.keys(miss).map((k) => `${escapeHtml(k)} ${miss[k]}`).join(' · ');
        const finished = stats.turns ? fmtScore((stats.success_rate || 0) * 100) : '—';
        const noFeedback = !(stats.good_feedback || stats.bad_feedback || stats.labeled);
        const jev = payload.jev || {};
        let jevNote = '';
        if (jev.running) {
            jevNote = `<div class="dash-note">Jev is labeling your turns in the background (${Number(jev.labeled || 0)} done, ${Number(jev.pending || 0)} to go). <button type="button" class="dash-link-btn" data-page-action="refresh">Refresh</button> to see the new accept scores.</div>`;
        } else if (jev.errors && jev.last_error) {
            jevNote = `<div class="dash-note">Jev labeling stopped after errors: ${escapeHtml(jev.last_error)}</div>`;
        } else if (noFeedback && rows.length) {
            jevNote = `<div class="dash-note">No thumbs or Jev labels yet, so <strong>Accept score</strong> only tells you whether a run finished without error, and most runs do. Use 👍 / 👎 under replies in chat, or <em>Label with Jev</em>, to make it mean “was the answer good”.</div>`;
        }
        const days = Number(payload.selected_days || 0);
        root.innerHTML = `
            <div class="dash-page-bar">
                <button type="button" class="dash-back" id="dashBack">← All dashboards</button>
                <div class="compact-page-title">
                    <h1>My Cuttle Performance</h1>
                    <p>Your own turns — pinned agents and the router — one point per agent, model, and reasoning effort.</p>
                </div>
                <div class="dash-menu-wrap">
                    ${hamburgerBtn('dashPageMenuBtn', 'Page menu')}
                    <div class="dash-menu" id="dashPageMenu" hidden>
                        <button type="button" class="dash-menu-item" data-page-action="refresh">Refresh data</button>
                        <button type="button" class="dash-menu-item" data-page-action="label">Label with Jev</button>
                    </div>
                </div>
            </div>
            <div class="dash-source-picker">
                <label for="dashPerfSource">Turns</label>
                <select id="dashPerfSource">${(payload.available_sources || []).map((item) =>
                    `<option value="${escapeAttr(item.id)}"${item.id === payload.selected_source ? ' selected' : ''}>${escapeHtml(item.name)}</option>`
                ).join('')}</select>
                <label for="dashPerfDays">Window</label>
                <select id="dashPerfDays">${(payload.available_days || []).map((d) =>
                    `<option value="${d}"${Number(d) === days ? ' selected' : ''}>${d ? 'Last ' + d + ' days' : 'All time'}</option>`
                ).join('')}</select>
            </div>
            <div class="dash-meta">
                <span>Pinned turns: <strong>${stats.pinned_turns || 0}</strong></span>
                <span>Router turns: <strong>${stats.router_turns || 0}</strong></span>
                <span>Oldest: ${escapeHtml(fmtAgoUnix(stats.first_recorded_at))}</span>
                <span>Latest: ${escapeHtml(fmtAgoUnix(stats.last_recorded_at))}</span>
            </div>
            ${jevNote}
            <div class="dash-stats">
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.configs || 0}</div><div class="dash-stat-label">Configs</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.turns || 0}</div><div class="dash-stat-label">Turns</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${finished}</div><div class="dash-stat-label">Finished</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.good_feedback || 0}/${stats.bad_feedback || 0}</div><div class="dash-stat-label">Thumbs +/-</div></div>
                <div class="dash-stat-card"><div class="dash-stat-value">${stats.accepted_labels || 0}/${stats.rejected_labels || 0}</div><div class="dash-stat-label">Jev accept/reject</div></div>
            </div>
            <p class="dash-chart-hint">Transport ${stats.transport || 0} · task fail ${stats.task_fail || 0} · cancelled ${stats.cancelled || 0}${missBits ? ' · Jev: ' + missBits : ''}. Cost is only known for ${stats.with_cost || 0} turns (Cursor doesn't report it).</p>
            ${axesToolbarHtml()}
            <div class="dash-chip-filters" id="dashChips">
                ${chipRow('Provider', filters.providers || [], 'providers')}
                ${chipRow('Reasoning', filters.reasoning_efforts || [], 'efforts')}
                ${chipRow('Agent', filters.harnesses || [], 'harnesses')}
            </div>
            ${chartWidgetHtml()}
            <div class="jobs-table-scroll jobs-table-scroll--capped" data-jobs-table="1">
                <table class="hist-table" id="dashTable"></table>
            </div>
            <h2 class="dash-section-title" id="dashTurnsTitle">Recent turns</h2>
            <div class="jobs-table-scroll jobs-table-scroll--capped" data-jobs-table="1">
                <table class="hist-table" id="dashTurns"></table>
            </div>`;

        document.getElementById('dashBack').addEventListener('click', () => navigateTo(''));
        document.getElementById('dashPerfSource').addEventListener('change', (e) => switchDataset({ source: e.target.value }));
        document.getElementById('dashPerfDays').addEventListener('change', (e) => switchDataset({ days: e.target.value }));
        bindMenuToggle('dashPageMenuBtn', 'dashPageMenu');
        document.getElementById('dashPageMenu').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-page-action]');
            if (!btn) return;
            closeMenus();
            const act = btn.getAttribute('data-page-action');
            if (act === 'refresh') loadDashboard(false);
            if (act === 'label') loadDashboard(true);
        });
        root.querySelectorAll('.dash-note [data-page-action="refresh"]').forEach((btn) =>
            btn.addEventListener('click', () => loadDashboard(false)));
        bindChartWidget();
        renderPerfTurns();
    }

    function renderPerfTurns() {
        const table = document.getElementById('dashTurns');
        if (!table) return;
        const selected = selectedRowIndex == null ? null : (payload.rows || [])[selectedRowIndex];
        const all = payload.turns || [];
        const turns = (selected ? all.filter((t) => t.config_id === selected.id) : all).slice(0, 200);
        const title = document.getElementById('dashTurnsTitle');
        if (title) title.textContent = selected ? 'Recent turns · ' + selected.label + ' (' + selected.harness + ')' : 'Recent turns';
        const head = `<thead><tr>
            <th>When</th><th>Model</th><th>Agent</th><th>Via</th><th>Result</th>
            <th>Feedback</th><th>Time</th><th>Out tokens</th><th>Cost</th><th>Chat</th>
        </tr></thead>`;
        const body = turns.map((t) => {
            const result = t.failure_kind && t.failure_kind !== 'none' ? t.failure_kind : 'ok';
            let fb = '—';
            if (t.user_feedback === 'good') fb = '👍';
            else if (t.user_feedback === 'bad') fb = '👎';
            else if (t.label_source === 'jev') {
                fb = t.jev_accepted === true ? 'Jev: accept'
                    : t.jev_accepted === false ? 'Jev: reject · ' + (t.miss_kind || 'model')
                    : 'Jev: unsure';
            }
            const chat = /^\d+$/.test(String(t.session_id || '')) ? 'CH-' + String(t.session_id).padStart(6, '0') : '—';
            return `<tr>
                <td>${escapeHtml(fmtAgoUnix(t.recorded_at))}</td>
                <td class="jobs-cell-wrap">${escapeHtml(t.model + (t.reasoning_effort && t.reasoning_effort !== 'unspecified' ? ' · ' + t.reasoning_effort : ''))}</td>
                <td>${escapeHtml(t.agent)}</td>
                <td>${escapeHtml(t.source === 'pinned' ? 'pinned' : 'router')}</td>
                <td${t.reason ? ` title="${escapeAttr(t.reason)}"` : ''}>${escapeHtml(result)}</td>
                <td${t.accept_noul != null ? ` title="Jev accept ${escapeAttr(String(t.accept_noul))}"` : ''}>${escapeHtml(fb)}</td>
                <td>${fmtDur(t.duration_seconds)}</td>
                <td>${fmtCount(t.output_tokens)}</td>
                <td>${t.cost_usd == null ? '—' : fmtMoney(t.cost_usd)}</td>
                <td>${escapeHtml(chat)}</td>
            </tr>`;
        }).join('');
        const empty = selected
            ? 'No recent turns for this config in the loaded window.'
            : 'No turns recorded yet. Send a message to any agent, then refresh.';
        table.innerHTML = head + '<tbody>' + (body || `<tr><td colspan="10">${empty}</td></tr>`) + '</tbody>';
    }

    function fmtCompact(n) {
        if (n == null || Number.isNaN(n)) return '—';
        const v = Number(n);
        const abs = Math.abs(v);
        if (abs >= 1e9) return (v / 1e9).toFixed(abs >= 1e10 ? 1 : 2) + 'B';
        if (abs >= 1e6) return (v / 1e6).toFixed(abs >= 1e7 ? 1 : 2) + 'M';
        if (abs >= 1e3) return (v / 1e3).toFixed(abs >= 1e4 ? 1 : 2) + 'K';
        return String(Math.round(v));
    }

    function fmtUsageValue(unit, n) {
        if (unit === 'usd') return fmtMoney(n);
        if (unit === 'tokens') return fmtCompact(n);
        if (unit === 'hours') return (n >= 10 ? Number(n).toFixed(0) : Number(n || 0).toFixed(1)) + ' h';
        return Number(n || 0).toLocaleString();
    }

    function usageBucketLabels() {
        const buckets = payload.buckets || [];
        const interval = payload.bucket_interval;
        const years = new Set(buckets.map((iso) => iso.slice(0, 4)));
        return buckets.map((iso) => {
            const [y, m, d] = iso.split('-').map(Number);
            const dt = new Date(y, m - 1, d);
            if (interval === 'month') return dt.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
            const opts = { month: 'short', day: 'numeric' };
            if (years.size > 1) opts.year = '2-digit';
            const s = dt.toLocaleDateString(undefined, opts);
            return interval === 'week' ? 'Wk ' + s : s;
        });
    }

    function usageGroupColor(index) {
        if (index < USAGE_COLORS.length) return USAGE_COLORS[index];
        const hue = Math.round((index * 137.508) % 360);
        const light = 55 + ((index % 3) - 1) * 10;
        return `hsl(${hue}, 65%, ${light}%)`;
    }

    function renderCuttleUsage() {
        const stats = payload.stats || {};
        const totals = payload.totals || {};
        const metrics = payload.metrics || [];
        const groups = payload.groups || [];
        const view = new URLSearchParams(window.location.search).get('view') === 'total' ? 'total' : 'time';
        usageHidden = new Set(Array.from(usageHidden).filter((key) => groups.some((g) => g.key === key)));
        const range = payload.selected_range;
        const pills = (payload.available_ranges || []).map((r) =>
            `<button type="button" class="dash-chip ${r.id === range ? 'is-on' : ''}" data-usage-range="${escapeAttr(r.id)}">${escapeHtml(r.label)}</button>`
        ).join('');
        const select = (id, items, selected) => `<select id="${id}">${(items || []).map((item) =>
            `<option value="${escapeAttr(item.id)}"${item.id === selected ? ' selected' : ''}>${escapeHtml(item.name)}</option>`
        ).join('')}</select>`;
        const statCards = metrics.map((m) =>
            `<div class="dash-stat-card"><div class="dash-stat-value">${escapeHtml(fmtUsageValue(m.unit, totals[m.id]))}</div><div class="dash-stat-label">${escapeHtml(m.label)}</div></div>`
        ).join('');
        const coverage = {
            cost_usd: `${stats.with_cost || 0} of ${stats.turns || 0} turns report cost`,
            total_tokens: `${stats.with_tokens || 0} of ${stats.turns || 0} turns report tokens`,
            input_tokens: `${stats.with_tokens || 0} of ${stats.turns || 0} turns report tokens`,
            output_tokens: `${stats.with_tokens || 0} of ${stats.turns || 0} turns report tokens`,
        };
        const chartCards = metrics.map((m) => `
            <div class="dash-usage-card">
                <div class="dash-usage-card-head">
                    <h3>${escapeHtml(m.label)}</h3>
                    ${coverage[m.id] ? `<span>${escapeHtml(coverage[m.id])}</span>` : ''}
                </div>
                <div class="dash-usage-chart" data-usage-metric="${escapeAttr(m.id)}"></div>
            </div>`).join('');
        const intervalName = (payload.available_intervals || []).find((i) => i.id === payload.bucket_interval);
        root.innerHTML = `
            <div class="dash-page-bar">
                <button type="button" class="dash-back" id="dashBack">← All dashboards</button>
                <div class="compact-page-title">
                    <h1>Cuttle Usage</h1>
                    <p>Cost, tokens, turns and agent time from your own turns, by model or harness.</p>
                </div>
                <div class="dash-menu-wrap">
                    ${hamburgerBtn('dashPageMenuBtn', 'Page menu')}
                    <div class="dash-menu" id="dashPageMenu" hidden>
                        <button type="button" class="dash-menu-item" data-page-action="refresh">Refresh data</button>
                    </div>
                </div>
            </div>
            <div class="dash-usage-range" id="dashUsageRange">
                ${pills}
                <label class="dash-usage-dates">
                    <input type="date" id="dashUsageStart" value="${escapeAttr(payload.start || '')}" aria-label="Start date">
                    <span>to</span>
                    <input type="date" id="dashUsageEnd" value="${escapeAttr(payload.end || '')}" aria-label="End date">
                </label>
            </div>
            <div class="dash-source-picker">
                <label for="dashUsageGroup">Group by</label>
                ${select('dashUsageGroup', payload.available_group_by, payload.selected_group_by)}
                <label for="dashUsageInterval">Interval</label>
                ${select('dashUsageInterval', payload.available_intervals, payload.selected_interval)}
                <label for="dashUsageSource">Turns</label>
                ${select('dashUsageSource', payload.available_sources, payload.selected_source)}
                <label for="dashUsageView">Chart</label>
                <select id="dashUsageView">
                    <option value="time"${view === 'time' ? ' selected' : ''}>Over time (stacked columns)</option>
                    <option value="total"${view === 'total' ? ' selected' : ''}>Breakdown (totals by group)</option>
                </select>
            </div>
            <div class="dash-meta">
                <span>${escapeHtml(payload.start || '')} → ${escapeHtml(payload.end || '')}</span>
                <span>${(payload.buckets || []).length} × ${escapeHtml(((intervalName && intervalName.name) || payload.bucket_interval || '').toLowerCase())}</span>
                <span>Cancelled: <strong>${stats.cancelled || 0}</strong></span>
            </div>
            <div class="dash-stats dash-stats--usage">${statCards}</div>
            <p class="dash-chart-hint">${escapeHtml(payload.message || '')}</p>
            <div class="dash-chip-row dash-usage-legend" id="dashUsageLegend">${groups.map((g, i) =>
                `<button type="button" class="dash-chip ${usageHidden.has(g.key) ? '' : 'is-on'}" data-usage-group="${escapeAttr(g.key)}"${g.members ? ` title="${escapeAttr(g.members.join(', '))}"` : ''}><span class="dash-swatch" style="background:${usageGroupColor(i)}"></span>${escapeHtml(g.label)}</button>`
            ).join('')}</div>
            ${stats.turns ? `<div class="dash-usage-grid" data-view="${view}">${chartCards}</div>` : '<p class="dash-empty">No turns in this range.</p>'}
            <h2 class="dash-section-title">Breakdown</h2>
            <div class="jobs-table-scroll" data-jobs-table="1">
                <table class="hist-table" id="dashUsageTable"></table>
            </div>`;

        document.getElementById('dashBack').addEventListener('click', () => navigateTo(''));
        bindMenuToggle('dashPageMenuBtn', 'dashPageMenu');
        document.getElementById('dashPageMenu').addEventListener('click', (e) => {
            if (!e.target.closest('[data-page-action="refresh"]')) return;
            closeMenus();
            loadDashboard(false);
        });
        document.getElementById('dashUsageRange').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-usage-range]');
            if (btn) switchDataset({ range: btn.getAttribute('data-usage-range'), start: null, end: null });
        });
        const onDates = () => {
            const start = document.getElementById('dashUsageStart').value;
            const end = document.getElementById('dashUsageEnd').value;
            if (start || end) switchDataset({ range: 'custom', start, end });
        };
        document.getElementById('dashUsageStart').addEventListener('change', onDates);
        document.getElementById('dashUsageEnd').addEventListener('change', onDates);
        document.getElementById('dashUsageGroup').addEventListener('change', (e) => {
            usageHidden = new Set();
            switchDataset({ group: e.target.value });
        });
        document.getElementById('dashUsageInterval').addEventListener('change', (e) => switchDataset({ interval: e.target.value }));
        document.getElementById('dashUsageSource').addEventListener('change', (e) => switchDataset({ source: e.target.value }));
        document.getElementById('dashUsageView').addEventListener('change', (e) => {
            const url = new URL(window.location.href);
            url.searchParams.set('view', e.target.value);
            window.history.replaceState(window.history.state, '', url.toString());
            const grid = root.querySelector('.dash-usage-grid');
            if (grid) grid.setAttribute('data-view', e.target.value);
            drawUsageCharts();
        });
        document.getElementById('dashUsageLegend').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-usage-group]');
            if (!btn) return;
            const key = btn.getAttribute('data-usage-group');
            if (usageHidden.has(key)) usageHidden.delete(key);
            else usageHidden.add(key);
            btn.classList.toggle('is-on', !usageHidden.has(key));
            drawUsageCharts();
            renderUsageTable();
        });
        renderUsageTable();
        drawUsageCharts();
    }

    function renderUsageTable() {
        const table = document.getElementById('dashUsageTable');
        if (!table) return;
        const metrics = payload.metrics || [];
        const groups = (payload.groups || []).map((g, i) => ({ g, i })).filter(({ g }) => !usageHidden.has(g.key));
        const tokenTotal = groups.reduce((sum, { g }) => sum + (g.totals.total_tokens || 0), 0);
        const head = `<thead><tr><th>${escapeHtml(payload.selected_group_by === 'harness' ? 'Harness' : 'Model')}</th>${
            metrics.map((m) => `<th>${escapeHtml(m.label)}</th>`).join('')
        }<th>Token share</th></tr></thead>`;
        const body = groups.map(({ g, i }) => `<tr>
            <td class="jobs-cell-wrap"><span class="dash-swatch" style="background:${usageGroupColor(i)}"></span>${escapeHtml(g.label)}</td>
            ${metrics.map((m) => `<td>${escapeHtml(fmtUsageValue(m.unit, g.totals[m.id]))}</td>`).join('')}
            <td>${tokenTotal ? ((100 * (g.totals.total_tokens || 0)) / tokenTotal).toFixed(1) + '%' : '—'}</td>
        </tr>`).join('');
        table.innerHTML = head + '<tbody>' + (body || `<tr><td colspan="${metrics.length + 2}">No turns in this range.</td></tr>`) + '</tbody>';
    }

    function drawUsageCharts() {
        const cells = Array.from(root.querySelectorAll('.dash-usage-chart'));
        if (!cells.length) return;
        if (!window.Plotly) {
            cells.forEach((el) => { el.innerHTML = '<p class="dash-loading">Loading chart…</p>'; });
            if (window.CuttleOptionalCdn && typeof window.CuttleOptionalCdn.loadPlotlyExtras === 'function') {
                window.CuttleOptionalCdn.loadPlotlyExtras();
            }
            return;
        }
        const colors = themeColors();
        const byId = {};
        (payload.metrics || []).forEach((m) => { byId[m.id] = m; });
        const labels = usageBucketLabels();
        const breakdown = new URLSearchParams(window.location.search).get('view') === 'total';
        const visible = (payload.groups || []).map((g, i) => ({ g, color: usageGroupColor(i) }))
            .filter(({ g }) => !usageHidden.has(g.key));
        cells.forEach((el) => {
            const metric = byId[el.getAttribute('data-usage-metric')];
            if (!metric) return;
            const fmt = USAGE_FORMATS[metric.unit] || USAGE_FORMATS.count;
            const suffix = fmt.suffix || '';
            const valueAxis = {
                tickformat: fmt.tick, ticksuffix: suffix, gridcolor: colors.grid, zerolinecolor: colors.grid,
                color: colors.muted, rangemode: 'tozero', automargin: true,
            };
            let traces;
            let layout;
            if (breakdown) {
                const rows = visible.slice().sort((a, b) => (a.g.totals[metric.id] || 0) - (b.g.totals[metric.id] || 0));
                traces = [{
                    type: 'bar',
                    orientation: 'h',
                    x: rows.map(({ g }) => g.totals[metric.id] || 0),
                    y: rows.map(({ g }) => g.label),
                    marker: { color: rows.map(({ color }) => color) },
                    hovertemplate: `%{y}: %{x:${fmt.hover}}${suffix}<extra></extra>`,
                }];
                layout = {
                    xaxis: valueAxis,
                    yaxis: { type: 'category', color: colors.muted, automargin: true },
                    hovermode: 'closest',
                };
            } else {
                traces = visible.map(({ g, color }) => ({
                    type: 'bar',
                    name: g.label,
                    x: labels,
                    y: g.series[metric.id],
                    marker: { color },
                    hovertemplate: `%{fullData.name}: %{y:${fmt.hover}}${suffix}<extra></extra>`,
                }));
                layout = {
                    barmode: 'stack',
                    xaxis: { type: 'category', color: colors.muted, automargin: true, tickangle: labels.length > 14 ? -45 : 0 },
                    yaxis: valueAxis,
                    hovermode: 'x unified',
                };
            }
            Object.assign(layout, {
                autosize: true,
                height: el.clientHeight || 260,
                margin: { l: 8, r: 12, t: 8, b: 8 },
                paper_bgcolor: 'rgba(0,0,0,0)',
                plot_bgcolor: 'rgba(0,0,0,0)',
                font: { color: colors.text, size: 11 },
                showlegend: false,
                bargap: 0.18,
                hoverlabel: { bgcolor: 'rgba(15,23,42,0.92)', bordercolor: colors.grid, font: { color: '#e2e8f0' } },
            });
            if (!el.data) el.innerHTML = '';
            window.Plotly.react(el, traces, layout, { responsive: true, displaylogo: false, displayModeBar: false })
                .catch((err) => { el.innerHTML = '<p class="dash-error">' + escapeHtml((err && err.message) || String(err)) + '</p>'; });
        });
    }

    function renderSoon() {
        const msg = (payload && payload.message) || 'This dashboard is not wired yet.';
        root.innerHTML = `
            <div class="dash-page-bar">
                <button type="button" class="dash-back" id="dashBack">← All dashboards</button>
                <div class="compact-page-title">
                    <h1>${escapeHtml((payload && payload.title) || 'Dashboard')}</h1>
                </div>
            </div>
            <div class="dash-soon-body">${escapeHtml(msg)}</div>`;
        document.getElementById('dashBack').addEventListener('click', () => navigateTo(''));
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function escapeAttr(s) { return escapeHtml(s); }

    async function loadHub() {
        catalog = (await fetchJson('/api/dashboards')).dashboards || [];
        renderHub();
    }

    async function loadDashboard(force) {
        const id = dashIdFromUrl();
        root.innerHTML = '<p class="dash-loading">Loading…</p>';
        const params = new URLSearchParams();
        const urlParams = new URLSearchParams(window.location.search);
        if (force) params.set('refresh', '1');
        if (id === 'model-benchmarks') params.set('source', urlParams.get('source') || 'deepswe');
        if (id === PERF_ID) {
            params.set('source', urlParams.get('source') || 'all');
            params.set('days', urlParams.get('days') || '30');
        }
        if (id === USAGE_ID) {
            ['range', 'start', 'end', 'group', 'interval', 'source'].forEach((key) => {
                if (urlParams.get(key)) params.set(key, urlParams.get(key));
            });
            params.set('tz', String(new Date().getTimezoneOffset()));
        }
        const q = params.toString() ? '?' + params.toString() : '';
        payload = await fetchJson('/api/dashboards/' + encodeURIComponent(id) + q);
        if (id === 'model-benchmarks') renderModelBenchmarks();
        else if (id === 'cuttle-performance') renderCuttlePerformance();
        else if (id === USAGE_ID) renderCuttleUsage();
        else renderSoon();
    }

    async function boot() {
        try {
            const id = dashIdFromUrl();
            if (!id) await loadHub();
            else await loadDashboard(false);
        } catch (err) {
            root.innerHTML = '<p class="dash-error">' + escapeHtml(err.message || String(err)) + '</p>';
        }
    }

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.dash-menu-wrap, .dash-widget .dash-hamburger, .dash-menu')) closeMenus();
    });
    document.addEventListener('fullscreenchange', () => {
        const wrap = document.getElementById('dashWidget');
        if (wrap) wrap.classList.toggle('is-fs', !!document.fullscreenElement);
        setTimeout(schedulePlotResize, 80);
    });

    window.addEventListener('cuttle-plotly-ready', () => {
        if (payload && payload.id === USAGE_ID) {
            if (window.Plotly) drawUsageCharts();
            else root.querySelectorAll('.dash-usage-chart').forEach((el) => {
                el.innerHTML = '<p class="dash-error">Chart library could not load (offline?). The breakdown table below still works.</p>';
            });
            return;
        }
        if (!payload || (dashIdFromUrl() !== 'model-benchmarks' && dashIdFromUrl() !== PERF_ID)) return;
        if (window.Plotly) {
            drawChart();
            return;
        }
        const el = document.getElementById('dashChart');
        if (el) el.innerHTML = '<p class="dash-error">3D graph library could not load (offline?). The table below still works.</p>';
    });
    window.addEventListener('resize', () => {
        schedulePlotResize();
    });

    function resizePlotToWidget() {
        const gd = lastPlotGd;
        if (!gd || !window.Plotly || !gd.data) return;
        const camera = readLiveCamera(gd);
        try {
            const resized = window.Plotly.Plots.resize(gd);
            Promise.resolve(resized).then(() => {
                if (lastPlotGd !== gd) return;
                if (camera) applyCamera(gd, camera);
                requestAnimationFrame(() => updateAxisLabels(gd));
            }, () => {
                if (lastPlotGd === gd) updateAxisLabels(gd);
            });
        } catch (_) {
            updateAxisLabels(gd);
        }
    }

    function schedulePlotResize() {
        if (resizeRaf) cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => {
            resizeRaf = 0;
            resizePlotToWidget();
        });
    }

    function observeWidgetSize() {
        const widget = document.getElementById('dashWidget');
        if (!widget || !window.ResizeObserver) return;
        if (widgetResizeObserver) widgetResizeObserver.disconnect();
        widgetResizeObserver = new ResizeObserver(schedulePlotResize);
        widgetResizeObserver.observe(widget);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
