/* ================================================================
   Cuttle Shell rail — rail customization + apps list (shell_rail.js)
   Owner: the rail layout lifetime in the shell. Owns the canonical
   rail/footer order, the pinned/hidden layout (`lastUILayout`) and its
   persistence, hold-to-edit mode, the Apps-page list broadcast and the
   Agent Feed rail availability.

   DOM/transport capabilities arrive as an explicit host object; this
   module never reads page globals. Loaded before app_shell.js in
   src/web/app_shell.html; app_shell keeps the composition call site,
   the global `getAppsList` delegate and the pane-mount re-apply calls.
   ================================================================ */
(function (root) {
    'use strict';

    function create(host) {
        const win = host.win || root;
        const doc = host.doc || win.document;
        const getColumnEl = host.getColumnEl;
        const broadcastToFrames = host.broadcastToFrames;
        const fetchImpl = (url, opts) => win.fetch(url, opts);

        // ── Default UI layout order (used when no saved layout) ─────────
        // Must list every `.rail-item[data-id]` in the main rail, in canonical order (matches app_shell.html).
        const CANONICAL_RAIL_ITEM_ORDER = [
            'nav-chat',
            'nav-editor',
            'nav-git',
            'nav-jobs',
            'nav-mesh',
            'nav-dashboards',
            'nav-achievements',
            'nav-agent-feed',
            'nav-gizmos',
            'nav-test-lab',
            'nav-projects',
            'nav-apps',
        ];
        // 'nav-tools' and 'nav-automation' (Home Automation) are retired. Saved layouts
        // referencing retired rail entries are filtered out during restore.
        // Footer may only contain these (do not put page nav buttons here — breaks reorder on load).
        const CANONICAL_RAIL_FOOTER_ORDER = ['nav-account', 'nav-notifications', 'nav-workspace', 'nav-settings', 'panelToggle'];
        // The Apps launcher is the way back to every stashed app, so it can never be removed.
        const RAIL_LOCKED_IDS = new Set(['nav-apps']);
        // Footer utilities that the user may hide (unpin) from the blade bar. They are
        // not page apps, but the Apps grid still lists them so they can be re-pinned.
        const HIDEABLE_FOOTER_IDS = new Set(['nav-workspace']);
        // Cuttle web apps that live in the Apps grid (not the blade bar) until the user pins them.
        const DEFAULT_RAIL_HIDDEN = ['nav-achievements', 'nav-gizmos', 'nav-test-lab', 'nav-projects', 'nav-agent-feed', 'nav-mesh'];
        // Bump when defaults change; saved layouts below this version get DEFAULT_RAIL_HIDDEN merged in once.
        const RAIL_LAYOUT_VERSION = 10;

        const DEFAULT_LAYOUT = {
            rail_items: CANONICAL_RAIL_ITEM_ORDER.filter(id => !DEFAULT_RAIL_HIDDEN.includes(id)),
            rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
            rail_hidden: DEFAULT_RAIL_HIDDEN.slice(),
            rail_footer_hidden: [],
            layout_version: RAIL_LAYOUT_VERSION,
        };

        // Experimental apps: their rail icon and Apps-grid tile stay hidden
        // unless the owning feature flag is on. Availability never changes the
        // user's saved pin layout.
        const EXPERIMENTAL_RAIL_APPS = {
            'nav-achievements': 'achievements',
            'nav-agent-feed': 'agent_feed',
            'nav-gizmos': 'gizmos',
            'nav-test-lab': 'test_lab',
        };
        let enabledFlags = new Set();
        function railAppEnabled(id) {
            const flag = EXPERIMENTAL_RAIL_APPS[id];
            return !flag || enabledFlags.has(flag);
        }
        /** Shared rail layout for every blade (not per-bar). */
        let lastUILayout = {
            rail_items: DEFAULT_LAYOUT.rail_items.slice(),
            rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
            rail_hidden: DEFAULT_RAIL_HIDDEN.slice(),
            rail_footer_hidden: [],
            layout_version: RAIL_LAYOUT_VERSION,
        };
        let railEditing = false;
        let railSuppressClick = false;
        let railCustomizeGlobalsReady = false;
        const RAIL_HOLD_MS = 500;
        const RAIL_HOLD_MOVE_PX = 8;
        const STORAGE_UI_LAYOUT = 'shell_ui_layout';

        function interactionLocked() {
            return railEditing || railSuppressClick;
        }

        function getLayoutFromDOM(colEl) {
            const col = colEl || getColumnEl(0);
            if (!col) {
                return {
                    rail_items: CANONICAL_RAIL_ITEM_ORDER.slice(),
                    rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
                    rail_hidden: [],
                };
            }
            const rail = col.querySelector('.rail-items');
            const footer = col.querySelector('.rail-footer');
            const stash = col.querySelector('.rail-stash');
            const hidden = Array.from(stash?.querySelectorAll('.rail-item[data-id]') || [])
                .map(e => e.dataset.id)
                .filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id));
            return {
                rail_items: Array.from(rail?.querySelectorAll('.rail-item[data-id]') || [])
                    .map(e => e.dataset.id)
                    .filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id)),
                rail_hidden: hidden,
                rail_footer: Array.from(footer?.querySelectorAll('.rail-item[data-id], .rail-panel-toggle[data-id]') || []).map(e => e.dataset.id),
                rail_footer_hidden: Array.from(footer?.querySelectorAll('.rail-item[data-id]') || [])
                    .filter(e => HIDEABLE_FOOTER_IDS.has(e.dataset.id) && e.hidden)
                    .map(e => e.dataset.id),
            };
        }

        function insertAtCanonicalSlot(result, id, canonical) {
            const canonIdx = canonical.indexOf(id);
            let insertAt = result.length;
            for (let i = 0; i < result.length; i++) {
                const ri = canonical.indexOf(result[i]);
                if (ri === -1) continue;
                if (ri > canonIdx) {
                    insertAt = i;
                    break;
                }
            }
            result.splice(insertAt, 0, id);
        }

        /**
         * Merge saved rail order with DOM: ids missing from older settings are inserted at their
         * canonical slot. Stray or retired ids are ignored here.
         */
        function normalizeRailItemOrder(savedIds, colEl) {
            const known = new Set(CANONICAL_RAIL_ITEM_ORDER);
            const rail = colEl?.querySelector?.('.rail-items');
            const stash = colEl?.querySelector?.('.rail-stash');
            const domIds = [
                ...Array.from(rail?.querySelectorAll('.rail-item[data-id]') || []),
                ...Array.from(stash?.querySelectorAll('.rail-item[data-id]') || []),
            ].map(e => e.dataset.id).filter(id => known.has(id));
            const canonInDom = CANONICAL_RAIL_ITEM_ORDER.filter(id => domIds.includes(id));
            if (!Array.isArray(savedIds) || savedIds.length === 0) return canonInDom;
            const validSaved = savedIds.filter(id => known.has(id) && canonInDom.includes(id));
            const missing = canonInDom.filter(id => !validSaved.includes(id));
            const result = validSaved.slice();
            for (const mid of missing) insertAtCanonicalSlot(result, mid, CANONICAL_RAIL_ITEM_ORDER);
            return result;
        }

        /**
         * Visible page icons. If `rail_hidden` is present (even empty), respect add/remove.
         * Legacy saves without that key keep the old "re-insert missing icons" behavior.
         */
        function resolveVisibleRailIds(layout, colEl) {
            if (!Array.isArray(layout?.rail_hidden)) {
                return normalizeRailItemOrder(layout?.rail_items, colEl);
            }
            const hidden = new Set(layout.rail_hidden.filter(
                id => CANONICAL_RAIL_ITEM_ORDER.includes(id) && !RAIL_LOCKED_IDS.has(id),
            ));
            const saved = Array.isArray(layout.rail_items)
                ? layout.rail_items.filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id) && !hidden.has(id))
                : [];
            const present = new Set([...saved, ...hidden]);
            const result = saved.slice();
            for (const id of CANONICAL_RAIL_ITEM_ORDER) {
                if (present.has(id)) continue;
                insertAtCanonicalSlot(result, id, CANONICAL_RAIL_ITEM_ORDER);
            }
            return result;
        }

        /** Footer: account, notifications, workspace, settings, panel toggle (not page nav). */
        function normalizeRailFooterOrder(savedIds) {
            const allowed = new Set(CANONICAL_RAIL_FOOTER_ORDER);
            const base = Array.isArray(savedIds) ? savedIds.filter(id => allowed.has(id)) : [];
            for (const id of CANONICAL_RAIL_FOOTER_ORDER) {
                if (base.includes(id)) continue;
                insertAtCanonicalSlot(base, id, CANONICAL_RAIL_FOOTER_ORDER);
            }
            return base;
        }

        function applyLayoutToColumn(colEl, layout) {
            if (!colEl || !layout) return;
            ensureRailEditChrome(colEl);
            const rail = colEl.querySelector('.rail-items');
            const footer = colEl.querySelector('.rail-footer');
            const stash = colEl.querySelector('.rail-stash');
            const addSlot = colEl.querySelector('.rail-add-slot');
            const byId = new Map();
            colEl.querySelectorAll('.rail-item[data-id], .rail-panel-toggle[data-id]').forEach(el => {
                if (el.dataset.id) byId.set(el.dataset.id, el);
            });

            const visible = resolveVisibleRailIds(layout, colEl);
            visible.forEach(id => {
                const el = byId.get(id);
                if (el && rail) rail.appendChild(el);
            });
            CANONICAL_RAIL_ITEM_ORDER.forEach(id => {
                if (visible.includes(id)) return;
                const el = byId.get(id);
                if (el && stash) stash.appendChild(el);
            });
            if (addSlot && rail) rail.appendChild(addSlot);

            const footerOrder = normalizeRailFooterOrder(layout.rail_footer);
            const footerHidden = new Set(
                (Array.isArray(layout?.rail_footer_hidden) ? layout.rail_footer_hidden : [])
                    .filter(id => HIDEABLE_FOOTER_IDS.has(id)),
            );
            footerOrder.forEach(id => {
                const el = byId.get(id);
                if (el && footer) {
                    footer.appendChild(el);
                    if (el.classList.contains('rail-item')) el.hidden = footerHidden.has(id);
                }
            });
        }

        function applyLayoutToAllColumns(layout) {
            doc.querySelectorAll('.split-column').forEach(col => applyLayoutToColumn(col, layout));
            applyAgentFeedAvailability();
            if (typeof win.ensureCuttleMobileApkUpdateUi === 'function') {
                win.ensureCuttleMobileApkUpdateUi();
            }
        }

        function commitUILayout(layout) {
            lastUILayout = {
                rail_items: Array.isArray(layout?.rail_items) ? layout.rail_items.slice() : CANONICAL_RAIL_ITEM_ORDER.slice(),
                rail_footer: Array.isArray(layout?.rail_footer) ? layout.rail_footer.slice() : CANONICAL_RAIL_FOOTER_ORDER.slice(),
                rail_hidden: Array.isArray(layout?.rail_hidden) ? layout.rail_hidden.slice() : [],
                rail_footer_hidden: Array.isArray(layout?.rail_footer_hidden) ? layout.rail_footer_hidden.slice() : [],
                layout_version: RAIL_LAYOUT_VERSION,
            };
            applyLayoutToAllColumns(lastUILayout);
            cacheUILayout(lastUILayout);
            broadcastAppsList();
            fetchImpl('/api/settings/ui-layout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(lastUILayout)
            }).catch(() => {});
        }

        function cacheUILayout(layout) {
            try { win.localStorage.setItem(STORAGE_UI_LAYOUT, JSON.stringify(layout)); } catch (_) {}
        }

        function readCachedUILayout() {
            try {
                const raw = JSON.parse(win.localStorage.getItem(STORAGE_UI_LAYOUT) || 'null');
                return raw && typeof raw === 'object' ? raw : null;
            } catch (_) {
                return null;
            }
        }

        /**
         * Saved layouts from before the Apps page get the new default-hidden apps
         * stashed once; later user pins survive because the version is then current.
         */
        function migrateUILayout(saved) {
            const layout = { ...DEFAULT_LAYOUT, ...saved };
            if (!Array.isArray(saved.rail_hidden)) delete layout.rail_hidden;
            layout.rail_footer_hidden = Array.isArray(saved.rail_footer_hidden)
                ? saved.rail_footer_hidden.filter(id => HIDEABLE_FOOTER_IDS.has(id))
                : [];
            if ((Number(saved.layout_version) || 0) >= RAIL_LAYOUT_VERSION) return { layout, changed: false };
            const hidden = new Set(Array.isArray(saved.rail_hidden) ? saved.rail_hidden : []);
            // Existing v4 user pins remain intact; only the new App starts stashed.
            const version = Number(saved.layout_version) || 0;
            const additions = version >= 9 ? ['nav-test-lab']
                : version >= 8 ? ['nav-mesh', 'nav-test-lab']
                : version >= 7 ? ['nav-agent-feed'] : version >= 6 ? ['nav-gizmos', 'nav-agent-feed']
                : version >= 5 ? ['nav-projects', 'nav-gizmos', 'nav-agent-feed']
                : version >= 4 ? ['nav-achievements', 'nav-projects', 'nav-gizmos', 'nav-agent-feed']
                : DEFAULT_RAIL_HIDDEN;
            additions.forEach(id => hidden.add(id));
            hidden.add('nav-test-lab');
            const known = new Set(CANONICAL_RAIL_ITEM_ORDER);
            layout.rail_hidden = Array.from(hidden).filter(id => known.has(id));
            layout.rail_items = (Array.isArray(saved.rail_items) ? saved.rail_items : CANONICAL_RAIL_ITEM_ORDER)
                .filter(id => known.has(id) && !hidden.has(id));
            layout.layout_version = RAIL_LAYOUT_VERSION;
            return { layout, changed: true };
        }

        function applyExperimentalRailAvailability() {
            Object.keys(EXPERIMENTAL_RAIL_APPS).forEach(id => {
                const on = enabledFlags.has(EXPERIMENTAL_RAIL_APPS[id]);
                doc.querySelectorAll(`[data-id="${id}"]`).forEach(el => {
                    el.style.setProperty('display', on ? '' : 'none', 'important');
                });
            });
        }
        // Compatibility name (app_shell + window contract call it by this name).
        const applyAgentFeedAvailability = applyExperimentalRailAvailability;
        async function refreshExperimentalRailAvailability() {
            try {
                const response = await fetchImpl('/api/experimental/flags', {credentials:'include', cache:'no-store'});
                if (!response.ok) throw new Error('flags unavailable');
                const data = await response.json();
                enabledFlags = new Set(data.kill_switch ? []
                    : (data.flags || []).filter(flag => flag.enabled).map(flag => flag.id));
            } catch (_) { enabledFlags = new Set(); }
            applyExperimentalRailAvailability();
            broadcastAppsList();
        }
        // Compatibility name (app_shell + window contract call it by this name).
        const refreshAgentFeedAvailability = refreshExperimentalRailAvailability;

        // ── Cuttle web apps (Apps page ↔ blade bar) ─────────────────────
        // Footer utilities live outside the pinnable rail order but belong in the
        // Apps grid too. panelToggle is view chrome (collapse sidebar), not an app.
        const RAIL_FOOTER_APP_IDS = ['nav-account', 'nav-notifications', 'nav-workspace', 'nav-settings'];
        /** Every page app on the rail or in the stash, alphabetical like an app drawer. */
        function getAppsList() {
            const col = getColumnEl(0);
            if (!col) return [];
            const apps = [];
            CANONICAL_RAIL_ITEM_ORDER.forEach(id => {
                if (RAIL_LOCKED_IDS.has(id) || !railAppEnabled(id)) return;
                const el = col.querySelector(`.rail-items .rail-item[data-id="${id}"], .rail-stash .rail-item[data-id="${id}"]`);
                if (!el || !el.dataset.page) return;
                apps.push({
                    id,
                    label: el.dataset.tooltip || id.replace(/^nav-/, ''),
                    page: el.dataset.page,
                    icon: el.querySelector('svg')?.outerHTML || '',
                    pinned: !!el.closest('.rail-items'),
                });
            });
            RAIL_FOOTER_APP_IDS.forEach(id => {
                const el = col.querySelector(`.rail-footer .rail-item[data-id="${id}"]`);
                if (!el) return;
                // The Account button renders the user's profile photo (or initials)
                // inside .rail-account-avatar, not as an <svg> — forward that so the
                // Apps grid tile shows the same picture as the rail.
                let iconHtml = '';
                if (id === 'nav-account') {
                    const avatar = el.querySelector('.rail-account-avatar');
                    iconHtml = (avatar && avatar.innerHTML) || el.querySelector('svg')?.outerHTML || '';
                } else {
                    iconHtml = el.querySelector('svg')?.outerHTML || '';
                }
                const entry = {
                    id,
                    label: el.dataset.tooltip || id.replace(/^nav-/, ''),
                    icon: iconHtml,
                    pinned: !el.hidden,
                    pinnable: HIDEABLE_FOOTER_IDS.has(id),
                };
                if (el.dataset.page) entry.page = el.dataset.page;
                else entry.action = id;
                apps.push(entry);
            });
            apps.sort((a, b) => a.label.localeCompare(b.label));
            return apps;
        }

        function broadcastAppsList() {
            broadcastToFrames({ type: 'cuttle-apps', apps: getAppsList() });
        }

        function saveUILayout(fromCol) {
            commitUILayout(getLayoutFromDOM(fromCol || getColumnEl(0)));
        }

        function hideRailAddPicker() {
            const picker = doc.getElementById('railAddPicker');
            if (picker) picker.hidden = true;
        }

        function setRailEditing(on) {
            railEditing = !!on;
            doc.querySelectorAll('.icon-rail').forEach(r => r.classList.toggle('rail-editing', railEditing));
            doc.querySelectorAll('.rail-edit-scrim').forEach(s => { s.hidden = !railEditing; });
            if (!railEditing) hideRailAddPicker();
        }

        /**
         * Transparent tap catcher over page content while the rail is in edit mode.
         * Taps inside the content iframe never reach the shell document, so without
         * this there is no tap-outside-to-exit on most of the screen. Tapping the
         * scrim (or Done) leaves edit mode; the tap itself is swallowed.
         */
        function ensureRailEditScrim(colEl) {
            const main = colEl?.querySelector?.('.shell-main');
            if (!main || main.querySelector('.rail-edit-scrim')) return;
            const scrim = doc.createElement('div');
            scrim.className = 'rail-edit-scrim';
            scrim.hidden = !railEditing;
            const done = doc.createElement('button');
            done.type = 'button';
            done.className = 'rail-edit-done';
            done.textContent = 'Done';
            done.setAttribute('aria-label', 'Done editing sidebar');
            done.addEventListener('click', (e) => {
                e.stopPropagation();
                setRailEditing(false);
            });
            scrim.appendChild(done);
            scrim.addEventListener('pointerdown', (e) => {
                if (e.target !== scrim) return;
                setRailEditing(false);
            });
            main.appendChild(scrim);
        }

        function ensureRailEditChrome(colEl) {
            const rail = colEl?.querySelector?.('.rail-items');
            if (!rail) return;
            let stash = colEl.querySelector('.rail-stash');
            if (!stash) {
                stash = doc.createElement('div');
                stash.className = 'rail-stash';
                stash.hidden = true;
                rail.insertAdjacentElement('afterend', stash);
            }
            let addSlot = rail.querySelector('.rail-add-slot');
            if (!addSlot) {
                addSlot = doc.createElement('button');
                addSlot.type = 'button';
                addSlot.className = 'rail-add-slot';
                addSlot.dataset.tooltip = 'Add page';
                addSlot.setAttribute('aria-label', 'Add page to sidebar');
                addSlot.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
                rail.appendChild(addSlot);
            }
            colEl.querySelectorAll('.rail-items .rail-item[data-id], .rail-stash .rail-item[data-id], .rail-footer .rail-item[data-id]').forEach(btn => {
                const id = btn.dataset.id;
                const isFooter = !!btn.closest('.rail-footer');
                const removable = isFooter
                    ? HIDEABLE_FOOTER_IDS.has(id)
                    : (CANONICAL_RAIL_ITEM_ORDER.includes(id) && !RAIL_LOCKED_IDS.has(id));
                if (!removable) return;
                if (btn.querySelector('.rail-item-remove')) return;
                if (isFooter) btn.classList.add('rail-item-removable');
                const rm = doc.createElement('span');
                rm.className = 'rail-item-remove';
                rm.setAttribute('role', 'button');
                rm.setAttribute('aria-label', 'Remove from sidebar');
                rm.textContent = '×';
                btn.appendChild(rm);
            });
        }

        function hideRailItem(id) {
            if (HIDEABLE_FOOTER_IDS.has(id)) {
                const layout = getLayoutFromDOM(getColumnEl(0));
                if (!layout.rail_footer_hidden.includes(id)) layout.rail_footer_hidden.push(id);
                commitUILayout(layout);
                return;
            }
            if (!CANONICAL_RAIL_ITEM_ORDER.includes(id) || RAIL_LOCKED_IDS.has(id)) return;
            const layout = getLayoutFromDOM(getColumnEl(0));
            layout.rail_items = layout.rail_items.filter(x => x !== id);
            if (!layout.rail_hidden.includes(id)) layout.rail_hidden.push(id);
            commitUILayout(layout);
        }

        function showRailItem(id) {
            if (HIDEABLE_FOOTER_IDS.has(id)) {
                const layout = getLayoutFromDOM(getColumnEl(0));
                layout.rail_footer_hidden = layout.rail_footer_hidden.filter(x => x !== id);
                commitUILayout(layout);
                return;
            }
            if (!CANONICAL_RAIL_ITEM_ORDER.includes(id)) return;
            const layout = getLayoutFromDOM(getColumnEl(0));
            layout.rail_hidden = layout.rail_hidden.filter(x => x !== id);
            if (!layout.rail_items.includes(id)) {
                const appsIdx = layout.rail_items.indexOf('nav-apps');
                if (appsIdx >= 0) layout.rail_items.splice(appsIdx, 0, id);
                else layout.rail_items.push(id);
            }
            commitUILayout(layout);
        }

        function openRailAddPicker(anchor) {
            const picker = doc.getElementById('railAddPicker');
            const list = picker?.querySelector('.rail-add-picker-list');
            if (!picker || !list) return;
            const layout = getLayoutFromDOM(getColumnEl(0));
            const hiddenIds = (layout.rail_hidden || []).filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id) && railAppEnabled(id));
            list.innerHTML = '';
            if (!hiddenIds.length) {
                const empty = doc.createElement('div');
                empty.className = 'rail-add-picker-empty';
                empty.textContent = 'All pages are on the bar';
                list.appendChild(empty);
            } else {
                const sourceCol = getColumnEl(0);
                hiddenIds.forEach(id => {
                    const src = sourceCol?.querySelector(`.rail-stash .rail-item[data-id="${id}"]`);
                    const btn = doc.createElement('button');
                    btn.type = 'button';
                    btn.className = 'rail-add-picker-item';
                    btn.setAttribute('role', 'menuitem');
                    const icon = src?.querySelector('svg')?.cloneNode(true);
                    if (icon) btn.appendChild(icon);
                    const label = doc.createElement('span');
                    label.textContent = src?.dataset?.tooltip || id.replace(/^nav-/, '');
                    btn.appendChild(label);
                    btn.addEventListener('click', () => {
                        showRailItem(id);
                        hideRailAddPicker();
                    });
                    list.appendChild(btn);
                });
            }
            picker.hidden = false;
            const rect = anchor.getBoundingClientRect();
            const pad = 8;
            let left = rect.right + 10;
            let top = rect.top;
            picker.style.left = '0px';
            picker.style.top = '0px';
            const pw = picker.offsetWidth || 180;
            const ph = picker.offsetHeight || 120;
            if (left + pw > win.innerWidth - pad) left = Math.max(pad, rect.left - pw - 10);
            if (top + ph > win.innerHeight - pad) top = Math.max(pad, win.innerHeight - ph - pad);
            picker.style.left = `${Math.round(left)}px`;
            picker.style.top = `${Math.round(top)}px`;
        }

        function ensureRailCustomizeGlobals() {
            if (railCustomizeGlobalsReady) return;
            railCustomizeGlobalsReady = true;
            doc.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') setRailEditing(false);
            });
            doc.addEventListener('pointerdown', (e) => {
                if (!railEditing) return;
                if (e.target.closest('.icon-rail, .rail-add-picker')) return;
                setRailEditing(false);
            });
            doc.addEventListener('click', (e) => {
                if (!railSuppressClick) return;
                e.preventDefault();
                e.stopPropagation();
                railSuppressClick = false;
            }, true);
        }

        function setupRailCustomize(colEl) {
            if (!colEl) return;
            ensureRailEditChrome(colEl);
            ensureRailCustomizeGlobals();
            const nav = colEl.querySelector('.icon-rail');
            if (!nav || nav.dataset.railCustomize === '1') return;
            nav.dataset.railCustomize = '1';

            let holdTimer = null;
            let holdStart = null;

            const clearHold = () => {
                if (holdTimer) {
                    clearTimeout(holdTimer);
                    holdTimer = null;
                }
                holdStart = null;
            };

            nav.addEventListener('pointerdown', (e) => {
                if (e.target.closest('.rail-item-remove')) {
                    e.stopPropagation();
                    return;
                }
                if (e.button != null && e.button !== 0) return;
                if (e.target.closest('.rail-add-slot, .rail-logo')) return;
                const item = e.target.closest('.rail-item[data-id]');
                if (!item || !nav.contains(item)) return;
                holdStart = { x: e.clientX, y: e.clientY };
                holdTimer = setTimeout(() => {
                    holdTimer = null;
                    setRailEditing(true);
                    railSuppressClick = true;
                }, RAIL_HOLD_MS);
            });

            nav.addEventListener('pointermove', (e) => {
                if (!holdStart) return;
                const dx = e.clientX - holdStart.x;
                const dy = e.clientY - holdStart.y;
                if ((dx * dx) + (dy * dy) > (RAIL_HOLD_MOVE_PX * RAIL_HOLD_MOVE_PX)) clearHold();
            });

            nav.addEventListener('pointerup', clearHold);
            nav.addEventListener('pointercancel', clearHold);
            nav.addEventListener('dragstart', clearHold, true);

            nav.addEventListener('click', (e) => {
                const rm = e.target.closest('.rail-item-remove');
                if (rm) {
                    e.preventDefault();
                    e.stopPropagation();
                    const item = rm.closest('.rail-item[data-id]');
                    if (item) hideRailItem(item.dataset.id);
                    return;
                }
                const add = e.target.closest('.rail-add-slot');
                if (add) {
                    e.preventDefault();
                    e.stopPropagation();
                    openRailAddPicker(add);
                }
            });
        }

        function setupLayoutDragDrop(colEl) {
            if (!colEl) return;
            let draggedEl = null;

            const dragStart = (e) => {
                if (!e.target.closest('[draggable="true"]')) return;
                draggedEl = e.target.closest('[draggable="true"]');
                draggedEl.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', draggedEl.dataset.id || draggedEl.id || '');
                e.dataTransfer.setData('cuttle/source', (e.target.closest('[data-droppable]') || {}).dataset?.droppable || '');
            };

            const dragEnd = () => {
                if (draggedEl) draggedEl.classList.remove('dragging');
                draggedEl = null;
                colEl.querySelectorAll('[data-droppable].drop-target').forEach(z => z.classList.remove('drop-target'));
            };

            const dragOver = (e) => {
                const dz = e.target.closest('[data-droppable]');
                if (!dz) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                colEl.querySelectorAll('[data-droppable].drop-target').forEach(z => z.classList.remove('drop-target'));
                dz.classList.add('drop-target');
            };

            const drop = (e) => {
                e.preventDefault();
                const dz = e.target.closest('[data-droppable]');
                if (!dz || !draggedEl) return;
                const selector = '.rail-item[data-id], .rail-panel-toggle[data-id]';
                let target = e.target.closest(selector);
                if (target === draggedEl) target = null;
                if (target) {
                    const rect = target.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    if (e.clientY < midY) dz.insertBefore(draggedEl, target);
                    else dz.insertBefore(draggedEl, target.nextSibling);
                } else {
                    dz.appendChild(draggedEl);
                }
                dz.classList.remove('drop-target');
                saveUILayout(colEl);
            };

            const dragLeave = (e) => {
                const left = e.target.closest('[data-droppable]');
                if (left && !left.contains(e.relatedTarget)) left.classList.remove('drop-target');
            };

            colEl.addEventListener('dragstart', dragStart, true);
            colEl.addEventListener('dragend', dragEnd, true);
            colEl.addEventListener('dragover', dragOver, true);
            colEl.addEventListener('dragleave', dragLeave, true);
            colEl.addEventListener('drop', drop, true);
        }

        /** Wire the rail: agent-feed availability, the Apps message contract and
         *  the persisted layout load. Called once by app_shell. */
        function setup() {
            // Experiment availability does not change the user's saved pin
            // preferences; re-check on focus and when flags change.
            win.addEventListener('focus', refreshAgentFeedAvailability);
            win.addEventListener('message', e => {
                if (e.origin === win.location.origin && e.data?.type === 'cuttle-experimental-flags-changed') refreshAgentFeedAvailability();
            });
            refreshAgentFeedAvailability();

            win.addEventListener('message', (e) => {
                const d = e.data;
                if (!d || typeof d !== 'object') return;
                if (d.type === 'cuttle-apps-request') {
                    try { e.source?.postMessage({ type: 'cuttle-apps', apps: getAppsList() }, '*'); } catch (_) {}
                } else if (d.type === 'cuttle-apps-pin' && typeof d.id === 'string') {
                    if (d.pinned) showRailItem(d.id);
                    else hideRailItem(d.id);
                } else if (d.type === 'cuttle-rail-action' && typeof d.id === 'string') {
                    // Apps-grid tiles for footer utilities (Account, Notifications,
                    // Workspace) carry no page. Activate the rail button so the same
                    // popover/modal handlers run. Allowlisted to footer app ids only.
                    if (!RAIL_FOOTER_APP_IDS.includes(d.id)) return;
                    getColumnEl(0)?.querySelector(`.rail-footer .rail-item[data-id="${d.id}"]`)?.click();
                }
            });

            // Paint the last known layout now so stashed apps don't flash on the
            // rail while the server copy loads.
            const cached = readCachedUILayout();
            lastUILayout = cached ? migrateUILayout(cached).layout : { ...DEFAULT_LAYOUT };
            applyLayoutToAllColumns(lastUILayout);

            // Load saved layout on init — apply to every blade so splits stay in sync.
            fetchImpl('/api/settings/ui-layout', { cache: 'no-store' })
                .then(r => r.ok ? r.json() : null)
                .then(data => {
                    const saved = data?.ui_layout;
                    if (saved && Object.keys(saved).length > 0) {
                        const { layout, changed } = migrateUILayout(saved);
                        if (changed) {
                            commitUILayout(layout);
                            return;
                        }
                        lastUILayout = layout;
                    } else {
                        lastUILayout = { ...DEFAULT_LAYOUT };
                    }
                    applyLayoutToAllColumns(lastUILayout);
                    cacheUILayout(lastUILayout);
                    broadcastAppsList();
                })
                .catch(() => {})
                .finally(() => {
                    setupLayoutDragDrop(getColumnEl(0));
                });
        }

        // Window contracts the shell page + e2e consume by bare name.
        win.refreshAgentFeedAvailability = refreshAgentFeedAvailability;
        win.applyAgentFeedAvailability = applyAgentFeedAvailability;
        Object.defineProperty(win, 'lastUILayout', {
            configurable: true,
            get: () => lastUILayout,
        });

        return {
            // Layout + persistence.
            applyLayoutToColumn,
            applyLayoutToAllColumns,
            commitUILayout,
            saveUILayout,
            migrateUILayout,
            // Edit mode + customization.
            setRailEditing,
            hideRailItem,
            showRailItem,
            ensureRailEditScrim,
            setupRailCustomize,
            setupLayoutDragDrop,
            interactionLocked,
            // Apps list.
            getAppsList,
            broadcastAppsList,
            // Agent Feed rail availability.
            applyAgentFeedAvailability,
            refreshAgentFeedAvailability,
            // Init.
            setup,
            // Canonical order + flags (tests + callers).
            CANONICAL_RAIL_ITEM_ORDER,
            CANONICAL_RAIL_FOOTER_ORDER,
            RAIL_LOCKED_IDS,
            HIDEABLE_FOOTER_IDS,
            DEFAULT_RAIL_HIDDEN,
            RAIL_LAYOUT_VERSION,
            RAIL_FOOTER_APP_IDS,
        };
    }

    root.CuttleShellRail = { create };
})(typeof window !== 'undefined' ? window : globalThis);
