"""``python -m api.media_library list`` — media shared in a user's chats.

    python -m api.media_library list [--chat CH-000182] [--kind image|video]
        [--available] [--limit 50] [--json] [--user-id N]

Default user is the install's first account (the single-user owner).
"""

from __future__ import annotations

import argparse
import json
import re
import sys


def _chat_id(raw: str):
    if not raw:
        return None
    m = re.match(r"^(?:CH-)?0*(\d+)(?:-\d+)?$", raw.strip(), re.I)
    if not m:
        raise SystemExit(f"not a chat handle: {raw}")
    return int(m.group(1))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.media_library")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="List images/videos shared in chats, newest first")
    ls.add_argument("--chat", default="", help="Only this chat (CH-… handle or id)")
    ls.add_argument("--kind", choices=("image", "video"))
    ls.add_argument("--available", action="store_true", help="Skip expired / missing files")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--user-id", type=int)
    ls.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from api.auth_db import get_auth_db
    from api.media_library import service

    db = get_auth_db()
    uid = args.user_id or db.first_account_id()
    data = service.list_media(int(uid), db=db, chat_id=_chat_id(args.chat))
    items = [it for it in data["items"]
             if (not args.kind or it["kind"] == args.kind)
             and (not args.available or it["available"] is not False)]
    items = items[: max(0, args.limit)] if args.limit else items
    if args.json:
        print(json.dumps({"items": items, "totals": data["totals"]}, indent=2))
        return 0
    for it in items:
        flag = "" if it["available"] is not False else "  [expired]"
        print(f"{it['shared_at'][:16].replace('T', ' ')}  {it['kind']:5}  {it['handle']:<16} {it['name']}{flag}\n"
              f"    {it['url']}")
    print(f"{len(items)} shown · {data['totals']['items']} total", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
