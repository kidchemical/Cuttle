/* Release presentation belongs here; Settings only supplies the mount point. */
(function () {
    'use strict';
    function mount(root) {
        if (!root) return;
        const status = root.querySelector('[data-release-status]');
        const version = root.querySelector('[data-release-version]');
        const checked = root.querySelector('[data-release-checked]');
        const notes = root.querySelector('[data-release-notes]');
        const details = root.querySelector('details');
        const title = root.querySelector('[data-release-title]');
        const link = root.querySelector('[data-release-link]');
        const button = root.querySelector('[data-release-check]');
        let identity = '';
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
        version.addEventListener('click', async () => {
            if (!identity) return;
            try {
                await navigator.clipboard.writeText(identity);
                checked.textContent = 'Version and revision copied.';
            } catch (error) {
                checked.textContent = 'Could not copy. Select the version text to copy it.';
            }
        });
        load(false);
    }
    window.CuttleSettingsReleases = { mount };
}());
