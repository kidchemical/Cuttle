/* ================================================================
   Cuttle Chat — timeline notices (chat_timeline_notice.js).
   Owner: the one-line "something happened" rows in the transcript
   (project / worktree / checkout change, Stopped generating, model
   change, context compacted, …) — their typed shape, markup, in-place
   updates, the running → done/error transition and durable persist.

   Vocabulary (docs/architecture/chat-inline-surfaces.md):
     timeline notice    — this module; a fact, saved to the chat log
     live status card   — chat_background_work.js; pinned last, never saved
     action card        — chat_action_forms.js / chat_action_cards.js;
                          agent-authored, inside a reply

   A notice is a spec: { type, label, detail?, icon?, state? }.
   `type` picks the icon and styling from TYPES; `state` is 'done'
   (default), 'running' (spinner + elapsed, never persisted) or 'error'.
   Legacy rows (plain text with a leading emoji) parse via `fromText`.
   Persisted rows keep readable `content` (what agents and the history
   CLI see) plus `metadata.timeline_notice` (what the renderer reads).

   Interface: pure `normalize`, `fromText`, `fromRow`, `toText`,
   `renderHtml` on the namespace; `create(host)` → one controller per
   chat frame with `add(spec, opts)` / `update(el, spec, opts)`.
   host: { doc, win, container(), isVisible(), autoScroll(), escapeHtml,
           fetch, persistTarget() → bare numeric session id | null,
           onPersisted(messageId) }.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const TYPES = {
        project: { icon: '\u{1F4C1}', codeDetail: true },
        worktree: { icon: '\u{1F33F}' },
        checkout: { icon: '\u{1F4C1}' },
        model: { icon: '⚡' },
        stop: { icon: '⏹' },
        compact: { icon: '\u{1F5DC}️' },
        supervision: { icon: '\u{1F441}️' }, // a supervision schedule's settings changed
        notice: { icon: '' },
    };
    const KIND_TYPES = {
        'generation-stop': 'stop',
        'model-change': 'model',
        'context-compact': 'compact',
    };
    const STATES = new Set(['done', 'running', 'error']);
    const LEAD_ICON = /^(\p{Extended_Pictographic}️?)\s*/u;
    const META_KEY = 'timeline_notice';
    const AUTO_FADE_MS = 3000;

    function normalize(spec) {
        if (spec == null || typeof spec !== 'object') return fromText(spec);
        const type = Object.prototype.hasOwnProperty.call(TYPES, spec.type) ? spec.type : 'notice';
        const state = STATES.has(spec.state) ? spec.state : 'done';
        const icon = spec.icon != null ? String(spec.icon) : TYPES[type].icon;
        return {
            type,
            state,
            icon,
            label: String(spec.label == null ? '' : spec.label),
            detail: spec.detail == null || spec.detail === '' ? '' : String(spec.detail),
        };
    }

    function typeFromLabel(label) {
        if (/^Project:/i.test(label)) return 'project';
        if (/^Worktree:/i.test(label)) return 'worktree';
        if (/^Local checkout/i.test(label)) return 'checkout';
        if (/^Model:/i.test(label)) return 'model';
        if (/^Stopped generating/i.test(label)) return 'stop';
        return 'notice';
    }

    /** Parse a legacy text row ("📁 Project: X → `path`") into a spec. */
    function fromText(text, kind) {
        let rest = String(text == null ? '' : text).trim();
        let icon = null;
        const m = rest.match(LEAD_ICON);
        if (m) {
            icon = m[1];
            rest = rest.slice(m[0].length);
        }
        let label = rest;
        let detail = '';
        for (const sep of [' · ', ' → ']) {
            const at = rest.indexOf(sep);
            if (at > 0) {
                label = rest.slice(0, at);
                detail = rest.slice(at + sep.length).replace(/^`([^`]*)`$/, '$1');
                break;
            }
        }
        const type = KIND_TYPES[kind] || typeFromLabel(label);
        return normalize({ type, label, detail, icon: icon == null ? undefined : icon });
    }

    /** Spec for a persisted row: structured metadata wins, else parse text. */
    function fromRow(content, kind, meta) {
        const saved = meta && typeof meta === 'object' ? meta[META_KEY] : null;
        if (saved && typeof saved === 'object' && saved.label) {
            return normalize(Object.assign({}, saved, { state: saved.state === 'error' ? 'error' : 'done' }));
        }
        return fromText(content, kind);
    }

    /** Readable one-line content for the chat log / clipboard / agents. */
    function toText(spec) {
        const p = normalize(spec);
        return (p.icon ? p.icon + ' ' : '') + p.label + (p.detail ? ' · ' + p.detail : '');
    }

    function renderHtml(spec, escapeHtml) {
        const esc = typeof escapeHtml === 'function' ? escapeHtml : String;
        const p = normalize(spec);
        const cls = [
            'message-content', 'system-message', 'timeline-notice',
            'timeline-notice--' + p.type, 'is-' + p.state,
        ];
        if (TYPES[p.type].codeDetail) cls.push('timeline-notice--code-detail');
        const icon = p.state === 'running'
            ? '<span class="timeline-notice-spinner" aria-hidden="true"></span>'
            : (p.icon ? `<span class="timeline-notice-icon" aria-hidden="true">${esc(p.icon)}</span>` : '');
        const live = p.state === 'running' ? ' role="status" aria-live="polite"' : '';
        return (
            `<div class="${cls.join(' ')}"${live}>` +
            // Whitespace between flex items does not render but keeps the
            // row's textContent readable ("⏹ Stopped generating.").
            (icon ? icon + ' ' : '') +
            `<span class="timeline-notice-label">${esc(p.label)}</span>` +
            (p.detail ? ` <span class="timeline-notice-detail">${esc(p.detail)}</span>` : '') +
            (p.state === 'running' ? ' <span class="timeline-notice-elapsed"></span>' : '') +
            `</div>`
        );
    }

    function elapsedText(sec) {
        if (!(sec >= 1)) return '';
        const s = Math.floor(sec);
        return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's';
    }

    function create(host) {
        const doc = host.doc;
        const win = host.win;
        const esc = host.escapeHtml;
        let ticker = 0;

        function paint(el, spec) {
            const p = normalize(spec);
            el.dataset.noticeType = p.type;
            el.dataset.rawContent = toText(p);
            el.__notice = p;
            if (p.state === 'running') {
                if (!el.__noticeStarted) el.__noticeStarted = Date.now();
            } else {
                delete el.__noticeStarted;
            }
            el.innerHTML = renderHtml(p, esc);
            if (p.state === 'running') ensureTicker();
            return p;
        }

        function tick() {
            const box = host.container();
            const rows = box ? box.querySelectorAll('.message.system[data-notice-type]') : [];
            let running = 0;
            rows.forEach((row) => {
                if (!row.__noticeStarted) return;
                running += 1;
                const out = row.querySelector('.timeline-notice-elapsed');
                if (out) out.textContent = elapsedText((Date.now() - row.__noticeStarted) / 1000);
            });
            if (!running && ticker) {
                win.clearInterval(ticker);
                ticker = 0;
            }
        }

        function ensureTicker() {
            if (!ticker) ticker = win.setInterval(tick, 1000);
        }

        function persist(el, opts) {
            // A notice belongs to the chat it was shown in, even if a slow
            // running → done update lands after the user switched chats.
            const sid = el && el.dataset.noticeSession ? el.dataset.noticeSession : host.persistTarget();
            if (!el || !sid || el.dataset.persistInFlight === '1' || el.dataset.messageId) return;
            const p = el.__notice;
            if (!p || p.state === 'running') return;
            el.dataset.persistInFlight = '1';
            const body = {
                role: 'system',
                content: toText(p),
                metadata: { [META_KEY]: { type: p.type, label: p.label, detail: p.detail, icon: p.icon, state: p.state } },
            };
            if (opts && opts.kind) body.kind = String(opts.kind);
            host.fetch(`/api/auth/sessions/${encodeURIComponent(sid)}/messages`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            })
                .then((r) => r.json().catch(() => ({})))
                .then((data) => {
                    if (data && data.success && data.message_id != null) {
                        el.dataset.messageId = String(data.message_id);
                        host.onPersisted(Number(data.message_id));
                    }
                })
                .catch((e) => console.warn('[Cuttle Chat] persist timeline notice failed:', e))
                .finally(() => { delete el.dataset.persistInFlight; });
        }

        /** Re-render a notice in place (e.g. running → done) and persist when asked. */
        function update(el, spec, opts) {
            const o = opts || {};
            if (!el || !el.isConnected) return null;
            paint(el, spec);
            if (!o.skipScroll) host.autoScroll();
            if (o.persist) persist(el, { kind: o.kind || el.dataset.kind });
            return el;
        }

        /**
         * Add a notice. spec: notice spec or legacy text.
         * opts: { kind, persist, sticky, replace, prepend, insertBefore,
         *         skipScroll, message_id }. Non-persisted, non-sticky notices
         * fade after 3s; a `kind` updates this turn's latest matching row.
         */
        function add(spec, opts) {
            const o = opts || {};
            const box = host.container();
            if (!box || !host.isVisible()) return null;
            if (o.kind && o.replace !== false) {
                const list = box.querySelectorAll(`.message.system[data-kind="${CSS.escape(String(o.kind))}"]`);
                const existing = list.length ? list[list.length - 1] : null;
                const users = box.querySelectorAll('.message.user');
                const lastUser = users.length ? users[users.length - 1] : null;
                const stale = existing && lastUser
                    && (existing.compareDocumentPosition(lastUser) & Node.DOCUMENT_POSITION_FOLLOWING);
                if (existing && !stale) {
                    paint(existing, spec);
                    if (!o.skipScroll) host.autoScroll();
                    // Already durable — do not POST a duplicate row on a text tweak.
                    if (o.persist) persist(existing, o);
                    return existing;
                }
            }
            const el = doc.createElement('div');
            el.className = 'message system';
            if (o.kind) el.dataset.kind = String(o.kind);
            if (o.message_id != null && o.message_id !== '') el.dataset.messageId = String(o.message_id);
            const target = host.persistTarget();
            if (target) el.dataset.noticeSession = target;
            paint(el, spec);
            if (o.prepend) {
                const anchor = o.insertBefore || box.querySelector('.message') || box.firstChild;
                if (anchor) box.insertBefore(el, anchor);
                else box.appendChild(el);
            } else {
                box.appendChild(el);
            }
            if (!o.skipScroll) host.autoScroll();
            if (o.persist) {
                persist(el, o);
                return el;
            }
            if (o.sticky || el.__notice.state === 'running') return el;
            win.setTimeout(() => {
                el.style.transition = 'opacity 0.3s';
                el.style.opacity = '0';
                win.setTimeout(() => el.remove(), 300);
            }, AUTO_FADE_MS);
            return el;
        }

        return { add, update };
    }

    const api = {
        TYPES,
        KIND_TYPES,
        META_KEY,
        normalize,
        fromText,
        fromRow,
        toText,
        renderHtml,
        elapsedText,
        create,
    };
    const ns = (root.CuttleChatTimelineNotice = root.CuttleChatTimelineNotice || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
