/* Device-local session preferences. Each field has its own atomic storage key:
 * unrelated frames never replace a shared map. Legacy maps are read-only
 * fallback; tombstones prevent removed values from coming back after migration.
 * Storage is injected; no DOM or hidden page state. */
(function (root) {
    'use strict';
    const LEGACY = 'cuttleChatSessionPrefs';
    const PREFIX = 'cuttle.sessionPrefs.v2.';
    function isKey(key) { return key === LEGACY || String(key || '').startsWith(PREFIX); }
    function create(storage) {
        const memory = new Map(), failed = new Set();
        let cached = null;
        function read(key) {
            if (failed.has(key)) return memory.get(key) || null;
            try { return storage.getItem(key); } catch (_) { return memory.get(key) || null; }
        }
        function write(key, value) {
            cached = null;
            const raw = JSON.stringify(value);
            memory.set(key, raw);
            try { storage.setItem(key, raw); failed.delete(key); }
            catch (_) { failed.add(key); }
        }
        function legacy() {
            try { const p = JSON.parse(read(LEGACY) || '{}'); return p && typeof p === 'object' && !Array.isArray(p) ? p : {}; }
            catch (_) { return {}; }
        }
        function key(sid, field) { return PREFIX + encodeURIComponent(String(sid)).replace(/\./g, '%2E') + '.' + encodeURIComponent(field).replace(/\./g, '%2E'); }
        function keys() {
            const found = new Set(memory.keys());
            try { for (let i = 0; i < storage.length; i++) found.add(storage.key(i)); } catch (_) {}
            return [...found].filter(k => k && k.startsWith(PREFIX));
        }
        function get(sid) {
            if (sid == null || sid === '') return null;
            return map()[String(sid)] || null;
        }
        function update(sid, patch) {
            if (sid == null || sid === '') return;
            Object.entries(patch || {}).forEach(([field,value]) => {
                if (field !== '__deleted' && value !== undefined) write(key(sid, field), value);
            });
        }
        function remove(sid) {
            const row = get(sid) || {};
            Object.keys(row).forEach(field => write(key(sid, field), null));
            write(key(sid, '__deleted'), true);
        }
        function map() {
            if (cached) return cached;
            const out = legacy();
            const all = keys();
            all.filter(k => k.endsWith('.__deleted')).forEach(k => {
                if (read(k) === 'true') out[decodeURIComponent(k.slice(PREFIX.length).split('.')[0])] = {};
            });
            all.forEach(k => {
                const [id, field] = k.slice(PREFIX.length).split('.').map(decodeURIComponent);
                if (!field || field === '__deleted') return;
                try { (out[id] ||= {})[field] = JSON.parse(read(k)); } catch (_) {}
            });
            Object.keys(out).forEach(id => {
                if (!Object.values(out[id]).some(value => value != null)) delete out[id];
            });
            cached = out;
            return out;
        }
        return {get, update, remove, map, invalidate: () => { cached = null; }};
    }
    const api = {create, isKey, PREFIX, LEGACY};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleSessionPrefs = api;
})(globalThis);
