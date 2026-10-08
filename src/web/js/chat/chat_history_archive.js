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

    // Archived-mark state decisions over an explicit tab-lifetime Set (the
    // page owns the Set; `extra` is the page's id-form adapter — raw plus
    // frame-canonical plus auth-db numeric forms — so every form of one
    // session marks, unmarks, and compares equal).
    function markArchivedIds(markedSet, id, extra) {
        if (!markedSet || id == null || id === '') return;
        idKeys(id, extra).forEach((k) => markedSet.add(k));
    }

    function unmarkArchivedIds(markedSet, id, extra) {
        if (!markedSet || id == null || id === '') return;
        idKeys(id, extra).forEach((k) => markedSet.delete(k));
    }

    function isArchivedId(markedSet, id, extra) {
        return isActiveId(markedSet, id, extra);
    }

    // Collapsed-state decisions for the Archived section. `storage` is the
    // page's storage object (localStorage in browsers, a stub in tests) —
    // the module never touches a page global directly. Defaults to
    // collapsed on any failure; writes swallow errors.
    var ARCHIVE_SECTION_COLLAPSE_KEY = 'cuttleArchiveSectionCollapsed';

    function isArchiveSectionCollapsed(storage) {
        try {
            if (!storage || typeof storage.getItem !== 'function') return true;
            return storage.getItem(ARCHIVE_SECTION_COLLAPSE_KEY) !== '0';
        } catch (_) {
            return true;
        }
    }

    // Stores the NEW collapsed state (`true` → collapsed): '0' reads back
    // as expanded, anything else as collapsed. Callers pass the flipped
    // current state, mirroring the page's original inline toggle.
    function storeArchiveSectionCollapsed(storage, collapsed) {
        try {
            if (storage && typeof storage.setItem === 'function') {
                storage.setItem(ARCHIVE_SECTION_COLLAPSE_KEY, collapsed ? '1' : '0');
            }
        } catch (_) {}
    }

    // Section markup as pure strings over explicit inputs. The row items
    // stay with the page (it owns `createAuthHistoryItemHTML`); the header
    // template lives here so the Archived section reads as one owner piece.
    // `toggleHandler` / `keyHandler` arrive as attribute strings from the
    // page (it owns the window-facing toggle names).
    function archivedSectionClassName(collapsed) {
        return 'history-section history-archived-section' + (collapsed ? ' is-collapsed' : '');
    }

    function archivedSectionHeaderHTML(opts) {
        const o = opts || {};
        const collapsed = !!o.collapsed;
        const count = Number(o.count) || 0;
        const toggleAttr = o.toggleHandler ? ' onclick="' + o.toggleHandler + '"' : '';
        const keyAttr = o.keyHandler ? ' onkeydown="' + o.keyHandler + '"' : '';
        return '<div class="history-section-header is-collapsible" role="button" tabindex="0"'
            + ' aria-expanded="' + (collapsed ? 'false' : 'true') + '"'
            + ' aria-label="Archived chats"'
            + toggleAttr + keyAttr + '>'
            + '<span class="history-section-chevron" aria-hidden="true">'
            + '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
            + '<polyline points="6 9 12 15 18 9"/>'
            + '</svg>'
            + '</span>'
            + '<div class="history-section-title">Archived'
            + '<span class="history-section-count" title="' + count + ' archived chat' + (count === 1 ? '' : 's') + '">' + count + '</span>'
            + '</div>'
            + '<div class="history-section-actions"></div>'
            + '</div>';
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
        markArchivedIds,
        unmarkArchivedIds,
        isArchivedId,
        isArchiveSectionCollapsed,
        storeArchiveSectionCollapsed,
        archivedSectionClassName,
        archivedSectionHeaderHTML,
    };

    const ns = (root.CuttleChatHistoryArchive = root.CuttleChatHistoryArchive || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
