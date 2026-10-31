/* ================================================================
   Cuttle Chat — /orchestrate composer chip (chat_orchestrate_chip.js).
   Owner: the segmented "Orchestrate · <playbook>" chip a picked
   `/orchestrate` project command shows, and the popup its click opens
   (choose the playbook, make it the default, jump to Apps → Orchestrator).

   The chip's state is its prefix: `/orchestrate @<id> ` pins a playbook,
   bare `/orchestrate ` uses the user's default — the same text the
   command parses, so the composed turn needs no extra plumbing.
   Playbooks and the default come from /api/orchestrator (authoritative).

   Pure helpers (node-testable): `isChip`, `playbookOf`, `withPlaybook`,
   `labelFor`. Controller: `create(host)` → `{ isChip, label, open }`.
   host: { escapeHtml, openModal(title, subtitle, html, onMount), close,
           rerender(), navigate(page), showToast(msg, kind) }.
   Composed once by chat_page.js; it never reads page scope.
   ================================================================ */
(function (root) {
    'use strict';

    const PREFIX_RE = /^\/(?:cmd\s+)?orchestrate(?:\s+@([a-z][a-z0-9_-]{0,39}))?\s*$/i;

    function isChip(chip) {
        return !!chip && PREFIX_RE.test(String(chip.prefix || ''));
    }

    /** Playbook id pinned on the chip, or '' (= the user's default). */
    function playbookOf(chip) {
        const m = PREFIX_RE.exec(String((chip && chip.prefix) || ''));
        return m && m[1] ? m[1].toLowerCase() : '';
    }

    /** Same chip pointing at `playbookId` ('' = default). Keeps a `/cmd` form. */
    function withPlaybook(chip, playbookId) {
        const head = /^\/cmd\s/i.test(String(chip.prefix || '')) ? '/cmd orchestrate' : '/orchestrate';
        const id = String(playbookId || '').trim().toLowerCase();
        return Object.assign({}, chip, { prefix: head + (id ? ' @' + id : '') + ' ' });
    }

    /** "Orchestrate - <name>": the agent-badge shape, so it renders as two segments. */
    function labelFor(chip, playbooks, defaultId) {
        const id = playbookOf(chip) || defaultId || '';
        const pb = (playbooks || []).find((p) => p && p.id === id);
        const name = pb ? (pb.name || pb.id) : (playbookOf(chip) ? '@' + playbookOf(chip) : '');
        return name ? 'Orchestrate - ' + name.replace(/\s+[-–—·]\s+/g, ' ') : 'Orchestrate';
    }

    function create(host) {
        const h = host || {};
        const esc = typeof h.escapeHtml === 'function' ? h.escapeHtml : (s) => String(s == null ? '' : s);
        const cache = { playbooks: null, defaultId: 'default', loading: null, failed: false };

        function load(force) {
            if (cache.loading) return cache.loading;
            if (cache.playbooks && !force) return Promise.resolve(cache);
            cache.loading = fetch('/api/orchestrator/playbooks', { credentials: 'include', cache: 'no-store' })
                .then((r) => r.json().catch(() => ({})))
                .then((data) => {
                    cache.failed = !(data && data.success);
                    cache.playbooks = (data && data.playbooks) || [];
                    cache.defaultId = (data && data.default_id) || 'default';
                    return cache;
                })
                .catch(() => { cache.failed = true; cache.playbooks = cache.playbooks || []; return cache; })
                .finally(() => { cache.loading = null; });
            return cache.loading;
        }

        /** Chip label; the first call loads playbooks and re-renders once they land. */
        function label(chip) {
            if (!cache.playbooks) {
                load().then(() => { if (typeof h.rerender === 'function') h.rerender(); });
            }
            return labelFor(chip, cache.playbooks, cache.defaultId);
        }

        function fieldsHtml(chip) {
            const pinned = playbookOf(chip);
            const current = pinned || cache.defaultId;
            const options = cache.playbooks.map((pb) => {
                const tags = [pb.id === cache.defaultId ? '★ default' : '', (pb.missing_profiles || []).length ? 'missing profiles' : '']
                    .filter(Boolean).join(' · ');
                return '<option value="' + esc(pb.id) + '"' + (pb.id === current ? ' selected' : '') + '>' +
                    esc(pb.name || pb.id) + (tags ? ' (' + esc(tags) + ')' : '') + '</option>';
            }).join('');
            return '<div class="chip-config-field">' +
                '<span class="chip-config-field-label">Playbook</span>' +
                '<select class="chip-config-select" data-native-select id="orchChipPlaybook">' + options + '</select></div>' +
                '<label class="chip-config-field orch-chip-default">' +
                '<input type="checkbox" id="orchChipDefault"' + (current === cache.defaultId ? ' checked disabled' : '') + '> ' +
                '<span>Use this playbook by default for <code>/orchestrate</code></span></label>' +
                '<p class="orch-chip-summary" id="orchChipSummary"></p>' +
                '<button type="button" class="orch-chip-edit" id="orchChipEdit">Edit playbooks and agents…</button>';
        }

        function summary(pb) {
            if (!pb) return '';
            return 'Org chart: ' + (pb.tiers || []).map((t) => {
                const members = Array.isArray(t.members) ? t.members : (t.profiles || []).map((p) => ({ profile: p }));
                const on = members.filter((m) => m.on !== false).length;
                const off = members.length - on;
                return (t.label || t.id) + ' (' + (on ? on + ' agent' + (on === 1 ? '' : 's') : 'skipped') +
                    (on && off ? ', ' + off + ' off' : '') + ')';
            }).join(' → ');
        }

        /** True when it handled the chip (the popup opens once playbooks load). */
        function open(chip) {
            if (!isChip(chip) || typeof h.openModal !== 'function') return false;
            openPopup(chip);
            return true;
        }

        async function openPopup(chip) {
            await load(true);
            if (cache.failed && !cache.playbooks.length) {
                if (h.showToast) h.showToast('Turn on Orchestrator in Settings → Experimental to choose a playbook.', 'info');
                return;
            }
            h.openModal('Orchestrate', 'Which playbook this chat follows: its org chart, limits and supervisor defaults.',
                fieldsHtml(chip), (el) => {
                    const sel = el.querySelector('#orchChipPlaybook');
                    const def = el.querySelector('#orchChipDefault');
                    const sum = el.querySelector('#orchChipSummary');
                    const paint = () => {
                        const pb = cache.playbooks.find((p) => p.id === sel.value);
                        sum.textContent = summary(pb);
                        def.checked = sel.value === cache.defaultId;
                        def.disabled = def.checked;
                    };
                    paint();
                    sel.addEventListener('change', () => {
                        // Picking the default leaves the chip bare so it follows later default changes.
                        Object.assign(chip, withPlaybook(chip, sel.value === cache.defaultId ? '' : sel.value));
                        paint();
                        if (h.rerender) h.rerender();
                    });
                    def.addEventListener('change', async () => {
                        if (!def.checked) return;
                        try {
                            const resp = await fetch('/api/orchestrator/default', {
                                method: 'PUT', credentials: 'include',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ id: sel.value }),
                            });
                            const data = await resp.json().catch(() => ({}));
                            if (!resp.ok || !data.success) throw new Error(data.error || 'Could not set the default');
                            cache.defaultId = data.default_id;
                            Object.assign(chip, withPlaybook(chip, ''));
                            paint();
                            if (h.rerender) h.rerender();
                        } catch (err) {
                            def.checked = false;
                            if (h.showToast) h.showToast(err.message, 'error');
                        }
                    });
                    el.querySelector('#orchChipEdit').addEventListener('click', () => {
                        if (h.close) h.close();
                        if (h.navigate) h.navigate('/orchestrator_page.html');
                    });
                });
        }

        return { isChip, label, open };
    }

    const api = { create, isChip, playbookOf, withPlaybook, labelFor };
    root.CuttleChatOrchestrateChip = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
