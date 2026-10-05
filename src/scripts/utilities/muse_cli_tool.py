"""
Muse Code CLI integration — Meta's terminal coding agent (`muse exec --json`).

Resolution order on Windows is native-first:
  ``MUSE_CLI_PATH`` → ``%LOCALAPPDATA%\\Programs\\muse\\muse-bin-*.exe``
  (Meta's official Windows install) → PATH. Only the native host
  installation is supported.

Auth: native Muse login/configuration; host API credentials are isolated.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import queue as queue_module

_MAX_PROMPT_FOR_ARGV = 2800 if os.name == "nt" else 12000
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


# Static fallback when env + Muse CLI settings.json are unavailable.
# Meta's docs currently recommend 1.3 for new work; the CLI default is whatever
# is in ~/.config/muse/settings.json (often contributor).
DEFAULT_MUSE_MODEL = "muse-spark-1.3"

# Muse Code has no `models list` subcommand, so the palette needs a static
# catalog (https://dev.meta.ai/docs/models). Unknown ids are still accepted.
MUSE_KNOWN_MODELS: List[Dict[str, str]] = [
    {
        "id": "muse-spark-1.3",
        "label": "Muse Spark 1.3",
        "description": "Latest checkpoint, Standard tier, 1M context — recommended",
    },
    {
        "id": "muse-spark-1.3-contributor",
        "label": "Muse Spark 1.3 (Contributor)",
        "description": "Latest checkpoint, discounted tier — prompts may train Meta models",
    },
    {
        "id": "muse-spark-1.2",
        "label": "Muse Spark 1.2",
        "description": "Previous checkpoint, Standard tier, 1M context",
    },
    {
        "id": "muse-spark-1.2-contributor",
        "label": "Muse Spark 1.2 (Contributor)",
        "description": "Previous checkpoint, discounted tier — prompts may train Meta models",
    },
    {
        "id": "muse-spark-1.1",
        "label": "Muse Spark 1.1",
        "description": "Earlier checkpoint, Standard tier",
    },
]

# Cached CLI settings model: (monotonic_ts, model_or_None).
_SETTINGS_MODEL_CACHE: Optional[Tuple[float, Optional[str]]] = None
_SETTINGS_MODEL_CACHE_TTL_SEC = 30.0

# Live Meta Model API catalog overlay for the Muse Code palette.
# (monotonic_ts, models_list, source, error_or_None)
_MUSE_CATALOG_CACHE: Optional[Tuple[float, List[Dict[str, str]], str, Optional[str]]] = None
_MUSE_CATALOG_CACHE_TTL_SEC = 300.0

# Discrete --reasoning-effort levels for --provider meta. "none" is not a CLI
# level (the CLI rejects it); unpinned turns omit the flag entirely.
MUSE_REASONING_EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]


def muse_model_label(model: Optional[str]) -> str:
    """Human label for a Muse model id (`muse-spark-1.3` → `Spark 1.3`)."""
    raw = str(model or DEFAULT_MUSE_MODEL).strip() or DEFAULT_MUSE_MODEL
    for known in MUSE_KNOWN_MODELS:
        if known["id"].lower() == raw.lower():
            return known["label"].replace("Muse ", "", 1)
    return re.sub(r"^muse[-_]", "", raw, flags=re.I).replace("-", " ").title()


def _parse_muse_settings_model(raw: str) -> Optional[str]:
    try:
        data = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _read_native_muse_settings_model() -> Optional[str]:
    """Read ``model`` from the host Muse CLI settings.json, if present."""
    candidates: List[Path] = []
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        candidates.append(Path(xdg) / "muse" / "settings.json")
    candidates.append(Path.home() / ".config" / "muse" / "settings.json")
    for path in candidates:
        try:
            if path.is_file():
                return _parse_muse_settings_model(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return None




def read_muse_cli_settings_model() -> Optional[str]:
    """Return the Muse CLI's configured default model, or None if unknown."""
    return _read_native_muse_settings_model()


def resolve_muse_default_model() -> str:
    """Env ``MUSE_MODEL`` → Muse CLI settings.json → ``DEFAULT_MUSE_MODEL``.

    Cuttle used to hardcode Spark 1.2 and always pass ``--model``, which
    overrode the CLI default (e.g. Spark 1.3 Contributor) while the badge
    still claimed 1.2.
    """
    env_model = (os.getenv("MUSE_MODEL") or "").strip()
    if env_model:
        return env_model

    global _SETTINGS_MODEL_CACHE
    now = time.monotonic()
    if _SETTINGS_MODEL_CACHE is not None:
        ts, cached = _SETTINGS_MODEL_CACHE
        if now - ts < _SETTINGS_MODEL_CACHE_TTL_SEC:
            return cached or DEFAULT_MUSE_MODEL

    found = read_muse_cli_settings_model()
    _SETTINGS_MODEL_CACHE = (now, found)
    return found or DEFAULT_MUSE_MODEL


def clear_muse_default_model_cache() -> None:
    """Test helper — drop the cached CLI settings model."""
    global _SETTINGS_MODEL_CACHE
    _SETTINGS_MODEL_CACHE = None


def clear_muse_catalog_cache() -> None:
    """Drop the Meta Model API catalog overlay (palette refresh)."""
    global _MUSE_CATALOG_CACHE
    _MUSE_CATALOG_CACHE = None










def list_muse_catalog_models(*, refresh: bool = False) -> Dict[str, Any]:
    """Shipped CLI model hints; never fetch with Cuttle provider credentials.

    Muse has no model-list command. Arbitrary model pins remain supported.
    """
    if refresh:
        clear_muse_default_model_cache()
    models = [dict(m) for m in MUSE_KNOWN_MODELS]
    return {"models": models, "count": len(models), "source": "static",
            "error": None, "fetched_at": None}


def refresh_muse_catalog() -> Dict[str, Any]:
    """Refresh native default-model settings and return shipped model hints."""
    return list_muse_catalog_models(refresh=True)


def _default_timeout() -> float:
    raw = (os.getenv("MUSE_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(60.0, min(float(raw), 7200.0))
        except ValueError:
            pass
    return 3600.0


def _status_put(status_queue: Optional["queue_module.Queue"], message: str) -> None:
    if not status_queue:
        return
    try:
        status_queue.put(("status", message))
    except Exception:
        pass


# `muse exec --json` line budget. Tool results embed whole files, and asyncio's
# default 64 KiB stream limit turns those into ValueError instead of an event.
_STDOUT_LINE_LIMIT = 8 * 1024 * 1024

# Reserved key inside the tool-label map: assistant text streamed so far, so the
# "writing: …" preview grows like Cursor's partial output instead of flashing
# one delta at a time.
_WRITING_BUF_KEY = "\x00writing"

# Throttled activity kinds — token-level events would otherwise repaint the chat
# status line dozens of times a second.
_THROTTLED_PREFIXES = ("writing: ", "thinking: ")
_THROTTLE_SEC = 1.2
_HEARTBEAT_SEC = 15.0


def _throttle_kind(activity: str) -> str:
    """Rate-limit runs of the same streaming line, not the first one of a kind."""
    for prefix in _THROTTLED_PREFIXES:
        if activity.startswith(prefix):
            return prefix
    return ""


def _muse_task_label(task_kind: str) -> Optional[str]:
    """``tool.workspace.read_file`` → ``workspace read file``; internals → None.

    Muse schedules reminder and model-step tasks alongside real tool calls, and
    those are pure noise in a chat status line.
    """
    kind = (task_kind or "").strip()
    low = kind.lower()
    if not kind or low.startswith("reminder.") or low.startswith("model."):
        return None
    name = re.sub(r"^(?:tool|builtin|agent)[.:]", "", kind, flags=re.I)
    label = re.sub(r"[\s._:/\-]+", " ", name).strip()
    if len(label) > 100:
        label = label[:99] + "…"
    return label or None


def _first_line(text: Any, limit: int = 70) -> str:
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _muse_activity_for_event(
    event: Dict[str, Any],
    tool_labels: Dict[str, Any],
    tool_count: List[int],
) -> Optional[str]:
    """Map one ``muse exec --json`` event to a chat status line, or None to skip.

    ``tool_labels`` and ``tool_count`` carry state across the stream: task id →
    (index, label) so a later failure can name the tool that broke, and the
    running tool counter that mirrors Cursor Agent's ``tool 3: …`` lines.
    """
    if not isinstance(event, dict):
        return None
    pt = (event.get("payload_type") or "").strip()
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    inner = payload.get("event") if isinstance(payload.get("event"), dict) else {}

    if pt == "run.model.configured":
        model = payload.get("display_label") or payload.get("model_id")
        if isinstance(model, str) and model.strip():
            return f"Muse Code ready (model: {model.strip()})"
        return None

    if pt == "run.lifecycle.started":
        return "Muse Code is thinking…"

    if "reasoning" in pt:
        raw = payload.get("text") or inner.get("text") or inner.get("chunk")
        preview = _first_line(raw, 140)
        return f"thinking: {preview}…" if preview else None

    if pt == "run.output.delta":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        buf = str(tool_labels.get(_WRITING_BUF_KEY) or "") + text
        tool_labels[_WRITING_BUF_KEY] = buf[-4000:]
        preview = buf.replace("\n", " ").strip()[-120:]
        from api.agent_harness.activity import text_preview
        return f"writing: {text_preview(buf)}" if preview else None

    if pt == "task.lifecycle.proposed":
        kind = str(inner.get("task_kind") or "")
        if kind.lower().startswith("model."):
            return "Muse Code is thinking…"
        label = _muse_task_label(kind)
        if not label:
            return None
        tool_count[0] += 1
        index = tool_count[0]
        task_id = str(inner.get("task_id") or payload.get("task_id") or "").strip()
        if task_id:
            tool_labels[task_id] = (index, label)
        return f"tool {index}: {label}"

    if pt == "task.lifecycle.output":
        entry = tool_labels.get(str(inner.get("task_id") or ""))
        if not isinstance(entry, tuple):
            return None
        detail = _first_line(inner.get("chunk"))
        # Structured tool payloads open with `{`/`[` — a lone brace is no signal.
        if not detail or detail[0] in "{[":
            return None
        return f"tool {entry[0]}: {entry[1]} · {detail}"

    if pt == "task.lifecycle.failed":
        entry = tool_labels.get(str(inner.get("task_id") or ""))
        if not isinstance(entry, tuple):
            return None
        reason = _first_line(inner.get("reason"), 60)
        return f"tool failed: {entry[1]}" + (f" — {reason}" if reason else "")

    if pt == "tool.result":
        facts = payload.get("correlation_facts")
        facts = facts if isinstance(facts, dict) else {}
        outcome = str(facts.get("outcome") or "").strip().lower()
        name = str(facts.get("tool_name") or "").strip()
        if name and outcome and outcome != "success":
            return f"tool failed: {re.sub(r'[._]+', ' ', name)} ({outcome})"
        return None

    if pt == "task.lifecycle.status":
        # Only retries are worth a line; attempt 1 is every model step.
        m = re.search(r"attempt (\d+)/(\d+)", str(inner.get("message") or ""))
        if m and m.group(1) != "1":
            return f"Muse Code retrying the model stream (attempt {m.group(1)}/{m.group(2)})…"
        return None

    return None




# Batch/script launchers look like a binary to `shutil.which`. Meta's official
# Windows install is ``muse.cmd`` → PowerShell launcher → ``muse-bin-<ver>.exe``;
# a launcher is only usable when that real binary sits beside it.
_SHIM_EXTENSIONS = {".cmd", ".bat", ".ps1", ".com"}


def _muse_bin_exe_beside(directory: Path) -> Optional[str]:
    """Pick the newest ``muse-bin-*.exe`` next to Meta's Windows install shim."""
    try:
        matches = sorted(
            directory.glob("muse-bin-*.exe"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    for candidate in matches:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def _default_windows_muse_install() -> Optional[str]:
    """``%LOCALAPPDATA%\\Programs\\muse\\muse-bin-*.exe`` from Meta's installer."""
    if os.name != "nt":
        return None
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        return None
    return _muse_bin_exe_beside(Path(local) / "Programs" / "muse")


def _is_native_muse_executable(path: str) -> bool:
    """True for a real launchable native Muse binary (not a script launcher)."""
    if not path or not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() not in _SHIM_EXTENSIONS


def _resolve_native_muse_path(path: str) -> Optional[str]:
    """Normalize a candidate path to a launchable native binary, or None."""
    if not path:
        return None
    if os.path.splitext(path)[1].lower() in _SHIM_EXTENSIONS:
        # Launch the binary beside the launcher (Meta's muse-bin / muse.exe).
        sibling = _muse_bin_exe_beside(Path(path).parent)
        if sibling:
            return sibling
        exe = Path(path).with_suffix(".exe")
        return str(exe) if exe.is_file() else None
    return path if _is_native_muse_executable(path) else None


def _which_muse_native() -> Optional[str]:
    """Native Muse CLI (real ``muse-bin-*.exe`` / ``muse.exe`` / POSIX binary).

    Resolution order on Windows:
      1. ``MUSE_CLI_PATH`` (resolved to sibling ``muse-bin-*.exe`` when needed)
      2. Well-known Meta install under ``%LOCALAPPDATA%\\Programs\\muse``
      3. ``PATH`` via ``shutil.which`` (launchers resolve to their binary)
    """
    override = (os.getenv("MUSE_CLI_PATH") or "").strip()
    if override:
        resolved = _resolve_native_muse_path(override)
        if resolved:
            return resolved

    known = _default_windows_muse_install()
    if known:
        return known

    found = shutil.which("muse")
    if found:
        resolved = _resolve_native_muse_path(found)
        if resolved:
            return resolved
    return None








def muse_available() -> bool:
    return bool(_which_muse_native())


def muse_resolution() -> Dict[str, Any]:
    """Diagnostics for /muse help / install errors."""
    native = _which_muse_native()
    if native:
        return {"mode": "native", "path": native}
    return {"mode": "missing", "path": None}


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_like_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """Return a normalized usage dict if ``obj`` looks like Muse token usage."""
    if not isinstance(obj, dict):
        return None
    # Nested ``usage`` wrappers (usage/read, usage/changed, task details).
    nested = obj.get("usage")
    if isinstance(nested, dict) and nested is not obj:
        inner = _usage_like_dict(nested)
        if inner:
            return inner

    pt = _coerce_int(
        obj.get("input_tokens")
        if obj.get("input_tokens") is not None
        else obj.get("prompt_tokens")
        if obj.get("prompt_tokens") is not None
        else obj.get("inputTokens")
    )
    ct = _coerce_int(
        obj.get("output_tokens")
        if obj.get("output_tokens") is not None
        else obj.get("completion_tokens")
        if obj.get("completion_tokens") is not None
        else obj.get("outputTokens")
    )
    cached = _coerce_int(
        obj.get("cached_tokens")
        if obj.get("cached_tokens") is not None
        else obj.get("cache_read_tokens")
    )
    reasoning = _coerce_int(obj.get("reasoning_tokens"))
    total = _coerce_int(
        obj.get("total_tokens")
        if obj.get("total_tokens") is not None
        else obj.get("totalTokens")
    )
    cost = None
    if obj.get("cost") is not None:
        try:
            cost = float(obj.get("cost"))
        except (TypeError, ValueError):
            cost = None
    if cost is None and obj.get("cost_micros") is not None:
        micros = _coerce_int(obj.get("cost_micros"))
        if micros is not None and micros >= 0:
            cost = micros / 1_000_000.0
    if pt is None and ct is None and total is None and cost is None:
        return None
    if (pt or 0) <= 0 and (ct or 0) <= 0 and (total or 0) <= 0 and cost is None:
        # A usage-shaped object with only cache/reasoning zeros is noise.
        if not cached and not reasoning:
            return None

    out: Dict[str, Any] = {}
    if pt is not None:
        out["input_tokens"] = max(0, pt)
    if ct is not None:
        out["output_tokens"] = max(0, ct)
    if cached is not None and cached > 0:
        out["cached_tokens"] = cached
    if reasoning is not None and reasoning > 0:
        out["reasoning_tokens"] = reasoning
    if total is not None and total > 0:
        out["total_tokens"] = total
    elif (pt or 0) or (ct or 0):
        out["total_tokens"] = max(0, pt or 0) + max(0, ct or 0)
    if cost is not None and cost >= 0:
        out["cost"] = cost
    return out if out else None


def _walk_usage_dicts(node: Any, out: List[Dict[str, Any]], *, depth: int = 0) -> None:
    if depth > 8:
        return
    hit = _usage_like_dict(node)
    if hit:
        out.append(hit)
        # Don't also count the same fields via nested ``usage`` / children.
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_usage_dicts(v, out, depth=depth + 1)
    elif isinstance(node, list):
        for v in node[:50]:
            _walk_usage_dicts(v, out, depth=depth + 1)


def _merge_muse_usage(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine usage snapshots from a Muse JSONL run.

    Prefer the richest single snapshot when totals look cumulative; otherwise
    sum per-task deltas (TaskUsageDetail).
    """
    if not parts:
        return {}
    scored = []
    for u in parts:
        score = int(u.get("total_tokens") or 0) or (
            int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
        )
        scored.append((score, u))
    best_score, best = max(scored, key=lambda t: t[0])
    sum_in = sum(int(u.get("input_tokens") or 0) for u in parts)
    sum_out = sum(int(u.get("output_tokens") or 0) for u in parts)
    sum_score = sum_in + sum_out
    # Cumulative final > sum of parts → take the best snapshot.
    # Otherwise treat as deltas and sum.
    if best_score >= sum_score and best_score > 0:
        chosen = dict(best)
    else:
        chosen = {
            "input_tokens": sum_in,
            "output_tokens": sum_out,
            "total_tokens": sum_score,
        }
        cached = sum(int(u.get("cached_tokens") or 0) for u in parts)
        reasoning = sum(int(u.get("reasoning_tokens") or 0) for u in parts)
        if cached:
            chosen["cached_tokens"] = cached
        if reasoning:
            chosen["reasoning_tokens"] = reasoning
    costs = [float(u["cost"]) for u in parts if u.get("cost") is not None]
    if costs:
        # Prefer max reported cost (usually the run total); fall back to sum.
        chosen["cost"] = max(costs) if max(costs) >= sum(costs) * 0.9 else sum(costs)
    if not chosen.get("total_tokens"):
        chosen["total_tokens"] = int(chosen.get("input_tokens") or 0) + int(
            chosen.get("output_tokens") or 0
        )
    return chosen


def _capture_muse_question(bridge, item: Dict[str, Any], *, complete: bool = True) -> bool:
    """Recognize muse's native input tool items; never infer from prose."""
    name = str(item.get("tool") or item.get("toolName") or item.get("name") or "")
    if name.rsplit(".", 1)[-1] not in ("request_user_input", "request_user_input_async"):
        return False
    args = item.get("args") or item.get("arguments") or item.get("input")
    # Some transports announce the tool before its arguments are complete.
    if not args and not item.get("questions") and not complete:
        return False
    bridge.capture(args or item)
    return True


def _capture_muse_question_event(bridge, event: Dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    if str(event.get("payload_type") or "").startswith("tool."):
        return _capture_muse_question(bridge, payload, complete=event.get("payload_type") == "tool.result")
    inner = payload.get("event") or {}
    if isinstance(inner, dict) and str(inner.get("task_kind") or "").rsplit(".", 1)[-1] in (
        "request_user_input", "request_user_input_async"
    ):
        args = inner.get("args") or inner.get("input")
        if not args and not inner.get("questions") and event.get("payload_type") != "task.lifecycle.failed":
            return False
        bridge.capture(args or inner)
        return True
    return False


def _parse_muse_jsonl(raw: str, *, recover_questions: bool = True) -> Dict[str, Any]:
    """Parse ``muse exec --json`` stdout into display text, session id, errors.

    Terminal ``text`` wins when present. Otherwise streamed ``run.output.delta``
    chunks are joined so a killed/timed-out turn still surfaces partial reply
    text instead of an empty bubble.
    """
    from api.agent_harness.questions import QuestionBridge

    questions = QuestionBridge()
    session_id: Optional[str] = None
    messages: List[str] = []
    delta_chunks: List[str] = []
    errors: List[str] = []
    usage_parts: List[Dict[str, Any]] = []
    terminal: Optional[str] = None

    for line in (raw or "").splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue

        stream = ev.get("stream")
        if isinstance(stream, dict) and (stream.get("kind") or "") == "session":
            sid = stream.get("id")
            if isinstance(sid, str) and _UUID_RE.match(sid.strip()):
                session_id = sid.strip()

        pt = (ev.get("payload_type") or "").strip()
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}

        _capture_muse_question_event(questions, ev)

        if pt == "run.output.delta":
            text = payload.get("text")
            if isinstance(text, str) and text:
                delta_chunks.append(text)
        elif pt == "run.terminal.completed":
            terminal = str(payload.get("terminal") or "completed")
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(text.strip())
            # Terminal payloads sometimes carry final usage — keep scanning below.
        elif pt == "run.terminal.failed":
            terminal = str(payload.get("terminal") or "failed")
            reason = payload.get("reason") or payload.get("text") or payload.get("message")
            if isinstance(reason, str) and reason.strip():
                errors.append(reason.strip())
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(text.strip())

        # Soft-fail reasons on task lifecycle (keep last few).
        if pt == "task.lifecycle.failed":
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            reason = event.get("reason") or payload.get("reason")
            if isinstance(reason, str) and reason.strip() and len(errors) < 5:
                errors.append(reason.strip())

        # Muse embeds TaskUsageDetail / Usage under many payload types (not only
        # ones with "usage" in the name). Walk the whole event.
        found: List[Dict[str, Any]] = []
        _walk_usage_dicts(ev, found)
        if found:
            # Prefer explicit usage payload types when present on this line.
            if "usage" in pt.lower() or pt.endswith("usage_attribution"):
                usage_parts.extend(found)
            else:
                usage_parts.extend(found)

    usage = _merge_muse_usage(usage_parts)
    display = "\n\n".join(messages).strip()
    if not display and delta_chunks:
        display = "".join(delta_chunks).strip()
    return {
        "session_id": session_id,
        "output": questions.render(display) if recover_questions else display,
        "errors": errors,
        "usage": usage,
        "terminal": terminal,
    }


def _muse_session_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    stream = event.get("stream")
    if not isinstance(stream, dict) or (stream.get("kind") or "") != "session":
        return None
    sid = stream.get("id")
    if isinstance(sid, str) and _UUID_RE.match(sid.strip()):
        return sid.strip()
    return None


def _tool_digest_from_labels(tool_labels: Dict[str, Any], *, limit: int = 12) -> str:
    """Compact ``1. workspace read file; 2. shell`` trail for interrupt notices."""
    rows: List[Tuple[int, str]] = []
    for key, entry in tool_labels.items():
        if key == _WRITING_BUF_KEY:
            continue
        if isinstance(entry, tuple) and len(entry) == 2:
            try:
                rows.append((int(entry[0]), str(entry[1])))
            except (TypeError, ValueError):
                continue
    if not rows:
        return ""
    rows.sort(key=lambda row: row[0])
    shown = rows[-max(1, limit) :]
    return "; ".join(f"{idx}. {label}" for idx, label in shown)


def _interrupted_muse_result(
    *,
    stdout_parts: List[bytes],
    stderr_parts: List[bytes],
    reason: str,
    last_activity: str,
    tool_labels: Dict[str, Any],
    elapsed_sec: float,
    cancelled: bool = False,
    timed_out: bool = False,
    fallback_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a failure result that keeps session id + partial work for the chat."""
    out = b"".join(stdout_parts).decode("utf-8", errors="replace")
    err = b"".join(stderr_parts).decode("utf-8", errors="replace")
    parsed = _parse_muse_jsonl(out, recover_questions=not cancelled)
    partial = (parsed.get("output") or "").strip()
    writing = str(tool_labels.get(_WRITING_BUF_KEY) or "").strip()
    if not partial and writing:
        partial = writing
    session_id = parsed.get("session_id") or (fallback_session_id or None)

    digest: List[str] = [
        f"*Muse Code interrupted after {int(elapsed_sec)}s — {reason}.*"
    ]
    if last_activity and last_activity not in ("starting",):
        digest.append(f"*Last activity: {last_activity}*")
    tools = _tool_digest_from_labels(tool_labels)
    if tools:
        digest.append(f"*Tools this turn:* {tools}")
    if session_id:
        digest.append(
            "*Muse session id was saved — the next `/muse` turn resumes the "
            "same Muse Code transcript (renders/edits from this attempt stay "
            "in that session).*"
        )
    else:
        digest.append(
            "*No Muse session id was captured before the interrupt — this "
            "turn's Muse-local transcript may not resume.*"
        )
    if not partial and err.strip():
        digest.append(f"```\n{err.strip()[:2000]}\n```")

    body_parts: List[str] = []
    if partial:
        body_parts.append(partial)
    body_parts.append("\n".join(digest))
    return {
        "success": False,
        "error": reason,
        "output": "\n\n".join(body_parts).strip(),
        "usage": parsed.get("usage") or {},
        "muse_session_id": session_id,
        "returncode": None,
        "stderr": err.strip()[:4000] if err else "",
        "timed_out": bool(timed_out),
        "cancelled": bool(cancelled),
    }


def usage_for_query_report(usage: Dict[str, Any], model: str) -> Dict[str, Any]:
    pt = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
    tt = int(usage.get("total_tokens", 0) or 0)
    if tt <= 0 and (pt or ct):
        tt = pt + ct
    m = (model or DEFAULT_MUSE_MODEL).strip() or DEFAULT_MUSE_MODEL
    out = {
        "input_tokens": pt,
        "output_tokens": ct,
        "total_tokens": tt,
        "model": m,
    }
    try:
        cached = int(
            usage.get("cached_tokens")
            or usage.get("cache_read_tokens")
            or 0
        )
    except (TypeError, ValueError):
        cached = 0
    if cached > 0:
        out["cache_read_tokens"] = cached
    if usage.get("cost") is not None:
        try:
            out["cost"] = float(usage["cost"])
        except (TypeError, ValueError):
            pass
    return out




def _build_argv(
    *,
    muse_bin: str,
    prompt: str,
    cwd: str,
    resume: Optional[str],
    model: Optional[str],
    provider: Optional[str],
    reasoning_effort: Optional[str],
    max_model_steps: Optional[int],
    prompt_file: Optional[str],
    yolo: bool,
    context_compaction_strategy: Optional[str] = None,
    context_compaction_soft_threshold: Optional[float] = None,
    context_compaction_hard_threshold: Optional[float] = None,
) -> Tuple[List[str], Optional[str]]:
    """Return (argv_for_create_subprocess_exec, optional_prompt_file_to_cleanup)."""
    workspace = str(Path(cwd).resolve())
    muse_args: List[str] = [
        muse_bin,
        "exec",
        "--json",
        "--workspace",
        workspace,
    ]
    if yolo:
        muse_args.append("--yolo")
    else:
        muse_args.extend(["--disable-approval", "--trust-workspace"])

    rid = (resume or "").strip()
    if rid:
        muse_args.extend(["--session-id", rid, "--allow-workspace-switch"])

    if provider:
        muse_args.extend(["--provider", provider])
    # --model is Meta-provider only (echo rejects it).
    if model and (not provider or provider.strip().lower() == "meta"):
        muse_args.extend(["--model", model])
    effort_value = (reasoning_effort or "").strip()
    # "none" is not a CLI level (rejected by --provider meta); historic pins
    # meaning "no pin" must omit the flag, never pass it literally.
    if (
        effort_value
        and effort_value.lower() != "none"
        and (not provider or provider.strip().lower() == "meta")
    ):
        muse_args.extend(["--reasoning-effort", effort_value])
    if max_model_steps is not None:
        muse_args.extend(["--max-model-steps", str(int(max_model_steps))])
    strategy = (context_compaction_strategy or "").strip()
    if strategy:
        muse_args.extend(["--context-compaction-strategy", strategy])
    if context_compaction_soft_threshold is not None:
        muse_args.extend(
            [
                "--context-compaction-soft-threshold",
                str(float(context_compaction_soft_threshold)),
            ]
        )
    if context_compaction_hard_threshold is not None:
        muse_args.extend(
            [
                "--context-compaction-hard-threshold",
                str(float(context_compaction_hard_threshold)),
            ]
        )

    cleanup: Optional[str] = None
    use_file = bool(prompt_file) or len(prompt) > _MAX_PROMPT_FOR_ARGV
    if use_file:
        if prompt_file:
            pf = prompt_file
        else:
            fd, pf = tempfile.mkstemp(prefix="cuttle_muse_", suffix=".txt")
            os.close(fd)
            Path(pf).write_text(prompt, encoding="utf-8")
            cleanup = pf
        mapped = str(Path(pf).resolve())
        muse_args.extend(["--prompt-file", mapped])
    else:
        muse_args.append(prompt)

    return muse_args, cleanup


class MuseCliTool:
    """Non-interactive Muse Code runs for Cuttle slash + remote-agent backends."""

    def __init__(
        self,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        yolo: bool = True,
    ):
        env_model = (os.getenv("MUSE_MODEL") or "").strip()
        # Explicit constructor arg wins; otherwise env / CLI settings / static.
        if model is not None and str(model).strip():
            self.model = str(model).strip()
        elif env_model:
            self.model = env_model
        else:
            self.model = resolve_muse_default_model()
        env_provider = (os.getenv("MUSE_PROVIDER") or "").strip()
        self.provider = (provider or env_provider or "").strip() or None
        self.reasoning_effort = (reasoning_effort or "").strip() or None
        self.yolo = bool(yolo)

    async def execute_prompt(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        resume: Optional[str] = None,
        status_queue: Optional["queue_module.Queue"] = None,
        provider: Optional[str] = None,
        max_model_steps: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        cancel_event: Any = None,
        context_compaction_strategy: Optional[str] = None,
        context_compaction_soft_threshold: Optional[float] = None,
        context_compaction_hard_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not (prompt or "").strip():
            return {"success": False, "error": "No prompt provided", "output": ""}

        muse_bin = _which_muse_native()
        if not muse_bin:
            return {
                "success": False,
                "error": (
                    "Muse Code CLI not found. Install the native build for this host "
                    "and ensure `muse` is on PATH (or set MUSE_CLI_PATH). "
                ),
                "output": "",
            }

        workdir = cwd or os.getcwd()
        if not os.path.isdir(workdir):
            return {"success": False, "error": f"Invalid working directory: {workdir}", "output": ""}

        timeout_sec = float(timeout) if timeout is not None else _default_timeout()
        prov = (provider or self.provider or "").strip() or None
        effort = (reasoning_effort or self.reasoning_effort or "").strip() or None

        argv, cleanup = _build_argv(
            muse_bin=muse_bin,
            prompt=prompt,
            cwd=workdir,
            resume=resume,
            model=self.model,
            provider=prov,
            reasoning_effort=effort,
            max_model_steps=max_model_steps,
            prompt_file=None,
            yolo=self.yolo,
            context_compaction_strategy=context_compaction_strategy,
            context_compaction_soft_threshold=context_compaction_soft_threshold,
            context_compaction_hard_threshold=context_compaction_hard_threshold,
        )

        _status_put(
            status_queue,
            "Resuming Muse Code…" if (resume or "").strip() else "Starting Muse Code…",
        )

        from api.agent_harness.questions import QuestionBridge

        pending_questions = QuestionBridge()
        stdout_parts: List[bytes] = []
        stderr_parts: List[bytes] = []
        task_labels: Dict[str, Any] = {}
        tool_counter = [0]
        started_at = time.monotonic()
        last_emit = [started_at]
        last_activity = ["starting"]
        from api.agent_harness.activity import TextActivityLog, put_status
        text_log = TextActivityLog("muse")
        text_log.start("writing")
        text_log.start("thinking")
        seen_session_id: List[Optional[str]] = [(resume or "").strip() or None]
        persisted_session_id: List[Optional[str]] = [None]

        def _emit(activity: str) -> bool:
            now = time.monotonic()
            if activity == last_activity[0]:
                return False
            kind = _throttle_kind(activity)
            if (
                kind
                and kind == _throttle_kind(last_activity[0])
                and (now - last_emit[0]) < _THROTTLE_SEC
            ):
                return False
            last_emit[0] = now
            last_activity[0] = activity
            put_status(status_queue, activity, preview_only=bool(kind))
            return True

        def _persist_resume_early(sid: Optional[str]) -> None:
            """Pin Muse session as soon as the CLI emits it (survive kill/timeout)."""
            if not sid or not chat_session_id:
                return
            seen_session_id[0] = sid
            if persisted_session_id[0] == sid:
                return
            persisted_session_id[0] = sid
            try:
                from scripts.utilities.muse_cli_session_store import save_muse_resume_id

                save_muse_resume_id(workdir, chat_session_id, sid)
            except Exception:
                pass

        try:
            from api.agent_harness.timeouts import ActivityDeadline
            from scripts.utilities.agent_process import kill_process_tree

            # Idle budget (resets on stdout/stderr), not a hard wall clock — long
            # productive Blender/tool turns must not die at 3600s of continuous work.
            deadline = ActivityDeadline(timeout_sec)

            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=workdir,
                env=agent_cli_env(),
                limit=_STDOUT_LINE_LIMIT,
            )
            # Register with chat_run_registry so Stop can cancel the native process
            if chat_session_id:
                try:
                    from api.chat_run_registry import attach_process
                    attach_process(chat_session_id, proc)
                except Exception:
                    pass

            async def _pump_stderr() -> None:
                try:
                    while True:
                        chunk = await proc.stderr.read(4096)
                        if not chunk:
                            return
                        deadline.poke()
                        stderr_parts.append(chunk)
                except Exception:
                    return

            async def _heartbeat() -> None:
                """A single tool call can run for minutes with no events."""
                while True:
                    await asyncio.sleep(2.0)
                    if time.monotonic() - last_emit[0] < _HEARTBEAT_SEC:
                        continue
                    last_emit[0] = time.monotonic()
                    elapsed = int(time.monotonic() - started_at)
                    _status_put(
                        status_queue,
                        f"Muse Code working… {elapsed}s ({last_activity[0]})",
                    )

            err_task = asyncio.create_task(_pump_stderr())
            beat = (
                asyncio.ensure_future(_heartbeat()) if status_queue is not None else None
            )
            timed_out_reason: Optional[str] = None
            was_cancelled = False
            try:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    was_cancelled = True
                    await kill_process_tree(proc)
                else:
                    while True:
                        if cancel_event is not None and getattr(
                            cancel_event, "is_set", lambda: False
                        )():
                            was_cancelled = True
                            await kill_process_tree(proc)
                            break
                        expiry = deadline.check()
                        if expiry:
                            timed_out_reason = expiry
                            await kill_process_tree(proc)
                            break
                        poll = deadline.next_wake(poll=1.0)
                        try:
                            line = await asyncio.wait_for(
                                proc.stdout.readline(), timeout=poll
                            )
                        except asyncio.TimeoutError:
                            continue
                        except ValueError:
                            # One event outgrew the stream limit (a tool result
                            # carrying a whole file); drop it and keep the run alive.
                            deadline.poke()
                            continue
                        if not line:
                            break
                        deadline.poke()
                        stdout_parts.append(line)
                        try:
                            ev = json.loads(line.decode("utf-8", errors="replace"))
                        except (json.JSONDecodeError, UnicodeError):
                            continue
                        if not isinstance(ev, dict):
                            continue
                        if _capture_muse_question_event(pending_questions, ev):
                            _emit("Preparing Cuttle question form…")
                            await kill_process_tree(proc)
                            break
                        sid = _muse_session_id_from_event(ev)
                        if sid:
                            _persist_resume_early(sid)
                        activity = _muse_activity_for_event(
                            ev, task_labels, tool_counter
                        )
                        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                        inner = payload.get("event") if isinstance(payload.get("event"), dict) else {}
                        pt = str(ev.get("payload_type") or "")
                        kind = "writing" if pt == "run.output.delta" else "thinking" if "reasoning" in pt else None
                        if kind:
                            raw = payload.get("text") or inner.get("text") or inner.get("chunk") or ""
                            raw = raw if isinstance(raw, str) else ""
                            if kind == "thinking" and "delta" not in pt:
                                text_log.save(kind, raw)
                                full_text = raw
                            else:
                                full_text = text_log.delta(kind, raw)
                            if activity and _emit(activity):
                                text_log.save(kind, full_text)
                        else:
                            if pt == "run.terminal.completed" and isinstance(payload.get("text"), str):
                                text_log.save("writing", payload["text"])
                            if activity:
                                _emit(activity)
                                if activity.startswith("tool "):
                                    text_log.flush()
                                    text_log.start("writing")
                                    text_log.start("thinking")

                    # Only wait for a clean EOF exit — after kill/cancel the
                    # process may already be gone and wait() can hang forever
                    # on stubs or zombie wrappers.
                    if not was_cancelled and timed_out_reason is None:
                        try:
                            await proc.wait()
                        except ProcessLookupError:
                            pass
            finally:
                err_task.cancel()
                if beat is not None:
                    beat.cancel()
                for t in (err_task, beat):
                    if t is None:
                        continue
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

            elapsed = time.monotonic() - started_at
            if was_cancelled:
                return _interrupted_muse_result(
                    stdout_parts=stdout_parts,
                    stderr_parts=stderr_parts,
                    reason="cancelled (Stop or chat deleted)",
                    last_activity=last_activity[0],
                    tool_labels=task_labels,
                    elapsed_sec=elapsed,
                    cancelled=True,
                    fallback_session_id=seen_session_id[0],
                )
            if timed_out_reason:
                return _interrupted_muse_result(
                    stdout_parts=stdout_parts,
                    stderr_parts=stderr_parts,
                    reason=f"Muse Code {timed_out_reason}",
                    last_activity=last_activity[0],
                    tool_labels=task_labels,
                    elapsed_sec=elapsed,
                    timed_out=True,
                    fallback_session_id=seen_session_id[0],
                )

            stdout = b"".join(stdout_parts)
            stderr = b"".join(stderr_parts)
        except Exception as e:
            return {"success": False, "error": str(e), "output": ""}
        finally:
            text_log.flush()
            if cleanup:
                try:
                    os.unlink(cleanup)
                except OSError:
                    pass

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        parsed = _parse_muse_jsonl(out)
        if parsed.get("session_id"):
            _persist_resume_early(parsed["session_id"])
        ok = proc.returncode == 0 and (
            parsed.get("terminal") in (None, "completed") or bool(parsed.get("output"))
        )
        # Echo/meta can exit 0 with terminal completed even when observers fail.
        if proc.returncode == 0 and parsed.get("output"):
            ok = True
        elif proc.returncode != 0:
            ok = False

        display = parsed.get("output") or ""
        if pending_questions.pending:
            ok = True
            display = pending_questions.render(display)
        if not ok and not display and err.strip():
            display = err.strip()
        error_msg = None
        if not ok:
            if parsed.get("errors"):
                error_msg = "; ".join(parsed["errors"][:3])
            else:
                error_msg = err.strip() or f"exit {proc.returncode}"

        return {
            "success": ok,
            "output": display,
            "error": error_msg,
            "usage": parsed.get("usage") or {},
            "muse_session_id": parsed.get("session_id") or seen_session_id[0],
            "returncode": proc.returncode,
            "stderr": err.strip()[:4000] if err else "",
        }
