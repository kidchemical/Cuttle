"""Human-confirmed Discord channel posts (REST). Alias allowlist only."""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple


def resolve_post_channel_id(
    action: Dict[str, Any],
    params: Dict[str, Any],
) -> Tuple[str, str]:
    """Return ``(channel_id, display_key)``.

    ``display_key`` is the alias when one was used, otherwise the allowlisted snowflake.

    A numeric id is accepted **only** when it appears as a value in ``action["channels"]``.
    Arbitrary snowflakes cannot bypass the project allowlist.
    """
    channels = action.get("channels") or {}
    if not isinstance(channels, dict):
        channels = {}
    channel_key = str(params.get("channel") or params.get("channel_id") or "").strip()
    allowed_aliases = ", ".join(sorted(str(k) for k in channels.keys())) or "(none configured)"
    if not channel_key:
        raise ValueError(
            f"Unknown Discord channel `(missing)`. Allowlisted aliases: {allowed_aliases}"
        )
    if channel_key in channels:
        cid = str(channels[channel_key] or "").strip()
        if not cid:
            raise ValueError(
                f"Unknown Discord channel `{channel_key}`. Allowlisted aliases: {allowed_aliases}"
            )
        return cid, channel_key
    allowlisted_ids = {str(v).strip() for v in channels.values() if str(v).strip()}
    if channel_key.isdigit() and channel_key in allowlisted_ids:
        return channel_key, channel_key
    raise ValueError(
        f"Unknown Discord channel `{channel_key}`. Allowlisted aliases: {allowed_aliases}"
    )


def execute_discord_post(action: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    import requests

    from api.discord_ops.token import load_discord_bot_token

    content = str(params.get("content") or "").strip()
    if not content:
        return {"success": False, "error": "Nothing to post (empty content)."}

    try:
        channel_id, channel_key = resolve_post_channel_id(action, params)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    token = load_discord_bot_token()
    if not token:
        return {
            "success": False,
            "error": "Discord bot token not found (DISCORD_TOKEN / DISCORD_BOT_TOKEN).",
        }

    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "CuttleBot (agent-ops; discord.post)",
        "Content-Type": "application/json",
    }
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    last_err = ""
    for attempt in range(1, 4):
        try:
            r = requests.post(
                url,
                headers=headers,
                json={"content": content},
                timeout=30,
            )
            if r.ok:
                mid = (r.json() or {}).get("id")
                guild = action.get("guild_id") or ""
                msg_url = (
                    f"https://discord.com/channels/{guild}/{channel_id}/{mid}"
                    if guild and mid
                    else (f"channel {channel_id} message {mid}" if mid else "posted")
                )
                return {
                    "success": True,
                    "response": (
                        f"**Posted to Discord** (`{channel_key or channel_id}`)\n\n"
                        f"{msg_url}"
                    ),
                    "channel_id": channel_id,
                    "message_id": mid,
                    "url": msg_url if guild and mid else None,
                }
            if r.status_code == 429:
                try:
                    wait = float((r.json() or {}).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(wait)
                continue
            last_err = f"HTTP {r.status_code}: {(r.text or '')[:400]}"
            break
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)
    return {"success": False, "error": last_err or "Discord post failed"}
