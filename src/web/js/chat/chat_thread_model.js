/* ================================================================
   Cuttle Chat — reply-thread model (chat_thread_model.js). Pure.
   Owner: which transcript rows form a turn's reply thread — steers you
   sent into a running turn, the agent's later follow-up bubbles, and the
   thread bar’s counts,
   label, latest-reply pick and unread keys.

   No DOM, storage, page globals or Cuttle imports. The controller
   (chat_threads.js) reads the transcript into records and applies the
   result; rows are never reordered, so CH-…-N numbering is unchanged.

   Record (one per transcript row, in transcript order):
     { role: 'user'|'assistant', steered, steerIdle, turnId, messageId, live,
       ts }
   Rules:
   - A non-steer user row starts a turn.
   - The first assistant row of a turn id is that turn's root; a later
     assistant row with the same turn id is a follow-up reply.
   - A steered user row joins the thread of its turn id; a steer saved
     without one (older history, or the optimistic bubble just sent)
     joins the current turn's root if that root is already above it.
     Otherwise it stays a plain row.
   - An idle steer (``steerIdle``: sent into a detached background run
     while the chat looked idle) is a plain row that starts a new turn:
     the run's later rows with that turn id open a fresh thread below it
     instead of joining the old bubble's.
   - A thread with no replies is not a thread (no bar).
   ================================================================ */
(function (root) {
    'use strict';

    const KIND = Object.freeze({ STEER: 'steer', FOLLOWUP: 'followup' });

    function threadKeyFor(rec, index) {
        if (rec.turnId) return 't:' + rec.turnId;
        if (rec.messageId != null && rec.messageId !== '') return 'm:' + rec.messageId;
        return 'i:' + index;
    }

    function newThread(rec, index, segment) {
        const base = threadKeyFor(rec, index);
        return {
            key: segment ? base + '#' + segment : base,
            turnId: rec.turnId || '',
            root: index,
            replies: [],
            counts: { steer: 0, followup: 0 },
            live: !!rec.live,
        };
    }

    function addReply(thread, index, kind, rec) {
        thread.replies.push({ index, kind });
        thread.counts[kind] += 1;
        if (rec.live) thread.live = true;
    }

    /**
     * Group transcript records into reply threads.
     * @returns {{ threads: object[], memberOf: Int32Array-like number[] }}
     *   memberOf[i] = thread index the row belongs to (root or reply), -1 if none.
     */
    function group(records) {
        const list = Array.isArray(records) ? records : [];
        const threads = [];
        const byTurn = new Map();
        // Idle steers seen per turn id: each one starts a new segment (thread).
        const segments = new Map();
        const memberOf = new Array(list.length).fill(-1);
        let turnRoot = -1;
        list.forEach((rec, i) => {
            if (!rec) return;
            if (rec.role === 'user' && (!rec.steered || rec.steerIdle)) {
                turnRoot = -1;
                const tid = rec.steered ? (rec.turnId || '') : '';
                if (tid) {
                    byTurn.delete(tid);
                    segments.set(tid, (segments.get(tid) || 0) + 1);
                }
                return;
            }
            if (rec.role === 'assistant') {
                const tid = rec.turnId || '';
                const existing = tid ? byTurn.get(tid) : undefined;
                if (existing !== undefined) {
                    addReply(threads[existing], i, KIND.FOLLOWUP, rec);
                    memberOf[i] = existing;
                    return;
                }
                const t = newThread(rec, i, tid ? segments.get(tid) || 0 : 0);
                threads.push(t);
                const ti = threads.length - 1;
                if (tid) byTurn.set(tid, ti);
                memberOf[i] = ti;
                if (turnRoot < 0) turnRoot = ti;
                return;
            }
            if (rec.role === 'user' && rec.steered) {
                const tid = rec.turnId || '';
                let ti = tid ? byTurn.get(tid) : undefined;
                if (ti === undefined && turnRoot >= 0) {
                    const cand = threads[turnRoot];
                    // A steer that names its turn only falls back onto a root
                    // that has no turn id (older rows) — never onto another turn.
                    if (!tid || !cand.turnId) ti = turnRoot;
                }
                if (ti === undefined) return;
                addReply(threads[ti], i, KIND.STEER, rec);
                memberOf[i] = ti;
            }
        });
        const kept = [];
        const remap = new Map();
        threads.forEach((t, i) => {
            if (!t.replies.length) return;
            remap.set(i, kept.length);
            kept.push(t);
        });
        for (let i = 0; i < memberOf.length; i++) {
            const m = memberOf[i];
            memberOf[i] = m >= 0 && remap.has(m) ? remap.get(m) : -1;
        }
        kept.forEach((t) => {
            t.latest = latestReply(t, list);
            t.unreadKeys = unreadKeys(t, list);
        });
        return { threads: kept, memberOf };
    }

    function parseTime(v) {
        if (v == null || v === '') return NaN;
        const n = Number(v);
        if (Number.isFinite(n)) return n;
        const p = Date.parse(String(v));
        return Number.isFinite(p) ? p : NaN;
    }

    /** Newest reply is the last transcript row in the thread. */
    function latestReply(thread, records) {
        const last = thread.replies[thread.replies.length - 1];
        return last ? { kind: last.kind, index: last.index, at: parseTime(records[last.index].ts) } : null;
    }

    /**
     * Keys of agent-authored replies (your own steers are never unread):
     * saved follow-ups by message id.
     */
    function unreadKeys(thread, records) {
        const out = [];
        thread.replies.forEach((r) => {
            if (r.kind !== KIND.FOLLOWUP) return;
            const rec = records[r.index];
            if (rec.live || rec.messageId == null || rec.messageId === '') return;
            out.push({ key: 'm:' + rec.messageId, messageId: Number(rec.messageId), at: parseTime(rec.ts) });
        });
        return out;
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : many);
    }

    /** Bar text: "3 replies" + "2 steers · 1 follow-up". */
    function summary(thread) {
        const c = thread.counts;
        const total = c.steer + c.followup;
        const parts = [];
        if (c.steer) parts.push(plural(c.steer, 'steer', 'steers'));
        if (c.followup) parts.push(plural(c.followup, 'follow-up', 'follow-ups'));
        return { total, label: plural(total, 'reply', 'replies'), detail: parts.join(' · ') };
    }

    /** One quiet line of reply text: markup and the leading /agent prefix removed. */
    function previewText(text, max) {
        const limit = max || 120;
        let s = String(text || '');
        s = s.replace(/<think>[\s\S]*?(<\/think>|$)/gi, ' ');
        s = s.replace(/```[\s\S]*?(```|$)/g, ' ');
        s = s.replace(/<\/?cuttle_[a-z_]+[^>]*>/gi, ' ');
        s = s.replace(/\s+/g, ' ').trim();
        s = s.replace(/^\/[a-z][\w-]*\s+/i, '');
        if (s.length > limit) s = s.slice(0, limit - 1).trimEnd() + '…';
        return s;
    }

    const api = { KIND, group, summary, previewText, threadKeyFor };

    const ns = (root.CuttleChatThreadModel = root.CuttleChatThreadModel || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
