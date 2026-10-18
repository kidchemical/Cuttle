/* IDE App page — composition root (experimental flag `ide`).
 * Gathers the owners (CuttleIdeEditor / Explorer / Search / Quick /
 * Terminal / Chat over CuttleIdeModel + CuttleIdeApi) and owns only the
 * page lifetimes they share: the workspace, open documents (disk sync,
 * conflicts, agent-change review, hot exit), layout and keybindings. */
(function () {
    'use strict';

    const M = window.CuttleIdeModel;
    const api = window.CuttleIdeApi.create();
    const esc = M.escapeHtml;
    const $ = (id) => document.getElementById(id);
    const LAYOUT_KEY = 'cuttleIdeLayout';
    const LAST_WS_KEY = 'cuttleIdeLastWorkspace';
    const WATCH_MS = 2500;
    const inShell = window.parent && window.parent !== window;
    const navigation = window.CuttleIdeNavigation.create({
        win: window, open: params => openDeepLink(params), fail, isChatSource: source => chat.isSource(source),
    });

    let layout = M.normalizeLayout(readJson(LAYOUT_KEY));
    let workspaces = [];
    let ws = null; // {id, name, path}
    let wsState = M.normalizeWorkspaceState(null);
    let tabs = M.emptyTabs();
    const docs = new Map(); // path → doc (see loadDoc)
    let decorations = { files: {}, dirs: {} };
    let pending = null;
    let fileList = null;
    let closedStack = [];
    let editor = null;
    let diffState = null;
    let completionsReg = null;
    let watchTimer = null;
    let watchTick = 0;
    let gutterTimer = null;
    let hotExitTimer = null;
    let scmPanel = null;

    // ── small utilities ─────────────────────────────────────────────
    function readJson(key, store) {
        try { return JSON.parse((store || localStorage).getItem(key) || 'null'); } catch (_) { return null; }
    }
    function writeJson(key, value, store) {
        try { (store || localStorage).setItem(key, JSON.stringify(value)); } catch (_) {}
    }
    function toast(message, kind) {
        if (window.showToast) window.showToast(message, kind || 'info');
    }
    function fail(err) {
        const message = (err && err.message) || String(err || 'Something went wrong');
        toast(message, 'error');
        setStatusMsg(message);
    }
    function setStatusMsg(text) {
        const el = $('ideStatusMsg');
        el.textContent = text || '';
        clearTimeout(setStatusMsg.timer);
        if (text) setStatusMsg.timer = setTimeout(() => { el.textContent = ''; }, 6000);
    }
    function absPath(rel) { return ws ? ws.path.replace(/[\\/]+$/, '') + '/' + M.normPath(rel) : ''; }
    function saveLayout() { writeJson(LAYOUT_KEY, layout); }
    function wsKey() { return 'cuttleIdeWorkspace:' + (ws ? ws.path : ''); }
    function saveWsState() {
        if (!ws) return;
        wsState.tabs = tabs.tabs;
        wsState.active = tabs.active;
        wsState.mru = tabs.mru;
        wsState.expanded = explorer.expandedList();
        writeJson(wsKey(), wsState);
    }

    /** Small modal: resolves to the chosen button value (or null). */
    function ask(text, buttons) {
        const dialog = $('ideDialog');
        $('ideDialogText').textContent = text;
        const actions = $('ideDialogActions');
        actions.innerHTML = buttons.map((b, i) => '<button value="' + esc(b.value) + '"' + (b.primary ? ' class="is-primary"' : '') + (i === 0 ? ' autofocus' : '') + '>' + esc(b.label) + '</button>').join('');
        return new Promise((resolve) => {
            dialog.onclose = () => resolve(dialog.returnValue || null);
            dialog.returnValue = '';
            dialog.showModal();
        });
    }
    const confirmAction = (text, label) => ask(text, [{ label: label || 'OK', value: 'ok', primary: true }, { label: 'Cancel', value: '' }]).then((v) => v === 'ok');

    // ── layout ──────────────────────────────────────────────────────
    function applyLayout() {
        const main = $('ideMain');
        main.style.setProperty('--ide-sidebar-w', layout.sidebarWidth + 'px');
        main.style.setProperty('--ide-chat-w', layout.chatWidth + 'px');
        main.style.setProperty('--ide-panel-h', layout.panelHeight + 'px');
        $('ideSidebar').hidden = !layout.sidebar;
        document.querySelector('[data-resize="sidebar"]').hidden = !layout.sidebar;
        $('idePanel').hidden = !layout.panel;
        document.querySelector('[data-resize="panel"]').hidden = !layout.panel;
        $('ideChat').hidden = !layout.chat;
        document.querySelector('[data-resize="chat"]').hidden = !layout.chat;
        document.querySelectorAll('[data-view-panel]').forEach((el) => { el.hidden = el.dataset.viewPanel !== layout.view; });
        document.querySelectorAll('.ide-activity-btn').forEach((b) => b.classList.toggle('is-active', layout.sidebar && b.dataset.view === layout.view));
        document.querySelectorAll('[data-cmd="view.chat"].ide-ai-btn').forEach((b) => b.classList.toggle('is-active', layout.chat));
        if (editor) {
            editor.setOptions(layout);
            requestAnimationFrame(() => editor.layout());
        }
        renderStatus();
    }
    function showView(view, focus) {
        if (layout.sidebar && layout.view === view && !focus) layout.sidebar = false;
        else { layout.view = view; layout.sidebar = true; }
        saveLayout();
        applyLayout();
        if (view === 'scm' && layout.sidebar) refreshGit();
    }
    async function togglePanel(force) {
        layout.panel = force == null ? !layout.panel : force;
        saveLayout();
        applyLayout();
        if (layout.panel) {
            try { await terminal.ensure(); terminal.fit(); } catch (err) { fail(err); }
        } else if (editor) editor.focus();
    }
    async function toggleChat(force) {
        layout.chat = force == null ? !layout.chat : force;
        saveLayout();
        applyLayout();
        if (layout.chat) {
            try { await chat.ensure(); } catch (err) { fail(err); }
        }
    }
    function wireResizers() {
        document.querySelectorAll('.ide-resizer').forEach((bar) => {
            bar.addEventListener('pointerdown', (e) => {
                e.preventDefault();
                const kind = bar.dataset.resize;
                const startX = e.clientX;
                const startY = e.clientY;
                const start = { sidebar: layout.sidebarWidth, chat: layout.chatWidth, panel: layout.panelHeight }[kind];
                bar.setPointerCapture(e.pointerId);
                document.body.classList.add('ide-resizing');
                const move = (ev) => {
                    if (kind === 'sidebar') layout.sidebarWidth = Math.min(640, Math.max(160, start + ev.clientX - startX));
                    if (kind === 'chat') layout.chatWidth = Math.min(900, Math.max(280, start - (ev.clientX - startX)));
                    if (kind === 'panel') layout.panelHeight = Math.min(window.innerHeight - 160, Math.max(100, start - (ev.clientY - startY)));
                    applyLayout();
                    if (kind === 'panel') terminal.fit();
                };
                const up = () => {
                    bar.removeEventListener('pointermove', move);
                    bar.removeEventListener('pointerup', up);
                    document.body.classList.remove('ide-resizing');
                    saveLayout();
                    terminal.fit();
                };
                bar.addEventListener('pointermove', move);
                bar.addEventListener('pointerup', up);
            });
        });
    }

    // ── owners ──────────────────────────────────────────────────────
    const quick = window.CuttleIdeQuick.create({ el: $('ideQuick'), escapeHtml: esc });
    const explorer = window.CuttleIdeExplorer.create({
        el: $('ideTree'), model: M, api: api,
        getDecorations: () => decorations,
        getActive: () => tabs.active,
        onOpen: (path, o) => openFile(path, o),
        onContext: (entry, e) => showContextMenu(entry, e),
        onExpandedChange: () => saveWsState(),
        onMove: (from, to) => renameEntry(from, to),
        onRename: (entry) => promptRename(entry),
        onDelete: (entry) => deleteEntry(entry),
        onError: fail,
    });
    const search = window.CuttleIdeSearch.create({
        el: $('ideSearch'), model: M, api: api,
        getRoot: () => ws && ws.path,
        onOpenMatch: (path, m) => openFile(path, { preview: true, line: m.line, col: m.col, length: m.length }),
        onReplaced: (files, count) => {
            toast('Replaced ' + count + ' occurrence' + (count === 1 ? '' : 's') + ' in ' + files.length + ' file' + (files.length === 1 ? '' : 's'), 'success');
            pollOpenFiles();
            refreshGit();
        },
        confirm: confirmAction,
        onError: fail,
    });
    const terminal = window.CuttleIdeTerminal.create({
        tabsEl: $('ideTermTabs'), bodyEl: $('ideTermBody'),
        onEmpty: () => togglePanel(false),
    });
    const chat = window.CuttleIdeChat.create({
        frameHost: $('ideChatBody'),
        savedSession: () => wsState.chatSession,
        onSession: (id) => { wsState.chatSession = id; saveWsState(); },
    });

    // ── workspaces ──────────────────────────────────────────────────
    function renderWorkspacePicker() {
        const sel = $('ideWorkspace');
        sel.innerHTML = workspaces.map((w) => '<option value="' + esc(w.path) + '"' + (w.available ? '' : ' disabled') + '>' + esc(w.name) + (w.available ? '' : ' (unavailable)') + '</option>').join('');
        if (ws && !workspaces.some((w) => M.absKey(w.path) === M.absKey(ws.path))) {
            sel.insertAdjacentHTML('afterbegin', '<option value="' + esc(ws.path) + '">' + esc(ws.name) + '</option>');
        }
        if (ws) sel.value = ws.path;
    }
    async function openWorkspace(path, opts) {
        if (ws && M.absKey(ws.path) === M.absKey(path) && !(opts && opts.force)) return;
        if (ws && !(await confirmLeaveDirty('switching projects'))) { renderWorkspacePicker(); return; }
        if (ws) { saveWsState(); persistHotExit(); }
        const known = workspaces.find((w) => M.absKey(w.path) === M.absKey(path));
        ws = { id: known ? known.id : null, name: known ? known.name : M.basename(path) || path, path: known ? known.path : path };
        localStorage.setItem(LAST_WS_KEY, ws.path);
        renderWorkspacePicker();
        document.title = ws.name + ' — IDE — Cuttle';
        $('ideExplorerTitle').textContent = ws.name;
        // Reset document state for the new workspace.
        Array.from(docs.keys()).forEach((p) => editor.close(p));
        docs.clear();
        loading.clear();
        hideDiff();
        hideBanner();
        fileList = null;
        closedStack = [];
        decorations = { files: {}, dirs: {} };
        pending = null;
        wsState = M.normalizeWorkspaceState(readJson(wsKey()));
        tabs = { tabs: wsState.tabs, active: wsState.active, mru: wsState.mru };
        terminal.setWorkspace(ws.path);
        chat.setWorkspace(ws);
        search.reset();
        mountScm();
        await explorer.reset(ws.path, wsState.expanded);
        renderTabs();
        await restoreHotExit();
        if (tabs.active) await showDoc(tabs.active);
        else showSurface('welcome');
        refreshGit();
        if (layout.panel) terminal.ensure().catch(() => {});
        if (layout.chat) chat.ensure().catch(fail);
    }

    // ── documents ───────────────────────────────────────────────────
    /** Load a document from disk (once) and register it with the editor. */
    const loading = new Map(); // path → in-flight loadDoc promise
    function loadDoc(path) {
        if (docs.has(path)) return Promise.resolve(docs.get(path));
        if (!loading.has(path)) {
            const root = ws.path;
            loading.set(path, readDoc(path).finally(() => { if (ws && ws.path === root) loading.delete(path); }));
        }
        return loading.get(path);
    }
    async function readDoc(path) {
        const root = ws.path;
        const file = await api.read(root, path);
        if (!ws || ws.path !== root) throw new Error('Project changed');
        const doc = {
            path: path, etag: file.etag, encoding: file.encoding || 'utf-8', readOnly: !!file.readonly,
            kind: file.too_large ? 'large' : file.image ? 'image' : file.binary ? 'binary' : 'text',
            size: file.size, savedText: file.content, original: undefined, deleted: false,
            conflict: null, review: null, agentPending: false,
        };
        docs.set(path, doc);
        if (doc.kind === 'text') {
            editor.ensureModel(path, file.content);
            loadOriginal(doc);
        }
        return doc;
    }
    async function loadOriginal(doc) {
        try {
            const original = await api.original(ws.path, doc.path);
            doc.original = original && original.tracked ? original.content : null;
        } catch (_) { doc.original = null; }
        if (tabs.active === doc.path) updateGutter();
    }
    function updateGutter() {
        clearTimeout(gutterTimer);
        gutterTimer = setTimeout(() => {
            const doc = docs.get(tabs.active);
            if (!doc || doc.kind !== 'text') return editor.setGutter([]);
            const base = doc.original === undefined ? null : (doc.original === null ? '' : doc.original);
            if (base === null) return editor.setGutter([]);
            // Untracked files read as entirely added (VS Code shows the same).
            editor.setGutter(M.lineDiff(base, editor.getValue(doc.path)) || []);
        }, 250);
    }
    function isDirty(path) { return !!(docs.get(path) && docs.get(path).kind === 'text' && editor.isDirty(path)); }
    function dirtyPaths() { return tabs.tabs.map((t) => t.path).filter(isDirty); }

    function showSurface(kind) {
        $('ideEditor').hidden = kind !== 'text';
        $('idePreview').hidden = kind !== 'preview';
        $('ideWelcome').hidden = kind !== 'welcome';
        if (kind === 'text') requestAnimationFrame(() => editor.layout());
    }
    async function showDoc(path, opts) {
        const o = opts || {};
        let doc;
        try {
            doc = await loadDoc(path);
        } catch (err) {
            if (err && err.status === 404) {
                tabs = M.closeTab(tabs, path);
                renderTabs();
                toast(path + ' no longer exists', 'error');
                if (tabs.active) return showDoc(tabs.active);
                showSurface('welcome');
                return;
            }
            throw err;
        }
        if (tabs.active !== path) return;
        hideDiff();
        if (doc.kind === 'text') {
            editor.open(path, doc.savedText, { readOnly: doc.readOnly });
            showSurface('text');
            if (o.line) {
                if (o.length) editor.select(o.line, o.col || 1, o.length);
                else editor.reveal(o.line, o.col || 1);
            } else if (!o.noFocus && !quick.isOpen()) editor.focus();
            updateGutter();
        } else {
            editor.detach();
            const box = $('idePreview');
            if (doc.kind === 'image') {
                box.innerHTML = '<figure class="ide-image"><img alt="' + esc(path) + '" src="' + esc(api.rawUrl(ws.path, path)) + '&t=' + Date.now() + '"><figcaption>' + esc(path) + ' · ' + esc(M.formatBytes(doc.size)) + '</figcaption></figure>';
            } else {
                box.innerHTML = '<div class="ide-binary"><p>' + esc(doc.kind === 'large' ? 'This file is too large to open in the editor (' + M.formatBytes(doc.size) + ').' : 'This file is binary or uses an unsupported text encoding.') + '</p>'
                    + '<button type="button" data-cmd="file.openDefault">Open in default app</button> <button type="button" data-cmd="file.reveal">Reveal in file manager</button></div>';
            }
            showSurface('preview');
        }
        renderBanner();
        renderCrumbs();
        renderStatus();
        explorer.render();
    }
    async function openFile(path, opts) {
        if (!ws) return;
        const p = M.normPath(path);
        const o = opts || {};
        const before = tabs.tabs.map((t) => t.path);
        tabs = M.openTab(tabs, p, { preview: !!o.preview });
        // A replaced preview tab drops its (clean) document.
        before.filter((x) => !tabs.tabs.some((t) => t.path === x)).forEach(disposeDoc);
        wsState.recent = M.pushRecent(wsState.recent, p);
        renderTabs();
        saveWsState();
        try { await showDoc(p, o); } catch (err) { fail(err); }
    }
    function disposeDoc(path) {
        editor.close(path);
        docs.delete(path);
    }
    async function closeTabsGuarded(paths) {
        const dirty = paths.filter(isDirty);
        if (dirty.length) {
            const choice = await ask(dirty.length === 1 ? 'Save changes to ' + dirty[0] + '?' : 'Save changes to ' + dirty.length + ' files?',
                [{ label: 'Save', value: 'save', primary: true }, { label: "Don't save", value: 'discard' }, { label: 'Cancel', value: '' }]);
            if (!choice) return false;
            if (choice === 'save') {
                for (const p of dirty) if (!(await save(p))) return false;
            }
        }
        tabs = M.closeTabs(tabs, paths);
        closedStack = paths.concat(closedStack).slice(0, 20);
        paths.forEach(disposeDoc);
        renderTabs();
        saveWsState();
        persistHotExit();
        if (tabs.active) await showDoc(tabs.active);
        else { editor.detach(); hideDiff(); showSurface('welcome'); renderCrumbs(); renderStatus(); hideBanner(); explorer.render(); }
        return true;
    }
    async function confirmLeaveDirty(reason) {
        const dirty = dirtyPaths();
        if (!dirty.length) return true;
        const choice = await ask(dirty.length + ' file' + (dirty.length === 1 ? ' has' : 's have') + ' unsaved changes before ' + reason + '.',
            [{ label: 'Save all', value: 'save', primary: true }, { label: 'Keep as unsaved', value: 'keep' }, { label: 'Cancel', value: '' }]);
        if (!choice) return false;
        if (choice === 'save') return saveAll();
        return true;
    }

    /** Save one file; resolves false when it did not save. */
    async function save(path, opts) {
        const doc = docs.get(path || tabs.active);
        if (!doc || doc.kind !== 'text') return true;
        if (doc.readOnly) { toast(doc.path + ' is read-only', 'error'); return false; }
        const content = editor.getValue(doc.path);
        try {
            const file = await api.write(ws.path, doc.path, content, { etag: doc.etag, encoding: doc.encoding, force: !!(opts && opts.force), create: doc.deleted });
            doc.etag = file.etag;
            doc.savedText = content;
            doc.deleted = false;
            doc.conflict = null;
            editor.markSaved(doc.path);
            setStatusMsg('Saved ' + doc.path);
            renderTabs();
            renderBanner();
            persistHotExit();
            scheduleGitRefresh();
            return true;
        } catch (err) {
            if (err.conflict) {
                const disk = await api.read(ws.path, doc.path).catch(() => null);
                if (disk && disk.content === content) {
                    // Same text was written elsewhere; adopt the disk version.
                    doc.etag = disk.etag;
                    doc.savedText = content;
                    editor.markSaved(doc.path);
                    renderTabs();
                    return true;
                }
                doc.conflict = disk ? { etag: disk.etag, content: disk.content } : { etag: err.current && err.current.etag, content: null };
                if (tabs.active !== doc.path) await activate(doc.path);
                renderBanner();
                toast('Not saved: ' + doc.path + ' changed on disk', 'error');
                return false;
            }
            fail(err);
            return false;
        }
    }
    async function saveAll() {
        let ok = true;
        for (const p of dirtyPaths()) ok = (await save(p)) && ok;
        return ok;
    }
    async function revertFile(path) {
        const doc = docs.get(path || tabs.active);
        if (!doc || doc.kind !== 'text') return;
        const disk = await api.read(ws.path, doc.path);
        doc.etag = disk.etag;
        doc.savedText = disk.content;
        doc.conflict = null;
        editor.setValue(doc.path, disk.content, { saved: true });
        renderTabs();
        renderBanner();
        updateGutter();
        persistHotExit();
    }
    async function activate(path) {
        tabs = M.activateTab(tabs, path);
        renderTabs();
        saveWsState();
        await showDoc(path);
    }

    // ── disk sync: external edits, agent-change review, deletions ───
    async function pollOpenFiles() {
        if (!ws || document.hidden) return;
        const loaded = tabs.tabs.map((t) => t.path).filter((p) => docs.has(p) && docs.get(p).kind === 'text');
        if (!loaded.length) return;
        const root = ws.path;
        let stats;
        try { stats = await api.stat(root, loaded); } catch (_) { return; }
        if (!ws || ws.path !== root) return;
        for (const path of loaded) {
            const doc = docs.get(path);
            const st = stats[path];
            if (!doc || !st) continue;
            if (!st.exists) {
                if (!doc.deleted) { doc.deleted = true; renderTabs(); if (tabs.active === path) renderBanner(); }
                continue;
            }
            if (doc.deleted) { doc.deleted = false; renderTabs(); }
            if (!st.etag || st.etag === doc.etag || (doc.conflict && doc.conflict.etag === st.etag)) continue;
            let disk;
            try { disk = await api.read(root, path); } catch (_) { continue; }
            if (disk.content == null) continue;
            const buffer = editor.getValue(path);
            if (disk.content === buffer) {
                doc.etag = disk.etag;
                doc.savedText = disk.content;
                editor.markSaved(path);
                renderTabs();
                continue;
            }
            if (isDirty(path)) {
                doc.conflict = { etag: disk.etag, content: disk.content };
            } else {
                // Clean buffer: follow the disk (undoable) and offer a review.
                const before = doc.savedText;
                editor.setValue(path, disk.content, { saved: true });
                doc.etag = disk.etag;
                doc.savedText = disk.content;
                doc.review = { before: doc.review ? doc.review.before : before, after: disk.content, at: Date.now() };
                doc.agentPending = false;
            }
            if (tabs.active === path) { renderBanner(); updateGutter(); }
            renderTabs();
        }
    }
    function startWatching() {
        clearInterval(watchTimer);
        watchTimer = setInterval(() => {
            watchTick++;
            pollOpenFiles();
            if (watchTick % 4 === 0 && !document.hidden) refreshGit();
            if (watchTick % 6 === 0 && !document.hidden) explorer.refresh();
        }, WATCH_MS);
    }

    // ── banner (conflict / review / deleted) ────────────────────────
    function hideBanner() { $('ideBanner').hidden = true; $('ideBanner').innerHTML = ''; }
    function renderBanner() {
        const doc = docs.get(tabs.active);
        const el = $('ideBanner');
        if (!doc) return hideBanner();
        let html = '';
        if (doc.conflict) {
            html = '<span class="ide-banner-icon is-warn">!</span><span>This file changed on disk while you have unsaved edits.</span>'
                + '<button data-banner="compareDisk">Compare</button><button data-banner="overwrite">Overwrite disk</button><button data-banner="loadDisk">Load disk version</button>';
        } else if (doc.deleted) {
            html = '<span class="ide-banner-icon is-warn">!</span><span>This file was deleted on disk. Save to recreate it.</span><button data-banner="closeTab">Close</button>';
        } else if (doc.review) {
            const lines = M.lineDiff(doc.review.before, doc.review.after) || [];
            html = '<span class="ide-banner-icon is-ai">✦</span><span>Changed on disk' + (lines.length ? ' · ' + lines.length + ' change' + (lines.length === 1 ? '' : 's') : '') + '. Review the edit, keep it, or undo it.</span>'
                + '<button data-banner="review">Review</button><button data-banner="keep" class="is-primary">Keep</button><button data-banner="undo">Undo</button>';
        } else if (doc.agentPending) {
            html = '<span class="ide-banner-icon is-ai ide-spin">✦</span><span>Waiting for the agent to edit this file… changes appear here for review.</span><button data-banner="dismissAgent">Dismiss</button>';
        }
        el.hidden = !html;
        el.innerHTML = html;
    }
    $('ideBanner').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-banner]');
        const doc = docs.get(tabs.active);
        if (!btn || !doc) return;
        const action = btn.dataset.banner;
        try {
            if (action === 'compareDisk') showDiff(doc.conflict.content, editor.getValue(doc.path), { path: doc.path, title: doc.path + ' — disk ↔ your edits' });
            else if (action === 'overwrite') await save(doc.path, { force: true });
            else if (action === 'loadDisk') await revertFile(doc.path);
            else if (action === 'closeTab') await closeTabsGuarded([doc.path]);
            else if (action === 'review') openReview(doc);
            else if (action === 'keep') { doc.review = null; hideDiff(); renderBanner(); }
            else if (action === 'undo') await undoReview(doc);
            else if (action === 'dismissAgent') { doc.agentPending = false; renderBanner(); }
        } catch (err) { fail(err); }
    });
    function openReview(doc) {
        showDiff(doc.review.before, doc.review.after, {
            path: doc.path, title: doc.path + ' — changes on disk',
            actions: [{ label: 'Keep', primary: true, run: () => { doc.review = null; hideDiff(); renderBanner(); } },
                { label: 'Undo', run: () => undoReview(doc) }],
        });
    }
    async function undoReview(doc) {
        const before = doc.review.before;
        editor.setValue(doc.path, before);
        doc.review = null;
        hideDiff();
        await save(doc.path);
        updateGutter();
    }

    // ── diff view ───────────────────────────────────────────────────
    function showDiff(original, modified, opts) {
        const o = opts || {};
        diffState = o;
        $('ideDiffView').hidden = false;
        $('ideDiffTitle').textContent = o.title || o.path || 'Diff';
        const actions = (o.actions || []).concat([
            { label: 'Inline', run: () => { o.inline = !o.inline; editor.setDiffSideBySide(!o.inline); } },
            o.path && o.openable !== false ? { label: 'Open file', run: () => { hideDiff(); openFile(o.path, { preview: false }); } } : null,
            { label: '×', title: 'Close diff (Esc)', run: hideDiff },
        ]).filter(Boolean);
        $('ideDiffActions').innerHTML = actions.map((a, i) => '<button type="button" data-diff-action="' + i + '"' + (a.primary ? ' class="is-primary"' : '') + (a.title ? ' title="' + esc(a.title) + '"' : '') + '>' + esc(a.label) + '</button>').join('');
        $('ideDiffActions').onclick = (e) => {
            const b = e.target.closest('[data-diff-action]');
            if (b) Promise.resolve(actions[Number(b.dataset.diffAction)].run()).catch(fail);
        };
        editor.showDiff(original, modified, { path: o.path, sideBySide: !o.inline });
    }
    function hideDiff() {
        if ($('ideDiffView').hidden) return;
        $('ideDiffView').hidden = true;
        diffState = null;
        editor.hideDiff();
        if (tabs.active && docs.has(tabs.active) && docs.get(tabs.active).kind === 'text') editor.focus();
    }
    async function compareWithHead(path) {
        const p = path || tabs.active;
        if (!p) return;
        const original = await api.original(ws.path, p);
        const current = docs.has(p) && docs.get(p).kind === 'text' ? editor.getValue(p) : (await api.read(ws.path, p)).content;
        if (!original.tracked) toast(p + ' is not committed yet; showing it as new', 'info');
        showDiff(original.content || '', current, { path: p, title: M.basename(p) + ' (HEAD ↔ working tree)' });
    }

    // ── tabs / crumbs / status ──────────────────────────────────────
    function renderTabs() {
        menuBar.refresh();
        const labels = M.tabLabels(tabs);
        $('ideTabs').innerHTML = tabs.tabs.map((t) => {
            const doc = docs.get(t.path);
            const dirty = isDirty(t.path);
            const letter = decorations.files[t.path] || '';
            const cls = ['ide-tab'];
            if (t.path === tabs.active) cls.push('is-active');
            if (t.preview) cls.push('is-preview');
            if (dirty) cls.push('is-dirty');
            if (doc && doc.deleted) cls.push('is-deleted');
            if (letter) cls.push('git-' + letter.toLowerCase().replace('!', 'conflict'));
            const l = labels[t.path];
            return '<div class="' + cls.join(' ') + '" role="tab" draggable="true" aria-selected="' + (t.path === tabs.active) + '" data-path="' + esc(t.path) + '" title="' + esc(t.path) + '">'
                + '<span class="ide-tab-name">' + esc(l.name) + '</span>' + (l.hint ? '<span class="ide-tab-hint">' + esc(l.hint) + '</span>' : '')
                + (doc && doc.review ? '<span class="ide-tab-ai" title="Changed on disk — review">✦</span>' : '')
                + '<button type="button" class="ide-tab-close" data-close="' + esc(t.path) + '" aria-label="Close ' + esc(l.name) + '" title="Close (Ctrl+W)"></button></div>';
        }).join('');
        const active = $('ideTabs').querySelector('.is-active');
        if (active) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        const anyDirty = dirtyPaths().length;
        document.title = (anyDirty ? '● ' : '') + (ws ? ws.name + ' — ' : '') + 'IDE — Cuttle';
    }
    $('ideTabs').addEventListener('click', (e) => {
        const close = e.target.closest('[data-close]');
        if (close) { e.stopPropagation(); closeTabsGuarded([close.dataset.close]); return; }
        const tab = e.target.closest('.ide-tab');
        if (tab) activate(tab.dataset.path).catch(fail);
    });
    $('ideTabs').addEventListener('dblclick', (e) => {
        const tab = e.target.closest('.ide-tab');
        if (tab) { tabs = M.pinTab(tabs, tab.dataset.path); renderTabs(); saveWsState(); }
    });
    $('ideTabs').addEventListener('auxclick', (e) => {
        const tab = e.target.closest('.ide-tab');
        if (tab && e.button === 1) { e.preventDefault(); closeTabsGuarded([tab.dataset.path]); }
    });
    $('ideTabs').addEventListener('contextmenu', (e) => {
        const tab = e.target.closest('.ide-tab');
        if (!tab) return;
        e.preventDefault();
        const path = tab.dataset.path;
        openMenu(e, [
            { label: 'Close', run: () => closeTabsGuarded([path]) },
            { label: 'Close Others', run: () => closeTabsGuarded(tabs.tabs.map((t) => t.path).filter((p) => p !== path)) },
            { label: 'Close to the Right', run: () => { const i = tabs.tabs.findIndex((t) => t.path === path); return closeTabsGuarded(tabs.tabs.slice(i + 1).map((t) => t.path)); } },
            { label: 'Close Saved', run: () => closeTabsGuarded(tabs.tabs.map((t) => t.path).filter((p) => !isDirty(p))) },
            { label: 'Close All', run: () => closeTabsGuarded(tabs.tabs.map((t) => t.path)) },
            null,
            { label: 'Copy Path', run: () => copyText(absPath(path)) },
            { label: 'Copy Relative Path', run: () => copyText(path) },
            { label: 'Reveal in Explorer', run: () => { showView('explorer', true); explorer.reveal(path); } },
            { label: 'Compare with HEAD', run: () => compareWithHead(path) },
            { label: 'Add File to AI Chat', run: () => addToChat({ path: path }) },
        ]);
    });
    let dragTab = null;
    $('ideTabs').addEventListener('dragstart', (e) => { const t = e.target.closest('.ide-tab'); dragTab = t && t.dataset.path; });
    $('ideTabs').addEventListener('dragover', (e) => { if (dragTab) e.preventDefault(); });
    $('ideTabs').addEventListener('drop', (e) => {
        if (!dragTab) return;
        e.preventDefault();
        const over = e.target.closest('.ide-tab');
        const idx = over ? tabs.tabs.findIndex((t) => t.path === over.dataset.path) : tabs.tabs.length;
        tabs = M.moveTab(tabs, dragTab, idx);
        dragTab = null;
        renderTabs();
        saveWsState();
    });
    function renderCrumbs() {
        const p = tabs.active;
        const el = $('ideCrumbs');
        if (!p) { el.innerHTML = ''; return; }
        const parts = p.split('/');
        el.innerHTML = '<span class="ide-crumb is-root" data-crumb="">' + esc(ws.name) + '</span>' + parts.map((part, i) => '<span class="ide-crumb-sep">›</span><span class="ide-crumb' + (i === parts.length - 1 ? ' is-file' : '') + '" data-crumb="' + esc(parts.slice(0, i + 1).join('/')) + '">' + esc(part) + '</span>').join('');
    }
    $('ideCrumbs').addEventListener('click', (e) => {
        const c = e.target.closest('[data-crumb]');
        if (!c) return;
        showView('explorer', true);
        if (c.dataset.crumb) explorer.reveal(c.dataset.crumb);
    });
    function renderStatus() {
        const branch = pending && pending.repos && pending.repos[0] ? pending.repos[0].branch : '';
        const changes = Object.keys(decorations.files).length;
        $('ideStatusBranch').textContent = branch ? '⎇ ' + branch + (changes ? '  ● ' + changes : '') : (changes ? '● ' + changes : '');
        $('ideStatusBranch').hidden = !branch && !changes;
        const badge = $('ideScmBadge');
        badge.hidden = !changes;
        badge.textContent = changes > 99 ? '99+' : String(changes);
        const doc = docs.get(tabs.active);
        const text = !!(doc && doc.kind === 'text' && editor);
        ['ideStatusPos', 'ideStatusIndent', 'ideStatusEncoding', 'ideStatusEol', 'ideStatusLang'].forEach((id) => { $(id).hidden = !text; });
        if (text) {
            const pos = editor.position();
            $('ideStatusPos').textContent = pos ? 'Ln ' + pos.line + ', Col ' + pos.col + (pos.selected ? ' (' + pos.selected + ' selected)' : '') : '';
            $('ideStatusIndent').textContent = editor.indent(doc.path);
            $('ideStatusEncoding').textContent = ({ 'utf-8': 'UTF-8', 'utf-8-bom': 'UTF-8 with BOM', 'utf-16': 'UTF-16', 'latin-1': 'Latin-1' })[doc.encoding] || 'UTF-8';
            $('ideStatusEol').textContent = editor.eol(doc.path);
            const lang = editor.language(doc.path);
            const meta = editor.languages().find((l) => l.id === lang);
            $('ideStatusLang').textContent = (meta && meta.label) || lang;
        }
        $('ideStatusTab').textContent = (layout.completions ? '✦ Tab' : '✦ Tab off');
        $('ideStatusTab').classList.toggle('is-on', layout.completions);
        $('ideStatusTab').hidden = !editor || editor.engine !== 'monaco';
        $('ideStatusEngine').hidden = !editor || editor.engine === 'monaco';
        $('ideStatusEngine').textContent = 'Plain editor (Monaco unavailable offline)';
    }

    // ── git ─────────────────────────────────────────────────────────
    let gitTimer = null;
    let gitInFlight = false;
    function scheduleGitRefresh() { clearTimeout(gitTimer); gitTimer = setTimeout(refreshGit, 600); }
    async function refreshGit() {
        if (!ws || gitInFlight) return;
        const root = ws.path;
        gitInFlight = true;
        try {
            const data = await api.pending(root);
            if (!ws || ws.path !== root) return;
            pending = data;
            decorations = M.decorationsFromPending(data, root);
        } catch (_) {
            if (!ws || ws.path !== root) return;
            pending = null;
            decorations = { files: {}, dirs: {} };
        } finally { gitInFlight = false; }
        const repos = (pending && pending.repos) || [];
        $('ideScmBranch').textContent = repos.length ? repos.map((r) => (repos.length > 1 ? (r.label || M.basename(r.repo_root)) + ': ' : '') + '⎇ ' + (r.branch || 'detached')).join(' · ') : 'Not a git repository';
        $('ideScmEmpty').hidden = !repos.length || repos.some((r) => (r.files || []).length);
        explorer.render();
        renderTabs();
        renderStatus();
        if (scmPanel && layout.view === 'scm' && layout.sidebar) scmPanel.scheduleRefresh(0);
    }
    function mountScm() {
        if (scmPanel) { try { scmPanel.destroy(); } catch (_) {} scmPanel = null; }
        $('ideScmHost').innerHTML = '';
        if (!window.CuttlePendingChangesPanel) return;
        scmPanel = window.CuttlePendingChangesPanel.create({
            host: $('ideScmHost'),
            getProject: () => (ws ? { path: ws.path, name: ws.name } : null),
            onOpenDiff: (relPath, meta) => openScmDiff(meta && meta.repoRoot, relPath),
            onAfterCommit: () => { refreshGit(); docs.forEach((d) => { if (d.kind === 'text') loadOriginal(d); }); },
            onAfterIgnore: () => { scmPanel.scheduleRefresh(80); explorer.refresh(); },
            isPageBackgrounded: () => document.hidden || layout.view !== 'scm' || !layout.sidebar,
            startExpanded: true,
        });
    }
    async function openScmDiff(repoRoot, file) {
        const abs = M.absKey((repoRoot || ws.path) + '/' + file);
        const rootKey = M.absKey(ws.path);
        if (!abs.startsWith(rootKey + '/')) {
            if (window.CuttleDiffModal) window.CuttleDiffModal.open({ projectPath: ws.path, repoRoot: repoRoot, file: file });
            return;
        }
        const rel = abs.slice(rootKey.length + 1);
        // A new file has nothing to compare against: just open it.
        if (decorations.files[rel] === 'U') { openFile(rel, { preview: true }); return; }
        try {
            const original = await api.original(ws.path, rel);
            let current = '';
            const letter = decorations.files[rel];
            if (letter !== 'D') {
                if (docs.has(rel) && docs.get(rel).kind === 'text') current = editor.getValue(rel);
                else {
                    const disk = await api.read(ws.path, rel);
                    if (disk.content == null) { if (window.CuttleDiffModal) window.CuttleDiffModal.open({ projectPath: ws.path, repoRoot: repoRoot, file: file }); return; }
                    current = disk.content;
                }
            }
            showDiff(original.content || '', current, { path: rel, openable: letter !== 'D', title: M.basename(rel) + ' (' + (letter === 'U' ? 'untracked' : letter === 'D' ? 'deleted' : 'working tree') + ')' });
        } catch (err) { fail(err); }
    }

    // ── file operations ─────────────────────────────────────────────
    function targetDir(entry) {
        if (entry && entry.type === 'dir') return entry.path;
        if (entry && entry.path) return M.dirname(entry.path);
        const sel = explorer.selected();
        if (sel) return sel.type === 'dir' ? sel.path : M.dirname(sel.path);
        return '';
    }
    const validName = (v) => (!v ? 'Enter a name' : /(^|\/)\.\.(\/|$)/.test(v) || v.startsWith('/') ? 'Stay inside the project' : '');
    async function newEntry(kind, entry) {
        const dir = targetDir(entry);
        const prefix = dir ? dir + '/' : '';
        const value = await quick.prompt({
            placeholder: kind === 'dir' ? 'New folder name' : 'New file name (folders are created with /)',
            value: prefix, selectRange: [prefix.length, prefix.length], validate: validName,
            hint: 'Press Enter to create ' + (kind === 'dir' ? 'the folder' : 'the file') + ' in ' + (ws ? ws.name : 'the project'),
        });
        if (!value) return;
        const path = M.normPath(value);
        try {
            // Create missing parent folders, like VS Code does for "a/b/c.txt".
            const parts = path.split('/');
            for (let i = 1; i < parts.length; i++) {
                const d = parts.slice(0, i).join('/');
                if (!explorer.entryType(d)) await api.createEntry(ws.path, d, 'dir').catch((err) => { if (err.status !== 409) throw err; });
            }
            await api.createEntry(ws.path, path, kind);
            await explorer.refresh();
            await explorer.reveal(path);
            fileList = null;
            if (kind === 'file') openFile(path, { preview: false });
        } catch (err) { fail(err); }
    }
    async function promptRename(entry) {
        if (!entry || !entry.path) return;
        const base = M.basename(entry.path);
        const dot = entry.type === 'file' && base.lastIndexOf('.') > 0 ? base.lastIndexOf('.') : base.length;
        const start = entry.path.length - base.length;
        const value = await quick.prompt({ placeholder: 'New name', value: entry.path, selectRange: [start, start + dot], validate: validName, hint: 'Rename or move ' + entry.path });
        if (value && M.normPath(value) !== entry.path) await renameEntry(entry.path, M.normPath(value));
    }
    async function renameEntry(from, to) {
        const affected = M.tabsUnder(tabs, from);
        try {
            await api.rename(ws.path, from, to);
            affected.forEach((p) => {
                const next = M.renamedPath(p, from, to);
                if (docs.has(p)) {
                    editor.rename(p, next);
                    const d = docs.get(p);
                    d.path = next;
                    docs.delete(p);
                    docs.set(next, d);
                }
            });
            tabs = M.renameInTabs(tabs, from, to);
            fileList = null;
            renderTabs();
            saveWsState();
            await explorer.refresh();
            await explorer.reveal(to);
            if (tabs.active && affected.length) { renderCrumbs(); renderStatus(); }
            scheduleGitRefresh();
        } catch (err) { fail(err); }
    }
    async function deleteEntry(entry) {
        if (!entry || !entry.path) return;
        const ok = await confirmAction('Delete ' + (entry.type === 'dir' ? 'the folder ' + entry.path + ' and everything in it' : entry.path) + '? This cannot be undone from Cuttle.', 'Delete');
        if (!ok) return;
        try {
            await api.remove(ws.path, entry.path);
            const affected = M.tabsUnder(tabs, entry.path);
            if (affected.length) {
                tabs = M.closeTabs(tabs, affected);
                affected.forEach(disposeDoc);
                renderTabs();
                if (tabs.active) await showDoc(tabs.active); else { editor.detach(); showSurface('welcome'); renderCrumbs(); }
            }
            fileList = null;
            saveWsState();
            await explorer.refresh();
            scheduleGitRefresh();
        } catch (err) { fail(err); }
    }
    function copyText(text) {
        navigator.clipboard.writeText(text).then(() => setStatusMsg('Copied ' + text), () => fail(new Error('Clipboard unavailable')));
    }

    // ── context menu ────────────────────────────────────────────────
    function openMenu(e, items) {
        const menu = $('ideMenu');
        menu.innerHTML = items.map((it, i) => it ? '<button type="button" role="menuitem" data-i="' + i + '"' + (it.disabled ? ' disabled' : '') + '>' + esc(it.label) + (it.keys ? '<kbd>' + esc(it.keys) + '</kbd>' : '') + '</button>' : '<hr>').join('');
        menu.hidden = false;
        const rect = menu.getBoundingClientRect();
        menu.style.left = Math.min(e.clientX, window.innerWidth - rect.width - 6) + 'px';
        menu.style.top = Math.min(e.clientY, window.innerHeight - rect.height - 6) + 'px';
        menu.onclick = (ev) => {
            const b = ev.target.closest('[data-i]');
            if (!b) return;
            menu.hidden = true;
            Promise.resolve(items[Number(b.dataset.i)].run()).catch(fail);
        };
        const first = menu.querySelector('button');
        if (first) first.focus();
    }
    document.addEventListener('mousedown', (e) => { if (!e.target.closest('#ideMenu')) $('ideMenu').hidden = true; }, true);
    function showContextMenu(entry, e) {
        const isRoot = !entry.path;
        const file = entry.type === 'file';
        openMenu(e, [
            { label: 'New File…', run: () => newEntry('file', entry) },
            { label: 'New Folder…', run: () => newEntry('dir', entry) },
            null,
            file ? { label: 'Open', run: () => openFile(entry.path, { preview: false }) } : null,
            file ? { label: 'Open in Default App', run: () => api.openDefault(absPath(entry.path)) } : null,
            { label: 'Reveal in File Manager', run: () => api.reveal(absPath(entry.path)) },
            { label: 'Open in Integrated Terminal', run: async () => { await togglePanel(true); terminal.run('cd ' + JSON.stringify(file ? M.dirname(entry.path) || '.' : entry.path || '.')); } },
            null,
            { label: 'Find in Folder…', run: () => { showView('search', true); search.focus(); } },
            file ? { label: 'Add File to AI Chat', run: () => addToChat({ path: entry.path }) } : null,
            file ? { label: 'Compare with HEAD', run: () => compareWithHead(entry.path) } : null,
            null,
            { label: 'Copy Path', run: () => copyText(absPath(entry.path)) },
            { label: 'Copy Relative Path', run: () => copyText(entry.path || '.') },
            isRoot ? null : { label: 'Rename…', keys: 'F2', run: () => promptRename(entry) },
            isRoot ? null : { label: 'Delete', keys: 'Del', run: () => deleteEntry(entry) },
        ].filter((x, i, arr) => x !== null || (i > 0 && arr[i - 1] !== null)));
    }

    // ── AI ──────────────────────────────────────────────────────────
    function selectionContext() {
        const doc = docs.get(tabs.active);
        if (!doc || doc.kind !== 'text') return null;
        const sel = editor.selection() || {};
        return { path: doc.path, language: editor.language(doc.path), startLine: sel.startLine, endLine: sel.endLine, text: sel.text || '' };
    }
    async function addToChat(ctx) {
        const c = ctx || selectionContext();
        if (!c) { toast('Open a file first', 'info'); return; }
        try {
            await toggleChat(true);
            await chat.insert(M.selectionContext(c));
        } catch (err) { fail(err); }
    }
    let inlineCtx = null;
    function openInlineEdit() {
        const c = selectionContext();
        if (!c) { toast('Open a file first', 'info'); return; }
        if (docs.get(c.path).readOnly) { toast(c.path + ' is read-only', 'error'); return; }
        inlineCtx = c;
        $('ideInlineEditTarget').textContent = c.path + (c.text ? ' · ' + (c.startLine === c.endLine ? 'line ' + c.startLine : 'lines ' + c.startLine + '–' + c.endLine) : ' · line ' + c.startLine);
        $('ideInlineEdit').hidden = false;
        $('ideInlineEditText').value = '';
        $('ideInlineEditText').focus();
    }
    function closeInlineEdit() {
        $('ideInlineEdit').hidden = true;
        inlineCtx = null;
        if (editor) editor.focus();
    }
    async function sendInlineEdit() {
        const instruction = $('ideInlineEditText').value.trim();
        const c = inlineCtx;
        if (!instruction || !c) return;
        closeInlineEdit();
        try {
            // The agent must see the latest text.
            if (isDirty(c.path) && !(await save(c.path))) return;
            const doc = docs.get(c.path);
            if (doc) { doc.agentPending = true; doc.review = null; renderBanner(); }
            await toggleChat(true);
            await chat.insert(M.editPrompt(c, instruction), { send: true });
            setStatusMsg('Sent edit request to the AI chat');
        } catch (err) { fail(err); }
    }
    $('ideInlineEditSend').addEventListener('click', sendInlineEdit);
    $('ideInlineEditCancel').addEventListener('click', closeInlineEdit);
    $('ideInlineEditText').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendInlineEdit(); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeInlineEdit(); }
    });
    function applyCompletions() {
        if (completionsReg) { completionsReg.dispose(); completionsReg = null; }
        if (!layout.completions || !editor || editor.engine !== 'monaco') return;
        completionsReg = editor.registerCompletions(async (ctx, signal, token) => {
            await new Promise((r) => setTimeout(r, 450));
            if (token.isCancellationRequested || !ws) return '';
            try {
                const data = await api.complete(ws.path, { file: ctx.path, language: ctx.language, prefix: ctx.prefix, suffix: ctx.suffix }, { signal: signal });
                if (!data.available) setStatusMsg('AI completions unavailable: no completion provider configured');
                return data.text || '';
            } catch (_) { return ''; }
        });
    }

    // ── run active file ─────────────────────────────────────────────
    const RUNNERS = { py: 'python3', js: 'node', mjs: 'node', ts: 'npx tsx', sh: 'bash', rb: 'ruby', go: 'go run', pl: 'perl', php: 'php', lua: 'lua' };
    async function runActiveFile() {
        const p = tabs.active;
        const runner = p && RUNNERS[M.extname(p)];
        if (!runner) { toast('No runner for this file type', 'info'); return; }
        if (isDirty(p) && !(await save(p))) return;
        await togglePanel(true);
        terminal.run(runner + ' ' + JSON.stringify(p));
    }

    // ── commands / quick open ───────────────────────────────────────
    const COMMANDS = [
        { id: 'file.quickOpen', label: 'File: Open File…', keys: 'Ctrl+P', run: () => quickOpen('') },
        { id: 'editor.undo', label: 'Undo', keys: 'Ctrl+Z', run: () => editor.run('undo') },
        { id: 'editor.redo', label: 'Redo', keys: 'Ctrl+Shift+Z', run: () => editor.run('redo') },
        { id: 'editor.selectAll', label: 'Select All', keys: 'Ctrl+A', run: () => editor.run('editor.action.selectAll') },
        { id: 'file.save', label: 'File: Save', keys: 'Ctrl+S', run: () => save() },
        { id: 'file.saveAll', label: 'File: Save All', keys: 'Ctrl+Alt+S', run: saveAll },
        { id: 'file.new', label: 'File: New File…', run: () => newEntry('file') },
        { id: 'file.newFolder', label: 'File: New Folder…', run: () => newEntry('dir') },
        { id: 'file.revert', label: 'File: Revert File', run: () => revertFile() },
        { id: 'file.rename', label: 'File: Rename…', run: () => tabs.active && promptRename({ path: tabs.active, type: 'file' }) },
        { id: 'file.close', label: 'View: Close Editor', keys: 'Ctrl+W', run: () => tabs.active && closeTabsGuarded([tabs.active]) },
        { id: 'file.closeAll', label: 'View: Close All Editors', run: () => closeTabsGuarded(tabs.tabs.map((t) => t.path)) },
        { id: 'file.reopen', label: 'View: Reopen Closed Editor', keys: 'Ctrl+Shift+T', run: () => { const p = closedStack.shift(); if (p) openFile(p); } },
        { id: 'file.copyPath', label: 'File: Copy Path of Active File', run: () => tabs.active && copyText(absPath(tabs.active)) },
        { id: 'file.copyRel', label: 'File: Copy Relative Path of Active File', run: () => tabs.active && copyText(tabs.active) },
        { id: 'file.reveal', label: 'File: Reveal Active File in File Manager', run: () => tabs.active && api.reveal(absPath(tabs.active)) },
        { id: 'file.openDefault', label: 'File: Open Active File in Default App', run: () => tabs.active && api.openDefault(absPath(tabs.active)) },
        { id: 'file.compareHead', label: 'Git: Compare Active File with HEAD', run: () => compareWithHead() },
        { id: 'explorer.reveal', label: 'Explorer: Reveal Active File', run: () => { if (tabs.active) { showView('explorer', true); explorer.reveal(tabs.active); } } },
        { id: 'explorer.refresh', label: 'Explorer: Refresh', run: () => { fileList = null; explorer.refresh(); refreshGit(); } },
        { id: 'explorer.collapse', label: 'Explorer: Collapse Folders', run: () => explorer.collapseAll() },
        { id: 'view.explorer', label: 'View: Show Explorer', keys: 'Ctrl+Shift+E', run: () => { showView('explorer', true); explorer.focus(); } },
        { id: 'view.search', label: 'Search: Find in Files', keys: 'Ctrl+Shift+F', run: () => { showView('search', true); search.focus((selectionContext() || {}).text); } },
        { id: 'view.replace', label: 'Search: Replace in Files', keys: 'Ctrl+Shift+H', run: () => { showView('search', true); search.focus((selectionContext() || {}).text, true); } },
        { id: 'view.scm', label: 'View: Show Source Control', keys: 'Ctrl+Shift+G', run: () => showView('scm', true) },
        { id: 'scm.refresh', label: 'Git: Refresh', run: () => { refreshGit(); if (scmPanel) scmPanel.scheduleRefresh(0); } },
        { id: 'view.sidebar', label: 'View: Toggle Primary Sidebar', keys: 'Ctrl+B', run: () => { layout.sidebar = !layout.sidebar; saveLayout(); applyLayout(); } },
        { id: 'view.panel', label: 'View: Toggle Terminal', keys: 'Ctrl+`', run: () => togglePanel() },
        { id: 'terminal.new', label: 'Terminal: Create New Terminal', keys: 'Ctrl+Shift+`', run: async () => { if (!layout.panel) await togglePanel(true); await terminal.spawn(); } },
        { id: 'terminal.kill', label: 'Terminal: Kill the Active Terminal', run: () => terminal.killActive() },
        { id: 'terminal.clear', label: 'Terminal: Clear', run: () => terminal.clear() },
        { id: 'terminal.runFile', label: 'Run: Run Active File in Terminal', keys: 'F5', run: runActiveFile },
        { id: 'view.chat', label: 'AI: Toggle Chat', keys: 'Ctrl+I', run: () => toggleChat() },
        { id: 'chat.new', label: 'AI: New Chat', run: async () => { await toggleChat(true); await chat.newChat(); } },
        { id: 'ai.addToChat', label: 'AI: Add Selection to Chat', keys: 'Ctrl+L', run: () => addToChat() },
        { id: 'ai.inlineEdit', label: 'AI: Edit Selection with AI…', keys: 'Ctrl+K', run: openInlineEdit },
        { id: 'ai.completions', label: 'AI: Toggle Tab Completions', run: () => { layout.completions = !layout.completions; saveLayout(); applyCompletions(); renderStatus(); setStatusMsg('AI tab completions ' + (layout.completions ? 'on' : 'off')); } },
        { id: 'editor.gotoLine', label: 'Go to Line…', keys: 'Ctrl+G', run: () => quickOpen(':') },
        { id: 'editor.symbol', label: 'Go to Symbol in Editor…', keys: 'Ctrl+Shift+O', run: () => editor.run('editor.action.quickOutline') },
        { id: 'editor.find', label: 'Find', keys: 'Ctrl+F', run: () => editor.run('actions.find') },
        { id: 'editor.replace', label: 'Replace', keys: 'Ctrl+H', run: () => editor.run('editor.action.startFindReplaceAction') },
        { id: 'editor.format', label: 'Format Document', keys: 'Shift+Alt+F', run: () => editor.run('editor.action.formatDocument') },
        { id: 'editor.comment', label: 'Toggle Line Comment', keys: 'Ctrl+/', run: () => editor.run('editor.action.commentLine') },
        { id: 'editor.fold', label: 'Fold All', run: () => editor.run('editor.foldAll') },
        { id: 'editor.unfold', label: 'Unfold All', run: () => editor.run('editor.unfoldAll') },
        { id: 'editor.monaco', label: 'Editor: Show All Editor Commands…', keys: 'F1', run: () => editor.run('editor.action.quickCommand') },
        { id: 'editor.wrap', label: 'View: Toggle Word Wrap', keys: 'Alt+Z', run: () => { layout.wordWrap = !layout.wordWrap; saveLayout(); applyLayout(); } },
        { id: 'editor.minimap', label: 'View: Toggle Minimap', run: () => { layout.minimap = !layout.minimap; saveLayout(); applyLayout(); } },
        { id: 'editor.language', label: 'Change Language Mode…', run: pickLanguage },
        { id: 'editor.eol', label: 'Change End of Line Sequence…', run: pickEol },
        { id: 'tabs.next', label: 'View: Next Editor', keys: 'Ctrl+Tab', run: () => cycleTab(1) },
        { id: 'tabs.prev', label: 'View: Previous Editor', keys: 'Ctrl+Shift+Tab', run: () => cycleTab(-1) },
        { id: 'workspace.switch', label: 'Project: Switch Project…', run: pickWorkspace },
    ];
    const commandById = (id) => COMMANDS.find((c) => c.id === id);
    const menuBar = window.CuttleIdeMenubar.create({
        win: window, el: $('ideMenubar'), getCommand: commandById, runCommand,
        hasFile: () => !!tabs.active, isDirty: () => !!tabs.active && isDirty(tabs.active),
    });
    function runCommand(id) {
        const c = commandById(id);
        if (c) Promise.resolve(c.run()).catch(fail);
    }
    async function getFileList() {
        if (fileList && Date.now() - fileList.at < 30000) return fileList.files;
        const root = ws.path;
        const data = await api.files(root);
        if (ws && ws.path === root) fileList = { files: data.files || [], at: Date.now() };
        return data.files || [];
    }
    function cycleTab(delta) {
        const next = M.nextTab(tabs, delta);
        if (next.active && next.active !== tabs.active) activate(next.active).catch(fail);
    }
    async function quickOpen(prefix) {
        if (!ws) return;
        const item = await quick.pick({
            placeholder: 'Search files by name (append :line to jump) · > commands · : line · @ symbol',
            value: prefix || '',
            provider: async (raw) => {
                if (raw.startsWith('>')) {
                    const q = raw.slice(1).trim().toLowerCase();
                    return COMMANDS.filter((c) => !q || M.fuzzyScore(q, c.label.toLowerCase())).map((c) => ({ label: c.label, keys: c.keys, command: c.id }));
                }
                if (raw.startsWith('@')) return [{ label: 'Go to symbol in the active editor', command: 'editor.symbol' }];
                const parsed = M.parseQuickOpen(raw);
                if (parsed.gotoLineOnly || raw === ':') {
                    return tabs.active ? [{ label: parsed.line ? 'Go to line ' + parsed.line + (parsed.col ? ', column ' + parsed.col : '') : 'Type a line number', line: parsed.line, col: parsed.col, path: tabs.active }] : [];
                }
                const files = await getFileList();
                if (!parsed.query) {
                    return wsState.recent.filter((p) => files.includes(p)).slice(0, 20).map((p, i) => ({ label: M.basename(p), description: M.dirname(p), path: p, separator: i === 0 }));
                }
                return M.fuzzyFilter(parsed.query, files, 60, wsState.recent).map((r) => {
                    const base = r.path.lastIndexOf('/') + 1;
                    return {
                        labelHtml: M.highlightHtml(r.path.slice(base), r.positions.filter((x) => x >= base).map((x) => x - base)),
                        description: M.dirname(r.path), path: r.path, line: parsed.line, col: parsed.col,
                    };
                });
            },
        });
        if (!item) return;
        if (item.command) {
            if (item.command === 'editor.symbol') setTimeout(() => runCommand(item.command), 0);
            else runCommand(item.command);
            return;
        }
        if (item.path && item.path === tabs.active && item.line) { editor.reveal(item.line, item.col || 1); return; }
        if (item.path) openFile(item.path, { preview: false, line: item.line, col: item.col });
    }
    async function pickLanguage() {
        const doc = docs.get(tabs.active);
        if (!doc || doc.kind !== 'text') return;
        const langs = editor.languages();
        const item = await quick.pick({
            placeholder: 'Select language mode',
            provider: (q) => langs.filter((l) => !q || M.fuzzyScore(q, l.label + ' ' + l.id)).map((l) => ({ label: l.label, description: l.id, id: l.id })),
        });
        if (item) { editor.setLanguage(doc.path, item.id); renderStatus(); }
    }
    async function pickEol() {
        const doc = docs.get(tabs.active);
        if (!doc || doc.kind !== 'text') return;
        const item = await quick.pick({ placeholder: 'Select end of line sequence', provider: () => [{ label: 'LF', crlf: false }, { label: 'CRLF', crlf: true }] });
        if (item) { editor.setEol(doc.path, item.crlf); renderTabs(); renderStatus(); }
    }
    async function pickWorkspace() {
        const item = await quick.pick({
            placeholder: 'Open project',
            provider: (q) => workspaces.filter((w) => w.available && (!q || M.fuzzyScore(q, w.name + ' ' + w.path))).map((w) => ({ label: w.name, description: w.path, path: w.path })),
        });
        if (item) openWorkspace(item.path).catch(fail);
    }
    $('ideCommandCenter').addEventListener('click', () => quickOpen(''));
    $('ideWorkspace').addEventListener('change', (e) => openWorkspace(e.target.value).catch(fail));
    document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-cmd]');
        if (b && !b.closest('#ideMenu')) runCommand(b.dataset.cmd);
        const v = e.target.closest('.ide-activity-btn');
        if (v) showView(v.dataset.view);
    });

    // ── keybindings (capture phase: ahead of Monaco and the browser) ─
    const KEYMAP = {
        'ctrl+p': () => quickOpen(''), 'ctrl+e': () => quickOpen(''), 'ctrl+shift+p': () => quickOpen('>'), 'f1': () => quickOpen('>'),
        'ctrl+s': () => save(), 'ctrl+alt+s': saveAll, 'ctrl+w': () => runCommand('file.close'), 'ctrl+shift+t': () => runCommand('file.reopen'),
        'ctrl+b': () => runCommand('view.sidebar'), 'ctrl+`': () => togglePanel(), 'ctrl+shift+`': () => runCommand('terminal.new'),
        'ctrl+j': () => togglePanel(),
        'ctrl+shift+e': () => runCommand('view.explorer'), 'ctrl+shift+f': () => runCommand('view.search'), 'ctrl+shift+h': () => runCommand('view.replace'),
        'ctrl+shift+g': () => runCommand('view.scm'), 'ctrl+i': () => toggleChat(), 'ctrl+l': () => addToChat(), 'ctrl+k': openInlineEdit,
        'ctrl+g': () => quickOpen(':'), 'ctrl+tab': () => cycleTab(1), 'ctrl+shift+tab': () => cycleTab(-1),
        'ctrl+pagedown': () => cycleTab(1), 'ctrl+pageup': () => cycleTab(-1), 'alt+z': () => runCommand('editor.wrap'), 'f5': runActiveFile,
    };
    function keyId(e) {
        const k = e.key === ' ' ? 'space' : e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
        return (e.ctrlKey || e.metaKey ? 'ctrl+' : '') + (e.altKey ? 'alt+' : '') + (e.shiftKey ? 'shift+' : '') + (e.code === 'Backquote' ? '`' : k);
    }
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (!$('ideMenu').hidden) { $('ideMenu').hidden = true; e.preventDefault(); return; }
            if (!$('ideDiffView').hidden && !quick.isOpen() && !e.defaultPrevented) {
                // Leave Monaco's own Escape (find widget, suggest) alone first.
                if (!document.activeElement || !document.activeElement.closest('.ide-diff')) { hideDiff(); e.preventDefault(); return; }
            }
        }
        if (quick.isOpen() && e.key === 'Escape') { quick.close(); e.preventDefault(); return; }
        if (quick.isOpen() || $('ideDialog').open) return;
        const id = keyId(e);
        const fn = KEYMAP[id];
        if (!fn) return;
        if (e.target && e.target.closest && e.target.closest('.ide-term-host') && !['ctrl+`', 'ctrl+j', 'ctrl+shift+`', 'ctrl+p', 'ctrl+shift+p', 'ctrl+b', 'ctrl+i'].includes(id)) return;
        e.preventDefault();
        e.stopPropagation();
        Promise.resolve(fn()).catch(fail);
    }, true);

    // ── hot exit: unsaved buffers survive reloads/navigation ────────
    function hotExitKey() { return 'cuttleIdeUnsaved:' + (ws ? ws.path : ''); }
    function persistHotExit() {
        if (!ws || !editor) return;
        const out = {};
        let size = 0;
        dirtyPaths().forEach((p) => {
            const text = editor.getValue(p);
            if (size + text.length > 4 * 1024 * 1024) return;
            size += text.length;
            out[p] = { content: text, etag: docs.get(p).etag };
        });
        if (Object.keys(out).length) writeJson(hotExitKey(), out);
        else try { localStorage.removeItem(hotExitKey()); } catch (_) {}
    }
    function scheduleHotExit() { clearTimeout(hotExitTimer); hotExitTimer = setTimeout(persistHotExit, 800); }
    /** Reapply unsaved buffers from the last session (a newer disk copy → conflict). */
    async function restoreHotExit() {
        const saved = readJson(hotExitKey());
        if (!saved) return;
        for (const p of Object.keys(saved)) {
            if (!M.findTab(tabs, p)) {
                const active = tabs.active;
                tabs = M.openTab(tabs, p, {});
                if (active) tabs = M.activateTab(tabs, active);
            }
            try {
                const doc = await loadDoc(p);
                if (doc.kind !== 'text') continue;
                editor.setValue(p, saved[p].content);
                if (saved[p].etag && saved[p].etag !== doc.etag) doc.conflict = { etag: doc.etag, content: doc.savedText };
            } catch (_) { /* file gone: the tab reports it on activation */ }
        }
        renderTabs();
        renderBanner();
        updateGutter();
    }
    window.addEventListener('beforeunload', (e) => {
        saveWsState();
        persistHotExit();
        if (dirtyPaths().length) { e.preventDefault(); e.returnValue = ''; }
    });

    // ── embedded chat ↔ IDE messages ────────────────────────────────
    window.addEventListener('message', (e) => {
        const d = e.data;
        if (!d || typeof d !== 'object' || !chat.isSource(e.source)) return;
        if (d.type === 'cuttle-chat-url' || d.type === 'cuttle-chat-session') chat.adoptSession(d.chatId || d.sessionId, e.source);
        if (d.type === 'cuttle-pending-changes-refresh' || d.type === 'cuttle-pending-changes-changed') { pollOpenFiles(); scheduleGitRefresh(); explorer.refresh(); }
        if (d.type === 'cuttle-navigate' && typeof d.page === 'string') {
            const url = new URL(d.page, location.origin);
            // "Editor" from a diff inside the side chat opens here, not a new IDE.
            if (url.pathname !== '/ide_page.html' && inShell) window.parent.postMessage(d, '*');
        }
    });
    async function openDeepLink(params) {
        const path = params.get('path');
        if (path && (!ws || M.absKey(path) !== M.absKey(ws.path))) await openWorkspace(path);
        if (path && (!ws || M.absKey(path) !== M.absKey(ws.path))) return; // Cancelled project switch.
        const file = params.get('file');
        if (!file || !ws) return;
        let rel = M.normPath(file);
        const repoRoot = params.get('repo_root');
        if (repoRoot) {
            const abs = M.absKey(repoRoot + '/' + rel);
            const key = M.absKey(ws.path);
            if (abs.startsWith(key + '/')) rel = abs.slice(key.length + 1);
        }
        await openFile(rel, { preview: false, line: Number(params.get('line')) || null });
        showView('explorer', true);
        explorer.reveal(rel);
    }

    // ── boot ────────────────────────────────────────────────────────
    function showNotice(html) {
        $('ideNotice').hidden = false;
        $('ideNotice').innerHTML = html;
        $('ideApp').hidden = true;
    }
    async function boot() {
        try {
            if (!(await api.flagEnabled('ide'))) {
                showNotice('<h2>IDE is turned off</h2><p>Enable <strong>IDE</strong> in <a href="/settings_page.html?tab=experimental">Settings → Experimental</a> to edit projects here.</p>');
                return;
            }
            workspaces = (await api.workspaces()).workspaces || [];
        } catch (err) {
            showNotice('<h2>IDE could not load</h2><p>' + esc((err && err.message) || 'Unknown error') + '</p>');
            return;
        }
        $('ideApp').hidden = false;
        $('ideNotice').hidden = true;
        applyLayout();
        wireResizers();
        editor = await window.CuttleIdeEditor.create({
            container: $('ideEditor'), diffContainer: $('ideDiff'),
            fallbackLanguage: M.languageForPath,
            onChange: (path) => {
                const t = M.findTab(tabs, path);
                if (t && t.preview) { tabs = M.pinTab(tabs, path); saveWsState(); }
                const doc = docs.get(path);
                if (doc && doc.review && editor.getValue(path) !== doc.review.after) doc.review = null;
                renderTabs();
                if (tabs.active === path) renderBanner();
                updateGutter();
                scheduleHotExit();
                renderStatus();
            },
            onCursor: () => renderStatus(),
        });
        applyLayout();
        applyCompletions();
        new MutationObserver(() => { editor.applyTheme(); terminal.applyTheme(); }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        window.addEventListener('focus', () => { pollOpenFiles(); explorer.refresh(); });
        document.addEventListener('visibilitychange', () => { if (!document.hidden) pollOpenFiles(); });

        // Deep link: ?path=<project or checkout>&file=<rel>[&repo_root=…][&line=N]
        const params = new URLSearchParams(location.search);
        const wantPath = params.get('path') || (params.get('project') && (workspaces.find((w) => String(w.id) === params.get('project')) || {}).path);
        const last = localStorage.getItem(LAST_WS_KEY);
        const initial = wantPath
            || (last && workspaces.some((w) => M.absKey(w.path) === M.absKey(last)) ? last : null)
            || ((workspaces.find((w) => w.current && w.available) || workspaces.find((w) => w.available) || {}).path);
        if (!initial) {
            showNotice('<h2>No projects yet</h2><p>Register a project in <a href="/projects_page.html">Apps → Projects</a>, then open it here.</p>');
            return;
        }
        try {
            await openWorkspace(initial, { force: true });
        } catch (err) { fail(err); return; }
        await openDeepLink(new URLSearchParams({ file: params.get('file') || '', repo_root: params.get('repo_root') || '', line: params.get('line') || '' }));
        if (params.has('file') || params.has('path')) {
            try { history.replaceState(null, '', location.pathname); } catch (_) {}
        }
        startWatching();
        navigation.ready();
    }
    boot();
})();
