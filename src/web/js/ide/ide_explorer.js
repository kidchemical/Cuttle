/* IDE App — explorer tree. Owns the lazily loaded directory cache, expansion
 * state, selection, drag-to-move and keyboard navigation for one workspace;
 * file operations and opening go back to the page through `host`. */
(function (root) {
    'use strict';

    const EXT_TONES = {
        js: '#e8c547', mjs: '#e8c547', cjs: '#e8c547', jsx: '#61dafb', ts: '#3d8fd6', tsx: '#61dafb', py: '#4b8bbe',
        json: '#cbcb41', md: '#6ca0dc', html: '#e44d26', css: '#2965f1', scss: '#cd6799', go: '#00add8', rs: '#dea584',
        java: '#b07219', kt: '#a97bff', rb: '#cc342d', php: '#777bb3', sh: '#89e051', yml: '#cb171e', yaml: '#cb171e',
        toml: '#9c4221', c: '#a8b9cc', h: '#a8b9cc', cpp: '#f34b7d', cs: '#178600', svg: '#ffb13b', png: '#a074c4',
        jpg: '#a074c4', gif: '#a074c4', lock: '#888', txt: '#999', sql: '#e38c00', vue: '#41b883', swift: '#f05138',
    };

    function create(host) {
        const el = host.el;
        const M = host.model;
        const esc = M.escapeHtml;
        const children = new Map(); // dir → entries
        const expanded = new Set();
        const loading = new Set();
        let rootPath = null;
        let selected = null;
        let gen = 0;
        let rows = [];

        async function load(dir) {
            if (!rootPath || loading.has(dir)) return;
            loading.add(dir);
            const g = gen;
            try {
                const data = await host.api.tree(rootPath, dir);
                if (g !== gen) return;
                children.set(dir, data.entries || []);
            } catch (err) {
                if (g !== gen) return;
                if (err && err.status === 404) { children.delete(dir); expanded.delete(dir); }
                else host.onError(err);
            } finally {
                loading.delete(dir);
            }
        }
        function flatten() {
            const out = [];
            const walk = (dir, depth) => {
                (children.get(dir) || []).forEach((entry) => {
                    out.push({ entry: entry, depth: depth });
                    if (entry.type === 'dir' && expanded.has(entry.path)) {
                        if (children.has(entry.path)) walk(entry.path, depth + 1);
                        else out.push({ loading: true, depth: depth + 1, path: entry.path + '/…' });
                    }
                });
            };
            walk('', 0);
            return out;
        }
        function iconHtml(entry) {
            if (entry.type === 'dir') {
                return '<span class="ide-tree-chevron" aria-hidden="true"></span><span class="ide-tree-icon is-folder" aria-hidden="true"></span>';
            }
            const ext = M.extname(entry.name);
            const tone = EXT_TONES[ext] || 'var(--text-tertiary)';
            const label = (ext || entry.name.replace(/^\./, '')).slice(0, 2) || '·';
            return '<span class="ide-tree-chevron is-none" aria-hidden="true"></span><span class="ide-tree-icon" style="--tone:' + tone + '" aria-hidden="true">' + esc(label) + '</span>';
        }
        function render() {
            const decorations = host.getDecorations();
            const marks = host.getReviewMarks ? host.getReviewMarks() : { files: {}, dirs: {} };
            const active = host.getActive();
            rows = flatten();
            if (!rows.length) {
                el.innerHTML = '<div class="ide-tree-empty">' + (children.has('') ? 'This folder is empty.' : 'Loading…') + '</div>';
                return;
            }
            el.innerHTML = rows.map((r, i) => {
                if (r.loading) return '<div class="ide-tree-row is-loading" style="--depth:' + r.depth + '">Loading…</div>';
                const e = r.entry;
                const letter = e.type === 'dir' ? (decorations.dirs[e.path] ? '•' : '') : (decorations.files[e.path] || '');
                const reviewed = e.type === 'dir' ? (marks.dirs[e.path] || 0) : ((marks.files[e.path] || {}).hunks || 0);
                const cls = ['ide-tree-row', 'is-' + e.type];
                if (e.type === 'dir' && expanded.has(e.path)) cls.push('is-open');
                if (e.ignored) cls.push('is-ignored');
                if (e.path === selected) cls.push('is-selected');
                if (e.path === active) cls.push('is-active');
                if (letter) cls.push('git-' + (letter === '•' ? 'dir' : letter.toLowerCase().replace('!', 'conflict')));
                return '<div class="' + cls.join(' ') + '" role="treeitem" tabindex="-1" draggable="true" data-row="' + i + '" data-path="' + esc(e.path) + '" style="--depth:' + r.depth + '"'
                    + (e.type === 'dir' ? ' aria-expanded="' + expanded.has(e.path) + '"' : '') + ' title="' + esc(e.path) + '">'
                    + iconHtml(e) + '<span class="ide-tree-name">' + esc(e.name) + (e.symlink ? ' <span class="ide-tree-link">↗</span>' : '') + '</span>'
                    + (reviewed ? '<span class="ide-tree-review" title="' + reviewed + ' agent change' + (reviewed === 1 ? '' : 's') + ' to review">✦</span>' : '')
                    + (letter ? '<span class="ide-tree-git">' + esc(letter) + '</span>' : '') + '</div>';
            }).join('');
        }
        function rowAt(target) {
            const row = target.closest('.ide-tree-row[data-row]');
            return row ? rows[Number(row.dataset.row)] : null;
        }
        async function toggle(dir, open) {
            const want = open == null ? !expanded.has(dir) : open;
            if (want) {
                expanded.add(dir);
                render();
                if (!children.has(dir)) { await load(dir); render(); }
            } else {
                expanded.delete(dir);
                render();
            }
            host.onExpandedChange();
        }
        function select(path) {
            selected = path;
            render();
            const row = el.querySelector('.ide-tree-row.is-selected');
            if (row) row.scrollIntoView({ block: 'nearest' });
        }

        // Rows re-render on every click, so a native dblclick can lose its
        // target; a second click on the same file within the window pins it.
        let lastClick = { path: null, at: 0 };
        el.addEventListener('click', (e) => {
            const r = rowAt(e.target);
            if (!r || !r.entry) return;
            selected = r.entry.path;
            const now = Date.now();
            const second = lastClick.path === r.entry.path && now - lastClick.at < 450;
            lastClick = { path: second ? null : r.entry.path, at: now };
            if (r.entry.type === 'dir') { if (!second) toggle(r.entry.path); return; }
            render();
            host.onOpen(r.entry.path, { preview: !second });
        });
        el.addEventListener('contextmenu', (e) => {
            const r = rowAt(e.target);
            e.preventDefault();
            if (r && r.entry) select(r.entry.path);
            host.onContext(r && r.entry ? r.entry : { path: '', type: 'dir', name: '' }, e);
        });
        el.addEventListener('keydown', (e) => {
            const idx = rows.findIndex((r) => r.entry && r.entry.path === selected);
            const cur = idx >= 0 ? rows[idx].entry : null;
            const move = (d) => {
                let i = idx;
                do { i += d; } while (rows[i] && !rows[i].entry);
                if (rows[i]) select(rows[i].entry.path);
            };
            if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
            else if (e.key === 'ArrowRight' && cur && cur.type === 'dir') { e.preventDefault(); toggle(cur.path, true); }
            else if (e.key === 'ArrowLeft' && cur) {
                e.preventDefault();
                if (cur.type === 'dir' && expanded.has(cur.path)) toggle(cur.path, false);
                else if (M.dirname(cur.path)) select(M.dirname(cur.path));
            } else if (e.key === 'Enter' && cur) {
                e.preventDefault();
                if (cur.type === 'dir') toggle(cur.path); else host.onOpen(cur.path, { preview: false });
            } else if (e.key === 'F2' && cur) { e.preventDefault(); host.onRename(cur); }
            else if (e.key === 'Delete' && cur) { e.preventDefault(); host.onDelete(cur); }
        });
        // Drag a row onto a folder (or the empty area for the root) to move it.
        el.addEventListener('dragstart', (e) => {
            const r = rowAt(e.target);
            if (!r || !r.entry) return;
            e.dataTransfer.setData('application/x-cuttle-ide-path', r.entry.path);
            e.dataTransfer.effectAllowed = 'move';
        });
        const dropDir = (target) => {
            const r = rowAt(target);
            if (!r || !r.entry) return '';
            return r.entry.type === 'dir' ? r.entry.path : M.dirname(r.entry.path);
        };
        el.addEventListener('dragover', (e) => {
            if (!Array.from(e.dataTransfer.types || []).includes('application/x-cuttle-ide-path')) return;
            e.preventDefault();
            el.querySelectorAll('.is-drop').forEach((n) => n.classList.remove('is-drop'));
            const row = e.target.closest('.ide-tree-row');
            if (row) row.classList.add('is-drop');
        });
        el.addEventListener('dragleave', () => el.querySelectorAll('.is-drop').forEach((n) => n.classList.remove('is-drop')));
        el.addEventListener('drop', (e) => {
            const from = e.dataTransfer.getData('application/x-cuttle-ide-path');
            el.querySelectorAll('.is-drop').forEach((n) => n.classList.remove('is-drop'));
            if (!from) return;
            e.preventDefault();
            const dir = dropDir(e.target);
            const to = M.joinPath(dir, M.basename(from));
            if (to !== from && !M.isUnder(dir, from)) host.onMove(from, to);
        });

        return {
            async reset(path, expandedList) {
                gen++;
                rootPath = path;
                children.clear();
                expanded.clear();
                selected = null;
                (expandedList || []).forEach((p) => expanded.add(p));
                render();
                await load('');
                // Restore expansion breadth-first so parents load before children.
                const dirs = Array.from(expanded).sort((a, b) => a.split('/').length - b.split('/').length);
                for (const dir of dirs) {
                    if (!expanded.has(M.dirname(dir)) && M.dirname(dir)) { expanded.delete(dir); continue; }
                    await load(dir);
                }
                render();
            },
            /** Reload every loaded folder (or just `dirs`) and repaint. */
            async refresh(dirs) {
                const targets = dirs ? dirs.filter((d) => d === '' || children.has(d)) : Array.from(children.keys());
                await Promise.all(targets.map((d) => load(d)));
                render();
            },
            render: render,
            toggle: toggle,
            async reveal(path) {
                const parts = M.normPath(path).split('/');
                parts.pop();
                let dir = '';
                for (const part of parts) {
                    dir = M.joinPath(dir, part);
                    expanded.add(dir);
                    if (!children.has(dir)) await load(dir);
                }
                host.onExpandedChange();
                select(path);
            },
            collapseAll() { expanded.clear(); render(); host.onExpandedChange(); },
            expandedList: () => Array.from(expanded),
            selected: () => {
                const r = rows.find((x) => x.entry && x.entry.path === selected);
                return r ? r.entry : null;
            },
            entryType(path) {
                const dir = M.dirname(path);
                const e = (children.get(dir) || []).find((x) => x.path === path);
                return e ? e.type : null;
            },
            focus() { el.focus(); },
        };
    }

    root.CuttleIdeExplorer = { create: create };
})(globalThis);
