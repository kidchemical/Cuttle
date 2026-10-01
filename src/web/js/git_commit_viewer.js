/* Shared Git commit detail (Git page drawer + chat overlay).
 * File diffs delegate to CuttleDiffModal. */
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

        function openCommitDiffModal(hash, files, initialPath) {
            if (!hash) return;
            const list = (files || []).filter((f) => f && f.path);
            const file = initialPath || (list[0] && list[0].path);
            return global.CuttleDiffModal.open({
                projectPath: getProjectPath(), repoRoot: getRepoRoot(),
                file: file, files: list, commitHash: hash,
            });
        }

        function openPendingDiffModal(relPath, meta) {
            const root = (meta && meta.repoRoot) || getRepoRoot();
            return global.CuttleDiffModal.open({
                projectPath: getProjectPath(), repoRoot: root, file: relPath,
                files: options.getPendingFiles ? options.getPendingFiles(root) : [relPath],
            });
        }

        function closeDiffModal() { global.CuttleDiffModal.close(); }

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
        );
    }

    function ensureChatModals() {
        if (document.getElementById('gitCommitModal')) {
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
