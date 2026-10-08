/* Revisioned server attention plus a durable local outbox for read/manual
 * unread writes. Snapshots recover state without witnessing a completion. */
(function (root) {
    'use strict';
    function create(host) {
        const snapshots = new Map();
        const writes = new Map();
        function get(sid) {
            const key = String(sid);
            const snap = snapshots.get(key) || null;
            const pending = (host.prefs.get(key) || {}).attentionWrite;
            if (!snap || !pending) return snap;
            return {...snap, hasUnread: pending.unread || snap.reply_id > Math.max(snap.read_id, pending.through_id || 0)};
        }
        function accept(sid, snapshot) {
            if (!snapshot) return;
            const key = String(sid), prev = snapshots.get(key);
            if (!prev || snapshot.revision >= prev.revision) snapshots.set(key, snapshot);
            const prefs = host.prefs.get(key) || {};
            // Clean snapshots need no per-chat migration write. Avoid rebuilding
            // the prefs index for every row of a large initial session list.
            if (!prefs.attentionMigrated && prefs.hasUnread) {
                host.prefs.update(key, {attentionMigrated: true});
                mark(key, {unread: true, legacy: true});
            }
            if (prefs.attentionWrite) flush(key);
        }
        function mark(sid, data) {
            const key = String(sid), previous = (host.prefs.get(key) || {}).attentionWrite;
            const snapshot = snapshots.get(key);
            if (!data.unread && !previous && snapshot && !snapshot.hasUnread && snapshot.read_id >= data.through_id) return Promise.resolve(true);
            if (!data.unread && previous && !previous.unread) data = {...data, through_id: Math.max(data.through_id || 0, previous.through_id || 0)};
            host.prefs.update(key, {attentionWrite: {...data, id: host.id()}});
            return flush(key);
        }
        function flush(key) {
            if (writes.has(key)) return writes.get(key);
            const pending = (host.prefs.get(key) || {}).attentionWrite;
            if (!pending) return Promise.resolve();
            const write = host.request(key, pending).then(result => {
                const current = (host.prefs.get(key) || {}).attentionWrite;
                if (current && current.id === pending.id) host.prefs.update(key, {attentionWrite: null});
                if (result && result.attention) {
                    const previous = snapshots.get(key);
                    if (!previous || result.attention.revision >= previous.revision) snapshots.set(key, result.attention);
                }
                host.changed(key);
                return true;
            }).catch(() => false).then(ok => {
                writes.delete(key);
                const next = (host.prefs.get(key) || {}).attentionWrite;
                // A new write superseded this request; send it even if this one failed.
                if (next && next.id !== pending.id) return flush(key);
                return ok;
            });
            writes.set(key, write);
            return write;
        }
        return {get, accept, mark, flush};
    }
    const api = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatAttention = api;
})(globalThis);
