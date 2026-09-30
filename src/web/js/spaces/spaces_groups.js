/* ================================================================
   Cuttle Spaces — group transitions (spaces_groups.js)
   Owner: Spaces subsystem. Pure state transitions on the state object
   owned by the shell (`{ active, spaces, groups }`): no DOM, no globals,
   no persistence, no rendering. The caller persists + re-renders.
   Semantics frozen from app_shell.js group functions.
   ================================================================ */
(function (root) {
    'use strict';

    function _state() {
        if (typeof module !== 'undefined' && typeof require === 'function') {
            return require('./spaces_state.js');
        }
        return root.CuttleSpaces || {};
    }

    function ensureGroupsArray(state) {
        if (!Array.isArray(state.groups)) state.groups = [];
        return state.groups;
    }

    /** Drop groups with no member tabs. Returns true when anything changed. */
    function pruneEmptyGroups(state) {
        const groups = ensureGroupsArray(state);
        if (!groups.length) return false;
        const used = new Set((state.spaces || []).map((s) => s && s.groupId).filter(Boolean));
        const kept = groups.filter((g) => g && used.has(g.id));
        if (kept.length !== groups.length) {
            state.groups = kept;
            return true;
        }
        return false;
    }

    /**
     * Keep a group's tabs adjacent: move the space right after the last
     * member. A first/only member stays where it is (Chrome parity).
     */
    function moveNextToGroup(state, spaceId, gid) {
        const list = state.spaces || [];
        const idx = list.findIndex((s) => s && s.id === spaceId);
        if (idx < 0) return false;
        let last = -1;
        for (let i = 0; i < list.length; i++) {
            if (list[i] && list[i].id !== spaceId && list[i].groupId === gid) last = i;
        }
        if (last < 0) return false;
        const [space] = list.splice(idx, 1);
        list.splice(last > idx ? last : last + 1, 0, space);
        return true;
    }

    /**
     * Assign a group (null/undefined clears). Clicking the current group
     * removes the space from it. Returns true when applied.
     */
    function setGroup(state, spaceId, gid) {
        const S = _state();
        const space = S.getSpaceById(state, spaceId);
        if (!space) return false;
        if (gid && space.groupId === gid) gid = null;
        if (gid && !S.getGroupById(state, gid)) return false;
        if (gid) {
            space.groupId = gid;
            moveNextToGroup(state, spaceId, gid);
        } else {
            delete space.groupId;
        }
        return true;
    }

    /** Create a group for a space. Returns the group or null. */
    function createGroupForSpace(state, spaceId, newId, defaultColor) {
        const S = _state();
        const space = S.getSpaceById(state, spaceId);
        if (!space) return null;
        const group = {
            id: newId || S.newGroupId(),
            name: '',
            color: defaultColor || S.DEFAULT_COLOR,
        };
        ensureGroupsArray(state).push(group);
        space.groupId = group.id;
        moveNextToGroup(state, spaceId, group.id);
        return group;
    }

    /** Remove a space from its group. Returns true when applied. */
    function removeFromGroup(state, spaceId) {
        const S = _state();
        const space = S.getSpaceById(state, spaceId);
        if (!space || !space.groupId) return false;
        delete space.groupId;
        return true;
    }

    /** Rename a group (40 chars). No-op when missing. */
    function renameGroup(state, gid, name) {
        const S = _state();
        const group = S.getGroupById(state, gid);
        if (!group) return false;
        group.name = String(name || '').trim().slice(0, 40);
        return true;
    }

    /** Set group color (preset or default fallback). Returns false when missing. */
    function setGroupColor(state, gid, hex) {
        const S = _state();
        const group = S.getGroupById(state, gid);
        if (!group) return false;
        group.color = S.sanitizeColor(hex) || S.DEFAULT_COLOR;
        return true;
    }

    /** Set space accent (preset; invalid clears). Returns false when missing. */
    function setSpaceColor(state, spaceId, hex) {
        const S = _state();
        const space = S.getSpaceById(state, spaceId);
        if (!space) return false;
        const clean = S.sanitizeColor(hex);
        if (clean) space.color = clean;
        else delete space.color;
        return true;
    }

    /** Ungroup: dissolve the group, member spaces survive as plain tabs. */
    function dissolveGroup(state, gid) {
        const S = _state();
        if (!S.getGroupById(state, gid)) return false;
        (state.spaces || []).forEach((s) => {
            if (s && s.groupId === gid) delete s.groupId;
        });
        state.groups = ensureGroupsArray(state).filter((g) => g && g.id !== gid);
        return true;
    }

    /**
     * Collapse/expand. Collapsing a group that hides the active tab is
     * refused ({ ok:false, reason:'hides-active' }); never hides active.
     */
    function toggleGroupCollapsed(state, gid, activeId) {
        const S = _state();
        const group = S.getGroupById(state, gid);
        if (!group) return { ok: false, reason: 'missing' };
        if (!group.collapsed) {
            const hidesActive = (state.spaces || []).some(
                (s) => s && s.groupId === gid && s.id === (activeId !== undefined ? activeId : state.active)
            );
            if (hidesActive) return { ok: false, reason: 'hides-active' };
            group.collapsed = true;
        } else {
            group.collapsed = false;
        }
        return { ok: true, collapsed: group.collapsed };
    }

    /**
     * Delete group: dissolve it and report member ids (at least one space
     * survives — the caller closes members through the normal close path).
     */
    function planGroupDelete(state, gid) {
        const S = _state();
        if (!S.getGroupById(state, gid)) return null;
        const members = (state.spaces || [])
            .filter((s) => s && s.groupId === gid)
            .map((s) => s.id);
        dissolveGroup(state, gid);
        return members;
    }

    const api = {
        ensureGroupsArray,
        pruneEmptyGroups,
        moveNextToGroup,
        setGroup,
        createGroupForSpace,
        removeFromGroup,
        renameGroup,
        setGroupColor,
        setSpaceColor,
        dissolveGroup,
        toggleGroupCollapsed,
        planGroupDelete,
    };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
