/* Home Feed — tile grid, hover preview, media mode via shell background */

(function () {
    /** Pipeline file name (without .json); must match src/pipelines/Feed_Agent.json */
    const FEED_PIPELINE_NAME = 'Feed_Agent';

    const feedEditBtn = document.getElementById('feedEditBtn');
    const feedEditPanel = document.getElementById('feedEditPanel');
    const feedEditCloseBtn = document.getElementById('feedEditCloseBtn');
    const refreshBtn = document.getElementById('refreshBtn');
    const feedRefreshStatus = document.getElementById('feedRefreshStatus');
    const feedStream = document.getElementById('feedStream');
    const keywordChips = document.getElementById('keywordChips');
    const sourceChips = document.getElementById('sourceChips');
    const keywordInput = document.getElementById('keywordInput');
    const sourceInput = document.getElementById('sourceInput');
    const keywordAddBtn = document.getElementById('keywordAddBtn');
    const sourceAddBtn = document.getElementById('sourceAddBtn');
    const rssFeedChips = document.getElementById('rssFeedChips');
    const rssFeedInput = document.getElementById('rssFeedInput');
    const rssFeedAddBtn = document.getElementById('rssFeedAddBtn');
    const interestsField = document.getElementById('interestsField');
    const interestsSaveBtn = document.getElementById('interestsSaveBtn');
    let activeSource = 'all';
    let feedItems = [];
    /** Items currently shown (after filter); tile indices refer to this array. */
    let displayedItems = [];
    let previewTimer = null;

    function isDirectVideoUrl(url) {
        if (!url || typeof url !== 'string') return false;
        const trimmed = url.trim().toLowerCase();
        return /\.(mp4|webm|ogg)(\?|$)/i.test(trimmed) || trimmed.startsWith('blob:');
    }

    function getItemUrl(item) {
        return (item.url || item.link || '').trim();
    }

    function getPlayableUrl(item) {
        const v = item.video_url || item.videoUrl || item.embed_url || item.embedUrl;
        if (v && String(v).trim()) return String(v).trim();
        return getItemUrl(item);
    }

    function isVideoItem(item) {
        const t = (item.type || item.source || '').toLowerCase();
        const playUrl = getPlayableUrl(item);
        const pageUrl = getItemUrl(item);
        if (typeof parseYouTubeVideoId === 'function') {
            if (parseYouTubeVideoId(playUrl) || parseYouTubeVideoId(pageUrl)) return true;
        }
        if (t.includes('video') || t.includes('youtube')) return true;
        const u = pageUrl.toLowerCase();
        const p = playUrl.toLowerCase();
        if (/youtube\.com|youtu\.be/.test(u) || /youtube\.com|youtu\.be/.test(p)) return true;
        return isDirectVideoUrl(pageUrl) || isDirectVideoUrl(playUrl);
    }

    function filterBySource(items, source) {
        if (!items || source === 'all') return items || [];
        return items.filter(i => {
            const t = (i.type || i.source || '').toLowerCase();
            const u = getItemUrl(i).toLowerCase();
            if (source === 'youtube') {
                return t.includes('youtube') || u.includes('youtube.com') || u.includes('youtu.be');
            }
            return t.includes(source) || u.includes(source);
        });
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }

    function stopTilePreview(tile) {
        if (!tile) return;
        tile.classList.remove('is-previewing');
        const host = tile.querySelector('.feed-tile-preview-host');
        if (host) host.innerHTML = '';
    }

    function startTilePreview(tile, item) {
        const host = tile.querySelector('.feed-tile-preview-host');
        if (!host || !isVideoItem(item)) return;
        const playUrl = getPlayableUrl(item);
        host.innerHTML = '';
        const ytId = typeof parseYouTubeVideoId === 'function' ? parseYouTubeVideoId(playUrl) : null;
        if (ytId) {
            const iframe = document.createElement('iframe');
            iframe.setAttribute('loading', 'lazy');
            iframe.src = 'https://www.youtube.com/embed/' + encodeURIComponent(ytId) +
                '?autoplay=1&mute=1&controls=0&modestbranding=1&rel=0&playsinline=1&iv_load_policy=3';
            iframe.title = 'Preview';
            iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen');
            host.appendChild(iframe);
            tile.classList.add('is-previewing');
            return;
        }
        if (isDirectVideoUrl(playUrl)) {
            const video = document.createElement('video');
            video.src = playUrl;
            video.muted = true;
            video.loop = true;
            video.playsInline = true;
            video.autoplay = true;
            video.setAttribute('playsinline', '');
            host.appendChild(video);
            tile.classList.add('is-previewing');
            video.play().catch(function () {});
        }
    }

    function bindTileHover(tiles) {
        tiles.forEach(tile => {
            const idx = parseInt(tile.getAttribute('data-feed-index'), 10);
            if (isNaN(idx) || !displayedItems[idx]) return;
            const item = displayedItems[idx];

            tile.addEventListener('mouseenter', function () {
                if (previewTimer) clearTimeout(previewTimer);
                previewTimer = setTimeout(function () {
                    startTilePreview(tile, item);
                }, 220);
            });
            tile.addEventListener('mouseleave', function () {
                if (previewTimer) {
                    clearTimeout(previewTimer);
                    previewTimer = null;
                }
                stopTilePreview(tile);
            });
            tile.addEventListener('focus', function () {
                startTilePreview(tile, item);
            });
            tile.addEventListener('blur', function () {
                stopTilePreview(tile);
            });
        });
    }

    function openMediaMode(item) {
        const playUrl = getPlayableUrl(item);
        if (!playUrl) return;
        const meta = {
            title: item.title || item.name || 'Media',
            description: item.excerpt || item.description || '',
            url: playUrl,
            externalUrl: getItemUrl(item) || playUrl,
            isYouTube: !!(typeof parseYouTubeVideoId === 'function' && parseYouTubeVideoId(playUrl)),
            isDirectVideo: isDirectVideoUrl(playUrl)
        };
        try {
            sessionStorage.setItem('cuttleMediaMeta', JSON.stringify(meta));
        } catch (_) {}

        if (window.parent !== window) {
            window.parent.postMessage({ type: 'cuttle-enter-media-mode', url: playUrl, title: meta.title }, '*');
        } else {
            if (window.CuttleVideoBackground && window.CuttleVideoBackground.enterMediaMode) {
                window.CuttleVideoBackground.enterMediaMode(playUrl);
            }
            window.location.href = '/media_player.html?v=' + Date.now();
        }
    }

    function openArticleLink(item) {
        const u = getItemUrl(item);
        if (u && u !== '#') window.open(u, '_blank', 'noopener,noreferrer');
    }

    function onTileActivate(item) {
        if (isVideoItem(item)) openMediaMode(item);
        else openArticleLink(item);
    }

    function renderTile(item, index) {
        const type = (item.type || item.source || 'feed').toLowerCase();
        const badge = (item.type || item.source || 'Item').toString();
        const badgeShort = badge.length > 12 ? badge.slice(0, 11) + '…' : badge;
        const title = item.title || item.name || 'Untitled';
        const meta = item.meta || item.source_name || '';
        const desc = (item.excerpt || item.description || '').trim();
        const url = getItemUrl(item);
        const thumb = item.thumbnail || item.image;
        const video = isVideoItem(item);

        const posterHtml = thumb
            ? '<img class="feed-tile-poster" src="' + escapeHtml(thumb) + '" alt="" loading="lazy" decoding="async" onerror="this.style.display=\'none\'">'
            : '<div class="feed-tile-poster" style="display:flex;align-items:center;justify-content:center;font-size:2.5rem;opacity:0.35;background:rgba(0,0,0,0.25)">' +
              (video ? '▶️' : '📄') + '</div>';

        const descHtml = desc
            ? '<div class="feed-tile-desc">' + escapeHtml(desc) + '</div>'
            : '<div class="feed-tile-desc" style="opacity:0"> </div>';

        return (
            '<button type="button" class="feed-tile' + (video ? ' feed-tile--video' : '') + '" data-feed-index="' + index + '" ' +
            'data-is-video="' + (video ? '1' : '0') + '" ' +
            'aria-label="' + escapeHtml(title) + (video ? ', play video' : ', open link') + '">' +
            '<span class="feed-tile-thumb-wrap">' +
            '<span class="feed-tile-badge">' + escapeHtml(badgeShort) + '</span>' +
            '<span class="feed-tile-preview-host" aria-hidden="true"></span>' +
            posterHtml +
            descHtml +
            '</span>' +
            '<span class="feed-tile-title">' + escapeHtml(title) + '</span>' +
            (meta ? '<span class="feed-tile-meta">' + escapeHtml(meta) + '</span>' : '') +
            '</button>'
        );
    }

    function renderFeed(items) {
        if (!feedStream) return;
        displayedItems = items || [];
        if (!displayedItems.length) {
            feedStream.innerHTML = feedItems.length
                ? '<p class="feed-empty">No items match this filter.</p>'
                : '<p class="feed-empty">No items in your feed yet.</p>';
            return;
        }
        feedStream.innerHTML = displayedItems.map((card, i) => renderTile(card, i)).join('');
        feedStream.querySelectorAll('.feed-tile').forEach(tile => {
            tile.addEventListener('click', function () {
                const idx = parseInt(tile.getAttribute('data-feed-index'), 10);
                if (!isNaN(idx) && displayedItems[idx]) onTileActivate(displayedItems[idx]);
            });
        });
        bindTileHover(Array.from(feedStream.querySelectorAll('.feed-tile')));
    }

    document.querySelectorAll('.filter-badge:not(.disabled)').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-badge').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeSource = btn.dataset.source || 'all';
            renderFeed(filterBySource(feedItems, activeSource));
        });
    });

    async function loadFeed() {
        if (!feedStream) return;
        feedStream.innerHTML = '<p class="feed-empty">Loading feed...</p>';
        try {
            const r = await fetch('/api/feed', { cache: 'no-store' });
            if (r.ok) {
                const data = await r.json();
                feedItems = data.items || data.feed || data || [];
                if (!Array.isArray(feedItems)) feedItems = [];
                if (feedItems.length === 0) {
                    feedStream.innerHTML = '<p class="feed-empty">No items yet. Feed Agent used a pipeline graph that was removed, so automatic ingest is off.</p>';
                } else {
                    renderFeed(filterBySource(feedItems, activeSource));
                }
            } else {
                feedStream.innerHTML = '<p class="feed-empty">Feed not available. The Feed Agent graph was removed.</p>';
            }
        } catch (e) {
            feedStream.innerHTML = '<p class="feed-empty">Could not load feed. Make sure the Cuttle daemon is running.</p>';
        }
    }

    function setFeedEditOpen(open) {
        if (!feedEditPanel || !feedEditBtn) return;
        feedEditPanel.hidden = !open;
        feedEditBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            try {
                feedEditPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            } catch (_) {}
            setTimeout(function () {
                const el = document.getElementById('keywordInput');
                if (el) el.focus();
            }, 100);
        }
    }

    if (feedEditBtn && feedEditPanel) {
        feedEditBtn.addEventListener('click', function () {
            setFeedEditOpen(feedEditPanel.hidden);
        });
    }
    if (feedEditCloseBtn) {
        feedEditCloseBtn.addEventListener('click', function () {
            setFeedEditOpen(false);
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (!feedEditPanel || feedEditPanel.hidden) return;
        setFeedEditOpen(false);
        if (feedEditBtn) feedEditBtn.focus();
    });

    function setRefreshStatus(text, show) {
        if (!feedRefreshStatus) return;
        if (show === false || text === '') {
            feedRefreshStatus.hidden = true;
            feedRefreshStatus.textContent = '';
            feedRefreshStatus.innerHTML = '';
            return;
        }
        feedRefreshStatus.hidden = false;
        feedRefreshStatus.innerHTML = '';
        feedRefreshStatus.textContent = text;
    }

    /** Optional HTML status line (e.g. link to query report). Clears with setRefreshStatus('', false). */
    function setRefreshStatusHtml(html, show) {
        if (!feedRefreshStatus) return;
        if (show === false || !html) {
            feedRefreshStatus.hidden = true;
            feedRefreshStatus.textContent = '';
            feedRefreshStatus.innerHTML = '';
            return;
        }
        feedRefreshStatus.hidden = false;
        feedRefreshStatus.innerHTML = html;
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            let prevUpdated = null;
            try {
                const r0 = await fetch('/api/feed', { cache: 'no-store' });
                if (r0.ok) {
                    const d0 = await r0.json();
                    prevUpdated = d0.updated_at != null ? String(d0.updated_at) : null;
                }
            } catch (_) {}

            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<span class="feed-icon-btn-symbol" aria-hidden="true">⏳</span>';
            setRefreshStatus('Starting Feed Agent (RSS ingest + curation)… This usually takes 1–3 minutes.', true);

            setRefreshStatus('Feed Agent used a pipeline graph that was removed. Home feed refresh is unavailable until a non-graph ingest exists.', true);
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<span class="feed-icon-btn-symbol" aria-hidden="true">🔄</span>';
        });
    }

    function rssChipDisplayLabel(url) {
        const u = String(url || '');
        if (u.length <= 56) return u;
        return u.slice(0, 28) + '…' + u.slice(-24);
    }

    function renderChipList(container, items, kind) {
        if (!container) return;
        container.innerHTML = (items || []).map(function (label) {
            const rawStr = String(label);
            const display = kind === 'rss' ? rssChipDisplayLabel(rawStr) : rawStr;
            const safe = escapeHtml(display);
            const v = encodeURIComponent(rawStr);
            return '<span class="feed-chip" data-kind="' + kind + '" data-value="' + v + '" title="' + escapeHtml(rawStr) + '">' +
                safe + '<button type="button" class="feed-chip-remove" data-kind="' + kind + '" data-value="' + v + '" title="Remove">×</button></span>';
        }).join('');
        container.querySelectorAll('.feed-chip-remove').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                const kind = btn.getAttribute('data-kind');
                const raw = decodeURIComponent(btn.getAttribute('data-value') || '');
                const body = { action: 'patch' };
                if (kind === 'keyword') body.remove_keywords = [raw];
                else if (kind === 'rss') body.remove_rss_feeds = [raw];
                else body.remove_sources = [raw];
                try {
                    const r = await fetch('/api/feed/preferences', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });
                    if (r.ok) await loadPreferences();
                } catch (_) {}
            });
        });
    }

    async function loadPreferences() {
        try {
            const r = await fetch('/api/feed/preferences', { cache: 'no-store' });
            if (!r.ok) return;
            const d = await r.json();
            if (!d.success) return;
            renderChipList(keywordChips, d.keywords || [], 'keyword');
            renderChipList(sourceChips, d.sources || [], 'source');
            renderChipList(rssFeedChips, d.rss_feeds || [], 'rss');
            if (interestsField) interestsField.value = d.interests || '';
        } catch (_) {}
    }

    async function postPreferences(body) {
        const r = await fetch('/api/feed/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (r.ok) await loadPreferences();
    }

    if (keywordAddBtn && keywordInput) {
        keywordAddBtn.addEventListener('click', async function () {
            const v = keywordInput.value.trim();
            if (!v) return;
            keywordInput.value = '';
            await postPreferences({ action: 'patch', add_keywords: [v] });
        });
        keywordInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                keywordAddBtn.click();
            }
        });
    }
    if (sourceAddBtn && sourceInput) {
        sourceAddBtn.addEventListener('click', async function () {
            const v = sourceInput.value.trim();
            if (!v) return;
            sourceInput.value = '';
            await postPreferences({ action: 'patch', add_sources: [v] });
        });
        sourceInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                sourceAddBtn.click();
            }
        });
    }
    if (rssFeedAddBtn && rssFeedInput) {
        rssFeedAddBtn.addEventListener('click', async function () {
            let v = rssFeedInput.value.trim();
            if (!v) return;
            if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
            rssFeedInput.value = '';
            await postPreferences({ action: 'patch', add_rss_feeds: [v] });
        });
        rssFeedInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                rssFeedAddBtn.click();
            }
        });
    }
    if (interestsSaveBtn && interestsField) {
        interestsSaveBtn.addEventListener('click', async function () {
            await postPreferences({
                action: 'patch',
                replace_interests: interestsField.value
            });
            interestsSaveBtn.textContent = 'Saved';
            setTimeout(function () { interestsSaveBtn.textContent = 'Save notes'; }, 1500);
        });
    }

    loadFeed();
    loadPreferences();

    if (window.parent !== window) {
        window.addEventListener('message', (e) => {
            if (e.data?.type === 'cuttle-theme-change' && e.data?.theme && !window.CuttleUiBoot) {
                document.body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');
                if (e.data.theme === 'light') document.body.classList.add('light-mode');
                else if (e.data.theme === 'midnight') document.body.classList.add('midnight-mode');
                else document.body.classList.add('dark-mode');
            }
        });
    }
})();
