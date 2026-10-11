/* ================================================================
   Cuttle Chat — stream/status transport coordination
   (chat_stream_transport.js).
   Owner: the decisions the page used to hold inline while it drove the
   status poll, the connecting watchdog, pool-stall detection and the
   local typing-indicator status:
     - the status-poll tick decision (whether to keep/stop polling, what
       live status to paint, when the worker looks finished, when a long
       stagnant status warrants a resync) and its per-poll bookkeeping
       ({lastStatus, stagnantTicks}),
     - the status-poll cadence + stagnant threshold + watchdog/probe
       timeouts (moved verbatim from the page),
     - the connecting-watchdog decisions (does the label still say
       Connecting…, does live-status preempt the health probe, what the
       probe result means, and the fallback messages after the probe
       fails),
     - pool-stall target resolution: which plain-HTTP companion a stalled
       HTTPS pool may switch to (never guesses an unrelated port),
     - the typing-status update decision (paint vs. no-op, when to
       clear/re-arm the watchdog).
   PURE: no document, no window, no fetch, no timers. The page owns the
   timers, transport, DOM paint, navigation and the recovery effects;
   it feeds snapshots in and applies the returned plans. chat_stream.js
   stays the ONLY SSE byte decoder — this module never reads a stream.
   Loaded after chat_stream.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** Status-poll cadence (moved verbatim from the page). */
    var STATUS_POLL_MS = 3500;
    /** Same tool status for this many polls (~84s) → nudge recovery sync. */
    var STAGNANT_TICKS = 24;
    /** Connecting-watchdog delay before it inspects/probes. */
    var WATCHDOG_MS = 12000;
    /** Watchdog /api/status health-probe timeout. */
    var WATCHDOG_PROBE_MS = 4000;
    /** Companion HTTP /api/status probe timeout during a pool stall. */
    var POOL_STALL_PROBE_MS = 3000;

    /** Per-poll bookkeeping for the status poll (page-held state). */
    function createStatusPollState() {
        return { lastStatus: '', stagnantTicks: 0 };
    }

    /**
     * Status-poll pre-fetch gate, run before the live-status fetch. snap:
     * { loadingThisSession, sessionId, backgrounded }. Returns
     * { stop: true } — clear the interval (not loading / no session),
     * { skip: true } — backgrounded, do nothing this tick, or
     * { fetch: true } — fetch live-status and call planStatusPollLive.
     */
    function planStatusPollStart(snap) {
        if (!snap.loadingThisSession || snap.sessionId == null) {
            return { stop: true };
        }
        if (snap.backgrounded) return { skip: true };
        return { fetch: true };
    }

    /**
     * Status-poll post-fetch decision. state: {lastStatus, stagnantTicks}
     * (mutated in place, as the page's closure did). live: the fetched
     * live-status (null → skip). stillGoing: liveStatusLooksActive(live).
     * Returns { skip?, paintStatus?, subagents?, reportUrl?, recover?,
     * resync?, stop? }:
     *   skip    — no live payload; no effects this tick
     *   paintStatus — live.status to paint through updateTypingStatus
     *   subagents   — live.subagents to sync onto launcher rows
     *   reportUrl   — live.report_url to point the query-log link at
     *   recover — worker looks done server-side: pull the reply even if
     *             SSE/pending stalled (page runs its recovery branch; stop
     *             is set so the page clears the interval)
     *   resync  — stagnant while active: run a recovery sync
     */
    function planStatusPollLive(state, live, stillGoing) {
        if (!live) return { skip: true };

        const out = {
            paintStatus: null,
            subagents: live.subagents,
            reportUrl: live.report_url || null,
            recover: false,
            resync: false,
        };
        if (live.status) {
            if (live.status === state.lastStatus) {
                state.stagnantTicks += 1;
            } else {
                state.stagnantTicks = 0;
                state.lastStatus = live.status;
            }
            out.paintStatus = live.status;
        }
        if (!stillGoing) {
            out.stop = true;
            out.recover = true;
            return out;
        }
        if (state.stagnantTicks >= STAGNANT_TICKS) {
            state.stagnantTicks = 0;
            out.resync = true;
        }
        return out;
    }

    /**
     * Typing-status update decision (updateTypingStatus body). snap: {
     *   statusText,        // requested label (null → "Connecting...")
     *   hasIndicator,      // a #typing-indicator exists
     *   currentText,       // that indicator's .typing-status text, or null
     * }.
     * Returns { text, paint, clearWatchdog, armWatchdog }. The page owns
     * the voice hook, DOM paint/scroll and the timer calls.
     */
    function planTypingStatusUpdate(snap) {
        const next = snap.statusText || 'Connecting...';
        const out = {
            text: next,
            paint: false,
            clearWatchdog: false,
            armWatchdog: false,
        };
        if (snap.hasIndicator) {
            if (snap.currentText === next) {
                if (next !== 'Connecting...' && next.indexOf('Still connecting') < 0) {
                    out.clearWatchdog = true;
                }
                return out;
            }
            out.clearWatchdog = true;
            out.paint = true;
        } else {
            out.clearWatchdog = true;
        }
        if (!snap.statusText || snap.statusText === 'Connecting...') {
            out.armWatchdog = true;
        }
        return out;
    }

    /**
     * Does the connecting watchdog still need to act for this label?
     * True while the label is the default or a "Still connecting" probe;
     * false once real status text arrived.
     */
    function watchdogWantsLive(statusText) {
        const text = String(statusText || '').trim();
        return !(text !== 'Connecting...' && text.indexOf('Still connecting') < 0);
    }

    /**
     * After the watchdog reads live-status: either paint it (a genuinely
     * active generation with a status) or fall through to the health
     * probe. Returns { kind: 'live', status, reportUrl } | { kind: 'probe' }.
     */
    function planWatchdogAfterLive(live) {
        if (live && (live.active || live.generating) && live.status) {
            return {
                kind: 'live',
                status: live.status,
                reportUrl: live.report_url || null,
            };
        }
        return { kind: 'probe' };
    }

    /**
     * Interpret the /api/status health probe. snap: { ok, status,
     * hasIndicator, stillConnecting } (hasIndicator/stillConnecting read
     * again after the await, as the page did). Returns { abort: true }
     * when the indicator vanished or already moved past "Still
     * connecting"; otherwise { message } for the label.
     */
    function planWatchdogProbe(snap) {
        if (!snap.hasIndicator) return { abort: true };
        if (!snap.stillConnecting) return { abort: true };
        if (snap.ok) {
            return { message: 'Server is up — waiting for chat stream…' };
        }
        return {
            message: 'Server health HTTP ' + snap.status
                + ' — try refreshing or restart Flask',
        };
    }

    /** Label shown while switching to the plain-HTTP companion. */
    function poolStallSwitchMessage(httpPort) {
        return 'HTTPS pool stalled — switching to HTTP portal (:'
            + httpPort + ')…';
    }

    /** Final watchdog label when no companion redirect is possible. */
    function watchdogStallMessage(inElectron) {
        return inElectron
            ? 'Cannot reach Cuttle API — Electron connection pool stalled. Quit Cuttle.exe fully and reopen (or use the HTTP portal for your connected host).'
            : 'Cannot reach Cuttle API quickly — stream may be stalled. Try Stop, or open the HTTP portal for this host.';
    }

    // Companion-HTTP target for an HTTPS pool stall (armConnectingWatchdog).
    // Pure except for the injected getDesktopConfig: resolves to
    // { probeUrl, destBase, httpPort } or null (fail closed — the caller
    // shows a generic message instead of guessing an unrelated endpoint).
    // Local paired mode requires matching host and HTTPS port. Explicit
    // single targets never auto-downgrade. Only true legacy
    // default-mode (exact https://127.0.0.1|localhost:8080 with no config
    // available, e.g. plain browser) keeps the documented :8000 companion.
    function poolStallSameHost(cfgHost, normHost, loopback127) {
        const cfgNorm = String(cfgHost || '').trim().replace(/^\[|\]$/g, '').toLowerCase();
        if (!cfgNorm) return false;
        if (cfgNorm === normHost) return true;
        return !!(loopback127 && (cfgNorm === '127.0.0.1' || cfgNorm === 'localhost'));
    }
    function poolStallTarget(host, httpPort) {
        return {
            probeUrl: `http://${host}:${httpPort}/api/status`,
            destBase: `http://${host}:${httpPort}`,
            httpPort,
        };
    }
    async function poolStallHttpTarget({ origin, getDesktopConfig }) {
        const m = /^https:\/\/(\[[^\]]+\]|[^/:]+):(\d+)$/i.exec(String(origin || ''));
        if (!m) return null;
        const host = m[1];
        const httpsPort = Number(m[2]);
        if (!Number.isSafeInteger(httpsPort) || httpsPort < 1 || httpsPort > 65535) return null;
        const normHost = host.replace(/^\[|\]$/g, '').toLowerCase();
        const loopback127 = normHost === '127.0.0.1' || normHost === 'localhost';
        let cfg = null;
        try {
            cfg = typeof getDesktopConfig === 'function' ? await getDesktopConfig() : null;
        } catch (_) {
            cfg = null;
        }
        // Explicit connections never change protocol, including saved configs
        // that contain companion metadata from an older desktop build.
        const ep = cfg ? cfg.endpoint : null;
        if (ep && ep.kind === 'single') return null;
        // A local paired target knows both owner-resolved listeners.
        if (cfg && Number(cfg.httpsPort) === httpsPort
            && poolStallSameHost(cfg.host, normHost, loopback127)) {
            const httpPort = Number(cfg.httpPort);
            if (Number.isSafeInteger(httpPort) && httpPort >= 1 && httpPort <= 65535) {
                return poolStallTarget(host, httpPort);
            }
            return null;
        }
        // Plain-browser legacy compatibility: no desktop config is available.
        if (!cfg && loopback127 && httpsPort === 8080) {
            return poolStallTarget('127.0.0.1', 8000);
        }
        return null;
    }

    var api = {
        STATUS_POLL_MS: STATUS_POLL_MS,
        STAGNANT_TICKS: STAGNANT_TICKS,
        WATCHDOG_MS: WATCHDOG_MS,
        WATCHDOG_PROBE_MS: WATCHDOG_PROBE_MS,
        POOL_STALL_PROBE_MS: POOL_STALL_PROBE_MS,
        createStatusPollState: createStatusPollState,
        planStatusPollStart: planStatusPollStart,
        planStatusPollLive: planStatusPollLive,
        planTypingStatusUpdate: planTypingStatusUpdate,
        watchdogWantsLive: watchdogWantsLive,
        planWatchdogAfterLive: planWatchdogAfterLive,
        planWatchdogProbe: planWatchdogProbe,
        poolStallSwitchMessage: poolStallSwitchMessage,
        watchdogStallMessage: watchdogStallMessage,
        poolStallSameHost: poolStallSameHost,
        poolStallTarget: poolStallTarget,
        poolStallHttpTarget: poolStallHttpTarget,
    };

    const ns = (root.CuttleChatStreamTransport = root.CuttleChatStreamTransport || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
