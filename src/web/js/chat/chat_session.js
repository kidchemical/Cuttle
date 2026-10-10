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

    const api = { create: create, createLifecycle: createLifecycle };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatSession = api;
})(globalThis);
