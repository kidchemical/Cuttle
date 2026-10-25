/* Shared agent-review client for the Git surfaces (Pending Changes strip,
 * diff modal) on every page that hosts them. Owner of the truth is the
 * server (api.agent_review): this module is transport, a short page-wide
 * cache, one live wake signal per origin, the commit guard and a pure
 * unified-row builder. Experimental flag `agent_review`; while it is off
 * every call resolves to "no review". */
(function (global) {
    'use strict';

    const LISTING_TTL_MS = 1500;
    const POLL_MS = 5000;
    const listeners = new Set();
    const cache = new Map(); // project key → {at, promise}
    let flag = null;
    const STREAM_LOCK = 'cuttle-agent-review-stream';
    const REVISION_CHANNEL = 'cuttle-agent-review-revision';
    let source = null;
    let channel = null;
    let live = false;
    let pollTimer = null;
    let revision = null;

    const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const keyOf = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    const qs = (params) => new URLSearchParams(params).toString();

    async function call(method, url, body) {
        const init = { method: method, credentials: 'include', cache: 'no-store' };
        if (body !== undefined) {
            init.headers = { 'Content-Type': 'application/json' };
            init.body = JSON.stringify(body);
        }
        const response = await fetch(url, init);
        const data = await response.json().catch(() => null);
        if (!response.ok || !data || data.success === false) {
            const err = new Error((data && data.error) || ('HTTP ' + response.status));
            err.status = response.status;
            err.conflict = !!(data && data.conflict);
            throw err;
        }
        return data;
    }

    /** Resolves once per page (re-checked after `refreshFlag`). */
    function enabled() {
        if (!flag) {
            flag = fetch('/api/experimental/flags', { credentials: 'include', cache: 'no-store' })
                .then((r) => (r.ok ? r.json() : null))
                .then((d) => !!(d && !d.kill_switch && (d.flags || []).some((f) => f.id === 'agent_review' && f.enabled)))
                .catch(() => false);
        }
        return flag;
    }

    // ── live wake signal (one per page) ─────────────────────────────
    function notify() {
        cache.clear();
        listeners.forEach((fn) => { try { fn(); } catch (_) {} });
    }
    function onRevision(next) {
        if (revision !== null && next !== revision) notify();
        revision = next;
    }
    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(() => {
            if (document.hidden || !listeners.size) return;
            call('GET', '/api/agent-review/revision').then((d) => onRevision(d.revision)).catch(() => {});
        }, POLL_MS);
    }
    // One stream per origin, not per frame: every shell pane is its own page,
    // and an EventSource per pane exhausted the browser's six HTTP/1.1
    // connections per host, so later fetches (commit suggest, commit) queued
    // forever. The Web Lock holder owns the stream and relays revisions over
    // a BroadcastChannel; when its frame goes away the next waiter takes over.
    function openStream(release) {
        const done = () => { if (release) { release(); release = null; } };
        if (!global.EventSource) { startPolling(); done(); return; }
        try {
            source = new global.EventSource('/api/agent-review/stream', { withCredentials: true });
            source.onmessage = (e) => {
                let next;
                try { next = JSON.parse(e.data).revision; } catch (_) { return; }
                onRevision(next);
                if (channel) { try { channel.postMessage({ revision: next }); } catch (_) {} }
            };
            source.onerror = () => {
                if (source) { try { source.close(); } catch (_) {} source = null; }
                startPolling();
                done();
            };
        } catch (_) { startPolling(); done(); }
    }
    function ensureLive() {
        if (live) return;
        live = true;
        const locks = global.navigator && global.navigator.locks;
        if (!locks || typeof global.BroadcastChannel !== 'function') { openStream(null); return; }
        try {
            channel = new global.BroadcastChannel(REVISION_CHANNEL);
            channel.onmessage = (e) => {
                if (e.data && typeof e.data.revision === 'number') onRevision(e.data.revision);
            };
        } catch (_) { channel = null; openStream(null); return; }
        locks.request(STREAM_LOCK, () => new Promise((release) => openStream(release)))
            .catch(() => startPolling());
    }
    /** Called whenever reviews may have changed; returns an unsubscribe. */
    function subscribe(fn) {
        listeners.add(fn);
        enabled().then((on) => { if (on && listeners.has(fn)) ensureLive(); });
        return () => listeners.delete(fn);
    }

    // ── reads ───────────────────────────────────────────────────────
    /** Open reviews under a project (registered workspace), or null. */
    async function listing(projectPath, opts) {
        if (!projectPath || !(await enabled())) return null;
        const key = keyOf(projectPath);
        const hit = cache.get(key);
        if (hit && !(opts && opts.force) && Date.now() - hit.at < LISTING_TTL_MS) return hit.promise;
        const promise = call('GET', '/api/agent-review/files?' + qs({ root: projectPath }))
            .then((d) => { if (revision === null) revision = d.revision; return d; })
            .catch(() => null);
        cache.set(key, { at: Date.now(), promise: promise });
        return promise;
    }
    /** {repo-relative path → review entry} for one repository's panel. */
    function forRepo(data, repoRoot) {
        const out = {};
        if (!data || !Array.isArray(data.files)) return out;
        if (repoRoot && keyOf(data.repo_root) !== keyOf(repoRoot)) return out;
        data.files.forEach((f) => { if (f.hunks) out[f.rel_path] = f; });
        return out;
    }
    async function entry(projectPath, repoRoot, relPath) {
        return forRepo(await listing(projectPath), repoRoot)[relPath] || null;
    }
    async function detail(projectPath, path) {
        return (await call('GET', '/api/agent-review/file?' + qs({ root: projectPath, path: path }))).review;
    }

    // ── actions ─────────────────────────────────────────────────────
    /** kind 'keep' | 'undo'; body {path|paths, hunks?, etag?} (paths relative to the project). */
    async function act(kind, projectPath, body) {
        try {
            return await call('POST', '/api/agent-review/' + (kind === 'undo' ? 'undo' : 'keep'),
                Object.assign({ root: projectPath }, body || {}));
        } finally {
            notify();
            // Undo rewrites files: every pending strip on this project re-reads git
            // (same channel + shell relay the strips use after a commit).
            const msg = { type: 'cuttle-pending-changes-changed', path: String(projectPath) };
            try { const ch = new BroadcastChannel('cuttle-pending-changes'); ch.postMessage(msg); ch.close(); } catch (_) {}
            try { if (global.parent && global.parent !== global) global.parent.postMessage(msg, '*'); } catch (_) {}
        }
    }

    // ── commit guard ────────────────────────────────────────────────
    async function commitPolicy() {
        try { return (await call('GET', '/api/settings/agent-review')).commit_policy || 'warn'; } catch (_) { return 'warn'; }
    }
    function setCommitPolicy(value) {
        return call('POST', '/api/settings/agent-review', { commit_policy: value });
    }
    /** Small modal: resolves {ok, remember}. */
    function confirmDialog(o) {
        return new Promise((resolve) => {
            const dialog = document.createElement('dialog');
            dialog.className = 'cuttle-review-dialog';
            dialog.innerHTML = '<form method="dialog">'
                + '<h3>' + esc(o.title) + '</h3>'
                + '<p>' + esc(o.text) + '</p>'
                + (o.files && o.files.length ? '<ul>' + o.files.slice(0, 8).map((f) => '<li>' + esc(f) + '</li>').join('')
                    + (o.files.length > 8 ? '<li>… and ' + (o.files.length - 8) + ' more</li>' : '') + '</ul>' : '')
                + (o.remember ? '<label class="cuttle-review-dialog-remember"><input type="checkbox" name="remember"> ' + esc(o.remember) + '</label>' : '')
                + '<div class="cuttle-review-dialog-actions">'
                + '<button value="" type="submit">Cancel</button>'
                + '<button value="ok" type="submit" class="is-primary" autofocus>' + esc(o.confirm || 'OK') + '</button>'
                + '</div></form>';
            document.body.appendChild(dialog);
            dialog.addEventListener('close', () => {
                const remember = !!(dialog.querySelector('[name="remember"]') || {}).checked;
                const ok = dialog.returnValue === 'ok';
                dialog.remove();
                resolve({ ok: ok, remember: remember });
            });
            dialog.showModal();
        });
    }
    /**
     * Before a commit: files with unreviewed agent changes are kept (the
     * commit accepts them). Policy `warn` asks first; resolves false to cancel.
     * `paths` are repo-relative paths about to be committed.
     */
    async function guardCommit(o) {
        const reviewed = forRepo(await listing(o.projectPath, { force: true }), o.repoRoot);
        const hits = (o.paths || []).filter((p) => reviewed[p]);
        if (!hits.length) return true;
        if ((await commitPolicy()) !== 'keep') {
            const hunks = hits.reduce((n, p) => n + reviewed[p].hunks, 0);
            const answer = await confirmDialog({
                title: 'Unreviewed agent changes',
                text: hits.length + ' file' + (hits.length === 1 ? ' has ' : 's have ') + hunks + ' agent change'
                    + (hunks === 1 ? '' : 's') + ' you have not reviewed. Committing keeps them.',
                files: hits, confirm: 'Keep & commit', remember: "Don't ask again (change in Settings → Agents → Advanced → Git)",
            });
            if (!answer.ok) return false;
            if (answer.remember) setCommitPolicy('keep').catch(() => {});
        }
        await act('keep', o.projectPath, { paths: hits.map((p) => reviewed[p].path) });
        return true;
    }

    // ── pure: unified rows for the diff modal ───────────────────────
    function splitLines(text) {
        if (!text) return [];
        const lines = String(text).split('\n');
        if (lines[lines.length - 1] === '') lines.pop();
        return lines.map((l) => l.replace(/\r$/, ''));
    }
    const strip = (line) => String(line).replace(/\r?\n$/, '');
    /** Review detail → [{id, header, additions, deletions, lines:[{type, text, old_no, new_no}]}]. */
    function unifiedHunks(review, context) {
        const ctx = context == null ? 3 : context;
        const cur = splitLines(review && review.current);
        const list = ((review && review.hunk_list) || []).slice().sort((a, b) => a.new_start - b.new_start);
        let shown = 0; // last current-file line already shown
        return list.map((h, i) => {
            const lines = [];
            const before = h.old_start - h.new_start;
            for (let n = Math.max(shown + 1, h.new_start - ctx); n < h.new_start; n++) {
                lines.push({ type: 'context', text: cur[n - 1], new_no: n, old_no: n + before });
            }
            (h.removed || []).forEach((t, k) => lines.push({ type: 'del', text: strip(t), old_no: h.old_start + k }));
            (h.added || []).forEach((t, k) => lines.push({ type: 'add', text: strip(t), new_no: h.new_start + k }));
            const end = h.new_start + h.new_count; // first line after the hunk
            const after = (h.old_start + h.old_count) - end;
            const next = list[i + 1];
            const stop = Math.min(cur.length, end - 1 + ctx, next ? next.new_start - 1 : Infinity);
            for (let n = end; n <= stop; n++) lines.push({ type: 'context', text: cur[n - 1], new_no: n, old_no: n + after });
            shown = Math.max(shown, stop, end - 1);
            return {
                id: h.id, lines: lines, additions: h.new_count, deletions: h.old_count,
                header: '@@ -' + h.old_start + ',' + h.old_count + ' +' + h.new_start + ',' + h.new_count + ' @@',
            };
        });
    }

    const api = {
        enabled, refreshFlag: () => { flag = null; return enabled(); }, subscribe, listing, forRepo, entry,
        detail, act, commitPolicy, setCommitPolicy, guardCommit, confirmDialog, unifiedHunks,
    };
    global.CuttleAgentReview = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
