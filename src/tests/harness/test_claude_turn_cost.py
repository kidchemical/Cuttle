"""Claude Code result costs are per turn, not the resumed session's running total."""

import json

import pytest

from scripts.utilities import claude_cli_session_store as store
from scripts.utilities.claude_cli_tool import _merge_results, _parse_claude_json
from scripts.utilities.claude_turn_cost import TurnCostLedger

RATES = {"opus-x": {"input": 5, "output": 25, "cache_read": .5, "cache_write": 6.25},
         "haiku-x": {"input": 1, "output": 5, "cache_read": .1, "cache_write": 1.25}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from api import model_pricing
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "map.json")
    monkeypatch.setattr(model_pricing, "lookup_model_rates", RATES.get)


def _result(turn_out, turn_cr, cum, cost, sid="s-1"):
    """One root result: per-turn ``usage`` plus session-wide ``modelUsage``."""
    return {"type": "result", "session_id": sid, "result": "ok", "total_cost_usd": cost,
            "usage": {"input_tokens": 4, "output_tokens": turn_out, "cache_read_input_tokens": turn_cr},
            "modelUsage": {m: {"inputTokens": r[0], "outputTokens": r[1], "cacheReadInputTokens": r[2],
                               "contextWindow": 200000} for m, r in cum.items()}}


def _cost(ledger, result):
    return _parse_claude_json(json.dumps(ledger.turn(result)))["usage"]


def test_consecutive_resumed_turns_price_only_their_own_tokens():
    first = _cost(TurnCostLedger(None), _result(1000, 100_000, {"opus-x": (4, 1000, 100_000)}, 0.2))
    # Next run resumes the same session; totals now include the first turn.
    second = _cost(TurnCostLedger("s-1"), _result(500, 50_000, {"opus-x": (8, 1500, 150_000)}, 0.3))
    assert first["cost"] == pytest.approx(0.07502)   # 4*5 + 1000*25 + 100k*.5, per 1M
    assert second["cost"] == pytest.approx(0.03752)  # only the 500 out / 50k cache read
    assert second["reported_cost"] == pytest.approx(0.1)


def test_helper_model_cost_is_kept_in_the_difference():
    TurnCostLedger(None).turn(_result(1000, 0, {"opus-x": (0, 1000, 0)}, 0.1))
    usage = _cost(TurnCostLedger("s-1"),
                  _result(1000, 0, {"opus-x": (0, 2000, 0), "haiku-x": (1_000_000, 0, 0)}, 0.2))
    assert usage["cost"] == pytest.approx(0.025 + 1.0)


def test_counter_reset_falls_back_to_the_turns_own_tokens():
    TurnCostLedger(None).turn(_result(5000, 0, {"opus-x": (0, 5000, 0)}, 1.0))
    # The CLI did not restore its totals: they restart below the saved baseline.
    usage = _cost(TurnCostLedger("s-1"), _result(3000, 0, {"opus-x": (0, 3000, 0)}, 0.5))
    assert usage["cost"] == pytest.approx(0.07502)  # 4 in + 3000 out, nothing subtracted


def test_resume_without_baseline_prices_turn_usage_not_session_total():
    usage = _cost(TurnCostLedger("old"), _result(975, 378_128, {"opus-x": (4, 900_000, 9e7)}, 45.9, sid="old"))
    assert usage["cost"] == pytest.approx((4 * 5 + 975 * 25 + 378_128 * .5) / 1e6)
    assert "reported_cost" not in usage


def test_steered_results_in_one_run_sum_to_the_run_total():
    ledger = TurnCostLedger(None)
    parsed = [_parse_claude_json(json.dumps(ledger.turn(r))) for r in (
        _result(1000, 0, {"opus-x": (0, 1000, 0)}, 0.025),
        _result(1000, 0, {"opus-x": (0, 2000, 0)}, 0.05),
    )]
    assert _merge_results(parsed)["usage"]["cost"] == pytest.approx(0.05)
