/* Agent identity — the colour dot and plain name for an agent.
 *
 * Pure: no DOM lookups, no fetch, no storage. The colour is read from the
 * catalog row (`color` in each harness manifest.yaml, exposed by
 * `catalog.public_catalog`); nothing is hard-coded per call site. An unknown
 * agent, a missing colour or a malformed one gets the neutral grey.
 *
 *   forAgent(agentId, catalog) -> {color, name}
 *   dotHtml(identity)          -> '<span class="agent-dot" …></span>'
 *   labelHtml(identity)        -> dot + escaped name
 *
 * `catalog` is the row list from `public_catalog` (served by `GET /api/agents`).
 * Loaded before any owner that renders agent names (Tasks, fleet, tray,
 * supervision, meters). Also loads under node for tests (module.exports).
 */
(function (root) {
    'use strict';

    /** Used for an unknown agent or an invalid/missing manifest colour. */
    const NEUTRAL_COLOR = '#6b7280';
    const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** `#rrggbb` (lower-cased) or the neutral grey. Anything else never reaches CSS. */
    function colorOrNeutral(raw) {
        const value = String(raw == null ? '' : raw).trim();
        return HEX_COLOR.test(value) ? value.toLowerCase() : NEUTRAL_COLOR;
    }

    /** {color, name} for an agent id, looked up in a catalog array. Pure. */
    function forAgent(agentId, catalog) {
        const id = String(agentId == null ? '' : agentId).trim();
        const rows = Array.isArray(catalog) ? catalog : [];
        const row = id ? rows.find((r) => r && String(r.id) === id) : null;
        if (!row) return { color: NEUTRAL_COLOR, name: id || 'Agent' };
        return {
            color: colorOrNeutral(row.color),
            name: String(row.label || row.id || id),
        };
    }

    /** The coloured dot alone, decorative (the name next to it carries the meaning). */
    function dotHtml(identity) {
        const color = colorOrNeutral(identity && identity.color);
        return '<span class="agent-dot" style="background:' + color + '" aria-hidden="true"></span>';
    }

    /** Dot followed by the escaped plain name, e.g. "● Codex". */
    function labelHtml(identity) {
        const name = escapeHtml(identity && identity.name ? identity.name : 'Agent');
        return '<span class="agent-identity">' + dotHtml(identity)
            + '<span class="agent-identity-name">' + name + '</span></span>';
    }

    const api = { NEUTRAL_COLOR, colorOrNeutral, escapeHtml, forAgent, dotHtml, labelHtml };
    root.CuttleAgentIdentity = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
