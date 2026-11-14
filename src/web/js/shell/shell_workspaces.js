/* ================================================================
   Cuttle shell — saved workspaces popover (shell_workspaces.js)
   Owner: the rail "Workspace" popover — save the current split/spaces
   under a name, update a saved one in place, rename, load, delete.

   Saved workspaces live server-side (/api/shell/workspaces, so they load
   on another device). The workspace this device last loaded or saved is
   remembered locally as the *current* one: the popover offers "Update"
   for it, so re-saving never means retyping its name.

   Layout snapshot/apply, the shared backdrop and the other popovers are
   host-owned and injected; this module never reads shell globals.
   Loaded before app_shell.js in src/web/app_shell.html.
   ================================================================ */
(function (root) {
    'use strict';

    const DEFAULT_WORKSPACE_ID = '__default__';
    const ACTIVE_KEY = 'cuttle.shell.activeWorkspace';
    const ARM_MS = 3000;

    function builtinDefault() {
        return {
            id: DEFAULT_WORKSPACE_ID,
            name: 'Default',
            summary: '1 pane · single viewport',
            builtin: true,
            columns: [{ page: '/chat_page.html' }],
            orientation: 'horizontal',
            root: { type: 'leaf', page: '/chat_page.html', flex: '' },
        };
    }

    function isDefault(ws) {
        return !!ws && (ws.builtin || String(ws.id) === DEFAULT_WORKSPACE_ID);
    }

    function normName(name) {
        return String(name || '').trim().toLowerCase();
    }

    function columnLabel(entry, pageTitles, origin) {
        const page = (entry && entry.page) || '';
        try {
            const url = new URL(page, origin || 'https://local');
            const titles = pageTitles || {};
            const title = titles[url.pathname]
                || (url.pathname.replace(/^\//, '').replace(/\.html$/, '') || 'Pane');
            const cid = url.searchParams.get('chat') || url.searchParams.get('session')
                || (entry && (entry.chat || entry.terminal));
            if (cid) {
                const raw = String(cid);
                const display = /^\d+$/.test(raw) ? ('CH-' + raw.padStart(6, '0')) : raw;
                return title + ' ' + display;
            }
            return title;
        } catch (_) {
            return page || 'Pane';
        }
    }

    function formatSummary(columns, pageTitles, origin) {
        const cols = Array.isArray(columns) ? columns : [];
        const n = Math.max(1, cols.length);
        const labels = cols.map((c) => columnLabel(c, pageTitles, origin)).filter(Boolean);
        return n + ' ' + (n === 1 ? 'pane' : 'panes') + (labels.length ? (' · ' + labels.join(' · ')) : '');
    }

    function formatTime(ts) {
        const n = Number(ts);
        if (!Number.isFinite(n) || n <= 0) return '';
        try {
            return new Date(n * 1000).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
            });
        } catch (_) {
            return '';
        }
    }

    /** Saved workspaces minus anything shadowing the built-in Default. */
    function savedOnly(list) {
        return (Array.isArray(list) ? list : []).filter((ws) => ws && ws.id
            && String(ws.id) !== DEFAULT_WORKSPACE_ID && normName(ws.name) !== 'default');
    }

    function findByName(list, name, exceptId) {
        const key = normName(name);
        if (!key) return null;
        return savedOnly(list).find((ws) => normName(ws.name) === key
            && String(ws.id) !== String(exceptId || '')) || null;
    }

    /** What the "save as new" button would do with this name.
     *  kind: 'empty' | 'reserved' | 'new' | 'replace' (another saved one has it). */
    function saveIntent(list, name) {
        const trimmed = String(name || '').trim();
        if (!trimmed) return { kind: 'empty', name: '' };
        if (normName(trimmed) === 'default') return { kind: 'reserved', name: trimmed };
        const match = findByName(list, trimmed);
        return match ? { kind: 'replace', name: trimmed, match } : { kind: 'new', name: trimmed };
    }

    function create(host) {
        const win = host.win || root;
        const doc = host.doc || win.document;
        const storage = host.storage || (() => { try { return win.localStorage; } catch (_) { return null; } })();
        const escapeHtml = host.escapeHtml;
        const pageTitles = host.pageTitles || {};
        const origin = (win.location && win.location.origin) || undefined;
        const fetchImpl = host.fetch || ((url, opts) => win.fetch(url, opts));
        const toast = (msg, kind) => {
            if (typeof win.showToast === 'function') win.showToast(msg, kind);
        };

        let saved = [];
        let busy = false;
        let renamingId = null;
        const armed = new Map(); // `${action}:${id}` → timer

        const $ = (id) => doc.getElementById(id);
        const summaryOf = (columns) => formatSummary(columns, pageTitles, origin);

        function readActiveId() {
            try { return (storage && storage.getItem(ACTIVE_KEY)) || ''; } catch (_) { return ''; }
        }
        function writeActiveId(id) {
            try {
                if (!storage) return;
                if (id) storage.setItem(ACTIVE_KEY, String(id));
                else storage.removeItem(ACTIVE_KEY);
            } catch (_) {}
        }
        function activeWorkspace() {
            const id = readActiveId();
            return id ? (saved.find((ws) => String(ws.id) === id) || null) : null;
        }

        // ── open / close ───────────────────────────────────────────
        function isOpen() {
            const pop = $('workspacePopover');
            return !!(pop && pop.classList.contains('notifications-popover-open'));
        }
        function syncBackdrop() {
            const back = $('notificationsPopoverBackdrop');
            if (!back) return;
            const on = isOpen() || (host.isOtherPopoverOpen && host.isOtherPopoverOpen());
            back.classList.toggle('notifications-popover-backdrop-visible', !!on);
        }
        function open() {
            if (host.closeOtherPopovers) host.closeOtherPopovers();
            const pop = $('workspacePopover');
            if (pop) pop.classList.add('notifications-popover-open');
            renamingId = null;
            syncBackdrop();
            refresh();
        }
        function close() {
            const pop = $('workspacePopover');
            if (pop) pop.classList.remove('notifications-popover-open');
            renamingId = null;
            disarmAll();
            syncBackdrop();
        }
        function toggle() {
            if (isOpen()) close();
            else open();
        }

        // ── server calls ───────────────────────────────────────────
        async function request(url, opts) {
            const res = await fetchImpl(url, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opts || {}));
            if (res.status === 404 && (!opts || !opts.method)) return { success: true, workspaces: [] };
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.success) throw new Error(data.error || 'Workspace request failed');
            return data;
        }
        function postJson(body) {
            return request('/api/shell/workspaces', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        }
        async function withBusy(fn) {
            if (busy) return;
            busy = true;
            renderControls();
            try { await fn(); } finally { busy = false; render(); }
        }

        // ── actions ────────────────────────────────────────────────
        /** Save the current layout. `target` set → overwrite that workspace in place (keeps its name). */
        function save(target) {
            const input = $('workspaceNameInput');
            const intent = target ? { kind: 'update', name: target.name, match: target } : saveIntent(saved, input && input.value);
            if (intent.kind === 'empty') { if (input) input.focus(); return Promise.resolve(); }
            if (intent.kind === 'reserved') {
                toast('Default is built-in and cannot be overwritten', 'info');
                return Promise.resolve();
            }
            return withBusy(async () => {
                const layout = host.snapshot();
                const body = Object.assign({ name: intent.name }, layout);
                if (intent.match) body.id = intent.match.id;
                try {
                    const data = await postJson(body);
                    saved = savedOnly(data.workspaces);
                    if (data.workspace && data.workspace.id) writeActiveId(data.workspace.id);
                    if (input && !target) input.value = '';
                    const verb = intent.kind === 'new' ? 'Saved' : 'Updated';
                    toast(verb + ' ' + intent.name + ' · ' + summaryOf(layout.columns), 'success');
                } catch (err) {
                    toast((err && err.message) || 'Save failed', 'error');
                }
            });
        }

        function rename(ws, nextName) {
            const name = String(nextName || '').trim().slice(0, 80);
            renamingId = null;
            if (!name || name === ws.name) { render(); return Promise.resolve(); }
            if (normName(name) === 'default') {
                toast('Default is reserved for the built-in workspace', 'info');
                render();
                return Promise.resolve();
            }
            return withBusy(async () => {
                try {
                    // No `columns` → the server renames only and keeps the saved layout.
                    const data = await postJson({ id: ws.id, name });
                    saved = savedOnly(data.workspaces);
                    toast('Renamed to ' + name, 'success');
                } catch (err) {
                    toast((err && err.message) || 'Rename failed', 'error');
                }
            });
        }

        function load(ws) {
            if (isDefault(ws)) {
                if (!host.applyDefault()) return;
                writeActiveId('');
                close();
                toast('Loaded Default · 1 pane', 'success');
                return;
            }
            if (!host.apply(ws)) {
                toast('Could not load workspace', 'error');
                return;
            }
            writeActiveId(ws.id);
            close();
            toast('Loaded ' + (ws.name || 'workspace') + ' · ' + (ws.summary || summaryOf(ws.columns)), 'success');
        }

        function remove(ws) {
            return withBusy(async () => {
                try {
                    const data = await request('/api/shell/workspaces/' + encodeURIComponent(ws.id), { method: 'DELETE' });
                    saved = savedOnly(data.workspaces);
                    if (readActiveId() === String(ws.id)) writeActiveId('');
                    toast('Deleted ' + (ws.name || 'workspace'), 'info');
                } catch (err) {
                    toast((err && err.message) || 'Delete failed', 'error');
                }
            });
        }

        // Two-click confirm for actions that overwrite or drop a saved workspace.
        function disarmAll() {
            armed.forEach((t) => clearTimeout(t));
            armed.clear();
        }
        function armOrRun(key, btn, run) {
            if (armed.has(key)) {
                clearTimeout(armed.get(key));
                armed.delete(key);
                run();
                return;
            }
            disarmAll();
            armed.set(key, setTimeout(() => { armed.delete(key); render(); }, ARM_MS));
            render();
        }

        // ── render ─────────────────────────────────────────────────
        function renderControls() {
            const input = $('workspaceNameInput');
            const btn = $('workspaceSaveBtn');
            const note = $('workspaceSaveNote');
            const intent = saveIntent(saved, input && input.value);
            if (btn) {
                btn.disabled = busy || intent.kind === 'empty' || intent.kind === 'reserved';
                btn.textContent = intent.kind === 'replace' ? 'Replace' : 'Save new';
            }
            if (note) {
                let text = '';
                if (intent.kind === 'replace') text = 'Overwrites the saved "' + intent.match.name + '" with this layout.';
                else if (intent.kind === 'reserved') text = 'Default is built-in; pick another name.';
                note.textContent = text;
                note.hidden = !text;
            }
            const updateBtn = $('workspaceUpdateBtn');
            if (updateBtn) updateBtn.disabled = busy;
        }

        function renderCurrent() {
            const summaryEl = $('workspaceCurrentSummary');
            const nameEl = $('workspaceCurrentName');
            const updateBtn = $('workspaceUpdateBtn');
            const current = activeWorkspace();
            if (summaryEl) summaryEl.textContent = summaryOf(host.snapshotColumns());
            if (nameEl) {
                nameEl.textContent = current ? current.name : 'Unsaved layout';
                nameEl.classList.toggle('is-unsaved', !current);
            }
            if (updateBtn) {
                updateBtn.hidden = !current;
                updateBtn.textContent = current ? 'Update' : '';
                updateBtn.title = current ? ('Save this layout over "' + current.name + '"') : '';
            }
        }

        function rowHtml(ws, activeId) {
            const builtin = isDefault(ws);
            const id = String(ws.id || '');
            const isActive = !builtin && id === activeId;
            const esc = (s) => escapeHtml(String(s == null ? '' : s));
            const when = builtin ? '' : formatTime(ws.updated_at);
            const badges = (builtin ? '<span class="workspace-item-badge">built-in</span>' : '')
                + (isActive ? '<span class="workspace-item-badge workspace-item-badge-current">current</span>' : '');
            const nameHtml = renamingId === id
                ? '<input type="text" class="workspace-name-input workspace-rename-input" maxlength="80" data-workspace-rename-input value="'
                    + esc(ws.name) + '" aria-label="Rename workspace">'
                : '<span class="workspace-item-title">' + esc(ws.name || 'Workspace') + '</span>' + badges;
            const btn = (action, label, cls, title) => {
                const isArmed = armed.has(action + ':' + id);
                return '<button type="button" class="workspace-btn' + (cls ? ' ' + cls : '') + (isArmed ? ' is-armed' : '')
                    + '" data-workspace-' + action + (title ? ' title="' + esc(title) + '"' : '')
                    + (busy ? ' disabled' : '') + '>' + esc(isArmed ? 'Confirm' : label) + '</button>';
            };
            const actions = builtin
                ? btn('load', 'Load', 'workspace-btn-primary', 'Collapse to a single viewport')
                : btn('load', 'Load', 'workspace-btn-primary', 'Open this workspace')
                    + btn('update', 'Update', '', 'Save the current layout over this one')
                    + btn('rename', 'Rename', '', 'Rename (keeps its layout)')
                    + btn('delete', 'Delete', 'workspace-btn-danger', 'Delete this workspace');
            return '<div class="workspace-item' + (builtin ? ' workspace-item-builtin' : '') + (isActive ? ' is-current' : '')
                + '" data-workspace-id="' + esc(id) + '">'
                + '<div class="workspace-item-main">'
                + '<div class="workspace-item-name">' + nameHtml + '</div>'
                + '<div class="workspace-item-summary">' + esc(ws.summary || summaryOf(ws.columns))
                + (when ? ' · ' + esc(when) : '') + '</div>'
                + '</div>'
                + '<div class="workspace-item-actions">' + actions + '</div></div>';
        }

        function renderList() {
            const listEl = $('workspaceList');
            const emptyEl = $('workspaceEmpty');
            if (!listEl) return;
            const activeId = readActiveId();
            const items = [builtinDefault()].concat(saved);
            listEl.innerHTML = items.map((ws) => rowHtml(ws, activeId)).join('');
            if (emptyEl && !emptyEl.dataset.error) {
                emptyEl.textContent = 'No saved workspaces yet — name this layout above to save it.';
                emptyEl.style.display = saved.length ? 'none' : 'block';
            }
            const input = listEl.querySelector('[data-workspace-rename-input]');
            if (input) {
                input.focus();
                input.select();
            }
        }

        function render() {
            renderCurrent();
            renderControls();
            renderList();
        }

        async function refresh() {
            const emptyEl = $('workspaceEmpty');
            if (emptyEl) delete emptyEl.dataset.error;
            render();
            try {
                saved = savedOnly((await request('/api/shell/workspaces')).workspaces);
            } catch (err) {
                // Keep the built-in Default usable when the API is unreachable.
                saved = [];
                if (emptyEl) {
                    emptyEl.dataset.error = '1';
                    emptyEl.style.display = 'block';
                    emptyEl.textContent = (err && err.message) || 'Could not load workspaces';
                }
            }
            render();
        }

        // ── wiring ─────────────────────────────────────────────────
        function rowWorkspace(el) {
            const row = el && el.closest('.workspace-item');
            const id = row && row.dataset.workspaceId;
            if (!id) return null;
            if (id === DEFAULT_WORKSPACE_ID) return builtinDefault();
            return saved.find((ws) => String(ws.id) === id) || null;
        }

        function onListClick(e) {
            const btn = e.target.closest('button');
            if (!btn || btn.disabled) return;
            const ws = rowWorkspace(btn);
            if (!ws) return;
            const id = String(ws.id);
            if (btn.hasAttribute('data-workspace-load')) load(ws);
            else if (btn.hasAttribute('data-workspace-update')) armOrRun('update:' + id, btn, () => save(ws));
            else if (btn.hasAttribute('data-workspace-delete')) armOrRun('delete:' + id, btn, () => remove(ws));
            else if (btn.hasAttribute('data-workspace-rename')) {
                disarmAll();
                renamingId = id;
                renderList();
            }
        }

        function onListKeydown(e) {
            const input = e.target.closest('[data-workspace-rename-input]');
            if (!input) return;
            const ws = rowWorkspace(input);
            if (e.key === 'Enter') {
                e.preventDefault();
                if (ws) rename(ws, input.value);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                renamingId = null;
                renderList();
            }
        }

        function onListFocusOut(e) {
            const input = e.target.closest && e.target.closest('[data-workspace-rename-input]');
            if (!input || renamingId == null) return;
            const ws = rowWorkspace(input);
            if (ws) rename(ws, input.value);
        }

        function setup() {
            $('workspaceSaveBtn')?.addEventListener('click', () => save(null));
            $('workspaceUpdateBtn')?.addEventListener('click', () => {
                const current = activeWorkspace();
                if (current) save(current);
            });
            const input = $('workspaceNameInput');
            input?.addEventListener('input', renderControls);
            input?.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    save(null);
                }
            });
            const listEl = $('workspaceList');
            listEl?.addEventListener('click', onListClick);
            listEl?.addEventListener('keydown', onListKeydown);
            listEl?.addEventListener('focusout', onListFocusOut);
        }

        return { setup, open, close, toggle, isOpen, syncBackdrop, refresh, load, save };
    }

    root.CuttleShellWorkspaces = {
        create,
        builtinDefault,
        DEFAULT_WORKSPACE_ID,
        ACTIVE_KEY,
        // Pure helpers (tests / other surfaces).
        columnLabel,
        formatSummary,
        findByName,
        saveIntent,
        savedOnly,
    };
})(typeof window !== 'undefined' ? window : globalThis);
