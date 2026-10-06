/**
 * Resolve a YouTube video id from watch / shorts / live / embed / youtu.be URLs.
 * Handles v= anywhere in the query string (e.g. ?si=…&v=11chars).
 */
(function (global) {
    'use strict';

    function parseYouTubeVideoId(url) {
        if (!url || typeof url !== 'string') return null;
        const s = url.trim();
        if (!s) return null;

        let m = s.match(/youtu\.be\/([a-zA-Z0-9_-]{11})(?:[?&#]|$)/i);
        if (m) return m[1];

        m = s.match(/youtube\.com\/embed\/([a-zA-Z0-9_-]{11})(?:[?&#]|$)/i);
        if (m) return m[1];

        m = s.match(/youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})(?:[?&#]|$)/i);
        if (m) return m[1];

        m = s.match(/youtube\.com\/live\/([a-zA-Z0-9_-]{11})(?:[?&#]|$)/i);
        if (m) return m[1];

        m = s.match(/[?&]v=([a-zA-Z0-9_-]{11})(?:[&=#]|$)/i);
        if (m) return m[1];

        return null;
    }

    global.parseYouTubeVideoId = parseYouTubeVideoId;
})(typeof window !== 'undefined' ? window : globalThis);
