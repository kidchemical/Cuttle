/* Reads session cuttleMediaMeta; chrome for shell background playback */

(function () {
    const META_KEY = 'cuttleMediaMeta';

    function readMeta() {
        try {
            const raw = sessionStorage.getItem(META_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) {
            return null;
        }
    }

    function goBack() {
        if (window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-exit-media-mode' }, '*');
        } else {
            if (window.CuttleVideoBackground && window.CuttleVideoBackground.exitMediaMode) {
                window.CuttleVideoBackground.exitMediaMode();
            }
            window.location.href = '/chat_page.html';
        }
    }

    const meta = readMeta();
    if (meta && meta.url && window.parent !== window) {
        window.parent.postMessage({
            type: 'cuttle-enter-media-mode',
            url: meta.url,
            title: meta.title,
            resyncOnly: true
        }, '*');
    }
    const titleEl = document.getElementById('mediaTitle');
    const backBtn = document.getElementById('mediaBack');
    const unmuteBtn = document.getElementById('mediaUnmute');
    const externalEl = document.getElementById('mediaExternal');
    const hintEl = document.getElementById('mediaHint');

    if (titleEl && meta && meta.title) {
        titleEl.textContent = meta.title;
    }

    if (backBtn) backBtn.addEventListener('click', goBack);

    if (meta && meta.isDirectVideo && unmuteBtn) {
        unmuteBtn.hidden = false;
        unmuteBtn.addEventListener('click', function () {
            if (window.parent !== window) {
                window.parent.postMessage({ type: 'cuttle-media-set-muted', muted: false }, '*');
            } else if (window.CuttleVideoBackground && window.CuttleVideoBackground.setMediaMuted) {
                window.CuttleVideoBackground.setMediaMuted(false);
            }
            unmuteBtn.textContent = 'Unmuted';
            unmuteBtn.disabled = true;
        });
    }

    if (meta && meta.externalUrl && externalEl) {
        try {
            const u = new URL(meta.externalUrl, window.location.origin);
            if (u.protocol === 'http:' || u.protocol === 'https:') {
                externalEl.href = meta.externalUrl;
                externalEl.hidden = false;
            }
        } catch (_) {}
    }

    if (meta && meta.isYouTube && hintEl) {
        hintEl.textContent = 'Embedded YouTube may stay muted in the background. Use “Open link” for the full watch page with sound.';
        hintEl.hidden = false;
    }

    if (window.parent !== window) {
        window.addEventListener('message', function (e) {
            if (e.data?.type === 'cuttle-theme-change' && e.data?.theme && !window.CuttleUiBoot) {
                document.body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
                if (e.data.theme === 'light') document.body.classList.add('light-mode');
                else if (e.data.theme === 'midnight') document.body.classList.add('midnight-mode');
                else document.body.classList.add('dark-mode');
            }
        });
    }
})();
