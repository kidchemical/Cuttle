"""Muse Code CLI harness adapter — wraps ``scripts.utilities.muse_cli_tool``."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from api.agent_harness.activity import put_status
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult


def _handle_muse_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    """Native `/muse model …` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.muse_cli_session_store import (
        load_muse_model,
        save_muse_model,
    )
    from scripts.utilities.muse_cli_tool import (
        list_muse_catalog_models,
        muse_model_label,
        refresh_muse_catalog,
        resolve_muse_default_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'")
    # Bare "model the login flow" is a task, not a model switch.
    if not match.group(1) and arg and not re.match(
        r"^(muse[-_]|default$|reset$|clear$|refresh$|reload$|sync$)", arg, flags=re.I
    ):
        return None
    if arg.lower() in ("refresh", "reload", "sync"):
        result = refresh_muse_catalog()
        count = int(result.get("count") or 0)
        err = result.get("error")
        source = result.get("source") or "unknown"
        if err and source == "static_fallback" and count == 0:
            return AgentResult(
                success=False,
                output="",
                error=f"Muse model refresh failed: {err}",
                model=active_model,
            )
        note = ""
        if err and source == "static_fallback":
            note = (
                f" (Meta API unavailable — {err}; showing static Muse Spark list)"
            )
        elif source.startswith("meta_api"):
            note = " from Meta Model API"
        return AgentResult(
            success=True,
            output=(
                f"**Muse Code:** Refreshed model catalog{note} — "
                f"{count} models available in the `/` palette.\n\n"
                "Filter in chat (with a Muse badge): type `spark`, `1.3`, "
                "`contributor`, … then pick a row. "
                "Or pin directly: `/muse model muse-spark-1.3`."
            ),
            model=active_model,
            meta={
                "agent_model": active_model,
                "muse_catalog_count": count,
                "muse_catalog_source": source,
            },
        )
    if not arg or arg.lower() in ("list", "ls", "?"):
        catalog = list_muse_catalog_models()
        lines = ["**Muse models** (pin with `/muse model <id>`):", ""]
        lines.append(
            f"Palette catalog: {int(catalog.get('count') or 0)} models "
            f"(source `{catalog.get('source')}`). "
            "Refresh with `/muse model refresh`."
        )
        lines.append("")
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
        pinned = load_muse_model(chat_session_id)
        if pinned:
            lines.append("")
            lines.append(f"Pinned for this chat: `{pinned}` ({muse_model_label(pinned)}).")
        else:
            lines.append("")
            lines.append(
                f"No pin — using `{active_model}` ({muse_model_label(active_model)})."
            )
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        from api.agent_harness.agent_defaults import get_starred_model as _sm

        save_muse_model(chat_session_id, None)
        back = _sm("muse")
        default = back or resolve_muse_default_model()
        return AgentResult(
            success=True,
            output=(
                f"**Muse Code:** Model reset to starred default `{back}`."
                if back
                else f"**Muse Code:** Model reset to default `{default}`."
            ),
            model=default,
            meta={
                "agent_model": default,
                "model_source": ("starred" if back else "cli_default"),
            },
        )

    saved = save_muse_model(chat_session_id, arg) or arg
    return AgentResult(
        success=True,
        output=f"**Muse Code:** Model set to `{saved}` ({muse_model_label(saved)}) for this chat.",
        model=saved,
        meta={
            "agent_model": saved,
            "model_source": "session",
        },
    )


def _handle_muse_effort_slash(
    prompt: str,
    chat_session_id: Optional[str],
    model: Optional[str] = None,
) -> Optional[AgentResult]:
    """Native `/muse effort …` without starting a CLI turn."""
    from scripts.utilities.muse_cli_session_store import (
        load_muse_effort,
        load_muse_model,
        save_muse_effort,
    )
    from scripts.utilities.muse_cli_tool import (
        MUSE_REASONING_EFFORTS,
        muse_model_label,
        resolve_muse_default_model,
    )

    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    known = [str(e) for e in MUSE_REASONING_EFFORTS]
    # Bare "effort the login flow" is a task, not an effort switch.
    if not match.group(1) and arg and arg not in known and arg not in (
        "list", "ls", "?", "default", "reset", "clear", "none",
    ):
        return None
    # Carry the active model so reply badges keep "Muse Code - <model>"
    # instead of degrading to the bare agent id.
    from api.agent_harness.agent_defaults import (
        resolve_effective_effort as _resolve_e,
        resolve_effective_model as _resolve_m,
    )

    active_model, _model_source = _resolve_m(
        "muse",
        session_model=load_muse_model(chat_session_id),
        kernel_override=model,
        cli_default=resolve_muse_default_model(),
    )
    active_model = active_model or resolve_muse_default_model()
    current = load_muse_effort(chat_session_id)
    resolved_effort, _effort_source = _resolve_e(
        "muse", session_effort=current, cli_default=None,
    )
    base_meta = {
        "agent_model": active_model,
        "model_source": _model_source,
    }
    if resolved_effort:
        base_meta["agent_effort"] = resolved_effort
        base_meta["effort_source"] = _effort_source
    if not arg or arg in ("list", "ls", "?"):
        lines = ["**Muse reasoning effort** (pin with `/muse effort <level>`):", ""]
        for level in known:
            mark = " ✅ current" if level == current else ""
            lines.append(f"- `{level}`{mark}")
        lines.append("")
        if current:
            lines.append(f"Pinned for this chat: `{current}`.")
        elif resolved_effort:
            lines.append(f"Starred default for new chats: `{resolved_effort}`.")
        else:
            lines.append("No pin — using the CLI default.")
        lines.append("")
        lines.append(f"Model for this chat: `{active_model}` ({muse_model_label(active_model)}).")
        return AgentResult(success=True, output="\n".join(lines), model=active_model, meta=dict(base_meta))
    if arg in ("default", "reset", "clear", "none"):
        from api.agent_harness.agent_defaults import get_starred_effort as _se

        save_muse_effort(chat_session_id, None)
        back = _se("muse")
        return AgentResult(
            success=True,
            output=(
                f"**Muse Code:** Reasoning effort reset to starred default `{back}`."
                if back
                else "**Muse Code:** Reasoning effort reset to the CLI default."
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
            output=f"Unknown effort `{arg}`. Pick one of: {', '.join(f'`{e}`' for e in known)}.",
            model=active_model,
            meta=dict(base_meta),
        )
    save_muse_effort(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**Muse Code:** Reasoning effort set to `{arg}` for this chat.",
        model=active_model,
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
    )


class Adapter:
    def available(self) -> bool:
        from scripts.utilities.muse_cli_tool import muse_available

        return bool(muse_available())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        from scripts.utilities.muse_cli_session_store import load_muse_resume_id

        return load_muse_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        from scripts.utilities.muse_cli_session_store import save_muse_resume_id

        save_muse_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        from scripts.utilities.muse_cli_session_store import clear_muse_resume_id

        clear_muse_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str],
        model: Optional[str],
    ) -> Optional[AgentResult]:
        """Handle model/effort/usage commands before Context Compiler wraps the prompt."""
        from scripts.utilities.muse_cli_session_store import load_muse_model
        from scripts.utilities.muse_cli_tool import resolve_muse_default_model

        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("muse", prompt)
        if usage_md is not None:
            from api.agent_harness.agent_defaults import resolve_effective_model as _resolve_model

            active, _ = _resolve_model(
                "muse",
                session_model=load_muse_model(chat_session_id),
                kernel_override=model,
                cli_default=resolve_muse_default_model(),
            )
            return AgentResult(success=True, output=usage_md, model=active or "")

        effort_result = _handle_muse_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        from api.agent_harness.agent_defaults import resolve_effective_model as _resolve_model

        active, _ = _resolve_model(
            "muse",
            session_model=load_muse_model(chat_session_id),
            kernel_override=model,
            cli_default=resolve_muse_default_model(),
        )
        return _handle_muse_model_slash(prompt, chat_session_id, active or "")

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
        reasoning_effort: Optional[str] = None,
    ) -> AgentResult:
        from scripts.utilities.muse_cli_session_store import (
            load_muse_effort,
            load_muse_model,
        )
        from scripts.utilities.muse_cli_tool import (
            MuseCliTool,
            resolve_muse_default_model,
            usage_for_query_report,
        )

        from api.agent_harness.agent_defaults import (
            SOURCE_NONE,
            SOURCE_OVERRIDE,
            badge_meta,
            resolve_effective_effort,
            resolve_effective_model,
        )

        # Policy order: per-chat pin → explicit override → starred → CLI default.
        mid, model_source = resolve_effective_model(
            "muse",
            session_model=load_muse_model(chat_session_id),
            kernel_override=model,
            cli_default=resolve_muse_default_model(),
        )
        effort, effort_source = resolve_effective_effort(
            "muse",
            session_effort=load_muse_effort(chat_session_id),
            kernel_override=reasoning_effort,
            cli_default=None,
        )
        # This-turn kernel override is the frozen send identity — it must beat
        # a stale session pin (composer max vs leftover starred low).
        if reasoning_effort:
            pinned = str(reasoning_effort).strip().lower()
            if pinned and pinned != "none":
                effort, effort_source = pinned, SOURCE_OVERRIDE
        if (effort or "").strip().lower() == "none":
            # "none" was offered as a level before the CLI declared it invalid;
            # historic session/starred pins mean "no pin" (omit the flag).
            effort, effort_source = None, SOURCE_NONE

        # MuseCliTool owns Cursor-style activity + silent heartbeat. Do not also
        # run a coarse adapter heartbeat (stomps tool/thinking lines).
        put_status(status_queue, "Calling Muse Code…")
        from api.agent_harness.steer import steer_enabled
        from scripts.utilities.muse_cli_session_store import read_muse_session_usage

        # Cumulative usage in the session log before the turn — resuming a
        # session needs this baseline so the post-turn diff is exactly this
        # turn's model calls.
        baseline: Dict[str, Any] = {}
        try:
            baseline = read_muse_session_usage(resume)
        except Exception:
            baseline = {}

        muse = MuseCliTool(model=mid)
        raw = None
        if steer_enabled("muse"):
            from scripts.utilities.muse_serve_turn import run_muse_turn_serve

            raw = await run_muse_turn_serve(
                prompt,
                cwd=cwd,
                resume=resume,
                model=muse.model,
                reasoning_effort=effort,
                provider=muse.provider,
                yolo=muse.yolo,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                cancel_event=cancel_event,
            )
            if raw.get("fallback"):
                print(f"[MUSE] serve unavailable, using exec: {raw.get('error')}", flush=True)
                raw = None
        if raw is None:
            raw = await muse.execute_prompt(
                prompt,
                cwd=cwd,
                resume=resume,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                reasoning_effort=effort,
                cancel_event=cancel_event,
            )

        usage_raw = raw.get("usage") or {}
        # muse exec --json often omits usage now; fall back to MSP view projection.
        if not usage_raw or not (
            usage_raw.get("input_tokens")
            or usage_raw.get("prompt_tokens")
            or usage_raw.get("total_tokens")
        ):
            try:
                from scripts.utilities.muse_cli_session_store import read_muse_msp_context

                msp = read_muse_msp_context(raw.get("muse_session_id") or resume)
            except Exception:
                msp = None
            if isinstance(msp, dict) and (
                msp.get("context_tokens") or msp.get("prompt_tokens")
            ):
                usage_raw = {
                    "input_tokens": int(msp.get("prompt_tokens") or 0),
                    "output_tokens": int(msp.get("completion_tokens") or 0),
                    "total_tokens": int(
                        msp.get("total_tokens")
                        or msp.get("context_tokens")
                        or 0
                    ),
                    "context_tokens": int(msp.get("context_tokens") or 0),
                }
                if msp.get("cached_tokens"):
                    usage_raw["cached_tokens"] = int(msp.get("cached_tokens") or 0)
                if msp.get("model") and not mid:
                    mid = str(msp.get("model")).strip() or mid
        uq = usage_for_query_report(usage_raw, mid)
        usage = {
            "prompt_tokens": int(uq.get("input_tokens") or 0),
            "completion_tokens": int(uq.get("output_tokens") or 0),
            "total_tokens": int(uq.get("total_tokens") or 0),
            "model": mid,
            "cache_inclusive": True,
        }
        if usage_raw.get("context_tokens"):
            try:
                usage["context_tokens"] = int(usage_raw.get("context_tokens") or 0)
            except (TypeError, ValueError):
                pass
        cached = (
            usage_raw.get("cached_tokens")
            or usage_raw.get("cache_read_tokens")
            or uq.get("cache_read_tokens")
        )
        if cached:
            try:
                usage["cache_read_tokens"] = int(cached or 0)
            except (TypeError, ValueError):
                pass
        if uq.get("cost") is not None:
            try:
                usage["cost"] = float(uq["cost"])
            except (TypeError, ValueError):
                pass
        # Session-log diff is the authoritative per-turn token count — Muse 1.4
        # exec stdout often omits usage and the MSP fallback only sees the last
        # snapshot. Includes cache/reasoning splits the CLI may not report.
        try:
            logged = read_muse_session_usage(
                raw.get("muse_session_id") or resume, baseline=baseline
            )
        except Exception:
            logged = {}
        if logged.get("input_tokens") or logged.get("output_tokens"):
            usage["prompt_tokens"] = int(logged.get("input_tokens") or 0)
            usage["completion_tokens"] = int(logged.get("output_tokens") or 0)
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            for src in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
                usage[src] = int(logged.get(src) or 0)
        # CLI-reported cost wins; otherwise estimate from the diffed tokens.
        try:
            from api.model_pricing import attach_estimated_cost

            attach_estimated_cost(usage, mid or "", cache_inclusive=True)
        except Exception:
            pass
        ok = bool(raw.get("success"))
        err = None if ok else (raw.get("error") or "Muse Code failed")
        if err and re.search(r"credential|meta_api_key|muse login", err, re.I):
            err = (
                f"{err}\n\n"
                "Authenticate in Muse itself: run "
                "`muse login` / `muse auth set --api-key-stdin`."
            )
        meta = badge_meta("muse", mid, model_source, effort, effort_source)
        if raw.get("steered"):
            meta["steered"] = int(raw["steered"])
        if raw.get("undelivered_steers"):
            meta["undelivered_steers"] = list(raw["undelivered_steers"])
        # On interrupt, muse_cli_tool already packs partial text + digest into
        # output — prefer that for the chat bubble over the short error alone.
        if ok:
            display = (raw.get("output") or "").strip()
        else:
            display = (raw.get("output") or "").strip() or (err or "")
        return AgentResult(
            success=ok,
            output=display,
            error=err,
            usage=usage,
            session_id=raw.get("muse_session_id"),
            model=mid,
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
