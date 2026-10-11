/**
 * Scroll preservation across innerHTML swaps (poll refreshes / re-renders).
 *
 * One owner for the recurring "a periodic refresh reset my scroll" bug: snapshot
 * the scroll offsets of the matching scroll containers, replace the HTML, then
 * restore them. Use for any container whose innerHTML is rewritten on a timer.
 *
 *   CuttleScroll.setHtml(el, html, { selector: '.jobs-table-scroll' });
 *
 * Falls back to the element itself when the selector matches nothing, so a
 * direct scroll container (el is the scroller) also works.
 */
(function (root) {
    'use strict';

    function scrollers(el, selector) {
        if (selector) {
            const found = Array.from(el.querySelectorAll(selector));
            if (found.length) return found;
        }
        return [el];
    }

    function setHtml(el, html, opts) {
        if (!el) return false;
        if (el.innerHTML === html) return false;
        opts = opts || {};
        const selector = opts.selector || '[data-scroll-preserve]';
        const before = scrollers(el, selector).map((n) => ({
            left: n.scrollLeft,
            top: n.scrollTop,
        }));
        el.innerHTML = html;
        const after = scrollers(el, selector);
        after.forEach((n, i) => {
            const snap = before[i];
            if (!snap) return;
            n.scrollLeft = snap.left;
            n.scrollTop = snap.top;
        });
        return true;
    }

    root.CuttleScroll = { setHtml: setHtml };
})(globalThis);
