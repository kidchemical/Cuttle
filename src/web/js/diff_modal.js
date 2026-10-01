/* Shared file-diff popup. Owns markup, rendering, navigation and desktop opening.
 * Pages pass an explicit project/repo snapshot; pending and commit diffs use one UI.
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
                            <div class="pending-diff-view-toggle" role="group" aria-label="Diff view mode">
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
        ['prev', 'sidePrev'].forEach((key) => el(key).addEventListener('click', () => navigate(-1)));
        ['next', 'sideNext'].forEach((key) => el(key).addEventListener('click', () => navigate(1)));
        modal.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
        modal.addEventListener('close', () => { if (state && !modal.open) close(); });
        modal.addEventListener('click', (e) => {
            if (e.target === modal) close();
            const view = e.target.closest('[data-diff-view]');
            if (view) { writeViewMode(view.dataset.diffView); if (lastData) render(lastData); }
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
        el('loading').hidden = false;
        el('empty').hidden = true;
        el('hunks').hidden = true;
        el('hunks').innerHTML = '';
        updateNavigation();
        try {
            const qs = new URLSearchParams({ path: requestState.projectPath, file: requestState.file, context: '3', max_lines: String(requestState.maxLines) });
            if (requestState.repoRoot) qs.set('repo_root', requestState.repoRoot);
            if (requestState.full) qs.set('full', '1');
            const endpoint = requestState.commitHash ? '/api/git/commit/' + encodeURIComponent(requestState.commitHash) + '/file-diff' : '/api/git/pending-diff';
            const response = await fetch(endpoint + '?' + qs, { credentials: 'include', cache: 'no-store', signal: signal });
            const data = await response.json().catch(() => null);
            if (token !== generation) return;
            if (!response.ok || !data || !data.success) throw new Error((data && data.error) || ('HTTP ' + response.status));
            render(data);
        } catch (err) {
            if (token !== generation) return;
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
    function open(options) {
        if (!options || !options.projectPath || !options.file) return Promise.resolve();
        ensureModal();
        const files = Array.from(new Set((options.files || []).map((f) => typeof f === 'string' ? f : f.path).filter(Boolean)));
        if (!files.includes(options.file)) files.unshift(options.file);
        if (!isOpen()) { returnFocus = document.activeElement; modal.showModal(); }
        state = { projectPath: String(options.projectPath), repoRoot: options.repoRoot || null,
            commitHash: options.commitHash || null, files: files, file: String(options.file),
            full: !!options.full, maxLines: options.full ? 100000 : Math.max(50, Math.min(Number(options.maxLines) || 300, 100000)) };
        el('close').focus();
        return loadFile(state.file);
    }
    global.CuttleDiffModal = { open: open, close: close, isOpen: isOpen, navigate: navigate };
})(typeof window !== 'undefined' ? window : this);
