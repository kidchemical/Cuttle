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

   Mirrors the page contract: agent_pins nested reads win over legacy
   flat keys, the active harness always attaches its current composer
   pins (dirty-only for the rest), the Codex pre-send fetch never
   clobbers a dirty pick, and badge effort prefers run data over
   composer pins in muse → hermes → opencode → codex order.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Canonical backend pin read: nested `agent_pins[agent][kind]`
     * wins; blank nested falls back to the legacy flat key
     * (`<agent>_<kind>`).
     */
    function sessionPin(data, agent, kind) {
        const pins = data && data.agent_pins;
        const nested = pins && pins[agent] && pins[agent][kind];
        if (nested != null && String(nested).trim() !== '') return String(nested).trim();
        return String((data && data[agent + '_' + kind]) || '').trim();
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
     * `models`: { muse, hermes, opencode, codex } each with
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
        const activeMatch = msg.match(/^\/(muse|hermes|opencode|codex)\b/i);
        const aid = activeMatch ? String(activeMatch[1]).toLowerCase() : '';
        if (aid === 'muse') put('muse', S.muse);
        else if (aid === 'hermes') put('hermes', S.hermes);
        else if (aid === 'opencode') put('opencode', S.opencode);
        else if (aid === 'codex') put('codex', S.codex);
        const dirtyPut = (agent, slot) => {
            if (!slot || (!slot.modelDirty && !slot.effortDirty)) return;
            put(agent, slot);
        };
        dirtyPut('muse', S.muse);
        dirtyPut('hermes', S.hermes);
        dirtyPut('opencode', S.opencode);
        dirtyPut('codex', S.codex);
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
     * opencode → codex order. Matches the page's original `||` chain
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
            || ''
        );
        return raw.trim();
    }

    const api = {
        sessionPin,
        starredAgentModel,
        starredAgentEffort,
        isAgentDefaultStarred,
        buildAgentPinsForRequest,
        codexEffortFetchForSend,
        resolveAgentEffortForBadge,
    };

    const ns = (root.CuttleChatAgentModel = root.CuttleChatAgentModel || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
