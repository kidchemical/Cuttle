"""Outcome records for turns that bypass the router (pinned / starred agents).

Live turns are recorded by ``record_pinned_turn`` from the ``/api/chat`` harness
path; ``backfill_from_history`` rebuilds the same rows from persisted chat
messages so My Cuttle Performance covers turns from before logging existed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api.agent_router.outcomes import known_query_ids, record_turn, set_feedback_for_query
from api.agent_router.policy import classify_failure, is_cancellation
from core.runtime_paths import data_db_dir

PINNED_SOURCE = "pinned"

EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

_KNOWN_AGENTS = {"cursor", "codex", "muse", "claude", "hermes", "opencode", "antigravity"}

_PROVIDER_RULES = (
    (re.compile(r"grok"), "xAI"),
    (re.compile(r"claude|sonnet|opus|haiku|fable"), "Anthropic"),
    (re.compile(r"gemini"), "Google"),
    (re.compile(r"muse|spark|llama"), "Meta"),
    (re.compile(r"deepseek"), "DeepSeek"),
    (re.compile(r"qwen"), "Alibaba"),
    (re.compile(r"kimi"), "Moonshot"),
    (re.compile(r"composer|^auto$"), "Cursor"),
    (re.compile(r"gpt|^o\d|codex|luna|terra|\bsol\b"), "OpenAI"),
)

_AGENT_DEFAULT_PROVIDER = {
    "cursor": "Cursor",
    "codex": "OpenAI",
    "muse": "Meta",
    "claude": "Anthropic",
}

# Native harness controls (`/muse model …`, `/codex effort …`, session clear)
# that were persisted as assistant bubbles but are not agent work.
_META_REPLY_RE = re.compile(
    r"^\*\*[^*\n]{1,40}:\*\*\s*(model|effort|reasoning|session cleared)\b"
    r"|^\*\*[^*\n]{1,40}(models|efforts?)\*\*\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def split_model_effort(model: Optional[str]) -> Tuple[str, Optional[str]]:
    """``cursor-grok-4.6-high-fast`` → (``grok-4.6-fast``, ``high``)."""
    raw = str(model or "").strip().lower()
    if not raw:
        return "default", None
    if raw.startswith("cursor-") and len(raw) > 7:
        raw = raw[7:]
    parts = raw.split("-")
    effort = None
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] in EFFORT_LEVELS:
            effort = parts[i]
            del parts[i]
            break
    return "-".join(parts) or "default", effort


def provider_for(agent: str, model: str) -> str:
    m = str(model or "").lower()
    for pattern, provider in _PROVIDER_RULES:
        if pattern.search(m):
            return provider
    return _AGENT_DEFAULT_PROVIDER.get(str(agent or "").lower(), "unknown")


def normalize_usage(*candidates: Any) -> Dict[str, Any]:
    """First usable token/cost payload among ``candidates`` (camel or snake case)."""
    for usage in candidates:
        if not isinstance(usage, dict):
            continue
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("inputTokens")))
        completion = usage.get(
            "completion_tokens", usage.get("output_tokens", usage.get("outputTokens"))
        )
        total = usage.get("total_tokens", usage.get("totalTokens"))
        if total is None and (prompt is not None or completion is not None):
            total = int(prompt or 0) + int(completion or 0)
        if prompt is None and completion is None and total is None and usage.get("cost") is None:
            continue
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cost": usage.get("cost"),
        }
    return {}


def _usage_result(usage: Dict[str, Any], cost: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"usage": dict(usage or {})}
    if cost is not None:
        out["cost"] = cost
    return out


def classify_turn(result: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(failure_kind, reason) for a harness reply.

    Unlike router classification this never scans a *successful* reply for
    transport words — an answer that mentions "not found" or "rate limit" is
    still a good answer.
    """
    if not isinstance(result, dict):
        return "transport", "empty result"
    text = str(result.get("response") or result.get("error") or "").strip()
    rtype = str(result.get("type") or "")
    if text.startswith("[CANCELLED]"):
        return "cancelled", "cancelled by user"
    failed = (
        rtype.endswith("_error")
        or result.get("success") is False
        or text.startswith("[FAIL]")
        or text.startswith("❌")
    )
    if not failed:
        return "none", ""
    if is_cancellation(text):
        return "cancelled", "cancelled by user"
    kind, reason = classify_failure(result, response_text=text)
    if kind == "none":
        kind = "task"
    return kind, reason or text[:200]


def _turn_model_and_effort(agent: str, result: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    cursor_run = result.get("cursor_run") if isinstance(result.get("cursor_run"), dict) else {}
    model = (
        cursor_run.get("requested_model")
        or result.get("agent_model")
        or cursor_run.get("reported_model")
        or ""
    )
    if str(model).strip().lower() == str(agent).lower():
        model = ""
    base, effort = split_model_effort(model)
    explicit = str(result.get("agent_effort") or "").strip().lower()
    return base, (explicit or effort)


def record_pinned_turn(
    agent_id: str,
    result: Optional[Dict[str, Any]],
    *,
    latency_ms: Optional[float],
    session_id: Any = None,
    project_path: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Store one pinned-agent turn. Telemetry failure never fails the user turn."""
    if not isinstance(result, dict) or result.get("meta_command"):
        return False
    agent = str(result.get("agent_id") or agent_id or "").strip().lower()
    if not agent:
        return False
    query_id = str(result.get("query_id") or "").strip()
    if not query_id:
        return False
    kind, reason = classify_turn(result)
    model, effort = _turn_model_and_effort(agent, result)
    return record_turn(
        decision_id=f"pin-{query_id}",
        target_agent=agent,
        target_model=model,
        source=PINNED_SOURCE,
        failure_kind=kind,
        reason=reason,
        latency_ms=latency_ms,
        result=_usage_result(normalize_usage(result.get("usage")), result.get("cost")),
        session_id=session_id,
        project_path=project_path,
        query_id=query_id,
        reasoning_effort=effort,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# History backfill
# ---------------------------------------------------------------------------

_CHIP_AGENT_RE = re.compile(r"^/([a-z][a-z0-9_-]*)")
_CHIP_MODEL_RE = re.compile(r"\bmodel\s+([^\s·]+)", re.IGNORECASE)
_CHIP_EFFORT_RE = re.compile(r"\beffort\s+([a-z]+)", re.IGNORECASE)
# A reply this long after its prompt is a queued/overnight turn, not a latency.
_MAX_BACKFILL_DURATION_S = 4 * 3600


def _parse_ts(value: Any) -> Optional[float]:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _known_agents() -> set:
    try:
        from api.agent_harness.catalog import list_agents

        return set(list_agents()) | _KNOWN_AGENTS
    except Exception:
        return set(_KNOWN_AGENTS)


def turn_from_history(
    meta: Dict[str, Any],
    content: str,
    *,
    agents: Iterable[str],
) -> Optional[Dict[str, Any]]:
    """Describe a persisted assistant bubble as an outcome, or None to skip it."""
    if not meta.get("query_id"):
        return None
    if any(meta.get(k) for k in ("supervised_task_id", "restart_id", "control_request_id")):
        return None
    chips = (meta.get("slash_command") or {}).get("chips") or []
    if not chips or not isinstance(chips[0], dict):
        return None
    chip = chips[0]
    chip_meta = str(chip.get("meta") or "")
    label = str(chip.get("label") or "")
    agents = set(agents)
    m = _CHIP_AGENT_RE.match(chip_meta.strip())
    agent = (m.group(1).lower() if m else "") or str(chip.get("category") or "").lower()
    if agent not in agents:
        agent = str(chip.get("category") or "").lower()
    if agent not in agents:
        return None
    text = str(content or "")
    usage = normalize_usage(
        meta.get("usage"),
        (meta.get("cursor_run") or {}).get("usage") if isinstance(meta.get("cursor_run"), dict) else None,
    )
    if not usage and _META_REPLY_RE.search(text[:400]):
        return None

    cursor_run = meta.get("cursor_run") if isinstance(meta.get("cursor_run"), dict) else {}
    mm = _CHIP_MODEL_RE.search(chip_meta)
    model = (
        cursor_run.get("requested_model")
        or (mm.group(1) if mm else "")
        or cursor_run.get("reported_model")
        or (meta.get("usage") or {}).get("model")
        or ""
    )
    base, effort = split_model_effort(model)
    em = _CHIP_EFFORT_RE.search(chip_meta)
    if em:
        effort = em.group(1).lower()

    result = {
        "response": text,
        "type": f"{agent}_error" if meta.get("slash_command_failed") else f"{agent}_command",
        "success": True,
    }
    kind, reason = classify_turn(result)
    return {
        "agent": agent,
        "model": base,
        "effort": effort,
        "source": "router" if label.lower().startswith("router") else PINNED_SOURCE,
        "failure_kind": kind,
        "reason": reason,
        "usage": usage,
        "query_id": str(meta["query_id"]),
        "project_path": meta.get("project_path") or "",
    }


_CONTEXT_BLOCK_RE = re.compile(r"<cuttle_context>.*?</cuttle_context>", re.S)
_LEADING_SLASH_RE = re.compile(r"^(?:/model\s+\S+\s*|/[a-z][\w-]*\s+)+", re.I)


def _excerpt(text: Any, head: int, tail: int = 0, *, user: bool = False) -> str:
    s = _CONTEXT_BLOCK_RE.sub("", str(text or "")).strip()
    s = re.sub(r"\s+", " ", s)
    if user:
        s = _LEADING_SLASH_RE.sub("", s)
    if len(s) <= head + tail + 5:
        return s
    return s[:head].rstrip() + " … " + (s[-tail:].lstrip() if tail else "")


def turn_contexts(
    rows: List[Dict[str, Any]],
    *,
    auth_db_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Map ``query_id`` → {ask, reply, next_user, next_gap_s} from chat history.

    ``next_user`` is the user's following message in the same chat — the
    strongest implicit accept/reject signal a classifier gets.
    """
    wanted = {str(r["query_id"]) for r in rows if r.get("query_id") and r.get("session_id")}
    if not wanted:
        return {}
    sessions = sorted({str(r["session_id"]) for r in rows if r.get("query_id") and r.get("session_id")})
    if auth_db_path is None:
        auth_db_path = data_db_dir() / "cuttle_auth.db"
    out: Dict[str, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(str(auth_db_path), timeout=10.0)
    except sqlite3.Error:
        return {}
    try:
        for start in range(0, len(sessions), 400):
            chunk = sessions[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            cursor = conn.execute(
                "SELECT chat_session_id, role, content, timestamp, metadata FROM chat_messages "
                f"WHERE chat_session_id IN ({marks}) ORDER BY chat_session_id, id",
                chunk,
            )
            last_session = None
            last_user = ""
            awaiting: List[Dict[str, Any]] = []
            for sid, role, content, ts, raw_meta in cursor:
                if sid != last_session:
                    last_session, last_user, awaiting = sid, "", []
                if role == "user":
                    for ctx in awaiting:
                        ctx["next_user"] = _excerpt(content, 300, user=True)
                        reply_ts, user_ts = ctx.pop("_ts", None), _parse_ts(ts)
                        if reply_ts is not None and user_ts is not None and user_ts >= reply_ts:
                            ctx["next_gap_s"] = round(user_ts - reply_ts)
                    awaiting = []
                    last_user = content
                    continue
                if role != "assistant" or not raw_meta or "query_id" not in raw_meta:
                    continue
                try:
                    meta = json.loads(raw_meta)
                except (TypeError, ValueError):
                    continue
                qid = str(meta.get("query_id") or "") if isinstance(meta, dict) else ""
                if qid not in wanted:
                    continue
                ctx = {
                    "ask": _excerpt(last_user, 300, user=True),
                    "reply": _excerpt(content, 300, 200),
                    "next_user": None,
                    "next_gap_s": None,
                    "_ts": _parse_ts(ts),
                }
                out[qid] = ctx
                awaiting.append(ctx)
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    for ctx in out.values():
        ctx.pop("_ts", None)
    return out


def backfill_from_history(
    *,
    auth_db_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create outcome rows for past assistant replies that have none yet.

    Idempotent: rows are keyed ``hist-<message id>`` and replies whose
    ``query_id`` is already recorded (router or live pinned) are skipped.
    """
    if auth_db_path is None:
        auth_db_path = data_db_dir() / "cuttle_auth.db"
    agents = _known_agents()
    seen_queries = known_query_ids(db_path=db_path)
    stats = {"scanned": 0, "inserted": 0, "already_recorded": 0, "skipped": 0, "dry_run": dry_run}

    conn = sqlite3.connect(str(auth_db_path), timeout=10.0)
    try:
        cursor = conn.execute(
            "SELECT id, chat_session_id, role, content, timestamp, metadata "
            "FROM chat_messages ORDER BY chat_session_id, id"
        )
        last_session = None
        pending_user_ts: Optional[float] = None
        for mid, sid, role, content, ts, raw_meta in cursor:
            if sid != last_session:
                last_session = sid
                pending_user_ts = None
            if role == "user":
                pending_user_ts = _parse_ts(ts)
                continue
            if role != "assistant":
                continue
            stats["scanned"] += 1
            user_ts, pending_user_ts = pending_user_ts, None
            try:
                meta = json.loads(raw_meta) if raw_meta else {}
            except (TypeError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                stats["skipped"] += 1
                continue
            turn = turn_from_history(meta, content, agents=agents)
            if not turn:
                stats["skipped"] += 1
                continue
            if turn["query_id"] in seen_queries:
                stats["already_recorded"] += 1
                continue
            reply_ts = _parse_ts(ts)
            latency_ms = None
            if reply_ts is not None and user_ts is not None:
                gap = reply_ts - user_ts
                if 0 < gap <= _MAX_BACKFILL_DURATION_S:
                    latency_ms = gap * 1000.0
            if dry_run:
                stats["inserted"] += 1
                continue
            ok = record_turn(
                decision_id=f"hist-{mid}",
                target_agent=turn["agent"],
                target_model=turn["model"],
                source=turn["source"],
                failure_kind=turn["failure_kind"],
                reason=turn["reason"],
                latency_ms=latency_ms,
                result=_usage_result(turn["usage"]),
                session_id=sid,
                project_path=turn["project_path"],
                query_id=turn["query_id"],
                reasoning_effort=turn["effort"],
                recorded_at=reply_ts,
                replace=False,
                db_path=db_path,
            )
            if ok:
                stats["inserted"] += 1
                seen_queries.add(turn["query_id"])
                if meta.get("user_feedback") in ("good", "bad"):
                    set_feedback_for_query(meta["user_feedback"], turn["query_id"], db_path=db_path)
    finally:
        conn.close()
    return stats
