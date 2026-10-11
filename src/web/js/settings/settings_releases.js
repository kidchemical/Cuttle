/* Release presentation belongs here; Settings only supplies the mount point. */
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
        const deviceUpdateBtn = root.querySelector('[data-device-update]');
        const deviceStatus = root.querySelector('[data-device-update-status]');
        const setDeviceStatus = (text, kind) => {
            if (!deviceStatus) return;
            const msg = String(text || '').trim();
            deviceStatus.textContent = msg;
            deviceStatus.hidden = !msg;
            deviceStatus.classList.toggle('is-error', kind === 'error');
            deviceStatus.classList.toggle('is-ok', kind === 'ok');
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
        button.addEventListener('click', () => load(true));
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
                    if (window.showToast) {
                        window.showToast('Update source set to ' + choice.toUpperCase(), 'success');
                    }
                } catch (error) {
                    if (window.showToast) window.showToast(String(error.message || error), 'error');
                    loadUpdateSource();
                }
            });
            loadUpdateSource();
        }
        // "Update this device": actually pull + restart, unlike the release
        // check above (published GitHub releases). Targets the viewer's own
        // worker (set by app_shell), so it works from a thin Client's Settings.
        function viewerWorkerId() {
            try { return String(sessionStorage.getItem('cuttle_viewer_worker_id') || '').trim().toLowerCase(); }
            catch (_) { return ''; }
        }
        function viewerIsClient() {
            try { return sessionStorage.getItem('cuttle_client_mode') === '1'; }
            catch (_) { return false; }
        }
        async function resolveViewerTarget() {
            let self = '';
            try {
                const r = await fetch('/api/workers', { cache: 'no-store' });
                const data = r.ok ? await r.json() : {};
                self = String((data && data.self_worker_id) || '').trim().toLowerCase();
            } catch (_) { /* fall back to the viewer id below */ }
            const vid = viewerWorkerId();
            return (viewerIsClient() && vid) ? vid : (self || vid);
        }
        if (deviceUpdateBtn) {
            deviceUpdateBtn.addEventListener('click', async () => {
                deviceUpdateBtn.disabled = true;
                setDeviceStatus('Resolving this device…', 'info');
                try {
                    const target = await resolveViewerTarget();
                    if (!target) throw new Error('Could not identify this device on the mesh.');
                    const source = sourceSelect && sourceSelect.value === 'host' ? 'host' : 'official';
                    setDeviceStatus('Updating ' + target + ' (' + source.toUpperCase() + ')…', 'info');
                    const r = await fetch('/api/workers/self-update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            target: target,
                            submitted_by: 'settings-ui',
                            update_source: source,
                            no_daemon: !viewerIsClient(),
                        }),
                    });
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
                    const jobId = String((data.job && (data.job.id || data.job.job_id)) || data.job_id || '').trim();
                    let final = data.job || null;
                    if (jobId) {
                        const deadline = Date.now() + 180000;
                        while (Date.now() < deadline) {
                            await new Promise((res) => setTimeout(res, 1500));
                            try {
                                const sr = await fetch('/api/workers/jobs/' + encodeURIComponent(jobId), { cache: 'no-store' });
                                const sj = await sr.json().catch(() => ({}));
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
                    setDeviceStatus(
                        (jr.advanced === false)
                            ? (jr.note || 'Already at the latest — restarted.')
                            : ('Updated to ' + (jr.head || 'the latest')
                                + '. If Cuttle does not restart on its own, quit and reopen it to finish applying.'),
                        'ok'
                    );
                } catch (error) {
                    setDeviceStatus(String(error.message || error), 'error');
                } finally {
                    deviceUpdateBtn.disabled = false;
                }
            });
        }
        if (autoEnabled) load(false);
        else showManualOnly();
    }
    window.CuttleSettingsReleases = { mount };
}());
