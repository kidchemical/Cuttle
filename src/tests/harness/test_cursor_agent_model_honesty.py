"""Cursor runs must not badge a premium model as "Auto"."""

from __future__ import annotations

from scripts.utilities.cursor_cli_tool import _cursor_agent_cli_option_args


def test_auto_is_passed_explicitly():
    # Omitting --model let the CLI's global selectedModel run the turn.
    assert _cursor_agent_cli_option_args(model="auto") == ["--model", "auto"]
    assert _cursor_agent_cli_option_args(model=None) == []


def test_badge_names_reported_model_when_request_was_auto():
    from api.web_chat_api import _assistant_message_metadata

    meta = _assistant_message_metadata({
        'response': 'ok',
        'cursor_run': {
            'requested_model': 'auto',
            'reported_model': 'Cursor Grok 4.6 High',
            'cwd': r'C:\Projects\Cuttle',
        },
    })
    chip = meta['slash_command']['chips'][0]
    assert chip['label'] == 'Cursor - Cursor Grok 4.6 High'
    assert 'requested Auto' in chip['meta']
    assert 'CLI reported Cursor Grok 4.6 High' in chip['meta']


def test_badge_keeps_auto_when_cli_also_reports_auto():
    from api.web_chat_api import _assistant_message_metadata

    meta = _assistant_message_metadata({
        'response': 'ok',
        'cursor_run': {'requested_model': 'auto', 'reported_model': 'Auto'},
    })
    assert meta['slash_command']['chips'][0]['label'] == 'Cursor - Auto'


def test_badge_keeps_pinned_request_label():
    from api.web_chat_api import _assistant_message_metadata

    # Pinned models report differently-formatted strings for the same model;
    # the request stays authoritative there.
    meta = _assistant_message_metadata({
        'response': 'ok',
        'cursor_run': {
            'requested_model': 'claude-sonnet-5-thinking-high',
            'reported_model': 'Sonnet 5 200K Medium',
        },
    })
    label = meta['slash_command']['chips'][0]['label']
    assert label != 'Cursor - Sonnet 5 200K Medium'
    assert label.startswith('Cursor - ')
