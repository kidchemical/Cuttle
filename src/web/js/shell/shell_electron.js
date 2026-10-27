/* Shell Electron chrome: frameless window titlebar (window controls, worker
 * chip, fullscreen auto-hide), the chat-find shortcut bridge, and the
 * Ctrl+wheel / Ctrl+/- zoom HUD. The titlebar Update button and its modal are
 * owned by shell_updates.js; this file reports device state to it.
 *
 * Owned here. app_shell.js loads after this file, forwards zoom postMessages
 * through ``CuttleShellElectron.handleZoomMessage``, calls
 * ``injectElectronZoomHandler`` per pane, and re-arms/cancels the titlebar
 * auto-hide via ``rescheduleTitlebarHide`` / ``cancelTitlebarHide``.
 */
(function (root) {
    'use strict';

    // Hooks the fullscreen titlebar auto-hide exposes. Space menus/bubbles pin
    // the titlebar while open and re-arm the hide timer after they close.
    let rescheduleHide = null;
    let cancelHide = null;
    let desktopCfg = { clientMode: false, host: '127.0.0.1' };

    function initElectronTitlebar() {
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
        const updateChip = document.getElementById('shellWinUpdateChip');
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

        const connection = root.CuttleShellConnection || null;
        let hostNetInfo = null;
        const applyHostChip = (cfg) => {
            if (!hostBtn) return;
            const role = typeof cuttleDesktopRole === 'function'
                ? cuttleDesktopRole(cfg)
                : (cfg && cfg.clientMode ? 'client' : 'host');
            const isClient = role === 'client';
            hostBtn.hidden = false;
            hostBtn.dataset.role = role;
            hostBtn.textContent = isClient ? 'Client' : 'Host';
            hostBtn.title = connection
                ? connection.badgeTooltip(cfg, hostNetInfo)
                : (isClient ? `Connected to ${cfg && cfg.host ? cfg.host : 'remote Cuttle'}` : 'This PC is running the Cuttle daemon');
            if (cfg && cfg.packageVersion) applyTitleVersion(cfg.packageVersion);
        };
        const fetchNetworkInfo = () => fetch('/api/network-info', { cache: 'no-store' })
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null);
        desktopCfg = { clientMode: false, host: '127.0.0.1' };
        applyHostChip(desktopCfg);
        // The titlebar Update button belongs to the update center
        // (shell_updates.js): this file only reports device state to it —
        // desktop config, the packaged-shell (asar) offer and the mesh rev.
        const updates = root.cuttleShellUpdates || null;
        if (updates) updates.bindTitlebar(updateBtn, updateChip);
        else if (updateBtn) updateBtn.hidden = true;
        const NO_MESH_UPDATE = { available: false, target: '', localVersion: '', hostVersion: '', localRev: '', hostRev: '' };
        let meshUpdate = { ...NO_MESH_UPDATE };
        const reportUpdateState = () => {
            if (updates) updates.setDesktopState({ cfg: desktopCfg, mesh: meshUpdate });
        };
        const applyAsarUpdateBtn = (payload) => {
            if (updates) updates.setDesktopState({ asarAvailable: !!(payload && payload.available) });
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
            // Older Electron main processes do not report lanIps; ask the Host's Flask.
            if (!desktopCfg.clientMode && !(Array.isArray(desktopCfg.lanIps) && desktopCfg.lanIps.length)) {
                fetchNetworkInfo().then((net) => {
                    hostNetInfo = net;
                    applyHostChip(desktopCfg);
                });
            }
            const fromCfg = desktopCfg && desktopCfg.packageVersion;
            const fromApi = desktopManifest && desktopManifest.packageVersion;
            // Packaged Host Cuttle.exe bakes a stale package.json into app.asar.
            // Titlebar must prefer Flask's live checkout version on Host; Client
            // still prefers its local shell version (update button compares hashes).
            const isClient = !!(desktopCfg && desktopCfg.clientMode);
            applyTitleVersion(isClient ? (fromCfg || fromApi) : (fromApi || fromCfg));
            reportUpdateState();
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
        const connectionPopup = connection && api.desktop
            ? connection.create({
                api: api.desktop,
                doc: document,
                fetchNetworkInfo,
                getConfig: () => desktopCfg,
                onConfig: (cfg, net) => {
                    desktopCfg = cfg || desktopCfg;
                    if (net !== undefined) hostNetInfo = net;
                    applyHostChip(desktopCfg);
                },
            })
            : null;
        hostBtn?.addEventListener('click', () => {
            if (connectionPopup) connectionPopup.open();
            else if (typeof api.desktop?.showConnect === 'function') api.desktop.showConnect();
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
            const hostBranch = String((data && data.host_git_branch) || '').trim();
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
                wrongBranch: !!(me && me.wrong_branch),
                branch: String((me && me.git_branch) || '').trim(),
                hostBranch,
            };
            reportUpdateState();
        };

        const refreshWorkersChip = async () => {
            if (!workersBtn && !updateBtn) return;
            try {
                const r = await fetch('/api/workers', { cache: 'no-store' });
                if (!r.ok) {
                    applyWorkersChip(0, 0);
                    meshUpdate = { ...NO_MESH_UPDATE };
                    reportUpdateState();
                    return;
                }
                const data = await r.json();
                if (!data || data.enabled === false) {
                    applyWorkersChip(0, 0);
                    meshUpdate = { ...NO_MESH_UPDATE };
                    reportUpdateState();
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
                meshUpdate = { ...NO_MESH_UPDATE };
                reportUpdateState();
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
        rescheduleHide = () => {
            if (!document.body.classList.contains('fullscreen')) return;
            if (titlebar.matches(':hover')) return;
            scheduleHideTitlebar();
        };
        cancelHide = () => {
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

    // Renderer zoom lives in ``webFrame`` only, so it does not survive a shell
    // reload on its own. Persist the ladder value per shell origin and re-apply
    // it on startup so a refresh does not snap back to 100%.
    const ZOOM_STORAGE_KEY = 'cuttle.shell.zoomPercent';

    function readPersistedZoomPercent() {
        try {
            const raw = window.localStorage?.getItem(ZOOM_STORAGE_KEY);
            if (raw == null) return null;
            const n = Number(raw);
            return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
        } catch (_) {
            return null;
        }
    }

    function persistZoomPercent(pct) {
        try {
            const n = Number(pct);
            if (Number.isFinite(n) && n > 0) {
                window.localStorage?.setItem(ZOOM_STORAGE_KEY, String(Math.round(n)));
            }
        } catch (_) {}
    }

    function restorePersistedZoom() {
        if (typeof window.electron?.setZoomPercent !== 'function') return;
        const stored = readPersistedZoomPercent();
        if (stored == null || stored === 100) return;
        try {
            window.electron.setZoomPercent(stored);
        } catch (_) {}
    }

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
        const resolved = pct != null ? pct : readZoomPercent();
        persistZoomPercent(resolved);
        showZoomHud(resolved);
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

        // Re-apply the zoom chosen before the last reload (webFrame zoom is
        // frame-scoped and would otherwise reset to 100%).
        restorePersistedZoom();

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

    function rescheduleTitlebarHide() {
        if (typeof rescheduleHide === 'function') rescheduleHide();
    }

    function cancelTitlebarHide() {
        if (typeof cancelHide === 'function') cancelHide();
    }

    /** Handle the shell's zoom postMessages; returns true when consumed. */
    function handleZoomMessage(data) {
        if (!data) return false;
        if (data.type === 'electron-zoom' && root.electron) {
            handleElectronZoomWheel(data.deltaY, data.deltaMode);
            return true;
        }
        if (data.type === 'electron-zoom-key' && root.electron) {
            const now = Date.now();
            if (data.action !== 'reset' && (now - _zoomLastStepAt) < ZOOM_MIN_STEP_MS) return true;
            _zoomLastStepAt = now;
            _zoomWheelAccum = 0;
            applyZoomAction(data.action);
            return true;
        }
        if (data.type === 'browser-zoom-hint') {
            requestAnimationFrame(() => showZoomHud(readZoomPercent()));
            setTimeout(() => showZoomHud(readZoomPercent()), 60);
            return true;
        }
        return false;
    }

    initElectronTitlebar();
    setupShellZoomHud();

    root.CuttleShellElectron = {
        getDesktopConfig: function () { return desktopCfg; },
        initElectronTitlebar: initElectronTitlebar,
        wireElectronChatFind: wireElectronChatFind,
        injectElectronZoomHandler: injectElectronZoomHandler,
        setupShellZoomHud: setupShellZoomHud,
        handleZoomMessage: handleZoomMessage,
        rescheduleTitlebarHide: rescheduleTitlebarHide,
        cancelTitlebarHide: cancelTitlebarHide,
    };
})(typeof window !== 'undefined' ? window : globalThis);
