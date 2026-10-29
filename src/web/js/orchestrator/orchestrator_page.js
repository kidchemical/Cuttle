/**
 * Orchestrator App controller — transport + events over CuttleOrchestrator
 * (pure). Playbooks are owned by api.orchestrator; profiles by the Agents App.
 */
(function () {
    'use strict';

    const M = window.CuttleOrchestrator;
    const $ = (id) => document.getElementById(id);
    const notice = $('orchNotice');
    const content = $('orchContent');
    const meta = $('orchMeta');
    const sup = $('orchSupervisor');
    let state = { playbooks: [], profiles: [], catalog: [], bindings: [], draft: null, savedId: '', dirty: false, preview: null };
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

    function showNotice(html, kind) {
        notice.hidden = false;
        notice.className = 'orch-notice' + (kind ? ' is-' + kind : '');
        notice.innerHTML = html;
        content.hidden = true;
    }

    function profileById(id) {
        return state.profiles.find((p) => p.id === id) || null;
    }

    function markDirty() {
        state.dirty = true;
        $('orchSaveState').textContent = 'Unsaved changes';
        renderProblems();
    }

    // ── Rendering ────────────────────────────────────────────────────────

    function renderList() {
        $('orchList').innerHTML = state.playbooks.map((pb) => {
            const missing = (pb.missing_profiles || []).length;
            const badges = (pb.seed ? '<span class="orch-badge">not saved</span>' : '')
                + (missing ? `<span class="orch-badge is-bad">${missing} missing</span>` : '');
            return `<div class="orch-row${state.draft && state.savedId === pb.id ? ' is-selected' : ''}" data-open="${M.esc(pb.id)}" style="cursor:pointer;grid-template-columns:minmax(0,1fr) auto">
                <div><div class="orch-row-title">${M.esc(pb.name)} ${badges}</div>
                <div class="orch-row-meta">${M.esc(pb.id)} · ${(pb.tiers || []).length} tiers</div></div></div>`;
        }).join('') || '<div class="orch-notice">No playbooks.</div>';
        $('orchBindings').innerHTML = state.bindings.map((b) => `<div class="orch-row" style="grid-template-columns:minmax(0,1fr) auto">
            <div><div class="orch-row-title">${M.esc(b.session)} <span class="orch-muted">${M.esc(b.playbook_id)}</span></div>
            <div class="orch-row-meta">${M.esc(b.title || b.goal || '')}</div></div>
            <div class="orch-row-actions"><button type="button" class="orch-small" data-target="${M.esc(b.session)}">Supervise…</button>
            <button type="button" class="orch-small orch-danger" data-unbind="${M.esc(b.session)}">Unbind</button></div></div>`).join('')
            || '<div class="orch-muted">None yet. Start one with <code>/orchestrate</code>.</div>';
    }

    function tierHtml(tier, index, count) {
        const chips = tier.profiles.map((pid) => {
            const p = profileById(pid);
            return `<span class="orch-chip${p ? '' : ' is-missing'}" title="${M.esc(M.profileSummary(p))}">
                ${M.esc(p ? p.name : pid)} <span class="orch-muted">${M.esc(M.profileSummary(p))}</span>
                <button type="button" data-tier="${index}" data-up-profile="${M.esc(pid)}" title="Make default" aria-label="Move earlier">↑</button>
                <button type="button" data-tier="${index}" data-drop-profile="${M.esc(pid)}" aria-label="Remove">✕</button></span>`;
        }).join('');
        const options = state.profiles.filter((p) => tier.profiles.indexOf(p.id) < 0)
            .map((p) => `<option value="${M.esc(p.id)}">${M.esc(p.name)} — ${M.esc(M.profileSummary(p))}</option>`).join('');
        const roles = M.ROLES.map((r) => `<option value="${r}"${tier.role === r ? ' selected' : ''}>${M.ROLE_LABELS[r]}</option>`).join('');
        return `<div class="orch-tier" data-index="${index}">
            <div class="orch-tier-head">
                <span class="orch-tier-step">${index + 1}</span>
                <input data-field="label" value="${M.esc(tier.label)}" aria-label="Tier name" maxlength="60">
                <select data-field="role" aria-label="Role">${roles}</select>
                <div class="orch-row-actions">
                    <button type="button" class="orch-small" data-move="-1" ${index === 0 ? 'disabled' : ''} aria-label="Move up">↑</button>
                    <button type="button" class="orch-small" data-move="1" ${index === count - 1 ? 'disabled' : ''} aria-label="Move down">↓</button>
                    <button type="button" class="orch-small orch-danger" data-remove-tier ${count === 1 ? 'disabled' : ''}>Remove</button>
                </div>
            </div>
            <div class="orch-chips">${chips || '<span class="orch-muted">No profiles yet.</span>'}</div>
            <div style="display:flex;gap:8px;margin-bottom:8px">
                <select data-add-profile aria-label="Add profile"><option value="">Add a profile…</option>${options}</select>
                <a href="/agent_profiles_page.html" class="orch-muted" style="align-self:center">Manage profiles</a>
            </div>
            <textarea data-field="use_for" rows="2" maxlength="600" placeholder="Use this tier for…" style="width:100%;box-sizing:border-box">${M.esc(tier.use_for)}</textarea>
        </div>`;
    }

    function renderTiers() {
        const tiers = state.draft.tiers;
        $('orchTiers').innerHTML = tiers.map((t, i) => tierHtml(t, i, tiers.length)).join('');
    }

    function renderLimits() {
        $('orchLimits').innerHTML = M.LIMIT_FIELDS.map(([key, label, low, high]) =>
            `<label>${M.esc(label)}<input type="number" name="${key}" min="${low}" max="${high}" value="${M.esc(state.draft.limits[key])}"></label>`).join('');
    }

    function renderSupervisor() {
        const s = state.draft.supervisor;
        sup.agent.innerHTML = ['<option value="">Follow the supervisor chat (recommended)</option>']
            .concat(state.catalog.map((r) => `<option value="${M.esc(r.id)}">${M.esc(r.label || r.id)}</option>`)).join('');
        sup.every_sec.value = s.every_sec;
        sup.agent.value = s.agent || '';
        sup.require_branch.value = s.require_branch || '';
        sup.require_path.value = s.require_path || '';
        sup.forbid_branches.value = (s.forbid_branches || []).join(', ');
        sup.instructions.value = s.instructions || '';
    }

    function renderProblems() {
        const problems = M.playbookProblems(state.draft, state.profiles.map((p) => p.id));
        const box = $('orchProblems');
        box.hidden = !problems.length;
        box.innerHTML = problems.length ? `<strong>Fix before saving</strong><ul>${problems.map((p) => `<li>${M.esc(p)}</li>`).join('')}</ul>` : '';
        $('orchSave').disabled = problems.length > 0;
    }

    function renderEditor() {
        const d = state.draft;
        meta.id.value = d.id;
        meta.id.readOnly = !!state.savedId && !d.seed;
        meta.name.value = d.name;
        meta.description.value = d.description || '';
        $('orchNotes').value = d.notes || '';
        $('orchStartHint').innerHTML = `Start in any chat: <code>/orchestrate${d.id === 'default' ? '' : ' @' + M.esc(d.id)} &lt;goal&gt;</code>`;
        $('orchDelete').disabled = !state.savedId || !!d.seed;
        $('orchSaveState').textContent = d.seed ? 'Built-in seed — save to customize' : '';
        renderTiers();
        renderLimits();
        renderSupervisor();
        renderProblems();
        renderList();
        loadPreview();
    }

    async function loadPreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(async () => {
            if (!state.savedId) { $('orchBrief').textContent = 'Save the playbook to preview its brief.'; $('orchSupervise').textContent = ''; return; }
            const target = $('orchTarget').value.trim();
            try {
                const q = target ? '?session=' + encodeURIComponent(target) : '';
                state.preview = await call(`/api/orchestrator/playbooks/${encodeURIComponent(state.savedId)}/preview${q}`);
                $('orchBrief').textContent = state.preview.brief;
                $('orchSupervise').textContent = state.preview.supervisor_command;
            } catch (err) {
                $('orchSupervise').textContent = err.message;
            }
        }, 250);
    }

    // ── Draft edits ──────────────────────────────────────────────────────

    function readForms() {
        const d = state.draft;
        d.id = M.slug(meta.id.value);
        d.name = meta.name.value.trim();
        d.description = meta.description.value.trim();
        d.notes = $('orchNotes').value;
        M.LIMIT_FIELDS.forEach(([key]) => { d.limits[key] = Number($('orchLimits').elements[key].value); });
        d.supervisor = {
            every_sec: Number(sup.every_sec.value), agent: sup.agent.value,
            instructions: sup.instructions.value.trim(), require_branch: sup.require_branch.value.trim(),
            require_path: sup.require_path.value.trim(), forbid_branches: M.parseBranches(sup.forbid_branches.value),
        };
    }

    function openPlaybook(id) {
        if (state.dirty && !window.confirm('Discard unsaved changes?')) return;
        const pb = state.playbooks.find((p) => p.id === id);
        if (!pb) return;
        state.draft = M.clone(pb);
        state.savedId = pb.id;
        state.dirty = false;
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
            state.dirty = false;
            if (want) openPlaybook(want); else renderList();
        } catch (err) {
            if (err.disabled) showNotice('Turn on <strong>Orchestrator</strong> in <a href="/settings_page.html?tab=experimental">Settings → Experimental</a>.');
            else showNotice(M.esc(err.message || 'Orchestrator could not load.'), 'error');
        }
    }

    $('orchTiers').addEventListener('input', (event) => {
        const box = event.target.closest('.orch-tier');
        const field = event.target.dataset.field;
        if (!box || !field) return;
        state.draft.tiers[Number(box.dataset.index)][field] = event.target.value;
        markDirty();
    });
    $('orchTiers').addEventListener('change', (event) => {
        const box = event.target.closest('.orch-tier');
        if (!box) return;
        const i = Number(box.dataset.index);
        if (event.target.dataset.field === 'role') {
            state.draft.tiers[i].role = event.target.value;
            markDirty();
        } else if (event.target.hasAttribute('data-add-profile') && event.target.value) {
            state.draft.tiers[i] = M.addProfileToTier(state.draft.tiers[i], event.target.value);
            markDirty();
            renderTiers();
        }
    });
    $('orchTiers').addEventListener('click', (event) => {
        const btn = event.target.closest('button');
        const box = event.target.closest('.orch-tier');
        if (!btn || !box) return;
        const i = Number(box.dataset.index);
        const tiers = state.draft.tiers;
        if (btn.dataset.move) state.draft.tiers = M.moveItem(tiers, i, Number(btn.dataset.move));
        else if (btn.hasAttribute('data-remove-tier')) tiers.splice(i, 1);
        else if (btn.dataset.dropProfile) tiers[i] = M.removeProfileFromTier(tiers[i], btn.dataset.dropProfile);
        else if (btn.dataset.upProfile) {
            const at = tiers[i].profiles.indexOf(btn.dataset.upProfile);
            tiers[i].profiles = M.moveItem(tiers[i].profiles, at, -1);
        } else return;
        markDirty();
        renderTiers();
    });
    $('orchAddTier').addEventListener('click', () => {
        state.draft.tiers.push(M.newTier(state.draft.tiers));
        markDirty();
        renderTiers();
    });
    [meta, $('orchLimits'), sup].forEach((f) => f.addEventListener('input', () => { readForms(); markDirty(); }));
    sup.addEventListener('change', () => { readForms(); markDirty(); });
    $('orchNotes').addEventListener('input', () => { readForms(); markDirty(); });
    $('orchTarget').addEventListener('input', loadPreview);

    $('orchNew').addEventListener('click', () => {
        if (state.dirty && !window.confirm('Discard unsaved changes?')) return;
        let n = 1;
        while (state.playbooks.some((p) => p.id === `playbook-${n}`)) n += 1;
        state.draft = M.newPlaybook(`playbook-${n}`);
        state.savedId = '';
        state.dirty = true;
        renderEditor();
        meta.name.focus();
    });

    $('orchSave').addEventListener('click', async () => {
        readForms();
        const body = M.clone(state.draft);
        delete body.seed; delete body.missing_profiles; delete body.updated_at;
        try {
            const data = await call('/api/orchestrator/playbooks/' + encodeURIComponent(body.id), 'PUT', body);
            toast(`Saved ${data.playbook.name}`, 'success');
            state.savedId = data.playbook.id;
            state.dirty = false;
            load(data.playbook.id);
        } catch (err) {
            toast(err.message, 'error');
            $('orchSaveState').textContent = err.message;
        }
    });

    $('orchDelete').addEventListener('click', async () => {
        if (!state.savedId || !window.confirm(`Delete playbook ${state.savedId}? Chats bound to it fall back to an error until rebound.`)) return;
        try {
            await call('/api/orchestrator/playbooks/' + encodeURIComponent(state.savedId), 'DELETE');
            toast('Deleted', 'success');
            state.savedId = '';
            state.dirty = false;
            load();
        } catch (err) { toast(err.message, 'error'); }
    });

    async function copy(text, label) {
        try { await navigator.clipboard.writeText(text || ''); toast(`${label} copied`, 'success'); }
        catch (_) { toast('Copy failed — select the text instead', 'error'); }
    }
    $('orchCopyBrief').addEventListener('click', () => copy(state.preview && state.preview.brief, 'Brief'));
    $('orchCopySupervise').addEventListener('click', () => copy(state.preview && state.preview.supervisor_command, 'Command'));

    content.addEventListener('click', async (event) => {
        const open = event.target.closest('[data-open]');
        if (open) { openPlaybook(open.dataset.open); return; }
        const btn = event.target.closest('button');
        if (!btn) return;
        if (btn.dataset.target) {
            const b = state.bindings.find((x) => x.session === btn.dataset.target);
            if (b && b.playbook_id !== state.savedId) openPlaybook(b.playbook_id);
            $('orchTarget').value = btn.dataset.target;
            loadPreview();
            $('orchSupervise').scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else if (btn.dataset.unbind) {
            if (!window.confirm(`Unbind ${btn.dataset.unbind}? Its next fresh context will not carry the brief.`)) return;
            try {
                await call('/api/orchestrator/bindings/' + encodeURIComponent(btn.dataset.unbind), 'DELETE');
                load();
            } catch (err) { toast(err.message, 'error'); }
        }
    });

    $('orchRefresh').addEventListener('click', () => load());
    load();
})();
