/* ================================================================
   Cuttle Chat — orchestration controller (chat_orchestration.js).
   Owner: the "Orchestrating · <playbook>" header chip, its popover
   (goal, Edit…, Open playbook, Stop orchestrating), the ☰ menu item
   "Orchestrate this chat…" and its dialog. The binding is host state:
   starting sends `/orchestrate @id goal` (the server binds it before the
   agent runs); switching playbook / editing the goal is a PUT, never an
   agent turn. Display decisions live in CuttleChatOrchestrationModel;
   capabilities come in through `create(host)`. Hidden until the
   `orchestrator` flag resolves on.
   ================================================================ */
(function (root) {
    'use strict';

    const POLL_MS = 30000;

    function create(host) {
        const M = root.CuttleChatOrchestrationModel;
        const doc = host.doc;
        const esc = host.escapeHtml;
        let enabled = false;
        let session = null;
        let binding = null;
        let popover = null;
        let timer = null;
        let seq = 0;

        const slot = () => doc.getElementById(host.slotId || 'chatOrchestrationSlot');
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
            if (!enabled || !sid) { binding = null; render(); return; }
            try {
                const data = await call('GET', '/api/orchestrator/bindings/' + sid);
                if (mine === seq) { binding = data; render(); }
            } catch (_) {
                if (mine === seq) { binding = null; render(); }
            }
        }

        function render() {
            const el = slot();
            if (!el) return;
            const c = enabled ? M.chip(binding) : null;
            if (!c) { el.hidden = true; el.innerHTML = ''; closePopover(); return; }
            el.innerHTML = '<button type="button" class="chat-supervision-pill is-orchestrator' + (c.missing ? ' is-warn' : '')
                + '" data-orch-open="1" title="' + esc(c.goal ? 'Goal: ' + c.goal : 'Orchestrator playbook')
                + '" aria-haspopup="dialog">' + (c.missing ? '⚠ ' : '') + esc(c.text) + '</button>';
            el.hidden = false;
            if (popover) popover.innerHTML = popoverHtml(c);
        }

        function popoverHtml(c) {
            return '<div class="chat-supervision-title">' + esc(c.text) + '</div>'
                + (c.missing ? '<div class="chat-supervision-violation">⚠ Playbook “' + esc(c.playbookId)
                    + '” no longer exists; pick another with Edit….</div>' : '')
                + '<div class="chat-supervision-section">Goal</div>'
                + '<div class="chat-supervision-meta">' + esc(c.goal || '(none)') + '</div>'
                + '<div class="chat-supervision-meta">A changed playbook or goal reaches the agent at its next turn;'
                + ' a turn already running keeps the old brief. Supervision on this chat follows a playbook switch.</div>'
                + '<div class="chat-supervision-actions">'
                + '<button type="button" data-orch-act="edit">Edit…</button>'
                + '<button type="button" data-orch-act="playbooks">Open playbooks</button>'
                + '<button type="button" class="is-danger" data-orch-act="stop">Stop orchestrating</button>'
                + '</div>';
        }

        function openPopover(anchor) {
            closePopover();
            const c = M.chip(binding);
            if (!c) return;
            popover = doc.createElement('div');
            popover.className = 'chat-supervision-popover';
            popover.setAttribute('role', 'dialog');
            popover.innerHTML = popoverHtml(c);
            doc.body.appendChild(popover);
            const r = anchor.getBoundingClientRect();
            const W = host.viewportWidth();
            popover.style.top = Math.round(r.bottom + 6) + 'px';
            popover.style.left = Math.round(Math.max(8, Math.min(r.left, W - popover.offsetWidth - 8))) + 'px';
        }

        function closePopover() {
            if (popover) { popover.remove(); popover = null; }
        }

        async function act(verb) {
            const sid = numericSession(session);
            if (!sid) return;
            if (verb === 'edit') { closePopover(); openDialog(sid); return; }
            if (verb === 'playbooks') { closePopover(); host.navigate('/orchestrator_page.html'); return; }
            if (verb === 'stop') {
                if (!host.confirm('Stop orchestrating this chat? The playbook brief is dropped from its next turn.')) return;
                try {
                    await call('DELETE', '/api/orchestrator/bindings/' + sid);
                    host.toast('Orchestration stopped', 'success');
                } catch (e) {
                    host.toast(String(e.message || e), 'error');
                }
                closePopover();
                refresh();
            }
        }

        function onClick(ev) {
            const t = ev.target;
            const open = t.closest && t.closest('[data-orch-open]');
            if (open && slot() && slot().contains(open)) {
                if (popover) closePopover(); else openPopover(open);
                return;
            }
            if (popover && popover.contains(t)) {
                const a = t.closest('[data-orch-act]');
                if (a) act(a.getAttribute('data-orch-act'));
                return;
            }
            closePopover();
        }

        // ---- ☰ "Orchestrate this chat…" ----

        function menuHtml(sessionId, icon) {
            const sid = numericSession(sessionId);
            if (!enabled || !sid) return '';
            const label = String(sid) === String(numericSession(session)) && M.chip(binding)
                ? 'Orchestration…' : 'Orchestrate this chat…';
            return '<button type="button" class="chat-chrome-menu-item" role="menuitem" data-orch-start="' + sid + '"'
                + ' title="' + label + '" aria-label="' + label + '">' + (icon || '')
                + '<span class="chat-chrome-menu-item-label">' + label + '</span></button>';
        }

        async function openDialog(sessionId) {
            const sid = numericSession(sessionId);
            if (!enabled || !sid) return;
            let current;
            let playbooks;
            try {
                [current, playbooks] = await Promise.all([
                    call('GET', '/api/orchestrator/bindings/' + sid),
                    call('GET', '/api/orchestrator/playbooks'),
                ]);
            } catch (e) {
                host.toast(String(e.message || e), 'error');
                return;
            }
            const d = M.dialogDefaults(current, playbooks);
            const wrap = doc.createElement('div');
            wrap.className = 'chat-supervision-dialog-backdrop';
            wrap.innerHTML = '<form class="chat-supervision-dialog" role="dialog" aria-label="Orchestrate this chat">'
                + '<h3>' + (d.editing ? 'Orchestration of CH-' : 'Orchestrate CH-') + esc(sid.padStart(6, '0')) + '</h3>'
                + '<p class="chat-supervision-meta">' + (d.editing
                    ? 'Switch the playbook or edit the goal. The agent gets the new brief at its next turn; supervision on this chat follows a playbook switch.'
                    : 'Binds this chat to a playbook and sends the goal as the first turn, on this chat’s agent.') + '</p>'
                + '<label>Playbook<select name="playbookId">' + d.playbooks.map((p) => '<option value="' + esc(p.id) + '"'
                    + (p.id === d.playbookId ? ' selected' : '') + '>' + esc(p.label) + (p.problem ? ' — ' + esc(p.problem) : '')
                    + '</option>').join('') + '</select></label>'
                + '<label>Goal<textarea name="goal" rows="4" placeholder="What should the orchestrator get done?">'
                    + esc(d.goal) + '</textarea></label>'
                + '<div class="chat-supervision-errors" role="alert"></div>'
                + '<div class="chat-supervision-actions"><button type="button" data-orch-dismiss>Cancel</button>'
                + '<button type="submit" class="is-primary">' + (d.editing ? 'Save changes' : 'Start orchestrating')
                + '</button></div></form>';
            doc.body.appendChild(wrap);
            const form = wrap.querySelector('form');
            const close = () => wrap.remove();
            wrap.addEventListener('click', (ev) => {
                if (ev.target === wrap || (ev.target.closest && ev.target.closest('[data-orch-dismiss]'))) close();
            });
            form.addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const plan = M.submitPlan(d, { playbookId: form.playbookId.value, goal: form.goal.value });
                const errBox = form.querySelector('.chat-supervision-errors');
                if (!plan.ok) { errBox.textContent = plan.errors.join(' '); return; }
                if (plan.kind === 'none') { close(); return; }
                const submit = form.querySelector('[type=submit]');
                submit.disabled = true;
                try {
                    if (plan.kind === 'send') {
                        close();
                        await host.sendMessage(plan.text);
                        // The server binds as the message arrives; pick the chip up promptly.
                        [1500, 6000].forEach((ms) => root.setTimeout(refresh, ms));
                    } else {
                        await call('PUT', '/api/orchestrator/bindings/' + sid, plan.body);
                        close();
                        host.toast('Orchestration updated', 'success');
                    }
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
            const btn = ev.target.closest && ev.target.closest('[data-orch-start]');
            if (!btn) return;
            ev.preventDefault();
            ev.stopPropagation();
            host.closeMenus();
            openDialog(btn.getAttribute('data-orch-start'));
        }

        function setEnabled(on) {
            on = !!on;
            if (on === enabled) return;
            enabled = on;
            if (timer) host.clearInterval(timer);
            timer = enabled ? host.setInterval(() => { if (doc.visibilityState !== 'hidden') refresh(); }, POLL_MS) : null;
            refresh();
        }

        async function syncFlag() {
            try {
                const flags = await host.enabledFlags();
                setEnabled(flags.has('orchestrator'));
            } catch (_) {
                setEnabled(false);
            }
        }

        function setSession(sid) {
            if (String(sid || '') === String(session || '')) return;
            session = sid;
            binding = null;
            closePopover();
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

        return { setSession, refresh, menuHtml, openDialog, isEnabled: () => enabled };
    }

    root.CuttleChatOrchestration = { create };
})(typeof window !== 'undefined' ? window : globalThis);
