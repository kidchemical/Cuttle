/*
 * Voice narrator pacing (experimental `voice_narrator`): decides when a voice
 * turn gets a spoken progress line, plus the /api/voice-narrator transport.
 * Playback and phase checks stay in chat_voice.js.
 */
(function (root) {
    'use strict';

    /** Let the acknowledgment breathe before the first progress line. */
    const FIRST_PROGRESS_MS = 8000;
    const MIN_GAP_MS = 15000;
    const MAX_LINES_PER_TURN = 8;
    const MAX_PENDING_EVENTS = 12;

    const GENERIC_STATUS = /^(connecting|still connecting|thinking|working|queued|waiting|starting|running)\b[.…\s]*$/i;

    function norm(text) {
        return String(text || '').replace(/\s+/g, ' ').trim();
    }

    function isMeaningfulStatus(text) {
        const t = norm(text);
        return t.length >= 4 && !GENERIC_STATUS.test(t);
    }

    function createPlan() {
        return {
            turn: 0, open: false, message: '', events: [], lastEvent: '',
            said: [], lastAt: 0, inflight: false, speaking: false,
        };
    }

    /** A new voice turn was submitted; returns its token. */
    function beginTurn(plan, message, now) {
        plan.turn += 1;
        plan.open = true;
        plan.message = String(message || '');
        plan.events = [];
        plan.lastEvent = '';
        plan.said = [];
        plan.lastAt = now - MIN_GAP_MS + FIRST_PROGRESS_MS;
        plan.inflight = false;
        plan.speaking = false;
        return plan.turn;
    }

    function endTurn(plan) {
        plan.open = false;
        plan.events = [];
        plan.inflight = false;
        plan.speaking = false;
    }

    function isCurrent(plan, turn) {
        return plan.open && plan.turn === turn;
    }

    /** Record a live status line; true when it is new progress worth narrating. */
    function noteStatus(plan, text) {
        if (!plan.open || !isMeaningfulStatus(text)) return false;
        const t = norm(text);
        if (t === plan.lastEvent) return false;
        plan.lastEvent = t;
        plan.events.push(t);
        if (plan.events.length > MAX_PENDING_EVENTS) plan.events.shift();
        return true;
    }

    /** ms until a progress line may be requested; null when nothing is pending. */
    function dueIn(plan, now) {
        if (!plan.open || !plan.events.length || plan.inflight || plan.speaking) return null;
        if (plan.said.length >= MAX_LINES_PER_TURN) return null;
        return Math.max(0, plan.lastAt + MIN_GAP_MS - now);
    }

    /** Claim the pending events as one progress request (or null if not due). */
    function takeProgress(plan, now) {
        if (dueIn(plan, now) !== 0) return null;
        const payload = {
            kind: 'progress',
            message: plan.message,
            events: plan.events.slice(),
            said: plan.said.slice(),
        };
        plan.events = [];
        plan.inflight = true;
        plan.lastAt = now;
        return payload;
    }

    function finishRequest(plan) {
        plan.inflight = false;
    }

    function noteSaid(plan, text, now) {
        plan.said.push(norm(text));
        plan.lastAt = now;
    }

    /** No progress request while a narration clip is playing. */
    function setSpeaking(plan, speaking) {
        plan.speaking = !!speaking;
    }

    /**
     * POST one narration request. Resolves `{disabled: true}`, `null` (nothing
     * worth saying / failure), or `{text, url}` with a playable audio URL.
     */
    async function request(fetchFn, payload) {
        let d;
        try {
            const r = await fetchFn('/api/voice-narrator/narrate', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            d = await r.json().catch(() => ({}));
            if (!r.ok) return null;
        } catch (_) {
            return null;
        }
        if (d && d.disabled) return { disabled: true };
        if (!d || !d.success || !d.text || !d.audio_base64) return null;
        const bin = atob(d.audio_base64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: d.content_type || 'audio/mpeg' });
        return { text: String(d.text), url: URL.createObjectURL(blob) };
    }

    const api = {
        FIRST_PROGRESS_MS: FIRST_PROGRESS_MS,
        MIN_GAP_MS: MIN_GAP_MS,
        MAX_LINES_PER_TURN: MAX_LINES_PER_TURN,
        isMeaningfulStatus: isMeaningfulStatus,
        createPlan: createPlan,
        beginTurn: beginTurn,
        endTurn: endTurn,
        isCurrent: isCurrent,
        noteStatus: noteStatus,
        dueIn: dueIn,
        takeProgress: takeProgress,
        finishRequest: finishRequest,
        noteSaid: noteSaid,
        setSpeaking: setSpeaking,
        request: request,
    };
    root.CuttleChatVoiceNarrator = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
