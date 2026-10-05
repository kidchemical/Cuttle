"""Cursor Agent CLI — headless ``agent -p`` (stream-json, resume, multi-segment).

Mirrors ``codex_cli_tool.py``: the Cursor harness adapter is a thin wrapper around
this module. Do not add Unity/screenshot/send-keys here.

Repo fallback cwd: Cuttle root (file lives under ``src/scripts/utilities/``).
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_CURSOR_AGENT_VERSION_RE = re.compile(
    r"^\d{4}\.\d{1,2}\.\d{1,2}(-\d{2}-\d{2}-\d{2})?-[a-f0-9]+$",
    re.I,
)


def _cursor_agent_install_roots() -> List[Path]:
    """Candidate install roots for the Cursor Agent CLI (Windows + PATH wrappers)."""
    roots: List[Path] = []
    local = os.path.expandvars(r"%LOCALAPPDATA%\cursor-agent")
    if local and local != r"%LOCALAPPDATA%\cursor-agent":
        roots.append(Path(local))
    import shutil

    for name in ("agent", "cursor-agent"):
        found = shutil.which(name)
        if not found:
            continue
        p = Path(found).resolve()
        # agent.cmd / agent.ps1 live in the install root; node+index live under versions/.
        if p.suffix.lower() in (".cmd", ".bat", ".ps1"):
            roots.append(p.parent)
        elif p.name.lower() in ("node.exe", "node"):
            # Rare: PATH points at the versioned node.exe
            roots.append(p.parent.parent if p.parent.name.lower() != "cursor-agent" else p.parent)
        else:
            # POSIX `agent` is typically a symlink into versions/<id>/cursor-agent
            roots.append(p.parent)
            if p.parent.name.lower() != "cursor-agent":
                share = Path.home() / ".local" / "share" / "cursor-agent"
                if share.is_dir():
                    roots.append(share)
    posix_share = Path.home() / ".local" / "share" / "cursor-agent"
    if posix_share.is_dir():
        roots.append(posix_share)
    # Dedupe while preserving order
    out: List[Path] = []
    seen = set()
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _cursor_agent_version_sort_key(name: str) -> tuple:
    """Sort key matching cursor-agent.ps1 Parse-VersionString (date, then full name)."""
    date_part = name.split("-", 1)[0]
    parts = date_part.split(".")
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return (y, m, d, name)
    except Exception:
        return (0, 0, 0, name)


def _resolve_cursor_agent_node_entry(install_root: Path) -> Optional[List[str]]:
    """Return [node.exe, index.js] for an install root, or None."""
    root = Path(install_root)
    # Flat layout (script dir has node.exe)
    flat_node = root / ("node.exe" if os.name == "nt" else "node")
    flat_index = root / "index.js"
    if flat_node.is_file() and flat_index.is_file():
        return [str(flat_node), str(flat_index)]

    versions = root / "versions"
    if not versions.is_dir():
        return None
    candidates = [
        d
        for d in versions.iterdir()
        if d.is_dir() and _CURSOR_AGENT_VERSION_RE.match(d.name)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda d: _cursor_agent_version_sort_key(d.name), reverse=True)
    for d in candidates:
        node = d / ("node.exe" if os.name == "nt" else "node")
        index = d / "index.js"
        if node.is_file() and index.is_file():
            return [str(node), str(index)]
    return None


def _resolve_cursor_agent_argv() -> Optional[List[str]]:
    """Argv prefix to spawn Cursor Agent without .cmd/.ps1 wrappers.

    On Windows, ``agent.cmd`` forwards args via ``%*``, which **truncates at the
    first newline**. Multiline ``/cursor`` prompts (blank lines, quoted blocks)
    then arrive at the agent as a cut-off first line. Spawning ``node.exe`` +
    ``index.js`` directly preserves the full prompt through CreateProcess.
    """
    for root in _cursor_agent_install_roots():
        pair = _resolve_cursor_agent_node_entry(root)
        if pair:
            return pair

    import shutil

    for name in ("agent", "cursor-agent"):
        found = shutil.which(name)
        if not found:
            continue
        # Never fall back to .cmd/.ps1/.bat on Windows — they reintroduce %* truncation.
        if os.name == "nt" and Path(found).suffix.lower() in (".cmd", ".bat", ".ps1"):
            continue
        return [found]
    return None


def _resolve_cursor_agent_cli() -> Optional[str]:
    """Prefer a direct node+index entry; else PATH / common Windows install wrappers.

    Callers that *spawn* the agent should use ``_resolve_cursor_agent_argv()`` so
    multiline prompts are not truncated by ``agent.cmd``'s ``%*`` forwarding.
    """
    argv = _resolve_cursor_agent_argv()
    if argv:
        # Prefer index.js path as the stable "cli" identity when using node entry.
        return argv[-1] if len(argv) > 1 else argv[0]

    import shutil

    for name in ("agent", "cursor-agent"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        os.path.expandvars(r"%LOCALAPPDATA%\cursor-agent\agent.cmd"),
        os.path.expandvars(r"%LOCALAPPDATA%\cursor-agent\cursor-agent.cmd"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def _cursor_agent_subprocess_env() -> Dict[str, str]:
    """Env for direct node+index launches (mirrors agent.ps1 defaults)."""
    env = agent_cli_env()
    env.setdefault("CURSOR_INVOKED_AS", "agent")
    if os.name == "nt" and not env.get("NODE_COMPILE_CACHE"):
        cache = os.path.expandvars(r"%LOCALAPPDATA%\cursor-compile-cache")
        if cache and cache != r"%LOCALAPPDATA%\cursor-compile-cache":
            env["NODE_COMPILE_CACHE"] = cache
    return env


_CURSOR_TOOL_ARG_KEYS = (
    "globPattern",
    "relativeWorkspacePath",
    "targetFile",
    "path",
    "targetDirectory",
    "command",
    "query",
    "pattern",
    "url",
    "filePath",
)


def _cursor_tool_summary(tool_call: dict) -> str:
    """Human label for a Cursor Agent tool call.

    Events look like ``{"globToolCall": {"args": {...}, "result": {...}}}`` —
    the tool name is the *key*, not a ``name`` field.
    """
    if not isinstance(tool_call, dict):
        return "tool"
    for key, val in tool_call.items():
        if not key.endswith("ToolCall") or not isinstance(val, dict):
            continue
        raw_name = key[: -len("ToolCall")]
        # camelCase → spaced words (readFile → read file)
        name = re.sub(r"(?<!^)(?=[A-Z])", " ", raw_name).lower()
        args = val.get("args") if isinstance(val.get("args"), dict) else {}
        detail = ""
        for arg_key in _CURSOR_TOOL_ARG_KEYS:
            v = args.get(arg_key)
            if isinstance(v, str) and v.strip():
                detail = v.strip()
                break
        if detail:
            # Long absolute paths add no signal — keep the tail.
            detail = detail.replace("\\", "/")
            if len(detail) > 60:
                detail = "…" + detail[-59:]
            return f"{name} {detail}"
        return name
    return "tool"


def _cursor_tool_failed(tool_call: dict) -> bool:
    if not isinstance(tool_call, dict):
        return False
    for key, val in tool_call.items():
        if key.endswith("ToolCall") and isinstance(val, dict):
            result = val.get("result")
            if isinstance(result, dict):
                return "error" in result or "failure" in result
    return False


def _unglue_cursor_result_text(text: str) -> str:
    """Insert paragraph breaks where the result event smashed turns together.

    Cursor's ``result`` field concatenates assistant turns with no separator
    (``bug.The messages`` / ``elsewhere.Confirmed:``). Only touch the common
    lowercase-letter + period + Uppercase smash; leave normal prose alone.
    """
    if not text:
        return text
    return re.sub(r"([a-z])\.([A-Z])", r"\1.\n\n\2", text)


def _merge_cursor_assistant_delta(delta_buf: str, piece: str) -> str:
    """Merge a streamed assistant text piece into the in-progress turn buffer.

    ``--stream-partial-output`` emits both tiny continuations and full-turn
    rewrites. Prefix growth → replace with longer; substantial non-prefix
    pieces → replace (rewrite), never glue two near-complete answers.
    """
    if not piece:
        return delta_buf or ""
    if not delta_buf:
        return piece
    if piece.startswith(delta_buf):
        return piece
    if delta_buf.startswith(piece):
        return delta_buf
    if delta_buf.endswith(piece):
        return delta_buf
    # Shared opening → same turn being rewritten mid-stream (e.g. :800 → :8000).
    probe = delta_buf[: max(1, min(64, len(delta_buf) // 2))]
    if probe and piece.startswith(probe):
        return piece
    # Large non-continuation chunk: treat as a draft replace, not append.
    if len(piece) >= 80 and len(piece) >= max(80, int(len(delta_buf) * 0.5)):
        return piece
    return delta_buf + piece


def _peel_cursor_final_answer(final_text: str, interim: List[str]) -> str:
    """Strip interim assistant turns from the result event's concatenated text."""
    rest = (final_text or "").strip()
    if not rest:
        return ""
    for turn in interim:
        t = (turn or "").strip()
        if not t:
            continue
        if rest.startswith(t):
            rest = rest[len(t) :].lstrip()
            continue
        # Result often smashes turns with no separator (``done.Next``).
        idx = rest.find(t)
        if idx == 0:
            rest = rest[len(t) :].lstrip()
        elif idx > 0 and idx < 8:
            # stray whitespace / punctuation before the turn
            rest = rest[idx + len(t) :].lstrip()
        else:
            break
    return rest.strip()


# Default wall-clock per agent -p segment (Cuttle-imposed, not Cursor CLI).
_CURSOR_AGENT_TIMEOUT_DEFAULT = 3600.0
_CURSOR_AGENT_TIMEOUT_MIN = 60.0
_CURSOR_AGENT_TIMEOUT_MAX = 7200.0
_CURSOR_AGENT_MAX_SEGMENTS_DEFAULT = 4
_CURSOR_CONTINUE_PROMPT = (
    "Continue where you left off. Finish the task without repeating completed work."
)
_CURSOR_FINISH_PROMPT = (
    "Your last turn ended before you delivered an answer. This is a headless one-shot "
    "run: ending the turn exits the process, so background subagents and deferred work "
    "never report back. Using what you already gathered, write the complete final "
    "answer now."
)
# A cleanly-exited run whose reply is only this long can still be pure narration.
_CURSOR_INCOMPLETE_MAX_CHARS = 400
_CURSOR_MAX_FINISH_NUDGES = 2
# Forward-looking phrases that mean "the answer comes next" — which, headless, it never does.
_CURSOR_PROMISE_RE = re.compile(
    r"(?:\bi'?ll\b|\bi will\b|\blet me\b(?!\s+know)|\bi'?m going to\b|\bi have enough\b"
    r"|\bi now have enough\b|\bnext,?\s+i\b|\bfirst,?\s+i\b|\bproceeding to\b|\babout to\b"
    r"|\bstand by\b|\bone moment\b|\bstarting now\b|\bdigging in\b|\bwill now\b)",
    re.IGNORECASE,
)
# Short post-tool status lines that should not displace a real mid-run deliverable.
_CURSOR_STATUS_CLOSER_RE = re.compile(
    r"(?is)^(?:done|fixed|updated|confirmed|wired|ok)(?:\s*[\u2014\-:.]|\s+[-–—].*)?$"
)
_CURSOR_STRUCTURED_MD_RE = re.compile(
    r"(?m)^(#{1,6}\s|```|\| .+\|)|<cuttle_(?:action_form|confirm|widget)\b",
    re.IGNORECASE,
)


def _cursor_turn_is_progress(text: str) -> bool:
    """True for short bridging/status narration that belongs in ``<think>``.

    Deliverables (forms, long prose, structured markdown) must stay visible even
    when a later tool call flushes another short closer after them.
    """
    t = (text or "").strip()
    if not t:
        return True
    if _CURSOR_STRUCTURED_MD_RE.search(t):
        return False
    if len(t) > _CURSOR_INCOMPLETE_MAX_CHARS:
        return False
    if _CURSOR_PROMISE_RE.search(t):
        return True
    if len(t) < 120:
        return True
    if len(t) < 200 and _CURSOR_STATUS_CLOSER_RE.match(t):
        return True
    return False


def _prefer_peeled_cursor_answer(peeled: str, answer: str) -> bool:
    """Whether result-event peel should replace the streamed answer.

    Never shrink a longer structured stream answer just because peel is shorter.
    """
    peeled = (peeled or "").strip()
    answer = (answer or "").strip()
    if not peeled:
        return False
    if peeled == answer:
        return True
    # Peel is a refinement / extension of the stream answer.
    if len(peeled) >= len(answer) and peeled.startswith(
        answer[: max(1, min(80, len(answer) // 2 or 1))]
    ):
        return True
    # Near-duplicate rewrite (same opening, similar length) — avoid doubled drafts.
    if abs(len(peeled) - len(answer)) <= max(40, len(answer) // 10):
        n = min(40, len(answer), len(peeled))
        if n and (answer.startswith(peeled[:n]) or peeled.startswith(answer[:n])):
            return True
    return False


def _assemble_cursor_agent_reply(
    turns: List[str],
    delta_buf: str = "",
    final_text: str = "",
) -> str:
    """Build the chat reply from streamed turns + optional result text.

    Short progress narration (already shown live under the dancing dots) goes in
    a ``<think>`` block. Deliverable turns — long prose, markdown structure,
    action forms — stay visible even when they are not the chronologically last
    piece (a later tool often ends on a short closer).
    """
    pieces = [t.strip() for t in (turns or []) if isinstance(t, str) and t.strip()]
    if isinstance(delta_buf, str) and delta_buf.strip():
        pieces.append(delta_buf.strip())
    final_text = (final_text or "").strip()

    if len(pieces) > 1:
        answers = [p for p in pieces if not _cursor_turn_is_progress(p)]
        progress = [p for p in pieces if _cursor_turn_is_progress(p)]
        if answers:
            answer = "\n\n".join(answers)
            thinking_parts = progress
        else:
            # All progress-shaped: keep last-wins so something still surfaces.
            thinking_parts = pieces[:-1]
            answer = pieces[-1]
        if final_text and len(answers) <= 1:
            # Single deliverable (or last-wins): peel progress/other turns from
            # result. Skip when multiple deliverables were joined — smash-concat
            # peel would re-glue them worse than the stream join.
            peel_against = (
                [p for p in pieces if p != answers[0]]
                if answers
                else thinking_parts
            )
            peeled = _peel_cursor_final_answer(final_text, peel_against)
            if _prefer_peeled_cursor_answer(peeled, answer):
                answer = peeled
        if thinking_parts:
            thinking = "\n\n".join(thinking_parts)
            return f"<think>\n{thinking}\n</think>\n\n{answer}"
        return answer
    if len(pieces) == 1:
        return pieces[0]
    if final_text:
        return _unglue_cursor_result_text(final_text)
    return ""


def _visible_cursor_answer(assembled: str) -> str:
    """The part of an assembled reply the user actually reads (outside <think>)."""
    text = assembled or ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def _cursor_reply_looks_incomplete(answer: str, tool_count: int = 0) -> bool:
    """True when a cleanly-exited run shipped narration instead of an answer.

    `agent -p` exits the moment the model ends its turn, so a turn that stops on
    "I'll dig into X" — or that hands off to a background subagent expecting a
    completion notification — delivers that promise as the whole reply. Kept
    deliberately conservative: a nudge costs a real CLI segment, so only short
    promise-shaped text or text cut off mid-sentence qualifies.
    """
    text = (answer or "").strip()
    if not text:
        return tool_count > 0
    # Cut mid-sentence (stream died, model ran out): resuming is the right move.
    if text.count("```") % 2 == 0 and re.search(r"[a-z,]$", text):
        return True
    if len(text) > _CURSOR_INCOMPLETE_MAX_CHARS:
        return False
    return bool(_CURSOR_PROMISE_RE.search(text))


def _cursor_agent_timeout_sec(explicit: Optional[float] = None) -> float:
    """Resolve per-segment timeout: explicit arg, else CUTTLE_CURSOR_AGENT_TIMEOUT_SEC, else 3600."""
    if explicit is not None:
        try:
            val = float(explicit)
        except (TypeError, ValueError):
            val = _CURSOR_AGENT_TIMEOUT_DEFAULT
    else:
        raw = (os.getenv("CUTTLE_CURSOR_AGENT_TIMEOUT_SEC") or "").strip()
        if raw:
            try:
                val = float(raw)
            except ValueError:
                val = _CURSOR_AGENT_TIMEOUT_DEFAULT
        else:
            val = _CURSOR_AGENT_TIMEOUT_DEFAULT
    return max(_CURSOR_AGENT_TIMEOUT_MIN, min(_CURSOR_AGENT_TIMEOUT_MAX, val))


def _cursor_agent_max_segments() -> int:
    """Max agent -p segments (1 initial + auto-continues). Env CUTTLE_CURSOR_AGENT_MAX_SEGMENTS."""
    raw = (os.getenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS") or "").strip()
    if raw:
        try:
            val = int(raw)
        except ValueError:
            val = _CURSOR_AGENT_MAX_SEGMENTS_DEFAULT
    else:
        val = _CURSOR_AGENT_MAX_SEGMENTS_DEFAULT
    return max(1, min(10, val))


def _cursor_agent_cli_option_args(
    *,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    sandbox: Optional[str] = None,
) -> List[str]:
    """Build optional `agent` CLI flags from per-chat Cursor Agent prefs."""
    args: List[str] = []
    if model and str(model).strip():
        args += ["--model", str(model).strip()]
    mode_norm = (mode or "").strip().lower()
    if mode_norm in ("plan", "ask"):
        args += ["--mode", mode_norm]
    sandbox_norm = (sandbox or "").strip().lower()
    if sandbox_norm in ("enabled", "disabled"):
        args += ["--sandbox", sandbox_norm]
    return args


def _cursor_usage_int(usage: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        raw = usage.get(key)
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return 0


def cursor_usage_looks_aggregated(usage: Optional[Dict[str, Any]]) -> bool:
    """True when a usage blob looks like multi-step billing, not one model call."""
    if not isinstance(usage, dict) or not usage:
        return False
    inn = _cursor_usage_int(usage, "inputTokens", "input_tokens", "prompt_tokens")
    cr = _cursor_usage_int(usage, "cacheReadTokens", "cache_read_tokens")
    cw = _cursor_usage_int(usage, "cacheWriteTokens", "cache_write_tokens")
    total_in = inn + cr + cw
    if cr >= 400_000 or total_in >= 1_200_000:
        return True
    # Explicit peak that is just inn+cr+cw with huge cache reads = bad stamp.
    peak = _cursor_usage_int(usage, "context_tokens", "peak_context_tokens", "contextTokens")
    if peak >= 400_000 and cr >= 200_000 and peak >= cr:
        if abs(peak - total_in) <= max(2000, int(peak * 0.03)):
            return True
    return False


def cursor_usage_context_tokens(usage: Optional[Dict[str, Any]]) -> Optional[int]:
    """Estimate one model-call context occupancy from a Cursor usage blob.

    Cursor ``result.usage`` is often **cumulative billing** across every step of
    a long agent turn. Prefer an explicit ``context_tokens`` / peak captured
    while streaming — but only when it is not itself a stamped aggregate sum.
    Otherwise treat ``input + cacheRead + cacheWrite`` as one-call size when it
    looks plausible; return None when the blob is clearly multi-step billing.
    """
    if not isinstance(usage, dict) or not usage:
        return None
    if cursor_usage_looks_aggregated(usage):
        return None
    for key in ("context_tokens", "peak_context_tokens", "contextTokens"):
        raw = usage.get(key)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    inn = _cursor_usage_int(usage, "inputTokens", "input_tokens", "prompt_tokens")
    cr = _cursor_usage_int(usage, "cacheReadTokens", "cache_read_tokens")
    cw = _cursor_usage_int(usage, "cacheWriteTokens", "cache_write_tokens")
    total_in = inn + cr + cw
    if total_in > 0:
        return total_in
    return inn or None


def _cursor_usage_short(usage: Optional[Dict[str, Any]]) -> str:
    if not isinstance(usage, dict) or not usage:
        return ""
    inn = usage.get("inputTokens")
    if inn is None:
        inn = usage.get("input_tokens")
    out = usage.get("outputTokens")
    if out is None:
        out = usage.get("output_tokens")
    if not isinstance(inn, (int, float)) and not isinstance(out, (int, float)):
        return ""

    def _fmt(n: Any) -> str:
        try:
            v = float(n or 0)
        except (TypeError, ValueError):
            return "0"
        if v >= 1000:
            return f"{v / 1000:.1f}k".rstrip("0").rstrip(".")
        return str(int(v))

    return f"{_fmt(inn)}→{_fmt(out)} tok"


def _is_cursor_auto_model(name: Optional[str]) -> bool:
    s = (name or "").strip().lower()
    return (not s) or s in ("auto", "default")


def _format_cursor_agent_run_log_note(
    *,
    requested_model: Optional[str] = None,
    reported_model: Optional[str] = None,
    request_id: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Human-readable run meta for query reports (not shown in chat bubbles)."""
    req = (requested_model or "auto").strip() or "auto"
    rep = (reported_model or req).strip() or req
    rid = (request_id or "").strip()
    parts = [f"requested={req}", f"reported={rep}"]
    if rid:
        parts.append(f"request_id={rid}")
    usage_s = _cursor_usage_short(usage)
    if usage_s:
        parts.append(usage_s)
    if cwd:
        parts.append(f"cwd={cwd}")
    note = "Cursor run · " + " · ".join(parts)
    if _is_cursor_auto_model(req) or _is_cursor_auto_model(rep):
        note += " · (Auto does not disclose underlying routed model to the CLI)"
    return note


def _wrap_cursor_agent_reply(
    text: str,
    *,
    label: str = "agent",
    cwd: str = "",
    requested_model: Optional[str] = None,
    reported_model: Optional[str] = None,
    request_id: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the agent answer only — model/cwd/request meta goes to badges + query logs."""
    return (text or "").rstrip()


def _cursor_run_meta_dict(
    *,
    cwd: str,
    requested_model: Optional[str],
    reported_model: Optional[str],
    request_id: Optional[str],
    usage: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    req = (requested_model or "auto").strip() or "auto"
    rep = (reported_model or req).strip() or req
    out: Dict[str, Any] = {
        "requested_model": req,
        "reported_model": rep,
        "cwd": cwd,
    }
    if request_id and str(request_id).strip():
        out["request_id"] = str(request_id).strip()
    if isinstance(usage, dict) and usage:
        out["usage"] = usage
    return out


def _persist_cursor_run_meta(
    *,
    cwd: str,
    chat_session_id,
    requested_model: Optional[str],
    reported_model: Optional[str],
    request_id: Optional[str],
    usage: Optional[Dict[str, Any]],
) -> None:
    if chat_session_id is None:
        return
    try:
        from scripts.utilities.cursor_cli_session_store import append_cursor_run_meta

        append_cursor_run_meta(
            cwd,
            chat_session_id,
            {
                "requested_model": (requested_model or "auto").strip() or "auto",
                "reported_model": (reported_model or requested_model or "auto"),
                "request_id": request_id,
                "usage": usage if isinstance(usage, dict) else None,
                "cwd": cwd,
            },
        )
    except Exception:
        pass


def _run_cursor_agent_stream_segment(
    *,
    agent_argv: List[str],
    cwd: str,
    prompt: str,
    resume_id: Optional[str],
    timeout: float,
    status_queue,
    chat_session_id,
    cancel_event,
    emit,
    cancelled,
    save_cursor_resume_id,
    tool_count_start: int = 0,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    sandbox: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one `agent -p` stream-json segment. Returns a result dict (never raises for timeout)."""
    import threading as _threading
    import queue as _queue

    cmd = list(agent_argv) + [
        "-p",
        "--force",
        "--trust",
        "--approve-mcps",
        "--workspace",
        cwd,
        "--output-format",
        "stream-json",
    ]
    cmd += _cursor_agent_cli_option_args(model=model, mode=mode, sandbox=sandbox)
    if resume_id:
        cmd += ["--resume", resume_id]
    cmd.append("--stream-partial-output")
    cmd.append(prompt)

    result: Dict[str, Any] = {
        "final_text": "",
        "turns": [],
        "delta_buf": "",
        "session_id": None,
        "timed_out": False,
        "cancelled": False,
        "errored": False,
        "err": "",
        "returncode": None,
        "tool_count": tool_count_start,
        "reported_model": None,
        "request_id": None,
        "usage": None,
        "create_plan": None,
        "ask_question": None,
    }

    if cancelled():
        result["cancelled"] = True
        return result

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_cursor_agent_subprocess_env(),
    )
    if chat_session_id is not None:
        try:
            from api.chat_run_registry import attach_process
            attach_process(chat_session_id, proc)
        except Exception:
            pass

    lines_q: "_queue.Queue" = _queue.Queue()
    stderr_chunks: List[str] = []

    def _read_stdout():
        try:
            for raw in proc.stdout or ():
                lines_q.put(raw)
        except Exception:
            pass
        finally:
            lines_q.put(None)

    def _read_stderr():
        try:
            for raw in proc.stderr or ():
                stderr_chunks.append(raw)
        except Exception:
            pass

    out_thread = _threading.Thread(target=_read_stdout, daemon=True)
    err_thread = _threading.Thread(target=_read_stderr, daemon=True)
    out_thread.start()
    err_thread.start()

    started = time.time()
    deadline = started + timeout
    final_text = ""
    turns: List[str] = []
    delta_buf = ""
    thinking_buf = ""
    from api.agent_harness.activity import TextActivityLog, text_preview
    text_log = TextActivityLog("cursor")
    text_log.start("writing")
    text_log.start("thinking")
    thinking_since = 0.0
    last_thought = ""
    tool_count = tool_count_start
    last_activity = "starting"
    errored = False
    session_id: Optional[str] = None
    timed_out = False
    was_cancelled = False
    reported_model: Optional[str] = None
    request_id: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    peak_context_tokens: int = 0

    def _note_usage(raw_usage: Any, *, from_result: bool = False) -> None:
        nonlocal usage, peak_context_tokens
        if not isinstance(raw_usage, dict):
            return
        usage = raw_usage
        # Final result.usage is cumulative billing across the whole agent turn
        # (Cursor docs). Never treat it as a single-call context peak.
        if from_result or cursor_usage_looks_aggregated(raw_usage):
            return
        try:
            ctx = cursor_usage_context_tokens(raw_usage)
        except Exception:
            ctx = None
        inn = _cursor_usage_int(raw_usage, "inputTokens", "input_tokens", "prompt_tokens")
        cr = _cursor_usage_int(raw_usage, "cacheReadTokens", "cache_read_tokens")
        cw = _cursor_usage_int(raw_usage, "cacheWriteTokens", "cache_write_tokens")
        call_size = inn + cr + cw
        # Mid-stream single-call peaks only — keep well under a full 1M+ window
        # of summed billing.
        if call_size > 0 and call_size < 800_000 and cr < 400_000:
            peak_context_tokens = max(peak_context_tokens, call_size)
        if ctx and 0 < int(ctx) < 800_000:
            peak_context_tokens = max(peak_context_tokens, int(ctx))

    def _persist_session(sid: str) -> None:
        nonlocal session_id
        session_id = sid
        if chat_session_id and save_cursor_resume_id is not None:
            try:
                save_cursor_resume_id(cwd, chat_session_id, sid)
            except Exception as persist_err:
                # Silently losing this makes every later turn a memory-less
                # Cursor session, which reads as the agent forgetting the chat.
                print(
                    f"[Cursor Agent] resume id not persisted for chat "
                    f"{chat_session_id!r}: {persist_err}",
                    flush=True,
                )

    while True:
        if cancelled():
            was_cancelled = True
            try:
                from api.chat_run_registry import _kill_proc
                _kill_proc(proc)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            break
        if time.time() > deadline:
            timed_out = True
            try:
                from api.chat_run_registry import _kill_proc
                _kill_proc(proc)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            break
        try:
            raw_line = lines_q.get(timeout=5.0)
        except _queue.Empty:
            elapsed = int(time.time() - started)
            emit(f"Cursor Agent working… {elapsed}s ({last_activity})", throttle=15.0)
            if proc.poll() is not None and not out_thread.is_alive():
                break
            continue
        if raw_line is None:
            break
        line = (raw_line or "").strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue

        et = evt.get("type")
        sub = evt.get("subtype")

        # Per-call usage can appear on several event types; track peak context.
        if isinstance(evt.get("usage"), dict) and et != "result":
            _note_usage(evt.get("usage"))

        if et == "system" and sub == "init":
            reported_model = str(evt.get("model") or "auto").strip() or "auto"
            last_activity = "thinking"
            emit(f"Cursor Agent ready (model: {reported_model})")
            sid = evt.get("session_id")
            if sid and isinstance(sid, str) and sid.strip():
                _persist_session(sid.strip())

        elif et == "thinking":
            if sub == "delta":
                if not thinking_buf:
                    thinking_since = time.time()
                thinking_buf += evt.get("text") or ""
                last_activity = "thinking"
                if time.time() - thinking_since > 1.5:
                    preview = thinking_buf.replace("\n", " ").strip()[:140]
                    if preview and emit(f"thinking: {preview}…", throttle=1.5):
                        last_thought = preview
                        text_log.save("thinking", thinking_buf)
            elif sub == "completed":
                preview = thinking_buf.replace("\n", " ").strip()[:160]
                if preview and preview != last_thought:
                    emit(f"thinking: {preview}")
                    last_thought = preview
                try:
                    if thinking_buf.strip():
                        text_log.save("thinking", thinking_buf)
                except Exception:
                    pass
                thinking_buf = ""
                text_log.start("thinking")

        elif et == "assistant":
            msg = evt.get("message") or {}
            content = msg.get("content") or []
            piece = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        piece += block.get("text") or ""
            elif isinstance(content, str):
                piece = content
            if not piece:
                continue
            delta_buf = _merge_cursor_assistant_delta(delta_buf, piece)
            preview = delta_buf.replace("\n", " ").strip()[-120:]
            if preview:
                last_activity = "writing"
                if emit(f"writing: {text_preview(delta_buf)}", throttle=1.2):
                    text_log.save("writing", delta_buf)

        elif et == "tool_call":
            tc = evt.get("tool_call") or {}
            summary = _cursor_tool_summary(tc)
            try:
                from api.cursor_plan_bridge import extract_create_plan_from_tool_call

                plan_payload = extract_create_plan_from_tool_call(tc)
                if plan_payload:
                    result["create_plan"] = plan_payload
            except Exception:
                pass
            try:
                from api.cursor_question_bridge import extract_ask_question_from_tool_call

                ask_payload = extract_ask_question_from_tool_call(tc)
                if ask_payload:
                    result["ask_question"] = ask_payload
            except Exception:
                pass
            if sub == "started":
                if delta_buf.strip():
                    text_log.save("writing", delta_buf)
                    text_log.start("writing")
                    turns.append(delta_buf)
                    delta_buf = ""
                tool_count += 1
                last_activity = f"tool: {summary}"
                emit(f"tool {tool_count}: {summary}")
                try:
                    from api.query_events import enrich_or_record_tool

                    enrich_or_record_tool(summary, tool_call=tc, phase="started")
                except Exception:
                    pass
            elif sub == "completed":
                if _cursor_tool_failed(tc):
                    emit(f"tool failed: {summary}")
                try:
                    from api.query_events import enrich_or_record_tool

                    enrich_or_record_tool(
                        summary,
                        tool_call=tc,
                        phase="failed" if _cursor_tool_failed(tc) else "completed",
                        failed=_cursor_tool_failed(tc),
                    )
                except Exception:
                    pass
                last_activity = f"finished {summary}"

        elif et == "result":
            final_text = (evt.get("result") or "").strip()
            text_log.save("writing", final_text or delta_buf)
            errored = bool(evt.get("is_error"))
            rid = evt.get("request_id")
            if isinstance(rid, str) and rid.strip():
                request_id = rid.strip()
            raw_usage = evt.get("usage")
            if isinstance(raw_usage, dict):
                _note_usage(raw_usage, from_result=True)
            sid = evt.get("session_id")
            if sid and isinstance(sid, str) and sid.strip():
                _persist_session(sid.strip())

    text_log.save("writing", final_text or delta_buf)
    text_log.save("thinking", thinking_buf)
    text_log.flush()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    out_thread.join(timeout=2)
    err_thread.join(timeout=2)

    # Leave the finished proc in chat_run_registry until the outer multi-segment
    # loop finishes so the same cancel Event stays bound for Stop.

    # Stamp peak single-call context onto usage for the composer gauge.
    # Final result.usage is often cumulative billing across the whole turn.
    if isinstance(usage, dict):
        usage = dict(usage)
        if peak_context_tokens > 0:
            usage["context_tokens"] = int(peak_context_tokens)
            usage["peak_context_tokens"] = int(peak_context_tokens)
        else:
            # Last resort: one-call estimate when the blob isn't an aggregate.
            try:
                ctx = cursor_usage_context_tokens(usage)
            except Exception:
                ctx = None
            if ctx:
                usage["context_tokens"] = int(ctx)

    result.update(
        {
            "final_text": final_text,
            "turns": turns,
            "delta_buf": delta_buf,
            "session_id": session_id,
            "timed_out": timed_out,
            "cancelled": was_cancelled or cancelled(),
            "errored": errored,
            "err": "".join(stderr_chunks).strip(),
            "returncode": proc.returncode,
            "tool_count": tool_count,
            "proc": proc,
            "reported_model": reported_model,
            "request_id": request_id,
            "usage": usage,
            "peak_context_tokens": int(peak_context_tokens or 0),
        }
    )
    return result


def _cursor_agent_oneline_prompt(
    prompt: str,
    workspace: Optional[str] = None,
    status_queue=None,
    timeout: Optional[float] = None,
    chat_session_id=None,
    cancel_event=None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    sandbox: Optional[str] = None,
    run_meta_out: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Run a one-shot prompt via Cursor Agent CLI (`agent -p`); None if CLI missing.

    Always uses stream-json so the CLI emits a ``session_id`` we can persist for
    ``--resume``. ``status_queue`` is optional: when present, thinking / tool /
    partial output also surface as chat status updates; when absent (non-SSE
    chat, Discord sync, offline smoke), resume still works.

    Streaming runs auto-continue on Cuttle's per-segment wall-clock timeout via
    ``--resume`` (up to CUTTLE_CURSOR_AGENT_MAX_SEGMENTS), so long jobs stay one
    chat turn instead of failing at 900s/3600s.

    Optional ``model`` / ``mode`` / ``sandbox`` override stored per-chat prefs
    (from Cursor Agent slash commands like ``/model`` and ``/plan``).

    If ``run_meta_out`` is a dict, it is updated with requested/reported model,
    request_id, usage, and cwd for query logs / UI badges.
    """
    agent_argv = _resolve_cursor_agent_argv()
    if not agent_argv:
        return None
    repo_root = Path(__file__).resolve().parents[3]
    cwd = workspace or (str(repo_root) if repo_root.is_dir() else os.getcwd())
    # Prefer "agent" label even when argv is node.exe + index.js
    label = "agent" if len(agent_argv) > 1 else Path(agent_argv[0]).stem
    resolved_timeout = _cursor_agent_timeout_sec(timeout)
    max_segments = _cursor_agent_max_segments()

    last_emit_at = [0.0]

    def _emit(msg: str, throttle: float = 0.0) -> bool:
        """Push a status update; returns False when throttled/dropped."""
        if not status_queue or not msg:
            return False
        now = time.time()
        if throttle and (now - last_emit_at[0]) < throttle:
            return False
        last_emit_at[0] = now
        try:
            put_preview = getattr(status_queue, "put_preview", None)
            if msg.startswith(("thinking:", "writing:")) and callable(put_preview):
                put_preview(("status", msg))
            else:
                status_queue.put_nowait(("status", msg))
            return True
        except Exception:
            return False

    def _cancelled() -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return True
        if chat_session_id is not None:
            try:
                from api.chat_run_registry import is_run_cancelled
                return is_run_cancelled(chat_session_id)
            except Exception:
                return False
        return False

    # Always stream-json: text mode never emits session_id, so --resume silently
    # died on every non-SSE / no-queue call (harness smoke, Discord sync, etc.).
    use_stream = True

    # Load stored session for --resume (mirrors other harness session persistence)
    # plus per-chat model/mode/sandbox from Cursor Agent slash commands.
    _resume_id = None
    save_cursor_resume_id = None  # type: ignore[assignment]
    _opt_model = model
    _opt_mode = mode
    _opt_sandbox = sandbox
    try:
        from scripts.utilities.cursor_cli_session_store import (
            load_cursor_agent_options,
            prepare_cursor_agent_workspace,
            save_cursor_resume_id,
        )
        requested_cwd = cwd
        cwd, _resume_id = prepare_cursor_agent_workspace(requested_cwd, chat_session_id)
        try:
            from api.agent_harness.cwd import constrain_to_project, same_project

            if requested_cwd and cwd and not same_project(str(requested_cwd), str(cwd)):
                cwd = requested_cwd
                _resume_id = None
            elif requested_cwd:
                cwd = constrain_to_project(str(requested_cwd), str(cwd or requested_cwd))
        except Exception:
            pass
        if cwd and requested_cwd and str(Path(cwd).resolve()) != str(Path(requested_cwd).resolve()):
            print(
                f"[Cursor Agent] pinning --workspace {cwd!r} "
                f"(resume/git) over requested {requested_cwd!r}",
                flush=True,
            )
        if chat_session_id:
            opts = load_cursor_agent_options(requested_cwd, chat_session_id) or {}
            # Default / explicit "auto" must not wipe a per-chat `/model` pin.
            if _opt_model is None or not str(_opt_model).strip() or str(_opt_model).strip().lower() == "auto":
                pinned = opts.get("model")
                if isinstance(pinned, str) and pinned.strip():
                    _opt_model = pinned.strip()
            if _opt_mode is None:
                _opt_mode = opts.get("mode")
            if _opt_sandbox is None:
                _opt_sandbox = opts.get("sandbox")
    except Exception:
        _resume_id = None
        save_cursor_resume_id = None  # type: ignore[assignment]

    # Nothing pinned for this chat: ask for Auto explicitly. Omitting --model let
    # the CLI's own global selection (cli-config.json selectedModel, e.g. Grok 4.6
    # High) run the turn while Cuttle still badged it "Auto" — silent premium spend.
    if not (_opt_model and str(_opt_model).strip()):
        _opt_model = "auto"

    def _record_run_meta(
        reported_model: Optional[str],
        request_id: Optional[str],
        usage: Optional[Dict[str, Any]],
    ) -> None:
        usg = usage if isinstance(usage, dict) else None
        meta = _cursor_run_meta_dict(
            cwd=cwd,
            requested_model=_opt_model,
            reported_model=reported_model,
            request_id=request_id,
            usage=usg,
        )
        if isinstance(run_meta_out, dict):
            run_meta_out.clear()
            run_meta_out.update(meta)
        _persist_cursor_run_meta(
            cwd=cwd,
            chat_session_id=chat_session_id,
            requested_model=_opt_model,
            reported_model=reported_model,
            request_id=request_id,
            usage=usg,
        )

    def _wrap(
        text: str,
        *,
        reported_model: Optional[str] = None,
        request_id: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> str:
        rep = reported_model
        rid = request_id
        usg = usage
        _record_run_meta(rep, rid, usg)
        return _wrap_cursor_agent_reply(
            text,
            label=label,
            cwd=cwd,
            requested_model=_opt_model,
            reported_model=rep,
            request_id=rid,
            usage=usg if isinstance(usg, dict) else None,
        )

    def _fail(
        text: str,
        *,
        reported_model: Optional[str] = None,
        request_id: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Failures need run meta too, or the UI badge falls back to inventing "Auto"."""
        _record_run_meta(reported_model, request_id, usage)
        return text

    try:
        if not use_stream:
            if _cancelled():
                return "[CANCELLED] Cursor Agent run was cancelled."
            cmd = list(agent_argv) + [
                "-p",
                "--force",
                "--trust",
                "--approve-mcps",
                "--workspace",
                cwd,
                "--output-format",
                "text",
            ]
            cmd += _cursor_agent_cli_option_args(
                model=_opt_model, mode=_opt_mode, sandbox=_opt_sandbox
            )
            if _resume_id:
                cmd += ["--resume", _resume_id]
            cmd.append(prompt)
            r = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=resolved_timeout,
                env=_cursor_agent_subprocess_env(),
            )
            if _cancelled():
                return "[CANCELLED] Cursor Agent run was cancelled."
            raw_out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            out = raw_out
            _captured_session_id: List[str] = []
            _reported: Optional[str] = None
            _req_id: Optional[str] = None
            _usage: Optional[Dict[str, Any]] = None
            if r.returncode == 0 and raw_out:
                for _jline in raw_out.splitlines():
                    try:
                        _jevt = json.loads(_jline)
                        if _jevt.get("type") == "system" and _jevt.get("subtype") == "init":
                            _sid = _jevt.get("session_id")
                            if _sid and isinstance(_sid, str) and _sid.strip():
                                _captured_session_id.append(_sid.strip())
                            _rm = _jevt.get("model")
                            if isinstance(_rm, str) and _rm.strip():
                                _reported = _rm.strip()
                        if _jevt.get("type") == "result":
                            out = (_jevt.get("result") or raw_out).strip()
                            _rid = _jevt.get("request_id")
                            if isinstance(_rid, str) and _rid.strip():
                                _req_id = _rid.strip()
                            if isinstance(_jevt.get("usage"), dict):
                                _usage = _jevt.get("usage")
                    except Exception:
                        pass
                if _captured_session_id and chat_session_id and save_cursor_resume_id is not None:
                    try:
                        save_cursor_resume_id(cwd, chat_session_id, _captured_session_id[0])
                    except Exception:
                        pass
                return _wrap(out, reported_model=_reported, request_id=_req_id, usage=_usage)
            if err:
                return _fail(
                    f"[FAIL] **Cursor Agent** (`{label}`):\n```\n{err[:2000]}\n```",
                    reported_model=_reported,
                    request_id=_req_id,
                    usage=_usage,
                )
            if raw_out:
                return _wrap(raw_out, reported_model=_reported, request_id=_req_id, usage=_usage)
            return _fail(
                f"[FAIL] Cursor Agent (`{label}`) exited with code {r.returncode}",
                reported_model=_reported,
                request_id=_req_id,
                usage=_usage,
            )

        _emit("Cursor Agent starting...")
        if _cancelled():
            return "[CANCELLED] Cursor Agent run was cancelled."

        all_turns: List[str] = []
        last_delta = ""
        last_final = ""
        last_err = ""
        last_returncode = None
        last_errored = False
        live_resume = _resume_id
        tool_count = 0
        finish_nudges = 0
        segment_prompt = prompt
        segment_procs: List[Any] = []
        last_reported_model: Optional[str] = None
        last_request_id: Optional[str] = None
        last_usage: Optional[Dict[str, Any]] = None
        last_create_plan: Optional[Dict[str, Any]] = None
        last_ask_question: Optional[Dict[str, Any]] = None

        def _assemble_and_bridge(
            turns: List[str], delta: str, final: str
        ) -> str:
            assembled = _assemble_cursor_agent_reply(turns, delta, final)
            if last_create_plan:
                try:
                    from api.cursor_plan_bridge import bridge_create_plan_into_reply

                    assembled = bridge_create_plan_into_reply(assembled, last_create_plan)
                except Exception:
                    pass
            if last_ask_question:
                try:
                    from api.cursor_question_bridge import bridge_ask_question_into_reply

                    assembled = bridge_ask_question_into_reply(assembled, last_ask_question)
                except Exception:
                    pass
            return assembled

        try:
            for segment_idx in range(max_segments):
                if _cancelled():
                    return "[CANCELLED] Cursor Agent run was cancelled (chat deleted or stopped)."

                seg = _run_cursor_agent_stream_segment(
                    agent_argv=agent_argv,
                    cwd=cwd,
                    prompt=segment_prompt,
                    resume_id=live_resume,
                    timeout=resolved_timeout,
                    status_queue=status_queue,
                    chat_session_id=chat_session_id,
                    cancel_event=cancel_event,
                    emit=_emit,
                    cancelled=_cancelled,
                    save_cursor_resume_id=save_cursor_resume_id,
                    tool_count_start=tool_count,
                    model=_opt_model,
                    mode=_opt_mode,
                    sandbox=_opt_sandbox,
                )
                if seg.get("proc") is not None:
                    segment_procs.append(seg["proc"])
                tool_count = int(seg.get("tool_count") or tool_count)
                if seg.get("session_id"):
                    live_resume = seg["session_id"]
                if seg.get("reported_model"):
                    last_reported_model = str(seg.get("reported_model")).strip() or last_reported_model
                if seg.get("request_id"):
                    last_request_id = str(seg.get("request_id")).strip() or last_request_id
                if isinstance(seg.get("usage"), dict):
                    last_usage = seg.get("usage")
                if isinstance(seg.get("create_plan"), dict) and seg.get("create_plan"):
                    last_create_plan = seg.get("create_plan")
                if isinstance(seg.get("ask_question"), dict) and seg.get("ask_question"):
                    last_ask_question = seg.get("ask_question")

                seg_turns = list(seg.get("turns") or [])
                seg_delta = (seg.get("delta_buf") or "").strip()
                if seg_turns:
                    all_turns.extend(seg_turns)
                if seg_delta:
                    # Incomplete segment text becomes interim context for the next continue.
                    all_turns.append(seg_delta)
                    last_delta = ""
                else:
                    last_delta = ""
                last_final = (seg.get("final_text") or "").strip()
                last_err = (seg.get("err") or "").strip()
                last_returncode = seg.get("returncode")
                last_errored = bool(seg.get("errored"))

                if seg.get("cancelled"):
                    return "[CANCELLED] Cursor Agent run was cancelled (chat deleted or stopped)."

                if seg.get("timed_out"):
                    if live_resume and segment_idx + 1 < max_segments:
                        _emit("Continuing long agent run…")
                        segment_prompt = _CURSOR_CONTINUE_PROMPT
                        continue
                    out = _assemble_and_bridge(all_turns, last_delta, last_final)
                    if out:
                        return _wrap(
                            out
                            + f"\n\n*(Timed out after {int(resolved_timeout)}s "
                            f"× {segment_idx + 1} segment(s); could not auto-continue.)*",
                            reported_model=last_reported_model,
                            request_id=last_request_id,
                            usage=last_usage,
                        )
                    return _fail(
                        f"[FAIL] Cursor Agent timed out after {int(resolved_timeout)}s "
                        f"({segment_idx + 1} segment(s)).",
                        reported_model=last_reported_model,
                        request_id=last_request_id,
                        usage=last_usage,
                    )

                # Normal completion for this segment.
                if last_errored:
                    out = _assemble_and_bridge(all_turns, last_delta, last_final)
                    return _fail(
                        f"[FAIL] **Cursor Agent:** {out or last_err or 'agent reported an error'}",
                        reported_model=last_reported_model,
                        request_id=last_request_id,
                        usage=last_usage,
                    )
                out = _assemble_and_bridge(all_turns, last_delta, last_final)
                if (
                    live_resume
                    and finish_nudges < _CURSOR_MAX_FINISH_NUDGES
                    and segment_idx + 1 < max_segments
                    and _cursor_reply_looks_incomplete(_visible_cursor_answer(out), tool_count)
                ):
                    finish_nudges += 1
                    _emit("Agent stopped before answering — asking it to finish…")
                    segment_prompt = _CURSOR_FINISH_PROMPT
                    continue
                if out:
                    return _wrap(
                        out,
                        reported_model=last_reported_model,
                        request_id=last_request_id,
                        usage=last_usage,
                    )
                if last_err:
                    return _fail(
                        f"[FAIL] **Cursor Agent** (`{label}`):\n```\n{last_err[:2000]}\n```",
                        reported_model=last_reported_model,
                        request_id=last_request_id,
                        usage=last_usage,
                    )
                return _fail(
                    f"[FAIL] Cursor Agent (`{label}`) exited with code {last_returncode}",
                    reported_model=last_reported_model,
                    request_id=last_request_id,
                    usage=last_usage,
                )

            out = _assemble_and_bridge(all_turns, last_delta, last_final)
            if out:
                return _wrap(
                    out,
                    reported_model=last_reported_model,
                    request_id=last_request_id,
                    usage=last_usage,
                )
            return _fail(
                f"[FAIL] Cursor Agent timed out after {int(resolved_timeout)}s "
                f"× {max_segments} segment(s).",
                reported_model=last_reported_model,
                request_id=last_request_id,
                usage=last_usage,
            )
        finally:
            if chat_session_id is not None:
                try:
                    from api.chat_run_registry import end_run
                    for proc in segment_procs:
                        end_run(chat_session_id, proc=proc)
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        return f"[FAIL] Cursor Agent timed out after {int(resolved_timeout)}s."
    except Exception as e:
        return f"[FAIL] Cursor Agent: {e}"


def handle_cursor_cli_command(command: str) -> str:
    """
    Handle /cursor: one-shot `agent` prompts or `--version` / `--help`.

    Args:
        command: e.g. ``fix the login bug``
    """
    print(f"🖥️ Cursor CLI Command: '{command}'")

    command = (command or "").strip()
    if not command:
        return "[FAIL] No command provided"

    low = command.lower()
    agent_argv = _resolve_cursor_agent_argv()
    agent_exe = _resolve_cursor_agent_cli()

    # Version / help
    if low in ("--version", "-v", "version"):
        parts = []
        if agent_argv:
            try:
                r = subprocess.run(
                    list(agent_argv) + ["--version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=_cursor_agent_subprocess_env(),
                )
                out = (r.stdout or r.stderr or "").strip()
                shown = Path(agent_argv[-1]).name if len(agent_argv) > 1 else Path(agent_argv[0]).name
                parts.append(f"**agent CLI** (`{shown}`):\n```\n{out or '(no output)'}\n```")
            except Exception as e:
                parts.append(f"**agent CLI:** failed — {e}")
        else:
            parts.append("**agent CLI:** not found on PATH (install Cursor Agent / `agent`)")
        return "\n\n".join(parts)

    if low in ("--help", "-h", "help"):
        agent_note = (
            f"`{Path(agent_argv[-1]).name}`"
            if agent_argv and len(agent_argv) > 1
            else (f"`{Path(agent_exe).name}`" if agent_exe else "`agent` (not found)")
        )
        return (
            "**Cursor Agent** (`/cursor`)\n\n"
            f"• **Plain text** — one-shot prompt via Cursor Agent CLI ({agent_note}, `agent -p`)\n"
            "• **`--version`** / **`--help`**\n\n"
            "Requires chat mode **Auto** or **Cloud** (blocked in Local)."
        )

    # One-shot headless Cursor Agent (`agent -p`)
    agent_out = _cursor_agent_oneline_prompt(command)
    if agent_out is not None:
        return agent_out

    return (
        "**Cursor Agent** — `agent` not found on PATH.\n\n"
        "• Install the Cursor Agent CLI so `agent` works in a terminal\n"
    )
