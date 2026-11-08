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
        let pauseMenuFor = null;

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

        // Tell the page whether this chat is under supervision (composer lock).
        // A failed poll reports nothing, so a network blip keeps the last answer.
        function reportSupervised(sid, on, by) {
            if (host.onSupervised) host.onSupervised(sid, on, by || '');
        }

        async function refresh() {
            const sid = numericSession(session);
            const mine = ++seq;
            if (!enabled || !sid) { overview = null; render(); reportSupervised(session, false); return; }
            try {
                const data = await call('GET', '/api/chat-ops/sessions/' + sid);
                if (mine === seq) { overview = data; render(); reportSupervised(session, M.isSupervised(data), M.supervisorOf(data)); }
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

        // `dot`: a status mark before the label (green ping = active, amber bars = paused).
        function pill(cls, id, label, title, kind, dot) {
            return '<button type="button" class="chat-supervision-pill ' + cls + '" data-sup-open="' + esc(kind)
                + '" data-sup-id="' + esc(id) + '" title="' + esc(title) + '" aria-haspopup="dialog">'
                + (dot ? '<span class="chat-supervision-dot" aria-hidden="true"></span>' : '')
                + '<span class="chat-supervision-pill-text">' + esc(label) + '</span></button>';
        }

        function stateClass(paused) { return paused ? ' is-paused' : ' is-active'; }

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
            v.chips.forEach((c) => parts.push(pill('is-supervisor' + stateClass(c.paused)
                + (c.violations.length ? ' is-warn' : ''), c.id, (c.violations.length ? '⚠ ' : '') + c.text,
            c.title + ' — click for details and controls', 'chip', true)));
            v.badges.forEach((b) => parts.push(pill('is-supervised' + stateClass(b.paused)
                + (b.violations.length ? ' is-warn' : ''), b.id,
            (b.violations.length ? '⚠ ' : '') + b.text,
            b.violations.length ? b.violations.join('\n') : b.title + ' — click for details', 'badge', true)));
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

        // Pausing asks for how long: a timed pause auto-resumes on the host, so
        // supervision cannot stop silently for days. "Until I resume" is last.
        function pauseMenuHtml(id) {
            return '<div class="chat-supervision-pause-menu" role="group" aria-label="Pause for how long">'
                + '<span class="chat-supervision-meta">Pause for how long?</span>'
                + M.PAUSE_CHOICES.map((c) => '<button type="button"'
                    + (c.primary ? ' class="is-primary"' : '') + ' data-sup-pause="' + esc(c.id)
                    + '" data-sup-id="' + esc(id) + '">' + esc(c.label) + '</button>').join('')
                + '</div>';
        }

        function controls(id, paused, openHandle, openLabel) {
            return '<div class="chat-supervision-actions">'
                + '<button type="button" data-sup-chat="' + esc(openHandle) + '">' + esc(openLabel) + '</button>'
                + '<button type="button" data-sup-act="edit" data-sup-id="' + esc(id) + '">Edit…</button>'
                + '<button type="button" data-sup-act="' + (paused ? 'resume' : 'pause-menu') + '" data-sup-id="' + esc(id) + '">'
                + (paused ? 'Resume now' : 'Pause…') + '</button>'
                + '<button type="button" class="is-danger" data-sup-act="cancel" data-sup-id="' + esc(id) + '">Cancel</button>'
                + '</div>'
                + (!paused && pauseMenuFor === id ? pauseMenuHtml(id) : '');
        }

        // Header (who + state), one status line (when), facts (how): each said once.
        function headHtml(title, paused, info) {
            return '<div class="chat-supervision-head">'
                + '<span class="chat-supervision-dot' + stateClass(paused) + '" aria-hidden="true"></span>'
                + '<span class="chat-supervision-title">' + esc(title) + '</span>'
                + (info ? '<button type="button" class="cuttle-info" data-tooltip="' + esc(info)
                    + '" aria-label="About supervision">i</button>' : '')
                + '<span class="chat-supervision-state' + stateClass(paused) + '">' + (paused ? 'Paused' : 'Active') + '</span>'
                + '</div>';
        }

        function detailsHtml(d, paused) {
            return '<div class="chat-supervision-status' + stateClass(paused) + '">' + esc(d.status)
                + (d.hint ? '<span class="chat-supervision-hint">' + esc(d.hint) + '</span>' : '') + '</div>'
                + (d.facts.length ? '<dl class="chat-supervision-facts">' + d.facts.map((f) => '<dt>' + esc(f.label)
                    + '</dt><dd>' + esc(f.value) + '</dd>').join('') + '</dl>' : '');
        }

        function violationsHtml(list) {
            return list.length ? '<div class="chat-supervision-violation">⚠ ' + list.map(esc).join('<br>⚠ ') + '</div>' : '';
        }

        const WAKE_INFO = 'Each check looks at the chat without spending a turn. The supervisor is only woken when '
            + 'the chat stops, errors, needs input, makes progress, stalls or breaks a branch rule.';

        function popoverContent(kind, id, v) {
            if (kind === 'chip') {
                const c = v.chips.find((x) => x.id === id);
                if (!c) return '';
                return headHtml(c.title, c.paused, WAKE_INFO) + detailsHtml(c.details, c.paused)
                    + violationsHtml(c.violations)
                    + '<div class="chat-supervision-section">Recent checks</div>' + historyHtml(c.history)
                    + controls(c.id, c.paused, c.target, 'Open ' + c.target);
            }
            if (kind === 'badge') {
                const b = v.badges.find((x) => x.id === id);
                if (!b) return '';
                return headHtml(b.title, b.paused, WAKE_INFO) + detailsHtml(b.details, b.paused)
                    + violationsHtml(b.violations)
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
            pauseMenuFor = null;
        }

        async function act(verb, id, choice) {
            if (verb === 'edit') {
                const sched = M.scheduleById(overview, id);
                closePopover();
                if (sched) openDialog(String(sched.target).replace(/^CH-0*/, ''), sched);
                return;
            }
            if (verb === 'pause-menu') {
                pauseMenuFor = pauseMenuFor === id ? null : id;
                render();
                return;
            }
            if (verb === 'cancel' && !host.confirm('Cancel this ' + (id && overview && (overview.queued || [])
                .some((q) => q.id === id) ? 'queued message' : 'supervision schedule') + '?')) return;
            const body = verb === 'pause' ? M.pauseBody(choice, host.now()) : undefined;
            try {
                await call('POST', '/api/chat-ops/operations/' + encodeURIComponent(id) + '/' + verb, body);
                host.toast(verb === 'cancel' ? 'Cancelled' : verb === 'pause'
                    ? (body && body.indefinite ? 'Supervision paused until you resume it'
                        : 'Supervision paused; it resumes by itself')
                    : 'Supervision resumed; checking now', 'success');
            } catch (e) {
                host.toast(String(e.message || e), 'error');
            }
            pauseMenuFor = null;
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
                const chosen = t.closest('[data-sup-pause]');
                if (chosen) {
                    act('pause', chosen.getAttribute('data-sup-id'), chosen.getAttribute('data-sup-pause'));
                    return;
                }
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

        async function openDialog(sessionId, schedule) {
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
            const d = M.dialogDefaults(defaults, schedule);
            const editing = !!d.editId;
            // Reviewer profiles (Apps → Agents) are optional: off with the orchestrator flag.
            const profiles = await host.fetch('/api/agent-profiles', { credentials: 'include' })
                .then((r) => r.json()).then((x) => (x && x.success && x.profiles) || []).catch(() => []);
            const doOptions = (cur) => [['wake', 'Wake the supervisor'], ['stop,wake', 'Stop the chat, wake the supervisor']]
                .concat(profiles.filter((pr) => pr && pr.id && pr.agent && pr.agent !== 'none').map((pr) => [
                    'supervisor:' + pr.id, 'Send to reviewer: ' + (pr.name || pr.id)]))
                .concat([[cur, cur]]).filter((o, i, all) => o[0] && all.findIndex((x) => x[0] === o[0]) === i)
                .map(([v, l]) => '<option value="' + esc(v) + '"' + (v === cur ? ' selected' : '') + '>' + esc(l) + '</option>').join('');
            const cpRow = (c) => {
                const f = d.checkpoints[c.id];
                const param = c.id === 'tests'
                    ? '<select name="cp_when_tests" aria-label="Which test runs">' + [['pass', 'that passes'], ['fail', 'that fails'], ['any', 'either way']]
                        .map(([v, l]) => '<option value="' + v + '"' + (v === f.when ? ' selected' : '') + '>' + l + '</option>').join('') + '</select>'
                    : c.id === 'repeat_failures'
                        ? '<input name="cp_n_repeat_failures" type="number" min="2" max="20" value="' + esc(f.n) + '" title="times in a row">'
                        : c.id === 'watched_paths'
                            ? '<input name="cp_regex_watched_paths" value="' + esc(f.regex || '') + '" placeholder="^(api|schema)/ or \\.sql$">' : '';
                return '<div class="chat-supervision-checkpoint"><label class="chat-supervision-task"><span>' + esc(c.label)
                    + '</span><input type="checkbox" role="switch" name="cp_on_' + c.id + '"' + (f.on ? ' checked' : '') + '></label>'
                    + '<div class="chat-supervision-checkpoint-opts">' + param + '<select name="cp_do_' + c.id
                    + '" aria-label="What happens">'
                    + doOptions(f.do) + '</select></div></div>';
            };
            const list = ((agents && agents.agents) || []).filter((a) => a && a.id && a.sticky !== false);
            const wrap = doc.createElement('div');
            wrap.className = 'chat-supervision-dialog-backdrop';
            wrap.innerHTML = '<form class="auth-modal chat-supervision-dialog" role="dialog" aria-modal="true" aria-label="'
                + (editing ? 'Edit supervision' : 'Supervise this chat') + '">'
                + '<header class="chat-supervision-dialog-header"><button type="button" class="auth-modal-close" data-sup-dismiss aria-label="Close">×</button>'
                + '<h3>' + (editing ? 'Edit supervision of ' : 'Supervise ') + esc(d.target) + '</h3>'
                + (editing ? '<p class="chat-supervision-meta">Changes apply to the running schedule (a paused one stays'
                    + ' paused). The supervisor gets one check-in saying what changed.</p>'
                    : (d.playbookName ? '<p class="chat-supervision-meta">Defaults from playbook <strong>' + esc(d.playbookName)
                        + '</strong>, which this chat runs.</p>' : '')
                    + '<p class="chat-supervision-meta">Creates a supervisor chat. The host checks ' + esc(d.target)
                + ' on the interval and wakes the supervisor only when this chat stops, errors, needs input,'
                + ' makes progress, or breaks a branch rule.</p>') + '</header><div class="chat-supervision-dialog-body"><div class="chat-supervision-fields">'
                + '<label>Supervisor agent<select name="agent">' + list.map((a) => '<option value="' + esc(a.id) + '"'
                    + (a.id === (d.agent || 'cursor') ? ' selected' : '') + '>' + esc(a.label || a.id) + '</option>').join('')
                + '</select></label>'
                + '<label>Model<select name="model"></select></label>'
                + '<label>Effort<select name="effort"></select></label>'
                + '<label>Check every<select name="every">' + M.INTERVALS.map((s) => '<option value="' + s + '"'
                    + (s === d.every ? ' selected' : '') + '>' + M.fmtInterval(s) + '</option>').join('') + '</select></label></div>'
                + (d.tasks.length ? '<fieldset class="chat-supervision-tasks"><legend>Tasks lists to follow</legend>'
                    + d.tasks.map((t) => '<label class="chat-supervision-task"><span>' + esc(t.title)
                        + ' <small>' + t.done + '/' + t.total
                        + (t.scope === 'project' ? ' · project list' : '') + '</small></span>'
                        + '<input type="checkbox" role="switch" name="taskIds" value="'
                        + esc(t.id) + '"' + (t.selected ? ' checked' : '') + '></label>').join('')
                    + '<p class="chat-supervision-meta">Lists this chat creates later are followed too.</p></fieldset>' : '')
                + '<fieldset class="chat-supervision-tasks"><legend>Also wake the supervisor when…</legend>'
                    + M.CHECKPOINTS.map(cpRow).join('')
                    + '<p class="chat-supervision-meta">On top of the interval and the usual stops (question, error, open'
                    + ' Tasks, branch rules). “Stop the chat” halts it until the supervisor or you send it on.</p></fieldset>'
                + '<label>Instructions<textarea name="instructions" rows="4">' + esc(d.instructions) + '</textarea></label>'
                + '<details class="chat-supervision-advanced"><summary>Branch rules and model guidance</summary>'
                + '<label>Must stay on branch<input name="requiredBranch" value="' + esc(d.requiredBranch)
                + '" placeholder="any"></label>'
                + '<label>Never touch branches<input name="forbidden" value="' + esc(d.forbidden) + '"></label>'
                + '<label>Worker models (optional)<input name="worker" value="' + esc(d.worker) + '" placeholder="e.g. cursor auto, codex terra medium"></label>'
                + '<label>Expert models (optional)<input name="expert" value="' + esc(d.expert) + '" placeholder="e.g. claude opus"></label></details></div>'
                + '<footer class="chat-supervision-dialog-footer"><div class="chat-supervision-errors" role="alert"></div>'
                + '<div class="chat-supervision-actions"><button type="button" data-sup-dismiss>Cancel</button>'
                + '<button type="submit" class="is-primary">' + (editing ? 'Save changes' : 'Start supervising')
                + '</button></div></footer></form>';
            const returnFocus = doc.activeElement;
            doc.body.appendChild(wrap);
            const form = wrap.querySelector('form');
            const combos = [];
            const close = () => {
                combos.forEach((c) => c.destroy());
                wrap.remove();
                if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
            };
            const rowFor = (id) => list.find((a) => a.id === id) || null;
            const fillSelect = (sel, items, keep) => {
                sel.innerHTML = '<option value="">CLI default</option>' + items.map((it) => '<option value="'
                    + esc(it.id) + '"' + (it.id === keep ? ' selected' : '') + '>' + esc(it.label) + '</option>').join('');
                sel.closest('label').hidden = !items.length;
            };
            const fillEffort = () => {
                const choices = M.pinChoices(rowFor(form.agent.value), form.model.value);
                fillSelect(form.effort, choices.efforts.map((e) => ({ id: e, label: e })), form.effort.value);
            };
            const fillPins = () => {
                const row = rowFor(form.agent.value);
                fillSelect(form.model, M.pinChoices(row, '').models, form.agent.value === d.agent ? d.model : '');
                fillEffort();
            };
            form.agent.addEventListener('change', fillPins);
            form.model.addEventListener('change', fillEffort);
            fillPins();
            if (d.effort && form.agent.value === d.agent) { form.effort.value = d.effort; }
            form.querySelectorAll('select').forEach((select) => {
                select.classList.add('chip-config-select');
                if (root.CuttleSearchableSelect) combos.push(root.CuttleSearchableSelect.enhance(select, {
                    label: (select.closest('label') ? select.closest('label').firstChild.textContent : '')
                        || select.getAttribute('aria-label') || '',
                    floating: true, searchable: select.name === 'agent' || select.name === 'model',
                }));
            });
            // Capture phase: a closed dropdown's trigger stops Escape from bubbling, so the
            // dialog must see it first. An open dropdown keeps Escape for itself.
            wrap.addEventListener('keydown', (ev) => {
                if (ev.key === 'Escape' && !wrap.querySelector('.cuttle-combo.is-open')) {
                    ev.preventDefault(); ev.stopPropagation(); close();
                    return;
                }
                if (ev.key !== 'Tab') return;
                const focusable = Array.from(form.querySelectorAll('button, input, textarea, select, summary'))
                    .filter((el) => !el.disabled && el.getClientRects().length);
                const first = focusable[0], last = focusable[focusable.length - 1];
                if (ev.shiftKey && doc.activeElement === first) { ev.preventDefault(); last.focus(); }
                else if (!ev.shiftKey && doc.activeElement === last) { ev.preventDefault(); first.focus(); }
            }, true);
            wrap.addEventListener('click', (ev) => {
                if (ev.target === wrap || (ev.target.closest && ev.target.closest('[data-sup-dismiss]'))) close();
            });
            form.addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const fd = new root.FormData(form);
                const payload = M.supervisePayload(Object.assign({}, d, {
                    agent: fd.get('agent'), model: fd.get('model'), effort: fd.get('effort'), every: Number(fd.get('every')), instructions: fd.get('instructions'),
                    requiredBranch: fd.get('requiredBranch'), forbidden: fd.get('forbidden'),
                    worker: fd.get('worker'), expert: fd.get('expert'), taskIds: fd.getAll('taskIds'),
                    checkpoints: Object.fromEntries(M.CHECKPOINTS.map((c) => [c.id, {
                        on: fd.get('cp_on_' + c.id) != null, do: fd.get('cp_do_' + c.id) || 'wake',
                        when: fd.get('cp_when_tests'), n: fd.get('cp_n_repeat_failures'),
                        regex: fd.get('cp_regex_watched_paths'),
                    }])),
                }));
                const errBox = form.querySelector('.chat-supervision-errors');
                if (!payload.ok) { errBox.textContent = payload.errors.join(' '); return; }
                const submit = form.querySelector('[type=submit]');
                submit.disabled = true;
                try {
                    if (editing) {
                        const res = await call('POST', '/api/chat-ops/operations/' + encodeURIComponent(d.editId)
                            + '/edit', payload.body);
                        close();
                        host.toast((res.updated || []).length ? 'Supervision updated' : 'No changes', 'success');
                    } else {
                        const res = await call('POST', '/api/chat-ops/supervise', payload.body);
                        close();
                        host.toast('Supervisor started: CH-' + String(res.operation.actor).padStart(6, '0'), 'success');
                    }
                    refresh();
                } catch (e) {
                    errBox.textContent = String(e.message || e);
                    submit.disabled = false;
                }
            });
            const first = form.querySelector('.cuttle-combo-trigger') || form.querySelector('select');
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
