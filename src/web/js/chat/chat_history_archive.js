// Archived-section reconciliation for the chat history panel.
//
// Pure decisions (no DOM, no fetch): the `?archived=only` fetch targets a
// backend that may predate the archive column — Flask serves new JS from
// disk immediately, but the new `/api/auth/sessions` filter only goes live
// after a restart. An old backend answers `?archived=only` with the FULL
// session list, which used to duplicate every chat into the Archived section
// (double fetch + double DOM on phones) and mark every session archived.
// These helpers keep the Archived section disjoint from the main list.
(function(root) {
    'use strict';

    // Canonical id key: mirrors the page's id forms (raw + frame-canonical
    // plus auth-db numeric id) so every form compares equal. `extra` may be
    // a scalar or an array.
    function idKeys(id, extra) {
        const keys = [];
        const push = (v) => {
            if (v == null || v === '') return;
            const k = String(v);
            if (keys.indexOf(k) === -1) keys.push(k);
        };
        push(id);
        (Array.isArray(extra) ? extra : [extra]).forEach(push);
        return keys;
    }

    // Build the active (main-list) id set from the full-list server sessions.
    // `rows` are `{id}` objects; `toAuthId` maps a frame id to its extra
    // forms (canonical and/or auth-db id, scalar or array, may be null).
    // Returns a Set of strings.
    function activeSessionIdSet(rows, toAuthId) {
        const set = new Set();
        (rows || []).forEach((s) => {
            const id = s && s.id;
            if (id == null || id === '') return;
            idKeys(id, typeof toAuthId === 'function' ? toAuthId(id) : null)
                .forEach((k) => set.add(k));
        });
        return set;
    }

    function isActiveId(activeSet, id, extra) {
        if (!activeSet || id == null || id === '') return false;
        return idKeys(id, extra).some((k) => activeSet.has(k));
    }

    // Drop `?archived=only` rows the main list already shows. A backend
    // without `?archived=` support returns the full list here — filtering
    // yields an empty Archived section instead of a duplicate panel.
    function filterArchivedSessions(archivedRows, activeSet, toAuthId) {
        if (!Array.isArray(archivedRows)) return [];
        return archivedRows.filter((s) => {
            const id = s && s.id;
            if (id == null || id === '') return false;
            const authId = typeof toAuthId === 'function' ? toAuthId(id) : null;
            return !isActiveId(activeSet, id, authId);
        });
    }

    // Prune stale archived marks: ids back in the main list (unarchived on
    // another device, or mis-marked by an unfiltered response) are not
    // archived. Returns the ids to unmark.
    function staleArchivedMarks(markedIds, activeSet) {
        const out = [];
        (markedIds || []).forEach((id) => {
            if (id != null && id !== '' && activeSet && activeSet.has(String(id))) {
                out.push(String(id));
            }
        });
        return out;
    }

    const api = {
        idKeys,
        activeSessionIdSet,
        isActiveId,
        filterArchivedSessions,
        staleArchivedMarks,
    };

    const ns = (root.CuttleChatHistoryArchive = root.CuttleChatHistoryArchive || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
