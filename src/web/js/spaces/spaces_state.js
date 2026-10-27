/* ================================================================
   Cuttle Spaces — state model + persistence (spaces_state.js)
   Owner: Spaces subsystem. Pure domain logic: no document, no window,
   no localStorage access here (storage is injected). Loaded before
   app_shell.js; app_shell owns the singleton + DOM wiring and calls
   into `CuttleSpaces.*`.

   State shape:
     { active: <space id>,
       spaces: [{ id, name, root, groupId?, color?, projectOverride?, agentOverride? }],
       groups: [{ id, name, color, collapsed, projectOverride?, agentOverride? }] }
   `root` is the inactive-space layout tree (or null). The active space's
   live layout is owned by the shell, not this module.
   New-chat defaults: space override wins, else the tab's group, else the
   starred project/agent. Existing sessions are unchanged. This is a Spaces
   context-menu enhancement (color already ships), not an experimental flag.
   ================================================================ */
(function (root) {
    'use strict';

    const DEFAULT_COLOR = '#e8eaed';
    const COLORS = [
        { name: 'White', hex: '#e8eaed' },
        { name: 'Grey', hex: '#9aa0a6' },
        { name: 'Blue', hex: '#8ab4f8' },
        { name: 'Red', hex: '#f28b82' },
        { name: 'Yellow', hex: '#fdd663' },
        { name: 'Green', hex: '#81c995' },
        { name: 'Pink', hex: '#ff8bcb' },
        { name: 'Purple', hex: '#c58af9' },
        { name: 'Cyan', hex: '#78d9ec' },
        { name: 'Orange', hex: '#fcad70' },
        { name: 'Teal', hex: '#469990' },
        { name: 'Magenta', hex: '#f032e6' },
        { name: 'Lime', hex: '#bfef45' },
        { name: 'Brown', hex: '#9a6324' },
        { name: 'Navy', hex: '#000075' },
    ];
    const STORAGE_KEY = 'shell_spaces_v1';
    const MAX_NAME = 60;
    const MAX_GROUP_NAME = 40;

    function newSpaceId() {
        return 'sp_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }

    function newGroupId() {
        return 'sg_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }

    /** Only preset swatches persist; arbitrary strings never become colors. */
    function sanitizeColor(hex) {
        const want = String(hex || '').toLowerCase();
        const hit = COLORS.find((c) => c.hex.toLowerCase() === want);
        return hit ? hit.hex : null;
    }

    /** Project identity for new chats in a space/group. Junk records drop. */
    function sanitizeProjectOverride(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const id = raw.id != null ? raw.id : (raw.projectId != null ? raw.projectId : null);
        const path = String(raw.path || raw.projectPath || '').trim();
        const name = String(raw.name || raw.projectName || '').trim();
        if (id == null && !path && !name) return null;
        const out = { name: name || 'Project' };
        if (id != null && String(id) !== '') out.id = id;
        if (path) out.path = path;
        return out;
    }

    /** Sticky-agent identity for new chats in a space/group. Requires a prefix. */
    function sanitizeAgentOverride(raw) {
        if (!raw || typeof raw !== 'object') return null;
        let prefix = String(raw.prefix || raw.slash || '').trim();
        if (!prefix && raw.id) prefix = '/' + String(raw.id).trim();
        if (!prefix) return null;
        if (prefix.charAt(0) !== '/') prefix = '/' + prefix;
        if (prefix.charAt(prefix.length - 1) !== ' ') prefix += ' ';
        const out = {
            prefix,
            label: String(raw.label || prefix).trim() || prefix.trim(),
            category: String(raw.category || raw.id || 'command'),
        };
        const id = String(raw.id || '').trim().toLowerCase();
        if (id) out.id = id;
        const model = String(raw.model || '').trim();
        const effort = String(raw.effort || '').trim();
        if (model) out.model = model;
        if (effort) out.effort = effort;
        return out;
    }

    function attachOverrides(entry, raw) {
        const project = sanitizeProjectOverride(raw && raw.projectOverride);
        const agent = sanitizeAgentOverride(raw && raw.agentOverride);
        if (project) entry.projectOverride = project;
        if (agent) entry.agentOverride = agent;
        return entry;
    }

    /**
     * New-chat project/agent for a space: the space's own override, else
     * its group's, else null (caller falls through to starred defaults).
     * Fields inherit independently.
     */
    function resolveNewChatDefaults(state, spaceId) {
        const space = getSpaceById(state, spaceId || (state && state.active));
        if (!space) return { project: null, agent: null };
        const group = space.groupId ? getGroupById(state, space.groupId) : null;
        return {
            project: space.projectOverride || (group && group.projectOverride) || null,
            agent: space.agentOverride || (group && group.agentOverride) || null,
        };
    }

    function getSpaceById(state, id) {
        if (!state || !id) return null;
        return (state.spaces || []).find((s) => s && s.id === id) || null;
    }

    function getGroupById(state, gid) {
        if (!state || !gid) return null;
        return (state.groups || []).find((g) => g && g.id === gid) || null;
    }

    function createDefaultState() {
        const id = newSpaceId();
        return { active: id, spaces: [{ id, name: 'Space 1', root: null }], groups: [] };
    }

    /**
     * Pure core of the load path: validate + clean parsed JSON into state.
     * Returns null when the payload is unusable (caller falls back to default).
     * Rules (frozen from app_shell readSpacesState): groups deduped by id,
     * names sliced, colors sanitized with default fallback, unknown groupIds
     * dropped, spaces without ids dropped, active falls back to first space.
     */
    function sanitizeLoadedData(data) {
        try {
            if (!data || !Array.isArray(data.spaces) || !data.spaces.length) return null;
            const groupById = new Map();
            const cleanGroups = [];
            (Array.isArray(data.groups) ? data.groups : []).forEach((g) => {
                if (!g || !g.id || groupById.has(String(g.id))) return;
                const clean = attachOverrides({
                    id: String(g.id),
                    name: String(g.name || '').slice(0, MAX_GROUP_NAME),
                    color: sanitizeColor(g.color) || DEFAULT_COLOR,
                    collapsed: !!g.collapsed,
                }, g);
                groupById.set(clean.id, clean);
                cleanGroups.push(clean);
            });
            const spaces = data.spaces
                .filter((s) => s && s.id)
                .map((s) => {
                    const entry = {
                        id: String(s.id),
                        name: String(s.name || 'Space').slice(0, MAX_NAME),
                        root: s.root && typeof s.root === 'object' ? s.root : null,
                    };
                    if (s.groupId && groupById.has(String(s.groupId))) {
                        entry.groupId = String(s.groupId);
                    }
                    const color = sanitizeColor(s.color);
                    if (color) entry.color = color;
                    attachOverrides(entry, s);
                    return entry;
                });
            if (!spaces.length) return null;
            const active = spaces.some((s) => s.id === data.active) ? data.active : spaces[0].id;
            return { active, spaces, groups: cleanGroups };
        } catch (_) {
            return null;
        }
    }

    /** Load with injected storage ({getItem} or null). Never throws. */
    function loadSpacesState(storage) {
        try {
            const raw = storage ? storage.getItem(STORAGE_KEY) : null;
            const clean = sanitizeLoadedData(JSON.parse(raw || 'null'));
            if (clean) return clean;
        } catch (_) {}
        return createDefaultState();
    }

    /** Save with injected storage ({setItem} or null). Never throws. */
    function saveSpacesState(storage, state) {
        try {
            if (storage) storage.setItem(STORAGE_KEY, JSON.stringify(state));
            return true;
        } catch (_) {
            return false;
        }
    }

    /** Next "Space N" name avoiding (case-insensitive) collisions. */
    function nextSpaceName(spaces) {
        const list = Array.isArray(spaces) ? spaces : [];
        const taken = new Set(list.map((s) => String((s && s.name) || '').toLowerCase()));
        let n = list.length + 1;
        while (taken.has('space ' + n)) n += 1;
        return 'Space ' + n;
    }

    /** Pure state transition: append a space. Returns the new entry. */
    function addSpaceToState(state, entry) {
        const space = entry && entry.id
            ? { id: String(entry.id), name: String(entry.name || 'Space').slice(0, MAX_NAME), root: entry.root || null }
            : { id: newSpaceId(), name: nextSpaceName(state.spaces), root: null };
        state.spaces.push(space);
        return space;
    }

    /**
     * Pure state transition: plan closing a space.
     * Returns { ok, switchTo } — switchTo is the neighbor the shell must
     * activate first when closing the active space, else null.
     */
    function planSpaceClose(state, id) {
        const list = state.spaces || [];
        if (list.length < 2) return { ok: false, switchTo: null };
        const idx = list.findIndex((s) => s && s.id === id);
        if (idx < 0) return { ok: false, switchTo: null };
        let switchTo = null;
        if (id === state.active) {
            const neighbor = list[idx + 1] || list[idx - 1];
            if (!neighbor) return { ok: false, switchTo: null };
            switchTo = neighbor.id;
        }
        return { ok: true, switchTo };
    }

    /** Pure state transition: remove a closed space. Returns true if removed. */
    function commitSpaceClose(state, id) {
        const list = state.spaces || [];
        const idx = list.findIndex((s) => s && s.id === id);
        if (idx < 0) return false;
        list.splice(idx, 1);
        return true;
    }

    /** Pure state transition: rename. Returns true if renamed. */
    function renameSpaceInState(state, id, name) {
        const space = getSpaceById(state, id);
        const clean = String(name || '').trim().slice(0, MAX_NAME);
        if (!space || !clean) return false;
        space.name = clean;
        return true;
    }

    const api = {
        DEFAULT_COLOR,
        COLORS,
        STORAGE_KEY,
        newSpaceId,
        newGroupId,
        sanitizeColor,
        sanitizeProjectOverride,
        sanitizeAgentOverride,
        resolveNewChatDefaults,
        getSpaceById,
        getGroupById,
        createDefaultState,
        sanitizeLoadedData,
        loadSpacesState,
        saveSpacesState,
        nextSpaceName,
        addSpaceToState,
        planSpaceClose,
        commitSpaceClose,
        renameSpaceInState,
    };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
