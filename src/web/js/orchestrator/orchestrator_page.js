/**
 * Orchestrator App controller — transport + events over CuttleOrchestrator
 * (pure). Playbooks are owned by api.orchestrator; profiles by the Agents App.
 * Selects are plain <select>s upgraded by the shared page_controls combobox.
 */
(function () {
    'use strict';

    const M = window.CuttleOrchestrator;
    const $ = (id) => document.getElementById(id);
    const notice = $('orchNotice');
    const content = $('orchContent');
    const sup = $('orchSupervisor');
    const LIMIT_HELP = {
        solo_attempts: 'Failed attempts on one problem in its own turn before it must hand the problem to a worker.',
        solo_minutes: 'Minutes on one problem in its own turn before it must delegate, even without a clear failure.',
        child_retries: 'Retries on the same tier, with a sharper brief, before a task moves down the ladder.',
        setup_retries: 'Spawns that fail to start (bad pin, quota, missing CLI) before it reports instead of looping.',
    };
    let state = { playbooks: [], profiles: [], catalog: [], bindings: [], draft: null, savedId: '', saved: '', tab: 'chart', preview: null };
    let previewTimer = null;

    function toast(message, kind) {
        if (typeof window.showToast === 'function') window.showToast(message, kind || 'info');
    }

    async function call(path, method, body) {
        const resp = await fetch(path, {
            method: method || 'GET', credentials: 'include', cache: 'no-store',
            headers: body ? { 'Content-Type': 'application/json' } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        let data = {};
        try { data = await resp.json(); } catch (_) {}
        if (!resp.ok || data.success === false) {
            const err = new Error(data.error || `Request failed (${resp.status})`);
            err.disabled = !!data.disabled;
            throw err;
        }
        return data;
    }

    function profileById(id) { return state.profiles.find((p) => p.id === id) || null; }

    function snapshot(d) {
        const body = M.clone(d);
        delete body.seed; delete body.missing_profiles; delete body.updated_at;
        return JSON.stringify(body);
    }

    function isDirty() { return !!state.draft && snapshot(state.draft) !== state.saved; }

    async function copy(text, label) {
        try { await navigator.clipboard.writeText(text || ''); toast(`${label} copied`, 'success'); }
        catch (_) { toast('Copy failed — select the text instead', 'error'); }
    }

    function destroyCombos(rootEl) {
        rootEl.querySelectorAll('select').forEach((sel) => { try { sel.__cuttleSearchable?.destroy(); } catch (_) {} });
    }

    // ── Sidebar ─────────────────────────────────────────────────────────

    function ladderDots(pb) {
        return (pb.tiers || []).map((t) => `<span class="orch-ladder-dot role-${M.esc(t.role)}" title="${M.esc(t.label)}"></span>`).join('');
    }

    function renderSidebar() {
        $('orchList').innerHTML = state.playbooks.map((pb) => {
            const missing = (pb.missing_profiles || []).length;
            return `<button type="button" class="orch-card${state.savedId === pb.id ? ' is-active' : ''}" data-open="${M.esc(pb.id)}">
                <span class="orch-card-icon" aria-hidden="true"><span class="orch-ladder-dots">${ladderDots(pb)}</span></span>
                <span class="orch-card-main">
                    <span class="orch-card-title">${M.esc(pb.name)}</span>
                    <span class="orch-card-sub">${M.esc(pb.id)} · ${(pb.tiers || []).length} tiers${pb.seed ? ' · not saved' : ''}</span>
                </span>
                ${missing ? `<span class="orch-dot-count is-bad" title="${missing} missing profile(s)">!</span>` : ''}
            </button>`;
        }).join('') || '<p class="orch-empty-line">No playbooks.</p>';
        $('orchCount').textContent = String(state.playbooks.filter((p) => !p.seed).length || '');
        $('orchBindCount').textContent = state.bindings.length ? String(state.bindings.length) : '';
        $('orchBindings').innerHTML = state.bindings.map((b) => `<div class="orch-card orch-card--static">
            <span class="orch-card-icon orch-card-icon--chat" aria-hidden="true">💬</span>
            <span class="orch-card-main" title="${M.esc(b.goal || '')}">
                <span class="orch-card-title">${M.esc(b.title || 'Untitled chat')}</span>
                <span class="orch-card-sub">${M.esc([b.session, b.project, b.playbook_id].filter(Boolean).join(' · '))}</span>
            </span>
            <span class="orch-card-actions">
                <button type="button" class="orch-icon-btn" data-target="${M.esc(b.session)}" title="Supervise command" aria-label="Supervise ${M.esc(b.session)}">⌖</button>
                <button type="button" class="orch-icon-btn is-danger" data-unbind="${M.esc(b.session)}" title="Unbind" aria-label="Unbind ${M.esc(b.session)}">✕</button>
            </span></div>`).join('')
            || '<p class="orch-empty-line">None yet. Start one with <code>/orchestrate &lt;goal&gt;</code> in any chat.</p>';
    }

    // ── Org chart ───────────────────────────────────────────────────────

    function profileChip(pid, index, tierIndex) {
        const p = profileById(pid);
        return `<li class="orch-member${p ? '' : ' is-missing'}${index === 0 ? ' is-default' : ''}">
            <span class="orch-av orch-av--sm">${M.avatarHtml(p ? p.avatar : '')}</span>
            <span class="orch-member-main">
                <span class="orch-member-name">${M.esc(p ? p.name : pid)}${index === 0 ? '<span class="orch-tag is-accent">Default</span>' : ''}</span>
                <span class="orch-member-pin">${p ? `<span aria-hidden="true">${M.esc(M.agentIcon(state.catalog, p.agent))}</span> ${M.esc(M.pinLabel(state.catalog, p))}` : 'Missing profile — create it in Agents'}</span>
            </span>
            <span class="orch-member-actions">
                ${index > 0 ? `<button type="button" class="orch-icon-btn" data-tier="${tierIndex}" data-up-profile="${M.esc(pid)}" title="Make default" aria-label="Make ${M.esc(pid)} the default">★</button>` : ''}
                <button type="button" class="orch-icon-btn is-danger" data-tier="${tierIndex}" data-drop-profile="${M.esc(pid)}" title="Remove" aria-label="Remove ${M.esc(pid)}">✕</button>
            </span>
        </li>`;
    }

    function tierHtml(tier, index, count) {
        const meta = M.ROLE_META[tier.role] || M.ROLE_META.worker;
        const available = state.profiles.filter((p) => tier.profiles.indexOf(p.id) < 0);
        const roles = M.ROLES.map((r) => `<option value="${r}"${tier.role === r ? ' selected' : ''}>${M.ROLE_LABELS[r]}</option>`).join('');
        return `<li class="orch-tier role-${M.esc(tier.role)}" data-index="${index}">
            <div class="orch-tier-rail" aria-hidden="true"><span class="orch-tier-step">${index + 1}</span>${index < count - 1 ? '<span class="orch-tier-line"></span>' : ''}</div>
            <div class="orch-tier-card">
                <div class="orch-tier-head">
                    <span class="orch-role-badge" title="${M.esc(meta.hint)}"><span aria-hidden="true">${meta.icon}</span></span>
                    <input class="orch-tier-name" data-field="label" value="${M.esc(tier.label)}" aria-label="Tier name" maxlength="60">
                    <select data-field="role" aria-label="Tier role" class="orch-role-select">${roles}</select>
                    <span class="orch-tier-actions">
                        <button type="button" class="orch-icon-btn" data-move="-1" ${index === 0 ? 'disabled' : ''} title="Move up" aria-label="Move tier up">↑</button>
                        <button type="button" class="orch-icon-btn" data-move="1" ${index === count - 1 ? 'disabled' : ''} title="Move down" aria-label="Move tier down">↓</button>
                        <button type="button" class="orch-icon-btn is-danger" data-remove-tier ${count === 1 ? 'disabled' : ''} title="Remove tier" aria-label="Remove tier">✕</button>
                    </span>
                </div>
                <p class="orch-tier-hint">${M.esc(meta.hint)}</p>
                <ul class="orch-members">${tier.profiles.map((pid, i) => profileChip(pid, i, index)).join('')
                    || '<li class="orch-member is-empty">No profiles yet — add one below.</li>'}</ul>
                <div class="orch-tier-add">
                    <select data-add-profile aria-label="Add a profile to this tier"${available.length ? '' : ' disabled'}>
                        <option value="">${available.length ? 'Add a profile…' : 'All profiles added'}</option>
                        ${available.map((p) => `<option value="${M.esc(p.id)}">${M.esc(p.name)} — ${M.esc(M.pinLabel(state.catalog, p))}</option>`).join('')}
                    </select>
                </div>
                <textarea class="orch-tier-use" data-field="use_for" rows="2" maxlength="600" placeholder="Use this tier for…" aria-label="Use this tier for">${M.esc(tier.use_for)}</textarea>
            </div>
        </li>`;
    }

    function renderTiers() {
        const host = $('orchTiers');
        destroyCombos(host);
        const tiers = state.draft.tiers;
        host.innerHTML = tiers.map((t, i) => tierHtml(t, i, tiers.length)).join('');
    }

    // ── Limits / supervisor ─────────────────────────────────────────────

    function renderLimits() {
        $('orchLimits').innerHTML = M.LIMIT_FIELDS.map(([key, label, low, high]) => `<div class="orch-limit">
            <div class="orch-limit-text"><strong>${M.esc(label)}</strong><span>${M.esc(LIMIT_HELP[key] || '')}</span></div>
            <div class="orch-stepper" data-key="${key}">
                <button type="button" class="orch-icon-btn" data-step="-1" aria-label="Decrease">−</button>
                <input type="number" min="${low}" max="${high}" value="${M.esc(state.draft.limits[key])}" aria-label="${M.esc(label)}">
                <button type="button" class="orch-icon-btn" data-step="1" aria-label="Increase">＋</button>
            </div></div>`).join('');
    }

    function renderForbid() {
        const host = $('orchForbid');
        const input = host.querySelector('input');
        host.querySelectorAll('.orch-tag-chip').forEach((n) => n.remove());
        (state.draft.supervisor.forbid_branches || []).forEach((b) => {
            const chip = document.createElement('span');
            chip.className = 'orch-tag-chip';
            chip.innerHTML = `${M.esc(b)}<button type="button" data-unforbid="${M.esc(b)}" aria-label="Remove ${M.esc(b)}">✕</button>`;
            host.insertBefore(chip, input);
        });
    }

    function renderSupervisor() {
        const s = state.draft.supervisor;
        destroyCombos(sup);
        sup.agent.innerHTML = ['<option value="">The supervisor chat\'s own agent (recommended)</option>']
            .concat(state.catalog.map((r) => `<option value="${M.esc(r.id)}">${M.esc(r.label || r.id)}</option>`)).join('');
        sup.every_min.value = Math.round((s.every_sec || 900) / 60);
        sup.agent.value = s.agent || '';
        sup.require_branch.value = s.require_branch || '';
        sup.require_path.value = s.require_path || '';
        sup.instructions.value = s.instructions || '';
        renderForbid();
    }

    // ── Editor shell ────────────────────────────────────────────────────

    function startCommand(d) {
        return `/orchestrate${d.id === 'default' ? '' : ' @' + d.id} <goal>`;
    }

    function renderSavebar() {
        const bar = $('orchSavebar');
        $('orchDelete').hidden = !state.draft || !state.savedId || !!state.draft.seed;
        if (!state.draft) { bar.hidden = true; return; }
        const problems = M.playbookProblems(state.draft, state.profiles.map((p) => p.id));
        const dirty = isDirty() || !state.savedId || state.draft.seed;
        // Only on screen when there is something to save or fix.
        bar.hidden = !dirty && !problems.length;
        const stateEl = $('orchSaveState');
        if (problems.length) {
            stateEl.className = 'orch-savebar-state is-bad';
            stateEl.innerHTML = `<strong>${problems.length} to fix</strong> · ${M.esc(problems[0])}`;
            stateEl.title = problems.join('\n');
        } else {
            stateEl.className = 'orch-savebar-state' + (dirty ? ' is-dirty' : ' is-ok');
            stateEl.textContent = state.draft.seed ? 'Built-in default — save to customize it' : (dirty ? 'Unsaved changes' : 'All changes saved');
            stateEl.title = '';
        }
        $('orchSave').disabled = !!problems.length || !dirty;
        $('orchDiscard').hidden = !isDirty();
        bar.classList.toggle('is-dirty', dirty && !problems.length);
    }

    function changed() {
        renderSavebar();
        const d = state.draft;
        $('orchStartCmd').querySelector('code').textContent = startCommand(d);
    }

    function setTab(tab) {
        state.tab = tab;
        document.querySelectorAll('.orch-tabs [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
        document.querySelectorAll('.orch-tab').forEach((p) => { p.hidden = p.dataset.panel !== tab; });
        if (tab === 'preview') loadPreview();
    }

    function renderEditor() {
        const d = state.draft;
        $('orchName').value = d.name;
        $('orchDescription').value = d.description || '';
        $('orchId').value = d.id;
        $('orchId').readOnly = !!state.savedId && !d.seed;
        $('orchSeed').hidden = !d.seed;
        $('orchNotes').value = d.notes || '';
        renderTiers();
        renderLimits();
        renderSupervisor();
        renderSidebar();
        changed();
        if (state.tab === 'preview') loadPreview();
    }

    function loadPreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(async () => {
            if (!state.savedId) {
                $('orchBrief').textContent = 'Save the playbook to preview its brief.';
                $('orchSupervise').textContent = '';
                return;
            }
            const target = $('orchTarget').value.trim();
            try {
                const q = target ? '?session=' + encodeURIComponent(target) : '';
                state.preview = await call(`/api/orchestrator/playbooks/${encodeURIComponent(state.savedId)}/preview${q}`);
                $('orchBrief').textContent = state.preview.brief;
                $('orchSupervise').textContent = state.preview.supervisor_command;
            } catch (err) {
                $('orchSupervise').textContent = err.message;
            }
        }, 200);
    }

    function openPlaybook(id, force) {
        if (!force && isDirty() && !window.confirm('Discard unsaved changes?')) return;
        const pb = state.playbooks.find((p) => p.id === id);
        if (!pb) return;
        state.draft = M.clone(pb);
        state.savedId = pb.id;
        state.saved = pb.seed ? '' : snapshot(pb);
        renderEditor();
    }

    async function load(selectId) {
        try {
            const [pbs, profiles, catalog, bindings] = await Promise.all([
                call('/api/orchestrator/playbooks'),
                call('/api/agent-profiles'),
                call('/api/agents').catch(() => ({ agents: [] })),
                call('/api/orchestrator/bindings').catch(() => ({ bindings: [] })),
            ]);
            state.playbooks = pbs.playbooks || [];
            state.profiles = profiles.profiles || [];
            state.catalog = (catalog.agents || []).filter((a) => a && a.id);
            state.bindings = bindings.bindings || [];
            notice.hidden = true;
            content.hidden = false;
            const want = selectId || state.savedId || (state.playbooks[0] && state.playbooks[0].id);
            if (want) openPlaybook(want, true); else renderSidebar();
        } catch (err) {
            notice.hidden = false;
            notice.classList.add('is-error');
            notice.innerHTML = err.disabled
                ? 'Turn on <strong>Orchestrator</strong> in <a href="/settings_page.html?tab=experimental">Settings → Experimental</a>.'
                : M.esc(err.message || 'Orchestrator could not load.');
            content.hidden = true;
            $('orchSavebar').hidden = true;
        }
    }

    // ── Events ──────────────────────────────────────────────────────────

    const tiersEl = $('orchTiers');
    tiersEl.addEventListener('input', (e) => {
        const box = e.target.closest('.orch-tier');
        const field = e.target.dataset.field;
        if (!box || !field || field === 'role') return;
        state.draft.tiers[Number(box.dataset.index)][field] = e.target.value;
        changed();
    });
    tiersEl.addEventListener('change', (e) => {
        const box = e.target.closest('.orch-tier');
        if (!box) return;
        const i = Number(box.dataset.index);
        if (e.target.dataset.field === 'role') {
            state.draft.tiers[i].role = e.target.value;
            renderTiers();
            changed();
        } else if (e.target.hasAttribute('data-add-profile') && e.target.value) {
            state.draft.tiers[i] = M.addProfileToTier(state.draft.tiers[i], e.target.value);
            renderTiers();
            changed();
        }
    });
    tiersEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        const box = e.target.closest('.orch-tier');
        if (!btn || !box || btn.disabled) return;
        const i = Number(box.dataset.index);
        const tiers = state.draft.tiers;
        if (btn.dataset.move) state.draft.tiers = M.moveItem(tiers, i, Number(btn.dataset.move));
        else if (btn.hasAttribute('data-remove-tier')) {
            if (tiers[i].profiles.length && !window.confirm(`Remove the ${tiers[i].label} tier?`)) return;
            tiers.splice(i, 1);
        } else if (btn.dataset.dropProfile) tiers[i] = M.removeProfileFromTier(tiers[i], btn.dataset.dropProfile);
        else if (btn.dataset.upProfile) {
            const list = tiers[i].profiles.filter((p) => p !== btn.dataset.upProfile);
            tiers[i].profiles = [btn.dataset.upProfile].concat(list);
        } else return;
        renderTiers();
        changed();
    });
    $('orchAddTier').addEventListener('click', () => {
        const tier = M.newTier(state.draft.tiers);
        tier.role = state.draft.tiers.some((t) => t.role === 'expert') ? 'escalation' : 'expert';
        tier.label = M.ROLE_LABELS[tier.role];
        state.draft.tiers.push(tier);
        renderTiers();
        changed();
        tiersEl.lastElementChild?.querySelector('.orch-tier-name')?.focus();
    });

    $('orchName').addEventListener('input', (e) => { state.draft.name = e.target.value; changed(); });
    $('orchDescription').addEventListener('input', (e) => { state.draft.description = e.target.value; changed(); });
    $('orchId').addEventListener('input', (e) => { state.draft.id = M.slug(e.target.value); changed(); });
    $('orchNotes').addEventListener('input', (e) => { state.draft.notes = e.target.value; changed(); });

    $('orchLimits').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-step]');
        if (!btn) return;
        const wrap = btn.closest('.orch-stepper');
        const input = wrap.querySelector('input');
        const v = Math.min(Number(input.max), Math.max(Number(input.min), (Number(input.value) || 0) + Number(btn.dataset.step)));
        input.value = v;
        state.draft.limits[wrap.dataset.key] = v;
        changed();
    });
    $('orchLimits').addEventListener('input', (e) => {
        const wrap = e.target.closest('.orch-stepper');
        if (!wrap) return;
        state.draft.limits[wrap.dataset.key] = Number(e.target.value);
        changed();
    });

    function readSupervisor() {
        const s = state.draft.supervisor;
        s.every_sec = Math.round(Number(sup.every_min.value) * 60);
        s.agent = sup.agent.value;
        s.require_branch = sup.require_branch.value.trim();
        s.require_path = sup.require_path.value.trim();
        s.instructions = sup.instructions.value.trim();
        changed();
    }
    sup.addEventListener('input', readSupervisor);
    sup.addEventListener('change', readSupervisor);
    sup.addEventListener('submit', (e) => e.preventDefault());
    const forbidInput = $('orchForbid').querySelector('input');
    function addForbid() {
        const names = M.parseBranches(forbidInput.value);
        if (!names.length) return;
        const list = state.draft.supervisor.forbid_branches;
        names.forEach((b) => { if (list.indexOf(b) < 0) list.push(b); });
        forbidInput.value = '';
        renderForbid();
        changed();
    }
    forbidInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addForbid(); }
        else if (e.key === 'Backspace' && !forbidInput.value && state.draft.supervisor.forbid_branches.length) {
            state.draft.supervisor.forbid_branches.pop();
            renderForbid();
            changed();
        }
    });
    forbidInput.addEventListener('blur', addForbid);
    $('orchForbid').addEventListener('click', (e) => {
        const b = e.target.closest('[data-unforbid]');
        if (b) {
            state.draft.supervisor.forbid_branches = state.draft.supervisor.forbid_branches.filter((x) => x !== b.dataset.unforbid);
            renderForbid();
            changed();
        } else forbidInput.focus();
    });

    document.querySelector('.orch-tabs').addEventListener('click', (e) => {
        const b = e.target.closest('[data-tab]');
        if (b) setTab(b.dataset.tab);
    });
    $('orchTarget').addEventListener('input', loadPreview);
    $('orchStartCmd').addEventListener('click', () => copy(startCommand(state.draft), 'Command'));
    $('orchCopyBrief').addEventListener('click', () => copy(state.preview && state.preview.brief, 'Brief'));
    $('orchCopySupervise').addEventListener('click', () => copy(state.preview && state.preview.supervisor_command, 'Command'));

    $('orchNew').addEventListener('click', () => {
        if (isDirty() && !window.confirm('Discard unsaved changes?')) return;
        let n = 1;
        while (state.playbooks.some((p) => p.id === `playbook-${n}`)) n += 1;
        state.draft = M.newPlaybook(`playbook-${n}`);
        state.savedId = '';
        state.saved = '';
        setTab('chart');
        renderEditor();
        $('orchName').select();
    });

    $('orchSave').addEventListener('click', async () => {
        readSupervisor();
        const body = JSON.parse(snapshot(state.draft));
        try {
            const data = await call('/api/orchestrator/playbooks/' + encodeURIComponent(body.id), 'PUT', body);
            toast(`Saved ${data.playbook.name}`, 'success');
            state.savedId = data.playbook.id;
            await load(data.playbook.id);
        } catch (err) {
            toast(err.message, 'error');
            const el = $('orchSaveState');
            el.className = 'orch-savebar-state is-bad';
            el.textContent = err.message;
        }
    });
    $('orchDiscard').addEventListener('click', () => { if (state.savedId) openPlaybook(state.savedId, true); });
    $('orchDelete').addEventListener('click', async () => {
        if (!state.savedId || !window.confirm(`Delete playbook “${state.draft.name}”? Chats bound to it lose their brief until rebound.`)) return;
        try {
            await call('/api/orchestrator/playbooks/' + encodeURIComponent(state.savedId), 'DELETE');
            toast('Playbook deleted', 'success');
            state.savedId = '';
            state.saved = '';
            state.draft = null;
            load();
        } catch (err) { toast(err.message, 'error'); }
    });

    content.querySelector('.orch-sidebar').addEventListener('click', async (e) => {
        const open = e.target.closest('[data-open]');
        if (open) { openPlaybook(open.dataset.open); return; }
        const btn = e.target.closest('button');
        if (!btn) return;
        if (btn.dataset.target) {
            const b = state.bindings.find((x) => x.session === btn.dataset.target);
            if (b && b.playbook_id !== state.savedId) openPlaybook(b.playbook_id);
            $('orchTarget').value = btn.dataset.target;
            setTab('preview');
        } else if (btn.dataset.unbind) {
            if (!window.confirm(`Unbind ${btn.dataset.unbind}? Its next fresh context will not carry the brief.`)) return;
            try {
                await call('/api/orchestrator/bindings/' + encodeURIComponent(btn.dataset.unbind), 'DELETE');
                load();
            } catch (err) { toast(err.message, 'error'); }
        }
    });

    window.addEventListener('beforeunload', (e) => { if (isDirty()) { e.preventDefault(); e.returnValue = ''; } });
    $('orchRefresh').addEventListener('click', () => { if (!isDirty() || window.confirm('Discard unsaved changes?')) load(); });
    load();
})();
