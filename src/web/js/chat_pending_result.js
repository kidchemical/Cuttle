/* ================================================================
   Cuttle Chat — pending-result / replay / reconcile lifecycle
   (chat_pending_result.js).
   Owner: the post-send result lifecycle as explicit decisions plus
   injected-effect async orchestration —
     * SSE event classification (status/session/busy/query/response)
     * pending-result waiter loop (collectPendingResult)
     * history recovery matching (recoverChatResult[WithRetries])
     * parked-reply consume ack
     * message-sync per-message exactly-once classification
     * stale-generating heal decision
     * send-failure recovery sequence (stream-detach / transport-failed /
       control-lane) — closes the carried 9B send-catch integration gap
       at the decision level
     * post-restart server recovery poll
   Pure where possible (classify, decide, and find helpers take data plus
   return decisions; no document, no window, no fetch, no timers). Async
   flows take ALL effects as explicit `deps`: transport fetchers, paint
   callbacks, sleep/now clock, stop/nav predicates. The page holds timers
   (messageSyncTimer, historyGeneratingPollTimer), transport URLs, DOM,
   persistence, the busy lock (begin/endLocalGeneration), session-adopt
   guards, and applies every effect. Turn/stop primitives are used ONLY
   through the CuttleTurnGuard / CuttleStopState / CuttleFollowupQueue
   interfaces passed in — never reimplemented.
   Loaded after chat_turn_guard.js + chat_stop_state.js,
   before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** Long-poll ceiling for a detached turn (matches pre-extraction). */
    var DETACHED_WAIT_MS = 90 * 60 * 1000;
    var DEFAULT_POLL_MS = 4000;
    var HISTORY_PAGE_FLOOR = 20;
    var RESTART_WAIT_MS = 45000;
    var RESTART_POLL_MS = 2000;

    // ---------- SSE event classification (pure) ----------

    /**
     * Classify one parsed SSE `data:` object. Returns
     * { kind: 'ignore' } | { kind:'status', message } |
     * { kind:'session', sessionId } |
     * { kind:'busy', result } (shaped "still working" turn result) |
     * { kind:'query', reportUrl, queryId } | { kind:'response' }.
     * The page applies paint/adopt/finalResult effects, including the
     * session-adopt nav guards.
     */
    function classifyStreamEvent(ev) {
        if (!ev || typeof ev !== 'object') return { kind: 'ignore' };
        if (ev.type === 'status' && ev.message) {
            return { kind: 'status', message: ev.message };
        }
        if (ev.type === 'session' && ev.session_id != null) {
            return { kind: 'session', sessionId: ev.session_id };
        }
        if (ev.type === 'busy') {
            return {
                kind: 'busy',
                result: {
                    success: false,
                    busy: true,
                    session_id: ev.session_id,
                    response: (
                        '⏳ Still working on your previous message in this chat. '
                        + 'Wait for it to finish, or stop it first.'
                    ),
                },
            };
        }
        if (ev.type === 'query_started') {
            return { kind: 'query', reportUrl: ev.report_url, queryId: ev.query_id || '' };
        }
        if (ev.type === 'response') return { kind: 'response' };
        return { kind: 'ignore' };
    }

    // ---------- Pending-result waiter ----------

    /**
     * Wait for the parked reply for `sessionId` (pre-extraction
     * collectPendingResult). deps:
     *  isStaleNav() — turnNavGen stale; isStopped() — userStopped ||
     *    abortSuppressed; hasReplyForTurn() — sync already painted this
     *    turn's assistant; fetchPending(sid) — pending-result payload
     *    (throws on transport blip); fetchLive(sid) — live-status or null;
     *    liveLooksActive(live); recoverFromHistory(sid); onLiveStatus(live);
     *    onReportUrl(url); consume(sid) — ack parked copy;
     *    netDebug(name, info); sleep(ms); now().
     * Returns the shaped result or null (stale / stopped / already
     * painted / deadline without a live run).
     */
    async function waitForPendingResult(sessionId, opts, deps) {
        opts = opts || {};
        if (sessionId == null) return null;
        const deadline = deps.now() + (opts.waitMs || 0);
        const pollMs = opts.pollMs != null ? opts.pollMs : DEFAULT_POLL_MS;
        let polls = 0;
        for (;;) {
            if (deps.isStaleNav()) return null;
            if (deps.isStopped()) return null;
            // Sync may have already painted the assistant for THIS turn.
            if (deps.hasReplyForTurn()) {
                try {
                    deps.netDebug && deps.netDebug(
                        'pending-skip-ui-has-reply', 'session=' + sessionId
                    );
                } catch (_) {}
                return null;
            }
            let data = null;
            try {
                data = await deps.fetchPending(sessionId);
            } catch (_) {
                // Timed out / network blip — keep waiting while allowed.
                if (deps.isStaleNav()) return null;
                if (deps.isStopped()) return null;
                if (deps.now() >= deadline) return null;
                await deps.sleep(pollMs);
                continue;
            }
            if (deps.isStaleNav()) return null;
            if (deps.isStopped()) return null;
            if (!data || !data.success) {
                // Auth blip / transient — don't abandon a live agent run.
                const live = await deps.fetchLive(sessionId);
                if (deps.now() < deadline) {
                    if (live && live.status) deps.onLiveStatus(live);
                    await deps.sleep(pollMs);
                    continue;
                }
                return null;
            }
            if (data.pending && data.result) {
                const r = data.result;
                // Peek-only poll — consume after we know the client has it.
                deps.consume(sessionId);
                return {
                    success: !!r.success,
                    response: r.response || '',
                    session_id: r.session_id,
                    type: r.type,
                    query_id: r.query_id,
                    report_url: r.report_url,
                };
            }
            const live = await deps.fetchLive(sessionId);
            if (deps.isStaleNav()) return null;
            if (deps.isStopped()) return null;
            if (live && live.status) deps.onLiveStatus(live);
            if (live && live.report_url) deps.onReportUrl(live.report_url);
            const stillGoing = !!(data.generating || deps.liveLooksActive(live));
            // Every few idle polls, try history — pending may have been
            // cleared after an SSE write the client never read.
            polls += 1;
            if (!stillGoing || polls % 3 === 0) {
                const fromHistory = await deps.recoverFromHistory(sessionId);
                if (fromHistory && String(fromHistory.response || '').trim()) {
                    return fromHistory;
                }
            }
            if (!stillGoing || deps.now() >= deadline) {
                // Pending empty but worker finished — often the SSE loop had
                // already wiped pending after a write the client never read.
                return null;
            }
            await deps.sleep(pollMs);
        }
    }

    // ---------- History recovery (pure match + transport wrapper) ----------

    /**
     * Pure match: find the recoverable assistant row in `messages`.
     * wantNorm: normalized in-flight user text ('' when none).
     * fns: { norm(content), alreadyOnScreen(role, content),
     *   isCancelledText(content) }. Returns the message or null:
     * with in-flight text, the first non-empty assistant after the
     * matching user row; without, the trailing unseen, non-cancelled
     * assistant (never resurrects an older turn or a cancelled row).
     */
    function findRecoverableAssistant(messages, wantNorm, fns) {
        if (!Array.isArray(messages) || !messages.length) return null;
        let userIdx = -1;
        if (wantNorm) {
            for (let i = messages.length - 1; i >= 0; i--) {
                const m = messages[i];
                if (!m || m.role !== 'user') continue;
                const got = fns.norm(m.content);
                if (got === wantNorm || got.endsWith(wantNorm) || wantNorm.endsWith(got)) {
                    userIdx = i;
                    break;
                }
            }
        }
        if (userIdx >= 0) {
            for (let i = userIdx + 1; i < messages.length; i++) {
                const m = messages[i];
                if (m && m.role === 'assistant' && String(m.content || '').trim()) {
                    return m;
                }
            }
            return null;
        }
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (!m || m.role !== 'assistant' || !String(m.content || '').trim()) continue;
            if (fns.alreadyOnScreen('assistant', m.content)) return null;
            // A cancelled row is never painted, so it stays "unseen" forever
            // and would otherwise be re-recovered on later turns.
            if (fns.isCancelledText(m.content)) return null;
            return m;
        }
        return null;
    }

    function shapeRecoveredAssistant(asst, sessionId, normalizeUsage) {
        let meta = asst.metadata;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
        }
        return {
            success: true,
            response: String(asst.content || ''),
            session_id: sessionId,
            type: asst.type || 'unknown',
            query_id: asst.query_id,
            report_url: asst.report_url,
            usage: normalizeUsage((meta && meta.usage) || asst.usage) || undefined,
        };
    }

    /**
     * History recovery (pre-extraction recoverChatResultFromServer). deps:
     *  isAuthMode(), toAuthDbSessionId(), fetchMessages(authSid, limit) —
     *  { ok, data } (throws on transport blip), pageSize, inFlightText,
     *  match fns { norm, alreadyOnScreen, isCancelledText },
     *  normalizeUsage, netDebug.
     */
    async function recoverChatResult(sessionId, deps) {
        if (!deps.isAuthMode() || sessionId == null) return null;
        try {
            const authSid = deps.toAuthDbSessionId(sessionId);
            const limit = Math.max(deps.pageSize, HISTORY_PAGE_FLOOR);
            const fetched = await deps.fetchMessages(authSid, limit);
            if (!fetched || !fetched.ok) return null;
            const data = fetched.data;
            if (!data || !data.success || !Array.isArray(data.messages) || !data.messages.length) {
                return null;
            }
            const asst = findRecoverableAssistant(
                data.messages,
                deps.norm(deps.inFlightText),
                deps
            );
            if (!asst) return null;
            try {
                deps.netDebug && deps.netDebug('history-recover', 'session=' + sessionId);
            } catch (_) {}
            return shapeRecoveredAssistant(asst, sessionId, deps.normalizeUsage);
        } catch (_) {
            return null;
        }
    }

    /**
     * Extra history polls before giving up (pre-extraction
     * recoverChatResultWithRetries). deps: isStopped(), recover(sessionId),
     * isViewing(), paintSyncing(), sleep(ms).
     */
    async function recoverChatResultWithRetries(sessionId, attempts, gapMs, deps) {
        const n = Math.max(1, attempts || 1);
        const gap = gapMs != null ? gapMs : 1500;
        for (let i = 0; i < n; i++) {
            if (deps.isStopped()) return null;
            const got = await deps.recover(sessionId);
            if (got && String(got.response || '').trim()) return got;
            if (i + 1 < n) {
                if (deps.isViewing()) deps.paintSyncing();
                await deps.sleep(gap);
            }
        }
        return null;
    }

    /**
     * Ack/consume a parked reply after the browser actually received it
     * via SSE. `fire(sessionId)` is the page's fire-and-forget transport.
     */
    function consumeParkedChatResult(sessionId, fire) {
        if (sessionId == null) return;
        try {
            fire(sessionId);
        } catch (_) {}
    }

    // ---------- Sync per-message exactly-once classification (pure) ----------

    function parseControlRequestId(msg) {
        let meta = msg.metadata;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
        }
        return (meta && meta.control_request_id) || null;
    }

    /**
     * Classify one server message inside the sync reconcile loop. snap:
     *  { stopped } (userStopped — a late DB write must not paint a
     *  cancelled turn); fns: { hasDomId(id), findControlClaim(crid, role)
     *  → 'exact' | 'role' | null, isCancelledText(content),
     *  claimableUnmarked(role, content), loadingThisSession,
     *  replyShownFor(content), hasUnmarkedAssistant(),
     *  alreadyOnScreen(role, content) }.
     * Decisions: skip-invalid | update-in-place | claim-control |
     *  skip-cancelled | skip-stopped | claim-unmarked | already-shown |
     *  adopt-last | append-new | skip-dup-user | stamp-duplicate | append.
     * The page applies the existing effect for each decision (in-place
     * update, id stamping, bubble claim/adopt/append) and accumulates the
     * appendedAssistant/addedAny/appendedNewAssistant flags exactly as
     * before. claim-control carries `matched` ('exact' | 'role').
     */
    function classifyServerMessage(msg, snap, fns) {
        if (!msg || msg.id == null) return { decision: 'skip-invalid' };
        const mid = Number(msg.id);
        if (fns.hasDomId(mid)) return { decision: 'update-in-place' };
        // Exactly-once: a second history row with the same
        // control_request_id only gets its server id stamped.
        const crid = parseControlRequestId(msg);
        if (crid) {
            const matched = fns.findControlClaim(crid, msg.role);
            if (matched) return { decision: 'claim-control', matched: matched, crid: crid };
        }
        if (msg.role === 'assistant' && fns.isCancelledText(msg.content)) {
            return { decision: 'skip-cancelled' };
        }
        if (msg.role === 'assistant' && snap.stopped) {
            // Stop already dropped this turn. A late DB write must not
            // paint the reply the user cancelled.
            return { decision: 'skip-stopped' };
        }
        if (fns.claimableUnmarked(msg.role, msg.content)) {
            return { decision: 'claim-unmarked' };
        }
        // Local stream still owns this turn — don't append a second
        // assistant bubble; the finish path adopts the server result.
        if (fns.loadingThisSession && msg.role === 'assistant') {
            if (fns.replyShownFor(msg.content)) return { decision: 'already-shown' };
            // Prefer adopting the unmarked stream bubble even when the
            // server text differs slightly — exact match already failed.
            if (fns.hasUnmarkedAssistant()) return { decision: 'adopt-last' };
            // No local bubble to claim (detached wait / lost SSE) — paint
            // from history so the reply shows without refreshing.
            return { decision: 'append-new' };
        }
        // Avoid a second copy of a user prompt already on screen.
        if (msg.role === 'user' && fns.alreadyOnScreen('user', msg.content)) {
            return { decision: 'skip-dup-user' };
        }
        // Stream already painted the reply; sync must not add a second
        // bubble — stamp the unmarked bubble instead.
        if (msg.role === 'assistant' && fns.alreadyOnScreen('assistant', msg.content)) {
            return { decision: 'stamp-duplicate' };
        }
        if (msg.role === 'assistant' && fns.hasUnmarkedAssistant()) {
            return { decision: 'adopt-last' };
        }
        return { decision: 'append' };
    }

    // ---------- Stale-generating heal decision (pure) ----------

    /**
     * Decide the busy-heal action from a snapshot. snap: { stopped,
     *  suppressed, loading, hasEventSource, hasRequest, hasTrackedTurn
     *  (inFlightUserMessage), replyOnScreen, hasRemoteWait, sessionRunning,
     *  queueLength }. Returns { kind:'none' } |
     *  { kind:'finish', why:'orphan'|'zombie' } |
     *  { kind:'clear-remote', drain }.
     * The page applies the existing effects (finishLocalStreamFromServerSync
     * for both finish reasons; indicator/history/drain cleanup for
     * clear-remote).
     */
    function decideStaleHeal(snap) {
        if (snap.stopped || snap.suppressed) return { kind: 'none' };
        // Orphan isLoading with no in-flight request/SSE and no tracked user
        // turn — common after New Chat aborted a prior fetch.
        if (snap.loading && !snap.hasEventSource && !snap.hasRequest && !snap.hasTrackedTurn) {
            return { kind: 'finish', why: 'orphan' };
        }
        if (!snap.replyOnScreen) return { kind: 'none' };
        // Live EventSource still painting into an assistant bubble — don't
        // treat that as idle.
        if (snap.loading && snap.hasEventSource) return { kind: 'none' };
        if (snap.loading) {
            // Reply is on screen; the local SSE/fetch wait is a zombie.
            return { kind: 'finish', why: 'zombie' };
        }
        if (!snap.hasRemoteWait && !snap.sessionRunning) return { kind: 'none' };
        return { kind: 'clear-remote', drain: snap.queueLength > 0 };
    }

    // ---------- Send-failure recovery sequence ----------

    /**
     * First recovery attempt shared by both send-catch paths: parked
     * waiter, then history retries (another device may have consumed
     * pending, or DB commit may lag the agent finish). Returns
     * { outcome:'data', data } or { outcome:'miss' }.
     */
    async function firstRecoveryAttempt(sid, deps) {
        const recovered = await deps.wait(sid, deps.waitOpts || {});
        if (recovered) {
            try {
                deps.netDebug && deps.netDebug(
                    'pending-got', 'session=' + (recovered.session_id || '')
                );
            } catch (_) {}
            return { outcome: 'data', data: recovered };
        }
        const fromHistory = await deps.retries(sid);
        if (fromHistory) {
            try {
                deps.netDebug && deps.netDebug('pending-miss-history', 'recovered');
            } catch (_) {}
            return { outcome: 'data', data: fromHistory };
        }
        return { outcome: 'miss' };
    }

    /**
     * Detached-stream tail (SSE ended without a final event): first
     * attempt → already-painted shortcut → nav-away guard → live handoff
     * with a second waiter round. deps: wait(sid, waitOpts), retries(sid),
     *  fetchLive(sid), liveLooksActive(live), canPaint(), paintStatus(text),
     *  uiPaintedReply() → { present:true, raw } (assistant bubble after
     *  the in-flight user; raw may be '' — still present) or null (none,
     *  or caller nulls it after a chat switch), hasTyping(),
     *  isNavAway(), isStopped(), netDebug(name, info), waitOpts {pollMs}.
     * Returns { outcome:'data'|'no-reply', data }.
     */
    async function settlePendingRecovery(sid, navGen, deps) {
        void navGen;
        if (deps.canPaint()) deps.paintStatus('Working… (waiting for reply)');
        const first = await firstRecoveryAttempt(sid, deps);
        if (first.outcome === 'data') return first;
        // Never use this shortcut after a chat switch: detach clears
        // inFlightUserMessage / typing UI, and any assistant in the new
        // transcript would look "painted" → false Reply ready.
        const painted = deps.isNavAway() ? null : deps.uiPaintedReply();
        if (!deps.hasTyping() && painted && painted.present) {
            try {
                deps.netDebug && deps.netDebug('pending-miss-ui-ok', 'already painted');
            } catch (_) {}
            const raw = painted.raw || '';
            return {
                outcome: 'data',
                data: { success: true, response: raw || ' ', session_id: sid },
            };
        }
        if (deps.isNavAway()) {
            try {
                deps.netDebug && deps.netDebug('pending-miss-nav', 'detached — no false ready');
            } catch (_) {}
            return {
                outcome: 'no-reply',
                data: { success: true, response: '', no_reply: true, session_id: sid },
            };
        }
        try {
            deps.netDebug && deps.netDebug('pending-miss', 'no parked reply');
        } catch (_) {}
        // Don't invent an assistant bubble. If the worker is still going,
        // keep polling; otherwise let message-sync pick up a late DB write
        // instead of "No response received."
        const liveNow = await deps.fetchLive(sid).catch(() => null);
        if (liveNow && deps.liveLooksActive(liveNow) && !deps.isStopped()) {
            if (deps.canPaint()) {
                deps.paintStatus(liveNow.status || 'Working… (waiting for reply)');
            }
            const again = await deps.wait(sid, deps.waitOpts || {});
            if (again) return { outcome: 'data', data: again };
            const fromHistory2 = await deps.retries(sid);
            if (fromHistory2) return { outcome: 'data', data: fromHistory2 };
        }
        return {
            outcome: 'no-reply',
            data: { success: true, response: '', no_reply: true, session_id: sid },
        };
    }

    /**
     * Detached-stream path (SSE ended without a final event): settle only.
     */
    function recoverAfterStreamDetach(sid, navGen, deps) {
        return settlePendingRecovery(sid, navGen, deps);
    }

    /**
     * Transport-failure path (stream + non-stream POST both died, but the
     * agent often keeps running server-side): restart recovery first, then
     * exactly one waiter + history attempt. deps adds recoverServer(opts)
     * (post-restart poll).
     * Returns settle outcome, or { outcome:'handoff-remote' } when live
     * status shows an active run — caller clears local generating, starts
     * sync, and shows remote-waiting UI instead of faking an error.
     */
    async function recoverAfterTransportFailure(sid, navGen, error, deps) {
        if (deps.canPaint()) {
            deps.paintStatus('Connection interrupted — waiting for reply…');
        }
        // Flask restart may be in progress — recover durable status +
        // history without requiring another user message.
        try {
            await deps.recoverServer({ waitMs: RESTART_WAIT_MS, pollMs: RESTART_POLL_MS });
        } catch (_) {}
        // Transport path takes exactly one waiter + history attempt (no
        // painted-shortcut, no second waiter round): on a miss it checks
        // live status once, then hands off or rethrows.
        const first = await firstRecoveryAttempt(sid, deps);
        if (first.outcome === 'data') return first;
        const live = await deps.fetchLive(sid);
        if (live && (live.active || live.generating)) {
            // Hand off to remote waiting + poll; don't fake an error.
            return { outcome: 'handoff-remote', live: live };
        }
        return { outcome: 'throw', error: error };
    }

    /**
     * Control-lane path: never transport-retry (the server may have already
     * applied) — recover from history / live status instead. deps:
     *  retries(sid), controlRequestId. Returns { outcome:'data', data }
     *  (reconcile-only) or { outcome:'throw', error }.
     */
    async function recoverControlLaneFailure(sid, error, deps) {
        const fromHistory = await deps.retries(sid);
        if (fromHistory) {
            return {
                outcome: 'data',
                data: Object.assign({}, fromHistory, {
                    reconcile_only: true,
                    control_request_id: deps.controlRequestId,
                }),
            };
        }
        return { outcome: 'throw', error: error };
    }

    // ---------- Post-restart server recovery poll ----------

    /**
     * Post-restart recovery (pre-extraction recoverAfterFlaskRestart):
     * recover durable restart status + history without requiring another
     * message. Page holds the in-flight guard and the notified-id set.
     * deps: fetchRestartStatus() — status payload (throws on blip),
     *  fetchHealthOk(), syncMessages(), paintStatus(text), clearTyping(),
     *  toast(msg, kind), sleep(ms), now(), wasNotified(id),
     *  markNotified(id). Returns the status data or null on timeout.
     */
    async function waitForServerRecovery(opts, deps) {
        const maxWait = opts.waitMs != null ? opts.waitMs : 90000;
        const pollMs = opts.pollMs != null ? opts.pollMs : 2500;
        const deadline = deps.now() + maxWait;
        try {
            deps.paintStatus('Server restarting — reconnecting…');
            while (deps.now() < deadline) {
                let data = null;
                try {
                    data = await deps.fetchRestartStatus();
                } catch (_) {
                    await deps.sleep(pollMs);
                    continue;
                }
                const st = (data && data.status) || {};
                const state = String(st.state || '');
                const rid = st.restart_id || null;
                if (state === 'healthy' || state === 'failed' || state === 'timed_out') {
                    // Card-driven restarts report on their card; a toast here
                    // would just repeat it.
                    if (rid && !deps.wasNotified(rid) && st.chat_notify !== false) {
                        deps.markNotified(rid);
                        const msg = data.completion_message
                            || (state === 'healthy'
                                ? `Flask restart complete (${rid}).`
                                : `Flask restart ${state} (${rid}).`);
                        try {
                            deps.toast(msg, state === 'healthy' ? 'success' : 'error');
                        } catch (_) {}
                    }
                    try {
                        await deps.syncMessages();
                    } catch (_) {}
                    deps.clearTyping();
                    return data;
                }
                if (state && state !== 'none') {
                    deps.paintStatus(`Flask restart: ${state}…`);
                }
                // Also try health — daemon may have respawned without file.
                try {
                    if (await deps.fetchHealthOk()) {
                        try {
                            await deps.syncMessages();
                        } catch (_) {}
                        // Keep polling status briefly so we still surface it.
                    }
                } catch (_) {}
                await deps.sleep(pollMs);
            }
            return null;
        } finally {
            // Page clears its in-flight guard.
        }
    }

    var api = {
        DETACHED_WAIT_MS: DETACHED_WAIT_MS,
        DEFAULT_POLL_MS: DEFAULT_POLL_MS,
        classifyStreamEvent: classifyStreamEvent,
        waitForPendingResult: waitForPendingResult,
        findRecoverableAssistant: findRecoverableAssistant,
        recoverChatResult: recoverChatResult,
        recoverChatResultWithRetries: recoverChatResultWithRetries,
        consumeParkedChatResult: consumeParkedChatResult,
        classifyServerMessage: classifyServerMessage,
        decideStaleHeal: decideStaleHeal,
        recoverAfterStreamDetach: recoverAfterStreamDetach,
        recoverAfterTransportFailure: recoverAfterTransportFailure,
        recoverControlLaneFailure: recoverControlLaneFailure,
        waitForServerRecovery: waitForServerRecovery,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.CuttleChatPendingResult = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
