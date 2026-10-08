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
     * renderDiff(host, patch), openStep(seq), esc?}. Re-fetches only while the
     * run is live; open file rows survive repaints.
     */
    function mount(body, queryId, deps) {
        var state = body._queryChanges;
        if (!state || state.queryId !== queryId) {
            state = body._queryChanges = { queryId: queryId, model: null, fetchedAt: 0, snapshot: null, loading: false };
        }
        function paint() {
            var open = {};
            body.querySelectorAll('details.query-changes-file[open]').forEach(function (d) { open[d.getAttribute('data-path')] = 1; });
            body.innerHTML = renderHtml(state.model, deps.esc);
            body.querySelectorAll('details.query-changes-file').forEach(function (d) {
                d.addEventListener('toggle', function () { if (d.open) fillDiff(d); });
                if (open[d.getAttribute('data-path')]) { d.open = true; fillDiff(d); }
            });
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
            slot.textContent = 'Loading…';
            snapshotPatches().then(function (patches) {
                var patch = patches[details.getAttribute('data-path')];
                if (patch) deps.renderDiff(slot, patch);
                else slot.textContent = 'No net diff in the turn snapshot. Open a step for the reported edit.';
            }).catch(function (error) {
                slot.textContent = (error && error.message) || 'Diff unavailable';
                slot.removeAttribute('data-filled');
            });
        }
        var live = !state.model || state.model.status === 'running';
        if (state.model) paint();
        if (state.loading || (!live && state.model) || Date.now() - state.fetchedAt < 3000) return;
        state.loading = true;
        if (!state.model) body.innerHTML = '<p class="query-log-empty">Loading changes…</p>';
        deps.fetchJson('/api/agent-events/runs/' + encodeURIComponent(queryId) + '/changes').then(function (model) {
            state.loading = false;
            state.fetchedAt = Date.now();
            var changed = JSON.stringify(model) !== JSON.stringify(state.model);
            if (changed) { state.model = model; state.snapshot = null; }
            if (changed || !body.querySelector('.query-changes')) paint();
        }).catch(function (error) {
            state.loading = false;
            state.fetchedAt = Date.now();
            body.innerHTML = '<p class="query-log-empty">' + (deps.esc || defaultEsc)((error && error.message) || 'Changes unavailable') + '</p>';
        });
    }

    root.CuttleQueryChanges = { STATUS: STATUS, VERDICT: VERDICT, verdictText: verdictText, splitPatch: splitPatch, renderHtml: renderHtml, mount: mount };
    if (typeof module === 'object' && module.exports) module.exports = root.CuttleQueryChanges;
})(typeof window === 'undefined' ? globalThis : window);
