/* ================================================================
   Cuttle Chat — live text of a composer voice clip
   (chat_voice_clip_text.js).
   Owner: the words of a recording as they appear in the composer —
   one editable block of text per clip, kept track of while the user
   edits around and inside it, removed with its clip — and the two
   engines that produce those words while recording:
     'server'   pause-cut phrases sent to /api/voice-stt/transcribe
                (the provider chosen in Settings → Voice);
     'browser'  the browser's own speech recognizer, with interim words.

   Not owned here: recording, the recording bar and the attachment
   (`chat_voice_clip.js`), mic capture and the phrase upload queue
   (`CuttleChatVoiceRecorder`).

   Pure helpers (`applyEdit`, `write`, `remove`, `textOf`) are exported
   for node tests; `createDraft` and `createSession` touch the DOM.
   ================================================================ */
(function (root) {
    'use strict';

    /** Longest wait for the last phrases' text when a recording stops. */
    const FLUSH_TIMEOUT_MS = 20000;

    function cloneBlocks(blocks) {
        const out = {};
        for (const id of Object.keys(blocks || {})) out[id] = Object.assign({}, blocks[id]);
        return out;
    }

    /**
     * Blocks are `{start, end, tail}` ranges of the composer text; `tail` is
     * how many characters at the end are interim words still being heard.
     * Move them across a user edit, found as the changed middle between the
     * common prefix and suffix of the old and new text. Typing at a block's
     * edge stays outside it; edits inside grow or shrink it.
     */
    function applyEdit(blocks, oldValue, newValue) {
        const a = String(oldValue == null ? '' : oldValue);
        const b = String(newValue == null ? '' : newValue);
        const out = cloneBlocks(blocks);
        if (a === b) return out;
        const max = Math.min(a.length, b.length);
        let p = 0;
        while (p < max && a.charCodeAt(p) === b.charCodeAt(p)) p++;
        let s = 0;
        while (s < max - p && a.charCodeAt(a.length - 1 - s) === b.charCodeAt(b.length - 1 - s)) s++;
        const oldEnd = a.length - s;
        const newEnd = b.length - s;
        const delta = b.length - a.length;
        for (const id of Object.keys(out)) {
            const blk = out[id];
            const start = blk.start >= oldEnd ? blk.start + delta : (blk.start <= p ? blk.start : newEnd);
            const end = blk.end <= p ? blk.end : (blk.end >= oldEnd ? blk.end + delta : p);
            blk.start = Math.max(0, Math.min(start, b.length));
            blk.end = Math.max(blk.start, Math.min(end, b.length));
            blk.tail = Math.min(blk.tail || 0, blk.end - blk.start);
        }
        return out;
    }

    function shiftAfter(blocks, skipId, from, delta) {
        for (const id of Object.keys(blocks)) {
            if (id === skipId) continue;
            const blk = blocks[id];
            if (blk.start >= from) {
                blk.start += delta;
                blk.end += delta;
            }
        }
    }

    /**
     * Put heard words into clip `id`'s block: `text` replaces the block's
     * interim tail; `final` commits it (later words go after it). A clip's
     * first words start a new block at the end of the composer text.
     * Returns `{value, blocks}`.
     */
    function write(value, blocks, id, text, final) {
        let v = String(value == null ? '' : value);
        const out = cloneBlocks(blocks);
        const words = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
        let blk = out[id];
        if (!blk) {
            if (!words) return { value: v, blocks: out };
            const sep = v && !/\s$/.test(v) ? ' ' : '';
            v += sep;
            blk = out[id] = { start: v.length, end: v.length, tail: 0 };
        }
        const tailStart = blk.end - (blk.tail || 0);
        const head = v.slice(blk.start, tailStart);
        const piece = words ? (head && !/\s$/.test(head) ? ' ' : '') + words : '';
        const delta = piece.length - (blk.tail || 0);
        v = v.slice(0, tailStart) + piece + v.slice(blk.end);
        shiftAfter(out, id, blk.end, delta);
        blk.end = tailStart + piece.length;
        blk.tail = final ? 0 : piece.length;
        return { value: v, blocks: out };
    }

    /** The words currently in clip `id`'s block ('' when it has none). */
    function textOf(value, blocks, id) {
        const blk = (blocks || {})[id];
        return blk ? String(value || '').slice(blk.start, blk.end).trim() : '';
    }

    /**
     * Take clip `id`'s block out of the text (with one adjoining space, so
     * no gap is left). Without a tracked block — the page was reloaded —
     * `fallback` (the clip's words when it was attached) is removed if the
     * text still contains it. Returns `{value, blocks, removed}`.
     */
    function remove(value, blocks, id, fallback) {
        const v = String(value == null ? '' : value);
        const out = cloneBlocks(blocks);
        const blk = out[id];
        delete out[id];
        let s = -1;
        let e = -1;
        if (blk && blk.end > blk.start) {
            s = blk.start;
            e = blk.end;
        } else if (!blk && fallback && String(fallback).trim()) {
            const needle = String(fallback).trim();
            s = v.lastIndexOf(needle);
            e = s + needle.length;
        }
        if (s < 0) return { value: v, blocks: out, removed: false };
        if (s > 0 && v[s - 1] === ' ' && (e >= v.length || /\s/.test(v[e]))) s -= 1;
        else if (s === 0 && v[e] === ' ') e += 1;
        for (const other of Object.keys(out)) {
            const o = out[other];
            // A block the removed span overlaps (fallback match) is no longer trustworthy.
            if (o.start < e && o.end > s) delete out[other];
        }
        shiftAfter(out, id, e, s - e);
        return { value: v.slice(0, s) + v.slice(e), blocks: out, removed: true };
    }

    /**
     * Bind the blocks to a composer textarea. Edits the user makes move the
     * blocks; programmatic changes by the page (send clears the box, a draft
     * is restored) are picked up the next time the draft is used.
     */
    function createDraft(textarea) {
        let blocks = {};
        let last = textarea.value;
        let writing = false;

        function sync() {
            if (textarea.value === last) return;
            blocks = applyEdit(blocks, last, textarea.value);
            last = textarea.value;
        }

        function commit(next) {
            const before = textarea.value;
            if (next.value === before) {
                blocks = next.blocks;
                return;
            }
            // Keep the user's caret where it was relative to their own text.
            const caret = applyEdit({ c: { start: textarea.selectionStart || 0, end: textarea.selectionEnd || 0, tail: 0 } },
                before, next.value).c;
            const atEnd = (textarea.selectionStart || 0) >= before.length;
            textarea.value = next.value;
            blocks = next.blocks;
            last = next.value;
            try {
                if (atEnd) textarea.setSelectionRange(next.value.length, next.value.length);
                else textarea.setSelectionRange(caret.start, caret.end);
            } catch (_) {}
            // Let the page's composer listeners (auto-grow, draft save) see it.
            // 'insertFromPaste' keeps typing-only conversions (emoticons) off.
            writing = true;
            try {
                const Ev = (textarea.ownerDocument.defaultView || root).InputEvent;
                textarea.dispatchEvent(Ev
                    ? new Ev('input', { bubbles: true, inputType: 'insertFromPaste' })
                    : new Event('input', { bubbles: true }));
            } catch (_) {}
            writing = false;
            sync();
        }

        textarea.addEventListener('input', () => { if (!writing) sync(); });

        return {
            write(id, text, final) {
                sync();
                commit(write(textarea.value, blocks, id, text, final));
            },
            remove(id, fallback) {
                sync();
                const next = remove(textarea.value, blocks, id, fallback);
                commit(next);
                return next.removed;
            },
            has(id) {
                sync();
                return !!blocks[id];
            },
            text(id) {
                sync();
                return textOf(textarea.value, blocks, id);
            },
        };
    }

    function speechRecognition(win) {
        return (win && (win.SpeechRecognition || win.webkitSpeechRecognition)) || null;
    }

    /** Which engine will actually run here for the configured one. */
    function usableEngine(win, engine) {
        if (engine === 'server') return 'server';
        if (engine === 'browser' && speechRecognition(win)) return 'browser';
        return 'none';
    }

    /**
     * Live text for one recording.
     * @param {object} opts
     * @param {Window} opts.win
     * @param {'server'|'browser'} opts.engine
     * @param {object} opts.draft      from `createDraft`
     * @param {string} opts.id         the clip's id (its block key)
     * @param {object} opts.source     mic source with `cutPhrase` (server engine)
     * @param {object} opts.recorder   CuttleChatVoiceRecorder (VAD + text queue)
     * @param {typeof fetch} opts.fetch
     * @param {() => (string|number)} [opts.sessionId]
     * @returns {{levels, pause, resume, beforeStop, finish, cancel}}
     *   `finish()` → Promise<{ok, text}>: `ok` false when some words were lost.
     */
    function createSession(opts) {
        const draft = opts.draft;
        const id = opts.id;
        let lost = 0;
        let cancelled = false;
        const heard = (text, final) => { if (!cancelled) draft.write(id, text, final); };

        if (opts.engine === 'browser') {
            const SR = speechRecognition(opts.win);
            let rec = null;
            let want = false;
            let ended = null;
            const open = () => {
                const r = new SR();
                r.continuous = true;
                r.interimResults = true;
                r.lang = (opts.win.navigator && opts.win.navigator.language) || 'en-US';
                r.onresult = (e) => {
                    let interim = '';
                    for (let i = e.resultIndex; i < e.results.length; i++) {
                        const res = e.results[i];
                        const t = (res[0] && res[0].transcript) || '';
                        if (res.isFinal) heard(t, true);
                        else interim += t;
                    }
                    heard(interim, false);
                };
                r.onerror = (e) => {
                    const code = String((e && e.error) || '');
                    if (code === 'no-speech' || code === 'aborted') return;
                    lost += 1;
                    if (code === 'not-allowed' || code === 'service-not-allowed' || code === 'network') want = false;
                };
                r.onend = () => {
                    rec = null;
                    if (want) {
                        try { open(); } catch (_) { want = false; lost += 1; }
                    } else if (ended) {
                        ended();
                        ended = null;
                    }
                };
                r.start();
                rec = r;
            };
            const halt = () => new Promise((resolve) => {
                want = false;
                if (!rec) return resolve();
                ended = resolve;
                try { rec.stop(); } catch (_) { resolve(); }
                opts.win.setTimeout(resolve, 1500);
            });
            try {
                want = true;
                open();
            } catch (_) {
                want = false;
                lost += 1;
            }
            return {
                id: id,
                draft: draft,
                levels() {},
                pause() { halt(); },
                resume() {
                    want = true;
                    if (!rec) { try { open(); } catch (_) { want = false; lost += 1; } }
                },
                beforeStop() {},
                async finish() {
                    await halt();
                    return { ok: lost === 0, text: draft.text(id) };
                },
                cancel() {
                    cancelled = true;
                    halt();
                    draft.remove(id);
                },
            };
        }

        const recorder = opts.recorder;
        const source = opts.source;
        let vad = recorder.createVad();
        const queue = recorder.createTextQueue({
            fetch: opts.fetch,
            sessionId: opts.sessionId,
            purpose: 'clip',
            onPhraseText: (t) => heard(t, true),
            onPending: () => {},
            onError: () => { lost += 1; },
        });
        /** Send the words spoken since the last cut (nothing when it was silence). */
        const flush = () => {
            if (vad.heard) queue.push(source.cutPhrase(true));
            else source.cutPhrase(false);
            vad = recorder.createVad();
        };
        return {
            id: id,
            draft: draft,
            levels(frames) {
                for (let i = 0; i < frames.length; i++) {
                    const ev = recorder.vadStep(vad, frames[i]);
                    if (ev === 'phrase_end') queue.push(source.cutPhrase(true));
                    else if (ev === 'idle_rotate') source.cutPhrase(false);
                }
            },
            /** Call before the source pauses: the phrase in progress ends here. */
            pause: flush,
            resume() {},
            /** Call before the source stops. */
            beforeStop: flush,
            async finish() {
                let timedOut = false;
                await Promise.race([
                    queue.idle(),
                    new Promise((resolve) => opts.win.setTimeout(() => { timedOut = true; resolve(); }, FLUSH_TIMEOUT_MS)),
                ]);
                if (timedOut && queue.pending()) lost += 1;
                return { ok: lost === 0, text: draft.text(id) };
            },
            cancel() {
                cancelled = true;
                draft.remove(id);
            },
        };
    }

    const api = {
        FLUSH_TIMEOUT_MS: FLUSH_TIMEOUT_MS,
        applyEdit: applyEdit,
        write: write,
        remove: remove,
        textOf: textOf,
        usableEngine: usableEngine,
        createDraft: createDraft,
        createSession: createSession,
    };
    root.CuttleChatVoiceClipText = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
