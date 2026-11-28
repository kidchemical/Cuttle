/* ================================================================
   Cuttle Chat — Studio work panel (chat_work_panel.js). Pure.
   The block at the end of a turn's bubble: the turn's Tasks lists on the
   left (owner dot + name per item, a glowing unticked box while an item is
   in progress) and the sub-agent fleet cards on the right. It stacks to one
   column when the pane is narrow (CSS container query).

   Binding (never rewrites older bubbles): a Tasks list shows in a turn's
   bubble only when its `origin_turn_id` is that turn, or when one of its items
   is owned by a child chat of this turn's sub-agent batch. Lists the user
   merely linked to the chat are not shown here.

   model(lists, fleetEntries, turnId)  → {turnId, tasks, cards, hasTasks, hasCards, empty}
   html(model)                          → '' or the panel markup
   agentHeaderHtml(agent, opts)         → Studio bubble header: identity + working tag
   markStopped(bubble)                  → a stopped live bubble drops its working tag
   setCatalog(rows) / ownerHtml(owner)  → agent colours from the /api/agents catalog

   Dependencies are read lazily: browser globals (CuttleAgentIdentity,
   CuttleTaskGizmos, CuttleChatSubagentFleet) or require() under node.
   The page owns mounting (chat_subagent_launchers.js) and the class names
   chat_thread_model.js ignores (`.work-panel`). Experimental: chat_studio_display.
   ================================================================ */
(function (root) {
    'use strict';

    function dep(name, file) {
        if (typeof module !== 'undefined' && module.exports) return require(file);
        return root[name];
    }

    function identityLib() { return dep('CuttleAgentIdentity', '../shared/agent_identity.js'); }
    function tasksLib() { return dep('CuttleTaskGizmos', '../gizmos/tasks_gizmo.js'); }
    function fleetLib() { return dep('CuttleChatSubagentFleet', './chat_subagent_fleet.js'); }

    const STATES = { DONE: 'done', IN_PROGRESS: 'in_progress', OPEN: 'open' };

    /** The agent catalog rows (`GET /api/agents`), set once by the page's launcher controller. */
    let catalog = [];
    function setCatalog(rows) { catalog = Array.isArray(rows) ? rows : []; }
    function getCatalog() { return catalog; }

    function esc(value) { return identityLib().escapeHtml(value); }

    /** done ⇒ not in progress (the server keeps this invariant; guard anyway). Pure. */
    function itemState(item) {
        if (item && item.done) return STATES.DONE;
        if (item && item.in_progress === true) return STATES.IN_PROGRESS;
        return STATES.OPEN;
    }

    /**
     * {color, name, title} for an item owner. A chat or agent shows its agent
     * identity (harness colour and agent name), the child chat's title only as
     * `title` (tooltip); a person or group is neutral. Pure.
     */
    function ownerIdentity(owner, rows) {
        const A = identityLib();
        const o = owner && typeof owner === 'object' ? owner : {};
        const label = String(o.label || '').trim();
        const kind = String(o.kind || '');
        if (kind === 'chat' || kind === 'agent') {
            const id = A.forAgent(o.agent || '', rows);
            return { color: id.color, name: o.agent ? id.name : (label || id.name), title: label };
        }
        return { color: A.NEUTRAL_COLOR, name: label || (kind === 'user' ? 'You' : 'Group'), title: '' };
    }

    /** The owner as dot + agent name (escaped); the child title rides as the app's data-tooltip. */
    function ownerHtml(owner) {
        const id = ownerIdentity(owner, catalog);
        const tip = id.title && id.title !== id.name ? ' data-tooltip="' + esc(id.title) + '"' : '';
        return '<span class="work-owner"' + tip + '>' + identityLib().labelHtml(id) + '</span>';
    }

    function listItems(list) {
        const payload = list && list.payload && typeof list.payload === 'object' ? list.payload : {};
        const config = list && list.config && typeof list.config === 'object' ? list.config : {};
        if (Array.isArray(payload.items)) return payload.items;
        return Array.isArray(config.items) ? config.items : [];
    }

    function allItems(items, out) {
        (items || []).forEach((it) => {
            if (!it || typeof it !== 'object') return;
            out.push(it);
            allItems(it.children, out);
        });
        return out;
    }

    function originOf(list) {
        const payload = list && list.payload && typeof list.payload === 'object' ? list.payload : {};
        return String(list.origin_turn_id || payload.origin_turn_id || '');
    }

    function normHandle(raw) { return String(raw == null ? '' : raw).trim().toUpperCase(); }

    /** Whether a Tasks list belongs in this turn's bubble. Pure. */
    function listBound(list, turnId, handles) {
        if (!list || list.type !== 'tasks' || String(list.status || 'active') !== 'active') return false;
        const turn = String(turnId || '').trim();
        if (turn && originOf(list) === turn) return true;
        const kids = allItems(listItems(list), []);
        return kids.some((it) => it.owner && typeof it.owner === 'object'
            && it.owner.kind === 'chat' && handles.has(normHandle(it.owner.id)));
    }

    /** Items flattened for the rows list, with depth and state. Pure. */
    function flatRows(items, depth, rows, catalogRows) {
        (items || []).forEach((it) => {
            if (!it || typeof it !== 'object') return;
            rows.push({
                id: String(it.id || ''),
                text: String(it.text || ''),
                depth,
                state: itemState(it),
                owner: it.owner && typeof it.owner === 'object' ? ownerIdentity(it.owner, catalogRows) : null,
            });
            flatRows(it.children, depth + 1, rows, catalogRows);
        });
        return rows;
    }

    function doneCount(items) {
        const all = allItems(items, []);
        return { done: all.filter((it) => it.done).length, total: all.length };
    }

    /**
     * The panel's data for one turn. `lists`: Tasks gizmos (the dock's list
     * shape). `fleetEntries`: normalized sub-agent entries (`handle`, `agent`,
     * `outcome`, `summary`, …). `turnId`: the bubble's turn. Pure.
     */
    function model(lists, fleetEntries, turnId, opts) {
        const rows = opts && Array.isArray(opts.catalog) ? opts.catalog : catalog;
        const cards = (Array.isArray(fleetEntries) ? fleetEntries : []).filter((s) => s && typeof s === 'object');
        const handles = new Set(cards.map((s) => normHandle(s.handle)).filter(Boolean));
        const tasks = (Array.isArray(lists) ? lists : [])
            .filter((l) => listBound(l, turnId, handles))
            .map((l) => {
                const items = listItems(l);
                return {
                    id: String(l.id || ''),
                    title: String(l.title || 'Tasks').trim() || 'Tasks',
                    items,
                    counts: doneCount(items),
                    rows: flatRows(items, 0, [], rows),
                };
            });
        const hasTasks = tasks.length > 0;
        const hasCards = cards.length > 0;
        return {
            turnId: String(turnId || ''),
            tasks,
            cards,
            hasTasks,
            hasCards,
            empty: !hasTasks && !hasCards,
        };
    }

    /** Studio's agent identity inside the fleet cards and the tray header. */
    function agentIdentity(agent, rows) {
        return identityLib().forAgent(agent, rows);
    }

    function itemHtml(item) {
        if (item && item.hidden === true) return '';
        const kids = (Array.isArray(item.children) ? item.children : []).filter((k) => k && k.hidden !== true);
        const nested = kids.length
            ? '<ul class="chat-widget-task-list">' + kids.map(itemHtml).join('') + '</ul>'
            : '';
        // Work panel is a compact checklist; hide/unhide lives in the Tasks dock.
        return tasksLib().taskRowHtml(item, {
            studio: true,
            short: true,
            shared: false,
            hideable: false,
            ownerHtml: ownerHtml,
            childrenHtml: nested,
        });
    }

    function listHtml(task) {
        const count = task.counts.done + '/' + task.counts.total;
        const rows = (task.items || []).map(itemHtml).filter(Boolean).join('');
        return '<section class="work-list" data-list-id="' + esc(task.id) + '" aria-label="' + esc(task.title) + '">'
            + '<header class="work-list-head"><span class="work-list-title">' + esc(task.title) + '</span>'
            + '<span class="work-list-count">' + esc(count) + '</span></header>'
            + '<ul class="chat-widget-task-list work-task-list">' + rows + '</ul>'
            + '</section>';
    }

    /**
     * Panel markup for a model. Empty model → ''. The root carries
     * `data-work-panel` and `.work-panel`; the launcher mounts it at the end of
     * the bubble and repaints it in place.
     */
    function html(m, opts) {
        if (!m || m.empty) return '';
        const o = opts || {};
        const fleet = fleetLib();
        const cols = m.hasTasks && m.hasCards ? 'is-two' : (m.hasTasks ? 'is-tasks' : 'is-cards');
        const tasks = m.hasTasks
            ? '<div class="work-panel-tasks">' + m.tasks.map(listHtml).join('') + '</div>'
            : '';
        // The tray's "sub-agent of" names the turn's Tasks list (the parent a card's child reports to).
        const cards = m.hasCards
            ? '<div class="work-panel-cards">' + fleet.renderStudioCardsHtml(m.cards, {
                esc,
                identityHtml: (s) => identityLib().labelHtml(agentIdentity(s.agent, catalog)),
                selectedHandle: o.selectedHandle || '',
                parentTitle: m.hasTasks ? m.tasks[0].title : '',
            }) + '</div>'
            : '';
        const label = m.hasTasks && m.hasCards ? 'Tasks and sub-agents for this turn'
            : (m.hasTasks ? 'Tasks for this turn' : 'Sub-agents for this turn');
        return '<div class="work-panel" data-work-panel="1" data-turn-id="' + esc(m.turnId) + '"'
            + ' role="region" aria-label="' + esc(label) + '">'
            + '<div class="work-panel-cols ' + cols + '">' + tasks + cards + '</div></div>';
    }

    /**
     * Studio bubble header for an assistant turn: identity (dot + plain name)
     * and, while the turn runs, a "Working" tag. `opts`: {label, working, catalog}.
     * `label` is the chip label used when the agent is not in the catalog. Pure.
     */
    function agentHeaderHtml(agent, opts) {
        const o = opts || {};
        const id = String(agent || '').trim();
        const rows = Array.isArray(o.catalog) ? o.catalog : catalog;
        const row = id ? rows.find((r) => r && String(r.id) === id) : null;
        const fallback = String(o.label || '').split(/ - | · /)[0].trim();
        const name = row ? String(row.label || row.id) : (fallback || id || 'Agent');
        const color = identityLib().forAgent(id, rows).color;
        const working = o.working
            ? '<span class="studio-working-tag"><span class="studio-live-dot" aria-hidden="true"></span>Working</span>'
            : '';
        return '<span class="studio-agent-header" data-agent="' + esc(id) + '">'
            + identityLib().labelHtml({ color, name }) + working + '</span>';
    }

    /**
     * A live bubble was stopped: its Studio header drops the "Working" tag and
     * reads as stopped. No-op for a bubble without a Studio header (Classic).
     */
    function markStopped(bubble) {
        if (!bubble || !bubble.querySelectorAll) return;
        bubble.querySelectorAll('.studio-agent-header').forEach((head) => {
            head.querySelectorAll('.studio-working-tag').forEach((tag) => tag.remove());
            head.classList.add('is-stopped');
            head.setAttribute('aria-label', 'Stopped: ' + (head.textContent || '').trim());
        });
    }

    const api = {
        STATES, itemState, ownerIdentity, ownerHtml, listBound, model, html, agentHeaderHtml, markStopped,
        setCatalog, getCatalog,
    };

    root.CuttleWorkPanel = Object.assign(root.CuttleWorkPanel || {}, api);
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
