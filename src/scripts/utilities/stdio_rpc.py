"""JSON-RPC over a harness server's stdio (``codex app-server``, ``muse serve``).

Driven from ``run_interruptible``'s stdout line callback on the event loop, so
requests and replies stay on one thread. ``request_threadsafe`` is the only
entry point for other threads (the steer endpoint runs on a Flask worker).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any, Callable, Dict, Optional

ReplyHandler = Callable[[Optional[Dict[str, Any]], Optional[Dict[str, Any]]], None]


def rpc_error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error or "unknown error")


class StdioRpc:
    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        *,
        loop: asyncio.AbstractEventLoop,
        jsonrpc_tag: bool = False,
    ) -> None:
        self.proc = proc
        self.loop = loop
        self.jsonrpc_tag = jsonrpc_tag
        self._ids = itertools.count(1)
        self._pending: Dict[int, ReplyHandler] = {}
        self._closed = False

    def _write(self, msg: Dict[str, Any]) -> None:
        if self.jsonrpc_tag:
            msg = {"jsonrpc": "2.0", **msg}
        stdin = self.proc.stdin
        if self._closed or stdin is None or stdin.is_closing():
            raise RuntimeError("server stdin is closed")
        stdin.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))

    def request(
        self,
        method: str,
        params: Dict[str, Any],
        on_reply: Optional[ReplyHandler] = None,
    ) -> int:
        mid = next(self._ids)
        if on_reply is not None:
            self._pending[mid] = on_reply
        try:
            self._write({"id": mid, "method": method, "params": params})
        except Exception:
            self._pending.pop(mid, None)
            raise
        return mid

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def respond(
        self,
        msg_id: Any,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {"id": msg_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result if result is not None else {}
        try:
            self._write(payload)
        except Exception:
            pass

    def dispatch_reply(self, msg: Dict[str, Any]) -> bool:
        """Route a response to its handler. False for notifications / server requests."""
        if "method" in msg or "id" not in msg:
            return False
        if "result" not in msg and "error" not in msg:
            return False
        try:
            mid = int(msg.get("id"))
        except (TypeError, ValueError):
            return True
        handler = self._pending.pop(mid, None)
        if handler is not None:
            err = msg.get("error")
            res = msg.get("result")
            handler(
                res if isinstance(res, dict) else ({} if err is None else None),
                err if err is not None else None,
            )
        return True

    def request_threadsafe(
        self,
        method: str,
        params_factory: Callable[[], Dict[str, Any]],
        on_reply: ReplyHandler,
    ) -> None:
        """Send from a non-loop thread; ``on_reply`` runs on the loop."""

        def _go() -> None:
            try:
                self.request(method, params_factory(), on_reply)
            except Exception as exc:
                on_reply(None, {"message": str(exc)})

        try:
            self.loop.call_soon_threadsafe(_go)
        except RuntimeError as exc:
            on_reply(None, {"message": f"event loop closed: {exc}"})

    def close_stdin(self) -> None:
        if self._closed:
            return
        self._closed = True
        stdin = self.proc.stdin
        if stdin is not None and not stdin.is_closing():
            try:
                stdin.close()
            except Exception:
                pass
        for handler in list(self._pending.values()):
            try:
                handler(None, {"message": "server connection closed"})
            except Exception:
                pass
        self._pending.clear()
