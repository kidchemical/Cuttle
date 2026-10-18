/*
 * Voice-mode agent processing activity (chat_voice_activity.js).
 *
 * Owns the chat-style "thinking / tool activity" bubble shown inside the voice
 * transcript while the selected agent runs. Reuses the regular chat view's
 * `.typing-indicator` / `.typing-orbit` / `.typing-wave` / `.typing-status`
 * primitives (built by chat_page.js for a running turn) so the voice surface
 * looks identical to a normal assistant processing bubble.
 *
 * Pure decisions + static markup only: chat_voice.js owns DOM insertion, phase
 * transitions and status updates, exactly like the rest of the overlay.
 */
(function (root) {
    'use strict';

    /** Label shown before the first live status arrives. */
    const DEFAULT_STATUS = 'Thinking…';

    /** The activity bubble only belongs on screen while the agent processes. */
    function shouldShow(phase) {
        return phase === 'processing';
    }

    /** Trim the live status; fall back to a stable label for empty text. */
    function normalizeStatus(text) {
        const t = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
        return t || DEFAULT_STATUS;
    }

    /**
     * Static orbit + wave markup, copied from the chat view's typing bubble.
     * Contains no dynamic text, so chat_voice.js can safely set it with
     * innerHTML and keep the status line as a textContent node.
     */
    function orbitHtml() {
        return '<div class="typing-orbit">'
            + '<span class="typing-orbit-ring"></span>'
            + '<span class="typing-orbit-ring typing-orbit-ring--inner"></span>'
            + '<span class="typing-orbit-core"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--1"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--2"></span>'
            + '<span class="typing-orbit-sat typing-orbit-sat--3"></span>'
            + '<span class="typing-orbit-beam"></span>'
            + '</div>'
            + '<div class="typing-wave"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>';
    }

    const api = {
        DEFAULT_STATUS: DEFAULT_STATUS,
        shouldShow: shouldShow,
        normalizeStatus: normalizeStatus,
        orbitHtml: orbitHtml,
    };
    root.CuttleChatVoiceActivity = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
