/* Cuttle Mobile native shell (Capacitor / Android WebView on the phone):
 * native-client detection and the LAN APK update rail + modal.
 *
 * Owned here; app_shell.js loads after this file. The mobile detection and
 * server-switch helpers stay top-level function declarations because the
 * shell's frame-retry spine and shared/auth.js's server-row sync call them,
 * and ``window.cuttleSyncMobileServerRows`` is an external contract.
 */
'use strict';

function isCuttleMobileShell() {
    try {
        if (window.isCuttleMobile || window.cuttleMobile?.isNative) return true;
    } catch (_) {}
    return /\bCuttleMobile\//.test(navigator.userAgent || '');
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
                if (typeof window.cuttleRailInteractionLocked === 'function'
                    && window.cuttleRailInteractionLocked()) return;
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

    // The native app keeps its startup screen up until the first page is
    // revealed, so restored layout (collapsed sidebar, last chat) never flashes.
    document.addEventListener('cuttle-shell-frame-revealed', () => {
        try { window.CuttleShellNative?.startupReady?.(); } catch (_) {}
        syncStartupColors();
    }, { once: true });

    // The native startup screen is drawn before any page loads, so it uses the
    // theme colours reported here on the previous run.
    function cssColorToHex(value) {
        const m = /^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?/.exec(String(value || ''));
        if (!m || (m[4] !== undefined && Number(m[4]) < 0.5)) return '';
        return '#' + [m[1], m[2], m[3]]
            .map((n) => Math.min(255, Number(n)).toString(16).padStart(2, '0')).join('');
    }

    function syncStartupColors() {
        try {
            const native = window.CuttleShellNative;
            if (!native?.setStartupColors || !document.body) return;
            // What the phone actually shows is the page in the first pane; the
            // shell's rail colour stands in when that page paints no solid colour.
            let background = '';
            try {
                const page = document.querySelector('.shell-main iframe')?.contentDocument?.body;
                if (page) background = cssColorToHex(window.getComputedStyle(page).backgroundColor);
            } catch (_) {}
            const probe = document.createElement('span');
            probe.style.cssText = 'position:absolute;visibility:hidden;'
                + 'background-color:var(--rail-bg);color:var(--accent)';
            document.body.appendChild(probe);
            const style = window.getComputedStyle(probe);
            background = background || cssColorToHex(style.backgroundColor);
            const accent = cssColorToHex(style.color);
            probe.remove();
            if (background && accent) native.setStartupColors(background, accent);
        } catch (_) {}
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') syncStartupColors();
    });

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
