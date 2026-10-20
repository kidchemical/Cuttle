/* IDE App — agent review controller (experimental flag `agent_review`).
 * Owns the review lifetime of one workspace: the server listing
 * (api.agent_review, the single source of truth), the live wake signal
 * (SSE, polling fallback), inline hunks on the active editor, the floating
 * review bar and the status-bar count. Keep / Undo always go to the server;
 * this controller keeps no review state of its own beyond a cache. */
(function (root) {
    'use strict';

    const KEYS = { keep: 'Ctrl+Alt+Y', undo: 'Ctrl+Alt+N', next: 'Alt+]', prev: 'Alt+[' };
    const POLL_MS = 4000;

    function create(host) {
        const R = host.model || root.CuttleIdeReviewModel;
        const api = host.api;
        const bar = host.barEl;
        const status = host.statusEl;
        const EventSourceImpl = host.EventSource === undefined ? root.EventSource : host.EventSource;
        let enabled = false;
        let rootPath = null;
        let idx = R.index([]);
        let revision = -1;
        const details = new Map(); // path → server review detail (for `revision`)
        let applied = null; // "path@revision" currently drawn in the editor
        let source = null;
        let pollTimer = null;
        let inflight = null;
        let again = false;
        let busy = false;

        // ── live wake signal ────────────────────────────────────────
        function stop() {
            if (source) { try { source.close(); } catch (_) {} source = null; }
            clearInterval(pollTimer);
            pollTimer = null;
        }
        function poll() {
            if (!pollTimer) pollTimer = setInterval(() => { if (!host.isHidden || !host.isHidden()) refresh(); }, POLL_MS);
        }
        function connect() {
            stop();
            if (!EventSourceImpl) return poll();
            try {
                source = new EventSourceImpl(api.reviewStreamUrl(), { withCredentials: true });
                source.onmessage = (e) => {
                    let data = null;
                    try { data = JSON.parse(e.data); } catch (_) { return; }
                    if (data && data.revision !== revision) refresh();
                };
                source.onerror = () => {
                    // Stream unavailable (proxy, restart): poll until the page reconnects.
                    if (source) { try { source.close(); } catch (_) {} source = null; }
                    poll();
                };
            } catch (_) { poll(); }
        }

        // ── server state ────────────────────────────────────────────
        function refresh() {
            if (!enabled || !rootPath) return Promise.resolve();
            if (inflight) { again = true; return inflight; }
            inflight = (async () => {
                do {
                    again = false;
                    const rootAt = rootPath;
                    let data;
                    try { data = await api.reviewFiles(rootAt); } catch (_) { return; }
                    if (rootAt !== rootPath) return;
                    const changed = data.revision !== revision;
                    revision = data.revision;
                    idx = R.index(data.files);
                    if (changed) {
                        details.clear();
                        // Clean open buffers follow the agent's writes before hunks are drawn.
                        await host.followDisk();
                    }
                    await applyActive();
                    host.onChange();
                    render();
                } while (again);
            })().finally(() => { inflight = null; });
            return inflight;
        }
        async function detail(path) {
            if (details.has(path)) return details.get(path);
            const found = await api.reviewFile(rootPath, path);
            details.set(path, found);
            return found;
        }

        // ── active editor ───────────────────────────────────────────
        async function applyActive() {
            const editor = host.editor();
            if (!editor) return;
            const path = host.activePath();
            if (!enabled || !path || !idx.files[path]) {
                if (editor.reviewPath()) editor.setReview(null);
                applied = null;
                renderFile();
                return;
            }
            const key = path + '@' + revision;
            if (applied === key && editor.reviewPath() === path) return;
            let d;
            try { d = await detail(path); } catch (_) { return; }
            if (host.activePath() !== path || !d) return;
            if (!host.isDirty(path) && editor.getValue(path) !== d.current) await host.adoptDisk(path);
            editor.setReview(path, R.layout(d.hunk_list), {
                keep: (id) => act('keep', [path], [id]),
                undo: (id) => act('undo', [path], [id]),
            }, KEYS);
            applied = key;
            renderFile();
        }
        function items(path) {
            const d = details.get(path);
            return d ? R.layout(d.hunk_list) : [];
        }

        // ── Keep / Undo ─────────────────────────────────────────────
        async function act(kind, paths, ids) {
            if (busy) return;
            busy = true;
            try {
                const all = paths == null;
                const targets = all ? idx.order.slice() : paths;
                if (kind === 'undo') {
                    // Unsaved edits first: the server folds them before reverting the agent's hunks.
                    for (const p of targets) if (host.isDirty(p) && !(await host.save(p))) return;
                }
                const body = { hunks: ids || undefined };
                if (all) body.paths = undefined;
                else if (targets.length === 1) body.path = targets[0];
                else body.paths = targets;
                if (kind === 'undo' && ids && targets.length === 1 && details.has(targets[0])) body.etag = details.get(targets[0]).etag;
                try {
                    await api.reviewAct(kind, rootPath, body);
                } catch (err) {
                    if (err && err.conflict) {
                        host.toast('These changes moved; the review was refreshed', 'info');
                        details.clear();
                        revision = -1;
                        await refresh();
                        return;
                    }
                    throw err;
                }
                if (kind === 'undo') for (const p of targets) await host.adoptDisk(p);
                details.clear();
                revision = -1;
                await refresh();
            } catch (err) {
                host.fail(err);
            } finally {
                busy = false;
            }
        }
        function cursorLine() {
            const pos = host.editor() && host.editor().position();
            return pos ? pos.line : 1;
        }
        async function atCursor(kind) {
            const path = host.activePath();
            if (!path || !idx.files[path]) return goTo(1);
            const hunk = R.hunkAt(items(path), cursorLine());
            if (hunk) await act(kind, [path], [hunk.id]);
        }
        async function file(kind, path) {
            const p = path || host.activePath();
            if (!p || !idx.files[p]) return;
            const entry = idx.files[p];
            if (kind === 'undo' && entry.change === 'added'
                && !(await host.confirm('Undo all changes to ' + p + '? The agent created this file, so it will be deleted.', 'Delete file'))) return;
            await act(kind, [p], null);
        }
        async function everything(kind) {
            const t = idx.totals;
            if (!t.files) return;
            if (kind === 'undo' && !(await host.confirm('Undo every agent change in ' + R.summary(t)
                + '? Files are rewritten on disk; files the agent created are deleted.', 'Undo all'))) return;
            await act(kind, null, null);
        }

        // ── navigation ──────────────────────────────────────────────
        async function goTo(dir) {
            const path = host.activePath();
            const line = cursorLine();
            if (path && idx.files[path]) {
                const list = items(path);
                if (R.hasHunkAfter(list, line, dir) || idx.order.length === 1) {
                    const hunk = R.nextHunk(list, line, dir);
                    if (hunk) host.reveal(hunk.anchor);
                    return;
                }
            }
            const target = R.nextFile(idx, path, dir);
            if (!target) return;
            await host.openFile(target);
            const d = await detail(target).catch(() => null);
            const list = d ? R.layout(d.hunk_list) : [];
            const hunk = dir < 0 ? list[list.length - 1] : list[0];
            if (hunk && host.activePath() === target) host.reveal(hunk.anchor);
        }

        // ── DOM: floating review bar + status item ──────────────────
        const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        function renderFile() {
            if (!bar) return;
            const el = bar.querySelector('[data-rb="file"]');
            if (!el) return;
            const path = host.activePath();
            const entry = path && idx.files[path];
            el.textContent = entry ? path.split('/').pop() + ' · ' + entry.hunks + ' change' + (entry.hunks === 1 ? '' : 's') : '';
            bar.querySelectorAll('[data-rb="keepFile"], [data-rb="undoFile"]').forEach((b) => { b.hidden = !entry; });
        }
        function render() {
            const t = idx.totals;
            const show = enabled && t.files > 0;
            if (status) {
                status.hidden = !show;
                status.textContent = show ? '✦ ' + t.hunks : '';
                status.title = show ? 'Agent changes to review: ' + R.summary(t) + ' (' + KEYS.next + ' next)' : '';
                status.classList.toggle('is-live', !!t.live);
            }
            if (!bar) return;
            bar.hidden = !show;
            if (!show) return;
            bar.classList.toggle('is-live', !!t.live);
            bar.innerHTML = '<span class="ide-review-bar-icon' + (t.live ? ' ide-spin' : '') + '" aria-hidden="true">✦</span>'
                + '<span class="ide-review-bar-title">' + (t.live ? 'Agent editing…' : 'Agent changes') + '</span>'
                + '<span class="ide-review-bar-meta">' + esc(R.summary(t)) + '</span>'
                + '<span class="ide-review-bar-file" data-rb="file"></span>'
                + '<span class="ide-review-bar-group">'
                + '<button type="button" data-rb="prev" title="Previous change (' + KEYS.prev + ')" aria-label="Previous change">‹</button>'
                + '<button type="button" data-rb="next" title="Next change (' + KEYS.next + ')" aria-label="Next change">›</button>'
                + '</span>'
                + '<button type="button" data-rb="undoFile" title="Undo every change in this file">Undo file</button>'
                + '<button type="button" data-rb="keepFile" title="Keep every change in this file">Keep file</button>'
                + '<button type="button" data-rb="undoAll" title="Undo every agent change">Undo all</button>'
                + '<button type="button" data-rb="keepAll" class="is-primary" title="Keep every agent change">Keep all</button>';
            renderFile();
        }
        if (bar) {
            bar.addEventListener('click', (e) => {
                const b = e.target.closest('[data-rb]');
                if (!b) return;
                const run = {
                    prev: () => goTo(-1), next: () => goTo(1),
                    keepFile: () => file('keep'), undoFile: () => file('undo'),
                    keepAll: () => everything('keep'), undoAll: () => everything('undo'),
                }[b.dataset.rb];
                if (run) Promise.resolve(run()).catch(host.fail);
            });
        }
        if (status) status.addEventListener('click', () => goTo(1).catch(host.fail));

        return {
            KEYS: KEYS,
            /** Resolve the flag once; false keeps every entry point a no-op. */
            async init() {
                try { enabled = !!(await api.flagEnabled('agent_review')); } catch (_) { enabled = false; }
                render();
                return enabled;
            },
            enabled: () => enabled,
            setWorkspace(path) {
                stop();
                rootPath = path || null;
                idx = R.index([]);
                details.clear();
                revision = -1;
                applied = null;
                render();
                if (enabled && rootPath) { connect(); refresh(); }
            },
            refresh: refresh,
            /** Redraw hunks after the page shows (or re-shows) a document. */
            applyActive: () => applyActive().catch(() => {}),
            has: (path) => enabled && !!idx.files[path],
            count: (path) => (enabled && idx.files[path] ? idx.files[path].hunks : 0),
            marks: () => (enabled ? { files: idx.files, dirs: idx.dirs } : { files: {}, dirs: {} }),
            next: () => goTo(1),
            prev: () => goTo(-1),
            keepAtCursor: () => atCursor('keep'),
            undoAtCursor: () => atCursor('undo'),
            keepFile: () => file('keep'),
            undoFile: () => file('undo'),
            keepAll: () => everything('keep'),
            undoAll: () => everything('undo'),
            dispose: stop,
        };
    }

    root.CuttleIdeReview = { create: create };
})(globalThis);
