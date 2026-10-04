"""Claude's native SDK initialization catalog; discovery sends no user turn."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import yaml

from core.agent_cli_env import agent_cli_env
from scripts.utilities.claude_cli_tool import claude_executable

_CACHE = {}
_LOCK = threading.Lock()
_TTL = 120


def _manifest_models():
    raw = yaml.safe_load(Path(__file__).with_name('manifest.yaml').read_text(encoding='utf-8'))
    return [{'id': mid, 'label': mid, 'efforts': raw.get('model_efforts', {}).get(mid, [])}
            for mid in raw.get('models', [])]


def _discover(cwd=None):
    exe = claude_executable()
    if not exe:
        return [], 'Claude Code CLI not found; install and authenticate with `claude auth login`.'
    request = {'type': 'control_request', 'request_id': 'cuttle-models',
               'request': {'subtype': 'initialize'}}
    try:
        result = subprocess.run(
            [exe, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose'],
            input=json.dumps(request) + '\n', capture_output=True, text=True,
            cwd=cwd, env=agent_cli_env(), timeout=30,
        )
        for line in result.stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            response = item.get('response', {})
            if item.get('type') != 'control_response' or response.get('request_id') != 'cuttle-models':
                continue
            rows = response.get('response', {}).get('models', [])
            out = []
            seen = set()
            for row in rows:
                mid = str(row.get('value') or '').strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                out.append({'id': mid, 'label': row.get('displayName') or mid,
                            'description': row.get('description') or '',
                            'resolved_model': row.get('resolvedModel') or mid,
                            'efforts': list(row.get('supportedEffortLevels') or [])
                            if row.get('supportsEffort') else []})
            if out:
                return out, None
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Never expose auth/config stderr or pretend a fallback is a live refresh.
    return [], 'Claude catalog discovery failed; check `claude auth login` and update the CLI.'


def list_claude_catalog_models(*, refresh=False, cwd=None):
    key = str(Path(cwd or Path(__file__).resolve().parents[5]).resolve())
    with _LOCK:
        cached = _CACHE.get(key)
        if not refresh and cached and time.monotonic() - cached['fetched_at'] < _TTL:
            return {**cached, 'models': [dict(row) for row in cached['models']]}
        rows, error = _discover(key)
        result = {'models': rows or _manifest_models(), 'source': 'cli' if rows else 'static_fallback',
                  'error': error, 'fetched_at': time.monotonic()}
        result['count'] = len(result['models'])
        if rows or not cached:
            _CACHE[key] = result
        elif error:
            result = {**cached, 'error': error, 'source': 'cached_fallback'}
        return result


def claude_efforts_for_model(model=None, *, cwd=None):
    mid = str(model or 'default').lower()
    for row in list_claude_catalog_models(cwd=cwd)['models']:
        if mid in (row['id'].lower(), str(row.get('resolved_model') or '').lower()):
            return list(row['efforts'])
    return []
