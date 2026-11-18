/* ================================================================
   Cuttle Chat — sub-agent peek panel (chat_subagent_peek.js).
   Owner: the overlay a fleet card opens inside its own viewport pane.
   Clicking a card shows that child chat in a drawer over this chat
   (the real chat page in a frame — live, same agents and forms, as
   the IDE embeds it) instead of navigating the pane away. The
   drawer's "Open chat" button navigates the pane for real; ✕, Esc
   or the backdrop close it.

   Layout: a side drawer (default) or a centred dialog, chosen in
   Settings → Chat (`cuttleSubagentPeekLayout`), read on each open. Studio
   (L4) uses `push`: the tray sits at the right and the thread shifts left,
   with no backdrop and the same frame. Below ~900px of pane width Studio falls
   back to the side overlay. Esc closes; focus returns to the card.

   Interface: `create(deps)` → { open(handle, opts), close(), isOpen(),
   current() }. deps: { win, doc, parseChatHandleToken, navigate(handle),
   isStudio? }. `peekSrc` / `headerModel` / `layoutFor` / `layoutForMode` are
   pure (no DOM). The page owns the click → open call site; the tray header's
   identity is copied from the clicked card. Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    // Pane broadcasts relayed to the nested chat so a theme/wallpaper change
    // while the drawer is open updates it too (same set as ide_chat.js).
    const APPEARANCE = new Set(['cuttle-theme-change', 'cuttle-video-state',
        'cuttle-bg-effect-blend', 'cuttle-ui-anim-change', 'cuttle-shell-visibility']);

    // Settings → Chat → "Sub-agent chat opens": 'side' (default) or 'center'.
    const LAYOUT_KEY = 'cuttleSubagentPeekLayout';
    // Studio's push tray needs this much pane width; narrower panes overlay.
    const PUSH_MIN_WIDTH = 900;
    const PUSH_CLASS = 'cuttle-peek-push';

    /** Layout name from a stored value; anything unknown is the side drawer. Pure. */
    function layoutFor(raw) {
        return raw === 'center' ? 'center' : 'side';
    }

    /**
     * The layout to open with. Classic keeps the Settings choice. Studio pushes
     * the thread when the pane is wide enough, else overlays the side drawer. Pure.
     */
    function layoutForMode(raw, opts) {
        const o = opts || {};
        if (!o.studio) return layoutFor(raw);
        return Number(o.width) >= PUSH_MIN_WIDTH ? 'push' : 'side';
    }

    /** Frame URL for a parsed handle; `-N` bubble refs carry through as `msg`. */
    function peekSrc(parsed) {
        if (!parsed || !parsed.sessionId) return '';
        let src = '/chat_page.html?chat=' + encodeURIComponent(String(parsed.sessionId)) + '&peek=1';
        if (parsed.messageIndex) src += '&msg=' + encodeURIComponent(String(parsed.messageIndex));
        return src;
    }

    /** Header text from the card the user clicked (falls back to the handle). */
    function headerModel(parsed, opts) {
        const o = opts || {};
        const handle = parsed ? parsed.display : '';
        const title = String(o.title || '').trim() || handle;
        return { title, handle: title === handle ? '' : handle, status: String(o.status || '').trim() };
    }

    function create(deps) {
        const win = deps.win;
        const doc = deps.doc;
        let el = null;
        let frame = null;
        let openHandle = '';
        let lastFocus = null;

        function readLayoutSetting() {
            try { return win.localStorage.getItem(LAYOUT_KEY); } catch (_) { return null; }
        }

        function onKey(e) {
            if (e.key === 'Escape' && openHandle) {
                e.preventDefault();
                e.stopPropagation();
                close();
            }
        }

        function bindFrameKeys() {
            // Esc pressed inside the nested chat never reaches this document.
            try { frame.contentWindow.document.addEventListener('keydown', onKey, true); } catch (_) {}
        }

        function build() {
            el = doc.createElement('div');
            el.className = 'subagent-peek';
            el.hidden = true;
            el.innerHTML = '<div class="subagent-peek-backdrop" data-peek-close></div>'
                + '<section class="subagent-peek-panel" role="dialog" aria-modal="true" aria-label="Sub-agent chat">'
                + '<header class="subagent-peek-head">'
                + '<span class="subagent-peek-id">'
                + '<span class="subagent-peek-line subagent-peek-line--1">'
                + '<span class="subagent-peek-identity" hidden></span><span class="subagent-peek-title"></span></span>'
                + '<span class="subagent-peek-line subagent-peek-line--2">'
                + '<span class="subagent-peek-handle"></span><span class="subagent-peek-parent" hidden></span></span>'
                + '<span class="subagent-peek-status"></span>'
                + '</span>'
                + '<span class="subagent-peek-spacer"></span>'
                + '<button type="button" class="subagent-peek-open" title="Open this chat in the pane">Open chat</button>'
                + '<button type="button" class="subagent-peek-close" data-peek-close title="Close (Esc)" aria-label="Close">✕</button>'
                + '</header>'
                + '<iframe class="subagent-peek-frame" title="Sub-agent chat"></iframe>'
                + '</section>';
            frame = el.querySelector('iframe');
            frame.addEventListener('load', bindFrameKeys);
            el.addEventListener('click', (e) => {
                if (e.target.closest('[data-peek-close]')) { close(); return; }
                if (e.target.closest('.subagent-peek-open')) {
                    const handle = openHandle;
                    close();
                    if (handle) deps.navigate(handle);
                }
            });
            doc.addEventListener('keydown', onKey, true);
            win.addEventListener('message', (e) => {
                if (!openHandle || e.origin !== win.location.origin || e.source !== win.parent) return;
                if (!e.data || !APPEARANCE.has(e.data.type)) return;
                try { frame.contentWindow.postMessage(e.data, win.location.origin); } catch (_) {}
            });
            doc.body.appendChild(el);
        }

        function studioOn() {
            if (typeof deps.isStudio === 'function') return !!deps.isStudio();
            const display = root.CuttleChatDisplay;
            return !!(display && typeof display.isStudio === 'function' && display.isStudio());
        }

        /** The clicked card's identity (dot + name), copied from its markup. '' when none. */
        function identityHtmlFor(handle) {
            const cards = doc.querySelectorAll('.subagent-fleet-card[data-chat-handle]');
            for (let i = 0; i < cards.length; i++) {
                if (cards[i].getAttribute('data-chat-handle') !== handle) continue;
                const id = cards[i].querySelector('.agent-identity');
                return id ? id.outerHTML : '';
            }
            return '';
        }

        function setPush(on) {
            if (doc.documentElement) doc.documentElement.classList.toggle(PUSH_CLASS, !!on);
        }

        /** The Studio card for a handle on this page, or null. */
        function cardFor(handle) {
            const cards = doc.querySelectorAll('.subagent-fleet-card[data-chat-handle]');
            for (let i = 0; i < cards.length; i++) {
                if (cards[i].getAttribute('data-chat-handle') === handle) return cards[i];
            }
            return null;
        }

        /**
         * The card whose tray is open carries the selected state (accent border);
         * closing clears it. Studio cards only. The handle also rides the frame root so
         * a repaint of the work panel keeps the selection (chat_subagent_launchers.js).
         */
        function markSelected(handle) {
            doc.querySelectorAll('.studio-fleet-card.is-selected').forEach((c) => {
                c.classList.remove('is-selected');
                c.removeAttribute('aria-pressed');
            });
            if (doc.documentElement) {
                if (handle) doc.documentElement.setAttribute('data-peek-handle', handle);
                else doc.documentElement.removeAttribute('data-peek-handle');
            }
            if (!handle || !studioOn()) return;
            doc.querySelectorAll('.studio-fleet-card[data-chat-handle]').forEach((c) => {
                if (c.getAttribute('data-chat-handle') !== handle) return;
                c.classList.add('is-selected');
                c.setAttribute('aria-pressed', 'true');
            });
        }

        function open(raw, opts) {
            const parsed = deps.parseChatHandleToken(raw);
            const src = peekSrc(parsed);
            if (!src) return false;
            if (!el) build();
            const card = cardFor(parsed.display);
            // The title falls back to the card's child title when the call site gives none.
            const cardTitle = card ? String(card.getAttribute('data-card-title') || '') : '';
            const head = headerModel(parsed, Object.assign({}, opts || {}, { title: (opts && opts.title) || cardTitle }));
            el.querySelector('.subagent-peek-title').textContent = head.title;
            const studio = studioOn();
            // Studio's second line always names the handle; Classic shows it only when it is not the title.
            el.querySelector('.subagent-peek-handle').textContent = studio ? parsed.display : head.handle;
            const status = el.querySelector('.subagent-peek-status');
            status.textContent = head.status;
            status.hidden = !head.status;
            // Studio's parent line: "<handle> · sub-agent of <parent Tasks list>" (Classic keeps its header).
            const parentEl = el.querySelector('.subagent-peek-parent');
            const parentTitle = card ? String(card.getAttribute('data-parent-title') || '').trim() : '';
            parentEl.textContent = ' · sub-agent' + (parentTitle ? ' of ' + parentTitle : '');
            parentEl.hidden = !studio;
            const identity = el.querySelector('.subagent-peek-identity');
            const idHtml = identityHtmlFor(parsed.display);
            // Studio: "<agent> · <child title>" on the first line.
            identity.innerHTML = idHtml ? idHtml + '<span class="subagent-peek-sep"> · </span>' : '';
            identity.hidden = !identity.innerHTML;
            markSelected(parsed.display);
            el.querySelector('.subagent-peek-panel').setAttribute('aria-label', 'Sub-agent chat: ' + head.title);
            if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
            const layout = layoutForMode(readLayoutSetting(), {
                studio: studioOn(),
                width: win.innerWidth,
            });
            el.dataset.layout = layout;
            setPush(layout === 'push');
            openHandle = parsed.display;
            if (el.hidden) lastFocus = doc.activeElement;
            el.hidden = false;
            el.querySelector('.subagent-peek-close').focus({ preventScroll: true });
            return true;
        }

        function close() {
            if (!el || el.hidden) return;
            el.hidden = true;
            openHandle = '';
            setPush(false);
            markSelected('');
            // Drop the nested chat: it stops polling and streaming while closed.
            frame.removeAttribute('src');
            try { frame.contentWindow.location.replace('about:blank'); } catch (_) {}
            if (lastFocus && typeof lastFocus.focus === 'function') {
                try { lastFocus.focus({ preventScroll: true }); } catch (_) {}
            }
            lastFocus = null;
        }

        return { open, close, isOpen: () => !!openHandle, current: () => openHandle };
    }

    const api = { create, peekSrc, headerModel, layoutFor, layoutForMode, LAYOUT_KEY, PUSH_MIN_WIDTH, PUSH_CLASS };

    const ns = (root.CuttleChatSubagentPeek = root.CuttleChatSubagentPeek || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
