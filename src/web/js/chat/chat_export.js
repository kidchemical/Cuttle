/* ================================================================
   Cuttle Chat — transcript export (chat_export.js).
   Owner: the chat menu's "Export" action. Signed-in chats download the
   server's Markdown (GET /api/auth/sessions/<id>/export, built by
   auth_api); local chats (no account) are rendered here from the
   `chatSessions` store.

   Pure: hhmm / agentDisplayName / messageAuthor / transcriptMarkdown /
   exportFilename (no document, no storage, callers pass `now`).
   Controller: create(host).exportSession(sessionId, event) downloads.
   Message decoding (opts, legacy chrome strip, slash chips, assistant
   name) arrives through `host.format`; no page globals.
   ================================================================ */
(function (root) {
    'use strict';

    const AGENT_NAMES = {
        codex: 'Codex', cursor: 'Cursor', claude: 'Claude Code', muse: 'Muse Code',
        deepseek: 'DeepSeek', opencode: 'OpenCode', hermes: 'Hermes',
    };

    /** Local "HH:MM" for an epoch-ms timestamp, '' when missing or invalid. */
    function hhmm(ts) {
        if (!ts) return '';
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) return '';
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }

    function agentDisplayName(agent) {
        const name = String(agent || '');
        return AGENT_NAMES[name] || name;
    }

    /**
     * Author label for one exported row: sub-agent speaker → routing badge →
     * slash chip → assistant name. `fmt` = { normalizeSlashCommand, assistantName }.
     */
    function messageAuthor(role, opts, fmt) {
        const speaker = opts && opts.speaker && String(opts.speaker).trim();
        if (speaker) return speaker;
        if (role === 'user') return 'You';
        const badge = opts && opts.routing_badge;
        if (badge && badge.agent) return agentDisplayName(badge.agent);
        const sc = fmt.normalizeSlashCommand(opts && opts.slash_command);
        if (sc && sc.chips && sc.chips.length) {
            const meta = String((sc.chips[0] && sc.chips[0].meta) || '');
            const m = meta.match(/^\/([a-z][\w-]*)/);
            if (m) return agentDisplayName(m[1]);
            const label = String((sc.chips[0] && sc.chips[0].label) || '').split(/\s*[—-]\s*/)[0].trim();
            if (label) return label;
        }
        return fmt.assistantName();
    }

    /**
     * Markdown transcript. `doc` = { title, displayId, messages, now (Date) };
     * `fmt` = { messageOpts, stripAgentChrome, normalizeSlashCommand, assistantName }.
     */
    function transcriptMarkdown(doc, fmt) {
        const lines = [
            '# ' + (String(doc.title || '').trim() || 'Chat transcript'),
            'Chat: ' + doc.displayId + ' · Exported ' + doc.now.toISOString().slice(0, 16).replace('T', ' ') + ' UTC',
            '---',
            '',
        ];
        (doc.messages || []).filter((m) => m && m.role !== 'system').forEach((m) => {
            const role = String(m.role || 'user');
            const opts = fmt.messageOpts(m);
            const raw = String(m.content ?? '');
            const body = role === 'assistant' ? fmt.stripAgentChrome(raw) : raw;
            if (!body) return;
            const stamp = hhmm(opts.timestamp > 0 ? Number(opts.timestamp) : 0);
            lines.push('**' + (stamp ? stamp + ' — ' : '') + messageAuthor(role, opts, fmt) + '**');
            lines.push('');
            body.trim().split(/\r\n|\r|\n/).forEach((line) => lines.push(line));
            lines.push('');
        });
        return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
    }

    /** cuttle-chat-ch-000042-20261101.md */
    function exportFilename(displayId, now) {
        const stamp = now.toISOString().slice(0, 10).replace(/-/g, '');
        return 'cuttle-chat-' + String(displayId || 'chat').replace(/[^A-Za-z0-9_-]/g, '').toLowerCase() + '-' + stamp + '.md';
    }

    /**
     * host: { win, storage(), isAuthMode(), authSessionId(sid), displayId(sid),
     *         title(sid), format: {…see transcriptMarkdown}, closeMenus(), toast(msg, kind) }
     */
    function create(host) {
        const win = host.win;
        const doc = win.document;

        function clickDownload(href, filename) {
            const a = doc.createElement('a');
            a.href = href;
            a.download = filename;
            a.rel = 'noopener';
            a.style.display = 'none';
            doc.body.appendChild(a);
            a.click();
            try { a.remove(); } catch (_) { /* detached */ }
        }

        function localMessages(sessionId) {
            try {
                const sessions = JSON.parse(host.storage().getItem('chatSessions') || '{}');
                const s = sessions[sessionId] || sessions[String(sessionId)];
                return s && Array.isArray(s.messages) ? s.messages : null;
            } catch (_) {
                return null;
            }
        }

        async function exportSession(sessionId, event) {
            if (event) {
                event.stopPropagation();
                event.preventDefault();
            }
            if (sessionId == null || sessionId === '') return;
            host.closeMenus();
            const now = new Date();
            if (host.isAuthMode()) {
                const authSid = host.authSessionId(sessionId);
                if (authSid == null || authSid === '') return;
                const filename = exportFilename(host.displayId(authSid) || String(authSid), now);
                const tz = -now.getTimezoneOffset();
                clickDownload('/api/auth/sessions/' + encodeURIComponent(authSid) + '/export?tz=' + tz, filename);
                host.toast('Exported ' + filename, 'success');
                return;
            }
            const messages = localMessages(sessionId);
            if (!messages || !messages.some((m) => m && m.role !== 'system')) {
                host.toast('Nothing to export yet', 'error');
                return;
            }
            try {
                const displayId = host.displayId(sessionId) || String(sessionId);
                const md = transcriptMarkdown({ title: host.title(sessionId), displayId, messages, now }, host.format);
                const url = win.URL.createObjectURL(new win.Blob([md], { type: 'text/markdown;charset=utf-8' }));
                const filename = exportFilename(host.displayId(sessionId) || 'chat', now);
                clickDownload(url, filename);
                win.setTimeout(() => { try { win.URL.revokeObjectURL(url); } catch (_) { /* gone */ } }, 4000);
                host.toast('Exported ' + filename, 'success');
            } catch (e) {
                host.toast('Export failed: ' + String((e && e.message) || e), 'error');
            }
        }

        return { exportSession };
    }

    const api = { hhmm, agentDisplayName, messageAuthor, transcriptMarkdown, exportFilename, create };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatExport = api;
})(typeof window !== 'undefined' ? window : globalThis);
