/* ================================================================
   Cuttle App Shell — navigation, rail, split columns
   ================================================================ */

const shell = document.getElementById('appShell');
const splitContainer = document.getElementById('splitContainer');

// ── Pin document scroll (iOS/iPad input-focus scroll-into-view) ──
// Focusing a textarea inside the chat iframe can still nudge the
// *parent* document. Keep the shell pinned without resizing layout
// to the keyboard (that fight caused the bottom white strip).
(function pinShellDocumentScroll() {
    const pin = () => {
        if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
        if (document.documentElement.scrollTop) document.documentElement.scrollTop = 0;
        if (document.body.scrollTop) document.body.scrollTop = 0;
    };
    window.addEventListener('scroll', pin, { passive: true });
    document.addEventListener('focusin', pin);
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', pin);
        window.visualViewport.addEventListener('scroll', pin);
    }
})();

// Hooks the fullscreen titlebar auto-hide exposes (set inside
// initElectronTitlebar). Space menus/bubbles pin the titlebar while open and
// re-arm the hide timer after they close.
let cuttleTitlebarRescheduleHide = null;
let cuttleTitlebarCancelHide = null;

// ── Electron frameless window chrome ───────────────────────────
(function initElectronTitlebar() {
    const api = window.electron;
    if (!api?.isElectron || !api.windowControls) return;

    document.body.classList.add('is-electron');
    const titlebar = document.getElementById('shellTitlebar');
    if (titlebar) titlebar.hidden = false;

    const reloadBtn = document.getElementById('shellWinReload');
    const fullscreenBtn = document.getElementById('shellWinFullscreen');
    const minBtn = document.getElementById('shellWinMin');
    const maxBtn = document.getElementById('shellWinMax');
    const closeBtn = document.getElementById('shellWinClose');
    const drag = document.getElementById('shellTitlebarDrag');
    const updateBtn = document.getElementById('shellWinUpdate');
    const hostBtn = document.getElementById('shellTitlebarHost');
    const workersBtn = document.getElementById('shellTitlebarWorkers');

    const setMaximizedUi = (maximized) => {
        document.body.classList.toggle('maximized', !!maximized);
        if (maxBtn) {
            maxBtn.title = maximized ? 'Restore' : 'Maximize';
            maxBtn.setAttribute('aria-label', maximized ? 'Restore' : 'Maximize');
        }
    };

    const setFullscreenUi = (fullscreen) => {
        document.body.classList.toggle('fullscreen', !!fullscreen);
        if (fullscreenBtn) {
            fullscreenBtn.title = fullscreen ? 'Exit full screen' : 'Full screen';
            fullscreenBtn.setAttribute('aria-label', fullscreen ? 'Exit full screen' : 'Full screen');
        }
    };

    reloadBtn?.addEventListener('click', () => {
        // Do NOT mutate iframe src before reload — that loads every chat twice
        // (once for _cb= navigation, again when the shell document reloads) and
        // is what made titlebar refresh feel 10–60s with two panes.
        // Cache bust happens via main-process clearCache + withCacheBust on restore.
        if (typeof api.windowControls.reload === 'function') {
            api.windowControls.reload();
            // If main-process reload never fires (wedged clearCache on old builds),
            // force a document reload as a safety net.
            setTimeout(() => {
                try {
                    if (!document.hidden) window.location.reload();
                } catch (_) { /* ignore */ }
            }, 4000);
        } else {
            console.warn('[shell] windowControls.reload missing — using location.reload');
            window.location.reload();
        }
    });
    fullscreenBtn?.addEventListener('click', () => {
        if (typeof api.windowControls.toggleFullscreen === 'function') api.windowControls.toggleFullscreen();
        else console.warn('[shell] windowControls.toggleFullscreen missing — rebuild Electron asar');
    });
    minBtn?.addEventListener('click', () => api.windowControls.minimize());
    maxBtn?.addEventListener('click', () => api.windowControls.maximize());
    closeBtn?.addEventListener('click', () => api.windowControls.close());
    drag?.addEventListener('dblclick', () => api.windowControls.maximize());

    api.windowControls.isMaximized().then(setMaximizedUi).catch(() => {});
    api.windowControls.onMaximizedChange(setMaximizedUi);

    const applyTitleVersion = (version) => {
        const ver = String(version || '').trim();
        if (!ver || !titlebar) return;
        const label = titlebar.querySelector('.shell-titlebar-label');
        if (!label) return;
        label.dataset.versioned = '1';
        label.textContent = `Cuttle ${ver}`;
    };

    const applyHostChip = (cfg) => {
        if (!hostBtn) return;
        const role = typeof cuttleDesktopRole === 'function'
            ? cuttleDesktopRole(cfg)
            : (cfg && cfg.clientMode ? 'client' : 'host');
        const isClient = role === 'client';
        hostBtn.hidden = false;
        hostBtn.dataset.role = role;
        hostBtn.textContent = isClient ? 'Client' : 'Host';
        hostBtn.title = isClient
            ? (`Connected to ${cfg && cfg.host ? cfg.host : 'remote Cuttle'} — click to change host`)
            : 'This PC is running the Cuttle daemon — click to connect to another host';
        if (cfg && cfg.packageVersion) applyTitleVersion(cfg.packageVersion);
    };
    let desktopCfg = { clientMode: false, host: '127.0.0.1' };
    applyHostChip(desktopCfg);
    // Two update channels share one titlebar button:
    //  - asar: packaged Client shell hash vs host /api/desktop/electron
    //  - mesh: this machine's cuttle_version vs host_cuttle_version (8s workers poll)
    let asarUpdateAvailable = false;
    let meshUpdate = { available: false, target: '', localVersion: '', hostVersion: '', localRev: '', hostRev: '' };

    const isRemoteClient = () => {
        if (typeof cuttleIsRemoteDesktopClient === 'function') {
            return !!cuttleIsRemoteDesktopClient(desktopCfg);
        }
        return !!(desktopCfg && desktopCfg.clientMode);
    };

    const refreshUpdateBtn = () => {
        if (!updateBtn) return;
        const asarOn = asarUpdateAvailable && isRemoteClient();
        const meshOn = !!meshUpdate.available;
        // Mesh (git) wins over asar when both are offered — same as click handler.
        const preferMesh = meshOn;
        const on = asarOn || meshOn;
        updateBtn.hidden = !on;
        updateBtn.classList.toggle('is-mesh-update', preferMesh);
        updateBtn.classList.toggle('is-asar-update', asarOn && !preferMesh);
        if (!on) {
            updateBtn.classList.remove('is-busy');
            return;
        }
        updateBtn.classList.remove('is-busy');
        const label = updateBtn.querySelector('.shell-win-update-label');
        if (label) label.textContent = 'Update';
        if (preferMesh) {
            const local = meshUpdate.localVersion || '?';
            const host = meshUpdate.hostVersion || '?';
            updateBtn.title = `Update available — ${local} → ${host} (git pull + restart)`;
            updateBtn.setAttribute('aria-label', `Update Cuttle from ${local} to ${host}`);
        } else {
            updateBtn.title = 'Update Cuttle desktop shell (download from host)';
            updateBtn.setAttribute('aria-label', 'Update Cuttle desktop shell');
        }
    };

    const applyAsarUpdateBtn = (payload) => {
        asarUpdateAvailable = !!(payload && payload.available);
        refreshUpdateBtn();
    };
    // Tell Jobs iframe who "you" are (iframe has no preload).
    const postViewerIdentity = (cfg) => {
        try {
            if (!cfg) return;
            const payload = {
                type: 'cuttle-viewer-identity',
                workerId: String(cfg.workerId || '').trim().toLowerCase(),
                clientMode: !!cfg.clientMode,
                hostname: String(cfg.hostname || '').trim().toLowerCase(),
            };
            document.querySelectorAll('iframe').forEach((fr) => {
                try { fr.contentWindow?.postMessage(payload, '*'); } catch (_) {}
            });
        } catch (_) {}
    };
    window.addEventListener('message', (ev) => {
        if (ev?.data?.type === 'cuttle-viewer-identity-request') {
            postViewerIdentity(desktopCfg);
        }
    });
    // Re-broadcast when a framed page finishes loading (Jobs has no preload).
    // No periodic rebroadcast — pages that miss the ready event can send
    // cuttle-viewer-identity-request (handled above).
    document.addEventListener('cuttle-page-ready', () => postViewerIdentity(desktopCfg));

    Promise.all([
        api.desktop?.getConfig?.().catch(() => null),
        api.desktop?.updateStatus?.().catch(() => null),
        // Host Electron may be an older process (daemon tray / long-lived shell)
        // without packageVersion in IPC — fall back to Flask's desktop manifest.
        fetch('/api/desktop/electron', { cache: 'no-store' })
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null),
    ]).then(([cfg, status, desktopManifest]) => {
        desktopCfg = cfg || desktopCfg;
        applyHostChip(desktopCfg);
        const fromCfg = desktopCfg && desktopCfg.packageVersion;
        const fromApi = desktopManifest && desktopManifest.packageVersion;
        // Packaged Host Cuttle.exe bakes a stale package.json into app.asar.
        // Titlebar must prefer Flask's live checkout version on Host; Client
        // still prefers its local shell version (update button compares hashes).
        const isClient = !!(desktopCfg && desktopCfg.clientMode);
        applyTitleVersion(isClient ? (fromCfg || fromApi) : (fromApi || fromCfg));
        applyAsarUpdateBtn(status);
        // Seed identity for Jobs iframe (no preload there) — fixes Client "you" = host.
        try {
            if (desktopCfg) {
                sessionStorage.setItem('cuttle_client_mode', desktopCfg.clientMode ? '1' : '0');
                sessionStorage.setItem(
                    'cuttle_viewer_worker_id',
                    String(desktopCfg.workerId || '').trim().toLowerCase()
                );
                if (desktopCfg.hostname) {
                    sessionStorage.setItem(
                        'cuttle_viewer_hostname',
                        String(desktopCfg.hostname || '').trim().toLowerCase()
                    );
                }
                postViewerIdentity(desktopCfg);
            }
        } catch (_) {}
    });
    api.desktop?.onUpdateAvailable?.(applyAsarUpdateBtn);
    hostBtn?.addEventListener('click', () => {
        if (typeof api.desktop?.showConnect === 'function') api.desktop.showConnect();
    });

    const applyWorkersChip = (onlineRemote, totalRemote) => {
        if (!workersBtn) return;
        const n = Math.max(0, Number(onlineRemote) || 0);
        workersBtn.dataset.count = String(n);
        if (n <= 0) {
            workersBtn.hidden = true;
            return;
        }
        workersBtn.hidden = false;
        workersBtn.textContent = n === 1 ? '1 worker' : `${n} workers`;
        const total = Math.max(n, Number(totalRemote) || 0);
        workersBtn.title = total > n
            ? `${n} other device${n === 1 ? '' : 's'} online (${total} registered) — open Jobs → Devices`
            : `${n} other device worker${n === 1 ? '' : 's'} online — open Jobs → Devices`;
    };

    const applyMeshUpdateFromWorkers = (data) => {
        const hostVer = String((data && data.host_cuttle_version) || '').trim();
        const hostRev = String((data && data.host_git_rev) || '').trim();
        const list = Array.isArray(data && data.workers) ? data.workers : [];
        const isClient = !!(desktopCfg && desktopCfg.clientMode);
        // Never fall back to host self_worker_id on a Client — that compared
        // Host→Host and hid the Update button even when the laptop was behind.
        let myId = String((desktopCfg && desktopCfg.workerId) || '').trim().toLowerCase();
        try {
            if (!myId) {
                myId = String(sessionStorage.getItem('cuttle_viewer_worker_id') || '')
                    .trim()
                    .toLowerCase();
            }
        } catch (_) {}
        if (!isClient && !myId) {
            myId = String((data && data.self_worker_id) || '').trim().toLowerCase();
        }
        const myHost = String((desktopCfg && desktopCfg.hostname) || '').trim().toLowerCase();
        let me = myId
            ? list.find((w) => String(w && w.worker_id || '').trim().toLowerCase() === myId)
            : null;
        if (!me && myHost) {
            me = list.find((w) => {
                if (!w || w.is_self) return false;
                return String(w.hostname || '').trim().toLowerCase() === myHost;
            }) || null;
        }
        if (!me && isClient) {
            // Last resort: sole non-self online worker (typical 1-laptop mesh).
            const remotes = list.filter((w) => w && !w.is_self && w.online);
            if (remotes.length === 1) me = remotes[0];
        }
        const myVer = String(
            (me && (me.cuttle_version || (me.meta && me.meta.cuttle_version)))
            || (desktopCfg && desktopCfg.packageVersion)
            || ''
        ).trim();
        const myRev = String(
            (me && (me.cuttle_git_rev || (me.meta && me.meta.cuttle_git_rev)))
            || ''
        ).trim();
        const revsDiffer = (a, b) => {
            const left = String(a || '').trim().toLowerCase();
            const right = String(b || '').trim().toLowerCase();
            if (!left || !right) return false;
            const shorter = left.length <= right.length ? left : right;
            const longer = left.length <= right.length ? right : left;
            if (shorter.length < 7) return left !== right;
            return !longer.startsWith(shorter);
        };
        const verBehind = !!(hostVer && myVer && hostVer !== myVer);
        const gitBehind = revsDiffer(hostRev, myRev);
        // Prefer server flag when this worker row is present.
        const flagged = !!(me && me.needs_update);
        const available = flagged || verBehind || gitBehind;
        const target = String(
            (me && me.worker_id)
            || myId
            || ''
        ).trim().toLowerCase();
        // When package versions match (common until bump), surface git revs so
        // the toast is not a confusing "0.2.18 → 0.2.18".
        const versionLabel = (ver, rev) => {
            const v = String(ver || '').trim();
            const r = String(rev || '').trim();
            if (v && r) return `${v} (${r.slice(0, 7)})`;
            return v || r || '?';
        };
        meshUpdate = {
            available: available && !!target,
            target,
            localVersion: versionLabel(myVer, myRev),
            hostVersion: versionLabel(hostVer, hostRev),
            localRev: myRev,
            hostRev,
            samePackageVersion: !!(myVer && hostVer && myVer === hostVer),
        };
        refreshUpdateBtn();
    };

    const refreshWorkersChip = async () => {
        if (!workersBtn && !updateBtn) return;
        try {
            const r = await fetch('/api/workers', { cache: 'no-store' });
            if (!r.ok) {
                applyWorkersChip(0, 0);
                meshUpdate = { available: false, target: '', localVersion: '', hostVersion: '', localRev: '', hostRev: '' };
                refreshUpdateBtn();
                return;
            }
            const data = await r.json();
            if (!data || data.enabled === false) {
                applyWorkersChip(0, 0);
                meshUpdate = { available: false, target: '', localVersion: '', hostVersion: '', localRev: '', hostRev: '' };
                refreshUpdateBtn();
                return;
            }
            applyMeshUpdateFromWorkers(data);
            // Titlebar badge = other machines only (this Host's local worker is not "extra capacity")
            if (typeof data.online_remote === 'number') {
                const list = Array.isArray(data.workers) ? data.workers : [];
                const remoteTotal = list.filter((w) => w && !w.is_self).length;
                applyWorkersChip(data.online_remote, remoteTotal);
                return;
            }
            const selfId = data.self_worker_id || '';
            const list = Array.isArray(data.workers) ? data.workers : [];
            const remote = list.filter((w) => w && !w.is_self && w.worker_id !== selfId);
            const online = remote.filter((w) => w.online).length;
            applyWorkersChip(online, remote.length);
        } catch (_) {
            applyWorkersChip(0, 0);
            meshUpdate = { available: false, target: '', localVersion: '', hostVersion: '', localRev: '', hostRev: '' };
            refreshUpdateBtn();
        }
    };

    workersBtn?.addEventListener('click', () => {
        try { localStorage.setItem('jobsPageTab', 'devices'); } catch (_) {}
        if (typeof navigate === 'function') navigate(0, '/jobs_page.html');
        else window.location.href = '/jobs_page.html';
    });

    // Polled by the shell heartbeat (not a separate setInterval).
    window.__cuttleShellPollWorkers = refreshWorkersChip;
    refreshWorkersChip();

    updateBtn?.addEventListener('click', async () => {
        const label = updateBtn.querySelector('.shell-win-update-label');
        const asarOn = asarUpdateAvailable && isRemoteClient();
        const meshOn = !!meshUpdate.available && !!meshUpdate.target;
        // Prefer mesh (git pull + relaunch) when the Client is behind on the
        // repo. ASAR-only replaces the packaged shell and used to win the
        // click even when the real intent was mesh — then quit without pull.
        if (!meshOn && asarOn && typeof api.desktop?.applyUpdate === 'function') {
            updateBtn.classList.add('is-busy');
            if (label) label.textContent = 'Updating…';
            try {
                const result = await api.desktop.applyUpdate();
                if (!result || !result.ok) {
                    updateBtn.classList.remove('is-busy');
                    if (label) label.textContent = 'Update';
                    if (window.showToast) window.showToast(result?.error || 'Desktop update failed', 'error');
                }
            } catch (err) {
                updateBtn.classList.remove('is-busy');
                if (label) label.textContent = 'Update';
                if (window.showToast) window.showToast(err.message || String(err), 'error');
            }
            return;
        }

        if (!meshOn) return;
        updateBtn.classList.add('is-busy');
        if (label) label.textContent = 'Updating…';
        const isClient = !!(desktopCfg && desktopCfg.clientMode);
        try {
            const r = await fetch('/api/workers/self-update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target: meshUpdate.target,
                    submitted_by: 'titlebar',
                    // Host Electron refresh should not kill cuttle_daemon.
                    no_daemon: !isClient,
                }),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok || !data.success) {
                throw new Error(data.error || (`HTTP ${r.status}`));
            }
            const jobId = String(
                (data.job && (data.job.id || data.job.job_id))
                || data.job_id
                || ''
            ).trim();
            if (label) label.textContent = 'Pulling…';
            if (window.showToast) {
                window.showToast(
                    `Updating ${meshUpdate.target} (${meshUpdate.localVersion} → ${meshUpdate.hostVersion})…`,
                    'info'
                );
            }
            // Wait for stash+pull to finish so dirty-tree / ff failures are not silent.
            let final = data.job || null;
            if (jobId) {
                const deadline = Date.now() + 180000;
                while (Date.now() < deadline) {
                    await new Promise((res) => setTimeout(res, 1500));
                    try {
                        const sr = await fetch(`/api/workers/jobs/${encodeURIComponent(jobId)}`, {
                            cache: 'no-store',
                        });
                        const sj = await sr.json().catch(() => ({}));
                        final = (sj && (sj.job || sj)) || final;
                        const st = String((final && final.status) || '').toLowerCase();
                        if (st === 'succeeded' || st === 'failed' || st === 'cancelled') break;
                        if (label && st === 'running') label.textContent = 'Pulling…';
                    } catch (_) { /* keep waiting */ }
                }
            }
            const st = String((final && final.status) || '').toLowerCase();
            if (st === 'failed' || st === 'cancelled') {
                const err = String(
                    (final && (final.error || (final.result && final.result.error)))
                    || 'Self-update failed'
                );
                throw new Error(err);
            }
            if (st && st !== 'succeeded') {
                throw new Error(`Self-update timed out (status=${st || 'unknown'})`);
            }
            if (label) label.textContent = 'Restarting…';
            // Job success only means the updater was scheduled. Confirm the
            // worker actually advanced (or toast a clear failure).
            const beforeVer = String(meshUpdate.localVersion || '');
            const expectHost = String(meshUpdate.hostVersion || '');
            let advanced = false;
            const verifyDeadline = Date.now() + 90000;
            while (Date.now() < verifyDeadline) {
                await new Promise((res) => setTimeout(res, 2500));
                try {
                    await refreshWorkersChip();
                } catch (_) {}
                const nowVer = String(meshUpdate.localVersion || '');
                const stillNeeds = !!meshUpdate.available;
                if (nowVer && nowVer !== beforeVer) {
                    advanced = true;
                    break;
                }
                // Host/client versions match and Update cleared.
                if (!stillNeeds && expectHost && nowVer && nowVer === expectHost) {
                    advanced = true;
                    break;
                }
                if (!stillNeeds && nowVer && beforeVer && nowVer !== beforeVer) {
                    advanced = true;
                    break;
                }
            }
            if (!advanced) {
                throw new Error(
                    'Update scheduled but this device is still on '
                    + (meshUpdate.localVersion || beforeVer || '?')
                    + '. Check Desktop\\cuttle-self-update-last.log on that machine.'
                );
            }
            if (window.showToast) {
                window.showToast(
                    `Updated to ${meshUpdate.localVersion || expectHost}.`,
                    'success'
                );
            }
            setTimeout(() => {
                updateBtn.classList.remove('is-busy');
                if (label) label.textContent = 'Update';
                refreshUpdateBtn();
            }, 1500);
        } catch (err) {
            updateBtn.classList.remove('is-busy');
            if (label) label.textContent = 'Update';
            if (window.showToast) window.showToast(err.message || String(err), 'error');
        }
    });

    // Fullscreen: titlebar overlays and auto-hides; a top-edge hotzone reveals it.
    // Iframes swallow mousemove, so a dedicated strip is required.
    let hideTimer = null;
    let revealTimer = null;
    const HOTZONE_PX = 10;
    // Hover intent: a cursor skimming the top edge (e.g. reaching for the
    // blade toolbar) should not pop the bar over it.
    const REVEAL_DELAY_MS = 180;
    const HIDE_DELAY_MS = 150;
    let hotzone = document.getElementById('shellTitlebarHotzone');
    if (!hotzone) {
        hotzone = document.createElement('div');
        hotzone.id = 'shellTitlebarHotzone';
        hotzone.className = 'shell-titlebar-hotzone';
        hotzone.setAttribute('aria-hidden', 'true');
        titlebar.insertAdjacentElement('afterend', hotzone);
    }

    const cancelPendingReveal = () => {
        if (revealTimer) {
            clearTimeout(revealTimer);
            revealTimer = null;
        }
    };
    const revealTitlebar = () => {
        cancelPendingReveal();
        if (hideTimer) {
            clearTimeout(hideTimer);
            hideTimer = null;
        }
        titlebar.classList.add('is-revealed');
    };
    // An open space menu/bubble, or a space tab mid-rename, pins the titlebar.
    const isTitlebarPinned = () => {
        if (typeof isSpaceCtxOpen === 'function' && isSpaceCtxOpen()) return true;
        const active = document.activeElement;
        return !!(active && active.classList.contains('shell-space-rename') && titlebar.contains(active));
    };
    const scheduleHideTitlebar = () => {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = null;
        if (isTitlebarPinned()) return;
        hideTimer = setTimeout(() => {
            hideTimer = null;
            // Re-check: a rename can start inside the delay (menu → Rename).
            if (isTitlebarPinned()) return;
            titlebar.classList.remove('is-revealed');
        }, HIDE_DELAY_MS);
    };
    cuttleTitlebarRescheduleHide = () => {
        if (!document.body.classList.contains('fullscreen')) return;
        if (titlebar.matches(':hover')) return;
        scheduleHideTitlebar();
    };
    cuttleTitlebarCancelHide = () => {
        if (hideTimer) {
            clearTimeout(hideTimer);
            hideTimer = null;
        }
    };

    const onFullscreenChange = (fullscreen) => {
        setFullscreenUi(fullscreen);
        if (!fullscreen) {
            cancelPendingReveal();
            if (hideTimer) {
                clearTimeout(hideTimer);
                hideTimer = null;
            }
            titlebar.classList.remove('is-revealed');
        }
    };
    api.windowControls.isFullscreen?.().then(onFullscreenChange).catch(() => {});
    api.windowControls.onFullscreenChange?.(onFullscreenChange);
    if (typeof wireElectronChatFind === 'function') wireElectronChatFind(api);

    hotzone.addEventListener('pointerenter', () => {
        if (titlebar.classList.contains('is-revealed')) {
            revealTitlebar();
            return;
        }
        cancelPendingReveal();
        revealTimer = setTimeout(revealTitlebar, REVEAL_DELAY_MS);
    });
    hotzone.addEventListener('pointerleave', cancelPendingReveal);
    titlebar.addEventListener('pointerenter', revealTitlebar);
    titlebar.addEventListener('pointerleave', (e) => {
        if (!document.body.classList.contains('fullscreen')) return;
        const y = e.clientY;
        if (y <= HOTZONE_PX) return;
        scheduleHideTitlebar();
    });
})();

// ── Default UI layout order (used when no saved layout) ─────────
// Must list every `.rail-item[data-id]` in the main rail, in canonical order (matches app_shell.html).
const CANONICAL_RAIL_ITEM_ORDER = [
    'nav-chat',
    'nav-editor',
    'nav-git',
    'nav-jobs',
    'nav-dashboards',
    'nav-achievements',
    'nav-gizmos',
    'nav-projects',
    'nav-apps',
];
// 'nav-tools' and 'nav-automation' (Home Automation) are retired. Saved layouts
// referencing retired rail entries are filtered out during restore.
// Footer may only contain these (do not put page nav buttons here — breaks reorder on load).
const CANONICAL_RAIL_FOOTER_ORDER = ['nav-account', 'nav-notifications', 'nav-workspace', 'nav-settings', 'panelToggle'];
// The Apps launcher is the way back to every stashed app, so it can never be removed.
const RAIL_LOCKED_IDS = new Set(['nav-apps']);
// Cuttle web apps that live in the Apps grid (not the blade bar) until the user pins them.
const DEFAULT_RAIL_HIDDEN = ['nav-achievements', 'nav-gizmos', 'nav-projects'];
// Bump when defaults change; saved layouts below this version get DEFAULT_RAIL_HIDDEN merged in once.
const RAIL_LAYOUT_VERSION = 7;

const DEFAULT_LAYOUT = {
    rail_items: CANONICAL_RAIL_ITEM_ORDER.filter(id => !DEFAULT_RAIL_HIDDEN.includes(id)),
    rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
    rail_hidden: DEFAULT_RAIL_HIDDEN.slice(),
    layout_version: RAIL_LAYOUT_VERSION,
};

/** Shared rail layout for every blade (not per-bar). */
let lastUILayout = {
    rail_items: DEFAULT_LAYOUT.rail_items.slice(),
    rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
    rail_hidden: DEFAULT_RAIL_HIDDEN.slice(),
    layout_version: RAIL_LAYOUT_VERSION,
};
let railEditing = false;
let railSuppressClick = false;
let railCustomizeGlobalsReady = false;
const RAIL_HOLD_MS = 500;
const RAIL_HOLD_MOVE_PX = 8;

// ── Restore rail & active page from session ──────────────────
const STORAGE_PAGE = 'shell_page';
const STORAGE_RAIL = 'shell_rail_collapsed';
const STORAGE_SPLITS = 'shell_split_count';
/** Durable multi-pane layout (survives browser/Electron close). */
const STORAGE_LAYOUT = 'shell_split_layout';

const _shellIsMobile = () => window.matchMedia('(max-width: 768px)').matches;
// Mobile defaults to collapsed rail for more chat space; desktop keeps prior preference.
let railCollapsed = _shellIsMobile()
    ? sessionStorage.getItem(STORAGE_RAIL) !== 'false'
    : sessionStorage.getItem(STORAGE_RAIL) === 'true';
/** True while recreate-from-localStorage is in progress (skip mid-loop persists). */
let _restoringLayout = false;
/** Shift/Alt/Ctrl held — rail +/× preview icons. */
let _railModShift = false;
let _railModAlt = false;
let _railModCtrl = false;
const _railHeightObservers = new WeakMap();

function normalizeSplitOrientation(raw) {
    return raw === 'vertical' ? 'vertical' : 'horizontal';
}

function isSplitGroupEl(el) {
    return !!(el && (el.id === 'splitContainer' || el.classList?.contains('split-group')));
}

function getGroupOrientation(groupEl) {
    if (!groupEl) return 'horizontal';
    if (groupEl.classList.contains('split-vertical')) return 'vertical';
    if (groupEl.classList.contains('split-horizontal')) return 'horizontal';
    return normalizeSplitOrientation(groupEl.dataset.orientation);
}

function applyGroupOrientation(groupEl, orientation) {
    if (!groupEl) return;
    const dir = normalizeSplitOrientation(orientation);
    groupEl.dataset.orientation = dir;
    groupEl.classList.toggle('split-vertical', dir === 'vertical');
    groupEl.classList.toggle('split-horizontal', dir === 'horizontal');
    if (groupEl.id === 'splitContainer') {
        groupEl.classList.add('split-group');
    }
}

function getLeafParentGroup(leafEl) {
    let p = leafEl && leafEl.parentElement;
    while (p && p !== document.body) {
        if (isSplitGroupEl(p)) return p;
        p = p.parentElement;
    }
    return splitContainer;
}

/** True if a pane/group is pending deferred teardown (must leave layout counts). */
function isSplitDiscardedEl(el) {
    return !!(el && (el.dataset?.splitDiscarded === '1' || el.dataset?.paneClosing === '1'));
}

/** Direct pane/group children of a split group (skips resize handles + discarded).
 *  Order follows DOM order — flex `order` is only a mirror assigned by
 *  rebuildGroupResizeHandles. Sorting by `order` here used to pull a freshly
 *  appended pane (no order yet → 0) between older siblings (0, 2, 4…), so
 *  primary `+` produced A–C–B instead of A–B–C.
 */
function getGroupChildNodes(groupEl) {
    if (!groupEl) return [];
    return Array.from(groupEl.children).filter((el) => (
        !isSplitDiscardedEl(el)
        && (el.classList.contains('split-column') || el.classList.contains('split-group'))
    ));
}

function newLeafId() {
    return 'leaf_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

function newGroupId() {
    return 'grp_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

function createSplitGroupEl(orientation) {
    const el = document.createElement('div');
    el.className = 'split-group';
    el.dataset.groupId = newGroupId();
    applyGroupOrientation(el, orientation);
    return el;
}

/**
 * Serialize nested layout from the live DOM (v2).
 * Leaves carry page/flex; groups carry orientation + children.
 */
function snapshotLayoutTree(nodeEl) {
    if (!nodeEl) return null;
    if (nodeEl.classList.contains('split-column')) {
        const idx = parseInt(nodeEl.dataset.column, 10);
        const state = columnState.get(idx);
        const handles = lastChatByColumn.get(idx) || {};
        const page = pageWithPaneSession(idx, (state && state.page) || '/chat_page.html');
        const entry = {
            type: 'leaf',
            id: nodeEl.dataset.leafId || newLeafId(),
            page,
            flex: nodeEl.style.flex || '',
        };
        if (handles.chat) entry.chat = String(handles.chat);
        if (handles.terminal) entry.terminal = String(handles.terminal);
        if (!entry.chat || !entry.terminal) {
            try {
                const url = new URL(page, window.location.origin);
                const kind = chatSurfaceKind(url.pathname);
                const cid = url.searchParams.get('chat') || url.searchParams.get('session');
                if (kind && cid && !entry[kind]) entry[kind] = String(cid);
            } catch (_) {}
        }
        return entry;
    }
    if (isSplitGroupEl(nodeEl)) {
        const children = getGroupChildNodes(nodeEl).map(snapshotLayoutTree).filter(Boolean);
        if (children.length === 1 && nodeEl.id === 'splitContainer') {
            return children[0];
        }
        if (children.length === 0) {
            return {
                type: 'leaf',
                id: newLeafId(),
                page: '/chat_page.html',
                flex: '',
            };
        }
        if (children.length === 1) return children[0];
        return {
            type: 'group',
            id: nodeEl.dataset.groupId || newGroupId(),
            orientation: getGroupOrientation(nodeEl),
            flex: nodeEl.style.flex || '',
            children,
        };
    }
    return null;
}

/** Flatten tree leaves in DFS reading order (agent / workspace flat API). */
function flattenLayoutLeaves(node, out = []) {
    if (!node) return out;
    if (node.type === 'leaf') {
        out.push({
            page: node.page || '/chat_page.html',
            flex: node.flex || '',
            chat: node.chat,
            terminal: node.terminal,
        });
        return out;
    }
    if (node.type === 'group' && Array.isArray(node.children)) {
        node.children.forEach((ch) => flattenLayoutLeaves(ch, out));
    }
    return out;
}

function primaryOrientationFromTree(node) {
    if (!node) return 'horizontal';
    if (node.type === 'group') return normalizeSplitOrientation(node.orientation);
    return 'horizontal';
}

/** Migrate flat v1 { orientation, columns[] } → nested tree root. */
function flatLayoutToTree(columns, orientation) {
    const cols = Array.isArray(columns) ? columns : [];
    if (cols.length < 1) {
        return { type: 'leaf', id: newLeafId(), page: '/chat_page.html', flex: '' };
    }
    if (cols.length === 1) {
        const c = cols[0] || {};
        return {
            type: 'leaf',
            id: newLeafId(),
            page: c.page || '/chat_page.html',
            flex: c.flex || '',
            chat: c.chat,
            terminal: c.terminal,
        };
    }
    return {
        type: 'group',
        id: newGroupId(),
        orientation: normalizeSplitOrientation(orientation),
        flex: '',
        children: cols.map((c) => ({
            type: 'leaf',
            id: newLeafId(),
            page: (c && c.page) || '/chat_page.html',
            flex: (c && c.flex) || '',
            chat: c && c.chat,
            terminal: c && c.terminal,
        })),
    };
}

function readSplitLayout() {
    try {
        const raw = localStorage.getItem(STORAGE_LAYOUT);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object') return null;
        if (data.version >= 2 && data.root && typeof data.root === 'object') {
            return { version: 2, root: data.root };
        }
        if (Array.isArray(data.columns) && data.columns.length >= 1) {
            return {
                version: 2,
                root: flatLayoutToTree(data.columns, data.orientation),
                _migratedFromV1: true,
            };
        }
        return null;
    } catch (_) {
        return null;
    }
}

/** Reading-order leaves (DFS document order — nested groups included). */
function getSplitColumns() {
    if (!splitContainer) return [];
    return Array.from(splitContainer.querySelectorAll('.split-column'))
        .filter((c) => !isSplitDiscardedEl(c));
}

/** Embed a pane's last chat/terminal handle on the page URL when missing. */
function canonicalizeShellPage(page) {
    const fallback = '/chat_page.html';
    try {
        const u = new URL(page || fallback, window.location.origin);
        if (['/task_management.html', '/media_player.html', '/git_ui.html', '/wizard_page.html'].includes(u.pathname)) return fallback;
        u.searchParams.delete('_cb');
        let path = u.pathname || fallback;
        // Guard against blank / non-page navigations that surface as Werkzeug "Not Found".
        if (!path || path === '/' || path === '/index.html') path = fallback;
        if (!/\.html$/i.test(path)) path = fallback;
        return path + u.search + u.hash;
    } catch (_) {
        return fallback;
    }
}

function pageWithPaneSession(colIdx, page) {
    const fallback = canonicalizeShellPage(page || '/chat_page.html');
    try {
        const url = new URL(fallback, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return url.pathname + url.search + url.hash;
        if (!url.searchParams.get('chat') && !url.searchParams.get('session')) {
            const remembered = (lastChatByColumn.get(colIdx) || {})[kind];
            if (remembered) url.searchParams.set('chat', remembered);
        }
        url.searchParams.delete('_cb');
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return fallback;
    }
}

/** Strip chat/session from a page URL — used for brand-new panes so they never
 *  inherit another column's remembered session. */
function bareShellPage(page) {
    const fallback = canonicalizeShellPage(page || '/chat_page.html');
    try {
        const url = new URL(fallback, window.location.origin);
        url.searchParams.delete('chat');
        url.searchParams.delete('session');
        url.searchParams.delete('_cb');
        return url.pathname + (url.search || '') + (url.hash || '');
    } catch (_) {
        return '/chat_page.html';
    }
}

/**
 * Page URL for a layout-tree leaf. Trust the snapshot (already session-aware).
 * Do not re-apply lastChatByColumn — that contaminated blank panes when remumber
 * briefly duplicated data-column indices.
 */
function pageFromLayoutLeaf(node) {
    if (!node) return '/chat_page.html';
    let page = canonicalizeShellPage(node.page || '/chat_page.html');
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (kind && !url.searchParams.get('chat') && !url.searchParams.get('session')) {
            const handle = node[kind] || node.chat || node.terminal;
            if (handle) url.searchParams.set('chat', String(handle));
        }
        url.searchParams.delete('_cb');
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return page;
    }
}

/** Equal flex among direct children of a split group. */
function equalizeGroupChildren(groupEl) {
    if (!groupEl) return;
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    applySplitRatioFlex(
        cols,
        Array(cols.length).fill(1),
        getGroupOrientation(groupEl),
        groupEl
    );
}

function snapshotSplitColumns() {
    const tree = snapshotLayoutTree(splitContainer);
    return flattenLayoutLeaves(tree);
}

function persistSplitLayout() {
    if (_restoringLayout) return;
    try {
        const root = snapshotLayoutTree(splitContainer);
        const columns = flattenLayoutLeaves(root);
        const orientation = primaryOrientationFromTree(root);
        localStorage.setItem(STORAGE_LAYOUT, JSON.stringify({ version: 2, root, orientation, columns }));
        localStorage.setItem(STORAGE_SPLITS, String(columns.length));
        // Flat columns for agents ("1st pane" / "pane 2") — DFS reading order.
        try {
            fetch('/api/shell/panes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version: 1, orientation, columns }),
                keepalive: true,
            }).catch(() => {});
        } catch (_) {}
        scheduleBroadcastOpenPanes();
        try { syncSpaceActivityTabs(); } catch (_) {}
        try { scheduleSpaceActivityPoll(false); } catch (_) {}
    } catch (_) {}
}

// ── Last chat / terminal handle per column ─────────────────────
// The rail nav sends a bare "/chat_page.html", so leaving Chat for Settings and
// coming back landed on the new-chat splash. Remember what each column had open
// (durably, so a reload keeps it) and re-attach ?chat= on the way back.
const STORAGE_LAST_CHAT = 'shell_last_chat';

function readLastChatHandles() {
    const map = new Map();
    try {
        const raw = localStorage.getItem(STORAGE_LAST_CHAT);
        const data = raw ? JSON.parse(raw) : null;
        if (!data || typeof data !== 'object' || Array.isArray(data)) return map;
        Object.keys(data).forEach((key) => {
            const idx = parseInt(key, 10);
            const entry = data[key];
            if (!Number.isFinite(idx) || !entry || typeof entry !== 'object') return;
            const clean = {};
            if (entry.chat) clean.chat = String(entry.chat);
            if (entry.terminal) clean.terminal = String(entry.terminal);
            if (Object.keys(clean).length) map.set(idx, clean);
        });
    } catch (_) {}
    return map;
}

const lastChatByColumn = readLastChatHandles();

function persistLastChatHandles() {
    try {
        const out = {};
        lastChatByColumn.forEach((entry, idx) => { out[idx] = entry; });
        localStorage.setItem(STORAGE_LAST_CHAT, JSON.stringify(out));
    } catch (_) {}
}

/** 'chat' | 'terminal' for the two session-bearing surfaces, else null. */
function chatSurfaceKind(pathname) {
    if (pathname === '/chat_page.html') return 'chat';
    if (pathname === '/terminal_page.html') return 'terminal';
    return null;
}

function rememberChatHandle(colIdx, kind, chatId) {
    if (!kind || !Number.isFinite(colIdx)) return;
    const entry = lastChatByColumn.get(colIdx) || {};
    const next = chatId == null || chatId === '' ? '' : String(chatId);
    if (next) entry[kind] = next;
    else delete entry[kind];
    if (Object.keys(entry).length) lastChatByColumn.set(colIdx, entry);
    else lastChatByColumn.delete(colIdx);
    persistLastChatHandles();
}

/** Record the handle carried by a page URL (no-op for pages without one). */
function rememberChatHandleFromPage(colIdx, page) {
    if (!page) return;
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return;
        const cid = url.searchParams.get('chat') || url.searchParams.get('session');
        if (cid) rememberChatHandle(colIdx, kind, cid);
    } catch (_) {}
}

/**
 * Re-attach a column's last session when nav asks for a bare chat/terminal page.
 * Deep-links, sign-in, and an explicit new chat (handle already forgotten) pass through.
 */
function withRememberedChat(colIdx, page) {
    try {
        const url = new URL(page, window.location.origin);
        const kind = chatSurfaceKind(url.pathname);
        if (!kind) return page;
        if (url.searchParams.get('chat') || url.searchParams.get('session')) return page;
        if (url.searchParams.has('signin') || url.searchParams.has('new')) return page;
        const remembered = (lastChatByColumn.get(colIdx) || {})[kind];
        if (!remembered) return page;
        url.searchParams.set('chat', remembered);
        return url.pathname + url.search + url.hash;
    } catch (_) {
        return page;
    }
}

function findColumnIndexForSource(source) {
    if (!source) return 0;
    const cols = document.querySelectorAll('.split-column');
    for (const col of cols) {
        const idx = parseInt(col.dataset.column, 10);
        const frame = getLiveFrame(idx);
        try {
            if (frame && frame.contentWindow === source) return idx;
        } catch (_) {}
    }
    return 0;
}

// ── Split-pane live-status hub ─────────────────────────────────
// Chromium shares ~6 HTTP/1.1 connections per origin across all chat iframes.
// Per-pane live-status + message sync used to starve sibling panes (one updates,
// refresh flips which one works). Shell owns one batch poll and fans out.
let focusedColumnIdx = 0;
const paneSessionByColumn = new Map(); // colIdx -> sessionId string
/** Live-status is driven by the shell heartbeat (no dedicated interval). */
let liveStatusHubArmed = false;
let liveStatusHubInFlight = false;

/** colIdx -> project path for pending-changes hub (dedupe by path across panes). */
const paneProjectByColumn = new Map();
let pendingChangesHubTimer = null;
let pendingChangesPathInFlight = new Map(); // normalized path -> Promise

const PENDING_CHANGES_VISIBLE_MS = 12000;
const PENDING_CHANGES_HIDDEN_MS = 45000;

function normalizeShellProjectPath(p) {
    return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function setFocusedColumn(idx) {
    if (!Number.isFinite(idx) || idx < 0) return;
    if (focusedColumnIdx === idx) return;
    focusedColumnIdx = idx;
    broadcastPaneFocus();
}

/** Nudge visible usage-live reports to rescan after a tab/space change.
 * Hidden frames stopped polling while away and nothing inside them fires
 * on the return transition; the chat frame's own rescan is idempotent. */
function broadcastUsageLiveWake() {
    document.querySelectorAll('.split-column .shell-main iframe').forEach((frame) => {
        if (!isChatPageFrame(frame) || !frame.contentWindow) return;
        try {
            const live = frame.contentWindow.CuttleUsageLive;
            if (live && typeof live.wake === 'function') {
                live.wake();
                return;
            }
        } catch (_) {}
        try {
            frame.contentWindow.postMessage({ type: 'cuttle-usage-live-wake' }, '*');
        } catch (_) {}
    });
}

function broadcastPaneFocus() {
    document.querySelectorAll('.split-column').forEach((col) => {
        const idx = parseInt(col.dataset.column, 10);
        const frame = getLiveFrame(idx);
        if (!frame || !frame.contentWindow) return;
        try {
            frame.contentWindow.postMessage({
                type: 'cuttle-pane-focus',
                focused: idx === focusedColumnIdx,
                column: idx,
            }, '*');
        } catch (_) {}
    });
}

function registerPaneChatSession(source, sessionId) {
    const colIdx = findColumnIndexForSource(source);
    if (sessionId == null || sessionId === '') {
        paneSessionByColumn.delete(colIdx);
    } else {
        paneSessionByColumn.set(colIdx, String(sessionId));
    }
    ensureLiveStatusHub();
    scheduleBroadcastOpenPanes();
}

/**
 * Chats/terminals actually showing in THIS window's split columns.
 * Local to this renderer (Electron/browser instance) — not the Flask
 * last-writer /api/shell/panes snapshot used by agents.
 */
function getOpenPaneSessions() {
    const cols = getSplitColumns();
    const out = [];
    cols.forEach((col, visualIdx) => {
        const idx = parseInt(col.dataset.column, 10);
        const state = columnState.get(idx);
        const page = (state && state.page) || '';
        let kind = null;
        let sid = null;
        try {
            const url = new URL(page || '/chat_page.html', window.location.origin);
            kind = chatSurfaceKind(url.pathname);
            sid = url.searchParams.get('chat') || url.searchParams.get('session');
        } catch (_) {}
        const live = paneSessionByColumn.get(idx);
        // Chat iframes report live session ids; don't let a stale chat id
        // override a terminal (or other) surface in the same column.
        if (kind === 'chat' && live) sid = live;
        if (!kind || !sid) return;
        out.push({
            sessionId: String(sid),
            pane: visualIdx + 1,
            column: idx,
            kind,
            focused: idx === focusedColumnIdx,
        });
    });
    return out;
}

function broadcastOpenPanes() {
    const panes = getOpenPaneSessions();
    broadcastToFrames({ type: 'cuttle-open-panes', panes });
}

let _openPanesBroadcastTimer = null;
function scheduleBroadcastOpenPanes() {
    if (_openPanesBroadcastTimer) return;
    _openPanesBroadcastTimer = setTimeout(() => {
        _openPanesBroadcastTimer = null;
        try { broadcastOpenPanes(); } catch (_) {}
    }, 40);
}

window.getOpenPaneSessions = getOpenPaneSessions;

function ensureLiveStatusHub() {
    if (liveStatusHubArmed) return;
    liveStatusHubArmed = true;
    // Immediate sample; cadence continues via runShellHeartbeat.
    pollLiveStatusHub();
}

function registerPaneProject(source, projectPath) {
    const colIdx = findColumnIndexForSource(source);
    if (!Number.isFinite(colIdx) || colIdx < 0) return;
    const path = projectPath != null && String(projectPath).trim()
        ? String(projectPath).trim()
        : '';
    if (!path) {
        paneProjectByColumn.delete(colIdx);
    } else {
        paneProjectByColumn.set(colIdx, path);
        ensurePendingChangesHub();
        // Fresh registration — sample this path once (deduped if already in flight).
        refreshPendingChangesPath(path);
    }
}

function uniquePaneProjectPaths() {
    return [...new Set(
        [...paneProjectByColumn.values()].filter((p) => p && String(p).trim())
    )];
}

async function refreshPendingChangesPath(projectPath) {
    const path = String(projectPath || '').trim();
    if (!path) return;
    const key = normalizeShellProjectPath(path);
    if (pendingChangesPathInFlight.has(key)) {
        return pendingChangesPathInFlight.get(key);
    }
    const work = (async () => {
        try {
            const qs = new URLSearchParams();
            qs.set('path', path);
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 20000);
            let data = null;
            try {
                const r = await fetch(
                    '/api/git/pending-changes?' + qs.toString(),
                    { credentials: 'include', cache: 'no-store', signal: controller.signal }
                );
                data = await r.json().catch(() => null);
            } finally {
                clearTimeout(timeout);
            }
            if (!data) return;
            broadcastToFrames({
                type: 'cuttle-pending-changes-data',
                path,
                data,
            });
        } catch (_) {
        } finally {
            pendingChangesPathInFlight.delete(key);
        }
    })();
    pendingChangesPathInFlight.set(key, work);
    return work;
}

async function pollPendingChangesHub() {
    const paths = uniquePaneProjectPaths();
    if (!paths.length) return;
    await Promise.all(paths.map((p) => refreshPendingChangesPath(p)));
}

function pendingChangesHubDelayMs() {
    return document.hidden ? PENDING_CHANGES_HIDDEN_MS : PENDING_CHANGES_VISIBLE_MS;
}

function schedulePendingChangesHub(immediate) {
    if (pendingChangesHubTimer) {
        clearTimeout(pendingChangesHubTimer);
        pendingChangesHubTimer = null;
    }
    if (!uniquePaneProjectPaths().length) return;
    const delay = immediate ? 0 : pendingChangesHubDelayMs();
    pendingChangesHubTimer = setTimeout(async () => {
        pendingChangesHubTimer = null;
        try {
            await pollPendingChangesHub();
        } finally {
            schedulePendingChangesHub(false);
        }
    }, delay);
}

function ensurePendingChangesHub() {
    if (pendingChangesHubTimer) return;
    schedulePendingChangesHub(true);
}

async function pollLiveStatusHub() {
    if (liveStatusHubInFlight) return;
    const ids = [...new Set([...paneSessionByColumn.values()].filter(Boolean))];
    if (!ids.length) return;
    liveStatusHubInFlight = true;
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        let data = null;
        try {
            const r = await fetch(
                '/api/chat-live-status-batch?session_ids=' + encodeURIComponent(ids.join(',')),
                { credentials: 'include', cache: 'no-store', signal: controller.signal }
            );
            if (r.ok) data = await r.json();
        } finally {
            clearTimeout(timeout);
        }
        if (!data || !data.success || !data.statuses) return;
        paneSessionByColumn.forEach((sid, colIdx) => {
            const frame = getLiveFrame(colIdx);
            if (!frame || !frame.contentWindow) return;
            const status = data.statuses[String(sid)] || null;
            try {
                frame.contentWindow.postMessage({
                    type: 'cuttle-live-status',
                    sessionId: sid,
                    status,
                    focused: colIdx === focusedColumnIdx,
                }, '*');
            } catch (_) {}
        });
    } catch (_) {
    } finally {
        liveStatusHubInFlight = false;
    }
}

document.addEventListener('pointerdown', (e) => {
    const col = e.target && e.target.closest ? e.target.closest('.split-column') : null;
    if (!col) return;
    const idx = parseInt(col.dataset.column, 10);
    if (Number.isFinite(idx)) setFocusedColumn(idx);
}, true);

// Initial page: restore from session; default to chat_page for immediate usability
const getDefaultPage = () => {
    const skip = localStorage.getItem('cuttle_skip_welcome');
    if (skip) return '/chat_page.html';
    // Prefer chat over landing so users land directly in the full chat UI
    return '/chat_page.html';
};
// Deep-link ?chat=<id> wins. Otherwise restore durable split layout (incl. per-pane
// ?chat=). Same-tab sessionStorage alone must not revive a chat on bare / — that
// used to reopen ?chat=N after navigating to "/".
const bootParams = new URLSearchParams(window.location.search);
const bootChatId = bootParams.get('chat') || bootParams.get('session');
const savedBootLayout = readSplitLayout();

function isTerminalChatHandle(chatId) {
    if (chatId == null || chatId === '') return false;
    try {
        const map = JSON.parse(localStorage.getItem('cuttle_terminal_sessions_v1') || '{}');
        return !!(map && map[String(chatId)]);
    } catch (_) {
        return false;
    }
}

let currentPage;
if (bootChatId) {
    currentPage = isTerminalChatHandle(bootChatId)
        ? '/terminal_page.html?chat=' + encodeURIComponent(bootChatId)
        : '/chat_page.html?chat=' + encodeURIComponent(bootChatId);
} else if (savedBootLayout?.root) {
    const leaves = flattenLayoutLeaves(savedBootLayout.root);
    if (leaves[0]?.page) currentPage = leaves[0].page;
    else {
        const stored = sessionStorage.getItem(STORAGE_PAGE) || getDefaultPage();
        currentPage = stored;
    }
} else {
    const stored = sessionStorage.getItem(STORAGE_PAGE) || getDefaultPage();
    try {
        const storedUrl = new URL(stored, window.location.origin);
        if (storedUrl.pathname === '/chat_page.html') {
            currentPage = '/chat_page.html';
            sessionStorage.setItem(STORAGE_PAGE, '/chat_page.html');
        } else {
            currentPage = stored;
        }
    } catch (_) {
        currentPage = getDefaultPage();
    }
}

currentPage = canonicalizeShellPage(currentPage);
sessionStorage.setItem(STORAGE_PAGE, currentPage);
rememberChatHandleFromPage(0, currentPage);

/** Keep the address-bar ?chat= in sync with the active chat session.
 * Mobile system-back uses shellBackStack (not WebView history) so back never
 * falls through to minimize/setup. */
let shellApplyingHistory = false;
const shellBackStack = [];
const SHELL_BACK_STACK_MAX = 40;

function shellPageFromChatId(chatId) {
    if (chatId) {
        return '/chat_page.html?chat=' + encodeURIComponent(String(chatId));
    }
    // ?new=1 prevents withRememberedChat from resurrecting the last session.
    return '/chat_page.html?new=1';
}

function shellPagesEqual(a, b) {
    try {
        const ua = new URL(a || '/chat_page.html', window.location.origin);
        const ub = new URL(b || '/chat_page.html', window.location.origin);
        const norm = (u) => {
            u.searchParams.delete('_v');
            u.searchParams.delete('_cb');
            const chat = u.searchParams.get('chat') || u.searchParams.get('session') || '';
            const isNew = u.searchParams.has('new');
            return u.pathname + '|c=' + chat + '|n=' + (isNew ? '1' : '0');
        };
        return norm(ua) === norm(ub);
    } catch (_) {
        return String(a || '') === String(b || '');
    }
}

function isShellNewChatPage(page) {
    try {
        const u = new URL(page || '/chat_page.html', window.location.origin);
        if (u.pathname !== '/chat_page.html') return false;
        if (u.searchParams.get('chat') || u.searchParams.get('session')) return false;
        return true;
    } catch (_) {
        return false;
    }
}

function currentShellPage() {
    try {
        const st = typeof getState === 'function' ? getState(0) : null;
        if (st && st.page) return st.page;
    } catch (_) {}
    try {
        const cid = new URL(window.location.href).searchParams.get('chat')
            || new URL(window.location.href).searchParams.get('session');
        if (cid) return shellPageFromChatId(cid);
    } catch (_) {}
    try {
        const live = paneSessionByColumn.get(0);
        if (live) return shellPageFromChatId(live);
    } catch (_) {}
    return '/chat_page.html?new=1';
}

function pushShellBackEntry(fromPage) {
    if (shellApplyingHistory || !fromPage) return;
    if (isShellNewChatPage(fromPage) && shellBackStack.length === 0) return;
    const top = shellBackStack.length ? shellBackStack[shellBackStack.length - 1] : null;
    if (top && shellPagesEqual(top, fromPage)) return;
    shellBackStack.push(fromPage);
    while (shellBackStack.length > SHELL_BACK_STACK_MAX) shellBackStack.shift();
}

function recordShellHistory(page, chatId, mode) {
    if (shellApplyingHistory || mode === 'none') return;
    try {
        const url = new URL(window.location.href);
        if (chatId) {
            url.searchParams.set('chat', String(chatId));
            url.searchParams.delete('session');
        } else {
            url.searchParams.delete('chat');
            url.searchParams.delete('session');
        }
        const next = url.pathname + url.search + url.hash;
        const state = {
            cuttleShell: 1,
            page: page || shellPageFromChatId(chatId),
            chatId: chatId ? String(chatId) : null,
        };
        const cur = window.location.pathname + window.location.search + window.location.hash;
        // Address bar only — back navigation is owned by shellBackStack.
        if (next !== cur || mode === 'replace' || mode === 'push') {
            history.replaceState(state, '', next);
        }
    } catch (_) {}
}

function syncShellChatUrl(chatId, opts) {
    const mode = (opts && opts.mode) || 'replace';
    let page = opts && opts.page;
    if (!page) {
        try {
            const st = typeof getState === 'function' ? getState(0) : null;
            page = (st && st.page) || shellPageFromChatId(chatId);
            if (chatId) {
                const u = new URL(page, window.location.origin);
                if (u.pathname === '/chat_page.html' || u.pathname === '/terminal_page.html') {
                    u.searchParams.set('chat', String(chatId));
                    u.searchParams.delete('session');
                    u.searchParams.delete('new');
                    page = u.pathname + u.search + u.hash;
                }
            }
        } catch (_) {
            page = shellPageFromChatId(chatId);
        }
    }
    recordShellHistory(page, chatId || null, mode);
}

function applyShellHistoryState(page) {
    if (!page) page = '/chat_page.html?new=1';
    shellApplyingHistory = true;
    try {
        if (typeof navigate === 'function') {
            navigate(0, page, { history: 'none' });
        }
    } finally {
        shellApplyingHistory = false;
    }
}

/**
 * Android system back → previous chat/page, then new chat, then stay put.
 * Always returns true so the native shell never minimizes or opens setup.
 */
window.__cuttleHandleBack = function () {
    try {
        if (typeof isNotificationsPopoverOpen === 'function' && isNotificationsPopoverOpen()) {
            closeNotificationsPopover();
            return true;
        }
    } catch (_) {}
    try {
        if (typeof isWorkspacePopoverOpen === 'function' && isWorkspacePopoverOpen()) {
            closeWorkspacePopover();
            return true;
        }
    } catch (_) {}

    if (shellBackStack.length > 0) {
        const prev = shellBackStack.pop();
        applyShellHistoryState(prev);
        return true;
    }

    const page = currentShellPage();
    if (!isShellNewChatPage(page)) {
        rememberChatHandle(0, 'chat', null);
        applyShellHistoryState('/chat_page.html?new=1');
        return true;
    }

    // Already on new-chat root — consume back, stay in app.
    return true;
};

// If boot restored a chat/terminal page from durable layout (no explicit ?chat=
// in address bar), mirror it into the URL so refresh / share still deep-link.
if (!bootChatId && currentPage) {
    try {
        const bootPageUrl = new URL(currentPage, window.location.origin);
        if (
            bootPageUrl.pathname === '/chat_page.html'
            || bootPageUrl.pathname === '/terminal_page.html'
        ) {
            const cid = bootPageUrl.searchParams.get('chat') || bootPageUrl.searchParams.get('session');
            if (cid) syncShellChatUrl(cid);
        }
    } catch (_) {}
}

// ── Video background (from Settings/child pages) ───────────────
window.addEventListener('message', function(e) {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'cuttle-video-reinit') {
        if (window.CuttleVideoBackground) window.CuttleVideoBackground.init();
    } else if (e.data.type === 'cuttle-video-play-now' && e.data.url) {
        if (window.CuttleVideoBackground && window.CuttleVideoBackground.playNow) {
            window.CuttleVideoBackground.playNow(String(e.data.url).trim());
        }
    } else if (e.data.type === 'cuttle-video-update-opacity' && typeof e.data.value === 'number') {
        if (window.CuttleVideoBackground && window.CuttleVideoBackground.updateOverlayOpacity) {
            window.CuttleVideoBackground.updateOverlayOpacity(e.data.value);
        }
    } else if (e.data.type === 'cuttle-bg-effect-blend' && typeof e.data.value === 'number') {
        document.querySelectorAll('.split-column .shell-main iframe').forEach(function (fr) {
            try {
                fr.contentWindow?.postMessage({ type: 'cuttle-bg-effect-blend', value: e.data.value }, '*');
            } catch (_) {}
        });
    } else if (e.data.type === 'cuttle-flask-restart-linked') {
        // One restart card answered → settle siblings in every open chat pane.
        document.querySelectorAll('.split-column .shell-main iframe').forEach(function (fr) {
            try {
                if (fr.contentWindow === e.source) return;
                fr.contentWindow?.postMessage(e.data, '*');
            } catch (_) {}
        });
    } else if (e.data.type === 'cuttle-video-request-state') {
        try {
            const active = document.body.classList.contains('has-video-background');
            e.source?.postMessage({ type: 'cuttle-video-state', active: active }, '*');
            var bh = localStorage.getItem('cuttleBackgroundEffectBlend');
            var bv = bh != null ? parseInt(bh, 10) : 100;
            if (isNaN(bv)) bv = 100;
            e.source?.postMessage({ type: 'cuttle-bg-effect-blend', value: Math.max(0, Math.min(100, bv)) }, '*');
            e.source?.postMessage({ type: 'cuttle-shell-visibility', hidden: !!document.hidden }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-shell-visibility-request') {
        try {
            e.source?.postMessage({ type: 'cuttle-shell-visibility', hidden: !!document.hidden }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-auth-changed') {
        setShellAuthUser(e.data.user || null);
    } else if (e.data.type === 'cuttle-chat-url') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        // Unmatched sources default to column 0. An outgoing iframe still
        // streaming during replaceFrame must not retarget the visible pane.
        if (!live || live.contentWindow !== e.source) return;
        // Keep each column's page (and durable layout) in sync with the open chat.
        try {
            const state = getState(colIdx);
            const cur = state.page || '/chat_page.html';
            const pageUrl = new URL(cur, window.location.origin);
            if (pageUrl.pathname === '/chat_page.html') {
                const next = e.data.chatId
                    ? '/chat_page.html?chat=' + encodeURIComponent(String(e.data.chatId))
                    : '/chat_page.html?new=1';
                // Null means the pane is on a fresh unsaved chat — forget the old
                // handle so returning to Chat doesn't resurrect it.
                rememberChatHandle(colIdx, 'chat', e.data.chatId || null);
                if (colIdx === 0 && !shellApplyingHistory && !shellPagesEqual(cur, next)) {
                    pushShellBackEntry(cur);
                }
                setState(colIdx, next);
                if (colIdx === 0) {
                    sessionStorage.setItem(STORAGE_PAGE, next);
                    syncShellChatUrl(e.data.chatId || null, { mode: 'replace', page: next });
                }
                persistSplitLayout();
            } else if (pageUrl.pathname === '/terminal_page.html') {
                const next = e.data.chatId
                    ? '/terminal_page.html?chat=' + encodeURIComponent(String(e.data.chatId))
                    : '/terminal_page.html';
                rememberChatHandle(colIdx, 'terminal', e.data.chatId || null);
                if (colIdx === 0 && !shellApplyingHistory && !shellPagesEqual(cur, next)) {
                    pushShellBackEntry(cur);
                }
                setState(colIdx, next);
                if (colIdx === 0) {
                    sessionStorage.setItem(STORAGE_PAGE, next);
                    syncShellChatUrl(e.data.chatId || null, { mode: 'replace', page: next });
                }
                persistSplitLayout();
            } else if (colIdx === 0) {
                syncShellChatUrl(e.data.chatId || null, { mode: 'replace' });
            }
        } catch (_) {
            if (colIdx === 0) syncShellChatUrl(e.data.chatId || null);
        }
    } else if (e.data.type === 'cuttle-jobs-tab') {
        // Per-pane Jobs tab (Devices) — must not
        // share one localStorage key across split viewports or refresh collapses them.
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        const tab = String(e.data.tab || '').trim().toLowerCase();
        const valid = { devices: 1 };
        if (!valid[tab]) return;
        try {
            const state = getState(colIdx);
            const cur = state.page || '/jobs_page.html';
            const pageUrl = new URL(cur, window.location.origin);
            if (pageUrl.pathname !== '/jobs_page.html') return;
            if (pageUrl.searchParams.get('tab') === tab) return;
            pageUrl.searchParams.set('tab', tab);
            const next = pageUrl.pathname + pageUrl.search + pageUrl.hash;
            setState(colIdx, next);
            if (colIdx === 0) sessionStorage.setItem(STORAGE_PAGE, next);
            persistSplitLayout();
        } catch (_) {}
    } else if (e.data.type === 'cuttle-chat-session') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        // Chat iframe reports its open session so the shell can batch live-status.
        registerPaneChatSession(e.source, e.data.sessionId);
        if (e.data.preferFocus) setFocusedColumn(colIdx);
    } else if (e.data.type === 'cuttle-pending-project') {
        const colIdx = findColumnIndexForSource(e.source);
        const live = getLiveFrame(colIdx);
        if (!live || live.contentWindow !== e.source) return;
        registerPaneProject(e.source, e.data.path);
    } else if (e.data.type === 'cuttle-pending-changes-refresh') {
        const path = e.data.path || '';
        if (path) refreshPendingChangesPath(path);
        else schedulePendingChangesHub(true);
    } else if (e.data.type === 'cuttle-pending-changes-changed') {
        // Commit/ignore/local refresh in one pane → one fetch (or fan-out payload)
        // for every sibling on the same project path.
        if (e.data.data) {
            broadcastToFrames({
                type: 'cuttle-pending-changes-data',
                path: e.data.path || '',
                data: e.data.data,
            });
        } else if (e.data.path) {
            refreshPendingChangesPath(e.data.path);
        }
    } else if (e.data.type === 'cuttle-open-panes-request') {
        try {
            e.source?.postMessage({ type: 'cuttle-open-panes', panes: getOpenPaneSessions() }, '*');
        } catch (_) {}
    } else if (e.data.type === 'cuttle-pane-activity') {
        setFocusedColumn(findColumnIndexForSource(e.source));
    } else if (e.data.type === 'cuttle-chat-activity') {
        // Snapshot of the chats this frame owns (immediate). Replaces the
        // frame's previous snapshot; the server poll stays authoritative.
        try {
            if (e.source) {
                CuttleSpaces.notePushSnapshot(
                    e.source,
                    Array.isArray(e.data.sessions) ? e.data.sessions : [],
                    Array.isArray(e.data.owned) ? e.data.owned : null
                );
            }
            refreshSpaceActivityLocalState();
            syncSpaceActivityTabs();
        } catch (_) {}
    } else if (
        e.data.type === 'cuttle-terminal-delete'
        || e.data.type === 'cuttle-terminal-rename'
    ) {
        // Fan out history-panel mutations to any live terminal iframes.
        document.querySelectorAll('iframe').forEach((fr) => {
            try {
                const src = fr.getAttribute('src') || '';
                if (src.indexOf('terminal_page') === -1) return;
                fr.contentWindow?.postMessage(e.data, '*');
            } catch (_) {}
        });
    }
});

// ── Navigation map ─────────────────────────────────────────────
const PAGE_TITLES = {
    '/chat_page.html': 'Chat',
    '/router_editor.html': 'Router',
    '/git_graph_page.html': 'Git',
    '/jobs_page.html': 'Jobs',
    '/dashboards_page.html': 'Dashboards',
    '/apps_page.html': 'Apps',
    '/achievements_page.html': 'Achievements',
    '/gizmos_page.html': 'Gizmos',
    '/projects_page.html': 'Projects',
    '/settings_page.html': 'Settings',
    '/query_log.html': 'Query log',
    '/terminal_page.html': 'Terminal',
};

// ── Column state (per-column) ──────────────────────────────────
const columnState = new Map();

function getColumnEl(idx) {
    const want = String(idx);
    // Soft-discarded stubs keep stale data-column values; never return those.
    return getSplitColumns().find((c) => c.dataset.column === want) || null;
}

function getColumnElements(col) {
    const colEl = typeof col === 'number' ? getColumnEl(col) : col;
    if (!colEl) return null;
    const colIdx = parseInt(colEl.dataset.column, 10);
    return {
        column: colEl,
        frame: getLiveFrame(colIdx),
        toggle: colEl.querySelector('.rail-panel-toggle'),
    };
}

function getState(colIdx) {
    let s = columnState.get(colIdx);
    if (!s) {
        s = { page: currentPage, railCollapsed: colIdx === 0 ? railCollapsed : false };
        columnState.set(colIdx, s);
    }
    return s;
}

function setState(colIdx, page) {
    const s = getState(colIdx);
    if (page !== undefined) s.page = page;
}

function applyRailCollapsed(colIdx, collapsed) {
    const column = getColumnEl(colIdx);
    if (!column) return;
    const state = getState(colIdx);
    state.railCollapsed = !!collapsed;
    column.classList.toggle('rail-collapsed', state.railCollapsed);
    const toggle = column.querySelector('.rail-panel-toggle');
    if (toggle) {
        toggle.setAttribute(
            'data-tooltip',
            state.railCollapsed ? 'Show sidebar' : 'Collapse sidebar'
        );
        toggle.setAttribute(
            'aria-label',
            state.railCollapsed ? 'Show sidebar' : 'Collapse sidebar'
        );
    }
    if (colIdx === 0) {
        railCollapsed = state.railCollapsed;
        sessionStorage.setItem(STORAGE_RAIL, state.railCollapsed ? 'true' : 'false');
    }
}

// ── Get the current live frame for a column ───────────────────
// Always looks up from DOM so it works after iframe replacement.
// During a transition, prefer the incoming frame over the outgoing one.
function getLiveFrame(colIdx) {
    const main = getColumnEl(colIdx)?.querySelector('.shell-main');
    if (!main) return null;
    if (colIdx === 0) {
        const byId = document.getElementById('contentFrame');
        if (byId) return byId;
    }
    const frames = Array.from(main.querySelectorAll('iframe'))
        .filter((f) => !f.classList.contains('shell-frame-outgoing'));
    return frames.find((f) => f.classList.contains('shell-frame-pending'))
        || frames[frames.length - 1]
        || null;
}

function isChatPageFrame(frame) {
    if (!frame) return false;
    let href = '';
    try {
        href = frame.contentWindow?.location?.href || '';
    } catch (_) {}
    if (!href) href = frame.getAttribute('src') || '';
    return href.indexOf('chat_page.html') !== -1;
}

function dispatchFocusedChatFind(action) {
    if (!action) return;
    const focused = focusedColumnIdx;
    document.querySelectorAll('.split-column').forEach((col) => {
        const idx = parseInt(col.dataset.column, 10);
        const frame = getLiveFrame(idx);
        if (!frame || !frame.contentWindow) return;
        const isTarget = idx === focused && isChatPageFrame(frame);
        try {
            frame.contentWindow.postMessage({
                type: 'cuttle-chat-find',
                action: isTarget ? action : 'close',
            }, '*');
        } catch (_) {}
    });
}

function wireElectronChatFind(api) {
    if (!api?.isElectron) return;
    if (document.documentElement.dataset.chatFindWired === '1') return;
    document.documentElement.dataset.chatFindWired = '1';
    if (typeof api.chatFind?.onShortcut === 'function') {
        api.chatFind.onShortcut((payload) => {
            dispatchFocusedChatFind(payload && payload.action);
        });
    }
    document.addEventListener('keydown', (e) => {
        const helper = window.CuttleChatFind;
        const action = helper && typeof helper.actionFromDomEvent === 'function'
            ? helper.actionFromDomEvent(e)
            : null;
        if (!action) return;
        e.preventDefault();
        e.stopPropagation();
        dispatchFocusedChatFind(action);
    }, true);
}

// ── Page transitions ──────────────────────────────────────────
// Hold the incoming iframe at opacity 0 until ui_boot posts cuttle-page-ready
// (theme + wallpaper settled, first paint done). That masks FOUC. Optional fade
// is controlled by cuttleUiAnimations / cuttleUiTransitionMs. The previous
// frame stays visible underneath until the new one reveals.
const SPINNER_DELAY_MS = 90;
const TRANSITION_FAILSAFE_MS = 2500;
const DEFAULT_TRANSITION_MS = 220;
const ANIM_KEY = 'cuttleUiAnimations';
const ANIM_MS_KEY = 'cuttleUiTransitionMs';

const pendingFrameReveals = new WeakMap();
const frameRevealTokens = new Map();

function uiAnimationsEnabled() {
    try {
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false;
    } catch (_) {}
    try {
        return localStorage.getItem(ANIM_KEY) !== '0';
    } catch (_) {
        return true;
    }
}

function uiTransitionMs() {
    try {
        const n = parseInt(localStorage.getItem(ANIM_MS_KEY), 10);
        if (isNaN(n)) return DEFAULT_TRANSITION_MS;
        return Math.max(0, Math.min(1000, n));
    } catch (_) {
        return DEFAULT_TRANSITION_MS;
    }
}

function applyUiAnimationPrefs() {
    document.documentElement.style.setProperty('--cuttle-transition-ms', uiTransitionMs() + 'ms');
    document.documentElement.classList.toggle('cuttle-anim', uiAnimationsEnabled());
}

function ensurePageLoader(mainEl) {
    let loader = mainEl.querySelector('.shell-page-loader');
    if (!loader) {
        loader = document.createElement('div');
        loader.className = 'shell-page-loader';
        loader.setAttribute('aria-hidden', 'true');
        loader.innerHTML =
            '<div class="shell-page-loader-veil"></div>' +
            '<div class="shell-page-spinner" aria-hidden="true"></div>';
        mainEl.appendChild(loader);
    }
    return loader;
}

function setPageLoaderVisible(mainEl, visible, opts) {
    const loader = ensurePageLoader(mainEl);
    const spinning = !!(opts && opts.spinning);
    loader.classList.toggle('visible', !!visible);
    loader.classList.toggle('spinning', !!visible && spinning);
    if (visible) {
        loader.setAttribute('aria-busy', 'true');
        loader.removeAttribute('aria-hidden');
    } else {
        loader.removeAttribute('aria-busy');
        loader.setAttribute('aria-hidden', 'true');
    }
}

function cancelPendingReveal(frameEl) {
    const entry = pendingFrameReveals.get(frameEl);
    if (!entry) return;
    if (entry.timer) clearTimeout(entry.timer);
    if (entry.spinnerTimer) clearTimeout(entry.spinnerTimer);
    pendingFrameReveals.delete(frameEl);
}

function revealFrame(frameEl) {
    const entry = pendingFrameReveals.get(frameEl);
    if (!entry) return;
    // Remumber may move data-column while a reveal is pending — resolve live index
    // from the frame's column so we don't cancel and leave a forever spinner.
    const colEl = frameEl.closest?.('.split-column');
    const liveIdx = colEl && !isSplitDiscardedEl(colEl)
        ? parseInt(colEl.dataset.column, 10)
        : NaN;
    const idx = Number.isFinite(liveIdx) ? liveIdx : entry.colIdx;
    if (entry.token !== frameRevealTokens.get(idx)) {
        cancelPendingReveal(frameEl);
        return;
    }
    entry.colIdx = idx;
    cancelPendingReveal(frameEl);

    const { mainEl, oldFrames } = entry;
    const anim = uiAnimationsEnabled();
    const ms = anim ? uiTransitionMs() : 0;

    frameEl.classList.remove('shell-frame-pending');
    frameEl.style.transition = ms > 0 ? `opacity ${ms}ms ease` : 'none';

    requestAnimationFrame(() => {
        frameEl.style.opacity = '1';
        frameEl.style.pointerEvents = '';
    });

    setPageLoaderVisible(mainEl, false);

    (oldFrames || []).forEach((old) => {
        if (!old || old === frameEl || !old.parentNode) return;
        old.classList.add('shell-frame-outgoing');
        old.style.pointerEvents = 'none';
        const removeOld = () => {
            try { old.remove(); } catch (_) {}
        };
        if (ms > 0) {
            old.style.transition = `opacity ${ms}ms ease`;
            requestAnimationFrame(() => { old.style.opacity = '0'; });
            setTimeout(removeOld, ms + 40);
        } else {
            removeOld();
        }
    });
}

/** Reveal the frame whose document just told us it painted. */
function finishFrameTransitionFor(sourceWindow) {
    if (!sourceWindow) return;
    document.querySelectorAll('.shell-main iframe').forEach((frame) => {
        try {
            if (frame.contentWindow === sourceWindow) revealFrame(frame);
        } catch (_) {}
    });
}

function armFrameReveal(colIdx, frameEl, mainEl, oldFrames) {
    applyUiAnimationPrefs();
    const token = (frameRevealTokens.get(colIdx) || 0) + 1;
    frameRevealTokens.set(colIdx, token);

    (oldFrames || []).forEach((f) => cancelPendingReveal(f));

    // Always hide until ready — even with animations off — so theme/wallpaper FOUC never shows.
    frameEl.classList.add('shell-frame-pending');
    frameEl.style.opacity = '0';
    frameEl.style.pointerEvents = 'none';
    frameEl.style.transition = 'none';
    frameEl.style.background = 'transparent';

    // Soft veil immediately; spinner only if the page takes a beat.
    setPageLoaderVisible(mainEl, true, { spinning: false });
    const spinnerTimer = setTimeout(
        () => setPageLoaderVisible(mainEl, true, { spinning: true }),
        SPINNER_DELAY_MS
    );
    // Phones may retry dropped CSS for a few seconds before cuttle-page-ready;
    // don't unveil an unstyled iframe mid-recovery.
    const failsafeMs = isCuttleMobileShell() ? 7000 : TRANSITION_FAILSAFE_MS;
    const timer = setTimeout(() => revealFrame(frameEl), failsafeMs);

    pendingFrameReveals.set(frameEl, {
        colIdx,
        mainEl,
        oldFrames: oldFrames || [],
        token,
        timer,
        spinnerTimer
    });
}

function broadcastToFrames(message, skipWindow) {
    document.querySelectorAll('.shell-main iframe').forEach((frame) => {
        try {
            if (skipWindow && frame.contentWindow === skipWindow) return;
            frame.contentWindow?.postMessage(message, '*');
        } catch (_) {}
    });
}

applyUiAnimationPrefs();

// ── Inject Ctrl+wheel / Ctrl+/- zoom hooks into iframe panes ─
function injectElectronZoomHandler(frameEl) {
    try {
        const doc = frameEl.contentDocument || frameEl.contentWindow?.document;
        if (!doc || doc.documentElement?.dataset?.cuttleZoomBound === '1') return;
        doc.documentElement.dataset.cuttleZoomBound = '1';
        const hasElectronZoom = !!(window.electron?.zoomIn);
        doc.addEventListener('wheel', function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if (hasElectronZoom) {
                e.preventDefault();
                e.stopPropagation();
                window.parent.postMessage({
                    type: 'electron-zoom',
                    deltaY: e.deltaY,
                    deltaMode: e.deltaMode,
                }, '*');
                return;
            }
            window.parent.postMessage({ type: 'browser-zoom-hint' }, '*');
        }, { capture: true, passive: false });
        doc.addEventListener('keydown', function (e) {
            if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
            const key = String(e.key || '');
            let action = null;
            if (key === '+' || key === '=' || key === 'Add') action = 'in';
            else if (key === '-' || key === '_' || key === 'Subtract') action = 'out';
            else if (key === '0') action = 'reset';
            if (!action) return;
            if (hasElectronZoom) {
                e.preventDefault();
                e.stopPropagation();
                window.parent.postMessage({ type: 'electron-zoom-key', action }, '*');
                return;
            }
            window.parent.postMessage({ type: 'browser-zoom-hint' }, '*');
        }, true);
    } catch (_) {}
}

/** Transient upper-right zoom percentage chip. */
let _zoomHudHideTimer = null;
let _zoomWheelAccum = 0;
let _zoomWheelDir = 0; // -1 zoom in (wheel up), +1 zoom out
let _zoomWheelIdleTimer = null;
let _zoomLastStepAt = 0;

/** Chromium-ish: one zoom step per ~100px of wheel delta (pixel mode). */
const ZOOM_WHEEL_THRESHOLD = 100;
/** Ignore opposite-direction jitter while actively zooming one way. */
const ZOOM_DIR_LOCK_MS = 280;
/** Cap how fast steps can fire (trackpads spam wheel events). */
const ZOOM_MIN_STEP_MS = 55;

function approxBrowserZoomPercent() {
    try {
        if (window.visualViewport && Number.isFinite(window.visualViewport.scale) && window.visualViewport.scale > 0) {
            const vv = Math.round(window.visualViewport.scale * 100);
            if (vv !== 100) return vv;
        }
    } catch (_) {}
    try {
        if (window.outerWidth > 0 && window.innerWidth > 0) {
            const ratio = window.outerWidth / window.innerWidth;
            if (ratio > 0.4 && ratio < 4) return Math.round(ratio * 100);
        }
    } catch (_) {}
    return 100;
}

function readZoomPercent() {
    try {
        if (typeof window.electron?.getZoomPercent === 'function') {
            const n = Number(window.electron.getZoomPercent());
            if (Number.isFinite(n) && n > 0) return Math.round(n);
        }
    } catch (_) {}
    return approxBrowserZoomPercent();
}

function showZoomHud(percent) {
    const el = document.getElementById('shellZoomHud');
    if (!el) return;
    const pct = Number.isFinite(Number(percent)) ? Math.round(Number(percent)) : readZoomPercent();
    el.textContent = pct + '%';
    el.hidden = false;
    void el.offsetWidth;
    el.classList.add('is-visible');
    if (_zoomHudHideTimer) clearTimeout(_zoomHudHideTimer);
    _zoomHudHideTimer = setTimeout(() => {
        el.classList.remove('is-visible');
        _zoomHudHideTimer = setTimeout(() => {
            if (!el.classList.contains('is-visible')) el.hidden = true;
            _zoomHudHideTimer = null;
        }, 240);
    }, 1400);
}

function applyZoomAction(action) {
    if (!window.electron) return;
    let pct = null;
    try {
        if (action === 'in' && typeof window.electron.zoomIn === 'function') {
            pct = window.electron.zoomIn();
        } else if (action === 'out' && typeof window.electron.zoomOut === 'function') {
            pct = window.electron.zoomOut();
        } else if (action === 'reset' && typeof window.electron.resetZoom === 'function') {
            pct = window.electron.resetZoom();
        }
    } catch (_) {}
    showZoomHud(pct != null ? pct : readZoomPercent());
}

/**
 * Normalize wheel delta to roughly "CSS pixels" so line/page modes don't
 * overshoot, then accumulate with direction lock before taking a step.
 */
function handleElectronZoomWheel(deltaY, deltaMode) {
    if (!window.electron?.zoomIn) return;
    let dy = Number(deltaY) || 0;
    if (!dy) return;
    // 0 = pixel, 1 = line, 2 = page
    if (deltaMode === 1) dy *= 16;
    else if (deltaMode === 2) dy *= 100;

    const now = Date.now();
    // Wheel up (negative) → zoom in. Positive → zoom out.
    const sign = dy < 0 ? -1 : 1;

    if (_zoomWheelDir && sign !== _zoomWheelDir && (now - _zoomLastStepAt) < ZOOM_DIR_LOCK_MS) {
        // Trackpads often emit a few opposite-sign samples mid-gesture — ignore.
        return;
    }
    if (sign !== _zoomWheelDir) {
        _zoomWheelAccum = 0;
        _zoomWheelDir = sign;
    }

    _zoomWheelAccum += Math.abs(dy);
    if (_zoomWheelIdleTimer) clearTimeout(_zoomWheelIdleTimer);
    _zoomWheelIdleTimer = setTimeout(() => {
        _zoomWheelAccum = 0;
        _zoomWheelDir = 0;
        _zoomWheelIdleTimer = null;
    }, ZOOM_DIR_LOCK_MS);

    if (_zoomWheelAccum < ZOOM_WHEEL_THRESHOLD) return;
    if ((now - _zoomLastStepAt) < ZOOM_MIN_STEP_MS) return;

    _zoomWheelAccum = 0;
    _zoomLastStepAt = now;
    applyZoomAction(sign < 0 ? 'in' : 'out');
}

function setupShellZoomHud() {
    if (document.documentElement.dataset.cuttleZoomHud === '1') return;
    document.documentElement.dataset.cuttleZoomHud = '1';

    document.addEventListener('wheel', (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        if (!window.electron?.zoomIn) {
            requestAnimationFrame(() => showZoomHud(readZoomPercent()));
            return;
        }
        e.preventDefault();
        handleElectronZoomWheel(e.deltaY, e.deltaMode);
    }, { capture: true, passive: false });

    document.addEventListener('keydown', (e) => {
        if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
        const key = String(e.key || '');
        let action = null;
        if (key === '+' || key === '=' || key === 'Add') action = 'in';
        else if (key === '-' || key === '_' || key === 'Subtract') action = 'out';
        else if (key === '0') action = 'reset';
        if (!action) return;
        if (!window.electron?.zoomIn) {
            requestAnimationFrame(() => showZoomHud(readZoomPercent()));
            setTimeout(() => showZoomHud(readZoomPercent()), 50);
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        // Prefer main-process shortcut IPC (blocks Chromium's built-in zoom).
        // Fall back to direct apply if preload hasn't been restarted yet.
        if (typeof window.electron?.onZoomShortcut !== 'function') {
            const now = Date.now();
            if (action !== 'reset' && e.repeat && (now - _zoomLastStepAt) < ZOOM_MIN_STEP_MS) return;
            _zoomLastStepAt = now;
            _zoomWheelAccum = 0;
            applyZoomAction(action);
        }
    }, true);

    if (typeof window.electron?.onZoomShortcut === 'function') {
        window.electron.onZoomShortcut((payload) => {
            const action = payload && payload.action;
            if (!action) return;
            const now = Date.now();
            if (action !== 'reset' && (now - _zoomLastStepAt) < ZOOM_MIN_STEP_MS) return;
            _zoomLastStepAt = now;
            _zoomWheelAccum = 0;
            applyZoomAction(action);
        });
    }
}

setupShellZoomHud();

// ── Attach load-sync listener to a frame element ─────────────
function attachFrameLoadListener(colIdx, frameEl) {
    if (!frameEl || frameEl.dataset.shellLoadBound === '1') return;
    frameEl.dataset.shellLoadBound = '1';
    frameEl.addEventListener('load', function () {
        try {
            const href = frameEl.contentWindow?.location?.href;
            // Ignore about:blank — Chromium may fire load for it before the real page loads.
            // Resetting state here would desync the UI. Only sync when we have a real URL.
            if (!href || href === 'about:blank') return;
            injectElectronZoomHandler(frameEl);
            try {
                if (typeof window.bindRailModKeysForFrame === 'function') {
                    window.bindRailModKeysForFrame(frameEl);
                }
            } catch (_) {}
            try {
                if (window.electron?.isElectron) {
                    frameEl.contentWindow?.postMessage({ type: 'cuttle-electron', enabled: true }, '*');
                }
            } catch (_) {}
            try {
                frameEl.contentWindow?.postMessage({ type: 'cuttle-video-state', active: document.body.classList.contains('has-video-background') }, '*');
            } catch (_) {}
            try {
                var bh = localStorage.getItem('cuttleBackgroundEffectBlend');
                var bv = bh != null ? parseInt(bh, 10) : 100;
                if (isNaN(bv)) bv = 100;
                bv = Math.max(0, Math.min(100, bv));
                frameEl.contentWindow?.postMessage({ type: 'cuttle-bg-effect-blend', value: bv }, '*');
            } catch (_) {}
            const url = new URL(href);
            let path = url.pathname;
            if (path === '/' || path === '/index.html') {
                // Root → redirect to welcome or chat based on preference
                const target = getDefaultPage();
                const newFrame = replaceFrame(colIdx, target);
                attachFrameLoadListener(colIdx, newFrame);
                setState(colIdx, target);
                updateColumnUI(colIdx, target);
                return;
            }
            // Keep ?chat= / ?session= — PAGE_TITLES keys must not strip deep-links
            // (that used to wipe terminal/chat handles from durable split layout).
            // Drop `_cb=` so remount/split never persist cache-bust into layout URLs.
            const matchKey = Object.keys(PAGE_TITLES).find(
                (k) => path === k || path.startsWith(k.endsWith('.html') ? k : k + '/')
            );
            const activePage = canonicalizeShellPage(path + (url.search || ''));
            rememberChatHandleFromPage(colIdx, activePage);
            const state = getState(colIdx);
            retryFailedShellFrame(colIdx, frameEl);
            if (state.page !== activePage) {
                setState(colIdx, activePage);
                updateColumnUI(colIdx, matchKey || '');
                if (colIdx === 0) {
                    try {
                        sessionStorage.setItem(STORAGE_PAGE, activePage);
                        const isChatSurface = path === '/chat_page.html' || path === '/terminal_page.html';
                        const cid = isChatSurface
                            ? (url.searchParams.get('chat') || url.searchParams.get('session'))
                            : null;
                        syncShellChatUrl(cid);
                    } catch (_) {}
                    persistSplitLayout();
                }
            }
        } catch (_err) {
            retryFailedShellFrame(colIdx, frameEl);
            setState(colIdx, null);
            updateColumnUI(colIdx, '');
        }
    });
}

function isCuttleMobileShell() {
    try {
        if (window.isCuttleMobile || window.cuttleMobile?.isNative) return true;
    } catch (_) {}
    return /\bCuttleMobile\//.test(navigator.userAgent || '');
}

// Establish the space identity before mounting the first chat frame.
const spacesState = readSpacesState();
persistSpacesState();

function stampComposerDraftScope(frame, colIdx) {
    if (!frame) return;
    const col = getColumnEl(colIdx);
    ensureLeafId(col);
    frame.dataset.cuttleDraftScope = spacesState.active + ':' + (col?.dataset.leafId || 'main');
}

/** Append `_cb=` so Chromium cannot reuse a stale HTML document for the iframe. */
function withCacheBust(page) {
    if (!page || String(page).startsWith('data:')) return page;
    // Unique `_cb=` URLs stampede Werkzeug HTTPS on phones (net::ERR_TOO_MANY_RETRIES).
    if (isCuttleMobileShell()) return page;
    try {
        const u = new URL(page, window.location.origin);
        u.searchParams.set('_cb', String(Date.now()));
        return u.pathname + u.search + u.hash;
    } catch (_) {
        const s = String(page);
        return s + (s.includes('?') ? '&' : '?') + '_cb=' + Date.now();
    }
}

const frameLoadRetries = new WeakMap();

function frameDocumentLooksFailed(frameEl) {
    try {
        const href = frameEl.contentWindow?.location?.href || '';
        if (/^chrome-error:/i.test(href) || /chromewebdata/i.test(href)) return true;
        const text = String(frameEl.contentDocument?.body?.innerText || '').slice(0, 800);
        return /ERR_TOO_MANY_RETRIES/i.test(text);
    } catch (_) {
        return false;
    }
}

function retryFailedShellFrame(colIdx, frameEl) {
    if (!isCuttleMobileShell() || !frameEl || !frameDocumentLooksFailed(frameEl)) return;
    const n = (frameLoadRetries.get(frameEl) || 0) + 1;
    frameLoadRetries.set(frameEl, n);
    if (n > 3) return;
    const page = (getState(colIdx) && getState(colIdx).page) || currentPage || '/chat_page.html';
    const delay = 400 * n;
    setTimeout(() => {
        try {
            if (!frameEl.parentNode) return;
            frameEl.src = 'about:blank';
            setTimeout(() => {
                try { frameEl.src = page; } catch (_) {}
            }, 50);
        } catch (_) {}
    }, delay);
}

// ── Replace the iframe element for a column ───────────────────
// Returns the new iframe element. Keeps the previous frame until the new page
// signals cuttle-page-ready (or the failsafe), then fades.
function pauseShellFrameStreams(frame) {
    if (!frame) return;
    try {
        frame.contentWindow?.postMessage({ type: 'cuttle-pause-streams' }, '*');
    } catch (_) {}
}

function pauseAllShellPaneStreams() {
    document.querySelectorAll('.shell-main iframe').forEach((frame) => {
        pauseShellFrameStreams(frame);
    });
}

/**
 * Multi-pane Electron freezes if we create a new iframe and destroy the old
 * chat document (EventSource / connection pool). Reuse the existing iframe and
 * only change src — verified via CDP: in-place src swap loads Git; replaceFrame
 * wedges before paint.
 */
function replaceFrameInPlace(colIdx, page) {
    const col = getColumnEl(colIdx);
    if (!col) return null;
    const mainEl = col.querySelector('.shell-main');
    if (!mainEl) return null;

    pauseAllShellPaneStreams();

    let frame = getLiveFrame(colIdx);
    if (!frame) {
        frame = document.createElement('iframe');
        frame.frameBorder = '0';
        frame.allowFullscreen = true;
        frame.allow = 'clipboard-read; clipboard-write';
        frame.setAttribute('aria-label', 'Cuttle Content');
        if (colIdx === 0) {
            frame.id = 'contentFrame';
            frame.name = 'contentFrame';
        }
        mainEl.appendChild(frame);
        attachFrameLoadListener(colIdx, frame);
    }

    // Drop any leftover stacked frames from older replaceFrame crossfades.
    Array.from(mainEl.querySelectorAll('iframe')).forEach((f) => {
        if (f === frame) return;
        try { f.remove(); } catch (_) {}
    });

    armFrameReveal(colIdx, frame, mainEl, []);
    stampComposerDraftScope(frame, colIdx);
    const target = withCacheBust(page);
    // Let pause-streams close EventSources before the navigation request.
    setTimeout(() => {
        try { frame.src = target; } catch (_) {}
    }, 0);
    return frame;
}

function replaceFrame(colIdx, page) {
    // Split view: never tear down / recreate the content iframe.
    if (getSplitColumns().length > 1) {
        return replaceFrameInPlace(colIdx, page);
    }

    const col = getColumnEl(colIdx);
    if (!col) return null;
    const mainEl = col.querySelector('.shell-main');
    if (!mainEl) return null;

    const oldFrames = Array.from(mainEl.querySelectorAll('iframe')).filter(
        (f) => !f.classList.contains('shell-frame-outgoing')
    );
    oldFrames.forEach(pauseShellFrameStreams);

    const newFrame = document.createElement('iframe');
    newFrame.frameBorder = '0';
    newFrame.allowFullscreen = true;
    newFrame.allow = 'clipboard-read; clipboard-write';
    newFrame.setAttribute('aria-label', 'Cuttle Content');
    if (colIdx === 0) {
        oldFrames.forEach((f) => {
            f.removeAttribute('id');
            f.removeAttribute('name');
        });
        newFrame.id = 'contentFrame';
        newFrame.name = 'contentFrame';
    }

    armFrameReveal(colIdx, newFrame, mainEl, oldFrames);
    mainEl.appendChild(newFrame);
    stampComposerDraftScope(newFrame, colIdx);
    newFrame.src = withCacheBust(page);
    return newFrame;
}

// ── Apply initial state (column 0) ──────────────────────────────
const col0 = getColumnEl(0);
/** Sanitized column chrome HTML (no iframes). Captured before col0 loads so
 *  later splits never cloneNode/serialize a live iframe tree (Chromium freeze). */
let _leafTemplateHtml = '';
if (col0) {
    try {
        const bare = col0.cloneNode(true);
        bare.querySelectorAll('iframe').forEach((f) => f.remove());
        bare.querySelectorAll('.shell-page-loader, .shell-loader').forEach((el) => el.remove());
        _leafTemplateHtml = bare.outerHTML;
    } catch (_) {
        _leafTemplateHtml = '';
    }
    applyRailCollapsed(0, railCollapsed);
    setState(0, currentPage);
    // Attach load listener to the existing HTML iframe (don't replace on init)
    const initFrame = getLiveFrame(0);
    if (initFrame) {
        console.log('[App Shell] Initial iframe loading:', currentPage);
        const mainEl = col0.querySelector('.shell-main');
        if (mainEl) armFrameReveal(0, initFrame, mainEl, []);
        stampComposerDraftScope(initFrame, 0);
        initFrame.src = withCacheBust(currentPage);
        attachFrameLoadListener(0, initFrame);
    }
}

function parkShellIframes(root) {
    if (!root) return;
    // Pause first; avoid about:blank (wedges live chat EventSources on Electron).
    Array.from(root.querySelectorAll('iframe')).forEach((frame) => {
        try { cancelPendingReveal(frame); } catch (_) {}
        try { pauseShellFrameStreams(frame); } catch (_) {}
        try {
            // Guard: contentWindow access during load can freeze Chromium.
            if (frame.contentDocument !== null) {
                frame.contentWindow?.postMessage({ type: 'cuttle-pane-teardown' }, '*');
            }
        } catch (_) {}
        try {
            frame.style.display = 'none';
            frame.style.pointerEvents = 'none';
        } catch (_) {}
        const doomed = frame;
        setTimeout(() => {
            try { doomed.remove(); } catch (_) {}
        }, 0);
    });
}

/**
 * Tear down a subtree without probing live iframe contentWindows and without
 * destroying a loaded chat document (sync or deferred remove freezes Chromium).
 * Marks the node + descendant panes/groups discarded (excluded from layout
 * counts) and soft-neutralizes iframes via srcdoc. Nodes stay hidden in the DOM.
 */
function discardSplitSubtree(root) {
    if (!root || root.dataset?.splitDiscarded === '1') return;
    try {
        root.dataset.splitDiscarded = '1';
        root.style.display = 'none';
        root.style.pointerEvents = 'none';
        root.setAttribute('aria-hidden', 'true');
        // Scrub indices so querySelector/getColumnEl never hits stale stubs.
        if (root.classList?.contains('split-column')) {
            root.dataset.column = `discarded-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        }
        root.querySelectorAll('.split-column, .split-group').forEach((el) => {
            el.dataset.splitDiscarded = '1';
            el.style.pointerEvents = 'none';
            if (el.classList.contains('split-column')) {
                el.dataset.column = `discarded-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            }
        });
    } catch (_) {}
    try {
        root.querySelectorAll('iframe').forEach((frame) => {
            try { cancelPendingReveal(frame); } catch (_) {}
            try {
                frame.style.display = 'none';
                frame.style.pointerEvents = 'none';
                frame.setAttribute('aria-hidden', 'true');
            } catch (_) {}
            try {
                // Soft unload — do not frame.remove() / root.remove() (Chrome freeze).
                frame.removeAttribute('src');
                frame.srcdoc = '<!doctype html><title></title>';
            } catch (_) {}
        });
    } catch (_) {}
}

// ── Close a leaf pane (never the last remaining leaf) ───────────
function closeSplitColumn(colIdxOrEl) {
    const leaves = getSplitColumns();
    if (leaves.length <= 1) return;
    const col = (typeof colIdxOrEl === 'number' || typeof colIdxOrEl === 'string')
        ? getColumnEl(parseInt(colIdxOrEl, 10))
        : colIdxOrEl;
    if (!col || !col.classList?.contains('split-column')) return;
    if (isSplitDiscardedEl(col) || col.dataset.paneClosing === '1') return;
    col.dataset.paneClosing = '1';

    const colIdx = parseInt(col.dataset.column, 10);
    const parent = getLeafParentGroup(col);
    // Remove only one adjacent handle (both would orphan the neighbor pair).
    const prevSib = col.previousElementSibling;
    const nextSib = col.nextElementSibling;
    if (prevSib?.classList?.contains('split-resize-handle')) prevSib.remove();
    else if (nextSib?.classList?.contains('split-resize-handle')) nextSib.remove();

    frameRevealTokens.delete(colIdx);
    columnState.delete(colIdx);
    paneSessionByColumn.delete(colIdx);
    paneProjectByColumn.delete(colIdx);
    lastChatByColumn.delete(colIdx);
    if (focusedColumnIdx === colIdx) focusedColumnIdx = 0;

    // Safe teardown — do not probe live iframe contentWindows (Chrome freeze).
    discardSplitSubtree(col);

    collapseSplitGroupIfNeeded(parent);
    const host = (parent && parent.isConnected) ? parent : splitContainer;
    const kids = getGroupChildNodes(host);
    if (kids.length >= 2) {
        applySplitRatioFlex(kids, Array(kids.length).fill(1), getGroupOrientation(host), host);
        rebuildGroupResizeHandles(host);
    } else if (kids.length === 1) {
        kids[0].style.flex = '';
        clearPaneBoxStyles(kids[0]);
    }
    sanitizeSplitDom();
    remumberSplitColumns();
    persistSplitLayout();
}

/** Remove orphan handles / empty nested groups that leave blank gaps. */
function sanitizeSplitDom() {
    if (!splitContainer) return;
    // Orphan handles with no neighboring pane/group on both sides
    splitContainer.querySelectorAll('.split-resize-handle').forEach((h) => {
        const parent = h.parentElement;
        if (!parent) { h.remove(); return; }
        const kids = getGroupChildNodes(parent);
        if (kids.length < 2) h.remove();
    });
    // Empty nested groups — soft-discard only. Sync .remove() of a group that
    // still wraps iframe stubs freezes Chromium/Electron.
    Array.from(splitContainer.querySelectorAll('.split-group')).forEach((g) => {
        if (g.id === 'splitContainer') return;
        if (g.dataset.splitDiscarded === '1') return;
        if (getGroupChildNodes(g).length === 0) {
            discardSplitSubtree(g);
        }
    });
    // Single child at root → full-bleed; keep a nested group intact (do not
    // unwrap / flatten complex layouts into a top-level row).
    const rootKids = getGroupChildNodes(splitContainer);
    if (rootKids.length <= 1) {
        splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
        if (rootKids[0]) {
            rootKids[0].style.flex = '';
            clearPaneBoxStyles(rootKids[0]);
        }
        if (rootKids[0]?.classList.contains('split-column')) {
            applyGroupOrientation(splitContainer, 'horizontal');
        }
    }
    // Deferred physical cleanup of soft-discarded stubs (never sync-remove on close).
    schedulePurgeDiscardedSplitNodes();
}

/** If a group has 0/1 children, unwrap it into its parent (except empty root).
 * Never reparent a live iframe column — recreate the leaf instead.
 */
function collapseSplitGroupIfNeeded(groupEl) {
    if (!groupEl || !isSplitGroupEl(groupEl) || !groupEl.isConnected) return;
    const kids = getGroupChildNodes(groupEl);
    if (kids.length > 1) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    if (groupEl.id === 'splitContainer') {
        groupEl.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
        if (kids.length === 1) {
            // Sole remaining root child (leaf or nested group) just fills the shell.
            // Never unwrap a nested group here — that flattened complex layouts into
            // a single horizontal row when the top of a vertical split was closed.
            kids[0].style.flex = '';
            clearPaneBoxStyles(kids[0]);
            if (kids[0].classList.contains('split-column')) {
                applyGroupOrientation(groupEl, 'horizontal');
            }
        }
        return;
    }
    // Nested group with ≤1 child → promote into parent (safe recreate)
    const grand = groupEl.parentElement;
    if (!grand) return;
    const flex = groupEl.style.flex || '1 1 0%';
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) ch.remove();
    });
    if (kids.length === 1) {
        const only = kids[0];
        if (only.classList.contains('split-column')) {
            const idx = parseInt(only.dataset.column, 10);
            const st = columnState.get(idx);
            const page = pageWithPaneSession(idx, (st && st.page) || '/chat_page.html');
            const built = createSplitLeafColumn(page, only.dataset.leafId);
            if (built) {
                built.clone.style.flex = flex;
                grand.insertBefore(built.clone, groupEl);
                discardSplitSubtree(groupEl);
                const provisionalIdx = 9990;
                built.clone.dataset.column = String(provisionalIdx);
                setState(provisionalIdx, page);
                mountNewLeafFrame(provisionalIdx, built.frame, built.mainEl, page);
                setupColumnListeners(provisionalIdx, built.clone);
                setupLayoutDragDrop(built.clone);
                applyLayoutToColumn(built.clone, lastUILayout);
            } else {
                discardSplitSubtree(groupEl);
            }
        } else if (only.classList.contains('split-group')) {
            // Unwrap one nesting level only — do not flatten all descendant leaves.
            promoteGroupChildrenToParent(only, grand, groupEl);
        }
    } else {
        discardSplitSubtree(groupEl);
    }
    if (isSplitGroupEl(grand) && grand.isConnected) collapseSplitGroupIfNeeded(grand);
}

/**
 * Unwrap `groupEl` into `parentEl` (optionally replacing `replaceEl`), preserving
 * nested subgroup structure. Soft-discards live iframes and remounts from snapshot.
 */
function promoteGroupChildrenToParent(groupEl, parentEl, replaceEl) {
    if (!groupEl || !parentEl) return;
    const tree = snapshotLayoutTree(groupEl);
    const flex = groupEl.style.flex || '';
    const groupOri = getGroupOrientation(groupEl);
    const anchor = replaceEl || groupEl;
    const marker = document.createComment('split-promote');
    try {
        parentEl.insertBefore(marker, anchor);
    } catch (_) {}

    discardSplitSubtree(groupEl);
    if (replaceEl && replaceEl !== groupEl && replaceEl.isConnected) {
        discardSplitSubtree(replaceEl);
    }

    if (!tree) {
        try { marker.remove(); } catch (_) {}
        return;
    }

    const treeOri = tree.type === 'group' ? (tree.orientation || groupOri) : groupOri;
    const parentOri = getGroupOrientation(parentEl);
    const rootEmpty = parentEl.id === 'splitContainer'
        && getGroupChildNodes(parentEl).length === 0;

    if (rootEmpty) {
        // Sole remaining tree under root: keep group nesting intact. Hoisting
        // children into #splitContainer flattened complex layouts into one row
        // when the top of a vertical split was closed.
        if (tree.type === 'group' && Array.isArray(tree.children) && tree.children.length) {
            const nested = tree.children.length > 1
                || tree.children.some((ch) => ch && ch.type === 'group');
            if (nested) {
                applyGroupOrientation(parentEl, treeOri || 'horizontal');
                mountSnapshotAsNestedGroup(tree, parentEl, treeOri);
            } else {
                applyGroupOrientation(parentEl, 'horizontal');
                mountLayoutTree(tree.children[0], parentEl, { allowReuseColumn0: true });
            }
        } else {
            applyGroupOrientation(parentEl, 'horizontal');
            mountLayoutTree(tree, parentEl, { allowReuseColumn0: true });
        }
        try { marker.remove(); } catch (_) {}
        rebuildGroupResizeHandles(parentEl);
        return;
    }

    if (tree.type === 'group' && Array.isArray(tree.children) && tree.children.length >= 1) {
        if (parentOri === treeOri) {
            tree.children.forEach((ch) => {
                mountLayoutTree(ch, parentEl, { allowReuseColumn0: true, before: marker });
            });
        } else {
            const g = createSplitGroupEl(treeOri);
            if (flex) g.style.flex = flex;
            if (tree.id) g.dataset.groupId = tree.id;
            parentEl.insertBefore(g, marker);
            tree.children.forEach((ch) => {
                mountLayoutTree(ch, g, { allowReuseColumn0: true });
            });
            rebuildGroupResizeHandles(g);
        }
    } else {
        mountLayoutTree(tree, parentEl, { allowReuseColumn0: true, before: marker });
    }
    try { marker.remove(); } catch (_) {}
    rebuildGroupResizeHandles(parentEl);
}

function clearPaneBoxStyles(el) {
    if (!el) return;
    el.style.width = '';
    el.style.height = '';
    el.style.minWidth = '';
    el.style.maxWidth = '';
    el.style.minHeight = '';
    el.style.maxHeight = '';
}

// Logo SVG snippets must be initialized before restoreSplitLayout() / createSplitLeafColumn
// (those run mid-script and used to hit TDZ on these consts → blank panes after refresh).
const RAIL_PANE_GRIP_HTML =
    '<svg viewBox="0 0 16 10" width="16" height="10" aria-hidden="true">'
    + '<circle cx="4" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="8" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="12" cy="2" r="1.2" fill="currentColor"/>'
    + '<circle cx="4" cy="8" r="1.2" fill="currentColor"/>'
    + '<circle cx="8" cy="8" r="1.2" fill="currentColor"/>'
    + '<circle cx="12" cy="8" r="1.2" fill="currentColor"/>'
    + '</svg>';

const RAIL_SVG_PLUS =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    + '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

const RAIL_SVG_CLOSE =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
    + '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

const RAIL_SVG_SPLIT_V =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="4" y="3" width="16" height="7" rx="1.5"/><rect x="4" y="14" width="16" height="7" rx="1.5"/></svg>';

const RAIL_SVG_SPLIT_H =
    '<svg class="rail-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="3" y="4" width="7" height="16" rx="1.5"/><rect x="14" y="4" width="7" height="16" rx="1.5"/></svg>';

const RAIL_LOGO_MOD_LAYERS =
    '<span class="rail-logo-mod rail-logo-split-v" aria-hidden="true">' + RAIL_SVG_SPLIT_V + '</span>'
    + '<span class="rail-logo-mod rail-logo-split-h" aria-hidden="true">' + RAIL_SVG_SPLIT_H + '</span>';

function primaryRailLogoInnerHtml() {
    return '<img src="/img/cuttle-mascot.png" alt="Cuttle" class="rail-logo-img"'
        + ' onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
        + '<span class="rail-logo-fallback" style="display:none">C</span>'
        + '<span class="rail-logo-plus rail-logo-face">' + RAIL_SVG_PLUS + '</span>'
        + RAIL_LOGO_MOD_LAYERS;
}

function closeRailLogoInnerHtml() {
    return '<span class="rail-logo-close-face rail-logo-face">' + RAIL_SVG_CLOSE + '</span>'
        + RAIL_LOGO_MOD_LAYERS;
}

/**
 * Build a fresh leaf column from the boot-time chrome template (no live iframes).
 * Does not insert into the DOM.
 */
function createSplitLeafColumn(page, leafId) {
    if (!_leafTemplateHtml) {
        // Last-resort: build a minimal shell if boot capture failed.
        const holder = document.createElement('div');
        holder.innerHTML = '<div class="split-column" data-column="0">'
            + '<nav class="icon-rail"><div class="rail-logo rail-logo-close rail-logo-clickable" data-action="close"></div>'
            + '<div class="rail-items"></div><div class="rail-footer"></div></nav>'
            + '<main class="shell-main"></main></div>';
        _leafTemplateHtml = holder.innerHTML;
    }
    const holder = document.createElement('div');
    holder.innerHTML = _leafTemplateHtml;
    const clone = holder.firstElementChild;
    if (!clone) return null;

    clone.dataset.leafId = leafId || newLeafId();
    // Unique provisional index so remumber never maps two fresh clones that both
    // still say data-column="0" onto the same lastChatByColumn entry.
    clone.dataset.column = String(900000 + Math.floor(Math.random() * 100000));
    clone.classList.remove('rail-collapsed');
    clone.style.flex = '';
    clearPaneBoxStyles(clone);
    clone.removeAttribute('data-pane-closing');
    clone.removeAttribute('data-layout-mounted');

    const logoEl = clone.querySelector('.rail-logo-primary, .rail-logo-add, .rail-logo');
    if (logoEl) {
        logoEl.className = 'rail-logo rail-logo-close rail-logo-clickable';
        logoEl.dataset.action = 'close';
        logoEl.removeAttribute('title');
        logoEl.removeAttribute('id');
        logoEl.dataset.tooltip = defaultRailLogoTooltip('close');
        logoEl.innerHTML = closeRailLogoInnerHtml();
    }

    clone.querySelectorAll('[id]').forEach((el) => { el.id = ''; });
    clone.querySelector('.icon-rail')?.removeAttribute('data-rail-customize');
    clone.querySelector('.icon-rail')?.removeAttribute('data-pane-reorder');
    ensureRailPaneGrip(clone);
    clone.querySelectorAll('.shell-page-loader, .shell-loader').forEach((el) => el.remove());
    clone.querySelectorAll('iframe').forEach((el) => el.remove());

    const mainEl = clone.querySelector('.shell-main');
    let frame = null;
    if (mainEl) {
        // Ensure main is empty before attaching a fresh frame.
        while (mainEl.firstChild) mainEl.removeChild(mainEl.firstChild);
        frame = document.createElement('iframe');
        frame.frameBorder = '0';
        frame.allowFullscreen = true;
        frame.allow = 'clipboard-read; clipboard-write';
        frame.setAttribute('aria-label', 'Cuttle Content');
        mainEl.appendChild(frame);
    }
    ensurePaneDropOverlay(clone);
    return { clone, frame, mainEl, page: page || '/chat_page.html' };
}

function mountNewLeafFrame(newIdx, frame, mainEl, page) {
    if (!frame || !mainEl) return;
    const safePage = canonicalizeShellPage(page || '/chat_page.html');
    setState(newIdx, safePage);
    updateColumnUI(newIdx, safePage);
    frame.classList.remove('shell-frame-pending', 'frame-entering');
    armFrameReveal(newIdx, frame, mainEl, []);
    stampComposerDraftScope(frame, newIdx);
    frame.src = withCacheBust(safePage);
    attachFrameLoadListener(newIdx, frame);
}

/**
 * Mount a snapshot tree as a nested group under parent (never absorb into root).
 */
function mountSnapshotAsNestedGroup(snapshot, parentEl, orientationFallback) {
    if (!snapshot || !parentEl) return null;
    const ori = snapshot.type === 'group'
        ? (snapshot.orientation || orientationFallback || 'horizontal')
        : (orientationFallback || 'horizontal');
    const wrap = createSplitGroupEl(ori);
    wrap.style.flex = snapshot.flex || '1 1 0%';
    if (snapshot.type === 'group' && snapshot.id) wrap.dataset.groupId = snapshot.id;
    parentEl.appendChild(wrap);
    if (snapshot.type === 'group' && Array.isArray(snapshot.children)) {
        snapshot.children.forEach((ch) => {
            mountLayoutTree(ch, wrap, { allowReuseColumn0: true });
        });
    } else {
        mountLayoutTree(snapshot, wrap, { allowReuseColumn0: true });
    }
    rebuildGroupResizeHandles(wrap);
    return wrap;
}

/**
 * Discard every active root child (soft) — used when wrapping the whole layout.
 */
function discardAllRootSplitChildren() {
    if (!splitContainer) return;
    Array.from(splitContainer.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) {
            try { ch.remove(); } catch (_) {}
            return;
        }
        if (ch.classList?.contains('split-column') || ch.classList?.contains('split-group')) {
            discardSplitSubtree(ch);
        }
    });
}

/**
 * Primary-blade `+`: append a fresh chat at the far right of the shell.
 * Horizontal roots append in place (no remount) and equalize widths.
 * Vertical multi-pane roots wrap the stack on the left, then size by leaf count
 * so the new cell is 1/N of the shell — not half.
 */
function appendRootHorizontalPane(initialPage) {
    if (!splitContainer) return;
    const page = bareShellPage(initialPage || '/chat_page.html');
    const kids = getGroupChildNodes(splitContainer);
    const ori = getGroupOrientation(splitContainer);

    const finishNewLeaf = (built) => {
        if (!built) return;
        splitContainer.appendChild(built.clone);
        remumberSplitColumns();
        const newIdx = parseInt(built.clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, built.frame, built.mainEl, page);
        setupColumnListeners(newIdx, built.clone);
        setupLayoutDragDrop(built.clone);
        applyLayoutToColumn(built.clone, lastUILayout);
    };

    // Vertical multi-pane root → [existing stack | new] without destroying nesting.
    if (ori === 'vertical' && kids.length >= 2) {
        const leafCountBefore = Math.max(1, getSplitColumns().length);
        const snapshot = snapshotLayoutTree(splitContainer);
        discardAllRootSplitChildren();
        applyGroupOrientation(splitContainer, 'horizontal');
        if (snapshot) mountSnapshotAsNestedGroup(snapshot, splitContainer, 'vertical');
        finishNewLeaf(createSplitLeafColumn(page));
        const rootKids = getGroupChildNodes(splitContainer);
        if (!_restoringLayout && rootKids.length >= 2) {
            applySplitRatioFlex(
                rootKids,
                [leafCountBefore, 1],
                'horizontal',
                splitContainer
            );
        } else {
            rebuildGroupResizeHandles(splitContainer);
        }
        persistSplitLayout();
        return;
    }

    // Single pane / already horizontal: append at end — do not remount siblings.
    if (kids.length <= 1) applyGroupOrientation(splitContainer, 'horizontal');
    finishNewLeaf(createSplitLeafColumn(page));
    if (!_restoringLayout) equalizeGroupChildren(splitContainer);
    else rebuildGroupResizeHandles(splitContainer);
    persistSplitLayout();
}

/**
 * Alt+Shift+click `+`: absolute vertical split — new cell at the very bottom,
 * existing layout pushed up as the top pane/group.
 */
function appendRootVerticalPane(initialPage) {
    if (!splitContainer) return;
    const page = bareShellPage(initialPage || '/chat_page.html');
    const kids = getGroupChildNodes(splitContainer);
    const ori = getGroupOrientation(splitContainer);

    const finishNewLeaf = (built) => {
        if (!built) return null;
        splitContainer.appendChild(built.clone);
        remumberSplitColumns();
        const newIdx = parseInt(built.clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, built.frame, built.mainEl, page);
        setupColumnListeners(newIdx, built.clone);
        setupLayoutDragDrop(built.clone);
        applyLayoutToColumn(built.clone, lastUILayout);
        return newIdx;
    };

    if (kids.length <= 1) {
        applyGroupOrientation(splitContainer, 'vertical');
        finishNewLeaf(createSplitLeafColumn(page));
        if (!_restoringLayout) equalizeGroupChildren(splitContainer);
        else rebuildGroupResizeHandles(splitContainer);
        persistSplitLayout();
        return;
    }

    if (ori === 'vertical') {
        finishNewLeaf(createSplitLeafColumn(page));
        if (!_restoringLayout) equalizeGroupChildren(splitContainer);
        else rebuildGroupResizeHandles(splitContainer);
        persistSplitLayout();
        return;
    }

    // Horizontal (or mixed) multi-pane root → wrap everything on top, new cell below.
    const leafCountBefore = Math.max(1, getSplitColumns().length);
    const snapshot = snapshotLayoutTree(splitContainer);
    discardAllRootSplitChildren();
    applyGroupOrientation(splitContainer, 'vertical');
    if (snapshot) mountSnapshotAsNestedGroup(snapshot, splitContainer, 'horizontal');
    finishNewLeaf(createSplitLeafColumn(page));
    const rootKids = getGroupChildNodes(splitContainer);
    if (!_restoringLayout && rootKids.length >= 2) {
        applySplitRatioFlex(
            rootKids,
            [leafCountBefore, 1],
            'vertical',
            splitContainer
        );
    } else {
        rebuildGroupResizeHandles(splitContainer);
    }
    persistSplitLayout();
}

/**
 * Split one viewport/cell only.
 * - Same-orientation parent → append sibling after this leaf
 * - Else create a nested group and replace this leaf with [kept copy + new]
 *   (never reparent a live iframe column — that freezes Chrome/Electron)
 */
function splitLeafColumn(targetCol, orientation, initialPage) {
    if (!targetCol || !splitContainer) return;
    const want = normalizeSplitOrientation(orientation);
    const defaultPage = bareShellPage(initialPage || '/chat_page.html');

    let parent = getLeafParentGroup(targetCol);
    if (parent === splitContainer && getGroupChildNodes(parent).length === 1) {
        applyGroupOrientation(parent, want);
    }

    const sameAxis = getGroupOrientation(parent) === want
        || (parent === splitContainer && getGroupChildNodes(parent).length === 1);

    if (sameAxis) {
        applyGroupOrientation(parent, want);
        const built = createSplitLeafColumn(defaultPage);
        if (!built) return;
        const { clone, frame, mainEl, page } = built;
        if (targetCol.nextSibling) parent.insertBefore(clone, targetCol.nextSibling);
        else parent.appendChild(clone);

        remumberSplitColumns();
        const newIdx = parseInt(clone.dataset.column, 10);
        lastChatByColumn.delete(newIdx);
        persistLastChatHandles();
        mountNewLeafFrame(newIdx, frame, mainEl, page);
        setupColumnListeners(newIdx, clone);
        setupLayoutDragDrop(clone);
        applyLayoutToColumn(clone, lastUILayout);
        if (!_restoringLayout) equalizeGroupChildren(parent);
        else rebuildGroupResizeHandles(parent);
        persistSplitLayout();
        return;
    }

    // Different orientation → replace target with a nested group of two NEW leaves.
    // Copy the target's page into the first leaf; never move the live iframe node.
    const targetIdx = parseInt(targetCol.dataset.column, 10);
    const targetState = columnState.get(targetIdx);
    const keepPage = pageWithPaneSession(
        targetIdx,
        (targetState && targetState.page) || '/chat_page.html'
    );
    const oldFlex = targetCol.style.flex || '';
    const keepLeafId = targetCol.dataset.leafId || newLeafId();
    const keepHandles = lastChatByColumn.get(targetIdx)
        ? { ...lastChatByColumn.get(targetIdx) }
        : null;

    const builtA = createSplitLeafColumn(keepPage, keepLeafId);
    const builtB = createSplitLeafColumn(defaultPage);
    if (!builtA || !builtB) return;

    const group = createSplitGroupEl(want);
    if (oldFlex) group.style.flex = oldFlex;
    else group.style.flex = '1 1 0%';

    parent.insertBefore(group, targetCol);
    discardSplitSubtree(targetCol);

    columnState.delete(targetIdx);
    paneSessionByColumn.delete(targetIdx);
    paneProjectByColumn.delete(targetIdx);
    frameRevealTokens.delete(targetIdx);
    lastChatByColumn.delete(targetIdx);

    builtA.clone.style.flex = '1 1 0%';
    builtB.clone.style.flex = '1 1 0%';
    group.appendChild(builtA.clone);
    group.appendChild(builtB.clone);

    remumberSplitColumns();
    const idxA = parseInt(builtA.clone.dataset.column, 10);
    const idxB = parseInt(builtB.clone.dataset.column, 10);
    if (keepHandles) {
        lastChatByColumn.set(idxA, keepHandles);
        persistLastChatHandles();
    }
    rememberChatHandleFromPage(idxA, keepPage);
    lastChatByColumn.delete(idxB);
    persistLastChatHandles();

    mountNewLeafFrame(idxA, builtA.frame, builtA.mainEl, keepPage);
    mountNewLeafFrame(idxB, builtB.frame, builtB.mainEl, builtB.page);
    setupColumnListeners(idxA, builtA.clone);
    setupColumnListeners(idxB, builtB.clone);
    setupLayoutDragDrop(builtA.clone);
    setupLayoutDragDrop(builtB.clone);
    applyLayoutToColumn(builtA.clone, lastUILayout);
    applyLayoutToColumn(builtB.clone, lastUILayout);

    if (!_restoringLayout) {
        applySplitRatioFlex(getGroupChildNodes(group), [1, 1], want, group);
    }
    rebuildGroupResizeHandles(group);
    rebuildGroupResizeHandles(parent);
    persistSplitLayout();
}

/** Back-compat name used by restore / workspace load (adds root-level siblings). */
function addSplitColumn(initialPage, opts) {
    const want = normalizeSplitOrientation(
        opts && Object.prototype.hasOwnProperty.call(opts, 'orientation')
            ? opts.orientation
            : 'horizontal'
    );
    if (!_restoringLayout && want === 'horizontal') {
        appendRootHorizontalPane(initialPage);
        return;
    }
    const leaves = getSplitColumns();
    const target = leaves[leaves.length - 1] || getColumnEl(0);
    if (!target) return;
    // Restore path: prefer appending into root when building a flat row/stack.
    if (_restoringLayout && splitContainer) {
        applyGroupOrientation(splitContainer, want);
        const built = createSplitLeafColumn(canonicalizeShellPage(initialPage || '/chat_page.html'));
        if (!built) return;
        const { clone, frame, mainEl, page } = built;
        const kids = getGroupChildNodes(splitContainer);
        if (kids.length >= 1) {
            const handle = document.createElement('div');
            handle.className = 'split-resize-handle';
            splitContainer.appendChild(handle);
        }
        splitContainer.appendChild(clone);
        remumberSplitColumns();
        const newIdx = parseInt(clone.dataset.column, 10);
        if (initialPage) rememberChatHandleFromPage(newIdx, initialPage);
        mountNewLeafFrame(newIdx, frame, mainEl, page);
        setupColumnListeners(newIdx, clone);
        setupLayoutDragDrop(clone);
        applyLayoutToColumn(clone, lastUILayout);
        return;
    }
    splitLeafColumn(target, want, initialPage);
}

// ── Split sizing (per-group; ratios scale with window) ───────────
const SPLIT_MIN_PX = 72;           // horizontal (width) floor
const SPLIT_MIN_PX_VERTICAL = 36;  // vertical (height) floor — keep short stacks scrollable
/** Must match `.split-resize-handle` thickness in app_shell.css */
const SPLIT_HANDLE_PX = 5;

/** Parse legacy drag-resize flex `0 0 Npx` → N, else null. */
function parseSplitFlexPx(flex) {
    const m = String(flex || '').trim().match(/^0\s+0\s+(\d+(?:\.\d+)?)px$/i);
    return m ? parseFloat(m[1]) : null;
}

/** Main-axis size of a pane/group child. */
function measureSplitMain(el, orientation) {
    if (!el) return 0;
    const vertical = normalizeSplitOrientation(orientation) === 'vertical'
        || (!orientation && getGroupOrientation(getLeafParentGroup(el)) === 'vertical');
    return vertical ? (el.offsetHeight || 0) : (el.offsetWidth || 0);
}

/** Pixel budget for siblings inside a group. */
function getSplitGroupBudget(groupEl, childCount) {
    const n = Math.max(1, childCount | 0);
    const handles = Math.max(0, n - 1);
    const ori = getGroupOrientation(groupEl);
    const main = ori === 'vertical'
        ? (groupEl?.clientHeight || 0)
        : (groupEl?.clientWidth || 0);
    const raw = main - handles * SPLIT_HANDLE_PX;
    return Math.max(n, raw);
}

/**
 * Preferred per-pane floor, lowered when n×min would exceed the window so
 * panes (and their rails/toolbars) can never spill past the viewport.
 */
function splitMinPxFor(colCount, budget, orientation) {
    const n = Math.max(1, colCount | 0);
    const fit = Math.floor((budget || 0) / n);
    const floor = normalizeSplitOrientation(orientation) === 'vertical'
        ? SPLIT_MIN_PX_VERTICAL
        : SPLIT_MIN_PX;
    return Math.max(1, Math.min(floor, fit));
}

/**
 * Force widths to fill exactly `budget` with each pane ≥ min. Prevents the
 * divider from growing a pane so far that siblings are pushed off-screen.
 */
function clampSplitWidths(widths, budget, orientation) {
    const n = widths.length;
    if (n === 0) return [];
    const avail = Math.max(n, Math.round(budget || 0));
    const minPx = splitMinPxFor(n, avail, orientation);
    let next = widths.map((w) => Math.max(minPx, Math.round(Number(w) || 0)));
    let sum = next.reduce((a, b) => a + b, 0);
    if (sum > avail) {
        let overflow = sum - avail;
        // Shrink widest panes first (never below min).
        while (overflow > 0) {
            let progressed = false;
            const order = next.map((w, i) => i).sort((a, b) => next[b] - next[a]);
            for (const i of order) {
                if (overflow <= 0) break;
                const can = next[i] - minPx;
                if (can <= 0) continue;
                const take = Math.min(can, overflow);
                next[i] -= take;
                overflow -= take;
                progressed = true;
            }
            if (!progressed) break;
        }
        sum = next.reduce((a, b) => a + b, 0);
    }
    if (sum < avail) {
        // Give leftover to the last pane so columns always fill the window.
        next[n - 1] += avail - sum;
    }
    return next;
}

/**
 * Apply proportional flex-grow so siblings keep relative sizes when the
 * window is resized. `widths` are relative weights (e.g. [1,1] or measured px);
 * they are normalized to the group budget before clamping.
 */
function applySplitRatioFlex(cols, widths, orientation, groupEl) {
    if (!cols || cols.length === 0) return;
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = orientation || getGroupOrientation(group);
    const budget = getSplitGroupBudget(group, cols.length);
    const weights = cols.map((_, i) => Math.max(0, Number(widths?.[i]) || 0));
    const weightSum = weights.reduce((a, b) => a + b, 0);
    const normalized = weightSum > 0
        ? weights.map((w) => (w / weightSum) * budget)
        : cols.map(() => budget / cols.length);
    const safe = clampSplitWidths(normalized, budget, ori);
    const total = safe.reduce((a, b) => a + b, 0) || 1;
    cols.forEach((c, i) => {
        const ratio = safe[i] / total;
        c.style.flex = `${ratio.toFixed(6)} 1 0%`;
        clearPaneBoxStyles(c);
    });
}

/**
 * After splitting inside a group: new sibling ≈ average of prior siblings.
 */
function fitNewSiblingsInGroup(groupEl, priorSizes) {
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) {
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    const ori = getGroupOrientation(groupEl);
    const budget = getSplitGroupBudget(groupEl, cols.length);
    let prior = Array.isArray(priorSizes) ? priorSizes.map((w) => Math.max(0, Number(w) || 0)) : [];
    if (prior.length !== cols.length - 1) {
        prior = cols.slice(0, -1).map((c) => measureSplitMain(c, ori));
    }
    const sumPrior = prior.reduce((a, b) => a + b, 0);
    if (sumPrior <= 0) {
        applySplitRatioFlex(cols, Array(cols.length).fill(1), ori, groupEl);
        rebuildGroupResizeHandles(groupEl);
        return;
    }
    const avg = sumPrior / prior.length;
    const raw = prior.concat([avg]);
    const scaled = raw.map((w) => (w / (sumPrior + avg)) * budget);
    applySplitRatioFlex(cols, scaled, ori, groupEl);
    rebuildGroupResizeHandles(groupEl);
}

function applySplitPixelFlex(cols, widths, orientation, groupEl) {
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = orientation || getGroupOrientation(group);
    const budget = getSplitGroupBudget(group, cols.length);
    const clamped = clampSplitWidths(widths, budget, ori);
    cols.forEach((c, i) => {
        c.style.flex = `0 0 ${clamped[i]}px`;
    });
    return clamped;
}

/** If any sibling still uses fixed-px flex, convert all to ratios. */
function migrateFixedPxSplitFlex(cols, groupEl) {
    if (!cols || cols.length < 2) return false;
    const group = groupEl || (cols[0] && getLeafParentGroup(cols[0])) || splitContainer;
    const ori = getGroupOrientation(group);
    let anyFixed = false;
    const widths = cols.map((c) => {
        const px = parseSplitFlexPx(c.style.flex);
        if (px != null) {
            anyFixed = true;
            return px;
        }
        return measureSplitMain(c, ori);
    });
    if (!anyFixed) return false;
    applySplitRatioFlex(cols, widths, ori, group);
    return true;
}

/** After a drag, lock in ratio flex from current main-axis sizes (one group). */
function finalizeGroupToRatios(groupEl) {
    const cols = getGroupChildNodes(groupEl);
    if (cols.length < 2) return;
    const ori = getGroupOrientation(groupEl);
    const widths = cols.map((c) => {
        const px = parseSplitFlexPx(c.style.flex);
        return px != null ? px : measureSplitMain(c, ori);
    });
    applySplitRatioFlex(cols, widths, ori, groupEl);
}

function finalizeSplitColumnsToRatios() {
    if (!splitContainer) return;
    const groups = [splitContainer, ...splitContainer.querySelectorAll('.split-group')];
    groups.forEach((g) => finalizeGroupToRatios(g));
}

function ensureLeafId(colEl) {
    if (colEl && !colEl.dataset.leafId) colEl.dataset.leafId = newLeafId();
}

/**
 * Recursively mount a saved layout tree under `parentEl`.
 * First leaf reuses existing column 0 when present.
 *
 * `absorbIntoRoot: true` — mount a top-level group directly into #splitContainer
 * even when column 0 is already a child. Without this, restore wraps the whole
 * layout in an extra .split-group beside col0 and applyFlexWalk / sizing leave
 * blank flex regions with no blade.
 */
function mountLayoutTree(node, parentEl, opts) {
    const options = opts || {};
    if (!node || !parentEl) return;
    const before = (options.before && options.before.parentNode === parentEl)
        ? options.before
        : null;
    const insertNode = (el) => {
        if (before) parentEl.insertBefore(el, before);
        else parentEl.appendChild(el);
    };

    if (node.type === 'leaf' || (!node.type && node.page)) {
        let col = null;
        const col0 = getColumnEl(0);
        const canReuse = options.allowReuseColumn0 !== false && col0
            && !col0.dataset.layoutMounted
            && getSplitColumns().length <= 1
            && !isSplitDiscardedEl(col0);
        if (canReuse) {
            col = col0;
            col.dataset.layoutMounted = '1';
            ensureLeafId(col);
            if (node.id) col.dataset.leafId = node.id;
            if (parentEl !== col.parentElement) {
                const kids = getGroupChildNodes(parentEl);
                if (kids.length >= 1) {
                    const handle = document.createElement('div');
                    handle.className = 'split-resize-handle';
                    insertNode(handle);
                }
                insertNode(col);
            }
            remumberSplitColumns();
            const idx = parseInt(col.dataset.column, 10);
            rememberHandlesFromWorkspaceEntry(idx, node);
            stampComposerDraftScope(getLiveFrame(idx), idx);
            navigate(idx, pageFromLayoutLeaf(node), { force: !!options.forceNavigate });
        } else {
            const built = createSplitLeafColumn(pageFromLayoutLeaf(node), node.id);
            if (!built) return;
            const kids = getGroupChildNodes(parentEl);
            if (kids.length >= 1) {
                const handle = document.createElement('div');
                handle.className = 'split-resize-handle';
                insertNode(handle);
            }
            insertNode(built.clone);
            col = built.clone;
            col.dataset.layoutMounted = '1';
            remumberSplitColumns();
            const newIdx = parseInt(col.dataset.column, 10);
            rememberHandlesFromWorkspaceEntry(newIdx, node);
            mountNewLeafFrame(newIdx, built.frame, built.mainEl, pageFromLayoutLeaf(node));
            setupColumnListeners(newIdx, col);
            setupLayoutDragDrop(col);
            applyLayoutToColumn(col, lastUILayout);
        }
        if (col && node.flex) col.style.flex = node.flex;
        return;
    }

    if (node.type === 'group' && Array.isArray(node.children)) {
        let groupEl = parentEl;
        const absorb = !!(options.absorbIntoRoot && parentEl.id === 'splitContainer');
        const isEmptyRoot = parentEl.id === 'splitContainer'
            && getGroupChildNodes(parentEl).length === 0
            && !parentEl.querySelector(':scope > .split-group:not([data-split-discarded="1"])');
        const isRoot = absorb || isEmptyRoot;
        if (!isRoot) {
            groupEl = createSplitGroupEl(node.orientation);
            if (node.flex) groupEl.style.flex = node.flex;
            if (node.id) groupEl.dataset.groupId = node.id;
            const kids = getGroupChildNodes(parentEl);
            if (kids.length >= 1) {
                const handle = document.createElement('div');
                handle.className = 'split-resize-handle';
                insertNode(handle);
            }
            insertNode(groupEl);
        } else {
            applyGroupOrientation(parentEl, node.orientation);
            if (node.id) parentEl.dataset.groupId = node.id;
        }
        // Nested mounts must not absorb into root again.
        const childOpts = {
            allowReuseColumn0: options.allowReuseColumn0,
            absorbIntoRoot: false,
            forceNavigate: options.forceNavigate,
        };
        node.children.forEach((ch) => {
            mountLayoutTree(ch, groupEl, childOpts);
        });
        rebuildGroupResizeHandles(groupEl);
        return;
    }
}

/** Physically remove soft-discarded split nodes after iframes are neutralized.
 *  NEVER call this synchronously from close/teardown — Chromium/Electron freezes
 *  when a just-live iframe tree is removed in the same turn as the click.
 */
function purgeDiscardedSplitNodes(scope) {
    const root = scope || splitContainer;
    if (!root) return;
    const discarded = Array.from(root.querySelectorAll('[data-split-discarded="1"]'));
    discarded.forEach((el) => {
        try {
            el.querySelectorAll('iframe').forEach((fr) => {
                try {
                    fr.removeAttribute('src');
                    fr.srcdoc = '';
                    fr.remove();
                } catch (_) {}
            });
        } catch (_) {}
    });
    // Deepest first so parents do not re-walk detached subtrees.
    discarded
        .slice()
        .sort((a, b) => b.querySelectorAll('*').length - a.querySelectorAll('*').length)
        .forEach((el) => {
            try { el.remove(); } catch (_) {}
        });
    if (root.dataset?.splitDiscarded === '1' && root.id !== 'splitContainer') {
        try { root.remove(); } catch (_) {}
    }
}

let _purgeDiscardedTimer = null;
function schedulePurgeDiscardedSplitNodes(delayMs) {
    const ms = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 1200;
    if (_purgeDiscardedTimer) {
        try { clearTimeout(_purgeDiscardedTimer); } catch (_) {}
    }
    _purgeDiscardedTimer = setTimeout(() => {
        _purgeDiscardedTimer = null;
        try { purgeDiscardedSplitNodes(splitContainer); } catch (err) {
            console.warn('[App Shell] purgeDiscardedSplitNodes failed:', err);
        }
    }, ms);
}

/** Recreate panes from localStorage after column 0 boots. */
function restoreSplitLayout() {
    const layout = savedBootLayout || readSplitLayout();
    if (!layout || !layout.root) return;

    _restoringLayout = true;
    try {
        // Drop anything beyond column 0, then mount tree (reusing col 0).
        removeExtraSplitColumnsNow();
        // Clear root handles / nested leftovers
        if (splitContainer) {
            splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
            splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
                discardSplitSubtree(g);
            });
            applyGroupOrientation(splitContainer, 'horizontal');
        }
        // Do not sync-remove discarded stubs here (Electron freeze). They are
        // display:none and skipped by getGroupChildNodes; purge is deferred.
        schedulePurgeDiscardedSplitNodes(2000);
        // Absorb into #splitContainer so we don't wrap beside the boot column 0.
        try {
            mountLayoutTree(layout.root, splitContainer, {
                allowReuseColumn0: true,
                absorbIntoRoot: true,
            });
        } catch (err) {
            console.error('[App Shell] restoreSplitLayout mount failed:', err);
        }
        getSplitColumns().forEach((c) => { delete c.dataset.layoutMounted; });
        remumberSplitColumns();
        // Apply flex from tree snapshot where present
        const applyFlexWalk = (node, el) => {
            if (!node || !el) return;
            if (node.type === 'leaf' || (!node.type && node.page)) {
                if (node.flex) el.style.flex = node.flex;
                return;
            }
            if (node.type === 'group') {
                const kids = getGroupChildNodes(el);
                (node.children || []).forEach((ch, i) => applyFlexWalk(ch, kids[i]));
                migrateFixedPxSplitFlex(kids, el);
            }
        };
        applyFlexWalk(layout.root, splitContainer);
        // Drop empty nested groups that would show as blank blade-less regions.
        sanitizeSplitDom();
    } finally {
        _restoringLayout = false;
    }
    persistSplitLayout();
}

function rememberHandlesFromWorkspaceEntry(colIdx, entry) {
    if (!entry || !Number.isFinite(colIdx)) return;
    rememberChatHandleFromPage(colIdx, entry.page);
    if (entry.chat) rememberChatHandle(colIdx, 'chat', entry.chat);
    if (entry.terminal) rememberChatHandle(colIdx, 'terminal', entry.terminal);
}

/** Drop extra blades immediately so a saved workspace can replace the layout. */
function removeExtraSplitColumnsNow() {
    // Re-read live columns each pass: collapsing a stack recreates its survivor
    // as a new element, so a precomputed list would skip it and leak the pane.
    let removed = 0;
    for (let guard = 0; guard < 256; guard++) {
        const extras = getSplitColumns().slice(1);
        if (!extras.length) break;
        const col = extras[extras.length - 1];
        removed += 1;
        const idx = parseInt(col.dataset.column, 10);
        frameRevealTokens.delete(idx);
        columnState.delete(idx);
        paneSessionByColumn.delete(idx);
        paneProjectByColumn.delete(idx);
        lastChatByColumn.delete(idx);
        if (focusedColumnIdx === idx) focusedColumnIdx = 0;
        const parent = getLeafParentGroup(col);
        const prevSib = col.previousElementSibling;
        const nextSib = col.nextElementSibling;
        if (prevSib?.classList?.contains('split-resize-handle')) prevSib.remove();
        else if (nextSib?.classList?.contains('split-resize-handle')) nextSib.remove();
        // Safe teardown — do not probe live iframe contentWindows (Chrome freeze).
        discardSplitSubtree(col);
        collapseSplitGroupIfNeeded(parent);
    }
    if (removed) {
        persistLastChatHandles();
        remumberSplitColumns();
        getSplitColumns().forEach((c) => {
            c.style.flex = '';
            clearPaneBoxStyles(c);
        });
        if (splitContainer) applyGroupOrientation(splitContainer, 'horizontal');
    }
}

function applyWorkspaceColumns(columns, orientation, root, opts = {}) {
    const tree = root && typeof root === 'object'
        ? root
        : flatLayoutToTree(columns, orientation);
    if (!tree) return false;

    _restoringLayout = true;
    try {
        removeExtraSplitColumnsNow();
        if (splitContainer) {
            splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => h.remove());
            splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
                discardSplitSubtree(g);
            });
            applyGroupOrientation(splitContainer, 'horizontal');
        }
        schedulePurgeDiscardedSplitNodes(2000);
        mountLayoutTree(tree, splitContainer, {
            allowReuseColumn0: true,
            absorbIntoRoot: true,
            forceNavigate: !!opts.forceNavigate,
        });
        getSplitColumns().forEach((c) => { delete c.dataset.layoutMounted; });
        remumberSplitColumns();
        sanitizeSplitDom();
    } finally {
        _restoringLayout = false;
    }
    persistSplitLayout();
    return true;
}

// ── Setup listeners for a column ───────────────────────────────
function setupColumnListeners(colIdx, colEl) {
    const els = getColumnElements(colEl);
    if (!els) return;

    const { column, toggle } = els;
    ensureRailPaneGrip(column);
    observeRailHeight(column);
    bindRailLogoModifierKeys();

    // Rail logo:
    //   + → append chat at far right
    //   Shift → horizontal split this cell · Alt → vertical split this cell
    //   Alt+Shift → absolute vertical (new cell at very bottom)
    //   Ctrl+ + → close this pane when others exist
    //   × → close · same Shift/Alt split modifiers
    const logoBtn = column.querySelector('.rail-logo-clickable');
    if (logoBtn) {
        logoBtn.addEventListener('click', (e) => {
            const col = column;
            const action = column.querySelector('.rail-logo-clickable')?.dataset.action
                || logoBtn.dataset.action;
            // Alt+Shift = absolute vertical append at bottom of the shell.
            if (e.shiftKey && e.altKey) {
                e.preventDefault();
                appendRootVerticalPane('/chat_page.html');
                return;
            }
            if (e.shiftKey) {
                e.preventDefault();
                splitLeafColumn(col, 'horizontal');
                return;
            }
            if (e.altKey) {
                e.preventDefault();
                splitLeafColumn(col, 'vertical');
                return;
            }
            // Primary + with Ctrl/Cmd: close this pane when others exist
            // so the top cell of a vertical stack can be dismissed without ×.
            if (
                action === 'add'
                && (e.ctrlKey || e.metaKey)
                && getSplitColumns().length >= 2
            ) {
                e.preventDefault();
                closeSplitColumn(col);
                return;
            }
            if (action === 'close') {
                closeSplitColumn(col);
            } else {
                // Primary +: always add a fresh chat at the far right of the root.
                appendRootHorizontalPane('/chat_page.html');
            }
        });
    }

    // Rail nav
    column.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (railEditing || railSuppressClick) return;
            if (e.target.closest('.rail-item-remove')) return;
            const idx = parseInt(column.dataset.column, 10);
            navigate(idx, btn.dataset.page);
        });
    });

    // Notifications rail button (opens popover, does not navigate)
    column.querySelector('[data-id="nav-notifications"]')?.addEventListener('click', () => {
        if (railEditing || railSuppressClick) return;
        toggleNotificationsPopover();
    });

    column.querySelector('[data-id="nav-workspace"]')?.addEventListener('click', () => {
        if (railEditing || railSuppressClick) return;
        toggleWorkspacePopover();
    });

    // Account rail button (sign in / sign out, does not navigate)
    column.querySelector('[data-id="nav-account"]')?.addEventListener('click', () => {
        if (railEditing || railSuppressClick) return;
        const idx = parseInt(column.dataset.column, 10);
        handleAccountClick(idx);
    });

    // Sidebar toggle: collapse/expand the left blade (icon rail).
    toggle?.addEventListener('click', () => {
        if (railEditing || railSuppressClick) return;
        const idx = parseInt(column.dataset.column, 10);
        const state = getState(idx);
        applyRailCollapsed(idx, !state.railCollapsed);
    });
    setupRailCustomize(column);
    setupPaneReorder(column);

    column.querySelector('.rail-expand-tab')?.addEventListener('click', () => {
        const idx = parseInt(column.dataset.column, 10);
        applyRailCollapsed(idx, false);
    });
}

// Run setup for column 0 (existing)
setupColumnListeners(0, getColumnEl(0));
ensureLeafId(getColumnEl(0));
observeRailHeight(getColumnEl(0));
bindRailLogoModifierKeys();
if (splitContainer) applyGroupOrientation(splitContainer, 'horizontal');
// Recreate extra split panes from last session (localStorage)
restoreSplitLayout();
sanitizeSplitDom();
updateSplitContainerChrome();
syncSplitColumnLogos();
syncSplitColumnLogos();

// ── UI Layout: load, apply, save, drag-and-drop ─────────────────
function getLayoutFromDOM(colEl) {
    const col = colEl || getColumnEl(0);
    if (!col) {
        return {
            rail_items: CANONICAL_RAIL_ITEM_ORDER.slice(),
            rail_footer: CANONICAL_RAIL_FOOTER_ORDER.slice(),
            rail_hidden: [],
        };
    }
    const rail = col.querySelector('.rail-items');
    const footer = col.querySelector('.rail-footer');
    const stash = col.querySelector('.rail-stash');
    const hidden = Array.from(stash?.querySelectorAll('.rail-item[data-id]') || [])
        .map(e => e.dataset.id)
        .filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id));
    return {
        rail_items: Array.from(rail?.querySelectorAll('.rail-item[data-id]') || [])
            .map(e => e.dataset.id)
            .filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id)),
        rail_hidden: hidden,
        rail_footer: Array.from(footer?.querySelectorAll('.rail-item[data-id], .rail-panel-toggle[data-id]') || []).map(e => e.dataset.id),
    };
}

function insertAtCanonicalSlot(result, id, canonical) {
    const canonIdx = canonical.indexOf(id);
    let insertAt = result.length;
    for (let i = 0; i < result.length; i++) {
        const ri = canonical.indexOf(result[i]);
        if (ri === -1) continue;
        if (ri > canonIdx) {
            insertAt = i;
            break;
        }
    }
    result.splice(insertAt, 0, id);
}

/**
 * Merge saved rail order with DOM: ids missing from older settings are inserted at their
 * canonical slot. Stray or retired ids are ignored here.
 */
function normalizeRailItemOrder(savedIds, colEl) {
    const known = new Set(CANONICAL_RAIL_ITEM_ORDER);
    const rail = colEl?.querySelector?.('.rail-items');
    const stash = colEl?.querySelector?.('.rail-stash');
    const domIds = [
        ...Array.from(rail?.querySelectorAll('.rail-item[data-id]') || []),
        ...Array.from(stash?.querySelectorAll('.rail-item[data-id]') || []),
    ].map(e => e.dataset.id).filter(id => known.has(id));
    const canonInDom = CANONICAL_RAIL_ITEM_ORDER.filter(id => domIds.includes(id));
    if (!Array.isArray(savedIds) || savedIds.length === 0) return canonInDom;
    const validSaved = savedIds.filter(id => known.has(id) && canonInDom.includes(id));
    const missing = canonInDom.filter(id => !validSaved.includes(id));
    const result = validSaved.slice();
    for (const mid of missing) insertAtCanonicalSlot(result, mid, CANONICAL_RAIL_ITEM_ORDER);
    return result;
}

/**
 * Visible page icons. If `rail_hidden` is present (even empty), respect add/remove.
 * Legacy saves without that key keep the old "re-insert missing icons" behavior.
 */
function resolveVisibleRailIds(layout, colEl) {
    if (!Array.isArray(layout?.rail_hidden)) {
        return normalizeRailItemOrder(layout?.rail_items, colEl);
    }
    const hidden = new Set(layout.rail_hidden.filter(
        id => CANONICAL_RAIL_ITEM_ORDER.includes(id) && !RAIL_LOCKED_IDS.has(id),
    ));
    const saved = Array.isArray(layout.rail_items)
        ? layout.rail_items.filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id) && !hidden.has(id))
        : [];
    const present = new Set([...saved, ...hidden]);
    const result = saved.slice();
    for (const id of CANONICAL_RAIL_ITEM_ORDER) {
        if (present.has(id)) continue;
        insertAtCanonicalSlot(result, id, CANONICAL_RAIL_ITEM_ORDER);
    }
    return result;
}

/** Footer: account, notifications, workspace, settings, panel toggle (not page nav). */
function normalizeRailFooterOrder(savedIds) {
    const allowed = new Set(CANONICAL_RAIL_FOOTER_ORDER);
    const base = Array.isArray(savedIds) ? savedIds.filter(id => allowed.has(id)) : [];
    for (const id of CANONICAL_RAIL_FOOTER_ORDER) {
        if (base.includes(id)) continue;
        insertAtCanonicalSlot(base, id, CANONICAL_RAIL_FOOTER_ORDER);
    }
    return base;
}

function applyLayoutToColumn(colEl, layout) {
    if (!colEl || !layout) return;
    ensureRailEditChrome(colEl);
    const rail = colEl.querySelector('.rail-items');
    const footer = colEl.querySelector('.rail-footer');
    const stash = colEl.querySelector('.rail-stash');
    const addSlot = colEl.querySelector('.rail-add-slot');
    const byId = new Map();
    colEl.querySelectorAll('.rail-item[data-id], .rail-panel-toggle[data-id]').forEach(el => {
        if (el.dataset.id) byId.set(el.dataset.id, el);
    });

    const visible = resolveVisibleRailIds(layout, colEl);
    visible.forEach(id => {
        const el = byId.get(id);
        if (el && rail) rail.appendChild(el);
    });
    CANONICAL_RAIL_ITEM_ORDER.forEach(id => {
        if (visible.includes(id)) return;
        const el = byId.get(id);
        if (el && stash) stash.appendChild(el);
    });
    if (addSlot && rail) rail.appendChild(addSlot);

    const footerOrder = normalizeRailFooterOrder(layout.rail_footer);
    footerOrder.forEach(id => {
        const el = byId.get(id);
        if (el && footer) footer.appendChild(el);
    });
}

function applyLayoutToAllColumns(layout) {
    document.querySelectorAll('.split-column').forEach(col => applyLayoutToColumn(col, layout));
    if (typeof window.ensureCuttleMobileApkUpdateUi === 'function') {
        window.ensureCuttleMobileApkUpdateUi();
    }
}

function commitUILayout(layout) {
    lastUILayout = {
        rail_items: Array.isArray(layout?.rail_items) ? layout.rail_items.slice() : CANONICAL_RAIL_ITEM_ORDER.slice(),
        rail_footer: Array.isArray(layout?.rail_footer) ? layout.rail_footer.slice() : CANONICAL_RAIL_FOOTER_ORDER.slice(),
        rail_hidden: Array.isArray(layout?.rail_hidden) ? layout.rail_hidden.slice() : [],
        layout_version: RAIL_LAYOUT_VERSION,
    };
    applyLayoutToAllColumns(lastUILayout);
    cacheUILayout(lastUILayout);
    broadcastAppsList();
    fetch('/api/settings/ui-layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lastUILayout)
    }).catch(() => {});
}

const STORAGE_UI_LAYOUT = 'shell_ui_layout';

function cacheUILayout(layout) {
    try { localStorage.setItem(STORAGE_UI_LAYOUT, JSON.stringify(layout)); } catch (_) {}
}

function readCachedUILayout() {
    try {
        const raw = JSON.parse(localStorage.getItem(STORAGE_UI_LAYOUT) || 'null');
        return raw && typeof raw === 'object' ? raw : null;
    } catch (_) {
        return null;
    }
}

/**
 * Saved layouts from before the Apps page get the new default-hidden apps
 * stashed once; later user pins survive because the version is then current.
 */
function migrateUILayout(saved) {
    const layout = { ...DEFAULT_LAYOUT, ...saved };
    if (!Array.isArray(saved.rail_hidden)) delete layout.rail_hidden;
    if ((Number(saved.layout_version) || 0) >= RAIL_LAYOUT_VERSION) return { layout, changed: false };
    const hidden = new Set(Array.isArray(saved.rail_hidden) ? saved.rail_hidden : []);
    // Existing v4 user pins remain intact; only the new App starts stashed.
    const version = Number(saved.layout_version) || 0;
    const additions = version >= 6 ? ['nav-gizmos']
        : version >= 5 ? ['nav-projects', 'nav-gizmos']
        : version >= 4 ? ['nav-achievements', 'nav-projects', 'nav-gizmos']
        : DEFAULT_RAIL_HIDDEN;
    additions.forEach(id => hidden.add(id));
    const known = new Set(CANONICAL_RAIL_ITEM_ORDER);
    layout.rail_hidden = Array.from(hidden).filter(id => known.has(id));
    layout.rail_items = (Array.isArray(saved.rail_items) ? saved.rail_items : CANONICAL_RAIL_ITEM_ORDER)
        .filter(id => known.has(id) && !hidden.has(id));
    layout.layout_version = RAIL_LAYOUT_VERSION;
    return { layout, changed: true };
}

// ── Cuttle web apps (Apps page ↔ blade bar) ─────────────────────
/** Every page app on the rail or in the stash, alphabetical like an app drawer. */
function getAppsList() {
    const col = getColumnEl(0);
    if (!col) return [];
    const apps = [];
    CANONICAL_RAIL_ITEM_ORDER.forEach(id => {
        if (RAIL_LOCKED_IDS.has(id)) return;
        const el = col.querySelector(`.rail-items .rail-item[data-id="${id}"], .rail-stash .rail-item[data-id="${id}"]`);
        if (!el || !el.dataset.page) return;
        apps.push({
            id,
            label: el.dataset.tooltip || id.replace(/^nav-/, ''),
            page: el.dataset.page,
            icon: el.querySelector('svg')?.outerHTML || '',
            pinned: !!el.closest('.rail-items'),
        });
    });
    apps.sort((a, b) => a.label.localeCompare(b.label));
    return apps;
}

function broadcastAppsList() {
    broadcastToFrames({ type: 'cuttle-apps', apps: getAppsList() });
}

window.addEventListener('message', (e) => {
    const d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.type === 'cuttle-apps-request') {
        try { e.source?.postMessage({ type: 'cuttle-apps', apps: getAppsList() }, '*'); } catch (_) {}
    } else if (d.type === 'cuttle-apps-pin' && typeof d.id === 'string') {
        if (d.pinned) showRailItem(d.id);
        else hideRailItem(d.id);
    }
});

function saveUILayout(fromCol) {
    commitUILayout(getLayoutFromDOM(fromCol || getColumnEl(0)));
}

function hideRailAddPicker() {
    const picker = document.getElementById('railAddPicker');
    if (picker) picker.hidden = true;
}

function setRailEditing(on) {
    railEditing = !!on;
    document.querySelectorAll('.icon-rail').forEach(r => r.classList.toggle('rail-editing', railEditing));
    if (!railEditing) hideRailAddPicker();
}

function ensureRailEditChrome(colEl) {
    const rail = colEl?.querySelector?.('.rail-items');
    if (!rail) return;
    let stash = colEl.querySelector('.rail-stash');
    if (!stash) {
        stash = document.createElement('div');
        stash.className = 'rail-stash';
        stash.hidden = true;
        rail.insertAdjacentElement('afterend', stash);
    }
    let addSlot = rail.querySelector('.rail-add-slot');
    if (!addSlot) {
        addSlot = document.createElement('button');
        addSlot.type = 'button';
        addSlot.className = 'rail-add-slot';
        addSlot.dataset.tooltip = 'Add page';
        addSlot.setAttribute('aria-label', 'Add page to sidebar');
        addSlot.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
        rail.appendChild(addSlot);
    }
    colEl.querySelectorAll('.rail-items .rail-item[data-id], .rail-stash .rail-item[data-id]').forEach(btn => {
        if (!CANONICAL_RAIL_ITEM_ORDER.includes(btn.dataset.id)) return;
        if (RAIL_LOCKED_IDS.has(btn.dataset.id)) return;
        if (btn.querySelector('.rail-item-remove')) return;
        const rm = document.createElement('span');
        rm.className = 'rail-item-remove';
        rm.setAttribute('role', 'button');
        rm.setAttribute('aria-label', 'Remove from sidebar');
        rm.textContent = '×';
        btn.appendChild(rm);
    });
}

function hideRailItem(id) {
    if (!CANONICAL_RAIL_ITEM_ORDER.includes(id) || RAIL_LOCKED_IDS.has(id)) return;
    const layout = getLayoutFromDOM(getColumnEl(0));
    layout.rail_items = layout.rail_items.filter(x => x !== id);
    if (!layout.rail_hidden.includes(id)) layout.rail_hidden.push(id);
    commitUILayout(layout);
}

function showRailItem(id) {
    if (!CANONICAL_RAIL_ITEM_ORDER.includes(id)) return;
    const layout = getLayoutFromDOM(getColumnEl(0));
    layout.rail_hidden = layout.rail_hidden.filter(x => x !== id);
    if (!layout.rail_items.includes(id)) {
        const appsIdx = layout.rail_items.indexOf('nav-apps');
        if (appsIdx >= 0) layout.rail_items.splice(appsIdx, 0, id);
        else layout.rail_items.push(id);
    }
    commitUILayout(layout);
}

function openRailAddPicker(anchor) {
    const picker = document.getElementById('railAddPicker');
    const list = picker?.querySelector('.rail-add-picker-list');
    if (!picker || !list) return;
    const layout = getLayoutFromDOM(getColumnEl(0));
    const hiddenIds = (layout.rail_hidden || []).filter(id => CANONICAL_RAIL_ITEM_ORDER.includes(id));
    list.innerHTML = '';
    if (!hiddenIds.length) {
        const empty = document.createElement('div');
        empty.className = 'rail-add-picker-empty';
        empty.textContent = 'All pages are on the bar';
        list.appendChild(empty);
    } else {
        const sourceCol = getColumnEl(0);
        hiddenIds.forEach(id => {
            const src = sourceCol?.querySelector(`.rail-stash .rail-item[data-id="${id}"]`);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rail-add-picker-item';
            btn.setAttribute('role', 'menuitem');
            const icon = src?.querySelector('svg')?.cloneNode(true);
            if (icon) btn.appendChild(icon);
            const label = document.createElement('span');
            label.textContent = src?.dataset?.tooltip || id.replace(/^nav-/, '');
            btn.appendChild(label);
            btn.addEventListener('click', () => {
                showRailItem(id);
                hideRailAddPicker();
            });
            list.appendChild(btn);
        });
    }
    picker.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const pad = 8;
    let left = rect.right + 10;
    let top = rect.top;
    picker.style.left = '0px';
    picker.style.top = '0px';
    const pw = picker.offsetWidth || 180;
    const ph = picker.offsetHeight || 120;
    if (left + pw > window.innerWidth - pad) left = Math.max(pad, rect.left - pw - 10);
    if (top + ph > window.innerHeight - pad) top = Math.max(pad, window.innerHeight - ph - pad);
    picker.style.left = `${Math.round(left)}px`;
    picker.style.top = `${Math.round(top)}px`;
}

function ensureRailCustomizeGlobals() {
    if (railCustomizeGlobalsReady) return;
    railCustomizeGlobalsReady = true;
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setRailEditing(false);
    });
    document.addEventListener('pointerdown', (e) => {
        if (!railEditing) return;
        if (e.target.closest('.icon-rail, .rail-add-picker')) return;
        setRailEditing(false);
    });
    document.addEventListener('click', (e) => {
        if (!railSuppressClick) return;
        e.preventDefault();
        e.stopPropagation();
        railSuppressClick = false;
    }, true);
}

function setupRailCustomize(colEl) {
    if (!colEl) return;
    ensureRailEditChrome(colEl);
    ensureRailCustomizeGlobals();
    const nav = colEl.querySelector('.icon-rail');
    if (!nav || nav.dataset.railCustomize === '1') return;
    nav.dataset.railCustomize = '1';

    let holdTimer = null;
    let holdStart = null;

    const clearHold = () => {
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        holdStart = null;
    };

    nav.addEventListener('pointerdown', (e) => {
        if (e.target.closest('.rail-item-remove')) {
            e.stopPropagation();
            return;
        }
        if (e.button != null && e.button !== 0) return;
        if (e.target.closest('.rail-add-slot, .rail-logo')) return;
        const item = e.target.closest('.rail-item[data-id]');
        if (!item || !nav.contains(item)) return;
        holdStart = { x: e.clientX, y: e.clientY };
        holdTimer = setTimeout(() => {
            holdTimer = null;
            setRailEditing(true);
            railSuppressClick = true;
        }, RAIL_HOLD_MS);
    });

    nav.addEventListener('pointermove', (e) => {
        if (!holdStart) return;
        const dx = e.clientX - holdStart.x;
        const dy = e.clientY - holdStart.y;
        if ((dx * dx) + (dy * dy) > (RAIL_HOLD_MOVE_PX * RAIL_HOLD_MOVE_PX)) clearHold();
    });

    nav.addEventListener('pointerup', clearHold);
    nav.addEventListener('pointercancel', clearHold);
    nav.addEventListener('dragstart', clearHold, true);

    nav.addEventListener('click', (e) => {
        const rm = e.target.closest('.rail-item-remove');
        if (rm) {
            e.preventDefault();
            e.stopPropagation();
            const item = rm.closest('.rail-item[data-id]');
            if (item) hideRailItem(item.dataset.id);
            return;
        }
        const add = e.target.closest('.rail-add-slot');
        if (add) {
            e.preventDefault();
            e.stopPropagation();
            openRailAddPicker(add);
        }
    });
}

function setupLayoutDragDrop(colEl) {
    if (!colEl) return;
    let draggedEl = null;

    const dragStart = (e) => {
        if (!e.target.closest('[draggable="true"]')) return;
        draggedEl = e.target.closest('[draggable="true"]');
        draggedEl.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedEl.dataset.id || draggedEl.id || '');
        e.dataTransfer.setData('cuttle/source', (e.target.closest('[data-droppable]') || {}).dataset?.droppable || '');
    };

    const dragEnd = () => {
        if (draggedEl) draggedEl.classList.remove('dragging');
        draggedEl = null;
        colEl.querySelectorAll('[data-droppable].drop-target').forEach(z => z.classList.remove('drop-target'));
    };

    const dragOver = (e) => {
        const dz = e.target.closest('[data-droppable]');
        if (!dz) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        colEl.querySelectorAll('[data-droppable].drop-target').forEach(z => z.classList.remove('drop-target'));
        dz.classList.add('drop-target');
    };

    const drop = (e) => {
        e.preventDefault();
        const dz = e.target.closest('[data-droppable]');
        if (!dz || !draggedEl) return;
        const selector = '.rail-item[data-id], .rail-panel-toggle[data-id]';
        let target = e.target.closest(selector);
        if (target === draggedEl) target = null;
        if (target) {
            const rect = target.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            if (e.clientY < midY) dz.insertBefore(draggedEl, target);
            else dz.insertBefore(draggedEl, target.nextSibling);
        } else {
            dz.appendChild(draggedEl);
        }
        dz.classList.remove('drop-target');
        saveUILayout(colEl);
    };

    const dragLeave = (e) => {
        const left = e.target.closest('[data-droppable]');
        if (left && !left.contains(e.relatedTarget)) left.classList.remove('drop-target');
    };

    colEl.addEventListener('dragstart', dragStart, true);
    colEl.addEventListener('dragend', dragEnd, true);
    colEl.addEventListener('dragover', dragOver, true);
    colEl.addEventListener('dragleave', dragLeave, true);
    colEl.addEventListener('drop', drop, true);
}

// Paint the last known layout now so stashed apps don't flash on the rail
// while the server copy loads.
{
    const cached = readCachedUILayout();
    lastUILayout = cached ? migrateUILayout(cached).layout : { ...DEFAULT_LAYOUT };
    applyLayoutToAllColumns(lastUILayout);
}

// Load saved layout on init — apply to every blade so splits stay in sync.
fetch('/api/settings/ui-layout', { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
        const saved = data?.ui_layout;
        if (saved && Object.keys(saved).length > 0) {
            const { layout, changed } = migrateUILayout(saved);
            if (changed) {
                commitUILayout(layout);
                return;
            }
            lastUILayout = layout;
        } else {
            lastUILayout = { ...DEFAULT_LAYOUT };
        }
        applyLayoutToAllColumns(lastUILayout);
        cacheUILayout(lastUILayout);
        broadcastAppsList();
    })
    .catch(() => {})
    .finally(() => {
        setupLayoutDragDrop(getColumnEl(0));
    });

function basePagePath(p) {
    if (!p) return '';
    const s = String(p);
    const q = s.indexOf('?');
    return q >= 0 ? s.slice(0, q) : s;
}

function updateColumnUI(colIdx, page) {
    const col = getColumnEl(colIdx);
    if (!col) return;
    const cur = basePagePath(page);
    col.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        btn.classList.toggle('active', basePagePath(btn.dataset.page) === cur);
    });
}

// ── Navigate: replace iframe element for reliability ──────────
// Using frame.src = url silently fails in some renderer states
// Replace the frame to avoid carrying stale renderer state across pages.
// Replacing the DOM element is unconditionally reliable.
/** Navigate the split column that contains `source` (child iframe window). */
function navigateFromSource(source, page) {
    navigate(findColumnIndexForSource(source), page);
}

function navigate(colIdx, page, opts) {
    page = withRememberedChat(colIdx, page);
    // Jobs mesh UI — bust cache + pass viewer identity (Jobs iframe has no preload).
    try {
        const u = new URL(page, window.location.origin);
        if (u.pathname === '/jobs_page.html') {
            if (!u.searchParams.has('_v')) {
                u.searchParams.set('_v', String(Date.now()));
            }
            try {
                const wid = String(
                    (desktopCfg && desktopCfg.workerId) ||
                    sessionStorage.getItem('cuttle_viewer_worker_id') ||
                    ''
                ).trim().toLowerCase();
                const clientMode = !!(desktopCfg && desktopCfg.clientMode) ||
                    sessionStorage.getItem('cuttle_client_mode') === '1';
                if (wid && !u.searchParams.has('me')) u.searchParams.set('me', wid);
                if (clientMode && !u.searchParams.has('client')) u.searchParams.set('client', '1');
            } catch (_) {}
            page = u.pathname + u.search + u.hash;
        }
    } catch (_) {}
    rememberChatHandleFromPage(colIdx, page);
    const state = getState(colIdx);
    if (state.page === page && !(opts && opts.force)) return;
    console.log('[App Shell] Navigating to:', page);
    if (colIdx === 0 && !shellApplyingHistory && !(opts && opts.history === 'none')) {
        pushShellBackEntry(state.page);
    }
    setState(colIdx, page);
    paneSessionByColumn.delete(colIdx);
    paneProjectByColumn.delete(colIdx);

    if (colIdx === 0) {
        sessionStorage.setItem(STORAGE_PAGE, page);
        // Top-level ?chat= for chat + terminal deep-links (refresh must keep the handle).
        try {
            const pageUrl = new URL(page, window.location.origin);
            const isChatSurface = pageUrl.pathname === '/chat_page.html'
                || pageUrl.pathname === '/terminal_page.html';
            const cid = isChatSurface
                ? (pageUrl.searchParams.get('chat') || pageUrl.searchParams.get('session'))
                : null;
            const histMode = (opts && opts.history)
                || (shellApplyingHistory ? 'none' : 'replace');
            syncShellChatUrl(cid, { mode: histMode, page: page });
        } catch (_) {}
    }
    updateColumnUI(colIdx, page);
    persistSplitLayout();

    const newFrame = opts && opts.force
        ? replaceFrameInPlace(colIdx, page)
        : replaceFrame(colIdx, page);
    if (newFrame) {
        attachFrameLoadListener(colIdx, newFrame);
    }
}

// ── Account button (rail footer) ────────────────────────────────
const ACCOUNT_PLACEHOLDER_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

let shellAuthUser = null;

function accountDisplayName(user) {
    if (!user) return '';
    return user.username ? '@' + user.username : (user.display_name || user.email || 'Signed in');
}

function accountInitials(user) {
    if (window.CuttleAuth && window.CuttleAuth.userAvatarInitials) {
        return window.CuttleAuth.userAvatarInitials(user);
    }
    const label = user.display_name || user.username || user.email || '?';
    const initials = label.trim().split(/[\s._-]+/).filter(Boolean).map(p => p[0]).join('');
    return (initials || '?').toUpperCase().slice(0, 2);
}

function accountAvatarInnerHtml(user) {
    if (window.CuttleAuth && window.CuttleAuth.userAvatarInnerHtml) {
        return window.CuttleAuth.userAvatarInnerHtml(user);
    }
    if (user.profile_image) {
        return `<img src="${escapeHtml(user.profile_image)}" alt="">`;
    }
    return accountInitials(user);
}

function renderAccountButtons() {
    document.querySelectorAll('.rail-account').forEach(btn => {
        const avatar = btn.querySelector('.rail-account-avatar');
        btn.classList.toggle('signed-in', !!shellAuthUser);
        btn.classList.toggle('signed-out', !shellAuthUser);
        if (shellAuthUser) {
            const name = accountDisplayName(shellAuthUser);
            btn.dataset.tooltip = 'Account — ' + name;
            btn.setAttribute('aria-label', 'Account — signed in as ' + name + '. Click to log out');
            if (avatar) {
                avatar.innerHTML = accountAvatarInnerHtml(shellAuthUser);
            }
        } else {
            btn.dataset.tooltip = 'Account — signed out';
            btn.setAttribute('aria-label', 'Account — signed out. Click to sign in');
            if (avatar) avatar.innerHTML = ACCOUNT_PLACEHOLDER_SVG;
        }
    });
}

function setShellAuthUser(user) {
    shellAuthUser = user || null;
    renderAccountButtons();
}

async function refreshShellAuth() {
    try {
        const res = await fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' });
        const data = await res.json().catch(() => null);
        if (data && typeof data.authenticated === 'boolean') {
            setShellAuthUser(data.authenticated ? data.user : null);
        }
        // Network/parse failure: keep prior shellAuthUser (don't flash signed-out).
    } catch (_) {
        /* keep prior */
    }
}

/** Open the app-wide sign-in modal (lives on the shell, not the chat page). */
function openSignInForColumn(_colIdx) {
    if (typeof openAuthModal === 'function') {
        openAuthModal('login');
        return;
    }
    if (window.CuttleAuth && typeof window.CuttleAuth.openAuthModal === 'function') {
        window.CuttleAuth.openAuthModal('login');
        return;
    }
    // Fallback: ask the chat iframe to open its modal (standalone / older builds)
    navigate(0, '/chat_page.html?signin=1');
}

function getLogoutConfirmModal() {
    return document.getElementById('logoutConfirmModal');
}

function closeLogoutConfirmModal() {
    const modal = getLogoutConfirmModal();
    if (!modal) return;
    modal.classList.remove('active');
    const submit = document.getElementById('logoutConfirmSubmit');
    if (submit) {
        submit.disabled = false;
        submit.textContent = 'Log out';
    }
}

function openLogoutConfirmModal() {
    if (!shellAuthUser) return;
    const modal = getLogoutConfirmModal();
    if (!modal) return;
    cuttleSyncMobileServerRows();

    const name = accountDisplayName(shellAuthUser);
    const title = document.getElementById('logoutConfirmTitle');
    if (title) title.textContent = name || 'Account';

    const provider = shellAuthUser.auth_provider || 'local';
    const isGuest = !!(shellAuthUser.is_guest || provider === 'guest');
    const isLocal = provider === 'local';
    const isGoogle = provider === 'google';
    const linked = !!(shellAuthUser.google_linked || shellAuthUser.provider_user_id);
    const canLinkGoogle = window.CuttleAuth && window.CuttleAuth.userCanLinkGoogle
        ? window.CuttleAuth.userCanLinkGoogle(shellAuthUser)
        : ((isLocal || isGuest) && !linked);
    const canManageGoogle = isLocal || isGuest || isGoogle;

    const subtitle = document.getElementById('logoutConfirmSubtitle');
    if (subtitle) {
        const uname = shellAuthUser.username ? `@${shellAuthUser.username}` : '';
        if (isGuest && !linked) {
            subtitle.textContent = uname
                ? `${uname} — sign in with Google to keep this account, or log out.`
                : 'Sign in with Google to keep this account, or log out.';
        } else {
            subtitle.textContent = uname
                ? `${uname} — connect Google for your avatar, or log out.`
                : 'Connect Google for your avatar, or log out.';
        }
    }

    const avatar = document.getElementById('logoutConfirmAvatar');
    if (avatar) {
        avatar.innerHTML = accountAvatarInnerHtml(shellAuthUser);
    }

    const linkBtn = document.getElementById('logoutConfirmLinkGoogle');
    const refreshBtn = document.getElementById('logoutConfirmRefreshGoogle');
    const statusEl = document.getElementById('logoutConfirmGoogleStatus');

    if (linkBtn) {
        linkBtn.hidden = !canLinkGoogle;
        linkBtn.textContent = isGuest ? 'Continue with Google' : 'Connect Google';
        linkBtn.href = '/api/auth/oauth/google?link=1';
    }
    if (refreshBtn) {
        refreshBtn.hidden = !(canManageGoogle && linked && !isGuest);
    }
    if (statusEl) {
        statusEl.hidden = !(canManageGoogle && linked);
        statusEl.textContent = linked ? 'Google linked — avatar syncs to this account.' : '';
    }

    modal.classList.add('active');
    const cancel = document.getElementById('logoutConfirmCancel');
    if (cancel) cancel.focus();
}

async function performShellLogout() {
    const submit = document.getElementById('logoutConfirmSubmit');
    if (submit) {
        submit.disabled = true;
        submit.textContent = 'Logging out…';
    }
    try {
        if (typeof handleLogout === 'function') {
            await handleLogout({ skipRedirect: true });
            setShellAuthUser(null);
            return;
        }
        try {
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
        } catch (_) {}
        setShellAuthUser(null);
        document.querySelectorAll('.split-column .shell-main iframe').forEach(fr => {
            try {
                fr.contentWindow?.postMessage({ type: 'cuttle-auth-changed', user: null }, '*');
            } catch (_) {}
        });
    } finally {
        closeLogoutConfirmModal();
    }
}

function setupLogoutConfirmModal() {
    const modal = getLogoutConfirmModal();
    if (!modal || modal.dataset.bound === '1') return;
    modal.dataset.bound = '1';

    const closeBtn = document.getElementById('logoutConfirmClose');
    const cancelBtn = document.getElementById('logoutConfirmCancel');
    const submitBtn = document.getElementById('logoutConfirmSubmit');
    const refreshBtn = document.getElementById('logoutConfirmRefreshGoogle');

    if (closeBtn) closeBtn.addEventListener('click', closeLogoutConfirmModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeLogoutConfirmModal);
    if (submitBtn) submitBtn.addEventListener('click', performShellLogout);
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            window.location.href = '/api/auth/oauth/google?link=1';
        });
    }

    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeLogoutConfirmModal();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            closeLogoutConfirmModal();
        }
    });
}

async function handleAccountClick(_colIdx) {
    if (!shellAuthUser) {
        openSignInForColumn(_colIdx);
        return;
    }
    openLogoutConfirmModal();
}

/* Phone (Capacitor) client: no floating Server button — switching servers
   lives in the login modal and the account popup, next to log out. */
function cuttleIsMobileClient() {
    try {
        if (window.isCuttleMobile || window.cuttleMobile?.isNative) return true;
    } catch (_) {}
    return /\bCuttleMobile\/[\d.]+\b/.test(navigator.userAgent || '');
}

function cuttleOpenMobileServerSettings() {
    try {
        if (window.cuttleMobile?.openSettings) {
            window.cuttleMobile.openSettings();
            return;
        }
    } catch (_) {}
    try {
        if (window.CuttleShellNative?.openSettings) {
            window.CuttleShellNative.openSettings();
            return;
        }
    } catch (_) {}
    window.location.href = 'https://localhost/index.html?setup=1';
}

function cuttleSyncMobileServerRows() {
    const show = cuttleIsMobileClient();
    let origin = '';
    try {
        origin = window.location.origin || '';
    } catch (_) {}
    const row = document.getElementById('authServerRow');
    if (row) {
        row.hidden = !show;
        const label = document.getElementById('authServerOrigin');
        if (label && origin) label.textContent = origin;
    }
    const sw = document.getElementById('logoutConfirmSwitchServer');
    if (sw) sw.hidden = !show;
}

document.addEventListener('click', (e) => {
    const t = e.target?.closest?.('#logoutConfirmSwitchServer, #authServerChange');
    if (!t) return;
    e.preventDefault();
    closeLogoutConfirmModal();
    cuttleOpenMobileServerSettings();
});

setupLogoutConfirmModal();
renderAccountButtons();
refreshShellAuth();
(function handleAuthQueryFlags() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.has('auth_linked') || params.has('auth_success')) {
            refreshShellAuth().then(() => {
                params.delete('auth_linked');
                params.delete('auth_success');
                params.delete('auth_error');
                const q = params.toString();
                const next = window.location.pathname + (q ? '?' + q : '') + window.location.hash;
                window.history.replaceState({}, document.title, next);
            });
        } else if (params.has('auth_error')) {
            const err = params.get('auth_error') || 'unknown';
            console.warn('OAuth error:', err);
            params.delete('auth_error');
            const q = params.toString();
            window.history.replaceState({}, document.title, window.location.pathname + (q ? '?' + q : ''));
        }
    } catch (_) {}
})();
window.addEventListener('focus', refreshShellAuth);
window.addEventListener('cuttle-auth-changed', function (ev) {
    setShellAuthUser((ev.detail && ev.detail.user) || null);
});

// ── Notifications popover (past toasts) ─────────────────────────
function getNotificationsPopover() {
    return document.getElementById('notificationsPopover');
}

function isNotificationsPopoverOpen() {
    const pop = getNotificationsPopover();
    return pop && pop.classList.contains('notifications-popover-open');
}

function openNotificationsPopover() {
    closeWorkspacePopover();
    const pop = getNotificationsPopover();
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (pop) pop.classList.add('notifications-popover-open');
    if (back) back.classList.add('notifications-popover-backdrop-visible');
    renderNotificationsList();
}

function closeNotificationsPopover() {
    const pop = getNotificationsPopover();
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (pop) pop.classList.remove('notifications-popover-open');
    if (typeof setWorkspaceBackdropVisible === 'function') setWorkspaceBackdropVisible(false);
    else if (back) back.classList.remove('notifications-popover-backdrop-visible');
}

function toggleNotificationsPopover() {
    if (isNotificationsPopoverOpen()) closeNotificationsPopover();
    else {
        closeWorkspacePopover();
        openNotificationsPopover();
    }
}

function notificationUnreadIconHTML() {
    return '<span class="notifications-unread-icon" title="Unread response" aria-label="Unread response"></span>';
}

function openChatFromNotification(sessionId) {
    if (sessionId == null || sessionId === '') return;
    let sid = String(sessionId).trim();
    if (/^CH-\d+$/i.test(sid)) sid = String(parseInt(sid.slice(3), 10));
    else if (sid.startsWith('db_session_')) sid = sid.slice('db_session_'.length);
    const page = '/chat_page.html?chat=' + encodeURIComponent(sid);
    // Already on this chat (cold start deep-linked ?chat=) — do not replaceFrame again.
    try {
        const st = typeof getState === 'function' ? getState(0) : null;
        if (st && st.page === page) {
            closeNotificationsPopover();
            return;
        }
        const cur = new URL(window.location.href);
        if (cur.searchParams.get('chat') === sid || cur.searchParams.get('session') === sid) {
            const live = typeof getLiveFrame === 'function' ? getLiveFrame(0) : null;
            const href = live?.contentWindow?.location?.href || '';
            if (href && href.includes('chat=' + encodeURIComponent(sid))) {
                closeNotificationsPopover();
                return;
            }
        }
    } catch (_) {}
    if (typeof navigate === 'function') navigate(0, page);
    else if (typeof window.navigate === 'function') window.navigate(0, page);
    closeNotificationsPopover();
}
window.openChatFromNotification = openChatFromNotification;

function renderNotificationsList() {
    const listEl = document.getElementById('notificationsPopoverList');
    const emptyEl = document.getElementById('notificationsPopoverEmpty');
    if (!listEl || !emptyEl) return;
    const history = typeof window.getCuttleNotificationHistory === 'function' ? window.getCuttleNotificationHistory() : [];
    if (history.length === 0) {
        listEl.innerHTML = '';
        listEl.style.display = 'none';
        emptyEl.style.display = 'block';
        return;
    }
    emptyEl.style.display = 'none';
    listEl.style.display = 'block';
    const VARIANTS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    listEl.innerHTML = history.map((n, i) => {
        const icon = VARIANTS[n.variant] || 'ℹ';
        const timeStr = n.time ? new Date(n.time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
        const linkHtml = n.linkUrl ? ` <a href="${escapeHtml(n.linkUrl)}" target="_blank" rel="noopener" class="notifications-popover-item-link">${escapeHtml(n.linkText || 'View query log')}</a>` : '';
        const isUnreadResponse = !!(n.unread && !n.read);
        const unreadIcon = isUnreadResponse ? notificationUnreadIconHTML() : '';
        const unreadClass = isUnreadResponse ? ' is-unread' : '';
        const clickable = n.sessionId ? ' is-clickable' : '';
        const sidAttr = n.sessionId ? ` data-session-id="${escapeHtml(String(n.sessionId))}"` : '';
        const idAttr = n.id ? ` data-notif-id="${escapeHtml(String(n.id))}"` : '';
        return `<div class="notifications-popover-item notifications-popover-item-${n.variant}${unreadClass}${clickable}" data-index="${i}"${idAttr}${sidAttr}><span class="notifications-popover-item-icon">${escapeHtml(icon)}</span><span class="notifications-popover-item-message">${escapeHtml(n.message)}${linkHtml}</span>${unreadIcon}<span class="notifications-popover-item-time">${escapeHtml(timeStr)}</span></div>`;
    }).join('');

    listEl.querySelectorAll('.notifications-popover-item').forEach((el) => {
        el.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            const notifId = el.dataset.notifId;
            if (notifId && typeof window.markCuttleNotificationsRead === 'function') {
                window.markCuttleNotificationsRead(notifId);
            }
            const sid = el.dataset.sessionId;
            if (sid) openChatFromNotification(sid);
            else {
                renderNotificationsList();
                updateNotificationsBadge();
            }
        });
    });
}

function updateNotificationsBadge() {
    const unreadCount = typeof window.getCuttleUnreadNotificationCount === 'function'
        ? window.getCuttleUnreadNotificationCount()
        : 0;
    document.querySelectorAll('.rail-badge-notifications').forEach(badge => {
        badge.textContent = String(unreadCount);
        badge.style.display = unreadCount > 0 ? '' : 'none';
    });
}

(function setupNotificationsPopover() {
    const back = document.getElementById('notificationsPopoverBackdrop');
    const clearBtn = document.getElementById('notificationsClearBtn');
    if (back) back.addEventListener('click', () => {
        closeNotificationsPopover();
        closeWorkspacePopover();
    });
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            if (typeof window.clearCuttleNotificationHistory === 'function') window.clearCuttleNotificationHistory();
            renderNotificationsList();
            updateNotificationsBadge();
        });
    }
    window.addEventListener('cuttle-notification-added', updateNotificationsBadge);
    updateNotificationsBadge();
})();

// ── Workspace save / load (cross-device split + open chats) ─────
function getWorkspacePopover() {
    return document.getElementById('workspacePopover');
}

function isWorkspacePopoverOpen() {
    const pop = getWorkspacePopover();
    return pop && pop.classList.contains('notifications-popover-open');
}

function setWorkspaceBackdropVisible(on) {
    const back = document.getElementById('notificationsPopoverBackdrop');
    if (!back) return;
    if (on || isNotificationsPopoverOpen() || isWorkspacePopoverOpen()) {
        back.classList.add('notifications-popover-backdrop-visible');
    } else {
        back.classList.remove('notifications-popover-backdrop-visible');
    }
}

function openWorkspacePopover() {
    const pop = getWorkspacePopover();
    if (pop) pop.classList.add('notifications-popover-open');
    setWorkspaceBackdropVisible(true);
    const input = document.getElementById('workspaceNameInput');
    if (input && !input.value.trim()) input.value = 'Workspace';
    refreshWorkspacePopover();
}

function closeWorkspacePopover() {
    const pop = getWorkspacePopover();
    if (pop) pop.classList.remove('notifications-popover-open');
    setWorkspaceBackdropVisible(false);
}

function toggleWorkspacePopover() {
    if (isWorkspacePopoverOpen()) closeWorkspacePopover();
    else {
        closeNotificationsPopover();
        openWorkspacePopover();
    }
}

function workspaceColumnLabel(entry) {
    const page = (entry && entry.page) || '';
    try {
        const url = new URL(page, window.location.origin);
        const title = PAGE_TITLES[url.pathname] || (url.pathname.replace(/^\//, '').replace(/\.html$/, '') || 'Pane');
        const cid = url.searchParams.get('chat') || url.searchParams.get('session')
            || (entry && (entry.chat || entry.terminal));
        if (cid) {
            const raw = String(cid);
            const display = /^\d+$/.test(raw) ? ('CH-' + raw.padStart(6, '0')) : raw;
            return title + ' ' + display;
        }
        return title;
    } catch (_) {
        return page || 'Pane';
    }
}

function formatWorkspaceSummary(columns) {
    const cols = Array.isArray(columns) ? columns : [];
    const n = Math.max(1, cols.length);
    const paneWord = n === 1 ? 'pane' : 'panes';
    const labels = cols.map(workspaceColumnLabel).filter(Boolean);
    return n + ' ' + paneWord + (labels.length ? (' · ' + labels.join(' · ')) : '');
}

function formatWorkspaceTime(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return '';
    try {
        return new Date(n * 1000).toLocaleString(undefined, {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        });
    } catch (_) {
        return '';
    }
}

async function fetchWorkspaces() {
    const res = await fetch('/api/shell/workspaces', { cache: 'no-store', credentials: 'same-origin' });
    if (res.status === 404) return [];
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.error || 'Could not load workspaces');
    return Array.isArray(data.workspaces) ? data.workspaces : [];
}

const DEFAULT_WORKSPACE_ID = '__default__';

function getBuiltinDefaultWorkspace() {
    return {
        id: DEFAULT_WORKSPACE_ID,
        name: 'Default',
        summary: '1 pane · single viewport',
        builtin: true,
        columns: [{ page: '/chat_page.html' }],
        orientation: 'horizontal',
        root: { type: 'leaf', page: '/chat_page.html', flex: '' },
    };
}

/** Collapse the shell to a single full-bleed viewport (built-in Default workspace). */
function applyDefaultWorkspace() {
    removeExtraSplitColumnsNow();
    if (splitContainer) {
        splitContainer.querySelectorAll(':scope > .split-resize-handle').forEach((h) => {
            try { h.remove(); } catch (_) {}
        });
        splitContainer.querySelectorAll(':scope > .split-group').forEach((g) => {
            discardSplitSubtree(g);
        });
        applyGroupOrientation(splitContainer, 'horizontal');
    }
    sanitizeSplitDom();
    const cols = getSplitColumns();
    cols.forEach((c) => {
        c.style.flex = '';
        clearPaneBoxStyles(c);
    });
    remumberSplitColumns();
    persistSplitLayout();
    closeWorkspacePopover();
    if (typeof window.showToast === 'function') {
        window.showToast('Loaded Default · 1 pane', 'success');
    }
    return true;
}

function renderWorkspaceList(workspaces) {
    const listEl = document.getElementById('workspaceList');
    const emptyEl = document.getElementById('workspaceEmpty');
    if (!listEl || !emptyEl) return;
    const saved = Array.isArray(workspaces) ? workspaces : [];
    const items = [getBuiltinDefaultWorkspace()].concat(
        saved.filter((ws) => String(ws?.id || '') !== DEFAULT_WORKSPACE_ID
            && String(ws?.name || '').trim().toLowerCase() !== 'default')
    );
    emptyEl.style.display = 'none';
    listEl.innerHTML = items.map((ws) => {
        const id = escapeHtml(String(ws.id || ''));
        const name = escapeHtml(String(ws.name || 'Workspace'));
        const summary = escapeHtml(ws.summary || formatWorkspaceSummary(ws.columns));
        const when = ws.builtin ? '' : escapeHtml(formatWorkspaceTime(ws.updated_at));
        const badge = ws.builtin ? '<span class="workspace-item-badge">built-in</span>' : '';
        const deleteBtn = ws.builtin
            ? ''
            : '<button type="button" class="workspace-btn workspace-btn-danger" data-workspace-delete>Delete</button>';
        return '<div class="workspace-item' + (ws.builtin ? ' workspace-item-builtin' : '')
            + '" data-workspace-id="' + id + '"'
            + (ws.builtin ? ' data-workspace-builtin="1"' : '') + '>'
            + '<div class="workspace-item-main">'
            + '<div class="workspace-item-name">' + name + badge + (when ? ' · ' + when : '') + '</div>'
            + '<div class="workspace-item-summary">' + summary + '</div>'
            + '</div>'
            + '<div class="workspace-item-actions">'
            + '<button type="button" class="workspace-btn workspace-btn-primary" data-workspace-load>Load</button>'
            + deleteBtn
            + '</div></div>';
    }).join('');
    listEl.querySelectorAll('[data-workspace-load]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.workspace-item');
            const id = row?.dataset.workspaceId;
            if (id === DEFAULT_WORKSPACE_ID || row?.dataset.workspaceBuiltin === '1') {
                applyDefaultWorkspace();
                return;
            }
            const ws = saved.find((x) => String(x.id) === String(id));
            if (ws) loadWorkspace(ws);
        });
    });
    listEl.querySelectorAll('[data-workspace-delete]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const row = btn.closest('.workspace-item');
            if (row?.dataset.workspaceBuiltin === '1') return;
            const id = row?.dataset.workspaceId;
            if (id) deleteWorkspace(id);
        });
    });
}

async function refreshWorkspacePopover() {
    const summaryEl = document.getElementById('workspaceCurrentSummary');
    if (summaryEl) summaryEl.textContent = formatWorkspaceSummary(snapshotSplitColumns());
    try {
        renderWorkspaceList(await fetchWorkspaces());
    } catch (err) {
        // Still show the built-in Default entry when the API is unreachable.
        renderWorkspaceList([]);
        const emptyEl = document.getElementById('workspaceEmpty');
        if (emptyEl && err) {
            emptyEl.style.display = 'block';
            emptyEl.textContent = (err && err.message) || 'Could not load workspaces';
        }
    }
}

async function saveCurrentWorkspace() {
    const input = document.getElementById('workspaceNameInput');
    const btn = document.getElementById('workspaceSaveBtn');
    const name = ((input && input.value) || 'Workspace').trim() || 'Workspace';
    if (name.toLowerCase() === 'default') {
        if (typeof window.showToast === 'function') {
            window.showToast('Default is built-in and cannot be overwritten', 'info');
        }
        return;
    }
    const columns = snapshotSplitColumns();
    if (btn) btn.disabled = true;
    try {
        const res = await fetch('/api/shell/workspaces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({
                name,
                columns,
                orientation: primaryOrientationFromTree(snapshotLayoutTree(splitContainer)),
                root: snapshotLayoutTree(splitContainer),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error(data.error || 'Save failed');
        if (typeof window.showToast === 'function') {
            window.showToast('Saved workspace · ' + formatWorkspaceSummary(columns), 'success');
        }
        renderWorkspaceList(data.workspaces || []);
    } catch (err) {
        if (typeof window.showToast === 'function') {
            window.showToast((err && err.message) || 'Save failed', 'error');
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function loadWorkspace(ws) {
    if (ws && (ws.builtin || String(ws.id) === DEFAULT_WORKSPACE_ID)) {
        applyDefaultWorkspace();
        return;
    }
    const columns = ws && ws.columns;
    if (!applyWorkspaceColumns(columns, ws && ws.orientation, ws && ws.root)) {
        if (typeof window.showToast === 'function') window.showToast('Could not load workspace', 'error');
        return;
    }
    closeWorkspacePopover();
    if (typeof window.showToast === 'function') {
        window.showToast('Loaded ' + (ws.name || 'workspace') + ' · ' + formatWorkspaceSummary(columns), 'success');
    }
}

async function deleteWorkspace(id) {
    if (String(id) === DEFAULT_WORKSPACE_ID) return;
    try {
        const res = await fetch('/api/shell/workspaces/' + encodeURIComponent(id), {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error(data.error || 'Delete failed');
        renderWorkspaceList(data.workspaces || []);
        if (typeof window.showToast === 'function') window.showToast('Workspace deleted', 'info');
    } catch (err) {
        if (typeof window.showToast === 'function') {
            window.showToast((err && err.message) || 'Delete failed', 'error');
        }
    }
}

(function setupWorkspacePopover() {
    document.getElementById('workspaceSaveBtn')?.addEventListener('click', () => saveCurrentWorkspace());
    document.getElementById('workspaceNameInput')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveCurrentWorkspace();
        }
    });
})();

// ── Space groups (Chrome-style tab groups) ─────────────
// A group is a named, colored bucket of spaces. Tabs keep working exactly as
// before; members render under a group pill and share the group color.
// ── Space groups (Chrome-style tab groups) ─────────────
// A group is a named, colored bucket of spaces. Tabs keep working exactly as
// before; members render under a group pill and share the group color.
// Domain logic lives in spaces_groups.js (pure state transitions on
// spacesState); the wrappers below bind the singleton and own the
// persist + re-render tail. Palette + ids live in spaces_state.js.
function spaceGroupById(gid) {
    return CuttleSpaces.getGroupById(spacesState, gid);
}

function ensureSpaceGroupsArray() {
    return CuttleSpaces.ensureGroupsArray(spacesState);
}

/** Drop groups with no member tabs (runs after ungroup/close/delete). */
function pruneEmptySpaceGroups() {
    if (CuttleSpaces.pruneEmptyGroups(spacesState)) persistSpacesState();
}

function setSpaceGroup(spaceId, gid) {
    if (!CuttleSpaces.setGroup(spacesState, spaceId, gid)) return false;
    pruneEmptySpaceGroups();
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

function createSpaceGroupForSpace(spaceId) {
    const group = CuttleSpaces.createGroupForSpace(spacesState, spaceId);
    if (!group) return null;
    persistSpacesState();
    renderSpaceTabs();
    return group;
}

function removeSpaceFromGroup(spaceId) {
    if (!CuttleSpaces.removeFromGroup(spacesState, spaceId)) return false;
    pruneEmptySpaceGroups();
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

function renameSpaceGroup(gid, name) {
    if (!CuttleSpaces.renameGroup(spacesState, gid, name)) return;
    persistSpacesState();
}

function setSpaceGroupColor(gid, hex) {
    if (!CuttleSpaces.setGroupColor(spacesState, gid, hex)) return false;
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

function setSpaceColor(spaceId, hex) {
    if (!CuttleSpaces.setSpaceColor(spacesState, spaceId, hex)) return false;
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

/** Ungroup: dissolve the group, member spaces survive as plain tabs. */
function dissolveSpaceGroup(gid) {
    if (!CuttleSpaces.dissolveGroup(spacesState, gid)) return false;
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

/** Collapse/expand a group. Never hides the active tab. */
function toggleSpaceGroupCollapsed(gid) {
    const res = CuttleSpaces.toggleGroupCollapsed(spacesState, gid, spacesState.active);
    if (!res.ok) {
        if (res.reason === 'hides-active' && typeof window.showToast === 'function') {
            window.showToast('Active space is in this group', 'info');
        }
        return false;
    }
    persistSpacesState();
    renderSpaceTabs();
    return true;
}

/** Delete group: dissolve it and close its member spaces (at least one space survives). */
function deleteSpaceGroupWithSpaces(gid) {
    const members = CuttleSpaces.planGroupDelete(spacesState, gid);
    if (!members) return false;
    persistSpacesState();
    renderSpaceTabs();
    // Close non-active members first so the active space is the last one standing.
    members.sort((a, b) => (a === spacesState.active ? 1 : 0) - (b === spacesState.active ? 1 : 0));
    members.forEach((id) => closeSpace(id));
    pruneEmptySpaceGroups();
    return true;
}

function spaceGroupPillHtml(group) {
    const color = CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR;
    const name = escapeHtml(group.name || '');
    const collapsed = !!group.collapsed;
    const members = spacesState.spaces.filter((s) => s && s.groupId === group.id).length;
    const title = (group.name ? 'Group: ' + name : 'Group')
        + ' — click to ' + (collapsed ? 'expand' : 'collapse') + ', right-click to edit';
    return '<button type="button" class="shell-space-group' + (collapsed ? ' is-collapsed' : '') + '"'
        + ' data-group-id="' + escapeHtml(group.id) + '"'
        + ' style="--space-accent:' + color + '"'
        + ' title="' + title + '">'
        + '<span class="shell-space-group-dot" aria-hidden="true"></span>'
        + (group.name ? '<span class="shell-space-group-name">' + name + '</span>' : '')
        + (collapsed ? '<span class="shell-space-group-count">' + members + '</span>' : '')
        + '</button>';
}

// ── Spaces (titlebar tabs — each holds its own split layout) ────
// Per-device, like STORAGE_LAYOUT. The active space's layout *is* the live
// STORAGE_LAYOUT; `root` is only authoritative for inactive spaces.
// Singleton storage binding lives here (shell orchestration); validation,
// ids, names, and transitions live in spaces_state.js.
const STORAGE_SPACES = 'shell_spaces_v1';

function readSpacesState() {
    try {
        return CuttleSpaces.loadSpacesState(
            typeof localStorage !== 'undefined' ? localStorage : null
        );
    } catch (_) {
        return CuttleSpaces.createDefaultState();
    }
}

function persistSpacesState() {
    CuttleSpaces.saveSpacesState(
        typeof localStorage !== 'undefined' ? localStorage : null,
        spacesState
    );
}

function switchSpace(id) {
    if (_restoringLayout || id === spacesState.active) return;
    const target = spacesState.spaces.find((s) => s.id === id);
    if (!target) return;
    const targetGroup = target.groupId ? spaceGroupById(target.groupId) : null;
    if (targetGroup && targetGroup.collapsed) {
        targetGroup.collapsed = false;
        persistSpacesState();
        renderSpaceTabs();
    }
    const current = spacesState.spaces.find((s) => s.id === spacesState.active);
    if (current) current.root = snapshotLayoutTree(splitContainer);
    spacesState.active = id;
    persistSpacesState();
    // navigate() re-attaches column 0's remembered chat — without this a blank
    // pane in the target space would reopen the previous space's chat.
    lastChatByColumn.delete(0);
    persistLastChatHandles();
    // Even identical URLs belong to different spaces and must restore that
    // space's composer. Reuse the frame element through the navigation path.
    applyWorkspaceColumns(null, null, target.root || getBuiltinDefaultWorkspace().root, { forceNavigate: true });
    hideOutgoingSpaceFrames();
    // Already-loaded frames that just became visible need a rescan now;
    // navigating frames get theirs on cuttle-page-ready.
    broadcastUsageLiveWake();
    syncActiveSpaceTab();
    syncSpaceActivityTabs();
    scheduleSpaceActivityPoll(true);
}

/** A reused pane crossfades from its old iframe once the new page paints —
 *  right for page nav, but on a space switch the old space must vanish now. */
function hideOutgoingSpaceFrames() {
    document.querySelectorAll('.split-column .shell-main').forEach((mainEl) => {
        const frames = Array.from(mainEl.querySelectorAll('iframe'));
        if (!frames.some((f) => f.classList.contains('shell-frame-pending'))) return;
        frames.forEach((f) => {
            if (f.classList.contains('shell-frame-pending')) return;
            f.style.transition = 'none';
            f.style.opacity = '0';
            f.style.visibility = 'hidden';
            f.style.pointerEvents = 'none';
        });
    });
}

function addSpace() {
    const space = CuttleSpaces.addSpaceToState(spacesState);
    renderSpaceTabs();
    switchSpace(space.id);
    startSpaceRename(space.id);
}

function closeSpace(id) {
    const plan = CuttleSpaces.planSpaceClose(spacesState, id);
    if (!plan.ok) return;
    if (plan.switchTo) {
        switchSpace(plan.switchTo);
        if (spacesState.active === id) return;
    }
    CuttleSpaces.commitSpaceClose(spacesState, id);
    pruneEmptySpaceGroups();
    persistSpacesState();
    renderSpaceTabs();
    scheduleSpaceActivityPoll(true);
}

function renameSpace(id, name) {
    if (!CuttleSpaces.renameSpaceInState(spacesState, id, name)) return;
    persistSpacesState();
}

function startSpaceRename(id) {
    const tab = document.querySelector('.shell-space-tab[data-space-id="' + CSS.escape(id) + '"]');
    const nameEl = tab && tab.querySelector('.shell-space-name');
    const space = spacesState.spaces.find((s) => s.id === id);
    if (!nameEl || !space) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'shell-space-rename';
    input.maxLength = 60;
    input.value = space.name;
    input.setAttribute('aria-label', 'Space name');
    let done = false;
    const finish = (commit) => {
        if (done) return;
        done = true;
        if (commit) renameSpace(id, input.value);
        renderSpaceTabs();
        // The rename pinned the fullscreen titlebar; re-arm its auto-hide.
        if (typeof cuttleTitlebarRescheduleHide === 'function') {
            try { cuttleTitlebarRescheduleHide(); } catch (_) {}
        }
    };
    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
    ['click', 'dblclick', 'mousedown'].forEach((evt) => {
        input.addEventListener(evt, (e) => e.stopPropagation());
    });
    nameEl.replaceWith(input);
    input.focus();
    input.select();
}

function renderSpaceTabs() {
    const host = document.getElementById('shellSpacesTabs');
    if (!host) return;
    host.dataset.count = String(spacesState.spaces.length);
    // Grouped runs render inside a shared sleeve so the group reads as one
    // container; a pill opens every run (members may be split by dragging).
    let html = '';
    let openGroup = null;
    const closeSleeve = () => { if (openGroup) { html += '</span>'; openGroup = null; } };
    spacesState.spaces.forEach((s) => {
        const group = s.groupId ? spaceGroupById(s.groupId) : null;
        const gid = group ? group.id : null;
        if (gid !== openGroup) {
            closeSleeve();
            if (group) {
                const color = CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR;
                openGroup = gid;
                html += '<span class="shell-space-group-sleeve'
                    + (group.collapsed ? ' is-collapsed' : '') + '" role="presentation"'
                    + ' data-group-id="' + escapeHtml(gid) + '"'
                    + ' style="--space-accent:' + color + '">';
                html += spaceGroupPillHtml(group);
            }
        }
        if (group && group.collapsed) return; // hidden until the pill expands it
        const accent = group
            ? (CuttleSpaces.sanitizeColor(group.color) || CuttleSpaces.DEFAULT_COLOR)
            : CuttleSpaces.sanitizeColor(s.color);
        const active = s.id === spacesState.active;
        const name = escapeHtml(s.name).replace(/"/g, '&quot;');
        html += '<div class="shell-space-tab' + (active ? ' is-active' : '') + (accent ? ' has-accent' : '') + '" role="tab" tabindex="0"'
            + ' aria-selected="' + (active ? 'true' : 'false') + '"'
            + ' data-space-id="' + escapeHtml(s.id) + '"'
            + (group ? ' data-group-id="' + escapeHtml(group.id) + '"' : '')
            + (accent ? ' style="--space-accent:' + accent + '"' : '')
            + ' title="' + name + ' — drag to reorder, double-click to rename">'
            + '<span class="shell-space-activity" aria-hidden="true" hidden></span>'
            + '<span class="shell-space-name">' + name + '</span>'
            + '<button type="button" class="shell-space-close" tabindex="-1" aria-label="Close space ' + name + '">×</button>'
            + '</div>';
    });
    closeSleeve();
    host.innerHTML = html;
    syncActiveSpaceTab();
    syncSpaceActivityTabs();
    syncCrossSpaceGripVisibility();
}

/** Toggle the active tab in place — re-rendering would eat the second click of a dblclick. */
function syncActiveSpaceTab() {
    document.querySelectorAll('.shell-space-tab').forEach((tab) => {
        const active = tab.dataset.spaceId === spacesState.active;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        if (active) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    });
}

// ── Spaces activity (one indicator per tab) ─────────────────────
// Same states as chat history/title dots: input (blue), running (spinner),
// error (red), unread (green), queued/active (orange), paused (yellow).
// A space shows the highest-priority state across its chats:
// input > running > error > unread > queued > paused > none.
// Aggregation + snapshot map live in spaces_activity.js; the poll transport
// and DOM patching below stay in the shell.
let spaceActivityPollInFlight = false;
let spaceActivityLastPollAt = 0;
let spaceActivitySig = '';

function lookupSpaceSessionActivity(sid) {
    return CuttleSpaces.lookupSessionActivity(sid);
}

/** Forget snapshots from chat frames that were replaced or closed. */
function pruneSpaceActivitySources() {
    const live = new Set();
    document.querySelectorAll('iframe').forEach((fr) => {
        try { if (fr.contentWindow) live.add(fr.contentWindow); } catch (_) {}
    });
    CuttleSpaces.pruneSources((win) => live.has(win));
}

/** Chat session ids belonging to a space. Active space reads live panes;
 *  inactive spaces parse their stored layout tree. Terminals have no dots. */
function spaceChatIds(space) {
    if (!space) return [];
    if (space.id === spacesState.active) {
        try {
            return getOpenPaneSessions()
                .filter((p) => p && p.kind === 'chat' && p.sessionId)
                .map((p) => String(p.sessionId));
        } catch (_) {
            return [];
        }
    }
    const ids = [];
    try {
        flattenLayoutLeaves(space.root || null).forEach((leaf) => {
            if (!leaf) return;
            try {
                const url = new URL(leaf.page || '/chat_page.html', window.location.origin);
                // Terminals have no activity dots — only chat surfaces count.
                if (chatSurfaceKind(url.pathname) !== 'chat') return;
                const handle = url.searchParams.get('chat') || url.searchParams.get('session')
                    || leaf.chat || null;
                if (handle) ids.push(String(handle));
            } catch (_) {}
        });
    } catch (_) {}
    return [...new Set(ids)];
}

/** Currently visible chat ids — unread there is already seen, so it never
 *  raises the active tab (running / queued dots still do). */
function visibleActiveSpaceChatIds() {
    try {
        return new Set(
            getOpenPaneSessions()
                .filter((p) => p && p.kind === 'chat' && p.sessionId)
                .map((p) => String(p.sessionId))
        );
    } catch (_) {
        return new Set();
    }
}

function spaceActivityFor(space) {
    const ids = spaceChatIds(space);
    if (!ids.length) return '';
    const isActive = space.id === spacesState.active;
    const visible = isActive ? visibleActiveSpaceChatIds() : new Set();
    return CuttleSpaces.selectSpaceActivity(ids, lookupSpaceSessionActivity, isActive, visible);
}

/** Patch tab dots in place (no re-render — preserves dblclick rename). */
function syncSpaceActivityTabs() {
    const tabs = document.querySelectorAll('.shell-space-tab');
    if (!tabs.length) return;
    pruneSpaceActivitySources();
    const sigParts = [];
    tabs.forEach((tab) => {
        const space = spacesState.spaces.find((s) => s.id === tab.dataset.spaceId);
        const kind = space ? spaceActivityFor(space) : '';
        sigParts.push(tab.dataset.spaceId + ':' + kind);
        let dot = tab.querySelector('.shell-space-activity');
        if (!dot) {
            dot = document.createElement('span');
            dot.className = 'shell-space-activity';
            dot.setAttribute('aria-hidden', 'true');
            tab.insertBefore(dot, tab.firstChild);
        }
        const prev = dot.dataset.kind || '';
        if (prev !== kind || dot.hidden === !!kind) {
            dot.dataset.kind = kind;
            dot.className = 'shell-space-activity' + (kind ? ' is-' + kind : '');
            if (kind) {
                dot.hidden = false;
                const label = CuttleSpaces.LABEL[kind] || kind;
                dot.title = label;
                dot.setAttribute('aria-label', label);
            } else {
                dot.hidden = true;
                dot.removeAttribute('title');
                dot.removeAttribute('aria-label');
            }
            const spaceName = space ? space.name : '';
            tab.title = spaceName + ' — drag to reorder, double-click to rename'
                + (kind ? ' · ' + (CuttleSpaces.LABEL[kind] || kind) : '');
        }
    });
    spaceActivitySig = sigParts.join('|');
}

function allSpaceChatIds() {
    const out = new Set();
    try {
        spacesState.spaces.forEach((space) => {
            spaceChatIds(space).forEach((id) => out.add(String(id)));
        });
    } catch (_) {}
    // Live panes may know fresher ids than the stored tree right after nav.
    try {
        getOpenPaneSessions().forEach((p) => {
            if (p && p.kind === 'chat' && p.sessionId) out.add(String(p.sessionId));
        });
    } catch (_) {}
    return [...out].filter(Boolean).slice(0, 60);
}

function readChatPrefsMap() {
    try {
        return JSON.parse(localStorage.getItem('cuttleChatSessionPrefs') || '{}') || {};
    } catch (_) {
        return {};
    }
}

function readLocalChatSessions() {
    try {
        return JSON.parse(localStorage.getItem('chatSessions') || '{}') || {};
    } catch (_) {
        return {};
    }
}

/** Unread flags + local-mode queues are same-origin localStorage. */
function refreshSpaceActivityLocalState() {
    try { CuttleSpaces.setUnreadPrefs(readChatPrefsMap()); } catch (_) {}
    try {
        const local = readLocalChatSessions();
        const queues = {};
        Object.keys(local || {}).forEach((sid) => {
            const obj = local[sid];
            if (!obj || typeof obj !== 'object') return;
            queues[sid] = obj.followup_queue != null ? obj.followup_queue : obj.followups;
        });
        CuttleSpaces.setLocalQueues(queues);
    } catch (_) {}
}

/** A chat in a background space finished with no frame watching it — flag
 *  it unread (same prefs row the history panel reads) so its tab goes green. */
function markFinishedBackgroundChatsUnread(finished) {
    if (!finished || !finished.length) return;
    const visible = new Set([...visibleActiveSpaceChatIds()].map((s) => CuttleSpaces.bareSid(s)));
    const targets = finished.filter((bare) => bare && !visible.has(bare));
    if (!targets.length) return;
    try {
        const prefs = readChatPrefsMap();
        let changed = false;
        targets.forEach((bare) => {
            const key = Object.keys(prefs).find((k) => CuttleSpaces.bareSid(k) === bare) || bare;
            const row = prefs[key] && typeof prefs[key] === 'object' ? prefs[key] : {};
            if (row.hasUnread) return;
            prefs[key] = Object.assign({}, row, { hasUnread: true, unreadIsError: false });
            changed = true;
        });
        if (changed) localStorage.setItem('cuttleChatSessionPrefs', JSON.stringify(prefs));
    } catch (_) {}
}

async function fetchSpaceSessionsList() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6000);
    try {
        const r = await fetch('/api/auth/sessions', {
            cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
        });
        if (!r.ok) return null;
        const data = await r.json().catch(() => null);
        return data && data.success && Array.isArray(data.sessions) ? data.sessions : null;
    } catch (_) {
        return null;
    } finally {
        clearTimeout(timeout);
    }
}

/** Not authenticated — live-status knows only `generating`. */
async function fetchSpaceLiveStatusRows(ids) {
    const rows = [];
    for (let i = 0; i < ids.length; i += 12) {
        const chunk = ids.slice(i, i + 12);
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const r = await fetch(
                '/api/chat-live-status-batch?session_ids=' + encodeURIComponent(chunk.join(',')),
                { credentials: 'include', cache: 'no-store', signal: controller.signal }
            );
            if (!r.ok) continue;
            const data = await r.json().catch(() => null);
            if (!data || !data.success) continue;
            const statuses = data.statuses || {};
            chunk.forEach((sid) => {
                const st = statuses[String(sid)] || statuses[CuttleSpaces.bareSid(sid)] || null;
                rows.push({ id: sid, running: !!(st && st.generating), queue: null });
            });
        } catch (_) {
        } finally {
            clearTimeout(timeout);
        }
    }
    return rows;
}

/** Server truth for every chat in every space (inactive spaces have no
 *  live frame). Frame pushes add immediacy between polls. */
async function refreshSpaceActivityFromServer() {
    if (spaceActivityPollInFlight) return;
    refreshSpaceActivityLocalState();
    const ids = allSpaceChatIds();
    if (!ids.length) {
        syncSpaceActivityTabs();
        return;
    }
    spaceActivityPollInFlight = true;
    const startedAt = Date.now();
    try {
        let rows = null;
        const sessions = await fetchSpaceSessionsList();
        if (sessions) {
            const byBare = new Map();
            sessions.forEach((s) => {
                if (s && s.id != null) byBare.set(CuttleSpaces.bareSid(s.id), s);
            });
            rows = [];
            ids.forEach((sid) => {
                const s = byBare.get(CuttleSpaces.bareSid(sid));
                if (!s) return;
                rows.push({
                    id: sid,
                    running: !!(s.generating || s.awaiting_action),
                    queue: CuttleSpaces.followupKind(parseSpaceFollowupQueue(
                        s.followup_queue != null ? s.followup_queue : s.followups)),
                });
            });
        } else {
            rows = await fetchSpaceLiveStatusRows(ids);
        }
        markFinishedBackgroundChatsUnread(CuttleSpaces.noteServerSnapshot(rows, startedAt));
        refreshSpaceActivityLocalState();
    } finally {
        spaceActivityPollInFlight = false;
        spaceActivityLastPollAt = Date.now();
        syncSpaceActivityTabs();
    }
}

/** followup_queue arrives as an array or a JSON string. */
function parseSpaceFollowupQueue(raw) {
    if (Array.isArray(raw)) return raw;
    if (typeof raw === 'string' && raw.trim()) {
        try {
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }
    return [];
}

function scheduleSpaceActivityPoll(immediate) {
    const since = Date.now() - spaceActivityLastPollAt;
    if (!immediate && since < 8000) {
        refreshSpaceActivityLocalState();
        syncSpaceActivityTabs();
        return;
    }
    refreshSpaceActivityFromServer();
}

// ── Space tab right-click menu + group editor bubble ────
let spaceCtxEls = [];
let spaceCtxDismiss = null;

/** True while a space menu, submenu, or group bubble is on screen. */
function isSpaceCtxOpen() {
    return spaceCtxEls.length > 0;
}

function closeSpaceCtxLayers() {
    if (spaceCtxDismiss) {
        try { spaceCtxDismiss(); } catch (_) {}
        spaceCtxDismiss = null;
    }
    spaceCtxEls.forEach((el) => { try { el.remove(); } catch (_) {} });
    spaceCtxEls = [];
    // The open layers pinned the fullscreen titlebar; re-arm its auto-hide now
    // that they are gone (no-op unless fullscreen and the pointer is away).
    if (typeof cuttleTitlebarRescheduleHide === 'function') {
        try { cuttleTitlebarRescheduleHide(); } catch (_) {}
    }
}

function trackSpaceCtxDismiss() {
    if (spaceCtxDismiss) {
        try { spaceCtxDismiss(); } catch (_) {}
        spaceCtxDismiss = null;
    }
    // Layers opening pin the titlebar: drop any hide another close just armed.
    if (typeof cuttleTitlebarCancelHide === 'function') {
        try { cuttleTitlebarCancelHide(); } catch (_) {}
    }
    const onDown = (e) => {
        if (e.target && e.target.closest && e.target.closest('.shell-space-ctx, .shell-group-bubble')) return;
        closeSpaceCtxLayers();
    };
    const onKey = (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); closeSpaceCtxLayers(); }
    };
    const onHide = () => closeSpaceCtxLayers();
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('scroll', onHide, true);
    window.addEventListener('resize', onHide);
    window.addEventListener('blur', onHide);
    spaceCtxDismiss = () => {
        document.removeEventListener('pointerdown', onDown, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('scroll', onHide, true);
        window.removeEventListener('resize', onHide);
        window.removeEventListener('blur', onHide);
    };
}

/** Append a fixed panel, measure it, then clamp it inside the viewport. */
function placeFixedPanel(el, x, y) {
    el.style.left = '0px';
    el.style.top = '0px';
    document.body.appendChild(el);
    spaceCtxEls.push(el);
    const rect = el.getBoundingClientRect();
    const pad = 8;
    let nx = Number(x) || 0;
    let ny = Number(y) || 0;
    if (nx + rect.width + pad > window.innerWidth) nx = Math.max(pad, window.innerWidth - rect.width - pad);
    if (ny + rect.height + pad > window.innerHeight) ny = Math.max(pad, window.innerHeight - rect.height - pad);
    el.style.left = Math.max(pad, nx) + 'px';
    el.style.top = Math.max(pad, ny) + 'px';
    return el;
}

function spaceCtxSwatchesHtml(selected) {
    return '<div class="shell-ctx-swatches" role="group" aria-label="Colors">'
        + CuttleSpaces.COLORS.map((c) => (
            '<button type="button" class="shell-ctx-swatch' + (selected === c.hex ? ' is-selected' : '') + '"'
            + ' data-color="' + c.hex + '"'
            + ' style="--swatch:' + c.hex + '"'
            + ' title="' + c.name + '" aria-label="' + c.name + '"></button>'
        )).join('')
        + '</div>';
}

function openSpaceTabMenu(x, y, spaceId) {
    const space = spacesState.spaces.find((s) => s && s.id === spaceId);
    if (!space) return;
    closeSpaceCtxLayers();
    trackSpaceCtxDismiss();
    const menu = document.createElement('div');
    menu.className = 'shell-space-ctx';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', 'Space options');
    let html = '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="rename"><span>Rename</span></button>'
        + '<button type="button" class="shell-space-ctx-item has-sub" role="menuitem" data-ctx-act="color" aria-haspopup="menu"><span>Set color</span></button>'
        + '<button type="button" class="shell-space-ctx-item has-sub" role="menuitem" data-ctx-act="group" aria-haspopup="menu"><span>Add to group</span></button>';
    if (space.groupId && spaceGroupById(space.groupId)) {
        html += '<div class="shell-space-ctx-sep" aria-hidden="true"></div>'
            + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-ctx-act="remove-group"><span>Remove from group</span></button>';
    }
    menu.innerHTML = html;
    placeFixedPanel(menu, x, y);

    let openSub = null;
    let subFor = null;
    const closeOpenSub = () => {
        if (openSub) {
            try { openSub.remove(); } catch (_) {}
            spaceCtxEls = spaceCtxEls.filter((el) => el !== openSub);
            openSub = null;
        }
        subFor = null;
        menu.querySelectorAll('.is-open').forEach((b) => b.classList.remove('is-open'));
    };
    const openSubFor = (btn, build) => {
        if (subFor === btn && openSub) {
            closeOpenSub();
            return;
        }
        closeOpenSub();
        subFor = btn;
        btn.classList.add('is-open');
        const sub = build();
        const r = btn.getBoundingClientRect();
        placeFixedPanel(sub, r.right - 2, r.top - 4);
        openSub = sub;
    };
    const buildColorSub = () => {
        const sub = document.createElement('div');
        sub.className = 'shell-space-ctx shell-space-ctx-sub';
        sub.setAttribute('role', 'menu');
        sub.setAttribute('aria-label', 'Space color');
        sub.innerHTML = spaceCtxSwatchesHtml(space.color || null)
            + '<button type="button" class="shell-space-ctx-item" role="menuitem" data-color="">'
            + '<span>None</span></button>';
        sub.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-color]');
            if (!btn || !sub.isConnected) return;
            e.preventDefault();
            e.stopPropagation();
            const hex = btn.dataset.color || '';
            const id = spaceId;
            closeSpaceCtxLayers();
            setSpaceColor(id, hex || null);
        });
        return sub;
    };
    const buildGroupSub = () => {
        const sub = document.createElement('div');
        sub.className = 'shell-space-ctx shell-space-ctx-sub';
        sub.setAttribute('role', 'menu');
        sub.setAttribute('aria-label', 'Add to group');
        const groups = ensureSpaceGroupsArray();
        let h = '<button type="button" class="shell-space-ctx-item" role="menuitem" data-new-group>'
            + '<span>New group</span></button>';
        if (groups.length) h += '<div class="shell-space-ctx-sep" aria-hidden="true"></div>';
        groups.forEach((g) => {
            if (!g) return;
            const color = CuttleSpaces.sanitizeColor(g.color) || CuttleSpaces.DEFAULT_COLOR;
            h += '<button type="button" class="shell-space-ctx-item" role="menuitem" data-gid="' + escapeHtml(g.id) + '">'
                + '<span class="shell-ctx-dot" style="--swatch:' + color + '" aria-hidden="true"></span>'
                + '<span class="shell-ctx-label">' + (escapeHtml(g.name) || 'Unnamed group') + '</span>'
                + (space.groupId === g.id ? '<span class="shell-ctx-check" aria-hidden="true">✓</span>' : '')
                + '</button>';
        });
        sub.innerHTML = h;
        sub.addEventListener('click', (e) => {
            const id = spaceId;
            if (e.target.closest('[data-new-group]')) {
                e.preventDefault();
                e.stopPropagation();
                closeSpaceCtxLayers();
                const group = createSpaceGroupForSpace(id);
                if (group) openSpaceGroupBubbleFor(group.id, id, true);
                return;
            }
            const btn = e.target.closest('[data-gid]');
            if (!btn || !sub.isConnected) return;
            e.preventDefault();
            e.stopPropagation();
            closeSpaceCtxLayers();
            setSpaceGroup(id, btn.dataset.gid || null);
        });
        return sub;
    };

    menu.querySelectorAll('[data-ctx-act]').forEach((btn) => {
        btn.addEventListener('pointerenter', () => {
            const act = btn.dataset.ctxAct;
            if (act === 'color') openSubFor(btn, buildColorSub);
            else if (act === 'group') openSubFor(btn, buildGroupSub);
            else closeOpenSub();
        });
        btn.addEventListener('click', (e) => {
            const act = btn.dataset.ctxAct;
            const id = spaceId;
            if (act === 'color') { e.stopPropagation(); openSubFor(btn, buildColorSub); return; }
            if (act === 'group') { e.stopPropagation(); openSubFor(btn, buildGroupSub); return; }
            e.preventDefault();
            e.stopPropagation();
            closeSpaceCtxLayers();
            if (act === 'rename') startSpaceRename(id);
            else if (act === 'remove-group') removeSpaceFromGroup(id);
        });
    });
}

/** Chrome-style group editor: name field, color palette, Ungroup / Delete group. */
function openSpaceGroupBubbleFor(gid, anchorSpaceId, focusName) {
    const group = spaceGroupById(gid);
    if (!group) return;
    closeSpaceCtxLayers();
    trackSpaceCtxDismiss();
    const bubble = document.createElement('div');
    bubble.className = 'shell-group-bubble';
    bubble.setAttribute('role', 'dialog');
    bubble.setAttribute('aria-label', 'Edit group');
    bubble.innerHTML = '<input type="text" class="shell-group-name" maxlength="40"'
        + ' placeholder="Name group" aria-label="Group name"'
        + ' value="' + escapeHtml(group.name || '').replace(/"/g, '&quot;') + '">'
        + spaceCtxSwatchesHtml(group.color)
        + '<div class="shell-group-actions">'
        + '<button type="button" class="shell-group-btn" data-group-act="ungroup">Ungroup</button>'
        + '<button type="button" class="shell-group-btn shell-group-btn-danger" data-group-act="delete">Delete group</button>'
        + '</div>';
    renderSpaceTabs();
    const pill = document.querySelector('.shell-space-group[data-group-id="' + CSS.escape(gid) + '"]');
    if (pill) pill.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const anchor = pill
        || (anchorSpaceId && document.querySelector('.shell-space-tab[data-space-id="' + CSS.escape(anchorSpaceId) + '"]'));
    const r = anchor ? anchor.getBoundingClientRect() : { left: 8, bottom: 40 };
    placeFixedPanel(bubble, r.left, (r.bottom || 40) + 6);

    const input = bubble.querySelector('.shell-group-name');
    if (focusName && input) {
        input.focus();
        input.select();
    }
    input.addEventListener('input', () => {
        renameSpaceGroup(gid, input.value);
        renderSpaceTabs(); // pill text follows live; the bubble keeps focus
    });
    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); closeSpaceCtxLayers(); }
        else if (e.key === 'Escape') { e.preventDefault(); closeSpaceCtxLayers(); }
    });
    ['click', 'dblclick', 'mousedown', 'contextmenu'].forEach((evt) => {
        input.addEventListener(evt, (e) => e.stopPropagation());
    });
    let deleteArmed = false;
    bubble.addEventListener('click', (e) => {
        const swatch = e.target.closest('[data-color]');
        if (swatch) {
            e.stopPropagation();
            setSpaceGroupColor(gid, swatch.dataset.color || null);
            bubble.querySelectorAll('.shell-ctx-swatch').forEach((el) => {
                el.classList.toggle('is-selected', el.dataset.color === spaceGroupById(gid).color);
            });
            return;
        }
        const btn = e.target.closest('[data-group-act]');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        if (btn.dataset.groupAct === 'ungroup') {
            closeSpaceCtxLayers();
            dissolveSpaceGroup(gid);
            return;
        }
        if (btn.dataset.groupAct === 'delete') {
            if (!deleteArmed) {
                deleteArmed = true;
                btn.classList.add('is-armed');
                btn.textContent = 'Click again to confirm';
                return;
            }
            closeSpaceCtxLayers();
            deleteSpaceGroupWithSpaces(gid);
        }
    });
}

function setupSpaceTabMenus(host) {
    if (!host || host.dataset.spaceMenus === '1') return;
    host.dataset.spaceMenus = '1';
    host.addEventListener('click', (e) => {
        const pill = e.target.closest('.shell-space-group');
        if (pill && pill.dataset.groupId) {
            e.stopPropagation();
            toggleSpaceGroupCollapsed(pill.dataset.groupId);
        }
    });
    host.addEventListener('dblclick', (e) => {
        // Pills must not bubble to the titlebar maximize gesture.
        if (e.target.closest('.shell-space-group')) e.stopPropagation();
    });
    host.addEventListener('contextmenu', (e) => {
        const pill = e.target.closest('.shell-space-group');
        if (pill && pill.dataset.groupId) {
            e.preventDefault();
            e.stopPropagation();
            openSpaceGroupBubbleFor(pill.dataset.groupId, null, false);
            return;
        }
        const tab = e.target.closest('.shell-space-tab');
        if (!tab || !tab.dataset.spaceId) return;
        e.preventDefault();
        e.stopPropagation();
        openSpaceTabMenu(e.clientX, e.clientY, tab.dataset.spaceId);
    });
}

(function setupSpaces() {
    const host = document.getElementById('shellSpacesTabs');
    if (!host) return;
    persistSpacesState();
    renderSpaceTabs();

    const tabId = (e) => e.target.closest('.shell-space-tab')?.dataset.spaceId || '';
    host.addEventListener('click', (e) => {
        const id = tabId(e);
        if (!id) return;
        if (e.target.closest('.shell-space-close')) {
            e.stopPropagation();
            closeSpace(id);
            return;
        }
        switchSpace(id);
    });
    // Titlebar double-click maximizes the window; renaming must not bubble there.
    host.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        const id = tabId(e);
        if (id && !e.target.closest('.shell-space-close')) startSpaceRename(id);
    });
    host.addEventListener('auxclick', (e) => {
        const id = tabId(e);
        if (id && e.button === 1) {
            e.preventDefault();
            closeSpace(id);
        }
    });
    host.addEventListener('keydown', (e) => {
        const id = tabId(e);
        if (!id) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchSpace(id); }
        else if (e.key === 'F2') { e.preventDefault(); startSpaceRename(id); }
    });
    const addBtn = document.getElementById('shellSpacesAdd');
    addBtn?.addEventListener('click', () => addSpace());
    addBtn?.addEventListener('dblclick', (e) => e.stopPropagation());
    setupSpaceTabDrag(host);
    setupSpaceTabMenus(host);
    // Chat iframes write unread prefs + followups to the same localStorage —
    // refresh dots without waiting for the next heartbeat.
    window.addEventListener('storage', (e) => {
        if (!e || !e.key) return;
        if (e.key === 'cuttleChatSessionPrefs' || e.key === 'chatSessions') {
            scheduleSpaceActivityPoll(false);
        }
    });
    scheduleSpaceActivityPoll(true);
})();

/**
 * Commit a tab-strip drop. Only visible tabs take part in the DOM order —
 * members of collapsed groups are hidden, so they keep their slots: the
 * dragged tab anchors after its left visible neighbor (or at the front).
 * The old all-or-nothing reorder silently dropped every commit while any
 * group was collapsed.
 */
function spaceById(id) {
    return CuttleSpaces.getSpaceById(spacesState, id);
}

// ── Cross-space pane drag (grip → space tab) ───────────────
// Quick release over a space tab moves the dragged viewport to the far right
// of that space (new horizontal split, placed at end). Holding over a tab for
// PANE_SPACE_HOVER_MS instead switches to that space so the still-held grip
// can be dropped on the exact pane to swap with.
const PANE_SPACE_HOVER_MS = 1000;

/** Space-tab element under a viewport point (titlebar hit-test during pane drags). */
function spaceTabAtPoint(x, y) {
    const tabs = document.querySelectorAll('.shell-space-tab');
    for (const tab of tabs) {
        const r = tab.getBoundingClientRect();
        if (x >= r.left && x < r.right && y >= r.top && y < r.bottom) return tab;
    }
    return null;
}

function blankLayoutLeafNode() {
    return { type: 'leaf', id: newLeafId(), page: '/chat_page.html', flex: '' };
}

/** Append a leaf copy as a far-right horizontal split; wraps when needed. */
function appendLayoutLeafToEnd(root, leaf) {
    const node = {
        type: 'leaf',
        id: (leaf && leaf.id) || newLeafId(),
        page: (leaf && leaf.page) || '/chat_page.html',
        flex: '',
    };
    if (leaf && leaf.chat) node.chat = String(leaf.chat);
    if (leaf && leaf.terminal) node.terminal = String(leaf.terminal);
    if (!root || typeof root !== 'object') {
        return {
            type: 'group', id: newGroupId(), orientation: 'horizontal', flex: '',
            children: [blankLayoutLeafNode(), node],
        };
    }
    if (root.type === 'group' && normalizeSplitOrientation(root.orientation) === 'horizontal'
        && Array.isArray(root.children)) {
        root.children.push(node);
        return root;
    }
    return {
        type: 'group', id: newGroupId(), orientation: 'horizontal', flex: '',
        children: [root, node],
    };
}

/** Depth-first leaf lookup by stored leaf id. */
function findLayoutLeafNode(root, leafId) {
    if (!root || typeof root !== 'object' || !leafId) return null;
    if (root.type === 'leaf') return root.id === leafId ? root : null;
    if (root.type === 'group' && Array.isArray(root.children)) {
        for (const ch of root.children) {
            const hit = findLayoutLeafNode(ch, leafId);
            if (hit) return hit;
        }
    }
    return null;
}

/** Remove a leaf by id, collapsing single-child groups. Returns { root, removed }. */
function removeLayoutLeafNode(root, leafId) {
    if (!root || typeof root !== 'object' || !leafId) return { root, removed: null };
    if (root.type === 'leaf') {
        return root.id === leafId ? { root: null, removed: root } : { root, removed: null };
    }
    if (root.type !== 'group' || !Array.isArray(root.children)) return { root, removed: null };
    let removed = null;
    const kept = [];
    for (const ch of root.children) {
        if (removed) { kept.push(ch); continue; }
        if (ch && ch.type === 'leaf' && ch.id === leafId) { removed = ch; continue; }
        const res = removeLayoutLeafNode(ch, leafId);
        if (res.removed) removed = res.removed;
        if (res.root) kept.push(res.root);
    }
    if (!removed) return { root, removed: null };
    if (kept.length === 0) return { root: null, removed };
    if (kept.length === 1) return { root: kept[0], removed };
    root.children = kept;
    return { root, removed };
}

/** Point a live pane at stored leaf content (page + chat/terminal handles). */
function applyStoredLeafToLivePane(colIdx, leaf) {
    if (!Number.isFinite(colIdx) || !leaf) return false;
    const col = getColumnEl(colIdx);
    if (!col) return false;
    const entry = {};
    if (leaf.chat) entry.chat = String(leaf.chat);
    if (leaf.terminal) entry.terminal = String(leaf.terminal);
    lastChatByColumn.delete(colIdx);
    if (Object.keys(entry).length) lastChatByColumn.set(colIdx, entry);
    const page = pageWithPaneSession(colIdx, canonicalizeShellPage(leaf.page || '/chat_page.html'));
    setState(colIdx, page);
    updateColumnUI(colIdx, page);
    const frame = replaceFrame(colIdx, page);
    if (frame) attachFrameLoadListener(colIdx, frame);
    return true;
}

/** Quick drop: move the dragged viewport to the far right of another space. */
function moveDraggedPaneToSpace(load, colEl, targetSpaceId) {
    if (!load || !targetSpaceId || targetSpaceId === spacesState.active) return false;
    const target = spaceById(targetSpaceId);
    if (!target) return false;
    target.root = appendLayoutLeafToEnd(target.root, load);
    persistSpacesState();
    renderSpaceTabs();
    const live = getSplitColumns();
    if (live.length >= 2 && colEl && colEl.isConnected) {
        closeSplitColumn(colEl);
    } else if (colEl && colEl.isConnected) {
        // Sole viewport moved away — leave a fresh chat behind, never an empty space.
        const idx = parseInt(colEl.dataset.column, 10);
        if (Number.isFinite(idx)) {
            lastChatByColumn.delete(idx);
            applyStoredLeafToLivePane(idx, blankLayoutLeafNode());
            persistLastChatHandles();
            persistSplitLayout();
        }
    }
    if (typeof window.showToast === 'function') {
        window.showToast('Moved pane to ' + (target.name || 'space'), 'success');
    }
    return true;
}

/** Chained drop while carrying: relocate the payload between stored spaces. */
function moveCarriedPaneToSpace(load, srcId, targetSpaceId) {
    if (!load || !targetSpaceId || targetSpaceId === spacesState.active) return false;
    const dst = spaceById(targetSpaceId);
    if (!dst || targetSpaceId === srcId) return false;
    const src = spaceById(srcId);
    if (src && src.root) {
        const res = removeLayoutLeafNode(src.root, load.id);
        if (res.removed) src.root = res.root || blankLayoutLeafNode();
    }
    dst.root = appendLayoutLeafToEnd(dst.root, load);
    persistSpacesState();
    renderSpaceTabs();
    if (typeof window.showToast === 'function') {
        window.showToast('Moved pane to ' + (dst.name || 'space'), 'success');
    }
    return true;
}

/** Drop on a live pane while carrying: swap the payload with that pane's content. */
function exchangeCarriedPaneWithLive(load, srcId, targetCol) {
    const src = spaceById(srcId);
    if (!src || !load || !targetCol || !targetCol.isConnected) return false;
    const idxT = parseInt(targetCol.dataset.column, 10);
    if (!Number.isFinite(idxT)) return false;
    const stateT = { ...getState(idxT) };
    const oldPage = pageWithPaneSession(idxT, canonicalizeShellPage(stateT.page || '/chat_page.html'));
    const oldHandles = { ...(lastChatByColumn.get(idxT) || {}) };
    if (!applyStoredLeafToLivePane(idxT, load)) return false;
    const slot = findLayoutLeafNode(src.root, load.id);
    if (slot) {
        slot.page = oldPage;
        delete slot.chat;
        delete slot.terminal;
        if (oldHandles.chat) slot.chat = String(oldHandles.chat);
        if (oldHandles.terminal) slot.terminal = String(oldHandles.terminal);
    }
    persistSpacesState();
    persistLastChatHandles();
    persistSplitLayout();
    focusedColumnIdx = idxT;
    broadcastPaneFocus();
    if (typeof window.showToast === 'function') {
        window.showToast('Swapped panes with ' + (src.name || 'space'), 'success');
    }
    return true;
}

/** Show the grip for cross-space drags even when this space has a single pane. */
function syncCrossSpaceGripVisibility() {
    if (!splitContainer) return;
    splitContainer.classList.toggle('has-cross-space', spacesState.spaces.length >= 2);
}

/** Measure the tab strip for the canonical drop computation (DOM read only). */
function measureSpaceDropLanes(host, draggedId) {
    const lanes = [];
    const tabRects = {};
    const sleeveRects = {};
    host.querySelectorAll('.shell-space-tab, .shell-space-group-sleeve').forEach((el) => {
        if (el.classList.contains('shell-space-tab')) {
            const id = el.dataset.spaceId;
            if (!id || id === draggedId) return;
            const r = el.getBoundingClientRect();
            lanes.push({ t: 'tab', id });
            tabRects[id] = { left: r.left, width: r.width };
        } else if (el.dataset.groupId) {
            const r = el.getBoundingClientRect();
            lanes.push({ t: 'sleeve', gid: el.dataset.groupId });
            sleeveRects[el.dataset.groupId] = { left: r.left, width: r.width };
        }
    });
    return { lanes, tabRects, sleeveRects };
}

/** One canonical drop target for preview AND commit (spaces_drop.js). */
function computeSpaceDropTarget(host, draggedId, x) {
    const visibleIds = Array.from(host.querySelectorAll('.shell-space-tab')).map((t) => t.dataset.spaceId);
    const m = measureSpaceDropLanes(host, draggedId);
    return CuttleSpaces.computeDropTarget({
        spaces: spacesState.spaces,
        groups: spacesState.groups,
        visibleIds,
        draggedId,
        lanes: m.lanes,
        tabRects: m.tabRects,
        sleeveRects: m.sleeveRects,
        x,
    });
}

/** Position the dragged tab element per the canonical preview directive. */
function placeDraggedSpaceTab(host, tab, target) {
    const place = (target && !target.noop && target.place) || { end: true };
    const tabElFor = (id) => {
        try {
            return host.querySelector('.shell-space-tab[data-space-id="' + CSS.escape(id) + '"]');
        } catch (_) { return null; }
    };
    const sleeveElFor = (gid) => {
        try {
            return host.querySelector('.shell-space-group-sleeve[data-group-id="' + CSS.escape(gid) + '"]');
        } catch (_) { return null; }
    };
    if (place.park) {
        const sleeve = sleeveElFor(place.park);
        if (sleeve && (tab.parentElement !== sleeve || tab.nextElementSibling !== null)) {
            sleeve.appendChild(tab);
        }
    } else if (place.beforeTab) {
        const ref = tabElFor(place.beforeTab);
        if (ref && tab.nextElementSibling !== ref) ref.parentElement.insertBefore(tab, ref);
    } else if (place.beforeSleeve) {
        const ref = sleeveElFor(place.beforeSleeve);
        if (ref && tab.nextElementSibling !== ref) host.insertBefore(tab, ref);
    } else if (host.lastElementChild !== tab) {
        host.appendChild(tab);
    }
}

/** Drag a tab sideways to reorder; it follows the pointer while siblings reflow around it. */
function setupSpaceTabDrag(host) {
    const THRESHOLD = 4;
    let drag = null;
    let suppressClick = false;

    const finish = (commit, dropX) => {
        if (!drag) return;
        const { tab, active, pointerId } = drag;
        drag = null;
        if (!active) return;
        tab.classList.remove('is-dragging');
        tab.style.transform = '';
        host.classList.remove('is-reordering');
        document.body.classList.remove('is-reordering-spaces');
        if (tab.hasPointerCapture(pointerId)) tab.releasePointerCapture(pointerId);
        if (commit) {
            // Commit consumes the same canonical target the preview showed:
            // no re-derivation from DOM order, no separate neighbor pass.
            const target = computeSpaceDropTarget(host, tab.dataset.spaceId, dropX);
            if (!target.noop) {
                const res = CuttleSpaces.applyDropTarget(spacesState, target, tab.dataset.spaceId);
                if (res.orderChanged || res.groupChanged || res.pruned) persistSpacesState();
            }
        }
        renderSpaceTabs();
        // The pointerup still produces a click on the tab; don't treat the drop as a switch.
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 0);
    };

    host.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || spacesState.spaces.length < 2) return;
        const tab = e.target.closest('.shell-space-tab');
        if (!tab || e.target.closest('.shell-space-close, .shell-space-rename')) return;
        drag = { tab, pointerId: e.pointerId, startX: e.clientX, originLeft: tab.getBoundingClientRect().left, tx: 0, active: false };
    });

    // Move/up/cancel ride on window (capture): the strip is small and the page
    // below is all iframes, which swallow events aimed at the strip. While a
    // drag is active, content iframes also lose hit-testing (see
    // body.is-reordering-spaces) so a release over page content still lands.
    window.addEventListener('pointermove', (e) => {
        if (!drag || e.pointerId !== drag.pointerId) return;
        // A press whose release was missed (off-window pointerup) must never
        // start a button-less reorder on the next hover — cancel it instead.
        if (e.buttons === 0) { finish(false); return; }
        const { tab } = drag;
        if (!drag.active) {
            if (Math.abs(e.clientX - drag.startX) < THRESHOLD) return;
            drag.active = true;
            tab.setPointerCapture(e.pointerId);
            tab.classList.add('is-dragging');
            host.classList.add('is-reordering');
            document.body.classList.add('is-reordering-spaces');
        }
        // Preview consumes the canonical target: the tab renders where the
        // drop would commit it, and the sleeve highlight matches the
        // membership the drop would assign (no separate hover rule).
        const target = computeSpaceDropTarget(host, tab.dataset.spaceId, e.clientX);
        placeDraggedSpaceTab(host, tab, target);
        // Hovering a sleeve lights its boundary exactly when the drop joins.
        host.querySelectorAll('.shell-space-group-sleeve.is-drop-target').forEach((el) => {
            el.classList.remove('is-drop-target');
        });
        if (target.groupId) {
            try {
                host.querySelector(
                    '.shell-space-group-sleeve[data-group-id="' + CSS.escape(target.groupId) + '"]'
                )?.classList.add('is-drop-target');
            } catch (_) {}
        }
        const rect = tab.getBoundingClientRect();
        const layoutLeft = rect.left - drag.tx;
        const hostRect = host.getBoundingClientRect();
        let left = drag.originLeft + (e.clientX - drag.startX);
        left = Math.max(hostRect.left, Math.min(left, hostRect.right - rect.width));
        drag.tx = left - layoutLeft;
        tab.style.transform = 'translateX(' + drag.tx + 'px)';
    });

    window.addEventListener('pointerup', (e) => {
        if (drag && e.pointerId === drag.pointerId) finish(true, e.clientX);
    }, true);
    window.addEventListener('pointercancel', (e) => {
        if (drag && e.pointerId === drag.pointerId) finish(false);
    }, true);
    // Releasing off-window delivers no pointerup; snap back instead of
    // floating the tab until the next hover cancels the gesture.
    window.addEventListener('blur', () => { if (drag) finish(false); });
    host.addEventListener('keydown', (e) => {
        if (drag?.active && e.key === 'Escape') {
            e.preventDefault();
            finish(false);
        }
    }, true);
    host.addEventListener('click', (e) => {
        if (!suppressClick) return;
        e.stopPropagation();
        e.preventDefault();
    }, true);
}

// ── Pane reorder (grip under logo) ─────────────────────────────
// RAIL_SVG_* / primaryRailLogoInnerHtml / closeRailLogoInnerHtml are defined
// earlier (before createSplitLeafColumn) so restoreSplitLayout cannot hit TDZ.

function ensureRailLogoModLayers(logo) {
    if (!logo || logo.querySelector('.rail-logo-split-v')) return;
    logo.insertAdjacentHTML('beforeend', RAIL_LOGO_MOD_LAYERS);
}

function defaultRailLogoTooltip(action) {
    return action === 'add'
        ? 'Add chat on the right · Shift horizontal · Alt vertical · Alt+Shift absolute vertical · Ctrl closes this pane'
        : 'Close · Shift horizontal split · Alt vertical split';
}

function syncRailLogoModifierUi() {
    const absVertical = !!_railModShift && !!_railModAlt;
    document.body.classList.toggle('rail-mod-alt-shift', absVertical);
    document.body.classList.toggle('rail-mod-shift', !!_railModShift && !absVertical);
    document.body.classList.toggle('rail-mod-alt', !!_railModAlt && !absVertical);
    document.querySelectorAll('.rail-logo-clickable').forEach((logo) => {
        const action = logo.dataset.action || 'add';
        if (absVertical) {
            logo.dataset.tooltip = 'Absolute vertical split — new cell at bottom';
        } else if (_railModShift) {
            logo.dataset.tooltip = 'Horizontal split this pane';
        } else if (_railModAlt) {
            logo.dataset.tooltip = 'Vertical split this pane';
        } else {
            logo.dataset.tooltip = defaultRailLogoTooltip(action);
        }
    });
}

function bindRailLogoModifierKeys() {
    if (document.documentElement.dataset.railModKeysBound === '1') return;
    document.documentElement.dataset.railModKeysBound = '1';

    const refresh = (e) => {
        if (!e) return;
        const nextShift = !!e.shiftKey;
        const nextAlt = !!e.altKey;
        const nextCtrl = !!(e.ctrlKey || e.metaKey);
        if (nextShift === _railModShift && nextAlt === _railModAlt && nextCtrl === _railModCtrl) {
            return;
        }
        _railModShift = nextShift;
        _railModAlt = nextAlt;
        _railModCtrl = nextCtrl;
        syncRailLogoModifierUi();
    };

    const clearMods = () => {
        if (!_railModShift && !_railModAlt && !_railModCtrl) return;
        _railModShift = false;
        _railModAlt = false;
        _railModCtrl = false;
        syncRailLogoModifierUi();
    };

    const bindDoc = (doc) => {
        if (!doc || !doc.documentElement) return;
        if (doc.documentElement.dataset.railModKeysDocBound === '1') return;
        doc.documentElement.dataset.railModKeysDocBound = '1';
        doc.addEventListener('keydown', refresh, true);
        doc.addEventListener('keyup', refresh, true);
        doc.addEventListener('blur', clearMods, true);
    };

    bindDoc(document);
    window.addEventListener('blur', clearMods);

    // Pointer over logo: sync modifiers from the mouse event (covers press-then-hover).
    document.addEventListener('pointerover', (e) => {
        const logo = e.target?.closest?.('.rail-logo-clickable');
        if (!logo) return;
        logo.classList.add('is-rail-logo-hot');
        refresh(e);
    }, true);
    document.addEventListener('pointerout', (e) => {
        const logo = e.target?.closest?.('.rail-logo-clickable');
        if (!logo) return;
        const toLogo = e.relatedTarget?.closest?.('.rail-logo-clickable');
        if (toLogo === logo) return;
        logo.classList.remove('is-rail-logo-hot');
    }, true);
    document.addEventListener('pointermove', (e) => {
        if (!e.target?.closest?.('.rail-logo-clickable')) return;
        refresh(e);
    }, true);

    // Fixed tips for icons inside the scrolling rail (avoids horizontal scrollbar).
    document.addEventListener('pointerover', (e) => {
        const item = e.target?.closest?.('.rail-items .rail-item');
        if (!item) return;
        const r = item.getBoundingClientRect();
        item.style.setProperty('--rail-tip-left', Math.round(r.right + 12) + 'px');
        item.style.setProperty('--rail-tip-top', Math.round(r.top + r.height / 2) + 'px');
    }, true);

    /** Call after iframe loads so Shift/Alt while hovering still update (focus is often in the frame). */
    window.bindRailModKeysForFrame = (frame) => {
        if (!frame) return;
        try {
            bindDoc(frame.contentDocument);
        } catch (_) {}
        try {
            const win = frame.contentWindow;
            if (win && win.__railModKeysWinBound !== 1) {
                win.__railModKeysWinBound = 1;
                win.addEventListener('keydown', refresh, true);
                win.addEventListener('keyup', refresh, true);
                win.addEventListener('blur', clearMods);
            }
        } catch (_) {}
    };

    // Bind any frames already present.
    document.querySelectorAll('.shell-main iframe').forEach((fr) => {
        try { window.bindRailModKeysForFrame(fr); } catch (_) {}
    });
}

function syncRailHeightChrome(colEl) {
    if (!colEl || !colEl.classList?.contains('split-column')) return;
    const h = colEl.clientHeight || 0;
    colEl.classList.toggle('rail-compact', h > 0 && h < 320);
    colEl.classList.toggle('rail-tight', h > 0 && h < 200);
}

function observeRailHeight(colEl) {
    if (!colEl || typeof ResizeObserver === 'undefined') {
        syncRailHeightChrome(colEl);
        return;
    }
    if (_railHeightObservers.has(colEl)) {
        syncRailHeightChrome(colEl);
        return;
    }
    const ro = new ResizeObserver(() => syncRailHeightChrome(colEl));
    ro.observe(colEl);
    _railHeightObservers.set(colEl, ro);
    syncRailHeightChrome(colEl);
}

function ensurePaneDropOverlay(colEl) {
    if (!colEl) return null;
    let el = colEl.querySelector(':scope > .pane-drop-overlay');
    if (!el) {
        el = document.createElement('div');
        el.className = 'pane-drop-overlay';
        el.setAttribute('aria-hidden', 'true');
        colEl.appendChild(el);
    }
    return el;
}

function ensureRailPaneGrip(colEl) {
    ensurePaneDropOverlay(colEl);
    const rail = colEl?.querySelector?.('.icon-rail');
    if (!rail) return null;
    let grip = rail.querySelector('.rail-pane-grip');
    if (!grip) {
        const logo = rail.querySelector('.rail-logo');
        grip = document.createElement('button');
        grip.type = 'button';
        grip.className = 'rail-pane-grip';
        grip.dataset.tooltip = 'Drag onto a pane to swap · onto a space tab to move';
        grip.setAttribute('aria-label', 'Drag onto a pane to swap · onto a space tab to move');
        grip.innerHTML = RAIL_PANE_GRIP_HTML;
        if (logo) logo.insertAdjacentElement('afterend', grip);
        else rail.insertBefore(grip, rail.firstChild);
    }
    return grip;
}

function updateSplitContainerChrome() {
    if (!splitContainer) return;
    const n = getSplitColumns().length;
    splitContainer.classList.toggle('has-splits', n >= 2);
    splitContainer.querySelectorAll('.split-group').forEach((g) => {
        g.classList.toggle('has-splits', getGroupChildNodes(g).length >= 2);
    });
}

function syncSplitColumnLogos() {
    const cols = getSplitColumns();
    cols.forEach((col, i) => {
        ensureLeafId(col);
        ensureRailPaneGrip(col);
        observeRailHeight(col);
        let logo = col.querySelector('.rail-logo');
        if (!logo) return;
        if (i === 0) {
            if (logo.dataset.action !== 'add') {
                logo.className = 'rail-logo rail-logo-primary rail-logo-clickable';
                logo.dataset.action = 'add';
                logo.removeAttribute('title');
                logo.id = 'railLogo';
                logo.innerHTML = primaryRailLogoInnerHtml();
            } else {
                ensureRailLogoModLayers(logo);
                if (!logo.querySelector('.rail-logo-plus')) {
                    logo.innerHTML = primaryRailLogoInnerHtml();
                }
            }
            logo.dataset.tooltip = defaultRailLogoTooltip('add');
        } else if (logo.dataset.action !== 'close') {
            logo.className = 'rail-logo rail-logo-close rail-logo-clickable';
            logo.dataset.action = 'close';
            logo.removeAttribute('title');
            logo.removeAttribute('id');
            logo.innerHTML = closeRailLogoInnerHtml();
            logo.dataset.tooltip = defaultRailLogoTooltip('close');
        } else {
            ensureRailLogoModLayers(logo);
            if (!logo.querySelector('.rail-logo-close-face')) {
                logo.innerHTML = closeRailLogoInnerHtml();
            }
            logo.dataset.tooltip = defaultRailLogoTooltip('close');
        }
    });
    syncRailLogoModifierUi();
}

/** Rebuild resize handles for one group (direct children only). */
function rebuildGroupResizeHandles(groupEl) {
    if (!groupEl || !isSplitGroupEl(groupEl)) return;
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) ch.remove();
    });
    const kids = getGroupChildNodes(groupEl);
    kids.forEach((kid, i) => {
        kid.style.order = String(i * 2);
        if (kid.classList.contains('split-column')) ensurePaneDropOverlay(kid);
    });
    for (let i = 0; i < kids.length - 1; i++) {
        const handle = document.createElement('div');
        handle.className = 'split-resize-handle';
        handle.style.order = String(i * 2 + 1);
        handle.dataset.groupId = groupEl.dataset.groupId || groupEl.id || '';
        handle.dataset.between = `${i}-${i + 1}`;
        groupEl.appendChild(handle);
        setupGroupSplitResize(handle, groupEl, i, i + 1);
    }
}

function rebuildSplitResizeHandles() {
    if (!splitContainer) return;
    rebuildGroupResizeHandles(splitContainer);
    splitContainer.querySelectorAll('.split-group').forEach((g) => rebuildGroupResizeHandles(g));
}

/** Remumber after reorder/close so col 0 stays the primary (+ logo) blade (1st = DFS reading order). */
function remumberSplitColumns() {
    const cols = getSplitColumns();
    const oldToNew = new Map();
    const nextState = new Map();
    const nextSessions = new Map();
    const nextTokens = new Map();
    const nextChatHandles = new Map();
    const claimedOldIdx = new Set();

    cols.forEach((col, newIdx) => {
        ensureLeafId(col);
        const oldIdx = parseInt(col.dataset.column, 10);
        const oldValid = Number.isFinite(oldIdx);
        // Each prior index may map to only one new column. Fresh clones used to
        // share data-column="0", which copied pane 0's chat onto every new leaf.
        const claim = oldValid && !claimedOldIdx.has(oldIdx);
        if (claim) {
            claimedOldIdx.add(oldIdx);
            oldToNew.set(oldIdx, newIdx);
            if (columnState.has(oldIdx)) nextState.set(newIdx, columnState.get(oldIdx));
            if (paneSessionByColumn.has(oldIdx)) nextSessions.set(newIdx, paneSessionByColumn.get(oldIdx));
            if (frameRevealTokens.has(oldIdx)) nextTokens.set(newIdx, frameRevealTokens.get(oldIdx));
            if (lastChatByColumn.has(oldIdx)) {
                nextChatHandles.set(newIdx, { ...lastChatByColumn.get(oldIdx) });
            }
        }
        col.dataset.column = String(newIdx);
        col.id = `splitColumn${newIdx}`;
    });

    columnState.clear();
    nextState.forEach((v, k) => columnState.set(k, v));
    paneSessionByColumn.clear();
    nextSessions.forEach((v, k) => paneSessionByColumn.set(k, v));
    frameRevealTokens.clear();
    nextTokens.forEach((v, k) => frameRevealTokens.set(k, v));
    // Keep in-flight frame reveals valid after index remap (avoids forever spinner).
    try {
        pendingFrameReveals.forEach((entry) => {
            if (entry && oldToNew.has(entry.colIdx)) {
                entry.colIdx = oldToNew.get(entry.colIdx);
            }
        });
    } catch (_) {}
    lastChatByColumn.clear();
    nextChatHandles.forEach((v, k) => lastChatByColumn.set(k, v));
    persistLastChatHandles();

    document.querySelectorAll('.shell-main iframe#contentFrame').forEach((fr) => {
        fr.removeAttribute('id');
    });
    if (cols[0]) {
        const main0 = cols[0].querySelector('.shell-main');
        const frames = main0
            ? Array.from(main0.querySelectorAll('iframe'))
                .filter((f) => !f.classList.contains('shell-frame-outgoing'))
            : [];
        const live0 = frames.find((f) => f.classList.contains('shell-frame-pending'))
            || frames[frames.length - 1]
            || null;
        if (live0) live0.id = 'contentFrame';
    }

    if (oldToNew.has(focusedColumnIdx)) {
        focusedColumnIdx = oldToNew.get(focusedColumnIdx);
    } else {
        focusedColumnIdx = 0;
    }

    syncSplitColumnLogos();
    rebuildSplitResizeHandles();
    updateSplitContainerChrome();
    broadcastPaneFocus();
}

function clearPaneDropIndicators() {
    document.querySelectorAll('.split-column.pane-drop-before, .split-column.pane-drop-after, .split-column.pane-drop-target')
        .forEach((c) => c.classList.remove('pane-drop-before', 'pane-drop-after', 'pane-drop-target'));
}

function stripGroupResizeHandles(groupEl) {
    if (!groupEl) return;
    Array.from(groupEl.children).forEach((ch) => {
        if (ch.classList?.contains('split-resize-handle')) {
            try { ch.remove(); } catch (_) {}
        }
    });
}

/** Swap two Map entries by key (missing keys stay missing). */
function swapMapKeys(map, keyA, keyB) {
    if (!map || keyA === keyB) return;
    const hasA = map.has(keyA);
    const hasB = map.has(keyB);
    const valA = hasA ? map.get(keyA) : undefined;
    const valB = hasB ? map.get(keyB) : undefined;
    if (hasB) map.set(keyA, valB);
    else map.delete(keyA);
    if (hasA) map.set(keyB, valA);
    else map.delete(keyB);
}

/**
 * Swap any two panes via the rail grip.
 *
 * Must NOT reparent `.split-column` nodes that still hold live chat iframes —
 * Chromium/Electron freezes on that DOM move (same class of bug as close/split
 * used to hit). Instead swap slot state + reload each frame in place so vertical
 * neighbors and cross-group cells can trade contents safely.
 */
function reorderSplitColumn(fromCol, targetCol, _before) {
    if (!fromCol || !targetCol || fromCol === targetCol) return false;
    if (!fromCol.classList?.contains('split-column') || !targetCol.classList?.contains('split-column')) {
        return false;
    }
    if (isSplitDiscardedEl(fromCol) || isSplitDiscardedEl(targetCol)) return false;

    const idxA = parseInt(fromCol.dataset.column, 10);
    const idxB = parseInt(targetCol.dataset.column, 10);
    if (!Number.isFinite(idxA) || !Number.isFinite(idxB) || idxA === idxB) return false;

    // Snapshot pages (with chat/terminal handles) before map keys move.
    const stateA = { ...getState(idxA) };
    const stateB = { ...getState(idxB) };
    const pageA = pageWithPaneSession(idxA, canonicalizeShellPage(stateA.page || '/chat_page.html'));
    const pageB = pageWithPaneSession(idxB, canonicalizeShellPage(stateB.page || '/chat_page.html'));

    const leafA = fromCol.dataset.leafId || newLeafId();
    const leafB = targetCol.dataset.leafId || newLeafId();
    fromCol.dataset.leafId = leafB;
    targetCol.dataset.leafId = leafA;

    swapMapKeys(columnState, idxA, idxB);
    swapMapKeys(lastChatByColumn, idxA, idxB);
    swapMapKeys(paneSessionByColumn, idxA, idxB);
    swapMapKeys(frameRevealTokens, idxA, idxB);

    // Slots keep their indices; contents trade. Re-apply explicit pages after the map swap.
    setState(idxA, pageB);
    setState(idxB, pageA);
    getState(idxA).railCollapsed = !!stateB.railCollapsed;
    getState(idxB).railCollapsed = !!stateA.railCollapsed;
    applyRailCollapsed(idxA, !!stateB.railCollapsed);
    applyRailCollapsed(idxB, !!stateA.railCollapsed);

    updateColumnUI(idxA, pageB);
    updateColumnUI(idxB, pageA);

    const frameA = replaceFrame(idxA, pageB);
    if (frameA) attachFrameLoadListener(idxA, frameA);
    const frameB = replaceFrame(idxB, pageA);
    if (frameB) attachFrameLoadListener(idxB, frameB);

    if (focusedColumnIdx === idxA) focusedColumnIdx = idxB;
    else if (focusedColumnIdx === idxB) focusedColumnIdx = idxA;

    persistLastChatHandles();
    persistSplitLayout();
    broadcastPaneFocus();
    return true;
}

function setupPaneReorder(colEl) {
    if (!colEl) return;
    const rail = colEl.querySelector('.icon-rail');
    if (!rail || rail.dataset.paneReorder === '1') return;
    rail.dataset.paneReorder = '1';
    const grip = ensureRailPaneGrip(colEl);
    if (!grip) return;

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let activated = false;
    let dropTarget = null;
    let dropBefore = true;
    // Cross-space carry: snapshot of the dragged leaf + source space while the
    // pointer hovers space tabs (quick drop = move to end, 1s hover = switch).
    let dragPayload = null;
    let carrying = false;
    let sourceSpaceId = null;
    let hoverTab = null;
    let hoverTimer = 0;

    const clearSpaceHover = () => {
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        if (hoverTab) { hoverTab.classList.remove('pane-space-hover'); hoverTab = null; }
    };

    const unbindWindow = () => {
        window.removeEventListener('pointermove', onMove, true);
        window.removeEventListener('pointerup', onUp, true);
        window.removeEventListener('pointercancel', onCancel, true);
        window.removeEventListener('blur', onBlurCancel, true);
    };

    const autoSwitchToSpace = (spaceId) => {
        if (!dragging || !spaceId || spaceId === spacesState.active) return;
        if (!carrying) {
            carrying = true;
            sourceSpaceId = spacesState.active;
        }
        clearSpaceHover();
        dropTarget = null;
        clearPaneDropIndicators();
        switchSpace(spaceId);
        // Hovering home for 1s just comes back — the payload never left.
        if (spacesState.active === sourceSpaceId) {
            carrying = false;
            sourceSpaceId = null;
        }
    };

    const trackSpaceTabHover = (e) => {
        if (spacesState.spaces.length < 2) return;
        const tab = spaceTabAtPoint(e.clientX, e.clientY);
        if (tab === hoverTab) return;
        clearSpaceHover();
        hoverTab = tab || null;
        if (hoverTab && hoverTab.dataset.spaceId !== spacesState.active) {
            hoverTab.classList.add('pane-space-hover');
            const id = hoverTab.dataset.spaceId;
            hoverTimer = setTimeout(() => {
                if (dragging && hoverTab && hoverTab.dataset.spaceId === id) autoSwitchToSpace(id);
            }, PANE_SPACE_HOVER_MS);
        }
    };

    const finish = (upEvent, cancelled) => {
        if (!dragging) return;
        const wasActive = activated;
        const target = dropTarget;
        const before = dropBefore;
        const load = dragPayload;
        const wasCarrying = carrying;
        const srcId = sourceSpaceId;
        dragging = false;
        activated = false;
        carrying = false;
        sourceSpaceId = null;
        dragPayload = null;
        unbindWindow();
        clearSpaceHover();
        grip.classList.remove('is-dragging');
        colEl.classList.remove('is-pane-dragging');
        splitContainer?.classList.remove('is-reordering-panes');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        clearPaneDropIndicators();
        dropTarget = null;
        if (cancelled || !wasActive || !load) return;
        const tab = (upEvent && Number.isFinite(upEvent.clientX) && Number.isFinite(upEvent.clientY))
            ? spaceTabAtPoint(upEvent.clientX, upEvent.clientY)
            : null;
        const tabId = tab ? (tab.dataset.spaceId || '') : '';
        if (tabId && tabId !== spacesState.active) {
            if (!wasCarrying) {
                setTimeout(() => moveDraggedPaneToSpace(load, colEl, tabId), 0);
                return;
            }
            if (tabId === srcId) {
                setTimeout(() => switchSpace(srcId), 0);
                return;
            }
            setTimeout(() => moveCarriedPaneToSpace(load, srcId, tabId), 0);
            return;
        }
        if (wasCarrying) {
            if (target && target.isConnected && target !== colEl) {
                const targetEl = target;
                setTimeout(() => exchangeCarriedPaneWithLive(load, srcId, targetEl), 0);
            }
            return;
        }
        if (!target || target === colEl) return;
        setTimeout(() => reorderSplitColumn(colEl, target, before), 0);
    };

    const onMove = (e) => {
        if (!dragging) return;
        // A grip press whose release was missed must never start a button-less
        // drag on the next hover — cancel it instead of committing anything.
        if (e.buttons === 0) { finish(null, true); return; }
        trackSpaceTabHover(e);
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        if (!activated) {
            if ((dx * dx) + (dy * dy) < 36) return;
            activated = true;
            grip.classList.add('is-dragging');
            colEl.classList.add('is-pane-dragging');
            document.body.style.cursor = 'grabbing';
            document.body.style.userSelect = 'none';
        }

        // Hit-test every live leaf (horizontal, vertical, nested). Prefer the
        // smallest containing rect so a nested cell wins over a larger sibling.
        const cols = getSplitColumns().filter((c) => c !== colEl);
        clearPaneDropIndicators();
        dropTarget = null;

        let hit = null;
        let hitArea = Infinity;
        for (const other of cols) {
            const rect = other.getBoundingClientRect();
            if (
                e.clientX >= rect.left && e.clientX < rect.right
                && e.clientY >= rect.top && e.clientY < rect.bottom
            ) {
                const area = Math.max(1, rect.width) * Math.max(1, rect.height);
                if (area < hitArea) {
                    hit = other;
                    hitArea = area;
                }
            }
        }
        if (!hit || hit === colEl) return;

        const rect = hit.getBoundingClientRect();
        const parentOri = getGroupOrientation(getLeafParentGroup(hit));
        const vertical = parentOri === 'vertical';
        const useVertical = vertical || rect.height >= rect.width;
        dropBefore = useVertical
            ? (e.clientY < (rect.top + rect.height / 2))
            : (e.clientX < (rect.left + rect.width / 2));
        hit.classList.add(
            'pane-drop-target',
            dropBefore ? 'pane-drop-before' : 'pane-drop-after'
        );
        dropTarget = hit;
    };

    const onUp = (e) => finish(e, false);
    const onCancel = () => finish(null, true);
    const onBlurCancel = () => finish(null, true);

    grip.addEventListener('pointerdown', (e) => {
        if (e.button != null && e.button !== 0) return;
        if (getSplitColumns().length < 2 && spacesState.spaces.length < 2) return;
        if (dragging) finish();
        e.preventDefault();
        e.stopPropagation();
        dragging = true;
        activated = false;
        carrying = false;
        sourceSpaceId = null;
        dragPayload = snapshotLayoutTree(colEl);
        startX = e.clientX;
        startY = e.clientY;
        dropTarget = null;
        dropBefore = true;
        splitContainer?.classList.add('is-reordering-panes');
        window.addEventListener('pointermove', onMove, true);
        window.addEventListener('pointerup', onUp, true);
        window.addEventListener('pointercancel', onCancel, true);
        window.addEventListener('blur', onBlurCancel, true);
    });
}

// ── Per-group split resize ────────────────────────────────────
function setupGroupSplitResize(handleEl, groupEl, leftPos, rightPos) {
    if (!handleEl || handleEl.dataset.resizeBound === '1') return;
    handleEl.dataset.resizeBound = '1';
    let dragging = false;

    function stopDrag() {
        if (!dragging) return;
        dragging = false;
        handleEl.classList.remove('dragging');
        splitContainer?.classList.remove('split-resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        finalizeGroupToRatios(groupEl);
        persistSplitLayout();
    }

    handleEl.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        const cols = getGroupChildNodes(groupEl);
        if (cols.length < 2) return;
        if (leftPos < 0 || rightPos < 0 || rightPos !== leftPos + 1) return;
        if (rightPos >= cols.length) return;

        e.preventDefault();
        handleEl.setPointerCapture(e.pointerId);
        dragging = true;

        const ori = getGroupOrientation(groupEl);
        const vertical = ori === 'vertical';
        const budget = getSplitGroupBudget(groupEl, cols.length);
        const minPx = splitMinPxFor(cols.length, budget, ori);
        const startWidths = clampSplitWidths(
            cols.map((c) => measureSplitMain(c, ori)),
            budget,
            ori
        );
        const startCoord = vertical ? e.clientY : e.clientX;
        handleEl.classList.add('dragging');
        splitContainer?.classList.add('split-resizing');
        document.body.style.cursor = vertical ? 'row-resize' : 'col-resize';
        document.body.style.userSelect = 'none';

        applySplitPixelFlex(cols, startWidths, ori, groupEl);

        const onMove = (ev) => {
            if (!dragging) return;
            const delta = (vertical ? ev.clientY : ev.clientX) - startCoord;
            const widths = startWidths.slice();
            let remaining = delta;

            if (remaining > 0) {
                for (let i = rightPos; i < widths.length && remaining > 0; i++) {
                    const can = widths[i] - minPx;
                    if (can <= 0) continue;
                    const take = Math.min(can, remaining);
                    widths[i] -= take;
                    remaining -= take;
                }
                widths[leftPos] += delta - remaining;
            } else if (remaining < 0) {
                let need = -remaining;
                for (let i = leftPos; i >= 0 && need > 0; i--) {
                    const can = widths[i] - minPx;
                    if (can <= 0) continue;
                    const take = Math.min(can, need);
                    widths[i] -= take;
                    need -= take;
                }
                widths[rightPos] += (-remaining) - need;
            }
            applySplitPixelFlex(cols, widths, ori, groupEl);
        };
        const onUp = () => {
            try { handleEl.releasePointerCapture(e.pointerId); } catch (_) {}
            handleEl.removeEventListener('pointermove', onMove);
            handleEl.removeEventListener('pointerup', onUp);
            handleEl.removeEventListener('pointercancel', onUp);
            stopDrag();
        };
        handleEl.addEventListener('pointermove', onMove);
        handleEl.addEventListener('pointerup', onUp);
        handleEl.addEventListener('pointercancel', onUp);
    });
    // Intentionally no document/window listeners — rebuilds used to leak them and freeze Chrome.
}

// Legacy sessions may still have fixed-px flex in memory — convert on resize so
// maximize/windowed transitions don't crop panes before the next drag.
// Ratio flex already scales with the container (min-width: 0); no re-finalize.
window.addEventListener('resize', () => {
    if (!splitContainer) return;
    let changed = false;
    const groups = [splitContainer, ...splitContainer.querySelectorAll('.split-group')];
    groups.forEach((g) => {
        if (migrateFixedPxSplitFlex(getGroupChildNodes(g), g)) changed = true;
    });
    if (changed) persistSplitLayout();
});

// ── Theme sync (rail & panel) ─────────────────────────────────
function applyShellTheme(theme) {
    if (window.CuttleUiBoot) {
        if (window.CuttleUiBoot.currentTheme() !== window.CuttleUiBoot.normalizeTheme(theme)) {
            window.CuttleUiBoot.applyTheme(theme);
        }
        applyUiAnimationPrefs();
        return;
    }
    const want = theme === 'light' ? 'light-mode'
        : theme === 'midnight' ? 'midnight-mode'
        : 'dark-mode';
    [document.documentElement, document.body].forEach((el) => {
        if (!el) return;
        const others = ['dark-mode', 'midnight-mode', 'light-mode'].filter((c) => c !== want);
        if (el.classList.contains(want) && !others.some((c) => el.classList.contains(c))) return;
        el.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
        el.classList.add(want);
    });
    applyUiAnimationPrefs();
}

function initializeShellTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    applyShellTheme(saved);
}

initializeShellTheme();

// ── postMessage: navigate focused/first column; electron zoom; theme ─
window.addEventListener('message', (e) => {
    if (e.data?.type === 'cuttle-page-ready') {
        finishFrameTransitionFor(e.source);
        // Freshly revealed frames (space switches included) rescan live
        // usage immediately instead of waiting out a dead refresh timer.
        broadcastUsageLiveWake();
        return;
    }
    if (e.data?.type === 'cuttle-navigate' && e.data?.page) {
        navigateFromSource(e.source, e.data.page);
    }
    if (e.data?.type === 'electron-zoom' && window.electron) {
        handleElectronZoomWheel(e.data.deltaY, e.data.deltaMode);
    }
    if (e.data?.type === 'electron-zoom-key' && window.electron) {
        const now = Date.now();
        if (e.data.action !== 'reset' && (now - _zoomLastStepAt) < ZOOM_MIN_STEP_MS) return;
        _zoomLastStepAt = now;
        _zoomWheelAccum = 0;
        applyZoomAction(e.data.action);
    }
    if (e.data?.type === 'browser-zoom-hint') {
        requestAnimationFrame(() => showZoomHud(readZoomPercent()));
        setTimeout(() => showZoomHud(readZoomPercent()), 60);
    }
    if (e.data?.type === 'cuttle-theme-change' && e.data?.theme) {
        applyShellTheme(e.data.theme);
        // Keep the other blades in sync; the sender already applied it.
        broadcastToFrames({ type: 'cuttle-theme-change', theme: e.data.theme }, e.source);
    }
    if (e.data?.type === 'cuttle-ui-anim-change') {
        applyUiAnimationPrefs();
        broadcastToFrames({ type: 'cuttle-ui-anim-change' });
    }
});


// ── Shell heartbeat (jobs badge + toasts + workers + live-status) ─
// One cadence instead of separate 2s/3s/8s/15s timers. Slows down when the
// window is hidden so laptop Clients stop waking Wi‑Fi for invisible UI.
const SHELL_HEARTBEAT_VISIBLE_MS = 6000;
const SHELL_HEARTBEAT_HIDDEN_MS = 45000;
let shellHeartbeatTimer = null;
let shellHeartbeatInFlight = false;

async function pollJobsBadge() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const rJobs = await fetch('/api/executing-jobs', { cache: 'no-store', signal: controller.signal });
        if (rJobs.ok) {
            const dJobs = await rJobs.json();
            const executing = dJobs.executing_jobs || [];
            document.querySelectorAll('.rail-item[data-page="/jobs_page.html"] .rail-badge').forEach(badge => {
                const count = executing ? executing.length : 0;
                badge.textContent = String(count);
                badge.style.display = count > 0 ? '' : 'none';
            });
        }
    } catch (_) {}
    clearTimeout(timeout);
}

/** Drain system toasts into Electron toast + chirp. */
async function pollUiToasts() {
    try {
        const r = await fetch('/api/ui-toasts', { cache: 'no-store' });
        if (!r.ok) return;
        const data = await r.json();
        const toasts = data.toasts || [];
        for (const t of toasts) {
            const msg = (t && t.message) ? String(t.message) : '';
            if (!msg) continue;
            const variant = (t && t.variant) ? String(t.variant) : 'info';
            if (typeof window.showToast === 'function') {
                window.showToast(msg, variant);
            }
            if ((variant === 'success' || variant === 'error')
                && typeof window.playCuttleCompletionChirp === 'function') {
                window.playCuttleCompletionChirp();
            }
        }
    } catch (_) {}
}

function shellHeartbeatDelayMs() {
    return document.hidden ? SHELL_HEARTBEAT_HIDDEN_MS : SHELL_HEARTBEAT_VISIBLE_MS;
}

function applyShellBackgroundPaused(hidden) {
    const on = !!hidden;
    document.documentElement.classList.toggle('cuttle-bg-paused', on);
    if (document.body) document.body.classList.toggle('cuttle-bg-paused', on);
    try {
        broadcastToFrames({ type: 'cuttle-shell-visibility', hidden: on });
    } catch (_) {}
}

async function runShellHeartbeat() {
    if (shellHeartbeatInFlight) return;
    shellHeartbeatInFlight = true;
    try {
        const tasks = [pollUiToasts(), pollJobsBadge()];
        if (typeof window.__cuttleShellPollWorkers === 'function') {
            tasks.push(Promise.resolve(window.__cuttleShellPollWorkers()));
        }
        if (paneSessionByColumn.size > 0) {
            tasks.push(pollLiveStatusHub());
        }
        tasks.push(refreshSpaceActivityFromServer());
        await Promise.all(tasks);
    } catch (_) {
    } finally {
        shellHeartbeatInFlight = false;
    }
}

function scheduleShellHeartbeat(immediate) {
    if (shellHeartbeatTimer) {
        clearTimeout(shellHeartbeatTimer);
        shellHeartbeatTimer = null;
    }
    const delay = immediate ? 0 : shellHeartbeatDelayMs();
    shellHeartbeatTimer = setTimeout(async () => {
        shellHeartbeatTimer = null;
        try {
            await runShellHeartbeat();
        } finally {
            scheduleShellHeartbeat(false);
        }
    }, delay);
}

applyShellBackgroundPaused(document.hidden);
document.addEventListener('visibilitychange', () => {
    applyShellBackgroundPaused(document.hidden);
    // Immediate beat on focus so badges/toasts catch up after a long hide.
    scheduleShellHeartbeat(true);
    schedulePendingChangesHub(true);
});
scheduleShellHeartbeat(true);

function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Initial UI
updateColumnUI(0, currentPage);

// ── Nav debug ─────────────────────────────────────────────────
// Enable via localStorage: localStorage.setItem('cuttle_nav_debug', '1')
(function () {
    if (!localStorage.getItem('cuttle_nav_debug')) return;
    function ts() { return new Date().toISOString().slice(11, 23); }
    function log(msg) {
        fetch('/api/debug-log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ msg: '[' + ts() + '] ' + msg })
        }).catch(() => {});
    }
    window._dbg = log;

    // Patch navigate to log every call
    const _navigate = navigate;
    window.navigate = function (colIdx, page) {
        const st = getState(colIdx);
        if (st.page === page) {
            log('NAV-SKIP col=' + colIdx + ' page="' + page + '" (state already="' + st.page + '")');
            return;
        }
        log('NAV col=' + colIdx + ' from="' + st.page + '" to="' + page + '"');
        _navigate(colIdx, page);
        const f = getLiveFrame(colIdx);
        log('NAV-AFTER col=' + colIdx + ' newFrame=' + (f ? f.src : 'null'));
    };

    // Reassign rail listeners to use patched navigate (they close over original)
    document.querySelectorAll('.rail-item[data-page]').forEach(btn => {
        const listeners = btn.cloneNode(true);
        btn.parentNode.replaceChild(listeners, btn);
    });
    const col = getColumnEl(0);
    if (col) {
        col.querySelectorAll('.rail-item[data-page]').forEach(btn => {
            btn.addEventListener('click', () => window.navigate(0, btn.dataset.page));
        });
    }

    // Poll iframe URL every 500ms, log changes
    let lastHref = '', lastState = '';
    setInterval(() => {
        const f = getLiveFrame(0);
        let href = '(null)';
        try { href = f?.contentWindow?.location?.href || '(blank)'; } catch (e) { href = '(err)'; }
        const s = getState(0)?.page || '(none)';
        if (href !== lastHref || s !== lastState) {
            log('POLL state="' + s + '" iframe="' + href + '"');
            lastHref = href; lastState = s;
        }
    }, 500);

    log('--- debug started --- initPage="' + currentPage + '"');
})();

// ── Cuttle Mobile native shell (Electron-equivalent on phone) ─
(function initMobileAppShell() {
    const LATER_KEY = 'cuttle-apk-update-later';
    const LATER_HASH_KEY = 'cuttle-apk-update-later-hash';
    let apkModalHash = '';
    let apkInstalling = false;

    function isMobileClient() {
        if (window.isCuttleMobile || window.cuttleMobile?.isNative) return true;
        return /\bCuttleMobile\/[\d.]+\b/.test(navigator.userAgent || '');
    }

    function localShellHash() {
        try {
            if (window.CuttleShellNative?.getShellHash) return String(window.CuttleShellNative.getShellHash() || '');
        } catch (_) {}
        try {
            if (window.cuttleMobile?.getShellHash) return String(window.cuttleMobile.getShellHash() || '');
        } catch (_) {}
        return String(window.cuttleMobile?.shellHash || '');
    }

    function startInstall() {
        apkInstalling = true;
        const modal = document.getElementById('mobileApkUpdateModal');
        if (modal) modal.hidden = true;
        if (window.cuttleMobile?.installUpdate) {
            window.cuttleMobile.installUpdate();
            return;
        }
        if (window.CuttleShellNative?.installUpdate) {
            window.CuttleShellNative.installUpdate();
            return;
        }
        if (window.cuttleMobile?.checkUpdate) {
            window.cuttleMobile.checkUpdate();
            return;
        }
        if (window.CuttleShellNative?.checkUpdate) {
            window.CuttleShellNative.checkUpdate();
        }
    }

    function ensureModal() {
        if (document.getElementById('mobileApkUpdateModal')) return;
        const wrap = document.createElement('div');
        wrap.id = 'mobileApkUpdateModal';
        wrap.className = 'mobile-apk-update-modal';
        wrap.hidden = true;
        wrap.innerHTML =
            '<div class="mobile-apk-update-card" role="dialog" aria-labelledby="mobileApkUpdateTitle">'
            + '<h2 id="mobileApkUpdateTitle">Update available</h2>'
            + '<p>A newer Cuttle phone app is on your PC. Install over Wi‑Fi — Android will ask you to confirm.</p>'
            + '<div class="mobile-apk-update-actions">'
            + '<button type="button" class="mobile-apk-update-install" id="mobileApkUpdateInstall">Download &amp; install</button>'
            + '<button type="button" class="mobile-apk-update-later" id="mobileApkUpdateLater">Later</button>'
            + '</div></div>';
        document.body.appendChild(wrap);
        wrap.querySelector('#mobileApkUpdateInstall')?.addEventListener('click', () => {
            startInstall();
            wrap.hidden = true;
        });
        wrap.querySelector('#mobileApkUpdateLater')?.addEventListener('click', () => {
            try {
                sessionStorage.setItem(LATER_KEY, '1');
                if (apkModalHash) localStorage.setItem(LATER_HASH_KEY, apkModalHash);
            } catch (_) {}
            wrap.hidden = true;
        });
    }

    function setRailVisible(on) {
        document.querySelectorAll('.rail-apk-update').forEach(btn => {
            btn.hidden = !on;
            btn.classList.toggle('apk-update-ready', !!on);
        });
    }

    function showStartupModal(on, hash) {
        const modal = document.getElementById('mobileApkUpdateModal');
        if (!modal) return;
        if (!on || apkInstalling) {
            modal.hidden = true;
            return;
        }
        const h = String(hash || apkModalHash || '');
        let skipped = false;
        try {
            skipped = sessionStorage.getItem(LATER_KEY) === '1'
                || (h && localStorage.getItem(LATER_HASH_KEY) === h);
        } catch (_) {}
        if (skipped) {
            modal.hidden = true;
            return;
        }
        if (!modal.hidden && h && h === apkModalHash) return;
        apkModalHash = h;
        modal.hidden = false;
    }

    function ensureUpdateRail() {
        if (!isMobileClient()) return;
        ensureModal();
        document.querySelectorAll('.rail-footer').forEach(footer => {
            if (footer.querySelector('.rail-apk-update')) return;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rail-item rail-apk-update';
            btn.hidden = true;
            btn.setAttribute('data-tooltip', 'Update available');
            btn.setAttribute('aria-label', 'Update available');
            btn.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                + '<polyline points="7 10 12 15 17 10"/>'
                + '<line x1="12" y1="15" x2="12" y2="3"/>'
                + '</svg>'
                + '<span class="rail-badge rail-badge-apk-update">!</span>';
            const notif = footer.querySelector('[data-id="nav-notifications"]');
            if (notif) footer.insertBefore(btn, notif);
            else footer.insertBefore(btn, footer.firstChild);
            btn.addEventListener('click', () => {
                if (railEditing || railSuppressClick) return;
                startInstall();
            });
        });
    }

    window.ensureCuttleMobileApkUpdateUi = ensureUpdateRail;

    async function refreshUpdateAvailability() {
        if (!isMobileClient()) {
            setRailVisible(false);
            return;
        }
        ensureUpdateRail();
        let available = !!window.cuttleApkUpdateAvailable;
        try {
            const res = await fetch('/api/mobile/android', { cache: 'no-store' });
            if (res.ok) {
                const remote = await res.json();
                const local = localShellHash();
                if (remote && remote.ok && remote.artifact && remote.hash && local) {
                    available = remote.hash !== local;
                    if (available) apkModalHash = remote.hash;
                } else {
                    available = false;
                }
            }
        } catch (_) {}
        window.cuttleApkUpdateAvailable = available;
        setRailVisible(available);
        showStartupModal(available, apkModalHash);
    }

    function boot() {
        if (!isMobileClient()) return;
        ensureUpdateRail();
        // Delay APK check — competing with the first chat iframe on Werkzeug HTTPS
        // causes net::ERR_TOO_MANY_RETRIES / black screens on notification opens.
        setTimeout(() => {
            if (!isMobileClient()) return;
            refreshUpdateAvailability();
            window.cuttleMobile?.checkUpdate?.();
        }, 12000);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState !== 'visible' || !isMobileClient()) return;
            fetch('/api/lan-ping', { cache: 'no-store' })
                .then((r) => (r.ok ? r.json() : Promise.reject(new Error('http'))))
                .then((d) => {
                    if (!d?.ok) throw new Error('bad');
                })
                .catch(() => {
                    try {
                        window.CuttleShellNative?.showConnectionError?.('');
                    } catch (_) {}
                });
        });
    }

    document.addEventListener('cuttle-mobile-ready', boot);
    document.addEventListener('cuttle-apk-update', (e) => {
        if (!isMobileClient()) return;
        const d = (e.detail && typeof e.detail === 'object') ? e.detail : {};
        const status = String(d.status || '');
        if (d.hash) apkModalHash = String(d.hash);
        if (status === 'installing' || status === 'downloading' || status === 'checking') {
            if (status === 'installing') apkInstalling = true;
            setRailVisible(!!d.available);
            if (status === 'installing') showStartupModal(false);
            return;
        }
        if (status === 'up_to_date' || status === 'error') {
            apkInstalling = false;
            window.cuttleApkUpdateAvailable = false;
            ensureUpdateRail();
            setRailVisible(false);
            showStartupModal(false);
            return;
        }
        const on = !!d.available;
        window.cuttleApkUpdateAvailable = on;
        ensureUpdateRail();
        setRailVisible(on);
        showStartupModal(on, d.hash);
    });
    if (isMobileClient()) boot();
    setInterval(() => {
        if (isMobileClient()) refreshUpdateAvailability();
    }, 60000);
})();

// ── Device-worker SSH approval (HITL) ───────────────────────────
(function initSshApprovalModal() {
    const modal = document.getElementById('sshApprovalModal');
    if (!modal) return;
    const subtitle = document.getElementById('sshApprovalSubtitle');
    const meta = document.getElementById('sshApprovalMeta');
    const btnOnce = document.getElementById('sshApprovalOnce');
    const btnSession = document.getElementById('sshApprovalSession');
    const btnDeny = document.getElementById('sshApprovalDeny');
    let currentId = '';
    let notifiedIds = new Set();
    let deciding = false;

    function show(req) {
        if (!req || !req.id) return;
        currentId = String(req.id);
        if (subtitle) {
            subtitle.textContent = req.message
                || ('Cuttle (' + (req.worker_id || 'worker') + ') is requesting to attempt "'
                    + (req.job_kind || 'execute_shell_ssh') + '". Would you like to approve?');
        }
        if (meta) {
            const bits = [];
            if (req.job_kind) bits.push(req.job_kind);
            if (req.target) bits.push('target: ' + req.target);
            if (req.command_preview) bits.push('cmd: ' + req.command_preview);
            if (req.job_id) bits.push('job: ' + String(req.job_id).slice(0, 8));
            meta.textContent = bits.join(' · ');
        }
        modal.hidden = false;
        if (!notifiedIds.has(currentId)) {
            notifiedIds.add(currentId);
            try {
                if (window.Notification && Notification.permission === 'granted') {
                    new Notification('Cuttle shell approval', {
                        body: (req.worker_id || 'worker') + ' · ' + (req.job_kind || 'unsafe shell'),
                    });
                } else if (window.Notification && Notification.permission === 'default') {
                    Notification.requestPermission();
                }
            } catch (_) {}
            try {
                if (window.showToast) window.showToast('Unsafe shell approval needed', 'info');
            } catch (_) {}
        }
    }

    function hide() {
        modal.hidden = true;
        currentId = '';
        deciding = false;
    }

    async function decide(decision) {
        if (!currentId || deciding) return;
        deciding = true;
        try {
            const r = await fetch('/api/workers/ssh-approval/' + encodeURIComponent(currentId) + '/decide', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision }),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
            hide();
        } catch (e) {
            deciding = false;
            try { if (window.showToast) window.showToast(String(e.message || e), 'error'); } catch (_) {}
        }
    }

    btnOnce && btnOnce.addEventListener('click', () => decide('once'));
    btnSession && btnSession.addEventListener('click', () => decide('session'));
    btnDeny && btnDeny.addEventListener('click', () => decide('deny'));

    async function poll() {
        if (document.hidden) return;
        try {
            const r = await fetch('/api/workers/ssh-approval/pending', { cache: 'no-store' });
            if (!r.ok) return;
            const data = await r.json();
            const pending = (data && data.pending) || [];
            if (!pending.length) {
                if (currentId) hide();
                return;
            }
            // Show oldest pending first
            const sorted = pending.slice().sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
            const next = sorted[0];
            if (!currentId || currentId !== next.id) show(next);
        } catch (_) {}
    }

    setInterval(poll, 2000);
    setTimeout(poll, 800);
})();
