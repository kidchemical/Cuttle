/* ================================================================
   Cuttle Chat — session menu (chat_session_menu.js).
   Owner: the per-chat overflow menu — the header ☰ dropdown and the
   history-row menu share its items. Order: Copy → Export → Star → Mark
   unread → Rename → Sub-agents → owner items (Git push, Supervise,
   composer lock) → Terminal → Voice → Archive → Delete.

   Pure: itemsHtml(model, icons, esc) builds the markup from a fact
   snapshot. Controller: create(host) gathers facts through `host`, owns
   the header dropdown (open / close / outside click / Escape) and the
   Terminal item's availability probe. Item clicks stay on the page's
   `window.chatPage*` handlers; other owners contribute items through
   `host.extraItems`.
   ================================================================ */
(function (root) {
    'use strict';

    function jsArg(sessionId) {
        return String(sessionId).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }

    function item(cls, title, onclick, iconHtml, label, extra) {
        return '<button type="button" class="chat-chrome-menu-item' + (cls ? ' ' + cls : '') + '" role="menuitem"'
            + (extra || '') + ' title="' + title + '" aria-label="' + title + '"'
            + ' onclick="' + onclick + '">' + iconHtml
            + '<span class="chat-chrome-menu-item-label">' + label + '</span></button>';
    }

    /**
     * model: { sessionId|null, displayId, starred, includeRename, includeDelete,
     *          subagents: null|{collapsed}, extraHtml: [html], terminal, archive: null|{archived} }
     */
    function itemsHtml(model, icons, esc) {
        const m = model || {};
        const has = m.sessionId != null && m.sessionId !== '';
        const sid = has ? jsArg(m.sessionId) : '';
        const call = (fn) => 'window.' + fn + '(\'' + sid + '\', event)';
        const parts = [];
        if (has && m.displayId) {
            const copyLabel = esc('Copy ' + m.displayId);
            parts.push('<button type="button" class="chat-chrome-menu-item" role="menuitem"'
                + ' data-menu-action="copy-id" title="Copy chat ID" aria-label="' + copyLabel + '"'
                + ' onclick="' + call('copyChatSessionId') + '">' + icons.copy
                + '<span class="chat-chrome-menu-item-label">' + copyLabel + '</span>'
                + '<span class="chat-id-badge-label" hidden>' + esc(m.displayId) + '</span></button>');
        }
        if (has) {
            parts.push(item('', 'Export transcript (Markdown)', call('chatPageExportSession'), icons.download, 'Export'));
            const starLabel = m.starred ? 'Unstar chat' : 'Star chat';
            parts.push(item(m.starred ? 'is-starred' : '', starLabel, call('chatPageToggleStarSession'),
                icons.starOutline + icons.starFilled, starLabel));
            parts.push(item('', 'Mark as unread', call('chatPageMarkUnreadSession'), icons.unread, 'Mark as unread'));
        }
        if (has && m.includeRename) {
            parts.push(item('', 'Rename', call('chatPageRenameSession'), icons.pencil, 'Rename'));
        }
        if (m.subagents) {
            const subLabel = m.subagents.collapsed ? 'Show sub-agents' : 'Hide sub-agents';
            parts.push(item('', subLabel, call('toggleHistorySubagentGroup'), icons.subagents, subLabel));
        }
        (m.extraHtml || []).forEach((html) => { if (html) parts.push(html); });
        if (m.terminal) {
            parts.push(item('', 'Open terminal', 'window.chatPageOpenTerminalFromMenu(event)', icons.terminal, 'Terminal'));
        }
        parts.push(item('', 'Voice mode', 'window.chatPageEnterVoiceMode(event)', icons.voice, 'Voice'));
        if (has && m.archive) {
            const archiveLabel = m.archive.archived ? 'Unarchive' : 'Archive';
            parts.push(item('', archiveLabel, call('chatPageToggleArchiveSession'),
                m.archive.archived ? icons.unarchive : icons.archive, archiveLabel));
        }
        if (has && m.includeDelete) {
            parts.push(item('is-danger', 'Delete', call('chatPageRequestDeleteSession'), icons.trash, 'Delete'));
        }
        return parts.join('');
    }

    function isLoopbackHost(hostname) {
        const h = String(hostname || '').toLowerCase();
        return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1';
    }

    /**
     * host: { win, icons(), escapeHtml, facts(sid) → {displayId, starred, archived, canArchive, subagentGroup},
     *         extraItems(sid, icons) → [html], currentSession(), closeHistoryMenu(), afterFill(dropdown) }
     */
    function create(host) {
        const win = host.win;
        const doc = win.document;
        let terminalAvailable = true;

        /** Items for one chat; shared by the header dropdown and history-row menus. */
        function html(sessionId, opts) {
            const o = opts || {};
            const has = sessionId != null && sessionId !== '';
            const icons = host.icons();
            const f = has ? host.facts(sessionId) : {};
            const group = f.subagentGroup;
            return itemsHtml({
                sessionId: has ? sessionId : null,
                displayId: f.displayId || '',
                starred: !!f.starred,
                includeRename: has && o.includeRename !== false,
                includeDelete: has && o.includeDelete !== false,
                subagents: group ? { collapsed: group.classList.contains('is-collapsed') } : null,
                extraHtml: host.extraItems(sessionId, icons),
                terminal: terminalAvailable,
                archive: has && f.canArchive ? { archived: !!(o.archived || f.archived) } : null,
            }, icons, host.escapeHtml);
        }

        const button = () => doc.getElementById('chatChromeMenuBtn');
        const dropdown = () => doc.getElementById('chatChromeMenuDropdown');

        function isOpen() {
            const d = dropdown();
            return !!(d && !d.hidden);
        }

        function close() {
            const d = dropdown();
            const b = button();
            if (d) d.hidden = true;
            if (b) b.setAttribute('aria-expanded', 'false');
        }

        function toggle(forceOpen) {
            const b = button();
            const d = dropdown();
            if (!b || !d) return;
            const open = forceOpen == null ? d.hidden : !!forceOpen;
            if (open) {
                host.closeHistoryMenu();
                const sid = host.currentSession();
                const has = sid != null && sid !== '';
                d.innerHTML = html(has ? sid : null, { includeRename: has, includeDelete: has });
                host.afterFill(d);
            }
            d.hidden = !open;
            b.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        // Terminal needs /api/terminal/status; off-host clients without it hide the item.
        win.fetch('/api/terminal/status', { credentials: 'same-origin' })
            .then((resp) => { if (!resp.ok) terminalAvailable = false; })
            .catch(() => { if (!isLoopbackHost(win.location.hostname)) terminalAvailable = false; });

        const btn = button();
        if (btn) {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                toggle();
            });
        }
        doc.addEventListener('click', (e) => {
            if (!isOpen()) return;
            const menu = doc.getElementById('chatChromeMenu');
            if (menu && menu.contains(e.target)) return;
            close();
        });
        doc.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isOpen()) close();
        });

        return { html, isOpen, close, toggle };
    }

    const api = { itemsHtml, isLoopbackHost, create };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatSessionMenu = api;
})(typeof window !== 'undefined' ? window : globalThis);
