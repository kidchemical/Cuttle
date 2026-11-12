/* Settings → Agents → Worktrees (api.worktrees.space; flag `worktrees`).
   `CuttleSettingsWorktrees.mount(container)` reads /api/settings/worktrees and
   builds the form: the folder new worktrees go in, a folder per project, and
   the free space kept on the drive. The section stays hidden while the flag is
   off or the read fails. */
(function (root) {
    'use strict';

    let container = null;
    let generation = 0;

    function el(tag, text, cls) {
        const n = document.createElement(tag);
        if (text) n.textContent = text;
        if (cls) n.className = cls;
        return n;
    }

    function info(label, text) {
        const b = el('button', 'i', 'cuttle-info');
        b.type = 'button';
        b.setAttribute('data-tooltip', text);
        b.setAttribute('aria-label', 'About ' + label);
        return b;
    }

    /** "12.3 GB free" for a folder, or why it cannot be used. Pure. */
    function folderNote(folder) {
        if (!folder || !folder.usable) return 'This folder cannot be used on this machine.';
        const free = folder.free_gb == null ? '' : ' · ' + folder.free_gb + ' GB free';
        return folder.path + free;
    }

    /** Settings patch from the form's plain values; only changed projects are sent. Pure. */
    function patchFrom(values, data) {
        const out = { root: String(values.root || '').trim(), min_free_gb: Number(values.min_free_gb) };
        const changed = {};
        for (const p of (data && data.projects) || []) {
            const next = String((values.projects || {})[p.id] || '').trim();
            if (next !== String(p.root || '')) changed[p.id] = next;
        }
        if (Object.keys(changed).length) out.project_roots = changed;
        return out;
    }

    function row(title, help) {
        const item = el('div', '', 'setting-item');
        const label = el('div', '', 'setting-label');
        label.append(el('strong', title));
        if (help) label.append(info(title, help));
        const control = el('div', '', 'setting-control');
        item.append(label, control);
        return { item, control };
    }

    function textInput(label, value, placeholder) {
        const input = el('input', '', 'setting-input');
        input.type = 'text';
        input.value = value || '';
        input.placeholder = placeholder || '';
        input.spellcheck = false;
        input.setAttribute('aria-label', label);
        input.style.width = 'min(32em, 100%)';
        return input;
    }

    function build(data) {
        const form = el('form', '', 'worktrees-settings');

        const folder = row('Cuttle default worktree folder', 'Where agents create git worktrees for every project that has no folder of its own. Each one is a full copy of the project, so pick a drive with room. Empty uses the built-in folder in the Cuttle home.');
        const rootInput = textInput('Worktree folder', data.root, data.default_root);
        const rootNote = el('div', folderNote(data.effective), 'setting-description');
        folder.control.append(rootInput, rootNote);

        const reserve = row('Free space to keep', 'Cuttle refuses to create a worktree that would leave the drive with less than this. 0 turns the check off.');
        const lim = (data.limits && data.limits.min_free_gb) || [0, 10000];
        const reserveInput = el('input', '', 'setting-input');
        reserveInput.type = 'number';
        reserveInput.min = String(lim[0]);
        reserveInput.max = String(lim[1]);
        reserveInput.step = '1';
        reserveInput.inputMode = 'decimal';
        reserveInput.value = String(data.min_free_gb);
        reserveInput.setAttribute('aria-label', 'Free space to keep in GB');
        reserveInput.style.width = '6em';
        const reserveWrap = el('span', '', 'setting-control-inline');
        reserveWrap.append(reserveInput, el('span', ' GB', 'setting-description'));
        reserve.control.append(reserveWrap);

        form.append(folder.item, reserve.item);

        const projectInputs = {};
        if ((data.projects || []).length) {
            const head = row('Per project', 'A project with its own folder ignores the default above. Leave empty to use the Cuttle default. Also editable in Projects → Locations.');
            form.append(head.item);
            for (const p of data.projects) {
                const r = row(p.name);
                const input = textInput('Worktree folder for ' + p.name, p.root, 'Use Cuttle default');
                r.control.append(input, el('div', folderNote(p.effective), 'setting-description'));
                projectInputs[p.id] = input;
                form.append(r.item);
            }
        }

        const actions = el('div', '', 'provider-picker');
        const save = el('button', 'Save worktree settings', 'control-button btn-start');
        save.type = 'submit';
        actions.append(save);
        const status = el('div', '', 'setting-description');
        status.setAttribute('role', 'status');
        form.append(actions, status);

        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const values = { root: rootInput.value, min_free_gb: reserveInput.value, projects: {} };
            for (const id of Object.keys(projectInputs)) values.projects[id] = projectInputs[id].value;
            save.disabled = true;
            status.textContent = 'Saving…';
            try {
                const r = await fetch('/api/settings/worktrees', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(patchFrom(values, data)),
                });
                const d = await r.json();
                if (!d.success) throw new Error(d.error || 'Save failed');
                container.replaceChildren(build(d));
                const done = container.querySelector('[role="status"]');
                if (done) done.textContent = 'Saved. Existing worktrees stay where they are.';
            } catch (e) {
                status.textContent = 'Could not save: ' + (e && e.message ? e.message : e);
            } finally {
                save.disabled = false;
            }
        });
        return form;
    }

    async function mount(target, refresh) {
        if (!target || (target.dataset.mounted && !refresh)) return;
        container = target;
        const section = document.getElementById('worktreesSettings');
        const mine = ++generation;
        const hide = () => {
            if (section) section.hidden = true;
            target.replaceChildren();
            delete target.dataset.mounted;
        };
        try {
            const r = await fetch('/api/settings/worktrees', { cache: 'no-store' });
            const d = await r.json();
            if (mine !== generation) return;
            if (!d || d.success !== true || d.available !== true) { hide(); return; }
            if (section) section.hidden = false;
            if (target.dataset.mounted) return; // keep an unsaved draft
            target.dataset.mounted = 'true';
            target.replaceChildren(build(d));
        } catch (_) {
            if (mine === generation) hide();
        }
    }

    function refresh() { return mount(container || document.getElementById('worktreesEditor'), true); }

    const api = { mount, refresh, patchFrom, folderNote };
    root.CuttleSettingsWorktrees = api;
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
