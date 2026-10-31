/* ================================================================
   Cuttle Chat — composer chip controller (chat_chip_config.js).
   Owner: everything a composer chip opens or shows on click —
   the centered project/agent config modal (modal DOM, one native
   <select> per chip part: Project + Branch, or Agent + Model + Effort,
   each upgraded to a searchable combobox by the shared
   js/shared/searchable_select.js), chip-row click/keyboard binding, the
   branch list/switch transport, and the segmented project chip's branch
   cache.

   Optional host labels (app pages reusing the picker, e.g. Apps →
   Agents): `agentPickerTitle`, `agentPickerSubtitle`, `clearChoiceLabel`.

   Composed once per page by chat_page.js:
   `CuttleChipConfig.create(host)` returns
   `{ openProject, openAgent, close, bindRow, openCustom }` — `openCustom`
   lends the same modal to other chip owners; `bindRow(row, { onChip })`
   lets them claim a chip click first. The page keeps chat
   state, palette catalogs and setters, and chip rendering: they
   arrive as explicit `host` capabilities and this module never reads
   page scope directly.

   `CuttleChipConfig.branchFor(path)` / `refreshBranch(path, onChange,
   force)` are module-scoped (the cache is shared, not per-compose).
   Projects whose path holds 2+ sibling repos get a Repository selector
   in the project modal (hidden otherwise); the pick is remembered per
   project path and scopes the Branch list/switch and the chip label.
   Pure decision helpers (registry → agent menu entries, palette items
   → select options) are exported and node-testable; DOM/fetch only run
   inside `create` or the branch helpers.
   ================================================================ */
(function (root) {
    'use strict';

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Sticky agent registry rows as menu entries: `[{id, prefix, label,
     * category}]`, de-duplicated by agent id. Pure; the page injects the
     * slash registry and its `composerChipAgentId` classifier.
     */
    function stickyAgentEntries(slashCommands, composerChipAgentId) {
        const seen = {};
        const out = [];
        (slashCommands || []).forEach((c) => {
            if (!c.stickySession) return;
            const id = composerChipAgentId({ prefix: c.prefix, label: c.label, category: c.category });
            if (!id || seen[id]) return;
            seen[id] = true;
            out.push({ id, prefix: c.prefix, label: c.label, category: c.category || 'command' });
        });
        return out;
    }

    /**
     * Palette items for one agent setting (`spec.models` / `spec.efforts`)
     * as `<select>` options. Strips the builder's `(current)` suffix and
     * the `Effort ` label prefix. Pure.
     */
    /**
     * Prepend a clear/none choice for defaults pickers (space/group
     * overrides). Pure; `value: ''` is the sentinel the modal treats as clear.
     */
    function withClearChoice(options, noneLabel) {
        return [{
            value: '',
            label: noneLabel || 'None — use starred default',
        }].concat(options || []);
    }

    function agentSettingOptions(spec, kind) {
        const setting = spec && spec[kind];
        if (!setting || typeof setting.build !== 'function') return [];
        let items = [];
        try { items = setting.build({ full: true }) || []; } catch (_) { items = []; }
        return items
            .filter((it) => it && it.modelId !== undefined && it.modelId !== '')
            .map((it) => ({
                value: String(it.modelId),
                label: String(it.label || it.modelId)
                    .replace(/\s*\(current\)\s*$/i, '')
                    .replace(/^effort\s+/i, ''),
                disabled: !!it.disabled,
            }));
    }

    // Project-chip branch cache (segmented chip `star | name | branch`).
    // Module-owned so the page only asks for a path's cached branch and
    // requests a refresh; it never holds the cache or the fetch itself.
    let branchCache = { path: '', branch: '', at: 0 };
    let branchInFlight = false;

    // Persist the last-known branch per project path so the chip shows it
    // immediately on a cold start (before the async refresh returns) — without
    // this the branch segment blinks away after a reload / phone reopen.
    const BRANCH_LS_PREFIX = 'cuttle.chipBranch.';
    function readPersistedBranch(path) {
        try {
            return (root.localStorage && root.localStorage.getItem(BRANCH_LS_PREFIX + path)) || '';
        } catch (_) {
            return '';
        }
    }
    function writePersistedBranch(path, branch) {
        try {
            if (!root.localStorage) return;
            if (branch) root.localStorage.setItem(BRANCH_LS_PREFIX + path, branch);
            else root.localStorage.removeItem(BRANCH_LS_PREFIX + path);
        } catch (_) {}
    }

    function branchFor(path) {
        if (!path) return '';
        if (branchCache.path === path) return branchCache.branch || '';
        return readPersistedBranch(path);
    }

    // Active repo per project path, for project paths that hold several
    // sibling repos (e.g. Dev/Blender/{A,B}). Picked in the project modal's
    // Repository selector, which only appears when 2+ repos are detected.
    const REPO_LS_PREFIX = 'cuttle.chipRepo.';
    function readActiveRepo(projectPath) {
        if (!projectPath) return '';
        try {
            return (root.localStorage && root.localStorage.getItem(REPO_LS_PREFIX + projectPath)) || '';
        } catch (_) {
            return '';
        }
    }
    function writeActiveRepo(projectPath, repoRoot) {
        if (!projectPath) return;
        try {
            if (!root.localStorage) return;
            if (repoRoot) root.localStorage.setItem(REPO_LS_PREFIX + projectPath, repoRoot);
            else root.localStorage.removeItem(REPO_LS_PREFIX + projectPath);
        } catch (_) {}
    }

    // Pure: /api/git/repos list + remembered pick → Repository selector state.
    // `show` only when 2+ repos; `selected` is the remembered repo when it is
    // still detected, else the first (the server's default work tree).
    function repoChoice(repos, persisted) {
        const list = (repos || []).filter((r) => r && r.repo_root);
        const options = list.map((r) => ({
            value: String(r.repo_root),
            label: String(r.label || r.repo_root) + (r.branch ? '  (' + r.branch + ')' : ''),
        }));
        const want = String(persisted || '');
        const selected = options.some((o) => o.value === want)
            ? want
            : (options.length ? options[0].value : '');
        return { show: options.length > 1, options, selected };
    }

    // Pure: chip branch label — the active repo's branch when the project has
    // several repos and one is picked, else the merged label.
    function activeRepoBranchLabel(repos, persisted) {
        const list = repos || [];
        if (list.length > 1 && persisted) {
            const hit = list.find((r) => r && String(r.repo_root) === String(persisted));
            if (hit) return String(hit.branch || '').trim();
        }
        return distinctBranchLabel(list);
    }

    function distinctBranchLabel(repos) {
        const branches = (repos || [])
            .map((r) => String((r && r.branch) || '').trim())
            .filter(Boolean);
        if (!branches.length) return '';
        const distinct = branches.filter((b, i) => branches.indexOf(b) === i);
        return distinct[0] + (distinct.length > 1 ? ' +' + (distinct.length - 1) : '');
    }

    async function refreshBranch(path, onChange, force) {
        if (!path) {
            branchCache = { path: '', branch: '', at: 0 };
            return;
        }
        const now = Date.now();
        if (!force && branchCache.path === path && (now - branchCache.at) < 5000) return;
        if (branchInFlight) return;
        branchInFlight = true;
        const prev = branchCache.path === path ? branchCache.branch : readPersistedBranch(path);
        try {
            const qs = new URLSearchParams();
            qs.set('path', path);
            const resp = await fetch('/api/git/repos?' + qs.toString(), {
                credentials: 'include',
                cache: 'no-store',
            });
            const data = await resp.json().catch(() => null);
            const repos = (data && data.success && Array.isArray(data.repos)) ? data.repos : [];
            const projPath = (data && data.project && data.project.path) || path;
            const branch = activeRepoBranchLabel(repos, readActiveRepo(projPath));
            branchCache = { path, branch, at: Date.now() };
            writePersistedBranch(path, branch);
            if (branch !== prev && typeof onChange === 'function') onChange();
        } catch (_) {
            if (branchCache.path === path) branchCache.at = Date.now();
        } finally {
            branchInFlight = false;
        }
    }

    function create(host) {
        const h = host || {};
        const esc = typeof h.escapeHtml === 'function' ? h.escapeHtml : escapeHtml;
        const toast = typeof h.showToast === 'function'
            ? h.showToast
            : (msg, kind) => { if (typeof root.showToast === 'function') root.showToast(msg, kind); };

        let modalEl = null;

        function optionsHtml(list, selected) {
            const opts = (list && list.length)
                ? list
                : [{ value: '', label: 'No options loaded', disabled: true }];
            return opts.map((o) =>
                '<option value="' + esc(String(o.value)) + '"' +
                (String(o.value) === String(selected == null ? '' : selected) ? ' selected' : '') +
                (o.disabled ? ' disabled' : '') +
                '>' + esc(o.label) + '</option>'
            ).join('');
        }

        // Field markup: a label + a native <select>. The shared
        // CuttleSearchableSelect (wired in enhanceSelects) turns the select
        // into a searchable, lazily-paged combobox while it stays the single
        // source of value/change for the page handlers and tests.
        function selectHtml(id, label, options, selected, disabled) {
            return '<div class="chip-config-field">' +
                '<span class="chip-config-field-label">' + esc(label) + '</span>' +
                // data-native-select: this module owns the combobox, so app-page
                // skins (page_controls.js) leave it as the composer renders it.
                '<select class="chip-config-select" data-native-select id="' + id + '"' + (disabled ? ' disabled' : '') + '>' +
                optionsHtml(options, selected) + '</select></div>';
        }

        // Free-text field (used only for a new worktree's branch name).
        function inputHtml(id, label, value, placeholder) {
            return '<div class="chip-config-field">' +
                '<span class="chip-config-field-label">' + esc(label) + '</span>' +
                '<input class="chip-config-input" type="text" id="' + id + '" autocomplete="off" ' +
                'value="' + esc(value == null ? '' : value) + '" ' +
                'placeholder="' + esc(placeholder || '') + '"></div>';
        }

        // Wire the shared searchable combobox onto each rendered select. The
        // component owns the trigger/search/lazy list; the native select stays
        // the value/change source (see js/shared/searchable_select.js).
        function enhanceSelects(rootEl) {
            if (!rootEl || !root.CuttleSearchableSelect) return;
            rootEl.querySelectorAll('select.chip-config-select').forEach((sel) => {
                const field = sel.closest ? sel.closest('.chip-config-field') : null;
                const labelEl = field ? field.querySelector('.chip-config-field-label') : null;
                root.CuttleSearchableSelect.enhance(sel, {
                    label: labelEl ? labelEl.textContent : '',
                    floating: true,
                });
            });
        }

        // Refill policy: skip identical options. The shared searchable-select
        // observes the select, so the trigger label and any open list refresh
        // on their own after a refill.
        function fillSelect(sel, options, selected) {
            if (!sel) return;
            const html = optionsHtml(options, selected);
            if (sel._chipConfigSig === html) return;
            sel._chipConfigSig = html;
            sel.innerHTML = html;
            sel.disabled = !(options && options.length);
        }

        function ensureModal() {
            if (modalEl && modalEl.isConnected) return modalEl;
            const overlay = document.createElement('div');
            overlay.className = 'auth-modal-overlay chip-config-modal-overlay';
            overlay.id = 'chipConfigModal';
            overlay.innerHTML =
                '<div class="auth-modal chip-config-modal" role="dialog" aria-modal="true">' +
                '<button type="button" class="auth-modal-close" id="chipConfigClose" aria-label="Close">×</button>' +
                '<div class="auth-modal-header">' +
                '<h2 class="auth-modal-title" id="chipConfigTitle"></h2>' +
                '<p class="auth-modal-subtitle" id="chipConfigSubtitle"></p>' +
                '</div>' +
                '<div class="chip-config-fields" id="chipConfigFields"></div>' +
                '<div class="chip-config-actions">' +
                '<button type="button" class="logout-confirm-btn logout-confirm-submit" id="chipConfigDone">Done</button>' +
                '</div>' +
                '</div>';
            document.body.appendChild(overlay);
            overlay.addEventListener('mousedown', (e) => {
                if (e.target === overlay) close();
            });
            overlay.querySelector('#chipConfigClose').addEventListener('click', close);
            overlay.querySelector('#chipConfigDone').addEventListener('click', close);
            modalEl = overlay;
            return overlay;
        }

        function close() {
            if (!modalEl) return;
            modalEl.classList.remove('active');
            const fields = modalEl.querySelector('#chipConfigFields');
            if (fields) {
                fields.querySelectorAll('select').forEach(sel => sel.__cuttleSearchable?.destroy());
                fields.innerHTML = '';
            }
            // A project/worktree change may have moved the branch — refresh the chip.
            if (typeof h.refreshBranch === 'function') {
                try { h.refreshBranch(); } catch (_) {}
            }
        }

        function openModal(title, subtitle, fieldsHtml, onMount) {
            const el = ensureModal();
            el.querySelector('#chipConfigTitle').textContent = title;
            el.querySelector('#chipConfigSubtitle').textContent = subtitle || '';
            const host_ = el.querySelector('#chipConfigFields');
            host_.querySelectorAll('select').forEach(sel => sel.__cuttleSearchable?.destroy());
            host_.innerHTML = fieldsHtml;
            host_.querySelectorAll('.chip-config-select').forEach((sel) => {
                sel._chipConfigSig = sel.innerHTML;
            });
            enhanceSelects(host_);
            el.classList.add('active');
            if (onMount) onMount(el);
            return el;
        }

        async function loadBranches(sel, projectId, repoRoot) {
            if (!sel) return;
            sel.disabled = true;
            sel.innerHTML = '<option value="">Loading…</option>';
            try {
                const qs = new URLSearchParams();
                if (projectId != null && String(projectId) !== '') qs.set('project_id', String(projectId));
                if (repoRoot) qs.set('repo_root', repoRoot);
                const resp = await fetch(
                    '/api/git/branches' + (qs.toString() ? '?' + qs.toString() : ''),
                    { credentials: 'include', cache: 'no-store' }
                );
                const data = await resp.json().catch(() => null);
                const branches = (data && data.success && Array.isArray(data.data)) ? data.data : [];
                const current = (data && data.current) || '';
                const seen = {};
                const opts = [];
                branches.forEach((b) => {
                    const name = String((b && b.name) || '').trim();
                    if (!name || seen[name]) return;
                    seen[name] = true;
                    opts.push({ value: name, label: name + (b.current ? '  ✓' : '') });
                });
                fillSelect(sel, opts, current);
            } catch (_) {
                sel.innerHTML = '<option value="">Could not load branches</option>';
                sel.disabled = true;
            }
        }

        // Fill the Repository selector; hide it unless the project path holds
        // 2+ repos. Resolves to the active repo root ('' = project default).
        async function loadRepos(sel, projectId) {
            if (!sel) return '';
            sel._projectPath = '';
            let repos = [];
            try {
                const qs = new URLSearchParams();
                if (projectId != null && String(projectId) !== '') qs.set('project_id', String(projectId));
                const resp = await fetch('/api/git/repos' + (qs.toString() ? '?' + qs.toString() : ''), {
                    credentials: 'include',
                    cache: 'no-store',
                });
                const data = await resp.json().catch(() => null);
                if (data && data.success) {
                    repos = Array.isArray(data.repos) ? data.repos : [];
                    sel._projectPath = (data.project && data.project.path) || '';
                }
            } catch (_) {
                repos = [];
            }
            const choice = repoChoice(repos, readActiveRepo(sel._projectPath));
            const field = sel.closest ? sel.closest('.chip-config-field') : null;
            if (field) field.style.display = choice.show ? '' : 'none';
            if (!choice.show) {
                sel._repoRoot = '';
                return '';
            }
            fillSelect(sel, choice.options, choice.selected);
            sel._repoRoot = choice.selected;
            return choice.selected;
        }

        async function loadWorktrees(sel, projectId, selectedUid) {
            if (!sel) return;
            sel.disabled = true;
            sel.innerHTML = '<option value="">Loading…</option>';
            let items = [];
            try {
                items = await Promise.resolve(h.getWorktrees ? h.getWorktrees(projectId) : []);
            } catch (_) {
                items = [];
            }
            const list = Array.isArray(items) ? items : [];
            sel._worktrees = list;
            const opts = list
                .filter((w) => w && w.checkout_uid && w.state !== 'missing')
                .map((w) => ({
                    value: w.checkout_uid,
                    label: (w.branch || w.checkout_uid) + (w.checkout_uid === selectedUid ? '  ✓' : ''),
                }));
            opts.push({ value: '__new__', label: '➕ Create new worktree…' });
            fillSelect(sel, opts, selectedUid || '__new__');
        }

        async function switchBranch(name, sel, projectId, repoRoot) {
            if (!name) return;
            if (sel) sel.disabled = true;
            const current = h.getCurrentProject ? h.getCurrentProject() : null;
            const body = { action: 'switch', branch: name };
            if (repoRoot) body.repo_root = repoRoot;
            if (projectId != null && String(projectId) !== '') body.project_id = projectId;
            else if (current && current.id != null) body.project_id = current.id;
            else if (current && current.path) body.path = current.path;
            try {
                const resp = await fetch('/api/git/branch', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.success) {
                    toast((data && (data.error || data.message)) || 'Branch switch failed', 'error');
                    loadBranches(sel, body.project_id, repoRoot);
                    return;
                }
                toast(data.message || ('Switched to ' + name), 'success');
                if (typeof h.refreshBranch === 'function') h.refreshBranch();
            } catch (err) {
                toast((err && err.message) || 'Branch switch failed', 'error');
                if (sel) sel.disabled = false;
            }
        }

        function openProject() {
            const defaultsMode = h.pickerMode === 'defaults';
            const projects = h.getProjects ? (h.getProjects() || []) : [];
            const current = h.getCurrentProject ? h.getCurrentProject() : null;
            const isStarred = typeof h.isProjectStarred === 'function' ? h.isProjectStarred : () => false;
            let projOptions = projects.map((p) => ({
                value: String(p && p.id != null ? p.id : ''),
                label: ((p && isStarred(p)) ? '★ ' : '') + ((p && (p.display_name || p.name)) || ('Project ' + (p && p.id))),
            })).filter((o) => o.value !== '');
            const curId = current && current.id != null ? String(current.id) : '';
            if (defaultsMode) {
                projOptions = withClearChoice(projOptions);
            }
            const wtEnabled = !defaultsMode
                && typeof h.worktreesEnabled === 'function' && h.worktreesEnabled();
            const currentCheckout = h.getChatCheckout ? (h.getChatCheckout() || '') : '';
            const mode = currentCheckout ? 'worktree' : 'local';
            const defBranch = h.defaultWorktreeBranch ? h.defaultWorktreeBranch() : '';

            let fields = selectHtml('chipConfigProject', 'Project', projOptions, curId, !projOptions.length);
            if (wtEnabled) {
                fields += selectHtml('chipConfigMode', 'Mode', [
                    { value: 'local', label: 'Local — shared checkout' },
                    { value: 'worktree', label: 'Worktree — isolated branch' },
                ], mode);
            }
            fields += selectHtml('chipConfigRepo', 'Repository', [], '', true);
            fields += selectHtml('chipConfigBranch', 'Branch', [], '', true);
            if (wtEnabled) {
                fields += selectHtml('chipConfigWorktree', 'Worktree', [], '', true);
                fields += inputHtml('chipConfigNewBranch', 'New worktree branch', defBranch, 'cuttle/feature');
                fields += '<div class="chip-config-field">' +
                    '<button type="button" class="chip-config-create" id="chipConfigCreate">Create worktree</button>' +
                    '</div>';
            }

            if (defaultsMode) {
                // Defaults pickers are project identity only: branch/worktree
                // stay per-chat (switching here would mutate the shared checkout).
                fields = selectHtml('chipConfigProject', 'Project', projOptions, curId, false);
            }

            openModal(
                'Working project',
                defaultsMode
                    ? 'Choose the project for new chats in this tab or group.'
                    : (wtEnabled
                        ? 'Local = shared checkout. Worktree = an isolated branch for this chat.'
                        : 'Choose the project and branch for this chat.'),
                fields,
                (el) => {
                    const projSel = el.querySelector('#chipConfigProject');
                    const modeSel = el.querySelector('#chipConfigMode');
                    const repoSel = el.querySelector('#chipConfigRepo');
                    const branchSel = el.querySelector('#chipConfigBranch');
                    const repoRoot = () => (repoSel && repoSel._repoRoot) || '';
                    const wtSel = el.querySelector('#chipConfigWorktree');
                    const newBranchInput = el.querySelector('#chipConfigNewBranch');
                    const createBtn = el.querySelector('#chipConfigCreate');
                    const currentMode = () => (wtEnabled && modeSel ? modeSel.value : 'local');
                    const setVisible = (node, visible) => {
                        const field = node && node.closest ? node.closest('.chip-config-field') : null;
                        if (field) field.style.display = visible ? '' : 'none';
                    };
                    const showCreate = () => !!(wtSel && wtSel.value === '__new__');
                    const toggleCreate = () => {
                        setVisible(newBranchInput, showCreate());
                        setVisible(createBtn, showCreate());
                    };
                    const sync = () => {
                        const local = currentMode() !== 'worktree';
                        setVisible(branchSel, local);
                        setVisible(wtSel, !local);
                        setVisible(repoSel, false); // loadRepos reveals it for 2+ repos
                        if (local) {
                            setVisible(newBranchInput, false);
                            setVisible(createBtn, false);
                            loadRepos(repoSel, projSel.value)
                                .then((root_) => loadBranches(branchSel, projSel.value, root_));
                        } else {
                            loadWorktrees(wtSel, projSel.value, h.getChatCheckout ? h.getChatCheckout() : '')
                                .then(toggleCreate);
                        }
                    };

                    projSel.addEventListener('change', () => {
                        if (defaultsMode && !projSel.value) {
                            if (h.setChatProject) h.setChatProject(null);
                            return;
                        }
                        const proj = h.findProjectById ? h.findProjectById(projSel.value) : null;
                        if (proj && h.setChatProject) h.setChatProject(proj);
                        if (newBranchInput && h.defaultWorktreeBranch) {
                            newBranchInput.value = h.defaultWorktreeBranch();
                        }
                        if (!defaultsMode) sync();
                    });
                    if (defaultsMode) return;
                    if (modeSel) {
                        modeSel.addEventListener('change', () => {
                            if (currentMode() === 'local' && h.setChatCheckout) h.setChatCheckout(null);
                            sync();
                        });
                    }
                    if (repoSel) {
                        repoSel.addEventListener('change', () => {
                            if (!repoSel.value) return;
                            repoSel._repoRoot = repoSel.value;
                            writeActiveRepo(repoSel._projectPath, repoSel.value);
                            loadBranches(branchSel, projSel.value, repoSel.value);
                            if (typeof h.refreshBranch === 'function') h.refreshBranch();
                        });
                    }
                    if (branchSel) {
                        branchSel.addEventListener('change', () => {
                            if (branchSel.value) switchBranch(branchSel.value, branchSel, projSel.value, repoRoot());
                        });
                    }
                    if (wtSel) {
                        wtSel.addEventListener('change', () => {
                            if (wtSel.value === '__new__') {
                                toggleCreate();
                                return;
                            }
                            const picked = (wtSel._worktrees || []).find((w) => w.checkout_uid === wtSel.value);
                            if (picked && h.setChatCheckout) h.setChatCheckout(picked);
                            toggleCreate();
                        });
                    }
                    if (createBtn) {
                        createBtn.addEventListener('click', () => {
                            const name = ((newBranchInput && newBranchInput.value) || '').trim()
                                || (h.defaultWorktreeBranch ? h.defaultWorktreeBranch() : '');
                            if (!name) return;
                            createBtn.disabled = true;
                            Promise.resolve(h.createWorktree ? h.createWorktree(projSel.value, name) : null)
                                .then((created) => {
                                    createBtn.disabled = false;
                                    if (created && created.checkout_uid) {
                                        if (h.setChatCheckout) h.setChatCheckout(created);
                                        toast('Worktree ready: ' + (created.branch || name), 'success');
                                    } else {
                                        toast((created && created.error) || 'Worktree creation failed', 'error');
                                    }
                                    loadWorktrees(wtSel, projSel.value, created && created.checkout_uid);
                                })
                                .catch((err) => {
                                    createBtn.disabled = false;
                                    toast((err && err.message) || 'Worktree creation failed', 'error');
                                });
                        });
                    }
                    sync();
                }
            );
        }

        function openAgent(agentId) {
            const defaultsMode = h.pickerMode === 'defaults';
            const entries = h.getAgentEntries ? (h.getAgentEntries() || []) : [];
            const id = String(agentId || '').toLowerCase();
            const spec = id && h.getAgentSpec ? h.getAgentSpec(id) : null;
            let agentOptions = entries.map((e) => ({ value: e.id, label: e.label }));
            if (defaultsMode) agentOptions = withClearChoice(agentOptions, h.clearChoiceLabel);
            let fields = selectHtml('chipConfigAgent', 'Agent', agentOptions, id, !agentOptions.length);
            let modelOpts = [];
            let effortOpts = [];
            if (spec && spec.models) {
                try { spec.models.load(); } catch (_) {}
                modelOpts = agentSettingOptions(spec, 'models');
                fields += selectHtml('chipConfigModel', 'Model', modelOpts, spec.models.current());
            }
            if (spec && spec.efforts) {
                try { spec.efforts.load(); } catch (_) {}
                effortOpts = agentSettingOptions(spec, 'efforts');
                fields += selectHtml('chipConfigEffort', 'Effort', effortOpts, spec.efforts.current());
            }
            openModal(
                h.agentPickerTitle || 'Agent',
                h.agentPickerSubtitle || (defaultsMode
                    ? 'Choose the agent, model, and effort for new chats in this tab or group.'
                    : 'Choose the agent, model, and effort for this chat.'),
                fields,
                (el) => {
                    const agentSel = el.querySelector('#chipConfigAgent');
                    const modelSel = el.querySelector('#chipConfigModel');
                    const effortSel = el.querySelector('#chipConfigEffort');
                    const after = (setting) => {
                        if (setting && typeof setting.repaint === 'function') {
                            try { setting.repaint(); } catch (_) {}
                        }
                        if (typeof h.rerenderChips === 'function') h.rerenderChips();
                    };
                    agentSel.addEventListener('change', () => {
                        if (defaultsMode && !agentSel.value) {
                            if (h.applyAgent) h.applyAgent(null);
                            openAgent('');
                            return;
                        }
                        const entry = entries.find((e) => e.id === agentSel.value);
                        if (entry && h.applyAgent) h.applyAgent(entry);
                        openAgent(agentSel.value);
                    });
                    if (modelSel && spec && spec.models) {
                        modelSel.addEventListener('change', () => {
                            if (!modelSel.value) return;
                            spec.models.set(modelSel.value);
                            after(spec.models);
                        });
                    }
                    if (effortSel && spec && spec.efforts) {
                        effortSel.addEventListener('change', () => {
                            if (!effortSel.value) return;
                            spec.efforts.set(effortSel.value);
                            after(spec.efforts);
                        });
                    }
                    // Only refill a select that opened empty (catalog still
                    // loading), and cancel on first user contact so a late
                    // catalog can never dismiss an open native picker.
                    if (!modelOpts.length || !effortOpts.length) {
                        const timers = [500, 1400].map((ms) => setTimeout(() => {
                            if (!el.classList.contains('active')) return;
                            if (agentSel.value !== String(agentId || '').toLowerCase()) return;
                            if (modelSel && spec && spec.models) {
                                fillSelect(modelSel, agentSettingOptions(spec, 'models'), spec.models.current());
                            }
                            if (effortSel && spec && spec.efforts) {
                                fillSelect(effortSel, agentSettingOptions(spec, 'efforts'), spec.efforts.current());
                            }
                        }, ms));
                        const cancel = () => timers.forEach((t) => clearTimeout(t));
                        const cancelTargets = [agentSel, modelSel, effortSel];
                        [agentSel, modelSel, effortSel].forEach((sel) => {
                            const combo = sel && sel.closest ? sel.closest('.cuttle-combo') : null;
                            if (!combo) return;
                            cancelTargets.push(
                                combo.querySelector('.cuttle-combo-trigger'),
                                combo.querySelector('.cuttle-combo-search'));
                        });
                        cancelTargets.forEach((node) => {
                            if (!node) return;
                            node.addEventListener('pointerdown', cancel, { once: true });
                            node.addEventListener('focus', cancel, { once: true });
                        });
                    }
                }
            );
        }

        // Wire a rendered chips row: the project chip opens the project
        // modal; a sticky agent chip opens the agent modal. The × remove
        // button is left to the page (never opens the modal).
        function bindRow(row, options) {
            if (!row) return;
            const opts = options || {};
            const chips = opts.chips || [];
            const classify = typeof opts.composerChipAgentId === 'function'
                ? opts.composerChipAgentId
                : null;
            row.querySelectorAll('.slash-command-chip').forEach((el) => {
                const activate = () => {
                    if (el.getAttribute('data-project-chip') === '1') {
                        openProject();
                        return;
                    }
                    const idx = parseInt(el.getAttribute('data-chip-index'), 10);
                    const chip = Number.isFinite(idx) ? chips[idx] : null;
                    // Other chip owners (e.g. the /orchestrate chip) claim their own clicks.
                    if (chip && typeof opts.onChip === 'function' && opts.onChip(chip)) return;
                    const agentId = chip && classify ? classify(chip) : '';
                    if (agentId) openAgent(agentId);
                };
                el.addEventListener('click', (e) => {
                    if (e.target && e.target.closest && e.target.closest('.slash-chip-remove')) return;
                    e.preventDefault();
                    e.stopPropagation();
                    activate();
                });
                el.addEventListener('keydown', (e) => {
                    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
                    e.preventDefault();
                    activate();
                });
            });
        }

        if (typeof document !== 'undefined') {
            // Escape closes the modal. When a searchable-select panel is open,
            // the shared component (js/shared/searchable_select.js) closes it
            // first and stops the event, so the modal stays up.
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') close();
            });
        }

        return { openProject, openAgent, close, bindRow, openCustom: openModal };
    }

    const api = {
        create, stickyAgentEntries, agentSettingOptions, withClearChoice, branchFor, refreshBranch,
        repoChoice, activeRepoBranchLabel,
    };

    const ns = (root.CuttleChipConfig = root.CuttleChipConfig || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // `globalThis` directly (not `typeof window ? window`) so node importers
    // that later declare a lexical `window` don't hit TDZ.
})(globalThis);
