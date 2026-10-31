/**
 * CuttleChatSecretFields — secret-vault field types inside chat action forms.
 *
 * Field types (spec owner: api.secret_vault.cards):
 *   secret           password input; the value goes straight to
 *                    PUT /api/secret-vault/secrets/<name> and the form answer
 *                    only says it was stored.
 *   secret_approval  Allow for this chat / Allow once / Deny; the grant is
 *                    recorded via POST /api/secret-vault/grants.
 *
 * Inputs carry no `data-field-id`, so the generic form collector never sees
 * a value. Pure markup + one async `apply(card, deps)` with injected request.
 */
(function (root) {
    'use strict';

    const TYPES = ['secret', 'secret_approval'];
    const APPROVAL_CHOICES = [
        { value: 'granted_chat', label: 'Allow for this chat' },
        { value: 'granted_once', label: 'Allow once' },
        { value: 'denied', label: 'Deny' },
    ];

    function handles(type) {
        return TYPES.indexOf(String(type || '').toLowerCase()) >= 0;
    }

    function names(f) {
        const list = Array.isArray(f && f.secrets) ? f.secrets : [];
        return list.map(String).filter(Boolean);
    }

    /** Markup for one field; `ctx` = {fid, label, req, help, disabled, esc}. */
    function renderFieldHtml(f, ctx) {
        const esc = ctx.esc;
        const dis = ctx.disabled ? ' disabled' : '';
        const type = String((f && f.type) || '').toLowerCase();
        if (type === 'secret') {
            const name = esc(String((f && f.secret) || ''));
            return `<div class="form-field cuttle-secret-field">` +
                `<label class="form-label">🔒 ${ctx.label}${ctx.req}</label>` +
                `<input class="form-input" type="password" autocomplete="new-password" spellcheck="false"` +
                ` data-secret-field="${ctx.fid}" data-secret-name="${name}"` +
                ` data-secret-required="${f && f.required ? '1' : '0'}"` +
                ` placeholder="Saved to your Cuttle vault"${dis}>` +
                `<div class="form-help">Goes only to your vault as <code>secret:${name}</code>. ` +
                `The agent never sees it.</div>${ctx.help}</div>`;
        }
        const list = names(f);
        const group = `af_${ctx.fid}`;
        const command = String((f && f.command) || '');
        return `<div class="form-field cuttle-secret-approval">` +
            `<div class="form-label">🔑 ${ctx.label}${ctx.req}</div>` +
            `<div class="form-help">Secrets: ${list.map((n) => `<code>${esc(n)}</code>`).join(', ') || '(none)'}</div>` +
            (command ? `<pre class="cuttle-secret-command">${esc(command)}</pre>` : '') +
            `<div class="radio-group" data-secret-approval="${ctx.fid}"` +
            ` data-secret-names="${esc(list.join(','))}" data-secret-command="${esc(command)}">` +
            APPROVAL_CHOICES.map((c) => `<label class="radio-item"><input type="radio" name="${group}"` +
                ` value="${c.value}"${dis}><span>${c.label}</span></label>`).join('') +
            `</div>${ctx.help}</div>`;
    }

    /** Safe placeholder when this module is missing: never a plain text input. */
    function unavailableHtml(label, esc) {
        return `<div class="form-field"><div class="form-label">${label}</div>` +
            `<div class="form-help">${esc('Secret fields are unavailable on this page. Refresh Cuttle.')}</div></div>`;
    }

    function hasFields(card) {
        return !!(card && card.querySelector('[data-secret-field],[data-secret-approval]'));
    }

    async function readError(resp) {
        let data = {};
        try { data = await resp.json(); } catch (_) {}
        if (resp.ok && data && data.success !== false) return '';
        return (data && data.error) || `Vault request failed (${resp.status}).`;
    }

    /**
     * Store typed secrets / record approvals before the form answer is sent.
     * deps = {request(url, {options, signal}) → Response, sessionId, signal}.
     * Returns {fields} (answer tokens per field id) or {error}.
     */
    async function apply(card, deps) {
        const fields = {};
        const inputs = Array.from(card.querySelectorAll('input[data-secret-field]'));
        for (const input of inputs) {
            const fid = input.getAttribute('data-secret-field');
            const name = input.getAttribute('data-secret-name') || '';
            const value = String(input.value || '');
            if (!value) {
                if (input.getAttribute('data-secret-required') === '1') {
                    return { error: `Enter a value for ${name || fid}.` };
                }
                fields[fid] = 'skipped';
                continue;
            }
            const resp = await deps.request('/api/secret-vault/secrets/' + encodeURIComponent(name), {
                options: {
                    method: 'PUT',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ value, session_id: deps.sessionId || '' }),
                },
                signal: deps.signal,
            });
            const err = await readError(resp);
            if (err) return { error: err };
            input.value = '';
            fields[fid] = 'stored';
        }
        const groups = Array.from(card.querySelectorAll('[data-secret-approval]'));
        for (const group of groups) {
            const fid = group.getAttribute('data-secret-approval');
            const chosen = group.querySelector('input[type="radio"]:checked');
            if (!chosen) return { error: 'Choose Allow or Deny.' };
            const choice = chosen.value;
            fields[fid] = choice;
            if (choice === 'denied') continue;
            const list = String(group.getAttribute('data-secret-names') || '').split(',').filter(Boolean);
            const resp = await deps.request('/api/secret-vault/grants', {
                options: {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: deps.sessionId || '',
                        secrets: list,
                        mode: choice === 'granted_once' ? 'once' : 'chat',
                        command: group.getAttribute('data-secret-command') || '',
                    }),
                },
                signal: deps.signal,
            });
            const err = await readError(resp);
            if (err) return { error: err };
        }
        return { fields };
    }

    const api = { TYPES, APPROVAL_CHOICES, handles, renderFieldHtml, unavailableHtml, hasFields, apply };
    root.CuttleChatSecretFields = Object.assign(root.CuttleChatSecretFields || {}, api);
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
