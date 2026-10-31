/**
 * CuttleSecrets — pure model/markup for the Secrets App (no DOM, no fetch).
 * The page controller (secrets_page.js) owns transport and events.
 */
(function (root) {
    'use strict';

    const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
    const POLICY_LABELS = {
        per_chat: 'Once per chat',
        every_use: 'Every use',
        never: 'Never ask',
    };

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function validName(name) {
        return NAME_RE.test(String(name || ''));
    }

    function formatTime(ts) {
        const n = Number(ts);
        if (!ts || !Number.isFinite(n)) return 'never';
        return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
            .format(new Date(n * 1000));
    }

    /** argv from a textarea: one argument per line (blank lines dropped). */
    function parseArgv(text) {
        return String(text || '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    }

    function argvText(argv) {
        return (Array.isArray(argv) ? argv : []).join('\n');
    }

    function backendById(backends, id) {
        return (backends || []).find((b) => b.id === id) || null;
    }

    function sessionLabel(session) {
        const s = String(session || '');
        return /^\d+$/.test(s) ? 'CH-' + s.padStart(6, '0') : s;
    }

    function overviewHtml(status, secrets) {
        const backends = status.backends || [];
        const ok = backends.filter((b) => b.available).length;
        const cfg = status.config || {};
        const stat = (value, label) => `<div class="secrets-stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
        return stat(secrets.length, 'secrets')
            + stat(`${ok}/${backends.length}`, 'backends available')
            + stat(POLICY_LABELS[cfg.approval] || cfg.approval || '—', 'approval')
            + stat(cfg.redact === false ? 'off' : 'on', 'output redaction');
    }

    function secretRowHtml(s, backends) {
        const b = backendById(backends, s.backend);
        const writable = !b || b.writable;
        const state = s.stored
            ? '<span class="secrets-badge is-ok">stored</span>'
            : '<span class="secrets-badge is-warn">no value</span>';
        const meta = [
            `backend ${esc((b && b.label) || s.backend)}`,
            s.ref && s.ref !== s.name ? `ref ${esc(s.ref)}` : '',
            s.project ? `only in ${esc(s.project)}` : '',
            `used ${esc(s.use_count || 0)}× · last ${esc(formatTime(s.last_used))}`,
        ].filter(Boolean).join(' · ');
        return `<div class="secrets-row" data-secret="${esc(s.name)}">`
            + `<div class="secrets-row-main"><div class="secrets-row-title"><code>${esc(s.name)}</code>${state}</div>`
            + (s.description ? `<div class="secrets-row-meta">${esc(s.description)}</div>` : '')
            + `<div class="secrets-row-meta">${meta}</div></div>`
            + `<div class="secrets-row-actions">`
            + (writable ? `<button type="button" data-act="set-value">${s.stored ? 'Replace value' : 'Set value'}</button>` : '')
            + `<button type="button" data-act="revoke-secret">Revoke approvals</button>`
            + `<button type="button" class="secrets-danger" data-act="delete-secret">Delete</button>`
            + `</div></div>`;
    }

    function secretsListHtml(secrets, backends) {
        if (!secrets.length) {
            return '<div class="secrets-empty">No secrets yet. Add one below, or let an agent ask for one '
                + 'in chat (the value goes straight to the vault).</div>';
        }
        return secrets.map((s) => secretRowHtml(s, backends)).join('');
    }

    function grantsHtml(grants) {
        if (!grants.length) return '<div class="secrets-empty">No active approvals.</div>';
        return grants.map((g) => `<div class="secrets-row" data-grant-session="${esc(g.session)}" data-grant-secret="${esc(g.secret)}">`
            + `<div class="secrets-row-main"><div class="secrets-row-title"><code>${esc(g.secret)}</code>`
            + `<span class="secrets-badge">${g.mode === 'once' ? 'once' : 'this chat'}</span> ${esc(sessionLabel(g.session))}</div>`
            + (g.command ? `<div class="secrets-row-meta"><code>${esc(g.command)}</code></div>` : '')
            + `<div class="secrets-row-meta">approved ${esc(formatTime(g.created))}</div></div>`
            + `<div class="secrets-row-actions"><button type="button" data-act="revoke-grant">Revoke</button></div></div>`).join('');
    }

    function backendsHtml(backends) {
        return backends.map((b) => {
            const badge = b.available
                ? '<span class="secrets-badge is-ok">available</span>'
                : '<span class="secrets-badge is-bad">unavailable</span>';
            const flags = [b.kind, b.writable ? 'read/write' : 'read-only', b.default ? 'default' : '']
                .filter(Boolean).join(' · ');
            return `<div class="secrets-row" data-backend="${esc(b.id)}">`
                + `<div class="secrets-row-main"><div class="secrets-row-title">${esc(b.label)} <code>${esc(b.id)}</code>${badge}</div>`
                + `<div class="secrets-row-meta">${esc(flags)}</div>`
                + `<div class="secrets-row-meta">${esc(b.detail || '')}</div></div>`
                + `<div class="secrets-row-actions">`
                + (b.default ? '' : `<button type="button" data-act="default-backend"${b.available ? '' : ' disabled'}>Make default</button>`)
                + (b.builtin ? '' : `<button type="button" class="secrets-danger" data-act="delete-backend">Remove</button>`)
                + `</div></div>`;
        }).join('');
    }

    function usageText() {
        return [
            '# Ask you for a value (shows a password field in chat):',
            'python -m api.secret_vault request prod-ssh --label "prod-ssh=SSH password for deploy@prod"',
            '',
            '# ssh / scp / rsync / sudo / git password prompts (askpass):',
            'python -m api.secret_vault run --askpass prod-ssh -- ssh deploy@prod uptime',
            'python -m api.secret_vault run --askpass root-pw -- sudo systemctl restart nginx',
            '',
            '# API keys as environment variables:',
            'python -m api.secret_vault run --env OPENAI_API_KEY=openai -- python script.py',
            '',
            '# Tools that read stdin or a file:',
            'python -m api.secret_vault run --stdin registry -- docker login -u me --password-stdin ghcr.io',
            'python -m api.secret_vault run --file KUBECONFIG=kube-prod -- kubectl get pods',
            '',
            '# Several prompts: map prompt regexes to secrets',
            'python -m api.secret_vault run --askpass gh-user=Username --askpass gh-token=Password -- git push',
        ].join('\n');
    }

    /** Body for PUT /api/secret-vault/backends/<id> from the add-backend form. */
    function backendPayload(form) {
        const preset = String(form.preset || '');
        if (preset === 'keyring') {
            return { kind: 'keyring', label: form.label || '',
                options: form.keyring_backend ? { keyring_backend: form.keyring_backend } : {} };
        }
        return {
            kind: 'command',
            label: form.label || '',
            options: {
                get: parseArgv(form.get),
                set: parseArgv(form.set),
                delete: parseArgv(form.delete),
                first_line: !!form.first_line,
            },
        };
    }

    const api = {
        POLICY_LABELS, esc, validName, formatTime, parseArgv, argvText, backendById, sessionLabel,
        overviewHtml, secretRowHtml, secretsListHtml, grantsHtml, backendsHtml, usageText, backendPayload,
    };
    root.CuttleSecrets = Object.assign(root.CuttleSecrets || {}, api);
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
