"""Cursor Agent CLI harness adapter.

Wraps ``scripts.utilities.cursor_cli_tool`` (stream-json, multi-segment continue,
resume). Meta slash commands (``/model``, ``/plan``, …) stay in
``api.cursor_agent_commands`` and are answered here without starting a CLI turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from api.agent_harness.activity import put_status
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult

_USER_REQUEST_HEADER = "## User request"


def _peel_user_request(prompt: str) -> str:
    """Return the raw user text, stripping a Context Compiler envelope if present.

    Slash commands like ``/model`` must be parsed from the user request — not from
    the compiled ``<cuttle_context>`` wrapper — or they never match.
    """
    text = prompt or ""
    marker = f"{_USER_REQUEST_HEADER}\n"
    if marker in text:
        return text.rsplit(marker, 1)[-1].strip()
    # Header without trailing newline (delta / handoff variants).
    alt = f"{_USER_REQUEST_HEADER}\r\n"
    if alt in text:
        return text.rsplit(alt, 1)[-1].strip()
    idx = text.find(_USER_REQUEST_HEADER)
    if idx >= 0:
        rest = text[idx + len(_USER_REQUEST_HEADER) :].lstrip("\r\n")
        return rest.strip()
    return text.strip()


def _resolve_cursor_model(
    *,
    slash_preferred: Optional[str],
    session_model: Optional[str],
    kernel_model: Optional[str],
) -> str:
    """Pick the CLI ``--model`` id for this turn.

    Order: this-turn ``/model`` → per-chat pin → explicit non-auto
    kernel/router override → globally starred model → Auto (Cursor's CLI
    default). Effort is baked into the model id — no separate effort layer.
    """
    slash = (slash_preferred or "").strip()
    if slash:
        return slash
    pinned = (session_model or "").strip()
    if pinned:
        return pinned
    kernel = (kernel_model or "").strip()
    if kernel and kernel.lower() != "auto":
        return kernel
    try:
        from api.agent_harness.agent_defaults import get_starred_model

        starred = (get_starred_model("cursor") or "").strip()
        if starred:
            return starred
    except Exception:
        pass
    return "auto"


def _slash_reply_result(
    slash_result: dict,
    *,
    cwd: str,
    model: Optional[str],
) -> AgentResult:
    meta: dict = {}
    preferred = slash_result.get("preferred_model")
    if preferred:
        meta["preferred_model"] = preferred
        meta["cursor_run"] = {
            "requested_model": preferred,
            "reported_model": preferred,
            "cwd": cwd,
        }
    if slash_result.get("ui"):
        meta["ui"] = slash_result["ui"]
    if slash_result.get("notice"):
        meta["notice"] = slash_result["notice"]
    return AgentResult(
        success=True,
        output=slash_result.get("message") or "Done.",
        model=str(preferred or model or "auto"),
        meta=meta,
    )


class Adapter:
    def available(self) -> bool:
        from scripts.utilities.cursor_cli_tool import _resolve_cursor_agent_argv

        return bool(_resolve_cursor_agent_argv())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        from scripts.utilities.cursor_cli_session_store import load_cursor_resume_id

        return load_cursor_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        from scripts.utilities.cursor_cli_session_store import save_cursor_resume_id

        save_cursor_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        from scripts.utilities.cursor_cli_session_store import clear_cursor_resume_id

        clear_cursor_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str],
        model: Optional[str],
        cwd: Optional[str] = None,
    ) -> Optional[AgentResult]:
        """Handle reply-only Cursor slash cmds before Context Compiler wraps them.

        Set-and-run forms (``/model <id> <prompt>``, ``/plan …``) return None so the
        kernel still compiles + executes; ``execute`` peels the user request and
        applies the pin.
        """
        from api.cursor_agent_commands import handle_cursor_agent_slash, parse_cursor_agent_slash

        raw = (prompt or "").strip()
        if not parse_cursor_agent_slash(raw):
            return None
        work_cwd = (cwd or "").strip() or resolve_harness_cwd("")
        try:
            slash_result = handle_cursor_agent_slash(
                raw, cwd=work_cwd, chat_session_id=chat_session_id
            )
        except Exception as exc:
            print(f"[cursor harness] handle_meta slash error: {exc}", flush=True)
            return None
        if isinstance(slash_result, dict) and slash_result.get("action") == "reply":
            return _slash_reply_result(slash_result, cwd=work_cwd, model=model)
        # action == "run": prefs already saved; let execute peel + run the remainder.
        return None

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
        from scripts.utilities.cursor_cli_tool import (
            _cursor_agent_oneline_prompt,
            handle_cursor_cli_command,
        )

        # Nested git (Unity source/) stays in-project. Never pin to another
        # chip's resume workspace — the kernel already resolved the project cwd.
        try:
            from api.agent_harness.cwd import constrain_to_project
            from scripts.utilities.cursor_cli_session_store import prepare_cursor_agent_workspace

            requested = cwd
            pinned, _ = prepare_cursor_agent_workspace(cwd, chat_session_id)
            cwd = constrain_to_project(requested, pinned or requested)
        except Exception:
            pass

        run_mode = None
        slash_preferred: Optional[str] = None
        # Prefer the raw user request (post–Context Compiler) for /model /plan /ask.
        slash_prompt = _peel_user_request(prompt)
        try:
            from api.cursor_agent_commands import handle_cursor_agent_slash

            slash_result = handle_cursor_agent_slash(
                slash_prompt, cwd=cwd, chat_session_id=chat_session_id
            )
        except Exception as exc:
            print(f"[cursor harness] slash handler error: {exc}", flush=True)
            slash_result = None

        if isinstance(slash_result, dict) and slash_result.get("action") == "reply":
            return _slash_reply_result(slash_result, cwd=cwd, model=model)
        if isinstance(slash_result, dict) and slash_result.get("action") == "run":
            remainder = slash_result.get("prompt") or ""
            run_mode = slash_result.get("mode")
            slash_preferred = (
                str(slash_result.get("preferred_model") or "").strip() or None
            )
            # Keep the compiled envelope when present; only replace the user section.
            if f"{_USER_REQUEST_HEADER}\n" in (prompt or ""):
                head, _sep, _old = prompt.partition(f"{_USER_REQUEST_HEADER}\n")
                prompt = f"{head}{_USER_REQUEST_HEADER}\n{remainder}"
            else:
                prompt = remainder

        low_p = (prompt or "").strip().lower()
        peel_low = slash_prompt.strip().lower()
        is_meta = peel_low.startswith("session ") or peel_low in (
            "--version", "-v", "version", "--help", "-h", "help"
        )
        if is_meta:
            put_status(status_queue, "Cursor Agent meta command…")
            text = await asyncio.to_thread(handle_cursor_cli_command, slash_prompt)
            return AgentResult(
                success=True,
                output=text or "Done.",
                model=str(model or "auto"),
            )

        session_model = None
        try:
            from scripts.utilities.cursor_cli_session_store import load_cursor_agent_options

            opts = load_cursor_agent_options(cwd, chat_session_id) or {}
            session_model = opts.get("model")
        except Exception:
            session_model = None

        mid = _resolve_cursor_model(
            slash_preferred=slash_preferred,
            session_model=session_model if isinstance(session_model, str) else None,
            kernel_model=model,
        )

        put_status(status_queue, "Calling Cursor Agent…")
        run_meta: dict = {}

        def _run():
            return _cursor_agent_oneline_prompt(
                prompt,
                workspace=cwd,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                mode=run_mode,
                model=mid,
                run_meta_out=run_meta,
                cancel_event=cancel_event,
            )

        output = await asyncio.to_thread(_run)
        if output is None:
            output = await asyncio.to_thread(handle_cursor_cli_command, prompt)

        failed = isinstance(output, str) and (
            output.startswith("[FAIL]") or output.startswith("[CANCELLED]")
        )
        sid = None
        if isinstance(run_meta, dict):
            # cursor_cli_tool persists resume; also surface for kernel save.
            try:
                from scripts.utilities.cursor_cli_session_store import load_cursor_resume_id

                sid = load_cursor_resume_id(cwd, chat_session_id)
            except Exception:
                sid = None
        usage = {}
        if isinstance(run_meta.get("usage"), dict):
            raw_u = run_meta["usage"]
            usage = {
                "prompt_tokens": int(raw_u.get("inputTokens") or raw_u.get("prompt_tokens") or 0),
                "completion_tokens": int(
                    raw_u.get("outputTokens") or raw_u.get("completion_tokens") or 0
                ),
                "total_tokens": int(raw_u.get("totalTokens") or raw_u.get("total_tokens") or 0),
            }
            # Preserve cache + peak context for the composer gauge. Final
            # inputTokens alone are often cumulative billing, not window fill.
            for src, dst in (
                ("cacheReadTokens", "cache_read_tokens"),
                ("cacheWriteTokens", "cache_write_tokens"),
                ("context_tokens", "context_tokens"),
                ("peak_context_tokens", "peak_context_tokens"),
            ):
                if raw_u.get(src) is not None:
                    try:
                        usage[dst] = int(raw_u.get(src) or 0)
                    except (TypeError, ValueError):
                        pass
            if usage.get("context_tokens") is None and run_meta.get("peak_context_tokens"):
                try:
                    usage["context_tokens"] = int(run_meta.get("peak_context_tokens") or 0)
                except (TypeError, ValueError):
                    pass
            # Prefer stream peak when present on the segment result.
            peak = run_meta.get("peak_context_tokens")
            if peak and not usage.get("context_tokens"):
                try:
                    usage["context_tokens"] = int(peak)
                except (TypeError, ValueError):
                    pass
        # Cursor (subscription billing) never reports cost — estimate the
        # API-equivalent price from the final cumulative usage. Cursor usage
        # is additive (inputTokens excludes cached reads), so the inclusive
        # heuristic must not subtract cache reads from the prompt. "auto"
        # turns have no public per-model rate and stay unpriced.
        usage["cache_inclusive"] = False
        try:
            from api.model_pricing import attach_estimated_cost

            attach_estimated_cost(usage, reported or "", cache_inclusive=False)
        except Exception:
            pass
        reported = (
            str(run_meta.get("reported_model") or mid).strip()
            if isinstance(run_meta, dict)
            else mid
        )
        meta = {"cursor_run": dict(run_meta)} if run_meta else {}
        if mid and mid.lower() != "auto":
            meta["preferred_model"] = mid
        try:
            from api.agent_harness.agent_defaults import (
                SOURCE_CLI_DEFAULT,
                SOURCE_OVERRIDE,
                SOURCE_SESSION,
                SOURCE_STARRED,
                get_starred_model as _cursor_starred,
            )

            _km = (model or "").strip()
            if slash_preferred:
                meta["model_source"] = SOURCE_SESSION
            elif (session_model or "").strip():
                meta["model_source"] = SOURCE_SESSION
            elif _km and _km.lower() != "auto":
                meta["model_source"] = SOURCE_OVERRIDE
            elif (_cursor_starred("cursor") or "").strip():
                meta["model_source"] = SOURCE_STARRED
            else:
                meta["model_source"] = SOURCE_CLI_DEFAULT
            meta["agent_model"] = mid
        except Exception:
            pass
        if failed:
            return AgentResult(
                success=False,
                output=output or "",
                error=output or "Cursor Agent failed",
                usage=usage,
                session_id=sid,
                model=reported,
                meta=meta,
            )
        return AgentResult(
            success=True,
            output=output or "Done.",
            usage=usage,
            session_id=sid,
            model=reported,
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
