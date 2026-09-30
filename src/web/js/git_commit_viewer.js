/* Shared Git commit detail + file-diff modal (Git page drawer + chat overlay). */
(function (global) {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function isGitHashToken(raw) {
        const s = String(raw || '').trim();
        if (!/^[0-9a-f]{7,40}$/i.test(s)) return false;
        return /[a-f]/i.test(s);
    }

    /** Bare 7 (git default abbrev) or 9–40. Skip 8-char — Cuttle query ids. */
    function shouldLinkifyBareGitHash(raw) {
        const s = String(raw || '').trim();
        if (!isGitHashToken(s)) return false;
        const n = s.length;
        return n === 7 || (n >= 9 && n <= 40);
    }

    /** Backticks around a hash are an intentional citation (7–40). */
    function shouldLinkifyInlineCodeGitHash(raw) {
        return isGitHashToken(raw);
    }

    function renderGitHashLink(raw) {
        const hash = String(raw || '').trim();
        const safe = esc(hash);
        return (
            '<a class="git-commit-link" href="#git-commit-' + safe + '" '
            + 'data-git-hash="' + safe + '" '
            + 'title="View commit ' + safe + '">' + safe + '</a>'
        );
    }

    /**
     * Turn git SHAs into chips. Mutates linkChips in place.
     * Inline `hash` (7–40) and bare 7 / 9–40 hex tokens.
     */
    function linkifyGitHashesInText(text, linkChips, renderLink) {
        if (typeof text !== 'string' || !text) return text;
        const chips = Array.isArray(linkChips) ? linkChips : [];
        const render = typeof renderLink === 'function' ? renderLink : renderGitHashLink;
        const inlineCodeHold = [];
        let out = text.replace(/`([^`\n]+)`/g, function (_, code) {
            if (shouldLinkifyInlineCodeGitHash(code)) {
                const placeholder = '{{CUTTLE_LINK_' + chips.length + '}}';
                chips.push(render(String(code).trim()));
                return placeholder;
            }
            const ph = '{{CUTTLE_ICG_' + inlineCodeHold.length + '}}';
            inlineCodeHold.push('`' + code + '`');
            return ph;
        });
        out = out.replace(/\b([0-9a-fA-F]{7,40})\b/g, function (match) {
            if (!shouldLinkifyBareGitHash(match)) return match;
            const placeholder = '{{CUTTLE_LINK_' + chips.length + '}}';
            chips.push(render(match));
            return placeholder;
        });
        for (let i = 0; i < inlineCodeHold.length; i++) {
            out = out.split('{{CUTTLE_ICG_' + i + '}}').join(inlineCodeHold[i]);
        }
        return out;
    }

    function renderDetailHTML(d) {
        const fileList = Array.isArray(d && d.files) ? d.files : [];
        const files = fileList.map(function (f) {
            const st = String(f.status || '');
            const path = String(f.path || '');
            return (
                '<li>'
                + '<button type="button" class="git-graph-file-btn" data-rel-path="' + esc(path) + '" data-status="' + esc(st) + '" title="View diff">'
                + '<span class="git-graph-file-st">' + esc(st) + '</span>'
                + '<span class="git-graph-file-path">' + esc(path) + '</span>'
                + '</button></li>'
            );
        }).join('');
        const refs = ((d && d.refs) || []).map(function (r) {
            return '<span class="git-graph-ref' + (r === 'HEAD' ? ' is-head' : '') + '">' + esc(r) + '</span>';
        }).join(' ');
        return (
            '<p class="git-graph-detail-subject">' + esc((d && d.subject) || '') + '</p>'
            + ((d && d.body)
                ? '<p class="git-graph-muted" style="white-space:pre-wrap;margin:0 0 12px">' + esc(d.body) + '</p>'
                : '')
            + '<dl class="git-graph-detail-kv">'
            + '<dt>Hash</dt><dd>' + esc(d && d.hash) + '</dd>'
            + '<dt>Author</dt><dd>' + esc(d && d.author)
            + ((d && d.email) ? ' &lt;' + esc(d.email) + '&gt;' : '') + '</dd>'
            + '<dt>Date</dt><dd>' + esc(d && d.date) + '</dd>'
            + '<dt>Parents</dt><dd>' + esc(((d && d.parents) || []).map(function (p) {
                return String(p).slice(0, 7);
            }).join(', ') || '—') + '</dd>'
            + (refs ? '<dt>Refs</dt><dd>' + refs + '</dd>' : '')
            + '</dl>'
            + '<h3 style="font-size:12px;margin:0 0 6px">Files</h3>'
            + (files
                ? '<ul class="git-graph-file-list" data-commit-hash="' + esc((d && d.hash) || '') + '">' + files + '</ul>'
                : '<p class="git-graph-muted">No file list</p>')
            + ((d && d.stat)
                ? '<h3 style="font-size:12px;margin:12px 0 6px">Stat</h3><pre class="git-graph-patch">'
                + esc(d.stat) + '</pre>'
                : '')
        );
    }

    function statusBadgeLabel(status) {
        const s = String(status || '').toLowerCase();
        if (s === 'a' || s === 'added') return 'added';
        if (s === 'd' || s === 'deleted') return 'deleted';
        if (s === 'r' || s === 'renamed' || s.startsWith('r')) return 'renamed';
        if (s === 'c' || s === 'copied' || s.startsWith('c')) return 'copied';
        if (s === 'm' || s === 'modified') return 'modified';
        return s || 'modified';
    }

    function formatDiffStat(n, sign) {
        const v = Number(n);
        if (!Number.isFinite(v) || v <= 0) return '';
        return sign + String(Math.floor(v));
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
        return list.map(function (hunk) {
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
        const hasLines = hunks.some(function (h) {
            return Array.isArray(h.lines) && h.lines.length;
        });
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
                + '<button type="button" class="pending-diff-expand-btn" data-diff-more="'
                + esc(kind)
                + '">Show more</button>'
                + '<button type="button" class="pending-diff-expand-btn" data-diff-all="'
                + esc(kind)
                + '">Show all</button>'
                + '</span>'
                + '</div>';
        }
        html += '</section>';
        return html;
    }

    function showDialog(modal) {
        if (!modal) return;
        try {
            if (typeof modal.showModal === 'function') {
                if (!modal.open) modal.showModal();
            } else {
                modal.setAttribute('open', '');
                modal.removeAttribute('hidden');
            }
        } catch (_) {
            modal.setAttribute('open', '');
            modal.removeAttribute('hidden');
        }
    }

    function hideDialog(modal) {
        if (!modal) return;
        if (typeof modal.close === 'function' && modal.open) {
            try { modal.close(); } catch (_) {}
        }
        modal.removeAttribute('open');
        if (modal.hasAttribute('hidden')) return;
        if (!modal.matches || !modal.matches('dialog')) {
            modal.setAttribute('hidden', '');
        }
    }

    function create(options) {
        options = options || {};
        const els = options.els || {};
        const getProjectPath = options.getProjectPath || function () { return ''; };
        const getRepoRoot = options.getRepoRoot || function () { return null; };
        const toast = options.toast || global.showToast || function () {};

        let diffModalFiles = [];
        let diffModalHash = null;
        let diffModalSource = 'commit';
        let diffModalRepoRoot = null;
        let diffFetchGen = 0;
        let diffModalOpts = { full: false, maxLines: 300 };
        let bound = false;

        function renderDetail(d) {
            const fileList = Array.isArray(d && d.files) ? d.files : [];
            if (els.detailTitle) {
                els.detailTitle.textContent = (d && d.short) || String((d && d.hash) || '').slice(0, 7);
            }
            if (els.detailBody) {
                els.detailBody.innerHTML = renderDetailHTML(d);
                els.detailBody.dataset.commitFiles = JSON.stringify(fileList);
                els.detailBody.dataset.commitHash = String((d && d.hash) || '');
            }
        }

        function bindDiffExpandButtons(data) {
            if (!els.diffHunks) return;
            const currentMax = Math.max(
                50,
                Number((data && data.max_lines_per_side) || diffModalOpts.maxLines || 300)
            );
            els.diffHunks.querySelectorAll('[data-diff-more]').forEach(function (btn) {
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    diffModalOpts = {
                        full: false,
                        maxLines: Math.min(currentMax * 3, 100000),
                    };
                    loadDiffForSelectedFile();
                });
            });
            els.diffHunks.querySelectorAll('[data-diff-all]').forEach(function (btn) {
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    diffModalOpts = { full: true, maxLines: 100000 };
                    loadDiffForSelectedFile();
                });
            });
        }

        function setDiffModalLoading(loading) {
            if (els.diffLoading) els.diffLoading.hidden = !loading;
            if (loading) {
                if (els.diffEmpty) {
                    els.diffEmpty.hidden = true;
                    els.diffEmpty.textContent = '';
                }
                if (els.diffHunks) {
                    els.diffHunks.hidden = true;
                    els.diffHunks.innerHTML = '';
                }
            }
        }

        function populateDiffFileSelect(files, selectedPath) {
            if (!els.diffFileSelect) return;
            const list = Array.isArray(files) ? files : [];
            els.diffFileSelect.innerHTML = list.map(function (f) {
                const path = String(f.path || '');
                const st = String(f.status || '');
                const label = (st ? st + '  ' : '') + path;
                return '<option value="' + esc(path) + '">' + esc(label) + '</option>';
            }).join('');
            if (selectedPath) els.diffFileSelect.value = selectedPath;
            els.diffFileSelect.disabled = list.length <= 1;
        }

        function renderDiffModal(data) {
            if (els.diffOpenFile) {
                const path = String((data && data.path) || (els.diffFileSelect && els.diffFileSelect.value) || '');
                els.diffOpenFile.textContent = path || 'Open file';
                els.diffOpenFile.setAttribute('aria-label', path ? 'Open ' + path + ' in its default application' : 'Open file');
                els.diffOpenFile.setAttribute('title', 'Open in its default application');
                els.diffOpenFile.classList.toggle('is-disabled', !path);
                els.diffOpenFile.setAttribute('aria-disabled', path ? 'false' : 'true');
            }
            const status = statusBadgeLabel((data && data.status) || 'modified');
            if (els.diffStatus) {
                els.diffStatus.textContent = status;
                els.diffStatus.setAttribute('data-status', status);
            }
            if (els.diffStats) {
                const a = formatDiffStat(data && data.additions, '+');
                const dlt = formatDiffStat(data && data.deletions, '-');
                els.diffStats.innerHTML =
                    (a ? '<span class="pending-changes-add">' + esc(a) + '</span>' : '')
                    + (dlt ? '<span class="pending-changes-del">' + esc(dlt) + '</span>' : '');
            }
            if (els.diffLoading) els.diffLoading.hidden = true;

            const message = String((data && data.message) || '').trim();
            const sections = data && data.sections;
            const hasSections = !!(sections && (sections.added || sections.removed));
            const hunks = Array.isArray(data && data.hunks) ? data.hunks : [];
            const hasLegacyHunks = hunks.some(function (h) {
                return Array.isArray(h.lines) && h.lines.length;
            });
            const sectionHasLines = function (sec) {
                return sec && (
                    (Number(sec.total) || 0) > 0
                    || (Array.isArray(sec.hunks) && sec.hunks.some(function (h) {
                        return Array.isArray(h.lines) && h.lines.length;
                    }))
                );
            };
            const hasContent = hasSections
                ? (sectionHasLines(sections.added) || sectionHasLines(sections.removed))
                : hasLegacyHunks;

            if (els.diffEmpty) {
                if (hasContent) {
                    els.diffEmpty.hidden = true;
                    els.diffEmpty.textContent = '';
                } else {
                    els.diffEmpty.hidden = false;
                    if (data && data.secret_redacted) {
                        els.diffEmpty.textContent = message || 'Diff hidden for credential-like paths';
                    } else if (data && data.binary) {
                        els.diffEmpty.textContent = message || 'Binary file (no text preview)';
                    } else {
                        els.diffEmpty.textContent = message || 'No line changes to show';
                    }
                }
            }
            if (els.diffHunks) {
                if (hasContent) {
                    let html = '';
                    if (hasSections) {
                        html += renderDiffSectionHTML('added', sections.added, data);
                        html += renderDiffSectionHTML('removed', sections.removed, data);
                    } else {
                        html = renderDiffHunksHTML(hunks);
                        if (data && data.truncated) {
                            html +=
                                '<div class="pending-diff-truncated-note">'
                                + '<span>Diff truncated — file has more changes than shown.</span>'
                                + '<span class="pending-diff-truncate-actions">'
                                + '<button type="button" class="pending-diff-expand-btn" data-diff-more="all">Show more</button>'
                                + '<button type="button" class="pending-diff-expand-btn" data-diff-all="all">Show all</button>'
                                + '</span>'
                                + '</div>';
                        }
                    }
                    els.diffHunks.innerHTML = html;
                    els.diffHunks.hidden = false;
                    bindDiffExpandButtons(data);
                } else {
                    els.diffHunks.hidden = true;
                    els.diffHunks.innerHTML = '';
                }
            }
        }

        function closeDiffModal() {
            diffFetchGen += 1;
            diffModalFiles = [];
            diffModalHash = null;
            diffModalSource = 'commit';
            diffModalRepoRoot = null;
            diffModalOpts = { full: false, maxLines: 300 };
            hideDialog(els.diffModal);
        }

        async function loadDiffForSelectedFile() {
            const relPath = els.diffFileSelect ? els.diffFileSelect.value : '';
            const path = getProjectPath();
            if (!relPath || !path) return;
            if (diffModalSource === 'commit' && !diffModalHash) return;

            setDiffModalLoading(true);
            if (els.diffStatus) {
                els.diffStatus.textContent = '';
                els.diffStatus.removeAttribute('data-status');
            }
            if (els.diffStats) els.diffStats.textContent = '';

            const fetchGen = ++diffFetchGen;
            try {
                const qs = new URLSearchParams({
                    path: path,
                    file: relPath,
                    context: '3',
                    max_lines: String(diffModalOpts.maxLines || 300),
                });
                if (diffModalOpts.full) qs.set('full', '1');
                const activeRepo = diffModalSource === 'pending'
                    ? (diffModalRepoRoot || getRepoRoot())
                    : getRepoRoot();
                if (activeRepo) qs.set('repo_root', activeRepo);
                const url = diffModalSource === 'pending'
                    ? ('/api/git/pending-diff?' + qs.toString())
                    : ('/api/git/commit/' + encodeURIComponent(diffModalHash) + '/file-diff?' + qs.toString());
                const resp = await fetch(url, { credentials: 'include', cache: 'no-store' });
                const data = await resp.json().catch(function () { return null; });
                if (fetchGen !== diffFetchGen) return;
                if (!data || !data.success) {
                    const err = (data && data.error) || 'Could not load diff';
                    renderDiffModal({ path: relPath, hunks: [], message: err });
                    toast(err, 'error');
                    return;
                }
                renderDiffModal(data);
            } catch (e) {
                if (fetchGen !== diffFetchGen) return;
                const err = (e && e.message) || 'Diff failed';
                renderDiffModal({ path: relPath, hunks: [], message: err });
                toast(err, 'error');
            } finally {
                if (fetchGen === diffFetchGen) setDiffModalLoading(false);
            }
        }

        async function openSelectedFile() {
            const relPath = els.diffFileSelect ? els.diffFileSelect.value : '';
            const path = getProjectPath();
            if (!relPath || !path) return;
            try {
                const resp = await fetch('/api/git/open-file', {
                    method: 'POST', credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: path, repo_root: diffModalRepoRoot || getRepoRoot(), file: relPath }),
                });
                const data = await resp.json().catch(function () { return null; });
                if (!resp.ok || !data || !data.success) throw new Error((data && data.error) || ('HTTP ' + resp.status));
            } catch (e) {
                toast('Could not open file: ' + ((e && e.message) || e), 'error');
            }
        }

        function openDiffModalShell(files, initialPath, meta) {
            const list = Array.isArray(files) ? files.filter(function (f) { return f && f.path; }) : [];
            if (!list.length) return;
            const modal = els.diffModal;
            if (!modal) return;

            diffModalOpts = { full: false, maxLines: 300 };

            if (modal.parentElement !== document.body) {
                document.body.appendChild(modal);
            }

            diffModalFiles = list;
            const startPath = initialPath && list.some(function (f) { return f.path === initialPath; })
                ? initialPath
                : list[0].path;

            populateDiffFileSelect(list, startPath);
            if (els.diffCommit) {
                els.diffCommit.textContent = (meta && meta.label) ? String(meta.label) : '';
            }
            setDiffModalLoading(true);
            showDialog(modal);
            loadDiffForSelectedFile();
            if (els.diffClose) els.diffClose.focus();
        }

        function openCommitDiffModal(hash, files, initialPath) {
            if (!hash) return;
            diffModalSource = 'commit';
            diffModalHash = hash;
            diffModalRepoRoot = null;
            openDiffModalShell(files, initialPath, { label: String(hash).slice(0, 7) });
        }

        function openPendingDiffModal(relPath, meta) {
            if (!relPath) return;
            diffModalSource = 'pending';
            diffModalHash = null;
            const repoRoot = meta && meta.repoRoot ? String(meta.repoRoot) : null;
            diffModalRepoRoot = repoRoot;
            let files = [];
            try {
                const sel = repoRoot
                    ? '.pending-changes[data-repo-root="' + CSS.escape(repoRoot) + '"] .pending-changes-row[data-rel-path]'
                    : '#pendingChangesHost .pending-changes-row[data-rel-path]';
                const rows = document.querySelectorAll(sel);
                if (rows && rows.length) {
                    files = Array.from(rows).map(function (row) {
                        return {
                            path: row.getAttribute('data-rel-path') || '',
                            status: '',
                        };
                    }).filter(function (f) { return f.path; });
                }
            } catch (_) {}
            if (!files.some(function (f) { return f.path === relPath; })) {
                files = [{ path: relPath, status: '' }].concat(files);
            }
            if (!files.length) files = [{ path: relPath, status: '' }];
            openDiffModalShell(files, relPath, {
                label: 'working tree',
                repoRoot: repoRoot,
            });
        }

        function onDetailFileClick(e) {
            const btn = e.target && e.target.closest
                ? e.target.closest('.git-graph-file-btn')
                : null;
            if (!btn) return;
            const relPath = btn.getAttribute('data-rel-path') || '';
            let files = [];
            try {
                files = JSON.parse((els.detailBody && els.detailBody.dataset.commitFiles) || '[]');
            } catch (_) {
                files = [];
            }
            const hash = (els.detailBody && els.detailBody.dataset.commitHash) || '';
            if (!files.length) {
                files = [{ path: relPath, status: btn.getAttribute('data-status') || '' }];
            }
            openCommitDiffModal(hash, files, relPath);
        }

        async function loadCommit(hash) {
            const h = String(hash || '').trim();
            if (!h) return null;
            if (els.detailTitle) els.detailTitle.textContent = h.slice(0, 7);
            if (els.detailBody) {
                els.detailBody.innerHTML = '<p class="git-graph-muted">Loading…</p>';
            }
            const path = getProjectPath();
            if (!path) {
                const err = 'No project selected';
                if (els.detailBody) {
                    els.detailBody.innerHTML = '<p class="git-graph-muted is-error">' + esc(err) + '</p>';
                }
                toast(err, 'error');
                return null;
            }
            const qs = new URLSearchParams({ path: path });
            const repoRoot = getRepoRoot();
            if (repoRoot) qs.set('repo_root', repoRoot);
            try {
                const resp = await fetch(
                    '/api/git/commit/' + encodeURIComponent(h) + '/detail?' + qs.toString(),
                    { credentials: 'include' }
                );
                const data = await resp.json().catch(function () { return null; });
                if (!data || !data.success) {
                    const err = (data && data.error) || 'Failed to load commit';
                    if (els.detailBody) {
                        els.detailBody.innerHTML = '<p class="git-graph-muted is-error">' + esc(err) + '</p>';
                    }
                    return null;
                }
                renderDetail(data);
                return data;
            } catch (e) {
                const err = (e && e.message) || 'Error';
                if (els.detailBody) {
                    els.detailBody.innerHTML = '<p class="git-graph-muted">' + esc(err) + '</p>';
                }
                return null;
            }
        }

        function bind() {
            if (bound) return;
            bound = true;
            if (els.detailBody) {
                els.detailBody.addEventListener('click', onDetailFileClick);
            }
            if (els.diffClose) {
                els.diffClose.addEventListener('click', function (e) {
                    e.stopPropagation();
                    closeDiffModal();
                });
            }
            if (els.diffModal) {
                els.diffModal.addEventListener('cancel', function (e) {
                    e.preventDefault();
                    closeDiffModal();
                });
                els.diffModal.addEventListener('click', function (e) {
                    if (e.target === els.diffModal) closeDiffModal();
                });
                els.diffModal.addEventListener('close', function () {
                    diffFetchGen += 1;
                    diffModalFiles = [];
                    diffModalHash = null;
                    diffModalSource = 'commit';
                });
            }
            if (els.diffFileSelect) {
                els.diffFileSelect.addEventListener('change', function () {
                    diffModalOpts = { full: false, maxLines: 300 };
                    loadDiffForSelectedFile();
                });
            }
            if (els.diffOpenFile) els.diffOpenFile.addEventListener('click', function (e) {
                e.preventDefault();
                if (!els.diffOpenFile.classList.contains('is-disabled')) openSelectedFile();
            });
        }

        return {
            renderDetail: renderDetail,
            loadCommit: loadCommit,
            openCommitDiffModal: openCommitDiffModal,
            openPendingDiffModal: openPendingDiffModal,
            closeDiffModal: closeDiffModal,
            bind: bind,
        };
    }

    let chatViewer = null;

    function chatModalMarkup() {
        return (
            '<dialog class="pending-diff-modal git-commit-modal" id="gitCommitModal" aria-labelledby="gitCommitModalTitle">'
            + '<div class="pending-diff-modal-card git-commit-modal-card">'
            + '<header class="pending-diff-modal-header git-commit-modal-header">'
            + '<h2 class="pending-diff-modal-title" id="gitCommitModalTitle">Commit</h2>'
            + '<button type="button" class="pending-diff-modal-close" id="gitCommitModalClose" title="Close commit" aria-label="Close commit">×</button>'
            + '</header>'
            + '<div class="pending-diff-modal-body git-graph-drawer-body" id="gitCommitModalBody">'
            + '<p class="git-graph-muted">Select a commit.</p>'
            + '</div></div></dialog>'
            + '<dialog class="pending-diff-modal" id="gitDiffModal" aria-labelledby="gitDiffTitle">'
            + '<div class="pending-diff-modal-card">'
            + '<header class="pending-diff-modal-header">'
            + '<div class="pending-diff-modal-heading">'
            + '<div class="git-diff-file-row">'
            + '<span class="pending-diff-modal-title sr-only" id="gitDiffTitle">File diff</span>'
            + '<a href="#" class="git-diff-open-file" id="gitDiffOpenFile" title="Open file in its default application">Open file</a>'
            + '<label class="git-diff-file-picker" for="gitDiffFileSelect" title="Switch changed file">'
            + '<select id="gitDiffFileSelect" class="git-diff-file-select" aria-label="Switch changed file"></select>'
            + '</label></div>'
            + '<div class="pending-diff-modal-meta">'
            + '<span class="pending-diff-modal-badge" id="gitDiffStatus"></span>'
            + '<span class="pending-diff-modal-stats" id="gitDiffStats"></span>'
            + '<span class="git-diff-commit" id="gitDiffCommit"></span>'
            + '</div></div>'
            + '<button type="button" class="pending-diff-modal-close" id="gitDiffClose" title="Close diff" aria-label="Close diff">×</button>'
            + '</header>'
            + '<div class="pending-diff-modal-body" id="gitDiffBody">'
            + '<div class="pending-diff-modal-loading" id="gitDiffLoading" hidden>'
            + '<span class="pending-diff-modal-spinner" aria-hidden="true"></span>'
            + '<span>Loading diff…</span></div>'
            + '<div class="pending-diff-modal-empty" id="gitDiffEmpty" hidden></div>'
            + '<div class="pending-diff-modal-hunks" id="gitDiffHunks" hidden></div>'
            + '</div></div></dialog>'
        );
    }

    function ensureChatModals() {
        if (document.getElementById('gitCommitModal') && document.getElementById('gitDiffModal')) {
            return;
        }
        const wrap = document.createElement('div');
        wrap.innerHTML = chatModalMarkup();
        while (wrap.firstChild) document.body.appendChild(wrap.firstChild);
    }

    function initChat(options) {
        options = options || {};
        ensureChatModals();
        chatViewer = create({
            getProjectPath: options.getProjectPath,
            getRepoRoot: options.getRepoRoot,
            toast: options.toast,
            els: {
                detailTitle: document.getElementById('gitCommitModalTitle'),
                detailBody: document.getElementById('gitCommitModalBody'),
                diffModal: document.getElementById('gitDiffModal'),
                diffFileSelect: document.getElementById('gitDiffFileSelect'),
                diffOpenFile: document.getElementById('gitDiffOpenFile'),
                diffStatus: document.getElementById('gitDiffStatus'),
                diffStats: document.getElementById('gitDiffStats'),
                diffCommit: document.getElementById('gitDiffCommit'),
                diffLoading: document.getElementById('gitDiffLoading'),
                diffEmpty: document.getElementById('gitDiffEmpty'),
                diffHunks: document.getElementById('gitDiffHunks'),
                diffClose: document.getElementById('gitDiffClose'),
            },
        });
        chatViewer.bind();
        const commitModal = document.getElementById('gitCommitModal');
        const commitClose = document.getElementById('gitCommitModalClose');
        if (commitClose) {
            commitClose.addEventListener('click', function (e) {
                e.stopPropagation();
                if (chatViewer) chatViewer.closeDiffModal();
                hideDialog(commitModal);
            });
        }
        if (commitModal) {
            commitModal.addEventListener('cancel', function (e) {
                e.preventDefault();
                if (chatViewer) chatViewer.closeDiffModal();
                hideDialog(commitModal);
            });
            commitModal.addEventListener('click', function (e) {
                if (e.target === commitModal) {
                    if (chatViewer) chatViewer.closeDiffModal();
                    hideDialog(commitModal);
                }
            });
        }
        return chatViewer;
    }

    async function openFromChat(opts) {
        opts = opts || {};
        const hash = String(opts.hash || '').trim();
        if (!hash) return;
        if (!chatViewer) {
            initChat({
                getProjectPath: opts.getProjectPath,
                getRepoRoot: opts.getRepoRoot,
                toast: opts.toast,
            });
        }
        const modal = document.getElementById('gitCommitModal');
        showDialog(modal);
        await chatViewer.loadCommit(hash);
    }

    global.CuttleGitCommitViewer = {
        esc: esc,
        renderDetailHTML: renderDetailHTML,
        isGitHashToken: isGitHashToken,
        shouldLinkifyBareGitHash: shouldLinkifyBareGitHash,
        shouldLinkifyInlineCodeGitHash: shouldLinkifyInlineCodeGitHash,
        linkifyGitHashesInText: linkifyGitHashesInText,
        renderGitHashLink: renderGitHashLink,
        create: create,
        initChat: initChat,
        openFromChat: openFromChat,
    };
})(typeof window !== 'undefined' ? window : this);
