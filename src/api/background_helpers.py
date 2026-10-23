"""Background text executor selection over model API and restricted CLI adapters.

Owns preferences and fallback deadlines. Domain callers own prompts and cleanup.
No chat dispatcher, router, resume maps or Context Compiler is invoked here.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import math
import threading
import time

from api.completion_providers import describe as api_description, resolve_model, resolve_effort
from api.model_providers import service as providers
from api.model_providers.types import TextRequest, TextResult
from api.agent_harness import catalog
from api.agent_harness.helper import HelperRequest, HelperCancelled

SETTINGS_KEY = 'background_helpers'
TASKS = {
    'titles': 'Chat titles', 'commits': 'Commit suggestions',
    'enhance': 'Prompt enhancement', 'narration': 'Reply narration',
    'progress': 'Progress narration', 'router': 'Router decisions',
    'wiki': 'Knowledge wiki explanations',
}
_SLOTS = threading.BoundedSemaphore(2)
_LOG = logging.getLogger(__name__)


def _settings():
    from managers.settings_manager import get_settings_manager
    return get_settings_manager()


def preferences():
    raw = _settings().get_setting(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def has_selection(task=''):
    prefs = preferences()
    return bool((prefs.get('tasks') or {}).get(task) or prefs.get('chain'))


def _cli_entries():
    for manifest in catalog.list_agent_manifests():
        if manifest.background_text:
            entry = catalog.get_agent(manifest.id)
            if entry and callable(getattr(entry[1], 'generate_helper', None)) and callable(getattr(entry[1], 'helper_models', None)) and callable(getattr(entry[1], 'helper_capabilities', None)):
                yield manifest, entry[1]


def targets():
    rows = []
    for row in api_description()['providers']:
        rows.append({**row, 'kind': 'local' if row['id'] == 'local' else 'api',
                     'target': row['id'], 'key': 'api:' + row['id'], 'auth_modes': []})
    for manifest, adapter in _cli_entries():
        available = bool(adapter.available()) if callable(getattr(adapter, 'available', None)) else False
        rows.append({'id': manifest.id, 'target': manifest.id, 'key': 'cli:' + manifest.id,
                     'label': manifest.label, 'kind': 'cli', 'configured': available,
                     'default_model': manifest.default_model, 'suggested_models': manifest.models,
                     'auth_modes': manifest.background_text.get('auth_modes', []),
                     'authentication': 'Saved CLI login; access and remaining allowance are checked when running',
                     'auth_command': manifest.auth_command})
    return rows


def describe():
    return {'preferences': preferences(), 'targets': targets(), 'tasks': TASKS,
            'legacy': api_description()}


def discover(kind, target, refresh=False):
    if kind == 'api':
        return providers.discover_models(target, refresh=refresh)
    entry = catalog.get_agent(target) if kind == 'cli' else None
    if not entry or not entry[0].background_text or not callable(getattr(entry[1], 'helper_models', None)) or not callable(getattr(entry[1], 'helper_capabilities', None)):
        raise ValueError('This CLI does not implement restricted background text')
    data = entry[1].helper_models(refresh=refresh)
    return {'provider': target, 'source': data.get('source'), 'message': data.get('error'),
            'models': [{'id': r['id'], 'label': r.get('label') or r['id'],
                        'compatibility': 'supported', 'availability': 'unknown',
                        'capabilities': {'reasoning_efforts': r.get('efforts') or [],
                                         'temperature': False,
                                         'temperature_control': {'supported': False, 'reason': 'This CLI does not expose temperature.'}}}
                       for r in data.get('models', [])],
            'default_efforts': data.get('default_efforts') or entry[1].helper_capabilities('').get('reasoning_efforts', [])}


def _temperature(value):
    if value is None or value == '': return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Temperature must be a finite number or Task default')
    if not 0 <= value <= 2: raise ValueError('Temperature must be between 0 and 2')
    return float(value)


def validate_target(raw):
    if not isinstance(raw, dict): raise ValueError('Executor must be an object')
    kind, target = raw.get('kind'), raw.get('target')
    if kind not in ('api', 'cli') or not isinstance(target, str): raise ValueError('Invalid executor')
    row = next((r for r in targets() if r['key'] == kind + ':' + target), None)
    if row is None: raise ValueError('Unknown or unsupported background executor')
    model, effort = raw.get('model', ''), raw.get('effort', '')
    if not isinstance(model, str) or len(model) > 200 or not isinstance(effort, str):
        raise ValueError('Invalid model or effort')
    model, effort = model.strip(), effort.strip()
    temperature = _temperature(raw.get('temperature'))
    auth = raw.get('auth', 'subscription') if kind == 'cli' else ''
    if kind == 'cli':
        if auth not in row['auth_modes']: raise ValueError('Unsupported CLI authentication method')
        if temperature is not None: raise ValueError('This CLI does not expose temperature')
        _, adapter = catalog.get_agent(target)
        caps = adapter.helper_capabilities(model)
        if effort and effort not in caps.get('reasoning_efforts', []):
            raise ValueError('Refresh CLI models and choose a supported effort')
    else:
        effective = resolve_model(target, model or None)
        providers.validate_effort(target, effective, effort)
        if temperature is not None:
            providers.validate_temperature(target, effective, temperature, effort or None)
    return dict(kind=kind, target=target, model=model, effort=effort, temperature=temperature, auth=auth)


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - {'chain', 'tasks'}: raise ValueError('Invalid background helper preferences')
    def chain(value):
        if not isinstance(value, list) or len(value) > 8: raise ValueError('Fallback chain must contain at most eight executors')
        result = [validate_target(v) for v in value]
        keys = [(r['kind'], r['target'], r['model'], r['effort'], r['auth']) for r in result]
        if len(set(keys)) != len(keys): raise ValueError('Duplicate fallback executor')
        return result
    overrides = raw.get('tasks') or {}
    # Keep pre-separation saved overrides readable without advertising or using
    # them. Response processing now owns its executor chain independently.
    if not isinstance(overrides, dict) or set(overrides) - (set(TASKS) | {'postprocessing'}): raise ValueError('Unknown background task')
    return {'chain': chain(raw.get('chain', [])),
            'tasks': {k: chain(v) for k,v in overrides.items() if v}}


def save(raw):
    clean = validate(raw)
    return _settings().set_setting(SETTINGS_KEY, clean)


def execution_chain(task):
    """Explicit config only: this task's chain, else the user's Default chain.

    There is deliberately **no** third source. Unconfigured background helpers
    fall back to the per-feature completion-provider path at the call sites
    (they gate on :func:`has_selection`), not to an invisible chain here.
    """
    prefs = preferences()
    if task in (prefs.get('tasks') or {}):
        return prefs['tasks'][task]
    return prefs.get('chain') or []


def generate(task: str, request: TextRequest, *, chain=None, cancel_event=None) -> TextResult | None:
    """One total deadline across the explicit fallback chain; cancel is terminal."""
    deadline = time.monotonic() + request.timeout
    chain = execution_chain(task) if chain is None else chain
    for target in chain:
        if cancel_event and cancel_event.is_set(): raise HelperCancelled('Background helper cancelled')
        remaining = deadline - time.monotonic()
        if remaining <= 0: return None
        try:
            kind, pid = target['kind'], target['target']
            model = target.get('model') or (resolve_model(pid) if kind == 'api' else '')
            effort = target.get('effort') or (resolve_effort(pid, model, request.effort) if kind == 'api' else None)
            temp = target.get('temperature')
            temp = request.temperature if temp is None else temp
            hop = replace(request, model=model, effort=effort, temperature=temp, timeout=remaining)
            if kind == 'api':
                if hop.json_object and providers.cached_capabilities(pid, model).get('json_object') is False:
                    # Providers without a JSON response parameter can still produce
                    # JSON text. Validate the returned object before accepting it.
                    hop = replace(hop, json_object=False,
                                  system=hop.system + '\nReturn exactly one valid JSON object without Markdown fences.')
                result = providers.generate_text(pid, hop, usage_task=f"background:{task}")
            else:
                if request.images: raise ValueError("CLI background helpers accept text only")
                entry = catalog.get_agent(pid)
                if not entry or not entry[0].background_text or not callable(getattr(entry[1], 'generate_helper', None)):
                    raise ValueError('CLI does not support background text')
                if effort and effort not in entry[1].helper_capabilities(model).get("reasoning_efforts", []):
                    raise ValueError("CLI helper effort is unsupported; refresh models")
                acquired = False
                while not acquired:
                    if cancel_event and cancel_event.is_set(): raise HelperCancelled('Background helper cancelled')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: return None
                    acquired = _SLOTS.acquire(timeout=min(.1, remaining))
                try:
                    result = entry[1].generate_helper(HelperRequest(
                        request.user, request.system, model, effort,
                        max(.01, deadline-time.monotonic()), request.json_object,
                        target.get('auth') or 'subscription', cancel_event, request.max_tokens))
                    result = TextResult(result.text, {**result.usage, 'model': result.model, 'execution': 'cli', 'agent': pid,
                                                                 'billing_source': target.get('auth') or 'subscription'})
                finally:
                    _SLOTS.release()
            if cancel_event and cancel_event.is_set(): raise HelperCancelled("Background helper cancelled")
            if result and result.text.strip():
                if request.json_object:
                    if not isinstance(json.loads(result.text), dict): raise ValueError('Helper returned invalid JSON')
                return result
        except HelperCancelled:
            raise
        except Exception as exc:
            _LOG.warning('Background %s via %s failed (%s)', task, target.get('target'), type(exc).__name__)
    return None


def main():
    """Read-only agent verbs; discovery never submits a content request."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest='verb', required=True)
    verbs.add_parser('list', help='Preferences and supported executors; no remote discovery')
    for name in ('models', 'inspect'):
        command = verbs.add_parser(name)
        command.add_argument('kind', choices=('api', 'cli'))
        command.add_argument('target')
        if name == 'models': command.add_argument('--refresh', action='store_true')
        else: command.add_argument('model')
    args = parser.parse_args()
    try:
        if args.verb == 'list': result = describe()
        elif args.verb == 'models': result = discover(args.kind, args.target, args.refresh)
        elif args.kind == 'api': result = providers.cached_capabilities(args.target, args.model)
        else:
            entry = catalog.get_agent(args.target)
            if not entry or not entry[0].background_text or not callable(getattr(entry[1], 'helper_capabilities', None)):
                raise ValueError('Unsupported background CLI')
            result = entry[1].helper_capabilities(args.model)
        print(json.dumps({'success':True, **result}, indent=2))
    except Exception as exc:
        from api.model_providers.cache import public_error
        print(json.dumps({'success':False, 'error':public_error(exc, 'Background helper catalog')}, indent=2))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
