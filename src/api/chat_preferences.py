"""User-owned prompt preferences; settings persistence has one owner."""
from __future__ import annotations

import json

MAX_PROMPT_CHARS = 32000


def _settings():
    from managers.settings_manager import get_settings_manager
    return get_settings_manager()


def _text(value):
    if not isinstance(value, str) or len(value) > MAX_PROMPT_CHARS:
        raise ValueError(f'Prompt must be text of at most {MAX_PROMPT_CHARS} characters')
    return value.strip()


def prompts():
    raw = _settings().get_setting('chat_prompts') or {}
    return {'global': raw.get('global', ''), 'agents': dict(raw.get('agents') or {})}


def save_prompts(raw):
    if not isinstance(raw, dict) or not raw or set(raw) - {'global', 'agents'}:
        raise ValueError('Expected global and/or agents prompt settings')
    clean = {}
    if 'global' in raw:
        clean['global'] = _text(raw['global'])
    if 'agents' in raw:
        from api.agent_harness.catalog import list_agents
        agents = raw['agents']
        if not isinstance(agents, dict) or set(agents) - set(list_agents()):
            raise ValueError('Unknown agent in prompt settings')
        clean['agents'] = {aid: _text(text) for aid, text in agents.items()}

    def merge(current):
        current = dict(current or {})
        if 'global' in clean:
            current['global'] = clean['global']
        agents = dict(current.get('agents') or {})
        for aid, text in clean.get('agents', {}).items():
            if text:
                agents[aid] = text
            else:
                agents.pop(aid, None)
        current['agents'] = agents
        return current
    return _settings().update_setting('chat_prompts', merge)


def apply_agent_prompt(text, agent_id):
    """CLI-neutral instructions on every turn, including native resume turns.

    After the first save even an empty preference snapshot is sent, so clearing
    instructions explicitly supersedes the old snapshot in a resumed thread.
    These preferences supplement the CLI's native system prompt and project rules.
    """
    raw = _settings().get_setting('chat_prompts')
    if not raw:
        return text
    selected = {'shared': raw.get('global', ''), 'agent': (raw.get('agents') or {}).get(agent_id, '')}
    return ('<cuttle_prompt_preferences>\n'
            'Current user-configured instructions for this turn. This snapshot replaces '
            'all earlier Cuttle prompt preferences, including when a value is empty. '
            'Apply shared instructions first, then the agent-specific instructions. '
            'These supplement your native system instructions and project briefing.\n'
            + json.dumps(selected, ensure_ascii=False) + '\n</cuttle_prompt_preferences>\n\n' + text)


def processor_preferences():
    raw = _settings().get_setting('response_postprocessing') or {}
    return {'enabled': bool(raw.get('enabled', False)),
            'prompt': raw.get('prompt', 'Give me a concise TL;DR of the response.'),
            'mode': raw.get('mode', 'append'), 'chain': raw.get('chain') or []}


def save_processor(raw):
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'prompt', 'mode', 'chain'} or not {'enabled', 'prompt', 'mode'} <= set(raw):
        raise ValueError('Expected enabled, prompt, mode and executor chain')
    if not isinstance(raw['enabled'], bool) or raw['mode'] not in ('append', 'replace'):
        raise ValueError('Invalid enabled value or output mode')
    prompt = _text(raw['prompt'])
    if not prompt:
        raise ValueError('Enter post-processing instructions')
    from api.background_helpers import validate
    chain = validate({'chain': raw.get('chain', [])})['chain']
    return _settings().set_setting('response_postprocessing', {**raw, 'prompt': prompt, 'chain': chain})
