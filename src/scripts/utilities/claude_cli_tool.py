"""
Claude Code CLI integration — non-interactive ``claude -p`` with JSON output.

Runs against the real project cwd (no sandbox mirror). Resume uses
``--resume <session_id>`` from ``claude_cli_session_store``.
Auth: the user's native Claude login/config; host API credentials are isolated.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.agent_cli_env import agent_cli_env
from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    run_interruptible,
)

if TYPE_CHECKING:
    import queue as queue_module

_MAX_PROMPT_FOR_ARGV = 2800 if os.name == "nt" else 12000
_STDOUT_LIMIT = 16 * 1024 * 1024


def claude_executable() -> Optional[str]:
    override = (os.getenv("CLAUDE_CLI_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    found = shutil.which("claude")
    if found:
        return found
    if os.name == "nt":
        # Official native Windows install (user-local).
        local = Path.home() / ".local" / "bin" / "claude.exe"
        if local.is_file():
            return str(local)
        # npm global shim
        appdata = os.environ.get("APPDATA") or ""
        npm = Path(appdata) / "npm" / "claude.cmd"
        if npm.is_file():
            return str(npm)
    return None


def _default_timeout() -> float:
    raw = (os.getenv("CLAUDE_TIMEOUT_SEC") or "").strip()
    try:
        return max(30.0, float(raw)) if raw else 3600.0
    except (TypeError, ValueError):
        return 3600.0


def _status_put(status_queue: Optional["queue_module.Queue"], message: str) -> None:
    if not status_queue:
        return
    try:
        status_queue.put(("status", message))
    except Exception:
        pass


def _parse_claude_json(raw: str) -> Dict[str, Any]:
    """Parse ``claude -p --output-format json`` stdout into text + usage + session."""
    text = (raw or "").strip()
    if not text:
        return {"output": "", "session_id": None, "usage": {}, "errors": []}
    # Prefer last JSON object (CLI may print logs before the result).
    obj: Optional[Dict[str, Any]] = None
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                obj = parsed
        except json.JSONDecodeError:
            obj = None
    if obj is None:
        for line in reversed(text.splitlines()):
            s = line.strip()
            if not s.startswith("{"):
                continue
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                obj = parsed
                break
    if not isinstance(obj, dict):
        return {"output": text, "session_id": None, "usage": {}, "errors": []}

    session_id = None
    for key in ("session_id", "sessionId", "conversation_id"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            session_id = val.strip()
            break

    output = ""
    for key in ("result", "response", "text", "message", "output"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            output = val.strip()
            break

    usage: Dict[str, Any] = {}
    raw_usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    if raw_usage:
        try:
            usage["prompt_tokens"] = int(
                raw_usage.get("input_tokens")
                or raw_usage.get("prompt_tokens")
                or 0
            )
        except (TypeError, ValueError):
            usage["prompt_tokens"] = 0
        try:
            usage["completion_tokens"] = int(
                raw_usage.get("output_tokens")
                or raw_usage.get("completion_tokens")
                or 0
            )
        except (TypeError, ValueError):
            usage["completion_tokens"] = 0
        if usage.get("prompt_tokens"):
            usage["context_tokens"] = int(usage["prompt_tokens"])
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        if pt or ct:
            usage["total_tokens"] = pt + ct
        # Anthropic prompt-cache fields (exclusive of input_tokens).
        try:
            cr = int(
                raw_usage.get("cache_read_input_tokens")
                or raw_usage.get("cache_read_tokens")
                or raw_usage.get("cacheReadTokens")
                or 0
            )
        except (TypeError, ValueError):
            cr = 0
        try:
            cw = int(
                raw_usage.get("cache_creation_input_tokens")
                or raw_usage.get("cache_write_tokens")
                or raw_usage.get("cacheWriteTokens")
                or 0
            )
        except (TypeError, ValueError):
            cw = 0
        if cr > 0:
            usage["cache_read_tokens"] = cr
        if cw > 0:
            usage["cache_write_tokens"] = cw

    # modelUsage rollup (multi-model turns).
    model_usage = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
    if model_usage and not usage.get("prompt_tokens"):
        inn = out = 0
        cache_r = cache_w = 0
        for row in model_usage.values():
            if not isinstance(row, dict):
                continue
            try:
                inn += int(row.get("inputTokens") or row.get("input_tokens") or 0)
                out += int(row.get("outputTokens") or row.get("output_tokens") or 0)
            except (TypeError, ValueError):
                continue
            try:
                cache_r += int(
                    row.get("cacheReadInputTokens")
                    or row.get("cache_read_input_tokens")
                    or row.get("cacheReadTokens")
                    or 0
                )
            except (TypeError, ValueError):
                pass
            try:
                cache_w += int(
                    row.get("cacheCreationInputTokens")
                    or row.get("cache_creation_input_tokens")
                    or row.get("cacheWriteTokens")
                    or 0
                )
            except (TypeError, ValueError):
                pass
        if inn or out:
            usage["prompt_tokens"] = inn
            usage["completion_tokens"] = out
            usage["context_tokens"] = inn
            usage["total_tokens"] = inn + out
        if cache_r:
            usage["cache_read_tokens"] = cache_r
        if cache_w:
            usage["cache_write_tokens"] = cache_w

    cost = obj.get("total_cost_usd")
    if cost is not None:
        try:
            usage["cost"] = float(cost)
        except (TypeError, ValueError):
            pass

    errors: List[str] = []
    subtype = str(obj.get("subtype") or "").lower()
    is_error = subtype in ("error", "failure", "failed") or bool(obj.get("is_error"))
    if is_error:
        err = obj.get("error") or obj.get("result") or obj.get("message")
        if isinstance(err, str) and err.strip():
            errors.append(err.strip())
        elif isinstance(err, dict) and err.get("message"):
            errors.append(str(err.get("message")))

    from api.agent_harness.questions import QuestionBridge

    questions = QuestionBridge()
    for denied in obj.get("permission_denials") or []:
        if isinstance(denied, dict) and denied.get("tool_name") == "AskUserQuestion":
            questions.capture(denied.get("tool_input"))
    return {
        "output": questions.render(output or (text if not is_error else "")),
        "session_id": session_id,
        "usage": usage,
        "errors": errors,
        "raw": obj,
    }


def usage_for_query_report(usage: Dict[str, Any], model: str) -> Dict[str, Any]:
    pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    tt = int(usage.get("total_tokens") or 0) or (pt + ct)
    out = {
        "input_tokens": pt,
        "output_tokens": ct,
        "total_tokens": tt,
        "model": (model or "claude").strip() or "claude",
        "cost": usage.get("cost"),
    }
    try:
        cr = int(usage.get("cache_read_tokens") or 0)
    except (TypeError, ValueError):
        cr = 0
    try:
        cw = int(usage.get("cache_write_tokens") or 0)
    except (TypeError, ValueError):
        cw = 0
    if cr > 0:
        out["cache_read_tokens"] = cr
    if cw > 0:
        out["cache_write_tokens"] = cw
    return out


class ClaudeCliTool:
    """Non-interactive Claude Code runs for Cuttle slash + harness backends."""

    def __init__(self, model: Optional[str] = None, reasoning_effort: Optional[str] = None):
        self.reasoning_effort = (reasoning_effort or "").strip().lower() or None
        env_model = (os.getenv("CLAUDE_MODEL") or "").strip()
        self.model = (model or env_model or "").strip() or None

    @property
    def available(self) -> bool:
        return bool(claude_executable())

    async def execute_prompt(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        resume: Optional[str] = None,
        status_queue: Optional["queue_module.Queue"] = None,
        chat_session_id: Optional[str] = None,
        timeout: Optional[float] = None,
        permission_mode: str = "bypassPermissions",
        cancel_event: Any = None,
    ) -> Dict[str, Any]:
        if not (prompt or "").strip():
            return {"success": False, "error": "No prompt provided", "output": ""}

        exe = claude_executable()
        if not exe:
            return {
                "success": False,
                "error": (
                    "Claude Code CLI not found. Install `@anthropic-ai/claude-code` "
                    "or set CLAUDE_CLI_PATH."
                ),
                "output": "",
            }

        workdir = (cwd or "").strip() or os.getcwd()
        if not os.path.isdir(workdir):
            return {
                "success": False,
                "error": f"Invalid working directory: {workdir}",
                "output": "",
            }

        resolved_timeout = (
            float(timeout) if timeout is not None else _default_timeout()
        )
        cmd: List[str] = [
            exe,
            "-p",
            "--output-format",
            "json",
            "--disallowedTools",
            "AskUserQuestion",
            "--permission-mode",
            permission_mode or "bypassPermissions",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.reasoning_effort:
            cmd.extend(["--effort", self.reasoning_effort])
        rid = (resume or "").strip()
        if rid:
            cmd.extend(["--resume", rid])

        # Long prompts on stdin to avoid Windows argv limits.
        use_stdin = len(prompt) > _MAX_PROMPT_FOR_ARGV
        if use_stdin:
            cmd.append("-")
        else:
            cmd.append(prompt)

        _status_put(status_queue, "Calling Claude Code…")
        env = agent_cli_env()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=env,
                limit=_STDOUT_LIMIT,
            )
            attach_to_chat_run(chat_session_id, proc)
            if use_stdin and proc.stdin is not None:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            run = await run_interruptible(
                proc,
                timeout=resolved_timeout,
                cancel_event=cancel_event,
                line_mode=False,
            )
        except OSError as exc:
            return {
                "success": False,
                "error": f"Failed to spawn Claude Code: {exc}",
                "output": "",
                "usage": {},
            }

        out = run.stdout.decode("utf-8", errors="replace")
        err = run.stderr.decode("utf-8", errors="replace").strip()
        parsed = _parse_claude_json(out)
        session_id = parsed.get("session_id") or rid or None
        usage = parsed.get("usage") or {}
        errors = list(parsed.get("errors") or [])
        display = (parsed.get("output") or "").strip()
        if not display and out.strip() and not parsed.get("session_id"):
            # Incomplete JSON mid-interrupt — still surface raw text.
            display = out.strip()

        def _persist(sid: Optional[str]) -> None:
            if not (chat_session_id and sid):
                return
            try:
                from scripts.utilities.claude_cli_session_store import (
                    save_claude_resume_id,
                )

                save_claude_resume_id(workdir, chat_session_id, sid)
            except Exception:
                pass

        if run.timed_out or run.cancelled:
            reason = run.reason or (
                "cancelled" if run.cancelled else f"timed out after {resolved_timeout:.0f}s"
            )
            _persist(session_id)
            notice = format_interrupt_notice(
                "Claude Code",
                reason,
                elapsed_sec=run.elapsed_sec,
                session_saved=bool(session_id),
                resume_slash="claude",
            )
            body = f"{display}\n\n{notice}".strip() if display else notice
            if err and not display:
                body = f"```\n{err[:2000]}\n```\n\n{notice}"
            return {
                "success": False,
                "error": f"Claude Code {reason}",
                "output": body,
                "usage": usage,
                "claude_session_id": session_id,
                "timed_out": run.timed_out,
                "cancelled": run.cancelled,
            }

        ok = run.returncode == 0 and not errors
        if not ok and not errors and err:
            errors.append(err[:2000])
        if not ok and not display and err:
            display = err

        if ok and session_id:
            _persist(session_id)
        elif not ok and session_id:
            # Keep resume across soft failures too.
            _persist(session_id)

        return {
            "success": ok,
            "output": display,
            "error": (errors[0] if errors else None) if not ok else None,
            "usage": usage,
            "claude_session_id": session_id,
            "returncode": run.returncode,
            "stderr": err[:2000] if err else "",
        }


# --- Back-compat aliases for older call sites (pipeline / bundled tools) ---


class ClaudeCodeTool:
    """Thin wrapper kept for pipeline / bundled-tool call sites."""

    def __init__(
        self,
        wsl_enabled: bool = False,  # ignored — native PATH only
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._tool = ClaudeCliTool(model=model)
        self.session_id = session_id
        self.model = model
        self.wsl_enabled = False
        self.claude_code_available = self._tool.available

    def _resolve_project_path(
        self, project_hint: str = None, mirror_from: str = None
    ) -> str:
        """Return an absolute cwd; absolute hints win, else process cwd."""
        hint = (project_hint or "").strip()
        if hint and os.path.isdir(hint):
            return str(Path(hint).resolve())
        return os.getcwd()

    async def execute_claude_command(
        self, prompt: str, project_hint: str = None, mirror_from: str = None
    ) -> Dict[str, Any]:
        cwd = self._resolve_project_path(project_hint, mirror_from)
        resume = None
        if self.session_id:
            try:
                from scripts.utilities.claude_cli_session_store import (
                    load_claude_resume_id,
                )

                resume = load_claude_resume_id(cwd, self.session_id)
            except Exception:
                resume = None
        raw = await self._tool.execute_prompt(
            prompt,
            cwd=cwd,
            resume=resume,
            chat_session_id=self.session_id,
        )
        # Shape expected by older pipeline nodes.
        usage = raw.get("usage") or {}
        return {
            "success": bool(raw.get("success")),
            "output": raw.get("output") or "",
            "error": raw.get("error"),
            "usage_info": {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "cost": usage.get("cost") or 0,
            },
            "claude_session_id": raw.get("claude_session_id"),
        }


def get_claude_code_tool(session_id: str = None) -> ClaudeCodeTool:
    return ClaudeCodeTool(session_id=session_id)


async def send_prompt_to_claude_code(
    prompt: str,
    project_hint: str = None,
    mirror_from: str = None,
    session_id: str = None,
) -> str:
    tool = ClaudeCodeTool(session_id=session_id)
    result = await tool.execute_claude_command(prompt, project_hint)
    if result.get("success"):
        return str(result.get("output") or "")
    return f"[FAIL] {result.get('error') or 'Claude Code failed'}"


def send_prompt_to_claude_code_sync(prompt: str, project_hint: str = None) -> str:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(
                        send_prompt_to_claude_code(prompt, project_hint)
                    )
                ).result(timeout=1900)
        return loop.run_until_complete(
            send_prompt_to_claude_code(prompt, project_hint)
        )
    except RuntimeError:
        return asyncio.run(send_prompt_to_claude_code(prompt, project_hint))
