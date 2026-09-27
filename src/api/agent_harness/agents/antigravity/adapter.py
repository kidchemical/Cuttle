"""Google Antigravity CLI adapter using documented headless JSON mode."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from api.agent_harness.activity import heartbeat_status, put_status
from api.agent_harness.agents.antigravity.session_store import (
    clear_antigravity_resume_id,
    load_antigravity_resume_id,
    save_antigravity_resume_id,
)
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult
from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    run_interruptible,
)


def antigravity_executable() -> Optional[str]:
    configured = (os.environ.get("AGY_CLI_PATH") or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("agy")
    if found:
        return found
    candidates = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "agy" / "bin" / "agy.exe")
    candidates.append(Path.home() / ".local" / "bin" / "agy")
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def summarize_antigravity_error(raw: str, returncode: Optional[int] = None) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "authentication required" in low or "not authenticated" in low or "sign in" in low:
        return (
            "Antigravity authentication is required. Run `agy` once interactively to sign in, "
            "or configure its Gemini API-key provider, then retry."
        )
    if "gemini_api_key" in low and ("missing" in low or "not set" in low):
        return (
            "Antigravity is configured for Gemini API-key auth but GEMINI_API_KEY is not set. "
            "Set it for the Cuttle daemon or remove the `modelProvider` override."
        )
    if "quota" in low or "credits" in low and ("exhaust" in low or "insufficient" in low):
        return "Antigravity quota or credits are exhausted. Check `agy -p \"/usage\"` and retry later."
    if "permission" in low and ("denied" in low or "approval" in low):
        return (
            "Antigravity could not obtain a headless tool permission. Allow the specific action "
            "in Antigravity settings, or run a less privileged task."
        )
    compact = re.sub(r"\s+", " ", text)
    if compact:
        return compact[:2000]
    return f"Antigravity CLI failed (exit {returncode})." if returncode is not None else "Antigravity CLI failed."


def normalize_antigravity_usage(raw: Any) -> Dict[str, Any]:
    """Map Antigravity JSON ``usage`` blobs to Cuttle prompt/completion keys."""
    if not isinstance(raw, dict) or not raw:
        return {}

    def _pick(*keys: str) -> int:
        for k in keys:
            if raw.get(k) is None:
                continue
            try:
                return max(0, int(raw.get(k) or 0))
            except (TypeError, ValueError):
                continue
        return 0

    prompt = _pick(
        "prompt_tokens",
        "input_tokens",
        "promptTokenCount",
        "inputTokenCount",
        "promptTokens",
        "inputTokens",
    )
    completion = _pick(
        "completion_tokens",
        "output_tokens",
        "candidatesTokenCount",
        "outputTokenCount",
        "completionTokens",
        "outputTokens",
    )
    total = _pick("total_tokens", "totalTokenCount", "totalTokens")
    if total <= 0 and (prompt or completion):
        total = prompt + completion
    cache_read = _pick(
        "cache_read_tokens",
        "cacheReadTokens",
        "cachedContentTokenCount",
        "cached_content_token_count",
    )
    cache_write = _pick(
        "cache_write_tokens",
        "cacheWriteTokens",
    )
    out: Dict[str, Any] = {}
    if prompt:
        out["prompt_tokens"] = prompt
        # Only stamp context when this does not look like multi-step billing.
        try:
            from scripts.utilities.cursor_cli_tool import cursor_usage_looks_aggregated

            probe = {
                "prompt_tokens": prompt,
                "cache_read_tokens": cache_read,
            }
            if not cursor_usage_looks_aggregated(probe):
                out["context_tokens"] = prompt
        except Exception:
            out["context_tokens"] = prompt
    if completion:
        out["completion_tokens"] = completion
    if total:
        out["total_tokens"] = total
    if cache_read:
        out["cache_read_tokens"] = cache_read
    if cache_write:
        out["cache_write_tokens"] = cache_write
    return out


async def _authentication_preflight(exe: str, cwd: str) -> Optional[str]:
    """Use a fast read-only command so headless OAuth never waits for browser input."""
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except (OSError, asyncio.TimeoutError):
        return None
    if proc.returncode == 0:
        return None
    raw = (
        stdout.decode("utf-8", errors="replace")
        + "\n"
        + stderr.decode("utf-8", errors="replace")
    ).strip()
    low = raw.lower()
    if "sign in" in low or "authentication required" in low or "not authenticated" in low:
        return summarize_antigravity_error(raw, proc.returncode)
    return None


class Adapter:
    def available(self) -> bool:
        return bool(antigravity_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        return load_antigravity_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        save_antigravity_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        clear_antigravity_resume_id(cwd, chat_session_id)

    async def execute(
        self,
        prompt: str,
        *,
        cwd: str,
        resume: Optional[str],
        model: Optional[str],
        status_queue: Any = None,
        chat_session_id: Optional[str] = None,
        timeout: float = 600.0,
        cancel_event: Any = None,
    ) -> AgentResult:
        exe = antigravity_executable()
        if not exe:
            return AgentResult(success=False, error="Antigravity CLI not found", output="")
        if not (prompt or "").strip():
            return AgentResult(success=False, error="No prompt provided", output="")
        auth_error = await _authentication_preflight(exe, cwd)
        if auth_error:
            return AgentResult(success=False, error=auth_error, output="", model=model or "")

        cmd = [
            exe,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--print-timeout",
            f"{max(1, math.ceil(timeout))}s",
        ]
        if resume:
            cmd.extend(["--conversation", str(resume).strip()])
        if model:
            cmd.extend(["--model", str(model).strip()])

        put_status(status_queue, "Calling Antigravity CLI…")
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            heartbeat_status(
                status_queue,
                label="Antigravity working",
                interval=15.0,
                stop_event=stop,
                last_activity="running",
            )
        )
        proc = None
        run = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=os.environ.copy(),
            )
            attach_to_chat_run(chat_session_id, proc)
            run = await run_interruptible(
                proc, timeout=timeout, cancel_event=cancel_event, line_mode=False
            )
        except OSError as exc:
            return AgentResult(
                success=False,
                error=summarize_antigravity_error(str(exc)),
                model=model or "",
            )
        finally:
            stop.set()
            try:
                await asyncio.wait_for(heartbeat, timeout=1.0)
            except Exception:
                heartbeat.cancel()

        if run is None:
            return AgentResult(
                success=False,
                error="Antigravity CLI failed to start",
                model=model or "",
            )

        out = run.stdout.decode("utf-8", errors="replace").strip()
        err = run.stderr.decode("utf-8", errors="replace").strip()
        payload: Dict[str, Any] = {}
        try:
            parsed = json.loads(out) if out else {}
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

        status = str(payload.get("status") or "").upper()
        response = str(payload.get("response") or (out if not payload else "")).strip()
        conversation_id = payload.get("conversation_id")
        sid = str(conversation_id).strip() if conversation_id else (str(resume).strip() if resume else None)
        usage = normalize_antigravity_usage(payload.get("usage"))

        if run.timed_out or run.cancelled:
            reason = run.reason or (
                "cancelled" if run.cancelled else f"timed out after {timeout:.0f}s"
            )
            notice = format_interrupt_notice(
                "Antigravity",
                f"Antigravity {reason}" if "Antigravity" not in reason else reason,
                elapsed_sec=run.elapsed_sec,
                session_saved=bool(sid),
                resume_slash="antigravity",
            )
            body = f"{response}\n\n{notice}".strip() if response else notice
            if chat_session_id and sid:
                try:
                    save_antigravity_resume_id(cwd, chat_session_id, sid)
                except Exception:
                    pass
            return AgentResult(
                success=False,
                output=body,
                error=f"Antigravity {reason}",
                usage=usage,
                session_id=sid,
                model=model or "",
                meta={"status": status or "INTERRUPTED", "timed_out": run.timed_out, "cancelled": run.cancelled},
            )

        ok = (run.returncode == 0) and (not status or status == "SUCCESS")
        if not ok:
            raw_error = str(
                payload.get("error") or err or response or f"exit {run.returncode}"
            )
            return AgentResult(
                success=False,
                output=response,
                error=summarize_antigravity_error(raw_error, run.returncode),
                usage=usage,
                session_id=sid,
                model=model or "",
                meta={"status": status},
            )
        return AgentResult(
            success=True,
            output=response or "Done.",
            usage=usage,
            session_id=sid,
            model=model or "",
            meta={"status": status or "SUCCESS"},
        )


def build_adapter() -> Adapter:
    return Adapter()
