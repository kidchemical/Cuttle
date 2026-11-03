/**
 * Tasks App controller — transport + DOM over CuttleTasksModel (pure).
 *
 * Owner of the data is api.gizmos.tasks (auth-owned rows, audited writes);
 * this page reads GET /api/gizmos/tasks?all=1 and writes through the same
 * /api/gizmos/tasks routes the composer strip uses, so agent-managed lists
 * stay agent-managed until the user opens them for shared edits.
 *
 * Title, description and the task tree are a draft saved with the save bar
 * (guarded by expected_revision). Status, edit mode, scope, links and — while
 * the tree has no unsaved edits — checkbox toggles apply immediately.
 */
(function () {
    'use strict';

    const M = window.CuttleTasksModel;
    const $ = (id) => document.getElementById(id);
    const notice = $('tasksNotice');
    const content = $('tasksContent');
    const editor = $('tasksEditor');
    const listEl = $('tasksList');
    const search = $('tasksSearch');
    const savebar = $('tasksSavebar');
    const POLL_MS = 8000;

    const state = {
        lists: [], chats: [], revision: -1,
        status: 'active', query: '',
        selected: '', mode: '', tab: 'tasks',
        base: null, draft: null,
        hideDone: false, conflict: false, remote: false, busy: false,
        history: null, historyReads: false, related: null,
        focusId: '',
    };
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
        if (!resp.ok || data.success === false) {
            const err = new Error(data.error || `Request failed (${resp.status})`);
            err.code = data.code || '';
            err.status = resp.status;
            throw err;
        }
        return data;
    }

    function navigate(page) {
        if (window.parent !== window) window.parent.postMessage({ type: 'cuttle-navigate', page }, location.origin);
        else location.href = page;
    }

    function openChat(id) { if (id) navigate(`/chat_page.html?chat=${encodeURIComponent(id)}`); }

    function find(id) { return state.lists.find((l) => l.id === id) || null; }
    function chat(id) { return state.chats.find((c) => String(c.id) === String(id)) || null; }

    function syncUrl() {
        try {
            const url = new URL(location.href);
            url.searchParams.delete('chat');
            if (state.selected && state.mode === 'edit') url.searchParams.set('list', state.selected);
            else url.searchParams.delete('list');
            history.replaceState(null, '', url.pathname + url.search);
        } catch (_) {}
    }

    // ── Progress ring ───────────────────────────────────────────────────

    function ringHtml(c, size, archived) {
        const r = size / 2 - 3;
        const len = 2 * Math.PI * r;
        const full = c.total > 0 && c.done >= c.total;
        const off = len * (1 - (c.total ? c.done / c.total : 0));
        const cls = `tasks-ring${full ? ' is-full' : ''}${archived ? ' is-archived' : ''}`;
        const label = size > 40 ? `<span class="tasks-ring-label"><strong>${c.done}</strong>/${c.total}</span>`
            : (full ? '<span class="tasks-ring-label tasks-ring-check" aria-hidden="true">✓</span>' : '');
        return `<span class="${cls}" style="--size:${size}px" role="img" aria-label="${c.done} of ${c.total} done">
            <svg viewBox="0 0 ${size} ${size}" aria-hidden="true">
                <circle class="tasks-ring-track" cx="${size / 2}" cy="${size / 2}" r="${r}"/>
                <circle class="tasks-ring-fill" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-dasharray="${len.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"/>
            </svg>${label}</span>`;
    }

    // ── Sidebar ─────────────────────────────────────────────────────────

    function cardHtml(l) {
        const c = M.count(M.items(l));
        const active = state.mode === 'edit' && state.selected === l.id;
        const archived = l.status === 'archived';
        return `<button type="button" class="orch-card tasks-card${active ? ' is-active' : ''}${archived ? ' is-archived' : ''}" data-open="${M.esc(l.id)}">
            ${ringHtml(c, 34, archived)}
            <span class="orch-card-main">
                <span class="orch-card-title">${M.esc(l.title || 'Tasks')}</span>
                <span class="orch-card-sub">${l.scope === 'project' ? '<span class="tasks-scope-dot is-project" aria-hidden="true"></span>' : ''}${(l.linked_chats || []).length ? '<span class="tasks-scope-dot is-linked" aria-hidden="true"></span>' : ''}<span class="tasks-card-reach">${M.esc(M.reachLabel(l))}</span></span>
            </span>
            <span class="tasks-card-meta">
                <span class="tasks-card-count">${c.done}/${c.total}</span>
                <span class="tasks-card-time">${M.esc(M.relativeTime(l.updated_at))}</span>
            </span>
        </button>`;
    }

    function renderList() {
        const counts = M.statusCounts(state.lists);
        content.querySelectorAll('.tasks-filter [data-status]').forEach((b) => {
            b.setAttribute('aria-selected', String(b.dataset.status === state.status));
            b.querySelector('[data-count]').textContent = counts[b.dataset.status] ? String(counts[b.dataset.status]) : '';
        });
        const shown = M.filterLists(state.lists, { status: state.status, query: state.query });
        if (shown.length) { listEl.innerHTML = shown.map(cardHtml).join(''); return; }
        const q = state.query.trim();
        listEl.innerHTML = `<p class="orch-empty-line">${q ? 'No lists match.'
            : state.status === 'archived' ? 'Nothing archived. Finished lists archive themselves.'
            : state.status === 'active' ? 'No active lists.' : 'No Tasks lists yet.'}</p>`;
    }

    // ── Editor: shared pieces ───────────────────────────────────────────

    function editable() {
        if (state.mode === 'new') return true;
        const l = find(state.selected);
        return !!l && l.edit_mode === 'shared';
    }

    function dirty() {
        const d = state.draft;
        const b = state.base;
        if (!d || !b) return false;
        if (state.mode === 'new') return !!(d.title.trim() || d.description.trim() || M.prune(d.items).length);
        return d.title !== b.title || d.description !== b.description || !M.sameItems(d.items, b.items);
    }

    function itemsDirty() {
        return !!(state.draft && state.base) && !M.sameItems(state.draft.items, state.base.items);
    }

    function itemHtml(n, depth, canEdit, siblingCount, index) {
        if (state.hideDone && n.done && state.mode === 'edit') return '';
        const kids = (n.children || []).map((k, i) => itemHtml(k, depth + 1, canEdit, n.children.length, i)).join('');
        const actions = canEdit ? `<span class="tasks-row-actions">
                <button type="button" class="orch-icon-btn" data-act="up" title="Move up (Alt+↑)" aria-label="Move up" ${index === 0 ? 'disabled' : ''}>↑</button>
                <button type="button" class="orch-icon-btn" data-act="down" title="Move down (Alt+↓)" aria-label="Move down" ${index >= siblingCount - 1 ? 'disabled' : ''}>↓</button>
                <button type="button" class="orch-icon-btn" data-act="outdent" title="Outdent (Shift+Tab)" aria-label="Outdent" ${depth === 0 ? 'disabled' : ''}>⇤</button>
                <button type="button" class="orch-icon-btn" data-act="indent" title="Make sub-task (Tab)" aria-label="Indent" ${index === 0 ? 'disabled' : ''}>⇥</button>
                <button type="button" class="orch-icon-btn" data-act="sub" title="Add sub-task" aria-label="Add sub-task">＋</button>
                <button type="button" class="orch-icon-btn is-danger" data-act="remove" title="Delete task" aria-label="Delete task">✕</button>
            </span>` : '';
        return `<li class="tasks-item${n.done ? ' is-done' : ''}" data-id="${M.esc(n.id)}">
            <div class="tasks-row">
                <label class="tasks-check"><input type="checkbox" data-act="toggle" ${n.done ? 'checked' : ''} ${canEdit ? '' : 'disabled'} aria-label="Done"><span aria-hidden="true"></span></label>
                <textarea class="tasks-text" rows="1" maxlength="4000" data-item="${M.esc(n.id)}" placeholder="Describe the task" ${canEdit ? '' : 'readonly tabindex="-1"'} aria-label="Task">${M.esc(n.text)}</textarea>
                ${actions}
            </div>
            ${kids ? `<ul class="tasks-children">${kids}</ul>` : ''}
        </li>`;
    }

    function treeHtml() {
        const items = state.draft.items;
        const canEdit = editable();
        const rows = items.map((n, i) => itemHtml(n, 0, canEdit, items.length, i)).join('');
        const hidden = state.hideDone && state.mode === 'edit' && M.count(items).done;
        const empty = !items.length
            ? `<li class="tasks-empty-row">${canEdit ? 'No tasks yet.' : 'This list is empty.'}</li>`
            : (!rows && hidden ? '<li class="tasks-empty-row">Every task is done. Turn off “Hide completed” to see them.</li>' : '');
        return `<ul class="tasks-tree" id="tasksTree">${rows}${empty}</ul>
            ${canEdit ? '<button type="button" class="tasks-add" data-act="add"><span aria-hidden="true">＋</span> Add task</button>' : ''}`;
    }

    function renderTree() {
        const host = $('tasksTreeHost');
        if (!host) return;
        host.innerHTML = treeHtml();
        host.querySelectorAll('textarea.tasks-text').forEach(autosize);
        const c = M.count(state.draft.items);
        const ring = $('tasksHeroRing');
        if (ring) ring.innerHTML = ringHtml(c, 64, state.mode === 'edit' && (find(state.selected) || {}).status === 'archived');
        if (state.focusId) {
            const el = host.querySelector(`textarea[data-item="${CSS.escape(state.focusId)}"]`);
            state.focusId = '';
            if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
        }
        renderSavebar();
    }

    function autosize(el) {
        el.style.height = 'auto';
        el.style.height = `${el.scrollHeight}px`;
    }

    function segHtml(name, value, options) {
        return `<div class="tasks-seg" role="radiogroup" data-seg="${name}">${options.map((o) =>
            `<button type="button" role="radio" data-value="${M.esc(o.value)}" aria-checked="${String(o.value === value)}" ${o.disabled ? 'disabled' : ''} title="${M.esc(o.title || '')}">${M.esc(o.label)}</button>`).join('')}</div>`;
    }

    function chatOptions(excludeIds, placeholder) {
        const skip = new Set((excludeIds || []).map(String));
        return `<option value="">${M.esc(placeholder)}</option>` + state.chats.filter((c) => !skip.has(String(c.id))).map((c) =>
            `<option value="${c.id}">${M.esc(c.name)} · ${M.esc(c.handle)}${c.project_path ? ' · ' + M.esc(M.projectName(c.project_path)) : ''}${c.archived ? ' (archived)' : ''}</option>`).join('');
    }

    function enhanceSelects(scope) {
        if (!window.CuttleSearchableSelect) return;
        scope.querySelectorAll('select[data-combo]').forEach((sel) => {
            window.CuttleSearchableSelect.enhance(sel, { floating: true, label: sel.getAttribute('aria-label') || 'Chat', searchPlaceholder: 'Search chats…' });
        });
    }

    function chatRowHtml(c, extra) {
        return `<div class="tasks-chat${c.missing ? ' is-missing' : ''}">
            <span class="orch-card-icon orch-card-icon--chat" aria-hidden="true">💬</span>
            <span class="orch-card-main">
                <span class="orch-card-title">${M.esc(c.name)}</span>
                <span class="orch-card-sub"><span class="tasks-handle">${M.esc(c.handle)}</span>${c.project_path ? ` · ${M.esc(M.projectName(c.project_path))}` : ''}${c.last_activity ? ` · ${M.esc(M.relativeTime(c.last_activity))}` : ''}</span>
            </span>
            ${extra || ''}
            ${c.missing ? '' : `<button type="button" class="orch-small-btn" data-chat="${c.id}">Open</button>`}
        </div>`;
    }

    // ── Editor: empty / new / detail ────────────────────────────────────

    function emptyHtml() {
        const any = state.lists.length;
        return `<div class="orch-empty">
            <div class="tasks-empty-art" aria-hidden="true"><span>☑</span><span>☐</span><span>☑</span></div>
            <h2>${any ? 'Pick a list' : 'No Tasks lists yet'}</h2>
            <p>${any ? 'Choose a list on the left to see its tasks, the chats it shows up in, and its change history.'
                : 'Agents create Tasks lists while they plan multi-step work. You can also start one yourself and attach it to a chat, a whole project, or several linked chats.'}</p>
            <button type="button" class="primary" data-act="new">New list</button>
        </div>`;
    }

    function newHtml() {
        const d = state.draft;
        const home = chat(d.session_id);
        const hasProject = !!(home && home.project_path);
        const linked = d.linked.map((id) => chat(id)).filter(Boolean);
        return `
            <div class="orch-identity tasks-identity">
                <span id="tasksHeroRing">${ringHtml(M.count(d.items), 64, false)}</span>
                <div class="orch-identity-fields">
                    <input id="tasksTitle" class="orch-title-input" maxlength="200" placeholder="List name" value="${M.esc(d.title)}" aria-label="List name">
                    <input id="tasksDescription" class="orch-sub-input" maxlength="2000" placeholder="What is this list for? Agents read this as the list's intent." value="${M.esc(d.description)}" aria-label="Description">
                    <div class="orch-id-row"><span class="orch-tag is-accent">New list</span></div>
                </div>
            </div>

            <div class="tasks-new-grid">
                <div class="orch-field-block">
                    <div class="orch-block-head"><h3>Home chat</h3></div>
                    <select id="tasksNewChat" data-combo aria-label="Home chat">${chatOptions([], 'Choose a chat…')}</select>
                    <p class="orch-help">Agents in this chat see the list in their context and can update it.</p>
                </div>
                <div class="orch-field-block">
                    <div class="orch-block-head"><h3>Shows in</h3></div>
                    ${segHtml('scope', d.scope, [
                        { value: 'session', label: 'This chat' },
                        { value: 'project', label: hasProject ? `All of ${M.projectName(home.project_path)}` : 'Whole project', disabled: !hasProject, title: hasProject ? '' : 'The home chat has no project' },
                    ])}
                    <p class="orch-help">${d.scope === 'project' ? 'Every chat in the project shows this list.' : 'Only the home chat, plus any chats you link below.'}</p>
                </div>
                <div class="orch-field-block">
                    <div class="orch-block-head"><h3>Who edits tasks</h3></div>
                    ${segHtml('edit_mode', d.edit_mode, [
                        { value: 'shared', label: 'You and agents' },
                        { value: 'agent', label: 'Agents only' },
                    ])}
                    <p class="orch-help">${d.edit_mode === 'shared' ? 'You can check, edit and reorder tasks from chat and here.' : 'Only agents change tasks; you can still rename or archive the list.'}</p>
                </div>
                <div class="orch-field-block">
                    <div class="orch-block-head"><h3>Linked chats</h3><span class="orch-help">Optional</span></div>
                    <div class="tasks-chip-row">${linked.map((c) => `<span class="tasks-chip">${M.esc(c.name)} <span class="tasks-handle">${M.esc(c.handle)}</span><button type="button" data-unlink-new="${c.id}" aria-label="Remove ${M.esc(c.name)}">✕</button></span>`).join('')}</div>
                    <select id="tasksNewLink" data-combo aria-label="Link a chat">${chatOptions([d.session_id, ...d.linked], 'Link another chat…')}</select>
                </div>
            </div>

            <div class="orch-field-block">
                <div class="orch-block-head"><h3>Tasks</h3><span class="orch-help">Enter adds a task · Tab nests it · Alt+↑/↓ reorders</span></div>
                <div id="tasksTreeHost"></div>
            </div>

            <div class="orch-editor-actions">
                <span class="orch-grow"></span>
                <button type="button" data-act="cancel">Cancel</button>
                <button type="button" class="primary" data-act="create">Create list</button>
            </div>`;
    }

    function detailHeroHtml(l) {
        const archived = l.status === 'archived';
        const reach = M.reachChats(l);
        const projectChats = l.scope === 'project' ? M.chatsInProject(state.chats, l.project_path) : [];
        const openTarget = (reach[0] && !reach[0].missing && reach[0].id) || (projectChats[0] && projectChats[0].id) || '';
        return `
            <div class="orch-identity tasks-identity">
                <span id="tasksHeroRing">${ringHtml(M.count(state.draft.items), 64, archived)}</span>
                <div class="orch-identity-fields">
                    <input id="tasksTitle" class="orch-title-input" maxlength="200" placeholder="List name" value="${M.esc(state.draft.title)}" aria-label="List name">
                    <input id="tasksDescription" class="orch-sub-input" maxlength="2000" placeholder="Add a description (agents read it as the list's intent)" value="${M.esc(state.draft.description)}" aria-label="Description">
                    <div class="orch-id-row">
                        <span class="tasks-status ${archived ? 'is-archived' : 'is-active'}">${archived ? 'Archived' : 'Active'}</span>
                        <span class="orch-tag">${l.edit_mode === 'shared' ? 'You and agents' : 'Agents only'}</span>
                        <span class="orch-tag${l.scope === 'project' ? ' is-accent' : ''}">${M.esc(l.scope === 'project' ? `Project · ${M.projectName(l.project_path)}` : 'Chat')}${(l.linked_chats || []).length ? ` +${l.linked_chats.length}` : ''}</span>
                        <button type="button" class="orch-code-chip" data-act="copy-id" title="Copy id for python -m api.gizmos tasks"><span class="orch-code-chip-label">id</span><code>${M.esc(l.id)}</code><span class="orch-copy-icon" aria-hidden="true">⧉</span></button>
                        <span class="orch-grow"></span>
                        ${openTarget ? `<button type="button" class="orch-small-btn" data-chat="${openTarget}">Open chat</button>` : ''}
                        <button type="button" class="orch-small-btn" data-act="${archived ? 'restore' : 'archive'}">${archived ? 'Restore' : 'Archive'}</button>
                        <button type="button" class="danger orch-small-btn" data-act="delete">Delete</button>
                    </div>
                </div>
            </div>`;
    }

    function tasksTabHtml(l) {
        const lock = l.edit_mode !== 'shared'
            ? `<div class="tasks-lock"><span class="tasks-lock-icon" aria-hidden="true">🔒</span>
                <span class="tasks-lock-text"><strong>Agents only.</strong> Agents keep this list up to date; you can rename, describe, link or archive it.</span>
                <button type="button" class="orch-small-btn" data-act="share">Let me edit tasks</button></div>` : '';
        const c = M.count(state.draft.items);
        return `${lock}
            <div class="tasks-toolbar">
                <span class="tasks-progress-text"><strong>${c.done}</strong> of ${c.total} done${c.total ? ` · ${c.pct}%` : ''}</span>
                <span class="orch-grow"></span>
                ${c.done ? `<label class="tasks-toggle"><input type="checkbox" data-act="hide-done" ${state.hideDone ? 'checked' : ''}> Hide completed</label>` : ''}
            </div>
            <div class="tasks-bar" aria-hidden="true"><span style="width:${c.pct}%"></span></div>
            <div id="tasksTreeHost"></div>
            ${l.edit_mode === 'shared' ? '<p class="orch-help tasks-keys">Enter adds a task · Tab nests it · Shift+Tab un-nests · Alt+↑/↓ reorders · Backspace on an empty task deletes it</p>' : ''}`;
    }

    function chatsTabHtml(l) {
        const home = l.home_chat;
        const homeProject = home && home.project_path;
        const linked = l.linked_chats || [];
        const projectChats = l.scope === 'project' ? M.chatsInProject(state.chats, l.project_path) : [];
        const exclude = [home && home.id, ...linked.map((c) => c.id)].filter(Boolean);
        let homeBlock;
        if (l.scope === 'project') {
            homeBlock = `<div class="tasks-project">
                    <span class="orch-card-icon" aria-hidden="true">📁</span>
                    <span class="orch-card-main"><span class="orch-card-title">${M.esc(M.projectName(l.project_path))}</span>
                    <span class="orch-card-sub" title="${M.esc(l.project_path)}">${M.esc(l.project_path)}</span></span>
                </div>
                <p class="orch-help">Shows in every chat of this project${projectChats.length ? ` (${projectChats.length} with messages)` : ''}.</p>
                ${projectChats.length ? `<div class="tasks-chat-list">${projectChats.slice(0, 6).map((c) => chatRowHtml(c)).join('')}</div>` : ''}
                <div class="tasks-inline-form"><select id="tasksScopeChat" data-combo aria-label="Limit to one chat">${chatOptions([], 'Limit to one chat…')}</select></div>`;
        } else {
            homeBlock = home ? `<div class="tasks-chat-list">${chatRowHtml(home, '<span class="orch-tag is-accent">Home</span>')}</div>
                ${homeProject ? `<div class="tasks-inline-form"><button type="button" class="orch-small-btn" data-act="to-project">Show in all of ${M.esc(M.projectName(homeProject))}</button><span class="orch-help">Every chat in the project will see it.</span></div>` : ''}`
                : '<p class="orch-help">This list has no home chat.</p>';
        }
        let related = '';
        if (state.related === null) related = '<p class="orch-help">Looking for related chats…</p>';
        else {
            const skip = new Set(exclude.map(String));
            const fresh = state.related.filter((c) => !skip.has(String(c.id)));
            related = fresh.length
                ? `<div class="tasks-chat-list">${fresh.map((c) => chatRowHtml(c, `<span class="orch-tag">${M.esc(M.RELATION_LABELS[c.relation] || c.relation)}</span><button type="button" class="orch-small-btn" data-link="${c.id}">Link</button>`)).join('')}</div>
                   ${fresh.length > 1 ? `<button type="button" class="orch-small-btn tasks-link-all" data-act="link-all">Link all ${fresh.length}</button>` : ''}`
                : `<p class="orch-help">${state.related.length ? 'Every related chat is already linked.' : 'No orchestrator, supervisor or sub-agent chats are connected to the home chat.'}</p>`;
        }
        return `
            <div class="orch-field-block tasks-first-block">
                <div class="orch-block-head"><h3>${l.scope === 'project' ? 'Project' : 'Home chat'}</h3></div>
                ${homeBlock}
            </div>
            <div class="orch-field-block">
                <div class="orch-block-head"><h3>Linked chats</h3><span class="orch-count">${linked.length || ''}</span></div>
                <p class="orch-help tasks-help-top">Linked chats show this list in their composer and agent context too — share one plan between an orchestrator, its supervisor and its sub-agents.</p>
                ${linked.length ? `<div class="tasks-chat-list">${linked.map((c) => chatRowHtml(c, `<button type="button" class="orch-icon-btn is-danger" data-unlink="${c.id}" title="Unlink" aria-label="Unlink ${M.esc(c.name)}">✕</button>`)).join('')}</div>` : ''}
                <div class="tasks-inline-form"><select id="tasksLinkChat" data-combo aria-label="Link a chat">${chatOptions(exclude, 'Link a chat…')}</select></div>
            </div>
            <div class="orch-field-block">
                <div class="orch-block-head"><h3>Same orchestration</h3></div>
                <p class="orch-help tasks-help-top">Parent, sub-agent and supervisor chats connected to the home chat.</p>
                ${related}
            </div>
            <div class="orch-field-block">
                <div class="orch-block-head"><h3>Who edits tasks</h3></div>
                ${segHtml('edit_mode', l.edit_mode === 'shared' ? 'shared' : 'agent', [
                    { value: 'shared', label: 'You and agents' },
                    { value: 'agent', label: 'Agents only' },
                ])}
            </div>`;
    }

    function historyTabHtml() {
        if (state.history === null) return '<p class="orch-help">Loading history…</p>';
        const rows = M.historyEntries(state.history, { includeReads: state.historyReads });
        return `<div class="tasks-toolbar"><span class="orch-help">Every change, with who made it.</span><span class="orch-grow"></span>
                <label class="tasks-toggle"><input type="checkbox" data-act="history-reads" ${state.historyReads ? 'checked' : ''}> Show agent reads</label></div>
            ${rows.length ? `<ol class="tasks-timeline">${rows.map((e) => `<li class="tasks-event is-${M.esc(e.source || 'other')} op-${M.esc(e.operation)}">
                <span class="tasks-event-dot" aria-hidden="true"></span>
                <div class="tasks-event-body">
                    <div class="tasks-event-head"><strong>${M.esc(e.label)}</strong><span class="tasks-event-actor">${M.esc(e.actor)}</span><span class="orch-grow"></span>${e.progress ? `<span class="tasks-card-count">${M.esc(e.progress)}</span>` : ''}<time title="${M.esc(String(e.time || ''))} UTC">${M.esc(M.relativeTime(e.time))}</time></div>
                    ${e.detail ? `<div class="tasks-event-detail">${M.esc(e.detail)}</div>` : ''}
                </div></li>`).join('')}</ol>` : '<p class="orch-help">No changes recorded yet.</p>'}`;
    }

    function detailHtml(l) {
        const tab = state.tab;
        const tabs = [['tasks', 'Tasks'], ['chats', 'Chats'], ['history', 'History']];
        const body = tab === 'chats' ? chatsTabHtml(l) : tab === 'history' ? historyTabHtml() : tasksTabHtml(l);
        return `${detailHeroHtml(l)}
            <nav class="orch-tabs tasks-tabs" role="tablist" aria-label="List sections">
                ${tabs.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${String(tab === id)}">${label}${id === 'chats' ? ` <span class="orch-count">${M.reachChats(l).length || ''}</span>` : ''}</button>`).join('')}
            </nav>
            <section class="orch-tab" data-panel="${tab}">${body}</section>`;
    }

    function renderEditor() {
        if (state.mode === 'new' && state.draft) editor.innerHTML = newHtml();
        else if (state.mode === 'edit' && find(state.selected)) editor.innerHTML = detailHtml(find(state.selected));
        else editor.innerHTML = emptyHtml();
        if ($('tasksNewChat')) $('tasksNewChat').value = state.draft.session_id ? String(state.draft.session_id) : '';
        enhanceSelects(editor);
        if ($('tasksTreeHost')) renderTree();
        else renderSavebar();
    }

    function renderSavebar() {
        const show = state.mode === 'edit' && (dirty() || state.conflict);
        savebar.hidden = !show;
        savebar.classList.toggle('is-dirty', show);
        if (!show) return;
        const stateEl = $('tasksSaveState');
        if (state.conflict) {
            stateEl.className = 'orch-savebar-state is-bad';
            stateEl.innerHTML = '<strong>Changed elsewhere.</strong> Reload to see it, or overwrite with your version.';
            $('tasksDiscard').textContent = 'Reload';
            $('tasksSave').textContent = 'Overwrite';
        } else {
            stateEl.className = `orch-savebar-state ${state.remote ? 'is-bad' : 'is-dirty'}`;
            stateEl.innerHTML = state.remote ? '<strong>Unsaved changes</strong> · an agent also updated this list' : '<strong>Unsaved changes</strong>';
            $('tasksDiscard').textContent = 'Discard';
            $('tasksSave').textContent = 'Save changes';
        }
        $('tasksSave').disabled = state.busy;
    }

    // ── Opening ─────────────────────────────────────────────────────────

    function snapshot(l) {
        return { title: l.title || '', description: l.description || '', items: M.items(l), revision: l.revision };
    }

    function confirmDiscard() {
        return !dirty() || window.confirm('Discard unsaved changes to this list?');
    }

    function open(id, opts) {
        opts = opts || {};
        if (!opts.force && !confirmDiscard()) return;
        const l = find(id);
        if (!l) return;
        const same = state.selected === id && state.mode === 'edit';
        state.selected = id;
        state.mode = 'edit';
        state.base = snapshot(l);
        state.draft = M.clone(state.base);
        state.conflict = false;
        state.remote = false;
        if (!same) {
            state.tab = opts.tab || 'tasks';
            state.history = null;
            state.related = null;
        }
        renderList();
        renderEditor();
        syncUrl();
        loadTab();
    }

    function startNew(sessionId) {
        if (!confirmDiscard()) return;
        const sid = sessionId || (state.chats[0] && state.chats[0].id) || '';
        state.mode = 'new';
        state.selected = '';
        state.base = { title: '', description: '', items: [] };
        const first = { id: M.newId([]), text: '', done: false, children: [] };
        state.draft = { title: '', description: '', items: [first], session_id: sid, scope: 'session', edit_mode: 'shared', linked: [] };
        state.conflict = false;
        renderList();
        renderEditor();
        syncUrl();
        $('tasksTitle')?.focus();
    }

    function closeEditor() {
        state.mode = '';
        state.selected = '';
        state.draft = null;
        state.base = null;
        renderList();
        renderEditor();
        syncUrl();
    }

    async function loadTab() {
        const id = state.selected;
        if (state.mode !== 'edit' || !id) return;
        if (state.tab === 'history' && state.history === null) {
            try {
                const data = await call(`/api/gizmos/tasks/${encodeURIComponent(id)}/history?limit=300`);
                if (state.selected === id) { state.history = data.events || []; rerenderTab(); }
            } catch (err) { if (state.selected === id) { state.history = []; rerenderTab(); toast(err.message, 'error'); } }
        }
        if (state.tab === 'chats' && state.related === null) {
            try {
                const data = await call(`/api/gizmos/tasks/${encodeURIComponent(id)}/related`);
                if (state.selected === id) { state.related = data.chats || []; rerenderTab(); }
            } catch (_) { if (state.selected === id) { state.related = []; rerenderTab(); } }
        }
    }

    function rerenderTab() {
        if (state.mode !== 'edit') return;
        const l = find(state.selected);
        const panel = editor.querySelector('.orch-tab');
        if (!l || !panel) return;
        panel.innerHTML = state.tab === 'chats' ? chatsTabHtml(l) : state.tab === 'history' ? historyTabHtml() : tasksTabHtml(l);
        enhanceSelects(panel);
        if ($('tasksTreeHost')) renderTree();
    }

    // ── Writes ──────────────────────────────────────────────────────────

    function replaceList(row) {
        const i = state.lists.findIndex((l) => l.id === row.id);
        if (i >= 0) state.lists[i] = row; else state.lists.unshift(row);
    }

    /** Immediate PATCH (status, mode, scope, links, toggles). Keeps any draft. */
    async function patchNow(ops, okMessage) {
        const id = state.selected;
        try {
            const data = await call(`/api/gizmos/tasks/${encodeURIComponent(id)}?summary=1`, 'PATCH', ops);
            const row = data.gizmo;
            replaceList(row);
            const fresh = snapshot(row);
            if (dirty()) {
                // These ops never touch the drafted fields; keep the draft and
                // move the revision guard so the next save is not a false conflict.
                const treeDirty = itemsDirty();
                state.base = { ...state.base, revision: fresh.revision, items: treeDirty ? state.base.items : fresh.items };
                if (!treeDirty) state.draft.items = M.clone(fresh.items);
            } else {
                state.base = fresh;
                state.draft = M.clone(fresh);
            }
            state.history = null;
            if (okMessage) toast(okMessage, 'success');
            if (row.status === 'archived' && ops.set_done && find(id)) toast('Every task is done — list archived.', 'success');
            renderList();
            renderEditor();
            loadTab();
            return row;
        } catch (err) {
            toast(err.message, 'error');
            return null;
        }
    }

    async function save(overwrite) {
        const l = find(state.selected);
        if (!l || state.busy) return;
        const d = state.draft;
        const ops = {};
        if (d.title.trim() !== state.base.title) ops.title = d.title.trim() || 'Tasks';
        if (d.description !== state.base.description) ops.description = d.description;
        if (itemsDirty()) {
            const items = M.prune(d.items);
            if (!items.length && M.count(state.base.items).total) {
                if (!window.confirm('Save this list with no tasks?')) return;
            }
            ops.items = items;
        }
        if (!Object.keys(ops).length) { state.conflict = false; renderSavebar(); return; }
        if (!overwrite) ops.expected_revision = state.base.revision;
        state.busy = true;
        renderSavebar();
        try {
            const data = await call(`/api/gizmos/tasks/${encodeURIComponent(l.id)}?summary=1`, 'PATCH', ops);
            replaceList(data.gizmo);
            state.base = snapshot(data.gizmo);
            state.draft = M.clone(state.base);
            state.conflict = false;
            state.remote = false;
            state.history = null;
            toast(data.gizmo.status === 'archived' && l.status !== 'archived' ? 'Saved — every task is done, so the list is archived.' : 'Saved', 'success');
            renderList();
            renderEditor();
        } catch (err) {
            if (err.code === 'conflict' && !overwrite) { state.conflict = true; renderSavebar(); }
            else toast(err.message, 'error');
        } finally {
            state.busy = false;
            renderSavebar();
        }
    }

    async function create() {
        const d = state.draft;
        const items = M.prune(d.items);
        if (!d.session_id) { toast('Choose the chat this list belongs to.', 'error'); return; }
        if (!items.length) { toast('Add at least one task.', 'error'); editor.querySelector('textarea.tasks-text')?.focus(); return; }
        if (state.busy) return;
        state.busy = true;
        try {
            const data = await call('/api/gizmos/tasks', 'POST', {
                title: d.title.trim() || 'Tasks', description: d.description.trim(), items,
                scope: d.scope, edit_mode: d.edit_mode, session_id: String(d.session_id),
                linked_sessions: d.linked.map(String),
            });
            toast(`Created ${data.gizmo.title}`, 'success');
            state.draft = null;
            state.base = null;
            state.status = 'active';
            await load({ select: data.gizmo.id, force: true });
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            state.busy = false;
        }
    }

    async function removeList() {
        const l = find(state.selected);
        if (!l) return;
        const reach = M.reachChats(l).length;
        const where = l.scope === 'project' ? ` from every chat in ${M.projectName(l.project_path)}` : reach > 1 ? ` from ${reach} chats` : '';
        if (!window.confirm(`Delete “${l.title}”${where}? This can't be undone — archive it instead to keep it.`)) return;
        try {
            await call(`/api/gizmos/tasks/${encodeURIComponent(l.id)}`, 'DELETE');
            state.lists = state.lists.filter((x) => x.id !== l.id);
            toast(`Deleted ${l.title}`, 'success');
            state.draft = null;
            closeEditor();
        } catch (err) { toast(err.message, 'error'); }
    }

    /** Checkbox: immediate when the tree has no unsaved edits, else part of the draft. */
    function toggle(itemId, done) {
        if (state.mode === 'new' || itemsDirty()) {
            M.setDone(state.draft.items, itemId, done);
            renderTree();
            return;
        }
        const before = M.clone(state.draft.items);
        const after = M.clone(before);
        M.setDone(after, itemId, done);
        const setDone = [];
        const setUndone = [];
        (function walk(a, b) {
            a.forEach((n, i) => {
                const m = b[i];
                if (n.done !== m.done) (m.done ? setDone : setUndone).push(m.id);
                walk(n.children, m.children);
            });
        })(before, after);
        state.draft.items = after;
        state.base.items = M.clone(after);
        renderTree();
        const ops = {};
        if (setDone.length) ops.set_done = setDone;
        if (setUndone.length) ops.set_undone = setUndone;
        if (Object.keys(ops).length) patchNow(ops).then((row) => { if (!row) load({ force: true }); });
    }

    // ── Loading + live refresh ──────────────────────────────────────────

    async function load(opts) {
        opts = opts || {};
        try {
            const data = await call('/api/gizmos/tasks?all=1');
            state.lists = data.lists || [];
            state.chats = data.chats || [];
            state.revision = data.revision;
            notice.hidden = true;
            content.hidden = false;
            const want = opts.select || (state.mode === 'edit' && state.selected);
            if (want && find(want)) {
                // A deep-linked or just-created list must be visible in the sidebar.
                if (opts.select && find(want).status === 'archived' && state.status === 'active') state.status = 'archived';
                open(want, { force: true, tab: opts.tab });
            } else if (state.mode === 'new') { renderList(); }
            else { state.mode = ''; state.selected = ''; renderList(); renderEditor(); }
        } catch (err) {
            notice.hidden = false;
            notice.classList.add('is-error');
            notice.textContent = err.message || 'Tasks could not load.';
            content.hidden = true;
        }
    }

    async function poll() {
        if (document.hidden || state.busy) return;
        let data;
        try { data = await call('/api/gizmos/tasks?all=1'); } catch (_) { return; }
        if (data.revision === state.revision) return;
        state.revision = data.revision;
        state.lists = data.lists || [];
        state.chats = data.chats || [];
        renderList();
        if (state.mode !== 'edit') return;
        const l = find(state.selected);
        if (!l) {
            toast('This list was deleted elsewhere.', 'info');
            state.draft = null;
            closeEditor();
            return;
        }
        if (l.revision === state.base.revision) return;
        state.history = null;
        state.related = null;
        if (dirty()) { state.remote = true; renderSavebar(); return; }
        const active = document.activeElement;
        const focusItem = active && active.dataset ? active.dataset.item : '';
        open(l.id, { force: true });
        if (focusItem) { state.focusId = focusItem; renderTree(); }
    }

    function startPolling() {
        clearInterval(pollTimer);
        pollTimer = setInterval(poll, POLL_MS);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
    }

    // ── Tree events ─────────────────────────────────────────────────────

    function itemIdOf(el) { return el.closest('.tasks-item')?.dataset.id || ''; }

    function structural(fn, focusId) {
        fn(state.draft.items);
        state.focusId = focusId || '';
        renderTree();
    }

    function onTreeKey(e) {
        const ta = e.target;
        if (!ta.matches('textarea.tasks-text') || ta.readOnly) return;
        const id = ta.dataset.item;
        const items = state.draft.items;
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            const at = M.locate(items, id);
            if (at && !ta.value.trim() && at.depth > 0) { structural((xs) => M.outdent(xs, id), id); return; }
            const created = M.addItem(items, { afterId: id });
            state.focusId = created;
            renderTree();
        } else if (e.key === 'Tab') {
            e.preventDefault();
            structural((xs) => (e.shiftKey ? M.outdent(xs, id) : M.indent(xs, id)), id);
        } else if (e.key === 'Backspace' && !ta.value && (M.count(items).total > 1 || state.mode === 'edit')) {
            e.preventDefault();
            const prev = previousVisible(ta);
            M.removeItem(items, id);
            state.focusId = prev;
            renderTree();
        } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
            e.preventDefault();
            structural((xs) => M.move(xs, id, e.key === 'ArrowUp' ? -1 : 1), id);
        } else if (!e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
            const atEdge = e.key === 'ArrowUp' ? ta.selectionStart === 0 : ta.selectionEnd === ta.value.length;
            if (!atEdge) return;
            const all = Array.from(editor.querySelectorAll('textarea.tasks-text'));
            const next = all[all.indexOf(ta) + (e.key === 'ArrowUp' ? -1 : 1)];
            if (next) { e.preventDefault(); next.focus(); }
        }
    }

    function previousVisible(ta) {
        const all = Array.from(editor.querySelectorAll('textarea.tasks-text'));
        const i = all.indexOf(ta);
        const prev = all[i - 1] || all[i + 1];
        return prev ? prev.dataset.item : '';
    }

    function onTreeClick(btn) {
        const act = btn.dataset.act;
        const id = itemIdOf(btn);
        const items = state.draft.items;
        if (act === 'add') { const created = M.addItem(items, {}); state.focusId = created; renderTree(); return true; }
        if (!id) return false;
        if (act === 'up' || act === 'down') structural((xs) => M.move(xs, id, act === 'up' ? -1 : 1), id);
        else if (act === 'indent') structural((xs) => M.indent(xs, id), id);
        else if (act === 'outdent') structural((xs) => M.outdent(xs, id), id);
        else if (act === 'sub') { const created = M.addItem(items, { parentId: id }); state.focusId = created; renderTree(); }
        else if (act === 'remove') {
            const at = M.locate(items, id);
            const kids = at ? M.count(at.node.children).total : 0;
            if (kids && !window.confirm(`Delete this task and its ${kids} sub-task${kids === 1 ? '' : 's'}?`)) return true;
            const next = M.removeItem(items, id);
            state.focusId = next;
            renderTree();
        } else return false;
        return true;
    }

    // ── Events ──────────────────────────────────────────────────────────

    $('tasksNew').addEventListener('click', () => startNew());
    $('tasksRefresh').addEventListener('click', () => {
        if (!confirmDiscard()) return;
        state.history = null;
        state.related = null;
        if (state.mode === 'new') { state.mode = ''; state.draft = null; }
        load({ force: true });
    });
    search.addEventListener('input', () => { state.query = search.value; renderList(); });
    content.querySelector('.tasks-filter').addEventListener('click', (e) => {
        const b = e.target.closest('[data-status]');
        if (!b) return;
        state.status = b.dataset.status;
        renderList();
    });
    listEl.addEventListener('click', (e) => {
        const card = e.target.closest('[data-open]');
        if (card) open(card.dataset.open);
    });

    editor.addEventListener('click', async (e) => {
        const btn = e.target.closest('button');
        if (!btn || btn.disabled) return;
        if (btn.dataset.chat) { openChat(btn.dataset.chat); return; }
        if (btn.dataset.tab) {
            state.tab = btn.dataset.tab;
            editor.querySelectorAll('.tasks-tabs [data-tab]').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === state.tab)));
            const panel = editor.querySelector('.orch-tab');
            panel.dataset.panel = state.tab;
            rerenderTab();
            loadTab();
            return;
        }
        if (btn.closest('.tasks-seg')) {
            const seg = btn.closest('.tasks-seg').dataset.seg;
            const value = btn.dataset.value;
            if (state.mode === 'new') { state.draft[seg] = value; renderEditor(); return; }
            const l = find(state.selected);
            if (seg === 'edit_mode' && l && l.edit_mode !== value) patchNow({ edit_mode: value }, value === 'shared' ? 'You can edit these tasks now' : 'Only agents edit these tasks now');
            return;
        }
        if (btn.dataset.unlinkNew) { state.draft.linked = state.draft.linked.filter((id) => String(id) !== btn.dataset.unlinkNew); renderEditor(); return; }
        if (btn.dataset.unlink) { const c = chat(btn.dataset.unlink); patchNow({ unlink: [btn.dataset.unlink] }, `Unlinked ${c ? c.name : 'chat'}`); return; }
        if (btn.dataset.link) { const c = (state.related || []).find((x) => String(x.id) === btn.dataset.link); patchNow({ link: [btn.dataset.link] }, `Linked ${c ? c.name : 'chat'}`); return; }
        if (btn.closest('#tasksTreeHost') && onTreeClick(btn)) return;
        const act = btn.dataset.act;
        if (act === 'new') startNew();
        else if (act === 'cancel') { state.draft = null; closeEditor(); }
        else if (act === 'create') create();
        else if (act === 'archive') patchNow({ status: 'archived' }, 'Archived');
        else if (act === 'restore') patchNow({ status: 'active' }, 'Restored');
        else if (act === 'delete') removeList();
        else if (act === 'share') patchNow({ edit_mode: 'shared' }, 'You can edit these tasks now');
        else if (act === 'to-project') {
            const l = find(state.selected);
            if (l && l.home_chat && window.confirm(`Show “${l.title}” in every chat of ${M.projectName(l.home_chat.project_path)}?`)) {
                patchNow({ scope: 'project', session_id: String(l.home_chat.id) }, 'Now shows in the whole project');
            }
        } else if (act === 'link-all') {
            const l = find(state.selected);
            const skip = new Set([l.home_chat && l.home_chat.id, ...(l.linked_chats || []).map((c) => c.id)].filter(Boolean).map(String));
            const ids = (state.related || []).filter((c) => !skip.has(String(c.id))).map((c) => String(c.id));
            if (ids.length) patchNow({ link: ids }, `Linked ${ids.length} chats`);
        } else if (act === 'copy-id') {
            try { await navigator.clipboard.writeText(find(state.selected).id); toast('Copied id', 'success'); } catch (_) {}
        }
    });

    editor.addEventListener('change', (e) => {
        const t = e.target;
        if (t.dataset.act === 'toggle') { toggle(itemIdOf(t), t.checked); return; }
        if (t.dataset.act === 'hide-done') { state.hideDone = t.checked; renderTree(); return; }
        if (t.dataset.act === 'history-reads') { state.historyReads = t.checked; rerenderTab(); return; }
        if (t.id === 'tasksNewChat') {
            state.draft.session_id = t.value;
            state.draft.linked = state.draft.linked.filter((id) => String(id) !== t.value);
            const c = chat(t.value);
            if (!(c && c.project_path)) state.draft.scope = 'session';
            renderEditor();
        } else if (t.id === 'tasksNewLink' && t.value) {
            state.draft.linked.push(t.value);
            renderEditor();
        } else if (t.id === 'tasksLinkChat' && t.value) {
            const c = chat(t.value);
            patchNow({ link: [t.value] }, `Linked ${c ? c.name : 'chat'}`);
        } else if (t.id === 'tasksScopeChat' && t.value) {
            const c = chat(t.value);
            const l = find(state.selected);
            if (window.confirm(`Show “${l.title}” only in ${c ? c.name : 'that chat'} (plus linked chats)?`)) {
                patchNow({ scope: 'session', session_id: t.value }, `Now lives in ${c ? c.name : 'that chat'}`);
            } else { t.value = ''; }
        }
    });

    editor.addEventListener('input', (e) => {
        const t = e.target;
        if (!state.draft) return;
        if (t.id === 'tasksTitle') state.draft.title = t.value;
        else if (t.id === 'tasksDescription') state.draft.description = t.value;
        else if (t.matches('textarea.tasks-text')) {
            // Task text is one line; pasted newlines become spaces.
            if (/\n/.test(t.value)) t.value = t.value.replace(/\s*\n\s*/g, ' ');
            M.setText(state.draft.items, t.dataset.item, t.value);
            autosize(t);
        } else return;
        if (state.mode === 'edit') renderSavebar();
    });

    editor.addEventListener('keydown', (e) => {
        if (e.target.closest && e.target.closest('#tasksTreeHost')) onTreeKey(e);
        else if (e.key === 'Enter' && (e.target.id === 'tasksTitle' || e.target.id === 'tasksDescription')) {
            e.preventDefault();
            if (state.mode === 'new') editor.querySelector('textarea.tasks-text')?.focus();
            else save();
        }
    });

    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            if (state.mode === 'edit' && (dirty() || state.conflict)) { e.preventDefault(); save(state.conflict); }
            else if (state.mode === 'new') { e.preventDefault(); create(); }
        }
    });

    $('tasksSave').addEventListener('click', () => save(state.conflict));
    $('tasksDiscard').addEventListener('click', () => {
        if (state.conflict || state.remote) { state.conflict = false; state.remote = false; load({ force: true }); return; }
        state.draft = M.clone(state.base);
        renderEditor();
    });
    window.addEventListener('beforeunload', (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } });

    // Deep links: ?list=<id> opens a list, ?chat=<id> starts a new one for that chat.
    const params = new URLSearchParams(location.search);
    load({ select: params.get('list') || '', force: true, tab: params.get('tab') || '' }).then(() => {
        const sid = params.get('chat');
        if (sid && !params.get('list')) startNew(sid.replace(/^CH-0*/i, ''));
        startPolling();
    });
})();
