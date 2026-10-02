"""
Codex CLI integration — runs OpenAI Codex non-interactively (`codex exec --json`).

Resume: ``codex exec resume <thread_id>`` using the thread id from ``thread.started``.
Auth: same as the user's Codex install (`codex login` / ChatGPT account).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    kill_process_tree,
    run_interruptible,
)

if TYPE_CHECKING:
    import queue as queue_module

# Windows CreateProcess command-line limit (~8191); keep long prompts on stdin.
_MAX_PROMPT_FOR_ARGV = 2800 if os.name == "nt" else 12000

# ``codex exec --json`` emits one JSON object per line, and tool results can
# legitimately make a single event much larger than asyncio's 64 KiB default
# StreamReader limit.  Without an explicit limit, ``readline()`` raises
# ``LimitOverrunError`` / ``ValueError: Separator is not found...`` and the
# whole chat turn fails.
_STDOUT_LINE_LIMIT = 16 * 1024 * 1024

_WRITING_BUF_KEY = "\x00writing"

# Bound for waiting out a previous thread owner at exec start; cancel-aware.
_OWNERSHIP_WAIT_SEC = 30.0

# Bound for reaping our own exec server before freeing its thread: a kill
# request is not proof of exit.
_REAP_TIMEOUT_SEC = 10.0


def codex_executable() -> Optional[str]:
    override = (os.getenv("CODEX_CLI_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    found = shutil.which("codex")
    if found:
        return found
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or ""
        candidate = os.path.join(local, "Programs", "OpenAI", "Codex", "bin", "codex.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def _default_timeout() -> float:
    raw = (os.getenv("CODEX_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(60.0, min(float(raw), 7200.0))
        except ValueError:
            pass
    return 3600.0


def _first_line(text: Any, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _preview_text(text: Any, *, limit: int = 140) -> str:
    if not isinstance(text, str):
        return ""
    return text.replace("\n", " ").strip()[:limit]


def _extract_agent_text(item: Dict[str, Any]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    if (item.get("type") or "").strip() != "agent_message":
        return None
    for key in ("text", "content", "message"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _codex_reasoning_preview(item: Dict[str, Any]) -> str:
    for key in ("text", "summary", "content", "reasoning"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return _preview_text(val)
        if isinstance(val, list):
            parts: List[str] = []
            for block in val:
                if isinstance(block, str) and block.strip():
                    parts.append(block.strip())
                elif isinstance(block, dict):
                    t = block.get("text") or block.get("content")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            if parts:
                return _preview_text(" ".join(parts))
    return ""


def _codex_file_change_label(item: Dict[str, Any]) -> str:
    paths: List[str] = []
    changes = item.get("changes")
    if isinstance(changes, list):
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            path = ch.get("path") or ch.get("file") or ch.get("filename")
            if isinstance(path, str) and path.strip():
                paths.append(Path(path.strip()).name)
    if not paths:
        for key in ("path", "file", "filename"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                paths.append(Path(raw.strip()).name)
                break
    if not paths:
        return "edit files"
    shown = ", ".join(paths[:3])
    if len(paths) > 3:
        shown += f" +{len(paths) - 3}"
    return f"edit {shown}"


def _codex_mcp_label(item: Dict[str, Any]) -> str:
    name = (
        item.get("tool")
        or item.get("name")
        or item.get("tool_name")
        or item.get("server")
        or ""
    )
    name_s = str(name).strip() if name else ""
    if name_s:
        return f"mcp {name_s}"
    return "mcp"


def _codex_web_search_label(item: Dict[str, Any]) -> str:
    q = item.get("query") or item.get("q") or item.get("text") or ""
    q_s = _first_line(q, 80) if isinstance(q, str) else ""
    return f"web search: {q_s}" if q_s else "web search"


def _codex_tool_label(item: Dict[str, Any]) -> str:
    itype = (item.get("type") or "").strip()
    if itype == "command_execution":
        cmd_s = (item.get("command") or "").strip()
        if not cmd_s:
            return "shell"
        return cmd_s[:100] + ("…" if len(cmd_s) > 100 else "")
    if itype == "file_change":
        return _codex_file_change_label(item)
    if itype == "mcp_tool_call":
        return _codex_mcp_label(item)
    if itype in ("web_search", "web_search_call"):
        return _codex_web_search_label(item)
    if itype:
        return re.sub(r"[_-]+", " ", itype).strip() or "tool"
    return "tool"


def _looks_like_codex_transport_recovery(msg: str) -> bool:
    """True for Codex reconnect / WS→HTTPS fallback noise (not a hard failure).

    Long resumed threads often hit Responses WebSocket limits (openai/codex#38846 /
    #30933): reconnect 1–5, then fall back to HTTPS. Those events are recoverable
    mid-turn; labeling them ``Codex error`` / ``tool failed`` scared users into
    thinking Stop→followup was broken (CH-000504).
    """
    low = (msg or "").strip().lower()
    if not low:
        return False
    needles = (
        "reconnecting",
        "falling back from websockets",
        "https transport",
        "stream disconnected before completion",
        "websocket closed by server before response.completed",
    )
    return any(n in low for n in needles)


def _codex_activity_for_event(
    event: Dict[str, Any],
    state: Dict[str, Any],
) -> Optional[str]:
    """Map one ``codex exec --json`` line to a Cursor-style chat status, or None.

    Mirrors Cursor Agent / Muse: ``thinking:`` / ``tool N:`` / ``writing:``.
    ``state`` carries ``tool_count`` and a writing buffer across the stream.
    """
    if not isinstance(event, dict):
        return None
    et = (event.get("type") or "").strip()
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    itype = (item.get("type") or "").strip()

    if et == "thread.started":
        model = event.get("model") or event.get("model_id")
        if isinstance(model, str) and model.strip():
            return f"Codex ready (model: {model.strip()})"
        return None

    if et == "turn.started":
        return "Codex is thinking…"

    if et in ("item.started", "item.updated"):
        if itype in ("reasoning", "thought", "thinking"):
            preview = _codex_reasoning_preview(item)
            return f"thinking: {preview}…" if preview else "Codex is thinking…"
        if itype == "agent_message":
            text = _extract_agent_text(item) or ""
            if text:
                buf = str(state.get(_WRITING_BUF_KEY) or "") + text
                state[_WRITING_BUF_KEY] = buf[-4000:]
                preview = buf.replace("\n", " ").strip()[-120:]
                return f"writing: …{preview}" if preview else None
            return None
        # Tool-like items: count once on started (not every updated).
        if et == "item.started" and itype:
            state["tool_count"] = int(state.get("tool_count") or 0) + 1
            index = state["tool_count"]
            return f"tool {index}: {_codex_tool_label(item)}"
        if et == "item.updated" and itype == "command_execution":
            # Refresh command text if Codex fills it in after start.
            cmd_s = (item.get("command") or "").strip()
            if cmd_s and state.get("tool_count"):
                return f"tool {int(state['tool_count'])}: {_codex_tool_label(item)}"
        return None

    if et == "item.completed":
        if itype == "agent_message":
            text = _extract_agent_text(item) or ""
            if text:
                state[_WRITING_BUF_KEY] = text[-4000:]
                preview = text.replace("\n", " ").strip()[-120:]
                return f"writing: …{preview}" if preview else None
            return None
        if itype in ("reasoning", "thought", "thinking"):
            preview = _codex_reasoning_preview(item)
            return f"thinking: {preview}" if preview else None
        if itype == "error":
            msg = _first_line(item.get("message") or item.get("error"), 100)
            if _looks_like_codex_transport_recovery(msg):
                return f"Codex reconnecting: {msg}" if msg else "Codex reconnecting…"
            return f"tool failed: {msg}" if msg else "tool failed"
        status = str(item.get("status") or item.get("outcome") or "").strip().lower()
        if status in ("failed", "error", "cancelled"):
            label = _codex_tool_label(item)
            if _looks_like_codex_transport_recovery(label):
                return f"Codex reconnecting: {label}"
            return f"tool failed: {label}"
        return None

    if et in ("turn.failed", "error"):
        err = event.get("error") if isinstance(event.get("error"), dict) else {}
        msg = err.get("message") if isinstance(err, dict) else None
        if not msg:
            msg = event.get("message")
        preview = _first_line(msg, 120)
        if _looks_like_codex_transport_recovery(preview or ""):
            return (
                f"Codex reconnecting: {preview}"
                if preview
                else "Codex reconnecting…"
            )
        return f"Codex error: {preview}" if preview else "Codex error"

    return None


def _parse_codex_jsonl(raw: str) -> Dict[str, Any]:
    """Parse ``codex exec --json`` stdout into display text, thread id, usage, errors."""
    thread_id: Optional[str] = None
    messages: List[str] = []
    errors: List[str] = []
    usage: Dict[str, Any] = {}
    cached_sum = 0
    cache_write_sum = 0
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        et = (ev.get("type") or "").strip()
        if et == "thread.started":
            tid = ev.get("thread_id")
            if isinstance(tid, str) and tid.strip():
                thread_id = tid.strip()
            continue
        if et == "item.completed":
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            text = _extract_agent_text(item)
            if text:
                messages.append(text)
            elif (item.get("type") or "") == "error":
                msg = item.get("message")
                if isinstance(msg, str) and msg.strip():
                    errors.append(msg.strip())
            continue
        # Per-step cache from token_usage_record (rollout / verbose streams).
        # turn.completed totals win when present — see below.
        if et == "token_usage_record":
            u = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
            info = ev.get("info") if isinstance(ev.get("info"), dict) else None
            if u is None and info:
                u = info.get("usage") if isinstance(info.get("usage"), dict) else info
            if isinstance(u, dict):
                try:
                    cr = int(
                        u.get("cached_input_tokens")
                        or u.get("cache_read_tokens")
                        or 0
                    )
                except (TypeError, ValueError):
                    cr = 0
                try:
                    cw = int(
                        u.get("cache_write_input_tokens")
                        or u.get("cache_write_tokens")
                        or 0
                    )
                except (TypeError, ValueError):
                    cw = 0
                if cr > 0:
                    cached_sum += cr
                if cw > 0:
                    cache_write_sum += cw
        if et == "turn.completed":
            u = ev.get("usage")
            if isinstance(u, dict):
                usage = dict(u)
            continue
        if et in ("turn.failed", "error"):
            err = ev.get("error") if isinstance(ev.get("error"), dict) else None
            msg = None
            if err:
                msg = err.get("message")
            if not msg:
                msg = ev.get("message")
            if isinstance(msg, str) and msg.strip():
                errors.append(msg.strip())
            continue
    # Prefer explicit totals on turn.completed; else summed intermediates.
    if usage:
        try:
            final_cached = int(
                usage.get("cached_input_tokens")
                or usage.get("cache_read_tokens")
                or 0
            )
        except (TypeError, ValueError):
            final_cached = 0
        try:
            final_write = int(
                usage.get("cache_write_input_tokens")
                or usage.get("cache_write_tokens")
                or 0
            )
        except (TypeError, ValueError):
            final_write = 0
        if final_cached <= 0 and cached_sum > 0:
            usage["cached_input_tokens"] = cached_sum
        if final_write <= 0 and cache_write_sum > 0:
            usage["cache_write_input_tokens"] = cache_write_sum
    elif cached_sum or cache_write_sum:
        usage = {}
        if cached_sum:
            usage["cached_input_tokens"] = cached_sum
        if cache_write_sum:
            usage["cache_write_input_tokens"] = cache_write_sum
    display = "\n\n".join(messages).strip()
    return {
        "thread_id": thread_id,
        "output": display,
        "errors": errors,
        "usage": usage,
    }


def usage_for_query_report(usage: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Map Codex usage dict to query-report fields."""
    pt = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
    tt = int(usage.get("total_tokens", 0) or 0)
    if tt <= 0 and (pt or ct):
        tt = pt + ct
    m = (model or "codex").strip() or "codex"
    out = {
        "input_tokens": pt,
        "output_tokens": ct,
        "total_tokens": tt,
        "model": m,
    }
    try:
        cr = int(
            usage.get("cached_input_tokens")
            or usage.get("cache_read_tokens")
            or 0
        )
    except (TypeError, ValueError):
        cr = 0
    try:
        cw = int(
            usage.get("cache_write_input_tokens")
            or usage.get("cache_write_tokens")
            or 0
        )
    except (TypeError, ValueError):
        cw = 0
    if cr > 0:
        out["cache_read_tokens"] = cr
        out["cached_input_tokens"] = cr
    if cw > 0:
        out["cache_write_tokens"] = cw
        out["cache_write_input_tokens"] = cw
    return out


class CodexCliTool:
    """Non-interactive Codex CLI runs for Cuttle slash + remote-agent backends."""

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        config_overrides: Optional[List[str]] = None,
    ):
        env_model = (os.getenv("CODEX_MODEL") or "").strip()
        self.model = (model or env_model or "").strip() or None
        # Normalized effort string mapped by callers; passed as Codex config override.
        self.reasoning_effort = (reasoning_effort or "").strip() or None
        self.config_overrides = list(config_overrides or [])

    async def execute_prompt(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        resume: Optional[str] = None,
        status_queue: Optional["queue_module.Queue"] = None,
        reasoning_effort: Optional[str] = None,
        config_overrides: Optional[List[str]] = None,
        chat_session_id: Optional[str] = None,
        cancel_event: Any = None,
    ) -> Dict[str, Any]:
        if not (prompt or "").strip():
            return {"success": False, "error": "No prompt provided", "output": ""}
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return {
                "success": False, "error": "Codex CLI cancelled", "output": "",
                "usage": {}, "cancelled": True, "timed_out": False,
                "codex_session_id": (resume or "").strip() or None,
            }
        exe = codex_executable()
        if not exe:
            return {
                "success": False,
                "error": (
                    "Codex CLI not found. Install OpenAI Codex and ensure `codex` is on PATH "
                    "(or set CODEX_CLI_PATH)."
                ),
                "output": "",
            }
        workdir = cwd or os.getcwd()
        if not os.path.isdir(workdir):
            return {"success": False, "error": f"Invalid working directory: {workdir}", "output": ""}

        timeout_sec = float(timeout) if timeout is not None else _default_timeout()
        use_stdin = len(prompt) > _MAX_PROMPT_FOR_ARGV
        last_msg_path: Optional[str] = None
        try:
            fd, last_msg_path = tempfile.mkstemp(prefix="cuttle_codex_", suffix=".txt")
            os.close(fd)

            cmd: List[str] = [exe, "exec"]
            rid = (resume or "").strip()
            if rid:
                cmd.extend(["resume", rid])
            else:
                cmd.extend(["-C", str(Path(workdir).resolve())])
            cmd.extend(
                [
                    "--json",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-o",
                    last_msg_path,
                ]
            )
            if self.model:
                cmd.extend(["-m", self.model])
            # Reasoning: Codex config key ``model_reasoning_effort``. The
            # adapter validates each requested level against this model's catalog.
            effort = (reasoning_effort or self.reasoning_effort or "").strip()
            overrides = list(config_overrides or self.config_overrides or [])
            if effort:
                # TOML string value via -c key=value
                overrides.append(f'model_reasoning_effort="{effort}"')
            for ov in overrides:
                ov_s = str(ov).strip()
                if ov_s:
                    cmd.extend(["-c", ov_s])
            if use_stdin:
                cmd.append("-")
            else:
                cmd.append(prompt)

            env = os.environ.copy()
            # Cursor-style strip: event lines + silent contextual heartbeat.
            # Do not also heartbeat from the adapter — that stomps tool lines.
            from api.agent_harness.activity import ActivityEmitter

            activity = ActivityEmitter(status_queue, agent_label="Codex")
            activity.emit(
                "Resuming Codex…" if rid else "Starting Codex…",
                force=True,
            )
            activity_state: Dict[str, Any] = {"tool_count": 0}
            stop_hb = asyncio.Event()
            hb_task = None

            from api.agent_harness import codex_thread_ownership as _ownership

            # Own the resume thread BEFORE spawning: a probe, compaction, or
            # another turn must never write this thread concurrently. The
            # wait is bounded and cancel-aware; the loop only sleeps. Busy
            # carries no ``fallback`` key — exec already is the fallback, so
            # a busy error stays terminal instead of retry-looping.
            exec_token: Optional[int] = None
            exec_tid: Optional[str] = rid or None
            exec_conflict = False
            proc = None
            if rid:
                exec_token = await _ownership.await_acquire(
                    rid,
                    "codex-exec",
                    timeout=_OWNERSHIP_WAIT_SEC,
                    cancel_event=cancel_event,
                )
                if exec_token is None:
                    if cancel_event is not None and getattr(
                        cancel_event, "is_set", lambda: False
                    )():
                        return {
                            "success": False,
                            "error": "Codex CLI cancelled",
                            "output": "",
                            "usage": {},
                            "codex_session_id": rid,
                            "timed_out": False,
                            "cancelled": True,
                        }
                    return {
                        "success": False,
                        "error": (
                            "Codex thread busy (owned by "
                            f"{_ownership.owner_of(rid) or 'another writer'})"
                        ),
                        "output": "",
                        "usage": {},
                        "codex_session_id": rid,
                    }

            try:
                hb_task = (
                    asyncio.create_task(activity.heartbeat_loop(stop_hb))
                    if status_queue is not None
                    else None
                )
                seen_thread: List[Optional[str]] = [None]
                out_b = b""
                err_b = b""
                run = None
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL,
                    cwd=workdir,
                    env=env,
                    limit=_STDOUT_LINE_LIMIT,
                )
                # Register so Stop can kill this run, and so the busy lock / restart
                # drain check can see it. Without this a live Codex run looks idle.
                attach_to_chat_run(chat_session_id, proc)

                if use_stdin and proc.stdin is not None:
                    proc.stdin.write(prompt.encode("utf-8"))
                    await proc.stdin.drain()
                    proc.stdin.close()

                def _on_line(line: bytes) -> None:
                    nonlocal exec_token, exec_tid, exec_conflict
                    try:
                        ev = json.loads(line.decode("utf-8", errors="replace").strip())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        return
                    if not isinstance(ev, dict):
                        return
                    tid = ev.get("thread_id")
                    if isinstance(tid, str) and tid.strip():
                        # Fresh threads learn their id mid-run: claim it so a
                        # later probe skips instead of racing this writer. A
                        # fresh id is unique, so losing here means genuine
                        # cross-talk — flag it and fail closed after the run.
                        if exec_tid is None and not exec_conflict:
                            tok = _ownership.try_acquire(tid.strip(), "codex-exec")
                            if tok is None:
                                exec_conflict = True
                            else:
                                exec_tid, exec_token = tid.strip(), tok
                        seen_thread[0] = tid.strip()
                        if chat_session_id and exec_token is not None and exec_tid == tid.strip():
                            try:
                                from scripts.utilities.codex_cli_session_store import (
                                    save_codex_resume_id,
                                )

                                save_codex_resume_id(
                                    workdir, chat_session_id, seen_thread[0]
                                )
                            except Exception:
                                pass
                    line_status = _codex_activity_for_event(ev, activity_state)
                    if line_status:
                        activity.emit(line_status)

                run = await run_interruptible(
                    proc,
                    timeout=timeout_sec,
                    cancel_event=cancel_event,
                    on_stdout_line=_on_line,
                    line_mode=True,
                )
                out_b = run.stdout
                err_b = run.stderr
                if exec_conflict:
                    return {
                        "success": False,
                        "error": (
                            "Codex thread busy "
                            "(another writer took the thread mid-run)"
                        ),
                        "output": "",
                        "usage": {},
                        "codex_session_id": exec_tid,
                    }
            finally:
                stop_hb.set()
                if hb_task is not None:
                    try:
                        await asyncio.wait_for(hb_task, timeout=1.0)
                    except Exception:
                        hb_task.cancel()
                # Release ONLY on confirmed exit: a kill request is not
                # proof of exit. On unconfirmed exit the lease is RETAINED
                # fail-closed; restarting the owning process recovers it.
                if proc is not None and proc.returncode is None:
                    await kill_process_tree(proc)
                reaped = await _ownership.reap_confirmed(
                    proc, timeout=_REAP_TIMEOUT_SEC
                )
                if exec_token is not None and exec_tid:
                    if reaped:
                        _ownership.release(exec_tid, exec_token)
                    else:
                        print(
                            f"[CODEX] thread {exec_tid} lease retained: "
                            "server exit unconfirmed; restart recovers",
                            flush=True,
                        )
                    exec_token = None

            out = out_b.decode("utf-8", errors="replace") if isinstance(out_b, (bytes, bytearray)) else ""
            err = err_b.decode("utf-8", errors="replace") if isinstance(err_b, (bytes, bytearray)) else ""
            parsed = _parse_codex_jsonl(out)
            display = (parsed.get("output") or "").strip()
            if not display and last_msg_path and os.path.isfile(last_msg_path):
                try:
                    display = Path(last_msg_path).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass
            thread_id = parsed.get("thread_id") or (seen_thread[0] if seen_thread else None)
            usage = parsed.get("usage") or {}
            errors = list(parsed.get("errors") or [])
            noise = "Reading additional input from stdin"
            clean_err = "\n".join(
                ln for ln in (err or "").splitlines() if noise not in ln
            ).strip()

            if run is not None and (run.timed_out or run.cancelled):
                reason = run.reason or (
                    "cancelled" if run.cancelled else f"timed out after {timeout_sec:.0f}s"
                )
                if chat_session_id and thread_id:
                    try:
                        from scripts.utilities.codex_cli_session_store import (
                            save_codex_resume_id,
                        )

                        save_codex_resume_id(workdir, chat_session_id, thread_id)
                    except Exception:
                        pass
                notice = format_interrupt_notice(
                    "Codex",
                    reason,
                    elapsed_sec=run.elapsed_sec,
                    session_saved=bool(thread_id),
                    resume_slash="codex",
                )
                body = f"{display}\n\n{notice}".strip() if display else notice
                return {
                    "success": False,
                    "error": f"Codex CLI {reason}",
                    "output": body,
                    "usage": usage,
                    "codex_session_id": thread_id,
                    "timed_out": run.timed_out,
                    "cancelled": run.cancelled,
                }

            rc = run.returncode if run is not None else None
            if rc == 0 and (display or not errors):
                return {
                    "success": True,
                    "output": display or "Done.",
                    "error": None,
                    "usage": usage,
                    "codex_session_id": thread_id,
                }

            err_msg = (errors[-1] if errors else "") or clean_err or f"exit {rc}"
            merged = display
            if clean_err and clean_err not in (merged or ""):
                merged = (merged + "\n\n" + clean_err).strip() if merged else clean_err
            return {
                "success": False,
                "error": err_msg[:2000],
                "output": merged or "",
                "usage": usage,
                "codex_session_id": thread_id,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "output": ""}
        finally:
            if last_msg_path:
                try:
                    os.unlink(last_msg_path)
                except OSError:
                    pass
