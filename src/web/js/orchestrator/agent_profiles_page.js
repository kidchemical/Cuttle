/**
 * Agents App controller — transport + events over CuttleOrchestrator (pure).
 * Profiles are owned by api.subagents.profiles; this page edits them through
 * /api/agent-profiles, which runs the same pin check as spawn.
 */
(function () {
    'use strict';

    const M = window.CuttleOrchestrator;
    const $ = (id) => document.getElementById(id);
    const notice = $('agentsNotice');
    const content = $('agentsContent');
    const editor = $('agentsEditor');
    const form = $('agentsForm');
    const check = $('agentsCheck');
    let state = { profiles: [], catalog: [], usage: {}, editing: null };
    let checkTimer = null;
    let checkSeq = 0;

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

    function avatarHtml(avatar) {
        const a = String(avatar || '');
        if (/^https?:\/\//.test(a) || a.startsWith('/')) return `<img src="${M.esc(a)}" alt="" referrerpolicy="no-referrer">`;
        if (a === 'cuttle') return '🦑';
        return M.esc(a || '🤖');
    }

    function rowHtml(p) {
        const used = state.usage[p.id] || [];
        const usedBadge = used.length
            ? `<span class="orch-badge is-ok" title="${M.esc(used.join(', '))}">in ${used.length} playbook${used.length > 1 ? 's' : ''}</span>` : '';
        const actions = p.builtin
            ? `<button type="button" class="orch-small" data-dup="${M.esc(p.id)}">Duplicate</button>`
            : `<button type="button" class="orch-small" data-edit="${M.esc(p.id)}">Edit</button>
               <button type="button" class="orch-small" data-dup="${M.esc(p.id)}">Duplicate</button>
               <button type="button" class="orch-small orch-danger" data-del="${M.esc(p.id)}">Delete</button>`;
        return `<div class="orch-row${state.editing === p.id ? ' is-selected' : ''}">
            <div class="orch-avatar">${avatarHtml(p.avatar)}</div>
            <div><div class="orch-row-title">${M.esc(p.name)} <span class="orch-muted">${M.esc(p.id)}</span> ${usedBadge}</div>
                <div class="orch-row-meta">${M.esc(M.profileSummary(p))}</div></div>
            <div class="orch-row-actions">${actions}</div></div>`;
    }

    function render() {
        notice.hidden = true;
        content.hidden = false;
        const custom = state.profiles.filter((p) => !p.builtin);
        const builtin = state.profiles.filter((p) => p.builtin);
        $('agentsCustom').innerHTML = custom.length ? custom.map(rowHtml).join('')
            : '<div class="orch-notice">No custom profiles yet. Create one, or duplicate a built-in.</div>';
        $('agentsBuiltin').innerHTML = builtin.map(rowHtml).join('');
    }

    function fillHarnesses(selected) {
        const opts = ['<option value="none">Any harness (no pin)</option>'].concat(
            state.catalog.map((row) => `<option value="${M.esc(row.id)}">${M.esc(row.label || row.id)}${row.available === false ? ' (not installed)' : ''}</option>`));
        form.agent.innerHTML = opts.join('');
        form.agent.value = selected && (selected === 'none' || M.catalogRow(state.catalog, selected)) ? selected : 'none';
    }

    function syncPins() {
        const row = M.catalogRow(state.catalog, form.agent.value);
        const opts = M.pinOptions(row, form.model.value.trim());
        $('agentsModels').innerHTML = opts.models.map((m) => `<option value="${M.esc(m)}"></option>`).join('');
        form.model.disabled = !row || !opts.supportsModel;
        if (form.model.disabled) form.model.value = '';
        const current = form.effort.value;
        form.effort.innerHTML = ['<option value="">CLI default</option>']
            .concat(opts.efforts.map((e) => `<option value="${M.esc(e)}">${M.esc(e)}</option>`)).join('');
        form.effort.disabled = !opts.supportsEffort;
        form.effort.value = opts.efforts.indexOf(current) >= 0 ? current : '';
        scheduleCheck();
    }

    function scheduleCheck() {
        clearTimeout(checkTimer);
        const agent = form.agent.value;
        const model = form.model.value.trim();
        const effort = form.effort.value;
        const local = M.pinProblem(M.catalogRow(state.catalog, agent), agent, model, effort);
        if (local) { check.className = 'orch-check is-bad'; check.textContent = local; return; }
        check.className = 'orch-check'; check.textContent = 'Checking…';
        const seq = ++checkSeq;
        checkTimer = setTimeout(async () => {
            try {
                const data = await call('/api/agent-profiles/check', 'POST', { agent, model, effort });
                if (seq !== checkSeq) return;
                check.className = 'orch-check ' + (data.ok ? 'is-ok' : 'is-bad');
                check.textContent = data.ok ? 'Pin is runnable' : data.error;
            } catch (err) {
                if (seq === checkSeq) { check.className = 'orch-check is-bad'; check.textContent = err.message; }
            }
        }, 350);
    }

    function openEditor(profile, mode) {
        const p = profile || M.emptyProfile();
        state.editing = mode === 'edit' ? p.id : null;
        $('agentsEditorTitle').textContent = mode === 'edit' ? `Edit ${p.name}` : 'New profile';
        form.id.value = mode === 'dup' ? `${p.id}-copy`.slice(0, 40) : p.id;
        form.id.readOnly = mode === 'edit';
        form.name.value = mode === 'dup' ? `${p.name} (copy)` : p.name;
        form.avatar.value = p.avatar || '';
        fillHarnesses(p.agent || 'none');
        form.model.value = p.model || '';
        syncPins();
        form.effort.value = p.effort || '';
        scheduleCheck();
        editor.hidden = false;
        render();
        form.name.focus();
    }

    function closeEditor() {
        editor.hidden = true;
        state.editing = null;
        render();
    }

    async function load() {
        try {
            const [profiles, catalog, playbooks] = await Promise.all([
                call('/api/agent-profiles'),
                call('/api/agents?live_models=1'),
                call('/api/orchestrator/playbooks').catch(() => ({ playbooks: [] })),
            ]);
            state.profiles = profiles.profiles || [];
            state.catalog = (catalog.agents || []).filter((a) => a && a.id);
            state.usage = M.profileUsage(playbooks.playbooks);
            render();
        } catch (err) {
            if (err.disabled) showNotice('Agents is part of the Orchestrator experiment. Turn it on in <a href="/settings_page.html?tab=experimental">Settings → Experimental</a>.');
            else showNotice(M.esc(err.message || 'Agents could not load.'), 'error');
        }
    }

    form.agent.addEventListener('change', () => { form.model.value = ''; form.effort.value = ''; syncPins(); });
    form.model.addEventListener('input', syncPins);
    form.effort.addEventListener('change', scheduleCheck);
    $('agentsCancel').addEventListener('click', closeEditor);
    $('agentsNew').addEventListener('click', () => openEditor(null, 'new'));
    $('agentsRefresh').addEventListener('click', load);

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const id = M.slug(form.id.value);
        if (!M.validId(id)) { toast('Id must start with a letter and use a-z 0-9 _ -', 'error'); return; }
        try {
            await call('/api/agent-profiles/' + encodeURIComponent(id), 'PUT', {
                name: form.name.value.trim(), avatar: form.avatar.value.trim(),
                agent: form.agent.value, model: form.model.value.trim(), effort: form.effort.value,
            });
            toast(`Saved ${id}`, 'success');
            closeEditor();
            load();
        } catch (err) {
            check.className = 'orch-check is-bad';
            check.textContent = err.message;
            toast(err.message, 'error');
        }
    });

    content.addEventListener('click', async (event) => {
        const btn = event.target.closest('button');
        if (!btn) return;
        const find = (id) => state.profiles.find((p) => p.id === id);
        if (btn.dataset.edit) openEditor(find(btn.dataset.edit), 'edit');
        else if (btn.dataset.dup) openEditor(find(btn.dataset.dup), 'dup');
        else if (btn.dataset.del) {
            const id = btn.dataset.del;
            const used = state.usage[id] || [];
            const warn = used.length ? `\n\nUsed by: ${used.join(', ')}. Those playbooks will need another profile.` : '';
            if (!window.confirm(`Delete profile ${id}?${warn}`)) return;
            try {
                await call('/api/agent-profiles/' + encodeURIComponent(id), 'DELETE');
                toast(`Deleted ${id}`, 'success');
                load();
            } catch (err) { toast(err.message, 'error'); }
        }
    });

    load();
})();
