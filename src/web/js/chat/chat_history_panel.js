/* ================================================================
   Cuttle Chat — history panel controller (chat_history_panel.js).
   Owner: the sidebar history panel's SEARCH/FILTER slice and history
   row-menu / rename-delete modal lifecycle. This controller owns:

   - the search query/debounce/abort state machine, the `/project` +
     `/agent` filter chips and slash palette, and the search-result
     paint (title vs content tiers). Full-list paint (`loadChatHistory`)
     lives here too because it shares the active-search hard gate: an
     auth-changed or poll refresh must never wipe an in-flight search or
     its chips;
   - the history row overflow menu (`#historyItemOverflowMenu`) mounting,
     positioning and lifecycle, and the pane-centered rename/delete
     modals. The markup stays in `chat_page.html`; the controller binds
     it, it does not relocate it. Action EFFECTS (delete/rename
     persistence, terminal actions, title update) stay in the page and
     are injected.

   Pure search helpers (`entrySearchMatchRank`) and history row/header
   markup (`createRenderers`) live in `chat_history_view.js`. Project
   grouping and subagent nesting also split there: the pure algorithms and
   templates (`nestHistoryEntries`, `renderHistoryGroupBody`,
   `groupSessionsByProject`, `historySubagentToggleTitle`, …) live in the
   view, while THIS controller owns persisted collapse/expand prefs,
   per-project reveal counts, DOM toggles and the grouped repaint. Attention
   projection remains a page/attention owner (commit 4) and is injected here
   as narrow capabilities.

   Interface: `create(deps)` → one controller per chat frame exposing
   `search`, `onInput`, `onKeydown`, `clear`, `load`,
   `reloadKeepingSearch`, `isSearchActive`, `refreshSearchIndicators`,
   `repaintIfSearchActive`, `filtersActive`, `renderFilterChips`,
   `matchesFilters`, `sessionHaystack`, `localMatchesSearch`,
   `hideSlashPalette`, `closeMenu`, `toggleMenu`, `requestDelete`,
   `cancelDelete`, `confirmDelete`, `isDeleteModalOpen`,
   `closeDeleteModal`, `renameSession`, `closeRenameModal`,
   `isRenameModalOpen`, `setupModals`, plus the grouping/nesting surface
   `isProjectSectionExpanded`, `revealMore`, `collapseReveal`,
   `toggleProjectCollapse`, `toggleSubagentGroup`,
   `applyHistorySubagentExpandState`, `syncHistorySubagentAttention`,
   `historySubagentGroupOwnedBy`, `paintEntries`. The page keeps the
   `window.*` inline-handler names as thin forwarders and routes
   history-refresh call sites through this controller.

   deps: { win, doc, storage, fetch, escapeHtml,
           parseSessionTimestamp, formatChatDisplayId,
           resolveSessionProjectInfo, getProjects,
           stickyTitleChipsForSession, parseTitleSlashChips,
           getSlashCommands, isAuthMode, getAuthSessions,
           applyGeneratingFlagsFromSessions, isChatSessionDeleted,
           getTerminalHistoryEntries, createAuthHistoryItemHTML,
           createHistoryItemHTML, createTerminalHistoryItemHTML,
           createHistorySectionHeaderHTML,
           sessionHasUnread, sessionHasUnreadError,
           sessionHasPausedFollowup, sessionHasActiveFollowup,
           scheduleSlashChipCompactLabels, renderArchivedSection,
           rememberActiveHistorySessions, refreshArchivedSessions,
           isArchivedSessionsLoaded, syncHistoryRunningIndicators,
           syncHistoryUnreadIndicators,
           refreshLocalOpenPanes,
           buildSessionMenuHTML, refreshGitPushMenuMode,
           closeChatChromeMenu, isTerminalSessionId,
           getChatSessionDisplayTitle, readTerminalSessionRegistry,
           writeTerminalSessionRegistry, fetchWithTimeout,
           currentSessionId, sessionIdsEqual, toAuthDbSessionId,
           deleteChatSession, persistRenamedTitle,
           refreshChatHistoryList, updateChatSessionTitle }.
   Loaded after chat_history_view.js and before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * One history-panel search/filter controller per chat frame.
     * @param {object} deps see header
     */
    function create(deps) {
        const win = deps.win;
        const doc = deps.doc || win.document;
        const storage = deps.storage;
        const escapeHtml = deps.escapeHtml;
        const view = root.CuttleChatHistoryView;

        // Last applied history search (not only the input DOM). Android/Capacitor
        // WebViews can race keyboard/IME with refreshes; the live query keeps the
        // hard gate from falling through to a full project-list paint.
        let historySearchLiveQuery = '';
        // Last displayed search wave, reused only for explicit group reveal/collapse.
        let repaintHistorySearch = null;

        let historySearchSeq = 0;
        let historySearchTimer = null;
        let historySearchAbort = null;

        /** Active history filters from the Search slash palette (AND across kinds, OR within). */
        const historySearchFilters = {
            projects: [], // { key, name, path }
            agents: [],   // { key, id, label, prefix }
        };
        let historySlashPaletteItems = [];
        let historySlashPaletteIndex = 0;

        // ---- History row overflow menu ------------------------------------
        function historyItemEl(sessionId, event) {
            const fromEvent = event && event.currentTarget && typeof event.currentTarget.closest === 'function'
                ? event.currentTarget.closest('.chat-history-item')
                : null;
            if (fromEvent) return fromEvent;
            if (sessionId == null || sessionId === '') return null;
            const escaped = (win.CSS && win.CSS.escape) ? win.CSS.escape(String(sessionId)) : String(sessionId);
            return doc.querySelector(`.chat-history-item[data-session-id="${escaped}"]`);
        }

        function historyItemOverflowMenuEl() {
            let el = doc.getElementById('historyItemOverflowMenu');
            if (el) {
                el.className = 'history-item-menu chat-chrome-menu-dropdown';
                return el;
            }
            el = doc.createElement('div');
            el.id = 'historyItemOverflowMenu';
            // Reuse in-chat chrome dropdown skin; history-item-menu only pins fixed coords.
            el.className = 'history-item-menu chat-chrome-menu-dropdown';
            el.setAttribute('role', 'menu');
            el.hidden = true;
            doc.body.appendChild(el);
            return el;
        }

        function closeHistoryItemMenu() {
            doc.querySelectorAll('.chat-history-item.menu-open').forEach((item) => {
                item.classList.remove('menu-open');
                const btn = item.querySelector('.chat-menu-btn');
                if (btn) btn.setAttribute('aria-expanded', 'false');
            });
            const menu = doc.getElementById('historyItemOverflowMenu');
            if (menu) {
                menu.hidden = true;
                menu.innerHTML = '';
            }
        }

        function positionHistoryItemMenu(btn, menu) {
            if (!btn || !menu) return;
            const rect = btn.getBoundingClientRect();
            const menuW = menu.offsetWidth || 160;
            const menuH = menu.offsetHeight || 120;
            let top = rect.bottom + 4;
            let left = rect.right - menuW;
            if (left < 8) left = 8;
            if (left + menuW > win.innerWidth - 8) {
                left = Math.max(8, win.innerWidth - menuW - 8);
            }
            if (top + menuH > win.innerHeight - 8) {
                top = Math.max(8, rect.top - menuH - 4);
            }
            menu.style.top = top + 'px';
            menu.style.left = left + 'px';
        }

        function toggleHistoryItemMenu(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const item = historyItemEl(sessionId, event);
            if (!item) return;
            const alreadyOpen = item.classList.contains('menu-open');
            closeHistoryItemMenu();
            if (alreadyOpen) return;

            closeHistoryDeleteModal();
            closeHistoryRenameModal();
            deps.closeChatChromeMenu();
            item.classList.add('menu-open');
            const btn = item.querySelector('.chat-menu-btn');
            if (btn) btn.setAttribute('aria-expanded', 'true');

            const actions = item.querySelector('.chat-actions');
            const includeRename = !!(actions && actions.getAttribute('data-include-rename') === '1');
            const menu = historyItemOverflowMenuEl();
            menu.innerHTML = deps.buildSessionMenuHTML(sessionId, {
                includeRename,
                includeDelete: true,
            });
            menu.hidden = false;
            positionHistoryItemMenu(btn, menu);
            deps.refreshGitPushMenuMode(menu);
        }

        // ---- Delete confirmation modal ------------------------------------
        let pendingDeleteSessionId = null;

        function historyDeleteModalEl() {
            return doc.getElementById('historyDeleteModal');
        }

        function isHistoryDeleteModalOpen() {
            const modal = historyDeleteModalEl();
            return !!(modal && !modal.hidden);
        }

        function closeHistoryDeleteModal() {
            pendingDeleteSessionId = null;
            const modal = historyDeleteModalEl();
            if (modal) modal.hidden = true;
            const confirmBtn = doc.getElementById('historyDeleteConfirm');
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Delete';
            }
        }

        function historyDeleteLabel(item, sessionId) {
            const titleEl = item && item.querySelector('.chat-title');
            const title = titleEl ? String(titleEl.textContent || '').trim() : '';
            if (title) return title;
            if (sessionId != null && sessionId !== '') {
                const fallback = String(deps.getChatSessionDisplayTitle(sessionId) || '').trim();
                if (fallback) return fallback;
            }
            return 'this chat';
        }

        function openHistoryDeleteModal(sessionId, item) {
            const modal = historyDeleteModalEl();
            if (!modal) return;
            pendingDeleteSessionId = sessionId;
            const isTerminal = !!(item && item.getAttribute('data-kind') === 'terminal')
                || deps.isTerminalSessionId(sessionId);
            const heading = doc.getElementById('historyDeleteTitle');
            const subtitle = doc.getElementById('historyDeleteSubtitle');
            if (heading) heading.textContent = isTerminal ? 'Delete terminal?' : 'Delete chat?';
            if (subtitle) {
                subtitle.textContent = '"' + historyDeleteLabel(item, sessionId) + '" will be removed. This cannot be undone.';
            }
            modal.hidden = false;
            const cancel = doc.getElementById('historyDeleteCancel');
            if (cancel) cancel.focus();
        }

        function setupHistoryDeleteModal() {
            const modal = historyDeleteModalEl();
            if (!modal || modal.dataset.bound === '1') return;
            modal.dataset.bound = '1';
            const cancelBtn = doc.getElementById('historyDeleteCancel');
            const confirmBtn = doc.getElementById('historyDeleteConfirm');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    closeHistoryDeleteModal();
                });
            }
            if (confirmBtn) {
                confirmBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    confirmDeleteChatSession(pendingDeleteSessionId, e);
                });
            }
            modal.addEventListener('click', (e) => {
                e.stopPropagation();
                if (e.target === modal) closeHistoryDeleteModal();
            });
        }

        function requestDeleteChatSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            closeHistoryItemMenu();
            deps.closeChatChromeMenu();
            const item = historyItemEl(sessionId, event);
            openHistoryDeleteModal(sessionId, item);
        }

        function cancelDeleteChatSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            closeHistoryDeleteModal();
        }

        async function confirmDeleteChatSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const sid = (sessionId != null && sessionId !== '') ? sessionId : pendingDeleteSessionId;
            if (sid == null || sid === '') return;
            const confirmBtn = doc.getElementById('historyDeleteConfirm');
            if (confirmBtn) {
                confirmBtn.disabled = true;
                confirmBtn.textContent = 'Deleting…';
            }
            try {
                await deps.deleteChatSession(sid);
            } finally {
                closeHistoryDeleteModal();
            }
        }

        // ---- Rename modal -------------------------------------------------
        let pendingRenameSessionId = null;
        let pendingRenameKind = 'chat'; // chat | terminal | local
        let renameEditMode = false;
        let renameSuggestGen = 0;
        let renameSuggestInFlight = false;
        let renameAvoidTitles = [];

        function historyRenameModalEl() {
            return doc.getElementById('historyRenameModal');
        }

        function isHistoryRenameModalOpen() {
            const modal = historyRenameModalEl();
            return !!(modal && !modal.hidden);
        }

        function setHistoryRenameStatus(msg, isError) {
            const status = doc.getElementById('historyRenameStatus');
            if (!status) return;
            if (!msg) {
                status.hidden = true;
                status.textContent = '';
                status.classList.remove('is-error');
                return;
            }
            status.hidden = false;
            status.textContent = msg;
            status.classList.toggle('is-error', !!isError);
        }

        function descriptiveRenameTitle(fullTitle) {
            const parsed = deps.parseTitleSlashChips(String(fullTitle || ''));
            const body = String(parsed.body || '').trim();
            if (body) return body;
            // Title was only slash chips — leave the editable field empty.
            if (parsed.chips && parsed.chips.length) return '';
            return String(fullTitle || '').trim();
        }

        function rememberRenameAvoid(title) {
            const t = descriptiveRenameTitle(title);
            if (!t) return;
            const key = t.toLowerCase();
            if (renameAvoidTitles.some((x) => String(x).toLowerCase() === key)) return;
            renameAvoidTitles.push(t);
            if (renameAvoidTitles.length > 12) renameAvoidTitles = renameAvoidTitles.slice(-12);
        }

        function syncHistoryRenamePreviewFromInput() {
            const input = doc.getElementById('historyRenameInput');
            const preview = doc.getElementById('historyRenamePreview');
            if (!preview) return;
            preview.textContent = input ? String(input.value || '') : '';
        }

        function setHistoryRenameSuggesting(loading) {
            const loadingEl = doc.getElementById('historyRenameLoading');
            const preview = doc.getElementById('historyRenamePreview');
            const previewWrap = doc.getElementById('historyRenamePreviewWrap');
            const input = doc.getElementById('historyRenameInput');
            const editBtn = doc.getElementById('historyRenameEdit');
            if (loading) {
                renameEditMode = false;
                if (previewWrap) previewWrap.hidden = false;
                if (loadingEl) loadingEl.hidden = false;
                if (preview) preview.hidden = true;
                if (input) input.hidden = true;
                if (editBtn) {
                    editBtn.hidden = true;
                    editBtn.classList.remove('is-editing');
                    const pencil = editBtn.querySelector('.history-rename-edit-pencil');
                    const check = editBtn.querySelector('.history-rename-edit-check');
                    if (pencil) pencil.hidden = false;
                    if (check) check.hidden = true;
                }
                return;
            }
            if (loadingEl) loadingEl.hidden = true;
            applyHistoryRenameEditMode(renameEditMode);
        }

        function applyHistoryRenameEditMode(editing) {
            renameEditMode = !!editing;
            const previewWrap = doc.getElementById('historyRenamePreviewWrap');
            const preview = doc.getElementById('historyRenamePreview');
            const loadingEl = doc.getElementById('historyRenameLoading');
            const input = doc.getElementById('historyRenameInput');
            const editBtn = doc.getElementById('historyRenameEdit');
            const hasText = !!(input && String(input.value || '').trim());
            if (loadingEl) loadingEl.hidden = true;
            if (renameEditMode) {
                if (previewWrap) previewWrap.hidden = true;
                if (preview) preview.hidden = true;
                if (input) {
                    input.hidden = false;
                    try { input.focus({ preventScroll: true }); } catch (_) { try { input.focus(); } catch (__) {} }
                    try { input.select(); } catch (_) {}
                }
            } else {
                syncHistoryRenamePreviewFromInput();
                if (previewWrap) previewWrap.hidden = false;
                if (preview) preview.hidden = false;
                if (input) input.hidden = true;
            }
            if (editBtn) {
                editBtn.hidden = !hasText && !renameEditMode;
                editBtn.classList.toggle('is-editing', renameEditMode);
                editBtn.title = renameEditMode ? 'Done editing' : 'Edit name';
                editBtn.setAttribute('aria-label', renameEditMode ? 'Done editing' : 'Edit chat name');
                const pencil = editBtn.querySelector('.history-rename-edit-pencil');
                const check = editBtn.querySelector('.history-rename-edit-check');
                if (pencil) pencil.hidden = renameEditMode;
                if (check) check.hidden = !renameEditMode;
            }
        }

        function closeHistoryRenameModal() {
            pendingRenameSessionId = null;
            pendingRenameKind = 'chat';
            renameEditMode = false;
            renameSuggestGen += 1;
            renameSuggestInFlight = false;
            renameAvoidTitles = [];
            const modal = historyRenameModalEl();
            if (modal) modal.hidden = true;
            const saveBtn = doc.getElementById('historyRenameSave');
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
            const resuggestBtn = doc.getElementById('historyRenameResuggest');
            if (resuggestBtn) resuggestBtn.disabled = false;
            setHistoryRenameStatus('', false);
            const input = doc.getElementById('historyRenameInput');
            if (input) input.value = '';
            const preview = doc.getElementById('historyRenamePreview');
            if (preview) preview.textContent = '';
        }

        function resolveRenameCurrentTitle(sessionId, kind) {
            if (kind === 'terminal') {
                const map = deps.readTerminalSessionRegistry();
                const entry = map[String(sessionId)];
                return (entry && (entry.title || entry.shell)) || 'Terminal';
            }
            if (kind === 'local') {
                try {
                    const sessions = JSON.parse(storage.getItem('chatSessions') || '{}');
                    const local = sessions[sessionId] || sessions[String(sessionId)];
                    if (local && local.title) return String(local.title);
                } catch (_) {}
            }
            return deps.getChatSessionDisplayTitle(sessionId) || 'Chat';
        }

        async function suggestHistoryRenameTitle(opts) {
            const force = !!(opts && opts.force);
            const input = doc.getElementById('historyRenameInput');
            const saveBtn = doc.getElementById('historyRenameSave');
            const resuggestBtn = doc.getElementById('historyRenameResuggest');
            const sessionId = pendingRenameSessionId;
            if (sessionId == null || sessionId === '') return null;

            if (!force && input && String(input.value || '').trim() && pendingRenameKind !== 'chat') {
                applyHistoryRenameEditMode(false);
                return String(input.value).trim();
            }

            const canSuggest = pendingRenameKind === 'chat' && deps.isAuthMode() && deps.toAuthDbSessionId(sessionId) != null;
            if (!canSuggest) {
                const current = descriptiveRenameTitle(resolveRenameCurrentTitle(sessionId, pendingRenameKind));
                if (input) input.value = current;
                setHistoryRenameSuggesting(false);
                applyHistoryRenameEditMode(false);
                setHistoryRenameStatus('Pencil to edit', false);
                return current;
            }

            const gen = ++renameSuggestGen;
            renameSuggestInFlight = true;
            if (saveBtn) saveBtn.disabled = true;
            if (resuggestBtn) resuggestBtn.disabled = true;
            setHistoryRenameSuggesting(true);
            setHistoryRenameStatus('', false);
            try {
                const authSid = deps.toAuthDbSessionId(sessionId);
                const body = {};
                if (force && renameAvoidTitles.length) {
                    body.avoid = renameAvoidTitles.slice();
                } else if (input && String(input.value || '').trim()) {
                    // First open still may want to avoid the currently shown name on re-open.
                    rememberRenameAvoid(input.value);
                }
                const resp = await deps.fetchWithTimeout(
                    `/api/auth/sessions/${encodeURIComponent(authSid)}/suggest-title`,
                    {
                        method: 'POST',
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    },
                    90000
                );
                if (gen !== renameSuggestGen) return null;
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.success) {
                    throw new Error((data && data.error) || ('HTTP ' + resp.status));
                }
                const title = descriptiveRenameTitle(
                    String(data.title || '').trim()
                    || resolveRenameCurrentTitle(sessionId, pendingRenameKind)
                );
                if (input) input.value = title;
                rememberRenameAvoid(title);
                setHistoryRenameSuggesting(false);
                applyHistoryRenameEditMode(false);
                const src = data.source === 'llm'
                    ? 'Suggested'
                    : (data.source === 'fallback' ? 'From first message' : 'Current title');
                setHistoryRenameStatus(src + ' — pencil to edit', false);
                return title;
            } catch (err) {
                if (gen !== renameSuggestGen) return null;
                const fallback = descriptiveRenameTitle(resolveRenameCurrentTitle(sessionId, pendingRenameKind));
                if (input) input.value = fallback;
                setHistoryRenameSuggesting(false);
                applyHistoryRenameEditMode(true);
                setHistoryRenameStatus(
                    'Could not auto-suggest — edit manually (' + ((err && err.message) || 'error') + ')',
                    true
                );
                return fallback;
            } finally {
                if (gen === renameSuggestGen) {
                    renameSuggestInFlight = false;
                    if (saveBtn) saveBtn.disabled = false;
                    if (resuggestBtn) resuggestBtn.disabled = false;
                }
            }
        }

        function openHistoryRenameModal(sessionId, item) {
            const modal = historyRenameModalEl();
            if (!modal) return;
            closeHistoryDeleteModal();
            pendingRenameSessionId = sessionId;
            renameAvoidTitles = [];
            const isTerminal = !!(item && item.getAttribute('data-kind') === 'terminal')
                || deps.isTerminalSessionId(sessionId);
            if (isTerminal) {
                pendingRenameKind = 'terminal';
            } else if (deps.isAuthMode() && deps.toAuthDbSessionId(sessionId) != null) {
                pendingRenameKind = 'chat';
            } else {
                pendingRenameKind = 'local';
            }

            const heading = doc.getElementById('historyRenameTitle');
            if (heading) {
                heading.textContent = pendingRenameKind === 'terminal' ? 'Rename terminal' : 'Rename chat';
            }
            const resuggestBtn = doc.getElementById('historyRenameResuggest');
            if (resuggestBtn) {
                resuggestBtn.hidden = pendingRenameKind !== 'chat';
            }

            const currentFull = resolveRenameCurrentTitle(sessionId, pendingRenameKind);
            const current = descriptiveRenameTitle(currentFull);
            rememberRenameAvoid(current);
            const input = doc.getElementById('historyRenameInput');
            if (input) input.value = current;

            modal.hidden = false;
            if (pendingRenameKind === 'chat') {
                setHistoryRenameSuggesting(true);
                suggestHistoryRenameTitle({ force: true });
            } else {
                setHistoryRenameSuggesting(false);
                applyHistoryRenameEditMode(false);
                setHistoryRenameStatus('Pencil to edit', false);
            }
        }

        function setupHistoryRenameModal() {
            const modal = historyRenameModalEl();
            if (!modal || modal.dataset.bound === '1') return;
            modal.dataset.bound = '1';
            const cancelBtn = doc.getElementById('historyRenameCancel');
            const saveBtn = doc.getElementById('historyRenameSave');
            const resuggestBtn = doc.getElementById('historyRenameResuggest');
            const editBtn = doc.getElementById('historyRenameEdit');
            const input = doc.getElementById('historyRenameInput');

            if (cancelBtn) {
                cancelBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    closeHistoryRenameModal();
                });
            }
            if (saveBtn) {
                saveBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    confirmRenameChatSession(pendingRenameSessionId, e);
                });
            }
            if (resuggestBtn) {
                resuggestBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    suggestHistoryRenameTitle({ force: true });
                });
            }
            if (editBtn) {
                editBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (renameSuggestInFlight) return;
                    applyHistoryRenameEditMode(!renameEditMode);
                });
            }
            if (input) {
                input.addEventListener('input', () => {
                    if (renameEditMode) return;
                    syncHistoryRenamePreviewFromInput();
                });
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        e.stopPropagation();
                        if (renameEditMode) {
                            applyHistoryRenameEditMode(false);
                        } else {
                            confirmRenameChatSession(pendingRenameSessionId, e);
                        }
                    }
                });
            }
            modal.addEventListener('click', (e) => {
                e.stopPropagation();
                if (e.target === modal) closeHistoryRenameModal();
            });
        }

        async function confirmRenameChatSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const sid = (sessionId != null && sessionId !== '') ? sessionId : pendingRenameSessionId;
            if (sid == null || sid === '') return;
            const input = doc.getElementById('historyRenameInput');
            const newTitle = descriptiveRenameTitle(input ? String(input.value || '').trim() : '');
            if (!newTitle) {
                setHistoryRenameStatus('Enter a name (agent badges stay separate)', true);
                applyHistoryRenameEditMode(true);
                return;
            }
            const saveBtn = doc.getElementById('historyRenameSave');
            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving…';
            }
            try {
                const ok = await deps.persistRenamedTitle(sid, newTitle, pendingRenameKind);
                if (!ok) {
                    setHistoryRenameStatus('Could not rename this chat', true);
                    return;
                }
                closeHistoryRenameModal();
                // refreshChatHistoryList already re-applies search/filters when active.
                await deps.refreshChatHistoryList();
                if (deps.currentSessionId() != null && deps.sessionIdsEqual(deps.currentSessionId(), sid)) {
                    deps.updateChatSessionTitle(sid);
                }
            } catch (err) {
                setHistoryRenameStatus((err && err.message) || 'Rename failed', true);
            } finally {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save';
                }
            }
        }

        function renameChatSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
            }
            closeHistoryItemMenu();
            deps.closeChatChromeMenu();
            const item = historyItemEl(sessionId, event);
            openHistoryRenameModal(sessionId, item);
        }

        function historyFiltersActive() {
            return historySearchFilters.projects.length > 0
                || historySearchFilters.agents.length > 0;
        }

        function normalizeHistoryFilterKey(kind, value) {
            return String(kind || '') + ':' + String(value || '').trim().toLowerCase();
        }

        function sessionProjectFields(session) {
            const s = session || {};
            let name = String(s.project_name || s.projectName || '').trim();
            let path = String(s.project_path || s.projectPath || '').trim();
            if (!name && !path && s.id != null) {
                try {
                    const info = deps.resolveSessionProjectInfo(s.id, s);
                    if (info && info.key !== '__none__') {
                        name = String(info.name || '').trim();
                        path = String(info.path || '').trim();
                        // Avoid inventing the default Cuttle project badge for chats
                        // that truly have no project when listing filters/matching.
                        if (info.name === 'No project') {
                            name = '';
                            path = '';
                        }
                    }
                } catch (_) {}
            }
            return { name, path };
        }

        function sessionMatchesProjectFilter(session, filter) {
            if (!filter) return true;
            const fields = sessionProjectFields(session);
            const name = fields.name.toLowerCase();
            const path = fields.path.toLowerCase().replace(/\\/g, '/');
            const wantName = String(filter.name || '').trim().toLowerCase();
            const wantPath = String(filter.path || '').trim().toLowerCase().replace(/\\/g, '/');
            if (wantPath && path && (path === wantPath || path.includes(wantPath) || wantPath.includes(path))) {
                return true;
            }
            if (wantName && name && (name === wantName || name.includes(wantName))) return true;
            if (wantName && path && path.includes(wantName)) return true;
            return false;
        }

        function sessionAgentHaystack(session) {
            const s = session || {};
            const bits = [
                s.session_name,
                s.title,
                s.id,
            ];
            deps.stickyTitleChipsForSession(s.id).forEach((c) => {
                bits.push(c.prefix, c.label, c.meta);
            });
            try {
                const parsed = deps.parseTitleSlashChips(s.session_name || s.title || '');
                (parsed.chips || []).forEach((c) => bits.push(c.prefix, c.label, c.meta));
            } catch (_) {}
            return bits.join(' ').toLowerCase();
        }

        function sessionMatchesAgentFilter(session, filter) {
            if (!filter) return true;
            const hay = sessionAgentHaystack(session);
            const prefix = String(filter.prefix || '').trim().toLowerCase();
            const id = String(filter.id || '').trim().toLowerCase();
            if (prefix && hay.includes(prefix.trim())) return true;
            if (id && (hay.includes('/' + id) || hay.includes(id))) return true;
            return false;
        }

        function sessionMatchesHistoryFilters(session) {
            if (historySearchFilters.projects.length) {
                const ok = historySearchFilters.projects.some((p) => sessionMatchesProjectFilter(session, p));
                if (!ok) return false;
            }
            if (historySearchFilters.agents.length) {
                const ok = historySearchFilters.agents.some((a) => sessionMatchesAgentFilter(session, a));
                if (!ok) return false;
            }
            return true;
        }

        function historySessionHaystack(session) {
            const s = session || {};
            const participants = Array.isArray(s.discord_participants) ? s.discord_participants : [];
            const participantBits = participants.map((p) => (
                (p && (p.discord_username || p.discord_user_id)) || ''
            ));
            const proj = sessionProjectFields(s);
            return [
                s.session_name,
                s.title,
                s.id,
                deps.formatChatDisplayId(s.id),
                s.discord_username,
                s.project_name,
                proj.name,
                proj.path,
                s.origin,
                ...participantBits,
            ].join(' ').toLowerCase();
        }

        function localSessionMatchesSearch(session, searchTerm) {
            if (!sessionMatchesHistoryFilters(session)) return false;
            if (!searchTerm) return true;
            if (historySessionHaystack(session).includes(searchTerm)) return true;
            const messages = session && session.messages;
            if (!Array.isArray(messages)) return false;
            return messages.some((msg) => String((msg && msg.content) || '').toLowerCase().includes(searchTerm));
        }

        function collectHistoryFilterProjects() {
            const byKey = new Map();
            const add = (name, path) => {
                const n = String(name || '').trim();
                const p = String(path || '').trim();
                if (!n && !p) return;
                const label = n || p.split(/[\\/]/).filter(Boolean).pop() || p;
                const key = normalizeHistoryFilterKey('project', p || n);
                if (byKey.has(key)) return;
                byKey.set(key, { key, name: label, path: p });
            };
            (deps.getProjects() || []).forEach((p) => add(p.name || p.project_name, p.path || p.project_path));
            try {
                const listed = deps.getAuthSessions();
                listed.forEach((s) => add(s.project_name, s.project_path));
            } catch (_) {}
            return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
        }

        function collectHistoryFilterAgents() {
            return deps.getSlashCommands()
                .filter((c) => c && c.stickySession)
                .map((c) => {
                    const prefix = String(c.prefix || '').trim();
                    const id = prefix.replace(/^\/+/, '').split(/\s/)[0].toLowerCase();
                    return {
                        key: normalizeHistoryFilterKey('agent', id),
                        id,
                        label: c.label || id,
                        prefix,
                    };
                })
                .filter((a, i, arr) => a.id && arr.findIndex((x) => x.id === a.id) === i);
        }

        function renderHistoryFilterChips() {
            const el = doc.getElementById('historySearchFilterChips');
            if (!el) return;
            const chips = [];
            historySearchFilters.projects.forEach((p) => {
                chips.push({
                    kind: 'project',
                    key: p.key,
                    label: '📁 ' + (p.name || 'Project'),
                    title: p.path || p.name,
                });
            });
            historySearchFilters.agents.forEach((a) => {
                chips.push({
                    kind: 'agent',
                    key: a.key,
                    label: (a.prefix || ('/' + a.id)).trim(),
                    title: a.label || a.id,
                });
            });
            if (!chips.length) {
                el.hidden = true;
                el.innerHTML = '';
                return;
            }
            el.hidden = false;
            el.innerHTML = chips.map((c) => (
                '<span class="history-filter-chip" data-kind="' + escapeHtml(c.kind) + '" data-key="' + escapeHtml(c.key) + '" title="' + escapeHtml(c.title || c.label) + '">' +
                '<span class="history-filter-chip-label">' + escapeHtml(c.label) + '</span>' +
                '<button type="button" class="history-filter-chip-remove" data-history-filter-remove="' + escapeHtml(c.key) + '" aria-label="Remove filter">×</button>' +
                '</span>'
            )).join('');
            el.querySelectorAll('[data-history-filter-remove]').forEach((btn) => {
                btn.addEventListener('click', (ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    removeHistorySearchFilter(btn.getAttribute('data-history-filter-remove'));
                });
            });
        }

        function removeHistorySearchFilter(key) {
            const k = String(key || '');
            historySearchFilters.projects = historySearchFilters.projects.filter((p) => p.key !== k);
            historySearchFilters.agents = historySearchFilters.agents.filter((a) => a.key !== k);
            renderHistoryFilterChips();
            searchChats();
        }

        function addHistoryProjectFilter(proj) {
            if (!proj) return;
            const key = proj.key || normalizeHistoryFilterKey('project', proj.path || proj.name);
            if (historySearchFilters.projects.some((p) => p.key === key)) return;
            historySearchFilters.projects.push({
                key,
                name: proj.name || '',
                path: proj.path || '',
            });
            renderHistoryFilterChips();
        }

        function addHistoryAgentFilter(agent) {
            if (!agent) return;
            const key = agent.key || normalizeHistoryFilterKey('agent', agent.id);
            if (historySearchFilters.agents.some((a) => a.key === key)) return;
            historySearchFilters.agents.push({
                key,
                id: agent.id,
                label: agent.label || agent.id,
                prefix: agent.prefix || ('/' + agent.id + ' '),
            });
            renderHistoryFilterChips();
        }

        function parseHistorySlashDraft(raw) {
            const text = String(raw || '');
            const m = text.match(/(?:^|\s)(\/[^\n]*)$/);
            if (!m) return null;
            const token = m[1];
            const start = text.length - token.length;
            return { token, start, before: text.slice(0, start) };
        }

        /** True when project name/path matches a slash query like "esc" → Escape Purgatory. */
        function historyProjectMatchesQuery(proj, query) {
            const q = String(query || '').trim().toLowerCase();
            if (!q) return true;
            const name = String((proj && proj.name) || '').toLowerCase();
            const path = String((proj && proj.path) || '').toLowerCase().replace(/\\/g, '/');
            const hay = (name + ' ' + path).trim();
            if (!hay) return false;
            if (hay.includes(q)) return true;
            // Word / path-segment prefixes: "esc" → "escape", "ep" → no unless present
            const parts = hay.split(/[^a-z0-9]+/).filter(Boolean);
            if (parts.some((w) => w.startsWith(q))) return true;
            // Compact acronym-ish: "ep" from "Escape Purgatory"
            if (q.length >= 2) {
                const initials = parts.map((w) => w[0]).join('');
                if (initials.startsWith(q) || initials.includes(q)) return true;
            }
            return false;
        }

        function historyAgentMatchesQuery(agent, query) {
            const q = String(query || '').trim().toLowerCase();
            if (!q) return true;
            const id = String((agent && agent.id) || '').toLowerCase();
            const label = String((agent && agent.label) || '').toLowerCase();
            const prefix = String((agent && agent.prefix) || '').toLowerCase();
            if (id.startsWith(q) || id.includes(q)) return true;
            if (label.includes(q)) return true;
            if (prefix.includes(q)) return true;
            return false;
        }

        function pushHistoryProjectPaletteItems(items, needle) {
            collectHistoryFilterProjects().forEach((p) => {
                if (!historyProjectMatchesQuery(p, needle)) return;
                items.push({
                    kind: 'project',
                    id: p.key,
                    prefix: '/project',
                    label: p.name + (p.path ? ' — ' + p.path : ''),
                    apply: () => addHistoryProjectFilter(p),
                });
            });
        }

        function pushHistoryAgentPaletteItems(items, needle) {
            collectHistoryFilterAgents().forEach((a) => {
                if (!historyAgentMatchesQuery(a, needle)) return;
                items.push({
                    kind: 'agent',
                    id: a.key,
                    prefix: (a.prefix || ('/' + a.id)).trim(),
                    label: a.label,
                    apply: () => addHistoryAgentFilter(a),
                });
            });
        }

        function buildHistorySlashPaletteItems(token) {
            const t = String(token || '').trim().toLowerCase();
            const items = [];
            const q = t.replace(/^\/+/, '');

            const projectCmd = /^(?:project|p|cd)(?:\s+(.*))?$/i.exec(q);
            const agentCmd = /^agent(?:\s+(.*))?$/i.exec(q);
            const isRoot = t === '/' || q === '';

            if (isRoot || /^(?:p|pr|pro|proj|project|cd)$/i.test(q)) {
                items.push({
                    kind: 'hint',
                    id: 'hint-project',
                    prefix: '/project',
                    label: 'Filter by project…',
                    apply: null,
                });
            }
            if (isRoot || /^(?:a|ag|age|agen|agent)$/i.test(q)) {
                items.push({
                    kind: 'hint',
                    id: 'hint-agent',
                    prefix: '/agent',
                    label: 'Filter by agent…',
                    apply: null,
                });
            }

            if (isRoot) {
                pushHistoryProjectPaletteItems(items, '');
                pushHistoryAgentPaletteItems(items, '');
            } else if (projectCmd) {
                // /project, /p esc, /cd Escape…
                pushHistoryProjectPaletteItems(items, String(projectCmd[1] || '').trim());
            } else if (agentCmd) {
                pushHistoryAgentPaletteItems(items, String(agentCmd[1] || '').trim());
            } else {
                // Bare query: /esc → Escape Purgatory, /cur → Cursor, etc.
                pushHistoryProjectPaletteItems(items, q);
                pushHistoryAgentPaletteItems(items, q);
            }

            // Dedupe by id
            const seen = new Set();
            return items.filter((it) => {
                const k = it.kind + ':' + it.id;
                if (seen.has(k)) return false;
                seen.add(k);
                return true;
            }).slice(0, 40);
        }

        function hideHistorySlashPalette() {
            const el = doc.getElementById('historySearchSlashPalette');
            if (el) {
                el.hidden = true;
                el.innerHTML = '';
            }
            historySlashPaletteItems = [];
            historySlashPaletteIndex = 0;
        }

        function paintHistorySlashPalette(items) {
            const el = doc.getElementById('historySearchSlashPalette');
            if (!el) return;
            historySlashPaletteItems = items || [];
            if (!historySlashPaletteItems.length) {
                el.hidden = false;
                el.innerHTML = '<div class="history-slash-palette-empty">No matching filters. Try /project or /agent</div>';
                return;
            }
            if (historySlashPaletteIndex >= historySlashPaletteItems.length) {
                historySlashPaletteIndex = 0;
            }
            let html = '';
            let lastKind = '';
            historySlashPaletteItems.forEach((it, idx) => {
                if (it.kind !== lastKind && (it.kind === 'project' || it.kind === 'agent')) {
                    html += '<div class="history-slash-palette-section">' + escapeHtml(it.kind === 'project' ? 'Projects' : 'Agents') + '</div>';
                    lastKind = it.kind;
                } else if (it.kind === 'hint' && lastKind !== 'hint') {
                    html += '<div class="history-slash-palette-section">Filters</div>';
                    lastKind = 'hint';
                }
                html += (
                    '<button type="button" class="history-slash-palette-item' + (idx === historySlashPaletteIndex ? ' is-active' : '') + '" data-history-slash-idx="' + idx + '" role="option" aria-selected="' + (idx === historySlashPaletteIndex ? 'true' : 'false') + '">' +
                    '<span class="hslp-prefix">' + escapeHtml(it.prefix || '') + '</span>' +
                    '<span class="hslp-label">' + escapeHtml(it.label || '') + '</span>' +
                    '</button>'
                );
            });
            el.hidden = false;
            el.innerHTML = html;
            el.querySelectorAll('[data-history-slash-idx]').forEach((btn) => {
                btn.addEventListener('mousedown', (ev) => {
                    ev.preventDefault();
                    const idx = Number(btn.getAttribute('data-history-slash-idx'));
                    applyHistorySlashPaletteItem(idx);
                });
            });
        }

        function applyHistorySlashPaletteItem(idx) {
            const item = historySlashPaletteItems[idx];
            const input = doc.getElementById('searchInput');
            if (!item || !input) return;
            const draft = parseHistorySlashDraft(input.value);
            if (item.kind === 'hint') {
                input.value = (draft ? draft.before : '') + item.prefix + ' ';
                hideHistorySlashPalette();
                onHistorySearchInput({ target: input });
                input.focus();
                return;
            }
            if (typeof item.apply === 'function') item.apply();
            input.value = draft ? draft.before.replace(/\s+$/, '') : '';
            hideHistorySlashPalette();
            searchChats();
            input.focus();
        }

        function onHistorySearchInput() {
            const input = doc.getElementById('searchInput');
            const raw = (input && input.value) || '';
            const draft = parseHistorySlashDraft(raw);
            if (draft && draft.token.startsWith('/')) {
                const items = buildHistorySlashPaletteItems(draft.token);
                paintHistorySlashPalette(items);
                // While composing a slash filter, still apply text-before + active chips.
                searchChats({ slashDraft: draft });
                return;
            }
            hideHistorySlashPalette();
            searchChats();
        }

        function clearHistorySearch() {
            const input = doc.getElementById('searchInput');
            if (!input) return;
            input.value = '';
            hideHistorySlashPalette();
            searchChats();
            input.focus({ preventScroll: true });
        }

        function onHistorySearchKeydown(ev) {
            const el = doc.getElementById('historySearchSlashPalette');
            const open = el && !el.hidden && historySlashPaletteItems.length;
            if (!open) {
                if (ev.key === 'Escape' && historyFiltersActive()) {
                    // keep chips; just blur handled elsewhere
                }
                return;
            }
            if (ev.key === 'ArrowDown') {
                ev.preventDefault();
                historySlashPaletteIndex = (historySlashPaletteIndex + 1) % historySlashPaletteItems.length;
                paintHistorySlashPalette(historySlashPaletteItems);
                return;
            }
            if (ev.key === 'ArrowUp') {
                ev.preventDefault();
                historySlashPaletteIndex = (historySlashPaletteIndex - 1 + historySlashPaletteItems.length) % historySlashPaletteItems.length;
                paintHistorySlashPalette(historySlashPaletteItems);
                return;
            }
            if (ev.key === 'Enter') {
                ev.preventDefault();
                applyHistorySlashPaletteItem(historySlashPaletteIndex);
                return;
            }
            if (ev.key === 'Escape') {
                ev.preventDefault();
                hideHistorySlashPalette();
                const input = doc.getElementById('searchInput');
                const draft = parseHistorySlashDraft(input && input.value);
                if (input && draft) {
                    input.value = draft.before.replace(/\s+$/, '');
                    searchChats();
                }
            }
        }

        function filterTerminalHistory(searchTerm) {
            return deps.getTerminalHistoryEntries().filter((entry) => {
                const s = entry.session || {};
                // Terminals rarely have project/agent; hide when project/agent filters are on.
                if (historyFiltersActive()) return false;
                const hay = [
                    s.title,
                    s.shell,
                    s.id,
                    deps.formatChatDisplayId(s.id),
                    'terminal',
                ].join(' ').toLowerCase();
                return !searchTerm || hay.includes(searchTerm);
            });
        }

        function paintSearchResults(sessions, terminalEntries, renderSession, emptyOpts) {
            const historyContainer = doc.getElementById('chatHistory');
            if (!historyContainer) return;
            repaintHistorySearch = () => paintSearchResults(sessions, terminalEntries, renderSession, emptyOpts);
            const options = Object.assign({ showRefresh: false }, emptyOpts || {});

            const toEntry = (session) => {
                const isServer = session.session_name != null || session.message_count != null;
                const match = String(session.match || '').toLowerCase();
                return {
                    sessionId: session.id,
                    session: isServer ? null : session,
                    updated: deps.parseSessionTimestamp(session.last_message_time)
                        || deps.parseSessionTimestamp(session.last_activity)
                        || Number(session.updated)
                        || 0,
                    serverSession: isServer ? session : null,
                    searchMatch: match === 'content' ? 'content' : (match === 'title' ? 'title' : ''),
                };
            };

            const titleSessions = [];
            const contentSessions = [];
            (sessions || []).forEach((session) => {
                if (String(session.match || '').toLowerCase() === 'content') contentSessions.push(session);
                else titleSessions.push(session);
            });

            const titleEntries = titleSessions.map(toEntry);
            const contentEntries = contentSessions.map(toEntry);
            const termEntries = terminalEntries || [];
            const all = titleEntries.concat(contentEntries).concat(termEntries);

            if (!all.length) {
                const icon = options.emptyIcon || '🔍';
                const title = options.emptyTitle || 'No results found';
                const text = options.emptyText || 'Try a different search term';
                view.paint(historyContainer, `
                    ${deps.createHistorySectionHeaderHTML('Search')}
                    <div class="empty-state">
                        <div class="empty-state-icon">${icon}</div>
                        <div class="empty-state-title">${escapeHtml(title)}</div>
                        <div class="empty-state-text">${escapeHtml(text)}</div>
                    </div>
                `);
                return;
            }

            const renderEntry = (entry, itemOpts) => {
                if (entry.isTerminal) return deps.createTerminalHistoryItemHTML(entry.session);
                if (entry.serverSession) return renderSession(entry.serverSession, itemOpts);
                return renderSession(entry.session, itemOpts);
            };

            // Search uses match-kind sections (not project/recency groups) so a late
            // content-search wave cannot bury title hits under unrelated recent chats.
            let html = '';
            const paintTier = (key, label, entries) => {
                if (!entries.length) return;
                const group = {
                    project: { key, id: null, name: label, path: '' },
                    entries: entries.slice().sort((a, b) => {
                        const aStarred = view.entryIsStarred(a);
                        const bStarred = view.entryIsStarred(b);
                        if (aStarred && !bStarred) return -1;
                        if (bStarred && !aStarred) return 1;
                        const d = (b.updated || 0) - (a.updated || 0);
                        if (d !== 0) return d;
                        return String(b.sessionId).localeCompare(String(a.sessionId), undefined, {
                            numeric: true,
                        });
                    }),
                    latest: 0,
                };
                html += `<div class="history-section history-search-tier" data-search-tier="${escapeHtml(key)}" data-project-key="${escapeHtml(key)}">`;
                html += deps.createHistorySectionHeaderHTML(label, {
                    showRefresh: false,
                    project: group.project,
                    collapsible: false,
                    collapsed: false,
                    count: group.entries.length,
                });
                html += `<div class="history-section-body">${renderHistoryGroupBody(group, renderEntry)}</div>`;
                html += '</div>';
            };

            paintTier('__search_titles__', 'Matching titles', titleEntries.concat(termEntries));
            paintTier('__search_content__', 'In messages', contentEntries);

            view.paint(historyContainer, html, () => {
                deps.syncHistoryRunningIndicators();
                deps.syncHistoryUnreadIndicators();
                applyHistorySubagentExpandState();
                deps.scheduleSlashChipCompactLabels(historyContainer);
                deps.renderArchivedSection();
            });
        }

        function searchChats(opts) {
            closeHistoryItemMenu();
            opts = opts || {};
            const rawFull = (doc.getElementById('searchInput') && doc.getElementById('searchInput').value) || '';
            const clear = doc.getElementById('historySearchClear');
            if (clear) clear.hidden = !rawFull;
            repaintHistorySearch = null;
            const draft = opts.slashDraft || parseHistorySlashDraft(rawFull);
            const composingSlash = !!(draft && String(draft.token || '').startsWith('/'));
            const rawForText = draft ? draft.before : rawFull;
            const searchTerm = String(rawForText || '').trim().toLowerCase();
            const filtersOn = historyFiltersActive();

            if (historySearchTimer) {
                win.clearTimeout(historySearchTimer);
                historySearchTimer = null;
            }
            if (historySearchAbort) {
                try { historySearchAbort.abort(); } catch (_) {}
                historySearchAbort = null;
            }
            if (!searchTerm && !filtersOn) {
                historySearchSeq += 1;
                historySearchLiveQuery = '';
                // Keep the slash filter palette open while the user is composing /project|/agent.
                if (!composingSlash) hideHistorySlashPalette();
                loadChatHistory();
                return;
            }

            historySearchLiveQuery = searchTerm || '__filters__';
            const seq = ++historySearchSeq;
            const terminalEntries = filterTerminalHistory(searchTerm);

            if (deps.isAuthMode()) {
                const listed = deps.getAuthSessions()
                    .filter((s) => !deps.isChatSessionDeleted(s && s.id))
                    .filter((s) => sessionMatchesHistoryFilters(s));
                const titleHits = listed
                    .filter((s) => !searchTerm || historySessionHaystack(s).includes(searchTerm))
                    .map((s) => Object.assign({}, s, { match: 'title' }));
                paintSearchResults(
                    titleHits,
                    terminalEntries,
                    deps.createAuthHistoryItemHTML,
                    titleHits.length || terminalEntries.length
                        ? null
                        : {
                            emptyIcon: '🔍',
                            emptyTitle: searchTerm ? 'Searching messages…' : 'No matching chats',
                            emptyText: searchTerm ? 'Looking through chat contents' : 'Try another /project or /agent filter',
                        },
                );

                if (!searchTerm) return;

                historySearchTimer = win.setTimeout(() => {
                    if (seq !== historySearchSeq) return;
                    const controller = (typeof win.AbortController !== 'undefined') ? new win.AbortController() : null;
                    historySearchAbort = controller;
                    const url = `/api/auth/sessions/search?q=${encodeURIComponent(String(rawForText || '').trim())}`;
                    deps.fetch(url, {
                        credentials: 'include',
                        cache: 'no-store',
                        signal: controller ? controller.signal : undefined,
                    }).then((res) => (res.ok ? res.json() : null)).then((data) => {
                        if (seq !== historySearchSeq) return;
                        if (!data || !data.success || !Array.isArray(data.sessions)) {
                            if (!titleHits.length && !terminalEntries.length) {
                                paintSearchResults([], [], (s) => deps.createAuthHistoryItemHTML(s), {
                                    emptyIcon: '🔍',
                                    emptyTitle: 'No results found',
                                    emptyText: 'Try a different search term',
                                });
                            }
                            return;
                        }
                        const byId = new Map();
                        titleHits.forEach((s) => byId.set(String(s.id), s));
                        data.sessions.forEach((s) => {
                            if (deps.isChatSessionDeleted(s && s.id)) return;
                            if (!sessionMatchesHistoryFilters(s)) return;
                            const key = String(s.id);
                            const existing = byId.get(key);
                            const matchKind = String(s.match || '').toLowerCase() === 'title' ? 'title' : 'content';
                            if (!existing) {
                                byId.set(key, Object.assign({}, s, { match: matchKind }));
                                return;
                            }
                            // Keep title rank; attach snippet from content hit if present.
                            if (matchKind === 'title') existing.match = 'title';
                            if (s.snippet && !existing.snippet) existing.snippet = s.snippet;
                            if (s.search_snippet && !existing.search_snippet) {
                                existing.search_snippet = s.search_snippet;
                            }
                        });
                        // Title matches first, then content; recency within each tier.
                        const merged = Array.from(byId.values()).sort((a, b) => {
                            const am = String(a.match || '') === 'title' ? 0 : 1;
                            const bm = String(b.match || '') === 'title' ? 0 : 1;
                            if (am !== bm) return am - bm;
                            const at = deps.parseSessionTimestamp(a.last_message_time)
                                || deps.parseSessionTimestamp(a.last_activity)
                                || 0;
                            const bt = deps.parseSessionTimestamp(b.last_message_time)
                                || deps.parseSessionTimestamp(b.last_activity)
                                || 0;
                            return bt - at;
                        });
                        paintSearchResults(
                            merged,
                            terminalEntries,
                            deps.createAuthHistoryItemHTML,
                            {
                                emptyIcon: '🔍',
                                emptyTitle: 'No results found',
                                emptyText: 'Try a different search term',
                            },
                        );
                    }).catch((err) => {
                        if (err && err.name === 'AbortError') return;
                        if (seq !== historySearchSeq) return;
                        if (!titleHits.length && !terminalEntries.length) {
                            paintSearchResults([], [], (s) => deps.createAuthHistoryItemHTML(s), {
                                emptyIcon: '🔍',
                                emptyTitle: 'No results found',
                                emptyText: 'Try a different search term',
                            });
                        }
                    });
                }, 280);
                return;
            }

            const sessions = JSON.parse(storage.getItem('chatSessions') || '{}');
            const filtered = Object.values(sessions)
                .filter((session) => localSessionMatchesSearch(session, searchTerm))
                .map((session) => {
                    const titleHit = !searchTerm || historySessionHaystack(session).includes(searchTerm);
                    return Object.assign({}, session, { match: titleHit ? 'title' : 'content' });
                })
                .sort((a, b) => {
                    const am = a.match === 'title' ? 0 : 1;
                    const bm = b.match === 'title' ? 0 : 1;
                    if (am !== bm) return am - bm;
                    return (Number(b.updated) || 0) - (Number(a.updated) || 0);
                });
            paintSearchResults(
                filtered,
                terminalEntries,
                (s) => deps.createHistoryItemHTML(s),
                {
                    emptyIcon: '🔍',
                    emptyTitle: 'No results found',
                    emptyText: 'Try a different search term or /project /agent filter',
                },
            );
        }

        function historySearchQueryActive() {
            if (historyFiltersActive()) return true;
            if (String(historySearchLiveQuery || '').trim()) return true;
            const searchInput = doc.getElementById('searchInput');
            if (searchInput && String(searchInput.value || '').trim()) return true;
            return false;
        }

        function softRefreshHistorySearchIndicators() {
            // Session cache may have been refreshed; do not repaint (would abort
            // in-flight content search and flash the unfiltered list).
            if (deps.isAuthMode()) {
                try {
                    const listed = deps.getAuthSessions();
                    deps.applyGeneratingFlagsFromSessions(listed);
                } catch (_) {}
            }
            deps.syncHistoryRunningIndicators();
            deps.syncHistoryUnreadIndicators();
        }

        function reloadHistoryListKeepingSearch() {
            // Panel open/poll/title refresh / auth-changed must not wipe an active
            // search. Re-running searchChats() aborts content fetch and flashes empty.
            if (historySearchQueryActive()) {
                softRefreshHistorySearchIndicators();
                return;
            }
            loadChatHistory();
        }

        function loadChatHistory() {
            // Hard gate: accidental full-list paints (auth-changed, loadProjects, poll)
            // must not wipe an active search or filter chips.
            if (historySearchQueryActive()) {
                softRefreshHistorySearchIndicators();
                return;
            }
            closeHistoryItemMenu();
            deps.refreshLocalOpenPanes();
            const historyContainer = doc.getElementById('chatHistory');
            if (!historyContainer) return;

            const terminalEntries = deps.getTerminalHistoryEntries();

            if (deps.isAuthMode()) {
                const serverSessions = deps.getAuthSessions()
                    .filter((s) => !deps.isChatSessionDeleted(s && s.id));
                if (!serverSessions.length && !terminalEntries.length) {
                    paintHistoryEntries([], null, {});
                    return;
                }
                deps.applyGeneratingFlagsFromSessions(serverSessions);
                // Record the main-list ids before the Archived fetch resolves so
                // an unfiltered (?archived=-unaware) response can't duplicate it.
                deps.rememberActiveHistorySessions(serverSessions);
                // First paint kicks off the Archived section fetch (cached after).
                if (!deps.isArchivedSessionsLoaded()) deps.refreshArchivedSessions();
                const entries = serverSessions.map((s) => ({
                    sessionId: s.id,
                    session: null,
                    // Sort by last message only — last_activity can bump on non-message
                    // touches and reshuffle the sidebar without a new turn.
                    updated: deps.parseSessionTimestamp(s.last_message_time)
                        || deps.parseSessionTimestamp(s.last_activity),
                    serverSession: s,
                })).concat(terminalEntries);
                paintHistoryEntries(entries, (entry, itemOpts) => {
                    if (entry.isTerminal) return deps.createTerminalHistoryItemHTML(entry.session);
                    return deps.createAuthHistoryItemHTML(entry.serverSession, itemOpts);
                });
                return;
            }

            const sessions = JSON.parse(storage.getItem('chatSessions') || '{}');
            const sessionArray = Object.values(sessions);
            const entries = sessionArray.map((session) => ({
                sessionId: session.id,
                session,
                updated: Number(session.updated) || 0,
            })).concat(terminalEntries);

            paintHistoryEntries(entries, (entry) => {
                if (entry.isTerminal) return deps.createTerminalHistoryItemHTML(entry.session);
                return deps.createHistoryItemHTML(entry.session);
            });
        }

        /** Repaint the last search wave when one is active; false when none. */
        function repaintIfSearchActive() {
            if (historySearchQueryActive() && repaintHistorySearch) {
                repaintHistorySearch();
                return true;
            }
            return false;
        }

        // ---- Project grouping + subagent nesting --------------------------
        // Pure algorithms/templates live in chat_history_view.js
        // (CuttleChatHistoryView). This controller owns persisted collapse/
        // expand prefs, per-project reveal counts, DOM toggles and the grouped
        // list repaint; project resolution and session identity are injected.
        const HISTORY_GROUP_PREVIEW_LIMIT = view.HISTORY_GROUP_PREVIEW_LIMIT;
        const HISTORY_GROUP_PAGE_SIZE = view.HISTORY_GROUP_PAGE_SIZE;
        const HISTORY_PROJECT_COLLAPSE_KEY = 'cuttle.historyProjectCollapse';
        const HISTORY_SUBAGENT_EXPAND_KEY = 'cuttle.historySubagentExpand';
        /** projectKey → how many chats currently revealed (at least PREVIEW). */
        const historyGroupVisibleCount = new Map();

        function readHistoryProjectCollapsePrefs() {
            try {
                const raw = storage.getItem(HISTORY_PROJECT_COLLAPSE_KEY);
                const parsed = raw ? JSON.parse(raw) : {};
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (_) {
                return {};
            }
        }

        function writeHistoryProjectCollapsePrefs(map) {
            try {
                storage.setItem(HISTORY_PROJECT_COLLAPSE_KEY, JSON.stringify(map || {}));
            } catch (_) {}
        }

        function readHistorySubagentExpandPrefs() {
            try {
                const raw = storage.getItem(HISTORY_SUBAGENT_EXPAND_KEY);
                const parsed = raw ? JSON.parse(raw) : {};
                return parsed && typeof parsed === 'object' ? parsed : {};
            } catch (_) {
                return {};
            }
        }

        function writeHistorySubagentExpandPrefs(map) {
            try {
                storage.setItem(HISTORY_SUBAGENT_EXPAND_KEY, JSON.stringify(map || {}));
            } catch (_) {}
        }

        /** Default expand latest project (+ open/unread/queue); honor stored overrides. */
        function isHistoryProjectSectionExpanded(group, groupIdx) {
            if (!group || !group.project) return true;
            const key = String(group.project.key);
            const prefs = readHistoryProjectCollapsePrefs();
            const pref = prefs[key];
            if (pref === 'expanded') return true;
            if (pref === 'collapsed') return false;
            if (view.historyGroupContainsCurrent(group, {
                currentSessionId: deps.currentSessionId(),
                sessionIdsEqual: deps.sessionIdsEqual,
            })) return true;
            if (view.historyGroupHasUnread(group, { hasUnread: deps.sessionHasUnread })) return true;
            if (view.historyGroupHasActiveFollowup(group, { hasActiveFollowup: deps.sessionHasActiveFollowup })) return true;
            if (view.historyGroupHasPausedFollowup(group, { hasPausedFollowup: deps.sessionHasPausedFollowup })) return true;
            return groupIdx === 0;
        }

        function getHistoryGroupVisibleLimit(groupKey, total) {
            const t = Math.max(0, Number(total) || 0);
            const stored = historyGroupVisibleCount.get(String(groupKey));
            if (stored == null || !isFinite(stored)) {
                return Math.min(HISTORY_GROUP_PREVIEW_LIMIT, t);
            }
            return Math.min(Math.max(HISTORY_GROUP_PREVIEW_LIMIT, Math.floor(stored)), t);
        }

        function renderHistoryGroupBody(group, renderEntry) {
            return view.renderHistoryGroupBody(group, renderEntry, {
                getVisibleLimit: getHistoryGroupVisibleLimit,
                searchActive: historySearchQueryActive(),
                currentSessionId: deps.currentSessionId(),
                prefs: readHistorySubagentExpandPrefs(),
                sessionIdsEqual: deps.sessionIdsEqual,
                escapeHtml: escapeHtml,
            });
        }

        function repaintHistoryGroups() {
            // Active search repaints its own result wave; otherwise the panel
            // reloads while preserving any active search/filters.
            if (!repaintIfSearchActive()) reloadHistoryListKeepingSearch();
        }

        /** Drop "Show N more" reveals; each panel open starts fresh. */
        function clearHistoryGroupReveals() {
            historyGroupVisibleCount.clear();
        }

        function revealMoreHistoryGroup(event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const btn = event && event.currentTarget;
            const key = btn && btn.getAttribute('data-group-key');
            if (!key) return;
            const total = Math.max(0, Number(btn.getAttribute('data-total')) || 0);
            const cur = getHistoryGroupVisibleLimit(key, total || Number.MAX_SAFE_INTEGER);
            const next = Math.min(
                total || (cur + HISTORY_GROUP_PAGE_SIZE),
                cur + HISTORY_GROUP_PAGE_SIZE
            );
            historyGroupVisibleCount.set(String(key), next);
            repaintHistoryGroups();
        }

        function collapseHistoryGroupReveal(event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const btn = event && event.currentTarget;
            const key = btn && btn.getAttribute('data-group-key');
            if (!key) return;
            historyGroupVisibleCount.set(String(key), HISTORY_GROUP_PREVIEW_LIMIT);
            repaintHistoryGroups();
        }

        function toggleHistoryProjectCollapse(event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            const header = event && event.currentTarget;
            if (!header) return;
            // Action buttons stopPropagation themselves; belt-and-suspenders.
            if (event && event.target && event.target.closest
                && event.target.closest('.history-section-actions')) {
                return;
            }
            const section = header.closest && header.closest('.history-section');
            const key = (section && section.getAttribute('data-project-key'))
                || header.getAttribute('data-project-key');
            if (!key) return;
            const prefs = readHistoryProjectCollapsePrefs();
            const currentlyExpanded = !(section && section.classList.contains('is-collapsed'));
            prefs[String(key)] = currentlyExpanded ? 'collapsed' : 'expanded';
            writeHistoryProjectCollapsePrefs(prefs);
            repaintHistoryGroups();
        }

        function historySubagentGroupOwnedBy(sessionId) {
            if (sessionId == null || sessionId === '') return null;
            const key = String(sessionId);
            const cssEscape = (win.CSS && win.CSS.escape) ? win.CSS.escape : (s) => String(s);
            let group = doc.querySelector(`.history-subagent-group[data-parent-session-id="${cssEscape(key)}"]`);
            if (group) return group;
            const bare = deps.toAuthDbSessionId(sessionId);
            if (bare && String(bare) !== key) {
                group = doc.querySelector(`.history-subagent-group[data-parent-session-id="${cssEscape(String(bare))}"]`);
            }
            return group;
        }

        function historySubagentGroupContainsCurrent(group) {
            if (!group || deps.currentSessionId() == null) return false;
            const kids = group.querySelectorAll('.history-subagent-children .chat-history-item');
            for (let i = 0; i < kids.length; i++) {
                if (deps.sessionIdsEqual(kids[i].dataset.sessionId, deps.currentSessionId())) return true;
            }
            return false;
        }

        function syncHistorySubagentAttention() {
            doc.querySelectorAll('.history-subagent-group').forEach((group) => {
                const items = group.querySelectorAll('.history-subagent-children .chat-history-item');
                let input = false;
                let error = false;
                let unread = false;
                let queued = false;
                let paused = false;
                let running = false;
                items.forEach((item) => {
                    if (item.classList.contains('has-input')) input = true;
                    if (item.classList.contains('has-unread-error')) error = true;
                    if (item.classList.contains('has-unread')) unread = true;
                    if (item.classList.contains('has-queued')) queued = true;
                    if (item.classList.contains('has-paused-queue')) paused = true;
                    if (item.classList.contains('is-running')) running = true;
                });
                const badge = group.querySelector(':scope > .chat-history-item .history-subagent-count-badge');
                if (!badge) return;
                badge.classList.toggle('has-input', input);
                badge.classList.toggle('has-unread-error', error);
                badge.classList.toggle('has-unread', !error && unread);
                badge.classList.toggle('has-queued', !error && !unread && queued);
                badge.classList.toggle('has-paused-queue', !error && !unread && !queued && paused);
                badge.classList.toggle('is-running', running);
            });
        }

        function applyHistorySubagentExpandState() {
            const searchActive = historySearchQueryActive();
            const prefs = readHistorySubagentExpandPrefs();
            doc.querySelectorAll('.history-subagent-group').forEach((group) => {
                const pid = group.getAttribute('data-parent-session-id') || '';
                const force = historySubagentGroupContainsCurrent(group);
                const expanded = force || searchActive || prefs[pid] === 'expanded';
                group.classList.toggle('is-collapsed', !expanded);
                if (force) group.setAttribute('data-force-expanded', '1');
                else group.removeAttribute('data-force-expanded');
                const countEl = group.querySelector(':scope > .chat-history-item [data-child-count]');
                const count = countEl ? (Number(countEl.getAttribute('data-child-count')) || 0) : 0;
                const title = view.historySubagentToggleTitle(count, expanded, force);
                group.querySelectorAll(':scope > .chat-history-item .history-subagent-toggle, :scope > .chat-history-item .history-subagent-count-badge').forEach((el) => {
                    el.title = title;
                    el.setAttribute('aria-label', title);
                    if (el.classList.contains('history-subagent-toggle')) {
                        el.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                    }
                });
            });
            syncHistorySubagentAttention();
        }

        function setHistorySubagentGroupExpanded(parentId, expanded) {
            if (parentId == null || parentId === '') return;
            const prefs = readHistorySubagentExpandPrefs();
            prefs[String(parentId)] = expanded ? 'expanded' : 'collapsed';
            writeHistorySubagentExpandPrefs(prefs);
            applyHistorySubagentExpandState();
        }

        function toggleHistorySubagentGroup(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            closeHistoryItemMenu();
            const group = historySubagentGroupOwnedBy(sessionId);
            if (!group || group.getAttribute('data-force-expanded') === '1') return;
            const expand = group.classList.contains('is-collapsed');
            setHistorySubagentGroupExpanded(group.getAttribute('data-parent-session-id') || sessionId, expand);
        }

        function paintHistoryEntries(entries, renderEntry, opts) {
            const historyContainer = doc.getElementById('chatHistory');
            if (!historyContainer) return;
            const options = opts || {};
            const showRefresh = options.showRefresh !== false;
            if (!entries.length) {
                const icon = options.emptyIcon || '💬';
                const title = options.emptyTitle || 'No chats yet';
                const text = options.emptyText || 'Start a new conversation';
                view.paint(historyContainer, `
                    ${deps.createHistorySectionHeaderHTML('Your chats')}
                    <div class="empty-state">
                        <div class="empty-state-icon">${icon}</div>
                        <div class="empty-state-title">${escapeHtml(title)}</div>
                        <div class="empty-state-text">${escapeHtml(text)}</div>
                    </div>
                `, deps.renderArchivedSection);
                return;
            }
            const groups = view.groupSessionsByProject(entries, {
                resolveSessionProjectInfo: deps.resolveSessionProjectInfo,
                entryIsStarred: view.entryIsStarred,
            });
            let html = '';
            groups.forEach((group, groupIdx) => {
                const expanded = isHistoryProjectSectionExpanded(group, groupIdx);
                const unread = view.historyGroupHasUnread(group, { hasUnread: deps.sessionHasUnread });
                const unreadError = view.historyGroupHasUnreadError(group, { hasUnreadError: deps.sessionHasUnreadError });
                const queuedAny = view.historyGroupHasActiveFollowup(group, { hasActiveFollowup: deps.sessionHasActiveFollowup });
                const pausedAny = view.historyGroupHasPausedFollowup(group, { hasPausedFollowup: deps.sessionHasPausedFollowup });
                const queued = !unread && queuedAny;
                const paused = !unread && !queued && pausedAny;
                const sectionClass = [
                    expanded ? '' : ' is-collapsed',
                    unreadError ? ' has-unread-error' : '',
                    unread ? ' has-unread' : '',
                    queued ? ' has-queued' : '',
                    paused ? ' has-paused-queue' : '',
                ].join('');
                const safeKey = escapeHtml(String(group.project.key));
                html += `<div class="history-section${sectionClass}" data-project-key="${safeKey}" data-has-error="${unreadError ? '1' : '0'}" data-has-unread="${unread ? '1' : '0'}" data-has-queued="${queuedAny ? '1' : '0'}" data-has-paused="${pausedAny ? '1' : '0'}">`;
                html += deps.createHistorySectionHeaderHTML(group.project.name, {
                    showRefresh: showRefresh && groupIdx === 0,
                    project: group.project,
                    collapsible: true,
                    collapsed: !expanded,
                    count: group.entries.length,
                    hasError: unreadError,
                    hasUnread: unread,
                    hasQueued: queued,
                    hasPaused: paused,
                });
                html += `<div class="history-section-body">${renderHistoryGroupBody(group, renderEntry)}</div>`;
                html += '</div>';
            });
            view.paint(historyContainer, html, () => {
                deps.syncHistoryRunningIndicators();
                deps.syncHistoryUnreadIndicators();
                applyHistorySubagentExpandState();
                deps.scheduleSlashChipCompactLabels(historyContainer);
                deps.renderArchivedSection();
            });
        }

        return {
            search: searchChats,
            onInput: onHistorySearchInput,
            onKeydown: onHistorySearchKeydown,
            clear: clearHistorySearch,
            load: loadChatHistory,
            reloadKeepingSearch: reloadHistoryListKeepingSearch,
            isSearchActive: historySearchQueryActive,
            refreshSearchIndicators: softRefreshHistorySearchIndicators,
            repaintIfSearchActive: repaintIfSearchActive,
            filtersActive: historyFiltersActive,
            renderFilterChips: renderHistoryFilterChips,
            matchesFilters: sessionMatchesHistoryFilters,
            sessionHaystack: historySessionHaystack,
            localMatchesSearch: localSessionMatchesSearch,
            hideSlashPalette: hideHistorySlashPalette,
            closeMenu: closeHistoryItemMenu,
            toggleMenu: toggleHistoryItemMenu,
            requestDelete: requestDeleteChatSession,
            cancelDelete: cancelDeleteChatSession,
            confirmDelete: confirmDeleteChatSession,
            isDeleteModalOpen: isHistoryDeleteModalOpen,
            closeDeleteModal: closeHistoryDeleteModal,
            renameSession: renameChatSession,
            closeRenameModal: closeHistoryRenameModal,
            isRenameModalOpen: isHistoryRenameModalOpen,
            setupModals: function () {
                setupHistoryDeleteModal();
                setupHistoryRenameModal();
            },
            // Project grouping + subagent nesting.
            isProjectSectionExpanded: isHistoryProjectSectionExpanded,
            revealMore: revealMoreHistoryGroup,
            collapseReveal: collapseHistoryGroupReveal,
            clearGroupReveals: clearHistoryGroupReveals,
            toggleProjectCollapse: toggleHistoryProjectCollapse,
            toggleSubagentGroup: toggleHistorySubagentGroup,
            applyHistorySubagentExpandState: applyHistorySubagentExpandState,
            syncHistorySubagentAttention: syncHistorySubagentAttention,
            historySubagentGroupOwnedBy: historySubagentGroupOwnedBy,
            paintEntries: paintHistoryEntries,
        };
    }

    root.CuttleChatHistoryPanel = { create };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports.create = create;
    }
})(globalThis);
