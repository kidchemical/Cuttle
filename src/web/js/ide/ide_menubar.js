/* Compact, keyboard-accessible menus over the IDE's existing command registry. */
(function (root) {
    'use strict';
    const MENUS = {
        File: ['file.new', 'file.newFolder', null, 'file.save', 'file.saveAll',
            null, 'file.rename', 'file.revert', 'file.close'],
        Edit: ['editor.undo', 'editor.redo', null, 'editor.selectAll', 'editor.find',
            'editor.replace', null, 'editor.comment', 'editor.format'],
        View: ['view.explorer', 'view.search', 'view.scm', null, 'view.sidebar',
            'view.panel', 'view.chat', null, 'editor.wrap', 'editor.minimap'],
        Go: ['file.quickOpen', 'editor.gotoLine', 'editor.symbol', null, 'tabs.next', 'tabs.prev'],
        Terminal: ['terminal.new', 'terminal.runFile', null, 'terminal.clear', 'terminal.kill'],
        AI: ['view.chat', 'ai.addToChat', 'ai.inlineEdit', 'ai.completions'],
    };
    function create(host) {
        const { win, el, getCommand, runCommand } = host;
        const doc = win.document;
        let active = null;
        let restoreFocus = null;
        const buttons = [];
        const popup = doc.createElement('div');
        popup.className = 'ide-menubar-popup ide-menu';
        popup.setAttribute('role', 'menu');
        popup.id = 'ideMenubarPopup';
        popup.hidden = true;
        doc.body.appendChild(popup);
        el.setAttribute('role', 'menubar');
        el.setAttribute('aria-label', 'Editor commands');
        function close(focus) {
            if (active) active.setAttribute('aria-expanded', 'false');
            active = null;
            popup.hidden = true;
            if (focus && restoreFocus?.isConnected) restoreFocus.focus();
        }
        const items = () => Array.from(popup.querySelectorAll('button:not(:disabled)'));
        function open(button, last) {
            if (!active) restoreFocus = doc.activeElement;
            close(false);
            active = button;
            button.setAttribute('aria-expanded', 'true');
            popup.replaceChildren();
            for (const id of MENUS[button.textContent]) {
                if (!id) { popup.appendChild(doc.createElement('hr')); continue; }
                const c = getCommand(id);
                if (!c) continue;
                const item = doc.createElement('button');
                item.type = 'button'; item.setAttribute('role', 'menuitem');
                item.textContent = c.label.replace(/^(File|View|Search|Editor|Git|AI|Terminal|Run): /, '');
                if (c.keys) { const key = doc.createElement('kbd'); key.textContent = c.keys; item.appendChild(key); }
                item.disabled = !host.hasFile() && /^(editor\.|file\.(save$|rename|revert|close)|ai\.(add|inline)|terminal.runFile)/.test(id);
                item.addEventListener('click', () => { close(true); runCommand(id); });
                popup.appendChild(item);
            }
            popup.setAttribute('aria-label', button.textContent);
            popup.hidden = false;
            const rect = button.getBoundingClientRect();
            popup.style.left = Math.max(6, Math.min(rect.left, win.innerWidth - popup.offsetWidth - 6)) + 'px';
            popup.style.top = Math.min(rect.bottom + 4, win.innerHeight - popup.offsetHeight - 6) + 'px';
            const available = items();
            (last ? available[available.length - 1] : available[0])?.focus();
        }
        for (const label of Object.keys(MENUS)) {
            const button = doc.createElement('button');
            button.type = 'button'; button.textContent = label;
            button.tabIndex = buttons.length ? -1 : 0;
            button.addEventListener('focus', () => buttons.forEach(b => { b.tabIndex = b === button ? 0 : -1; }));
            button.setAttribute('role', 'menuitem'); button.setAttribute('aria-haspopup', 'menu');
            button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', popup.id);
            button.addEventListener('click', () => active === button ? close(true) : open(button));
            button.addEventListener('pointerenter', () => { if (active && active !== button) open(button); });
            buttons.push(button); el.appendChild(button);
        }
        const save = doc.createElement('button');
        save.type = 'button'; save.className = 'ide-menu-save'; save.dataset.cmd = 'file.save';
        save.setAttribute('role', 'menuitem');
        save.textContent = 'Save'; save.title = 'Save file (Ctrl+S)';
        el.appendChild(save);
        doc.addEventListener('pointerdown', e => { if (!el.contains(e.target) && !popup.contains(e.target)) close(false); });
        doc.addEventListener('focusin', e => { if (active && !el.contains(e.target) && !popup.contains(e.target)) close(false); });
        win.addEventListener('resize', () => close(false));
        win.addEventListener('keydown', e => {
            if (active && e.key === 'Escape') {
                const button = active;
                e.preventDefault(); e.stopImmediatePropagation(); close(false); button.focus();
            }
        }, true);
        const keydown = e => {
            const button = buttons.includes(e.target) ? e.target : active;
            if (!button) return;
            const list = items();
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); button.focus(); }
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault(); const next = buttons[(buttons.indexOf(button) + (e.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length];
                if (active) open(next); else next.focus();
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (!active) open(button, e.key === 'ArrowUp');
                else list[(list.indexOf(doc.activeElement) + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length]?.focus();
            } else if (e.key === 'Home' || e.key === 'End') {
                e.preventDefault(); (e.key === 'Home' ? list[0] : list[list.length - 1])?.focus();
            } else if (e.key === 'Tab') close(false);
        };
        el.addEventListener('keydown', keydown);
        popup.addEventListener('keydown', keydown);
        return { refresh() { save.disabled = !host.hasFile(); save.textContent = host.isDirty() ? 'Save •' : 'Save'; } };
    }
    root.CuttleIdeMenubar = { create: create };
})(globalThis);
