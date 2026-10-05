/* ================================================================
   Cuttle Chat — sub-agent fleet cards (chat_subagent_fleet.js).
   Owner: fleet-card markup for child chats under a parent reply.
   One card per child: identity, harness · model · effort, outcome
   (queued / running / done / failed / cancelled / lost) and a
   one-line result whose full text rides the tooltip. Outcome and
   summary come from the server (`api.subagents.fleet`); this module
   never infers success. Pure: no document, no window, no fetch.
   The page owns mounting and click → open-chat (`data-chat-handle`).
   Experimental: `subagent_fleet_cards`. Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const MAX_CARDS = 24;
    const OUTCOMES = {
        queued: { icon: '◌', label: 'Queued' },
        running: { icon: '', label: 'Running' },
        done: { icon: '✓', label: 'Done' },
        failed: { icon: '✕', label: 'Failed' },
        cancelled: { icon: '⊘', label: 'Cancelled' },
        lost: { icon: '!', label: 'Lost' },
        unknown: { icon: '?', label: 'Unknown' },
    };

    function hasFleet(list) {
        return Array.isArray(list) && list.some((s) => s && s.fleet);
    }

    function parseTime(raw) {
        const s = String(raw || '').trim();
        if (!s) return NaN;
        // SQLite CURRENT_TIMESTAMP is UTC without a zone marker.
        return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(' ', 'T') + 'Z');
    }

    function formatDuration(startedAt, finishedAt) {
        const ms = parseTime(finishedAt) - parseTime(startedAt);
        if (!Number.isFinite(ms) || ms < 0) return '';
        const sec = Math.round(ms / 1000);
        if (sec < 60) return sec + 's';
        const min = Math.floor(sec / 60);
        if (min < 60) return min + 'm ' + String(sec % 60).padStart(2, '0') + 's';
        return Math.floor(min / 60) + 'h ' + String(min % 60).padStart(2, '0') + 'm';
    }

    /**
     * @param {Array} list normalized sub-agent entries (page normalizeSubagentList)
     * @param {{esc: Function, avatarHtml?: Function}} opts
     */
    function renderFleetHtml(list, opts) {
        const esc = opts.esc;
        const avatarHtml = typeof opts.avatarHtml === 'function' ? opts.avatarHtml : null;
        const items = (Array.isArray(list) ? list : []).filter(Boolean);
        if (!items.length) return '';
        const cards = items.slice(0, MAX_CARDS).map((s) => {
            const state = OUTCOMES[s.outcome] ? s.outcome : (s.generating ? 'running' : 'unknown');
            const meta = OUTCOMES[state];
            const name = String(s.displayName || s.label || s.handle);
            const segments = [s.agent, s.model, s.effort].map((x) => String(x || '').trim()).filter(Boolean);
            const duration = (state === 'running' || state === 'queued') ? '' : formatDuration(s.startedAt, s.finishedAt);
            const summary = String(s.summary || '');
            const tip = name + ' · ' + s.handle + ' · ' + meta.label + (s.detail ? '\n\n' + String(s.detail) : '');
            const avatar = s.avatar && s.avatar.toLowerCase() !== 'cuttle' && avatarHtml
                ? '<span class="subagent-fleet-avatar" aria-hidden="true">' + avatarHtml(s.avatar) + '</span>'
                : '<span class="subagent-launcher-orb" aria-hidden="true"></span>';
            return (
                '<button type="button" class="subagent-fleet-card is-' + state + '" data-chat-handle="' + esc(s.handle) + '"'
                + ' data-tooltip="' + esc(tip) + '" aria-label="' + esc('Open ' + name + ' (' + meta.label + ')') + '">'
                + '<span class="subagent-fleet-head">' + avatar
                + '<span class="subagent-fleet-name">' + esc(name) + '</span>'
                + '<span class="subagent-fleet-outcome"><i aria-hidden="true">' + esc(meta.icon) + '</i>' + esc(meta.label) + '</span>'
                + '</span>'
                + (segments.length
                    ? '<span class="subagent-fleet-agent">' + segments.map((x) => '<span>' + esc(x) + '</span>').join('') + '</span>'
                    : '')
                + '<span class="subagent-fleet-summary">' + esc(summary) + '</span>'
                + '<span class="subagent-fleet-foot"><span>' + esc(s.handle) + '</span>'
                + (duration ? '<span>' + esc(duration) + '</span>' : '') + '</span>'
                + '</button>'
            );
        }).join('');
        const more = items.length > MAX_CARDS
            ? '<span class="subagent-fleet-more">+' + (items.length - MAX_CARDS) + ' more in history</span>'
            : '';
        const done = items.filter((s) => s.outcome === 'done').length;
        return '<div class="subagent-launchers subagent-fleet" role="group" aria-label="'
            + esc('Sub-agent fleet: ' + done + ' of ' + items.length + ' done') + '">' + cards + more + '</div>';
    }

    const api = { hasFleet, renderFleetHtml, formatDuration, OUTCOMES };

    const ns = (root.CuttleChatSubagentFleet = root.CuttleChatSubagentFleet || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
