/**
 * Agents App controller — transport + events over CuttleOrchestrator (pure).
 * Profiles are owned by api.subagents.profiles; this page edits them through
 * /api/agent-profiles, which runs the same pin check as spawn. "Runs on" uses
 * the composer's agent chip picker (CuttleAgentPinPicker → CuttleChipConfig);
 * the avatar is what child-chat bubbles show for the profile.
 */
(function () {
    'use strict';

    const M = window.CuttleOrchestrator;
    const $ = (id) => document.getElementById(id);
    const notice = $('agentsNotice');
    const content = $('agentsContent');
    const editor = $('agentsEditor');
    const search = $('agentsSearch');
    let state = { profiles: [], catalog: [], usage: {}, selected: '', draft: null, mode: '', check: null, dirty: false };
    let checkTimer = null;
    let checkSeq = 0;

    const pinPicker = window.CuttleAgentPinPicker.create({
        getCatalog: () => state.catalog,
        onChange(pin) {
            if (!state.draft) return;
            Object.assign(state.draft, pin);
            state.dirty = true;
            renderPin();
            scheduleCheck();
        },
    });
    const avatarPicker = window.CuttleAvatarPicker.create({
        upload: uploadAvatar,
        onPick(value) {
            if (!state.draft) return;
            state.draft.avatar = value;
            state.dirty = true;
            renderEditor();
        },
    });

    function toast(message, kind) {
        if (typeof window.showToast === 'function') window.showToast(message, kind || 'info');
    }

    async function call(path, method, body, raw) {
        const resp = await fetch(path, {
            method: method || 'GET', credentials: 'include', cache: 'no-store',
            headers: body && !raw ? { 'Content-Type': 'application/json' } : undefined,
            body: raw ? body : (body ? JSON.stringify(body) : undefined),
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

    async function uploadAvatar(file) {
        const form = new FormData();
        form.append('file', file);
        const data = await call('/api/agent-profiles/avatar', 'POST', form, true);
        return data.url;
    }

    function find(id) { return state.profiles.find((p) => p.id === id) || null; }

    // ── Sidebar ─────────────────────────────────────────────────────────

    function cardHtml(p) {
        const used = state.usage[p.id] || [];
        const active = state.selected === p.id && state.mode !== 'new' && state.mode !== 'dup';
        return `<button type="button" class="orch-card${active ? ' is-active' : ''}" data-open="${M.esc(p.id)}">
            <span class="orch-av orch-av--md">${M.avatarHtml(p.avatar)}</span>
            <span class="orch-card-main">
                <span class="orch-card-title">${M.esc(p.name)}</span>
                <span class="orch-card-sub"><span class="orch-pin-icon" aria-hidden="true">${M.esc(M.agentIcon(state.catalog, p.agent))}</span>${M.esc(M.pinLabel(state.catalog, p))}</span>
            </span>
            ${used.length ? `<span class="orch-dot-count" title="Used in ${M.esc(used.join(', '))}">${used.length}</span>` : ''}
        </button>`;
    }

    function renderList() {
        const q = search.value.trim().toLowerCase();
        const match = (p) => !q || [p.id, p.name, p.agent, p.model].join(' ').toLowerCase().includes(q);
        const custom = state.profiles.filter((p) => !p.builtin && match(p));
        const builtin = state.profiles.filter((p) => p.builtin && match(p));
        $('agentsCustom').innerHTML = custom.map(cardHtml).join('')
            || `<p class="orch-empty-line">${q ? 'No matches.' : 'None yet — create one or duplicate a built-in.'}</p>`;
        $('agentsBuiltin').innerHTML = builtin.map(cardHtml).join('') || (q ? '<p class="orch-empty-line">No matches.</p>' : '');
        $('agentsCustomCount').textContent = String(state.profiles.filter((p) => !p.builtin).length);
        $('agentsBuiltinCount').textContent = String(state.profiles.filter((p) => p.builtin).length);
    }

    // ── Editor ──────────────────────────────────────────────────────────

    function emptyHtml() {
        return `<div class="orch-empty">
            <div class="orch-empty-art" aria-hidden="true">${M.avatarHtml('🤖')}${M.avatarHtml('🦉')}${M.avatarHtml('cuttle')}</div>
            <h2>Agent profiles</h2>
            <p>A profile gives a sub-agent a name and an avatar in chat, and pins the harness, model and effort it runs on. Orchestrator playbooks are built from profiles.</p>
            <button type="button" class="primary" data-act="new">New profile</button>
        </div>`;
    }

    function pinHtml(d) {
        return `<button type="button" class="orch-pin" id="agentsPin" ${d.builtin ? 'disabled' : ''} aria-label="Change what this profile runs on">
            <span class="orch-pin-icon" aria-hidden="true">${M.esc(M.agentIcon(state.catalog, d.agent))}</span>
            <span class="orch-pin-text">${M.esc(M.pinLabel(state.catalog, d))}</span>
            ${d.builtin ? '' : '<span class="orch-pin-caret" aria-hidden="true"></span>'}
        </button>`;
    }

    function checkHtml() {
        const c = state.check;
        if (!c) return '';
        return `<span class="orch-status is-${c.kind}">${M.esc(c.text)}</span>`;
    }

    function renderPin() {
        const host = $('agentsPinHost');
        if (host) host.innerHTML = pinHtml(state.draft);
        renderPreview();
    }

    function renderPreview() {
        const host = $('agentsPreview');
        const d = state.draft;
        if (!host || !d) return;
        host.innerHTML = `<div class="orch-bubble">
            <span class="orch-av orch-av--sm">${M.avatarHtml(d.avatar)}</span>
            <div class="orch-bubble-body">
                <div class="orch-bubble-head"><strong>${M.esc(d.name || 'Unnamed')}</strong>
                    <span class="orch-mini-chip"><span aria-hidden="true">${M.esc(M.agentIcon(state.catalog, d.agent))}</span> ${M.esc(M.pinLabel(state.catalog, d))}</span></div>
                <div class="orch-bubble-text">On it — I'll report back in this child chat when the task is done.</div>
            </div>
        </div>`;
    }

    function renderEditor() {
        const d = state.draft;
        if (!d) { editor.innerHTML = emptyHtml(); return; }
        const isNew = state.mode === 'new' || state.mode === 'dup';
        const used = state.usage[d.id] || [];
        editor.innerHTML = `
            <div class="orch-identity">
                <button type="button" class="orch-av orch-av--xl orch-av-edit" id="agentsAvatar" ${d.builtin ? 'disabled' : ''} aria-haspopup="dialog" aria-expanded="false" aria-label="Change avatar">
                    ${M.avatarHtml(d.avatar)}${d.builtin ? '' : '<span class="orch-av-badge" aria-hidden="true">✎</span>'}
                </button>
                <div class="orch-identity-fields">
                    <input id="agentsName" class="orch-title-input" maxlength="80" placeholder="Profile name" value="${M.esc(d.name)}" ${d.builtin ? 'readonly' : ''} aria-label="Name">
                    <div class="orch-id-row">
                        <span class="orch-id-label">id</span>
                        <input id="agentsId" class="orch-id-input" maxlength="40" value="${M.esc(d.id)}" ${isNew ? '' : 'readonly'} aria-label="Profile id" spellcheck="false">
                        ${d.builtin ? '<span class="orch-tag">Built-in</span>' : ''}
                        ${used.map((u) => `<span class="orch-tag is-accent">${M.esc(u)}</span>`).join('')}
                    </div>
                </div>
            </div>

            <div class="orch-field-block">
                <div class="orch-block-head"><h3>Runs on</h3>${checkHtml()}</div>
                <div id="agentsPinHost">${pinHtml(d)}</div>
                <p class="orch-help">Checked against that CLI's model catalog on save and again before every spawn. “Any harness” lets the parent pick.</p>
            </div>

            <div class="orch-field-block">
                <div class="orch-block-head"><h3>In chat</h3></div>
                <div id="agentsPreview"></div>
                <p class="orch-help">Child chats spawned with this profile use this name and avatar on every reply.</p>
            </div>

            <div class="orch-editor-actions">
                ${d.builtin
                    ? '<span class="orch-help">Built-in profiles are read-only.</span><span class="orch-grow"></span><button type="button" class="primary" data-act="dup">Duplicate to customize</button>'
                    : `${isNew ? '' : '<button type="button" class="danger" data-act="delete">Delete</button>'}
                       <span class="orch-grow"></span>
                       ${isNew ? '' : '<button type="button" data-act="dup">Duplicate</button>'}
                       <button type="button" data-act="cancel">${isNew ? 'Cancel' : 'Revert'}</button>
                       <button type="button" class="primary" data-act="save">${isNew ? 'Create profile' : 'Save'}</button>`}
            </div>`;
        renderPreview();
    }

    function scheduleCheck() {
        clearTimeout(checkTimer);
        const d = state.draft;
        if (!d || d.builtin) { state.check = null; return; }
        const local = M.pinProblem(M.catalogRow(state.catalog, d.agent), d.agent, d.model, d.effort);
        if (local) { state.check = { kind: 'bad', text: local }; refreshCheck(); return; }
        state.check = { kind: 'pending', text: 'Checking…' };
        refreshCheck();
        const seq = ++checkSeq;
        checkTimer = setTimeout(async () => {
            try {
                const data = await call('/api/agent-profiles/check', 'POST', { agent: d.agent, model: d.model, effort: d.effort });
                if (seq !== checkSeq) return;
                state.check = data.ok ? { kind: 'ok', text: 'Ready to run' } : { kind: 'bad', text: data.error };
            } catch (err) {
                if (seq === checkSeq) state.check = { kind: 'bad', text: err.message };
            }
            refreshCheck();
        }, 300);
    }

    function refreshCheck() {
        const head = editor.querySelector('.orch-field-block .orch-block-head');
        if (!head) return;
        head.querySelector('.orch-status')?.remove();
        head.insertAdjacentHTML('beforeend', checkHtml());
    }

    function confirmDiscard() {
        return !state.dirty || window.confirm('Discard unsaved changes to this profile?');
    }

    function open(id, mode) {
        if (!confirmDiscard()) return;
        const p = id ? find(id) : null;
        const base = p ? M.clone(p) : M.emptyProfile();
        if (mode === 'dup' && p) {
            let n = 1;
            let next = `${p.id}-copy`.slice(0, 40);
            while (find(next)) next = `${p.id}-copy-${++n}`.slice(0, 40);
            base.id = next;
            base.name = `${p.name} (copy)`;
            base.builtin = false;
        }
        if (mode === 'new') base.avatar = '🤖';
        state.draft = base;
        state.selected = p ? p.id : '';
        state.mode = mode || 'edit';
        state.dirty = mode === 'dup';
        state.check = null;
        renderList();
        renderEditor();
        scheduleCheck();
        if (mode === 'new' || mode === 'dup') $('agentsName')?.focus();
    }

    async function save() {
        const d = state.draft;
        d.name = $('agentsName').value.trim();
        const id = M.slug($('agentsId').value);
        if (!M.validId(id)) { toast('Id must start with a letter and use a-z 0-9 _ - (max 40).', 'error'); $('agentsId').focus(); return; }
        if (!d.name) { toast('Give the profile a name.', 'error'); $('agentsName').focus(); return; }
        if ((state.mode === 'new' || state.mode === 'dup') && find(id)) { toast(`A profile named ${id} already exists.`, 'error'); return; }
        try {
            await call('/api/agent-profiles/' + encodeURIComponent(id), 'PUT', {
                name: d.name, avatar: d.avatar || '', agent: d.agent || 'none', model: d.model || '', effort: d.effort || '',
            });
            toast(`Saved ${d.name}`, 'success');
            state.dirty = false;
            await load(id);
        } catch (err) {
            state.check = { kind: 'bad', text: err.message };
            refreshCheck();
            toast(err.message, 'error');
        }
    }

    async function remove() {
        const d = state.draft;
        const used = state.usage[d.id] || [];
        const warn = used.length ? `\n\nUsed by: ${used.join(', ')}. Those playbooks will need another profile.` : '';
        if (!window.confirm(`Delete ${d.name}?${warn}`)) return;
        try {
            await call('/api/agent-profiles/' + encodeURIComponent(d.id), 'DELETE');
            toast(`Deleted ${d.name}`, 'success');
            state.dirty = false;
            state.draft = null;
            state.selected = '';
            await load();
        } catch (err) { toast(err.message, 'error'); }
    }

    async function load(selectId) {
        try {
            const [profiles, catalog, playbooks] = await Promise.all([
                call('/api/agent-profiles'),
                call('/api/agents?live_models=1').catch(() => call('/api/agents')),
                call('/api/orchestrator/playbooks').catch(() => ({ playbooks: [] })),
            ]);
            state.profiles = profiles.profiles || [];
            state.catalog = (catalog.agents || []).filter((a) => a && a.id);
            state.usage = M.profileUsage(playbooks.playbooks);
            notice.hidden = true;
            content.hidden = false;
            const want = selectId || state.selected;
            if (want && find(want)) { state.dirty = false; open(want, 'edit'); }
            else { renderList(); renderEditor(); }
        } catch (err) {
            notice.hidden = false;
            notice.classList.add('is-error');
            notice.innerHTML = err.disabled
                ? 'Agents is part of the Orchestrator experiment. Turn it on in <a href="/settings_page.html?tab=experimental">Settings → Experimental</a>.'
                : M.esc(err.message || 'Agents could not load.');
            content.hidden = true;
        }
    }

    // ── Events ──────────────────────────────────────────────────────────

    $('agentsNew').addEventListener('click', () => open('', 'new'));
    $('agentsRefresh').addEventListener('click', () => { if (confirmDiscard()) { state.dirty = false; load(); } });
    search.addEventListener('input', renderList);
    content.querySelector('.orch-sidebar').addEventListener('click', (e) => {
        const card = e.target.closest('[data-open]');
        if (card) open(card.dataset.open, 'edit');
    });
    editor.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn || btn.disabled) return;
        if (btn.id === 'agentsAvatar') { avatarPicker.open(btn, state.draft.avatar); return; }
        if (btn.id === 'agentsPin') { pinPicker.open(state.draft); return; }
        const act = btn.dataset.act;
        if (act === 'new') open('', 'new');
        else if (act === 'dup') open(state.draft.builtin || state.mode === 'edit' ? state.draft.id : state.selected, 'dup');
        else if (act === 'cancel') {
            state.dirty = false;
            if (state.selected && find(state.selected)) open(state.selected, 'edit');
            else { state.draft = null; renderList(); renderEditor(); }
        } else if (act === 'save') save();
        else if (act === 'delete') remove();
    });
    editor.addEventListener('input', (e) => {
        if (!state.draft) return;
        if (e.target.id === 'agentsName') { state.draft.name = e.target.value; state.dirty = true; renderPreview(); }
        else if (e.target.id === 'agentsId') { state.dirty = true; }
    });
    editor.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.target.id === 'agentsName' || e.target.id === 'agentsId')) { e.preventDefault(); save(); }
    });

    load();
})();
