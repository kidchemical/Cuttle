"""User-scoped Media index: read chat history, check files, shape items.

Read-only. Scope is the caller's own live chats (``chat_sessions.user_id``,
``is_active = 1``), archived chats included and flagged — the same set whose
uploads ``/output/uploads/<chat>/…`` lets this user read.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote

from api.media_library import index

# Share index = 1-based position among user+assistant rows (CH-…-N), the
# same count as AuthDatabase.get_message_by_share_index. The LIKE prefilter
# only narrows rows; index.extract_refs decides what is media.
_QUERY = """
WITH idx AS (
    SELECT m.id, ROW_NUMBER() OVER (PARTITION BY m.chat_session_id ORDER BY m.id) AS share_index
    FROM chat_messages m JOIN chat_sessions s ON s.id = m.chat_session_id
    WHERE s.user_id = ? AND s.is_active = 1 AND m.role IN ('user', 'assistant')
)
SELECT m.id, m.chat_session_id, m.role, m.content, m.timestamp, m.metadata, idx.share_index,
       s.session_name, s.project_name, s.is_archived, s.starred
FROM idx
JOIN chat_messages m ON m.id = idx.id
JOIN chat_sessions s ON s.id = m.chat_session_id
WHERE (m.content LIKE '%![%' OR m.content LIKE '%<media%' OR m.metadata LIKE '%"attachments"%')
"""


def _output_root() -> Path:
    from core.runtime_paths import output_dir

    return output_dir().resolve()


def file_info_for(output_root: Path):
    """``url → {available, size}``; hosted files are stat'ed inside ``output_root`` only."""
    root = Path(output_root).resolve()

    def info(url: str) -> Dict[str, Any]:
        u = str(url or "")
        if not u.startswith("/output/"):
            return {"available": None, "size": None}  # external link: unknown
        rel = unquote(u[len("/output/"):].split("?", 1)[0].split("#", 1)[0])
        try:
            path = (root / rel).resolve()
            path.relative_to(root)
            st = path.stat()
        except (OSError, ValueError):
            return {"available": False, "size": None}
        return {"available": path.is_file(), "size": int(st.st_size)}

    return info


def list_media(user_id: int, *, db=None, output_root: Optional[Path] = None,
               chat_id: Optional[int] = None) -> Dict[str, Any]:
    """Every image/video/audio in ``user_id``'s chats, newest first, plus chat summaries."""
    if db is None:
        from api.auth_db import get_auth_db

        db = get_auth_db()
    sql, params = _QUERY, [int(user_id)]
    if chat_id is not None:
        sql += " AND m.chat_session_id = ?"
        params.append(int(chat_id))
    with closing(db._get_connection()) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    items = index.build_items(rows, file_info_for(output_root or _output_root()))
    images = sum(1 for it in items if it["kind"] == "image")
    videos = sum(1 for it in items if it["kind"] == "video")
    audios = sum(1 for it in items if it["kind"] == "audio")
    return {
        "items": items,
        "chats": index.summarize_chats(items),
        "totals": {
            "items": len(items),
            "images": images,
            "videos": videos,
            "audios": audios,
            "available": sum(1 for it in items if it["available"] is not False),
        },
    }
