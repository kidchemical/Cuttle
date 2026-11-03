/**
 * CuttleChatStatusMotion — "Rise" style for the live activity status line.
 *
 * Settings → Appearance → Activity status style. ui_boot.js mirrors
 * localStorage `cuttleActivityStatusStyle` onto <html data-activity-status>.
 * In `rise` mode each `.typing-status` text change animates: the new line
 * rises into place while fading in, and the previous line rises away while
 * fading out (css/chat_activity_status.css).
 *
 * The writers (chat_page.js, chat_voice.js, supervised_control.js, …) keep
 * setting `el.textContent`; this module only observes. The outgoing line is
 * a CSS ::before fed from `data-status-prev`, so `el.textContent` is always
 * exactly the current status — callers that compare it keep working.
 */
(function (global) {
    'use strict';

    var STATUS_SELECTOR = '.typing-status';
    var LINE_CLASS = 'status-motion-line';
    var PHASE_A = 'status-motion-a';
    var PHASE_B = 'status-motion-b';
    var lastText = typeof WeakMap === 'function' ? new WeakMap() : null;
    var observer = null;

    function riseEnabled() {
        var root = document.documentElement;
        return !!root && root.getAttribute('data-activity-status') === 'rise';
    }

    /** Wrap the element's text in one animated line; ghost the old text. */
    function sync(el) {
        if (!el || !lastText) return;
        var text = el.textContent || '';
        var prev = lastText.has(el) ? lastText.get(el) : null;
        var line = el.firstElementChild;
        var wrapped = el.childNodes.length === 1 && line && line.classList.contains(LINE_CLASS);
        if (wrapped && prev === text) return;
        lastText.set(el, text);

        var fresh = document.createElement('span');
        fresh.className = LINE_CLASS;
        fresh.textContent = text;
        el.replaceChildren(fresh);

        if (prev != null && prev !== text && prev.trim()) {
            el.setAttribute('data-status-prev', prev);
            // Alternate two identical keyframes so the ghost restarts
            // without forcing a reflow.
            var useA = !el.classList.contains(PHASE_A);
            el.classList.toggle(PHASE_A, useA);
            el.classList.toggle(PHASE_B, !useA);
        }
    }

    function statusFor(node) {
        var el = node && (node.nodeType === 1 ? node : node.parentElement);
        return el && el.closest ? el.closest(STATUS_SELECTOR) : null;
    }

    function onMutations(records) {
        if (!riseEnabled()) return;
        var seen = new Set();
        for (var i = 0; i < records.length; i++) {
            var r = records[i];
            var hit = statusFor(r.target);
            if (hit) seen.add(hit);
            if (r.type !== 'childList') continue;
            for (var j = 0; j < r.addedNodes.length; j++) {
                var n = r.addedNodes[j];
                if (n.nodeType !== 1) continue;
                if (n.matches(STATUS_SELECTOR)) seen.add(n);
                else if (n.firstElementChild) {
                    var found = n.querySelectorAll(STATUS_SELECTOR);
                    for (var k = 0; k < found.length; k++) seen.add(found[k]);
                }
            }
        }
        seen.forEach(sync);
    }

    function start() {
        if (observer || typeof MutationObserver !== 'function' || !document.body) return;
        observer = new MutationObserver(onMutations);
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
        if (riseEnabled()) {
            var els = document.querySelectorAll(STATUS_SELECTOR);
            for (var i = 0; i < els.length; i++) sync(els[i]);
        }
    }

    global.CuttleChatStatusMotion = { start: start, sync: sync, riseEnabled: riseEnabled };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})(window);
