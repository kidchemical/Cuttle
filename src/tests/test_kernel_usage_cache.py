"""Kernel usage normalization preserves cache token fields."""

from api.agent_harness.kernel import _attach_usage_to_web_response, _usage_from_result


def test_usage_from_result_keeps_cache_tokens():
    payload = _usage_from_result(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "cache_read_tokens": 8000,
            "cache_write_tokens": 100,
            "context_tokens": 9000,
        }
    )
    assert payload["prompt_tokens"] == 1000
    assert payload["completion_tokens"] == 50
    assert payload["cache_read_tokens"] == 8000
    assert payload["cache_write_tokens"] == 100
    assert payload["context_tokens"] == 9000


def test_usage_from_result_accepts_cursor_camel_case():
    payload = _usage_from_result(
        {
            "inputTokens": 7,
            "outputTokens": 12,
            "cacheReadTokens": 147695,
            "cacheWriteTokens": 39331,
        }
    )
    assert payload["prompt_tokens"] == 7
    assert payload["completion_tokens"] == 12
    assert payload["cache_read_tokens"] == 147695
    assert payload["cache_write_tokens"] == 39331


def test_attach_usage_to_web_response_includes_cache():
    out = {}
    _attach_usage_to_web_response(
        out,
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "cache_read_tokens": 5000,
        },
    )
    assert out["usage"]["cache_read_tokens"] == 5000
