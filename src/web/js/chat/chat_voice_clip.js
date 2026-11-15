/* ================================================================
   Cuttle Chat — composer voice clips (chat_voice_clip.js).
   Owner: the composer mic button's recording lifetime — one mic
   source at a time, the recording bar (live waveform, timer, pause /
   discard / stop), turning a finished recording into a file for the
   page's upload path, and the voice-clip look of a staged chip
   (play button + duration). Experimental flag `composer_voice_clip`.

   Not owned here: mic capture (`CuttleChatVoiceRecorder.openSource`),
   the words typed into the composer while recording
   (`CuttleChatVoiceClipText`), the pending-attachment list, upload
   transport and send — the page passes `upload`, asks
   `finishForSend()` before it sends and reports a removed chip to
   `attachmentRemoved()`. Voice mode (`chat_voice.js`) is a separate
   surface and is not involved.

   Pure helpers are exported for node tests; `create(host)` is the
   only part that touches DOM, timers or audio.
   ================================================================ */
(function (root) {
    'use strict';

    const TICK_MS = 50;
    /** Bars kept for the scrolling waveform (more than any composer is wide). */
    const MAX_LEVELS = 600;
    /** Stop by itself here unless Settings → Voice says otherwise. */
    const MAX_CLIP_MS = 10 * 60 * 1000;
    /** The app's native mic records WAV: longer than this exceeds the upload limit. */
    const NATIVE_MAX_CLIP_MS = 12 * 60 * 1000;
    /** Shorter than this is a mis-tap, not a clip. */
    const MIN_CLIP_MS = 400;

    const TRANSITIONS = {
        idle: { start: 'starting' },
        starting: { started: 'recording', error: 'idle' },
        recording: { pause: 'paused', stop: 'saving', discard: 'idle' },
        paused: { resume: 'recording', stop: 'saving', discard: 'idle' },
        saving: { saved: 'idle', empty: 'idle', error: 'failed' },
        failed: { retry: 'saving', discard: 'idle' },
    };

    /** Next phase for `event`, or null when the event does not apply. */
    function nextPhase(phase, event) {
        return (TRANSITIONS[phase] || {})[event] || null;
    }

    /** 75000 → "1:15". */
    function formatDuration(ms) {
        const total = Math.max(0, Math.round((Number(ms) || 0) / 1000));
        return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }

    /** The longest clip to record: the setting, capped for the native mic's WAV. */
    function clipLimitMs(configured, native) {
        const ms = Number(configured);
        const limit = Number.isFinite(ms) && ms >= 1000 ? ms : MAX_CLIP_MS;
        return native ? Math.min(limit, NATIVE_MAX_CLIP_MS) : limit;
    }

    function _pad2(n) {
        return String(n).padStart(2, '0');
    }

    /** Upload name; the extension follows the recorded container. */
    function clipFilename(mime, now) {
        const d = now instanceof Date ? now : new Date();
        const type = String(mime || '').toLowerCase();
        const ext = /wav/.test(type) ? '.wav' : (/mp4|m4a|aac/.test(type) ? '.m4a' : (/ogg/.test(type) ? '.ogg' : '.webm'));
        return 'voice-' + d.getFullYear() + _pad2(d.getMonth() + 1) + _pad2(d.getDate()) + '-'
            + _pad2(d.getHours()) + _pad2(d.getMinutes()) + _pad2(d.getSeconds()) + ext;
    }

    /** Mic RMS → bar height as a fraction of the canvas (never invisible). */
    function levelToBar(rms) {
        const v = Math.pow(Math.max(0, Number(rms) || 0) * 6, 0.6);
        return Math.max(0.08, Math.min(1, v));
    }

    /** Append a level, keeping the newest `max`. */
    function pushLevel(levels, rms, max) {
        levels.push(Number(rms) || 0);
        const over = levels.length - (max || MAX_LEVELS);
        if (over > 0) levels.splice(0, over);
        return levels;
    }

    /** "Voice clip · 0:12" (duration only when known). */
    function chipLabel(att) {
        const ms = att && att.duration_ms;
        return Number.isFinite(ms) && ms > 0 ? 'Voice clip · ' + formatDuration(ms) : 'Voice clip';
    }

    const ICONS = {
        mic: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v4"/></svg>',
        pause: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="4" width="5" height="16" rx="1.5"/><rect x="14" y="4" width="5" height="16" rx="1.5"/></svg>',
        play: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>',
        stop: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>',
        retry: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>',
        discard: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/></svg>',
    };

    /* ---- Staged-chip playback: one clip plays at a time. ---- */
    let playing = null; // { url, audio, btn }

    function paintPlayButton(btn, on) {
        btn.innerHTML = on ? ICONS.pause : ICONS.play;
        btn.setAttribute('aria-label', on ? 'Pause voice clip' : 'Play voice clip');
        btn.title = on ? 'Pause' : 'Play';
    }

    function stopPlayback() {
        if (!playing) return;
        try { playing.audio.pause(); } catch (_) {}
        if (playing.btn) paintPlayButton(playing.btn, false);
        playing = null;
    }

    function togglePlayback(url, btn) {
        const same = playing && playing.url === url;
        stopPlayback();
        if (same) return;
        const Audio = root.Audio;
        if (!Audio) return;
        const audio = new Audio(url);
        const mine = { url: url, audio: audio, btn: btn };
        const end = () => { if (playing === mine) stopPlayback(); };
        audio.addEventListener('ended', end);
        audio.addEventListener('error', end);
        // A removed chip (✕, or sent) takes its playback with it.
        audio.addEventListener('timeupdate', () => { if (mine.btn && !mine.btn.isConnected) end(); });
        playing = mine;
        paintPlayButton(btn, true);
        Promise.resolve(audio.play()).catch(end);
    }

    /**
     * Give a staged attachment chip the voice-clip look: a play button
     * before the name and "Voice clip · 0:12" instead of the file name.
     * The page built `chip` and `nameEl`; removal stays the page's.
     */
    function decorateChip(chip, nameEl, att) {
        const doc = chip.ownerDocument;
        const url = String((att && att.url) || '');
        chip.classList.add('attach-chip--voice');
        if (nameEl) nameEl.textContent = chipLabel(att);
        if (!url) return;
        const btn = doc.createElement('button');
        btn.type = 'button';
        btn.className = 'attach-chip-play';
        const on = !!(playing && playing.url === url);
        // Chips re-render on every change: a playing clip keeps its button.
        if (on) playing.btn = btn;
        paintPlayButton(btn, on);
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            togglePlayback(url, btn);
        });
        chip.insertBefore(btn, chip.firstChild);
    }

    /**
     * @param {object} host
     * @param {Window} host.win
     * @param {object} host.recorder  CuttleChatVoiceRecorder (`openSource`, `missingSupport`)
     * @param {(file: File, extra: {duration_ms: number}) => Promise<boolean>} host.upload
     *        stage the file as a pending attachment; false when it failed
     * @param {(message: string, kind?: string) => void} host.toast
     * @param {Array<{button: Element, row: Element, input?: HTMLTextAreaElement}>} host.composers
     *        each composer's mic button, the chips row its bar sits above and
     *        its text box (where live text is typed)
     * @param {object} [host.text]  CuttleChatVoiceClipText; without it clips carry no live text
     * @param {() => Promise<{engine?: string, max_clip_ms?: number}>} [host.config]
     *        speech-to-text settings, read when a recording starts
     * @param {typeof fetch} [host.fetch]
     * @param {() => (string|number)} [host.sessionId]
     */
    function create(host) {
        const win = host.win;
        const doc = win.document;
        const composers = (host.composers || []).filter((c) => c && c.button && c.row);
        for (const c of composers) c.draft = host.text && c.input ? host.text.createDraft(c.input) : null;

        let phase = 'idle';
        let source = null;
        let bar = null; // DOM parts of the bar in the composer being recorded in
        let timer = 0;
        let levels = [];
        let activeMs = 0;
        let lastTick = 0;
        let failed = null; // { file, extra } awaiting retry
        let live = null; // live-text session of the recording in progress
        let limitMs = MAX_CLIP_MS;
        let clipSeq = 0;
        let saving = null; // Promise<'attached'|'empty'|'failed'>
        let sendClaimed = false;

        function go(event) {
            const next = nextPhase(phase, event);
            if (!next) return false;
            phase = next;
            paint();
            return true;
        }

        function buildBar(composer) {
            const el = doc.createElement('div');
            el.className = 'voice-clip-bar';
            el.setAttribute('role', 'group');
            el.setAttribute('aria-label', 'Voice recording');
            el.innerHTML = '<span class="voice-clip-dot" aria-hidden="true"></span>'
                + '<span class="voice-clip-time">0:00</span>'
                + '<canvas class="voice-clip-wave" aria-hidden="true"></canvas>'
                + '<button type="button" class="voice-clip-btn voice-clip-pause"></button>'
                + '<button type="button" class="voice-clip-btn voice-clip-discard" title="Discard recording" aria-label="Discard recording">' + ICONS.discard + '</button>'
                + '<button type="button" class="voice-clip-btn voice-clip-stop"></button>';
            const parts = {
                el: el,
                time: el.querySelector('.voice-clip-time'),
                canvas: el.querySelector('.voice-clip-wave'),
                pause: el.querySelector('.voice-clip-pause'),
                discard: el.querySelector('.voice-clip-discard'),
                stop: el.querySelector('.voice-clip-stop'),
            };
            parts.pause.addEventListener('click', () => (phase === 'paused' ? resume() : pause()));
            parts.discard.addEventListener('click', discard);
            parts.stop.addEventListener('click', () => { finish(); });
            composer.row.parentNode.insertBefore(el, composer.row);
            return parts;
        }

        function paint() {
            const live = phase !== 'idle';
            for (const c of composers) {
                c.button.classList.toggle('is-recording', live);
                c.button.setAttribute('aria-pressed', live ? 'true' : 'false');
                const label = live ? 'Stop and attach voice clip' : 'Record a voice clip';
                c.button.title = label;
                c.button.setAttribute('aria-label', label);
            }
            if (!bar) return;
            if (!live) {
                bar.el.remove();
                bar = null;
                return;
            }
            bar.el.dataset.state = phase;
            const paused = phase === 'paused';
            bar.pause.innerHTML = paused ? ICONS.mic : ICONS.pause;
            bar.pause.title = paused ? 'Resume recording' : 'Pause recording';
            bar.pause.setAttribute('aria-label', bar.pause.title);
            bar.pause.hidden = phase === 'failed';
            bar.pause.disabled = !(phase === 'recording' || paused);
            bar.stop.innerHTML = phase === 'failed' ? ICONS.retry : ICONS.stop;
            bar.stop.title = phase === 'failed' ? 'Retry upload' : 'Stop and attach';
            bar.stop.setAttribute('aria-label', bar.stop.title);
            bar.stop.disabled = phase === 'starting' || phase === 'saving';
            bar.discard.disabled = phase === 'starting' || phase === 'saving';
            bar.time.textContent = phase === 'failed' ? 'Upload failed'
                : (phase === 'saving' ? 'Attaching…' : formatDuration(activeMs));
            drawWave();
        }

        function drawWave() {
            if (!bar) return;
            const canvas = bar.canvas;
            const ratio = win.devicePixelRatio || 1;
            const w = canvas.clientWidth;
            const h = canvas.clientHeight;
            if (!w || !h) return;
            if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
                canvas.width = Math.round(w * ratio);
                canvas.height = Math.round(h * ratio);
            }
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            ctx.clearRect(0, 0, w, h);
            // Color comes from CSS (`color` on the canvas) so themes and the
            // paused look need no script.
            ctx.fillStyle = win.getComputedStyle(canvas).color || '#667eea';
            const barW = 2;
            const step = 4;
            const count = Math.floor(w / step);
            const from = Math.max(0, levels.length - count);
            let x = w - (levels.length - from) * step;
            for (let i = from; i < levels.length; i++, x += step) {
                const bh = Math.max(2, levelToBar(levels[i]) * h);
                if (ctx.roundRect) {
                    ctx.beginPath();
                    ctx.roundRect(x, (h - bh) / 2, barW, bh, 1);
                    ctx.fill();
                } else {
                    ctx.fillRect(x, (h - bh) / 2, barW, bh);
                }
            }
        }

        /** Count the time and levels since the last tick. */
        function advance() {
            const now = Date.now();
            if (phase === 'recording' && source) {
                activeMs += now - lastTick;
                const frames = source.levels();
                for (let i = 0; i < frames.length; i++) pushLevel(levels, frames[i], MAX_LEVELS);
                if (live) live.levels(frames);
                if (bar) bar.time.textContent = formatDuration(activeMs);
                drawWave();
            }
            lastTick = now;
        }

        function tick() {
            advance();
            if (phase === 'recording' && activeMs >= limitMs) {
                host.toast('Voice clip reached ' + formatDuration(limitMs) + ' and was attached.', 'info');
                finish();
            }
        }

        function stopTimer() {
            if (timer) win.clearInterval(timer);
            timer = 0;
        }

        async function start(composer) {
            if (phase !== 'idle') return;
            const missing = host.recorder.missingSupport();
            if (missing.length) {
                host.toast('Voice clips are not available here (needs ' + missing.join(', ') + ').', 'error');
                return;
            }
            stopPlayback();
            levels = [];
            activeMs = 0;
            failed = null;
            bar = buildBar(composer);
            go('start');
            const next = host.recorder.openSource();
            let cfg = null;
            try {
                const settings = host.config ? Promise.resolve(host.config()).catch(() => null) : null;
                cfg = (await settings) || {};
                const engine = composer.draft ? host.text.usableEngine(win, cfg.engine) : 'none';
                await next.start({ phrases: engine === 'server' });
                cfg.engine = engine;
            } catch (err) {
                go('error');
                host.toast(err && err.name === 'NotAllowedError'
                    ? 'Microphone permission is needed to record a voice clip.'
                    : 'Could not start the microphone: ' + String((err && err.message) || err), 'error');
                return;
            }
            source = next;
            limitMs = clipLimitMs(cfg.max_clip_ms, host.recorder.sourceKind && host.recorder.sourceKind() === 'native');
            live = cfg.engine === 'none' ? null : host.text.createSession({
                win: win, engine: cfg.engine, draft: composer.draft,
                id: 'vc' + Date.now().toString(36) + (++clipSeq),
                source: next, recorder: host.recorder, fetch: host.fetch, sessionId: host.sessionId,
            });
            lastTick = Date.now();
            timer = win.setInterval(tick, TICK_MS);
            go('started');
        }

        function pause() {
            if (phase !== 'recording' || !source) return;
            advance();
            if (live) live.pause();
            source.pause();
            go('pause');
        }

        function resume() {
            if (phase !== 'paused' || !source) return;
            source.resume();
            if (live) live.resume();
            lastTick = Date.now();
            go('resume');
        }

        /** Drop the recording (or a clip whose upload failed). */
        function discard() {
            if (!nextPhase(phase, 'discard')) return;
            stopTimer();
            const src = source;
            source = null;
            if (live) live.cancel();
            live = null;
            if (failed && failed.draft) failed.draft.remove(failed.extra.clip_id);
            failed = null;
            go('discard');
            if (src) Promise.resolve(src.stop(false)).catch(() => {});
        }

        async function uploadClip(clip) {
            let ok = false;
            try {
                ok = await host.upload(clip.file, clip.extra);
            } catch (_) {
                ok = false;
            }
            if (ok) {
                failed = null;
                go('saved');
                return 'attached';
            }
            failed = clip;
            go('error');
            return 'failed';
        }

        /**
         * Stop recording and stage the clip as an attachment (or retry a
         * failed upload). Resolves 'attached', 'empty' (nothing recorded)
         * or 'failed' (the clip is kept in the bar for Retry / Discard).
         */
        function finish() {
            if (phase === 'saving') return saving;
            if (phase === 'failed' && failed) {
                const clip = failed;
                go('retry');
                saving = uploadClip(clip);
                return saving;
            }
            if (phase !== 'recording' && phase !== 'paused') return Promise.resolve('empty');
            if (phase === 'recording') advance();
            stopTimer();
            const src = source;
            source = null;
            const text = live;
            live = null;
            const durationMs = activeMs;
            if (text) text.beforeStop();
            go('stop');
            saving = (async () => {
                let clip = null;
                try { clip = await src.stop(true); } catch (_) { clip = null; }
                if (!clip || !clip.blob || !clip.blob.size || durationMs < MIN_CLIP_MS) {
                    if (text) text.cancel();
                    go('empty');
                    return 'empty';
                }
                const extra = { duration_ms: durationMs };
                if (text) {
                    // The clip's words are in the composer: the user sends
                    // (and can fix) that text, so the server skips the clip.
                    const heard = await text.finish();
                    extra.clip_id = text.id;
                    extra.transcript = heard.text;
                    if (heard.ok) extra.transcribed = true;
                    else host.toast('Some of this clip was not turned into text. It will be transcribed when you send.', 'info');
                }
                const type = clip.blob.type || 'audio/webm';
                const file = new win.File([clip.blob], clipFilename(type, new Date()), { type: type });
                return uploadClip({ file: file, extra: extra, draft: text ? text.draft : null });
            })();
            return saving;
        }

        /**
         * The page's send calls this first: a recording in progress is
         * stopped and attached so Send sends it. False = do not send
         * (upload failed, mic still opening, or another send already
         * waits on the same clip).
         */
        async function finishForSend() {
            if (phase === 'idle') return true;
            if (phase === 'starting' || sendClaimed) return false;
            sendClaimed = true;
            try {
                return (await finish()) !== 'failed';
            } finally {
                sendClaimed = false;
            }
        }

        composers.forEach((composer) => {
            composer.button.innerHTML = ICONS.mic;
            composer.button.addEventListener('click', (e) => {
                e.preventDefault();
                if (phase === 'idle') start(composer);
                else finish();
            });
        });
        paint();

        /** The page removed a staged clip (chip ✕): its words leave the composer too. */
        function attachmentRemoved(att) {
            if (!att || (!att.clip_id && !att.transcript)) return;
            const drafts = composers.map((c) => c.draft).filter(Boolean);
            const owner = drafts.find((d) => att.clip_id && d.has(att.clip_id));
            if (owner) owner.remove(att.clip_id);
            else drafts.some((d) => d.remove(att.clip_id || '', att.transcript));
        }

        return {
            attachmentRemoved: attachmentRemoved,
            isActive: () => phase !== 'idle',
            phase: () => phase,
            pause: pause,
            resume: resume,
            discard: discard,
            finish: finish,
            finishForSend: finishForSend,
        };
    }

    const api = {
        MAX_CLIP_MS: MAX_CLIP_MS,
        MIN_CLIP_MS: MIN_CLIP_MS,
        NATIVE_MAX_CLIP_MS: NATIVE_MAX_CLIP_MS,
        clipLimitMs: clipLimitMs,
        nextPhase: nextPhase,
        formatDuration: formatDuration,
        clipFilename: clipFilename,
        levelToBar: levelToBar,
        pushLevel: pushLevel,
        chipLabel: chipLabel,
        decorateChip: decorateChip,
        create: create,
    };
    root.CuttleChatVoiceClip = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
