/* Cuttle Chat — session identity owner (chat_session.js).

   Owns the current chat session id AND the navigation sequence that fences
   in-flight session loads (the page's former ``_loadSessionSeq``). It is the
   only writer of session identity: the page reads through ``current()`` and
   every change goes through ``open`` / ``adopt`` / ``create``.

   Methods:
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

   Loaded before chat_page.js. Pure state machine: no DOM, window, storage,
   or transport. */
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

    const api = { create: create };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatSession = api;
})(globalThis);
