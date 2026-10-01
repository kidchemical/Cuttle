/* ================================================================
   Cuttle Chat — prompt-history navigation (chat_prompt_history.js).
   Owner: explicit-state prompt recall decisions — record
   (trim/dedupe/cap), browse index stepping with draft capture/restore,
   session-merge choice, user-message derivation, caret-anchor
   predicates. Pure: no document, no window, no localStorage, no slash
   chips. The page holds the single state object plus all IO (storage,
   session id), DOM (textarea apply, caret reads, key events) and slash
   integration (draft composition, chip clearing); nothing here reads
   or writes page scope. Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** User-sent prompts for ArrowUp/ArrowDown recall (newest at end). */
    const PROMPT_HISTORY_CAP = 100;

    /** Single browse session: list + index (-1 = at live draft) + draft. */
    function createState() {
        return { list: [], index: -1, draft: '' };
    }

    function resetBrowse(state) {
        state.index = -1;
        state.draft = '';
    }

    /**
     * Record a sent prompt. Trims, skips empties and repeats of the
     * newest entry, caps length, resets any active browse.
     * @returns {boolean} true when the list changed
     */
    function appendRecord(state, composedMessage) {
        const m = String(composedMessage || '').trim();
        if (!m) return false;
        const list = state.list;
        if (list[list.length - 1] === m) return false;
        list.push(m);
        if (list.length > PROMPT_HISTORY_CAP) list.shift();
        resetBrowse(state);
        return true;
    }

    /**
     * Stash the live draft on the first older-step of a browse. The page
     * composes the draft (slash chips) and calls this only when
     * state.index === -1, mirroring the pre-extraction call site.
     */
    function captureDraft(state, draft) {
        if (state.index === -1) state.draft = String(draft ?? '');
    }

    /**
     * Step the browse index. direction -1 = older (Up), +1 = newer (Down).
     * @returns {{status:string, text?:string}} 'none' (key not consumed),
     *   'oldest' (consumed, already at oldest), 'text' / 'draft' (apply text).
     */
    function step(state, direction) {
        const n = state.list.length;
        if (!n) return { status: 'none' };
        if (direction < 0) {
            if (state.index >= n - 1) return { status: 'oldest' };
            state.index += 1;
            return { status: 'text', text: state.list[n - 1 - state.index] };
        }
        if (state.index < 0) return { status: 'none' };
        if (state.index === 0) {
            state.index = -1;
            return { status: 'draft', text: state.draft };
        }
        state.index -= 1;
        return { status: 'text', text: state.list[n - 1 - state.index] };
    }

    /**
     * Choose the surviving history when the server remaps session_id:
     * longer stored side wins, live list is the fallback, then cap.
     * Storage read/write/delete stays in the page.
     */
    function mergeSessionHistories(fromOld, fromNew, liveList) {
        const o = Array.isArray(fromOld) ? fromOld : [];
        const w = Array.isArray(fromNew) ? fromNew : [];
        let merged = o.length >= w.length ? o.slice() : w.slice();
        if (merged.length === 0 && Array.isArray(liveList) && liveList.length) {
            merged = liveList.slice();
        }
        while (merged.length > PROMPT_HISTORY_CAP) merged.shift();
        return merged;
    }

    /**
     * Rebuild recall from stored transcript user messages when no
     * dedicated history exists for the session (newest capped).
     */
    function deriveFromUserMessages(messages) {
        const fromMsgs = (Array.isArray(messages) ? messages : [])
            .filter((m) => m && m.role === 'user')
            .map((m) => String(m.content || '').trim())
            .filter(Boolean);
        return fromMsgs.length > PROMPT_HISTORY_CAP
            ? fromMsgs.slice(-PROMPT_HISTORY_CAP)
            : fromMsgs.slice();
    }

    /** Caret collapsed at offset 0 — ArrowUp walks into history. */
    function atStartAnchor(selStart, selEnd) {
        return selStart === selEnd && selStart === 0;
    }

    /** Caret collapsed at end of text — ArrowDown walks toward the draft. */
    function atEndAnchor(selStart, selEnd, length) {
        return selStart === selEnd && selStart === length;
    }

    const api = {
        PROMPT_HISTORY_CAP,
        createState,
        resetBrowse,
        appendRecord,
        captureDraft,
        step,
        mergeSessionHistories,
        deriveFromUserMessages,
        atStartAnchor,
        atEndAnchor,
    };

    const ns = (root.CuttlePromptHistory = root.CuttlePromptHistory || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
