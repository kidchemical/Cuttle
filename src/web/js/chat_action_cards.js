/* ================================================================
   Cuttle Chat — action-card effects controller (chat_action_cards.js).
   Owner: card activation/submission, action-card dismissal, lock and
   progress DOM effects, watch/restart loops, choice storage, adoption
   and linked-restart recovery for `.cuttle-action-form` cards.
   One instance is composed per chat root by the page
   (`actionCardsController().mount(containerEl)`).

   The page keeps every send lane, the history panel, the composer,
   and all message transport: they arrive as explicit `host`
   capabilities and the controller never reads page scope directly.
   Pure card model/watch interpretation stays in `CuttleChatActionForms`;
   identity matching reuses `CuttleChatActivity`. Server owns the
   HMAC signatures and the authoritative run locks.

   Lifetime model: each mount installs one explicit lifetime record on
   the card (`__cardsLife`: abort signal, alive flag, owned timers).
   All listeners bind to the lifetime signal and every poll/read fetch
   carries it, so disposal detaches handlers, rejects pending reads,
   and settles card-owned waits at once; stale continuations test their
   token and touch nothing, so an old loop can never clear or paint a
   successor lifetime's markers. One deliberate exception: the
   canonical followup-message write carries no abort signal — it must
   run to completion even if its claimant detaches, with replacement
   cards joining the same controller-owned in-flight request instead
   of POSTing again. That is in-flight coalescing during this
   controller lifetime only, not exactly-once delivery: the storage
   ack is recorded only after confirmed server persistence, and
   cross-reload delivery keeps the pre-existing ambiguous limitation
   (a reload before persistence re-offers; a reload after persistence
   may duplicate the write — server-side idempotency is separately
   owned).
   One root-scoped observer disposes cards removed from the root
   (same path as explicit disposal); nodes moved within the root stay
   contained and are preserved; re-mount installs one fresh lifetime
   and rewires cleanly. `destroy` makes every mounted card inert,
   stops the observer and the link poller, drops in-flight followup
   handles, and ignores later mounts.
   ================================================================ */
(function (root) {
    'use strict';


    function mountCards(chatRoot, host) {
        if (!chatRoot || typeof chatRoot.querySelectorAll !== 'function'
            || typeof chatRoot.contains !== 'function') {
            throw new Error(
                'CuttleChatActionCards.mountCards requires a chat root element');
        }
        host = host || {};
        const REQUIRED_HOST = ['request', 'sendAnswerText', 'resumeWithStatus',
            'sendControlCommand', 'context', 'notify',
            'syncMessages', 'publishRestartEvent', 'setHistoryAwaiting',
            'syncHistoryAwaiting', 'syncComposerStop', 'paintAssistantMessage',
            'escapeHtml'];
        const missingHost = REQUIRED_HOST.filter((k) => typeof host[k] !== 'function');
        const store = host.storage || {};
        if (typeof store.getItem !== 'function'
            || typeof store.setItem !== 'function'
            || typeof store.removeItem !== 'function') {
            missingHost.push('storage.getItem/setItem/removeItem');
        }
        if (missingHost.length) {
            throw new Error(
                'CuttleChatActionCards.mountCards missing host capabilities: '
                + missingHost.join(', '));
        }
        let destroyed = false;
        const mountedCards = new Set();
        let linkPoll = null;
        let linkPollCtrl = null;
        try { linkPollCtrl = new AbortController(); } catch (_) { linkPollCtrl = null; }

        // Context composition errors fail loudly; the controller never
        // invents a session, project, or auth mode.
        function currentCtx() {
            return host.context() || {};
        }

        // One explicit lifetime per mount. Disposal aborts the signal
        // (pending fetches reject, signal-bound listeners detach), settles
        // pending waits, and clears timers; the record below is the only
        // per-card state the controller keeps — never lock/choice/busy
        // copies. Old continuations test their token and touch nothing.
        function newLifetime() {
            const abort = new AbortController();
            return { abort: abort, signal: abort.signal, alive: true, timers: new Set() };
        }

        function isCurrent(card, life) {
            return !!life && !!card && life.alive && card.__cardsLife === life
                && !destroyed && chatRoot.contains(card);
        }

        function clearLife(card, life) {
            if (!life || !life.alive) return;
            life.alive = false;
            try { life.abort.abort(); } catch (_) {}
            life.timers.forEach((t) => {
                try { clearTimeout(t); } catch (_) {}
                try { clearInterval(t); } catch (_) {}
            });
            life.timers.clear();
            if (card && card.__cardsLife === life) {
                card.__cardsLife = null;
                card.__watchLoop = false;
                card.__restartWatchId = null;
                card.__busy = false;
                if (card.__watchElapsedTimer) {
                    try { clearInterval(card.__watchElapsedTimer); } catch (_) {}
                    card.__watchElapsedTimer = null;
                }
            }
            mountedCards.delete(card);
        }

        function disposeCard(card) {
            if (!card) return;
            clearLife(card, card.__cardsLife);
        }

        function maybeDisposeRemoved(node) {
            if (!node || !(node.__cardsLife && node.__cardsLife.alive)) return;
            // A move within the root fires remove+insert together, so the
            // node is still contained: preserve it, never tear it down.
            try { if (chatRoot.contains(node)) return; } catch (_) { return; }
            disposeCard(node);
        }

        function watchRemovals() {
            try {
                const obs = new MutationObserver((records) => {
                    if (destroyed) return;
                    records.forEach((rec) => {
                        ((rec && rec.removedNodes) || []).forEach((node) => {
                            if (!node || node.nodeType !== 1) return;
                            if (node.matches && node.matches('.cuttle-action-form')) {
                                maybeDisposeRemoved(node);
                            }
                            const inner = node.querySelectorAll
                                ? node.querySelectorAll('.cuttle-action-form')
                                : [];
                            Array.prototype.forEach.call(inner, maybeDisposeRemoved);
                        });
                    });
                });
                obs.observe(chatRoot, { childList: true, subtree: true });
                return obs;
            } catch (_) { return null; }
        }

        const observer = watchRemovals();

        function destroy() {
            if (destroyed) return;
            destroyed = true;
            try { if (observer) observer.disconnect(); } catch (_) {}
            if (linkPoll) {
                try { clearInterval(linkPoll); } catch (_) {}
                linkPoll = null;
            }
            try { if (linkPollCtrl) linkPollCtrl.abort(); } catch (_) {}
            Array.from(mountedCards).forEach(disposeCard);
            mountedCards.clear();
            // Joined continuations test their token and touch nothing,
            // so dropping the handle only frees the key.
            try { followupFlights.clear(); } catch (_) {}
        }

        function linkPollSignal() {
            try { return linkPollCtrl ? linkPollCtrl.signal : undefined; } catch (_) { return undefined; }
        }

        // Card-owned cancellable wait: disposal settles the promise as
        // false immediately instead of leaving the loop asleep. No new
        // timeout policy — same cadences the page used, same owners.
        function cardWait(card, life, ms) {
            return new Promise((resolve) => {
                if (!isCurrent(card, life)) { resolve(false); return; }
                const done = (v) => {
                    life.timers.delete(t);
                    try { life.signal.removeEventListener('abort', onAbort); } catch (_) {}
                    resolve(v);
                };
                const onAbort = () => {
                    try { clearTimeout(t); } catch (_) {}
                    done(false);
                };
                const t = setTimeout(() => done(isCurrent(card, life)), ms);
                life.timers.add(t);
                try {
                    life.signal.addEventListener('abort', onAbort, { once: true });
                } catch (_) {}
            });
        }


            const composeActionFormContent = (card, fields) => {
                let spec = {};
                try { spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {}; } catch (_) {}
                const defs = Array.isArray(spec.fields) ? spec.fields : [];
                const title = String((fields && fields.title) || '').trim();
                const lines = [];
                const missing = [];
                card.querySelectorAll('input[type="checkbox"][data-field-id]').forEach((el) => {
                    if (!el.checked) return;
                    const fid = el.getAttribute('data-field-id') || '';
                    if (fid === 'title' || fid === 'channel') return;
                    let line = String(el.getAttribute('data-line') || '').trim();
                    if (!line) {
                        const def = defs.find((f) => String(f.id) === String(fid));
                        line = String((def && (def.line || def.body)) || '').trim();
                    }
                    if (!line) {
                        const lab = (el.closest('label') && el.closest('label').textContent) || '';
                        missing.push(String(lab).trim() || fid);
                        return;
                    }
                    lines.push(line);
                });
                return {
                    content: lines.length ? (title ? title + '\n\n' : '') + lines.join('\n') : '',
                    missing,
                };
            };
        function mountCard(card) {
            if (!card || destroyed) return;
            // One fresh lifetime per mount: disposal aborts the signal, so
            // every listener below detaches with the lifetime and a later
            // re-mount rewires cleanly. A live lifetime means already wired.
            if (card.__cardsLife && card.__cardsLife.alive) return;
            const life = newLifetime();
            card.__cardsLife = life;
            mountedCards.add(card);

            // Collapsed cards stay expandable, so wire this before the
            // already-consumed bail-out below.
            const toggleEl = card.querySelector('[data-action-form-toggle]');
            if (toggleEl) {
                toggleEl.addEventListener('click', () => {
                    if (!card.classList.contains('is-collapsible')) return;
                    const nowCollapsed = card.classList.toggle('is-collapsed');
                    toggleEl.setAttribute('aria-expanded', nowCollapsed ? 'false' : 'true');
                }, { signal: life.signal });
            }
            if (resumeCard(card, life)) return;

            const statusEl = card.querySelector('.cuttle-action-form-status');
            const lockMode = (card.getAttribute('data-lock') || 'form').toLowerCase();

            const setStatus = (text, ok) => {
                if (!statusEl) return;
                statusEl.hidden = !text;
                statusEl.textContent = text || '';
                statusEl.classList.toggle('is-error', ok === false);
                statusEl.classList.toggle('is-ok', ok === true);
            };

            const lockCard = (selectedIds, summary) => {
                collapseLockedActionForm(card, selectedIds, summary);
            };

            const lockField = (optionId) => {
                const hit = card.querySelector(
                    `[data-action-form-option="${CSS.escape(String(optionId))}"]`
                );
                if (!hit) return;
                if (hit.tagName === 'INPUT') {
                    hit.disabled = true;
                    const lab = hit.closest('label');
                    if (lab) lab.classList.add('is-selected');
                } else {
                    hit.disabled = true;
                    hit.classList.add('is-selected');
                }
            };

            const tokenForCard = () => {
                const fallback = (card.getAttribute('data-fallback') || '').trim();
                const formId = (card.getAttribute('data-form-id') || '').trim();
                if (fallback && formId) return formId + ' ' + fallback;
                return fallback || formId;
            };

            const runSubmission = async (selection) => {
                // Lifetime first: a disposed card (listeners are already
                // detached, but a queued handler may still fire) starts
                // nothing and touches nothing.
                const life = card.__cardsLife;
                if (!isCurrent(card, life)) return;
                if (card.classList.contains('cuttle-action-form--locked')) return;
                if (card.getAttribute('data-restart-pending-sync') === '1') return;
                if (card.__busy) return;
                card.__busy = true;
                setStatus('Running…', null);
                try {
                    // Programmatic native restart / dismiss — never route button
                    // labels through the agent router.
                    try {
                        const spec0 = JSON.parse(card.getAttribute('data-spec') || '{}');
                        const ids0 = selection.options
                            || (selection.option ? [selection.option] : []);
                        const opts0 = Array.isArray(spec0.options) ? spec0.options : [];
                        const hit0 = opts0.find((o) => ids0.includes(String(o.id))
                            || String(o.id) === String(selection.option));
                        if (hit0 && String(hit0.action || '') === '__dismiss__') {
                            setStatus('Dismissed.', true);
                            card.setAttribute('data-locked', '1');
                            lockCard(ids0, 'Dismissed.');
                            return;
                        }
                        if (hit0 && CuttleChatActionForms.isWatchFormAction(hit0.action)) {
                            if (String(hit0.action) === '__watch_resume__'
                                || String(hit0.action) === '__watch_park__') {
                                rememberActionFormWatchChoice(
                                    card,
                                    String(hit0.action) === '__watch_resume__' ? 'resume' : 'park'
                                );
                            }
                            // Fall through to POST /api/action-form/run so the
                            // lock is persisted and other devices collapse too.
                        } else if (hit0 && String(hit0.action || '') === '__native_restart__') {
                            const mode = String((hit0.params || {}).mode || 'status');
                            const msg = '/restart ' + mode;
                            await host.sendControlCommand(msg);
                            // The control lane awaited: a card disposed
                            // mid-send paints, locks, and toasts nothing.
                            if (!isCurrent(card, life)) return;
                            const soft = 'Restart command sent: ' + mode;
                            setStatus(soft, true);
                            host.notify(soft, 'success');
                            card.setAttribute('data-locked', '1');
                            lockCard(ids0, soft);
                            return;
                        }
                    } catch (_) {}
                    const tokenParts = tokenForCard().split(/\s+/).filter(Boolean);
                    // Prefer a still-valid pending id or a server-signed
                    // inline token. Never mint unsigned inline.* in the
                    // browser — HMAC lives only on the server.
                    let token = tokenParts.find((t) => t.startsWith('inline.')) || tokenParts[0] || '';
                    let specFromCard = null;
                    try {
                        specFromCard = JSON.parse(card.getAttribute('data-spec') || 'null');
                        if (specFromCard && typeof specFromCard !== 'object') specFromCard = null;
                    } catch (_) {
                        specFromCard = null;
                    }
                    if (specFromCard && !specFromCard.project_path && currentCtx().projectPath) {
                        specFromCard.project_path = String(currentCtx().projectPath);
                    }
                    if (!token && !specFromCard) {
                        const msg = 'Form token missing — refresh the chat or ask for a new restart card.';
                        setStatus(msg, false);
                        host.notify(msg, 'error');
                        return;
                    }
                    // The card's own chat, not the pane's current one: a
                    // stale global used to send restart acks (and the user)
                    // into an unrelated chat.
                    const cardSession = (card.getAttribute('data-session-id') || '').trim();
                    const body = {
                        token,
                        selection,
                        session_id: cardSession || currentCtx().sessionId,
                        form_id: (card.getAttribute('data-form-id') || '').trim() || undefined,
                        project_path: currentCtx().projectPath,
                    };
                    if (specFromCard) body.spec = specFromCard;
                    const resp = await host.request('/api/action-form/run', {
                        options: {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body),
                        },
                        signal: life.signal,
                    });
                    // A disposed card applies nothing from a late response —
                    // locks, paints, resumes, and restart adoption all stop.
                    if (!isCurrent(card, life)) return;
                    const data = await resp.json().catch(() => ({}));
                    if (!isCurrent(card, life)) return;
                    const ok = !!(data && data.success);
                    const pushSummary = !ok && host.showPushFailure ? host.showPushFailure(data || {}) : '';
                    const toast = pushSummary || (data && data.toast) || (ok ? 'Done.' : 'Failed.');
                    const restartId = ok && data && data.flask_restart
                        ? String(data.flask_restart.restart_id || '')
                        : '';
                    const watchAct = ((data && data.actions) || []).find((a) => CuttleChatActionForms.isWatchFormAction(a)) || '';
                    const alreadyLocked = !!(data && data.already_locked);
                    if (!restartId && !watchAct && !alreadyLocked) {
                        host.notify(toast, ok ? 'success' : 'error');
                        setStatus(toast, ok);
                    } else if (watchAct || alreadyLocked) {
                        setStatus(toast, ok || alreadyLocked);
                    }
                    const selected = (data && data.selected) || selection.options || (selection.option ? [selection.option] : []);
                    const effectiveLock = (data && data.lock) || lockMode;
                    const reusable = !!(data && data.reusable);
                    // One-shot: lock on cancel or successful run — not on errors
                    // (so a bad project path can be fixed and retried).
                    if (alreadyLocked || (!reusable && (selection.cancel || (ok && effectiveLock === 'form')))) {
                        card.setAttribute('data-locked', '1');
                        lockCard(selected, toast);
                    } else if (ok && effectiveLock === 'field') {
                        (selected || []).forEach((id) => lockField(id));
                    }
                    // This send is the only writer of the answer bubble —
                    // /api/action-form/run no longer persists its own copy.
                    const resumeSid = String((data && data.session_id) || cardSession || '')
                        .replace(/^db_session_/, '');
                    const openSid = String(currentCtx().sessionId || '').replace(/^db_session_/, '');
                    if (data && data.should_resume && data.injected_user_message
                        && (!resumeSid || !openSid || resumeSid === openSid)) {
                        host.sendAnswerText({ text: String(data.injected_user_message) });
                    }
                    if (restartId) {
                        // Card owns the progress from here — no toast, no bubble.
                        paintFlaskRestartProgress(card, data.flask_restart);
                        watchFlaskRestartOnCard(card, restartId, { quietStart: true, initialStatus: data.flask_restart });
                        broadcastLinkedFlaskRestart({
                            formId: (card.getAttribute('data-form-id') || '').trim(),
                            restartId,
                            selected,
                            toast: toast || 'Restarting Flask…',
                            status: data.flask_restart,
                        });
                    } else if (ok && watchAct === '__watch_cancel__') {
                        setActionFormCardProgress(card, toast, 'ok');
                        applyActionFormWatchProgress(card, {
                            ...(card.__watchLastData || {}),
                            state: 'failed',
                            label: 'Cancelled',
                        });
                        host.syncComposerStop();
                    } else if (ok && (watchAct === '__watch_resume__' || watchAct === '__watch_park__')) {
                        setActionFormCardProgress(card, toast, 'pending');
                        watchActionFormJob(card, { restoreChoice: false });
                    }
                } catch (err) {
                    // Disposal (including an aborted POST) ends here with
                    // no late status, toast, lock, or adoption.
                    if (!isCurrent(card, life)) return;
                    const msg = (err && err.message) || 'Request failed';
                    // Self-restart can drop the socket after scheduling — treat as OK.
                    let restartish = false;
                    try {
                        const spec = JSON.parse(card.getAttribute('data-spec') || '{}');
                        const ids = selection.options
                            || (selection.option ? [selection.option] : []);
                        const opts = Array.isArray(spec.options) ? spec.options : [];
                        restartish = ids.some((id) => {
                            const hit = opts.find((o) => String(o.id) === String(id));
                            return hit && String(hit.action || '') === 'flask.restart';
                        });
                    } catch (_) {}
                    if (restartish && /failed to fetch|networkerror|load failed/i.test(msg)) {
                        const soft = 'Restarting Flask…';
                        card.setAttribute('data-locked', '1');
                        lockCard(
                            selection.options || (selection.option ? [selection.option] : []),
                            soft
                        );
                        setActionFormCardProgress(card, soft, 'pending');
                        adoptInFlightRestartOnCard(card);
                    } else {
                        setStatus(msg, false);
                        host.notify(msg, 'error');
                    }
                } finally {
                    // Never clear a successor lifetime's busy flag.
                    if (card.__cardsLife === life) card.__busy = false;
                }
            };

            card.querySelectorAll('[data-action-form-option]').forEach((el) => {
                if (el.tagName === 'INPUT') return; // multi checkboxes
                el.addEventListener('click', () => {
                    const oid = el.getAttribute('data-action-form-option') || '';
                    // Prefer data-spec over the cancel attr: older cards marked
                    // every no-action Q&A option as cancel and showed "Cancelled."
                    let isCancel = el.getAttribute('data-action-form-cancel') === '1';
                    try {
                        const spec = JSON.parse(card.getAttribute('data-spec') || '{}');
                        const opts = Array.isArray(spec.options) ? spec.options : [];
                        const hit = opts.find((o) => String(o.id) === String(oid));
                        if (hit) isCancel = CuttleChatActionForms.isExplicitActionFormCancelOption(hit);
                    } catch (_) {}
                    if (isCancel) {
                        runSubmission({ cancel: true, option: oid, options: [oid] });
                    } else {
                        runSubmission({ option: oid, options: [oid] });
                    }
                }, { signal: life.signal });
            });

            const submitBtn = card.querySelector('[data-action-form-submit]');
            if (submitBtn) {
                submitBtn.addEventListener('click', () => {
                    const modeWrap = card.querySelector('[data-mode="multi"]');
                    if (modeWrap) {
                        const opts = [];
                        modeWrap.querySelectorAll('input[data-action-form-option]:checked').forEach((inp) => {
                            opts.push(inp.getAttribute('data-action-form-option'));
                        });
                        runSubmission({ options: opts });
                        return;
                    }
                    // form mode — collect fields
                    const fields = {};
                    card.querySelectorAll('[data-field-id]').forEach((el) => {
                        const fid = el.getAttribute('data-field-id');
                        if (!fid) return;
                        if (el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'checkbox') {
                            fields[fid] = !!el.checked;
                        } else {
                            fields[fid] = el.value ?? '';
                        }
                    });
                    card.querySelectorAll('[data-radio-group]').forEach((wrap) => {
                        const fid = wrap.getAttribute('data-radio-group');
                        if (!fid) return;
                        const chosen = wrap.querySelector('input[type="radio"]:checked');
                        fields[fid] = chosen ? chosen.value : '';
                    });
                    card.querySelectorAll('[data-checkbox-group]').forEach((wrap) => {
                        const fid = wrap.getAttribute('data-checkbox-group');
                        if (!fid) return;
                        fields[fid] = Array.from(wrap.querySelectorAll('input[type="checkbox"]:checked'))
                            .map((inp) => inp.value);
                    });
                    let cardSpec = {};
                    try { cardSpec = JSON.parse(card.getAttribute('data-spec') || '{}') || {}; } catch (_) {}
                    if (!CuttleChatActionForms.actionFormHasSideEffect(cardSpec)) {
                        runSubmission({ fields });
                        return;
                    }
                    const composed = composeActionFormContent(card, fields);
                    if (composed.missing.length) {
                        const msg = 'No post text for: ' + composed.missing.join(', ')
                            + '. Ask the agent to resend the form with a body for each item.';
                        setStatus(msg, false);
                        host.notify(msg, 'error');
                        return;
                    }
                    const selection = { fields };
                    if (composed.content) {
                        fields.content = composed.content;
                        selection.contentSource = 'lines';
                    }
                    runSubmission(selection);
                }, { signal: life.signal });
            }

            const cancelBtn = card.querySelector('[data-action-form-cancel="1"]:not([data-action-form-option])');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', () => {
                    runSubmission({ cancel: true });
                }, { signal: life.signal });
            }
        }
        function resumeCard(card, life) {
            if (!isCurrent(card, life)) return true;
            if (card.getAttribute('data-locked') === '1'
                || card.classList.contains('cuttle-action-form--locked')) {
                // Consumed card: no handlers, but a restart it started may
                // still be in flight (reload / reconnect during downtime).
                const pendingRestart = (card.getAttribute('data-restart-id') || '').trim();
                if (pendingRestart) {
                    const toast = (card.querySelector('.cuttle-action-form-summary-text') || {}).textContent || '';
                    const short = toast.includes('—') ? toast.split('—').slice(1).join('—').trim() : 'Restarting Flask…';
                    setActionFormCardProgress(card, short || 'Restarting Flask…', 'pending');
                    watchFlaskRestartOnCard(card, pendingRestart, { quietStart: true });
                }
                watchActionFormJob(card, { restoreChoice: true });
                return true;
            }
            watchActionFormJob(card, { restoreChoice: true });
            return false;
        }
        function mount(containerEl) {
            if (destroyed || !containerEl || !containerEl.querySelectorAll) return;
            containerEl.querySelectorAll('.cuttle-action-form').forEach(mountCard);
            ensureLinkPoller();
            try { host.syncHistoryAwaiting(); } catch (_) {}
        }
        function collapseLockedActionForm(card, selectedIds, summary) {
            if (!card) return;
            card.removeAttribute('data-restart-pending-sync');
            const optionLabelFor = (id) => {
                const hit = card.querySelector(
                    `[data-action-form-option="${CSS.escape(String(id))}"]`
                );
                const text = hit
                    ? (hit.tagName === 'INPUT'
                        ? (hit.closest('label') || {}).textContent
                        : hit.textContent)
                    : '';
                return String(text || id).trim() || String(id);
            };
            const text = String(summary || '').trim()
                || ((selectedIds || []).length
                    ? 'Used — ' + selectedIds.map(optionLabelFor).join(', ')
                    : 'Used');
            const titleText = (card.querySelector('.cuttle-action-form-title') || {}).textContent || '';
            const out = card.querySelector('.cuttle-action-form-summary-text');
            if (out) out.textContent = (titleText ? titleText.trim() + ' — ' : '') + text;
            const cancelled = /^(cancell?ed|ignored)\b/i.test(text);
            const icon = card.querySelector('.cuttle-action-form-summary-icon');
            if (icon) icon.textContent = cancelled ? '✕' : '✓';
            card.classList.toggle('is-cancelled', cancelled);
            card.classList.remove('is-pending');
            card.classList.add('cuttle-action-form--locked', 'is-collapsible', 'is-collapsed');
            card.setAttribute('data-locked', '1');
            card.querySelectorAll('.cuttle-action-form-body button, input, textarea, select')
                .forEach((el) => { el.disabled = true; });
            if (Array.isArray(selectedIds)) {
                selectedIds.forEach((id) => {
                    const hit = card.querySelector(
                        `[data-action-form-option="${CSS.escape(String(id))}"]`
                    );
                    if (hit) {
                        const btn = hit.tagName === 'INPUT' ? hit.closest('label') : hit;
                        if (btn) btn.classList.add('is-selected');
                    }
                });
            }
            const toggleEl = card.querySelector('[data-action-form-toggle]');
            if (toggleEl) toggleEl.setAttribute('aria-expanded', 'false');
        }
        function unlockFlaskRestartPendingSyncCard(card) {
            if (!card || card.getAttribute('data-restart-pending-sync') !== '1') return;
            if (card.getAttribute('data-locked') === '1'
                || card.classList.contains('cuttle-action-form--locked')) {
                card.removeAttribute('data-restart-pending-sync');
                return;
            }
            card.removeAttribute('data-restart-pending-sync');
            card.classList.remove('is-collapsible', 'is-collapsed', 'is-pending');
            const titleText = String(
                (card.querySelector('.cuttle-action-form-title') || {}).textContent || ''
            ).trim() || 'Restart Flask';
            const out = card.querySelector('.cuttle-action-form-summary-text');
            if (out) out.textContent = titleText;
            const icon = card.querySelector('.cuttle-action-form-summary-icon');
            if (icon) icon.textContent = '';
            card.querySelectorAll('.cuttle-action-form-body button, input, textarea, select')
                .forEach((el) => { el.disabled = false; });
            const toggleEl = card.querySelector('[data-action-form-toggle]');
            if (toggleEl) toggleEl.setAttribute('aria-expanded', 'true');
        }
        function actionFormWatchSpec(card) {
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                return CuttleChatActionForms.inferActionFormWatch(spec);
            } catch (_) {
                return null;
            }
        }
        // Owned by chat_action_forms.js — DOM gather, domain decides.
        function actionFormWatchStorageKey(card) {
            const formId = String(card.getAttribute('data-form-id') || '').trim();
            const watch = actionFormWatchSpec(card) || {};
            const sid = String(card.getAttribute('data-session-id') || currentCtx().sessionId || '').trim();
            return CuttleChatActionForms.actionFormWatchStorageKey({ formId, watch, sessionId: sid });
        }
        function rememberActionFormWatchChoice(card, mode) {
            try {
                host.storage.setItem(actionFormWatchStorageKey(card), JSON.stringify({
                    mode: String(mode || ''),
                    resumed: false,
                }));
            } catch (_) {}
        }

        function loadActionFormWatchChoice(card) {
            try {
                const raw = host.storage.getItem(actionFormWatchStorageKey(card));
                return raw ? (JSON.parse(raw) || {}) : {};
            } catch (_) {
                return {};
            }
        }

        function markActionFormWatchResumed(card) {
            try {
                const prev = loadActionFormWatchChoice(card);
                host.storage.setItem(actionFormWatchStorageKey(card), JSON.stringify({
                    mode: prev.mode || 'resume',
                    resumed: true,
                }));
            } catch (_) {}
        }
        function watchCardsSharingUrl(card, url) {
            const root = (card && card.closest('#chatMessages, .chat-messages')) || chatRoot;
            return Array.prototype.filter.call(root.querySelectorAll('.cuttle-action-form'), (c) => {
                const w = actionFormWatchSpec(c);
                return !!(w && CuttleChatActionForms.safeActionFormWatchUrl(w.url) === url);
            });
        }

        function isLiveWatchCardForUrl(card, url) {
            const same = watchCardsSharingUrl(card, url);
            return same.length <= 1 || same[same.length - 1] === card;
        }

        function watchStatusMatchesCard(card, data) {
            const watch = actionFormWatchSpec(card) || {};
            const bind = CuttleChatActionForms.cardWatchBind(watch);
            const key = CuttleChatActionForms.watchRunKey(data);
            if (bind) return !!(key && key === bind);
            // Legacy cards (no stamp): only the newest form with this URL follows live status.
            const url = CuttleChatActionForms.safeActionFormWatchUrl(watch.url);
            if (!url) return false;
            return isLiveWatchCardForUrl(card, url);
        }
        function lockWatchFormCard(card, summary, failed) {
            if (!card || card.getAttribute('data-locked') === '1') return;
            let shouldLock = true;
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                const lockMode = String(spec.lock || card.getAttribute('data-lock') || 'form').toLowerCase();
                shouldLock = !spec.reusable && lockMode === 'form';
            } catch (_) {}
            if (!shouldLock) return;
            const text = String(summary || '').trim() || (failed ? 'Failed' : 'Finished');
            collapseLockedActionForm(card, [], text);
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                spec.locked = true;
                if (text) spec.toast = text;
                card.setAttribute('data-spec', JSON.stringify(spec));
            } catch (_) {}
        }
        function persistActionFormWatchSnapshot(card, data, terminal) {
            if (!card || !data || typeof data !== 'object') return;
            if (!data.state && !data.started_at && !data.run_id && !data.label && !data.bars) return;
            const snapshot = {
                state: String(data.state || ''),
                percent: Number(data.percent != null ? data.percent : 0),
                label: String(data.label || data.file || ''),
                version: String(data.version || ''),
                elapsed: String(data.elapsed || ''),
                elapsed_sec: Number(data.elapsed_sec != null ? data.elapsed_sec : 0),
                started_at: String(data.started_at || ''),
                run_id: String(data.run_id || ''),
                action: String(data.action || ''),
                detail: String(data.detail || ''),
            };
            if (Array.isArray(data.bars) && data.bars.length) {
                snapshot.bars = data.bars;
            }
            if (data.grid) snapshot.grid = data.grid;
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                spec.watch = spec.watch && typeof spec.watch === 'object' ? spec.watch : {};
                spec.watch.snapshot = snapshot;
                spec.watch.terminal = !!terminal;
                if (terminal && snapshot.label) spec.toast = snapshot.label;
                if (terminal) {
                    const lockMode = String(spec.lock || card.getAttribute('data-lock') || 'form').toLowerCase();
                    if (!spec.reusable && lockMode === 'form') spec.locked = true;
                }
                if (snapshot.started_at && !spec.watch.started_at) spec.watch.started_at = snapshot.started_at;
                if (snapshot.run_id && !spec.watch.run_id) spec.watch.run_id = snapshot.run_id;
                if (snapshot.action && !spec.watch.action) spec.watch.action = snapshot.action;
                card.setAttribute('data-spec', JSON.stringify(spec));
            } catch (_) {}
            const formId = String(card.getAttribute('data-form-id') || '').trim();
            const sid = String(card.getAttribute('data-session-id') || currentCtx().sessionId || '').trim();
            if (!formId || !sid) return;
            let shouldLock = false;
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                shouldLock = !!(terminal && spec.locked);
            } catch (_) {}
            const life = card.__cardsLife;
            host.request('/api/action-form/watch-state', {
                options: {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        form_id: formId,
                        session_id: sid,
                        snapshot,
                        terminal: !!terminal,
                        lock: shouldLock,
                        toast: terminal ? snapshot.label : '',
                    }),
                },
                signal: life ? life.signal : undefined,
            }).catch(() => {});
        }
        // Bounded in-flight followup writes, keyed by logical followup
        // (session + build/time). The map is the coalescing point: the
        // first offer starts exactly one canonical POST and every later
        // offer for the same key joins that same promise — a replacement
        // node never POSTs twice. No persistent response cache, no
        // polling loop, no timeout-based loss: a slow POST delivers
        // whenever it completes. The write deliberately carries no
        // lifetime abort signal (see the header): it must run to
        // completion even if its claimant detaches. Detached
        // continuations paint nothing; only confirmed server success
        // records the storage ack, and a replacement recovers the
        // canonical server row through host.syncMessages().
        //
        // Storage holds one marker, never content: '1' is the ack,
        // written only after confirmed server persistence. There is
        // deliberately no durable pre-request claim: a reload that kills
        // the request leaves no marker, so a later offer retries — which
        // preserves the original ambiguous cross-reload limitation (the
        // retried write may duplicate the server row; server-side
        // idempotency is a separately owned problem, out of this slice).
        // All three storage calls are guarded; only setItem is known to
        // throw in the wild (quota / private mode), which the tests model
        // by throwing on setItem alone — getItem/removeItem stay
        // delegating. A failed ack write leaves the in-memory flight as
        // the single coalescing point for concurrent offers.
        const followupFlights = new Map();
        async function offerJobSuccessDiscordForm(card, data) {
            const spec = data && data.discord_form;
            if (!spec || typeof spec !== 'object') return;
            const life = card.__cardsLife;
            if (!isCurrent(card, life)) return;
            if (card.__discordFollowup) return;
            card.__discordFollowup = true;
            const sid = String(card.getAttribute('data-session-id') || currentCtx().sessionId || '');
            const key = 'cuttle.discordFollowup.' + sid + '.' + String((data && (data.build_id || data.time)) || '');
            let mark = null;
            try { mark = host.storage.getItem(key); } catch (_) { mark = null; }
            if (mark === '1') {
                // Already delivered by an earlier claimant: recover the
                // canonical server row, never paint a cached copy.
                if (isCurrent(card, life)) {
                    try { await host.syncMessages(); } catch (_) {}
                }
                return;
            }
            let flight = followupFlights.get(key);
            if (!flight) {
                const preface = 'Steam upload succeeded. Confirm the Discord note below, or Cancel to skip.';
                const fallback = preface + '\n\n<cuttle_action_form>\n' + JSON.stringify(spec) + '\n</cuttle_action_form>';
                flight = { owner: card, content: fallback, persisted: false,
                           fallbackPainted: false, promise: null };
                followupFlights.set(key, flight);
                flight.promise = (async () => {
                    try {
                        const resp = await host.request('/api/action-form/followup-message', {
                            options: {
                                method: 'POST',
                                credentials: 'include',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    spec,
                                    preface,
                                    session_id: sid,
                                    project_path: (currentCtx().projectPath || ''),
                                }),
                            },
                        });
                        const j = await resp.json();
                        if (j && j.success && j.response) {
                            flight.content = j.response;
                            flight.persisted = true;
                        }
                    } catch (_) {
                        flight.persisted = false;
                    }
                    // The ack is the only durable write, and only on
                    // confirmed success. Failure records nothing, so a
                    // later live offer retries with a new flight.
                    if (flight.persisted) {
                        try { host.storage.setItem(key, '1'); } catch (_) {}
                    }
                    return flight;
                })();
            }
            let done = null;
            try {
                done = await flight.promise;
            } catch (_) {
                done = null;
            }
            // Settle the handle first (only if still ours): success is
            // recorded in the ack, failure recorded nothing, so later
            // offers never need this entry — the map stays bounded to
            // in-flight keys and a detached continuation claims nothing.
            if (followupFlights.get(key) === flight) followupFlights.delete(key);
            if (!done || !done.persisted) {
                // Confirmed failure: exactly one live fallback paint per
                // flight; a later retry owns a new flight (and its paint).
                if (!isCurrent(card, life)) return;
                if (flight && !flight.fallbackPainted) {
                    flight.fallbackPainted = true;
                    host.paintAssistantMessage(flight.content);
                }
                return;
            }
            if (!isCurrent(card, life)) return;
            if (flight.owner === card) {
                // Claimant still current: original paint behavior.
                host.paintAssistantMessage(flight.content);
                return;
            }
            // Joining replacement: the claimant owns the paint (or is
            // gone, in which case the row is already canonical).
            // Recover through history sync, never a second paint.
            try { await host.syncMessages(); } catch (_) {}
        }
        function applyActionFormWatchProgress(card, data) {
            const wrap = card.querySelector('[data-watch-progress]');
            if (!wrap) return;
            card.__watchLastData = data || {};
            const bars = CuttleChatActionForms.normalizeWatchBars(data || {});
            const stack = wrap.querySelector('[data-watch-bars]');
            if (stack) {
                stack.innerHTML = CuttleChatActionForms.renderWatchBarsHtml(bars, host.escapeHtml, data && data.grid);
            } else {
                // Legacy single-bar markup
                const pct = bars[0] ? bars[0].percent : 0;
                const bar = wrap.querySelector('.progress-bar');
                const val = wrap.querySelector('.progress-value');
                if (bar) bar.style.width = pct + '%';
                if (val) val.textContent = pct + '%';
            }
            let grid = wrap.querySelector('[data-watch-grid]');
            if (!grid && data && data.grid) {
                grid = card.ownerDocument.createElement('div');
                grid.setAttribute('data-watch-grid', '');
                wrap.appendChild(grid);
            }
            if (grid) {
                const html = CuttleChatActionForms.renderWatchGridHtml(data && data.grid, host.escapeHtml);
                if (grid.__gridHtml !== html) {
                    grid.innerHTML = html;
                    grid.__gridHtml = html;
                }
            }
            const lab = wrap.querySelector('.cuttle-action-form-progress-label');
            const meta = wrap.querySelector('[data-watch-meta]');
            if (lab) lab.textContent = String((data && (data.label || data.file)) || '');
            const ver = String((data && data.version) || '').trim();
            const elapsed = CuttleChatActionForms.watchElapsedText(data);
            const parts = [];
            if (ver) parts.push(ver);
            if (elapsed) {
                parts.push(String((data && data.state) || '') === 'running' ? elapsed + ' elapsed' : elapsed);
            }
            if (meta) {
                meta.textContent = parts.join(' · ');
                meta.hidden = parts.length === 0;
            }
            const running = String((data && data.state) || '') === 'running';
            const life = card.__cardsLife;
            if (running && data && data.started_at) {
                if (!card.__watchElapsedTimer) {
                    const timer = setInterval(() => {
                        // Disposal clears this interval; a stale tick from
                        // a superseded lifetime paints nothing.
                        if (!isCurrent(card, life)) return;
                        applyActionFormWatchProgress(card, card.__watchLastData || data);
                    }, 1000);
                    card.__watchElapsedTimer = timer;
                    if (life) life.timers.add(timer);
                }
            } else if (card.__watchElapsedTimer) {
                clearInterval(card.__watchElapsedTimer);
                if (life) life.timers.delete(card.__watchElapsedTimer);
                card.__watchElapsedTimer = null;
            }
            host.syncComposerStop();
        }
        function updateRestartCardProgressBar(card, pct, label) {
            const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
            let wrap = card && card.querySelector('[data-restart-progress]');
            if (!wrap && card) {
                const body = card.querySelector('.cuttle-action-form-body');
                if (body) body.insertAdjacentHTML('beforeend', CuttleChatActionForms.renderRestartProgressHtml(clamped, label, host.escapeHtml));
                wrap = card.querySelector('[data-restart-progress]');
            }
            if (!wrap) return;
            const bar = wrap.querySelector('.progress-bar');
            const val = wrap.querySelector('.progress-value');
            const lab = wrap.querySelector('.cuttle-action-form-progress-label');
            if (bar) bar.style.width = clamped + '%';
            if (val) val.textContent = clamped + '%';
            if (lab && label) lab.textContent = String(label);
        }
        function setActionFormCardProgress(card, text, state) {
            if (!card) return;
            const label = String(text || '');
            const statusEl = card.querySelector('.cuttle-action-form-status');
            if (statusEl) {
                statusEl.hidden = !label;
                statusEl.textContent = label;
                statusEl.classList.toggle('is-ok', state === 'ok');
                statusEl.classList.toggle('is-error', state === 'error');
                statusEl.classList.toggle('is-pending', state === 'pending');
            }
            const titleEl = card.querySelector('.cuttle-action-form-title');
            const titleText = titleEl ? String(titleEl.textContent || '').trim() : '';
            const summaryText = card.querySelector('.cuttle-action-form-summary-text');
            if (summaryText) {
                summaryText.textContent = (titleText ? titleText + ' — ' : '') + label;
            }
            card.classList.toggle('is-pending', state === 'pending');
            card.classList.toggle('is-failed', state === 'error');
            const icon = card.querySelector('.cuttle-action-form-summary-icon');
            if (icon) {
                icon.textContent = state === 'pending' ? '' : (state === 'error' ? '✕' : '✓');
            }
            // History panel: same spinner while a form is waiting (e.g. when-idle restart).
            const awaitSid = formAwaitingSessionIdFromCard(card);
            if (state === 'pending') {
                host.setHistoryAwaiting(awaitSid, true);
            } else {
                // Recompute from all cards — another pending form may still be active.
                host.syncHistoryAwaiting();
            }
            if (card && card.getAttribute('data-restart-id')) {
                const pct = card.__restartPct != null
                    ? card.__restartPct
                    : (state === 'ok' ? 100 : state === 'error' ? 100 : 18);
                updateRestartCardProgressBar(card, pct, label);
            }
        }
        // Owned by chat_action_forms.js — DOM gather, domain decides.
        function isLinkedFlaskRestartCard(card) {
            if (!card) return false;
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}');
                const fid = String(card.getAttribute('data-form-id') || '').trim();
                return CuttleChatActionForms.specLooksLikeFlaskRestart(spec, fid);
            } catch (_) {
                return false;
            }
        }
        function linkedFlaskRestartFormId(card) {
            const fid = String(card.getAttribute('data-form-id') || '').trim();
            const group = String(
                card.getAttribute('data-restart-form-group')
                || (JSON.parse(card.getAttribute('data-spec') || '{}').restartFormGroup || '')
            ).trim();
            return CuttleChatActionForms.linkedRestartFormId({ formId: fid, group });
        }
        function formAwaitingSessionIdFromCard(card) {
            if (!card) return null;
            const fromAttr = String(card.getAttribute('data-session-id') || '').trim();
            if (fromAttr) return fromAttr;
            try {
                const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                const sid = String(spec.session_id || '').trim();
                if (sid) return sid;
            } catch (_) {}
            return currentCtx().sessionId || null;
        }
        function runningWatchJobIds() {
            const ids = [];
            chatRoot.querySelectorAll('.cuttle-action-form').forEach((card) => {
                const watch = actionFormWatchSpec(card);
                if (!watch || !watch.id) return;
                if (watch.terminal) return;
                const last = card.__watchLastData || CuttleChatActionForms.watchSnapshotFromSpec(watch) || {};
                const st = String(last.state || '');
                if (st !== 'running') return;
                if (!watchStatusMatchesCard(card, last)) return;
                ids.push(String(watch.id));
            });
            return ids;
        }
        function dismissOpenActionForms(reason) {
            const summary = String(reason || 'Ignored').trim() || 'Ignored';
            const root = chatRoot;
            if (!root) return { formIds: [], summary };
            const formIds = [];
            root.querySelectorAll('.cuttle-action-form').forEach((card) => {
                if (card.getAttribute('data-locked') === '1'
                    || card.classList.contains('cuttle-action-form--locked')) {
                    return;
                }
                // Still waiting on /restart/status — don't mark as ignored mid-heal.
                if (card.getAttribute('data-restart-pending-sync') === '1') return;
                // Flask restart controllers share form id across chats until generation
                // bumps. Soft-dismissing them as "Ignored" (on any follow-up send)
                // poisons every later restart card in this generation — click then
                // returns already_locked with toast "Ignored".
                if (isLinkedFlaskRestartCard(card)) return;
                const lockMode = String(card.getAttribute('data-lock') || 'form').toLowerCase();
                if (lockMode === 'none') return;
                try {
                    const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                    if (spec.reusable) return;
                    if (String(spec.lock || '').toLowerCase() === 'none') return;
                } catch (_) {}
                collapseLockedActionForm(card, [], summary);
                try {
                    const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                    spec.locked = true;
                    spec.toast = summary;
                    card.setAttribute('data-spec', JSON.stringify(spec));
                } catch (_) {}
                const fid = String(card.getAttribute('data-form-id') || '').trim();
                if (fid) formIds.push(fid);
            });
            return { formIds, summary };
        }
        function sendDismissNotice(formIds, summary) {
            if (!formIds.length || !currentCtx().isAuthMode) return;
            const sid = currentCtx().sessionId;
            if (sid == null || sid === '') return;
            host.request('/api/action-form/dismiss', { options: {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    form_ids: formIds,
                    session_id: sid,
                    toast: summary,
                }),
            } }).catch(() => {});
        }
        async function watchActionFormJob(card, opts = {}) {
            if (!card) return;
            const life = card.__cardsLife;
            if (!isCurrent(card, life)) return;
            const watch = actionFormWatchSpec(card);
            const url = watch ? CuttleChatActionForms.safeActionFormWatchUrl(watch.url) : '';
            if (!url) return;
            if (card.__watchLoop) return;
            const doneStates = Array.isArray(watch.done_states) && watch.done_states.length
                ? watch.done_states.map(String)
                : ['done'];
            const failStates = Array.isArray(watch.fail_states) && watch.fail_states.length
                ? watch.fail_states.map(String)
                : ['failed'];
            const snap = CuttleChatActionForms.watchSnapshotFromSpec(watch);
            if (snap) {
                const snapTerminal = CuttleChatActionForms.watchIsTerminalState(watch, snap, doneStates, failStates);
                if (!snapTerminal || watch.terminal) {
                    applyActionFormWatchProgress(card, snap);
                    if (snapTerminal && watch.terminal) {
                        const failed = failStates.indexOf(String(snap.state || '')) >= 0;
                        setActionFormCardProgress(
                            card,
                            String(snap.label || (failed ? 'Failed' : 'Finished')),
                            failed ? 'error' : 'ok'
                        );
                        lockWatchFormCard(card, snap.label, failed);
                        return;
                    }
                } else {
                    applyActionFormWatchProgress(card, { state: 'running', percent: 0, label: 'Starting…' });
                }
            } else {
                applyActionFormWatchProgress(card, { state: 'running', percent: 0, label: 'Starting…' });
            }
            if (watch && watch.terminal) return;
            card.__watchLoop = true;
            const interval = Math.max(1500, Number(watch.interval_ms || 4000));
            const resumeMsg = String(watch.resume_message || 'The background job finished. Continue from the latest status.').trim();
            if (opts.restoreChoice) {
                const saved = loadActionFormWatchChoice(card);
                if ((saved.mode === 'resume' || saved.mode === 'park')
                    && card.getAttribute('data-locked') !== '1') {
                    const summary = saved.mode === 'park'
                        ? "Locked — reply here when it's done."
                        : 'Waiting until this finishes, then I will continue in this chat.';
                    collapseLockedActionForm(card, [], summary);
                    setActionFormCardProgress(card, summary, 'pending');
                }
            }

            const maybeResume = async (data) => {
                if (!isCurrent(card, life)) return;
                const saved = loadActionFormWatchChoice(card);
                if (saved.resumed) return;
                if (saved.mode !== 'resume') return;
                const cardSid = String(card.getAttribute('data-session-id') || '').trim();
                const liveSid = String(currentCtx().authSessionId || currentCtx().sessionId || '').trim();
                if (cardSid && liveSid && !CuttleChatActivity.sessionIdsEqual(cardSid, liveSid)) {
                    setActionFormCardProgress(card, 'Finished — reopen this chat to continue.', 'ok');
                    return;
                }
                markActionFormWatchResumed(card);
                const detail = String((data && data.label) || 'done');
                setActionFormCardProgress(card, 'Finished — continuing…', 'ok');
                try {
                    await host.resumeWithStatus(resumeMsg + '\n\nStatus: ' + detail);
                } catch (_) {}
            };

            const finishWatch = async (data, state) => {
                if (!isCurrent(card, life)) return;
                const failed = failStates.indexOf(state) >= 0;
                const summary = String((data && data.label) || (failed ? 'Failed' : 'Finished'));
                if (typeof host.notifyWorkCompletion === 'function') {
                    const identity = String(data.run_id || watch.run_id || data.batch_id || watch.started_at || '');
                    const started = Date.parse(data.started_at || watch.started_at || '');
                    // Stable run evidence is required; reloading old terminal cards never calls finishWatch.
                    if (identity) host.notifyWorkCompletion({
                        id: 'watch:' + url + ':' + identity,
                        sessionId: card.getAttribute('data-session-id') || currentCtx().sessionId,
                        kind: data.batch_id ? 'mesh' : 'job',
                        outcome: data.cancelled || /cancel/i.test(state) ? 'cancelled' : failed ? 'failed' : 'done',
                        startedAt: Number.isFinite(started) ? started : null,
                        label: summary,
                    });
                }
                persistActionFormWatchSnapshot(card, data || {}, true);
                lockWatchFormCard(card, summary, failed);
                if (data && data.render_result && Array.isArray(data.render_result.attachments)
                        && data.render_result.attachments.length) {
                    // Recover server-saved output; the browser never owns its insert.
                    // Do not delay the watch's resume/park handling on history I/O.
                    Promise.resolve().then(() => host.syncMessages()).catch(() => {});
                }
                if (failed) {
                    if (card.__cardsLife === life) card.__watchLoop = false;
                    return;
                }
                applyActionFormWatchProgress(card, Object.assign({}, data, { percent: 100 }));
                const saved = loadActionFormWatchChoice(card);
                const hasDiscord = data && data.discord_form && typeof data.discord_form === 'object';
                if (hasDiscord) {
                    await offerJobSuccessDiscordForm(card, data);
                    if (!isCurrent(card, life)) return;
                    setActionFormCardProgress(card, 'Finished — Discord confirm is below (Post or Cancel).', 'ok');
                    if (card.__cardsLife === life) card.__watchLoop = false;
                    return;
                }
                if (saved.mode === 'resume') {
                    await maybeResume(data);
                } else if (saved.mode === 'park') {
                    if (!isCurrent(card, life)) return;
                    setActionFormCardProgress(card, 'Finished — waiting for your reply.', 'ok');
                }
                if (card.__cardsLife === life) card.__watchLoop = false;
            };

            while (isCurrent(card, life)) {
                let data = null;
                try {
                    const resp = await host.request(url + (url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now(), {
                        options: { credentials: 'include', cache: 'no-store' },
                        signal: life.signal,
                    });
                    data = await resp.json();
                } catch (_) {
                    // Detached or disposed while awaiting: disposal owns
                    // teardown, so a stale continuation only exits.
                    if (!isCurrent(card, life)) return;
                    if (!card.__watchLastData) {
                        applyActionFormWatchProgress(card, { percent: 0, label: 'Waiting for status…' });
                    }
                    if (!(await cardWait(card, life, interval))) return;
                    continue;
                }
                // Detached or disposed while awaiting: never apply, persist,
                // or resume a late response for a card this lifetime no
                // longer owns.
                if (!isCurrent(card, life)) return;
                if (!watchStatusMatchesCard(card, data || {})) {
                    const bind = CuttleChatActionForms.cardWatchBind(watch);
                    const polledState = String((data && data.state) || '');
                    const polledTerminal = failStates.indexOf(polledState) >= 0 || doneStates.indexOf(polledState) >= 0;
                    // Status file may still show the previous run for a moment after /build.
                    if (!bind || polledTerminal) {
                        if (!card.__watchLastData || !CuttleChatActionForms.cardWatchBind(actionFormWatchSpec(card) || {})) {
                            applyActionFormWatchProgress(card, { state: 'running', percent: 0, label: 'Starting…' });
                        }
                        if (!(await cardWait(card, life, Math.min(interval, 1200)))) return;
                        continue;
                    }
                    // Another job reused this status URL. Freeze this card; do not
                    // paint the later run's success/failure onto it.
                    persistActionFormWatchSnapshot(card, card.__watchLastData || snap || {}, true);
                    if (card.__cardsLife === life) card.__watchLoop = false;
                    return;
                }
                applyActionFormWatchProgress(card, data || {});
                if (!card.__watchBindPersisted && data && (data.started_at || data.run_id)) {
                    card.__watchBindPersisted = true;
                    try {
                        const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                        spec.watch = spec.watch && typeof spec.watch === 'object' ? spec.watch : {};
                        if (data.run_id) spec.watch.run_id = String(data.run_id);
                        if (data.started_at) spec.watch.started_at = String(data.started_at);
                        card.setAttribute('data-spec', JSON.stringify(spec));
                    } catch (_) {}
                    persistActionFormWatchSnapshot(card, data, false);
                }
                const state = String((data && data.state) || '');
                if (failStates.indexOf(state) >= 0 || doneStates.indexOf(state) >= 0) {
                    await finishWatch(data, state);
                    return;
                }
                const saved = loadActionFormWatchChoice(card);
                if (saved.mode === 'resume') {
                    setActionFormCardProgress(
                        card,
                        String((data && data.label) || 'Working…'),
                        'pending'
                    );
                }
                if (!(await cardWait(card, life, interval))) return;
            }
            if (card.__cardsLife === life) card.__watchLoop = false;
        }
        function broadcastLinkedFlaskRestart(payload) {
            try {
                host.publishRestartEvent(payload);
            } catch (_) {}
            try {
                applyLinkedFlaskRestartEvent(payload);
            } catch (_) {}
        }
        function applyLinkedFlaskRestartEvent(payload) {
            if (!payload) return;
            const formId = String(payload.formId || payload.form_id || '').trim();
            const restartId = String(payload.restartId || payload.restart_id || '').trim();
            const selected = payload.selected || [];
            const toast = String(payload.toast || 'Restarting Flask…');
            const superseded = !!payload.superseded;
            // Inbound cross-pane events apply only to this controller's
            // own root and its current lifetimes — never whole-document
            // ownership, never another controller's cards, never a
            // disposed card. Generation matching below is unchanged.
            if (destroyed) return;
            chatRoot.querySelectorAll('.cuttle-action-form').forEach((card) => {
                if (!isLinkedFlaskRestartCard(card)) return;
                const life = card.__cardsLife;
                if (!life || !life.alive) return;
                const cardId = linkedFlaskRestartFormId(card);
                if (formId && cardId && cardId !== formId) {
                    // Different generation — leave alone unless superseded for that id.
                    if (!(superseded && cardId === formId)) return;
                }
                if (superseded || payload.done) {
                    if (!card.classList.contains('cuttle-action-form--locked')) {
                        collapseLockedActionForm(card, selected.length ? selected : ['graceful'], toast);
                    }
                    setActionFormCardProgress(
                        card,
                        toast || (payload.ok === false ? 'Restart failed' : 'Flask restarted'),
                        payload.ok === false ? 'error' : 'ok'
                    );
                    return;
                }
                if (!restartId) return;
                if (!card.classList.contains('cuttle-action-form--locked')) {
                    card.setAttribute('data-locked', '1');
                    collapseLockedActionForm(card, selected.length ? selected : ['graceful'], toast);
                }
                if (payload.status) paintFlaskRestartProgress(card, payload.status);
                else setActionFormCardProgress(card, toast, 'pending');
                watchFlaskRestartOnCard(card, restartId, { quietStart: true, initialStatus: payload.status });
            });
        }
        async function syncLinkedFlaskRestartCardsFromStatus() {
            // Root-scoped scan: this controller settles only its own
            // root's cards. Generation semantics are unchanged.
            const cards = Array.from(chatRoot.querySelectorAll('.cuttle-action-form'))
                .filter(isLinkedFlaskRestartCard);
            if (!cards.length) return;
            let data = null;
            try {
                const resp = await host.request(
                    '/api/flask/restart/status',
                    { options: { credentials: 'include', cache: 'no-store' }, timeout: 6000, signal: linkPollSignal() }
                );
                data = await resp.json();
            } catch (_) {
                // A destroyed controller paints nothing, even on failure.
                if (destroyed) return;
                // Don't leave brand-new cards stuck on "Checking restart status…".
                cards.forEach((card) => unlockFlaskRestartPendingSyncCard(card));
                return;
            }
            const liveGen = data && data.live_generation != null
                ? Number(data.live_generation)
                : null;
            const currentFormId = String((data && data.restart_form_id) || '').trim();
            const st = (data && data.status) || {};
            const state = String(st.state || '');
            const rid = String(st.restart_id || '').trim();
            const inFlight = !!(rid && state && CuttleChatActionForms.RESTART_TERMINAL_STATES.indexOf(state) < 0);
            const terminal = !!(rid && CuttleChatActionForms.RESTART_TERMINAL_STATES.indexOf(state) >= 0);

            cards.forEach((card) => {
                // Success-path effects only for current lifetimes in this
                // root. Disposed cards (__cardsLife null) never pass; a
                // stale continuation cannot paint a successor's markers
                // because every downstream entry re-checks its token.
                const life = card.__cardsLife;
                if (!life || !life.alive || destroyed) return;
                const cardId = linkedFlaskRestartFormId(card);
                const epoch = CuttleChatActionForms.flaskRestartFormEpoch(cardId);
                // Heal soft follow-up dismiss ("Ignored") left on shared restart controllers.
                try {
                    const spec = JSON.parse(card.getAttribute('data-spec') || '{}') || {};
                    const toast = String(spec.toast || '').trim();
                    if (/^(ignored|cancell?ed)\b/i.test(toast)
                        && (card.getAttribute('data-locked') === '1'
                            || card.classList.contains('cuttle-action-form--locked'))) {
                        spec.locked = false;
                        delete spec.toast;
                        card.setAttribute('data-spec', JSON.stringify(spec));
                        card.setAttribute('data-locked', '0');
                        card.classList.remove(
                            'cuttle-action-form--locked',
                            'is-cancelled',
                            'is-collapsed',
                            'is-collapsible',
                            'is-pending'
                        );
                        card.removeAttribute('data-restart-pending-sync');
                        card.querySelectorAll('.cuttle-action-form-body button, input, textarea, select')
                            .forEach((el) => { el.disabled = false; });
                        const titleText = String(
                            (card.querySelector('.cuttle-action-form-title') || {}).textContent || ''
                        ).trim() || 'Restart Flask';
                        const out = card.querySelector('.cuttle-action-form-summary-text');
                        if (out) out.textContent = titleText;
                        const icon = card.querySelector('.cuttle-action-form-summary-icon');
                        if (icon) icon.textContent = '';
                    }
                } catch (_) {}
                // Prior generation → already restarted since this card was offered.
                if (epoch != null && liveGen != null && liveGen > epoch) {
                    if (!card.classList.contains('cuttle-action-form--locked')
                        || card.classList.contains('is-pending')) {
                        collapseLockedActionForm(card, ['graceful'], 'Flask already restarted');
                        setActionFormCardProgress(card, 'Flask already restarted', 'ok');
                    }
                    return;
                }
                if (inFlight && rid && (!currentFormId || !cardId || cardId === currentFormId
                    || (epoch != null && liveGen != null && epoch === liveGen))) {
                    card.removeAttribute('data-restart-pending-sync');
                    // Already following this restart — do not clobber the watcher's
                    // "Health check…" / phase label every poll (that caused the
                    // Restarting ↔ Health check flicker on a stuck status file).
                    if (card.__restartWatchId === String(rid)) {
                        return;
                    }
                    if (!card.classList.contains('cuttle-action-form--locked')) {
                        card.setAttribute('data-locked', '1');
                        collapseLockedActionForm(card, ['graceful'], 'Restarting Flask…');
                    }
                    setActionFormCardProgress(card, 'Restarting Flask…', 'pending');
                    watchFlaskRestartOnCard(card, rid, { quietStart: true });
                    return;
                }
                if (terminal && rid && state === 'healthy'
                    && (!currentFormId || !cardId || cardId === currentFormId)) {
                    // Only auto-settle unlocked peers that shared this generation;
                    // avoid collapsing a brand-new card after a finished restart.
                    if (!card.classList.contains('cuttle-action-form--locked')
                        && card.__restartWatchId) {
                        setActionFormCardProgress(card, 'Flask restarted', 'ok');
                        return;
                    }
                }
                // Generation still live (or unknown) and nothing in flight — open for click.
                unlockFlaskRestartPendingSyncCard(card);
            });
        }
        function ensureLinkPoller() {
            if (linkPoll) return;
            try {
                linkPoll = setInterval(() => {
                    if (destroyed) return;
                    syncLinkedFlaskRestartCardsFromStatus().catch(() => {});
                    try { host.syncHistoryAwaiting(); } catch (_) {}
                }, 2500);
            } catch (_) { linkPoll = null; }
            syncLinkedFlaskRestartCardsFromStatus().catch(() => {});
            try { host.syncHistoryAwaiting(); } catch (_) {}
        }
        function paintFlaskRestartProgress(card, status, liveWork) {
            if (status && status.restart_id) card.setAttribute('data-restart-id', String(status.restart_id));
            const state = String((status && status.state) || '');
            card.__restartState = state;
            card.__restartPct = CuttleChatActionForms.RESTART_PROGRESS_PCT[state] != null
                ? CuttleChatActionForms.RESTART_PROGRESS_PCT[state] : 25;
            const terminal = CuttleChatActionForms.isRestartTerminalState(state);
            setActionFormCardProgress(card, CuttleChatActionForms.restartProgressLabel(status, liveWork),
                terminal ? (state === 'healthy' ? 'ok' : 'error') : 'pending');
        }
        async function watchFlaskRestartOnCard(card, restartId, opts = {}) {
            if (!card || !restartId) return;
            const life = card.__cardsLife;
            if (!isCurrent(card, life)) return;
            // A successor lifetime that already adopted this restart owns it.
            if (card.__restartWatchId === String(restartId)) return;
            card.__restartWatchId = String(restartId);
            card.setAttribute('data-restart-id', String(restartId));
            const deadline = Date.now() + (opts.waitMs != null ? opts.waitMs : 600000);
            const pollMs = opts.pollMs != null ? opts.pollMs : 2000;
            if (!opts.quietStart) {
                setActionFormCardProgress(card, 'Restarting Flask…', 'pending');
            }
            const clearId = () => {
                if (card.__cardsLife === life && card.__restartWatchId === String(restartId)) card.__restartWatchId = null;
            };
            while (isCurrent(card, life) && card.__restartWatchId === String(restartId) && Date.now() < deadline) {
                let status = null;
                let liveWork = null;
                try {
                    const resp = await host.request(
                        '/api/flask/restart/status',
                        { options: { credentials: 'include', cache: 'no-store' }, timeout: 6000, signal: life.signal }
                    );
                    const data = await resp.json();
                    status = (data && data.status) || {};
                    liveWork = (data && data.active_work) || null;
                } catch (_) {
                    // Detached or disposed while awaiting: disposal owns
                    // teardown, so a stale continuation only exits.
                    if (!isCurrent(card, life) || card.__restartWatchId !== String(restartId)) return;
                    // Expected while the daemon swaps the process.
                    setActionFormCardProgress(card, 'Restarting Flask — reconnecting…', 'pending');
                    if (!(await cardWait(card, life, pollMs))) return;
                    continue;
                }
                // Detached or disposed while awaiting: stop requests and
                // late UI/history effects for a card this lifetime no
                // longer owns.
                if (!isCurrent(card, life) || card.__restartWatchId !== String(restartId)) return;
                if (String(status.restart_id || '') !== String(restartId)) {
                    // A different request's result does not prove this restart finished.
                    // Keep the actual submission state until its matching status arrives.
                    if (opts.initialStatus) {
                        paintFlaskRestartProgress(card, opts.initialStatus);
                        if (!(await cardWait(card, life, pollMs))) return;
                        continue;
                    }
                    setActionFormCardProgress(card, 'Restart status changed — check Status', 'error');
                    clearId();
                    return;
                }
                const state = String(status.state || '');
                card.__restartState = state;
                card.__restartPct = CuttleChatActionForms.RESTART_PROGRESS_PCT[state] != null
                    ? CuttleChatActionForms.RESTART_PROGRESS_PCT[state]
                    : 25;
                const label = CuttleChatActionForms.restartProgressLabel(status, liveWork);
                if (CuttleChatActionForms.RESTART_TERMINAL_STATES.indexOf(state) >= 0) {
                    const ok = state === 'healthy';
                    setActionFormCardProgress(card, label, ok ? 'ok' : 'error');
                    clearId();
                    // The transcript may have advanced while Flask was down.
                    try { await host.syncMessages(); } catch (_) {}
                    return;
                }
                setActionFormCardProgress(card, label, 'pending');
                if (!(await cardWait(card, life, pollMs))) return;
            }
            if (isCurrent(card, life) && card.__restartWatchId === String(restartId)) {
                setActionFormCardProgress(card, 'Restart status unknown — check /restart status', 'error');
            }
            clearId();
        }
        async function adoptInFlightRestartOnCard(card, opts = {}) {
            if (!card) return;
            const life = card.__cardsLife;
            if (!isCurrent(card, life)) return;
            const deadline = Date.now() + 60000;
            while (isCurrent(card, life) && Date.now() < deadline) {
                try {
                    const resp = await host.request(
                        '/api/flask/restart/status',
                        { options: { credentials: 'include', cache: 'no-store' }, timeout: 6000, signal: life.signal }
                    );
                    const data = await resp.json();
                    const rid = ((data && data.status) || {}).restart_id;
                    if (rid) {
                        if (!isCurrent(card, life)) return;
                        return watchFlaskRestartOnCard(card, String(rid), {
                            quietStart: true,
                            ...opts,
                        });
                    }
                } catch (_) {}
                if (!isCurrent(card, life)) return;
                if (!(await cardWait(card, life, 2000))) return;
            }
            if (isCurrent(card, life)) {
                setActionFormCardProgress(card, 'Restart status unavailable', 'error');
            }
        }

        return {
            mount,
            dismissOpenActionForms,
            sendDismissNotice,
            handleExternalRestart: applyLinkedFlaskRestartEvent,
            runningWatchJobIds,
            awaitingSessionId: formAwaitingSessionIdFromCard,
            disposeCard,
            destroy,
        };
    }


    const api = {
        mountCards,
    };

    const ns = (root.CuttleChatActionCards = root.CuttleChatActionCards || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
