/* Agent Feed "Running now": every chat with live work and the processes it owns.
   Controller over CuttleAgentWork (pure model). Reads GET /api/jobs/work-tree;
   controls go to their owners: chat Stop → /api/chat-cancel, processes →
   /api/jobs/processes/<pid>/end and /api/jobs/chats/<id>/end-processes,
   schedules → /api/jobs/chat_op/<id>/cancel. */
(function(root) {
    'use strict';
    const POLL_MS = 4000;

    function create(opts) {
        const host = opts || {};
        const el = host.root;
        const doc = host.doc || document;
        const win = host.win || window;
        const fetchImpl = host.fetch || ((...a) => win.fetch(...a));
        const confirmImpl = host.confirm || ((text) => win.confirm(text));
        const say = host.status || (() => {});
        const M = root.CuttleAgentWork;
        const openOwners = new Set();
        // `?chat=CH-001821` (chat bubble "Details") opens that chat's row.
        const wanted = String(host.openChat || '').trim().replace(/^CH-0*/i, '').replace(/^db_session_/, '');
        if (wanted) openOwners.add(wanted);
        const collapsedProcs = new Set();
        let tree = null, sig = '', timer = 0, ticker = 0, inFlight = false, stopped = false;

        const nowSec = () => (host.now ? host.now() : Date.now()) / 1000;
        function node(tag, cls, text) {
            const n = doc.createElement(tag);
            if (cls) n.className = cls;
            if (text != null) n.textContent = text;
            return n;
        }
        function chatHref(chat) { return '/chat_page.html?chat=' + encodeURIComponent(chat); }
        function timeEl(started) {
            const t = node('span', 'work-elapsed', M.since(started, nowSec()));
            const start = M.epoch(started);
            if (start != null) t.dataset.start = String(start);
            return t;
        }
        function button(cls, text, label, onClick) {
            const b = node('button', cls, text);
            b.type = 'button';
            if (label) b.setAttribute('aria-label', label);
            b.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                onClick(b);
            });
            return b;
        }
        async function post(url, body) {
            const r = await fetchImpl(url, { method: 'POST', credentials: 'include',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
            const data = await r.json().catch(() => ({}));
            if (!r.ok || data.success === false) throw new Error(data.error || ('Request failed (' + r.status + ')'));
            return data;
        }
        async function act(btn, busyText, work) {
            const old = btn.textContent;
            btn.disabled = true;
            btn.textContent = busyText;
            try {
                say(await work());
            } catch (error) {
                say(error.message);
                btn.disabled = false;
                btn.textContent = old;
            }
            await refresh();
        }

        function orbit() {
            const o = node('span', 'typing-orbit typing-orbit--feed');
            o.setAttribute('aria-hidden', 'true');
            ['typing-orbit-ring', 'typing-orbit-ring typing-orbit-ring--inner', 'typing-orbit-core',
                'typing-orbit-sat typing-orbit-sat--1', 'typing-orbit-sat typing-orbit-sat--2',
                'typing-orbit-sat typing-orbit-sat--3', 'typing-orbit-beam'].forEach((c) => o.append(node('span', c)));
            return o;
        }

        function renderProc(n, owner) {
            const li = node('li', 'work-proc');
            li.dataset.pid = String(n.pid);
            const row = node('div', 'work-proc-row');
            const kids = n.children || [];
            const key = owner.chat_session_id + ':' + n.pid;
            if (kids.length) {
                const t = button('work-proc-toggle', collapsedProcs.has(key) ? '▸' : '▾', null, () => {
                    if (collapsedProcs.has(key)) collapsedProcs.delete(key); else collapsedProcs.add(key);
                    const open = !collapsedProcs.has(key);
                    t.textContent = open ? '▾' : '▸';
                    t.setAttribute('aria-expanded', String(open));
                    list.hidden = !open;
                });
                t.setAttribute('aria-expanded', String(!collapsedProcs.has(key)));
                t.setAttribute('aria-label', 'Child processes of ' + n.name);
                row.append(t);
            } else {
                row.append(node('span', 'work-proc-leaf', '·'));
            }
            row.append(node('span', 'work-proc-name', n.name || String(n.pid)), node('span', 'work-proc-pid', String(n.pid)));
            if (n.detached) {
                const b = node('span', 'work-badge is-detached', 'detached');
                b.title = 'Re-parented away from its agent (nohup/setsid). Owner from its run marker'
                    + (n.run_id ? ' (run ' + n.run_id + ')' : '') + '.';
                row.append(b);
            }
            if (n.parent_chat) row.append(node('span', 'work-badge', 'started by ' + M.handle(n.parent_chat)));
            const cmd = node('span', 'work-proc-cmd', M.shortCommand(n.cmdline));
            cmd.title = n.cmdline || '';
            row.append(cmd, node('span', 'work-proc-mem', M.bytes(n.rss)), timeEl(n.create_time));
            row.append(button('work-proc-end', 'End', 'End process ' + n.pid, (b) => {
                if (!confirmImpl(M.endProcessPrompt(n))) return;
                act(b, 'Ending…', async () => {
                    const d = await post('/api/jobs/processes/' + n.pid + '/end', { create_time: n.create_time });
                    return 'Ended ' + (d.name || n.name) + ' (' + d.ended + (d.ended === 1 ? ' process)' : ' processes)');
                });
            }));
            li.append(row);
            const list = node('ul', 'work-procs');
            kids.forEach((c) => list.append(renderProc(c, owner)));
            list.hidden = !kids.length || collapsedProcs.has(key);
            if (kids.length) li.append(list);
            return li;
        }

        function renderLine(line, owner) {
            const li = node('li', 'work-line');
            li.dataset.kind = line.kind;
            const icons = { run: '▶', wait: '⏳', watch: '▤', schedule: '↻' };
            li.append(node('span', 'work-line-icon', icons[line.kind] || '·'), node('span', 'work-line-text', line.text));
            if (line.kind === 'watch' && (line.percent != null || line.indeterminate)) {
                const bar = node('span', 'work-line-bar');
                const fill = node('span', 'work-line-fill');
                bar.append(fill);
                bar.setAttribute('role', 'progressbar');
                if (line.indeterminate) {
                    // Nothing measured: animated stripe (shared CSS), no "0%".
                    bar.setAttribute('data-progress', 'indeterminate');
                    bar.setAttribute('aria-valuetext', 'In progress');
                    li.append(bar);
                } else {
                    fill.style.width = line.percent + '%';
                    bar.setAttribute('aria-valuenow', String(line.percent));
                    li.append(bar, node('span', 'work-line-pct', line.percent + '%'));
                }
            }
            if (line.kind === 'run' && line.query_id) {
                const a = node('a', 'work-line-link', 'query log');
                a.href = '/query_log.html?id=' + encodeURIComponent(line.query_id);
                li.append(a);
            }
            if (line.kind === 'schedule' && line.target) {
                const a = node('a', 'work-line-link', line.target);
                a.href = chatHref(String(line.target).replace(/^CH-0*/, ''));
                li.append(a);
            }
            if (line.started) li.append(timeEl(line.started));
            if (line.kind === 'schedule' && line.cancellable) {
                li.append(button('work-line-end', 'Cancel', 'Cancel ' + line.text, (b) => {
                    if (!confirmImpl('Cancel "' + line.text + '"?')) return;
                    act(b, 'Cancelling…', async () => {
                        await post('/api/jobs/chat_op/' + encodeURIComponent(line.id) + '/cancel');
                        return 'Cancelled ' + line.text;
                    });
                }));
            }
            return li;
        }

        function renderOwner(o, depth) {
            const det = node('details', 'work-owner');
            det.dataset.chat = o.chat_session_id;
            det.dataset.state = o.state;
            if (openOwners.has(o.chat_session_id)) det.open = true;
            det.addEventListener('toggle', () => {
                if (det.open) openOwners.add(o.chat_session_id); else openOwners.delete(o.chat_session_id);
            });
            const sum = node('summary', 'work-owner-row');
            sum.append(o.state === 'scheduled' ? node('span', 'work-dot is-scheduled') : orbit());
            const main = node('span', 'work-owner-main');
            const top = node('span', 'work-owner-top');
            const chat = node('a', 'work-chat', o.handle || M.handle(o.chat_session_id));
            chat.href = chatHref(o.chat_session_id);
            chat.addEventListener('click', (e) => e.stopPropagation());
            top.append(chat, node('span', 'work-title', o.title || ''));
            const sub = node('span', 'work-owner-sub');
            sub.append(node('span', 'work-state', M.stateLabel(o)));
            if (o.label) sub.append(node('span', 'work-label', o.label));
            if (o.parent && depth === 0) sub.append(node('span', 'work-badge', 'child of ' + o.parent.handle));
            main.append(top, sub);
            sum.append(main);
            const badges = node('span', 'work-badges');
            M.ownerBadges(o).forEach((b) => badges.append(node('span', 'work-badge' + (b.kind === 'detached' ? ' is-detached' : ''), b.text)));
            sum.append(badges, timeEl(o.started_at));
            if (M.canEndAll(o)) {
                sum.append(button('work-end', 'End all', 'End all work in ' + o.handle, (b) => {
                    if (!confirmImpl(M.endAllPrompt(o))) return;
                    act(b, 'Ending…', async () => {
                        if (M.canStop(o)) await post('/api/chat-cancel', { session_id: o.chat_session_id, cancel_jobs: true });
                        let ended = 0;
                        if (o.process_count) {
                            const d = await post('/api/jobs/chats/' + encodeURIComponent(o.chat_session_id) + '/end-processes');
                            ended = (d.ended || []).reduce((n, r) => n + (r.ended || 0), 0);
                        }
                        return 'Ended ' + o.handle + (ended ? ' · ' + ended + ' processes' : '');
                    });
                }));
            }
            det.append(sum);
            const body = node('div', 'work-body');
            const lines = M.ownerLines(o);
            if (lines.length) {
                const ul = node('ul', 'work-lines');
                lines.forEach((l) => ul.append(renderLine(l, o)));
                body.append(ul);
            }
            if ((o.processes || []).length) {
                body.append(node('div', 'work-section-title', 'Processes'));
                const ul = node('ul', 'work-procs is-root');
                o.processes.forEach((n) => ul.append(renderProc(n, o)));
                body.append(ul);
            }
            if ((o.children || []).length) {
                body.append(node('div', 'work-section-title', 'Sub-agent chats'));
                const kids = node('div', 'work-children');
                o.children.forEach((c) => kids.append(renderOwner(c, depth + 1)));
                body.append(kids);
            }
            if (!body.childElementCount) body.append(node('p', 'work-empty-note', 'No processes visible for this chat.'));
            det.append(body);
            return det;
        }

        function renderMesh(job) {
            const row = node('div', 'work-mesh');
            row.append(node('span', 'work-dot is-mesh'), node('span', 'work-title', job.label || 'mesh job'),
                node('span', 'work-state', job.status || ''), node('span', 'work-label', (job.meta && job.meta.detail) || job.target || ''),
                timeEl(job.started_at));
            if ((job.actions || []).includes('cancel')) {
                row.append(button('work-end', 'Cancel', 'Cancel mesh job ' + job.short_id, (b) => {
                    if (!confirmImpl('Cancel mesh job ' + (job.label || job.id) + '?')) return;
                    act(b, 'Cancelling…', async () => {
                        await post('/api/jobs/mesh/' + encodeURIComponent(job.id) + '/cancel');
                        return 'Cancelled ' + (job.label || job.id);
                    });
                }));
            }
            return row;
        }

        function render() {
            if (!el) return;
            if (!tree || M.isEmpty(tree)) {
                el.hidden = true;
                el.replaceChildren();
                return;
            }
            el.hidden = false;
            const focusedChat = el.contains(doc.activeElement) ? doc.activeElement.closest('[data-chat]') : null;
            const focusKey = focusedChat ? focusedChat.dataset.chat : null;
            const frag = doc.createDocumentFragment();
            const title = node('h2', 'feed-active-title', 'Running now');
            title.append(node('span', 'work-totals', M.totalsText(tree)));
            frag.append(title);
            (tree.owners || []).forEach((o) => frag.append(renderOwner(o, 0)));
            if ((tree.mesh || []).length) {
                frag.append(node('div', 'work-section-title', 'Mesh jobs'));
                tree.mesh.forEach((j) => frag.append(renderMesh(j)));
            }
            el.replaceChildren(frag);
            if (focusKey) {
                const again = el.querySelector('[data-chat="' + focusKey + '"] > summary');
                if (again) again.focus({ preventScroll: true });
            }
        }

        function tick() {
            if (!el) return;
            const now = nowSec();
            el.querySelectorAll('.work-elapsed[data-start]').forEach((t) => {
                t.textContent = M.elapsed(Math.max(0, now - Number(t.dataset.start)));
            });
        }

        async function refresh() {
            if (inFlight || stopped) return;
            inFlight = true;
            try {
                const r = await fetchImpl('/api/jobs/work-tree', { credentials: 'include', cache: 'no-store' });
                if (!r.ok) return;
                tree = await r.json();
                const next = M.signature(tree);
                if (next !== sig) {
                    sig = next;
                    render();
                } else {
                    tick();
                }
            } catch (_) {
            } finally {
                inFlight = false;
            }
        }

        function start() {
            stopped = false;
            refresh();
            if (!timer) timer = win.setInterval(() => { if (!doc.hidden) refresh(); }, POLL_MS);
            if (!ticker) ticker = win.setInterval(tick, 1000);
        }
        function stop() {
            stopped = true;
            if (timer) win.clearInterval(timer);
            if (ticker) win.clearInterval(ticker);
            timer = ticker = 0;
        }
        return { start, stop, refresh, tree: () => tree };
    }

    root.CuttleAgentWorkPanel = { create, POLL_MS };
})(typeof window === 'undefined' ? globalThis : window);
