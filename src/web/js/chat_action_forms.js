/* ================================================================
   Cuttle Chat — action-forms frontend domain (chat_action_forms.js)
   Owner: action-forms UI interpretation. Card model predicates, watch
   interpretation, restart presentation decisions, and lock/state
   pure logic: no document, no window, no localStorage, no fetch here
   (all inputs are explicit arguments). Loaded before chat_page.js; the
   chat page owns DOM rendering, fetch transport, timers/polling,
   message-history integration, and orchestration, and calls into
   `CuttleChatActionForms.*`.

   Mirrors the stabilized backend contract (Phase 2 Slice 2): one-shot
   locking, already-locked behavior, watch-state, follow-up and restart
   recovery semantics. Card attribute reads stay in the page; this
   module decides over plain data.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * True only for an explicit dismiss/cancel option. Q&A choices omit
     * `action` on purpose and must NOT be treated as cancel (that used to
     * collapse every preference click to "Cancelled.").
     */
    function isExplicitActionFormCancelOption(opt) {
        if (!opt || typeof opt !== 'object') return false;
        if (opt.cancel === true) return true;
        if (String(opt.action || '') === '__dismiss__') return true;
        return String(opt.id || '').toLowerCase() === 'cancel';
    }

    /** False for Q&A cards: answers go back to the agent, nothing runs or posts. */
    function actionFormHasSideEffect(spec) {
        if (!spec || typeof spec !== 'object') return false;
        if (spec.watch) return true;
        if (spec.submit && spec.submit.action) return true;
        const opts = Array.isArray(spec.options) ? spec.options : [];
        return opts.some((o) => o && o.action && !isExplicitActionFormCancelOption(o));
    }

    /**
     * Action cards own their own progress line: a daemon restart requested from
     * a card reports here (spinner → green check) instead of dropping ack and
     * completion bubbles into the transcript.
     */
    function isWatchFormAction(action) {
        const a = String(action || '');
        return a === '__watch_resume__' || a === '__watch_park__' || a === '__watch_cancel__';
    }

    function specLooksLikeFlaskRestart(spec, formId) {
        const opts = (spec && spec.options) || [];
        if (opts.some((o) => {
            const a = String((o && o.action) || '');
            return a === 'flask.restart' || a === '__native_restart__';
        })) return true;
        const title = String((spec && spec.title) || '').trim().toLowerCase();
        if (title.includes('restart flask') || title.startsWith('flask restart')) return true;
        // Form id flask-restart-gN alone is not enough: a git.push card that
        // reused that id would otherwise show restart chrome / run Status as push.
        return false;
    }

    function inferActionFormWatch(spec) {
        const options = Array.isArray(spec && spec.options) ? spec.options : [];
        const hasWatchBtn = options.some((o) => isWatchFormAction((o && o.action) || ''));
        let watch = (spec && spec.watch && typeof spec.watch === 'object') ? spec.watch : null;
        if (!watch && hasWatchBtn) {
            const fromParams = options
                .map((o) => o && o.params && o.params.url)
                .find((u) => u);
            watch = {
                id: 'job',
                url: String(fromParams || '/output/trellis-download-status.json'),
                interval_ms: 4000,
                done_states: ['done', 'trellis_ok'],
                fail_states: ['failed'],
            };
        }
        return watch;
    }

    function safeActionFormWatchUrl(url) {
        const u = String(url || '').trim();
        if (u.startsWith('/output/') || u.startsWith('/api/')) return u;
        return '';
    }

    function watchRunKey(data) {
        if (!data || typeof data !== 'object') return '';
        return String(data.run_id || data.started_at || '').trim();
    }

    function cardWatchBind(watch) {
        if (!watch || typeof watch !== 'object') return '';
        return String(watch.run_id || watch.started_at || '').trim();
    }

    function watchSnapshotFromSpec(watch) {
        const snap = watch && watch.snapshot && typeof watch.snapshot === 'object' ? watch.snapshot : null;
        return snap;
    }

    function watchIsTerminalState(watch, data, doneStates, failStates) {
        const state = String((data && data.state) || '');
        if (watch && watch.terminal) return true;
        const done = Array.isArray(doneStates) ? doneStates : ['done', 'trellis_ok'];
        const fail = Array.isArray(failStates) ? failStates : ['failed'];
        return done.indexOf(state) >= 0 || fail.indexOf(state) >= 0;
    }

    /**
     * Per-card storage key (not per job-id: /build and /deploy both used
     * "ep-release" and leaked Continue/Park across forms).
     */
    function actionFormWatchStorageKey(parts) {
        const p = parts || {};
        const formId = String(p.formId || '').trim();
        const watch = p.watch || {};
        const sid = String(p.sessionId || '').trim();
        const id = formId || [watch.id, watch.started_at || watch.run_id].filter(Boolean).join('.') || 'watch';
        return 'cuttle.formWatch.' + sid + '.' + id;
    }

    function formatWatchElapsedSeconds(sec) {
        const n = Math.max(0, Math.floor(Number(sec) || 0));
        const h = Math.floor(n / 3600);
        const m = Math.floor((n % 3600) / 60);
        const s = n % 60;
        if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm ' + String(s).padStart(2, '0') + 's';
        if (m > 0) return m + 'm ' + String(s).padStart(2, '0') + 's';
        return s + 's';
    }

    function watchElapsedText(data) {
        if (!data) return '';
        const state = String(data.state || '');
        if ((state === 'done' || state === 'failed') && data.elapsed) return String(data.elapsed);
        if ((state === 'done' || state === 'failed') && data.elapsed_sec != null) {
            return formatWatchElapsedSeconds(data.elapsed_sec);
        }
        const started = Date.parse(data.started_at || '');
        if (!Number.isNaN(started)) {
            return formatWatchElapsedSeconds((Date.now() - started) / 1000);
        }
        if (data.elapsed) return String(data.elapsed);
        return '';
    }

    function normalizeWatchBars(data) {
        const pct = Math.max(0, Math.min(100, Number(data && data.percent != null ? data.percent : 0)));
        const label = String((data && (data.label || data.file)) || '');
        const raw = (data && Array.isArray(data.bars)) ? data.bars : null;
        if (!raw || !raw.length) {
            return [{ id: 'overall', label: label || 'Overall', percent: pct, kind: 'primary' }];
        }
        return raw.slice(0, 12).map((b, i) => {
            const kind = String((b && b.kind) || (i === 0 ? 'primary' : 'worker')).toLowerCase();
            const safeKind = (kind === 'primary' || kind === 'worker' || kind === 'secondary')
                ? kind
                : (i === 0 ? 'primary' : 'worker');
            return {
                id: String((b && (b.id || b.label)) || ('bar' + i)).slice(0, 64),
                label: String((b && b.label) || b.id || ('Bar ' + (i + 1))).slice(0, 120),
                percent: Math.max(0, Math.min(100, Number(b && b.percent != null ? b.percent : 0))),
                kind: safeKind,
                detail: String((b && b.detail) || '').slice(0, 160),
            };
        });
    }

    const RESTART_PROGRESS_PCT = {
        waiting_for_idle: 12,
        acknowledged: 22,
        preparing: 32,
        stopping_old_flask: 52,
        starting_new_flask: 72,
        health_checking: 88,
        healthy: 100,
        failed: 100,
        timed_out: 100,
        rejected: 100,
        cancelled: 100,
    };

    function restartProgressPercent(state) {
        const pct = RESTART_PROGRESS_PCT[String(state || '')];
        return pct != null ? pct : null;
    }

    const RESTART_TERMINAL_STATES = ['healthy', 'failed', 'timed_out', 'rejected', 'cancelled'];

    function isRestartTerminalState(state) {
        return RESTART_TERMINAL_STATES.indexOf(String(state || '')) >= 0;
    }

    function restartProgressLabel(status, liveWork) {
        const state = String((status && status.state) || '');
        // Live count while waiting; the status file's copy is a request-time snapshot.
        const work = liveWork || (status && status.active_work) || {};
        const n = Number(work.active_count || 0);
        switch (state) {
            case 'waiting_for_idle':
                return n > 0
                    ? `Waiting for ${n} active task${n === 1 ? '' : 's'} to finish…`
                    : 'Waiting for active work to finish…';
            case 'acknowledged':
            case 'preparing':
                return 'Handing off to the daemon…';
            case 'stopping_old_flask':
                return 'Stopping Flask…';
            case 'starting_new_flask':
                return 'Starting Flask…';
            case 'health_checking':
                return 'Health check…';
            case 'healthy': {
                const ms = status && status.health_ms;
                const pid = status && status.new_flask_pid;
                const detail = [
                    pid ? `PID ${pid}` : '',
                    ms != null ? `${(Number(ms) / 1000).toFixed(1)}s` : '',
                ].filter(Boolean).join(', ');
                return detail ? `Flask restarted — ${detail}` : 'Flask restarted';
            }
            case 'failed':
            case 'timed_out':
                return `Restart ${state.replace('_', ' ')} — ${(status && status.error) || 'see daemon logs'}`;
            case 'rejected':
                return 'Restart postponed — other work is still running';
            case 'cancelled':
                return 'Restart cancelled';
            default:
                return 'Restarting Flask…';
        }
    }

    /**
     * Flask restart cards share form id `flask-restart-g{generation}` across chats
     * until the daemon actually replaces Flask. One click should settle every
     * sibling card; after restart the generation bumps and new cards unlink.
     */
    function flaskRestartFormEpoch(formId) {
        const m = /^flask-restart-g(\d+)$/i.exec(String(formId || '').trim());
        return m ? parseInt(m[1], 10) : null;
    }

    /** Whether a {spec, formId} pair is a linked restart card (no DOM). */
    function isFlaskRestartLinked(spec, formId) {
        if (!spec && !formId) return false;
        try {
            return specLooksLikeFlaskRestart(spec || {}, formId);
        } catch (_) {
            return false;
        }
    }

    /** Group id linking sibling restart cards (explicit group wins, else form id). */
    function linkedRestartFormId(parts) {
        const p = parts || {};
        const fid = String(p.formId || '').trim();
        if (flaskRestartFormEpoch(fid) != null) return fid;
        const group = String(p.group || '').trim();
        return group || fid;
    }

    const api = {
        isExplicitActionFormCancelOption,
        actionFormHasSideEffect,
        isWatchFormAction,
        specLooksLikeFlaskRestart,
        inferActionFormWatch,
        safeActionFormWatchUrl,
        watchRunKey,
        cardWatchBind,
        watchSnapshotFromSpec,
        watchIsTerminalState,
        actionFormWatchStorageKey,
        formatWatchElapsedSeconds,
        watchElapsedText,
        normalizeWatchBars,
        RESTART_PROGRESS_PCT,
        restartProgressPercent,
        RESTART_TERMINAL_STATES,
        isRestartTerminalState,
        restartProgressLabel,
        flaskRestartFormEpoch,
        isFlaskRestartLinked,
        linkedRestartFormId,
    };

    const ns = (root.CuttleChatActionForms = root.CuttleChatActionForms || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
