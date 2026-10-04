"""Muse Code turns on ``muse serve`` (MSP over stdio) so follow-ups can steer.

Same result dict as ``MuseCliTool.execute_prompt`` (``muse exec --json``),
plus ``steered``. Returns ``{"fallback": True, …}`` when the host could not
start a turn (WSL-only install, resume id unknown to this host, spawn error),
so the adapter reruns the prompt on ``exec``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    kill_process_tree,
    run_interruptible,
)
from scripts.utilities.muse_cli_tool import (
    _STDOUT_LINE_LIMIT,
    _default_timeout,
    _first_line,
    _which_muse_native,
)
from scripts.utilities.stdio_rpc import StdioRpc, rpc_error_text

if TYPE_CHECKING:
    import queue as queue_module

_REAP_GRACE_SEC = 15.0
_ARG_KEYS = ("command", "path", "file_path", "pattern", "query", "url", "description")


def command_id() -> str:
    """UUIDv7 — MSP rejects any other idempotency handle."""
    ms = int(time.time() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _tool_label(item: Dict[str, Any]) -> str:
    name = str(item.get("tool") or item.get("toolName") or "tool").strip()
    detail = ""
    try:
        args = json.loads(item.get("args") or "{}")
    except (TypeError, ValueError):
        args = {}
    if isinstance(args, dict):
        for key in _ARG_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                detail = _first_line(val, 90)
                break
    return f"{name} {detail}".strip()


def _reasoning_preview(item: Dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, list):
        text = " ".join(str(s) for s in summary if isinstance(s, str))
    else:
        text = str(item.get("text") or "")
    return text.replace("\n", " ").strip()[:140]


def _usage_from_turn(usage: Any, prompt_tokens: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(usage, dict):
        for src, dst in (
            ("inputTokens", "input_tokens"),
            ("input_tokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("output_tokens", "output_tokens"),
            ("cachedTokens", "cached_tokens"),
            ("cacheReadTokens", "cache_read_tokens"),
            ("cacheWriteTokens", "cache_write_tokens"),
            ("reasoningTokens", "reasoning_tokens"),
        ):
            try:
                val = int(usage.get(src) or 0)
            except (TypeError, ValueError):
                val = 0
            if val:
                out[dst] = val
        cost = usage.get("costUsd")
        if cost is None:
            cost = usage.get("cost")
        if cost is None:
            micros = usage.get("costMicros")
            if micros is not None:
                try:
                    cost = int(micros) / 1_000_000.0
                except (TypeError, ValueError):
                    cost = None
        if cost is not None:
            try:
                out["cost"] = float(cost)
            except (TypeError, ValueError):
                pass
        if out.get("input_tokens") or out.get("output_tokens"):
            out["total_tokens"] = int(out.get("input_tokens") or 0) + int(out.get("output_tokens") or 0)
    if prompt_tokens:
        out["context_tokens"] = prompt_tokens
    return out


def _fallback(error: str) -> Dict[str, Any]:
    return {"success": False, "fallback": True, "error": error, "output": ""}


async def run_muse_turn_serve(
    prompt: str,
    *,
    cwd: Optional[str],
    resume: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    provider: Optional[str] = None,
    yolo: bool = True,
    status_queue: Optional["queue_module.Queue"] = None,
    chat_session_id: Optional[str] = None,
    timeout: Optional[float] = None,
    cancel_event: Any = None,
) -> Dict[str, Any]:
    if not (prompt or "").strip():
        return {"success": False, "error": "No prompt provided", "output": ""}
    muse_bin = _which_muse_native()
    if not muse_bin:
        return _fallback("no native Muse Code binary (WSL installs use exec)")
    workdir = cwd or os.getcwd()
    if not os.path.isdir(workdir):
        return _fallback(f"Invalid working directory: {workdir}")
    workdir = str(Path(workdir).resolve())
    timeout_sec = float(timeout) if timeout is not None else _default_timeout()
    rid = (resume or "").strip()
    effort = (reasoning_effort or "").strip()
    if effort.lower() == "none":
        effort = ""
    prov = (provider or os.getenv("MUSE_PROVIDER") or "").strip() or None
    meta_provider = not prov or prov.lower() == "meta"

    from api.agent_harness import steer as steer_registry
    from api.agent_harness.activity import ActivityEmitter

    argv = [muse_bin, "serve", "--trust-workspace"]
    if yolo:
        argv.append("--disable-sandbox")
    loop = asyncio.get_running_loop()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=os.environ.copy(),
            limit=_STDOUT_LINE_LIMIT,
        )
    except OSError as exc:
        return _fallback(f"Failed to spawn muse serve: {exc}")
    attach_to_chat_run(chat_session_id, proc)

    rpc = StdioRpc(proc, loop=loop, jsonrpc_tag=True)
    activity = ActivityEmitter(status_queue, agent_label="Muse Code")
    activity.emit("Resuming Muse Code…" if rid else "Starting Muse Code…", force=True)
    st: Dict[str, Any] = {
        "session_id": None,
        "turn_id": None,
        "terminal": None,
        "turn_error": None,
        "turn_usage": None,
        "prompt_tokens": 0,
        "setup_error": None,
        "steer_token": None,
        "steered": 0,
        "tool_count": 0,
        "writing": {},
    }
    from api.agent_harness.questions import QuestionBridge
    from scripts.utilities.muse_cli_tool import _capture_muse_question

    questions = QuestionBridge()
    messages: List[str] = []
    reap_handle: List[Optional[asyncio.TimerHandle]] = [None]

    def _finish_connection() -> None:
        if st["steer_token"] is not None:
            steer_registry.unregister(chat_session_id, st["steer_token"])
            st["steer_token"] = None
        rpc.close_stdin()

        def _reap() -> None:
            if proc.returncode is None:
                asyncio.ensure_future(kill_process_tree(proc))

        if reap_handle[0] is None:
            reap_handle[0] = loop.call_later(_REAP_GRACE_SEC, _reap)

    def _fail_setup(message: str) -> None:
        if st["setup_error"] is None and st["turn_id"] is None:
            st["setup_error"] = message
        _finish_connection()

    def _steer_send(text: str) -> "concurrent.futures.Future":
        out: "concurrent.futures.Future" = concurrent.futures.Future()

        def _done(_result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
            if out.done():
                return
            if error is not None:
                out.set_result((False, rpc_error_text(error)))
            else:
                st["steered"] += 1
                out.set_result((True, None))

        def _params() -> Dict[str, Any]:
            params: Dict[str, Any] = {
                "commandId": command_id(),
                "sessionId": st["session_id"],
                "expectedTurnId": st["turn_id"],
                "input": [{"type": "text", "text": text}],
            }
            if effort and meta_provider:
                params["reasoningEffort"] = effort
            return params

        rpc.request_threadsafe("turn/steer", _params, _done)
        return out

    def _on_turn(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        if error is not None or not isinstance(result, dict):
            _fail_setup(f"turn/start: {rpc_error_text(error)}")
            return
        if result.get("turnId"):
            st["turn_id"] = str(result["turnId"])
        if st["terminal"] is None and chat_session_id and st["steer_token"] is None:
            st["steer_token"] = steer_registry.register(chat_session_id, "muse", _steer_send)

    def _start_turn() -> None:
        params: Dict[str, Any] = {
            "commandId": command_id(),
            "sessionId": st["session_id"],
            "input": [{"type": "text", "text": prompt}],
        }
        if effort and meta_provider:
            params["reasoningEffort"] = effort
        try:
            rpc.request("turn/start", params, _on_turn)
        except Exception as exc:
            _fail_setup(f"turn/start: {exc}")

    def _after_resume_setup(steps: List[tuple]) -> None:
        """Apply per-turn session settings one at a time, then start the turn."""
        if not steps:
            _start_turn()
            return
        method, params = steps[0]

        def _next(_r: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
            if error is not None:
                activity.note(f"{method} ignored: {rpc_error_text(error)}")
            _after_resume_setup(steps[1:])

        try:
            rpc.request(method, params, _next)
        except Exception as exc:
            _fail_setup(f"{method}: {exc}")

    def _on_session(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        method = "session/resume" if rid else "session/start"
        if error is not None or not isinstance(result, dict):
            _fail_setup(f"{method}: {rpc_error_text(error)}")
            return
        session = result.get("session") if isinstance(result.get("session"), dict) else {}
        sid = str(session.get("sessionId") or session.get("id") or rid or "").strip()
        if not sid:
            _fail_setup(f"{method}: no session id")
            return
        st["session_id"] = sid
        if chat_session_id:
            try:
                from scripts.utilities.muse_cli_session_store import save_muse_resume_id

                save_muse_resume_id(workdir, chat_session_id, sid)
            except Exception:
                pass
        used = session.get("modelId")
        if isinstance(used, str) and used.strip():
            activity.emit(f"Muse Code ready (model: {used.strip()})")
        steps: List[tuple] = []
        if rid:
            steps.append(
                ("session/setApprovalMode", {"commandId": command_id(), "sessionId": sid, "mode": "allowAll"})
            )
            if model and meta_provider:
                steps.append(
                    ("session/setModel", {"commandId": command_id(), "sessionId": sid, "model": model})
                )
        _after_resume_setup(steps)

    def _on_init(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        if error is not None:
            _fail_setup(f"initialize: {rpc_error_text(error)}")
            return
        try:
            rpc.notify("initialized", {})
            if rid:
                rpc.request(
                    "session/resume",
                    {"commandId": command_id(), "sessionId": rid, "excludeItems": True},
                    _on_session,
                )
            else:
                params: Dict[str, Any] = {
                    "commandId": command_id(),
                    "workspaceRoot": workdir,
                    "approvalMode": "allowAll",
                }
                if model and meta_provider:
                    params["modelId"] = model
                if prov:
                    params["providerId"] = prov
                rpc.request("session/start", params, _on_session)
        except Exception as exc:
            _fail_setup(f"session setup: {exc}")

    def _handle_server_request(msg: Dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method.rsplit("/", 1)[-1] in ("requestUserInput", "requestUserInputAsync", "request_user_input"):
            if st["session_id"] and params.get("sessionId") not in (None, st["session_id"]):
                rpc.respond(msg.get("id"), error={"code": -32000, "message": "Input request belongs to another session"})
                return
            if st["turn_id"] and params.get("turnId") not in (None, st["turn_id"]):
                rpc.respond(msg.get("id"), error={"code": -32000, "message": "Stale input request"})
                return
            questions.capture(params)
            rpc.respond(msg.get("id"), error={"code": -32000, "message":
                "Input deferred to a Cuttle form. No user answer supplied; resume on the next turn."})
            activity.emit("Preparing Cuttle question form…", force=True)
            _finish_connection()
            return
        rpc.respond(
            msg.get("id"),
            error={"code": -32601, "message": f"{method} is not supported in headless Cuttle turns"},
        )

    def _on_item(method: str, item: Dict[str, Any]) -> None:
        kind = str(item.get("kind") or "")
        if st["turn_id"] and item.get("turnId") and item.get("turnId") != st["turn_id"]:
            return
        if kind == "userMessage":
            if method == "item/completed" and item.get("steered"):
                activity.emit(
                    f"steer received: {_first_line(item.get('text') or '', 100)}", force=True
                )
            return
        if kind == "agentMessage":
            if method == "item/completed":
                text = str(item.get("text") or "").strip()
                if text:
                    messages.append(text)
                    activity.emit(f"writing: …{text.replace(chr(10), ' ')[-120:]}")
                st["writing"].pop(item.get("itemId"), None)
            return
        if kind == "reasoning":
            preview = _reasoning_preview(item)
            if preview:
                activity.emit(f"thinking: {preview}")
            return
        if kind == "toolCall" and _capture_muse_question(questions, item, complete=method == "item/completed"):
            activity.emit("Preparing Cuttle question form…", force=True)
            _finish_connection()
            return
        if kind in ("toolCall", "subagent", "workflow", "userShell"):
            status = str(item.get("status") or "").lower()
            if method == "item/started":
                st["tool_count"] += 1
                activity.emit(f"tool {st['tool_count']}: {_tool_label(item)}")
            elif method == "item/completed" and status in ("failed", "error", "cancelled", "denied"):
                activity.emit(f"tool failed: {_tool_label(item)}")

    def _on_line(raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8", errors="replace").strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict) or rpc.dispatch_reply(msg):
            return
        method = str(msg.get("method") or "")
        if "id" in msg and method:
            _handle_server_request(msg)
            return
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if st["session_id"] and params.get("sessionId") not in (None, st["session_id"]):
            return

        if method in ("item/started", "item/completed"):
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            _on_item(method, item)
            return
        if method == "item/delta" and params.get("field") in (None, "text"):
            item_id = params.get("itemId")
            buf = str(st["writing"].get(item_id) or "") + str(params.get("delta") or "")
            st["writing"][item_id] = buf[-4000:]
            preview = buf.replace("\n", " ").strip()[-120:]
            if preview:
                activity.emit(f"writing: …{preview}")
            return
        if method == "session/tokenUsage":
            try:
                st["prompt_tokens"] = int(params.get("promptTokens") or 0)
            except (TypeError, ValueError):
                pass
            return
        if method == "turn/completed":
            if st["turn_id"] and params.get("turnId") and params.get("turnId") != st["turn_id"]:
                return
            st["terminal"] = str(params.get("terminal") or "unknown")
            err = params.get("error") if isinstance(params.get("error"), dict) else {}
            if err.get("message"):
                st["turn_error"] = str(err["message"])
            elif st["terminal"] != "completed" and params.get("reason"):
                st["turn_error"] = str(params["reason"])
            st["turn_usage"] = params.get("usage")
            _finish_connection()

    stop_hb = asyncio.Event()
    hb_task = (
        asyncio.create_task(activity.heartbeat_loop(stop_hb))
        if status_queue is not None
        else None
    )
    run = None
    try:
        rpc.request(
            "initialize",
            {"clientInfo": {"name": "cuttle", "title": "Cuttle", "version": "1.0"}},
            _on_init,
        )
        run = await run_interruptible(
            proc,
            timeout=timeout_sec,
            cancel_event=cancel_event,
            on_stdout_line=_on_line,
            line_mode=True,
        )
    except Exception as exc:
        st["turn_error"] = st["turn_error"] or str(exc)
        if st["setup_error"] is None:
            st["setup_error"] = f"muse serve failed: {exc}"
    finally:
        stop_hb.set()
        if hb_task is not None:
            try:
                await asyncio.wait_for(hb_task, timeout=1.0)
            except Exception:
                hb_task.cancel()
        if st["steer_token"] is not None:
            steer_registry.unregister(chat_session_id, st["steer_token"])
            st["steer_token"] = None
        if reap_handle[0] is not None:
            reap_handle[0].cancel()
        if run is None and proc.returncode is None:
            await kill_process_tree(proc)

    session_id = st["session_id"]
    # exec shows only the terminal text; a steered turn can answer in several messages.
    if st["steered"]:
        display = "\n\n".join(messages).strip()
    else:
        display = messages[-1] if messages else ""
    if not display and st["writing"]:
        display = "\n\n".join(v for v in st["writing"].values() if v).strip()
    stderr = (run.stderr if run is not None else b"").decode("utf-8", errors="replace").strip()
    base: Dict[str, Any] = {
        "usage": _usage_from_turn(st["turn_usage"], st["prompt_tokens"]),
        "muse_session_id": session_id,
        "steered": st["steered"],
        "transport": "serve",
        "stderr": stderr[:4000],
    }

    if run is not None and (run.cancelled or run.timed_out):
        reason = run.reason or ("cancelled" if run.cancelled else f"timed out after {timeout_sec:.0f}s")
        notice = format_interrupt_notice(
            "Muse Code",
            reason,
            elapsed_sec=run.elapsed_sec,
            session_saved=bool(session_id),
            resume_slash="muse",
        )
        body = f"{display}\n\n{notice}".strip() if display else notice
        return {
            **base,
            "success": False,
            "error": f"Muse Code {reason}",
            "output": body,
            "timed_out": run.timed_out,
            "cancelled": run.cancelled,
        }

    if questions.pending:
        return {**base, "success": True, "output": questions.render(display),
                "error": None, "awaiting_input": bool(questions.questions)}

    if st["turn_id"] is None:
        return _fallback(st["setup_error"] or _first_line(stderr, 300) or "muse serve exited before the turn started")

    if st["terminal"] == "completed":
        return {**base, "success": True, "output": display or "Done.", "error": None}

    err_msg = (
        st["turn_error"]
        or (f"turn {st['terminal']}" if st["terminal"] else "")
        or _first_line(stderr, 300)
        or "muse serve exited mid-turn"
    )
    return {**base, "success": False, "output": display or err_msg, "error": err_msg[:2000]}
