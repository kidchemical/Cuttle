/* ================================================================
   Cuttle Spaces — activity aggregation (spaces_activity.js)
   Owner: Spaces subsystem. Pure aggregation: which dot a space tab shows.
   Priority frozen from app_shell.js: running > error > unread > queued >
   paused > none. Session-id variant handling + follow-up classification
   included. Transport (server poll) and DOM patching stay in the shell;
   the per-session snapshot map lives here as explicitly owned module
   state (resettable for tests).
   ================================================================ */
(function (root) {
    'use strict';

    const RANK = { running: 5, error: 4, unread: 3, queued: 2, paused: 1 };
    const LABEL = {
        running: 'Active',
        error: 'Unread error',
        unread: 'Unread',
        queued: 'Queued prompt',
        paused: 'Paused queued prompt',
    };

    /** sessionId variants -> { activity, running, at } */
    const _bySession = new Map();

    function resetActivity() {
        _bySession.clear();
    }

    function sidVariants(sid) {
        const raw = String(sid == null ? '' : sid).trim();
        if (!raw) return [];
        const out = [raw];
        const bare = raw.startsWith('db_session_') ? raw.slice('db_session_'.length) : raw;
        if (bare && bare !== raw) out.push(bare);
        if (bare && 'db_session_' + bare !== raw) out.push('db_session_' + bare);
        return out;
    }

    function noteSessionActivity(sid, activity, running) {
        const clean = activity === 'error' || activity === 'unread'
            || activity === 'queued' || activity === 'paused' ? activity : '';
        const entry = { activity: clean, running: !!running, at: Date.now() };
        sidVariants(sid).forEach((key) => { _bySession.set(key, entry); });
    }

    function lookupSessionActivity(sid) {
        const keys = sidVariants(sid);
        for (let i = 0; i < keys.length; i++) {
            const hit = _bySession.get(keys[i]);
            if (hit) return hit;
        }
        return null;
    }

    function clearSessionActivity(sid) {
        sidVariants(sid).forEach((key) => { _bySession.delete(key); });
    }

    /** Classify a follow-up queue into a dot kind ('' when empty). */
    function followupKind(items) {
        let active = false;
        let paused = false;
        (Array.isArray(items) ? items : []).forEach((x) => {
            if (!x) return;
            if (x.paused) paused = true;
            else active = true;
        });
        if (active) return 'queued';
        if (paused) return 'paused';
        return '';
    }

    /**
     * Pure core of the per-space decision.
     * @param chatIds: session ids belonging to the space
     * @param getEntry: (sid) -> { activity, running } | null
     * @param isActive: whether this is the visible space
     * @param visibleSet: Set of currently visible chat ids (active space)
     * Unread/error on a chat you are looking at is already seen → skipped.
     */
    function selectSpaceActivity(chatIds, getEntry, isActive, visibleSet) {
        const ids = Array.isArray(chatIds) ? chatIds : [];
        if (!ids.length) return '';
        const visible = visibleSet instanceof Set ? visibleSet : new Set();
        // Bare forms of the visible ids (a visible 'db_session_7' also covers
        // plain '7'). Built from the visible set, not from each candidate.
        const visibleBare = new Set([...visible].map((s) => {
            const v = sidVariants(s);
            return v.length > 1 ? v[1] : s;
        }));
        let best = '';
        let bestRank = 0;
        ids.forEach((sid) => {
            const hit = getEntry(sid);
            if (!hit) return;
            if (hit.running) {
                if (RANK.running > bestRank) {
                    best = 'running';
                    bestRank = RANK.running;
                }
                return;
            }
            const kind = hit.activity || '';
            if (!kind) return;
            if (isActive && (kind === 'unread' || kind === 'error')) {
                const variants = sidVariants(sid);
                if (variants.some((v) => visible.has(v) || visibleBare.has(v))) return;
            }
            const rank = RANK[kind] || 0;
            if (rank > bestRank) {
                best = kind;
                bestRank = rank;
            }
        });
        return best;
    }

    const api = {
        RANK,
        LABEL,
        resetActivity,
        sidVariants,
        noteSessionActivity,
        lookupSessionActivity,
        clearSessionActivity,
        followupKind,
        selectSpaceActivity,
    };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
