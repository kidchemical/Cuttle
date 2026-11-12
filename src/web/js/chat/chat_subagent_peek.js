/* ================================================================
   Cuttle Chat — sub-agent peek panel (chat_subagent_peek.js).
   Owner: the overlay a fleet card opens inside its own viewport pane.
   Clicking a card shows that child chat in a drawer over this chat
   (the real chat page in a frame — live, same agents and forms, as
   the IDE embeds it) instead of navigating the pane away. The
   drawer's "Open chat" button navigates the pane for real; ✕, Esc
   or the backdrop close it.

   Interface: `create(deps)` → { open(handle, opts), close(), isOpen(),
   current() }. deps: { win, doc, parseChatHandleToken, navigate(handle) }. `peekSrc` / `headerModel` are pure (no DOM).
   The page owns the click → open call site. Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    // Pane broadcasts relayed to the nested chat so a theme/wallpaper change
    // while the drawer is open updates it too (same set as ide_chat.js).
    const APPEARANCE = new Set(['cuttle-theme-change', 'cuttle-video-state',
        'cuttle-bg-effect-blend', 'cuttle-ui-anim-change', 'cuttle-shell-visibility']);

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
                + '<span class="subagent-peek-title"></span>'
                + '<span class="subagent-peek-handle"></span>'
                + '<span class="subagent-peek-status"></span>'
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

        function open(raw, opts) {
            const parsed = deps.parseChatHandleToken(raw);
            const src = peekSrc(parsed);
            if (!src) return false;
            if (!el) build();
            const head = headerModel(parsed, opts);
            el.querySelector('.subagent-peek-title').textContent = head.title;
            el.querySelector('.subagent-peek-handle').textContent = head.handle;
            const status = el.querySelector('.subagent-peek-status');
            status.textContent = head.status;
            status.hidden = !head.status;
            el.querySelector('.subagent-peek-panel').setAttribute('aria-label', 'Sub-agent chat: ' + head.title);
            if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
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

    const api = { create, peekSrc, headerModel };

    const ns = (root.CuttleChatSubagentPeek = root.CuttleChatSubagentPeek || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
