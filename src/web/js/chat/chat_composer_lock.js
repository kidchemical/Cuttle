/* ================================================================
   Cuttle Chat — composer lock (chat_composer_lock.js).
   Owner: whether the open chat's composer is locked, the centered lock
   overlay, and the chat-menu "Unlock composer" / "Lock composer" item.
   Agent-driven chats (sub-agent child chats and chats under supervision)
   open locked so a stray keystroke cannot land in the wrong chat. The user
   unlocks one chat from the ☰ menu; that choice is remembered on this
   device until they lock it again.

   Pure decisions: isSubagentIdentity / lockReason / isLocked /
   parseUnlocked / withUnlocked. Controller: create(host) makes the input
   parts `inert` (text, agent + project chips, attach / enhance / send)
   and leaves Stop and the context gauge live. Facts arrive from the page
   (setIdentity, from the messages payload) and the supervision controller
   (setSupervised); the controller never fetches and reads no page globals.
   ================================================================ */
(function (root) {
    'use strict';

    const STORAGE_KEY = 'cuttle.chat.composerUnlocked.v1';
    const MAX_REMEMBERED = 200;
    const WRAP_ID = 'chatComposerWrap';
    // Made inert while locked. Stop and the context gauge stay usable.
    const LOCKED_PARTS = ['#chatSlashChipsRow', '#chatAttachChipsRow', '.textarea-with-slash-menu',
        '#enhanceButton', '#attachButton', '#sendButton'];
    const REASON_TEXT = { subagent: 'sub-agent chat', supervised: 'supervised chat' };
    /** Padlock icon; `open` draws the shackle released. */
    function lockSvg(size, open) {
        return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            + '<rect x="4" y="11" width="16" height="10" rx="2"/>'
            + '<path d="' + (open ? 'M8 11V7a4 4 0 0 1 7.9-.9' : 'M8 11V7a4 4 0 0 1 8 0v4') + '"/></svg>';
    }

    /** True for a sub-agent child chat (messages payload `parent_session_id` / `origin`). */
    function isSubagentIdentity(identity) {
        const id = identity || {};
        const parent = id.parentSessionId;
        return (parent != null && String(parent) !== '') || String(id.origin || '') === 'subagent';
    }

    /** Why a chat locks by default: 'subagent', 'supervised' or '' (never locks). */
    function lockReason(facts) {
        const f = facts || {};
        if (f.subagent) return 'subagent';
        if (f.supervised) return 'supervised';
        return '';
    }

    function isLocked(reason, unlocked) {
        return !!reason && !unlocked;
    }

    function lockTitle(reason) {
        return 'Composer locked (' + (REASON_TEXT[reason] || 'agent chat') + '). Unlock it from the chat menu (☰).';
    }

    /** Stored unlocked-chat list → array of session id strings (bad input → []). */
    function parseUnlocked(raw) {
        try {
            const list = JSON.parse(raw || '[]');
            return Array.isArray(list) ? list.map(String).filter(Boolean) : [];
        } catch (_) {
            return [];
        }
    }

    /** New list with `sid` added (most recent last, capped) or removed. */
    function withUnlocked(list, sid, on) {
        const key = String(sid || '');
        const rest = (list || []).filter((s) => s !== key);
        if (!on || !key) return rest;
        rest.push(key);
        return rest.slice(-MAX_REMEMBERED);
    }

    function create(host) {
        const doc = host.doc;
        // One key per chat however the caller spells it (42, '42', db_session_42, CH-000042).
        const keyOf = (sid) => {
            if (sid == null || sid === '') return '';
            return String(host.sessionKey ? host.sessionKey(sid) : sid);
        };
        let session = '';
        let subagent = false;
        let supervised = false;
        let overlay = null;

        function storage() {
            try { return host.storage(); } catch (_) { return null; }
        }

        function readUnlocked() {
            const s = storage();
            try { return parseUnlocked(s && s.getItem(STORAGE_KEY)); } catch (_) { return []; }
        }

        function writeUnlocked(list) {
            const s = storage();
            try { if (s) s.setItem(STORAGE_KEY, JSON.stringify(list)); } catch (_) { /* quota / private mode */ }
        }

        const reason = () => (session ? lockReason({ subagent, supervised }) : '');
        const unlockedHere = () => !!session && readUnlocked().indexOf(session) >= 0;
        const locked = () => isLocked(reason(), unlockedHere());

        function ensureOverlay(wrap) {
            if (overlay && overlay.parentNode === wrap) return overlay;
            overlay = doc.createElement('div');
            overlay.className = 'composer-lock-overlay';
            overlay.innerHTML = '<span class="composer-lock-badge" role="img">' + lockSvg(16, false) + '</span>';
            wrap.appendChild(overlay);
            return overlay;
        }

        function apply() {
            const wrap = doc.getElementById(WRAP_ID);
            if (!wrap) return;
            const on = locked();
            wrap.classList.toggle('is-composer-locked', on);
            LOCKED_PARTS.forEach((sel) => wrap.querySelectorAll(sel).forEach((el) => {
                if (on) el.setAttribute('inert', '');
                else el.removeAttribute('inert');
            }));
            if (on) {
                const badge = ensureOverlay(wrap).firstChild;
                const title = lockTitle(reason());
                badge.setAttribute('title', title);
                badge.setAttribute('aria-label', title);
                overlay.hidden = false;
            } else if (overlay) {
                overlay.hidden = true;
            }
        }

        /** Open chat changed: forget the last chat's facts (unlocked until this chat's arrive). */
        function setSession(sid) {
            const key = keyOf(sid);
            if (key === session) return;
            session = key;
            subagent = false;
            supervised = false;
            apply();
        }

        /** The open chat's messages payload arrived. `identity` = page session identity. */
        function setIdentity(sid, identity) {
            setSession(sid);
            subagent = isSubagentIdentity(identity);
            apply();
        }

        /** From the supervision controller; ignored when it reports for another chat. */
        function setSupervised(sid, on) {
            if (keyOf(sid) !== session) return;
            supervised = !!on;
            apply();
        }

        function menuHtml(sessionId) {
            if (!session || keyOf(sessionId) !== session || !reason()) return '';
            const unlock = locked();
            const label = unlock ? 'Unlock composer' : 'Lock composer';
            return '<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' data-composer-lock="' + (unlock ? 'unlock' : 'lock') + '"'
                + ' title="' + label + '" aria-label="' + label + '">' + lockSvg(14, unlock)
                + '<span class="chat-chrome-menu-item-label">' + label + '</span></button>';
        }

        function setUnlocked(on) {
            if (!session) return;
            writeUnlocked(withUnlocked(readUnlocked(), session, on));
            apply();
            if (on) {
                const input = doc.getElementById('chatInput');
                if (input) input.focus();
            }
        }

        /** Send-path guard for composer-sourced sends (form resumes pass explicit text and are allowed). */
        function blocksSend() {
            if (!locked()) return false;
            host.toast('Composer is locked. Unlock it from the chat menu (☰).', 'info');
            return true;
        }

        doc.addEventListener('click', (ev) => {
            const btn = ev.target.closest && ev.target.closest('[data-composer-lock]');
            if (!btn) return;
            ev.preventDefault();
            ev.stopPropagation();
            host.closeMenus();
            setUnlocked(btn.getAttribute('data-composer-lock') === 'unlock');
        }, true);
        // Another pane on this device unlocked or relocked a chat.
        root.addEventListener('storage', (ev) => { if (ev.key === STORAGE_KEY) apply(); });

        return { setSession, setIdentity, setSupervised, menuHtml, blocksSend, isLocked: locked, reason, setUnlocked };
    }

    const api = {
        STORAGE_KEY, LOCKED_PARTS, isSubagentIdentity, lockReason, isLocked, lockTitle,
        parseUnlocked, withUnlocked, create,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatComposerLock = api;
})(typeof window !== 'undefined' ? window : globalThis);
