/*
 * Voice-mode waveform (chat_voice.js owner of lifecycle; this module owns
 * rendering math + canvases). Two layers:
 * - background: subtle full-overlay bars, always faint;
 * - ring: an apparent oscilloscope ring around the orbital graphic.
 * Levels arrive as RMS (0..~0.5) from a provider fn; null falls back to a
 * quiet ambient shimmer so the visual never freezes. Pure helpers
 * (smoothLevel, mapLevel, ringRadii, barHeights) carry no DOM for tests.
 */
(function (root) {
    'use strict';

    /** Attack/release smoothing so bars breathe instead of jumping. */
    function smoothLevel(prev, target, up, down) {
        const p = Number(prev) || 0;
        const t = Math.max(0, Number(target) || 0);
        const a = t > p ? (up == null ? 0.5 : up) : (down == null ? 0.08 : down);
        return p + (t - p) * Math.max(0, Math.min(1, a));
    }

    /** RMS (~0..0.5) to a 0..1 display level with a soft knee. */
    function mapLevel(rms) {
        const v = Math.max(0, Number(rms) || 0);
        const scaled = v / 0.22;
        return Math.max(0, Math.min(1, scaled * (2 - scaled) * 0.5 + scaled * 0.5));
    }

    /** One radius per sample around the ring: level swells, time ripples. */
    function ringRadii(count, level, t) {
        const n = Math.max(8, Math.floor(Number(count) || 48));
        const lv = Math.max(0, Math.min(1, Number(level) || 0));
        const out = [];
        for (let i = 0; i < n; i++) {
            const ripple = Math.sin((i / n) * Math.PI * 4 + t * 3.1) * 0.5
                + Math.sin((i / n) * Math.PI * 7 - t * 1.7) * 0.5;
            const ambient = 0.04 + 0.02 * ripple;
            out.push(ambient + lv * (0.22 + 0.10 * ripple));
        }
        return out;
    }

    /** Vertical bar half-heights (fractions of canvas height) for the bed. */
    function barHeights(count, level, t) {
        const n = Math.max(8, Math.floor(Number(count) || 64));
        const lv = Math.max(0, Math.min(1, Number(level) || 0));
        const out = [];
        for (let i = 0; i < n; i++) {
            const wave = Math.sin(i * 0.42 + t * 1.9) * 0.5 + Math.sin(i * 0.13 - t * 0.7) * 0.5;
            out.push(0.015 + 0.012 * Math.abs(wave) + lv * (0.10 + 0.09 * Math.abs(wave)));
        }
        return out;
    }

    function accentColor(fallback) {
        try {
            const v = String(getComputedStyle(document.body).getPropertyValue('--accent-color') || '').trim();
            if (v) return v;
        } catch (_) {}
        return fallback || '#7aa2f7';
    }

    function fitCanvas(canvas) {
        const dpr = Math.min(2, root.devicePixelRatio || 1);
        const rect = canvas.getBoundingClientRect();
        const w = Math.max(1, Math.round(rect.width || canvas.clientWidth || 300));
        const h = Math.max(1, Math.round(rect.height || canvas.clientHeight || 300));
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        return { w: w, h: h, dpr: dpr };
    }

    function reducedMotion() {
        try {
            return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) { return false; }
    }

    /**
     * @param {object} opts
     * @param {HTMLCanvasElement} opts.bg    subtle full-overlay bed
     * @param {HTMLCanvasElement} opts.ring  apparent ring around the orbit
     * @param {() => (number|null)} opts.level  RMS provider, null = ambient
     * @param {() => string} opts.phase  idle|listening|processing|speaking
     */
    function create(opts) {
        const o = opts || {};
        let raf = 0;
        let running = false;
        let display = 0;
        let color = '#7aa2f7';

        function paint(nowMs) {
            const t = (nowMs || 0) / 1000;
            const phase = typeof o.phase === 'function' ? o.phase() : 'idle';
            let raw = null;
            try { raw = typeof o.level === 'function' ? o.level() : null; } catch (_) { raw = null; }
            const target = raw == null ? (phase === 'idle' ? 0.03 : 0.10) : mapLevel(raw);
            display = smoothLevel(display, target, 0.45, 0.07);

            if (o.bg && o.bg.isConnected !== false) {
                try {
                    const m = fitCanvas(o.bg);
                    const ctx = o.bg.getContext('2d');
                    if (ctx) {
                        ctx.setTransform(m.dpr, 0, 0, m.dpr, 0, 0);
                        ctx.clearRect(0, 0, m.w, m.h);
                        const hs = barHeights(64, display, t);
                        const bw = m.w / hs.length;
                        ctx.fillStyle = color;
                        ctx.globalAlpha = 0.10;
                        const mid = m.h / 2;
                        for (let i = 0; i < hs.length; i++) {
                            const hh = Math.max(1, hs[i] * m.h);
                            ctx.fillRect(i * bw + bw * 0.22, mid - hh, bw * 0.56, hh * 2);
                        }
                        ctx.globalAlpha = 1;
                    }
                } catch (_) {}
            }
            if (o.ring) {
                try {
                    const m = fitCanvas(o.ring);
                    const ctx = o.ring.getContext('2d');
                    if (ctx) {
                        ctx.setTransform(m.dpr, 0, 0, m.dpr, 0, 0);
                        ctx.clearRect(0, 0, m.w, m.h);
                        const cx = m.w / 2, cy = m.h / 2;
                        const base = Math.min(m.w, m.h) / 2 - 8;
                        const rr = ringRadii(56, display, t);
                        const live = phase === 'listening' || phase === 'speaking';
                        ctx.strokeStyle = color;
                        ctx.globalAlpha = live ? 0.55 : 0.28;
                        ctx.lineWidth = live ? 2 : 1.25;
                        ctx.beginPath();
                        for (let i = 0; i <= rr.length; i++) {
                            const r = base * (1 + rr[i % rr.length]);
                            const a = (i / rr.length) * Math.PI * 2 - Math.PI / 2;
                            const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
                            if (i === 0) ctx.moveTo(x, y);
                            else ctx.lineTo(x, y);
                        }
                        ctx.closePath();
                        ctx.stroke();
                        ctx.globalAlpha = 1;
                    }
                } catch (_) {}
            }
        }

        function loop(now) {
            if (!running) return;
            paint(now || 0);
            raf = root.requestAnimationFrame(loop);
        }

        return {
            start: function () {
                stop();
                color = accentColor(color);
                display = 0;
                if (reducedMotion()) {
                    paint(1200);
                    return;
                }
                running = true;
                raf = root.requestAnimationFrame(loop);
            },
            stop: function () {
                running = false;
                if (raf) {
                    try { root.cancelAnimationFrame(raf); } catch (_) {}
                    raf = 0;
                }
            },
        };
    }

    function rmsOfFrame(buf) {
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        return Math.sqrt(sum / (buf.length || 1));
    }

    /**
     * Analysis-only mic tap for engines that hold no stream themselves
     * (browser speech recognizer). Open while listening, stop after.
     * Returns {start, stop, level} with RMS levels, 0 when off.
     */
    function createMicMonitor() {
        let stream = null;
        let ctx = null;
        let analyser = null;
        let frame = null;
        let lastRms = 0;

        return {
            async start() {
                if (stream) return;
                const md = root.navigator && root.navigator.mediaDevices;
                if (!md || typeof md.getUserMedia !== 'function') throw new Error('no-mic');
                stream = await md.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
                const Ctx = root.AudioContext || root.webkitAudioContext;
                if (!Ctx) throw new Error('no-audio');
                ctx = new Ctx();
                try { await ctx.resume(); } catch (_) {}
                analyser = ctx.createAnalyser();
                analyser.fftSize = 1024;
                ctx.createMediaStreamSource(stream).connect(analyser);
                frame = new Float32Array(analyser.fftSize);
                lastRms = 0;
            },
            level() {
                if (!analyser) return 0;
                try {
                    analyser.getFloatTimeDomainData(frame);
                    const rms = rmsOfFrame(frame);
                    if (rms > lastRms) lastRms = rms;
                    else lastRms += (rms - lastRms) * 0.2;
                    return lastRms;
                } catch (_) { return 0; }
            },
            async stop() {
                const s = stream, c = ctx;
                stream = null;
                ctx = null;
                analyser = null;
                frame = null;
                lastRms = 0;
                try { if (s) s.getTracks().forEach((t) => t.stop()); } catch (_) {}
                try { if (c) await c.close(); } catch (_) {}
            },
        };
    }

    const api = { create: create, createMicMonitor: createMicMonitor,
        smoothLevel: smoothLevel, mapLevel: mapLevel,
        ringRadii: ringRadii, barHeights: barHeights };
    root.CuttleChatVoiceWaveform = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
