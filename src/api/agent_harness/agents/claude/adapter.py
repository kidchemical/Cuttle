"""Claude Code CLI harness adapter — native ``claude -p`` with per-chat resume."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from api.agent_harness.activity import heartbeat_status, put_status
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult
from scripts.utilities.claude_cli_tool import ClaudeCliTool, claude_executable


def _handle_claude_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.claude_cli_session_store import (
        load_claude_model,
        save_claude_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'")
    if not match.group(1) and arg and arg.lower() not in (
        "haiku",
        "sonnet",
        "opus",
        "default",
        "reset",
        "clear",
        "list",
        "ls",
        "?",
    ) and not arg.lower().startswith("claude-"):
        # Likely a task ("model the auth flow"), not a pin.
        return None

    if not arg or arg.lower() in ("list", "ls", "?"):
        pinned = load_claude_model(chat_session_id)
        lines = [
            "**Claude Code models** (pin with `/claude model <id>`):",
            "",
            "- `haiku` — fast / cheap",
            "- `sonnet` — balanced default",
            "- `opus` — strongest",
            "- or a full id like `claude-sonnet-4-6`",
            "",
        ]
        if pinned:
            lines.append(f"Pinned for this chat: `{pinned}`.")
        else:
            lines.append(
                f"No pin — using `{active_model or 'CLI default'}`."
            )
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        save_claude_model(chat_session_id, None)
        return AgentResult(
            success=True,
            output="**Claude Code:** Cleared model pin — next turn uses the CLI default.",
            model=active_model,
        )

    save_claude_model(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**Claude Code:** Pinned model `{arg}` for this chat.",
        model=arg,
        meta={"agent_model": arg},
    )


class Adapter:
    def available(self) -> bool:
        return bool(claude_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        from scripts.utilities.claude_cli_session_store import load_claude_resume_id

        return load_claude_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        from scripts.utilities.claude_cli_session_store import save_claude_resume_id

        save_claude_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        from scripts.utilities.claude_cli_session_store import clear_claude_resume_id

        clear_claude_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        cwd: str,
        chat_session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[AgentResult]:
        from scripts.utilities.claude_cli_session_store import load_claude_model
        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("claude", prompt)
        if usage_md is not None:
            active = (
                load_claude_model(chat_session_id)
                or (str(model).strip() if model else "")
                or ""
            )
            return AgentResult(success=True, output=usage_md, model=active or "")

        active = (
            load_claude_model(chat_session_id)
            or (str(model).strip() if model else "")
            or ""
        )
        return _handle_claude_model_slash(prompt, chat_session_id, active)

    async def execute(
        self,
        prompt: str,
        *,
        cwd: str,
        resume: Optional[str],
        model: Optional[str],
        status_queue: Any = None,
        chat_session_id: Optional[str] = None,
        timeout: float = 3600.0,
        cancel_event: Any = None,
    ) -> AgentResult:
        from scripts.utilities.claude_cli_session_store import load_claude_model
        from scripts.utilities.claude_cli_tool import usage_for_query_report

        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_SESSION,
            SOURCE_STARRED,
            badge_meta,
            get_starred_model,
        )

        session_model = load_claude_model(chat_session_id)
        _override = (str(model).strip() if model else "") or None
        _starred = get_starred_model("claude")
        mid = session_model or _override or _starred or None
        if session_model:
            _model_source = SOURCE_SESSION
        elif _override:
            _model_source = SOURCE_OVERRIDE
        elif _starred:
            _model_source = SOURCE_STARRED
        else:
            _model_source = SOURCE_CLI_DEFAULT

        put_status(status_queue, "Calling Claude Code…")
        # No mid-run NDJSON — coarse tick only (Cursor-style agents stream instead).
        stop = asyncio.Event()
        hb = asyncio.create_task(
            heartbeat_status(
                status_queue,
                label="Claude Code working",
                interval=15.0,
                stop_event=stop,
                last_activity="running",
            )
        )
        try:
            tool = ClaudeCliTool(model=mid)
            raw = await tool.execute_prompt(
                prompt,
                cwd=cwd,
                resume=resume,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                cancel_event=cancel_event,
            )
        finally:
            stop.set()
            try:
                await asyncio.wait_for(hb, timeout=1.0)
            except Exception:
                hb.cancel()

        ok = bool(raw.get("success"))
        usage_raw = raw.get("usage") or {}
        uq = usage_for_query_report(usage_raw, mid or "claude")
        usage = {
            "prompt_tokens": int(uq.get("input_tokens") or 0),
            "completion_tokens": int(uq.get("output_tokens") or 0),
            "total_tokens": int(uq.get("total_tokens") or 0),
            "model": mid or "",
        }
        if usage_raw.get("context_tokens"):
            try:
                usage["context_tokens"] = int(usage_raw.get("context_tokens") or 0)
            except (TypeError, ValueError):
                pass
        if uq.get("cache_read_tokens"):
            usage["cache_read_tokens"] = int(uq.get("cache_read_tokens") or 0)
        if uq.get("cache_write_tokens"):
            usage["cache_write_tokens"] = int(uq.get("cache_write_tokens") or 0)
        if uq.get("cost") is not None:
            try:
                usage["cost"] = float(uq["cost"])
            except (TypeError, ValueError):
                pass

        meta = badge_meta("claude", mid or "", _model_source)
        sid = raw.get("claude_session_id")
        # Prefer last-call occupancy for the gauge (not multi-step billing).
        if not usage.get("context_tokens") and usage.get("prompt_tokens"):
            try:
                from scripts.utilities.cursor_cli_tool import cursor_usage_looks_aggregated

                if not cursor_usage_looks_aggregated(usage):
                    usage["context_tokens"] = int(usage["prompt_tokens"])
            except Exception:
                usage["context_tokens"] = int(usage.get("prompt_tokens") or 0)
        if ok and chat_session_id and usage.get("context_tokens"):
            try:
                from scripts.utilities.claude_cli_session_store import (
                    save_claude_context_snapshot,
                )
                import time as _time

                save_claude_context_snapshot(
                    chat_session_id,
                    {
                        "context_tokens": int(usage["context_tokens"]),
                        "ts": _time.time(),
                        "session_id": str(sid).strip() if sid else None,
                    },
                )
            except Exception:
                pass
        err = None if ok else (raw.get("error") or "Claude Code failed")
        display = (raw.get("output") or "").strip()
        if ok and not display:
            display = "Done."
        if not ok and not display:
            display = str(err or "")
        return AgentResult(
            success=ok,
            output=display,
            error=err,
            usage=usage,
            session_id=str(sid).strip() if sid else None,
            model=mid or "",
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
