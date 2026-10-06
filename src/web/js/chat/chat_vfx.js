/* Generic visual effects; session poller receives explicit host capabilities. */
(function (root) {
    'use strict';
    const CONFETTI_COLORS = ['#58a6ff', '#a371f7', '#d29922', '#f85149', '#3fb950', '#ffffff'];
    function animationsEnabled() {
        if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
            try {
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
            } catch (_) { /* ignore */ }
        }
        try {
            if (localStorage.getItem('cuttleUiAnimations') === '0') return false;
            if (localStorage.getItem('notificationsEnabled') === 'false') return false;
        } catch (_) { /* ignore */ }
        // app_shell exposes the shell-wide preference; honour it when present.
        try {
            if (typeof root.uiAnimationsEnabled === 'function' && !root.uiAnimationsEnabled()) return false;
        } catch (_) { /* ignore */ }
        return true;
    }

    function confetti(count, options) {
        if (typeof document === 'undefined') return null;
        const opts = options || {};
        if (!animationsEnabled()) return null;
        const total = Math.max(0, Math.min(400, count || 0));
        if (!total) return null;

        const layer = document.createElement('div');
        layer.className = 'cuttle-confetti-layer';
        layer.setAttribute('aria-hidden', 'true');
        layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:999999;overflow:hidden';
        document.body.appendChild(layer);

        const shards = [];
        for (let i = 0; i < total; i += 1) {
            const shard = document.createElement('i');
            const color = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
            const w = 6 + Math.random() * 7;
            const h = 8 + Math.random() * 10;
            shard.style.cssText = [
                'position:absolute',
                'top:-24px',
                'left:' + (Math.random() * 100).toFixed(2) + 'vw',
                'width:' + w.toFixed(1) + 'px',
                'height:' + h.toFixed(1) + 'px',
                'background:' + color,
                'opacity:' + (0.75 + Math.random() * 0.25).toFixed(2),
                'border-radius:' + (Math.random() < 0.3 ? '50%' : '1px'),
                'transform:rotate(' + (Math.random() * 360).toFixed(0) + 'deg)',
                'transition:transform ' + (2.1 + Math.random() * 1.8).toFixed(2) + 's cubic-bezier(.15,.6,.4,1),'
                    + 'opacity ' + (2.1 + Math.random() * 1.8).toFixed(2) + 's ease-in',
            ].join(';');
            layer.appendChild(shard);
            shards.push({
                el: shard,
                dx: (Math.random() - 0.5) * 220,
                dy: window.innerHeight + 80 + Math.random() * 220,
                rot: (Math.random() - 0.5) * 1440,
            });
        }

        // Next frame so the transition has a start state to animate from.
        requestAnimationFrame(function () {
            shards.forEach(function (s) {
                s.el.style.transform = 'translate(' + s.dx.toFixed(0) + 'px,' + s.dy.toFixed(0) + 'px) rotate(' + s.rot.toFixed(0) + 'deg)';
                s.el.style.opacity = '0';
            });
        });

        const cleanupMs = opts.cleanupMs || 5200;
        setTimeout(function () { if (layer.parentNode) layer.parentNode.removeChild(layer); }, cleanupMs);
        return layer;
    }


    function create(host) {
        const cursors = new Map();
        let stopped = false, timer = null, controller = null;
        async function tick() {
            if (stopped) return;
            const session = host.getSessionId();
            if (session) {
                const key = String(session);
                controller = new AbortController();
                try {
                    const response = await host.fetch('/api/chat-vfx/' + encodeURIComponent(key) + '?after=' + (cursors.get(key) || 0), {signal: controller.signal});
                    if (response.ok) {
                        const data = await response.json();
                        // Navigation during a request must never paint into another chat.
                        if (!stopped && String(host.getSessionId()) === key) {
                            for (const event of data.events || []) {
                                if (event.seq <= (cursors.get(key) || 0)) continue;
                                cursors.set(key, event.seq);
                                if (event.expires * 1000 <= host.now()) continue;
                                if (event.kind === 'confetti') host.confetti(event.count);
                                else if (event.kind === 'toast') host.toast(event.message, event.variant);
                            }
                        }
                    }
                } catch (_) { /* transient transport error; retry next tick */ }
                finally { controller = null; }
            }
            if (!stopped) timer = host.setTimeout(tick, 2000);
        }
        tick();
        return {dispose: function () {
            stopped = true;
            if (timer !== null) host.clearTimeout(timer);
            if (controller) controller.abort();
        }};
    }
    const api = {confetti: confetti, animationsEnabled: animationsEnabled, create: create};
    root.CuttleChatVfx = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
