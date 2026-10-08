/* Gizmos — pure model + markup (experimental flag `gizmos`, owner api.gizmos).
 * No DOM, no fetch, no timers: the shell controller, the Gizmos App page and
 * the desktop pop-out all render through these functions. */
(function (root) {
    'use strict';
    const DOCKS = ['titlebar', 'rail', 'float', 'popout'];
    const DOCK_LABELS = {
        titlebar: 'Title bar',
        rail: 'Blade bar',
        float: 'Floating (every Space)',
        popout: 'Desktop window (always on top)',
    };
    const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));

    function clampPct(v) {
        const n = Number(v);
        return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
    }

    /** 3h 12m · 2d 4h · 14m · <1m */
    function formatDuration(seconds) {
        const s = Math.floor(Number(seconds) || 0);
        if (s <= 0) return 'now';
        const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
        if (d) return d + 'd' + (h ? ' ' + h + 'h' : '');
        if (h) return h + 'h' + (m ? ' ' + m + 'm' : '');
        return m ? m + 'm' : '<1m';
    }

    function relative(at, nowMs) {
        if (!at) return '';
        const secs = Number(at) - (Number(nowMs) || Date.now()) / 1000;
        return secs <= 0 ? 'now' : 'in ' + formatDuration(secs);
    }

    function absolute(at) {
        if (!at) return '';
        try {
            return new Date(Number(at) * 1000).toLocaleString([], {
                weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
            });
        } catch (_) { return ''; }
    }

    function toneFor(remaining, blocked) {
        if (blocked) return 'blocked';
        if (remaining == null) return 'unknown';
        if (remaining <= 10) return 'critical';
        if (remaining <= 25) return 'low';
        return 'ok';
    }

    /** The window a meter shows: a configured id, else the tightest (most used). */
    function pickWindow(data, config) {
        const windows = (data && Array.isArray(data.windows) ? data.windows : []).filter(Boolean);
        if (!windows.length) return null;
        const want = config && config.window && config.window !== 'tightest' ? config.window : '';
        if (want) {
            const hit = windows.find(w => w.id === want);
            if (hit) return hit;
        }
        return windows.reduce((a, b) => (Number(b.used_percent) > Number(a.used_percent) ? b : a));
    }

    /** Shared fetch key: gizmos showing the same source share one request. */
    function dataKey(gizmo) {
        if (!gizmo) return '';
        if (gizmo.type === 'usage_meter') return 'usage:' + String((gizmo.config || {}).agent || '');
        return 'gizmo:' + gizmo.id;
    }

    function meterModel(gizmo, data, nowMs) {
        const cfg = (gizmo && gizmo.config) || {};
        const show = cfg.show === 'used' ? 'used' : 'remaining';
        const title = (gizmo && gizmo.title) || 'Usage';
        const agentLabel = (data && data.label) || String(cfg.agent || 'Agent');
        const base = {
            title, agentLabel, show, pct: 0, value: '…', valueLabel: show === 'used' ? 'used' : 'left',
            caption: '', resetText: '', rows: [], tone: 'unknown', state: 'loading',
            error: '', stale: false, plan: '', updatedAt: null, unblockAt: null, extras: {},
            notifyArmed: !!cfg.notify_on_unblock,
        };
        if (!data) return base;
        const windows = Array.isArray(data.windows) ? data.windows : [];
        if (!windows.length) {
            return { ...base, state: 'error', value: '—', error: data.error || 'No usage windows reported',
                resetText: data.error || 'No usage windows reported', plan: data.plan || '',
                updatedAt: data.updated_at || null };
        }
        const win = pickWindow(data, cfg);
        const remaining = clampPct(win.remaining_percent);
        const pct = show === 'used' ? 100 - remaining : remaining;
        let resetText = '';
        if (data.blocked && data.unblock_at) resetText = 'Unblocks ' + relative(data.unblock_at, nowMs);
        else if (data.blocked) resetText = 'Limit reached';
        else if (win.reset_at) resetText = win.label + ' resets ' + relative(win.reset_at, nowMs);
        if (data.stale) {
            const now = (nowMs == null ? Date.now() : nowMs) / 1000;
            if (windows.some(w => w.reset_at && w.reset_at <= now))
                resetText = 'Scheduled reset passed; awaiting verification';
            else resetText = 'Expected: ' + resetText;
        }
        const rows = windows.map(w => {
            const left = clampPct(w.remaining_percent);
            return {
                id: w.id, label: w.label, remaining: left, used: 100 - left,
                pct: show === 'used' ? 100 - left : left,
                resetText: w.reset_at ? relative(w.reset_at, nowMs) : '',
                resetAbs: absolute(w.reset_at),
                tone: toneFor(left, left <= 0 && !!data.blocked),
                active: w.id === win.id,
            };
        });
        return {
            ...base, pct, value: Math.round(pct) + '%', caption: win.label, resetText, rows,
            tone: toneFor(remaining, !!data.blocked), state: data.blocked ? 'blocked' : 'ok',
            error: data.error || '', stale: !!data.stale, plan: data.plan || '',
            updatedAt: data.updated_at || null, unblockAt: data.unblock_at || null,
            extras: data.extras || {},
        };
    }

    function tooltipText(model) {
        const lines = [model.title];
        if (model.plan) lines.push('Plan: ' + model.plan);
        model.rows.forEach(r => lines.push(r.label + ': ' + Math.round(r.remaining) + '% left'
            + (r.resetText ? ' · resets ' + r.resetText : '')));
        if (model.resetText && model.state === 'blocked') lines.push(model.resetText);
        if (model.error) lines.push('⚠ ' + model.error);
        return lines.join('\n');
    }

    function ringSvg(pct, size) {
        const r = (size / 2) - 3, c = 2 * Math.PI * r, dash = (clampPct(pct) / 100) * c;
        return '<svg class="gizmo-ring" viewBox="0 0 ' + size + ' ' + size + '" width="' + size + '" height="' + size
            + '" aria-hidden="true"><circle class="gizmo-ring-track" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r
            + '"/><circle class="gizmo-ring-fill" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r
            + '" stroke-dasharray="' + dash.toFixed(2) + ' ' + c.toFixed(2) + '" transform="rotate(-90 '
            + size / 2 + ' ' + size / 2 + ')"/></svg>';
    }

    function bar(pct, cls) {
        return '<span class="gizmo-bar ' + (cls || '') + '"><span class="gizmo-bar-fill" style="width:'
            + clampPct(pct).toFixed(1) + '%"></span></span>';
    }

    function shortLabel(label) {
        const s = String(label || '').replace(/\s+code$/i, '').trim();
        return s.length > 7 ? s.slice(0, 6) + '…' : s;
    }

    function renderRows(model) {
        return '<div class="gizmo-rows">' + model.rows.map(r =>
            '<div class="gizmo-row tone-' + r.tone + (r.active ? ' is-active' : '') + '">'
            + '<span class="gizmo-row-label">' + escape(r.label) + '</span>'
            + bar(r.pct, 'gizmo-bar--row')
            + '<span class="gizmo-row-value">' + Math.round(r.pct) + '%</span>'
            + '<span class="gizmo-row-reset" title="' + escape(r.resetAbs) + '">'
            + escape(r.resetText ? 'resets ' + r.resetText : '') + '</span></div>').join('') + '</div>';
    }

    /** variant: titlebar | rail | float | popout | card */
    function renderUsageMeter(gizmo, model, variant) {
        const cls = 'gizmo gizmo-usage gizmo--' + variant + ' tone-' + model.tone
            + (model.stale ? ' is-stale' : '') + ' state-' + model.state;
        const attrs = ' data-gizmo-id="' + escape(gizmo.id) + '" data-gizmo-type="usage_meter" title="'
            + escape(tooltipText(model)) + '"';
        if (variant === 'titlebar') {
            return '<div class="' + cls + '"' + attrs + '>'
                + '<span class="gizmo-agent">' + escape(shortLabel(model.agentLabel)) + '</span>'
                + bar(model.pct)
                + '<span class="gizmo-value">' + escape(model.value) + '</span>'
                + (model.state === 'blocked' && model.unblockAt
                    ? '<span class="gizmo-reset">' + escape(relative(model.unblockAt).replace(/^in /, '')) + '</span>' : '')
                + '</div>';
        }
        if (variant === 'rail') {
            return '<div class="' + cls + '"' + attrs + '>'
                + '<span class="gizmo-ring-wrap">' + ringSvg(model.pct, 36)
                + '<span class="gizmo-ring-value">' + escape(model.state === 'loading' ? '…' : Math.round(model.pct)) + '</span></span>'
                + '<span class="gizmo-agent">' + escape(shortLabel(model.agentLabel)) + '</span>'
                + '</div>';
        }
        const head = '<div class="gizmo-head"><span class="gizmo-title">' + escape(model.title) + '</span>'
            + '<span class="gizmo-value">' + escape(model.value)
            + (model.state === 'ok' || model.state === 'blocked' ? ' <small>' + escape(model.valueLabel) + '</small>' : '')
            + '</span></div>';
        const foot = '<div class="gizmo-foot">'
            + '<span class="gizmo-reset">' + escape(model.resetText || model.caption) + '</span>'
            + (model.stale ? '<span class="gizmo-stale" title="' + escape(model.error) + '">stale</span>' : '')
            + '</div>';
        const rows = variant === 'card' && model.rows.length ? renderRows(model) : '';
        return '<div class="' + cls + '"' + attrs + '>' + head + bar(model.pct, 'gizmo-bar--main') + foot + rows + '</div>';
    }

    function renderGizmoHtml(gizmo, data, variant, nowMs) {
        if (gizmo && gizmo.type === 'usage_meter') {
            return renderUsageMeter(gizmo, meterModel(gizmo, data, nowMs), variant || 'float');
        }
        return '<div class="gizmo gizmo--' + escape(variant) + ' tone-unknown" data-gizmo-id="'
            + escape(gizmo && gizmo.id) + '"><span class="gizmo-title">Unknown gizmo</span></div>';
    }

    /** Detail popover body (shell click / right-click). */
    function renderDetailHtml(gizmo, data, nowMs) {
        if (!gizmo || gizmo.type !== 'usage_meter') return '<div class="gizmo-detail">Unknown gizmo</div>';
        const model = meterModel(gizmo, data, nowMs);
        const extras = model.extras || {};
        const notes = [];
        if (model.plan) notes.push('Plan: ' + model.plan);
        if (extras.resets_available) notes.push(extras.resets_available + ' saved reset' + (extras.resets_available === 1 ? '' : 's'));
        if (extras.credits) notes.push('Credits: ' + extras.credits);
        if (extras.note) notes.push(extras.note);
        const status = model.state === 'blocked'
            ? '<div class="gizmo-detail-status tone-blocked">Blocked' + (model.unblockAt
                ? ' · unblocks ' + escape(relative(model.unblockAt, nowMs)) + ' (' + escape(absolute(model.unblockAt)) + ')' : '') + '</div>'
            : '';
        const updated = model.updatedAt
            ? 'Updated ' + new Date(model.updatedAt * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
        const armed = model.notifyArmed
            ? '<div class="gizmo-detail-notes">Will notify when this account unblocks.</div>' : '';
        return '<div class="gizmo-detail">'
            + '<div class="gizmo-detail-title">' + escape(model.title) + '</div>'
            + status
            + (model.rows.length ? renderRows(model) : '<div class="gizmo-detail-empty">' + escape(model.error || 'Loading…') + '</div>')
            + (notes.length ? '<div class="gizmo-detail-notes">' + notes.map(escape).join(' · ') + '</div>' : '')
            + armed
            + '<div class="gizmo-detail-meta">' + escape(updated)
            + (model.error && model.rows.length ? ' · ⚠ ' + escape(model.error) : '') + '</div>'
            + '</div>';
    }

    /** Where a gizmo actually renders on this surface. */
    function effectiveDock(placement, env) {
        const dock = DOCKS.includes(placement && placement.dock) ? placement.dock : 'titlebar';
        const e = env || {};
        if (dock === 'titlebar' && !e.titlebar) return 'rail';
        if (dock === 'popout' && !e.popout) return 'float';
        return dock;
    }

    function sortForDock(gizmos, dock, env) {
        return (gizmos || []).filter(g => effectiveDock(g.placement, env) === dock)
            .slice().sort((a, b) => (Number(a.placement.order) || 0) - (Number(b.placement.order) || 0)
                || String(a.id).localeCompare(String(b.id)));
    }

    /** Order value that lands between two neighbours (null = open end). */
    function orderBetween(prev, next) {
        const p = prev == null ? null : Number(prev), n = next == null ? null : Number(next);
        if (p == null && n == null) return 0;
        if (p == null) return n - 1;
        if (n == null) return p + 1;
        return (p + n) / 2;
    }

    const api = {
        DOCKS, DOCK_LABELS, escape, clampPct, formatDuration, relative, absolute, toneFor,
        pickWindow, dataKey, meterModel, tooltipText, renderGizmoHtml, renderDetailHtml,
        effectiveDock, sortForDock, orderBetween,
    };
    root.CuttleGizmos = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
