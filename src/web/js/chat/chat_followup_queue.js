/* ================================================================
   Cuttle Chat — follow-up queue state (chat_followup_queue.js).
   Owner: the pending follow-up queue as ONE explicit state object
   { items, dirty, takeInFlight } plus its pure transitions and the
   take/reconcile decisions. Item shaping (normalize, partition,
   combine, fingerprint) stays owned by chat_activity.js and is used
   here ONLY through an explicit `activity` dependency — never
   reimplemented. Pure: no document, no window, no fetch, no timers.
   The page holds the single queue instance and performs all effects:
   drain-timer scheduling, persistence transport, render, edit UI
   state (editingFollowupId, passed per call), DOM, and the send flow.
   Loaded after chat_activity.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** Live queue: normalized items + persistence/take flags. */
    function createQueueState() {
        return { items: [], dirty: false, takeInFlight: false };
    }

    /**
     * Append a follow-up. clock: { now() → ms, rand() → float } so id
     * generation is deterministic under test; the page passes
     * Date.now/Math.random. Shape mirrors the pre-extraction builder.
     */
    function enqueue(state, fields, clock) {
        const now = clock.now();
        const item = {
            id: 'fq_' + Number(now).toString(36) + '_' + Number(clock.rand()).toString(36).slice(2, 7),
            content: fields.content,
            created: Number(now),
            attachments: Array.isArray(fields.attachments) ? fields.attachments : [],
            rawMessage: fields.rawMessage != null ? fields.rawMessage : fields.content,
            paused: !!fields.paused,
        };
        state.items.push(item);
        return item;
    }

    function setPaused(state, id, paused) {
        if (!id) return null;
        const item = state.items.find((x) => x.id === id);
        if (!item) return null;
        item.paused = !!paused;
        return item;
    }

    function removeItem(state, id) {
        if (!id) return false;
        const before = state.items.length;
        state.items = state.items.filter((x) => x.id !== id);
        return state.items.length !== before;
    }

    function clearAll(state) {
        state.items = [];
    }

    function markDirty(state) {
        state.dirty = true;
    }

    function markClean(state) {
        state.dirty = false;
    }

    function beginTake(state) {
        state.takeInFlight = true;
    }

    function endTake(state) {
        state.takeInFlight = false;
    }

    /**
     * Resolve the next outbound batch (pure part of drainNextFollowup).
     * serverResult: null (no session id → local path), { ok:true,
     * followups, remaining? } (server take), or { ok:false } (failed
     * take). Sets state.items to the remainder, returns the batch.
     * Mirrors the pre-extraction branches exactly, including the
     * server-remaining-as-is (unnormalized) and empty-queue-noop rules.
     */
    function resolveTake(state, editingId, serverResult, activity) {
        let batch = [];
        if (serverResult && serverResult.ok && Array.isArray(serverResult.followups)) {
            batch = serverResult.followups;
            state.items = Array.isArray(serverResult.remaining)
                ? serverResult.remaining
                : state.items.filter((x) => x.paused);
        } else if (serverResult == null || state.items.length) {
            const take = activity.partitionFollowupForDrain(state.items, editingId);
            batch = take.batch;
            state.items = take.remaining;
        }
        return batch;
    }

    /**
     * Reconcile a server-pushed list (pure part of applyServerFollowups).
     * opts: { editingId }. Never clobbers an in-flight take, unsynced
     * local edits, an active queue edit, or an identical list.
     */
    function reconcileServerList(state, list, opts, activity) {
        if (state.takeInFlight || state.dirty) return { applied: false, reason: 'busy' };
        if (opts.editingId) return { applied: false, reason: 'editing' };
        if (!Array.isArray(list)) return { applied: false, reason: 'invalid' };
        if (activity.followupQueueFingerprint(list)
                === activity.followupQueueFingerprint(state.items)) {
            return { applied: false, reason: 'same' };
        }
        state.items = list.map((item) => activity.normalizeFollowupItem(item));
        return { applied: true };
    }

    const api = {
        createQueueState,
        enqueue,
        setPaused,
        removeItem,
        clearAll,
        markDirty,
        markClean,
        beginTake,
        endTake,
        resolveTake,
        reconcileServerList,
    };

    const ns = (root.CuttleFollowupQueue = root.CuttleFollowupQueue || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
