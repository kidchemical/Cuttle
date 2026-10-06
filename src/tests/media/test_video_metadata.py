"""Wallpaper video metadata (``src/api/video_metadata.py`` + its settings route).

The playlist rows in Settings show a title, an author, and a thumbnail. The
page cannot ask YouTube itself (oEmbed sends no CORS headers), so Flask proxies
the lookup. These tests pin the parts that must not silently rot:

* the id parser agrees with ``src/web/js/media/youtube_id.js`` on every URL shape the
  wallpaper player accepts — if those two disagree, the row shows one video's
  title above another video's URL,
* the thumbnail URL is *derived* from the id, so the common case costs no extra
  request and a dead thumbnail still degrades to the placeholder,
* results are cached per id and network failures never raise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "media/youtube_id.js"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api import video_metadata  # noqa: E402

WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SHORT = "https://youtu.be/9bZkp7q19f0"
EMBED = "https://www.youtube.com/embed/aaaaaaaaaaa"
MP4 = "https://example.com/clip.mp4"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    video_metadata.clear_cache()
    yield
    video_metadata.clear_cache()


class _Resp:
    def __init__(self, payload=None, ok=True):
        self._payload = payload
        self.ok = ok
        self.status_code = 200 if ok else 500

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _stub_requests(monkeypatch, payload=None, ok=True, raise_exc=False):
    calls = []

    class _Requests:
        @staticmethod
        def get(url, params=None, timeout=None):
            calls.append((url, params, timeout))
            if raise_exc:
                raise RuntimeError("network down")
            return _Resp(payload, ok)

    monkeypatch.setitem(sys.modules, "requests", _Requests)
    return calls


# ── id parsing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        WATCH,
        "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&t=42s",
        SHORT,
        "https://youtu.be/9bZkp7q19f0?t=10",
        EMBED,
        "https://www.youtube.com/shorts/aaaaaaaaaaa",
        "https://www.youtube.com/live/aaaaaaaaaaa",
        "  " + SHORT + "  ",
    ],
)
def test_every_player_url_shape_resolves(url):
    assert video_metadata.parse_youtube_id(url)


@pytest.mark.parametrize(
    "url",
    [MP4, "https://example.com/clip.webm", "", "   ", None, 123,
     "https://vimeo.com/12345", "https://www.youtube.com/"],
)
def test_non_youtube_urls_do_not_resolve(url):
    assert video_metadata.parse_youtube_id(url) is None


@node_only
def test_parser_agrees_with_youtube_id_js():
    """One parser, two runtimes: a drift shows a title on the wrong row."""
    harness = r"""
const fs = require('fs');
global.window = {};
eval(fs.readFileSync(process.env.MOD_JS, 'utf8'));
const cases = JSON.parse(process.env.CASES);
process.stdout.write(JSON.stringify(cases.map((c) => global.window.parseYouTubeVideoId(c))));
"""
    import os

    urls = [
        WATCH,
        "https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&t=42s",
        SHORT,
        "https://youtu.be/9bZkp7q19f0?t=10",
        EMBED,
        "https://www.youtube.com/shorts/aaaaaaaaaaa",
        "https://www.youtube.com/live/aaaaaaaaaaa",
        MP4,
        "https://example.com/clip.webm",
        "https://vimeo.com/12345",
        "",
        "https://www.youtube.com/",
    ]
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=30,
        env={
            "PATH": os.environ["PATH"],
            "MOD_JS": str(MOD_JS),
            "CASES": json.dumps(urls),
        },
    )
    assert proc.returncode == 0, proc.stderr
    from_js = json.loads(proc.stdout)
    from_py = [video_metadata.parse_youtube_id(u) for u in urls]
    assert from_js == from_py


# ── metadata resolution ───────────────────────────────────────────────────


def test_thumbnail_is_derived_from_the_id():
    assert video_metadata.thumbnail_url("dQw4w9WgXcQ") == (
        "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
    )


def test_non_youtube_url_is_reported_unsupported_without_a_fetch(monkeypatch):
    calls = _stub_requests(monkeypatch, {"title": "nope"})
    meta = video_metadata.resolve_video_metadata(MP4)
    assert meta["supported"] is False
    assert meta["title"] == ""
    assert calls == []


def test_empty_url_is_unsupported():
    assert video_metadata.resolve_video_metadata("")["supported"] is False
    assert video_metadata.resolve_video_metadata(None)["supported"] is False


def test_title_and_author_come_from_oembed(monkeypatch):
    calls = _stub_requests(monkeypatch, {"title": "Never Gonna Give You Up",
                                         "author_name": "Rick Astley"})
    meta = video_metadata.resolve_video_metadata(WATCH)
    assert meta["supported"] is True
    assert meta["title"] == "Never Gonna Give You Up"
    assert meta["author"] == "Rick Astley"
    assert meta["thumbnail"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
    assert meta["cached"] is False
    assert calls[0][1]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_second_lookup_is_cached_per_video_id(monkeypatch):
    calls = _stub_requests(monkeypatch, {"title": "Gangnam Style",
                                         "author_name": "officialpsy"})
    video_metadata.resolve_video_metadata(SHORT)
    again = video_metadata.resolve_video_metadata(SHORT)
    assert len(calls) == 1
    assert again["cached"] is True
    assert again["title"] == "Gangnam Style"


def test_cache_key_is_the_video_id_not_the_url_shape(monkeypatch):
    calls = _stub_requests(monkeypatch, {"title": "Same video"})
    video_metadata.resolve_video_metadata(WATCH)
    video_metadata.resolve_video_metadata("https://www.youtube.com/embed/dQw4w9WgXcQ")
    assert len(calls) == 1


def test_network_failure_still_renders_the_row(monkeypatch):
    _stub_requests(monkeypatch, raise_exc=True)
    meta = video_metadata.resolve_video_metadata(WATCH)
    assert meta["supported"] is True
    assert meta["title"] == ""
    assert meta["thumbnail"].endswith("dQw4w9WgXcQ/mqdefault.jpg")


def test_non_json_response_does_not_raise(monkeypatch):
    _stub_requests(monkeypatch, None)
    meta = video_metadata.resolve_video_metadata(WATCH)
    assert meta["supported"] is True
    assert meta["title"] == ""


def test_private_video_falls_back_to_thumbnail_only(monkeypatch):
    _stub_requests(monkeypatch, ok=False)
    meta = video_metadata.resolve_video_metadata(WATCH)
    assert meta["supported"] is True
    assert meta["title"] == ""
    assert meta["thumbnail"]


def test_cache_evicts_instead_of_growing_without_bound(monkeypatch):
    _stub_requests(monkeypatch, {"title": "x"})
    for i in range(video_metadata.CACHE_MAX_ENTRIES + 20):
        video_metadata.resolve_video_metadata(
            f"https://www.youtube.com/watch?v={i:011d}"
        )
    assert len(video_metadata._cache) <= video_metadata.CACHE_MAX_ENTRIES


def test_cache_is_bounded_after_realistic_use(monkeypatch):
    _stub_requests(monkeypatch, {"title": "x"})
    video_metadata.resolve_video_metadata(WATCH)
    assert video_metadata.parse_youtube_id(WATCH) in video_metadata._cache


# ── route ─────────────────────────────────────────────────────────────────


def test_route_returns_metadata_and_requires_authentication(monkeypatch):
    from api import web_chat_api as wca

    _stub_requests(monkeypatch, {"title": "nope"})
    anon = wca.app.test_client()
    res = anon.get("/api/settings/video-metadata", query_string={"url": WATCH})
    assert res.status_code == 401, res.get_json()


def test_route_shape(monkeypatch):
    from api import web_chat_api as wca

    _stub_requests(monkeypatch, {"title": "Route Title", "author_name": "Author"})
    client = wca.app.test_client()
    client.post("/api/auth/guest", json={})
    res = client.get("/api/settings/video-metadata", query_string={"url": WATCH})
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()
    assert payload["success"] is True
    meta = payload["metadata"]
    assert meta["supported"] is True
    assert meta["title"] == "Route Title"
    assert meta["author"] == "Author"
    assert meta["thumbnail"].endswith("dQw4w9WgXcQ/mqdefault.jpg")


def test_route_handles_missing_and_unsupported_urls(monkeypatch):
    from api import web_chat_api as wca

    _stub_requests(monkeypatch, {"title": "never used"})
    client = wca.app.test_client()
    client.post("/api/auth/guest", json={})
    for url in (None, "", MP4):
        query = {"url": url} if url is not None else {}
        res = client.get("/api/settings/video-metadata", query_string=query)
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["metadata"]["supported"] is False


def test_route_is_registered_in_the_settings_family_registry():
    from api import settings_routes

    names = {row["name"] for row in settings_routes.SETTING_FAMILIES}
    assert "video-metadata" in names