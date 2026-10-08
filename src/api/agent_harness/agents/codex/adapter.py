"""Codex CLI harness adapter — wraps ``scripts.utilities.codex_cli_tool``.

Per-chat ``/codex model`` + ``/codex effort`` pins mirror Muse / OpenCode badge-
gated palette controls. Refresh uses ``codex debug models``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from api.agent_harness.activity import put_status
from api.agent_harness.agents.codex.model_catalog import (
    codex_efforts_for_model,
    codex_model_label,
    list_codex_catalog_models,
    refresh_codex_catalog,
)
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult


def _handle_codex_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    """Native ``/codex model …`` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    from scripts.utilities.codex_cli_session_store import (
        load_codex_model,
        save_codex_model,
    )

    arg = (match.group(2) or "").strip().strip('"').strip("'")
    # Bare "model the login flow" is a task, not a model switch.
    if not match.group(1) and arg and not re.match(
        r"^(gpt-|o[0-9]|codex-|openai/|default$|reset$|clear$|refresh$|reload$|sync$)",
        arg,
        flags=re.I,
    ):
        known_ids = {
            str(m.get("id") or "").lower()
            for m in (list_codex_catalog_models().get("models") or [])
            if isinstance(m, dict)
        }
        if arg.lower() not in known_ids and "/" not in arg:
            return None

    if arg.lower() in ("refresh", "reload", "sync"):
        result = refresh_codex_catalog()
        count = int(result.get("count") or 0)
        err = result.get("error")
        source = result.get("source") or "unknown"
        if err and source == "static_fallback" and count == 0:
            return AgentResult(
                success=False,
                output="",
                error=f"Codex model refresh failed: {err}",
                model=active_model,
            )
        note = ""
        if err and source == "static_fallback":
            note = f" (CLI catalog unavailable — {err}; showing soft-known list)"
        elif source.startswith("cli"):
            note = " from `codex debug models`"
        return AgentResult(
            success=True,
            output=(
                f"**Codex:** Refreshed model catalog{note} — "
                f"{count} models available in the `/` palette.\n\n"
                "Filter in chat (with a Codex badge): type `sol`, `luna`, "
                "`5.6`, … then pick a row. "
                "Each model row includes its supported effort levels. "
                "Refresh reloads models and effort support from `codex debug models`; "
                "the manifest is the offline fallback."
            ),
            model=active_model,
            meta={
                "agent_model": active_model,
                "codex_catalog_count": count,
                "codex_catalog_source": source,
            },
        )

    if not arg or arg.lower() in ("list", "ls", "?"):
        catalog = list_codex_catalog_models()
        lines = [
            "**Codex models** (pin with `/codex model <id>`):",
            "",
            f"Palette catalog: {int(catalog.get('count') or 0)} models "
            f"(source `{catalog.get('source')}`). "
            "Refresh with `/codex model refresh`.",
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
        pinned = load_codex_model(chat_session_id)
        if pinned:
            lines.append("")
            lines.append(
                f"Pinned for this chat: `{pinned}` ({codex_model_label(pinned)})."
            )
        else:
            lines.append("")
            lines.append(
                f"No pin — using `{active_model or 'CLI default'}`"
                + (f" ({codex_model_label(active_model)})" if active_model else "")
                + "."
            )
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        from api.agent_harness.agent_defaults import get_starred_model as _sm

        save_codex_model(chat_session_id, None)
        back = _sm("codex")
        default = back or ""
        return AgentResult(
            success=True,
            output=(
                f"**Codex:** Model reset to starred default `{back}`."
                if back
                else "**Codex:** Model pin cleared — using the Codex CLI default."
            ),
            model=default,
            meta={
                "agent_model": default,
                "model_source": ("starred" if back else "cli_default"),
            },
        )

    saved = save_codex_model(chat_session_id, arg) or arg
    return AgentResult(
        success=True,
        output=(
            f"**Codex:** Model set to `{saved}` "
            f"({codex_model_label(saved)}) for this chat."
        ),
        model=saved,
        meta={
            "agent_model": saved,
            "model_source": "session",
        },
    )


def _handle_codex_effort_slash(
    prompt: str,
    chat_session_id: Optional[str],
    model: Optional[str] = None,
) -> Optional[AgentResult]:
    """Native ``/codex effort …`` without starting a CLI turn."""
    from scripts.utilities.codex_cli_session_store import (
        load_codex_effort,
        load_codex_model,
        save_codex_effort,
    )
    from api.agent_harness.agent_defaults import (
        resolve_effective_effort as _resolve_effort,
        resolve_effective_model as _resolve,
    )

    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None

    active, _src = _resolve(
        "codex",
        session_model=load_codex_model(chat_session_id),
        kernel_override=model,
        cli_default="",
    )
    known = codex_efforts_for_model(active or None)
    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    pinned = load_codex_effort(chat_session_id) or ""
    effective_effort, effort_source = _resolve_effort(
        "codex", session_effort=pinned
    )
    base_meta = {
        "agent_model": active or "",
        "agent_effort": effective_effort or "",
        "effort_source": effort_source,
    }

    if not arg or arg in ("list", "ls", "?"):
        lines = [
            "**Codex reasoning effort** (pin with `/codex effort <level>`):",
            "",
            "Maps to Codex `-c model_reasoning_effort=\"…\"`.",
            "",
        ]
        if active:
            lines.insert(2, f"Model `{active}` supports:")
        else:
            lines.insert(2, "With no model pin, showing levels common to the CLI catalog:")
        for e in known:
            mark = " ✅ current" if e == effective_effort else ""
            lines.append(f"- `{e}`{mark}")
        if not known:
            lines.append(
                "No verified effort levels are available for this model. "
                "Refresh with `/codex model refresh` or clear the model pin."
            )
        if pinned:
            lines.append("")
            lines.append(f"Pinned for this chat: `{pinned}`.")
        elif effective_effort:
            lines.append("")
            lines.append(f"Starred default for new chats: `{effective_effort}`.")
        else:
            lines.append("")
            lines.append("No pin — Codex uses its config default.")
        return AgentResult(
            success=True,
            output="\n".join(lines),
            model=active or "",
            meta=dict(base_meta),
        )

    if arg in ("default", "reset", "clear", "none"):
        from api.agent_harness.agent_defaults import get_starred_effort as _se

        save_codex_effort(chat_session_id, None)
        back = _se("codex") or ""
        return AgentResult(
            success=True,
            output=(
                f"**Codex:** Effort reset to starred default `{back}`."
                if back
                else "**Codex:** Effort pin cleared — using the Codex CLI default."
            ),
            model=active or "",
            meta={
                **{k: v for k, v in base_meta.items() if k not in ("agent_effort", "effort_source")},
                "agent_effort": back,
                "effort_source": ("starred" if back else "none"),
            },
        )

    if arg not in known:
        supported = (
            f"Supported by `{active}`: "
            if active and known
            else "Common supported levels: "
            if known
            else "No verified effort levels are known for this model. "
        )
        return AgentResult(
            success=True,
            output=(
                f"Effort `{arg}` is not supported for the current Codex model. "
                + (
                    supported + ", ".join(f"`{e}`" for e in known) + "."
                    if known
                    else supported + "Try `/codex model refresh` for current CLI metadata."
                )
            ),
            model=active or "",
            meta=dict(base_meta),
        )

    save_codex_effort(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**Codex:** Reasoning effort set to `{arg}` for this chat.",
        model=active or "",
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
    )


class Adapter:
    def available(self) -> bool:
        from scripts.utilities.codex_cli_tool import codex_executable

        return bool(codex_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        from scripts.utilities.codex_cli_session_store import load_codex_resume_id

        return load_codex_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        from scripts.utilities.codex_cli_session_store import save_codex_resume_id

        save_codex_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        from scripts.utilities.codex_cli_session_store import clear_codex_resume_id

        clear_codex_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str],
        model: Optional[str],
    ) -> Optional[AgentResult]:
        """Handle model/effort/usage commands before Context Compiler wraps the prompt."""
        from api.agent_harness.agent_defaults import resolve_effective_model as _resolve
        from scripts.utilities.codex_cli_session_store import load_codex_model

        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("codex", prompt)
        if usage_md is not None:
            active, _ = _resolve(
                "codex",
                session_model=load_codex_model(chat_session_id),
                kernel_override=model,
                cli_default="",
            )
            return AgentResult(success=True, output=usage_md, model=active or "")

        effort_result = _handle_codex_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        active, _ = _resolve(
            "codex",
            session_model=load_codex_model(chat_session_id),
            kernel_override=model,
            cli_default="",
        )
        return _handle_codex_model_slash(prompt, chat_session_id, active or "")

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
        from scripts.utilities.codex_cli_tool import CodexCliTool, usage_for_query_report
        from scripts.utilities.codex_cli_session_store import (
            load_codex_effort,
            load_codex_model,
        )

        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_SESSION,
            SOURCE_STARRED,
            badge_meta,
            get_starred_effort,
            get_starred_model,
            resolve_effective_model,
        )

        session_model = load_codex_model(chat_session_id)
        mid, _model_source = resolve_effective_model(
            "codex",
            session_model=session_model,
            kernel_override=model,
            cli_default="",
        )
        if not mid:
            _override = (str(model).strip() if model else "") or None
            _starred = get_starred_model("codex")
            mid = _override or _starred or None
            _model_source = (
                SOURCE_OVERRIDE
                if _override
                else (SOURCE_STARRED if _starred else SOURCE_CLI_DEFAULT)
            )
        elif session_model and mid == session_model:
            _model_source = SOURCE_SESSION

        sess_effort = load_codex_effort(chat_session_id)
        star_effort = get_starred_effort("codex")
        effort = (
            (str(reasoning_effort).strip() if reasoning_effort else "")
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
            effort = str(effort).strip().lower()
            supported_efforts = codex_efforts_for_model(mid)
            if effort not in supported_efforts:
                allowed = ", ".join(f"`{level}`" for level in supported_efforts)
                detail = (
                    f"Supported levels for `{mid}`: {allowed}."
                    if supported_efforts
                    else "Cuttle has no verified effort list for this model; refresh the Codex model catalog."
                )
                return AgentResult(
                    success=True,
                    output=(
                        f"Codex was not started: effort `{effort}` is not supported for "
                        f"`{mid or 'the CLI default'}`. {detail} "
                        "Change the effort pin or reset it to the CLI default."
                    ),
                    model=mid or "",
                    meta={
                        "agent_model": mid or "",
                        "agent_effort": effort,
                        "effort_source": effort_source,
                    },
                )

        # Status strip is owned by CodexCliTool (Cursor-style tool/thinking lines +
        # silent contextual heartbeat). Do not run a coarse adapter heartbeat here —
        # it stomps live activity with "Codex working… Ns".
        put_status(status_queue, "Calling Codex…")
        from api.agent_harness.steer import steer_enabled

        raw = None
        if steer_enabled("codex"):
            from scripts.utilities.codex_app_server_turn import run_codex_turn_app_server

            raw = await run_codex_turn_app_server(
                prompt,
                cwd=cwd,
                resume=resume,
                model=mid,
                reasoning_effort=effort,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                cancel_event=cancel_event,
            )
            if raw.get("fallback"):
                print(f"[CODEX] app-server unavailable, using exec: {raw.get('error')}", flush=True)
                raw = None
        if raw is None:
            cx = CodexCliTool(
                model=mid,
                reasoning_effort=effort,
            )
            raw = await cx.execute_prompt(
                prompt,
                cwd=cwd,
                resume=resume,
                status_queue=status_queue,
                chat_session_id=chat_session_id,
                timeout=timeout,
                cancel_event=cancel_event,
            )

        usage_raw = raw.get("usage") or {}
        uq = usage_for_query_report(usage_raw, mid or "codex")
        usage = {
            "prompt_tokens": int(uq.get("input_tokens") or 0),
            "completion_tokens": int(uq.get("output_tokens") or 0),
            "total_tokens": int(uq.get("total_tokens") or 0),
            "model": mid or "codex",
            "cache_inclusive": True,
        }
        if uq.get("cache_read_tokens"):
            usage["cache_read_tokens"] = int(uq.get("cache_read_tokens") or 0)
        if uq.get("cache_write_tokens"):
            usage["cache_write_tokens"] = int(uq.get("cache_write_tokens") or 0)
        # Codex CLI reports tokens only, never cost — estimate the API price
        # from the turn totals. Codex input_tokens include cached reads
        # (OpenAI-style).
        try:
            from api.model_pricing import attach_estimated_cost

            attach_estimated_cost(usage, mid or "codex", cache_inclusive=True)
        except Exception:
            pass
        ok = bool(raw.get("success"))
        err = None if ok else (raw.get("error") or "Codex CLI failed")
        meta = badge_meta("codex", mid or "", _model_source)
        if effort:
            meta["agent_effort"] = effort
            meta["effort_source"] = effort_source

        if raw.get("steered"):
            meta["steered"] = int(raw["steered"])
        if raw.get("compacted"):
            meta["context_compacted"] = True

        # Refresh true window occupancy from app-server (last ≠ turn billing totals).
        tid = raw.get("codex_session_id") or resume
        if ok and tid and chat_session_id:
            try:
                from scripts.utilities.codex_app_server import (
                    fetch_codex_thread_token_usage,
                )
                from scripts.utilities.codex_cli_session_store import (
                    save_codex_context_snapshot,
                )
                import time as _time

                if isinstance(raw.get("token_usage"), dict):
                    live = {"success": True, "token_usage": raw["token_usage"]}
                else:
                    live = fetch_codex_thread_token_usage(
                        str(tid), cwd=cwd, timeout=45.0
                    )
                tu = (
                    live.get("token_usage")
                    if isinstance(live.get("token_usage"), dict)
                    else None
                )
                if live.get("success") and tu:
                    save_codex_context_snapshot(
                        chat_session_id,
                        {
                            "context_tokens": int(
                                tu.get("context_tokens")
                                or tu.get("prompt_tokens")
                                or 0
                            ),
                            "model_context_window": int(
                                tu.get("model_context_window") or 0
                            ),
                            "ts": _time.time(),
                            "thread_id": str(tid),
                        },
                    )
            except Exception:
                pass

        return AgentResult(
            success=ok,
            output=(raw.get("output") or "").strip()
            if ok
            else ((raw.get("output") or "").strip() or (err or "")),
            error=err,
            usage=usage,
            session_id=raw.get("codex_session_id"),
            model=mid or "",
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
