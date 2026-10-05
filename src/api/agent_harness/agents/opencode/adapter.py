"""OpenCode CLI harness adapter (pilot).

Uses ``opencode run`` (non-interactive). See https://opencode.ai/docs/cli/
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from api.agent_harness.activity import put_status
from api.agent_harness.agents.opencode.session_store import (
    clear_opencode_resume_id,
    load_opencode_effort,
    load_opencode_model,
    load_opencode_resume_id,
    save_opencode_effort,
    save_opencode_model,
    save_opencode_resume_id,
)
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.timeouts import ActivityDeadline
from api.agent_harness.types import AgentResult
from api.agent_harness.win_cli import which_preferring_native
from scripts.utilities.agent_process import attach_to_chat_run, kill_process_tree

DEFAULT_OPENCODE_MODEL = "openrouter/z-ai/glm-5.3-flash"

# OpenCode ``--variant`` / reasoning-effort levels (model-specific; curated common set).
OPENCODE_REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_THROTTLE_SEC = 1.0
_HEARTBEAT_SEC = 4.0
# One ``--format json`` event per line; tool results (file reads, diffs) blow
# past asyncio's 64 KiB default and readline() raises mid-turn.
_STREAM_LIMIT = 64 * 1024 * 1024
_WRITING_BUF_KEY = "__writing_buf__"


def _first_line(text: Any, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _throttle_kind(activity: str) -> str:
    if activity.startswith("tool "):
        return "tool"
    if activity.startswith("writing:"):
        return "writing"
    if "thinking" in activity:
        return "thinking"
    return ""


def _opencode_tool_label(tool: str, title: str) -> str:
    title = (title or "").strip()
    if title:
        return title
    tool = (tool or "").strip()
    if tool:
        return re.sub(r"[_-]+", " ", tool).strip()
    return "tool"


def _opencode_activity_for_event(
    event: Dict[str, Any],
    state: Dict[str, Any],
) -> Optional[str]:
    """Map one ``opencode run --format json`` line to a chat status, or None."""
    if not isinstance(event, dict):
        return None
    et = (event.get("type") or "").strip()
    part = event.get("part") if isinstance(event.get("part"), dict) else {}

    if et == "step_start":
        return "OpenCode is thinking…"

    if et == "tool_use":
        state["tool_count"] = int(state.get("tool_count") or 0) + 1
        index = state["tool_count"]
        tool = str(part.get("tool") or "")
        st = part.get("state") if isinstance(part.get("state"), dict) else {}
        title = str(st.get("title") or st.get("description") or "")
        label = _opencode_tool_label(tool, title)
        return f"tool {index}: {label}"

    if et == "text":
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        buf = str(state.get(_WRITING_BUF_KEY) or "") + text
        state[_WRITING_BUF_KEY] = buf[-4000:]
        preview = buf.replace("\n", " ").strip()[-120:]
        from api.agent_harness.activity import text_preview
        return f"writing: {text_preview(buf)}" if preview else None

    if et == "error":
        err = event.get("error")
        if isinstance(err, dict):
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            msg = str(data.get("message") or err.get("name") or "").strip()
            if msg:
                return f"OpenCode error: {_first_line(msg, 140)}"
        return "OpenCode error"

    return None

OPENCODE_KNOWN_MODELS = (
    {"id": "openrouter/z-ai/glm-5.3-flash", "label": "GLM 5.3 Flash (OpenRouter)"},
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini (OpenAI)"},
    {"id": "openai/gpt-4o", "label": "GPT-4o (OpenAI)"},
    {"id": "openai/gpt-4.1-mini", "label": "GPT-4.1 mini (OpenAI)"},
    {"id": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4 (Anthropic)"},
)


def opencode_supports_variant(model: Optional[str]) -> bool:
    """Whether OpenCode will accept ``--variant`` for this model.

    Resolution order:
    1. Connector ``model_capabilities`` overlay (explicit CLI bug overrides)
    2. Baked OpenCode catalog (``variants`` map from ``opencode models --verbose``)
    3. Default True (send ``--variant``; unknown models are optimistic)
    """
    mid = (str(model or "").strip())
    if not mid:
        return True

    try:
        from api.agent_harness.catalog import get_agent
        from api.agent_harness.model_capabilities import resolve_model_supports

        pair = get_agent("opencode")
        if pair is not None:
            caps = resolve_model_supports(pair[0].model_capabilities or (), mid)
            if "variant" in caps:
                return bool(caps["variant"])
    except Exception:
        pass

    try:
        from api.agent_harness.agents.opencode.model_catalog import catalog_supports_variant

        known = catalog_supports_variant(mid)
        if known is not None:
            return bool(known)
    except Exception:
        pass
    return True


def opencode_model_label(model: Optional[str]) -> str:
    mid = (model or "").strip()
    for row in OPENCODE_KNOWN_MODELS:
        if row["id"] == mid:
            return str(row["label"])
    if mid:
        try:
            from api.agent_harness.agents.opencode.model_catalog import _label_from_id

            return _label_from_id(mid)
        except Exception:
            pass
    return mid or DEFAULT_OPENCODE_MODEL


def list_opencode_palette_models(
    *,
    refresh: bool = False,
    provider: Optional[str] = None,
    q: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Live OpenCode catalog for the slash palette (cached ``opencode models``)."""
    from api.agent_harness.agents.opencode.model_catalog import list_opencode_catalog_models

    return list_opencode_catalog_models(
        refresh=refresh, provider=provider, q=q, limit=limit
    )


def opencode_executable() -> Optional[str]:
    return which_preferring_native(
        ("opencode",),
        env_var="OPENCODE_CLI_PATH",
        packaged_relpaths=("node_modules/opencode-ai/bin/opencode.exe",),
    )


# Back-compat for tests that import the old helper name.
def _prefer_native_binary(path: str, *, is_windows: Optional[bool] = None) -> str:
    from pathlib import Path

    from api.agent_harness.win_cli import prefer_native_binary

    extras = (Path(path).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe",)
    return prefer_native_binary(path, extra_candidates=extras, is_windows=is_windows)


def _accumulate_opencode_usage(obj: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate ``step_finish`` billing totals + last-step context fill.

    ``prompt_tokens`` / ``completion_tokens`` are **sums** across steps (billing).
    ``context_tokens`` is overwritten each step with that step's ``tokens.input`` —
    the best single-call occupancy snapshot OpenCode emits (no peak field).
    """
    if (obj.get("type") or "").strip() != "step_finish":
        return
    part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
    try:
        inp = int(tokens.get("input") or 0)
        out = int(tokens.get("output") or 0)
    except (TypeError, ValueError):
        inp = out = 0
    if inp or out:
        usage["prompt_tokens"] = int(usage.get("prompt_tokens") or 0) + inp
        usage["completion_tokens"] = int(usage.get("completion_tokens") or 0) + out
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    try:
        cr = int(cache.get("read") or tokens.get("cacheRead") or 0)
    except (TypeError, ValueError):
        cr = 0
    try:
        cw = int(cache.get("write") or tokens.get("cacheWrite") or 0)
    except (TypeError, ValueError):
        cw = 0
    if cr > 0:
        usage["cache_read_tokens"] = int(usage.get("cache_read_tokens") or 0) + cr
    if cw > 0:
        usage["cache_write_tokens"] = int(usage.get("cache_write_tokens") or 0) + cw
    # Last step wins — do not sum (would inflate the context gauge like Cursor
    # cacheRead aggregates).
    if inp > 0:
        usage["context_tokens"] = inp
        usage["peak_context_tokens"] = max(
            int(usage.get("peak_context_tokens") or 0), inp
        )
    cost = part.get("cost")
    if cost is not None:
        try:
            usage["cost"] = float(usage.get("cost") or 0) + float(cost)
        except (TypeError, ValueError):
            pass


def _finalize_opencode_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    if pt or ct:
        usage["total_tokens"] = int(usage.get("total_tokens") or 0) or (pt + ct)
    return usage


def _extract_opencode_event_model(obj: Dict[str, Any]) -> Optional[str]:
    """Best-effort executed-model probe for one ``opencode run`` JSON event.

    The CLI picks its own default when ``--model`` is omitted; when an event
    carries that choice (``model`` / ``modelID`` / ``model_id`` at the top
    level or one level nested), surface it so badges and usage reflect what
    actually ran. Returns None when the event says nothing about the model.
    """
    if not isinstance(obj, dict):
        return None
    candidates: List[Any] = [
        obj.get("model"),
        obj.get("modelID"),
        obj.get("modelId"),
        obj.get("model_id"),
    ]
    for nested_key in ("session", "message", "properties", "part", "info"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            candidates.extend(
                (
                    nested.get("model"),
                    nested.get("modelID"),
                    nested.get("modelId"),
                    nested.get("model_id"),
                )
            )
    for cand in candidates:
        if isinstance(cand, dict):
            cand = cand.get("id") or cand.get("modelID")
        if not isinstance(cand, str):
            continue
        mid = cand.strip()
        # Event-type words and JSON blobs are never model ids.
        if not mid or len(mid) > 160 or any(c in mid for c in " \t\n\r{}[]\"'"):
            continue
        return mid
    return None


def _capture_opencode_question(bridge, event: Dict[str, Any]) -> bool:
    part = event.get("part") or {}
    if not isinstance(part, dict) or part.get("tool") != "question":
        return False
    state = part.get("state") or {}
    if not isinstance(state, dict) or "input" not in state:
        return False
    bridge.capture(state["input"])
    return True


def _parse_opencode_stdout(raw: str) -> Tuple[str, Optional[str], Dict[str, Any], Optional[str]]:
    """
    Prefer ``--format json`` event stream / object; fall back to plain text.

    Returns (display_text, session_id, usage, detected_model) where
    detected_model is the executed provider/model observed in the event
    stream (None when the stream carries no model metadata).
    """
    stripped = (raw or "").strip()
    if not stripped:
        return "", None, {}, None

    from api.agent_harness.questions import QuestionBridge

    questions = QuestionBridge()
    session_id: Optional[str] = None
    usage: Dict[str, Any] = {}
    texts: List[str] = []
    detected_model: Optional[str] = None

    # JSONL event stream
    if "\n" in stripped and stripped.lstrip().startswith("{"):
        for line in stripped.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                if line:
                    texts.append(line)
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                texts.append(line)
                continue
            if not isinstance(obj, dict):
                continue
            sid = obj.get("sessionID") or obj.get("session_id") or obj.get("sessionId")
            if isinstance(sid, str) and sid.strip():
                session_id = sid.strip()
            if detected_model is None:
                detected_model = _extract_opencode_event_model(obj)
            _capture_opencode_question(questions, obj)
            _accumulate_opencode_usage(obj, usage)
            # Common shapes: type=text / message / part / content
            t = obj.get("type") or obj.get("event")
            if t == "text":
                part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
                text = part.get("text") if isinstance(part.get("text"), str) else obj.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                continue
            if t in ("message", "part", "content"):
                part = obj.get("text") or obj.get("content") or obj.get("part")
                if isinstance(part, str) and part.strip():
                    texts.append(part.strip())
                elif isinstance(part, dict):
                    inner = part.get("text") or part.get("content")
                    if isinstance(inner, str) and inner.strip():
                        texts.append(inner.strip())
            elif isinstance(obj.get("text"), str) and obj["text"].strip():
                texts.append(obj["text"].strip())
            props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
            if isinstance(props.get("text"), str) and props["text"].strip():
                texts.append(props["text"].strip())
        usage = _finalize_opencode_usage(usage)
        if texts or usage or detected_model or questions.pending:
            return questions.render("\n".join(texts).strip()), session_id, usage, detected_model

    # Single JSON object
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped, None, {}, None
        if isinstance(parsed, dict):
            sid = parsed.get("sessionID") or parsed.get("session_id") or parsed.get("sessionId")
            if isinstance(sid, str) and sid.strip():
                session_id = sid.strip()
            detected_model = _extract_opencode_event_model(parsed)
            if _capture_opencode_question(questions, parsed):
                return questions.render(""), session_id, usage, detected_model
            for key in ("response", "text", "output", "message", "content"):
                val = parsed.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip(), session_id, usage, detected_model
            return stripped, session_id, usage, detected_model

    return stripped, None, {}, None


def summarize_opencode_error(raw: str, returncode: Optional[int] = None) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "not authenticated" in low or "unauthorized" in low or "auth login" in low:
        return (
            "OpenCode is not authenticated. Run `opencode auth login`, then retry."
        )
    if "api key" in low and ("missing" in low or "required" in low or "not set" in low):
        return (
            "OpenCode needs provider authentication. Run `opencode auth login`. "
            "Configure credentials in OpenCode itself, then retry."
        )
    if (
        "quota" in low
        or "rate limit" in low
        or "credit balance" in low
        or "plans & billing" in low
    ):
        return (
            "OpenCode provider quota/credits exhausted. Sync another provider via "
            "`opencode auth login`, pin `/opencode model openai/gpt-4o-mini`, and retry."
        )
    compact = re.sub(r"\s+", " ", text)
    if compact:
        return compact[:2000]
    return f"OpenCode failed (exit {returncode})." if returncode is not None else "OpenCode failed."


def _handle_opencode_model_slash(
    prompt: str, chat_session_id: Optional[str], active_model: str
) -> Optional[AgentResult]:
    """Native `/opencode model …` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'")
    # Bare "model the login flow" is a task, not a model switch.
    if not match.group(1) and arg and not re.match(
        r"^(openrouter/|openai/|anthropic/|opencode/|deepseek/|google/|x-ai/"
        r"|default$|reset$|clear$|refresh$|reload$|sync$)",
        arg,
        flags=re.I,
    ):
        return None
    if arg.lower() in ("refresh", "reload", "sync"):
        from api.agent_harness.agents.opencode.model_catalog import refresh_opencode_catalog

        result = refresh_opencode_catalog()
        count = int(result.get("count") or 0)
        err = result.get("error")
        if err and count == 0:
            return AgentResult(
                success=False,
                output="",
                error=f"OpenCode model refresh failed: {err}",
                model=active_model,
            )
        note = f" ({err})" if err else ""
        pricing_note = ""
        if result.get("pricing_refreshed") and result.get("pricing_count"):
            pricing_note = (
                f" Also refreshed models.dev pricing "
                f"({int(result.get('pricing_count') or 0)} rates) for bubble cost estimates."
            )
        elif result.get("pricing_error"):
            pricing_note = f" Pricing refresh skipped: {result.get('pricing_error')}."
        return AgentResult(
            success=True,
            output=(
                f"**OpenCode:** Refreshed model catalog from models.dev — "
                f"{count} models available in the `/` palette.{note}"
                f"{pricing_note}\n\n"
                "Filter in chat (with an OpenCode badge): type `deepseek`, "
                "`glm`, `openrouter`, … then pick a row. "
                "Or pin directly: `/opencode model openrouter/deepseek/deepseek-v4.1-flash`."
            ),
            model=active_model,
            meta={
                "agent_model": active_model,
                "opencode_catalog_count": count,
                "opencode_catalog_source": result.get("source"),
            },
        )
    if not arg or arg.lower() in ("list", "ls", "?"):
        from api.agent_harness.agents.opencode.model_catalog import (
            list_opencode_catalog_models,
        )

        catalog = list_opencode_catalog_models(limit=12)
        lines = [
            "**OpenCode models** (pin with `/opencode model <id>`):",
            "",
            "The chat `/` palette loads the live OpenCode catalog "
            f"({int(catalog.get('count') or 0)} models, source `{catalog.get('source')}`). "
            "Type to filter (e.g. `deepseek`, `glm`). "
            "Refresh with `/opencode model refresh`.",
            "",
            "Favorites / sample:",
        ]
        for known in catalog.get("models") or OPENCODE_KNOWN_MODELS:
            kid = str((known.get("id") if isinstance(known, dict) else known) or "")
            if not kid and isinstance(known, dict):
                continue
            label = (
                str(known.get("label") or kid)
                if isinstance(known, dict)
                else opencode_model_label(str(known))
            )
            mid = kid if isinstance(known, dict) else str(known)
            mark = " ✅ current" if mid == active_model else ""
            lines.append(f"- `{mid}` — {label}{mark}")
        pinned = load_opencode_model(chat_session_id)
        if pinned:
            lines.append("")
            lines.append(
                f"Pinned for this chat: `{pinned}` ({opencode_model_label(pinned)})."
            )
        else:
            lines.append("")
            if active_model:
                lines.append(
                    f"No pin — using `{active_model}` ({opencode_model_label(active_model)})."
                )
            else:
                lines.append("No pin — using the OpenCode CLI default (opencode.json).")
        return AgentResult(success=True, output="\n".join(lines), model=active_model)

    if arg.lower() in ("default", "reset", "clear"):
        save_opencode_model(chat_session_id, None)
        from api.agent_harness.agent_defaults import get_starred_model as _starred

        _s = _starred("opencode")
        _back = _s or "the OpenCode CLI default (opencode.json)"
        return AgentResult(
            success=True,
            output=f"**OpenCode:** Model reset to {_back}.",
            model=_s or "",
            meta={
                "agent_model": _s or "",
                "model_source": ("starred" if _s else "cli_default"),
            },
        )

    saved = save_opencode_model(chat_session_id, arg) or arg
    return AgentResult(
        success=True,
        output=(
            f"**OpenCode:** Model set to `{saved}` "
            f"({opencode_model_label(saved)}) for this chat."
        ),
        model=saved,
        meta={
            "agent_model": saved,
            "model_source": "session",
        },
    )


def _handle_opencode_effort_slash(
    prompt: str,
    chat_session_id: Optional[str],
    model: Optional[str] = None,
) -> Optional[AgentResult]:
    """Native ``/opencode effort …`` without starting a CLI turn."""
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    known = [str(e) for e in OPENCODE_REASONING_EFFORTS]
    # Bare "effort the login flow" is a task, not an effort switch.
    if not match.group(1) and arg and arg not in known and arg not in (
        "list", "ls", "?", "default", "reset", "clear",
    ):
        return None
    from api.agent_harness.agent_defaults import (
        get_starred_effort as _se_o,
        resolve_effective_model as _resolve_mo,
    )

    active_model, _model_source = _resolve_mo(
        "opencode",
        session_model=load_opencode_model(chat_session_id),
        kernel_override=model,
        cli_default=None,
    )
    active_model = active_model or ""
    base_meta = {
        "agent_model": active_model,
        "model_source": _model_source,
    }
    current = load_opencode_effort(chat_session_id)
    resolved_effort = current or _se_o("opencode")
    if resolved_effort:
        base_meta["agent_effort"] = resolved_effort
        base_meta["effort_source"] = "session" if current else "starred"
    if not arg or arg in ("list", "ls", "?"):
        lines = [
            "**OpenCode reasoning effort** (pin with `/opencode effort <level>`):",
            "",
        ]
        for level in known:
            mark = " ✅ current" if level == current else ""
            lines.append(f"- `{level}`{mark}")
        lines.append("")
        if current:
            lines.append(f"Pinned for this chat: `{current}`.")
        elif resolved_effort:
            lines.append(f"Starred default for new chats: `{resolved_effort}`.")
        else:
            lines.append("No pin — using the OpenCode CLI default for the model.")
        return AgentResult(
            success=True,
            output="\n".join(lines),
            model=active_model,
            meta=dict(base_meta),
        )

    if arg in ("default", "reset", "clear"):
        save_opencode_effort(chat_session_id, None)
        back = _se_o("opencode")
        return AgentResult(
            success=True,
            output=(
                f"**OpenCode:** Reasoning effort reset to starred default `{back}`."
                if back
                else "**OpenCode:** Reasoning effort reset to the CLI default."
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
            success=False,
            error=f"Unknown effort `{arg}`. Pick one of: {', '.join(known)}.",
            output=f"Unknown effort `{arg}`. Pick one of: {', '.join(f'`{e}`' for e in known)}.",
            model=active_model,
            meta=base_meta,
        )

    save_opencode_effort(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**OpenCode:** Reasoning effort set to `{arg}` for this chat.",
        model=active_model,
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
    )


class Adapter:
    def available(self) -> bool:
        return bool(opencode_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        return load_opencode_resume_id(cwd, chat_session_id)

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        save_opencode_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        clear_opencode_resume_id(cwd, chat_session_id)

    def handle_meta(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str],
        model: Optional[str],
    ) -> Optional[AgentResult]:
        """Handle model/effort/usage commands before Context Compiler wraps the user prompt."""
        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("opencode", prompt)
        if usage_md is not None:
            from api.agent_harness.agent_defaults import get_starred_model as _starred3

            active = load_opencode_model(chat_session_id) or (
                (str(model).strip() if model else "") or _starred3("opencode") or ""
            )
            return AgentResult(success=True, output=usage_md, model=active or "")

        effort_result = _handle_opencode_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        from api.agent_harness.agent_defaults import get_starred_model as _starred3

        active = load_opencode_model(chat_session_id) or (
            (str(model).strip() if model else "") or _starred3("opencode") or ""
        )
        return _handle_opencode_model_slash(prompt, chat_session_id, active)

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
        reasoning_effort: Optional[str] = None,
    ) -> AgentResult:
        exe = opencode_executable()
        if not exe:
            return AgentResult(
                success=False,
                error="opencode CLI not found on PATH",
                output="",
            )
        if not (prompt or "").strip():
            return AgentResult(success=False, error="No prompt provided", output="")

        # Policy order: per-chat pin → explicit override → starred → CLI
        # default (omit --model so opencode.json wins). Never inject
        # DEFAULT_OPENCODE_MODEL onto the argv.
        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_SESSION,
            SOURCE_STARRED,
            get_starred_effort,
            get_starred_model,
        )

        session_model = load_opencode_model(chat_session_id)
        _override = (str(model).strip() if model else "") or None
        _starred = get_starred_model("opencode")
        mid = session_model or _override or _starred or None
        if session_model:
            _model_source = SOURCE_SESSION
        elif _override:
            _model_source = SOURCE_OVERRIDE
        elif _starred:
            _model_source = SOURCE_STARRED
        else:
            _model_source = SOURCE_CLI_DEFAULT
        _sess_effort = load_opencode_effort(chat_session_id)
        _star_effort = get_starred_effort("opencode")
        _override_effort = (str(reasoning_effort).strip().lower() if reasoning_effort else "")
        effort = _override_effort or _sess_effort or _star_effort or None
        _effort_source = (
            SOURCE_OVERRIDE
            if _override_effort
            else ("session" if _sess_effort else ("starred" if _star_effort else "none"))
        )

        put_status(status_queue, "Calling OpenCode…")
        cmd: List[str] = [exe, "run", "--format", "json", "--auto"]
        r = (resume or "").strip()
        if r:
            cmd.extend(["--session", r])
        if mid:
            cmd.extend(["--model", mid])
        _effort_ignored = None
        if effort:
            # Skip ``--variant`` when catalog/manifest says this model has no
            # variants map (see model_catalog bake + optional overlays).
            if not opencode_supports_variant(mid):
                _effort_ignored = (
                    f"`{mid}` does not support `--variant`; effort `{effort}` stored "
                    "for badges but not sent to the CLI."
                )
            else:
                # OpenCode maps ``--variant`` to provider reasoning-effort overlays.
                cmd.extend(["--variant", effort])

        from api.agent_harness.questions import QuestionBridge

        pending_questions = QuestionBridge()
        activity_state: Dict[str, Any] = {"tool_count": 0}
        started_at = time.monotonic()
        last_emit = [started_at]
        last_activity = ["starting"]
        from api.agent_harness.activity import TextActivityLog
        text_log = TextActivityLog("opencode")

        def _emit(activity: str) -> None:
            now = time.monotonic()
            if activity == last_activity[0]:
                return
            kind = _throttle_kind(activity)
            if (
                kind
                and kind == _throttle_kind(last_activity[0])
                and (now - last_emit[0]) < _THROTTLE_SEC
            ):
                return
            last_emit[0] = now
            last_activity[0] = activity
            put_status(status_queue, activity, preview_only=activity.startswith(("writing:", "thinking:")))

        stop = asyncio.Event()

        async def _heartbeat() -> None:
            if status_queue is None:
                return
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_SEC)
                    return
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - last_emit[0] < _HEARTBEAT_SEC:
                        continue
                    last_emit[0] = now
                    elapsed = int(now - started_at)
                    put_status(
                        status_queue,
                        f"OpenCode working… {elapsed}s ({last_activity[0]})",
                    )

        hb = asyncio.create_task(_heartbeat())
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=agent_cli_env(),
                limit=_STREAM_LIMIT,
            )
            attach_to_chat_run(chat_session_id, proc)

            # Activity-aware budget: the kernel's `timeout` is an *idle* budget
            # (deadline resets whenever the CLI emits output), with an absolute
            # runaway cap on top. Long productive turns are not killed.
            deadline = ActivityDeadline(timeout)

            async def _pump_err() -> None:
                # stderr activity also counts as progress; buffer for errors.
                try:
                    while True:
                        chunk = await proc.stderr.readline()
                        if not chunk:
                            return
                        deadline.poke()
                        err_buf.append(chunk)
                except Exception:
                    return

            async def _feed_stdin() -> None:
                try:
                    proc.stdin.write(prompt.encode("utf-8"))
                    await proc.stdin.drain()
                    proc.stdin.close()
                except Exception:
                    return

            err_buf: List[bytes] = []
            out_buf: List[bytes] = []
            stdin_task = asyncio.create_task(_feed_stdin())
            err_task = asyncio.create_task(_pump_err())

            timed_out: Optional[str] = None
            try:
                if cancel_event is not None and cancel_event.is_set():
                    await kill_process_tree(proc)
                    return AgentResult(
                        success=False,
                        error="Cancelled",
                        output="[CANCELLED] OpenCode run was cancelled.",
                        model=mid,
                    )
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        await kill_process_tree(proc)
                        return AgentResult(
                            success=False,
                            error="Cancelled",
                            output="[CANCELLED] OpenCode run was cancelled.",
                            model=mid,
                        )
                    expiry = deadline.check()
                    if expiry:
                        timed_out = expiry
                        await kill_process_tree(proc)
                        break
                    poll = deadline.next_wake(poll=1.0)
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=poll)
                    except asyncio.TimeoutError:
                        continue
                    if not line:
                        break  # stdout EOF — process finished
                    deadline.poke()
                    out_buf.append(line)
                    try:
                        ev = json.loads(line.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeError):
                        continue
                    if not isinstance(ev, dict):
                        continue
                    if _capture_opencode_question(pending_questions, ev):
                        _emit("Preparing Cuttle question form…")
                        await kill_process_tree(proc)
                        break
                    part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
                    if ev.get("type") in ("text", "reasoning"):
                        kind = "writing" if ev["type"] == "text" else "thinking"
                        text = str(part.get("text") or "")
                        if not part.get("id"):
                            text = text_log.delta(kind, text)
                        text_log.save(kind, text, part.get("id"))
                    if status_queue is not None:
                        activity = _opencode_activity_for_event(ev, activity_state)
                        if activity:
                            _emit(activity)

                await stdin_task
                await proc.wait()
            finally:
                # An exception out of the read loop must not orphan the CLI: it
                # stays attached to the chat run and blocks when-idle restarts.
                if proc.returncode is None:
                    try:
                        await kill_process_tree(proc)
                    except Exception:
                        pass
                err_task.cancel()
                stdin_task.cancel()
                for t in (stdin_task, err_task):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

            if timed_out:
                partial = _parse_opencode_stdout(
                    b"".join(out_buf).decode("utf-8", errors="replace")
                )[0]
                return AgentResult(
                    success=False,
                    error=f"OpenCode {timed_out}",
                    output=partial,
                    model=mid,
                )
        finally:
            text_log.flush()
            stop.set()
            try:
                await asyncio.wait_for(hb, timeout=1.0)
            except Exception:
                hb.cancel()

        out = b"".join(out_buf).decode("utf-8", errors="replace")
        err = b"".join(err_buf).decode("utf-8", errors="replace")
        display, sid, usage, detected = _parse_opencode_stdout(out)
        ok = proc.returncode == 0 or pending_questions.pending
        from api.agent_harness.agent_defaults import badge_meta as _badge_meta

        # Precedence: per-chat pin → explicit override → starred → executed
        # model observed in the event stream → unknown (never config-guess:
        # execution metadata wins over opencode.json).
        if mid:
            effective_model, effective_source = mid, _model_source
        elif detected:
            effective_model, effective_source = detected, SOURCE_CLI_DEFAULT
        else:
            effective_model, effective_source = "", "unknown"
        meta = _badge_meta("opencode", effective_model, effective_source, effort, _effort_source)
        if _effort_ignored:
            meta["effort_ignored"] = _effort_ignored
            display = (display + "\n\n_" + _effort_ignored + "_").strip() if display else ("_" + _effort_ignored + "_")
        if not ok:
            merged = display
            if err.strip():
                merged = (display + "\n\n" + err.strip()).strip() if display else err.strip()
            return AgentResult(
                success=False,
                output=merged,
                error=summarize_opencode_error(
                    err.strip() or display,
                    proc.returncode,
                ),
                usage=usage,
                session_id=sid,
                model=effective_model,
                meta=meta,
            )
        return AgentResult(
            success=True,
            output=display or out.strip() or "Done.",
            usage=usage,
            session_id=sid,
            model=effective_model,
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
