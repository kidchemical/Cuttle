/* Desktop pop-out body: one gizmo in an always-on-top Electron window.
 * The window closes itself once the gizmo is re-docked, removed, or disabled;
 * the shell owns opening it (window.electron.gizmos.syncPopouts). */
(function () {
    'use strict';
    const M = window.CuttleGizmos;
    const id = new URLSearchParams(window.location.search).get('id') || '';
    const box = document.getElementById('gizmoPopout');
    const state = { gizmo: null, data: null, fetchedAt: 0 };
    const LIST_MS = 10000;
    const DATA_MS = 60000;

    function closeWindow() {
        try {
            if (window.electron && window.electron.windowControls) { window.electron.windowControls.close(); return; }
        } catch (_) { /* fall through */ }
        window.close();
    }

    async function api(path, init) {
        const res = await fetch(path, {
            credentials: 'same-origin', cache: 'no-store', ...(init || {}),
            headers: { 'Content-Type': 'application/json' },
        });
        let body = {};
        try { body = await res.json(); } catch (_) { body = {}; }
        return { status: res.status, body };
    }

    function render() {
        if (!state.gizmo) return;
        box.innerHTML = M.renderGizmoHtml(state.gizmo, state.data, 'popout', Date.now());
        document.title = state.gizmo.title + ' - Cuttle';
    }

    async function loadData(force) {
        if (!state.gizmo) return;
        state.fetchedAt = Date.now();
        try {
            const res = await api('/api/gizmos/' + encodeURIComponent(id) + '/data' + (force ? '?refresh=1' : ''));
            if (res.body && res.body.success) state.data = res.body.data;
        } catch (_) { /* keep last */ }
        render();
    }

    async function loadGizmo() {
        try {
            const res = await api('/api/gizmos/' + encodeURIComponent(id));
            const g = res.body && res.body.gizmo;
            if (res.body.disabled || res.status === 404 || (g && g.placement && g.placement.dock !== 'popout')) {
                closeWindow();
                return;
            }
            if (!g) {
                box.innerHTML = '<div class="popout-message">' + M.escape(res.body.error || 'Unavailable') + '</div>';
                return;
            }
            const agentChanged = !state.gizmo || M.dataKey(state.gizmo) !== M.dataKey(g);
            state.gizmo = g;
            if (agentChanged || Date.now() - state.fetchedAt >= DATA_MS) await loadData(false);
            else render();
        } catch (_) { /* server restarting; retry */ }
    }

    document.getElementById('popoutRefresh').addEventListener('click', () => loadData(true));
    document.getElementById('popoutDockBack').addEventListener('click', async () => {
        try {
            await api('/api/gizmos/' + encodeURIComponent(id), {
                method: 'PATCH', body: JSON.stringify({ placement: { dock: 'float' } }),
            });
        } catch (_) { /* shell reconciles */ }
        closeWindow();
    });
    setInterval(loadGizmo, LIST_MS);
    setInterval(render, 15000);
    loadGizmo();
})();
