"""Tests for chat inference mode helpers."""

from api.inference_mode import (
    apply_inference_mode_to_llm_node,
    cloud_cli_slash_blocked_message,
    inference_mode_from_context,
    is_cloud_cli_slash_command,
    llm_fallback_chain_for_mode,
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


def test_apply_inference_mode_to_llm_node_local():
    node = {'id': 3, 'type': 'llm', 'config': {'provider': 'openai', 'model': 'gpt-4o-mini'}}
    out = apply_inference_mode_to_llm_node(node, 'local')
    assert out['type'] == 'llm-local'
    assert out['config']['provider'] == 'local'


def test_apply_inference_mode_to_llm_node_cloud():
    node = {'id': 3, 'type': 'llm', 'config': {'provider': 'local', 'model': 'local-default'}}
    out = apply_inference_mode_to_llm_node(node, 'cloud')
    assert out['type'] in ('llm-anthropic', 'llm-openai')
    assert out['config']['provider'] in ('anthropic', 'openai')


def test_apply_inference_mode_auto_is_noop():
    node = {'id': 3, 'type': 'llm', 'config': {'provider': 'local'}}
    assert apply_inference_mode_to_llm_node(node, 'auto') is node


def test_llm_fallback_chain_for_mode():
    base = ['local', 'anthropic', 'openai']
    assert llm_fallback_chain_for_mode('local', base) == ['local']
    assert llm_fallback_chain_for_mode('cloud', base) == ['anthropic', 'openai']
    assert llm_fallback_chain_for_mode('auto', base) == base


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
