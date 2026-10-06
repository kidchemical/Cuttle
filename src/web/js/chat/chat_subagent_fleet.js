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
     * Minimal shared agent chip: agent label only, `{agent} | {model} | {effort}` in the tip.
     * Same `slash-command-chip` classes as the header/composer chips; the page may
     * override via `opts.chipHtml` for palette labels.
     */
    function defaultAgentChipHtml(s, esc) {
        const agent = String((s && s.agent) || '').trim();
        if (!agent) return '';
        const bits = [agent, s.model, s.effort].map((x) => String(x || '').trim()).filter(Boolean);
        const tip = bits.join(' | ');
        return '<span class="slash-command-chip slash-command-chip--header slash-command-chip--no-shorten"'
            + ' role="img" aria-label="' + esc('Agent: ' + tip) + '"'
            + ' title="' + esc(tip) + '" data-full-label="' + esc(agent) + '" data-tip="' + esc(tip) + '">'
            + '<span class="slash-chip-label">' + esc(agent) + '</span></span>';
    }

    /**
     * Live-state orbit: the same typing-orbit treatment as the parent bubble,
     * sized down for the card (`typing-orbit--fleet`). The core carries the
     * avatar glyph when there is one; terminal states keep the plain avatar/dot.
     */
    function orbitHtml(s, avatarHtml) {
        const a = String((s && s.avatar) || '').trim();
        const core = (a && a.toLowerCase() !== 'cuttle' && typeof avatarHtml === 'function')
            ? '<span class="typing-orbit-core typing-orbit-core--glyph">' + avatarHtml(a) + '</span>'
            : '<span class="typing-orbit-core"></span>';
        return '<span class="typing-orbit typing-orbit--fleet" aria-hidden="true">'
            + '<span class="typing-orbit-ring"></span>'
            + '<span class="typing-orbit-ring typing-orbit-ring--inner"></span>'
            + core
            + '<span class="typing-orbit-sat typing-orbit-sat--1"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--2"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--3"></span>'
            + '<span class="typing-orbit-beam"></span>'
            + '</span>';
    }

    /**
     * @param {Array} list normalized sub-agent entries (page normalizeSubagentList)
     * @param {{esc: Function, avatarHtml?: Function, chipHtml?: Function}} opts
     */
    function renderFleetHtml(list, opts) {
        const esc = opts.esc;
        const avatarHtml = typeof opts.avatarHtml === 'function' ? opts.avatarHtml : null;
        const items = (Array.isArray(list) ? list : []).filter(Boolean);
        if (!items.length) return '';
        const chipHtml = typeof opts.chipHtml === 'function'
            ? opts.chipHtml
            : ((s) => defaultAgentChipHtml(s, esc));
        const cards = items.slice(0, MAX_CARDS).map((s) => {
            const state = OUTCOMES[s.outcome] ? s.outcome : (s.generating ? 'running' : 'unknown');
            const meta = OUTCOMES[state];
            const name = String(s.displayName || s.label || s.handle);
            const duration = (state === 'running' || state === 'queued') ? '' : formatDuration(s.startedAt, s.finishedAt);
            const summary = String(s.summary || '');
            const tip = name + ' · ' + s.handle + ' · ' + meta.label + (s.detail ? '\n\n' + String(s.detail) : '')
                + (state === 'running' && s.liveStatusAt ? '\nUpdated: ' + String(s.liveStatusAt) + ' UTC' : '');
            const live = state === 'running' || state === 'queued';
            const avatar = live
                ? orbitHtml(s, avatarHtml)
                : (s.avatar && s.avatar.toLowerCase() !== 'cuttle' && avatarHtml
                    ? '<span class="subagent-fleet-avatar" aria-hidden="true">' + avatarHtml(s.avatar) + '</span>'
                    : '<span class="subagent-launcher-orb" aria-hidden="true"></span>');
            return (
                '<button type="button" class="subagent-fleet-card is-' + state + '" data-chat-handle="' + esc(s.handle) + '"'
                + ' data-tooltip="' + esc(tip) + '" aria-label="' + esc('Open ' + name + ' (' + meta.label + ')') + '">'
                + '<span class="subagent-fleet-head">' + avatar
                + '<span class="subagent-fleet-name">' + esc(name) + '</span>'
                + '<span class="subagent-fleet-outcome"><i aria-hidden="true">' + esc(meta.icon) + '</i>' + esc(meta.label) + '</span>'
                + '</span>'
                + (chipHtml(s) || '')
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
