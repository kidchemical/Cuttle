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
    const DEFAULT_FORBIDDEN = ['main', 'master'];
    // The supervisor's job and settings come from the host (api.chat_ops.brief);
    // Focus is optional free text for job-specific notes, under Advanced.
    const INVOLVE_CHOICES = [
        { id: 'blocked', label: 'Only when blocked' },
        { id: 'never', label: 'Never' },
        { id: 'questions', label: 'For questions it cannot answer' },
    ];
    const NUDGE_CHOICES = [
        { id: 'agent', label: 'Supervisor writes the next step' },
        { id: 'template', label: 'Send the next Tasks item (no supervisor turn)' },
    ];
    // Tasks lists shown before searching; the rest are one query away.
    const TASKS_SHOWN = 3;

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

    // Header chips stay short; the popover carries the detail (who, since when, rules).
    function shortState(schedule, now, opts) {
        if (schedule.state === 'paused') return 'Paused';
        return nextLabel(schedule, now, opts) || 'Active';
    }

    function chipText(schedule, now, opts) {
        return 'Supervising · ' + shortState(schedule, now, opts);
    }

    function badgeText(schedule, now, opts) {
        return 'Supervised · ' + shortState(schedule, now, opts);
    }

    // Target states as people read them (api.chat_ops.service.status).
    const STATE_TEXT = {
        busy: 'working', idle: 'idle', idle_with_work: 'idle with open tasks',
        waiting_background: 'waiting on background work', awaiting_input: 'waiting for input',
        error: 'stopped on an error', cancelled: 'stopped',
    };
    function stateText(state) {
        const k = String(state || '');
        return STATE_TEXT[k] || k.replace(/_/g, ' ');
    }

    /** "Tasks milestones, repeated failures (3×)" from a schedule's checkpoints. */
    function wakesText(checkpoints) {
        const c = checkpoints || {};
        const bits = [];
        if (c.milestone) bits.push('Tasks milestones');
        if (c.tests) bits.push(c.tests.when === 'fail' ? 'failed test runs' : c.tests.when === 'any' ? 'test runs' : 'passing test runs');
        if (c.repeat_failures) bits.push('repeated failures (' + (c.repeat_failures.n || 3) + '×)');
        if (c.watched_paths) bits.push('watched files');
        return bits.join(', ');
    }

    function pinText(s) {
        if (!s.agent) return 'this chat’s agent';
        return ['/' + s.agent, s.model, s.effort].filter(Boolean).join(' · ');
    }

    /**
     * The popover's status line and facts, without repeating each other:
     * the header says who, the status line says when, the facts say how.
     */
    function details(s, now, opts, side) {
        const paused = s.state === 'paused';
        const since = fmtWhen(s.paused_at, now, opts);
        const until = fmtWhen(s.paused_until, now, opts);
        const next = nextLabel(s, now, opts);
        const status = paused
            ? (since ? 'Since ' + since : 'Paused') + (until ? ' · resumes ' + until : ' · until you resume it')
            : (next === 'checking…' ? 'Checking now' : (next ? 'Next check ' + next.replace(/^next /, '') : 'Active'))
                + ' · every ' + fmtInterval(s.every);
        const hint = paused
            ? (side === 'badge' ? 'Nothing checks this chat or wakes its supervisor until then.'
                : 'No check-ins or wake-ups until then.')
            : '';
        const facts = [];
        if (side === 'chip') facts.push({ label: 'Check-ins', value: pinText(s) });
        const wakes = wakesText(s.checkpoints);
        if (wakes) facts.push({ label: 'Also wakes on', value: wakes });
        const rules = rulesText(s);
        if (rules) facts.push({ label: 'Branch rules', value: rules });
        const seen = s.last_seen || {};
        if (side === 'chip' && seen.state) {
            const tasks = tasksSummary(seen);
            facts.push({ label: 'Last seen', value: stateText(seen.state) + (tasks ? ' · ' + tasks : '') });
        }
        return { status, hint, facts };
    }

    const OUTCOME_TEXT = {
        woke: 'woke supervisor',
        violation: 'rule broken, woke supervisor',
        error: 'check failed',
        resumed: 'timed pause expired, resumed',
        nudged: 'sent the next Tasks item (no supervisor turn)',
        paused_alert: 'paused too long, user notified',
        undelivered: 'messages not delivered, woke supervisor',
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
            if (d.state) text += ' (' + stateText(d.state) + ')';
        } else {
            text = OUTCOME_TEXT[check.outcome] || String(check.outcome || 'checked');
            if (check.outcome !== 'error' && d.state) text += ' (' + stateText(d.state) + ')';
            if (d.already_waiting) text += ', previous check-in still pending';
        }
        return {
            time: fmtClock(check.at, opts),
            text: text,
            tone: check.outcome === 'violation' || check.outcome === 'error' ? 'warn'
                : check.outcome === 'undelivered' ? 'warn'
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
            title: 'Supervising ' + s.target,
            details: details(s, now, opts, 'chip'),
        }));
        const badges = (data.supervised_by || []).map((s) => ({
            id: s.id,
            paused: s.state === 'paused',
            text: badgeText(s, now, opts),
            pausedText: pausedText(s, now, opts),
            supervisor: s.supervisor,
            next: nextLabel(s, now, opts),
            violations: s.violations || [],
            title: 'Supervised by ' + s.supervisor,
            details: details(s, now, opts, 'badge'),
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
        const parts = Array.isArray(raw) ? raw : String(raw || '').split(',');
        return parts.map((s) => String(s).trim()).filter((s, i, all) => s && all.indexOf(s) === i);
    }

    /**
     * Which Tasks lists the picker shows: with no query, the first ``limit``
     * (the chat's own come first); with a query, every title match. Hidden
     * rows stay in the form, so ``hiddenSelected`` lists are still followed.
     */
    function taskPickerView(tasks, query, limit) {
        const all = Array.isArray(tasks) ? tasks : [];
        const q = String(query || '').trim().toLowerCase();
        const n = Number(limit) > 0 ? Number(limit) : TASKS_SHOWN;
        const shown = q
            ? all.filter((t) => String(t.title || t.id).toLowerCase().indexOf(q) >= 0)
            : all.slice(0, n);
        const hiddenRows = all.filter((t) => shown.indexOf(t) < 0);
        return { ids: shown.map((t) => t.id), hidden: hiddenRows.length,
            hiddenSelected: hiddenRows.filter((t) => t.selected).length, searchable: all.length > n };
    }

    /** Branch picker options: the repo's branches, plus stored names it no longer has. */
    function branchOptions(branches, keep) {
        return splitList([].concat(Array.isArray(branches) ? branches : [], splitList(keep)));
    }

    /** Dialog prefill from /api/chat-ops/sessions/<id>/defaults. */
    // Event wakes ("checkpoints", api.chat_ops.checkpoints). New schedules start
    // with milestones + repeated failures on; edits show what the schedule has.
    const CHECKPOINTS = [
        { id: 'milestone', label: 'A followed Tasks item or list is completed' },
        { id: 'tests', label: 'A test run finishes' },
        { id: 'repeat_failures', label: 'The same command keeps failing' },
        { id: 'watched_paths', label: 'Watched files change (schemas, API contracts…)' },
    ];
    const CHECKPOINT_PRESETS = {
        milestone: { on: true, do: 'wake' },
        tests: { on: false, do: 'wake', when: 'pass' },
        repeat_failures: { on: true, do: 'wake', n: 3 },
        watched_paths: { on: false, do: 'stop,wake', regex: '' },
    };

    function checkpointFields(stored) {
        const out = {};
        CHECKPOINTS.forEach((c) => {
            const preset = CHECKPOINT_PRESETS[c.id];
            if (!stored) { out[c.id] = Object.assign({}, preset); return; }
            const cfg = stored[c.id];
            out[c.id] = cfg ? Object.assign({}, preset, cfg, { on: true, do: (cfg.do || ['wake']).join(',') })
                : Object.assign({}, preset, { on: false });
        });
        return out;
    }

    /** Checkpoints body for the API from dialog fields; errors for bad params. */
    function checkpointsBody(fields) {
        const body = {};
        const errors = [];
        CHECKPOINTS.forEach((c) => {
            const f = (fields || {})[c.id];
            if (!f || !f.on) return;
            const entry = { do: String(f.do || 'wake').split(',').map((x) => x.trim()).filter(Boolean) };
            if (c.id === 'tests') entry.when = f.when || 'pass';
            if (c.id === 'repeat_failures') {
                const n = Number(f.n);
                if (!(n >= 2 && n <= 20)) errors.push('Repeated failures: pick 2–20 attempts.');
                entry.n = n;
            }
            if (c.id === 'watched_paths') {
                const rx = String(f.regex || '').trim();
                if (!rx) errors.push('Watched files: give a path pattern.');
                else {
                    try { new RegExp(rx); } catch (_) { errors.push('Watched files: the pattern is not a valid regex.'); }
                }
                entry.regex = rx;
            }
            body[c.id] = entry;
        });
        return { body, errors };
    }

    /**
     * Dialog fields. ``schedule`` (an overview schedule row) switches it to
     * editing that schedule: its stored settings win over the chat's defaults.
     */
    function dialogDefaults(defaults, schedule) {
        const d = defaults || {};
        const base = {
            target: d.handle || '',
            every: 900,
            instructions: '',
            involveUser: 'blocked',
            nudge: 'agent',
            branches: splitList(d.branches),
            requiredBranch: d.branch || '',
            requiredPath: d.worktree ? (d.execution_path || '') : '',
            // Defaults name only branches the repo has (all of them off git).
            forbidden: DEFAULT_FORBIDDEN.filter((b) => !(d.branches || []).length || d.branches.indexOf(b) >= 0),
            worker: '',
            expert: '',
            // Tasks lists the supervisor follows: the chat's own preselected, project lists optional.
            tasks: (Array.isArray(d.tasks) ? d.tasks : []).filter((t) => t && t.id).map((t) => ({
                id: String(t.id), title: String(t.title || t.id), done: Number(t.done) || 0,
                total: Number(t.total) || 0, scope: t.scope || 'session', selected: !!t.selected,
            })),
            checkpoints: checkpointFields(null),
        };
        const pb = d.playbook;
        if (!schedule && pb) {
            // An orchestrating chat starts from its playbook's supervisor defaults.
            const tiers = pb.model_tiers || {};
            return Object.assign(base, {
                playbookName: String(pb.name || pb.id || ''),
                agent: String(pb.agent || ''),
                every: Number(pb.every) || base.every,
                instructions: pb.instructions || base.instructions,
                involveUser: pb.involve_user || base.involveUser,
                nudge: pb.nudge || base.nudge,
                requiredBranch: pb.required_branch || base.requiredBranch,
                requiredPath: pb.required_path || base.requiredPath,
                forbidden: (pb.forbidden_branches || []).length ? splitList(pb.forbidden_branches) : base.forbidden,
                worker: (tiers.worker || []).join(', '),
                expert: (tiers.expert || []).join(', '),
                checkpoints: checkpointFields(pb.checkpoints || {}),
            });
        }
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
            instructions: s.instructions || '',
            involveUser: s.involve_user || base.involveUser,
            nudge: s.nudge || base.nudge,
            requiredBranch: s.required_branch || '',
            requiredPath: s.required_path || '',
            forbidden: splitList(s.forbidden_branches),
            worker: (tiers.worker || []).join(', '),
            expert: (tiers.expert || []).join(', '),
            tasks: base.tasks.map((t) => Object.assign({}, t, { selected: picked ? picked.indexOf(t.id) >= 0 : t.selected })),
            checkpoints: checkpointFields(s.checkpoints || {}),
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
        if (String(f.instructions || '').trim().length > 2000) errors.push('Focus is limited to 2000 characters.');
        const forbidden = splitList(f.forbidden);
        if (f.requiredBranch && forbidden.indexOf(f.requiredBranch) >= 0) {
            errors.push('The required branch is also in the forbidden list.');
        }
        const points = f.checkpoints ? checkpointsBody(f.checkpoints) : null;
        if (points) errors.push(...points.errors);
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
                involve_user: f.involveUser || 'blocked',
                nudge: f.nudge || 'agent',
                required_branch: String(f.requiredBranch || '').trim(),
                required_path: String(f.requiredPath || '').trim(),
                forbidden_branches: forbidden,
                model_tiers: tiers,
                ...(points ? { checkpoints: points.body } : {}),
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

    // A row Cuttle sent into this chat (chat_ops outbox), not something the
    // user typed: a supervision check-in, a relayed send, or a Cuttle notice.
    const CHAT_OP_KINDS = {
        checkin: { label: 'Supervision check-in', title: 'Sent by Cuttle on the supervision schedule, not typed by you' },
        send: { label: '', title: 'Sent into this chat by another chat, not typed by you' },
        notice: { label: '', title: 'Sent by Cuttle when background work finished, not typed by you' },
    };

    /** {kind, label, title} for a chat_ops row's header badge, or null. */
    function chatOpBadge(opts) {
        if (!opts || String(opts.origin || '') !== 'chat_ops') return null;
        const text = String(opts.parent_label || '').trim();
        let kind = String(opts.chat_op_kind || '');
        if (!Object.prototype.hasOwnProperty.call(CHAT_OP_KINDS, kind)) {
            kind = /^Supervision check-in/i.test(text) ? 'checkin' : (/^Sent by /i.test(text) ? 'send' : 'notice');
        }
        const spec = CHAT_OP_KINDS[kind];
        return { kind, label: spec.label || text || 'Cuttle', title: spec.title };
    }

    /**
     * A supervision check-in row split for display: `head` (its first line, the
     * one-line status) stays visible; `body` (the briefing written for the
     * supervisor agent) folds away collapsed. The stored text is untouched — the
     * agent always gets all of it. Returns null when there is nothing to fold.
     */
    function checkinParts(text) {
        const raw = String(text || '').replace(/\r\n/g, '\n');
        const nl = raw.indexOf('\n');
        if (nl < 0) return null;
        const head = raw.slice(0, nl).trim();
        const body = raw.slice(nl + 1).replace(/^\s+/, '').replace(/\s+$/, '');
        if (!head || !body) return null;
        return { head, body };
    }

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // The check-in's opening sentence (api.chat_ops.worker._STATE_TEXT) in a
    // word or two, with the tone its chip takes.
    const CHECKIN_STATES = [
        [/^working/i, 'working', 'live'],
        [/^waiting on its own background/i, 'waiting on a job', 'live'],
        [/^idle with open/i, 'idle, tasks open', 'warn'],
        [/^idle/i, 'idle', 'muted'],
        [/^waiting on a question/i, 'needs input', 'warn'],
        [/^stopped on an error/i, 'error', 'bad'],
        [/^stopped/i, 'stopped', 'muted'],
    ];

    /**
     * What a check-in says, as facts a card can draw: the supervised chat, its
     * state, Tasks progress and what needs attention. Read from the check-in's
     * own markdown (api.chat_ops.worker.checkin_text); null when it is not one.
     */
    function checkinFacts(text) {
        const raw = String(text || '').replace(/\r\n/g, '\n');
        const m = /Supervision check-in for (CH-\d+)\*{0,2}:\s*([^\n]*)/.exec(raw);
        if (!m) return null;
        const said = m[2].replace(/\.\s*$/, '').trim();
        const known = CHECKIN_STATES.filter((row) => row[0].test(said))[0];
        const section = (title) => {
            const at = raw.indexOf('**' + title);
            if (at < 0) return [];
            const lines = raw.slice(at).split('\n').slice(1);
            const out = [];
            for (const line of lines) {
                if (!/^- /.test(line)) break;
                out.push(line.slice(2));
            }
            return out;
        };
        let done = 0;
        let total = 0;
        section('Tasks:').forEach((line) => {
            const t = /: (\d+)\/(\d+) done\s*$/.exec(line);
            if (t) { done += Number(t[1]); total += Number(t[2]); }
        });
        const stalled = /\*\*No progress for (\d+) min/.exec(raw);
        const undelivered = /\*\*Not delivered \((\d+)\)/.exec(raw);
        const context = /\*\*Context (\d+)% full/.exec(raw);
        const alerts = [];
        const violations = section('Rule violations:').length;
        if (violations) alerts.push({ tone: 'bad', text: violations + (violations === 1 ? ' rule broken' : ' rules broken') });
        if (stalled) alerts.push({ tone: 'warn', text: 'no progress ' + stalled[1] + 'm' });
        if (undelivered) alerts.push({ tone: 'warn', text: undelivered[1] + ' not delivered' });
        if (context) alerts.push({ tone: 'warn', text: 'context ' + context[1] + '%' });
        return {
            handle: m[1], state: known ? known[1] : said, tone: known ? known[2] : 'muted',
            tasks: total ? { done, total, percent: Math.round((done / total) * 100) } : null,
            alerts,
        };
    }

    const EYE_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
        + 'stroke-linejoin="round" aria-hidden="true"><path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/>'
        + '<circle cx="10" cy="10" r="2.2"/></svg>';
    const CARET_ICON = '<svg class="chat-op-caret" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" '
        + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6l4 4-4 4"/></svg>';

    /**
     * Bubble HTML for a check-in: one line of facts (chat, state, alerts, a Tasks
     * bar); the briefing written for the supervisor agent opens from it. `fmtHead`
     * / `fmtBody` are the page's own user/markdown formatters (injected, so this
     * stays pure). null → render the row as usual.
     */
    function checkinHtml(text, fmtHead, fmtBody) {
        const parts = checkinParts(text);
        if (!parts) return null;
        const facts = checkinFacts(text);
        if (!facts) {
            return fmtHead(parts.head)
                + '<details class="chat-checkin-details"><summary>Supervisor briefing</summary>'
                + '<div class="chat-checkin-body">' + fmtBody(parts.body) + '</div></details>';
        }
        const tasks = facts.tasks
            ? '<span class="chat-checkin-tasks" title="Tasks: ' + facts.tasks.done + ' of ' + facts.tasks.total + ' done">'
                + '<span class="chat-checkin-bar"><span style="width:' + facts.tasks.percent + '%"></span></span>'
                + facts.tasks.done + '/' + facts.tasks.total + '</span>'
            : '';
        return '<details class="chat-checkin-details chat-checkin-card">'
            + '<summary class="chat-checkin-row" title="Supervision check-in. Click for the briefing the supervisor got">'
            + '<span class="chat-checkin-ico">' + EYE_ICON + '</span>'
            + '<span class="chat-checkin-target">' + esc(facts.handle) + '</span>'
            + '<span class="chat-checkin-state is-' + facts.tone + '">' + esc(facts.state) + '</span>'
            + facts.alerts.map((a) => '<span class="chat-checkin-alert is-' + a.tone + '">' + esc(a.text) + '</span>').join('')
            + tasks + CARET_ICON + '</summary>'
            + '<div class="chat-checkin-body">' + fmtBody(parts.body) + '</div></details>';
    }

    // A relayed message longer than this folds to a preview.
    const FOLD_CHARS = 280;
    const FOLD_LINES = 4;

    /** The first words of a long relayed message, on one line; null when it is short enough to show whole. */
    function foldPreview(text) {
        const raw = String(text || '').replace(/\r\n/g, '\n').trim();
        if (raw.length <= FOLD_CHARS && raw.split('\n').length <= FOLD_LINES) return null;
        const flat = raw.split(/\n\s*\n/)[0].replace(/\s+/g, ' ').trim();
        return flat.length > 200 ? flat.slice(0, 200).replace(/\s+\S*$/, '') + '…' : flat;
    }

    /** A long send/notice row: a preview line that opens the whole message. */
    function foldHtml(text, fmt) {
        const preview = foldPreview(text);
        if (preview == null) return null;
        return '<details class="chat-op-fold"><summary>'
            + '<span class="chat-op-fold-preview">' + esc(preview) + '</span>'
            + '<span class="chat-op-fold-more">Show all' + CARET_ICON + '</span></summary>'
            + '<div class="chat-op-fold-body">' + fmt(text) + '</div></details>';
    }

    /** A user-row bubble: check-ins show as a fact line, long relayed messages fold, the rest renders as usual. */
    function userBubbleHtml(badge, text, fmtHead, fmtBody) {
        if (badge && badge.kind === 'checkin') return checkinHtml(text, fmtHead, fmtBody) || fmtHead(text);
        if (badge && (badge.kind === 'send' || badge.kind === 'notice')) return foldHtml(text, fmtHead) || fmtHead(text);
        return fmtHead(text);
    }

    const api = {
        chatOpBadge, checkinParts, checkinFacts, checkinHtml, foldPreview, foldHtml, userBubbleHtml, stateText, wakesText,
        SCHEMA_VERSION, INTERVALS, PAUSE_CHOICES, fmtInterval, fmtClock, fmtWhen, nextLabel,
        chipText, badgeText, pausedText, pauseBody,
        historyRow, view, isSupervised, supervisorOf, dialogDefaults, supervisePayload, pinChoices, scheduleById,
        CHECKPOINTS, checkpointFields, checkpointsBody,
        INVOLVE_CHOICES, NUDGE_CHOICES, TASKS_SHOWN, taskPickerView, branchOptions,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatSupervisionModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
