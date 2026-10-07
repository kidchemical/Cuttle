"""Hermes Agent harness adapter — wraps ``scripts.utilities.hermes_cli_tool``.

Reads model/provider from Hermes ``config.yaml`` when not overridden.
Hermes owns provider selection; users manage any local model server.

Per-chat ``/hermes model`` + ``/hermes effort`` pins mirror Muse Code's badge-
gated palette controls.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from api.agent_harness.activity import put_status
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult


def _handle_hermes_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    """Native ``/hermes model …`` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.hermes_cli_session_store import (
        load_hermes_model,
        save_hermes_model,
    )
    from scripts.utilities.hermes_cli_tool import (
        hermes_model_label,
        list_hermes_known_models,
        resolve_hermes_default_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'")
    # Bare "model the login flow" is a task, not a model switch.
    if not match.group(1) and arg and not re.match(
        r"^(z-ai/|anthropic/|openai/|deepseek/|qwen|default$|reset$|clear$)",
        arg,
        flags=re.I,
    ):
        # Still allow known curated ids / slash-bearing ids.
        known_ids = {m["id"].lower() for m in list_hermes_known_models()}
        if arg.lower() not in known_ids and "/" not in arg:
            return None
    if not arg or arg.lower() in ("list", "ls", "?"):
        lines = ["**Hermes models** (pin with `/hermes model <id>`):", ""]
        for known in list_hermes_known_models():
            mark = " ✅ current" if known["id"] == active_model else ""
            lines.append(f"- `{known['id']}` — {known['label']}{mark}")
            if known.get("description"):
                lines.append(f"  _{known['description']}_")
        pinned = load_hermes_model(chat_session_id)
        if pinned:
            lines.append("")
            lines.append(
                f"Pinned for this chat: `{pinned}` ({hermes_model_label(pinned)})."
            )
        else:
            lines.append("")
            lines.append(
                f"No pin — using `{active_model}` ({hermes_model_label(active_model)})."
            )
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        from api.agent_harness.agent_defaults import get_starred_model as _sm_hr

        save_hermes_model(chat_session_id, None)
        back = _sm_hr("hermes")
        default = back or resolve_hermes_default_model()
        return AgentResult(
            success=True,
            output=(
                f"**Hermes:** Model reset to starred default `{back}`."
                if back
                else f"**Hermes:** Model reset to default `{default}`."
            ),
            model=default,
            meta={
                "agent_model": default,
                "model_source": ("starred" if back else "cli_default"),
            },
        )

    saved = save_hermes_model(chat_session_id, arg) or arg
    return AgentResult(
        success=True,
        output=(
            f"**Hermes:** Model set to `{saved}` "
            f"({hermes_model_label(saved)}) for this chat."
        ),
        model=saved,
        meta={
            "agent_model": saved,
            "model_source": "session",
        },
    )


def _handle_hermes_effort_slash(
    prompt: str,
    chat_session_id: Optional[str],
    model: Optional[str] = None,
) -> Optional[AgentResult]:
    """Native ``/hermes effort …`` without starting a CLI turn."""
    from scripts.utilities.hermes_cli_session_store import (
        load_hermes_effort,
        load_hermes_model,
        save_hermes_effort,
    )
    from scripts.utilities.hermes_cli_tool import (
        HERMES_REASONING_EFFORTS,
        hermes_model_label,
        hermes_model_supports_reasoning_extra_body,
        load_hermes_config_reasoning_effort,
        resolve_hermes_default_model,
    )

    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    known = [str(e) for e in HERMES_REASONING_EFFORTS]
    # Bare "effort the login flow" is a task, not an effort switch.
    if not match.group(1) and arg and arg not in known and arg not in (
        "list", "ls", "?", "default", "reset", "clear",
    ):
        return None
    from api.agent_harness.agent_defaults import (
        get_starred_effort as _se_h,
        resolve_effective_model as _resolve_mh,
    )

    active_model, _model_source = _resolve_mh(
        "hermes",
        session_model=load_hermes_model(chat_session_id),
        kernel_override=model,
        cli_default=resolve_hermes_default_model(),
    )
    active_model = active_model or resolve_hermes_default_model()
    base_meta = {
        "agent_model": active_model,
        "model_source": _model_source,
    }
    current = load_hermes_effort(chat_session_id)
    resolved_effort = current or _se_h("hermes")
    if resolved_effort:
        base_meta["agent_effort"] = resolved_effort
        base_meta["effort_source"] = "session" if current else "starred"
    cfg_effort = load_hermes_config_reasoning_effort() or "medium"
    supports = hermes_model_supports_reasoning_extra_body(active_model)
    if not arg or arg in ("list", "ls", "?"):
        lines = ["**Hermes reasoning effort** (pin with `/hermes effort <level>`):", ""]
        for level in known:
            mark = " ✅ current" if level == current else ""
            lines.append(f"- `{level}`{mark}")
        lines.append("")
        if current:
            lines.append(f"Pinned for this chat: `{current}`.")
        elif resolved_effort:
            lines.append(f"Starred default for new chats: `{resolved_effort}`.")
        else:
            lines.append(
                f"No pin — using Hermes config.yaml (`{cfg_effort}`)."
            )
        lines.append("")
        lines.append(
            f"Model for this chat: `{active_model}` ({hermes_model_label(active_model)})."
        )
        if not supports:
            lines.append("")
            lines.append(
                "_Note:_ Hermes only forwards the reasoning field for certain "
                "OpenRouter families (deepseek/, anthropic/, openai/, x-ai/, …). "
                f"`{active_model}` is outside that allowlist, so the effort pin "
                "is stored for badges but may not reach the provider."
            )
        return AgentResult(
            success=True,
            output="\n".join(lines),
            model=active_model,
            meta=dict(base_meta),
        )
    if arg in ("default", "reset", "clear"):
        save_hermes_effort(chat_session_id, None)
        back = _se_h("hermes")
        return AgentResult(
            success=True,
            output=(
                f"**Hermes:** Reasoning effort reset to starred default `{back}`."
                if back
                else "**Hermes:** Reasoning effort reset to the config.yaml default."
            ),
            model=active_model,
            meta={
                **{k: v for k, v in base_meta.items() if k not in ("agent_effort", "effort_source")},
                "agent_effort": back,
                "effort_source": ("starred" if back else "none"),
            },
        )
    if arg not in known:
        return AgentResult(
            success=True,
            output=(
                f"Unknown effort `{arg}`. Pick one of: "
                f"{', '.join(f'`{e}`' for e in known)}."
            ),
            model=active_model,
            meta=dict(base_meta),
        )
    save_hermes_effort(chat_session_id, arg)
    note = ""
    if not supports:
        note = (
            f"\n\n_Note:_ `{active_model}` may not receive the reasoning field "
            "(Hermes OpenRouter allowlist)."
        )
    return AgentResult(
        success=True,
        output=f"**Hermes:** Reasoning effort set to `{arg}` for this chat.{note}",
        model=active_model,
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
    )


class Adapter:
    def available(self) -> bool:
        from scripts.utilities.hermes_cli_tool import hermes_executable

        return bool(hermes_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        from scripts.utilities.hermes_cli_session_store import load_hermes_resume_id

        return load_hermes_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        from scripts.utilities.hermes_cli_session_store import save_hermes_resume_id

        save_hermes_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        from scripts.utilities.hermes_cli_session_store import clear_hermes_resume_id

        clear_hermes_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str],
        model: Optional[str],
    ) -> Optional[AgentResult]:
        """Handle model/effort/usage commands before Context Compiler wraps the prompt."""
        from api.agent_harness.agent_defaults import resolve_effective_model as _resolve_mhm
        from scripts.utilities.hermes_cli_session_store import load_hermes_model
        from scripts.utilities.hermes_cli_tool import resolve_hermes_default_model

        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("hermes", prompt)
        if usage_md is not None:
            active, _ = _resolve_mhm(
                "hermes",
                session_model=load_hermes_model(chat_session_id),
                kernel_override=model,
                cli_default=resolve_hermes_default_model(),
            )
            return AgentResult(success=True, output=usage_md, model=active or "")

        effort_result = _handle_hermes_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        active, _ = _resolve_mhm(
            "hermes",
            session_model=load_hermes_model(chat_session_id),
            kernel_override=model,
            cli_default=resolve_hermes_default_model(),
        )
        return _handle_hermes_model_slash(prompt, chat_session_id, active or "")

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
        provider: Optional[str] = None,
        toolsets: Optional[str] = None,
        cancel_event: Any = None,
        reasoning_effort: Optional[str] = None,
    ) -> AgentResult:
        from scripts.utilities.hermes_cli_session_store import (
            load_hermes_effort,
            load_hermes_model,
        )
        from scripts.utilities.hermes_cli_tool import (
            HermesCliTool,
            hermes_reasoning_effort_override,
            resolve_hermes_runtime,
            usage_for_query_report,
        )

        from api.agent_harness.agent_defaults import get_starred_model

        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_SESSION,
            SOURCE_STARRED,
            badge_meta,
            get_starred_effort,
            get_starred_model,
        )

        session_model = load_hermes_model(chat_session_id)
        # Per-chat pin → explicit call arg → starred → config.yaml default.
        mid_override = (
            session_model
            or (str(model).strip() if model else "")
            or get_starred_model("hermes")
            or None
        )
        if session_model:
            _model_source = SOURCE_SESSION
        elif (str(model).strip() if model else ""):
            _model_source = SOURCE_OVERRIDE
        elif get_starred_model("hermes"):
            _model_source = SOURCE_STARRED
        else:
            _model_source = SOURCE_CLI_DEFAULT
        # A chat model pin also owns provider inference (don't keep an OpenRouter
        # pipeline provider when the pin is local qwen3-coder, or vice versa).
        prov_override = None if session_model else provider
        runtime = resolve_hermes_runtime(mid_override, prov_override)
        mid = runtime["model"]
        prov = runtime["provider"]
        from scripts.utilities.hermes_cli_session_store import load_hermes_effort as _load_effort

        _sess_effort = _load_effort(chat_session_id)
        _star_effort = get_starred_effort("hermes")
        _override_effort = (str(reasoning_effort).strip().lower() if reasoning_effort else "")
        effort = _override_effort or _sess_effort or _star_effort or None
        _effort_source = (
            SOURCE_OVERRIDE
            if _override_effort
            else ("session" if _sess_effort else ("starred" if _star_effort else "none"))
        )

        put_status(status_queue, f"Calling Hermes ({prov}/{mid})…")
        hm = HermesCliTool(
            model=mid,
            provider=prov,
            toolsets=(str(toolsets).strip() if toolsets else None),
        )
        with hermes_reasoning_effort_override(effort):
            raw = await hm.execute_prompt(
                prompt,
                cwd=cwd,
                timeout=timeout,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                resume=resume,
                cancel_event=cancel_event,
            )

        ok = bool(raw.get("success"))
        err = None if ok else (raw.get("error") or "Hermes Agent failed")
        usage_raw = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        uq = usage_for_query_report(mid, usage_raw)
        usage = {
            "prompt_tokens": int(uq.get("input_tokens") or 0),
            "completion_tokens": int(uq.get("output_tokens") or 0),
            "total_tokens": int(uq.get("total_tokens") or 0),
            "model": mid,
        }
        if uq.get("cache_read_tokens"):
            usage["cache_read_tokens"] = int(uq.get("cache_read_tokens") or 0)
        if uq.get("cache_write_tokens"):
            usage["cache_write_tokens"] = int(uq.get("cache_write_tokens") or 0)
        if uq.get("cost") is not None:
            try:
                usage["cost"] = float(uq["cost"])
            except (TypeError, ValueError):
                pass
        if isinstance(uq.get("cost_estimated"), bool):
            usage["cost_estimated"] = uq["cost_estimated"]
        hermes_sid = raw.get("hermes_session_id") or resume
        if hermes_sid:
            try:
                from scripts.utilities.hermes_cli_tool import load_hermes_context_occupancy
                from scripts.utilities.hermes_cli_session_store import (
                    save_hermes_context_snapshot,
                )
                import time as _time

                occ = load_hermes_context_occupancy(str(hermes_sid))
                ctx = int(occ.get("context_tokens") or 0)
                if ctx > 0:
                    usage["context_tokens"] = ctx
                    if chat_session_id:
                        save_hermes_context_snapshot(
                            chat_session_id,
                            {
                                "context_tokens": ctx,
                                "ts": _time.time(),
                                "session_id": str(hermes_sid),
                            },
                        )
            except Exception:
                pass
        meta = badge_meta("hermes", mid, _model_source, effort, _effort_source)
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
            session_id=raw.get("hermes_session_id"),
            model=mid,
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
