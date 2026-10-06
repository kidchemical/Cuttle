/* ================================================================
   Cuttle Achievements — polling + celebration dispatch (achievements.js)
   Owner: Achievements subsystem (experimental, flag `achievements`).
   Pure logic lives in this file (grouping, ordering, filtering, ack
   bookkeeping) so it is unit-testable in node; the DOM work is delegated
   to `CuttleCelebrate` and to showToast. Loaded before app_shell.js.

   Self-contained on purpose: it polls GET /api/achievements/pending on its
   own timer, so nothing in app_shell.js has to know this feature exists.
   Removing achievements = delete this file + celebrate.js + their two
   <script> lines in app_shell.html.
   ================================================================ */
(function (root) {
    'use strict';

    const POLL_MS_ACTIVE = 20000;
    const POLL_MS_HIDDEN = 120000;
    const CELEBRATION_GAP_MS = 1400;

    // --------------------------------------------------------------------
    // Pure helpers (no DOM, no network) — exported for tests.
    // --------------------------------------------------------------------

    /** Newest unlocks first (the API returns oldest first, oldest must go first). */
    function orderForCelebration(pending) {
        return (Array.isArray(pending) ? pending.slice() : []).sort(function (a, b) {
            return (b.unlocked_at || 0) - (a.unlocked_at || 0);
        });
    }

    /** Items eligible for a live celebration: unlocked, and not already acked. */
    function celebratable(pending) {
        return (Array.isArray(pending) ? pending : []).filter(function (item) {
            return item && item.id && !item.seen;
        });
    }

    /** Cap a burst so a first-time backfill does not fire 40 toasts at once. */
    function batch(items, size) {
        const max = Math.max(1, size || 5);
        const out = [];
        const list = orderForCelebration(items);
        for (let i = 0; i < list.length; i += max) out.push(list.slice(i, i + max));
        return out;
    }

    const CATEGORY_LABELS = {
        milestones: 'Milestones', tokens: 'Token Ocean', marathon: 'Marathon',
        variety: 'Variety', router: 'The Router', swarm: 'Swarm',
        time: 'Clockwork', projects: 'Many Rooms', economy: 'The Wallet',
    };
    const CATEGORY_ORDER = [
        'milestones', 'tokens', 'marathon', 'variety', 'router',
        'swarm', 'time', 'projects', 'economy',
    ];
    const RARITY_TIER = {
        common: 0, rare: 1, epic: 2, legendary: 3, mythic: 4,
    };

    /** Group items for the trophy-case grid, in catalog order, unlocked first. */
    function groupByCategory(items) {
        const groups = new Map();
        (Array.isArray(items) ? items : []).forEach(function (item) {
            const key = item && item.category ? item.category : 'milestones';
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(item);
        });
        // Known categories in catalog order; anything unrecognized keeps its
        // entries and lands at the end rather than being silently dropped.
        const ordered = CATEGORY_ORDER.filter(function (key) { return groups.has(key); });
        const extras = Array.from(groups.keys()).filter(function (key) {
            return CATEGORY_ORDER.indexOf(key) === -1;
        });
        return ordered.concat(extras).map(function (key) {
                const list = groups.get(key).slice().sort(function (a, b) {
                    const tier = (RARITY_TIER[a.rarity] || 0) - (RARITY_TIER[b.rarity] || 0);
                    if (tier) return tier;
                    return (a.title || '').localeCompare(b.title || '');
                });
                return { key: key, label: CATEGORY_LABELS[key] || key, items: list };
            });
    }

    function percentOf(item) {
        const target = Number(item && item.threshold) || 0;
        if (!target) return 0;
        return Math.max(0, Math.min(100, (Number(item.progress) || 0) / target * 100));
    }

    /** "12.3M" / "845k" — matches the chat footer / dashboard formatters. */
    function formatCount(value) {
        const n = Number(value) || 0;
        if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
        return String(Math.round(n));
    }

    function storageKey() {
        return 'cuttleAchievementsSeen';
    }

    /** Locally remembered acks so a reload does not re-toast. */
    function readSeen() {
        try {
            const raw = JSON.parse(localStorage.getItem(storageKey()) || '[]');
            return Array.isArray(raw) ? raw : [];
        } catch (_) {
            return [];
        }
    }

    function rememberSeen(ids) {
        try {
            const set = new Set(readSeen());
            (ids || []).forEach(function (id) { set.add(id); });
            localStorage.setItem(storageKey(), JSON.stringify(Array.from(set)));
        } catch (_) { /* storage unavailable — server ack still carries the truth */ }
    }

    function alreadySeenLocally(id) {
        return readSeen().indexOf(id) !== -1;
    }

    // --------------------------------------------------------------------
    // Network + dispatch
    // --------------------------------------------------------------------
    const api = {
        POLL_MS_ACTIVE: POLL_MS_ACTIVE,
        POLL_MS_HIDDEN: POLL_MS_HIDDEN,
        orderForCelebration: orderForCelebration,
        celebratable: celebratable,
        batch: batch,
        groupByCategory: groupByCategory,
        percentOf: percentOf,
        formatCount: formatCount,
        readSeen: readSeen,
        rememberSeen: rememberSeen,
        alreadySeenLocally: alreadySeenLocally,

        isFlagOn: function () {
            try {
                return localStorage.getItem('cuttleAchievementsEnabled') !== 'off';
            } catch (_) {
                return true;
            }
        },

        setFlagHint: function (on) {
            try { localStorage.setItem('cuttleAchievementsEnabled', on ? 'on' : 'off'); } catch (_) {}
        },

        /** Fetch unseen unlocks. `{success:false, disabled:true}` → null. */
        fetchPending: async function () {
            try {
                const r = await fetch('/api/achievements/pending', { cache: 'no-store' });
                if (!r.ok) return null;
                const d = await r.json();
                if (!d || !d.success) return null;
                return celebratable(d.pending || []).filter(function (item) {
                    return !alreadySeenLocally(item.id);
                });
            } catch (_) {
                return null;
            }
        },

        ack: async function (ids) {
            if (!ids || !ids.length) return;
            rememberSeen(ids);
            try {
                await Promise.all(ids.map(function (id) {
                    return fetch('/api/achievements/' + encodeURIComponent(id) + '/ack', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: '{}',
                    });
                }));
            } catch (_) { /* retried by the next poll via local memory */ }
        },

        /** Celebrate one unlock: toast + (rare+) sound + (epic+) confetti. */
        celebrate: function (item) {
            const celebrate = root.CuttleCelebrate;
            if (celebrate && typeof celebrate.celebrate === 'function') {
                celebrate.celebrate(item);
                return;
            }
            const toast = root.showToast || function () {};
            toast('🏆 ' + (item.title || item.id) + ' unlocked!', 'success');
        },

        /**
         * One poll cycle: fetch, celebrate the newest batch, ack it, repeat
         * after a short gap so a burst reads as a sequence rather than a wall.
         */
        poll: async function () {
            if (api._polling || !api.isFlagOn()) return null;
            api._polling = true;
            let pending = [];
            try {
                pending = await api.fetchPending();
                if (!pending || !pending.length) return null;
                const waves = batch(pending, 4);
                for (let i = 0; i < waves.length; i += 1) {
                    const wave = waves[i];
                    wave.forEach(api.celebrate);
                    await api.ack(wave.map(function (it) { return it.id; }));
                    if (i < waves.length - 1) {
                        await new Promise(function (r) { setTimeout(r, CELEBRATION_GAP_MS); });
                    }
                }
                return pending.length;
            } finally {
                api._polling = false;
            }
        },

        /** Own timer — the shell heartbeat never needs to know about this. */
        start: function () {
            if (api._timer || typeof api.poll !== 'function') return;
            const tick = function () {
                api.poll();
                const hidden = (typeof document !== 'undefined' && document.hidden);
                clearTimeout(api._timer);
                api._timer = setTimeout(tick, hidden ? POLL_MS_HIDDEN : POLL_MS_ACTIVE);
            };
            api.poll();
            api._timer = setTimeout(tick, POLL_MS_ACTIVE);
        },

        stop: function () {
            if (api._timer) clearTimeout(api._timer);
            api._timer = null;
        },
    };

    const ns = (root.CuttleAchievements = root.CuttleAchievements || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }

    // Auto-start on pages that host the shell (not on the settings page, which
    // renders the grid itself and does its own fetches).
    if (typeof document !== 'undefined' && !root.__CUTTLE_ACHIEVEMENTS_MANUAL) {
        const boot = function () { api.start(); };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', boot, { once: true });
        } else {
            boot();
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);