/* ================================================================
   Cuttle Chat — media thumbs, lightbox and image menu (chat_media.js).
   Owner: markdown/attachment media thumb HTML, the poster fallback, the
   shared lightbox (gallery, fit/actual/zoom/pan/pinch, captions from the
   staging sidecar `/api/shared-media/meta`, download) and the image
   right-click Copy / Save menu, plus all of their state.

   The lightbox mounts on the host (app-shell) document so it covers every
   split pane; listeners this frame adds there are bound to the page's
   frame-teardown signal (`hostTeardownSignal`), so an unloaded chat frame
   leaves nothing behind on the shell.

   Interface: pure helpers on the namespace (mediaKindFromUrl,
   isSafeMediaSrc, mediaDownloadUrl, …) and `create(deps)` → one controller
   per chat frame with buildMediaThumbHtml, thumbFallback and bind().
   deps: { win, escapeHtmlInline, clamp, hostTeardownSignal, fetch? }.
   The page keeps message formatting and calls in through these.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';


    function mediaKindFromUrl(url) {
        const path = String(url || '').split('?')[0].split('#')[0].toLowerCase();
        if (/\.(mp4|webm|mov|m4v|ogg|ogv)$/i.test(path)) return 'video';
        return 'image';
    }

    /** Conventional mid-frame poster for staged videos: foo.mp4 → foo.poster.jpg */

    function mediaPosterUrl(src) {
        const u = String(src || '').trim().split('?')[0].split('#')[0];
        if (!u.startsWith('/output/shared/')) return '';
        if (!/\.(mp4|webm|mov|m4v|ogg|ogv)$/i.test(u)) return '';
        return u.replace(/\.[^.\/]+$/i, '.poster.jpg');
    }

    function isSafeMediaSrc(url) {
        const u = String(url || '').trim();
        if (!u) return false;
        if (/^(javascript|data|vbscript):/i.test(u)) return false;
        if (/^https?:\/\//i.test(u)) return true;
        if (u.startsWith('/output/')) return true;
        // Same-origin site paths (avatars, static), not protocol-relative
        if (u.charAt(0) === '/' && u.charAt(1) !== '/') return true;
        return false;
    }

    function mediaDownloadUrl(src) {
        const u = String(src || '').trim();
        if (!u) return '';
        if (u.startsWith('/output/')) {
            return u + (u.includes('?') ? '&' : '?') + 'download=1';
        }
        return u;
    }

    function mediaDownloadFilename(src, title) {
        const t = String(title || '').trim();
        if (t && /\.[a-z0-9]{2,5}$/i.test(t)) return t;
        try {
            const path = String(src || '').split('?')[0];
            const base = path.split('/').filter(Boolean).pop() || 'download';
            return decodeURIComponent(base);
        } catch (_) {
            return 'download';
        }
    }

    /** Parse ``url`` / ``url "description"`` / ``url 'description'`` from markdown image dest. */

    /** True when alt/title looks like prose context, not a bare filename. */
    function mediaAltLooksLikeDescription(text) {
        const s = String(text || '').trim();
        if (!s) return false;
        if (/^[\w.\-]+\.[a-z0-9]{2,5}$/i.test(s)) return false;
        if (/-[a-f0-9]{8,12}\.[^.]+$/i.test(s)) return false;
        if (s.length >= 28) return true;
        return /\s/.test(s);
    }

    function mediaBasenameFromSrc(src) {
        try {
            const path = String(src || '').split('?')[0].split('#')[0];
            return decodeURIComponent(path.split('/').filter(Boolean).pop() || '') || '';
        } catch (_) {
            return '';
        }
    }

    /** Strip staging suffix: ``Jambit_intro-44c426b348.mp4`` → ``Jambit_intro.mp4``. */

    function originalNameFromStaged(srcOrName) {
        const base = mediaBasenameFromSrc(srcOrName) || String(srcOrName || '');
        return base.replace(/-[a-f0-9]{8,12}(\.[^.]+)$/i, '$1');
    }

    function blobToPngBlob(blob, win) {
        return new Promise(function (resolve, reject) {
            if (!blob) {
                reject(new Error('No image data'));
                return;
            }
            if (blob.type === 'image/png') {
                resolve(blob);
                return;
            }
            const w = win || globalThis;
            const doc = w.document;
            const url = (w.URL || URL).createObjectURL(blob);
            const img = new (w.Image || Image)();
            img.onload = function () {
                try {
                    const canvas = doc.createElement('canvas');
                    canvas.width = img.naturalWidth || img.width;
                    canvas.height = img.naturalHeight || img.height;
                    if (!canvas.width || !canvas.height) {
                        (w.URL || URL).revokeObjectURL(url);
                        reject(new Error('Empty image'));
                        return;
                    }
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    canvas.toBlob(function (png) {
                        (w.URL || URL).revokeObjectURL(url);
                        if (png) resolve(png);
                        else reject(new Error('PNG encode failed'));
                    }, 'image/png');
                } catch (err) {
                    (w.URL || URL).revokeObjectURL(url);
                    reject(err);
                }
            };
            img.onerror = function () {
                (w.URL || URL).revokeObjectURL(url);
                reject(new Error('Image load failed'));
            };
            img.src = url;
        });
    }

    /**
     * One media controller per chat frame.
     * @param {{win: Window, escapeHtmlInline: Function, clamp: Function,
     *          hostTeardownSignal: AbortSignal, fetch?: Function}} deps
     */
    function create(deps) {
        const frameWin = deps.win;
        const frameDoc = frameWin.document;
        const escapeHtmlInline = deps.escapeHtmlInline;
        const clamp = deps.clamp;
        const hostTeardownSignal = deps.hostTeardownSignal;
        const fetchFn = deps.fetch || frameWin.fetch.bind(frameWin);

        function buildMediaThumbHtml(src, opts) {
            const o = opts || {};
            const rawSrc = String(src || '').trim();
            if (!isSafeMediaSrc(rawSrc)) {
                return escapeHtmlInline(o.alt || o.title || rawSrc || '');
            }
            const kind = o.kind || mediaKindFromUrl(rawSrc);
            const title = escapeHtmlInline(o.title || o.alt || '');
            const description = String(o.description || '').trim();
            const safeDesc = description ? escapeHtmlInline(description) : '';
            const safeSrc = escapeHtmlInline(rawSrc).replace(/"/g, '&quot;');
            const dl = escapeHtmlInline(mediaDownloadUrl(rawSrc)).replace(/"/g, '&quot;');
            const poster = (o.poster && isSafeMediaSrc(o.poster))
                ? String(o.poster).trim()
                : (kind === 'video' ? mediaPosterUrl(rawSrc) : '');
            const safePoster = poster ? escapeHtmlInline(poster).replace(/"/g, '&quot;') : '';
            const cls = 'cuttle-media-thumb' + (kind === 'video' ? ' cuttle-media-thumb--video' : '');
            let inner;
            if (kind === 'video') {
                // Prefer mid-frame poster JPEG; fall back to muted video frame if missing.
                if (safePoster) {
                    inner =
                        '<img class="cuttle-media-poster" src="' +
                        safePoster +
                        '" alt="' +
                        title +
                        '" loading="lazy" onerror="window.CuttleMediaThumbFallback&&window.CuttleMediaThumbFallback(this)">' +
                        '<span class="cuttle-media-play" aria-hidden="true">▶</span>';
                } else {
                    inner =
                        '<video src="' +
                        safeSrc +
                        '" muted preload="metadata" playsinline></video>' +
                        '<span class="cuttle-media-play" aria-hidden="true">▶</span>';
                }
            } else {
                inner =
                    '<img src="' +
                    safeSrc +
                    '" alt="' +
                    title +
                    '" loading="lazy" onerror="this.closest(\'.cuttle-media-thumb\')?.classList.add(\'cuttle-media-thumb--broken\')">';
            }
            const tip = safeDesc || title || 'Open media';
            let html =
                '<button type="button" class="' +
                cls +
                '" data-src="' +
                safeSrc +
                '" data-kind="' +
                escapeHtmlInline(kind) +
                '" data-title="' +
                title +
                '"' +
                (safeDesc ? ' data-description="' + safeDesc.replace(/"/g, '&quot;') + '"' : '') +
                (safePoster ? ' data-poster="' + safePoster + '"' : '') +
                ' data-download="' +
                dl +
                '" title="' +
                tip +
                '">' +
                inner +
                '</button>';
            if (safeDesc) {
                html =
                    '<figure class="cuttle-media-figure">' +
                    html +
                    '<figcaption class="cuttle-media-caption">' +
                    safeDesc +
                    '</figcaption>' +
                    '</figure>';
            }
            return html;
        }

        // Poster missing / broken → swap to <video preload=metadata> stills.

        function thumbFallback(imgEl) {
            try {
                const thumb = imgEl && imgEl.closest && imgEl.closest('.cuttle-media-thumb');
                if (!thumb) return;
                const src = thumb.getAttribute('data-src') || '';
                if (!src) {
                    thumb.classList.add('cuttle-media-thumb--broken');
                    return;
                }
                const video = frameDoc.createElement('video');
                video.src = src;
                video.muted = true;
                video.preload = 'metadata';
                video.setAttribute('playsinline', '');
                imgEl.replaceWith(video);
            } catch (_) {
                try {
                    imgEl.closest('.cuttle-media-thumb')?.classList.add('cuttle-media-thumb--broken');
                } catch (__) {}
            }
        }

        let _cuttleLightbox = null;

        let _lightboxItems = [];

        let _lightboxIndex = 0;
        // Zoom: mode 'fit' (contain) or 'actual' (1:1 px); userZoom multiplies the base.

        let _lightboxMode = 'fit';

        let _lightboxUserZoom = 1;

        let _lightboxFitScale = 1;

        let _lightboxNatW = 0;

        let _lightboxNatH = 0;

        let _lightboxPanX = 0;

        let _lightboxPanY = 0;

        let _lightboxDrag = null;
        /** Active pointers for pan + pinch (pointerId → {x,y}). */

        let _lightboxPointers = new Map();

        let _lightboxPinch = null;

        const _LIGHTBOX_ZOOM_MIN = 0.25; // 25% of natural pixels

        const _LIGHTBOX_ZOOM_MAX = 8;

        const _LIGHTBOX_ZOOM_STEP = 1.25;

        let _lightboxHostKeyBound = false;

        let _lightboxHostResizeBound = false;

        /** Prefer the app-shell parent so the overlay covers every split pane. */

        function lightboxHostWindow() {
            try {
                if (frameWin.parent && frameWin.parent !== frameWin && frameWin.parent.document && frameWin.parent.document.body) {
                    // Same-origin probe (throws if cross-origin).
                    void frameWin.parent.document.documentElement;
                    return frameWin.parent;
                }
            } catch (_) {}
            return frameWin;
        }

        function lightboxHostDocument() {
            try {
                return lightboxHostWindow().document;
            } catch (_) {
                return frameDoc;
            }
        }

        function lightboxSetOpenClass(on) {
            const docs = [frameDoc];
            try {
                const hostDoc = lightboxHostDocument();
                if (hostDoc && hostDoc !== frameDoc) docs.push(hostDoc);
            } catch (_) {}
            docs.forEach(function (doc) {
                try {
                    if (on) doc.documentElement.classList.add('cuttle-lightbox-open');
                    else doc.documentElement.classList.remove('cuttle-lightbox-open');
                } catch (_) {}
            });
        }

        function ensureCuttleLightbox() {
            const hostDoc = lightboxHostDocument();
            const hostBody = hostDoc.body || frameDoc.body;

            // Another chat pane may have left a shell lightbox — drop it so this
            // pane owns the handlers (avoid stale closures from a sibling iframe).
            if (!_cuttleLightbox || !hostBody.contains(_cuttleLightbox)) {
                try {
                    const existing = hostDoc.getElementById('cuttle-shell-lightbox');
                    if (existing && existing !== _cuttleLightbox) {
                        existing.remove();
                    }
                } catch (_) {}
                _cuttleLightbox = null;
            }

            if (_cuttleLightbox && hostBody.contains(_cuttleLightbox)) {
                // Rebuild if an older shell is missing caption slots or zoom chrome.
                if (
                    _cuttleLightbox.querySelector('.cuttle-lightbox-path')
                    && _cuttleLightbox.querySelector('.cuttle-lightbox-desc')
                    && _cuttleLightbox.querySelector('.cuttle-lightbox-zoom')
                ) {
                    return _cuttleLightbox;
                }
                try {
                    _cuttleLightbox.remove();
                } catch (_) {}
                _cuttleLightbox = null;
            }

            const el = hostDoc.createElement('div');
            el.id = 'cuttle-shell-lightbox';
            el.className = 'cuttle-lightbox';
            el.hidden = true;
            el.tabIndex = -1;
            el.setAttribute('role', 'dialog');
            el.setAttribute('aria-modal', 'true');
            el.setAttribute('aria-label', 'Media viewer');
            el.innerHTML =
                '<div class="cuttle-lightbox-backdrop" data-lb-action="close"></div>' +
                '<button type="button" class="cuttle-lightbox-close" data-lb-action="close" aria-label="Close">×</button>' +
                '<button type="button" class="cuttle-lightbox-nav cuttle-lightbox-prev" data-lb-action="prev" aria-label="Previous">‹</button>' +
                '<button type="button" class="cuttle-lightbox-nav cuttle-lightbox-next" data-lb-action="next" aria-label="Next">›</button>' +
                '<div class="cuttle-lightbox-stage"></div>' +
                '<div class="cuttle-lightbox-bar">' +
                '<div class="cuttle-lightbox-caption">' +
                '<span class="cuttle-lightbox-title"></span>' +
                '<span class="cuttle-lightbox-desc" hidden></span>' +
                '<span class="cuttle-lightbox-path" hidden></span>' +
                '</div>' +
                '<div class="cuttle-lightbox-zoom" role="group" aria-label="Zoom">' +
                '<button type="button" class="cuttle-lightbox-zoom-btn" data-lb-action="zoom-out" aria-label="Zoom out" title="Zoom out (−)">−</button>' +
                '<button type="button" class="cuttle-lightbox-zoom-toggle" data-lb-action="zoom-toggle" aria-label="Toggle fit and actual size" title="Toggle fit / actual size">Fit</button>' +
                '<button type="button" class="cuttle-lightbox-zoom-btn" data-lb-action="zoom-in" aria-label="Zoom in" title="Zoom in (+)">+</button>' +
                '</div>' +
                '<span class="cuttle-lightbox-counter"></span>' +
                '<a class="cuttle-lightbox-download" href="#" download>Download</a>' +
                '</div>';
            hostBody.appendChild(el);
            el.addEventListener('click', function (e) {
                const btn = e.target && e.target.closest && e.target.closest('[data-lb-action]');
                if (!btn) return;
                const action = btn.getAttribute('data-lb-action');
                if (action === 'close') closeCuttleLightbox();
                else if (action === 'prev') showLightboxIndex(_lightboxIndex - 1);
                else if (action === 'next') showLightboxIndex(_lightboxIndex + 1);
                else if (action === 'zoom-in') lightboxZoomBy(_LIGHTBOX_ZOOM_STEP);
                else if (action === 'zoom-out') lightboxZoomBy(1 / _LIGHTBOX_ZOOM_STEP);
                else if (action === 'zoom-toggle') toggleLightboxSizeMode();
            });
            const stage = el.querySelector('.cuttle-lightbox-stage');
            if (stage) {
                stage.addEventListener('pointerdown', onLightboxPointerDown);
                stage.addEventListener('pointermove', onLightboxPointerMove);
                stage.addEventListener('pointerup', onLightboxPointerUp);
                stage.addEventListener('pointercancel', onLightboxPointerUp);
                stage.addEventListener('wheel', onLightboxWheel, { passive: false });
                stage.addEventListener('dblclick', onLightboxDblClick);
                // Legacy iOS gesture stream (no-ops on Android / Capacitor Chromium).
                stage.addEventListener('gesturestart', onLightboxGesture, { passive: false });
                stage.addEventListener('gesturechange', onLightboxGesture, { passive: false });
                stage.addEventListener('gestureend', onLightboxGesture, { passive: false });
                // Right-click Copy / Save on the full-size image (host doc when shelled).
                stage.addEventListener('contextmenu', onMediaContextMenu);
            }
            _cuttleLightbox = el;
            return el;
        }

        function lightboxMediaEl() {
            const root = _cuttleLightbox;
            if (!root) return null;
            return root.querySelector('.cuttle-lightbox-stage img, .cuttle-lightbox-stage video');
        }

        function lightboxZoomBase() {
            return _lightboxMode === 'actual' ? 1 : (_lightboxFitScale || 1);
        }

        function lightboxEffectiveScale() {
            return lightboxZoomBase() * (_lightboxUserZoom || 1);
        }

        /** Smallest allowed scale vs natural size: 25%, or Fit when Fit is already smaller. */

        function lightboxMinEffectiveScale() {
            const fit = _lightboxFitScale > 0 ? _lightboxFitScale : 1;
            return Math.min(_LIGHTBOX_ZOOM_MIN, fit);
        }

        function resetLightboxZoomState() {
            _lightboxMode = 'fit';
            _lightboxUserZoom = 1;
            _lightboxFitScale = 1;
            _lightboxNatW = 0;
            _lightboxNatH = 0;
            _lightboxPanX = 0;
            _lightboxPanY = 0;
            _lightboxDrag = null;
            _lightboxPinch = null;
            if (_lightboxPointers && _lightboxPointers.clear) _lightboxPointers.clear();
        }

        function lightboxPinchDistance(pts) {
            const arr = [];
            pts.forEach(function (p) { arr.push(p); });
            if (arr.length < 2) return 0;
            const dx = arr[0].x - arr[1].x;
            const dy = arr[0].y - arr[1].y;
            return Math.hypot(dx, dy) || 0;
        }

        function lightboxPinchMidpoint(pts) {
            const arr = [];
            pts.forEach(function (p) { arr.push(p); });
            if (arr.length < 2) return { x: 0, y: 0 };
            return {
                x: (arr[0].x + arr[1].x) / 2,
                y: (arr[0].y + arr[1].y) / 2,
            };
        }

        function lightboxSetEffectiveScale(nextEff) {
            const base = Math.max(lightboxZoomBase(), 0.0001);
            const clamped = clamp(
                nextEff,
                lightboxMinEffectiveScale(),
                _LIGHTBOX_ZOOM_MAX
            );
            _lightboxUserZoom = clamped / base;
        }

        function measureLightboxFitScale() {
            const root = _cuttleLightbox;
            const media = lightboxMediaEl();
            if (!root || !media) return;
            const natW = media.naturalWidth || media.videoWidth || 0;
            const natH = media.naturalHeight || media.videoHeight || 0;
            if (!natW || !natH) return;
            _lightboxNatW = natW;
            _lightboxNatH = natH;
            const stage = root.querySelector('.cuttle-lightbox-stage');
            const sw = (stage && stage.clientWidth) || Math.max(1, lightboxHostWindow().innerWidth - 112);
            const sh = (stage && stage.clientHeight) || Math.max(1, lightboxHostWindow().innerHeight - 160);
            _lightboxFitScale = Math.min(sw / natW, sh / natH, 1);
            if (!Number.isFinite(_lightboxFitScale) || _lightboxFitScale <= 0) {
                _lightboxFitScale = 1;
            }
        }

        function clampLightboxPan() {
            const media = lightboxMediaEl();
            if (!media || !_lightboxNatW || !_lightboxNatH) {
                _lightboxPanX = 0;
                _lightboxPanY = 0;
                return;
            }
            const root = _cuttleLightbox;
            const stage = root && root.querySelector('.cuttle-lightbox-stage');
            const sw = (stage && stage.clientWidth) || lightboxHostWindow().innerWidth;
            const sh = (stage && stage.clientHeight) || lightboxHostWindow().innerHeight;
            const scale = lightboxEffectiveScale();
            const dispW = _lightboxNatW * scale;
            const dispH = _lightboxNatH * scale;
            const maxX = Math.max(0, (dispW - sw) / 2);
            const maxY = Math.max(0, (dispH - sh) / 2);
            _lightboxPanX = clamp(_lightboxPanX, -maxX, maxX);
            _lightboxPanY = clamp(_lightboxPanY, -maxY, maxY);
        }

        function updateLightboxZoomUi() {
            const root = _cuttleLightbox;
            if (!root) return;
            const toggle = root.querySelector('.cuttle-lightbox-zoom-toggle');
            const pct = Math.round(lightboxEffectiveScale() * 100);
            if (toggle) {
                if (_lightboxMode === 'actual' && Math.abs(_lightboxUserZoom - 1) < 0.02) {
                    toggle.textContent = 'Actual';
                    toggle.title = 'Switch to fit (default size)';
                    toggle.setAttribute('aria-pressed', 'true');
                } else if (_lightboxMode === 'fit' && Math.abs(_lightboxUserZoom - 1) < 0.02) {
                    toggle.textContent = 'Fit';
                    toggle.title = 'Switch to actual size (1:1)';
                    toggle.setAttribute('aria-pressed', 'false');
                } else {
                    toggle.textContent = pct + '%';
                    toggle.title = _lightboxMode === 'actual'
                        ? 'Reset to actual size'
                        : 'Reset to fit (default size)';
                    toggle.setAttribute('aria-pressed', _lightboxMode === 'actual' ? 'true' : 'false');
                }
            }
            const stage = root.querySelector('.cuttle-lightbox-stage');
            if (stage) {
                const scale = lightboxEffectiveScale();
                const canPan = !!(_lightboxNatW && _lightboxNatH
                    && ((_lightboxNatW * scale > (stage.clientWidth || 0) + 1)
                        || (_lightboxNatH * scale > (stage.clientHeight || 0) + 1)));
                stage.classList.toggle('is-pannable', canPan);
            }
        }

        function applyLightboxZoom() {
            const media = lightboxMediaEl();
            if (!media) return;
            if (!_lightboxNatW || !_lightboxNatH) {
                measureLightboxFitScale();
            }
            if (!_lightboxNatW || !_lightboxNatH) return;
            clampLightboxPan();
            const scale = lightboxEffectiveScale();
            media.style.width = _lightboxNatW + 'px';
            media.style.height = _lightboxNatH + 'px';
            media.style.maxWidth = 'none';
            media.style.maxHeight = 'none';
            media.style.transform =
                'translate(' + _lightboxPanX + 'px,' + _lightboxPanY + 'px) scale(' + scale + ')';
            media.style.transformOrigin = 'center center';
            updateLightboxZoomUi();
        }

        function lightboxZoomBy(factor) {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            lightboxSetEffectiveScale(lightboxEffectiveScale() * factor);
            applyLightboxZoom();
        }

        function setLightboxSizeMode(mode) {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            measureLightboxFitScale();
            _lightboxMode = mode === 'actual' ? 'actual' : 'fit';
            _lightboxUserZoom = 1;
            _lightboxPanX = 0;
            _lightboxPanY = 0;
            applyLightboxZoom();
        }

        function toggleLightboxSizeMode() {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            // If already zoomed away from the mode's 1x, first reset to that mode's default.
            if (Math.abs(_lightboxUserZoom - 1) > 0.02) {
                _lightboxUserZoom = 1;
                _lightboxPanX = 0;
                _lightboxPanY = 0;
                applyLightboxZoom();
                return;
            }
            setLightboxSizeMode(_lightboxMode === 'actual' ? 'fit' : 'actual');
        }

        function onLightboxMediaReady(media) {
            if (!media) return;
            const apply = function () {
                measureLightboxFitScale();
                _lightboxUserZoom = 1;
                _lightboxPanX = 0;
                _lightboxPanY = 0;
                applyLightboxZoom();
            };
            if (media.tagName === 'VIDEO') {
                if (media.videoWidth) apply();
                else media.addEventListener('loadedmetadata', apply, { once: true });
            } else if (media.complete && media.naturalWidth) {
                apply();
            } else {
                media.addEventListener('load', apply, { once: true });
            }
        }

        function onLightboxPointerDown(e) {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            if (e.button != null && e.button !== 0) return;
            const stage = e.currentTarget;
            if (!stage) return;
            // Don't steal gestures from native video controls.
            const media = lightboxMediaEl();
            if (!media || media.tagName === 'VIDEO') return;

            _lightboxPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
            try {
                stage.setPointerCapture(e.pointerId);
            } catch (_) {}

            if (_lightboxPointers.size >= 2) {
                // Two fingers → pinch (works from Fit, not only when already pannable).
                _lightboxDrag = null;
                const dist = lightboxPinchDistance(_lightboxPointers) || 1;
                const mid = lightboxPinchMidpoint(_lightboxPointers);
                _lightboxPinch = {
                    startDist: dist,
                    startEff: lightboxEffectiveScale(),
                    startPanX: _lightboxPanX,
                    startPanY: _lightboxPanY,
                    startMidX: mid.x,
                    startMidY: mid.y,
                };
                stage.classList.add('is-dragging');
                e.preventDefault();
                return;
            }

            // Single finger pan when the image overflows the stage.
            if (stage.classList.contains('is-pannable')) {
                _lightboxPinch = null;
                _lightboxDrag = {
                    pointerId: e.pointerId,
                    startX: e.clientX,
                    startY: e.clientY,
                    originX: _lightboxPanX,
                    originY: _lightboxPanY,
                    moved: false,
                };
                stage.classList.add('is-dragging');
                e.preventDefault();
            }
        }

        function onLightboxPointerMove(e) {
            if (!_lightboxPointers.has(e.pointerId)) return;
            _lightboxPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

            if (_lightboxPinch && _lightboxPointers.size >= 2) {
                const dist = lightboxPinchDistance(_lightboxPointers);
                const mid = lightboxPinchMidpoint(_lightboxPointers);
                const ratio = dist / (_lightboxPinch.startDist || 1);
                lightboxSetEffectiveScale(_lightboxPinch.startEff * ratio);
                _lightboxPanX = _lightboxPinch.startPanX + (mid.x - _lightboxPinch.startMidX);
                _lightboxPanY = _lightboxPinch.startPanY + (mid.y - _lightboxPinch.startMidY);
                applyLightboxZoom();
                e.preventDefault();
                return;
            }

            if (!_lightboxDrag || e.pointerId !== _lightboxDrag.pointerId) return;
            if (_lightboxPointers.size !== 1) return;
            const dx = e.clientX - _lightboxDrag.startX;
            const dy = e.clientY - _lightboxDrag.startY;
            if (Math.abs(dx) + Math.abs(dy) > 3) _lightboxDrag.moved = true;
            _lightboxPanX = _lightboxDrag.originX + dx;
            _lightboxPanY = _lightboxDrag.originY + dy;
            applyLightboxZoom();
            e.preventDefault();
        }

        function onLightboxPointerUp(e) {
            if (!_lightboxPointers.has(e.pointerId) && !_lightboxDrag && !_lightboxPinch) return;
            const stage = e.currentTarget;
            _lightboxPointers.delete(e.pointerId);
            try {
                if (stage) stage.releasePointerCapture(e.pointerId);
            } catch (_) {}

            if (_lightboxPointers.size >= 2) {
                // Still pinching with remaining fingers — reseat pinch baseline.
                const dist = lightboxPinchDistance(_lightboxPointers) || 1;
                const mid = lightboxPinchMidpoint(_lightboxPointers);
                _lightboxPinch = {
                    startDist: dist,
                    startEff: lightboxEffectiveScale(),
                    startPanX: _lightboxPanX,
                    startPanY: _lightboxPanY,
                    startMidX: mid.x,
                    startMidY: mid.y,
                };
                _lightboxDrag = null;
                return;
            }

            _lightboxPinch = null;

            if (_lightboxPointers.size === 1) {
                // One finger left — continue as pan if oversized.
                const entry = _lightboxPointers.entries().next().value;
                if (entry && stage && stage.classList.contains('is-pannable')) {
                    _lightboxDrag = {
                        pointerId: entry[0],
                        startX: entry[1].x,
                        startY: entry[1].y,
                        originX: _lightboxPanX,
                        originY: _lightboxPanY,
                        moved: false,
                    };
                    return;
                }
            }

            _lightboxDrag = null;
            if (stage) stage.classList.remove('is-dragging');
        }

        function onLightboxWheel(e) {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            e.preventDefault();
            const delta = e.deltaY;
            if (delta < 0) lightboxZoomBy(_LIGHTBOX_ZOOM_STEP);
            else if (delta > 0) lightboxZoomBy(1 / _LIGHTBOX_ZOOM_STEP);
        }

        function onLightboxDblClick(e) {
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            if (e.target && e.target.closest && e.target.closest('video')) return;
            e.preventDefault();
            toggleLightboxSizeMode();
        }

        function onLightboxGesture(e) {
            // iOS Safari legacy gesture events — keep the page from stealing pinch.
            if (!_cuttleLightbox || _cuttleLightbox.hidden) return;
            e.preventDefault();
        }

        function applyLightboxCaption(item, meta) {
            const root = ensureCuttleLightbox();
            const titleEl = root.querySelector('.cuttle-lightbox-title');
            const descEl = root.querySelector('.cuttle-lightbox-desc');
            const pathEl = root.querySelector('.cuttle-lightbox-path');
            if (!titleEl || !pathEl) return;
            const m = meta && typeof meta === 'object' ? meta : null;
            const origName = String((m && m.original_name) || '').trim();
            const origPath = String((m && m.original_path) || item.originalPath || '').trim();
            const guessedName = originalNameFromStaged(item.src);
            const alt = String(item.title || '').trim();
            const explicitDesc = String(item.description || '').trim();
            // Prefer real original filename from sidecar; never prefer the uuid staged name.
            const displayName =
                origName
                || (alt && !mediaAltLooksLikeDescription(alt) && !/-[a-f0-9]{8,12}\.[^.]+$/i.test(alt) ? alt : '')
                || guessedName
                || 'Media';
            titleEl.textContent = displayName;
            let desc = explicitDesc;
            if (!desc && mediaAltLooksLikeDescription(alt) && alt !== displayName) {
                desc = alt;
            }
            if (descEl) {
                if (desc) {
                    descEl.textContent = desc;
                    descEl.hidden = false;
                    descEl.title = desc;
                } else {
                    descEl.textContent = '';
                    descEl.hidden = true;
                    descEl.removeAttribute('title');
                }
            }
            // Path line is ONLY the source disk path — never /output/shared/…
            if (origPath && !origPath.startsWith('/output/')) {
                pathEl.textContent = origPath;
                pathEl.hidden = false;
                pathEl.title = origPath;
            } else {
                pathEl.textContent = '';
                pathEl.hidden = true;
            }
            const dl = root.querySelector('.cuttle-lightbox-download');
            if (dl) {
                dl.setAttribute('download', origName || displayName || mediaDownloadFilename(item.src, item.title));
            }
        }

        const _lightboxMetaCache = Object.create(null);

        async function fetchSharedMediaMeta(src) {
            const u = String(src || '').trim().split('?')[0];
            if (!u.startsWith('/output/shared/')) return null;
            if (Object.prototype.hasOwnProperty.call(_lightboxMetaCache, u)) {
                return _lightboxMetaCache[u];
            }
            try {
                const resp = await fetchFn(
                    '/api/shared-media/meta?url=' + encodeURIComponent(u),
                    { credentials: 'include' }
                );
                const data = await resp.json().catch(function () { return null; });
                if (data && data.success && data.meta) {
                    _lightboxMetaCache[u] = data.meta;
                    return data.meta;
                }
            } catch (_) {}
            _lightboxMetaCache[u] = null;
            return null;
        }

        function collectBubbleMediaItems(fromEl) {
            const bubble = fromEl && fromEl.closest && fromEl.closest('.message');
            const root = bubble || fromEl;
            if (!root) return [];
            const nodes = root.querySelectorAll('.cuttle-media-thumb[data-src]');
            const items = [];
            nodes.forEach(function (node) {
                const src = node.getAttribute('data-src') || '';
                if (!src || !isSafeMediaSrc(src)) return;
                items.push({
                    src: src,
                    kind: node.getAttribute('data-kind') || mediaKindFromUrl(src),
                    title: node.getAttribute('data-title') || '',
                    description: node.getAttribute('data-description') || '',
                    download: node.getAttribute('data-download') || mediaDownloadUrl(src),
                    originalPath: node.getAttribute('data-original-path') || '',
                });
            });
            return items;
        }

        function showLightboxIndex(idx) {
            if (!_lightboxItems.length) return;
            const n = _lightboxItems.length;
            _lightboxIndex = ((idx % n) + n) % n;
            const item = _lightboxItems[_lightboxIndex];
            const root = ensureCuttleLightbox();
            const stage = root.querySelector('.cuttle-lightbox-stage');
            const counterEl = root.querySelector('.cuttle-lightbox-counter');
            const dl = root.querySelector('.cuttle-lightbox-download');
            const prevBtn = root.querySelector('.cuttle-lightbox-prev');
            const nextBtn = root.querySelector('.cuttle-lightbox-next');
            const shownIndex = _lightboxIndex;
            hideMediaCtxMenu();
            resetLightboxZoomState();
            stage.innerHTML = '';
            stage.classList.remove('is-pannable', 'is-dragging', 'is-video');
            const ownerDoc = root.ownerDocument || lightboxHostDocument();
            let media = null;
            if (item.kind === 'video') {
                stage.classList.add('is-video');
                const video = ownerDoc.createElement('video');
                video.src = item.src;
                video.controls = true;
                video.autoplay = true;
                video.playsInline = true;
                video.setAttribute('controlsList', 'nodownload');
                stage.appendChild(video);
                media = video;
            } else {
                const img = ownerDoc.createElement('img');
                img.src = item.src;
                img.alt = item.title || '';
                img.draggable = false;
                stage.appendChild(img);
                media = img;
            }
            applyLightboxCaption(item, null);
            counterEl.textContent = n > 1 ? (_lightboxIndex + 1) + ' / ' + n : '';
            const href = item.download || mediaDownloadUrl(item.src);
            dl.href = href;
            dl.setAttribute('download', mediaDownloadFilename(item.src, item.title));
            dl.style.display = href ? '' : 'none';
            const multi = n > 1;
            prevBtn.hidden = !multi;
            nextBtn.hidden = !multi;
            root.hidden = false;
            lightboxSetOpenClass(true);
            updateLightboxZoomUi();
            onLightboxMediaReady(media);
            try {
                root.focus({ preventScroll: true });
            } catch (_) {
                try { root.focus(); } catch (__) {}
            }

            // Fill original filename + source path from staging sidecar (async).
            if (String(item.src || '').startsWith('/output/shared/')) {
                fetchSharedMediaMeta(item.src).then(function (meta) {
                    if (_lightboxIndex !== shownIndex) return;
                    if (meta) applyLightboxCaption(item, meta);
                });
            }
        }

        function openCuttleLightbox(items, startIndex) {
            _lightboxItems = Array.isArray(items) ? items.filter(Boolean) : [];
            if (!_lightboxItems.length) return;
            showLightboxIndex(startIndex || 0);
        }

        function closeCuttleLightbox() {
            hideMediaCtxMenu();
            const root = _cuttleLightbox;
            if (!root) return;
            const video = root.querySelector('video');
            if (video) {
                try {
                    video.pause();
                } catch (_) {}
            }
            const stage = root.querySelector('.cuttle-lightbox-stage');
            if (stage) {
                stage.innerHTML = '';
                stage.classList.remove('is-pannable', 'is-dragging', 'is-video');
            }
            root.hidden = true;
            lightboxSetOpenClass(false);
            _lightboxItems = [];
            _lightboxIndex = 0;
            resetLightboxZoomState();
        }

        function onLightboxKeydown(e) {
            if (_mediaCtxMenu) {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    hideMediaCtxMenu();
                }
                return;
            }
            if (!_cuttleLightbox || _cuttleLightbox.hidden || !_cuttleLightbox.isConnected) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                closeCuttleLightbox();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                showLightboxIndex(_lightboxIndex - 1);
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                showLightboxIndex(_lightboxIndex + 1);
            } else if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') {
                e.preventDefault();
                lightboxZoomBy(_LIGHTBOX_ZOOM_STEP);
            } else if (e.key === '-' || e.key === '_' || e.code === 'NumpadSubtract') {
                e.preventDefault();
                lightboxZoomBy(1 / _LIGHTBOX_ZOOM_STEP);
            } else if (e.key === '0' || e.code === 'Numpad0') {
                e.preventDefault();
                setLightboxSizeMode('fit');
            } else if (e.key === '1' || e.code === 'Numpad1') {
                e.preventDefault();
                setLightboxSizeMode('actual');
            }
        }

        function onLightboxHostResize() {
            if (!_cuttleLightbox || _cuttleLightbox.hidden || !_cuttleLightbox.isConnected) return;
            measureLightboxFitScale();
            applyLightboxZoom();
        }

        // Image right-click menu (Copy / Save) for thumbs + lightbox.

        let _mediaCtxMenu = null;

        let _mediaCtxMenuDoc = null;

        let _mediaCtxDismissBound = false;

        function mediaCtxToast(msg, kind) {
            try {
                if (typeof frameWin.showToast === 'function') {
                    frameWin.showToast(msg, kind || 'info');
                    return;
                }
            } catch (_) {}
            try {
                const hostWin = lightboxHostWindow();
                if (hostWin && hostWin !== frameWin && typeof hostWin.showToast === 'function') {
                    hostWin.showToast(msg, kind || 'info');
                }
            } catch (__) {}
        }

        function hideMediaCtxMenu() {
            if (_mediaCtxMenu && _mediaCtxMenu.parentNode) {
                try {
                    _mediaCtxMenu.parentNode.removeChild(_mediaCtxMenu);
                } catch (_) {}
            }
            _mediaCtxMenu = null;
            _mediaCtxMenuDoc = null;
        }

        function onMediaCtxDismiss(e) {
            if (!_mediaCtxMenu) return;
            if (e && e.type === 'keydown' && e.key !== 'Escape') return;
            if (e && e.type === 'pointerdown') {
                if (_mediaCtxMenu.contains && _mediaCtxMenu.contains(e.target)) return;
            }
            hideMediaCtxMenu();
        }

        function bindMediaCtxDismiss(doc) {
            if (_mediaCtxDismissBound) return;
            _mediaCtxDismissBound = true;
            const opts = { capture: true };
            frameDoc.addEventListener('pointerdown', onMediaCtxDismiss, opts);
            frameDoc.addEventListener('scroll', onMediaCtxDismiss, true);
            frameDoc.addEventListener('keydown', onMediaCtxDismiss, opts);
            try {
                const hostDoc = lightboxHostDocument();
                if (hostDoc && hostDoc !== frameDoc) {
                    const hostOpts = { capture: true, signal: hostTeardownSignal };
                    hostDoc.addEventListener('pointerdown', onMediaCtxDismiss, hostOpts);
                    hostDoc.addEventListener('scroll', onMediaCtxDismiss, hostOpts);
                    hostDoc.addEventListener('keydown', onMediaCtxDismiss, hostOpts);
                }
            } catch (_) {}
            void doc;
        }

        async function copyImageToClipboard(src, win) {
            const u = String(src || '').trim();
            if (!u || !isSafeMediaSrc(u)) throw new Error('Invalid image');
            const w = win || frameWin;
            const nav = w.navigator || navigator;
            if (!nav.clipboard || typeof nav.clipboard.write !== 'function') {
                throw new Error('Clipboard image copy is not supported here');
            }
            const ClipItem = w.ClipboardItem || (typeof ClipboardItem === 'function' ? ClipboardItem : null);
            if (typeof ClipItem !== 'function') throw new Error('ClipboardItem not available');

            // Pass a Promise into ClipboardItem so write() stays in the user-gesture
            // turn while fetch + PNG encode finish asynchronously.
            const pngPromise = (async function () {
                const resp = await w.fetch(u, { credentials: 'include', cache: 'force-cache' });
                if (!resp.ok) throw new Error('Could not fetch image (' + resp.status + ')');
                const raw = await resp.blob();
                if (!raw || !String(raw.type || '').startsWith('image/')) {
                    throw new Error('Not an image');
                }
                return blobToPngBlob(raw, w);
            })();

            await nav.clipboard.write([new ClipItem({ 'image/png': pngPromise })]);
        }

        function saveMediaFile(src, title, downloadHref, ownerDoc) {
            const href = String(downloadHref || mediaDownloadUrl(src) || src || '').trim();
            if (!href) throw new Error('Nothing to save');
            const filename = mediaDownloadFilename(src, title);
            const doc = (ownerDoc && ownerDoc.body) ? ownerDoc : frameDoc;
            const a = doc.createElement('a');
            a.href = href;
            a.download = filename;
            a.rel = 'noopener';
            a.style.display = 'none';
            (doc.body || frameDoc.body).appendChild(a);
            a.click();
            try {
                a.remove();
            } catch (_) {}
        }

        function mediaCtxTargetFromEvent(e) {
            const t = e && e.target;
            if (!t || !t.closest) return null;

            const thumb = t.closest('.cuttle-media-thumb[data-src]');
            if (thumb) {
                const src = thumb.getAttribute('data-src') || '';
                if (!src || !isSafeMediaSrc(src)) return null;
                const kind = thumb.getAttribute('data-kind') || mediaKindFromUrl(src);
                if (kind !== 'image') return null;
                return {
                    src: src,
                    title: thumb.getAttribute('data-title') || '',
                    download: thumb.getAttribute('data-download') || mediaDownloadUrl(src),
                    kind: 'image',
                };
            }

            const lbImg = t.closest('.cuttle-lightbox-stage img');
            if (lbImg && _lightboxItems && _lightboxItems.length) {
                const item = _lightboxItems[_lightboxIndex];
                if (!item || item.kind === 'video') return null;
                const src = item.src || lbImg.getAttribute('src') || '';
                if (!src || !isSafeMediaSrc(src)) return null;
                return {
                    src: src,
                    title: item.title || lbImg.getAttribute('alt') || '',
                    download: item.download || mediaDownloadUrl(src),
                    kind: 'image',
                };
            }
            return null;
        }

        function showMediaCtxMenu(clientX, clientY, media, ownerDoc) {
            hideMediaCtxMenu();
            const doc = ownerDoc || frameDoc;
            const menu = doc.createElement('div');
            menu.className = 'cuttle-media-ctx-menu';
            menu.setAttribute('role', 'menu');
            menu.innerHTML =
                '<button type="button" class="cuttle-media-ctx-item" data-media-ctx="copy" role="menuitem">Copy</button>' +
                '<button type="button" class="cuttle-media-ctx-item" data-media-ctx="save" role="menuitem">Save</button>';
            (doc.body || frameDoc.body).appendChild(menu);
            _mediaCtxMenu = menu;
            _mediaCtxMenuDoc = doc;
            bindMediaCtxDismiss(doc);

            // Position, then clamp inside the viewport.
            menu.style.left = '0px';
            menu.style.top = '0px';
            const rect = menu.getBoundingClientRect();
            const pad = 8;
            const vw = doc.defaultView ? doc.defaultView.innerWidth : frameWin.innerWidth;
            const vh = doc.defaultView ? doc.defaultView.innerHeight : frameWin.innerHeight;
            let x = Number(clientX) || 0;
            let y = Number(clientY) || 0;
            if (x + rect.width + pad > vw) x = Math.max(pad, vw - rect.width - pad);
            if (y + rect.height + pad > vh) y = Math.max(pad, vh - rect.height - pad);
            if (x < pad) x = pad;
            if (y < pad) y = pad;
            menu.style.left = x + 'px';
            menu.style.top = y + 'px';

            menu.addEventListener('click', function (ev) {
                const btn = ev.target && ev.target.closest && ev.target.closest('[data-media-ctx]');
                if (!btn) return;
                ev.preventDefault();
                ev.stopPropagation();
                const action = btn.getAttribute('data-media-ctx');
                const win = (doc.defaultView) || frameWin;
                hideMediaCtxMenu();
                if (action === 'copy') {
                    copyImageToClipboard(media.src, win)
                        .then(function () {
                            mediaCtxToast('Image copied', 'success');
                        })
                        .catch(function (err) {
                            mediaCtxToast(
                                (err && err.message) || 'Could not copy image',
                                'error'
                            );
                        });
                } else if (action === 'save') {
                    try {
                        saveMediaFile(media.src, media.title, media.download, doc);
                    } catch (err) {
                        mediaCtxToast(
                            (err && err.message) || 'Could not save image',
                            'error'
                        );
                    }
                }
            });
        }

        function onMediaContextMenu(e) {
            const media = mediaCtxTargetFromEvent(e);
            if (!media) return;
            e.preventDefault();
            e.stopPropagation();
            const ownerDoc = (e.target && e.target.ownerDocument) || frameDoc;
            showMediaCtxMenu(e.clientX, e.clientY, media, ownerDoc);
        }

        function bindCuttleMediaLightbox() {
            frameDoc.addEventListener(
                'click',
                function (e) {
                    const thumb =
                        e.target && e.target.closest && e.target.closest('.cuttle-media-thumb[data-src]');
                    if (!thumb) return;
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                    e.preventDefault();
                    e.stopPropagation();
                    const items = collectBubbleMediaItems(thumb);
                    const src = thumb.getAttribute('data-src') || '';
                    let idx = items.findIndex(function (it) {
                        return it.src === src;
                    });
                    if (idx < 0) {
                        items.unshift({
                            src: src,
                            kind: thumb.getAttribute('data-kind') || mediaKindFromUrl(src),
                            title: thumb.getAttribute('data-title') || '',
                            description: thumb.getAttribute('data-description') || '',
                            download: thumb.getAttribute('data-download') || mediaDownloadUrl(src),
                        });
                        idx = 0;
                    }
                    openCuttleLightbox(items, idx);
                },
                true
            );
            // Right-click Copy / Save on markdown + attachment image thumbs.
            frameDoc.addEventListener('contextmenu', onMediaContextMenu, true);
            // Chat iframe (focused composer) + host shell (lightbox has focus when open).
            frameDoc.addEventListener('keydown', onLightboxKeydown);
            if (!_lightboxHostKeyBound) {
                try {
                    const hostDoc = lightboxHostDocument();
                    if (hostDoc && hostDoc !== frameDoc) {
                        hostDoc.addEventListener('keydown', onLightboxKeydown, { signal: hostTeardownSignal });
                        _lightboxHostKeyBound = true;
                    }
                } catch (_) {}
            }
            if (!_lightboxHostResizeBound) {
                try {
                    lightboxHostWindow().addEventListener('resize', onLightboxHostResize, {
                        signal: hostTeardownSignal,
                    });
                    _lightboxHostResizeBound = true;
                } catch (_) {
                    frameWin.addEventListener('resize', onLightboxHostResize);
                    _lightboxHostResizeBound = true;
                }
            }
        }

        return {
            buildMediaThumbHtml,
            thumbFallback,
            bind: bindCuttleMediaLightbox,
        };
    }

    const api = {
        mediaKindFromUrl,
        mediaPosterUrl,
        isSafeMediaSrc,
        mediaDownloadUrl,
        mediaDownloadFilename,
        mediaAltLooksLikeDescription,
        mediaBasenameFromSrc,
        originalNameFromStaged,
        blobToPngBlob,
        create,
    };

    const ns = (root.CuttleChatMedia = root.CuttleChatMedia || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
