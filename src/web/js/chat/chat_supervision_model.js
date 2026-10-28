/* ================================================================
   Cuttle Chat — supervision view-model (chat_supervision_model.js).
   Owner: how a chat's supervision state reads in the UI. Turns the
   /api/chat-ops/sessions/<id> overview into chip, badge, history and
   queued-message display rows, and builds the "Supervise this chat…"
   request. Pure: no document, no window, no fetch, no clock reads
   (callers pass `now` in seconds). Server owner: api.chat_ops.
   Experimental: `chat_supervision`. Loaded before chat_supervision.js.
   ================================================================ */
(function (root) {
    'use strict';

    const SCHEMA_VERSION = 1;
    const INTERVALS = [300, 600, 900, 1800, 3600];
    const DEFAULT_FORBIDDEN = 'main, master';
    const DEFAULT_INSTRUCTIONS = 'Keep this chat working through its Tasks list until every item is done. '
        + 'When it stalls, waits on a finished job, or stops after a failure, send it a concrete next step. '
        + 'Never merge or push to protected branches. The user is not involved.';

    function fmtInterval(seconds) {
        const s = Math.max(0, Math.round(Number(seconds) || 0));
        if (s % 3600 === 0 && s >= 3600) return (s / 3600) + 'h';
        if (s >= 60) return Math.round(s / 60) + 'm';
        return s + 's';
    }

    function fmtClock(epochSeconds, opts) {
        const n = Number(epochSeconds);
        if (!Number.isFinite(n) || n <= 0) return '';
        const d = new Date(n * 1000);
        try {
            return d.toLocaleTimeString((opts && opts.locale) || undefined, { hour: 'numeric', minute: '2-digit' });
        } catch (_) {
            return d.toISOString().slice(11, 16);
        }
    }

    /** "next 2:28", "checking…" when due, "paused". */
    function nextLabel(schedule, now, opts) {
        if (!schedule) return '';
        if (schedule.state === 'paused') return 'paused';
        if (schedule.state === 'dispatching') return 'checking…';
        const due = Number(schedule.next_check);
        if (Number.isFinite(due) && due <= now) return 'checking…';
        const clock = fmtClock(due, opts);
        return clock ? 'next ' + clock : '';
    }

    function chipText(schedule, now, opts) {
        const parts = ['Supervising ' + schedule.target, 'every ' + fmtInterval(schedule.every)];
        const next = nextLabel(schedule, now, opts);
        if (next) parts.push(next);
        return parts.join(' · ');
    }

    function badgeText(schedule) {
        return 'Supervised by ' + schedule.supervisor;
    }

    const OUTCOME_TEXT = {
        woke: 'woke supervisor',
        violation: 'rule broken, woke supervisor',
        error: 'check failed',
    };

    /** One history line: "2:28 skipped, busy" / "2:43 woke supervisor (idle_with_work)". */
    function historyRow(check, opts) {
        const d = (check && check.detail) || {};
        let text;
        if (check.outcome === 'skipped') {
            text = 'skipped, ' + (d.reason === 'busy' ? 'busy' : 'no change');
        } else {
            text = OUTCOME_TEXT[check.outcome] || String(check.outcome || 'checked');
            if (check.outcome !== 'error' && d.state) text += ' (' + d.state + ')';
            if (d.already_waiting) text += ', previous check-in still pending';
        }
        return {
            time: fmtClock(check.at, opts),
            text: text,
            tone: check.outcome === 'violation' || check.outcome === 'error' ? 'warn'
                : check.outcome === 'woke' ? 'active' : 'muted',
            violations: Array.isArray(d.violations) ? d.violations : [],
        };
    }

    function tasksSummary(seen) {
        const tasks = (seen && seen.tasks) || [];
        if (!tasks.length) return '';
        let done = 0;
        let total = 0;
        tasks.forEach((t) => { done += Number(t.done) || 0; total += Number(t.total) || 0; });
        return total ? done + '/' + total + ' tasks done' : '';
    }

    /**
     * Everything the page shows for one chat.
     * Returns { chips: [...], badges: [...], queued: [...], warn: bool, empty: bool }.
     */
    function view(overview, now, opts) {
        const data = overview || {};
        const chips = (data.supervising || []).map((s) => ({
            id: s.id,
            paused: s.state === 'paused',
            text: chipText(s, now, opts),
            target: s.target,
            agent: s.agent || '',
            rules: rulesText(s),
            lastSeen: (s.last_seen && s.last_seen.state) || '',
            tasks: tasksSummary(s.last_seen),
            violations: s.violations || [],
            history: (s.history || []).map((c) => historyRow(c, opts)),
        }));
        const badges = (data.supervised_by || []).map((s) => ({
            id: s.id,
            paused: s.state === 'paused',
            text: badgeText(s),
            supervisor: s.supervisor,
            next: nextLabel(s, now, opts),
            violations: s.violations || [],
        }));
        const queued = (data.queued || []).map((q) => ({
            id: q.id,
            from: q.from,
            text: String(q.text || ''),
            preview: String(q.text || '').replace(/\s+/g, ' ').slice(0, 140),
        }));
        const warn = chips.some((c) => c.violations.length) || badges.some((b) => b.violations.length);
        return { chips, badges, queued, warn, empty: !chips.length && !badges.length && !queued.length };
    }

    function rulesText(s) {
        const bits = [];
        if (s.required_branch) bits.push('stay on ' + s.required_branch);
        if ((s.forbidden_branches || []).length) bits.push('never ' + s.forbidden_branches.join(', '));
        return bits.join(' · ');
    }

    function splitList(raw) {
        return String(raw || '').split(',').map((s) => s.trim()).filter(Boolean);
    }

    /** Dialog prefill from /api/chat-ops/sessions/<id>/defaults. */
    function dialogDefaults(defaults) {
        const d = defaults || {};
        return {
            target: d.handle || '',
            every: 900,
            instructions: DEFAULT_INSTRUCTIONS,
            requiredBranch: d.branch || '',
            requiredPath: d.worktree ? (d.execution_path || '') : '',
            forbidden: DEFAULT_FORBIDDEN,
            worker: '',
            expert: '',
        };
    }

    /** Validate dialog fields and build the POST /api/chat-ops/supervise body. */
    function supervisePayload(fields) {
        const f = fields || {};
        const every = Number(f.every);
        const errors = [];
        if (!f.target) errors.push('No chat to supervise.');
        if (!f.agent) errors.push('Pick an agent for the supervisor.');
        if (INTERVALS.indexOf(every) < 0) errors.push('Pick a check interval.');
        if (!String(f.instructions || '').trim()) errors.push('Give the supervisor instructions.');
        const forbidden = splitList(f.forbidden);
        if (f.requiredBranch && forbidden.indexOf(f.requiredBranch) >= 0) {
            errors.push('The required branch is also in the forbidden list.');
        }
        const tiers = {};
        if (splitList(f.worker).length) tiers.worker = splitList(f.worker);
        if (splitList(f.expert).length) tiers.expert = splitList(f.expert);
        return {
            ok: !errors.length,
            errors,
            body: {
                schema_version: SCHEMA_VERSION,
                target: f.target,
                agent: f.agent,
                every,
                instructions: String(f.instructions || '').trim(),
                required_branch: String(f.requiredBranch || '').trim(),
                required_path: String(f.requiredPath || '').trim(),
                forbidden_branches: forbidden,
                model_tiers: tiers,
            },
        };
    }

    const api = {
        SCHEMA_VERSION, INTERVALS, fmtInterval, fmtClock, nextLabel, chipText, badgeText,
        historyRow, view, dialogDefaults, supervisePayload,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatSupervisionModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
