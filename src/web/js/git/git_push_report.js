/* Shared redacted push-blocker report + git push menu/action family for the
   Git UI, chat menu and action cards.

   CuttleGitPushReport owns:
     - render/show/fromAction: the redacted push-blocker report popup.
     - create(host): one per chat frame. Owns the git push menu HTML/mode,
       the repo cache (known/fetch), the multi-repo submenu, the push
       confirm modal, and the push request itself. The page supplies project
       identity, the pending-changes repo snapshot, DOM/toast/close effects
       and the inline escape as an explicit host object.
   ================================================================ */
(function (root) {
    'use strict';
    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g,
        c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    function render(report) {
        const findings = Array.isArray(report.findings) ? report.findings : [];
        return '<h2 id="push-report-title">Push blocked by pre-push checks</h2>' +
            '<p>' + esc(report.summary || 'The pre-push secret hook blocked this push.') + '</p>' +
            '<p>No changes were sent by this rejected ref update. Flagged values are redacted. ' +
            'Review the original commit locally before deciding how to resolve each finding.</p>' +
            '<p><strong>' + findings.length + ' findings · ' + new Set(findings.map(f => f.file).filter(Boolean)).size + ' files</strong></p>' +
            findings.map((f, index) => '<details open style="border:1px solid #555;border-radius:8px;margin:10px 0;padding:12px">' +
                '<summary><strong>' + esc(f.file || 'Inspection failure') + '</strong>' + (f.line ? ':' + esc(f.line) : '') + ' — ' + esc(f.hook) + '</summary>' +
                '<p>' + esc(f.rule) + '</p><p>Commit: <code style="user-select:all">' + esc(f.commit || 'Unknown') + '</code></p>' +
                (f.table ? '<p>Table: <code>' + esc(f.table) + '</code> · column: <code>' + esc(f.column) + '</code> · row position: ' + esc(f.row) + '</p>' : '') +
                (f.file && f.commit ? '<button type="button" data-push-diff="' + index + '">View flagged commit diff</button>' : '') +
                '<pre style="overflow:auto;background:#242428;padding:12px;border-left:3px solid #d77"><span style="color:#aaa">' + esc(f.line || (f.table ? 'DB' : '—')) + '</span>  ' + esc(f.preview || '[No source value shown]') + '</pre></details>').join('') +
            (report.warnings || []).map(w => '<p>Warning: ' + esc(w) + '</p>').join('');
    }
    function show(report, context) {
        if (!report || !Array.isArray(report.findings)) return false;
        context = context || {};
        const prior = root.document.getElementById('cuttle-push-report');
        if (prior) prior.close();
        const focus = root.document.activeElement;
        const dialog = root.document.createElement('dialog');
        dialog.id = 'cuttle-push-report';
        dialog.setAttribute('aria-labelledby', 'push-report-title');
        dialog.style.cssText = 'width:min(950px,90vw);max-height:85vh;overflow:auto;background:var(--bg-secondary,#202024);color:var(--text-primary,#eee);border:1px solid #666;border-radius:12px;padding:24px;font-family:system-ui,sans-serif;box-shadow:0 12px 60px #0008';
        dialog.innerHTML = '<button type="button" style="float:right">Close</button>' + render(report);
        dialog.querySelectorAll('button').forEach(button => {
            button.style.cssText = 'padding:7px 12px;border:1px solid #777;border-radius:6px;background:#34343b;color:#eee;cursor:pointer';
        });
        dialog.querySelector('button').addEventListener('click', () => dialog.close());
        dialog.querySelectorAll('[data-push-diff]').forEach(button => {
            const f = report.findings[Number(button.dataset.pushDiff)];
            button.disabled = !context.projectPath || !root.CuttleDiffModal;
            button.addEventListener('click', () => root.CuttleDiffModal.open({
                projectPath: context.projectPath, repoRoot: context.repoRoot || context.projectPath,
                file: f.file, files: report.findings.filter(item => item.commit === f.commit).map(item => item.file),
                commitHash: f.commit,
            }));
        });
        dialog.addEventListener('close', () => { dialog.remove(); if (focus && focus.isConnected) focus.focus(); }, {once:true});
        root.document.body.appendChild(dialog);
        dialog.showModal();
        return true;
    }
    function fromAction(data, context) {
        for (const result of (data.results || [])) {
            if (result.action !== 'git.push') continue;
            for (const line of String(result.response || result.error || '').split(/\r?\n/)) {
                if (!line.startsWith('CUTTLE_PUSH_REPORT=')) continue;
                try {
                    const report = JSON.parse(line.slice('CUTTLE_PUSH_REPORT='.length));
                    if (report.version !== 1 || !Array.isArray(report.findings)) continue;
                    const rules = [...new Set(report.findings.map(f => f.rule))];
                    report.summary = 'Pre-push secret hook blocked the push: ' + rules.slice(0, 2).join(', ') +
                        (rules.length > 2 ? ' (and more)' : '') + ' [' + [...new Set(report.findings.map(f => f.hook))].join(', ') + ']';
                    show(report, context);
                    return report.summary;
                } catch (_) {}
            }
        }
        return '';
    }

    /** Default inline escaper (the chat page injects its escapeHtmlInline). */
    const escapeInline = value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    /**
     * Git push menu + action controller. One instance per chat frame.
     *
     * host: { getProjectPath(), getPendingRepos(), getCurrentProject(),
     *         closeMenus(), toast(...args), closeToast(id),
     *         escapeHtmlInline, document?, fetch? }
     */
    function create(host) {
        host = host || {};
        const doc = host.document || root.document;
        const escapeHtmlInline = host.escapeHtmlInline || escapeInline;
        const doFetch = host.fetch || (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);

        let pushInFlight = false;
        let reposCache = { path: '', repos: [], at: 0 };

        function getProjectPath() {
            return host.getProjectPath ? String(host.getProjectPath() || '') : '';
        }

        /** Sync known repos for the current project (cache or pending-changes panel). */
        function knownRepos() {
            const path = getProjectPath();
            if (!path) return null;
            const now = Date.now();
            if (
                reposCache.path === path
                && (now - reposCache.at) < 15000
                && Array.isArray(reposCache.repos)
            ) {
                return reposCache.repos;
            }
            const fromPanel = host.getPendingRepos ? (host.getPendingRepos() || []) : [];
            if (fromPanel.length) {
                reposCache = { path, repos: fromPanel, at: now };
                return fromPanel;
            }
            return null;
        }

        async function fetchRepos(force) {
            const path = getProjectPath();
            if (!path) return [];
            if (!force) {
                const known = knownRepos();
                if (known) return known;
            }
            try {
                const qs = new URLSearchParams();
                qs.set('path', path);
                const resp = await doFetch('/api/git/repos?' + qs.toString(), {
                    credentials: 'include',
                    cache: 'no-store',
                });
                const data = await resp.json().catch(() => null);
                const repos = (data && data.success && Array.isArray(data.repos))
                    ? data.repos
                    : [];
                reposCache = { path, repos, at: Date.now() };
                return repos;
            } catch (_) {
                return reposCache.path === path ? (reposCache.repos || []) : [];
            }
        }

        /** Caret / submenu only when the project has more than one git repo. */
        function isMultiRepo(repos) {
            return Array.isArray(repos) && repos.length > 1;
        }

        function applyMenuMode(wrap, multi) {
            if (!wrap) return;
            const trigger = wrap.querySelector('[data-git-push-trigger]');
            if (!trigger) return;
            let caret = trigger.querySelector('.chat-chrome-menu-caret');
            if (multi) {
                wrap.setAttribute('data-git-push-multi', '1');
                trigger.setAttribute('aria-haspopup', 'true');
                if (!caret) {
                    caret = doc.createElement('span');
                    caret.className = 'chat-chrome-menu-caret';
                    caret.setAttribute('aria-hidden', 'true');
                    caret.textContent = '▸';
                    trigger.appendChild(caret);
                }
            } else {
                wrap.removeAttribute('data-git-push-multi');
                trigger.removeAttribute('aria-haspopup');
                trigger.setAttribute('aria-expanded', 'false');
                if (caret) caret.remove();
                const submenu = wrap.querySelector('[data-git-push-submenu]');
                if (submenu) {
                    submenu.innerHTML = '';
                    submenu.hidden = true;
                }
            }
        }

        function menuHtml(icons) {
            const known = knownRepos();
            const multi = isMultiRepo(known);
            return (
                '<div class="chat-chrome-menu-item-wrap is-git-push" data-git-push-wrap="1"'
                + (multi ? ' data-git-push-multi="1"' : '')
                + '>'
                + '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' title="Git push" aria-label="Git push"'
                + (multi ? ' aria-haspopup="true" aria-expanded="false"' : '')
                + ' data-git-push-trigger="1"'
                + (pushInFlight ? ' aria-disabled="true"' : '')
                + '>'
                + icons.gitPush
                + '<span class="chat-chrome-menu-item-label">Git push</span>'
                + (multi
                    ? '<span class="chat-chrome-menu-caret" aria-hidden="true">▸</span>'
                    : '')
                + '</button>'
                + '<div class="chat-chrome-menu-submenu" data-git-push-submenu="1" hidden role="menu"></div>'
                + '</div>'
            );
        }

        /** After menu HTML lands, refresh repo count and show/hide the caret. */
        function refreshMenuMode(rootEl) {
            const wraps = rootEl
                ? rootEl.querySelectorAll('[data-git-push-wrap]')
                : doc.querySelectorAll('[data-git-push-wrap]');
            if (!wraps.length) return;
            const known = knownRepos();
            if (known) {
                const multi = isMultiRepo(known);
                wraps.forEach((wrap) => applyMenuMode(wrap, multi));
                return;
            }
            fetchRepos(false).then((repos) => {
                const multi = isMultiRepo(repos);
                wraps.forEach((wrap) => {
                    if (!wrap.isConnected) return;
                    applyMenuMode(wrap, multi);
                });
            }).catch(() => {});
        }

        function repoSubmenuHtml(repos) {
            return repos.map((r) => {
                const repoRoot = String(r.repo_root || '');
                const label = escapeHtmlInline(String(r.label || repoRoot));
                const branch = r.branch ? (' · ' + escapeHtmlInline(String(r.branch))) : '';
                const enc = encodeURIComponent(repoRoot);
                return (
                    '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                    + ' data-git-push-repo="' + enc + '"'
                    + ' title="' + label + '">'
                    + '<span class="chat-chrome-menu-item-label">' + label + branch + '</span>'
                    + '</button>'
                );
            }).join('');
        }

        async function openSubmenu(triggerBtn) {
            const wrap = triggerBtn && triggerBtn.closest
                ? triggerBtn.closest('[data-git-push-wrap]')
                : null;
            if (!wrap) return;
            const submenu = wrap.querySelector('[data-git-push-submenu]');
            if (!submenu) {
                push();
                return;
            }

            const pushFromRepos = (repos) => {
                applyMenuMode(wrap, isMultiRepo(repos));
                if (!repos.length) {
                    push();
                    return true;
                }
                if (repos.length === 1) {
                    push(null, repos[0].repo_root);
                    return true;
                }
                return false;
            };

            // Single-repo (or none): push immediately — no caret / submenu flash.
            const known = knownRepos();
            if (known && !isMultiRepo(known)) {
                pushFromRepos(known);
                return;
            }

            const opening = submenu.hidden;
            doc.querySelectorAll('[data-git-push-submenu]').forEach((el) => {
                if (el !== submenu) el.hidden = true;
            });
            doc.querySelectorAll('[data-git-push-trigger]').forEach((el) => {
                el.setAttribute('aria-expanded', 'false');
            });
            if (!opening) {
                submenu.hidden = true;
                triggerBtn.setAttribute('aria-expanded', 'false');
                return;
            }

            if (known && isMultiRepo(known)) {
                applyMenuMode(wrap, true);
                triggerBtn.setAttribute('aria-expanded', 'true');
                submenu.innerHTML = repoSubmenuHtml(known);
                submenu.hidden = false;
                return;
            }

            submenu.innerHTML = '<div class="chat-chrome-menu-item is-muted">Loading repos…</div>';
            submenu.hidden = false;
            triggerBtn.setAttribute('aria-expanded', 'true');
            const repos = await fetchRepos(true);
            if (!submenu.isConnected) return;
            if (pushFromRepos(repos)) {
                submenu.innerHTML = '';
                submenu.hidden = true;
                triggerBtn.setAttribute('aria-expanded', 'false');
                return;
            }
            applyMenuMode(wrap, true);
            submenu.innerHTML = repoSubmenuHtml(repos);
        }

        let confirmResolve = null;

        function confirmModalEl() {
            return doc.getElementById('gitPushConfirmModal');
        }

        function closeConfirmModal(accepted) {
            const modal = confirmModalEl();
            if (modal) modal.hidden = true;
            const fn = confirmResolve;
            confirmResolve = null;
            if (typeof fn === 'function') fn(!!accepted);
        }

        function confirmModal(message) {
            return new Promise(function (resolve) {
                const modal = confirmModalEl();
                if (!modal) {
                    resolve(false);
                    return;
                }
                if (typeof confirmResolve === 'function') {
                    confirmResolve(false);
                }
                confirmResolve = resolve;
                const subtitle = doc.getElementById('gitPushConfirmSubtitle');
                if (subtitle) subtitle.textContent = String(message || 'Are you sure you want to push?');
                modal.hidden = false;
                const cancel = doc.getElementById('gitPushConfirmCancel');
                if (cancel) cancel.focus();
            });
        }

        function setupConfirmModal() {
            const modal = confirmModalEl();
            if (!modal || modal.dataset.bound === '1') return;
            modal.dataset.bound = '1';
            const cancelBtn = doc.getElementById('gitPushConfirmCancel');
            const acceptBtn = doc.getElementById('gitPushConfirmAccept');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    closeConfirmModal(false);
                });
            }
            if (acceptBtn) {
                acceptBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    closeConfirmModal(true);
                });
            }
            modal.addEventListener('click', function (e) {
                e.stopPropagation();
                if (e.target === modal) closeConfirmModal(false);
            });
            doc.addEventListener('keydown', function (e) {
                if (e.key !== 'Escape') return;
                if (!modal || modal.hidden) return;
                e.preventDefault();
                closeConfirmModal(false);
            });
        }

        async function push(event, repoRoot) {
            if (event) {
                event.preventDefault();
                event.stopPropagation();
            }
            if (host.closeMenus) host.closeMenus();
            const toast = host.toast || function () {};
            if (pushInFlight) {
                toast('Git push already in progress…', 'info');
                return;
            }
            const proj = host.getCurrentProject ? host.getCurrentProject() : null;
            const path = proj && proj.path ? String(proj.path) : '';
            const projectId = proj && proj.id != null ? proj.id : null;
            if (!path && projectId == null) {
                toast('No project selected', 'error');
                return;
            }

            const repos = await fetchRepos(true);
            const wantRoot = repoRoot ? String(repoRoot).replace(/\\/g, '/').toLowerCase() : '';
            let info = null;
            if (wantRoot && Array.isArray(repos)) {
                info = repos.find(function (r) {
                    const root = String((r && r.repo_root) || '').replace(/\\/g, '/').toLowerCase();
                    return root === wantRoot;
                }) || null;
            }
            if (!info && Array.isArray(repos) && repos.length === 1) info = repos[0];
            const projectName = (proj && proj.name) ? String(proj.name) : 'this project';
            const repoName = (info && (info.repo || info.label))
                ? String(info.repo || info.label)
                : 'the remote';
            const remoteName = (info && info.remote) ? String(info.remote) : 'origin';
            const branchName = (info && info.branch) ? String(info.branch) : 'HEAD';
            const confirmMsg = 'Are you sure you want to push '
                + projectName
                + ' to '
                + repoName
                + ' '
                + remoteName
                + ' '
                + branchName
                + '?';
            const accepted = await confirmModal(confirmMsg);
            if (!accepted) return;

            pushInFlight = true;
            const name = projectName;
            const repoLabel = repoRoot
                ? String(repoRoot).replace(/\\/g, '/').split('/').filter(Boolean).pop()
                : '';
            const closePushProgress = function () {
                try {
                    if (host.closeToast) host.closeToast('git-push');
                } catch (_) {}
            };
            toast('Pushing ' + (repoLabel || name) + '…', 'info', {
                toastId: 'git-push',
                sticky: true,
                progress: true,
                skipHistory: true
            });
            try {
                const body = {};
                if (path) body.path = path;
                if (projectId != null) body.project_id = projectId;
                if (repoRoot) body.repo_root = String(repoRoot);
                const resp = await doFetch('/api/git/push', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.success) {
                    if (data.push_report) show(data.push_report, {projectPath: path || data.repo_root, repoRoot: data.repo_root});
                    let err = String(
                        (data && (data.error || data.message))
                        || ('Git push failed (' + resp.status + ')')
                    ).trim();
                    err = err.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)[0] || err;
                    if (err.length > 220) err = err.slice(0, 217) + '…';
                    closePushProgress();
                    toast(err, 'error');
                    return;
                }
                const branch = data.branch ? String(data.branch) : '';
                const remote = data.remote ? String(data.remote) : '';
                const output = String(data.output || '');
                let okMsg = data.message || 'Push successful';
                if (/everything up-to-date/i.test(output)) {
                    okMsg = branch ? (branch + ' already up to date') : 'Already up to date';
                } else if (branch && remote) {
                    okMsg = 'Pushed ' + branch + ' → ' + remote;
                } else if (branch) {
                    okMsg = 'Pushed ' + branch;
                }
                // Single-repo projects don't need the repo prefix; the outcome
                // reuses the progress toast id so it swaps in place (one toast,
                // one history entry per push).
                if (repoLabel && Array.isArray(repos) && repos.length > 1) okMsg = repoLabel + ': ' + okMsg;
                closePushProgress();
                toast(okMsg, 'success', { toastId: 'git-push' });
            } catch (err) {
                closePushProgress();
                toast((err && err.message) || 'Git push failed', 'error');
            } finally {
                pushInFlight = false;
            }
        }

        return {
            menuHtml,
            applyMenuMode,
            refreshMenuMode,
            openSubmenu,
            isMultiRepo,
            knownRepos,
            fetchRepos,
            push,
            setupConfirmModal,
        };
    }

    const api = {render, show, fromAction, create};
    root.CuttleGitPushReport = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
