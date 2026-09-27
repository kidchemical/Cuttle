"""CLI: ``python -m api.chat_cli <verb> …``

Agent-facing verbs over the chat transcript store. Canonical library stays
``api.cuttle_ui_capabilities.resolve_chat_handle_message`` /
``AuthDatabase.get_message_by_share_index`` — this module only formats and
wires argparse.

Examples (from Cuttle repo, venv Python)::

    .venv\\Scripts\\python.exe -m api.chat_cli get CH-000430-99
    .venv\\Scripts\\python.exe -m api.chat_cli get CH-000430-99 --json
    .venv\\Scripts\\python.exe -m api.chat_cli session CH-000430 --limit 20
    .venv\\Scripts\\python.exe -m api.chat_cli search "flask restart" --from-chat CH-000465
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Paths / DB
# ---------------------------------------------------------------------------


def _ensure_src_on_path() -> None:
    """Allow ``python -m api.chat_cli`` when cwd is the repo root."""
    # __file__ = src/api/chat_cli/cli.py → parents[2] = src
    src = Path(__file__).resolve().parents[2]
    src_s = str(src)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)


def _open_db(db_path: Optional[str] = None):
    from api.auth_db import AuthDatabase, get_auth_db

    if db_path:
        return AuthDatabase(Path(db_path))
    return get_auth_db()

def _format_handle(session_id: int, share_index: Optional[int] = None) -> str:
    base = f"CH-{int(session_id):06d}"
    if share_index is None:
        return base
    return f"{base}-{int(share_index)}"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    return str(value)


def _clip(text: str, max_chars: Optional[int]) -> str:
    s = text if isinstance(text, str) else str(text or "")
    if max_chars is None or max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[: max(0, max_chars - 1)] + "…"


def _message_payload(
    msg: Dict[str, Any],
    *,
    session_id: int,
    share_index: Optional[int],
    max_chars: Optional[int],
) -> Dict[str, Any]:
    content = msg.get("content") or ""
    out: Dict[str, Any] = {
        "id": msg.get("id"),
        "session_id": int(session_id),
        "share_index": share_index,
        "handle": _format_handle(session_id, share_index) if share_index else _format_handle(session_id),
        "role": msg.get("role"),
        "timestamp": msg.get("timestamp"),
        "content": _clip(str(content), max_chars),
    }
    if max_chars and isinstance(content, str) and len(content) > max_chars:
        out["content_truncated"] = True
        out["content_chars"] = len(content)
    meta = msg.get("metadata")
    if meta is not None:
        out["metadata"] = _json_ready(meta)
    return out


def _session_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    sid = int(row["id"])
    return {
        "id": sid,
        "handle": _format_handle(sid),
        "session_name": row.get("session_name") or "",
        "user_id": row.get("user_id"),
        "project_id": row.get("project_id"),
        "project_name": row.get("project_name") or "",
        "project_path": row.get("project_path") or "",
        "last_activity": row.get("last_activity"),
        "is_active": row.get("is_active"),
        "starred": row.get("starred"),
        "discord_username": row.get("discord_username") or "",
    }


def _emit_text_once(payload: Dict[str, Any], *, as_json: bool, text_fn) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    text = text_fn(payload)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


# ---------------------------------------------------------------------------
# User resolution (search)
# ---------------------------------------------------------------------------


def _list_user_ids(db) -> List[int]:
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC")
        return [int(r["id"] if hasattr(r, "keys") else r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def _resolve_search_user_id(
    db,
    *,
    user_id: Optional[int],
    from_chat: Optional[str],
) -> int:
    if user_id is not None:
        return int(user_id)
    if from_chat:
        from api.cuttle_ui_capabilities import parse_chat_handle

        parsed = parse_chat_handle(from_chat)
        if not parsed:
            raise SystemExit(f"error: invalid --from-chat handle {from_chat!r}")
        row = db.get_chat_session_by_id(parsed["session_id"])
        if not row:
            raise SystemExit(f"error: session {from_chat} not found")
        return int(row["user_id"])
    ids = _list_user_ids(db)
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise SystemExit("error: no users in auth DB")
    raise SystemExit(
        "error: multiple users — pass --from-chat CH-000xxx or --user-id N"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_parse(args: argparse.Namespace) -> int:
    from api.cuttle_ui_capabilities import parse_chat_handle

    parsed = parse_chat_handle(args.handle)
    if not parsed:
        payload = {"ok": False, "error": "invalid_handle", "handle": args.handle}
        _emit_text_once(
            payload,
            as_json=args.json,
            text_fn=lambda p: f"invalid handle: {args.handle}\n",
        )
        return 2
    payload = {
        "ok": True,
        "handle": args.handle,
        "session_id": parsed["session_id"],
        "message_index": parsed.get("message_index"),
        "display": _format_handle(
            parsed["session_id"], parsed.get("message_index")
        ),
    }
    _emit_text_once(
        payload,
        as_json=args.json,
        text_fn=lambda p: (
            f"{p['display']}: session={p['session_id']}"
            + (
                f" share_index={p['message_index']}\n"
                if p.get("message_index")
                else " (session only)\n"
            )
        ),
    )
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    from api.cuttle_ui_capabilities import parse_chat_handle, resolve_chat_handle_message

    parsed = parse_chat_handle(args.handle)
    if not parsed:
        _emit_text_once(
            {"ok": False, "error": "invalid_handle", "handle": args.handle},
            as_json=args.json,
            text_fn=lambda p: f"invalid handle: {args.handle}\n",
        )
        return 2

    db = _open_db(args.db)
    sid = int(parsed["session_id"])
    max_chars = None if args.full else int(args.max_chars)

    if parsed.get("message_index") is not None:
        msg = resolve_chat_handle_message(args.handle, db=db)
        if msg is None:
            _emit_text_once(
                {
                    "ok": False,
                    "error": "not_found",
                    "handle": _format_handle(sid, parsed["message_index"]),
                    "session_id": sid,
                    "share_index": parsed["message_index"],
                },
                as_json=args.json,
                text_fn=lambda p: (
                    f"not found: {p['handle']} "
                    f"(no user/assistant bubble at share index {p['share_index']})\n"
                ),
            )
            return 2
        body = _message_payload(
            msg,
            session_id=sid,
            share_index=int(parsed["message_index"]),
            max_chars=max_chars,
        )
        payload = {"ok": True, **body}
        _emit_text_once(
            payload,
            as_json=args.json,
            text_fn=lambda p: _format_message_text(p),
        )
        return 0

    # Session-only handle
    row = db.get_chat_session_by_id(sid)
    if not row:
        _emit_text_once(
            {"ok": False, "error": "not_found", "handle": _format_handle(sid), "session_id": sid},
            as_json=args.json,
            text_fn=lambda p: f"not found: {p['handle']}\n",
        )
        return 2
    session = _session_payload(dict(row))
    # Quick counts (visible vs all)
    messages = db.get_messages(sid)
    visible = [m for m in messages if m.get("role") in ("user", "assistant")]
    payload = {
        "ok": True,
        "handle": session["handle"],
        "session_id": sid,
        "session": session,
        "message_count": len(messages),
        "share_count": len(visible),
        "hint": (
            f"Bubble ref: {_format_handle(sid)}-N  |  "
            f"List: python -m api.chat_cli session {_format_handle(sid)}"
        ),
    }
    _emit_text_once(
        payload,
        as_json=args.json,
        text_fn=lambda p: (
            f"{p['handle']}  {p['session'].get('session_name') or '(untitled)'}\n"
            f"  project: {p['session'].get('project_name') or '—'} "
            f"({p['session'].get('project_path') or '—'})\n"
            f"  messages: {p['message_count']} total, {p['share_count']} share-indexed "
            f"(user+assistant)\n"
            f"  {p['hint']}\n"
        ),
    )
    return 0


def _format_message_text(p: Dict[str, Any]) -> str:
    handle = p.get("handle") or ""
    role = p.get("role") or "?"
    ts = p.get("timestamp") or ""
    content = p.get("content") or ""
    lines = [f"{handle}  [{role}]  id={p.get('id')}  ts={ts}"]
    if p.get("content_truncated"):
        lines.append(f"(truncated; {p.get('content_chars')} chars — pass --full)")
    lines.append(content)
    lines.append("")
    return "\n".join(lines)


def cmd_session(args: argparse.Namespace) -> int:
    from api.cuttle_ui_capabilities import parse_chat_handle

    parsed = parse_chat_handle(args.handle)
    if not parsed:
        _emit_text_once(
            {"ok": False, "error": "invalid_handle", "handle": args.handle},
            as_json=args.json,
            text_fn=lambda p: f"invalid handle: {args.handle}\n",
        )
        return 2

    db = _open_db(args.db)
    sid = int(parsed["session_id"])
    row = db.get_chat_session_by_id(sid)
    if not row:
        _emit_text_once(
            {"ok": False, "error": "not_found", "handle": _format_handle(sid)},
            as_json=args.json,
            text_fn=lambda p: f"not found: {p['handle']}\n",
        )
        return 2

    max_chars = None if args.full else int(args.max_chars)
    include_system = bool(args.include_system)

    # Optional id window (same semantics as AuthDatabase.get_messages)
    limit = None if args.all else int(args.limit)
    messages = db.get_messages(
        sid,
        limit=limit,
        before_id=args.before_id,
        after_id=args.after_id,
    )

    # Absolute share indices (UI contract): offset by older visible rows when
    # we only loaded a window (newest N / before_id / after_id).
    share_base = 0
    if messages:
        first_id = messages[0].get("id")
        if first_id is not None:
            share_base = int(
                db.message_page_meta(sid, int(first_id)).get("older_visible_count") or 0
            )

    items: List[Dict[str, Any]] = []
    visible_i = share_base
    for msg in messages:
        role = msg.get("role")
        share_index = None
        if role in ("user", "assistant"):
            visible_i += 1
            share_index = visible_i
        elif not include_system:
            continue
        items.append(
            _message_payload(
                msg,
                session_id=sid,
                share_index=share_index,
                max_chars=max_chars,
            )
        )

    payload = {
        "ok": True,
        "handle": _format_handle(sid),
        "session_id": sid,
        "session": _session_payload(dict(row)),
        "count": len(items),
        "include_system": include_system,
        "messages": items,
    }

    def _text(p: Dict[str, Any]) -> str:
        head = (
            f"{p['handle']}  {p['session'].get('session_name') or '(untitled)'}  "
            f"({p['count']} shown)\n"
        )
        blocks = []
        for m in p["messages"]:
            label = m["handle"] if m.get("share_index") else f"{p['handle']} [system]"
            blocks.append(
                f"— {label} [{m.get('role')}] id={m.get('id')}\n{m.get('content') or ''}\n"
            )
        return head + "\n".join(blocks)

    _emit_text_once(payload, as_json=args.json, text_fn=_text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    db = _open_db(args.db)
    try:
        uid = _resolve_search_user_id(
            db, user_id=args.user_id, from_chat=args.from_chat
        )
    except SystemExit as e:
        msg = str(e)
        if args.json:
            sys.stdout.write(
                json.dumps({"ok": False, "error": "user_required", "detail": msg}, indent=2)
                + "\n"
            )
        else:
            sys.stderr.write(msg + "\n")
        return 2

    hits = db.search_user_chats(uid, args.query, limit=int(args.limit))
    results = []
    for row in hits:
        entry = _session_payload(dict(row))
        entry["match"] = row.get("match")
        entry["snippet"] = row.get("snippet") or ""
        if row.get("message_count") is not None:
            entry["message_count"] = row.get("message_count")
        results.append(entry)

    payload = {
        "ok": True,
        "query": args.query,
        "user_id": uid,
        "count": len(results),
        "results": results,
    }

    def _text(p: Dict[str, Any]) -> str:
        if not p["results"]:
            return f"No chats matching {p['query']!r}\n"
        lines = [f"{p['count']} hit(s) for {p['query']!r} (user_id={p['user_id']})"]
        for r in p["results"]:
            snip = (r.get("snippet") or "").replace("\n", " ")
            extra = f"  … {snip}" if snip else ""
            lines.append(
                f"  {r['handle']}  [{r.get('match')}]  "
                f"{r.get('session_name') or '(untitled)'}{extra}"
            )
        return "\n".join(lines) + "\n"

    _emit_text_once(payload, as_json=args.json, text_fn=_text)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=None,
        help="Override path to cuttle_auth.db (default: runtime AuthDatabase path)",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON (recommended for agents)",
    )

    p = argparse.ArgumentParser(
        prog="python -m api.chat_cli",
        description=(
            "Cuttle chat history — agent ops CLI. "
            "Prefer this over hand-rolled SQL against cuttle_auth.db. "
            "Put flags after the verb: … get CH-000182-3 --json"
        ),
    )

    sub = p.add_subparsers(dest="command", required=True)

    parse_p = sub.add_parser(
        "parse",
        parents=[common],
        help="Parse a CH- handle without touching the DB",
    )
    parse_p.add_argument("handle", help="CH-000182 or CH-000182-23")
    parse_p.set_defaults(func=cmd_parse)

    get_p = sub.add_parser(
        "get",
        parents=[common],
        help="Resolve a CH- handle (session summary or one bubble by share index)",
    )
    get_p.add_argument("handle", help="CH-000182 or CH-000182-23")
    get_p.add_argument(
        "--max-chars",
        type=int,
        default=2000,
        help="Truncate message content (default 2000; ignored with --full)",
    )
    get_p.add_argument(
        "--full",
        action="store_true",
        help="Do not truncate message content",
    )
    get_p.set_defaults(func=cmd_get)

    sess_p = sub.add_parser(
        "session",
        parents=[common],
        help="List bubbles in a chat (share indices match the UI)",
    )
    sess_p.add_argument("handle", help="CH-000182 or numeric session id")
    sess_p.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Newest N messages (default 40). Ignored with --all.",
    )
    sess_p.add_argument(
        "--all",
        action="store_true",
        help="Full transcript (can be large)",
    )
    sess_p.add_argument(
        "--before-id",
        type=int,
        default=None,
        dest="before_id",
        help="Page older than this chat_messages.id",
    )
    sess_p.add_argument(
        "--after-id",
        type=int,
        default=None,
        dest="after_id",
        help="Messages newer than this chat_messages.id",
    )
    sess_p.add_argument(
        "--include-system",
        action="store_true",
        help="Include system rows (Stop notices, etc.). They have no share index.",
    )
    sess_p.add_argument("--max-chars", type=int, default=500)
    sess_p.add_argument("--full", action="store_true")
    sess_p.set_defaults(func=cmd_session)

    search_p = sub.add_parser(
        "search",
        parents=[common],
        help="Search chat titles and message bodies",
    )
    search_p.add_argument("query", help="Text or CH-000182 handle")
    search_p.add_argument("--limit", type=int, default=20)
    search_p.add_argument(
        "--from-chat",
        default=None,
        help="CH- handle whose owner scopes the search (preferred on multi-user DBs)",
    )
    search_p.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Auth user id (alternative to --from-chat)",
    )
    search_p.set_defaults(func=cmd_search)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    # Re-bind cwd-friendly imports after path fix when launched oddly.
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    # Global flags live on the parent; subcommands inherit via namespace.
    try:
        return int(args.func(args))
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except Exception as e:
        sys.stderr.write(f"error: {e}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
