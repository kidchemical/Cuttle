/* Cuttle Chat — session identity owner (chat_session.js).

   Owns the current chat session id AND the navigation sequence that fences
   in-flight session loads (the page's former ``_loadSessionSeq``). It is the
   only writer of session identity: the page reads through ``current()`` and
   every change goes through ``open`` / ``adopt`` / ``create``.

   ``create()`` — pure state machine (no DOM, window, storage, transport):
   - ``open(sessionId)``   navigate to a chat (history switch, deep link,
                             local-session load). Advances the navigation
                             sequence and returns it, so a stale load of the
                             chat we just left cannot paint into this one.
   - ``adopt(sessionId)``  bind a server-assigned id to the live turn without
                             navigating (does not advance the sequence).
   - ``create(sessionId)`` start a fresh session: ``null`` for the New Chat
                             splash, or a generated CH-… id on the first
                             anonymous save.
   - ``current()``         the bound session id (or null).
   - ``navSeq()``          the current navigation sequence token.

   ``createLifecycle(session, host)`` — the identity writers that need page
   capabilities. It reads and writes identity ONLY through the passed state
   machine and takes every page capability (storage, transport URLs, badges,
   migration helpers, timers) from the injected ``host``. It never reads page
   globals:
   - ``adoptChatSessionId(sessionId)``  bind a server-assigned id to the live
                                        turn (the page's former
                                        ``adoptChatSessionId``).
   - ``saveChatSession(message, role, opts)``  persist a message to the local
                                        anonymous store, minting a CH-… id on
                                        the first save (the page's former
                                        ``saveChatSession``).

   ``createLoader(session, host)`` is the session navigation entry point (the
   page's former ``loadChatSession``): it reads/writes identity only through
   the state machine and re-checks ``session.navSeq()`` after every await so a
   superseded load cannot paint into the chat we just left.

   ``createSessionBoot(session, host)`` owns the new-chat splash and the
   ``?chat=`` deep-link restore (the page's former ``createNewChat`` and
   ``bootRestoreSession``). It is the only writer of the ``chatRestoreAbandoned``
   flag, which the page reads through ``isRestoreAbandoned()``. It reads/writes
   identity only through the state machine and takes every page capability
   (storage, transport, render, timers, DOM hooks) from the injected ``host``.

   Loaded before chat_page.js. */

(function (root) {
    'use strict';

    function create() {
        let currentId = null;
        let navSeq = 0;

        function current() {
            return currentId;
        }

        function navSeqValue() {
            return navSeq;
        }

        function open(sessionId) {
            navSeq += 1;
            currentId = sessionId;
            return navSeq;
        }

        function adopt(sessionId) {
            currentId = sessionId;
            return currentId;
        }

        function createSession(sessionId) {
            currentId = sessionId;
            return currentId;
        }

        return {
            current: current,
            navSeq: navSeqValue,
            open: open,
            adopt: adopt,
            create: createSession,
        };
    }

    function createLifecycle(session, host) {
        const h = host || {};

        function adoptChatSessionId(sessionId) {
            if (sessionId == null || sessionId === '') return;
            sessionId = h.canonicalizeChatSessionId(sessionId);
            const prev = session.current();
            if (prev && !h.sessionIdsEqual(sessionId, prev)) {
                const prevBare = h.toAuthDbSessionId(prev);
                const nextBare = h.toAuthDbSessionId(sessionId);
                // Already bound to a Cuttle chat. Do not jump to a different
                // numeric id — that painted this turn under another session's
                // chrome (phone notification / CH- vs numeric remap / agent CLI
                // session_id).
                if (
                    prevBare && nextBare
                    && /^\d+$/.test(prevBare) && /^\d+$/.test(nextBare)
                    && prevBare !== nextBare
                ) {
                    return;
                }
                h.migratePromptHistorySession(prev, sessionId);
                h.migrateSessionPrefs(prev, sessionId);
                h.migrateComposerDraft(prev, sessionId);
                if (!h.isAuthMode()) h.migrateLocalChatSession(prev, sessionId);
            }
            const previousBadgeKey = String(h.canonicalizeChatSessionId(prev) || '');
            if (previousBadgeKey !== String(sessionId) && h.turnBadgeBySession.has(previousBadgeKey)) {
                h.turnBadgeBySession.set(String(sessionId), h.turnBadgeBySession.get(previousBadgeKey));
                h.turnBadgeBySession.delete(previousBadgeKey);
            }
            const wasNew = session.current() == null || !h.sessionIdsEqual(session.current(), sessionId);
            const completionNotifications = h.getCompletionNotifications();
            if (completionNotifications && h.generation.loading) {
                completionNotifications.broker().adopt(prev || h.getCompletionNotificationPendingId(), sessionId);
            }
            h.releaseManualUnreadHoldIfLeaving(sessionId);
            session.adopt(sessionId);
            h.adoptFollowupQueue(session.current(), {
                transfer: prev == null || !/^\d+$/.test(String(h.toAuthDbSessionId(prev))),
            });
            h.updateChatIdBadge(sessionId);
            h.storage.setItem('lastChatSessionId', String(sessionId));
            const auth = h.getAuth();
            if (auth && auth.setCurrentChatSession) {
                auth.setCurrentChatSession(sessionId);
            }
            h.syncChatUrl(sessionId);
            h.reportSessionToShell(sessionId);
            if (prev == null) h.clearSessionPrefs(h.newComposerPrefsId);
            // Persist project / sticky agent chosen before the server assigned an id.
            h.persistProjectForCurrentSession();
            h.persistStickySlashForCurrentSession(wasNew);
            // Persist a Muse model/effort picked while the chat had no id yet.
            // Only dirty picks persist here — persisting the in-memory default
            // would clobber the stored pin (e.g. Contributor reset to plain Spark 1.3).
            h.flushDirtyAgentSelectionsOnAdopt();
            // Owned by chat_generation.js — rebind the local lock to the newly
            // bound session while loading; idle means no lock to move.
            const rebound = h.rebindGeneration(h.generation, sessionId);
            if (rebound) {
                if (rebound.prev) h.setHistorySessionRunning(rebound.prev, false);
                h.setHistorySessionRunning(rebound.sessionId, true);
            }
            if (wasNew) {
                h.refreshChatHistoryList();
                h.scheduleHistoryTitleRefresh();
                h.startMessageSync();
            }
        }

        function saveChatSession(message, role, opts = {}) {
            // Authenticated chats are persisted server-side in /api/chat
            if (h.isAuthMode()) {
                if (session.current() != null) {
                    h.storage.setItem('lastChatSessionId', String(session.current()));
                }
                h.storage.setItem('autoResumeSession', 'true');
                if (role === 'assistant') h.scheduleHistoryTitleRefresh();
                return;
            }

            if (!session.current()) {
                // Create new session (CH-XXXXXX for anonymous local chats)
                h.clearSessionPrefs(h.newComposerPrefsId);
                session.create(h.generateLocalChatId());
                h.adoptFollowupQueue(session.current());
                h.storage.setItem('lastChatSessionId', session.current());
                h.updateChatIdBadge(session.current());
            }

            const sessions = JSON.parse(h.storage.getItem('chatSessions') || '{}');

            if (!sessions[session.current()]) {
                const currentProject = h.getCurrentProject();
                sessions[session.current()] = {
                    id: session.current(),
                    title: message.substring(0, 50) + (message.length > 50 ? '...' : ''),
                    created: Date.now(),
                    updated: Date.now(),
                    messages: [],
                    projectId: currentProject ? currentProject.id : null,
                    projectPath: currentProject ? currentProject.path : '',
                    projectName: currentProject ? currentProject.name : '',
                };
            }

            const msg = { content: message, role: role, timestamp: Date.now() };
            if (opts.query_id) msg.query_id = opts.query_id;
            if (opts.report_url) msg.report_url = opts.report_url;
            if (opts.slash_command) msg.slash_command = opts.slash_command;
            if (opts.slash_command_failed) msg.slash_command_failed = true;
            if (opts.usage) msg.usage = opts.usage;
            if (opts.routing_badge) msg.routing_badge = opts.routing_badge;
            const atts = h.normalizeAttachmentList(opts.attachments);
            if (atts.length) msg.attachments = atts;
            sessions[session.current()].messages.push(msg);

            sessions[session.current()].updated = Date.now();

            h.storage.setItem('chatSessions', JSON.stringify(sessions));

            // Re-enable auto-resume since user is actively chatting
            h.storage.setItem('autoResumeSession', 'true');

            // Reload chat history to update sidebar (keep active search/filters)
            h.reloadHistoryListKeepingSearch();
        }

        return {
            adoptChatSessionId: adoptChatSessionId,
            saveChatSession: saveChatSession,
        };
    }


    // ``createLoader(session, host)`` — the session navigation entry point.
    // It moves the live chat to ``sessionId`` (history switch, boot restore,
    // deep link, CH-… handle) and paints its transcript. The navigation
    // sequence fences in-flight loads: ``session.open`` advances it and each
    // await re-checks ``session.navSeq()`` so a superseded load cannot paint.
    // Reads and writes identity ONLY through the passed state machine; every
    // page capability (transport URLs, render, prefs, badges, timers, the
    // mutable page state it advances) comes from the injected ``host``.
    function createLoader(session, host) {
        const h = host || {};

        async function loadChatSession(sessionId, opts = {}) {
            const {
                sessionIdsEqual, saveComposerDraft, clearAllSlashChips,
                canonicalizeChatSessionId, closeChatHistoryPanel,
                detachLocalGenerationForNavigation, isAuthMode,
                releaseManualUnreadHoldIfLeaving,
                cancelDetachedSessionCompletionWatch, adoptFollowupQueue,
                setHistorySessionRunning, updateChatIdBadge,
                loadPromptHistoryForSession, syncChatUrl, refreshChatWidgets,
                restoreSessionStickySlash, reportSessionToShell,
                sessionPrefsHasUnreadFlag, resetChatAttentionState,
                sessionPrefsUnreadIsError, resetChatHistoryWindow,
                removeRemoteWaitingIndicator, stopMessageSync, toAuthDbSessionId,
                takeChatTranscriptPrefetch, fetchWithTimeout, sessionMessagesUrl,
                updateSessionUsageSummary,
                windowChatHistoryMessages, annotateMessageProjects,
                reconcileChatProject, preferredModelFromMessages,
                hydrateAgentSelectionFromSessionData, applySessionIdentity,
                rememberChatSessionTitle, updateChatSessionTitle,
                beginSuppressChatAttention, appendServerMessageToUI,
                endSuppressChatAttention, applyServerFollowups,
                acceptSessionAttention, clearChatRestoreVeil,
                reconcileCuttleButtonLocks, fetchChatLiveStatus,
                updateRemoteWaitingFromMessages, healStaleGeneratingState,
                isSessionGenerating, scheduleFollowupDrain,
                refreshTypingIndicatorHeaderBadges,
                applyGeneratingFlagsFromSessions, _restorePendingAttachments,
                restoreComposerDraft, markChatSessionRead,
                applyHistorySubagentExpandState, syncHistoryUnreadIndicators,
                syncChatAttentionIndicators, setupChatHistoryLazyLoad,
                startMessageSync, consumeCancelledAgentReply, addMessageToUI,
                normalizeUsagePayload, _mutateSessionIdSet,
                CHAT_HISTORY_PAGE_SIZE, stopState, transcriptNav, agentControls,
                formAwaitingSessionIds, slashPaletteSupplement, generation,
                storage, pinChatLayout, getAuth, getNetDebug, getFollowupQueue,
                getEditingFollowupId, setHubLiveStatusCache,
                setLastSeenServerMessageId,
                setDeferOpenChatGeneratingFromSessions,
                getLastHydratedSessionProject, setLastHydratedSessionProject,
            } = h;
            // Persist unsent text from the chat we're leaving (survives refresh).
            if (session.current() != null && !sessionIdsEqual(session.current(), sessionId)) {
                saveComposerDraft(session.current());
            } else if (session.current() == null) {
                saveComposerDraft('new');
            }
            clearAllSlashChips();
            CuttleStopState.clearWaitingSuppression(stopState);
            sessionId = canonicalizeChatSessionId(sessionId);
            // Close immediately on tap — don't wait for message fetch (felt stuck open on mobile).
            closeChatHistoryPanel();

            // Leaving a different chat's local stream — detach UI so this session
            // isn't treated as generating (false follow-up queue).
            if (
                generation.loading
                && (
                    generation.localSessionId == null
                    || !sessionIdsEqual(generation.localSessionId, sessionId)
                )
            ) {
                detachLocalGenerationForNavigation();
            }

            // assumeAuth: the boot prefetch already got a 200 from the auth-only
            // messages endpoint, so the cookie is valid even if auth.js hasn't emitted yet.
            if (isAuthMode() || opts.assumeAuth) {
                // Invalidate the per-session model cache before updating currentSessionId so
                // any render or send that follows picks up the correct model for the new session.
                if (session.current() !== sessionId) {
                    agentControls.invalidateProviderCacheForSessionSwitch();
                }
                releaseManualUnreadHoldIfLeaving(sessionId);
                const prevSessionId = session.current();
                const switchingAway = prevSessionId != null
                    && !sessionIdsEqual(prevSessionId, sessionId);
                // Opening this chat — stop any background completion poll for it.
                cancelDetachedSessionCompletionWatch(sessionId);
                const loadSeq = session.open(sessionId);
                adoptFollowupQueue(session.current());
                // Opened chat defaults to idle until the one-shot live-status (below)
                // confirms generating. Stale runningSessionIds / hub cache from a
                // prior visit used to flash the purple title + history spinner for
                // a few seconds while waiting on the shell heartbeat poll.
                // Owned by chat_generation.js — an opened chat defaults to idle
                // until the one-shot live-status confirms generating.
                const openFlags = CuttleChatGeneration.decideSessionOpen({ switchingAway });
                if (openFlags.clearHubCache) setHubLiveStatusCache(null);
                if (openFlags.deferGenerating) setDeferOpenChatGeneratingFromSessions(true);
                if (openFlags.markIdle) setHistorySessionRunning(sessionId, false);
                if (switchingAway) {
                    // Form-awaiting is DOM-derived; cleared messages must not keep
                    // the old chat's pending-form spinner on this title.
                    _mutateSessionIdSet(formAwaitingSessionIds, sessionId, false);
                }
                updateChatIdBadge(sessionId);
                if (getAuth() && getAuth().setCurrentChatSession) {
                    getAuth().setCurrentChatSession(sessionId);
                }
                loadPromptHistoryForSession(String(sessionId));
                storage.setItem('lastChatSessionId', String(sessionId));
                storage.setItem('autoResumeSession', 'true');
                syncChatUrl(sessionId);
                refreshChatWidgets();
                // Prefs-only sticky restore BEFORE shell live-status can paint the
                // working bubble (phone hub race). Transcript inference runs again
                // after /messages below.
                restoreSessionStickySlash(sessionId, []);
                reportSessionToShell(sessionId, { preferFocus: true });
                const loadT0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
                // Capture before markChatSessionRead so the title unread can survive open.
                const openNeedsAck = sessionPrefsHasUnreadFlag(sessionId);
                resetChatAttentionState({
                    needsAck: openNeedsAck,
                    isError: openNeedsAck && sessionPrefsUnreadIsError(sessionId),
                });
                // Don't block transcript load on refreshing the full session list —
                // under a busy connection pool that await made reopen feel like 10–60s.
                const sessionsRefresh = (getAuth() && typeof getAuth().loadChatSessions === 'function')
                    ? Promise.resolve(getAuth().loadChatSessions()).catch(() => {})
                    : Promise.resolve();

                document.getElementById('welcomeScreen').style.display = 'none';
                document.getElementById('chatArea').style.display = 'flex';
                pinChatLayout();

                const chatMessages = document.getElementById('chatMessages');
                chatMessages.innerHTML = '';
                setLastSeenServerMessageId(0);
                resetChatHistoryWindow();
                removeRemoteWaitingIndicator();
                stopMessageSync();
                // Drop a prior chat's hydrate so findAuthServerSession cannot briefly
                // resolve the wrong project while this session's /messages is in flight.
                const lastHydratedSessionProject = getLastHydratedSessionProject();
                if (
                    lastHydratedSessionProject
                    && !sessionIdsEqual(lastHydratedSessionProject.id, sessionId)
                ) {
                    setLastHydratedSessionProject(null);
                }

                const authSid = toAuthDbSessionId(sessionId);
                let loadedMessages = [];
                try {
                    // Boot deep-links already have this transcript in flight (started in
                    // chat_page.html) — reuse it instead of paying for a second round trip.
                    let data = opts.prefetched || null;
                    if (!data && opts.prefetchPromise) {
                        try { data = await opts.prefetchPromise; } catch (_) { data = null; }
                    }
                    if (!data) {
                        const prefetch = takeChatTranscriptPrefetch(sessionId);
                        if (prefetch) data = await prefetch;
                    }
                    if (!data) {
                        const response = await fetchWithTimeout(
                            sessionMessagesUrl(authSid, { limit: CHAT_HISTORY_PAGE_SIZE }),
                            { credentials: 'include', cache: 'no-store' },
                            12000
                        );
                        if (!response.ok) {
                            throw new Error(`HTTP ${response.status}`);
                        }
                        data = await response.json();
                    }
                    // A newer loadChatSession started while we were fetching — drop this result.
                    if (loadSeq !== session.navSeq()) return;
                    if (data.success && Array.isArray(data.messages)) {
                        if (updateSessionUsageSummary) updateSessionUsageSummary(data.usage_summary);
                        loadedMessages = windowChatHistoryMessages(data);
                        annotateMessageProjects(loadedMessages);
                        if (data.project_id != null || data.project_path || data.project_name) {
                            const projectSnapshot = {
                                id: authSid,
                                project_id: data.project_id,
                                project_name: data.project_name,
                                project_path: data.project_path,
                            };
                            if (Object.prototype.hasOwnProperty.call(data, 'checkout_uid')) {
                                projectSnapshot.checkout_uid = data.checkout_uid;
                            }
                            setLastHydratedSessionProject(projectSnapshot);
                        }
                        // Apply this session's project BEFORE the generating bubble is
                        // painted — otherwise the chip shows the previous chat's project
                        // (common on phone when switching projects mid-run).
                        reconcileChatProject();
                        // Early refreshChatWidgets() above often still had the prior
                        // chip path; re-fetch now that project matches this session.
                        refreshChatWidgets();
                        // Hydrate preferred model from transcript before painting so
                        // collapse/composer don't flash "Cursor - Auto" while the
                        // models API is still in flight.
                        const fromHistory = preferredModelFromMessages(loadedMessages);
                        if (fromHistory) {
                            slashPaletteSupplement.preferredModel = fromHistory;
                        }
                        // Same for Muse: the messages payload carries the session
                        // pins, so badges paint correctly on the first pass with
                        // no /api/muse/* round trip (and no heal flicker).
                        hydrateAgentSelectionFromSessionData(data, sessionId);
                        applySessionIdentity(data);
                        // Title from the same payload — don't wait for the session
                        // list (history panel) or the delayed title-refresh timers.
                        if (data.session_name) {
                            rememberChatSessionTitle(sessionId, data.session_name);
                            updateChatSessionTitle(sessionId);
                        }
                        beginSuppressChatAttention();
                        try {
                            // skipScroll/skipNav: bulk paint must not schedule bottom
                            // autoScroll rAFs that race a pending CH-…-N message jump.
                            loadedMessages.forEach(msg => {
                                appendServerMessageToUI(msg, {
                                    announce: false,
                                    skipScroll: true,
                                    skipNav: true,
                                });
                            });
                        } finally {
                            endSuppressChatAttention();
                        }
                        transcriptNav.updateMessageNav();
                        // Server follow-ups travel with /messages — apply on open so a
                        // stuck queue (e.g. CH-000441) drains without waiting for sync.
                        applyServerFollowups(data.followups, data.followup_revision);
                        acceptSessionAttention(sessionId, data.attention);
                        // Transcript is on screen — drop the restore veil before the
                        // live-status round trip, which nobody needs to wait behind.
                        clearChatRestoreVeil();
                        reconcileCuttleButtonLocks(loadedMessages);
                        // Sticky agent BEFORE remote-waiting paint — otherwise
                        // currentTypingSlashMeta() sees empty/other-chat chips and
                        // the working bubble reopens without the agent badge.
                        restoreSessionStickySlash(sessionId, loadedMessages);
                        const liveStatus = await fetchChatLiveStatus(sessionId);
                        if (loadSeq !== session.navSeq()) return;
                        setDeferOpenChatGeneratingFromSessions(false);
                        updateRemoteWaitingFromMessages(loadedMessages, liveStatus);
                        healStaleGeneratingState();
                        if (
                            getFollowupQueue().items.some((x) => !x.paused)
                            && !isSessionGenerating()
                            && !getEditingFollowupId()
                        ) {
                            scheduleFollowupDrain(120);
                        }
                        refreshTypingIndicatorHeaderBadges();
                    }
                } catch (e) {
                    if (loadSeq !== session.navSeq()) return;
                    setDeferOpenChatGeneratingFromSessions(false);
                    console.error('Failed to load auth session messages:', e);
                }

                clearChatRestoreVeil();
                        if (loadSeq !== session.navSeq()) return;

                try { await sessionsRefresh; } catch (_) {}
                        if (loadSeq !== session.navSeq()) return;
                // One-shot generating flags for *other* chats (history rows). The open
                // chat stays idle until fetchChatLiveStatus above confirms — do not
                // wait for HISTORY_GENERATING_POLL_MS / shell heartbeat.
                try {
                    const list = (getAuth() && getAuth().getSessions
                        && getAuth().getSessions()) || [];
                    if (list.length) applyGeneratingFlagsFromSessions(list);
                } catch (_) {}
                // Session list may have landed after the early chrome paint — refresh
                // the open-chat title now that getSessions() is populated.
                updateChatSessionTitle(sessionId);
                reconcileChatProject();

                // Idempotent re-apply (also covers the messages-load failure path
                // where the early restore above never ran). Composer chips must
                // match this session even when there is no generating bubble.
                restoreSessionStickySlash(sessionId, loadedMessages);
                _restorePendingAttachments();
                restoreComposerDraft(sessionId, { force: true });

                // Proactively fetch the per-session model preference so the composer chip
                // and typing indicator show the correct model before the palette is opened.
                agentControls.loadCursorAgentModelsForPalette(true);
                agentControls.loadMuseModelsForPalette(true);

                markChatSessionRead(sessionId);
                document.querySelectorAll('.chat-history-item').forEach(item => {
                    item.classList.toggle('active', sessionIdsEqual(item.dataset.sessionId, sessionId));
                });
                applyHistorySubagentExpandState();
                syncHistoryUnreadIndicators();
                syncChatAttentionIndicators();
                transcriptNav.settleAfterLoad();
                setupChatHistoryLazyLoad();
                // Re-apply after messages/sessions settle (server project beats stale empty local path).
                reconcileChatProject();
                startMessageSync();
                try {
                    const ms = Math.round(
                        ((typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now()) - loadT0
                    );
                    getNetDebug() && getNetDebug().event(
                        'chat-load',
                        'session=' + sessionId + ' msgs=' + loadedMessages.length + ' ' + ms + 'ms'
                    );
                } catch (_) {}
                return;
            }

            // Load messages for this session from localStorage
            const sessions = JSON.parse(storage.getItem('chatSessions') || '{}');
            const storedSession = sessions[sessionId];
        
            if (!storedSession) {
                console.error('Session not found:', sessionId);
                closeChatHistoryPanel();
                clearChatRestoreVeil();
                return;
            }
        
            releaseManualUnreadHoldIfLeaving(sessionId);
            const prevSessionId = session.current();
            const switchingAway = prevSessionId != null
                && !sessionIdsEqual(prevSessionId, sessionId);
            session.open(sessionId);
            adoptFollowupQueue(session.current());
            if (switchingAway) {
                setHubLiveStatusCache(null);
                setHistorySessionRunning(sessionId, false);
                _mutateSessionIdSet(formAwaitingSessionIds, sessionId, false);
            }
            updateChatIdBadge(sessionId);
            loadPromptHistoryForSession(sessionId);
            _restorePendingAttachments();
            restoreComposerDraft(sessionId, { force: true });
            storage.setItem('lastChatSessionId', sessionId);
            syncChatUrl(sessionId);
            reportSessionToShell(sessionId, { preferFocus: true });
            refreshChatWidgets();
            const openNeedsAck = sessionPrefsHasUnreadFlag(sessionId);
            resetChatAttentionState({
                needsAck: openNeedsAck,
                isError: openNeedsAck && sessionPrefsUnreadIsError(sessionId),
            });
        
            // Re-enable auto-resume since user explicitly loaded this session
            storage.setItem('autoResumeSession', 'true');
        
            // Show chat area
            clearChatRestoreVeil();
            document.getElementById('welcomeScreen').style.display = 'none';
            document.getElementById('chatArea').style.display = 'flex';
            pinChatLayout();
        
            // Load messages
            const chatMessages = document.getElementById('chatMessages');
            chatMessages.innerHTML = '';
        
            if (storedSession.messages && storedSession.messages.length > 0) {
                beginSuppressChatAttention();
                try {
                    storedSession.messages.forEach(msg => {
                        if (msg.role === 'assistant' && consumeCancelledAgentReply(msg.content, { announce: false })) return;
                        const reportUrl = msg.report_url || (
                            msg.query_id ? `/query_log.html?id=${msg.query_id}` : null
                        );
                        addMessageToUI(msg.content, msg.role, {
                            report_url: reportUrl,
                            query_id: msg.query_id,
                            timestamp: msg.timestamp,
                            slash_command: msg.slash_command,
                            slash_command_failed: msg.slash_command_failed,
                            attachments: msg.attachments,
                            routing_badge: msg.routing_badge || undefined,
                            usage: normalizeUsagePayload(msg.usage) || undefined,
                            skipScroll: true,
                            skipNav: true,
                        });
                    });
                } finally {
                    endSuppressChatAttention();
                }
                transcriptNav.updateMessageNav();
                reconcileCuttleButtonLocks(storedSession.messages);
            }
        
            // Update active state in sidebar
            document.querySelectorAll('.chat-history-item').forEach(item => {
                item.classList.remove('active');
                if (item.dataset.sessionId === sessionId) {
                    item.classList.add('active');
                }
            });
            applyHistorySubagentExpandState();

            // Restore the project (cwd) this conversation was using
            reconcileChatProject();
            restoreSessionStickySlash(sessionId, storedSession.messages);
            markChatSessionRead(sessionId);
            syncHistoryUnreadIndicators();
            syncChatAttentionIndicators();
        
            // Scroll to bottom (or deep-linked message) - default stays pinned to bottom
            transcriptNav.settleAfterLoad();
        }

        return { loadChatSession: loadChatSession };
    }


    // ``createSessionBoot(session, host)`` — the new-chat splash and the
    // ``?chat=`` deep-link restore (the page's former ``createNewChat`` and
    // ``bootRestoreSession``). It owns the ``chatRestoreAbandoned`` flag — set
    // when a new chat opens or a slow restore gives up — and the page reads it
    // through ``isRestoreAbandoned()``. Identity changes go through the state
    // machine; every page capability comes from the injected ``host``.
    function createSessionBoot(session, host) {
        const h = host || {};
        const CHAT_RESTORE_TIMEOUT_MS = 10000;
        let chatRestoreAbandoned = false;

        function isRestoreAbandoned() {
            return chatRestoreAbandoned;
        }

        async function createNewChat() {
            if (session.current() != null) h.saveComposerDraft(session.current());
            h.applyStarredSlashChips();
            chatRestoreAbandoned = true;
            h.clearChatRestoreVeil();
            h.storage.setItem('autoResumeSession', 'false');

            // Detach prior chat's local stream UI first — otherwise welcome stays
            // disabled (processMessage) and/or isLoading parks the first send.
            h.detachLocalGenerationForNavigation();

            const chatMessages = document.getElementById('chatMessages');
            if (chatMessages) {
                chatMessages.innerHTML = '';
            }
            // Drop prior session's lazy-history offset — otherwise new-chat numbers
            // keep starting at older_visible_count+1 from the chat we left.
            h.resetChatHistoryWindow();
            h.transcriptNav.resetNav();

            document.getElementById('welcomeScreen').style.display = 'flex';
            document.getElementById('chatArea').style.display = 'none';

            document.querySelectorAll('.chat-history-item').forEach(item => {
                item.classList.remove('active');
            });

            // Drop the previous chat id before reconcile. Otherwise the welcome
            // screen inherits that session's project, then the first send (still
            // unsaved) snaps to the Cuttle default.
            h.releaseManualUnreadHoldIfLeaving(null);
            session.create(null);
            h.adoptFollowupQueue(session.current());
            h.applyHistorySubagentExpandState();
            h.applySessionIdentity(null);
            h.setCurrentProject(null);
            h.setHubLiveStatusCache(null);
            h.reconcileChatProject();
            // Prior chat's Tasks strip must not stick on welcome / new chat.
            h.refreshChatWidgets();
            h.closeChatHistoryPanel();

            // Don't persist an empty session — wait until the user sends a message.
            h.updateChatIdBadge(null);
            h.loadPromptHistoryForSession(null);
            h.storage.removeItem('lastChatSessionId');
            const auth = h.getAuth();
            if (auth && auth.setCurrentChatSession) {
                auth.setCurrentChatSession(null);
            }
            h.stopMessageSync({ resetCursor: true });
            h.clearFollowupQueue();
            CuttleStopState.clearWaitingSuppression(h.stopState);
            h.syncChatUrl(null);
            h.reportSessionToShell(null);
            h.restoreWelcomeStickySlash();
            h.restoreComposerDraft('new', { force: true });
            h.pinChatLayout();
            h.resetChatAttentionState();
            h.focusWelcomeComposer();
            console.log('New chat ready (not saved until first message)');
        }

        async function bootRestoreSession(sessionParam) {
            // Never leave a spinner up, whatever happens below.
            setTimeout(h.clearChatRestoreVeil, CHAT_RESTORE_TIMEOUT_MS + 2000);
            const prefetch = h.takeChatTranscriptPrefetch(sessionParam);
            let prefetchData = null;
            const prefetchReady = prefetch
                ? prefetch.then((data) => {
                    if (data && data.success) {
                        prefetchData = data;
                        return 'prefetch';
                    }
                    return null;
                }).catch(() => null)
                : null;
            const authReady = h.waitForAuthReady(CHAT_RESTORE_TIMEOUT_MS)
                .then((ok) => (ok ? 'auth' : null));

            const winner = await h.firstTruthy([prefetchReady, authReady], CHAT_RESTORE_TIMEOUT_MS);
            if (chatRestoreAbandoned) return;

            if (winner) {
                try {
                    await h.loadChatSession(sessionParam, {
                        assumeAuth: winner === 'prefetch',
                        prefetched: prefetchData,
                        prefetchPromise: prefetch,
                    });
                } finally {
                    h.clearChatRestoreVeil();
                }
                return;
            }

            // Anonymous chats live in localStorage and need no auth at all.
            try {
                const local = JSON.parse(h.storage.getItem('chatSessions') || '{}');
                if (local && local[sessionParam]) {
                    await h.loadChatSession(sessionParam);
                    return;
                }
            } catch (_) {}

            chatRestoreAbandoned = true;
            h.showWelcomeSplash();
            h.showToast('Chat took too long to load — starting a new chat.', 'warning');
        }

        return {
            createNewChat: createNewChat,
            bootRestoreSession: bootRestoreSession,
            isRestoreAbandoned: isRestoreAbandoned,
        };
    }

    const api = {
        create: create,
        createLifecycle: createLifecycle,
        createLoader: createLoader,
        createSessionBoot: createSessionBoot,
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatSession = api;
})(globalThis);
