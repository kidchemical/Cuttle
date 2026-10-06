/* ================================================================
   Cuttle Chat — busy-generation lifecycle (chat_generation.js).
   Owner: the local generation busy-lock as ONE explicit state object
   { loading, localSessionId, seq } plus its transitions, the message-sync
   poll cadence decision, the detached-completion poll classification, the
   session-open generation-flag decision, and the message-sync claim
   lifetime as one page-held state { inFlight, startedAt, seq } with a
   token claim/finish interface (D2b: preserves the page's claim
   contract — same 20 s replacement and fresh-coalescing boundary).
   - Token-scoped release: every begin bumps the token (re-begin while
     loading reuses it — begin is idempotent, as the composer +
     processMessage double-begin requires); end releases ONLY on a token
     match, so a stale turn callback (Stop → re-send → old finally) can
     never release a newer turn's busy state. Force (no token) is reserved
     for Stop / chat-delete, which own the turn outright. Detach always
     wins and invalidates pre-detach tokens.
   - Pure where possible (decide/classify/isLoadingForSession take
     snapshots; no document, no window, no fetch, no timers). The page
     holds the single state instance and performs ALL effects: timer
     handles/scheduling, transport, DOM, persistence, voice phase,
     stop-state transitions, history running-flag paint, and the
     session-adopt guards. Turn/stop/queue/pending-result owners are used
     ONLY through passed-in interfaces — never reimplemented.
   Loaded after chat_turn_guard.js + chat_stop_state.js +
   chat_pending_result.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** Message-sync poll cadence (moved verbatim from the page). */
    var SYNC_MS = {
        idle: 12000,
        active: 5000,
        hidden: 60000,
        unfocused: 16000,
        unfocusedIdle: 45000,
        recover: 4000,
        inflightMax: 20000,
    };

    /** Busy-lock: is this tab generating + for which session + begin token. */
    function createGenerationState() {
        return { loading: false, localSessionId: null, seq: 0 };
    }

    function currentToken(state) {
        return state.seq;
    }

    /**
     * Claim generation for a send. Idempotent while loading (the composer
     * and processMessage both begin the same turn — the second begin must
     * NOT invalidate the first token). Returns { token, sessionId,
     * markRunning } — the page clears abort suppression, marks the history
     * running flag, and (re)arms message sync.
     */
    function beginGeneration(state, ids) {
        if (!state.loading) state.seq += 1;
        state.loading = true;
        state.localSessionId = (ids && (ids.currentSessionId || ids.requestSessionId)) || null;
        return { token: state.seq, sessionId: state.localSessionId, markRunning: state.localSessionId };
    }

    /**
     * Release generation. Scoped: a token mismatch means a newer turn (or
     * a detach) has since claimed the lock — do NOT release it.
     * token == null forces (Stop / chat-delete own the turn outright).
     * Returns { released, sessionId } — the page clears the stream poll,
     * unmarks the history running flag, and reschedules sync on release.
     */
    function endGeneration(state, token) {
        if (token != null && token !== state.seq) {
            return { released: false, sessionId: null };
        }
        const id = state.localSessionId;
        state.loading = false;
        state.localSessionId = null;
        return { released: true, sessionId: id };
    }

    /**
     * Navigation detach: the server run continues but the local lock is
     * dropped and pre-detach tokens are invalidated. Returns
     * { keepRunningId } — the page keeps that chat's history spinner and
     * starts the detached completion watch.
     */
    function detachGeneration(state) {
        const keep = state.localSessionId;
        state.loading = false;
        state.localSessionId = null;
        state.seq += 1;
        return { keepRunningId: keep };
    }

    /**
     * Session switch rebind while loading (adoptChatSessionId): move the
     * local lock to the newly bound session. Returns { prev, sessionId }
     * for flag updates, or null when idle (no lock to move).
     */
    function rebindGeneration(state, sessionId) {
        if (!state.loading) return null;
        const prev = state.localSessionId;
        state.localSessionId = sessionId;
        return { prev: prev, sessionId: sessionId };
    }

    /**
     * This-transcript generating predicate (pre-extraction
     * isLoadingThisSession). isViewing(sessionId) is the page's
     * session-visibility predicate (injected for testability).
     */
    function isLoadingForSession(state, isViewing) {
        if (!state.loading) return false;
        if (state.localSessionId == null) return true;
        return !!isViewing(state.localSessionId);
    }

    /**
     * Message-sync claim lifetime (D2b: preserves the page's claim
     * contract for the inFlight/startedAt/seq fields). One page-held
     * state { inFlight, startedAt, seq }; the single monotonic seq is
     * both the token and the claim owner (no parallel owner field).
     * Pure: now is passed explicitly, no Date, no timers, no DOM/fetch.
     */
    function createSyncState() {
        return { inFlight: false, startedAt: 0, seq: 0 };
    }

    /**
     * Claim a sync at explicit now. A fresh outstanding claim coalesces
     * (null); a claim older than SYNC_MS.inflightMax is replaced (the
     * stale holder's token stops matching). Boundary preserved exactly:
     * only strictly-greater-than replaces; a zero startedAt coalesces.
     * Returns { token } — the page captures nav/session guards with it.
     */
    function claimSync(state, now) {
        if (state.inFlight) {
            if (state.startedAt && (now - state.startedAt) > SYNC_MS.inflightMax) {
                // Timeout replacement: fall through and take ownership.
            } else {
                return null;
            }
        }
        state.inFlight = true;
        state.startedAt = now;
        state.seq += 1;
        return { token: state.seq };
    }

    /**
     * True when token matches the latest claim, including after finish.
     * A newer claim invalidates older tokens; finish does not bump seq,
     * so repeated finish of the same token stays safe.
     */
    function isSyncCurrent(state, token) {
        return token === state.seq;
    }

    /**
     * Finish only the owning token. A stale token (replaced claim) must
     * not clear the newer in-flight state. Returns { released } — the
     * page performs no further effect on release.
     */
    function finishSync(state, token) {
        if (token !== state.seq) {
            return { released: false };
        }
        state.inFlight = false;
        state.startedAt = 0;
        return { released: true };
    }

    /**
     * Message-sync poll cadence. snap: { backgrounded, shellUnfocused,
     * loading, generating }. Branch order preserved exactly.
     */
    function decideSyncDelayMs(snap) {
        if (snap.backgrounded) return SYNC_MS.hidden;
        if (snap.shellUnfocused) {
            if (snap.loading || snap.generating) return SYNC_MS.unfocused;
            return SYNC_MS.unfocusedIdle;
        }
        // Local stream/detached wait: keep a light recovery poll so a
        // finished server reply can paint without requiring a refresh.
        if (snap.loading) return SYNC_MS.recover;
        if (snap.generating) return SYNC_MS.active;
        return SYNC_MS.idle;
    }

    /**
     * One detached-completion poll step (post-navigation quiet poll).
     * step: { cancelled, returnedHome, body (parked reply text or ''),
     * generating (pending flag), liveActive }. Returns 'stop' |
     * 'notify-done' | 'keep-waiting' | 'spin-down'. The page owns the
     * cancel registry, transport, sleeps, chirp, and running-flag paint.
     */
    function classifyDetachedPoll(step) {
        if (step.cancelled || step.returnedHome) return 'stop';
        if (step.body) return 'notify-done';
        if (step.generating || step.liveActive) return 'keep-waiting';
        // Idle, no parked body — spin down; opening the chat loads history.
        return 'spin-down';
    }

    /**
     * Session-open generation flags. An opened chat defaults to idle until
     * the one-shot live-status confirms generating (stale flags used to
     * flash the title/history spinner). Returns { clearHubCache,
     * deferGenerating, markIdle } — the page applies hub-cache reset,
     * the defer latch, and the running-flag clear.
     */
    function decideSessionOpen(opts) {
        if (!opts || !opts.switchingAway) {
            return { clearHubCache: false, deferGenerating: false, markIdle: false };
        }
        return { clearHubCache: true, deferGenerating: true, markIdle: true };
    }

    var api = {
        SYNC_MS: SYNC_MS,
        createGenerationState: createGenerationState,
        createSyncState: createSyncState,
        claimSync: claimSync,
        isSyncCurrent: isSyncCurrent,
        finishSync: finishSync,
        currentToken: currentToken,
        beginGeneration: beginGeneration,
        endGeneration: endGeneration,
        detachGeneration: detachGeneration,
        rebindGeneration: rebindGeneration,
        isLoadingForSession: isLoadingForSession,
        decideSyncDelayMs: decideSyncDelayMs,
        classifyDetachedPoll: classifyDetachedPoll,
        decideSessionOpen: decideSessionOpen,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.CuttleChatGeneration = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
