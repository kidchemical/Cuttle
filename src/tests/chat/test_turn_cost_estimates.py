"""Turn cost estimates (attach_estimated_cost) + Codex steer per-turn tokens."""

from __future__ import annotations

from scripts.utilities.codex_app_server_turn import (
    _note_turn_tokens,
    _usage_from_turn_tokens,
)


# ── attach_estimated_cost ────────────────────────────────────────────────────

def _pricing(**rates):
    from unittest.mock import patch

    return patch("api.model_pricing.estimate_cost_usd", return_value=rates.get("cost", 0.5))


def test_attach_estimated_cost_flags_estimate():
    usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    with _pricing(cost=0.25):
        from api.model_pricing import attach_estimated_cost

        out = attach_estimated_cost(usage, "gpt-6-luna", cache_inclusive=True)
    assert out is not None
    assert usage["cost"] == 0.25
    assert usage["cost_estimated"] is True


def test_attach_estimated_cost_keeps_reported_cost():
    usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cost": 0.01}
    from api.model_pricing import attach_estimated_cost

    assert attach_estimated_cost(usage, "gpt-6-luna") is None
    assert usage["cost"] == 0.01
    assert "cost_estimated" not in usage


def test_attach_estimated_cost_skips_unpriced_model_and_empty_usage():
    from api.model_pricing import attach_estimated_cost

    # Cursor "auto" resolves to no public rate → estimate_cost_usd returns None.
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert attach_estimated_cost(usage, "auto", cache_inclusive=False) is None
    assert "cost" not in usage
    assert attach_estimated_cost({"total_tokens": 0}, "gpt-6-luna") is None


def test_estimate_cost_usd_cache_inclusive_override():
    from api.model_pricing import estimate_cost_usd

    # grok-4.6: $2 in / $6 out / $0.50 cache-read per 1M (models.dev).
    # Exclusive (Cursor-style): prompt is uncached → additive.
    exclusive = estimate_cost_usd(
        "grok-4.6", 200_000, 10_000, cache_read_tokens=1_000_000, cache_inclusive=False
    )
    # 0.2*2 + 1.0*0.5 + 0.01*6
    assert exclusive is not None
    assert abs(exclusive - (0.2 * 2 + 1.0 * 0.5 + 0.01 * 6)) < 1e-6
    # Inclusive (OpenAI-style): cached reads are a subset of prompt → subtract.
    inclusive = estimate_cost_usd(
        "grok-4.6", 1_200_000, 10_000, cache_read_tokens=1_000_000, cache_inclusive=True
    )
    # 0.2*2 + 1.0*0.5 + 0.01*6 — same money, different token semantics.
    assert inclusive is not None
    assert abs(inclusive - exclusive) < 1e-6
    # Without the override the heuristic treats cache_read <= prompt as inclusive.
    heuristic = estimate_cost_usd("grok-4.6", 200_000, 10_000, cache_read_tokens=100_000)
    assert abs(heuristic - estimate_cost_usd(
        "grok-4.6", 200_000, 10_000, cache_read_tokens=100_000, cache_inclusive=True
    )) < 1e-6


# ── Codex steer path: per-turn token deltas ─────────────────────────────────

def _usage_event(inp, out, cached=0, cwrite=0):
    return {
        "last": {
            "inputTokens": inp,
            "outputTokens": out,
            "cachedInputTokens": cached,
            "cacheWriteInputTokens": cwrite,
        }
    }


def test_codex_turn_tokens_accumulate_per_request():
    st: dict = {}
    for inp, out in ((500, 20), (700, 30), (900, 50)):
        _note_turn_tokens(st, _usage_event(inp, out, cached=inp - 100))
    usage = _usage_from_turn_tokens(st)
    assert usage["input_tokens"] == 2100
    assert usage["output_tokens"] == 100
    assert usage["total_tokens"] == 2200
    assert usage["cached_input_tokens"] == (400 + 600 + 800)
    assert "cache_write_input_tokens" not in usage


def test_codex_turn_tokens_empty_falls_back_to_total():
    st: dict = {}
    assert _usage_from_turn_tokens(st) == {}
    _note_turn_tokens(st, {"last": {}})
    assert _usage_from_turn_tokens(st) == {}
