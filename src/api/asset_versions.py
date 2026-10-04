"""Server-stamped fingerprints for UI ``/js`` and ``/css`` assets.

Hand-edited ``?v=`` tags are cached ``immutable`` for a week, so an asset edited
without a tag bump stayed stale on clients with no hard refresh (Android
WebView). HTML responses get each local asset URL's ``v`` suffixed with the
file's mtime fingerprint; only URLs carrying the current fingerprint are cached
long-term, anything else revalidates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_SEP = '~'
_ASSET_URL = re.compile(r'''(?P<pre>(?:src|href)=["'])(?P<path>/(?:js|css)/[^"'?#]+)\?v=(?P<tag>[^"'&#]*)''')


def _asset_file(web_dir: Path, url_path: str) -> Optional[Path]:
    root = web_dir.resolve()
    try:
        path = (root / url_path.lstrip('/')).resolve()
    except (OSError, ValueError):
        return None
    if root not in path.parents or not path.is_file():
        return None
    return path


def fingerprint(web_dir: Path, url_path: str) -> Optional[str]:
    path = _asset_file(web_dir, url_path)
    if path is None:
        return None
    return format(path.stat().st_mtime_ns // 1_000_000, 'x')


def stamp_html(html: str, web_dir: Path) -> str:
    """Append the current file fingerprint to every local ``/js|/css ?v=`` URL."""
    def repl(m: re.Match) -> str:
        fp = fingerprint(web_dir, m.group('path'))
        if fp is None:
            return m.group(0)
        tag = m.group('tag').split(_SEP, 1)[0]
        return f"{m.group('pre')}{m.group('path')}?v={tag}{_SEP}{fp}"
    return _ASSET_URL.sub(repl, html)


def is_current(web_dir: Path, url_path: str, v: str) -> bool:
    """True when ``v`` carries the asset's current fingerprint (safe to cache)."""
    if _SEP not in v:
        return False
    return v.rsplit(_SEP, 1)[1] == fingerprint(web_dir, url_path)
