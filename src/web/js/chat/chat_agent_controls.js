/* ================================================================
   Cuttle Chat — agent model/effort controls (chat_agent_controls.js).
   Owner: per-provider model/effort control state and its transitions
   (catalogs, selected values, source/count metadata, loading/loaded/
   tried flags, session keys, generation counters, dirty flags, starred
   maps, agent capabilities) plus the six provider model/effort
   palette loaders and palette-item builders. Sits over the pure
   `chat_agent_model.js`; the page keeps project-command/harness-agent
   supplement state, the label/repaint/badge families, and calls into
   `CuttleChatAgentControls.*` for persistence setters, session
   seeding/reset, the Codex send gate and adoption flush.

   No DOM, no page globals: every capability is injected through
   `create(deps)`. Transport is but thin fetch wrappers so provider
   differences (URLs, query params, payload shapes, refresh branches,
   favorite/current rows, hint/label text, toast text) stay explicit
   per provider — they are not collapsed into one template.

   Loaded after chat_agent_model.js / chat_slash.js and before
   chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Provider-only control state. The page merges its page-owned
     * supplement fields (project commands, harness agents, generic load
     * flags) into this object so out-of-scope page code keeps reading the
     * same fields; the controller is the sole writer of the provider
     * fields below.
     */
    function createProviderState() {
        return {
            cursorModels: [],
            preferredModel: 'auto',
            lastReportedModel: null,
            cursorModelsLoading: false,
            cursorModelsLoaded: false,
            cursorModelsKey: '',
            cursorModelsCount: 0,
            museModels: [],
            museModel: '',
            museModelsLoading: false,
            museModelsKey: '',
            museModelsTried: null,
            museModelsCount: 0,
            museModelsSource: '',
            museModelDirty: false,
            museEffortDirty: false,
            museEffort: '',
            museEffortLoading: false,
            museEffortKey: '',
            hermesModels: [],
            hermesModel: '',
            hermesModelsLoading: false,
            hermesModelsKey: '',
            hermesModelsTried: null,
            hermesModelDirty: false,
            hermesEffortDirty: false,
            hermesEffort: '',
            hermesEffortLoading: false,
            hermesEffortKey: '',
            opencodeModels: [],
            opencodeModel: '',
            opencodeModelsLoading: false,
            opencodeModelsKey: '',
            opencodeModelsTried: null,
            opencodeModelsSource: '',
            opencodeModelsCount: 0,
            opencodeModelDirty: false,
            opencodeEffortDirty: false,
            opencodeEffort: '',
            opencodeEffortLoading: false,
            opencodeEffortKey: '',
            codexModels: [],
            codexModel: '',
            codexModelsLoading: false,
            codexModelsKey: '',
            codexModelsTried: null,
            codexModelsSource: '',
            codexModelsCount: 0,
            codexCommonEfforts: [],
            codexModelDirty: false,
            codexEffortDirty: false,
            codexEffort: '',
            codexEffortLoading: false,
            codexEffortKey: '',
            claudeModels: [],
            claudeModel: '',
            claudeModelsLoading: false,
            claudeModelsKey: '',
            claudeModelsTried: null,
            claudeModelsSource: '',
            claudeModelsCount: 0,
            claudeCommonEfforts: [],
            claudeModelDirty: false,
            claudeEffortDirty: false,
            claudeEffort: '',
            claudeEffortLoading: false,
            claudeEffortKey: '',
            starredAgentModels: {},
            starredAgentEfforts: {},
            agentCapabilities: null,
            agentCapabilitiesLoading: false,
            cursorStarredLoaded: false,
            starredDefaultsLoading: false,
            starredDefaultsLoaded: false,
            sessionIdentity: null,
            cursorModelsGen: 0,
            museModelsGen: 0,
            hermesModelsGen: 0,
            opencodeModelsGen: 0,
            codexModelsGen: 0,
            claudeModelsGen: 0,
        };
    }

    /**
     * Build the controller. All state and capabilities are explicit:
     *   state                  provider state object (createProviderState)
     *   getSessionId()         current session id ('' for unsent composer)
     *   getProjectPath()       active project path ('' when none)
     *   hasActiveCursor()      cursor-agent chip present
     *   hasActiveMuse()        /muse chip present
     *   hasActiveHermes()      /hermes chip present
     *   hasActiveOpenCode()    /opencode chip present
     *   hasActiveCodex()       /codex chip present
     *   hasActiveHarness(id)   harness agent chip present (claude)
     *   hasCursorModelChip()   any cursor-model chip in either composer
     *   supportsEffort(id)     agent capability gate for effort rows
     *   fetch(url, opts)       transport (defaults to the ambient fetch)
     *   sessionMutationFetch(url, opts)  session-mutation-ordered transport
     *   beginFetch(field)      increment a generation counter in state
     *   isCurrentFetch(f,g)    generation still current
     *   captureMutation(sid,label) session-mutation token
     *   isMutationCurrent(tok) token still current
     *   renderSlashChips()     repaint both composers
     *   syncActiveSlashMenu()  re-sync the focused palette menu
     *   repaintUserBadges(id)  provider user-badge repaint
     *   persistStickySlashForCurrentSession()
     *   refreshHistoryAgentChips()
     *   toast(message, type)   showToast
     */
    function create(deps) {
        const d = deps || {};
        const state = d.state;
        const slashApi = root.CuttleChatSlash;

        const sessionId = () => (d.getSessionId ? d.getSessionId() : null);
        const sessionKey = () => (sessionId() != null ? String(sessionId()) : '');
        const itemMatches = (item, f) => slashApi.slashPaletteItemMatches(item, f);
        const renderChips = () => { if (d.renderSlashChips) d.renderSlashChips(); };
        const syncMenu = () => { if (d.syncActiveSlashMenu) d.syncActiveSlashMenu(); };
        const repaint = (id) => { if (d.repaintUserBadges) d.repaintUserBadges(id); };
        const persistSticky = () => { if (d.persistStickySlashForCurrentSession) d.persistStickySlashForCurrentSession(); };
        const refreshHistoryChips = () => { if (d.refreshHistoryAgentChips) d.refreshHistoryAgentChips(); };
        const toast = (msg, type) => { if (d.toast) d.toast(msg, type); };
        const captureMutation = (label) => (d.captureMutation ? d.captureMutation(sessionId(), label) : null);
        const isMutationCurrent = (tok) => (d.isMutationCurrent ? d.isMutationCurrent(tok) : true);
        const beginFetch = (field) => (d.beginFetch ? d.beginFetch(field) : 0);
        const isCurrentFetch = (field, gen) => (d.isCurrentFetch ? d.isCurrentFetch(field, gen) : true);
        const supportsEffort = (id) => (d.supportsEffort ? d.supportsEffort(id) : true);
        const doFetch = (url, opts) => (typeof d.fetch === 'function' ? d.fetch(url, opts) : fetch(url, opts));
        const doSessionMutationFetch = (url, opts) => (
            typeof d.sessionMutationFetch === 'function'
                ? d.sessionMutationFetch(url, opts)
                : doFetch(url, opts)
        );

        /** Commit hook shared by every loader: repaint chips + focused menu. */
        function finishProviderUi() {
            renderChips();
            syncMenu();
        }

        // ── Cursor ────────────────────────────────────────────────────────
        function loadCursorAgentModelsForPalette(force, opts) {
            const forceRefresh = !!(opts && opts.refresh);
            if (!force && !forceRefresh && !(d.hasActiveCursor && d.hasActiveCursor())) return;
            const path = d.getProjectPath ? d.getProjectPath() : '';
            const sid = sessionId();
            const key = path + '\x1f' + (sid != null ? String(sid) : '');
            if (
                !force &&
                !forceRefresh &&
                (state.cursorModelsLoading ||
                    (state.cursorModelsLoaded && state.cursorModelsKey === key))
            ) {
                return;
            }
            const _cursorModelsGen = beginFetch('cursorModelsGen');
            state.cursorModelsLoading = true;
            const qs = new URLSearchParams();
            if (path) qs.set('path', path);
            if (sid != null && sid !== '') qs.set('session', String(sid));
            if (forceRefresh) qs.set('refresh', '1');
            const url = qs.toString()
                ? '/api/cursor-agent/models?' + qs.toString()
                : '/api/cursor-agent/models';
            const mutationToken = captureMutation('cursor:model:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.cursorModels = j && j.success && Array.isArray(j.models) ? j.models : [];
                    state.preferredModel =
                        (!(d.hasCursorModelChip && d.hasCursorModelChip()) &&
                            isCurrentFetch('cursorModelsGen', _cursorModelsGen) &&
                            j && j.success ? j.preferredModel : null) ||
                        state.preferredModel || 'auto';
                    state.lastReportedModel = (j && j.lastReportedModel) || null;
                    state.cursorModelsKey = key;
                    state.cursorModelsCount =
                        (j && (j.count != null ? j.count : state.cursorModels.length)) || 0;
                    if (forceRefresh) {
                        const n = state.cursorModelsCount || 0;
                        const err = j && j.error ? String(j.error) : '';
                        toast(
                            err && !n
                                ? ('Cursor models refresh failed: ' + err)
                                : ('Cursor models refreshed (' + n + ')'),
                            err && !n ? 'error' : 'success'
                        );
                    }
                })
                .catch(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.cursorModels = [];
                    if (forceRefresh) toast('Cursor models refresh failed', 'error');
                })
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.cursorModelsLoaded = true;
                    state.cursorModelsLoading = false;
                    finishProviderUi();
                });
        }

        function buildCursorModelPaletteItems(filterLower) {
            if (!(d.hasActiveCursor && d.hasActiveCursor())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            let modelFilter = f;
            if (modelFilter === 'model') {
                modelFilter = '';
            } else if (modelFilter.startsWith('model ')) {
                modelFilter = modelFilter.slice(6).trim();
            }
            const showAllForModel = !modelFilter;
            loadCursorAgentModelsForPalette(false);
            const preferred = String(state.preferredModel || 'auto')
                .trim()
                .toLowerCase() || 'auto';
            const refreshItem = {
                category: 'cursor-cmd',
                prefix: '/model refresh',
                label: 'Refresh Cursor models',
                hint: 'Re-run agent models and reload the slash palette catalog',
                meta: 'refresh',
                keywords: 'cursor model refresh reload sync catalog agent models',
            };
            const items = [];
            if (!modelFilter || itemMatches(refreshItem, modelFilter)) {
                items.push(refreshItem);
            }
            const models = state.cursorModels || [];
            if (state.cursorModelsLoading && !models.length) {
                items.push({
                    category: 'cursor-model',
                    prefix: '/model',
                    label: 'Loading Cursor models…',
                    hint: 'Fetching agent models catalog',
                    meta: '',
                    keywords: 'cursor model loading',
                    modelId: '',
                    disabled: true,
                });
                return items;
            }
            const mapped = models
                .map((m) => {
                    const id = String(m.id || '').trim();
                    if (!id) return null;
                    const label = String(m.label || id).trim() || id;
                    const current = id.toLowerCase() === preferred;
                    const contextBits = (label.match(/\b\d+\s*[km]\b/gi) || []).join(' ');
                    return {
                        category: 'cursor-model',
                        prefix: '/model ' + id,
                        label: label + (current ? ' (current)' : ''),
                        hint: 'Set Cursor Agent model to ' + id,
                        meta: id,
                        keywords:
                            'cursor model ' +
                            id +
                            ' ' +
                            label +
                            ' ' +
                            id.replace(/[-_]+/g, ' ') +
                            ' ' +
                            contextBits,
                        modelId: id,
                    };
                })
                .filter(Boolean)
                .filter((item) => {
                    if (showAllForModel) return true;
                    return itemMatches(item, modelFilter);
                });
            return items.concat(mapped);
        }

        // ── Muse ──────────────────────────────────────────────────────────
        function loadMuseModelsForPalette(anyChat, opts) {
            if (!anyChat && !(d.hasActiveMuse && d.hasActiveMuse())) return;
            const key = sessionKey();
            const forceRefresh = !!(opts && opts.refresh);
            if (
                !forceRefresh &&
                (state.museModelsLoading ||
                    (state.museModels.length && state.museModelsKey === key) ||
                    state.museModelsTried === key)
            ) {
                return;
            }
            const _museModelsGen = beginFetch('museModelsGen');
            state.museModelsLoading = true;
            const params = new URLSearchParams();
            if (key) params.set('session', key);
            if (forceRefresh) params.set('refresh', '1');
            const qs = params.toString();
            const url = qs ? '/api/muse/models?' + qs : '/api/muse/models';
            const mutationToken = captureMutation('muse:model:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.museModels = j && j.success && Array.isArray(j.models) ? j.models : [];
                    state.museModel =
                        (!state.museModelDirty &&
                            isCurrentFetch('museModelsGen', _museModelsGen) &&
                            j && j.success ? (j.preferredModel || j.defaultModel) : null) ||
                        state.museModel || '';
                    state.museModelsKey = key;
                    state.museModelsCount =
                        (j && (j.count != null ? j.count : state.museModels.length)) || 0;
                    state.museModelsSource = (j && j.source) || '';
                    renderChips();
                    repaint('muse');
                    persistSticky();
                    refreshHistoryChips();
                    if (forceRefresh) {
                        const n = state.museModelsCount || 0;
                        const err = j && j.error ? String(j.error) : '';
                        const src = state.museModelsSource || '';
                        toast(
                            err && !n
                                ? ('Muse models refresh failed: ' + err)
                                : ('Muse models refreshed (' + n + (src ? ', ' + src : '') + ')'),
                            err && !n ? 'error' : 'success'
                        );
                    }
                })
                .catch(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (forceRefresh) toast('Muse models refresh failed', 'error');
                })
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.museModelsLoading = false;
                    state.museModelsTried = key;
                    finishProviderUi();
                });
        }

        function buildMuseModelPaletteItems(filterLower) {
            if (!(d.hasActiveMuse && d.hasActiveMuse())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadMuseModelsForPalette(true);
            const models = state.museModels || [];
            let modelFilter = f.replace(/^muse\s+/, '').replace(/^models?\b\s*/, '').trim();
            if (modelFilter === 'muse') modelFilter = '';
            const preferred = String(state.museModel || '').toLowerCase();
            const refreshItem = {
                category: 'muse-cmd',
                prefix: '/model refresh',
                label: 'Refresh Muse models',
                hint: 'Re-fetch Muse Spark ids from Meta Model API and reload the slash palette',
                meta: 'refresh',
                keywords: 'muse model refresh reload sync catalog meta api spark',
            };
            const items = [];
            if (!modelFilter || itemMatches(refreshItem, modelFilter)) {
                items.push(refreshItem);
            }
            if (state.museModelsLoading && !models.length) {
                items.push({
                    category: 'muse-model',
                    prefix: '/muse model',
                    label: 'Loading Muse models…',
                    hint: 'Fetching Muse Spark catalog',
                    meta: '',
                    keywords: 'muse model loading',
                    modelId: '',
                    museModel: true,
                    disabled: true,
                });
                return items;
            }
            const mapped = models
                .map((m) => {
                    const id = String((m && m.id) || '').trim();
                    if (!id) return null;
                    const label = String((m && m.label) || id).trim() || id;
                    const current = id.toLowerCase() === preferred;
                    return {
                        category: 'muse-model',
                        prefix: '/muse model ' + id,
                        label: label + (current ? ' (current)' : ''),
                        hint: (m && m.description) || ('Set Muse Code model to ' + id),
                        meta: id,
                        keywords: 'muse model spark ' + id + ' ' + label + ' ' + id.replace(/[-_]+/g, ' '),
                        modelId: id,
                        museModel: true,
                    };
                })
                .filter(Boolean)
                .filter((item) => (modelFilter ? itemMatches(item, modelFilter) : true));
            return items.concat(mapped);
        }

        function loadMuseEffortForPalette() {
            if (state.museEffortDirty) return;
            if (!(d.hasActiveMuse && d.hasActiveMuse())) return;
            const key = sessionKey();
            if (state.museEffortLoading || (state.museEffortKey === key && key)) return;
            const effortGeneration = beginFetch('museEffortGen');
            state.museEffortLoading = true;
            const url = key ? '/api/muse/effort?session=' + encodeURIComponent(key) : '/api/muse/effort';
            const mutationToken = captureMutation('muse:effort:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (state.museEffortDirty || !isCurrentFetch('museEffortGen', effortGeneration)) return;
                    state.museEffort = (j && j.preferredEffort) || '';
                    state.museEffortKey = key;
                    renderChips();
                    repaint('muse');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.museEffortLoading = false;
                });
        }

        function buildMuseEffortPaletteItems(filterLower) {
            if (!supportsEffort('muse')) return [];
            if (!(d.hasActiveMuse && d.hasActiveMuse())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadMuseEffortForPalette();
            let effortFilter = f.replace(/^muse\s+/, '').replace(/^efforts?\b\s*/, '').trim();
            if (effortFilter === 'effort') effortFilter = '';
            const preferred = String(state.museEffort || '').toLowerCase();
            return MUSE_EFFORT_LEVELS
                .map((id) => ({
                    category: 'muse-effort',
                    prefix: '/muse effort ' + id,
                    label: 'Effort ' + id + (id.toLowerCase() === preferred ? ' (current)' : ''),
                    hint: 'Set Muse Code reasoning effort to ' + id,
                    meta: id,
                    keywords: 'muse effort reasoning ' + id,
                    modelId: id,
                    museEffort: true,
                }))
                .filter((item) => (effortFilter ? itemMatches(item, effortFilter) : true));
        }

        // ── Hermes ────────────────────────────────────────────────────────
        function loadHermesModelsForPalette(anyChat) {
            if (!anyChat && !(d.hasActiveHermes && d.hasActiveHermes())) return;
            const key = sessionKey();
            if (
                state.hermesModelsLoading ||
                (state.hermesModels.length && state.hermesModelsKey === key) ||
                state.hermesModelsTried === key
            ) {
                return;
            }
            const modelsGeneration = beginFetch('hermesModelsGen');
            state.hermesModelsLoading = true;
            const url = key
                ? '/api/hermes/models?session=' + encodeURIComponent(key)
                : '/api/hermes/models';
            const mutationToken = captureMutation('hermes:model:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.hermesModels = j && j.success && Array.isArray(j.models) ? j.models : [];
                    if (!state.hermesModelDirty && isCurrentFetch('hermesModelsGen', modelsGeneration)) {
                        state.hermesModel = (j && j.preferredModel) || '';
                    }
                    state.hermesModelsKey = key;
                    renderChips();
                    repaint('hermes');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.hermesModelsLoading = false;
                    state.hermesModelsTried = key;
                    finishProviderUi();
                });
        }

        function buildHermesModelPaletteItems(filterLower) {
            if (!(d.hasActiveHermes && d.hasActiveHermes())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadHermesModelsForPalette(true);
            const models = state.hermesModels || [];
            if (!models.length) return [];
            let modelFilter = f.replace(/^hermes\s+/, '').replace(/^models?\b\s*/, '').trim();
            if (modelFilter === 'hermes') modelFilter = '';
            const preferred = String(state.hermesModel || '').toLowerCase();
            return models
                .map((m) => {
                    const id = String((m && m.id) || '').trim();
                    if (!id) return null;
                    const label = String((m && m.label) || id).trim() || id;
                    const current = id.toLowerCase() === preferred;
                    return {
                        category: 'hermes-model',
                        prefix: '/hermes model ' + id,
                        label: label + (current ? ' (current)' : ''),
                        hint: (m && m.description) || ('Set Hermes model to ' + id),
                        meta: id,
                        keywords: 'hermes model glm qwen ' + id + ' ' + label + ' ' + id.replace(/[-_]+/g, ' '),
                        modelId: id,
                        hermesModel: true,
                    };
                })
                .filter(Boolean)
                .filter((item) => (modelFilter ? itemMatches(item, modelFilter) : true));
        }

        function loadHermesEffortForPalette() {
            if (state.hermesEffortDirty) return;
            if (!(d.hasActiveHermes && d.hasActiveHermes())) return;
            const key = sessionKey();
            if (state.hermesEffortLoading || (state.hermesEffortKey === key && key)) return;
            const effortGeneration = beginFetch('hermesEffortGen');
            state.hermesEffortLoading = true;
            const url = key ? '/api/hermes/effort?session=' + encodeURIComponent(key) : '/api/hermes/effort';
            const mutationToken = captureMutation('hermes:effort:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (state.hermesEffortDirty || !isCurrentFetch('hermesEffortGen', effortGeneration)) return;
                    state.hermesEffort = (j && j.preferredEffort) || '';
                    state.hermesEffortKey = key;
                    renderChips();
                    repaint('hermes');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.hermesEffortLoading = false;
                });
        }

        function buildHermesEffortPaletteItems(filterLower) {
            if (!supportsEffort('hermes')) return [];
            if (!(d.hasActiveHermes && d.hasActiveHermes())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadHermesEffortForPalette();
            let effortFilter = f.replace(/^hermes\s+/, '').replace(/^efforts?\b\s*/, '').trim();
            if (effortFilter === 'effort') effortFilter = '';
            const preferred = String(state.hermesEffort || '').toLowerCase();
            return HERMES_EFFORT_LEVELS
                .map((id) => ({
                    category: 'hermes-effort',
                    prefix: '/hermes effort ' + id,
                    label: 'Effort ' + id + (id.toLowerCase() === preferred ? ' (current)' : ''),
                    hint: 'Set Hermes reasoning effort to ' + id,
                    meta: id,
                    keywords: 'hermes effort reasoning ' + id,
                    modelId: id,
                    hermesEffort: true,
                }))
                .filter((item) => (effortFilter ? itemMatches(item, effortFilter) : true));
        }

        // ── OpenCode ──────────────────────────────────────────────────────
        function loadOpenCodeModelsForPalette(anyChat, opts) {
            if (!anyChat && !(d.hasActiveOpenCode && d.hasActiveOpenCode())) return;
            const key = sessionKey();
            const forceRefresh = !!(opts && opts.refresh);
            if (
                !forceRefresh &&
                (state.opencodeModelsLoading ||
                    (state.opencodeModels.length && state.opencodeModelsKey === key) ||
                    state.opencodeModelsTried === key)
            ) {
                return;
            }
            const modelsGeneration = beginFetch('opencodeModelsGen');
            state.opencodeModelsLoading = true;
            const params = new URLSearchParams();
            if (key) params.set('session', key);
            if (forceRefresh) params.set('refresh', '1');
            const qs = params.toString();
            const url = qs ? '/api/opencode/models?' + qs : '/api/opencode/models';
            const mutationToken = captureMutation('opencode:model:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.opencodeModels = j && j.success && Array.isArray(j.models) ? j.models : [];
                    if (!state.opencodeModelDirty && isCurrentFetch('opencodeModelsGen', modelsGeneration)) {
                        state.opencodeModel = (j && j.preferredModel) || '';
                    }
                    state.opencodeModelsKey = key;
                    state.opencodeModelsSource = (j && j.source) || '';
                    state.opencodeModelsCount =
                        (j && (j.count != null ? j.count : state.opencodeModels.length)) || 0;
                    renderChips();
                    repaint('opencode');
                    persistSticky();
                    refreshHistoryChips();
                    if (forceRefresh) {
                        const n = state.opencodeModelsCount || 0;
                        const err = j && j.error ? String(j.error) : '';
                        toast(
                            err && !n
                                ? ('OpenCode models refresh failed: ' + err)
                                : ('OpenCode models refreshed (' + n + ')'),
                            err && !n ? 'error' : 'success'
                        );
                    }
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.opencodeModelsLoading = false;
                    state.opencodeModelsTried = key;
                    finishProviderUi();
                });
        }

        function buildOpenCodeModelPaletteItems(filterLower, opts) {
            if (!(d.hasActiveOpenCode && d.hasActiveOpenCode())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadOpenCodeModelsForPalette(true);
            const models = state.opencodeModels || [];
            let modelFilter = f.replace(/^opencode\s+/, '').replace(/^models?\b\s*/, '').trim();
            if (modelFilter === 'opencode') modelFilter = '';
            const preferred = String(state.opencodeModel || '').toLowerCase();
            const refreshItem = {
                category: 'opencode-cmd',
                prefix: '/model refresh',
                label: 'Refresh models',
                hint: 'Fetch latest OpenCode models (models.dev). Staged as /model refresh — send to run.',
                meta: 'cmd',
                keywords: 'opencode model refresh reload sync catalog models.dev openrouter',
            };
            const items = [];
            if (!modelFilter || itemMatches(refreshItem, modelFilter)) {
                items.push(refreshItem);
            }
            if (state.opencodeModelsLoading && !models.length) {
                items.push({
                    category: 'opencode-model',
                    prefix: '/opencode model',
                    label: 'Loading OpenCode models…',
                    hint: 'Fetching opencode models catalog',
                    meta: '',
                    keywords: 'opencode model loading',
                    modelId: '',
                    opencodeModel: true,
                    disabled: true,
                });
                return items;
            }
            const mapped = models
                .map((m) => {
                    const id = String((m && m.id) || '').trim();
                    if (!id) return null;
                    const label = String((m && m.label) || id).trim() || id;
                    const current = id.toLowerCase() === preferred;
                    const fav = !!(m && (m.favorite === true || m.favorite === '1' || m.favorite === 1));
                    return {
                        category: 'opencode-model',
                        prefix: '/opencode model ' + id,
                        label: (fav ? '★ ' : '') + label + (current ? ' (current)' : ''),
                        hint: (m && m.description) || ('Set OpenCode model to ' + id),
                        meta: id,
                        keywords: 'opencode model ' + id + ' ' + label + ' ' + id.replace(/[-_/]+/g, ' '),
                        modelId: id,
                        opencodeModel: true,
                    };
                })
                .filter(Boolean)
                .filter((item) => (modelFilter ? itemMatches(item, modelFilter) : true));
            const MAX = (opts && opts.full) ? Infinity : 40;
            mapped.sort((a, b) => slashApi.slashPaletteItemSearchRank(a, f)
                - slashApi.slashPaletteItemSearchRank(b, f));
            const clipped = mapped.length > MAX ? mapped.slice(0, MAX) : mapped;
            if (mapped.length > MAX) {
                items.push({
                    category: 'opencode-model',
                    prefix: '/opencode model',
                    label: '… ' + (mapped.length - MAX) + ' more — type to narrow (e.g. deepseek)',
                    hint: 'OpenCode catalog has ' + (state.opencodeModelsCount || mapped.length) + ' models',
                    meta: '',
                    keywords: 'opencode model more filter',
                    modelId: '',
                    opencodeModel: true,
                    disabled: true,
                });
            }
            return items.concat(clipped);
        }

        function loadOpenCodeEffortForPalette() {
            if (state.opencodeEffortDirty) return;
            if (!(d.hasActiveOpenCode && d.hasActiveOpenCode())) return;
            const key = sessionKey();
            if (state.opencodeEffortLoading || (state.opencodeEffortKey === key && key)) return;
            const effortGeneration = beginFetch('opencodeEffortGen');
            state.opencodeEffortLoading = true;
            const url = key ? '/api/opencode/effort?session=' + encodeURIComponent(key) : '/api/opencode/effort';
            const mutationToken = captureMutation('opencode:effort:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (state.opencodeEffortDirty || !isCurrentFetch('opencodeEffortGen', effortGeneration)) return;
                    state.opencodeEffort = (j && j.preferredEffort) || '';
                    state.opencodeEffortKey = key;
                    renderChips();
                    repaint('opencode');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.opencodeEffortLoading = false;
                });
        }

        function buildOpenCodeEffortPaletteItems(filterLower) {
            if (!supportsEffort('opencode')) return [];
            if (!(d.hasActiveOpenCode && d.hasActiveOpenCode())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadOpenCodeEffortForPalette();
            let effortFilter = f.replace(/^opencode\s+/, '').replace(/^efforts?\b\s*/, '').trim();
            if (effortFilter === 'effort') effortFilter = '';
            const preferred = String(state.opencodeEffort || '').toLowerCase();
            return OPENCODE_EFFORT_LEVELS
                .map((id) => ({
                    category: 'opencode-effort',
                    prefix: '/opencode effort ' + id,
                    label: 'Effort ' + id + (id.toLowerCase() === preferred ? ' (current)' : ''),
                    hint: 'Set OpenCode reasoning effort (--variant) to ' + id,
                    meta: id,
                    keywords: 'opencode effort reasoning variant ' + id,
                    modelId: id,
                    opencodeEffort: true,
                }))
                .filter((item) => (effortFilter ? itemMatches(item, effortFilter) : true));
        }

        // ── Codex ─────────────────────────────────────────────────────────
        function loadCodexModelsForPalette(anyChat, opts) {
            if (!anyChat && !(d.hasActiveCodex && d.hasActiveCodex())) return;
            const key = sessionKey();
            const forceRefresh = !!(opts && opts.refresh);
            if (
                !forceRefresh &&
                (state.codexModelsLoading ||
                    (state.codexModels.length && state.codexModelsKey === key) ||
                    state.codexModelsTried === key)
            ) {
                return;
            }
            const modelsGeneration = beginFetch('codexModelsGen');
            state.codexModelsLoading = true;
            const params = new URLSearchParams();
            if (key) params.set('session', key);
            if (forceRefresh) params.set('refresh', '1');
            const qs = params.toString();
            const url = qs ? '/api/codex/models?' + qs : '/api/codex/models';
            const mutationToken = captureMutation('codex:model:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.codexModels = j && j.success && Array.isArray(j.models) ? j.models : [];
                    if (!state.codexModelDirty && isCurrentFetch('codexModelsGen', modelsGeneration)) {
                        state.codexModel = (j && j.preferredModel) || '';
                    }
                    state.codexModelsKey = key;
                    state.codexModelsSource = (j && j.source) || '';
                    state.codexModelsCount =
                        (j && (j.count != null ? j.count : state.codexModels.length)) || 0;
                    state.codexCommonEfforts =
                        j && Array.isArray(j.commonEfforts) ? j.commonEfforts : [];
                    renderChips();
                    repaint('codex');
                    persistSticky();
                    refreshHistoryChips();
                    if (forceRefresh) {
                        const n = state.codexModelsCount || 0;
                        const err = j && j.error ? String(j.error) : '';
                        toast(
                            err && !n
                                ? ('Codex models refresh failed: ' + err)
                                : ('Codex models and effort support refreshed (' + n + ')'),
                            err && !n ? 'error' : 'success'
                        );
                    }
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.codexModelsLoading = false;
                    state.codexModelsTried = key;
                    finishProviderUi();
                });
        }

        function buildCodexModelPaletteItems(filterLower, opts) {
            if (!(d.hasActiveCodex && d.hasActiveCodex())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadCodexModelsForPalette(true);
            const models = state.codexModels || [];
            let modelFilter = f.replace(/^codex\s+/, '').replace(/^models?\b\s*/, '').trim();
            if (modelFilter === 'codex') modelFilter = '';
            const preferred = String(state.codexModel || '').toLowerCase();
            const refreshItem = {
                category: 'codex-cmd',
                prefix: '/model refresh',
                label: 'Refresh Codex models',
                hint: 'Refresh Codex models and per-model effort levels from codex debug models',
                meta: 'refresh',
                keywords: 'codex model refresh reload sync catalog debug models',
            };
            const items = [];
            if (!modelFilter || itemMatches(refreshItem, modelFilter)) {
                items.push(refreshItem);
            }
            if (state.codexModelsLoading && !models.length) {
                items.push({
                    category: 'codex-model',
                    prefix: '/codex model',
                    label: 'Loading Codex models…',
                    hint: 'Fetching codex debug models catalog',
                    meta: '',
                    keywords: 'codex model loading',
                    modelId: '',
                    codexModel: true,
                    disabled: true,
                });
                return items;
            }
            const mapped = models
                .map((m) => {
                    const id = String((m && m.id) || '').trim();
                    if (!id) return null;
                    const label = String((m && m.label) || id).trim() || id;
                    const current = id.toLowerCase() === preferred;
                    const fav = !!(m && (m.favorite === true || m.favorite === '1' || m.favorite === 1));
                    const modelEfforts = Array.isArray(m && m.efforts) ? m.efforts : [];
                    const effortHint = modelEfforts.length
                        ? 'Supported efforts: ' + modelEfforts.join(', ')
                        : 'No verified effort levels in the Codex CLI catalog';
                    return {
                        category: 'codex-model',
                        prefix: '/codex model ' + id,
                        label: (fav ? '★ ' : '') + label + (current ? ' (current)' : ''),
                        hint: [
                            (m && m.description) || ('Set Codex model to ' + id),
                            effortHint,
                        ].filter(Boolean).join(' · '),
                        meta: id,
                        keywords: 'codex model ' + id + ' ' + label + ' ' + id.replace(/[-_/]+/g, ' '),
                        modelId: id,
                        codexModel: true,
                    };
                })
                .filter(Boolean)
                .filter((item) => (modelFilter ? itemMatches(item, modelFilter) : true));
            const MAX = (opts && opts.full) ? Infinity : 40;
            mapped.sort((a, b) => slashApi.slashPaletteItemSearchRank(a, f)
                - slashApi.slashPaletteItemSearchRank(b, f));
            const clipped = mapped.length > MAX ? mapped.slice(0, MAX) : mapped;
            if (mapped.length > MAX) {
                items.push({
                    category: 'codex-model',
                    prefix: '/codex model',
                    label: '… ' + (mapped.length - MAX) + ' more — type to narrow (e.g. gpt-5)',
                    hint: 'Codex catalog has ' + (state.codexModelsCount || mapped.length) + ' models',
                    meta: '',
                    keywords: 'codex model more filter',
                    modelId: '',
                    codexModel: true,
                    disabled: true,
                });
            }
            return items.concat(clipped);
        }

        function loadCodexEffortForPalette() {
            if (state.codexEffortDirty) return;
            if (!(d.hasActiveCodex && d.hasActiveCodex())) return;
            const key = sessionKey();
            if (state.codexEffortLoading || (state.codexEffortKey === key && key)) return;
            const effortGeneration = beginFetch('codexEffortGen');
            state.codexEffortLoading = true;
            const url = key ? '/api/codex/effort?session=' + encodeURIComponent(key) : '/api/codex/effort';
            const mutationToken = captureMutation('codex:effort:read');
            doFetch(url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (state.codexEffortDirty || !isCurrentFetch('codexEffortGen', effortGeneration)) return;
                    state.codexEffort = (j && j.preferredEffort) || '';
                    state.codexEffortKey = key;
                    renderChips();
                    repaint('codex');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.codexEffortLoading = false;
                });
        }

        function buildCodexEffortPaletteItems(filterLower) {
            if (!supportsEffort('codex')) return [];
            if (!(d.hasActiveCodex && d.hasActiveCodex())) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadCodexEffortForPalette();
            let effortFilter = f.replace(/^codex\s+/, '').replace(/^efforts?\b\s*/, '').trim();
            if (effortFilter === 'effort') effortFilter = '';
            const preferred = String(state.codexEffort || '').toLowerCase();
            const selectedModel = String(state.codexModel || '').toLowerCase();
            const selectedRow = selectedModel
                ? (state.codexModels || []).find((m) =>
                    String((m && m.id) || '').toLowerCase() === selectedModel)
                : null;
            const levels = selectedRow && Array.isArray(selectedRow.efforts)
                ? selectedRow.efforts
                : selectedModel
                    ? []
                    : (state.codexCommonEfforts || []);
            if (!levels.length) {
                return [{
                    category: 'codex-effort',
                    prefix: '/codex effort',
                    label: state.codexModelsLoading
                        ? 'Loading Codex effort support…'
                        : 'No verified effort levels for ' + (selectedModel || 'the CLI default'),
                    hint: state.codexModelsLoading
                        ? 'Reading per-model levels from codex debug models'
                        : 'Refresh the Codex model catalog to read current per-model effort support',
                    meta: '',
                    keywords: 'codex effort loading supported levels refresh',
                    modelId: '',
                    codexEffort: true,
                    disabled: true,
                }];
            }
            const items = levels
                .map((id) => ({
                    category: 'codex-effort',
                    prefix: '/codex effort ' + id,
                    label: 'Effort ' + id + (id.toLowerCase() === preferred ? ' (current)' : ''),
                    hint: 'Set Codex reasoning effort (model_reasoning_effort) to ' + id
                        + (selectedModel ? ' for ' + selectedModel : ' (supported across the CLI catalog)'),
                    meta: id,
                    keywords: 'codex effort reasoning ' + id,
                    modelId: id,
                    codexEffort: true,
                }))
                .filter((item) => (effortFilter ? itemMatches(item, effortFilter) : true));
            if (!items.length && effortFilter) {
                return [{
                    category: 'codex-effort',
                    prefix: '/codex effort',
                    label: '`' + effortFilter + '` is not supported'
                        + (selectedModel ? ' by ' + selectedModel : ' across all models'),
                    hint: 'Supported: ' + levels.join(', '),
                    meta: '',
                    keywords: 'codex effort unsupported ' + effortFilter,
                    modelId: '',
                    codexEffort: true,
                    disabled: true,
                }];
            }
            return items;
        }

        // ── Claude ────────────────────────────────────────────────────────
        function loadClaudeModelsForPalette(anyChat, opts) {
            const key = sessionKey();
            const forceRefresh = !!(opts && opts.refresh);
            const gate = root.CuttleChatAgentModel.claudeModelsFetchForPalette({
                anyChat,
                hasClaudeChip: d.hasActiveHarness && d.hasActiveHarness('claude'),
                loading: state.claudeModelsLoading,
                modelsLength: (state.claudeModels || []).length,
                modelsKey: state.claudeModelsKey,
                triedKey: state.claudeModelsTried,
                sessionKey: key,
                forceRefresh,
            });
            if (!gate.fetch) return;
            const modelsGeneration = beginFetch('claudeModelsGen');
            state.claudeModelsLoading = true;
            const mutationToken = captureMutation('claude:model:read');
            doFetch(gate.url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    const applied = root.CuttleChatAgentModel.applyClaudeModelsResponse(j, {
                        modelDirty: state.claudeModelDirty || !isCurrentFetch('claudeModelsGen', modelsGeneration),
                    });
                    state.claudeModels = applied.models;
                    if (applied.model !== null) state.claudeModel = applied.model;
                    state.claudeModelsKey = key;
                    state.claudeModelsSource = applied.source;
                    state.claudeModelsCount = applied.count;
                    state.claudeCommonEfforts = applied.commonEfforts;
                    renderChips();
                    repaint('claude');
                    persistSticky();
                    refreshHistoryChips();
                    if (forceRefresh) {
                        const n = state.claudeModelsCount || 0;
                        const err = applied.error;
                        toast(
                            err && !n
                                ? ('Claude Code models refresh failed: ' + err)
                                : ('Claude Code models and effort support refreshed (' + n + ')'),
                            err && !n ? 'error' : 'success'
                        );
                    }
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.claudeModelsLoading = false;
                    state.claudeModelsTried = key;
                    finishProviderUi();
                });
        }

        function buildClaudeModelPaletteItems(filterLower, opts) {
            if (!(d.hasActiveHarness && d.hasActiveHarness('claude'))) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadClaudeModelsForPalette(true);
            const models = state.claudeModels || [];
            const modelFilter = root.CuttleChatAgentModel.claudeModelFilterForPalette(f);
            const preferred = String(state.claudeModel || '').toLowerCase();
            const refreshItem = {
                category: 'claude-cmd',
                prefix: '/model refresh',
                label: 'Refresh Claude Code models',
                hint: 'Refresh Claude Code models and per-model effort levels from the installed CLI',
                meta: 'refresh',
                keywords: 'claude model refresh reload sync catalog',
            };
            const items = [];
            if (!modelFilter || itemMatches(refreshItem, modelFilter)) {
                items.push(refreshItem);
            }
            if (state.claudeModelsLoading && !models.length) {
                items.push({
                    category: 'claude-model',
                    prefix: '/claude model',
                    label: 'Loading Claude Code models…',
                    hint: 'Reading the Claude Code CLI model catalog',
                    meta: '',
                    keywords: 'claude model loading',
                    modelId: '',
                    claudeModel: true,
                    disabled: true,
                });
                return items;
            }
            const mapped = models
                .map((m) => root.CuttleChatAgentModel.buildClaudeModelRow(m, preferred))
                .filter(Boolean)
                .filter((item) => (modelFilter ? itemMatches(item, modelFilter) : true));
            const MAX = (opts && opts.full) ? Infinity : 40;
            mapped.sort((a, b) => slashApi.slashPaletteItemSearchRank(a, f)
                - slashApi.slashPaletteItemSearchRank(b, f));
            const clipped = mapped.length > MAX ? mapped.slice(0, MAX) : mapped;
            if (mapped.length > MAX) {
                items.push({
                    category: 'claude-model',
                    prefix: '/claude model',
                    label: '… ' + (mapped.length - MAX) + ' more — type to narrow (e.g. opus)',
                    hint: 'Claude Code catalog has ' + (state.claudeModelsCount || mapped.length) + ' models',
                    meta: '',
                    keywords: 'claude model more filter',
                    modelId: '',
                    claudeModel: true,
                    disabled: true,
                });
            }
            return items.concat(clipped);
        }

        function loadClaudeEffortForPalette() {
            if (state.claudeEffortDirty) return;
            const key = sessionKey();
            const gate = root.CuttleChatAgentModel.claudeEffortFetchForPalette({
                hasClaudeChip: d.hasActiveHarness && d.hasActiveHarness('claude'),
                loading: state.claudeEffortLoading,
                effortKey: state.claudeEffortKey,
                sessionKey: key,
            });
            if (!gate.fetch) return;
            const effortGeneration = beginFetch('claudeEffortGen');
            state.claudeEffortLoading = true;
            const mutationToken = captureMutation('claude:effort:read');
            doFetch(gate.url, { cache: 'no-store' })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (state.claudeEffortDirty || !isCurrentFetch('claudeEffortGen', effortGeneration)) return;
                    state.claudeEffort = root.CuttleChatAgentModel.applyClaudeEffortResponse(j);
                    state.claudeEffortKey = key;
                    renderChips();
                    repaint('claude');
                    persistSticky();
                    refreshHistoryChips();
                })
                .catch(() => {})
                .finally(() => {
                    if (!isMutationCurrent(mutationToken)) return;
                    state.claudeEffortLoading = false;
                });
        }

        function buildClaudeEffortPaletteItems(filterLower) {
            if (!supportsEffort('claude')) return [];
            if (!(d.hasActiveHarness && d.hasActiveHarness('claude'))) return [];
            const f = (filterLower || '').toLowerCase().trim();
            if (!f) return [];
            loadClaudeEffortForPalette();
            const effortFilter = root.CuttleChatAgentModel.claudeEffortFilterForPalette(f);
            const preferred = String(state.claudeEffort || '').toLowerCase();
            const selectedModel = String(state.claudeModel || '').toLowerCase();
            const levels = root.CuttleChatAgentModel.claudeEffortLevelsForModel({
                selectedModel,
                models: state.claudeModels,
                commonEfforts: state.claudeCommonEfforts,
            });
            if (!levels.length) {
                return [{
                    category: 'claude-effort',
                    prefix: '/claude effort',
                    label: state.claudeModelsLoading
                        ? 'Loading Claude Code effort support…'
                        : 'No verified effort levels for ' + (selectedModel || 'the CLI default'),
                    hint: state.claudeModelsLoading
                        ? 'Reading per-model levels from the Claude Code CLI'
                        : 'This model takes no --effort level; refresh the Claude Code catalog if that looks wrong',
                    meta: '',
                    keywords: 'claude effort loading supported levels refresh',
                    modelId: '',
                    claudeEffort: true,
                    disabled: true,
                }];
            }
            const items = levels
                .map((id) => root.CuttleChatAgentModel.buildClaudeEffortRow(id, {
                    preferredLower: preferred,
                    selectedModel,
                }))
                .filter((item) => (effortFilter ? itemMatches(item, effortFilter) : true));
            if (!items.length && effortFilter) {
                return [{
                    category: 'claude-effort',
                    prefix: '/claude effort',
                    label: '`' + effortFilter + '` is not supported'
                        + (selectedModel ? ' by ' + selectedModel : ' by the CLI default model'),
                    hint: 'Supported: ' + levels.join(', '),
                    meta: '',
                    keywords: 'claude effort unsupported ' + effortFilter,
                    modelId: '',
                    claudeEffort: true,
                    disabled: true,
                }];
            }
            return items;
        }

        // ── Muse selection persistence ────────────────────────────────────
        /** Persist the chat's Muse model pick (palette click — no message sent). */
        function persistMuseModelSelection(modelId) {
            const id = String(modelId || '').trim();
            if (!id) return;
            state.museModelsLoading = false;
            if (id.toLowerCase() === 'refresh' || id.toLowerCase() === '__refresh__') {
                state.museModelsKey = '';
                state.museModels = [];
                loadMuseModelsForPalette(true, { refresh: true });
                return;
            }
            state.museModel = id;
            beginFetch('museModelsGen');
            state.museModels = (state.museModels || []).map((m) => ({
                ...m,
                current: String(m && m.id) === id,
            }));
            const sid = sessionKey();
            if (!sid) {
                // New chat with no id yet — mark dirty so the pick (not the
                // default) is persisted once the session exists.
                state.museModelDirty = true;
                state.museModelsKey = '';
                return;
            }
            state.museModelDirty = true;
            const mutationToken = captureMutation('muse:model');
            doSessionMutationFetch('/api/muse/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, model: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.museModel = id;
                        state.museModelDirty = false;
                        state.museModelsKey = sid;
                        repaint('muse');
                        persistSticky();
                        refreshHistoryChips();
                    }
                })
                .catch(() => {});
        }

        function seedMuseSupplementFromSessionData(data, sessionId) {
            if (!data || typeof data !== 'object') return;
            const key = sessionId != null ? String(sessionId) : '';
            const serverModel = root.CuttleChatAgentModel.sessionPin(data, 'muse', 'model');
            if (serverModel && !state.museModelDirty) {
                state.museModel = serverModel;
                if (key) state.museModelsKey = key;
            }
            const serverEffort = root.CuttleChatAgentModel.sessionPin(data, 'muse', 'effort').toLowerCase();
            if (serverEffort && !state.museEffortDirty) {
                state.museEffort = serverEffort;
                if (key) state.museEffortKey = key;
            } else if (!state.museEffortDirty && key && data && ((data.agent_pins && data.agent_pins.muse) || ('muse_effort' in data))) {
                // Explicitly unpinned — don't inherit another chat's effort.
                state.museEffort = '';
                state.museEffortKey = key;
            }
        }

        /** Persist the chat's Muse reasoning-effort pick (palette click — no message sent). */
        function persistMuseEffortSelection(effort) {
            const id = String(effort || '').trim().toLowerCase();
            if (!id) return;
            state.museEffortLoading = false;
            beginFetch('museEffortGen');
            state.museEffort = id;
            const sid = sessionKey();
            if (!sid) {
                state.museEffortDirty = true;
                state.museEffortKey = '';
                return;
            }
            state.museEffortKey = sid;
            state.museEffortDirty = true;
            const mutationToken = captureMutation('muse:effort');
            doSessionMutationFetch('/api/muse/effort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, effort: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.museEffortKey = sid;
                        state.museEffortDirty = false;
                    }
                })
                .catch(() => {});
        }

        // ── Hermes selection persistence ──────────────────────────────────
        /** Persist the chat's Hermes model pick (palette click — no message sent). */
        function persistHermesModelSelection(modelId) {
            const id = String(modelId || '').trim();
            if (!id) return;
            state.hermesModelsLoading = false;
            beginFetch('hermesModelsGen');
            state.hermesModel = id;
            state.hermesModels = (state.hermesModels || []).map((m) => ({
                ...m,
                current: String(m && m.id) === id,
            }));
            const sid = sessionKey();
            if (!sid) {
                state.hermesModelDirty = true;
                state.hermesModelsKey = '';
                return;
            }
            state.hermesModelDirty = true;
            const mutationToken = captureMutation('hermes:model');
            doSessionMutationFetch('/api/hermes/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, model: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.hermesModel = id;
                        state.hermesModelDirty = false;
                        state.hermesModelsKey = sid;
                        repaint('hermes');
                        persistSticky();
                        refreshHistoryChips();
                    }
                })
                .catch(() => {});
        }

        function seedHermesSupplementFromSessionData(data, sessionId) {
            if (!data || typeof data !== 'object') return;
            const key = sessionId != null ? String(sessionId) : '';
            const serverModel = root.CuttleChatAgentModel.sessionPin(data, 'hermes', 'model');
            if (serverModel && !state.hermesModelDirty) {
                state.hermesModel = serverModel;
                if (key) state.hermesModelsKey = key;
            }
            const serverEffort = root.CuttleChatAgentModel.sessionPin(data, 'hermes', 'effort').toLowerCase();
            if (serverEffort && !state.hermesEffortDirty) {
                state.hermesEffort = serverEffort;
                if (key) state.hermesEffortKey = key;
            } else if (!state.hermesEffortDirty && key && data && ((data.agent_pins && data.agent_pins.hermes) || ('hermes_effort' in data))) {
                state.hermesEffort = '';
                state.hermesEffortKey = key;
            }
        }

        function persistHermesEffortSelection(effort) {
            const id = String(effort || '').trim().toLowerCase();
            if (!id) return;
            state.hermesEffortLoading = false;
            beginFetch('hermesEffortGen');
            state.hermesEffort = id;
            const sid = sessionKey();
            if (!sid) {
                state.hermesEffortDirty = true;
                state.hermesEffortKey = '';
                return;
            }
            // Optimistic key so a concurrent GET cannot wipe the pick before POST lands.
            state.hermesEffortKey = sid;
            state.hermesEffortDirty = true;
            const mutationToken = captureMutation('hermes:effort');
            doSessionMutationFetch('/api/hermes/effort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, effort: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.hermesEffortKey = sid;
                        state.hermesEffortDirty = false;
                    }
                })
                .catch(() => {});
        }

        // ── OpenCode selection persistence ────────────────────────────────
        function persistOpenCodeModelSelection(modelId) {
            const id = String(modelId || '').trim();
            if (!id) return;
            state.opencodeModelsLoading = false;
            beginFetch('opencodeModelsGen');
            if (id.toLowerCase() === 'refresh' || id.toLowerCase() === '__refresh__') {
                state.opencodeModelsKey = '';
                state.opencodeModels = [];
                loadOpenCodeModelsForPalette(true, { refresh: true });
                return;
            }
            state.opencodeModel = id;
            state.opencodeModels = (state.opencodeModels || []).map((m) => ({
                ...m,
                current: String(m && m.id) === id,
            }));
            const sid = sessionKey();
            if (!sid) {
                state.opencodeModelDirty = true;
                state.opencodeModelsKey = '';
                return;
            }
            state.opencodeModelDirty = true;
            const mutationToken = captureMutation('opencode:model');
            doSessionMutationFetch('/api/opencode/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, model: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.opencodeModel = id;
                        state.opencodeModelDirty = false;
                        state.opencodeModelsKey = sid;
                        repaint('opencode');
                        persistSticky();
                        refreshHistoryChips();
                    }
                })
                .catch(() => {});
        }

        function seedOpenCodeSupplementFromSessionData(data, sessionId) {
            if (!data || typeof data !== 'object') return;
            const key = sessionId != null ? String(sessionId) : '';
            const serverModel = root.CuttleChatAgentModel.sessionPin(data, 'opencode', 'model');
            if (serverModel && !state.opencodeModelDirty) {
                state.opencodeModel = serverModel;
                if (key) state.opencodeModelsKey = key;
            }
            const serverEffort = root.CuttleChatAgentModel.sessionPin(data, 'opencode', 'effort').toLowerCase();
            if (serverEffort && !state.opencodeEffortDirty) {
                state.opencodeEffort = serverEffort;
                if (key) state.opencodeEffortKey = key;
            } else if (!state.opencodeEffortDirty && key && data && ((data.agent_pins && data.agent_pins.opencode) || ('opencode_effort' in data))) {
                state.opencodeEffort = '';
                state.opencodeEffortKey = key;
            }
        }

        function persistOpenCodeEffortSelection(effort) {
            const id = String(effort || '').trim().toLowerCase();
            if (!id) return;
            state.opencodeEffortLoading = false;
            beginFetch('opencodeEffortGen');
            state.opencodeEffort = id;
            const sid = sessionKey();
            if (!sid) {
                state.opencodeEffortDirty = true;
                state.opencodeEffortKey = '';
                return;
            }
            // Optimistic key so a concurrent GET cannot wipe the pick before POST lands.
            state.opencodeEffortKey = sid;
            state.opencodeEffortDirty = true;
            const mutationToken = captureMutation('opencode:effort');
            doSessionMutationFetch('/api/opencode/effort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, effort: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.opencodeEffortKey = sid;
                        state.opencodeEffortDirty = false;
                    }
                })
                .catch(() => {});
        }

        // ── Codex selection persistence ───────────────────────────────────
        function persistCodexModelSelection(modelId) {
            const id = String(modelId || '').trim();
            if (!id) return;
            state.codexModelsLoading = false;
            beginFetch('codexModelsGen');
            if (id.toLowerCase() === 'refresh' || id.toLowerCase() === '__refresh__') {
                state.codexModelsKey = '';
                state.codexModels = [];
                loadCodexModelsForPalette(true, { refresh: true });
                return;
            }
            state.codexModel = id;
            state.codexModels = (state.codexModels || []).map((m) => ({
                ...m,
                current: String(m && m.id) === id,
            }));
            const sid = sessionKey();
            if (!sid) {
                state.codexModelDirty = true;
                state.codexModelsKey = '';
                return;
            }
            state.codexModelDirty = true;
            const mutationToken = captureMutation('codex:model');
            doSessionMutationFetch('/api/codex/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, model: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.codexModel = id;
                        state.codexModelDirty = false;
                        state.codexModelsKey = sid;
                        repaint('codex');
                        persistSticky();
                        refreshHistoryChips();
                    }
                })
                .catch(() => {});
        }

        function seedCodexSupplementFromSessionData(data, sessionId) {
            if (!data || typeof data !== 'object') return;
            const key = sessionId != null ? String(sessionId) : '';
            const serverModel = root.CuttleChatAgentModel.sessionPin(data, 'codex', 'model');
            if (serverModel && !state.codexModelDirty) {
                state.codexModel = serverModel;
                if (key) state.codexModelsKey = key;
            }
            const serverEffort = root.CuttleChatAgentModel.sessionPin(data, 'codex', 'effort').toLowerCase();
            if (serverEffort && !state.codexEffortDirty) {
                state.codexEffort = serverEffort;
                if (key) state.codexEffortKey = key;
            } else if (!state.codexEffortDirty && key && data && ((data.agent_pins && data.agent_pins.codex) || ('codex_effort' in data))) {
                state.codexEffort = '';
                state.codexEffortKey = key;
            }
        }

        /**
         * Resolve Codex's effective effort before snapshotting a first-turn user
         * badge. The palette fetch is intentionally async; a fast Send must not
         * persist a bare Codex chip while the starred default is still loading.
         *
         * Never clobber a user pick that has not been persisted yet (new chat /
         * no session id). That used to overwrite `xhigh` with the starred `low`
         * right before the badge snapshot, then flush the corrupted value on adopt.
         */
        async function ensureCodexEffortForSend(message) {
            const gate = root.CuttleChatAgentModel.codexEffortFetchForSend({
                messageText: message,
                hasCodexChip: d.hasActiveCodex && d.hasActiveCodex(),
                codexEffortDirty: state.codexEffortDirty,
                sessionKey: sessionKey(),
                effortKey: state.codexEffortKey,
            });
            if (!gate.fetch) return;
            const key = sessionKey();
            try {
                const response = await doFetch(gate.url, { cache: 'no-store' });
                const result = await response.json();
                if (!result || !result.success) return;
                // A palette click may have set dirty while this fetch was in flight.
                if (state.codexEffortDirty) return;
                state.codexEffort = String(result.preferredEffort || '');
                state.codexEffortKey = key;
            } catch (_) {}
        }

        function persistCodexEffortSelection(effort) {
            const id = String(effort || '').trim().toLowerCase();
            if (!id) return;
            state.codexEffortLoading = false;
            beginFetch('codexEffortGen');
            state.codexEffort = id;
            const sid = sessionKey();
            if (!sid) {
                state.codexEffortDirty = true;
                state.codexEffortKey = '';
                return;
            }
            state.codexEffortKey = sid;
            state.codexEffortDirty = true;
            const mutationToken = captureMutation('codex:effort');
            doSessionMutationFetch('/api/codex/effort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session: sid,
                    effort: id,
                    model: state.codexModel || '',
                }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.codexEffortKey = sid;
                        state.codexEffortDirty = false;
                    }
                })
                .catch(() => {});
        }

        // ── Claude selection persistence ──────────────────────────────────
        function persistClaudeModelSelection(modelId) {
            const id = String(modelId || '').trim();
            if (!id) return;
            state.claudeModelsLoading = false;
            beginFetch('claudeModelsGen');
            if (root.CuttleChatAgentModel.isClaudeModelRefreshPick(id)) {
                state.claudeModelsKey = '';
                state.claudeModels = [];
                loadClaudeModelsForPalette(true, { refresh: true });
                return;
            }
            state.claudeModel = id;
            state.claudeModels =
                root.CuttleChatAgentModel.markClaudeCurrentModel(
                    state.claudeModels, id);
            const sid = sessionKey();
            if (!sid) {
                state.claudeModelDirty = true;
                state.claudeModelsKey = '';
                return;
            }
            state.claudeModelDirty = true;
            const mutationToken = captureMutation('claude:model');
            doSessionMutationFetch('/api/claude/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sid, model: id }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.claudeModel = id;
                        state.claudeModelDirty = false;
                        state.claudeModelsKey = sid;
                        repaint('claude');
                        persistSticky();
                        refreshHistoryChips();
                    }
                })
                .catch(() => {});
        }

        function seedClaudeSupplementFromSessionData(data, sessionId) {
            if (!data || typeof data !== 'object') return;
            const patch = root.CuttleChatAgentModel.claudeSeedPatchFromSessionData(data, {
                modelDirty: state.claudeModelDirty,
                effortDirty: state.claudeEffortDirty,
                sessionKey: sessionId != null ? String(sessionId) : '',
            });
            if (patch.model !== undefined) state.claudeModel = patch.model;
            if (patch.modelsKey !== undefined) state.claudeModelsKey = patch.modelsKey;
            if (patch.effort !== undefined) state.claudeEffort = patch.effort;
            if (patch.effortKey !== undefined) state.claudeEffortKey = patch.effortKey;
        }

        function persistClaudeEffortSelection(effort) {
            const id = String(effort || '').trim().toLowerCase();
            if (!id) return;
            state.claudeEffortLoading = false;
            beginFetch('claudeEffortGen');
            state.claudeEffort = id;
            const sid = sessionKey();
            if (!sid) {
                state.claudeEffortDirty = true;
                state.claudeEffortKey = '';
                return;
            }
            state.claudeEffortKey = sid;
            state.claudeEffortDirty = true;
            const mutationToken = captureMutation('claude:effort');
            doSessionMutationFetch('/api/claude/effort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session: sid,
                    effort: id,
                    model: state.claudeModel || '',
                }),
            })
                .then((r) => r.json())
                .then((j) => {
                    if (!isMutationCurrent(mutationToken)) return;
                    if (j && j.success) {
                        state.claudeEffortKey = sid;
                        state.claudeEffortDirty = false;
                    }
                })
                .catch(() => {});
        }

        /**
         * Neutralize cached agent preferences when the session identity changes.
         * prev/next are '' for the unsent composer: '' → id keeps dirty user
         * picks (same unsent chat gaining an id); anything else fully resets so a
         * new chat never inherits the previous chat's model. Always invalidates
         * caches and in-flight fetches via the generation counters.
         */
        function resetAgentSupplementForIdentity(prev, next) {
            const S = state;
            S.cursorModelsKey = '';
            S.cursorModelsLoaded = false;
            S.museModels = [];
            S.museModelsKey = '';
            S.museModelsLoading = false;
            S.museModelsTried = null;
            S.hermesModels = [];
            S.hermesModelsKey = '';
            S.hermesModelsLoading = false;
            S.hermesModelsTried = null;
            S.opencodeModels = [];
            S.opencodeModelsKey = '';
            S.opencodeModelsLoading = false;
            S.opencodeModelsTried = null;
            S.codexModels = [];
            S.codexModelsKey = '';
            S.codexModelsLoading = false;
            S.codexModelsTried = null;
            S.claudeModels = [];
            S.claudeModelsKey = '';
            S.claudeModelsLoading = false;
            S.claudeModelsTried = null;
            S.museEffortKey = '';
            S.hermesEffortKey = '';
            S.opencodeEffortKey = '';
            S.codexEffortKey = '';
            S.claudeEffortKey = '';
            S.cursorModelsGen = (S.cursorModelsGen || 0) + 1;
            S.museModelsGen = (S.museModelsGen || 0) + 1;
            S.hermesModelsGen = (S.hermesModelsGen || 0) + 1;
            S.opencodeModelsGen = (S.opencodeModelsGen || 0) + 1;
            S.codexModelsGen = (S.codexModelsGen || 0) + 1;
            S.claudeModelsGen = (S.claudeModelsGen || 0) + 1;
            if (prev && next) {
                S.preferredModel = 'auto';
                S.lastReportedModel = null;
                S.museModel = '';
                S.museEffort = '';
                S.museModelDirty = false;
                S.museEffortDirty = false;
                S.museEffortLoading = false;
                S.hermesModel = '';
                S.hermesEffort = '';
                S.hermesModelDirty = false;
                S.hermesEffortDirty = false;
                S.hermesEffortLoading = false;
                S.opencodeModel = '';
                S.opencodeEffort = '';
                S.opencodeModelDirty = false;
                S.opencodeEffortDirty = false;
                S.opencodeEffortLoading = false;
                S.codexModel = '';
                S.codexEffort = '';
                S.codexModelDirty = false;
                S.codexEffortDirty = false;
                S.codexEffortLoading = false;
                S.claudeModel = '';
                S.claudeEffort = '';
                S.claudeModelDirty = false;
                S.claudeEffortDirty = false;
                S.claudeEffortLoading = false;
            } else if (!next) {
                S.preferredModel = 'auto';
                S.lastReportedModel = null;
                S.museModel = '';
                S.museEffort = '';
                S.museModelDirty = false;
                S.museEffortDirty = false;
                S.hermesModel = '';
                S.hermesEffort = '';
                S.hermesModelDirty = false;
                S.hermesEffortDirty = false;
                S.opencodeModel = '';
                S.opencodeEffort = '';
                S.opencodeModelDirty = false;
                S.opencodeEffortDirty = false;
                S.codexModel = '';
                S.codexEffort = '';
                S.codexModelDirty = false;
                S.codexEffortDirty = false;
                S.claudeModel = '';
                S.claudeEffort = '';
                S.claudeModelDirty = false;
                S.claudeEffortDirty = false;
            }
        }

        /**
         * Persist a model/effort picked while the chat had no id yet, once the
         * server assigns one. Only dirty picks persist here — persisting the
         * in-memory default would clobber the stored pin (e.g. Contributor
         * reset to plain Spark 1.3).
         */
        function flushDirtyAgentSelectionsOnAdopt() {
            if (state.museModelDirty) {
                state.museModelDirty = false;
                persistMuseModelSelection(state.museModel);
            }
            if (state.museEffortDirty) {
                state.museEffortDirty = false;
                persistMuseEffortSelection(state.museEffort);
            }
            if (state.hermesModelDirty) {
                state.hermesModelDirty = false;
                persistHermesModelSelection(state.hermesModel);
            }
            if (state.hermesEffortDirty) {
                state.hermesEffortDirty = false;
                persistHermesEffortSelection(state.hermesEffort);
            }
            if (state.opencodeModelDirty) {
                state.opencodeModelDirty = false;
                persistOpenCodeModelSelection(state.opencodeModel);
            }
            if (state.opencodeEffortDirty) {
                state.opencodeEffortDirty = false;
                persistOpenCodeEffortSelection(state.opencodeEffort);
            }
            if (state.codexModelDirty) {
                state.codexModelDirty = false;
                persistCodexModelSelection(state.codexModel);
            }
            if (state.codexEffortDirty) {
                state.codexEffortDirty = false;
                persistCodexEffortSelection(state.codexEffort);
            }
            if (state.claudeModelDirty) {
                state.claudeModelDirty = false;
                persistClaudeModelSelection(state.claudeModel);
            }
            if (state.claudeEffortDirty) {
                state.claudeEffortDirty = false;
                persistClaudeEffortSelection(state.claudeEffort);
            }
        }

        return {
            state: state,
            loadCursorAgentModelsForPalette: loadCursorAgentModelsForPalette,
            loadMuseModelsForPalette: loadMuseModelsForPalette,
            loadMuseEffortForPalette: loadMuseEffortForPalette,
            loadHermesModelsForPalette: loadHermesModelsForPalette,
            loadHermesEffortForPalette: loadHermesEffortForPalette,
            loadOpenCodeModelsForPalette: loadOpenCodeModelsForPalette,
            loadOpenCodeEffortForPalette: loadOpenCodeEffortForPalette,
            loadCodexModelsForPalette: loadCodexModelsForPalette,
            loadCodexEffortForPalette: loadCodexEffortForPalette,
            loadClaudeModelsForPalette: loadClaudeModelsForPalette,
            loadClaudeEffortForPalette: loadClaudeEffortForPalette,
            buildCursorModelPaletteItems: buildCursorModelPaletteItems,
            buildMuseModelPaletteItems: buildMuseModelPaletteItems,
            buildMuseEffortPaletteItems: buildMuseEffortPaletteItems,
            buildHermesModelPaletteItems: buildHermesModelPaletteItems,
            buildHermesEffortPaletteItems: buildHermesEffortPaletteItems,
            buildOpenCodeModelPaletteItems: buildOpenCodeModelPaletteItems,
            buildOpenCodeEffortPaletteItems: buildOpenCodeEffortPaletteItems,
            buildCodexModelPaletteItems: buildCodexModelPaletteItems,
            buildCodexEffortPaletteItems: buildCodexEffortPaletteItems,
            buildClaudeModelPaletteItems: buildClaudeModelPaletteItems,
            buildClaudeEffortPaletteItems: buildClaudeEffortPaletteItems,
            persistMuseModelSelection: persistMuseModelSelection,
            persistMuseEffortSelection: persistMuseEffortSelection,
            persistHermesModelSelection: persistHermesModelSelection,
            persistHermesEffortSelection: persistHermesEffortSelection,
            persistOpenCodeModelSelection: persistOpenCodeModelSelection,
            persistOpenCodeEffortSelection: persistOpenCodeEffortSelection,
            persistCodexModelSelection: persistCodexModelSelection,
            persistCodexEffortSelection: persistCodexEffortSelection,
            persistClaudeModelSelection: persistClaudeModelSelection,
            persistClaudeEffortSelection: persistClaudeEffortSelection,
            seedMuseSupplementFromSessionData: seedMuseSupplementFromSessionData,
            seedHermesSupplementFromSessionData: seedHermesSupplementFromSessionData,
            seedOpenCodeSupplementFromSessionData: seedOpenCodeSupplementFromSessionData,
            seedCodexSupplementFromSessionData: seedCodexSupplementFromSessionData,
            seedClaudeSupplementFromSessionData: seedClaudeSupplementFromSessionData,
            ensureCodexEffortForSend: ensureCodexEffortForSend,
            resetAgentSupplementForIdentity: resetAgentSupplementForIdentity,
            flushDirtyAgentSelectionsOnAdopt: flushDirtyAgentSelectionsOnAdopt,
        };
    }

    const MUSE_EFFORT_LEVELS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
    const HERMES_EFFORT_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'];
    const OPENCODE_EFFORT_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

    const api = {
        create: create,
        createProviderState: createProviderState,
        MUSE_EFFORT_LEVELS: MUSE_EFFORT_LEVELS,
        HERMES_EFFORT_LEVELS: HERMES_EFFORT_LEVELS,
        OPENCODE_EFFORT_LEVELS: OPENCODE_EFFORT_LEVELS,
    };

    root.CuttleChatAgentControls = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
