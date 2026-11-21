"""Google Antigravity CLI adapter using documented headless stream-json mode.

Per-chat ``/antigravity model`` + ``/antigravity effort`` pins mirror the Codex /
Claude badge-gated palette controls and map to ``--model <family>`` /
``--effort <level>``. Refresh re-reads ``agy models`` (no turn runs). Quota is
``/antigravity usage`` (headless ``/usage`` + ``/credits`` JSON, no turn).

Transport: the prompt goes over stdin as one ``--input-format stream-json``
user event (lossless; no argv limits), and stdin closes so the CLI exits after
that turn. Headless runs have no approval prompt, so — like Codex
(``--dangerously-bypass-approvals-and-sandbox``) and Claude
(``bypassPermissions``) — turns run with ``--dangerously-skip-permissions``;
otherwise every edit or command needing approval is soft-denied while the run
still exits 0.

Not wired, on purpose: steering (stdin messages sent mid-turn queue as the next
turn, which Cuttle's follow-up queue already does), native question recovery
(``ask_question`` is exported as ``step_type: unknown`` without arguments) and
background text helpers (agy has no way to disable its tools, which the
restricted helper contract requires).
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from api.agent_harness.activity import put_status
from .stream import AntigravityStream
from api.agent_harness.agents.antigravity.model_catalog import (
    antigravity_efforts_for_model,
    antigravity_model_label,
    list_antigravity_catalog_models,
    refresh_antigravity_catalog,
    split_effort_slug,
)
from api.agent_harness.agents.antigravity.session_store import (
    clear_antigravity_resume_id,
    load_antigravity_effort,
    load_antigravity_model,
    load_antigravity_resume_id,
    save_antigravity_context,
    save_antigravity_effort,
    save_antigravity_model,
    save_antigravity_resume_id,
)
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult
from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    run_interruptible,
)

_EFFORT_WORDS = ("low", "medium", "high", "xhigh", "max")
_RESET_WORDS = ("default", "reset", "clear", "none")
_AUTH_OK_TTL_SEC = 600.0
_auth_ok_at = 0.0


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


def _conversations_dir() -> Path:
    override = (os.environ.get("CUTTLE_AGY_CONVERSATIONS_DIR") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "antigravity-cli" / "conversations"


def conversation_exists(conversation_id: Optional[str]) -> bool:
    """False only when the CLI's store is readable and lacks this id.

    ``agy --conversation <missing>`` warns and silently starts a new
    conversation, so a stale id would run a turn without memory *and* without
    the Cuttle briefing (the kernel skips it when a resume exists).
    """
    cid = str(conversation_id or "").strip()
    if not cid:
        return False
    folder = _conversations_dir()
    if not folder.is_dir():
        return True
    return (folder / f"{cid}.db").is_file()


def summarize_antigravity_error(raw: str, returncode: Optional[int] = None, *, model: str = "") -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "authentication required" in low or "not authenticated" in low or "sign in" in low:
        return (
            "Antigravity is not signed in. Run `agy` once in a terminal and finish the "
            "Google sign-in (or configure its Gemini API-key provider), then retry."
        )
    if "gemini_api_key" in low and ("missing" in low or "not set" in low):
        return (
            "Antigravity is configured for Gemini API-key auth but GEMINI_API_KEY is not set. "
            "Configure authentication in Antigravity itself or remove the `modelProvider` override."
        )
    if "invalid model selection" in low or "conflicts with --effort" in low:
        return (
            f"Antigravity rejected the model/effort pair{f' for `{model}`' if model else ''}. "
            "Pick a model with `/antigravity model` and a level it lists with `/antigravity effort`."
        )
    if "not_found" in low and "404" in low:
        target = f"`{model}`" if model else "the CLI's default model (its saved `/model`)"
        return (
            f"Antigravity's backend returned 404 NOT_FOUND for {target} (seen transiently). "
            "Retry, or pin another with `/antigravity model <id>`."
        )
    if "resource_exhausted" in low or ("quota" in low and ("exhaust" in low or "exceed" in low)) or (
        "credits" in low and ("exhaust" in low or "insufficient" in low)
    ):
        return (
            "Antigravity quota or credits are exhausted. Check `/antigravity usage` "
            "and retry after the window resets, or pin a model from another group."
        )
    if "permission" in low and ("denied" in low or "approval" in low):
        return (
            "Antigravity could not obtain a headless tool permission. Allow the specific action "
            "in Antigravity settings, or run a less privileged task."
        )
    compact = re.sub(r"\s+", " ", re.sub(r"(?m)^AGY_ERROR: .*$", "", text))
    if compact:
        return compact[:2000]
    return f"Antigravity CLI failed (exit {returncode})." if returncode is not None else "Antigravity CLI failed."


def normalize_antigravity_usage(raw: Any) -> Dict[str, Any]:
    """Map Antigravity ``usage`` (input excludes cache reads) to Cuttle keys."""
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

    prompt = _pick("prompt_tokens", "input_tokens", "promptTokenCount", "inputTokens")
    completion = _pick("completion_tokens", "output_tokens", "candidatesTokenCount", "outputTokens")
    thinking = _pick("thinking_tokens", "thoughtsTokenCount", "thinkingTokens")
    total = _pick("total_tokens", "totalTokenCount", "totalTokens")
    if total <= 0 and (prompt or completion):
        total = prompt + completion
    cache_read = _pick("cache_read_tokens", "cacheReadTokens", "cachedContentTokenCount")
    cache_write = _pick("cache_write_tokens", "cacheWriteTokens")
    out: Dict[str, Any] = {}
    if prompt:
        out["prompt_tokens"] = prompt
    if completion:
        out["completion_tokens"] = completion
    if thinking:
        # Already inside output_tokens (21 output = 19 thinking + 2 text).
        out["reasoning_tokens"] = thinking
    if total:
        out["total_tokens"] = total
    if cache_read:
        out["cache_read_tokens"] = cache_read
    if cache_write:
        out["cache_write_tokens"] = cache_write
    if out:
        out["cache_inclusive"] = False
    return out


async def _authentication_preflight(exe: str, cwd: str) -> Optional[str]:
    """``agy models`` exits 0 while signed out: read its text, cache success."""
    global _auth_ok_at
    if time.monotonic() - _auth_ok_at < _AUTH_OK_TTL_SEC:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=agent_cli_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
    except (OSError, asyncio.TimeoutError):
        return None
    raw = (
        stdout.decode("utf-8", errors="replace")
        + "\n"
        + stderr.decode("utf-8", errors="replace")
    ).strip()
    low = raw.lower()
    if "sign in" in low or "authentication required" in low or "not authenticated" in low:
        return summarize_antigravity_error(raw, proc.returncode)
    if proc.returncode == 0 and "\t" in raw:
        _auth_ok_at = time.monotonic()
    return None


# ── meta commands (no CLI turn) ──────────────────────────────────────────────

def _active_model(chat_session_id: Optional[str], model: Optional[str] = None) -> str:
    from api.agent_harness.agent_defaults import resolve_effective_model

    active, _src = resolve_effective_model(
        "antigravity",
        session_model=load_antigravity_model(chat_session_id),
        kernel_override=model,
        cli_default="",
    )
    return active or ""


def _handle_model_slash(prompt: str, chat_session_id: Optional[str], active: str) -> Optional[AgentResult]:
    raw = (prompt or "").strip()
    match = re.match(r"^(/?)models?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'")
    if not match.group(1) and arg and not re.match(
        r"^(gemini-|claude-|gpt-|default$|reset$|clear$|refresh$|reload$|sync$|list$|ls$|\?$)",
        arg, flags=re.I,
    ):
        known = {str(m.get("id") or "").lower() for m in list_antigravity_catalog_models().get("models") or []}
        if arg.lower() not in known:
            return None  # "model the login flow" is a task

    if arg.lower() in ("refresh", "reload", "sync"):
        from api.model_catalog_delta import record_and_diff, render_delta

        result = refresh_antigravity_catalog()
        count = int(result.get("count") or 0)
        err, source = result.get("error"), result.get("source") or "unknown"
        if err and source == "static_fallback" and count == 0:
            return AgentResult(success=False, output="", error=f"Antigravity model refresh failed: {err}", model=active)
        note = (
            f" (CLI catalog unavailable — {err}; showing the manifest snapshot)"
            if err and source == "static_fallback" else " from `agy models`"
        )
        delta = record_and_diff("antigravity", result.get("models") or [])
        return AgentResult(
            success=True,
            output=(
                f"**Antigravity:** Refreshed model catalog{note} — {count} models available "
                f"in the `/` palette.\n\n{render_delta(delta)}\n\n"
                "Filter in chat (with an Antigravity badge): type `flash`, `neon`, `opus`, … "
                "then pick a row. Each row lists its effort levels; the manifest is the offline fallback."
            ),
            model=active,
            meta={
                "agent_model": active,
                "antigravity_catalog_count": count,
                "antigravity_catalog_source": source,
                "antigravity_catalog_added": [r["id"] for r in delta.get("added") or []],
                "antigravity_catalog_removed": [r["id"] for r in delta.get("removed") or []],
            },
        )

    if not arg or arg.lower() in ("list", "ls", "?"):
        catalog = list_antigravity_catalog_models()
        lines = [
            "**Antigravity models** (pin with `/antigravity model <id>`):", "",
            f"Palette catalog: {int(catalog.get('count') or 0)} models (source `{catalog.get('source')}`). "
            "Refresh with `/antigravity model refresh`.", "",
        ]
        for row in catalog.get("models") or []:
            rid = str(row.get("id") or "")
            mark = " ✅ current" if rid == active else ""
            lines.append(f"- `{rid}` — {row.get('label') or rid}{mark}")
            if row.get("efforts"):
                lines.append("  Effort levels: " + ", ".join(f"`{e}`" for e in row["efforts"]))
        pinned = load_antigravity_model(chat_session_id)
        lines.append("")
        lines.append(
            f"Pinned for this chat: `{pinned}` ({antigravity_model_label(pinned)})." if pinned
            else f"No pin — using `{active or 'CLI default'}`."
        )
        return AgentResult(success=True, output="\n".join(lines), model=active)

    if arg.lower() in ("default", "reset", "clear"):
        from api.agent_harness.agent_defaults import get_starred_model

        save_antigravity_model(chat_session_id, None)
        back = get_starred_model("antigravity") or ""
        return AgentResult(
            success=True,
            output=(f"**Antigravity:** Model reset to starred default `{back}`." if back
                    else "**Antigravity:** Model pin cleared — using the Antigravity CLI default."),
            model=back,
            meta={"agent_model": back, "model_source": "starred" if back else "cli_default"},
        )

    saved, dropped = pin_antigravity_model(chat_session_id, arg)
    saved = saved or arg
    note = f" Effort pin `{dropped}` cleared (not offered by this model)." if dropped else ""
    return AgentResult(
        success=True,
        output=f"**Antigravity:** Model set to `{saved}` ({antigravity_model_label(saved)}) for this chat.{note}",
        model=saved,
        meta={"agent_model": saved, "model_source": "session"},
    )


def pin_antigravity_model(chat_session_id: Any, model: Optional[str]) -> Tuple[str, str]:
    """Pin the chat's model; clear an effort pin the new model does not list.

    Without this a Neon ``xhigh`` pin would stop every turn after switching to
    Flash (low/medium/high). Returns ``(saved_model, dropped_effort)``.
    """
    saved = save_antigravity_model(chat_session_id, model) or ""
    effort = load_antigravity_effort(chat_session_id)
    supported = antigravity_efforts_for_model(saved) if saved and effort else []
    if supported and effort not in supported:
        save_antigravity_effort(chat_session_id, None)
        return saved, effort
    return saved, ""


def _handle_effort_slash(prompt: str, chat_session_id: Optional[str], model: Optional[str]) -> Optional[AgentResult]:
    from api.agent_harness.agent_defaults import get_starred_effort, resolve_effective_effort

    raw = (prompt or "").strip()
    match = re.match(r"^(/?)efforts?\b[\s:=]*(\S*)\s*$", raw, flags=re.I)
    if not match:
        return None
    arg = (match.group(2) or "").strip().strip('"').strip("'").lower()
    if not match.group(1) and arg and arg not in (*_EFFORT_WORDS, *_RESET_WORDS, "list", "ls", "?"):
        return None  # "effort estimate for X" is a task
    active = _active_model(chat_session_id, model)
    known = antigravity_efforts_for_model(active or None)
    pinned = load_antigravity_effort(chat_session_id) or ""
    effective, source = resolve_effective_effort("antigravity", session_effort=pinned)
    base_meta = {"agent_model": active, "agent_effort": effective or "", "effort_source": source}

    if not arg or arg in ("list", "ls", "?"):
        lines = ["**Antigravity reasoning effort** (pin with `/antigravity effort <level>`):", "",
                 f"Model `{active}` supports:" if active else "With no model pin, levels across the catalog:",
                 "", "Maps to `agy --effort <level>`.", ""]
        lines += [f"- `{e}`" + (" ✅ current" if e == effective else "") for e in known]
        if not known:
            lines.append("This model takes no effort level (fixed variant).")
        lines.append("")
        lines.append(f"Pinned for this chat: `{pinned}`." if pinned
                     else (f"Starred default: `{effective}`." if effective else "No pin — Antigravity picks its default."))
        return AgentResult(success=True, output="\n".join(lines), model=active, meta=base_meta)

    if arg in _RESET_WORDS:
        save_antigravity_effort(chat_session_id, None)
        back = get_starred_effort("antigravity") or ""
        return AgentResult(
            success=True,
            output=(f"**Antigravity:** Effort reset to starred default `{back}`." if back
                    else "**Antigravity:** Effort pin cleared — using the Antigravity CLI default."),
            model=active,
            meta={"agent_model": active, "agent_effort": back, "effort_source": "starred" if back else "none"},
        )

    if arg not in known:
        detail = ("Supported: " + ", ".join(f"`{e}`" for e in known) + ".") if known else \
            "This model takes no effort level."
        return AgentResult(
            success=True,
            output=f"Effort `{arg}` is not supported for `{active or 'the CLI default model'}`. {detail}",
            model=active, meta=base_meta,
        )
    save_antigravity_effort(chat_session_id, arg)
    return AgentResult(
        success=True,
        output=f"**Antigravity:** Reasoning effort set to `{arg}` for this chat.",
        model=active,
        meta={**base_meta, "agent_effort": arg, "effort_source": "session"},
    )


def resolve_turn_model_effort(
    chat_session_id: Optional[str], model: Optional[str], reasoning_effort: Optional[str]
) -> Dict[str, Any]:
    """Model + effort for one turn in policy order, and the flags agy accepts.

    A suffixed slug (``gemini-3.8-flash-low``) already names its effort and
    ``agy`` rejects it with ``--effort``, so the effort pin is dropped there.
    """
    from api.agent_harness.agent_defaults import (
        SOURCE_SESSION,
        resolve_effective_effort,
        resolve_effective_model,
    )

    session_model = load_antigravity_model(chat_session_id)
    mid, model_source = resolve_effective_model(
        "antigravity", session_model=session_model, kernel_override=model, cli_default=""
    )
    if session_model and mid == session_model:
        model_source = SOURCE_SESSION
    effort, effort_source = resolve_effective_effort(
        "antigravity", session_effort=load_antigravity_effort(chat_session_id), kernel_override=reasoning_effort
    )
    _family, suffix = split_effort_slug(mid)
    flag_effort = None if suffix else effort
    return {"model": mid or "", "model_source": model_source,
            "effort": suffix or effort or "", "effort_source": effort_source if (suffix or effort) else "none",
            "flag_effort": flag_effort}


def _pricing_model(model: str) -> str:
    family, _suffix = split_effort_slug(model)
    return family or model or "gemini"


def compact_conversation(cwd: str, resume_id: str, chat_session_id: Any = None) -> Dict[str, Any]:
    """``agy -p "/compact" --conversation <id>`` (documented slash, headless)."""
    import subprocess

    exe = antigravity_executable()
    base = {"agent_id": "antigravity", "method": "compact", "session_id": resume_id}
    if not exe:
        return {**base, "success": False, "error": "Antigravity CLI not found"}
    if not conversation_exists(resume_id):
        # agy would silently start (and "compact") a brand-new conversation.
        return {**base, "success": False, "error": "Antigravity conversation not found; nothing to compact."}
    plan = resolve_turn_model_effort(str(chat_session_id) if chat_session_id is not None else None, None, None)
    cmd = [exe, "-p", "/compact", "--conversation", resume_id, "--output-format", "json"]
    if plan["model"]:
        cmd += ["--model", plan["model"]]
    if plan["flag_effort"]:
        cmd += ["--effort", plan["flag_effort"]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=cwd,
                              stdin=subprocess.DEVNULL, env=agent_cli_env(), encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**base, "success": False, "error": f"Antigravity compact failed: {type(exc).__name__}"}
    body: Dict[str, Any] = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                body = json.loads(line)
                break
            except ValueError:
                continue
    ok = str(body.get("status") or "").upper() == "SUCCESS" and proc.returncode == 0
    if ok:
        return {**base, "success": True, "output": str(body.get("response") or "Compacted.")[:2000]}
    raw = str(body.get("error") or proc.stderr or proc.stdout or "")
    return {**base, "success": False,
            "error": "Antigravity /compact failed: " + summarize_antigravity_error(raw, proc.returncode, model=plan["model"])}


class Adapter:
    def available(self) -> bool:
        return bool(antigravity_executable())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        found = load_antigravity_resume_id(cwd, chat_session_id)
        if found and not conversation_exists(found):
            clear_antigravity_resume_id(cwd, chat_session_id)
            return None
        return found

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        save_antigravity_resume_id(cwd, chat_session_id, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        clear_antigravity_resume_id(cwd, chat_session_id)

    def handle_meta(
        self, prompt: str, *, chat_session_id: Optional[str], model: Optional[str]
    ) -> Optional[AgentResult]:
        """Model / effort / usage commands before Context Compiler wraps the prompt."""
        from api.agent_usage import handle_agent_usage_slash

        usage_md = handle_agent_usage_slash("antigravity", prompt)
        if usage_md is not None:
            return AgentResult(success=True, output=usage_md, model=_active_model(chat_session_id, model))
        effort_result = _handle_effort_slash(prompt, chat_session_id, model)
        if effort_result is not None:
            return effort_result
        return _handle_model_slash(prompt, chat_session_id, _active_model(chat_session_id, model))

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
        from api.agent_harness.agent_defaults import badge_meta

        exe = antigravity_executable()
        if not exe:
            return AgentResult(success=False, error="Antigravity CLI not found", output="")
        if not (prompt or "").strip():
            return AgentResult(success=False, error="No prompt provided", output="")
        plan = resolve_turn_model_effort(chat_session_id, model, reasoning_effort)
        mid = plan["model"]
        meta_base = badge_meta("antigravity", mid, plan["model_source"], plan["effort"], plan["effort_source"])
        if plan["flag_effort"]:
            supported = antigravity_efforts_for_model(mid or None)
            if supported and plan["flag_effort"] not in supported:
                allowed = ", ".join(f"`{e}`" for e in supported)
                return AgentResult(
                    success=True,
                    output=(f"Antigravity was not started: effort `{plan['flag_effort']}` is not supported for "
                            f"`{mid or 'the CLI default'}`. Supported: {allowed}. "
                            "Change the effort pin or reset it with `/antigravity effort default`."),
                    model=mid, meta=meta_base,
                )
        auth_error = await _authentication_preflight(exe, cwd)
        if auth_error:
            return AgentResult(success=False, error=auth_error, output="", model=mid, meta=meta_base)

        cmd = [
            exe, "-p", "",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--print-timeout", f"{max(1, math.ceil(timeout))}s",
        ]
        if resume:
            cmd.extend(["--conversation", str(resume).strip()])
        if mid:
            cmd.extend(["--model", mid])
        if plan["flag_effort"]:
            cmd.extend(["--effort", plan["flag_effort"]])
        user_line = json.dumps(
            {"event": "user", "message": {"role": "user", "content": prompt}}, ensure_ascii=False
        ) + "\n"

        put_status(status_queue, "Calling Antigravity CLI…")

        def _persist(sid):
            if chat_session_id and sid:
                try:
                    save_antigravity_resume_id(cwd, chat_session_id, sid)
                except Exception:
                    pass

        stream = AntigravityStream(status_queue, _persist)
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(stream.activity.heartbeat_loop(stop))
        run = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=agent_cli_env(),
                limit=16 * 1024 * 1024,
            )
            attach_to_chat_run(chat_session_id, proc)
            try:
                proc.stdin.write(user_line.encode("utf-8"))
                await proc.stdin.drain()
            finally:
                # EOF: the CLI exits after answering this one turn.
                proc.stdin.close()
            run = await run_interruptible(
                proc, timeout=timeout, cancel_event=cancel_event, line_mode=True, on_stdout_line=stream.feed
            )
        except OSError as exc:
            return AgentResult(success=False, error=summarize_antigravity_error(str(exc)), model=mid, meta=meta_base)
        finally:
            stream.text.flush()
            stop.set()
            try:
                await asyncio.wait_for(heartbeat, timeout=1.0)
            except Exception:
                heartbeat.cancel()

        if run is None:
            return AgentResult(success=False, error="Antigravity CLI failed to start", model=mid, meta=meta_base)

        err = run.stderr.decode("utf-8", errors="replace").strip()
        payload: Dict[str, Any] = stream.payload or {}
        status = str(payload.get("status") or "").upper()
        response = str(payload.get("response") or stream.partial_output()).strip()
        conversation_id = payload.get("conversation_id") or stream.session_id
        sid = str(conversation_id).strip() if conversation_id else (str(resume).strip() if resume else None)
        usage = normalize_antigravity_usage(payload.get("usage"))
        if stream.last_step_usage:
            # Window fill = the last model call's prompt (input + cached), not the
            # turn's summed billing across steps.
            step = stream.last_step_usage
            fill = int(step.get("input_tokens") or 0) + int(step.get("cache_read_tokens") or 0)
            if fill > 0:
                usage["context_tokens"] = fill
                if chat_session_id:
                    try:
                        save_antigravity_context(chat_session_id, {
                            "context_tokens": fill, "ts": time.time(), "conversation_id": sid, "model": mid,
                        })
                    except Exception:
                        pass
        if usage:
            usage["model"] = mid or "antigravity"
            try:
                from api.model_pricing import attach_estimated_cost

                attach_estimated_cost(usage, _pricing_model(mid or stream.model or ""), cache_inclusive=False)
            except Exception:
                pass
        meta = dict(meta_base)
        if stream.model and not mid:
            meta["agent_model_resolved"] = stream.model
        if resume and sid and sid != str(resume).strip():
            # agy started a fresh conversation (stale id): native memory is gone.
            meta["resume_lost"] = True

        if run.timed_out or run.cancelled:
            reason = run.reason or ("cancelled" if run.cancelled else f"timed out after {timeout:.0f}s")
            notice = format_interrupt_notice(
                "Antigravity",
                f"Antigravity {reason}" if "Antigravity" not in reason else reason,
                elapsed_sec=run.elapsed_sec,
                session_saved=bool(sid),
                resume_slash="antigravity",
            )
            body = f"{response}\n\n{notice}".strip() if response else notice
            _persist(sid)
            return AgentResult(
                success=False, output=body, error=f"Antigravity {reason}", usage=usage, session_id=sid,
                model=mid, meta={**meta, "status": status or "INTERRUPTED",
                                 "timed_out": run.timed_out, "cancelled": run.cancelled},
            )

        ok = stream.payload is not None and (run.returncode == 0) and (not status or status == "SUCCESS")
        if not ok:
            raw_error = str(
                payload.get("error") or err
                or ("Antigravity ended without a terminal result" if stream.payload is None else response)
                or f"exit {run.returncode}"
            )
            return AgentResult(
                success=False, output=response,
                error=summarize_antigravity_error(raw_error, run.returncode, model=mid),
                usage=usage, session_id=sid, model=mid, meta={**meta, "status": status},
            )
        return AgentResult(
            success=True, output=response or "Done.", usage=usage, session_id=sid,
            model=mid, meta={**meta, "status": status or "SUCCESS"},
        )


def build_adapter() -> Adapter:
    return Adapter()
