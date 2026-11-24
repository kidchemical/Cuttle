"""Antigravity turns on ``agy acp`` (ACP JSON-RPC over stdio) so follow-ups can steer.

``agy -p --input-format stream-json`` queues extra stdin lines as separate turns,
whereas ``agy acp`` accepts a mid-turn ``session/prompt`` while a turn is running:
the server immediately resolves the superseded ``session/prompt`` with its partial
usage, injects the follow-up into the live turn (echoing it as a
``user_message_chunk``), and resolves the new ``session/prompt`` when the turn
finishes.

Returns ``{"fallback": True, ...}`` when the server could not start the turn
(spawn error, handshake/session failure, unknown resume id), so the adapter
reruns the prompt on ``agy -p``.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from api.agent_harness.activity import (
    ActivityEmitter,
    TextActivityLog,
    ToolActivityLog,
    text_preview,
)
from api.agent_harness.agents.antigravity.model_catalog import split_effort_slug
from api.agent_harness.agents.antigravity.session_store import save_antigravity_resume_id
from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    kill_process_tree,
    run_interruptible,
)
from scripts.utilities.stdio_rpc import StdioRpc, rpc_error_text

if TYPE_CHECKING:
    import queue as queue_module

_STDOUT_LINE_LIMIT = 16 * 1024 * 1024
_REAP_GRACE_SEC = 10.0


def _first_line(text: str, limit: int = 100) -> str:
    line = " ".join(str(text or "").split())
    return line[:limit]


def _fallback(error: str) -> Dict[str, Any]:
    return {"success": False, "fallback": True, "error": error, "output": ""}


def _accumulate_usage(totals: Dict[str, int], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for src, dst in (
        ("inputTokens", "input_tokens"),
        ("input_tokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("output_tokens", "output_tokens"),
        ("cachedReadTokens", "cache_read_tokens"),
        ("cache_read_tokens", "cache_read_tokens"),
        ("cachedWriteTokens", "cache_write_tokens"),
        ("cache_write_tokens", "cache_write_tokens"),
        ("thoughtTokens", "thinking_tokens"),
        ("thinking_tokens", "thinking_tokens"),
        ("totalTokens", "total_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        val = raw.get(src)
        if val is None:
            continue
        try:
            n = max(0, int(val))
        except (TypeError, ValueError):
            continue
        if n:
            totals[dst] = totals.get(dst, 0) + n


def _config_option_values(config_options: Any, config_id: str) -> Set[str]:
    if not isinstance(config_options, list):
        return set()
    for opt in config_options:
        if isinstance(opt, dict) and opt.get("id") == config_id:
            vals: Set[str] = set()
            for item in opt.get("options") or []:
                if isinstance(item, dict) and item.get("value"):
                    vals.add(str(item["value"]))
            return vals
    return set()


def _plan_config_steps(
    sid: str,
    model: Optional[str],
    effort: Optional[str],
    config_options: Any,
) -> List[Tuple[str, Dict[str, Any], bool]]:
    """Build ``(config_id, params, required)`` steps for ``session/set_config_option``.

    ACP exposes ``model`` (family slug, or single-effort slug like
    ``gpt-oss-120b-medium``) and ``thought_level`` (``low``..``max``).
    """
    steps: List[Tuple[str, Dict[str, Any], bool]] = []
    mid = str(model or "").strip()
    eff = str(effort or "").strip().lower()
    model_opts = _config_option_values(config_options, "model")
    effort_opts = _config_option_values(config_options, "thought_level")

    if mid:
        family, suffix = split_effort_slug(mid)
        if model_opts and mid in model_opts:
            target_model = mid
            target_effort = eff
        elif model_opts and family in model_opts:
            target_model = family
            target_effort = suffix or eff
        else:
            target_model = family or mid
            target_effort = suffix or eff
        steps.append((
            "model",
            {"sessionId": sid, "configId": "model", "value": target_model},
            True,
        ))
        if target_effort and (not effort_opts or target_effort in effort_opts):
            steps.append((
                "thought_level",
                {"sessionId": sid, "configId": "thought_level", "value": target_effort},
                False,
            ))
    elif eff and (not effort_opts or eff in effort_opts):
        steps.append((
            "thought_level",
            {"sessionId": sid, "configId": "thought_level", "value": eff},
            False,
        ))
    return steps


def _extract_chunk_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "".join(parts)
    if isinstance(content, str):
        return content
    return ""


async def run_antigravity_turn_acp(
    prompt: str,
    *,
    exe: str,
    cwd: str,
    resume: Optional[str],
    model: Optional[str],
    effort: Optional[str],
    status_queue: Optional["queue_module.Queue"] = None,
    chat_session_id: Optional[str] = None,
    timeout: float = 3600.0,
    cancel_event: Any = None,
) -> Dict[str, Any]:
    if not (prompt or "").strip():
        return {"success": False, "error": "No prompt provided", "output": ""}
    workdir = cwd or os.getcwd()
    if not os.path.isdir(workdir):
        return _fallback(f"Invalid working directory: {workdir}")
    workdir = str(Path(workdir).resolve())
    rid = (resume or "").strip()

    from api.agent_harness import steer as steer_registry
    from api.agent_harness.steer_delivery import SteerDelivery

    argv = [exe, "acp", "--stdio", "--dangerously-skip-permissions"]
    loop = asyncio.get_running_loop()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=agent_cli_env(),
            limit=_STDOUT_LINE_LIMIT,
        )
    except OSError as exc:
        return _fallback(f"Failed to spawn agy acp: {exc}")
    attach_to_chat_run(chat_session_id, proc)

    rpc = StdioRpc(proc, loop=loop, jsonrpc_tag=True)
    activity = ActivityEmitter(
        status_queue,
        agent_label="Antigravity",
        record_text_previews=False,
        record_tool_previews=False,
    )
    text_log = TextActivityLog("antigravity")
    tools = ToolActivityLog("antigravity", activity)
    delivery = SteerDelivery()
    accepting_steers = True
    activity.emit("Resuming Antigravity…" if rid else "Starting Antigravity…", force=True)

    st: Dict[str, Any] = {
        "session_id": None,
        "resolved_model": None,
        "prompt_started": False,
        "inflight_prompts": set(),
        "terminal": None,
        "turn_error": None,
        "setup_error": None,
        "steer_token": None,
        "usage_totals": {},
        "context_tokens": 0,
        "initial_echo_pending": True,
        "msg_seq": 0,
        "cur_msg_id": None,
        "cur_msg_buf": "",
        "thought_seq": 0,
        "cur_thought_id": None,
    }
    messages: List[str] = []
    reap_handle: List[Optional[asyncio.TimerHandle]] = [None]

    def _note(fn: Any, *args: Any) -> None:
        if st["steer_token"] is None:
            return
        try:
            fn(chat_session_id, st["steer_token"], *args)
        except Exception:
            pass

    def _note_echoes(receipts: List[Any]) -> None:
        for receipt in receipts:
            if receipt.steer_id:
                _note(steer_registry.note_echo, receipt.steer_id)

    delivery.confirmed = _note_echoes

    def _close_assistant_segment() -> None:
        seg_id = st["cur_msg_id"]
        if seg_id is None:
            return
        text = st["cur_msg_buf"].strip()
        st["cur_msg_id"] = None
        st["cur_msg_buf"] = ""
        text_log.save("writing", text, seg_id)
        if text:
            messages.append(text)
            _note(steer_registry.note_output_text, seg_id, text)
            activity.emit(f"writing: {text_preview(text)}", force=True)

    def _finish_connection() -> None:
        nonlocal accepting_steers
        accepting_steers = False
        _close_assistant_segment()
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
        if st["setup_error"] is None and not st["prompt_started"]:
            st["setup_error"] = message
        _finish_connection()

    def _on_prompt_reply(req_id: int, result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        st["inflight_prompts"].discard(req_id)
        if isinstance(result, dict):
            _accumulate_usage(st["usage_totals"], result.get("usage"))
        if error is not None:
            st["turn_error"] = rpc_error_text(error)
        # When a steer session/prompt took over, the superseded prompt resolves
        # immediately while the newer prompt is still in flight.
        if st["inflight_prompts"]:
            return
        if error is not None:
            st["terminal"] = "error"
        else:
            stop_reason = str((result or {}).get("stopReason") or "end_turn")
            st["terminal"] = stop_reason
        _finish_connection()

    def _send_prompt_request(text: str) -> int:
        req_id_holder: List[int] = [0]

        def _reply(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
            _on_prompt_reply(req_id_holder[0], result, error)

        req_id = rpc.request(
            "session/prompt",
            {
                "sessionId": st["session_id"],
                "prompt": [{"type": "text", "text": text}],
            },
            _reply,
        )
        req_id_holder[0] = req_id
        st["inflight_prompts"].add(req_id)
        return req_id

    def _steer_send(text: str, steer_id: Optional[str] = None) -> "concurrent.futures.Future":
        out: "concurrent.futures.Future" = concurrent.futures.Future()

        def _go() -> None:
            if out.done():
                return
            if not accepting_steers or st["terminal"] is not None or not st["inflight_prompts"]:
                out.set_result((False, "turn is no longer active"))
                return
            receipt = delivery.begin(text, steer_id)
            try:
                _send_prompt_request(text)
            except Exception as exc:
                accepted = delivery.acknowledge(receipt, succeeded=False, refused=False)
                out.set_result((True, None) if accepted else (False, str(exc)))
                return
            delivery.acknowledge(receipt, succeeded=True)
            if not receipt.received:
                activity.emit(
                    f"steer queued: {_first_line(text, 100)} (waiting for Antigravity receipt)",
                    force=True,
                )
            out.set_result((True, None))

        try:
            loop.call_soon_threadsafe(_go)
        except RuntimeError as exc:
            out.set_result((False, f"event loop closed: {exc}"))
        return out

    def _start_prompt() -> None:
        try:
            _send_prompt_request(prompt)
            st["prompt_started"] = True
            if st["terminal"] is None and chat_session_id and st["steer_token"] is None:
                st["steer_token"] = steer_registry.register(chat_session_id, "antigravity", _steer_send)
        except Exception as exc:
            _fail_setup(f"session/prompt: {exc}")

    def _apply_config_steps(steps: List[Tuple[str, Dict[str, Any], bool]]) -> None:
        if not steps:
            _start_prompt()
            return
        cfg_id, params, required = steps[0]

        def _next(_res: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
            if error is not None:
                msg = f"session/set_config_option({cfg_id}): {rpc_error_text(error)}"
                if required:
                    _fail_setup(msg)
                    return
                activity.note(f"{msg} ignored")
            _apply_config_steps(steps[1:])

        try:
            rpc.request("session/set_config_option", params, _next)
        except Exception as exc:
            _fail_setup(f"session/set_config_option({cfg_id}): {exc}")

    def _on_session(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        method = "session/resume" if rid else "session/new"
        if error is not None or not isinstance(result, dict):
            _fail_setup(f"{method}: {rpc_error_text(error)}")
            return
        sid = str(result.get("sessionId") or rid or "").strip()
        if not sid:
            _fail_setup(f"{method}: no sessionId")
            return
        st["session_id"] = sid
        models_info = result.get("models") if isinstance(result.get("models"), dict) else {}
        cur_model = str(models_info.get("currentModelId") or "").strip()
        if cur_model:
            st["resolved_model"] = cur_model
        if chat_session_id:
            try:
                save_antigravity_resume_id(workdir, chat_session_id, sid)
            except Exception:
                pass
        activity.emit("Antigravity ready", force=True)
        steps = _plan_config_steps(sid, model, effort, result.get("configOptions"))
        _apply_config_steps(steps)

    def _on_init(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        if error is not None:
            _fail_setup(f"initialize: {rpc_error_text(error)}")
            return
        try:
            if rid:
                rpc.request(
                    "session/resume",
                    {"sessionId": rid, "cwd": workdir, "mcpServers": []},
                    _on_session,
                )
            else:
                rpc.request(
                    "session/new",
                    {"cwd": workdir, "mcpServers": []},
                    _on_session,
                )
        except Exception as exc:
            _fail_setup(f"session setup: {exc}")

    def _handle_server_request(msg: Dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "session/request_permission":
            options = params.get("options") if isinstance(params.get("options"), list) else []
            chosen = None
            for kind in ("allow_always", "allow_once"):
                for opt in options:
                    if isinstance(opt, dict) and opt.get("kind") == kind and opt.get("optionId"):
                        chosen = str(opt["optionId"])
                        break
                if chosen:
                    break
            if chosen:
                rpc.respond(msg.get("id"), result={"outcome": {"outcome": "selected", "optionId": chosen}})
                return
            rpc.respond(msg.get("id"), result={"outcome": {"outcome": "cancelled"}})
            return
        rpc.respond(
            msg.get("id"),
            error={"code": -32601, "message": f"{method} is not supported in headless Cuttle turns"},
        )

    def _on_update(update: Dict[str, Any]) -> None:
        kind = str(update.get("sessionUpdate") or "")
        if kind == "user_message_chunk":
            text = _extract_chunk_text(update.get("content"))
            if st["initial_echo_pending"]:
                if text.strip() == prompt.strip() or not delivery.accepted_count:
                    st["initial_echo_pending"] = False
                    return
            if accepting_steers and text.strip():
                _close_assistant_segment()
                confirmed = delivery.receive(text)
                if confirmed:
                    activity.emit(f"steer received: {_first_line(text, 100)}", force=True)
            return
        if kind == "agent_message_chunk":
            delta = _extract_chunk_text(update.get("content"))
            if not delta:
                return
            st["cur_thought_id"] = None
            if st["cur_msg_id"] is None:
                st["msg_seq"] += 1
                seg_id = f"msg-{st['msg_seq']}"
                st["cur_msg_id"] = seg_id
                st["cur_msg_buf"] = ""
                text_log.start("writing", seg_id)
                _note(steer_registry.note_output_start, seg_id)
            seg_id = st["cur_msg_id"]
            st["cur_msg_buf"] += delta
            full = text_log.delta("writing", delta, seg_id)
            if activity.emit(f"writing: {text_preview(full)}"):
                text_log.save("writing", full, seg_id)
            return
        if kind == "agent_thought_chunk":
            delta = _extract_chunk_text(update.get("content"))
            if not delta:
                return
            _close_assistant_segment()
            if st["cur_thought_id"] is None:
                st["thought_seq"] += 1
                st["cur_thought_id"] = f"thought-{st['thought_seq']}"
                text_log.start("thinking", st["cur_thought_id"])
            full = text_log.delta("thinking", delta, st["cur_thought_id"])
            if activity.emit(f"thinking: {text_preview(full)}"):
                text_log.save("thinking", full, st["cur_thought_id"])
            return
        if kind in ("tool_call", "tool_call_update"):
            _close_assistant_segment()
            st["cur_thought_id"] = None
            call_id = str(update.get("toolCallId") or update.get("id") or "tool")
            name = str(update.get("name") or update.get("title") or "tool")
            args = update.get("args") if update.get("args") is not None else update.get("rawInput")
            status = str(update.get("status") or "").lower()
            failed = status in ("failed", "error", "cancelled", "denied")
            done = status in ("completed", "done") or failed
            result_payload = update.get("rawOutput") if done else None
            if done and result_payload is None and update.get("content") is not None:
                result_payload = update.get("content")
            tools.record(
                call_id,
                name,
                args,
                phase="failed" if failed else ("completed" if done else "started"),
                result=result_payload,
                failed=failed,
            )
            return
        if kind == "usage_update":
            try:
                used = int(update.get("used") or 0)
            except (TypeError, ValueError):
                used = 0
            if used > 0:
                st["context_tokens"] = used
            return

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
        if method == "session/update":
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            _on_update(update)

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
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "cuttle", "title": "Cuttle", "version": "1.0"},
            },
            _on_init,
        )
        run = await run_interruptible(
            proc,
            timeout=timeout,
            cancel_event=cancel_event,
            on_stdout_line=_on_line,
            line_mode=True,
        )
    except Exception as exc:
        st["turn_error"] = st["turn_error"] or str(exc)
        if st["setup_error"] is None:
            st["setup_error"] = f"agy acp failed: {exc}"
    finally:
        _finish_connection()
        text_log.flush()
        stop_hb.set()
        if hb_task is not None:
            try:
                await asyncio.wait_for(hb_task, timeout=1.0)
            except Exception:
                hb_task.cancel()
        if reap_handle[0] is not None:
            reap_handle[0].cancel()
        if run is None and proc.returncode is None:
            await kill_process_tree(proc)

    session_id = st["session_id"]
    if delivery.accepted_count:
        display = "\n\n".join(messages).strip()
    else:
        display = "\n\n".join(messages).strip() if messages else ""
    undelivered = delivery.undelivered
    closing = delivery.closing_notice("Antigravity")
    if closing:
        display = f"{display}\n\n{closing}".strip()
    stderr = (run.stderr if run is not None else b"").decode("utf-8", errors="replace").strip()
    base: Dict[str, Any] = {
        "usage": dict(st["usage_totals"]),
        "context_tokens": int(st["context_tokens"] or 0),
        "session_id": session_id,
        "resolved_model": st["resolved_model"],
        "steered": delivery.received_count,
        "undelivered_steers": undelivered,
        "transport": "acp",
        "stderr": stderr[:4000],
        "returncode": run.returncode if run is not None else None,
    }

    if run is not None and (run.cancelled or run.timed_out):
        reason = run.reason or ("cancelled" if run.cancelled else f"timed out after {timeout:.0f}s")
        notice = format_interrupt_notice(
            "Antigravity",
            f"Antigravity {reason}" if "Antigravity" not in reason else reason,
            elapsed_sec=run.elapsed_sec,
            session_saved=bool(session_id),
            resume_slash="antigravity",
        )
        body = f"{display}\n\n{notice}".strip() if display else notice
        return {
            **base,
            "success": False,
            "error": f"Antigravity {reason}",
            "output": body,
            "timed_out": run.timed_out,
            "cancelled": run.cancelled,
        }

    if not st["prompt_started"]:
        return _fallback(
            st["setup_error"]
            or _first_line(stderr, 300)
            or "agy acp exited before the turn started"
        )

    if st["terminal"] in ("end_turn", "max_tokens") and not st["turn_error"]:
        return {**base, "success": True, "output": display or "Done.", "error": None}

    err_msg = (
        st["turn_error"]
        or (f"turn {st['terminal']}" if st["terminal"] else "")
        or _first_line(stderr, 300)
        or "agy acp exited mid-turn"
    )
    return {**base, "success": False, "output": display, "error": err_msg[:2000]}
