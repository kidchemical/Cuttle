/* ================================================================
   Cuttle Chat — messages/history record domain (chat_messages.js).
   Owner: message-record normalization, history-window decisions,
   and user-bubble display dispatch as pure functions over explicit
   inputs: no document, no window, no localStorage, no fetch here.
   Loaded before chat_page.js; the page owns transcript DOM paint,
   message sync/poll machinery, persistence, session restore +
   ordering, search/history-panel UI, prompt history, streaming and
   live-status orchestration, leaf renderers (markdown, attachment
   HTML, form-reply/button, slash chips), and calls into
   `CuttleChatMessages.*`.

   Cross-domain rule: attachment list shape stays in
   `CuttleChatAttachments` (injected as `normalizeAttachmentList`,
   never duplicated); project resolution stays in `CuttleChatProject`
   (injected as `projectFromMessageOpts`); send dispatch stays in
   `CuttleChatComposer`; agent/model selection stays in
   `CuttleChatAgentModel`. Timestamp parsing and usage shaping are
   page-owned helpers, injected as callbacks.

   Mirrors the page contract: nested metadata wins with legacy flat
   fallbacks, server-paged payloads paint as-is, unpaged dumps keep
   only the newest page on screen, project stamps fill backward from
   the newest stamped row, and system rows never count as visible.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Canonical per-record view options for one server message.
     * `deps`: { parseSessionTimestamp, normalizeAttachmentList,
     * normalizeUsagePayload } — page-owned helpers.
     */
    function authMessageOptsFromServer(msg, deps) {
        const d = deps || {};
        const parseTs = typeof d.parseSessionTimestamp === 'function'
            ? d.parseSessionTimestamp : (v) => v;
        const normAtts = typeof d.normalizeAttachmentList === 'function'
            ? d.normalizeAttachmentList : (l) => (Array.isArray(l) ? l : []);
        const normUsage = typeof d.normalizeUsagePayload === 'function'
            ? d.normalizeUsagePayload : (u) => u;
        const m = msg || {};
        let meta = m.metadata;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (_) { meta = {}; }
        }
        if (!meta || typeof meta !== 'object') meta = {};
        const reportUrl = m.report_url || meta.report_url || (
            (m.query_id || meta.query_id)
                ? `/query_log.html?id=${m.query_id || meta.query_id}`
                : null
        );
        // SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS". Raw Date.parse
        // treats that as local time, so in US timezones messages look hours in
        // the future and formatTimeAgo always shows "Just now".
        const parsed = parseTs(m.timestamp);
        const attachments = normAtts(
            m.attachments || meta.attachments || []
        );
        return {
            message_id: m.id,
            timestamp: parsed > 0 ? parsed : Date.now(),
            report_url: reportUrl,
            query_id: m.query_id || meta.query_id || undefined,
            slash_command: m.slash_command || meta.slash_command,
            slash_command_failed: !!(m.slash_command_failed || meta.slash_command_failed),
            cursor_run: meta.cursor_run || m.cursor_run || undefined,
            usage: normUsage(meta.usage || m.usage) || undefined,
            user_feedback: meta.user_feedback || undefined,
            attachments: attachments.length ? attachments : undefined,
            project_id: m.project_id != null ? m.project_id : (meta.project_id != null ? meta.project_id : (m._project && m._project.id)),
            project_name: m.project_name || meta.project_name || (m._project && m._project.name),
            project_path: m.project_path || meta.project_path || (m._project && m._project.path),
            speaker: meta.speaker || m.speaker || undefined,
            speaker_kind: meta.speaker_kind || m.speaker_kind || undefined,
            avatar: meta.avatar || m.avatar || undefined,
            parent_session_id: meta.parent_session_id != null ? meta.parent_session_id : m.parent_session_id,
            parent_handle: meta.parent_handle || m.parent_handle || undefined,
            parent_label: meta.parent_label || m.parent_label || undefined,
            origin: meta.origin || m.origin || undefined,
            control_request_id: meta.control_request_id || m.control_request_id || undefined,
            supervised_task_id: meta.supervised_task_id || undefined,
            supervised_phase: meta.supervised_phase || undefined,
            supervised_terminal: !!meta.supervised_terminal,
            supervised_delivery_event_id: meta.supervised_delivery_event_id || undefined,
            parent_user_message_id: meta.parent_user_message_id || undefined,
            coordinator_response_message_id: meta.coordinator_response_message_id || undefined,
            kind: meta.kind || m.kind || undefined,
            subagents: meta.subagents || m.subagents || undefined,
            subagent_batch_id: meta.subagent_batch_id || undefined,
        };
    }

    /** Pull the latest requested Cursor model from assistant message metadata. */
    function preferredModelFromMessages(messages) {
        if (!Array.isArray(messages)) return null;
        for (let i = messages.length - 1; i >= 0; i--) {
            const m = messages[i];
            if (!m || m.role !== 'assistant') continue;
            let meta = m.metadata;
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (_) { meta = null; }
            }
            if (!meta || typeof meta !== 'object') continue;
            const cr = meta.cursor_run;
            const req = cr && (cr.requested_model || '').trim();
            if (req) return req;
        }
        return null;
    }

    /**
     * Project stamp for one record: explicit `_project` wins, else
     * resolve via the injected project-domain callback over this
     * module's own record options.
     * `deps`: auth-opts deps + { projectFromMessageOpts, projects,
     * currentProject }.
     */
    function projectFromMessageRecord(msg, deps) {
        const d = deps || {};
        if (!msg) return null;
        if (msg._project) return msg._project;
        const projectFromMessageOpts = typeof d.projectFromMessageOpts === 'function'
            ? d.projectFromMessageOpts : () => null;
        return projectFromMessageOpts(
            authMessageOptsFromServer(msg, d),
            d.projects,
            d.currentProject
        );
    }

    /**
     * Stamp `_project` onto a loaded transcript in place: rows walk
     * newest-first, and an unstamped row inherits the nearest newer
     * stamp (a user turn belongs to the project of the reply below
     * it). Non-array input is a no-op (page passes through).
     */
    function annotateMessageProjects(messages, deps) {
        if (!Array.isArray(messages)) return;
        let pending = null;
        for (let i = messages.length - 1; i >= 0; i--) {
            const found = projectFromMessageRecord(messages[i], deps);
            if (found) {
                pending = found;
                messages[i]._project = found;
            } else if (pending) {
                messages[i]._project = pending;
            }
        }
    }

    /** Visible bubble count: system rows never paint as bubbles. */
    function countVisibleChatMessages(messages) {
        if (!Array.isArray(messages)) return 0;
        let n = 0;
        for (const m of messages) {
            if (m && m.role !== 'system') n += 1;
        }
        return n;
    }

    function finiteOldestId(message) {
        const first = message || {};
        const oid = first.id != null ? Number(first.id) : NaN;
        return Number.isFinite(oid) ? oid : undefined;
    }

    /**
     * Standalone page-meta resolution: only defined fields apply —
     * an absent `older_visible_count` or an empty message list keeps
     * the page's current values (the page applies, never this).
     */
    function historyPageMeta(parts) {
        const p = parts || {};
        const out = {};
        if (typeof p.hasMore === 'boolean') out.hasMore = p.hasMore;
        if (p.olderVisibleCount != null) {
            out.olderVisibleCount = Math.max(0, Number(p.olderVisibleCount) || 0);
        }
        if (Array.isArray(p.messages) && p.messages.length) {
            const oid = finiteOldestId(p.messages[0]);
            if (oid !== undefined) out.oldestId = oid;
        }
        return out;
    }

    /**
     * Window an unpaged full dump: paint the newest page, buffer the
     * rest for scroll-up. Server-paged payloads (`hasMore` boolean,
     * even when false) paint as-is with a cleared buffer.
     * `parts`: { messages, hasMore, olderVisibleCount, pageSize }.
     * `oldestId` is undefined when the page must keep its current
     * value (empty dump) and null only where the page nulls it.
     */
    function windowHistoryMessages(parts) {
        const p = parts || {};
        const messages = Array.isArray(p.messages) ? p.messages : [];
        const pageSize = Number(p.pageSize) > 0 ? Number(p.pageSize) : 10;
        // Server already paged (has_more present, even when false).
        if (typeof p.hasMore === 'boolean') {
            const meta = historyPageMeta({
                hasMore: p.hasMore,
                olderVisibleCount: p.olderVisibleCount,
                messages,
            });
            return {
                paint: messages,
                buffer: [],
                hasMore: meta.hasMore,
                olderVisibleCount: meta.olderVisibleCount,
                oldestId: meta.oldestId,
            };
        }
        if (messages.length <= pageSize) {
            return {
                paint: messages,
                buffer: [],
                hasMore: false,
                olderVisibleCount: 0,
                oldestId: messages.length ? finiteOldestId(messages[0]) : undefined,
            };
        }
        const buffer = messages.slice(0, -pageSize);
        const paint = messages.slice(-pageSize);
        const oid = paint.length ? Number(paint[0] && paint[0].id) : NaN;
        return {
            paint,
            buffer,
            hasMore: buffer.length > 0,
            olderVisibleCount: countVisibleChatMessages(buffer),
            oldestId: Number.isFinite(oid) ? oid : null,
        };
    }

    /** Server transcript ends on an assistant turn → live-status reads idle. */
    function transcriptEndsWithAssistant(messages) {
        if (!Array.isArray(messages) || !messages.length) return false;
        const last = messages[messages.length - 1];
        return !!(last && last.role === 'assistant');
    }

    /**
     * User-bubble display dispatch: which body HTML a user turn paints.
     * Owns the branch order only — every leaf renderer stays in its
     * owner and arrives via `deps` (all required):
     * - attachments owned (`CuttleChatAttachments` via the page's
     *   adapters): normalizeAttachmentList, stripAttachedNote,
     *   inferAttachmentsFromContent.
     * - action-form leaves (page): parseButtonClickFromContent,
     *   formatFormReplyHtml.
     * - attachment HTML leaf (page): buildMessageAttachmentsHtml.
     * - markdown leaf (page, also paints assistant turns):
     *   formatMessage.
     * - slash parse (page adapter over `CuttleChatSlash` with
     *   pipeline/model resolvers): parseStoredSlashCommandMessage.
     * - slash chip leaves (page, shared with badge/palette paths):
     *   collapseCursorSlashChips, slashCommandChipHistoryHtml.
     * - shared util (page): escapeHtmlInline.
     *
     * Preserves the page contract exactly: `Selected:` / button /
     * form-reply shortcuts win over slash parsing; chips already in
     * the header (`suppressInlineSlashChips`) render body only; the
     * attachment-only placeholder hides when thumbs show; otherwise
     * body + thumbs compose, and bare text falls back to markdown.
     */
    function formatUserMessageForDisplay(content, opts, deps) {
        const d = deps || {};
        const o = opts || {};
        const normalizeAttachmentList = d.normalizeAttachmentList;
        const stripAttachedNote = d.stripAttachedNote;
        const inferAttachmentsFromContent = d.inferAttachmentsFromContent;
        const buildMessageAttachmentsHtml = d.buildMessageAttachmentsHtml;
        const parseButtonClickFromContent = d.parseButtonClickFromContent;
        const formatFormReplyHtml = d.formatFormReplyHtml;
        const formatMessage = d.formatMessage;
        const parseStoredSlashCommandMessage = d.parseStoredSlashCommandMessage;
        const collapseCursorSlashChips = d.collapseCursorSlashChips;
        const slashCommandChipHistoryHtml = d.slashCommandChipHistoryHtml;
        const escapeHtmlInline = d.escapeHtmlInline;
        let attachments = normalizeAttachmentList(o.attachments);
        if (!attachments.length) {
            attachments = inferAttachmentsFromContent(content);
        }
        const attHtml = buildMessageAttachmentsHtml(attachments);
        let displayContent = attachments.length ? stripAttachedNote(content) : content;
        // Attachment-only send used this placeholder outbound; hide it when thumbs show.
        if (attHtml && (!displayContent || displayContent === '(see attached files)')) {
            displayContent = '';
        }

        let bodyHtml = '';
        if (displayContent) {
            const sel = String(displayContent ?? '').trim().match(/^Selected:\s*(.+)$/i);
            if (sel) {
                bodyHtml = `<div class="cuttle-button-selection">Selected: <strong>${escapeHtmlInline(sel[1].trim())}</strong></div>`;
            } else {
                const btnClick = parseButtonClickFromContent(displayContent);
                if (btnClick) {
                    bodyHtml = `<div class="cuttle-button-selection">Selected: <strong>${escapeHtmlInline(btnClick.label)}</strong></div>`;
                } else {
                    const renderBody = (text) => formatFormReplyHtml(text) || formatMessage(text);
                    const parsed = parseStoredSlashCommandMessage(displayContent);
                    if (!parsed) {
                        bodyHtml = renderBody(displayContent);
                    } else if (o.suppressInlineSlashChips) {
                        // Chips already shown in the message header row; just render the body.
                        bodyHtml = parsed.body ? renderBody(parsed.body) : '';
                    } else {
                        const chipsHtml = collapseCursorSlashChips(parsed.chips)
                            .map((c) => slashCommandChipHistoryHtml(c.label, c.meta, c.category))
                            .join('');
                        const inline = '<span class="slash-chips-inline">' + chipsHtml + '</span>';
                        if (!parsed.body) {
                            bodyHtml = '<div class="user-message-with-slash">' + inline + '</div>';
                        } else {
                            bodyHtml = (
                                '<div class="user-message-with-slash">'
                                + inline
                                + '<div class="user-slash-body">'
                                + renderBody(parsed.body)
                                + '</div></div>'
                            );
                        }
                    }
                }
            }
        }

        if (!attHtml) return bodyHtml || formatMessage(content);
        if (!bodyHtml) return attHtml;
        return '<div class="user-message-with-attachments">' + bodyHtml + attHtml + '</div>';
    }

    const api = {
        authMessageOptsFromServer,
        preferredModelFromMessages,
        projectFromMessageRecord,
        annotateMessageProjects,
        countVisibleChatMessages,
        historyPageMeta,
        windowHistoryMessages,
        transcriptEndsWithAssistant,
        formatUserMessageForDisplay,
    };

    const ns = (root.CuttleChatMessages = root.CuttleChatMessages || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
