"""Codex turns on ``codex app-server`` so follow-ups can steer the live turn.

Same result dict as ``CodexCliTool.execute_prompt`` (``codex exec --json``),
plus ``token_usage`` (window occupancy) and ``steered``. Returns
``{"fallback": True, …}`` when the server could not start a turn, so the
adapter reruns the prompt on ``exec`` and nothing is lost.

The runner owns its thread id for the whole server lifetime (see
``api.agent_harness.codex_thread_ownership``): probes and compaction skip
instead of spawning a competing writer, in either order.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    kill_process_tree,
    run_interruptible,
)
from scripts.utilities.codex_app_server import _token_usage_fill
from scripts.utilities.codex_cli_tool import (
    _STDOUT_LINE_LIMIT,
    _codex_activity_for_event,
    _default_timeout,
    _first_line,
    codex_executable,
)
from scripts.utilities.stdio_rpc import StdioRpc, rpc_error_text

if TYPE_CHECKING:
    import queue as queue_module

_REAP_GRACE_SEC = 15.0
_APPROVAL_METHODS = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "applyPatchApproval",
    "execCommandApproval",
)
_SKIP_ACTIVITY_ITEMS = ("userMessage", "hookPrompt")
_REASONING_DELTAS = ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta")
_SNAPSHOT_MIN_SEC = 10.0


def _delta_preview(buf: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", buf or "").strip()[-limit:]


def _undelivered_notice(texts: List[str]) -> str:
    lines = "\n".join(f"- {_first_line(t, 200)}" for t in texts)
    return (
        "Codex ended this turn before reading your follow-up"
        f"{'s' if len(texts) > 1 else ''}:\n{lines}\nSend it again to continue."
    )


def _snake(item_type: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", item_type or "").lower()


def _exec_style_event(event_type: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape an app-server item so ``_codex_activity_for_event`` can label it."""
    copy = dict(item)
    copy["type"] = _snake(str(item.get("type") or ""))
    return {"type": event_type, "item": copy}


def _usage_from_token_usage(tu: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(tu, dict):
        return {}
    total = tu.get("total") if isinstance(tu.get("total"), dict) else {}
    out: Dict[str, Any] = {}
    for src, dst in (
        ("inputTokens", "input_tokens"),
        ("input_tokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("output_tokens", "output_tokens"),
        ("totalTokens", "total_tokens"),
        ("total_tokens", "total_tokens"),
        ("cachedInputTokens", "cached_input_tokens"),
        ("cached_input_tokens", "cached_input_tokens"),
        ("cacheWriteInputTokens", "cache_write_input_tokens"),
        ("cache_write_input_tokens", "cache_write_input_tokens"),
    ):
        try:
            val = int(total.get(src) or 0)
        except (TypeError, ValueError):
            val = 0
        if val:
            out[dst] = val
    return out


def _note_turn_tokens(st: Dict[str, Any], tu: Dict[str, Any]) -> None:
    """Accumulate the per-call ``last`` deltas of thread/tokenUsage/updated.

    ``total`` is thread-cumulative across a resumed session, so per-turn usage
    must be summed from the per-request ``last`` payloads instead.
    """
    last = tu.get("last") if isinstance(tu.get("last"), dict) else {}
    if not last:
        return
    acc = st.setdefault(
        "turn_tokens",
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "calls": 0,
        },
    )
    try:
        acc["input_tokens"] += int(last.get("inputTokens") or last.get("input_tokens") or 0)
        acc["output_tokens"] += int(last.get("outputTokens") or last.get("output_tokens") or 0)
        acc["cached_input_tokens"] += int(
            last.get("cachedInputTokens") or last.get("cached_input_tokens") or 0
        )
        acc["cache_write_input_tokens"] += int(
            last.get("cacheWriteInputTokens") or last.get("cache_write_input_tokens") or 0
        )
        acc["calls"] += 1
    except (TypeError, ValueError):
        pass


def _usage_from_turn_tokens(st: Dict[str, Any]) -> Dict[str, Any]:
    acc = st.get("turn_tokens")
    if not isinstance(acc, dict) or not acc.get("calls"):
        return {}
    inp = int(acc.get("input_tokens") or 0)
    outp = int(acc.get("output_tokens") or 0)
    if inp <= 0 and outp <= 0:
        return {}
    out: Dict[str, Any] = {"input_tokens": inp, "output_tokens": outp, "total_tokens": inp + outp}
    if acc.get("cached_input_tokens"):
        out["cached_input_tokens"] = int(acc["cached_input_tokens"])
    if acc.get("cache_write_input_tokens"):
        out["cache_write_input_tokens"] = int(acc["cache_write_input_tokens"])
    return out


def _fallback(error: str) -> Dict[str, Any]:
    return {"success": False, "fallback": True, "error": error, "output": ""}


# Bound for waiting out a previous owner at turn start (e.g. resend racing
# a cancelled turn's reaping). Cancel-aware; the runner never blocks longer.
_OWNERSHIP_WAIT_SEC = 30.0

# Bound for reaping our own server before freeing its thread: a kill
# request is not proof of exit, and the lease must not drop while the
# vendor may still hold the thread writer.
_REAP_TIMEOUT_SEC = 10.0


def _is_cancelled(cancel_event: Any) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception:
        return False


def _early_end(thread_id: Optional[str], error: str, *, cancelled: bool) -> Dict[str, Any]:
    """Fail-closed result before any turn started (no proc to reap)."""
    return {
        "usage": {},
        "codex_session_id": thread_id,
        "token_usage": None,
        "steered": 0,
        "undelivered_steers": [],
        "transport": "app-server",
        "success": False,
        "error": error,
        "output": "",
        "timed_out": False,
        "cancelled": bool(cancelled),
    }


async def run_codex_turn_app_server(
    prompt: str,
    *,
    cwd: Optional[str],
    resume: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    status_queue: Optional["queue_module.Queue"] = None,
    chat_session_id: Optional[str] = None,
    timeout: Optional[float] = None,
    cancel_event: Any = None,
) -> Dict[str, Any]:
    if not (prompt or "").strip():
        return {"success": False, "error": "No prompt provided", "output": ""}
    exe = codex_executable()
    if not exe:
        return _fallback("Codex CLI not found")
    workdir = cwd or os.getcwd()
    if not os.path.isdir(workdir):
        return _fallback(f"Invalid working directory: {workdir}")
    workdir = str(Path(workdir).resolve())
    timeout_sec = float(timeout) if timeout is not None else _default_timeout()
    rid = (resume or "").strip()

    from api.agent_harness import steer as steer_registry
    from api.agent_harness.activity import ActivityEmitter

    from api.agent_harness import codex_thread_ownership as _ownership

    # Own the resume thread BEFORE spawning: no child exists while we wait
    # for the lease, so async cancellation or a setup exception can never
    # leave a spawned server outside the cleanup scope. The wait is
    # bounded and cancel-aware; on failure fail closed before touching
    # the thread.
    own_token: Optional[int] = None
    own_tid: Optional[str] = None
    if _is_cancelled(cancel_event):
        return _early_end(rid or None, "Codex CLI cancelled", cancelled=True)
    if rid:
        own_token = await _ownership.await_acquire(
            rid,
            "codex-turn",
            timeout=_OWNERSHIP_WAIT_SEC,
            cancel_event=cancel_event,
        )
        if own_token is None:
            if _is_cancelled(cancel_event):
                return _early_end(rid, "Codex CLI cancelled", cancelled=True)
            return _early_end(
                rid,
                "Codex thread busy "
                f"(owned by {_ownership.owner_of(rid) or 'another writer'})",
                cancelled=False,
            )
        own_tid = rid

    loop = asyncio.get_running_loop()
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=agent_cli_env(),
            limit=_STDOUT_LINE_LIMIT,
        )
    except OSError as exc:
        # No server was spawned; release the just-taken lease, then fail.
        if own_token is not None and own_tid:
            _ownership.release(own_tid, own_token)
        return _fallback(f"Failed to spawn Codex app-server: {exc}")
    except BaseException:
        # No resume request has been sent while spawning. A cancelled
        # spawn must not leave a lease held without a running turn.
        if own_token is not None and own_tid:
            _ownership.release(own_tid, own_token)
        raise
    attach_to_chat_run(chat_session_id, proc)

    rpc = StdioRpc(proc, loop=loop)
    activity = ActivityEmitter(status_queue, agent_label="Codex")
    activity.emit("Resuming Codex…" if rid else "Starting Codex…", force=True)
    activity_state: Dict[str, Any] = {"tool_count": 0}
    st: Dict[str, Any] = {
        "thread_id": None,
        "turn_id": None,
        "turn_status": None,
        "turn_error": None,
        "setup_error": None,
        "errors": [],
        "token_usage": None,
        "steer_token": None,
        "own_token": own_token,
        "own_tid": own_tid,
        "steered": 0,
        "user_items": 0,
        # Codex only splices steer input in at its next sampling step, and echoes
        # each one as a userMessage item (possibly before the turn/steer reply).
        "steers_accepted": [],
        "user_texts": [],
        "reasoning_buf": "",
        "writing_buf": "",
        "snapshot_at": 0.0,
    }
    from api.agent_harness.questions import QuestionBridge
    from scripts.utilities.codex_cli_tool import _capture_codex_question

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
                st["steers_accepted"].append(text)
                activity.emit(
                    f"steer queued: {_first_line(text, 80)} (Codex reads it after its current step)",
                    force=True,
                )
                out.set_result((True, None))

        rpc.request_threadsafe(
            "turn/steer",
            lambda: {
                "threadId": st["thread_id"],
                "expectedTurnId": st["turn_id"],
                "input": [{"type": "text", "text": text}],
            },
            _done,
        )
        return out

    def _on_turn(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        if error is not None or not isinstance(result, dict):
            _fail_setup(f"turn/start: {rpc_error_text(error)}")
            return
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        if turn.get("id"):
            st["turn_id"] = str(turn["id"])
        if st["turn_status"] is None and chat_session_id and st["steer_token"] is None:
            st["steer_token"] = steer_registry.register(chat_session_id, "codex", _steer_send)

    def _on_thread(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        method = "thread/resume" if rid else "thread/start"
        if error is not None or not isinstance(result, dict):
            _fail_setup(f"{method}: {rpc_error_text(error)}")
            return
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        tid = str(thread.get("id") or rid or "").strip()
        if not tid:
            _fail_setup(f"{method}: no thread id")
            return
        st["thread_id"] = tid
        if st.get("own_tid") != tid:
            if st.get("own_token") is not None:
                _ownership.release(st.get("own_tid"), st.get("own_token"))
                st["own_token"] = None
            tok = _ownership.try_acquire(tid, "codex-turn")
            if tok is None:
                _fail_setup(
                    "Codex thread busy (owned by "
                    f"{_ownership.owner_of(tid) or 'another writer'})"
                )
                return
            st["own_token"] = tok
            st["own_tid"] = tid
        if chat_session_id:
            try:
                from scripts.utilities.codex_cli_session_store import save_codex_resume_id

                save_codex_resume_id(workdir, chat_session_id, tid)
            except Exception:
                pass
        used_model = result.get("model")
        if isinstance(used_model, str) and used_model.strip():
            activity.emit(f"Codex ready (model: {used_model.strip()})")
        params: Dict[str, Any] = {
            "threadId": tid,
            "input": [{"type": "text", "text": prompt}],
        }
        if model:
            params["model"] = model
        if reasoning_effort:
            params["effort"] = reasoning_effort
        try:
            rpc.request("turn/start", params, _on_turn)
        except Exception as exc:
            _fail_setup(f"turn/start: {exc}")

    def _on_init(result: Optional[Dict[str, Any]], error: Optional[Dict[str, Any]]) -> None:
        if error is not None:
            _fail_setup(f"initialize: {rpc_error_text(error)}")
            return
        params: Dict[str, Any] = {
            "cwd": workdir,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if model:
            params["model"] = model
        try:
            rpc.notify("initialized", {})
            if rid:
                params["threadId"] = rid
                params["excludeTurns"] = True
                rpc.request("thread/resume", params, _on_thread)
            else:
                rpc.request("thread/start", params, _on_thread)
        except Exception as exc:
            _fail_setup(f"thread setup: {exc}")

    def _handle_server_request(msg: Dict[str, Any]) -> None:
        method = str(msg.get("method") or "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method.rsplit("/", 1)[-1] in ("requestUserInput", "requestUserInputAsync", "request_user_input"):
            if st["thread_id"] and params.get("threadId") not in (None, st["thread_id"]):
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
        if method in _APPROVAL_METHODS or "approval" in method.lower():
            rpc.respond(msg.get("id"), {"decision": "accept"})
            return
        rpc.respond(
            msg.get("id"),
            error={"code": -32601, "message": f"{method} is not supported in headless Cuttle turns"},
        )

    def _mine(params: Dict[str, Any]) -> bool:
        tid = params.get("threadId")
        return tid is None or st["thread_id"] is None or tid == st["thread_id"]

    def _save_live_snapshot(tu: Dict[str, Any]) -> None:
        """Keep the context gauge fed so /api/agent-context never resumes a busy thread."""
        now = time.time()
        if not chat_session_id or now - st["snapshot_at"] < _SNAPSHOT_MIN_SEC:
            return
        fill = _token_usage_fill(tu)
        used = int(fill.get("context_tokens") or 0)
        if used <= 0:
            return
        st["snapshot_at"] = now
        try:
            from scripts.utilities.codex_cli_session_store import save_codex_context_snapshot

            save_codex_context_snapshot(
                chat_session_id,
                {
                    "context_tokens": used,
                    "model_context_window": int(fill.get("model_context_window") or 0),
                    "ts": now,
                    "thread_id": st["thread_id"],
                },
            )
        except Exception:
            pass

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
        if not _mine(params):
            return

        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if turn.get("id") and not st["turn_id"]:
                st["turn_id"] = str(turn["id"])
            line = _codex_activity_for_event({"type": "turn.started"}, activity_state)
            if line:
                activity.emit(line)
            return

        if method in ("item/started", "item/completed"):
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if st["turn_id"] and params.get("turnId") not in (None, st["turn_id"]):
                return
            if _capture_codex_question(questions, item, complete=method == "item/completed"):
                activity.emit("Preparing Cuttle question form…", force=True)
                _finish_connection()
                return
            itype = str(item.get("type") or "")
            if itype == "userMessage":
                if method == "item/completed":
                    st["user_items"] += 1
                    text = ""
                    for part in item.get("content") or []:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = str(part.get("text") or "")
                            break
                    st["user_texts"].append(text.strip())
                    if st["user_items"] > 1:
                        activity.emit(f"steer received: {_first_line(text, 100)}", force=True)
                return
            if itype == "agentMessage" and method == "item/completed":
                text = str(item.get("text") or "").strip()
                if text and (params.get("turnId") in (None, st["turn_id"])):
                    messages.append(text)
            if method == "item/started":
                st["reasoning_buf"] = ""
                st["writing_buf"] = ""
            if itype in _SKIP_ACTIVITY_ITEMS:
                return
            event_type = "item.started" if method == "item/started" else "item.completed"
            line = _codex_activity_for_event(_exec_style_event(event_type, item), activity_state)
            if line:
                activity.emit(line)
            return

        if method in _REASONING_DELTAS:
            st["reasoning_buf"] = (st["reasoning_buf"] + str(params.get("delta") or ""))[-2000:]
            preview = _delta_preview(st["reasoning_buf"])
            if preview:
                activity.emit(f"thinking: {preview}…")
            return

        if method == "item/agentMessage/delta":
            st["writing_buf"] = (st["writing_buf"] + str(params.get("delta") or ""))[-4000:]
            preview = _delta_preview(st["writing_buf"])
            if preview:
                activity.emit(f"writing: …{preview}")
            return

        if method == "thread/tokenUsage/updated":
            tu = params.get("tokenUsage")
            if isinstance(tu, dict):
                st["token_usage"] = tu
                _note_turn_tokens(st, tu)
                _save_live_snapshot(tu)
            return

        if method == "error":
            err = params.get("error") if isinstance(params.get("error"), dict) else {}
            text = str(err.get("message") or params.get("message") or "").strip()
            if text:
                st["errors"].append(text)
            line = _codex_activity_for_event({"type": "error", "message": text}, activity_state)
            if line:
                activity.emit(line)
            return

        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if st["turn_id"] and turn.get("id") and turn.get("id") != st["turn_id"]:
                return
            st["turn_status"] = str(turn.get("status") or "unknown")
            err = turn.get("error") if isinstance(turn.get("error"), dict) else {}
            if err.get("message"):
                st["turn_error"] = str(err["message"])
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
        st["errors"].append(str(exc))
        if st["setup_error"] is None:
            st["setup_error"] = f"Codex app-server failed: {exc}"
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
        if proc.returncode is None:
            await kill_process_tree(proc)
        # Release ONLY on confirmed exit: a kill request is not proof of
        # exit, and a still-live server may still hold the vendor thread
        # writer. On unconfirmed exit the lease is RETAINED fail-closed
        # (no second writer is admitted); restarting the owning process
        # is the recovery path for that exceptional held lease.
        reaped = await _ownership.reap_confirmed(proc, timeout=_REAP_TIMEOUT_SEC)
        if st.get("own_token") is not None and st.get("own_tid"):
            if reaped:
                _ownership.release(st.get("own_tid"), st.get("own_token"))
            else:
                print(
                    f"[CODEX] thread {st.get('own_tid')} lease retained: "
                    "server exit unconfirmed; restart recovers",
                    flush=True,
                )
            st["own_token"] = None

    thread_id = st["thread_id"]
    display = "\n\n".join(messages).strip()
    echoed = list(st["user_texts"])
    undelivered: List[str] = []
    for text in st["steers_accepted"]:
        if text.strip() in echoed:
            echoed.remove(text.strip())
        else:
            undelivered.append(text)
    if undelivered:
        display = f"{display}\n\n{_undelivered_notice(undelivered)}".strip()
    # Per-turn tokens come from the accumulated per-request deltas; the
    # thread-cumulative total is only a fallback for older servers.
    usage = _usage_from_turn_tokens(st) or _usage_from_token_usage(st["token_usage"])
    token_usage = _token_usage_fill(st["token_usage"]) if st["token_usage"] else None
    base: Dict[str, Any] = {
        "usage": usage,
        "codex_session_id": thread_id,
        "token_usage": token_usage,
        "steered": st["steered"] - len(undelivered),
        "undelivered_steers": undelivered,
        "transport": "app-server",
    }

    if run is not None and (run.cancelled or run.timed_out):
        reason = run.reason or ("cancelled" if run.cancelled else f"timed out after {timeout_sec:.0f}s")
        notice = format_interrupt_notice(
            "Codex",
            reason,
            elapsed_sec=run.elapsed_sec,
            session_saved=bool(thread_id),
            resume_slash="codex",
        )
        body = f"{display}\n\n{notice}".strip() if display else notice
        return {
            **base,
            "success": False,
            "error": f"Codex CLI {reason}",
            "output": body,
            "timed_out": run.timed_out,
            "cancelled": run.cancelled,
        }

    if questions.pending:
        return {**base, "success": True, "output": questions.render(display),
                "error": None, "awaiting_input": bool(questions.questions)}

    if st["turn_id"] is None:
        stderr = (run.stderr if run is not None else b"").decode("utf-8", errors="replace").strip()
        return _fallback(st["setup_error"] or _first_line(stderr, 300) or "Codex app-server exited before the turn started")

    if st["turn_status"] == "completed":
        return {**base, "success": True, "output": display or "Done.", "error": None}

    stderr = (run.stderr if run is not None else b"").decode("utf-8", errors="replace").strip()
    err_msg = (
        st["turn_error"]
        or (st["errors"][-1] if st["errors"] else "")
        or (f"turn {st['turn_status']}" if st["turn_status"] else "")
        or _first_line(stderr, 300)
        or "Codex app-server exited mid-turn"
    )
    return {**base, "success": False, "error": err_msg[:2000], "output": display}
