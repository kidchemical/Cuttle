/* Gizmos shell controller (experimental flag `gizmos`, owner api.gizmos).
 * Effects only: dock containers, drag-to-redock, the detail popover, list and
 * data polling, and Electron pop-out sync. Decisions and markup come from
 * CuttleGizmos (gizmos_model.js). The shell document hosts every Space, so
 * floating gizmos stay visible across Space switches. */
(function (root) {
    'use strict';
    const LIST_MS = 8000;
    const DISABLED_MS = 30000;
    const DATA_MS = 60000;
    const TICK_MS = 15000;
    const MOUNT_MS = 2000;
    const DRAG_PX = 5;

    function start(opts) {
        const M = root.CuttleGizmos;
        const doc = root.document;
        if (!M || !doc || root.__cuttleGizmosShell) return root.__cuttleGizmosShell || null;
        const options = opts || {};
        const electron = root.electron && root.electron.isElectron ? root.electron : null;
        const popoutApi = electron && electron.gizmos ? electron.gizmos : null;
        const state = {
            enabled: false, revision: -1, gizmos: [], types: [],
            data: new Map(), fetchedAt: new Map(), inflight: new Set(),
            wasBlocked: new Map(),
            popoutKey: null, listTimer: null, drag: null, popoverId: null,
        };

        const toast = (msg, type) => { try { (root.showToast || (() => {}))(msg, type || 'info'); } catch (_) { /* optional */ } };

        function env() {
            const tb = doc.getElementById('shellTitlebar');
            return {
                titlebar: !!(tb && !tb.hidden && root.getComputedStyle(tb).display !== 'none'),
                popout: !!(popoutApi && typeof popoutApi.syncPopouts === 'function'),
            };
        }

        /** OS safe-area insets (px) via the repo vars ui_boot maintains. */
        function safeInsets() {
            const out = { top: 0, right: 0, bottom: 0, left: 0 };
            try {
                const cs = root.getComputedStyle(doc.documentElement);
                ['top', 'right', 'bottom', 'left'].forEach(side => {
                    const raw = cs.getPropertyValue('--cuttle-safe-' + side)
                        || cs.getPropertyValue('--safe-area-inset-' + side);
                    const n = parseFloat(raw);
                    if (Number.isFinite(n) && n > 0) out[side] = n;
                });
            } catch (_) { /* no insets without computed style */ }
            return out;
        }

        async function api(path, init) {
            const res = await root.fetch(path, {
                credentials: 'same-origin', cache: 'no-store', ...(init || {}),
                headers: { 'Content-Type': 'application/json' },
            });
            let body = {};
            try { body = await res.json(); } catch (_) { body = {}; }
            return { ok: res.ok, status: res.status, body };
        }

        // ── dock containers ─────────────────────────────────────────────
        function titlebarDock() {
            const host = doc.getElementById('shellTitlebarDrag');
            if (!host) return null;
            let el = doc.getElementById('shellGizmoDockTitlebar');
            if (!el) {
                el = doc.createElement('div');
                el.id = 'shellGizmoDockTitlebar';
                el.className = 'shell-gizmo-dock shell-gizmo-dock--titlebar';
                el.dataset.gizmoDock = 'titlebar';
                host.appendChild(el);
            }
            return el;
        }

        function leftmostRail() {
            let best = null, bestRect = null;
            doc.querySelectorAll('.split-column .icon-rail').forEach(rail => {
                const col = rail.closest('.split-column');
                if (col && col.dataset.splitDiscarded === '1') return;
                const r = rail.getBoundingClientRect();
                if (!r.width || !r.height) return;
                if (!bestRect || r.left < bestRect.left - 1
                    || (Math.abs(r.left - bestRect.left) <= 1 && r.top < bestRect.top)) {
                    best = rail; bestRect = r;
                }
            });
            return best;
        }

        function railDock() {
            const rail = leftmostRail();
            let el = doc.getElementById('shellGizmoDockRail');
            if (!rail) return el && el.isConnected ? el : null;
            if (!el) {
                el = doc.createElement('div');
                el.id = 'shellGizmoDockRail';
                el.className = 'rail-gizmos shell-gizmo-dock shell-gizmo-dock--rail';
                el.dataset.gizmoDock = 'rail';
            }
            if (el.parentElement !== rail) {
                const footer = rail.querySelector(':scope > .rail-footer');
                rail.insertBefore(el, footer || null);
            }
            return el;
        }

        function floatLayer() {
            let el = doc.getElementById('shellGizmoFloatLayer');
            if (!el) {
                el = doc.createElement('div');
                el.id = 'shellGizmoFloatLayer';
                el.className = 'shell-gizmo-float-layer';
                doc.body.appendChild(el);
            }
            return el;
        }

        const findWrap = id => doc.querySelector('.shell-gizmo[data-gizmo-id="' + String(id).replace(/[^a-z0-9_-]/g, '') + '"]');

        function positionFloat(wrap, placement) {
            const W = root.innerWidth, H = root.innerHeight;
            const w = wrap.offsetWidth || 220, h = wrap.offsetHeight || 64;
            const x = placement && placement.x != null ? placement.x : 0.72;
            const y = placement && placement.y != null ? placement.y : 0.08;
            wrap.style.left = Math.round(Math.max(4, Math.min(W - w - 4, x * W))) + 'px';
            wrap.style.top = Math.round(Math.max(4, Math.min(H - h - 4, y * H))) + 'px';
        }

        // ── render ──────────────────────────────────────────────────────
        function render() {
            const e = env();
            if (!state.enabled) {
                doc.querySelectorAll('.shell-gizmo[data-gizmo-id]').forEach(w => w.remove());
                closePopover();
                syncPopouts();
                return;
            }
            const nowMs = Date.now();
            const boxes = { titlebar: e.titlebar ? titlebarDock() : null, rail: railDock(), float: floatLayer() };
            const seen = new Set();
            ['titlebar', 'rail', 'float'].forEach(dock => {
                const box = boxes[dock];
                if (!box) return;
                const list = M.sortForDock(state.gizmos, dock, e);
                list.forEach((g, i) => {
                    seen.add(g.id);
                    let wrap = findWrap(g.id);
                    if (!wrap) {
                        wrap = doc.createElement('div');
                        wrap.className = 'shell-gizmo';
                        wrap.dataset.gizmoId = g.id;
                    }
                    const html = M.renderGizmoHtml(g, state.data.get(M.dataKey(g)), dock, nowMs);
                    if (wrap.__gizmoHtml !== html) { wrap.innerHTML = html; wrap.__gizmoHtml = html; }
                    if (wrap.parentElement !== box || box.children[i] !== wrap) box.insertBefore(wrap, box.children[i] || null);
                    wrap.dataset.dock = dock;
                    if (dock === 'float') positionFloat(wrap, g.placement);
                    else { wrap.style.left = ''; wrap.style.top = ''; }
                });
                box.classList.toggle('is-empty', !list.length);
            });
            doc.querySelectorAll('.shell-gizmo[data-gizmo-id]').forEach(w => {
                if (!seen.has(w.dataset.gizmoId)) w.remove();
            });
            syncPopouts();
            if (state.popoverId) refreshPopover();
        }

        function syncPopouts() {
            if (!env().popout) return;
            const want = state.enabled
                ? state.gizmos.filter(g => g.placement && g.placement.dock === 'popout').map(g => ({ id: g.id, title: g.title }))
                : [];
            const key = JSON.stringify(want);
            if (key === state.popoutKey) return;
            state.popoutKey = key;
            Promise.resolve(popoutApi.syncPopouts(want)).catch(() => { state.popoutKey = null; });
        }

        // ── server state ───────────────────────────────────────────────
        async function loadList(force) {
            root.clearTimeout(state.listTimer);
            let next = DISABLED_MS;
            try {
                const res = await api('/api/gizmos');
                const b = res.body || {};
                if (b.success) {
                    next = LIST_MS;
                    const wasEnabled = state.enabled;
                    state.enabled = true;
                    if (force || !wasEnabled || b.revision !== state.revision) {
                        state.revision = b.revision;
                        state.gizmos = Array.isArray(b.gizmos) ? b.gizmos : [];
                        state.types = Array.isArray(b.types) ? b.types : state.types;
                        render();
                        refreshDue(false);
                    }
                } else if (state.enabled || force) {
                    state.enabled = false;
                    state.gizmos = [];
                    render();
                }
            } catch (_) {
                next = state.enabled ? LIST_MS : DISABLED_MS;
            }
            state.listTimer = root.setTimeout(() => loadList(false), next);
        }

        function applyGizmo(gizmo) {
            const i = state.gizmos.findIndex(g => g.id === gizmo.id);
            if (i >= 0) state.gizmos[i] = gizmo; else state.gizmos.push(gizmo);
            render();
            refreshDue(false);
        }

        async function patch(id, payload) {
            try {
                const res = await api('/api/gizmos/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify(payload) });
                if (res.body && res.body.success && res.body.gizmo) { applyGizmo(res.body.gizmo); return res.body.gizmo; }
                toast((res.body && res.body.error) || 'Gizmo update failed', 'error');
            } catch (err) {
                toast('Gizmo update failed: ' + (err.message || err), 'error');
            }
            loadList(true);
            return null;
        }

        async function remove(id) {
            try {
                const res = await api('/api/gizmos/' + encodeURIComponent(id), { method: 'DELETE' });
                if (!res.body || !res.body.success) toast((res.body && res.body.error) || 'Could not remove gizmo', 'error');
            } catch (err) {
                toast('Could not remove gizmo: ' + (err.message || err), 'error');
            }
            state.gizmos = state.gizmos.filter(g => g.id !== id);
            closePopover();
            render();
            loadList(true);
        }

        function refreshDue(force, onlyId) {
            if (!state.enabled) return;
            const groups = new Map();
            state.gizmos.forEach(g => {
                if (onlyId && g.id !== onlyId) return;
                const key = M.dataKey(g);
                if (!groups.has(key)) groups.set(key, g);
            });
            groups.forEach((g, key) => {
                if (state.inflight.has(key)) return;
                const age = Date.now() - (state.fetchedAt.get(key) || 0);
                if (!force && (age < DATA_MS || doc.hidden)) return;
                state.inflight.add(key);
                api('/api/gizmos/' + encodeURIComponent(g.id) + '/data' + (force ? '?refresh=1' : ''))
                    .then(res => {
                        if (res.body && res.body.success) {
                            state.data.set(key, res.body.data);
                            watchUnblock(key, res.body.data);
                        }
                    })
                    .catch(() => { /* next tick retries */ })
                    .finally(() => {
                        state.fetchedAt.set(key, Date.now());
                        state.inflight.delete(key);
                        render();
                    });
            });
        }

        // ── notify-on-unblock ────────────────────────────────────────
        // One-shot: when a meter armed in its popover sees blocked → open,
        // queue a tray + UI toast (the shell heartbeat drains it into every
        // open Cuttle window, phone browser included) and disarm so it fires
        // once. The page must be open to observe the transition — same
        // caveat as completion notifications.
        function watchUnblock(key, data) {
            const blocked = !!(data && data.blocked);
            const was = state.wasBlocked.get(key);
            state.wasBlocked.set(key, blocked);
            if (was !== true || blocked) return;
            const armed = state.gizmos.filter(g => M.dataKey(g) === key
                && g.config && g.config.notify_on_unblock);
            if (!armed.length) return;
            const label = (data && data.label)
                || (armed[0].config && armed[0].config.agent) || 'Agent';
            armed.forEach(g => { g.config = { ...g.config, notify_on_unblock: false }; });
            render();
            if (state.popoverId && armed.some(g => g.id === state.popoverId)) refreshPopover();
            api('/api/toast', {
                method: 'POST',
                body: JSON.stringify({ message: label + ' unblocked — usage limits reset', variant: 'success' }),
            }).catch(() => { /* heartbeat shows the local toast below anyway */ });
            toast(label + ' unblocked', 'success');
            armed.forEach(g => patch(g.id, { config: { notify_on_unblock: false } }));
        }

        // ── drag to re-dock ────────────────────────────────────────────
        function dropTarget(x, y) {
            if (env().titlebar) {
                const r = doc.getElementById('shellTitlebar').getBoundingClientRect();
                if (r.height > 0 && y >= r.top && y <= r.bottom && x >= r.left && x <= r.right) return 'titlebar';
            }
            const rail = leftmostRail();
            if (rail) {
                const r = rail.getBoundingClientRect();
                if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return 'rail';
            }
            return 'float';
        }

        function dropOrder(dock, id, x, y) {
            const siblings = M.sortForDock(state.gizmos, dock, env()).filter(g => g.id !== id);
            let i = 0;
            for (; i < siblings.length; i += 1) {
                const wrap = findWrap(siblings[i].id);
                if (!wrap) continue;
                const r = wrap.getBoundingClientRect();
                const before = dock === 'titlebar' ? x < r.left + r.width / 2 : y < r.top + r.height / 2;
                if (before) break;
            }
            const prev = siblings[i - 1], next = siblings[i];
            return M.orderBetween(prev ? prev.placement.order : null, next ? next.placement.order : null);
        }

        function setDropHover(target) {
            const tb = doc.getElementById('shellTitlebar');
            const rail = doc.getElementById('shellGizmoDockRail');
            const railEl = rail && rail.closest('.icon-rail');
            if (tb) tb.classList.toggle('gizmo-drop-hover', target === 'titlebar');
            doc.querySelectorAll('.icon-rail.gizmo-drop-hover').forEach(el => { if (el !== railEl || target !== 'rail') el.classList.remove('gizmo-drop-hover'); });
            if (railEl && target === 'rail') railEl.classList.add('gizmo-drop-hover');
        }

        function beginDrag(d) {
            d.active = true;
            closePopover();
            const rect = d.wrap.getBoundingClientRect();
            d.offX = d.startX - rect.left;
            d.offY = d.startY - rect.top;
            const ghost = d.wrap.cloneNode(true);
            ghost.className = 'shell-gizmo-ghost';
            delete ghost.dataset.gizmoId;
            ghost.style.width = rect.width + 'px';
            doc.body.appendChild(ghost);
            d.ghost = ghost;
            d.wrap.classList.add('is-drag-source');
            doc.body.classList.add('gizmo-dragging');
        }

        function cleanupDrag(d) {
            if (d.ghost) d.ghost.remove();
            d.wrap.classList.remove('is-drag-source');
            doc.body.classList.remove('gizmo-dragging');
            setDropHover(null);
        }

        function endDrag(d, ev) {
            const target = dropTarget(ev.clientX, ev.clientY);
            const gizmo = state.gizmos.find(g => g.id === d.id);
            const order = target === 'float' ? null : dropOrder(target, d.id, ev.clientX, ev.clientY);
            cleanupDrag(d);
            if (!gizmo) return;
            const clamp01 = v => Math.max(0, Math.min(1, v));
            const placement = target === 'float'
                ? { dock: 'float', x: clamp01((ev.clientX - d.offX) / root.innerWidth), y: clamp01((ev.clientY - d.offY) / root.innerHeight) }
                : { dock: target, order };
            gizmo.placement = { ...gizmo.placement, ...placement };
            render();
            patch(gizmo.id, { placement });
        }

        doc.addEventListener('pointerdown', ev => {
            const inPopover = ev.target.closest && ev.target.closest('#shellGizmoPopover');
            const wrap = ev.target.closest && ev.target.closest('.shell-gizmo[data-gizmo-id]');
            if (!inPopover && !wrap) closePopover();
            if (ev.button !== 0 || !wrap) return;
            ev.preventDefault();
            state.drag = { id: wrap.dataset.gizmoId, wrap, startX: ev.clientX, startY: ev.clientY, pointerId: ev.pointerId, active: false };
            try { wrap.setPointerCapture(ev.pointerId); } catch (_) { /* capture optional */ }
        }, true);
        doc.addEventListener('pointermove', ev => {
            const d = state.drag;
            if (!d || ev.pointerId !== d.pointerId) return;
            if (!d.active) {
                if (Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) < DRAG_PX) return;
                beginDrag(d);
            }
            d.ghost.style.left = (ev.clientX - d.offX) + 'px';
            d.ghost.style.top = (ev.clientY - d.offY) + 'px';
            setDropHover(dropTarget(ev.clientX, ev.clientY));
        });
        doc.addEventListener('pointerup', ev => {
            const d = state.drag;
            if (!d || ev.pointerId !== d.pointerId) return;
            state.drag = null;
            if (d.active) endDrag(d, ev);
            else togglePopover(d.id);
        });
        doc.addEventListener('pointercancel', () => {
            const d = state.drag;
            state.drag = null;
            if (d && d.active) cleanupDrag(d);
        });
        doc.addEventListener('contextmenu', ev => {
            const wrap = ev.target.closest && ev.target.closest('.shell-gizmo[data-gizmo-id]');
            if (!wrap) return;
            ev.preventDefault();
            openPopover(wrap.dataset.gizmoId);
        });

        // ── detail popover / menu ──────────────────────────────────────
        function popoverEl() {
            let el = doc.getElementById('shellGizmoPopover');
            if (!el) {
                el = doc.createElement('div');
                el.id = 'shellGizmoPopover';
                el.className = 'shell-gizmo-popover';
                el.setAttribute('role', 'dialog');
                el.setAttribute('aria-label', 'Gizmo');
                el.hidden = true;
                el.addEventListener('click', onPopoverClick);
                el.addEventListener('change', onPopoverChange);
                doc.body.appendChild(el);
            }
            return el;
        }

        function togglePopover(id) {
            if (state.popoverId === id) closePopover();
            else openPopover(id);
        }

        function openPopover(id) {
            state.popoverId = id;
            refreshPopover();
            refreshDue(false, id);
        }

        function closePopover() {
            state.popoverId = null;
            const el = doc.getElementById('shellGizmoPopover');
            if (el) el.hidden = true;
        }

        function agentOptions() {
            const t = state.types.find(x => x.id === 'usage_meter');
            return (t && t.options && Array.isArray(t.options.agents)) ? t.options.agents : [];
        }

        function refreshPopover() {
            const g = state.gizmos.find(x => x.id === state.popoverId);
            const anchor = g && findWrap(g.id);
            if (!g || !anchor) { closePopover(); return; }
            const el = popoverEl();
            const active = doc.activeElement;
            if (!el.hidden && active && active.tagName === 'SELECT' && el.contains(active)) return;
            const e = env();
            const docks = M.DOCKS.filter(dk => (dk !== 'titlebar' || e.titlebar) && (dk !== 'popout' || e.popout));
            const esc = M.escape;
            const agentField = g.type === 'usage_meter'
                ? '<label class="shell-gizmo-popover-field"><span>Agent</span><select data-gizmo-field="agent">'
                    + agentOptions().map(a => '<option value="' + esc(a.agent) + '"' + (a.agent === g.config.agent ? ' selected' : '')
                        + '>' + esc(a.label) + '</option>').join('') + '</select></label>'
                    + '<label class="shell-gizmo-popover-field"><span>Show</span><select data-gizmo-field="show">'
                    + ['remaining', 'used'].map(s => '<option value="' + s + '"' + ((g.config.show || 'remaining') === s ? ' selected' : '')
                        + '>' + (s === 'used' ? 'Used' : 'Remaining') + '</option>').join('') + '</select></label>'
                : '';
            const armed = !!(g.config && g.config.notify_on_unblock);
            const notifyBtn = g.type === 'usage_meter'
                ? '<button type="button" data-gizmo-action="notify"'
                    + (armed ? ' class="is-current" aria-pressed="true"' : '')
                    + '>' + (armed ? 'Stop notify' : 'Notify when unblocked') + '</button>'
                : '';
            const html = M.renderDetailHtml(g, state.data.get(M.dataKey(g)), Date.now())
                + agentField
                + '<div class="shell-gizmo-popover-section">Move to</div><div class="shell-gizmo-popover-docks">'
                + docks.map(dk => '<button type="button" data-gizmo-action="dock" data-dock="' + dk + '"'
                    + (g.placement.dock === dk ? ' class="is-current" aria-pressed="true"' : '') + '>'
                    + esc(M.DOCK_LABELS[dk]) + '</button>').join('') + '</div>'
                + '<div class="shell-gizmo-popover-actions">'
                + '<button type="button" data-gizmo-action="refresh">Refresh</button>'
                + notifyBtn
                + (options.openApp ? '<button type="button" data-gizmo-action="manage">Gizmos app</button>' : '')
                + '<button type="button" data-gizmo-action="remove" class="is-danger">Remove</button></div>';
            if (el.__gizmoHtml !== html) { el.innerHTML = html; el.__gizmoHtml = html; }
            el.hidden = false;
            // Clamp inside the usable viewport: the repo's safe-area insets
            // (--cuttle-safe-*, set by ui_boot from env()/native values) plus
            // a finger's padding. CSS max-height caps the panel first, so
            // offsetWidth/Height already reflect the cap. visualViewport keeps
            // a pinched page honest: rects are visual-relative while fixed
            // positioning is layout-relative, so shift by the viewport offset.
            const safe = safeInsets();
            const PAD = 8;
            const vv = root.visualViewport;
            const W = (vv && vv.width) || root.innerWidth;
            const H = (vv && vv.height) || root.innerHeight;
            const vox = (vv && vv.offsetLeft) || 0;
            const voy = (vv && vv.offsetTop) || 0;
            const r = anchor.getBoundingClientRect();
            const pw = el.offsetWidth, ph = el.offsetHeight;
            const minLeft = PAD + safe.left, minTop = PAD + safe.top;
            const maxLeft = Math.max(minLeft, W - PAD - safe.right - pw);
            const maxTop = Math.max(minTop, H - PAD - safe.bottom - ph);
            let left, top;
            if (r.right + 8 + pw <= W - PAD - safe.right && r.width < 80 && r.left < 120) {
                left = r.right + 8;
                top = Math.max(minTop, Math.min(maxTop, r.top));
            } else {
                left = Math.max(minLeft, Math.min(maxLeft, r.left));
                const fitsBelow = r.bottom + 8 + ph <= H - PAD - safe.bottom;
                top = fitsBelow ? r.bottom + 8 : Math.max(minTop, Math.min(maxTop, r.top - ph - 8));
            }
            el.style.left = Math.round(vox + Math.max(minLeft, Math.min(maxLeft, left))) + 'px';
            el.style.top = Math.round(voy + Math.max(minTop, Math.min(maxTop, top))) + 'px';
        }

        function onPopoverClick(ev) {
            const btn = ev.target.closest('[data-gizmo-action]');
            const id = state.popoverId;
            if (!btn || !id) return;
            const action = btn.dataset.gizmoAction;
            if (action === 'dock') {
                closePopover();
                patch(id, { placement: { dock: btn.dataset.dock } });
            } else if (action === 'refresh') {
                refreshDue(true, id);
            } else if (action === 'notify') {
                const cur = state.gizmos.find(x => x.id === id);
                const on = !(cur && cur.config && cur.config.notify_on_unblock);
                patch(id, { config: { notify_on_unblock: on } });
                toast(on ? 'Will notify when unblocked' : 'Unblock notification off', 'info');
            } else if (action === 'manage') {
                closePopover();
                options.openApp('/gizmos_page.html');
            } else if (action === 'remove') {
                const g = state.gizmos.find(x => x.id === id);
                if (root.confirm('Remove “' + ((g && g.title) || 'gizmo') + '”?')) remove(id);
            }
        }

        function onPopoverChange(ev) {
            const field = ev.target.closest('[data-gizmo-field]');
            const id = state.popoverId;
            if (!field || !id) return;
            patch(id, { config: { [field.dataset.gizmoField]: field.value } });
        }

        doc.addEventListener('keydown', ev => { if (ev.key === 'Escape' && state.popoverId) closePopover(); });
        // Clicking into a chat pane (or any iframe) never reaches the shell
        // document's pointerdown handler above, so the panel would stay open.
        // Focus moving into embedded content means the user clicked off it.
        root.addEventListener('blur', () => { if (state.popoverId) closePopover(); });
        root.addEventListener('resize', () => { closePopover(); if (state.enabled) render(); });
        doc.addEventListener('visibilitychange', () => { if (!doc.hidden && state.enabled) { render(); refreshDue(false); } });
        root.addEventListener('message', ev => {
            const d = ev.data;
            if (!d || typeof d !== 'object') return;
            if (d.type === 'cuttle-gizmos-changed' || d.type === 'cuttle-experimental-flags-changed') loadList(true);
        });
        if (popoutApi && typeof popoutApi.onPopoutClosed === 'function') {
            popoutApi.onPopoutClosed(async id => {
                // Return a user-closed window to the Cuttle window, unless an
                // agent or another surface already re-docked it.
                try {
                    const res = await api('/api/gizmos/' + encodeURIComponent(id));
                    const g = res.body && res.body.gizmo;
                    if (g && g.placement && g.placement.dock === 'popout') {
                        await api('/api/gizmos/' + encodeURIComponent(id), {
                            method: 'PATCH', body: JSON.stringify({ placement: { dock: 'float' } }),
                        });
                    }
                } catch (_) { /* list poll reconciles */ }
                state.popoutKey = null;
                loadList(true);
            });
        }

        root.setInterval(() => { if (state.enabled) { render(); refreshDue(false); } }, TICK_MS);
        root.setInterval(() => {
            if (!state.enabled || state.drag) return;
            const rail = leftmostRail();
            const dock = doc.getElementById('shellGizmoDockRail');
            const titleDock = doc.getElementById('shellGizmoDockTitlebar');
            if ((rail && (!dock || dock.parentElement !== rail)) || (env().titlebar && !titleDock)) render();
        }, MOUNT_MS);
        loadList(true);

        const controller = { refresh: () => loadList(true), state, render };
        root.__cuttleGizmosShell = controller;
        return controller;
    }

    root.CuttleGizmosShell = { start };
    if (typeof module !== 'undefined' && module.exports) module.exports = root.CuttleGizmosShell;
    if (root.document && !root.__CUTTLE_GIZMOS_MANUAL) {
        start({ openApp: page => { if (typeof root.navigate === 'function') root.navigate(0, page); } });
    }
})(globalThis);
