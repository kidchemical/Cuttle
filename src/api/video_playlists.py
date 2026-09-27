"""Video wallpaper prefs with named playlists (``settings.json`` → ``video_background``).

Shape::

    {
        "playlists": {"default": [url, ...], "anime": [...]},
        "active_playlist": "default",
        "urls": [...],          # mirror of the active playlist for older clients
        "duration": 60, "opacity": 45, "enabled": True,
    }

Older settings (``urls`` only) migrate into a ``default`` playlist. A POST that
only carries ``urls`` edits the active playlist, so pre-playlist clients keep
working.
"""

from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_PLAYLIST = 'default'
MAX_PLAYLIST_NAME = 40


def clean_urls(raw: Any) -> List[str]:
    """Trimmed, non-empty, de-duplicated URLs in their original order."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for u in raw:
        s = str(u).strip() if u is not None else ''
        if s and s not in out:
            out.append(s)
    return out


def clean_playlist_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ''
    return ' '.join(raw.split())[:MAX_PLAYLIST_NAME]


def clean_playlists(raw: Any) -> Dict[str, List[str]]:
    """Name → URLs; drops blank names and case-insensitive duplicates (first wins)."""
    out: Dict[str, List[str]] = {}
    if not isinstance(raw, dict):
        return out
    seen = set()
    for name, urls in raw.items():
        key = clean_playlist_name(name)
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        out[key] = clean_urls(urls)
    return out


def _clamp_int(raw: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return fallback


def normalize_video_background(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    playlists = clean_playlists(raw.get('playlists'))
    if not playlists:
        playlists = {DEFAULT_PLAYLIST: clean_urls(raw.get('urls'))}
    active = clean_playlist_name(raw.get('active_playlist'))
    if active not in playlists:
        active = next(iter(playlists))
    return {
        'playlists': playlists,
        'active_playlist': active,
        'urls': list(playlists[active]),
        'duration': _clamp_int(raw.get('duration', 60), 5, 600, 60),
        'opacity': _clamp_int(raw.get('opacity', 45), 0, 100, 45),
        # Missing key → enabled (backward compatible with older settings.json).
        'enabled': bool(raw.get('enabled')) if 'enabled' in raw else True,
    }


def apply_video_background_update(current: Any, data: Any) -> Dict[str, Any]:
    """Merge a POST body into the stored prefs.

    Order: ``playlists`` replaces the set, ``active_playlist`` switches (creating
    an empty playlist for a new name), then ``urls`` overwrites the active one.
    """
    vb = normalize_video_background(current)
    data = data if isinstance(data, dict) else {}
    playlists = vb['playlists']
    active = vb['active_playlist']

    incoming = clean_playlists(data.get('playlists'))
    if incoming:
        playlists = incoming
        if active not in playlists:
            active = next(iter(playlists))

    requested = clean_playlist_name(data.get('active_playlist'))
    if requested:
        match = next((n for n in playlists if n.lower() == requested.lower()), None)
        if match is None:
            playlists[requested] = []
            match = requested
        active = match

    if isinstance(data.get('urls'), list):
        playlists[active] = clean_urls(data['urls'])

    merged = dict(vb, playlists=playlists, active_playlist=active)
    for key in ('duration', 'opacity'):
        if key in data:
            try:
                merged[key] = int(data[key])
            except (TypeError, ValueError):
                pass
    if 'enabled' in data:
        merged['enabled'] = bool(data['enabled'])
    return normalize_video_background(merged)
