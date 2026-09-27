"""
Fetch and normalize RSS/Atom feeds for the Feed Agent pipeline.

YouTube channel feeds expose media:thumbnail; many news outlets use RSS 2.0 with
media:thumbnail or enclosure. Outputs JSON consumed by the curation step.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, unquote

import requests

# Sensible defaults when preferences have no rss_feeds yet (first-run / migration).
DEFAULT_RSS_FEEDS: Tuple[str, ...] = (
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCHnyfMqiRRG1u-2MsSQLbXA",
)

_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; CuttleFeed/1.0; +https://github.com/) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _local_tag(tag: str) -> str:
    if not tag:
        return ""
    if tag[0] == "{":
        return tag.split("}", 1)[-1]
    return tag


def _direct_children(parent: ET.Element, name: str) -> List[ET.Element]:
    return [c for c in list(parent) if _local_tag(c.tag) == name]


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _pick_thumbnail(elem: ET.Element) -> str:
    """Largest media:thumbnail url under elem (YouTube publishes several sizes)."""
    candidates: List[Tuple[int, str]] = []
    for node in elem.iter():
        if _local_tag(node.tag) != "thumbnail":
            continue
        url = (node.attrib.get("url") or "").strip()
        if not url:
            continue
        try:
            w = int(node.attrib.get("width") or 0)
        except ValueError:
            w = 0
        candidates.append((w, url))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def _youtube_thumb_from_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "youtube.com/watch" in u:
        q = parse_qs(urlparse(u).query)
        ids = q.get("v") or []
        vid = ids[0] if ids else ""
    elif "youtu.be/" in u:
        vid = urlparse(u).path.strip("/").split("/")[0]
    else:
        return ""
    if not vid or len(vid) < 6:
        return ""
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def _strip_html(s: str) -> str:
    if not s:
        return ""
    t = re.sub(r"<[^>]+>", " ", s)
    t = re.sub(r"\s+", " ", t).strip()
    return unquote(t)[:2000]


def _enrich_item(item: Dict[str, Any]) -> Dict[str, Any]:
    url = (item.get("url") or "").strip()
    typ = (item.get("type") or "").lower()
    if "youtube.com" in url or "youtu.be" in url:
        item["type"] = "youtube"
        if not (item.get("thumbnail") or "").strip():
            t = _youtube_thumb_from_url(url)
            if t:
                item["thumbnail"] = t
    elif typ == "youtube" and url:
        if not (item.get("thumbnail") or "").strip():
            t = _youtube_thumb_from_url(url)
            if t:
                item["thumbnail"] = t
    return item


def _parse_atom(root: ET.Element, feed_url: str, max_items: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    feed_title = ""
    ft_el = root.find("{http://www.w3.org/2005/Atom}title")
    if ft_el is None:
        for ch in root:
            if _local_tag(ch.tag) == "title":
                ft_el = ch
                break
    if ft_el is not None:
        feed_title = _text(ft_el)

    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if not entries:
        entries = _direct_children(root, "entry")

    for entry in entries[:max_items]:
        title = ""
        for ch in entry:
            if _local_tag(ch.tag) == "title":
                title = _text(ch)
                break
        link_href = ""
        for ch in entry:
            if _local_tag(ch.tag) != "link":
                continue
            if ch.attrib.get("rel", "alternate") in ("alternate", ""):
                link_href = (ch.attrib.get("href") or "").strip()
                if link_href:
                    break
        if not link_href:
            for ch in entry:
                if _local_tag(ch.tag) == "link":
                    link_href = (ch.attrib.get("href") or "").strip()
                    if link_href:
                        break
        summary = ""
        for tag in ("summary", "content"):
            for ch in entry:
                if _local_tag(ch.tag) == tag:
                    summary = _strip_html(_text(ch))
                    if summary:
                        break
            if summary:
                break
        thumb = _pick_thumbnail(entry)
        published = ""
        for ch in entry:
            if _local_tag(ch.tag) in ("published", "updated"):
                published = (ch.text or "").strip()
                if published:
                    break

        itype = "article"
        if "youtube.com" in link_href or "youtu.be" in link_href:
            itype = "youtube"

        item = {
            "type": itype,
            "title": title or "(untitled)",
            "url": link_href,
            "excerpt": summary[:1200] if summary else "",
            "thumbnail": thumb,
            "meta": feed_title or feed_url,
            "feed_source": feed_title or feed_url,
            "published": published,
        }
        out.append(_enrich_item(item))
    return out


def _parse_rss(root: ET.Element, feed_url: str, max_items: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    channel = None
    for ch in root:
        if _local_tag(ch.tag) == "channel":
            channel = ch
            break
    if channel is None:
        return out

    feed_title = ""
    for ch in channel:
        if _local_tag(ch.tag) == "title":
            feed_title = _text(ch)
            break

    items: List[ET.Element] = []
    for ch in channel:
        if _local_tag(ch.tag) == "item":
            items.append(ch)

    for entry in items[:max_items]:
        title = ""
        link_u = ""
        desc = ""
        pub = ""
        for ch in entry:
            ln = _local_tag(ch.tag)
            if ln == "title":
                title = _text(ch)
            elif ln == "link":
                link_u = _text(ch)
            elif ln == "description":
                desc = _strip_html(_text(ch))
            elif ln == "pubDate":
                pub = _text(ch)
        thumb = _pick_thumbnail(entry)
        if not thumb:
            enc = None
            for ch in entry:
                if _local_tag(ch.tag) == "enclosure":
                    enc = ch
                    break
            if enc is not None:
                t = (enc.attrib.get("type") or "").lower()
                href = (enc.attrib.get("url") or "").strip()
                if href and t.startswith("image/"):
                    thumb = href

        itype = "article"
        if "youtube.com" in link_u or "youtu.be" in link_u:
            itype = "youtube"

        item = {
            "type": itype,
            "title": title or "(untitled)",
            "url": link_u,
            "excerpt": desc[:1200] if desc else "",
            "thumbnail": thumb,
            "meta": feed_title or feed_url,
            "feed_source": feed_title or feed_url,
            "published": pub,
        }
        out.append(_enrich_item(item))
    return out


def fetch_and_parse_feed(feed_url: str, timeout: float, max_items: int) -> List[Dict[str, Any]]:
    """HTTP GET feed_url and return normalized items (may be empty on parse failure)."""
    feed_url = (feed_url or "").strip()
    if not feed_url:
        return []

    r = requests.get(
        feed_url,
        timeout=timeout,
        headers={"User-Agent": _DEFAULT_UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
    )
    r.raise_for_status()
    text = r.content.decode(r.encoding or "utf-8", errors="replace")
    root = ET.fromstring(text)

    tag = _local_tag(root.tag)
    if tag == "feed":
        return _parse_atom(root, feed_url, max_items)
    if tag == "rss":
        return _parse_rss(root, feed_url, max_items)
    return []


def resolve_feed_urls(config: Dict[str, Any], prefs: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    merge_with_prefs = config.get("mergeWithPreferences", True)
    if merge_with_prefs is not False:
        lst = prefs.get("rss_feeds") or []
        if isinstance(lst, list):
            urls.extend(str(x).strip() for x in lst if str(x).strip())

    extra = config.get("feedUrls") or ""
    if isinstance(extra, str):
        for line in extra.splitlines():
            u = line.strip()
            if u and not u.startswith("#"):
                urls.append(u)

    seen = set()
    ordered: List[str] = []
    for u in urls:
        k = u.lower()
        if k in seen:
            continue
        seen.add(k)
        ordered.append(u)

    if not ordered:
        ordered = list(DEFAULT_RSS_FEEDS)
    return ordered


def run_rss_ingest_node(config: Dict[str, Any], trigger_context: str) -> Dict[str, Any]:
    """Pipeline /api/execute-tool entry for tool-rss-ingest."""
    from managers.feed_preferences import load_preferences

    try:
        prefs = load_preferences()
        urls = resolve_feed_urls(config or {}, prefs)
        per_feed = max(1, min(50, int(config.get("maxItemsPerFeed", 12) or 12)))
        total_cap = max(1, min(200, int(config.get("maxTotalItems", 80) or 80)))
        timeout = float(config.get("timeoutSeconds", 25) or 25)

        collected: List[Dict[str, Any]] = []
        errors: List[str] = []

        for url in urls:
            try:
                batch = fetch_and_parse_feed(url, timeout=timeout, max_items=per_feed)
                for it in batch:
                    it["feed_url"] = url
                collected.extend(batch)
            except Exception as ex:
                errors.append(f"{url}: {ex}")

        deduped: List[Dict[str, Any]] = []
        seen_u = set()
        for it in collected:
            u = (it.get("url") or "").strip().lower()
            if not u or u in seen_u:
                continue
            seen_u.add(u)
            deduped.append(it)
            if len(deduped) >= total_cap:
                break

        payload = {
            "trigger_context": trigger_context,
            "rss_items": deduped,
            "ingest_errors": errors,
            "feeds_fetched": len(urls),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "success": True,
            "output": json.dumps(payload, ensure_ascii=False),
            "error": None,
        }
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
