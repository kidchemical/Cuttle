"""Bridge Discord conversations into owner-owned Cuttle CH sessions.

Discord DMs (and guild mentions) used to run the same pipeline as web chat but
never created a ``chat_sessions`` row, so they were invisible in Cuttle history
and did not share Cursor Agent resume ids with ``CH-xxxxxx``.

This module:
- Resolves (or creates) a CH session owned by the hub account
- Stamps ``session_id = db_session_<id>`` so Cursor resume matches web chat
- Persists both sides of the turn into that session
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from api.auth_db import get_auth_db


def _hub_owner_user_id(db) -> Optional[int]:
    email = (os.getenv("OWNER_USER_EMAIL") or "").strip().lower()
    if email:
        user = db.get_user_by_email(email) or db.get_user_by_login(email)
        if user and user.get("id") is not None:
            return int(user["id"])
    return db.get_first_active_user_id()


def _display_name(user_context: Dict[str, Any]) -> str:
    for key in ("display_name", "username"):
        val = str(user_context.get(key) or "").strip()
        if val:
            return val[:80]
    uid = str(user_context.get("id") or "").strip()
    return uid or "Discord"


def origin_from_session_kind(session_kind: str) -> str:
    kind = (session_kind or "").strip().lower()
    if kind == "discord_guild":
        return "discord_guild"
    return "discord_dm"


def cuttle_session_key(db_sid: int) -> str:
    return f"db_session_{int(db_sid)}"


def resolve_discord_chat_session(
    user_context: Optional[Dict[str, Any]],
    session_data: Optional[Dict[str, Any]],
) -> Tuple[Optional[int], bool]:
    """Find or create the CH session for this Discord identity.

    Mutates ``session_data`` with ``session_id`` / ``chat_session_id`` when
    successful. Returns ``(db_session_id, created)``.
    """
    user_context = dict(user_context or {})
    session_data = session_data if isinstance(session_data, dict) else {}
    discord_uid = str(
        user_context.get("id") or session_data.get("user_id") or ""
    ).strip()
    if not discord_uid:
        return None, False

    origin = origin_from_session_kind(session_data.get("session_kind") or "")
    channel_id = str(
        user_context.get("channel_id") or session_data.get("channel_id") or ""
    ).strip()
    username = _display_name(user_context)

    try:
        db = get_auth_db()
        owner_id = _hub_owner_user_id(db)
        if owner_id is None:
            print("[DISCORD-CH] no hub user — skipping CH session")
            return None, False

        # Invites bind a Discord person to an existing CH (same Cursor resume).
        # Only DMs follow that link — guild mentions keep their own sessions.
        from_link = False
        existing = None
        if origin == "discord_dm":
            existing = db.find_discord_link(discord_uid)
            from_link = existing is not None
        if not existing:
            existing = db.find_discord_chat_session(
                origin=origin,
                discord_user_id=discord_uid,
                discord_channel_id=channel_id if origin != "discord_dm" else None,
            )
        created = False
        if existing:
            db_sid = int(existing.get("chat_session_id") or existing["id"])
            # Keep the handle current if they renamed on Discord.
            stored = str(existing.get("discord_username") or "").strip()
            if (not from_link) and username and username != stored:
                try:
                    conn = db._get_connection()
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE chat_sessions
                        SET discord_username = ?
                        WHERE id = ?
                        """,
                        (username, db_sid),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
        else:
            try:
                db_sid = db.create_discord_chat_session(
                    owner_id,
                    origin=origin,
                    discord_user_id=discord_uid,
                    discord_channel_id=channel_id,
                    discord_username=username,
                    session_name=username,
                )
                created = True
                print(f"[DISCORD-CH] created CH-{db_sid:06d} for {username} ({origin})")
            except Exception as e:
                # Unique index: a parallel DM already created the row.
                print(f"[DISCORD-CH] create raced ({e}); re-lookup")
                existing = db.find_discord_chat_session(
                    origin=origin,
                    discord_user_id=discord_uid,
                    discord_channel_id=channel_id if origin != "discord_dm" else None,
                )
                if not existing:
                    raise
                db_sid = int(existing["id"])
                created = False

        if origin == "discord_dm":
            try:
                db.upsert_discord_link(
                    db_sid,
                    discord_user_id=discord_uid,
                    discord_username=username,
                    discord_channel_id=channel_id,
                )
            except Exception as e:
                print(f"[DISCORD-CH] link upsert failed: {e}")

        session_data["chat_session_id"] = db_sid
        session_data["session_id"] = cuttle_session_key(db_sid)
        session_data["origin"] = origin
        return db_sid, created
    except Exception as e:
        print(f"[DISCORD-CH] resolve failed: {e}")
        return None, False


def persist_discord_user_turn(
    db_sid: int,
    content: str,
    user_context: Optional[Dict[str, Any]] = None,
) -> None:
    text = (content or "").strip()
    if db_sid is None or not text:
        return
    user_context = dict(user_context or {})
    meta = {
        "origin": "discord",
        "speaker": _display_name(user_context),
        "discord_user_id": str(user_context.get("id") or ""),
        "discord_channel_id": str(user_context.get("channel_id") or ""),
    }
    try:
        db = get_auth_db()
        db.add_message(int(db_sid), "user", text, metadata=meta)
    except Exception as e:
        print(f"[DISCORD-CH] persist user failed: {e}")


def persist_discord_assistant_turn(
    db_sid: int,
    result: Optional[Dict[str, Any]],
) -> None:
    if db_sid is None or not isinstance(result, dict):
        return
    text = (result.get("response") or result.get("output") or "").strip()
    if not text:
        return
    if text.startswith("[CANCELLED]"):
        return
    meta: Dict[str, Any] = {"origin": "discord"}
    if result.get("query_id"):
        meta["query_id"] = result.get("query_id")
    if result.get("report_url"):
        meta["report_url"] = result.get("report_url")
    cursor_run = result.get("cursor_run")
    if isinstance(cursor_run, dict) and cursor_run:
        meta["cursor_run"] = cursor_run
    try:
        db = get_auth_db()
        db.add_message(int(db_sid), "assistant", text, metadata=meta)
    except Exception as e:
        print(f"[DISCORD-CH] persist assistant failed: {e}")
    try:
        from api.chat_titler import schedule_session_autoname

        # name_auto=0 on create, so this is a no-op unless someone flips it.
        schedule_session_autoname(int(db_sid), "auto")
    except Exception:
        pass


def list_known_discord_people() -> List[Dict[str, Any]]:
    db = get_auth_db()
    owner_id = _hub_owner_user_id(db)
    if owner_id is None:
        return []
    return db.list_discord_identities(owner_id)


def _match_known(query: str) -> Optional[Dict[str, Any]]:
    q = (query or "").strip().lstrip("@")
    if not q:
        return None
    q_low = q.lower()
    for row in list_known_discord_people():
        uid = str(row.get("discord_user_id") or "")
        name = str(row.get("discord_username") or "")
        if uid == q or name.lower() == q_low:
            return row
        if name.lower().startswith(q_low) and len(q_low) >= 3:
            return row
    return None


def _discord_api(method: str, path: str, json_body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    import requests
    from api.project_actions import _load_discord_bot_token

    token = _load_discord_bot_token()
    if not token:
        return 0, {"message": "Discord bot token not found"}
    url = "https://discord.com/api/v10" + path
    try:
        r = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            },
            json=json_body,
            timeout=20,
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": (r.text or "")[:300]}
        return r.status_code, data
    except Exception as e:
        return 0, {"message": str(e)}


def lookup_discord_identity(query: str) -> Optional[Dict[str, Any]]:
    """Resolve a Discord @name / username / snowflake to {discord_user_id, discord_username}."""
    q = (query or "").strip().lstrip("@")
    if not q:
        return None
    known = _match_known(q)
    if known:
        return {
            "discord_user_id": str(known.get("discord_user_id") or ""),
            "discord_username": str(known.get("discord_username") or q),
            "discord_channel_id": str(known.get("discord_channel_id") or ""),
            "chat_session_id": known.get("chat_session_id"),
            "source": "known",
        }
    if q.isdigit() and len(q) >= 15:
        code, data = _discord_api("GET", f"/users/{q}")
        if code == 200 and isinstance(data, dict) and data.get("id"):
            uname = str(data.get("global_name") or data.get("username") or q)
            return {
                "discord_user_id": str(data["id"]),
                "discord_username": uname,
                "discord_channel_id": "",
                "source": "discord_user",
            }
    code, guilds = _discord_api("GET", "/users/@me/guilds")
    if code != 200 or not isinstance(guilds, list):
        return None
    for g in guilds[:12]:
        gid = str((g or {}).get("id") or "")
        if not gid:
            continue
        sc, members = _discord_api(
            "GET",
            f"/guilds/{gid}/members/search?query={requests_quote(q)}&limit=5",
        )
        if sc != 200 or not isinstance(members, list):
            continue
        for m in members:
            user = (m or {}).get("user") or {}
            uid = str(user.get("id") or "")
            uname = str(user.get("global_name") or user.get("username") or "")
            if not uid:
                continue
            if (
                uname.lower() == q.lower()
                or str(user.get("username") or "").lower() == q.lower()
                or str(m.get("nick") or "").lower() == q.lower()
            ):
                return {
                    "discord_user_id": uid,
                    "discord_username": uname or q,
                    "discord_channel_id": "",
                    "source": "guild_search",
                }
        if members:
            user = (members[0] or {}).get("user") or {}
            if user.get("id"):
                return {
                    "discord_user_id": str(user["id"]),
                    "discord_username": str(user.get("global_name") or user.get("username") or q),
                    "discord_channel_id": "",
                    "source": "guild_search",
                }
    return None


def requests_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s or "", safe="")


def dm_discord_user(discord_user_id: str, content: str) -> Dict[str, Any]:
    uid = str(discord_user_id or "").strip()
    text = (content or "").strip()
    if not uid or not text:
        return {"success": False, "error": "missing user or text"}
    code, ch = _discord_api("POST", "/users/@me/channels", {"recipient_id": uid})
    if code not in (200, 201) or not isinstance(ch, dict) or not ch.get("id"):
        err = (ch or {}).get("message") if isinstance(ch, dict) else str(ch)
        return {"success": False, "error": err or f"HTTP {code}"}
    channel_id = str(ch["id"])
    mc, posted = _discord_api(
        "POST",
        f"/channels/{channel_id}/messages",
        {"content": text[:1900]},
    )
    if mc not in (200, 201):
        err = (posted or {}).get("message") if isinstance(posted, dict) else str(posted)
        return {"success": False, "error": err or f"HTTP {mc}", "channel_id": channel_id}
    return {"success": True, "channel_id": channel_id}


def invite_discord_to_session(
    chat_session_id: int,
    query: str,
    *,
    notify: bool = True,
) -> Dict[str, Any]:
    """Bind a Discord identity to an existing CH session (same Cursor resume)."""
    ident = lookup_discord_identity(query)
    if not ident or not ident.get("discord_user_id"):
        known = list_known_discord_people()
        names = ", ".join(
            str(x.get("discord_username") or x.get("discord_user_id"))
            for x in known[:12]
        ) or "(none yet — they need to DM JamBit OS once, or pass a Discord user id)"
        return {
            "success": False,
            "error": (
                f"Couldn't resolve `{query}`. Known Discord chats: {names}."
            ),
        }
    db = get_auth_db()
    owner_id = _hub_owner_user_id(db)
    if owner_id is None:
        return {"success": False, "error": "No hub Cuttle user to attach this chat to."}
    sess = db.get_chat_session(int(chat_session_id), int(owner_id))
    if not sess:
        return {"success": False, "error": "Chat session not found."}

    uid = str(ident["discord_user_id"])
    uname = str(ident.get("discord_username") or query)
    db.upsert_discord_link(
        int(chat_session_id),
        discord_user_id=uid,
        discord_username=uname,
        discord_channel_id=str(ident.get("discord_channel_id") or ""),
    )
    dm = {"success": False}
    if notify:
        handle = f"CH-{int(chat_session_id):06d}"
        dm = dm_discord_user(
            uid,
            (
                f"You're linked into a Cuttle chat (**{handle}**) with JamBit OS. "
                f"Reply here and you'll be talking to the same agent that's already "
                f"working in that session."
            ),
        )
        if dm.get("channel_id"):
            try:
                db.upsert_discord_link(
                    int(chat_session_id),
                    discord_user_id=uid,
                    discord_username=uname,
                    discord_channel_id=str(dm["channel_id"]),
                )
            except Exception:
                pass
    return {
        "success": True,
        "discord_user_id": uid,
        "discord_username": uname,
        "chat_session_id": int(chat_session_id),
        "notified": bool(dm.get("success")),
        "notify_error": None if dm.get("success") else dm.get("error"),
    }


_INVITE_RE = re.compile(
    r"^(?:/cursor(?:-cli)?\s+)?/invite(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)


def parse_invite_slash(message: str) -> Optional[str]:
    """Return the invite query, or None if this is not `/invite`.

    Empty string means list known Discord people (no bind).
    Also matches sticky ``/cursor /invite …``.
    """
    m = _INVITE_RE.match((message or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def format_known_people_help() -> str:
    people = list_known_discord_people()
    if not people:
        return (
            "**Invite someone from Discord**\n\n"
            "No Discord chats yet. They can DM JamBit OS once (that opens a CH "
            "in History → Discord), or pass a Discord user id:\n"
            "`/invite 123456789012345678`\n\n"
            "Then `/invite <name>` in this chat binds their DMs to this same "
            "Cursor Agent session."
        )
    lines = ["**Discord people you can invite to this chat**", ""]
    for row in people[:20]:
        name = str(row.get("discord_username") or "").strip() or "(unknown)"
        uid = str(row.get("discord_user_id") or "")
        sid = row.get("chat_session_id")
        handle = f"CH-{int(sid):06d}" if sid else ""
        extra = f" — currently {handle}" if handle else ""
        lines.append(f"• **{name}** `{uid}`{extra}")
    lines.append("")
    lines.append("Type `/invite <name>` (or a Discord user id) to bind them here.")
    return "\n".join(lines)


def handle_invite_command(chat_session_id: int, query: str) -> Dict[str, Any]:
    """List known people or bind one Discord identity to this CH."""
    q = (query or "").strip()
    if not q:
        return {
            "success": True,
            "response": format_known_people_help(),
            "type": "invite",
            "invited": False,
        }
    result = invite_discord_to_session(int(chat_session_id), q, notify=True)
    if not result.get("success"):
        return {
            "success": True,
            "response": f"❌ {result.get('error') or 'Invite failed.'}",
            "type": "invite",
            "invited": False,
        }
    uname = str(result.get("discord_username") or q)
    handle = f"CH-{int(chat_session_id):06d}"
    dm_line = (
        "Discord DM: sent."
        if result.get("notified")
        else (
            "Couldn't DM them"
            + (f" ({result.get('notify_error')})" if result.get("notify_error") else "")
            + " — bind is still on. Next time they message JamBit OS, it continues this chat."
        )
    )
    return {
        "success": True,
        "response": (
            f"**Invited {uname}** to this chat (`{handle}`). "
            f"Their DMs to JamBit OS will talk to the same Cursor Agent that's "
            f"already working here.\n\n{dm_line}"
        ),
        "type": "invite",
        "invited": True,
        "discord_username": uname,
        "discord_user_id": result.get("discord_user_id"),
    }
