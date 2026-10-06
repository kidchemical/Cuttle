/**
 * Load optional CDN libs after first paint so offline / slow WAN never blocks
 * DOMContentLoaded (which would leave Electron chat panes black until timeout).
 *
 * highlight.js + Vega are niceties for code blocks / charts — chat works without them.
 */
(function () {
    'use strict';

    var LOADED = {};
    // key → callbacks waiting for an in-flight script; absent once it settled.
    var PENDING = {};

    function injectScript(src, key, onDone) {
        if (PENDING[key]) {
            if (typeof onDone === 'function') PENDING[key].push(onDone);
            return;
        }
        if (LOADED[key] || document.querySelector('script[data-cuttle-cdn="' + key + '"]')) {
            if (typeof onDone === 'function') onDone();
            return;
        }
        LOADED[key] = true;
        PENDING[key] = typeof onDone === 'function' ? [onDone] : [];
        function settle() {
            var waiting = PENDING[key] || [];
            delete PENDING[key];
            waiting.forEach(function (fn) { fn(); });
        }
        var s = document.createElement('script');
        s.src = src;
        s.async = true;
        s.dataset.cuttleCdn = key;
        s.onload = settle;
        s.onerror = function () {
            try { console.warn('[Cuttle] optional CDN failed (offline?):', src); } catch (_) {}
            settle();
        };
        document.head.appendChild(s);
    }

    /** Vega → Vega-Lite → vega-embed must run in order (Lite reads vega.logger on load). */
    function injectScriptChain(items, onDone) {
        var i = 0;
        function next() {
            if (i >= items.length) {
                if (typeof onDone === 'function') onDone();
                return;
            }
            var item = items[i++];
            injectScript(item.src, item.key, next);
        }
        next();
    }

    function injectStylesheet(href, key) {
        if (document.querySelector('link[data-cuttle-cdn="' + key + '"]')) return;
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = href;
        l.dataset.cuttleCdn = key;
        l.media = 'print';
        l.onload = function () { l.media = 'all'; };
        l.onerror = function () {
            try { console.warn('[Cuttle] optional CDN CSS failed (offline?):', href); } catch (_) {}
        };
        document.head.appendChild(l);
    }

    function loadChatExtras() {
        injectStylesheet(
            'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/styles/github-dark.min.css',
            'hljs-css'
        );
        injectScript(
            'https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/highlight.min.js',
            'hljs'
        );
        injectScriptChain(
            [
                { src: 'https://cdn.jsdelivr.net/npm/vega@5.30.0', key: 'vega' },
                { src: 'https://cdn.jsdelivr.net/npm/vega-lite@5.21.0', key: 'vega-lite' },
                { src: 'https://cdn.jsdelivr.net/npm/vega-embed@6.26.0', key: 'vega-embed' },
            ],
            function () {
                try {
                    window.dispatchEvent(new CustomEvent('cuttle-vega-ready'));
                } catch (_) {}
            }
        );
    }

    function loadPlotlyExtras(cb) {
        injectScript(
            'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js',
            'plotly',
            function () {
                try {
                    window.dispatchEvent(new CustomEvent('cuttle-plotly-ready'));
                } catch (_) {}
                if (typeof cb === 'function') cb();
            }
        );
    }

    function loadTerminalExtras(cb) {
        injectStylesheet(
            'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css',
            'xterm-css'
        );
        var pending = 3;
        function done() {
            pending -= 1;
            if (pending <= 0 && typeof cb === 'function') cb();
        }
        function inject(src, key) {
            if (LOADED[key] || document.querySelector('script[data-cuttle-cdn="' + key + '"]')) {
                done();
                return;
            }
            LOADED[key] = true;
            var s = document.createElement('script');
            s.src = src;
            s.async = true;
            s.dataset.cuttleCdn = key;
            s.onload = done;
            s.onerror = function () {
                try { console.warn('[Cuttle] optional CDN failed (offline?):', src); } catch (_) {}
                done();
            };
            document.head.appendChild(s);
        }
        inject('https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js', 'xterm');
        inject('https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js', 'xterm-fit');
        inject('https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.min.js', 'xterm-links');
    }

    function boot() {
        var path = (location.pathname || '').toLowerCase();
        if (path.indexOf('terminal_page') !== -1) {
            // Terminal page loads its own extras before init (see terminal_page.js).
            return;
        }
        if (path.indexOf('dashboards_page') !== -1) {
            loadPlotlyExtras();
            return;
        }
        if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(loadChatExtras, { timeout: 2500 });
        } else {
            setTimeout(loadChatExtras, 0);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.CuttleOptionalCdn = {
        loadChatExtras: loadChatExtras,
        loadPlotlyExtras: loadPlotlyExtras,
        loadTerminalExtras: loadTerminalExtras,
    };
})();
