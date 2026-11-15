/* ================================================================
   Cuttle Chat — attachments frontend domain (chat_attachments.js).
   Owner: attachment classification, normalization, history-note
   parsing, upload-result interpretation, outbound note formatting, and
   the pure pending-list transitions (append, remove, take-clear) as
   pure decisions over explicit inputs: no document,
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

    /** Voice clip (composer mic) or other audio, by extension then mime. */
    function isAudioAttachment(att) {
        if (!att) return false;
        const name = String(att.filename || att.url || '').toLowerCase();
        if (/\.(webm|ogg|m4a|wav|mp3)$/i.test(name)) return true;
        return String(att.mime || '').toLowerCase().startsWith('audio/');
    }

    /**
     * Server rows / restored rows → canonical item shape. `duration_ms`
     * (voice clips, staged rows only) is carried when it is a number, and
     * a voice clip's live-text fields when present.
     */
    function normalizeAttachmentList(list) {
        if (!Array.isArray(list)) return [];
        return list
            .filter((a) => a && typeof a === 'object')
            .map((a) => {
                const item = {
                    filename: a.filename || 'file',
                    path: a.path || '',
                    mime: a.mime || '',
                    size: a.size,
                    url: a.url || '',
                };
                if (Number.isFinite(a.duration_ms)) item.duration_ms = a.duration_ms;
                // Live text of a voice clip: its block id, its words when it
                // was attached, and whether those words cover the whole clip.
                if (typeof a.clip_id === 'string' && a.clip_id) item.clip_id = a.clip_id;
                if (typeof a.transcript === 'string' && a.transcript) item.transcript = a.transcript;
                if (a.transcribed === true) item.transcribed = true;
                return item;
            });
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
        return normalizeAttachmentList(attachments).map((a) => Object.assign({
            filename: a.filename,
            path: a.path,
            mime: a.mime || '',
            url: a.url || '',
        }, a.transcribed ? { transcribed: true } : null));
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
     * Take (copy) the pending list and clear it, for a send that consumes
     * the staged rows. Returns { taken, remaining } — the page applies
     * `remaining` back to its state and persists the clear.
     */
    function takePendingAttachments(list) {
        return {
            taken: Array.isArray(list) ? list.slice() : [],
            remaining: [],
        };
    }

    /**
     * Message-HTML branch for one item: thumbnail button (image + URL),
     * audio player (voice clip + URL), file link (URL only), or plain span (no URL — e.g. legacy rows).
     * The page still builds the HTML (escaping + download URLs live in
     * the message/media domains); this owns the branch decision.
     */
    function messageAttachmentKind(att) {
        const url = String((att && att.url) || '').trim();
        if (isAudioAttachment(att) && url) return 'audio';
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

    const PASTE_IMAGE_EXT = {
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'image/bmp': '.bmp',
    };

    function _pad2(n) {
        return String(n).padStart(2, '0');
    }

    /**
     * Upload name for a pasted image. Clipboard images arrive as a bare
     * `image.png` (or no name at all); give each a unique, extension-correct
     * name so `/api/upload` accepts it and same-second pastes don't collide.
     */
    function pastedImageName(mime, index, now) {
        const d = now instanceof Date ? now : new Date();
        const ext = PASTE_IMAGE_EXT[String(mime || '').toLowerCase()] || '.png';
        const stamp =
            d.getFullYear() + _pad2(d.getMonth() + 1) + _pad2(d.getDate()) + '-' +
            _pad2(d.getHours()) + _pad2(d.getMinutes()) + _pad2(d.getSeconds());
        return 'pasted-' + stamp + '-' + (Number(index) + 1) + ext;
    }

    /**
     * Image files carried by a paste's `clipboardData`, renamed for upload.
     * Returns [] when the paste should stay a normal text paste: no images,
     * or the clipboard also carries plain text (e.g. spreadsheet cells
     * copied with a rendered preview image).
     */
    function clipboardImageFiles(clipboardData, now) {
        const dt = clipboardData;
        if (!dt) return [];
        const types = Array.from(dt.types || []);
        if (types.includes('text/plain')) return [];
        let files = [];
        if (dt.items && dt.items.length) {
            for (const item of Array.from(dt.items)) {
                if (item && item.kind === 'file' && /^image\//i.test(item.type || '')) {
                    const f = item.getAsFile && item.getAsFile();
                    if (f) files.push(f);
                }
            }
        }
        if (!files.length && dt.files && dt.files.length) {
            files = Array.from(dt.files).filter((f) => f && /^image\//i.test(f.type || ''));
        }
        return files.map((f, i) => {
            const type = f.type || 'image/png';
            const name = pastedImageName(type, i, now);
            try {
                return new File([f], name, { type });
            } catch (_) {
                return f;
            }
        });
    }

    /**
     * Wire composer textareas so pasting an image attaches it like the 📎
     * button: `upload(files)` is the page's existing upload path. Text
     * pastes are left alone. Browser-only (no-op without `document`).
     */
    function bindPasteUpload(textareaIds, upload) {
        if (typeof document === 'undefined') return;
        for (const id of textareaIds || []) {
            const ta = document.getElementById(id);
            if (!ta || ta.dataset.pasteUpload) continue;
            ta.dataset.pasteUpload = '1';
            ta.addEventListener('paste', (e) => {
                const files = clipboardImageFiles(e.clipboardData);
                if (!files.length) return;
                e.preventDefault();
                upload(files);
            });
        }
    }

    const api = {
        isImageAttachment,
        isAudioAttachment,
        bindPasteUpload,
        pastedImageName,
        clipboardImageFiles,
        normalizeAttachmentList,
        stripAttachedNote,
        parseAttachedFilenames,
        inferAttachmentsFromContent,
        formatMessageWithAttachments,
        requestAttachmentPayload,
        addAttachmentsToPending,
        removeAttachmentAt,
        takePendingAttachments,
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
