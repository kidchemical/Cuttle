/**
 * Jobs ledger view-model (pure).
 *
 * Owner: Jobs surface. Turns unified job rows from GET /api/jobs into display
 * rows and HTML fragments; never touches DOM, transport, or page globals.
 * The page owns mounting/polling/actions.
 */
window.CuttleJobsLedger = (function () {

    const STATUS_LABELS = {
        queued: 'queued',
        claimed: 'claimed',
        starting: 'starting',
        running: 'running',
        succeeded: 'succeeded',
        failed: 'failed',
        cancelled: 'cancelled',
        expired: 'expired',
    };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function fmtDuration(s) {
        if (s == null || !isFinite(s)) return '';
        const secs = Math.max(0, Math.round(s));
        if (secs < 60) return secs + 's';
        if (secs < 3600) return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
        if (secs < 86400) return Math.floor(secs / 3600) + 'h ' + Math.floor((secs % 3600) / 60) + 'm';
        return Math.floor(secs / 86400) + 'd ' + Math.floor((secs % 86400) / 3600) + 'h';
    }

    function fmtAgo(stamp) {
        if (stamp == null || !isFinite(Number(stamp))) return '';
        const diff = Math.floor(Date.now() / 1000 - Number(stamp));
        if (diff < 0) return 'just now';
        if (diff < 60) return diff + 's ago';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    }

    function fmtCost(c) {
        if (c == null || !isFinite(c)) return '';
        const v = Number(c);
        if (v === 0) return '';
        return v < 1 ? ('$' + v.toFixed(4)) : ('$' + v.toFixed(2));
    }

    function statusClass(status) {
        return String(status || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '');
    }

    /** Normalize one unified job row into a display row. */
    function display(job) {
        job = job || {};
        const meta = job.meta || {};
        const links = job.links || {};
        const status = STATUS_LABELS[job.status] || job.status || 'unknown';
        return {
            kind: job.kind === 'mesh' ? 'mesh' : 'run',
            id: job.id,
            shortId: job.short_id || String(job.id || '').slice(0, 8),
            label: job.label || meta.type || 'job',
            status: status,
            statusClass: statusClass(status),
            when: fmtAgo(job.started_at),
            duration: fmtDuration(job.duration_s),
            target: job.target || '',
            model: meta.model || '',
            cost: meta.cost != null ? fmtCost(meta.cost) : '',
            detail: (meta.error && String(meta.error)) || meta.detail || '',
            chatLink: links.chat || null,
            queryLogLink: links.query_log || null,
            meshLink: links.mesh || null,
            actions: Array.isArray(job.actions) ? job.actions : [],
            active: job.status === 'running' || job.status === 'claimed' || job.status === 'queued',
        };
    }

    function renderKindChip(kind, label) {
        return '<span class="job-type-badge ' + esc(kind) + '">' + esc(kind === 'run' ? (label || 'agent') : 'mesh') + '</span>';
    }

    function statusPill(status) {
        const cls = statusClass(status);
        return '<span class="status-pill ' + esc(cls) + '">' + esc(status || '—') + '</span>';
    }

    function actionButtons(row) {
        if (!row.actions || !row.actions.length) return '';
        const parts = [];
        if (row.actions.indexOf('cancel') >= 0) {
            parts.push(
                '<button type="button" class="jobs-pager-btn" data-job-cancel="' + esc(row.id) + '"' +
                ' data-job-kind="' + esc(row.kind) + '">Stop</button>'
            );
        }
        if (row.actions.indexOf('rerun') >= 0) {
            parts.push(
                '<button type="button" class="jobs-pager-btn" data-job-rerun="' + esc(row.id) + '">Rerun</button>'
            );
        }
        return parts.join(' ');
    }

    function nameCell(row) {
        if (row.kind === 'run') {
            if (row.chatLink) {
                return '<a href="' + esc(row.chatLink) + '" class="hist-pr">' + esc(row.label) + '</a>';
            }
            if (row.queryLogLink) {
                return '<a href="' + esc(row.queryLogLink) + '" class="hist-pr">' + esc(row.label) + '</a>';
            }
            return esc(row.label);
        }
        return esc(row.label);
    }

    /**
     * Render a unified jobs table.
     * opts: { empty, capped, showKind }
     */
    function renderTable(jobs, opts) {
        opts = opts || {};
        const rows = (jobs || []).map(display).map(function (r) {
            const detail = r.detail || r.model || r.target;
            return '<tr>' +
                '<td>' + esc(r.kind) + '</td>' +
                '<td class="jobs-cell-wrap">' + nameCell(r) + '</td>' +
                '<td>' + statusPill(r.status) + '</td>' +
                '<td>' + esc(r.when) + '</td>' +
                '<td>' + esc(r.duration) + '</td>' +
                '<td class="jobs-cell-wrap">' + (detail ? esc(detail) : '—') + '</td>' +
                (r.kind === 'run' ? '<td>' + esc(r.cost) + '</td>' : '<td>—</td>') +
                '<td class="device-actions-cell">' + actionButtons(r) + '</td>' +
                '</tr>';
        }).join('');
        if (!rows) {
            return '<div class="jobs-empty">' + esc(opts.empty || 'Nothing yet.') + '</div>';
        }
        const costTh = '<th>Cost</th>';
        return '<div class="jobs-table-block"><div class="jobs-table-scroll' + (opts.capped ? ' jobs-table-scroll--capped' : '') + '">' +
            '<table class="hist-table"><thead><tr>' +
            '<th>Source</th><th>Job</th><th>Status</th><th>When</th><th>Duration</th><th>Detail</th>' + costTh +
            '<th class="device-actions-cell" aria-label="Actions"></th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
    }

    /** Running-now strip (unified in-flight jobs). */
    function renderRunning(jobs) {
        const rows = (jobs || []).map(display).map(function (r) {
            const pulse = r.status === 'running' ? '<span class="status-indicator status-running"></span>' : '';
            return '<div class="job-card running">' +
                '<div class="job-main">' +
                '<div class="job-row-top">' +
                '<div class="job-title-compact"><span class="job-name">' + esc(r.label) + '</span>' + statusPill(r.status) + '</div>' +
                '</div>' +
                '<div class="job-meta">' +
                '<span>' + esc(r.kind) + '</span>' +
                '<span>' + pulse + esc(r.when) + '</span>' +
                (r.duration ? '<span>' + esc(r.duration) + '</span>' : '') +
                (r.model ? '<span>' + esc(r.model) + '</span>' : '') +
                '</div>' +
                '</div>' +
                '<div class="job-actions">' + actionButtons(r) + '</div>' +
                '</div>';
        }).join('');
        if (!rows) {
            return '<div class="running-now-empty">No jobs running right now.</div>';
        }
        return rows;
    }

    /** Telemetry pane. */
    function renderStats(stats, opts) {
        opts = opts || {};
        stats = stats || {};
        const runs = stats.runs || {};
        const mesh = stats.mesh || {};
        const dur = stats.duration || {};
        function card(label, value, sub) {
            return '<div class="jobs-stat">' +
                '<div class="jobs-stat-value">' + esc(value == null ? '—' : value) + '</div>' +
                '<div class="jobs-stat-label">' + esc(label) + (sub ? '<div class="jobs-stat-sub">' + esc(sub) + '</div>' : '') + '</div>' +
                '</div>';
        }
        const byAgent = (stats.by_agent || []).slice(0, 6).map(function (b) {
            return '<div class="jobs-stat">' +
                '<div class="jobs-stat-value">' + esc(b.count) + '</div>' +
                '<div class="jobs-stat-label">' + esc(b.agent) +
                '<div class="jobs-stat-sub">avg ' + esc(b.avg_duration_s != null ? fmtDuration(b.avg_duration_s) : '—') +
                (b.total_cost ? ' · ' + fmtCost(b.total_cost) : '') + '</div></div></div>';
        }).join('');
        let html =
            '<div class="jobs-stats-grid">' +
            card('Runs', runs.total, (runs.window_hours ? 'last ' + runs.window_hours + 'h' : '')) +
            card('Running', runs.running || 0) +
            card('Success', (runs.success_rate != null ? Math.round(runs.success_rate * 100) + '%' : '—'), runs.succeeded + ' ok / ' + (runs.failed || 0) + ' failed') +
            card('Cost', (runs.cost ? fmtCost(runs.cost) : '—')) +
            card('Mesh active', mesh.active || 0, mesh.succeeded + ' done') +
            card('Duration avg', dur.avg_s != null ? fmtDuration(dur.avg_s) : '—', 'p95 ' + (dur.p95_s != null ? fmtDuration(dur.p95_s) : '—')) +
            '</div>';
        if (byAgent) {
            html += '<h3 class="jobs-section-title" style="margin-top:14px;">By agent</h3><div class="jobs-stats-grid">' + byAgent + '</div>';
        }
        return html;
    }

    return {
        display: display,
        renderRunning: renderRunning,
        renderTable: renderTable,
        renderStats: renderStats,
        fmtDuration: fmtDuration,
        fmtAgo: fmtAgo,
        fmtCost: fmtCost,
    };
})();