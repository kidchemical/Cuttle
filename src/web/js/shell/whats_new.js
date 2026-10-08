/* First-launch published release notes for the app shell.
 * The release API remains the source of truth; this module only decides when
 * to present the notes and remembers the last version dismissed on this device.
 */
(function (root) {
    'use strict';

    const AUTO_CHECK_KEY = 'cuttleAutoUpdateChecks';
    const LAST_SEEN_KEY = 'cuttleLastSeenReleaseVersion';
    const RELEASES_URL = 'https://github.com/kidchemical/Cuttle/releases';

    function localStorageValue(key) {
        try { return root.localStorage.getItem(key); } catch (_) { return null; }
    }

    function autoChecksEnabled() {
        return localStorageValue(AUTO_CHECK_KEY) !== '0';
    }

    function normalizeVersion(value) {
        return String(value || '').trim().replace(/^v/i, '');
    }

    function start() {
        const doc = root.document;
        const modal = doc && doc.getElementById('cuttleWhatsNewModal');
        if (!modal || !autoChecksEnabled()) return;

        const versionEl = modal.querySelector('[data-whats-new-version]');
        const notesEl = modal.querySelector('[data-whats-new-notes]');
        const linkEl = modal.querySelector('[data-whats-new-link]');
        const dismissers = modal.querySelectorAll('[data-whats-new-dismiss]');
        let version = '';

        function dismiss() {
            if (version) {
                try { root.localStorage.setItem(LAST_SEEN_KEY, version); } catch (_) {}
            }
            modal.hidden = true;
        }

        dismissers.forEach((el) => el.addEventListener('click', dismiss));
        modal.addEventListener('click', (event) => {
            if (event.target === modal) dismiss();
        });
        doc.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !modal.hidden) dismiss();
        });

        root.fetch('/api/settings/releases', {
            credentials: 'same-origin',
            cache: 'no-store',
        }).then((response) => {
            if (!response.ok) throw new Error('release check unavailable');
            return response.json();
        }).then((data) => {
            const current = normalizeVersion(data && data.current_version);
            const release = data && data.release;
            const published = normalizeVersion(release && release.version);
            if (!current || !release || !published || current !== published) return;
            if (localStorageValue(LAST_SEEN_KEY) === current) return;

            version = current;
            if (versionEl) versionEl.textContent = 'v' + current;
            if (notesEl) notesEl.textContent = String(release.notes || 'No release notes were provided.');
            if (linkEl) {
                try {
                    const url = new URL(String(release.url || ''), root.location.origin);
                    linkEl.href = url.origin === 'https://github.com'
                        && url.pathname.startsWith('/kidchemical/Cuttle/releases/')
                        ? url.href : RELEASES_URL;
                } catch (_) { linkEl.href = RELEASES_URL; }
            }
            modal.hidden = false;
            const close = modal.querySelector('[data-whats-new-dismiss]');
            if (close && typeof close.focus === 'function') close.focus();
        }).catch(() => { /* Release notes are optional and never block shell boot. */ });
    }

    root.CuttleWhatsNew = { start };
    if (root.document && root.document.readyState === 'loading') {
        root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else if (root.document) {
        start();
    }
})(globalThis);
