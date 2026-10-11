/**
 * Mesh page orchestrator.
 *
 * Owner of the /mesh_page.html composition: the device fleet (workers,
 * capabilities, load, pairing) and the mesh job queue (submit/cancel/rerun,
 * batches, shards). Execution history across all sources lives on the Jobs
 * page (api.jobs); this page owns the mesh admin + queue.
 */

let deviceWorkers = [];
let meshJobs = [];
let meshJobsPage = 0;
const MESH_JOBS_PAGE_SIZE = 25;
let devicesEnabled = true;
let selfWorkerId = '';
let hostCuttleVersion = '';
let hostGitBranch = '';
let viewingWorkerId = '';

async function getDesktopViewerConfig() {
    try {
        const qs = new URLSearchParams(window.location.search || '');
        const me = String(qs.get('me') || '').trim().toLowerCase();
        const clientFlag = qs.get('client');
        if (me || clientFlag === '1') {
            return {
                clientMode: clientFlag === '1' || clientFlag === 'true',
                workerId: me,
                hostname: String(qs.get('host') || '').trim(),
            };
        }
    } catch (_) {}
    const bridges = [];
    try { if (window.electron?.desktop) bridges.push(window.electron.desktop); } catch (_) {}
    try { if (window.parent && window.parent !== window && window.parent.electron?.desktop) bridges.push(window.parent.electron.desktop); } catch (_) {}
    try { if (window.top && window.top !== window && window.top.electron?.desktop) bridges.push(window.top.electron.desktop); } catch (_) {}
    for (const desktop of bridges) {
        try {
            if (typeof desktop.getConfig === 'function') {
                const cfg = await desktop.getConfig();
                if (cfg && typeof cfg === 'object') return cfg;
            }
        } catch (_) {}
    }
    try {
        const clientMode = sessionStorage.getItem('cuttle_client_mode') === '1';
        const workerId = String(sessionStorage.getItem('cuttle_viewer_worker_id') || '').trim();
        if (workerId || clientMode) {
            return { clientMode: clientMode, workerId: workerId };
        }
    } catch (_) {}
    try {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-viewer-identity-request' }, '*');
        }
    } catch (_) {}
    return null;
}

window.addEventListener('message', (ev) => {
    const d = ev && ev.data;
    if (!d || d.type !== 'cuttle-viewer-identity') return;
    try {
        const wid = String(d.workerId || '').trim().toLowerCase();
        if (d.clientMode) sessionStorage.setItem('cuttle_client_mode', '1');
        else if (d.clientMode === false) sessionStorage.setItem('cuttle_client_mode', '0');
        if (wid) sessionStorage.setItem('cuttle_viewer_worker_id', wid);
        if (d.hostname) sessionStorage.setItem('cuttle_viewer_hostname', String(d.hostname).toLowerCase());
        loadDevices();
    } catch (_) {}
});

async function resolveViewingWorkerId(hostSelfId) {
    const cfg = await getDesktopViewerConfig();
    const fromDesktop = String(cfg?.workerId || '').trim().toLowerCase();
    const hostname = String(
        cfg?.hostname ||
        (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('cuttle_viewer_hostname')) ||
        ''
    ).trim().toLowerCase();
    if (cfg && cfg.clientMode) {
        if (fromDesktop) return fromDesktop;
        if (hostname && Array.isArray(deviceWorkers) && deviceWorkers.length) {
            const byHost = deviceWorkers.find((w) => {
                const hn = String(w.hostname || '').trim().toLowerCase();
                const id = String(w.worker_id || '').trim().toLowerCase();
                return hn === hostname || id === hostname;
            });
            if (byHost) return String(byHost.worker_id || '').toLowerCase();
        }
        return '';
    }
    if (fromDesktop) return fromDesktop;
    if (cfg && cfg.clientMode === false && hostSelfId) {
        return String(hostSelfId).toLowerCase();
    }
    const hasElectronBridge = !!(
        window.electron ||
        (window.parent && window.parent !== window && window.parent.electron) ||
        (window.top && window.top !== window && window.top.electron)
    );
    if (!hasElectronBridge && !cfg) {
        return hostSelfId ? String(hostSelfId).toLowerCase() : '';
    }
    return '';
}

function workerRoleTags(w) {
    const id = String(w.worker_id || '').toLowerCase();
    const tags = [];
    if (w.is_self) tags.push('host');
    else tags.push('worker');
    if (viewingWorkerId && id === viewingWorkerId) tags.push('you');
    return tags;
}
const VALID_JOBS_TABS = { devices: 1 };
function readInitialJobsTab() {
    try {
        const fromUrl = String(
            new URLSearchParams(window.location.search || '').get('tab') || ''
        ).trim().toLowerCase();
        if (VALID_JOBS_TABS[fromUrl]) return fromUrl;
    } catch (_) {}
    try {
        const fromLs = String(localStorage.getItem('jobsPageTab') || '').trim().toLowerCase();
        if (VALID_JOBS_TABS[fromLs]) return fromLs;
    } catch (_) {}
    return 'devices';
}
let currentTab = readInitialJobsTab();
let initialLoad = true;
let pollTimer = null;

function setHtmlIfChanged(el, html) {
    if (!el) return;
    if (el.innerHTML !== html) el.innerHTML = html;
}

let openDeviceMenuKey = null;
let suppressDeviceMenuClose = 0;

function cssEsc(value) {
    const s = String(value == null ? '' : value);
    if (window.CSS && typeof CSS.escape === 'function') return CSS.escape(s);
    return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function deviceMenuKeyFromBtn(btn) {
    if (!btn) return null;
    const jid = String(btn.getAttribute('data-job-id') || '').trim();
    if (jid) return 'job:' + jid;
    const wid = String(btn.getAttribute('data-worker-id') || '').trim();
    if (wid) return 'worker:' + wid;
    return null;
}

function findDeviceMenuButton(key) {
    if (!key) return null;
    if (key.indexOf('job:') === 0) {
        const id = key.slice(4);
        return document.querySelector('.device-menu-btn[data-job-id="' + cssEsc(id) + '"]');
    }
    if (key.indexOf('worker:') === 0) {
        const id = key.slice(7);
        return document.querySelector('.device-menu-btn[data-worker-id="' + cssEsc(id) + '"]');
    }
    return null;
}

function fillDeviceMenuFromBtn(menu, btn) {
    if (!menu || !btn) return false;
    const jobId = String(btn.getAttribute('data-job-id') || '').trim();
    if (jobId) {
        const canCancel = btn.getAttribute('data-can-cancel') === '1';
        menu.innerHTML =
            '<button type="button" role="menuitem" class="danger" data-action="cancel-job" data-job-id="' +
            escapeHtml(jobId) + '"' + (canCancel ? '' : ' disabled') +
            (canCancel ? '' : ' title="Only queued/claimed/running jobs can be cancelled"') +
            '>Cancel</button>';
        return true;
    }
    const wid = String(btn.getAttribute('data-worker-id') || '').trim();
    if (!wid) return false;
    const online = btn.getAttribute('data-online') === '1';
    const canUpdate = btn.getAttribute('data-can-update') === '1';
    const canRemove = btn.getAttribute('data-can-remove') === '1';
    menu.innerHTML =
        '<button type="button" role="menuitem" data-action="ping" data-worker-id="' +
        escapeHtml(wid) + '"' + (online ? '' : ' disabled') + '>Ping</button>' +
        '<button type="button" role="menuitem" data-action="update" data-worker-id="' +
        escapeHtml(wid) + '"' + (canUpdate ? '' : ' disabled') +
        (canUpdate ? '' : ' title="No update needed or not available"') + '>Update</button>' +
        '<button type="button" role="menuitem" class="danger" data-action="remove" data-worker-id="' +
        escapeHtml(wid) + '"' + (canRemove ? '' : ' disabled') +
        (canRemove ? '' : ' title="Host worker cannot be removed"') + '>Remove</button>';
    return true;
}

function positionDeviceMenu(btn) {
    const menu = document.getElementById('deviceRowMenu');
    if (!menu || !btn || menu.hidden) return;
    const rect = btn.getBoundingClientRect();
    const mw = menu.offsetWidth || 160;
    const mh = menu.offsetHeight || 120;
    let left = rect.right - mw;
    let top = rect.bottom + 4;
    if (left < 8) left = 8;
    if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
    if (top + mh > window.innerHeight - 8) top = Math.max(8, rect.top - mh - 4);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
}

function restoreDeviceMenu(key) {
    if (!key) return;
    const btn = findDeviceMenuButton(key);
    if (!btn) {
        closeDeviceMenu();
        return;
    }
    const menu = ensureDeviceMenu();
    if (!fillDeviceMenuFromBtn(menu, btn)) {
        closeDeviceMenu();
        return;
    }
    openDeviceMenuKey = key;
    document.querySelectorAll('.device-menu-btn[aria-expanded="true"]').forEach(function (b) {
        if (b !== btn) b.setAttribute('aria-expanded', 'false');
    });
    btn.setAttribute('aria-expanded', 'true');
    menu.hidden = false;
    positionDeviceMenu(btn);
}

function setJobsTableHtml(el, html) {
    if (!el) return;
    if (el.innerHTML === html) return;
    const prev = el.querySelector('.jobs-table-scroll');
    const left = prev ? prev.scrollLeft : 0;
    const top = prev ? prev.scrollTop : 0;
    const keepKey = openDeviceMenuKey;
    suppressDeviceMenuClose += 1;
    try {
        el.innerHTML = html;
        const next = el.querySelector('.jobs-table-scroll');
        if (next) {
            next.scrollLeft = left;
            next.scrollTop = top;
        }
        if (keepKey) {
            openDeviceMenuKey = keepKey;
            restoreDeviceMenu(keepKey);
            requestAnimationFrame(function () {
                restoreDeviceMenu(keepKey);
                if (next && next.isConnected) {
                    next.scrollLeft = left;
                    next.scrollTop = top;
                }
            });
        }
    } finally {
        requestAnimationFrame(function () {
            suppressDeviceMenuClose = Math.max(0, suppressDeviceMenuClose - 1);
        });
    }
}

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function wrapJobsTable(tableHtml, opts) {
    opts = opts || {};
    const cls = 'jobs-table-scroll' + (opts.capped ? ' jobs-table-scroll--capped' : '');
    return '<div class="' + cls + '" data-jobs-table="1">' +
        String(tableHtml || '') +
        '</div>';
}

function formatTime(iso) {
    if (iso == null || iso === '') return '';
    let d;
    if (typeof iso === 'number' && Number.isFinite(iso)) {
        d = new Date(iso < 1e12 ? iso * 1000 : iso);
    } else {
        const s = String(iso).trim();
        if (/^\d+(\.\d+)?$/.test(s)) {
            const n = Number(s);
            d = new Date(n < 1e12 ? n * 1000 : n);
        } else {
            d = new Date(s);
        }
    }
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 0) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return d.toLocaleDateString();
}

function formatTimeAbsolute(iso) {
    if (iso == null || iso === '') return '';
    let d;
    if (typeof iso === 'number' && Number.isFinite(iso)) {
        d = new Date(iso < 1e12 ? iso * 1000 : iso);
    } else {
        const s = String(iso).trim();
        if (/^\d+(\.\d+)?$/.test(s)) {
            const n = Number(s);
            d = new Date(n < 1e12 ? n * 1000 : n);
        } else {
            d = new Date(s);
        }
    }
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString();
}

function meshJobWhen(j) {
    const st = String((j && j.status) || '').toLowerCase();
    if (st === 'succeeded' || st === 'failed' || st === 'cancelled') {
        return j.completed_at || j.updated_at || j.started_at || j.created_at;
    }
    if (st === 'running' || st === 'claimed') {
        return j.started_at || j.updated_at || j.created_at;
    }
    return j.created_at || j.updated_at || j.started_at;
}

function notifyShellJobsTab(tab) {
    try {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-jobs-tab', tab: tab }, '*');
        }
    } catch (_) {}
}

function syncJobsTabUrl(tab) {
    try {
        const u = new URL(window.location.href);
        if (u.searchParams.get('tab') !== tab) {
            u.searchParams.set('tab', tab);
            history.replaceState(null, '', u.pathname + u.search + u.hash);
        }
    } catch (_) {}
    notifyShellJobsTab(tab);
}

function setTab(tab) {
    tab = String(tab || '').trim().toLowerCase();
    if (!VALID_JOBS_TABS[tab]) tab = 'devices';
    currentTab = tab;
    try { localStorage.setItem('jobsPageTab', tab); } catch (_) {}
    syncJobsTabUrl(tab);
    document.querySelectorAll('.jobs-panel').forEach(p => {
        p.classList.toggle('active', p.id === 'panel-' + tab);
    });
    schedulePoll();
}

function schedulePoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(loadAll, 4000);
}

async function loadDevices() {
    const listEl = document.getElementById('devicesList');
    const jobsEl = document.getElementById('meshJobsList');
    const statusEl = document.getElementById('devicesStatus');
    try {
        const [wr, jr] = await Promise.all([
            fetch('/api/workers', { cache: 'no-store', credentials: 'include' }),
            fetch('/api/workers/jobs?limit=100', { cache: 'no-store', credentials: 'include' }),
        ]);
        const wdata = wr.ok ? await wr.json() : { workers: [], enabled: false };
        const jdata = jr.ok ? await jr.json() : { jobs: [] };
        deviceWorkers = wdata.workers || [];
        devicesEnabled = wdata.enabled !== false;
        selfWorkerId = String(wdata.self_worker_id || '').trim();
        hostCuttleVersion = String(wdata.host_cuttle_version || '').trim();
        hostGitBranch = String(wdata.host_git_branch || '').trim();
        viewingWorkerId = await resolveViewingWorkerId(selfWorkerId);
        meshJobs = jdata.jobs || [];
        const pageCount = Math.max(1, Math.ceil(meshJobs.length / MESH_JOBS_PAGE_SIZE));
        if (meshJobsPage >= pageCount) meshJobsPage = pageCount - 1;
        if (meshJobsPage < 0) meshJobsPage = 0;
        if (statusEl) {
            const online = deviceWorkers.filter(w => w.online).length;
            const outdated = (wdata.outdated_workers || []).length;
            let msg = devicesEnabled
                ? (online + ' online / ' + deviceWorkers.length + ' registered')
                : 'Device workers disabled';
            if (hostCuttleVersion) msg += ' · host ' + hostCuttleVersion;
            if (outdated) msg += ' · ' + outdated + ' outdated';
            statusEl.textContent = msg;
        }
        renderDevices();
        renderMeshJobs();
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Error: ' + (e.message || e);
        if (listEl && initialLoad) {
            setHtmlIfChanged(listEl, '<div class="jobs-empty" style="grid-column:1/-1">Could not load devices.</div>');
        }
    }
}

function renderDevices() {
    const el = document.getElementById('devicesList');
    if (!el) return;
    if (!deviceWorkers.length) {
        const empty = '<div class="jobs-empty">No workers registered yet. Host daemon local worker should appear within a few seconds after restart.</div>';
        if (el.innerHTML !== empty) {
            if (openDeviceMenuKey && openDeviceMenuKey.indexOf('worker:') === 0) closeDeviceMenu();
            el.innerHTML = empty;
        }
        return;
    }
    const ordered = deviceWorkers.slice().sort(function (a, b) {
        const sa = (a.slot != null) ? Number(a.slot) : 9999;
        const sb = (b.slot != null) ? Number(b.slot) : 9999;
        if (sa !== sb) return sa - sb;
        const ha = a.is_self ? 0 : 1;
        const hb = b.is_self ? 0 : 1;
        if (ha !== hb) return ha - hb;
        return String(a.worker_id || '').localeCompare(String(b.worker_id || ''));
    });
    const rows = ordered.map(function (w) {
        const roles = workerRoleTags(w);
        if (w.stale_process && roles.indexOf('stale') < 0) roles.push('stale');
        const roleHtml = roles.map(function (t) {
            let cls = 'device-role';
            if (t === 'you') cls += ' device-role-you';
            else if (t === 'host') cls += ' device-role-host';
            else if (t === 'stale') cls += ' device-role-stale';
            return '<span class="' + cls + '">' + escapeHtml(t) + '</span>';
        }).join(' ');
        const caps = w.capabilities || {};
        const load = w.load || {};
        const capBits = ['filesystem', 'blender', 'ffmpeg', 'python', 'cuttle_self_update']
            .filter(function (k) { return caps[k]; })
            .join(', ') || '—';
        const cpu = load.cpu_pct != null ? Math.round(load.cpu_pct) + '%' : '—';
        const gpu = load.gpu_pct != null ? Math.round(load.gpu_pct) + '%' : '—';
        const ver = String(w.cuttle_version || (w.meta && w.meta.cuttle_version) || '').trim() || '—';
        const branch = String(w.git_branch || (w.meta && w.meta.git_branch) || '').trim();
        const wrongBranch = !!w.wrong_branch;
        const mismatch = !!w.version_mismatch || !!w.needs_update || !!w.stale_process;
        let verHtml = escapeHtml(ver);
        if (mismatch && ver !== '—') {
            const why = wrongBranch
                ? ('Tracking ' + (branch || '?') + ', but host is on ' + (hostGitBranch || '?')
                    + '. Update cannot fast-forward across branches — switch this device to the host branch.')
                : (w.stale_process
                    ? 'Running worker process is older than on-disk git (needs reload/Update)'
                    : ('Host is ' + (hostCuttleVersion || '?')));
            verHtml = '<span style="color:var(--warning, #d29922)" title="' +
                escapeHtml(why) + '">' + escapeHtml(ver) + ' ⚠</span>';
        }
        if (branch) {
            const bTitle = wrongBranch
                ? ('On ' + branch + ' (host on ' + (hostGitBranch || '?') + ')')
                : ('Tracking branch ' + branch);
            verHtml += ' <span style="opacity:.6;font-size:11px" title="' +
                escapeHtml(bTitle) + '">' + escapeHtml(branch) + '</span>';
        }
        let latency = '—';
        if (w.last_rtt_ms != null && isFinite(Number(w.last_rtt_ms))) {
            latency = Math.round(Number(w.last_rtt_ms)) + ' ms';
            if (w.last_rtt_at) {
                const age = Math.max(0, Math.floor((Date.now() / 1000) - Number(w.last_rtt_at)));
                if (age < 60) latency += ' · <1m';
                else if (age < 7200) latency += ' · ' + Math.floor(age / 60) + 'm ago';
            }
        }
        const wid = String(w.worker_id || '');
        const slot = (w.slot != null && isFinite(Number(w.slot)))
            ? String(Math.trunc(Number(w.slot)))
            : '—';
        const canUpdate = !!(w.needs_update || (mismatch && w.online && caps.cuttle_self_update && !w.is_self));
        const canRemove = !w.is_self && !!wid;
        const menuBtn = wid
            ? '<button type="button" class="device-menu-btn" data-worker-id="' +
                escapeHtml(wid) + '"' +
                ' data-online="' + (w.online ? '1' : '0') + '"' +
                ' data-can-update="' + (canUpdate ? '1' : '0') + '"' +
                ' data-can-remove="' + (canRemove ? '1' : '0') + '"' +
                ' aria-haspopup="menu" aria-expanded="false" title="Actions"' +
                '><span class="device-hamburger" aria-hidden="true"><span></span><span></span><span></span></span></button>'
            : '—';
        const rowCls = roles.indexOf('you') >= 0 ? ' class="device-row-you"' : '';
        return '<tr' + rowCls + '>' +
            '<td class="device-slot-cell">' + escapeHtml(slot) + '</td>' +
            '<td>' + escapeHtml(w.worker_id || '?') + '</td>' +
            '<td>' + roleHtml + '</td>' +
            '<td>' + (w.online
                ? '<span style="color:var(--success, #3fb950)">online</span>'
                : '<span style="opacity:.55">offline</span>') + '</td>' +
            '<td>' + verHtml + '</td>' +
            '<td>' + escapeHtml(latency) + '</td>' +
            '<td>' + escapeHtml(w.hostname || '') + '</td>' +
            '<td>' + escapeHtml(capBits) + '</td>' +
            '<td>' + escapeHtml(cpu + ' / ' + gpu) + '</td>' +
            '<td class="device-actions-cell">' + menuBtn + '</td>' +
            '</tr>';
    }).join('');
    const html = wrapJobsTable(
        '<table class="hist-table"><thead><tr>' +
        '<th class="device-slot-cell" title="Stable mesh slot (#1 = host)">#</th>' +
        '<th>Worker</th><th>Role</th><th>Status</th><th>Version</th><th>Latency</th>' +
        '<th>Hostname</th><th>Caps</th><th>CPU / GPU</th>' +
        '<th class="device-actions-cell" aria-label="Actions"></th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>'
    );
    setJobsTableHtml(el, html);
}

function ensureDeviceMenu() {
    let menu = document.getElementById('deviceRowMenu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'deviceRowMenu';
    menu.className = 'device-row-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    return menu;
}

function closeDeviceMenu() {
    const menu = document.getElementById('deviceRowMenu');
    if (menu) menu.hidden = true;
    openDeviceMenuKey = null;
    document.querySelectorAll('.device-menu-btn[aria-expanded="true"]').forEach(function (b) {
        b.setAttribute('aria-expanded', 'false');
    });
}

function openDeviceMenu(btn) {
    const menu = ensureDeviceMenu();
    const key = deviceMenuKeyFromBtn(btn);
    const open = btn.getAttribute('aria-expanded') === 'true' && openDeviceMenuKey === key;
    closeDeviceMenu();
    if (open || !key) return;
    if (!fillDeviceMenuFromBtn(menu, btn)) return;
    menu.hidden = false;
    openDeviceMenuKey = key;
    btn.setAttribute('aria-expanded', 'true');
    positionDeviceMenu(btn);
}

async function removeWorker(workerId) {
    const wid = String(workerId || '').trim();
    if (!wid) return;
    const ok = window.confirm(
        'Remove "' + wid + '" from the mesh registry?\n\n' +
        'This clears the Jobs → Devices entry and revokes its enroll token. ' +
        'A live Client will reappear after it reconnects.'
    );
    if (!ok) return;
    try {
        const r = await fetch('/api/workers/' + encodeURIComponent(wid), {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ revoke_enroll: true }),
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) {
            throw new Error(data.error || ('HTTP ' + r.status));
        }
        if (window.showToast) {
            window.showToast('Removed ' + wid, 'success');
        }
        await loadDevices();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
    }
}

async function requestWorkerSelfUpdate(workerId, btn) {
    const wid = String(workerId || '').trim();
    if (!wid) return;
    const label = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '…';
    }
    try {
        const r = await fetch('/api/workers/self-update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: wid, submitted_by: 'jobs-ui' }),
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) {
            throw new Error(data.error || ('HTTP ' + r.status));
        }
        if (window.showToast) window.showToast('Updating ' + wid + '…', 'info');
        const jobId = String(
            (data.job && (data.job.id || data.job.job_id)) || data.job_id || ''
        ).trim();
        // Wait for the preservation pull so a no-op (already at upstream / wrong
        // branch) or dirty-tree failure is reported instead of a false "queued".
        let final = data.job || null;
        if (jobId) {
            const deadline = Date.now() + 180000;
            while (Date.now() < deadline) {
                await new Promise(function (res) { setTimeout(res, 1500); });
                try {
                    const sr = await fetch('/api/workers/jobs/' + encodeURIComponent(jobId), { cache: 'no-store' });
                    const sj = await sr.json().catch(function () { return {}; });
                    final = (sj && (sj.job || sj)) || final;
                    const st = String((final && final.status) || '').toLowerCase();
                    if (st === 'succeeded' || st === 'failed' || st === 'cancelled') break;
                } catch (_) { /* keep waiting */ }
            }
        }
        const st = String((final && final.status) || '').toLowerCase();
        if (st === 'failed' || st === 'cancelled') {
            throw new Error(String(
                (final && (final.error || (final.result && final.result.error))) || 'Self-update failed'
            ));
        }
        const jr = (final && final.result) || {};
        if (window.showToast) {
            if (jr.advanced === false) {
                window.showToast(wid + ': ' + (jr.note || 'nothing to update'), 'info');
            } else {
                window.showToast('Updated ' + wid + ' → ' + (jr.head || '?') + '.', 'success');
            }
        }
        await loadDevices();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = label || 'Update';
        }
    }
}

async function probeWorker(workerId, btn) {
    const wid = String(workerId || '').trim();
    if (!wid) return;
    const label = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '…';
    }
    try {
        const r = await fetch('/api/workers/' + encodeURIComponent(wid) + '/probe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timeout_seconds: 25 }),
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) {
            throw new Error(data.error || ('HTTP ' + r.status));
        }
        if (window.showToast) {
            window.showToast(wid + ': ' + Math.round(data.rtt_ms) + ' ms', 'success');
        }
        await loadDevices();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = label || 'Ping';
        }
    }
}

async function pingLocalWorker() {
    const target = viewingWorkerId || selfWorkerId;
    if (!target) {
        if (window.showToast) window.showToast('No local worker id yet', 'error');
        return;
    }
    await probeWorker(target, document.getElementById('btnPingWorker'));
}

function renderMeshJobs() {
    const el = document.getElementById('meshJobsList');
    if (!el) return;
    if (!meshJobs.length) {
        meshJobsPage = 0;
        if (openDeviceMenuKey && openDeviceMenuKey.indexOf('job:') === 0) closeDeviceMenu();
        setHtmlIfChanged(el, '<div class="jobs-empty">No mesh jobs yet.</div>');
        return;
    }
    const pageSize = MESH_JOBS_PAGE_SIZE;
    const total = meshJobs.length;
    const pageCount = Math.max(1, Math.ceil(total / pageSize));
    if (meshJobsPage >= pageCount) meshJobsPage = pageCount - 1;
    if (meshJobsPage < 0) meshJobsPage = 0;
    const start = meshJobsPage * pageSize;
    const pageRows = meshJobs.slice(start, start + pageSize);
    const end = start + pageRows.length;
    const rows = pageRows.map(function (j) {
        const st = String(j.status || '');
        const sender = String(j.submitted_by || '').trim() || '—';
        const worker = String(j.claimed_by || j.target_worker_id || '').trim() || '—';
        const params = j.params || {};
        let detail = '';
        if (j.type === 'blender_render') {
            const fs = params.frame_start;
            const fe = params.frame_end;
            detail = (fs != null && fe != null) ? ('frames ' + fs + '–' + fe) : '';
            if (params.dry_run) detail = (detail ? detail + ' · ' : '') + 'dry-run';
            if (j.result && j.result.frames_written != null) {
                detail += (detail ? ' · ' : '') + j.result.frames_written + ' files';
            }
            if (j.result && j.result.elapsed_seconds != null) {
                detail += (detail ? ' · ' : '') + j.result.elapsed_seconds + 's';
            }
        } else if (j.type === 'file_copy') {
            detail = (params.dest || params.source || '') + '';
        } else if (j.type === 'shell') {
            detail = params.recipe || '';
        } else if (j.result && j.result.echo != null) {
            detail = String(j.result.echo);
        }
        if (j.error) detail = String(j.error);
        const when = meshJobWhen(j);
        const whenRel = formatTime(when) || '—';
        const whenAbs = formatTimeAbsolute(when);
        const whenHtml = whenAbs
            ? ('<span title="' + escapeHtml(whenAbs) + '">' + escapeHtml(whenRel) + '</span>')
            : escapeHtml(whenRel);
        const jid = String(j.id || '');
        const canCancel = st === 'queued' || st === 'claimed' || st === 'running';
        const stCls = st.toLowerCase().replace(/[^a-z0-9_-]/g, '');
        const statusHtml = st
            ? '<span class="status-pill ' + escapeHtml(stCls) + '">' + escapeHtml(st) + '</span>'
            : '—';
        const menuBtn = jid
            ? '<button type="button" class="device-menu-btn mesh-job-menu-btn" data-job-id="' +
                escapeHtml(jid) + '"' +
                ' data-can-cancel="' + (canCancel ? '1' : '0') + '"' +
                ' aria-haspopup="menu" aria-expanded="false" title="Actions"' +
                '><span class="device-hamburger" aria-hidden="true"><span></span><span></span><span></span></span></button>'
            : '—';
        return '<tr>' +
            '<td>' + whenHtml + '</td>' +
            '<td>' + escapeHtml(jid.slice(0, 8)) + '</td>' +
            '<td>' + escapeHtml(j.type || '') + '</td>' +
            '<td>' + statusHtml + '</td>' +
            '<td>' + escapeHtml(sender) + '</td>' +
            '<td>' + escapeHtml(worker) + '</td>' +
            '<td class="jobs-cell-wrap">' + escapeHtml(detail) + '</td>' +
            '<td class="device-actions-cell">' + menuBtn + '</td>' +
            '</tr>';
    }).join('');
    const pager = ('<div class="jobs-table-pager">' +
        '<span class="jobs-table-pager-meta">' +
        (pageCount > 1
            ? ('Showing ' + (start + 1) + '–' + end + ' of ' + total)
            : (total + ' job' + (total === 1 ? '' : 's'))) +
        '</span>' +
        (pageCount > 1
            ? ('<div class="jobs-table-pager-actions">' +
                '<button type="button" class="jobs-pager-btn" data-mesh-page="prev"' +
                (meshJobsPage <= 0 ? ' disabled' : '') + '>Prev</button>' +
                '<span class="jobs-pager-page">' + (meshJobsPage + 1) + ' / ' + pageCount + '</span>' +
                '<button type="button" class="jobs-pager-btn" data-mesh-page="next"' +
                (meshJobsPage >= pageCount - 1 ? ' disabled' : '') + '>Next</button>' +
                '</div>')
            : '') +
        '</div>');
    const meshHtml =
        '<div class="jobs-table-block">' +
        wrapJobsTable(
            '<table class="hist-table"><thead><tr>' +
            '<th>When</th><th>Id</th><th>Type</th><th>Status</th><th>Sender</th><th>Worker</th><th>Detail</th>' +
            '<th class="device-actions-cell" aria-label="Actions"></th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table>',
            { capped: true }
        ) +
        pager +
        '</div>';
    setJobsTableHtml(el, meshHtml);
}

async function cancelMeshJob(jobId, btn) {
    const jid = String(jobId || '').trim();
    if (!jid) return;
    if (btn) btn.disabled = true;
    try {
        const r = await fetch('/api/workers/jobs/' + encodeURIComponent(jid) + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'cancelled from Jobs UI' }),
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
        if (window.showToast) window.showToast('Cancelled ' + jid.slice(0, 8), 'success');
        await loadAll();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
        if (btn) btn.disabled = false;
    }
}

let pendingPairing = [];

async function loadPairing() {
    const el = document.getElementById('pairingList');
    try {
        const r = await fetch('/api/workers/enroll-requests', { cache: 'no-store', credentials: 'include' });
        const data = r.ok ? await r.json() : {};
        pendingPairing = (data && data.pending) || [];
        renderPairing();
    } catch (e) {
        if (el && initialLoad) el.innerHTML = '<div class="jobs-empty">Could not load pairing requests.</div>';
    }
}

function renderPairing() {
    const el = document.getElementById('pairingList');
    if (!el) return;
    if (!pendingPairing.length) {
        const empty = '<div class="jobs-empty">No devices waiting for approval. New Client connects appear here with a pairing code.</div>';
        if (el.innerHTML !== empty) el.innerHTML = empty;
        return;
    }
    const rows = pendingPairing.map(function (q) {
        const age = Math.max(0, Math.floor(Number(q.age_seconds) || 0));
        const ageTxt = age < 60 ? age + 's ago' : Math.floor(age / 60) + 'm ago';
        const rid = escapeHtml(String(q.id || ''));
        return '<tr>' +
            '<td><strong style="font-size:1.2em; letter-spacing:.1em;">' + escapeHtml(String(q.code || '')) + '</strong></td>' +
            '<td>' + escapeHtml(String(q.worker_id || '')) + '</td>' +
            '<td>' + escapeHtml(String(q.hostname || '')) + '</td>' +
            '<td>' + escapeHtml(String(q.remote_addr || '')) + '</td>' +
            '<td>' + escapeHtml(ageTxt) + '</td>' +
            '<td class="device-actions-cell">' +
            '<button type="button" class="hist-btn" data-pairing-approve="' + rid + '">Approve</button> ' +
            '<button type="button" class="hist-btn" data-pairing-deny="' + rid + '">Deny</button>' +
            '</td></tr>';
    }).join('');
    setJobsTableHtml(el, wrapJobsTable(
        '<table class="hist-table"><thead><tr>' +
        '<th>Code</th><th>Worker</th><th>Hostname</th><th>IP</th><th>Waiting</th><th aria-label="Actions"></th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>'
    ));
}

async function decidePairing(requestId, approve) {
    try {
        const r = await fetch('/api/workers/enroll-requests/' + encodeURIComponent(requestId) +
            (approve ? '/approve' : '/deny'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const data = await r.json().catch(function () { return {}; });
        if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
        if (window.showToast) window.showToast(approve ? 'Device approved' : 'Pairing denied', 'success');
        await loadPairing();
        await loadDevices();
    } catch (e) {
        if (window.showToast) window.showToast(String(e.message || e), 'error');
    }
}

async function loadAll() {
    await loadDevices();
    await loadPairing();
    initialLoad = false;
}

const btnPing = document.getElementById('btnPingWorker');
if (btnPing) btnPing.addEventListener('click', pingLocalWorker);

const pairingEl = document.getElementById('pairingList');
if (pairingEl) {
    pairingEl.addEventListener('click', function (ev) {
        const okBtn = ev.target && ev.target.closest && ev.target.closest('[data-pairing-approve]');
        const noBtn = ev.target && ev.target.closest && ev.target.closest('[data-pairing-deny]');
        const btn = okBtn || noBtn;
        if (!btn || btn.disabled) return;
        ev.preventDefault();
        const rid = btn.getAttribute('data-pairing-approve') || btn.getAttribute('data-pairing-deny');
        if (okBtn && !window.confirm('Approve this device? A fresh worker credential is issued on approval.')) return;
        decidePairing(rid, !!okBtn);
    });
}

const devicesListEl = document.getElementById('devicesList');
if (devicesListEl) {
    devicesListEl.addEventListener('click', function (ev) {
        const menuBtn = ev.target && ev.target.closest && ev.target.closest('.device-menu-btn');
        if (menuBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            openDeviceMenu(menuBtn);
            return;
        }
    });
}

const meshJobsEl = document.getElementById('meshJobsList');
if (meshJobsEl) {
    meshJobsEl.addEventListener('click', function (ev) {
        const pageBtn = ev.target && ev.target.closest && ev.target.closest('[data-mesh-page]');
        if (pageBtn && !pageBtn.disabled) {
            ev.preventDefault();
            const dir = pageBtn.getAttribute('data-mesh-page');
            if (dir === 'prev') meshJobsPage = Math.max(0, meshJobsPage - 1);
            else if (dir === 'next') meshJobsPage += 1;
            renderMeshJobs();
            return;
        }
        const menuBtn = ev.target && ev.target.closest && ev.target.closest('.device-menu-btn');
        if (menuBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            openDeviceMenu(menuBtn);
        }
    });
}

document.addEventListener('click', function (ev) {
    const menu = document.getElementById('deviceRowMenu');
    if (!menu || menu.hidden) return;
    const t = ev.target;
    if (menu.contains(t)) {
        const item = t.closest && t.closest('button[data-action]');
        if (!item || item.disabled) return;
        ev.preventDefault();
        const action = item.getAttribute('data-action');
        const wid = item.getAttribute('data-worker-id');
        const jid = item.getAttribute('data-job-id');
        closeDeviceMenu();
        if (action === 'ping') probeWorker(wid, null);
        else if (action === 'update') requestWorkerSelfUpdate(wid, null);
        else if (action === 'remove') removeWorker(wid);
        else if (action === 'cancel-job') cancelMeshJob(jid, null);
        return;
    }
    if (t.closest && t.closest('.device-menu-btn')) return;
    closeDeviceMenu();
});
window.addEventListener('scroll', function (ev) {
    if (suppressDeviceMenuClose) return;
    const menu = document.getElementById('deviceRowMenu');
    if (!menu || menu.hidden || !openDeviceMenuKey) return;
    const t = ev.target;
    if (t && t.closest && t.closest('.jobs-table-scroll')) {
        restoreDeviceMenu(openDeviceMenuKey);
        return;
    }
    closeDeviceMenu();
}, true);
window.addEventListener('resize', function () {
    if (suppressDeviceMenuClose) return;
    if (openDeviceMenuKey) restoreDeviceMenu(openDeviceMenuKey);
    else closeDeviceMenu();
});

setTab(currentTab);
loadAll();
schedulePoll();
setInterval(function () {
    if (window.CronCountdown) window.CronCountdown.updateAllCountdowns();
}, 1000);