"""Published-release discovery. Read-only; mesh updates remain device_workers-owned.

One process cache shared by all Settings clients; no credentials or local writes.
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

import requests

from api.device_workers.capabilities import cuttle_git_rev, cuttle_version

RELEASES_URL = 'https://github.com/kidchemical/Cuttle/releases'
LATEST_URL = 'https://api.github.com/repos/kidchemical/Cuttle/releases/latest'
CACHE_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 5 * 60
_lock = threading.Lock()
_cache: dict = {}


def version_key(value: str) -> tuple:
    """Strict SemVer precedence, including numeric prerelease identifiers."""
    match = re.fullmatch(
        r'[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
        r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', value.strip())
    if not match:
        raise ValueError('Release tag must be a semantic version')
    pre = match[4]
    identifiers = []
    for part in pre.split('.') if pre else []:
        if part.isdigit():
            if len(part) > 1 and part.startswith('0'):
                raise ValueError('Invalid numeric prerelease identifier')
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part))
    return (*(int(match[i]) for i in (1, 2, 3)), pre is None, tuple(identifiers))


def normalize_release(data: dict) -> dict:
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Expected a published stable release')
    tag = data.get('tag_name')
    if not isinstance(tag, str):
        raise ValueError('Missing release tag')
    version_key(tag)
    return {
        'version': tag.lstrip('vV'),
        'title': str(data.get('name') or tag),
        # Construct the link from our known repository, never trust remote URLs.
        'url': RELEASES_URL + '/tag/' + quote(tag, safe=''),
        'notes': str(data.get('body') or '')[:100_000],
        'published_at': str(data.get('published_at') or ''),
    }


def check_releases(*, force: bool = False) -> dict:
    """Coalesce refreshes; retain old notes on failure and label them stale."""
    global _cache
    current = cuttle_version()
    revision = cuttle_git_rev()
    requested_at = time.time()
    with _lock:
        attempted = _cache.get('attempted_at', 0)
        ttl = RETRY_SECONDS if _cache.get('error') else CACHE_SECONDS
        # <=: Windows time() can repeat within a clock tick; a forced check
        # only coalesces into an attempt that began after it was requested.
        if (not _cache or (force and attempted <= requested_at)
                or requested_at - attempted >= ttl):
            try:
                response = requests.get(
                    LATEST_URL, timeout=(3, 8),
                    headers={'Accept': 'application/vnd.github+json',
                             'User-Agent': 'Cuttle-release-check'},
                )
                if response.status_code == 404:
                    release = None
                else:
                    response.raise_for_status()
                    release = normalize_release(response.json())
                _cache = {'release': release, 'checked_at': time.time(),
                          'attempted_at': time.time(), 'error': ''}
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                _cache = {**_cache, 'attempted_at': time.time(),
                          'error': 'Could not check GitHub releases. Try again later.'}
        result = dict(_cache)
    release = result.get('release')
    state = 'unknown' if result.get('error') else 'no_release'
    if release:
        try:
            comparison = (version_key(current) > version_key(release['version'])) - (
                version_key(current) < version_key(release['version']))
            state = 'available' if comparison < 0 else 'ahead' if comparison > 0 else 'latest'
        except ValueError:
            state = 'unknown'
    return {**result, 'current_version': current, 'git_rev': revision,
            'state': state, 'stale': bool(result.get('error')),
            'releases_url': RELEASES_URL}
