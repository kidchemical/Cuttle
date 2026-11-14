/* ================================================================
   Cuttle Chat — quote clips (chat_quote_clips.js).
   Owner: replying to a piece of the transcript. Selecting text in a
   message shows a small toolbar above it (Agree, Disagree, Explain,
   Investigate, Do this, Reply). A button stages a **quote clip** in the
   composer: the selected text plus a reply — canned for the quick
   buttons, typed in a small panel for Reply. Clips are removable cards
   above the composer text, several at once, kept per chat until sent.

   A clip is plain message text: at send the page asks `source(input)`
   and each clip becomes a Markdown blockquote followed by its reply,
   ahead of whatever the user typed. Nothing new reaches the server.
   Experimental flag `chat_quote_clips` (default on).

   Pure (node-testable): `ACTIONS`, `actionFor`, `normalizeQuote`,
   `makeClip`, `clipText`, `composeBody`, `excerpt`, `placeToolbar`.
   Controller: `create(host)` →
     { start, setEnabled, syncFlag, source, compose, clear, render,
       count, add, hide }
   host: { doc, win, container() → transcript element,
           composers: [{ input, before }]  (clip row goes before `before`),
           sessionId() → id or '', storage (localStorage-like),
           onChange()  (composer height may have changed) }.
   Composed once by chat_page.js; it never reads page scope.
   ================================================================ */
(function (root) {
    'use strict';

    const MAX_QUOTE_CHARS = 4000;
    const MAX_CLIPS = 12;

    /** Toolbar buttons, in order. `reply: null` opens the reply panel. */
    const ACTIONS = [
        { id: 'agree', label: 'Agree', icon: '👍', reply: 'I agree with this.' },
        { id: 'disagree', label: 'Disagree', icon: '👎', reply: 'I disagree with this.' },
        { id: 'explain', label: 'Explain', icon: '💡', reply: 'Explain this in more detail.' },
        { id: 'investigate', label: 'Investigate', icon: '🔍', reply: 'Investigate this further and report what you find.' },
        { id: 'do', label: 'Do this', icon: '▶', reply: 'Go ahead and do this.' },
        { id: 'reply', label: 'Reply', icon: '↩', reply: null },
    ];

    function actionFor(id) {
        return ACTIONS.find((a) => a.id === id) || null;
    }

    /** Selected text as a quote: trimmed, blank runs collapsed, length capped. */
    function normalizeQuote(text) {
        let s = String(text == null ? '' : text).replace(/\r\n?/g, '\n').replace(/ /g, ' ');
        s = s.split('\n').map((line) => line.replace(/\s+$/, '')).join('\n');
        s = s.replace(/\n{3,}/g, '\n\n').trim();
        if (s.length > MAX_QUOTE_CHARS) s = s.slice(0, MAX_QUOTE_CHARS).replace(/\s+$/, '') + '…';
        return s;
    }

    /** A clip, or null when there is nothing to quote / no reply to give. */
    function makeClip(actionId, quote, replyText) {
        const action = actionFor(actionId);
        const q = normalizeQuote(quote);
        if (!action || !q) return null;
        const reply = String(action.reply != null ? action.reply : (replyText || '')).trim();
        if (!reply) return null;
        return { action: action.id, quote: q, reply };
    }

    /** The message text of one clip: blockquote, blank line, reply. */
    function clipText(clip) {
        const quoted = String(clip.quote || '').split('\n').map((line) => (line ? '> ' + line : '>')).join('\n');
        return quoted + '\n\n' + String(clip.reply || '').trim();
    }

    /**
     * Clips + typed text → the composer body. Clips lead, the typed text
     * closes; typed text that starts a slash command stays first so the
     * command still parses.
     */
    function composeBody(clips, body) {
        const typed = String(body == null ? '' : body).trim();
        const blocks = (clips || []).filter(Boolean).map(clipText);
        if (!blocks.length) return typed;
        if (!typed) return blocks.join('\n\n');
        return typed.charAt(0) === '/'
            ? typed + '\n\n' + blocks.join('\n\n')
            : blocks.join('\n\n') + '\n\n' + typed;
    }

    /** One-line preview of a quote or reply. */
    function excerpt(text, max) {
        const s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
        const n = max || 60;
        return s.length > n ? s.slice(0, n - 1).replace(/\s+$/, '') + '…' : s;
    }

    /**
     * Where a `size` box goes for a selection `rect` in a `view` port:
     * centred above the selection, below when there is no room above (or
     * `preferBelow`, for touch, where the system menu sits above), and
     * always inside the viewport.
     */
    function placeToolbar(rect, size, view, preferBelow) {
        const gap = 8;
        const margin = 6;
        const above = rect.top - size.height - gap;
        const below = rect.bottom + gap;
        const fitsAbove = above >= margin;
        const fitsBelow = below + size.height <= view.height - margin;
        let top;
        if (preferBelow && fitsBelow) top = below;
        else if (fitsAbove) top = above;
        else if (fitsBelow) top = below;
        else top = Math.max(margin, Math.min(above, view.height - size.height - margin));
        let left = rect.left + rect.width / 2 - size.width / 2;
        left = Math.max(margin, Math.min(left, view.width - size.width - margin));
        return { top: Math.round(top), left: Math.round(left) };
    }

    function create(host) {
        const h = host || {};
        const doc = h.doc;
        const win = h.win;
        const composers = (h.composers || []).filter((c) => c && c.before);
        const rows = [];
        const memory = new Map(); // clips of chats with no id yet / no storage
        let enabled = false;
        let started = false;
        let bar = null; // the toolbar or reply panel element
        let mode = ''; // '' | 'toolbar' | 'reply'
        let pendingQuote = '';
        let pointerDown = false;
        let lastPointerTouch = false;
        let timer = 0;
        let barPressUntil = 0;

        // ---- clip state (per chat) ---------------------------------------
        const NEW_KEY = 'cuttle.quoteClips.new'; // a chat with no id yet: memory only
        function key() {
            const sid = typeof h.sessionId === 'function' ? h.sessionId() : '';
            return sid ? 'cuttle.quoteClips.' + sid : NEW_KEY;
        }

        function load() {
            const k = key();
            if (memory.has(k)) return memory.get(k);
            let clips = [];
            try {
                const raw = h.storage && k !== NEW_KEY && h.storage.getItem(k);
                const parsed = raw ? JSON.parse(raw) : [];
                if (Array.isArray(parsed)) {
                    clips = parsed.filter((c) => c && typeof c.quote === 'string' && typeof c.reply === 'string' && actionFor(c.action));
                }
            } catch (_) {}
            memory.set(k, clips);
            return clips;
        }

        function save(clips) {
            const k = key();
            memory.set(k, clips);
            try {
                if (!h.storage || k === NEW_KEY) return;
                if (clips.length) h.storage.setItem(k, JSON.stringify(clips));
                else h.storage.removeItem(k);
            } catch (_) {}
        }

        function changed() {
            render();
            if (typeof h.onChange === 'function') h.onChange();
        }

        function add(actionId, quote, replyText) {
            const clip = makeClip(actionId, quote, replyText);
            if (!clip) return false;
            const clips = load().slice();
            if (clips.length >= MAX_CLIPS) clips.shift();
            clips.push(clip);
            save(clips);
            changed();
            return true;
        }

        function removeAt(idx) {
            const clips = load().slice();
            clips.splice(idx, 1);
            save(clips);
            changed();
        }

        /** Sent: drop this chat's clips (and the ones staged before it had an id). */
        function clear() {
            const had = load().length || (memory.get(NEW_KEY) || []).length;
            memory.delete(NEW_KEY);
            if (!had) return;
            save([]);
            changed();
        }

        function count() {
            return enabled ? load().length : 0;
        }

        /** Composer text with the staged clips folded in. */
        function compose(body) {
            return enabled ? composeBody(load(), body) : String(body == null ? '' : body).trim();
        }

        /** Stand-in for the composer textarea at send: same id, composed value. */
        function source(input) {
            return { id: input.id, value: compose(input.value) };
        }

        // ---- clip cards in the composer ------------------------------------
        function ensureRows() {
            if (rows.length || !doc) return;
            composers.forEach((c) => {
                const row = doc.createElement('div');
                row.className = 'quote-clips-row';
                row.hidden = true;
                c.before.parentNode.insertBefore(row, c.before);
                rows.push(row);
            });
        }

        function render() {
            ensureRows();
            const clips = enabled ? load() : [];
            rows.forEach((row) => {
                row.textContent = '';
                row.hidden = !clips.length;
                clips.forEach((clip, idx) => {
                    const action = actionFor(clip.action);
                    const card = doc.createElement('span');
                    card.className = 'quote-clip';
                    card.setAttribute('data-action', clip.action);
                    card.title = clip.reply + '\n\n“' + clip.quote + '”';
                    const text = doc.createElement('span');
                    text.className = 'quote-clip-text';
                    const head = doc.createElement('span');
                    head.className = 'quote-clip-reply';
                    head.textContent = action.reply != null ? action.icon + ' ' + action.label : action.icon + ' ' + excerpt(clip.reply, 48);
                    const quote = doc.createElement('span');
                    quote.className = 'quote-clip-quote';
                    quote.textContent = excerpt(clip.quote, 80);
                    text.appendChild(head);
                    text.appendChild(quote);
                    const remove = doc.createElement('button');
                    remove.type = 'button';
                    remove.className = 'quote-clip-remove';
                    remove.setAttribute('aria-label', 'Remove quote');
                    remove.textContent = '×';
                    remove.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        removeAt(idx);
                    });
                    card.appendChild(text);
                    card.appendChild(remove);
                    row.appendChild(card);
                });
            });
        }

        // ---- selection toolbar ---------------------------------------------
        function selectionInTranscript() {
            const container = typeof h.container === 'function' ? h.container() : null;
            const sel = win.getSelection ? win.getSelection() : null;
            if (!container || !sel || sel.isCollapsed || !sel.rangeCount) return null;
            const range = sel.getRangeAt(0);
            const inMessage = (node) => {
                const el = node && (node.nodeType === 1 ? node : node.parentElement);
                return !!el && container.contains(el) && !!el.closest('.message-content');
            };
            if (!inMessage(range.startContainer) || !inMessage(range.endContainer)) return null;
            const quote = normalizeQuote(sel.toString());
            if (!quote) return null;
            const rect = range.getBoundingClientRect();
            if (!rect || (!rect.width && !rect.height)) return null;
            return { quote, rect };
        }

        function place(rect) {
            if (!bar) return;
            const size = { width: bar.offsetWidth, height: bar.offsetHeight };
            const view = { width: doc.documentElement.clientWidth, height: win.innerHeight };
            const pos = placeToolbar(rect, size, view, lastPointerTouch && mode === 'toolbar');
            bar.style.top = pos.top + 'px';
            bar.style.left = pos.left + 'px';
        }

        function hide() {
            if (bar) bar.remove();
            bar = null;
            mode = '';
            pendingQuote = '';
        }

        function mount(className, label) {
            if (bar) bar.remove();
            bar = doc.createElement('div');
            bar.className = className;
            bar.setAttribute('role', label === 'toolbar' ? 'toolbar' : 'dialog');
            bar.setAttribute('aria-label', label === 'toolbar' ? 'Reply to selection' : 'Reply to the selected text');
            doc.body.appendChild(bar);
            return bar;
        }

        function showToolbar(found) {
            if (mode === 'toolbar' && pendingQuote === found.quote) {
                place(found.rect);
                return;
            }
            pendingQuote = found.quote;
            mode = 'toolbar';
            const el = mount('quote-toolbar', 'toolbar');
            ACTIONS.forEach((action) => {
                const btn = doc.createElement('button');
                btn.type = 'button';
                btn.className = 'quote-toolbar-btn';
                btn.setAttribute('data-action', action.id);
                btn.title = action.reply != null ? 'Quote this and add: “' + action.reply + '”' : 'Quote this and write a reply';
                const icon = doc.createElement('span');
                icon.className = 'quote-toolbar-icon';
                icon.setAttribute('aria-hidden', 'true');
                icon.textContent = action.icon;
                btn.appendChild(icon);
                btn.appendChild(doc.createTextNode(action.label));
                // Keep the selection while the button is pressed.
                btn.addEventListener('mousedown', (e) => e.preventDefault());
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    const quote = pendingQuote;
                    if (action.reply == null) {
                        showReplyPanel(quote, found.rect);
                        return;
                    }
                    add(action.id, quote);
                    dropSelection();
                    hide();
                });
                el.appendChild(btn);
            });
            place(found.rect);
        }

        function showReplyPanel(quote, rect) {
            mode = 'reply';
            pendingQuote = quote;
            const el = mount('quote-reply-panel', 'dialog');
            const preview = doc.createElement('div');
            preview.className = 'quote-reply-quote';
            preview.textContent = excerpt(quote, 220);
            const input = doc.createElement('textarea');
            input.className = 'quote-reply-input';
            input.rows = 3;
            input.placeholder = 'Reply to this…';
            input.setAttribute('aria-label', 'Your reply');
            const actions = doc.createElement('div');
            actions.className = 'quote-reply-actions';
            const cancel = doc.createElement('button');
            cancel.type = 'button';
            cancel.className = 'quote-reply-cancel';
            cancel.textContent = 'Cancel';
            const accept = doc.createElement('button');
            accept.type = 'button';
            accept.className = 'quote-reply-accept';
            accept.textContent = 'Accept';
            accept.disabled = true;
            const submit = () => {
                if (!input.value.trim()) return;
                add('reply', quote, input.value);
                hide();
            };
            input.addEventListener('input', () => { accept.disabled = !input.value.trim(); });
            input.addEventListener('keydown', (e) => {
                e.stopPropagation();
                if (e.key === 'Escape') { e.preventDefault(); hide(); }
                else if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
            });
            cancel.addEventListener('click', hide);
            accept.addEventListener('click', submit);
            actions.appendChild(cancel);
            actions.appendChild(accept);
            el.appendChild(preview);
            el.appendChild(input);
            el.appendChild(actions);
            place(rect);
            try { input.focus({ preventScroll: true }); } catch (_) { input.focus(); }
        }

        function dropSelection() {
            try { win.getSelection().removeAllRanges(); } catch (_) {}
        }

        function update() {
            timer = 0;
            if (!enabled || mode === 'reply') return;
            const found = pointerDown ? null : selectionInTranscript();
            if (found) showToolbar(found);
            // A tap on the toolbar may collapse a touch selection before its click lands.
            else if (Date.now() > barPressUntil) hide();
        }

        function schedule(delay) {
            if (timer) win.clearTimeout(timer);
            timer = win.setTimeout(update, delay == null ? 0 : delay);
        }

        function start() {
            if (started || !doc || !win) return;
            started = true;
            ensureRows();
            doc.addEventListener('pointerdown', (e) => {
                lastPointerTouch = e.pointerType === 'touch';
                if (bar && bar.contains(e.target)) {
                    barPressUntil = Date.now() + 700;
                    return;
                }
                // A reply being typed survives a stray click; an empty one closes.
                if (mode === 'reply') {
                    const input = bar.querySelector('.quote-reply-input');
                    if (!input || !input.value.trim()) hide();
                    return;
                }
                if (mode === 'toolbar') hide();
                pointerDown = true;
            }, true);
            const release = () => {
                if (!pointerDown) return;
                pointerDown = false;
                schedule(10);
            };
            doc.addEventListener('pointerup', release, true);
            doc.addEventListener('pointercancel', release, true);
            doc.addEventListener('selectionchange', () => { if (!pointerDown) schedule(120); });
            doc.addEventListener('keydown', (e) => { if (e.key === 'Escape' && mode === 'toolbar') hide(); });
            // The transcript scrolls under a fixed toolbar: follow the selection.
            doc.addEventListener('scroll', () => { if (mode === 'toolbar') schedule(0); }, true);
            win.addEventListener('resize', () => { if (mode === 'toolbar') schedule(0); });
            render();
        }

        function setEnabled(on) {
            enabled = !!on;
            if (!enabled) hide();
            changed();
        }

        /**
         * Experimental flag `chat_quote_clips` (default on). Hidden until the
         * flags load; on unless the server lists it off or the kill switch is
         * set. A failed fetch keeps the current state.
         */
        function syncFlag(fetchFn) {
            if (typeof fetchFn !== 'function') return Promise.resolve(enabled);
            return Promise.resolve()
                .then(() => fetchFn('/api/experimental/flags', { credentials: 'include', cache: 'no-store' }))
                .then((r) => r.json())
                .then((d) => {
                    if (!d || !d.success) return enabled;
                    const spec = (d.flags || []).find((f) => f && f.id === 'chat_quote_clips');
                    const on = !d.kill_switch && !(spec && !spec.enabled);
                    if (on !== enabled) setEnabled(on);
                    return on;
                })
                .catch(() => enabled);
        }

        return { start, setEnabled, syncFlag, source, compose, clear, render, count, add, hide };
    }

    const api = { ACTIONS, MAX_QUOTE_CHARS, actionFor, normalizeQuote, makeClip, clipText, composeBody, excerpt, placeToolbar, create };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleChatQuoteClips = api;
})(typeof window !== 'undefined' ? window : globalThis);
