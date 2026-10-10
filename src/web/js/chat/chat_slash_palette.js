/* ================================================================
   Cuttle Chat — slash palette controller (chat_slash_palette.js).
   Owner: the `/` command palette DOM: rendering the menu list + type
   filter bar, keyboard navigation (arrows / Enter / Tab / Escape),
   filtering/assembly of the candidate items, dismiss lifecycle
   (Escape, outside pointerdown, frame blur) and the shared palette
   state ({ welcome, chat } cursors, selections, replace ranges).

   Built on the pure decisions in `chat_slash.js` (CuttleChatSlash.*):
   matching, ranking, category/bucket labels, starred rank, project
   and project-command palette items. This module never reimplements
   those; it gathers page-owned inputs (harness agents, model/effort
   catalogs, starred prefs, projects) through injected capabilities.

   Interface: `create(deps)` → one controller per chat frame exposing
   `ctx`, `contextKey`, `menuId`, `chipsRowId`, `parseAtCursor`,
   `hideMenu`, `filterItems`, `renderMenu`, `syncFromInput`,
   `consumeFilterText`, `handleKeyDown`, `setTypeFilter`, `getTypeFilter`,
   `applySelection`, `bind`. The page keeps the composer chips
   (`ctx.*.chips`) via `renderSlashChips` and passes every page symbol in
   as a capability. `applySlashSelectionInner` (405 lines) moved here as
   `applySelection` in Phase 2B stage 2; the page keeps the thin
   `applySlashSelection` choke point that also persists the draft.

   deps: { win, escapeHtmlInline, clamp,
           availableSlashCommands, cursorAgentSlashCommandsForPalette,
           harnessUsageSlashCommandsForPalette,
           buildProjectPaletteItems, buildProjectCommandPaletteItems,
           agentControls, readStarredSlashPrefixes, readStarredProject,
           normalizeStarredProjectPath, isSlashCommandStarred,
           isAgentDefaultStarred, isProjectStarred,
           toggleStarredProject, toggleStarredAgentDefault,
           toggleStarredSlashCommand, getModelStarSpec,
           loadAgentCapabilities, loadCursorStarredDefault,
           loadAllStarredDefaults, loadSlashPaletteSupplement,
           isPaletteLoading, hasActiveCursorAgentChip,
           loadCursorAgentModelsForPalette, applySlashSelection, saveDraft,
           findProjectById, setChatProject,
           autoResizeTextarea, autoResizeWelcomeTextarea,
           renderSlashChips, markStickyAgentCleared,
           persistStickySlashForCurrentSession, isOrphanedCompanionChip,
           onCursorModelPick }.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const SLASH_PALETTE_TYPE_ORDER = [
        'command',
        'project-cmd',
        'project',
        'skill',
        'cursor',
        'muse',
        'hermes',
        'opencode',
        'codex',
        'claude',
    ];
    const SLASH_PALETTE_TYPE_FILTER_KEY = 'cuttleSlashPaletteTypeFilter';

    /**
     * One slash-palette controller per chat frame.
     * @param {object} deps see header
     */
    function create(deps) {
        const win = deps.win;
        const doc = win.document;
        const S = root.CuttleChatSlash;
        const escapeHtmlInline = deps.escapeHtmlInline;
        const clamp = deps.clamp;
        const availableSlashCommands = deps.availableSlashCommands;
        const cursorAgentSlashCommandsForPalette = deps.cursorAgentSlashCommandsForPalette;
        const harnessUsageSlashCommandsForPalette = deps.harnessUsageSlashCommandsForPalette;
        const buildProjectPaletteItems = deps.buildProjectPaletteItems;
        const buildProjectCommandPaletteItems = deps.buildProjectCommandPaletteItems;
        const agentControls = deps.agentControls;
        const readStarredSlashPrefixes = deps.readStarredSlashPrefixes;
        const readStarredProject = deps.readStarredProject;
        const normalizeStarredProjectPath = deps.normalizeStarredProjectPath;
        const isSlashCommandStarred = deps.isSlashCommandStarred;
        const isAgentDefaultStarred = deps.isAgentDefaultStarred;
        const isProjectStarred = deps.isProjectStarred;
        const toggleStarredProject = deps.toggleStarredProject;
        const toggleStarredAgentDefault = deps.toggleStarredAgentDefault;
        const toggleStarredSlashCommand = deps.toggleStarredSlashCommand;
        const getModelStarSpec = deps.getModelStarSpec;
        const loadAgentCapabilities = deps.loadAgentCapabilities;
        const loadCursorStarredDefault = deps.loadCursorStarredDefault;
        const loadAllStarredDefaults = deps.loadAllStarredDefaults;
        const loadSlashPaletteSupplement = deps.loadSlashPaletteSupplement;
        const isPaletteLoading = deps.isPaletteLoading;
        const hasActiveCursorAgentChip = deps.hasActiveCursorAgentChip;
        const loadCursorAgentModelsForPalette = deps.loadCursorAgentModelsForPalette;
        const applySlashSelection = deps.applySlashSelection;
        const saveDraft = deps.saveDraft;
        const findProjectById = deps.findProjectById;
        const setChatProject = deps.setChatProject;
        const autoResizeTextarea = deps.autoResizeTextarea;
        const autoResizeWelcomeTextarea = deps.autoResizeWelcomeTextarea;
        const renderSlashChips = deps.renderSlashChips;
        const markStickyAgentCleared = deps.markStickyAgentCleared;
        const persistStickySlashForCurrentSession = deps.persistStickySlashForCurrentSession;
        const isOrphanedCompanionChip = deps.isOrphanedCompanionChip;
        const onCursorModelPick = deps.onCursorModelPick;

        const ctx = {
            welcome: {
                chips: [],
                selectedIndex: 0,
                filtered: [],
                typeCounts: {},
                replaceRange: null,
                paletteDismissed: false,
            },
            chat: {
                chips: [],
                selectedIndex: 0,
                filtered: [],
                typeCounts: {},
                replaceRange: null,
                paletteDismissed: false,
            },
        };

        let typeFilter = 'all';
        try {
            const saved = win.sessionStorage.getItem(SLASH_PALETTE_TYPE_FILTER_KEY);
            if (saved) typeFilter = saved;
        } catch (_) {}

        function getTypeFilter() {
            return typeFilter;
        }

        function setTypeFilter(next) {
            const v = next || 'all';
            typeFilter = v;
            try {
                win.sessionStorage.setItem(SLASH_PALETTE_TYPE_FILTER_KEY, v);
            } catch (_) {}
        }

        function contextKey(textarea) {
            if (!textarea) return null;
            if (textarea.id === 'welcomeChatInput') return 'welcome';
            if (textarea.id === 'chatInput') return 'chat';
            return null;
        }

        function chipsRowId(key) {
            return key === 'welcome' ? 'welcomeSlashChipsRow' : 'chatSlashChipsRow';
        }

        function menuId(key) {
            return key === 'welcome' ? 'welcomeSlashCommandMenu' : 'chatSlashCommandMenu';
        }

        function parseAtCursor(textarea) {
            const v = textarea.value;
            const cursor = textarea.selectionStart;
            const lineStart = v.lastIndexOf('\n', cursor - 1) + 1;
            const beforeInLine = v.slice(lineStart, cursor);
            // Allow spaces in the query so `/model Sonnet 4.6` and `/1M medium` keep
            // the palette open (previously [\w.-]* closed the menu on first space).
            const re = /(?:^|\s)(\/[^\n]*)$/;
            const m = beforeInLine.match(re);
            if (!m) return null;
            const full = m[0];
            const afterSlash = m[1].slice(1); // drop leading /
            return {
                filter: (afterSlash || '').toLowerCase(),
                replaceFrom: cursor - full.length,
                replaceTo: cursor,
            };
        }

        function hideMenu(key, opts) {
            const el = doc.getElementById(menuId(key));
            if (el) {
                el.hidden = true;
                el.innerHTML = '';
            }
            ctx[key].replaceRange = null;
            ctx[key].filtered = [];
            ctx[key].typeCounts = {};
            // Escape / outside-click: keep closed while the same `/…` token remains.
            if (opts && opts.dismiss) {
                ctx[key].paletteDismissed = true;
            }
        }

        // Click / tap outside an open palette (and outside its composer) closes it
        // like Escape: the `/…` token stays, but the menu won't reopen until the
        // token changes. Capture phase so menu-item picks (handled on click) and
        // textarea focus are unaffected — both targets are skipped below.
        function bind() {
            doc.addEventListener('pointerdown', (e) => {
                const pairs = { chat: 'chatInput', welcome: 'welcomeChatInput' };
                for (const key of Object.keys(pairs)) {
                    const menu = doc.getElementById(menuId(key));
                    if (!menu || menu.hidden) continue;
                    const ta = doc.getElementById(pairs[key]);
                    const t = e.target;
                    if ((ta && (t === ta || ta.contains(t))) || menu.contains(t)) continue;
                    hideMenu(key, { dismiss: true });
                }
            }, true);

            // Focus leaving this iframe also means clicking off the palette, e.g.
            // into a neighboring pane or the shell's navigation rail.
            win.addEventListener('blur', () => {
                for (const key of ['chat', 'welcome']) {
                    const menu = doc.getElementById(menuId(key));
                    if (menu && !menu.hidden) hideMenu(key, { dismiss: true });
                }
            });
        }

        /** `/restart <mode>` entries. Native Cuttle control — never an agent prompt. */
        function buildRestartPaletteItems(filterLower) {
            const f = (filterLower || '').trim();
            if (!f) return [];
            const defs = [
                ['/restart status', 'Restart status', 'Show restart state and active work — no agent call'],
                ['/restart graceful', 'Restart Flask (graceful)', 'Restart now if no other work is active'],
                ['/restart when-idle', 'Restart Flask (when idle)', 'Restart once active work finishes'],
                ['/restart force --yes', 'Restart Flask (force)', 'Interrupt active work and restart now'],
            ];
            return defs.map(([command, label, hint]) => ({
                category: 'command',
                prefix: command,
                label: label,
                hint: hint,
                meta: command,
                keywords: 'restart flask daemon reload ' + command.replace(/^\//, ''),
                controlCommand: true,
            }));
        }

        function filterItems(filter) {
            const f = (filter || '').toLowerCase();
            const cmds = availableSlashCommands().filter((c) => S.slashPaletteItemMatches(c, f));
            const cursorCmds = cursorAgentSlashCommandsForPalette().filter((c) =>
                S.slashPaletteItemMatches(c, f)
            );
            const harnessUsageCmds = harnessUsageSlashCommandsForPalette().filter((c) =>
                S.slashPaletteItemMatches(c, f)
            );
            const projs = buildProjectPaletteItems().filter((p) => S.slashPaletteItemMatches(p, f));
            const projCmds = buildProjectCommandPaletteItems().filter((p) => S.slashPaletteItemMatches(p, f));
            const cursorModels = agentControls.buildCursorModelPaletteItems(f);
            const museModels = agentControls.buildMuseModelPaletteItems(f);
            const museEfforts = agentControls.buildMuseEffortPaletteItems(f);
            const hermesModels = agentControls.buildHermesModelPaletteItems(f);
            const hermesEfforts = agentControls.buildHermesEffortPaletteItems(f);
            const opencodeModels = agentControls.buildOpenCodeModelPaletteItems(f);
            const opencodeEfforts = agentControls.buildOpenCodeEffortPaletteItems(f);
            const codexModels = agentControls.buildCodexModelPaletteItems(f);
            const codexEfforts = agentControls.buildCodexEffortPaletteItems(f);
            const claudeModels = agentControls.buildClaudeModelPaletteItems(f);
            const claudeEfforts = agentControls.buildClaudeEffortPaletteItems(f);
            const restartCmds = buildRestartPaletteItems(f).filter((c) =>
                S.slashPaletteItemMatches(c, f)
            );

            const starredSet = new Set(readStarredSlashPrefixes());
            const starredProject = readStarredProject();
            const starredProjectPath = normalizeStarredProjectPath(
                starredProject && starredProject.path
            );
            const starredProjectId =
                starredProject && starredProject.id != null
                    ? String(starredProject.id)
                    : null;
            function starredRank(item) {
                // Single implementation lives in chat_slash.js; the closure
                // supplies prefs gathered above.
                return S.starredRank(item, {
                    prefixes: Array.from(starredSet),
                    projectId: starredProjectId,
                    projectPath: starredProjectPath,
                });
            }

            function sortGroup(arr) {
                return arr.slice().sort((a, b) => {
                    const disabled = Number(!!a.disabled) - Number(!!b.disabled);
                    if (disabled !== 0) return disabled;
                    const relevance = S.slashPaletteItemSearchRank(a, f)
                        - S.slashPaletteItemSearchRank(b, f);
                    if (relevance !== 0) return relevance;
                    const sr = starredRank(a) - starredRank(b);
                    if (sr !== 0) return sr;
                    return String(a.label || '').localeCompare(String(b.label || ''), undefined, {
                        sensitivity: 'base',
                    });
                });
            }

            let merged;
            if (!f) {
                merged = []
                    .concat(sortGroup(cmds))
                    .concat(sortGroup(cursorCmds))
                    .concat(sortGroup(harnessUsageCmds))
                    .concat(sortGroup(projCmds))
                    .concat(sortGroup(projs));
            } else {
                merged = sortGroup(
                    cmds
                        .concat(restartCmds)
                        .concat(cursorCmds)
                        .concat(harnessUsageCmds)
                        .concat(projCmds)
                        .concat(projs)
                        .concat(cursorModels)
                        .concat(museModels)
                        .concat(museEfforts)
                        .concat(hermesModels)
                        .concat(hermesEfforts)
                        .concat(opencodeModels)
                        .concat(opencodeEfforts)
                        .concat(codexModels)
                        .concat(codexEfforts)
                        .concat(claudeModels)
                        .concat(claudeEfforts)
                );
            }

            // Counts for type badges (before applying the active type filter).
            const typeCounts = {};
            merged.forEach((item) => {
                const b = S.slashPaletteTypeBucket(item.category);
                typeCounts[b] = (typeCounts[b] || 0) + 1;
            });

            const active = typeFilter || 'all';
            const filtered =
                active === 'all'
                    ? merged
                    : merged.filter((item) => S.slashPaletteTypeBucket(item.category) === active);

            return { items: filtered, allItems: merged, typeCounts: typeCounts };
        }

        function buildFilterBarHtml(typeCounts) {
            const counts = typeCounts || {};
            const total = Object.keys(counts).reduce((n, k) => n + (counts[k] || 0), 0);
            const active = typeFilter || 'all';
            const badges = [];
            badges.push({
                id: 'all',
                label: 'All',
                count: total,
                active: active === 'all',
            });
            SLASH_PALETTE_TYPE_ORDER.forEach((bucket) => {
                const n = counts[bucket] || 0;
                if (!n) return;
                badges.push({
                    id: bucket,
                    label: S.slashPaletteTypeBadgeLabel(bucket),
                    count: n,
                    active: active === bucket,
                });
            });
            // If the saved filter points at an empty bucket, still show it so the
            // user can see why the list is empty and switch back.
            if (active !== 'all' && !(counts[active] > 0)) {
                badges.push({
                    id: active,
                    label: S.slashPaletteTypeBadgeLabel(active),
                    count: 0,
                    active: true,
                });
            }
            return (
                '<div class="slash-palette-filters" role="tablist" aria-label="Filter command types">' +
                badges
                    .map((b) => {
                        return (
                            '<button type="button" class="slash-palette-filter-badge' +
                            (b.active ? ' is-active' : '') +
                            '" role="tab" aria-selected="' +
                            (b.active ? 'true' : 'false') +
                            '" data-slash-type-filter="' +
                            escapeHtmlInline(b.id) +
                            '" data-kind="' +
                            escapeHtmlInline(b.id) +
                            '" title="' +
                            escapeHtmlInline(b.label) +
                            '">' +
                            '<span class="slash-palette-filter-label">' +
                            escapeHtmlInline(b.label) +
                            '</span>' +
                            '<span class="slash-palette-filter-count">' +
                            b.count +
                            '</span>' +
                            '</button>'
                        );
                    })
                    .join('') +
                '</div>'
            );
        }

        function renderMenu(key, textarea) {
            loadAgentCapabilities();
            loadCursorStarredDefault();
            loadAllStarredDefaults();
            const menu = doc.getElementById(menuId(key));
            if (!menu) return;
            const state = ctx[key];
            const items = state.filtered || [];
            const typeCounts = state.typeCounts || {};
            const sel = clamp(state.selectedIndex, 0, Math.max(0, items.length - 1));
            state.selectedIndex = items.length ? sel : 0;

            const filterBar = buildFilterBarHtml(typeCounts);
            let listHtml = '';
            if (!items.length) {
                listHtml =
                    '<div class="slash-command-menu-status" role="status">No matches in this filter</div>';
            } else {
                listHtml = items
                    .map((cmd, i) => {
                        const active = i === sel ? ' is-active' : '';
                        const cat = cmd.category || 'command';
                        const catClass =
                            cat === 'skill'
                                  ? ' slash-command-item--skill'
                                  : cat === 'project-cmd'
                                    ? ' slash-command-item--project-cmd'
                                    : cat === 'project'
                                      ? ' slash-command-item--project'
                                      : cat === 'cursor' || cat === 'cursor-model' || cat === 'cursor-cmd'
                                        ? ' slash-command-item--cursor'
                                        : cat === 'muse' || cat === 'muse-model' || cat === 'muse-effort' || cat === 'muse-cmd'
                                          ? ' slash-command-item--muse'
                                          : cat === 'hermes' || cat === 'hermes-model' || cat === 'hermes-effort' || cat === 'hermes-cmd'
                                            ? ' slash-command-item--hermes'
                                            : cat === 'opencode' || cat === 'opencode-model' || cat === 'opencode-effort' || cat === 'opencode-cmd'
                                              ? ' slash-command-item--opencode'
                                              : cat === 'codex' || cat === 'codex-model' || cat === 'codex-effort' || cat === 'codex-cmd'
                                                ? ' slash-command-item--codex'
                                          : ' slash-command-item--command';
                        const kind = escapeHtmlInline(S.slashPaletteCategoryLabel(cat));
                        const meta =
                            cat === 'command' || cat === 'cursor' || cat === 'muse'
                                || cat === 'hermes' || cat === 'opencode' || cat === 'codex'
                                ? escapeHtmlInline(cmd.prefix.replace(/\s+$/, ''))
                                : escapeHtmlInline(cmd.meta || cmd.prefix.trim());
                        const hint = cmd.hint ? escapeHtmlInline(cmd.hint) : '';
                        const hintBlock = hint
                            ? '<span class="slash-cmd-hint">' + hint + '</span>'
                            : '';
                        const canStar =
                            !!cmd.stickySession || cat === 'project' || !!getModelStarSpec(cat);
                        const _modelStarSpec = getModelStarSpec(cat);
                        const starred = canStar
                            && (_modelStarSpec
                                ? isAgentDefaultStarred(_modelStarSpec, cmd)
                                : (cat === 'project'
                                ? isProjectStarred({
                                    id: cmd.projectId,
                                    path: cmd.projectPath,
                                    name: cmd.projectName,
                                })
                                : isSlashCommandStarred(cmd.prefix)));
                        const starBtn = canStar
                            ? (
                                '<button type="button" class="slash-star-btn' +
                                (starred ? ' is-starred' : '') +
                                '" data-slash-star-idx="' +
                                i +
                                '" title="' +
                                (cat === 'project'
                                    ? (starred
                                        ? 'Unstar — stop using as default for new chats'
                                        : 'Star — default working project for new chats (only one)')
                                    : (starred
                                        ? 'Unstar — stop applying to new chats'
                                        : 'Star — apply to new Cuttle chats')) +
                                '" aria-label="' +
                                (starred
                                    ? (cat === 'project' ? 'Unstar project' : 'Unstar command')
                                    : (cat === 'project' ? 'Star project' : 'Star command')) +
                                '" aria-pressed="' +
                                (starred ? 'true' : 'false') +
                                '">' +
                                (starred ? '★' : '☆') +
                                '</button>'
                            )
                            : '';
                        return (
                            '<div role="option" class="slash-command-item' +
                            catClass +
                            active +
                            '" data-slash-idx="' +
                            i +
                            '" id="slash-opt-' +
                            key +
                            '-' +
                            i +
                            '">' +
                            '<button type="button" class="slash-command-item-main">' +
                            '<span class="slash-cmd-block">' +
                            '<span class="slash-cmd-row">' +
                            '<span class="slash-palette-kind" data-kind="' +
                            cat +
                            '">' +
                            kind +
                            '</span>' +
                            '<span class="slash-cmd-title">' +
                            escapeHtmlInline(cmd.label) +
                            '</span>' +
                            '</span>' +
                            hintBlock +
                            '</span>' +
                            '<span class="slash-cmd-meta" translate="no">' +
                            meta +
                            '</span>' +
                            '</button>' +
                            starBtn +
                            '</div>'
                        );
                    })
                    .join('');
            }

            menu.innerHTML =
                '<div class="slash-palette-list" role="listbox">' +
                listHtml +
                '</div>' +
                filterBar;
            menu.hidden = false;

            menu.querySelectorAll('[data-slash-type-filter]').forEach((btn) => {
                btn.addEventListener('mousedown', (e) => e.preventDefault());
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const next = btn.getAttribute('data-slash-type-filter') || 'all';
                    setTypeFilter(next);
                    syncFromInput(textarea);
                });
            });

            menu.querySelectorAll('.slash-command-item-main').forEach((btn) => {
                btn.addEventListener('mousedown', (e) => e.preventDefault());
                btn.addEventListener('click', () => {
                    const row = btn.closest('[data-slash-idx]');
                    const idx = parseInt(row && row.getAttribute('data-slash-idx'), 10);
                    if (!Number.isNaN(idx)) applySlashSelection(textarea, key, idx);
                });
            });
            menu.querySelectorAll('.slash-star-btn').forEach((btn) => {
                btn.addEventListener('mousedown', (e) => e.preventDefault());
                btn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const idx = parseInt(btn.getAttribute('data-slash-star-idx'), 10);
                    const cmd = state.filtered[idx];
                    if (!cmd) return;
                    if (cmd.category === 'project') {
                        toggleStarredProject({
                            id: cmd.projectId,
                            path: cmd.projectPath,
                            name: cmd.projectName,
                        });
                        renderMenu(key, textarea);
                        return;
                    }
                    const _starSpec = getModelStarSpec(cmd.category || '');
                    if (_starSpec) {
                        toggleStarredAgentDefault(_starSpec.agent, _starSpec.kind, cmd.modelId, textarea, key);
                        consumeFilterText(textarea);
                        return;
                    }
                    if (!cmd.stickySession) return;
                    toggleStarredSlashCommand(cmd.prefix);
                    if (!consumeFilterText(textarea)) renderMenu(key, textarea);
                });
            });
            const activeEl = menu.querySelector('.slash-command-item.is-active');
            if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
        }

        // Palette star actions consume the filter text that found the row (the chip
        // or default is now applied), so it must not linger as raw slash text: it
        // would double-compose on send and resurrect on New Chat (popping the
        // palette) via the composer draft. Only consumes when the whole composer
        // is the filter fragment — message body is never touched.
        function consumeFilterText(textarea) {
            if (!textarea) return false;
            const v = String(textarea.value || '');
            if (!v.trim()) return false;
            const parsed = parseAtCursor(textarea);
            if (!parsed) return false;
            const token = v.slice(parsed.replaceFrom, parsed.replaceTo);
            if (v.trim() !== token.trim()) return false;
            textarea.value = '';
            try { syncFromInput(textarea); } catch (_) {}
            try { saveDraft(); } catch (_) {}
            return true;
        }

        function syncFromInput(textarea) {
            const key = contextKey(textarea);
            if (!key) return;
            const menu = doc.getElementById(menuId(key));
            if (!menu) return;
            const parsed = parseAtCursor(textarea);
            if (!parsed) {
                ctx[key].paletteDismissed = false;
                hideMenu(key);
                return;
            }
            // After Escape (or outside click), do not reopen on every keystroke
            // while the line still starts with `/…`. Cleared when the slash token
            // disappears (user deletes `/` or moves off it).
            if (ctx[key].paletteDismissed) {
                return;
            }
            loadSlashPaletteSupplement();
            if (hasActiveCursorAgentChip(key)) {
                loadCursorAgentModelsForPalette();
            }
            const result = filterItems(parsed.filter);
            const items = result.items || [];
            const allItems = result.allItems || [];
            ctx[key].filtered = items;
            ctx[key].typeCounts = result.typeCounts || {};
            ctx[key].replaceRange = { from: parsed.replaceFrom, to: parsed.replaceTo };
            const _syncFilter = String(parsed.filter || '');
                    if (ctx[key].lastFilter !== _syncFilter) ctx[key].selectedIndex = 0;
                    else ctx[key].selectedIndex = clamp(ctx[key].selectedIndex, 0, Math.max(0, items.length - 1));
                    ctx[key].lastFilter = _syncFilter;
            // No matches at all (before type filter) — hide, or show loading.
            // Type-filter-only empty still opens the menu so badges stay usable.
            if (!allItems.length) {
                if (isPaletteLoading()) {
                    const menuWait = doc.getElementById(menuId(key));
                    if (menuWait) {
                        menuWait.hidden = false;
                        menuWait.innerHTML =
                            '<div class="slash-command-menu-status" role="status">Loading commands…</div>';
                    }
                } else {
                    hideMenu(key);
                }
                return;
            }
            renderMenu(key, textarea);
        }

        function handleKeyDown(event, textarea) {
            const key = contextKey(textarea);
            if (!key) return false;
            const menu = doc.getElementById(menuId(key));
            if (!menu || menu.hidden) return false;
            const state = ctx[key];
            const n = state.filtered.length;

            // Allow Escape even when the type filter emptied the list.
            if (event.key === 'Escape') {
                event.preventDefault();
                hideMenu(key, { dismiss: true });
                return true;
            }
            if (!n) return false;

            if (event.key === 'ArrowDown') {
                event.preventDefault();
                state.selectedIndex = (state.selectedIndex + 1) % n;
                renderMenu(key, textarea);
                return true;
            }
            if (event.key === 'ArrowUp') {
                event.preventDefault();
                state.selectedIndex = (state.selectedIndex - 1 + n) % n;
                renderMenu(key, textarea);
                return true;
            }
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                applySlashSelection(textarea, key, state.selectedIndex);
                return true;
            }
            if (event.key === 'Tab') {
                event.preventDefault();
                applySlashSelection(textarea, key, state.selectedIndex);
                return true;
            }
            return false;
        }

        /**
         * Apply a palette pick (click / Enter / Tab) to the composer. Strips the
         * typed `/…` filter fragment, then either performs a project/agent
         * setting change or stages a composer chip. Moved verbatim from
         * chat_page.js `applySlashSelectionInner` (Phase 2B stage 2): the page
         * keeps the `applySlashSelection` choke point that also persists the
         * draft.
         */
        function applySelection(textarea, key, idx) {
            const state = ctx[key];
            const cmd = state.filtered[idx];
            if (!cmd || !state.replaceRange) return;
            const from = state.replaceRange.from;
            const to = state.replaceRange.to;
            const v = textarea.value;

            function finish() {
                try { textarea.focus({ preventScroll: true }); } catch (_) { textarea.focus(); }
                if (key === 'chat') autoResizeTextarea();
                else autoResizeWelcomeTextarea();
            }

            function renderBoth() {
                renderSlashChips('welcome', doc.getElementById('welcomeChatInput'));
                renderSlashChips('chat', doc.getElementById('chatInput'));
            }

            function toast(msg, type) {
                (win.showToast || function () {})(msg, type);
            }

            // Project entries set the chat's working project (cwd) instead of
            // inserting a sticky command chip into the message text.
            if (cmd.category === 'project') {
                textarea.value = v.slice(0, from) + v.slice(to);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                const proj = findProjectById(cmd.projectId) || {
                    id: cmd.projectId,
                    name: cmd.projectName,
                    path: cmd.projectPath,
                };
                setChatProject(proj);
                finish();
                return;
            }

            // Muse model picks are a chat setting, not part of the next message:
            // apply and relabel the Muse chip. These entries only appear while
            // the Muse badge is applied, so no chip auto-add here.
            if (cmd.category === 'muse-model') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const mid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/muse\s+model\s+/i, '').trim();
                if (!mid || mid === '__refresh__' || cmd.museRefresh || cmd.disabled) {
                    if (mid === '__refresh__' || cmd.museRefresh) {
                        agentControls.persistMuseModelSelection('__refresh__');
                    }
                    state.replaceRange = null;
                    state.filtered = [];
                    state.paletteDismissed = false;
                    hideMenu(key);
                    finish();
                    return;
                }
                agentControls.persistMuseModelSelection(mid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                toast('Muse Code model: ' + agentControls.museModelLabel(mid), 'success');
                finish();
                return;
            }

            // Muse effort picks are a chat setting like model picks: apply,
            // toast, send no message. Badge-gated, so no chip auto-add either.
            if (cmd.category === 'muse-effort') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const eid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/muse\s+effort\s+/i, '').trim();
                agentControls.persistMuseEffortSelection(eid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                agentControls.repaintMuseUserBadges();
                toast('Muse Code reasoning effort: ' + (eid || 'default'), 'success');
                finish();
                return;
            }

            // Hermes model picks — chat setting, no message sent (badge-gated).
            if (cmd.category === 'hermes-model') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const mid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/hermes\s+model\s+/i, '').trim();
                agentControls.persistHermesModelSelection(mid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                toast('Hermes model: ' + agentControls.hermesModelLabel(mid), 'success');
                finish();
                return;
            }

            // Hermes effort picks — chat setting like model picks.
            if (cmd.category === 'hermes-effort') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const eid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/hermes\s+effort\s+/i, '').trim();
                agentControls.persistHermesEffortSelection(eid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                agentControls.repaintHermesUserBadges();
                toast('Hermes reasoning effort: ' + (eid || 'default'), 'success');
                finish();
                return;
            }

            // OpenCode model picks — chat setting, no message sent (badge-gated).
            if (cmd.category === 'opencode-model') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const mid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/opencode\s+model\s+/i, '').trim();
                if (!mid || mid === '__refresh__' || cmd.opencodeRefresh || cmd.disabled) {
                    if (mid === '__refresh__' || cmd.opencodeRefresh) {
                        agentControls.persistOpenCodeModelSelection('__refresh__');
                    }
                    state.replaceRange = null;
                    state.filtered = [];
                    state.paletteDismissed = false;
                    hideMenu(key);
                    finish();
                    return;
                }
                agentControls.persistOpenCodeModelSelection(mid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                toast('OpenCode model: ' + agentControls.opencodeModelLabel(mid), 'success');
                finish();
                return;
            }

            // OpenCode effort picks — chat setting like model picks.
            if (cmd.category === 'opencode-effort') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const eid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/opencode\s+effort\s+/i, '').trim();
                agentControls.persistOpenCodeEffortSelection(eid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                agentControls.repaintOpenCodeUserBadges();
                toast('OpenCode reasoning effort: ' + (eid || 'default'), 'success');
                finish();
                return;
            }

            // Codex model picks — chat setting, no message sent (badge-gated).
            if (cmd.category === 'codex-model') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const mid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/codex\s+model\s+/i, '').trim();
                if (!mid || mid === '__refresh__' || cmd.codexRefresh || cmd.disabled) {
                    if (mid === '__refresh__' || cmd.codexRefresh) {
                        agentControls.persistCodexModelSelection('__refresh__');
                    }
                    state.replaceRange = null;
                    state.filtered = [];
                    state.paletteDismissed = false;
                    hideMenu(key);
                    finish();
                    return;
                }
                agentControls.persistCodexModelSelection(mid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                toast('Codex model: ' + agentControls.codexModelLabel(mid), 'success');
                finish();
                return;
            }

            // Codex effort picks — chat setting like model picks.
            if (cmd.category === 'codex-effort') {
                if (cmd.disabled) return;
                textarea.value = v.slice(0, from) + v.slice(to);
                const eid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/codex\s+effort\s+/i, '').trim();
                agentControls.persistCodexEffortSelection(eid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                agentControls.repaintCodexUserBadges();
                toast('Codex reasoning effort: ' + (eid || 'default'), 'success');
                finish();
                return;
            }

            // Claude model picks — chat setting, no message sent (badge-gated).
            if (cmd.category === 'claude-model') {
                textarea.value = v.slice(0, from) + v.slice(to);
                const mid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/claude\s+model\s+/i, '').trim();
                if (!mid || mid === '__refresh__' || cmd.claudeRefresh || cmd.disabled) {
                    if (mid === '__refresh__' || cmd.claudeRefresh) {
                        agentControls.persistClaudeModelSelection('__refresh__');
                    }
                    state.replaceRange = null;
                    state.filtered = [];
                    state.paletteDismissed = false;
                    hideMenu(key);
                    finish();
                    return;
                }
                agentControls.persistClaudeModelSelection(mid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                toast('Claude Code model: ' + agentControls.claudeModelLabel(mid), 'success');
                finish();
                return;
            }

            // Claude effort picks — chat setting like model picks.
            if (cmd.category === 'claude-effort') {
                if (cmd.disabled) return;
                textarea.value = v.slice(0, from) + v.slice(to);
                const eid =
                    String(cmd.modelId || '').trim()
                    || String(cmd.prefix || '').replace(/^\/claude\s+effort\s+/i, '').trim();
                agentControls.persistClaudeEffortSelection(eid);
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderBoth();
                agentControls.repaintClaudeUserBadges();
                toast('Claude Code effort: ' + (eid || 'default'), 'success');
                finish();
                return;
            }

            textarea.value = v.slice(0, from) + v.slice(to);
            // Model picks compose as `/cursor /model <id>` but should not become a
            // second visible badge — the Cursor Agent chip already shows the
            // preferred model.
            if (cmd.category === 'cursor-model') {
                const mid =
                    String(cmd.modelId || '').trim() ||
                    String(cmd.prefix || '')
                        .replace(/^\/model\s+/i, '')
                        .trim();
                // Catalog refresh is a cursor-cmd chip now (staged + send). Ignore
                // any stale __refresh__ model rows.
                if (!mid || mid === '__refresh__' || cmd.disabled) {
                    state.replaceRange = null;
                    state.filtered = [];
                    state.paletteDismissed = false;
                    hideMenu(key);
                    finish();
                    return;
                }
                textarea.value = v.slice(0, from) + v.slice(to);
                if (mid) {
                    onCursorModelPick(mid);
                }
                // Drop any prior model chips so we don't stack /model entries.
                state.chips = state.chips.filter((c) => (c.category || '') !== 'cursor-model');
                state.chips.push({
                    prefix: cmd.prefix,
                    label: agentControls.preferredModelLabel(mid)
                        || String(cmd.label || mid).replace(/\s*\(current\)\s*$/i, '').trim(),
                    category: 'cursor-model',
                    modelId: mid || undefined,
                });
                const other = key === 'chat' ? 'welcome' : 'chat';
                ctx[other].chips = state.chips.slice();
                state.replaceRange = null;
                state.filtered = [];
                state.paletteDismissed = false;
                hideMenu(key);
                renderSlashChips(other, doc.getElementById(other === 'chat' ? 'chatInput' : 'welcomeChatInput'));
                renderSlashChips(key, textarea);
                finish();
                return;
            }
            // Exclusive sticky agent: only one agent badge at a time (Cursor vs Muse etc.)
            const _stickyMatch = S.SLASH_COMMANDS.find((s) => s.prefix === cmd.prefix && s.stickySession);
            if (_stickyMatch) {
                state.chips = state.chips.filter((c) => {
                    const m = S.SLASH_COMMANDS.find((s) => s.prefix === c.prefix);
                    if (m && m.stickySession) return false;
                    return !isOrphanedCompanionChip(c, cmd);
                });
            }
            state.chips.push({
                prefix: cmd.prefix,
                label: cmd.label,
                category: cmd.category || 'command',
            });
            state.replaceRange = null;
            state.filtered = [];
            state.paletteDismissed = false;
            hideMenu(key);
            // Sticky agent chips apply for the rest of the session — mirror to both composers.
            const match = S.SLASH_COMMANDS.find((s) => s.prefix === cmd.prefix);
            if (match && match.stickySession) {
                const other = key === 'chat' ? 'welcome' : 'chat';
                ctx[other].chips = state.chips.slice();
                renderSlashChips(other, doc.getElementById(other === 'chat' ? 'chatInput' : 'welcomeChatInput'));
                markStickyAgentCleared(false);
                persistStickySlashForCurrentSession(true);
            }
            renderSlashChips(key, textarea);
            finish();
        }

        return {
            ctx,
            contextKey,
            chipsRowId,
            menuId,
            parseAtCursor,
            hideMenu,
            filterItems,
            renderMenu,
            syncFromInput,
            consumeFilterText,
            handleKeyDown,
            setTypeFilter,
            getTypeFilter,
            applySelection,
            bind,
        };
    }

    const api = { create };

    const ns = (root.CuttleChatSlashPalette = root.CuttleChatSlashPalette || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
