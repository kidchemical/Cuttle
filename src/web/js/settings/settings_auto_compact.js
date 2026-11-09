/* Settings → Agents → Auto-compaction (api.auto_compact; flag `auto_compact`).
   `CuttleSettingsAutoCompact.mount(container)` reads /api/settings/auto-compact
   and builds the form; `refresh()` re-checks availability after an in-page
   flag change. The section starts hidden and stays hidden while the flag is
   off or the read fails; saved values are kept while hidden. */
(function (root) {
    'use strict';

    const SCOPES = [
        ['chats', 'Ordinary chats', 'Chats you drive yourself.'],
        ['managed', 'Agent-driven chats', 'Supervised chats, supervisor chats, sub-agent children and orchestrator-bound chats. Usually a lower threshold: nobody is watching their window fill.'],
        ['milestone', 'At a Tasks milestone', 'When an item of the chat’s own Tasks list was checked off since its last turn, compact at this lower threshold instead: a finished step is the cleanest boundary.'],
    ];
    const MODES = [
        ['auto', 'Auto — the CLI’s own compact; a fresh session if that did not help'],
        ['native', 'Native — always the CLI’s own compact'],
        ['fresh', 'Fresh — new session with the Cuttle briefing and recent transcript'],
    ];

    let container = null;
    let generation = 0;

    function el(tag, text, cls) {
        const n = document.createElement(tag);
        if (text) n.textContent = text;
        if (cls) n.className = cls;
        return n;
    }

    function info(label, text) {
        const b = el('button', 'i', 'cuttle-info');
        b.type = 'button';
        b.setAttribute('data-tooltip', text);
        b.setAttribute('aria-label', 'About ' + label);
        return b;
    }

    function toggle(label, checked) {
        const wrap = el('label', '', 'toggle-switch' + (checked ? ' active' : ''));
        const input = el('input');
        input.type = 'checkbox';
        input.checked = !!checked;
        input.style.display = 'none';
        wrap.setAttribute('role', 'switch');
        wrap.tabIndex = 0;
        wrap.setAttribute('aria-label', label);
        wrap.setAttribute('aria-checked', String(!!checked));
        const flip = (ev) => {
            ev.preventDefault();
            input.checked = !input.checked;
            wrap.classList.toggle('active', input.checked);
            wrap.setAttribute('aria-checked', String(input.checked));
        };
        wrap.addEventListener('click', flip);
        wrap.addEventListener('keydown', (ev) => { if (ev.key === ' ' || ev.key === 'Enter') flip(ev); });
        wrap.append(input, el('span', '', 'slider'));
        return { wrap, input };
    }

    function number(label, value, min, max, step, suffix) {
        const span = el('span', '', 'setting-control-inline');
        const input = el('input', '', 'setting-input');
        input.type = 'number';
        input.min = String(min);
        input.max = String(max);
        input.step = String(step);
        input.inputMode = 'numeric';
        input.value = value == null ? '' : String(value);
        input.setAttribute('aria-label', label);
        input.style.width = '6em';
        span.append(input, el('span', ' ' + suffix, 'setting-description'));
        return { span, input };
    }

    /** Settings patch from the form's plain values. Pure. */
    function patchFrom(values) {
        const out = {};
        for (const [scope] of SCOPES) {
            const v = values[scope] || {};
            out[scope] = {
                enabled: !!v.enabled,
                percent: Math.round(Number(v.percent)),
                max_tokens: Math.max(0, Math.round(Number(v.max_k || 0) * 1000)),
            };
        }
        out.mode = values.mode;
        out.quiet_seconds = Math.round(Number(values.quiet_seconds || 0));
        return out;
    }

    function build(data) {
        const s = data.settings;
        const lim = data.limits || {};
        const form = el('form', '', 'auto-compact-settings');
        const fields = {};
        for (const [scope, title, help] of SCOPES) {
            const row = el('div', '', 'setting-item');
            const label = el('div', '', 'setting-label');
            label.append(el('strong', title), info(title, help));
            const control = el('div', '', 'setting-control');
            const t = toggle(title + ' auto-compact', s[scope].enabled);
            const range = scope === 'milestone' ? (lim.milestone_percent || [10, 95]) : (lim.percent || [20, 95]);
            const pct = number(title + ' threshold percent', s[scope].percent, range[0], range[1], 5, '%');
            const cap = number(title + ' token cap (thousands)', Math.round(s[scope].max_tokens / 1000), 0, 10000, 10, 'k tokens (0 = no cap)');
            control.append(t.wrap, pct.span, el('span', '\u00a0or\u00a0', 'setting-description'), cap.span);
            row.append(label, control);
            form.append(row);
            fields[scope] = { enabled: t.input, percent: pct.input, max_k: cap.input };
        }
        const modeRow = el('div', '', 'setting-item');
        const modeLabel = el('div', '', 'setting-label');
        modeLabel.append(el('strong', 'How'), info('How', 'Agents without a headless compact (Antigravity) manage their own window and are left alone in Auto and Native.'));
        const mode = el('select', '', 'setting-input');
        mode.setAttribute('aria-label', 'Auto-compact method');
        for (const [v, text] of MODES) {
            const o = el('option', text);
            o.value = v;
            o.selected = s.mode === v;
            mode.append(o);
        }
        const modeControl = el('div', '', 'setting-control');
        modeControl.append(mode);
        modeRow.append(modeLabel, modeControl);
        const quietRow = el('div', '', 'setting-item');
        const quietLabel = el('div', '', 'setting-label');
        quietLabel.append(el('strong', 'Quiet period'), info('Quiet period', 'How long after a turn ends Cuttle waits before compacting, so a message you send right away starts the next turn instead of waiting for a compact. Sub-agent children compact just before their next turn instead.'));
        const quiet = number('Quiet period seconds', s.quiet_seconds, (lim.quiet_seconds || [0])[0], (lim.quiet_seconds || [0, 600])[1], 5, 'seconds after a turn');
        const quietControl = el('div', '', 'setting-control');
        quietControl.append(quiet.span);
        quietRow.append(quietLabel, quietControl);
        const actions = el('div', '', 'provider-picker');
        const save = el('button', 'Save auto-compaction', 'control-button btn-start');
        save.type = 'submit';
        actions.append(save);
        const status = el('div', '', 'setting-description');
        status.setAttribute('role', 'status');
        form.append(modeRow, quietRow, actions, status);
        form.addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const values = { mode: mode.value, quiet_seconds: quiet.input.value };
            for (const [scope] of SCOPES) {
                values[scope] = { enabled: fields[scope].enabled.checked, percent: fields[scope].percent.value,
                    max_k: fields[scope].max_k.value };
            }
            save.disabled = true;
            status.textContent = 'Saving…';
            try {
                const r = await fetch('/api/settings/auto-compact', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(patchFrom(values)),
                });
                const d = await r.json();
                if (!d.success) throw new Error(d.error || 'Save failed');
                status.textContent = 'Saved.';
            } catch (e) {
                status.textContent = 'Could not save: ' + (e && e.message ? e.message : e);
            } finally {
                save.disabled = false;
            }
        });
        return form;
    }

    async function mount(target, refresh) {
        if (!target || (target.dataset.mounted && !refresh)) return;
        container = target;
        const section = document.getElementById('autoCompactSettings');
        const mine = ++generation;
        const hide = () => {
            if (section) section.hidden = true;
            target.replaceChildren();
            delete target.dataset.mounted;
        };
        try {
            const r = await fetch('/api/settings/auto-compact', { cache: 'no-store' });
            const d = await r.json();
            if (mine !== generation) return;
            if (!d || d.success !== true || d.available !== true) { hide(); return; }
            if (section) section.hidden = false;
            if (target.dataset.mounted) return; // keep an unsaved draft
            target.dataset.mounted = 'true';
            target.replaceChildren(build(d));
        } catch (_) {
            if (mine === generation) hide();
        }
    }

    function refresh() { return mount(container || document.getElementById('autoCompactEditor'), true); }

    const api = { mount, refresh, patchFrom };
    root.CuttleSettingsAutoCompact = api;
    if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
})(globalThis);
