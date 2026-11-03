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

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    const MD_LINK_RE = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;
    const BARE_URL_RE = /(^|[\s(])(https?:\/\/[^\s<>()"'`]*[^\s<>()"'`.,;:!?])/g;
    const LINK_TOKEN_RE = /\u0000(\d+)\u0000/g;

    /** Release notes are GitHub markdown. Escape everything, render with the
     *  chat's pure block renderer (CuttleChatMarkdown), and add http(s) links
     *  through placeholders so inline emphasis never rewrites a URL. Without
     *  the renderer the notes stay plain text. */
    function renderNotes(el, notes) {
        const text = String(notes || '').replace(/\r\n?/g, '\n').replace(/\u0000/g, '').trim()
            || 'No release notes were provided.';
        const md = root.CuttleChatMarkdown;
        if (!md || typeof md.renderMarkdownBlocks !== 'function') {
            el.classList.add('is-plain');
            el.textContent = text;
            return;
        }
        const links = [];
        const stash = (label, href) => {
            links.push('<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">'
                + escapeHtml(label) + '</a>');
            return '\u0000' + (links.length - 1) + '\u0000';
        };
        const tokenized = text
            .replace(MD_LINK_RE, (_, label, href) => stash(label, href))
            .replace(BARE_URL_RE, (_, lead, href) => lead + stash(href, href));
        el.classList.remove('is-plain');
        el.innerHTML = md.renderMarkdownBlocks(escapeHtml(tokenized))
            .replace(LINK_TOKEN_RE, (_, i) => links[Number(i)] || '');
    }

    function start() {
        const doc = root.document;
        const modal = doc && doc.getElementById('cuttleWhatsNewModal');
        if (!modal || !autoChecksEnabled()) return;

        const versionEl = modal.querySelector('[data-whats-new-version]');
        const notesEl = modal.querySelector('[data-whats-new-notes]');
        const linkEl = modal.querySelector('[data-whats-new-link]');
        const dismissers = modal.querySelectorAll('[data-whats-new-dismiss]');
        const shared = root.CuttleModal && typeof root.CuttleModal.create === 'function'
            ? root.CuttleModal.create(modal) : null;
        let version = '';
        let returnFocus = null;

        function isOpen() { return !modal.hidden; }

        function open() {
            returnFocus = doc.activeElement;
            if (shared) shared.open();
            else { modal.hidden = false; modal.classList.add('active'); }
        }

        function dismiss() {
            if (version) {
                try { root.localStorage.setItem(LAST_SEEN_KEY, version); } catch (_) {}
            }
            if (shared) shared.close();
            else { modal.classList.remove('active'); modal.hidden = true; }
            if (returnFocus && typeof returnFocus.focus === 'function' && doc.contains(returnFocus)) {
                try { returnFocus.focus(); } catch (_) {}
            }
            returnFocus = null;
        }

        dismissers.forEach((el) => el.addEventListener('click', dismiss));
        modal.addEventListener('click', (event) => {
            if (event.target === modal) dismiss();
        });
        doc.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && isOpen()) dismiss();
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
            if (notesEl) renderNotes(notesEl, release.notes);
            if (linkEl) {
                try {
                    const url = new URL(String(release.url || ''), root.location.origin);
                    linkEl.href = url.origin === 'https://github.com'
                        && url.pathname.startsWith('/kidchemical/Cuttle/releases/')
                        ? url.href : RELEASES_URL;
                } catch (_) { linkEl.href = RELEASES_URL; }
            }
            open();
            // Focus the scrollable notes (not a dismiss button) so arrow keys
            // scroll and an Enter typed mid-boot does not close the dialog.
            if (notesEl && typeof notesEl.focus === 'function') {
                notesEl.scrollTop = 0;
                notesEl.focus({ preventScroll: true });
            }
        }).catch(() => { /* Release notes are optional and never block shell boot. */ });
    }

    root.CuttleWhatsNew = { start, renderNotes };
    if (root.document && root.document.readyState === 'loading') {
        root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else if (root.document) {
        start();
    }
})(globalThis);
