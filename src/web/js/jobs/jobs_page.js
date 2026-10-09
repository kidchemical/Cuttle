/**
 * Jobs page orchestrator.
 *
 * The execution ledger: every piece of work Cuttle runs — local agent turns,
 * background helpers and mesh jobs — shown as one running-now strip + recent
 * list with Stop/Rerun control. Fleet admin lives on the Mesh page; deep
 * telemetry lives in Dashboards. Pure view-model: jobs_ledger.js.
 */

let recentJobs = [];
let runningJobs = [];
let jobsStats = null;
let pollTimer = null;

function setHtmlIfChanged(el, html) {
    if (!el) return;
    if (el.innerHTML !== html) el.innerHTML = html;
}

function hideJobsSurface() {
    ['jobsSummary', 'runningJobsSection', 'recentJobsSection'].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
}

async function loadJobs() {
    try {
        const [jr, ir] = await Promise.all([
            fetch('/api/jobs?limit=100', { cache: 'no-store', credentials: 'include' }),
            fetch('/api/jobs/in-flight', { cache: 'no-store', credentials: 'include' }),
        ]);
        if (!jr.ok || !ir.ok) {
            hideJobsSurface();
            return;
        }
        const jdata = await jr.json().catch(() => ({}));
        const idata = await ir.json().catch(() => ({}));
        recentJobs = jdata.jobs || [];
        runningJobs = idata.executing_jobs || [];
        renderJobs();
        renderRunningJobs();
    } catch (e) {
        hideJobsSurface();
    }
}

async function loadStats() {
    try {
        const r = await fetch('/api/jobs/stats?hours=24', { cache: 'no-store', credentials: 'include' });
        if (!r.ok) return;
        jobsStats = await r.json().catch(() => null);
        renderSummary();
    } catch (e) {}
}

function renderSummary() {
    const el = document.getElementById('jobsSummary');
    if (!el || !jobsStats) return;
    setHtmlIfChanged(el, window.CuttleJobsLedger.renderSummary(jobsStats));
}

function renderRunningJobs() {
    const el = document.getElementById('runningJobsList');
    if (!el) return;
    setHtmlIfChanged(el, window.CuttleJobsLedger.renderRunning(runningJobs));
}

function renderJobs() {
    const el = document.getElementById('recentJobsList');
    if (!el) return;
    setHtmlIfChanged(el, window.CuttleJobsLedger.renderTable(recentJobs, {
        capped: true,
        empty: 'No executions recorded yet.',
    }));
}

async function cancelUnifiedJob(kind, jobId) {
    const jid = String(jobId || '').trim();
    const k = String(kind || '').trim();
    if (!jid || !k) return;
    try {
        const r = await fetch('/api/jobs/' + encodeURIComponent(k) + '/' + encodeURIComponent(jid) + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
        if (window.showToast) window.showToast('Stopped ' + jid.slice(0, 8), 'success');
        await loadAll();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
    }
}

async function rerunUnifiedJob(jobId) {
    const jid = String(jobId || '').trim();
    if (!jid) return;
    try {
        const r = await fetch('/api/jobs/mesh/' + encodeURIComponent(jid) + '/rerun', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
        if (window.showToast) window.showToast('Reran as ' + String(data.job.id || '').slice(0, 8), 'success');
        await loadAll();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
    }
}

function schedulePoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(loadAll, 4000);
}

async function loadAll() {
    await loadJobs();
    await loadStats();
}

['runningJobsList', 'recentJobsList'].forEach(function (id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', function (ev) {
        const cancelBtn = ev.target && ev.target.closest && ev.target.closest('[data-job-cancel]');
        if (cancelBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            cancelUnifiedJob(cancelBtn.getAttribute('data-job-kind'), cancelBtn.getAttribute('data-job-cancel'));
            return;
        }
        const rerunBtn = ev.target && ev.target.closest && ev.target.closest('[data-job-rerun]');
        if (rerunBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            rerunUnifiedJob(rerunBtn.getAttribute('data-job-rerun'));
        }
    });
});

loadAll();
schedulePoll();