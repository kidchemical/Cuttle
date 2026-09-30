/* ================================================================
   Cuttle Spaces — ordering (spaces_order.js)
   Owner: Spaces subsystem. Pure order computation: hidden (collapsed)
   members keep their slots; only visible tabs participate. No DOM,
   no globals. The shell applies the result + persists + re-renders.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Pure core of the reorder commit: given the visible tab order AFTER the
     * drop (preview order) and the dragged id, compute the new FULL id order.
     * The dragged tab anchors after its left visible neighbor; hidden members
     * keep their slots. Returns null when nothing would change.
     */
    function computeReorderedIds(allIds, visibleIds, draggedId) {
        const list = (allIds || []).slice();
        const vi = (visibleIds || []).indexOf(draggedId);
        if (vi < 0) return null;
        if (!list.includes(draggedId)) return null;
        const without = list.filter((id) => id !== draggedId);
        let at = 0;
        if (vi > 0) {
            const li = without.indexOf(visibleIds[vi - 1]);
            at = li < 0 ? without.length : li + 1;
        }
        const next = without.slice(0, at).concat([draggedId], without.slice(at));
        if (next.every((id, idx) => id === list[idx])) return null;
        return next;
    }

    /**
     * Apply a visible order to state (full list incl. hidden). Returns true
     * when the order changed. `visibleIds` is the post-drop visible order.
     */
    function applyVisibleOrder(state, visibleIds, draggedId) {
        const allIds = (state.spaces || []).map((s) => s && s.id);
        const next = computeReorderedIds(allIds, visibleIds, draggedId);
        if (!next) return false;
        const byId = new Map((state.spaces || []).map((s) => [s && s.id, s]));
        state.spaces = next.map((id) => byId.get(id));
        return true;
    }

    const api = { computeReorderedIds, applyVisibleOrder };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
