/* Chat widgets strip (Tasks) — durable panels above the composer. */
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

    function create(options) {
        options = options || {};
        var root = options.root || document;
        var getSessionId = options.getSessionId || function () { return null; };
        var getProjectPath = options.getProjectPath || function () { return ''; };
        var fetchFn = options.fetchFn || fetch;
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

        function renderTaskItems(items, shared) {
            if (!items || !items.length) {
                return '<li class="chat-widget-task"><span class="chat-widget-task-text">(empty)</span></li>';
            }
            return items.map(function (item) {
                var id = esc(item.id || '');
                var text = esc(item.text || '');
                var done = !!item.done;
                var kids = item.children || [];
                var nested = kids.length
                    ? '<ul class="chat-widget-task-list">' + renderTaskItems(kids, shared) + '</ul>'
                    : '';
                var disabled = shared ? '' : ' disabled';
                return (
                    '<li class="chat-widget-task' + (done ? ' is-done' : '') + '" data-item-id="' + id + '">' +
                    '<input type="checkbox" ' + (done ? 'checked ' : '') + disabled +
                    ' aria-label="' + (shared ? 'Toggle task' : 'Agent-managed task') + '"/>' +
                    '<div style="flex:1;min-width:0">' +
                    '<div class="chat-widget-task-text">' + text + '</div>' +
                    nested +
                    '</div></li>'
                );
            }).join('');
        }

        function render() {
            var el = ensureStrip();
            if (!el) return;
            var tasks = (widgets || []).filter(function (w) {
                return w && w.type === 'tasks' && (w.status || 'active') === 'active';
            });
            if (!tasks.length) {
                el.hidden = true;
                el.innerHTML = '';
                return;
            }
            el.hidden = false;
            el.innerHTML = tasks.map(function (w) {
                var payload = w.payload || {};
                var items = payload.items || [];
                var counts = countTasks(items);
                var isOpen = !!expanded[w.id];
                var shared = String(w.edit_mode || 'agent').toLowerCase() === 'shared';
                var scopeLabel = (w.scope === 'project') ? 'Project' : 'This chat';
                var editLabel = shared ? 'Shared' : 'Agent';
                var title = esc(w.title || 'Tasks');
                var desc = String(w.description || '').trim();
                var infoBtn = desc
                    ? (
                        '<button type="button" class="chat-widget-info" data-widget-info ' +
                        'aria-label="About this list" data-tooltip="' + esc(desc) + '">' +
                        '<span class="chat-widget-info-mark" aria-hidden="true">i</span>' +
                        '</button>'
                    )
                    : '';
                return (
                    '<div class="chat-widget' + (isOpen ? ' is-expanded' : '') +
                    (shared ? '' : ' is-agent-managed') +
                    '" data-widget-id="' + esc(w.id) + '">' +
                    '<div class="chat-widget-header">' +
                    '<button type="button" class="chat-widget-toggle" data-widget-toggle aria-expanded="' + (isOpen ? 'true' : 'false') + '">' +
                    '<span class="chat-widget-chevron" aria-hidden="true"></span>' +
                    '<span class="chat-widget-title">' + title + '</span>' +
                    '<span class="chat-widget-meta">' + counts.done + '/' + counts.total + '</span>' +
                    '</button>' +
                    infoBtn +
                    '<button type="button" class="chat-widget-scope" data-widget-edit data-tooltip="Agent-managed (default) or Shared (you can check boxes)">' +
                    editLabel +
                    '</button>' +
                    '<button type="button" class="chat-widget-scope" data-widget-scope data-tooltip="Pin to this chat or whole project">' +
                    scopeLabel +
                    '</button>' +
                    '<button type="button" class="chat-widget-archive" data-widget-archive data-tooltip="Archive">✕</button>' +
                    '</div>' +
                    '<div class="chat-widget-body" ' + (isOpen ? '' : 'hidden') + '>' +
                    (desc
                        ? '<p class="chat-widget-desc">' + esc(desc) + '</p>'
                        : '') +
                    '<ul class="chat-widget-task-list">' + renderTaskItems(items, shared) + '</ul>' +
                    '</div></div>'
                );
            }).join('');
        }

        function applyData(data, expectedGen, expectedKey) {
            if (expectedGen != null && expectedGen !== fetchGen) return;
            if (expectedKey != null && expectedKey !== contextKey()) return;
            if (!data || !data.success) return;
            widgets = Array.isArray(data.widgets) ? data.widgets : [];
            // Empty chats report widgets_revision=0 — must not keep the prior chat's rev.
            revision = Number(data.widgets_revision || 0);
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
            var url = '/api/widgets?session_id=' + encodeURIComponent(sid) +
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
            return fetchFn('/api/widgets/' + encodeURIComponent(id), {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data && data.widget) {
                    var idx = widgets.findIndex(function (w) { return w.id === id; });
                    if (idx >= 0) widgets[idx] = data.widget;
                    else widgets.push(data.widget);
                    revision = Math.max(revision, Number(data.widget.revision || 0));
                    render();
                } else {
                    refresh();
                }
            }).catch(function () { refresh(); });
        }

        function patchItem(widgetId, itemId, done) {
            return fetchFn(
                '/api/widgets/' + encodeURIComponent(widgetId) +
                '/items/' + encodeURIComponent(itemId),
                {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ done: !!done }),
                }
            ).then(function (r) { return r.json(); }).then(function (data) {
                if (data && data.widget) {
                    var idx = widgets.findIndex(function (w) { return w.id === widgetId; });
                    if (idx >= 0) widgets[idx] = data.widget;
                    revision = Math.max(revision, Number(data.widget.revision || 0));
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
                    render();
                    return;
                }
                if (t.closest('[data-widget-info]')) {
                    // Keep list collapsed/expanded as-is; tooltip is CSS/hover.
                    ev.preventDefault();
                    ev.stopPropagation();
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
                if (t.closest('[data-widget-scope]')) {
                    var w = widgets.find(function (x) { return x.id === wid; });
                    if (!w) return;
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

        return {
            refresh: refresh,
            onRevision: onRevision,
            clear: clearStripImmediate,
            getRevision: function () { return revision; },
            render: render,
        };
    }

    global.CuttleChatWidgets = { create: create };
})(typeof window !== 'undefined' ? window : this);
