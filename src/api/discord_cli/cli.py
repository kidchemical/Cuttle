"""CLI: ``python -m api.discord_cli <verb> …``

Read Discord channels / history via the bot token in ``<home>/.env``.
Outbound posts stay on ``discord.post`` action forms — this CLI is **read-only**.

Examples::

    .venv\\Scripts\\python.exe -m api.discord_cli aliases --project "C:\\Projects\\DemoGame" --json
    .venv\\Scripts\\python.exe -m api.discord_cli channels --project "C:\\Projects\\DemoGame" --json
    .venv\\Scripts\\python.exe -m api.discord_cli messages feature-updates --project "…" --limit 20 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _ensure_src_on_path() -> None:
    src = Path(__file__).resolve().parents[2]
    src_s = str(src)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)


def _emit(payload: Dict[str, Any], *, as_json: bool, text: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _discord_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any, str]:
    import requests
    from api.discord_ops.token import load_discord_bot_token

    token = load_discord_bot_token()
    if not token:
        from core.runtime_paths import env_file

        return 0, None, f"Discord bot token not found (DISCORD_TOKEN / DISCORD_BOT_TOKEN in {env_file()})"
    url = "https://discord.com/api/v10" + path
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "CuttleBot (agent-ops; discord_cli)",
        "Content-Type": "application/json",
    }
    last_err = ""
    for attempt in range(1, 4):
        try:
            r = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=30,
            )
            try:
                data = r.json() if r.content else None
            except Exception:
                data = {"text": (r.text or "")[:400]}
            if r.status_code == 429:
                try:
                    wait = float((data or {}).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(wait)
                continue
            if r.ok:
                return r.status_code, data, ""
            return r.status_code, data, f"HTTP {r.status_code}: {(r.text or '')[:400]}"
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)
    return 0, None, last_err or "Discord request failed"


def _load_discord_action(project: Optional[str]) -> Optional[Dict[str, Any]]:
    if not project:
        return None
    from api.project_actions import find_project_action

    return find_project_action(str(project), "discord.post")


def _resolve_channel_id(
    raw: str,
    *,
    project: Optional[str],
) -> Tuple[str, Optional[str]]:
    """Return (channel_id, alias_or_None).

    With ``--project``, aliases and allowlisted snowflakes (yaml values) only.
    Without ``--project``, a raw snowflake is allowed (read-only CLI; bot token
    still required). Posts never use this helper — they go through discord.post.
    """
    key = str(raw or "").strip()
    if not key:
        raise SystemExit("error: channel id or alias required")
    action = _load_discord_action(project) if project else None
    channels = (action or {}).get("channels") or {}
    if key in channels:
        return str(channels[key]), key
    if key.isdigit():
        if project:
            allowlisted_ids = {str(v).strip() for v in channels.values() if str(v).strip()}
            if key in allowlisted_ids:
                return key, None
            allowed = ", ".join(sorted(channels.keys())) or "(none)"
            raise SystemExit(
                f"error: channel id {key!r} is not allowlisted for this project. "
                f"Aliases: {allowed}"
            )
        return key, None
    allowed = ", ".join(sorted(channels.keys())) or "(none — pass --project with discord-post.yaml)"
    raise SystemExit(
        f"error: unknown channel alias {key!r}. Allowlisted: {allowed}"
    )


def cmd_aliases(args: argparse.Namespace) -> int:
    action = _load_discord_action(args.project)
    if not action:
        _emit(
            {
                "ok": False,
                "error": "no_action",
                "detail": "Pass --project pointing at a folder with .cuttle/actions/discord-post.yaml",
            },
            as_json=args.json,
            text="error: no discord.post action for that project\n",
        )
        return 2
    channels = action.get("channels") or {}
    payload = {
        "ok": True,
        "project_path": action.get("project_path"),
        "guild_id": action.get("guild_id"),
        "channels": channels,
    }
    lines = [f"guild={payload['guild_id'] or '—'}"]
    for alias, cid in sorted(channels.items()):
        lines.append(f"  {alias}: {cid}")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    guild_id = (args.guild or "").strip()
    if not guild_id:
        action = _load_discord_action(args.project)
        guild_id = str((action or {}).get("guild_id") or "").strip()
    if not guild_id:
        msg = "error: pass --guild <id> or --project with guild_id in discord-post.yaml"
        if args.json:
            _emit({"ok": False, "error": "bad_args", "detail": msg}, as_json=True, text="")
        else:
            sys.stderr.write(msg + "\n")
        return 2
    code, data, err = _discord_request("GET", f"/guilds/{guild_id}/channels")
    if err or not isinstance(data, list):
        _emit(
            {"ok": False, "error": "discord_api", "detail": err or "bad response", "status": code},
            as_json=args.json,
            text=f"error: {err or 'bad response'}\n",
        )
        return 3
    # Text channels (type 0) + announcements (5) first; keep ids for agent use.
    rows = []
    for ch in data:
        if not isinstance(ch, dict):
            continue
        rows.append(
            {
                "id": ch.get("id"),
                "name": ch.get("name"),
                "type": ch.get("type"),
                "parent_id": ch.get("parent_id"),
                "position": ch.get("position"),
            }
        )
    rows.sort(key=lambda r: (r.get("position") is None, r.get("position") or 0, r.get("name") or ""))
    payload = {"ok": True, "guild_id": guild_id, "count": len(rows), "channels": rows}
    lines = [f"{len(rows)} channel(s) in guild {guild_id}"]
    for r in rows:
        lines.append(f"  {r.get('name')}: {r.get('id')}  (type={r.get('type')})")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return 0


def cmd_messages(args: argparse.Namespace) -> int:
    try:
        channel_id, alias = _resolve_channel_id(args.channel, project=args.project)
    except SystemExit as e:
        msg = str(e)
        if args.json:
            _emit({"ok": False, "error": "bad_args", "detail": msg}, as_json=True, text="")
        else:
            sys.stderr.write(msg + "\n")
        return 2
    limit = max(1, min(100, int(args.limit)))
    params: Dict[str, Any] = {"limit": limit}
    if args.before:
        params["before"] = str(args.before)
    if args.after:
        params["after"] = str(args.after)
    code, data, err = _discord_request(
        "GET", f"/channels/{channel_id}/messages", params=params
    )
    if err or not isinstance(data, list):
        _emit(
            {"ok": False, "error": "discord_api", "detail": err or "bad response", "status": code},
            as_json=args.json,
            text=f"error: {err or 'bad response'}\n",
        )
        return 3
    messages = []
    for m in data:
        if not isinstance(m, dict):
            continue
        author = m.get("author") or {}
        content = m.get("content") or ""
        if args.max_chars and len(content) > int(args.max_chars):
            content = content[: int(args.max_chars) - 1] + "…"
        messages.append(
            {
                "id": m.get("id"),
                "timestamp": m.get("timestamp"),
                "author": author.get("global_name") or author.get("username") or author.get("id"),
                "author_id": author.get("id"),
                "content": content,
            }
        )
    # Discord returns newest-first; keep that order unless --oldest-first.
    if args.oldest_first:
        messages = list(reversed(messages))
    payload = {
        "ok": True,
        "channel_id": channel_id,
        "alias": alias,
        "count": len(messages),
        "messages": messages,
    }
    lines = [f"{len(messages)} message(s) in {alias or channel_id}"]
    for m in messages:
        preview = (m.get("content") or "").replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:119] + "…"
        lines.append(f"  [{m.get('author')}] {preview}")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument(
        "--project",
        default=None,
        help="Project root with .cuttle/actions/discord-post.yaml (aliases + guild_id)",
    )

    p = argparse.ArgumentParser(
        prog="python -m api.discord_cli",
        description="Discord read helpers for Cuttle agents (posts stay on discord.post forms).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("aliases", parents=[common], help="List channel aliases from discord-post.yaml")
    a.set_defaults(func=cmd_aliases)

    c = sub.add_parser("channels", parents=[common], help="List guild channels")
    c.add_argument("--guild", default=None, help="Guild snowflake (defaults from --project)")
    c.set_defaults(func=cmd_channels)

    m = sub.add_parser("messages", parents=[common], help="Fetch recent channel messages")
    m.add_argument("channel", help="Alias from discord-post.yaml or channel snowflake")
    m.add_argument("--limit", type=int, default=20)
    m.add_argument("--before", default=None, help="Message id cursor")
    m.add_argument("--after", default=None, help="Message id cursor")
    m.add_argument("--max-chars", type=int, default=2000)
    m.add_argument("--oldest-first", action="store_true")
    m.set_defaults(func=cmd_messages)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return 0
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception as e:
        sys.stderr.write(f"error: {e}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
