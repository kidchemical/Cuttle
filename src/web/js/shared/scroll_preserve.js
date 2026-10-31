/**
 * Scroll preservation across innerHTML swaps (poll refreshes / re-renders).
 *
 * One owner for the recurring "a periodic refresh reset my scroll" bug:
 *
 *   CuttleScroll.setHtml(el, html, { selector: '.jobs-table-scroll' });
 *
 * - Skips the swap when `html` is the markup this helper last wrote and the
 *   element's children are still the ones it wrote. The browser re-serializes
 *   markup (quotes, entities), so `el.innerHTML === html` alone never matches
 *   and every poll rebuilt the DOM.
 * - Restores the scroll offsets of `el`, of `selector` matches, and of every
 *   scrolled descendant (inner <pre>, tables, nested lists).
 * - Keeps <details> open/closed state and the focused control.
 *
 * Descendants are matched across the swap by id, then a data key
 * (data-key, data-id, data-path, data-rel-path, data-project-key, data-row),
 * then tag/class + occurrence order.
 */
(function (root) {
    'use strict';

    const KEY_ATTRS = ['data-key', 'data-id', 'data-path', 'data-rel-path', 'data-project-key', 'data-row'];
    const rendered = typeof WeakMap === 'function' ? new WeakMap() : null;

    function list(el, selector) {
        return el && el.querySelectorAll ? Array.from(el.querySelectorAll(selector)) : [];
    }

    function keyOf(node, seen) {
        if (node.id) return '#' + node.id;
        for (const attr of KEY_ATTRS) {
            const v = node.getAttribute && node.getAttribute(attr);
            if (v != null && v !== '') return attr + '=' + v;
        }
        const cls = typeof node.className === 'string' ? node.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
        const base = String(node.tagName || '').toLowerCase() + '.' + cls;
        const n = seen[base] = (seen[base] || 0) + 1;
        return base + ':' + n;
    }

    function keyed(nodes) {
        const seen = {};
        return nodes.map((node) => ({ node, key: keyOf(node, seen) }));
    }

    function capture(el, selector) {
        const scrolls = [{ key: '', node: el, left: el.scrollLeft || 0, top: el.scrollTop || 0 }];
        const selected = selector ? list(el, selector) : [];
        selected.forEach((node, i) => {
            scrolls.push({ sel: i, left: node.scrollLeft || 0, top: node.scrollTop || 0 });
        });
        const all = list(el, '*');
        keyed(all).forEach(({ node, key }) => {
            if (node.scrollTop || node.scrollLeft) scrolls.push({ key, left: node.scrollLeft, top: node.scrollTop });
        });
        const details = keyed(list(el, 'details')).map(({ node, key }) => ({ key, open: !!node.open }));
        let focus = null;
        const active = el.ownerDocument && el.ownerDocument.activeElement;
        if (active && active !== el && el.contains && el.contains(active)) {
            const hit = keyed(all).find((k) => k.node === active);
            if (hit) {
                focus = { key: hit.key };
                try { focus.start = active.selectionStart; focus.end = active.selectionEnd; } catch (_) {}
            }
        }
        return { scrolls, details, focus, selected: selected.length };
    }

    function restore(el, snap, selector) {
        const all = keyed(list(el, '*'));
        const byKey = new Map(all.map((k) => [k.key, k.node]));
        const detailsByKey = new Map(keyed(list(el, 'details')).map((k) => [k.key, k.node]));
        snap.details.forEach((d) => {
            const node = detailsByKey.get(d.key);
            if (node && node.open !== d.open) node.open = d.open;
        });
        const selected = selector ? list(el, selector) : [];
        snap.scrolls.forEach((s) => {
            let node = null;
            if (s.node) node = s.node;
            else if (s.sel != null) node = selected[s.sel] || (selected.length ? null : el);
            else node = byKey.get(s.key);
            if (!node) return;
            node.scrollLeft = s.left;
            node.scrollTop = s.top;
        });
        if (snap.focus) {
            const node = byKey.get(snap.focus.key);
            if (node && typeof node.focus === 'function') {
                try { node.focus({ preventScroll: true }); } catch (_) { node.focus(); }
                try {
                    if (snap.focus.start != null) node.setSelectionRange(snap.focus.start, snap.focus.end);
                } catch (_) {}
            }
        }
    }

    function stamp(el, html) {
        return { html, first: el.firstChild, count: el.childNodes ? el.childNodes.length : 0 };
    }

    function unchanged(el, html) {
        const prev = rendered && rendered.get(el);
        if (prev) {
            const now = stamp(el, html);
            return prev.html === html && now.first === prev.first && now.count === prev.count;
        }
        return el.innerHTML === html;
    }

    function setHtml(el, html, opts) {
        if (!el) return false;
        opts = opts || {};
        html = String(html == null ? '' : html);
        if (!opts.force && unchanged(el, html)) {
            if (rendered && !rendered.get(el)) rendered.set(el, stamp(el, html));
            return false;
        }
        const selector = opts.selector || '[data-scroll-preserve]';
        const snap = capture(el, selector);
        el.innerHTML = html;
        if (rendered) rendered.set(el, stamp(el, html));
        restore(el, snap, selector);
        return true;
    }

    /** Forget what setHtml last wrote (call after replacing children by hand). */
    function forget(el) {
        if (rendered && el) rendered.delete(el);
    }

    root.CuttleScroll = { setHtml: setHtml, forget: forget };
})(globalThis);
