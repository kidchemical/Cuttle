/* ================================================================
   Cuttle Chat — activity / unread / follow-up queue domain
   (chat_activity.js). Owner: chat activity, unread/seen, attention,
   chirp eligibility, follow-up queue interpretation, and session-id
   normalization as pure decisions over explicit inputs: no document,
   no window, no localStorage, no fetch, no timers here. Loaded before
   chat_page.js; the page owns DOM badges, sounds, toast rendering,
   fetch/poll loops, persistence IO, streaming orchestration, and
   message rendering, and calls into `CuttleChatActivity.*`.

   Mirrors the stabilized page contract: unread-dot priority
   (error > unread > queued > paused), one-chirp-per-completion
   cooldown, manual-unread hold, follow-up batching with sticky
   prefix preservation, and CH-/numeric session-id equivalence.
   ================================================================ */
(function (root) {
    'use strict';

    /* ---------- session/chat identity normalization ---------- */

    function toAuthDbSessionId(sessionId) {
        if (sessionId == null || sessionId === '') return null;
        let s = String(sessionId).trim();
        if (s.startsWith('db_session_')) s = s.slice('db_session_'.length);
        else if (/^CH-/i.test(s)) {
            // CH-000182 or share ref CH-000182-23 → digits of the session only.
            const m = s.match(/^CH-(\d+)(?:-\d+)?$/i);
            if (m) return String(parseInt(m[1], 10));
            s = s.slice(3);
        }
        if (/^\d+$/.test(s)) return String(parseInt(s, 10));
        return s || null;
    }

    function canonicalizeChatSessionId(sessionId) {
        if (sessionId == null || sessionId === '') return sessionId;
        const bare = toAuthDbSessionId(sessionId);
        // Prefer bare numeric for auth DB chats; leave anonymous/web ids alone.
        if (bare != null && /^\d+$/.test(bare)) return bare;
        return String(sessionId);
    }

    function sessionIdsEqual(a, b) {
        if (a == null || b == null) return false;
        if (String(a) === String(b)) return true;
        const na = toAuthDbSessionId(a);
        const nb = toAuthDbSessionId(b);
        return na != null && nb != null && String(na) === String(nb);
    }

    /**
     * Human-friendly chat id shown in the corner badge (and copied on click).
     * Auth DB ids stay numeric internally; display as CH-XXXXXX.
     * Numeric sessions use zero-padded decimal (CH-000042) so they match the DB id.
     */
    function formatChatDisplayId(sessionId) {
        if (sessionId == null || sessionId === '') return null;
        const raw = String(sessionId).trim();
        if (!raw) return null;
        if (/^CH-[A-Z0-9]+$/i.test(raw)) return raw.toUpperCase();

        const bare = toAuthDbSessionId(raw);
        if (bare != null && /^\d+$/.test(bare)) {
            const n = parseInt(bare, 10);
            if (!Number.isFinite(n) || n < 0) return null;
            // Grow past 6 digits rather than truncate (session 1000000 → CH-1000000).
            return 'CH-' + String(n).padStart(6, '0');
        }

        const m = raw.match(/^(?:chat_|web_session_)(\d+)$/i);
        if (m) {
            // Timestamps / counters → stable 6-char base36 code.
            const digits = m[1].slice(-10);
            const num = parseInt(digits, 10);
            if (Number.isFinite(num) && num >= 0) {
                const mod = Math.pow(36, 6);
                const code = (num % mod).toString(36).toUpperCase().padStart(6, '0');
                return 'CH-' + code;
            }
        }

        // Stable fallback for odd string ids
        let h = 2166136261;
        for (let i = 0; i < raw.length; i++) {
            h ^= raw.charCodeAt(i);
            h = Math.imul(h, 16777619);
        }
        const code = (h >>> 0).toString(36).toUpperCase().padStart(6, '0').slice(-6);
        return 'CH-' + code;
    }

    /* ---------- unread / seen decisions ---------- */

    /**
     * History-badge unread for one session. `prefs` is the stored prefs row
     * (or null); `sessionObj` carries messages for the read-watermark
     * fallback. Currently-open-and-visible sessions read as not-unread.
     */
    function sessionHasUnread(parts) {
        const p = parts || {};
        const sessionId = p.sessionId;
        if (sessionId == null || sessionId === '') return false;
        // Currently open + visible ⇒ treat as read for the history badge.
        if (p.currentSessionId != null
            && sessionIdsEqual(p.currentSessionId, sessionId)
            && !p.backgrounded) {
            return false;
        }
        const prefs = p.prefs;
        if (prefs && prefs.hasUnread) return true;
        // Timestamp fallback only after we've recorded a read watermark (avoids
        // marking every historical chat unread on first load of this feature).
        const sessionObj = p.sessionObj;
        if (prefs && prefs.lastReadAt != null && sessionObj && Array.isArray(sessionObj.messages)) {
            const lastReadAt = Number(prefs.lastReadAt) || 0;
            for (let i = sessionObj.messages.length - 1; i >= 0; i--) {
                const m = sessionObj.messages[i];
                if (m && m.role === 'assistant') {
                    const ts = Number(m.timestamp) || 0;
                    return ts > lastReadAt;
                }
            }
        }
        return false;
    }

    /** Prefs flag only — ignores "currently viewing" so open-chat handoff can keep the title dot. */
    function prefsHasUnreadFlag(prefs) {
        return !!(prefs && prefs.hasUnread);
    }

    function prefsUnreadIsError(prefs) {
        return !!(prefs && prefs.hasUnread && prefs.unreadIsError);
    }

    /** Last assistant message on a session object (for unread-error fallback). */
    function lastAssistantMessageFromSessionObj(sessionObj) {
        if (!sessionObj || !Array.isArray(sessionObj.messages)) return null;
        for (let i = sessionObj.messages.length - 1; i >= 0; i--) {
            const m = sessionObj.messages[i];
            if (m && m.role === 'assistant') return m;
        }
        return null;
    }

    /**
     * Failed assistant reply for attention dots (red). Uses slash_command_failed
     * metadata when present; otherwise common error bubble text.
     */
    function assistantReplyLooksLikeError(content, meta) {
        if (meta && (meta.slash_command_failed || meta.failed || meta.is_error)) return true;
        const t = String(content || '').replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
        if (!t) return false;
        if (/^❌/.test(t)) return true;
        if (/Could not reach the Cuttle API/i.test(t)) return true;
        if (/^Request failed\b/i.test(t)) return true;
        if (/Sorry, I encountered an error/i.test(t)) return true;
        if (/\b(cursor|codex|muse|claude|hermes|opencode|deepseek|antigravity)_error\b/i.test(t)) {
            return true;
        }
        return false;
    }

    function sessionHasUnreadError(parts) {
        const p = parts || {};
        if (!sessionHasUnread(p)) return false;
        if (prefsUnreadIsError(p.prefs)) return true;
        const last = lastAssistantMessageFromSessionObj(p.sessionObj);
        if (!last) return false;
        let meta = last.metadata;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
        }
        if (!meta || typeof meta !== 'object') meta = {};
        if (last.slash_command_failed) meta = { ...meta, slash_command_failed: true };
        return assistantReplyLooksLikeError(last.content, meta);
    }

    /**
     * Attention kind for history dots (priority high → low):
     * error (red) > unread (green) > queued/active (amber) > paused (yellow).
     * `queue` is the already-resolved follow-up item array for the session.
     */
    function sessionHistoryAttentionKind(parts) {
        const p = parts || {};
        const queue = Array.isArray(p.queue) ? p.queue : [];
        if (sessionHasUnreadError(p)) return 'error';
        if (sessionHasUnread(p)) return 'unread';
        if (queueHasActive(queue)) return 'queued';
        if (queueHasPaused(queue)) return 'paused';
        return '';
    }

    /** True when the queue has at least one active (unpaused) prompt. */
    function queueHasActive(items) {
        return (items || []).some((x) => x && !x.paused);
    }

    /** True when the queue has at least one paused prompt. */
    function queueHasPaused(items) {
        return (items || []).some((x) => x && x.paused);
    }

    /* ---------- manual unread hold (pure transitions) ---------- */

    /** Manual "Mark as unread" while still viewing blocks focus/visibility clears. */
    function manualHoldBlocksRead(parts) {
        const p = parts || {};
        return p.holdId != null
            && p.sessionId != null
            && sessionIdsEqual(p.holdId, p.sessionId);
    }

    /**
     * Next hold value when navigating. Re-opening the held chat counts as
     * "open again" (hold clears); leaving it also releases the hold.
     */
    function releaseManualHold(parts) {
        const p = parts || {};
        const holdId = p.holdId;
        if (holdId == null) return null;
        const nextSessionId = p.nextSessionId;
        const currentSessionId = p.currentSessionId;
        if (nextSessionId != null && sessionIdsEqual(holdId, nextSessionId)) {
            return null;
        }
        if (
            currentSessionId != null
            && sessionIdsEqual(holdId, currentSessionId)
            && (nextSessionId == null || !sessionIdsEqual(nextSessionId, currentSessionId))
        ) {
            return null;
        }
        return holdId;
    }

    /* ---------- open-chat attention state (pure reducer) ---------- */

    function defaultAttentionState() {
        return {
            unseenBelow: false,
            needsAck: false,
            isError: false,
            viewportActivated: true,
        };
    }

    function attentionIsActive(state) {
        const s = state || {};
        return !!(s.unseenBelow || s.needsAck);
    }

    function attentionAfterReset(state, opts) {
        const needsAck = !!(opts && opts.needsAck);
        const isError = !!(opts && opts.needsAck && opts.isError);
        return {
            unseenBelow: false,
            needsAck,
            isError,
            // Unread open → require click/type. Clean open → already "active".
            viewportActivated: !needsAck,
        };
    }

    /**
     * Finalized assistant reply only — not mid-stream / generating.
     *
     * Green (or red-on-error) jump glow → content landed below the fold.
     * Title unread dot → unseen below, OR in-view but viewport never activated.
     * Does NOT re-nag after every reply once the viewport is already active.
     */
    function attentionAfterFinalized(state, opts) {
        const s = Object.assign({}, defaultAttentionState(), state);
        const isError = !!(opts && opts.isError);
        const scrolledAway = !!(opts && opts.scrolledAway);
        if (scrolledAway) {
            s.unseenBelow = true;
            s.isError = s.isError || isError;
        } else if (!s.viewportActivated) {
            s.needsAck = true;
            s.isError = s.isError || isError;
        } else if (isError) {
            // Bottom-pinned error still deserves a red title nag until ack.
            s.needsAck = true;
            s.isError = true;
            s.viewportActivated = false;
        }
        return s;
    }

    function attentionAfterClearUnseen(state) {
        const s = Object.assign({}, state);
        if (!s.unseenBelow) return s;
        s.unseenBelow = false;
        if (!s.needsAck) s.isError = false;
        return s;
    }

    /** Click in transcript or typing in composer acknowledges the open chat. */
    function attentionAfterActivate(state) {
        const s = Object.assign({}, state);
        s.viewportActivated = true;
        if (!s.needsAck) return s;
        s.needsAck = false;
        s.isError = false;
        return s;
    }

    /* ---------- chirp + response-ready plan ---------- */

    /** One chirp per session completion — cooldown blocks double-fire stacking. */
    function shouldChirp(parts) {
        const p = parts || {};
        const now = Number(p.nowMs) || 0;
        const lastChirp = Number(p.lastChirpAt) || 0;
        const cooldown = p.cooldownMs != null ? Number(p.cooldownMs) : 8000;
        return (now - lastChirp) >= cooldown;
    }

    /**
     * Dispatch plan for a finalized assistant reply. The page executes it:
     * stamps/plays the chirp, marks read/unread, shows the toast, syncs.
     * Returns { chirp, viewingThis, mark: 'read'|'unread', toast: bool }.
     */
    function responseReadyPlan(parts) {
        const p = parts || {};
        const sid = p.sessionId != null ? String(p.sessionId) : null;
        if (!sid) return null;
        const now = Number(p.nowMs) || 0;
        const chirp = shouldChirp({
            lastChirpAt: p.lastChirpAt,
            nowMs: now,
            cooldownMs: p.cooldownMs,
        });
        const viewingThis = p.currentSessionId != null
            && sessionIdsEqual(p.currentSessionId, sid)
            && !p.backgrounded;
        const isError = !!p.isError;
        if (viewingThis) {
            return { chirp, nowMs: now, viewingThis: true, mark: 'read', toast: false, isError };
        }
        // Avoid duplicate "Reply ready" toasts from sync shortly after local notify.
        const fromSync = !!p.fromSync;
        return {
            chirp,
            nowMs: now,
            viewingThis: false,
            mark: 'unread',
            toast: chirp || !fromSync,
            isError,
        };
    }

    /* ---------- follow-up queue interpretation ---------- */

    function parseFollowupQueue(raw) {
        if (Array.isArray(raw)) return raw;
        if (typeof raw === 'string' && raw.trim()) {
            try {
                const parsed = JSON.parse(raw);
                return Array.isArray(parsed) ? parsed : [];
            } catch (_) {
                return [];
            }
        }
        return [];
    }

    function followupQueueFingerprint(items) {
        return (items || []).map((x) => (
            String(x && x.id || '')
            + ':' + String(x && (x.rawMessage || x.content) || '')
            + ':' + (x && x.paused ? '1' : '0')
        )).join('|');
    }

    /** Shared item normalization (server take + local fallback paths). */
    function normalizeFollowupItem(item, nowMs) {
        const t = nowMs != null ? nowMs : Date.now();
        return {
            id: String((item && item.id) || ('fq_' + Number(t).toString(36))),
            content: String((item && (item.content || item.rawMessage)) || ''),
            created: Number(item && item.created) || Number(t),
            attachments: Array.isArray(item && item.attachments) ? item.attachments : [],
            rawMessage: item && item.rawMessage != null
                ? String(item.rawMessage)
                : String((item && item.content) || ''),
            paused: !!(item && item.paused),
        };
    }

    /**
     * Split the live queue into the next outbound batch (active, not being
     * edited) and what stays queued (paused or under edit).
     */
    function partitionFollowupForDrain(items, editingId) {
        const list = Array.isArray(items) ? items : [];
        return {
            batch: list.filter((x) => !x.paused && x.id !== editingId),
            remaining: list.filter((x) => x.paused || x.id === editingId),
        };
    }

    /**
     * Merge every pending follow-up into one outbound turn so the agent sees
     * the full queue at once (instead of N serial replies).
     * Sticky slash prefix from the first item is kept on the combined message
     * so /cursor · /claude routing still works.
     * `deps`: { stickyPrefixFor(raw) → string, formatWithAttachments(text, atts) → string }.
     */
    function combineFollowupBatch(items, deps) {
        const d = deps || {};
        const stickyPrefixFor = typeof d.stickyPrefixFor === 'function' ? d.stickyPrefixFor : null;
        const formatWithAttachments = typeof d.formatWithAttachments === 'function'
            ? d.formatWithAttachments
            : (text) => String(text || '');
        const parts = (items || []).map((item) => {
            const atts = Array.isArray(item && item.attachments) ? item.attachments.slice() : [];
            const raw = String(
                item && item.rawMessage != null ? item.rawMessage : (item && item.content) || ''
            ).trim();
            const content = String((item && item.content) || '').trim()
                || formatWithAttachments(raw, atts);
            return { raw, content, atts };
        }).filter((p) => p.raw || p.atts.length);

        if (!parts.length) return null;
        if (parts.length === 1) {
            const p = parts[0];
            return {
                message: p.raw || '(see attached files)',
                displayMessage: p.content || formatWithAttachments('', p.atts),
                attachments: p.atts,
            };
        }

        const sharedPrefix = stickyPrefixFor ? (stickyPrefixFor(parts[0].raw) || '') : '';
        const bodies = parts.map((p) => {
            let body = p.raw;
            if (sharedPrefix && body.startsWith(sharedPrefix)) {
                body = body.slice(sharedPrefix.length).trim();
            }
            if (!body && p.atts.length) body = '(see attached files)';
            return body || '(empty)';
        });
        const numbered = bodies.map((b, i) => `${i + 1}. ${b}`).join('\n\n');
        const combinedBody = (
            `The user queued ${parts.length} follow-ups while you were busy. `
            + `Address all of them in this turn:\n\n${numbered}`
        );
        const message = sharedPrefix
            ? (sharedPrefix.endsWith(' ') ? sharedPrefix + combinedBody : sharedPrefix + ' ' + combinedBody)
            : combinedBody;
        const attachments = parts.reduce((acc, p) => acc.concat(p.atts), []);
        const displayMessage = formatWithAttachments(message, attachments);
        return { message, displayMessage, attachments };
    }

    /* ---------- running / live-status activity ---------- */

    function liveStatusLooksActive(liveStatus, nowMs) {
        const live = liveStatus;
        if (!live) return false;
        // Stop / cancelled turn — refresh must not resurrect the spinner from
        // a leftover live-status row (CH-000522).
        if (live.cancelled) return false;
        // Busy lock means a worker is still running even if the last status
        // text is old (long Cursor tool with no stream events). Aging that out
        // made the UI paint "No response received." mid-run.
        if (live.generating) return true;
        const flagged = !!live.active;
        if (!flagged) return false;
        // updated_at is unix seconds from Flask. Stale rows (crashed worker /
        // uncleared tool status) used to resurrect the spinner on every refresh.
        const ts = Number(live.updated_at);
        if (!Number.isFinite(ts) || ts <= 0) return flagged;
        const now = nowMs != null ? nowMs : Date.now();
        const ageMs = now - ts * 1000;
        return ageMs < 180000; // 3 minutes
    }

    /** A delayed status snapshot from a turn whose reply already painted. */
    function liveStatusPredatesReply(live, reply) {
        if (!live || !reply) return false;
        if (live.query_id && reply.queryId) {
            return String(live.query_id) === String(reply.queryId);
        }
        const updatedMs = Number(live.updated_at) * 1000;
        const repliedMs = Number(reply.timestamp);
        return updatedMs > 0 && repliedMs > 0 && updatedMs <= repliedMs;
    }

    /**
     * History-row class → activity kind for the shell broadcast snapshot
     * (Spaces activity indicators consume the posted array, not the DOM).
     */
    function activityClassToKind(className) {
        const c = String(className || '');
        if (/\bhas-unread-error\b/.test(c)) return 'error';
        if (/\bhas-unread\b/.test(c)) return 'unread';
        if (/\bhas-queued\b/.test(c)) return 'queued';
        if (/\bhas-paused-queue\b/.test(c)) return 'paused';
        return '';
    }

    const api = {
        toAuthDbSessionId,
        canonicalizeChatSessionId,
        sessionIdsEqual,
        formatChatDisplayId,
        sessionHasUnread,
        prefsHasUnreadFlag,
        prefsUnreadIsError,
        lastAssistantMessageFromSessionObj,
        assistantReplyLooksLikeError,
        sessionHasUnreadError,
        sessionHistoryAttentionKind,
        queueHasActive,
        queueHasPaused,
        manualHoldBlocksRead,
        releaseManualHold,
        defaultAttentionState,
        attentionIsActive,
        attentionAfterReset,
        attentionAfterFinalized,
        attentionAfterClearUnseen,
        attentionAfterActivate,
        shouldChirp,
        responseReadyPlan,
        parseFollowupQueue,
        followupQueueFingerprint,
        normalizeFollowupItem,
        partitionFollowupForDrain,
        combineFollowupBatch,
        liveStatusLooksActive,
        liveStatusPredatesReply,
        activityClassToKind,
    };

    const ns = (root.CuttleChatActivity = root.CuttleChatActivity || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
