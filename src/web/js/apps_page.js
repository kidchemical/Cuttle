/**
 * Apps launcher — grid of Cuttle web apps. The shell owns the app list (rail +
 * stash buttons) and the blade-bar layout; this page asks for it over
 * postMessage and sends pin/unpin + navigate requests back.
 */
(function () {
    'use strict';

    const HOLD_MS = 500;
    const HOLD_MOVE_PX = 8;
    const APP_HUES = {
        'nav-chat': 212,
        'nav-editor': 268,
        'nav-tasks': 145,
        'nav-git': 22,
        'nav-jobs': 186,
        'nav-dashboards': 236,
    };
    const PIN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

    const grid = document.getElementById('appsGrid');
    const empty = document.getElementById('appsEmpty');
    const search = document.getElementById('appsSearch');
    const summary = document.getElementById('appsSummary');
    const menu = document.getElementById('appsMenu');
    const status = document.getElementById('appsStatus');
    const inShell = window.parent && window.parent !== window;

    let apps = [];
    let received = false;
    let statusTimer = null;

    function hueFor(id) {
        if (APP_HUES[id] != null) return APP_HUES[id];
        let h = 0;
        for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 360;
        return h;
    }

    function post(msg) {
        if (inShell) window.parent.postMessage(msg, '*');
    }

    function showStatus(text) {
        status.textContent = text;
        status.classList.add('show');
        clearTimeout(statusTimer);
        statusTimer = setTimeout(() => status.classList.remove('show'), 1800);
    }

    function openApp(app) {
        closeMenu();
        if (inShell) post({ type: 'cuttle-navigate', page: app.page });
        else window.location.href = app.page;
    }

    function setPinned(app, pinned) {
        closeMenu();
        post({ type: 'cuttle-apps-pin', id: app.id, pinned });
        showStatus(pinned ? `${app.label} added to the blade bar` : `${app.label} removed from the blade bar`);
    }

    function closeMenu() {
        menu.hidden = true;
        menu.innerHTML = '';
    }

    function openMenu(app, x, y) {
        menu.innerHTML = '';
        const title = document.createElement('div');
        title.className = 'apps-menu-title';
        title.textContent = app.label;
        menu.appendChild(title);

        const addItem = (label, fn) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.setAttribute('role', 'menuitem');
            b.textContent = label;
            b.addEventListener('click', fn);
            menu.appendChild(b);
            return b;
        };
        const first = addItem('Open', () => openApp(app));
        if (inShell) {
            addItem(app.pinned ? 'Remove from blade bar' : 'Add to blade bar', () => setPinned(app, !app.pinned));
        }

        menu.hidden = false;
        const pad = 8;
        const w = menu.offsetWidth;
        const h = menu.offsetHeight;
        const left = Math.min(Math.max(pad, x), window.innerWidth - w - pad);
        const top = Math.min(Math.max(pad, y), window.innerHeight - h - pad);
        menu.style.left = `${Math.round(left)}px`;
        menu.style.top = `${Math.round(top)}px`;
        first.focus({ preventScroll: true });
    }

    function buildTile(app) {
        const tile = document.createElement('button');
        tile.type = 'button';
        tile.className = 'apps-tile';
        tile.setAttribute('role', 'listitem');
        tile.dataset.id = app.id;
        tile.title = app.pinned ? `${app.label} · on blade bar` : app.label;

        const icon = document.createElement('span');
        icon.className = 'apps-icon';
        icon.style.setProperty('--app-hue', String(hueFor(app.id)));
        icon.innerHTML = app.icon || '';
        if (app.pinned) {
            const pin = document.createElement('span');
            pin.className = 'apps-pin';
            pin.setAttribute('aria-label', 'On blade bar');
            pin.innerHTML = PIN_SVG;
            icon.appendChild(pin);
        }
        const label = document.createElement('span');
        label.className = 'apps-label';
        label.textContent = app.label;
        tile.append(icon, label);

        let holdTimer = null;
        let holdStart = null;
        // The click that follows a long-press (or a touch contextmenu) must not also open the app.
        let suppressClickUntil = 0;
        const clearHold = () => {
            if (holdTimer) clearTimeout(holdTimer);
            holdTimer = null;
            holdStart = null;
            tile.classList.remove('holding');
        };

        tile.addEventListener('pointerdown', (e) => {
            if (e.button != null && e.button !== 0) return;
            holdStart = { x: e.clientX, y: e.clientY };
            tile.classList.add('holding');
            holdTimer = setTimeout(() => {
                const r = tile.getBoundingClientRect();
                clearHold();
                suppressClickUntil = Date.now() + 1500;
                openMenu(app, r.left + r.width / 2, r.bottom - 6);
            }, HOLD_MS);
        });
        tile.addEventListener('pointermove', (e) => {
            if (!holdStart) return;
            const dx = e.clientX - holdStart.x;
            const dy = e.clientY - holdStart.y;
            if (dx * dx + dy * dy > HOLD_MOVE_PX * HOLD_MOVE_PX) clearHold();
        });
        tile.addEventListener('pointerup', clearHold);
        tile.addEventListener('pointercancel', clearHold);
        tile.addEventListener('pointerleave', clearHold);
        tile.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            clearHold();
            if (!menu.hidden) return;
            suppressClickUntil = Date.now() + 1500;
            if (e.clientX || e.clientY) {
                openMenu(app, e.clientX, e.clientY);
            } else {
                const r = tile.getBoundingClientRect();
                openMenu(app, r.left + r.width / 2, r.bottom - 6);
            }
        });
        tile.addEventListener('click', (e) => {
            if (Date.now() < suppressClickUntil) {
                e.preventDefault();
                suppressClickUntil = 0;
                return;
            }
            openApp(app);
        });
        return tile;
    }

    function render() {
        const q = (search.value || '').trim().toLowerCase();
        const visible = q ? apps.filter(a => a.label.toLowerCase().includes(q)) : apps;
        grid.replaceChildren(...visible.map(buildTile));

        const pinned = apps.filter(a => a.pinned).length;
        summary.textContent = received
            ? `${apps.length} Cuttle web apps · ${pinned} on the blade bar`
            : 'Cuttle web apps';

        if (!received) {
            empty.hidden = false;
            empty.textContent = inShell
                ? 'Loading apps…'
                : 'Open Apps from the Cuttle shell to launch apps and manage the blade bar.';
        } else if (!visible.length) {
            empty.hidden = false;
            empty.textContent = q ? `No apps match “${search.value.trim()}”.` : 'No apps available.';
        } else {
            empty.hidden = true;
        }
    }

    window.addEventListener('message', (e) => {
        if (!inShell || e.source !== window.parent) return;
        const d = e.data;
        if (!d || d.type !== 'cuttle-apps' || !Array.isArray(d.apps)) return;
        apps = d.apps.filter(a => a && typeof a.id === 'string' && typeof a.page === 'string');
        received = true;
        render();
    });

    search.addEventListener('input', render);
    search.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        const q = search.value.trim().toLowerCase();
        const match = q && apps.find(a => a.label.toLowerCase().includes(q));
        if (match) openApp(match);
    });
    document.addEventListener('pointerdown', (e) => {
        if (!menu.hidden && !menu.contains(e.target)) closeMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
    });
    window.addEventListener('scroll', closeMenu, { passive: true });
    window.addEventListener('blur', closeMenu);

    render();
    post({ type: 'cuttle-apps-request' });
})();
