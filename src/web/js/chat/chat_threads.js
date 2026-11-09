/* ================================================================
   Cuttle Chat — reply threads (chat_threads.js).
   Owner: drawing a turn's reply thread in the transcript — the
   "N replies" bar under the turn's bubble, collapse / expand, the quiet
   unread dot + preview, and keeping open threads open.
   Grouping rules live in chat_thread_model.js (pure).

   The transcript stays one flat list of rows in saved order: this module
   never moves a row (CH-…-N numbering and history sync are unchanged).
   It tags rows with classes (`thread-root`, `thread-reply`, hidden
   replies) and mounts the bar inside the root bubble. A MutationObserver
   on the transcript re-derives everything after any change — history
   load, sync, a steer you send, the live bubble turning into the saved
   reply, sub-agent cards updating — so every path that paints a row is
   covered without call sites.

   Open state:
   - a thread you open or close stays that way (saved per chat);
   - a live thread (its turn or a reply is still running, or you just
     steered into it) opens by itself;
   - nothing ever closes a thread except you.
   Unread: an agent reply (follow-up) that arrives
   while its thread is closed, or since your last visit, shows a dot and
   a one-line preview until you open the thread.

   Reveal: dispatch `cuttle:reveal` (bubbling) on a row to open its
   thread first (message-nav jumps, find-in-chat).

   host: { doc, win, container(), sessionId(), storage, escapeHtml,
           renderHandleLink(handle), avatarHtml(avatar),
           isFollowingLatest(), revealTop(el) }
   Loaded after chat_thread_model.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const STORE_PREFIX = 'cuttle.chatThreads.v1:';
    const MAX_THREAD_KEYS = 120;
    const LIVE_IDS = new Set(['typing-indicator', 'typing-indicator-remote']);
    const REPLY_CLASSES = ['thread-reply', 'thread-reply--steer', 'thread-reply--followup',
        'thread-reply-last', 'thread-reply-hidden'];
    const CHEVRON = '<svg class="chat-thread-chevron" viewBox="0 0 16 16" aria-hidden="true">'
        + '<path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.8" '
        + 'stroke-linecap="round" stroke-linejoin="round"/></svg>';

    function model() {
        return root.CuttleChatThreadModel;
    }

    function create(host) {
        const h = host || {};
        const esc = h.escapeHtml || ((s) => String(s == null ? '' : s));
        let enabled = true;
        let started = false;
        let observer = null;
        let observed = null;
        let rafId = 0;
        let saveTimer = 0;
        let applying = false;

        // Per-chat state, swapped when the chat changes.
        let sid = null;
        let store = null; // { w, at, open: {key: bool}, pending: {key: [unreadKey]} }
        let viewBaseline = null; // previous visit watermark, fixed for this view
        let hadStore = false;
        const openNow = new Set(); // threads open in this view
        const seenNow = new Map(); // key → Set(unread keys seen this view)
        const elKey = new WeakMap(); // row → thread key it was in last
        const shown = new WeakSet(); // rows you have seen on screen in this view
        const wasLive = new Map(); // thread key → its root was live last pass

        function storageGet(key) {
            try { return h.storage ? h.storage.getItem(key) : null; } catch (_) { return null; }
        }

        function storageSet(key, value) {
            try { if (h.storage) h.storage.setItem(key, value); } catch (_) {}
        }

        function loadSession(next) {
            flushSave();
            sid = next;
            openNow.clear();
            seenNow.clear();
            wasLive.clear();
            store = null;
            hadStore = false;
            viewBaseline = null;
            if (sid == null || sid === '') return;
            const raw = storageGet(STORE_PREFIX + sid);
            if (raw) {
                try { store = JSON.parse(raw); } catch (_) { store = null; }
            }
            hadStore = !!(store && typeof store === 'object');
            if (!hadStore) store = {};
            store.open = store.open && typeof store.open === 'object' ? store.open : {};
            store.pending = store.pending && typeof store.pending === 'object' ? store.pending : {};
            if (hadStore && Number.isFinite(Number(store.w))) {
                viewBaseline = { messageId: Number(store.w), at: Number(store.at) || 0 };
            }
        }

        function ensureSession() {
            const cur = h.sessionId ? h.sessionId() : null;
            const norm = cur == null ? null : String(cur);
            if (store === null || norm !== sid) loadSession(norm);
        }

        function trim(map) {
            const keys = Object.keys(map);
            if (keys.length <= MAX_THREAD_KEYS) return;
            keys.slice(0, keys.length - MAX_THREAD_KEYS).forEach((k) => { delete map[k]; });
        }

        function scheduleSave() {
            if (sid == null || !store) return;
            if (saveTimer) return;
            saveTimer = (h.win || root).setTimeout(flushSave, 400);
        }

        function flushSave() {
            if (saveTimer) {
                (h.win || root).clearTimeout(saveTimer);
                saveTimer = 0;
            }
            if (sid == null || !store) return;
            trim(store.open);
            trim(store.pending);
            storageSet(STORE_PREFIX + sid, JSON.stringify(store));
        }

        function rowsOf(box) {
            return Array.from(box.children).filter((el) => el.classList
                && el.classList.contains('message') && !el.classList.contains('system'));
        }

        function readRecord(el) {
            const ts = el.querySelector('.message-timestamp');
            const mid = el.dataset.messageId;
            return {
                role: el.classList.contains('user') ? 'user' : (el.classList.contains('assistant') ? 'assistant' : ''),
                steered: el.classList.contains('is-steered'),
                steerIdle: el.dataset.steerIdle === '1',
                turnId: String(el.dataset.turnId || el.dataset.queryId || ''),
                messageId: mid != null && mid !== '' ? Number(mid) : null,
                live: LIVE_IDS.has(el.id),
                ts: ts ? Number(ts.getAttribute('data-ts')) || null : null,
            };
        }

        function clearRow(el) {
            if (!el.dataset.threadKey) return; // never part of a thread
            el.classList.remove('thread-root', 'thread-open', ...REPLY_CLASSES);
            delete el.dataset.threadKey;
            const wrap = el.querySelector(':scope > .message-content-wrapper');
            if (!wrap) return;
            wrap.querySelectorAll(':scope > .chat-thread-bar, :scope > .chat-thread-subagents, :scope > .chat-thread-tail')
                .forEach((n) => n.remove());
        }

        function previewFor(thread, rows) {
            const latest = thread.latest;
            if (!latest) return '';
            const M = model();
            const el = rows[latest.index];
            if (!el) return '';
            const sender = el.querySelector('.message-sender');
            const content = el.querySelector('.message-content');
            const who = (sender && sender.textContent.trim()) || (latest.kind === M.KIND.STEER ? 'You' : 'Agent');
            const text = LIVE_IDS.has(el.id)
                ? ((el.querySelector('.typing-status') || {}).textContent || 'Working…')
                : (content ? content.textContent : '');
            return who + ': ' + M.previewText(text, 110);
        }

        function barHtml(thread, open, unreadCount, preview) {
            const M = model();
            const sum = M.summary(thread);
            const label = open ? 'Hide replies' : sum.label;
            let status = '';
            if (thread.live) {
                status = '<span class="chat-thread-status is-live"><span class="chat-thread-pulse" aria-hidden="true"></span>working</span>';
            } else if (!open && unreadCount) {
                status = '<span class="chat-thread-status is-unread"><span class="chat-thread-dot" aria-hidden="true"></span>'
                    + unreadCount + ' new</span>';
            }
            const aria = (open ? 'Hide ' : 'Show ') + sum.label + (sum.detail ? ' (' + sum.detail + ')' : '')
                + (unreadCount && !open ? ', ' + unreadCount + ' new' : '');
            const showPreview = !open && preview && (unreadCount || thread.live);
            return '<button type="button" class="chat-thread-toggle" aria-expanded="' + (open ? 'true' : 'false') + '"'
                + ' data-thread-key="' + esc(thread.key) + '" aria-label="' + esc(aria) + '">'
                + CHEVRON
                + '<span class="chat-thread-label">' + esc(label) + '</span>'
                + (sum.detail ? '<span class="chat-thread-detail">' + esc(sum.detail) + '</span>' : '')
                + status
                + '</button>'
                + (showPreview ? '<div class="chat-thread-preview" title="' + esc(preview) + '">' + esc(preview) + '</div>' : '');
        }

        /**
         * "Hide replies" at the end of an open thread, so a long thread can be
         * closed without scrolling back up to its root's bar.
         */
        function mountTail(el, thread, want) {
            const wrap = el.querySelector(':scope > .message-content-wrapper');
            if (!wrap) return;
            let tail = wrap.querySelector(':scope > .chat-thread-tail');
            if (!want) {
                if (tail) tail.remove();
                return;
            }
            if (!tail) {
                tail = h.doc.createElement('div');
                tail.className = 'chat-thread-tail';
                tail.setAttribute('data-chat-find-skip', '');
            }
            if (tail.parentNode !== wrap || tail.nextElementSibling) wrap.appendChild(tail);
            const html = '<button type="button" class="chat-thread-toggle chat-thread-toggle--tail" aria-expanded="true"'
                + ' data-thread-key="' + esc(thread.key) + '" aria-label="Hide replies">'
                + CHEVRON + '<span class="chat-thread-label">Hide replies</span></button>';
            if (tail.__cuttleThreadHtml !== html) {
                tail.innerHTML = html;
                tail.__cuttleThreadHtml = html;
            }
        }

        function mountBar(rootEl, thread, open, unreadCount, preview) {
            const wrap = rootEl.querySelector(':scope > .message-content-wrapper');
            if (!wrap) return;
            let bar = wrap.querySelector(':scope > .chat-thread-bar');
            if (!bar) {
                bar = h.doc.createElement('div');
                bar.className = 'chat-thread-bar';
                bar.setAttribute('data-chat-find-skip', '');
            }
            // Keep the bar last in the bubble, under its footer.
            if (bar.parentNode !== wrap || bar.nextElementSibling) wrap.appendChild(bar);
            const html = barHtml(thread, open, unreadCount, preview);
            if (bar.__cuttleThreadHtml !== html) {
                bar.innerHTML = html;
                bar.__cuttleThreadHtml = html;
            }
            bar.classList.toggle('is-open', open);
            bar.classList.toggle('has-unread', !open && unreadCount > 0);
            bar.classList.toggle('is-live', !!thread.live);
            bar.dataset.threadKey = thread.key;


        }

        function priorKeys(els) {
            const out = new Set();
            els.forEach((el) => {
                const k = elKey.get(el);
                if (k) out.add(k);
            });
            return out;
        }

        /** Open or closed, by the rules in the header. Never closes by itself. */
        function resolveOpen(thread, memberEls, prior) {
            const key = thread.key;
            // Carry choices across a key change (live bubble → saved reply).
            prior.forEach((k) => {
                if (k === key) return;
                if (store.open[k] !== undefined && store.open[key] === undefined) store.open[key] = store.open[k];
                if (openNow.has(k)) openNow.add(key);
                if (wasLive.get(k)) wasLive.set(key, true);
                if (seenNow.has(k) && !seenNow.has(key)) seenNow.set(key, seenNow.get(k));
            });
            // A steer you just sent (optimistic, not yet saved) opens its thread.
            const sentNow = memberEls.some((el) => el.classList.contains('is-steered')
                && !el.dataset.messageId && !elKey.has(el));
            if (sentNow) {
                delete store.open[key];
                openNow.add(key);
                return true;
            }
            if (openNow.has(key)) return store.open[key] !== false;
            // Rows you already see on screen never vanish into a thread that
            // forms around them (a reply's root arriving later, a reload mid-turn).
            if (store.open[key] !== false && memberEls.slice(1).some((el) => shown.has(el))) {
                openNow.add(key);
                return true;
            }
            if (store.open[key] !== undefined) {
                if (store.open[key]) openNow.add(key);
                return !!store.open[key];
            }
            if (thread.live) {
                openNow.add(key);
                return true;
            }
            return false;
        }

        function isNewer(k) {
            const b = viewBaseline;
            if (!b) return false;
            if (k.messageId != null && Number.isFinite(k.messageId)) return k.messageId > b.messageId;
            if (Number.isFinite(k.at)) return k.at > b.at;
            return false;
        }

        function unreadFor(thread, open) {
            const key = thread.key;
            let seen = seenNow.get(key);
            if (!seen) {
                seen = new Set();
                seenNow.set(key, seen);
            }
            const pending = new Set(store.pending[key] || []);
            if (open) {
                thread.unreadKeys.forEach((k) => seen.add(k.key));
                if (store.pending[key]) {
                    delete store.pending[key];
                    scheduleSave();
                }
                return 0;
            }
            const unread = [];
            const present = new Set();
            thread.unreadKeys.forEach((k) => {
                present.add(k.key);
                if (seen.has(k.key)) return;
                if (pending.has(k.key) || isNewer(k)) unread.push(k.key);
                else seen.add(k.key);
            });
            // Unread rows not painted yet (older page, still loading) stay unread.
            const keep = Array.from(pending).filter((k) => !present.has(k));
            const next = keep.concat(unread).slice(-20);
            const before = (store.pending[key] || []).join('|');
            if (next.length) store.pending[key] = next;
            else delete store.pending[key];
            if (before !== next.join('|')) scheduleSave();
            return unread.length;
        }

        function maxMessageId(records) {
            let max = -Infinity;
            records.forEach((r) => {
                if (r.messageId != null && Number.isFinite(r.messageId) && r.messageId > max) max = r.messageId;
            });
            return max;
        }

        /** The answer just replaced the live bubble above an open thread: show its start. */
        function revealAnswer(rootEl) {
            const win = h.win || root;
            const go = () => {
                const box = h.container && h.container();
                if (!box || !rootEl.isConnected || !h.revealTop) return;
                // Only when the replies under it pushed the answer mostly out of view.
                const r = rootEl.getBoundingClientRect();
                const b = box.getBoundingClientRect();
                const visible = Math.min(r.bottom, b.bottom) - Math.max(r.top, b.top);
                if (r.top < b.top && visible < b.height * 0.4) h.revealTop(rootEl);
            };
            win.requestAnimationFrame(() => win.requestAnimationFrame(go));
        }

        function reconcile() {
            if (rafId) {
                (h.win || root).cancelAnimationFrame(rafId);
                rafId = 0;
            }
            const box = h.container && h.container();
            if (!box) return;
            if (started && observed !== box) observe(box);
            applying = true;
            try {
                apply(box);
            } finally {
                applying = false;
                if (observer) observer.takeRecords();
            }
        }

        /**
         * The working bubble belongs where its turn row is (`data-turn-message-id`,
         * stamped from live status), not last: after a reload it is appended below
         * steers already saved into its turn. It is not a CH-…-N record, so moving
         * it never renumbers the transcript.
         */
        function placeLiveBubbles(box) {
            LIVE_IDS.forEach((id) => {
                const el = h.doc.getElementById(id);
                if (!el || el.parentElement !== box) return;
                const mid = Number(el.dataset.turnMessageId);
                if (!el.dataset.turnMessageId || !Number.isFinite(mid)) return;
                let anchor = null;
                for (const row of box.children) {
                    if (row === el || !row.classList || !row.classList.contains('message') || LIVE_IDS.has(row.id)) continue;
                    const rid = row.dataset.messageId;
                    if (rid != null && rid !== '' && Number(rid) > mid) {
                        anchor = row;
                        break;
                    }
                }
                if (anchor && el.nextElementSibling !== anchor) box.insertBefore(el, anchor);
            });
        }

        function apply(box) {
            if (enabled) placeLiveBubbles(box);
            const rows = rowsOf(box);
            if (!enabled || !model()) {
                rows.forEach(clearRow);
                return;
            }
            ensureSession();
            const records = rows.map(readRecord);
            const maxId = maxMessageId(records);
            if (!viewBaseline && Number.isFinite(maxId)) {
                // First sight of this chat: what is on screen now counts as read.
                viewBaseline = { messageId: maxId, at: Date.now() };
            }
            const grouped = model().group(records);
            const threadOf = grouped.memberOf;
            rows.forEach((el, i) => {
                if (threadOf[i] < 0) clearRow(el);
            });
            const followLatest = !!(h.isFollowingLatest && h.isFollowingLatest());
            grouped.threads.forEach((thread) => {
                const rootEl = rows[thread.root];
                const memberEls = [rootEl].concat(thread.replies.map((r) => rows[r.index]));
                const prior = priorKeys(memberEls);
                const open = resolveOpen(thread, memberEls, prior);
                const unreadCount = unreadFor(thread, open);
                const rootLive = LIVE_IDS.has(rootEl.id);
                if (wasLive.get(thread.key) && !rootLive && open && thread.replies.length && followLatest) {
                    revealAnswer(rootEl);
                }
                wasLive.set(thread.key, rootLive);

                rootEl.classList.remove(...REPLY_CLASSES);
                mountTail(rootEl, thread, false);
                rootEl.classList.add('thread-root');
                rootEl.classList.toggle('thread-open', open && thread.replies.length > 0);
                rootEl.dataset.threadKey = thread.key;
                elKey.set(rootEl, thread.key);
                thread.replies.forEach((r, n) => {
                    const el = rows[r.index];
                    const wrap = el.querySelector(':scope > .message-content-wrapper');
                    if (wrap) {
                        wrap.querySelectorAll(':scope > .chat-thread-bar, :scope > .chat-thread-subagents')
                            .forEach((x) => x.remove());
                    }
                    mountTail(el, thread, open && n === thread.replies.length - 1 && thread.replies.length > 1);
                    el.classList.remove('thread-root', 'thread-open');
                    el.classList.add('thread-reply');
                    el.classList.toggle('thread-reply--steer', r.kind === 'steer');
                    el.classList.toggle('thread-reply--followup', r.kind === 'followup');
                    el.classList.toggle('thread-reply-last', n === thread.replies.length - 1);
                    el.classList.toggle('thread-reply-hidden', !open);
                    el.dataset.threadKey = thread.key;
                    elKey.set(el, thread.key);
                });
                const preview = (!open && (unreadCount || thread.live)) ? previewFor(thread, rows) : '';
                mountBar(rootEl, thread, open, unreadCount, preview);
            });
            rows.forEach((el) => {
                if (!el.classList.contains('thread-reply-hidden')) shown.add(el);
            });
            if (Number.isFinite(maxId) && store && !(Number(store.w) >= maxId)) {
                store.w = maxId;
                store.at = Date.now();
                scheduleSave();
            }
        }

        function schedule() {
            if (rafId) return;
            const win = h.win || root;
            rafId = win.requestAnimationFrame(() => {
                rafId = 0;
                reconcile();
            });
        }

        function insideOwnUi(node) {
            const el = node && (node.nodeType === 1 ? node : node.parentElement);
            return !!(el && el.closest && el.closest('.chat-thread-bar, .chat-thread-subagents'));
        }

        function onMutations(list) {
            if (applying) return;
            let structural = false;
            let relevant = false;
            for (const m of list) {
                if (insideOwnUi(m.target)) continue;
                relevant = true;
                if (m.type === 'attributes' || m.target === observed) {
                    structural = true;
                    break;
                }
            }
            if (!relevant) return;
            // Rows added/removed or re-keyed: settle before paint (no flash of
            // a hidden reply). Text inside a row (streaming, cards): next frame.
            if (structural) reconcile();
            else schedule();
        }

        function onClick(e) {
            const btn = e.target && e.target.closest && e.target.closest('.chat-thread-toggle');
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            const key = btn.getAttribute('data-thread-key') || '';
            if (!key) return;
            ensureSession();
            const isOpen = btn.getAttribute('aria-expanded') === 'true';
            const fromTail = btn.classList.contains('chat-thread-toggle--tail');
            if (isOpen) {
                openNow.delete(key);
                store.open[key] = false;
            } else {
                openNow.add(key);
                store.open[key] = true;
            }
            scheduleSave();
            reconcile();
            const again = h.container && h.container()
                && h.container().querySelector('.chat-thread-toggle[data-thread-key="' + cssEscape(key) + '"]');
            if (again && fromTail && again.scrollIntoView) {
                // Closed from the end: bring the root's bar back into view.
                try { again.scrollIntoView({ block: 'nearest' }); } catch (_) {}
            }
            if (again && again.focus) {
                try { again.focus({ preventScroll: true }); } catch (_) {}
            }
        }

        function cssEscape(s) {
            const css = (h.win || root).CSS;
            return css && css.escape ? css.escape(s) : String(s).replace(/["\\]/g, '\\$&');
        }

        /** Open the thread holding `el` (if any) so it can be shown. Returns true when it opened one. */
        function reveal(el) {
            if (!el || !el.classList || !el.classList.contains('thread-reply-hidden')) return false;
            const key = el.dataset.threadKey || '';
            if (!key) return false;
            ensureSession();
            openNow.add(key);
            if (store.open[key] === false) delete store.open[key];
            scheduleSave();
            reconcile();
            return true;
        }

        function onReveal(e) {
            const target = e.target && e.target.closest ? e.target.closest('.message') : null;
            reveal(target);
        }

        function observe(box) {
            if (observer) observer.disconnect();
            if (observed) {
                observed.removeEventListener('click', onClick);
                observed.removeEventListener('cuttle:reveal', onReveal);
            }
            observed = box;
            const MO = (h.win || root).MutationObserver;
            if (MO) {
                observer = new MO(onMutations);
                observer.observe(box, {
                    childList: true, subtree: true, attributes: true,
                    attributeFilter: ['id', 'data-turn-id', 'data-query-id', 'data-message-id'],
                });
            }
            box.addEventListener('click', onClick);
            box.addEventListener('cuttle:reveal', onReveal);
        }

        function start(on) {
            enabled = on !== false;
            started = true;
            const box = h.container && h.container();
            if (box) observe(box);
            reconcile();
        }

        function setEnabled(on) {
            enabled = !!on;
            reconcile();
        }

        /**
         * Experimental flag `chat_reply_threads` (default on): off only when the
         * server lists it off or the kill switch is set. A server that does not
         * list it yet (older Flask) keeps the default, so threads never flash
         * on then off. A failed fetch keeps the current state.
         */
        function syncFlag(fetchFn) {
            if (typeof fetchFn !== 'function') return Promise.resolve(enabled);
            return Promise.resolve()
                .then(() => fetchFn('/api/experimental/flags', { credentials: 'include', cache: 'no-store' }))
                .then((r) => r.json())
                .then((d) => {
                    if (!d || !d.success) return enabled;
                    const spec = (d.flags || []).find((f) => f && f.id === 'chat_reply_threads');
                    const on = !d.kill_switch && !(spec && !spec.enabled);
                    if (on !== enabled) setEnabled(on);
                    return on;
                })
                .catch(() => enabled);
        }

        return { start, setEnabled, syncFlag, reconcile, reveal, flush: flushSave };
    }

    const api = { create };

    const ns = (root.CuttleChatThreads = root.CuttleChatThreads || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
