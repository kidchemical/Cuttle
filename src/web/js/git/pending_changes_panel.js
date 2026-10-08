/* Shared Pending Changes strip controller (chat + Git pages). Multi-repo aware. */
(function (global) {
    'use strict';

    function defaultFetchWithTimeout(url, opts, timeoutMs) {
        opts = opts || {};
        const ctrl = new AbortController();
        const timer = setTimeout(function () {
            try { ctrl.abort(); } catch (_) {}
        }, timeoutMs == null ? 20000 : timeoutMs);
        const parent = opts.signal;
        const onParentAbort = function () {
            try { ctrl.abort(); } catch (_) {}
        };
        if (parent) {
            if (parent.aborted) {
                clearTimeout(timer);
                ctrl.abort();
            } else {
                parent.addEventListener('abort', onParentAbort, { once: true });
            }
        }
        // A long-lived parent signal must not collect one listener per request.
        return fetch(url, Object.assign({}, opts, { signal: ctrl.signal })).finally(function () {
            clearTimeout(timer);
            if (parent) parent.removeEventListener('abort', onParentAbort);
        });
    }

    function defaultEsc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function normalizePathKey(p) {
        return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    }

    function repoBasename(repoRoot) {
        const norm = String(repoRoot || '').replace(/\\/g, '/').replace(/\/+$/, '');
        const parts = norm.split('/').filter(Boolean);
        return parts.length ? parts[parts.length - 1] : norm;
    }

    function panelMarkup() {
        return (
            '<div class="pending-changes" hidden data-pending-panel aria-label="Pending git changes">'
            + '<div class="pending-changes-header">'
            + '<button type="button" class="pending-changes-toggle" data-pc="toggle" aria-expanded="false">'
            + '<span class="pending-changes-chevron" aria-hidden="true"></span>'
            + '<span class="pending-changes-title">Pending changes</span>'
            + '<span class="pending-changes-repo-chip" data-pc="chip" hidden></span>'
            + '<span class="pending-changes-meta" data-pc="meta"></span>'
            + '<span class="pending-changes-stats" data-pc="stats" aria-hidden="true"></span>'
            + '</button>'
            + '<button type="button" class="pending-changes-commit-btn pending-changes-commit-quick" data-pc="commitQuick" title="Auto-suggest a message and commit">Commit</button>'
            + '</div>'
            + '<div class="pending-changes-body" data-pc="body" hidden>'
            + '<div class="pending-changes-select-bar" data-pc="selectBar">'
            + '<button type="button" class="pending-changes-select-btn" data-pc="selectAll" title="Include all files in the commit">All</button>'
            + '<button type="button" class="pending-changes-select-btn" data-pc="selectNone" title="Exclude all files from the commit">None</button>'
            + '<span class="pending-changes-select-hint">check = include · Ig = .gitignore</span>'
            + '</div>'
            + '<ul class="pending-changes-list" data-pc="list"></ul>'
            + '<div class="pending-changes-commit" data-pc="commitForm" hidden>'
            + '<div class="pending-changes-commit-header">'
            + '<span class="pending-changes-commit-label">Commit message</span>'
            + '<button type="button" class="pending-changes-commit-edit" data-pc="commitEdit" title="Edit message" aria-label="Edit commit message" hidden>'
            + '<svg class="pending-changes-commit-edit-pencil" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            + '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>'
            + '</svg>'
            + '<svg class="pending-changes-commit-edit-check" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden>'
            + '<polyline points="20 6 9 17 4 12"/>'
            + '</svg>'
            + '</button>'
            + '</div>'
            + '<div class="pending-changes-commit-preview-wrap" data-pc="commitPreviewWrap">'
            + '<div class="pending-changes-commit-loading" data-pc="commitLoading" hidden>'
            + '<span class="pending-changes-commit-spinner" aria-hidden="true"></span>'
            + '<span>Suggesting message…</span>'
            + '</div>'
            + '<div class="pending and-changes-commit-preview" data-pc="commitPreview" hidden></div>'
            + '</div>'
            + '<textarea class="pending-changes-commit-message" data-pc="commitMessage" rows="2" placeholder="Write a commit message…" hidden></textarea>'
            + '<div class="pending-changes-commit-actions">'
            + '<button type="button" class="pending-changes-refresh" data-pc="commitCancel">Cancel</button>'
            + '<button type="button" class="pending-changes-refresh" data-pc="commitResuggest" title="Regenerate from current diff + prompts">Re-suggest</button>'
            + '<button type="button" class="pending-changes-commit-submit" data-pc="commitSubmit">Commit</button>'
            + '</div>'
            + '<div class="pending-changes-commit-status" data-pc="commitStatus" hidden></div>'
            + '</div>'
            + '<div class="pending-changes-footer">'
            + '<div class="pending-changes-footer-actions">'
            + '<button type="button" class="pending-changes-refresh" data-pc="refresh" title="Refresh">Refresh</button>'
            + '<button type="button" class="pending-changes-commit-btn" data-pc="commitBtn" title="Commit selected pending changes">Commit</button>'
            + '</div>'
            + '<span class="pending-changes-branch" data-pc="branch"></span>'
            + '</div>'
            + '</div>'
            + '</div>'
        ).replace('pending and-changes-commit-preview', 'pending-changes-commit-preview');
    }

    function normalizeReposPayload(data) {
        if (!data || typeof data !== 'object') return [];
        if (Array.isArray(data.repos) && data.repos.length) {
            return data.repos.slice();
        }
        if (data.repo_root || data.files || data.clean != null) {
            return [data];
        }
        return [];
    }

    /**
     * @param {object} options
     * @param {() => ({path:string,name?:string}|null)} options.getProject
     * @param {() => (string|number|null|undefined)} [options.getSessionId]
     * @param {() => string[]} [options.getPrompts]
     * @param {(relPath:string, meta:{repoRoot:string}) => void} [options.onOpenDiff]
     * @param {() => void} [options.onAfterCommit]
     * @param {() => void} [options.onAfterIgnore]
     * @param {() => boolean} [options.isPageBackgrounded]
     * @param {ParentNode} [options.root]
     * @param {HTMLElement|null} [options.host]
     * @param {boolean} [options.useShellHub]
     * @param {Function} [options.fetchWithTimeout]
     * @param {(s:any)=>string} [options.escapeHtml]
     */
    function create(options) {
        options = options || {};
        const docRoot = options.root || document;
        const getProject = options.getProject || function () { return null; };
        const getSessionId = options.getSessionId || function () { return null; };
        const getPrompts = options.getPrompts || function () { return []; };
        const onOpenDiff = options.onOpenDiff || function () {};
        const onAfterCommit = options.onAfterCommit || function () {};
        const onAfterIgnore = options.onAfterIgnore || function () {};
        const isPageBackgrounded = options.isPageBackgrounded || function () { return document.hidden; };
        const fetchWithTimeout = options.fetchWithTimeout || defaultFetchWithTimeout;
        const escapeHtmlInline = options.escapeHtml || defaultEsc;
        const useShellHub = !!options.useShellHub;

        let host = options.host || null;
        if (!host) {
            host = docRoot.getElementById
                ? docRoot.getElementById('pendingChangesHost')
                : document.getElementById('pendingChangesHost');
        }
        if (!host) {
            console.warn('[pending-changes] no host element');
            return {
                refresh: async function () {},
                scheduleRefresh: function () {},
                hide: function () {},
                destroy: function () {},
                notifyChanged: function () {},
                onInvalidated: function () {},
                applyHubPayload: function () {},
                getRepos: function () { return []; },
                getFilesForRepo: function () { return []; },
            };
        }

        /** @type {Map<string, object>} */
        const panels = new Map();
        let lastReposList = [];
        let pendingChangesTimer = null;
        let pendingChangesInFlight = false;
        let pendingChangesDirty = false;
        let pendingChangesSuppressFocusRefreshUntil = 0;
        let pendingChangesFetchGen = 0;
        let destroyed = false;
        let pollTimer = null;
        let projectPathBound = '';

        function currentProject() {
            return getProject();
        }

        function scheduleRefresh(delayMs, refreshOpts) {
            if (pendingChangesTimer) clearTimeout(pendingChangesTimer);
            pendingChangesTimer = setTimeout(function () {
                pendingChangesTimer = null;
                refresh(refreshOpts);
            }, delayMs == null ? 400 : delayMs);
        }

        function notifySiblingChanged(data) {
            const path = currentProject() && currentProject().path;
            if (!path) return;
            const msg = { type: 'cuttle-pending-changes-changed', path: String(path) };
            if (data && typeof data === 'object') msg.data = data;
            try {
                if (window.parent && window.parent !== window) {
                    window.parent.postMessage(msg, '*');
                }
            } catch (_) {}
            try {
                if (_broadcast) _broadcast.postMessage(msg);
            } catch (_) {}
        }

        function requestShellRefresh(projectPath) {
            if (!useShellHub) return false;
            const path = projectPath || (currentProject() && currentProject().path);
            if (!path) return false;
            try {
                window.parent.postMessage({
                    type: 'cuttle-pending-changes-refresh',
                    path: String(path),
                }, '*');
                return true;
            } catch (_) {
                return false;
            }
        }

        function onInvalidated(projectPath, data) {
            if (!currentProject() || !currentProject().path) return;
            if (
                projectPath
                && normalizePathKey(projectPath) !== normalizePathKey(currentProject().path)
            ) {
                return;
            }
            if (data && typeof data === 'object') {
                applyHubPayload(projectPath || currentProject().path, data);
                return;
            }
            pendingChangesSuppressFocusRefreshUntil = 0;
            if (requestShellRefresh(projectPath || currentProject().path)) return;
            if (pendingChangesInFlight) {
                pendingChangesDirty = true;
                return;
            }
            scheduleRefresh(40);
        }

        let _broadcast = null;
        try {
            _broadcast = new BroadcastChannel('cuttle-pending-changes');
            _broadcast.addEventListener('message', function (ev) {
                const data = ev && ev.data;
                if (!data || data.type !== 'cuttle-pending-changes-changed') return;
                onInvalidated(data.path, data.data);
            });
        } catch (_) {
            _broadcast = null;
        }

        function hideAll() {
            panels.forEach(function (p) {
                try { p.destroy(); } catch (_) {}
            });
            panels.clear();
            host.innerHTML = '';
            lastReposList = [];
        }

        function ensurePanel(repoRoot, label, showChip) {
            const key = normalizePathKey(repoRoot);
            let slot = panels.get(key);
            if (slot) {
                slot.setChip(label, showChip);
                return slot;
            }
            const wrap = document.createElement('div');
            wrap.innerHTML = panelMarkup();
            const el = wrap.firstElementChild;
            el.setAttribute('data-repo-root', String(repoRoot || ''));
            host.appendChild(el);
            slot = createRepoPanel(el, repoRoot, label, showChip);
            panels.set(key, slot);
            return slot;
        }

        function prunePanels(keepKeys) {
            const keep = new Set(keepKeys);
            [...panels.keys()].forEach(function (key) {
                if (keep.has(key)) return;
                const slot = panels.get(key);
                try { slot.destroy(); } catch (_) {}
                panels.delete(key);
            });
        }

        function applyReposData(repos, projectMeta) {
            const dirty = (repos || []).filter(function (r) {
                if (!r || r.clean) return false;
                const files = Array.isArray(r.files) ? r.files : [];
                const n = Number(r.pending_file_count) || (r.totals && r.totals.files) || files.length;
                return n > 0 || files.length > 0;
            });
            lastReposList = (repos || []).map(function (r) {
                return {
                    repo_root: r.repo_root,
                    label: r.label || repoBasename(r.repo_root),
                    branch: r.branch || null,
                    clean: !!r.clean,
                };
            });
            const multi = lastReposList.length > 1;
            if (!dirty.length) {
                hideAll();
                return;
            }
            const keys = dirty.map(function (r) { return normalizePathKey(r.repo_root); });
            prunePanels(keys);
            dirty.forEach(function (r) {
                const label = r.label || repoBasename(r.repo_root);
                const slot = ensurePanel(r.repo_root, label, multi);
                slot.render(Object.assign({}, r, {
                    project: projectMeta || (currentProject() && {
                        name: currentProject().name,
                        path: currentProject().path,
                    }),
                }));
            });
        }

        function applyHubPayload(projectPath, data) {
            if (!currentProject() || !currentProject().path) return;
            if (
                projectPath
                && normalizePathKey(projectPath) !== normalizePathKey(currentProject().path)
            ) {
                return;
            }
            if (!data || typeof data !== 'object' || !data.success) return;
            const repos = normalizeReposPayload(data);
            if (!repos.length) {
                hideAll();
                return;
            }
            applyReposData(repos, data.project);
        }

        async function refresh(opts) {
            if (!currentProject() || !currentProject().path) {
                hideAll();
                return;
            }
            if (pendingChangesInFlight) {
                pendingChangesDirty = true;
                return;
            }
            pendingChangesInFlight = true;
            pendingChangesDirty = false;
            const fetchGen = ++pendingChangesFetchGen;
            const pathAtStart = String(currentProject().path);
            if (pathAtStart !== projectPathBound) {
                projectPathBound = pathAtStart;
                hideAll();
            }
            const retryBusy = !!(opts && opts._retryBusy);
            try {
                if (useShellHub && !(opts && opts.forceLocal) && requestShellRefresh(pathAtStart)) {
                    return;
                }
                const qs = new URLSearchParams();
                qs.set('path', pathAtStart);
                const resp = await fetchWithTimeout(
                    '/api/git/pending-changes?' + qs.toString(),
                    { credentials: 'include', cache: 'no-store' },
                    20000
                );
                if (fetchGen !== pendingChangesFetchGen) return;
                if (!currentProject() || String(currentProject().path) !== pathAtStart) return;
                const data = await resp.json().catch(function () { return null; });
                if (fetchGen !== pendingChangesFetchGen) return;
                if (!data || !data.success) {
                    const err = String((data && data.error) || '');
                    if (!retryBusy && /already running/i.test(err)) {
                        scheduleRefresh(750, { _retryBusy: true });
                        return;
                    }
                    if (!panels.size) hideAll();
                    return;
                }
                applyReposData(normalizeReposPayload(data), data.project);
                if (!(opts && opts.fromHub)) notifySiblingChanged(data);
            } catch (_) {
            } finally {
                if (fetchGen === pendingChangesFetchGen) {
                    pendingChangesInFlight = false;
                    if (pendingChangesDirty) {
                        pendingChangesDirty = false;
                        scheduleRefresh(120);
                    }
                }
            }
        }

        function createRepoPanel(panelEl, repoRoot, label, showChip) {
            let expanded = false;
            let hasSnapshot = false;
            let quickCommitInFlight = false;
            let fileInclusion = {};
            let filesSnapshot = [];
            let truncated = false;
            let totalCount = 0;
            let includeUnlisted = true;
            let suggestGen = 0;
            let suggestInFlight = false;
            let avoidMessages = [];
            let commitEditMode = false;
            let bound = false;

            function qs(role) {
                return panelEl.querySelector('[data-pc="' + role + '"]');
            }

            function setChip(nextLabel, show) {
                label = nextLabel || label;
                const chip = qs('chip');
                if (!chip) return;
                const text = label || repoBasename(repoRoot);
                chip.textContent = text;
                chip.title = String(repoRoot || '');
                chip.hidden = !show;
            }

            function setExpanded(on) {
                expanded = !!on;
                panelEl.classList.toggle('is-expanded', expanded);
                const body = qs('body');
                const toggle = qs('toggle');
                if (body) body.hidden = !expanded;
                if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            }

            function formatStat(n, sign) {
                const v = Number(n) || 0;
                if (v <= 0) return '';
                return sign + (v > 9999 ? '9999+' : String(v));
            }

            function statusBadge(status) {
                const s = String(status || 'modified');
                if (s === 'untracked') return 'U';
                if (s === 'added') return 'A';
                if (s === 'deleted') return 'D';
                if (s === 'renamed') return 'R';
                if (s === 'conflict') return '!';
                return 'M';
            }

            function isIncluded(relPath) {
                const p = String(relPath || '');
                if (!p) return false;
                if (Object.prototype.hasOwnProperty.call(fileInclusion, p)) return !!fileInclusion[p];
                return true;
            }

            function includedPaths() {
                return filesSnapshot.filter(isIncluded);
            }

            function unlistedCount() {
                if (!truncated) return 0;
                return Math.max(0, totalCount - filesSnapshot.length);
            }

            function effectiveCount() {
                const visible = includedPaths().length;
                if (truncated && includeUnlisted) return visible + unlistedCount();
                return visible;
            }

            function buildFilesPayload() {
                const included = includedPaths();
                if (truncated && includeUnlisted) {
                    if (included.length === filesSnapshot.length && filesSnapshot.length > 0) {
                        return { all_pending: true };
                    }
                    return {
                        files: included,
                        include_unlisted: true,
                        shown_files: filesSnapshot.slice(),
                    };
                }
                return { files: included };
            }

            function updateSelectionChrome() {
                const meta = qs('meta');
                const submitBtn = qs('commitSubmit');
                const quickBtn = qs('commitQuick');
                const listed = filesSnapshot.length;
                const includedVisible = includedPaths();
                const effective = effectiveCount();
                const unlisted = unlistedCount();
                if (meta) {
                    if (!listed && !totalCount) meta.textContent = '';
                    else if (truncated && totalCount > listed) {
                        meta.textContent = effective === totalCount
                            ? ('· ' + totalCount + ' files')
                            : ('· ' + effective + '/' + totalCount + ' selected');
                    } else if (includedVisible.length === listed) {
                        meta.textContent = '· ' + listed + ' file' + (listed === 1 ? '' : 's');
                    } else {
                        meta.textContent = '· ' + includedVisible.length + '/' + listed + ' selected';
                    }
                }
                if (submitBtn) {
                    submitBtn.textContent = 'Commit';
                    if (!suggestInFlight && !quickCommitInFlight) submitBtn.disabled = effective === 0;
                }
                if (quickBtn && !quickCommitInFlight) {
                    quickBtn.textContent = 'Commit';
                    if (!suggestInFlight) quickBtn.disabled = effective === 0;
                }
                const list = qs('list');
                if (list) {
                    list.querySelectorAll('.pending-changes-row[data-rel-path]').forEach(function (row) {
                        const rel = row.getAttribute('data-rel-path') || '';
                        const on = isIncluded(rel);
                        row.classList.toggle('is-excluded', !on);
                        const cb = row.querySelector('.pending-changes-include');
                        if (cb) cb.checked = on;
                    });
                    const moreRow = list.querySelector('.pending-changes-row.is-more');
                    if (moreRow) {
                        moreRow.classList.toggle('is-excluded', !includeUnlisted);
                        const moreCb = moreRow.querySelector('.pending-changes-include-more');
                        if (moreCb) moreCb.checked = includeUnlisted;
                        const moreLabel = moreRow.querySelector('.pending-changes-more-label');
                        if (moreLabel && unlisted > 0) {
                            moreLabel.textContent = '+' + unlisted + ' more (not listed)';
                        }
                    }
                }
            }

            function syncPreviewFromTa() {
                const ta = qs('commitMessage');
                const preview = qs('commitPreview');
                if (!preview) return;
                preview.textContent = ta ? String(ta.value || '') : '';
            }

            function setSuggesting(loading) {
                const loadingEl = qs('commitLoading');
                const preview = qs('commitPreview');
                const previewWrap = qs('commitPreviewWrap');
                const ta = qs('commitMessage');
                const editBtn = qs('commitEdit');
                if (loading) {
                    commitEditMode = false;
                    if (previewWrap) previewWrap.hidden = false;
                    if (loadingEl) loadingEl.hidden = false;
                    if (preview) preview.hidden = true;
                    if (ta) ta.hidden = true;
                    if (editBtn) {
                        editBtn.hidden = true;
                        editBtn.classList.remove('is-editing');
                        const pencil = editBtn.querySelector('.pending-changes-commit-edit-pencil');
                        const check = editBtn.querySelector('.pending-changes-commit-edit-check');
                        if (pencil) pencil.hidden = false;
                        if (check) check.hidden = true;
                    }
                    return;
                }
                if (loadingEl) loadingEl.hidden = true;
                applyEditMode(commitEditMode);
            }

            function applyEditMode(editing) {
                commitEditMode = !!editing;
                const previewWrap = qs('commitPreviewWrap');
                const preview = qs('commitPreview');
                const loadingEl = qs('commitLoading');
                const ta = qs('commitMessage');
                const editBtn = qs('commitEdit');
                const hasText = !!(ta && String(ta.value || '').trim());
                if (loadingEl) loadingEl.hidden = true;
                if (commitEditMode) {
                    if (previewWrap) previewWrap.hidden = true;
                    if (preview) preview.hidden = true;
                    if (ta) {
                        ta.hidden = false;
                        try { ta.focus({ preventScroll: true }); } catch (_) { try { ta.focus(); } catch (__) {} }
                    }
                } else {
                    syncPreviewFromTa();
                    if (previewWrap) previewWrap.hidden = false;
                    if (preview) preview.hidden = false;
                    if (ta) ta.hidden = true;
                }
                if (editBtn) {
                    editBtn.hidden = !hasText && !commitEditMode;
                    editBtn.classList.toggle('is-editing', commitEditMode);
                    editBtn.title = commitEditMode ? 'Done editing' : 'Edit message';
                    editBtn.setAttribute('aria-label', commitEditMode ? 'Done editing' : 'Edit commit message');
                    const pencil = editBtn.querySelector('.pending-changes-commit-edit-pencil');
                    const check = editBtn.querySelector('.pending-changes-commit-edit-check');
                    if (pencil) pencil.hidden = commitEditMode;
                    if (check) check.hidden = !commitEditMode;
                }
            }

            function setCommitFormVisible(visible) {
                const form = qs('commitForm');
                const btn = qs('commitBtn');
                const status = qs('commitStatus');
                if (form) form.hidden = !visible;
                if (btn) btn.setAttribute('aria-expanded', visible ? 'true' : 'false');
                if (!visible && status) {
                    status.hidden = true;
                    status.textContent = '';
                    status.classList.remove('is-error');
                }
                if (visible) {
                    updateSelectionChrome();
                    const ta = qs('commitMessage');
                    const existing = ta ? String(ta.value || '').trim() : '';
                    if (existing) {
                        setSuggesting(false);
                        applyEditMode(false);
                        setStatus('', false);
                    } else {
                        setSuggesting(true);
                        suggestMessage({ force: false });
                    }
                } else {
                    commitEditMode = false;
                    avoidMessages = [];
                }
            }

            function setStatus(msg, isError) {
                const status = qs('commitStatus');
                if (!status) return;
                if (!msg) {
                    status.hidden = true;
                    status.textContent = '';
                    status.classList.remove('is-error');
                    return;
                }
                status.hidden = false;
                status.textContent = msg;
                status.classList.toggle('is-error', !!isError);
            }

            async function suggestMessage(opts) {
                const force = !!(opts && opts.force);
                if (!currentProject() || !currentProject().path) return null;
                const ta = qs('commitMessage');
                const submitBtn = qs('commitSubmit');
                if (!force && ta && String(ta.value || '').trim()) {
                    applyEditMode(false);
                    return String(ta.value).trim();
                }
                const gen = ++suggestGen;
                suggestInFlight = true;
                if (submitBtn) submitBtn.disabled = true;
                setSuggesting(true);
                setStatus('', false);
                try {
                    const included = includedPaths();
                    const body = {
                        path: currentProject().path,
                        repo_root: repoRoot,
                    };
                    if (included.length) body.files = included;
                    const sid = getSessionId();
                    if (sid != null && sid !== '') {
                        const n = Number(sid);
                        if (Number.isFinite(n)) body.session_id = n;
                    }
                    const prompts = getPrompts();
                    if (Array.isArray(prompts) && prompts.length) body.prompts = prompts.slice(-12);
                    if (force && avoidMessages.length) body.avoid = avoidMessages.slice();
                    else if (ta && String(ta.value || '').trim()) {
                        const cur = String(ta.value).trim();
                        if (!avoidMessages.some(function (x) { return String(x).toLowerCase() === cur.toLowerCase(); })) {
                            avoidMessages.push(cur);
                        }
                    }
                    const resp = await fetchWithTimeout(
                        '/api/git/suggest-commit-message',
                        {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body),
                        },
                        90000
                    );
                    const data = await resp.json().catch(function () { return null; });
                    if (gen !== suggestGen) return null;
                    if (!data || !data.success || !data.message) {
                        const err = (data && data.error) || 'Could not suggest a message';
                        setSuggesting(false);
                        applyEditMode(true);
                        setStatus(err, true);
                        return null;
                    }
                    if (ta && (force || !String(ta.value || '').trim())) ta.value = String(data.message);
                    const remembered = String((ta && ta.value) || data.message || '').trim();
                    if (remembered && !avoidMessages.some(function (x) {
                        return String(x).toLowerCase() === remembered.toLowerCase();
                    })) {
                        avoidMessages.push(remembered);
                        if (avoidMessages.length > 12) avoidMessages = avoidMessages.slice(-12);
                    }
                    const srcLabel = {
                        heuristic: 'from chat prompts',
                        anthropic: 'Haiku',
                        openai: 'OpenAI',
                        local: 'local LLM',
                    };
                    const src = srcLabel[data.source] || (data.source || 'suggested');
                    setSuggesting(false);
                    applyEditMode(false);
                    setStatus(src + ' — pencil to edit', false);
                    return String((ta && ta.value) || data.message).trim();
                } catch (e) {
                    if (gen !== suggestGen) return null;
                    setSuggesting(false);
                    applyEditMode(true);
                    setStatus((e && e.message) || 'Suggest failed', true);
                    return null;
                } finally {
                    if (gen === suggestGen) {
                        suggestInFlight = false;
                        updateSelectionChrome();
                    }
                }
            }

            async function submitCommit() {
                if (!currentProject() || !currentProject().path) {
                    setStatus('No project selected', true);
                    return;
                }
                if (!effectiveCount()) {
                    setStatus('Select at least one file to commit', true);
                    return;
                }
                const ta = qs('commitMessage');
                const submitBtn = qs('commitSubmit');
                let message = ta ? String(ta.value || '').trim() : '';
                if (!message) message = (await suggestMessage({ force: true })) || '';
                if (!message) {
                    setStatus('Enter a commit message', true);
                    if (ta) ta.focus();
                    return;
                }
                if (submitBtn) submitBtn.disabled = true;
                setStatus('Committing…', false);
                pendingChangesSuppressFocusRefreshUntil = Date.now() + 2500;
                try {
                    const resp = await fetchWithTimeout(
                        '/api/git/commit',
                        {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(Object.assign({
                                path: currentProject().path,
                                repo_root: repoRoot,
                                message: message,
                            }, buildFilesPayload())),
                        },
                        180000
                    );
                    const data = await resp.json().catch(function () { return null; });
                    if (!data || !data.success) {
                        const err = (data && data.error) || ('Commit failed (' + resp.status + ')');
                        setStatus(err, true);
                        (window.showToast || function () {})(err, 'error');
                        return;
                    }
                    const short = data.commit || '';
                    const n = data.files_count || (data.files_committed || []).length || 0;
                    let okMsg = 'Committed ' + short + (n ? ' · ' + n + ' file' + (n === 1 ? '' : 's') : '');
                    (window.showToast || function () {})(okMsg, 'success');
                    if (ta) ta.value = '';
                    includedPaths().forEach(function (p) { delete fileInclusion[p]; });
                    includeUnlisted = true;
                    setCommitFormVisible(false);
                    pendingChangesInFlight = false;
                    pendingChangesFetchGen += 1;
                    await refresh({ forceLocal: true });
                    notifySiblingChanged();
                    onAfterCommit();
                } catch (e) {
                    const err = (e && e.message) || 'Commit failed';
                    setStatus(err, true);
                    (window.showToast || function () {})(err, 'error');
                } finally {
                    if (submitBtn) submitBtn.disabled = false;
                    updateSelectionChrome();
                }
            }

            async function quickCommit() {
                if (quickCommitInFlight || suggestInFlight) return;
                if (!currentProject() || !currentProject().path) {
                    (window.showToast || function () {})('No project selected', 'error');
                    return;
                }
                if (!effectiveCount()) {
                    (window.showToast || function () {})('Select at least one file to commit', 'error');
                    return;
                }
                quickCommitInFlight = true;
                pendingChangesSuppressFocusRefreshUntil = Date.now() + 8000;
                const quickBtn = qs('commitQuick');
                try {
                    if (quickBtn) {
                        quickBtn.disabled = true;
                        quickBtn.textContent = 'Suggesting…';
                    }
                    const ta = qs('commitMessage');
                    if (ta) ta.value = '';
                    const message = await suggestMessage({ force: true });
                    if (!message) {
                        (window.showToast || function () {})('Could not suggest a commit message', 'error');
                        return;
                    }
                    if (quickBtn) quickBtn.textContent = 'Committing…';
                    await submitCommit();
                } finally {
                    quickCommitInFlight = false;
                    if (quickBtn) quickBtn.textContent = 'Commit';
                    updateSelectionChrome();
                }
            }

            async function ignoreFile(relPath) {
                if (!currentProject() || !currentProject().path || !relPath) return;
                pendingChangesSuppressFocusRefreshUntil = Date.now() + 2500;
                try {
                    const resp = await fetchWithTimeout(
                        '/api/git/ignore',
                        {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                path: currentProject().path,
                                repo_root: repoRoot,
                                file: relPath,
                            }),
                        },
                        20000
                    );
                    const data = await resp.json().catch(function () { return null; });
                    if (!data || !data.success) {
                        (window.showToast || function () {})((data && data.error) || 'Ignore failed', 'error');
                        return;
                    }
                    delete fileInclusion[relPath];
                    (window.showToast || function () {})('Ignored ' + relPath, 'success');
                    pendingChangesInFlight = false;
                    pendingChangesFetchGen += 1;
                    await refresh({ forceLocal: true });
                    notifySiblingChanged();
                    onAfterIgnore();
                } catch (e) {
                    (window.showToast || function () {})((e && e.message) || 'Ignore failed', 'error');
                }
            }

            function render(data) {
                if (!data) return;
                panelEl.hidden = false;
                hasSnapshot = true;
                setChip(data.label || label, showChip);
                const files = Array.isArray(data.files) ? data.files : [];
                const paths = files.map(function (f) { return String(f.path || ''); }).filter(Boolean);
                const keep = {};
                paths.forEach(function (p) {
                    if (Object.prototype.hasOwnProperty.call(fileInclusion, p)) keep[p] = fileInclusion[p];
                });
                fileInclusion = keep;
                filesSnapshot = paths;
                truncated = !!data.truncated;
                const apiTotal = Number(data.pending_file_count);
                totalCount = Number.isFinite(apiTotal) && apiTotal > 0 ? apiTotal : paths.length;
                if (!truncated) includeUnlisted = false;

                const nFiles = truncated ? totalCount : ((data.totals && data.totals.files) || files.length);
                const adds = (data.totals && data.totals.additions) || 0;
                const dels = (data.totals && data.totals.deletions) || 0;
                const meta = qs('meta');
                const stats = qs('stats');
                const branchEl = qs('branch');
                const list = qs('list');
                if (meta) meta.textContent = '· ' + nFiles + ' file' + (nFiles === 1 ? '' : 's');
                if (stats) {
                    const a = formatStat(adds, '+');
                    const d = formatStat(dels, '-');
                    stats.innerHTML =
                        (a ? '<span class="pending-changes-add">' + a + '</span>' : '')
                        + (d ? '<span class="pending-changes-del">' + d + '</span>' : '');
                }
                if (branchEl) {
                    const br = data.branch ? String(data.branch) : '';
                    const name = (data.project && data.project.name)
                        || (currentProject() && currentProject().name)
                        || '';
                    branchEl.textContent = [name, br].filter(Boolean).join(' · ');
                    branchEl.title = String(repoRoot || '') + (br ? ' (' + br + ')' : '');
                }
                if (list) {
                    let html = files.map(function (f) {
                        const rawPath = String(f.path || '');
                        const path = escapeHtmlInline(rawPath);
                        const st = String(f.status || 'modified');
                        const included = isIncluded(rawPath);
                        const a = formatStat(f.additions, '+');
                        const d = formatStat(f.deletions, '-');
                        const statsHtml =
                            '<span class="pending-changes-item-stats">'
                            + (a ? '<span class="pending-changes-add">' + a + '</span>' : '')
                            + (d ? '<span class="pending-changes-del">' + d + '</span>' : '')
                            + '</span>';
                        const badge =
                            '<span class="pending-changes-badge" data-status="'
                            + escapeHtmlInline(st) + '">' + statusBadge(st) + '</span>';
                        const fileSpan = '<span class="pending-changes-file" title="' + path + '">' + path + '</span>';
                        const check =
                            '<input type="checkbox" class="pending-changes-include" data-rel-path="'
                            + path + '" ' + (included ? 'checked ' : '')
                            + 'title="Include in commit" aria-label="Include ' + path + ' in commit">';
                        const ignoreBtn =
                            '<button type="button" class="pending-changes-ignore-btn" data-rel-path="'
                            + path + '" title="Add to .gitignore (and untrack if needed)" aria-label="Ignore '
                            + path + '">Ig</button>';
                        const diffBtn =
                            '<button type="button" class="pending-changes-diff-btn" data-rel-path="'
                            + path + '" title="View diff" aria-label="View diff for ' + path + '">Δ</button>';
                        const main =
                            '<button type="button" class="pending-changes-item" data-rel-path="'
                            + path + '" title="View diff">' + badge + fileSpan + statsHtml + '</button>';
                        return (
                            '<li class="pending-changes-row'
                            + (included ? '' : ' is-excluded')
                            + '" data-rel-path="' + path + '">'
                            + check + main + ignoreBtn + diffBtn + '</li>'
                        );
                    }).join('');
                    const moreN = unlistedCount();
                    if (truncated && moreN > 0) {
                        const moreOn = !!includeUnlisted;
                        html += (
                            '<li class="pending-changes-row is-more'
                            + (moreOn ? '' : ' is-excluded') + '">'
                            + '<input type="checkbox" class="pending-changes-include-more" '
                            + (moreOn ? 'checked ' : '')
                            + 'title="Include all files not shown in this list" '
                            + 'aria-label="Include ' + moreN + ' more files not listed">'
                            + '<span class="pending-changes-more-label" title="Truncated list — check to include the rest in the commit">'
                            + '+' + moreN + ' more (not listed)</span></li>'
                        );
                    }
                    list.innerHTML = html;
                }
                panelEl.classList.toggle('is-expanded', expanded);
                const body = qs('body');
                const toggle = qs('toggle');
                if (body) body.hidden = !expanded;
                if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                updateSelectionChrome();
                if (!bound) bind();
            }

            function bind() {
                if (bound) return;
                bound = true;
                const list = qs('list');
                if (list) {
                    list.addEventListener('click', function (e) {
                        const t = e.target;
                        const ignoreBtn = t && t.closest ? t.closest('.pending-changes-ignore-btn') : null;
                        if (ignoreBtn) {
                            e.preventDefault();
                            e.stopPropagation();
                            ignoreFile(ignoreBtn.getAttribute('data-rel-path') || '');
                            return;
                        }
                        const diffBtn = t && t.closest ? t.closest('.pending-changes-diff-btn') : null;
                        if (diffBtn) {
                            e.preventDefault();
                            e.stopPropagation();
                            onOpenDiff(diffBtn.getAttribute('data-rel-path') || '', { repoRoot: repoRoot });
                            return;
                        }
                        const itemBtn = t && t.closest ? t.closest('.pending-changes-item') : null;
                        if (itemBtn) {
                            e.preventDefault();
                            e.stopPropagation();
                            onOpenDiff(itemBtn.getAttribute('data-rel-path') || '', { repoRoot: repoRoot });
                        }
                    });
                    list.addEventListener('change', function (e) {
                        const t = e.target;
                        if (!t || !t.classList) return;
                        if (t.classList.contains('pending-changes-include-more')) {
                            includeUnlisted = !!t.checked;
                            updateSelectionChrome();
                            return;
                        }
                        if (!t.classList.contains('pending-changes-include')) return;
                        const p = t.getAttribute('data-rel-path') || '';
                        if (!p) return;
                        fileInclusion[p] = !!t.checked;
                        updateSelectionChrome();
                    });
                }
                const selectAll = qs('selectAll');
                const selectNone = qs('selectNone');
                if (selectAll) {
                    selectAll.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        filesSnapshot.forEach(function (p) { fileInclusion[p] = true; });
                        if (truncated) includeUnlisted = true;
                        updateSelectionChrome();
                    });
                }
                if (selectNone) {
                    selectNone.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        filesSnapshot.forEach(function (p) { fileInclusion[p] = false; });
                        if (truncated) includeUnlisted = false;
                        updateSelectionChrome();
                    });
                }
                const toggle = qs('toggle');
                if (toggle) {
                    toggle.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        pendingChangesSuppressFocusRefreshUntil = Date.now() + 1500;
                        setExpanded(!expanded);
                    });
                }
                const refreshBtn = qs('refresh');
                if (refreshBtn) {
                    refreshBtn.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        pendingChangesSuppressFocusRefreshUntil = Date.now() + 500;
                        refresh({ forceLocal: true });
                    });
                }
                const commitBtn = qs('commitBtn');
                if (commitBtn) {
                    commitBtn.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        pendingChangesSuppressFocusRefreshUntil = Date.now() + 2000;
                        const form = qs('commitForm');
                        const opening = !(form && !form.hidden);
                        if (opening && !expanded) setExpanded(true);
                        setCommitFormVisible(opening);
                    });
                }
                const commitQuick = qs('commitQuick');
                if (commitQuick) {
                    commitQuick.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        quickCommit();
                    });
                }
                const commitCancel = qs('commitCancel');
                if (commitCancel) {
                    commitCancel.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        setCommitFormVisible(false);
                    });
                }
                const commitResuggest = qs('commitResuggest');
                if (commitResuggest) {
                    commitResuggest.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        const ta = qs('commitMessage');
                        if (ta) ta.value = '';
                        suggestMessage({ force: true });
                    });
                }
                const commitEdit = qs('commitEdit');
                if (commitEdit) {
                    commitEdit.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        if (suggestInFlight) return;
                        applyEditMode(!commitEditMode);
                    });
                }
                const commitSubmit = qs('commitSubmit');
                if (commitSubmit) {
                    commitSubmit.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        submitCommit();
                    });
                }
                const commitMsg = qs('commitMessage');
                if (commitMsg) {
                    commitMsg.addEventListener('input', function () {
                        syncPreviewFromTa();
                        const editBtn = qs('commitEdit');
                        if (editBtn && commitEditMode) editBtn.hidden = false;
                    });
                    commitMsg.addEventListener('keydown', function (e) {
                        if (e.key === 'Escape') {
                            e.preventDefault();
                            if (commitEditMode && String(commitMsg.value || '').trim()) {
                                applyEditMode(false);
                                return;
                            }
                            setCommitFormVisible(false);
                            return;
                        }
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                            e.preventDefault();
                            submitCommit();
                        }
                    });
                }
                setExpanded(expanded);
            }

            setChip(label, showChip);

            return {
                el: panelEl,
                repoRoot: repoRoot,
                render: render,
                setChip: setChip,
                getFiles: function () { return filesSnapshot.slice(); },
                destroy: function () {
                    try { panelEl.remove(); } catch (_) {}
                },
            };
        }

        // The shell owns periodic scans for embedded panes, deduped by project.
        // Standalone pages retain their cadence; explicit refreshes still work.
        if (!useShellHub) pollTimer = setInterval(function () {
            if (destroyed) return;
            if (isPageBackgrounded()) return;
            const p = currentProject();
            if (!p || !p.path) return;
            scheduleRefresh(0);
        }, 12000);
        document.addEventListener('visibilitychange', function () {
            if (!isPageBackgrounded()) scheduleRefresh(200);
        });
        window.addEventListener('focus', function () {
            if (Date.now() < pendingChangesSuppressFocusRefreshUntil) return;
            scheduleRefresh(200);
        });

        scheduleRefresh(600);

        return {
            refresh: refresh,
            scheduleRefresh: scheduleRefresh,
            hide: hideAll,
            destroy: function () {
                destroyed = true;
                if (pendingChangesTimer) clearTimeout(pendingChangesTimer);
                pendingChangesTimer = null;
                if (pollTimer) clearInterval(pollTimer);
                pollTimer = null;
                try { if (_broadcast) _broadcast.close(); } catch (_) {}
                _broadcast = null;
                hideAll();
            },
            notifyChanged: notifySiblingChanged,
            onInvalidated: onInvalidated,
            applyHubPayload: applyHubPayload,
            getRepos: function () { return lastReposList.slice(); },
            getFilesForRepo: function (repoRoot) {
                const slot = panels.get(normalizePathKey(repoRoot));
                return slot ? slot.getFiles() : [];
            },
        };
    }

    global.CuttlePendingChangesPanel = { create: create };
})(typeof window !== 'undefined' ? window : globalThis);
