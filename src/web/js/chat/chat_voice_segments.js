/* Voice-mode phrase segments: one removable bubble per recognized phrase (pure). */
(function (root) {
    'use strict';

    function norm(text) {
        return String(text || '').replace(/\s+/g, ' ').trim();
    }

    function createState() {
        return { segments: [], live: '', nextId: 1, finalIndex: 0 };
    }

    /** Call when a new recognizer session starts (result indexes restart at 0). */
    function beginSession(state) {
        state.finalIndex = 0;
        state.live = '';
    }

    /**
     * Commit one finished phrase. Android/Chrome STT often re-emits the whole
     * phrase as each "final" (not a delta): that grows the previous bubble
     * instead of adding "hello" / "hello how" / "hello how are you".
     */
    function commit(state, text) {
        const t = norm(text);
        if (!t) return null;
        const last = state.segments[state.segments.length - 1];
        if (last) {
            if (t === last.text || last.text.startsWith(t)) return null;
            if (t.startsWith(last.text)) {
                last.text = t;
                return last;
            }
        }
        const seg = { id: state.nextId++, text: t };
        state.segments.push(seg);
        return seg;
    }

    /**
     * Apply a SpeechRecognition result list (array-like of
     * `{isFinal, 0: {transcript}}`): finals past `finalIndex` become
     * segments, the trailing non-final results become the live text.
     */
    function applyResults(state, results) {
        const list = results || [];
        let i = state.finalIndex;
        while (i < list.length && list[i] && list[i].isFinal) {
            const alt = list[i][0];
            commit(state, alt && alt.transcript);
            i++;
        }
        state.finalIndex = i;
        const interim = [];
        for (; i < list.length; i++) {
            const alt = list[i] && list[i][0];
            const t = norm(alt && alt.transcript);
            if (t) interim.push(t);
        }
        state.live = norm(interim.join(' '));
    }

    /** Recognizer session ended: keep any never-finalized live words. */
    function endSession(state) {
        if (state.live) commit(state, state.live);
        state.live = '';
        state.finalIndex = 0;
    }

    function remove(state, id) {
        const n = state.segments.length;
        state.segments = state.segments.filter((s) => s.id !== id);
        return state.segments.length !== n;
    }

    function utterance(state) {
        const parts = state.segments.map((s) => s.text);
        if (state.live) parts.push(state.live);
        return norm(parts.join(' '));
    }

    function isEmpty(state) {
        return !state.segments.length && !state.live;
    }

    function clear(state) {
        state.segments = [];
        state.live = '';
        state.finalIndex = 0;
    }

    const api = {
        createState: createState,
        beginSession: beginSession,
        commit: commit,
        applyResults: applyResults,
        endSession: endSession,
        remove: remove,
        utterance: utterance,
        isEmpty: isEmpty,
        clear: clear,
    };
    root.CuttleChatVoiceSegments = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
