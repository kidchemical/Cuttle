/* Voice-mode starfield canvas; drift speed follows the voice phase. */
(function (root) {
    'use strict';

    const PHASE_SPEED = { listening: 1.6, processing: 2.2, speaking: 1.2 };

    function reducedMotion() {
        try {
            return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) { return false; }
    }

    function baseColor() {
        try {
            const v = String(getComputedStyle(document.body).getPropertyValue('--text-primary') || '').trim();
            if (v) return v;
        } catch (_) {}
        try {
            const dark = root.matchMedia && root.matchMedia('(prefers-color-scheme: dark)').matches;
            return dark ? '#ffffff' : '#5b6472';
        } catch (_) { return '#ffffff'; }
    }

    function seed(w, h) {
        const count = Math.max(50, Math.min(150, Math.round((w * h) / 11000)));
        const stars = [];
        for (let i = 0; i < count; i++) {
            stars.push({
                x: Math.random() * w,
                y: Math.random() * h,
                r: 0.4 + Math.random() * 1.3,
                tw: Math.random() * Math.PI * 2,
                twSpeed: 0.6 + Math.random() * 1.8,
                drift: 3 + Math.random() * 11,
                depth: 0.35 + Math.random() * 0.65,
            });
        }
        return stars;
    }

    /**
     * @param {HTMLCanvasElement} canvas
     * @param {HTMLElement} overlay  sizes the canvas
     * @param {() => string} getPhase
     */
    function create(canvas, overlay, getPhase) {
        let raf = 0;
        let stars = [];
        let color = '#ffffff';
        let running = false;

        function measure() {
            const rect = overlay.getBoundingClientRect();
            return {
                w: Math.max(1, Math.round(rect.width || overlay.clientWidth || root.innerWidth || 300)),
                h: Math.max(1, Math.round(rect.height || overlay.clientHeight || root.innerHeight || 300)),
                dpr: Math.min(2, root.devicePixelRatio || 1),
            };
        }

        function resize() {
            const m = measure();
            canvas.width = Math.round(m.w * m.dpr);
            canvas.height = Math.round(m.h * m.dpr);
            stars = seed(m.w, m.h);
        }

        function frame(nowMs) {
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            const m = measure();
            ctx.setTransform(m.dpr, 0, 0, m.dpr, 0, 0);
            const t = nowMs / 1000;
            const speed = PHASE_SPEED[getPhase()] || 0.8;
            ctx.clearRect(0, 0, m.w, m.h);
            ctx.fillStyle = color;
            for (let i = 0; i < stars.length; i++) {
                const s = stars[i];
                s.y -= (s.drift * speed) / 60;
                if (s.y < -4) {
                    s.y = m.h + 4;
                    s.x = Math.random() * m.w;
                }
                const tw = 0.5 + 0.5 * Math.sin(t * s.twSpeed * speed + s.tw);
                ctx.globalAlpha = Math.min(1, (0.18 + 0.62 * tw) * s.depth);
                ctx.beginPath();
                ctx.arc(s.x, s.y, s.r * (0.8 + 0.4 * tw), 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
        }

        function loop(now) {
            if (!running) return;
            frame(now || 0);
            raf = root.requestAnimationFrame(loop);
        }

        function start() {
            stop();
            color = baseColor();
            resize();
            if (reducedMotion()) {
                frame(1200);
                return;
            }
            running = true;
            root.addEventListener('resize', resize);
            raf = root.requestAnimationFrame(loop);
        }

        function stop() {
            running = false;
            if (raf) {
                try { root.cancelAnimationFrame(raf); } catch (_) {}
                raf = 0;
            }
            root.removeEventListener('resize', resize);
            stars = [];
        }

        return { start: start, stop: stop };
    }

    const api = { create: create };
    root.CuttleChatVoiceStars = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
