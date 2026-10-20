/* IDE App — agent review, pure model (no DOM, no Monaco, no fetch).
 * Turns server hunks (owner api.agent_review) into editor-space layout,
 * steps between changes, and indexes reviewed files for tab / explorer
 * badges. Line numbers are 1-based in the current text. */
(function (root) {
    'use strict';

    /** One entry per hunk: added line range, removed lines, where they render. */
    function layout(hunks) {
        return (hunks || []).map((h) => {
            const added = h.new_count > 0 ? { start: h.new_start, end: h.new_start + h.new_count - 1 } : null;
            return {
                id: h.id,
                added: added,
                removed: (h.removed || []).map((line) => String(line).replace(/\r?\n$/, '')),
                // Removed lines sit just above the added ones (or at the deletion point).
                zoneAfter: Math.max(0, h.new_start - 1),
                // Keep / Undo sit right under the hunk.
                actionAfter: added ? added.end : Math.max(0, h.new_start - 1),
                anchor: added ? added.start : Math.max(1, h.new_start - 1),
                additions: h.new_count || 0,
                deletions: h.old_count || 0,
            };
        });
    }

    /** Next (dir > 0) or previous change relative to `line`, wrapping; null when none. */
    function nextHunk(items, line, dir) {
        const list = (items || []).slice().sort((a, b) => a.anchor - b.anchor);
        if (!list.length) return null;
        if (dir < 0) {
            for (let i = list.length - 1; i >= 0; i--) if (list[i].anchor < line) return list[i];
            return list[list.length - 1];
        }
        for (const item of list) if (item.anchor > line) return item;
        return list[0];
    }

    /** Next change after `line` without wrapping (for stepping on to the next file). */
    function hasHunkAfter(items, line, dir) {
        return (items || []).some((h) => (dir < 0 ? h.anchor < line : h.anchor > line));
    }

    /** The change under the cursor, else the nearest one below it. */
    function hunkAt(items, line) {
        const list = items || [];
        // A pure deletion has no lines of its own: the lines around it count.
        const inside = list.find((h) => (h.added ? line >= h.added.start && line <= h.added.end
            : line === h.anchor || line === h.anchor + 1));
        return inside || nextHunk(list, line - 1, 1);
    }

    /** {files: {path: entry}, dirs: {dir: count}, order: [paths], totals}. */
    function index(files) {
        const out = { files: {}, dirs: {}, order: [], totals: { files: 0, hunks: 0, additions: 0, deletions: 0, live: false } };
        (files || []).forEach((f) => {
            if (!f || !f.path || !f.hunks) return;
            out.files[f.path] = { hunks: f.hunks, additions: f.additions || 0, deletions: f.deletions || 0,
                live: !!f.live, change: f.change || 'modified', mixed: !!f.mixed, agent: f.agent_id || '' };
            out.order.push(f.path);
            const parts = f.path.split('/');
            for (let i = 1; i < parts.length; i++) {
                const dir = parts.slice(0, i).join('/');
                out.dirs[dir] = (out.dirs[dir] || 0) + f.hunks;
            }
            out.totals.files += 1;
            out.totals.hunks += f.hunks;
            out.totals.additions += f.additions || 0;
            out.totals.deletions += f.deletions || 0;
            out.totals.live = out.totals.live || !!f.live;
        });
        out.order.sort();
        return out;
    }

    /** Reviewed file after (dir > 0) or before `path`, wrapping; null when none. */
    function nextFile(idx, path, dir) {
        const order = (idx && idx.order) || [];
        if (!order.length) return null;
        const i = order.indexOf(path);
        if (i < 0) return dir < 0 ? order[order.length - 1] : order[0];
        if (order.length === 1) return null;
        return order[(i + (dir < 0 ? -1 : 1) + order.length) % order.length];
    }

    function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

    /** Review bar headline, e.g. "3 files · 7 changes · +40 −12". */
    function summary(totals) {
        const t = totals || {};
        if (!t.files) return '';
        return plural(t.files, 'file') + ' · ' + plural(t.hunks, 'change') + ' · +' + (t.additions || 0) + ' −' + (t.deletions || 0);
    }

    const api = { layout, nextHunk, hasHunkAfter, hunkAt, index, nextFile, summary };
    root.CuttleIdeReviewModel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
