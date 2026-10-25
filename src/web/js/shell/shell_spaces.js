/* ================================================================
   Cuttle Spaces — tab strip controller (shell_spaces.js)
   Owner: the Spaces UI lifetime in the shell. Composes the pure
   decision modules in src/web/js/spaces/ (spaces_state, spaces_groups,
   spaces_activity) and owns the tab strip DOM, the space/group context
   menus, the selection wiring and the activity dots. Layout/pane and
   cross-space drag effects are supplied by the shell as an explicit
   host object; this module never reads page globals.

   Loaded before app_shell.js in src/web/app_shell.html; app_shell owns
   the composition call site and the cross-space pane-drag effects.
   ================================================================ */
(function (root) {
    'use strict';

    function create(host) {
        const win = host.win || root;
        const doc = host.doc || win.document;
        const storage = host.storage || (win.localStorage || null);
        const CuttleSpaces = root.CuttleSpaces || {};
        const CuttleSessionPrefs = root.CuttleSessionPrefs;
        const CuttleActivityBroker = root.CuttleActivityBroker;

        const escapeHtml = host.escapeHtml;
        const getOpenPaneSessions = host.getOpenPaneSessions;
        const flattenLayoutLeaves = host.flattenLayoutLeaves;
        const chatSurfaceKind = host.chatSurfaceKind;
        const broadcastToFrames = host.broadcastToFrames;
        const broadcastUsageLiveWake = host.broadcastUsageLiveWake;
        const updateChatUnreadBadge = host.updateUnreadBadge;
        const getAuthUserId = host.getAuthUserId;
        const isRestoring = host.isRestoring;
        const snapshotLayout = host.snapshotLayout;
        const applySpaceRoot = host.applySpaceRoot;
        const defaultSpaceRoot = host.defaultSpaceRoot;
        const persistLastChatHandles = host.persistLastChatHandles;
        const clearLastChatColumn = host.clearLastChatColumn;
        const fetchImpl = (url, opts) => win.fetch(url, opts);

        // Pane/layout capabilities for the cross-space pane-drag effects. The
        // shell keeps the pane reorder itself; this owner only drives the
        // space-side move/swap and its stored-layout tree edits.
        const getSplitContainer = host.getSplitContainer;
        const getSplitColumns = host.getSplitColumns;
        const closeSplitColumn = host.closeSplitColumn;
        const getColumnEl = host.getColumnEl;
        const getState = host.getState;
        const setState = host.setState;
        const updateColumnUI = host.updateColumnUI;
        const replaceFrame = host.replaceFrame;
        const attachFrameLoadListener = host.attachFrameLoadListener;
        const pageWithPaneSession = host.pageWithPaneSession;
        const canonicalizeShellPage = host.canonicalizeShellPage;
        const persistSplitLayout = host.persistSplitLayout;
        const lastChatByColumn = host.lastChatByColumn;
        const newLeafId = host.newLeafId;
        const newGroupId = host.newGroupId;
        const normalizeSplitOrientation = host.normalizeSplitOrientation;
        const setFocusedColumnDirect = host.setFocusedColumnDirect;
        const broadcastPaneFocus = host.broadcastPaneFocus;

        // ── Space groups (Chrome-style tab groups) ─────────────
        // A group is a named, colored bucket of spaces. Tabs keep working exactly as
        // before; members render under a group pill and share the group color.
        // Domain logic lives in spaces_groups.js (pure state transitions on
        // spacesState); the wrappers below bind the singleton and own the
        // persist + re-render tail. Palette + ids live in spaces_state.js.
        function spaceGroupById(gid) {
            return CuttleSpaces.getGroupById(spacesState, gid);
        }

        function ensureSpaceGroupsArray() {
            return CuttleSpaces.ensureGroupsArray(spacesState);
        }

        /** Drop groups with no member tabs (runs after ungroup/close/delete). */
        function pruneEmptySpaceGroups() {
            if (CuttleSpaces.pruneEmptyGroups(spacesState)) persistSpacesState();
        }

        function setSpaceGroup(spaceId, gid) {
            if (!CuttleSpaces.setGroup(spacesState, spaceId, gid)) return false;
            pruneEmptySpaceGroups();
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        function createSpaceGroupForSpace(spaceId) {
            const group = CuttleSpaces.createGroupForSpace(spacesState, spaceId);
            if (!group) return null;
            persistSpacesState();
            renderSpaceTabs();
            return group;
        }

        function removeSpaceFromGroup(spaceId) {
            if (!CuttleSpaces.removeFromGroup(spacesState, spaceId)) return false;
            pruneEmptySpaceGroups();
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        function renameSpaceGroup(gid, name) {
            if (!CuttleSpaces.renameGroup(spacesState, gid, name)) return;
            persistSpacesState();
        }

        function setSpaceGroupColor(gid, hex) {
            if (!CuttleSpaces.setGroupColor(spacesState, gid, hex)) return false;
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        function setSpaceColor(spaceId, hex) {
            if (!CuttleSpaces.setSpaceColor(spacesState, spaceId, hex)) return false;
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        function setSpaceProjectOverride(spaceId, proj) {
            if (!CuttleSpaces.setSpaceProjectOverride(spacesState, spaceId, proj)) return false;
            persistSpacesState();
            notifySpaceDefaultsChanged({ type: 'space', id: spaceId });
            return true;
        }

        function setSpaceAgentOverride(spaceId, agent) {
            if (!CuttleSpaces.setSpaceAgentOverride(spacesState, spaceId, agent)) return false;
            persistSpacesState();
            notifySpaceDefaultsChanged({ type: 'space', id: spaceId });
            return true;
        }

        function setGroupProjectOverride(gid, proj) {
            if (!CuttleSpaces.setGroupProjectOverride(spacesState, gid, proj)) return false;
            persistSpacesState();
            notifySpaceDefaultsChanged({ type: 'group', id: gid });
            return true;
        }

        function setGroupAgentOverride(gid, agent) {
            if (!CuttleSpaces.setGroupAgentOverride(spacesState, gid, agent)) return false;
            persistSpacesState();
            notifySpaceDefaultsChanged({ type: 'group', id: gid });
            return true;
        }

        /** Live panes only exist for the active space; skip unrelated tabs. */
        function notifySpaceDefaultsChanged(target) {
            const active = CuttleSpaces.getSpaceById(spacesState, spacesState.active);
            if (!active) return;
            if (target.type === 'space' && target.id !== active.id) return;
            if (target.type === 'group' && active.groupId !== target.id) return;
            broadcastToFrames({ type: 'cuttle-space-defaults' });
        }

        /** Ungroup: dissolve the group, member spaces survive as plain tabs. */
        function dissolveSpaceGroup(gid) {
            if (!CuttleSpaces.dissolveGroup(spacesState, gid)) return false;
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        /** Collapse/expand a group without changing the active space. */
        function toggleSpaceGroupCollapsed(gid) {
            const res = CuttleSpaces.toggleGroupCollapsed(spacesState, gid);
            if (!res.ok) return false;
            persistSpacesState();
            renderSpaceTabs();
            return true;
        }

        /** Delete group: dissolve it and close its member spaces (at least one space survives). */
        function deleteSpaceGroupWithSpaces(gid) {
            const members = CuttleSpaces.planGroupDelete(spacesState, gid);
            if (!members) return false;
            persistSpacesState();
            renderSpaceTabs();
            // Close non-active members first so the active space is the last one standing.
            members.sort((a, b) => (a === spacesState.active ? 1 : 0) - (b === spacesState.active ? 1 : 0));
            members.forEach((id) => closeSpace(id));
            pruneEmptySpaceGroups();
            return true;
        }

        function spaceGroupPillHtml(group) {
            const color = CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR;
            const name = escapeHtml(group.name || '');
            const collapsed = !!group.collapsed;
            const active = spacesState.spaces.some((s) => s.id === spacesState.active && s.groupId === group.id);
            const members = spacesState.spaces.filter((s) => s && s.groupId === group.id).length;
            const title = (group.name ? 'Group: ' + name : 'Group')
                + ' — click to ' + (collapsed ? 'expand' : 'collapse') + ', drag to reorder, right-click to edit';
            return '<button type="button" class="shell-space-group' + (collapsed ? ' is-collapsed' : '') + (active ? ' is-active' : '') + '"'
                + ' aria-expanded="' + !collapsed + '"'
                + ' data-group-id="' + escapeHtml(group.id) + '"'
                + ' style="--space-accent:' + color + '"'
                + ' title="' + title + '">'
                + '<span class="shell-space-group-dot" aria-hidden="true"></span>'
                + (group.name ? '<span class="shell-space-group-name">' + name + '</span>' : '')
                + (collapsed ? '<span class="shell-space-group-count">' + members + '</span>' : '')
                + '</button>';
        }

        /** Show the grip for cross-space drags even when this space has a single pane. */
        function syncCrossSpaceGripVisibility() {
            const splitContainer = getSplitContainer();
            if (!splitContainer) return;
            splitContainer.classList.toggle('has-cross-space', spacesState.spaces.length >= 2);
        }

        // ── Spaces (titlebar tabs — each holds its own split layout) ────
        // Per-device, like STORAGE_LAYOUT. The active space's layout *is* the live
        // STORAGE_LAYOUT; `root` is only authoritative for inactive spaces.
        // Singleton storage binding lives here (shell orchestration); validation,
        // ids, names, and transitions live in spaces_state.js.
        const STORAGE_SPACES = 'shell_spaces_v1';

        function readSpacesState() {
            try {
                return CuttleSpaces.loadSpacesState(storage);
            } catch (_) {
                return CuttleSpaces.createDefaultState();
            }
        }

        function persistSpacesState() {
            CuttleSpaces.saveSpacesState(storage, spacesState);
        }

        const spacesState = readSpacesState();
        persistSpacesState();

        function switchSpace(id) {
            if (isRestoring() || id === spacesState.active) return;
            const target = spacesState.spaces.find((s) => s.id === id);
            if (!target) return;
            const targetGroup = target.groupId ? spaceGroupById(target.groupId) : null;
            if (targetGroup && targetGroup.collapsed) {
                targetGroup.collapsed = false;
                persistSpacesState();
                renderSpaceTabs();
            }
            const current = spacesState.spaces.find((s) => s.id === spacesState.active);
            if (current) current.root = snapshotLayout();
            spacesState.active = id;
            persistSpacesState();
            // navigate() re-attaches column 0's remembered chat — without this a blank
            // pane in the target space would reopen the previous space's chat.
            clearLastChatColumn(0);
            persistLastChatHandles();
            // Even identical URLs belong to different spaces and must restore that
            // space's composer. Reuse the frame element through the navigation path.
            applySpaceRoot(target.root || defaultSpaceRoot());
            hideOutgoingSpaceFrames();
            // Already-loaded frames that just became visible need a rescan now;
            // navigating frames get theirs on cuttle-page-ready.
            broadcastUsageLiveWake();
            syncActiveSpaceTab();
            syncSpaceActivityTabs();
            scheduleSpaceActivityPoll(true);
        }

        /** A reused pane crossfades from its old iframe once the new page paints —
         *  right for page nav, but on a space switch the old space must vanish now. */
        function hideOutgoingSpaceFrames() {
            doc.querySelectorAll('.split-column .shell-main').forEach((mainEl) => {
                const frames = Array.from(mainEl.querySelectorAll('iframe'));
                if (!frames.some((f) => f.classList.contains('shell-frame-pending'))) return;
                frames.forEach((f) => {
                    if (f.classList.contains('shell-frame-pending')) return;
                    f.style.transition = 'none';
                    f.style.opacity = '0';
                    f.style.visibility = 'hidden';
                    f.style.pointerEvents = 'none';
                });
            });
        }

        function addSpace() {
            const space = CuttleSpaces.addSpaceToState(spacesState);
            renderSpaceTabs();
            switchSpace(space.id);
            startSpaceRename(space.id);
        }

        function closeSpace(id) {
            const plan = CuttleSpaces.planSpaceClose(spacesState, id);
            if (!plan.ok) return;
            if (plan.switchTo) {
                switchSpace(plan.switchTo);
                if (spacesState.active === id) return;
            }
            CuttleSpaces.commitSpaceClose(spacesState, id);
            pruneEmptySpaceGroups();
            persistSpacesState();
            renderSpaceTabs();
            scheduleSpaceActivityPoll(true);
        }

        /** Saved-workspace payload: every space + group, with the active space's
         *  live layout folded in (its stored `root` is stale while it is live). */
        function exportSnapshot() {
            const snap = JSON.parse(JSON.stringify(spacesState));
            const current = snap.spaces.find((s) => s.id === snap.active);
            if (current) current.root = snapshotLayout();
            return snap;
        }

        /** Replace all spaces/groups from a saved workspace and mount its active
         *  space. Returns false (state untouched) when the payload is invalid. */
        function importSnapshot(data) {
            const clean = CuttleSpaces.sanitizeLoadedData(data);
            if (!clean) return false;
            spacesState.active = clean.active;
            spacesState.spaces = clean.spaces;
            spacesState.groups = clean.groups;
            persistSpacesState();
            renderSpaceTabs();
            clearLastChatColumn(0);
            persistLastChatHandles();
            const target = CuttleSpaces.getSpaceById(spacesState, spacesState.active);
            applySpaceRoot((target && target.root) || defaultSpaceRoot());
            hideOutgoingSpaceFrames();
            broadcastUsageLiveWake();
            syncActiveSpaceTab();
            syncSpaceActivityTabs();
            scheduleSpaceActivityPoll(true);
            return true;
        }

        function renameSpace(id, name) {
            if (!CuttleSpaces.renameSpaceInState(spacesState, id, name)) return;
            persistSpacesState();
        }

        function startSpaceRename(id) {
            const tab = doc.querySelector('.shell-space-tab[data-space-id="' + win.CSS.escape(id) + '"]');
            const nameEl = tab && tab.querySelector('.shell-space-name');
            const space = spacesState.spaces.find((s) => s.id === id);
            if (!nameEl || !space) return;
            const input = doc.createElement('input');
            input.type = 'text';
            input.className = 'shell-space-rename';
            input.maxLength = 60;
            input.value = space.name;
            input.setAttribute('aria-label', 'Space name');
            let done = false;
            const finish = (commit) => {
                if (done) return;
                done = true;
                if (commit) renameSpace(id, input.value);
                renderSpaceTabs();
                // The rename pinned the fullscreen titlebar; re-arm its auto-hide.
                if (win.CuttleShellElectron) {
                    try { win.CuttleShellElectron.rescheduleTitlebarHide(); } catch (_) {}
                }
            };
            input.addEventListener('keydown', (e) => {
                e.stopPropagation();
                if (e.key === 'Enter') { e.preventDefault(); finish(true); }
                else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
            });
            input.addEventListener('blur', () => finish(true));
            ['click', 'dblclick', 'mousedown'].forEach((evt) => {
                input.addEventListener(evt, (e) => e.stopPropagation());
            });
            nameEl.replaceWith(input);
            input.focus();
            input.select();
        }

        function renderSpaceTabs() {
            const hostEl = doc.getElementById('shellSpacesTabs');
            if (!hostEl) return;
            hostEl.dataset.count = String(spacesState.spaces.length);
            // Grouped runs render inside a shared sleeve so the group reads as one
            // container; a pill opens every run (members may be split by dragging).
            let html = '';
            let openGroup = null;
            const closeSleeve = () => { if (openGroup) { html += '</span>'; openGroup = null; } };
            spacesState.spaces.forEach((s) => {
                const group = s.groupId ? spaceGroupById(s.groupId) : null;
                const gid = group ? group.id : null;
                if (gid !== openGroup) {
                    closeSleeve();
                    if (group) {
                        const color = CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR;
                        openGroup = gid;
                        html += '<span class="shell-space-group-sleeve'
                            + (group.collapsed ? ' is-collapsed' : '') + '" role="presentation"'
                            + ' data-group-id="' + escapeHtml(gid) + '"'
                            + ' style="--space-accent:' + color + '">';
                        html += spaceGroupPillHtml(group);
                    }
                }
                if (group && group.collapsed) return; // hidden until the pill expands it
                const accent = group
                    ? (CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR)
                    : CuttleSpaces.sanitizeColor(s.color);
                const active = s.id === spacesState.active;
                const name = escapeHtml(s.name).replace(/"/g, '&quot;');
                html += '<div class="shell-space-tab' + (active ? ' is-active' : '') + (accent ? ' has-accent' : '') + '" role="tab" tabindex="0"'
                    + ' aria-selected="' + (active ? 'true' : 'false') + '"'
                    + ' data-space-id="' + escapeHtml(s.id) + '"'
                    + (group ? ' data-group-id="' + escapeHtml(group.id) + '"' : '')
                    + (accent ? ' style="--space-accent:' + accent + '"' : '')
                    + ' title="' + name + ' — drag to reorder, double-click to rename">'
                    + '<span class="shell-space-activity" aria-hidden="true" hidden></span>'
                    + '<span class="shell-space-name">' + name + '</span>'
                    + '<button type="button" class="shell-space-close" tabindex="-1" aria-label="Close space ' + name + '">×</button>'
                    + '</div>';
            });
            closeSleeve();
            hostEl.innerHTML = html;
            syncActiveSpaceTab();
            syncSpaceActivityTabs();
            syncCrossSpaceGripVisibility();
        }

        /** Toggle the active tab in place — re-rendering would eat the second click of a dblclick. */
        function syncActiveSpaceTab() {
            syncTabOverflow();
            doc.querySelectorAll('.shell-space-tab').forEach((tab) => {
                const active = tab.dataset.spaceId === spacesState.active;
                tab.classList.toggle('is-active', active);
                tab.setAttribute('aria-selected', active ? 'true' : 'false');
                if (active) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            });
            doc.querySelectorAll('.shell-space-group').forEach((pill) => {
                const active = spacesState.spaces.some((s) => s.id === spacesState.active && s.groupId === pill.dataset.groupId);
                pill.classList.toggle('is-active', active);
                if (active && spaceGroupById(pill.dataset.groupId)?.collapsed) {
                    pill.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                }
            });
            syncTabOverflow();
        }

        /** Measure capacity without arrows so their own width cannot keep them visible. */
        function syncTabOverflow() {
            const tabs = doc.getElementById('shellSpacesTabs');
            const spaces = doc.getElementById('shellSpaces');
            const add = doc.getElementById('shellSpacesAdd');
            const left = doc.getElementById('shellSpacesScrollLeft');
            const right = doc.getElementById('shellSpacesScrollRight');
            if (!tabs || !spaces || !add || !left || !right || !spaces.clientWidth) return;
            const gap = parseFloat(win.getComputedStyle(spaces).columnGap) || 0;
            const capacity = spaces.clientWidth - add.offsetWidth - gap;
            const overflow = tabs.scrollWidth > capacity + 1;
            left.hidden = right.hidden = !overflow;
            left.disabled = tabs.scrollLeft <= 1;
            right.disabled = tabs.scrollLeft >= tabs.scrollWidth - tabs.clientWidth - 1;
        }

        function setupTabOverflow(tabs) {
            for (const [id, direction] of [['shellSpacesScrollLeft', -1], ['shellSpacesScrollRight', 1]]) {
                const button = doc.getElementById(id);
                button?.addEventListener('click', (e) => {
                    e.stopPropagation();
                    tabs.scrollBy({ left: direction * Math.max(80, tabs.clientWidth * 0.8), behavior: 'smooth' });
                });
                button?.addEventListener('dblclick', (e) => e.stopPropagation());
            }
            tabs.addEventListener('scroll', syncTabOverflow, { passive: true });
            tabs.addEventListener('wheel', (e) => {
                if (e.ctrlKey || !e.deltaY || Math.abs(e.deltaX) >= Math.abs(e.deltaY)
                    || tabs.scrollWidth <= tabs.clientWidth) return;
                e.preventDefault();
                const unit = e.deltaMode === 1 ? 22 : e.deltaMode === 2 ? tabs.clientWidth : 1;
                tabs.scrollLeft += e.deltaY * unit;
            }, { passive: false });
            // Track window resizing, titlebar reveal, and width changes in adjacent chrome.
            const observer = win.ResizeObserver ? new win.ResizeObserver(syncActiveSpaceTab) : null;
            observer?.observe(tabs);
            win.addEventListener('resize', syncActiveSpaceTab);
            win.addEventListener('pagehide', () => {
                observer?.disconnect();
                win.removeEventListener('resize', syncActiveSpaceTab);
            });
            syncActiveSpaceTab();
        }

        // ── Cross-space pane drag (grip → space tab) ───────────────
        // Quick release over a space tab moves the dragged viewport to the far
        // right of that space (new horizontal split, placed at end). Holding
        // over a tab for PANE_SPACE_HOVER_MS instead switches to that space so
        // the still-held grip can be dropped on the exact pane to swap with.
        // The pane reorder itself stays in app_shell; this owner keeps only the
        // cross-space coupling and its stored-layout tree effects.
        function spaceById(id) {
            return CuttleSpaces.getSpaceById(spacesState, id);
        }

        function blankLayoutLeafNode() {
            return { type: 'leaf', id: newLeafId(), page: '/chat_page.html', flex: '' };
        }

        /** Append a leaf copy as a far-right horizontal split; wraps when needed. */
        function appendLayoutLeafToEnd(root, leaf) {
            const node = {
                type: 'leaf',
                id: (leaf && leaf.id) || newLeafId(),
                page: (leaf && leaf.page) || '/chat_page.html',
                flex: '',
            };
            if (leaf && leaf.chat) node.chat = String(leaf.chat);
            if (leaf && leaf.terminal) node.terminal = String(leaf.terminal);
            if (!root || typeof root !== 'object') {
                return {
                    type: 'group', id: newGroupId(), orientation: 'horizontal', flex: '',
                    children: [blankLayoutLeafNode(), node],
                };
            }
            if (root.type === 'group' && normalizeSplitOrientation(root.orientation) === 'horizontal'
                && Array.isArray(root.children)) {
                root.children.push(node);
                return root;
            }
            return {
                type: 'group', id: newGroupId(), orientation: 'horizontal', flex: '',
                children: [root, node],
            };
        }

        /** Depth-first leaf lookup by stored leaf id. */
        function findLayoutLeafNode(root, leafId) {
            if (!root || typeof root !== 'object' || !leafId) return null;
            if (root.type === 'leaf') return root.id === leafId ? root : null;
            if (root.type === 'group' && Array.isArray(root.children)) {
                for (const ch of root.children) {
                    const hit = findLayoutLeafNode(ch, leafId);
                    if (hit) return hit;
                }
            }
            return null;
        }

        /** Remove a leaf by id, collapsing single-child groups. Returns { root, removed }. */
        function removeLayoutLeafNode(root, leafId) {
            if (!root || typeof root !== 'object' || !leafId) return { root, removed: null };
            if (root.type === 'leaf') {
                return root.id === leafId ? { root: null, removed: root } : { root, removed: null };
            }
            if (root.type !== 'group' || !Array.isArray(root.children)) return { root, removed: null };
            let removed = null;
            const kept = [];
            for (const ch of root.children) {
                if (removed) { kept.push(ch); continue; }
                if (ch && ch.type === 'leaf' && ch.id === leafId) { removed = ch; continue; }
                const res = removeLayoutLeafNode(ch, leafId);
                if (res.removed) removed = res.removed;
                if (res.root) kept.push(res.root);
            }
            if (!removed) return { root, removed: null };
            if (kept.length === 0) return { root: null, removed };
            if (kept.length === 1) return { root: kept[0], removed };
            root.children = kept;
            return { root, removed };
        }

        /** Point a live pane at stored leaf content (page + chat/terminal handles). */
        function applyStoredLeafToLivePane(colIdx, leaf) {
            if (!Number.isFinite(colIdx) || !leaf) return false;
            const col = getColumnEl(colIdx);
            if (!col) return false;
            const entry = {};
            if (leaf.chat) entry.chat = String(leaf.chat);
            if (leaf.terminal) entry.terminal = String(leaf.terminal);
            lastChatByColumn.delete(colIdx);
            if (Object.keys(entry).length) lastChatByColumn.set(colIdx, entry);
            const page = pageWithPaneSession(colIdx, canonicalizeShellPage(leaf.page || '/chat_page.html'));
            setState(colIdx, page);
            updateColumnUI(colIdx, page);
            const frame = replaceFrame(colIdx, page);
            if (frame) attachFrameLoadListener(colIdx, frame);
            return true;
        }

        /** Quick drop: move the dragged viewport to the far right of another space. */
        function moveDraggedPaneToSpace(load, colEl, targetSpaceId) {
            if (!load || !targetSpaceId || targetSpaceId === spacesState.active) return false;
            const target = spaceById(targetSpaceId);
            if (!target) return false;
            target.root = appendLayoutLeafToEnd(target.root, load);
            persistSpacesState();
            renderSpaceTabs();
            const live = getSplitColumns();
            if (live.length >= 2 && colEl && colEl.isConnected) {
                closeSplitColumn(colEl);
            } else if (colEl && colEl.isConnected) {
                // Sole viewport moved away — leave a fresh chat behind, never an empty space.
                const idx = parseInt(colEl.dataset.column, 10);
                if (Number.isFinite(idx)) {
                    lastChatByColumn.delete(idx);
                    applyStoredLeafToLivePane(idx, blankLayoutLeafNode());
                    persistLastChatHandles();
                    persistSplitLayout();
                }
            }
            if (typeof win.showToast === 'function') {
                win.showToast('Moved pane to ' + (target.name || 'space'), 'success');
            }
            return true;
        }

        /** Chained drop while carrying: relocate the payload between stored spaces. */
        function moveCarriedPaneToSpace(load, srcId, targetSpaceId) {
            if (!load || !targetSpaceId || targetSpaceId === spacesState.active) return false;
            const dst = spaceById(targetSpaceId);
            if (!dst || targetSpaceId === srcId) return false;
            const src = spaceById(srcId);
            if (src && src.root) {
                const res = removeLayoutLeafNode(src.root, load.id);
                if (res.removed) src.root = res.root || blankLayoutLeafNode();
            }
            dst.root = appendLayoutLeafToEnd(dst.root, load);
            persistSpacesState();
            renderSpaceTabs();
            if (typeof win.showToast === 'function') {
                win.showToast('Moved pane to ' + (dst.name || 'space'), 'success');
            }
            return true;
        }

        /** Drop on a live pane while carrying: swap the payload with that pane's content. */
        function exchangeCarriedPaneWithLive(load, srcId, targetCol) {
            const src = spaceById(srcId);
            if (!src || !load || !targetCol || !targetCol.isConnected) return false;
            const idxT = parseInt(targetCol.dataset.column, 10);
            if (!Number.isFinite(idxT)) return false;
            const stateT = { ...getState(idxT) };
            const oldPage = pageWithPaneSession(idxT, canonicalizeShellPage(stateT.page || '/chat_page.html'));
            const oldHandles = { ...(lastChatByColumn.get(idxT) || {}) };
            if (!applyStoredLeafToLivePane(idxT, load)) return false;
            const slot = findLayoutLeafNode(src.root, load.id);
            if (slot) {
                slot.page = oldPage;
                delete slot.chat;
                delete slot.terminal;
                if (oldHandles.chat) slot.chat = String(oldHandles.chat);
                if (oldHandles.terminal) slot.terminal = String(oldHandles.terminal);
            }
            persistSpacesState();
            persistLastChatHandles();
            persistSplitLayout();
            setFocusedColumnDirect(idxT);
            broadcastPaneFocus();
            if (typeof win.showToast === 'function') {
                win.showToast('Swapped panes with ' + (src.name || 'space'), 'success');
            }
            return true;
        }

        const PANE_SPACE_HOVER_MS = 1000;

        /** Space-tab element under a viewport point (titlebar hit-test during pane drags). */
        function spaceTabAtPoint(x, y) {
            const tabs = doc.querySelectorAll('.shell-space-tab');
            for (const tab of tabs) {
                const r = tab.getBoundingClientRect();
                if (x >= r.left && x < r.right && y >= r.top && y < r.bottom) return tab;
            }
            return null;
        }

        /**
         * Cross-space coupling for one pane-grip drag. The shell keeps the
         * within-space pane reorder; this owns the tab hover→switch and the
         * drop that moves or swaps a pane between spaces.
         */
        function createPaneDrop() {
            let load = null;
            let colEl = null;
            let onAutoSwitch = null;
            let carrying = false;
            let sourceSpaceId = null;
            let hoverTab = null;
            let hoverTimer = 0;

            function clearHover() {
                if (hoverTimer) { win.clearTimeout(hoverTimer); hoverTimer = 0; }
                if (hoverTab) { hoverTab.classList.remove('pane-space-hover'); hoverTab = null; }
            }

            /** A drag may start when another pane OR another space can receive it. */
            function canStartDrag() {
                return !(getSplitColumns().length < 2 && spacesState.spaces.length < 2);
            }

            function beginDrag(payload, columnEl, resetPaneDrop) {
                load = payload;
                colEl = columnEl;
                onAutoSwitch = resetPaneDrop || null;
                carrying = false;
                sourceSpaceId = null;
                clearHover();
            }

            function endDrag() {
                load = null;
                colEl = null;
                onAutoSwitch = null;
                carrying = false;
                sourceSpaceId = null;
                clearHover();
            }

            function autoSwitchToSpace(spaceId) {
                if (!load || !spaceId || spaceId === spacesState.active) return;
                if (!carrying) {
                    carrying = true;
                    sourceSpaceId = spacesState.active;
                }
                clearHover();
                if (onAutoSwitch) { try { onAutoSwitch(); } catch (_) {} }
                switchSpace(spaceId);
                // Hovering home for 1s just comes back — the payload never left.
                if (spacesState.active === sourceSpaceId) {
                    carrying = false;
                    sourceSpaceId = null;
                }
            }

            function trackHover(e) {
                if (!load || spacesState.spaces.length < 2) return;
                const tab = spaceTabAtPoint(e.clientX, e.clientY);
                if (tab === hoverTab) return;
                clearHover();
                hoverTab = tab || null;
                if (hoverTab && hoverTab.dataset.spaceId !== spacesState.active) {
                    hoverTab.classList.add('pane-space-hover');
                    const id = hoverTab.dataset.spaceId;
                    hoverTimer = win.setTimeout(() => {
                        if (load && hoverTab && hoverTab.dataset.spaceId === id) autoSwitchToSpace(id);
                    }, PANE_SPACE_HOVER_MS);
                }
            }

            /**
             * Consume a drop that targets a space (or a live pane while
             * carrying). Returns true when handled; false lets the shell do the
             * within-space reorder. Always resets the drag state.
             */
            function finishDrop(upEvent, dropTarget) {
                const payload = load;
                const wasCarrying = carrying;
                const srcId = sourceSpaceId;
                const col = colEl;
                endDrag();
                if (!payload) return false;
                const tab = (upEvent && Number.isFinite(upEvent.clientX) && Number.isFinite(upEvent.clientY))
                    ? spaceTabAtPoint(upEvent.clientX, upEvent.clientY)
                    : null;
                const tabId = tab ? (tab.dataset.spaceId || '') : '';
                if (tabId && tabId !== spacesState.active) {
                    if (!wasCarrying) {
                        win.setTimeout(() => moveDraggedPaneToSpace(payload, col, tabId), 0);
                        return true;
                    }
                    if (tabId === srcId) {
                        win.setTimeout(() => switchSpace(srcId), 0);
                        return true;
                    }
                    win.setTimeout(() => moveCarriedPaneToSpace(payload, srcId, tabId), 0);
                    return true;
                }
                if (wasCarrying) {
                    if (dropTarget && dropTarget.isConnected && dropTarget !== col) {
                        const targetEl = dropTarget;
                        win.setTimeout(() => exchangeCarriedPaneWithLive(payload, srcId, targetEl), 0);
                    }
                    return true;
                }
                return false;
            }

            return {
                hoverMs: PANE_SPACE_HOVER_MS,
                canStartDrag, beginDrag, endDrag, trackHover, clearHover, finishDrop,
                isActive: () => !!load,
            };
        }

        const paneDrop = createPaneDrop();

        // ── Spaces activity (one indicator per tab) ─────────────────────
        // Same states as chat history/title dots: input (blue), running (spinner),
        // error (red), unread (green), queued/active (orange), paused (yellow).
        // A space shows the highest-priority state across its chats:
        // input > running > error > unread > queued > paused > none.
        // Aggregation + snapshot map live in spaces_activity.js; the poll transport
        // and DOM patching below stay in the shell.
        let spaceActivityPollInFlight = false;
        let spaceActivityLastPollAt = 0;
        let spaceActivitySig = '';

        function lookupSpaceSessionActivity(sid) {
            return CuttleSpaces.lookupSessionActivity(sid);
        }

        /** Forget snapshots from chat frames that were replaced or closed. */
        function pruneSpaceActivitySources() {
            const live = new Set();
            doc.querySelectorAll('iframe').forEach((fr) => {
                try { if (fr.contentWindow) live.add(fr.contentWindow); } catch (_) {}
            });
            CuttleSpaces.pruneSources((w) => live.has(w));
        }

        /** Chat session ids belonging to a space. Active space reads live panes;
         *  inactive spaces parse their stored layout tree. Terminals have no dots. */
        function spaceChatIds(space) {
            if (!space) return [];
            if (space.id === spacesState.active) {
                try {
                    return getOpenPaneSessions()
                        .filter((p) => p && p.kind === 'chat' && p.sessionId)
                        .map((p) => String(p.sessionId));
                } catch (_) {
                    return [];
                }
            }
            const ids = [];
            try {
                flattenLayoutLeaves(space.root || null).forEach((leaf) => {
                    if (!leaf) return;
                    try {
                        const url = new URL(leaf.page || '/chat_page.html', win.location.origin);
                        // Terminals have no activity dots — only chat surfaces count.
                        if (chatSurfaceKind(url.pathname) !== 'chat') return;
                        const handle = url.searchParams.get('chat') || url.searchParams.get('session')
                            || leaf.chat || null;
                        if (handle) ids.push(String(handle));
                    } catch (_) {}
                });
            } catch (_) {}
            return [...new Set(ids)];
        }

        /** Currently visible chat ids — unread there is already seen, so it never
         *  raises the active tab (running / queued dots still do). */
        function visibleActiveSpaceChatIds() {
            try {
                return new Set(
                    getOpenPaneSessions()
                        .filter((p) => p && p.kind === 'chat' && p.sessionId)
                        .map((p) => String(p.sessionId))
                );
            } catch (_) {
                return new Set();
            }
        }

        function spaceActivityFor(space) {
            const ids = spaceChatIds(space);
            if (!ids.length) return '';
            const isActive = space.id === spacesState.active;
            const visible = isActive ? visibleActiveSpaceChatIds() : new Set();
            return CuttleSpaces.selectSpaceActivity(ids, lookupSpaceSessionActivity, isActive, visible);
        }

        /** Patch tab dots in place (no re-render — preserves dblclick rename). */
        function syncSpaceActivityTabs() {
            host.syncCollapsedPanes?.();
            const tabs = doc.querySelectorAll('.shell-space-tab');
            if (!tabs.length) return;
            pruneSpaceActivitySources();
            const sigParts = [];
            tabs.forEach((tab) => {
                const space = spacesState.spaces.find((s) => s.id === tab.dataset.spaceId);
                const kind = space ? spaceActivityFor(space) : '';
                sigParts.push(tab.dataset.spaceId + ':' + kind);
                let dot = tab.querySelector('.shell-space-activity');
                if (!dot) {
                    dot = doc.createElement('span');
                    dot.className = 'shell-space-activity';
                    dot.setAttribute('aria-hidden', 'true');
                    tab.insertBefore(dot, tab.firstChild);
                }
                const prev = dot.dataset.kind || '';
                if (prev !== kind || dot.hidden === !!kind) {
                    dot.dataset.kind = kind;
                    dot.className = 'shell-space-activity' + (kind ? ' is-' + kind : '');
                    if (kind) {
                        dot.hidden = false;
                        const label = CuttleSpaces.LABEL[kind] || kind;
                        dot.title = label;
                        dot.setAttribute('aria-label', label);
                    } else {
                        dot.hidden = true;
                        dot.removeAttribute('title');
                        dot.removeAttribute('aria-label');
                    }
                    const spaceName = space ? space.name : '';
                    tab.title = spaceName + ' — drag to reorder, double-click to rename'
                        + (kind ? ' · ' + (CuttleSpaces.LABEL[kind] || kind) : '');
                }
            });
            spaceActivitySig = sigParts.join('|');
            updateChatUnreadBadge();
        }

        function allSpaceChatIds() {
            const out = new Set();
            try {
                spacesState.spaces.forEach((space) => {
                    spaceChatIds(space).forEach((id) => out.add(String(id)));
                });
            } catch (_) {}
            // Live panes may know fresher ids than the stored tree right after nav.
            try {
                getOpenPaneSessions().forEach((p) => {
                    if (p && p.kind === 'chat' && p.sessionId) out.add(String(p.sessionId));
                });
            } catch (_) {}
            return [...out].filter(Boolean).slice(0, 60);
        }

        const shellSessionPrefs = CuttleSessionPrefs.create(storage);

        function readChatPrefsMap() {
            return shellSessionPrefs.map();
        }


        function readLocalChatSessions() {
            try {
                return JSON.parse(storage.getItem('chatSessions') || '{}') || {};
            } catch (_) {
                return {};
            }
        }

        /** Unread flags + local-mode queues are same-origin localStorage. */
        function refreshSpaceActivityLocalState() {
            try { CuttleSpaces.setUnreadPrefs(readChatPrefsMap()); } catch (_) {}
            try {
                const local = readLocalChatSessions();
                const queues = {};
                Object.keys(local || {}).forEach((sid) => {
                    const obj = local[sid];
                    if (!obj || typeof obj !== 'object') return;
                    queues[sid] = obj.followup_queue != null ? obj.followup_queue : obj.followups;
                });
                CuttleSpaces.setLocalQueues(queues);
            } catch (_) {}
        }

        /** A chat in a background space finished with no frame watching it — flag
         *  it unread (same prefs row the history panel reads) so its tab goes green.
         *  Returns the bare ids that were newly marked (already-unread/visible chats
         *  are skipped) so callers can raise a notification for exactly those. */
        function markFinishedBackgroundChatsUnread(finished) {
            if (!finished || !finished.length) return [];
            const visible = new Set([...visibleActiveSpaceChatIds()].map((s) => CuttleSpaces.bareSid(s)));
            const targets = finished.filter((bare) => bare && !visible.has(bare));
            if (!targets.length) return [];
            const marked = [];
            try {
                const prefs = readChatPrefsMap();
                targets.forEach((bare) => {
                    const key = Object.keys(prefs).find((k) => CuttleSpaces.bareSid(k) === bare) || bare;
                    const row = prefs[key] && typeof prefs[key] === 'object' ? prefs[key] : {};
                    if (row.hasUnread) return;
                    prefs[key] = Object.assign({}, row, { hasUnread: true, unreadIsError: false });
                    marked.push(bare);
                });
                if (marked.length) marked.forEach(bare => shellSessionPrefs.update(bare, prefs[bare] || {}));
            } catch (_) {}
            return marked;
        }

        /** Human-readable label for a bare chat id (CH-000123 for numeric ids). */
        function formatBareChatLabel(bare) {
            const raw = String(bare == null ? '' : bare);
            if (/^\d+$/.test(raw)) return 'CH-' + String(parseInt(raw, 10)).padStart(6, '0');
            return raw || 'chat';
        }

        /**
         * A background chat finished (server saw running → idle). Mark it unread and
         * raise a "Reply ready" notification in Cuttle's notification panel so chat
         * completions no-frame-was-watching still surface. Idempotent per completion
         * (the transition fires once), and skipped entirely when server-attention rows
         * are present (those already carry hasUnread from the server).
         */
        function reconcileFinishedBackgroundChats(finished, sessions) {
            if (!finished || !finished.length) return;
            const titleByBare = new Map();
            (Array.isArray(sessions) ? sessions : []).forEach((s) => {
                if (s && s.id != null && s.title) titleByBare.set(CuttleSpaces.bareSid(s.id), String(s.title));
            });
            let marked = [];
            try {
                marked = markFinishedBackgroundChatsUnread(finished);
            } catch (_) {}
            if (marked.length) refreshSpaceActivityLocalState();
            marked.forEach((bare) => {
                const label = titleByBare.get(bare) || formatBareChatLabel(bare);
                try {
                    (win.showToast || function () {})('Reply ready — ' + label,
                        'success', { unread: true, sessionId: bare, kind: 'chat-response' });
                } catch (_) {}
            });
        }

        async function fetchSpaceSessionsList() {
            try { return await activityBroker.sessions(); } catch (_) { return null; }
        }

        async function loadActivitySessions() {
            const controller = new win.AbortController();
            const timeout = win.setTimeout(() => controller.abort(), 6000);
            try {
                const r = await fetchImpl('/api/auth/sessions', {
                    cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
                });
                if (!r.ok) return null;
                const data = await r.json().catch(() => null);
                return data && data.success && Array.isArray(data.sessions) ? data.sessions : null;
            } catch (_) {
                return null;
            } finally {
                win.clearTimeout(timeout);
            }
        }

        /** Not authenticated — live-status knows only `generating`. */
        async function fetchSpaceLiveStatusRows(ids) {
            const rows = [];
            for (let i = 0; i < ids.length; i += 12) {
                const chunk = ids.slice(i, i + 12);
                const controller = new win.AbortController();
                const timeout = win.setTimeout(() => controller.abort(), 5000);
                try {
                    const r = await fetchImpl(
                        '/api/chat-live-status-batch?session_ids=' + encodeURIComponent(chunk.join(',')),
                        { credentials: 'include', cache: 'no-store', signal: controller.signal }
                    );
                    if (!r.ok) continue;
                    const data = await r.json().catch(() => null);
                    if (!data || !data.success) continue;
                    const statuses = data.statuses || {};
                    chunk.forEach((sid) => {
                        const st = statuses[String(sid)] || statuses[CuttleSpaces.bareSid(sid)] || null;
                        rows.push({ id: sid, running: !!(st && st.generating), queue: null });
                    });
                } catch (_) {
                } finally {
                    win.clearTimeout(timeout);
                }
            }
            return rows;
        }

        /** Server truth for every chat in every space (inactive spaces have no
         *  live frame). Frame pushes add immediacy between polls. */
        async function refreshSpaceActivityFromServer() {
            if (spaceActivityPollInFlight) return;
            refreshSpaceActivityLocalState();
            const ids = allSpaceChatIds();
            if (!ids.length) {
                syncSpaceActivityTabs();
                return;
            }
            spaceActivityPollInFlight = true;
            const startedAt = Date.now();
            try {
                let rows = null;
                const sessions = await fetchSpaceSessionsList();
                if (sessions) {
                    const byBare = new Map();
                    sessions.forEach((s) => {
                        if (s && s.id != null) byBare.set(CuttleSpaces.bareSid(s.id), s);
                    });
                    rows = [];
                    ids.forEach((sid) => {
                        const s = byBare.get(CuttleSpaces.bareSid(sid));
                        if (!s) return;
                        rows.push({
                            id: sid,
                            running: !!(s.generating || s.awaiting_action),
                            attention: s.attention,
                            queue: CuttleSpaces.followupKind(parseSpaceFollowupQueue(
                                s.followup_queue != null ? s.followup_queue : s.followups)),
                        });
                    });
                } else {
                    rows = await fetchSpaceLiveStatusRows(ids);
                }
                const finished = CuttleSpaces.noteServerSnapshot(rows, startedAt);
                if (!rows.some(row => row.attention)) reconcileFinishedBackgroundChats(finished, sessions);
                refreshSpaceActivityLocalState();
            } finally {
                spaceActivityPollInFlight = false;
                spaceActivityLastPollAt = Date.now();
                syncSpaceActivityTabs();
            }
        }

        /** followup_queue arrives as an array or a JSON string. */
        function parseSpaceFollowupQueue(raw) {
            if (Array.isArray(raw)) return raw;
            if (typeof raw === 'string' && raw.trim()) {
                try {
                    const parsed = JSON.parse(raw);
                    return Array.isArray(parsed) ? parsed : [];
                } catch (_) {
                    return [];
                }
            }
            return [];
        }

        const activityBroker = CuttleActivityBroker.create({
            scope: () => getAuthUserId(),
            now: () => Date.now(), setTimeout: (fn, ms) => win.setTimeout(fn, ms),
            clearTimeout: id => win.clearTimeout(id),
            load: async () => {
                const rows = await loadActivitySessions();
                if (!rows) throw new Error('Session snapshot unavailable');
                return rows;
            },
            publish: sessions => {
                broadcastToFrames({type: 'cuttle-sessions-snapshot', sessions});
                const rows = sessions.map(s => ({id: s.id,
                    running: !!(s.generating || s.awaiting_action), attention: s.attention,
                    queue: CuttleSpaces.followupKind(parseSpaceFollowupQueue(s.followup_queue))}));
                const finished = CuttleSpaces.noteServerSnapshot(rows, Date.now());
                if (!rows.some(r => r.attention)) reconcileFinishedBackgroundChats(finished, sessions);
                syncSpaceActivityTabs();
            },
            openStream: wake => {
                if (typeof win.EventSource === 'undefined') return null;
                const stream = new win.EventSource('/api/activity/stream');
                stream.addEventListener('activity', wake);
                stream.onopen = wake;
                return stream;
            },
        });
        win.cuttleActivityBroker = activityBroker;

        function scheduleSpaceActivityPoll(immediate) {
            const since = Date.now() - spaceActivityLastPollAt;
            if (!immediate && since < 8000) {
                refreshSpaceActivityLocalState();
                syncSpaceActivityTabs();
                return;
            }
            if (immediate) activityBroker.refresh();
            refreshSpaceActivityFromServer();
        }

        // ── Space tab right-click menu + group editor bubble ────
        let spaceCtxEls = [];
        let spaceCtxDismiss = null;

        /** True while a space menu, submenu, or group bubble is on screen. */
        function isSpaceCtxOpen() {
            return spaceCtxEls.length > 0;
        }

        function closeSpaceCtxLayers() {
            if (spaceCtxDismiss) {
                try { spaceCtxDismiss(); } catch (_) {}
                spaceCtxDismiss = null;
            }
            spaceCtxEls.forEach((el) => { try { el.remove(); } catch (_) {} });
            spaceCtxEls = [];
            // The open layers pinned the fullscreen titlebar; re-arm its auto-hide now
            // that they are gone (no-op unless fullscreen and the pointer is away).
            if (win.CuttleShellElectron) {
                try { win.CuttleShellElectron.rescheduleTitlebarHide(); } catch (_) {}
            }
        }

        function trackSpaceCtxDismiss() {
            if (spaceCtxDismiss) {
                try { spaceCtxDismiss(); } catch (_) {}
                spaceCtxDismiss = null;
            }
            // Layers opening pin the titlebar: drop any hide another close just armed.
            if (win.CuttleShellElectron) {
                try { win.CuttleShellElectron.cancelTitlebarHide(); } catch (_) {}
            }
            const onDown = (e) => {
                if (e.target && e.target.closest && e.target.closest('.shell-space-ctx, .shell-group-bubble, .chip-config-modal-overlay')) return;
                closeSpaceCtxLayers();
            };
            const onKey = (e) => {
                if (e.key === 'Escape') { e.stopPropagation(); closeSpaceCtxLayers(); }
            };
            const onHide = () => closeSpaceCtxLayers();
            doc.addEventListener('pointerdown', onDown, true);
            doc.addEventListener('keydown', onKey, true);
            win.addEventListener('scroll', onHide, true);
            win.addEventListener('resize', onHide);
            win.addEventListener('blur', onHide);
            spaceCtxDismiss = () => {
                doc.removeEventListener('pointerdown', onDown, true);
                doc.removeEventListener('keydown', onKey, true);
                win.removeEventListener('scroll', onHide, true);
                win.removeEventListener('resize', onHide);
                win.removeEventListener('blur', onHide);
            };
        }

        /** Append a fixed panel, measure it, then clamp it inside the viewport. */
        function placeFixedPanel(el, x, y) {
            el.style.left = '0px';
            el.style.top = '0px';
            doc.body.appendChild(el);
            spaceCtxEls.push(el);
            const rect = el.getBoundingClientRect();
            const pad = 8;
            let nx = Number(x) || 0;
            let ny = Number(y) || 0;
            if (nx + rect.width + pad > win.innerWidth) nx = Math.max(pad, win.innerWidth - rect.width - pad);
            if (ny + rect.height + pad > win.innerHeight) ny = Math.max(pad, win.innerHeight - rect.height - pad);
            el.style.left = Math.max(pad, nx) + 'px';
            el.style.top = Math.max(pad, ny) + 'px';
            return el;
        }

        function spaceCtxSwatchesHtml(selected) {
            return '<div class="shell-ctx-swatches" role="group" aria-label="Colors">'
                + CuttleSpaces.COLORS.map((c) => (
                    '<button type="button" class="shell-ctx-swatch' + (selected === c.hex ? ' is-selected' : '') + '"'
                    + ' data-color="' + c.hex + '"'
                    + ' style="--swatch:' + c.hex + '"'
                    + ' title="' + c.name + '" aria-label="' + c.name + '"></button>'
                )).join('')
                + '</div>';
        }

        // Composer chip-config modal, reused for space/group new-chat overrides.
        let overrideTarget = null;
        let overrideChipConfig = null;
        const overrideCatalog = { projects: [], agents: [], loading: null };

        function overrideRecord(target) {
            if (!target) return null;
            return target.type === 'group'
                ? CuttleSpaces.getGroupById(spacesState, target.id)
                : CuttleSpaces.getSpaceById(spacesState, target.id);
        }

        function catalogSettingOptions(list) {
            return (list || []).map((item) => {
                if (item == null) return null;
                if (typeof item === 'string' || typeof item === 'number') {
                    const value = String(item);
                    return { modelId: value, label: value };
                }
                const value = item.id || item.modelId || item.value;
                if (value == null || String(value) === '') return null;
                return { modelId: String(value), label: String(item.label || value) };
            }).filter(Boolean);
        }

        function loadOverrideCatalog() {
            if (overrideCatalog.loading) return overrideCatalog.loading;
            overrideCatalog.loading = Promise.all([
                fetchImpl('/api/projects', { credentials: 'include', cache: 'no-store' })
                    .then((r) => r.json()).catch(() => null),
                fetchImpl('/api/agents', { credentials: 'include', cache: 'no-store' })
                    .then((r) => r.json()).catch(() => null),
            ]).then(([pj, ag]) => {
                overrideCatalog.projects = (pj && pj.success && Array.isArray(pj.data)) ? pj.data : [];
                overrideCatalog.agents = (ag && ag.success && Array.isArray(ag.agents)) ? ag.agents : [];
                overrideCatalog.loading = null;
                return overrideCatalog;
            });
            return overrideCatalog.loading;
        }

        function overrideAgentEntries() {
            const out = [];
            const seen = {};
            (overrideCatalog.agents || []).forEach((a) => {
                if (!a || a.sticky === false || a.stickySession === false) return;
                const id = String(a.id || '').toLowerCase();
                if (!id || seen[id]) return;
                seen[id] = true;
                let prefix = String(a.prefix || a.slash || ('/' + id));
                if (prefix.charAt(0) !== '/') prefix = '/' + prefix;
                if (prefix.charAt(prefix.length - 1) !== ' ') prefix += ' ';
                out.push({
                    id,
                    prefix,
                    label: a.label || id,
                    category: a.category || id,
                });
            });
            return out;
        }

        function findOverrideProjectById(id) {
            return (overrideCatalog.projects || []).find((p) => p && String(p.id) === String(id)) || null;
        }

        function resolveOverrideProject(raw) {
            if (!raw) return null;
            if (raw.id != null) {
                const byId = findOverrideProjectById(raw.id);
                if (byId) return byId;
            }
            const want = String(raw.path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
            if (want) {
                const byPath = (overrideCatalog.projects || []).find((p) => {
                    const path = String((p && p.path) || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
                    return path && path === want;
                });
                if (byPath) return byPath;
            }
            if (raw.name) {
                const wantName = String(raw.name).toLowerCase();
                const byName = (overrideCatalog.projects || []).find(
                    (p) => String((p && p.name) || '').toLowerCase() === wantName
                );
                if (byName) return byName;
            }
            return raw;
        }

        function writeOverrideProject(proj) {
            const target = overrideTarget;
            if (!target) return;
            if (target.type === 'group') setGroupProjectOverride(target.id, proj);
            else setSpaceProjectOverride(target.id, proj);
        }

        function writeOverrideAgent(agent) {
            const target = overrideTarget;
            if (!target) return;
            if (target.type === 'group') setGroupAgentOverride(target.id, agent);
            else setSpaceAgentOverride(target.id, agent);
        }

        function patchOverrideAgent(patch) {
            const rec = overrideRecord(overrideTarget);
            const prev = (rec && rec.agentOverride) || {};
            writeOverrideAgent(Object.assign({}, prev, patch || {}));
        }

        function overrideAgentSpec(agentId) {
            const id = String(agentId || '').toLowerCase();
            if (!id) return null;
            const row = (overrideCatalog.agents || []).find((a) => String((a && a.id) || '').toLowerCase() === id);
            if (!row) return null;
            const live = () => ((overrideRecord(overrideTarget) || {}).agentOverride || {});
            const modelItems = catalogSettingOptions(row.models);
            const effortItems = catalogSettingOptions(row.efforts);
            const spec = {};
            if (modelItems.length) {
                spec.models = {
                    build: () => modelItems,
                    load: function () {},
                    current: () => String(live().model || ''),
                    set: (v) => patchOverrideAgent({ id, model: v }),
                    repaint: function () {},
                };
            }
            if (effortItems.length) {
                spec.efforts = {
                    build: () => effortItems,
                    load: function () {},
                    current: () => String(live().effort || ''),
                    set: (v) => patchOverrideAgent({ id, effort: v }),
                    repaint: function () {},
                };
            }
            return (spec.models || spec.efforts) ? spec : null;
        }

        function ensureOverrideChipConfig() {
            if (overrideChipConfig) return overrideChipConfig;
            const Chip = win.CuttleChipConfig;
            if (!Chip || typeof Chip.create !== 'function') return null;
            overrideChipConfig = Chip.create({
                pickerMode: 'defaults',
                escapeHtml: escapeHtml,
                getProjects: () => overrideCatalog.projects,
                getCurrentProject: () => {
                    const rec = overrideRecord(overrideTarget);
                    return resolveOverrideProject(rec && rec.projectOverride);
                },
                isProjectStarred: () => false,
                findProjectById: findOverrideProjectById,
                setChatProject: writeOverrideProject,
                getAgentEntries: overrideAgentEntries,
                getAgentSpec: overrideAgentSpec,
                applyAgent: writeOverrideAgent,
                worktreesEnabled: () => false,
            });
            return overrideChipConfig;
        }

        function openOverridePicker(kind, target) {
            overrideTarget = target;
            closeSpaceCtxLayers();
            const open = () => {
                const ctrl = ensureOverrideChipConfig();
                if (!ctrl) return;
                const rec = overrideRecord(target);
                if (kind === 'agent') {
                    const agent = rec && rec.agentOverride;
                    ctrl.openAgent(agent && (agent.id || '') || '');
                } else {
                    ctrl.openProject();
                }
            };
            Promise.resolve(loadOverrideCatalog()).then(open).catch(open);
        }

        function openSpaceTabMenu(x, y, spaceId) {
            const space = spacesState.spaces.find((s) => s && s.id === spaceId);
            if (!space) return;
            closeSpaceCtxLayers();
            trackSpaceCtxDismiss();
            const menu = doc.createElement('div');
            menu.className = 'shell-space-ctx';
            menu.setAttribute('role', 'menu');
            menu.setAttribute('aria-label', 'Space options');
            let html = '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="rename"><span>Rename</span></button>'
                + '<button type="button" class="shell-space-ctx-item has-sub" role="menuitem" data-ctx-act="color" aria-haspopup="menu"><span>Set color</span></button>'
                + '<button type="button" class="shell-space-ctx-item has-sub" role="menuitem" data-ctx-act="group" aria-haspopup="menu"><span>Add to group</span></button>'
                + '<div class="shell-space-ctx-sep" aria-hidden="true"></div>'
                + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="project-override"><span>Set Project Override</span></button>'
                + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="agent-override"><span>Set Agent Override</span></button>';
            if (space.groupId && spaceGroupById(space.groupId)) {
                html += '<div class="shell-space-ctx-sep" aria-hidden="true"></div>'
                    + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="remove-group"><span>Remove from group</span></button>';
            }
            menu.innerHTML = html;
            placeFixedPanel(menu, x, y);

            let openSub = null;
            let subFor = null;
            const closeOpenSub = () => {
                if (openSub) {
                    try { openSub.remove(); } catch (_) {}
                    spaceCtxEls = spaceCtxEls.filter((el) => el !== openSub);
                    openSub = null;
                }
                subFor = null;
                menu.querySelectorAll('.is-open').forEach((b) => b.classList.remove('is-open'));
            };
            const openSubFor = (btn, build) => {
                if (subFor === btn && openSub) {
                    closeOpenSub();
                    return;
                }
                closeOpenSub();
                subFor = btn;
                btn.classList.add('is-open');
                const sub = build();
                const r = btn.getBoundingClientRect();
                placeFixedPanel(sub, r.right - 2, r.top - 4);
                openSub = sub;
            };
            const buildColorSub = () => {
                const sub = doc.createElement('div');
                sub.className = 'shell-space-ctx shell-space-ctx-sub';
                sub.setAttribute('role', 'menu');
                sub.setAttribute('aria-label', 'Space color');
                sub.innerHTML = spaceCtxSwatchesHtml(space.color || null)
                    + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-color="">'
                    + '<span>None</span></button>';
                sub.addEventListener('click', (e) => {
                    const btn = e.target.closest('[data-color]');
                    if (!btn || !sub.isConnected) return;
                    e.preventDefault();
                    e.stopPropagation();
                    const hex = btn.dataset.color || '';
                    const id = spaceId;
                    closeSpaceCtxLayers();
                    setSpaceColor(id, hex || null);
                });
                return sub;
            };
            const buildGroupSub = () => {
                const sub = doc.createElement('div');
                sub.className = 'shell-space-ctx shell-space-ctx-sub';
                sub.setAttribute('role', 'menu');
                sub.setAttribute('aria-label', 'Add to group');
                const groups = ensureSpaceGroupsArray();
                let h = '<button type="button" class="shell-space-ctx-item" role="menuitem" data-new-group>'
                    + '<span>New group</span></button>';
                if (groups.length) h += '<div class="shell-space-ctx-sep" aria-hidden="true"></div>';
                groups.forEach((g) => {
                    if (!g) return;
                    const color = CuttleSpaces.sanitizeColor(g.color) || CuttleSpaces.DEFAULT_COLOR;
                    h += '<button type="button" class="shell-space-ctx-item" role="menuitem" data-gid="' + escapeHtml(g.id) + '">'
                        + '<span class="shell-ctx-dot" style="--swatch:' + color + '" aria-hidden="true"></span>'
                        + '<span class="shell-ctx-label">' + (escapeHtml(g.name) || 'Unnamed group') + '</span>'
                        + (space.groupId === g.id ? '<span class="shell-ctx-check" aria-hidden="true">✓</span>' : '')
                        + '</button>';
                });
                sub.innerHTML = h;
                sub.addEventListener('click', (e) => {
                    const id = spaceId;
                    if (e.target.closest('[data-new-group]')) {
                        e.preventDefault();
                        e.stopPropagation();
                        closeSpaceCtxLayers();
                        const group = createSpaceGroupForSpace(id);
                        if (group) openSpaceGroupBubbleFor(group.id, id, true);
                        return;
                    }
                    const btn = e.target.closest('[data-gid]');
                    if (!btn || !sub.isConnected) return;
                    e.preventDefault();
                    e.stopPropagation();
                    closeSpaceCtxLayers();
                    setSpaceGroup(id, btn.dataset.gid || null);
                });
                return sub;
            };

            menu.querySelectorAll('[data-ctx-act]').forEach((btn) => {
                btn.addEventListener('pointerenter', () => {
                    const act = btn.dataset.ctxAct;
                    if (act === 'color') openSubFor(btn, buildColorSub);
                    else if (act === 'group') openSubFor(btn, buildGroupSub);
                    else closeOpenSub();
                });
                btn.addEventListener('click', (e) => {
                    const act = btn.dataset.ctxAct;
                    const id = spaceId;
                    if (act === 'color') { e.stopPropagation(); openSubFor(btn, buildColorSub); return; }
                    if (act === 'group') { e.stopPropagation(); openSubFor(btn, buildGroupSub); return; }
                    e.preventDefault();
                    e.stopPropagation();
                    if (act === 'project-override') {
                        openOverridePicker('project', { type: 'space', id });
                        return;
                    }
                    if (act === 'agent-override') {
                        openOverridePicker('agent', { type: 'space', id });
                        return;
                    }
                    closeSpaceCtxLayers();
                    if (act === 'rename') startSpaceRename(id);
                    else if (act === 'remove-group') removeSpaceFromGroup(id);
                });
            });
        }

        /** Chrome-style group editor: name field, color palette, Ungroup / Delete group. */
        function openSpaceGroupBubbleFor(gid, anchorSpaceId, focusName) {
            const group = spaceGroupById(gid);
            if (!group) return;
            closeSpaceCtxLayers();
            trackSpaceCtxDismiss();
            const bubble = doc.createElement('div');
            bubble.className = 'shell-group-bubble';
            bubble.setAttribute('role', 'dialog');
            bubble.setAttribute('aria-label', 'Edit group');
            bubble.innerHTML = '<input type="text" class="shell-group-name" maxlength="40"'
                + ' placeholder="Name group" aria-label="Group name"'
                + ' value="' + escapeHtml(group.name || '').replace(/"/g, '&quot;') + '">'
                + spaceCtxSwatchesHtml(group.color)
                + '<div class="shell-group-overrides">'
                + '<button type="button" class="shell-group-btn" data-group-act="project-override">Set Project Override</button>'
                + '<button type="button" class="shell-group-btn" data-group-act="agent-override">Set Agent Override</button>'
                + '</div>'
                + '<div class="shell-group-actions">'
                + '<button type="button" class="shell-group-btn" data-group-act="ungroup">Ungroup</button>'
                + '<button type="button" class="shell-group-btn shell-group-btn-danger" data-group-act="delete">Delete group</button>'
                + '</div>';
            renderSpaceTabs();
            const pill = doc.querySelector('.shell-space-group[data-group-id="' + win.CSS.escape(gid) + '"]');
            if (pill) pill.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            const anchor = pill
                || (anchorSpaceId && doc.querySelector('.shell-space-tab[data-space-id="' + win.CSS.escape(anchorSpaceId) + '"]'));
            const r = anchor ? anchor.getBoundingClientRect() : { left: 8, bottom: 40 };
            placeFixedPanel(bubble, r.left, (r.bottom || 40) + 6);

            const input = bubble.querySelector('.shell-group-name');
            if (focusName && input) {
                input.focus();
                input.select();
            }
            input.addEventListener('input', () => {
                renameSpaceGroup(gid, input.value);
                renderSpaceTabs(); // pill text follows live; the bubble keeps focus
            });
            input.addEventListener('keydown', (e) => {
                e.stopPropagation();
                if (e.key === 'Enter') { e.preventDefault(); closeSpaceCtxLayers(); }
                else if (e.key === 'Escape') { e.preventDefault(); closeSpaceCtxLayers(); }
            });
            ['click', 'dblclick', 'mousedown', 'contextmenu'].forEach((evt) => {
                input.addEventListener(evt, (e) => e.stopPropagation());
            });
            let deleteArmed = false;
            bubble.addEventListener('click', (e) => {
                const swatch = e.target.closest('[data-color]');
                if (swatch) {
                    e.stopPropagation();
                    setSpaceGroupColor(gid, swatch.dataset.color || null);
                    bubble.querySelectorAll('.shell-ctx-swatch').forEach((el) => {
                        el.classList.toggle('is-selected', el.dataset.color === spaceGroupById(gid).color);
                    });
                    return;
                }
                const btn = e.target.closest('[data-group-act]');
                if (!btn) return;
                e.preventDefault();
                e.stopPropagation();
                if (btn.dataset.groupAct === 'project-override') {
                    openOverridePicker('project', { type: 'group', id: gid });
                    return;
                }
                if (btn.dataset.groupAct === 'agent-override') {
                    openOverridePicker('agent', { type: 'group', id: gid });
                    return;
                }
                if (btn.dataset.groupAct === 'ungroup') {
                    closeSpaceCtxLayers();
                    dissolveSpaceGroup(gid);
                    return;
                }
                if (btn.dataset.groupAct === 'delete') {
                    if (!deleteArmed) {
                        deleteArmed = true;
                        btn.classList.add('is-armed');
                        btn.textContent = 'Click again to confirm';
                        return;
                    }
                    closeSpaceCtxLayers();
                    deleteSpaceGroupWithSpaces(gid);
                }
            });
        }

        function setupSpaceTabMenus(hostEl) {
            if (!hostEl || hostEl.dataset.spaceMenus === '1') return;
            hostEl.dataset.spaceMenus = '1';
            hostEl.addEventListener('click', (e) => {
                const pill = e.target.closest('.shell-space-group');
                if (pill && pill.dataset.groupId) {
                    e.stopPropagation();
                    toggleSpaceGroupCollapsed(pill.dataset.groupId);
                }
            });
            hostEl.addEventListener('dblclick', (e) => {
                // Pills must not bubble to the titlebar maximize gesture.
                if (e.target.closest('.shell-space-group')) e.stopPropagation();
            });
            hostEl.addEventListener('contextmenu', (e) => {
                const pill = e.target.closest('.shell-space-group');
                if (pill && pill.dataset.groupId) {
                    e.preventDefault();
                    e.stopPropagation();
                    openSpaceGroupBubbleFor(pill.dataset.groupId, null, false);
                    return;
                }
                const tab = e.target.closest('.shell-space-tab');
                if (!tab || !tab.dataset.spaceId) return;
                e.preventDefault();
                e.stopPropagation();
                openSpaceTabMenu(e.clientX, e.clientY, tab.dataset.spaceId);
            });
        }

        /** Measure the tab strip for the canonical drop computation (DOM read only). */
        function measureSpaceDropLanes(host, draggedId) {
            const lanes = [];
            const tabRects = {};
            const sleeveRects = {};
            host.querySelectorAll('.shell-space-tab, .shell-space-group-sleeve').forEach((el) => {
                if (el.classList.contains('shell-space-tab')) {
                    const id = el.dataset.spaceId;
                    if (!id || id === draggedId) return;
                    const r = el.getBoundingClientRect();
                    lanes.push({ t: 'tab', id });
                    tabRects[id] = { left: r.left, width: r.width };
                } else if (el.dataset.groupId) {
                    const r = el.getBoundingClientRect();
                    lanes.push({ t: 'sleeve', gid: el.dataset.groupId });
                    sleeveRects[el.dataset.groupId] = { left: r.left, width: r.width };
                }
            });
            return { lanes, tabRects, sleeveRects };
        }

        /** One canonical drop target for preview AND commit (spaces_drop.js). */
        function computeSpaceDropTarget(host, draggedId, x) {
            const visibleIds = Array.from(host.querySelectorAll('.shell-space-tab')).map((t) => t.dataset.spaceId);
            const m = measureSpaceDropLanes(host, draggedId);
            return CuttleSpaces.computeDropTarget({
                spaces: spacesState.spaces,
                groups: spacesState.groups,
                visibleIds,
                draggedId,
                lanes: m.lanes,
                tabRects: m.tabRects,
                sleeveRects: m.sleeveRects,
                x,
            });
        }

        /** Position the dragged tab element per the canonical preview directive. */
        function placeDraggedSpaceTab(host, tab, target) {
            const place = (target && !target.noop && target.place) || { end: true };
            const tabElFor = (id) => {
                try {
                    return host.querySelector('.shell-space-tab[data-space-id="' + win.CSS.escape(id) + '"]');
                } catch (_) { return null; }
            };
            const sleeveElFor = (gid) => {
                try {
                    return host.querySelector('.shell-space-group-sleeve[data-group-id="' + win.CSS.escape(gid) + '"]');
                } catch (_) { return null; }
            };
            if (place.park) {
                const sleeve = sleeveElFor(place.park);
                if (sleeve && (tab.parentElement !== sleeve || tab.nextElementSibling !== null)) {
                    sleeve.appendChild(tab);
                }
            } else if (place.beforeTab) {
                const ref = tabElFor(place.beforeTab);
                if (ref && tab.nextElementSibling !== ref) ref.parentElement.insertBefore(tab, ref);
            } else if (place.beforeSleeve) {
                const ref = sleeveElFor(place.beforeSleeve);
                if (ref && tab.nextElementSibling !== ref) host.insertBefore(tab, ref);
            } else if (host.lastElementChild !== tab) {
                host.appendChild(tab);
            }
        }

        /** Drag a tab sideways to reorder; it follows the pointer while siblings reflow around it. */
        function setupSpaceTabDrag(host) {
            const THRESHOLD = 4;
            let drag = null;
            let suppressClick = false;

            const finish = (commit) => {
                if (!drag) return;
                const { tab, active, pointerId, groupId, beforeId, target } = drag;
                drag = null;
                if (!active) return;
                tab.classList.remove('is-dragging');
                tab.style.transform = '';
                host.classList.remove('is-reordering');
                doc.body.classList.remove('is-reordering-spaces');
                if (tab.hasPointerCapture(pointerId)) tab.releasePointerCapture(pointerId);
                if (commit) {
                    // Commit consumes the same canonical target the preview showed:
                    // no re-derivation from DOM order, no separate neighbor pass.
                    if (groupId) {
                        if (CuttleSpaces.moveGroupBefore(spacesState, groupId, beforeId)) persistSpacesState();
                    } else {
                        if (target && !target.noop) {
                            const res = CuttleSpaces.applyDropTarget(spacesState, target, tab.dataset.spaceId);
                            if (res.orderChanged || res.groupChanged || res.pruned) persistSpacesState();
                        }
                    }
                }
                renderSpaceTabs();
                // The pointerup still produces a click on the tab; don't treat the drop as a switch.
                suppressClick = true;
                win.setTimeout(() => { suppressClick = false; }, 0);
            };

            host.addEventListener('pointerdown', (e) => {
                if (e.button !== 0 || spacesState.spaces.length < 2) return;
                const pill = e.target.closest('.shell-space-group');
                const tab = pill ? pill.closest('.shell-space-group-sleeve') : e.target.closest('.shell-space-tab');
                if (!tab || e.target.closest('.shell-space-close, .shell-space-rename')) return;
                drag = { tab, groupId: pill?.dataset.groupId, pointerId: e.pointerId, startX: e.clientX, originLeft: tab.getBoundingClientRect().left, tx: 0, active: false };
            });

            // Move/up/cancel ride on window (capture): the strip is small and the page
            // below is all iframes, which swallow events aimed at the strip. While a
            // drag is active, content iframes also lose hit-testing (see
            // body.is-reordering-spaces) so a release over page content still lands.
            win.addEventListener('pointermove', (e) => {
                if (!drag || e.pointerId !== drag.pointerId) return;
                // A press whose release was missed (off-window pointerup) must never
                // start a button-less reorder on the next hover — cancel it instead.
                if (e.buttons === 0) { finish(false); return; }
                const { tab } = drag;
                if (!drag.active) {
                    if (Math.abs(e.clientX - drag.startX) < THRESHOLD) return;
                    drag.active = true;
                    if (drag.groupId) {
                        // A group can have multiple visible runs after individual tab moves.
                        // Gather every run into the dragged sleeve; cancel re-renders the original.
                        const members = new Map(Array.from(host.querySelectorAll('.shell-space-tab'))
                            .filter((member) => member.dataset.groupId === drag.groupId)
                            .map((member) => [member.dataset.spaceId, member]));
                        spacesState.spaces.forEach((space) => {
                            const member = members.get(space.id);
                            if (member) tab.appendChild(member);
                        });
                        host.querySelectorAll('.shell-space-group-sleeve').forEach((sleeve) => {
                            if (sleeve === tab || sleeve.dataset.groupId !== drag.groupId) return;
                            sleeve.remove();
                        });
                    }
                    tab.setPointerCapture(e.pointerId);
                    tab.classList.add('is-dragging');
                    host.classList.add('is-reordering');
                    doc.body.classList.add('is-reordering-spaces');
                }
                // Preview consumes the canonical target: the tab renders where the
                // drop would commit it, and the sleeve highlight matches the
                // membership the drop would assign (no separate hover rule).
                const target = drag.groupId ? {} : computeSpaceDropTarget(host, tab.dataset.spaceId, e.clientX);
                drag.target = target;
                if (drag.groupId) {
                    const ref = Array.from(host.children).find((el) => {
                        if (el === tab) return false;
                        const r = el.getBoundingClientRect();
                        return e.clientX < r.left + r.width / 2;
                    });
                    drag.beforeId = ref
                        ? ref.dataset.spaceId || spacesState.spaces.find((s) => s.groupId === ref.dataset.groupId)?.id || null
                        : null;
                    host.insertBefore(tab, ref || null);
                } else {
                    placeDraggedSpaceTab(host, tab, target);
                }
                // Hovering a sleeve lights its boundary exactly when the drop joins.
                host.querySelectorAll('.shell-space-group-sleeve.is-drop-target').forEach((el) => {
                    el.classList.remove('is-drop-target');
                });
                if (target.groupId) {
                    try {
                        host.querySelector(
                            '.shell-space-group-sleeve[data-group-id="' + win.CSS.escape(target.groupId) + '"]'
                        )?.classList.add('is-drop-target');
                    } catch (_) {}
                }
                const rect = tab.getBoundingClientRect();
                const layoutLeft = rect.left - drag.tx;
                const hostRect = host.getBoundingClientRect();
                let left = drag.originLeft + (e.clientX - drag.startX);
                left = Math.max(hostRect.left, Math.min(left, hostRect.right - rect.width));
                drag.tx = left - layoutLeft;
                tab.style.transform = 'translateX(' + drag.tx + 'px)';
            });

            win.addEventListener('pointerup', (e) => {
                if (drag && e.pointerId === drag.pointerId) finish(true);
            }, true);
            win.addEventListener('pointercancel', (e) => {
                if (drag && e.pointerId === drag.pointerId) finish(false);
            }, true);
            // Releasing off-window delivers no pointerup; snap back instead of
            // floating the tab until the next hover cancels the gesture.
            win.addEventListener('blur', () => { if (drag) finish(false); });
            win.addEventListener('keydown', (e) => {
                if (drag?.active && e.key === 'Escape') {
                    e.preventDefault();
                    finish(false);
                }
            }, true);
            host.addEventListener('click', (e) => {
                if (!suppressClick) return;
                e.stopPropagation();
                e.preventDefault();
            }, true);
        }

        /** Wire the tab strip: selection, rename, add/close, menus, drag hook,
         *  storage wake and the initial activity poll. Called once by app_shell. */
        function setup() {
            const hostEl = doc.getElementById('shellSpacesTabs');
            if (!hostEl) return;
            persistSpacesState();
            renderSpaceTabs();

            setupTabOverflow(hostEl);
            const tabId = (e) => e.target.closest('.shell-space-tab')?.dataset.spaceId || '';
            hostEl.addEventListener('click', (e) => {
                const id = tabId(e);
                if (!id) return;
                if (e.target.closest('.shell-space-close')) {
                    e.stopPropagation();
                    closeSpace(id);
                    return;
                }
                switchSpace(id);
            });
            // Titlebar double-click maximizes the window; renaming must not bubble there.
            hostEl.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                const id = tabId(e);
                if (id && !e.target.closest('.shell-space-close')) startSpaceRename(id);
            });
            hostEl.addEventListener('auxclick', (e) => {
                const id = tabId(e);
                if (id && e.button === 1) {
                    e.preventDefault();
                    closeSpace(id);
                }
            });
            hostEl.addEventListener('keydown', (e) => {
                const id = tabId(e);
                if (!id) return;
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchSpace(id); }
                else if (e.key === 'F2') { e.preventDefault(); startSpaceRename(id); }
            });
            const addBtn = doc.getElementById('shellSpacesAdd');
            addBtn?.addEventListener('click', () => addSpace());
            addBtn?.addEventListener('dblclick', (e) => e.stopPropagation());
            setupSpaceTabDrag(hostEl);
            setupSpaceTabMenus(hostEl);
            // Chat iframes write unread prefs + followups to the same localStorage —
            // refresh dots without waiting for the next heartbeat.
            win.addEventListener('storage', (e) => {
                if (!e || !e.key) return;
                if (CuttleSessionPrefs.isKey(e.key)) shellSessionPrefs.invalidate();
                if (CuttleSessionPrefs.isKey(e.key) || e.key === 'chatSessions') {
                    scheduleSpaceActivityPoll(false);
                }
            });
            activityBroker.start();
            win.addEventListener('pagehide', () => activityBroker.dispose());
            scheduleSpaceActivityPoll(true);
        }

        // Window contracts the shell/Electron consume by bare name.
        win.isSpaceCtxOpen = isSpaceCtxOpen;
        win.switchSpace = switchSpace;
        win.addSpace = addSpace;

        return {
            state: spacesState,
            persist: persistSpacesState,
            renderSpaceTabs,
            switchSpace,
            addSpace,
            closeSpace,
            exportSnapshot,
            importSnapshot,
            visibleActiveChatIds: visibleActiveSpaceChatIds,
            syncActivityTabs: syncSpaceActivityTabs,
            refreshLocalState: refreshSpaceActivityLocalState,
            notePush: (source, sessions, owned) => {
                if (source) {
                    try { CuttleSpaces.notePushSnapshot(source, sessions, owned); } catch (_) {}
                }
                refreshSpaceActivityLocalState();
                syncSpaceActivityTabs();
            },
            schedulePoll: scheduleSpaceActivityPoll,
            refreshFromServer: refreshSpaceActivityFromServer,
            isCtxOpen: isSpaceCtxOpen,
            paneDrop,
            setup,
        };
    }

    root.CuttleShellSpaces = { create };
})(typeof window !== 'undefined' ? window : globalThis);
