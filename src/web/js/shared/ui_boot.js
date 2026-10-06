/**
 * Cuttle UI boot.
 *
 * Must be loaded synchronously from <head> on every page the app shell can
 * frame. It settles theme, wallpaper and background-blend state before the
 * first paint, so swapping pages never flashes the wrong theme or the page's
 * opaque fill before the shell video shows through.
 *
 * localStorage keys: theme, cuttleVideoBackgroundActive,
 * cuttleBackgroundEffectBlend, cuttleUiAnimations, cuttleUiTransitionMs,
 * cuttleVideoBackgroundList, cuttleVideoBackgroundEnabled
 */
(function () {
    'use strict';

    var THEME_KEY = 'theme';
    var VIDEO_ACTIVE_KEY = 'cuttleVideoBackgroundActive';
    var VIDEO_LIST_KEY = 'cuttleVideoBackgroundList';
    var VIDEO_ENABLED_KEY = 'cuttleVideoBackgroundEnabled';
    var BLEND_KEY = 'cuttleBackgroundEffectBlend';
    var ANIM_KEY = 'cuttleUiAnimations';
    var ANIM_MS_KEY = 'cuttleUiTransitionMs';

    /* Every theme rides on one of the three base classes so page stylesheets
       that only know dark/midnight/light keep working; non-base themes add a
       `theme-<id>` class whose palette lives in css/themes.css. */
    var THEMES = [
        { id: 'dark', label: 'Dark', icon: '🌙', base: 'dark', group: 'Classic' },
        { id: 'midnight', label: 'Midnight', icon: '🌑', base: 'midnight', group: 'Classic' },
        { id: 'light', label: 'Light', icon: '☀️', base: 'light', group: 'Classic' },
        { id: 'sakura', label: 'Sakura', icon: '🌸', base: 'light', group: 'Pastel' },
        { id: 'mint', label: 'Mint', icon: '🍃', base: 'light', group: 'Pastel' },
        { id: 'lavender', label: 'Lavender Dream', icon: '💜', base: 'light', group: 'Pastel' },
        { id: 'paper', label: 'Paper', icon: '📄', base: 'light', group: 'Minimal' },
        { id: 'graphite', label: 'Graphite', icon: '◼️', base: 'midnight', group: 'Minimal' },
        { id: 'nord', label: 'Nordic', icon: '❄️', base: 'dark', group: 'Minimal' },
        { id: 'rgb', label: 'RGB', icon: '🌈', base: 'midnight', group: 'Loud' },
        { id: 'synthwave', label: 'Synthwave', icon: '🌆', base: 'dark', group: 'Loud' },
        { id: 'glitch', label: 'Glitch', icon: '📺', base: 'midnight', group: 'Loud' },
        { id: 'matrix', label: 'Matrix', icon: '💾', base: 'midnight', group: 'Loud' }
    ];
    var THEME_BY_ID = {};
    var THEME_CLASSES = ['dark-mode', 'midnight-mode', 'light-mode', 'theme-custom'];
    for (var ti = 0; ti < THEMES.length; ti++) {
        THEME_BY_ID[THEMES[ti].id] = THEMES[ti];
        if (THEMES[ti].id !== THEMES[ti].base) THEME_CLASSES.push('theme-' + THEMES[ti].id);
    }
    /* Keeps RGB hue cycling in phase across the shell and its iframes. */
    var THEME_CYCLE_MS = 18000;
    var DEFAULT_ANIM_MS = 220;
    var MIN_ANIM_MS = 0;
    var MAX_ANIM_MS = 1000;

    var root = document.documentElement;
    var framed = window !== window.top;
    var readySignaled = false;

    function isCuttleMobileClient() {
        try {
            if (window.isCuttleMobile || (window.cuttleMobile && window.cuttleMobile.isNative)) return true;
        } catch (_) {}
        try {
            if (window.parent !== window) {
                var p = window.parent;
                if (p.isCuttleMobile || (p.cuttleMobile && p.cuttleMobile.isNative)) return true;
            }
        } catch (_) {}
        return /\bCuttleMobile\/[\d.]+\b/.test(navigator.userAgent || '');
    }

    /* Safe-area: chat runs in an iframe where env(safe-area-inset-*) is often
       0 on Android WebView even when the shell parent has real insets. Publish
       --safe-area-inset-* on <html> (see safe_area.css) and fan out to frames. */
    function safeAreaPx(value) {
        if (typeof value === 'number' && isFinite(value)) return value + 'px';
        var s = String(value == null ? '' : value).trim();
        if (!s) return '0px';
        if (/px$/i.test(s)) return s;
        var n = parseFloat(s);
        return isFinite(n) ? n + 'px' : '0px';
    }

    function safeAreaParsePx(value) {
        var n = parseFloat(value);
        return isFinite(n) ? n : 0;
    }

    function readEnvSafeAreaInsets() {
        var probe = document.createElement('div');
        probe.setAttribute('data-cuttle-safe-probe', '1');
        probe.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;padding:0;'
            + 'visibility:hidden;pointer-events:none;overflow:hidden;'
            + 'padding-top:env(safe-area-inset-top,0px);'
            + 'padding-right:env(safe-area-inset-right,0px);'
            + 'padding-bottom:env(safe-area-inset-bottom,0px);'
            + 'padding-left:env(safe-area-inset-left,0px);';
        (document.body || root).appendChild(probe);
        var cs = window.getComputedStyle(probe);
        var out = {
            top: cs.paddingTop,
            right: cs.paddingRight,
            bottom: cs.paddingBottom,
            left: cs.paddingLeft
        };
        if (probe.parentNode) probe.parentNode.removeChild(probe);
        return out;
    }

    function readNativeSafeAreaInsets() {
        try {
            var fn = window.cuttleMobile && window.cuttleMobile.getSafeAreaInsets;
            if (typeof fn === 'function') {
                var v = fn();
                if (v && typeof v === 'object') return v;
            }
        } catch (_) {}
        try {
            if (window.CuttleShellNative && typeof window.CuttleShellNative.getSafeAreaInsets === 'function') {
                var raw = window.CuttleShellNative.getSafeAreaInsets();
                if (typeof raw === 'string') return JSON.parse(raw);
                if (raw && typeof raw === 'object') return raw;
            }
        } catch (_) {}
        /* Chat iframe has no JavascriptInterface — borrow the shell's bridge. */
        if (framed) {
            try {
                var pFn = window.parent
                    && window.parent.cuttleMobile
                    && window.parent.cuttleMobile.getSafeAreaInsets;
                if (typeof pFn === 'function') {
                    var pv = pFn();
                    if (pv && typeof pv === 'object') return pv;
                }
            } catch (_) {}
        }
        return null;
    }

    function readParentSafeAreaInsets() {
        if (!framed) return null;
        try {
            var pub = window.parent && window.parent.__cuttleSafeArea;
            if (pub && typeof pub === 'object') {
                return {
                    top: pub.top,
                    right: pub.right,
                    bottom: pub.bottom,
                    left: pub.left
                };
            }
        } catch (_) {}
        try {
            var pRoot = window.parent && window.parent.document && window.parent.document.documentElement;
            if (!pRoot) return null;
            var ps = window.parent.getComputedStyle(pRoot);
            return {
                top: pRoot.style.getPropertyValue('--safe-area-inset-top') || ps.getPropertyValue('--cuttle-safe-top'),
                right: pRoot.style.getPropertyValue('--safe-area-inset-right') || ps.getPropertyValue('--cuttle-safe-right'),
                bottom: pRoot.style.getPropertyValue('--safe-area-inset-bottom') || ps.getPropertyValue('--cuttle-safe-bottom'),
                left: pRoot.style.getPropertyValue('--safe-area-inset-left') || ps.getPropertyValue('--cuttle-safe-left')
            };
        } catch (_) {}
        return null;
    }

    function safeAreaSum(insets) {
        if (!insets) return 0;
        return safeAreaParsePx(insets.top)
            + safeAreaParsePx(insets.right)
            + safeAreaParsePx(insets.bottom)
            + safeAreaParsePx(insets.left);
    }

    function resolveSafeAreaInsets() {
        var env = readEnvSafeAreaInsets();
        var parentInsets = readParentSafeAreaInsets();
        var native = isCuttleMobileClient() ? readNativeSafeAreaInsets() : null;
        var cssNeeded = !!(native && native.cssNeeded === true);
        var envSum = safeAreaSum(env);
        var parentSum = safeAreaSum(parentInsets);
        var nativeSum = native ? (
            safeAreaParsePx(native.top)
            + safeAreaParsePx(native.right)
            + safeAreaParsePx(native.bottom)
            + safeAreaParsePx(native.left)
        ) : 0;

        /* Chromium ≥140 + edge-to-edge: prefer native when env() is missing or
           under-reports (common in iframes / some Samsung WebViews). Chromium
           <140 pads the WebView natively and sets cssNeeded=false — keep 0. */
        if (cssNeeded && nativeSum > 0 && (
            envSum < 1
            || safeAreaParsePx(native.top) > safeAreaParsePx(env.top) + 0.5
            || safeAreaParsePx(native.bottom) > safeAreaParsePx(env.bottom) + 0.5
        )) {
            return {
                top: safeAreaPx(native.top),
                right: safeAreaPx(native.right),
                bottom: safeAreaPx(native.bottom),
                left: safeAreaPx(native.left)
            };
        }
        if (parentSum > envSum) {
            return {
                top: safeAreaPx(parentInsets.top),
                right: safeAreaPx(parentInsets.right),
                bottom: safeAreaPx(parentInsets.bottom),
                left: safeAreaPx(parentInsets.left)
            };
        }
        return {
            top: safeAreaPx(env.top),
            right: safeAreaPx(env.right),
            bottom: safeAreaPx(env.bottom),
            left: safeAreaPx(env.left)
        };
    }

    function fanOutSafeAreaInsets(insets) {
        if (framed || !insets) return;
        try {
            var frames = document.querySelectorAll('iframe');
            for (var i = 0; i < frames.length; i++) {
                try {
                    var win = frames[i].contentWindow;
                    if (!win) continue;
                    win.postMessage({ type: 'cuttle-safe-area', insets: insets }, '*');
                } catch (_) {}
            }
        } catch (_) {}
    }

    /* Pages that lay out their own insets (chat) set data-cuttle-safe-area="self"
       on <html>. Every other shell page is inset by the shell instead: tag our
       iframe so safe_area.css moves it clear of the status/nav bars. Toggle, not
       add — in-frame navigation reuses the same iframe element. */
    function syncShellFrameInset() {
        if (!framed) return false;
        var el = null;
        try { el = window.frameElement; } catch (_) {}
        if (!el || !el.classList || !el.closest || !el.closest('.shell-main')) return false;
        var inset = root.getAttribute('data-cuttle-safe-area') !== 'self';
        el.classList.toggle('shell-safe-inset', inset);
        return inset;
    }

    function applySafeAreaInsets(optionalInsets) {
        if (!isCuttleMobileClient()) return;
        var insets = optionalInsets || resolveSafeAreaInsets();
        if (!insets) return;
        var normalized = {
            top: safeAreaPx(insets.top),
            right: safeAreaPx(insets.right),
            bottom: safeAreaPx(insets.bottom),
            left: safeAreaPx(insets.left)
        };
        if (syncShellFrameInset()) {
            normalized.top = '0px';
            normalized.bottom = '0px';
        }
        root.style.setProperty('--safe-area-inset-top', normalized.top);
        root.style.setProperty('--safe-area-inset-right', normalized.right);
        root.style.setProperty('--safe-area-inset-bottom', normalized.bottom);
        root.style.setProperty('--safe-area-inset-left', normalized.left);
        window.__cuttleSafeArea = normalized;
        fanOutSafeAreaInsets(normalized);
    }

    function read(key) {
        try { return localStorage.getItem(key); } catch (_) { return null; }
    }

    function write(key, value) {
        try { localStorage.setItem(key, value); } catch (_) {}
    }

    function normalizeTheme(theme) {
        return THEME_BY_ID[theme] ? theme : 'dark';
    }

    function themeBase(theme) {
        return THEME_BY_ID[normalizeTheme(theme)].base;
    }

    function themeClass(theme) {
        return themeBase(theme) + '-mode';
    }

    function themeClassList(theme) {
        var id = normalizeTheme(theme);
        var out = [themeClass(id)];
        if (id !== THEME_BY_ID[id].base) out.push('theme-custom', 'theme-' + id);
        return out;
    }

    function prefersReducedMotion() {
        try {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (_) {
            return false;
        }
    }

    function animationsEnabled() {
        if (prefersReducedMotion()) return false;
        return read(ANIM_KEY) !== '0';
    }

    function transitionMs() {
        var n = parseInt(read(ANIM_MS_KEY), 10);
        if (isNaN(n)) return DEFAULT_ANIM_MS;
        return Math.max(MIN_ANIM_MS, Math.min(MAX_ANIM_MS, n));
    }

    function playlistConfigured() {
        try {
            var raw = read(VIDEO_LIST_KEY);
            if (!raw) {
                var legacy = read('cuttleVideoBackground');
                return !!(legacy && String(legacy).trim());
            }
            var arr = JSON.parse(raw);
            return Array.isArray(arr) && arr.some(function (u) { return u && String(u).trim(); });
        } catch (_) {
            return false;
        }
    }

    function wallpaperEnabled() {
        return read(VIDEO_ENABLED_KEY) !== '0';
    }

    /* Wallpaper state: prefer localStorage when framed. Sync parent.document
       access from an iframe can deadlock Electron's shared renderer (seen when
       loading terminal_page inside the app shell). The shell still broadcasts
       cuttle-video-state after load for any race. */
    function detectWallpaper() {
        if (!wallpaperEnabled()) {
            return { video: false };
        }
        return {
            video: read(VIDEO_ACTIVE_KEY) === '1' || playlistConfigured()
        };
    }

    var state = {
        theme: normalizeTheme(read(THEME_KEY)),
        video: false
    };

    var wallpaper = detectWallpaper();
    state.video = wallpaper.video;

    function applyTo(el) {
        if (!el) return;
        var list = el.classList;
        var want = themeClassList(state.theme);
        for (var i = 0; i < THEME_CLASSES.length; i++) {
            if (want.indexOf(THEME_CLASSES[i]) === -1) list.remove(THEME_CLASSES[i]);
        }
        for (var j = 0; j < want.length; j++) list.add(want[j]);
        list.toggle('has-video-background', state.video);
        list.toggle('is-cuttle-mobile', isCuttleMobileClient());
    }

    function apply() {
        applyTo(root);
        applyTo(document.body);
        root.classList.toggle('in-shell', framed);
        root.classList.toggle('cuttle-anim', animationsEnabled());
        root.classList.add('cuttle-booted');
        root.style.setProperty('--cuttle-transition-ms', transitionMs() + 'ms');
        if (state.video) write(VIDEO_ACTIVE_KEY, '1');
        applySafeAreaInsets();
    }

    document.addEventListener('cuttle-mobile-ready', function () {
        applyTo(root);
        applyTo(document.body);
        applySafeAreaInsets();
    });

    function applyBlend(percent, persist) {
        var p = Math.round(Number(percent));
        if (isNaN(p)) p = 100;
        p = Math.max(0, Math.min(100, p));
        if (persist) write(BLEND_KEY, String(p));
        root.style.setProperty('--cuttle-bg-effect-blend', String(p / 100));
    }

    /* Set once: re-setting an animation-delay mid-run makes it jump. */
    root.style.setProperty('--cuttle-theme-phase', '-' + (Date.now() % THEME_CYCLE_MS) + 'ms');
    apply();
    applyBlend(read(BLEND_KEY) === null ? 100 : read(BLEND_KEY), false);

    /* <body> does not exist yet when this runs from <head>. The observer fires
       on a microtask the moment the parser inserts it — still before paint —
       which lets us correct any theme class hardcoded in the markup. */
    if (document.body) {
        applyTo(document.body);
    } else if (typeof MutationObserver === 'function') {
        var observer = new MutationObserver(function () {
            if (!document.body) return;
            applyTo(document.body);
            observer.disconnect();
        });
        observer.observe(root, { childList: true });
    } else {
        document.addEventListener('DOMContentLoaded', function () { applyTo(document.body); });
    }

    window.addEventListener('message', function (e) {
        var data = e && e.data;
        if (!data || typeof data !== 'object') return;

        if (data.type === 'cuttle-theme-change' && data.theme) {
            state.theme = normalizeTheme(data.theme);
            write(THEME_KEY, state.theme);
            apply();
        } else if (data.type === 'cuttle-video-state' && typeof data.active === 'boolean') {
            state.video = wallpaperEnabled() && (data.active || playlistConfigured());
            write(VIDEO_ACTIVE_KEY, state.video ? '1' : '0');
            apply();
        } else if (data.type === 'cuttle-bg-effect-blend' && typeof data.value === 'number') {
            applyBlend(data.value, true);
        } else if (data.type === 'cuttle-ui-anim-change') {
            apply();
        } else if (data.type === 'cuttle-shell-visibility' && typeof data.hidden === 'boolean') {
            root.classList.toggle('cuttle-bg-paused', !!data.hidden);
            if (document.body) document.body.classList.toggle('cuttle-bg-paused', !!data.hidden);
        } else if (data.type === 'cuttle-safe-area' && data.insets && typeof data.insets === 'object') {
            applySafeAreaInsets(data.insets);
        }
    });

    if (isCuttleMobileClient()) {
        window.addEventListener('resize', function () { applySafeAreaInsets(); }, { passive: true });
        window.addEventListener('orientationchange', function () {
            setTimeout(function () { applySafeAreaInsets(); }, 50);
        });
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', function () { applySafeAreaInsets(); }, { passive: true });
        }
        if (!framed) {
            document.addEventListener('load', function (e) {
                var t = e && e.target;
                if (t && t.tagName === 'IFRAME') applySafeAreaInsets();
            }, true);
        }
    }

    /* Pause decorative infinite CSS while the shell window is backgrounded. */
    function applyBgPaused(hidden) {
        root.classList.toggle('cuttle-bg-paused', !!hidden);
        if (document.body) document.body.classList.toggle('cuttle-bg-paused', !!hidden);
    }
    if (framed) {
        try { window.parent.postMessage({ type: 'cuttle-shell-visibility-request' }, '*'); } catch (_) {}
    } else {
        applyBgPaused(document.hidden);
        document.addEventListener('visibilitychange', function () {
            applyBgPaused(document.hidden);
        });
    }

    /* Local <link rel=stylesheet> settle + retry.
       document.styleSheets only lists sheets that already loaded — a failed
       LAN/HTTPS fetch never appears there, so the old "walk styleSheets" check
       reported settled=true while the UI was unstyled. Video wallpaper still
       painted because video_background.js uses inline styles. Common on phone
       WebViews when Werkzeug HTTPS drops parallel CSS under connection pressure. */
    var CSS_RETRY_AFTER_MS = 700;
    var CSS_WAIT_MS = 4500;
    var CSS_RELOAD_KEY = 'cuttleCssReload';
    var recoveryStarted = false;

    function isLocalHref(href) {
        if (!href) return false;
        try {
            var origin = location.origin || '';
            if (!origin) return String(href).indexOf('://') === -1;
            return String(href).indexOf(origin) === 0;
        } catch (_) {
            return false;
        }
    }

    function localStylesheetLinks() {
        var out = [];
        var nodes = document.querySelectorAll('link[rel="stylesheet"]');
        for (var i = 0; i < nodes.length; i++) {
            var link = nodes[i];
            if (!isLocalHref(link.href || '')) continue;
            // Optional font CSS starts as media=print; never block on it.
            var media = String(link.media || 'all').trim().toLowerCase();
            if (media && media !== 'all' && media !== 'screen') continue;
            out.push(link);
        }
        return out;
    }

    function linkHasRules(link) {
        try {
            var sheet = link.sheet;
            if (!sheet) return false;
            void sheet.cssRules;
            return true;
        } catch (err) {
            if (err && err.name === 'InvalidAccessError') return false;
            return false;
        }
    }

    function stylesheetsSettled() {
        var links = localStylesheetLinks();
        if (!links.length) return true;
        for (var i = 0; i < links.length; i++) {
            if (!linkHasRules(links[i])) return false;
        }
        return true;
    }

    function reinsertStylesheet(link) {
        try {
            var raw = link.getAttribute('href') || '';
            if (!raw) return;
            var cleaned = raw.replace(/([?&])_cssr=\d+/g, '$1').replace(/[?&]$/, '');
            var sep = cleaned.indexOf('?') >= 0 ? '&' : '?';
            var next = link.cloneNode(true);
            next.setAttribute('href', cleaned + sep + '_cssr=' + Date.now());
            if (link.parentNode) {
                link.parentNode.insertBefore(next, link.nextSibling);
                link.parentNode.removeChild(link);
            }
        } catch (_) {}
    }

    function recoverStylesheets(done) {
        var started = Date.now();
        var retried = false;
        function tick() {
            if (document.body) applyTo(document.body);
            if (stylesheetsSettled()) {
                done(true);
                return;
            }
            var elapsed = Date.now() - started;
            if (!retried && elapsed >= CSS_RETRY_AFTER_MS) {
                retried = true;
                var links = localStylesheetLinks();
                for (var i = 0; i < links.length; i++) {
                    if (!linkHasRules(links[i])) reinsertStylesheet(links[i]);
                }
            }
            if (elapsed >= CSS_WAIT_MS) {
                done(false);
                return;
            }
            setTimeout(tick, 50);
        }
        tick();
    }

    function clearCssReloadFlag() {
        try {
            if (stylesheetsSettled()) sessionStorage.removeItem(CSS_RELOAD_KEY);
        } catch (_) {}
    }

    function maybeReloadForCss() {
        try {
            if (stylesheetsSettled()) return false;
            if (sessionStorage.getItem(CSS_RELOAD_KEY) === '1') return false;
            sessionStorage.setItem(CSS_RELOAD_KEY, '1');
            location.reload();
            return true;
        } catch (_) {
            return false;
        }
    }

    /* Tell the shell the page is painted so it can fade us in. Wait until
       theme/wallpaper classes are on <body>, local stylesheets have applied,
       and two frames have painted — otherwise the fade reveals FOUC.
       CSS recovery also runs on the top-level shell (not just iframes). */
    function signalReady() {
        if (recoveryStarted) return;
        recoveryStarted = true;
        recoverStylesheets(function (ok) {
            clearCssReloadFlag();
            // One-shot document reload when local CSS never arrived (phone LAN).
            if (!ok && maybeReloadForCss()) return;
            if (!framed || readySignaled) return;
            var attempts = 0;
            function tick() {
                attempts += 1;
                if (document.body) applyTo(document.body);
                var bodyReady = !!document.body &&
                    document.body.classList.contains(themeClass(state.theme)) &&
                    (!state.video || document.body.classList.contains('has-video-background'));
                if (!bodyReady && attempts < 30) {
                    requestAnimationFrame(tick);
                    return;
                }
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        if (readySignaled) return;
                        readySignaled = true;
                        try { window.parent.postMessage({ type: 'cuttle-page-ready' }, '*'); } catch (_) {}
                    });
                });
            }
            tick();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', signalReady);
        // Offline CDN / hung parser tasks can delay DOMContentLoaded for a long
        // time. Never leave the shell iframe invisible waiting on that.
        setTimeout(signalReady, 1200);
    } else {
        signalReady();
    }

    window.CuttleUiBoot = {
        applyTheme: function (theme) {
            state.theme = normalizeTheme(theme);
            write(THEME_KEY, state.theme);
            apply();
            if (framed) {
                try { window.parent.postMessage({ type: 'cuttle-theme-change', theme: state.theme }, '*'); } catch (_) {}
            }
        },
        setWallpaperActive: function (active) {
            state.video = !!active;
            write(VIDEO_ACTIVE_KEY, active ? '1' : '0');
            apply();
        },
        applyBlend: function (percent) { applyBlend(percent, true); },
        animationsEnabled: animationsEnabled,
        transitionMs: transitionMs,
        refresh: apply,
        themes: THEMES.map(function (t) {
            return { id: t.id, label: t.label, icon: t.icon, base: t.base, group: t.group };
        }),
        currentTheme: function () { return state.theme; },
        themeBase: themeBase,
        normalizeTheme: normalizeTheme,
        keys: {
            theme: THEME_KEY,
            wallpaper: VIDEO_ACTIVE_KEY,
            wallpaperEnabled: VIDEO_ENABLED_KEY,
            blend: BLEND_KEY,
            animations: ANIM_KEY,
            transitionMs: ANIM_MS_KEY
        }
    };
})();

/* Cuttle styled tooltips — promote native title= and show a floating tip
   matching the left-rail look. Skip .icon-rail (CSS ::after already).
   Touch / coarse pointers: ordinary controls never open tips on tap/focus — that stuck a
   "Chat history" tip above the Search row into the status-bar unsafe area
   on phones when opening the history panel. Explicit button.cuttle-info
   controls opt into tap-to-toggle help (also keyboard focus + Escape). */
(function () {
    'use strict';

    var SHOW_DELAY_MS = 220;
    var PAD = 8;
    var tipEl = null;
    var activeEl = null;
    var showTimer = null;
    var mo = null;
    var lastPointerType = '';
    var dismissedInfo = null;

    function ensureTip() {
        if (tipEl && tipEl.isConnected) return tipEl;
        tipEl = document.createElement('div');
        tipEl.className = 'cuttle-tooltip';
        tipEl.id = 'cuttle-shared-tooltip';
        tipEl.setAttribute('role', 'tooltip');
        tipEl.hidden = true;
        (document.body || document.documentElement).appendChild(tipEl);
        return tipEl;
    }

    function isRailManaged(el) {
        return !!(el && el.closest && el.closest('.icon-rail'));
    }

    /** Desktop fine-pointer hover only. Phones/tablets skip hover tips. */
    function tipHoverCapable() {
        try {
            return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
        } catch (_) {
            return true;
        }
    }

    function isTouchishPointer(e) {
        var pt = e && e.pointerType;
        return pt === 'touch' || pt === 'pen';
    }

    function cssPxVar(name) {
        try {
            var raw = getComputedStyle(document.documentElement).getPropertyValue(name);
            var n = parseFloat(raw);
            return isFinite(n) ? n : 0;
        } catch (_) {
            return 0;
        }
    }

    /** Keep tips inside the usable viewport (status bar / home indicator). */
    function tipInsets() {
        return {
            top: Math.max(PAD, cssPxVar('--cuttle-safe-top')),
            right: Math.max(PAD, cssPxVar('--cuttle-safe-right')),
            bottom: Math.max(PAD, cssPxVar('--cuttle-safe-bottom')),
            left: Math.max(PAD, cssPxVar('--cuttle-safe-left'))
        };
    }

    function promote(el) {
        if (!el || el.nodeType !== 1) return;
        if (el.hasAttribute('data-native-title')) return;
        // SVG <title> children are not HTML title attributes; getAttribute is fine.
        var title = el.getAttribute('title');
        if (title == null) return;
        if (!String(title).trim()) {
            el.removeAttribute('title');
            return;
        }
        // Always refresh — JS often reassigns .title after the first promote
        // (session name, star label, copy-ack). Keeping a stale data-tooltip
        // left native title removed but the tip text wrong.
        el.setAttribute('data-tooltip', title);
        el.removeAttribute('title');
    }

    function promoteTree(root) {
        if (!root) return;
        if (root.nodeType === 1) promote(root);
        if (!root.querySelectorAll) return;
        var list = root.querySelectorAll('[title]');
        for (var i = 0; i < list.length; i++) promote(list[i]);
    }

    function hideTip() {
        if (showTimer) {
            clearTimeout(showTimer);
            showTimer = null;
        }
        if (activeEl && tipEl) {
            var ids = String(activeEl.getAttribute('aria-describedby') || '').split(/\s+/)
                .filter(function (id) { return id && id !== tipEl.id; });
            if (ids.length) activeEl.setAttribute('aria-describedby', ids.join(' '));
            else activeEl.removeAttribute('aria-describedby');
        }
        activeEl = null;
        if (!tipEl) return;
        tipEl.hidden = true;
        tipEl.classList.remove('is-visible', 'is-wrap');
        tipEl.textContent = '';
    }

    function placeTip(anchor) {
        var text = String(anchor.getAttribute('data-tooltip') || '').trim();
        if (!text || !anchor.isConnected || isRailManaged(anchor)) {
            hideTip();
            return;
        }
        var tip = ensureTip();
        if (anchor.matches('button.cuttle-info')) {
            var ids = String(anchor.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
            if (ids.indexOf(tip.id) < 0) ids.push(tip.id);
            anchor.setAttribute('aria-describedby', ids.join(' '));
        }
        tip.textContent = text;
        var wrap = text.length > 42 || (text.indexOf(' ') >= 0 && text.length > 28);
        tip.classList.toggle('is-wrap', wrap);
        tip.hidden = false;
        tip.classList.remove('is-visible');
        tip.style.left = '0px';
        tip.style.top = '0px';

        var inset = tipInsets();
        var ar = anchor.getBoundingClientRect();
        var tr = tip.getBoundingClientRect();
        var left = ar.left + (ar.width - tr.width) / 2;
        var top = ar.top - tr.height - 8;
        if (top < inset.top) {
            top = ar.bottom + 8;
        }
        if (top + tr.height > window.innerHeight - inset.bottom) {
            top = Math.max(inset.top, window.innerHeight - tr.height - inset.bottom);
        }
        left = Math.max(inset.left, Math.min(left, window.innerWidth - tr.width - inset.right));
        tip.style.left = Math.round(left) + 'px';
        tip.style.top = Math.round(top) + 'px';
        tip.classList.add('is-visible');
    }

    function scheduleShow(el, keyboardInfo) {
        if (!el || isRailManaged(el)) return;
        if (!el.hasAttribute('data-tooltip')) return;
        if (!tipHoverCapable() && !keyboardInfo) return;
        if (activeEl && activeEl !== el) hideTip();
        if (activeEl === el && tipEl && !tipEl.hidden) {
            placeTip(el);
            return;
        }
        activeEl = el;
        if (showTimer) clearTimeout(showTimer);
        showTimer = setTimeout(function () {
            showTimer = null;
            if (activeEl === el) placeTip(el);
        }, SHOW_DELAY_MS);
    }

    function onPointerOver(e) {
        if (isTouchishPointer(e) || !tipHoverCapable()) return;
        var t = e.target;
        if (!t || !t.closest) return;
        var el = t.closest('[data-tooltip], [title]');
        if (!el) return;
        promote(el);
        scheduleShow(el);
    }

    function onPointerOut(e) {
        if (!activeEl) return;
        var t = e.target;
        if (!t || !t.closest) return;
        var el = t.closest('[data-tooltip]');
        if (!el || el !== activeEl) return;
        var rel = e.relatedTarget;
        if (rel && el.contains(rel)) return;
        hideTip();
    }

    function onPointerDown(e) {
        if (e && e.pointerType) lastPointerType = e.pointerType;
        var info = e.target && e.target.closest && e.target.closest('button.cuttle-info[data-tooltip]');
        dismissedInfo = info && info === activeEl && tipEl && !tipEl.hidden ? info : null;
        // Any press dismisses — tips are hover affordances, not tap labels.
        hideTip();
    }

    function onFocusIn(e) {
        // Tap-to-focus on phones must not open tips (history button → panel).
        if (lastPointerType === 'touch' || lastPointerType === 'pen') return;
        var t = e.target;
        if (!t || !t.closest) return;
        var el = t.closest('[data-tooltip], [title]');
        if (!el) return;
        if (!tipHoverCapable() && !el.matches('button.cuttle-info')) return;
        promote(el);
        scheduleShow(el, el.matches('button.cuttle-info'));
    }

    function onInfoClick(e) {
        var el = e.target && e.target.closest && e.target.closest('button.cuttle-info[data-tooltip]');
        if (!el || el.disabled) return;
        if (dismissedInfo === el) { dismissedInfo = null; return; }
        if (activeEl === el && tipEl && !tipEl.hidden) { hideTip(); return; }
        hideTip();
        activeEl = el;
        placeTip(el);
    }

    function onKeyDown(e) {
        if (e.key === 'Tab') lastPointerType = '';
        if (e.key === 'Escape') { hideTip(); dismissedInfo = null; }
    }

    function onFocusOut() {
        hideTip();
    }

    function onScroll() {
        if (activeEl && tipEl && !tipEl.hidden) placeTip(activeEl);
    }

    function boot() {
        promoteTree(document);
        if (!document.body && !document.documentElement) return;
        document.addEventListener('pointerover', onPointerOver, true);
        document.addEventListener('pointerout', onPointerOut, true);
        document.addEventListener('pointerdown', onPointerDown, true);
        document.addEventListener('focusin', onFocusIn, true);
        document.addEventListener('focusout', onFocusOut, true);
        document.addEventListener('click', onInfoClick, true);
        document.addEventListener('keydown', onKeyDown, true);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', hideTip);

        if (typeof MutationObserver !== 'undefined') {
            mo = new MutationObserver(function (muts) {
                if (activeEl && !activeEl.isConnected) hideTip();
                for (var i = 0; i < muts.length; i++) {
                    var m = muts[i];
                    if (m.type === 'attributes' && m.attributeName === 'title') {
                        promote(m.target);
                    }
                    var nodes = m.addedNodes;
                    for (var j = 0; j < nodes.length; j++) {
                        if (nodes[j].nodeType === 1) promoteTree(nodes[j]);
                    }
                }
            });
            mo.observe(document.documentElement, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['title']
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.CuttleTooltips = {
        promote: promote,
        promoteTree: promoteTree,
        hide: hideTip,
        refresh: function () { promoteTree(document); }
    };
})();
