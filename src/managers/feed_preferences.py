"""
User preferences for the home feed (keywords, sources, interests).
Stored at src/output/feed/preferences.json; consumed by Feed_Agent and the cuttle_feed_preferences LLM tool.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_MAX_KEYWORDS = 50
_MAX_SOURCES = 30
_MAX_RSS_FEEDS = 40
_MAX_RSS_URL_LEN = 500
_MAX_INTERESTS_LEN = 4000
_MAX_ITEM_LEN = 120


def _prefs_path() -> Path:
    return Path(__file__).resolve().parent.parent / "output" / "feed" / "preferences.json"


def _normalize_chip(s: str) -> str:
    t = (s or "").strip()
    if len(t) > _MAX_ITEM_LEN:
        t = t[:_MAX_ITEM_LEN]
    return t


def _dedupe_ci(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        n = _normalize_chip(x)
        if not n:
            continue
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def _normalize_rss_url(s: str) -> str:
    t = (s or "").strip()
    if len(t) > _MAX_RSS_URL_LEN:
        t = t[:_MAX_RSS_URL_LEN]
    return t


def _dedupe_urls(urls: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in urls:
        n = _normalize_rss_url(x)
        if not n:
            continue
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def default_preferences() -> Dict[str, Any]:
    from managers.rss_feed_ingest import DEFAULT_RSS_FEEDS

    return {
        "keywords": [],
        "sources": [],
        "rss_feeds": list(DEFAULT_RSS_FEEDS),
        "interests": "",
        "updated_at": None,
    }


def load_preferences() -> Dict[str, Any]:
    path = _prefs_path()
    if not path.exists():
        return default_preferences()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default_preferences()
        out = default_preferences()
        kw = data.get("keywords") or []
        src = data.get("sources") or []
        rss = data.get("rss_feeds")
        if isinstance(kw, list):
            out["keywords"] = _dedupe_ci([str(x) for x in kw])[:_MAX_KEYWORDS]
        if isinstance(src, list):
            out["sources"] = _dedupe_ci([str(x) for x in src])[:_MAX_SOURCES]
        if isinstance(rss, list):
            out["rss_feeds"] = _dedupe_urls([str(x) for x in rss])[:_MAX_RSS_FEEDS]
        elif "rss_feeds" not in data:
            pass
        else:
            out["rss_feeds"] = []
        interests = data.get("interests")
        if isinstance(interests, str):
            out["interests"] = interests.strip()[:_MAX_INTERESTS_LEN]
        out["updated_at"] = data.get("updated_at")
        return out
    except Exception:
        return default_preferences()


def save_preferences(data: Dict[str, Any]) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["keywords"] = _dedupe_ci([str(x) for x in (data.get("keywords") or [])])[:_MAX_KEYWORDS]
    data["sources"] = _dedupe_ci([str(x) for x in (data.get("sources") or [])])[:_MAX_SOURCES]
    data["rss_feeds"] = _dedupe_urls([str(x) for x in (data.get("rss_feeds") or [])])[:_MAX_RSS_FEEDS]
    interests = data.get("interests") or ""
    if not isinstance(interests, str):
        interests = str(interests)
    data["interests"] = interests.strip()[:_MAX_INTERESTS_LEN]
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def augment_feed_trigger_message(base: str) -> str:
    """Append user preferences to the Feed Agent trigger message (LLM user prompt)."""
    prefs = load_preferences()
    parts: List[str] = []
    if prefs.get("keywords"):
        parts.append(
            "Keyword topics (prioritize matching ideas, links, and videos): "
            + ", ".join(prefs["keywords"])
        )
    if prefs.get("sources"):
        parts.append(
            "Preferred outlets / sources to include when relevant: " + ", ".join(prefs["sources"])
        )
    if (prefs.get("interests") or "").strip():
        parts.append("Additional instructions: " + (prefs["interests"] or "").strip())
    if not parts:
        return base if isinstance(base, str) else str(base)
    b = (base or "").strip()
    block = "\n".join(parts)
    return (
        b
        + "\n\n--- USER FEED PREFERENCES (must honor) ---\n"
        + block
    )


def _remove_ci(items: List[str], to_remove: List[str]) -> List[str]:
    rset = { _normalize_chip(x).lower() for x in to_remove }
    return [x for x in items if _normalize_chip(x).lower() not in rset]


def apply_patch(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Merge patch from API or tool; returns updated preferences dict."""
    cur = load_preferences()
    action = (arguments or {}).get("action") or "patch"

    if action == "get":
        return cur

    if action not in ("patch", "replace"):
        action = "patch"

    add_kw = (arguments or {}).get("add_keywords") or []
    rm_kw = (arguments or {}).get("remove_keywords") or []
    add_src = (arguments or {}).get("add_sources") or []
    rm_src = (arguments or {}).get("remove_sources") or []
    add_rss = (arguments or {}).get("add_rss_feeds") or []
    rm_rss = (arguments or {}).get("remove_rss_feeds") or []

    if isinstance(add_kw, str):
        add_kw = [add_kw]
    if isinstance(rm_kw, str):
        rm_kw = [rm_kw]
    if isinstance(add_src, str):
        add_src = [add_src]
    if isinstance(rm_src, str):
        rm_src = [rm_src]
    if isinstance(add_rss, str):
        add_rss = [add_rss]
    if isinstance(rm_rss, str):
        rm_rss = [rm_rss]

    kw = list(cur.get("keywords") or [])
    src = list(cur.get("sources") or [])
    rss = list(cur.get("rss_feeds") or [])

    kw = _dedupe_ci(kw + [_normalize_chip(x) for x in add_kw if x])[:_MAX_KEYWORDS]
    kw = _remove_ci(kw, [str(x) for x in rm_kw if x])
    kw = _dedupe_ci(kw)[:_MAX_KEYWORDS]

    src = _dedupe_ci(src + [_normalize_chip(x) for x in add_src if x])[:_MAX_SOURCES]
    src = _remove_ci(src, [str(x) for x in rm_src if x])
    src = _dedupe_ci(src)[:_MAX_SOURCES]

    rss = _dedupe_urls(rss + [_normalize_rss_url(str(x)) for x in add_rss if x])[:_MAX_RSS_FEEDS]
    rm_rss_set = {_normalize_rss_url(str(x)).lower() for x in rm_rss if x}
    rss = [u for u in rss if _normalize_rss_url(u).lower() not in rm_rss_set]
    rss = _dedupe_urls(rss)[:_MAX_RSS_FEEDS]

    replace_interests = (arguments or {}).get("replace_interests")
    append_interests = (arguments or {}).get("append_interests")

    interests = cur.get("interests") or ""
    if not isinstance(interests, str):
        interests = str(interests)

    if isinstance(replace_interests, str) and replace_interests.strip():
        interests = replace_interests.strip()[:_MAX_INTERESTS_LEN]
    elif isinstance(append_interests, str) and append_interests.strip():
        extra = append_interests.strip()
        if interests:
            interests = (interests.rstrip() + "\n\n" + extra)[:_MAX_INTERESTS_LEN]
        else:
            interests = extra[:_MAX_INTERESTS_LEN]

    cur["keywords"] = kw
    cur["sources"] = src
    cur["rss_feeds"] = rss
    cur["interests"] = interests[:_MAX_INTERESTS_LEN]
    save_preferences(cur)
    return load_preferences()


def invoke_feed_preferences_tool(arguments: Dict[str, Any]) -> str:
    """LLM tool entry: get or patch preferences; returns human-readable result."""
    try:
        args = arguments or {}
        action = (args.get("action") or "patch").lower()
        if action == "get":
            prefs = load_preferences()
            return (
                "Current feed preferences:\n"
                + json.dumps(prefs, indent=2, ensure_ascii=False)
                + "\n\nTell the user what is configured. To change preferences, call this tool again with "
                "add_keywords, remove_keywords, add_sources, remove_sources, add_rss_feeds, remove_rss_feeds, "
                "append_interests, or replace_interests."
            )
        updated = apply_patch(args)
        return (
            "Updated feed preferences successfully.\n"
            + json.dumps(updated, indent=2, ensure_ascii=False)
            + "\n\nConfirm briefly to the user what changed. New preferences apply to the next Feed Agent run "
            "(scheduled or when they click Refresh on the home feed)."
        )
    except Exception as e:
        return f"Error updating feed preferences: {e}"


def cuttle_feed_preferences_tool_specs() -> List[Dict[str, Any]]:
    """OpenAI/Anthropic function spec for tool-calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "cuttle_feed_preferences",
                    "description": (
                    "Read or update the user's personalized **home feed** interests (Cuttle home page). "
                    "Use when they ask to add/remove topics, news sources, RSS feed URLs (YouTube channel RSS, "
                    "NYT sections, blogs), YouTube interests, or to change what the Feed Agent should prioritize. "
                    "action=get returns current keywords, sources, rss_feeds, and notes. "
                    "action=patch with add_keywords, remove_keywords, add_sources, remove_sources, "
                    "add_rss_feeds, remove_rss_feeds, append_interests, or replace_interests updates stored preferences."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["get", "patch"],
                            "description": "get = read only; patch = apply adds/removes/interests updates",
                        },
                        "add_keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Topic chips to add (e.g. quantum computing, indie games)",
                        },
                        "remove_keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Keywords to remove",
                        },
                        "add_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Outlets or sites to favor (e.g. New York Times, Ars Technica)",
                        },
                        "remove_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Sources to remove",
                        },
                        "append_interests": {
                            "type": "string",
                            "description": "Freeform text to append to interests (e.g. more UFO YouTube, less politics)",
                        },
                        "replace_interests": {
                            "type": "string",
                            "description": "Replace the entire interests notes field (optional)",
                        },
                        "add_rss_feeds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "RSS or Atom feed URLs to merge (e.g. YouTube channel "
                            "https://www.youtube.com/feeds/videos.xml?channel_id=UC... , NYT RSS)",
                        },
                        "remove_rss_feeds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "RSS feed URLs to remove (exact or same URL as stored)",
                        },
                    },
                    "required": ["action"],
                },
            },
        }
    ]
