"""
Hermes Agent CLI integration — runs NousResearch/hermes-agent via quiet chat
(``hermes chat -Q -q``) so Cuttle can resume per-chat sessions and surface
Cursor-like live status.

Quiet mode prints ONLY the final response on stdout and ``session_id: …`` on
stderr (no banner). Token streaming callbacks are suppressed by Hermes in this
mode; Cuttle polls ``%LOCALAPPDATA%\\hermes\\state.db`` for thinking/tool/writing
status (Cursor-style typing line) and reads the same DB after the turn for
input/output/cache token counts + estimated cost for the bubble footer.

Default chain (matches the user's stack):
    Cuttle -> hermes chat -Q -q -> OpenRouter / custom provider -> model

Resolution / config:
  - hermes.exe is found on PATH, else at the standard Windows install location
    (%LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe), overridable
    via the HERMES_CLI_PATH env var.
  - Model + provider come from ~/.hermes/config.yaml and can be overridden per
    call / per-chat palette pin.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TYPE_CHECKING

from scripts.utilities.agent_process import (
    attach_to_chat_run,
    format_interrupt_notice,
    run_interruptible,
)

if TYPE_CHECKING:
    import queue as queue_module

# asyncio.create_subprocess_exec uses CreateProcessW on Windows (limit ~32767
# chars for the whole command line), so we can safely pass sizeable prompts on
# argv. Keep a margin for the rest of the args.
_MAX_PROMPT_FOR_ARGV = 30000

# Lean coding default + memory/session_search so Hermes can persist MEMORY.md/USER.md
# and recall past sessions. Avoid the full "hermes-cli" preset (browser, vision,
# image-gen, tts, cron, ...) — too slow to prefill on a local 30B model.
_DEFAULT_TOOLSETS = "terminal,file,web,memory,session_search"

# Hermes VALID_REASONING_EFFORTS + "none" (see hermes_constants.parse_reasoning_effort).
# Muse's "ultra" does not exist here.
HERMES_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")

# Curated palette picks. Free-form ids are still accepted via /hermes model <id>.
# provider is optional — used when resolving a pin so -m + --provider stay aligned.
HERMES_KNOWN_MODELS: List[Dict[str, str]] = [
    {
        "id": "z-ai/glm-5.3-flash",
        "label": "GLM 5.3 Flash",
        "description": "OpenRouter · Z.ai (fast)",
        "provider": "openrouter",
    },
    {
        "id": "qwen3-coder",
        "label": "Qwen3 Coder (local)",
        "description": "llama.cpp via Hermes custom provider",
        "provider": "custom",
    },
    {
        "id": "anthropic/claude-sonnet-4",
        "label": "Claude Sonnet 4",
        "description": "OpenRouter · Anthropic",
        "provider": "openrouter",
    },
    {
        "id": "openai/gpt-4o-mini",
        "label": "GPT-4o mini",
        "description": "OpenRouter · OpenAI",
        "provider": "openrouter",
    },
    {
        "id": "deepseek/deepseek-chat",
        "label": "DeepSeek Chat",
        "description": "OpenRouter · DeepSeek (reasoning-capable)",
        "provider": "openrouter",
    },
]

# OpenRouter only forwards reasoning extra_body for certain model families;
# z-ai/ is not on Hermes' allowlist — effort pins are advisory for those.
_HERMES_REASONING_MODEL_PREFIXES = (
    "deepseek/",
    "anthropic/",
    "openai/",
    "x-ai/",
    "google/gemini-2",
    "qwen/qwen3",
    "xiaomi/",
)

_config_effort_lock = threading.Lock()

# Qwen3-Coder on llama.cpp sometimes emits Hermes-style tool XML as plain text
# instead of structured tool_calls. Hermes -z treats that as the final answer.
_LEAKED_TOOL_XML_RE = re.compile(r"<function=\w+>", re.IGNORECASE)

_RETRY_PROMPT_SUFFIX = (
    "\n\n[Cuttle] Your previous reply included raw <function=...> tool XML in the "
    "message body. Do not print tool calls as text — invoke tools through the tool "
    "calling API so they actually run, then answer with findings."
)


def _output_has_leaked_tool_calls(text: str) -> bool:
    return bool(_LEAKED_TOOL_XML_RE.search(text or ""))


def _resolve_git_bash_path() -> Optional[str]:
    """Prefer Git Bash over WSL's C:\\Windows\\System32\\bash.exe on Windows."""
    override = (os.getenv("HERMES_GIT_BASH_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    if sys.platform != "win32":
        return None
    local_app = os.environ.get("LOCALAPPDATA") or ""
    for candidate in (
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
        os.path.join(local_app, "hermes", "git", "bin", "bash.exe"),
        os.path.join(local_app, "hermes", "git", "usr", "bin", "bash.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _hermes_home() -> Path:
    override = (os.getenv("HERMES_HOME") or "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return Path(local) / "hermes"
    return Path.home() / ".hermes"


# Providers that talk to a machine-local OpenAI-compatible server (not cloud APIs).
_LOCAL_HERMES_PROVIDERS = frozenset(
    {"custom", "ollama", "vllm", "llamacpp", "lmstudio"}
)


def load_hermes_model_config() -> Dict[str, Optional[str]]:
    """Read ``model.default`` / ``provider`` / ``base_url`` from Hermes config.yaml."""
    path = _hermes_home() / "config.yaml"
    out: Dict[str, Optional[str]] = {
        "default": None,
        "provider": None,
        "base_url": None,
    }
    if not path.is_file():
        return out
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, dict):
        return out
    for key in ("default", "provider", "base_url"):
        val = model.get(key)
        if val is None and key == "default":
            val = model.get("model")
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def hermes_uses_local_backend() -> bool:
    """True when Hermes is configured for a local endpoint (needs llama.cpp / Ollama / etc.).

    Cloud providers (OpenRouter, Anthropic, …) return False so Cuttle must not
    gate ``/hermes`` on launching llama-server.
    """
    cfg = load_hermes_model_config()
    provider = (cfg.get("provider") or "").strip().lower()
    base_url = (cfg.get("base_url") or "").strip().lower()
    if provider.startswith("custom:"):
        return True
    if provider in _LOCAL_HERMES_PROVIDERS:
        return True
    if provider in ("", "auto"):
        # No explicit cloud provider — treat localhost endpoints as local;
        # empty config keeps Cuttle's historical local-first default.
        if not base_url:
            return True
        return any(
            token in base_url
            for token in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]")
        )
    return False


def resolve_hermes_runtime(
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict[str, str]:
    """Resolve model/provider for a Hermes run (explicit override → config → local default)."""
    cfg = load_hermes_model_config()
    mid = (str(model).strip() if model else "") or (cfg.get("default") or "") or "qwen3-coder"
    explicit_provider = (str(provider).strip() if provider else "")
    inferred = infer_hermes_provider_for_model(mid) if not explicit_provider else None
    prov = (
        explicit_provider
        or inferred
        or (cfg.get("provider") or "")
        or ("custom" if hermes_uses_local_backend() else "openrouter")
    )
    return {"model": mid, "provider": prov}


def resolve_hermes_default_model() -> str:
    """Default Hermes model from config.yaml (fallback: qwen3-coder)."""
    cfg = load_hermes_model_config()
    return (cfg.get("default") or "").strip() or "qwen3-coder"


def hermes_model_label(model: Optional[str]) -> str:
    mid = (model or "").strip()
    if not mid:
        return resolve_hermes_default_model()
    for known in HERMES_KNOWN_MODELS:
        if known["id"].lower() == mid.lower():
            return str(known["label"])
    # Shorten openrouter-style ids: z-ai/glm-5.3-flash → GLM 5.3 Flash
    leaf = mid.split("/")[-1] if "/" in mid else mid
    pretty = leaf.replace("-", " ").replace("_", " ")
    return pretty.title() if pretty else mid


def load_hermes_model_aliases() -> Dict[str, Dict[str, str]]:
    """Read ``model_aliases`` from Hermes config.yaml (alias → {model, provider, …})."""
    path = _hermes_home() / "config.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    raw = data.get("model_aliases") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(val, dict):
            entry = {
                k: str(v).strip()
                for k, v in val.items()
                if isinstance(v, str) and str(v).strip()
            }
            out[key.strip()] = entry
        elif isinstance(val, str) and val.strip():
            out[key.strip()] = {"model": val.strip()}
    return out


def infer_hermes_provider_for_model(model: Optional[str]) -> Optional[str]:
    """Best-effort provider for a model pin (known list → aliases → slash heuristic)."""
    mid = (model or "").strip()
    if not mid:
        return None
    for known in HERMES_KNOWN_MODELS:
        if known["id"].lower() == mid.lower() and known.get("provider"):
            return str(known["provider"]).strip() or None
    aliases = load_hermes_model_aliases()
    alias = aliases.get(mid)
    if isinstance(alias, dict) and alias.get("provider"):
        return str(alias["provider"]).strip() or None
    for alias_entry in aliases.values():
        if not isinstance(alias_entry, dict):
            continue
        if str(alias_entry.get("model") or "").strip().lower() == mid.lower():
            prov = str(alias_entry.get("provider") or "").strip()
            if prov:
                return prov
    if "/" in mid:
        return "openrouter"
    return None


def list_hermes_known_models() -> List[Dict[str, str]]:
    """Palette model rows: curated list + config default + aliases (deduped)."""
    seen: set[str] = set()
    rows: List[Dict[str, str]] = []

    def _add(mid: str, label: str, description: str = "", provider: str = "") -> None:
        key = mid.lower()
        if not mid or key in seen:
            return
        seen.add(key)
        entry: Dict[str, str] = {"id": mid, "label": label}
        if description:
            entry["description"] = description
        if provider:
            entry["provider"] = provider
        rows.append(entry)

    for known in HERMES_KNOWN_MODELS:
        _add(
            known["id"],
            known["label"],
            known.get("description") or "",
            known.get("provider") or "",
        )
    default = resolve_hermes_default_model()
    _add(default, hermes_model_label(default), "Hermes config.yaml default")
    for alias, entry in load_hermes_model_aliases().items():
        mid = str((entry or {}).get("model") or alias).strip() or alias
        prov = str((entry or {}).get("provider") or "").strip()
        _add(mid, hermes_model_label(mid), f"Alias `{alias}`", prov)
        if alias.lower() != mid.lower():
            _add(alias, f"{hermes_model_label(mid)} ({alias})", "Hermes model alias", prov)
    return rows


def hermes_model_supports_reasoning_extra_body(model: Optional[str]) -> bool:
    """Mirror Hermes' OpenRouter reasoning-gate (z-ai/ is currently excluded)."""
    mid = (model or "").strip().lower()
    if not mid:
        return False
    return any(mid.startswith(p) for p in _HERMES_REASONING_MODEL_PREFIXES)


def load_hermes_config_reasoning_effort() -> str:
    """Current ``agent.reasoning_effort`` from Hermes config.yaml (may be empty)."""
    path = _hermes_home() / "config.yaml"
    if not path.is_file():
        return ""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    agent = data.get("agent") if isinstance(data, dict) else None
    if not isinstance(agent, dict):
        return ""
    raw = agent.get("reasoning_effort")
    if isinstance(raw, str):
        return raw.strip().lower()
    return ""


def write_hermes_config_reasoning_effort(effort: str) -> None:
    """Atomically set ``agent.reasoning_effort`` in Hermes config.yaml."""
    path = _hermes_home() / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required to update Hermes config.yaml") from exc

    with _config_effort_lock:
        data: Dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        agent = data.get("agent")
        if not isinstance(agent, dict):
            agent = {}
            data["agent"] = agent
        agent["reasoning_effort"] = str(effort or "").strip().lower()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        tmp.replace(path)


@contextmanager
def hermes_reasoning_effort_override(effort: Optional[str]) -> Iterator[None]:
    """Apply a per-chat effort pin to config.yaml for one Hermes turn, then restore.

    One-shot ``hermes -z`` has no effort CLI flag. Effort is applied by
    temporarily writing ``agent.reasoning_effort`` into config.yaml for the
    turn; Hermes oneshot now reads that into ``AIAgent(reasoning_config=…)``
    (patched in hermes_cli/oneshot.py). Snapshot/restore keeps interactive
    Hermes and other chats safe.
    """
    pinned = str(effort or "").strip().lower()
    if not pinned:
        yield
        return
    if pinned not in HERMES_REASONING_EFFORTS:
        yield
        return
    previous = load_hermes_config_reasoning_effort()
    if previous == pinned:
        yield
        return
    write_hermes_config_reasoning_effort(pinned)
    try:
        yield
    finally:
        try:
            write_hermes_config_reasoning_effort(previous)
        except Exception as exc:
            print(f"[hermes] failed to restore reasoning_effort={previous!r}: {exc}", flush=True)


def _format_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _poll_hermes_live_status(since_ts: float) -> Optional[str]:
    """Best-effort Cursor-style live status from ~/.hermes/state.db.

    Hermes quiet/oneshot modes suppress stream callbacks, so Cuttle polls the
    same session DB Hermes writes while tools/thinking run — mirroring how
    Cursor surfaces ``thinking:`` / ``tool:`` / ``writing:`` on the status line
    (not token-by-token into the bubble).
    """
    db_path = _hermes_home() / "state.db"
    if not db_path.is_file():
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, tool_call_count, message_count
            FROM sessions
            WHERE started_at >= ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (since_ts - 5.0,),
        ).fetchone()
        if not row:
            conn.close()
            return None
        sid = row["id"]
        msgs = conn.execute(
            """
            SELECT role, tool_name, content, reasoning_content, tool_calls
            FROM messages
            WHERE session_id = ? AND active = 1
            ORDER BY id DESC
            LIMIT 12
            """,
            (sid,),
        ).fetchall()
        conn.close()

        tool_n = int(row["tool_call_count"] or 0)
        hints: List[str] = []
        for m in msgs:
            tool_name = (m["tool_name"] or "").strip()
            if tool_name:
                label = f"tool {tool_n}: {tool_name}" if tool_n else f"tool: {tool_name}"
                hints.append(label)
                continue
            raw_calls = m["tool_calls"]
            if raw_calls:
                try:
                    parsed = json.loads(raw_calls) if isinstance(raw_calls, str) else raw_calls
                    if isinstance(parsed, list) and parsed:
                        fn = parsed[0].get("function") if isinstance(parsed[0], dict) else None
                        name = (
                            (fn or {}).get("name")
                            if isinstance(fn, dict)
                            else parsed[0].get("name")
                        )
                        if name:
                            label = f"tool {tool_n}: {name}" if tool_n else f"tool: {name}"
                            hints.append(label)
                            continue
                except Exception:
                    pass
            reasoning = (m["reasoning_content"] or "").strip()
            if reasoning and len(reasoning) > 16:
                preview = reasoning.replace("\n", " ").strip()
                hints.append(f"thinking: {preview[:120]}…")
                break
            content = (m["content"] or "").strip()
            if m["role"] != "assistant" or not content:
                continue
            if "think" in content.lower() or "redacted_thinking" in content.lower():
                inner = re.sub(r"</?think>", "", content, flags=re.IGNORECASE).strip()
                inner = re.sub(
                    r"</?redacted_thinking>", "", inner, flags=re.IGNORECASE
                ).strip()
                if len(inner) > 16:
                    preview = inner.replace("\n", " ").strip()
                    hints.append(f"thinking: {preview[:120]}…")
                    break
            # Plain assistant draft — same "writing:" cue Cursor uses.
            preview = content.replace("\n", " ").strip()
            if len(preview) > 8:
                tail = preview[-120:] if len(preview) > 120 else preview
                hints.append(f"writing: …{tail}" if len(preview) > 120 else f"writing: {tail}")
                break

        if not hints and tool_n:
            hints.append(f"{tool_n} tool calls so far")
        elif not hints and row["message_count"]:
            hints.append(f"{row['message_count']} messages so far")
        return " · ".join(hints[:2]) if hints else None
    except Exception:
        return None


def _emit_status(status_queue: Optional["queue_module.Queue"], message: str) -> None:
    if not status_queue or not message:
        return
    try:
        status_queue.put_nowait(("status", message))
    except Exception:
        pass


async def _status_heartbeat(
    status_queue: Optional["queue_module.Queue"],
    start_ts: float,
    stop_event: asyncio.Event,
    working_label: str = "Hermes",
) -> None:
    last_msg = ""
    label = (working_label or "Hermes").strip() or "Hermes"
    _emit_status(status_queue, f"Hermes Agent ready ({label})")
    while not stop_event.is_set():
        elapsed = _format_elapsed(time.time() - start_ts)
        detail = _poll_hermes_live_status(start_ts)
        if detail:
            msg = f"Hermes Agent · {elapsed} · {detail}"
        else:
            msg = f"Hermes Agent working… {elapsed} ({label})"
        if msg != last_msg:
            _emit_status(status_queue, msg)
            last_msg = msg
        try:
            # Match Cursor's ~1.2s status cadence more closely than the old 3s poll.
            await asyncio.wait_for(stop_event.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            pass


_SESSION_ID_RE = re.compile(r"(?im)^\s*session_id:\s*(\S+)\s*$")


def _parse_hermes_session_id(stderr_text: str) -> Optional[str]:
    """Extract ``session_id: …`` that Hermes quiet mode prints on stderr."""
    if not stderr_text:
        return None
    matches = _SESSION_ID_RE.findall(stderr_text)
    if not matches:
        return None
    sid = str(matches[-1]).strip()
    return sid or None


def ensure_hermes_streaming_enabled() -> bool:
    """Turn on ``streaming.enabled`` in Hermes config.yaml when missing/false.

    Interactive Hermes uses this for live tokens. Cuttle's headless path still
    gets Cursor-like *status* via state.db polling (quiet mode clears stream
    callbacks), but leaving streaming off also hurts dogfood outside Cuttle.
    Returns True when the file was changed.
    """
    path = _hermes_home() / "config.yaml"
    if not path.is_file():
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    streaming = data.get("streaming")
    if not isinstance(streaming, dict):
        streaming = {}
    if streaming.get("enabled") is True:
        return False
    streaming["enabled"] = True
    data["streaming"] = streaming
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(path)
    return True


def _hermes_subprocess_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["HERMES_ACCEPT_HOOKS"] = "1"
    env["HERMES_YOLO_MODE"] = "1"
    git_bash = _resolve_git_bash_path()
    if git_bash:
        env["HERMES_GIT_BASH_PATH"] = git_bash
    return env


def hermes_executable() -> Optional[str]:
    """Locate the hermes CLI: env override -> PATH -> standard Windows install path."""
    override = (os.getenv("HERMES_CLI_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    exe = shutil.which("hermes")
    if exe:
        return exe
    local_app = os.getenv("LOCALAPPDATA") or os.path.expandvars(r"%LOCALAPPDATA%")
    candidate = os.path.join(local_app, "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe")
    if os.path.isfile(candidate):
        return candidate
    return None


def usage_for_query_report(
    model: str,
    usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize Hermes usage for query reports / bubble footers.

    Prefer counters loaded from ``~/.hermes/state.db`` (or LOCALAPPDATA\\hermes)
    after a quiet chat turn — Hermes does not print token usage on stdout.
    """
    u = usage if isinstance(usage, dict) else {}
    pt = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
    ct = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
    tt = int(u.get("total_tokens") or 0)
    if tt <= 0 and (pt or ct):
        tt = pt + ct
    m = (model or u.get("model") or "qwen3-coder").strip() or "qwen3-coder"
    out: Dict[str, Any] = {
        "input_tokens": pt,
        "output_tokens": ct,
        "total_tokens": tt,
        "model": m,
    }
    try:
        cr = int(u.get("cache_read_tokens") or 0)
    except (TypeError, ValueError):
        cr = 0
    try:
        cw = int(u.get("cache_write_tokens") or 0)
    except (TypeError, ValueError):
        cw = 0
    if cr > 0:
        out["cache_read_tokens"] = cr
    if cw > 0:
        out["cache_write_tokens"] = cw
    if u.get("cost") is not None:
        try:
            out["cost"] = float(u["cost"])
        except (TypeError, ValueError):
            pass
    return out


def _read_hermes_session_usage_row(session_id: str) -> Optional[Dict[str, Any]]:
    """Read token/cost counters for one Hermes session from state.db."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    db_path = _hermes_home() / "state.db"
    if not db_path.is_file():
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, model,
                   COALESCE(input_tokens, 0) AS input_tokens,
                   COALESCE(output_tokens, 0) AS output_tokens,
                   COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                   COALESCE(cache_write_tokens, 0) AS cache_write_tokens,
                   COALESCE(reasoning_tokens, 0) AS reasoning_tokens,
                   estimated_cost_usd, actual_cost_usd
            FROM sessions
            WHERE id = ?
            LIMIT 1
            """,
            (sid,),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


def load_hermes_session_usage(session_id: str) -> Dict[str, Any]:
    """Map Hermes ``sessions`` row → Cuttle usage dict (absolute counters)."""
    row = _read_hermes_session_usage_row(session_id)
    if not row:
        return {}
    try:
        pt = int(row.get("input_tokens") or 0)
        ct = int(row.get("output_tokens") or 0)
        cr = int(row.get("cache_read_tokens") or 0)
        cw = int(row.get("cache_write_tokens") or 0)
        reasoning = int(row.get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        return {}
    out: Dict[str, Any] = {
        "input_tokens": max(0, pt),
        "output_tokens": max(0, ct),
        "total_tokens": max(0, pt + ct),
    }
    if cr > 0:
        out["cache_read_tokens"] = cr
    if cw > 0:
        out["cache_write_tokens"] = cw
    if reasoning > 0:
        out["reasoning_tokens"] = reasoning
    cost = row.get("actual_cost_usd")
    if cost is None:
        cost = row.get("estimated_cost_usd")
    if cost is not None:
        try:
            cost_f = float(cost)
            if cost_f >= 0:
                out["cost"] = cost_f
        except (TypeError, ValueError):
            pass
    mid = str(row.get("model") or "").strip()
    if mid:
        out["model"] = mid
    return out


def load_hermes_context_occupancy(session_id: str) -> Dict[str, Any]:
    """Estimate current Hermes window fill from active, non-compacted messages.

    Session ``input_tokens`` is cumulative billing (and often includes cache), so
    it is a poor occupancy signal for long chats. Prefer a local char→token
    estimate over active transcript rows (``compacted=0``), falling back to
    session input when the transcript estimate is empty.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {}
    db_path = _hermes_home() / "state.db"
    if not db_path.is_file():
        return {}
    used = 0
    model = ""
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT model,
                   COALESCE(input_tokens, 0) AS input_tokens,
                   COALESCE(cache_read_tokens, 0) AS cache_read_tokens
            FROM sessions WHERE id = ? LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if row:
            model = str(row["model"] or "").strip()
            billing_in = int(row["input_tokens"] or 0)
            billing_cr = int(row["cache_read_tokens"] or 0)
        else:
            billing_in = billing_cr = 0
        # Active transcript estimate (post-compact rows drop out via compacted=1).
        msgs = conn.execute(
            """
            SELECT COALESCE(content, '') AS content,
                   COALESCE(tool_calls, '') AS tool_calls,
                   COALESCE(reasoning, '') AS reasoning,
                   COALESCE(reasoning_content, '') AS reasoning_content,
                   token_count
            FROM messages
            WHERE session_id = ?
              AND COALESCE(active, 1) = 1
              AND COALESCE(compacted, 0) = 0
            """,
            (sid,),
        ).fetchall()
        conn.close()
        chars = 0
        explicit = 0
        for m in msgs:
            tc = m["token_count"]
            if tc is not None:
                try:
                    explicit += max(0, int(tc))
                    continue
                except (TypeError, ValueError):
                    pass
            blob = (
                str(m["content"] or "")
                + str(m["tool_calls"] or "")
                + str(m["reasoning"] or "")
                + str(m["reasoning_content"] or "")
            )
            chars += len(blob)
        if explicit > 0:
            used = explicit
        elif chars > 0:
            used = max(1, chars // 4)
        else:
            # Fresh / empty transcript — fall back to session billing when small.
            used = billing_in
            try:
                from scripts.utilities.cursor_cli_tool import cursor_usage_looks_aggregated

                if cursor_usage_looks_aggregated(
                    {
                        "prompt_tokens": billing_in,
                        "cache_read_tokens": billing_cr,
                    }
                ):
                    used = 0
            except Exception:
                if billing_cr >= 400_000 or billing_in >= 1_200_000:
                    used = 0
    except Exception:
        return {}
    out: Dict[str, Any] = {}
    if used > 0:
        out["context_tokens"] = int(used)
        out["prompt_tokens"] = int(used)
        out["input_tokens"] = int(used)
    if model:
        out["model"] = model
    return out


def _delta_hermes_usage(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-turn delta when resuming (Hermes session counters are cumulative)."""
    after = after or {}
    if not after:
        return {}
    before = before or {}
    if not before:
        return dict(after)

    def _d(key: str) -> int:
        try:
            a = int(after.get(key) or 0)
            b = int(before.get(key) or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, a - b)

    pt = _d("input_tokens")
    ct = _d("output_tokens")
    cr = _d("cache_read_tokens")
    cw = _d("cache_write_tokens")
    reasoning = _d("reasoning_tokens")
    out: Dict[str, Any] = {
        "input_tokens": pt,
        "output_tokens": ct,
        "total_tokens": pt + ct,
    }
    if cr:
        out["cache_read_tokens"] = cr
    if cw:
        out["cache_write_tokens"] = cw
    if reasoning:
        out["reasoning_tokens"] = reasoning
    # Cost columns are cumulative across the session — only trust them for a
    # fresh session (no prior counters). Otherwise leave cost unset so
    # models.dev can estimate from the delta tokens.
    if not before.get("input_tokens") and not before.get("output_tokens"):
        if after.get("cost") is not None:
            out["cost"] = after["cost"]
    if after.get("model"):
        out["model"] = after["model"]
    return out


class HermesCliTool:
    """Non-interactive Hermes Agent runs for pipeline tool nodes and the remote-agent backend."""

    def __init__(
        self,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        toolsets: Optional[str] = None,
        inject_rules: bool = True,
    ):
        runtime = resolve_hermes_runtime(model, provider)
        self.model = runtime["model"]
        self.provider = runtime["provider"]
        self.toolsets = (toolsets or _DEFAULT_TOOLSETS).strip()
        # When True, Hermes injects MEMORY.md/USER.md and project rules (AGENTS.md, etc.).
        self.inject_rules = inject_rules

    async def execute_prompt(
        self,
        prompt: str,
        cwd: Optional[str] = None,
        timeout: float = 600.0,
        status_queue: Optional["queue_module.Queue"] = None,
        chat_session_id: Optional[str] = None,
        resume: Optional[str] = None,
        cancel_event: Any = None,
    ) -> Dict[str, Any]:
        if not (prompt or "").strip():
            return {"success": False, "error": "No prompt provided", "output": ""}
        if len(prompt) > _MAX_PROMPT_FOR_ARGV:
            return {
                "success": False,
                "error": (
                    f"Prompt too long for Hermes chat quiet mode "
                    f"({len(prompt)} chars > {_MAX_PROMPT_FOR_ARGV})"
                ),
                "output": "",
            }

        workdir = cwd or os.getcwd()
        if not os.path.isdir(workdir):
            return {"success": False, "error": f"Invalid working directory: {workdir}", "output": ""}

        exe = hermes_executable()
        if not exe:
            return {
                "success": False,
                "error": "hermes CLI not found (set HERMES_CLI_PATH or install Hermes Agent)",
                "output": "",
            }

        # Prefer ``hermes chat -Q -q`` over ``-z`` so we can --resume and parse
        # ``session_id:`` from stderr (oneshot never prints it). Quiet mode still
        # suppresses token streaming; live status comes from state.db polling —
        # the same surface Cursor uses for the typing-line (not bubble tokens).
        try:
            ensure_hermes_streaming_enabled()
        except Exception as exc:
            print(f"[hermes] streaming config update skipped: {exc}", flush=True)

        cmd: List[str] = [
            exe,
            "chat",
            "-Q",
            "-q", prompt,
            "-m", self.model,
            "--provider", self.provider,
            "--yolo",
            "--accept-hooks",
            "--source", "tool",  # keep Cuttle turns out of the interactive session list
        ]
        resume_id = (str(resume).strip() if resume else "")
        if resume_id:
            cmd.extend(["--resume", resume_id])
        if not self.inject_rules:
            cmd.append("--ignore-rules")
        if self.toolsets:
            cmd.extend(["-t", self.toolsets])

        env = _hermes_subprocess_env()
        working_label = f"{self.provider}/{self.model}"
        usage_before = load_hermes_session_usage(resume_id) if resume_id else {}

        async def _run_once(run_prompt: str) -> Dict[str, Any]:
            run_cmd = list(cmd)
            # Replace the prompt after -q
            try:
                q_idx = run_cmd.index("-q")
                run_cmd[q_idx + 1] = run_prompt
            except (ValueError, IndexError):
                run_cmd = list(cmd)
                run_cmd[run_cmd.index("-q") + 1] = run_prompt
            start_ts = time.time()
            stop_event = asyncio.Event()
            heartbeat_task = None
            if status_queue is not None:
                heartbeat_task = asyncio.create_task(
                    _status_heartbeat(
                        status_queue, start_ts, stop_event, working_label=working_label
                    )
                )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *run_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    cwd=workdir,
                    env=env,
                )
                attach_to_chat_run(chat_session_id, proc)
                run = await run_interruptible(
                    proc,
                    timeout=timeout,
                    cancel_event=cancel_event,
                    line_mode=False,
                )
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "output": "",
                    "usage": {},
                    "hermes_session_id": None,
                }
            finally:
                stop_event.set()
                if heartbeat_task is not None:
                    try:
                        await heartbeat_task
                    except Exception:
                        pass

            out = run.stdout.decode("utf-8", errors="replace").strip()
            err = run.stderr.decode("utf-8", errors="replace").strip()
            hermes_sid = _parse_hermes_session_id(err) or resume_id or None
            if hermes_sid and chat_session_id:
                try:
                    from scripts.utilities.hermes_cli_session_store import (
                        save_hermes_resume_id,
                    )

                    save_hermes_resume_id(workdir, chat_session_id, hermes_sid)
                except Exception:
                    pass

            if run.timed_out or run.cancelled:
                reason = run.reason or (
                    "cancelled" if run.cancelled else f"timed out after {timeout:.0f}s"
                )
                notice = format_interrupt_notice(
                    "Hermes",
                    reason,
                    elapsed_sec=run.elapsed_sec,
                    session_saved=bool(hermes_sid),
                    resume_slash="hermes",
                )
                body_parts = []
                if out:
                    body_parts.append(out)
                body_parts.append(notice)
                return {
                    "success": False,
                    "error": f"Hermes CLI {reason}",
                    "output": "\n\n".join(body_parts),
                    "usage": {},
                    "hermes_session_id": hermes_sid,
                    "timed_out": run.timed_out,
                    "cancelled": run.cancelled,
                }

            usage_after = load_hermes_session_usage(hermes_sid) if hermes_sid else {}
            # Quiet mode may finish before state.db flush — brief retry.
            if hermes_sid and not usage_after.get("input_tokens") and not usage_after.get(
                "output_tokens"
            ):
                await asyncio.sleep(0.35)
                usage_after = load_hermes_session_usage(hermes_sid) or usage_after
            usage = _delta_hermes_usage(usage_before, usage_after)
            ok = run.returncode == 0
            if not ok:
                merged = out + (("\n\n" + err) if (err and out) else err)
                # Drop the machine-readable session_id line from error text.
                merged = _SESSION_ID_RE.sub("", merged).strip()
                return {
                    "success": False,
                    "error": (err or f"exit {run.returncode}")[:2000],
                    "output": merged,
                    "usage": usage,
                    "hermes_session_id": hermes_sid,
                }
            return {
                "success": True,
                "output": out,
                "error": None,
                "usage": usage,
                "hermes_session_id": hermes_sid,
            }

        result = await _run_once(prompt)
        if (
            result.get("success")
            and _output_has_leaked_tool_calls(result.get("output") or "")
        ):
            retry = await _run_once(prompt + _RETRY_PROMPT_SUFFIX)
            if retry.get("success") and not _output_has_leaked_tool_calls(retry.get("output") or ""):
                return retry
            leaked_preview = (result.get("output") or "")[:500]
            return {
                "success": False,
                "error": (
                    "Hermes returned raw <function=...> tool XML instead of executing tools. "
                    "Local Qwen3-Coder sometimes emits tool calls as text; retry did not help. "
                    "Try /claude for coding tasks, or run `hermes chat` interactively."
                ),
                "output": leaked_preview,
                "usage": {},
                "hermes_session_id": result.get("hermes_session_id"),
            }
        return result
