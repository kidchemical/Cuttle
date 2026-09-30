/* ================================================================
   Cuttle Spaces — canonical drop target (spaces_drop.js)
   Owner: Spaces subsystem. ONE interpretation of pointer state:

     pointer + layout + current state
             ↓
     computeDropTarget()
             ↓
     canonical { order, groupId }
             ↓
     preview (positions tab + sleeve highlight) AND commit (mutates
     state) consume the same computed target.

   This replaces three competing rules that used to disagree:
   - preview insertion (midpoint over tabs/sleeves),
   - preview join highlight (strict sleeve hit-test, no slop),
   - commit membership (neighbor inference + sleeve slop).
   Semantics frozen from setupSpaceTabDrag + reorderSpacesAroundHidden +
   fixDraggedTabGroup in app_shell.js:

   - Tabs and sleeves share one lane order; the pointer precedes the first
     lane whose midpoint it beats. The group pill is never an insertion
     point (a sleeve lane resolves to before its first visible member).
   - Aiming inside a sleeve span (same +6 end slop the membership rule
     uses) but resolving outside it parks the tab inside the sleeve end.
   - Dropping left of a sleeve parks outside (never joins); on/inside the
     sleeve joins (front slot included); between members joins; past the
     sleeve end (+6 slop) parks outside. Left slop is -4.
   - Dragging a member out ungroups it. Collapsed groups never gain.
   - A drop with no coordinates (NaN) keeps the conservative outcome.
   - All geometry is measured by the shell and passed in — this module
     never touches the DOM.
   ================================================================ */
(function (root) {
    'use strict';

    function _mods() {
        if (typeof module !== 'undefined' && typeof require === 'function') {
            return {
                state: require('./spaces_state.js'),
                order: require('./spaces_order.js'),
            };
        }
        return { state: root.CuttleSpaces || {}, order: root.CuttleSpaces || {} };
    }

    function _groupOf(groups, gid) {
        return (groups || []).find((g) => g && g.id === gid) || null;
    }

    function _visibleMembers(spaces, others, gid) {
        const set = new Set(others);
        return (spaces || [])
            .filter((s) => s && s.groupId === gid && set.has(s.id))
            .map((s) => s.id);
    }

    /**
     Compute the canonical drop target.

     @param {object} args
       spaces: full state order [{ id, groupId? }]
       groups: [{ id, collapsed }]
       visibleIds: rendered tab order (collapsed members absent)
       draggedId: id of the dragged tab
       lanes: DOM order of tab/sleeve lanes, dragged tab excluded:
              [{ t: 'tab', id } | { t: 'sleeve', gid }]
       tabRects: { id: { left, width } } measured tab geometry
       sleeveRects: { gid: { left, width } } measured sleeve spans
                    (collapsed sleeves included as lanes, never as join)
       x: viewport clientX (NaN allowed → conservative)
     @returns {{ order: string[], groupId: string|null, noop: boolean }}
       order = post-drop visible order; groupId = membership after drop.
     */
    function computeDropTarget(args) {
        const spaces = (args && args.spaces) || [];
        const groups = (args && args.groups) || [];
        const visibleIds = ((args && args.visibleIds) || []).slice();
        const draggedId = args && args.draggedId;
        const lanes = ((args && args.lanes) || []).filter(
            (l) => l && !(l.t === 'tab' && l.id === draggedId)
        );
        const tabRects = (args && args.tabRects) || {};
        const sleeveRects = (args && args.sleeveRects) || {};
        const x = args ? args.x : NaN;

        const current = spaces.find((s) => s && s.id === draggedId) || null;
        const currentGroup = (current && current.groupId) || null;

        if (!draggedId || visibleIds.indexOf(draggedId) < 0) {
            return { order: visibleIds, groupId: currentGroup, noop: true };
        }

        const others = visibleIds.filter((id) => id !== draggedId);
        const rectOf = (lane) => (lane.t === 'tab' ? tabRects[lane.id] : sleeveRects[lane.gid]) || null;

        // 1. Insertion lane: first lane whose midpoint the pointer precedes.
        const before = lanes.find((lane) => {
            const r = rectOf(lane);
            return !!r && x < r.left + r.width / 2;
        });

        // Resolve the lane to a visible index. A sleeve lane parks before
        // its first visible member (the pill itself is never an insertion
        // point); a member-less (collapsed) sleeve parks before the next
        // visible tab after it, else at the end.
        let at = others.length;
        if (before) {
            if (before.t === 'tab') {
                const i = others.indexOf(before.id);
                at = i < 0 ? others.length : i;
            } else {
                const members = _visibleMembers(spaces, others, before.gid);
                if (members.length) {
                    at = others.indexOf(members[0]);
                } else {
                    const bi = lanes.indexOf(before);
                    const nextTab = lanes.slice(bi + 1).find((l) => l.t === 'tab');
                    if (nextTab) {
                        const i = others.indexOf(nextTab.id);
                        at = i < 0 ? others.length : i;
                    } else {
                        at = others.length;
                    }
                }
            }
        }

        // 2. Sleeve-end park: aiming inside a non-collapsed sleeve span
        //    (with the same +6 end slop the membership rule uses) but
        //    resolving outside it parks the tab inside the sleeve end.
        //    `before` being the sleeve itself (or one of its members) does
        //    NOT trigger the park — it keeps its lane-resolved slot.
        let joinSleeve = null;
        for (const lane of lanes) {
            if (!lane || lane.t !== 'sleeve') continue;
            const g = _groupOf(groups, lane.gid);
            if (!g || g.collapsed) continue;
            const r = sleeveRects[lane.gid];
            if (r && x >= r.left && x <= r.left + r.width + 6) {
                joinSleeve = lane.gid;
                break;
            }
        }
        if (joinSleeve) {
            const members = _visibleMembers(spaces, others, joinSleeve);
            // The old guard parks only when `before` resolved OUTSIDE the
            // sleeve (before.closest(sleeve) !== joinSleeve): `before` being
            // the sleeve marker itself (pointer in [left, mid)) or one of its
            // member tabs keeps its lane-resolved slot.
            const beforeInSleeve = !!before && (before.t === 'sleeve'
                ? before.gid === joinSleeve
                : members.indexOf(before.id) >= 0);
            if (!beforeInSleeve) {
                const lastMember = members[members.length - 1];
                if (lastMember) at = others.indexOf(lastMember) + 1;
            }
        }

        // Preview directive: the exact DOM placement the old pointermove
        // performed for this lane resolution (park appends inside the sleeve
        // end; a sleeve lane inserts before the sleeve element; a tab lane
        // inserts before that tab; otherwise the strip end). The commit path
        // ignores `place` — it consumes order + groupId only.
        let place = { end: true };
        if (joinSleeve && !(before && ((before.t === 'sleeve' && before.gid === joinSleeve) ||
                (before.t === 'tab' && _visibleMembers(spaces, others, joinSleeve).indexOf(before.id) >= 0)))) {
            place = { park: joinSleeve };
        } else if (before) {
            place = before.t === 'tab' ? { beforeTab: before.id } : { beforeSleeve: before.gid };
        }

        const visibleOrder = others.slice(0, at).concat([draggedId], others.slice(at));

        // 3. Membership: neighbor rule on the resulting FULL order (hidden
        //    members keep their slots, so neighbors may be hidden — exactly
        //    like the old commit path that read post-preview state order).
        const M = _mods();
        const allIds = spaces.map((s) => s && s.id);
        const fullOrder = M.order.computeReorderedIds(allIds, visibleOrder, draggedId) || allIds;
        const byId = new Map(spaces.map((s) => [s && s.id, s]));
        const i = fullOrder.indexOf(draggedId);
        const left = i > 0 ? byId.get(fullOrder[i - 1]) || null : null;
        const right = i >= 0 && i < fullOrder.length - 1 ? byId.get(fullOrder[i + 1]) || null : null;
        const leftGroup = left && left.groupId ? _groupOf(groups, left.groupId) : null;
        const rightGroup = right && right.groupId ? _groupOf(groups, right.groupId) : null;
        const spanOf = (gid) => {
            const r = sleeveRects[gid];
            return r ? { left: r.left, right: r.left + r.width } : null;
        };
        const sleeveStart = (gid) => {
            const r = spanOf(gid);
            if (!r) return false;
            return x >= r.left - 4;
        };
        const pastSleeveEnd = (gid) => {
            const r = spanOf(gid);
            if (!r) return false;
            return x > r.right + 6;
        };
        let want = null;
        if (leftGroup && rightGroup && leftGroup.id === rightGroup.id) {
            if (!leftGroup.collapsed) want = leftGroup.id;
        } else if (leftGroup && !rightGroup) {
            if (!leftGroup.collapsed && !pastSleeveEnd(leftGroup.id)) want = leftGroup.id;
        } else if (!leftGroup && rightGroup) {
            if (!rightGroup.collapsed) {
                if (currentGroup === rightGroup.id) {
                    // Already in: stay, unless explicitly dropped left of the sleeve.
                    const r = spanOf(rightGroup.id);
                    if (!(r && x < r.left - 4)) want = rightGroup.id;
                } else if (sleeveStart(rightGroup.id)) {
                    want = rightGroup.id;
                }
            }
        }

        return { order: visibleOrder, groupId: want, noop: false, place };
    }

    /**
     * Commit a computed target to state. Returns
     * { orderChanged, groupChanged, pruned }. The caller persists once.
     */
    function applyDropTarget(state, target, draggedId) {
        const M = _mods();
        let orderChanged = false;
        let groupChanged = false;
        if (target && !target.noop) {
            orderChanged = M.order.applyVisibleOrder(state, target.order, draggedId);
            const space = M.state.getSpaceById(state, draggedId);
            if (space) {
                const have = space.groupId || null;
                const want = (target && target.groupId) || null;
                if (have !== want) {
                    if (want) space.groupId = want;
                    else delete space.groupId;
                    groupChanged = true;
                }
            }
        }
        const pruned = M.state && M.state.pruneEmptyGroups
            ? M.state.pruneEmptyGroups(state)
            : _pruneEmptyGroups(state);
        return { orderChanged, groupChanged, pruned };
    }

    function _pruneEmptyGroups(state) {
        if (!Array.isArray(state.groups) || !state.groups.length) return false;
        const used = new Set((state.spaces || []).map((s) => s && s.groupId).filter(Boolean));
        const kept = state.groups.filter((g) => g && used.has(g.id));
        if (kept.length !== state.groups.length) {
            state.groups = kept;
            return true;
        }
        return false;
    }

    const api = { computeDropTarget, applyDropTarget };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
