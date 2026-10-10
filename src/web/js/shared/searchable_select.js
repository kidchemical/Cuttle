/* ================================================================
   Cuttle — shared searchable select (searchable_select.js).
   Owner: turning any native <select> into a searchable, lazily-paged
   combobox. The native select stays the source of truth for value and
   `change`, so existing handlers, programmatic value sets, and tests
   keep working; this module only renders a trigger, a search field, and
   an infinite-scroll option list over it.

   Usage (the select must already be in the DOM):
       const c = CuttleSearchableSelect.enhance(selectEl, {
           className: 'model',          // extra classes for the wrapper
           label: 'Model',              // aria-label / search label
           pageSize: 60,                // rows per page (infinite scroll)
           searchPlaceholder: 'Search…',
           focusSearch: 'auto',         // true | false | 'auto' (desktop only)
       });
   Controller: { element, trigger, searchInput, sync, open, close,
                 isOpen, destroy }.

   Any component that repopulates the select's <option>s (or toggles
   `disabled` / `hidden`) is picked up automatically via MutationObserver.
   ================================================================ */
(function (root) {
    'use strict';

    const DEFAULT_PAGE_SIZE = 60;
    const OPEN_SELECTOR = '.cuttle-combo.is-open';

    function shouldAutoFocusSearch(focusSearch) {
        if (focusSearch === true) return true;
        if (focusSearch === false) return false;
        try {
            return typeof root.matchMedia === 'function'
                && root.matchMedia('(hover: hover) and (pointer: fine)').matches;
        } catch (_) {
            return false;
        }
    }

    function closeOpenCombos(except) {
        if (typeof root.document === 'undefined') return;
        root.document.querySelectorAll(OPEN_SELECTOR).forEach((combo) => {
            if (combo !== except && typeof combo.__cuttleClose === 'function') combo.__cuttleClose();
        });
    }

    function enhance(select, options) {
        if (!select || !select.ownerDocument) return null;
        if (select.__cuttleSearchable) return select.__cuttleSearchable;
        const doc = select.ownerDocument;
        const opts = options || {};
        const pageSize = Number(opts.pageSize) > 0 ? Number(opts.pageSize) : DEFAULT_PAGE_SIZE;

        const wrap = doc.createElement('div');
        wrap.className = 'cuttle-combo';
        if (opts.className) {
            String(opts.className).split(/\s+/).forEach((c) => { if (c) wrap.classList.add(c); });
        }
        if (select.id) wrap.dataset.for = select.id;

        const selectLabel = (select.getAttribute && select.getAttribute('aria-label')) || '';
        const label = opts.label || selectLabel || '';

        const trigger = doc.createElement('button');
        trigger.type = 'button';
        trigger.className = 'cuttle-combo-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        // The native select keeps its own accessible name (callers/tests
        // address it by label). Only name the trigger when the select is
        // unlabeled, so the accessible name is not duplicated.
        if (label && !selectLabel) trigger.setAttribute('aria-label', label);
        else if (label) trigger.title = label;
        const valueEl = doc.createElement('span');
        valueEl.className = 'cuttle-combo-value';
        const caret = doc.createElement('span');
        caret.className = 'cuttle-combo-caret';
        caret.setAttribute('aria-hidden', 'true');
        trigger.appendChild(valueEl);
        trigger.appendChild(caret);

        const panel = doc.createElement('div');
        panel.className = 'cuttle-combo-panel';
        panel.hidden = true;
        const search = doc.createElement('input');
        search.type = 'text';
        search.className = 'cuttle-combo-search';
        search.placeholder = opts.searchPlaceholder || 'Search…';
        search.autocomplete = 'off';
        search.spellcheck = false;
        if (label) search.setAttribute('aria-label', 'Search ' + label);
        const list = doc.createElement('ul');
        list.className = 'cuttle-combo-list';
        list.setAttribute('role', 'listbox');
        panel.appendChild(search);
        panel.appendChild(list);

        if (select.parentNode) select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(trigger);
        wrap.appendChild(panel);
        wrap.appendChild(select);

        let filtered = [];
        let rendered = 0;
        let observer = null;

        function selectedLabel() {
            const opt = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
            return opt ? opt.textContent : '';
        }

        function effectivelyEmpty() {
            return !select.options.length
                || (select.options.length === 1 && select.options[0].disabled && !select.options[0].value);
        }

        function sync() {
            valueEl.textContent = selectedLabel() || 'No options loaded';
            trigger.disabled = !!select.disabled || effectivelyEmpty();
            wrap.classList.toggle('is-disabled', trigger.disabled);
            wrap.hidden = !!select.hidden;
            if (trigger.disabled) close();
        }

        function filterOptions() {
            const query = String(search.value || '').trim().toLowerCase();
            filtered = [];
            Array.prototype.forEach.call(select.options, (opt) => {
                // Skip the "No options loaded" placeholder; the empty state
                // below carries that message.
                if (opt.disabled && !opt.value) return;
                const text = opt.textContent || '';
                if (query
                    && text.toLowerCase().indexOf(query) === -1
                    && String(opt.value).toLowerCase().indexOf(query) === -1) return;
                filtered.push(opt);
            });
        }

        function render(reset) {
            if (reset) {
                list.innerHTML = '';
                list.scrollTop = 0;
                rendered = 0;
            }
            if (!filtered.length) {
                const empty = doc.createElement('li');
                empty.className = 'cuttle-combo-empty';
                empty.textContent = select.options.length ? 'No matches' : 'No options loaded';
                list.appendChild(empty);
                rendered = 0;
                return;
            }
            const start = rendered;
            const end = Math.min(start + pageSize, filtered.length);
            const fragment = doc.createDocumentFragment();
            for (let i = start; i < end; i++) {
                const opt = filtered[i];
                const li = doc.createElement('li');
                li.className = 'cuttle-combo-option';
                li.setAttribute('role', 'option');
                li.dataset.value = opt.value;
                li.textContent = opt.textContent || '';
                if (opt.value === select.value) li.classList.add('is-selected');
                if (opt.disabled) li.classList.add('is-disabled');
                li.addEventListener('click', () => choose(opt.value));
                fragment.appendChild(li);
            }
            list.appendChild(fragment);
            rendered = end;
        }

        function loadMore() {
            if (rendered >= filtered.length) return;
            render(false);
        }

        function rebuild() {
            filterOptions();
            render(true);
        }

        function choose(value) {
            if (select.value !== value) {
                select.value = value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            sync();
            close();
        }

        function close() {
            wrap.classList.remove('is-open');
            panel.hidden = true;
            trigger.setAttribute('aria-expanded', 'false');
        }

        function open() {
            if (wrap.classList.contains('is-disabled')) return;
            closeOpenCombos(wrap);
            wrap.classList.add('is-open');
            panel.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');
            search.value = '';
            if (shouldAutoFocusSearch(opts.focusSearch)) {
                try { search.focus({ preventScroll: true }); } catch (_) { search.focus(); }
            }
            rebuild();
        }

        function activeOptions() {
            return Array.prototype.slice.call(
                list.querySelectorAll('.cuttle-combo-option:not(.is-disabled)'));
        }

        function moveActive(delta) {
            const options = activeOptions();
            if (!options.length) return;
            let idx = options.findIndex((o) => o.classList.contains('is-active'));
            if (idx === -1) idx = delta > 0 ? -1 : 0;
            idx = (idx + delta + options.length) % options.length;
            options.forEach((o) => o.classList.remove('is-active'));
            options[idx].classList.add('is-active');
            try { options[idx].scrollIntoView({ block: 'nearest' }); } catch (_) {}
        }

        trigger.addEventListener('click', (e) => {
            e.preventDefault();
            if (wrap.classList.contains('is-open')) close();
            else open();
        });
        trigger.addEventListener('keydown', (e) => {
            // Enter/Space already activate the button (click); only
            // ArrowDown/Escape need explicit handling here.
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                open();
            } else if (e.key === 'Escape') {
                e.stopPropagation();
                close();
            }
        });
        search.addEventListener('input', rebuild);
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const options = activeOptions();
                const active = list.querySelector('.cuttle-combo-option.is-active');
                const pick = active || options[0];
                if (pick) choose(pick.dataset.value);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                close();
                try { trigger.focus(); } catch (_) {}
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const down = e.key === 'ArrowDown';
                const options = activeOptions();
                const last = options[options.length - 1];
                if (down && last && last.classList.contains('is-active')) {
                    loadMore();
                    moveActive(1);
                } else {
                    moveActive(down ? 1 : -1);
                }
            }
        });
        list.addEventListener('scroll', () => {
            if (list.scrollTop + list.clientHeight >= list.scrollHeight - 24) loadMore();
        });
        select.addEventListener('change', sync);

        // Programmatic `select.value = …` emits no `change`; wrap the value
        // setter so the trigger label follows any code that assigns it.
        try {
            const desc = Object.getOwnPropertyDescriptor(
                Object.getPrototypeOf(select), 'value');
            if (desc && typeof desc.get === 'function' && typeof desc.set === 'function') {
                Object.defineProperty(select, 'value', {
                    configurable: true,
                    enumerable: true,
                    get() { return desc.get.call(this); },
                    set(next) { desc.set.call(this, next); sync(); },
                });
            }
        } catch (_) { /* non-configurable: fall back to change/MutationObserver */ }

        if (typeof root.MutationObserver === 'function') {
            observer = new root.MutationObserver(() => {
                sync();
                if (wrap.classList.contains('is-open')) rebuild();
            });
            observer.observe(select, {
                childList: true,
                attributes: true,
                attributeFilter: ['disabled', 'hidden'],
            });
        }

        const controller = {
            element: wrap,
            trigger,
            searchInput: search,
            sync,
            open,
            close,
            isOpen: () => wrap.classList.contains('is-open'),
            destroy() {
                if (observer) observer.disconnect();
                if (wrap.parentNode) {
                    wrap.parentNode.insertBefore(select, wrap);
                    wrap.parentNode.removeChild(wrap);
                }
                delete select.__cuttleSearchable;
            },
        };
        wrap.__cuttleClose = close;
        select.__cuttleSearchable = controller;
        sync();
        return controller;
    }

    // One shared document handler: Escape closes the open combobox (and does
    // not reach a page-level Escape handler, e.g. the chat config modal);
    // a pointerdown outside any combobox closes it too.
    if (typeof root.document !== 'undefined') {
        root.document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            const open = root.document.querySelector(OPEN_SELECTOR);
            if (!open) return;
            if (typeof open.__cuttleClose === 'function') open.__cuttleClose();
            e.stopImmediatePropagation();
        });
        root.document.addEventListener('pointerdown', (e) => {
            const target = e.target;
            if (target && target.closest && target.closest('.cuttle-combo')) return;
            closeOpenCombos(null);
        });
    }

    const api = { enhance, closeAll: () => closeOpenCombos(null) };
    const ns = (root.CuttleSearchableSelect = root.CuttleSearchableSelect || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
