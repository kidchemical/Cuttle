/* ================================================================
   Cuttle Chat — reply-thread model (chat_thread_model.js). Pure.
   Owner: which transcript rows form a turn's reply thread — steers you
   sent into a running turn, the agent's later follow-up bubbles, and the
   sub-agent children that turn spawned — plus the thread bar's counts,
   label, latest-reply pick and unread keys.

   No DOM, storage, page globals or Cuttle imports. The controller
   (chat_threads.js) reads the transcript into records and applies the
   result; rows are never reordered, so CH-…-N numbering is unchanged.

   Record (one per transcript row, in transcript order):
     { role: 'user'|'assistant', steered, steerIdle, turnId, messageId, live,
       subagents: [{ handle, label, displayName, avatar, outcome,
                     summary, detail, finishedAt }], ts }
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
   - Finished sub-agent children (fleet cards on any member row) are
     sub-agent replies; running ones only count as `working`.
   - A thread with no replies is not a thread (no bar).
   ================================================================ */
(function (root) {
    'use strict';

    const KIND = Object.freeze({ STEER: 'steer', FOLLOWUP: 'followup', SUBAGENT: 'subagent' });
    const SUBAGENT_FINISHED = new Set(['done', 'failed', 'cancelled', 'lost']);

    function threadKeyFor(rec, index) {
        if (rec.turnId) return 't:' + rec.turnId;
        if (rec.messageId != null && rec.messageId !== '') return 'm:' + rec.messageId;
        return 'i:' + index;
    }

    function subagentKey(s) {
        return String(s.handle || s.sessionId || s.label || '');
    }

    function newThread(rec, index, segment) {
        const base = threadKeyFor(rec, index);
        return {
            key: segment ? base + '#' + segment : base,
            turnId: rec.turnId || '',
            root: index,
            replies: [],
            subagents: [],
            counts: { steer: 0, followup: 0, subagent: 0 },
            working: 0,
            live: !!rec.live,
            _subSeen: new Set(),
        };
    }

    /**
     * Finished children are replies; running / queued ones only show as
     * "N working" on the bar (their fleet cards already show progress).
     */
    function addSubagents(thread, rec) {
        const list = Array.isArray(rec.subagents) ? rec.subagents : [];
        list.forEach((s) => {
            if (!s || typeof s !== 'object') return;
            const k = subagentKey(s);
            if (!k || thread._subSeen.has(k)) return;
            thread._subSeen.add(k);
            const outcome = String(s.outcome || '');
            if (SUBAGENT_FINISHED.has(outcome)) {
                thread.subagents.push(s);
                thread.counts.subagent += 1;
            } else if (s.generating || outcome === 'running' || outcome === 'queued') {
                thread.working += 1;
            }
        });
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
                    addSubagents(threads[existing], rec);
                    memberOf[i] = existing;
                    return;
                }
                const t = newThread(rec, i, tid ? segments.get(tid) || 0 : 0);
                threads.push(t);
                const ti = threads.length - 1;
                if (tid) byTurn.set(tid, ti);
                memberOf[i] = ti;
                if (turnRoot < 0) turnRoot = ti;
                addSubagents(t, rec);
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
            if (!t.replies.length && !t.subagents.length) return;
            remap.set(i, kept.length);
            kept.push(t);
        });
        for (let i = 0; i < memberOf.length; i++) {
            const m = memberOf[i];
            memberOf[i] = m >= 0 && remap.has(m) ? remap.get(m) : -1;
        }
        kept.forEach((t) => {
            delete t._subSeen;
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

    /** Newest reply: the last row reply, unless a sub-agent finished after it. */
    function latestReply(thread, records) {
        const lastRow = thread.replies.length ? thread.replies[thread.replies.length - 1] : null;
        let best = lastRow ? { kind: lastRow.kind, index: lastRow.index, at: parseTime(records[lastRow.index].ts) } : null;
        thread.subagents.forEach((s) => {
            const at = parseTime(s.finishedAt);
            if (!best || (Number.isFinite(at) && (!Number.isFinite(best.at) || at > best.at))) {
                best = { kind: KIND.SUBAGENT, subagent: s, at };
            }
        });
        if (!best && thread.subagents.length) {
            best = { kind: KIND.SUBAGENT, subagent: thread.subagents[thread.subagents.length - 1], at: NaN };
        }
        return best;
    }

    /**
     * Keys of agent-authored replies (your own steers are never unread):
     * saved follow-ups by message id, finished sub-agents by handle + outcome.
     */
    function unreadKeys(thread, records) {
        const out = [];
        thread.replies.forEach((r) => {
            if (r.kind !== KIND.FOLLOWUP) return;
            const rec = records[r.index];
            if (rec.live || rec.messageId == null || rec.messageId === '') return;
            out.push({ key: 'm:' + rec.messageId, messageId: Number(rec.messageId), at: parseTime(rec.ts) });
        });
        thread.subagents.forEach((s) => {
            const outcome = String(s.outcome || '');
            out.push({ key: 's:' + subagentKey(s) + ':' + outcome, messageId: null, at: parseTime(s.finishedAt) });
        });
        return out;
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : many);
    }

    /** Bar text: "3 replies" + "2 steers · 1 follow-up · 1 sub-agent". */
    function summary(thread) {
        const c = thread.counts;
        const total = c.steer + c.followup + c.subagent;
        const parts = [];
        if (c.steer) parts.push(plural(c.steer, 'steer', 'steers'));
        if (c.followup) parts.push(plural(c.followup, 'follow-up', 'follow-ups'));
        if (c.subagent) parts.push(plural(c.subagent, 'sub-agent', 'sub-agents'));
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
