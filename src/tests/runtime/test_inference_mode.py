"""Tests for chat inference mode helpers."""

from api.inference_mode import (
    cloud_cli_slash_blocked_message,
    inference_mode_from_context,
    is_cloud_cli_slash_command,
    normalize_inference_mode,
)


def test_normalize_inference_mode_aliases():
    assert normalize_inference_mode(None) == 'auto'
    assert normalize_inference_mode('LOCAL') == 'local'
    assert normalize_inference_mode('ollama') == 'local'
    assert normalize_inference_mode('remote') == 'cloud'
    assert normalize_inference_mode('bogus') == 'auto'


def test_inference_mode_from_context_prefers_session():
    assert inference_mode_from_context({'inference_mode': 'local'}, {'inference_mode': 'cloud'}) == 'local'
    assert inference_mode_from_context({}, {'inference_mode': 'cloud'}) == 'cloud'


def test_local_mode_blocks_cloud_clis_but_allows_hermes():
    assert is_cloud_cli_slash_command('/cursor hi') is True
    assert is_cloud_cli_slash_command('/claude hi') is True
    assert is_cloud_cli_slash_command('/hermes hi') is False
    assert is_cloud_cli_slash_command('/deepseek hi') is True
    blocked = cloud_cli_slash_blocked_message('local')
    assert blocked is not None
    assert '/hermes' in blocked
    assert '/deepseek' in blocked
    assert cloud_cli_slash_blocked_message('auto') is None
