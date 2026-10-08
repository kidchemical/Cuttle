/* Gizmos App: create, configure, re-dock and remove gizmos without an agent.
 * Semantics stay in api.gizmos; markup comes from CuttleGizmos. */
(function () {
    'use strict';
    const M = window.CuttleGizmos;
    const esc = M.escape;
    const LIST_MS = 10000;
    const DATA_MS = 60000;
    const state = { revision: -1, gizmos: [], types: [], data: new Map(), fetchedAt: new Map() };
    const $ = id => document.getElementById(id);
    const toast = (msg, type) => (window.showToast || (m => console.log(m)))(msg, type || 'info');

    const desktop = (() => {
        try { return !!(window.top.electron && window.top.electron.gizmos); } catch (_) { return false; }
    })();

    function notifyShell() {
        try { window.top.postMessage({ type: 'cuttle-gizmos-changed' }, window.location.origin); } catch (_) { /* standalone */ }
    }

    async function api(path, init) {
        const res = await fetch(path, {
            credentials: 'same-origin', cache: 'no-store', ...(init || {}),
            headers: { 'Content-Type': 'application/json' },
        });
        let body = {};
        try { body = await res.json(); } catch (_) { body = {}; }
        return { ok: res.ok, status: res.status, body };
    }

    function dockLabel(dock) {
        if (dock === 'popout' && !desktop) return M.DOCK_LABELS.popout + ' — desktop app; floats here';
        if (dock === 'titlebar' && !desktop) return M.DOCK_LABELS.titlebar + ' — desktop app; blade bar here';
        return M.DOCK_LABELS[dock];
    }

    function options(list, selected, value, label) {
        return list.map(item => '<option value="' + esc(value(item)) + '"' + (value(item) === selected ? ' selected' : '')
            + '>' + esc(label(item)) + '</option>').join('');
    }

    function usageAgents() {
        const t = state.types.find(x => x.id === 'usage_meter');
        return (t && t.options && t.options.agents) || [];
    }

    function renderCreate() {
        $('gizmosCreate').hidden = false;
        const typeSel = $('gizmoCreateType');
        if (!typeSel.options.length) {
            typeSel.innerHTML = options(state.types.filter(t => !(t.options || {}).scoped), 'usage_meter', t => t.id, t => t.label);
            $('gizmoCreateDock').innerHTML = options(M.DOCKS, desktop ? 'titlebar' : 'rail', d => d, dockLabel);
        }
        const agentSel = $('gizmoCreateAgent');
        if (!agentSel.options.length) agentSel.innerHTML = options(usageAgents(), 'codex', a => a.agent, a => a.label);
        agentSel.closest('label').hidden = typeSel.value !== 'usage_meter';
    }

    function renderList() {
        const box = $('gizmosList');
        if (!state.gizmos.length) {
            box.innerHTML = '<h2>Your gizmos</h2><div class="gizmos-empty">No gizmos yet. Add a usage meter above, or ask an agent to run <code>python -m api.gizmos create usage_meter --agent codex</code>.</div>';
            return;
        }
        const now = Date.now();
        const focused = document.activeElement && document.activeElement.closest && document.activeElement.closest('[data-gizmo-item]');
        box.innerHTML = '<h2>Your gizmos</h2>' + state.gizmos.map(g => {
            const cfg = g.config || {};
            const fields = g.type === 'usage_meter'
                ? '<label><span>Agent</span><select data-field="agent">' + options(usageAgents(), cfg.agent, a => a.agent, a => a.label) + '</select></label>'
                  + '<label><span>Show</span><select data-field="show">' + options(['remaining', 'used'], cfg.show || 'remaining', s => s, s => (s === 'used' ? 'Used' : 'Remaining')) + '</select></label>'
                  + '<label><span>Window</span><select data-field="window">' + options(windowChoices(g), cfg.window || 'tightest', w => w.id, w => w.label) + '</select></label>'
                : '';
            return '<article class="gizmo-item" data-gizmo-item="' + esc(g.id) + '">'
                + '<div class="gizmo-item-preview">' + M.renderGizmoHtml(g, state.data.get(M.dataKey(g)), 'card', now) + '</div>'
                + '<div class="gizmo-item-fields">'
                + '<label><span>Title</span><input data-field="title" maxlength="60" value="' + esc(g.title) + '"></label>'
                + fields
                + '<label><span>Place on</span><select data-field="dock">' + options(M.DOCKS, g.placement.dock, d => d, dockLabel) + '</select></label>'
                + '</div>'
                + '<div class="gizmo-item-actions">'
                + '<button type="button" data-action="refresh">Refresh data</button>'
                + '<button type="button" data-action="remove" class="gizmos-danger">Remove</button>'
                + '<span class="gizmo-item-meta">' + esc(g.id) + ' · created by ' + esc(g.created_by || 'ui') + '</span>'
                + '</div></article>';
        }).join('');
        if (focused) {
            const again = box.querySelector('[data-gizmo-item="' + focused.dataset.gizmoItem + '"] [data-field="title"]');
            if (again) again.focus();
        }
    }

    function windowChoices(g) {
        const data = state.data.get(M.dataKey(g));
        const windows = (data && data.windows) || [];
        const list = [{ id: 'tightest', label: 'Tightest (auto)' }].concat(windows.map(w => ({ id: w.id, label: w.label })));
        const want = (g.config || {}).window;
        if (want && !list.some(w => w.id === want)) list.push({ id: want, label: want });
        return list;
    }

    async function loadData(force, onlyId) {
        const groups = new Map();
        state.gizmos.forEach(g => {
            if (onlyId && g.id !== onlyId) return;
            const key = M.dataKey(g);
            if (!groups.has(key)) groups.set(key, g);
        });
        await Promise.all(Array.from(groups, async ([key, g]) => {
            if (!force && Date.now() - (state.fetchedAt.get(key) || 0) < DATA_MS) return;
            state.fetchedAt.set(key, Date.now());
            try {
                const res = await api('/api/gizmos/' + encodeURIComponent(g.id) + '/data' + (force ? '?refresh=1' : ''));
                if (res.body && res.body.success) state.data.set(key, res.body.data);
            } catch (_) { /* keep last */ }
        }));
        if (!editing()) renderList();
    }

    const editing = () => {
        const a = document.activeElement;
        return !!(a && a.matches && a.matches('.gizmo-item input, .gizmo-item select'));
    };

    async function load(force) {
        const status = $('gizmosStatus');
        try {
            const res = await api('/api/gizmos');
            const b = res.body || {};
            if (!b.success) {
                status.textContent = b.disabled
                    ? 'Gizmos is disabled. Enable it in Settings → Experimental.'
                    : (res.status === 403 ? 'Only the Cuttle owner can manage gizmos.' : (b.error || 'Unavailable'));
                $('gizmosCreate').hidden = true;
                $('gizmosList').replaceChildren();
                state.revision = -1;
                return;
            }
            state.types = b.types || [];
            status.textContent = (b.gizmos || []).length
                ? (b.gizmos.length + ' gizmo' + (b.gizmos.length === 1 ? '' : 's') + '. Changes apply to every open Cuttle window.')
                : 'Ready.';
            if (force || b.revision !== state.revision) {
                state.revision = b.revision;
                state.gizmos = b.gizmos || [];
                renderCreate();
                if (!editing()) renderList();
            }
            loadData(false);
        } catch (err) {
            status.textContent = 'Could not load: ' + (err.message || err);
        }
    }

    async function patch(id, payload) {
        const res = await api('/api/gizmos/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify(payload) });
        if (!res.body.success) { toast(res.body.error || 'Update failed', 'error'); return; }
        notifyShell();
        await load(true);
    }

    $('gizmosCreateForm').addEventListener('submit', async ev => {
        ev.preventDefault();
        const type = $('gizmoCreateType').value;
        const payload = {
            type,
            title: $('gizmoCreateTitle').value.trim() || undefined,
            placement: { dock: $('gizmoCreateDock').value },
            config: type === 'usage_meter' ? { agent: $('gizmoCreateAgent').value } : {},
        };
        const res = await api('/api/gizmos', { method: 'POST', body: JSON.stringify(payload) });
        if (!res.body.success) { toast(res.body.error || 'Could not add gizmo', 'error'); return; }
        $('gizmoCreateTitle').value = '';
        toast('Added ' + res.body.gizmo.title, 'success');
        notifyShell();
        await load(true);
    });
    $('gizmoCreateType').addEventListener('change', renderCreate);

    $('gizmosList').addEventListener('change', ev => {
        const field = ev.target.closest('[data-field]');
        const item = ev.target.closest('[data-gizmo-item]');
        if (!field || !item) return;
        const id = item.dataset.gizmoItem;
        const key = field.dataset.field;
        if (key === 'title') patch(id, { title: field.value });
        else if (key === 'dock') patch(id, { placement: { dock: field.value } });
        else patch(id, { config: { [key]: field.value } });
    });

    $('gizmosList').addEventListener('click', async ev => {
        const btn = ev.target.closest('[data-action]');
        const item = ev.target.closest('[data-gizmo-item]');
        if (!btn || !item) return;
        const id = item.dataset.gizmoItem;
        if (btn.dataset.action === 'refresh') {
            btn.disabled = true;
            await loadData(true, id);
            btn.disabled = false;
        } else if (btn.dataset.action === 'remove') {
            const g = state.gizmos.find(x => x.id === id);
            if (!window.confirm('Remove “' + ((g && g.title) || id) + '”?')) return;
            const res = await api('/api/gizmos/' + encodeURIComponent(id), { method: 'DELETE' });
            if (!res.body.success) { toast(res.body.error || 'Remove failed', 'error'); return; }
            notifyShell();
            await load(true);
        }
    });

    $('refreshGizmos').addEventListener('click', () => load(true));
    setInterval(() => { if (!document.hidden) load(false); }, LIST_MS);
    setInterval(() => { if (!document.hidden && !editing()) renderList(); }, 15000);
    load(true);
})();
