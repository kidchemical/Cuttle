/* History panel rendering: history DOM refreshes (keep the reader's row in
   view, never re-focus it) plus the pure history ROW/HEADER markup.

   Owner: history row/header rendering. `createRenderers(ctx)` binds the
   page-supplied session/attention/project capabilities into row/header
   renderers; `chat_history_panel.js` consumes the produced functions and the
   page composes them. Keep this module free of session/attention decisions —
   it only formats what the page hands it. */
(function (root) {
    'use strict';
    const rendered = new WeakMap();

    /** Initial chats shown per project; each "Show N more" adds another page. */
    const HISTORY_GROUP_PREVIEW_LIMIT = 5;
    const HISTORY_GROUP_PAGE_SIZE = 5;

    function preserveScroll(container, update) {
        const top = container.scrollTop;
        const left = container.scrollLeft;
        const bounds = container.getBoundingClientRect();
        const anchor = top > 0 && Array.from(container.querySelectorAll('.chat-history-item')).find((item) => {
            const rect = item.getBoundingClientRect();
            return rect.height > 0 && rect.bottom > bounds.top && rect.top < bounds.bottom;
        });
        const id = anchor && anchor.dataset.sessionId;
        const offset = anchor && anchor.getBoundingClientRect().top - bounds.top;
        update();
        const replacement = id && Array.from(container.querySelectorAll('.chat-history-item')).find((item) =>
            item.dataset.sessionId === id && item.getBoundingClientRect().height > 0);
        container.scrollTop = replacement
            ? container.scrollTop + replacement.getBoundingClientRect().top - container.getBoundingClientRect().top - offset
            : top;
        container.scrollLeft = left;
    }

    function entrySearchMatchRank(entry) {
        // Lower sorts first: title matches above content-only matches.
        const s = (entry && (entry.serverSession || entry.session)) || {};
        const m = String((entry && entry.searchMatch) || s.match || '').toLowerCase();
        if (m === 'title') return 0;
        if (m === 'content') return 1;
        return 2;
    }

    function paint(container, html, enhance) {
        // Compare source markup, since tooltips and live indicators modify DOM.
        if (rendered.get(container) === html) {
            if (enhance) enhance();
            return;
        }
        preserveScroll(container, () => {
            container.innerHTML = html;
            rendered.set(container, html);
            if (enhance) enhance();
        });
    }

    /** Menu/item SVG fragments, shared by history rows and the (page-owned) chrome menu. */
    function historyActionIcons() {
        return {
            // Match in-chat chrome hamburger (3 lines), not a vertical kebab.
            hamburger: '<span class="chat-chrome-hamburger" aria-hidden="true"><span></span><span></span><span></span></span>',
            copy: '<svg class="chat-id-badge-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="18" cy="5" r="3" stroke="currentColor" stroke-width="2"/><circle cx="6" cy="12" r="3" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="19" r="3" stroke="currentColor" stroke-width="2"/><line x1="8.6" y1="10.5" x2="15.4" y2="6.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg><svg class="chat-id-badge-check" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20 6 9 17l-5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            pencil: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>',
            trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>',
            download: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
            archive: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
            unarchive: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><polyline points="12 12 12 16"/><polyline points="9.5 13.5 12 11 14.5 13.5"/></svg>',
            starOutline: '<svg class="chat-star-menu-icon chat-star-menu-icon--outline" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.8 6.7 19.6l1-5.8-4.2-4.1 5.9-.9L12 3.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
            starFilled: '<svg class="chat-star-menu-icon chat-star-menu-icon--filled" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.8 6.7 19.6l1-5.8-4.2-4.1 5.9-.9L12 3.5z"/></svg>',
            unread: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="5" fill="currentColor" opacity="0.9"/><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.6" opacity="0.45"/></svg>',
            gitPush: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M5 12l7-7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 19h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            terminal: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><polyline points="4 17 10 11 4 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="19" x2="20" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            voice: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="12" y1="19" x2="12" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="8" y1="23" x2="16" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
            subagents: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="6" cy="6" r="2.2" stroke="currentColor" stroke-width="2"/><circle cx="6" cy="18" r="2.2" stroke="currentColor" stroke-width="2"/><circle cx="18" cy="12" r="2.2" stroke="currentColor" stroke-width="2"/><path d="M8.3 7.2c2.1 2 2.1 7.6 0 9.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M8.4 12H15.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
        };
    }

    function historyStarredIconHTML() {
        return '<span class="history-starred-icon" title="Starred" aria-label="Starred">★</span>';
    }

    /** Meta line above the name: chips, badges, state dots. Empty → hidden by CSS. */
    function historyItemMetaHTML(parts) {
        const html = (parts || [])
            .filter(Boolean)
            .map((s) => String(s).trim())
            .join('');
        return '<div class="history-item-meta">' + html + '</div>';
    }

    /**
     * Bind page-supplied capabilities into history row/header renderers.
     * ctx: { escapeHtml, escapeHtmlInline, currentSessionId, sessionIdsEqual,
     *        sessionShowsHistorySpinner, sessionHistoryAttentionKind,
     *        resolveSessionProjectInfo, projectChipHtml, historyBranchFor,
     *        chatTitleParts, historyAttentionIconHTML, openPaneEntriesForSession,
     *        historyOpenPaneBadgeHTML, historySubagentToggleTitle,
     *        formatHistoryLastMessageAge, resolveHistoryLastMessageMs,
     *        historyRunningIconHTML, formatChatDisplayId }
     */
    function createRenderers(ctx) {
        const escapeHtml = ctx.escapeHtml;
        const escapeHtmlInline = ctx.escapeHtmlInline;

        function createHistoryDeleteActionsHTML(sessionId, opts) {
            const icons = historyActionIcons();
            const includeRename = !!(opts && opts.includeRename);
            const lastMs = Number(opts && opts.lastMessageMs) || 0;
            const sid = String(sessionId).replace(/'/g, "\\'");
            const timeHtml = lastMs > 0
                ? `<time class="chat-time" datetime="${new Date(lastMs).toISOString()}" data-ts="${lastMs}" title="${escapeHtmlInline(new Date(lastMs).toLocaleString())}">${escapeHtmlInline(ctx.formatHistoryLastMessageAge(lastMs))}</time>`
                : '';
            const openHtml = ctx.historyOpenPaneBadgeHTML(sessionId);
            return `
            <div class="chat-actions" data-include-rename="${includeRename ? '1' : '0'}">
                ${openHtml}
                ${timeHtml}
                <button type="button" class="chat-action-button chat-menu-btn"
                    onclick="window.chatPageToggleHistoryMenu('${sid}', event)"
                    title="Chat actions"
                    aria-label="Chat actions"
                    aria-haspopup="menu"
                    aria-expanded="false">
                    ${icons.hamburger}
                </button>
            </div>
        `;
        }

        function createHistorySectionHeaderHTML(title, opts) {
            const showRefresh = !(opts && opts.showRefresh === false);
            const project = opts && opts.project;
            const collapsible = !!(opts && opts.collapsible && project && project.key);
            const expanded = !(opts && opts.collapsed);
            const count = Number(opts && opts.count) || 0;
            const hasError = !!(opts && opts.hasError);
            const hasUnread = !!(opts && opts.hasUnread) || hasError;
            const hasQueued = !!(opts && opts.hasQueued);
            const hasPaused = !!(opts && opts.hasPaused);
            const refreshBtn = showRefresh
                ? `<button type="button" class="history-refresh-btn" id="chatHistoryRefreshBtn" onclick="window.refreshChatHistoryList(event)" title="Refresh chats" aria-label="Refresh chat list">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <polyline points="23 4 23 10 17 10"/>
                        <polyline points="1 20 1 14 7 14"/>
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                    </svg>
                </button>`
                : '';
            const newChatBtn = (project && project.key !== '__none__' && project.key !== '__discord__')
                ? `<button type="button" class="history-new-chat-btn"
                    data-project-id="${escapeHtml(project.id != null ? String(project.id) : '')}"
                    data-project-name="${escapeHtml(project.name || '')}"
                    data-project-path="${escapeHtml(project.path || '')}"
                    onclick="window.newChatForProject(event)"
                    title="New chat in ${escapeHtml(project.name || 'this project')}"
                    aria-label="New chat in ${escapeHtml(project.name || 'this project')}">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">
                        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                </button>`
                : '';
            const chevron = collapsible
                ? `<span class="history-section-chevron" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
               </span>`
                : '';
            const countClass = hasError
                ? ' has-unread has-unread-error'
                : (hasUnread
                    ? ' has-unread'
                    : (hasQueued ? ' has-queued' : (!hasUnread && hasPaused ? ' has-paused-queue' : '')));
            const countHtml = collapsible && count > 0
                ? `<span class="history-section-count${countClass}" title="${count} chat${count === 1 ? '' : 's'}">${count}</span>`
                : '';
            const attentionDot = hasError
                ? `<span class="history-section-unread-dot is-error" title="Unread error" aria-label="Unread error"></span>`
                : (hasUnread
                    ? `<span class="history-section-unread-dot" title="Unread" aria-label="Unread"></span>`
                    : (hasQueued
                        ? `<span class="history-section-queued-dot" title="Queued prompt" aria-label="Queued prompt"></span>`
                        : (hasPaused
                            ? `<span class="history-section-paused-dot" title="Paused queue" aria-label="Paused queued prompt"></span>`
                            : '')));
            const headerAttrs = collapsible
                ? ` role="button" tabindex="0" data-project-key="${escapeHtml(String(project.key))}" aria-expanded="${expanded ? 'true' : 'false'}" onclick="window.toggleHistoryProjectCollapse(event)" onkeydown="window.toggleHistoryProjectCollapseKey(event)"`
                : '';
            return `
            <div class="history-section-header${collapsible ? ' is-collapsible' : ''}"${headerAttrs}>
                ${chevron}
                <div class="history-section-title">${attentionDot}${escapeHtml(title)}${countHtml}</div>
                <div class="history-section-actions" onclick="event.stopPropagation()">${newChatBtn}${refreshBtn}</div>
            </div>
        `;
        }

        function createAuthHistoryItemHTML(s, extraOpts) {
            const sid = s.id;
            const title = s.session_name || (`Chat ${sid}`);
            const active = ctx.sessionIdsEqual(ctx.currentSessionId(), sid) ? ' active' : '';
            const running = ctx.sessionShowsHistorySpinner(sid);
            const runningClass = running ? ' is-running' : '';
            const attention = ctx.sessionHistoryAttentionKind(sid, s);
            const unreadClass = attention === 'input' ? ' has-input' : (attention === 'unread' || attention === 'error') ? ' has-unread' : '';
            const errorClass = attention === 'error' ? ' has-unread-error' : '';
            const queuedClass = attention === 'queued' ? ' has-queued' : '';
            const pausedClass = attention === 'paused' ? ' has-paused-queue' : '';
            const starred = !!Number(s.starred);
            const starredClass = starred ? ' is-starred' : '';
            const runningIcon = running ? ctx.historyRunningIconHTML() : '';
            const starredIcon = starred ? historyStarredIconHTML() : '';
            const count = Number(s.message_count) || 0;
            const origin = String(s.origin || '').trim().toLowerCase();
            const parentId = s.parent_session_id != null ? String(s.parent_session_id) : '';
            const itemOpts = extraOpts || {};
            const childCount = Math.max(0, Number(itemOpts.subagentChildCount) || 0);
            const subExpanded = !!itemOpts.subagentExpanded;
            const subForce = !!itemOpts.subagentForceExpanded;
            const subagentClass = (origin === 'subagent' || parentId) ? ' is-subagent' : '';
            const hasSubagentsClass = childCount > 0 ? ' has-subagents' : '';
            const discordName = String(s.discord_username || '').trim();
            const participants = Array.isArray(s.discord_participants) ? s.discord_participants : [];
            const participantNames = participants
                .map((p) => String((p && (p.discord_username || p.discord_user_id)) || '').trim())
                .filter(Boolean);
            const isDiscordOrigin = origin === 'discord_dm' || origin === 'discord_guild';
            const discordTip = participantNames.length
                ? ('Discord · ' + participantNames.join(', '))
                : (discordName ? ('Discord · ' + discordName) : 'Discord');
            const discordBadge = (isDiscordOrigin || participantNames.length)
                ? `<span class="history-kind-badge" title="${escapeHtml(discordTip)}">Discord</span>`
                : '';
            const subagentBadge = (origin === 'subagent' || parentId)
                ? `<span class="history-kind-badge history-subagent-badge" title="Sub-agent of ${escapeHtml(parentId ? ('CH-' + String(parentId).padStart(6, '0')) : 'parent chat')}">Subagent</span>`
                : '';
            const projInfo = ctx.resolveSessionProjectInfo(sid, s);
            const projectChip = projInfo
                ? ctx.projectChipHtml(projInfo, {
                    history: true,
                    worktree: !!String(s.checkout_uid || '').trim(),
                    branch: ctx.historyBranchFor(s),
                })
                : '';
            const badge = count > 0
                ? `<span class="history-msg-badge" title="${count} message${count === 1 ? '' : 's'}" aria-label="${count} messages">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <span class="history-msg-count">${count}</span>
               </span>`
                : '';
            const subToggleTitle = ctx.historySubagentToggleTitle(childCount, subExpanded, subForce);
            const subCountBadge = childCount > 0
                ? `<button type="button" class="history-subagent-count-badge" data-child-count="${childCount}" title="${escapeHtml(subToggleTitle)}" aria-label="${escapeHtml(subToggleTitle)}" onclick="window.toggleHistorySubagentGroup('${sid}', event)">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="12" r="2.4"/><path d="M8.4 7.2c2.2 2 2.2 7.6 0 9.6"/><path d="M8.5 12H15.5"/></svg>
                <span class="history-msg-count">${childCount}</span>
               </button>`
                : '';
            const subToggle = childCount > 0
                ? `<button type="button" class="history-subagent-toggle" data-child-count="${childCount}" aria-expanded="${subExpanded ? 'true' : 'false'}" aria-controls="history-subagents-${escapeHtml(String(sid))}-kids" title="${escapeHtml(subToggleTitle)}" aria-label="${escapeHtml(subToggleTitle)}" onclick="window.toggleHistorySubagentGroup('${sid}', event)">
                <span class="history-subagent-chevron" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </span>
               </button>`
                : '';
            const parts = ctx.chatTitleParts(title, sid);
            const openClass = ctx.openPaneEntriesForSession(sid).length ? ' is-open-pane' : '';
            const snippet = String(s.search_snippet || s.snippet || '').trim();
            const isContentMatch = String(s.match || '').toLowerCase() === 'content';
            const snippetText = snippet || (isContentMatch ? 'Matched in message body' : '');
            const snippetHtml = snippetText
                ? `<div class="history-search-snippet${isContentMatch ? ' is-content-match' : ''}">${escapeHtml(snippetText)}</div>`
                : '';
            const contentBadge = isContentMatch
                ? `<span class="history-kind-badge history-match-badge" title="Matched in message body">In messages</span>`
                : '';
            return `<div class="chat-history-item${active}${runningClass}${unreadClass}${errorClass}${queuedClass}${pausedClass}${starredClass}${openClass}${subagentClass}${hasSubagentsClass}" data-session-id="${sid}" data-parent-session-id="${escapeHtml(parentId)}" data-search-match="${isContentMatch ? 'content' : 'title'}" data-chat-title="${escapeHtml(title).replace(/"/g, '&quot;')}" onclick="window.chatPageLoadSession('${sid}')">
            ${ctx.historyAttentionIconHTML(attention)}
            ${subToggle}
            <div class="history-item-body">
                ${historyItemMetaHTML([parts.chipsHtml, projectChip, discordBadge, subagentBadge, contentBadge, subCountBadge, badge, starredIcon, runningIcon])}
                <div class="chat-title">${parts.titleHtml}</div>
                ${snippetHtml}
            </div>
            ${createHistoryDeleteActionsHTML(sid, { includeRename: true, starred, lastMessageMs: ctx.resolveHistoryLastMessageMs(s) })}
        </div>`;
        }

        function createHistoryItemHTML(session) {
            const isActive = ctx.currentSessionId() === session.id;
            const running = ctx.sessionShowsHistorySpinner(session.id);
            const attention = ctx.sessionHistoryAttentionKind(session.id, session);
            const starred = !!session.starred;
            const parts = ctx.chatTitleParts(session.title, session.id);
            const openClass = ctx.openPaneEntriesForSession(session.id).length ? ' is-open-pane' : '';
            const isContentMatch = String(session.match || '').toLowerCase() === 'content';
            const contentBadge = isContentMatch
                ? '<span class="history-kind-badge history-match-badge" title="Matched in message body">In messages</span>'
                : '';
            const snippet = String(session.search_snippet || session.snippet || '').trim();
            const snippetText = snippet || (isContentMatch ? 'Matched in message body' : '');
            const snippetHtml = snippetText
                ? `<div class="history-search-snippet${isContentMatch ? ' is-content-match' : ''}">${escapeHtml(snippetText)}</div>`
                : '';
            const projInfo = ctx.resolveSessionProjectInfo(session.id, session);
            const projectChip = projInfo
                ? ctx.projectChipHtml(projInfo, {
                    history: true,
                    worktree: !!String(session.checkout_uid || '').trim(),
                    branch: ctx.historyBranchFor(session),
                })
                : '';
            const meta = historyItemMetaHTML([
                '<span class="chat-icon">💬</span>',
                parts.chipsHtml,
                projectChip,
                contentBadge,
                starred ? historyStarredIconHTML() : '',
                running ? ctx.historyRunningIconHTML() : '',
            ]);
            const unreadClass = attention === 'input' ? ' has-input' : (attention === 'unread' || attention === 'error') ? ' has-unread' : '';
            const errorClass = attention === 'error' ? ' has-unread-error' : '';
            const queuedClass = attention === 'queued' ? ' has-queued' : '';
            const pausedClass = attention === 'paused' ? ' has-paused-queue' : '';
            return `
            <div class="chat-history-item ${isActive ? 'active' : ''}${running ? ' is-running' : ''}${unreadClass}${errorClass}${queuedClass}${pausedClass}${starred ? ' is-starred' : ''}${openClass}" 
                 data-session-id="${session.id}"
                 data-search-match="${isContentMatch ? 'content' : 'title'}"
                 data-chat-title="${escapeHtml(String(session.title || '')).replace(/"/g, '&quot;')}"
                 onclick="window.chatPageLoadSession('${session.id}')">
                ${ctx.historyAttentionIconHTML(attention)}
                <div class="history-item-body">
                    ${meta}
                    <div class="chat-title">${parts.titleHtml}</div>
                    ${snippetHtml}
                </div>
                ${createHistoryDeleteActionsHTML(session.id, { includeRename: true, starred, lastMessageMs: ctx.resolveHistoryLastMessageMs(session) })}
            </div>
        `;
        }

        function createTerminalHistoryItemHTML(session) {
            const sid = String(session.id).replace(/'/g, "\\'");
            const displayId = ctx.formatChatDisplayId(session.id) || '';
            const title = session.title || session.shell || 'Terminal';
            const starred = !!session.starred;
            const openClass = ctx.openPaneEntriesForSession(session.id).length ? ' is-open-pane' : '';
            const meta = historyItemMetaHTML([
                '<span class="chat-icon" aria-hidden="true">⌨️</span>',
                '<span class="history-kind-badge" title="Terminal session">Terminal</span>',
                displayId
                    ? `<span class="history-chat-handle" title="${escapeHtml(displayId)}">${escapeHtml(displayId)}</span>`
                    : '',
                starred ? historyStarredIconHTML() : '',
            ]);
            return `
            <div class="chat-history-item is-terminal${starred ? ' is-starred' : ''}${openClass}"
                 data-session-id="${escapeHtml(String(session.id))}"
                 data-kind="terminal"
                 onclick="window.chatPageOpenTerminalSession('${sid}')">
                <div class="history-item-body">
                    ${meta}
                    <div class="chat-title">${escapeHtml(title)}</div>
                </div>
                ${createHistoryDeleteActionsHTML(session.id, { includeRename: true, starred, lastMessageMs: ctx.resolveHistoryLastMessageMs(session) })}
            </div>
        `;
        }

        return {
            createHistoryDeleteActionsHTML,
            createHistorySectionHeaderHTML,
            createAuthHistoryItemHTML,
            createHistoryItemHTML,
            createTerminalHistoryItemHTML,
        };
    }

    /** Normalize a grouped history entry to its session object. */
    function historyGroupEntrySessionObj(entry) {
        if (!entry) return null;
        return entry.session || entry.serverSession || null;
    }

    /** Inject a per-entry predicate to summarize a project group. */
    function historyGroupSomeEntry(group, lookup) {
        if (typeof lookup !== 'function') return false;
        return !!(group && group.entries && group.entries.some((entry) => (
            lookup(entry.sessionId, historyGroupEntrySessionObj(entry))
        )));
    }

    function historyGroupHasUnread(group, opts) {
        return historyGroupSomeEntry(group, opts && opts.hasUnread);
    }

    function historyGroupHasUnreadError(group, opts) {
        return historyGroupSomeEntry(group, opts && opts.hasUnreadError);
    }

    function historyGroupHasPausedFollowup(group, opts) {
        return historyGroupSomeEntry(group, opts && opts.hasPausedFollowup);
    }

    function historyGroupHasActiveFollowup(group, opts) {
        return historyGroupSomeEntry(group, opts && opts.hasActiveFollowup);
    }

    function historyGroupContainsCurrent(group, opts) {
        const current = opts && opts.currentSessionId;
        if (current == null || !group || !group.entries) return false;
        const eq = (opts && typeof opts.sessionIdsEqual === 'function')
            ? opts.sessionIdsEqual
            : function (a, b) { return String(a) === String(b); };
        return group.entries.some((entry) => eq(entry.sessionId, current));
    }

    /** Show-more/show-less control markup for a project group. */
    function historyGroupToggleHTML(groupKey, total, visible, escapeFn) {
        const esc = typeof escapeFn === 'function' ? escapeFn : (s) => String(s);
        const safeKey = esc(String(groupKey));
        if (total <= HISTORY_GROUP_PREVIEW_LIMIT) return '';
        if (visible >= total) {
            return `<button type="button" class="history-show-all-btn" data-group-key="${safeKey}" data-total="${total}" onclick="window.collapseHistoryGroupReveal(event)">Show less</button>`;
        }
        const step = Math.min(HISTORY_GROUP_PAGE_SIZE, total - visible);
        const label = step === 1 ? 'Show 1 more' : `Show ${step} more`;
        return `<button type="button" class="history-show-all-btn" data-group-key="${safeKey}" data-total="${total}" onclick="window.revealMoreHistoryGroup(event)">${esc(label)}</button>`;
    }

    function entryIsStarred(entry) {
        if (!entry) return false;
        if (entry.serverSession) return !!Number(entry.serverSession.starred);
        if (entry.session) return !!entry.session.starred;
        return false;
    }

    function historyEntryParentId(entry) {
        if (!entry) return '';
        const s = entry.serverSession || entry.session || {};
        const pid = s.parent_session_id;
        if (pid == null || pid === '') return '';
        return String(pid);
    }

    function historyLookupNestedEntry(nested, sessionId, opts) {
        if (!nested || !nested.byId || sessionId == null || sessionId === '') return null;
        const eq = (opts && typeof opts.sessionIdsEqual === 'function')
            ? opts.sessionIdsEqual
            : function (a, b) { return String(a) === String(b); };
        const toAuth = (opts && typeof opts.toAuthDbSessionId === 'function') ? opts.toAuthDbSessionId : null;
        const key = String(sessionId);
        if (nested.byId.has(key)) return nested.byId.get(key);
        const bare = toAuth ? toAuth(sessionId) : null;
        if (bare != null && nested.byId.has(String(bare))) return nested.byId.get(String(bare));
        for (const [id, entry] of nested.byId.entries()) {
            if (eq(id, sessionId)) return entry;
            if (String(id) === key) return entry;
        }
        return null;
    }

    function nestHistoryEntries(entries) {
        const list = Array.isArray(entries) ? entries.slice() : [];
        const byId = new Map();
        list.forEach((e) => {
            const id = String(e && e.sessionId != null ? e.sessionId : '');
            if (id) byId.set(id, e);
        });
        const childrenByParent = new Map();
        const roots = [];
        list.forEach((e) => {
            const pid = historyEntryParentId(e);
            if (pid && byId.has(pid)) {
                if (!childrenByParent.has(pid)) childrenByParent.set(pid, []);
                childrenByParent.get(pid).push(e);
            } else {
                roots.push(e);
            }
        });
        return { roots: roots, childrenByParent: childrenByParent, byId: byId };
    }

    function historySessionAncestorIds(sessionId, nested, opts) {
        const ids = [];
        let cur = sessionId;
        const seen = new Set();
        while (cur != null && cur !== '') {
            const key = String(cur);
            if (seen.has(key)) break;
            seen.add(key);
            const entry = historyLookupNestedEntry(nested, cur, opts);
            const pid = historyEntryParentId(entry);
            if (!pid) break;
            if (!historyLookupNestedEntry(nested, pid, opts)) break;
            ids.push(pid);
            cur = pid;
        }
        return ids;
    }

    function isHistorySubagentGroupExpanded(parentId, nested, opts) {
        opts = opts || {};
        const pid = String(parentId || '');
        if (!pid) return false;
        if (opts.searchActive) return true;
        const current = opts.currentSessionId;
        if (current != null && current !== '') {
            const eq = (typeof opts.sessionIdsEqual === 'function')
                ? opts.sessionIdsEqual
                : function (a, b) { return String(a) === String(b); };
            const ancestors = historySessionAncestorIds(current, nested, opts);
            for (let i = 0; i < ancestors.length; i++) {
                if (ancestors[i] === pid) return true;
                if (eq(ancestors[i], pid)) return true;
            }
        }
        const prefs = opts.prefs || {};
        return prefs[pid] === 'expanded';
    }

    function visibleHistoryRoots(nested, visibleLimit, opts) {
        const roots = (nested && Array.isArray(nested.roots)) ? nested.roots : [];
        const limit = Math.max(0, Number(visibleLimit) || 0);
        const visible = roots.slice(0, limit);
        const current = opts && opts.currentSessionId;
        if (current == null || current === '') return visible;
        const eq = (opts && typeof opts.sessionIdsEqual === 'function')
            ? opts.sessionIdsEqual
            : function (a, b) { return String(a) === String(b); };
        const ancestors = historySessionAncestorIds(current, nested, opts);
        const rootId = ancestors.length ? ancestors[ancestors.length - 1] : String(current);
        const matchesRoot = (e) => {
            const id = e && e.sessionId;
            if (id == null) return false;
            if (String(id) === String(rootId)) return true;
            return eq(id, rootId);
        };
        const already = visible.some(matchesRoot);
        if (already) return visible;
        const missing = roots.find(matchesRoot);
        return missing ? visible.concat([missing]) : visible;
    }

    function historySubagentNoun(count) {
        const n = Math.max(0, Number(count) || 0);
        return n === 1 ? 'sub-agent' : 'sub-agents';
    }

    function historySubagentToggleTitle(count, expanded, force) {
        const n = Math.max(0, Number(count) || 0);
        const noun = historySubagentNoun(n);
        const amount = n ? (n + ' ' + noun) : noun;
        if (force) return amount + ' (open because this chat is a sub-agent)';
        return (expanded ? 'Hide ' : 'Show ') + amount;
    }

    /**
     * Render a project group's nested tree and visible roots. Pure: the panel
     * injects the reveal limit resolver, the active-search flag, persisted
     * expansion prefs and session-id equality.
     * opts: { getVisibleLimit, searchActive, currentSessionId, prefs,
     *         sessionIdsEqual }
     */
    function renderHistoryGroupBody(group, renderEntry, opts) {
        opts = opts || {};
        const esc = typeof opts.escapeHtml === 'function' ? opts.escapeHtml : (s) => String(s);
        const key = group.project.key;
        const nested = nestHistoryEntries(group.entries);
        const total = nested.roots.length;
        const visibleLimit = typeof opts.getVisibleLimit === 'function'
            ? opts.getVisibleLimit(key, total)
            : Math.min(HISTORY_GROUP_PREVIEW_LIMIT, total);
        const visible = visibleHistoryRoots(nested, visibleLimit, {
            currentSessionId: opts.currentSessionId,
            sessionIdsEqual: opts.sessionIdsEqual,
        });
        const searchActive = !!opts.searchActive;
        const prefs = opts.prefs || {};

        function renderTree(entry) {
            const pid = String(entry && entry.sessionId != null ? entry.sessionId : '');
            const kids = nested.childrenByParent.get(pid) || [];
            const expanded = kids.length
                ? isHistorySubagentGroupExpanded(pid, nested, {
                    searchActive: searchActive,
                    currentSessionId: opts.currentSessionId,
                    prefs: prefs,
                    sessionIdsEqual: opts.sessionIdsEqual,
                })
                : false;
            const force = !!(kids.length && opts.currentSessionId != null
                && historySessionAncestorIds(opts.currentSessionId, nested, {
                    sessionIdsEqual: opts.sessionIdsEqual,
                }).some((id) => {
                    if (id === pid) return true;
                    return typeof opts.sessionIdsEqual === 'function'
                        ? opts.sessionIdsEqual(id, pid)
                        : String(id) === String(pid);
                }));
            const itemHtml = renderEntry(entry, {
                subagentChildCount: kids.length,
                subagentExpanded: expanded,
                subagentForceExpanded: force,
            });
            if (!kids.length) return itemHtml;
            const safePid = esc(pid);
            let html = '<div class="history-subagent-group'
                + (expanded ? '' : ' is-collapsed')
                + '" data-parent-session-id="' + safePid + '"'
                + (force ? ' data-force-expanded="1"' : '')
                + '>';
            html += itemHtml;
            html += '<div class="history-subagent-children" id="history-subagents-'
                + safePid + '-kids" role="group">';
            kids.forEach((child) => {
                html += renderTree(child);
            });
            html += '</div></div>';
            return html;
        }

        let html = '';
        visible.forEach((entry) => {
            html += renderTree(entry);
        });
        html += historyGroupToggleHTML(key, total, visibleLimit, opts.escapeHtml);
        return html;
    }

    /**
     * Group/order history entries by project, origin, star, match and recency.
     * opts: { resolveSessionProjectInfo, entryIsStarred }
     */
    function groupSessionsByProject(entries, opts) {
        opts = opts || {};
        const resolve = typeof opts.resolveSessionProjectInfo === 'function'
            ? opts.resolveSessionProjectInfo
            : function () { return { key: '__none__', id: null, name: 'No project', path: '' }; };
        const isStarred = typeof opts.entryIsStarred === 'function' ? opts.entryIsStarred : entryIsStarred;
        const groups = new Map();
        (entries || []).forEach((entry) => {
            const origin = String(
                (entry.serverSession && entry.serverSession.origin)
                || (entry.session && entry.session.origin)
                || ''
            ).trim().toLowerCase();
            const proj = (origin === 'discord_dm' || origin === 'discord_guild')
                ? { key: '__discord__', id: null, name: 'Discord', path: '' }
                // Prefer the entry's own row (server rows carry project
                // fields): passing only entry.session (null for server
                // entries) forced a full-list scan per chat — O(n²) paints.
                : resolve(entry.sessionId, historyGroupEntrySessionObj(entry));
            if (!groups.has(proj.key)) {
                groups.set(proj.key, { project: proj, entries: [], latest: 0 });
            }
            const g = groups.get(proj.key);
            g.entries.push(entry);
            const u = Number(entry.updated) || 0;
            if (u > g.latest) g.latest = u;
        });
        const list = Array.from(groups.values());
        // Most recently active project first; "No project" stays last.
        list.sort((a, b) => {
            if (a.project.key === '__none__') return 1;
            if (b.project.key === '__none__') return -1;
            const d = (b.latest || 0) - (a.latest || 0);
            if (d !== 0) return d;
            return String(a.project.name).localeCompare(String(b.project.name), undefined, {
                sensitivity: 'base',
            });
        });
        list.forEach((g) => {
            g.entries.sort((a, b) => {
                // Starred favorites float to the top of each project category.
                const aStarred = isStarred(a);
                const bStarred = isStarred(b);
                if (aStarred && !bStarred) return -1;
                if (bStarred && !aStarred) return 1;
                // Search: title matches before content matches, even if content is newer.
                const aMatch = entrySearchMatchRank(a);
                const bMatch = entrySearchMatchRank(b);
                if (aMatch !== bMatch) return aMatch - bMatch;
                // Opening a chat must not move it: order by last message only.
                const d = (b.updated || 0) - (a.updated || 0);
                if (d !== 0) return d;
                // Stable tie-break so equal timestamps don't reshuffle on refresh.
                return String(b.sessionId).localeCompare(String(a.sessionId), undefined, {
                    numeric: true,
                });
            });
        });
        return list;
    }

    root.CuttleChatHistoryView = {
        HISTORY_GROUP_PREVIEW_LIMIT,
        HISTORY_GROUP_PAGE_SIZE,
        paint,
        preserveScroll,
        entrySearchMatchRank,
        historyActionIcons,
        historyStarredIconHTML,
        historyItemMetaHTML,
        createRenderers,
        historyGroupEntrySessionObj,
        historyGroupHasUnread,
        historyGroupHasUnreadError,
        historyGroupHasPausedFollowup,
        historyGroupHasActiveFollowup,
        historyGroupContainsCurrent,
        historyGroupToggleHTML,
        entryIsStarred,
        historyEntryParentId,
        historyLookupNestedEntry,
        nestHistoryEntries,
        historySessionAncestorIds,
        isHistorySubagentGroupExpanded,
        visibleHistoryRoots,
        historySubagentNoun,
        historySubagentToggleTitle,
        renderHistoryGroupBody,
        groupSessionsByProject,
    };
})(globalThis);
