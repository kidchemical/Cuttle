/**
 * CuttleTasksModel — pure logic for the Tasks App (no DOM, no fetch).
 *
 * Lists come from GET /api/gizmos/tasks?all=1 (owner: api.gizmos.tasks +
 * tasks_manager). Item trees use the server shape {id, text, done, children}.
 * Tree edits mutate the draft array they are given and return a result the
 * controller can use to move focus; callers clone before editing.
 */
(function (root) {
    'use strict';

    const MAX_DEPTH = 16;

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function clone(value) { return JSON.parse(JSON.stringify(value == null ? null : value)); }

    function normalizeItems(items) {
        return (Array.isArray(items) ? items : []).filter((n) => n && typeof n === 'object').map((n) => ({
            id: String(n.id || ''),
            text: String(n.text == null ? '' : n.text),
            done: !!n.done,
            children: normalizeItems(n.children),
        }));
    }

    function count(items) {
        let done = 0;
        let total = 0;
        (function walk(nodes) {
            (nodes || []).forEach((n) => { total += 1; if (n.done) done += 1; walk(n.children); });
        })(items);
        return { done, total, pct: total ? Math.round((done / total) * 100) : 0 };
    }

    function allIds(items, out) {
        out = out || new Set();
        (items || []).forEach((n) => { out.add(String(n.id)); allIds(n.children, out); });
        return out;
    }

    function newId(items, rand) {
        const ids = allIds(items);
        const next = rand || (() => Math.random().toString(36).slice(2, 8));
        for (let i = 0; i < 50; i += 1) {
            const id = 'u-' + next();
            if (!ids.has(id)) return id;
        }
        return 'u-' + Date.now().toString(36);
    }

    /** {node, siblings, index, parent, depth} for an id, or null. */
    function locate(items, id, parent, depth) {
        const list = items || [];
        for (let i = 0; i < list.length; i += 1) {
            const node = list[i];
            if (String(node.id) === String(id)) return { node, siblings: list, index: i, parent: parent || null, depth: depth || 0 };
            const hit = locate(node.children, id, node, (depth || 0) + 1);
            if (hit) return hit;
        }
        return null;
    }

    function depthOf(node) {
        let max = 0;
        (node.children || []).forEach((c) => { max = Math.max(max, 1 + depthOf(c)); });
        return max;
    }

    /** Insert a new item after ``afterId`` (same level), or append under ``parentId``. */
    function addItem(items, opts) {
        opts = opts || {};
        const node = { id: opts.id || newId(items, opts.rand), text: String(opts.text || ''), done: false, children: [] };
        if (opts.afterId) {
            const at = locate(items, opts.afterId);
            if (at) { at.siblings.splice(at.index + 1, 0, node); return node.id; }
        }
        if (opts.parentId) {
            const at = locate(items, opts.parentId);
            if (at && at.depth < MAX_DEPTH - 1) { at.node.children.push(node); return node.id; }
        }
        items.push(node);
        return node.id;
    }

    /** Remove an item (and its sub-tasks). Returns the id to focus next, or ''. */
    function removeItem(items, id) {
        const at = locate(items, id);
        if (!at) return '';
        at.siblings.splice(at.index, 1);
        const near = at.siblings[at.index - 1] || at.siblings[at.index] || at.parent;
        return near ? String(near.id) : '';
    }

    function setText(items, id, text) {
        const at = locate(items, id);
        if (at) at.node.text = String(text == null ? '' : text);
        return !!at;
    }

    /** Toggle one item. Checking a parent checks its sub-tasks; unchecking a
     * sub-task unchecks its ancestors, so a parent never reads done over open work. */
    function setDone(items, id, done) {
        const at = locate(items, id);
        if (!at) return false;
        at.node.done = !!done;
        if (done) {
            (function walk(nodes) { (nodes || []).forEach((n) => { n.done = true; walk(n.children); }); })(at.node.children);
        } else {
            let path = ancestors(items, id);
            path.forEach((n) => { n.done = false; });
        }
        return true;
    }

    function ancestors(items, id) {
        const out = [];
        (function walk(nodes, trail) {
            for (const n of nodes || []) {
                if (String(n.id) === String(id)) { out.push(...trail); return true; }
                if (walk(n.children, trail.concat([n]))) return true;
            }
            return false;
        })(items, []);
        return out;
    }

    function move(items, id, delta) {
        const at = locate(items, id);
        if (!at) return false;
        const to = at.index + delta;
        if (to < 0 || to >= at.siblings.length) return false;
        const [node] = at.siblings.splice(at.index, 1);
        at.siblings.splice(to, 0, node);
        return true;
    }

    /** Make an item the last sub-task of its previous sibling. */
    function indent(items, id) {
        const at = locate(items, id);
        if (!at || at.index === 0) return false;
        if (at.depth + 1 + depthOf(at.node) >= MAX_DEPTH) return false;
        const prev = at.siblings[at.index - 1];
        at.siblings.splice(at.index, 1);
        prev.children.push(at.node);
        return true;
    }

    /** Move an item out one level, right after its parent. */
    function outdent(items, id) {
        const at = locate(items, id);
        if (!at || !at.parent) return false;
        const up = locate(items, at.parent.id);
        at.siblings.splice(at.index, 1);
        up.siblings.splice(up.index + 1, 0, at.node);
        return true;
    }

    /** Drop blank items that have no sub-tasks (what the editor saves). */
    function prune(items) {
        return (items || []).map((n) => ({ ...n, text: String(n.text || '').trim(), children: prune(n.children) }))
            .filter((n) => n.text || n.children.length);
    }

    function sameItems(a, b) {
        return JSON.stringify(prune(normalizeItems(a))) === JSON.stringify(prune(normalizeItems(b)));
    }

    // ── Lists ───────────────────────────────────────────────────────────

    function items(list) { return normalizeItems(((list && list.payload) || {}).items); }

    function parseTime(value) {
        if (value == null || value === '') return NaN;
        if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
        const s = String(value).trim();
        // SQLite CURRENT_TIMESTAMP is UTC without a zone marker.
        const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/.test(s) ? s.replace(' ', 'T') + 'Z' : s;
        return Date.parse(iso);
    }

    function relativeTime(value, now) {
        const t = parseTime(value);
        if (!Number.isFinite(t)) return '';
        const sec = Math.max(0, Math.round(((now == null ? Date.now() : now) - t) / 1000));
        if (sec < 45) return 'just now';
        const min = Math.round(sec / 60);
        if (min < 60) return `${min}m ago`;
        const hr = Math.round(min / 60);
        if (hr < 24) return `${hr}h ago`;
        const day = Math.round(hr / 24);
        if (day < 30) return `${day}d ago`;
        return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function projectName(path) {
        const parts = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        return parts[parts.length - 1] || String(path || '');
    }

    /** Short "where this list shows up" label for cards. */
    function reachLabel(list) {
        const links = (list.linked_chats || []).length;
        let base;
        if (list.scope === 'project') base = `Project · ${projectName(list.project_path)}`;
        else if (list.home_chat) base = list.home_chat.name;
        else base = 'Chat';
        return links ? `${base} +${links} linked` : base;
    }

    /** Every chat (with a role) the list appears in, home first. */
    function reachChats(list) {
        const out = [];
        const seen = new Set();
        if (list.home_chat) { out.push({ ...list.home_chat, role: 'home' }); seen.add(list.home_chat.id); }
        (list.linked_chats || []).forEach((c) => { if (!seen.has(c.id)) { out.push({ ...c, role: 'linked' }); seen.add(c.id); } });
        return out;
    }

    function chatsInProject(chats, path) {
        const want = String(path || '');
        return (chats || []).filter((c) => want && c.project_path === want);
    }

    function matches(list, q) {
        if (!q) return true;
        const text = [list.title, list.description, list.id, list.project_path,
            list.home_chat && list.home_chat.name, list.home_chat && list.home_chat.handle,
            ...(list.linked_chats || []).flatMap((c) => [c.name, c.handle])];
        (function walk(nodes) { (nodes || []).forEach((n) => { text.push(n.text); walk(n.children); }); })(items(list));
        return text.join(' \n ').toLowerCase().includes(q);
    }

    function filterLists(lists, opts) {
        opts = opts || {};
        const q = String(opts.query || '').trim().toLowerCase();
        const status = opts.status || 'active';
        return (lists || []).filter((l) => (status === 'all' || l.status === status) && matches(l, q));
    }

    function statusCounts(lists) {
        const out = { active: 0, archived: 0, all: 0 };
        (lists || []).forEach((l) => { out.all += 1; if (out[l.status] != null) out[l.status] += 1; });
        return out;
    }

    // ── History ─────────────────────────────────────────────────────────

    const OPERATION_LABELS = {
        create: 'Created', patch: 'Updated', update: 'Updated', delete: 'Deleted',
        get: 'Viewed', list: 'Listed', history: 'Read history',
    };

    function actorLabel(actor) {
        const a = actor || {};
        if (a.source === 'ui') return 'You';
        if (a.source === 'subagents') return 'Sub-agents';
        const who = a.agent_id || (a.source === 'cli' ? 'Agent' : a.source || 'Cuttle');
        return a.model ? `${who} · ${a.model}` : who;
    }

    function describeChange(before, after) {
        if (!after) return '';
        if (after.deleted) return 'List deleted';
        if (!before) {
            const c = count(items(after));
            return `${c.total} task${c.total === 1 ? '' : 's'}`;
        }
        const notes = [];
        if ((before.title || '') !== (after.title || '')) notes.push(`Renamed to “${after.title}”`);
        if (before.status !== after.status) notes.push(after.status === 'archived' ? 'Archived' : 'Restored');
        if ((before.edit_mode || 'agent') !== (after.edit_mode || 'agent')) notes.push(after.edit_mode === 'shared' ? 'Opened for your edits' : 'Made agent-managed');
        if (before.scope !== after.scope) notes.push(after.scope === 'project' ? 'Moved to project' : 'Moved to a chat');
        const bl = (before.linked_sessions || []).length;
        const al = (after.linked_sessions || []).length;
        if (bl !== al) notes.push(al > bl ? `Linked ${al - bl} chat${al - bl === 1 ? '' : 's'}` : `Unlinked ${bl - al} chat${bl - al === 1 ? '' : 's'}`);
        if ((before.description || '') !== (after.description || '')) notes.push('Description edited');
        const b = count(items(before));
        const c = count(items(after));
        if (c.done > b.done) notes.push(`Checked ${c.done - b.done}`);
        if (c.done < b.done) notes.push(`Unchecked ${b.done - c.done}`);
        if (c.total > b.total) notes.push(`Added ${c.total - b.total}`);
        if (c.total < b.total) notes.push(`Removed ${b.total - c.total}`);
        if (!notes.length && JSON.stringify(items(before)) !== JSON.stringify(items(after))) notes.push('Tasks edited');
        return notes.join(' · ');
    }

    function historyEntries(events, opts) {
        const withReads = !!(opts && opts.includeReads);
        return (events || []).filter((e) => withReads || e.after_state).map((e) => {
            const after = e.after_state;
            const c = after && !after.deleted ? count(items(after)) : null;
            return {
                sequence: e.sequence,
                label: OPERATION_LABELS[e.operation] || e.operation,
                operation: e.operation,
                actor: actorLabel(e.actor),
                source: (e.actor && e.actor.source) || '',
                time: e.created_at,
                detail: describeChange(e.before_state, after),
                progress: c ? `${c.done}/${c.total}` : '',
            };
        });
    }

    const RELATION_LABELS = {
        parent: 'Parent', ancestor: 'Ancestor', 'sub-agent': 'Sub-agent', sibling: 'Sibling',
        supervisor: 'Supervisor', supervised: 'Supervised',
    };

    const api = {
        MAX_DEPTH, esc, clone, normalizeItems, count, newId, locate, addItem, removeItem, setText,
        setDone, move, indent, outdent, prune, sameItems, items, parseTime, relativeTime, projectName,
        reachLabel, reachChats, chatsInProject, filterLists, statusCounts, actorLabel, describeChange,
        historyEntries, RELATION_LABELS,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleTasksModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
