"""Claude Code CLI harness adapter — native ``claude -p`` with per-chat resume.

Per-chat ``/claude model`` + ``/claude effort`` pins mirror the Codex / Muse /
OpenCode badge-gated palette controls. Refresh re-reads the installed CLI's
model catalog (SDK ``initialize``; no turn runs).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from api.agent_harness.agents.claude.model_catalog import (
    claude_efforts_for_model,
    claude_model_label,
    list_claude_catalog_models,
    refresh_claude_catalog,
)
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult
from scripts.utilities.claude_cli_session_store import (
    claude_transcript_stats,
    count_compact_boundaries,
)
from scripts.utilities.claude_cli_tool import ClaudeCliTool, claude_executable

_EFFORT_WORDS = ("low", "medium", "high", "xhigh", "max")


def _handle_claude_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    """Native ``/claude model …`` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.claude_cli_session_store import (
        load_claude_model,
        save_claude_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'")
    # Bare "model the auth flow" is a task, not a model switch.
    if not match.group(1) and arg and not re.match(
        r"^(claude-|default$|reset$|clear$|refresh$|reload$|sync$|list$|ls$|\?$)",
        arg,
        flags=re.I,
    ):
        known_ids = {
            str(m.get("id") or "").lower()
            for m in (list_claude_catalog_models().get("models") or [])
            if isinstance(m, dict)
        }
        if arg.lower() not in known_ids:
            return None

    if arg.lower() in ("refresh", "reload", "sync"):
        result = refresh_claude_catalog()
        count = int(result.get("count") or 0)
        err = result.get("error")
        source = result.get("source") or "unknown"
        if err and source == "static_fallback" and count == 0:
            return AgentResult(
                success=False,
                output="",
                error=f"Claude Code model refresh failed: {err}",
                model=active_model,
            )
        note = (
            f" (CLI catalog unavailable — {err}; showing the manifest snapshot)"
            if err and source == "static_fallback"
            else " from the Claude Code CLI"
        )
        return AgentResult(
            success=True,
            output=(
                f"**Claude Code:** Refreshed model catalog{note} — "
                f"{count} models available in the `/` palette.\n\n"
                "Filter in chat (with a Claude badge): type `opus`, `sonnet`, "
                "`fable`, … then pick a row. "
                "Each model row includes its supported effort levels. "
                "Refresh reloads models and effort support from the installed CLI; "
                "the manifest is the offline fallback."
            ),
            model=active_model,
            meta={
                "agent_model": active_model,
                "claude_catalog_count": count,
                "claude_catalog_source": source,
            },
        )

    if not arg or arg.lower() in ("list", "ls", "?"):
        catalog = list_claude_catalog_models()
        lines = [
            "**Claude Code models** (pin with `/claude model <id>`):",
            "",
            f"Palette catalog: {int(catalog.get('count') or 0)} models "
            f"(source `{catalog.get('source')}`). "
            "Refresh with `/claude model refresh`.",
            "",
        ]
        for known in catalog.get("models") or []:
            if not isinstance(known, dict):
                continue
            kid = str(known.get("id") or "").strip()
            if not kid:
                continue
            mark = " ✅ current" if kid == active_model else ""
            lines.append(f"- `{kid}` — {known.get('label') or kid}{mark}")
            if known.get("description"):
                lines.append(f"  _{known['description']}_")
            supported = [str(level) for level in (known.get("efforts") or [])]
            if supported:
                lines.append(
                    "  Effort levels: " + ", ".join(f"`{level}`" for level in supported)
                )
        pinned = load_claude_model(chat_session_id)
        lines.append("")
        if pinned:
            lines.append(
                f"Pinned for this chat: `{pinned}` ({claude_model_label(pinned)})."
            )
        else:
            lines.append(
                f"No pin — using `{active_model or 'CLI default'}`"
                + (f" ({claude_model_label(active_model)})" if active_model else "")
                + "."
            )
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        from api.agent_harness.agent_defaults import get_starred_model as _sm

        save_claude_model(chat_session_id, None)
        back = _sm("claude") or ""
        return AgentResult(
            success=True,
            output=(
                f"**Claude Code:** Model reset to starred default `{back}`."
                if back
                else "**Claude Code:** Model pin cleared — using the Claude CLI default."
            ),
            model=back,
            meta={
                "agent_model": back,
                "model_source": ("starred" if back else "cli_default"),
            },
        )

    saved = save_claude_model(chat_session_id, arg) or arg
    return AgentResult(
        success=True,
        output=(
            f"**Claude Code:** Model set to `{saved}` "
            f"({claude_model_label(saved)}) for this chat."
        ),
        model=saved,
        meta={"agent_model": saved, "model_source": "session"},
    )


def _handle_claude_effort_slash(
    prompt: str,
    chat_session_id: Optional[str],
    model: Optional[str] = None,
) -> Optional[AgentResult]:
    """Native ``/claude effort …`` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.claude_cli_session_store import (
        load_claude_effort,
        load_claude_model,
        save_claude_effort,
    )
    from api.agent_harness.agent_defaults import (
        get_starred_effort,
        resolve_effective_effort,
        resolve_effective_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    if not match.group(1) and arg and arg not in (
        *_EFFORT_WORDS, "default", "reset", "clear", "none", "list", "ls", "?",
    ):
        # "effort estimate for X" is a task, not an effort pin.
        return None

    active, _src = resolve_effective_model(
        "claude",
        session_model=load_claude_model(chat_session_id),
        kernel_override=model,
        cli_default="",
    )
    known = claude_efforts_for_model(active or None)
    pinned = load_claude_effort(chat_session_id) or ""
    effective_effort, effort_source = resolve_effective_effort(
        "claude", session_effort=pinned
    )
    base_meta = {
        "agent_model": active or "",
        "agent_effort": effective_effort or "",
        "effort_source": effort_source,
    }

    if not arg or arg in ("list", "ls", "?"):
        lines = [
            "**Claude Code effort** (pin with `/claude effort <level>`):",
            "",
            f"Model `{active}` supports:" if active else "The CLI default model supports:",
            "",
            "Maps to Claude Code `--effort`.",
            "",
        ]
        for e in known:
            mark = " ✅ current" if e == effective_effort else ""
            lines.append(f"- `{e}`{mark}")
        if not known:
            lines.append(
                "This model takes no effort level. "
                "Refresh with `/claude model refresh` or pick another model."
            )
        lines.append("")
        if pinned:
            lines.append(f"Pinned for this chat: `{pinned}`.")
        elif effective_effort:
            lines.append(f"Starred default for new chats: `{effective_effort}`.")
        else:
            lines.append("No pin — Claude Code uses its configured default.")
        return AgentResult(
            success=True, output="\n".join(lines), model=active or "", meta=dict(base_meta)
        )

    if arg in ("default", "reset", "clear", "none"):
        save_claude_effort(chat_session_id, None)
        back = get_starred_effort("claude") or ""
        return AgentResult(
            success=True,
            output=(
                f"**Claude Code:** Effort reset to starred default `{back}`."
                if back
                else "**Claude Code:** Effort pin cleared — using the Claude CLI default."
            ),
            model=active or "",
            meta={
                "agent_model": active or "",
                "agent_effort": back,
                "effort_source": ("starred" if back else "none"),
            },
        )

    if arg not in known:
        supported = (
            "Supported levels: " + ", ".join(f"`{e}`" for e in known) + "."
            if known
            else "This model takes no effort level; try `/claude model refresh`."
        )
        return AgentResult(
            success=True,
            output=(
                f"Effort `{arg}` is not supported for "
                f"`{active or 'the CLI default model'}`. {supported}"
            ),
            model=active or "",
            meta=dict(base_meta),
        )

    save_claude_effort(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**Claude Code:** Effort set to `{arg}` for this chat.",
        model=active or "",
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
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
        """Handle model/effort/usage commands before Context Compiler wraps the prompt."""
        from api.agent_harness.agent_defaults import resolve_effective_model
        from api.agent_usage import handle_agent_usage_slash
        from scripts.utilities.claude_cli_session_store import load_claude_model

        active, _ = resolve_effective_model(
            "claude",
            session_model=load_claude_model(chat_session_id),
            kernel_override=model,
            cli_default="",
        )
        usage_md = handle_agent_usage_slash("claude", prompt)
        if usage_md is not None:
            return AgentResult(success=True, output=usage_md, model=active or "")

        effort_result = _handle_claude_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        return _handle_claude_model_slash(prompt, chat_session_id, active or "")

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
        reasoning_effort: Optional[str] = None,
        cancel_event: Any = None,
    ) -> AgentResult:
        from scripts.utilities.claude_cli_session_store import (
            load_claude_effort,
            load_claude_model,
        )
        from scripts.utilities.claude_cli_tool import usage_for_query_report

        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_SESSION,
            SOURCE_STARRED,
            badge_meta,
            get_starred_effort,
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

        sess_effort = load_claude_effort(chat_session_id)
        star_effort = get_starred_effort("claude")
        effort = (
            (str(reasoning_effort).strip().lower() if reasoning_effort else "")
            or (sess_effort or "")
            or (star_effort or "")
            or None
        )
        effort_source = (
            SOURCE_OVERRIDE
            if reasoning_effort
            else (
                SOURCE_SESSION
                if sess_effort
                else (SOURCE_STARRED if star_effort else SOURCE_CLI_DEFAULT)
            )
        )
        if effort:
            supported_efforts = claude_efforts_for_model(mid)
            if effort not in supported_efforts:
                detail = (
                    "Supported levels for `{}`: {}.".format(
                        mid or "the CLI default model",
                        ", ".join(f"`{level}`" for level in supported_efforts),
                    )
                    if supported_efforts
                    else "This model takes no effort level; refresh with `/claude model refresh`."
                )
                return AgentResult(
                    success=True,
                    output=(
                        f"Claude Code was not started: effort `{effort}` is not supported for "
                        f"`{mid or 'the CLI default model'}`. {detail} "
                        "Change the effort pin or reset it with `/claude effort default`."
                    ),
                    model=mid or "",
                    meta={
                        "agent_model": mid or "",
                        "agent_effort": effort,
                        "effort_source": effort_source,
                    },
                )

        # Transcript boundaries remain authoritative for auto-compaction.
        compacts_before = count_compact_boundaries(resume) if resume else None
        from api.agent_harness.steer import steer_enabled

        tool = ClaudeCliTool(model=mid, reasoning_effort=effort)
        raw = await tool.execute_prompt(
            prompt, cwd=cwd, resume=resume, status_queue=status_queue,
            chat_session_id=chat_session_id, timeout=timeout, cancel_event=cancel_event,
            steerable=steer_enabled("claude"),
        )

        ok = bool(raw.get("success"))
        usage_raw = raw.get("usage") or {}
        uq = usage_for_query_report(usage_raw, mid or "claude")
        usage = {
            "prompt_tokens": int(uq.get("input_tokens") or 0),
            "completion_tokens": int(uq.get("output_tokens") or 0),
            "total_tokens": int(uq.get("total_tokens") or 0),
            "model": mid or "",
            "cache_inclusive": False,
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
        for key in ("cost_estimated", "reported_cost"):
            if key in uq:
                usage[key] = uq[key]

        sid = raw.get("claude_session_id")
        transcript = claude_transcript_stats(str(sid).strip()) if sid else None
        if transcript and transcript["context_tokens"] > 0:
            # Real window fill (aggregate input_tokens excludes cached tokens).
            usage["context_tokens"] = transcript["context_tokens"]
        meta = badge_meta("claude", mid or "", _model_source)
        if effort:
            meta["agent_effort"] = effort
            meta["effort_source"] = effort_source
        if raw.get("steered"):
            meta["steered"] = int(raw["steered"])
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
        if (
            compacts_before is not None
            and transcript is not None
            and str(sid).strip() == str(resume).strip()
            and transcript["compactions"] > compacts_before
        ):
            meta["context_compacted"] = True
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
