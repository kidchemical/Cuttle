/* ================================================================
   Cuttle Chat — attachments frontend domain (chat_attachments.js).
   Owner: attachment classification, normalization, history-note
   parsing, upload-result interpretation, and outbound note
   formatting as pure decisions over explicit inputs: no document,
   no window, no localStorage, no fetch here. Loaded before
   chat_page.js; the page owns pending-attachment state, persistence
   IO, chip rendering, upload transport, message HTML rendering, and
   send orchestration, and calls into `CuttleChatAttachments.*`.

   Mirrors the stabilized backend contract (`POST /api/upload` →
   {success, files: [{filename, path, mime, size, url}]} and the
   vision pre-pass `[Attached: …]` history note): image kinds,
   upload error precedence, and legacy note inference.
   ================================================================ */
(function (root) {
    'use strict';

    /** Image (thumbnail-capable) vs generic file, by mime then extension. */
    function isImageAttachment(att) {
        if (!att) return false;
        const mime = String(att.mime || '').toLowerCase();
        if (mime.startsWith('image/')) return true;
        const name = String(att.filename || att.url || '').toLowerCase();
        return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
    }

    /** Server rows / restored rows → canonical item shape. */
    function normalizeAttachmentList(list) {
        if (!Array.isArray(list)) return [];
        return list
            .filter((a) => a && typeof a === 'object')
            .map((a) => ({
                filename: a.filename || 'file',
                path: a.path || '',
                mime: a.mime || '',
                size: a.size,
                url: a.url || '',
            }));
    }

    /** Strip trailing `[Attached: …]` note used in stored history text. */
    function stripAttachedNote(content) {
        return String(content || '')
            .replace(/\n?\[Attached:[^\]]*\]\s*$/i, '')
            .trim();
    }

    /** Parse `[Attached: a.png, b.pdf]` names from stored message text. */
    function parseAttachedFilenames(content) {
        const m = String(content || '').match(/\[Attached:\s*([^\]]+)\]\s*$/i);
        if (!m) return [];
        return m[1]
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);
    }

    /**
     * Infer attachment refs from history text when metadata is missing
     * (older messages). Tries the given session folder, then anon.
     */
    function inferAttachmentsFromContent(parts) {
        const p = parts || {};
        const names = parseAttachedFilenames(p.content);
        if (!names.length) return [];
        const folders = [];
        if (p.sessionId != null && p.sessionId !== '') {
            folders.push(String(p.sessionId));
        }
        folders.push('anon');
        const folder = folders[0];
        return names.map((filename) => {
            const url = '/output/uploads/' + encodeURIComponent(folder) + '/' + encodeURIComponent(filename);
            return {
                filename,
                mime: isImageAttachment({ filename }) ? 'image/*' : '',
                url,
            };
        });
    }

    /** Outbound `message + [Attached: …]` note (slash prefix stays at front). */
    function formatMessageWithAttachments(message, attachments) {
        const text = (message || '').trim();
        if (!attachments || !attachments.length) return text;
        const names = attachments.map((a) => a.filename || 'file').join(', ');
        const note = `[Attached: ${names}]`;
        return text ? `${text}\n${note}` : note;
    }

    /**
     * Request payload mapping for `/api/chat` (`requestBody.attachments`).
     * Narrower than the stored shape: the server resolves content from
     * path/url, so size never travels (staged rows keep it locally).
     */
    function requestAttachmentPayload(attachments) {
        return normalizeAttachmentList(attachments).map((a) => ({
            filename: a.filename,
            path: a.path,
            mime: a.mime || '',
            url: a.url || '',
        }));
    }

    /**
     * Append staged/uploaded rows to the pending list. Order-preserving
     * with no dedupe: staging the same file twice stages it twice
     * (matches the page's historical push behavior).
     */
    function addAttachmentsToPending(list, items) {
        const base = Array.isArray(list) ? list.slice() : [];
        return base.concat(normalizeAttachmentList(items));
    }

    /** Remove the pending row at `index` (chip ✕). Out of range → unchanged. */
    function removeAttachmentAt(list, index) {
        const base = Array.isArray(list) ? list.slice() : [];
        const i = Number(index);
        if (!Number.isInteger(i) || i < 0 || i >= base.length) return base;
        base.splice(i, 1);
        return base;
    }

    /**
     * Message-HTML branch for one item: thumbnail button (image + URL),
     * file link (URL only), or plain span (no URL — e.g. legacy rows).
     * The page still builds the HTML (escaping + download URLs live in
     * the message/media domains); this owns the branch decision.
     */
    function messageAttachmentKind(att) {
        const url = String((att && att.url) || '').trim();
        if (isImageAttachment(att) && url) return 'thumb';
        if (url) return 'link';
        return 'span';
    }

    /**
     * Dispatch plan for an `/api/upload` round-trip. The page executes it:
     * toasts the error or pushes the items into pending state.
     * Returns { ok: true, items } or { ok: false, error }.
     */
    function uploadResultPlan(parts) {
        const p = parts || {};
        const data = p.data;
        if (!p.ok || !data || !data.success) {
            const err = (data && data.error) || `Upload failed (HTTP ${p.status})`;
            return { ok: false, error: err };
        }
        return { ok: true, items: normalizeAttachmentList(data.files) };
    }

    const api = {
        isImageAttachment,
        normalizeAttachmentList,
        stripAttachedNote,
        parseAttachedFilenames,
        inferAttachmentsFromContent,
        formatMessageWithAttachments,
        requestAttachmentPayload,
        addAttachmentsToPending,
        removeAttachmentAt,
        messageAttachmentKind,
        uploadResultPlan,
    };

    const ns = (root.CuttleChatAttachments = root.CuttleChatAttachments || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
