/* ================================================================
   Cuttle Chat — execution node controller (chat_exec_node.js).
   Owner: the remote-node header chip, chat menu item, and dialog
   (node, working directory on that node). The
   binding is host state: Save is a PUT, never an agent turn, and it
   applies from the chat's next turn. A new chat has no id yet: its
   pick is held here and sent with the first message
   (`attachToRequest`). Display decisions live in
   CuttleChatExecNodeModel; capabilities come in through `create(host)`.
   Hidden until the `exec_nodes` flag resolves on.
   ================================================================ */
(function (root) {
    'use strict';

    const POLL_MS = 30000;

    function create(host) {
        const M = root.CuttleChatExecNodeModel;
        const doc = host.doc;
        const esc = host.escapeHtml;
        let enabled = false;
        let session = null;
        let binding = null;
        let pending = null; // a new chat's pick, until its first message mints the chat
        let timer = null;
        let seq = 0;

        const slot = () => doc.getElementById(host.slotId || 'chatExecNodeSlot');
        const numericSession = (sessionId) => {
            const sid = host.authSessionId(sessionId);
            return /^\d+$/.test(sid) ? sid : null;
        };
        const isDraft = (sessionId) => sessionId == null || sessionId === '';
        const setPending = (next) => {
            pending = next;
            render();
            if (typeof host.refreshHeader === 'function') host.refreshHeader();
        };

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
                const data = await call('GET', '/api/workers/exec/chats/' + sid);
                if (mine === seq) { binding = data.binding; render(); }
            } catch (_) {
                if (mine === seq) { binding = null; render(); }
            }
        }

        function render() {
            const el = slot();
            if (!el) return;
            const c = enabled ? M.chip(isDraft(session) ? pending : binding) : null;
            if (!c) { el.hidden = true; el.innerHTML = ''; return; }
            el.innerHTML = '<button type="button" class="chat-supervision-pill is-exec-node'
                + (c.warn ? ' is-warn' : ' is-active')
                + '" data-exec-node-open="1" data-sup-compact="' + esc(c.compact)
                + '" title="' + esc(c.title) + '" aria-label="' + esc(c.title) + '" aria-haspopup="dialog">'
                + '<span class="chat-supervision-pill-text">'
                + (c.warn ? '⚠ ' : '') + esc(c.text) + (c.busy ? ' · busy' : '') + '</span></button>';
            el.hidden = false;
        }

        function menuHtml(sessionId, icon) {
            const sid = numericSession(sessionId);
            if (!enabled || !(sid || isDraft(sessionId)) || !host.isOwner()) return '';
            return '<button type="button" class="chat-chrome-menu-item" role="menuitem" aria-haspopup="dialog"'
                + ' data-exec-node-menu="' + (sid || '') + '" title="Execution node…">' + (icon || '')
                + '<span class="chat-chrome-menu-item-label">Execution node…</span></button>';
        }

        async function openDialog(sessionId = session) {
            const sid = numericSession(sessionId);
            const draft = isDraft(sessionId);
            if (!enabled || !(sid || draft)) return;
            let data;
            try {
                const agent = typeof host.agentId === 'function' ? String(host.agentId() || '') : '';
                data = await call('GET', (draft ? '/api/workers/exec/nodes' : '/api/workers/exec/chats/' + sid)
                    + (agent ? '?agent=' + encodeURIComponent(agent) : ''));
            } catch (e) {
                host.toast(String(e.message || e), 'error');
                return;
            }
            const nodes = data.nodes || [];
            const current = draft ? (pending || M.draft(nodes, 'host', '')) : data.binding;
            const byId = (id) => nodes.find((n) => String(n.id) === String(id)) || (current && current.node) || null;
            const title = draft ? 'Execution node of the new chat' : 'Execution node of CH-' + sid.padStart(6, '0');
            const returnFocus = doc.activeElement;
            const wrap = doc.createElement('div');
            wrap.className = 'chat-supervision-dialog-backdrop';
            // Same shell as the supervision / orchestration dialogs.
            wrap.innerHTML = '<form class="auth-modal chat-supervision-dialog" role="dialog" aria-modal="true" aria-label="'
                + esc(title) + '">'
                + '<header class="chat-supervision-dialog-header"><button type="button" class="auth-modal-close" data-exec-node-dismiss'
                + ' aria-label="Close">×</button><h3>' + esc(title) + '</h3>'
                + '<p class="chat-supervision-meta">Pick the machine that runs this chat’s agent. Pair machines in Jobs → Devices.</p></header>'
                + '<div class="chat-supervision-dialog-body"><div class="chat-supervision-fields">'
                + '<label class="is-wide">Execution node<select name="nodeId">' + M.options(nodes, current).map((o) => '<option value="'
                    + esc(o.value) + '"' + (o.selected ? ' selected' : '') + '>' + esc(o.label) + '</option>').join('')
                + '</select></label>'
                + '<label class="is-wide" data-exec-node-cwd>Working directory on that machine'
                + '<input type="text" name="cwd" autocomplete="off" spellcheck="false" value="' + esc(current.cwd || '') + '"></label>'
                + '</div><p class="chat-supervision-meta" data-exec-node-hint></p></div>'
                + '<footer class="chat-supervision-dialog-footer"><div class="chat-supervision-errors" role="alert"></div>'
                + '<div class="chat-supervision-actions"><button type="button" data-exec-node-dismiss>Cancel</button>'
                + '<button type="submit" class="is-primary">Save</button></div></footer></form>';
            doc.body.appendChild(wrap);
            const form = wrap.querySelector('form');
            const select = form.nodeId;
            const cwdRow = form.querySelector('[data-exec-node-cwd]');
            const hintEl = form.querySelector('[data-exec-node-hint]');
            const sync = () => {
                const node = byId(select.value);
                cwdRow.hidden = !node || !!node.host;
                form.cwd.placeholder = M.cwdPlaceholder(node);
                hintEl.textContent = M.hint(node, typeof host.isWorking === 'function' && host.isWorking());
            };
            select.classList.add('chip-config-select');
            const combo = root.CuttleSearchableSelect
                ? root.CuttleSearchableSelect.enhance(select, { label: 'Execution node', floating: true, searchable: false })
                : null;
            select.addEventListener('change', sync);
            sync();
            const close = () => {
                if (combo) { try { combo.destroy(); } catch (_) {} }
                wrap.remove();
                if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
            };
            wrap.addEventListener('keydown', (ev) => {
                if (ev.key === 'Escape' && !wrap.querySelector('.cuttle-combo.is-open')) {
                    ev.preventDefault(); ev.stopPropagation(); close();
                }
            }, true);
            wrap.addEventListener('click', (ev) => {
                if (ev.target === wrap || (ev.target.closest && ev.target.closest('[data-exec-node-dismiss]'))) close();
            });
            form.addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const plan = M.plan(current, { nodeId: select.value, cwd: form.cwd.value });
                if (plan.kind === 'none') { close(); return; }
                if (draft) {
                    // No chat to PUT to yet: the pick rides the first message.
                    const picked = M.draft(nodes, plan.body.node_id, plan.body.cwd);
                    if (isDraft(session)) setPending(picked.node && !picked.node.host ? picked : null);
                    close();
                    host.toast('Agent runs on ' + M.nodeName(picked.node) + ' from the first message', 'success');
                    return;
                }
                const submit = form.querySelector('[type=submit]');
                const errBox = form.querySelector('.chat-supervision-errors');
                submit.disabled = true;
                try {
                    const saved = await call('PUT', '/api/workers/exec/chats/' + sid, plan.body);
                    if (sid === numericSession(session)) {
                        seq += 1;
                        binding = saved.binding;
                        render();
                    }
                    close();
                    host.toast('Agent runs on ' + M.nodeName(saved.binding.node) + ' from the next turn', 'success');
                } catch (e) {
                    errBox.textContent = String(e.message || e);
                    submit.disabled = false;
                }
            });
            const first = form.querySelector('.cuttle-combo-trigger') || select;
            if (first) first.focus();
        }

        function onClick(ev) {
            const menu = ev.target.closest && ev.target.closest('[data-exec-node-menu]');
            if (menu) {
                ev.preventDefault();
                ev.stopPropagation();
                host.closeMenus();
                openDialog(menu.getAttribute('data-exec-node-menu') || null);
                return;
            }
            const open = ev.target.closest && ev.target.closest('[data-exec-node-open]');
            if (!open) return;
            ev.preventDefault();
            openDialog();
        }

        function setEnabled(on) {
            on = !!on;
            if (on === enabled) return;
            enabled = on;
            if (timer) host.clearInterval(timer);
            timer = enabled ? host.setInterval(() => { if (doc.visibilityState !== 'hidden') refresh(); }, POLL_MS) : null;
            refresh();
            if (pending && typeof host.refreshHeader === 'function') host.refreshHeader();
        }

        /** First send of a new chat carries its pick: the server binds it as it mints the chat. */
        function attachToRequest(body) {
            if (!enabled || !pending || !isDraft(session) || !body || typeof body !== 'object') return;
            body.exec_node = { node_id: pending.node_id, cwd: pending.cwd };
        }

        async function syncFlag() {
            try {
                const flags = await host.enabledFlags();
                setEnabled(flags.has('exec_nodes'));
            } catch (_) {
                setEnabled(false);
            }
        }

        function setSession(sid) {
            if (String(sid || '') === String(session || '')) return;
            session = sid;
            binding = null;
            pending = null;
            render();
            refresh();
        }

        doc.addEventListener('click', onClick, true);
        root.addEventListener('message', (ev) => {
            if (ev.origin === root.location.origin && ev.data && ev.data.type === 'cuttle-experimental-flags-changed') {
                syncFlag();
            }
        });
        doc.addEventListener('visibilitychange', () => { if (doc.visibilityState === 'visible') refresh(); });
        syncFlag();

        return { setSession, refresh, menuHtml, openDialog, attachToRequest,
            draftNode: () => (enabled && isDraft(session) ? pending : null), isEnabled: () => enabled };
    }

    root.CuttleChatExecNode = { create };
})(typeof window !== 'undefined' ? window : globalThis);
