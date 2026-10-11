/* ================================================================
   Cuttle Chat — composer chip controller (chat_chip_config.js).
   Owner: everything a composer chip opens or shows on click —
   the centered project/agent config modal (modal DOM, one native
   <select> per chip part: Project + Branch, or Agent + Model + Effort,
   each upgraded to a searchable combobox by the shared
   js/shared/searchable_select.js), chip-row click/keyboard binding, the
   branch list/switch transport, and the segmented project chip's branch
   cache.

   Composed once per page by chat_page.js:
   `CuttleChipConfig.create(host)` returns
   `{ openProject, openAgent, close, bindRow }`. The page keeps chat
   state, palette catalogs and setters, and chip rendering: they
   arrive as explicit `host` capabilities and this module never reads
   page scope directly.

   `CuttleChipConfig.branchFor(path)` / `refreshBranch(path, onChange,
   force)` are module-scoped (the cache is shared, not per-compose).
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
            const branch = distinctBranchLabel(repos);
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
                '<select class="chip-config-select" id="' + id + '"' + (disabled ? ' disabled' : '') + '>' +
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
            overlay.className = 'auth-modal-overlay';
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
            if (fields) fields.innerHTML = '';
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
            host_.innerHTML = fieldsHtml;
            host_.querySelectorAll('.chip-config-select').forEach((sel) => {
                sel._chipConfigSig = sel.innerHTML;
            });
            enhanceSelects(host_);
            el.classList.add('active');
            if (onMount) onMount(el);
            return el;
        }

        async function loadBranches(sel, projectId) {
            if (!sel) return;
            sel.disabled = true;
            sel.innerHTML = '<option value="">Loading…</option>';
            try {
                const qs = new URLSearchParams();
                if (projectId != null && String(projectId) !== '') qs.set('project_id', String(projectId));
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

        async function switchBranch(name, sel, projectId) {
            if (!name) return;
            if (sel) sel.disabled = true;
            const current = h.getCurrentProject ? h.getCurrentProject() : null;
            const body = { action: 'switch', branch: name };
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
                    loadBranches(sel, body.project_id);
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
            const projects = h.getProjects ? (h.getProjects() || []) : [];
            const current = h.getCurrentProject ? h.getCurrentProject() : null;
            const isStarred = typeof h.isProjectStarred === 'function' ? h.isProjectStarred : () => false;
            const projOptions = projects.map((p) => ({
                value: String(p && p.id != null ? p.id : ''),
                label: ((p && isStarred(p)) ? '★ ' : '') + ((p && p.name) || ('Project ' + (p && p.id))),
            })).filter((o) => o.value !== '');
            const curId = current && current.id != null ? String(current.id) : '';
            const wtEnabled = typeof h.worktreesEnabled === 'function' && h.worktreesEnabled();
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
            fields += selectHtml('chipConfigBranch', 'Branch', [], '', true);
            if (wtEnabled) {
                fields += selectHtml('chipConfigWorktree', 'Worktree', [], '', true);
                fields += inputHtml('chipConfigNewBranch', 'New worktree branch', defBranch, 'cuttle/feature');
                fields += '<div class="chip-config-field">' +
                    '<button type="button" class="chip-config-create" id="chipConfigCreate">Create worktree</button>' +
                    '</div>';
            }

            openModal(
                'Working project',
                wtEnabled
                    ? 'Local = shared checkout. Worktree = an isolated branch for this chat.'
                    : 'Choose the project and branch for this chat.',
                fields,
                (el) => {
                    const projSel = el.querySelector('#chipConfigProject');
                    const modeSel = el.querySelector('#chipConfigMode');
                    const branchSel = el.querySelector('#chipConfigBranch');
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
                        if (local) {
                            setVisible(newBranchInput, false);
                            setVisible(createBtn, false);
                            loadBranches(branchSel, projSel.value);
                        } else {
                            loadWorktrees(wtSel, projSel.value, h.getChatCheckout ? h.getChatCheckout() : '')
                                .then(toggleCreate);
                        }
                    };

                    projSel.addEventListener('change', () => {
                        const proj = h.findProjectById ? h.findProjectById(projSel.value) : null;
                        if (proj && h.setChatProject) h.setChatProject(proj);
                        if (newBranchInput && h.defaultWorktreeBranch) {
                            newBranchInput.value = h.defaultWorktreeBranch();
                        }
                        sync();
                    });
                    if (modeSel) {
                        modeSel.addEventListener('change', () => {
                            if (currentMode() === 'local' && h.setChatCheckout) h.setChatCheckout(null);
                            sync();
                        });
                    }
                    branchSel.addEventListener('change', () => {
                        if (branchSel.value) switchBranch(branchSel.value, branchSel, projSel.value);
                    });
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
            const entries = h.getAgentEntries ? (h.getAgentEntries() || []) : [];
            const id = String(agentId || '').toLowerCase();
            const spec = h.getAgentSpec ? h.getAgentSpec(id) : null;
            const agentOptions = entries.map((e) => ({ value: e.id, label: e.label }));
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
                'Agent',
                'Choose the agent, model, and effort for this chat.',
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

        return { openProject, openAgent, close, bindRow };
    }

    const api = { create, stickyAgentEntries, agentSettingOptions, branchFor, refreshBranch };

    const ns = (root.CuttleChipConfig = root.CuttleChipConfig || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // `globalThis` directly (not `typeof window ? window`) so node importers
    // that later declare a lexical `window` don't hit TDZ.
})(globalThis);
