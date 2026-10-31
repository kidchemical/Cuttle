/**
 * Orchestrator App controller — transport + events over CuttleOrchestrator
 * (pure). Playbooks are owned by api.orchestrator; profiles by the Agents App.
 * Temporary agents pin harness · model · effort inline through the composer's
 * chip picker (CuttleAgentPinPicker). Selects are plain <select>s upgraded by
 * the shared page_controls combobox.
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
    let state = { playbooks: [], profiles: [], catalog: [], bindings: [], defaultId: 'default', draft: null, savedId: '', saved: '', tab: 'chart', preview: null };
    let previewTimer = null;
    // Temporary-agent pin edits land on this tier member (index -1 = add on first pick).
    let pinTarget = null;
    const pinPicker = window.CuttleAgentPinPicker ? window.CuttleAgentPinPicker.create({
        getCatalog: () => state.catalog,
        title: 'Temporary agent',
        subtitle: 'Harness, model and effort for this tier only. No profile to set up.',
        onChange: onTemporaryPin,
    }) : null;

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
        delete body.seed; delete body.missing_profiles; delete body.updated_at; delete body.is_default;
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
                    <span class="orch-card-title">${M.esc(pb.name)}${pb.id === state.defaultId ? '<span class="orch-default-star" title="Default for /orchestrate">★</span>' : ''}</span>
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

    function memberHtml(m, index, tierIndex, isDefault) {
        const temp = M.isTemporary(m);
        const p = temp ? null : profileById(m.profile);
        const on = M.isOn(m);
        const missing = !temp && !p;
        const label = temp ? m.name : (p ? p.name : m.profile);
        const pin = temp ? m : p;
        const pinText = pin ? `<span aria-hidden="true">${M.esc(M.agentIcon(state.catalog, pin.agent))}</span> ${M.esc(M.pinLabel(state.catalog, pin))}` : 'Missing profile — create it in Agents';
        const cls = ['orch-member', missing ? 'is-missing' : '', isDefault ? 'is-default' : '', on ? '' : 'is-off', temp ? 'is-temp' : ''].filter(Boolean).join(' ');
        return `<li class="${cls}" data-member="${index}">
            <label class="orch-switch" title="${on ? 'On — switch off to skip this agent' : 'Off — switch on to use this agent'}">
                <input type="checkbox" data-toggle-member${on ? ' checked' : ''} aria-label="Use ${M.esc(label)}"><span aria-hidden="true"></span>
            </label>
            <span class="orch-av orch-av--sm">${temp ? `<span class="orch-av-emoji" aria-hidden="true">${M.esc(M.agentIcon(state.catalog, m.agent))}</span>` : M.avatarHtml(p ? p.avatar : '')}</span>
            <span class="orch-member-main">
                <span class="orch-member-name">${temp
                    ? `<input class="orch-member-rename" data-rename value="${M.esc(m.name)}" maxlength="60" aria-label="Temporary agent name">`
                    : M.esc(label)}${temp ? '<span class="orch-tag">Temporary</span>' : ''}${isDefault ? '<span class="orch-tag is-accent">Default</span>' : ''}</span>
                ${temp
                    ? `<button type="button" class="orch-member-pin orch-pin-btn" data-edit-pin title="Change harness, model or effort">${pinText}</button>`
                    : `<span class="orch-member-pin">${pinText}</span>`}
            </span>
            <span class="orch-member-actions">
                ${index > 0 ? `<button type="button" class="orch-icon-btn" data-tier="${tierIndex}" data-promote title="Make default" aria-label="Make ${M.esc(label)} the default">★</button>` : ''}
                <button type="button" class="orch-icon-btn is-danger" data-tier="${tierIndex}" data-drop-member title="Remove" aria-label="Remove ${M.esc(label)}">✕</button>
            </span>
        </li>`;
    }

    function tierHtml(tier, index, count) {
        const meta = M.ROLE_META[tier.role] || M.ROLE_META.worker;
        const members = M.tierMembers(tier);
        const used = new Set(members.map((m) => m.profile).filter(Boolean));
        const available = state.profiles.filter((p) => !used.has(p.id));
        const full = members.length >= M.MAX_MEMBERS;
        const defaultIndex = members.findIndex(M.isOn);
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
                <ul class="orch-members">${members.map((m, i) => memberHtml(m, i, index, i === defaultIndex)).join('')
                    || '<li class="orch-member is-empty">No agents yet — add a profile or a temporary agent below.</li>'}</ul>
                ${members.length && defaultIndex < 0 ? '<p class="orch-tier-hint is-off">Every agent here is off, so this tier is skipped.</p>' : ''}
                <div class="orch-tier-add">
                    <select data-add-profile aria-label="Add a profile to this tier"${available.length && !full ? '' : ' disabled'}>
                        <option value="">${full ? `Tier is full (${M.MAX_MEMBERS})` : (available.length ? 'Add a profile…' : 'All profiles added')}</option>
                        ${available.map((p) => `<option value="${M.esc(p.id)}">${M.esc(p.name)} — ${M.esc(M.pinLabel(state.catalog, p))}</option>`).join('')}
                    </select>
                    <button type="button" class="orch-small-btn" data-add-temp${full || !pinPicker ? ' disabled' : ''} title="Pick a harness, model and effort just for this tier">＋ Temporary agent</button>
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
        return `/orchestrate${d.id === state.defaultId ? '' : ' @' + d.id} <goal>`;
    }

    function renderSavebar() {
        const bar = $('orchSavebar');
        $('orchDelete').hidden = !state.draft || !state.savedId || !!state.draft.seed;
        $('orchDuplicate').hidden = !state.draft;
        const isDefault = !!state.draft && state.savedId === state.defaultId && !!state.savedId;
        const defBtn = $('orchMakeDefault');
        defBtn.hidden = !state.draft;
        defBtn.disabled = isDefault || !state.savedId;
        defBtn.classList.toggle('is-default', isDefault);
        defBtn.textContent = isDefault ? '★ Default' : '☆ Make default';
        defBtn.title = isDefault ? '/orchestrate uses this playbook when no @playbook is given'
            : (state.savedId ? 'Use this playbook for /orchestrate without @playbook' : 'Save the playbook first');
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
        // An untouched seed is not dirty; the savebar still offers to save it (draft.seed).
        state.saved = snapshot(pb);
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
            state.defaultId = pbs.default_id || 'default';
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

    function memberIndex(el) {
        const li = el.closest('[data-member]');
        return li ? Number(li.dataset.member) : -1;
    }

    function openPin(tierIndex, index) {
        if (!pinPicker) return;
        const current = index >= 0 ? M.tierMembers(state.draft.tiers[tierIndex])[index] : null;
        pinTarget = { tierIndex, index, autoName: !current || current.name === M.pinLabel(state.catalog, current) };
        pinPicker.open(current ? { agent: current.agent, model: current.model, effort: current.effort } : null);
    }

    function onTemporaryPin(pin) {
        const t = pinTarget;
        if (!t || !state.draft || !state.draft.tiers[t.tierIndex]) return;
        const tier = state.draft.tiers[t.tierIndex];
        if (!pin.agent || pin.agent === 'none') return; // a temporary agent always runs on a harness
        const prev = t.index >= 0 ? M.tierMembers(tier)[t.index] : null;
        const member = M.temporaryMember(state.catalog, pin, t.autoName || !prev ? '' : prev.name);
        if (t.index < 0) {
            const next = M.addMember(tier, member);
            if (M.tierMembers(next).length === M.tierMembers(tier).length) { toast('That agent is already in this tier', 'info'); return; }
            state.draft.tiers[t.tierIndex] = next;
            t.index = M.tierMembers(next).length - 1;
        } else {
            state.draft.tiers[t.tierIndex] = M.replaceMember(tier, t.index, member);
        }
        renderTiers();
        changed();
    }

    const tiersEl = $('orchTiers');
    tiersEl.addEventListener('input', (e) => {
        const box = e.target.closest('.orch-tier');
        if (box && e.target.hasAttribute('data-rename')) {
            const m = M.tierMembers(state.draft.tiers[Number(box.dataset.index)])[memberIndex(e.target)];
            if (m) { m.name = e.target.value; changed(); }
            return;
        }
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
            state.draft.tiers[i] = M.addMember(state.draft.tiers[i], e.target.value);
            renderTiers();
            changed();
        } else if (e.target.hasAttribute('data-toggle-member')) {
            state.draft.tiers[i] = M.toggleMember(state.draft.tiers[i], memberIndex(e.target));
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
        } else if (btn.hasAttribute('data-drop-member')) tiers[i] = M.removeMember(tiers[i], memberIndex(btn));
        else if (btn.hasAttribute('data-promote')) tiers[i] = M.promoteMember(tiers[i], memberIndex(btn));
        else if (btn.hasAttribute('data-add-temp')) { openPin(i, -1); return; }
        else if (btn.hasAttribute('data-edit-pin')) { openPin(i, memberIndex(btn)); return; }
        else return;
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

    $('orchDuplicate').addEventListener('click', () => {
        if (!state.draft) return;
        if (isDirty() && !window.confirm('Duplicate with your unsaved changes? The original stays as last saved.')) return;
        state.draft = M.duplicatePlaybook(state.draft, state.playbooks.map((p) => p.id));
        state.savedId = '';
        state.saved = '';
        renderEditor();
        $('orchName').select();
        toast('Copy ready — save it to keep it', 'info');
    });
    $('orchMakeDefault').addEventListener('click', async () => {
        if (!state.savedId) return;
        if (isDirty() && !window.confirm('Make the saved version the default? Unsaved changes stay unsaved.')) return;
        try {
            const data = await call('/api/orchestrator/default', 'PUT', { id: state.savedId });
            state.defaultId = data.default_id;
            toast(`/orchestrate now uses ${state.draft.name}`, 'success');
            renderSidebar();
            changed();
        } catch (err) { toast(err.message, 'error'); }
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
