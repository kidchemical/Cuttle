/* Chat background-work bubble: the open chat still owns work while no turn runs.

   Source: live-status `background` (api.chat_live_extras → api.jobs.work_tree
   .background_summary): detached Claude waits, watch jobs bound to the chat,
   supervision schedules it runs, and OS processes its agents left running
   (detached ones included). Shown pinned at the end of the transcript, never
   while a turn is generating (the typing bubble owns that), and never counted
   as a transcript record (no `.message` class).

   `plan(background, nowSec)` is pure; `create(host)` owns the DOM element,
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

    /** Presentation for one summary, or null when there is nothing to show. */
    function plan(bg) {
        if (!bg || typeof bg !== 'object') return null;
        const waits = bg.waits || [];
        const watches = bg.watches || [];
        const schedules = bg.schedules || [];
        const n = Number(bg.process_count) || 0;
        const d = Number(bg.detached_count) || 0;
        if (!waits.length && !watches.length && !schedules.length && !n) return null;
        const working = !!(waits.length || watches.length || n);
        const meta = [];
        if (n) meta.push(plural(n, 'process', 'processes') + (d ? ' (' + d + ' detached)' : ''));
        if (waits.length > 1) meta.push(plural(waits.length, 'wait', 'waits'));
        schedules.forEach((s) => meta.push([s.label, s.detail].filter(Boolean).join(' · ')));
        const targets = [];
        schedules.forEach((s) => {
            const t = handle(s && s.target);
            if (/^CH-\d+$/.test(t) && !targets.includes(t)) targets.push(t);
        });
        const starts = waits.map((w) => epoch(w.started_at)).concat([epoch(bg.started_at)]).filter((t) => t != null);
        return {
            title: waits.length ? 'Waiting in background' : watches.length ? 'Job running'
                : n ? (d ? 'Detached processes running' : 'Processes still running') : 'Supervising',
            label: bg.label || '',
            meta,
            watches: watches.map((w) => ({ label: w.label || w.job_id, percent:
                Number.isFinite(Number(w.percent)) ? Math.max(0, Math.min(100, Number(w.percent))) : null })),
            started: starts.length ? Math.min(...starts) : null,
            working,
            // Waits and watches stop through the chat's Stop path; loose
            // processes end through the process owner. Schedules: Jobs/Feed.
            stop: (waits.length || watches.length) ? 'chat' : (n ? 'processes' : null),
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
            if (!node || !current || current.plan.started == null) return;
            const t = node.querySelector('.chat-bg-elapsed');
            if (t) t.textContent = elapsed(Date.now() / 1000 - current.plan.started);
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
            const btn = t && t.closest && t.closest('.chat-bg-open');
            if (btn) {
                ev.preventDefault();
                openTarget(btn.getAttribute('data-chat-handle'));
                return;
            }
            if (t && t.closest && t.closest('a, button')) return;
            const p = current && current.plan;
            if (p && !p.working && !p.stop && p.targets.length === 1) openTarget(p.targets[0]);
        }

        function render(sid, p) {
            let node = el();
            const box = container();
            if (!box) return;
            if (!node) {
                node = doc.createElement('div');
                node.id = ID;
                node.className = 'chat-bg-work';
                node.setAttribute('role', 'status');
                node.setAttribute('aria-live', 'polite');
            }
            node.classList.toggle('is-scheduled', !p.working);
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
            p.watches.forEach((w) => {
                const row = mk('div', 'chat-bg-watch');
                row.append(mk('span', 'chat-bg-watch-label', w.label));
                if (w.percent != null) {
                    const bar = mk('span', 'chat-bg-bar');
                    const fill = mk('span', 'chat-bg-fill');
                    fill.style.width = w.percent + '%';
                    bar.append(fill);
                    row.append(bar, mk('span', 'chat-bg-pct', w.percent + '%'));
                }
                body.append(row);
            });
            if (p.meta.length) body.append(mk('div', 'chat-bg-meta', p.meta.join(' · ')));
            const actions = mk('div', 'chat-bg-actions');
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
            const p = busy ? null : plan(live && live.background);
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

    root.CuttleChatBackgroundWork = { ID, plan, elapsed, create };
})(typeof window === 'undefined' ? globalThis : window);
