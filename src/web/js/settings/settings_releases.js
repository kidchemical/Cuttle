/* Release presentation belongs here; Settings only supplies the mount point.
 * Choosing and applying an update belongs to the shell update center
 * (js/shell/shell_updates.js): "Check for updates" opens it. This file shows
 * the published-release status inline and the Developer-only rows. */
(function () {
    'use strict';
    function mount(root) {
        if (!root) return;
        const AUTO_CHECK_KEY = 'cuttleAutoUpdateChecks';
        const status = root.querySelector('[data-release-status]');
        const version = root.querySelector('[data-release-version]');
        const checked = root.querySelector('[data-release-checked]');
        const notes = root.querySelector('[data-release-notes]');
        const details = root.querySelector('details');
        const title = root.querySelector('[data-release-title]');
        const link = root.querySelector('[data-release-link]');
        const button = root.querySelector('[data-release-check]');
        const autoToggle = root.querySelector('[data-release-auto-toggle]');
        const autoCheckbox = root.querySelector('[data-release-auto-check]');
        const sourceSelect = root.querySelector('[data-update-source]');
        const devModeRow = root.querySelector('[data-developer-mode-row]');
        const devModeToggle = root.querySelector('[data-developer-mode-toggle]');
        const devModeCheckbox = root.querySelector('[data-developer-mode-check]');
        const devRows = root.querySelectorAll('[data-developer-row]');
        const androidStatus = root.querySelector('[data-android-status]');
        const androidBtn = root.querySelector('[data-android-rebuild]');
        const setAndroidStatus = (text, kind) => {
            if (!androidStatus) return;
            const msg = String(text || '').trim();
            androidStatus.textContent = msg;
            androidStatus.hidden = !msg;
            androidStatus.classList.toggle('is-error', kind === 'error');
            androidStatus.classList.toggle('is-ok', kind === 'ok');
        };
        let identity = '';

        function autoChecksEnabled() {
            try { return window.localStorage.getItem(AUTO_CHECK_KEY) !== '0'; }
            catch (_) { return true; }
        }

        function paintAutoCheck(enabled) {
            if (autoToggle) autoToggle.classList.toggle('active', enabled);
            if (autoCheckbox) autoCheckbox.checked = enabled;
        }

        function showManualOnly() {
            status.textContent = 'Automatic checks are off. Use Check for updates to check manually.';
            checked.textContent = 'Manual checks remain available.';
        }

        async function load(force) {
            button.disabled = true;
            status.textContent = 'Checking published releases…';
            try {
                const response = await fetch('/api/settings/releases' + (force ? '?force=1' : ''));
                if (!response.ok) throw new Error('Release check unavailable');
                const data = await response.json();
                identity = 'Cuttle v' + (data.current_version || 'unknown') +
                    (data.git_rev ? ' · ' + data.git_rev : '');
                version.textContent = identity;
                const messages = {
                    available: 'New release available: v' + (data.release && data.release.version),
                    latest: 'This checkout matches the latest published release version.',
                    ahead: 'This checkout’s version is ahead of the latest published release.',
                    no_release: 'No published release found.',
                    unknown: 'Published release status is unavailable.'
                };
                status.textContent = data.error ? data.error + (data.release ? ' Showing saved release notes.' : '') : messages[data.state];
                checked.textContent = data.checked_at ? 'Last successful check: ' +
                    new Date(data.checked_at * 1000).toLocaleString() + (data.stale ? ' (saved result)' : '') : 'No successful check yet.';
                details.hidden = !data.release;
                if (data.release) {
                    title.textContent = data.release.title + (data.release.published_at ? ' · ' + new Date(data.release.published_at).toLocaleDateString() : '');
                    // Remote markdown is displayed as text, never executable HTML.
                    notes.textContent = data.release.notes || 'No release notes were provided.';
                    const url = new URL(data.release.url);
                    link.href = url.origin === 'https://github.com' && url.pathname.startsWith('/kidchemical/Cuttle/releases/')
                        ? url.href : 'https://github.com/kidchemical/Cuttle/releases';
                }
            } catch (error) {
                status.textContent = 'Release check unavailable. Try again later.';
            } finally {
                button.disabled = false;
            }
        }
        // The shell owns the update modal; a framed Settings page asks it to
        // open (standalone Settings keeps the inline release check only).
        function openUpdateCenter() {
            if (window.top === window) return false;
            try {
                window.top.postMessage({ type: 'cuttle-open-updates' }, window.location.origin);
                return true;
            } catch (_) { return false; }
        }
        function notifyUpdateCenter() {
            if (window.top === window) return;
            try { window.top.postMessage({ type: 'cuttle-updates-changed' }, window.location.origin); }
            catch (_) { /* shell refreshes on its own schedule */ }
        }
        button.addEventListener('click', () => {
            openUpdateCenter();
            load(true);
        });
        const autoEnabled = autoChecksEnabled();
        paintAutoCheck(autoEnabled);
        if (autoToggle) {
            autoToggle.addEventListener('click', (event) => {
                event.preventDefault();
                const enabled = !autoChecksEnabled();
                try { window.localStorage.setItem(AUTO_CHECK_KEY, enabled ? '1' : '0'); }
                catch (_) { /* keep the visual preference for this page */ }
                paintAutoCheck(enabled);
                if (enabled) {
                    load(false);
                    if (window.showToast) window.showToast('Automatic update checks enabled', 'info');
                } else {
                    showManualOnly();
                    if (window.showToast) window.showToast('Automatic update checks disabled', 'info');
                }
            });
        }
        version.addEventListener('click', async () => {
            if (!identity) return;
            try {
                await navigator.clipboard.writeText(identity);
                checked.textContent = 'Version and revision copied.';
            } catch (error) {
                checked.textContent = 'Could not copy. Select the version text to copy it.';
            }
        });
        async function loadUpdateSource() {
            if (!sourceSelect) return;
            try {
                const response = await fetch('/api/settings/update-source', { cache: 'no-store' });
                if (!response.ok) return;
                const data = await response.json();
                sourceSelect.value = String(data.update_source || 'official') === 'host' ? 'host' : 'official';
            } catch (_) { /* keep default selection */ }
        }
        if (sourceSelect) {
            sourceSelect.addEventListener('change', async () => {
                const choice = sourceSelect.value === 'host' ? 'host' : 'official';
                try {
                    const response = await fetch('/api/settings/update-source', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ update_source: choice }),
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok || !data.success) throw new Error(data.error || 'Failed to save');
                    notifyUpdateCenter();
                    if (window.showToast) {
                        window.showToast('Preferred channel set to ' + (choice === 'host' ? 'HOST' : 'GITHUB'), 'success');
                    }
                } catch (error) {
                    if (window.showToast) window.showToast(String(error.message || error), 'error');
                    loadUpdateSource();
                }
            });
        }
        // Developer mode: source checkouts only. It reveals the branch/Host
        // channels in the update modal and the developer rows below.
        function paintDeveloperMode(mode) {
            const available = !!(mode && mode.available);
            const enabled = !!(mode && mode.enabled);
            if (devModeRow) devModeRow.hidden = !available;
            if (devModeToggle) devModeToggle.classList.toggle('active', enabled);
            if (devModeCheckbox) devModeCheckbox.checked = enabled;
            devRows.forEach((row) => { row.hidden = !enabled; });
            if (enabled) {
                loadUpdateSource();
                loadAndroid();
            }
        }
        let developerMode = null;
        async function loadDeveloperMode() {
            try {
                const r = await fetch('/api/settings/updates?release=0', { cache: 'no-store' });
                const data = r.ok ? await r.json() : null;
                developerMode = (data && data.developer_mode) || null;
            } catch (_) { developerMode = null; }
            paintDeveloperMode(developerMode);
        }
        if (devModeToggle) {
            devModeToggle.addEventListener('click', async (event) => {
                event.preventDefault();
                const enabled = !(developerMode && developerMode.enabled);
                try {
                    const r = await fetch('/api/settings/developer-mode', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enabled }),
                    });
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok || !data.success) throw new Error(data.error || 'Failed to save');
                    developerMode = data.developer_mode;
                    paintDeveloperMode(developerMode);
                    notifyUpdateCenter();
                    if (window.showToast) window.showToast('Developer mode ' + (enabled ? 'on' : 'off'), 'info');
                } catch (error) {
                    if (window.showToast) window.showToast(String(error.message || error), 'error');
                }
            });
        }
        // Phone app (Android/Capacitor): the phone updates itself from the APK
        // this host serves; this row reflects that + can rebuild it.
        async function loadAndroid() {
            if (!androidStatus && !androidBtn) return;
            try {
                const r = await fetch('/api/mobile/android', { cache: 'no-store' });
                const d = await r.json().catch(() => ({}));
                if (!d || d.ok === false) { setAndroidStatus('Phone app status unavailable.', 'error'); return; }
                const ver = d.packageVersion || '?';
                const code = d.versionCode != null ? ' (' + d.versionCode + ')' : '';
                if (d.artifact) {
                    setAndroidStatus('APK v' + ver + code + ' ready · ' + String(d.hash || '').slice(0, 12), 'ok');
                } else if (d.building) {
                    setAndroidStatus('Rebuilding APK… (Gradle build in progress)', 'info');
                } else if (d.rebuildError) {
                    setAndroidStatus('APK not built: ' + d.rebuildError, 'error');
                } else {
                    setAndroidStatus('No APK published — phones will not see an update.', '');
                }
            } catch (_) {
                setAndroidStatus('Phone app status unavailable.', 'error');
            }
        }
        if (androidBtn) {
            androidBtn.addEventListener('click', async () => {
                androidBtn.disabled = true;
                setAndroidStatus('Requesting APK rebuild…', 'info');
                try {
                    const r = await fetch('/api/mobile/android/rebuild', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({}),
                    });
                    const d = await r.json().catch(() => ({}));
                    if (!r.ok || !d.success) throw new Error(d.error || d.reason || ('HTTP ' + r.status));
                    setAndroidStatus('APK rebuild started (Gradle) — this can take a few minutes.', 'info');
                    const deadline = Date.now() + 10 * 60 * 1000;
                    while (Date.now() < deadline) {
                        await new Promise((res) => setTimeout(res, 5000));
                        const sd = await fetch('/api/mobile/android', { cache: 'no-store' })
                            .then((sr) => sr.json()).catch(() => ({}));
                        if (sd.artifact) { setAndroidStatus('APK ready — phones will offer the update.', 'ok'); return; }
                        if (sd.rebuildError) { setAndroidStatus('APK rebuild failed: ' + sd.rebuildError, 'error'); return; }
                        if (!sd.building) { setAndroidStatus('Rebuild finished.', ''); return; }
                    }
                    setAndroidStatus('Still rebuilding — check back shortly.', 'info');
                } catch (error) {
                    setAndroidStatus(String(error.message || error), 'error');
                } finally {
                    androidBtn.disabled = false;
                }
            });
        }
        loadDeveloperMode();
        if (autoEnabled) load(false);
        else showManualOnly();
    }
    window.CuttleSettingsReleases = { mount };
}());
