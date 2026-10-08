/* Projects App. Registry/resolution belongs to the host; this page edits ordered paths. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const statuses = {available:'Available', missing:'Missing', different_os:'Different OS', invalid:'Invalid path', not_directory:'Not a folder', permission_denied:'Permission denied', unreachable:'Unreachable'};
    let projects = [], selected = null, overview = null, activity = null, tab = 'overview', paths = [], checks = [], canEdit = false, dirty = false, epoch = 0, dragged = null, removing = null, busy = false;
    const api = async (url, body, method = 'POST') => {
        const response = await fetch(url, body === undefined ? {} : {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
        const payload = await response.json();
        if (!response.ok || !payload.success) throw new Error(payload.error || 'Request failed.');
        return payload;
    };
    function notice(message, error = false) { $('notice').textContent = message; $('notice').className = error ? 'error' : ''; }
    function date(value) { if (!value) return 'No activity yet'; const d = new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z'); return Number.isNaN(d.getTime()) ? value : d.toLocaleString(); }
    function navigate(page) { if (window.parent !== window) window.parent.postMessage({type:'cuttle-navigate', page}, location.origin); else location.href = page; }
    function renderList() {
        const search = $('search').value.trim().toLowerCase();
        const visible = projects.filter(p => ($('show-archived').checked || !p.archived) && `${p.name} ${p.description || ''} ${(p.tags || []).join(' ')}`.toLowerCase().includes(search));
        $('project-list').innerHTML = visible.map(p => `<button class="project-card ${selected?.id === p.id ? 'active' : ''}" data-project="${p.id}" aria-pressed="${selected?.id === p.id}"><span class="project-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg></span><span class="project-label"><strong>${esc(p.name)}</strong><small>${p.archived ? 'Archived' : p.available ? 'Available on host' : 'Needs a valid path'}</small><small class="project-tags">${esc((p.tags || []).join(' · '))}</small></span></button>`).join('') || '<p class="empty">No matching projects.</p>';
    }
    async function load(preferredId = selected?.id) {
        const response = await api('/api/projects?include_archived=1');
        projects = response.data;
        renderList();
        const choice = projects.find(p => p.id === preferredId) || projects.find(p => !p.archived) || projects[0];
        if (choice) await select(choice.id); else { selected = null; $('detail').innerHTML = '<div class="empty">Add your first project to get started.</div>'; notice('No projects registered.'); }
    }
    async function select(id) {
        const token = ++epoch;
        selected = projects.find(p => p.id === id);
        overview = activity = null; dirty = false;
        paths = [...(selected.paths || [selected.path])]; checks = selected.path_checks || [];
        renderList(); renderDetail(); notice('Loading project details…');
        try {
            const [info, feed] = await Promise.all([api(`/api/projects/${id}/overview`), api(`/api/projects/${id}/activity`)]);
            if (token !== epoch) return;
            overview = info.data; activity = feed.data; selected = overview.project;
            renderDetail(); notice(canEdit ? 'Changes apply to future turns. Running turns keep their current folder.' : 'Read-only access. Project changes require an owner account.');
        } catch (error) { if (token === epoch) notice(error.message, true); }
    }
    function renderDetail() {
        if (!selected) return;
        const p = selected;
        $('detail').innerHTML = `<header class="detail-header"><div><h2>${esc(p.name)}</h2><span class="badge ${p.available && !p.archived ? 'good' : 'bad'}">${p.archived ? 'Archived' : p.available ? 'Available on host' : 'Unavailable on host'}</span></div><div class="actions"><button data-action="chat" ${!p.available || p.archived || busy ? 'disabled' : ''}>New chat</button><button data-action="git" ${!p.available ? 'disabled' : ''}>Open Git</button></div></header><nav class="tabs" aria-label="Project sections">${[['overview','Overview'],['locations','Locations'],['configuration','Configuration'],['activity','Activity'],['repository','Repository']].map(([key,title]) => `<button data-tab="${key}" aria-selected="${tab === key}" ${busy || !overview || !activity ? 'disabled' : ''}>${title}</button>`).join('')}</nav><div id="tab-content"></div>`;
        const tabs = $('detail').querySelector('.tabs');
        const active = tabs.querySelector('[aria-selected="true"]');
        if (active) {
            const bounds = tabs.getBoundingClientRect(), item = active.getBoundingClientRect();
            if (item.right > bounds.right) tabs.scrollLeft += item.right - bounds.right;
            else if (item.left < bounds.left) tabs.scrollLeft -= bounds.left - item.left;
        }
        if (overview && activity) renderTab();
        else $('tab-content').innerHTML = '<p class="muted">Loading details…</p>';
    }
    function renderTab() {
        const p = selected, repo = overview?.repository;
        if (tab === 'overview') {
            $('tab-content').innerHTML = `<div class="metrics"><div class="metric"><small>Chats</small><strong>${activity?.stats.chats ?? '—'}</strong></div><div class="metric"><small>Messages</small><strong>${activity?.stats.messages ?? '—'}</strong></div><div class="metric"><small>Local branches</small><strong>${repo?.branch_count ?? '—'}</strong></div></div><p class="muted">Last activity: ${esc(date(activity?.stats.last_activity))}</p><section class="panel"><h3>Project folder</h3><code class="path-text">${esc(p.resolved_path || 'No accessible folder. Open Locations to repair the path list.')}</code>${repo?.available ? `<p>Branch: <strong>${esc(repo.branch)}</strong></p>` : ''}</section><form id="metadata-form" class="panel"><h3>Project details</h3><fieldset ${!canEdit || busy ? 'disabled' : ''}><label>Name<input name="name" value="${esc(p.name)}" required maxlength="120"></label><label>Tags <small>comma separated</small><input name="tags" value="${esc((p.tags || []).join(', '))}"></label><label>Description<textarea name="description" rows="3" maxlength="4000">${esc(p.description || '')}</textarea></label><label>Repository URL<input name="repo_url" value="${esc(p.config?.repo_url || '')}" placeholder="https://git.example.com/you/project"></label><button type="submit" class="primary">Save details</button></fieldset></form>${canEdit ? `<section class="panel"><h3>Manage project</h3><div class="actions"><button data-action="archive" ${busy ? 'disabled' : ''}>${p.archived ? 'Unarchive' : 'Archive'} project</button><button data-action="remove" class="danger" ${busy ? 'disabled' : ''}>Remove from Cuttle…</button></div><p class="muted">Archiving hides the project from project pickers. Files and chats are kept.</p></section>` : ''}`;
        } else if (tab === 'locations') {
            $('tab-content').innerHTML = `<section class="panel"><h3>Ordered project paths</h3><p class="muted">Cuttle checks this list on the host and uses the first accessible folder. Drag a grip or use the arrows to change priority.</p><div id="paths"></div><div class="actions"><button data-action="add-path" ${!canEdit || busy ? 'disabled' : ''}>Add path</button><button data-action="test-paths" ${!canEdit || busy ? 'disabled' : ''}>Test on host</button><button class="primary" data-action="save-paths" ${!canEdit || busy ? 'disabled' : ''}>Save paths</button></div><p id="path-save-state" class="muted">${dirty ? 'Unsaved changes' : 'Saved order'}</p></section>`;
            renderPaths();
        } else if (tab === 'configuration') {
            $('tab-content').innerHTML = `<p class="muted">Configuration inventory by source. Personal replacements and appended rule/document overrides use Cuttle’s existing resolution rules. Shared-layer participation is controlled by the project’s GLOBAL.ini. File editing is available through project files.</p>${(overview?.configuration || []).map(layer => `<section class="panel"><h3>${esc(layer.label)}</h3><code class="path-text">${esc(layer.path)}</code>${layer.files.length ? `<ul class="config-files">${layer.files.map(file => `<li><code>${esc(file)}</code></li>`).join('')}</ul>` : `<p class="muted">${layer.present ? 'No configuration files.' : 'Layer unavailable or not initialized.'}</p>`}</section>`).join('')}`;
        } else if (tab === 'activity') renderFeed();
        else if (tab === 'repository') {
            $('tab-content').innerHTML = repo?.available ? `<section class="panel"><div class="panel-heading"><h3>Repository</h3><button data-action="git">Open Git</button></div><dl class="repo-details"><dt>Current branch</dt><dd><span class="branch-pill">${esc(repo.branch)}</span></dd><dt>Origin</dt><dd><code>${esc(repo.remote || 'No origin configured')}</code></dd><dt>Folder</dt><dd><code>${esc(repo.root)}</code></dd></dl></section><section class="panel"><h3>Working branch</h3><form id="branch-form"><fieldset ${!canEdit || busy ? 'disabled' : ''}><div class="branch-form-row"><label>Default working branch<input name="default_branch" list="local-branches" value="${esc(p.default_branch || '')}" maxlength="240" placeholder="Use current branch"></label><button type="submit" class="primary">Save default branch</button></div><datalist id="local-branches">${repo.branches.map(branch => `<option value="${esc(branch)}"></option>`).join('')}</datalist></fieldset></form><p class="branch-help">The branch agents use for new work. Saving leaves your checkout unchanged.</p><div class="branch-checkout"><p>Saved working branch<strong>${esc(p.default_branch || 'Use current branch')}</strong></p><button data-action="use-default-branch" ${!canEdit || busy || !p.default_branch ? 'disabled' : ''}>Switch / create default branch</button></div><details class="branch-note"><summary>How branch switching works</summary><p>Commit or stash pending changes before switching. A new branch starts at the current commit, or tracks origin if it exists there. This switches the shared checkout, so wait for running turns to finish. The remote default branch and protection rules stay unchanged.</p></details></section><section class="panel"><div class="panel-heading"><h3>Local branches</h3><small>${repo.branch_count} total</small></div><ul class="branch-list">${repo.branches.map(branch => `<li class="branch-pill">${esc(branch)}</li>`).join('')}</ul></section>` : `<section class="panel"><h3>Repository</h3><p class="muted">${esc(repo?.error || 'No Git repository available at the selected location.')}</p></section>`;
            if (overview?.changes?.length) $('tab-content').insertAdjacentHTML('beforeend', `<section class="panel"><h3>Recent configuration changes</h3>${overview.changes.map(change => `<p>${esc(date(change.timestamp))} · ${esc(change.action)}</p>`).join('')}</section>`);
        }
    }
    function renderPaths() {
        const root = $('paths'); if (!root) return;
        root.innerHTML = paths.map((path, index) => {
            const check = checks.find(c => c.path === path);
            return `<div class="path-row" data-index="${index}"><button class="grip" draggable="${canEdit && !busy}" aria-label="Drag path ${index+1} to reorder" ${!canEdit || busy ? 'disabled' : ''}>⠿</button><div><input data-path="${index}" aria-label="Project path ${index+1}" value="${esc(path)}" ${!canEdit || busy ? 'disabled' : ''}><small>${index+1}. ${check ? esc(statuses[check.status] || check.status) + (check.selected ? (dirty ? ' · First available' : ' · Selected') : '') : 'Not tested'}</small></div><div class="actions"><button data-move="${index}" data-direction="-1" aria-label="Move path ${index+1} up" ${index === 0 || !canEdit || busy ? 'disabled' : ''}>↑</button><button data-move="${index}" data-direction="1" aria-label="Move path ${index+1} down" ${index === paths.length-1 || !canEdit || busy ? 'disabled' : ''}>↓</button><button data-delete="${index}" aria-label="Remove path ${index+1}" ${paths.length === 1 || !canEdit || busy ? 'disabled' : ''}>×</button></div></div>`;
        }).join('');
        $('path-save-state').textContent = dirty ? 'Unsaved changes' : 'Saved order';
    }
    function move(from, to) { if (busy || from === to || to < 0 || to >= paths.length) return; paths.splice(to, 0, paths.splice(from, 1)[0]); checks = []; dirty = true; renderPaths(); }
    function renderFeed() {
        const feed = activity?.messages || [];
        $('tab-content').innerHTML = `<section class="panel"><h3>Recent messages</h3><p class="muted">Your conversations in this project, newest first.</p>${feed.map(message => `<article class="feed-item"><header><strong>${esc(message.role === 'assistant' ? 'Assistant' : 'You')}</strong><span class="muted">${esc(date(message.timestamp))}</span><button data-chat="${message.session_id}">${esc(message.session_name || 'Chat')} · CH-${String(message.session_id).padStart(6, '0')}</button></header><p>${esc(message.content)}</p></article>`).join('') || '<p class="muted">No messages yet.</p>'}${activity?.next_before_id ? '<button data-action="more-activity">Load older messages</button>' : ''}</section>`;
    }
    async function save(values) { const id = selected.id; await api(`/api/projects/${id}`, values, 'PUT'); dirty = false; await load(id); notice('Project saved.'); }
    function tags(value) { return value.split(',').map(t => t.trim()).filter(Boolean); }
    $('search').addEventListener('input', renderList);
    $('show-archived').addEventListener('change', renderList);
    $('project-list').addEventListener('click', event => {
        const button = event.target.closest('[data-project]');
        if (!button || busy) return;
        if (dirty && !window.confirm('Discard unsaved project changes?')) return;
        select(Number(button.dataset.project));
    });
    $('detail').addEventListener('input', event => {
        if (event.target.dataset.path !== undefined) { paths[Number(event.target.dataset.path)] = event.target.value; event.target.nextElementSibling.textContent = `${Number(event.target.dataset.path)+1}. Not tested`; dirty = true; $('path-save-state').textContent = 'Unsaved changes'; }
        if (event.target.closest('#metadata-form, #branch-form')) dirty = true;
    });
    $('detail').addEventListener('submit', async event => {
        if (!['metadata-form', 'branch-form'].includes(event.target.id)) return;
        event.preventDefault(); if (busy) return; busy = true;
        const form = new FormData(event.target);
        const fields = event.target.querySelector('fieldset'); fields.disabled = true;
        let saved = false;
        try {
            const values = event.target.id === 'branch-form' ? {default_branch:form.get('default_branch')} : {name:form.get('name'), description:form.get('description'), tags:tags(form.get('tags')), repo_url:form.get('repo_url')};
            await save(values); saved = true;
        }
        catch (error) { notice(error.message, true); }
        finally { busy = false; if (saved) renderDetail(); else fields.disabled = !canEdit; }
    });
    $('detail').addEventListener('click', async event => {
        const target = event.target.closest('button'); if (!target || target.disabled || busy) return;
        if (target.dataset.tab) { if (dirty && !window.confirm('Discard unsaved project changes?')) return; dirty = false; paths = [...selected.paths]; checks = selected.path_checks; tab = target.dataset.tab; renderDetail(); return; }
        if (target.dataset.move !== undefined) { move(Number(target.dataset.move), Number(target.dataset.move) + Number(target.dataset.direction)); return; }
        if (target.dataset.delete !== undefined) { paths.splice(Number(target.dataset.delete), 1); dirty = true; checks = []; renderPaths(); return; }
        if (target.dataset.chat) { navigate(`/chat_page.html?chat=${encodeURIComponent(target.dataset.chat)}`); return; }
        const action = target.dataset.action;
        if (!action) return;
        if (action === 'add-path') { paths.push(''); dirty = true; renderPaths(); $('paths').querySelector('.path-row:last-child input').focus(); return; }
        if (action === 'git') { try { localStorage.setItem('cuttleGitGraphProject', selected.resolved_path); } catch (_) {} navigate('/git_graph_page.html?project_id=' + selected.id); return; }
        if (action === 'remove') { removing = {...selected}; $('remove-description').textContent = `Unregister “${removing.name}” from Cuttle?`; $('confirm-name').value = ''; $('confirm-remove').disabled = true; $('remove-error').textContent = ''; $('remove-dialog').showModal(); return; }
        if (action === 'use-default-branch' && dirty) { notice('Save or discard the branch preference before switching.', true); return; }
        busy = true; target.disabled = true;
        try {
            if (action === 'test-paths') { checks = (await api('/api/projects/paths/check', {paths})).data.path_checks; renderPaths(); notice('Paths checked on the Cuttle host.'); }
            if (action === 'use-default-branch') { const result = await api(`/api/projects/${selected.id}/branch`, {}); await load(selected.id); notice(result.data.message); }
            if (action === 'save-paths') await save({paths});
            if (action === 'archive') await save({archived:!selected.archived});
            if (action === 'chat') { const response = await api(`/api/projects/${selected.id}/chat`, {}); navigate(`/chat_page.html?chat=${response.session_id}`); }
            if (action === 'more-activity') { const response = await api(`/api/projects/${selected.id}/activity?before_id=${activity.next_before_id}`); activity.messages.push(...response.data.messages); activity.next_before_id = response.data.next_before_id; renderFeed(); }
        } catch (error) { notice(error.message, true); }
        finally { busy = false; if (action === 'test-paths') renderPaths(); else renderDetail(); }
    });
    $('detail').addEventListener('dragstart', event => {
        if (!event.target.classList.contains('grip') || !canEdit || busy) { event.preventDefault(); return; }
        dragged = Number(event.target.closest('[data-index]').dataset.index);
        event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(dragged));
    });
    $('detail').addEventListener('dragover', event => { if (dragged === null) return; const row = event.target.closest('[data-index]'); if (row) { event.preventDefault(); row.classList.add('drag-over'); } });
    $('detail').addEventListener('dragleave', event => event.target.closest('[data-index]')?.classList.remove('drag-over'));
    $('detail').addEventListener('drop', event => { const row = event.target.closest('[data-index]'); if (row && dragged !== null) { event.preventDefault(); move(dragged, Number(row.dataset.index)); } dragged = null; });
    $('detail').addEventListener('dragend', () => { dragged = null; document.querySelectorAll('.drag-over').forEach(row => row.classList.remove('drag-over')); });
    $('refresh').addEventListener('click', () => { if (!busy && (!dirty || window.confirm('Discard unsaved project changes?'))) load().catch(error => notice(error.message, true)); });
    $('add-project').addEventListener('click', () => { $('register-form').reset(); $('register-error').textContent = ''; $('register-dialog').showModal(); });
    document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
    $('register-form').addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return; busy = true;
        const form = new FormData(event.target), button = event.target.querySelector('[type="submit"]'); button.disabled = true;
        try {
            const result = await api('/api/projects/register', {name:form.get('name'), path:form.get('path'), description:form.get('description'), tags:tags(form.get('tags')), repo_url:form.get('repo_url')});
            $('register-dialog').close(); await load(result.project_id); notice('Project registered.');
        } catch (error) { $('register-error').textContent = error.message; }
        finally { busy = false; button.disabled = false; renderDetail(); }
    });
    $('confirm-name').addEventListener('input', () => { $('confirm-remove').disabled = busy || $('confirm-name').value !== removing?.name; });
    $('remove-form').addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return; busy = true; $('confirm-remove').disabled = true;
        try { await api(`/api/projects/${removing.id}/remove`, {confirm_name:$('confirm-name').value}); $('remove-dialog').close(); selected = null; await load(); notice('Project removed from Cuttle. Files and chats were kept.'); }
        catch (error) { $('remove-error').textContent = error.message; }
        finally { busy = false; $('confirm-remove').disabled = $('confirm-name').value !== removing?.name; renderDetail(); }
    });
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    async function init() {
        try { canEdit = (await api('/api/projects/app/access')).can_edit; $('add-project').disabled = !canEdit; await load(Number(new URLSearchParams(location.search).get('project_id')) || undefined); }
        catch (error) { notice(error.message, true); }
    }
    init();
})();
