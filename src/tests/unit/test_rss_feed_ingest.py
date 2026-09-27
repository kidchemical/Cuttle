"""RSS/Atom parsing for Feed Agent (YouTube thumbnails, etc.)."""

import xml.etree.ElementTree as ET

from managers.rss_feed_ingest import _parse_atom, _youtube_thumb_from_url


def test_youtube_thumb_from_watch_url():
    u = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "dQw4w9WgXcQ" in _youtube_thumb_from_url(u)
    assert "i.ytimg.com" in _youtube_thumb_from_url(u)


def test_parse_atom_youtube_thumbnail():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Test Channel</title>
  <entry>
    <title>Sample Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123xyz"/>
    <media:group>
      <media:title>Sample Video</media:title>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123xyz/hqdefault.jpg" width="480" height="360"/>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123xyz/default.jpg" width="120" height="90"/>
    </media:group>
  </entry>
</feed>"""
    root = ET.fromstring(xml)
    items = _parse_atom(root, "https://example.com/feed.xml", max_items=5)
    assert len(items) == 1
    assert items[0]["type"] == "youtube"
    assert "hqdefault" in (items[0].get("thumbnail") or "")
    assert items[0]["url"].endswith("abc123xyz")
