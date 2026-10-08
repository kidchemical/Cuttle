/*
 * Voice-mode server transcription engine (experimental `voice_server_stt`):
 * one continuous mic stream, a small energy VAD that cuts phrases at pauses,
 * and one /api/voice-stt upload per phrase. No browser speech recognizer, so
 * no Android start/stop chime. Phrase text is delivered in spoken order.
 * Audio comes from the Cuttle Android app's native recorder when present
 * (works over LAN HTTP), else from the browser (HTTPS or localhost only).
 */
(function (root) {
    'use strict';

    const VAD_DEFAULTS = {
        frameMs: 50,
        /** Loud this long before it counts as speech (filters taps/clicks). */
        minSpeechMs: 200,
        /** Quiet this long after speech ends the phrase. */
        endSilenceMs: 900,
        /** Cut very long phrases so text keeps arriving. */
        maxPhraseMs: 30000,
        /** Drop silent audio this old so clips stay small. */
        maxIdleMs: 20000,
        minRms: 0.012,
        floorFactor: 3,
    };

    const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];

    function createVad(opts) {
        return {
            cfg: Object.assign({}, VAD_DEFAULTS, opts || {}),
            heard: false, speechMs: 0, silenceMs: 0, windowMs: 0, floor: 0.004,
        };
    }

    function resetWindow(vad) {
        vad.heard = false;
        vad.speechMs = 0;
        vad.silenceMs = 0;
        vad.windowMs = 0;
    }

    /**
     * Feed one frame's RMS level. Returns 'speech_start', 'phrase_end'
     * (keep the clip), 'idle_rotate' (discard silent audio) or null.
     */
    function vadStep(vad, rms) {
        const c = vad.cfg;
        const threshold = Math.max(c.minRms, vad.floor * c.floorFactor);
        const loud = rms > threshold;
        if (!loud) vad.floor = vad.floor * 0.95 + rms * 0.05;
        vad.windowMs += c.frameMs;
        if (loud) {
            vad.speechMs += c.frameMs;
            vad.silenceMs = 0;
            if (!vad.heard && vad.speechMs >= c.minSpeechMs) {
                vad.heard = true;
                return 'speech_start';
            }
        } else if (vad.heard) {
            vad.silenceMs += c.frameMs;
            if (vad.silenceMs >= c.endSilenceMs) {
                resetWindow(vad);
                return 'phrase_end';
            }
        } else {
            vad.speechMs = Math.max(0, vad.speechMs - c.frameMs);
        }
        if (vad.heard && vad.windowMs >= c.maxPhraseMs) {
            resetWindow(vad);
            return 'phrase_end';
        }
        if (!vad.heard && vad.windowMs >= c.maxIdleMs) {
            resetWindow(vad);
            return 'idle_rotate';
        }
        return null;
    }

    function rmsOf(buf) {
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        return Math.sqrt(sum / (buf.length || 1));
    }

    /**
     * The Cuttle Android app's native recorder (`window.cuttleMobile.mic*`).
     * Chat runs in an iframe without the JavaScript bridge, so also look at
     * the same-origin shell frame.
     */
    function nativeMic() {
        const candidates = [root.cuttleMobile];
        try {
            if (root.parent && root.parent !== root) candidates.push(root.parent.cuttleMobile);
        } catch (_) {}
        return candidates.find((m) => m && typeof m.micStart === 'function'
            && typeof m.micLevels === 'function' && typeof m.micCut === 'function'
            && typeof m.micStop === 'function') || null;
    }

    /** Browser features this engine lacks here (empty = usable). */
    function missingSupport() {
        if (nativeMic()) return [];
        const missing = [];
        if (root.isSecureContext === false) missing.push('secure (https) page');
        const md = root.navigator && root.navigator.mediaDevices;
        if (!md || typeof md.getUserMedia !== 'function') missing.push('getUserMedia');
        if (!root.MediaRecorder) missing.push('MediaRecorder');
        if (!(root.AudioContext || root.webkitAudioContext)) missing.push('AudioContext');
        return missing;
    }

    function isSupported() {
        return missingSupport().length === 0;
    }

    /** 'native' (Cuttle app mic) or 'browser' (getUserMedia + MediaRecorder). */
    function sourceKind() {
        return nativeMic() ? 'native' : 'browser';
    }

    function pickMime() {
        const MR = root.MediaRecorder;
        if (!MR || typeof MR.isTypeSupported !== 'function') return '';
        return MIME_CANDIDATES.find((m) => MR.isTypeSupported(m)) || '';
    }

    function base64ToBlob(b64, type) {
        const bin = root.atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new Blob([bytes], { type: type });
    }

    /*
     * Audio sources: start(), levels() → RMS per new frame, cut(keep) and
     * stop(keep) → Promise<{blob, durationMs}|null> for the audio since the
     * previous cut.
     */
    function browserSource() {
        let stream = null;
        let ctx = null;
        let analyser = null;
        let frame = null;
        let rec = null;
        let chunks = [];
        let startedAt = 0;

        function startRecorder() {
            const mime = pickMime();
            const r = mime ? new root.MediaRecorder(stream, { mimeType: mime }) : new root.MediaRecorder(stream);
            const mine = [];
            r.ondataavailable = (e) => { if (e.data && e.data.size) mine.push(e.data); };
            r.start();
            rec = r;
            chunks = mine;
            startedAt = Date.now();
        }

        function finish(r, mine, t0, keep) {
            return new Promise((resolve) => {
                r.onstop = () => {
                    resolve(keep && mine.length
                        ? { blob: new Blob(mine, { type: r.mimeType || 'audio/webm' }), durationMs: Date.now() - t0 }
                        : null);
                };
                try { r.stop(); } catch (_) { resolve(null); }
            });
        }

        return {
            async start() {
                stream = await root.navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
                const Ctx = root.AudioContext || root.webkitAudioContext;
                ctx = new Ctx();
                try { await ctx.resume(); } catch (_) {}
                analyser = ctx.createAnalyser();
                analyser.fftSize = 1024;
                ctx.createMediaStreamSource(stream).connect(analyser);
                frame = new Float32Array(analyser.fftSize);
                startRecorder();
            },
            levels() {
                if (!analyser) return [];
                analyser.getFloatTimeDomainData(frame);
                return [rmsOf(frame)];
            },
            cut(keep) {
                const r = rec, mine = chunks, t0 = startedAt;
                startRecorder();
                return finish(r, mine, t0, keep);
            },
            async stop(keep) {
                const done = rec ? finish(rec, chunks, startedAt, keep) : Promise.resolve(null);
                rec = null;
                const clip = await done;
                try { stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
                try { await ctx.close(); } catch (_) {}
                stream = null;
                ctx = null;
                analyser = null;
                return clip;
            },
        };
    }

    function nativeSource(mic) {
        function take(keep) {
            const b64 = String(mic.micCut(!!keep) || '');
            if (!keep || !b64) return null;
            const blob = base64ToBlob(b64, 'audio/wav');
            // 16 kHz mono 16-bit after a 44-byte header: 32 bytes per ms.
            return { blob: blob, durationMs: Math.max(0, Math.round((blob.size - 44) / 32)) };
        }
        return {
            async start() {
                const result = String(mic.micStart() || '');
                if (result === 'ok') return;
                const err = new Error(result === 'denied'
                    ? 'Microphone permission needed'
                    : 'Microphone failed: ' + result.replace(/^error:/, ''));
                err.name = result === 'denied' ? 'NotAllowedError' : 'NativeMicError';
                throw err;
            },
            levels() {
                const raw = String(mic.micLevels() || '');
                return raw ? raw.split(',').map(Number).filter((n) => isFinite(n)) : [];
            },
            cut(keep) {
                return Promise.resolve(take(keep));
            },
            async stop(keep) {
                const clip = take(keep);
                mic.micStop();
                return clip;
            },
        };
    }

    /**
     * @param {object} host
     * @param {typeof fetch} host.fetch
     * @param {(speaking: boolean) => void} host.onSpeech
     * @param {(text: string) => void} host.onPhraseText  in spoken order
     * @param {(pending: number) => void} host.onPending  clips awaiting text
     * @param {(message: string) => void} host.onError
     */
    function create(host) {
        let source = null;
        let timer = 0;
        let vad = createVad();
        let running = false;
        let pending = 0;
        let commits = Promise.resolve();
        let context = '';
        /** Latest frame RMS for the waveform; decays when quiet. */
        let lastRms = 0;

        async function requestText(clip) {
            const form = new FormData();
            const type = clip.blob.type || '';
            const ext = /wav/.test(type) ? 'wav' : (/mp4/.test(type) ? 'mp4' : (/ogg/.test(type) ? 'ogg' : 'webm'));
            form.append('audio', clip.blob, 'phrase.' + ext);
            form.append('duration_ms', String(clip.durationMs));
            form.append('language', (root.navigator && root.navigator.language) || '');
            if (context) form.append('prompt', context);
            const r = await host.fetch('/api/voice-stt/transcribe', {
                method: 'POST', credentials: 'include', body: form,
            });
            const d = await r.json().catch(() => ({}));
            if (d && d.disabled) throw new Error('Server transcription is turned off');
            if (!r.ok || !d || !d.success) throw new Error((d && d.error) || ('Transcription failed (' + r.status + ')'));
            return String(d.text || '').trim();
        }

        function enqueue(clipPromise) {
            pending += 1;
            host.onPending(pending);
            const text = clipPromise.then((clip) => (clip ? requestText(clip) : ''));
            commits = commits
                .then(() => text)
                .then((t) => {
                    if (!t) return;
                    context = (context + ' ' + t).slice(-400);
                    host.onPhraseText(t);
                })
                .catch((err) => host.onError(String((err && err.message) || err)))
                .finally(() => {
                    pending -= 1;
                    host.onPending(pending);
                });
        }

        function tick() {
            if (!running || !source) return;
            const levels = source.levels();
            for (let i = 0; i < levels.length; i++) {
                const rms = Number(levels[i]) || 0;
                if (rms > lastRms) lastRms = rms;
                else lastRms += (rms - lastRms) * 0.2;
                const ev = vadStep(vad, levels[i]);
                if (ev === 'speech_start') host.onSpeech(true);
                else if (ev === 'phrase_end') {
                    host.onSpeech(false);
                    enqueue(source.cut(true));
                } else if (ev === 'idle_rotate') source.cut(false);
            }
        }

        async function start() {
            if (running) return;
            const mic = nativeMic();
            const next = mic ? nativeSource(mic) : browserSource();
            await next.start();
            source = next;
            vad = createVad();
            running = true;
            timer = setInterval(tick, vad.cfg.frameMs);
        }

        /**
         * Stop listening. `flush` keeps a phrase in progress; resolves once
         * every clip's text has been delivered.
         */
        async function stop(opts) {
            const flush = !!(opts && opts.flush);
            if (running) {
                running = false;
                clearInterval(timer);
                timer = 0;
                const keep = flush && vad.heard;
                host.onSpeech(false);
                const src = source;
                source = null;
                const clip = src.stop(keep);
                if (keep) enqueue(clip);
                else await clip;
            }
            await commits;
        }

        function resetContext() {
            context = '';
        }

        return {
            start: start,
            stop: stop,
            resetContext: resetContext,
            isRunning: () => running,
            pending: () => pending,
            /** RMS of the latest mic frame (0 when idle) for the waveform. */
            level: () => (running ? lastRms : 0),
        };
    }

    const api = {
        VAD_DEFAULTS: VAD_DEFAULTS,
        createVad: createVad,
        vadStep: vadStep,
        rmsOf: rmsOf,
        isSupported: isSupported,
        missingSupport: missingSupport,
        sourceKind: sourceKind,
        nativeMic: nativeMic,
        create: create,
    };
    root.CuttleChatVoiceRecorder = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
