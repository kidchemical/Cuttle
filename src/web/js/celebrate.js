/* ================================================================
   Cuttle Celebrate — achievement unlock celebration (celebrate.js)
   Owner: Achievements presentation. Toast card + sound; generic confetti
   delegates to CuttleChatVfx.
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

    const vfx = root.CuttleChatVfx || (typeof require === 'function' ? require('./chat_vfx.js') : null);
    function animationsEnabled() { return vfx.animationsEnabled(); }

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
    function confetti(count, options) { return vfx.confetti(count, options); }

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