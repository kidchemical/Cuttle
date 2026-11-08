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
    // Pausing asks how long. A timed pause auto-resumes on the host, so a
    // forgotten pause cannot stop supervision for days (incident 2026-11-03);
    // "Until I resume" stays available but is never the default.
    const PAUSE_CHOICES = [
        { id: '1h', label: '1 hour', hours: 1, primary: true },
        { id: '4h', label: '4 hours', hours: 4 },
        { id: 'tomorrow', label: 'Until tomorrow', tomorrow: true },
        { id: 'indefinite', label: 'Until I resume', indefinite: true },
    ];
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

    /** Clock for today, date + clock for anything older. */
    function fmtWhen(epochSeconds, now, opts) {
        const n = Number(epochSeconds);
        if (!Number.isFinite(n) || n <= 0) return '';
        const clock = fmtClock(n, opts);
        if (Math.abs(Number(now) - n) < 43200) return clock;
        const d = new Date(n * 1000);
        try {
            return d.toLocaleDateString((opts && opts.locale) || undefined,
                { month: 'short', day: 'numeric' }) + ' ' + clock;
        } catch (_) {
            return d.toISOString().slice(5, 10) + ' ' + clock;
        }
    }

    /** "Paused since Nov 3 09:33 · resumes 13:33" / "" when the schedule runs. */
    function pausedText(schedule, now, opts) {
        if (!schedule || schedule.state !== 'paused') return '';
        const since = fmtWhen(schedule.paused_at, now, opts);
        const until = fmtWhen(schedule.paused_until, now, opts);
        return 'Paused' + (since ? ' since ' + since : '')
            + (until ? ' · resumes ' + until : ' · no auto-resume');
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
        if (schedule.state === 'paused') {
            return 'Supervising ' + schedule.target + ' · ' + pausedText(schedule, now, opts);
        }
        const parts = ['Supervising ' + schedule.target, 'every ' + fmtInterval(schedule.every)];
        const next = nextLabel(schedule, now, opts);
        if (next) parts.push(next);
        return parts.join(' · ');
    }

    function badgeText(schedule, now, opts) {
        const base = 'Supervised by ' + schedule.supervisor;
        const paused = pausedText(schedule, now, opts);
        return paused ? base + ' · ' + paused : base;
    }

    const OUTCOME_TEXT = {
        woke: 'woke supervisor',
        violation: 'rule broken, woke supervisor',
        error: 'check failed',
        resumed: 'timed pause expired, resumed',
        paused_alert: 'paused too long, user notified',
    };

    /** One history line: "2:28 skipped, busy" / "2:43 woke supervisor (idle_with_work)". */
    function historyRow(check, opts) {
        const d = (check && check.detail) || {};
        let text;
        if (check.outcome === 'skipped') {
            text = 'skipped, ' + (d.reason === 'busy' ? 'busy'
                : d.reason === 'waiting_background' ? 'waiting on background work' : 'no change');
        } else if (check.outcome === 'stalled') {
            text = 'no progress' + (d.stalled_min ? ' for ' + d.stalled_min + ' min' : '') + ', woke supervisor';
            if (d.state) text += ' (' + d.state + ')';
        } else {
            text = OUTCOME_TEXT[check.outcome] || String(check.outcome || 'checked');
            if (check.outcome !== 'error' && d.state) text += ' (' + d.state + ')';
            if (d.already_waiting) text += ', previous check-in still pending';
        }
        return {
            time: fmtClock(check.at, opts),
            text: text,
            tone: check.outcome === 'violation' || check.outcome === 'error' ? 'warn'
                : check.outcome === 'woke' || check.outcome === 'stalled' ? 'active' : 'muted',
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
            pausedText: pausedText(s, now, opts),
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
            text: badgeText(s, now, opts),
            pausedText: pausedText(s, now, opts),
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

    /** True when another chat supervises this one (paused schedules count). */
    function isSupervised(overview) {
        return !!(overview && Array.isArray(overview.supervised_by) && overview.supervised_by.length);
    }

    /** The chat driving this one ("CH-…") from the first supervised_by schedule, or ''. */
    function supervisorOf(overview) {
        const first = isSupervised(overview) ? overview.supervised_by[0] : null;
        return String((first && first.supervisor) || '');
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
    /**
     * Dialog fields. ``schedule`` (an overview schedule row) switches it to
     * editing that schedule: its stored settings win over the chat's defaults.
     */
    function dialogDefaults(defaults, schedule) {
        const d = defaults || {};
        const base = {
            target: d.handle || '',
            every: 900,
            instructions: DEFAULT_INSTRUCTIONS,
            requiredBranch: d.branch || '',
            requiredPath: d.worktree ? (d.execution_path || '') : '',
            forbidden: DEFAULT_FORBIDDEN,
            worker: '',
            expert: '',
            // Tasks lists the supervisor follows: the chat's own preselected, project lists optional.
            tasks: (Array.isArray(d.tasks) ? d.tasks : []).filter((t) => t && t.id).map((t) => ({
                id: String(t.id), title: String(t.title || t.id), done: Number(t.done) || 0,
                total: Number(t.total) || 0, scope: t.scope || 'session', selected: !!t.selected,
            })),
        };
        if (!schedule) return base;
        const s = schedule;
        const tiers = s.model_tiers || {};
        const picked = Array.isArray(s.task_ids) ? s.task_ids.map(String) : null;
        return Object.assign(base, {
            editId: s.id,
            agent: String(s.agent || ''),
            model: String(s.model || ''),
            effort: String(s.effort || ''),
            every: Number(s.every) || base.every,
            instructions: s.instructions || base.instructions,
            requiredBranch: s.required_branch || '',
            requiredPath: s.required_path || '',
            forbidden: (s.forbidden_branches || []).join(', '),
            worker: (tiers.worker || []).join(', '),
            expert: (tiers.expert || []).join(', '),
            tasks: base.tasks.map((t) => Object.assign({}, t, { selected: picked ? picked.indexOf(t.id) >= 0 : t.selected })),
        });
    }

    /** Raw schedule row (supervising or supervised_by) for an id, from an overview. */
    function scheduleById(overview, id) {
        const o = overview || {};
        return [].concat(o.supervising || [], o.supervised_by || []).find((s) => s && s.id === id) || null;
    }

    /**
     * Model + effort choices for one /api/agents catalog row (same rule as the
     * composer and agent profiles): [] when the harness takes no such pin.
     */
    function pinChoices(row, model) {
        if (!row) return { models: [], efforts: [] };
        const models = row.supports_model_pin === false ? [] : (row.models || []).map((m) => {
            if (m == null) return null;
            if (typeof m !== 'object') return { id: String(m), label: String(m) };
            const id = m.id || m.modelId || m.value;
            return id ? { id: String(id), label: String(m.label || id) } : null;
        }).filter(Boolean);
        let efforts = [];
        if (row.supports_effort !== false) {
            const perModel = row.model_efforts || {};
            efforts = (model && Object.prototype.hasOwnProperty.call(perModel, model) ? perModel[model] : row.efforts) || [];
        }
        return { models, efforts: efforts.map(String) };
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
                model: String(f.model || '').trim(),
                effort: String(f.effort || '').trim(),
                every,
                instructions: String(f.instructions || '').trim(),
                required_branch: String(f.requiredBranch || '').trim(),
                required_path: String(f.requiredPath || '').trim(),
                forbidden_branches: forbidden,
                model_tiers: tiers,
                // Only an offered list makes this an explicit pick; none offered = follow whatever appears.
                ...(Array.isArray(f.tasks) && f.tasks.length
                    ? { task_ids: (f.taskIds || []).map(String).filter((id) => f.tasks.some((t) => t.id === id)) }
                    : {}),
            },
        };
    }

    /** POST body for one pause choice; unknown ids fall back to the timed default. */
    function pauseBody(choiceId, nowMs) {
        const c = PAUSE_CHOICES.filter((x) => x.id === choiceId)[0] || PAUSE_CHOICES[0];
        if (c.indefinite) return { indefinite: true };
        if (c.tomorrow) {
            const d = new Date(Number(nowMs) || Date.now());
            d.setDate(d.getDate() + 1);
            d.setHours(9, 0, 0, 0);
            return { until: Math.round(d.getTime() / 1000) };
        }
        return { hours: c.hours };
    }

    const api = {
        SCHEMA_VERSION, INTERVALS, PAUSE_CHOICES, fmtInterval, fmtClock, fmtWhen, nextLabel,
        chipText, badgeText, pausedText, pauseBody,
        historyRow, view, isSupervised, supervisorOf, dialogDefaults, supervisePayload, pinChoices, scheduleById,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatSupervisionModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
