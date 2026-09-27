"""Agent profiles for sub-agent identity (name, avatar, harness preference).

Built-in profiles ship in code. Custom profiles live in ``agent_profiles``
(per user). Spawn child JSON may pin a catalog id, an inline ephemeral
object, or omit a profile entirely.

Harness ``none`` means no preference — spawn keeps the child's explicit
agent, otherwise the usual ``cursor`` default (or ``--route``).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, List, Optional

from api.subagents.types import ChildSpec

NONE_HARNESS = frozenset({"", "none", "inherit", "any"})
_HARNESS_ALIASES = {
    "cursor auto": "cursor",
    "cursor-auto": "cursor",
    "auto": "cursor",
    "muse code": "muse",
    "muse-code": "muse",
    "claude": "cursor",
    "codex cli": "codex",
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")

BUILTIN_PROFILES: List[Dict[str, str]] = [
    {
        "id": "cuttle",
        "name": "Cuttle",
        "avatar": "cuttle",
        "agent": "none",
        "model": "",
        "effort": "",
    },
    {
        "id": "scout",
        "name": "Scout",
        "avatar": "🔭",
        "agent": "cursor",
        "model": "",
        "effort": "",
    },
    {
        "id": "critic",
        "name": "Critic",
        "avatar": "🔍",
        "agent": "cursor",
        "model": "",
        "effort": "",
    },
    {
        "id": "builder",
        "name": "Builder",
        "avatar": "🛠️",
        "agent": "cursor",
        "model": "",
        "effort": "",
    },
    {
        "id": "chef",
        "name": "Chef",
        "avatar": "🍽️",
        "agent": "cursor",
        "model": "",
        "effort": "",
    },
    {
        "id": "muse",
        "name": "Muse",
        "avatar": "✨",
        "agent": "muse",
        "model": "",
        "effort": "",
    },
    {
        "id": "codex",
        "name": "Codex",
        "avatar": "📦",
        "agent": "codex",
        "model": "",
        "effort": "",
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "avatar": "⚡",
        "agent": "hermes",
        "model": "",
        "effort": "",
    },
]

BUILTIN_IDS = frozenset(p["id"] for p in BUILTIN_PROFILES)


def normalize_harness(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    s = _HARNESS_ALIASES.get(s, s)
    if s in NONE_HARNESS:
        return "none"
    return s


def slug_id(raw: Any) -> str:
    return str(raw or "").strip().lower().replace(" ", "-")


def public_profile(row: Dict[str, Any], *, builtin: bool = False) -> Dict[str, Any]:
    pid = str(row.get("id") or "")
    return {
        "id": pid,
        "name": str(row.get("name") or pid),
        "avatar": str(row.get("avatar") or ""),
        "agent": normalize_harness(row.get("agent")),
        "model": str(row.get("model") or ""),
        "effort": str(row.get("effort") or ""),
        "builtin": bool(builtin or row.get("builtin")),
        "ephemeral": bool(row.get("ephemeral")),
    }


def list_builtins() -> List[Dict[str, Any]]:
    return [public_profile(p, builtin=True) for p in BUILTIN_PROFILES]


def _get_custom(db, user_id: int, profile_id: str) -> Optional[Dict[str, Any]]:
    pid = slug_id(profile_id)
    if not pid:
        return None
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM agent_profiles
        WHERE user_id = ? AND id = ?
        LIMIT 1
        """,
        (int(user_id), pid),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_custom(db, user_id: int) -> List[Dict[str, Any]]:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM agent_profiles
        WHERE user_id = ?
        ORDER BY name COLLATE NOCASE ASC, id ASC
        """,
        (int(user_id),),
    )
    rows = cur.fetchall()
    conn.close()
    return [public_profile(dict(r), builtin=False) for r in rows]


def list_profiles(db, user_id: int) -> List[Dict[str, Any]]:
    custom = list_custom(db, user_id)
    custom_ids = {c["id"] for c in custom}
    out = [p for p in list_builtins() if p["id"] not in custom_ids]
    out.extend(custom)
    out.sort(key=lambda p: (not p["builtin"], p["name"].lower(), p["id"]))
    return out


def resolve(db, user_id: int, profile_id: str) -> Optional[Dict[str, Any]]:
    pid = slug_id(profile_id)
    if not pid:
        return None
    custom = _get_custom(db, user_id, pid)
    if custom:
        return public_profile(custom, builtin=False)
    for p in BUILTIN_PROFILES:
        if p["id"] == pid:
            return public_profile(p, builtin=True)
    return None


def save_profile(
    db,
    user_id: int,
    *,
    profile_id: str,
    name: str,
    avatar: str = "",
    agent: str = "none",
    model: str = "",
    effort: str = "",
) -> Dict[str, Any]:
    pid = slug_id(profile_id)
    if not _ID_RE.match(pid):
        raise ValueError(
            "profile id must be 1–40 chars, start with a letter, and use a-z 0-9 _ -"
        )
    if pid in BUILTIN_IDS:
        raise ValueError(f"cannot overwrite built-in profile {pid!r}")
    display = (name or "").strip()[:80]
    if not display:
        raise ValueError("profile name is required")
    harness = normalize_harness(agent)
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_profiles (
            id, user_id, name, avatar, agent, model, effort, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, id) DO UPDATE SET
            name = excluded.name,
            avatar = excluded.avatar,
            agent = excluded.agent,
            model = excluded.model,
            effort = excluded.effort,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            pid,
            int(user_id),
            display,
            (avatar or "").strip()[:200] or None,
            harness,
            (model or "").strip()[:80] or None,
            (effort or "").strip()[:40] or None,
        ),
    )
    conn.commit()
    conn.close()
    row = resolve(db, user_id, pid)
    if not row:
        raise ValueError("failed to save profile")
    return row


def delete_profile(db, user_id: int, profile_id: str) -> bool:
    pid = slug_id(profile_id)
    if pid in BUILTIN_IDS:
        raise ValueError(f"cannot delete built-in profile {pid!r}")
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM agent_profiles WHERE user_id = ? AND id = ?",
        (int(user_id), pid),
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n > 0


def infer_user_id(
    db,
    *,
    user_id: Optional[int] = None,
    parent_session_id: Optional[int] = None,
) -> int:
    if user_id is not None:
        return int(user_id)
    if parent_session_id is not None:
        row = db.get_chat_session_by_id(int(parent_session_id))
        if row:
            return int(row["user_id"])
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if not row:
        raise ValueError("no user in auth db")
    return int(row["id"])


def apply_profile(spec: ChildSpec, *, db, user_id: int) -> ChildSpec:
    """Fill empty identity / harness fields from a catalog profile."""
    profile = None
    if spec.profile_id:
        profile = resolve(db, user_id, spec.profile_id)
    title = spec.title
    display = spec.display_name
    avatar = spec.avatar
    agent = spec.agent
    model = spec.model
    effort = spec.effort
    if profile:
        if not display:
            display = profile["name"]
        if not avatar:
            avatar = profile.get("avatar") or ""
        if not title:
            title = profile["name"]
        if not spec.agent_explicit:
            pagent = normalize_harness(profile.get("agent"))
            if pagent != "none":
                agent = pagent or agent
                if not spec.model:
                    model = profile.get("model") or model
                if not spec.effort:
                    effort = profile.get("effort") or effort
    if not title:
        title = display or "Subagent"
    if not display:
        display = title
    return replace(
        spec,
        title=str(title)[:80],
        display_name=str(display)[:80],
        avatar=str(avatar or "")[:200],
        agent=agent or "cursor",
        model=model,
        effort=effort,
    )
