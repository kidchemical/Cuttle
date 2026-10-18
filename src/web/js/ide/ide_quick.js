/* IDE App — quick input (VS Code "QuickPick"): one floating box for quick
 * open, the command palette, pickers and name prompts. Callers supply the
 * items for each query; this controller owns only the box, keyboard and
 * selection. */
(function (root) {
    'use strict';

    function create(host) {
        const el = host.el;
        const esc = host.escapeHtml;
        el.innerHTML = '<div class="ide-quick-box" role="dialog" aria-label="Quick input">'
            + '<input class="ide-quick-input" type="text" spellcheck="false" autocomplete="off" aria-autocomplete="list" aria-controls="ideQuickList">'
            + '<div class="ide-quick-hint" hidden></div>'
            + '<ul class="ide-quick-list" id="ideQuickList" role="listbox"></ul></div>';
        const input = el.querySelector('input');
        const list = el.querySelector('ul');
        const hint = el.querySelector('.ide-quick-hint');
        let session = null; // {provider, onAccept, items, index, seq, resolve, prompt}

        function close(result) {
            if (!session) return;
            const s = session;
            session = null;
            el.hidden = true;
            list.innerHTML = '';
            if (s.restoreFocus && s.restoreFocus.isConnected) try { s.restoreFocus.focus(); } catch (_) {}
            s.resolve(result == null ? null : result);
        }
        function renderList() {
            if (!session) return;
            const items = session.items || [];
            list.innerHTML = items.map((item, i) => (
                '<li role="option" class="ide-quick-item' + (i === session.index ? ' is-active' : '') + (item.separator ? ' has-separator' : '') + '" data-index="' + i + '" aria-selected="' + (i === session.index) + '">'
                + (item.icon ? '<span class="ide-quick-icon">' + esc(item.icon) + '</span>' : '')
                + '<span class="ide-quick-label">' + (item.labelHtml || esc(item.label)) + '</span>'
                + (item.description ? '<span class="ide-quick-desc">' + esc(item.description) + '</span>' : '')
                + (item.keys ? '<kbd class="ide-quick-keys">' + esc(item.keys) + '</kbd>' : '')
                + '</li>'
            )).join('') || (session.prompt ? '' : '<li class="ide-quick-empty">' + esc(session.emptyText || 'No matching results') + '</li>');
            const active = list.querySelector('.is-active');
            if (active) active.scrollIntoView({ block: 'nearest' });
        }
        async function update() {
            if (!session || !session.provider) return;
            const s = session;
            const seq = ++s.seq;
            const query = input.value;
            let items;
            try { items = await s.provider(query); } catch (err) { items = []; s.emptyText = (err && err.message) || 'Failed'; }
            if (session !== s || seq !== s.seq) return;
            s.items = items || [];
            s.index = s.items.length ? 0 : -1;
            if (s.items.length && s.items[0].separator && s.items[0].skip) s.index = 1;
            renderList();
        }
        function accept(index) {
            if (!session) return;
            const s = session;
            if (s.prompt) {
                const value = input.value.trim();
                const problem = s.validate ? s.validate(value) : '';
                if (problem) { hint.hidden = false; hint.textContent = problem; return; }
                close(value || null);
                return;
            }
            const item = s.items[index == null ? s.index : index];
            if (!item) return;
            close(item);
        }
        input.addEventListener('input', () => {
            if (!session) return;
            if (session.prompt) {
                const problem = session.validate ? session.validate(input.value.trim()) : '';
                hint.hidden = !problem;
                hint.textContent = problem || '';
                return;
            }
            update();
        });
        input.addEventListener('keydown', (e) => {
            if (!session) return;
            const n = (session.items || []).length;
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(null); }
            else if (e.key === 'Enter') { e.preventDefault(); accept(); }
            else if ((e.key === 'ArrowDown' || (e.key === 'n' && e.ctrlKey)) && n) {
                e.preventDefault(); session.index = (session.index + 1) % n; renderList();
            } else if ((e.key === 'ArrowUp' || (e.key === 'p' && e.ctrlKey)) && n) {
                e.preventDefault(); session.index = (session.index - 1 + n) % n; renderList();
            } else if (e.key === 'PageDown' && n) { e.preventDefault(); session.index = Math.min(n - 1, session.index + 10); renderList(); }
            else if (e.key === 'PageUp' && n) { e.preventDefault(); session.index = Math.max(0, session.index - 10); renderList(); }
        });
        list.addEventListener('mousedown', (e) => {
            const li = e.target.closest('[data-index]');
            if (!li) return;
            e.preventDefault();
            accept(Number(li.dataset.index));
        });
        el.addEventListener('mousedown', (e) => { if (e.target === el) { e.preventDefault(); close(null); } });
        input.addEventListener('blur', () => setTimeout(() => {
            if (session && document.activeElement !== input) close(null);
        }, 120));

        function begin(opts, extra) {
            if (session) close(null);
            return new Promise((resolve) => {
                session = Object.assign({ items: [], index: -1, seq: 0, resolve: resolve, restoreFocus: document.activeElement }, extra);
                el.hidden = false;
                input.placeholder = opts.placeholder || '';
                input.value = opts.value || '';
                hint.hidden = !opts.hint;
                hint.textContent = opts.hint || '';
                input.focus();
                // Mode prefixes ('>', ':') stay put; typing continues after them.
                const end = input.value.length;
                if (opts.selectRange) input.setSelectionRange(opts.selectRange[0], opts.selectRange[1]);
                else input.setSelectionRange(end, end);
                if (!extra.prompt) update();
                else renderList();
            });
        }
        return {
            /** Resolve to the accepted item, or null. `provider(query)` returns items. */
            pick: (opts) => begin(opts, { provider: opts.provider, emptyText: opts.emptyText }),
            /** Resolve to the entered text, or null. */
            prompt: (opts) => begin(opts, { prompt: true, validate: opts.validate }),
            /** Swap the text while open (prefix mode switches). */
            setValue(value) { if (session) { input.value = value; update(); } },
            isOpen: () => !!session,
            close: () => close(null),
        };
    }

    root.CuttleIdeQuick = { create: create };
})(globalThis);
