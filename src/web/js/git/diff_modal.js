/* Shared file-diff popup. Owns markup, rendering, navigation and desktop opening.
 * Pages pass an explicit project/repo snapshot; pending and commit diffs use one UI.
 * Pending files with unreviewed agent changes (CuttleAgentReview, flag
 * `agent_review`) open in "Agent changes" mode: unified hunks against the
 * pre-turn baseline with per-hunk Keep / Undo; "Working tree" is the git diff.
 */
(function (global) {
    'use strict';
    const VIEW_KEY = 'cuttlePendingDiffViewMode';
    let modal = null;
    let state = null;
    let lastData = null;
    let generation = 0;
    let controller = null;
    let returnFocus = null;
    let ideFlag = null; // Promise<boolean>: experimental `ide` (Editor button)
    const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const el = (key) => modal.querySelector('[data-diff="' + key + '"]');
    const toast = (message) => (global.showToast || function () {})(message, 'error');

    function readViewMode() {
        try { return global.localStorage.getItem(VIEW_KEY) === 'unified' ? 'unified' : 'split'; }
        catch (_) { return 'split'; }
    }
    function writeViewMode(mode) {
        try { global.localStorage.setItem(VIEW_KEY, mode); } catch (_) {}
    }
    function statusLabel(raw) {
        const s = String(raw || 'modified').toLowerCase();
        if (s === 'untracked' || s === 'u') return 'U';
        if (s === 'added' || s === 'a') return 'A';
        if (s === 'deleted' || s === 'd') return 'D';
        if (s.startsWith('r')) return 'R';
        if (s.startsWith('c') && s !== 'conflict') return 'C';
        if (s === 'conflict' || s === '!') return '!';
        return 'M';
    }
    function formatStat(n, sign) {
        const v = Number(n) || 0;
        return v > 0 ? sign + (v > 9999 ? '9999+' : String(v)) : '';
    }

    function ensureModal() {
        if (modal) return;
        modal = document.createElement('dialog');
        modal.id = 'cuttleDiffModal';
        modal.className = 'pending-diff-modal cuttle-diff-modal';
        modal.setAttribute('aria-labelledby', 'cuttleDiffTitle');
        modal.innerHTML = `
            <button type="button" class="pending-diff-side-nav is-prev" data-diff="sidePrev" title="Previous file (←)" aria-label="Previous file" hidden>‹</button>
            <div class="pending-diff-modal-card">
                <header class="pending-diff-modal-header">
                    <div class="pending-diff-modal-heading">
                        <h2 class="pending-diff-modal-title" id="cuttleDiffTitle"><a href="#" data-diff="title" title="Open file in its default application">File diff</a></h2>
                        <div class="pending-diff-modal-meta">
                            <span class="pending-diff-modal-badge" data-diff="status"></span>
                            <span class="pending-diff-modal-stats" data-diff="stats"></span>
                            <span class="git-diff-commit" data-diff="source"></span>
                            <button type="button" class="pending-diff-editor-btn" data-diff="editor" title="Open this file in the Cuttle IDE" hidden>Editor</button>
                            <div class="pending-diff-view-toggle pending-diff-source-toggle" data-diff="sourceToggle" role="group" aria-label="Diff source" hidden>
                                <button type="button" class="pending-diff-view-btn" data-diff-source="agent" aria-pressed="true" title="Agent changes since the turn started, with Keep / Undo">✦ Agent changes</button>
                                <button type="button" class="pending-diff-view-btn" data-diff-source="git" aria-pressed="false" title="Working tree against HEAD">Working tree</button>
                            </div>
                            <span class="pending-diff-review-actions" data-diff="reviewActions" hidden>
                                <button type="button" class="pending-diff-review-btn is-undo" data-review-file="undo" title="Undo every agent change in this file">Undo file</button>
                                <button type="button" class="pending-diff-review-btn is-keep" data-review-file="keep" title="Keep every agent change in this file">Keep file</button>
                            </span>
                            <div class="pending-diff-view-toggle" data-diff="viewToggle" role="group" aria-label="Diff view mode">
                                <button type="button" class="pending-diff-view-btn" data-diff-view="split" aria-pressed="true" title="Separate Added and Removed sections">Split</button>
                                <button type="button" class="pending-diff-view-btn" data-diff-view="unified" aria-pressed="false" title="Preserve hunk line order (adds and removes interleaved)">Unified</button>
                            </div>
                        </div>
                    </div>
                    <div class="pending-diff-file-nav" data-diff="nav" hidden>
                        <button type="button" class="pending-diff-file-nav-btn" data-diff="prev" title="Previous file (←)" aria-label="Previous file">‹</button>
                        <span class="pending-diff-file-nav-pos" data-diff="position" aria-live="polite"></span>
                        <button type="button" class="pending-diff-file-nav-btn" data-diff="next" title="Next file (→)" aria-label="Next file">›</button>
                    </div>
                    <button type="button" class="pending-diff-modal-close" data-diff="close" title="Close diff" aria-label="Close diff">×</button>
                </header>
                <div class="pending-diff-modal-body">
                    <div class="pending-diff-modal-loading" data-diff="loading" hidden><span class="pending-diff-modal-spinner" aria-hidden="true"></span><span>Loading diff…</span></div>
                    <div class="pending-diff-modal-empty" data-diff="empty" hidden></div>
                    <div class="pending-diff-modal-hunks" data-diff="hunks" hidden></div>
                </div>
            </div>
            <button type="button" class="pending-diff-side-nav is-next" data-diff="sideNext" title="Next file (→)" aria-label="Next file" hidden>›</button>`;
        document.body.appendChild(modal);
        el('close').addEventListener('click', close);
        el('title').addEventListener('click', (e) => { e.preventDefault(); openFile(); });
        el('editor').addEventListener('click', openInEditor);
        ['prev', 'sidePrev'].forEach((key) => el(key).addEventListener('click', () => navigate(-1)));
        ['next', 'sideNext'].forEach((key) => el(key).addEventListener('click', () => navigate(1)));
        modal.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
        modal.addEventListener('close', () => { if (state && !modal.open) close(); });
        modal.addEventListener('click', (e) => {
            if (e.target === modal) close();
            const view = e.target.closest('[data-diff-view]');
            if (view) { writeViewMode(view.dataset.diffView); if (lastData) render(lastData); }
            const source = e.target.closest('[data-diff-source]');
            if (source && state && source.dataset.diffSource !== state.source) {
                state.source = source.dataset.diffSource;
                loadFile(state.file, { source: state.source });
            }
            const hunkAct = e.target.closest('[data-review-hunk]');
            if (hunkAct) reviewAct(hunkAct.dataset.reviewKind, [hunkAct.dataset.reviewHunk]);
            const fileAct = e.target.closest('[data-review-file]');
            if (fileAct) reviewAct(fileAct.dataset.reviewFile, null);
            const more = e.target.closest('[data-pending-diff-more], [data-pending-diff-all]');
            if (more && state) {
                const full = more.hasAttribute('data-pending-diff-all');
                const max = Math.max(50, Number((lastData && lastData.max_lines_per_side) || state.maxLines || 300));
                loadFile(state.file, { full: full, maxLines: full ? 100000 : Math.min(max * 3, 100000) });
            }
        });
        // Capture before page shortcuts, so dismissing the popup doesn't close its underlying drawer.
        document.addEventListener('keydown', (e) => {
            if (!isOpen() || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
            if (e.target && e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
            const handled = e.key === 'Escape' ? (close(), true)
                : e.key === 'ArrowLeft' ? navigate(-1) : e.key === 'ArrowRight' ? navigate(1) : false;
            if (handled) { e.preventDefault(); e.stopPropagation(); }
        }, true);
    }
    function isOpen() { return !!(modal && modal.open); }
    function close() {
        generation++;
        if (controller) controller.abort();
        controller = null;
        state = null;
        lastData = null;
        if (modal && modal.open) modal.close();
        if (returnFocus && returnFocus.isConnected) returnFocus.focus();
        returnFocus = null;
    }
    function updateNavigation() {
        const index = state.files.indexOf(state.file);
        const show = state.files.length > 1 && index >= 0;
        el('nav').hidden = !show;
        el('sidePrev').hidden = !show;
        el('sideNext').hidden = !show;
        el('position').textContent = show ? (index + 1) + ' / ' + state.files.length : '';
        ['prev', 'sidePrev'].forEach((key) => { el(key).disabled = !show || index <= 0; });
        ['next', 'sideNext'].forEach((key) => { el(key).disabled = !show || index >= state.files.length - 1; });
    }
    function navigate(delta) {
        if (!isOpen() || !state) return false;
        const index = state.files.indexOf(state.file) + delta;
        if (index < 0 || index >= state.files.length) return false;
        loadFile(state.files[index], { full: false, maxLines: 300 });
        return true;
    }
    function formatDiffLineNo(n) {
        if (n == null || n === '' || Number(n) <= 0) return '';
        return String(n);
    }

    function renderDiffLineHTML(line) {
        const type = String((line && line.type) || 'context');
        if (type === 'meta') {
            return '<div class="pending-diff-line is-meta">' + esc(String(line.text || '')) + '</div>';
        }
        const cls = type === 'add' ? 'is-add' : (type === 'del' ? 'is-del' : 'is-context');
        const ch = type === 'add' ? '+' : (type === 'del' ? '-' : ' ');
        const oldNo = formatDiffLineNo(line.old_no);
        const newNo = formatDiffLineNo(line.new_no);
        // Prefer the side that still exists for this line (new for adds, old for dels).
        const ln = newNo || oldNo || '';
        return (
            '<div class="pending-diff-line ' + cls + '">'
            + '<span class="pending-diff-ln">' + esc(ln) + '</span>'
            + '<span class="pending-diff-ch">' + ch + '</span>'
            + '<span class="pending-diff-txt">' + esc(String(line.text || '')) + '</span>'
            + '</div>'
        );
    }

    function renderDiffHunksHTML(hunks) {
        const list = Array.isArray(hunks) ? hunks : [];
        if (!list.length) return '';
        return list.map((hunk) => {
            const lines = Array.isArray(hunk.lines) ? hunk.lines : [];
            const header = esc(String(hunk.header || ''));
            const body = lines.map(renderDiffLineHTML).join('');
            return (
                '<section class="pending-diff-hunk">'
                + '<div class="pending-diff-hunk-header">' + header + '</div>'
                + '<div class="pending-diff-lines">' + body + '</div>'
                + '</section>'
            );
        }).join('');
    }

    function renderDiffSectionHTML(kind, section, opts) {
        const sec = section || {};
        const hunks = Array.isArray(sec.hunks) ? sec.hunks : [];
        const hasLines = hunks.some((h) => Array.isArray(h.lines) && h.lines.length);
        if (!hasLines && !(sec.total > 0)) return '';

        const label = kind === 'removed' ? 'Removed' : 'Added';
        const tone = kind === 'removed' ? 'is-removed' : 'is-added';
        const shown = Number(sec.shown) || 0;
        const total = Number(sec.total) || shown;
        const truncated = !!sec.truncated;
        const alreadyFull = !!(opts && opts.full);

        let html =
            '<section class="pending-diff-side ' + tone + '">'
            + '<div class="pending-diff-side-header">'
            + '<span class="pending-diff-side-title">' + label + '</span>'
            + '<span class="pending-diff-side-count">'
            + esc(String(shown))
            + (truncated ? ' of ' + esc(String(total)) : '')
            + '</span>'
            + '</div>';

        if (hasLines) {
            html += renderDiffHunksHTML(hunks);
        } else {
            html += '<div class="pending-diff-side-empty">No ' + label.toLowerCase() + ' lines</div>';
        }

        if (truncated && !alreadyFull) {
            html +=
                '<div class="pending-diff-truncated-note">'
                + '<span>Showing '
                + esc(String(shown))
                + ' of '
                + esc(String(total))
                + ' '
                + label.toLowerCase()
                + ' lines.</span>'
                + '<span class="pending-diff-truncate-actions">'
                + '<button type="button" class="pending-diff-expand-btn" data-pending-diff-more="'
                + esc(kind)
                + '">Show more</button>'
                + '<button type="button" class="pending-diff-expand-btn" data-pending-diff-all="'
                + esc(kind)
                + '">Show all</button>'
                + '</span>'
                + '</div>';
        }
        html += '</section>';
        return html;
    }


    // ── agent review mode ───────────────────────────────────────────
    function reviewClient() { return global.CuttleAgentReview || null; }
    function setReviewChrome(agentMode, hasReview) {
        el('sourceToggle').hidden = !hasReview;
        el('reviewActions').hidden = !agentMode;
        el('viewToggle').hidden = agentMode;
        modal.querySelectorAll('[data-diff-source]').forEach((btn) => {
            btn.setAttribute('aria-pressed', String((btn.dataset.diffSource === 'agent') === agentMode));
        });
    }
    function renderReview(entry, review) {
        lastData = null;
        const ra = reviewClient();
        el('title').textContent = state.file;
        const letter = entry.change === 'added' ? 'A' : entry.change === 'deleted' ? 'D' : 'M';
        el('status').textContent = letter;
        el('status').dataset.status = entry.change === 'added' ? 'added' : entry.change === 'deleted' ? 'deleted' : 'modified';
        el('stats').innerHTML = (entry.additions ? '<span class="pending-changes-add">' + esc(formatStat(entry.additions, '+')) + '</span>' : '')
            + (entry.deletions ? '<span class="pending-changes-del">' + esc(formatStat(entry.deletions, '-')) + '</span>' : '');
        el('loading').hidden = true;
        syncEditorButton({ success: true, status: letter });
        const hunks = ra.unifiedHunks(review, 3);
        el('empty').hidden = hunks.length > 0;
        el('hunks').hidden = !hunks.length;
        if (!hunks.length) { el('empty').textContent = 'All agent changes in this file are reviewed'; el('hunks').innerHTML = ''; return; }
        el('hunks').innerHTML = (review.live ? '<div class="pending-diff-review-live">✦ The agent is still editing this file; changes update live.</div>' : '')
            + (review.mixed ? '<div class="pending-diff-review-note">Some of your own edits sit inside agent changes and are shown with them.</div>' : '')
            + hunks.map((h) => '<section class="pending-diff-hunk is-review">'
                + '<div class="pending-diff-hunk-header"><span>' + esc(h.header) + '</span>'
                + '<span class="pending-diff-hunk-actions">'
                + '<button type="button" class="pending-diff-review-btn is-undo" data-review-kind="undo" data-review-hunk="' + esc(h.id) + '" title="Undo this change (rewrites the file)">Undo</button>'
                + '<button type="button" class="pending-diff-review-btn is-keep" data-review-kind="keep" data-review-hunk="' + esc(h.id) + '" title="Keep this change">Keep</button>'
                + '</span></div>'
                + '<div class="pending-diff-lines">' + h.lines.map(renderDiffLineHTML).join('') + '</div>'
                + '</section>').join('');
    }
    /** Agent mode for a pending file under review; false → fall back to the git diff. */
    async function loadReview(requestState, token) {
        const ra = reviewClient();
        if (!ra || requestState.commitHash) return false;
        const entry = await ra.entry(requestState.projectPath, requestState.repoRoot, requestState.file).catch(() => null);
        if (token !== generation) return true;
        if (!entry) { setReviewChrome(false, false); return false; }
        if (requestState.source === 'git') { setReviewChrome(false, true); return false; }
        const review = await ra.detail(requestState.projectPath, entry.path).catch(() => null);
        if (token !== generation) return true;
        if (!review) { setReviewChrome(false, false); return false; }
        state.review = { entry: entry, detail: review };
        state.source = 'agent';
        setReviewChrome(true, true);
        renderReview(entry, review);
        return true;
    }
    async function reviewAct(kind, hunkIds) {
        const ra = reviewClient();
        const snapshot = state;
        if (!ra || !snapshot || !snapshot.review) return;
        const entry = snapshot.review.entry;
        if (kind === 'undo' && !hunkIds) {
            const answer = await ra.confirmDialog({
                title: 'Undo agent changes?',
                text: entry.change === 'added' ? 'The agent created ' + snapshot.file + '. Undoing deletes the file.'
                    : 'Revert every agent change in ' + snapshot.file + ' on disk. Your own edits stay.',
                confirm: entry.change === 'added' ? 'Delete file' : 'Undo changes',
            });
            if (!answer.ok || state !== snapshot) return;
        }
        const body = { path: entry.path };
        if (hunkIds) body.hunks = hunkIds;
        if (kind === 'undo' && hunkIds) body.etag = snapshot.review.detail.etag;
        try {
            await ra.act(kind, snapshot.projectPath, body);
        } catch (err) {
            toast((err && err.conflict) ? 'This change moved; refreshed' : 'Agent review: ' + ((err && err.message) || err));
        }
        if (state === snapshot) loadFile(snapshot.file, { source: 'agent' });
    }

    function render(data) {
        lastData = data;
        const mode = readViewMode();
        modal.querySelectorAll('[data-diff-view]').forEach((btn) => {
            btn.setAttribute('aria-pressed', String(btn.dataset.diffView === mode));
        });
        el('title').textContent = state.file;
        el('title').setAttribute('aria-label', 'Open ' + state.file + ' in its default application');
        const status = String(data.status || 'modified');
        el('status').textContent = statusLabel(status);
        el('status').dataset.status = ({ A: 'added', D: 'deleted', R: 'renamed', C: 'copied', U: 'untracked', M: 'modified', '!': 'conflict' })[statusLabel(status)];
        el('stats').innerHTML = (data.additions ? '<span class="pending-changes-add">' + esc(formatStat(data.additions, '+')) + '</span>' : '')
            + (data.deletions ? '<span class="pending-changes-del">' + esc(formatStat(data.deletions, '-')) + '</span>' : '');
        el('loading').hidden = true;
        syncEditorButton(data);
        const sections = data.sections;
        const hasSections = !!(sections && (sections.added || sections.removed));
        const hunks = Array.isArray(data.hunks) ? data.hunks : [];
        const hasLines = (h) => Array.isArray(h.lines) && h.lines.length;
        const hasHunks = hunks.some(hasLines);
        const sectionHasLines = (s) => s && ((Number(s.total) || 0) > 0 || (Array.isArray(s.hunks) && s.hunks.some(hasLines)));
        const hasContent = hasSections ? sectionHasLines(sections.added) || sectionHasLines(sections.removed) : hasHunks;
        el('empty').hidden = !!hasContent;
        el('hunks').hidden = !hasContent;
        if (!hasContent) {
            el('empty').textContent = data.message || (data.secret_redacted ? 'Diff hidden for credential-like paths' : data.binary ? 'Binary file (no text preview)' : 'No line changes to show');
            el('hunks').innerHTML = '';
            return;
        }
        let html;
        if (hasSections && !(mode === 'unified' && hasHunks)) {
            html = renderDiffSectionHTML('added', sections.added, data) + renderDiffSectionHTML('removed', sections.removed, data);
        } else {
            html = renderDiffHunksHTML(hunks);
            if (data.truncated && !data.full) html += '<div class="pending-diff-truncated-note"><span>Diff truncated — file has more changes than shown.</span><span class="pending-diff-truncate-actions"><button type="button" class="pending-diff-expand-btn" data-pending-diff-more="all">Show more</button><button type="button" class="pending-diff-expand-btn" data-pending-diff-all="all">Show all</button></span></div>';
        }
        el('hunks').innerHTML = html;
    }
    async function loadFile(file, opts) {
        if (!state) return;
        state = Object.assign({}, state, opts || {}, { file: String(file) });
        lastData = null;
        const requestState = state;
        if (controller) controller.abort();
        controller = new AbortController();
        const signal = controller.signal;
        const token = ++generation;
        const timer = setTimeout(() => controller && token === generation && controller.abort(), 30000);
        el('title').textContent = requestState.file;
        el('source').textContent = requestState.commitHash ? requestState.commitHash.slice(0, 7) : '';
        el('title').title = requestState.commitHash ? 'Open the current working-tree file in its default application' : 'Open file in its default application';
        el('status').textContent = '';
        el('stats').textContent = '';
        el('editor').hidden = true;
        el('loading').hidden = false;
        el('empty').hidden = true;
        el('hunks').hidden = true;
        el('hunks').innerHTML = '';
        state.review = null;
        setReviewChrome(false, false);
        updateNavigation();
        // Agent mode wins when the file is under review; the git request still
        // goes out now so ordering and cancellation stay per request.
        const reviewing = requestState.commitHash ? Promise.resolve(false) : loadReview(requestState, token).catch(() => false);
        try {
            const qs = new URLSearchParams({ path: requestState.projectPath, file: requestState.file, context: '3', max_lines: String(requestState.maxLines) });
            if (requestState.repoRoot) qs.set('repo_root', requestState.repoRoot);
            if (requestState.full) qs.set('full', '1');
            const endpoint = requestState.commitHash ? '/api/git/commit/' + encodeURIComponent(requestState.commitHash) + '/file-diff' : '/api/git/pending-diff';
            const response = await fetch(endpoint + '?' + qs, { credentials: 'include', cache: 'no-store', signal: signal });
            const data = await response.json().catch(() => null);
            if (token !== generation || await reviewing || token !== generation) return;
            if (!response.ok || !data || !data.success) throw new Error((data && data.error) || ('HTTP ' + response.status));
            render(data);
        } catch (err) {
            if (token !== generation || await reviewing || token !== generation) return;
            const message = signal.aborted ? 'Diff request timed out' : (err && err.message) || 'Could not load diff';
            render({ path: requestState.file, hunks: [], message: message });
            toast(message);
        } finally {
            clearTimeout(timer);
            if (token === generation) el('loading').hidden = true;
        }
    }
    async function openFile() {
        if (!state) return;
        const snapshot = state;
        try {
            const response = await fetch('/api/git/open-file', {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: snapshot.projectPath, repo_root: snapshot.repoRoot || undefined, file: snapshot.file }),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok || !data || !data.success) throw new Error((data && data.error) || ('HTTP ' + response.status));
        } catch (err) { toast('Could not open file: ' + ((err && err.message) || err)); }
    }
    function ideEnabled() {
        if (!ideFlag) {
            ideFlag = fetch('/api/experimental/flags', { credentials: 'include', cache: 'no-store' })
                .then((r) => (r.ok ? r.json() : null))
                .then((d) => !!(d && !d.kill_switch && (d.flags || []).some((f) => f.id === 'ide' && f.enabled)))
                .catch(() => false);
        }
        return ideFlag;
    }
    /** Text files that still exist can open in the IDE (experimental `ide`). */
    function syncEditorButton(data) {
        const btn = el('editor');
        btn.hidden = true;
        const editable = data && data.success === true && !data.binary && !data.secret_redacted && statusLabel(data.status) !== 'D';
        if (!editable) return;
        const snapshot = state;
        ideEnabled().then((on) => { if (on && state === snapshot) btn.hidden = false; });
    }
    function openInEditor(options) {
        const file = options && options.file ? options : state;
        if (!file) return;
        const qs = new URLSearchParams({ path: file.projectPath, file: file.file });
        if (file.repoRoot) qs.set('repo_root', file.repoRoot);
        const page = '/ide_page.html?' + qs.toString();
        close();
        const localIde = global.location.pathname === '/ide_page.html';
        const shell = document.getElementById('splitContainer');
        if (localIde || shell) global.postMessage({ type: 'cuttle-open-editor', page: page }, global.location.origin);
        else if (global.parent && global.parent !== global) global.parent.postMessage({ type: 'cuttle-open-editor', page: page }, global.location.origin);
        else global.location.href = page;
    }
    function open(options) {
        if (!options || !options.projectPath || !options.file) return Promise.resolve();
        // Re-read the flag per popup so an in-page toggle shows/hides the button.
        ideFlag = null;
        ensureModal();
        const files = Array.from(new Set((options.files || []).map((f) => typeof f === 'string' ? f : f.path).filter(Boolean)));
        if (!files.includes(options.file)) files.unshift(options.file);
        if (!isOpen()) { returnFocus = document.activeElement; modal.showModal(); }
        state = { projectPath: String(options.projectPath), repoRoot: options.repoRoot || null,
            commitHash: options.commitHash || null, files: files, file: String(options.file),
            full: !!options.full, maxLines: options.full ? 100000 : Math.max(50, Math.min(Number(options.maxLines) || 300, 100000)),
            source: options.source || null, review: null };
        el('close').focus();
        return loadFile(state.file);
    }
    global.CuttleDiffModal = { open: open, close: close, isOpen: isOpen, navigate: navigate, openInEditor: openInEditor };
})(typeof window !== 'undefined' ? window : this);
