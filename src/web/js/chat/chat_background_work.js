/* Chat background-work bubble: the open chat still owns work while no turn runs.

   Source: live-status `background` (api.chat_live_extras → api.jobs.work_tree
   .background_summary): detached Claude waits, watch jobs bound to the chat,
   detached-job (unit) watches that will resume it (`kind: 'unit'`, with the
   systemd unit, its SubState and elapsed time; a unit still waiting on its
   systemd timer is `scheduled` with `starts_at`: shown as "scheduled" with its
   start time and a Start now action, never as running), supervision schedules it runs,
   and OS processes its agents left running (detached ones included). Shown pinned at the end of the transcript, never
   while a turn is generating (the typing bubble owns that), and never counted
   as a transcript record (no `.message` class).

   Sub-agents are not background jobs: their transcript cards (bound to the
   turn that spawned them, or the page's live strip) own them. While children
   of this chat are live (live-status `subagents`, any harness), a harness
   background task or detached unit that only runs the `api.subagents` command
   is left out here too, so the same run is never listed twice.

   `plan(background, subagents)` is pure; `create(host)` owns the DOM element,
   its Stop action and keeping it last in #chatMessages.

   Look: one line per item, graphics over prose. A job row shows step pips, a
   count and the step running now; a supervision row shows the chat, its last
   seen state and a ring counting down to the next check (`nextCheck`); a
   queued send shows how long it has waited, amber once stuck (`sendView`).
   Steps, health, hand-off and output open from the row. When nothing is live
   any more the card shrinks to one line (`settled`), like a locked form.
   Supervision schedules name the chat they drive; the card links to it
   ("Open CH-…", and a schedule-only card opens it on click).
   host: { win, currentSession(), sessionIdsEqual(a,b), isBusy(), openChat(bareId) } */
(function(root) {
    'use strict';
    const ID = 'chat-background-work';

    function epoch(v) {
        if (v == null || v === '') return null;
        if (typeof v === 'number' && Number.isFinite(v)) return v;
        const t = Date.parse(String(v));
        return Number.isFinite(t) ? t / 1000 : null;
    }

    function elapsed(sec) {
        if (!(sec >= 0)) return '';
        const m = Math.floor(sec / 60);
        if (m < 1) return Math.floor(sec) + 's';
        if (m < 60) return m + 'm';
        return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
    }

    const AGENT_LABEL = { claude: 'Claude Code', codex: 'Codex', muse: 'Muse Code' };

    function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

    /** "Starts 3:52 PM · in 47m" for a timer-scheduled unit (systemd-run --on-calendar). */
    function startsText(at, now) {
        if (!(at > 0)) return 'Scheduled';
        now = now == null ? Date.now() / 1000 : now;
        const clock = new Date(at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        return 'Starts ' + clock + (at > now ? ' · in ' + elapsed(at - now) : ' · starting…');
    }

    function progressModel() {
        const P = root.CuttleProgress || globalThis.CuttleProgress;
        if (P) return P;
        if (typeof require === 'function') return require('../shared/progress_model.js');
        throw new Error('progress_model.js must load before chat_background_work.js');
    }

    // A harness's own background task that IS a sub-agent run (any agent):
    // the children's transcript cards say more than the command line does.
    const SUBAGENT_CMD = /\bapi\.subagents\b|\bsubagents\s+(spawn|message|wait)\b/;

    function scheduleSummary(s, standalone, single) {
        const states = { waiting_background: 'waiting for background work', idle_with_work: 'idle with open tasks',
            busy: 'working', idle: 'idle', awaiting_input: 'waiting for input', error: 'stopped on an error' };
        const detail = s.status === 'paused' ? 'No check-ins until resumed.' : String(s.detail || '')
            .replace(/every (\d+)m\b/, 'Every $1 min')
            .replace(/last seen (\w+)/, (_, state) => 'Last checked: ' + (states[state] || state.replace(/_/g, ' ')));
        if (standalone && single) return detail;
        const label = s.status === 'paused' ? 'Supervision paused for ' + handle(s.target)
            : s.label || 'Supervising ' + handle(s.target);
        return [label, detail].filter(Boolean).join(' · ');
    }

    const STEP_LABELS = { pending: 'Pending', running: 'In progress', done: 'Done', failed: 'Failed',
        skipped: 'Skipped', cancelled: 'Cancelled', unknown: 'No result reported' };

    /** Pure activity presentation. Silence is evidence of silence, never proof of a hang. */
    function jobView(w, now) {
        now = now == null ? Date.now() / 1000 : now;
        const a = w.activity && w.activity.schema_version === 1 ? w.activity : {};
        const terminal = w.state && w.state !== 'running';
        const scheduled = !terminal && !!w.scheduled;
        const steps = (Array.isArray(a.steps) ? a.steps : []).slice(0, 32).map((s) => ({ ...s,
            state: terminal && s.state === 'running' ? 'unknown' : STEP_LABELS[s.state] ? s.state : 'pending' }));
        const age = a.updated_ts ? Math.max(0, now - a.updated_ts) : null;
        const stale = !terminal && age != null && age > (a.stale_after_sec || 600);
        const overdue = !terminal && a.deadline_ts && now > a.deadline_ts;
        const finished = steps.filter((s) => ['done', 'failed', 'skipped', 'cancelled'].includes(s.state)).length;
        const failed = steps.filter((s) => s.state === 'failed').length;
        const health = terminal ? '' : scheduled ? 'Not started yet. ' + startsText(epoch(w.starts_at), now) + '.'
            : overdue ? 'Expected deadline passed; check recent output.'
            : stale ? 'No update for ' + elapsed(age) + '; the process may still be working.'
            : age != null ? 'Updated ' + elapsed(age) + ' ago'
            : ''; // no progress reported: the row's bar and elapsed time say it is running
        const target = w.resume_chat || 'this chat';
        const handoff = ({ queued: 'Follow-up queued for ', sending: 'Sending follow-up to ',
            verifying: 'Checking follow-up delivery to ', delivered: 'Follow-up delivered to ',
            failed: 'Follow-up delivery failed for ', cancelled: 'Follow-up cancelled for ',
            turn_failed: 'Follow-up ran and reported failure in ',
            retrying: 'Follow-up could not be queued; retrying for ', unknown: 'Follow-up status unavailable for ' })[w.handoff];
        return { steps, terminal, scheduled, health, warning: !scheduled && !!(stale || overdue),
            summary: a.summary || (steps.length ? finished + ' of ' + steps.length + ' steps finished'
                + (failed ? ' · ' + failed + ' failed' : '') : ''),
            handoff: handoff ? handoff + target : target + ' resumes when it ends',
            output: String(a.output || '').slice(-4000), deadline: a.deadline_ts || null };
    }

    /** Whether any sub-agent child of this chat (live-status `subagents`) is still queued or running. */
    function childrenLive(list) {
        return (Array.isArray(list) ? list : []).some((s) => s && typeof s === 'object'
            && (s.outcome === 'running' || s.outcome === 'queued' || (!s.outcome && s.generating)));
    }

    /** Presentation for one summary (+ live sub-agents), or null when there is nothing to show. */
    function plan(bg, subagents) {
        if (!bg || typeof bg !== 'object') return null;
        // Live children are shown by their transcript cards; only the wait or
        // detached job that runs their `api.subagents` command is folded away.
        const folding = childrenLive(subagents);
        const allWaits = bg.waits || [];
        const waits = folding ? allWaits.filter((w) => !SUBAGENT_CMD.test(String((w && w.label) || ''))) : allWaits;
        const watches = (bg.watches || []).filter((w) => !(folding && w && w.subagents));
        const recent = bg.recent || [];
        const schedules = bg.schedules || [];
        const n = Number(bg.process_count) || 0;
        const d = Number(bg.detached_count) || 0;
        if (!waits.length && !watches.length && !recent.length && !schedules.length && !n) return null;
        const working = !!(waits.length || watches.length || n);
        const scheduleOnly = !working && !recent.length;
        const schedulesPaused = schedules.length > 0 && schedules.every((s) => s.status === 'paused');
        const processHeadline = n && !waits.length && !watches.length;
        const processTitle = plural(n, d === n ? 'detached process' : 'process',
            d === n ? 'detached processes' : 'processes') + ' running'
            + (d && d !== n ? ' (' + d + ' detached)' : '');
        // The backend's fallback label is the same count already in the title
        // or metadata. Preserve descriptive labels, suppress this fallback.
        const fallbackProcessLabel = /^\d+ process(?:es)? still running(?: \(\d+ detached\))?$/.test(bg.label || '');
        const meta = [];
        if (n && !processHeadline) meta.push(plural(n, 'process', 'processes') + (d ? ' (' + d + ' detached)' : ''));
        if (waits.length > 1) meta.push(plural(waits.length, 'wait', 'waits'));
        // A detached agent run (its CLI still open on background tasks): the
        // chat looks idle, but a message sent now goes into that run.
        const runAgent = waits.map((w) => AGENT_LABEL[String((w && w.agent) || '').toLowerCase()]).find(Boolean) || '';
        if (runAgent) meta.push('Messages you send now go to this run');
        schedules.forEach((s) => {
            const text = scheduleSummary(s, scheduleOnly, schedules.length === 1);
            if (text) meta.push(text);
        });
        const fallbackScheduleLabel = schedules.some((s) => bg.label === [s.label, s.detail].filter(Boolean).join(' · '));
        // Schedules split by what they are: supervision (a row with the target's
        // last seen state and a countdown to the next check) and queued sends
        // (a row with how long the message has waited).
        const sups = [];
        const sends = [];
        schedules.forEach((s) => {
            if (!s || typeof s !== 'object') return;
            const detail = String(s.detail || '');
            const kind = s.kind || (/^Send\b/.test(String(s.label || '')) ? 'send' : 'supervise');
            if (kind === 'supervise') {
                const every = Number(s.every) || (Number((/every (\d+)m\b/.exec(detail) || [])[1]) || 0) * 60;
                sups.push({ id: String(s.id || ''), target: handle(s.target), paused: s.status === 'paused',
                    seen: String(s.seen || (/last seen (\w+)/.exec(detail) || [])[1] || ''),
                    every: every || null, nextAt: epoch(s.next_at) });
            } else {
                sends.push({ id: String(s.id || ''), target: handle(s.target), text: detail,
                    since: epoch(s.waiting_since) });
            }
        });
        // What is left of `meta` once schedules have rows of their own.
        const notes = [];
        if (n && !processHeadline) notes.push(plural(n, 'process', 'processes') + (d ? ' (' + d + ' detached)' : ''));
        if (waits.length > 1) notes.push(plural(waits.length, 'wait', 'waits'));
        const targets = [];
        schedules.forEach((s) => {
            const t = handle(s && s.target);
            if (/^CH-\d+$/.test(t) && !targets.includes(t)) targets.push(t);
        });
        // Unit watches: a detached systemd job the host resumes the chat after.
        // Its own watch card stops it; the chat Stop path does not reach it.
        const units = watches.filter((w) => w && w.kind === 'unit');
        const bound = watches.filter((w) => !(w && w.kind === 'unit'));
        // Units waiting on their systemd timer have not started: "scheduled", not "running".
        const scheduledUnits = units.filter((w) => w.scheduled);
        const onlyScheduled = scheduledUnits.length > 0 && scheduledUnits.length === units.length
            && !bound.length && !waits.length && !n;
        const starts = onlyScheduled ? [] : waits.map((w) => epoch(w.started_at)).concat([epoch(bg.started_at)])
            .concat(units.filter((w) => !w.scheduled).map((w) => epoch(w.started_at))).filter((t) => t != null);
        // The summary label may name a run folded away above (sub-agent command wait or unit).
        const foldedLabel = (allWaits.length && !waits.length)
            || (bg.watches || []).some((w) => w && !watches.includes(w) && w.label === bg.label);
        // What the status mark shows: something running now, a schedule
        // watching on its own, a paused schedule, or nothing live (results only).
        const state = onlyScheduled ? 'scheduled' : working ? 'working'
            : sups.length ? (schedulesPaused ? 'paused' : 'supervising')
            : schedules.length ? 'queued' : 'idle';
        return {
            state,
            title: runAgent ? runAgent + ' still running' : waits.length ? 'Waiting in background' : bound.length ? 'Job running'
                : units.length ? (onlyScheduled ? (units.length > 1 ? 'Background jobs scheduled' : 'Background job scheduled')
                    : 'Background job running')
                : n ? processTitle
                : recent.length ? 'Background job results'
                : !sups.length && sends.length ? (sends.length > 1 ? 'Messages queued' : 'Message queued')
                : schedulesPaused ? 'Supervision paused' : 'Supervising',
            label: (units.length && !bound.length && !waits.length) || foldedLabel
                || fallbackProcessLabel || fallbackScheduleLabel || scheduleOnly ? '' : (bg.label || ''),
            meta,
            notes,
            hint: runAgent ? 'Messages you send now go to this run' : '',
            sups,
            sends,
            watches: watches.map((w) => {
                // Shared rule (js/shared/progress_model.js): measured → fill + %;
                // not measured (null, or a unit that only knows "running") →
                // indeterminate stripe with the elapsed time, never "0%".
                const isUnit = !!(w && w.kind === 'unit');
                const m = progressModel().measure({ percent: w.percent, state: 'running',
                    measured: isUnit ? false : undefined });
                const row = { label: w.label || w.job_id, percent: m.percent, indeterminate: !m.determinate,
                    started: epoch(w.started_at) };
                if (isUnit) {
                    row.unit = w.unit || '';
                    row.subState = w.sub_state || '';
                    row.job = w;
                    if (w.scheduled) {
                        row.scheduled = true;
                        row.startsAt = epoch(w.starts_at);
                        row.indeterminate = false;
                    }
                }
                return row;
            }),
            recent,
            started: starts.length ? Math.min(...starts) : null,
            working,
            // Waits and bound watches stop through the chat's Stop path; loose
            // processes end through the process owner. Schedules: Jobs/Feed.
            // Sub-agents stop from their own chats (their transcript cards).
            stop: (waits.length || bound.length) ? 'chat' : (n ? 'processes' : null),
            // Timer-scheduled unit watches the user can start early (POST …/start-now).
            startNow: scheduledUnits.map((w) => String(w.job_id || '')).filter(Boolean),
            processCount: n,
            feed: !!bg.feed,
            targets,
        };
    }

    // A send still undelivered after this long is flagged (matches the outbox's
    // STUCK_SEND_SEC, which is when a supervisor is told).
    const STUCK_AFTER_SEC = 15 * 60;

    /** Queued-send row facts at `now`: how long it has waited and whether that is a problem. */
    function sendView(send, now) {
        now = now == null ? Date.now() / 1000 : now;
        const age = send && send.since != null ? Math.max(0, now - send.since) : null;
        return { age, stuck: age != null && age >= STUCK_AFTER_SEC,
            text: age == null ? 'queued' : 'queued ' + elapsed(age) };
    }

    /** Supervision countdown at `now`: fraction of the interval left (0..1) and "9m". */
    function nextCheck(sup, now) {
        now = now == null ? Date.now() / 1000 : now;
        if (!sup || sup.paused || !(sup.nextAt > 0)) return { fraction: null, text: '' };
        const left = sup.nextAt - now;
        if (left <= 0) return { fraction: 0, text: 'checking' };
        return { fraction: sup.every > 0 ? Math.max(0, Math.min(1, left / sup.every)) : null, text: elapsed(left) };
    }

    const SEEN_TEXT = { busy: 'working', waiting_background: 'waiting on a job', idle_with_work: 'idle, tasks open',
        idle: 'idle', awaiting_input: 'needs input', error: 'error', cancelled: 'stopped' };
    // Which last-seen states want a person's eye.
    const SEEN_TONE = { awaiting_input: 'warn', error: 'bad', idle_with_work: 'warn' };

    /**
     * Nothing is live any more (finished jobs only): the card shrinks to one
     * line. `{ text, failed }`, or null while anything runs.
     */
    function settled(p) {
        if (!p || p.state !== 'idle') return null;
        const jobs = p.recent || [];
        const failed = jobs.filter((w) => w.state === 'failed').length;
        const total = jobs.length;
        if (!total) return null;
        const text = total === 1 ? String(jobs[0].label || 'Background job') : plural(total, 'job', 'jobs') + ' finished';
        return { text, failed, total };
    }

    function handle(sid) {
        const s = String(sid == null ? '' : sid).replace(/^db_session_/, '');
        return /^\d+$/.test(s) ? 'CH-' + s.padStart(6, '0') : s;
    }

    function bare(sid) {
        const s = String(sid == null ? '' : sid).replace(/^db_session_/, '');
        const m = s.match(/^CH-0*(\d+)/i);
        return m ? m[1] : s;
    }

    function create(host) {
        const win = host.win || window;
        const doc = win.document;
        let current = null; // { sid, plan, key }
        let observer = null;
        let ticker = 0;
        const expanded = new Set();

        function container() { return doc.getElementById('chatMessages'); }
        function el() { return doc.getElementById(ID); }

        function keepLast() {
            const box = container();
            const node = el();
            if (box && node && box.lastElementChild !== node) box.appendChild(node);
        }

        function remove() {
            const node = el();
            if (node) node.remove();
            current = null;
            if (observer) { observer.disconnect(); observer = null; }
            if (ticker) { win.clearInterval(ticker); ticker = 0; }
        }

        function tick() {
            const node = el();
            if (!node || !current) return;
            const now = Date.now() / 1000;
            const t = node.querySelector('.chat-bg-elapsed');
            if (t && current.plan.started != null) t.textContent = elapsed(now - current.plan.started);
            node.querySelectorAll('.chat-bg-watch-elapsed').forEach((span) => {
                const at = Number(span.getAttribute('data-started'));
                if (Number.isFinite(at)) span.textContent = elapsed(now - at);
            });
            node.querySelectorAll('.chat-bg-watch-starts').forEach((span) => {
                span.textContent = startsText(Number(span.getAttribute('data-starts')), now);
            });
            node.querySelectorAll('.chat-bg-age[data-since]').forEach((span) => {
                const v = sendView({ since: Number(span.getAttribute('data-since')) }, now);
                span.textContent = v.text;
                span.classList.toggle('is-warn', v.stuck);
                const row = span.closest('.chat-bg-send');
                if (row) { row.classList.toggle('is-warn', v.stuck); row.classList.toggle('is-muted', !v.stuck); }
            });
            node.querySelectorAll('.chat-bg-next').forEach((span) => {
                const v = nextCheck({ nextAt: Number(span.getAttribute('data-next')),
                    every: Number(span.getAttribute('data-every')) || 0 }, now);
                const arc = span.querySelector('.chat-bg-next-arc');
                const C = 2 * Math.PI * 7;
                if (arc) {
                    arc.style.strokeDasharray = String(C);
                    arc.style.strokeDashoffset = String(C * (v.fraction == null ? 0 : v.fraction));
                }
                const text = span.querySelector('.chat-bg-next-text');
                if (text) text.textContent = v.text;
                span.title = v.text === 'checking' ? 'Checking now' : 'Next check in ' + v.text;
            });
            node.querySelectorAll('.chat-bg-job-health').forEach((span) => {
                const view = jobView(span.__job, now);
                span.textContent = view.health;
                span.classList.toggle('is-warning', view.warning);
            });
        }

        async function stop(btn) {
            if (!current) return;
            const sid = current.sid;
            const p = current.plan;
            if (p.stop === 'processes' && !win.confirm('End ' + plural(p.processCount, 'process', 'processes')
                + ' started by ' + handle(sid) + '?')) return;
            btn.disabled = true;
            btn.textContent = 'Stopping…';
            try {
                const url = p.stop === 'chat' ? '/api/chat-cancel'
                    : '/api/jobs/chats/' + encodeURIComponent(bare(sid)) + '/end-processes';
                const body = p.stop === 'chat' ? { session_id: bare(sid), cancel_jobs: true } : {};
                const r = await win.fetch(url, { method: 'POST', credentials: 'include',
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                const data = await r.json().catch(() => ({}));
                if (!r.ok || data.success === false) throw new Error(data.error || 'Could not stop');
                btn.textContent = 'Stopped';
            } catch (error) {
                btn.disabled = false;
                btn.textContent = 'Stop';
                btn.title = error.message;
            }
        }

        async function startNow(btn) {
            if (!current) return;
            const ids = current.plan.startNow || [];
            btn.disabled = true;
            btn.textContent = 'Starting…';
            try {
                for (const id of ids) {
                    const r = await win.fetch('/api/jobs/unit-watch/' + encodeURIComponent(id) + '/start-now',
                        { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}' });
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok || data.success === false) throw new Error(data.error || 'Could not start');
                }
                btn.textContent = 'Started';
            } catch (error) {
                btn.disabled = false;
                btn.textContent = 'Start now';
                btn.title = error.message;
            }
        }

        function openTarget(h) {
            const id = bare(h);
            if (!id || typeof host.openChat !== 'function') return;
            host.openChat(id);
        }

        function onCardClick(ev) {
            const t = ev.target;
            const btn = t && t.closest && t.closest('.chat-bg-open');
            if (btn) {
                ev.preventDefault();
                openTarget(btn.getAttribute('data-chat-handle'));
                return;
            }
            if (t && t.closest && t.closest('a, button, details, summary')) return;
            const p = current && current.plan;
            if (p && !p.working && !p.stop && p.targets.length === 1) openTarget(p.targets[0]);
        }

        // Line icons (20×20, stroke = currentColor). Static markup only.
        const ICONS = {
            job: '<path d="M4.5 6.5l3.5 3.5-3.5 3.5M10.5 14h5"/>',
            wait: '<circle cx="10" cy="10" r="6.5"/><path d="M10 6.5V10l2.4 1.6"/>',
            check: '<path d="M5 10.5l3.2 3.2L15 7"/>',
            cross: '<path d="M6 6l8 8M14 6l-8 8"/>',
            send: '<path d="M4 10h11M11 6l4 4-4 4"/>',
            eye: '<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.2"/>',
            pause: '<path d="M7.5 5.5v9M12.5 5.5v9"/>',
            feed: '<path d="M9 5H5v10h10v-4M12 5h3v3M15 5l-6 6"/>',
            chevron: '<path d="M8 6l4 4-4 4"/>',
            alert: '<path d="M10 4.5l6 10.5H4z"/><path d="M10 9v2.6M10 13.4v.1"/>',
            resume: '<path d="M15 6v4H6.5M9.5 13l-3-3 3-3"/>',
            unknown: '<circle cx="10" cy="10" r="6.5"/><path d="M8.2 8.2a1.9 1.9 0 113 1.5c-.7.5-1.2.9-1.2 1.7M10 13.6v.1"/>',
        };

        function render(sid, p) {
            let node = el();
            const box = container();
            if (!box) return;
            // Read the actual disclosure state: native toggle events may still be
            // queued when the next poll arrives immediately after a click.
            if (node) node.querySelectorAll('details[data-job-key]').forEach((d) => {
                const key = d.getAttribute('data-job-key');
                if (d.open) expanded.add(key); else expanded.delete(key);
            });
            if (!node) {
                node = doc.createElement('div');
                node.id = ID;
                node.className = 'chat-bg-work';
                node.setAttribute('role', 'status');
                node.setAttribute('aria-live', 'polite');
            }
            const now = Date.now() / 1000;
            const mk = (tag, cls, text) => {
                const n = doc.createElement(tag);
                n.className = cls;
                if (text != null) n.textContent = text;
                return n;
            };
            const icon = (name, cls) => {
                const span = mk('span', 'chat-bg-ico' + (cls ? ' ' + cls : ''));
                span.setAttribute('aria-hidden', 'true');
                span.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" '
                    + 'stroke-linecap="round" stroke-linejoin="round">' + ICONS[name] + '</svg>';
                return span;
            };
            const done = settled(p);
            const cardKey = sid + '/card';
            const open = !done || expanded.has(cardKey);
            node.classList.toggle('is-scheduled', !p.working);
            node.classList.toggle('is-settled', !!done);
            node.classList.toggle('is-open', !!done && open);
            node.classList.toggle('has-failed', !!(done && done.failed));
            // Schedule-only card with one target: the whole card opens it.
            const opensOnClick = !p.working && !p.stop && p.targets.length === 1 && !p.recent.length;
            node.classList.toggle('is-clickable', opensOnClick);
            node.title = opensOnClick ? 'Open ' + p.targets[0] : '';
            node.setAttribute('data-state', p.state);

            // ── header: status mark · title · elapsed · chips · actions ──
            const head = mk(done ? 'button' : 'div', 'chat-bg-head');
            // Status mark (CSS draws it per data-state): a pinging dot while work
            // runs, a slower green ping while supervision watches, an amber pause
            // glyph when paused, a still ring when nothing is live.
            const ring = mk('span', 'chat-bg-ring');
            ring.setAttribute('aria-hidden', 'true');
            ring.append(mk('span', 'chat-bg-ring-core'));
            if (done) {
                head.type = 'button';
                head.setAttribute('aria-expanded', String(open));
                head.setAttribute('data-bg-toggle', '1');
                head.title = open ? 'Hide details' : 'Show details';
                head.append(icon(!done.failed ? 'check' : done.failed === done.total ? 'cross' : 'alert', 'chat-bg-settled-mark'));
                head.append(mk('span', 'chat-bg-title', done.text));
                head.append(mk('span', 'chat-bg-chip' + (done.failed ? ' is-bad' : ' is-ok'),
                    done.failed ? done.failed + ' failed' : 'done'));
                head.append(icon('chevron', 'chat-bg-caret'));
                head.addEventListener('click', () => {
                    if (expanded.has(cardKey)) expanded.delete(cardKey); else expanded.add(cardKey);
                    render(sid, p);
                });
            } else {
                head.append(ring);
                const title = mk('span', 'chat-bg-title', p.title);
                head.append(title);
                if (p.started != null) head.append(mk('span', 'chat-bg-elapsed', elapsed(now - p.started)));
                (p.notes || []).forEach((text) => head.append(mk('span', 'chat-bg-chip chat-bg-meta', text)));
            }
            const actions = mk('span', 'chat-bg-actions');
            if (p.feed && !done) {
                const a = mk('a', 'chat-bg-iconbtn chat-bg-details');
                a.href = '/agent_feed.html?chat=' + encodeURIComponent(handle(sid));
                a.title = 'Open the Running tree in Agent Feed';
                a.setAttribute('aria-label', 'Agent Feed');
                a.append(icon('feed'));
                actions.append(a);
            }
            if (p.startNow && p.startNow.length) {
                const b = mk('button', 'chat-bg-start-now', 'Start now');
                b.type = 'button';
                b.title = 'Start the scheduled job now instead of waiting for its timer';
                b.addEventListener('click', () => startNow(b));
                actions.append(b);
            }
            if (p.stop) {
                const b = mk('button', 'chat-bg-stop', 'Stop');
                b.type = 'button';
                b.setAttribute('aria-label', p.stop === 'chat' ? 'Stop background work' : 'End background processes');
                b.addEventListener('click', () => stop(b));
                actions.append(b);
            }
            if (actions.childNodes.length) head.append(actions);

            // ── rows: one line each; anything longer lives behind the row ──
            const rows = mk('div', 'chat-bg-rows');
            if (p.label) rows.append(mk('div', 'chat-bg-label', p.label));
            if (p.hint) rows.append(mk('div', 'chat-bg-hint', p.hint));

            function timeline(view) {
                const list = mk('ol', 'chat-bg-steps');
                view.steps.forEach((s) => {
                    const li = mk('li', 'chat-bg-step is-' + s.state);
                    const mark = mk('span', 'chat-bg-step-icon', ({ done: '✓', failed: '×', skipped: '−', cancelled: '−', unknown: '?' })[s.state] || '');
                    mark.setAttribute('aria-label', STEP_LABELS[s.state]);
                    const text = mk('span', 'chat-bg-step-text', s.label);
                    if (s.detail) text.append(mk('small', 'chat-bg-step-detail', s.detail));
                    li.append(mark, text);
                    list.append(li);
                });
                return list;
            }
            /** Step pips: one segment per step, coloured by state; a count beside them. */
            function pips(view) {
                const wrap = mk('span', 'chat-bg-pips');
                const finished = view.steps.filter((s) => ['done', 'failed', 'skipped', 'cancelled'].includes(s.state)).length;
                wrap.setAttribute('role', 'img');
                wrap.setAttribute('aria-label', finished + ' of ' + view.steps.length + ' steps finished');
                view.steps.forEach((s) => {
                    const pip = mk('span', 'chat-bg-pip is-' + s.state);
                    pip.title = s.label + ' · ' + STEP_LABELS[s.state];
                    wrap.append(pip);
                });
                return [wrap, mk('span', 'chat-bg-count', finished + '/' + view.steps.length)];
            }
            /** Everything a job row folds away: steps, health, hand-off, unit, links, output. */
            function jobPanel(w, completed) {
                const v = jobView(w);
                const block = mk('div', 'chat-bg-job');
                if (v.summary) block.append(mk('div', 'chat-bg-job-summary', v.summary));
                if (v.steps.length) block.append(timeline(v));
                if (!completed && v.health) {
                    const health = mk('div', 'chat-bg-job-health', v.health);
                    health.__job = w;
                    health.classList.toggle('is-warning', v.warning);
                    block.append(health);
                }
                block.append(mk('div', 'chat-bg-handoff', v.handoff));
                const facts = mk('div', 'chat-bg-facts');
                if (completed && w.finished_at) facts.append(mk('span', '', 'Ended ' + new Date(w.finished_at * 1000).toLocaleString()));
                if (v.deadline) facts.append(mk('span', '', 'Deadline ' + new Date(v.deadline * 1000).toLocaleString()));
                if (w.unit) facts.append(mk('span', 'chat-bg-watch-unit', w.unit + (w.sub_state ? ' · ' + w.sub_state : '')));
                function queryLink(id, label) {
                    if (!/^[\w-]{1,80}$/.test(id || '')) return;
                    const a = mk('a', 'chat-bg-details', label);
                    a.href = '/query_log.html?id=' + encodeURIComponent(id);
                    facts.append(a);
                }
                queryLink(w.origin_query_id, 'Started by turn');
                queryLink(w.followup_query_id, 'Follow-up turn');
                if (facts.childNodes.length) block.append(facts);
                // `resume_note` is the agent's own instruction for its follow-up turn: not shown here.
                if (v.output) block.append(mk('pre', 'chat-bg-output', v.output));
                return block;
            }
            /** A job row that opens its panel: <details> keeps it keyboard-reachable. */
            function jobRow(w, job, completed) {
                const details = mk('details', 'chat-bg-job-details');
                const key = sid + '/' + (job.job_id || job.unit);
                details.setAttribute('data-job-key', key);
                details.open = expanded.has(key);
                details.addEventListener('toggle', () => {
                    if (!details.isConnected) return;
                    if (details.open) expanded.add(key); else expanded.delete(key);
                });
                const v = jobView(job);
                const tone = completed ? (job.state === 'done' ? 'ok' : job.state === 'failed' ? 'bad' : 'muted')
                    : v.warning ? 'warn' : 'live';
                const line = mk('summary', 'chat-bg-row chat-bg-watch is-' + tone);
                line.append(icon(completed ? (job.state === 'done' ? 'check' : job.state === 'failed' ? 'cross' : 'unknown') : 'job'));
                line.append(mk('span', 'chat-bg-row-label chat-bg-watch-label', w.label || job.label || job.job_id));
                const viz = mk('span', 'chat-bg-row-viz');
                if (!completed && v.steps.length) {
                    viz.append(...pips(v));
                    const current = v.steps.find((s) => s.state === 'running');
                    if (current) viz.append(mk('span', 'chat-bg-now', current.label));
                } else if (!completed && w.scheduled) {
                    const span = mk('span', 'chat-bg-watch-starts', startsText(w.startsAt));
                    span.setAttribute('data-starts', String(w.startsAt || ''));
                    viz.append(span);
                } else if (!completed) {
                    const bar = mk('span', 'chat-bg-bar');
                    bar.setAttribute('data-progress', 'indeterminate');
                    bar.setAttribute('role', 'progressbar');
                    bar.setAttribute('aria-valuetext', 'In progress');
                    bar.append(mk('span', 'chat-bg-fill'));
                    viz.append(bar);
                }
                line.append(viz);
                const aside = mk('span', 'chat-bg-row-aside');
                if (!completed && v.warning) {
                    const warn = mk('span', 'chat-bg-chip is-warn', v.deadline && now > v.deadline ? 'overdue' : 'quiet');
                    warn.title = v.health;
                    aside.append(warn);
                }
                if (completed) {
                    const state = ({ done: 'done', failed: 'failed', unknown: 'unknown', gave_up: 'watch expired' })[job.state] || 'ended';
                    aside.append(mk('span', 'chat-bg-chip is-' + tone, state));
                    if (job.handoff && job.handoff !== 'on_exit') {
                        const failedHandoff = job.handoff === 'failed' || job.handoff === 'turn_failed';
                        const h = icon('resume', 'chat-bg-handoff-mark' + (failedHandoff ? ' is-bad' : job.handoff === 'delivered' ? ' is-ok' : ''));
                        h.title = v.handoff;
                        aside.append(h);
                    }
                } else if (!w.scheduled && w.started != null && p.watches.length > 1) {
                    // A lone job's elapsed time is already in the header.
                    const span = mk('span', 'chat-bg-watch-elapsed', elapsed(now - w.started));
                    span.setAttribute('data-started', String(w.started));
                    aside.append(span);
                }
                aside.append(icon('chevron', 'chat-bg-caret'));
                line.append(aside);
                if (job.unit != null) {
                    line.title = (completed ? '' : 'Detached job: this chat resumes when it exits. ') + 'Click for details';
                }
                details.append(line, jobPanel(job, completed));
                return details;
            }

            p.watches.forEach((w) => {
                if (w.unit != null) { rows.append(jobRow(w, w.job || w, false)); return; }
                // A chat-bound watch job: label, measured bar (or stripe) and percent.
                const row = mk('div', 'chat-bg-row chat-bg-watch is-live');
                row.append(icon('job'), mk('span', 'chat-bg-row-label chat-bg-watch-label', w.label));
                const viz = mk('span', 'chat-bg-row-viz');
                const bar = mk('span', 'chat-bg-bar');
                const fill = mk('span', 'chat-bg-fill');
                bar.append(fill);
                viz.append(bar);
                const aside = mk('span', 'chat-bg-row-aside');
                if (w.indeterminate) {
                    // Nothing measured: animated stripe (shared CSS), elapsed instead of "0%".
                    bar.setAttribute('data-progress', 'indeterminate');
                    bar.setAttribute('role', 'progressbar');
                    bar.setAttribute('aria-valuetext', 'In progress');
                    if (w.started != null) {
                        const span = mk('span', 'chat-bg-watch-elapsed', elapsed(now - w.started));
                        span.setAttribute('data-started', String(w.started));
                        aside.append(span);
                    }
                } else {
                    fill.style.width = w.percent + '%';
                    aside.append(mk('span', 'chat-bg-pct', w.percent + '%'));
                }
                row.append(viz, aside);
                rows.append(row);
            });
            p.recent.forEach((w) => rows.append(jobRow({ label: w.label }, w, true)));
            const chatChip = (target, title) => {
                const b = mk('button', 'chat-bg-open', target);
                b.type = 'button';
                b.setAttribute('data-chat-handle', target);
                b.title = title;
                return b;
            };
            // Queued sends: who it is for, the message in a few words, how long it has waited.
            (p.sends || []).forEach((send) => {
                const v = sendView(send, now);
                const row = mk('div', 'chat-bg-row chat-bg-send' + (v.stuck ? ' is-warn' : ' is-muted'));
                row.append(icon('send'), chatChip(send.target, 'Open ' + send.target));
                const text = mk('span', 'chat-bg-row-label', send.text);
                text.title = send.text;
                row.append(text);
                const aside = mk('span', 'chat-bg-row-aside');
                const age = mk('span', 'chat-bg-chip chat-bg-age' + (v.stuck ? ' is-warn' : ''), v.text);
                if (send.since != null) age.setAttribute('data-since', String(send.since));
                age.title = v.stuck ? 'Not delivered yet: ' + send.target + ' is still in a turn. It is sent when that turn ends.'
                    : 'Sent when ' + send.target + ' finishes its turn';
                aside.append(age);
                row.append(aside);
                rows.append(row);
            });
            // Supervision: the chat it watches, what it last saw, and a ring counting down to the next check.
            // A lone schedule with nothing else to show sits in the header: one line in all.
            const lone = !done && (p.sups || []).length === 1 && !rows.childNodes.length;
            (p.sups || []).forEach((sup) => {
                const tone = sup.paused ? 'warn' : SEEN_TONE[sup.seen] || 'ok';
                const row = lone ? head : mk('div', 'chat-bg-row chat-bg-sup is-' + tone);
                if (lone) head.classList.add('chat-bg-sup', 'is-' + tone);
                else row.append(icon(sup.paused ? 'pause' : 'eye'));
                const chip = chatChip(sup.target, 'Open the supervised chat ' + sup.target);
                const seen = sup.paused ? (lone ? '' : 'paused') : SEEN_TEXT[sup.seen] || String(sup.seen || '').replace(/_/g, ' ');
                const label = mk('span', 'chat-bg-row-label chat-bg-seen', seen);
                label.title = sup.paused ? 'No check-ins until resumed' : seen ? 'Last check saw it ' + seen : 'Not checked yet';
                const aside = mk('span', 'chat-bg-row-aside');
                if (lone) head.insertBefore(aside, actions.parentNode === head ? actions : null);
                row.insertBefore(chip, lone ? aside : null);
                row.insertBefore(label, lone ? aside : null);
                if (!sup.paused && sup.nextAt) {
                    const next = mk('span', 'chat-bg-next');
                    next.setAttribute('data-next', String(sup.nextAt));
                    next.setAttribute('data-every', String(sup.every || ''));
                    next.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle class="chat-bg-next-track" cx="10" cy="10" r="7"/>'
                        + '<circle class="chat-bg-next-arc" cx="10" cy="10" r="7" transform="rotate(-90 10 10)"/></svg>';
                    next.append(mk('span', 'chat-bg-next-text'));
                    aside.append(next);
                } else if (!sup.paused && sup.every) {
                    aside.append(mk('span', 'chat-bg-chip', 'every ' + elapsed(sup.every)));
                }
                if (!lone) { row.append(aside); rows.append(row); }
            });

            const parts = [head];
            if (open && rows.childNodes.length) parts.push(rows);
            node.replaceChildren(...parts);
            if (node.parentNode !== box) box.appendChild(node);
            keepLast();
            tick();
            if (!node.__bgOpenBound) {
                node.__bgOpenBound = true;
                node.addEventListener('click', onCardClick);
            }
            if (!observer && win.MutationObserver) {
                observer = new win.MutationObserver(keepLast);
                observer.observe(box, { childList: true });
            }
            if (!ticker) ticker = win.setInterval(tick, 15000);
        }

        /** Apply one live-status payload for `sessionId` (the open chat only). */
        function sync(live, sessionId) {
            if (!live || typeof live !== 'object') return; // a missed poll is not "idle"
            const open = host.currentSession();
            if (open == null || (sessionId != null && !host.sessionIdsEqual(sessionId, open))) {
                if (open == null) remove();
                return;
            }
            const busy = !!(live && (live.generating || live.active)) || !!(host.isBusy && host.isBusy());
            const p = busy ? null : plan(live && live.background, live && live.subagents);
            if (!p) { remove(); return; }
            const key = JSON.stringify(p);
            const node = el();
            if (current && current.key === key && node && node.isConnected && current.sid === String(open)) {
                keepLast();
                return;
            }
            current = { sid: String(open), plan: p, key };
            render(String(open), p);
        }

        return { sync, remove };
    }

    root.CuttleChatBackgroundWork = { ID, plan, jobView, elapsed, startsText, sendView, nextCheck, settled,
        SEEN_TEXT, STUCK_AFTER_SEC, create };
})(typeof window === 'undefined' ? globalThis : window);
