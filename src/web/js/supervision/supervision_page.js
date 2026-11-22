/**
 * Supervision App controller — transport + DOM over CuttleSupervisionApp (pure).
 *
 * Owner of the data is api.chat_ops: GET /api/chat-ops/supervisions (every
 * supervision in force, plus whether the host holds the display awake) and
 * GET/PUT /api/chat-ops/defaults (what a new supervision starts from). Pause,
 * resume and stop go through the same /api/chat-ops/operations routes as the
 * chat header chip; one supervision's settings are edited from its chat.
 */
(function () {
    'use strict';

    const A = window.CuttleSupervisionApp;
    const S = window.CuttleChatSupervisionModel;
    const $ = (id) => document.getElementById(id);
    const notice = $('supNotice');
    const content = $('supContent');
    const listEl = $('supList');
    const noteEl = $('supDisplayNote');
    const form = $('supDefaults');
    const savebar = $('supSavebar');
    const stateEl = $('supSaveState');
    const POLL_MS = 8000;

    const state = { rows: [], display: null, saved: null, builtin: null, agents: [], pauseFor: '', busy: false };
    let pollTimer = null;

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
        if (data.disabled) {
            const err = new Error('Chat supervision is turned off.');
            err.disabled = true;
            throw err;
        }
        if (!resp.ok || data.success === false) throw new Error(data.error || `Request failed (${resp.status})`);
        return data;
    }

    function navigate(page) {
        if (window.parent !== window) window.parent.postMessage({ type: 'cuttle-navigate', page }, location.origin);
        else location.href = page;
    }

    // ── Running now ─────────────────────────────────────────────────────

    function pauseMenuHtml(id) {
        return '<div class="sup-pause-menu" role="group" aria-label="Pause for how long">'
            + S.PAUSE_CHOICES.map((c) => `<button type="button" class="orch-small-btn${c.primary ? ' primary' : ''}" data-pause="${A.esc(c.id)}" data-id="${A.esc(id)}">${A.esc(c.label)}</button>`).join('')
            + '</div>';
    }

    function rowHtml(r) {
        const tags = r.care.map((c) => `<span class="orch-tag${c === 'display' ? ' is-accent' : ''}">${A.esc(A.CARE_LABELS[c] || c)}</span>`).join('');
        const alerts = r.alerts.map((a) => `<span class="orch-status is-bad">${A.esc(a)}</span>`).join('');
        return `<article class="sup-row${r.paused ? ' is-paused' : ''}" data-row="${A.esc(r.id)}">
            <span class="sup-dot" aria-hidden="true"></span>
            <div class="sup-row-main">
                <div class="sup-row-title"><strong>${A.esc(r.title)}</strong><span class="sup-handle">${A.esc(r.handle)}</span>${r.project ? `<span class="sup-project">${A.esc(r.project)}</span>` : ''}</div>
                <div class="sup-row-status">${A.esc(r.status)} · ${A.esc(r.every)}</div>
                <div class="sup-row-meta"><span class="sup-by">Supervised by ${A.esc(r.by)}</span>${tags}${alerts}</div>
                ${!r.paused && state.pauseFor === r.id ? pauseMenuHtml(r.id) : ''}
            </div>
            <div class="sup-row-actions">
                <button type="button" class="orch-small-btn" data-open="${A.esc(r.targetId)}">Open chat</button>
                <button type="button" class="orch-small-btn" data-open="${A.esc(r.supervisorId)}" title="Its badge edits this supervision's settings">Open supervisor</button>
                <button type="button" class="orch-small-btn" data-act="${r.paused ? 'resume' : 'pause-menu'}" data-id="${A.esc(r.id)}">${r.paused ? 'Resume now' : 'Pause…'}</button>
                <button type="button" class="orch-small-btn danger" data-act="cancel" data-id="${A.esc(r.id)}">Stop</button>
            </div>
        </article>`;
    }

    function renderList() {
        const now = Date.now() / 1000;
        const rows = state.rows.map((s) => A.row(s, now));
        $('supCount').textContent = rows.length ? String(rows.length) : '';
        listEl.innerHTML = rows.length ? rows.map(rowHtml).join('')
            : '<p class="orch-empty-line">No supervision is running. Start one from a chat\'s ☰ menu → <strong>Supervise this chat…</strong></p>';
        const note = A.displayNote(state.display, rows);
        noteEl.hidden = !note;
        if (note) {
            noteEl.className = 'orch-status sup-display-note ' + (note.kind === 'ok' ? 'is-ok' : 'is-bad');
            noteEl.textContent = note.text;
        }
    }

    async function loadList(quiet) {
        try {
            const data = await call('/api/chat-ops/supervisions');
            state.rows = data.supervisions || [];
            state.display = data.display || null;
            renderList();
        } catch (err) {
            if (!quiet) throw err;
        }
    }

    async function act(verb, id, body) {
        if (state.busy) return;
        if (verb === 'cancel' && !window.confirm('Stop this supervision? The supervisor chat stays, but it is no longer woken.')) return;
        state.busy = true;
        try {
            await call(`/api/chat-ops/operations/${encodeURIComponent(id)}/${verb}`, 'POST', body || {});
            state.pauseFor = '';
            toast(verb === 'cancel' ? 'Supervision stopped' : verb === 'pause' ? 'Supervision paused' : 'Supervision resumed', 'success');
            await loadList(true);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            state.busy = false;
        }
    }

    listEl.addEventListener('click', (ev) => {
        const btn = ev.target.closest('button');
        if (!btn) return;
        if (btn.dataset.open) { navigate(`/chat_page.html?chat=${encodeURIComponent(btn.dataset.open)}`); return; }
        if (btn.dataset.pause) { act('pause', btn.dataset.id, S.pauseBody(btn.dataset.pause)); return; }
        if (btn.dataset.act === 'pause-menu') {
            state.pauseFor = state.pauseFor === btn.dataset.id ? '' : btn.dataset.id;
            renderList();
            return;
        }
        if (btn.dataset.act) act(btn.dataset.act, btn.dataset.id);
    });

    // ── Defaults ────────────────────────────────────────────────────────

    function options(choices, selected) {
        return choices.map((c) => `<option value="${A.esc(c.id)}"${c.id === selected ? ' selected' : ''}>${A.esc(c.label)}</option>`).join('');
    }

    function fillForm(defaults) {
        const f = A.fields(defaults, state.builtin);
        form.every.innerHTML = S.INTERVALS.map((s) => `<option value="${s}"${s === f.every ? ' selected' : ''}>${A.esc(S.fmtInterval(s))}</option>`).join('');
        const agents = state.agents.slice();
        if (f.agent && !agents.some((a) => a.id === f.agent)) agents.push({ id: f.agent, label: f.agent + ' (not installed)' });
        form.agent.innerHTML = '<option value="">No preference</option>' + options(agents.map((a) => ({ id: a.id, label: a.label || a.id })), f.agent);
        form.involve_user.innerHTML = options(S.INVOLVE_CHOICES, f.involve_user);
        form.nudge.innerHTML = options(S.NUDGE_CHOICES, f.nudge);
        $('supCare').innerHTML = S.SYSTEM_CARE.map((c) => `<label class="sup-care"><span class="sup-care-text"><strong>${A.esc(c.label)}</strong><small>${A.esc(c.hint)}</small></span>
            <span class="orch-switch"><input type="checkbox" role="switch" name="sys_${A.esc(c.id)}"${f.system[c.id] ? ' checked' : ''}><span></span></span></label>`).join('');
        form.paused_alert_hours.value = String(f.paused_alert_hours);
        // Selects were rebuilt: let the shared combobox pick up the new options.
        form.querySelectorAll('select').forEach((el) => el.dispatchEvent(new Event('change', { bubbles: true })));
        syncSavebar();
    }

    function readForm() {
        const fd = new FormData(form);
        return {
            every: Number(fd.get('every')), agent: fd.get('agent') || '', involve_user: fd.get('involve_user'),
            nudge: fd.get('nudge'), paused_alert_hours: fd.get('paused_alert_hours'),
            system: Object.fromEntries(S.SYSTEM_CARE.map((c) => [c.id, fd.get('sys_' + c.id) != null])),
        };
    }

    function isDirty() { return !!state.saved && !A.same(readForm(), state.saved); }

    function syncSavebar(error) {
        const dirty = isDirty();
        savebar.hidden = !dirty && !error;
        savebar.classList.toggle('is-dirty', dirty);
        stateEl.className = 'orch-savebar-state' + (error ? ' is-bad' : dirty ? ' is-dirty' : ' is-ok');
        stateEl.textContent = error || (dirty ? 'Unsaved changes' : 'Saved');
        $('supReset').disabled = !state.builtin || A.same(readForm(), state.builtin);
    }

    async function save() {
        const out = A.body(readForm());
        if (!out.ok) { syncSavebar(out.errors.join(' ')); return; }
        try {
            const data = await call('/api/chat-ops/defaults', 'PUT', out.body);
            state.saved = data.defaults;
            fillForm(state.saved);
            toast('Supervision defaults saved', 'success');
        } catch (err) {
            syncSavebar(err.message);
        }
    }

    form.addEventListener('input', () => syncSavebar());
    form.addEventListener('change', () => syncSavebar());
    form.addEventListener('submit', (ev) => { ev.preventDefault(); save(); });
    $('supSave').addEventListener('click', save);
    $('supDiscard').addEventListener('click', () => fillForm(state.saved));
    $('supReset').addEventListener('click', () => fillForm(state.builtin));
    document.addEventListener('keydown', (ev) => {
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's' && isDirty()) { ev.preventDefault(); save(); }
    });
    window.addEventListener('beforeunload', (ev) => { if (isDirty()) ev.preventDefault(); });

    // ── Boot ────────────────────────────────────────────────────────────

    async function load() {
        try {
            const [defaults, agents] = await Promise.all([
                call('/api/chat-ops/defaults'),
                call('/api/agents').catch(() => ({ agents: [] })),
                loadList(false),
            ]);
            state.saved = defaults.defaults;
            state.builtin = defaults.builtin;
            state.agents = ((agents && agents.agents) || []).filter((a) => a && a.id && a.sticky !== false);
            fillForm(state.saved);
            notice.hidden = true;
            content.hidden = false;
        } catch (err) {
            content.hidden = true;
            notice.hidden = false;
            notice.classList.toggle('is-error', !err.disabled);
            notice.textContent = err.disabled
                ? 'Chat supervision is turned off. Turn it on in Settings → Experimental → Chat supervision.'
                : `Could not load supervision: ${err.message}`;
        }
    }

    $('supRefresh').addEventListener('click', () => { if (!isDirty() || window.confirm('Discard unsaved changes?')) load(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) loadList(true); });
    pollTimer = setInterval(() => { if (!document.hidden && !content.hidden && !state.pauseFor) loadList(true); }, POLL_MS);
    window.addEventListener('pagehide', () => clearInterval(pollTimer));
    load();
})();
