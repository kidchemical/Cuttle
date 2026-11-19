/* Chat display mode: Classic (default) or Studio.
 *
 * Effective Studio = the per-browser pref `cuttleChatDisplay === 'studio'`
 * AND the cached effective flag `cuttleChatStudioFlag === '1'` (experimental
 * flag `chat_studio_display`, written by syncFlag). A stale pref with the flag
 * off stays Classic; the pref is kept, so turning the flag back on restores it.
 *
 * ui_boot.js applies `<html class="cuttle-studio">` before first paint from the
 * same two keys. This module owns every change after that: it toggles the class,
 * tells owners through onChange, and keeps other panes in step via `storage`.
 *
 *   CuttleChatDisplay.mode()        'studio' | 'classic' (effective)
 *   CuttleChatDisplay.isStudio()    boolean
 *   CuttleChatDisplay.state()       {mode, studio, flag, pref}
 *   CuttleChatDisplay.setMode(m)    persist 'classic'|'studio', apply here
 *   CuttleChatDisplay.syncFlag(f)   fetch the flag (f defaults to window.fetch)
 *   CuttleChatDisplay.onChange(cb)  cb(state) on any change; returns unsubscribe
 *   CuttleChatDisplay.reloadOnFlip() reload this page when the effective mode flips
 *                                   after load (pages with mode-specific mounted UI)
 *
 * Browser: `window.CuttleChatDisplay` is one instance per page, created from
 * window/document/localStorage. Node/tests: `module.exports.create(deps)` with
 * fakes; no other page globals are read.
 */
(function (root) {
    'use strict';

    const MODE_KEY = 'cuttleChatDisplay';
    const FLAG_KEY = 'cuttleChatStudioFlag';
    const FLAG_ID = 'chat_studio_display';
    const CLASS = 'cuttle-studio';
    const FLAGS_URL = '/api/experimental/flags';
    const FLAGS_CHANGED = 'cuttle-experimental-flags-changed';
    /* Same set as chat_subagent_peek.js APPEARANCE: the parent re-broadcasts these
       into nested chat frames, so the frame re-applies its own look from storage. */
    const APPEARANCE = ['cuttle-theme-change', 'cuttle-video-state', 'cuttle-bg-effect-blend',
        'cuttle-ui-anim-change', 'cuttle-shell-visibility'];

    /** Stored pref → 'studio' | 'classic'. Anything unknown is Classic. Pure. */
    function normalizeMode(raw) {
        return raw === 'studio' ? 'studio' : 'classic';
    }

    /** Effective mode from the stored pref and the cached flag. Pure. */
    function effectiveMode(rawPref, rawFlag) {
        return normalizeMode(rawPref) === 'studio' && rawFlag === '1' ? 'studio' : 'classic';
    }

    /**
     * Flag state from a `/api/experimental/flags` payload. Pure.
     * Returns true/false, or null when the payload is unusable (keep the cache).
     * The kill switch forces false.
     */
    function flagFromPayload(data) {
        if (!data || typeof data !== 'object' || !data.success) return null;
        if (data.kill_switch) return false;
        const list = Array.isArray(data.flags) ? data.flags : [];
        const spec = list.find((f) => f && f.id === FLAG_ID);
        return !!(spec && spec.enabled);
    }

    function create(deps) {
        const win = deps.window;
        const doc = deps.document;
        const store = deps.storage;
        const defaultFetch = deps.fetch;
        const listeners = new Set();
        let lastKey = null;

        function readStore(key) {
            try { return store.getItem(key); } catch (_) { return null; }
        }

        function writeStore(key, value) {
            try { store.setItem(key, value); } catch (_) { /* private mode: keep the in-memory state */ }
        }

        function pref() { return normalizeMode(readStore(MODE_KEY)); }
        function flagOn() { return readStore(FLAG_KEY) === '1'; }
        function mode() { return effectiveMode(readStore(MODE_KEY), readStore(FLAG_KEY)); }
        function isStudio() { return mode() === 'studio'; }

        function state() {
            return { mode: mode(), studio: isStudio(), flag: flagOn(), pref: pref() };
        }

        function keyOf(s) {
            return s.mode + '|' + (s.flag ? '1' : '0') + '|' + s.pref;
        }

        function notifyIfChanged() {
            const s = state();
            const key = keyOf(s);
            if (key === lastKey) return;
            lastKey = key;
            if (doc && doc.documentElement) doc.documentElement.classList.toggle(CLASS, s.studio);
            listeners.forEach((cb) => { try { cb(s); } catch (_) { /* one bad owner never blocks the rest */ } });
        }

        function setMode(next) {
            if (next !== 'classic' && next !== 'studio') return mode();
            writeStore(MODE_KEY, next);
            notifyIfChanged();
            return mode();
        }

        function syncFlag(fetchImpl) {
            const fetchFn = typeof fetchImpl === 'function' ? fetchImpl : defaultFetch;
            if (typeof fetchFn !== 'function') return Promise.resolve(flagOn());
            return Promise.resolve()
                .then(() => fetchFn(FLAGS_URL, { credentials: 'include', cache: 'no-store' }))
                .then((r) => r.json())
                .then((data) => {
                    const on = flagFromPayload(data);
                    if (on === null) return flagOn();
                    writeStore(FLAG_KEY, on ? '1' : '0');
                    notifyIfChanged();
                    return on;
                })
                .catch(() => flagOn());
        }

        function onChange(cb) {
            if (typeof cb !== 'function') return () => {};
            listeners.add(cb);
            return () => { listeners.delete(cb); };
        }

        /**
         * The chat page mounts mode-specific UI (work panels, the push tray, the
         * supervision timeline, bubble headers) and ui_boot.js settles the
         * flat-messages class at load. A flip after load reloads the page, so no
         * piece of the other mode is left on screen. Returns unsubscribe.
         */
        function reloadOnFlip() {
            const was = isStudio();
            return onChange((s) => {
                if (s.studio === was) return;
                if (win.location && typeof win.location.reload === 'function') win.location.reload();
            });
        }

        function onStorage(e) {
            if (!e || e.key === null || e.key === MODE_KEY || e.key === FLAG_KEY) notifyIfChanged();
        }

        function onMessage(e) {
            if (!e || !e.data || typeof e.data !== 'object') return;
            if (win.location && e.origin !== win.location.origin) return;
            const type = e.data.type;
            if (type === FLAGS_CHANGED) {
                if (typeof defaultFetch === 'function') syncFlag();
                return;
            }
            if (APPEARANCE.indexOf(type) !== -1) notifyIfChanged();
        }

        win.addEventListener('storage', onStorage);
        win.addEventListener('message', onMessage);
        notifyIfChanged();

        return { mode, isStudio, state, setMode, syncFlag, onChange, reloadOnFlip };
    }

    const api = { MODE_KEY, FLAG_KEY, FLAG_ID, CLASS, normalizeMode, effectiveMode, flagFromPayload, create };

    if (typeof window !== 'undefined' && window.document && window.localStorage) {
        try {
            root.CuttleChatDisplay = Object.assign(create({
                window: window,
                document: window.document,
                storage: window.localStorage,
                fetch: window.fetch ? window.fetch.bind(window) : null,
            }), api);
        } catch (_) { /* storage blocked: Classic */ }
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
