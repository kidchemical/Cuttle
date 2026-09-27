/* Cuttle Router editor — compact vertical config UI.
   Edits settings.json → agent_router (config + use_cases) via /api/router/*. */
(() => {
'use strict';

const TASK_TYPES = [
    ['basic_ask', 'basic ask'], ['coding', 'coding'], ['debugging', 'debugging'],
    ['architecture', 'architecture'], ['research', 'research'], ['other', 'other'],
];
const DIFFICULTIES = [['low', 'low'], ['medium', 'medium'], ['high', 'high']];

const state = {
    config: null,   // {mode…, default_target, escalation_target, fallbacks[]}
    useCases: [],   // use-case blocks
    options: null,  // {modes, api_models, agents, agent_models}
    health: null,   // {metrics, report, demotions}
    rage: { enabled: true, investigator: { enabled: true, agent: 'jev', model: 'jev-latest', timeout_s: 30 } },
    dirty: false,
};

// ── helpers ──────────────────────────────────────────────────────────────────
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(v) {
    return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(msg, isError) {
    if (typeof showToast === 'function') { showToast(msg, isError ? 'error' : 'info'); return; }
    const t = $('#re-toast');
    t.textContent = msg;
    t.classList.toggle('error', !!isError);
    t.hidden = false;
    clearTimeout(toast._h);
    toast._h = setTimeout(() => { t.hidden = true; }, 3200);
}

function markDirty() {
    state.dirty = true;
    $('#dirty-chip').hidden = false;
}

async function api(url, opts) {
    const res = await fetch(url, opts);
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-json */ }
    if (!res.ok || (body && body.success === false)) {
        throw new Error((body && body.error) || `HTTP ${res.status}`);
    }
    return body;
}

function agentModelKey(t) { return `${(t.agent || '').toLowerCase()}:${t.model || ''}`; }

// ── target picker (agent select + model dropdown) ────────────────────────────
function modelsFor(agent) {
    return (state.options && state.options.agent_models && state.options.agent_models[agent]) || [];
}

function modelSelect(agent, current, emptyLabel) {
    const msel = document.createElement('select');
    msel.className = 're-input model';
    const rebuild = () => {
        const models = modelsFor(msel.dataset.agent);
        const cur = msel.dataset.current || '';
        msel.title = '';
        msel.innerHTML =
            `<option value="">${esc(emptyLabel)}</option>` +
            models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('') +
            (cur && !models.includes(cur) ? `<option value="${esc(cur)}">${esc(cur)}</option>` : '');
        msel.value = cur || '';
        msel.disabled = !msel.dataset.agent;
        if (msel.value) msel.title = msel.value;
    };
    msel.setAgent = (agentId) => {
        msel.dataset.agent = agentId || '';
        rebuild();
    };
    msel.dataset.current = current || '';
    msel.setAgent(agent);
    return msel;
}

function targetPicker(target, { allowNone = false, noneLabel = '(none)', defaultLabel = '(default)' } = {}) {
    const wrap = document.createElement('div');
    wrap.className = 're-target';

    const sel = document.createElement('select');
    sel.className = 're-input agent';
    const agents = (state.options && state.options.agents) || [];
    sel.innerHTML =
        (allowNone ? `<option value="">${esc(noneLabel)}</option>` : '') +
        agents.map((a) => `<option value="${esc(a.id)}">${esc(a.label || a.id)}</option>`).join('') +
        (target && target.agent && !agents.some((a) => a.id === target.agent)
            ? `<option value="${esc(target.agent)}">${esc(target.agent)}</option>` : '');
    sel.value = (target && target.agent) || (allowNone ? '' : (agents[0] && agents[0].id) || '');

    const msel = modelSelect(sel.value, (target && target.model) || '', defaultLabel);
    sel.addEventListener('change', () => {
        msel.setAgent(sel.value);
        wrap.dispatchEvent(new Event('target-change', { bubbles: true }));
    });
    msel.addEventListener('change', () => {
        if (msel.value) msel.title = msel.value;
        wrap.dispatchEvent(new Event('target-change', { bubbles: true }));
    });

    wrap.getValue = () => (sel.value ? { agent: sel.value, model: msel.value } : null);
    wrap.setValue = (t) => {
        sel.value = (t && t.agent) || (allowNone ? '' : sel.value);
        msel.dataset.current = (t && t.model) || '';
        msel.setAgent(sel.value);
    };
    wrap.appendChild(sel);
    wrap.appendChild(msel);
    return wrap;
}

// ── core routing ─────────────────────────────────────────────────────────────
function renderProviderFields() {
    const mode = $('#router-mode').value;
    $$('#core-card [data-provider]').forEach((el) => {
        el.hidden = el.dataset.provider !== mode;
    });
    $('#mode-chip').textContent = mode === 'off' ? 'router off' : `brain: ${mode}`;
    $('#mode-chip').classList.toggle('off', mode === 'off');
}

function renderFallbacks() {
    const list = $('#fallback-list');
    list.innerHTML = '';
    (state.config.fallbacks || []).forEach((fb, idx) => {
        const row = document.createElement('div');
        row.className = 're-fallback-row';
        row.innerHTML = `<span class="re-index">${idx + 1}.</span>`;
        const picker = targetPicker(fb);
        picker.addEventListener('target-change', () => {
            state.config.fallbacks[idx] = picker.getValue() || { agent: '', model: '' };
            markDirty();
        });
        const del = document.createElement('button');
        del.className = 're-btn small danger';
        del.textContent = '✕';
        del.title = 'Remove fallback';
        del.addEventListener('click', () => {
            state.config.fallbacks.splice(idx, 1);
            markDirty();
            renderFallbacks();
        });
        row.appendChild(picker);
        row.appendChild(del);
        list.appendChild(row);
    });
    if (!(state.config.fallbacks || []).length) {
        list.innerHTML = '<span class="re-hint">No fallbacks configured.</span>';
    }
}

function renderCore() {
    const cfg = state.config;
    const apiSel = $('#router-api-model');
    if (apiSel && state.options && Array.isArray(state.options.api_models)) {
        const models = state.options.api_models;
        const cur = cfg.api_model || '';
        apiSel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('')
            + (cur && !models.includes(cur) ? `<option value="${esc(cur)}">${esc(cur)}</option>` : '');
        apiSel.value = cur;
    }
    $('#router-mode').value = cfg.mode;
    $('#router-local-endpoint').value = cfg.local_endpoint || '';
    $('#router-local-model').value = cfg.local_model || '';
    $('#router-agent-id').value = cfg.agent_id || '';
    $('#router-agent-model').value = cfg.agent_model || '';
    renderProviderFields();

    $$('#core-card .re-row [data-role]').forEach((slot) => {
        const role = slot.dataset.role;
        slot.innerHTML = '';
        const picker = targetPicker(cfg[role]);
        picker.addEventListener('target-change', () => {
            cfg[role] = picker.getValue() || cfg[role];
            markDirty();
        });
        slot.appendChild(picker);
    });
    renderFallbacks();
}

// ── use cases ────────────────────────────────────────────────────────────────
function criteriaChips(uc, field, choices) {
    const row = document.createElement('div');
    row.className = 're-chip-row';
    row.innerHTML = `<span class="re-chip-label">${field === 'task_types' ? 'Task types' : 'Difficulty'}</span>`;
    const active = new Set(uc.criteria[field] || []);
    choices.forEach(([value, label]) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 're-chip' + (active.has(value) ? ' active' : '');
        chip.textContent = label;
        chip.addEventListener('click', () => {
            const set = new Set(uc.criteria[field] || []);
            if (set.has(value)) set.delete(value); else set.add(value);
            uc.criteria[field] = [...set];
            chip.classList.toggle('active', set.has(value));
            markDirty();
        });
        row.appendChild(chip);
    });
    return row;
}

function useCaseCard(uc, index) {
    const card = document.createElement('div');
    card.className = 're-uc' + (uc.enabled ? '' : ' disabled');
    card.dataset.index = String(index);

    const header = document.createElement('div');
    header.className = 're-uc-header';
    header.innerHTML = `
        <span class="caret">▼</span>
        <input class="re-uc-name" value="${esc(uc.name)}" title="Use case name">
        <input class="re-input re-priority" type="number" step="1" value="${Number(uc.priority) || 0}"
               title="Priority — lower number matches first">
        <span class="re-badge ${uc.enabled ? 'on' : ''}">${uc.enabled ? 'enabled' : 'disabled'}</span>
        <button class="re-btn small danger re-uc-del" title="Delete use case">🗑</button>`;
    card.appendChild(header);

    const body = document.createElement('div');
    body.className = 're-uc-body';

    // description
    const desc = document.createElement('input');
    desc.className = 're-input re-uc-desc';
    desc.placeholder = 'What kind of work is this? (optional)';
    desc.value = uc.description || '';
    desc.addEventListener('input', () => { uc.description = desc.value; markDirty(); });
    body.appendChild(desc);

    // criteria
    body.appendChild(criteriaChips(uc, 'task_types', TASK_TYPES));
    body.appendChild(criteriaChips(uc, 'difficulties', DIFFICULTIES));

    const ccRow = document.createElement('div');
    ccRow.className = 're-chip-row';
    ccRow.innerHTML = `<span class="re-chip-label">Code changes</span>`;
    const ccSel = document.createElement('select');
    ccSel.className = 're-input re-select-3way';    ccSel.innerHTML = `
        <option value="any">any</option>
        <option value="yes">requests code changes</option>
        <option value="no">no code changes</option>`;
    ccSel.value = uc.criteria.code_changes === true ? 'yes' : uc.criteria.code_changes === false ? 'no' : 'any';
    ccSel.addEventListener('change', () => {
        uc.criteria.code_changes = ccSel.value === 'yes' ? true : ccSel.value === 'no' ? false : null;
        markDirty();
    });
    ccRow.appendChild(ccSel);
    body.appendChild(ccRow);

    const kwRow = document.createElement('div');
    kwRow.className = 're-chip-row';
    kwRow.innerHTML = `<span class="re-chip-label">Keywords</span>`;
    const kw = document.createElement('input');
    kw.className = 're-input re-keywords';
    kw.placeholder = 'comma-separated substrings — any match qualifies (empty = any)';
    kw.value = (uc.criteria.keywords || []).join(', ');
    kw.addEventListener('input', () => {
        uc.criteria.keywords = kw.value.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
        markDirty();
    });
    kwRow.appendChild(kw);
    body.appendChild(kwRow);

    // routing
    const routing = document.createElement('div');
    routing.className = 're-uc-routing';
    const r = uc.routing;

    const mkRow = (labelText, control, onChange) => {
        const lab = document.createElement('span');
        lab.className = 're-chip-label';
        lab.textContent = labelText;
        routing.appendChild(lab);
        routing.appendChild(control);
        if (onChange) onChange();
    };

    const mkPickerRows = (getList, { defaultLabel = '(default)', emptyHint, addDefault, numbered = false }) => {
        const wrap = document.createElement('div');
        wrap.className = 're-target-list';
        const render = () => {
            wrap.innerHTML = '';
            const list = getList();
            if (!(list || []).length) {
                const hint = document.createElement('span');
                hint.className = 're-hint';
                hint.textContent = emptyHint;
                wrap.appendChild(hint);
                return;
            }
            list.forEach((t, idx) => {
                const row = document.createElement('div');
                row.className = 're-fallback-row';
                if (numbered) {
                    const num = document.createElement('span');
                    num.className = 're-index';
                    num.textContent = `${idx + 1}.`;
                    num.title = idx === 0 ? 'Runs the task first' : `Tried ${idx === 1 ? 'on task failure' : 'next'} if earlier targets fail`;
                    row.appendChild(num);
                }
                const picker = targetPicker(t, { defaultLabel });
                picker.addEventListener('target-change', () => {
                    list[idx] = picker.getValue() || { agent: '', model: '' };
                    markDirty();
                });
                const del = document.createElement('button');
                del.className = 're-btn small danger';
                del.textContent = '✕';
                del.addEventListener('click', () => {
                    list.splice(idx, 1);
                    markDirty();
                    render();
                });
                row.appendChild(picker);
                row.appendChild(del);
                wrap.appendChild(row);
            });
        };
        const add = document.createElement('button');
        add.className = 're-btn small';
        add.textContent = '+ Add';
        add.addEventListener('click', () => {
            const list = getList();
            list.push({ ...addDefault });
            markDirty();
            render();
        });
        const holder = document.createElement('div');
        holder.className = 're-fallbacks';
        holder.appendChild(wrap);
        holder.appendChild(add);
        render();
        return holder;
    };

    // Target chain — first entry runs the task; the rest are tried in order
    // when earlier ones fail (escalation + any number of fallback layers).
    mkRow(
        'Target chain',
        mkPickerRows(
            () => (r.targets = r.targets || []),
            {
                numbered: true,
                emptyHint: 'Empty — the routing brain decides the target.',
                addDefault: { agent: 'cursor', model: 'auto' },
            },
        ),
    );

    const chainHint = document.createElement('p');
    chainHint.className = 're-hint';
    chainHint.textContent =
        'First target runs the task; the rest are tried in order when earlier ones fail. ' +
        'An empty chain leaves the choice to the routing brain.';
    body.appendChild(chainHint);

    mkRow(
        'Never use',
        mkPickerRows(
            () => (r.never_use = r.never_use || []),
            {
                defaultLabel: '(whole agent)',
                emptyHint: 'No exclusions.',
                addDefault: { agent: 'hermes', model: '' },
            },
        ),
    );
    body.appendChild(routing);

    // footer: enable toggle
    const footer = document.createElement('div');
    footer.className = 're-uc-footer';
    const toggle = document.createElement('button');
    toggle.className = 're-btn small';
    toggle.textContent = uc.enabled ? 'Disable' : 'Enable';
    toggle.addEventListener('click', () => {
        uc.enabled = !uc.enabled;
        card.classList.toggle('disabled', !uc.enabled);
        const badge = header.querySelector('.re-badge');
        badge.textContent = uc.enabled ? 'enabled' : 'disabled';
        badge.classList.toggle('on', uc.enabled);
        toggle.textContent = uc.enabled ? 'Disable' : 'Enable';
        markDirty();
    });
    footer.appendChild(toggle);
    body.appendChild(footer);

    card.appendChild(body);

    // header events
    header.addEventListener('click', (e) => {
        if (e.target.closest('input, button')) return;
        card.classList.toggle('collapsed');
    });
    header.querySelector('.re-uc-name').addEventListener('input', (e) => {
        uc.name = e.target.value;
        markDirty();
    });
    header.querySelector('.re-priority').addEventListener('input', (e) => {
        uc.priority = parseInt(e.target.value, 10) || 0;
        markDirty();
    });
    header.querySelector('.re-uc-del').addEventListener('click', () => {
        if (!confirm(`Delete use case "${uc.name}"?`)) return;
        state.useCases.splice(index, 1);
        markDirty();
        renderUseCases();
    });

    return card;
}

function renderUseCases() {
    const list = $('#usecase-list');
    list.innerHTML = '';
    // Display in complexity order (priority ascending; storage order breaks ties).
    const sorted = state.useCases
        .map((uc, i) => [uc, i])
        .sort((a, b) => (Number(a[0].priority) || 0) - (Number(b[0].priority) || 0) || a[1] - b[1]);
    sorted.forEach(([uc, i]) => list.appendChild(useCaseCard(uc, i)));
}

// ── health ───────────────────────────────────────────────────────────────────
function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderHealth() {
    const h = state.health;
    if (!h) return;
    const report = (h.report && h.report.report) || [];
    const demotions = h.demotions || (h.report && h.report.demotions) || {};
    const metrics = h.metrics || [];
    const byKey = new Map(metrics.map((m) => [`${m.target_agent}:${m.target_model || ''}`, m]));

    const keys = new Set([
        ...report.map((r) => r.key),
        ...Object.keys(demotions),
        ...byKey.keys(),
    ]);

    const driftByKey = new Map(report.map((r) => [r.key, r]));
    const tbody = $('#health-table tbody');
    tbody.innerHTML = '';

    let flagged = 0;
    [...keys].sort().forEach((key) => {
        const m = byKey.get(key) || {};
        const dr = driftByKey.get(key) || {};
        const dem = demotions[key];
        const attempts = Number(m.attempts || 0);
        const successes = Number(m.successes || 0);
        const pct = attempts ? Math.round((successes / attempts) * 100) : null;

        const tr = document.createElement('tr');
        const [agent, ...rest] = key.split(':');
        const model = rest.join(':');
        tr.innerHTML = `
            <td><strong>${esc(agent)}</strong>${model ? ` / ${esc(model)}` : ' / <em>default</em>'}</td>
            <td class="num">${attempts || '–'}</td>
            <td class="num">${pct == null ? '–' : pct + '%'}</td>
            <td class="num">${attempts ? Number(m.task_failures || 0) : '–'}</td>
            <td class="num">${attempts ? Number(m.transport_failures || 0) : '–'}</td>
            <td class="num">${m.avg_latency_ms != null ? Math.round(m.avg_latency_ms) : '–'}</td>
            <td class="num">${attempts ? `+${Number(m.good_feedback || 0)} / −${Number(m.bad_feedback || 0)}` : '–'}</td>
            <td></td>`;

        const statusCell = tr.lastElementChild;
        const status = document.createElement('span');
        const detail = document.createElement('span');
        detail.className = 're-status-detail';

        if (dem) {
            flagged++;
            status.className = 're-status demoted';
            status.textContent = `DEMOTED until ${fmtTime(dem.until)}`;
            detail.textContent = dem.reason || '';
            const clear = document.createElement('button');
            clear.className = 're-btn small';
            clear.textContent = 'Re-promote';
            clear.style.marginLeft = '6px';
            clear.addEventListener('click', async () => {
                try {
                    await api('/api/router/demotion/clear', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ agent, model }),
                    });
                    toast(`Re-promoted ${key}`);
                    await loadHealth(false);
                } catch (err) { toast(err.message, true); }
            });
            statusCell.appendChild(clear);
        } else if (dr.drift) {
            flagged++;
            status.className = 're-status warn';
            status.textContent = 'regression signal';
            detail.textContent = dr.reason || '';
        } else if (attempts || dr.reason) {
            status.className = 're-status ok';
            status.textContent = 'OK';
            if (dr.reason && !/normal/.test(dr.reason)) detail.textContent = dr.reason;
        } else {
            status.className = 're-status';
            status.textContent = 'no data';
        }
        statusCell.insertBefore(status, statusCell.firstChild);
        statusCell.appendChild(detail);
        tbody.appendChild(tr);
    });

    $('#health-summary').innerHTML =
        `<span>${Object.keys(demotions).length} demoted · ${flagged} flagged · evaluated ${h.generated_at ? fmtTime(h.generated_at) + '' : '—'}</span>`;
}

async function loadHealth(recheck) {
    try {
        state.health = await api(recheck ? '/api/router/health/refresh' : '/api/router/health', {
            method: recheck ? 'POST' : 'GET',
        });
        renderHealth();
    } catch (err) {
        toast(`Health: ${err.message}`, true);
    }
}

// ── frustration analysis config ──────────────────────────────────────────────
function renderRage() {
    const inv = state.rage.investigator || {};
    $('#rage-enabled').checked = !!state.rage.enabled;

    const agentSel = $('#rage-agent');
    const agents = (state.options && state.options.agents) || [];
    const curAgent = inv.agent || 'jev';
    agentSel.innerHTML = agents
        .map((a) => `<option value="${esc(a.id)}">${esc(a.label || a.id)}</option>`)
        .join('') +
        (curAgent && !agents.some((a) => a.id === curAgent)
            ? `<option value="${esc(curAgent)}">${esc(curAgent)}</option>` : '');
    agentSel.value = curAgent;

    const modelSel = $('#rage-model');
    const models = modelsFor(agentSel.value);
    const curModel = inv.model || '';
    modelSel.innerHTML =
        `<option value="">(default)</option>` +
        models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('') +
        (curModel && !models.includes(curModel)
            ? `<option value="${esc(curModel)}">${esc(curModel)}</option>` : '');
    modelSel.value = curModel || '';

    $('#rage-timeout').value = inv.timeout_s || 240;
}

function wireRage() {
    $('#rage-enabled').addEventListener('change', (e) => {
        state.rage.enabled = e.target.checked;
        markDirty();
    });
    $('#rage-agent').addEventListener('change', (e) => {
        state.rage.investigator.agent = e.target.value;
        state.rage.investigator.model = '';
        renderRage();
        markDirty();
    });
    $('#rage-model').addEventListener('change', (e) => {
        state.rage.investigator.model = e.target.value;
        markDirty();
    });
    $('#rage-timeout').addEventListener('change', (e) => {
        const n = parseInt(e.target.value, 10);
        state.rage.investigator.timeout_s = Math.max(30, Math.min(isNaN(n) ? 240 : n, 1800));
        e.target.value = state.rage.investigator.timeout_s;
        markDirty();
    });
}

// ── load / save ──────────────────────────────────────────────────────────────
function applyLoaded(data) {
    const cfg = data.config || {};
    state.config = {
        mode: cfg.provider ? cfg.provider.mode : 'off',
        api_model: cfg.provider ? cfg.provider.api_model : '',
        local_endpoint: cfg.provider ? cfg.provider.local_endpoint : '',
        local_model: cfg.provider ? cfg.provider.local_model : '',
        agent_id: cfg.provider ? cfg.provider.agent_id : '',
        agent_model: cfg.provider ? cfg.provider.agent_model : '',
        default_target: cfg.default_target || { agent: 'cursor', model: 'auto' },
        escalation_target: cfg.escalation_target || { agent: 'cursor', model: 'grok-4.6' },
        fallbacks: (cfg.fallbacks && cfg.fallbacks.ordered) || [],
    };
    state.useCases = (data.use_cases || []).map((uc) => {
        // Legacy preferred/escalation/fallbacks → ordered target chain
        const r = uc.routing || {};
        if (!Array.isArray(r.targets)) {
            const chain = [r.preferred, r.escalation, ...(Array.isArray(r.fallbacks) ? r.fallbacks : [])]
                .filter((t) => t && t.agent);
            uc.routing = { targets: chain, never_use: Array.isArray(r.never_use) ? r.never_use : [] };
        }
        return uc;
    });
    if (data.rage && typeof data.rage === 'object') {
        state.rage = {
            enabled: data.rage.enabled !== false,
            investigator: {
                enabled: (data.rage.investigator && data.rage.investigator.enabled) !== false,
                agent: (data.rage.investigator && data.rage.investigator.agent) || 'jev',
                model: (data.rage.investigator && data.rage.investigator.model) || '',
                timeout_s: (data.rage.investigator && data.rage.investigator.timeout_s) || 30,
            },
        };
    }
    state.dirty = false;
    $('#dirty-chip').hidden = true;
    renderCore();
    renderUseCases();
    renderRage();
}

async function loadAll() {
    try {
        const [opts, cfg] = await Promise.all([
            api('/api/agent-router/options'),
            api('/api/router/config'),
        ]);
        state.options = opts;
        applyLoaded(cfg);
        await loadHealth(false);
    } catch (err) {
        toast(`Load failed: ${err.message}`, true);
    }
}

async function saveAll() {
    const c = state.config;
    const payload = {
        mode: c.mode,
        default_target: c.default_target,
        escalation_target: c.escalation_target,
        fallbacks: (c.fallbacks || []).filter((t) => t && t.agent),
        use_cases: state.useCases,
        rage: {
            enabled: !!state.rage.enabled,
            investigator: {
                enabled: !!(state.rage.investigator && state.rage.investigator.enabled),
                agent: state.rage.investigator.agent,
                model: state.rage.investigator.model,
                timeout_s: state.rage.investigator.timeout_s,
            },
        },
    };
    // Only send the provider fields that belong to the active mode — the
    // backend validates strictly (e.g. a local model id is required *for
    // mode=local*) and blanks from other modes must not trip it.
    if (c.mode === 'api') payload.api_model = c.api_model;
    if (c.mode === 'local') {
        payload.local_endpoint = c.local_endpoint;
        payload.local_model = c.local_model;
    }
    if (c.mode === 'agent') {
        payload.agent_id = c.agent_id;
        payload.agent_model = c.agent_model;
    }
    try {
        const data = await api('/api/router/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        applyLoaded(data);
        toast('Router configuration saved');
    } catch (err) {
        toast(`Save failed: ${err.message}`, true);
    }
}

// ── events / init ────────────────────────────────────────────────────────────
function wireEvents() {
    $('#router-mode').addEventListener('change', (e) => {
        state.config.mode = e.target.value;
        renderProviderFields();
        markDirty();
    });
    $('#router-api-model').addEventListener('change', (e) => { state.config.api_model = e.target.value; markDirty(); });
    $('#router-local-endpoint').addEventListener('input', (e) => { state.config.local_endpoint = e.target.value; markDirty(); });
    $('#router-local-model').addEventListener('input', (e) => { state.config.local_model = e.target.value; markDirty(); });
    $('#router-agent-id').addEventListener('input', (e) => { state.config.agent_id = e.target.value; markDirty(); });
    $('#router-agent-model').addEventListener('input', (e) => { state.config.agent_model = e.target.value; markDirty(); });

    $('#btn-add-fallback').addEventListener('click', () => {
        state.config.fallbacks.push({ agent: 'codex', model: '' });
        markDirty();
        renderFallbacks();
    });

    $('#btn-add-usecase').addEventListener('click', () => {
        const maxPrio = state.useCases.reduce((m, u) => Math.max(m, Number(u.priority) || 0), 0);
        state.useCases.push({
            id: '',
            name: 'New use case',
            description: '',
            enabled: true,
            priority: maxPrio + 10,
            criteria: { task_types: [], difficulties: [], code_changes: null, keywords: [] },
            routing: {
                targets: [{ ...state.config.default_target }],
                never_use: [],
            },
        });
        markDirty();
        renderUseCases();
    });

    $('#btn-save').addEventListener('click', saveAll);
    $('#btn-refresh').addEventListener('click', loadAll);
    $('#btn-recheck').addEventListener('click', () => loadHealth(true));

    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) { e.preventDefault(); e.returnValue = ''; }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    wireEvents();
    wireRage();
    loadAll();
});
})();
