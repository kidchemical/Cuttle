/* Revisioned server attention plus a durable local outbox for read/manual
 * unread writes. Snapshots recover state without witnessing a completion.
 *
 * This controller also owns the HISTORY attention projection and DOM
 * synchronization: the history row/section class+icon paint, the open-pane
 * badge, and the title-level unread dot. Attention-KIND decisions stay in
 * chat_activity.js and are injected as `host.attentionKindFor`; server
 * persistence stays in `get`/`accept`/`mark`/`flush` here. The panel and the
 * page are reached only through injected host callbacks (no page globals).
 *
 * Projection host capabilities (all optional; guarded so the pure
 * persistence tests can construct a bare host):
 *   doc, escapeHtmlInline, sessionIdsEqual, openPaneSnapshot,
 *   sessionObjFor(sid), attentionKindFor(sid, sessionObj),
 *   syncSubagentAttention(), scheduleBroadcast(), currentSessionId(),
 *   attentionPrefsFor(sid), attentionActive(), attentionIsError(),
 *   hasActiveFollowup(sid), hasPausedFollowup(sid). */
(function (root) {
    'use strict';
    function create(host) {
        const snapshots = new Map();
        const writes = new Map();
        function get(sid) {
            const key = String(sid);
            const snap = snapshots.get(key) || null;
            const pending = (host.prefs.get(key) || {}).attentionWrite;
            if (!snap || !pending) return snap;
            return {...snap, hasUnread: pending.unread || snap.reply_id > Math.max(snap.read_id, pending.through_id || 0)};
        }
        function accept(sid, snapshot) {
            if (!snapshot) return;
            const key = String(sid), prev = snapshots.get(key);
            if (!prev || snapshot.revision >= prev.revision) snapshots.set(key, snapshot);
            const prefs = host.prefs.get(key) || {};
            // Clean snapshots need no per-chat migration write. Avoid rebuilding
            // the prefs index for every row of a large initial session list.
            if (!prefs.attentionMigrated && prefs.hasUnread) {
                host.prefs.update(key, {attentionMigrated: true});
                mark(key, {unread: true, legacy: true});
            }
            if (prefs.attentionWrite) flush(key);
        }
        function mark(sid, data) {
            const key = String(sid), previous = (host.prefs.get(key) || {}).attentionWrite;
            const snapshot = snapshots.get(key);
            if (!data.unread && !previous && snapshot && !snapshot.hasUnread && snapshot.read_id >= data.through_id) return Promise.resolve(true);
            if (!data.unread && previous && !previous.unread) data = {...data, through_id: Math.max(data.through_id || 0, previous.through_id || 0)};
            host.prefs.update(key, {attentionWrite: {...data, id: host.id()}});
            return flush(key);
        }
        function flush(key) {
            if (writes.has(key)) return writes.get(key);
            const pending = (host.prefs.get(key) || {}).attentionWrite;
            if (!pending) return Promise.resolve();
            const write = host.request(key, pending).then(result => {
                const current = (host.prefs.get(key) || {}).attentionWrite;
                if (current && current.id === pending.id) host.prefs.update(key, {attentionWrite: null});
                if (result && result.attention) {
                    const previous = snapshots.get(key);
                    if (!previous || result.attention.revision >= previous.revision) snapshots.set(key, result.attention);
                }
                host.changed(key);
                return true;
            }).catch(() => false).then(ok => {
                writes.delete(key);
                const next = (host.prefs.get(key) || {}).attentionWrite;
                // A new write superseded this request; send it even if this one failed.
                if (next && next.id !== pending.id) return flush(key);
                return ok;
            });
            writes.set(key, write);
            return write;
        }

        /* ---------- history / title DOM projection ---------- */
        // Attention-KIND rules live in chat_activity.js; the page injects the
        // resolved kind + session object so this controller only projects.

        const doc = host.doc || (root && root.document) || null;
        const esc = typeof host.escapeHtmlInline === 'function'
            ? host.escapeHtmlInline
            : (s) => String(s == null ? '' : s);

        function idsEqual(a, b) {
            if (typeof host.sessionIdsEqual === 'function') return host.sessionIdsEqual(a, b);
            return a != null && b != null && String(a) === String(b);
        }

        function openPaneSnapshot() {
            if (typeof host.openPaneSnapshot !== 'function') return [];
            try { return host.openPaneSnapshot() || []; } catch (_) { return []; }
        }

        // Whether a session currently has an active run (open chat turn or a
        // submitted/adopted action form). Injected by the page; default idle.
        function sessionIsRunning(sid) {
            if (typeof host.sessionShowsHistorySpinner !== 'function') return false;
            try { return !!host.sessionShowsHistorySpinner(sid); } catch (_) { return false; }
        }

        function historyUnreadIconHTML(isError) {
            const err = !!isError;
            return `<span class="history-unread-icon${err ? ' is-error' : ''}" title="${err ? 'Unread error' : 'Unread'}" aria-label="${err ? 'Unread error' : 'Unread'}"></span>`;
        }

        function historyQueuedIconHTML() {
            return `<span class="history-queued-icon" title="Queued prompt" aria-label="Queued prompt"></span>`;
        }

        function historyPausedIconHTML() {
            return `<span class="history-paused-icon" title="Paused queue" aria-label="Paused queued prompt"></span>`;
        }

        function historyRunningIconHTML() {
            return `<span class="history-running-icon" title="Generating…" aria-label="Generating"></span>`;
        }

        function historyAttentionIconHTML(kind) {
            if (kind === 'running') return historyRunningIconHTML();
            if (kind === 'input') return '<span class="history-input-icon" title="Awaiting your input" aria-label="Awaiting your input"></span>';
            if (kind === 'error') return historyUnreadIconHTML(true);
            if (kind === 'unread') return historyUnreadIconHTML(false);
            if (kind === 'queued') return historyQueuedIconHTML();
            if (kind === 'paused') return historyPausedIconHTML();
            return '';
        }

        function openPaneEntriesForSession(sessionId) {
            if (sessionId == null || sessionId === '') return [];
            return openPaneSnapshot().filter((p) => p && p.sessionId && idsEqual(p.sessionId, sessionId));
        }

        /** Unique split-pane numbers currently showing a chat/terminal in this window. */
        function historyOpenPaneCount() {
            return [...new Set(
                openPaneSnapshot().map((p) => Number(p && p.pane) || 0).filter((n) => n > 0)
            )].length;
        }

        function historyOpenPaneBadgeHTML(sessionId) {
            // A "1" badge is noise when there is only one pane.
            if (historyOpenPaneCount() < 2) return '';
            const hits = openPaneEntriesForSession(sessionId);
            if (!hits.length) return '';
            const panes = [...new Set(hits.map((p) => Number(p.pane) || 0).filter((n) => n > 0))].sort((a, b) => a - b);
            if (!panes.length) return '';
            const text = panes.join('+');
            const title = panes.length === 1
                ? ('Open in pane ' + panes[0] + ' (this window)')
                : ('Open in panes ' + panes.join(', ') + ' (this window)');
            return `<span class="history-open-pane-badge" title="${esc(title)}" aria-label="${esc(title)}">${esc(text)}</span>`;
        }

        function syncHistoryOpenPaneIndicators() {
            if (!doc || typeof doc.querySelectorAll !== 'function') return;
            doc.querySelectorAll('.chat-history-item').forEach((item) => {
                const sid = item.dataset.sessionId;
                const hits = openPaneEntriesForSession(sid);
                item.classList.toggle('is-open-pane', hits.length > 0);
                const actions = item.querySelector('.chat-actions');
                let badge = item.querySelector('.history-open-pane-badge');
                const html = historyOpenPaneBadgeHTML(sid);
                if (!html) {
                    if (badge) badge.remove();
                    return;
                }
                if (!badge) {
                    const wrap = doc.createElement('div');
                    wrap.innerHTML = html;
                    badge = wrap.firstElementChild;
                    if (actions) actions.insertBefore(badge, actions.firstChild);
                    else item.appendChild(badge);
                } else {
                    const wrap = doc.createElement('div');
                    wrap.innerHTML = html;
                    const next = wrap.firstElementChild;
                    if (next) {
                        badge.title = next.title;
                        badge.setAttribute('aria-label', next.getAttribute('aria-label') || '');
                        badge.textContent = next.textContent;
                    }
                }
            });
        }

        function syncHistoryProjectAttentionIndicators() {
            if (!doc || typeof doc.querySelectorAll !== 'function') return;
            doc.querySelectorAll('.history-section[data-project-key]').forEach((section) => {
                const items = section.querySelectorAll('.chat-history-item');
                let hasInput = false;
                let hasError = false;
                let hasUnread = false;
                let hasQueued = false;
                let hasPaused = false;
                items.forEach((item) => {
                    if (item.classList.contains('has-input')) hasInput = true;
                    if (item.classList.contains('has-unread-error')) hasError = true;
                    if (item.classList.contains('has-unread')) hasUnread = true;
                    if (item.classList.contains('has-queued')) hasQueued = true;
                    if (item.classList.contains('has-paused-queue')) hasPaused = true;
                });
                if (!items.length) {
                    hasError = section.getAttribute('data-has-error') === '1';
                    hasUnread = section.getAttribute('data-has-unread') === '1';
                    hasQueued = section.getAttribute('data-has-queued') === '1';
                    hasPaused = section.getAttribute('data-has-paused') === '1';
                }
                const showInput = hasInput;
                const showError = !showInput && hasError;
                const showUnread = !showInput && !showError && hasUnread;
                const showQueued = !showInput && !showError && !showUnread && hasQueued;
                const showPaused = !showInput && !showError && !showUnread && !showQueued && hasPaused;
                section.classList.toggle('has-input', showInput);
                section.classList.toggle('has-unread-error', showError);
                section.classList.toggle('has-unread', showError || showUnread);
                section.classList.toggle('has-queued', showQueued);
                section.classList.toggle('has-paused-queue', showPaused);
                const count = section.querySelector('.history-section-count');
                if (count) {
                    count.classList.toggle('has-input', showInput);
                    count.classList.toggle('has-unread-error', showError);
                    count.classList.toggle('has-unread', showError || showUnread);
                    count.classList.toggle('has-queued', showQueued);
                    count.classList.toggle('has-paused-queue', showPaused);
                }
                let unreadDot = section.querySelector('.history-section-unread-dot');
                let queuedDot = section.querySelector('.history-section-queued-dot');
                let pausedDot = section.querySelector('.history-section-paused-dot');
                const titleEl = section.querySelector('.history-section-title');
                const removeDots = () => {
                    if (unreadDot) { unreadDot.remove(); unreadDot = null; }
                    if (queuedDot) { queuedDot.remove(); queuedDot = null; }
                    if (pausedDot) { pausedDot.remove(); pausedDot = null; }
                };
                if (showInput || showError || showUnread) {
                    if (queuedDot) queuedDot.remove();
                    if (pausedDot) pausedDot.remove();
                    if (!unreadDot && titleEl) {
                        unreadDot = doc.createElement('span');
                        unreadDot.className = 'history-section-unread-dot';
                        titleEl.insertBefore(unreadDot, titleEl.firstChild);
                    }
                    if (unreadDot) {
                        unreadDot.classList.toggle('is-input', showInput);
                        unreadDot.classList.toggle('is-error', showError);
                        unreadDot.title = showInput ? 'Awaiting your input' : showError ? 'Unread error' : 'Unread';
                        unreadDot.setAttribute('aria-label', showInput ? 'Awaiting your input' : showError ? 'Unread error' : 'Unread');
                    }
                } else if (showQueued) {
                    if (unreadDot) unreadDot.remove();
                    if (pausedDot) pausedDot.remove();
                    if (!queuedDot && titleEl) {
                        queuedDot = doc.createElement('span');
                        queuedDot.className = 'history-section-queued-dot';
                        queuedDot.title = 'Queued prompt';
                        queuedDot.setAttribute('aria-label', 'Queued prompt');
                        titleEl.insertBefore(queuedDot, titleEl.firstChild);
                    }
                } else if (showPaused) {
                    if (unreadDot) unreadDot.remove();
                    if (queuedDot) queuedDot.remove();
                    if (!pausedDot && titleEl) {
                        pausedDot = doc.createElement('span');
                        pausedDot.className = 'history-section-paused-dot';
                        pausedDot.title = 'Paused queue';
                        pausedDot.setAttribute('aria-label', 'Paused queued prompt');
                        titleEl.insertBefore(pausedDot, titleEl.firstChild);
                    }
                } else {
                    removeDots();
                }
            });
        }

        function updateSessionTitleUnreadDot() {
            if (!doc || typeof doc.getElementById !== 'function') return;
            const unreadIcon = doc.getElementById('chatSessionUnreadIcon');
            const queuedIcon = doc.getElementById('chatSessionQueuedIcon');
            const pausedIcon = doc.getElementById('chatSessionPausedIcon');
            const wrap = doc.getElementById('chatSessionTitle');
            if (!unreadIcon && !queuedIcon && !pausedIcon) return;
            const current = typeof host.currentSessionId === 'function' ? host.currentSessionId() : null;
            const prefsFor = typeof host.attentionPrefsFor === 'function' ? host.attentionPrefsFor : () => null;
            const titleVisible = !!(wrap && !wrap.hidden);
            const showInput = !!(titleVisible && current != null && (prefsFor(current) || {}).awaitingInput);
            const inputIcon = doc.getElementById('chatSessionInputIcon');
            if (inputIcon) inputIcon.hidden = !showInput;
            const showUnread = !!(titleVisible && !showInput
                && typeof host.attentionActive === 'function' && host.attentionActive());
            const showUnreadError = !!(showUnread
                && typeof host.attentionIsError === 'function' && host.attentionIsError());
            // Queue dots only when unread is not already showing (green/red wins).
            // Amber = active (unpaused) queue; yellow = paused (see CH-000439-14 legend).
            const hasActive = typeof host.hasActiveFollowup === 'function' ? host.hasActiveFollowup : () => false;
            const hasPaused = typeof host.hasPausedFollowup === 'function' ? host.hasPausedFollowup : () => false;
            const showQueued = !!(
                titleVisible
                && !showInput
                && !showUnread
                && current != null
                && hasActive(current)
            );
            const showPaused = !!(
                titleVisible
                && !showInput
                && !showUnread
                && !showQueued
                && current != null
                && hasPaused(current)
            );
            if (unreadIcon) {
                unreadIcon.hidden = !showUnread;
                unreadIcon.classList.toggle('is-error', showUnreadError);
                unreadIcon.title = showUnreadError ? 'Unread error' : 'Unread';
                unreadIcon.setAttribute('aria-label', showUnreadError ? 'Unread error' : 'Unread');
            }
            if (queuedIcon) queuedIcon.hidden = !showQueued;
            if (pausedIcon) pausedIcon.hidden = !showPaused;
            if (wrap) {
                wrap.classList.toggle('has-unread', showUnread);
                wrap.classList.toggle('has-unread-error', showUnreadError);
                wrap.classList.toggle('has-queued', showQueued);
                wrap.classList.toggle('has-paused-queue', showPaused);
            }
        }

        /** Paint the single far-left status glyph for a history row. */
        function syncHistoryLeftStatusIcon(item, kind) {
            const wanted = kind === 'running' ? 'history-running-icon'
                : kind === 'input' ? 'history-input-icon'
                : (kind === 'unread' || kind === 'error') ? 'history-unread-icon'
                : kind === 'queued' ? 'history-queued-icon'
                : kind === 'paused' ? 'history-paused-icon'
                : '';
            const selector = ':scope > .history-running-icon, :scope > .history-unread-icon,'
                + ' :scope > .history-queued-icon, :scope > .history-paused-icon,'
                + ' :scope > .history-input-icon';
            const current = item.querySelector(selector);
            if (current && wanted && current.classList.contains(wanted)) {
                if (wanted === 'history-unread-icon') current.classList.toggle('is-error', kind === 'error');
                return;
            }
            if (current) current.remove();
            if (!wanted) return;
            item.insertAdjacentHTML('afterbegin', historyAttentionIconHTML(kind));
        }

        function syncHistoryUnreadIndicators() {
            if (doc && typeof doc.querySelectorAll === 'function') {
                doc.querySelectorAll('.chat-history-item').forEach((item) => {
                    const sid = item.dataset.sessionId;
                    if (sid == null) return;
                    const sessionObj = typeof host.sessionObjFor === 'function' ? host.sessionObjFor(sid) : null;
                    const kind = typeof host.attentionKindFor === 'function'
                        ? host.attentionKindFor(sid, sessionObj)
                        : '';
                    // One status slot on the left: an active run wins over the
                    // attention dot so the spinner and unread markers never sit
                    // on two lines.
                    const running = sessionIsRunning(sid);
                    const attention = running ? '' : kind;
                    const statusKind = running ? 'running' : attention;
                    item.classList.toggle('is-running', running);
                    item.classList.toggle('has-unread', attention === 'unread' || attention === 'error');
                    item.classList.toggle('has-unread-error', attention === 'error');
                    item.classList.toggle('has-queued', attention === 'queued');
                    item.classList.toggle('has-paused-queue', attention === 'paused');
                    item.classList.toggle('has-input', attention === 'input');
                    syncHistoryLeftStatusIcon(item, statusKind);
                    // Older markup parked the spinner in the meta line; clear it here.
                    const metaSpinner = item.querySelector('.history-item-meta .history-running-icon');
                    if (metaSpinner) metaSpinner.remove();
                });
            }
            syncHistoryProjectAttentionIndicators();
            // Cross-owner seams are injected: the panel owns subagent badges,
            // the page owns the activity broadcast. No controller reaches into
            // the other's globals.
            if (typeof host.syncSubagentAttention === 'function') host.syncSubagentAttention();
            updateSessionTitleUnreadDot();
            if (typeof host.scheduleBroadcast === 'function') {
                try { host.scheduleBroadcast(); } catch (_) {}
            }
        }

        return {
            get, accept, mark, flush,
            historyUnreadIconHTML, historyQueuedIconHTML, historyPausedIconHTML,
            historyRunningIconHTML, historyAttentionIconHTML,
            historyOpenPaneCount, historyOpenPaneBadgeHTML, syncHistoryOpenPaneIndicators,
            syncHistoryLeftStatusIcon,
            syncHistoryUnreadIndicators, syncHistoryProjectAttentionIndicators,
            updateSessionTitleUnreadDot,
        };
    }
    const api = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatAttention = api;
})(globalThis);
