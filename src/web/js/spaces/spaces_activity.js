/* ================================================================
   Cuttle Spaces — activity aggregation (spaces_activity.js)
   Owner: Spaces subsystem. Pure aggregation: which dot a space tab shows.
   Priority: running > error > unread > queued > paused > none.
   Transport (server poll, localStorage reads, postMessage) and DOM
   patching stay in the shell; the observation maps live here as
   explicitly owned module state (resettable for tests).

   Inputs, and who wins:
   - Server poll (`noteServerSnapshot`): authoritative `generating` +
     follow-up queue for every chat it lists.
   - Chat iframe pushes (`notePushSnapshot`): each push *replaces* that
     frame's previous snapshot and only describes chats the frame owns
     (open chat, its local turn, pending action forms). A pushed spinner
     counts until a server poll that started after it says idle; a frame
     that re-pushes an old spinner cannot revive it.
   - Chat prefs (`setUnreadPrefs`): the shared unread/error flags — the
     same localStorage row the history panel reads.
   - Local-mode queues (`setLocalQueues`).
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
    /** A just-raised spinner survives one server poll that raced the turn start. */
    const PUSH_RUNNING_GRACE_MS = 4000;

    /** sourceKey -> { at, owned:Set<bare>, entries:Map<bare,{activity,running}>, runningSince:Map<bare,ms> } */
    const _sources = new Map();
    /** bare -> { running, queue, at } — `at` is the poll start time. */
    const _server = new Map();
    /** bare -> '' | 'unread' | 'error' (a row exists ⇒ prefs decide). */
    let _prefs = new Map();
    /** bare -> 'queued' | 'paused' (local mode). */
    let _local = new Map();

    function resetActivity() {
        _sources.clear();
        _server.clear();
        _prefs = new Map();
        _local = new Map();
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

    /** One key per chat: '7' for '7', 'db_session_7', 'CH-000007', 'CH-000007-3'. */
    function bareSid(sid) {
        let s = String(sid == null ? '' : sid).trim();
        if (!s) return '';
        if (s.startsWith('db_session_')) s = s.slice('db_session_'.length);
        const ch = s.match(/^CH-(\d+)(?:-\d+)?$/i);
        if (ch) return String(parseInt(ch[1], 10));
        if (/^\d+$/.test(s)) return String(parseInt(s, 10));
        return s;
    }

    function cleanKind(activity) {
        return activity === 'error' || activity === 'unread'
            || activity === 'queued' || activity === 'paused' ? activity : '';
    }

    /**
     * Replace one chat frame's snapshot.
     * @param sourceKey: stable key for the frame (its window object)
     * @param sessions: [{ id, activity, running }]
     * @param owned: ids this frame speaks for (idle ones included); omitted ⇒
     *   the ids in `sessions`
     */
    function notePushSnapshot(sourceKey, sessions, owned, now) {
        const at = now != null ? now : Date.now();
        const prev = _sources.get(sourceKey) || null;
        const entries = new Map();
        (Array.isArray(sessions) ? sessions : []).forEach((s) => {
            if (!s) return;
            const bare = bareSid(s.id);
            if (!bare) return;
            entries.set(bare, { activity: cleanKind(s.activity), running: !!s.running });
        });
        const ownedSet = new Set();
        (Array.isArray(owned) ? owned : [...entries.keys()]).forEach((id) => {
            const bare = bareSid(id);
            if (bare) ownedSet.add(bare);
        });
        entries.forEach((_, bare) => ownedSet.add(bare));
        const runningSince = new Map();
        entries.forEach((e, bare) => {
            if (!e.running) return;
            const since = prev && prev.runningSince.get(bare);
            runningSince.set(bare, since != null ? since : at);
        });
        _sources.set(sourceKey, { at, owned: ownedSet, entries, runningSince });
    }

    /** Drop snapshots from frames that no longer exist. */
    function pruneSources(isLive) {
        [..._sources.keys()].forEach((key) => {
            let live = false;
            try { live = !!isLive(key); } catch (_) { live = false; }
            if (!live) _sources.delete(key);
        });
    }

    /**
     * Record one server poll.
     * @param rows: [{ id, running, queue }] — `queue` null/undefined keeps the
     *   previous queue kind (live-status batch knows only `running`)
     * @param startedAt: when the poll request was sent
     * @returns bare ids the server saw finish (running → idle) this poll
     */
    function noteServerSnapshot(rows, startedAt) {
        const at = startedAt != null ? startedAt : Date.now();
        const finished = [];
        (Array.isArray(rows) ? rows : []).forEach((r) => {
            if (!r) return;
            const bare = bareSid(r.id);
            if (!bare) return;
            const prev = _server.get(bare) || null;
            const running = !!r.running;
            const queue = r.queue == null ? (prev ? prev.queue : '') : cleanKind(r.queue);
            if (prev && prev.running && !running) finished.push(bare);
            _server.set(bare, { running, queue, at });
        });
        return finished;
    }

    /** Replace unread flags from the chat prefs map ({ sid: { hasUnread, unreadIsError } }). */
    function setUnreadPrefs(prefsMap) {
        const next = new Map();
        Object.keys(prefsMap || {}).forEach((sid) => {
            const p = prefsMap[sid];
            if (!p || typeof p !== 'object') return;
            const bare = bareSid(sid);
            if (!bare) return;
            const kind = p.hasUnread ? (p.unreadIsError ? 'error' : 'unread') : '';
            // Duplicate rows (temp id + db id) — any unread row wins.
            if (!next.has(bare) || (kind && RANK[kind] > (RANK[next.get(bare)] || 0))) {
                next.set(bare, kind);
            }
        });
        _prefs = next;
    }

    /** Replace local-mode follow-up queues ({ sid: itemsArray }). */
    function setLocalQueues(queuesBySid) {
        const next = new Map();
        Object.keys(queuesBySid || {}).forEach((sid) => {
            const kind = followupKind(queuesBySid[sid]);
            const bare = bareSid(sid);
            if (kind && bare) next.set(bare, kind);
        });
        _local = next;
    }

    /** Effective { activity, running } for one chat, or null when idle. */
    function lookupSessionActivity(sid, now) {
        const bare = bareSid(sid);
        if (!bare) return null;
        const t = now != null ? now : Date.now();
        const srv = _server.get(bare) || null;

        let ownedAt = 0;
        let ownedRunning = false;
        let ownedRunningSince = 0;
        let ownedQueue = '';
        let ownedUnread = '';
        _sources.forEach((src) => {
            if (!src.owned.has(bare)) return;
            const e = src.entries.get(bare) || { activity: '', running: false };
            if (src.at >= ownedAt) {
                ownedAt = src.at;
                ownedQueue = e.activity === 'queued' || e.activity === 'paused' ? e.activity : '';
            }
            if (e.running) {
                ownedRunning = true;
                ownedRunningSince = Math.max(ownedRunningSince, src.runningSince.get(bare) || src.at);
            }
            if ((e.activity === 'unread' || e.activity === 'error')
                && RANK[e.activity] > (RANK[ownedUnread] || 0)) {
                ownedUnread = e.activity;
            }
        });

        let running = false;
        if (ownedRunning) {
            // A poll sent after the frame raised its spinner found the chat idle.
            const staleByServer = !!(srv && !srv.running && srv.at > ownedRunningSince
                && (t - ownedRunningSince) > PUSH_RUNNING_GRACE_MS);
            running = !staleByServer;
        }
        if (!running && srv && srv.running) {
            // The owning frame reported idle after this poll — it saw the end.
            running = !(ownedAt > srv.at && !ownedRunning);
        }

        let activity = _prefs.has(bare) ? _prefs.get(bare) : ownedUnread;
        if (!activity) {
            if (ownedAt && (!srv || ownedAt >= srv.at)) activity = ownedQueue;
            else if (srv) activity = srv.queue || '';
            if (!activity && _local.has(bare)) activity = _local.get(bare);
        }
        if (!running && !activity) return null;
        return { activity: activity || '', running };
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
        const visible = new Set();
        if (visibleSet instanceof Set) {
            visibleSet.forEach((s) => { const b = bareSid(s); if (b) visible.add(b); });
        }
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
            if (isActive && (kind === 'unread' || kind === 'error') && visible.has(bareSid(sid))) {
                return;
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
        PUSH_RUNNING_GRACE_MS,
        resetActivity,
        sidVariants,
        bareSid,
        notePushSnapshot,
        pruneSources,
        noteServerSnapshot,
        setUnreadPrefs,
        setLocalQueues,
        lookupSessionActivity,
        followupKind,
        selectSpaceActivity,
    };

    const ns = (root.CuttleSpaces = root.CuttleSpaces || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);
