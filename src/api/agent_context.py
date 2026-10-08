"""Per-chat agent context fill estimate + compact triggers.

Cursor / Muse / OpenCode / Codex CLIs do not expose a first-class \"context ring\" API
to Cuttle. We approximate fill from the last turn's **input / prompt tokens**
vs the model's context limit (models.dev ``limit.context`` + agent heuristics),
and trigger each CLI's best available compact path.
"""

from __future__ import annotations

from core.agent_cli_env import agent_cli_env

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_AGENTS = frozenset(
    {
        "cursor",
        "muse",
        "opencode",
        "codex",
        "antigravity",
        "hermes",
        "claude",
        "deepseek",
    }
)

# Agents with a real compact path (not just a gauge).
_COMPACTABLE_AGENTS = frozenset(
    {"cursor", "muse", "opencode", "codex", "hermes", "claude"}
)

# When catalogs miss, prefer these agent defaults (tokens).
# Cursor Auto / unlabeled models are 1M-class in current CLI catalogs.
# Codex gpt-5-class windows are commonly ~272k (usable slightly lower).
_DEFAULT_LIMITS: Dict[str, int] = {
    "cursor": 1_000_000,
    "muse": 1_000_000,
    "opencode": 200_000,
    "codex": 272_000,
    "antigravity": 1_000_000,
    "hermes": 200_000,
    "claude": 200_000,
    "deepseek": 128_000,
}

_MILLION_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[mM]\b")
_K_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[kK]\b")
_CONTEXT_BRACKET_RE = re.compile(
    r"\[(?:[^\]]*[,;])?context\s*=\s*([0-9.]+)\s*([kmKM])?",
    re.I,
)
_LABEL_CONTEXT_RE = re.compile(
    r"(?:^|[\s(])(\d+(?:\.\d+)?)\s*([kmKM])(?:\s*context|\b)",
)

_COMPACT_PROMPTS = {
    "cursor": "/summarize",
    "muse": (
        "Compact this session's context now using your context-compaction "
        "strategy. Reply with a single short confirmation line only — do not "
        "restate the full conversation."
    ),
}


def normalize_agent_id(raw: Optional[str]) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    s = s.lstrip("/")
    if s in SUPPORTED_AGENTS:
        return s
    # Chip / slash prefixes: "cursor ", "opencode-agent", etc.
    for aid in SUPPORTED_AGENTS:
        if s == aid or s.startswith(aid + " ") or s.startswith(aid + "-"):
            return aid
    return None


def _parse_token_magnitude(num: float, unit: Optional[str]) -> Optional[int]:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    u = (unit or "").strip().lower()
    if u == "m":
        return int(n * 1_000_000)
    if u == "k":
        return int(n * 1_000)
    # Bare number in bracket context=200000
    if n >= 1000:
        return int(n)
    return None


def parse_context_limit_hint(text: Optional[str]) -> Optional[int]:
    """Extract a context window size from a model id or label string."""
    s = (text or "").strip()
    if not s:
        return None
    m = _CONTEXT_BRACKET_RE.search(s)
    if m:
        hit = _parse_token_magnitude(m.group(1), m.group(2))
        if hit:
            return hit
    m = _LABEL_CONTEXT_RE.search(s)
    if m:
        hit = _parse_token_magnitude(m.group(1), m.group(2))
        if hit:
            return hit
    # Prefer million before thousand so "1M" wins over stray "4k".
    m = _MILLION_RE.search(s)
    if m:
        hit = _parse_token_magnitude(m.group(1), "m")
        if hit:
            return hit
    m = _K_RE.search(s)
    if m:
        hit = _parse_token_magnitude(m.group(1), "k")
        if hit and hit >= 32_000:  # ignore small "4k" image sizes etc.
            return hit
    return None


def lookup_catalog_context_limit(model: Optional[str]) -> Optional[int]:
    """models.dev ``limit.context`` when present in the pricing cache."""
    try:
        from api.model_pricing import lookup_model_context_limit

        return lookup_model_context_limit(model or "")
    except Exception:
        return None


def resolve_context_limit(
    agent_id: str,
    model: Optional[str] = None,
    *,
    model_label: Optional[str] = None,
) -> Tuple[int, str]:
    """Return ``(limit_tokens, source)``."""
    aid = normalize_agent_id(agent_id) or "cursor"
    for candidate in (model, model_label):
        hint = parse_context_limit_hint(candidate)
        if hint:
            return hint, "model_id"
    cat = lookup_catalog_context_limit(model)
    if cat:
        return int(cat), "models.dev"
    # Cursor Auto / unknown: try label from `agent models` list when model is set.
    if aid == "cursor" and model:
        try:
            from api.cursor_agent_commands import list_cursor_agent_models

            mid = (model or "").strip().lower()
            for row in list_cursor_agent_models() or []:
                if str(row.get("id") or "").strip().lower() == mid:
                    hint = parse_context_limit_hint(row.get("label") or "")
                    if hint:
                        return hint, "cursor_models"
                    break
        except Exception:
            pass
    if aid == "muse":
        # Spark / Meta Muse models are documented as 1M in Cuttle's catalog.
        return _DEFAULT_LIMITS["muse"], "agent_default"
    return _DEFAULT_LIMITS.get(aid, 200_000), "agent_default"


def context_percent(used_tokens: int, limit_tokens: int) -> float:
    if limit_tokens <= 0:
        return 0.0
    pct = 100.0 * max(0, int(used_tokens or 0)) / float(limit_tokens)
    return round(min(100.0, pct), 1)


def _ceil_context_limit(used: int, base: int) -> int:
    """If observed occupancy exceeds the known limit, bump to the next tier."""
    floor = max(int(base or 0), 1)
    u = max(0, int(used or 0))
    if u <= floor:
        return floor
    for tier in (200_000, 1_000_000, 2_000_000):
        if u <= int(tier * 1.05):
            return max(floor, tier)
    return max(floor, u)


def _usage_context_fill_tokens(
    usage: Optional[Dict[str, Any]],
    *,
    agent_id: Optional[str] = None,
) -> Tuple[int, str]:
    """Return ``(tokens, source)`` for the composer context ring.

    Prefer an explicit peak / context_tokens stamp. For Cursor / Codex / Claude /
    Hermes / Antigravity / Muse, never treat cumulative billing ``inputTokens`` (with
    huge cacheRead) as window fill.
    """
    if not isinstance(usage, dict) or not usage:
        return 0, "none"
    aid = normalize_agent_id(agent_id) or ""
    if aid in ("cursor", "codex", "claude", "hermes", "antigravity"):
        try:
            from scripts.utilities.cursor_cli_tool import (
                cursor_usage_context_tokens,
                cursor_usage_looks_aggregated,
            )
        except Exception:
            cursor_usage_context_tokens = None  # type: ignore
            cursor_usage_looks_aggregated = None  # type: ignore
        # Normalize cache key aliases so the Cursor aggregate detector applies.
        blob = usage
        if "cache_read_tokens" not in usage:
            cr = usage.get("cached_input_tokens") or usage.get("cacheReadTokens")
            if cr is not None:
                blob = dict(usage)
                blob["cache_read_tokens"] = cr
        for key in ("context_tokens", "peak_context_tokens", "contextTokens"):
            raw = usage.get(key)
            if raw is None:
                continue
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n > 0:
                # Large cache bills alone cannot invalidate an explicit snapshot.
                # Old Cursor stamps copied the aggregate input+cache sum instead.
                if aid == "cursor":
                    def billing_int(*keys: str) -> int:
                        for billing_key in keys:
                            try:
                                return int(blob[billing_key])
                            except (KeyError, TypeError, ValueError):
                                continue
                        return 0

                    cached = billing_int("cacheReadTokens", "cache_read_tokens")
                    total = (
                        billing_int("inputTokens", "input_tokens", "prompt_tokens")
                        + cached
                        + billing_int("cacheWriteTokens", "cache_write_tokens")
                    )
                    if n >= 400_000 and cached >= 200_000 and n >= cached:
                        if abs(n - total) <= max(2000, int(n * 0.03)):
                            return 0, "aggregated"
                return n, "peak"
        if cursor_usage_looks_aggregated and cursor_usage_looks_aggregated(blob):
            return 0, "aggregated"
        if aid == "cursor" and cursor_usage_context_tokens:
            try:
                ctx = cursor_usage_context_tokens(usage)
            except Exception:
                ctx = None
            if ctx and int(ctx) > 0:
                return int(ctx), "cursor_call"
            return 0, "aggregated"
        # Honest short turns: prompt_tokens ≈ last-call occupancy.
        for key in ("prompt_tokens", "input_tokens", "inputTokens"):
            raw = usage.get(key)
            if raw is None:
                continue
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n > 0:
                return n, "prompt"
        return 0, "none"
    for key in ("context_tokens", "peak_context_tokens", "contextTokens"):
        raw = usage.get(key)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n, "peak"
    if aid == "muse":
        # Muse prompt usage is cumulative billing, not current window occupancy.
        return 0, "aggregated"
    for key in ("prompt_tokens", "input_tokens", "inputTokens"):
        raw = usage.get(key)
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n, "prompt"
    return 0, "none"


def _codex_apply_live_or_snapshot(
    *,
    chat_session_id: Any,
    cwd: str,
    resume_id: Optional[str],
    tokens: int,
    token_source: str,
    limit: int,
    limit_source: str,
    live: bool = False,
) -> Tuple[int, str, int, str]:
    """Prefer Codex app-server last-call occupancy over billing totals.

    Uses a persisted snapshot when present; refreshes live only when asked
    (``live=True``) or when there is no snapshot and message usage is aggregated.
    """
    from scripts.utilities.codex_cli_session_store import (
        load_codex_context_snapshot,
        save_codex_context_snapshot,
    )

    snap = load_codex_context_snapshot(chat_session_id)
    now = time.time()
    has_snap = (
        isinstance(snap, dict)
        and int(snap.get("context_tokens") or snap.get("prompt_tokens") or 0) > 0
    )
    need_fetch = bool(resume_id) and (
        bool(live) or ((token_source == "aggregated" or tokens <= 0) and not has_snap)
    )
    if need_fetch:
        # A live app-server turn owns the thread: resuming it from a second server
        # hangs until timeout. The turn runner saves snapshots itself.
        try:
            from api.agent_harness.steer import active_agent

            if active_agent(chat_session_id) == "codex":
                need_fetch = False
        except Exception:
            pass
    if need_fetch and resume_id:
        # Same ownership the runner holds from spawn to reaping: skip here
        # (steer registration alone starts only at turn/start and ends
        # before the process exits). Never spawn a competing writer.
        try:
            from api.agent_harness.codex_thread_ownership import owner_of

            if owner_of(resume_id) is not None:
                need_fetch = False
        except Exception:
            pass
    if need_fetch:
        try:
            from scripts.utilities.codex_app_server import fetch_codex_thread_token_usage

            raw = fetch_codex_thread_token_usage(resume_id, cwd=cwd, timeout=15.0)
            tu = (
                raw.get("token_usage")
                if isinstance(raw.get("token_usage"), dict)
                else None
            )
            if raw.get("success") and tu:
                used = int(tu.get("context_tokens") or tu.get("prompt_tokens") or 0)
                win = int(tu.get("model_context_window") or 0)
                snap = {
                    "context_tokens": used,
                    "model_context_window": win,
                    "ts": now,
                    "thread_id": resume_id,
                }
                save_codex_context_snapshot(chat_session_id, snap)
                has_snap = used > 0
        except Exception:
            pass

    if has_snap and isinstance(snap, dict):
        used = int(snap.get("context_tokens") or snap.get("prompt_tokens") or 0)
        win = int(snap.get("model_context_window") or 0)
        if used > 0:
            tokens = used
            token_source = "app_server"
        if win > 0:
            limit = win
            limit_source = "app_server"
    return tokens, token_source, limit, limit_source


def _meta_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _last_usage_from_messages(
    messages: List[Dict[str, Any]],
    *,
    agent_id: Optional[str] = None,
) -> Tuple[int, Optional[str], Optional[Dict[str, Any]], str]:
    """Scan newest→oldest for assistant usage.

    Returns ``(tokens, model, usage, token_source)``.
    """
    aid = normalize_agent_id(agent_id)
    last_agg_model: Optional[str] = None
    last_agg_usage: Optional[Dict[str, Any]] = None
    for msg in messages:
        if str(msg.get("role") or "").lower() != "assistant":
            continue
        meta = _meta_dict(msg.get("metadata"))
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
        cursor_run = meta.get("cursor_run") if isinstance(meta.get("cursor_run"), dict) else None
        if not usage and cursor_run and isinstance(cursor_run.get("usage"), dict):
            usage = dict(cursor_run.get("usage") or {})
        elif usage and cursor_run and isinstance(cursor_run.get("usage"), dict):
            # Merge peak/cache fields from cursor_run when bubble usage omitted them.
            merged = dict(cursor_run.get("usage") or {})
            merged.update(usage)
            usage = merged
        tokens, token_source = _usage_context_fill_tokens(usage, agent_id=aid)
        # Prefer matching agent when we can tell from slash chip / type.
        if aid:
            chip = None
            sc = meta.get("slash_command")
            if isinstance(sc, dict):
                chips = sc.get("chips")
                if isinstance(chips, list) and chips:
                    chip = chips[0] if isinstance(chips[0], dict) else None
            prefix = ""
            if isinstance(chip, dict):
                prefix = str(chip.get("prefix") or chip.get("meta") or "").lower()
            typ = str(meta.get("type") or msg.get("type") or "").lower()
            if prefix and aid not in prefix and f"/{aid}" not in prefix:
                if aid not in typ:
                    continue
        model = None
        if isinstance(usage, dict):
            model = str(usage.get("model") or "").strip() or None
        if not model:
            for key in (
                "agent_model",
                "model",
                "muse_model",
                "opencode_model",
                "hermes_model",
            ):
                v = meta.get(key)
                if v:
                    model = str(v).strip()
                    break
        if not model and cursor_run:
            model = str(
                cursor_run.get("reported_model") or cursor_run.get("requested_model") or ""
            ).strip() or None
        # Aggregated multi-step billing with no peak — keep looking for a better
        # blob, but remember this so we can show a "unknown until next turn" hint.
        if tokens <= 0 and token_source == "aggregated":
            last_agg_model = model or last_agg_model
            last_agg_usage = usage if isinstance(usage, dict) else last_agg_usage
            continue
        if tokens <= 0:
            continue
        return tokens, model, usage if isinstance(usage, dict) else None, token_source
    if last_agg_usage is not None:
        return 0, last_agg_model, last_agg_usage, "aggregated"
    return 0, None, None, "none"


def _cursor_recent_usage(
    cwd: str, chat_session_id: Any
) -> Tuple[int, Optional[str], str]:
    try:
        from scripts.utilities.cursor_cli_session_store import (
            load_cursor_agent_options,
            load_cursor_agent_options_for_session,
        )

        if cwd:
            opts = load_cursor_agent_options(cwd, chat_session_id) or {}
        else:
            opts = load_cursor_agent_options_for_session(chat_session_id) or {}
        runs = list(opts.get("recent_runs") or [])
        model = opts.get("last_reported_model") or opts.get("model")
        if not runs:
            return 0, model, "none"
        last = runs[-1] if isinstance(runs[-1], dict) else {}
        usage = last.get("usage") if isinstance(last.get("usage"), dict) else {}
        tokens, source = _usage_context_fill_tokens(usage, agent_id="cursor")
        model = str(
            last.get("reported_model")
            or last.get("requested_model")
            or model
            or ""
        ).strip() or None
        return tokens, model, source
    except Exception:
        return 0, None, "none"


def _resolve_model_for_agent(
    agent_id: str,
    cwd: str,
    chat_session_id: Any,
    hint_model: Optional[str],
) -> Optional[str]:
    if hint_model and str(hint_model).strip():
        return str(hint_model).strip()
    aid = normalize_agent_id(agent_id) or ""
    try:
        if aid == "cursor":
            from scripts.utilities.cursor_cli_session_store import (
                load_cursor_agent_options,
                load_cursor_agent_options_for_session,
            )

            if cwd:
                opts = load_cursor_agent_options(cwd, chat_session_id) or {}
            else:
                opts = load_cursor_agent_options_for_session(chat_session_id) or {}
            return (
                str(opts.get("last_reported_model") or opts.get("model") or "").strip()
                or None
            )
        if aid == "muse":
            from scripts.utilities.muse_cli_session_store import load_muse_model

            return (load_muse_model(chat_session_id) or "").strip() or None
        if aid == "opencode":
            from api.agent_harness.agents.opencode.session_store import (
                load_opencode_model,
            )

            return (load_opencode_model(chat_session_id) or "").strip() or None
        if aid == "codex":
            from scripts.utilities.codex_cli_session_store import load_codex_model

            return (load_codex_model(chat_session_id) or "").strip() or None
        if aid == "hermes":
            from scripts.utilities.hermes_cli_session_store import load_hermes_model

            return (load_hermes_model(chat_session_id) or "").strip() or None
        if aid == "claude":
            from scripts.utilities.claude_cli_session_store import load_claude_model

            return (load_claude_model(chat_session_id) or "").strip() or None
    except Exception:
        return None
    return None


def _load_resume_id(agent_id: str, cwd: str, chat_session_id: Any) -> Optional[str]:
    aid = normalize_agent_id(agent_id)
    if not aid or not chat_session_id:
        return None
    work = (cwd or "").strip() or os.getcwd()
    try:
        if aid == "cursor":
            from scripts.utilities.cursor_cli_session_store import load_cursor_resume_id

            return load_cursor_resume_id(work, chat_session_id)
        if aid == "muse":
            from scripts.utilities.muse_cli_session_store import load_muse_resume_id

            return load_muse_resume_id(work, chat_session_id)
        if aid == "opencode":
            from api.agent_harness.agents.opencode.session_store import (
                load_opencode_resume_id,
            )

            return load_opencode_resume_id(work, chat_session_id)
        if aid == "codex":
            from scripts.utilities.codex_cli_session_store import load_codex_resume_id

            return load_codex_resume_id(work, chat_session_id)
        if aid == "antigravity":
            from api.agent_harness.agents.antigravity.session_store import (
                load_antigravity_resume_id,
            )

            return load_antigravity_resume_id(work, chat_session_id)
        if aid == "hermes":
            from scripts.utilities.hermes_cli_session_store import load_hermes_resume_id

            return load_hermes_resume_id(work, chat_session_id)
        if aid == "claude":
            from scripts.utilities.claude_cli_session_store import load_claude_resume_id

            return load_claude_resume_id(work, chat_session_id)
    except Exception:
        return None
    return None


def get_agent_context_status(
    *,
    chat_session_id: Any,
    agent_id: str,
    cwd: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    live: bool = False,
) -> Dict[str, Any]:
    """Build context-fill status for the composer radial.

    ``live=True`` forces a Codex app-server occupancy refresh when a resume id
    exists (slower; used when the user opens the gauge popover).
    """
    aid = normalize_agent_id(agent_id)
    if not aid:
        return {
            "success": False,
            "error": (
                "Unsupported agent (use cursor, muse, opencode, codex, "
                "antigravity, hermes, claude, or deepseek)"
            ),
            "supported": False,
        }
    work = (cwd or "").strip()
    msgs = messages
    if msgs is None and chat_session_id is not None:
        try:
            from api.auth_db import get_auth_db

            msgs = get_auth_db().get_messages(int(chat_session_id), limit=40)
        except Exception:
            msgs = []
    msgs = list(msgs or [])
    # Newest first for the scanner.
    newest_first = list(reversed(msgs)) if msgs and (
        (msgs[0].get("id") or 0) < (msgs[-1].get("id") or 0)
    ) else list(msgs)

    tokens, msg_model, _usage, token_source = _last_usage_from_messages(
        newest_first, agent_id=aid
    )
    model = _resolve_model_for_agent(aid, work, chat_session_id, msg_model)
    if tokens <= 0 and aid == "cursor":
        c_tokens, c_model, c_source = _cursor_recent_usage(work, chat_session_id)
        if c_tokens > 0:
            tokens = c_tokens
            token_source = c_source
        elif token_source == "none" and c_source == "aggregated":
            token_source = "aggregated"
        if not model and c_model:
            model = c_model

    if aid == "muse":
        resume_for_muse = _load_resume_id(aid, work, chat_session_id)
        try:
            from scripts.utilities.muse_cli_session_store import read_muse_msp_context

            msp = read_muse_msp_context(resume_for_muse) if resume_for_muse else None
        except Exception:
            msp = None
        if isinstance(msp, dict):
            msp_tokens = int(msp.get("context_tokens") or msp.get("prompt_tokens") or 0)
            # The current MSP view supersedes historical peaks after compaction.
            if msp_tokens > 0:
                tokens = msp_tokens
                token_source = "msp_view"
            if not model and msp.get("model"):
                model = str(msp.get("model")).strip() or model

    limit, limit_source = resolve_context_limit(aid, model)
    resume_id = _load_resume_id(aid, work, chat_session_id)
    resume_cwd = work

    if aid == "codex":
        try:
            from scripts.utilities.codex_cli_session_store import resolve_codex_resume

            resolved = resolve_codex_resume(work or os.getcwd(), chat_session_id)
            if resolved:
                resume_id, resume_cwd = resolved
        except Exception:
            pass
        tokens, token_source, limit, limit_source = _codex_apply_live_or_snapshot(
            chat_session_id=chat_session_id,
            cwd=resume_cwd or work or os.getcwd(),
            resume_id=resume_id,
            tokens=int(tokens or 0),
            token_source=token_source,
            limit=int(limit),
            limit_source=limit_source,
            live=bool(live),
        )

    if aid == "hermes":
        try:
            from scripts.utilities.hermes_cli_session_store import (
                load_hermes_context_snapshot,
                resolve_hermes_resume,
                save_hermes_context_snapshot,
            )
            from scripts.utilities.hermes_cli_tool import load_hermes_context_occupancy

            resolved = resolve_hermes_resume(work or os.getcwd(), chat_session_id)
            if resolved:
                resume_id, _ = resolved
            snap = load_hermes_context_snapshot(chat_session_id)
            if (
                (token_source == "aggregated" or tokens <= 0)
                and resume_id
                and (not isinstance(snap, dict) or not int(snap.get("context_tokens") or 0))
            ):
                occ = load_hermes_context_occupancy(resume_id)
                used = int(occ.get("context_tokens") or 0)
                if used > 0:
                    snap = {
                        "context_tokens": used,
                        "ts": time.time(),
                        "session_id": resume_id,
                    }
                    save_hermes_context_snapshot(chat_session_id, snap)
            if isinstance(snap, dict) and int(snap.get("context_tokens") or 0) > 0:
                tokens = int(snap["context_tokens"])
                token_source = "hermes_transcript"
            elif resume_id and (token_source == "aggregated" or tokens <= 0):
                occ = load_hermes_context_occupancy(resume_id)
                used = int(occ.get("context_tokens") or 0)
                if used > 0:
                    tokens = used
                    token_source = "hermes_transcript"
        except Exception:
            pass

    if aid == "claude":
        try:
            from scripts.utilities.claude_cli_session_store import (
                load_claude_context_snapshot,
                resolve_claude_resume,
            )

            resolved = resolve_claude_resume(work or os.getcwd(), chat_session_id)
            if resolved:
                resume_id, _ = resolved
            snap = load_claude_context_snapshot(chat_session_id)
            if isinstance(snap, dict) and int(snap.get("context_tokens") or 0) > 0:
                if token_source == "aggregated" or tokens <= 0:
                    tokens = int(snap["context_tokens"])
                    token_source = "snapshot"
                win = int(snap.get("model_context_window") or 0)
                if win > 0:
                    limit = win
                    limit_source = "snapshot"
        except Exception:
            pass

    # Never invent a fake window from billing aggregates (CH-000504-style 2.9M/2.9M).
    if tokens > 0 and token_source not in ("aggregated",):
        bumped = _ceil_context_limit(tokens, limit)
        if bumped > limit:
            limit = bumped
            limit_source = "observed"
    pct = context_percent(tokens, limit) if tokens > 0 else 0.0
    compact_ok = aid in _COMPACTABLE_AGENTS
    hint = _status_hint(pct, tokens, resume_id, token_source=token_source)
    if aid == "antigravity":
        compact_ok = False
        if tokens <= 0:
            hint = (
                "Waiting for the next Antigravity turn to report usage. "
                "Antigravity auto-manages context (no headless /compact)."
            )
    elif aid == "hermes" and tokens <= 0:
        hint = (
            "Hermes occupancy comes from the active transcript in state.db. "
            "Compact via /compact when a session is resumed."
        )
    elif aid == "deepseek":
        compact_ok = False
        hint = (
            "DeepSeek headless is one-shot (no mid-session resume). "
            "Gauge shows the last turn only; Compact is not available."
        )
    elif aid == "codex" and token_source == "aggregated" and tokens <= 0:
        hint = (
            "Last Codex turn reported billing totals (not window fill). "
            "Open the gauge to refresh live occupancy from the Codex thread."
        )
    return {
        "success": True,
        "supported": True,
        "agent_id": aid,
        "model": model,
        "resume_id": resume_id,
        "has_resume": bool(resume_id),
        "used_tokens": int(tokens or 0),
        "limit_tokens": int(limit),
        "percent": pct,
        "limit_source": limit_source,
        "token_source": token_source,
        "estimated": token_source not in ("app_server", "hermes_transcript"),
        "compact_available": compact_ok,
        "hint": hint,
    }


def _status_hint(
    pct: float,
    tokens: int,
    resume_id: Optional[str],
    *,
    token_source: str = "none",
) -> str:
    if not resume_id and tokens <= 0:
        return "No agent session yet — send a message to start tracking context."
    if tokens <= 0 and token_source == "aggregated":
        return (
            "Last turn was a long multi-step run (billing totals, not a single "
            "context snapshot). Context % refreshes after the next reply."
        )
    if tokens <= 0:
        return "Waiting for the next turn to report context size."
    if pct >= 85:
        return "Context is nearly full — compact soon to avoid overflows."
    if pct >= 60:
        return "Context is filling up — compact when the thread feels heavy."
    return "Context looks healthy."


def _compact_cursor(cwd: str, chat_session_id: Any, resume_id: str) -> Dict[str, Any]:
    """Native Cursor compact: ``agent -p --resume … "/summarize"``.

    Official CLI docs: ``/summarize`` (aliases ``/compress``, ``/compact``) frees
    context. Cuttle already runs with ``--force --trust`` in print mode.
    """
    from scripts.utilities.cursor_cli_tool import _cursor_agent_oneline_prompt

    run_meta: Dict[str, Any] = {}
    text = _cursor_agent_oneline_prompt(
        _COMPACT_PROMPTS["cursor"],
        workspace=cwd,
        timeout=180.0,
        chat_session_id=chat_session_id,
        run_meta_out=run_meta,
    )
    if text is None:
        return {
            "success": False,
            "agent_id": "cursor",
            "error": "Cursor Agent CLI unavailable",
            "method": "summarize",
            "session_id": resume_id,
        }
    out = str(text or "").strip()
    failed = out.startswith("❌") or out.startswith("[CANCELLED]") or out.startswith("[FAIL]")
    usage = run_meta.get("usage") if isinstance(run_meta.get("usage"), dict) else None
    return {
        "success": not failed,
        "agent_id": "cursor",
        "method": "summarize",
        "output": out[:2000],
        "error": out[:500] if failed else None,
        "usage": usage,
        "session_id": resume_id,
    }


def _compact_muse(cwd: str, chat_session_id: Any, resume_id: str) -> Dict[str, Any]:
    import asyncio

    from scripts.utilities.muse_cli_tool import MuseCliTool

    tool = MuseCliTool(yolo=True)

    async def _run() -> Dict[str, Any]:
        return await tool.execute_prompt(
            _COMPACT_PROMPTS["muse"],
            cwd=cwd,
            timeout=180.0,
            resume=resume_id,
            chat_session_id=(
                str(chat_session_id) if chat_session_id is not None else None
            ),
            max_model_steps=4,
            context_compaction_strategy="summary-preserved-suffix/v1",
            context_compaction_soft_threshold=0.05,
            context_compaction_hard_threshold=0.08,
        )

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        return {
            "success": False,
            "agent_id": "muse",
            "error": f"Muse compact failed: {exc}",
            "method": "context-compaction-threshold",
            "session_id": resume_id,
        }
    if not isinstance(result, dict):
        return {
            "success": False,
            "error": "Muse compact returned nothing",
            "agent_id": "muse",
            "method": "context-compaction-threshold",
            "session_id": resume_id,
        }
    ok = bool(result.get("success"))
    out = str(result.get("output") or result.get("error") or "").strip()
    return {
        "success": ok,
        "agent_id": "muse",
        "method": "context-compaction-threshold",
        "output": out[:2000],
        "error": None if ok else (out or "Compact failed"),
        "usage": result.get("usage"),
        "session_id": result.get("session_id") or resume_id,
    }


def _which_opencode() -> Optional[str]:
    import shutil

    return shutil.which("opencode") or shutil.which("opencode.exe")


def _compact_opencode(cwd: str, resume_id: str) -> Dict[str, Any]:
    bin_path = _which_opencode()
    if not bin_path:
        return {
            "success": False,
            "agent_id": "opencode",
            "error": "OpenCode CLI not found on PATH",
        }
    cmd = [
        bin_path,
        "run",
        "--session",
        resume_id,
        "--command",
        "compact",
        "--format",
        "json",
        "--auto",
    ]
    env = agent_cli_env()
    # Isolate manual compact from auto-preflight (which can run first and muddy
    # the JSONL / exit semantics). Official path is still --command compact;
    # HTTP POST /session/{id}/compact via `opencode serve` is more reliable but
    # heavier — keep CLI for now.
    env.setdefault("OPENCODE_DISABLE_AUTOCOMPACT", "true")
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=240,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "agent_id": "opencode",
            "error": "OpenCode compact timed out",
            "method": "command-compact",
        }
    except OSError as exc:
        return {
            "success": False,
            "agent_id": "opencode",
            "error": f"Failed to spawn OpenCode: {exc}",
            "method": "command-compact",
        }
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    ok = proc.returncode == 0
    # Some builds exit 0 with an event stream even when compact is queued.
    if not ok and "compact" in out.lower() and "error" not in out.lower()[:400]:
        ok = True
    return {
        "success": ok,
        "agent_id": "opencode",
        "method": "command-compact",
        "output": out[:2000],
        "error": None if ok else (out[:500] or f"exit {proc.returncode}"),
        "session_id": resume_id,
        "returncode": proc.returncode,
    }


def _compact_codex(cwd: str, resume_id: str) -> Dict[str, Any]:
    from scripts.utilities.codex_app_server import compact_codex_thread

    result = compact_codex_thread(resume_id, cwd=cwd, timeout=180.0)
    if not isinstance(result, dict):
        return {
            "success": False,
            "agent_id": "codex",
            "method": "app-server-compact",
            "error": "Codex compact returned nothing",
            "session_id": resume_id,
        }
    result.setdefault("agent_id", "codex")
    result.setdefault("method", "app-server-compact")
    result.setdefault("session_id", resume_id)
    return result


def _compact_hermes(cwd: str, resume_id: str) -> Dict[str, Any]:
    """Hermes session compact via quiet chat ``/compact`` on the resumed session."""
    from scripts.utilities.hermes_cli_tool import HermesCliTool, hermes_executable

    if not hermes_executable():
        return {
            "success": False,
            "agent_id": "hermes",
            "method": "compact",
            "error": "Hermes CLI not found",
            "session_id": resume_id,
        }

    async def _run() -> Dict[str, Any]:
        tool = HermesCliTool(inject_rules=False)
        return await tool.execute_prompt(
            "/compact",
            cwd=cwd,
            timeout=180.0,
            resume=resume_id,
        )

    import asyncio

    try:
        raw = asyncio.run(_run())
    except Exception as exc:
        return {
            "success": False,
            "agent_id": "hermes",
            "method": "compact",
            "error": f"Hermes compact failed: {exc}",
            "session_id": resume_id,
        }
    ok = bool(raw.get("success"))
    out = str(raw.get("output") or raw.get("error") or "").strip()
    result = {
        "success": ok,
        "agent_id": "hermes",
        "method": "compact",
        "output": out[:2000],
        "error": None if ok else (out[:500] or "Hermes /compact failed"),
        "session_id": resume_id,
    }
    try:
        from scripts.utilities.hermes_cli_tool import load_hermes_context_occupancy

        occ = load_hermes_context_occupancy(resume_id)
        if occ.get("context_tokens"):
            result["token_usage"] = {
                "context_tokens": int(occ["context_tokens"]),
                "prompt_tokens": int(occ["context_tokens"]),
            }
    except Exception:
        pass
    return result


def _compact_claude(cwd: str, resume_id: str, chat_session_id: Any = None) -> Dict[str, Any]:
    """Claude Code compact via ``claude -p --resume … "/compact"``."""
    import asyncio

    from scripts.utilities.claude_cli_tool import ClaudeCliTool, claude_executable

    if not claude_executable():
        return {
            "success": False,
            "agent_id": "claude",
            "method": "compact",
            "error": "Claude Code CLI not found",
            "session_id": resume_id,
        }

    async def _run() -> Dict[str, Any]:
        tool = ClaudeCliTool()
        return await tool.execute_prompt(
            "/compact",
            cwd=cwd,
            resume=resume_id,
            chat_session_id=(
                str(chat_session_id) if chat_session_id is not None else None
            ),
            timeout=180.0,
        )

    try:
        raw = asyncio.run(_run())
    except Exception as exc:
        return {
            "success": False,
            "agent_id": "claude",
            "method": "compact",
            "error": f"Claude compact failed: {exc}",
            "session_id": resume_id,
        }
    ok = bool(raw.get("success"))
    out = str(raw.get("output") or raw.get("error") or "").strip()
    return {
        "success": ok,
        "agent_id": "claude",
        "method": "compact",
        "output": out[:2000],
        "error": None if ok else (out[:500] or "Claude /compact failed"),
        "session_id": resume_id,
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
    }


def compact_agent_context(
    *,
    chat_session_id: Any,
    agent_id: str,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger compact/summarize for the sticky agent session."""
    aid = normalize_agent_id(agent_id)
    if not aid:
        return {
            "success": False,
            "error": (
                "Unsupported agent (use cursor, muse, opencode, codex, "
                "antigravity, hermes, claude, or deepseek)"
            ),
        }
    if aid not in _COMPACTABLE_AGENTS:
        return {
            "success": False,
            "agent_id": aid,
            "error": (
                f"{aid} has no headless compact API in Cuttle "
                "(Antigravity auto-manages context; DeepSeek headless is one-shot)."
            ),
        }
    work = (cwd or "").strip() or os.getcwd()
    try:
        from core.runtime_paths import rewrite_windows_cuttle_path

        work = rewrite_windows_cuttle_path(work)
    except Exception:
        pass
    if not os.path.isdir(work):
        return {"success": False, "error": f"Invalid working directory: {work}"}
    resume_id = _load_resume_id(aid, work, chat_session_id)
    if not resume_id:
        return {
            "success": False,
            "agent_id": aid,
            "error": (
                f"No {aid} session to compact yet. "
                "Send a message with that agent badge first."
            ),
        }
    started = time.monotonic()
    if aid == "cursor":
        result = _compact_cursor(work, chat_session_id, resume_id)
    elif aid == "muse":
        result = _compact_muse(work, chat_session_id, resume_id)
    elif aid == "codex":
        result = _compact_codex(work, resume_id)
    elif aid == "hermes":
        result = _compact_hermes(work, resume_id)
    elif aid == "claude":
        result = _compact_claude(work, resume_id, chat_session_id)
    else:
        result = _compact_opencode(work, resume_id)
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    result["resume_id"] = resume_id
    if result.get("success"):
        # The summary may drop the Cuttle briefing: the next turn re-sends it.
        try:
            from api.cuttle_brain.context_delta import clear_injected_snapshot

            clear_injected_snapshot(chat_session_id, aid)
        except Exception:
            pass
    # Refresh status after compact (usage may drop on a later turn).
    try:
        status = get_agent_context_status(
            chat_session_id=chat_session_id,
            agent_id=aid,
            cwd=work,
        )
        # App-server compact can return fresher occupancy than last chat bubble.
        tu = result.get("token_usage") if isinstance(result.get("token_usage"), dict) else None
        if (
            aid == "codex"
            and isinstance(status, dict)
            and tu
            and int(tu.get("context_tokens") or tu.get("prompt_tokens") or 0) > 0
        ):
            used = int(tu.get("context_tokens") or tu.get("prompt_tokens") or 0)
            limit = int(tu.get("model_context_window") or status.get("limit_tokens") or 0)
            if limit <= 0:
                limit = int(status.get("limit_tokens") or _DEFAULT_LIMITS["codex"])
            status = dict(status)
            status["used_tokens"] = used
            status["limit_tokens"] = limit
            status["percent"] = context_percent(used, limit)
            status["token_source"] = "app_server"
            status["hint"] = _status_hint(
                status["percent"], used, resume_id, token_source="app_server"
            )
            try:
                from scripts.utilities.codex_cli_session_store import (
                    save_codex_context_snapshot,
                )

                save_codex_context_snapshot(
                    chat_session_id,
                    {
                        "context_tokens": used,
                        "model_context_window": limit,
                        "ts": time.time(),
                        "thread_id": resume_id,
                    },
                )
            except Exception:
                pass
        result["status"] = status
    except Exception:
        pass
    return result
