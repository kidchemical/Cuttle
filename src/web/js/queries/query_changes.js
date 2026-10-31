/* Run-wide Changes for the query log inspector.
 * Pure model → markup (renderHtml, splitPatch, verdictText) plus a small
 * controller (mount) that fetches /api/agent-events/runs/<id>/changes and
 * lazily renders per-file diffs. Capabilities arrive as deps; no page globals.
 */
(function (root) {
    'use strict';

    var STATUS = {
        reported: { label: 'reported', tone: 'ok', title: 'Changed, and the agent reported this edit' },
        observed: { label: 'seen at step', tone: 'ok', title: 'Changed, observed after a tool step (agent sent no diff)' },
        unreported: { label: 'unreported', tone: 'warn', title: 'Changed with no per-step evidence (shell writes, formatters, …)' },
        reverted: { label: 'no net change', tone: 'muted', title: 'Edited during the turn, unchanged at the end' },
        unverifiable: { label: 'not snapshotted', tone: 'muted', title: 'Binary, oversized or vanished: the snapshot skipped it' }
    };
    var VERDICT = {
        consistent: 'Every changed file matches what the agent reported.',
        unreported: 'Some changes were not reported by the agent (shell writes, formatters or extra edits).',
        ambiguous: 'Another run worked in this repository at the same time, so nothing is attributed from this snapshot.',
        snapshot_only: 'This agent does not report per-step edits; the turn snapshot is the record.',
        no_changes: 'This turn changed no files.',
        no_snapshot: 'No turn snapshot (non-Git workspace or capture unavailable).'
    };
    var DIFF_HEADER = /^diff --git a\/(.*) b\/(.*)$/;

    function defaultEsc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function captureScroll(root) {
        var saved = [];
        if (!root) return saved;
        var nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
        nodes.forEach(function (n, i) {
            if (n.scrollTop || n.scrollLeft) saved.push([i, n.scrollTop, n.scrollLeft]);
        });
        return saved;
    }

    function restoreScroll(root, saved) {
        if (!root || !saved || !saved.length) return;
        var nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
        saved.forEach(function (s) {
            var n = nodes[s[0]];
            if (n) { n.scrollTop = s[1]; n.scrollLeft = s[2]; }
        });
    }

    function verdictText(model) {
        var m = model || {};
        var text = VERDICT[m.verdict] || '';
        if (m.verdict === 'ambiguous' && (m.overlap || []).length) {
            text += ' Overlapping: ' + m.overlap.join(', ') + '.';
        }
        if (m.compacted) text += ' Older detail was compacted by retention.';
        return text;
    }

    function splitPatch(text) {
        var out = {}, current = null;
        String(text || '').split('\n').forEach(function (line) {
            var match = DIFF_HEADER.exec(line);
            if (match) { current = match[2]; out[current] = []; }
            if (current !== null) out[current].push(line);
        });
        Object.keys(out).forEach(function (k) { out[k] = out[k].join('\n'); });
        return out;
    }

    function counts(row) {
        if (row.additions == null && row.deletions == null) return '';
        return '<span class="query-changes-add">+' + (row.additions || 0) + '</span>' +
            '<span class="query-changes-del">−' + (row.deletions || 0) + '</span>';
    }

    function renderHtml(model, esc) {
        esc = esc || defaultEsc;
        var m = model || {};
        var files = m.files || [];
        var tally = Object.keys(m.counts || {}).sort().map(function (k) {
            return esc((STATUS[k] || { label: k }).label) + ' ' + m.counts[k];
        }).join(' · ');
        var html = '<section class="query-changes">' +
            '<p class="query-changes-verdict query-changes-verdict--' + esc(m.verdict) + '">' + esc(verdictText(m)) + '</p>' +
            '<p class="query-changes-meta">' + files.length + ' file' + (files.length === 1 ? '' : 's') +
            (tally ? ' · ' + tally : '') +
            ' · ' + (m.native_count || 0) + ' reported edit' + (m.native_count === 1 ? '' : 's') +
            (m.step_count ? ' · ' + m.step_count + ' step snapshot' + (m.step_count === 1 ? '' : 's') : '') + '</p>';
        files.forEach(function (row) {
            var status = STATUS[row.status] || { label: row.status, tone: 'muted', title: '' };
            var lines = row.lines === 'differs'
                ? '<span class="query-log-pill query-changes-pill--warn" title="The reported diff and the net change differ">lines differ</span>'
                : row.lines === 'match' ? '<span class="query-log-pill" title="Reported and net line counts match">lines match</span>' : '';
            var steps = (row.native || []).concat(row.steps || []).filter(function (s) { return s.seq; })
                .map(function (s) {
                    return '<button type="button" class="query-changes-step" data-seq="' + Number(s.seq) + '">step ' + Number(s.seq) + '</button>';
                }).join('');
            html += '<details class="query-changes-file" data-path="' + esc(row.path) + '">' +
                '<summary><span class="query-changes-path">' + esc(row.path) + '</span>' + counts(row) +
                '<span class="query-log-pill query-changes-pill--' + esc(status.tone) + '" title="' + esc(status.title) + '">' +
                esc(status.label) + '</span>' + lines + steps + '</summary>' +
                '<div class="query-changes-diff" data-diff-slot></div></details>';
        });
        (m.outside || []).forEach(function (row) {
            html += '<p class="query-changes-outside">Reported outside the repository: ' + esc(row.path) + '</p>';
        });
        return html + '</section>';
    }

    /**
     * Controller for one inspector body. deps: {fetchJson(url) → Promise,
     * renderDiff(host, patch), openStep(seq), esc?, revision?, isActive?}.
     * Re-fetches when `revision` moves (or every 3s while running without
     * one); open file rows and scroll offsets survive repaints.
     */
    function mount(body, queryId, deps) {
        var state = body._queryChanges;
        if (!state || state.queryId !== queryId) {
            state = body._queryChanges = { queryId: queryId, model: null, fetchedAt: 0, snapshot: null, loading: false };
        }
        // Open diffs carry their rendered nodes and scroll offsets across a
        // repaint so a live model update never collapses or rewinds them.
        function paint() {
            var prevTop = body.scrollTop;
            var open = {};
            body.querySelectorAll('details.query-changes-file[open]').forEach(function (d) {
                var slot = d.querySelector('[data-diff-slot]');
                open[d.getAttribute('data-path')] = {
                    nodes: slot ? Array.prototype.slice.call(slot.childNodes) : [],
                    scroll: captureScroll(slot)
                };
            });
            body.innerHTML = renderHtml(state.model, deps.esc);
            body.querySelectorAll('details.query-changes-file').forEach(function (d) {
                d.addEventListener('toggle', function () { if (d.open) fillDiff(d); });
                var prev = open[d.getAttribute('data-path')];
                if (!prev) return;
                var slot = d.querySelector('[data-diff-slot]');
                if (slot) prev.nodes.forEach(function (n) { slot.appendChild(n); });
                d.open = true;
                restoreScroll(slot, prev.scroll);
                fillDiff(d);
            });
            body.scrollTop = prevTop;
            body.querySelectorAll('.query-changes-step').forEach(function (b) {
                b.addEventListener('click', function (e) {
                    e.preventDefault(); e.stopPropagation();
                    deps.openStep(Number(b.getAttribute('data-seq')));
                });
            });
        }
        function snapshotPatches() {
            if (state.snapshot) return Promise.resolve(state.snapshot);
            if (!state.model || !state.model.snapshot_event_id) return Promise.resolve({});
            return deps.fetchJson('/api/agent-events/events/' + state.model.snapshot_event_id).then(function (row) {
                state.snapshot = splitPatch(row && row.detail && row.detail.text);
                return state.snapshot;
            });
        }
        function fillDiff(details) {
            var slot = details.querySelector('[data-diff-slot]');
            if (!slot || slot.getAttribute('data-filled')) return;
            slot.setAttribute('data-filled', '1');
            if (!slot.hasChildNodes()) slot.textContent = 'Loading…';
            snapshotPatches().then(function (patches) {
                var patch = patches[details.getAttribute('data-path')];
                var saved = captureScroll(slot);
                if (patch) deps.renderDiff(slot, patch);
                else slot.textContent = 'No net diff in the turn snapshot. Open a step for the reported edit.';
                restoreScroll(slot, saved);
            }).catch(function (error) {
                slot.textContent = (error && error.message) || 'Diff unavailable';
                slot.removeAttribute('data-filled');
            });
        }
        // Every live update lands here; repaint only when another tab replaced the body.
        if (state.model && !body.querySelector('.query-changes')) paint();
        // deps.revision (edit rows + run state) gates refetches; without one,
        // a running model refreshes at most every 3s.
        var revision = deps.revision == null ? null : String(deps.revision);
        var stale = !state.model || (revision !== null
            ? revision !== state.revision
            : state.model.status === 'running' && Date.now() - state.fetchedAt >= 3000);
        if (!stale) return;
        if (state.loading) { state.pendingDeps = deps; return; }
        state.loading = true;
        state.revision = revision;
        if (!state.model) body.innerHTML = '<p class="query-log-empty">Loading changes…</p>';
        // The host may have switched tabs while a fetch was in flight.
        function active() { return body._queryChanges === state && (!deps.isActive || deps.isActive()); }
        function settle() {
            state.loading = false;
            state.fetchedAt = Date.now();
            var next = state.pendingDeps;
            state.pendingDeps = null;
            if (next && active()) mount(body, queryId, next);
        }
        deps.fetchJson('/api/agent-events/runs/' + encodeURIComponent(queryId) + '/changes').then(function (model) {
            var changed = JSON.stringify(model) !== JSON.stringify(state.model);
            if (changed) { state.model = model; state.snapshot = null; }
            if (active() && (changed || !body.querySelector('.query-changes'))) paint();
            settle();
        }).catch(function (error) {
            state.revision = null;
            if (!state.model && active()) {
                body.innerHTML = '<p class="query-log-empty">' + (deps.esc || defaultEsc)((error && error.message) || 'Changes unavailable') + '</p>';
            }
            settle();
        });
    }

    root.CuttleQueryChanges = { STATUS: STATUS, VERDICT: VERDICT, verdictText: verdictText, splitPatch: splitPatch, renderHtml: renderHtml, mount: mount };
    if (typeof module === 'object' && module.exports) module.exports = root.CuttleQueryChanges;
})(typeof window === 'undefined' ? globalThis : window);
