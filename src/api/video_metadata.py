"""Title/thumbnail lookup for wallpaper playlist entries.

The browser cannot do this itself: YouTube's oEmbed endpoint sends no CORS
headers, and there is no public no-key title API. So the server resolves the
title and author once per video id and the page paints a cached badge.

Scope is deliberately small — this is wallpaper metadata, not a video
downloader:

* only ``youtube.com`` / ``youtu.be`` ids resolve; anything else (``.mp4``,
  ``.webm``, ``blob:``) reports ``supported: false`` and the page falls back to
  the filename,
* the thumbnail URL is *derived* from the video id (``i.ytimg.com``), so the
  usual case costs zero extra HTTP requests — only the title needs a fetch,
* results are cached in-process with a TTL because playlists are static; a user
  editing one row should not re-hit YouTube on every keystroke.

The id parser mirrors ``src/web/js/youtube_id.js`` (the same URL shapes must
resolve on both sides), and ``preferred_ollama_model``-style settings are not
involved: nothing here writes preferences.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Optional

OEMBED_ENDPOINT = 'https://www.youtube.com/oembed'
OEMBED_TIMEOUT = 5.0
CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_ENTRIES = 256
THUMBNAIL_QUALITY = 'mqdefault'

_ID_PATTERNS = (
    re.compile(r'youtu\.be/([a-zA-Z0-9_-]{11})(?:[?&#]|$)', re.I),
    re.compile(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})(?:[?&#]|$)', re.I),
    re.compile(r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})(?:[?&#]|$)', re.I),
    re.compile(r'youtube\.com/live/([a-zA-Z0-9_-]{11})(?:[?&#]|$)', re.I),
    re.compile(r'[?&]v=([a-zA-Z0-9_-]{11})(?:[&=#]|$)', re.I),
)

_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def parse_youtube_id(url: Any) -> Optional[str]:
    """11-char YouTube video id, or None. Kept in sync with youtube_id.js."""
    if not url or not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    for pattern in _ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def thumbnail_url(video_id: str, quality: str = THUMBNAIL_QUALITY) -> str:
    return f'https://i.ytimg.com/vi/{video_id}/{quality}.jpg'


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        stamped_at, value = hit
        if now - stamped_at > CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), value)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _unsupported(reason: str = 'not a YouTube URL') -> Dict[str, Any]:
    return {'supported': False, 'title': '', 'author': '', 'thumbnail': '', 'reason': reason}


def resolve_video_metadata(url: Any, *, timeout: float = OEMBED_TIMEOUT) -> Dict[str, Any]:
    """Best-effort title/author/thumbnail for one wallpaper URL.

    Never raises: an unreachable or private YouTube video returns
    ``supported: True`` with an empty title so the row still renders its
    thumbnail instead of an error.
    """
    text = str(url or '').strip()
    if not text:
        return _unsupported('empty URL')

    video_id = parse_youtube_id(text)
    if not video_id:
        return _unsupported()

    thumb = thumbnail_url(video_id)
    cached = _cache_get(video_id)
    if cached is not None:
        return dict(cached, thumbnail=thumb, cached=True)

    result: Dict[str, Any] = {
        'supported': True,
        'video_id': video_id,
        'title': '',
        'author': '',
        'thumbnail': thumb,
        'cached': False,
    }
    try:
        import requests

        resp = requests.get(
            OEMBED_ENDPOINT,
            params={'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'},
            timeout=timeout,
        )
        if resp.ok:
            data = resp.json()
            result['title'] = str(data.get('title') or '').strip()
            result['author'] = str(data.get('author_name') or '').strip()
    except Exception:
        pass

    _cache_put(video_id, dict(result))
    return result