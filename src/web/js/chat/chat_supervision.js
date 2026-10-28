/* ================================================================
   Cuttle Chat — supervision controller (chat_supervision.js).
   Owner: the supervision chip (supervisor chat), "Supervised by" badge
   (worker chat), queued-message chip, their popover (check history,
   pause/resume/cancel) and the "Supervise this chat…" dialog.
   Display decisions live in CuttleChatSupervisionModel (pure); this
   controller only mounts, polls and calls /api/chat-ops/*. The page
   injects every capability through `create(host)`; no page globals.
   Hidden until the `chat_supervision` flag resolves on.
   ================================================================ */
(function (root) {
    'use strict';

    const POLL_MS = 30000;

    function create(host) {
        const M = root.CuttleChatSupervisionModel;
        const doc = host.doc;
        const esc = host.escapeHtml;
        let enabled = false;
        let session = null;
        let overview = null;
        let timer = null;
        let popover = null;
        let seq = 0;

        const slot = () => doc.getElementById(host.slotId || 'chatSupervisionSlot');
        const now = () => host.now() / 1000;
        const numericSession = (sid) => (/^\d+$/.test(String(sid || '')) ? String(sid) : null);

        async function call(method, path, body) {
            const r = await host.fetch(path, {
                method, credentials: 'include', cache: 'no-store',
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined,
            });
            const data = await r.json().catch(() => ({}));
            if (data && data.disabled) { setEnabled(false); throw new Error(data.error || 'disabled'); }
            if (!r.ok || !data.success) throw new Error((data && data.error) || ('HTTP ' + r.status));
            return data;
        }

        async function refresh() {
            const sid = numericSession(session);
            const mine = ++seq;
            if (!enabled || !sid) { overview = null; render(); return; }
            try {
                const data = await call('GET', '/api/chat-ops/sessions/' + sid);
                if (mine === seq) { overview = data; render(); }
            } catch (_) {
                if (mine === seq) { overview = null; render(); }
            }
        }

        function schedulePoll() {
            if (timer) host.clearInterval(timer);
            timer = enabled ? host.setInterval(() => {
                if (doc.visibilityState !== 'hidden') refresh();
            }, POLL_MS) : null;
        }

        function pill(cls, id, label, title, kind) {
            return '<button type="button" class="chat-supervision-pill ' + cls + '" data-sup-open="' + esc(kind)
                + '" data-sup-id="' + esc(id) + '" title="' + esc(title) + '" aria-haspopup="dialog">'
                + esc(label) + '</button>';
        }

        function render() {
            const el = slot();
            if (!el) return;
            const v = enabled && overview ? M.view(overview, now()) : null;
            if (!v || v.empty) {
                el.hidden = true;
                el.innerHTML = '';
                closePopover();
                return;
            }
            const parts = [];
            v.chips.forEach((c) => parts.push(pill('is-supervisor' + (c.paused ? ' is-paused' : '')
                + (c.violations.length ? ' is-warn' : ''), c.id, (c.violations.length ? '⚠ ' : '') + c.text,
            'Supervision schedule — click for history and controls', 'chip')));
            v.badges.forEach((b) => parts.push(pill('is-supervised' + (b.paused ? ' is-paused' : '')
                + (b.violations.length ? ' is-warn' : ''), b.id,
            (b.violations.length ? '⚠ ' : '') + b.text + (b.paused ? ' (paused)' : ''),
            b.violations.length ? b.violations.join('\n') : 'Click for supervisor details', 'badge')));
            if (v.queued.length) {
                parts.push(pill('is-queued', 'queued', v.queued.length + ' queued',
                    'Messages waiting for this chat to go idle', 'queued'));
            }
            el.innerHTML = parts.join('');
            el.hidden = false;
            if (popover && popover.__key) {
                const still = popoverContent(popover.__key.kind, popover.__key.id, v);
                if (still) popover.innerHTML = still; else closePopover();
            }
        }

        function historyHtml(rows) {
            if (!rows.length) return '<div class="chat-supervision-empty">No checks yet.</div>';
            return '<ol class="chat-supervision-history">' + rows.map((r) => '<li class="tone-' + esc(r.tone) + '">'
                + '<span class="chat-supervision-time">' + esc(r.time) + '</span> ' + esc(r.text)
                + (r.violations.length ? '<div class="chat-supervision-violation">' + r.violations.map(esc).join('<br>')
                    + '</div>' : '') + '</li>').join('') + '</ol>';
        }

        function controls(id, paused, openHandle, openLabel) {
            return '<div class="chat-supervision-actions">'
                + '<button type="button" data-sup-chat="' + esc(openHandle) + '">' + esc(openLabel) + '</button>'
                + '<button type="button" data-sup-act="' + (paused ? 'resume' : 'pause') + '" data-sup-id="' + esc(id) + '">'
                + (paused ? 'Resume' : 'Pause') + '</button>'
                + '<button type="button" class="is-danger" data-sup-act="cancel" data-sup-id="' + esc(id) + '">Cancel</button>'
                + '</div>';
        }

        function popoverContent(kind, id, v) {
            if (kind === 'chip') {
                const c = v.chips.find((x) => x.id === id);
                if (!c) return '';
                return '<div class="chat-supervision-title">' + esc(c.text) + '</div>'
                    + '<div class="chat-supervision-meta">'
                    + (c.agent ? 'Check-ins run on /' + esc(c.agent) + '. ' : '')
                    + (c.rules ? 'Rules: ' + esc(c.rules) + '. ' : '')
                    + (c.lastSeen ? 'Last seen: ' + esc(c.lastSeen) + (c.tasks ? ', ' + esc(c.tasks) : '') + '.' : '')
                    + '</div>'
                    + (c.violations.length ? '<div class="chat-supervision-violation">⚠ '
                        + c.violations.map(esc).join('<br>⚠ ') + '</div>' : '')
                    + '<div class="chat-supervision-section">Check history</div>' + historyHtml(c.history)
                    + controls(c.id, c.paused, c.target, 'Open ' + c.target);
            }
            if (kind === 'badge') {
                const b = v.badges.find((x) => x.id === id);
                if (!b) return '';
                return '<div class="chat-supervision-title">' + esc(b.text) + '</div>'
                    + '<div class="chat-supervision-meta">' + esc(b.paused ? 'Paused.' : (b.next ? 'Next check: '
                        + b.next.replace(/^next /, '') + '.' : '')) + ' The supervisor is only woken when this chat'
                    + ' stops, errors, needs input, makes progress, or breaks a branch rule.</div>'
                    + (b.violations.length ? '<div class="chat-supervision-violation">⚠ '
                        + b.violations.map(esc).join('<br>⚠ ') + '</div>' : '')
                    + controls(b.id, b.paused, b.supervisor, 'Open ' + b.supervisor);
            }
            if (kind === 'queued') {
                if (!v.queued.length) return '';
                return '<div class="chat-supervision-title">Queued for this chat</div>'
                    + '<div class="chat-supervision-meta">Delivered automatically when the current turn ends.</div>'
                    + '<ul class="chat-supervision-queue">' + v.queued.map((q) => '<li title="' + esc(q.text) + '">'
                        + '<span class="chat-supervision-from">' + esc(q.from) + '</span> ' + esc(q.preview)
                        + ' <button type="button" class="is-danger" data-sup-act="cancel" data-sup-id="' + esc(q.id)
                        + '">Cancel</button></li>').join('') + '</ul>';
            }
            return '';
        }

        function openPopover(kind, id, anchor) {
            closePopover();
            const v = overview && M.view(overview, now());
            const html = v && popoverContent(kind, id, v);
            if (!html) return;
            popover = doc.createElement('div');
            popover.className = 'chat-supervision-popover';
            popover.setAttribute('role', 'dialog');
            popover.__key = { kind, id };
            popover.innerHTML = html;
            doc.body.appendChild(popover);
            const r = anchor.getBoundingClientRect();
            const W = host.viewportWidth();
            popover.style.top = Math.round(r.bottom + 6) + 'px';
            popover.style.left = Math.round(Math.max(8, Math.min(r.left, W - popover.offsetWidth - 8))) + 'px';
        }

        function closePopover() {
            if (popover) { popover.remove(); popover = null; }
        }

        async function act(verb, id) {
            if (verb === 'cancel' && !host.confirm('Cancel this ' + (id && overview && (overview.queued || [])
                .some((q) => q.id === id) ? 'queued message' : 'supervision schedule') + '?')) return;
            try {
                await call('POST', '/api/chat-ops/operations/' + encodeURIComponent(id) + '/' + verb);
                host.toast(verb === 'cancel' ? 'Cancelled' : verb === 'pause' ? 'Supervision paused'
                    : 'Supervision resumed; checking now', 'success');
            } catch (e) {
                host.toast(String(e.message || e), 'error');
            }
            refresh();
        }

        function onClick(ev) {
            const t = ev.target;
            const open = t.closest && t.closest('[data-sup-open]');
            if (open && slot() && slot().contains(open)) {
                const same = popover && popover.__key && popover.__key.id === open.getAttribute('data-sup-id');
                if (same) closePopover();
                else openPopover(open.getAttribute('data-sup-open'), open.getAttribute('data-sup-id'), open);
                return;
            }
            if (popover && popover.contains(t)) {
                const a = t.closest('[data-sup-act]');
                if (a) act(a.getAttribute('data-sup-act'), a.getAttribute('data-sup-id'));
                const c = t.closest('[data-sup-chat]');
                if (c) { closePopover(); host.openChat(c.getAttribute('data-sup-chat').replace(/^CH-0*/, '')); }
                return;
            }
            closePopover();
        }

        // ---- "Supervise this chat…" ----

        function menuHtml(sessionId, icon) {
            const sid = numericSession(sessionId);
            if (!enabled || !sid) return '';
            return '<button type="button" class="chat-chrome-menu-item" role="menuitem" data-sup-start="' + sid + '"'
                + ' title="Supervise this chat…" aria-label="Supervise this chat…">' + (icon || '')
                + '<span class="chat-chrome-menu-item-label">Supervise this chat…</span></button>';
        }

        async function openDialog(sessionId) {
            const sid = numericSession(sessionId);
            if (!enabled || !sid) return;
            let defaults;
            let agents;
            try {
                [defaults, agents] = await Promise.all([
                    call('GET', '/api/chat-ops/sessions/' + sid + '/defaults'),
                    host.fetch('/api/agents', { credentials: 'include' }).then((r) => r.json()),
                ]);
            } catch (e) {
                host.toast(String(e.message || e), 'error');
                return;
            }
            const d = M.dialogDefaults(defaults);
            const list = ((agents && agents.agents) || []).filter((a) => a && a.id && a.sticky !== false);
            const wrap = doc.createElement('div');
            wrap.className = 'chat-supervision-dialog-backdrop';
            wrap.innerHTML = '<form class="chat-supervision-dialog" role="dialog" aria-label="Supervise this chat">'
                + '<h3>Supervise ' + esc(d.target) + '</h3>'
                + '<p class="chat-supervision-meta">Creates a supervisor chat. The host checks ' + esc(d.target)
                + ' on the interval and wakes the supervisor only when this chat stops, errors, needs input,'
                + ' makes progress, or breaks a branch rule.</p>'
                + '<label>Supervisor agent<select name="agent">' + list.map((a) => '<option value="' + esc(a.id) + '"'
                    + (a.id === 'cursor' ? ' selected' : '') + '>' + esc(a.label || a.id) + '</option>').join('')
                + '</select></label>'
                + '<label>Check every<select name="every">' + M.INTERVALS.map((s) => '<option value="' + s + '"'
                    + (s === d.every ? ' selected' : '') + '>' + M.fmtInterval(s) + '</option>').join('') + '</select></label>'
                + '<label>Instructions<textarea name="instructions" rows="4">' + esc(d.instructions) + '</textarea></label>'
                + '<label>Must stay on branch<input name="requiredBranch" value="' + esc(d.requiredBranch)
                + '" placeholder="any"></label>'
                + '<label>Never touch branches<input name="forbidden" value="' + esc(d.forbidden) + '"></label>'
                + '<label>Worker models (optional)<input name="worker" placeholder="e.g. cursor auto, codex terra medium"></label>'
                + '<label>Expert models (optional)<input name="expert" placeholder="e.g. claude opus"></label>'
                + '<div class="chat-supervision-errors" role="alert"></div>'
                + '<div class="chat-supervision-actions"><button type="button" data-sup-dismiss>Cancel</button>'
                + '<button type="submit" class="is-primary">Start supervising</button></div></form>';
            doc.body.appendChild(wrap);
            const form = wrap.querySelector('form');
            const close = () => wrap.remove();
            wrap.addEventListener('click', (ev) => {
                if (ev.target === wrap || (ev.target.closest && ev.target.closest('[data-sup-dismiss]'))) close();
            });
            form.addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const fd = new root.FormData(form);
                const payload = M.supervisePayload(Object.assign({}, d, {
                    agent: fd.get('agent'), every: Number(fd.get('every')), instructions: fd.get('instructions'),
                    requiredBranch: fd.get('requiredBranch'), forbidden: fd.get('forbidden'),
                    worker: fd.get('worker'), expert: fd.get('expert'),
                }));
                const errBox = form.querySelector('.chat-supervision-errors');
                if (!payload.ok) { errBox.textContent = payload.errors.join(' '); return; }
                const submit = form.querySelector('[type=submit]');
                submit.disabled = true;
                try {
                    const res = await call('POST', '/api/chat-ops/supervise', payload.body);
                    close();
                    host.toast('Supervisor started: CH-' + String(res.operation.actor).padStart(6, '0'), 'success');
                    refresh();
                } catch (e) {
                    errBox.textContent = String(e.message || e);
                    submit.disabled = false;
                }
            });
            const first = form.querySelector('select');
            if (first) first.focus();
        }

        function onMenuClick(ev) {
            const btn = ev.target.closest && ev.target.closest('[data-sup-start]');
            if (!btn) return;
            ev.preventDefault();
            ev.stopPropagation();
            host.closeMenus();
            openDialog(btn.getAttribute('data-sup-start'));
        }

        function setEnabled(on) {
            on = !!on;
            if (on === enabled) return;
            enabled = on;
            schedulePoll();
            refresh();
        }

        async function syncFlag() {
            try {
                const flags = await host.enabledFlags();
                setEnabled(flags.has('chat_supervision'));
            } catch (_) {
                setEnabled(false);
            }
        }

        function setSession(sid) {
            if (String(sid || '') === String(session || '')) return;
            session = sid;
            closePopover();
            overview = null;
            render();
            refresh();
        }

        doc.addEventListener('click', onClick);
        doc.addEventListener('click', onMenuClick, true);
        root.addEventListener('message', (ev) => {
            if (ev.origin === root.location.origin && ev.data && ev.data.type === 'cuttle-experimental-flags-changed') {
                syncFlag();
            }
        });
        doc.addEventListener('visibilitychange', () => { if (doc.visibilityState === 'visible') refresh(); });
        syncFlag();

        return { setSession, refresh, menuHtml, openDialog, isEnabled: () => enabled, _render: render };
    }

    root.CuttleChatSupervision = { create };
})(typeof window !== 'undefined' ? window : globalThis);
