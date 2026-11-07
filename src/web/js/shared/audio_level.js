/*
 * Audio level metering for voice UI (call strip, voice orb, speaker buttons).
 * Cuttle-free: callers pass the window and the media to meter.
 *
 * - rms(frame): RMS of a Float32 time-domain frame.
 * - peakHold(prev, rms): fast attack, slow release so meters breathe.
 * - display(rms): RMS (~0..0.5) to a 0..1 display level with a soft knee.
 * - bars(level, count, t): per-bar 0..1 heights for a small bar meter.
 * - parseNative(text): '{"out":x,"in":y}' from the Android bridge -> {out, in} | null.
 * - createStreamMeter(win, stream): analyse a MediaStream (WebRTC remote/mic)
 *   without routing it to the speakers; {level(), close()}.
 * - createElementMeter(win, audio): route an <audio> element through an
 *   analyser to the speakers; {level(), close()}. One shared AudioContext per
 *   window (browsers cap the number of contexts).
 */
(function (root) {
    'use strict';

    function rms(frame) {
        if (!frame || !frame.length) return 0;
        let sum = 0;
        for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
        return Math.sqrt(sum / frame.length);
    }

    function peakHold(prev, value) {
        const p = Number(prev) || 0;
        const v = Math.max(0, Number(value) || 0);
        return v > p ? v : p + (v - p) * 0.2;
    }

    function display(value) {
        const v = Math.max(0, Number(value) || 0);
        const scaled = Math.min(1, v / 0.22); // clamp first: the knee folds back above 1
        return Math.max(0, Math.min(1, scaled * (2 - scaled) * 0.5 + scaled * 0.5));
    }

    /** Symmetric bar heights: centre bars tallest, a little motion per bar. */
    function bars(level, count, t) {
        const n = Math.max(1, Math.floor(Number(count) || 5));
        const lv = Math.max(0, Math.min(1, Number(level) || 0));
        const mid = (n - 1) / 2;
        const out = [];
        for (let i = 0; i < n; i++) {
            const shape = 1 - (mid ? Math.abs(i - mid) / mid : 0) * 0.45;
            const wobble = 0.85 + 0.15 * Math.sin((Number(t) || 0) * 9 + i * 1.7);
            out.push(Math.max(0.12, Math.min(1, lv * shape * wobble)));
        }
        return out;
    }

    function parseNative(text) {
        if (!text) return null;
        try {
            const d = JSON.parse(String(text));
            const out = Number(d && d.out), inn = Number(d && d.in);
            if (!Number.isFinite(out) && !Number.isFinite(inn)) return null;
            return { out: Number.isFinite(out) ? out : 0, in: Number.isFinite(inn) ? inn : 0 };
        } catch (_) {
            return null;
        }
    }

    const contexts = new WeakMap();
    function sharedContext(win) {
        const w = win || root;
        let ctx = contexts.get(w);
        if (ctx && ctx.state !== 'closed') return ctx;
        const Ctx = w.AudioContext || w.webkitAudioContext;
        if (!Ctx) return null;
        ctx = new Ctx();
        contexts.set(w, ctx);
        return ctx;
    }

    function analyserMeter(ctx, connect, disconnect) {
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        connect(analyser);
        const frame = new Float32Array(analyser.fftSize);
        let held = 0, closed = false;
        return {
            level() {
                if (closed) return 0;
                try {
                    analyser.getFloatTimeDomainData(frame);
                    held = peakHold(held, rms(frame));
                    return held;
                } catch (_) { return 0; }
            },
            close() {
                if (closed) return;
                closed = true;
                try { disconnect(analyser); } catch (_) {}
            },
        };
    }

    /** Analysis only: the stream keeps playing through its own <audio> element. */
    function createStreamMeter(win, stream) {
        if (!stream) return null;
        try {
            const ctx = sharedContext(win);
            if (!ctx) return null;
            if (ctx.state === 'suspended') ctx.resume().catch(() => {});
            const src = ctx.createMediaStreamSource(stream);
            return analyserMeter(ctx, (a) => src.connect(a), (a) => { src.disconnect(); a.disconnect(); });
        } catch (_) {
            return null;
        }
    }

    /** Create/resume the shared context; call inside a user gesture (tap). */
    function prime(win) {
        try {
            const ctx = sharedContext(win);
            if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
            return ctx;
        } catch (_) {
            return null;
        }
    }

    /**
     * Element playback through the analyser. Once routed, the element only
     * sounds through the context, so callers should create it inside the
     * user gesture that starts playback (resume needs one on mobile).
     * opts.requireRunning: skip metering (plain playback) unless the context
     * already runs, so a suspended context can never mute the audio.
     */
    function createElementMeter(win, audio, opts) {
        if (!audio) return null;
        try {
            const ctx = sharedContext(win);
            if (!ctx) return null;
            if (opts && opts.requireRunning && ctx.state !== 'running') return null;
            if (ctx.state === 'suspended') ctx.resume().catch(() => {});
            const src = ctx.createMediaElementSource(audio);
            return analyserMeter(ctx, (a) => { src.connect(a); a.connect(ctx.destination); },
                (a) => { src.disconnect(); a.disconnect(); });
        } catch (_) {
            return null;
        }
    }

    const api = { rms, peakHold, display, bars, parseNative, prime, createStreamMeter, createElementMeter };
    root.CuttleAudioLevel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
