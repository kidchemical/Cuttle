/* ================================================================
   Cuttle Chat — auto-hiding scrollbars (chat_scrollbar_autohide.js).
   Owner: the scroll-activity class that reveals a chat scroller's
   scrollbar while it scrolls (and while the pointer sits on the
   scrollbar edge so it can still be grabbed), then fades it out after
   `idleMs`. The look lives in chat_page.css (`.scrollbar-autohide` /
   `.is-scrolling`); this module only toggles the class.

   `create(host)` → { attach(el), destroy() }. host: { doc, win,
   selectors?, idleMs?, edgePx? }.
   ================================================================ */
(function (root) {
    'use strict';

    const IDLE_MS = 900;
    const EDGE_PX = 14;
    const ACTIVE_CLASS = 'is-scrolling';
    const BASE_CLASS = 'scrollbar-autohide';

    /** True when (clientX, clientY) is over el's vertical scrollbar edge. */
    function nearScrollbarEdge(rect, clientX, edgePx) {
        if (!rect) return false;
        const edge = Number(edgePx) || EDGE_PX;
        return clientX <= rect.right && clientX >= rect.right - edge;
    }

    function create(host) {
        const doc = host.doc;
        const win = host.win || root;
        const idleMs = host.idleMs || IDLE_MS;
        const edgePx = host.edgePx || EDGE_PX;
        const timers = new Map();
        const cleanups = [];

        function show(el) {
            el.classList.add(ACTIVE_CLASS);
            clearTimeout(timers.get(el));
            timers.set(el, win.setTimeout(() => {
                if (!el.matches(':active')) el.classList.remove(ACTIVE_CLASS);
                else show(el);   // still dragging the thumb
            }, idleMs));
        }

        function attach(el) {
            if (!el || el.classList.contains(BASE_CLASS)) return;
            el.classList.add(BASE_CLASS);
            const onScroll = () => show(el);
            const onMove = (ev) => {
                if (el.scrollHeight <= el.clientHeight) return;
                if (nearScrollbarEdge(el.getBoundingClientRect(), ev.clientX, edgePx)) show(el);
            };
            el.addEventListener('scroll', onScroll, { passive: true });
            el.addEventListener('pointermove', onMove, { passive: true });
            cleanups.push(() => {
                el.removeEventListener('scroll', onScroll);
                el.removeEventListener('pointermove', onMove);
                clearTimeout(timers.get(el));
                el.classList.remove(BASE_CLASS, ACTIVE_CLASS);
            });
        }

        (host.selectors || ['#chatMessages', '#chatHistory']).forEach((sel) => {
            doc.querySelectorAll(sel).forEach(attach);
        });

        function destroy() {
            cleanups.splice(0).forEach((fn) => fn());
            timers.clear();
        }

        return { attach, destroy };
    }

    root.CuttleChatScrollbarAutohide = { create, nearScrollbarEdge, IDLE_MS };
})(typeof window !== 'undefined' ? window : globalThis);
