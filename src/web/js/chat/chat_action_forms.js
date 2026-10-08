/* ================================================================
   Cuttle Chat — action-forms frontend domain (chat_action_forms.js)
   Owner: action-forms UI interpretation. Card model predicates, watch
   interpretation, restart presentation decisions, lock/state pure logic,
   and card HTML/render planning (`renderActionFormCardHtml`,
   `renderWatchBarsHtml`): no document, no window, no localStorage, no
   fetch here (all inputs are explicit arguments). Loaded before
   chat_page.js; the chat page owns DOM rendering, fetch transport,
   timers/polling, message-history integration, and orchestration, and
   calls into `CuttleChatActionForms.*`.

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
                url: String(fromParams || '/output/job-status.json'),
                interval_ms: 4000,
                done_states: ['done'],
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
        const done = Array.isArray(doneStates) ? doneStates : ['done'];
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

    // Hash identities rather than list positions so colours survive new claims.
    function watchGroupColour(id) {
        let hash = 0;
        for (const c of String(id)) hash = (Math.imul(hash, 31) + c.charCodeAt(0)) | 0;
        return `hsl(${[160, 200, 260, 300, 80, 35][((hash % 6) + 6) % 6]} 62% 58%)`;
    }

    const WATCH_GRID_STATES = ['pending', 'running', 'completed', 'failed', 'missing', 'cancelled', 'skipped'];
    // Snapshots persisted before the generic grid used render vocabulary.
    const WATCH_GRID_STATE_ALIASES = { rendering: 'running', active: 'running', done: 'completed' };

    function renderWatchGridHtml(grid, esc) {
        if (!grid || !Array.isArray(grid.cells) || !grid.cells.length) return '';
        // Pre-generic snapshots carry `frame`/`gap_fill` and no unit.
        const legacy = !grid.unit && grid.cells.some(c => c && c.frame != null);
        const unit = String(grid.unit || (legacy ? 'frame' : 'item')).slice(0, 24);
        const unitTitle = unit.charAt(0).toUpperCase() + unit.slice(1);
        const markedLabel = String(grid.marked_label || (legacy ? 'gap-fill' : 'marked')).slice(0, 40);
        const seen = new Set();
        let anyMarked = false;
        const cells = grid.cells.slice(0, 2048).map((c) => {
            if (!c) return '';
            const key = c.key != null ? c.key : c.frame;
            if (!(Number.isSafeInteger(key) || (typeof key === 'string' && key))) return '';
            const raw = WATCH_GRID_STATE_ALIASES[c.state] || c.state;
            const state = WATCH_GRID_STATES.includes(raw) ? raw : 'pending';
            seen.add(state);
            const group = String(c.group || c.worker || '').slice(0, 120);
            const marked = !!(c.marked || c.gap_fill);
            anyMarked = anyMarked || marked;
            const title = `${unitTitle} ${String(key).slice(0, 64)} · ${state}` + (group ? ` · ${group}` : '')
                + (c.note ? ` · ${String(c.note).slice(0, 160)}` : '')
                + (marked ? ` · ${markedLabel}` : '');
            const colour = group && (state === 'completed' || state === 'running')
                ? ` style="--grid-colour:${watchGroupColour(group)}"` : '';
            return `<span class="watch-cell is-${state}${marked ? ' is-marked' : ''}"${colour} data-tooltip="${esc(title)}" aria-label="${esc(title)}"></span>`;
        }).join('');
        const groupsRaw = Array.isArray(grid.groups) ? grid.groups : (Array.isArray(grid.workers) ? grid.workers : []);
        const legend = groupsRaw.slice(0, 32).map(g => `<span class="watch-grid-key"><i style="background:${watchGroupColour(g)}"></i>${esc(String(g).slice(0, 120))}</span>`).join('');
        const omitted = Math.max(0, Number(grid.omitted) || 0);
        const inventory = grid.inventory === 'verified' ? ' · verified on host'
            : grid.inventory === 'reported' ? ' · reported (not verified on host)' : '';
        return `<div class="watch-grid-summary">${esc(String(grid.title || 'Progress').slice(0, 60))} · ${esc(String(grid.total || grid.cells.length))} ${esc(unit)}s${inventory}`
            + `</div><div class="watch-grid" role="group" aria-label="${esc(unitTitle)} progress">${cells}</div>`
            + `<div class="watch-grid-legend">${legend}`
            + WATCH_GRID_STATES.filter(state => seen.has(state)).map(state => `<span class="watch-grid-key"><i class="is-${state}"></i>${state}</span>`).join('')
            + (anyMarked ? `<span class="watch-grid-key"><i class="is-marked"></i>${esc(markedLabel)}</span>` : '') + '</div>'
            + (omitted ? `<div class="watch-grid-summary">Showing first 2048 ${esc(unit)}s · ${esc(String(omitted))} more; bars cover the full job.</div>` : '');
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

    function renderRestartProgressHtml(pct, label, esc) {
        const percent = Math.max(0, Math.min(100, Number(pct) || 0));
        return `<div class="cuttle-action-form-progress" data-restart-progress="1">`
            + `<div class="progress-row"><div class="progress-track"><div class="progress-bar" style="width:${percent}%"></div></div>`
            + `<div class="progress-value">${percent}%</div></div>`
            + `<div class="cuttle-action-form-progress-label">${esc(label || 'Restarting Flask…')}</div></div>`;
    }
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

    /** Bar stack HTML from normalized bars (moved from the page, plan C1). */
    function renderWatchBarsHtml(bars, esc, workerColours) {
        return (bars || []).map((b) => {
            const kind = esc(b.kind || 'secondary');
            const pct = Math.max(0, Math.min(100, Number(b.percent || 0)));
            const lab = esc(b.label || '');
            const detail = b.detail ? `<span class="progress-bar-detail">${esc(b.detail)}</span>` : '';
            return (
                `<div class="progress-bar-item progress-bar-item--${kind}" data-bar-id="${esc(b.id || '')}">`
                + `<div class="progress-bar-head">`
                + `<span class="progress-bar-name">${lab}</span>`
                + detail
                + `<span class="progress-value">${pct}%</span>`
                + `</div>`
                + `<div class="progress-row">`
                + `<div class="progress-track"><div class="progress-bar" style="width:${pct}%;${workerColours && b.kind === 'worker' ? 'background:' + watchGroupColour(b.id) : ''}"></div></div>`
                + `</div>`
                + `</div>`
            );
        }).join('');
    }

    /**
     * Card HTML + render planning (moved from the page, plan C1).
     * Pure: same explicit inputs in, same markup out. `parts` carries
     * exactly what the page call sites already compute:
     * {spec, formId, fallback, lockedAttr, selectedAttr,
     *  contentPreviewHtml, esc}.
     * - `contentPreviewHtml` is the nested `formatMessage` preview,
     *   pre-rendered by the page (this owner never calls formatMessage and
     *   never reads page globals; session targeting arrives stamped in
     *   `spec.session_id`). Balanced-JSON extraction and placeholder
     *   sequencing stay in the page.
     * - `esc` is the page's HTML escaper, passed explicitly (pure
     *   string->string, no DOM).
     * DOM/fetch/timers/action execution/widget writes stay outside.
     */
    function renderActionFormCardHtml(parts) {
        const p = parts || {};
        const spec = p.spec;
        const formId = p.formId;
        const fallback = p.fallback;
        const lockedAttr = p.lockedAttr;
        const selectedAttr = p.selectedAttr;
        const esc = p.esc;

        const mode = String((spec && spec.mode) || 'choice').toLowerCase();
        const title = esc((spec && spec.title) || 'Choose an action');
        const desc = esc((spec && spec.description) || '');
        const lock = esc((spec && spec.lock) || 'form');
        const isQaForm = !actionFormHasSideEffect(spec);
        const submitLabel = esc((spec && spec.submitLabel) || (isQaForm ? 'Submit' : 'Run'));
        const cancelLabel = esc((spec && spec.cancelLabel) || 'Cancel');
        const busyLabel = esc((spec && spec.busyLabel) || 'Running…');
        const selectedIds = Array.isArray(spec && spec.selected)
            ? spec.selected.map(String)
            : String(selectedAttr || '')
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean);
        const contentPreview = String(p.contentPreviewHtml || '');
        const watchSpec = inferActionFormWatch(spec);
        const watchSnap = watchSnapshotFromSpec(watchSpec);
        const watchFail = !!(watchSnap && String(watchSnap.state || '') === 'failed');
        const watchDone = !!(watchSpec && watchSpec.terminal && watchSnap && (
            String(watchSnap.state || '') === 'done'
            || String(watchSnap.state || '') === 'failed'
        ));
        // Soft follow-up dismiss ("Ignored") must not permanently kill shared
        // Flask restart controllers — heal on render so a poisoned transcript
        // becomes clickable again after refresh.
        const softDismissToast = /^(ignored|cancell?ed)\b/i.test(
            String((spec && spec.toast) || '').trim()
        );
        const restartSoftDismiss = softDismissToast
            && specLooksLikeFlaskRestart(spec, formId);
        const alreadyLocked = !restartSoftDismiss && !!(
            (spec && spec.locked)
            || lockedAttr === '1'
            || lockedAttr === 'true'
            || watchDone
        );
        // Flask-restart cards share a generation id. After a real restart the
        // chat still has the old unlocked markup — collapse until status sync
        // confirms whether this generation is still live (avoids a few seconds
        // of clickable Graceful/Force on every refresh).
        const pendingRestartSync = !alreadyLocked && specLooksLikeFlaskRestart(spec, formId);
        const controlsDisabled = alreadyLocked || pendingRestartSync;
        const watchPct = watchSnap && watchSnap.percent != null
            ? Math.max(0, Math.min(100, Number(watchSnap.percent)))
            : 8;
        const watchLabel = watchSnap
            ? String(watchSnap.label || '')
            : 'Checking download status…';
        const restartIdFromSpec = String((spec && spec.restartId) || '').trim();
        const restartPending = !!(spec && spec.pending);
        const restartProgressHtml = restartIdFromSpec && alreadyLocked
            ? renderRestartProgressHtml(restartPending ? 18 : 100, spec && spec.toast, esc)
            : '';
        const watchBars = normalizeWatchBars(watchSnap || { percent: watchPct, label: watchLabel });
        const watchHtml = watchSpec
            ? (`<div class="cuttle-action-form-progress" data-watch-progress="1">`
                + `<div class="cuttle-action-form-progress-bars" data-watch-bars>${renderWatchBarsHtml(watchBars, esc, watchSnap && watchSnap.grid)}</div>`
                + `<div data-watch-grid>${renderWatchGridHtml(watchSnap && watchSnap.grid, esc)}</div>`
                + `<div class="cuttle-action-form-progress-meta" data-watch-meta hidden></div>`
                + `<div class="cuttle-action-form-progress-label">${esc(watchLabel)}</div>`
                + `</div>`)
            : '';
        const options = Array.isArray(spec && spec.options) ? spec.options : [];
        const fields = Array.isArray(spec && spec.fields) ? spec.fields : [];
        let body = '';

        if (mode === 'choice') {
            body = `<div class="cuttle-action-form-options" data-mode="choice">` +
                options.map((o) => {
                    const oid = esc(o.id || '');
                    const lab = esc(o.label || o.id || 'Option');
                    // Q&A options omit `action` on purpose — that is NOT cancel.
                    // Only id "cancel", cancel:true, or __dismiss__ are cancel.
                    const isCancel = isExplicitActionFormCancelOption(o);
                    const isSel = selectedIds.indexOf(String(o.id || '')) >= 0;
                    return (
                        `<button type="button" class="cuttle-button${isCancel ? '' : ' cuttle-button--primary'}${isSel ? ' is-selected' : ''}" ` +
                        `data-action-form-option="${oid}" data-action-form-cancel="${isCancel ? '1' : '0'}"` +
                        `${controlsDisabled ? ' disabled' : ''}>${lab}</button>`
                    );
                }).join('') +
                `</div>`;
        } else if (mode === 'multi') {
            body = `<div class="cuttle-action-form-options" data-mode="multi">` +
                options.map((o) => {
                    const oid = esc(o.id || '');
                    const lab = esc(o.label || o.id || 'Option');
                    // The card has its own Cancel button; Q&A options omit `action`.
                    if (isExplicitActionFormCancelOption(o)) return '';
                    if (!o.action && !isQaForm) return '';
                    const isSel = selectedIds.indexOf(String(o.id || '')) >= 0;
                    return (
                        `<label class="check-item cuttle-action-form-check${isSel ? ' is-selected' : ''}">` +
                        `<input type="checkbox" data-action-form-option="${oid}"` +
                        `${isSel ? ' checked' : ''}${controlsDisabled ? ' disabled' : ''}>` +
                        `<span>${lab}</span></label>`
                    );
                }).join('') +
                `<div class="cuttle-action-form-actions">` +
                `<button type="button" class="cuttle-button cuttle-button--primary" data-action-form-submit="1" data-label="${submitLabel}" data-busy-label="${busyLabel}"${controlsDisabled ? ' disabled' : ''}>${submitLabel}</button>` +
                `<button type="button" class="cuttle-button" data-action-form-cancel="1"${controlsDisabled ? ' disabled' : ''}>${cancelLabel}</button>` +
                `</div></div>`;
        } else {
            // form mode
            const fieldHtml = fields.map((f, idx) => {
                const fid = esc(f.id || ('field_' + idx));
                const label = esc(f.label || fid);
                const type = String(f.type || 'text').toLowerCase();
                const req = f.required ? '<span class="req">*</span>' : '';
                const help = f.help ? `<div class="form-help">${esc(f.help)}</div>` : '';
                const dis = controlsDisabled ? ' disabled' : '';
                if (type === 'textarea') {
                    return `<div class="form-field"><label class="form-label">${label}${req}</label>` +
                        `<textarea class="form-textarea" data-field-id="${fid}" ${f.required ? 'required' : ''}${dis}>${esc(f.value ?? '')}</textarea>${help}</div>`;
                }
                if (type === 'select') {
                    const opts = Array.isArray(f.options) ? f.options : [];
                    return `<div class="form-field"><label class="form-label">${label}${req}</label>` +
                        `<select class="form-select" data-field-id="${fid}" ${f.required ? 'required' : ''}${dis}>` +
                        opts.map((o) => {
                            const ov = esc(o.value ?? o.id ?? o);
                            const ol = esc(o.label ?? o.name ?? o.value ?? o);
                            return `<option value="${ov}">${ol}</option>`;
                        }).join('') +
                        `</select>${help}</div>`;
                }
                if (type === 'radio') {
                    const opts = Array.isArray(f.options) ? f.options : [];
                    const group = `af_${fid}`;
                    const current = f.value ?? '';
                    return `<div class="form-field"><div class="form-label">${label}${req}</div>` +
                        `<div class="radio-group" data-radio-group="${fid}">` +
                        opts.map((o) => {
                            const ov = esc(o.value ?? o.id ?? o);
                            const ol = esc(o.label ?? o.name ?? o.value ?? o);
                            const checked = String(current) === String(o.value ?? o.id ?? o) ? ' checked' : '';
                            return `<label class="radio-item"><input type="radio" name="${group}" value="${ov}"${checked}${dis}><span>${ol}</span></label>`;
                        }).join('') +
                        `</div>${help}</div>`;
                }
                if (type === 'checkboxes') {
                    const opts = Array.isArray(f.options) ? f.options : [];
                    const current = (Array.isArray(f.value) ? f.value : []).map(String);
                    return `<div class="form-field"><div class="form-label">${label}${req}</div>` +
                        `<div class="radio-group" data-checkbox-group="${fid}">` +
                        opts.map((o) => {
                            const raw = String(o.value ?? o.id ?? o);
                            const ov = esc(raw);
                            const ol = esc(o.label ?? o.name ?? o.value ?? o);
                            const checked = current.indexOf(raw) >= 0 ? ' checked' : '';
                            return `<label class="check-item"><input type="checkbox" value="${ov}"${checked}${dis}><span>${ol}</span></label>`;
                        }).join('') +
                        `</div>${help}</div>`;
                }
                if (type === 'checkbox' || type === 'toggle') {
                    const checked = !!(f.value === true || f.value === 'true' || f.value === 1 || f.value === '1');
                    const rawLine = String(f.line || f.body || '');
                    const line = esc(rawLine);
                    // Show the exact text that will be posted, so the card is a
                    // real preview instead of a list of summaries.
                    let linePreview = '';
                    if (rawLine.trim()) {
                        linePreview = `<div class="form-line-preview">${line}</div>`;
                    } else if (!isQaForm) {
                        linePreview = `<div class="form-line-preview is-missing">No post text for this item</div>`;
                    }
                    return `<div class="form-field"><label class="check-item">` +
                        `<input type="checkbox" data-field-id="${fid}" data-line="${line}" ${checked ? 'checked' : ''}${dis}>` +
                        `<span>${label}${req ? ' ' + req : ''}</span></label>${linePreview}${help}</div>`;
                }
                return `<div class="form-field"><label class="form-label">${label}${req}</label>` +
                    `<input class="form-input" type="text" data-field-id="${fid}" value="${esc(f.value ?? '')}" ${f.required ? 'required' : ''}${dis}>${help}</div>`;
            }).join('');
            body = `<div class="cuttle-action-form-fields">${fieldHtml}</div>` +
                `<div class="cuttle-action-form-actions">` +
                `<button type="button" class="cuttle-button cuttle-button--primary" data-action-form-submit="1" data-label="${submitLabel}" data-busy-label="${busyLabel}"${controlsDisabled ? ' disabled' : ''}>${submitLabel}</button>` +
                `<button type="button" class="cuttle-button" data-action-form-cancel="1"${controlsDisabled ? ' disabled' : ''}>${cancelLabel}</button>` +
                `</div>`;
        }

        // Q&A cards that resume the agent always offer a free-text custom
        // answer, so the user is never locked into the listed options.
        // Side-effect / watch cards never get one. Opt out per card with
        // "allow_custom": false.
        const customAllowed = isQaForm && !!(spec && spec.resume)
            && (!spec || spec.allow_custom === undefined || spec.allow_custom === null
                || (spec.allow_custom !== false && spec.allowCustom !== false));
        if (customAllowed) {
            body += `<div class="cuttle-action-form-custom">` +
                `<input class="form-input" type="text" data-custom-input="1"` +
                ` placeholder="Or type your own answer…"${controlsDisabled ? ' disabled' : ''}>` +
                `<button type="button" class="cuttle-button" data-custom-submit="1"${controlsDisabled ? ' disabled' : ''}>Send</button>` +
                `</div>`;
        }

        const optionLabelFor = (id) => {
            const hit = options.find((o) => String(o.id || '') === String(id));
            return (hit && (hit.label || hit.id)) || id;
        };
        const usedSummary = pendingRestartSync
            ? 'Checking restart status…'
            : (String((watchSnap && watchSnap.label) || (spec && spec.toast) || '').trim()
                || (selectedIds.length
                    ? 'Used — ' + selectedIds.map(optionLabelFor).join(', ')
                    : 'Used'));
        const statusHtml = alreadyLocked
            ? `<div class="cuttle-action-form-status ${watchFail ? 'is-error' : 'is-ok'}">${esc(usedSummary)}</div>`
            : `<div class="cuttle-action-form-status" hidden></div>`;

        // One-shot cards collapse to this row once used; reusable ones never do.
        // Pending Flask-restart sync also starts collapsed (non-interactive).
        const collapsible = (alreadyLocked || pendingRestartSync) && !(spec && spec.reusable);
        const cancelled = /^cancell?ed/i.test(usedSummary)
            || selectedIds.indexOf('cancel') >= 0;
        const showFailIcon = cancelled || watchFail;
        const summaryPending = (restartPending && !showFailIcon) || pendingRestartSync;
        const summaryHtml =
            `<button type="button" class="cuttle-action-form-summary" data-action-form-toggle="1"` +
            ` aria-expanded="${collapsible ? 'false' : 'true'}">` +
            `<span class="cuttle-action-form-summary-icon" aria-hidden="true">${showFailIcon ? '✕' : (summaryPending ? '' : '✓')}</span>` +
            `<span class="cuttle-action-form-summary-text">${title} — ${esc(usedSummary)}</span>` +
            `<span class="cuttle-action-form-summary-caret" aria-hidden="true">` +
            `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>` +
            `</span></button>`;

        const specAttr = esc(JSON.stringify(spec || {}));
        const cardSession = esc((spec && spec.session_id) || '');
        const restartId = esc((spec && spec.restartId) || '');
        const restartGroup = esc(
            (spec && (spec.restartFormGroup || spec.id)) || formId || ''
        );
        return (
            `<div class="cuttle-action-form${alreadyLocked ? ' cuttle-action-form--locked' : ''}` +
            `${collapsible ? ' is-collapsible is-collapsed' : ''}${cancelled ? ' is-cancelled' : ''}${watchFail ? ' is-failed' : ''}${summaryPending ? ' is-pending' : ''}" ` +
            `data-form-id="${esc(formId || '')}" ` +
            `data-fallback="${esc(fallback || '')}" data-lock="${lock}" ` +
            `data-locked="${alreadyLocked ? '1' : '0'}" ` +
            `data-restart-pending-sync="${pendingRestartSync ? '1' : '0'}" ` +
            `data-session-id="${cardSession}" ` +
            (restartGroup ? `data-restart-form-group="${restartGroup}" ` : '') +
            (restartId ? `data-restart-id="${restartId}" ` : '') +
            `data-spec="${specAttr}">` +
            summaryHtml +
            `<div class="cuttle-action-form-body">` +
            `<div class="cuttle-action-form-title">${title}</div>` +
            (desc ? `<div class="cuttle-action-form-desc">${desc}</div>` : '') +
            contentPreview +
            restartProgressHtml +
            watchHtml +
            body +
            statusHtml +
            `</div></div>`
        );
    }

    const api = {
        renderRestartProgressHtml,
        renderActionFormCardHtml,
        renderWatchBarsHtml,
        renderWatchGridHtml,
        watchGroupColour,
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
