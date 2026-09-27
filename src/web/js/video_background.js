/**
 * Video Background for Cuttle
 * Wallpaper-Engine style: YouTube playlist with configurable duration before transition
 * Reads from localStorage: cuttleVideoBackgroundList, cuttleVideoBackgroundDuration,
 * cuttleVideoBackgroundOpacity, cuttleVideoBackgroundEnabled; named playlists in
 * cuttleVideoBackgroundPlaylists / cuttleVideoBackgroundActivePlaylist (List mirrors the active one).
 * When embedded in app shell iframe: forwards init to parent; parent owns the video (persists across navigation).
 */
(function() {
    'use strict';

    const LIST_KEY = 'cuttleVideoBackgroundList';
    const DURATION_KEY = 'cuttleVideoBackgroundDuration';
    const OPACITY_KEY = 'cuttleVideoBackgroundOpacity';
    const ENABLED_KEY = 'cuttleVideoBackgroundEnabled';
    const DEFAULT_OPACITY = 45;
    const DEFAULT_DURATION = 60;
    const FADE_MS = 1200;
    /* YouTube auto-hides its startup overlay ~3s into playback; wait past that. */
    const YT_CHROME_SETTLE_MS = 3800;

    function isWallpaperEnabled() {
        try {
            return localStorage.getItem(ENABLED_KEY) !== '0';
        } catch (_) {
            return true;
        }
    }

    function setWallpaperEnabled(enabled) {
        try {
            localStorage.setItem(ENABLED_KEY, enabled ? '1' : '0');
        } catch (_) {}
    }

    /* Named playlists. LIST_KEY always mirrors the active playlist so the player
       (and pre-playlist pages) keep reading a flat URL list. */
    const PLAYLISTS_KEY = 'cuttleVideoBackgroundPlaylists';
    const ACTIVE_PLAYLIST_KEY = 'cuttleVideoBackgroundActivePlaylist';
    const DEFAULT_PLAYLIST = 'default';
    const DEVICE_PLAYLIST = 'this device';
    /* Set while a local playlist edit has not reached settings.json yet (offline,
       or a sibling frame syncing at the same time); local wins until it lands. */
    const DIRTY_KEY = 'cuttleVideoBackgroundPlaylistsDirty';
    /* When the last local POST landed; a GET issued before then may predate it. */
    const PUSHED_AT_KEY = 'cuttleVideoBackgroundPlaylistsPushedAt';

    function setPlaylistsDirty(dirty) {
        try {
            if (dirty) localStorage.setItem(DIRTY_KEY, '1');
            else localStorage.removeItem(DIRTY_KEY);
        } catch (_) {}
    }

    function isPlaylistsDirty() {
        try { return localStorage.getItem(DIRTY_KEY) === '1'; } catch (_) { return false; }
    }

    /** POST prefs to settings.json, clearing the dirty flag only once the server accepted them. */
    function postVideoPrefs(body) {
        setPlaylistsDirty(true);
        if (typeof fetch === 'undefined') return Promise.resolve();
        return fetch('/api/settings/video-background', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) {
            if (!r.ok) return;
            setPlaylistsDirty(false);
            try { localStorage.setItem(PUSHED_AT_KEY, String(Date.now())); } catch (_) {}
        }).catch(function () {});
    }

    function cleanUrls(list) {
        if (!Array.isArray(list)) return [];
        const out = [];
        list.forEach(function (u) {
            const s = String(u == null ? '' : u).trim();
            if (s && out.indexOf(s) < 0) out.push(s);
        });
        return out;
    }

    function cleanPlaylists(raw) {
        const out = {};
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
        const seen = {};
        Object.keys(raw).forEach(function (name) {
            const key = String(name).split(/\s+/).filter(Boolean).join(' ').slice(0, 40);
            if (!key || seen[key.toLowerCase()]) return;
            seen[key.toLowerCase()] = true;
            out[key] = cleanUrls(raw[name]);
        });
        return out;
    }

    function readFlatList() {
        try {
            const arr = JSON.parse(localStorage.getItem(LIST_KEY) || 'null');
            if (Array.isArray(arr)) return cleanUrls(arr);
            const legacy = localStorage.getItem('cuttleVideoBackground');
            if (legacy && legacy.trim()) return [legacy.trim()];
        } catch (_) {}
        return [];
    }

    /** {playlists, active} from localStorage, or null before the first playlist sync. */
    function readPlaylists() {
        let playlists = null;
        try { playlists = cleanPlaylists(JSON.parse(localStorage.getItem(PLAYLISTS_KEY) || 'null')); } catch (_) {}
        if (!playlists || !Object.keys(playlists).length) return null;
        let active = null;
        try { active = localStorage.getItem(ACTIVE_PLAYLIST_KEY); } catch (_) {}
        if (!active || !Object.prototype.hasOwnProperty.call(playlists, active)) active = Object.keys(playlists)[0];
        return { playlists: playlists, active: active };
    }

    function writePlaylists(playlists, active) {
        const clean = cleanPlaylists(playlists);
        if (!Object.keys(clean).length) clean[DEFAULT_PLAYLIST] = [];
        if (!Object.prototype.hasOwnProperty.call(clean, active)) active = Object.keys(clean)[0];
        try {
            localStorage.setItem(PLAYLISTS_KEY, JSON.stringify(clean));
            localStorage.setItem(ACTIVE_PLAYLIST_KEY, active);
            localStorage.setItem(LIST_KEY, JSON.stringify(clean[active]));
        } catch (_) {}
        return { playlists: clean, active: active };
    }

    function pushPlaylistsToServer(extra) {
        const state = readPlaylists() || writePlaylists({ [DEFAULT_PLAYLIST]: readFlatList() }, DEFAULT_PLAYLIST);
        const dur = parseInt(localStorage.getItem(DURATION_KEY), 10);
        const op = parseInt(localStorage.getItem(OPACITY_KEY), 10);
        const body = Object.assign({
            playlists: state.playlists,
            active_playlist: state.active,
            duration: (isNaN(dur) || dur < 5 || dur > 600) ? DEFAULT_DURATION : dur,
            opacity: (isNaN(op) || op < 0 || op > 100) ? DEFAULT_OPACITY : op,
            enabled: isWallpaperEnabled()
        }, extra || {});
        return postVideoPrefs(body);
    }

    /**
     * Fold the server's `video_background` into localStorage. settings.json is the
     * shared copy (every edit POSTs there), so it wins once it has any videos. A
     * pre-playlist device whose flat list matches no server playlist keeps it as
     * "this device" instead of losing it. Pass `requestedAt` (ms, when the GET was
     * sent) so a response older than this device's last save is ignored.
     * Returns {changed, push}.
     */
    function adoptServerPlaylists(s, requestedAt) {
        const before = readFlatList();
        const local = readPlaylists();
        let pushedAt = 0;
        try { pushedAt = parseInt(localStorage.getItem(PUSHED_AT_KEY), 10) || 0; } catch (_) {}
        if (local && requestedAt && requestedAt < pushedAt) return { changed: false, push: false };
        let server = cleanPlaylists(s && s.playlists);
        if (!Object.keys(server).length) server = { [DEFAULT_PLAYLIST]: cleanUrls(s && s.urls) };
        const serverEmpty = Object.keys(server).every(function (k) { return !server[k].length; });
        const same = function (a, b) { return a.length === b.length && a.every(function (u, i) { return u === b[i]; }); };
        let push = false;
        let state;
        if (local && isPlaylistsDirty()) {
            state = local;
            push = true;
        } else if (serverEmpty) {
            state = local || writePlaylists({ [DEFAULT_PLAYLIST]: before }, DEFAULT_PLAYLIST);
            push = Object.keys(state.playlists).some(function (k) { return state.playlists[k].length; });
        } else {
            let active = (s && s.active_playlist) || '';
            if (!Object.prototype.hasOwnProperty.call(server, active)) active = Object.keys(server)[0];
            if (!local && before.length && !Object.keys(server).some(function (k) { return same(server[k], before); })) {
                let name = DEVICE_PLAYLIST;
                for (let i = 2; Object.prototype.hasOwnProperty.call(server, name); i++) name = DEVICE_PLAYLIST + ' ' + i;
                server[name] = before;
                active = name;
                push = true;
            }
            state = writePlaylists(server, active);
            if (push) setPlaylistsDirty(true);
        }
        [[DURATION_KEY, s && s.duration], [OPACITY_KEY, s && s.opacity]].forEach(function (pair) {
            try {
                if (pair[1] != null && localStorage.getItem(pair[0]) === null) localStorage.setItem(pair[0], String(pair[1]));
            } catch (_) {}
        });
        return { changed: !same(before, state.playlists[state.active]), push: push };
    }

    const playlistApi = {
        read: readPlaylists,
        write: writePlaylists,
        adoptServer: adoptServerPlaylists,
        push: pushPlaylistsToServer,
        post: postVideoPrefs,
        defaultName: DEFAULT_PLAYLIST
    };

    /* When in iframe (app shell embeds this page), parent shell owns the video. Forward requests. */
    if (window !== window.top) {
        function applyShellVideoState(active) {
            document.documentElement.classList.toggle('has-video-background', !!active);
            if (document.body) document.body.classList.toggle('has-video-background', !!active);
            try { localStorage.setItem('cuttleVideoBackgroundActive', active ? '1' : '0'); } catch (_) {}
        }
        window.addEventListener('message', function(e) {
            if (e.data && e.data.type === 'cuttle-video-state' && typeof e.data.active === 'boolean') {
                applyShellVideoState(e.data.active);
            }
            if (e.data && e.data.type === 'cuttle-media-state' && typeof e.data.active === 'boolean') {
                if (document.body) document.body.classList.toggle('cuttle-media-mode', e.data.active);
            }
        });
        // Ask parent to re-broadcast in case the load-time postMessage was missed.
        try { window.parent.postMessage({ type: 'cuttle-video-request-state' }, '*'); } catch (_) {}
        window.CuttleVideoBackground = {
            init: function() { window.top.postMessage({ type: 'cuttle-video-reinit' }, '*'); },
            playNow: function(url) { window.top.postMessage({ type: 'cuttle-video-play-now', url: url }, '*'); },
            updateOverlayOpacity: function(val) { window.top.postMessage({ type: 'cuttle-video-update-opacity', value: val }, '*'); },
            setEnabled: function(enabled) {
                setWallpaperEnabled(!!enabled);
                window.top.postMessage({ type: 'cuttle-video-reinit' }, '*');
            },
            isEnabled: isWallpaperEnabled,
            enterMediaMode: function(url, opts) {
                window.top.postMessage(Object.assign({ type: 'cuttle-enter-media-mode', url: url }, opts || {}), '*');
            },
            exitMediaMode: function() { window.top.postMessage({ type: 'cuttle-exit-media-mode' }, '*'); },
            setMediaMuted: function(muted) { window.top.postMessage({ type: 'cuttle-media-set-muted', muted: !!muted }, '*'); },
            playlists: playlistApi,
            getListKey: () => LIST_KEY,
            getDurationKey: () => DURATION_KEY,
            getOpacityKey: () => OPACITY_KEY,
            getEnabledKey: () => ENABLED_KEY
        };
        return;
    }

    const ACTIVE_KEY = 'cuttleVideoBackgroundActive';

    let rotationTimer = null;
    let mediaModeActive = false;

    /* Mirrored so ui_boot.js can make a page transparent before its first paint,
       instead of waiting for the shell's postMessage on iframe load. */
    function rememberActive(active) {
        try { localStorage.setItem(ACTIVE_KEY, active ? '1' : '0'); } catch (_) {}
    }

    function shuffle(arr) {
        const a = arr.slice();
        for (let i = a.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [a[i], a[j]] = [a[j], a[i]];
        }
        return a;
    }

    function parseYouTubeId(url) {
        if (typeof parseYouTubeVideoId === 'function') {
            return parseYouTubeVideoId(url);
        }
        return null;
    }

    function isDirectVideoUrl(url) {
        if (!url || typeof url !== 'string') return false;
        const trimmed = url.trim().toLowerCase();
        return /\.(mp4|webm|ogg)(\?|$)/i.test(trimmed) || trimmed.startsWith('blob:');
    }

    function getUrlList() {
        try {
            const stored = localStorage.getItem(LIST_KEY);
            if (stored) {
                const arr = JSON.parse(stored);
                if (Array.isArray(arr)) {
                    const filtered = arr.filter(u => u && String(u).trim());
                    if (filtered.length) return filtered;
                }
            }
            const legacy = localStorage.getItem('cuttleVideoBackground');
            if (legacy && legacy.trim()) return [legacy.trim()];
        } catch (e) {}
        return [];
    }

    function getDurationSec() {
        const v = localStorage.getItem(DURATION_KEY);
        const n = parseInt(v, 10);
        return (isNaN(n) || n < 5 || n > 600) ? DEFAULT_DURATION : n;
    }

    function getOverlayOpacity() {
        const v = localStorage.getItem(OPACITY_KEY);
        const n = parseInt(v, 10);
        return (isNaN(n) || n < 0 || n > 100) ? DEFAULT_OPACITY / 100 : n / 100;
    }

    function buildMediaForUrl(url, mode) {
        const wallpaper = mode !== 'media';
        const ytId = parseYouTubeId(url);
        if (ytId) {
            const iframe = document.createElement('iframe');
            const loopPart = wallpaper ? `&loop=1&playlist=${ytId}` : '';
            iframe.src = 'https://www.youtube.com/embed/' + encodeURIComponent(ytId) +
                '?autoplay=1&mute=1' + loopPart +
                '&controls=0&rel=0&modestbranding=1&playsinline=1&iv_load_policy=3' +
                '&disablekb=1&fs=0&cc_load_policy=0&enablejsapi=1' +
                '&origin=' + encodeURIComponent(window.location.origin);
            iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen');
            iframe.allowFullscreen = true;
            iframe.title = wallpaper ? 'Background video' : 'Media playback';
            iframe.className = 'cuttle-video-background-media';
            return iframe;
        }
        if (isDirectVideoUrl(url)) {
            const video = document.createElement('video');
            video.autoplay = true;
            video.muted = true;
            video.loop = wallpaper;
            video.playsInline = true;
            video.className = 'cuttle-video-background-media';
            const source = document.createElement('source');
            source.src = url.trim();
            const lower = url.toLowerCase();
            source.type = lower.includes('.webm') ? 'video/webm' : lower.includes('.ogg') ? 'video/ogg' : 'video/mp4';
            video.appendChild(source);
            return video;
        }
        return null;
    }

    function showWhenReady(container) {
        /* Show the dark overlay immediately so transparent pages never flash
           the shell/page fill while YouTube/video media is still loading. */
        container.classList.add('cuttle-video-warming');
        container.classList.add('visible');
        whenMediaPresentable(container, function () {
            container.classList.remove('cuttle-video-warming');
        });
    }

    /**
     * Run `cb` once the clip is safe to show: decoded frames for a direct video,
     * or — for YouTube — playing with its startup overlay (centre transport,
     * corner branding) already auto-hidden. `controls=0` does not suppress that
     * overlay, so the only reliable fix is to stay on the underlay until it goes.
     */
    function whenMediaPresentable(container, cb) {
        const media = container.querySelector('.cuttle-video-background-media');
        if (media && media.tagName === 'VIDEO') {
            if (media.readyState >= 2) {
                cb();
                return;
            }
            let fired = false;
            const done = function () {
                if (fired) return;
                fired = true;
                cb();
            };
            media.addEventListener('canplay', done, { once: true });
            setTimeout(done, 3000);
            return;
        }

        const iframe = container.querySelector('iframe');
        if (!iframe) {
            cb();
            return;
        }

        let fired = false;
        function finish(delayMs) {
            if (fired) return;
            fired = true;
            clearTimeout(hardStop);
            window.removeEventListener('message', onPlayerMessage);
            setTimeout(cb, delayMs);
        }

        function onPlayerMessage(e) {
            if (!e.data || e.source !== iframe.contentWindow) return;
            let data = e.data;
            if (typeof data === 'string') {
                try { data = JSON.parse(data); } catch (_) { return; }
            }
            if (!data || (data.event !== 'onStateChange' && data.event !== 'infoDelivery')) return;
            const info = data.info;
            const state = (info && typeof info === 'object') ? info.playerState : info;
            if (state === 1) finish(YT_CHROME_SETTLE_MS);
        }

        window.addEventListener('message', onPlayerMessage);
        iframe.addEventListener('load', function () {
            try {
                iframe.contentWindow.postMessage(
                    JSON.stringify({ event: 'listening', id: 'cuttle-video-background' }),
                    '*'
                );
            } catch (_) {}
        }, { once: true });

        // Never strand the wallpaper on black when the player API stays quiet.
        const hardStop = setTimeout(function () { finish(0); }, 9000);
    }

    function createContainer(url) {
        const container = document.createElement('div');
        container.className = 'cuttle-video-background';
        const media = buildMediaForUrl(url, 'wallpaper');
        if (!media) return null;
        container.appendChild(media);
        const overlay = document.createElement('div');
        overlay.className = 'cuttle-video-background-overlay';
        overlay.style.background = 'rgba(0, 0, 0, ' + getOverlayOpacity() + ')';
        container.appendChild(overlay);
        return container;
    }

    function createMediaModeContainer(url) {
        const container = document.createElement('div');
        container.className = 'cuttle-video-background cuttle-video-background--media';
        const media = buildMediaForUrl(url, 'media');
        if (!media) return null;
        container.appendChild(media);
        const overlay = document.createElement('div');
        overlay.className = 'cuttle-video-background-overlay';
        const o = getOverlayOpacity();
        overlay.style.background = 'rgba(0, 0, 0, ' + Math.min(o, 0.35) + ')';
        container.appendChild(overlay);
        return container;
    }

    function mountAndShow(container, index) {
        const root = document.getElementById('cuttle-video-background-root');
        if (!root) return;
        container.id = 'cuttle-video-background';
        root.innerHTML = '';
        root.appendChild(container);
        showWhenReady(container);
    }

    function notifyFrameVideoState(active) {
        try {
            const frames = document.querySelectorAll('.shell-main iframe');
            for (const frame of frames) {
                if (frame.contentWindow) {
                    frame.contentWindow.postMessage({ type: 'cuttle-video-state', active }, '*');
                }
            }
        } catch (_) {}
    }

    function notifyFrameMediaState(active) {
        try {
            const frames = document.querySelectorAll('.shell-main iframe');
            for (const frame of frames) {
                if (frame.contentWindow) {
                    frame.contentWindow.postMessage({ type: 'cuttle-media-state', active }, '*');
                }
            }
        } catch (_) {}
    }

    function clearState() {
        if (rotationTimer) {
            clearTimeout(rotationTimer);
            rotationTimer = null;
        }
        mediaModeActive = false;
        const root = document.getElementById('cuttle-video-background-root');
        const old = document.getElementById('cuttle-video-background');
        if (root) root.innerHTML = '';
        if (old) old.remove();
        document.body.classList.remove('has-video-background', 'cuttle-media-mode');
        document.documentElement.classList.remove('has-video-background', 'cuttle-media-mode');
        rememberActive(false);
        notifyFrameVideoState(false);
        notifyFrameMediaState(false);
    }

    function scheduleNext(playOrder, nextIndex, durationMs, originalUrls) {
        rotationTimer = setTimeout(function() {
            rotationTimer = null;
            const url = playOrder[nextIndex];
            const container = createContainer(url);
            if (!container) return;
            const current = document.getElementById('cuttle-video-background');
            if (current) {
                /* Crossfade into the next clip without clearing has-video-background
                   (that would flash opaque page fills across every framed page). */
                container.style.opacity = '0';
                container.classList.add('visible');
                const root = ensureVideoRoot();
                root.appendChild(container);
                container.id = 'cuttle-video-background-incoming';
                // Hold the outgoing clip until the incoming one is past its
                // startup overlay — otherwise every rotation flashes YouTube UI.
                whenMediaPresentable(container, function () {
                    requestAnimationFrame(function () {
                        current.style.transition = 'opacity ' + (FADE_MS / 1000) + 's ease';
                        container.style.transition = 'opacity ' + (FADE_MS / 1000) + 's ease';
                        requestAnimationFrame(function () {
                            current.style.opacity = '0';
                            container.style.opacity = '1';
                        });
                    });
                    setTimeout(function () {
                        try { current.remove(); } catch (_) {}
                        container.id = 'cuttle-video-background';
                        container.classList.add('visible');
                        container.classList.remove('cuttle-video-warming');
                        container.style.opacity = '';
                        const newIdx = nextIndex + 1;
                        if (newIdx >= playOrder.length) {
                            const nextOrder = shuffle(originalUrls);
                            scheduleNext(nextOrder, 0, durationMs, originalUrls);
                        } else {
                            scheduleNext(playOrder, newIdx, durationMs, originalUrls);
                        }
                    }, FADE_MS);
                });
            } else {
                mountAndShow(container, nextIndex);
                const newIdx = nextIndex + 1;
                if (newIdx >= playOrder.length) {
                    const nextOrder = shuffle(originalUrls);
                    scheduleNext(nextOrder, 0, durationMs, originalUrls);
                } else {
                    scheduleNext(playOrder, newIdx, durationMs, originalUrls);
                }
            }
        }, durationMs);
    }

    function ensureVideoRoot() {
        let root = document.getElementById('cuttle-video-background-root');
        if (!root) {
            root = document.createElement('div');
            root.id = 'cuttle-video-background-root';
            root.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;pointer-events:none;overflow:hidden;';
            document.body.insertBefore(root, document.body.firstChild);
        }
        return root;
    }

    function enterMediaMode(url) {
        if (!url || typeof url !== 'string') return;
        if (rotationTimer) {
            clearTimeout(rotationTimer);
            rotationTimer = null;
        }
        mediaModeActive = true;
        const root = ensureVideoRoot();
        root.innerHTML = '';
        const container = createMediaModeContainer(url.trim());
        if (!container) {
            mediaModeActive = false;
            console.warn('[Cuttle] Could not play URL as embedded video (need YouTube or direct .mp4/.webm/.ogg):', url);
            return;
        }
        container.id = 'cuttle-video-background';
        root.appendChild(container);
        document.body.classList.add('has-video-background', 'cuttle-media-mode');
        document.documentElement.classList.add('has-video-background', 'cuttle-media-mode');
        rememberActive(true);
        notifyFrameVideoState(true);
        notifyFrameMediaState(true);
        showWhenReady(container);
    }

    function exitMediaMode() {
        if (!mediaModeActive) return;
        clearState();
        init();
    }

    function setMediaMuted(muted) {
        const v = document.querySelector('#cuttle-video-background video.cuttle-video-background-media');
        if (v) {
            v.muted = !!muted;
            if (!muted) {
                v.play().catch(function() {});
            }
        }
    }

    function playNow(url) {
        url = String(url || '').trim();
        if (!url) return false;
        if (!isWallpaperEnabled()) return false;
        const urls = getUrlList();
        const rest = urls.filter(function (u) { return u !== url; });
        const originals = urls.indexOf(url) >= 0 ? urls.slice() : [url].concat(urls);
        const playOrder = [url].concat(rest.length ? shuffle(rest) : []);
        clearState();
        const first = createContainer(playOrder[0]);
        if (!first) return false;
        document.body.classList.add('has-video-background');
        document.documentElement.classList.add('has-video-background');
        rememberActive(true);
        notifyFrameVideoState(true);
        mountAndShow(first, 0);
        if (originals.length > 1) {
            scheduleNext(playOrder, 1, getDurationSec() * 1000, originals);
        }
        return true;
    }

    function init() {
        const urls = getUrlList();
        clearState();
        if (!isWallpaperEnabled() || !urls.length) return;

        const root = ensureVideoRoot();
        document.body.classList.add('has-video-background');
        document.documentElement.classList.add('has-video-background');
        rememberActive(true);
        notifyFrameVideoState(true);

        const playOrder = urls.length > 1 ? shuffle(urls) : urls;
        const first = createContainer(playOrder[0]);
        if (!first) return;
        mountAndShow(first, 0);

        if (urls.length > 1) {
            const durationSec = getDurationSec();
            const durationMs = durationSec * 1000;
            scheduleNext(playOrder, 1, durationMs, urls);
        }
    }

    function setEnabled(enabled) {
        setWallpaperEnabled(!!enabled);
        init();
    }

    /** Sync playlists with settings.json: restore after a localStorage clear, pick up edits from other devices. */
    function serverHydrateAndSync() {
        if (typeof fetch === 'undefined') return;
        var requestedAt = Date.now();
        fetch('/api/settings/video-background')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.success || !data.video_background) return;
                var s = data.video_background;
                var hadList = getUrlList().length > 0;
                var hydrated = false;
                if (typeof s.enabled === 'boolean') {
                    var localEnabled = null;
                    try { localEnabled = localStorage.getItem(ENABLED_KEY); } catch (_) {}
                    if (localEnabled === null) {
                        setWallpaperEnabled(s.enabled);
                        hydrated = true;
                    }
                }
                var result = adoptServerPlaylists(s, requestedAt);
                if (result.changed) {
                    if (!hadList) {
                        if (s.duration != null) localStorage.setItem(DURATION_KEY, String(s.duration));
                        if (s.opacity != null) localStorage.setItem(OPACITY_KEY, String(s.opacity));
                    }
                    hydrated = true;
                }
                if (result.push) pushPlaylistsToServer();
                if (hydrated) init();
            })
            .catch(function () {});
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            init();
            serverHydrateAndSync();
        });
    } else {
        init();
        serverHydrateAndSync();
    }

    function updateOverlayOpacity(val) {
        const alpha = Math.max(0, Math.min(100, Number(val))) / 100;
        document.querySelectorAll('.cuttle-video-background-overlay').forEach(function (overlay) {
            overlay.style.background = 'rgba(0, 0, 0, ' + alpha + ')';
        });
        try { localStorage.setItem(OPACITY_KEY, String(Math.max(0, Math.min(100, Math.round(Number(val)))))); } catch (_) {}
    }

    window.CuttleVideoBackground = {
        init,
        playNow,
        updateOverlayOpacity,
        setEnabled,
        isEnabled: isWallpaperEnabled,
        enterMediaMode,
        exitMediaMode,
        setMediaMuted,
        playlists: playlistApi,
        getListKey: () => LIST_KEY,
        getDurationKey: () => DURATION_KEY,
        getOpacityKey: () => OPACITY_KEY,
        getEnabledKey: () => ENABLED_KEY
    };
})();
