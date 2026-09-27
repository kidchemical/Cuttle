/* Home — Cuttle overview (services / pipelines / active jobs) */

(function () {
    const POLL_MS = 8000;
    const STORAGE_VIEW = 'cuttle_home_view';

    const tabOverview = document.getElementById('tabOverview');
    const tabFeed = document.getElementById('tabFeed');
    const viewOverview = document.getElementById('viewOverview');
    const viewFeed = document.getElementById('viewFeed');
    const pipelinesList = document.getElementById('pipelinesList');
    const activeJobsList = document.getElementById('activeJobsList');
    const activeCount = document.getElementById('activeCount');
    const homeRefreshHint = document.getElementById('homeRefreshHint');

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function setPill(el, state, label) {
        if (!el) return;
        el.className = 'home-pill home-pill--' + state;
        el.textContent = label;
    }

    function setView(view) {
        const isFeed = view === 'feed';
        if (viewOverview) viewOverview.hidden = isFeed;
        if (viewFeed) viewFeed.hidden = !isFeed;
        if (tabOverview) {
            tabOverview.classList.toggle('active', !isFeed);
            tabOverview.setAttribute('aria-selected', String(!isFeed));
        }
        if (tabFeed) {
            tabFeed.classList.toggle('active', isFeed);
            tabFeed.setAttribute('aria-selected', String(isFeed));
        }
        try { sessionStorage.setItem(STORAGE_VIEW, isFeed ? 'feed' : 'overview'); } catch (_) {}
    }

    document.querySelectorAll('.home-view-tab').forEach((btn) => {
        btn.addEventListener('click', () => setView(btn.getAttribute('data-view') || 'overview'));
    });

    document.querySelectorAll('.home-link-btn[data-nav]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const page = btn.getAttribute('data-nav');
            if (!page) return;
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({ type: 'cuttle-navigate', page }, '*');
            } else {
                window.location.href = page;
            }
        });
    });

    function formatAge(startTime) {
        if (startTime == null || startTime === '') return '';
        let ms;
        if (typeof startTime === 'number') {
            ms = startTime < 1e12 ? startTime * 1000 : startTime;
        } else {
            const t = Date.parse(String(startTime));
            if (Number.isNaN(t)) return '';
            ms = t;
        }
        const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
        if (sec < 60) return sec + 's';
        if (sec < 3600) return Math.floor(sec / 60) + 'm';
        return Math.floor(sec / 3600) + 'h ' + Math.floor((sec % 3600) / 60) + 'm';
    }

    async function fetchJson(url) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 6000);
        try {
            const r = await fetch(url, { cache: 'no-store', signal: controller.signal });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return await r.json();
        } finally {
            clearTimeout(timeout);
        }
    }

    async function refreshOverview() {
        const started = Date.now();
        let status = null;
        let llm = null;
        let running = [];
        let executing = [];

        try {
            status = await fetchJson('/api/status');
            setPill(document.getElementById('flaskPill'), 'ok', 'Running');
            const flaskMeta = document.getElementById('flaskMeta');
            if (flaskMeta) {
                const n = status.running_pipeline_count;
                flaskMeta.textContent = typeof n === 'number'
                    ? ('Port 8080 · ' + n + ' pipeline' + (n === 1 ? '' : 's') + ' listening')
                    : 'Port 8080';
            }
        } catch (_) {
            setPill(document.getElementById('flaskPill'), 'bad', 'Unreachable');
        }

        try {
            const discordOk = status && status.discord_connected;
            if (discordOk === true) {
                setPill(document.getElementById('discordPill'), 'ok', 'Online');
            } else if (discordOk === false) {
                setPill(document.getElementById('discordPill'), 'bad', 'Offline');
            } else {
                setPill(document.getElementById('discordPill'), 'unknown', 'Unknown');
            }
            const discordMeta = document.getElementById('discordMeta');
            if (discordMeta) discordMeta.textContent = 'Discord gateway';
        } catch (_) {}

        try {
            llm = await fetchJson('/api/local-llm/status');
            const runningLlm = !!(llm && llm.running);
            setPill(document.getElementById('llmPill'), runningLlm ? 'ok' : 'off', runningLlm ? 'Running' : 'Stopped');
            const llmMeta = document.getElementById('llmMeta');
            if (llmMeta) {
                const backend = (llm && llm.backend) || 'local';
                const models = (llm && llm.models) || [];
                llmMeta.textContent = runningLlm
                    ? (backend + (models.length ? ' · ' + models.slice(0, 2).join(', ') : ''))
                    : (backend + ' · not reachable');
            }
        } catch (_) {
            setPill(document.getElementById('llmPill'), 'unknown', 'Unknown');
        }

        try {
            const d = await fetchJson('/api/running-pipelines');
            running = d.running_pipelines || [];
        } catch (_) {
            running = null;
        }

        if (pipelinesList) {
            if (running === null) {
                pipelinesList.innerHTML = '<p class="home-empty">Could not load pipelines.</p>';
            } else if (!running.length) {
                pipelinesList.innerHTML = '<p class="home-empty">No pipelines listening. Start one from Jobs.</p>';
            } else {
                pipelinesList.innerHTML = running.map((name) => (
                    '<div class="home-list-row">' +
                    '<span class="home-list-name">' + escapeHtml(name) + '</span>' +
                    '<span class="home-pill home-pill--ok">Listening</span>' +
                    '</div>'
                )).join('');
            }
        }

        try {
            const d = await fetchJson('/api/executing-jobs');
            executing = d.executing_jobs || [];
        } catch (_) {
            executing = null;
        }

        if (activeJobsList) {
            if (executing === null) {
                activeJobsList.innerHTML = '<p class="home-empty">Could not load active runs.</p>';
                if (activeCount) activeCount.hidden = true;
            } else if (!executing.length) {
                activeJobsList.innerHTML = '<p class="home-empty">Nothing executing right now.</p>';
                if (activeCount) activeCount.hidden = true;
            } else {
                if (activeCount) {
                    activeCount.hidden = false;
                    activeCount.textContent = String(executing.length);
                }
                activeJobsList.innerHTML = executing.map((job) => {
                    const name = job.pipeline_name || 'Unknown';
                    const qid = job.query_id || '';
                    const age = formatAge(job.start_time);
                    return (
                        '<div class="home-list-row">' +
                        '<div class="home-list-main">' +
                        '<span class="home-list-name">' + escapeHtml(name) + '</span>' +
                        (qid ? '<span class="home-list-sub">' + escapeHtml(qid) + '</span>' : '') +
                        '</div>' +
                        '<span class="home-pill home-pill--run">' + (age ? ('Running · ' + age) : 'Running') + '</span>' +
                        '</div>'
                    );
                }).join('');
            }
        }

        if (homeRefreshHint) {
            const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
            homeRefreshHint.textContent = 'Updated just now · auto-refresh every ' + (POLL_MS / 1000) + 's';
            void sec;
        }
    }

    // Default to overview (feed only if user last chose it this session)
    let initial = 'overview';
    try {
        const saved = sessionStorage.getItem(STORAGE_VIEW);
        if (saved === 'feed' || saved === 'overview') initial = saved;
    } catch (_) {}
    setView(initial);

    refreshOverview();
    setInterval(refreshOverview, POLL_MS);
})();
