/* IDE App — editor engine. Owns the Monaco lifetime (CDN load, models per
 * open file, view state, dirty tracking, diff editor, gutter decorations,
 * inline completions) behind one small interface, with a plain textarea
 * engine when Monaco cannot load (offline). The page never touches Monaco.
 * Agent review (`setReview`) renders CuttleIdeReviewModel.layout() items:
 * added lines, removed lines as read-only zones, Keep / Undo per hunk. */
(function (root) {
    'use strict';

    const MONACO_VERSION = '0.52.2';
    const MONACO_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@' + MONACO_VERSION + '/min/';
    let loadPromise = null;

    /** Resolve to `window.monaco`, or null when the CDN is unreachable. */
    function loadMonaco(timeoutMs) {
        if (root.monaco && root.monaco.editor) return Promise.resolve(root.monaco);
        if (loadPromise) return loadPromise;
        loadPromise = new Promise((resolve) => {
            const timer = setTimeout(() => resolve(null), timeoutMs || 15000);
            const done = (value) => { clearTimeout(timer); resolve(value); };
            // Workers load cross-origin through a same-origin data: shim.
            root.MonacoEnvironment = {
                getWorkerUrl: function () {
                    const src = "self.MonacoEnvironment={baseUrl:'" + MONACO_BASE + "'};importScripts('" + MONACO_BASE + "vs/base/worker/workerMain.js');";
                    return 'data:text/javascript;charset=utf-8,' + encodeURIComponent(src);
                },
            };
            const css = document.createElement('link');
            css.rel = 'stylesheet';
            css.href = MONACO_BASE + 'vs/editor/editor.main.css';
            css.dataset.cuttleCdn = 'monaco-css';
            document.head.appendChild(css);
            const script = document.createElement('script');
            script.src = MONACO_BASE + 'vs/loader.js';
            script.dataset.cuttleCdn = 'monaco-loader';
            script.onerror = () => done(null);
            script.onload = () => {
                try {
                    root.require.config({ paths: { vs: MONACO_BASE + 'vs' } });
                    root.require(['vs/editor/editor.main'], () => done(root.monaco || null), () => done(null));
                } catch (_) { done(null); }
            };
            document.head.appendChild(script);
        });
        return loadPromise;
    }

    // ── Monaco engine ───────────────────────────────────────────────
    function createMonaco(monaco, host) {
        const models = new Map(); // path → {model, saved, viewState}
        let current = null;
        let diffEditor = null;
        let decorations = null;
        const editor = monaco.editor.create(host.container, {
            automaticLayout: true,
            fontSize: 13,
            fontFamily: "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
            fontLigatures: true,
            minimap: { enabled: true },
            smoothScrolling: true,
            cursorSmoothCaretAnimation: 'on',
            renderWhitespace: 'selection',
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: 'active', indentation: true },
            stickyScroll: { enabled: true },
            inlineSuggest: { enabled: true },
            scrollBeyondLastLine: false,
            tabSize: 4,
            detectIndentation: true,
            model: null,
        });

        function applyTheme() {
            monaco.editor.defineTheme('cuttle', root.CuttleIdeTheme.monacoTheme());
            monaco.editor.setTheme('cuttle');
        }
        applyTheme();

        function uriFor(path) { return monaco.Uri.from({ scheme: 'file', path: '/' + path }); }
        function languageFor(path) {
            const name = path.split('/').pop().toLowerCase();
            const ext = name.includes('.') ? '.' + name.split('.').pop() : '';
            const lang = monaco.languages.getLanguages().find((l) =>
                (l.filenames || []).some((f) => f.toLowerCase() === name) || (ext && (l.extensions || []).includes(ext)));
            return lang ? lang.id : host.fallbackLanguage(path);
        }
        function entry(path) { return models.get(path) || null; }

        // ── agent review: inline hunks (layout from CuttleIdeReviewModel) ──
        let review = null; // {path, decos, zoneIds, widgets}
        function clearReview() {
            if (!review) return;
            const r = review;
            review = null;
            r.decos.clear();
            editor.changeViewZones((acc) => r.zoneIds.forEach((id) => acc.removeZone(id)));
            r.widgets.forEach((w) => editor.removeContentWidget(w));
        }
        function removedZone(lines) {
            const font = editor.getOption(monaco.editor.EditorOption.fontInfo);
            const node = document.createElement('div');
            node.className = 'ide-review-removed';
            node.style.fontFamily = font.fontFamily;
            node.style.fontSize = font.fontSize + 'px';
            node.style.lineHeight = font.lineHeight + 'px';
            node.style.tabSize = String(editor.getModel().getOptions().tabSize);
            lines.forEach((text) => {
                const row = document.createElement('div');
                row.className = 'ide-review-removed-line';
                row.textContent = text || ' ';
                node.appendChild(row);
            });
            return node;
        }
        /** Gutter beside removed lines: red like the lines, a "−" per line. */
        function removedMargin(count) {
            const font = editor.getOption(monaco.editor.EditorOption.fontInfo);
            const node = document.createElement('div');
            node.className = 'ide-review-removed-margin';
            node.style.lineHeight = font.lineHeight + 'px';
            node.style.fontSize = font.fontSize + 'px';
            node.innerHTML = '<div class="ide-review-removed-sign">−</div>'.repeat(count);
            return node;
        }
        function actionWidget(item, marker, handlers, keys) {
            const node = document.createElement('div');
            node.className = 'ide-review-actions';
            node.dataset.hunk = item.id;
            node.innerHTML = '<button type="button" class="is-keep" data-review="keep" title="Keep this change (' + keys.keep + ')">Keep</button>'
                + '<button type="button" class="is-undo" data-review="undo" title="Undo this change (' + keys.undo + ')">Undo</button>';
            // Keep Monaco from treating the click as a cursor move.
            node.addEventListener('mousedown', (e) => e.stopPropagation());
            node.addEventListener('click', (e) => {
                const b = e.target.closest('[data-review]');
                if (b && handlers[b.dataset.review]) handlers[b.dataset.review](item.id);
            });
            const BELOW = monaco.editor.ContentWidgetPositionPreference.BELOW;
            const ABOVE = monaco.editor.ContentWidgetPositionPreference.ABOVE;
            return {
                getId: () => 'cuttle.review.' + item.id,
                getDomNode: () => node,
                getPosition: () => {
                    // A deletion at the very top has no line above it.
                    if (!item.actionAfter) return { position: { lineNumber: 1, column: 1 }, preference: [ABOVE] };
                    // Follows edits above the hunk through its tracked marker.
                    const range = marker();
                    return range ? { position: { lineNumber: range.endLineNumber, column: 1 }, preference: [BELOW] } : null;
                },
            };
        }
        function applyReview(path, items, handlers, keys) {
            clearReview();
            const model = editor.getModel();
            if (!path || path !== current || !model || !items || !items.length) return;
            const last = model.getLineCount();
            const clamp = (n) => Math.min(Math.max(1, n), last);
            const green = 'rgba(46, 160, 67, 0.75)';
            const red = 'rgba(248, 81, 73, 0.75)';
            const ruler = (color) => ({ color: color, position: monaco.editor.OverviewRulerLane.Full });
            const minimap = (color) => ({ color: color, position: monaco.editor.MinimapPosition.Gutter });
            const decos = [];
            const markerIndex = [];
            items.forEach((item) => {
                if (item.added) {
                    decos.push({ range: new monaco.Range(clamp(item.added.start), 1, clamp(item.added.end), 1), options: {
                        isWholeLine: true, className: 'ide-review-added', marginClassName: 'ide-review-added-margin',
                        linesDecorationsClassName: 'ide-review-gutter-added',
                        overviewRuler: ruler(green), minimap: minimap(green),
                    } });
                }
                // Marker on the line the actions hang under; it moves with edits.
                const line = clamp(item.actionAfter || 1);
                markerIndex.push(decos.length);
                decos.push({ range: new monaco.Range(line, 1, line, 1), options: Object.assign({
                    isWholeLine: true, stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
                }, item.added ? {} : { overviewRuler: ruler(red), minimap: minimap(red), linesDecorationsClassName: 'ide-review-gutter-removed' }) });
            });
            const collection = editor.createDecorationsCollection(decos);
            const zoneIds = [];
            editor.changeViewZones((acc) => {
                items.forEach((item) => {
                    if (item.removed.length) {
                        zoneIds.push(acc.addZone({ afterLineNumber: Math.min(item.zoneAfter, last), heightInLines: item.removed.length,
                            domNode: removedZone(item.removed), marginDomNode: removedMargin(item.removed.length), ordinal: 0 }));
                    }
                    // Room for the Keep / Undo widget under the hunk.
                    const spacer = document.createElement('div');
                    spacer.className = 'ide-review-spacer';
                    zoneIds.push(acc.addZone({ afterLineNumber: Math.min(item.actionAfter, last), heightInPx: 28, domNode: spacer, ordinal: 1 }));
                });
            });
            const widgets = items.map((item, i) => actionWidget(item, () => collection.getRange(markerIndex[i]), handlers, keys));
            widgets.forEach((w) => editor.addContentWidget(w));
            review = { path: path, decos: collection, zoneIds: zoneIds, widgets: widgets };
        }

        editor.onDidChangeModelContent(() => {
            if (review) review.widgets.forEach((w) => editor.layoutContentWidget(w));
            if (current) host.onChange(current);
        });
        editor.onDidChangeCursorSelection(() => host.onCursor());

        const api = {
            engine: 'monaco',
            /** Register a document without showing it. */
            ensureModel(path, content) {
                if (entry(path)) return;
                const model = monaco.editor.createModel(content, languageFor(path), uriFor(path));
                models.set(path, { model: model, saved: model.getAlternativeVersionId(), viewState: null });
            },
            open(path, content, opts) {
                let e = entry(path);
                if (!e) {
                    const model = monaco.editor.createModel(content, languageFor(path), uriFor(path));
                    e = { model: model, saved: model.getAlternativeVersionId(), viewState: null };
                    models.set(path, e);
                }
                if (current && current !== path && entry(current)) entry(current).viewState = editor.saveViewState();
                if (current !== path) clearReview();
                current = path;
                editor.setModel(e.model);
                editor.updateOptions({ readOnly: !!(opts && opts.readOnly) });
                if (e.viewState) editor.restoreViewState(e.viewState);
                return e.model.getLanguageId();
            },
            has: (path) => models.has(path),
            current: () => current,
            detach() {
                if (current && entry(current)) entry(current).viewState = editor.saveViewState();
                clearReview();
                current = null;
                editor.setModel(null);
            },
            getValue: (path) => (entry(path) ? entry(path).model.getValue() : null),
            /** Replace content as one undoable edit (disk reloads stay undoable). */
            setValue(path, text, opts) {
                const e = entry(path);
                if (!e) return;
                const model = e.model;
                if (model.getValue() !== text) {
                    model.pushStackElement();
                    model.pushEditOperations([], [{ range: model.getFullModelRange(), text: text }], () => null);
                    model.pushStackElement();
                }
                if (opts && opts.saved) e.saved = model.getAlternativeVersionId();
            },
            isDirty: (path) => !!(entry(path) && entry(path).model.getAlternativeVersionId() !== entry(path).saved),
            markSaved(path) { const e = entry(path); if (e) e.saved = e.model.getAlternativeVersionId(); },
            close(path) {
                const e = entry(path);
                if (!e) return;
                if (current === path) api.detach();
                e.model.dispose();
                models.delete(path);
            },
            rename(from, to) {
                const e = entry(from);
                if (!e) return;
                const dirty = api.isDirty(from);
                const wasCurrent = current === from;
                const viewState = wasCurrent ? editor.saveViewState() : e.viewState;
                const model = monaco.editor.createModel(e.model.getValue(), languageFor(to), uriFor(to));
                if (wasCurrent) { clearReview(); editor.setModel(model); }
                e.model.dispose();
                models.delete(from);
                // A dirty buffer stays dirty under its new name until saved.
                models.set(to, { model: model, saved: dirty ? -1 : model.getAlternativeVersionId(), viewState: viewState });
                if (wasCurrent) {
                    current = to;
                    if (viewState) editor.restoreViewState(viewState);
                }
            },
            language: (path) => (entry(path) ? entry(path).model.getLanguageId() : 'plaintext'),
            setLanguage(path, id) { const e = entry(path); if (e) monaco.editor.setModelLanguage(e.model, id); },
            languages: () => monaco.languages.getLanguages().map((l) => ({ id: l.id, label: (l.aliases && l.aliases[0]) || l.id })),
            eol: (path) => (entry(path) && entry(path).model.getEOL() === '\r\n' ? 'CRLF' : 'LF'),
            setEol(path, crlf) {
                const e = entry(path);
                if (e) e.model.pushEOL(crlf ? monaco.editor.EndOfLineSequence.CRLF : monaco.editor.EndOfLineSequence.LF);
            },
            indent(path) {
                const e = entry(path);
                if (!e) return '';
                const o = e.model.getOptions();
                return (o.insertSpaces ? 'Spaces: ' : 'Tab Size: ') + o.tabSize;
            },
            focus: () => editor.focus(),
            layout: () => { editor.layout(); if (diffEditor) diffEditor.layout(); },
            reveal(line, col) {
                if (!line) return;
                const pos = { lineNumber: line, column: col || 1 };
                editor.setPosition(pos);
                editor.revealPositionInCenter(pos);
                editor.focus();
            },
            /** Highlight a search match range. */
            select(line, col, length) {
                const range = new monaco.Range(line, col, line, col + (length || 0));
                editor.setSelection(range);
                editor.revealRangeInCenter(range);
                editor.focus();
            },
            position() {
                const p = editor.getPosition();
                const sel = editor.getSelection();
                const model = editor.getModel();
                const selected = sel && model && !sel.isEmpty() ? model.getValueInRange(sel).length : 0;
                return p ? { line: p.lineNumber, col: p.column, selected: selected } : null;
            },
            selection() {
                const model = editor.getModel();
                const sel = editor.getSelection();
                if (!model || !sel) return null;
                return {
                    startLine: sel.startLineNumber,
                    endLine: sel.endColumn === 1 && sel.endLineNumber > sel.startLineNumber ? sel.endLineNumber - 1 : sel.endLineNumber,
                    text: sel.isEmpty() ? '' : model.getValueInRange(sel),
                };
            },
            setOptions(o) {
                editor.updateOptions({ minimap: { enabled: !!o.minimap }, wordWrap: o.wordWrap ? 'on' : 'off' });
            },
            applyTheme: applyTheme,
            run(actionId) {
                const action = editor.getAction(actionId);
                editor.focus();
                if (action) action.run();
                else editor.trigger('cuttle', actionId, null);
            },
            /** Quick-diff gutter ranges from CuttleIdeModel.lineDiff. */
            setGutter(ranges) {
                const decos = (ranges || []).map((r) => ({
                    range: new monaco.Range(Math.max(1, r.start), 1, Math.max(1, r.end), 1),
                    options: { isWholeLine: true, linesDecorationsClassName: 'ide-gutter-' + r.type },
                }));
                if (!decorations) decorations = editor.createDecorationsCollection(decos);
                else decorations.set(decos);
            },
            /** Inline agent review for the shown file; `items` null clears it. */
            setReview(path, items, handlers, keys) { applyReview(path, items, handlers || {}, keys || { keep: '', undo: '' }); },
            reviewPath: () => (review ? review.path : null),
            showDiff(original, modified, opts) {
                const o = opts || {};
                if (!diffEditor) {
                    diffEditor = monaco.editor.createDiffEditor(host.diffContainer, {
                        automaticLayout: true, renderSideBySide: o.sideBySide !== false, originalEditable: false,
                        readOnly: true, fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false,
                        useInlineViewWhenSpaceIsLimited: true,
                    });
                }
                const lang = o.path ? languageFor(o.path) : 'plaintext';
                const prev = diffEditor.getModel();
                diffEditor.setModel({
                    original: monaco.editor.createModel(original == null ? '' : original, lang),
                    modified: monaco.editor.createModel(modified == null ? '' : modified, lang),
                });
                if (prev) { prev.original.dispose(); prev.modified.dispose(); }
                diffEditor.updateOptions({ renderSideBySide: o.sideBySide !== false });
                setTimeout(() => diffEditor.layout(), 0);
            },
            hideDiff() {
                if (!diffEditor) return;
                const prev = diffEditor.getModel();
                diffEditor.setModel(null);
                if (prev) { prev.original.dispose(); prev.modified.dispose(); }
            },
            setDiffSideBySide(on) { if (diffEditor) diffEditor.updateOptions({ renderSideBySide: !!on }); },
            /** Ghost-text completions; `fetch(ctx, signal)` resolves to text. */
            registerCompletions(fetchCompletion) {
                return monaco.languages.registerInlineCompletionsProvider({ pattern: '**' }, {
                    provideInlineCompletions: async (model, position, _context, token) => {
                        const path = [...models.entries()].find(([, e]) => e.model === model);
                        if (!path) return { items: [] };
                        const full = model.getFullModelRange();
                        const prefix = model.getValueInRange(new monaco.Range(1, 1, position.lineNumber, position.column));
                        const suffix = model.getValueInRange(new monaco.Range(position.lineNumber, position.column, full.endLineNumber, full.endColumn));
                        const ctl = new AbortController();
                        token.onCancellationRequested(() => ctl.abort());
                        const text = await fetchCompletion({ path: path[0], language: model.getLanguageId(), prefix: prefix, suffix: suffix }, ctl.signal, token);
                        if (!text || token.isCancellationRequested) return { items: [] };
                        return { items: [{ insertText: text, range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column) }] };
                    },
                    freeInlineCompletions: () => {},
                    disposeInlineCompletions: () => {},
                });
            },
        };
        return api;
    }

    // ── textarea engine (offline fallback) ──────────────────────────
    function createTextarea(host) {
        const docs = new Map(); // path → {value, saved, scroll, sel}
        let current = null;
        const ta = document.createElement('textarea');
        ta.className = 'ide-textarea';
        ta.spellcheck = false;
        ta.wrap = 'off';
        host.container.appendChild(ta);
        const note = document.createElement('div');
        note.className = 'ide-diff-fallback';
        host.diffContainer.appendChild(note);
        ta.addEventListener('input', () => {
            if (!current) return;
            docs.get(current).value = ta.value;
            host.onChange(current);
        });
        ta.addEventListener('keyup', () => host.onCursor());
        ta.addEventListener('click', () => host.onCursor());
        ta.addEventListener('keydown', (e) => {
            if (e.key !== 'Tab' || e.ctrlKey || e.metaKey || e.altKey) return;
            e.preventDefault();
            const s = ta.selectionStart;
            ta.setRangeText('    ', s, ta.selectionEnd, 'end');
            ta.dispatchEvent(new Event('input'));
        });
        function lineCol(offset) {
            const before = ta.value.slice(0, offset);
            const lines = before.split('\n');
            return { line: lines.length, col: lines[lines.length - 1].length + 1 };
        }
        function offsetOf(line, col) {
            const lines = ta.value.split('\n');
            let off = 0;
            for (let i = 0; i < Math.min(line - 1, lines.length); i++) off += lines[i].length + 1;
            return off + Math.max(0, (col || 1) - 1);
        }
        const api = {
            engine: 'textarea',
            ensureModel(path, content) {
                if (!docs.has(path)) docs.set(path, { value: content, saved: content, scroll: 0, sel: 0 });
            },
            open(path, content, opts) {
                if (current && docs.has(current)) Object.assign(docs.get(current), { scroll: ta.scrollTop, sel: ta.selectionStart });
                if (!docs.has(path)) docs.set(path, { value: content, saved: content, scroll: 0, sel: 0 });
                current = path;
                const d = docs.get(path);
                ta.hidden = false;
                ta.value = d.value;
                ta.readOnly = !!(opts && opts.readOnly);
                ta.scrollTop = d.scroll;
                ta.selectionStart = ta.selectionEnd = d.sel;
                return host.fallbackLanguage(path);
            },
            has: (path) => docs.has(path),
            current: () => current,
            detach() { current = null; ta.value = ''; ta.hidden = true; },
            getValue: (path) => (docs.has(path) ? docs.get(path).value : null),
            setValue(path, text, opts) {
                const d = docs.get(path);
                if (!d) return;
                d.value = text;
                if (opts && opts.saved) d.saved = text;
                if (current === path) ta.value = text;
            },
            isDirty: (path) => !!(docs.get(path) && docs.get(path).value !== docs.get(path).saved),
            markSaved(path) { const d = docs.get(path); if (d) d.saved = d.value; },
            close(path) { if (current === path) api.detach(); docs.delete(path); },
            rename(from, to) {
                const d = docs.get(from);
                if (!d) return;
                docs.delete(from);
                docs.set(to, d);
                if (current === from) current = to;
            },
            language: (path) => host.fallbackLanguage(path),
            setLanguage() {},
            languages: () => [],
            eol: (path) => (docs.has(path) && docs.get(path).value.includes('\r\n') ? 'CRLF' : 'LF'),
            setEol() {},
            indent: () => 'Spaces: 4',
            focus: () => ta.focus(),
            layout() {},
            reveal(line, col) { const off = offsetOf(line, col); ta.focus(); ta.setSelectionRange(off, off); },
            select(line, col, length) { const off = offsetOf(line, col); ta.focus(); ta.setSelectionRange(off, off + (length || 0)); },
            position() {
                const p = lineCol(ta.selectionStart);
                return { line: p.line, col: p.col, selected: ta.selectionEnd - ta.selectionStart };
            },
            selection() {
                const a = lineCol(ta.selectionStart);
                const b = lineCol(ta.selectionEnd);
                return { startLine: a.line, endLine: b.line, text: ta.value.slice(ta.selectionStart, ta.selectionEnd) };
            },
            setOptions(o) { ta.wrap = o.wordWrap ? 'soft' : 'off'; },
            applyTheme() {},
            run(action) {
                ta.focus();
                if (action === 'editor.action.selectAll') ta.select();
                else if (action === 'undo' || action === 'redo') document.execCommand(action);
            },
            setGutter() {},
            setReview() {},
            reviewPath: () => null,
            showDiff(original, modified) {
                note.innerHTML = '';
                const pre = (label, text) => {
                    const box = document.createElement('div');
                    const h = document.createElement('h4');
                    h.textContent = label;
                    const p = document.createElement('pre');
                    p.textContent = text == null ? '' : text;
                    box.append(h, p);
                    return box;
                };
                note.append(pre('Original', original), pre('Modified', modified));
            },
            hideDiff() { note.innerHTML = ''; },
            setDiffSideBySide() {},
            registerCompletions() { return { dispose() {} }; },
        };
        api.detach();
        return api;
    }

    async function create(host) {
        const monaco = await loadMonaco(host.timeoutMs);
        if (monaco) {
            try { return createMonaco(monaco, host); } catch (err) { console.warn('[IDE] Monaco failed; using plain editor', err); }
        }
        return createTextarea(host);
    }

    root.CuttleIdeEditor = { create: create, loadMonaco: loadMonaco, MONACO_VERSION: MONACO_VERSION };
})(globalThis);
