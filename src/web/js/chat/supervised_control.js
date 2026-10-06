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
            /^\/(?:cursor|codex|claude|hermes|muse|deepseek|opencode|antigravity)(?:\s+[^\s/]+)?\s+(?=\/(?:coordinate|coordinator|restart)\b)/i,
            ''
        ).trim().toLowerCase();
        if (t === '/restart' || t.startsWith('/restart ')) return true;
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

    // Back-compat alias used by older tests.
    function activityCardStateFromTask(task) {
        return activityStateFromTask(task);
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

    /**
     * Legacy card builder — kept for tests; default UI no longer mounts a
     * separate worker participant card.
     */
    function buildActivityCardHtml(state) {
        if (!state) return '';
        // Quiet disclosure only (not a Cursor participant card).
        return (
            '<div class="supervised-activity-card is-inline" '
            + 'data-supervised-task-id="' + escapeHtml(state.task_id) + '" '
            + 'role="group" aria-label="Task activity">'
            + buildActivityDisclosureHtml(state)
            + '</div>'
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
        activityCardStateFromTask,
        formatElapsed,
        buildActivityDisclosureHtml,
        buildActivityCardHtml,
        buildWorkingBubbleHtml,
        buildRestartFormHtml,
        shouldShowJumpNotification,
        findCanonicalBubbleEl,
        stripCuttleCopyMetadata,
        // Default presentation: no separate worker card.
        showWorkerCardByDefault: false,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = root.CuttleSupervised;
    }
})(typeof window !== 'undefined' ? window : globalThis);
