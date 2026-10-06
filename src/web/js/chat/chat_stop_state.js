/* ================================================================
   Cuttle Chat — stop/cancel lifecycle state (chat_stop_state.js).
   Owner: the three stop-related flags as ONE explicit state object
   plus their transition table and the send-abort classification.
   Stopped (user Stop, terminal for the turn), stream-detached
   (navigation / shell pause / server-sync finish — orphaned waiters
   must exit without painting), remote-waiting suppression (after
   Stop, no "waiting for reply" indicator until the next send).
   Pure: no document, no window, no fetch, no timers, no transports.
   The page holds the single state instance, performs all DOM reads/
   writes, transport teardown (AbortController/EventSource) and the
   backend cancel call; turn-guard tokens stay owned by
   chat_turn_guard.js and follow-up queue state stays in the page
   (9C). Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    function createStopState() {
        return {
            userStopped: false,
            abortSuppressed: false,
            waitingSuppressed: false,
        };
    }

    /**
     * Explicit user Stop: terminal for the turn. All three latches set;
     * idempotent (repeated Stop re-asserts, clears nothing).
     */
    function requestStop(state) {
        state.userStopped = true;
        state.abortSuppressed = true;
        state.waitingSuppressed = true;
    }

    /**
     * Non-control send starts a turn: the stop latches clear, but an
     * orphaned-stream suppression is left alone (only generation begin
     * or explicit-stop recovery clears it).
     */
    function beginSend(state) {
        state.userStopped = false;
        state.waitingSuppressed = false;
    }

    /**
     * Stream detached without cancelling the server run (navigation,
     * shell pause, server-sync finish): orphaned waiters must exit
     * instead of painting into the next chat.
     */
    function markStreamDetached(state) {
        state.abortSuppressed = true;
    }

    /**
     * Generation (re)starts locally: orphaned-stream suppression lifts
     * (also used for explicit-stop recovery in the send catch).
     */
    function clearAbortSuppression(state) {
        state.abortSuppressed = false;
    }

    /** New chat / session switch: remote-waiting suppression lifts. */
    function clearWaitingSuppression(state) {
        state.waitingSuppressed = false;
    }

    /**
     * Classify a send-flow AbortError against the stop state.
     * @returns {'detached'|'stopped'|'dropped'|'other'} detached keeps
     *   suppression (navigation abort); stopped clears it (explicit
     *   Stop must not poison later recovery); dropped/other leave it.
     */
    function classifySendAbort(state, errorName) {
        const isAbort = errorName === 'AbortError';
        if (state.abortSuppressed && isAbort) return 'detached';
        if (state.userStopped) return 'stopped';
        if (isAbort) return 'dropped';
        return 'other';
    }

    const api = {
        createStopState,
        requestStop,
        beginSend,
        markStreamDetached,
        clearAbortSuppression,
        clearWaitingSuppression,
        classifySendAbort,
    };

    const ns = (root.CuttleStopState = root.CuttleStopState || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
