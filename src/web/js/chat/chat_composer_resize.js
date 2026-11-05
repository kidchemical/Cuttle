/* ================================================================
   Cuttle Chat — user-resizable composer (chat_composer_resize.js).
   Owner: the drag grips that make a composer taller — bottom-right
   corner on the new-chat (welcome) composer (drag down), top-right corner
   on the in-chat composer (drag up) — and the textarea height decision that combines auto-grow with the
   height the user dragged to. Pure height math is exported for tests;
   `create(host)` owns the grip DOM, pointer drag and the device-local
   saved height. The page keeps calling its autoResize entry, which
   delegates to `controller.fit()`.

   Double-click the grip to return to auto height.
   ================================================================ */
(function (root) {
    'use strict';

    const STORAGE_KEY = 'cuttle.composerHeight.welcome';
    const CHAT_STORAGE_KEY = 'cuttle.composerHeight.chat';
    const AUTO_CAP = 168;   // auto-grow ceiling (matches .chat-input max-height)
    const MIN_HEIGHT = 44;

    function clampHeight(h, max) {
        const n = Number(h);
        if (!isFinite(n)) return null;
        const hi = Math.max(MIN_HEIGHT, Number(max) || 0);
        return Math.round(Math.min(hi, Math.max(MIN_HEIGHT, n)));
    }

    /** Height for the textarea: auto-grow to AUTO_CAP, never below the dragged height. */
    function fitHeight(p) {
        const scroll = Math.max(0, Number(p && p.scrollHeight) || 0);
        const user = p && p.userHeight ? clampHeight(p.userHeight, p.max) : null;
        const auto = Math.min(scroll, AUTO_CAP);
        const height = user ? Math.max(user, auto) : auto;
        return { height, overflow: scroll > height };
    }

    function parseStored(raw) {
        const n = parseInt(raw, 10);
        return isFinite(n) && n >= MIN_HEIGHT ? n : null;
    }

    function create(host) {
        const doc = host.doc;
        const win = host.win || root;
        const storage = () => { try { return host.storage(); } catch (_) { return null; } };
        let userHeight = null;
        const key = host.storageKey || STORAGE_KEY;
        const up = host.grow === 'up';
        try { userHeight = parseStored(storage() && storage().getItem(key)); } catch (_) {}

        const input = () => doc.getElementById(host.inputId || 'welcomeChatInput');
        const maxHeight = () => Math.max(AUTO_CAP, Math.round((win.innerHeight || 800) * 0.7));

        function fit() {
            const ta = input();
            if (!ta) return;
            ta.style.height = 'auto';
            const r = fitHeight({ scrollHeight: ta.scrollHeight, userHeight, max: maxHeight() });
            ta.style.height = r.height + 'px';
            ta.style.maxHeight = userHeight ? 'none' : '';
            ta.style.overflowY = r.overflow ? 'auto' : 'hidden';
        }

        function save() {
            const s = storage();
            if (!s) return;
            try {
                if (userHeight) s.setItem(key, String(userHeight));
                else s.removeItem(key);
            } catch (_) {}
        }

        function attach() {
            const ta = input();
            const wrap = ta && ta.closest(host.wrapSelector || '.input-wrapper');
            if (!wrap || wrap.querySelector('.composer-resize-grip')) return;
            const grip = doc.createElement('div');
            grip.className = 'composer-resize-grip' + (up ? ' composer-resize-grip--up' : '');
            grip.title = 'Drag to resize · double-click to reset';
            grip.setAttribute('aria-hidden', 'true');
            wrap.classList.add('composer-resizable');
            wrap.appendChild(grip);

            let drag = null;
            grip.addEventListener('pointerdown', (ev) => {
                if (ev.button !== 0) return;
                ev.preventDefault();
                drag = { y: ev.clientY, h: ta.offsetHeight, id: ev.pointerId };
                try { grip.setPointerCapture(ev.pointerId); } catch (_) {}
                wrap.classList.add('composer-resizing');
            });
            grip.addEventListener('pointermove', (ev) => {
                if (!drag || ev.pointerId !== drag.id) return;
                userHeight = clampHeight(drag.h + (ev.clientY - drag.y) * (up ? -1 : 1), maxHeight());
                fit();
            });
            const end = (ev) => {
                if (!drag || ev.pointerId !== drag.id) return;
                drag = null;
                wrap.classList.remove('composer-resizing');
                save();
            };
            grip.addEventListener('pointerup', end);
            grip.addEventListener('pointercancel', end);
            grip.addEventListener('dblclick', () => { userHeight = null; save(); fit(); });
            fit();
        }

        if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', attach);
        else attach();

        return { fit, attach, userHeight: () => userHeight };
    }

    const api = { STORAGE_KEY, CHAT_STORAGE_KEY, AUTO_CAP, MIN_HEIGHT, clampHeight, fitHeight, parseStored, create };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatComposerResize = api;
})(typeof window !== 'undefined' ? window : globalThis);
