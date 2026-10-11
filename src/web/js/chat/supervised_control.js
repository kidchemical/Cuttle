/**
 * Supervised-task presentation helpers (testable under Node).
 * Loaded before chat_page.js; attaches to window.CuttleSupervised.
 *
 * Presentation model: one coordinator-owned assistant bubble per task.
 * Worker/Cursor is never a separate chat participant by default.
 */
(function (root) {
    'use strict';

    const PHASE_LABELS = {
        coordinating: 'Planning task…',
        delegated: 'Working…',
        worker_running: 'Working…',
        follow_up: 'Working…',
        followup_pending: 'Working…',
        reviewing: 'Reviewing results…',
        awaiting_user: 'Awaiting your input…',
        approved: 'Done',
        cancelled: 'Cancelled',
        failed: 'Failed',
        escalated: 'Escalated',
        budget_exhausted: 'Budget exhausted',
        created: 'Thinking…',
        completed: 'Done',
    };

    const WORKER_PHASES = ['delegated', 'worker_running', 'follow_up', 'followup_pending'];

    const JUMP_LABELS = {
        approved: 'Background task completed',
        escalated: 'Background task escalated',
        failed: 'Background task failed',
        cancelled: 'Background task cancelled',
        budget_exhausted: 'Background task exhausted budget',
        awaiting_user: 'Background task needs your input',
    };

    function phaseLabel(phase) {
        const p = String(phase || '');
        return PHASE_LABELS[p] || p || 'Working…';
    }

    function jumpLabel(phase) {
        const p = String(phase || '');
        return JUMP_LABELS[p] || 'Background task updated';
    }

    function isImmediateControlLaneMessage(text) {
        const raw = String(text || '').trim();
        if (!raw.startsWith('/')) return false;
        const t = raw.replace(
            /^\/(?:cursor|codex|claude|hermes|muse|deepseek|opencode|antigravity)(?:\s+[^\s/]+)?\s+(?=\/(?:coordinate|coordinator|restart|branch)\b)/i,
            ''
        ).trim().toLowerCase();
        if (t === '/restart' || t.startsWith('/restart ')) return true;
        if (t === '/branch' || t.startsWith('/branch ')) return true;
        if (t === '/coordinator' || t.startsWith('/coordinator ')) {
            const rest = t.slice('/coordinator'.length).trim();
            if (!rest) return true;
            const head = rest.split(/\s+/)[0];
            return ['status', 'show', 'mode', 'profile', 'worker', 'review-loops', 'review_loops', 'followups', 'reset'].includes(head);
        }
        if (t === '/coordinate' || t.startsWith('/coordinate ')) {
            const rest = t.slice('/coordinate'.length).trim();
            if (!rest) return true;
            const head = rest.split(/\s+/)[0];
            return ['status', 'show', 'cancel', 'stop', 'abort', 'followup', 'follow-up', 'follow_up'].includes(head);
        }
        return false;
    }

    /** Control-lane must terminate dispatch — never fall through to agent routing. */
    function controlLaneFetchOptions(message, controlRequestId) {
        return {
            stream: false,
            control_request_id: controlRequestId || '',
            message: String(message || ''),
            controlLane: true,
            terminateDispatch: true,
        };
    }

    function shouldPersistControlExchange(serverBody) {
        if (!serverBody || typeof serverBody !== 'object') return true;
        if (serverBody.skip_history_persist || serverBody.reconcile_only) return false;
        if (serverBody.idempotent || serverBody.delivery_handled) return false;
        if (serverBody.history_reconcile_only) return false;
        return true;
    }

    function shouldAppendControlAssistantBubble(serverBody, existingControlIds) {
        if (!serverBody) return false;
        // Canonical supervised bubble is updated in place — never append a second row.
        if (serverBody.update_existing_bubble || serverBody.canonical_bubble) {
            return false;
        }
        if (serverBody.type === 'supervised_started' || serverBody.type === 'supervised_complete') {
            if (serverBody.coordinator_response_message_id) return false;
        }
        const cid = String(serverBody.control_request_id || '');
        if (cid && existingControlIds && existingControlIds.has(cid)) {
            return false;
        }
        if (serverBody.reconcile_only || serverBody.idempotent) {
            return !(existingControlIds && cid && existingControlIds.has(cid));
        }
        return !!(serverBody.response || serverBody.output);
    }

    function newControlRequestId() {
        return (
            'cr_'
            + Date.now().toString(36)
            + '_'
            + Math.random().toString(36).slice(2, 10)
        );
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function activityStateFromTask(task) {
        if (!task || !task.task_id) return null;
        const pending = Number(task.pending_followup_count || 0);
        const phase = String(task.phase || '');
        return {
            task_id: task.task_id,
            phase,
            phase_label: phaseLabel(phase),
            followup_hint: pending > 0 ? 'Follow-up pending' : '',
            terminal: !!task.terminal,
            pending_followup_count: pending,
            followups_used: Number(task.followups_used || 0),
            max_followups: Number(task.max_followups || 0),
            worker_label: task.worker_label || task.worker || 'Worker',
            coordinator_label: task.coordinator_label
                || (task.coordinator && (task.coordinator.label || task.coordinator.id))
                || 'Coordinator',
            router_strategy: task.router_strategy || 'supervised',
            decision_id: task.decision_id || '',
            objective_preview: task.objective_preview || '',
            updated_at: task.updated_at || '',
            created_at: task.created_at || '',
            raw_report_url: task.raw_report_url || '',
            run_id: task.run_id || '',
            verification_mode: task.verification_mode || '',
            timeline: Array.isArray(task.timeline) ? task.timeline : [],
            details: task.details && typeof task.details === 'object' ? task.details : {},
            details_markdown: task.details_markdown
                || (task.details && task.details.markdown)
                || '',
            coordinator_response_message_id: task.coordinator_response_message_id || null,
            parent_user_message_id: task.parent_user_message_id || null,
            bubble_content: task.bubble_content || '',
            events: Array.isArray(task.events) ? task.events : [],
        };
    }

    function formatElapsed(createdAt, updatedAt) {
        try {
            const a = Date.parse(createdAt || '');
            const b = Date.parse(updatedAt || '') || Date.now();
            if (!Number.isFinite(a)) return '';
            const sec = Math.max(0, Math.round((b - a) / 1000));
            if (sec < 60) return sec + 's';
            const m = Math.floor(sec / 60);
            const s = sec % 60;
            return m + 'm ' + s + 's';
        } catch (_) {
            return '';
        }
    }

    function buildActivityDisclosureHtml(state) {
        if (!state) return '';
        const terminal = !!state.terminal;
        const elapsed = formatElapsed(state.created_at, state.updated_at);
        const actions = terminal
            ? [
                { id: 'view_activity', label: 'View activity details' },
                { id: 'copy_details', label: 'Copy details' },
                { id: 'view_report', label: 'View worker report' },
            ]
            : [
                { id: 'status', label: 'Status' },
                { id: 'followup', label: 'Add instruction' },
                { id: 'cancel', label: 'Cancel' },
            ];
        const btns = actions.map(function (a) {
            return (
                '<button type="button" class="supervised-activity-btn" '
                + 'data-supervised-action="' + a.id + '" '
                + 'data-task-id="' + escapeHtml(state.task_id) + '" '
                + (a.id === 'view_report' && state.raw_report_url
                    ? 'data-report-url="' + escapeHtml(state.raw_report_url) + '" '
                    : '')
                + 'aria-label="' + escapeHtml(a.label) + '">'
                + escapeHtml(a.label)
                + '</button>'
            );
        }).join('');
        const timelineSrc = Array.isArray(state.timeline) && state.timeline.length
            ? state.timeline
            : (state.events || []);
        const timeline = timelineSrc.slice(-8).map(function (ev) {
            const t = (ev && (ev.type || ev.event)) || '';
            const at = (ev && (ev.at || ev.ts)) || '';
            return '<li>' + escapeHtml(t) + (at ? ' · ' + escapeHtml(String(at).slice(0, 19)) : '') + '</li>';
        }).join('');
        const details = state.details || {};
        const detailsMd = state.details_markdown
            || (details && details.markdown)
            || '';
        const evidenceItems = (details && Array.isArray(details.evidence_items))
            ? details.evidence_items
            : [];
        const evidenceHtml = evidenceItems.slice(0, 12).map(function (item) {
            const ok = item && item.ok;
            const flag = ok === true ? 'yes' : (ok === false ? 'no' : 'uncertain');
            return (
                '<li><code>' + escapeHtml(item.key || 'item') + '</code>'
                + ' · ' + escapeHtml(item.source || '')
                + ' · ' + escapeHtml(flag)
                + ' — ' + escapeHtml(String(item.detail || '').slice(0, 160))
                + '</li>'
            );
        }).join('');
        const findings = (details && Array.isArray(details.findings)) ? details.findings : [];
        const findingsHtml = findings.slice(0, 8).map(function (f, i) {
            const title = (f && (f.symbol || f.file)) || ('finding ' + (i + 1));
            return (
                '<li>' + escapeHtml(String(title))
                + (f && f.impact ? ': ' + escapeHtml(String(f.impact).slice(0, 120)) : '')
                + '</li>'
            );
        }).join('');
        return (
            '<details class="supervised-activity-disclosure">'
            + '<summary class="supervised-activity-summary">Activity</summary>'
            + '<div class="supervised-activity-body">'
            + '<div class="supervised-activity-meta-row">'
            + '<div><strong>Coordinator</strong>: ' + escapeHtml(state.coordinator_label) + '</div>'
            + '<div><strong>Worker</strong>: ' + escapeHtml(state.worker_label) + '</div>'
            + '<div><strong>Strategy</strong>: ' + escapeHtml(state.router_strategy) + '</div>'
            + '<div><strong>Task</strong>: ' + escapeHtml(state.task_id) + '</div>'
            + '<div><strong>Phase</strong>: ' + escapeHtml(state.phase_label)
            + (elapsed ? ' · ' + escapeHtml(elapsed) : '')
            + '</div>'
            + '<div><strong>Follow-ups</strong>: '
            + state.followups_used + '/' + state.max_followups
            + ' (pending ' + state.pending_followup_count + ')</div>'
            + (state.verification_mode
                ? '<div><strong>Verification</strong>: ' + escapeHtml(state.verification_mode) + '</div>'
                : '')
            + (state.decision_id
                ? '<div><strong>Decision</strong>: ' + escapeHtml(state.decision_id) + '</div>'
                : '')
            + '</div>'
            + (timeline ? '<ul class="supervised-activity-timeline">' + timeline + '</ul>' : '')
            + (findingsHtml
                ? '<div class="supervised-activity-section"><strong>Worker findings</strong>'
                + '<ul class="supervised-activity-findings">' + findingsHtml + '</ul></div>'
                : '')
            + (evidenceHtml
                ? '<div class="supervised-activity-section"><strong>Evidence ledger</strong>'
                + '<ul class="supervised-activity-evidence">' + evidenceHtml + '</ul></div>'
                : '')
            + (detailsMd
                ? '<pre class="supervised-activity-details-md" hidden>'
                + escapeHtml(detailsMd)
                + '</pre>'
                : '')
            + '<div class="supervised-activity-actions">' + btns + '</div>'
            + '<div class="supervised-activity-followup-form" hidden>'
            + '<label class="supervised-activity-followup-label" for="sup-fu-'
            + escapeHtml(state.task_id) + '">Add instruction</label>'
            + '<textarea id="sup-fu-' + escapeHtml(state.task_id)
            + '" class="supervised-activity-followup-input" rows="2" '
            + 'placeholder="Instruction for the worker…"></textarea>'
            + '<div class="supervised-activity-followup-actions">'
            + '<button type="button" class="supervised-activity-btn" data-supervised-action="followup_submit" '
            + 'data-task-id="' + escapeHtml(state.task_id) + '">Submit</button>'
            + '<button type="button" class="supervised-activity-btn" data-supervised-action="followup_cancel" '
            + 'data-task-id="' + escapeHtml(state.task_id) + '">Dismiss</button>'
            + '</div></div>'
            + '</div></details>'
        );
    }

    function activityStatusText(state) {
        if (!state) return 'Working…';
        if (state.status_text) return String(state.status_text);
        if (!state.terminal && WORKER_PHASES.indexOf(String(state.phase || '')) !== -1) {
            return 'Working with a subagent…';
        }
        return state.phase_label || phaseLabel(state.phase);
    }

    /**
     * Same markup a normal in-flight reply uses, so a supervised task animates
     * like any other message instead of printing its status as body text.
     */
    function buildLiveIndicatorHtml(state) {
        if (!state || state.terminal) return '';
        return (
            '<div class="supervised-canonical-status" data-supervised-phase="'
            + escapeHtml(state.phase) + '">'
            + '<div class="typing-indicator" aria-hidden="true">'
            + '<div class="typing-orbit">'
            + '<span class="typing-orbit-ring"></span>'
            + '<span class="typing-orbit-ring typing-orbit-ring--inner"></span>'
            + '<span class="typing-orbit-core"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--1"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--2"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--3"></span>'
            + '<span class="typing-orbit-beam"></span>'
            + '</div>'
            + '<div class="typing-wave"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>'
            + '</div>'
            + '<div class="typing-status" aria-live="polite" role="status">'
            + escapeHtml(activityStatusText(state))
            + '</div>'
            + '</div>'
        );
    }

    function buildWorkingBubbleHtml(state) {
        if (!state) return '';
        return buildLiveIndicatorHtml(state) + buildActivityDisclosureHtml(state);
    }

    function buildRestartFormHtml() {
        const spec = {
            mode: 'choice',
            title: 'Flask restart required',
            description: 'Choose how to restart Flask. These call the native restart controller.',
            lock: 'form',
            silent: true,
            options: [
                { id: 'graceful', label: 'Restart gracefully', action: '__native_restart__', params: { mode: 'graceful' } },
                { id: 'when_idle', label: 'Restart when idle', action: '__native_restart__', params: { mode: 'when-idle' } },
                { id: 'status', label: 'Show active work', action: '__native_restart__', params: { mode: 'status' } },
                { id: 'not_now', label: 'Not now', action: '__dismiss__', params: {} },
            ],
        };
        return '<cuttle_action_form>\n' + JSON.stringify(spec) + '\n</cuttle_action_form>';
    }

    function shouldShowJumpNotification(task, acknowledgedIds) {
        if (!task || !task.terminal) return false;
        const ev = (task.delivery && task.delivery.terminal_event_id)
            || task.supervised_delivery_event_id
            || (task.task_id + ':' + task.phase);
        if (!ev) return false;
        if (acknowledgedIds && acknowledgedIds.has(String(ev))) return false;
        return true;
    }

    function findCanonicalBubbleEl(root, task) {
        if (!root || !task) return null;
        const mid = task.coordinator_response_message_id;
        if (mid != null) {
            const byId = root.querySelector('.message.assistant[data-message-id="' + String(mid) + '"]');
            if (byId) return byId;
        }
        const byTask = root.querySelector(
            '.message.assistant[data-supervised-task-id="' + String(task.task_id).replace(/"/g, '') + '"]'
        );
        return byTask || null;
    }

    function stripCuttleCopyMetadata(text) {
        /**
         * Remove Cuttle-owned transport/metadata / UI chrome from Copy Text.
         * Does not strip arbitrary user/assistant XML or code fences.
         */
        let s = String(text == null ? '' : text);
        // Supervised Activity transport payload (JSON + timeline).
        s = s.replace(
            /<cuttle_supervised_activity\b[^>]*>[\s\S]*?<\/cuttle_supervised_activity>/gi,
            ''
        );
        // Hidden query-trace appendix (rendered separately; not the answer).
        s = s.replace(/<cuttle_trace\b[^>]*>[\s\S]*?<\/cuttle_trace>/gi, '');
        // Action-form cards (JSON specs) — UI only; not part of the answer text.
        s = s.replace(
            /<cuttle_action_form(?:_pending)?\b[^>]*>[\s\S]*?<\/cuttle_action_form(?:_pending)?>/gi,
            ''
        );
        // Confirm: keep the payload body (often the Discord/post text), drop the tag.
        s = s.replace(
            /<cuttle_confirm(?:_pending)?\b[^>]*>([\s\S]*?)<\/cuttle_confirm(?:_pending)?>/gi,
            '$1'
        );
        // One-shot buttons / legacy forms — chrome, not prose.
        s = s.replace(/<cuttle_button\b[^>]*\/>/gi, '');
        s = s.replace(/<cuttle_button\b[^>]*>[\s\S]*?<\/cuttle_button>/gi, '');
        s = s.replace(/<cuttle_form\b[^>]*>[\s\S]*?<\/cuttle_form>/gi, '');
        // Collapse leftover blank lines from stripped blocks.
        s = s.replace(/\n{3,}/g, '\n\n').trim();
        return s;
    }

    /**
     * Supervised activity-card controller (one per chat root).
     *
     * Owns the rendering/coordination decisions for the canonical supervised
     * bubble, the jump notification, the off-screen task indicator, the typed
     * control-card wiring and the control-lane dispatch. Every DOM/transport
     * effect arrives through `host`; the page keeps the call sites and supplies
     * formatMessage / stripLegacyAgentChrome / activateEnhancements / session
     * identity / transport.
     */
    function createController(host) {
        const win = host.win;
        const doc = win.document;
        const toast = typeof host.toast === 'function' ? host.toast : function () {};

        const _jumpAck = new Set(
            (function () {
                try {
                    return JSON.parse(win.sessionStorage.getItem('cuttle_supervised_jump_ack') || '[]');
                } catch (_) {
                    return [];
                }
            })()
        );
        let _bubbleObserver = null;
        let _lastTask = null;
        const _inflightControl = new Set();
        const _inflightMessage = new Set();

        function acknowledgeJump(eventId) {
            const id = String(eventId || '');
            if (!id) return;
            _jumpAck.add(id);
            try {
                win.sessionStorage.setItem(
                    'cuttle_supervised_jump_ack',
                    JSON.stringify(Array.from(_jumpAck).slice(-40))
                );
            } catch (_) {}
        }

        function scrollToBubble(bubble, opts) {
            const highlight = !opts || opts.highlight !== false;
            if (!bubble) return;
            try {
                bubble.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } catch (_) {
                try { bubble.scrollIntoView(); } catch (__) {}
            }
            if (highlight) {
                bubble.classList.add('supervised-bubble-highlight');
                win.setTimeout(() => bubble.classList.remove('supervised-bubble-highlight'), 1600);
            }
        }

        function showJumpNotification(task) {
            if (!task || !task.terminal) return;
            const ev = (task.delivery && task.delivery.terminal_event_id)
                || task.supervised_delivery_event_id
                || (String(task.task_id) + ':' + String(task.phase || ''));
            if (!shouldShowJumpNotification(task, _jumpAck)) return;
            let bar = doc.getElementById('supervisedJumpNotify');
            if (!bar) {
                bar = doc.createElement('div');
                bar.id = 'supervisedJumpNotify';
                bar.className = 'supervised-jump-notify';
                bar.setAttribute('role', 'status');
                bar.setAttribute('aria-live', 'polite');
                doc.body.appendChild(bar);
            }
            const label = jumpLabel(task.phase) || 'Background task completed';
            bar.innerHTML = '';
            const text = doc.createElement('span');
            text.textContent = label + ' · ';
            const btn = doc.createElement('button');
            btn.type = 'button';
            btn.className = 'supervised-jump-notify-btn';
            btn.textContent = 'Jump to result';
            btn.setAttribute('aria-label', label + '. Jump to result');
            btn.addEventListener('click', () => {
                acknowledgeJump(ev);
                const box = doc.getElementById('chatMessages');
                const bubble = findCanonicalBubbleEl(box, task);
                scrollToBubble(bubble);
                bar.hidden = true;
            });
            bar.appendChild(text);
            bar.appendChild(btn);
            bar.hidden = false;
            bar.dataset.eventId = ev;
        }

        function upsertActivityCard(task) {
            if (!task || !task.task_id) return;
            if (
                task.parent_session_id != null
                && host.currentSession() != null
                && String(task.parent_session_id) !== String(host.currentSession())
                && String(task.parent_session_id) !== String(host.toAuthDbSessionId(host.currentSession()))
            ) {
                const bare = String(host.currentSession()).replace(/^db_session_/, '');
                if (String(task.parent_session_id) !== bare) return;
            }
            const state = activityStateFromTask(task);
            if (!state) return;
            const box = doc.getElementById('chatMessages');
            if (!box) return;

            box.querySelectorAll('.message.system.supervised-activity-row').forEach((row) => {
                try { row.remove(); } catch (_) {}
            });

            let bubble = findCanonicalBubbleEl(box, task);
            if (!bubble && task.coordinator_response_message_id) {
                bubble = box.querySelector(
                    `.message[data-message-id="${win.CSS.escape(String(task.coordinator_response_message_id))}"]`
                );
            }
            if (!bubble) return;

            bubble.dataset.supervisedTaskId = String(task.task_id);
            bubble.classList.add('supervised-coordinator-message');
            if (task.coordinator_response_message_id != null) {
                bubble.dataset.messageId = String(task.coordinator_response_message_id);
            }

            const contentEl = bubble.querySelector('.message-content');
            if (contentEl) {
                const nextRaw = String(
                    task.bubble_content
                    || (task.terminal && task.final_response)
                    || bubble.dataset.rawContent
                    || ''
                );
                let html;
                if (nextRaw && /cuttle_supervised_activity|Working…|Planning|Reviewing|Thinking/i.test(nextRaw)) {
                    bubble.dataset.rawContent = nextRaw;
                    html = host.formatMessage(host.stripLegacyAgentChrome(nextRaw));
                } else if (!task.terminal) {
                    html = buildWorkingBubbleHtml(state);
                    bubble.dataset.rawContent = state.phase_label || 'Working…';
                } else {
                    const disclosure = buildActivityDisclosureHtml(state);
                    const body = String(task.final_response || nextRaw || state.phase_label || '');
                    bubble.dataset.rawContent = nextRaw || body;
                    html = host.formatMessage(host.stripLegacyAgentChrome(body)) + disclosure;
                }
                contentEl.innerHTML = html;
                try { host.activateEnhancements(bubble); } catch (_) {}
            }

            const disclosure = bubble.querySelector('.supervised-activity-disclosure, .supervised-activity-card');
            if (disclosure) wireActivityCard(disclosure);

            if (task.terminal) {
                showJumpNotification(task);
            }
            observeCanonicalBubble(bubble, task);
        }

        function wireActivityCard(card) {
            if (!card || card.__wiredSupervised) return;
            card.__wiredSupervised = true;
            card.addEventListener('click', async (ev) => {
                const btn = ev.target && ev.target.closest
                    ? ev.target.closest('[data-supervised-action]')
                    : null;
                if (!btn) return;
                ev.preventDefault();
                const action = btn.getAttribute('data-supervised-action');
                const taskId = btn.getAttribute('data-task-id')
                    || card.getAttribute('data-supervised-task-id')
                    || (card.closest('[data-supervised-task-id]')
                        && card.closest('[data-supervised-task-id]').getAttribute('data-supervised-task-id'));
                if (action === 'followup') {
                    const form = card.querySelector('.supervised-activity-followup-form');
                    if (form) form.hidden = false;
                    const ta = card.querySelector('.supervised-activity-followup-input');
                    if (ta) ta.focus();
                    return;
                }
                if (action === 'followup_cancel') {
                    const form = card.querySelector('.supervised-activity-followup-form');
                    if (form) form.hidden = true;
                    return;
                }
                if (action === 'followup_submit') {
                    const ta = card.querySelector('.supervised-activity-followup-input');
                    const text = (ta && ta.value || '').trim();
                    if (!text) return;
                    btn.disabled = true;
                    try {
                        await dispatchTaskControl(taskId, 'followup', { instruction: text });
                        if (ta) ta.value = '';
                        const form = card.querySelector('.supervised-activity-followup-form');
                        if (form) form.hidden = true;
                    } finally {
                        btn.disabled = false;
                    }
                    return;
                }
                if (action === 'status') {
                    btn.disabled = true;
                    try {
                        await dispatchTaskControl(taskId, 'status');
                    } finally {
                        btn.disabled = false;
                    }
                    return;
                }
                if (action === 'cancel') {
                    btn.disabled = true;
                    try {
                        await dispatchTaskControl(taskId, 'cancel');
                    } finally {
                        btn.disabled = false;
                    }
                    return;
                }
                if (action === 'view_report') {
                    const reportUrl = btn.getAttribute('data-report-url')
                        || (_lastTask && _lastTask.raw_report_url)
                        || '';
                    if (reportUrl) {
                        win.open(reportUrl + (reportUrl.includes('?') ? '&' : '?') + 'format=text', '_blank', 'noopener');
                    } else {
                        toast('No worker report artifact yet.', 'info');
                    }
                    return;
                }
                if (action === 'copy_details') {
                    const pre = card.querySelector('.supervised-activity-details-md');
                    const detailsText = (pre && (pre.textContent || pre.innerText) || '').trim();
                    if (!detailsText) {
                        toast('No activity details to copy.', 'info');
                        return;
                    }
                    const copyFn = host.copyTextToClipboard
                        || (async (t) => {
                            if (win.navigator && win.navigator.clipboard && win.isSecureContext) {
                                await win.navigator.clipboard.writeText(t);
                                return true;
                            }
                            return false;
                        });
                    try {
                        const ok = await copyFn(detailsText);
                        toast(
                            ok ? 'Copied activity details' : 'Could not copy details',
                            ok ? 'success' : 'error'
                        );
                    } catch (_) {
                        toast('Could not copy details', 'error');
                    }
                    return;
                }
                if (action === 'view_activity') {
                    await dispatchTaskControl(taskId, 'status');
                }
            });
        }

        async function dispatchTaskControl(taskId, action, params = {}) {
            const key = `${taskId}:${action}:${params.instruction || ''}`;
            if (_inflightControl.has(key)) return null;
            _inflightControl.add(key);
            try {
                const response = await host.fetch(`/api/supervised/tasks/${encodeURIComponent(taskId)}/control`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, ...params, session_id: host.currentSession() }),
                });
                const body = await response.json();
                if (!response.ok || body.success === false) throw new Error(body.error || `HTTP ${response.status}`);
                if (body.supervised_task) upsertActivityCard(body.supervised_task);
                toast(body.acknowledgement || 'Updated.', 'success');
                return body;
            } catch (err) {
                toast(String(err && err.message || err), 'error');
                return null;
            } finally {
                _inflightControl.delete(key);
            }
        }

        async function dispatchControlMessage(message, opts = {}) {
            const onceKey = opts.onceKey || '';
            if (onceKey && _inflightMessage.has(onceKey)) {
                return;
            }
            if (onceKey) _inflightMessage.add(onceKey);
            try {
                const outbound = String(message || '').trim();
                if (!isImmediateControlLaneMessage(outbound)) return;
                const userTs = Date.now();
                host.addUserMessage(outbound, { timestamp: userTs });
                host.autoScroll();
                await host.processMessage(outbound, { controlLane: true });
            } finally {
                if (onceKey) _inflightMessage.delete(onceKey);
            }
        }

        function observeCanonicalBubble(bubble, task) {
            if (!bubble || typeof win.IntersectionObserver === 'undefined') return;
            if (_bubbleObserver) {
                try { _bubbleObserver.disconnect(); } catch (_) {}
            }
            const el = doc.getElementById('supervisedTaskIndicator');
            if (!el) return;
            _bubbleObserver = new win.IntersectionObserver((entries) => {
                const entry = entries[0];
                const active = task && !task.terminal;
                if (!active) {
                    el.hidden = true;
                    return;
                }
                el.hidden = !!(entry && entry.isIntersecting);
            }, { root: doc.getElementById('chatMessages'), threshold: 0.12 });
            _bubbleObserver.observe(bubble);
        }

        /** Tiny off-screen chip — never a worker card or pending-prompt modal. */
        function updateTaskIndicator(liveStatus, sessionId) {
            const el = doc.getElementById('supervisedTaskIndicator');
            if (!el) return;
            if (
                sessionId != null
                && host.currentSession() != null
                && !host.sessionIdsEqual(sessionId, host.currentSession())
            ) {
                return;
            }
            const task = liveStatus && liveStatus.supervised_task;
            _lastTask = task || null;
            if (task && task.task_id) {
                upsertActivityCard(task);
            }
            if (!task || !task.task_id || task.terminal) {
                el.hidden = true;
                el.classList.remove('is-terminal');
                return;
            }
            const phaseEl = doc.getElementById('supervisedTaskPhase');
            const metaEl = doc.getElementById('supervisedTaskMeta');
            const detailEl = doc.getElementById('supervisedTaskDetail');
            if (phaseEl) phaseEl.textContent = 'Background task working';
            if (metaEl) metaEl.textContent = '';
            if (detailEl) detailEl.textContent = 'Jump to task';
            el.classList.remove('is-terminal');
            el.setAttribute('role', 'button');
            el.setAttribute('tabindex', '0');
            el.setAttribute('aria-label', 'Background task working. Jump to task.');
            el.onclick = () => {
                const box = doc.getElementById('chatMessages');
                const bubble = findCanonicalBubbleEl(box, task);
                scrollToBubble(bubble);
            };
            el.onkeydown = (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    el.click();
                }
            };
            const box = doc.getElementById('chatMessages');
            const bubble = findCanonicalBubbleEl(box, task);
            if (!bubble) {
                el.hidden = false;
            } else {
                observeCanonicalBubble(bubble, task);
            }
        }

        return {
            acknowledgeJump,
            scrollToBubble,
            showJumpNotification,
            upsertActivityCard,
            wireActivityCard,
            dispatchTaskControl,
            dispatchControlMessage,
            observeCanonicalBubble,
            updateTaskIndicator,
        };
    }

    root.CuttleSupervised = {
        PHASE_LABELS,
        JUMP_LABELS,
        WORKER_PHASES,
        phaseLabel,
        activityStatusText,
        buildLiveIndicatorHtml,
        jumpLabel,
        isImmediateControlLaneMessage,
        controlLaneFetchOptions,
        shouldPersistControlExchange,
        shouldAppendControlAssistantBubble,
        newControlRequestId,
        activityStateFromTask,
        formatElapsed,
        buildActivityDisclosureHtml,
        buildWorkingBubbleHtml,
        buildRestartFormHtml,
        shouldShowJumpNotification,
        findCanonicalBubbleEl,
        stripCuttleCopyMetadata,
        createController,
        // Default presentation: no separate worker card.
        showWorkerCardByDefault: false,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = root.CuttleSupervised;
    }
})(typeof window !== 'undefined' ? window : globalThis);
