"""Voice API cost accounting for calls that bypass ``api.model_providers``.

Two voice paths never flow through the provider service, so they need their
own pricing owner:

* **Realtime voice** runs browser <-> OpenAI over WebRTC. Cuttle only mints the
  ephemeral credential, so the browser forwards OpenAI's usage events
  (``response.done`` and input-transcription completions) and this owner prices
  them.
* **File/live transcription** does flow through ``model_providers.service`` but
  its ledger row is count-only (the adapter returns no token usage), so this
  owner supplies the per-minute/per-token estimate.

Rates are provider list prices verified 2026-10 against OpenAI's pricing page
(USD per 1M tokens unless noted). They are display estimates, not invoices.
Realtime accounting rule (OpenAI): user audio = 1 token / 100 ms, assistant
audio = 1 token / 50 ms; text and audio are billed on separate lines.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from contextvars import ContextVar
from functools import wraps

_voice_context = ContextVar("voice_call_attribution", default={})


def current_context():
    return dict(_voice_context.get())


def attributed_call(fn):
    """Validate optional chat/message attribution once at each voice ingress."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from flask import request, jsonify
        from api.http_authz import require_chat_session_access
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id") if isinstance(body, dict) else None
        sid = sid or request.form.get("session_id")
        message_id = body.get("message_id") if isinstance(body, dict) else None
        query_id = body.get("query_id") if isinstance(body, dict) else None
        context = {}
        if sid:
            _, sid, error = require_chat_session_access(sid)
            if error:
                return error
            context["session_id"] = sid
            if isinstance(query_id, str) and len(query_id) <= 160:
                context["query_id"] = query_id
                if not message_id:
                    from api.auth_db import get_auth_db
                    for row in reversed(get_auth_db().get_messages(sid, limit=20)):
                        meta = row.get("metadata") or {}
                        if isinstance(meta, str):
                            import json
                            meta = json.loads(meta)
                        if row["role"] == "assistant" and (meta.get("query_id") == query_id or (meta.get("cursor_run") or {}).get("query_id") == query_id or query_id in str(meta.get("report_url") or "")):
                            message_id = row["id"]
                            break
            if message_id:
                from api.auth_db import get_auth_db
                try:
                    message = get_auth_db().get_message_by_id(int(message_id))
                except (ValueError, TypeError):
                    message = None
                if not message or message["chat_session_id"] != sid:
                    return jsonify(success=False, error="Message not found in this chat."), 404
                context["message_id"] = int(message_id)
        token = _voice_context.set(context)
        try:
            return fn(*args, **kwargs)
        finally:
            _voice_context.reset(token)
    return wrapped

from api.model_providers import usage as provider_usage

# USD per 1M tokens. ``cached_*`` is the prompt-cache read rate.
REALTIME_TOKEN_RATES: Dict[str, Dict[str, float]] = {
    "gpt-realtime-2.1": {
        "audio_input": 32.0, "cached_audio_input": 0.40, "audio_output": 64.0,
        "text_input": 4.0, "cached_text_input": 0.40, "text_output": 24.0,
    },
    "gpt-realtime-2.1-mini": {
        "audio_input": 10.0, "cached_audio_input": 0.30, "audio_output": 20.0,
        "text_input": 0.60, "cached_text_input": 0.06, "text_output": 2.40,
    },
    "gpt-realtime-2": {
        "audio_input": 32.0, "cached_audio_input": 0.40, "audio_output": 64.0,
        "text_input": 4.0, "cached_text_input": 0.40, "text_output": 24.0,
    },
    "gpt-realtime-1.5": {
        "audio_input": 32.0, "cached_audio_input": 0.40, "audio_output": 64.0,
        "text_input": 4.0, "cached_text_input": 0.40, "text_output": 16.0,
    },
    "gpt-realtime-mini": {
        "audio_input": 10.0, "cached_audio_input": 0.30, "audio_output": 20.0,
        "text_input": 0.60, "cached_text_input": 0.06, "text_output": 2.40,
    },
    "gpt-realtime": {
        "audio_input": 32.0, "cached_audio_input": 0.40, "audio_output": 64.0,
        "text_input": 4.0, "cached_text_input": 0.40, "text_output": 16.0,
    },
}

# USD per audio minute (OpenAI's published estimate).
TRANSCRIPTION_MINUTE_RATES: Dict[str, float] = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-transcribe-diarize": 0.006,
    "whisper-1": 0.006,
    "gpt-transcribe": 0.0045,
}

# USD per 1M tokens for token-billed transcription models.
TRANSCRIPTION_TOKEN_RATES: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini-transcribe": {"input": 1.25, "output": 5.0},
    "gpt-4o-transcribe": {"input": 2.50, "output": 10.0},
    "gpt-4o-transcribe-diarize": {"input": 2.50, "output": 10.0},
}


def realtime_rates(model: str) -> Optional[Dict[str, float]]:
    """Rate row for a realtime model id, tolerating a dated suffix."""
    mid = str(model or "").strip()
    if mid in REALTIME_TOKEN_RATES:
        return REALTIME_TOKEN_RATES[mid]
    for known in sorted(REALTIME_TOKEN_RATES, key=len, reverse=True):
        if mid.startswith(known):
            return REALTIME_TOKEN_RATES[known]
    return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def aggregate_realtime_responses(responses: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sum OpenAI ``response.done`` usage rows into one canonical aggregate."""
    out = {
        "input_tokens": 0, "output_tokens": 0,
        "input_text_tokens": 0, "input_audio_tokens": 0, "input_image_tokens": 0,
        "cached_text_tokens": 0, "cached_audio_tokens": 0,
        "output_text_tokens": 0, "output_audio_tokens": 0,
    }
    for raw in responses or []:
        if not isinstance(raw, dict):
            continue
        ind = raw.get("input_token_details") if isinstance(raw.get("input_token_details"), dict) else {}
        cached = ind.get("cached_tokens_details") if isinstance(ind.get("cached_tokens_details"), dict) else {}
        outd = raw.get("output_token_details") if isinstance(raw.get("output_token_details"), dict) else {}
        inp = _int(raw.get("input_tokens")) or (
            _int(ind.get("text_tokens")) + _int(ind.get("audio_tokens")) + _int(ind.get("image_tokens")))
        outp = _int(raw.get("output_tokens")) or (
            _int(outd.get("text_tokens")) + _int(outd.get("audio_tokens")))
        out["input_tokens"] += inp
        out["output_tokens"] += outp
        out["input_text_tokens"] += _int(ind.get("text_tokens"))
        out["input_audio_tokens"] += _int(ind.get("audio_tokens"))
        out["input_image_tokens"] += _int(ind.get("image_tokens"))
        out["cached_text_tokens"] += _int(cached.get("text_tokens"))
        out["cached_audio_tokens"] += _int(cached.get("audio_tokens"))
        out["output_text_tokens"] += _int(outd.get("text_tokens"))
        out["output_audio_tokens"] += _int(outd.get("audio_tokens"))
    return out


def aggregate_transcriptions(transcriptions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sum input-transcription usage rows (input audio tokens + output text)."""
    out = {"input_tokens": 0, "output_tokens": 0, "audio_tokens": 0, "seconds": 0, "text_tokens": 0}
    for raw in transcriptions or []:
        if not isinstance(raw, dict):
            continue
        ind = raw.get("input_token_details") if isinstance(raw.get("input_token_details"), dict) else {}
        out["input_tokens"] += _int(raw.get("input_tokens"))
        out["output_tokens"] += _int(raw.get("output_tokens"))
        out["audio_tokens"] += _int(ind.get("audio_tokens"))
        out["text_tokens"] += _int(ind.get("text_tokens"))
        if raw.get("type") == "duration":
            out["seconds"] += max(0, float(raw.get("seconds") or 0))
    return out


def estimate_realtime_cost_usd(model: str, responses: List[Dict[str, Any]]) -> Optional[float]:
    """USD for the accumulated ``response.done`` usage of one realtime session."""
    rates = realtime_rates(model)
    if rates is None:
        return None
    agg = aggregate_realtime_responses(responses)
    # Cached tokens are a subset already included in input_tokens; bill them at
    # the cached rate and the remainder at the full input rate.
    full_text_in = max(0, agg["input_text_tokens"] - agg["cached_text_tokens"])
    full_audio_in = max(0, agg["input_audio_tokens"] - agg["cached_audio_tokens"])
    cost = (
        full_text_in * rates["text_input"]
        + agg["cached_text_tokens"] * rates["cached_text_input"]
        + full_audio_in * rates["audio_input"]
        + agg["cached_audio_tokens"] * rates["cached_audio_input"]
        + agg["output_text_tokens"] * rates["text_output"]
        + agg["output_audio_tokens"] * rates["audio_output"]
    ) / 1_000_000.0
    return cost if cost > 0 else None


def estimate_transcription_cost_usd(
    model: str,
    *,
    seconds: Optional[float] = None,
    audio_tokens: int = 0,
    text_tokens: int = 0,
    output_tokens: int = 0,
) -> Optional[float]:
    """USD for one transcription. Prefers the per-minute rate when duration is known."""
    mid = str(model or "").strip()
    minute = TRANSCRIPTION_MINUTE_RATES.get(mid)
    if minute is None:
        for known in sorted(TRANSCRIPTION_MINUTE_RATES, key=len, reverse=True):
            if mid.startswith(known):
                minute = TRANSCRIPTION_MINUTE_RATES[known]
                break
    if seconds and minute is not None:
        cost = (float(seconds) / 60.0) * minute
        return cost if cost > 0 else None
    token_rates = TRANSCRIPTION_TOKEN_RATES.get(mid)
    if token_rates is None:
        return None
    cost = ((_int(audio_tokens) + _int(text_tokens)) * token_rates["input"]
            + _int(output_tokens) * token_rates["output"]) / 1_000_000.0
    return cost if cost > 0 else None


def price_realtime_payload(
    model: str,
    responses: Optional[List[Dict[str, Any]]] = None,
    transcriptions: Optional[List[Dict[str, Any]]] = None,
    *, transcription_model: str = "gpt-4o-mini-transcribe",
) -> Dict[str, Any]:
    """Price a forwarded realtime usage payload into chat-footer usage shape."""
    agg = aggregate_realtime_responses(responses or [])
    tx = aggregate_transcriptions(transcriptions or [])
    realtime_cost = estimate_realtime_cost_usd(model, responses or []) or 0.0
    tx_model = transcription_model
    tx_cost = estimate_transcription_cost_usd(
        tx_model, seconds=tx["seconds"] or None, audio_tokens=tx["audio_tokens"], text_tokens=tx["text_tokens"], output_tokens=tx["output_tokens"]
    ) or 0.0
    cost = realtime_cost + tx_cost
    prompt_tokens = agg["input_tokens"] + tx["input_tokens"]
    completion_tokens = agg["output_tokens"] + tx["output_tokens"]
    out: Dict[str, Any] = {
        "model": str(model or ""),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "realtime_audio_input_tokens": agg["input_audio_tokens"],
        "realtime_audio_output_tokens": agg["output_audio_tokens"],
        "realtime_cost": round(realtime_cost, 6),
        "transcription_cost": round(tx_cost, 6),
    }
    if cost > 0:
        out["cost"] = round(cost, 6)
        out["cost_estimated"] = True
    return out


def record_realtime_usage(
    model: str,
    responses: Optional[List[Dict[str, Any]]] = None,
    transcriptions: Optional[List[Dict[str, Any]]] = None,
    *,
    session_id: Any = None,
    db_path=None,
    transcription_model: str = "gpt-4o-mini-transcribe",
    usage_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Price a realtime payload and append one ledger row. Never raises."""
    priced = price_realtime_payload(model, responses, transcriptions, transcription_model=transcription_model)
    try:
        recorded = provider_usage.record_call(
            provider="openai", operation="realtime_session", model=str(model or ""),
            task="realtime", session_id=session_id,
            usage=priced,
            cost=priced.get("cost"),
            db_path=db_path,
            usage_key=usage_key,
        )
        priced["recorded"] = recorded
    except Exception:
        priced["recorded"] = False
    return priced


def session_entries(session_ids):
    """Durable voice records for the same chats as the agent usage summary."""
    import json
    rows = provider_usage.calls_for_sessions(session_ids)
    operations = {"live_session", "realtime_session", "speech_generation", "audio_transcription"}
    entries = []
    for row in rows:
        if row["operation"] not in operations and row["task"] not in {"tts:narration", "voice:narration"}:
            continue
        extra = json.loads(row.get("usage_json") or "{}")
        cost = row.get("cost")
        if cost is None and row["operation"] == "text_generation":
            from api.model_pricing import estimate_cost_usd
            cost = estimate_cost_usd(f"{row['provider']}/{row['model']}", int(row.get("prompt_tokens") or 0), int(row.get("completion_tokens") or 0))
        if row["operation"] == "live_session" and (row.get("usage_key") or "").startswith("live:"):
            from api.voice_live import store
            state = store.get(row["usage_key"][5:])
            extra["finalization_unconfirmed"] = bool(state and state["state"] in {"disconnected", "uncertain"})
        usage = {**extra, "prompt_tokens": row.get("prompt_tokens") or 0,
                 "completion_tokens": row.get("completion_tokens") or 0,
                 "cost": cost, "cost_estimated": True, "model": row["model"],
                 "usage_source": "voice"}
        entries.append({"id": row["id"], "message_id": row.get("message_id"), "usage": usage})
    return entries
