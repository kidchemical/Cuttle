/* ================================================================
   Cuttle Celebrate — achievement unlock celebration (celebrate.js)
   Owner: Achievements subsystem. Toast card + confetti + sound.
   Depends on: toast.js (showToast), app_shell.js (uiAnimationsEnabled
   is read defensively so this file also works standalone).

   Rarity tiers scale the celebration:
     common   → toast only
     rare     → toast + sound
     epic     → toast + sound + light confetti
     legendary→ toast + sound + full confetti + glow
     mythic   → everything, plus a screen-wide shimmer
   ================================================================ */
(function (root) {
    'use strict';

    const SFX_URL = '/sounds/achievement-unlock.wav';
    const CONFETTI_COLORS = ['#58a6ff', '#a371f7', '#d29922', '#f85149', '#3fb950', '#ffffff'];

    const RARITY_TIERS = {
        common:    { confetti: 0,   sfx: false, glow: false, shimmer: false },
        rare:      { confetti: 0,   sfx: true,  glow: false, shimmer: false },
        epic:      { confetti: 60,  sfx: true,  glow: true,  shimmer: false },
        legendary: { confetti: 120, sfx: true,  glow: true,  shimmer: false },
        mythic:    { confetti: 200, sfx: true,  glow: true,  shimmer: true },
    };

    let _audio = null;
    let _lastPlayedAt = 0;

    const RARITY_COLORS = {
        common: '#8b949e', rare: '#58a6ff', epic: '#a371f7',
        legendary: '#d29922', mythic: '#f85149',
    };

    // --------------------------------------------------------------------
    // Pure helpers (tested in node)
    // --------------------------------------------------------------------
    function tierFor(rarity) {
        return RARITY_TIERS[rarity] || RARITY_TIERS.common;
    }

    function rarityColor(rarity) {
        return RARITY_COLORS[rarity] || RARITY_COLORS.common;
    }

    /** "Achieved 12.3M / 100M tokens" style progress line for a locked item. */
    function progressLine(item, formatCount) {
        const fmt = formatCount || String;
        if (!item || item.unlocked) return '';
        return 'Progress ' + fmt(item.progress) + ' / ' + fmt(item.threshold);
    }

    function animationsEnabled() {
        if (typeof window === 'undefined' || typeof window.matchMedia === 'function') {
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

    // --------------------------------------------------------------------
    // Sound
    // --------------------------------------------------------------------
    /** Escalation ladder mirroring toast.js's completion chirp. */
    function playSfx() {
        try {
            const electron = root.electron;
            if (electron && electron.isElectron && typeof electron.playSfx === 'function') {
                if (typeof electron.isWindowObscured !== 'function') { electron.playSfx('achievement-unlock'); return Promise.resolve(); }
                return electron.isWindowObscured().then(function (obscured) {
                    if (obscured) electron.playSfx('achievement-unlock');
                    else playWebSfx();
                }).catch(function () { electron.playSfx('achievement-unlock'); });
            }
            playWebSfx();
        } catch (_) { /* autoplay policy — a silent unlock beats a thrown one */ }
        return Promise.resolve();
    }

    function playWebSfx() {
        // Debounce: a burst of unlocks should not stack five identical chimes.
        const now = Date.now();
        if (now - _lastPlayedAt < 1200) return;
        _lastPlayedAt = now;
        try {
            if (!_audio) {
                _audio = new Audio(SFX_URL);
                _audio.preload = 'auto';
                _audio.volume = 0.6;
            }
            _audio.currentTime = 0;
            const p = _audio.play();
            if (p && typeof p.catch === 'function') p.catch(function () {});
        } catch (_) { /* no audio in this environment */ }
    }

    // --------------------------------------------------------------------
    // Confetti — self-contained DOM/canvas particles, no CDN dependency.
    // --------------------------------------------------------------------
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

    // --------------------------------------------------------------------
    // Toast card
    // --------------------------------------------------------------------
    function toastFor(item, tier) {
        const notify = root.showToast || function () {};
        const label = (item && (item.title || item.id)) || 'Achievement';
        const body = (item && item.description) || '';
        notify('🏆 Achievement unlocked', 'success', {
            toastId: 'cuttle-achievement',
            sticky: !!tier.glow,
            kind: 'achievement',
            unread: true,
            duration: tier.glow ? 8000 : 5000,
            achievement: {
                id: item && item.id,
                icon: item && item.icon,
                title: label,
                description: body,
                rarity: (item && item.rarity) || 'common',
                color: rarityColor(item && item.rarity),
                progress: progressLine(item, root.CuttleAchievements && root.CuttleAchievements.formatCount),
            },
        });
    }

    function shimmer() {
        if (typeof document === 'undefined') return;
        const veil = document.createElement('div');
        veil.style.cssText = [
            'position:fixed', 'inset:0', 'pointer-events:none', 'z-index:999998',
            'background:radial-gradient(circle at 50% 40%, rgba(163,113,247,.35), transparent 60%)',
            'opacity:0', 'transition:opacity .35s ease',
        ].join(';');
        document.body.appendChild(veil);
        requestAnimationFrame(function () { veil.style.opacity = '1'; });
        setTimeout(function () {
            veil.style.opacity = '0';
            setTimeout(function () { if (veil.parentNode) veil.parentNode.removeChild(veil); }, 500);
        }, 1400);
    }

    // --------------------------------------------------------------------
    // Public
    // --------------------------------------------------------------------
    function celebrate(item) {
        if (!item) return null;
        const tier = tierFor(item.rarity);
        try { toastFor(item, tier); } catch (e) { /* never break the chat */ }
        if (tier.sfx) { try { playSfx(); } catch (_) {} }
        if (tier.confetti) { try { confetti(tier.confetti); } catch (_) {} }
        if (tier.shimmer) { try { shimmer(); } catch (_) {} }
        return tier;
    }

    const api = {
        SFX_URL: SFX_URL,
        RARITY_TIERS: RARITY_TIERS,
        CONFETTI_COLORS: CONFETTI_COLORS,
        tierFor: tierFor,
        rarityColor: rarityColor,
        progressLine: progressLine,
        animationsEnabled: animationsEnabled,
        playSfx: playSfx,
        confetti: confetti,
        toastFor: toastFor,
        celebrate: celebrate,
    };

    const ns = (root.CuttleCelebrate = root.CuttleCelebrate || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(typeof window !== 'undefined' ? window : globalThis);