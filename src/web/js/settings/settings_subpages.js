/* Settings page — click-in subpages and breadcrumbs.
 *
 * Owner for drill-in navigation inside a Settings tab. A tab panel marks
 * secondary groups with `data-subpage="<id>"` (+ `data-subpage-title`,
 * `-desc`, `-icon`); this module collapses them behind a list of link rows
 * (icon, title, one-line description, chevron) and opens one at a time with a
 * `Settings › Tab › Subpage` breadcrumb. `?page=<id>` deep-links a subpage.
 *
 * Pure view layer: it owns no setting's storage and never runs loaders —
 * subpages live inside their tab's panel, so the tab's lazy loaders
 * (settings_page_tabs.js) have already filled them. Without this script the
 * groups simply render inline, as before.
 *
 * Talks to the tab shell only through `cuttle:settings-tab` /
 * `cuttle:settings-index` events and the public `CuttleSettingsTabs` API.
 */
(function () {
    'use strict';

    const SVG_ATTRS = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
        + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
    // Line icons (Lucide geometry, ISC). Unknown names fall back to `dot`.
    const ICONS = {
        git: '<line x1="6" x2="6" y1="3" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
        timer: '<line x1="10" x2="14" y1="2" y2="2"/><line x1="12" x2="15" y1="14" y2="11"/><circle cx="12" cy="14" r="8"/>',
        'folder-plus': '<path d="M12 10v6"/><path d="M9 13h6"/><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        compass: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-1.8 5.41a2 2 0 0 1-1.27 1.27L7.76 16.24l1.8-5.41a2 2 0 0 1 1.27-1.27z"/>',
        image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
        tag: '<path d="M12.59 2.59A2 2 0 0 0 11.17 2H4a2 2 0 0 0-2 2v7.17a2 2 0 0 0 .59 1.42l8.7 8.7a2.43 2.43 0 0 0 3.42 0l6.58-6.58a2.43 2.43 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r="1"/>',
        type: '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/>',
        message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 8h8"/><path d="M8 12h5"/>',
        dot: '<circle cx="12" cy="12" r="3"/>',
    };
    const PAGE_PARAM = 'page';

    let crumbs = null;
    let currentTab = null;
    let openId = null;
    let initialized = false;
    let pendingDeepLink = null;

    function icon(name) {
        return '<svg class="settings-icon" ' + SVG_ATTRS + '>' + (ICONS[name] || ICONS.dot) + '</svg>';
    }

    function tabsApi() {
        return window.CuttleSettingsTabs || null;
    }

    function panelFor(tab) {
        return document.getElementById('panel-' + tab);
    }

    function subpagesIn(panel) {
        return panel ? Array.from(panel.querySelectorAll(':scope > [data-subpage]')) : [];
    }

    function findSubpage(tab, id) {
        return subpagesIn(panelFor(tab)).find(function (g) { return g.dataset.subpage === id; }) || null;
    }

    function readUrlPage() {
        try { return new URLSearchParams(window.location.search).get(PAGE_PARAM); } catch (_) { return null; }
    }

    function syncUrl(id) {
        if (!window.history || typeof window.history.replaceState !== 'function') return;
        try {
            const url = new URL(window.location.href);
            if (id) url.searchParams.set(PAGE_PARAM, id);
            else url.searchParams.delete(PAGE_PARAM);
            window.history.replaceState(null, '', url.pathname + url.search + url.hash);
        } catch (_) {}
    }

    function buildList(panel, groups) {
        const list = document.createElement('div');
        list.className = 'settings-link-list';
        list.setAttribute('data-subpage-list', '');
        groups.forEach(function (group) {
            const id = group.dataset.subpage;
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'settings-link-row';
            row.dataset.subpageTarget = id;
            row.setAttribute('aria-controls', group.id || '');
            row.innerHTML = '<span class="settings-link-icon">' + icon(group.dataset.subpageIcon) + '</span>'
                + '<span class="settings-link-text"><span class="settings-link-title"></span>'
                + '<span class="settings-link-desc"></span></span>'
                + '<span class="settings-link-chevron" aria-hidden="true"></span>';
            row.querySelector('.settings-link-title').textContent = group.dataset.subpageTitle || id;
            row.querySelector('.settings-link-desc').textContent = group.dataset.subpageDesc || '';
            if (!group.id) group.id = 'subpage-' + id;
            row.setAttribute('aria-controls', group.id);
            row.addEventListener('click', function () { open(panel.id.replace(/^panel-/, ''), id); });
            list.appendChild(row);
        });
        groups[0].parentNode.insertBefore(list, groups[0]);
        groups.forEach(function (g) {
            g.classList.add('settings-subpage');
            g.hidden = true;
        });
    }

    function crumbButton(label, onClick) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'settings-crumb';
        btn.textContent = label;
        btn.addEventListener('click', onClick);
        return btn;
    }

    function crumbSep() {
        const sep = document.createElement('span');
        sep.className = 'settings-crumb-sep';
        sep.setAttribute('aria-hidden', 'true');
        return sep;
    }

    function renderCrumbs() {
        if (!crumbs || !currentTab) return;
        const tabs = tabsApi();
        const tabLabel = tabs && tabs.tabLabel ? tabs.tabLabel(currentTab) : currentTab;
        crumbs.replaceChildren();
        crumbs.appendChild(crumbButton('Settings', function () {
            close();
            if (tabs && tabs.showIndex) tabs.showIndex();
        }));
        crumbs.appendChild(crumbSep());
        const group = openId ? findSubpage(currentTab, openId) : null;
        if (group) {
            crumbs.appendChild(crumbButton(tabLabel, function () { close(true); }));
            crumbs.appendChild(crumbSep());
            const here = document.createElement('span');
            here.className = 'settings-crumb is-current';
            here.setAttribute('aria-current', 'page');
            here.textContent = group.dataset.subpageTitle || openId;
            crumbs.appendChild(here);
        } else {
            const here = document.createElement('span');
            here.className = 'settings-crumb is-current';
            here.setAttribute('aria-current', 'page');
            here.textContent = tabLabel;
            crumbs.appendChild(here);
        }
        crumbs.hidden = false;
    }

    function applyState() {
        document.querySelectorAll('.settings-panel').forEach(function (panel) {
            const tab = panel.id.replace(/^panel-/, '');
            const active = tab === currentTab && openId ? openId : null;
            if (active) panel.setAttribute('data-subpage-open', active);
            else panel.removeAttribute('data-subpage-open');
            subpagesIn(panel).forEach(function (g) {
                const on = g.dataset.subpage === active;
                g.hidden = !on;
                g.classList.toggle('is-open', on);
            });
        });
        renderCrumbs();
    }

    function scrollTop() {
        if (typeof window.scrollTo === 'function') window.scrollTo({ top: 0, behavior: 'auto' });
    }

    /** Open subpage `id` of `tab` (switching tab first when needed). */
    function open(tab, id) {
        const tabs = tabsApi();
        if (tab && tab !== currentTab && tabs) tabs.setTab(tab);
        const group = findSubpage(tab || currentTab, id);
        if (!group) return false;
        openId = id;
        applyState();
        syncUrl(id);
        scrollTop();
        const title = group.querySelector('.settings-title');
        if (title) {
            title.setAttribute('tabindex', '-1');
            try { title.focus({ preventScroll: true }); } catch (_) {}
        }
        return true;
    }

    /** Back to the tab's root view; `restoreFocus` returns focus to the row. */
    function close(restoreFocus) {
        const was = openId;
        openId = null;
        applyState();
        syncUrl(null);
        if (restoreFocus && was) {
            const panel = panelFor(currentTab);
            const row = panel && panel.querySelector('[data-subpage-target="' + was + '"]');
            if (row) row.focus();
        }
    }

    function onTab(event) {
        const tab = event && event.detail && event.detail.tab;
        if (!tab) return;
        const changed = tab !== currentTab;
        currentTab = tab;
        if (pendingDeepLink && findSubpage(tab, pendingDeepLink)) {
            openId = pendingDeepLink;
        } else if (changed) {
            openId = null;
            if (initialized && readUrlPage()) syncUrl(null);
        }
        pendingDeepLink = null;
        applyState();
        if (changed && window.matchMedia && window.matchMedia('(max-width: 760px)').matches) scrollTop();
    }

    function init() {
        if (initialized) return;
        initialized = true;
        crumbs = document.querySelector('.settings-crumbs');
        document.querySelectorAll('.settings-panel').forEach(function (panel) {
            const groups = subpagesIn(panel);
            if (groups.length) buildList(panel, groups);
        });
        pendingDeepLink = readUrlPage();
        document.addEventListener('cuttle:settings-tab', onTab);
        // The tab shell may already have activated a tab before this ran.
        const tabs = tabsApi();
        const now = tabs && tabs.currentTab ? tabs.currentTab() : null;
        if (now) onTab({ detail: { tab: now } });
    }

    window.CuttleSettingsSubpages = {
        init: init,
        open: open,
        close: close,
        current: function () { return openId; },
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
