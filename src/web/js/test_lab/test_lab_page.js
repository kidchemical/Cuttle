(function () {
    'use strict';

    const projectSelect = document.getElementById('testLabProject');
    const refreshButton = document.getElementById('refreshTestLab');
    const runAllButton = document.getElementById('runAllTests');
    const notice = document.getElementById('testLabNotice');
    const content = document.getElementById('testLabContent');
    const overview = document.getElementById('testLabOverview');
    const suitesHost = document.getElementById('testLabSuites');
    const historyHost = document.getElementById('testLabHistory');
    const scanStatus = document.getElementById('testLabScanStatus');
    const outputSection = document.getElementById('testLabOutputSection');
    const output = document.getElementById('testLabOutput');
    const outputMeta = document.getElementById('testLabOutputMeta');
    const closeOutput = document.getElementById('closeTestLabOutput');
    const STORAGE_KEY = 'cuttleTestLabProject';
    let projects = [];
    let selectedProject = '';
    let activeRunId = '';
    let pollTimer = null;

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function showNotice(html, kind) {
        notice.hidden = false;
        notice.className = `test-lab-notice${kind ? ` is-${kind}` : ''}`;
        notice.innerHTML = html;
        content.hidden = true;
    }

    function showError(message) {
        showNotice(escapeHtml(message || 'Test Lab could not load.'), 'error');
    }

    function formatDate(timestamp) {
        if (!timestamp) return '—';
        const value = Number(timestamp);
        if (!Number.isFinite(value)) return '—';
        return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value * 1000));
    }

    function formatDuration(value) {
        const seconds = Math.max(0, Number(value) || 0);
        if (seconds < 60) return `${Math.round(seconds)}s`;
        const minutes = Math.floor(seconds / 60);
        return `${minutes}m ${Math.round(seconds % 60)}s`;
    }

    function statusClass(status) {
        return `test-lab-badge is-${String(status || 'unknown').replace(/[^a-z0-9_-]/gi, '')}`;
    }

    function latestRunFor(runs, suiteId) {
        const history = runs || [];
        const exact = history.find((run) => run.suite_id === suiteId) || null;
        const all = history.find((run) => run.suite_id === 'all') || null;
        if (!exact) return all;
        if (all && Number(all.started_at) > Number(exact.started_at)) return all;
        return exact;
    }

    function commandHint(suite) {
        if (suite.framework === 'pytest') return `python -m pytest -q ${suite.location}`;
        if (suite.framework === 'npm') return 'npm test';
        if (suite.framework === 'cargo') return 'cargo test';
        if (suite.framework === 'go') return 'go test ./...';
        return suite.framework;
    }

    function renderOverview(data) {
        const runs = data.runs || [];
        const complete = runs.find((run) => !['queued', 'running'].includes(run.status));
        const passed = runs.filter((run) => run.status === 'passed').length;
        const failed = runs.filter((run) => ['failed', 'timed_out'].includes(run.status)).length;
        const running = runs.some((run) => ['queued', 'running'].includes(run.status));
        const stats = [
            [data.suite_count || 0, 'recognized suites'],
            [data.test_file_count || 0, 'test files'],
            [running ? 'Running' : (complete ? complete.status.replace('_', ' ') : 'Not run'), 'latest result'],
            [`${passed} passed · ${failed} failed`, 'recent runs'],
        ];
        overview.innerHTML = stats.map(([value, label]) => `<div class="test-lab-stat"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join('');
        scanStatus.textContent = `Scanned ${formatDate(Date.now() / 1000)}`;
    }

    function renderSuites(data) {
        const suites = data.suites || [];
        const runs = data.runs || [];
        const busy = runs.some((run) => ['queued', 'running'].includes(run.status));
        runAllButton.disabled = !suites.length || busy;
        runAllButton.textContent = busy ? 'Tests running…' : 'Run all tests';
        if (!suites.length) {
            suitesHost.innerHTML = '<div class="test-lab-empty">No supported test suites were found. Test Lab currently recognizes Python pytest files, npm test scripts, Go modules, and Rust Cargo packages.</div>';
            return;
        }
        suitesHost.innerHTML = suites.map((suite) => {
            const latest = latestRunFor(runs, suite.id);
            const state = latest ? `<span class="${statusClass(latest.status)}">${escapeHtml(latest.status.replace('_', ' '))}</span>` : '<span class="test-lab-badge">Not run</span>';
            const count = `${Number(suite.test_files) || 0} test files`;
            const last = latest ? `Last run ${escapeHtml(formatDate(latest.started_at))}` : 'No run history';
            return `<article class="test-lab-suite">
                <div class="test-lab-suite-head"><div><h3>${escapeHtml(suite.label)}</h3><div class="test-lab-suite-meta"><span>${escapeHtml(suite.framework)}</span><span>${escapeHtml(count)}</span></div></div>${state}</div>
                <div class="test-lab-suite-command">${escapeHtml(commandHint(suite))}</div>
                <div class="test-lab-suite-meta"><span>${last}</span></div>
                <div style="margin-top:10px"><button type="button" data-run-suite="${escapeHtml(suite.id)}" ${busy ? 'disabled' : ''}>Run suite</button>${latest ? ` <button type="button" data-view-run="${escapeHtml(latest.id)}">View result</button>` : ''}</div>
            </article>`;
        }).join('');
    }

    function renderHistory(runs) {
        if (!runs || !runs.length) {
            historyHost.innerHTML = '<div class="test-lab-empty">Runs started from this page will appear here.</div>';
            return;
        }
        historyHost.innerHTML = runs.map((run) => `<div class="test-lab-run">
            <span class="test-lab-run-name" title="${escapeHtml(run.suite_label)}">${escapeHtml(run.suite_label)}</span>
            <span class="${statusClass(run.status)}">${escapeHtml(run.status.replace('_', ' '))}</span>
            <span class="test-lab-run-time">${escapeHtml(formatDate(run.started_at))}</span>
            <span class="test-lab-run-duration">${run.duration_s == null ? 'In progress' : escapeHtml(formatDuration(run.duration_s))}</span>
            <button type="button" data-view-run="${escapeHtml(run.id)}">Details</button>
        </div>`).join('');
    }

    function renderProject(data) {
        notice.hidden = true;
        content.hidden = false;
        renderOverview(data);
        renderSuites(data);
        renderHistory(data.runs || []);
    }

    async function loadProject() {
        if (!selectedProject) return;
        refreshButton.disabled = true;
        try {
            const response = await fetch(`/api/test-lab/projects/${encodeURIComponent(selectedProject)}`, { credentials: 'include', cache: 'no-store' });
            const data = await response.json();
            if (data.disabled) {
                showNotice(`${escapeHtml(data.error)} <a href="/settings_page.html?tab=experimental">Open Experimental settings</a>.`, 'disabled');
                projectSelect.disabled = true;
                refreshButton.disabled = true;
                runAllButton.disabled = true;
                return;
            }
            if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
            renderProject(data);
            if (activeRunId) {
                const active = (data.runs || []).find((run) => run.id === activeRunId);
                if (!active || !['queued', 'running'].includes(active.status)) {
                    activeRunId = '';
                    if (pollTimer) clearTimeout(pollTimer);
                    pollTimer = null;
                }
            }
        } catch (error) {
            showError(error.message || 'Could not load this project.');
        } finally {
            refreshButton.disabled = false;
        }
    }

    async function viewRun(runId) {
        try {
            const response = await fetch(`/api/test-lab/runs/${encodeURIComponent(runId)}`, { credentials: 'include', cache: 'no-store' });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Run details unavailable.');
            outputSection.hidden = false;
            output.textContent = data.run.output || data.run.error || 'No output was captured.';
            outputMeta.textContent = `${data.run.suite_label} · ${data.run.status}${data.run.duration_s == null ? '' : ` · ${formatDuration(data.run.duration_s)}`}`;
            outputSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (error) {
            showError(error.message);
        }
    }

    async function startRun(suiteId) {
        if (!selectedProject || activeRunId) return;
        runAllButton.disabled = true;
        try {
            const response = await fetch(`/api/test-lab/projects/${encodeURIComponent(selectedProject)}/runs`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ suite_id: suiteId }),
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
            const runId = data.run.id;
            activeRunId = runId;
            outputSection.hidden = false;
            output.textContent = 'Test run started. Waiting for runner output…';
            outputMeta.textContent = data.run.suite_label;
            await loadProject();
            if (activeRunId === runId) pollRun(runId);
            else await viewRun(runId);
        } catch (error) {
            showError(error.message || 'Could not start this test run.');
            runAllButton.disabled = false;
        }
    }

    async function pollRun(runId) {
        if (runId !== activeRunId) return;
        try {
            const response = await fetch(`/api/test-lab/runs/${encodeURIComponent(runId)}`, { credentials: 'include', cache: 'no-store' });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Run status unavailable.');
            const run = data.run;
            output.textContent = run.output || (run.status === 'queued' ? 'Queued…' : 'Tests are running…');
            outputMeta.textContent = `${run.suite_label} · ${run.status}${run.duration_s == null ? '' : ` · ${formatDuration(run.duration_s)}`}`;
            if (['queued', 'running'].includes(run.status)) {
                pollTimer = setTimeout(() => pollRun(runId), 1400);
            } else {
                activeRunId = '';
                await loadProject();
                await viewRun(runId);
            }
        } catch (error) {
            scanStatus.textContent = error.message || 'Run status unavailable';
            pollTimer = setTimeout(() => pollRun(runId), 2500);
        }
    }

    async function loadProjects() {
        try {
            const response = await fetch('/api/test-lab/projects', { credentials: 'include', cache: 'no-store' });
            const data = await response.json();
            if (data.disabled) {
                showNotice(`${escapeHtml(data.error)} <a href="/settings_page.html?tab=experimental">Open Experimental settings</a>.`, 'disabled');
                return;
            }
            if (!response.ok || !data.success) throw new Error(data.error || `HTTP ${response.status}`);
            projects = Array.isArray(data.projects) ? data.projects : [];
            projectSelect.replaceChildren();
            projects.forEach((project) => {
                const option = document.createElement('option');
                option.value = String(project.id);
                option.textContent = project.available ? project.name : `${project.name} · unavailable`;
                option.disabled = !project.available;
                projectSelect.appendChild(option);
            });
            if (!projects.length) {
                showNotice('No projects are registered yet. Add a project in <a href="/projects_page.html">Apps → Projects</a> to inspect its tests.', 'empty');
                return;
            }
            const stored = localStorage.getItem(STORAGE_KEY) || new URLSearchParams(location.search).get('project') || '';
            const preferred = projects.find((project) => String(project.id) === stored && project.available)
                || projects.find((project) => project.available);
            if (!preferred) {
                showNotice('None of your registered projects are available on this device. Repair project paths in <a href="/projects_page.html">Apps → Projects</a>.', 'empty');
                return;
            }
            selectedProject = String(preferred.id);
            projectSelect.value = selectedProject;
            projectSelect.disabled = false;
            refreshButton.disabled = false;
            await loadProject();
        } catch (error) {
            showError(error.message || 'Could not load registered projects.');
        }
    }

    projectSelect.addEventListener('change', () => {
        selectedProject = projectSelect.value;
        localStorage.setItem(STORAGE_KEY, selectedProject);
        activeRunId = '';
        if (pollTimer) clearTimeout(pollTimer);
        pollTimer = null;
        outputSection.hidden = true;
        loadProject();
    });
    refreshButton.addEventListener('click', loadProject);
    runAllButton.addEventListener('click', () => startRun('all'));
    suitesHost.addEventListener('click', (event) => {
        const button = event.target.closest('[data-run-suite]');
        if (button) startRun(button.dataset.runSuite);
    });
    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-view-run]');
        if (button) viewRun(button.dataset.viewRun);
    });
    closeOutput.addEventListener('click', () => { outputSection.hidden = true; });

    loadProjects();
})();
