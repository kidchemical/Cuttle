/**
 * Media App model — pure filter / sort / group / gallery over Media items.
 *
 * Items come from GET /api/media-library (api.media_library.index.build_items):
 * { id, url, kind, name, title, source, role, message_id, share_index, handle,
 *   shared_at, chat_id, chat_title, project, chat_archived, chat_starred,
 *   available, size, refs }.
 * No DOM, storage or network here; media_library_page.js owns those.
 */
(function (root) {
    'use strict';

    const DAY_MS = 86400000;

    const DEFAULT_PREFS = Object.freeze({
        query: '',
        kind: 'all',        // all | image | video | audio
        source: 'all',      // all | mine (uploads + user markdown) | agents
        chat: '',           // chat id ('' = every chat)
        project: '',        // project name ('' = every project, '-' = no project)
        range: 'any',       // any | day | week | month | year
        sort: 'newest',     // see SORTS
        group: 'none',      // none | day | chat | project
        size: 'medium',     // small | medium | large (tile density)
        showExpired: false, // include items whose file is gone
        showArchived: true, // include media from archived chats
    });

    const SORTS = {
        newest: { label: 'Newest first' },
        oldest: { label: 'Oldest first' },
        name: { label: 'Name A–Z' },
        'name-desc': { label: 'Name Z–A' },
        chat: { label: 'Chat A–Z' },
        'size-desc': { label: 'Largest first' },
        'size-asc': { label: 'Smallest first' },
    };
    const RANGES = {
        any: { label: 'Any time', days: 0 },
        day: { label: 'Last 24 hours', days: 1 },
        week: { label: 'Last 7 days', days: 7 },
        month: { label: 'Last 30 days', days: 30 },
        year: { label: 'Last 12 months', days: 365 },
    };
    const CHOICES = {
        kind: ['all', 'image', 'video', 'audio'],
        source: ['all', 'mine', 'agents'],
        range: Object.keys(RANGES),
        sort: Object.keys(SORTS),
        group: ['none', 'day', 'chat', 'project'],
        size: ['small', 'medium', 'large'],
    };

    function normalizePrefs(raw) {
        const src = raw && typeof raw === 'object' ? raw : {};
        const out = Object.assign({}, DEFAULT_PREFS);
        Object.keys(CHOICES).forEach((k) => {
            if (CHOICES[k].includes(src[k])) out[k] = src[k];
        });
        ['query', 'chat', 'project'].forEach((k) => {
            if (typeof src[k] === 'string' || typeof src[k] === 'number') out[k] = String(src[k]).trim();
        });
        ['showExpired', 'showArchived'].forEach((k) => {
            if (typeof src[k] === 'boolean') out[k] = src[k];
        });
        return out;
    }

    function timeOf(item) {
        const t = Date.parse(item && item.shared_at);
        return Number.isFinite(t) ? t : 0;
    }

    function chatLabel(item) {
        return String((item && item.chat_title) || '').trim() || ('Chat ' + String(item && item.chat_id || ''));
    }

    function isMine(item) {
        return item.source === 'upload' || item.source === 'user';
    }

    function matchesQuery(item, q) {
        if (!q) return true;
        const hay = [item.name, item.title, item.chat_title, item.project, item.handle, item.url]
            .map((v) => String(v || '').toLowerCase()).join('\n');
        return q.split(/\s+/).filter(Boolean).every((word) => hay.includes(word));
    }

    function filterItems(items, prefs, nowMs) {
        const p = normalizePrefs(prefs);
        const q = p.query.toLowerCase();
        const days = RANGES[p.range].days;
        const cutoff = days ? (Number(nowMs) || Date.now()) - days * DAY_MS : 0;
        return (Array.isArray(items) ? items : []).filter((it) => {
            if (!it) return false;
            if (!p.showExpired && it.available === false) return false;
            if (!p.showArchived && it.chat_archived) return false;
            if (p.kind !== 'all' && it.kind !== p.kind) return false;
            if (p.source === 'mine' && !isMine(it)) return false;
            if (p.source === 'agents' && it.source !== 'agent') return false;
            if (p.chat && String(it.chat_id) !== p.chat) return false;
            if (p.project === '-' ? !!it.project : (p.project && it.project !== p.project)) return false;
            if (cutoff && timeOf(it) < cutoff) return false;
            return matchesQuery(it, q);
        });
    }

    const collator = typeof Intl !== 'undefined' && Intl.Collator
        ? new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })
        : { compare: (a, b) => (a < b ? -1 : a > b ? 1 : 0) };

    /** Stable sort; ties fall back to newest first so order never jitters. */
    function sortItems(items, sort) {
        const key = SORTS[sort] ? sort : 'newest';
        const newest = (a, b) => (timeOf(b) - timeOf(a)) || (b.message_id - a.message_id) || collator.compare(a.id, b.id);
        const size = (it) => (Number.isFinite(it.size) ? it.size : -1);
        const cmp = {
            newest,
            oldest: (a, b) => -newest(a, b),
            name: (a, b) => collator.compare(a.name || '', b.name || '') || newest(a, b),
            'name-desc': (a, b) => collator.compare(b.name || '', a.name || '') || newest(a, b),
            chat: (a, b) => collator.compare(chatLabel(a), chatLabel(b)) || (a.chat_id - b.chat_id) || newest(a, b),
            'size-desc': (a, b) => (size(b) - size(a)) || newest(a, b),
            'size-asc': (a, b) => {
                const sa = size(a); const sb = size(b);
                if (sa < 0 || sb < 0) return ((sa < 0) - (sb < 0)) || newest(a, b); // unknown sizes last
                return (sa - sb) || newest(a, b);
            },
        }[key];
        return (Array.isArray(items) ? items : []).slice().sort(cmp);
    }

    function dayKey(ms) {
        const d = new Date(ms);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }

    function dayLabel(ms, nowMs) {
        if (!ms) return 'Unknown date';
        const now = Number(nowMs) || Date.now();
        const key = dayKey(ms);
        if (key === dayKey(now)) return 'Today';
        if (key === dayKey(now - DAY_MS)) return 'Yesterday';
        const d = new Date(ms);
        const opts = { weekday: 'short', month: 'short', day: 'numeric' };
        if (d.getFullYear() !== new Date(now).getFullYear()) opts.year = 'numeric';
        return d.toLocaleDateString(undefined, opts);
    }

    /** One heading per day / chat / project, in order of first appearance; items keep the sort order. */
    function groupItems(items, group, nowMs) {
        const list = Array.isArray(items) ? items : [];
        if (!['day', 'chat', 'project'].includes(group)) {
            return list.length ? [{ key: 'all', label: '', items: list.slice() }] : [];
        }
        const groups = [];
        const byKey = new Map();
        list.forEach((it) => {
            let key; let label; let chat = null;
            if (group === 'day') {
                const t = timeOf(it);
                key = t ? dayKey(t) : '?';
                label = dayLabel(t, nowMs);
            } else if (group === 'chat') {
                key = 'c' + it.chat_id;
                label = chatLabel(it);
                chat = { id: it.chat_id, handle: String(it.handle || '').replace(/-\d+$/, ''), archived: !!it.chat_archived };
            } else {
                key = 'p' + (it.project || '');
                label = it.project || 'No project';
            }
            let g = byKey.get(key);
            if (!g) {
                g = { key, label, items: [], chat };
                byKey.set(key, g);
                groups.push(g);
            }
            g.items.push(it);
        });
        return groups;
    }

    /** Lightbox items for the shown order; expired files are skipped. */
    function galleryItems(items) {
        return (Array.isArray(items) ? items : []).filter((it) => it && it.available !== false).map((it) => {
            const where = chatLabel(it) + ' · ' + (it.handle || '');
            return {
                id: it.id,
                src: it.url,
                kind: it.kind,
                title: it.title || it.name || '',
                description: it.title ? it.title + ' — ' + where : where,
                download: it.url.startsWith('/output/') ? it.url + (it.url.includes('?') ? '&' : '?') + 'download=1' : it.url,
                originalPath: '',
            };
        });
    }

    function chatOptions(chats) {
        return (Array.isArray(chats) ? chats : []).map((c) => ({
            value: String(c.id),
            label: (String(c.title || '').trim() || ('Chat ' + c.id)) + ' (' + c.count + ')',
        }));
    }

    function projectOptions(items) {
        const counts = new Map();
        let none = 0;
        (Array.isArray(items) ? items : []).forEach((it) => {
            if (it.project) counts.set(it.project, (counts.get(it.project) || 0) + 1);
            else none += 1;
        });
        const opts = Array.from(counts.keys()).sort((a, b) => collator.compare(a, b))
            .map((p) => ({ value: p, label: p + ' (' + counts.get(p) + ')' }));
        if (none && opts.length) opts.push({ value: '-', label: 'No project (' + none + ')' });
        return opts;
    }

    function formatSize(bytes) {
        if (bytes == null || bytes === '') return '';
        const n = Number(bytes);
        if (!Number.isFinite(n) || n < 0) return '';
        if (n < 1024) return n + ' B';
        const units = ['KB', 'MB', 'GB'];
        let v = n / 1024; let i = 0;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
        return (v >= 10 ? Math.round(v) : Math.round(v * 10) / 10) + ' ' + units[i];
    }

    function activeFilterCount(prefs) {
        const p = normalizePrefs(prefs);
        return ['query', 'chat', 'project'].filter((k) => p[k]).length
            + ['kind', 'source', 'range'].filter((k) => p[k] !== DEFAULT_PREFS[k]).length
            + (p.showExpired !== DEFAULT_PREFS.showExpired ? 1 : 0)
            + (p.showArchived !== DEFAULT_PREFS.showArchived ? 1 : 0);
    }

    /** Filters cleared, view choices (sort, group, size) kept. */
    function clearedFilters(prefs) {
        const p = normalizePrefs(prefs);
        return Object.assign({}, DEFAULT_PREFS, { sort: p.sort, group: p.group, size: p.size });
    }

    const api = {
        DEFAULT_PREFS, SORTS, RANGES,
        normalizePrefs, filterItems, sortItems, groupItems, galleryItems,
        chatOptions, projectOptions, formatSize, dayLabel, chatLabel,
        activeFilterCount, clearedFilters,
    };
    root.CuttleMediaLibraryModel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
