/* Tasks gizmos — scoped composer dock, controlled through the Gizmos API.
 *
 * Also the one Tasks row renderer: `taskRowHtml` is shared by this dock and the
 * Studio work panel (chat_work_panel.js). Pure apart from `create`.
 *
 * Studio (L4): an item with `in_progress` gets `is-in-progress` (the glowing,
 * unticked checkbox); an `owner` renders as dot + name through `ownerHtml`
 * (default: escaped label). A list shown by a work panel in the chat is hidden
 * here while Studio is on, and `cuttle-work-lists-changed` tells the panels
 * when the lists change. Classic markup is unchanged: none of the Studio
 * hooks add anything when `showOwner` is off. */
(function (global) {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function countTasks(items) {
        var done = 0;
        var total = 0;
        function walk(nodes) {
            (nodes || []).forEach(function (n) {
                total += 1;
                if (n && n.done) done += 1;
                walk(n && n.children);
            });
        }
        walk(items);
        return { done: done, total: total };
    }

    function displayTitle(title, projectName) {
        title = String(title || 'Tasks');
        if (!projectName) return title;
        var prefix = projectName + ':';
        var trimmed = title.trim();
        if (!trimmed.toLowerCase().startsWith(prefix.toLowerCase())) return title;
        return trimmed.slice(prefix.length).trimStart() || title;
    }

    /** Escaped owner label; the work panel swaps in dot + name (see chat_work_panel.js). */
    function defaultOwnerHtml(owner) {
        return '<span class="chat-widget-task-owner">' + esc(owner && owner.label) + '</span>';
    }

    /**
     * One Tasks item as a list row. `opts`: shared (editable checkbox), studio
     * (Studio hooks: glowing `is-in-progress` class and the owner under the text),
     * ownerHtml(owner) → html, childrenHtml (nested list, already rendered).
     * `short` (the in-chat work panel) shows the item's short `label` when it
     * has one, and the full text as the app tooltip; the dock keeps the full text.
     * With `studio` off the markup is the Classic row, byte for byte. Pure.
     */
    function taskRowHtml(item, opts) {
        opts = opts || {};
        var id = esc(item.id || '');
        var text = esc(item.text || '');
        var done = !!item.done;
        var studio = !!opts.studio;
        var inProgress = studio && !done && item.in_progress === true;
        var shared = !!opts.shared;
        var disabled = shared ? '' : ' disabled';
        var textHtml = '<div class="chat-widget-task-text">' + text + '</div>';
        var body = textHtml;
        if (opts.short) {
            textHtml = '<div class="chat-widget-task-text" data-tooltip="' + text + '">' + (item.label ? esc(item.label) : text) + '</div>';
        }
        if (studio) {
            // Studio: the owner sits on the text's line, right-aligned (dot + name, muted).
            var owner = (item.owner && typeof item.owner === 'object')
                ? '<span class="chat-widget-task-owner">'
                    + (typeof opts.ownerHtml === 'function' ? opts.ownerHtml(item.owner) : defaultOwnerHtml(item.owner))
                    + '</span>'
                : '';
            body = '<div class="chat-widget-task-line">' + textHtml + owner + '</div>';
        }
        return (
            '<li class="chat-widget-task' + (done ? ' is-done' : '') + (inProgress ? ' is-in-progress' : '')
            + '" data-item-id="' + id + '">' +
            '<input type="checkbox" ' + (done ? 'checked ' : '') + disabled +
            ' aria-label="' + (shared ? 'Toggle task' : 'Agent-managed task') + '"/>' +
            '<div style="flex:1;min-width:0">' +
            body +
            (opts.childrenHtml || '') +
            '</div></li>'
        );
    }

    /** Lists as the page last saw them (tasks only), for the work panels. */
    var latestLists = [];
    var lastListsSig = '';

    /** Work panels repaint from this when the lists change (chat_subagent_launchers.js). */
    function notifyPanels() {
        if (typeof document === 'undefined' || !document.dispatchEvent || typeof CustomEvent !== 'function') return;
        document.dispatchEvent(new CustomEvent('cuttle-work-lists-changed'));
    }

    function activeLists() {
        return latestLists.slice();
    }

    function create(options) {
        options = options || {};
        var root = options.root || document;
        var getSessionId = options.getSessionId || function () { return null; };
        var getProjectPath = options.getProjectPath || function () { return ''; };
        var getProjectName = options.getProjectName || function () { return ''; };
        var fetchFn = options.fetchFn || fetch;
        var isStudio = options.isStudio || function () {
            return !!(global.CuttleChatDisplay && global.CuttleChatDisplay.isStudio());
        };
        var ownerHtml = options.ownerHtml || function (owner) {
            var panel = global.CuttleWorkPanel;
            return panel && typeof panel.ownerHtml === 'function' ? panel.ownerHtml(owner) : defaultOwnerHtml(owner);
        };
        var navigate = options.navigate || function (page) {
            if (global.parent && global.parent !== global) global.parent.postMessage({ type: 'cuttle-navigate', page: page }, '*');
            else global.location.assign(page);
        };
        var $ = function (id) {
            return root.getElementById ? root.getElementById(id) : document.getElementById(id);
        };

        var strip = null;
        var widgets = [];
        var revision = 0;
        var expanded = {};
        var inFlight = false;
        var dirty = false;
        /** Bumps on every refresh start so stale in-flight responses are ignored. */
        var fetchGen = 0;
        var lastFetchedKey = '';
        var lastRenderedHtml = '';

        function ensureStrip() {
            if (!strip) strip = $('chatWidgetsStrip');
            return strip;
        }

        function sessionParam() {
            var sid = getSessionId();
            if (sid == null || sid === '') return '';
            sid = String(sid);
            if (sid.indexOf('db_session_') === 0) sid = sid.slice('db_session_'.length);
            return sid;
        }

        function contextKey() {
            return sessionParam() + '\0' + String(getProjectPath() || '');
        }

        function clearStripImmediate() {
            widgets = [];
            revision = 0;
            render();
        }

        /** List shown here only because this chat is linked to it (not its home chat/project). */
        function isLinkedHere(w) {
            var sid = sessionParam();
            var links = w.linked_sessions;
            if (typeof links === 'string') {
                // Older servers return the raw JSON column text.
                try { links = JSON.parse(links); } catch (_) { links = []; }
            }
            if (!sid || !Array.isArray(links) || links.map(String).indexOf(sid) === -1) return false;
            if (w.scope === 'project') {
                var norm = function (p) { return String(p || '').replace(/\\/g, '/').replace(/\/+$/, ''); };
                return norm(getProjectPath()) !== norm(w.project_path);
            }
            return String(w.session_id || '') !== sid;
        }

        function renderTaskItems(items, shared, studio) {
            if (!items || !items.length) {
                return '<li class="chat-widget-task"><span class="chat-widget-task-text">(empty)</span></li>';
            }
            return items.map(function (item) {
                var kids = item.children || [];
                var nested = kids.length
                    ? '<ul class="chat-widget-task-list">' + renderTaskItems(kids, shared, studio) + '</ul>'
                    : '';
                return taskRowHtml(item, { shared: shared, studio: studio, ownerHtml: ownerHtml, childrenHtml: nested });
            }).join('');
        }

        /** Tasks lists a work panel in this page already shows (Studio hides their dock card). */
        function panelShownIds() {
            // No prototype: a list id such as "constructor" must not read as present.
            var ids = Object.create(null);
            var scope = (root && root.querySelectorAll) ? root : document;
            if (!scope || !scope.querySelectorAll) return ids;
            scope.querySelectorAll('.work-panel .work-list[data-list-id]').forEach(function (el) {
                ids[el.getAttribute('data-list-id')] = true;
            });
            return ids;
        }

        function render(focusWidgetId) {
            var el = ensureStrip();
            var studio = !!isStudio();
            var tasks = (widgets || []).filter(function (w) {
                return w && w.type === 'tasks' && (w.status || 'active') === 'active';
            });
            latestLists = tasks;
            // Work panels repaint only when the lists really changed (not on every poll).
            var listsSig = JSON.stringify(tasks);
            if (listsSig !== lastListsSig) {
                lastListsSig = listsSig;
                notifyPanels();
            }
            // Studio: a list the turn's work panel already shows is not repeated in the
            // dock. A shared list stays, because the panel is read-only and the dock is
            // where the user ticks its items. Classic never filters.
            var dock = tasks;
            if (studio) {
                var inPanel = panelShownIds();
                dock = tasks.filter(function (w) {
                    if (String(w.edit_mode || 'agent').toLowerCase() === 'shared') return true;
                    return !inPanel[String(w.id)];
                });
            }
            if (el) {
                if (!dock.length) {
                    el.hidden = true;
                    if (lastRenderedHtml) el.innerHTML = '';
                    lastRenderedHtml = '';
                }
            }
            if (!el || !dock.length) return;
            el.hidden = false;
            var projectName = String(getProjectName() || '').trim();
            var html = dock.map(function (w) {
                var payload = w.payload || {};
                var items = payload.items || [];
                var counts = countTasks(items);
                var isOpen = !!expanded[w.id];
                var shared = String(w.edit_mode || 'agent').toLowerCase() === 'shared';
                var linkedHere = isLinkedHere(w);
                var scopeLabel = linkedHere ? 'Linked' : ((w.scope === 'project') ? 'Project' : 'This chat');
                var scopeTip = linkedHere
                    ? 'Linked to this chat from another chat. Manage its chats in the Tasks app.'
                    : 'Pin to this chat or whole project';
                var editLabel = shared ? 'Shared' : 'Agent';
                var title = esc(displayTitle(w.title, linkedHere ? '' : projectName));
                var desc = String(w.description || '').trim();
                return (
                    '<div class="chat-widget' + (isOpen ? ' is-expanded' : '') +
                    (shared ? '' : ' is-agent-managed') +
                    '" data-widget-id="' + esc(w.id) + '">' +
                    '<button type="button" class="chat-widget-toggle" data-widget-toggle aria-expanded="' + (isOpen ? 'true' : 'false') + '">' +
                    '<span class="chat-widget-chevron" aria-hidden="true"></span>' +
                    '<span class="chat-widget-title">' + title + '</span>' +
                    '<span class="chat-widget-meta">' + counts.done + '/' + counts.total + '</span>' +
                    '</button>' +
                    '<div class="chat-widget-body" ' + (isOpen ? '' : 'hidden') + '>' +
                    '<div class="chat-widget-actions">' +
                    '<button type="button" class="chat-widget-scope" data-widget-edit data-tooltip="Agent-managed (default) or Shared (you can check boxes)">' +
                    editLabel +
                    '</button>' +
                    '<button type="button" class="chat-widget-scope" data-widget-scope data-tooltip="' + esc(scopeTip) + '">' +
                    scopeLabel +
                    '</button>' +
                    '<button type="button" class="chat-widget-action" data-widget-manage>Open in Tasks</button>' +
                    '<button type="button" class="chat-widget-action" data-widget-archive>Archive</button>' +
                    '</div>' +
                    (desc
                        ? '<p class="chat-widget-desc">' + esc(desc) + '</p>'
                        : '') +
                    '<ul class="chat-widget-task-list">' + renderTaskItems(items, shared, studio) + '</ul>' +
                    '</div></div>'
                );
            }).join('');
            // A status poll may fetch the same lists again. Leave their DOM
            // alone so scrolling (including touch momentum) is undisturbed.
            if (html === lastRenderedHtml) return;
            var positions = new Map();
            el.querySelectorAll('[data-widget-id]').forEach(function (card) {
                var body = card.querySelector('.chat-widget-body');
                if (body) positions.set(card.getAttribute('data-widget-id'), {
                    top: body.scrollTop, left: body.scrollLeft,
                });
            });
            var top = el.scrollTop;
            var left = el.scrollLeft;
            el.innerHTML = html;
            lastRenderedHtml = html;
            el.querySelectorAll('[data-widget-id]').forEach(function (card) {
                var id = card.getAttribute('data-widget-id');
                var position = positions.get(id);
                var body = card.querySelector('.chat-widget-body');
                if (position && body) {
                    body.scrollTop = position.top;
                    body.scrollLeft = position.left;
                }
                if (id === focusWidgetId) {
                    card.querySelector('[data-widget-toggle]').focus({ preventScroll: true });
                }
            });
            el.scrollTop = top;
            el.scrollLeft = left;
        }

        function applyData(data, expectedGen, expectedKey) {
            if (expectedGen != null && expectedGen !== fetchGen) return;
            if (expectedKey != null && expectedKey !== contextKey()) return;
            if (!data || !data.success) return;
            widgets = Array.isArray(data.gizmos) ? data.gizmos : [];
            // Empty chats report widgets_revision=0 — must not keep the prior chat's rev.
            revision = Number(data.revision || 0);
            lastFetchedKey = expectedKey != null ? expectedKey : contextKey();
            render();
        }

        function refresh() {
            var sid = sessionParam();
            var key = contextKey();
            if (!sid) {
                fetchGen += 1;
                clearStripImmediate();
                lastFetchedKey = key;
                return Promise.resolve();
            }
            // Switching chats/projects: drop the prior strip immediately so a
            // slow/stale in-flight response cannot keep showing the wrong To-do.
            if (key !== lastFetchedKey) {
                clearStripImmediate();
            }
            if (inFlight) {
                dirty = true;
                return Promise.resolve();
            }
            inFlight = true;
            var myGen = ++fetchGen;
            var proj = encodeURIComponent(getProjectPath() || '');
            var url = '/api/gizmos/tasks?session_id=' + encodeURIComponent(sid) +
                (proj ? ('&project_path=' + proj) : '');
            return fetchFn(url, { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (data) { applyData(data, myGen, key); })
                .catch(function () {})
                .finally(function () {
                    inFlight = false;
                    if (dirty) {
                        dirty = false;
                        refresh();
                    }
                });
        }

        function onRevision(rev) {
            // 0 is meaningful (no widgets). Prior chat's non-zero rev must not
            // block a refresh when live-status reports an empty strip.
            rev = Number(rev || 0);
            if (rev !== revision) refresh();
        }

        function patchWidget(id, body) {
            return fetchFn('/api/gizmos/tasks/' + encodeURIComponent(id), {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({session_id: sessionParam()}, body || {})),
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data && data.gizmo) {
                    var idx = widgets.findIndex(function (w) { return w.id === id; });
                    if (idx >= 0) widgets[idx] = data.gizmo;
                    else widgets.push(data.gizmo);
                    refresh();
                    render();
                } else {
                    refresh();
                }
            }).catch(function () { refresh(); });
        }

        function patchItem(widgetId, itemId, done) {
            return fetchFn(
                '/api/gizmos/tasks/' + encodeURIComponent(widgetId) +
                '/items/' + encodeURIComponent(itemId),
                {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ done: !!done, session_id: sessionParam() }),
                }
            ).then(function (r) { return r.json(); }).then(function (data) {
                if (data && data.gizmo) {
                    var idx = widgets.findIndex(function (w) { return w.id === widgetId; });
                    if (idx >= 0) widgets[idx] = data.gizmo;
                    refresh();
                    render();
                } else {
                    refresh();
                }
            }).catch(function () { refresh(); });
        }

        function bind() {
            var el = ensureStrip();
            if (!el || el.__cuttleWidgetsBound) return;
            el.__cuttleWidgetsBound = true;
            el.addEventListener('click', function (ev) {
                var t = ev.target;
                if (!t || !t.closest) return;
                var card = t.closest('[data-widget-id]');
                if (!card) return;
                var wid = card.getAttribute('data-widget-id');
                if (t.closest('[data-widget-toggle]')) {
                    expanded[wid] = !expanded[wid];
                    render(wid);
                    return;
                }
                if (t.closest('[data-widget-edit]')) {
                    var cur = widgets.find(function (x) { return x.id === wid; });
                    if (!cur) return;
                    var nextEdit = (String(cur.edit_mode || 'agent').toLowerCase() === 'shared')
                        ? 'agent'
                        : 'shared';
                    patchWidget(wid, {
                        edit_mode: nextEdit,
                        session_id: sessionParam(),
                        project_path: getProjectPath() || '',
                        op: 'patch',
                        patch: {},
                    });
                    return;
                }
                if (t.closest('[data-widget-manage]')) {
                    navigate('/tasks_page.html?list=' + encodeURIComponent(wid));
                    return;
                }
                if (t.closest('[data-widget-scope]')) {
                    var w = widgets.find(function (x) { return x.id === wid; });
                    if (!w) return;
                    if (isLinkedHere(w)) {
                        // Do not move another chat's list here; links are managed in the App.
                        navigate('/tasks_page.html?list=' + encodeURIComponent(wid) + '&tab=chats');
                        return;
                    }
                    var next = w.scope === 'project' ? 'session' : 'project';
                    var body = {
                        scope: next,
                        session_id: sessionParam(),
                        project_path: getProjectPath() || '',
                        op: 'patch',
                        patch: {},
                    };
                    patchWidget(wid, body);
                    return;
                }
                if (t.closest('[data-widget-archive]')) {
                    patchWidget(wid, { status: 'archived', op: 'patch', patch: {} });
                    return;
                }
            });
            el.addEventListener('change', function (ev) {
                var t = ev.target;
                if (!t || t.type !== 'checkbox') return;
                var row = t.closest('[data-item-id]');
                var card = t.closest('[data-widget-id]');
                if (!row || !card) return;
                if (card.classList.contains('is-agent-managed') || t.disabled) {
                    t.checked = !t.checked;
                    return;
                }
                patchItem(
                    card.getAttribute('data-widget-id'),
                    row.getAttribute('data-item-id'),
                    !!t.checked
                );
            });
        }

        bind();

        // A work panel mounted or removed in the chat (Studio) changes which lists the dock shows.
        if (typeof document !== 'undefined' && document.addEventListener) {
            document.addEventListener('cuttle-work-panels-changed', function () { render(); });
        }
        // Studio switched on or off: the owner line and the panel hiding follow at once.
        if (!options.isStudio && global.CuttleChatDisplay && typeof global.CuttleChatDisplay.onChange === 'function') {
            global.CuttleChatDisplay.onChange(function () { render(); });
        }

        return {
            refresh: refresh,
            onRevision: onRevision,
            clear: clearStripImmediate,
            getRevision: function () { return revision; },
            render: render,
        };
    }

    var api = { create: create, taskRowHtml: taskRowHtml, countTasks: countTasks, activeLists: activeLists };
    global.CuttleTaskGizmos = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
