/* Projects App → Locations: the project's default worktree folder.
 *
 * The host owns the setting (settings.json `worktrees.project_roots`, served by
 * GET/POST /api/settings/worktrees — the same store Settings → Agents →
 * Worktrees edits). This module only reads and patches one project's entry.
 * An empty folder means "Use Cuttle default" (the global worktree folder).
 *
 *   CuttleProjectsWorktreeFolder.view(data, projectId)   pure: what to show
 *   CuttleProjectsWorktreeFolder.mount(el, {projectId, canEdit, notice, onDirty})
 */
(function (root) {
    'use strict';

    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    function folderText(folder) {
        if (!folder || !folder.usable || !folder.path) return 'cannot be used on this machine';
        return folder.path + (folder.free_gb == null ? '' : ` · ${folder.free_gb} GB free`);
    }

    /** What the section shows for one project. Pure. */
    function view(data, projectId) {
        if (!data || !data.available) return { available: false };
        const row = (data.projects || []).find(p => String(p.id) === String(projectId)) || null;
        const override = row ? String(row.root || '') : '';
        return {
            available: true,
            override,
            usesDefault: !override,
            defaultText: folderText(data.effective),
            effectiveText: folderText(row ? row.effective : data.effective),
            effectiveUsable: !!(row ? row.effective : data.effective)?.usable,
        };
    }

    /** POST body that sets (or, with an empty folder, clears) one project's folder. Pure. */
    function patchFor(projectId, folder) {
        return { project_roots: { [String(projectId)]: String(folder || '').trim() } };
    }

    function html(v, canEdit) {
        if (!v.available) return '';
        const off = canEdit ? '' : 'disabled';
        const status = v.usesDefault
            ? `Using the Cuttle default: <code>${esc(v.defaultText)}</code>`
            : `Using this project's folder: <code>${esc(v.effectiveText)}</code>`;
        return `<section class="panel" id="worktree-folder-panel"><h3>Default worktree folder</h3>
<p class="muted">Where agents create git worktrees for this project. Leave empty to use the Cuttle default, set in Settings → Agents → Worktrees.</p>
<form id="worktree-folder-form"><fieldset ${off}><div class="branch-form-row"><label>Folder<input name="worktree_root" value="${esc(v.override)}" placeholder="Use Cuttle default" autocomplete="off" spellcheck="false"></label>
<span class="worktree-folder-actions"><button type="submit" class="primary">Save folder</button><button type="button" data-worktree-default ${v.usesDefault ? 'disabled' : ''}>Use Cuttle default</button></span></div></fieldset></form>
<p class="branch-help ${v.effectiveUsable ? '' : 'error'}" data-worktree-status>${status}</p></section>`;
    }

    function mount(el, opts) {
        const o = opts || {};
        let busy = false;
        const notice = typeof o.notice === 'function' ? o.notice : () => {};
        async function request(body) {
            const init = body === undefined ? { cache: 'no-store' }
                : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
            const response = await fetch('/api/settings/worktrees', init);
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) throw new Error(payload.error || 'Could not save the worktree folder.');
            return payload;
        }
        function render(data) { el.innerHTML = html(view(data, o.projectId), !!o.canEdit); }
        async function save(folder) {
            if (busy) return; busy = true;
            const fields = el.querySelector('fieldset'); if (fields) fields.disabled = true;
            try {
                render(await request(patchFor(o.projectId, folder)));
                if (typeof o.onDirty === 'function') o.onDirty(false);
                notice(folder ? 'Worktree folder saved.' : 'This project now uses the Cuttle default worktree folder.');
            } catch (error) {
                notice(error.message, true);
                if (fields) fields.disabled = !o.canEdit;
            } finally { busy = false; }
        }
        el.addEventListener('submit', event => {
            if (event.target.id !== 'worktree-folder-form') return;
            event.preventDefault(); event.stopPropagation();
            save(new FormData(event.target).get('worktree_root'));
        });
        el.addEventListener('click', event => {
            if (!event.target.closest('[data-worktree-default]')) return;
            event.preventDefault(); event.stopPropagation(); save('');
        });
        el.addEventListener('input', event => {
            if (event.target.name === 'worktree_root' && typeof o.onDirty === 'function') o.onDirty(true);
        });
        request().then(render).catch(() => { el.innerHTML = ''; });
    }

    const api = { view, patchFor, folderText, mount };
    root.CuttleProjectsWorktreeFolder = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
