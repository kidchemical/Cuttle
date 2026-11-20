/**
 * Media App controller — transport + DOM over CuttleMediaLibraryModel (pure).
 *
 * Data owner: api.media_library (GET /api/media-library, read-only, the
 * caller's own chats). Thumbs, the lightbox slideshow and the image Copy /
 * Save menu are the chat's own (CuttleChatMedia); `galleryFor` makes a click
 * open the whole shown list instead of one bubble's thumbs.
 *
 * View prefs (filters, sort, group, tile size) persist in localStorage;
 * `?chat=<id>` opens the page filtered to one chat.
 */
(function () {
    'use strict';

    const M = window.CuttleMediaLibraryModel;
    const $ = (id) => document.getElementById(id);
    const PREFS_KEY = 'cuttle.mediaLibrary.prefs.v1';
    const PAGE_SIZE = 120;
    const FILL_MARGIN_PX = 600;

    const els = {
        toolbar: $('mediaToolbar'), notice: $('mediaNotice'), grid: $('mediaGrid'), more: $('mediaMore'),
        search: $('mediaSearch'), summary: $('mediaSummary'), clear: $('mediaClear'), refresh: $('mediaRefresh'),
        chat: $('mediaChat'), project: $('mediaProject'), range: $('mediaRange'), sort: $('mediaSort'),
    };

    const state = {
        items: [], chats: [], totals: null,
        prefs: loadPrefs(),
        shown: [],     // filtered + sorted items (render order)
        gallery: [],   // lightbox items for `shown`
        limit: PAGE_SIZE,
        rendered: 0,      // tiles in the DOM (prefix of `shown`)
        groupTotals: new Map(), // group key → items in the whole shown list
        loading: false,
    };

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function clamp(n, min, max) {
        const x = Number(n);
        return Number.isFinite(x) ? Math.min(max, Math.max(min, x)) : min;
    }

    // ── Shared chat media controller (thumbs + lightbox + Copy/Save) ──
    const teardown = new AbortController();
    window.addEventListener('pagehide', (e) => { if (!e.persisted) teardown.abort(); });
    const media = window.CuttleChatMedia.create({
        win: window,
        escapeHtmlInline: escapeHtml,
        clamp,
        hostTeardownSignal: teardown.signal,
        galleryFor: (thumb) => {
            const tile = thumb.closest('.media-tile');
            const id = tile && tile.dataset.id;
            const index = state.gallery.findIndex((g) => g.id === id);
            return index < 0 ? null : { items: state.gallery, index };
        },
    });
    window.CuttleMediaThumbFallback = media.thumbFallback;
    media.bind();

    // ── Prefs ──────────────────────────────────────────────────────────
    function loadPrefs() {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(PREFS_KEY) || 'null'); } catch (_) {}
        const prefs = M.normalizePrefs(saved);
        prefs.chat = ''; // a chat filter is per visit, not a standing preference
        try {
            const chat = new URLSearchParams(location.search).get('chat');
            if (chat && /^\d+$/.test(chat)) prefs.chat = chat;
        } catch (_) {}
        return prefs;
    }
    function savePrefs() {
        try { localStorage.setItem(PREFS_KEY, JSON.stringify(Object.assign({}, state.prefs, { chat: '' }))); } catch (_) {}
        try {
            const url = new URL(location.href);
            if (state.prefs.chat) url.searchParams.set('chat', state.prefs.chat);
            else url.searchParams.delete('chat');
            history.replaceState(null, '', url.pathname + url.search);
        } catch (_) {}
    }
    function setPrefs(patch) {
        state.prefs = M.normalizePrefs(Object.assign({}, state.prefs, patch));
        state.limit = PAGE_SIZE;
        savePrefs();
        syncControls();
        render();
    }

    // ── Transport ──────────────────────────────────────────────────────
    async function load() {
        if (state.loading) return;
        state.loading = true;
        els.refresh.disabled = true;
        try {
            const resp = await fetch('/api/media-library', { credentials: 'include', cache: 'no-store' });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || `Request failed (${resp.status})`);
            state.items = Array.isArray(data.items) ? data.items : [];
            state.chats = Array.isArray(data.chats) ? data.chats : [];
            state.totals = data.totals || null;
            fillOptions();
            syncControls();
            render();
        } catch (err) {
            showNotice('Could not load media: ' + (err && err.message ? err.message : err), true);
        } finally {
            state.loading = false;
            els.refresh.disabled = false;
        }
    }

    function navigate(page) {
        if (window.parent !== window) window.parent.postMessage({ type: 'cuttle-navigate', page }, location.origin);
        else location.href = page;
    }
    function openChat(chatId, shareIndex) {
        let page = '/chat_page.html?chat=' + encodeURIComponent(chatId);
        if (shareIndex) page += '&msg=' + encodeURIComponent(shareIndex);
        navigate(page);
    }

    // ── Controls ───────────────────────────────────────────────────────
    function setOptions(select, options, allLabel) {
        const value = select.value;
        select.innerHTML = '';
        if (allLabel) select.appendChild(new Option(allLabel, ''));
        options.forEach((o) => select.appendChild(new Option(o.label, o.value)));
        select.value = value;
    }

    function fillOptions() {
        setOptions(els.chat, M.chatOptions(state.chats), 'All chats');
        // A ?chat= link to a chat without media still filters (and shows empty).
        const pinned = state.prefs.chat;
        if (pinned && !state.chats.some((c) => String(c.id) === pinned)) {
            els.chat.appendChild(new Option('CH-' + pinned.padStart(6, '0') + ' (0)', pinned));
        }
        setOptions(els.project, M.projectOptions(state.items), 'All projects');
        els.project.closest('label').hidden = els.project.options.length <= 2 && !state.prefs.project;
        if (!els.range.options.length) {
            setOptions(els.range, Object.keys(M.RANGES).map((k) => ({ value: k, label: M.RANGES[k].label })));
        }
        if (!els.sort.options.length) {
            setOptions(els.sort, Object.keys(M.SORTS).map((k) => ({ value: k, label: M.SORTS[k].label })));
        }
    }

    function syncControls() {
        const p = state.prefs;
        if (document.activeElement !== els.search) els.search.value = p.query;
        document.querySelectorAll('select[data-pref]').forEach((sel) => {
            const v = String(p[sel.dataset.pref] ?? '');
            if (sel.value !== v) sel.value = v;
        });
        document.querySelectorAll('input[type="checkbox"][data-pref]').forEach((box) => {
            box.checked = !!p[box.dataset.pref];
        });
        document.querySelectorAll('.media-seg[data-pref]').forEach((seg) => {
            seg.querySelectorAll('button[data-value]').forEach((b) => {
                b.setAttribute('aria-checked', String(p[seg.dataset.pref] === b.dataset.value));
            });
        });
        els.grid.dataset.size = p.size;
        els.clear.hidden = M.activeFilterCount(p) === 0;
    }

    function wireControls() {
        let searchTimer = null;
        els.search.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => setPrefs({ query: els.search.value }), 150);
        });
        document.querySelectorAll('select[data-pref]').forEach((sel) => {
            sel.addEventListener('change', () => setPrefs({ [sel.dataset.pref]: sel.value }));
        });
        document.querySelectorAll('input[type="checkbox"][data-pref]').forEach((box) => {
            box.addEventListener('change', () => setPrefs({ [box.dataset.pref]: box.checked }));
        });
        document.querySelectorAll('.media-seg[data-pref]').forEach((seg) => {
            seg.addEventListener('click', (e) => {
                const b = e.target.closest('button[data-value]');
                if (b) setPrefs({ [seg.dataset.pref]: b.dataset.value });
            });
        });
        els.clear.addEventListener('click', () => setPrefs(M.clearedFilters(state.prefs)));
        els.refresh.addEventListener('click', load);
        els.grid.addEventListener('click', (e) => {
            const open = e.target.closest('[data-open-chat]');
            if (open) {
                e.preventDefault();
                openChat(open.dataset.openChat, open.dataset.msg || '');
                return;
            }
            const only = e.target.closest('[data-filter-chat]');
            if (only) {
                e.preventDefault();
                setPrefs({ chat: only.dataset.filterChat });
                window.scrollTo({ top: 0 });
            }
        });
        els.more.addEventListener('click', (e) => {
            if (e.target.closest('button')) showMore();
        });
        const clearBtn = (e) => {
            if (e.target.closest('[data-clear-filters]')) setPrefs(M.clearedFilters(state.prefs));
        };
        els.notice.addEventListener('click', clearBtn);
    }

    // ── Render ─────────────────────────────────────────────────────────
    function showNotice(text, isError) {
        els.notice.hidden = false;
        els.notice.classList.toggle('is-error', !!isError);
        els.notice.innerHTML = text;
        if (isError) els.notice.textContent = text;
    }

    function relTime(iso) {
        const t = Date.parse(iso);
        if (!Number.isFinite(t)) return '';
        const s = Math.max(0, (Date.now() - t) / 1000);
        if (s < 60) return 'just now';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        if (s < 86400 * 30) return Math.floor(s / 86400) + 'd ago';
        return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function sourceLabel(it) {
        if (it.source === 'upload') return 'You uploaded';
        if (it.source === 'user') return 'You shared';
        return 'Agent shared';
    }

    function tileHtml(it) {
        const thumb = it.available === false
            ? '<div class="media-expired" title="The staged copy expired or the file was removed">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/><path d="M4 4l16 16"/></svg>' +
              '<span>Expired</span></div>'
            : media.buildMediaThumbHtml(it.url, { kind: it.kind, title: it.title || it.name });
        const when = new Date(Date.parse(it.shared_at));
        const whenTitle = Number.isFinite(when.getTime()) ? when.toLocaleString() : '';
        const size = M.formatSize(it.size);
        const chat = M.chatLabel(it);
        const tags = [];
        if (it.chat_archived) tags.push('<span class="media-tag">Archived</span>');
        if (it.refs > 1) tags.push('<span class="media-tag" title="Shown in ' + it.refs + ' messages">×' + it.refs + '</span>');
        return (
            '<article class="media-tile' + (it.available === false ? ' is-expired' : '') + '" data-id="' + escapeHtml(it.id) + '" role="listitem">' +
                '<div class="media-thumb-wrap">' + thumb + '</div>' +
                '<div class="media-meta">' +
                    '<div class="media-name" title="' + escapeHtml(it.title || it.name) + '">' + escapeHtml(it.name) + '</div>' +
                    '<div class="media-sub">' +
                        '<span title="' + escapeHtml(whenTitle) + '">' + escapeHtml(relTime(it.shared_at)) + '</span>' +
                        (size ? '<span>' + escapeHtml(size) + '</span>' : '') +
                        '<span>' + escapeHtml(sourceLabel(it)) + '</span>' +
                        tags.join('') +
                    '</div>' +
                    '<div class="media-chat">' +
                        '<a href="/chat_page.html?chat=' + it.chat_id + (it.share_index ? '&amp;msg=' + it.share_index : '') + '" class="media-chat-link" data-open-chat="' + it.chat_id + '" data-msg="' + (it.share_index || '') + '" title="Open ' + escapeHtml(it.handle) + ' at this message">' +
                            '<span class="media-handle">' + escapeHtml(it.handle) + '</span>' +
                            '<span class="media-chat-title">' + escapeHtml(chat) + '</span>' +
                        '</a>' +
                        '<button type="button" class="media-icon-btn" data-filter-chat="' + it.chat_id + '" title="Only media from this chat" aria-label="Only media from this chat">' +
                            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 5h16l-6 7v6l-4 2v-8z"/></svg>' +
                        '</button>' +
                    '</div>' +
                '</div>' +
            '</article>'
        );
    }

    function groupHeadHtml(g) {
        if (!g.label) return '';
        const total = state.groupTotals.get(g.key) || g.items.length;
        let extra = '';
        if (g.chat) {
            extra = '<a href="/chat_page.html?chat=' + g.chat.id + '" class="media-group-link" data-open-chat="' + g.chat.id + '">' + escapeHtml(g.chat.handle) + '</a>';
        }
        return '<h2 class="media-group-head"><span>' + escapeHtml(g.label) + '</span>' + extra +
            '<span class="media-group-count">' + total + '</span></h2>';
    }

    function render() {
        const p = state.prefs;
        const filtered = M.filterItems(state.items, p);
        state.shown = M.sortItems(filtered, p.sort);
        state.gallery = M.galleryItems(state.shown);

        // Type tab counts follow every filter except the type itself.
        const typeless = M.filterItems(state.items, Object.assign({}, p, { kind: 'all' }));
        const counts = { all: typeless.length, image: 0, video: 0 };
        typeless.forEach((it) => { counts[it.kind] = (counts[it.kind] || 0) + 1; });
        document.querySelectorAll('[data-count]').forEach((el) => { el.textContent = counts[el.dataset.count] || '0'; });

        const total = state.items.length;
        const expired = state.items.filter((it) => it.available === false).length;
        els.summary.textContent = state.shown.length === total
            ? total + ' item' + (total === 1 ? '' : 's')
            : state.shown.length + ' of ' + total;
        els.toolbar.hidden = total === 0;

        if (!total) {
            els.grid.hidden = true;
            els.more.hidden = true;
            showNotice('Nothing yet. Images and videos you attach in chat, and media agents share in their replies, show up here.');
            return;
        }
        if (!state.shown.length) {
            els.grid.hidden = true;
            els.more.hidden = true;
            const hint = !p.showExpired && expired && M.filterItems(state.items, Object.assign({}, p, { showExpired: true })).length
                ? ' Some matches have expired; turn on <strong>Show expired</strong> to list them.' : '';
            showNotice('No media matches these filters.' + hint + ' <button type="button" class="media-link-btn" data-clear-filters>Clear filters</button>');
            return;
        }
        els.notice.hidden = true;

        state.groupTotals = new Map(M.groupItems(state.shown, p.group).map((g) => [g.key, g.items.length]));
        els.grid.innerHTML = '';
        state.rendered = 0;
        els.grid.hidden = false;
        appendTiles(state.limit);
    }

    /** Add the next tiles of `shown` without redrawing the ones already there. */
    function appendTiles(upTo) {
        const end = Math.min(upTo, state.shown.length);
        const slice = state.shown.slice(state.rendered, end);
        // Grouping a later slice gives the same groups a full redraw would:
        // items join their group's grid (sort order holds), new groups go last.
        M.groupItems(slice, state.prefs.group).forEach((g) => {
            let grid = els.grid.querySelector('.media-group[data-key="' + CSS.escape(g.key) + '"] > .media-grid');
            if (!grid) {
                els.grid.insertAdjacentHTML('beforeend',
                    '<section class="media-group" data-key="' + escapeHtml(g.key) + '">' + groupHeadHtml(g) +
                    '<div class="media-grid" role="list"></div></section>');
                grid = els.grid.lastElementChild.querySelector('.media-grid');
            }
            grid.insertAdjacentHTML('beforeend', g.items.map(tileHtml).join(''));
        });
        state.rendered = end;
        state.limit = Math.max(state.limit, end);

        const rest = state.shown.length - end;
        els.more.hidden = rest <= 0;
        els.more.innerHTML = rest > 0
            ? '<button type="button">Show ' + Math.min(rest, PAGE_SIZE) + ' more</button><span>' + rest + ' not shown</span>'
            : '';
        fillScreen();
    }

    function showMore() {
        if (state.rendered >= state.shown.length) return;
        appendTiles(state.rendered + PAGE_SIZE);
    }

    // Keep loading while the end marker is still within reach, so a tall window
    // or small tiles never strand the observer (it only fires on changes).
    let fillQueued = false;
    function fillScreen() {
        if (fillQueued || els.more.hidden) return;
        fillQueued = true;
        requestAnimationFrame(() => {
            fillQueued = false;
            if (!els.more.hidden && els.more.getBoundingClientRect().top < window.innerHeight + FILL_MARGIN_PX) showMore();
        });
    }

    // Load the next page as the end of the grid scrolls into view.
    if (typeof IntersectionObserver === 'function') {
        new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting) && !els.more.hidden) showMore();
        }, { rootMargin: FILL_MARGIN_PX + 'px 0px' }).observe(els.more);
    }

    wireControls();
    syncControls();
    load();
})();
