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
            steered: !!meta.steered,
            steered_agent: meta.steered_agent || m.steered_agent || undefined,
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

    /**
     * Inline-code/link shaping inside one reasoning line. Needs the
     * link-chip leaf (`renderMdLinkChip`) and the shared escaper.
     */
    function formatThinkingLineInline(raw, deps) {
        const d = deps || {};
        const chips = [];
        const codes = [];
        let t = String(raw ?? '');
        // Markdown links → chips (capture raw url/label before escaping).
        t = t.replace(/\[([^\]]*)]\(([^)]+)\)/g, function (_, label, url) {
            const ph = '\u0001L' + chips.length + '\u0001';
            chips.push(d.renderMdLinkChip(label, String(url || '').trim()));
            return ph;
        });
        // Inline code → placeholders so backticks don't render literally.
        t = t.replace(/`([^`]+)`/g, function (_, code) {
            const ph = '\u0001C' + codes.length + '\u0001';
            codes.push('<code>' + d.escapeHtmlInline(code) + '</code>');
            return ph;
        });
        t = d.escapeHtmlInline(t);
        t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        t = t.replace(/\u0001C(\d+)\u0001/g, function (_, i) { return codes[Number(i)] || ''; });
        t = t.replace(/\u0001L(\d+)\u0001/g, function (_, i) { return chips[Number(i)] || ''; });
        return t;
    }

    /**
     * Vega chart placeholder wrap (planning half; the DOM embed half
     * stays in the page's `activateVegaEmbeds`, which consumes the
     * `data-vega-spec` attribute produced here).
     */
    function buildVegaWrapHtml(rawSpec, deps) {
        const d = deps || {};
        const raw = String(rawSpec ?? '').trim();
        if (!raw) {
            return '<div class="vega-wrap vega-wrap--error">Empty Vega chart</div>';
        }
        // No ui-card chrome — charts sit flush in the bubble (transparent bg).
        return (
            '<div class="vega-wrap" data-vega-spec="' +
            d.escapeHtmlInline(raw) +
            '"><div class="vega-pending" aria-hidden="true">Loading chart…</div></div>'
        );
    }

    /**
     * Head structured-block extraction (think + tool_output). Runs at
     * the pipeline position before supervised-activity extraction, so
     * later stages never see raw think/tool markup.
     * Returns { text, blocks: { think, tool } }.
     */
    function extractHeadStructuredBlocks(text, deps) {
        const d = deps || {};
        let out = String(text || '');
        const thinkBlocks = [];
        const pushThinkBlock = (content) => {
            const placeholder = '{{CUTTLE_THINK_' + thinkBlocks.length + '}}';
            thinkBlocks.push(
                '<details class="thinking-block">'
                + '<summary>Progress</summary>'
                + formatThinkingInner(content, d)
                + '</details>'
            );
            return placeholder;
        };
        // Match <think>/<redacted_thinking> tags, but ignore any that live inside
        // fenced or inline code. Reasoning that quotes those tags (e.g. code
        // samples containing "</think>") used to truncate the block and spill the
        // rest of the reasoning — plus a stray closing tag — into the message body.
        (function extractThinkTags() {
            const protectedRanges = [];
            const addRanges = (re) => {
                let mm;
                while ((mm = re.exec(out)) !== null) {
                    const s = mm.index;
                    const e = s + mm[0].length;
                    if (!protectedRanges.some((r) => s >= r[0] && e <= r[1])) {
                        protectedRanges.push([s, e]);
                    }
                    if (mm.index === re.lastIndex) re.lastIndex++;
                }
            };
            addRanges(/```[^\n`]*\r?\n[\s\S]*?```/g);
            addRanges(/`[^`\n]+`/g);
            const inProtected = (pos) =>
                protectedRanges.some((r) => pos >= r[0] && pos < r[1]);

            const tagRe = /<(\/?)(think|redacted_thinking)>/g;
            const tokens = [];
            let mm;
            while ((mm = tagRe.exec(out)) !== null) {
                if (inProtected(mm.index)) continue;
                tokens.push({
                    close: mm[1] === '/',
                    name: mm[2],
                    start: mm.index,
                    end: mm.index + mm[0].length,
                });
            }

            const segments = [];
            let idx = 0;
            while (idx < tokens.length) {
                const open = tokens[idx];
                if (open.close) { idx++; continue; }
                let j = idx + 1;
                while (
                    j < tokens.length
                    && !(tokens[j].close && tokens[j].name === open.name)
                ) {
                    j++;
                }
                if (j >= tokens.length) break;
                segments.push({
                    start: open.start,
                    end: tokens[j].end,
                    content: out.slice(open.end, tokens[j].start),
                });
                idx = j + 1;
            }
            if (!segments.length) return;

            let rebuilt = '';
            let cursor = 0;
            for (const seg of segments) {
                rebuilt += out.slice(cursor, seg.start);
                rebuilt += pushThinkBlock(seg.content);
                cursor = seg.end;
            }
            rebuilt += out.slice(cursor);
            out = rebuilt;
        })();

        // Extract <tool_output> blocks (rendered as collapsible)
        const toolBlocks = [];
        out = out.replace(/<tool_output>([\s\S]*?)<\/tool_output>/g, function(_, content) {
            const escaped = d.escapeHtmlInline(content.trim());
            const placeholder = '{{CUTTLE_TOOL_' + toolBlocks.length + '}}';
            toolBlocks.push('<details class="tool-block"><summary>🧰 Tool output</summary><pre class="tool-content"><code>' + escaped + '</code></pre></details>');
            return placeholder;
        });
        return { text: out, blocks: { think: thinkBlocks, tool: toolBlocks } };
    }

    /**
     * Tail structured-block extraction (trace, progress, meters,
     * pricing, terminal, media, vega). Runs after supervised-activity
     * extraction, preserving the pipeline's original order: supervised
     * markup is placeholder-replaced before these patterns run, so
     * live-activity JSON can never match a block pattern here.
     * Returns { text, blocks } with one array per prefix below.
     */
    function extractTailStructuredBlocks(text, deps) {
        const d = deps || {};
        let out = String(text || '');
        // Query trace appendix (what actually ran; from _build_cuttle_trace_block)
        const traceBlocks = [];
        out = out.replace(/<cuttle_trace>([\s\S]*?)<\/cuttle_trace>/g, function(_, content) {
            const escaped = d.escapeHtmlInline(content.trim());
            const placeholder = '{{CUTTLE_TRACE_' + traceBlocks.length + '}}';
            traceBlocks.push(
                '<details class="pipeline-trace-block">'
                + '<summary>Query trace (what actually ran)</summary>'
                + '<pre class="trace-content"><code>' + escaped + '</code></pre>'
                + '</details>'
            );
            return placeholder;
        });

        // Extract <progress .../> (stream-friendly)
        const progressBlocks = [];
        out = out.replace(/<progress\s+([^>]*?)\/>/g, function(_, attrs) {
            const idMatch = String(attrs).match(/\bid\s*=\s*["']([^"']+)["']/i);
            const labelMatch = String(attrs).match(/\blabel\s*=\s*["']([^"']+)["']/i);
            const valueMatch = String(attrs).match(/\bvalue\s*=\s*["']([^"']+)["']/i);
            const id = d.escapeHtmlInline(idMatch ? idMatch[1] : ('p_' + progressBlocks.length));
            const label = d.escapeHtmlInline(labelMatch ? labelMatch[1] : 'Progress');
            const value = d.clamp(valueMatch ? valueMatch[1] : 0, 0, 100);
            const placeholder = '{{CUTTLE_PROGRESS_' + progressBlocks.length + '}}';
            progressBlocks.push(
                '<div class="ui-card" data-progress-id="' + id + '">'
                + '<div class="ui-title">' + label + '</div>'
                + '<div class="progress-row">'
                + '<div class="progress-track"><div class="progress-bar" style="width:' + value + '%"></div></div>'
                + '<div class="progress-value">' + value + '%</div>'
                + '</div></div>'
            );
            return placeholder;
        });

        // Compact multi-row meters (e.g. Cursor /usage) — no card chrome.
        const metersBlocks = [];
        out = out.replace(/<cuttle_meters>([\s\S]*?)<\/cuttle_meters>/gi, function(_, rawBody) {
            const placeholder = '{{CUTTLE_METERS_' + metersBlocks.length + '}}';
            let rows = [];
            let variant = '';
            try {
                const parsed = JSON.parse(String(rawBody || '').trim());
                if (Array.isArray(parsed)) rows = parsed;
                else if (parsed && typeof parsed === 'object') {
                    variant = String(parsed.variant || '');
                    rows = Array.isArray(parsed.rows) ? parsed.rows : [];
                }
            } catch (_) {
                rows = [];
            }
            const fmtPct = (p) => {
                const n = Number(p);
                if (!Number.isFinite(n)) return '—';
                if (Math.abs(n - Math.round(n)) < 0.05) return String(Math.round(n)) + '%';
                return n.toFixed(1).replace(/\.0$/, '') + '%';
            };
            const rowHtml = rows.map((row) => {
                if (!row || typeof row !== 'object') return '';
                let labelText = String(row.label || '').trim() || '—';
                let tooltip = typeof row.tooltip === 'string' ? row.tooltip.trim() : '';
                // Persisted reports from before the tooltip field was introduced.
                const legacyReset = labelText.match(/^(5-hour|Weekly) \((resets .+)\)$/i);
                if (legacyReset) {
                    labelText = legacyReset[1];
                    if (!tooltip) tooltip = legacyReset[2];
                }
                if (row.tooltip_at != null) {
                    const date = new Date(Number(row.tooltip_at) * 1000);
                    if (Number.isFinite(date.getTime())) {
                        tooltip = (tooltip || 'Resets') + ' ' + date.toLocaleString(undefined, {timeZoneName: 'short'});
                    }
                } else if (/^resets .+ UTC$/i.test(tooltip)) {
                    // Old persisted/cached reports also follow the viewer's timezone.
                    const date = new Date(tooltip.replace(/^resets /i, ''));
                    if (Number.isFinite(date.getTime())) tooltip = 'Resets ' + date.toLocaleString(undefined, {timeZoneName: 'short'});
                }
                const label = d.escapeHtmlInline(labelText);
                const info = tooltip ? '<button type="button" class="cuttle-info" data-tooltip="'
                    + d.escapeHtmlInline(tooltip) + '" aria-label="'
                    + d.escapeHtmlInline(labelText + ': ' + tooltip)
                    + '"><span aria-hidden="true">i</span></button>' : '';
                const disabled = !!row.disabled;
                const status = row.status != null ? String(row.status).trim() : '';
                const pctNum = d.clamp(row.pct != null ? row.pct : 0, 0, 100);
                const pctLabel = disabled && status
                    ? d.escapeHtmlInline(status)
                    : d.escapeHtmlInline(fmtPct(pctNum));
                const fillW = disabled ? 0 : pctNum;
                const disClass = disabled ? ' cuttle-meter-row--disabled' : '';
                return (
                    '<div class="cuttle-meter-row' + disClass + '">'
                    + '<span class="cuttle-meter-label">' + label + info + '</span>'
                    + '<span class="cuttle-meter-pct">' + pctLabel + '</span>'
                    + '<div class="cuttle-meter-track" aria-hidden="true">'
                    + '<div class="cuttle-meter-fill" style="width:' + fillW + '%"></div>'
                    + '</div>'
                    + '</div>'
                );
            }).join('');
            const varClass = variant
                ? ' cuttle-meters--' + d.escapeHtmlInline(variant.replace(/[^a-z0-9_-]/gi, ''))
                : '';
            metersBlocks.push(
                '<div class="cuttle-meters' + varClass + '" role="group">'
                + rowHtml
                + '</div>'
            );
            return placeholder;
        });

        // Per-model price table (harness ``/cost``) — active model row highlighted.
        const pricingBlocks = [];
        out = out.replace(/<cuttle_pricing>([\s\S]*?)<\/cuttle_pricing>/gi, function(_, rawBody) {
            const placeholder = '{{CUTTLE_PRICING_' + pricingBlocks.length + '}}';
            pricingBlocks.push(d.renderCuttlePricingHtml(rawBody));
            return placeholder;
        });

        // Extract <terminal ...>...</terminal>
        const terminalBlocks = [];
        out = out.replace(/<terminal([^>]*)>([\s\S]*?)<\/terminal>/g, function(_, attrs, content) {
            const idMatch = String(attrs).match(/\bid\s*=\s*["']([^"']+)["']/i);
            const titleMatch = String(attrs).match(/\btitle\s*=\s*["']([^"']+)["']/i);
            const lockedMatch = String(attrs).match(/\blocked\s*=\s*["']?(true|false)["']?/i);
            const interactiveMatch = String(attrs).match(/\binteractive\s*=\s*["']?(true|false)["']?/i);
            const id = d.escapeHtmlInline(idMatch ? idMatch[1] : ('t_' + terminalBlocks.length));
            const title = d.escapeHtmlInline(titleMatch ? titleMatch[1] : 'Console');
            const locked = (lockedMatch ? lockedMatch[1] : 'false').toLowerCase() === 'true';
            const interactive = (interactiveMatch ? interactiveMatch[1] : 'false').toLowerCase() === 'true';
            const body = d.escapeHtmlInline(String(content ?? '').trim());
            const placeholder = '{{CUTTLE_TERM_' + terminalBlocks.length + '}}';
            terminalBlocks.push(
                '<div class="ui-card terminal" data-terminal-id="' + id + '" data-interactive="' + (interactive && !locked ? 'true' : 'false') + '">'
                + '<div class="terminal-header">'
                + '<div class="terminal-title">' + title + '</div>'
                + '<div class="terminal-badge">' + (locked ? 'Locked' : (interactive ? 'Interactive' : 'Read-only')) + '</div>'
                + '</div>'
                + '<div class="terminal-body">' + body + '</div>'
                + (interactive && !locked
                    ? '<div class="terminal-input-row">'
                      + '<input data-terminal-input type="text" placeholder="Type a command…">'
                      + '<button data-terminal-send title="Send">↵</button>'
                      + '</div>'
                    : '')
                + '</div>'
            );
            return placeholder;
        });

        // Extract <media>...</media> and <media .../> (image/video/audio)
        const mediaBlocks = [];
        const parseMediaAttr = (attrsStr, attrName) => {
            const m = String(attrsStr || '').match(
                new RegExp('\\b' + attrName + '\\s*=\\s*["\']([^"\']*)["\']', 'i')
            );
            return m ? m[1] : '';
        };
        const pushMediaCard = (attrs, bodyText) => {
            const type = (parseMediaAttr(attrs, 'type') || 'image').toLowerCase();
            const src = String(parseMediaAttr(attrs, 'src') || '').trim();
            const title = parseMediaAttr(attrs, 'title');
            const description = String(
                parseMediaAttr(attrs, 'description')
                || parseMediaAttr(attrs, 'desc')
                || bodyText
                || ''
            ).trim();
            if (!src) return '';
            const placeholder = '{{CUTTLE_MEDIA_' + mediaBlocks.length + '}}';
            let inner = '';
            if (type === 'audio') {
                inner =
                    '<audio controls src="' +
                    d.escapeHtmlInline(src) +
                    '"></audio>';
                mediaBlocks.push(
                    '<div class="ui-card media-wrap">' +
                    (title ? '<div class="ui-title">' + d.escapeHtmlInline(title) + '</div>' : '') +
                    (description && description !== title
                        ? '<div class="cuttle-media-caption cuttle-media-caption--block">' +
                          d.escapeHtmlInline(description) +
                          '</div>'
                        : '') +
                    inner +
                    '</div>'
                );
            } else {
                const kind = type === 'video' ? 'video' : d.mediaKindFromUrl(src);
                mediaBlocks.push(
                    '<div class="ui-card media-wrap">' +
                    (title ? '<div class="ui-title">' + d.escapeHtmlInline(title) + '</div>' : '') +
                    d.buildMediaThumbHtml(src, {
                        title: title,
                        kind: kind,
                        description: description,
                    }) +
                    '</div>'
                );
            }
            return placeholder;
        };
        out = out.replace(/<media\s+([^>]*?)>([\s\S]*?)<\/media>/gi, function(_, attrs, body) {
            return pushMediaCard(attrs, String(body || '').trim());
        });
        out = out.replace(/<media\s+([^>]*?)\/>/gi, function(_, attrs) {
            return pushMediaCard(attrs, '');
        });

        // Extract <vega>...</vega> JSON spec + ```vega / ```vega-lite fences
        const vegaBlocks = [];
        const pushVega = (rawContent) => {
            const placeholder = '{{CUTTLE_VEGA_' + vegaBlocks.length + '}}';
            vegaBlocks.push(buildVegaWrapHtml(rawContent, d));
            return placeholder;
        };
        out = out.replace(/<vega>([\s\S]*?)<\/vega>/gi, function(_, content) {
            return pushVega(content);
        });
        out = out.replace(
            /```(?:vega(?:-lite)?|vegalite)\s*\r?\n([\s\S]*?)```/gi,
            function(_, content) {
                return pushVega(content);
            }
        );
        return {
            text: out,
            blocks: {
                trace: traceBlocks,
                progress: progressBlocks,
                meters: metersBlocks,
                pricing: pricingBlocks,
                terminal: terminalBlocks,
                media: mediaBlocks,
                vega: vegaBlocks,
            },
        };
    }

    /**
     * Fenced-code + markdown/bare-URL link extraction. Runs after the
     * form/button stages and before chat-handle linking, so extracted
     * code never yields links and link chips are in place for the
     * handle pass. Returns { text, blocks: { code, link } }.
     */
    function extractCodeLinkBlocks(text, deps) {
        const d = deps || {};
        text = String(text || '');
            // Fenced code blocks BEFORE escapeHtml + line splitting. Doing this after
            // escape (old path) left raw <pre><code>… across newlines; the line loop
            // shredded them into <p> tags and highlight.js warned about unescaped HTML.
            const codeBlocks = [];
            // Allow ```, ```lang, or ``` lang — require a newline after the opener.
            text = text.replace(/```([^\n`]*)\r?\n([\s\S]*?)```/g, function(_, langRaw, code) {
                const lang = String(langRaw || '').trim().split(/\s+/)[0] || '';
                const safeLang = /^[a-zA-Z0-9_+#.-]+$/.test(lang) ? lang : '';
                const safe = d.escapeHtmlInline(String(code ?? '').replace(/\n$/, ''));
                const cls = safeLang ? (' class="language-' + d.escapeHtmlInline(safeLang) + '"') : '';
                const placeholder = '{{CUTTLE_CODE_' + codeBlocks.length + '}}';
                codeBlocks.push('<pre class="message-code-block"><code' + cls + '>' + safe + '</code></pre>');
                return placeholder;
            });

            // Markdown links → chips (file://, vscode://, http(s)://, local Windows paths)
            // Allow spaces in destinations (CommonMark <url> or bare paths with spaces).
            const linkChips = [];
            text = text.replace(/\[([^\]]*)]\(([^)]+)\)/g, function(_, label, url) {
                const placeholder = '{{CUTTLE_LINK_' + linkChips.length + '}}';
                linkChips.push(d.renderMdLinkChip(label, String(url || '').trim()));
                return placeholder;
            });
            // Bare deep links / URLs (skip ones already turned into chips)
            text = text.replace(
                /(^|[\s(\[{·])((?:https?:\/\/|file:\/\/\/|vscode:\/\/|cursor:\/\/)[^\s<>\]"'`]+)/g,
                function(_, lead, url) {
                    let u = url;
                    let trail = '';
                    const m = u.match(/^(.*?)([.,;:!?)}\]]+)$/);
                    if (m) {
                        u = m[1];
                        trail = m[2];
                    }
                    if (!d.isSafeMdHref(u)) return lead + url;
                    const placeholder = '{{CUTTLE_LINK_' + linkChips.length + '}}';
                    linkChips.push(d.renderMdLinkChip(u, u));
                    return lead + placeholder + trail;
                }
            );

            // Bare chat handles: CH-000431 / CH-000431-23 → in-pane session links.

        return { text, blocks: { code: codeBlocks, link: linkChips } };
    }

    /**
     * Restore moved structured-block placeholders after the escaped
     * line rendering. Unknown prefixes and missing arrays pass
     * through untouched; restore order across prefixes is irrelevant
     * (distinct inert placeholders).
     */
    function restoreStructuredBlocks(html, blocks) {
        const b = blocks || {};
        const table = [
            ['CUTTLE_THINK_', b.think], ['CUTTLE_TOOL_', b.tool],
            ['CUTTLE_TRACE_', b.trace], ['CUTTLE_PROGRESS_', b.progress],
            ['CUTTLE_METERS_', b.meters], ['CUTTLE_PRICING_', b.pricing],
            ['CUTTLE_TERM_', b.terminal], ['CUTTLE_MEDIA_', b.media],
            ['CUTTLE_VEGA_', b.vega],
            ['CUTTLE_CODE_', b.code], ['CUTTLE_LINK_', b.link],
        ];
        let out = String(html || '');
        for (const [prefix, arr] of table) {
            if (!Array.isArray(arr)) continue;
            for (let i = 0; i < arr.length; i++) {
                out = out.split('{{' + prefix + i + '}}').join(arr[i]);
            }
        }
        return out;
    }

    // Invisible characters that survive a rendered-text copy and show up as
    // garbage in terminals: zero-width/BOM/soft hyphen dropped, exotic spaces
    // flattened to ASCII space.
    function cleanCodeCopyText(raw) {
        return String(raw ?? '')
            .replace(/\r\n?/g, '\n')
            .replace(/[\u200B-\u200D\u2060\uFEFF\u00AD]/g, '')
            .replace(/[\u00A0\u2007\u202F]/g, ' ')
            .replace(/\n+$/, '');
    }

    /** Structured layout inside model reasoning blocks (step labels vs plain lines). */
    function formatThinkingInner(raw, deps) {
        const d = deps || {};
        let trimmed = String(raw ?? '').trim();
        if (!trimmed) return '';
        // Render fenced code the model quoted in its reasoning as real blocks
        // instead of leaving raw ``` lines. Protect them before line splitting.
        const codeBlocks = [];
        trimmed = trimmed.replace(/```([^\n`]*)\r?\n([\s\S]*?)```/g, function (_, langRaw, code) {
            const lang = String(langRaw || '').trim().split(/\s+/)[0] || '';
            const safeLang = /^[a-zA-Z0-9_+#.-]+$/.test(lang) ? lang : '';
            const safe = d.escapeHtmlInline(String(code ?? '').replace(/\n$/, ''));
            const cls = safeLang ? (' class="language-' + d.escapeHtmlInline(safeLang) + '"') : '';
            const ph = '\u0002TC' + codeBlocks.length + '\u0002';
            codeBlocks.push('<pre class="message-code-block"><code' + cls + '>' + safe + '</code></pre>');
            return '\n' + ph + '\n';
        });
        const lines = trimmed.split('\n');
        const parts = [];
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const codeM = line.trim().match(/^\u0002TC(\d+)\u0002$/);
            if (codeM) {
                parts.push(codeBlocks[Number(codeM[1])] || '');
                continue;
            }
            if (line.trim() === '') continue;
            const m = line.match(
                /^\s*((?:Step\s*\d+[:.)]|\d{1,2}\.\s+|Action:|Thought:|Observation:|Tool(?:\s+use)?:|Result:|Planning:|Final:)\s*)(.*)$/i
            );
            if (m && m[1] && m[1].trim().length < 52) {
                parts.push(
                    '<div class="thinking-step">'
                    + '<span class="thinking-step-label">' + d.escapeHtmlInline(m[1].trim()) + '</span>'
                    + '<span class="thinking-step-body">' + formatThinkingLineInline(m[2], d) + '</span>'
                    + '</div>'
                );
            } else {
                parts.push('<div class="thinking-line">' + formatThinkingLineInline(line, d) + '</div>');
            }
        }
        return '<div class="thinking-inner">' + parts.join('') + '</div>';
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
        formatThinkingLineInline,
        formatThinkingInner,
        buildVegaWrapHtml,
        cleanCodeCopyText,
        extractHeadStructuredBlocks,
        extractTailStructuredBlocks,
        extractCodeLinkBlocks,
        restoreStructuredBlocks,
    };

    const ns = (root.CuttleChatMessages = root.CuttleChatMessages || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
