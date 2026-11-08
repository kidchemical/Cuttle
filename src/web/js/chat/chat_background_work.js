/* Chat background-work bubble: the open chat still owns work while no turn runs.

   Source: live-status `background` (api.chat_live_extras → api.jobs.work_tree
   .background_summary): detached Claude waits, watch jobs bound to the chat,
   detached-job (unit) watches that will resume it (`kind: 'unit'`, with the
   systemd unit, its SubState and elapsed time), supervision schedules it runs,
   and OS processes its agents left running (detached ones included). Shown pinned at the end of the transcript, never
   while a turn is generating (the typing bubble owns that), and never counted
   as a transcript record (no `.message` class).

   Live sub-agent children (live-status `subagents`, any harness) show as
   fleet-style rows; the harness's own background task that runs the
   `api.subagents` command is folded into them.

   `plan(background, subagents)` is pure; `create(host)` owns the DOM element,
   its Stop action and keeping it last in #chatMessages.
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

    function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

    function progressModel() {
        const P = root.CuttleProgress || globalThis.CuttleProgress;
        if (P) return P;
        if (typeof require === 'function') return require('../shared/progress_model.js');
        throw new Error('progress_model.js must load before chat_background_work.js');
    }

    const AGENT_OUTCOMES = {
        queued: 'Queued', running: 'Running', done: 'Done', failed: 'Failed',
        cancelled: 'Cancelled', lost: 'Lost', unknown: 'Unknown',
    };
    // A harness's own background task that IS a sub-agent run (any agent):
    // its child rows say more than the command line does.
    const SUBAGENT_CMD = /\bapi\.subagents\b|\bsubagents\s+(spawn|message|wait)\b/;

    const STEP_LABELS = { pending: 'Pending', running: 'In progress', done: 'Done', failed: 'Failed',
        skipped: 'Skipped', cancelled: 'Cancelled', unknown: 'No result reported' };

    /** Pure activity presentation. Silence is evidence of silence, never proof of a hang. */
    function jobView(w, now) {
        now = now == null ? Date.now() / 1000 : now;
        const a = w.activity && w.activity.schema_version === 1 ? w.activity : {};
        const terminal = w.state && w.state !== 'running';
        const steps = (Array.isArray(a.steps) ? a.steps : []).slice(0, 32).map((s) => ({ ...s,
            state: terminal && s.state === 'running' ? 'unknown' : STEP_LABELS[s.state] ? s.state : 'pending' }));
        const age = a.updated_ts ? Math.max(0, now - a.updated_ts) : null;
        const stale = !terminal && age != null && age > (a.stale_after_sec || 600);
        const overdue = !terminal && a.deadline_ts && now > a.deadline_ts;
        const finished = steps.filter((s) => ['done', 'failed', 'skipped', 'cancelled'].includes(s.state)).length;
        const failed = steps.filter((s) => s.state === 'failed').length;
        const health = terminal ? '' : overdue ? 'Expected deadline passed; check recent output.'
            : stale ? 'No update for ' + elapsed(age) + '; the process may still be working.'
            : age != null ? 'Updated ' + elapsed(age) + ' ago'
            : 'Step progress unavailable; only process status is known.';
        const target = w.resume_chat || 'this chat';
        const handoff = ({ queued: 'Follow-up queued for ', sending: 'Sending follow-up to ',
            verifying: 'Checking follow-up delivery to ', delivered: 'Follow-up delivered to ',
            failed: 'Follow-up delivery failed for ', cancelled: 'Follow-up cancelled for ',
            turn_failed: 'Follow-up ran and reported failure in ',
            retrying: 'Follow-up could not be queued; retrying for ', unknown: 'Follow-up status unavailable for ' })[w.handoff];
        return { steps, terminal, health, warning: !!(stale || overdue),
            summary: a.summary || (steps.length ? finished + ' of ' + steps.length + ' steps finished'
                + (failed ? ' · ' + failed + ' failed' : '') : ''),
            handoff: handoff ? handoff + target : 'When finished, an agent will resume ' + target + ' to review the result.',
            output: String(a.output || '').slice(-4000), deadline: a.deadline_ts || null };
    }

    /** Live sub-agent children of this chat (live-status `subagents`) as rows. */
    function agentRows(list) {
        return (Array.isArray(list) ? list : []).filter((s) => s && typeof s === 'object').map((s) => {
            const sid = s.session_id != null ? String(s.session_id) : '';
            const h = String(s.handle || '') || (sid ? handle(sid) : '');
            const outcome = AGENT_OUTCOMES[s.outcome] ? s.outcome : (s.generating ? 'running' : 'unknown');
            const name = String(s.display_name || s.label || s.title || h);
            const avatar = String(s.avatar || '').trim();
            return {
                handle: h, name, outcome, outcomeLabel: AGENT_OUTCOMES[outcome],
                status: String(s.summary || (outcome === 'running' ? 'Working…' : '')).slice(0, 160),
                agent: String(s.agent || ''),
                avatar: avatar && avatar.toLowerCase() !== 'cuttle' && avatar.length <= 4 ? avatar : '',
                initial: (name.replace(/[^\p{L}\p{N}]/gu, '')[0] || '?').toUpperCase(),
            };
        }).filter((r) => /^CH-\d+$/.test(r.handle));
    }

    /** Presentation for one summary (+ live sub-agents), or null when there is nothing to show. */
    function plan(bg, subagents) {
        const agents = agentRows(subagents);
        if ((!bg || typeof bg !== 'object') && !agents.length) return null;
        bg = bg && typeof bg === 'object' ? bg : {};
        const allWaits = bg.waits || [];
        // A background task running `python -m api.subagents spawn/message --wait`
        // is shown by its children's rows, not as a bare command wait.
        const waits = agents.length ? allWaits.filter((w) => !SUBAGENT_CMD.test(String((w && w.label) || ''))) : allWaits;
        const watches = bg.watches || [];
        const recent = bg.recent || [];
        const schedules = bg.schedules || [];
        const n = Number(bg.process_count) || 0;
        const d = Number(bg.detached_count) || 0;
        if (!waits.length && !watches.length && !recent.length && !schedules.length && !n && !agents.length) return null;
        const agentsLive = agents.some((a) => a.outcome === 'running' || a.outcome === 'queued');
        const working = !!(waits.length || watches.length || n || agentsLive);
        const meta = [];
        if (n) meta.push(plural(n, 'process', 'processes') + (d ? ' (' + d + ' detached)' : ''));
        if (waits.length > 1) meta.push(plural(waits.length, 'wait', 'waits'));
        schedules.forEach((s) => meta.push([s.label, s.detail].filter(Boolean).join(' · ')));
        const targets = [];
        schedules.forEach((s) => {
            const t = handle(s && s.target);
            if (/^CH-\d+$/.test(t) && !targets.includes(t)) targets.push(t);
        });
        // Unit watches: a detached systemd job the host resumes the chat after.
        // Its own watch card stops it; the chat Stop path does not reach it.
        const units = watches.filter((w) => w && w.kind === 'unit');
        const bound = watches.filter((w) => !(w && w.kind === 'unit'));
        const starts = waits.map((w) => epoch(w.started_at)).concat([epoch(bg.started_at)])
            .concat(units.map((w) => epoch(w.started_at))).filter((t) => t != null);
        const subagentWaitsOnly = allWaits.length && !waits.length;
        return {
            title: waits.length ? 'Waiting in background' : bound.length ? 'Job running'
                : units.length ? 'Background job running'
                : agents.length ? (agentsLive ? 'Sub-agents working' : 'Sub-agents')
                : n ? (d ? 'Detached processes running' : 'Processes still running')
                : recent.length ? 'Background job results' : 'Supervising',
            label: (units.length && !bound.length && !waits.length) || subagentWaitsOnly ? '' : (bg.label || ''),
            agents,
            meta,
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
                }
                return row;
            }),
            recent,
            started: starts.length ? Math.min(...starts) : null,
            working,
            // Waits and bound watches stop through the chat's Stop path; loose
            // processes end through the process owner. Schedules: Jobs/Feed.
            // Sub-agents stop from their own chats (a row opens it).
            stop: (allWaits.length || bound.length) ? 'chat' : (n ? 'processes' : null),
            processCount: n,
            feed: !!bg.feed,
            targets,
        };
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

        function openTarget(h) {
            const id = bare(h);
            if (!id || typeof host.openChat !== 'function') return;
            host.openChat(id);
        }

        function onCardClick(ev) {
            const t = ev.target;
            const btn = t && t.closest && t.closest('.chat-bg-open, .chat-bg-agent');
            if (btn) {
                ev.preventDefault();
                openTarget(btn.getAttribute('data-chat-handle'));
                return;
            }
            if (t && t.closest && t.closest('a, button, details')) return;
            const p = current && current.plan;
            if (p && !p.working && !p.stop && p.targets.length === 1) openTarget(p.targets[0]);
        }

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
            node.classList.toggle('is-scheduled', !p.working);
            node.classList.toggle('has-jobs', p.watches.some((w) => w.unit != null) || p.recent.length > 0);
            // Schedule-only card with one target: the whole card opens it.
            const opensOnClick = !p.working && !p.stop && p.targets.length === 1;
            node.classList.toggle('is-clickable', opensOnClick);
            node.title = opensOnClick ? 'Open ' + p.targets[0] : '';
            const mk = (tag, cls, text) => {
                const n = doc.createElement(tag);
                n.className = cls;
                if (text != null) n.textContent = text;
                return n;
            };
            const ring = mk('span', 'chat-bg-ring');
            ring.setAttribute('aria-hidden', 'true');
            const body = mk('div', 'chat-bg-body');
            const title = mk('div', 'chat-bg-title', p.title);
            if (p.started != null) title.append(mk('span', 'chat-bg-elapsed', elapsed(Date.now() / 1000 - p.started)));
            body.append(title);
            if (p.label) body.append(mk('div', 'chat-bg-label', p.label));
            function timeline(view) {
                const list = mk('ol', 'chat-bg-steps');
                view.steps.forEach((s) => {
                    const li = mk('li', 'chat-bg-step is-' + s.state);
                    const icon = mk('span', 'chat-bg-step-icon', ({ done: '✓', failed: '×', skipped: '−', cancelled: '−', unknown: '?' })[s.state] || '');
                    icon.setAttribute('aria-label', STEP_LABELS[s.state]);
                    const text = mk('span', 'chat-bg-step-text', s.label);
                    if (s.detail) text.append(mk('small', 'chat-bg-step-detail', s.detail));
                    li.append(icon, text);
                    list.append(li);
                });
                return list;
            }
            function jobInfo(w, parent, completed) {
                const v = jobView(w);
                const block = mk('div', 'chat-bg-job');
                if (v.summary) block.append(mk('div', 'chat-bg-job-summary', v.summary));
                if (!completed) {
                    block.append(mk('div', 'chat-bg-job-owner', 'Job running independently · no agent active'));
                    if (v.steps.length) block.append(timeline(v));
                    const health = mk('div', 'chat-bg-job-health', v.health);
                    health.__job = w;
                    health.classList.toggle('is-warning', v.warning);
                    block.append(health);
                }
                block.append(mk('div', 'chat-bg-handoff', v.handoff));
                const details = mk('details', 'chat-bg-job-details');
                const key = sid + '/' + (w.job_id || w.unit);
                details.setAttribute('data-job-key', key);
                details.open = expanded.has(key);
                details.addEventListener('toggle', () => {
                    if (!details.isConnected) return;
                    if (details.open) expanded.add(key); else expanded.delete(key);
                });
                details.append(mk('summary', '', 'Details'));
                if (completed && v.steps.length) details.append(timeline(v));
                if (completed && w.finished_at) details.append(mk('div', '', 'Ended: ' + new Date(w.finished_at * 1000).toLocaleString()));
                if (v.deadline) details.append(mk('div', '', 'Expected deadline: ' + new Date(v.deadline * 1000).toLocaleString()));
                if (w.unit) details.append(mk('div', '', 'Process: ' + w.unit + (w.sub_state ? ' · ' + w.sub_state : '')));
                if (w.resume_note) details.append(mk('div', 'chat-bg-resume-note', w.resume_note));
                details.append(mk('div', '', 'Completion follow-ups are independent of supervision schedules.'));
                if (w.handoff === 'delivered') details.append(mk('div', '', 'The agent turn was accepted; its reply may still be pending.'));
                function queryLink(id, label) {
                    if (!/^[\w-]{1,80}$/.test(id || '')) return;
                    const a = mk('a', 'chat-bg-details', label);
                    a.href = '/query_log.html?id=' + encodeURIComponent(id);
                    details.append(a);
                }
                queryLink(w.origin_query_id, 'View originating turn');
                queryLink(w.followup_query_id, 'View follow-up turn');
                if (v.output) details.append(mk('pre', 'chat-bg-output', v.output));
                else details.append(mk('div', '', 'No recent output published by this job.'));
                block.append(details);
                parent.append(block);
            }
            p.watches.forEach((w) => {
                const row = mk('div', 'chat-bg-watch');
                row.append(mk('span', 'chat-bg-watch-label', w.label));
                if (w.unit != null) {
                    row.title = 'Detached job' + (w.unit ? ' (systemd unit ' + w.unit + ')' : '')
                        + ': this chat resumes when it exits';
                }
                const bar = mk('span', 'chat-bg-bar');
                const fill = mk('span', 'chat-bg-fill');
                bar.append(fill);
                const hasSteps = w.job && jobView(w.job).steps.length;
                if (w.indeterminate) {
                    // Nothing measured: animated stripe (shared CSS), elapsed instead of "0%".
                    bar.setAttribute('data-progress', 'indeterminate');
                    bar.setAttribute('role', 'progressbar');
                    bar.setAttribute('aria-valuetext', 'In progress');
                    if (!hasSteps) row.append(bar);
                    if (w.started != null && !(w.unit != null && p.watches.length === 1)) {
                        const span = mk('span', 'chat-bg-watch-elapsed', elapsed(Date.now() / 1000 - w.started));
                        span.setAttribute('data-started', String(w.started));
                        row.append(span);
                    }
                } else {
                    fill.style.width = w.percent + '%';
                    row.append(bar, mk('span', 'chat-bg-pct', w.percent + '%'));
                }
                body.append(row);
                if (w.unit != null) jobInfo(w.job || w, body, false);
            });
            p.recent.forEach((w) => {
                const result = mk('div', 'chat-bg-result');
                result.append(mk('div', 'chat-bg-result-title', w.label + ' · '
                    + ({ done: 'Finished', failed: 'Failed', unknown: 'Status unknown', gave_up: 'Watch expired' }[w.state] || 'Ended')));
                jobInfo(w, result, true);
                body.append(result);
            });
            if (p.agents.length) {
                // Fleet-style rows: avatar, name, live status line, outcome. A row opens the child.
                const list = mk('div', 'chat-bg-agents');
                list.setAttribute('role', 'list');
                p.agents.forEach((a) => {
                    const row = mk('button', 'chat-bg-agent is-' + a.outcome);
                    row.type = 'button';
                    row.setAttribute('role', 'listitem');
                    row.setAttribute('data-chat-handle', a.handle);
                    row.title = 'Open ' + a.name + ' (' + a.handle + (a.agent ? ' · ' + a.agent : '') + ')';
                    const av = mk('span', 'chat-bg-agent-avatar', a.avatar || a.initial);
                    av.setAttribute('aria-hidden', 'true');
                    const text = mk('span', 'chat-bg-agent-text');
                    text.append(mk('span', 'chat-bg-agent-name', a.name));
                    if (a.status) text.append(mk('span', 'chat-bg-agent-status', a.status));
                    row.append(av, text, mk('span', 'chat-bg-agent-outcome', a.outcomeLabel));
                    list.append(row);
                });
                body.append(list);
            }
            if (p.meta.length) body.append(mk('div', 'chat-bg-meta', p.meta.join(' · ')));
            const actions = mk('div', 'chat-bg-actions');
            if (p.targets.length) actions.append(mk('span', 'chat-bg-supervising', 'Supervising'));
            p.targets.forEach((t) => {
                const b = mk('button', 'chat-bg-open', 'Open ' + t);
                b.type = 'button';
                b.setAttribute('data-chat-handle', t);
                b.title = 'Open the supervised chat ' + t;
                actions.append(b);
            });
            if (p.feed) {
                const a = mk('a', 'chat-bg-details', 'Details');
                a.href = '/agent_feed.html?chat=' + encodeURIComponent(handle(sid));
                a.title = 'Open the Running tree in Agent Feed';
                actions.append(a);
            }
            if (p.stop) {
                const b = mk('button', 'chat-bg-stop', 'Stop');
                b.type = 'button';
                b.setAttribute('aria-label', p.stop === 'chat' ? 'Stop background work' : 'End background processes');
                b.addEventListener('click', () => stop(b));
                actions.append(b);
            }
            node.replaceChildren(ring, body, actions);
            if (node.parentNode !== box) box.appendChild(node);
            keepLast();
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

    root.CuttleChatBackgroundWork = { ID, plan, jobView, elapsed, create };
})(typeof window === 'undefined' ? globalThis : window);
