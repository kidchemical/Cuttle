/* ================================================================
   Cuttle Chat — agent/model controls domain (chat_agent_model.js).
   Owner: agent/model/effort selection decisions as pure functions over
   explicit inputs: no document, no window, no localStorage, no fetch
   here. Loaded before chat_page.js; the page owns supplement state,
   palette/badge DOM, settings/fetch IO, session-prefs IO, sticky
   restore + override orchestration, and send-path ordering, and calls
   into `CuttleChatAgentModel.*`.

   Cross-domain rule: sticky-chip classification stays in
   `CuttleChatSlash` (the page resolves chips, this module never
   duplicates that logic); attachment payloads stay in
   `CuttleChatAttachments`; send dispatch stays in
   `CuttleChatComposer`.

   Mirrors the page contract: agent_pins supplies session preferences,
   the active harness always attaches its current composer
   pins (dirty-only for the rest), the Codex pre-send fetch never
   clobbers a dirty pick, and badge effort prefers run data over
   composer pins in muse → hermes → opencode → codex → claude order.
   Also owns the Claude Code model/effort palette decisions (filter
   normalization, row mapping, effort levels, seed patch, fetch gates,
   response normalization, labels) as pure functions over explicit
   inputs; the page keeps supplement state, fetch/POST transport,
   palette/badge DOM, and orchestration, and calls into
   `CuttleChatAgentModel.*`.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Canonical backend pin read: `agent_pins[agent][kind]`.
     */
    function sessionPin(data, agent, kind) {
        const pins = data && data.agent_pins;
        const nested = pins && pins[agent] && pins[agent][kind];
        return String(nested == null ? '' : nested).trim();
    }

    /**
     * Global starred model/effort defaults, looked up over an explicit
     * map (the page passes its supplement maps). Case-insensitive.
     */
    function starredAgentModel(starredModels, agentId) {
        return String(((starredModels || {})[String(agentId || '').toLowerCase()]) || '');
    }

    function starredAgentEffort(starredEfforts, agentId) {
        return String(((starredEfforts || {})[String(agentId || '').toLowerCase()]) || '');
    }

    /**
     * True when this model/effort palette row is the agent's global
     * starred default. `starredValue` is resolved by the caller via
     * starredAgentModel / starredAgentEffort.
     */
    function isAgentDefaultStarred(spec, cmd, starredValue) {
        if (!spec || !cmd) return false;
        const rowVal = String(cmd.modelId || '').trim();
        if (!rowVal) return false;
        const star = String(starredValue || '');
        return !!star && star.toLowerCase() === rowVal.toLowerCase();
    }

    /**
     * Send-time model/effort identity for one POST, as a pins object
     * ({} when nothing applies — the page skips `agent_pins` then).
     *
     * `models`: { muse, hermes, opencode, codex, claude } each with
     * { model, effort, modelDirty, effortDirty }.
     *
     * Always include the active harness's current composer pins (not
     * only *Dirty): dirty-only attach missed "palette shows max, dirty
     * already cleared" and raced the user-badge snapshot against a
     * later pin POST. Other agents attach when dirty so a pre-session
     * pick is not lost.
     */
    function buildAgentPinsForRequest(parts) {
        const p = parts || {};
        const S = p.models || {};
        const pins = {};
        const put = (agent, slot) => {
            const entry = {};
            const m = String((slot && slot.model) || '').trim();
            const e = String((slot && slot.effort) || '').trim();
            if (m) entry.model = m;
            if (e) entry.effort = e;
            if (Object.keys(entry).length) pins[agent] = entry;
        };
        const msg = String(p.message || '');
        const activeMatch = msg.match(/^\/(muse|hermes|opencode|codex|claude)\b/i);
        const aid = activeMatch ? String(activeMatch[1]).toLowerCase() : '';
        if (aid === 'muse') put('muse', S.muse);
        else if (aid === 'hermes') put('hermes', S.hermes);
        else if (aid === 'opencode') put('opencode', S.opencode);
        else if (aid === 'codex') put('codex', S.codex);
        else if (aid === 'claude') put('claude', S.claude);
        const dirtyPut = (agent, slot) => {
            if (!slot || (!slot.modelDirty && !slot.effortDirty)) return;
            put(agent, slot);
        };
        dirtyPut('muse', S.muse);
        dirtyPut('hermes', S.hermes);
        dirtyPut('opencode', S.opencode);
        dirtyPut('codex', S.codex);
        dirtyPut('claude', S.claude);
        return pins;
    }

    /**
     * Pre-send gate for the Codex effort fetch. The palette fetch is
     * intentionally async; a fast Send must not persist a bare Codex
     * chip while the starred default is still loading, and must never
     * clobber a user pick that has not been persisted yet (dirty).
     *
     * Returns { fetch } or { fetch: true, url }. The page performs the
     * fetch, re-checks dirty after the await, and assigns.
     */
    function codexEffortFetchForSend(parts) {
        const p = parts || {};
        const text = String(p.messageText || '');
        if (!/^\/codex(?:\s|$)/i.test(text) && !p.hasCodexChip) return { fetch: false };
        if (p.codexEffortDirty) return { fetch: false };
        const key = p.sessionKey != null ? String(p.sessionKey) : '';
        if (key && String(p.effortKey || '') === key) return { fetch: false };
        return {
            fetch: true,
            url: key
                ? '/api/codex/effort?session=' + encodeURIComponent(key)
                : '/api/codex/effort',
        };
    }

    /**
     * Badge effort resolution: run data first (generic agent_effort,
     * then per-harness keys), then composer pins in muse → hermes →
     * opencode → codex → claude order. Matches the page's original `||` chain
     * exactly, including end-only trimming (a blank data value still
     * beats a pinned one, then trims to '').
     */
    function resolveAgentEffortForBadge(parts) {
        const p = parts || {};
        const data = p.data;
        const pinned = Array.isArray(p.pinnedEfforts) ? p.pinnedEfforts : [];
        const raw = String(
            (data && (data.agent_effort || data.muse_effort || data.hermes_effort
                || data.opencode_effort || data.codex_effort))
            || pinned[0]
            || pinned[1]
            || pinned[2]
            || pinned[3]
            || pinned[4]
            || ''
        );
        return raw.trim();
    }

    // Only explicit picks belong to a draft. Restored picks stay dirty until
    // the real session accepts them, so async defaults cannot replace them.
    const DRAFT_PIN_AGENTS = ['muse', 'hermes', 'opencode', 'codex', 'claude'];
    function draftOverrides(supplement) {
        const S = supplement || {};
        const pins = {};
        DRAFT_PIN_AGENTS.forEach((agent) => {
            const entry = {};
            ['Model', 'Effort'].forEach((kind) => {
                if (S[agent + kind + 'Dirty']) {
                    entry[kind.toLowerCase()] = String(S[agent + kind] || '');
                }
            });
            if (Object.keys(entry).length) pins[agent] = entry;
        });
        return pins;
    }

    function draftSupplementPatch(pins) {
        const patch = {};
        DRAFT_PIN_AGENTS.forEach((agent) => {
            ['Model', 'Effort'].forEach((kind) => {
                const value = pins && pins[agent] && pins[agent][kind.toLowerCase()];
                if (typeof value !== 'string') return;
                patch[agent + kind] = value;
                patch[agent + kind + 'Dirty'] = true;
            });
        });
        return patch;
    }

    // A running badge never reads next-send controls. Live execution wins;
    // before it arrives, use the latest real user's frozen send-time badge.
    function turnSlashFromMessages(messages, liveStatus) {
        if (liveStatus && liveStatus.active && liveStatus.slash_command) return liveStatus.slash_command;
        for (let i = (messages || []).length - 1; i >= 0; i--) {
            const message = messages[i];
            if (!message || message.role !== 'user') continue;
            let meta = message.metadata || {};
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
            }
            if (meta.steered || meta.speaker_kind === 'parent') continue;
            return meta.slash_command || message.slash_command || null;
        }
        return undefined; // An empty hub update supplies no new transcript.
    }

    function shouldAdoptComposerSelection(incoming, state) {
        return !!incoming && Array.isArray(incoming.stickyChips)
            && !(state && (state.pending || state.unsynced))
            && Number(incoming.revision || 0) >= Number((state && state.revision) || 0);
    }

    /**
     * Claude Code palette filter normalization. Strips the agent + section
     * prefixes the composer already matched (`claude model opus` → `opus`),
     * so the remainder filters rows. Pure string decisions; the page still
     * applies them via its matcher and ranker.
     */
    function claudeModelFilterForPalette(filterLower) {
        let modelFilter = String(filterLower || '').toLowerCase().trim()
            .replace(/^claude\s+/, '')
            .replace(/^models?\b\s*/, '')
            .trim();
        if (modelFilter === 'claude') modelFilter = '';
        return modelFilter;
    }

    function claudeEffortFilterForPalette(filterLower) {
        let effortFilter = String(filterLower || '').toLowerCase().trim()
            .replace(/^claude\s+/, '')
            .replace(/^efforts?\b\s*/, '')
            .trim();
        if (effortFilter === 'effort') effortFilter = '';
        return effortFilter;
    }

    /**
     * Map one Claude catalog row to its palette item (null when the row
     * has no id). `preferredLower` is the current pick, lower-cased.
     */
    function buildClaudeModelRow(model, preferredLower) {
        const m = model || {};
        const id = String((m && m.id) || '').trim();
        if (!id) return null;
        const label = String((m && m.label) || id).trim() || id;
        const current = id.toLowerCase() === String(preferredLower || '');
        const fav = !!(m && (m.favorite === true || m.favorite === '1' || m.favorite === 1));
        const modelEfforts = Array.isArray(m && m.efforts) ? m.efforts : [];
        const effortHint = modelEfforts.length
            ? 'Supported efforts: ' + modelEfforts.join(', ')
            : 'No effort levels for this model';
        return {
            category: 'claude-model',
            prefix: '/claude model ' + id,
            label: (fav ? '★ ' : '') + label + (current ? ' (current)' : ''),
            hint: [
                (m && m.description) || ('Set Claude Code model to ' + id),
                effortHint,
            ].filter(Boolean).join(' · '),
            meta: id,
            keywords: 'claude model ' + id + ' ' + label + ' ' + id.replace(/[-_/]+/g, ' '),
            modelId: id,
            claudeModel: true,
        };
    }

    /** Refresh-pick detection for the model palette (`refresh` alias). */
    function isClaudeModelRefreshPick(id) {
        const v = String(id || '').trim().toLowerCase();
        return v === 'refresh' || v === '__refresh__';
    }

    /** Flag the picked row current across the cached catalog. Pure map. */
    function markClaudeCurrentModel(models, id) {
        const pick = String(id || '');
        return (models || []).map((m) => ({
            ...m,
            current: String(m && m.id) === pick,
        }));
    }

    /**
     * Effective effort levels for the effort palette: the selected model's
     * per-model levels when a model is picked (possibly none, meaning the
     * model takes no --effort), else the catalog-wide common levels.
     */
    function claudeEffortLevelsForModel(parts) {
        const p = parts || {};
        const selectedModel = String(p.selectedModel || '').toLowerCase();
        const models = Array.isArray(p.models) ? p.models : [];
        const selectedRow = selectedModel
            ? models.find((m) => String(m && m.id || '').toLowerCase() === selectedModel)
            : null;
        if (selectedRow && Array.isArray(selectedRow.efforts)) return selectedRow.efforts;
        if (selectedModel) return [];
        return Array.isArray(p.commonEfforts) ? p.commonEfforts : [];
    }

    /** Map one effort level to its palette item. */
    function buildClaudeEffortRow(id, parts) {
        const p = parts || {};
        const preferred = String(p.preferredLower || '');
        const selectedModel = String(p.selectedModel || '');
        return {
            category: 'claude-effort',
            prefix: '/claude effort ' + id,
            label: 'Effort ' + id + (String(id).toLowerCase() === preferred ? ' (current)' : ''),
            hint: 'Set Claude Code effort (--effort) to ' + id
                + (selectedModel ? ' for ' + selectedModel : ' (CLI default model)'),
            meta: id,
            keywords: 'claude effort ' + id,
            modelId: id,
            claudeEffort: true,
        };
    }

    /**
     * Seed patch from canonical session data. Returns explicit values with
     * `undefined` meaning "leave the page's current value alone", so dirty
     * picks and key scoping stay exactly as the page applied them.
     */
    function claudeSeedPatchFromSessionData(data, parts) {
        const p = parts || {};
        const patch = {};
        const key = p.sessionKey != null ? String(p.sessionKey) : '';
        const serverModel = sessionPin(data, 'claude', 'model');
        if (serverModel && !p.modelDirty) {
            patch.model = serverModel;
            if (key) patch.modelsKey = key;
        }
        const serverEffort = sessionPin(data, 'claude', 'effort').toLowerCase();
        if (serverEffort && !p.effortDirty) {
            patch.effort = serverEffort;
            if (key) patch.effortKey = key;
        } else if (!p.effortDirty && key && data
            && data.agent_pins && data.agent_pins.claude) {
            patch.effort = '';
            patch.effortKey = key;
        }
        return patch;
    }

    /**
     * Fetch gate for the Claude model catalog. Mirrors
     * codexEffortFetchForSend: returns { fetch } or { fetch: true, url }.
     * The page performs the fetch and applies applyClaudeModelsResponse.
     */
    function claudeModelsFetchForPalette(parts) {
        const p = parts || {};
        if (!p.anyChat && !p.hasClaudeChip) return { fetch: false };
        const key = p.sessionKey != null ? String(p.sessionKey) : '';
        const forceRefresh = !!p.forceRefresh;
        if (!forceRefresh && (p.loading
            || (Number(p.modelsLength || 0) > 0 && String(p.modelsKey || '') === key)
            || (p.triedKey != null && String(p.triedKey) === key))) {
            return { fetch: false };
        }
        const params = [];
        if (key) params.push('session=' + encodeURIComponent(key));
        if (forceRefresh) params.push('refresh=1');
        return {
            fetch: true,
            url: params.length ? '/api/claude/models?' + params.join('&') : '/api/claude/models',
        };
    }

    /**
     * Normalize a Claude models response. `model` is null when the page
     * must keep its current pick (dirty); blank means the server named no
     * preferred model.
     */
    function applyClaudeModelsResponse(json, parts) {
        const j = json || {};
        const models = j && j.success && Array.isArray(j.models) ? j.models : [];
        return {
            models,
            model: parts && parts.modelDirty ? null : ((j && j.preferredModel) || ''),
            source: (j && j.source) || '',
            count: (j && (j.count != null ? j.count : models.length)) || 0,
            commonEfforts: j && Array.isArray(j.commonEfforts) ? j.commonEfforts : [],
            error: j && j.error ? String(j.error) : '',
        };
    }

    /**
     * Fetch gate for the Claude effort default. Returns { fetch } or
     * { fetch: true, url }; the page re-checks dirty after the await.
     */
    function claudeEffortFetchForPalette(parts) {
        const p = parts || {};
        if (!p.hasClaudeChip) return { fetch: false };
        const key = p.sessionKey != null ? String(p.sessionKey) : '';
        if (p.loading || (String(p.effortKey || '') === key && key)) return { fetch: false };
        return {
            fetch: true,
            url: key
                ? '/api/claude/effort?session=' + encodeURIComponent(key)
                : '/api/claude/effort',
        };
    }

    /** Normalize a Claude effort response to its preferred-effort string. */
    function applyClaudeEffortResponse(json) {
        return String((json && json.preferredEffort) || '');
    }

    /**
     * Pretty/short Claude model labels. The catalog is injected (the page
     * passes its cached rows); unknown ids fall back to a title-cased leaf.
     */
    function findClaudeCatalogLabel(models, raw) {
        const list = Array.isArray(models) ? models : [];
        const known = list.find(
            (m) => m && String(m.id).toLowerCase() === String(raw).toLowerCase()
        );
        return (known && known.label) || '';
    }

    function prettyClaudeModelLabel(model, models) {
        const raw = String(model || '').trim();
        if (!raw) return 'Claude Code';
        const known = findClaudeCatalogLabel(models, raw);
        if (known) return String(known);
        const leaf = raw.includes('/') ? raw.split('/').pop() : raw;
        return String(leaf || raw).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function claudeModelLabel(model, models) {
        const raw = String(model || '').trim();
        return findClaudeCatalogLabel(models, raw) || raw || 'default';
    }

    const api = {
        turnSlashFromMessages,
        shouldAdoptComposerSelection,
        draftOverrides,
        draftSupplementPatch,
        sessionPin,
        starredAgentModel,
        starredAgentEffort,
        isAgentDefaultStarred,
        buildAgentPinsForRequest,
        codexEffortFetchForSend,
        resolveAgentEffortForBadge,
        claudeModelFilterForPalette,
        claudeEffortFilterForPalette,
        buildClaudeModelRow,
        isClaudeModelRefreshPick,
        markClaudeCurrentModel,
        claudeEffortLevelsForModel,
        buildClaudeEffortRow,
        claudeSeedPatchFromSessionData,
        claudeModelsFetchForPalette,
        applyClaudeModelsResponse,
        claudeEffortFetchForPalette,
        applyClaudeEffortResponse,
        prettyClaudeModelLabel,
        claudeModelLabel,
    };

    const ns = (root.CuttleChatAgentModel = root.CuttleChatAgentModel || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
