"""DeepSeek Harness (`dsh`) adapter — headless one-shot.

CLI: ``dsh --profile headless "<task>"`` (developer preview). Default model is
``deepseek-v4-flash``. Authentication belongs to the installed CLI.

Headless creates a fresh session every call, so ``resume`` is false. On Windows
we skip the npm ``dsh.cmd`` shim and run ``node …/bin.js`` so multiline prompts
are not truncated by ``%*``.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from api.agent_harness.activity import put_status
from .stream import DeepSeekStream
from api.agent_harness.cwd import resolve_harness_cwd
from api.agent_harness.types import AgentResult
from api.agent_harness.win_cli import which_preferring_native
from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    run_interruptible,
)

# Stay under Windows CreateProcess; Context Compiler envelopes can be large.
_MAX_PROMPT_FOR_ARGV = 20000
_DEFAULT_MODEL = "deepseek-v4-flash"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def dsh_argv() -> Optional[List[str]]:
    """Return argv prefix that launches dsh without a ``.cmd`` shim when possible."""
    found = which_preferring_native(("dsh",), env_var="DSH_CLI_PATH")
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat"):
        bin_js = path.parent / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        if bin_js.is_file():
            node = which_preferring_native(("node",)) or "node"
            return [node, str(bin_js)]
    return [found]


def _safe_model(model: Optional[str]) -> str:
    mid = (str(model).strip() if model else "") or _DEFAULT_MODEL
    return mid if _MODEL_RE.fullmatch(mid) else _DEFAULT_MODEL


def summarize_deepseek_error(raw: str, returncode: Optional[int] = None) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "missing_credential" in low or "no api key" in low or "deepseek_api_key" in low:
        return (
            "DeepSeek Harness is not authenticated. Configure credentials in the DeepSeek CLI "
            "itself, then retry."
        )
    if "unauthorized" in low or "invalid api key" in low or "401" in low:
        return (
            "DeepSeek API key was rejected. Check `DEEPSEEK_API_KEY` / billing at "
            "https://platform.deepseek.com then retry."
        )
    if "quota" in low or "insufficient" in low or "balance" in low:
        return "DeepSeek quota or balance is exhausted. Top up the API account and retry."
    compact = re.sub(r"\s+", " ", text)
    if compact:
        return compact[:2000]
    return (
        f"DeepSeek Harness failed (exit {returncode})."
        if returncode is not None
        else "DeepSeek Harness failed."
    )


def _write_model_patch(model: str) -> str:
    mid = _safe_model(model)
    handle = tempfile.NamedTemporaryFile(
        prefix="cuttle-dsh-model-",
        suffix=".yml",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    handle.write(
        "- id: agent-default-model\n"
        "  name: '@deepseek-ai/dsh-agent-default-model'\n"
        "  config:\n"
        "    provider: deepseek-official\n"
        f"    model: {mid}\n"
    )
    handle.close()
    return handle.name


class Adapter:
    def available(self) -> bool:
        return bool(dsh_argv())

    def resolve_cwd(self, project_path: str) -> str:
        return resolve_harness_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        return None

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None:
        return None

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
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
        timeout: float = 600.0,
        cancel_event: Any = None,
    ) -> AgentResult:
        argv0 = dsh_argv()
        if not argv0:
            return AgentResult(
                success=False,
                output="",
                error=(
                    "DeepSeek Harness CLI (`dsh`) is not on PATH. "
                    "Install `@deepseek-ai/dsh` or set `DSH_CLI_PATH`."
                ),
                model=_safe_model(model),
            )

        from api.agent_harness.agent_defaults import (
            SOURCE_CLI_DEFAULT,
            SOURCE_OVERRIDE,
            SOURCE_STARRED,
            badge_meta,
            get_starred_model,
        )

        # Policy: explicit override → starred → dsh CLI default. The patch
        # file is only written when pinned/starred away from the default.
        _override = (str(model).strip() if model else "") or None
        _starred = get_starred_model("deepseek")
        mid = _safe_model(_override or _starred or _DEFAULT_MODEL)
        if _override and mid != _DEFAULT_MODEL:
            _model_source = SOURCE_OVERRIDE
        elif _starred and mid != _DEFAULT_MODEL:
            _model_source = SOURCE_STARRED
        else:
            _model_source = SOURCE_CLI_DEFAULT
        put_status(status_queue, "Calling DeepSeek Harness…")
        patch_path = None
        if mid != _DEFAULT_MODEL:
            patch_path = _write_model_patch(mid)
        task_path = None
        task = prompt
        if len(prompt or "") > _MAX_PROMPT_FOR_ARGV:
            handle = tempfile.NamedTemporaryFile(
                prefix="cuttle-dsh-task-",
                suffix=".txt",
                delete=False,
                mode="w",
                encoding="utf-8",
            )
            handle.write(prompt)
            handle.close()
            task_path = handle.name
            task = (
                f"Read the UTF-8 file `{task_path}` in full and follow every instruction "
                "in it. Do not skip the last line."
            )

        cmd = list(argv0) + ["--profile", "headless", "--json"]
        if patch_path:
            cmd.extend(["--patch", patch_path])
        cmd.append(task)
        env = agent_cli_env()
        stream = DeepSeekStream(status_queue)
        stop = asyncio.Event()
        hb = asyncio.create_task(stream.activity.heartbeat_loop(stop))
        run = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                limit=16 * 1024 * 1024,
            )
            attach_to_chat_run(chat_session_id, proc)
            run = await run_interruptible(
                proc, timeout=timeout, cancel_event=cancel_event, line_mode=True, on_stdout_line=stream.feed
            )
        finally:
            stream.text.flush()
            stop.set()
            try:
                await asyncio.wait_for(hb, timeout=1.0)
            except Exception:
                hb.cancel()
            for leftover in (patch_path, task_path):
                if leftover:
                    try:
                        os.unlink(leftover)
                    except OSError:
                        pass

        if run is None:
            return AgentResult(
                success=False,
                output="",
                error="DeepSeek Harness failed to start",
                model=mid,
                meta=badge_meta("deepseek", mid, _model_source),
            )

        out = stream.final if stream.final is not None else stream.partial_output()
        err = run.stderr.decode("utf-8", errors="replace").strip()
        meta = badge_meta("deepseek", mid, _model_source)

        if run.timed_out or run.cancelled:
            reason = run.reason or (
                "cancelled" if run.cancelled else f"timed out after {timeout:.0f}s"
            )
            notice = format_interrupt_notice(
                "DeepSeek Harness",
                reason,
                elapsed_sec=run.elapsed_sec,
                session_saved=False,
                resume_slash="deepseek",
            )
            body_parts = []
            if out:
                body_parts.append(out)
            elif err:
                body_parts.append(err[:2000])
            body_parts.append(notice)
            return AgentResult(
                success=False,
                output="\n\n".join(body_parts),
                error=f"DeepSeek Harness {reason}",
                model=mid,
                meta={**meta, "timed_out": run.timed_out, "cancelled": run.cancelled},
            )

        ok = run.returncode == 0 and stream.final is not None and bool(out) and not stream.errors
        if ok:
            return AgentResult(
                success=True, output=out, model=mid, meta=meta, usage=stream.usage,
            )
        shaped = summarize_deepseek_error("\n".join(stream.errors) or err or (
            "DeepSeek Harness ended without a terminal result" if stream.final is None else out
        ), run.returncode)
        return AgentResult(
            success=False,
            output=out,
            error=shaped,
            model=mid,
            meta=meta,
        )


def build_adapter() -> Adapter:
    return Adapter()
