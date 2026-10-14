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
     * Split one transcript into sentence-sized phrases, keeping terminal
     * punctuation. A recognizer/VAD clip can carry several sentences ("Book a
     * flight. Then a hotel."); each sentence is its own removable bubble so the
     * split matches what was actually said. A split needs whitespace after the
     * punctuation, so decimals ("3.14") stay intact.
     */
    function splitSentences(text) {
        const t = norm(text);
        if (!t) return [];
        const parts = [];
        let rest = t;
        let at = rest.search(/[.!?…]\s+/);
        while (at >= 0) {
            parts.push(norm(rest.slice(0, at + 1)));
            rest = rest.slice(at + 1);
            at = rest.search(/[.!?…]\s+/);
        }
        if (norm(rest)) parts.push(norm(rest));
        return parts.length ? parts : [t];
    }

    /** Join re-emitted phrases that share a word overlap ("hello how" + "how are you"). */
    function overlapJoin(a, b) {
        const aw = a.split(' ');
        const bw = b.split(' ');
        const max = Math.min(aw.length, bw.length);
        for (let n = max; n >= 1; n--) {
            if (aw.slice(-n).join(' ').toLowerCase() === bw.slice(0, n).join(' ').toLowerCase()) {
                const rest = bw.slice(n).join(' ');
                return rest ? a + ' ' + rest : a;
            }
        }
        return '';
    }

    /** Replace one segment with one segment per sentence it now contains. */
    function resegment(state, seg) {
        const parts = splitSentences(seg.text);
        if (parts.length <= 1) {
            seg.text = parts[0] || seg.text;
            return seg;
        }
        const index = state.segments.indexOf(seg);
        const rows = parts.map((text, i) => ({ id: i === 0 ? seg.id : state.nextId++, text: text }));
        state.segments.splice(index, 1, ...rows);
        return rows[rows.length - 1];
    }

    /**
     * Commit one finished phrase. Android/Chrome STT often re-emits the whole
     * phrase as each "final" (not a delta): that grows the previous bubble
     * instead of adding "hello" / "hello how" / "hello how are you". A final
     * that only re-emits the previous words with a new tail is joined once, and
     * a phrase carrying several sentences becomes one bubble per sentence.
     */
    function commit(state, text) {
        const t = norm(text);
        if (!t) return null;
        const last = state.segments[state.segments.length - 1];
        if (last) {
            if (t === last.text || last.text.startsWith(t)) return null;
            if (t.startsWith(last.text)) {
                last.text = t;
                return resegment(state, last);
            }
            const joined = overlapJoin(last.text, t);
            if (joined && joined !== last.text) {
                last.text = joined;
                return resegment(state, last);
            }
        }
        const parts = splitSentences(t);
        let seg = null;
        parts.forEach((part) => {
            seg = { id: state.nextId++, text: part };
            state.segments.push(seg);
        });
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
        splitSentences: splitSentences,
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
