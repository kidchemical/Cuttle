/**
 * Supervision App — pure model (no DOM, no fetch). Controller: supervision_page.js.
 *
 * Defaults are the install-wide starting values of a new supervision
 * (api.chat_ops.defaults). The option lists and time labels are the Supervise
 * dialog's own (CuttleChatSupervisionModel), so both surfaces offer the same choices.
 */
(function (root) {
    'use strict';

    const S = root.CuttleChatSupervisionModel
        || (typeof require === 'function' ? require('../chat/chat_supervision_model.js') : null);

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g,
            (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    /** Form fields from stored defaults; an interval the form cannot show falls back to the built-in. */
    function fields(defaults, builtin) {
        const d = defaults || {};
        const b = builtin || {};
        const every = Number(d.every);
        return {
            every: S.INTERVALS.indexOf(every) >= 0 ? every : (Number(b.every) || 900),
            agent: String(d.agent || ''),
            involve_user: d.involve_user || b.involve_user || 'blocked',
            nudge: d.nudge || b.nudge || 'agent',
            system: S.systemFields(d.system || b.system || null),
            paused_alert_hours: Number.isFinite(Number(d.paused_alert_hours)) ? Number(d.paused_alert_hours) : 6,
        };
    }

    /** PUT body from form fields, or the reasons it cannot be saved. */
    function body(f) {
        const errors = [];
        const every = Number(f.every);
        if (S.INTERVALS.indexOf(every) < 0) errors.push('Pick a check interval.');
        const hours = Number(f.paused_alert_hours);
        if (String(f.paused_alert_hours).trim() === '' || !(hours >= 0 && hours <= 720)) {
            errors.push('Paused alert: 0–720 hours (0 turns it off).');
        }
        return {
            ok: !errors.length, errors,
            body: {
                every, agent: String(f.agent || ''), involve_user: f.involve_user, nudge: f.nudge,
                system: S.systemFields(f.system), paused_alert_hours: hours,
            },
        };
    }

    function same(a, b) { return JSON.stringify(fields(a)) === JSON.stringify(fields(b)); }

    /** What the target was doing at the last check, in a few words. */
    const SEEN = {
        busy: 'working', waiting_background: 'waiting on a background job', idle: 'finished',
        idle_with_work: 'idle with open tasks', awaiting_input: 'waiting for an answer', error: 'stopped on an error',
    };

    /** One row of the live list. */
    function row(s, now, opts) {
        const paused = s.state === 'paused';
        const seen = (s.last_seen || {}).state;
        const care = S.SYSTEM_CARE.filter((c) => (s.system || {})[c.id]).map((c) => c.id);
        const alerts = [];
        if ((s.violations || []).length) alerts.push(s.violations.length === 1 ? '1 rule violation' : s.violations.length + ' rule violations');
        if ((s.last_seen || {}).display_off) alerts.push('display is off');
        if ((s.last_seen || {}).display_unheld) alerts.push('display not kept awake');
        if ((s.last_seen || {}).memory_low) alerts.push('memory low');
        return {
            id: s.id, paused,
            targetId: s.target_id, supervisorId: s.supervisor_id,
            title: s.target_name || s.target,
            handle: s.target,
            by: (s.supervisor_name || s.supervisor) + (s.agent ? ' · ' + s.agent : ''),
            project: s.project_name || '',
            status: paused ? S.pausedText(s, now, opts)
                : [SEEN[seen] ? 'Chat ' + SEEN[seen] : 'First check pending', S.nextLabel(s, now, opts)].filter(Boolean).join(' · '),
            every: 'every ' + S.fmtInterval(s.every),
            care, alerts,
        };
    }

    const CARE_LABELS = { processes: 'Processes', memory: 'Memory', display: 'Display' };

    /** The line above the list about the display inhibitor ('' when nothing wants it). */
    function displayNote(display, rows) {
        const wants = (rows || []).some((r) => !r.paused && r.care.indexOf('display') >= 0);
        if (display && display.held) return { kind: 'ok', text: 'Keeping the display awake while supervised chats work.' };
        if (wants && display && display.error) return { kind: 'bad', text: 'Cannot keep the display awake: ' + display.error + '.' };
        return null;
    }

    const api = { esc, fields, body, same, row, displayNote, CARE_LABELS, SEEN };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleSupervisionApp = api;
})(typeof window !== 'undefined' ? window : globalThis);
