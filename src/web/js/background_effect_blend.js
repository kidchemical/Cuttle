/**
 * Opacity of landing_page.css animated body::before gradient (0 = hidden, 100 = full).
 * Persists to localStorage: cuttleBackgroundEffectBlend
 */
(function () {
    'use strict';

    var KEY = 'cuttleBackgroundEffectBlend';

    function applyBackgroundEffectBlend(percent, persist) {
        var p = Math.max(0, Math.min(100, Math.round(Number(percent))));
        if (isNaN(p)) p = 100;
        if (persist !== false) {
            try {
                localStorage.setItem(KEY, String(p));
            } catch (_) {}
        }
        document.documentElement.style.setProperty('--cuttle-bg-effect-blend', String(p / 100));
    }

    function initBackgroundEffectBlend() {
        try {
            var v = localStorage.getItem(KEY);
            var n = v !== null ? parseInt(v, 10) : 100;
            if (isNaN(n)) n = 100;
            document.documentElement.style.setProperty(
                '--cuttle-bg-effect-blend',
                String(Math.max(0, Math.min(100, n)) / 100)
            );
        } catch (_) {
            document.documentElement.style.setProperty('--cuttle-bg-effect-blend', '1');
        }
    }

    window.addEventListener('message', function (e) {
        if (!e.data || typeof e.data !== 'object') return;
        if (e.data.type === 'cuttle-bg-effect-blend' && typeof e.data.value === 'number') {
            applyBackgroundEffectBlend(e.data.value, true);
        }
    });

    window.CuttleBackgroundEffectBlend = {
        apply: function (p) {
            applyBackgroundEffectBlend(p, true);
        },
        init: initBackgroundEffectBlend,
        storageKey: KEY,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initBackgroundEffectBlend);
    } else {
        initBackgroundEffectBlend();
    }
})();
