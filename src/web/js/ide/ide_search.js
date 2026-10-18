/* IDE App — Search view (find / replace in files). Owns its query state,
 * debounced requests, results tree and replace-all confirmation; opening a
 * match and refreshing replaced buffers go through `host`. */
(function (root) {
    'use strict';

    function create(host) {
        const el = host.el;
        const M = host.model;
        const esc = M.escapeHtml;
        el.innerHTML = ''
            + '<div class="ide-search-form">'
            + '<div class="ide-search-row"><button type="button" class="ide-icon-btn ide-search-expand" data-s="expand" title="Toggle replace" aria-expanded="false">›</button>'
            + '<div class="ide-search-field"><input type="text" data-s="query" placeholder="Search" spellcheck="false" aria-label="Search">'
            + '<button type="button" class="ide-toggle" data-s="case" title="Match case" aria-pressed="false">Aa</button>'
            + '<button type="button" class="ide-toggle" data-s="word" title="Match whole word" aria-pressed="false"><u>ab</u></button>'
            + '<button type="button" class="ide-toggle" data-s="regex" title="Use regular expression" aria-pressed="false">.*</button></div></div>'
            + '<div class="ide-search-row" data-s="replaceRow" hidden><span class="ide-search-spacer"></span>'
            + '<div class="ide-search-field"><input type="text" data-s="replace" placeholder="Replace" spellcheck="false" aria-label="Replace">'
            + '<button type="button" class="ide-toggle" data-s="replaceAll" title="Replace all">⇄ All</button></div></div>'
            + '<details class="ide-search-globs"><summary>files to include / exclude</summary>'
            + '<input type="text" data-s="include" placeholder="e.g. *.py, src/" spellcheck="false" aria-label="Files to include">'
            + '<input type="text" data-s="exclude" placeholder="e.g. tests/, *.min.js" spellcheck="false" aria-label="Files to exclude"></details>'
            + '</div><div class="ide-search-summary" data-s="summary"></div><div class="ide-search-results" data-s="results" role="tree"></div>';
        const q = (k) => el.querySelector('[data-s="' + k + '"]');
        const flags = { case: false, word: false, regex: false };
        let timer = null;
        let controller = null;
        let last = null;
        const collapsed = new Set();

        function options() {
            return {
                query: q('query').value, regex: flags.regex, case: flags.case, word: flags.word,
                include: q('include').value, exclude: q('exclude').value,
            };
        }
        function render() {
            const box = q('results');
            if (!last) { box.innerHTML = ''; return; }
            box.innerHTML = last.results.map((file) => {
                const open = !collapsed.has(file.path);
                const head = '<div class="ide-search-file' + (open ? ' is-open' : '') + '" data-file="' + esc(file.path) + '" title="' + esc(file.path) + '">'
                    + '<span class="ide-tree-chevron"></span><span class="ide-search-fname">' + esc(M.basename(file.path)) + '</span>'
                    + '<span class="ide-search-fdir">' + esc(M.dirname(file.path)) + '</span><span class="ide-search-count">' + file.matches.length + '</span></div>';
                if (!open) return head;
                return head + file.matches.map((m, i) => {
                    const p = M.previewParts(m);
                    return '<div class="ide-search-match" data-file="' + esc(file.path) + '" data-i="' + i + '" title="Line ' + m.line + '">'
                        + esc(p.before) + '<mark>' + esc(p.match) + '</mark>' + esc(p.after) + '<span class="ide-search-line">' + m.line + '</span></div>';
                }).join('');
            }).join('');
        }
        async function run() {
            const o = options();
            if (controller) controller.abort();
            if (!o.query) { last = null; q('summary').textContent = ''; render(); return; }
            const root = host.getRoot();
            if (!root) return;
            controller = new AbortController();
            const signal = controller.signal;
            q('summary').textContent = 'Searching…';
            try {
                const data = await host.api.search(root, o, { signal: signal });
                if (signal.aborted) return;
                last = data;
                q('summary').textContent = data.matches
                    ? data.matches + ' result' + (data.matches === 1 ? '' : 's') + ' in ' + data.files + ' file' + (data.files === 1 ? '' : 's') + (data.truncated ? ' (limit reached)' : '')
                    : 'No results';
                render();
            } catch (err) {
                if (signal.aborted || (err && err.name === 'AbortError')) return;
                last = null;
                render();
                q('summary').textContent = (err && err.message) || 'Search failed';
            }
        }
        function schedule(ms) {
            clearTimeout(timer);
            timer = setTimeout(run, ms == null ? 300 : ms);
        }
        ['query', 'include', 'exclude'].forEach((k) => q(k).addEventListener('input', () => schedule()));
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.matches('input')) { e.preventDefault(); schedule(0); }
        });
        ['case', 'word', 'regex'].forEach((k) => q(k).addEventListener('click', () => {
            flags[k] = !flags[k];
            q(k).setAttribute('aria-pressed', String(flags[k]));
            schedule(0);
        }));
        q('expand').addEventListener('click', () => {
            const show = q('replaceRow').hidden;
            q('replaceRow').hidden = !show;
            q('expand').setAttribute('aria-expanded', String(show));
            q('expand').classList.toggle('is-open', show);
        });
        q('replaceAll').addEventListener('click', async () => {
            const o = options();
            if (!o.query || !last || !last.matches) return;
            const files = last.results.map((r) => r.path);
            const ok = await host.confirm('Replace ' + last.matches + ' occurrence' + (last.matches === 1 ? '' : 's') + ' across ' + files.length + ' file' + (files.length === 1 ? '' : 's') + ' with "' + q('replace').value + '"?', 'Replace');
            if (!ok) return;
            try {
                const data = await host.api.replace(host.getRoot(), Object.assign({ replacement: q('replace').value, paths: files }, o));
                host.onReplaced(data.files || [], data.replacements || 0);
                schedule(0);
            } catch (err) { host.onError(err); }
        });
        q('results').addEventListener('click', (e) => {
            const match = e.target.closest('.ide-search-match');
            if (match) {
                const file = last.results.find((r) => r.path === match.dataset.file);
                const m = file && file.matches[Number(match.dataset.i)];
                if (m) host.onOpenMatch(file.path, m);
                el.querySelectorAll('.ide-search-match.is-active').forEach((n) => n.classList.remove('is-active'));
                match.classList.add('is-active');
                return;
            }
            const head = e.target.closest('.ide-search-file');
            if (head) {
                const p = head.dataset.file;
                if (collapsed.has(p)) collapsed.delete(p); else collapsed.add(p);
                render();
            }
        });

        return {
            /** Focus the query, optionally seeded with the editor selection. */
            focus(seed, replaceMode) {
                if (seed && !seed.includes('\n')) { q('query').value = seed; schedule(0); }
                if (replaceMode && q('replaceRow').hidden) q('expand').click();
                q('query').focus();
                q('query').select();
            },
            rerun() { if (q('query').value) schedule(0); },
            reset() { last = null; q('summary').textContent = ''; render(); },
        };
    }

    root.CuttleIdeSearch = { create: create };
})(globalThis);
