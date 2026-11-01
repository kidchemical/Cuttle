/* Pure presentation model for the Agent Feed "Running now" tree.
   Input: GET /api/jobs/work-tree (schema_version 1, api.jobs.work_tree).
   No DOM, fetch or timers; feed_work.js renders and controls. */
(function(root) {
    'use strict';

    const STATE_LABELS = {
        running: 'Working',
        waiting: 'Waiting in background',
        watch: 'Job running',
        processes: 'Processes still running',
        scheduled: 'Supervising',
    };

    function handle(chat) {
        const s = String(chat == null ? '' : chat);
        return /^\d+$/.test(s) ? 'CH-' + s.padStart(6, '0') : s;
    }

    /** Epoch seconds from a number (epoch s) or an ISO timestamp. */
    function epoch(value) {
        if (value == null || value === '') return null;
        if (typeof value === 'number' && Number.isFinite(value)) return value;
        const parsed = Date.parse(String(value));
        return Number.isFinite(parsed) ? parsed / 1000 : null;
    }

    function elapsed(sec) {
        if (!(sec >= 0)) return '';
        const s = Math.floor(sec);
        if (s < 60) return s + 's';
        const m = Math.floor(s / 60);
        if (m < 60) return m + 'm';
        const h = Math.floor(m / 60);
        if (h < 24) return h + 'h ' + String(m % 60).padStart(2, '0') + 'm';
        return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
    }

    function since(value, nowSec) {
        const start = epoch(value);
        return start == null ? '' : elapsed(Math.max(0, nowSec - start));
    }

    function bytes(n) {
        const v = Number(n);
        if (!Number.isFinite(v) || v <= 0) return '';
        if (v >= 1024 ** 3) return (v / 1024 ** 3).toFixed(1) + ' GB';
        return Math.max(1, Math.round(v / 1024 ** 2)) + ' MB';
    }

    /** Command line without the interpreter path noise, bounded. */
    function shortCommand(cmdline, max) {
        const limit = max || 140;
        const parts = String(cmdline || '').trim().split(/\s+/).filter(Boolean);
        if (!parts.length) return '';
        parts[0] = parts[0].split(/[\\/]/).pop();
        const text = parts.join(' ');
        return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
    }

    function stateLabel(owner) {
        if (!owner) return '';
        if (owner.state === 'processes' && owner.detached_count) return 'Detached processes running';
        return STATE_LABELS[owner.state] || owner.state || '';
    }

    function countProcesses(nodes) {
        return (nodes || []).reduce((n, node) => n + 1 + countProcesses(node.children), 0);
    }

    function ownerBadges(owner) {
        const out = [];
        const n = Number(owner && owner.process_count) || 0;
        const d = Number(owner && owner.detached_count) || 0;
        if (n) out.push({ text: n + (n === 1 ? ' process' : ' processes'), kind: 'procs' });
        if (d) out.push({ text: d + ' detached', kind: 'detached' });
        const kids = (owner && owner.children) || [];
        if (kids.length) out.push({ text: kids.length + (kids.length === 1 ? ' sub-agent' : ' sub-agents'), kind: 'children' });
        return out;
    }

    /** Work items listed above the process tree. */
    function ownerLines(owner) {
        const lines = [];
        if (!owner) return lines;
        if (owner.run) {
            const r = owner.run;
            lines.push({ kind: 'run', text: [r.agent_id || 'Agent', r.model].filter(Boolean).join(' · ') + ' turn',
                started: r.started_at, query_id: r.query_id || '' });
        }
        (owner.waits || []).forEach((w) => lines.push({ kind: 'wait', text: w.label || 'Background wait',
            started: w.started_at, job_id: w.job_id || '' }));
        (owner.watches || []).forEach((w) => lines.push({ kind: 'watch', text: w.label || w.job_id,
            started: w.started_at, job_id: w.job_id || '',
            percent: Number.isFinite(Number(w.percent)) ? Math.max(0, Math.min(100, Number(w.percent))) : null }));
        (owner.schedules || []).forEach((s) => lines.push({ kind: 'schedule',
            text: [s.label, s.detail].filter(Boolean).join(' · '), id: s.id, status: s.status,
            target: s.target || '', cancellable: !!s.cancellable }));
        return lines;
    }

    /** A turn, wait or watch can be stopped through the chat's Stop path. */
    function canStop(owner) {
        return !!(owner && (owner.run || (owner.waits || []).length || (owner.watches || []).length));
    }

    function canEndAll(owner) {
        return canStop(owner) || !!(owner && owner.process_count);
    }

    function endAllPrompt(owner) {
        const parts = [];
        if (canStop(owner)) parts.push('stop its agent');
        const n = Number(owner.process_count) || 0;
        if (n) parts.push('end ' + n + (n === 1 ? ' process' : ' processes'));
        return 'End all work in ' + (owner.handle || handle(owner.chat_session_id)) + ': ' + parts.join(' and ') + '?';
    }

    function endProcessPrompt(node) {
        const kids = countProcesses(node.children);
        return 'End ' + (node.name || 'process') + ' (pid ' + node.pid + ')'
            + (kids ? ' and its ' + kids + (kids === 1 ? ' child process' : ' child processes') : '') + '?';
    }

    function totalsText(tree) {
        const t = (tree && tree.totals) || {};
        const parts = [];
        if (t.chats) parts.push(t.chats + (t.chats === 1 ? ' chat' : ' chats'));
        if (t.processes) parts.push(t.processes + (t.processes === 1 ? ' process' : ' processes'));
        if (t.detached) parts.push(t.detached + ' detached');
        const mesh = (tree && tree.mesh) || [];
        if (mesh.length) parts.push(mesh.length + ' mesh ' + (mesh.length === 1 ? 'job' : 'jobs'));
        return parts.join(' · ');
    }

    /** Structural signature: re-render only when this changes (times tick in place). */
    function signature(tree) {
        const owner = (o) => [o.chat_session_id, o.state, o.label, o.title, o.process_count, o.detached_count,
            (o.run || {}).query_id, ownerLines(o).map((l) => [l.kind, l.text, l.percent]),
            proc(o.processes), (o.children || []).map(owner)];
        const proc = (nodes) => (nodes || []).map((n) => [n.pid, n.name, n.detached, n.parent_chat, proc(n.children)]);
        return JSON.stringify([(tree && tree.owners || []).map(owner),
            (tree && tree.mesh || []).map((m) => [m.id, m.status, m.label])]);
    }

    function isEmpty(tree) {
        return !((tree && tree.owners || []).length || (tree && tree.mesh || []).length);
    }

    root.CuttleAgentWork = {
        STATE_LABELS, handle, epoch, elapsed, since, bytes, shortCommand, stateLabel, countProcesses,
        ownerBadges, ownerLines, canStop, canEndAll, endAllPrompt, endProcessPrompt, totalsText,
        signature, isEmpty,
    };
})(typeof window === 'undefined' ? globalThis : window);
