/**
 * Secrets App controller — transport + events over CuttleSecrets (pure).
 * Never reads a secret value back: the API has no such route.
 */
(function () {
    'use strict';

    const M = window.CuttleSecrets;
    const API = '/api/secret-vault';
    const $ = (id) => document.getElementById(id);
    const notice = $('secretsNotice');
    const content = $('secretsContent');
    const els = {
        overview: $('secretsOverview'), list: $('secretsList'), grants: $('secretsGrants'),
        backends: $('secretsBackends'), usage: $('secretsUsage'), addForm: $('secretsAddForm'),
        addBackend: $('secretsAddBackend'), addValueRow: $('secretsAddValueRow'),
        addRefHint: $('secretsAddRefHint'), backendForm: $('secretsBackendForm'),
        preset: $('secretsBackendPreset'), presetHelp: $('secretsPresetHelp'),
        policy: $('secretsPolicy'), redact: $('secretsRedact'), askpassMax: $('secretsAskpassMax'),
    };
    let state = { status: null, secrets: [], grants: [] };

    function toast(message, kind) {
        if (typeof window.showToast === 'function') window.showToast(message, kind || 'info');
    }

    async function call(path, method, body) {
        const resp = await fetch(API + path, {
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
        notice.className = 'secrets-notice' + (kind ? ' is-' + kind : '');
        notice.innerHTML = html;
        content.hidden = true;
    }

    function presetById(id) {
        return ((state.status && state.status.presets) || []).find((p) => p.id === id) || null;
    }

    function syncAddForm() {
        const backends = (state.status && state.status.backends) || [];
        const current = els.addBackend.value;
        els.addBackend.innerHTML = backends.map((b) => `<option value="${M.esc(b.id)}"${b.default ? ' data-default="1"' : ''}>`
            + `${M.esc(b.label)}${b.available ? '' : ' (unavailable)'}</option>`).join('');
        const fallback = (backends.find((b) => b.default) || backends[0] || {}).id || '';
        els.addBackend.value = backends.some((b) => b.id === current) ? current : fallback;
        syncAddBackendHints();
    }

    function syncAddBackendHints() {
        const b = M.backendById(state.status && state.status.backends, els.addBackend.value) || {};
        const preset = presetById(b.options && b.options.preset);
        els.addValueRow.hidden = b.writable === false;
        els.addRefHint.textContent = preset && preset.ref_hint
            ? `(${preset.ref_hint})` : '(defaults to the name)';
    }

    function syncPresetForm() {
        const id = els.preset.value;
        const form = els.backendForm;
        const keyringOnly = form.querySelector('[data-keyring-only]');
        keyringOnly.hidden = id !== 'keyring';
        form.querySelectorAll('textarea, input[name=first_line]').forEach((el) => {
            el.closest('label').hidden = id === 'keyring';
        });
        const preset = presetById(id);
        els.presetHelp.textContent = id === 'keyring'
            ? 'Another Python keyring backend (for example an encrypted file keyring on headless machines).'
            : preset ? preset.help : 'Any CLI: the read command prints the value; write gets it on stdin.';
        if (preset) {
            form.get.value = M.argvText(preset.get);
            form.set.value = M.argvText(preset.set);
            form.delete.value = M.argvText(preset.delete);
            form.first_line.checked = !!preset.first_line;
            if (!form.backend_id.value) form.backend_id.value = preset.id;
            if (!form.label.value) form.label.value = preset.label;
        }
    }

    function render() {
        const st = state.status;
        notice.hidden = true;
        content.hidden = false;
        els.overview.innerHTML = M.overviewHtml(st, state.secrets);
        els.list.innerHTML = M.secretsListHtml(state.secrets, st.backends);
        els.grants.innerHTML = M.grantsHtml(state.grants);
        els.backends.innerHTML = M.backendsHtml(st.backends);
        els.usage.textContent = M.usageText();
        const cfg = st.config || {};
        els.policy.querySelectorAll('input').forEach((r) => { r.checked = r.value === cfg.approval; });
        els.redact.checked = cfg.redact !== false;
        els.askpassMax.value = cfg.askpass_max || 3;
        if (!els.preset.options.length) {
            els.preset.innerHTML = '<option value="">Custom command</option>'
                + (st.presets || []).map((p) => `<option value="${M.esc(p.id)}">${M.esc(p.label)}</option>`).join('')
                + '<option value="keyring">Another keyring backend</option>';
            syncPresetForm();
        }
        syncAddForm();
    }

    async function load() {
        try {
            const [status, secrets, grants] = await Promise.all([
                call('/status'), call('/secrets'), call('/grants')]);
            state = { status, secrets: secrets.secrets || [], grants: grants.grants || [] };
            render();
        } catch (err) {
            if (err.disabled) {
                showNotice('The Secret vault is turned off. Enable it in '
                    + '<a href="/settings_page.html?tab=experimental">Settings → Experimental</a>.');
            } else {
                showNotice(M.esc(err.message || 'Secrets could not load.'), 'error');
            }
        }
    }

    async function act(fn, success) {
        try {
            await fn();
            if (success) toast(success, 'success');
        } catch (err) {
            toast(err.message || 'Failed', 'error');
        }
        await load();
    }

    function openValueEditor(row, name) {
        if (row.querySelector('.secrets-row-edit')) return;
        const wrap = document.createElement('div');
        wrap.className = 'secrets-row-edit';
        wrap.innerHTML = '<input type="password" autocomplete="new-password" spellcheck="false" placeholder="New value">'
            + '<button type="button" class="secrets-primary">Save</button><button type="button">Cancel</button>';
        row.appendChild(wrap);
        const [input, save, cancel] = [wrap.querySelector('input'), ...wrap.querySelectorAll('button')];
        input.focus();
        cancel.addEventListener('click', () => wrap.remove());
        const submit = () => {
            const value = input.value;
            input.value = '';
            if (!value) return;
            act(() => call('/secrets/' + encodeURIComponent(name), 'PUT', { value }), `Saved ${name}`);
        };
        save.addEventListener('click', submit);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    }

    els.list.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-act]');
        const row = btn && btn.closest('[data-secret]');
        if (!row) return;
        const name = row.getAttribute('data-secret');
        const what = btn.getAttribute('data-act');
        if (what === 'set-value') openValueEditor(row, name);
        if (what === 'revoke-secret') {
            act(() => call('/grants?secret=' + encodeURIComponent(name), 'DELETE'), `Approvals for ${name} revoked`);
        }
        if (what === 'delete-secret' && window.confirm(`Delete ${name} and its stored value?`)) {
            act(() => call('/secrets/' + encodeURIComponent(name), 'DELETE'), `Deleted ${name}`);
        }
    });

    els.grants.addEventListener('click', (e) => {
        const row = e.target.closest('[data-grant-session]');
        if (!row || !e.target.closest('button[data-act="revoke-grant"]')) return;
        const q = '?session_id=' + encodeURIComponent(row.getAttribute('data-grant-session'))
            + '&secret=' + encodeURIComponent(row.getAttribute('data-grant-secret'));
        act(() => call('/grants' + q, 'DELETE'), 'Approval revoked');
    });

    els.backends.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-act]');
        const row = btn && btn.closest('[data-backend]');
        if (!row) return;
        const id = row.getAttribute('data-backend');
        if (btn.getAttribute('data-act') === 'default-backend') {
            act(() => call('/config', 'PUT', { default_backend: id }), `${id} is now the default`);
        } else if (window.confirm(`Remove backend ${id}? (Stored values are not touched.)`)) {
            act(() => call('/backends/' + encodeURIComponent(id), 'DELETE'), `Removed ${id}`);
        }
    });

    els.addBackend.addEventListener('change', syncAddBackendHints);
    els.preset.addEventListener('change', syncPresetForm);

    els.addForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const f = els.addForm;
        const name = f.secret_name.value.trim();
        if (!M.validName(name)) { toast('Use letters, digits, ".", "_" or "-" for the name.', 'error'); return; }
        const body = { backend: f.backend.value, description: f.description.value,
            project: f.project.value.trim(), ref: f.ref.value.trim() };
        if (!els.addValueRow.hidden && f.value.value) body.value = f.value.value;
        f.value.value = '';
        act(async () => {
            await call('/secrets/' + encodeURIComponent(name), 'PUT', body);
            f.reset();
        }, `Saved ${name}`);
    });

    els.backendForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const f = els.backendForm;
        const id = f.backend_id.value.trim();
        if (!M.validName(id)) { toast('Use letters, digits, ".", "_" or "-" for the id.', 'error'); return; }
        const body = M.backendPayload({
            preset: f.preset.value, label: f.label.value, get: f.get.value, set: f.set.value,
            delete: f.delete.value, first_line: f.first_line.checked, keyring_backend: f.keyring_backend.value.trim(),
        });
        if (f.preset.value && f.preset.value !== 'keyring') body.preset = f.preset.value;
        act(async () => {
            await call('/backends/' + encodeURIComponent(id), 'PUT', body);
            f.reset();
            syncPresetForm();
        }, `Added ${id}`);
    });

    els.policy.addEventListener('change', (e) => {
        if (e.target.name === 'approval') act(() => call('/config', 'PUT', { approval: e.target.value }), 'Approval policy saved');
    });
    els.redact.addEventListener('change', () => act(() => call('/config', 'PUT', { redact: els.redact.checked }), 'Saved'));
    els.askpassMax.addEventListener('change', () => act(() => call('/config', 'PUT', { askpass_max: els.askpassMax.value }), 'Saved'));
    $('secretsRefresh').addEventListener('click', load);

    load();
})();
