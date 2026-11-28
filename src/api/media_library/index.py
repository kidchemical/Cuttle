"""Pure media-ref extraction and item shaping for the Media App.

No DB, Flask or filesystem access: callers pass message rows in and get refs
out. Markdown / ``<media>`` parsing reuses :mod:`api.shared_media` (the owner
of chat media syntax) so the index and the chat renderer agree on what counts.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote

from api.shared_media import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    POSTER_SUFFIX,
    VIDEO_EXTS,
    _MD_IMAGE_RE,
    _MEDIA_SRC_RE,
    split_md_image_dest,
)

# Fenced / inline code shows media syntax as text; it is not a shared file.
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)
# Staging suffix added by shared_media.stage_file: ``shot-1a2b3c4d5e.png``.
_STAGED_SUFFIX_RE = re.compile(r"-[a-f0-9]{8,12}(?=\.[^.]+$)", re.I)

SOURCE_UPLOAD = "upload"   # file the user attached in the composer
SOURCE_USER = "user"       # markdown image the user typed / pasted
SOURCE_AGENT = "agent"     # media an agent shared in its reply


def _path_of(url: str) -> str:
    return str(url or "").split("?", 1)[0].split("#", 1)[0]


def kind_of(url: str, mime: str = "") -> Optional[str]:
    """``image`` / ``video`` / ``audio`` for a media URL (or attachment mime), else None."""
    m = str(mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    path = _path_of(url).lower()
    if path.endswith(POSTER_SUFFIX):
        return None  # derived thumb of a video, never shared on its own
    ext = PurePosixPath(path).suffix
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def is_indexable_url(url: str) -> bool:
    """Hosted media only: ``/output/…`` files and absolute http(s) links."""
    u = str(url or "").strip()
    return u.startswith("/output/") or bool(re.match(r"^https?://", u, re.I))


def display_name(url: str, filename: str = "") -> str:
    """Human file name: the upload's name, else the URL basename minus staging hash."""
    if filename:
        return str(filename)
    base = unquote(PurePosixPath(_path_of(url)).name) or "media"
    return _STAGED_SUFFIX_RE.sub("", base)


def _metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def is_in_progress(metadata: Any) -> bool:
    return bool(_metadata(metadata).get("in_progress"))


def extract_refs(role: str, content: str, metadata: Any = None) -> List[Dict[str, Any]]:
    """Every image/video/audio one message shares, in order, each URL once.

    Returns ``{url, kind, name, title, source}`` dicts. ``title`` is the
    markdown alt / title text (the lightbox caption), ``source`` one of the
    ``SOURCE_*`` values.
    """
    refs: List[Dict[str, Any]] = []
    seen = set()

    def add(url: str, *, source: str, mime: str = "", filename: str = "", title: str = "") -> None:
        u = str(url or "").strip()
        if u.startswith("<") and u.endswith(">"):
            u = u[1:-1].strip()
        if not u or u in seen or not is_indexable_url(u):
            return
        kind = kind_of(u, mime)
        if not kind:
            return
        seen.add(u)
        refs.append({
            "url": u,
            "kind": kind,
            "name": display_name(u, filename),
            "title": str(title or "").strip(),
            "source": source,
        })

    for att in _metadata(metadata).get("attachments") or []:
        if isinstance(att, dict):
            add(att.get("url") or "", source=SOURCE_UPLOAD,
                mime=str(att.get("mime") or ""), filename=str(att.get("filename") or ""))

    text = _FENCE_RE.sub(" ", str(content or ""))
    inline_source = SOURCE_AGENT if role == "assistant" else SOURCE_USER
    inline = []  # (offset, url, title) in document order across both syntaxes
    for m in _MD_IMAGE_RE.finditer(text):
        url, desc = split_md_image_dest(m.group(2))
        inline.append((m.start(), url, m.group(1) or desc or ""))
    for m in _MEDIA_SRC_RE.finditer(text):
        inline.append((m.start(), m.group(2), ""))
    for _pos, url, title in sorted(inline, key=lambda t: t[0]):
        add(url, source=inline_source, title=title)
    return refs


def chat_handle(session_id: int, share_index: Optional[int] = None) -> str:
    """``CH-000182`` / ``CH-000182-23`` (same format as ``api.chat_cli``)."""
    base = f"CH-{int(session_id):06d}"
    return base if not share_index else f"{base}-{int(share_index)}"


def iso_utc(ts: Any) -> str:
    """SQLite ``CURRENT_TIMESTAMP`` (UTC, no zone) → ISO-8601 with ``Z``."""
    s = str(ts or "").strip()
    if not s:
        return ""
    s = s.replace(" ", "T", 1)
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    return s


def build_items(rows: Iterable[Dict[str, Any]], file_info) -> List[Dict[str, Any]]:
    """Shape message rows into Media items, newest message first.

    ``rows`` carry ``id, chat_session_id, role, content, metadata, timestamp,
    share_index, session_name, project_name, is_archived, starred``.
    ``file_info(url)`` returns ``{available, size}`` (``available`` None when
    unknown, e.g. an external link). One item per (chat, URL): a file an agent
    re-links later in the same chat keeps its first share, with ``refs`` the
    number of messages that showed it.
    """
    by_key: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for row in sorted(rows, key=lambda r: int(r["id"])):
        if is_in_progress(row.get("metadata")):
            continue
        sid = int(row["chat_session_id"])
        for pos, ref in enumerate(extract_refs(row.get("role") or "", row.get("content") or "", row.get("metadata"))):
            key = (sid, ref["url"])
            if key in by_key:
                by_key[key]["refs"] += 1
                continue
            info = file_info(ref["url"]) or {}
            idx = row.get("share_index")
            by_key[key] = {
                "id": f"{int(row['id'])}:{pos}",
                **ref,
                "role": row.get("role") or "",
                "message_id": int(row["id"]),
                "share_index": int(idx) if idx else None,
                "handle": chat_handle(sid, idx),
                "shared_at": iso_utc(row.get("timestamp")),
                "chat_id": sid,
                "chat_title": str(row.get("session_name") or "").strip(),
                "project": str(row.get("project_name") or "").strip(),
                "chat_archived": bool(row.get("is_archived")),
                "chat_starred": bool(row.get("starred")),
                "available": info.get("available"),
                "size": info.get("size"),
                "refs": 1,
            }
            order.append(key)
    return [by_key[k] for k in reversed(order)]


def summarize_chats(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chats that shared media, most recent share first, with counts."""
    chats: Dict[int, Dict[str, Any]] = {}
    for it in items:
        c = chats.get(it["chat_id"])
        if c is None:
            c = chats[it["chat_id"]] = {
                "id": it["chat_id"], "handle": chat_handle(it["chat_id"]),
                "title": it["chat_title"], "project": it["project"],
                "archived": it["chat_archived"], "count": 0, "last": it["shared_at"],
            }
        c["count"] += 1
        if it["shared_at"] > c["last"]:
            c["last"] = it["shared_at"]
    return sorted(chats.values(), key=lambda c: c["last"], reverse=True)
