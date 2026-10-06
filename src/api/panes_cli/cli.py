"""CLI: ``python -m api.panes_cli <verb> …``

Read the Host app-shell split panes via Flask (self-signed HTTPS on 8080).
Replaces ad-hoc ``curl -k`` against ``/api/shell/panes``.

Examples::

    .venv\\Scripts\\python.exe -m api.panes_cli list --json
    .venv\\Scripts\\python.exe -m api.panes_cli messages 1 --limit 40 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


def _default_base() -> str:
    """Configured primary-HTTPS base. Fails closed on malformed port config."""
    try:
        from api.server_ports import resolve_with_env_file

        return f"https://127.0.0.1:{resolve_with_env_file().https}"
    except Exception as exc:
        raise SystemExit(f"error: invalid listener-port configuration: {exc}")


DEFAULT_BASE = "https://127.0.0.1:8080"  # default snapshot; live value is _default_base()


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


def _get_json(url: str, *, timeout: float = 15.0) -> tuple[int, Any, str]:
    import requests

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            r = requests.get(url, verify=False, timeout=timeout)
        except Exception as e:
            return 0, None, str(e)
    try:
        data = r.json() if r.content else None
    except Exception:
        data = None
    if not r.ok:
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error") or "")
        return r.status_code, data, err or f"HTTP {r.status_code}"
    return r.status_code, data, ""


def cmd_list(args: argparse.Namespace) -> int:
    base = (args.base_url or _default_base()).rstrip("/")
    code, data, err = _get_json(f"{base}/api/shell/panes")
    if err or not isinstance(data, dict) or not data.get("success", True):
        # GET returns success key sometimes; tolerate panes without it.
        if not isinstance(data, dict) or "panes" not in (data or {}):
            _emit(
                {"ok": False, "error": "api", "detail": err or "bad response", "status": code},
                as_json=args.json,
                text=f"error: {err or 'could not reach panes API — is Flask up?'}\n",
            )
            return 3
    panes = data.get("panes") or []
    payload = {
        "ok": True,
        "orientation": data.get("orientation"),
        "count": len(panes),
        "panes": panes,
        "updated_at": data.get("updated_at"),
    }
    lines = [f"{len(panes)} pane(s) ({payload['orientation'] or '—'})"]
    for p in panes:
        lines.append(
            f"  {p.get('pane')}: {p.get('kind') or 'chat'}  "
            f"session={p.get('session_id') or '—'}  {p.get('title') or p.get('page') or ''}"
        )
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return 0


def cmd_messages(args: argparse.Namespace) -> int:
    base = (args.base_url or _default_base()).rstrip("/")
    n = int(args.pane)
    limit = max(1, min(200, int(args.limit)))
    code, data, err = _get_json(f"{base}/api/shell/panes/{n}/messages?limit={limit}")
    if err or not isinstance(data, dict):
        _emit(
            {"ok": False, "error": "api", "detail": err or "bad response", "status": code},
            as_json=args.json,
            text=f"error: {err or 'could not reach panes API'}\n",
        )
        return 3 if code != 404 else 2
    if not data.get("success", True) and data.get("error"):
        _emit(
            {"ok": False, "error": "not_found", "detail": data.get("error"), "panes": data.get("panes")},
            as_json=args.json,
            text=f"error: {data.get('error')}\n",
        )
        return 2
    messages = data.get("messages") or []
    payload = {
        "ok": True,
        "pane": data.get("pane"),
        "count": data.get("count", len(messages)),
        "note": data.get("note"),
        "messages": messages,
    }
    pane = data.get("pane") or {}
    lines = [
        f"pane {n}: session={pane.get('session_id') or '—'}  "
        f"{pane.get('title') or ''}  ({payload['count']} msgs)"
    ]
    if payload.get("note"):
        lines.append(f"  note: {payload['note']}")
    for m in messages:
        role = m.get("role") or "?"
        content = (m.get("content") or "").replace("\n", " ")
        if len(content) > 140:
            content = content[:139] + "…"
        lines.append(f"  [{role}] {content}")
    _emit(payload, as_json=args.json, text="\n".join(lines) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument(
        "--base-url",
        default=None,
        help=f"Flask base URL (default {DEFAULT_BASE}, or CUTTLE_HTTPS_PORT)",
    )

    p = argparse.ArgumentParser(
        prog="python -m api.panes_cli",
        description="Read Cuttle app-shell split panes (live Host UI state).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list", parents=[common], help="List open panes (left→right)")
    lst.set_defaults(func=cmd_list)

    msg = sub.add_parser("messages", parents=[common], help="Messages in pane N (1 = leftmost)")
    msg.add_argument("pane", type=int, help="1-based pane index")
    msg.add_argument("--limit", type=int, default=40)
    msg.set_defaults(func=cmd_messages)

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
