/* Cuttle Git visualizer — vertical commit topology */
(function () {
    'use strict';

    const STORAGE_PROJECT = 'cuttleGitGraphProject';
    const STORAGE_BRANCH = 'cuttleGitGraphBranch';
    const STORAGE_REPO_PREFIX = 'cuttleGitGraphRepo:';
    const ROW_H = 52;
    const LANE_W = 11;
    const GUTTER_PAD = 14;
    /** Gap from node center to the start of commit text */
    const TEXT_AFTER_NODE = 10;
    const PAGE_SIZE = 20;
    const COLORS = [
        '#38bdf8', '#a78bfa', '#34d399', '#fbbf24', '#f472b6',
        '#22d3ee', '#c084fc', '#4ade80', '#fb923c', '#e879f9',
    ];

    const els = {
        project: document.getElementById('gitGraphProject'),
        repo: document.getElementById('gitGraphRepo'),
        repoField: document.getElementById('gitGraphRepoField'),
        filter: document.getElementById('gitGraphFilter'),
        refresh: document.getElementById('gitGraphRefresh'),
        status: document.getElementById('gitGraphStatus'),
        scroll: document.getElementById('gitGraphScroll'),
        svg: document.getElementById('gitGraphSvg'),
        rows: document.getElementById('gitGraphRows'),
        layout: document.getElementById('gitGraphLayout'),
        drawer: document.getElementById('gitGraphDrawer'),
        drawerClose: document.getElementById('gitGraphDrawerClose'),
        detailTitle: document.getElementById('gitGraphDetailTitle'),
        detailBody: document.getElementById('gitGraphDetailBody'),
        subtitle: document.getElementById('gitGraphSubtitle'),
        branches: document.getElementById('gitGraphBranches'),
        branchesList: document.getElementById('gitGraphBranchesList'),
        laneTip: document.getElementById('gitGraphLaneTip'),
    };

    let projects = [];
    let projectRepos = [];
    let commits = [];
    let branches = [];
    let layoutRows = [];
    let selectedHash = null;
    /** null = current HEAD history; '__all__' = all refs; else branch name */
    let branchMode = null;
    let filterTimer = null;
    let paintRaf = 0;
    let graphAbort = null;
    let graphGutter = 120;
    let hashToRow = new Map();
    /** commit hash → owning local branch name (tip inheritance down first-parent) */
    let commitBranch = new Map();
    /** branch name → stroke/fill color */
    let branchColorMap = new Map();
    let hoveredBranch = null;
    /** HEAD branch from last graph load (for tip preference) */
    let graphHeadBranch = '';
    let graphHasMore = false;
    let graphLoadingMore = false;
    let pendingChangesCtl = null;

    function loadBranchMode() {
        try {
            const v = localStorage.getItem(STORAGE_BRANCH);
            if (v === '__all__') return '__all__';
            if (v) return v;
        } catch (_) {}
        return null;
    }

    function saveBranchMode(mode) {
        try {
            if (mode == null) localStorage.removeItem(STORAGE_BRANCH);
            else localStorage.setItem(STORAGE_BRANCH, mode);
        } catch (_) {}
    }

    branchMode = loadBranchMode();
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function setStatus(msg, isError) {
        if (!els.status) return;
        els.status.hidden = !msg;
        els.status.textContent = msg || '';
        els.status.classList.toggle('is-error', !!isError);
        if (els.scroll) els.scroll.hidden = !!msg;
    }

    function currentProjectPath() {
        return (els.project && els.project.value) || '';
    }

    function currentRepoRoot() {
        if (els.repo && els.repo.value) return String(els.repo.value);
        if (projectRepos.length === 1) return String(projectRepos[0].repo_root || '');
        return '';
    }

    const commitViewer = (window.CuttleGitCommitViewer && window.CuttleGitCommitViewer.create)
        ? window.CuttleGitCommitViewer.create({
            getProjectPath: currentProjectPath,
            getRepoRoot: currentRepoRoot,
            getPendingFiles: (root) => pendingChangesCtl ? pendingChangesCtl.getFilesForRepo(root) : [],
            els: {
                detailTitle: els.detailTitle,
                detailBody: els.detailBody,
            },
        })
        : null;
    if (commitViewer) commitViewer.bind();

    function repoStorageKey(projectPath) {
        return STORAGE_REPO_PREFIX + String(projectPath || '').replace(/\\/g, '/').toLowerCase();
    }

    function loadSavedRepoRoot(projectPath) {
        try {
            return localStorage.getItem(repoStorageKey(projectPath)) || '';
        } catch (_) {
            return '';
        }
    }

    function saveRepoRoot(projectPath, repoRoot) {
        try {
            const key = repoStorageKey(projectPath);
            if (repoRoot) localStorage.setItem(key, String(repoRoot));
            else localStorage.removeItem(key);
        } catch (_) {}
    }

    function applyRepoOptions(repos, projectPath) {
        projectRepos = Array.isArray(repos) ? repos.slice() : [];
        if (!els.repo || !els.repoField) return;
        if (projectRepos.length <= 1) {
            els.repoField.hidden = true;
            els.repo.innerHTML = projectRepos.length
                ? ('<option value="' + esc(projectRepos[0].repo_root || '') + '">'
                    + esc(projectRepos[0].label || projectRepos[0].repo_root || 'Repo')
                    + '</option>')
                : '';
            if (projectRepos.length === 1) {
                els.repo.value = projectRepos[0].repo_root || '';
            }
            return;
        }
        els.repoField.hidden = false;
        const saved = loadSavedRepoRoot(projectPath);
        els.repo.innerHTML = projectRepos.map((r) => {
            const root = r.repo_root || '';
            const label = r.label || root;
            const br = r.branch ? (' (' + r.branch + ')') : '';
            return '<option value="' + esc(root) + '">' + esc(label + br) + '</option>';
        }).join('');
        const match = projectRepos.find((r) => String(r.repo_root || '') === saved);
        els.repo.value = match
            ? String(match.repo_root)
            : String(projectRepos[0].repo_root || '');
        saveRepoRoot(projectPath, els.repo.value);
    }

    async function loadReposForProject(projectPath) {
        const path = String(projectPath || '').trim();
        if (!path) {
            applyRepoOptions([], '');
            return [];
        }
        try {
            const qs = new URLSearchParams({ path });
            const resp = await fetch('/api/git/repos?' + qs.toString(), {
                credentials: 'include',
                cache: 'no-store',
            });
            const data = await resp.json().catch(() => null);
            const repos = (data && data.success && Array.isArray(data.repos))
                ? data.repos
                : [];
            applyRepoOptions(repos, path);
            return repos;
        } catch (_) {
            applyRepoOptions([], path);
            return [];
        }
    }

    async function loadProjects() {
        const resp = await fetch('/api/projects', { credentials: 'include' });
        const data = await resp.json().catch(() => null);
        if (!data || !data.success) {
            throw new Error((data && data.error) || 'Failed to load projects');
        }
        projects = Array.isArray(data.projects) ? data.projects : (data.data || []);
        if (!Array.isArray(projects)) projects = [];

        let saved = '';
        try { saved = localStorage.getItem(STORAGE_PROJECT) || ''; } catch (_) {}

        els.project.innerHTML = projects.map((p) => {
            const path = p.path || '';
            const name = p.name || path || ('Project ' + p.id);
            return '<option value="' + esc(path) + '" data-name="' + esc(name) + '">' + esc(name) + '</option>';
        }).join('');

        if (!projects.length) {
            setStatus('No projects registered. Add one in Settings / Control Panel.', true);
            return;
        }

        const match = projects.find((p) => p.path === saved);
        if (match) els.project.value = match.path;
        else els.project.value = projects[0].path;
    }

    /**
     * Classic newest-first lane assignment.
     * Returns [{ commit, lane, edges: [{fromLane,toLane,merge}] }]
     */
    function assignLanes(list) {
        const reserved = []; // lane -> hash expected next (or null)
        const out = [];

        function firstFree() {
            const i = reserved.findIndex((h) => h == null);
            return i >= 0 ? i : reserved.length;
        }

        for (const c of list) {
            let lane = reserved.indexOf(c.hash);
            if (lane < 0) {
                lane = firstFree();
                while (reserved.length <= lane) reserved.push(null);
            }

            const parents = Array.isArray(c.parents) ? c.parents.filter(Boolean) : [];
            const edges = [];

            // First parent continues on this lane when possible.
            const nextReserved = reserved.slice();
            nextReserved[lane] = null;

            parents.forEach((ph, pi) => {
                let toLane;
                if (pi === 0) {
                    toLane = lane;
                    nextReserved[toLane] = ph;
                } else {
                    const existing = nextReserved.indexOf(ph);
                    if (existing >= 0) {
                        toLane = existing;
                    } else {
                        toLane = nextReserved.findIndex((h) => h == null);
                        if (toLane < 0) toLane = nextReserved.length;
                        while (nextReserved.length <= toLane) nextReserved.push(null);
                        nextReserved[toLane] = ph;
                    }
                }
                edges.push({ fromLane: lane, toLane, merge: pi > 0 });
            });

            // Carry forward other active lines
            for (let i = 0; i < reserved.length; i++) {
                if (i === lane) continue;
                if (reserved[i] && reserved[i] !== c.hash) {
                    if (nextReserved[i] == null && !parents.includes(reserved[i])) {
                        nextReserved[i] = reserved[i];
                    }
                }
            }

            // Trim trailing nulls
            while (nextReserved.length && nextReserved[nextReserved.length - 1] == null) {
                nextReserved.pop();
            }
            reserved.length = 0;
            Array.prototype.push.apply(reserved, nextReserved);

            out.push({
                commit: c,
                lane,
                edges,
                activeCount: Math.max(reserved.length, lane + 1),
            });
        }
        return out;
    }

    function laneColor(i) {
        return COLORS[i % COLORS.length];
    }

    function colorForBranch(name) {
        if (!name) return 'rgba(148, 163, 184, 0.85)';
        if (branchColorMap.has(name)) return branchColorMap.get(name);
        return laneColor(branchColorMap.size);
    }

    function isSyntheticBranch(name) {
        return !!name && String(name).indexOf('__side__') === 0;
    }

    function displayBranchName(name) {
        if (!name) return '';
        if (isSyntheticBranch(name)) return 'unnamed';
        return name;
    }

    /**
     * Prefer a local branch tip on this commit (skip HEAD / prefer non-remote).
     * e.g. HEAD + dev/core + origin/dev/core → "dev/core"
     */
    function pickCommitTip(refs) {
        let best = '';
        let bestScore = -1;
        const head = String(graphHeadBranch || '').toLowerCase();
        (refs || []).forEach((lab) => {
            const s = String(lab || '');
            if (!s || s === 'HEAD') return;
            const lower = s.toLowerCase();
            let sc = -1;
            if (lower.startsWith('tag:')) sc = 1;
            else if (lower.startsWith('origin/') || lower.startsWith('remotes/')) sc = 3;
            else if (lower === 'main' || lower === 'master') sc = 9;
            else sc = 8;
            if (head && lower === head) sc += 5;
            if (sc > bestScore) {
                bestScore = sc;
                best = s;
            }
        });
        return best;
    }

    function sideBranchId(hash) {
        return '__side__' + String(hash || '').slice(0, 7);
    }

    /**
     * Recover a deleted branch name from a merge commit subject.
     * Default git:  Merge branch 'feature/castle'
     * Also:         Merge remote-tracking branch 'origin/feature/castle'
     *               Merge pull request #12 from org/feature/castle
     */
    function parseMergedBranchName(subject) {
        const s = String(subject || '').trim();
        if (!s) return '';
        const patterns = [
            // git default / GitHub
            /Merge(?:\s+remote-tracking)?\s+branch\s+['"]([^'"]+)['"]/i,
            /Merge(?:\s+remote-tracking)?\s+branch\s+(\S+?)(?:\s+into\s+\S+)?$/i,
            // Gitea: Merge pull request 'Title' (#91) from feature/castle into master
            // GitHub: Merge pull request #91 from org/feature/castle
            /Merge pull request\b[\s\S]*?\bfrom\s+(\S+?)(?:\s+into\b|$)/i,
            /Merged?\s+['"]([^'"]+)['"]\s+into\s+/i,
        ];
        for (let i = 0; i < patterns.length; i++) {
            const m = s.match(patterns[i]);
            if (!m || !m[1]) continue;
            let name = m[1].replace(/,/g, '').replace(/^refs\/heads\//, '');
            if (name.toLowerCase().startsWith('origin/')) name = name.slice(7);
            if (name.toLowerCase().startsWith('remotes/origin/')) name = name.slice(15);
            if (!name || name === 'HEAD') continue;
            return name;
        }
        return '';
    }

    /**
     * Newest-first ownership:
     * - Tip refs claim a commit; first-parent ancestors inherit that tip.
     * - Merge 2nd+ parents start a distinct side identity. Prefer the branch
     *   name from the merge commit message (still present after the tip ref
     *   is deleted); otherwise a synthetic id → tooltip "unnamed".
     */
    function assignBranchOwnership(rows) {
        const labels = new Map();
        const inherit = new Map();
        const tipOrder = [];

        function rememberColorKey(name) {
            if (!name || tipOrder.includes(name)) return;
            tipOrder.push(name);
        }

        rows.forEach((r) => {
            const hash = r.commit.hash;
            const tip = pickCommitTip(r.commit.refs);
            let name = tip || inherit.get(hash) || '';
            if (tip) rememberColorKey(tip);
            if (name) labels.set(hash, name);

            const parents = r.commit.parents || [];
            const mergedName = parents.length > 1
                ? parseMergedBranchName(r.commit.subject)
                : '';
            parents.forEach((ph, pi) => {
                if (!ph) return;
                if (pi === 0) {
                    // First-parent: continue this commit's branch downward.
                    if (name && !inherit.has(ph)) inherit.set(ph, name);
                    return;
                }
                // Merge side: never inherit the merge commit's branch.
                if (!inherit.has(ph)) {
                    const side = mergedName || sideBranchId(ph);
                    inherit.set(ph, side);
                    rememberColorKey(side);
                }
            });
        });

        // Fill any remaining gaps (orphan stubs / truncated history).
        rows.forEach((r) => {
            const hash = r.commit.hash;
            if (labels.get(hash)) return;
            let name = inherit.get(hash) || '';
            if (!name) {
                name = '__side__lane' + r.lane;
                rememberColorKey(name);
            }
            labels.set(hash, name);
        });

        const colors = new Map();
        tipOrder.forEach((name, i) => colors.set(name, COLORS[i % COLORS.length]));
        labels.forEach((name) => {
            if (name && !colors.has(name)) {
                colors.set(name, COLORS[colors.size % COLORS.length]);
            }
        });

        return { labels, colors };
    }

    function layoutGraph(list) {
        layoutRows = assignLanes(list);
        hashToRow = new Map();
        layoutRows.forEach((r, i) => hashToRow.set(r.commit.hash, i));
        const owned = assignBranchOwnership(layoutRows);
        commitBranch = owned.labels;
        branchColorMap = owned.colors;
        const maxLane = layoutRows.reduce((m, r) => Math.max(m, r.lane, r.activeCount - 1), 0);
        // SVG needs the full lane stack; row text insets are per-node (not this max).
        graphGutter = GUTTER_PAD + (maxLane + 1) * LANE_W + 12;
        els.rows.style.setProperty('--row-h', ROW_H + 'px');
        const height = layoutRows.length * ROW_H + 8;
        els.rows.style.height = height + 'px';
        lastPaintKey = '';
        paintStorm = false;
        paintsInWindow = 0;
        clearBranchHover();
        schedulePaint();
    }

    function clearBranchHover() {
        hoveredBranch = null;
        if (els.svg) els.svg.classList.remove('has-lane-hot');
        if (els.laneTip) {
            els.laneTip.hidden = true;
            els.laneTip.textContent = '';
        }
    }

    function applyBranchHover(branch, clientX, clientY) {
        if (!els.svg) return;
        const name = branch == null ? '' : String(branch);
        if (!name) {
            clearBranchHover();
            return;
        }
        hoveredBranch = name;
        els.svg.classList.add('has-lane-hot');
        els.svg.querySelectorAll('[data-branch]').forEach((el) => {
            el.classList.toggle('is-lane-hot', el.getAttribute('data-branch') === name);
        });
        if (els.laneTip) {
            els.laneTip.textContent = displayBranchName(name);
            els.laneTip.hidden = !displayBranchName(name);
            if (clientX != null && clientY != null) {
                els.laneTip.style.left = clientX + 'px';
                els.laneTip.style.top = clientY + 'px';
            }
        }
    }

    function laneX(lane) {
        return GUTTER_PAD + lane * LANE_W;
    }

    function quantizedViewHeight(scrollEl) {
        // Bucket clientHeight so subpixel / scrollbar gutter flicker in narrow
        // Electron split panes cannot flip visibleRange every frame.
        const raw = Math.max(scrollEl.clientHeight || 0, ROW_H);
        return Math.max(ROW_H, Math.round(raw / 16) * 16);
    }

    function visibleRange() {
        const scrollEl = els.scroll;
        const n = layoutRows.length;
        if (!scrollEl || !n) return [0, 0];
        const st = scrollEl.scrollTop;
        const h = quantizedViewHeight(scrollEl);
        const buf = 10;
        const start = Math.max(0, Math.floor(st / ROW_H) - buf);
        const end = Math.min(n, Math.ceil((st + h) / ROW_H) + buf);
        return [start, end];
    }

    function schedulePaint() {
        if (paintStorm || paintRaf) return;
        paintRaf = requestAnimationFrame(() => {
            paintRaf = 0;
            paintViewport();
        });
    }

    let lastPaintKey = '';
    let ignoreScrollPaint = false;
    let paintStorm = false;
    let paintsInWindow = 0;
    let paintWindowStart = 0;
    let resizePaintTimer = 0;

    function paintViewport() {
        if (paintStorm || !els.rows || !els.svg || !els.scroll) return;
        const now = performance.now();
        if (now - paintWindowStart > 1000) {
            paintWindowStart = now;
            paintsInWindow = 0;
        }
        paintsInWindow += 1;
        // Split-pane Electron used to lock the shared renderer main thread here:
        // clientHeight flickered → paintKey changed → innerHTML → layout → repeat.
        if (paintsInWindow > 40) {
            paintStorm = true;
            console.error('[git-graph] paint storm suppressed (split/resize feedback loop)');
            return;
        }

        const n = layoutRows.length;
        const [start, end] = visibleRange();
        const st = els.scroll.scrollTop;
        const viewH = quantizedViewHeight(els.scroll);
        const width = graphGutter;
        // Height follows the commit list only. Sizing the SVG to the viewport
        // (Math.max(commits, viewH)) feedback-loops with Electron layout/resize.
        const totalH = Math.max(n * ROW_H + 8, ROW_H);
        const paintKey = [width, totalH, start, end, Math.round(st), selectedHash || ''].join('|');
        if (paintKey === lastPaintKey) return;
        lastPaintKey = paintKey;

        // Same coordinate space as the absolutely positioned rows (scroll together).
        els.svg.setAttribute('width', String(width));
        els.svg.setAttribute('height', String(totalH));
        els.svg.setAttribute('viewBox', '0 0 ' + width + ' ' + totalH);
        els.svg.style.width = width + 'px';
        els.svg.style.height = totalH + 'px';
        els.svg.style.top = '0';
        els.svg.style.left = '0';

        let html = '';
        for (let idx = start; idx < end; idx++) {
            const r = layoutRows[idx];
            const c = r.commit;
            const subject = String(c.subject || '(no subject)').replace(/\s+/g, ' ').trim();
            const refs = (c.refs || []).map((lab) => {
                const head = lab === 'HEAD' ? ' is-head' : '';
                return '<span class="git-graph-ref' + head + '" title="' + esc(lab) + '">' + esc(lab) + '</span>';
            }).join('');
            const meta = [c.author, c.date].filter(Boolean).join(' · ');
            const sel = c.hash === selectedHash ? ' is-selected' : '';
            // Sit text beside this commit's node — not after the global max-lane gutter.
            const inset = laneX(r.lane) + TEXT_AFTER_NODE;
            html +=
                '<div class="git-graph-row' + sel + '" data-hash="' + esc(c.hash)
                + '" data-idx="' + idx + '" role="button" tabindex="0" style="top:'
                + (idx * ROW_H) + 'px;--text-inset:' + inset + 'px">'
                + '<div class="git-graph-row-body">'
                + '<div class="git-graph-row-top">'
                + '<span class="git-graph-hash">' + esc(c.short || c.hash.slice(0, 7)) + '</span>'
                + (refs ? '<span class="git-graph-refs">' + refs + '</span>' : '')
                + '<span class="git-graph-subject" title="' + esc(subject) + '">' + esc(subject) + '</span>'
                + '</div>'
                + '<div class="git-graph-meta">' + esc(meta) + '</div>'
                + '</div></div>';
        }
        ignoreScrollPaint = true;
        els.rows.innerHTML = html;

        const yMin = st;
        const yMax = st + viewH;
        let paths = '';
        let nodes = '';

        // Full commit walk: long parent edges can cross the viewport even when
        // neither endpoint is in the visible row window.
        for (let i = 0; i < n; i++) {
            const r = layoutRows[i];
            const y1 = i * ROW_H + ROW_H / 2;
            const x1 = laneX(r.lane);
            const childBranch = commitBranch.get(r.commit.hash) || '';
            const parents = r.commit.parents || [];
            for (let pi = 0; pi < parents.length; pi++) {
                const ph = parents[pi];
                const j = hashToRow.get(ph);
                let x2;
                let y2;
                let edgeBranch = childBranch;
                if (j == null) {
                    const edgeMeta = (r.edges || []).find((e) =>
                        e.fromLane === r.lane && ((pi === 0 && !e.merge) || (pi > 0 && e.merge))
                    );
                    const stubLane = (edgeMeta && edgeMeta.toLane != null)
                        ? edgeMeta.toLane
                        : (r.lane + (pi ? pi : 0));
                    x2 = laneX(stubLane);
                    y2 = y1 + ROW_H * 0.85;
                    if (pi > 0) {
                        // Stubbed merge side — keep distinct from the merge commit.
                        edgeBranch = sideBranchId(ph);
                    }
                } else {
                    const dest = layoutRows[j];
                    x2 = laneX(dest.lane);
                    y2 = j * ROW_H + ROW_H / 2;
                    if (pi > 0) {
                        // Never fall back to the merge commit's branch (that made
                        // side-lane edges report "master").
                        edgeBranch = commitBranch.get(dest.commit.hash)
                            || sideBranchId(dest.commit.hash);
                    }
                }
                const top = Math.min(y1, y2);
                const bot = Math.max(y1, y2);
                if (bot < yMin || top > yMax) continue;
                paths += edgePath(x1, y1, x2, y2, colorForBranch(edgeBranch), edgeBranch);
            }
        }

        for (let i = start; i < end; i++) {
            const r = layoutRows[i];
            const cx = laneX(r.lane);
            const cy = i * ROW_H + ROW_H / 2;
            const br = commitBranch.get(r.commit.hash) || '';
            const col = colorForBranch(br);
            const sel = r.commit.hash === selectedHash;
            nodes +=
                '<circle class="git-graph-node" data-branch="' + esc(br) + '" cx="' + cx + '" cy="' + cy
                + '" r="' + (sel ? 6 : 4.5) + '" fill="' + col + '"'
                + (sel ? ' stroke="#fff" stroke-width="2"' : '') + '/>';
        }

        els.svg.innerHTML = paths + nodes;
        if (hoveredBranch) applyBranchHover(hoveredBranch);
        // Allow layout to settle before re-arming scroll→paint.
        requestAnimationFrame(() => { ignoreScrollPaint = false; });
    }

    function edgePath(x1, y1, x2, y2, color, branch) {
        const d = (x1 === x2)
            ? ('M' + x1 + ' ' + y1 + ' L' + x2 + ' ' + y2)
            : (function () {
                const midY = (y1 + y2) / 2;
                return 'M' + x1 + ' ' + y1 + ' C' + x1 + ' ' + midY + ', ' + x2 + ' ' + midY + ', ' + x2 + ' ' + y2;
            }());
        const brAttr = ' data-branch="' + esc(branch || '') + '"';
        const hit = '<path class="git-graph-edge-hit"' + brAttr + ' d="' + d
            + '" fill="none" stroke="transparent" stroke-width="12"/>';
        const vis = '<path class="git-graph-edge"' + brAttr + ' d="' + d
            + '" fill="none" stroke="' + color + '" stroke-width="2" opacity="'
            + (x1 === x2 ? '0.85' : '0.75') + '"/>';
        return hit + vis;
    }

    function highlightSelection() {
        schedulePaint();
    }

    function filteredCommits() {
        const q = (els.filter.value || '').trim().toLowerCase();
        if (!q) return commits;
        return commits.filter((c) => {
            const hay = [
                c.hash, c.short, c.subject, c.author,
                ...(c.refs || []),
            ].join(' ').toLowerCase();
            return hay.includes(q);
        });
    }

    function isDefaultBranchName(name) {
        const n = String(name || '').toLowerCase();
        return n === 'main' || n === 'master';
    }

    function branchChipHtml(opts) {
        const mode = opts.mode;
        const name = opts.name || '';
        const active = !!opts.active;
        const head = opts.head
            ? '<span class="is-head-mark" title="HEAD">●</span>'
            : '';
        const meta = opts.meta || '';
        const label = opts.label || name;
        let attrs = ' type="button" class="git-graph-branch' + (active ? ' is-active' : '') + '"';
        if (mode === 'current') attrs += ' data-mode="current"';
        else if (mode === 'all') attrs += ' data-mode="all"';
        else attrs += ' data-mode="branch" data-branch="' + esc(name) + '"';
        attrs += ' title="' + esc(opts.title || label) + '"';
        return '<button' + attrs + '>'
            + '<span class="git-graph-branch-name">' + head + esc(label)
            + (opts.inlineMeta
                ? ' <span class="git-graph-branch-meta">(' + esc(opts.inlineMeta) + ')</span>'
                : '')
            + '</span>'
            + (meta ? '<span class="git-graph-branch-meta">' + esc(meta) + '</span>' : '')
            + '</button>';
    }

    let branchesRenderCtx = { items: [], currentBranch: '' };
    let branchFitRaf = 0;
    const BRANCH_CHIP_GAP = 6;
    const BRANCH_MENU_BTN_W = 32;

    function scheduleFitBranches() {
        if (branchFitRaf) return;
        branchFitRaf = requestAnimationFrame(() => {
            branchFitRaf = 0;
            fitAndRenderBranches();
        });
    }

    function measureBranchChipHtml(html) {
        if (!els.branchesList) return 0;
        const probe = document.createElement('div');
        probe.setAttribute('aria-hidden', 'true');
        probe.style.cssText = 'position:absolute;left:-9999px;top:0;display:inline-flex;'
            + 'visibility:hidden;pointer-events:none;white-space:nowrap;';
        probe.innerHTML = html;
        els.branchesList.appendChild(probe);
        const w = Math.ceil(probe.getBoundingClientRect().width);
        probe.remove();
        return w;
    }

    /** Priority: main/master, active selection, then remaining (API order = recent). */
    function orderBranchCandidates(items) {
        const list = Array.isArray(items) ? items.filter((b) => b && b.name) : [];
        const seen = new Set();
        const out = [];

        function push(b) {
            if (!b || !b.name || seen.has(b.name)) return;
            seen.add(b.name);
            out.push(b);
        }

        const defaults = list.filter((b) => isDefaultBranchName(b.name));
        defaults.sort((a, b) => {
            if (a.current !== b.current) return a.current ? -1 : 1;
            if (a.name.toLowerCase() === 'main') return -1;
            if (b.name.toLowerCase() === 'main') return 1;
            return String(a.name).localeCompare(String(b.name));
        });
        defaults.forEach(push);

        if (branchMode && branchMode !== '__all__') {
            const active = list.find((b) => b.name === branchMode)
                || { name: branchMode, current: false, short: '', subject: '' };
            push(active);
        }

        list.forEach(push);
        return out;
    }

    function fitAndRenderBranches() {
        if (!els.branchesList) return;
        const items = branchesRenderCtx.items;
        const currentBranch = branchesRenderCtx.currentBranch;
        const candidates = orderBranchCandidates(items);

        const mustNames = new Set();
        candidates.forEach((b) => {
            if (isDefaultBranchName(b.name)) mustNames.add(b.name);
        });
        if (branchMode && branchMode !== '__all__') mustNames.add(branchMode);

        const currentHtml = branchChipHtml({
            mode: 'current',
            active: branchMode === null,
            label: 'Current',
            inlineMeta: currentBranch || '',
            title: currentBranch ? ('Current (' + currentBranch + ')') : 'Current HEAD history',
        });
        const allHtml = branchChipHtml({
            mode: 'all',
            active: branchMode === '__all__',
            label: 'All',
            title: 'All branches — merged topology',
        });

        const chipSpecs = candidates.map((b) => {
            const meta = [b.short, b.subject].filter(Boolean).join(' · ');
            return {
                name: b.name,
                must: mustNames.has(b.name),
                html: branchChipHtml({
                    mode: 'branch',
                    name: b.name,
                    active: branchMode === b.name,
                    head: !!b.current,
                    label: b.name,
                    meta: meta,
                    title: b.name,
                }),
            };
        });

        const listW = els.branchesList.clientWidth;
        if (listW <= 0) {
            scheduleFitBranches();
            return;
        }

        const fixedW = measureBranchChipHtml(currentHtml)
            + BRANCH_CHIP_GAP
            + measureBranchChipHtml(allHtml);
        const widths = chipSpecs.map((c) => measureBranchChipHtml(c.html));

        function pack(budget) {
            const visibleIdx = [];
            const overflowIdx = [];
            let used = 0;
            chipSpecs.forEach((c, i) => {
                const need = widths[i] + BRANCH_CHIP_GAP;
                if (used + need <= budget) {
                    visibleIdx.push(i);
                    used += need;
                } else {
                    overflowIdx.push(i);
                }
            });

            // Keep Current/All companions: promote main/master/active into the
            // strip by dropping trailing optional chips if needed.
            overflowIdx.slice().forEach((mi) => {
                if (!chipSpecs[mi].must) return;
                const need = widths[mi] + BRANCH_CHIP_GAP;
                while (used + need > budget) {
                    let dropPos = -1;
                    for (let k = visibleIdx.length - 1; k >= 0; k--) {
                        if (!chipSpecs[visibleIdx[k]].must) {
                            dropPos = k;
                            break;
                        }
                    }
                    if (dropPos < 0) break;
                    const dropped = visibleIdx.splice(dropPos, 1)[0];
                    used -= widths[dropped] + BRANCH_CHIP_GAP;
                    overflowIdx.push(dropped);
                }
                const oi = overflowIdx.indexOf(mi);
                if (oi >= 0) overflowIdx.splice(oi, 1);
                visibleIdx.push(mi);
                used += need;
            });

            visibleIdx.sort((a, b) => a - b);
            const visSet = new Set(visibleIdx);
            const cleanOverflow = [...new Set(overflowIdx)]
                .filter((i) => !visSet.has(i))
                .sort((a, b) => a - b);
            return { visibleIdx, overflowIdx: cleanOverflow };
        }

        const availNoMenu = Math.max(0, listW - fixedW - BRANCH_CHIP_GAP);
        let packed = pack(availNoMenu);
        if (packed.overflowIdx.length) {
            const availWithMenu = Math.max(
                0,
                listW - fixedW - BRANCH_CHIP_GAP - BRANCH_MENU_BTN_W - BRANCH_CHIP_GAP
            );
            packed = pack(availWithMenu);
        }

        let html = currentHtml + allHtml;
        packed.visibleIdx.forEach((i) => { html += chipSpecs[i].html; });

        if (packed.overflowIdx.length) {
            html +=
                '<div class="git-graph-branches-more">'
                + '<button type="button" class="git-graph-branches-menu-btn" id="gitGraphBranchesMenuBtn"'
                + ' aria-label="More branches" aria-expanded="false" aria-haspopup="true" title="More branches">'
                + '<span class="git-graph-hamburger" aria-hidden="true"><span></span><span></span><span></span></span>'
                + '</button>'
                + '<div class="git-graph-branches-menu" id="gitGraphBranchesMenu" hidden role="menu">';
            packed.overflowIdx.forEach((i) => { html += chipSpecs[i].html; });
            html += '</div></div>';
        }

        els.branchesList.innerHTML = html;
    }

    function renderBranches(list, currentBranch) {
        branchesRenderCtx = {
            items: Array.isArray(list) ? list : [],
            currentBranch: currentBranch || '',
        };
        fitAndRenderBranches();
    }

    function setBranchesMenuOpen(open) {
        const btn = document.getElementById('gitGraphBranchesMenuBtn');
        const menu = document.getElementById('gitGraphBranchesMenu');
        if (!btn || !menu) return;
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function toggleBranchesMenu() {
        const menu = document.getElementById('gitGraphBranchesMenu');
        if (!menu) return;
        setBranchesMenuOpen(!!menu.hidden);
    }

    function setBranchMode(mode) {
        branchMode = mode;
        saveBranchMode(mode);
        return loadGraph();
    }

    function updateCommitSubtitle() {
        if (!els.subtitle) return;
        const n = commits.length;
        let text = n
            + (graphHasMore ? '+' : '')
            + ' commit' + (n === 1 ? '' : 's');
        if (projectRepos.length > 1 && els.repo && els.repo.selectedOptions && els.repo.selectedOptions[0]) {
            const label = (els.repo.selectedOptions[0].textContent || '').trim();
            if (label) text = label + ' · ' + text;
        }
        els.subtitle.textContent = text;
    }

    function graphQueryParams(skip) {
        const path = currentProjectPath();
        const qs = new URLSearchParams({
            path,
            limit: String(PAGE_SIZE),
            skip: String(Math.max(0, skip || 0)),
            all: branchMode === '__all__' ? '1' : '0',
        });
        if (branchMode && branchMode !== '__all__') {
            qs.set('branch', branchMode);
        }
        const repoRoot = currentRepoRoot();
        if (repoRoot) qs.set('repo_root', repoRoot);
        return qs;
    }

    async function loadGraph() {
        const path = currentProjectPath();
        if (!path) {
            setStatus('Select a project.', true);
            return;
        }
        try { localStorage.setItem(STORAGE_PROJECT, path); } catch (_) {}
        if (!projectRepos.length) {
            await loadReposForProject(path);
        }

        if (graphAbort) graphAbort.abort();
        graphAbort = new AbortController();
        const ac = graphAbort;
        graphLoadingMore = false;
        graphHasMore = false;

        setStatus('Loading graph…', false);
        const resp = await fetch('/api/git/graph?' + graphQueryParams(0).toString(), {
            credentials: 'include',
            signal: ac.signal,
        });
        const data = await resp.json().catch(() => null);
        if (ac.signal.aborted) return;
        if (!data || !data.success) {
            setStatus((data && data.error) || 'Failed to load graph', true);
            return;
        }
        commits = Array.isArray(data.commits) ? data.commits : [];
        branches = Array.isArray(data.branches) ? data.branches : [];
        graphHeadBranch = data.branch || '';
        graphHasMore = !!data.has_more;
        renderBranches(branches, graphHeadBranch);
        updateCommitSubtitle();
        if (!commits.length) {
            setStatus('No commits in this repository.', false);
            return;
        }
        setStatus('', false);
        layoutGraph(filteredCommits());
    }

    async function loadMoreGraph() {
        if (!graphHasMore || graphLoadingMore) return;
        const path = currentProjectPath();
        if (!path || !commits.length) return;

        graphLoadingMore = true;
        if (graphAbort) graphAbort.abort();
        graphAbort = new AbortController();
        const ac = graphAbort;
        const skip = commits.length;

        try {
            const resp = await fetch('/api/git/graph?' + graphQueryParams(skip).toString(), {
                credentials: 'include',
                signal: ac.signal,
            });
            const data = await resp.json().catch(() => null);
            if (ac.signal.aborted) return;
            if (!data || !data.success) {
                graphHasMore = false;
                return;
            }
            const more = Array.isArray(data.commits) ? data.commits : [];
            graphHasMore = !!data.has_more && more.length > 0;
            if (!more.length) {
                graphHasMore = false;
                updateCommitSubtitle();
                return;
            }
            const seen = new Set(commits.map((c) => c.hash));
            const added = more.filter((c) => c && c.hash && !seen.has(c.hash));
            if (!added.length) {
                graphHasMore = false;
                updateCommitSubtitle();
                return;
            }
            commits = commits.concat(added);
            updateCommitSubtitle();
            layoutGraph(filteredCommits());
        } catch (e) {
            if (e && e.name === 'AbortError') return;
            console.warn('[git-graph] load more failed', e);
        } finally {
            graphLoadingMore = false;
        }
    }

    function maybeLoadMoreFromScroll() {
        const el = els.scroll;
        if (!el || el.hidden || !graphHasMore || graphLoadingMore) return;
        const remain = el.scrollHeight - el.scrollTop - el.clientHeight;
        if (remain < ROW_H * 12) {
            loadMoreGraph().catch(() => {});
        }
    }

    async function selectCommit(hash, opts) {
        if (!hash) return;
        selectedHash = hash;
        highlightSelection();

        els.drawer.hidden = false;
        els.layout.classList.remove('drawer-closed');
        els.detailTitle.textContent = hash.slice(0, 7);
        els.detailBody.innerHTML = '<p class="git-graph-muted">Loading…</p>';

        if (opts && opts.skipFetch) return;

        const path = currentProjectPath();
        const qs = new URLSearchParams({ path });
        const repoRoot = currentRepoRoot();
        if (repoRoot) qs.set('repo_root', repoRoot);
        try {
            const resp = await fetch(
                '/api/git/commit/' + encodeURIComponent(hash) + '/detail?' + qs.toString(),
                { credentials: 'include' }
            );
            const data = await resp.json().catch(() => null);
            if (!data || !data.success) {
                els.detailBody.innerHTML = '<p class="git-graph-muted is-error">'
                    + esc((data && data.error) || 'Failed to load commit') + '</p>';
                return;
            }
            if (selectedHash !== hash) return;
            if (commitViewer) commitViewer.renderDetail(data);
        } catch (e) {
            els.detailBody.innerHTML = '<p class="git-graph-muted">' + esc(e.message || 'Error') + '</p>';
        }
    }

    function closeDrawer() {
        els.drawer.hidden = true;
        els.layout.classList.add('drawer-closed');
        selectedHash = null;
        if (els.detailTitle) els.detailTitle.textContent = 'Commit';
        if (els.detailBody) {
            els.detailBody.innerHTML = '<p class="git-graph-muted">Select a commit on the graph.</p>';
            delete els.detailBody.dataset.commitFiles;
            delete els.detailBody.dataset.commitHash;
        }
        schedulePaint();
    }

    function currentProjectInfo() {
        const path = currentProjectPath();
        if (!path) return null;
        const fromList = projects.find((p) => p && p.path === path);
        if (fromList) {
            return {
                path: fromList.path,
                name: fromList.name || fromList.path,
                id: fromList.id,
            };
        }
        const opt = els.project && els.project.selectedOptions && els.project.selectedOptions[0];
        const name = (opt && (opt.getAttribute('data-name') || opt.textContent)) || path;
        return { path: path, name: String(name || '').trim() || path };
    }

    function reloadGraphAfterCommit() {
        loadGraph().catch((e) => {
            if (e && e.name === 'AbortError') return;
            setStatus(e.message, true);
        });
    }

    function initPendingChanges() {
        if (!window.CuttlePendingChangesPanel || typeof window.CuttlePendingChangesPanel.create !== 'function') {
            return;
        }
        pendingChangesCtl = window.CuttlePendingChangesPanel.create({
            getProject: currentProjectInfo,
            onOpenDiff: function (relPath, meta) {
                if (commitViewer) commitViewer.openPendingDiffModal(relPath, meta);
            },
            onAfterCommit: reloadGraphAfterCommit,
            onAfterIgnore: function () {
                if (pendingChangesCtl) pendingChangesCtl.scheduleRefresh(80);
            },
            isPageBackgrounded: function () {
                return document.hidden;
            },
        });
    }

    async function boot() {
        els.layout.classList.add('drawer-closed');
        els.drawer.hidden = true;
        initPendingChanges();
        try {
            await loadProjects();
            if (projects.length) {
                await loadReposForProject(currentProjectPath());
                await loadGraph();
            }
            if (pendingChangesCtl) pendingChangesCtl.scheduleRefresh(200);
        } catch (e) {
            if (e && e.name === 'AbortError') return;
            setStatus(e.message || String(e), true);
        }
    }

    els.refresh.addEventListener('click', () => {
        loadGraph().catch((e) => {
            if (e && e.name === 'AbortError') return;
            setStatus(e.message, true);
        });
        if (pendingChangesCtl) pendingChangesCtl.scheduleRefresh(80);
    });
    els.project.addEventListener('change', () => {
        selectedHash = null;
        closeDrawer();
        if (commitViewer) commitViewer.closeDiffModal();
        projectRepos = [];
        if (pendingChangesCtl) pendingChangesCtl.hide();
        (async () => {
            try {
                await loadReposForProject(currentProjectPath());
                await loadGraph();
            } catch (e) {
                if (e && e.name === 'AbortError') return;
                setStatus(e.message, true);
            }
            if (pendingChangesCtl) pendingChangesCtl.scheduleRefresh(120);
        })();
    });
    if (els.repo) {
        els.repo.addEventListener('change', () => {
            selectedHash = null;
            closeDrawer();
            if (commitViewer) commitViewer.closeDiffModal();
            // Branch names rarely match across sibling repos — reset to HEAD.
            branchMode = null;
            saveBranchMode(null);
            saveRepoRoot(currentProjectPath(), currentRepoRoot());
            loadGraph().catch((e) => {
                if (e && e.name === 'AbortError') return;
                setStatus(e.message, true);
            });
        });
    }
    if (els.branchesList) {
        els.branchesList.addEventListener('click', (e) => {
            const menuBtn = e.target && e.target.closest
                ? e.target.closest('.git-graph-branches-menu-btn')
                : null;
            if (menuBtn) {
                e.preventDefault();
                e.stopPropagation();
                toggleBranchesMenu();
                return;
            }
            const btn = e.target && e.target.closest ? e.target.closest('.git-graph-branch') : null;
            if (!btn) return;
            const mode = btn.getAttribute('data-mode');
            let next = null;
            if (mode === 'all') next = '__all__';
            else if (mode === 'branch') next = btn.getAttribute('data-branch') || null;
            else next = null;
            setBranchesMenuOpen(false);
            setBranchMode(next).catch((err) => {
                if (err && err.name === 'AbortError') return;
                setStatus(err.message, true);
            });
        });
    }
    document.addEventListener('click', (e) => {
        const menu = document.getElementById('gitGraphBranchesMenu');
        if (!menu || menu.hidden) return;
        const wrap = e.target && e.target.closest
            ? e.target.closest('.git-graph-branches-more')
            : null;
        if (!wrap) setBranchesMenuOpen(false);
    });
    if (els.svg) {
        els.svg.addEventListener('pointerover', (e) => {
            const hit = e.target && e.target.closest
                ? e.target.closest('[data-branch]')
                : null;
            if (!hit) return;
            const br = hit.getAttribute('data-branch') || '';
            if (!br) return;
            applyBranchHover(br, e.clientX, e.clientY);
        });
        els.svg.addEventListener('pointermove', (e) => {
            if (!hoveredBranch || !els.laneTip || els.laneTip.hidden) return;
            els.laneTip.style.left = e.clientX + 'px';
            els.laneTip.style.top = e.clientY + 'px';
        });
        els.svg.addEventListener('pointerout', (e) => {
            const related = e.relatedTarget;
            if (related && els.svg.contains(related)) {
                const hit = related.closest ? related.closest('[data-branch]') : null;
                const br = hit && hit.getAttribute('data-branch');
                if (br) {
                    applyBranchHover(br, e.clientX, e.clientY);
                    return;
                }
            }
            clearBranchHover();
        });
    }
    els.filter.addEventListener('input', () => {
        clearTimeout(filterTimer);
        filterTimer = setTimeout(() => {
            if (!commits.length) return;
            setStatus('', false);
            layoutGraph(filteredCommits());
        }, 120);
    });
    els.drawerClose.addEventListener('click', closeDrawer);
    els.scroll.addEventListener('scroll', () => {
        if (ignoreScrollPaint) return;
        schedulePaint();
        maybeLoadMoreFromScroll();
    }, { passive: true });
    window.addEventListener('resize', () => {
        scheduleFitBranches();
        if (resizePaintTimer) clearTimeout(resizePaintTimer);
        resizePaintTimer = setTimeout(() => {
            resizePaintTimer = 0;
            lastPaintKey = '';
            schedulePaint();
        }, 120);
    });
    if (typeof ResizeObserver !== 'undefined' && els.branchesList) {
        const branchRo = new ResizeObserver(() => scheduleFitBranches());
        branchRo.observe(els.branchesList);
    }
    els.rows.addEventListener('click', (e) => {
        const row = e.target && e.target.closest ? e.target.closest('.git-graph-row') : null;
        if (!row) return;
        selectCommit(row.getAttribute('data-hash'));
    });
    els.rows.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const row = ev.target && ev.target.closest ? ev.target.closest('.git-graph-row') : null;
        if (!row) return;
        ev.preventDefault();
        selectCommit(row.getAttribute('data-hash'));
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const menu = document.getElementById('gitGraphBranchesMenu');
        if (menu && !menu.hidden) {
            e.preventDefault();
            setBranchesMenuOpen(false);
            return;
        }
        if (window.CuttleDiffModal.isOpen()) {
            e.preventDefault();
            if (commitViewer) commitViewer.closeDiffModal();
            return;
        }
        if (els.drawer && !els.drawer.hidden) {
            e.preventDefault();
            closeDrawer();
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
