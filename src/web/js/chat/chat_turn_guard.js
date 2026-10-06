/* ================================================================
   Cuttle Chat — turn/staleness guard (chat_turn_guard.js).
   Owner: generation-scoped staleness tokens for the streaming
   lifecycle. One generation object per page; New Chat / history
   switch bumps it so every prior turn goes stale at once. Turns
   capture the token at send time (also ferried as opts.navGen into
   collectPendingResult); paint/adopt/wait decisions compare against
   the live generation. Pure: no document, no window, no fetch, no
   timers. The page holds the single generation instance and binds
   live session state (currentSessionId, isViewingSession) per call;
   bodies (SSE loop, pending waiter, send flow) keep their own
   control flow and call these predicates.
   ================================================================ */
(function (root) {
    'use strict';

    /** Token source. Exactly one instance lives in the page. */
    function createGeneration() {
        return { gen: 0 };
    }

    /** Navigation boundary: invalidate every captured turn token. */
    function bump(generation) {
        generation.gen += 1;
        return generation.gen;
    }

    /** Snapshot the current generation for a new turn. */
    function capture(generation) {
        return generation.gen;
    }

    /**
     * True when a captured token is stale. A null/undefined token
     * (legacy caller that passed no navGen) is never stale — this
     * preserves the pre-extraction `turnNavGen != null &&` guard shape
     * at the pending-waiter check sites.
     */
    function isStale(capturedNavGen, generation) {
        return capturedNavGen != null && capturedNavGen !== generation.gen;
    }

    /**
     * Full paint gate for a turn: generation fresh, then session
     * binding. A null bound session paints only on the welcome splash
     * (no open session); otherwise the bound session must be the
     * viewed one, so a background turn never paints into another chat.
     * snapshot: { gen, currentSessionId, isViewing } — isViewing is a
     * callback so session lookup stays lazy behind the gen check.
     */
    function canPaintHere(turnNavGen, boundSessionId, snapshot) {
        if (turnNavGen !== snapshot.gen) return false;
        if (boundSessionId == null) return snapshot.currentSessionId == null;
        return !!snapshot.isViewing(boundSessionId);
    }

    const api = {
        createGeneration,
        bump,
        capture,
        isStale,
        canPaintHere,
    };

    const ns = (root.CuttleTurnGuard = root.CuttleTurnGuard || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
