/* IDE App — pure model (experimental flag `ide`, server owner api.ide).
 * No DOM, no fetch, no timers. The IDE page controllers keep their state in
 * these shapes and call these transitions; Node tests exercise them directly. */
(function (root) {
    'use strict';

    // ── paths ───────────────────────────────────────────────────────
    function normPath(p) {
        return String(p == null ? '' : p).replace(/\\/g, '/').replace(/\/+/g, '/').replace(/^\.\//, '').replace(/^\/+|\/+$/g, '');
    }
    function basename(p) {
        const parts = normPath(p).split('/');
        return parts[parts.length - 1] || '';
    }
    function dirname(p) {
        const parts = normPath(p).split('/');
        parts.pop();
        return parts.join('/');
    }
    function joinPath(a, b) {
        const left = normPath(a);
        const right = normPath(b);
        return left && right ? left + '/' + right : (left || right);
    }
    function extname(p) {
        const name = basename(p);
        const dot = name.lastIndexOf('.');
        return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
    }
    function isUnder(path, dir) {
        const p = normPath(path);
        const d = normPath(dir);
        return !d || p === d || p.startsWith(d + '/');
    }
    /** Path after renaming `from` to `to` (folder renames carry descendants). */
    function renamedPath(path, from, to) {
        const p = normPath(path);
        const f = normPath(from);
        if (p === f) return normPath(to);
        if (p.startsWith(f + '/')) return joinPath(to, p.slice(f.length + 1));
        return p;
    }
    /** Absolute paths → comparable keys (case-folded for Windows drives). */
    function absKey(p) {
        let s = String(p || '').replace(/\\/g, '/').replace(/\/+$/, '');
        if (/^[A-Za-z]:/.test(s)) s = s.toLowerCase();
        return s;
    }

    // ── languages (fallback when Monaco cannot tell) ────────────────
    const LANGS = {
        js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
        py: 'python', pyi: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java', kt: 'kotlin', kts: 'kotlin',
        c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', cs: 'csharp', swift: 'swift',
        php: 'php', lua: 'lua', r: 'r', scala: 'scala', dart: 'dart', sql: 'sql', sh: 'shell', bash: 'shell', zsh: 'shell',
        ps1: 'powershell', psm1: 'powershell', bat: 'bat', cmd: 'bat', html: 'html', htm: 'html', xml: 'xml', svg: 'xml',
        css: 'css', scss: 'scss', less: 'less', json: 'json', jsonc: 'json', md: 'markdown', markdown: 'markdown',
        yaml: 'yaml', yml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini', dockerfile: 'dockerfile',
        graphql: 'graphql', gql: 'graphql', vue: 'html', hcl: 'hcl', tf: 'hcl', ex: 'elixir', exs: 'elixir',
        clj: 'clojure', fs: 'fsharp', pl: 'perl', m: 'objective-c', txt: 'plaintext', log: 'plaintext',
    };
    const NAMED = { dockerfile: 'dockerfile', makefile: 'makefile', '.gitignore': 'ignore', '.env': 'ini' };
    function languageForPath(p) {
        const name = basename(p).toLowerCase();
        if (NAMED[name]) return NAMED[name];
        if (name.startsWith('dockerfile')) return 'dockerfile';
        return LANGS[extname(p)] || 'plaintext';
    }

    // ── fuzzy matching (quick open) ─────────────────────────────────
    function isBoundary(text, i) {
        if (i === 0) return true;
        const prev = text[i - 1];
        return prev === '/' || prev === '_' || prev === '-' || prev === '.' || prev === ' '
            || (text[i] !== text[i].toLowerCase() && prev === prev.toLowerCase());
    }
    /** Subsequence match; higher score is better. null when not a match. */
    function fuzzyScore(query, path) {
        const q = String(query || '').replace(/\s+/g, '').toLowerCase();
        const text = normPath(path);
        if (!q) return { score: 0, positions: [] };
        const lower = text.toLowerCase();
        const baseStart = text.lastIndexOf('/') + 1;
        // Prefer matching inside the file name: try it first, then the whole path.
        const attempt = (from) => {
            const positions = [];
            let qi = 0;
            for (let i = from; i < lower.length && qi < q.length; i++) {
                if (lower[i] === q[qi]) { positions.push(i); qi++; }
            }
            return qi === q.length ? positions : null;
        };
        const positions = attempt(baseStart) || attempt(0);
        if (!positions) return null;
        let score = 0;
        let prev = -2;
        positions.forEach((pos) => {
            score += 1;
            if (pos === prev + 1) score += 5;
            if (isBoundary(text, pos)) score += 4;
            if (pos >= baseStart) score += 3;
            prev = pos;
        });
        const base = lower.slice(baseStart);
        if (base === q) score += 40;
        else if (base.startsWith(q)) score += 20;
        score -= text.length * 0.02;
        return { score: score, positions: positions };
    }
    function fuzzyFilter(query, files, limit, recent) {
        const max = limit || 50;
        const recentRank = new Map((recent || []).map((p, i) => [normPath(p), i]));
        const out = [];
        (files || []).forEach((path) => {
            const m = fuzzyScore(query, path);
            if (!m) return;
            const r = recentRank.get(normPath(path));
            out.push({ path: path, score: m.score + (r == null ? 0 : 30 - Math.min(r, 29)), positions: m.positions });
        });
        out.sort((a, b) => b.score - a.score || a.path.length - b.path.length || (a.path < b.path ? -1 : 1));
        return out.slice(0, max);
    }
    /** `file:12:3`, `:12` (go to line) → parts. */
    function parseQuickOpen(input) {
        const text = String(input || '').trim();
        const m = text.match(/^(.*?)(?::(\d+))?(?::(\d+))?$/);
        const query = (m && m[1]) || '';
        return {
            query: query,
            line: m && m[2] ? parseInt(m[2], 10) : null,
            col: m && m[3] ? parseInt(m[3], 10) : null,
            gotoLineOnly: !query && !!(m && m[2]),
        };
    }

    // ── tabs (VS Code preview semantics) ────────────────────────────
    function emptyTabs() { return { tabs: [], active: null, mru: [] }; }
    function touchMru(mru, path) { return [path].concat((mru || []).filter((p) => p !== path)).slice(0, 50); }
    function findTab(state, path) { return (state.tabs || []).find((t) => t.path === path) || null; }
    /** Open (or focus) a tab. A preview tab is replaced by the next preview. */
    function openTab(state, path, opts) {
        const p = normPath(path);
        const preview = !!(opts && opts.preview);
        let tabs = (state.tabs || []).slice();
        const existing = tabs.find((t) => t.path === p);
        if (existing) {
            if (!preview && existing.preview) tabs = tabs.map((t) => t.path === p ? Object.assign({}, t, { preview: false }) : t);
        } else {
            const tab = { path: p, preview: preview };
            const previewIdx = preview ? tabs.findIndex((t) => t.preview) : -1;
            if (previewIdx >= 0) tabs[previewIdx] = tab;
            else {
                const activeIdx = tabs.findIndex((t) => t.path === state.active);
                tabs.splice(activeIdx >= 0 ? activeIdx + 1 : tabs.length, 0, tab);
            }
        }
        const open = new Set(tabs.map((t) => t.path));
        return { tabs: tabs, active: p, mru: touchMru(state.mru, p).filter((x) => open.has(x)) };
    }
    function pinTab(state, path) {
        return Object.assign({}, state, { tabs: (state.tabs || []).map((t) => t.path === path ? Object.assign({}, t, { preview: false }) : t) });
    }
    function activateTab(state, path) {
        if (!findTab(state, path)) return state;
        return Object.assign({}, state, { active: path, mru: touchMru(state.mru, path) });
    }
    function closeTabs(state, paths) {
        const drop = new Set(paths);
        const tabs = (state.tabs || []).filter((t) => !drop.has(t.path));
        const mru = (state.mru || []).filter((p) => !drop.has(p));
        let active = state.active;
        if (drop.has(active)) {
            const next = mru.find((p) => tabs.some((t) => t.path === p));
            if (next) active = next;
            else {
                const oldIdx = (state.tabs || []).findIndex((t) => t.path === state.active);
                active = tabs.length ? tabs[Math.min(Math.max(oldIdx, 0), tabs.length - 1)].path : null;
            }
        }
        return { tabs: tabs, active: active, mru: mru };
    }
    function closeTab(state, path) { return closeTabs(state, [path]); }
    function closeOthers(state, path) { return closeTabs(state, (state.tabs || []).map((t) => t.path).filter((p) => p !== path)); }
    function moveTab(state, path, toIndex) {
        const tabs = (state.tabs || []).slice();
        const from = tabs.findIndex((t) => t.path === path);
        if (from < 0) return state;
        const [tab] = tabs.splice(from, 1);
        tabs.splice(Math.max(0, Math.min(toIndex, tabs.length)), 0, tab);
        return Object.assign({}, state, { tabs: tabs });
    }
    function renameInTabs(state, from, to) {
        const map = (p) => renamedPath(p, from, to);
        return {
            tabs: (state.tabs || []).map((t) => Object.assign({}, t, { path: map(t.path) })),
            active: state.active ? map(state.active) : state.active,
            mru: (state.mru || []).map(map),
        };
    }
    function tabsUnder(state, dir) { return (state.tabs || []).map((t) => t.path).filter((p) => isUnder(p, dir)); }
    function nextTab(state, delta) {
        const tabs = state.tabs || [];
        if (!tabs.length) return state;
        const idx = tabs.findIndex((t) => t.path === state.active);
        const next = tabs[(Math.max(idx, 0) + delta + tabs.length) % tabs.length].path;
        return activateTab(state, next);
    }
    /** Tab label, disambiguated with the parent folder when names collide. */
    function tabLabels(state) {
        const counts = {};
        (state.tabs || []).forEach((t) => { const b = basename(t.path); counts[b] = (counts[b] || 0) + 1; });
        const out = {};
        (state.tabs || []).forEach((t) => {
            const b = basename(t.path);
            out[t.path] = { name: b, hint: counts[b] > 1 ? (basename(dirname(t.path)) || '/') : '' };
        });
        return out;
    }

    // ── line diff (quick-diff gutter + agent change review) ─────────
    function splitLines(text) { return String(text == null ? '' : text).split(/\r\n|\r|\n/); }
    /**
     * Myers O(ND) line diff → gutter ranges on the NEW text:
     *   {type:'added'|'modified', start, end} (1-based inclusive) and
     *   {type:'deleted', start:n, end:n} meaning lines removed after line n.
     * Returns null when the inputs are too large/different to diff cheaply.
     */
    function lineDiff(oldText, newText, opts) {
        // An empty file has no lines (so new content reads as added, not modified).
        const a = oldText === '' ? [] : splitLines(oldText);
        const b = newText === '' ? [] : splitLines(newText);
        const maxLines = (opts && opts.maxLines) || 20000;
        const maxD = (opts && opts.maxEdits) || 4000;
        if (a.length > maxLines || b.length > maxLines) return null;
        let start = 0;
        while (start < a.length && start < b.length && a[start] === b[start]) start++;
        let endA = a.length;
        let endB = b.length;
        while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
        const A = a.slice(start, endA);
        const B = b.slice(start, endB);
        const N = A.length;
        const M = B.length;
        // Forward edit script: 'e' equal, 'd' delete from A, 'i' insert from B.
        const ops = [];
        if (N === 0 || M === 0) {
            for (let i = 0; i < N; i++) ops.push('d');
            for (let j = 0; j < M; j++) ops.push('i');
        } else {
            const max = N + M;
            const offset = max;
            const v = new Int32Array(2 * max + 2);
            const trace = [];
            let found = false;
            for (let d = 0; d <= max && d <= maxD && !found; d++) {
                trace.push(v.slice());
                for (let k = -d; k <= d; k += 2) {
                    let x;
                    if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) x = v[offset + k + 1];
                    else x = v[offset + k - 1] + 1;
                    let y = x - k;
                    while (x < N && y < M && A[x] === B[y]) { x++; y++; }
                    v[offset + k] = x;
                    if (x >= N && y >= M) { found = true; break; }
                }
            }
            if (!found) return null;
            let x = N;
            let y = M;
            for (let dd = trace.length - 1; dd > 0; dd--) {
                const vv = trace[dd];
                const k = x - y;
                const prevK = (k === -dd || (k !== dd && vv[offset + k - 1] < vv[offset + k + 1])) ? k + 1 : k - 1;
                const prevX = vv[offset + prevK];
                const prevY = prevX - prevK;
                while (x > prevX && y > prevY) { x--; y--; ops.push('e'); }
                ops.push(x === prevX ? 'i' : 'd');
                x = prevX;
                y = prevY;
            }
            while (x > 0 && y > 0) { x--; y--; ops.push('e'); }
            ops.reverse();
        }
        const ranges = [];
        let bPos = 0;
        let k = 0;
        while (k < ops.length) {
            if (ops[k] === 'e') { bPos++; k++; continue; }
            let dels = 0;
            let ins = 0;
            const startB = bPos;
            while (k < ops.length && ops[k] !== 'e') {
                if (ops[k] === 'd') dels++;
                else { ins++; bPos++; }
                k++;
            }
            const first = start + startB + 1;
            if (ins && dels) ranges.push({ type: 'modified', start: first, end: first + ins - 1 });
            else if (ins) ranges.push({ type: 'added', start: first, end: first + ins - 1 });
            else ranges.push({ type: 'deleted', start: start + startB, end: start + startB });
        }
        return ranges;
    }

    // ── search previews ─────────────────────────────────────────────
    function previewParts(match) {
        const text = String((match && match.preview) || '');
        const at = Math.max(0, (Number(match && match.col) || 1) - 1 - (Number(match && match.preview_offset) || 0));
        const len = Math.max(0, Number(match && match.length) || 0);
        const lead = text.slice(0, at);
        return {
            before: (match && match.preview_offset ? '…' : '') + lead.replace(/^\s+/, ''),
            match: text.slice(at, at + len),
            after: text.slice(at + len),
        };
    }

    // ── git decorations ─────────────────────────────────────────────
    function statusLetter(raw) {
        const s = String(raw || 'modified').toLowerCase();
        if (s === 'untracked' || s === 'u' || s === '??') return 'U';
        if (s === 'added' || s === 'a') return 'A';
        if (s === 'deleted' || s === 'd') return 'D';
        if (s.startsWith('r')) return 'R';
        if (s === 'conflict' || s === '!') return '!';
        return 'M';
    }
    /**
     * Pending-changes payload (`/api/git/pending-changes`) → workspace-relative
     * {files: {path: letter}, dirs: {path: true}} for explorer/tab decorations.
     */
    function decorationsFromPending(payload, workspaceRoot) {
        const rootKey = absKey(workspaceRoot);
        const files = {};
        const dirs = {};
        (payload && payload.repos || []).forEach((repo) => {
            const repoKey = absKey(repo.repo_root || workspaceRoot);
            (repo.files || []).forEach((f) => {
                const abs = repoKey + '/' + normPath(f.path);
                let rel = null;
                if (absKey(abs).startsWith(rootKey + '/')) rel = abs.slice(rootKey.length + 1);
                if (!rel) return;
                files[rel] = statusLetter(f.status);
                let d = dirname(rel);
                while (d) { dirs[d] = true; d = dirname(d); }
            });
        });
        return { files: files, dirs: dirs };
    }
    /** A workspace-relative path → {repoRoot, file} for the pending-diff APIs. */
    function repoRelative(payload, workspaceRoot, rel) {
        const abs = absKey(workspaceRoot) + '/' + normPath(rel);
        let best = null;
        (payload && payload.repos || []).forEach((repo) => {
            const key = absKey(repo.repo_root);
            if (key && absKey(abs).startsWith(key + '/') && (!best || key.length > absKey(best.repo_root).length)) best = repo;
        });
        if (!best) return null;
        return { repoRoot: best.repo_root, file: abs.slice(absKey(best.repo_root).length + 1) };
    }

    // ── AI chat prompts ─────────────────────────────────────────────
    function fence(code) {
        const text = String(code || '');
        let ticks = '```';
        while (text.indexOf(ticks) !== -1) ticks += '`';
        return ticks;
    }
    function lineRange(sel) {
        if (!sel || !sel.startLine) return '';
        return sel.endLine && sel.endLine !== sel.startLine ? 'lines ' + sel.startLine + '–' + sel.endLine : 'line ' + sel.startLine;
    }
    /** Context block for "Add to chat" (Ctrl+L). */
    function selectionContext(sel) {
        const s = sel || {};
        const where = '`' + normPath(s.path) + '`' + (lineRange(s) ? ' (' + lineRange(s) + ')' : '');
        if (!s.text) return where;
        const f = fence(s.text);
        return where + ':\n' + f + (s.language && s.language !== 'plaintext' ? s.language : '') + '\n' + s.text.replace(/\n$/, '') + '\n' + f;
    }
    /** Inline-edit request (Ctrl+K): the agent edits the file on disk. */
    function editPrompt(sel, instruction) {
        const s = sel || {};
        const where = s.text ? 'In ' + selectionContext(s) : 'In `' + normPath(s.path) + '`' + (s.startLine ? ' at line ' + s.startLine : '');
        return where + '\n\n' + String(instruction || '').trim()
            + '\n\nEdit the file in place; keep the change scoped to this ' + (s.text ? 'selection' : 'spot') + '.';
    }

    // ── persisted UI state ──────────────────────────────────────────
    const VIEWS = ['explorer', 'search', 'scm'];
    function clampNum(v, lo, hi, dflt) {
        const n = Number(v);
        return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
    }
    function normalizeLayout(raw) {
        const r = raw && typeof raw === 'object' ? raw : {};
        return {
            view: VIEWS.indexOf(r.view) >= 0 ? r.view : 'explorer',
            sidebar: r.sidebar !== false,
            sidebarWidth: clampNum(r.sidebarWidth, 160, 640, 260),
            panel: !!r.panel,
            panelHeight: clampNum(r.panelHeight, 100, 900, 240),
            chat: !!r.chat,
            chatWidth: clampNum(r.chatWidth, 280, 900, 420),
            completions: !!r.completions,
            minimap: r.minimap !== false,
            wordWrap: !!r.wordWrap,
        };
    }
    function normalizeWorkspaceState(raw) {
        const r = raw && typeof raw === 'object' ? raw : {};
        const tabs = (Array.isArray(r.tabs) ? r.tabs : []).filter((t) => t && typeof t.path === 'string' && normPath(t.path) && normPath(t.path).split('/').indexOf('..') < 0)
            .slice(0, 40).map((t) => ({ path: normPath(t.path), preview: !!t.preview }));
        const paths = tabs.map((t) => t.path);
        return {
            tabs: tabs,
            active: paths.indexOf(r.active) >= 0 ? r.active : (paths[0] || null),
            mru: (Array.isArray(r.mru) ? r.mru : []).filter((p) => paths.indexOf(p) >= 0),
            expanded: (Array.isArray(r.expanded) ? r.expanded : []).filter((p) => typeof p === 'string').slice(0, 200),
            recent: (Array.isArray(r.recent) ? r.recent : []).filter((p) => typeof p === 'string').slice(0, 30),
            chatSession: r.chatSession != null && /^\d+$/.test(String(r.chatSession)) ? String(r.chatSession) : null,
        };
    }
    function pushRecent(list, path) { return [normPath(path)].concat((list || []).filter((p) => p !== normPath(path))).slice(0, 30); }

    function formatBytes(n) {
        const v = Number(n) || 0;
        if (v < 1024) return v + ' B';
        if (v < 1024 * 1024) return (v / 1024).toFixed(1) + ' KB';
        return (v / 1024 / 1024).toFixed(1) + ' MB';
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    /** Fuzzy positions → highlighted HTML (escaped). */
    function highlightHtml(text, positions) {
        const set = new Set(positions || []);
        let out = '';
        let open = false;
        for (let i = 0; i < text.length; i++) {
            const hit = set.has(i);
            if (hit && !open) { out += '<b>'; open = true; }
            if (!hit && open) { out += '</b>'; open = false; }
            out += escapeHtml(text[i]);
        }
        return out + (open ? '</b>' : '');
    }
    /** Can the diff modal offer "Editor" for this pending file? */
    function canEditFromDiff(data) {
        if (!data) return false;
        if (data.binary || data.secret_redacted) return false;
        return statusLetter(data.status) !== 'D';
    }

    const api = {
        normPath, basename, dirname, joinPath, extname, isUnder, renamedPath, absKey,
        languageForPath, fuzzyScore, fuzzyFilter, parseQuickOpen,
        emptyTabs, findTab, openTab, pinTab, activateTab, closeTab, closeTabs, closeOthers, moveTab,
        renameInTabs, tabsUnder, nextTab, tabLabels,
        splitLines, lineDiff, previewParts, statusLetter, decorationsFromPending, repoRelative,
        selectionContext, editPrompt, normalizeLayout, normalizeWorkspaceState, pushRecent,
        formatBytes, escapeHtml, highlightHtml, canEditFromDiff, VIEWS,
    };
    root.CuttleIdeModel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
