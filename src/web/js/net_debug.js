/**
 * Cuttle network / pool debug insight.
 *
 * Enable:  localStorage.setItem('cuttle_net_debug', '1'); location.reload()
 * Disable: localStorage.removeItem('cuttle_net_debug'); location.reload()
 * Overlay: Ctrl+Shift+D (top window only — chat iframes forward into the shell panel)
 *
 * Tracks in-flight fetch count, slow calls, stream detach, and recent history.
 * Posts optional lines to POST /api/debug-log (channel=net → ~/cuttle_net_debug.log).
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'cuttle_net_debug';
    const OVERLAY_KEY = 'cuttle_net_debug_overlay';
    const SLOW_MS = 1000;
    const VERY_SLOW_MS = 3000;
    const WARN_INFLIGHT = 5;
    const HISTORY_CAP = 80;
    const isTop = (function () {
        try { return window.top === window; } catch (_) { return true; }
    })();
    const frameTag = (function () {
        if (isTop) return 'shell';
        try {
            const q = new URLSearchParams(location.search);
            return 'chat:' + (q.get('chat') || location.pathname);
        } catch (_) {
            return 'frame';
        }
    })();

    function enabled() {
        try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch (_) { return false; }
    }

    function overlayWanted() {
        try { return localStorage.getItem(OVERLAY_KEY) === '1'; } catch (_) { return false; }
    }

    function setOverlayWanted(on) {
        try {
            if (on) localStorage.setItem(OVERLAY_KEY, '1');
            else localStorage.removeItem(OVERLAY_KEY);
        } catch (_) {}
    }

    const state = {
        inflight: 0,
        peakInflight: 0,
        total: 0,
        slow: 0,
        verySlow: 0,
        failed: 0,
        byPath: Object.create(null),
        recent: [],
        events: [],
        streamHeld: false,
        streamHeldSince: 0,
        lastWarnAt: 0,
        frames: Object.create(null),
    };

    function shortPath(url) {
        try {
            const u = new URL(url, location.origin);
            return u.pathname + (u.search ? '?…' : '');
        } catch (_) {
            return String(url || '').slice(0, 80);
        }
    }

    function bucketPath(url) {
        try {
            const p = new URL(url, location.origin).pathname;
            if (p.indexOf('/api/auth/sessions/') === 0 && p.indexOf('/messages') > 0) return '/api/auth/sessions/*/messages';
            if (p.indexOf('/api/chat-live-status') === 0) return '/api/chat-live-status';
            if (p.indexOf('/api/chat-pending-result') === 0) return '/api/chat-pending-result';
            if (p.indexOf('/api/chat') === 0) return p;
            return p;
        } catch (_) {
            return '(other)';
        }
    }

    function pushRecent(entry) {
        state.recent.push(entry);
        if (state.recent.length > HISTORY_CAP) state.recent.shift();
    }

    function postToParent(type, payload) {
        if (isTop) return;
        try {
            window.parent.postMessage({
                type: type,
                cuttleNet: true,
                frame: frameTag,
                payload: payload || {},
            }, '*');
        } catch (_) {}
    }

    function postServerLog(msg) {
        if (!enabled() || !isTop) return;
        try {
            fetch('/api/debug-log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    channel: 'net',
                    msg: '[' + new Date().toISOString().slice(11, 23) + '] ' + msg,
                }),
                keepalive: true,
            }).catch(function () {});
        } catch (_) {}
    }

    function pushEvent(kind, detail) {
        const ev = { t: Date.now(), kind, detail: detail || '', frame: frameTag };
        if (!isTop) {
            postToParent('cuttle-net-event', { kind: kind, detail: detail || '' });
            return;
        }
        state.events.push(ev);
        if (state.events.length > 40) state.events.shift();
        if (kind === 'stream-hold') {
            state.streamHeld = true;
            state.streamHeldSince = Date.now();
        } else if (kind === 'stream-detach' || kind === 'stream-done' || kind === 'pending-got' || kind === 'pending-miss') {
            if (kind !== 'stream-hold') {
                state.streamHeld = false;
                state.streamHeldSince = 0;
            }
        }
        console.log('[CuttleNet]', kind, detail || '');
        postServerLog(kind + (detail ? ' ' + detail : ''));
        renderOverlay();
    }

    function noteComplete(meta) {
        if (!isTop) {
            postToParent('cuttle-net-fetch', meta);
            return;
        }
        const path = bucketPath(meta.url);
        if (!state.byPath[path]) state.byPath[path] = { n: 0, slow: 0, maxMs: 0 };
        const b = state.byPath[path];
        b.n += 1;
        if (meta.ms > b.maxMs) b.maxMs = meta.ms;
        if (meta.ms >= SLOW_MS) {
            b.slow += 1;
            state.slow += 1;
        }
        if (meta.ms >= VERY_SLOW_MS) state.verySlow += 1;
        if (meta.ok === false) state.failed += 1;
        pushRecent({
            t: Date.now(),
            method: meta.method,
            path: shortPath(meta.url),
            ms: Math.round(meta.ms),
            status: meta.status,
            ok: meta.ok,
            frame: meta.frame || 'shell',
        });
        if (meta.ms >= VERY_SLOW_MS || (state.inflight >= WARN_INFLIGHT && Date.now() - state.lastWarnAt > 5000)) {
            state.lastWarnAt = Date.now();
            console.warn(
                '[CuttleNet] pressure',
                'inflight=' + state.inflight,
                'peak=' + state.peakInflight,
                meta.method,
                shortPath(meta.url),
                Math.round(meta.ms) + 'ms',
                meta.status
            );
        }
        renderOverlay();
    }

    function applyChildFetch(frame, meta) {
        // Child frames report completions only; approximate inflight from active child reports.
        state.total += 1;
        noteComplete(Object.assign({}, meta, { frame: frame }));
    }

    function patchFetch() {
        if (window.__cuttleNetFetchPatched) return;
        window.__cuttleNetFetchPatched = true;
        const orig = window.fetch.bind(window);
        window.fetch = function (input, init) {
            if (!enabled()) return orig(input, init);
            const url = typeof input === 'string' ? input : (input && input.url) || '';
            if (String(url).indexOf('/api/debug-log') >= 0) return orig(input, init);
            const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
            const started = performance.now();
            if (isTop) {
                state.inflight += 1;
                state.total += 1;
                if (state.inflight > state.peakInflight) state.peakInflight = state.inflight;
                renderOverlay();
            } else {
                postToParent('cuttle-net-inflight', { delta: 1 });
            }
            return orig(input, init).then(
                function (resp) {
                    const ms = performance.now() - started;
                    if (isTop) {
                        state.inflight = Math.max(0, state.inflight - 1);
                        noteComplete({
                            url: url, method: method, ms: ms, status: resp.status, ok: resp.ok, frame: 'shell',
                        });
                    } else {
                        postToParent('cuttle-net-inflight', { delta: -1 });
                        postToParent('cuttle-net-fetch', {
                            url: url, method: method, ms: ms, status: resp.status, ok: resp.ok,
                        });
                    }
                    return resp;
                },
                function (err) {
                    const ms = performance.now() - started;
                    if (isTop) {
                        state.inflight = Math.max(0, state.inflight - 1);
                        noteComplete({
                            url: url, method: method, ms: ms, status: 0, ok: false, frame: 'shell',
                        });
                    } else {
                        postToParent('cuttle-net-inflight', { delta: -1 });
                        postToParent('cuttle-net-fetch', {
                            url: url, method: method, ms: ms, status: 0, ok: false,
                        });
                    }
                    throw err;
                }
            );
        };
    }

    let overlayEl = null;
    let overlayTimer = null;

    function ensureOverlay() {
        if (!isTop || overlayEl || !enabled()) return;
        overlayEl = document.createElement('div');
        overlayEl.id = 'cuttle-net-debug';
        overlayEl.setAttribute('aria-live', 'polite');
        overlayEl.style.cssText = [
            'position:fixed', 'right:10px', 'bottom:10px', 'z-index:2147483000',
            'width:min(380px,calc(100vw - 20px))', 'max-height:45vh', 'overflow:auto',
            'font:11px/1.35 ui-monospace,Consolas,monospace',
            'color:#e8edf5', 'background:rgba(12,16,24,0.92)',
            'border:1px solid rgba(167,139,250,0.45)', 'border-radius:10px',
            'padding:10px 12px', 'box-shadow:0 8px 28px rgba(0,0,0,0.45)',
            'backdrop-filter:blur(8px)', 'display:none',
        ].join(';');
        (document.body || document.documentElement).appendChild(overlayEl);
        overlayEl.addEventListener('dblclick', function () {
            showOverlay(overlayEl.style.display === 'none');
        });
    }

    function topPaths(n) {
        return Object.keys(state.byPath)
            .map(function (k) { return { k: k, v: state.byPath[k] }; })
            .sort(function (a, b) { return b.v.n - a.v.n; })
            .slice(0, n);
    }

    function renderOverlay() {
        if (!isTop || !enabled()) return;
        ensureOverlay();
        if (!overlayEl || overlayEl.style.display === 'none') return;
        const held = state.streamHeld
            ? Math.round((Date.now() - state.streamHeldSince) / 1000) + 's'
            : 'no';
        const pressure = state.inflight >= WARN_INFLIGHT ? ' ⚠ POOL PRESSURE' : '';
        const tops = topPaths(6).map(function (x) {
            return x.k + ' ×' + x.v.n + (x.v.slow ? ' slow:' + x.v.slow : '') + ' max:' + Math.round(x.v.maxMs) + 'ms';
        }).join('\n');
        const recent = state.recent.slice(-8).map(function (r) {
            const flag = r.ms >= VERY_SLOW_MS ? '!!' : (r.ms >= SLOW_MS ? '!' : ' ');
            return flag + ' ' + r.ms + 'ms ' + (r.frame || '') + ' ' + r.method + ' ' + r.status + ' ' + r.path;
        }).join('\n');
        const evs = state.events.slice(-6).map(function (e) {
            return (e.frame ? e.frame + ' ' : '') + e.kind + (e.detail ? ' — ' + e.detail : '');
        }).join('\n');
        overlayEl.innerHTML =
            '<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:6px">' +
            '<strong style="color:#c4b5fd">Cuttle net debug' + pressure + '</strong>' +
            '<span style="opacity:0.7">dblclick hide · Ctrl+Shift+D</span></div>' +
            '<div>inflight <b style="color:' + (state.inflight >= WARN_INFLIGHT ? '#fbbf24' : '#86efac') + '">' +
            state.inflight + '</b> · peak ' + state.peakInflight +
            ' · total ' + state.total + '</div>' +
            '<div>slow≥1s ' + state.slow + ' · ≥3s ' + state.verySlow + ' · fail ' + state.failed +
            ' · SSE hold ' + held + '</div>' +
            '<div style="margin-top:6px;opacity:0.85">Top paths\n' + escapeHtml(tops || '(none)') + '</div>' +
            '<div style="margin-top:6px;opacity:0.85">Recent\n' + escapeHtml(recent || '(none)') + '</div>' +
            '<div style="margin-top:6px;opacity:0.85">Events\n' + escapeHtml(evs || '(none)') + '</div>';
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function showOverlay(on) {
        if (!isTop) {
            postToParent('cuttle-net-overlay', { on: !!on });
            return;
        }
        ensureOverlay();
        if (!overlayEl) return;
        setOverlayWanted(!!on);
        overlayEl.style.display = on ? 'block' : 'none';
        if (on) renderOverlay();
    }

    function snapshot() {
        return {
            enabled: enabled(),
            overlay: overlayWanted(),
            isTop: isTop,
            frame: frameTag,
            inflight: state.inflight,
            peakInflight: state.peakInflight,
            total: state.total,
            slow: state.slow,
            verySlow: state.verySlow,
            failed: state.failed,
            streamHeld: state.streamHeld,
            byPath: state.byPath,
            recent: state.recent.slice(-20),
            events: state.events.slice(-20),
            origin: location.origin,
        };
    }

    window.CuttleNetDebug = {
        enabled: enabled,
        event: pushEvent,
        snapshot: snapshot,
        show: function () { showOverlay(true); },
        hide: function () { showOverlay(false); },
        enable: function () {
            try { localStorage.setItem(STORAGE_KEY, '1'); } catch (_) {}
            patchFetch();
            startTick();
            pushEvent('debug-on', location.origin);
        },
        disable: function () {
            try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
            setOverlayWanted(false);
            showOverlay(false);
            if (overlayTimer) { clearInterval(overlayTimer); overlayTimer = null; }
        },
    };

    function startTick() {
        if (!isTop || overlayTimer) return;
        overlayTimer = setInterval(function () {
            if (enabled() && overlayEl && overlayEl.style.display !== 'none') renderOverlay();
        }, 1000);
    }

    if (isTop) {
        window.addEventListener('message', function (e) {
            const d = e.data;
            if (!d || !d.cuttleNet) return;
            if (d.type === 'cuttle-net-inflight') {
                state.inflight = Math.max(0, state.inflight + (d.payload && d.payload.delta ? d.payload.delta : 0));
                if (state.inflight > state.peakInflight) state.peakInflight = state.inflight;
                renderOverlay();
            } else if (d.type === 'cuttle-net-fetch') {
                applyChildFetch(d.frame || 'frame', d.payload || {});
            } else if (d.type === 'cuttle-net-event') {
                const p = d.payload || {};
                state.events.push({
                    t: Date.now(),
                    kind: p.kind,
                    detail: p.detail || '',
                    frame: d.frame || 'frame',
                });
                if (state.events.length > 40) state.events.shift();
                if (p.kind === 'stream-hold') {
                    state.streamHeld = true;
                    state.streamHeldSince = Date.now();
                } else if (p.kind === 'stream-detach' || p.kind === 'stream-done' || p.kind === 'pending-got') {
                    state.streamHeld = false;
                    state.streamHeldSince = 0;
                }
                postServerLog((d.frame || 'frame') + ' ' + p.kind + (p.detail ? ' ' + p.detail : ''));
                renderOverlay();
            } else if (d.type === 'cuttle-net-overlay') {
                showOverlay(!!(d.payload && d.payload.on));
            } else if (d.type === 'cuttle-net-overlay-toggle') {
                if (!enabled()) {
                    window.CuttleNetDebug.enable();
                    showOverlay(true);
                } else {
                    ensureOverlay();
                    const on = !overlayEl || overlayEl.style.display === 'none';
                    showOverlay(on);
                }
            }
        });
    }

    document.addEventListener('keydown', function (e) {
        if (!(e.ctrlKey && e.shiftKey && (e.key === 'D' || e.key === 'd'))) return;
        e.preventDefault();
        // Only the top shell owns the panel (avoids 1 shell + N iframe clones).
        if (!isTop) {
            postToParent('cuttle-net-overlay-toggle', {});
            return;
        }
        if (!enabled()) {
            window.CuttleNetDebug.enable();
            showOverlay(true);
            return;
        }
        ensureOverlay();
        const on = !overlayEl || overlayEl.style.display === 'none';
        showOverlay(on);
    });

    if (enabled()) {
        patchFetch();
        if (isTop) startTick();
        const boot = function () {
            pushEvent('debug-on', location.origin);
            if (isTop && overlayWanted()) showOverlay(true);
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', boot);
        } else {
            boot();
        }
    }
})();
