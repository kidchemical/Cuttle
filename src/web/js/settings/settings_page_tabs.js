/* Settings page — category tab shell.
 *
 * Owner for tab behavior on src/web/settings_page.html. Pure view-layer: it owns
 * which panel is visible, URL/localStorage state, and per-tab lazy loading. It
 * does not own any setting's storage — each panel's loader stays in the page.
 *
 * Pattern matches src/web/jobs_page.html (data-tab buttons + #panel-<id>
 * sections, ?tab= URL param, localStorage memory) so the two pages behave alike.
 *
 * Tab ids are read from the markup (.settings-tab[data-tab]) rather than a
 * hardcoded list, so adding a tab is a markup edit.
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'cuttleSettingsTab';
    const TAB_SELECTOR = '.settings-tab[data-tab]';
    const PANEL_PREFIX = 'panel-';

    let current = null;
    let initialized = false;
    // tab id -> [fn] run once, on first activation.
    const loaders = Object.create(null);
    const loaded = Object.create(null);

    function tabIds() {
        return Array.from(document.querySelectorAll(TAB_SELECTOR))
            .map(function (b) { return String(b.getAttribute('data-tab') || '').trim(); })
            .filter(Boolean);
    }

    function panelFor(id) {
        return document.getElementById(PANEL_PREFIX + id);
    }

    function normalize(id) {
        const want = String(id == null ? '' : id).trim().toLowerCase();
        return tabIds().indexOf(want) === -1 ? null : want;
    }

    function readStored() {
        try { return localStorage.getItem(STORAGE_KEY); } catch (_) { return null; }
    }

    function writeStored(id) {
        try { localStorage.setItem(STORAGE_KEY, id); } catch (_) {}
    }

    /** Deep link: ?tab=<id> wins, then the last tab used here, then the first tab. */
    function readInitialTab() {
        let fromUrl = null;
        try {
            fromUrl = new URLSearchParams(window.location.search).get('tab');
        } catch (_) {}
        return normalize(fromUrl) || normalize(readStored()) || tabIds()[0] || null;
    }

    function syncUrl(id) {
        if (!window.history || typeof window.history.replaceState !== 'function') return;
        try {
            const url = new URL(window.location.href);
            if (id) url.searchParams.set('tab', id);
            else url.searchParams.delete('tab');
            window.history.replaceState(null, '', url.pathname + url.search + url.hash);
        } catch (_) {}
    }

    function runLoaders(id) {
        if (loaded[id]) return;
        loaded[id] = true;
        const fns = loaders[id] || [];
        fns.forEach(function (fn) {
            try { fn(); } catch (e) { console.error('[settings] tab loader failed for "' + id + '"', e); }
        });
    }

    function setTab(id, opts) {
        const options = opts || {};
        const next = normalize(id) || tabIds()[0] || null;
        if (!next) return null;
        current = next;

        document.querySelectorAll(TAB_SELECTOR).forEach(function (btn) {
            const isOn = btn.getAttribute('data-tab') === next;
            btn.classList.toggle('active', isOn);
            btn.setAttribute('aria-selected', isOn ? 'true' : 'false');
            btn.tabIndex = isOn ? 0 : -1;
        });
        tabIds().forEach(function (tid) {
            const panel = panelFor(tid);
            if (!panel) return;
            const isOn = tid === next;
            panel.classList.toggle('active', isOn);
            panel.hidden = !isOn;
        });

        if (options.persist !== false) {
            writeStored(next);
            if (options.syncUrl !== false) syncUrl(next);
        }
        // Panels stay in the DOM (hidden), so loaders are safe to run on
        // activation rather than at page load — that is what keeps a phone from
        // fetching API keys, LAN status, and TTS prefs it will never look at.
        runLoaders(next);

        if (typeof options.onChange === 'function') options.onChange(next);
        return next;
    }

    function currentTab() {
        return current;
    }

    /** Register a loader that runs the first time its tab is shown. */
    function register(id, fn) {
        const key = normalize(id);
        if (!key || typeof fn !== 'function') return;
        (loaders[key] || (loaders[key] = [])).push(fn);
    }

    /** Re-run a tab's loaders (e.g. after the user saves something on it). */
    function refresh(id) {
        const key = normalize(id) || current;
        if (!key) return;
        loaded[key] = false;
        runLoaders(key);
    }

    function focusTab(id) {
        const btn = document.querySelector(TAB_SELECTOR + '[data-tab="' + id + '"]');
        if (btn && typeof btn.focus === 'function') btn.focus();
        return btn;
    }

    function move(delta) {
        const ids = tabIds();
        if (!ids.length) return;
        const at = ids.indexOf(current);
        moveTo(ids[(at + delta + ids.length) % ids.length]);
    }

    function moveTo(id) {
        setTab(id);
        focusTab(id);
    }

    function onKeydown(event) {
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
            event.preventDefault(); move(1);
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
            event.preventDefault(); move(-1);
        } else if (event.key === 'Home') {
            event.preventDefault(); moveTo(tabIds()[0]);
        } else if (event.key === 'End') {
            const ids = tabIds();
            event.preventDefault(); moveTo(ids[ids.length - 1]);
        }
    }

    function init() {
        if (initialized) return;
        const strip = document.querySelector('.settings-tabs');
        if (!strip) return;
        initialized = true;

        strip.addEventListener('click', function (event) {
            const btn = event.target.closest(TAB_SELECTOR);
            if (btn && strip.contains(btn)) setTab(btn.getAttribute('data-tab'));
        });
        strip.addEventListener('keydown', onKeydown);

        // Tabs are requested while hidden tabs are still loading; run the
        // restored tab's loaders once its panel is actually displayed.
        const initial = readInitialTab();
        setTab(initial, { persist: false, syncUrl: false });
        writeStored(initial);
        syncUrl(initial);
    }

    window.CuttleSettingsTabs = {
        init: init,
        setTab: setTab,
        currentTab: currentTab,
        register: register,
        refresh: refresh,
        tabIds: tabIds,
    };
})();
