/* ================================================================
   Cuttle Chat — SSE byte transport (chat_stream.js).
   Owner: the native response reader, one retained read promise across
   timeout observations, read timers, TextDecoder plus LF-double-newline
   framing and JSON decode, reader cancellation and lock cleanup.
   The page owns the fetch request, HTTP status/content-type/JSON
   fallback, session adoption, event classification (always
   CuttleChatPendingResult), DOM paint, sawProgress, final-result
   mapping, turn/Stop/busy ownership, and pending recovery.
   Interface: readEvents(body, options) with the page's holdMs and
   readTimeoutMs plus one synchronous onEvents(batch) callback. The
   callback applies the whole decoded batch, then returns stop=true to
   end the turn — so the last final frame in a batch wins, exactly like
   the inline loop this replaces. Terminal/eof/detach outcomes carry
   elapsed/reason for the page's existing debug messages. No fetch, no
   DOM, no session, no paint, no second cache. Loaded before
   chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    function readEvents(body, options) {
        const opts = options || {};
        const holdMs = opts.holdMs;
        const readTimeoutMs = opts.readTimeoutMs;
        const onEvents = opts.onEvents;
        const signal = opts.signal || null;
        if (!body || typeof body.getReader !== 'function') {
            return Promise.reject(new TypeError(
                'CuttleChatStream.readEvents requires a readable stream body'));
        }
        if (typeof onEvents !== 'function') {
            return Promise.reject(new TypeError(
                'CuttleChatStream.readEvents requires an onEvents callback'));
        }
        if (signal && signal.aborted) {
            return Promise.reject(
                new DOMException('stream aborted', 'AbortError'));
        }
        const reader = body.getReader();
        const decoder = new TextDecoder();
        const startedAt = Date.now();
        let buffer = '';
        // At most one pending read: a timeout tick only rejects its own
        // race. The handle is retained until readChunk actually returns
        // the result, so a read settling between timeout and catch still
        // delivers its bytes instead of being discarded.
        let pendingRead = null;
        let onAbort = null;
        let aborted = false;
        const abortError = () =>
            new DOMException('stream aborted', 'AbortError');
        const elapsedMs = () => Date.now() - startedAt;
        function release() {
            // Native releaseLock no-ops once unlocked; the guard covers
            // only non-conforming reader shims, never read outcomes.
            try { reader.releaseLock(); } catch (_) {}
        }
        function unlisten() {
            try {
                if (signal && onAbort) {
                    signal.removeEventListener('abort', onAbort);
                }
            } catch (_) {}
            onAbort = null;
        }
        async function readChunk() {
            if (!pendingRead) {
                pendingRead = reader.read();
            }
            let timer = null;
            let result;
            try {
                result = await Promise.race([
                    pendingRead,
                    new Promise((_, reject) => {
                        timer = setTimeout(
                            () => reject(Object.assign(
                                new Error('stream-read-timeout'),
                                { name: 'StreamReadTimeout' })),
                            readTimeoutMs
                        );
                    }),
                ]);
            } catch (err) {
                if (err && err.name === 'StreamReadTimeout') {
                    throw err;
                }
                pendingRead = null;
                throw err;
            } finally {
                if (timer) clearTimeout(timer);
            }
            pendingRead = null;
            return result;
        }
        if (signal) {
            onAbort = () => {
                // cancel() alone would fulfill the retained read as
                // done (EOF shape), racing the platform abort error —
                // every settlement after this flag normalizes to
                // AbortError below. No cancel route, no stop decisions.
                aborted = true;
                try {
                    const cancelled = reader.cancel(abortError());
                    if (cancelled && typeof cancelled.catch === 'function') {
                        // cancel() on an already-errored stream rejects:
                        // observed here so it never surfaces unhandled.
                        cancelled.catch(() => {});
                    }
                } catch (_) {}
            };
            signal.addEventListener('abort', onAbort, { once: true });
        }
        async function run() {
            try {
                while (true) {
                    if ((Date.now() - startedAt) >= holdMs) {
                        try { await reader.cancel(); } catch (_) {}
                        return {
                            status: 'detached',
                            elapsedMs: elapsedMs(),
                            reason: 'hold',
                        };
                    }
                    let readResult;
                    try {
                        readResult = await readChunk();
                    } catch (readErr) {
                        if (aborted) {
                            throw abortError();
                        }
                        if (readErr && readErr.name === 'StreamReadTimeout') {
                            if ((Date.now() - startedAt) >= holdMs) {
                                try { await reader.cancel(); } catch (_) {}
                                return {
                                    status: 'detached',
                                    elapsedMs: elapsedMs(),
                                    reason: 'read-timeout',
                                };
                            }
                            continue;
                        }
                        throw readErr;
                    }
                    if (aborted) {
                        throw abortError();
                    }
                    const done = readResult.done;
                    const value = readResult.value;
                    if (done) {
                        return {
                            status: 'eof',
                            elapsedMs: elapsedMs(),
                            reason: 'eof',
                        };
                    }
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop() || '';
                    const events = [];
                    for (const chunk of lines) {
                        if (chunk.startsWith('data: ')) {
                            try {
                                events.push(JSON.parse(chunk.slice(6)));
                            } catch (_) { /* skip malformed frames */ }
                        }
                    }
                    if (events.length && onEvents(events)) {
                        return {
                            status: 'terminal',
                            elapsedMs: elapsedMs(),
                            reason: 'final',
                        };
                    }
                }
            } finally {
                // One exit path for every outcome and error: the abort
                // listener comes off and the lock is released exactly
                // once the turn stops owning the reader.
                unlisten();
                release();
            }
        }
        return run();
    }

    const api = {
        readEvents,
    };

    const ns = (root.CuttleChatStream = root.CuttleChatStream || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
