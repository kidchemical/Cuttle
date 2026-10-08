/* Session mutation ordering and captured lifetimes. Requests for the same
 * session/resource are serialized; unrelated sessions stay independent. */
(function (root) {
    'use strict';
    function create() {
        const chains = new Map();
        const versions = new Map();
        function capture(sessionId, resource, navigation) {
            const key = String(sessionId || '') + ':' + resource;
            const version = (versions.get(key) || 0) + 1;
            versions.set(key, version);
            return {key, sessionId: String(sessionId || ''), navigation, version};
        }
        function current(token, sessionId, navigation) {
            return token.sessionId === String(sessionId || '') && token.navigation === navigation
                && versions.get(token.key) === token.version;
        }
        function enqueue(key, work) {
            const next = (chains.get(key) || Promise.resolve()).catch(() => {}).then(work);
            chains.set(key, next);
            next.finally(() => { if (chains.get(key) === next) chains.delete(key); }).catch(() => {});
            return next;
        }
        return {capture, current, enqueue};
    }
    const api = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatMutations = api;
})(globalThis);
